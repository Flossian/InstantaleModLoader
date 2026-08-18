# -*- coding: utf-8 -*-
"""計測: 宿の宿泊（休暇）の未実測部分を録る。

##### 何を決めるための計測か

`315_vacation_custom` が宿泊の期間・部屋の名前・宿代を差し替える。
その前提は **2026-08-18 の実機で全部確定した**（GAME.md §2.17 / VERIFICATION.md
§3.28）:

| 項目 | 実測の結果 |
|---|---|
| 宿代の徴収 | `VacationStartManager.execute` の中で1回（窓の前後で `gold 19288 -> 19188`） |
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
一方ボタンのラベルは描かれる直前に `315_` が書き換えるため、
ここで写る `text` は書き換え後の姿になることがある。
生のラベルが要るときは `315_` を切って録る（spec の `args` はどちらでも生のまま）。

##### 出力

`out/vacation.log`（読む用）と `out/vacation.jsonl`（1画面・1窓=1行、
突き合わせる用）。
"""

import datetime
import json
import time

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

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("vacation probe: record failed")

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

    # ------------------------------------------------------------ 各段の窓
    def install_windows(cls_name):
        @ctx.wrap("__main__:{}.__init__".format(cls_name), required=False,
                  safe=True)
        def manager_init(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                # 引数の並びを決め打ちしない（`209_` と同じ受け方）。
                # app を除いた位置引数をそのまま控える。
                self._probe_vacation = [frames.repr_value(a) for a in args[1:]]
            except Exception:
                pass
            return result

        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False)
        def manager_execute(orig, self, choice_text=None, *args, **kwargs):
            """窓の前後の所持金と、窓の間の日数・文言・背景切り替えを1行に。"""
            app = getattr(self, "app", None) or ui.find_app()
            window = {"cls": cls_name, "texts": [], "days": [], "dots": 0,
                      "overflow": 0, "backgrounds": []}
            gold_before = ui.gold_of(app)
            started = time.monotonic()
            state["windows"].append(window)
            try:
                write("-" * 72)
                write("{}.execute: choice={!r} init_args={} gold={}".format(
                    cls_name, choice_text,
                    getattr(self, "_probe_vacation", None), gold_before))
                return orig(self, choice_text, *args, **kwargs)
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
                        "init_args": getattr(self, "_probe_vacation", None),
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
                    }
                    write("{} done: gold {} -> {} (moved {}) days={} bg={} "
                          "texts={} dots={} in {}s".format(
                              cls_name, gold_before, gold_after,
                              row["gold_moved"], window["days"],
                              window["backgrounds"], len(window["texts"]),
                              window["dots"], row["seconds"]))
                    for text in window["texts"]:
                        write("    text: {!r}".format(text))
                    record(row)
                except Exception:
                    ctx.log_exc("vacation probe: cannot record the window")

    for name in MANAGERS:
        install_windows(name)

    # ------------------------------------------------- 窓の間の日数・文言・背景
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days, *args, **kwargs):
        if state["windows"]:
            try:
                state["windows"][-1]["days"].append(days)
                write("elapse_days({!r}) in {}".format(
                    days, state["windows"][-1]["cls"]))
            except Exception:
                pass
        return orig(self, days, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.change_background_image_to_inn_room",
              required=False, safe=True)
    def inn_room_background(orig, self, quality=None, *args, **kwargs):
        """`quality` の実値がここで裸のまま観測できる。窓の外でも録る。"""
        try:
            holder = state["windows"][-1]["backgrounds"] if state["windows"] \
                else None
            write("change_background_image_to_inn_room(quality={!r}) from {}"
                  .format(quality, frames.caller()))
            if holder is not None:
                holder.append(frames.repr_value(quality))
            else:
                record({"at": now(), "phase": "inn_background",
                        "quality": frames.repr_value(quality)})
        except Exception:
            ctx.log_exc("vacation probe: cannot record the background")
        return orig(self, quality, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
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
        return orig(self, context, *args, **kwargs)

    ctx.log("vacation probe installed; log={} records={}".format(
        log_path, record_path))
