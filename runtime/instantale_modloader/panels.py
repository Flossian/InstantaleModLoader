# -*- coding: utf-8 -*-
"""MOD の窓（画面左下の一覧から開く状況の窓など）を、ゲームの画面と同じ作りで描く部品。TECH.md §3.3.17。

    window = panels.open_window(app, "勢力と情勢", build, subtitle="現在地　…", on_dismiss=…)
    #   build(kit) が本文のウィジェットを返す。kit が下の部品を持つ

見た目はゲームの画面に揃える: 黒地を細い白の枠で区切り、字はゲームの本文の字（HUD の `status_label` の
字体と大きさ）。字の大きさは4段（見出し・名前・本文・添え書き）、段階の色と目盛りで分ける。
`338_` と `339_` の窓が同じ部品を使うので、片方を直せばもう片方も揃う。

Kivy はゲームの中でだけ読む（関数の中で import する）。
"""
from . import frames, ui

BG = (0.06, 0.06, 0.08, 1)                # 不透明（0.96 では下の白い字が 4% 透けて読めた。2026-10-13 の実機）
#: 枠の色と太さ。ゲームの画面の枠（能力値の欄・本文の欄）は 2px の灰白（測ると 199,199,199）。
#: Kivy の `Line` は太さの指定と画面の px がずれた（1.2 で 1px、1.5 で 3px。2026-10-13 の実機）ので、
#: 枠は4辺を塗りの四角で引く。
LINE = (0.78, 0.78, 0.78, 1)
BORDER = 2
TEXT = (0.92, 0.92, 0.92, 1)
DIM = (0.55, 0.55, 0.55, 1)
FAINT = (0.37, 0.37, 0.37, 1)
QUOTE = (0.43, 0.43, 0.43, 1)
#: 字の大きさ（ゲームの本文の大きさに掛ける）。
TITLE, NAME, BODY, META, TICK = 1.5, 1.25, 1.0, 0.85, 0.75
SIZE_HINT = (0.8, 0.86)
OPEN_SOUND = "Assets/sounds/sounds/ui/常時/アイコン_キャラシート.wav"


def hex_of(color):
    return "".join("{:02x}".format(int(round(v * 255))) for v in color[:3])


def markup(pieces, colors=None):
    """`[(文, 色の鍵 or 色 or None)]` を色つきの markup にする。鍵は `colors` で引く。"""
    from kivy.utils import escape_markup
    out = []
    for text, key in pieces:
        text = escape_markup(text)
        color = (colors or {}).get(key, key) if key is not None else None
        out.append(text if color is None else "[color={}]{}[/color]".format(hex_of(color), text))
    return "".join(out)


def border(widget, color=LINE, width=BORDER):
    """`widget` の内側の縁に、太さ `width` px の枠を4辺の塗りの四角で引く（中身の上に描く）。"""
    from kivy.graphics import Color, Rectangle
    with widget.canvas.after:
        Color(*color)
        sides = [Rectangle() for _ in range(4)]

    def redraw(*_args):
        t = max(1, int(round(ui.upx(width))))
        x, y, w, h = widget.x, widget.y, widget.width, widget.height
        for side, (pos, size) in zip(sides, (((x, y), (w, t)), ((x, y + h - t), (w, t)),
                                              ((x, y), (t, h)), ((x + w - t, y), (t, h)))):
            side.pos, side.size = pos, size

    widget.bind(pos=redraw, size=redraw)
    redraw()
    return widget


