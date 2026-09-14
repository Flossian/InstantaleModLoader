# -*- coding: utf-8 -*-
r"""計測: 施設での訓練。ゲームは変えない。

ローダの `durations`（TECH.md §3.3.2）に訓練の期間を載せるための下調べ。
分かっていないのは次の4つで、GAME.md にも検証記録にも数字が無い。

    1. 訓練の選択肢に並ぶ年数と代金がどう決まるか
       （`DisplayTrainingChoice(app, training_type)` が組むボタンの spec の args）
    2. 代金がどこで引かれるか（`TrainingStartManager.execute` の前後の所持金）
    3. 1年で暦が何日進むか、どの段で進むか（`elapse_days` に来る数と呼び出し元）
    4. 各段が次へ渡す値（`TrainingPhaseManager(app, training_type, remaining_years,
       training_log)` の引数）

録り方は `218_probe_vacation` と同じ。マネージャの `execute` を窓にして、
窓の前後の所持金と日付、窓の間の `elapse_days`・文言・次に並ぶボタンを1行にまとめる。
`elapse_days` は窓の外でも呼び出し元つきで残す（窓の外の Clock で進むビルドなら、
そこに出る）。

    out\training.log      読む用
    out\training.jsonl    1窓＝1行。後から数える用

対象はすべて `targets.txt` の実在クラス（GAME.md §2.17 の表）。
"""
import datetime
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "training.log"
RECORD_BASENAME = "training.jsonl"

#: 窓の間に写す文言の上限。訓練の1段は数行のはず。超えた数も残す。
TEXT_LIMIT = 30

