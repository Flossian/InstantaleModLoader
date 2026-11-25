# -*- coding: utf-8 -*-
"""募集のウィンドウと契約の帯を描く（Kivy はゲームの中でだけ読む。関数の中で import する）。

中身（札と帯のマスの辞書）は `view.py` が組む。ここは並べて描くだけ。

- 募集のウィンドウ: ローダの `panels` と同じ黒地と枠。札を2列（横の狭い窓では1列）に並べ、
  札の中の契約のボタンを押すとウィンドウを閉じて `on_pick(札の番号, [回数, 長い契約か])` を呼ぶ
- 契約の帯: HUD の全面の層（選択肢のボタンと同じ親）の一番上に置く。押下は受け取らず、下のボタンへ素通しする。
  置く範囲は左の選択肢の列の右端から右の列の左端まで（ボタンには重ねない）
"""
import os

from instantale_modloader import panels, ui

from . import view as words

#: 色（`view.py` の色の鍵から引く）。
COLORS = {
    words.GOOD: (0.40, 0.73, 0.42, 1),
    words.BAD: (0.90, 0.49, 0.45, 1),
    words.DANGER: (0.90, 0.49, 0.45, 1),
    words.GOLD: (0.87, 0.75, 0.35, 1),
    words.ACCENT: (0.91, 0.63, 0.19, 1),
    words.PLAIN: panels.DIM,
}
NEXT_LINE = (0.22, 0.22, 0.24, 1)
CELL_FILL = (0.09, 0.09, 0.11, 1)
GREY_TINT = (0.45, 0.45, 0.45, 1)
#: 札を2列に並べる窓の横縦の比の下限（これより縦長の窓では1列。`350_` の 4:3 の扱いと同じ境目）。
TWO_COLUMNS_RATIO = 1.6
FACE = 132
STRIP_SIDE_GAP = 6
#: 帯の内側の余白と段の間（upx）。高さは字の大きさから決める（決め打ちの高さは中が空いた）。
STRIP_PAD = 10
STRIP_GAP = 6


def color_of(key, default=panels.TEXT):
    return COLORS.get(key, default)


def _exists(path):
    return isinstance(path, str) and path and os.path.isfile(path)


def _image(source, size, tint=None):
    from kivy.uix.image import Image
    try:
        picture = Image(source=source, fit_mode="contain")
    except Exception:
        picture = Image(source=source, allow_stretch=True, keep_ratio=True)
    picture.size_hint = (None, None)
    picture.size = (size, size)
    if tint is not None:
        picture.color = tint
    return picture


def _line(kit, text, scale, color=panels.TEXT, *, use_markup=False, halign="left"):
    """1行の字。欄の幅に収まらなければ後ろを「…」で詰める。"""
    from kivy.uix.label import Label
    label = kit._font(Label(text=text, color=color, markup=use_markup, halign=halign, valign="middle",
                            font_size=kit.base * scale, shorten=True, shorten_from="right", max_lines=1,
                            size_hint_y=None, height=kit.base * scale * 1.5))
    label.bind(size=lambda w, value: setattr(w, "text_size", value))
    return label