class Kit:
    """窓の部品。字体と字の大きさは、作ったときの HUD から読む。"""

    def __init__(self, app):
        hud = ui.find_hud(app)
        status = frames.attr(hud, "status_label", None) if hud is not None else None
        name = frames.text_of(status, "font_name") if status is not None else None
        size = frames.attr(status, "font_size", None) if status is not None else None
        self.font_name = name if isinstance(name, str) and name else None
        self.base = float(size) if isinstance(size, (int, float)) else ui.upx(16)
        self.px = ui.upx

    def _font(self, widget):
        if self.font_name:
            widget.font_name = self.font_name
        return widget

    def label(self, text, scale=BODY, color=TEXT, *, wrap=True, use_markup=False, halign="left"):
        """字。`wrap` なら幅に合わせて折り返し、高さは中身に合わせる。そうでなければ中身の大きさ。"""
        from kivy.uix.label import Label
        widget = self._font(Label(text=text, color=color, halign=halign, valign="top", markup=use_markup,
                                  font_size=self.base * scale))
        if wrap:
            widget.size_hint_y = None
            widget.bind(width=lambda w, value: setattr(w, "text_size", (value, None)),
                        texture_size=lambda w, value: setattr(w, "height", value[1]))
        else:
            widget.size_hint = (None, None)
            widget.bind(texture_size=lambda w, value: setattr(w, "size", value))
        return widget

    def framed(self, widget, color=LINE, width=BORDER, fill=None):
        """枠（と塗り）を付ける。枠の太さは px（`border` と同じ）。"""
        if fill is not None:
            from kivy.graphics import Color, Rectangle
            with widget.canvas.before:
                Color(*fill)
                rect = Rectangle(pos=widget.pos, size=widget.size)
            widget.bind(pos=lambda w, v: setattr(rect, "pos", v), size=lambda w, v: setattr(rect, "size", v))
        return border(widget, color, width)

    def column(self, spacing=10):
        """縦に積む入れ物（高さは中身に合わせる。スクロールの中に入れる）。"""
        from kivy.uix.gridlayout import GridLayout
        grid = GridLayout(cols=1, size_hint_y=None, spacing=self.px(spacing))
        grid.bind(minimum_height=grid.setter("height"))
        return grid

    def stack(self, spacing=4, padding=0):
        from kivy.uix.boxlayout import BoxLayout
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=self.px(spacing),
                        padding=self.px(padding))
        box.bind(minimum_height=box.setter("height"))
        return box

    def scroller(self, content):
        from kivy.uix.scrollview import ScrollView
        view = ScrollView(do_scroll_x=False, bar_width=self.px(6), scroll_type=["bars", "content"])
        view.add_widget(content)
        return view

    def card(self, spacing=6, padding=14):
        """枠で囲んだ札（中身を縦に積む）。"""
        return self.framed(self.stack(spacing=spacing, padding=padding))

    def gauge(self, names, current, color, *, lit, colors=None):
        """目盛り。`names` は段の名前の並び、`current` は今の段の位置、`lit(i)` は薄く塗る段か。

        `colors` を渡すと段ごとにその色で塗る（途中で良い段から悪い段へ転じる出来事など）。
        """
        from kivy.graphics import Color, Line, Rectangle
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=self.px(30), spacing=self.px(6))
        for index, name in enumerate(names):
            if colors is not None and index < len(colors):
                color = colors[index]
            cell = BoxLayout(orientation="vertical", spacing=self.px(3))
            bar = Widget(size_hint_y=None, height=self.px(9))
            on = index == current
            dim = not on and lit(index)
            with bar.canvas:
                if on:
                    Color(*color)
                elif dim:
                    Color(color[0] * 0.45, color[1] * 0.45, color[2] * 0.45, 1)
                else:
                    Color(0.27, 0.27, 0.27, 1)
                shape = Rectangle(pos=bar.pos, size=bar.size) if (on or dim) else \
                    Line(rectangle=(bar.x, bar.y, bar.width, bar.height), width=1)

            def follow(w, _value, shape=shape):
                if isinstance(shape, Rectangle):
                    shape.pos, shape.size = w.pos, w.size
                else:
                    shape.rectangle = (w.x, w.y, w.width, w.height)

            bar.bind(pos=follow, size=follow)
            cell.add_widget(bar)
            tick = self.label(name, TICK, color if on else FAINT, halign="center")
            cell.add_widget(tick)
            row.add_widget(cell)
        return row

    def head(self, title, *, sub="", sub_scale=BODY, badge=None, caption=""):
        """札の上段。名前・添え（小さく）・右に見出しつきの段階の札。`badge` は (文字, 色)。"""
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.widget import Widget
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=self.base * NAME * 1.6,
                        spacing=self.px(12))
        parts = [self.label(title, NAME, wrap=False)]
        if sub:
            parts.append(self.label(sub, sub_scale, TEXT if sub_scale >= BODY else DIM, wrap=False))
        for widget in parts:
            widget.pos_hint = {"center_y": 0.5}
            row.add_widget(widget)
        row.add_widget(Widget())
        if badge is not None:
            if caption:
                note = self.label(caption, TICK, DIM, wrap=False)
                note.pos_hint = {"center_y": 0.5}
                row.add_widget(note)
            text, color = badge
            mark = self._font(Label(text=text, color=color, font_size=self.base, size_hint=(None, 1),
                                    width=self.px(92)))
            row.add_widget(self.framed(mark, color=color))
        return row

    def barred(self, content, color, width=3):
        """左に縦の帯を引いた行（噂の引用・注意の行）。`content` は高さが中身に合う字。"""
        from kivy.graphics import Color, Rectangle
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        line = BoxLayout(orientation="horizontal", size_hint_y=None, spacing=self.px(10))
        bar = Widget(size_hint_x=None, width=self.px(width))
        with bar.canvas:
            Color(*color)
            mark = Rectangle(pos=bar.pos, size=bar.size)
        bar.bind(pos=lambda w, v: setattr(mark, "pos", v), size=lambda w, v: setattr(mark, "size", v))
        content.bind(height=lambda w, value: setattr(line, "height", value))
        line.add_widget(bar)
        line.add_widget(content)
        return line

    def dated(self, when, content):
        """左に日付（一定の幅）、右に中身の行。日付は中身の1行目に揃える。"""
        from kivy.uix.boxlayout import BoxLayout
        row = BoxLayout(orientation="horizontal", size_hint_y=None, spacing=self.px(12))
        stamp = self.label(when, META, DIM)
        stamp.size_hint_x, stamp.width = None, self.base * META * 4.3    # 「999日前」が収まる幅
        stamp.pos_hint = {"top": 1}
        content.bind(height=lambda w, value: setattr(row, "height", value))
        row.add_widget(stamp)
        row.add_widget(content)
        return row

    def section(self, title, items, empty):
        """見出しつきの枠。中身はスクロールする。"""
        from kivy.uix.boxlayout import BoxLayout
        box = self.framed(BoxLayout(orientation="vertical", padding=self.px(14), spacing=self.px(8)))
        box.add_widget(self.label(title, NAME))
        content = self.column()
        for widget in items or [self.label(empty, META, DIM)]:
            content.add_widget(widget)
        box.add_widget(self.scroller(content))
        return box


