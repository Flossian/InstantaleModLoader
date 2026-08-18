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
一方 `update_button_display` のラベルだけは orig が返った後に読むため、
`314_` が書き換えた後の姿になる。
生のラベルが要るときは `314_` を切って録る（spec の `args` はどちらでも生のまま）。

##### 出力

`out/area_move.log`（読む用）と `out/area_move.jsonl`（1移動=1行、突き合わせる用）。
"""

import datetime
import json
import time

from instantale_modloader import ui

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

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("area move probe: record failed")

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
    def confirmation_execute(orig, self, choice_text=None, *args, **kwargs):
        try:
            write("confirm pressed: {!r}".format(choice_text))
        except Exception:
            pass
        return orig(self, choice_text, *args, **kwargs)

    # ------------------------------------------------------- 行けないときの画面
    @ctx.wrap("__main__:AreaMoveRestriction.__init__", required=False, safe=True)
    def restriction(orig, self, app, target_area_id, *args, **kwargs):
        """`AreaMoveRestriction` が「手持ち不足」の受け皿かどうかを見る。"""
        try:
            gold = ui.gold_of(app)
            write("restriction: target={!r} gold={}".format(target_area_id, gold))
            record({"at": now(), "phase": "restriction",
                    "target_area_id": str(target_area_id), "gold": gold,
                    "area": area_brief(app)})
        except Exception:
            ctx.log_exc("area move probe: cannot record the restriction")
        return orig(self, app, target_area_id, *args, **kwargs)

    # ------------------------------------------------------------ 移動そのもの
    @ctx.wrap("__main__:AreaMoveManager.__init__", required=False, safe=True)
    def move_init(orig, self, app, target_area_id, mode, *args, **kwargs):
        result = orig(self, app, target_area_id, mode, *args, **kwargs)
        try:
            self._probe_area_move = {"target_id": str(target_area_id),
                                     "mode": str(mode)}
        except Exception:
            pass
        return result

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False)
    def move_execute(orig, self, choice_text=None, *args, **kwargs):
        """移動の窓。前後の所持金・エリアと、窓の間の日数・文言をまとめて1行に。"""
        app = getattr(self, "app", None) or ui.find_app()
        info = getattr(self, "_probe_area_move", None) or {}
        window = {"texts": [], "days": [], "dots": 0, "overflow": 0}
        gold_before = ui.gold_of(app)
        origin = area_brief(app)
        started = time.monotonic()
        state["window"] = window
        try:
            write("=" * 72)
            write("move: mode={!r} target={!r} choice={!r} gold={} from {}".format(
                info.get("mode"), info.get("target_id"), choice_text,
                gold_before, origin))
            return orig(self, choice_text, *args, **kwargs)
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
    def elapse_days(orig, self, days, *args, **kwargs):
        window = state["window"]
        if window is not None:
            try:
                window["days"].append(days)
                write("elapse_days({!r})".format(days))
            except Exception:
                pass
        return orig(self, days, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        window = state["window"]
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
        return orig(self, context, *args, **kwargs)

    ctx.log("area move probe installed; log={} records={}".format(
        log_path, record_path))