# ---------------------------------------------------------------- 募集のウィンドウ
class LaborWindow(object):
    def __init__(self, app, model, *, on_pick, on_dismiss=None, write=None):
        from kivy.core.window import Window
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.gridlayout import GridLayout
        from kivy.uix.modalview import ModalView
        from kivy.uix.widget import Widget

        self.on_pick = on_pick
        self.write = write
        self.kit = kit = panels.Kit(app)
        self.picked = False
        self.view = ModalView(size_hint=panels.SIZE_HINT, auto_dismiss=True)
        for name in ("background_color", "overlay_color"):
            try:
                setattr(self.view, name, (0, 0, 0, 0.5))
            except Exception:
                pass
        root = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(20), spacing=kit.px(10)),
                          fill=panels.BG)
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.TITLE * 1.5,
                         spacing=kit.px(14))
        title = kit.label(model.get("title") or "", panels.TITLE, wrap=False)
        title.pos_hint = {"center_y": 0.5}
        head.add_widget(title)
        if model.get("place"):
            place = kit.label(model["place"], panels.META, panels.DIM, wrap=False)
            place.pos_hint = {"center_y": 0.45}
            head.add_widget(place)
        head.add_widget(Widget())
        close = kit._font(Button(text="×", size_hint=(None, None), size=(kit.px(36), kit.px(36)),
                                 background_color=(0, 0, 0, 0), font_size=kit.base * panels.TITLE))
        close.bind(on_release=lambda *_a: self.view.dismiss())
        head.add_widget(close)
        root.add_widget(head)
        if model.get("subtitle"):
            pieces = []
            for label, value in model["subtitle"]:
                pieces += [(label + "　", panels.DIM), (value + "　　", panels.TEXT)]
            root.add_widget(kit.label(panels.markup(pieces), panels.META, use_markup=True))
        for text, key in model.get("notes") or []:
            root.add_widget(kit.label(text, panels.META, color_of(key, panels.DIM)))
        ratio = float(Window.width) / max(1.0, float(Window.height))
        columns = 2 if ratio >= TWO_COLUMNS_RATIO else 1
        grid = GridLayout(cols=columns, size_hint_y=None, spacing=kit.px(14))
        grid.bind(minimum_height=grid.setter("height"))
        cards = model.get("cards") or []
        # 札の高さは全部そろえる（短い札が行の中で上下の真ん中に浮かないように）
        height = max([self.card_height(card) for card in cards] or [0])
        for index, card in enumerate(cards):
            grid.add_widget(self.card(card, index, height))
        root.add_widget(kit.scroller(grid))
        self.view.add_widget(root)
        self.view.bind(on_dismiss=lambda *_a: panels.hide_tip())
        if on_dismiss is not None:
            self.view.bind(on_dismiss=lambda *_a: on_dismiss())
        self.view.open()
        manager = getattr(app, "sound_manager", None)
        play = getattr(manager, "play_sound_from_src", None)
        if callable(play):
            try:
                play(app, panels.OPEN_SOUND)
            except Exception:
                pass

    def dismiss(self):
        self.view.dismiss()

    def pick(self, index, payload):
        if self.picked:
            return
        self.picked = True
        self.view.dismiss()
        self.on_pick(index, payload)

    def card_height(self, card):
        kit = self.kit
        line = kit.base * panels.META * 1.5
        right = kit.base * panels.NAME * 1.5 + line * len(card["rows"])
        if card.get("tenure") is not None:
            right += line + kit.px(30)
        if card.get("buttons"):
            right += kit.px(46)
        elif card.get("note"):
            right += line
        right += kit.px(6) * (len(card["rows"]) + 3)
        left = kit.px(FACE) + kit.px(8) + kit.base * panels.TICK * 1.5
        return max(right, left) + kit.px(16) * 2

    def cornered(self, source, corner):
        """絵の右下に小さな札（危険の確率）を重ねた欄。"""
        from kivy.uix.label import Label
        from kivy.uix.relativelayout import RelativeLayout

        kit = self.kit
        size = kit.px(FACE)
        holder = RelativeLayout(size_hint=(None, None), size=(size, size))
        picture = _image(source, size)
        picture.pos = (0, 0)
        holder.add_widget(picture)
        text, key = corner
        color = color_of(key, panels.TEXT)
        mark = kit._font(Label(text=text, color=color, font_size=kit.base * panels.TICK, size_hint=(None, None),
                               height=kit.base * panels.TICK * 1.7))
        pad, gap = kit.px(10), kit.px(4)

        def place(widget, value):
            widget.width = value[0] + pad
            widget.pos = (size - widget.width - gap, gap)
        mark.bind(texture_size=place)
        holder.add_widget(kit.framed(mark, color=color, fill=panels.BG))
        return holder

    def card(self, card, index, height):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.label import Label
        from kivy.uix.widget import Widget

        kit = self.kit
        grey = bool(card.get("grey"))
        main = panels.FAINT if grey else panels.TEXT
        box = BoxLayout(orientation="horizontal", size_hint_y=None, height=height,
                        padding=kit.px(16), spacing=kit.px(18))
        kit.framed(box, color=panels.FAINT if grey else panels.LINE)

        left = BoxLayout(orientation="vertical", size_hint_x=None, width=kit.px(FACE), spacing=kit.px(8))
        if _exists(card.get("face")) and card.get("corner"):
            left.add_widget(self.cornered(card["face"], card["corner"]))
        elif _exists(card.get("face")):
            left.add_widget(_image(card["face"], kit.px(FACE), GREY_TINT if grey else None))
        else:
            holder = kit.framed(BoxLayout(orientation="vertical", size_hint=(None, None),
                                          size=(kit.px(FACE), kit.px(FACE)), padding=kit.px(10)),
                                color=panels.FAINT)
            top, bottom = card.get("placeholder") or ("", "")
            color = color_of(card["placeholder_tone"]) if card.get("placeholder_tone") else panels.DIM
            holder.add_widget(Widget())
            holder.add_widget(_line(kit, top, panels.NAME, color, halign="center"))
            holder.add_widget(_line(kit, bottom, panels.TICK, panels.FAINT, halign="center"))
            holder.add_widget(Widget())
            left.add_widget(holder)
        left.add_widget(_line(kit, card.get("per") or "", panels.TICK, panels.FAINT if grey else panels.DIM,
                              halign="center"))
        left.add_widget(Widget())
        box.add_widget(left)

        right = BoxLayout(orientation="vertical", spacing=kit.px(6))
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.NAME * 1.5,
                         spacing=kit.px(12))
        title = kit.label(card["title"], panels.NAME, main, wrap=False)
        title.pos_hint = {"center_y": 0.5}
        head.add_widget(title)
        if card.get("kind"):
            kind = kit.label(card["kind"], panels.TICK, panels.FAINT if grey else panels.DIM, wrap=False)
            kind.pos_hint = {"center_y": 0.45}
            head.add_widget(kind)
        head.add_widget(Widget())
        if card.get("badge"):
            text, key = card["badge"]
            color = color_of(key)
            mark = kit._font(Label(text=text, color=color, font_size=kit.base * panels.META, size_hint=(None, None),
                                   height=kit.base * panels.NAME * 1.3))
            mark.bind(texture_size=lambda w, value: setattr(w, "width", value[0] + kit.px(28)))
            mark.pos_hint = {"center_y": 0.5}
            head.add_widget(kit.framed(mark, color=color))
        right.add_widget(head)
        for label, value in card["rows"]:
            right.add_widget(_line(kit, panels.markup([(label + "　", panels.FAINT if grey else panels.DIM),
                                                       (value, main)]), panels.META, use_markup=True))
        tenure = card.get("tenure")
        if tenure is not None:
            top = len(words.rules.RANKS) - 1
            color = panels.FAINT if grey else color_of(words.GOLD if tenure["stage"] == top else words.GOOD)
            row = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.META * 1.5,
                            spacing=kit.px(12))
            row.add_widget(_line(kit, panels.markup([("勤続　", panels.FAINT if grey else panels.DIM),
                                                     (tenure["name"], color)]), panels.META, use_markup=True))
            if not grey:
                row.add_widget(_line(kit, tenure["next"], panels.TICK, panels.DIM, halign="right"))
            right.add_widget(row)
            names = [rank[0] for rank in words.rules.RANKS]
            right.add_widget(kit.gauge(names, tenure["stage"], color, lit=lambda i, s=tenure["stage"]: i < s))
        right.add_widget(Widget())
        if card.get("buttons"):
            row = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.px(46), spacing=kit.px(12))
            for label, amount, payload in card["buttons"]:
                text = panels.markup([(label + "　", panels.DIM), (amount, panels.TEXT)])
                button = kit._font(Button(text=text, markup=True, background_normal="", background_down="",
                                          background_color=(0, 0, 0, 0), font_size=kit.base * panels.META))
                button.bind(on_release=lambda *_a, i=index, p=payload: self.pick(i, p))
                row.add_widget(kit.framed(button))
            right.add_widget(row)
        elif card.get("note"):
            right.add_widget(_line(kit, card["note"], panels.META, panels.DIM))
        box.add_widget(right)
        return box


