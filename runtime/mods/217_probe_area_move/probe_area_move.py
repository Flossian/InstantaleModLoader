# -*- coding: utf-8 -*-
"""計測: エリア移動（土地から土地へ）の未実測部分を録る。

##### 何を決めるための計測か

`314_area_move_custom` がエリア移動の日数・料金・文言を差し替えるが、
GAME.md §2.18 の実測には穴が残っている（すべて `徒歩` 側の1経路の実測しかない）:

| 未実測 | この計測での見どころ |
|---|---|
| 馬車の移動中の文言 | 移動の窓の間の `add_text` を全部写す（`314_` の `DEPART_MARKS` に足すため） |
| 馬車の日数 | 窓の間の `elapse_days` の引数（徒歩の 90 に対する実値） |
| 馬車代の徴収箇所と時機 | `execute` の前後の所持金の差。窓の中で動けば「徴収は execute の中」が確定する |
| 手持ちが運賃に満たないときの挙動 | 馬車のボタンは出るのか・押すとどうなるのか・`AreaMoveRestriction` は出るのか |

3つ目と4つ目は `314_` の料金差し替え（建て替え方式）の前提そのもの。
建て替えは「残高チェックが `execute` の中で起きる」ときだけ正しく働く。

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。
`safe=True` と握り潰しで、記録に失敗しても本体は必ず呼ぶ。

##### 適用順とラベルの見え方

この計測は `314_` / `307_` より後（外側）に置くので、
`elapse_days` と `add_text` は **MOD が差し替える前のゲームの生の値**が録れる（TECH.md
§3.2.2 の「計測は修正より後」）。
日数を差し替えるのは MOD ではなくローダの日数送りの関所（`durations`。TECH.md §3.3.3）で、
関所が立つのは適用順で最初に日数を望む MOD（`314_`）の時点。
そこより外に居るので、ここへ来るのは関所が触る前の数になる。
一方 `update_button_display` のラベルだけは orig が返った後に読むため、
`314_` が書き換えた後の姿になる。
生のラベルが要るときは `314_` を切って録る（spec の `args` はどちらでも生のまま）。

##### 出力

`out/area_move.log`（読む用）と `out/area_move.jsonl`（1移動=1行、突き合わせる用）。

版2: 移動の窓（`AreaMoveManager.execute`）の包みで、本体を呼ぶ前の控え
（所持金・土地・ログの見出し）を try に入れた。版1ではここが投げると
本体が呼ばれずに移動そのものが止まる形だった（実機で踏んだ記録は無い）。
あわせて全部の包みを、受け取った引数をそのまま本体へ渡す形にした
（キーワードで来た引数を位置へ直さない。直すと内側の MOD の食い違いが
デバッグモードのときだけ隠れる。VERIFICATION.md §3.76 の `333_` の件）。
"""

import datetime
import json
import time
import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "area_move.log"
RECORD_BASENAME = "area_move.jsonl"

