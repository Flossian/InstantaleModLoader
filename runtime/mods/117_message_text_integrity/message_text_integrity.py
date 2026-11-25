# -*- coding: utf-8 -*-
"""長い本文の表示を末尾に絞り、本文が消えるのを防ぐ。

ゲームが溜める本文（`hud.display_text`）は切られずに伸び続け、数十万文字になる。
表示ラベルは長すぎると何も描かれなくなる（下の `DISPLAY_CHARS`）。
保持している本文（display_text / 履歴 / セーブ）は触らない。
画面の `hud.text_display` だけを、省略通知付きで末尾 DISPLAY_CHARS 文字へ載せ直す。
"""

import weakref

from instantale_modloader import frames
# `hud` は下で HUD の引数名に使っているので別名で読む。
from instantale_modloader import hud as hud_window

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
    # HUD ごとの控え。
    # 載せ替えるときに切り落とす前置き（`prefix`）を次の1文字へ持ち越すために要る。
    # 弱参照を張れない相手には `WeakKeyDictionary` が TypeError を出すので、
    # そのときだけ id 引きの辞書へ倒す。
    states = weakref.WeakKeyDictionary()
    fallback_states = {}

    def state_for(hud):
        """この HUD の控えを取り出す。無ければ作る。"""
        try:
            state = states.get(hud)
            if state is None:
                state = {"prefix": ""}
                states[hud] = state
            return state
        except TypeError:
            return fallback_states.setdefault(id(hud), {"prefix": ""})

    def label_for(hud):
        """載せ替えてよい表示ラベルだけを返す。

        `text` と `texture_update()` が揃っていないものは相手にしない。
        ゲームの外（オフライン検証）や、まだ組み上がっていない HUD がここへ来る。
        """
        label = frames.attr(hud, "text_display")
        if label in (None, frames.MISSING):
            return None
        text = frames.attr(label, "text")
        update = frames.attr(label, "texture_update")
        if not isinstance(text, str) or not callable(update):
            return None
        return label

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
        # 高さは次のフレームで1回だけ出し直す（本文は1文字ずつ伸び、ここはその回数だけ来る）。
        # 予約は HUD ごとに1つにまとめ、`texture_update()` は呼ばない（ローダの hud.settle_text。
        # 呼ぶと1文字ごとにラベルを作り直す。VERIFICATION_LOG.md §2.34）。
        hud_window.settle_text(hud)

    def repainted(call):
        # 先にゲームへ塗らせて、その後のラベルだけを載せ替える（窓口 hud の後の処理。TECH.md §3.3.21）。
        # 材料はゲームが持っている本文を優先する。
        # 通知の `value` は呼び出し元によっては渡らないので、控えの方が確か。
        canonical = frames.attr(call.obj, "display_text")
        if not isinstance(canonical, str):
            canonical = call.value
        if isinstance(canonical, str):
            limit_display(call.obj, canonical)

    hud_window.on(ctx.mod_name or "117_message_text_integrity", ctx, (hud_window.TEXT,),
                  after=repainted)

    ctx.log("message text integrity installed (DISPLAY_CHARS={})".format(
        DISPLAY_CHARS))