# ---------------------------------------------------------------- 契約の帯
_PASSTHROUGH = {}


def _passthrough_cls():
    """押下を受け取らない入れ物（下の選択肢のボタンへ素通しする）。"""
    cls = _PASSTHROUGH.get("cls")
    if cls is None:
        from kivy.uix.floatlayout import FloatLayout

        class LaborStrip(FloatLayout):
            def on_touch_down(self, touch):
                return False

            def on_touch_move(self, touch):
                return False

            def on_touch_up(self, touch):
                return False

        cls = _PASSTHROUGH["cls"] = LaborStrip
    return cls


def layer_of(app):
    """帯を置く層（選択肢のボタンの親）と、置く範囲 `(左, 右, 上端)`。引けなければ None。

    左右は左の選択肢の列の右端から右の列の左端まで、上端は左の1つ目のボタンの上端（窓の大きさに関わらず
    背景の欄の内側に収まる）。
    """
    hud = ui.find_hud(app)
    buttons = getattr(hud, "buttons", None) if hud is not None else None
    first = buttons[0] if isinstance(buttons, (list, tuple)) and buttons else None
    layer = getattr(first, "parent", None)
    if first is None or layer is None:
        return None
    left = float(first.right)
    right_layout = getattr(hud, "right_button_layout", None)
    right = float(right_layout.x) if right_layout is not None else float(layer.width) - left
    if right - left < ui.upx(200):
        right = float(layer.width) - left
    return layer, left, right, float(first.top)


