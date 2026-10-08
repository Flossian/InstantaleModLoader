# -*- coding: utf-8 -*-
"""修正: 待機表示の「…」が倍の速さで流れるのを止める。

##### 何が起きているか

待機中の点（`.` → `..` → `...`）は、ゲームの `InstantaleApp.display_button_load` が
約0.3秒ごとに自分を予約し直して送っている（GAME.md §2.4）。
待機を始める `scripts.functions.button_load` は、そのたびに点送りを1本始める。
ゲームは1回の待機の中で `button_load` を何度も呼ぶので（会話の始まり・会話の続き・
戦闘の始めと終わり・審判・依頼探しなど）、点送りが2本・4本と並んで回り、
1回の周期に何コマも進む。並んだ本数だけ点が速く流れる。
誰が始めたかは `234_probe_busy_display` の版4で測った（GAME.md §2.4）。

##### 直し方

`display_button_load` を包み、待機中に呼ばれたとき、別の `display_button_load` が
もう Clock に載っていれば、ゲームの処理を呼ばずに終える（その本はそこで止まる）。
何本並んでも1本に戻る。待機が明けた後（`is_button_enabled` が真）はいつもゲームの処理を呼ぶ。

Clock は1回きりの予約を、呼び出す前に一覧から外す（Kivy 2.3.0 の `ClockEvent.tick`）。
だから Clock から呼ばれた本の中で一覧に見えるのは、ほかの本だけ。
繰り返しの予約（`loop`）は数えない。数えると自分を見て止まり、点が出なくなる。

点の文字は今の一覧の文字から次のコマを決めるので、本数が変わっても並びは崩れない。
"""

LOG_BASENAME = "busy_dots_speed.log"

#: ログの上限（止めた本ごとに1行）。
MAX_LINES = 300


def queued_loads(clock, app):
    """Clock に載っている、`app` の `display_button_load` の1回きりの予約の数。読めなければ None。"""
    try:
        events = clock.get_events()
    except Exception:
        return None
    count = 0
    for event in events or ():
        if getattr(event, "loop", False):
            continue
        try:
            callback = event.get_callback()
        except Exception:
            continue
        if getattr(callback, "__name__", "") != "display_button_load":
            continue
        if getattr(callback, "__self__", app) is app:
            count += 1
    return count


def apply(ctx):
    append = ctx.logger(LOG_BASENAME)
    state = {"lines": 0, "stopped": 0}

    def write(text):
        if state["lines"] >= MAX_LINES:
            return
        state["lines"] += 1
        append(text)

    try:
        from kivy.clock import Clock
    except Exception:
        ctx.log("busy dots speed: no kivy Clock; nothing to do", level="WARN")
        return

    @ctx.wrap("__main__:InstantaleApp.display_button_load", required=False)
    def display_button_load(orig, self, *args, **kwargs):
        try:
            if getattr(self, "is_button_enabled", None) is False:
                others = queued_loads(Clock, self)
                if others:
                    state["stopped"] += 1
                    write("stopped an extra run of dots ({} more queued, {} so far)".format(
                        others, state["stopped"]))
                    return None
        except Exception:
            ctx.log_exc("busy dots speed: cannot count the queued dots")
        return orig(self, *args, **kwargs)

    ctx.log("busy dots speed: keeps one run of dots while waiting")
