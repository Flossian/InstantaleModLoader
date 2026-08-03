# -*- coding: utf-8 -*-
"""切り替えボタンそのもの（作る・置く・絵柄を描く）だけを知っている部品。

いつ広げるか・どこへ置くと決めたか（設定）は入口 `ui_party_expand.py` にある。
ここが知っているのは Kivy の書き方だけで、パーティ欄のことは知らない。

見た目は**背景なしの白いアイコン**で、**画像ファイルは持たない** ― Kivy の
canvas に線で描く。0〜1 の座標で形を持つので、どの解像度でも滲まず、色も透過も
こちらで決められる（配布物にバイナリが増えないのも利点）。`113_ui_text_expand`
と同じ作りだが、あちらの部品を借りずにこちらで持つ ― MOD 単体の部品は MOD の
フォルダの中で完結させる決まり（TECH.md §3.1.1.1）。
"""

import sys

from instantale_modloader import frames

# 絵柄に「文字」を選んだときの呼び名（アイコンではなく文字ボタンになる）。
AS_TEXT = "文字"

# 隅と `pos_hint` の対応。縁からわずかに内側へ入れる。
CORNERS = {
    "右上": {"right": 0.995, "top": 0.995},
    "左上": {"x": 0.005, "top": 0.995},
    "右下": {"right": 0.995, "y": 0.005},
    "左下": {"x": 0.005, "y": 0.005},
}

# 描いた絵柄を覚えておく先（同じなら描き直さない）。
ICON_ATTR = "_instantale_party_icon"


def upx(value):
    """ゲームの拡縮（`scripts.hud.new_hud:upx`）に合わせる。無ければ素の値。"""
    module = sys.modules.get("scripts.hud.new_hud")
    scale = getattr(module, "upx", None) if module is not None else None
    if callable(scale):
        try:
            return float(scale(value))
        except Exception:
            pass
    return float(value)


def window_size():
    try:
        from kivy.core.window import Window
        return float(Window.width), float(Window.height)
    except Exception:
        return 0.0, 0.0


def clamp(widget):
    """窓の内側へ寄せる。はみ出したボタンは押せないので、置いた後に必ず通す。"""
    width, height = window_size()
    if not width or not height:
        return
    if widget.y + widget.height > height:
        widget.y = height - widget.height
    if widget.y < 0:
        widget.y = 0
    if widget.x + widget.width > width:
        widget.x = width - widget.width
    if widget.x < 0:
        widget.x = 0


def make(font_name, text, size, icon, pos_hint):
    """ボタンを1枚作る。作れないビルド（Kivy が引けない）では None。"""
    try:
        from kivy.uix.button import Button
    except Exception:
        return None
    height = upx(size)
    # 文字は横長、アイコンは正方形。
    width = height if icon != AS_TEXT else height * 2.0
    button = Button(text=text, size_hint=(None, None), size=(width, height),
                    pos_hint=dict(pos_hint or {}))
    # 日本語を出すのでフォントは本文から写す。Kivy の既定（Roboto）には日本語が
    # 無く、写さないとボタンの文字が豆腐になる。
    if isinstance(font_name, str) and font_name:
        button.font_name = font_name
    button.font_size = height * 0.45
    if icon != AS_TEXT:
        # 背景を消す。`background_normal` を空にしないと、色を透明にしても
        # 既定のテクスチャがうっすら残る。
        for name, value in (("background_normal", ""), ("background_down", ""),
                            ("background_disabled_normal", ""),
                            ("background_color", (0, 0, 0, 0)),
                            ("border", (0, 0, 0, 0))):
            try:
                setattr(button, name, value)
            except Exception:
                pass      # その属性を持たないビルドでも描画は成り立つ
    return button


def strokes(icon, expanded):
    """アイコンの線。**0〜1 の座標**で返す（ボタンの大きさに依らない形）。

    `expanded` は「今広がっている」＝押すと**戻る**状態。上下を反転して
    「次に何が起きるか」をそのまま形にする。
    """
    flip = (lambda y: 1.0 - y) if expanded else (lambda y: y)

    def line(*points):
        return [(x, flip(y)) for x, y in points]

    if icon == "二重山形":
        return [line((0.22, 0.40), (0.50, 0.66), (0.78, 0.40)),
                line((0.22, 0.20), (0.50, 0.46), (0.78, 0.20))]
    if icon == "山形":
        return [line((0.20, 0.34), (0.50, 0.66), (0.80, 0.34))]
    if icon == "矢印":
        return [line((0.50, 0.18), (0.50, 0.82)),
                line((0.28, 0.60), (0.50, 0.82), (0.72, 0.60))]
    if icon == "人":
        # 頭と肩。広げる前は上に、戻すときは下に向く（増える側を指す）。
        return [line((0.50, 0.62), (0.50, 0.62)),
                line((0.36, 0.34), (0.42, 0.50), (0.58, 0.50), (0.64, 0.34)),
                line((0.42, 0.66), (0.50, 0.74), (0.58, 0.66), (0.50, 0.58),
                     (0.42, 0.66))]
    if icon == "枠":
        # 四隅のかぎ括弧。広げる前は外を向き、広がっているときは内を向く。
        if expanded:
            return [line((0.20, 0.42), (0.42, 0.42), (0.42, 0.20)),
                    line((0.80, 0.42), (0.58, 0.42), (0.58, 0.20)),
                    line((0.20, 0.58), (0.42, 0.58), (0.42, 0.80)),
                    line((0.80, 0.58), (0.58, 0.58), (0.58, 0.80))]
        return [line((0.20, 0.42), (0.20, 0.20), (0.42, 0.20)),
                line((0.80, 0.42), (0.80, 0.20), (0.58, 0.20)),
                line((0.20, 0.58), (0.20, 0.80), (0.42, 0.80)),
                line((0.80, 0.58), (0.80, 0.80), (0.58, 0.80))]
    return []


def paint(button, icon, expanded, width, alpha, log_exc):
    """絵柄を描き直す。**位置・大きさ・向き・絵柄が変わったときだけ**。

    パーティ欄の塗り直しは相手の HP が動くたびに来る。毎回引き直すと無駄が
    積み上がるので、同じ絵なら何もしない。
    """
    if icon == AS_TEXT:
        return
    signature = (icon, bool(expanded), width, alpha,
                 tuple(frames.attr(button, "pos", ()) or ()),
                 tuple(frames.attr(button, "size", ()) or ()))
    if frames.attr(button, ICON_ATTR, None) == signature:
        return
    try:
        from kivy.graphics import Color, Line
    except Exception:
        return            # 線が引けない環境（オフライン検証）では文字のまま
    try:
        group = button.canvas.after
        group.clear()
        x, y = float(button.x), float(button.y)
        box_width, box_height = float(button.width), float(button.height)
        group.add(Color(1, 1, 1, float(alpha)))
        for points in strokes(icon, expanded):
            flat = []
            for fx, fy in points:
                flat.extend((x + fx * box_width, y + fy * box_height))
            group.add(Line(points=flat, width=upx(width), cap="round", joint="round"))
        setattr(button, ICON_ATTR, signature)
    except Exception:
        log_exc("party expand: could not draw the icon")


def show(button, visible):
    """見せる／隠す。隠すときは**押せなくもする**（見えない当たり判定を残さない）。"""
    try:
        button.opacity = 1.0 if visible else 0.0
        button.disabled = not visible
    except Exception:
        pass
