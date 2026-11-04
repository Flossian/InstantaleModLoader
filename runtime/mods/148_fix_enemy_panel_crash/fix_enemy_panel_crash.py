# -*- coding: utf-8 -*-
"""修正: 戦闘中に持ち物を開いても、敵の欄の更新でゲームが落ちない。

## 原因（GAME.md §2.10、VERIFICATION.md §3.123）

点送りの最中に持ち物を開くと、ゲームは戦闘の欄（`top_info_layout_battle`）を大きさ 0 に畳む。
敵の欄の更新（`update_enemy_display`）は欄の高さで割るので、畳まれている間に走ると
`ZeroDivisionError` でゲームごと落ちる。

## 直し方

更新は飛ばさない。途中で止まると敵の絵の一覧が組み直されず、次の手が `resolve_opponents` の `KeyError` で止まる。
畳まれている間だけ欄に高さを与えて更新を最後まで走らせ、欄が戻ったら最後の更新をもう一度走らせて描き直す。
敵が居ないときは戦闘が終わっているので触らない（切り上げの直後の扱いは `instantale_modloader.defeat`）。
"""

from instantale_modloader import ui

LOG_BASENAME = "enemy_panel_crash.log"

TARGET = "scripts.hud.new_hud:InstanTaleHUD.update_enemy_display"
#: 戦闘の欄（敵の欄の更新が高さで割る相手）。
PANEL_ATTR = "top_info_layout_battle"
#: 畳まれている間の更新に与える高さ。欄が戻ったときに描き直すので、0 でなければ足りる。
FOLDED_HEIGHT = 456.3
#: 畳まれた欄が戻ったかを見る間隔（秒）。
POLL_SECONDS = 0.25


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    # 欄が戻ったら走らせる最後の更新 (orig, self, args, kwargs)。見張りが動いている間だけ入っている。
    state = {"pending": None}

    def enemies_left():
        return bool(getattr(ui.find_app(), "current_enemy_dict", None))

    def folded_panel(hud):
        """戦闘の途中で畳まれている欄。畳まれていない・敵が居ないなら None。"""
        panel = getattr(hud, PANEL_ATTR, None)
        height = getattr(panel, "height", None)
        if not isinstance(height, (int, float)) or height > 0:
            return None
        return panel if enemies_left() else None

    def redraw_when_unfolded(panel, update):
        """欄が戻ったら最後の更新をもう一度走らせる。見張りは1本だけで、走らせる更新は最後のものに置き換える。"""
        watching = state["pending"] is not None
        state["pending"] = update
        if watching:
            return
        from kivy.clock import Clock

        def tick(_dt):
            pending = state["pending"]
            if pending is None:
                return False
            if ctx.superseded() or not enemies_left():
                state["pending"] = None
                return False
            if panel.height <= 0:
                return True
            state["pending"] = None
            try:
                pending[0](pending[1], *pending[2], **pending[3])
                write("redrew the enemy panel after it unfolded")
            except Exception:
                ctx.log_exc("enemy panel crash: cannot redraw the enemy panel")
            return False

        write("the enemy panel updated while folded; keeping the update whole")
        Clock.schedule_interval(tick, POLL_SECONDS)

    @ctx.wrap(TARGET, required=False, safe=True)
    def update_enemy_display(orig, self, *args, **kwargs):
        panel = folded_panel(self)
        if panel is None:
            return orig(self, *args, **kwargs)
        height = panel.height
        panel.height = FOLDED_HEIGHT
        try:
            result = orig(self, *args, **kwargs)
        finally:
            panel.height = height
        redraw_when_unfolded(panel, (orig, self, args, kwargs))
        return result
