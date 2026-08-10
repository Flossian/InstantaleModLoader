# -*- coding: utf-8 -*-
"""切り替えボタンそのもの（作る・置く・絵柄を描く）だけを知っている部品。

いつ広げるか・どこへ置くと決めたか（設定）は入口 `ui_party_expand.py` にある。
ここが知っているのは Kivy の書き方だけで、パーティ欄のことは知らない。

**作り方はローダに移した**（`instantale_modloader.ui`）。同じものが `113_` /
`122_` にも1字違わず写されていて、実際にずれ始めていた ― こちらの `clamp` は
例外を握っておらず、他の2本は握っていた。写して回るものはローダの語彙
（TECH.md §3.2.3）。ここに残っているのは**この MOD だけの絵柄**（「人」）と、
呼び方を今までどおりに保つための薄い包みだけ。
"""

from instantale_modloader import ui

# 絵柄に「文字」を選んだときの呼び名（アイコンではなく文字ボタンになる）。
AS_TEXT = ui.AS_TEXT

# 隅と `pos_hint` の対応。
CORNERS = ui.CORNERS

# 描いた絵柄を覚えておく先（同じなら描き直さない）。`ui.MOD_WIDGET_PREFIX` で
# 始めること ― `overlay_host` が「他の MOD が足したもの」の見分けに使う。
ICON_ATTR = ui.MOD_WIDGET_PREFIX + "party_icon"

upx = ui.upx
window_size = ui.window_size
clamp = ui.clamp_into_window
show = ui.show_widget


def make(font_name, text, size, icon, pos_hint):
    """ボタンを1枚作る。作れないビルド（Kivy が引けない）では None。"""
    return ui.make_icon_button(text=text, size=size, square=(icon != AS_TEXT),
                               font_name=font_name, pos_hint=pos_hint)


def strokes(icon, expanded):
    """アイコンの線。**0〜1 の座標**で返す（ボタンの大きさに依らない形）。

    `expanded` は「今広がっている」＝押すと**戻る**状態。上下を反転して
    「次に何が起きるか」をそのまま形にする。共有の絵柄はローダから引き、
    **この MOD だけの「人」**だけをここに持つ。
    """
    if icon == "人":
        flip = (lambda y: 1.0 - y) if expanded else (lambda y: y)

        def line(*points):
            return [(x, flip(y)) for x, y in points]

        # 頭と肩。広げる前は上に、戻すときは下に向く（増える側を指す）。
        return [line((0.50, 0.62), (0.50, 0.62)),
                line((0.36, 0.34), (0.42, 0.50), (0.58, 0.50), (0.64, 0.34)),
                line((0.42, 0.66), (0.50, 0.74), (0.58, 0.66), (0.50, 0.58),
                     (0.42, 0.66))]
    return ui.icon_strokes(icon, expanded)


def paint(button, icon, expanded, width, alpha, log_exc):
    """絵柄を描き直す。**位置・大きさ・向き・絵柄が変わったときだけ**。

    パーティ欄の塗り直しは相手の HP が動くたびに来る。毎回引き直すと無駄が
    積み上がるので、同じ絵なら何もしない。
    """
    if icon == AS_TEXT:
        return
    ui.paint_icon(button, strokes(icon, expanded), attr=ICON_ATTR,
                  key=(icon, bool(expanded)), width=width, alpha=alpha,
                  log_exc=lambda msg: log_exc("party expand: " + msg))
