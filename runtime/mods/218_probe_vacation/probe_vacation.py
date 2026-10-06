# -*- coding: utf-8 -*-
"""計測: 宿の宿泊（休暇）の未実測部分を録る。

##### 何を決めるための計測か

`315_vacation_custom` が宿泊の期間・部屋の名前・宿代を差し替える。
その前提は **実機で全部確定した**（GAME.md §2.17 / VERIFICATION.md
§3.28）:

| 項目 | 実測の結果 |
|---|---|
| 宿代の徴収 | `VacationStartManager.execute` の中で1回（窓の前後で `gold` が宿代のぶんだけ減る） |
| `quality` の実値 | `'kennel'`(0G) / `'bunk'`(10G) / `'private_room'`(100G) / `'luxury_suite'`(1000G)。部屋は4つ（犬小屋はここで見つかった） |
| 日数送り | 同じ `execute` の中で `elapse_days(months * 30)` が1回。活動マネージャでは動かない |
| 連泊 | `まだ宿泊する` は宿代も日数ももう1回 |
| `period_months` / `age` | int / int（実測 4 と 31） |

それでもこの計測は置き続ける。
残っている問いが1つあるため:

- **`period_months` の年齢ごとの境目**。
  仕様は「若いと3ヵ月・年を取ると最長 6ヵ月」で、実測は 20代=3・31歳=4 の2点だけ。
  `display_vacation_choice` の行は `period_months` と
  `player.age` を毎回対で残すので、遊ぶほど表が埋まる

加えて、
ゲームの更新でこの前提が崩れたときに気づくための見張りになる（徴収の額・日数・語彙が変わればログの形が変わる）。

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。
`safe=True` と握り潰しで、記録に失敗しても本体は必ず呼ぶ。

##### 適用順とラベルの見え方

この計測は `315_` より後（外側）に置くので、
`elapse_days` と所持金は **MOD が差し替える前のゲームの生の値**が録れる（TECH.md
§3.2.2 の「計測は修正より後」）。
日数を差し替えるのは `315_` 自身ではなくローダの日数送りの関所（`durations`。TECH.md §3.3.3）だが、
関所も日数を望む MOD より内側に立つので、ここへ来る数は変わらない。
一方ボタンのラベルは描かれる直前に `315_` が書き換えるため、
ここで写る `text` は書き換え後の姿になることがある。
生のラベルが要るときは `315_` を切って録る（spec の `args` はどちらでも生のまま）。

##### 出力

`out/vacation.log`（読む用）と `out/vacation.jsonl`（1画面・1窓=1行、
突き合わせる用）。

版3: 各段の窓（`Vacation*Manager.execute`）の包みで、本体を呼ぶ前の控え
（所持金・ログの見出し）を try に入れた。版2ではここが投げると本体が呼ばれずに
宿泊の段が止まる形だった（実機で踏んだ記録は無い）。
あわせて全部の包みを、受け取った引数をそのまま本体へ渡す形にした
（キーワードで来た引数を位置へ直さない。VERIFICATION.md §3.76 の `333_` の件）。
`change_background_image_to_inn_room` の呼び出し元は窓の外の1行にだけ付ける
（窓の中は所属する段が分かっている。呼び出し元を組むのは1回あたり約1ms）。
"""

import datetime
import json
import time
import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "vacation.log"
RECORD_BASENAME = "vacation.jsonl"

# 窓の間に写す文言の上限。
# 宿泊の1段は数行のはずで、
# これを超えるなら想定していない経路（会話など）が窓に混ざっている。
# 超えたことも記録する。
TEXT_LIMIT = 30