class Strip(object):
    """契約の帯を1枚だけ持つ。出す・描き直す・片付けるはメインスレッドから呼ぶ。"""

    def __init__(self, store, key="strip", write=None):
        self.store = store
        self.key = key
        self.write = write

    def current(self):
        return self.store.get(self.key)

    def hide(self):
        widget = self.store.get(self.key)
        self.store[self.key] = None
        if widget is not None and widget.parent is not None:
            widget.parent.remove_widget(widget)
        return widget is not None

    def show(self, app, model):
        found = layer_of(app)
        if found is None:
            if self.write:
                self.write("WARN strip: the choice layer was not found; no strip")
            return False
        layer, left, right, top = found
        self.hide()
        widget = self.build(app, model, left, right, top)
        layer.add_widget(widget)            # 一番上に描く（押下は素通し）
        self.store[self.key] = widget
        return True


    def build(self, app, model, left, right, top):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget

        kit = panels.Kit(app)
        px = kit.px
        gap = px(STRIP_SIDE_GAP)
        width = max(px(300), right - left - gap * 2)
        banner = bool(model.get("banner"))
        # 立ち絵を出している間は見出しだけの低い帯にする（マスの段まで出すと立ち絵の顔に重なる）
        compact = bool(model.get("compact"))
        head_h = kit.base * (panels.BODY + panels.TICK) * 1.5
        cell_h = self.cell_height(kit)
        height = head_h + px(STRIP_PAD) * 2
        if not compact:
            height += px(STRIP_GAP) + cell_h
        if banner:
            height += kit.base * panels.META * 1.5 + px(STRIP_GAP)
        holder = _passthrough_cls()(size_hint=(None, None), size=(width, height))
        holder.pos = (left + gap, top - height)
        box = kit.framed(BoxLayout(orientation="vertical", size_hint=(None, None), size=(width, height),
                                   pos=holder.pos, padding=px(STRIP_PAD), spacing=px(STRIP_GAP)), fill=panels.BG)
        holder.add_widget(box)

        # 見出し: [顔] 題 添え / 主人・勤続 …… 稼ぎ / 残り
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=head_h, spacing=px(10))
        if _exists(model.get("face")):
            face = _image(model["face"], head_h)
            face.pos_hint = {"center_y": 0.5}
            head.add_widget(kit.framed(face))
        middle = BoxLayout(orientation="vertical")
        sub = "[size={}]{}[/size]".format(int(kit.base * panels.TICK), panels.markup(
            [("　" + (model.get("sub") or ""), panels.DIM)]))
        middle.add_widget(_line(kit, panels.markup([(model.get("title") or "", panels.TEXT)]) + sub,
                                panels.BODY, use_markup=True))
        middle.add_widget(_line(kit, model.get("line") or "", panels.TICK, panels.DIM))
        head.add_widget(middle)
        side = BoxLayout(orientation="vertical", size_hint_x=0.62)
        side.add_widget(_line(kit, model.get("right") or "", panels.BODY, panels.TEXT, halign="right"))
        side.add_widget(_line(kit, model.get("right2") or "", panels.TICK, panels.DIM, halign="right"))
        head.add_widget(side)
        box.add_widget(head)
        if banner:
            box.add_widget(_line(kit, model["banner"], panels.META, color_of(words.GOOD)))
        if compact:
            return holder

        cells = BoxLayout(orientation="horizontal", size_hint_y=None, height=cell_h, spacing=px(6))
        for cell in model.get("cells") or []:
            cells.add_widget(self.cell(kit, cell))
        if not model.get("cells"):
            cells.add_widget(Widget())
        box.add_widget(cells)
        return holder

    @staticmethod
    def cell_height(kit):
        """マスの高さ: 上の段（何期目と額）と下の段（印）に、枠の内側の余白。"""
        return kit.base * panels.BODY * 1.4 + kit.base * panels.TICK * 1.7 + kit.px(12)

    def cell(self, kit, cell):
        """マス1つ。上の段に何期目（左）と額（右）、下の段に結果の印か添え書き。"""
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.widget import Widget

        px = kit.px
        state = cell.get("state")
        line = {"done": panels.FAINT, "now": color_of(words.GOOD), "danger": color_of(words.DANGER)}.get(
            state, NEXT_LINE)
        fill = CELL_FILL if state in ("done", "now", "danger") else panels.BG
        box = kit.framed(BoxLayout(orientation="vertical", padding=(px(8), px(3))),
                         color=line, width=3 if state in ("now", "danger") else 2, fill=fill)
        top = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.BODY * 1.4,
                        spacing=px(4))
        head = panels.TEXT if state in ("now", "danger") else (panels.DIM if state == "done" else panels.FAINT)
        number = _line(kit, cell.get("n") or "", panels.TICK, head)
        number.size_hint_x = 0.38
        top.add_widget(number)
        pay_color = color_of(cell["pay_tone"]) if cell.get("pay_tone") else (
            panels.FAINT if state == "next" else panels.TEXT)
        top.add_widget(_line(kit, cell.get("pay") or "", panels.BODY, pay_color, halign="right"))
        box.add_widget(top)
        # 下の段は印が無くても同じ高さの入れ物にする（無いと上の段の高さがマスごとにずれる）
        bottom = BoxLayout(orientation="vertical", size_hint_y=None, height=kit.base * panels.TICK * 1.7)
        tag = cell.get("tag")
        note = cell.get("note")
        if tag:
            color = color_of(cell.get("tone"), panels.DIM)
            mark = kit._font(Label(text=tag, color=color, font_size=kit.base * panels.TICK, size_hint=(None, None),
                                   height=kit.base * panels.TICK * 1.5, shorten=True, max_lines=1))
            mark.bind(texture_size=lambda w, value: setattr(w, "width", value[0] + px(12)))
            mark.pos_hint = {"center_x": 0.5}
            bottom.add_widget(kit.framed(mark, color=color))
        elif note:
            bottom.add_widget(_line(kit, note, panels.TICK, color_of(cell.get("tone"), panels.DIM), halign="center"))
        box.add_widget(bottom)
        box.add_widget(Widget())        # 余った高さは下へ（上の段を上に寄せる）
        return box