#: 窓を開けるマネージャ（`__init__` の並びは GAME.md §2.17 の表）。
#:   TrainingStartManager(app, training_years, training_price)
#:   TrainingPhaseManager(app, training_type, remaining_years, training_log)
MANAGERS = ("TrainingStartManager", "TrainingPhaseManager")


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    state = {"windows": []}

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def age_of(app):
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

    def log_buttons(app, head):
        entries = buttons_brief(app)
        write("{}: gold={} day={}".format(head, ui.gold_of(app), ui.game_day(app)))
        for entry in entries:
            write("    button: {!r} cls={} args={}".format(
                entry["text"], entry["cls"], entry["args"]))
        return entries

    # ------------------------------------------------ 選択肢（年数と代金の出どころ）
    @ctx.wrap("__main__:DisplayTrainingChoice.__init__", required=False, safe=True)
    def choice_init(orig, self, *args, **kwargs):
        """`training_type` の実値と呼び出し元。押された画面のボタンも写す。"""
        try:
            app = ui.find_app()
            write("=" * 72)
            write("DisplayTrainingChoice(args={} kwargs={})".format(
                frames.repr_value(args[1:]), frames.repr_value(kwargs)))
            write("    from {}".format(frames.caller()))
            write("    player.age = {}  level = {}".format(
                age_of(app),
                frames.repr_value(getattr(getattr(app, "player", None),
                                          "experience_level", None))))
            entries = log_buttons(app, "    pressed screen")
            record({"at": now(), "phase": "display_training_choice",
                    "init_args": [frames.repr_value(a) for a in args[1:]],
                    "age": age_of(app), "gold": ui.gold_of(app),
                    "day": ui.game_day(app), "buttons": entries})
        except Exception:
            ctx.log_exc("training probe: cannot record the choice init")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:DisplayTrainingChoice.update_button_display",
              required=False, safe=True)
    def choice_buttons(orig, self, *args, **kwargs):
        """年数と代金の選択肢そのもの（spec の args に年数と代金が載るはず）。"""
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            entries = log_buttons(app, "training choice")
            record({"at": now(), "phase": "training_choice", "gold": ui.gold_of(app),
                    "age": age_of(app), "day": ui.game_day(app), "buttons": entries})
        except Exception:
            ctx.log_exc("training probe: cannot record the training choice")
        return result

    @ctx.wrap("__main__:DisplayTrainingChoice.execute", required=False, safe=True)
    def choice_execute(orig, self, choice_text=None, *args, **kwargs):
        """押された文言（`訓練を受ける((数)G)` の形）と、その後に並ぶもの。"""
        write("DisplayTrainingChoice.execute: choice={!r}".format(choice_text))
        result = orig(self, choice_text, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            log_buttons(app, "after the choice")
        except Exception:
            ctx.log_exc("training probe: cannot record after the choice")
        return result

    # ------------------------------------------------------------ 各段の窓
    def install_windows(cls_name):
        @ctx.wrap("__main__:{}.__init__".format(cls_name), required=False,
                  safe=True)
        def manager_init(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                # 引数の並びを決め打ちしない。app を除いた位置引数をそのまま控える。
                self._probe_training = [frames.repr_value(a) for a in args[1:]]
                write("{}.__init__(args={} kwargs={}) from {}".format(
                    cls_name, self._probe_training, frames.repr_value(kwargs),
                    frames.caller()))
            except Exception:
                pass
            return result

        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False)
        def manager_execute(orig, self, choice_text=None, *args, **kwargs):
            """窓の前後の所持金と日付、窓の間の日数・文言を1行に。"""
            app = getattr(self, "app", None) or ui.find_app()
            window = {"cls": cls_name, "texts": [], "days": [], "dots": 0,
                      "overflow": 0}
            gold_before = ui.gold_of(app)
            day_before = ui.game_day(app)
            started = time.monotonic()
            state["windows"].append(window)
            try:
                write("-" * 72)
                write("{}.execute: choice={!r} init_args={} gold={} day={}".format(
                    cls_name, choice_text,
                    getattr(self, "_probe_training", None), gold_before, day_before))
                return orig(self, choice_text, *args, **kwargs)
            finally:
                try:
                    state["windows"].remove(window)
                except ValueError:
                    pass
                try:
                    gold_after = ui.gold_of(app)
                    day_after = ui.game_day(app)
                    row = {
                        "at": now(),
                        "phase": "execute",
                        "cls": cls_name,
                        "choice_text": choice_text,
                        "init_args": getattr(self, "_probe_training", None),
                        "gold_before": gold_before,
                        "gold_after": gold_after,
                        "gold_moved": (gold_before - gold_after)
                            if (gold_before is not None
                                and gold_after is not None) else None,
                        "day_before": day_before,
                        "day_after": day_after,
                        "days_moved": (day_after - day_before)
                            if (isinstance(day_before, int)
                                and isinstance(day_after, int)) else None,
                        "elapse_days_calls": window["days"],
                        "texts": window["texts"],
                        "texts_dropped": window["overflow"],
                        "loading_dots": window["dots"],
                        "buttons_after": buttons_brief(app),
                        "seconds": round(time.monotonic() - started, 1),
                    }
                    write("{} done: gold {} -> {} (moved {}) day {} -> {} (moved {}) "
                          "elapse_days={} texts={} dots={} in {}s".format(
                              cls_name, gold_before, gold_after, row["gold_moved"],
                              day_before, day_after, row["days_moved"],
                              window["days"], len(window["texts"]),
                              window["dots"], row["seconds"]))
                    for text in window["texts"]:
                        write("    text: {!r}".format(text))
                    for entry in row["buttons_after"]:
                        write("    next button: {!r} cls={} args={}".format(
                            entry["text"], entry["cls"], entry["args"]))
                    record(row)
                except Exception:
                    ctx.log_exc("training probe: cannot record the window")

    for name in MANAGERS:
        install_windows(name)

    # ------------------------------------------------- 日数送りと文言（窓の内外）
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days, *args, **kwargs):
        """窓の中なら窓に足す。外でも呼び出し元つきで残す（Clock で進むビルドの検出）。"""
        try:
            if state["windows"]:
                state["windows"][-1]["days"].append(days)
                write("elapse_days({!r}) in {}".format(days, state["windows"][-1]["cls"]))
            else:
                write("elapse_days({!r}) outside any training window, from {}".format(
                    days, frames.caller()))
        except Exception:
            pass
        return orig(self, days, *args, **kwargs)

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

    ctx.log("training probe: ready ({}, {})".format(LOG_BASENAME, RECORD_BASENAME))