# 1回の移動で写す文言の上限。
# 移動中の文言は数行のはずで、
# これを超えるなら想定していない経路（会話など）が窓に混ざっている。
# 超えたことも記録する。
TEXT_LIMIT = 30


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    state = {"window": None}

    #: 1件1行の JSON。後から数えるための表（ローダの語彙）。
    record = ctx.jsonl(RECORD_BASENAME)

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def area_brief(app):
        area = ui.current_area(app)
        return {"id": ui.area_id_of(area), "name": getattr(area, "name", None)}

    # ------------------------------------------------------------ 確認画面
    @ctx.wrap("__main__:AreaMoveCofirmation.update_button_display", required=False,
              safe=True)
    def confirmation(orig, self, *args, **kwargs):
        """並んだ手段のボタンを写す。手持ちも一緒に（「足りないと出ない」の検証用）。"""
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            buttons = getattr(app, "buttons", None) if app is not None else None
            entries = []
            if isinstance(buttons, (list, tuple)):
                for entry in buttons:
                    if ui.spec_cls_name(entry) == "AreaMoveManager":
                        entries.append({"text": entry.get("text"),
                                        "args": ui.spec_args(entry)})
            if entries:
                gold = ui.gold_of(app)
                write("confirm: gold={} options={}".format(
                    gold, [(e["text"], e["args"]) for e in entries]))
                record({"at": now(), "phase": "confirm", "gold": gold,
                        "area": area_brief(app), "options": entries})
        except Exception:
            ctx.log_exc("area move probe: cannot record the confirmation")
        return result

    @ctx.wrap("__main__:AreaMoveCofirmation.execute", required=False, safe=True)
    def confirmation_execute(orig, self, *args, **kwargs):
        try:
            write("confirm pressed: {!r}".format(
                frames.arg(args, kwargs, "choice_text", 0)))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------- 行けないときの画面
    @ctx.wrap("__main__:AreaMoveRestriction.__init__", required=False, safe=True)
    def restriction(orig, self, *args, **kwargs):
        """`AreaMoveRestriction` が「手持ち不足」の受け皿かどうかを見る。"""
        try:
            app = frames.arg(args, kwargs, "app", 0)
            target_area_id = frames.arg(args, kwargs, "target_area_id", 1)
            gold = ui.gold_of(app)
            write("restriction: target={!r} gold={}".format(target_area_id, gold))
            record({"at": now(), "phase": "restriction",
                    "target_area_id": str(target_area_id), "gold": gold,
                    "area": area_brief(app)})
        except Exception:
            ctx.log_exc("area move probe: cannot record the restriction")
        return orig(self, *args, **kwargs)

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

    # ------------------------------------------------------------ 移動そのもの
    @ctx.wrap("__main__:AreaMoveManager.__init__", required=False, safe=True)
    def move_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            remember_init(self, {
                "target_id": str(frames.arg(args, kwargs, "target_area_id", 1)),
                "mode": str(frames.arg(args, kwargs, "mode", 2))})
        except Exception:
            pass
        return result

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False)
    def move_execute(orig, self, *args, **kwargs):
        """移動の窓。前後の所持金・エリアと、窓の間の日数・文言をまとめて1行に。

        `safe=True` を付けないのは、本体が投げた例外を窓の片付けの後にそのまま
        上げたいため。本体を呼ぶ前の控えは try に入れ、失敗しても本体は必ず1回呼ぶ（版2）。
        """
        app = info = choice_text = gold_before = origin = None
        window = {"texts": [], "days": [], "dots": 0, "overflow": 0}
        started = time.monotonic()
        try:
            app = getattr(self, "app", None) or ui.find_app()
            info = init_args_of(self) or {}
            choice_text = frames.arg(args, kwargs, "choice_text", 0)
            gold_before = ui.gold_of(app)
            origin = area_brief(app)
            state["window"] = window
            write("=" * 72)
            write("move: mode={!r} target={!r} choice={!r} gold={} from {}".format(
                info.get("mode"), info.get("target_id"), choice_text,
                gold_before, origin))
        except Exception:
            ctx.log_exc("area move probe: cannot open the move window")
        info = info or {}
        try:
            return orig(self, *args, **kwargs)
        finally:
            state["window"] = None
            try:
                gold_after = ui.gold_of(app)
                row = {
                    "at": now(),
                    "phase": "move",
                    "mode": info.get("mode"),
                    "target_area_id": info.get("target_id"),
                    "choice_text": choice_text,
                    "origin": origin,
                    "area_after": area_brief(app),
                    "gold_before": gold_before,
                    "gold_after": gold_after,
                    "gold_moved": (gold_before - gold_after)
                        if (gold_before is not None and gold_after is not None)
                        else None,
                    "elapse_days_calls": window["days"],
                    "texts": window["texts"],
                    "texts_dropped": window["overflow"],
                    "loading_dots": window["dots"],
                    "seconds": round(time.monotonic() - started, 1),
                }
                write("move done: gold {} -> {} (moved {}) days={} texts={} "
                      "dots={} in {}s".format(
                          gold_before, gold_after, row["gold_moved"],
                          window["days"], len(window["texts"]),
                          window["dots"], row["seconds"]))
                for text in window["texts"]:
                    write("    text: {!r}".format(text))
                record(row)
            except Exception:
                ctx.log_exc("area move probe: cannot record the move")

    # ------------------------------------------------- 窓の間の日数と文言
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        window = state["window"]
        if window is not None:
            try:
                days = frames.arg(args, kwargs, "days", 0)
                window["days"].append(days)
                write("elapse_days({!r})".format(days))
            except Exception:
                pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, *args, **kwargs):
        window = state["window"]
        context = frames.arg(args, kwargs, "context", 0) if window is not None else None
        if window is not None and isinstance(context, str):
            try:
                if context.strip() and not context.strip(".。 　"):
                    window["dots"] += 1          # 待機表示の点は数だけ
                elif len(window["texts"]) < TEXT_LIMIT:
                    window["texts"].append(context)
                else:
                    window["overflow"] += 1
            except Exception:
                pass
        return orig(self, *args, **kwargs)

    ctx.log("area move probe installed; log={} records={}".format(
        log_path, record_path))