# 窓を開けるマネージャ。
# すべて targets.txt の実在クラス（`__init__(self, app, months, quality)`。
# End だけ `(self, app)`、SocializeResolve は引数が多い）。
MANAGERS = (
    "VacationStartManager",
    "VacationRestManager",
    "VacationTrainManager",
    "VacationLaborManager",
    "VacationSocializeManager",
    "VacationSocializeResolveManager",
    "VacationBeggingManager",
    "VacationEndManager",
)


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    # 窓は入れ子になりうる（Socialize の中で Resolve が走る等）のでスタックで持つ。
    state = {"windows": []}

    #: 1件1行の JSON。後から数えるための表（ローダの語彙）。
    record = ctx.jsonl(RECORD_BASENAME)

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def age_of(app):
        """`app.player.age` の生の値。型そのものが知りたいので repr で残す。"""
        player = getattr(app, "player", None) if app is not None else None
        return frames.repr_value(getattr(player, "age", None))

    def buttons_brief(app, limit=16):
        """並んでいるボタンを `(text, cls, args)` で写す。"""
        entries = []
        buttons = getattr(app, "buttons", None) if app is not None else None
        if isinstance(buttons, (list, tuple)):
            for entry in buttons[:limit]:
                entries.append({"text": (entry or {}).get("text")
                                if isinstance(entry, dict) else repr(entry),
                                "cls": ui.spec_cls_name(entry),
                                "args": ui.spec_args(entry)})
        return entries

    # ------------------------------------------------- 宿泊の入口（期間の出どころ）
    @ctx.wrap("__main__:DisplayVacationChoice.__init__", required=False, safe=True)
    def choice_init(orig, self, *args, **kwargs):
        """`period_months` の実値と呼び出し元。押された画面のボタンも一緒に写す
        （`宿泊する(3ヵ月)` の spec の `args` の形がここで分かる）。"""
        try:
            app = ui.find_app()
            write("=" * 72)
            write("DisplayVacationChoice(args={} kwargs={})".format(
                frames.repr_value(args[1:]), frames.repr_value(kwargs)))
            write("    from {}".format(frames.caller()))
            # ★ 年齢は毎回ここで一緒に録る。period_months が年齢の変動式
            #   （仕様情報）なら、この2つの列だけで対応表になる。
            write("    player.age = {}".format(age_of(app)))
            entries = buttons_brief(app)
            for entry in entries:
                write("    button: {!r} cls={} args={}".format(
                    entry["text"], entry["cls"], entry["args"]))
            record({"at": now(), "phase": "display_vacation_choice",
                    "init_args": [frames.repr_value(a) for a in args[1:]],
                    "age": age_of(app), "buttons": entries,
                    "gold": ui.gold_of(app)})
        except Exception:
            ctx.log_exc("vacation probe: cannot record the choice init")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:DisplayVacationChoice.update_button_display",
              required=False, safe=True)
    def choice_buttons(orig, self, *args, **kwargs):
        """部屋選びに並ぶボタンを写す。手持ちも一緒に
        （「足りないと部屋が出ない」ビルドかどうかの検証用）。"""
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            entries = buttons_brief(app)
            gold = ui.gold_of(app)
            write("room choice: gold={}".format(gold))
            for entry in entries:
                write("    button: {!r} cls={} args={}".format(
                    entry["text"], entry["cls"], entry["args"]))
            record({"at": now(), "phase": "room_choice", "gold": gold,
                    "age": age_of(app), "buttons": entries})
        except Exception:
            ctx.log_exc("vacation probe: cannot record the room choice")
        return result

    # マネージャの引数の控え。ゲームの実体に属性を足さない（読み取りだけの約束。`231_` と同じ形）。
    inits = weakref.WeakKeyDictionary()
    inits_by_id = {}

    def remember_init(manager, init_args):
        try:
            inits[manager] = init_args
        except TypeError:
            # 弱参照を持てない型。最後の数件だけ id で控える。
            inits_by_id[id(manager)] = init_args
            while len(inits_by_id) > 8:
                inits_by_id.pop(next(iter(inits_by_id)))

    def init_args_of(manager):
        try:
            found = inits.get(manager)
        except TypeError:
            found = None
        return found if found is not None else inits_by_id.get(id(manager))

    # ------------------------------------------------------------ 各段の窓
    def install_windows(cls_name):
        @ctx.wrap("__main__:{}.__init__".format(cls_name), required=False,
                  safe=True)
        def manager_init(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                # 引数の並びを決め打ちしない（`209_` と同じ受け方）。
                # app を除いた位置引数をそのまま控える。
                remember_init(self, [frames.repr_value(a) for a in args[1:]])
            except Exception:
                pass
            return result

        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False)
        def manager_execute(orig, self, *args, **kwargs):
            """窓の前後の所持金と、窓の間の日数・文言・背景切り替えを1行に。

            `safe=True` を付けないのは、本体が投げた例外を窓の片付けの後にそのまま
            上げたいため。本体を呼ぶ前の控えは try に入れ、失敗しても本体は必ず1回呼ぶ（版3）。
            """
            app = choice_text = gold_before = None
            window = {"cls": cls_name, "texts": [], "days": [], "dots": 0,
                      "overflow": 0, "backgrounds": []}
            started = time.monotonic()
            try:
                app = getattr(self, "app", None) or ui.find_app()
                choice_text = frames.arg(args, kwargs, "choice_text", 0)
                gold_before = ui.gold_of(app)
                state["windows"].append(window)
                write("-" * 72)
                write("{}.execute: choice={!r} init_args={} gold={}".format(
                    cls_name, choice_text,
                    init_args_of(self), gold_before))
            except Exception:
                ctx.log_exc("vacation probe: cannot open the window")
            try:
                return orig(self, *args, **kwargs)
            finally:
                try:
                    state["windows"].remove(window)
                except ValueError:
                    pass
                try:
                    gold_after = ui.gold_of(app)
                    row = {
                        "at": now(),
                        "phase": "execute",
                        "cls": cls_name,
                        "choice_text": choice_text,
                        "init_args": init_args_of(self),
                        "gold_before": gold_before,
                        "gold_after": gold_after,
                        "gold_moved": (gold_before - gold_after)
                            if (gold_before is not None
                                and gold_after is not None) else None,
                        "elapse_days_calls": window["days"],
                        "backgrounds": window["backgrounds"],
                        "texts": window["texts"],
                        "texts_dropped": window["overflow"],
                        "loading_dots": window["dots"],
                        "seconds": round(time.monotonic() - started, 1),
                        # 窓が終わった時点で並んでいるボタン。
                        # `VacationStartManager` の後なら**活動の選択肢**がここに写る
                        # （`休養をとる` / `アイテム作成` の spec のクラス名が分かる）。
                        # 部屋選びと違ってここは `DisplayXxxChoice` を通らないので、
                        # 上の2つのフックでは録れない。
                        "buttons_after": buttons_brief(app),
                    }
                    write("{} done: gold {} -> {} (moved {}) days={} bg={} "
                          "texts={} dots={} in {}s".format(
                              cls_name, gold_before, gold_after,
                              row["gold_moved"], window["days"],
                              window["backgrounds"], len(window["texts"]),
                              window["dots"], row["seconds"]))
                    for text in window["texts"]:
                        write("    text: {!r}".format(text))
                    for entry in row["buttons_after"]:
                        write("    after: {!r} cls={} args={}".format(
                            entry["text"], entry["cls"], entry["args"]))
                    record(row)
                except Exception:
                    ctx.log_exc("vacation probe: cannot record the window")

    for name in MANAGERS:
        install_windows(name)

    # ------------------------------------------------- 窓の間の日数・文言・背景
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        if state["windows"]:
            try:
                days = frames.arg(args, kwargs, "days", 0)
                state["windows"][-1]["days"].append(days)
                write("elapse_days({!r}) in {}".format(
                    days, state["windows"][-1]["cls"]))
            except Exception:
                pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.change_background_image_to_inn_room",
              required=False, safe=True)
    def inn_room_background(orig, self, *args, **kwargs):
        """`quality` の実値がここで裸のまま観測できる。窓の外でも録る。"""
        try:
            quality = frames.arg(args, kwargs, "quality", 0)
            holder = state["windows"][-1]["backgrounds"] if state["windows"] \
                else None
            if holder is not None:
                write("change_background_image_to_inn_room(quality={!r}) in {}"
                      .format(quality, state["windows"][-1]["cls"]))
                holder.append(frames.repr_value(quality))
            else:
                write("change_background_image_to_inn_room(quality={!r}) from {}"
                      .format(quality, frames.caller()))
                record({"at": now(), "phase": "inn_background",
                        "quality": frames.repr_value(quality)})
        except Exception:
            ctx.log_exc("vacation probe: cannot record the background")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, *args, **kwargs):
        context = frames.arg(args, kwargs, "context", 0) if state["windows"] else None
        if state["windows"] and isinstance(context, str):
            try:
                window = state["windows"][-1]
                if context.strip() and not context.strip(".。 　"):
                    window["dots"] += 1          # 待機表示の点は数だけ
                elif len(window["texts"]) < TEXT_LIMIT:
                    window["texts"].append(context)
                else:
                    window["overflow"] += 1
            except Exception:
                pass
        return orig(self, *args, **kwargs)

    ctx.log("vacation probe installed; log={} records={}".format(
        log_path, record_path))