def open_window(app, title, build, *, subtitle="", on_dismiss=None, size_hint=SIZE_HINT, sound=True,
                log_exc=None):
    """見出し・閉じるボタン・本文の窓を開いて返す。本文は `build(kit)` が返すウィジェット。"""
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.modalview import ModalView
    from kivy.uix.widget import Widget

    kit = Kit(app)
    view = ModalView(size_hint=size_hint, auto_dismiss=True)
    for name in ("background_color", "overlay_color"):
        try:
            setattr(view, name, (0, 0, 0, 0.5))
        except Exception:
            pass
    root = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(20), spacing=kit.px(10)), fill=BG)
    head = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * TITLE * 1.5)
    head.add_widget(kit.label(title, TITLE, wrap=False))
    head.add_widget(Widget())
    close = kit._font(Button(text="×", size_hint=(None, None), size=(kit.px(36), kit.px(36)),
                             background_color=(0, 0, 0, 0), font_size=kit.base * TITLE))
    close.bind(on_release=lambda *_a: view.dismiss())
    head.add_widget(close)
    root.add_widget(head)
    if subtitle:
        root.add_widget(kit.label(subtitle, META, DIM))
    root.add_widget(build(kit))
    view.add_widget(root)
    if on_dismiss is not None:
        view.bind(on_dismiss=lambda *_a: on_dismiss())
    view.open()
    if sound:
        manager = getattr(app, "sound_manager", None)
        play = getattr(manager, "play_sound_from_src", None)
        if callable(play):
            try:
                play(app, OPEN_SOUND)
            except Exception:
                if log_exc is not None:
                    log_exc("panels: cannot play the open sound")
    return view
