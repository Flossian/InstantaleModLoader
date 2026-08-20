# -*- coding: utf-8 -*-
"""ゲーム本体が早く切る長い本文を、表示ラベルでは末尾まで多く見せる。

保持している本文（display_text / 履歴 / セーブ）は触らない。
画面の `hud.text_display` だけを、省略通知付きで末尾 DISPLAY_CHARS 文字へ載せ直す。
"""

import weakref

from instantale_modloader import frames

# 表示ラベルに載せる末尾の文字数。
# 上げすぎると何も描かれなくなる。
# Kivy の Label は中身を1枚のテクスチャに焼くので、
# GPU の上限（多くの環境で 16384px）を超えると**例外も出さずに空になる**（`122_` が実機で踏んだ。VERIFICATION.md
# §3.21）。
# 既定の 1000 文字はおよそ 1,500px で十分安全だが、
# `mod.json` の上限（10000）まで上げたうえに高解像度で書体が大きいと、
# 理屈のうえでは届きうる。
# 本文が丸ごと消えたらまずここを下げること。
DISPLAY_CHARS = 1000
TRUNCATED_NOTICE = "［表示負荷を抑えるため、前の本文は省略］\n"

def apply(ctx):
    states = weakref.WeakKeyDictionary()
    fallback_states = {}

    def state_for(hud):
        try:
            state = states.get(hud)
            if state is None:
                state = {"prefix": "", "pending": False}
                states[hud] = state
            return state
        except TypeError:
            return fallback_states.setdefault(
                id(hud), {"prefix": "", "pending": False})

    def label_for(hud):
        label = frames.attr(hud, "text_display")
        if label in (None, frames.MISSING):
            return None
        text = frames.attr(label, "text")
        update = frames.attr(label, "texture_update")
        if not isinstance(text, str) or not callable(update):
            return None
        return label

    def settle(hud, label, state):
        if state["pending"]:
            return

        def run(_dt=None, hud=hud, label=label, state=state):
            state["pending"] = False
            try:
                # `texture_update()` は呼ばない。
                # これを呼ぶと、本文が1文字進むたびにラベルを余計に作り直すことになる（1回 15ms ＝ ティックの間隔の 3分の2。VERIFICATION_LOG.md
                # §2.34）。
                # Kivy の作り直しはこの予約より先に走るので、
                # 高さを出す時点の `texture_size` は既に新しい。
                update_height = frames.attr(hud, "update_label_height")
                if callable(update_height):
                    update_height()
            except Exception:
                ctx.log_exc("message text integrity: could not settle label height")

        try:
            from kivy.clock import Clock
        except Exception:
            run()
            return
        state["pending"] = True
        try:
            Clock.schedule_once(run, 0)
        except Exception:
            state["pending"] = False
            ctx.log_exc("message text integrity: could not schedule height update")

    def limit_display(hud, value):
        """ラベルを「前置き + 省略通知 + 末尾 DISPLAY_CHARS 文字」へ載せ替える。

        前置き（`state["prefix"]`）を控える理由と、それが貼り付いたままになる
        未検証の穴は VERIFICATION.md §3.29 にまとめてある。
        要点だけ: 塗る相手は `display_text` ではなく画面の
        `text_display.text` で、この2つは一致しない（GAME.md §2.3）。
        載せ替えた後は `endswith` が二度と成立しないので、
        切り出せる機会は1回しかなく、持ち越すしかない。
        """
        state = state_for(hud)
        if not value:
            state["prefix"] = ""
            return

        label = label_for(hud)
        if label is None:
            return
        shown = frames.attr(label, "text")
        if not isinstance(shown, str):
            return
        if shown.endswith(value):
            state["prefix"] = shown[:-len(value)]
        if len(value) <= DISPLAY_CHARS:
            bounded = state["prefix"] + value
        else:
            bounded = state["prefix"] + TRUNCATED_NOTICE + value[-DISPLAY_CHARS:]
        if bounded == shown:
            return
        label.text = bounded
        settle(hud, label, state)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text", safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        result = orig(self, instance, value, *args, **kwargs)
        canonical = frames.attr(self, "display_text")
        if not isinstance(canonical, str):
            canonical = value
        if isinstance(canonical, str):
            limit_display(self, canonical)
        return result

    ctx.log("message text integrity installed (DISPLAY_CHARS={})".format(
        DISPLAY_CHARS))
