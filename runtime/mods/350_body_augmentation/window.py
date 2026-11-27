# -*- coding: utf-8 -*-
"""人体改造の画面を描く（Kivy）。中身は `body_augmentation.build_model` が組んだものをそのまま描く。

部品と色はローダの `panels`（TECH.md §3.3.17）に揃える。並びは次のとおり。

    見出し（題・主人公と数・所持金と現在地・閉じる）
    左の列: 頭・腕・脚の札 ／ 中央: 人体の図 ／ 右の列: 精神・胴・内臓の札（札は枠を横に2つ）
    下の段: 持ち物の候補 ／ 選んだものの詳細（施術・取り外しのボタン） ／ 合計の効果

見た目の種類（機械・魔術・中立）で、強調の色と図の背景（方眼・魔法陣・無地）だけを替える。
Kivy はゲームの中でだけ読む（関数の中で import する）。
"""
import math

from instantale_modloader import panels, ui

from . import rules

#: 見た目の種類ごとの色。`accent` は改造のある部位と強調、`fill` は図の塗り、`illegal` は違法、`hurt` は怪我。
#: 違法の改造の名前は `illegal` の色。禁制のアイテムは、さらに絵に `illegal` の色の枠を重ねる。
PALETTES = {
    rules.MACHINE: {"accent": (0.43, 0.78, 0.86, 1), "fill": (0.13, 0.29, 0.33, 1),
                    "illegal": (0.88, 0.33, 0.29, 1), "hurt": (0.88, 0.67, 0.24, 1),
                    "hurt_fill": (0.27, 0.21, 0.09, 1), "back": (0.16, 0.24, 0.26, 1)},
    rules.MAGIC: {"accent": (0.86, 0.73, 0.45, 1), "fill": (0.31, 0.24, 0.12, 1),
                  "illegal": (0.71, 0.47, 0.92, 1), "hurt": (0.90, 0.39, 0.29, 1),
                  "hurt_fill": (0.30, 0.12, 0.09, 1), "back": (0.36, 0.31, 0.20, 1)},
    rules.NEUTRAL: {"accent": (0.85, 0.85, 0.85, 1), "fill": (0.38, 0.38, 0.42, 1),
                    "illegal": (0.88, 0.33, 0.29, 1), "hurt": (0.88, 0.67, 0.24, 1),
                    "hurt_fill": (0.27, 0.21, 0.09, 1), "back": (0.20, 0.20, 0.22, 1)},
}
PLUS = (0.59, 0.84, 0.59, 1)
MINUS = (0.90, 0.49, 0.45, 1)
PLAIN_FILL = (0.12, 0.12, 0.14, 1)
PLAIN_LINE = (0.47, 0.47, 0.49, 1)
SELECTED = (0.17, 0.17, 0.20, 1)
#: 画面の大きさ（ゲームの窓に対する割合）。横に3列と下に3つの欄を並べるので、ほかの MOD の画面
#: （`panels.SIZE_HINT` の 0.8×0.86）より横に広げる。4:3 の窓では横幅が 16:9 の 75% しかない。
WINDOW_SIZE_HINT = (0.94, 0.86)
#: ボタンの中の小さい字の1行の高さ（字の大きさに対する倍率。字体の行の高さに合わせる）。
BUTTON_LINE = 1.3
#: 合計の効果の欄の字の大きさ（能力値の行・戦闘の行）。欄が広いので詳細の欄より一回り大きく描く。
TOTAL_STAT, TOTAL_BATTLE = 1.2, 1.0
#: 詳細の欄のアイテムの名前の字の大きさ。札の名前（`panels.NAME`）より大きく、画面の題（`panels.TITLE`）より小さい。
DETAIL_NAME = 1.4
#: 部位の札の効果の行数の上限（超える分は「…」。札の枠の高さに収める）。
SLOT_LINES = 2
#: 部位の札の字の欄がこれより狭いと（px。4:3 の窓）、効果を外して名前だけを2行まで出す。
NARROW_TEXT = 100
#: アイテムの絵の大きさ（画面の px）。ゲームの所持品の 1×1 のマス（64px。GAME.md §2.13.3）に揃える。
#: 所持品のマスはゲームの拡縮を受けない（1920×1080 でも 64px）ので、この値も `kit.px` に通さない。
ITEM_ICON = 64
#: 横幅の狭い窓（横縦の比が `NARROW_ASPECT` 未満。4:3 など）の絵の大きさ。4:3 の横幅は 16:9 の 75% なので、64 の 3/4。
ITEM_ICON_NARROW, NARROW_ASPECT = 48, 1.6
#: 部位の札の上下の余白・札どうしのすき間・改造の枠の上下の余白（px）。札の高さはこれと絵の大きさから決まる。
CARD_PAD, CARD_GAP, SLOT_PAD = 6, 10, 2
#: 改造の枠の左右の内側の余白と、絵と字の間・2つの枠の間（px）。字の欄を広く取るため小さめにする。
SLOT_SIDE, SLOT_GAP = 2, 6
#: 持ち物の候補の札の、絵の左右に取る余白の合計（px。列の数は欄の幅を「絵＋余白」で割って決める）。
CANDIDATE_MARGIN = 8
#: 部位の札の見出しの帯と、詳しい欄の項目名の枠の塗り。
BAND_FILL = (0.12, 0.12, 0.14, 1)
CHIP_FILL = (0.16, 0.16, 0.18, 1)
#: 項目名の枠の字の両脇の余白の合計・枠と中身の間・代償の行の字下げ・部位の見出しの帯の左の余白（upx）。
CHIP_PAD, CHIP_GAP, SUB_INDENT, BAND_PAD = 16, 10, 18, 6
#: 人体の図の下の凡例の段の高さ（字の大きさに対する倍率）。字の下端を左右の札の下端に揃える高さ。
LEGEND_ROW = 1.2
#: 見出しの項目の区切り。
HEAD_SEP = "　｜　"
#: 詳しい欄の方法ごとの塊の間の線と、スクロールする欄とボタンの段の間の線の色。
BLOCK_LINE = (0.24, 0.24, 0.26, 1)
#: 括弧の中の説明の字の大きさ（行の字に対する倍率）。「再生（毎ターン…）」の括弧の中を小さくして1行に収める。
PAREN_SCALE = 0.8

#: 人体の図の形（幅 320・高さ 500 の箱。y は下向き。x は中心からのずれ。左右対称のものは右半分だけ）。
FIG_W, FIG_H, FIG_TOP = 320.0, 500.0, 18.0
#: 主人公の性別によらない体つきにするため、肩幅を腰と同じにし、腕を細くして体に寄せている。
ARM = [(48, 94), (66, 103), (84, 204), (95, 276), (78, 280), (68, 210), (49, 142)]
HAND = (88, 292, 10, 15)
LEG = [(3, 266), (45, 266), (46, 305), (37, 362), (31, 455), (9, 455), (6, 362)]
FOOT = [(8, 455), (32, 455), (41, 472), (6, 472)]
TORSO = [(-47, 90), (47, 90), (44, 150), (37, 212), (46, 262), (-46, 262), (-37, 212), (-44, 150)]
NECK = [(-9, 68), (9, 68), (9, 90), (-9, 90)]
HEAD = (0, 38, 28, 35)
ORGANS = (0, 168, 22, 26)
SANITY = (0, 38, 52)
#: 図の縁の太さ（ふだん・違法の改造のある部位）と、入れ先に選んでいる部位の縁取りの太さ。
LINE_WIDTH = 1.2
ILLEGAL_WIDTH = 2.0
TARGET_WIDTH = 2.4

_CLASSES = {}


def triangulate(points):
    """単純な多角形（自己交差なし）を三角形に分ける（耳の切り落とし）。`[(i, j, k)]` の添字の並び。

    ゲームに同梱の Kivy には `kivy.graphics.tesselator` が入っていない（2026-11-21 の実機で ImportError）ので、
    図の部位の塗りはこれで分けて `Mesh(mode="triangles")` で描く。
    """
    count = len(points)
    if count < 3:
        return []
    area = sum(points[i][0] * points[(i + 1) % count][1] - points[(i + 1) % count][0] * points[i][1]
               for i in range(count))
    order = list(range(count)) if area > 0 else list(range(count))[::-1]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0

    triangles = []
    guard = 0
    while len(order) > 3 and guard < count * count:
        guard += 1
        for index in range(len(order)):
            i, j, k = order[index - 1], order[index], order[(index + 1) % len(order)]
            a, b, c = points[i], points[j], points[k]
            if cross(a, b, c) <= 0:
                continue
            if any(inside(points[m], a, b, c) for m in order if m not in (i, j, k)):
                continue
            triangles.append((i, j, k))
            order.pop(index)
            break
        else:
            break
    if len(order) == 3:
        triangles.append(tuple(order))
    return triangles


def _classes():
    """押せる箱（Kivy の部品を組み合わせた型）。ゲームの中でだけ作る。"""
    if _CLASSES:
        return _CLASSES
    from kivy.uix.behaviors import ButtonBehavior
    from kivy.uix.boxlayout import BoxLayout

    class PressBox(ButtonBehavior, BoxLayout):
        pass

    _CLASSES["PressBox"] = PressBox
    return _CLASSES


#: 色を markup の16進にする（ローダの `panels.hex_of`）。
_hex = panels.hex_of


def _escape(text):
    from kivy.utils import escape_markup
    return escape_markup(text)


def _pieces_markup(pieces):
    color = {rules.PLUS: PLUS, rules.MINUS: MINUS, rules.PLAIN: panels.TEXT}
    from kivy.utils import escape_markup
    return "  ".join("[color={}]{}[/color]".format(_hex(color.get(kind, panels.TEXT)), escape_markup(text.replace(" ", " ")))
                     for text, kind in pieces)


def _split_paren(text):
    """「名前（説明）」を `(名前, 説明)` に分ける。括弧で終わらない文は `(文, "")`。"""
    head, mark, rest = text.partition("（")
    if mark and head and rest.endswith("）"):
        return head, rest[:-1]
    return text, ""


def _paren_markup(text, size):
    """文の末尾の括弧（「…（…）」）を字の大きさ `size` にした markup（色は付けない）。"""
    from kivy.utils import escape_markup
    head, mark, rest = text.rpartition("（")
    if mark and rest.endswith("）"):
        return "{}[size={}]（{}[/size]".format(escape_markup(head), int(size), escape_markup(rest))
    return escape_markup(text)


def _rows_markup(pieces, size):
    """詳しい欄の行の効果の並び。括弧の中の説明は小さい字（`size`）にする。効果の中の空白は折り返さない空白にする。"""
    color = {rules.PLUS: PLUS, rules.MINUS: MINUS, rules.PLAIN: panels.TEXT}
    return "  ".join("[color={}]{}[/color]".format(_hex(color.get(kind, panels.TEXT)),
                                                  _paren_markup(text.replace(" ", " "), size))
                     for text, kind in pieces)


def _rule(kit, color=panels.FAINT):
    """細い横の線（欄の区切り）。"""
    from kivy.graphics import Color, Rectangle
    from kivy.uix.widget import Widget
    line = Widget(size_hint_y=None, height=max(1, int(round(kit.px(1)))))
    with line.canvas:
        Color(*color)
        rect = Rectangle(pos=line.pos, size=line.size)
    line.bind(pos=lambda w, v: setattr(rect, "pos", v), size=lambda w, v: setattr(rect, "size", v))
    return line


def _filled(widget, color):
    """`widget` の下に塗りだけを敷く（`kit.framed` は枠の線を字の上に描くので、字の端が塗りの色の線に隠れた）。"""
    from kivy.graphics import Color, Rectangle
    with widget.canvas.before:
        Color(*color)
        rect = Rectangle(pos=widget.pos, size=widget.size)
    widget.bind(pos=lambda w, v: setattr(rect, "pos", v), size=lambda w, v: setattr(rect, "size", v))
    return widget


def _chip(kit, text, width, height, color):
    """項目名の枠（薄い地に字を真ん中）。"""
    from kivy.uix.label import Label
    label = kit._font(Label(text=text, color=color, font_size=kit.base * panels.META, halign="center",
                            valign="middle", size_hint=(None, None), size=(width, height)))
    label.bind(size=lambda w, value: setattr(w, "text_size", value))
    return _filled(label, CHIP_FILL)


class AugmentWindow(object):
    """画面1つ。`render(model)` で中身を描き直す（見立てや判定が済んだとき）。"""

    def __init__(self, app, model, *, on_select, on_action, on_dismiss=None, write=None):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.modalview import ModalView
        self.app = app
        self.on_select = on_select
        self.on_action = on_action
        self.write = write
        self.kit = panels.Kit(app)
        self.view = ModalView(size_hint=WINDOW_SIZE_HINT, auto_dismiss=True)
        for name in ("background_color", "overlay_color"):
            try:
                setattr(self.view, name, (0, 0, 0, 0.5))
            except Exception:
                pass
        kit = self.kit
        self.root = kit.framed(BoxLayout(orientation="vertical", padding=(kit.px(20), kit.px(12), kit.px(20), kit.px(20)),
                                         spacing=kit.px(8)),
                               fill=panels.BG)
        self.view.add_widget(self.root)
        # 閉じたら説明の窓も消す（マウスが動かないと見張りは呼ばれない）
        self.view.bind(on_dismiss=lambda *_a: panels.hide_tip())
        if on_dismiss is not None:
            self.view.bind(on_dismiss=lambda *_a: on_dismiss())
        self.render(model)
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

    # ------------------------------------------------------------ 全体
    def render(self, model):
        from kivy.uix.boxlayout import BoxLayout
        kit = self.kit
        self.palette = PALETTES.get(model.get("theme")) or PALETTES[rules.NEUTRAL]
        self.model = model
        panels.hide_tip()           # 描き直すと札が作り直されるので、前の札の説明の窓を消す
        self.root.clear_widgets()
        self.root.add_widget(self.header(model))
        # 上の段の高さは、札3枚（64px の絵の行）から決める。残りは下の段
        cards = len(rules.LEFT_PARTS)
        top = BoxLayout(orientation="horizontal", spacing=kit.px(16), size_hint_y=None,
                        height=self.card_height() * cards + kit.px(CARD_GAP) * (cards - 1))
        top.add_widget(self.column(model, rules.LEFT_PARTS))
        middle = BoxLayout(orientation="vertical", spacing=kit.px(4), size_hint_x=0.5)
        middle.add_widget(Figure(model["parts"], self.palette))
        middle.add_widget(self.legend(model))
        top.add_widget(middle)
        top.add_widget(self.column(model, rules.RIGHT_PARTS))
        self.root.add_widget(top)
        bottom = BoxLayout(orientation="horizontal", spacing=kit.px(16))
        bottom.add_widget(self.candidates(model))
        bottom.add_widget(self.detail(model))
        bottom.add_widget(self.totals(model))
        self.root.add_widget(bottom)

    def header(self, model):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        kit = self.kit
        words = model["words"]
        # 左に題、右（閉じるの左）に主人公と数の行と、所持金・現在地の行の2行を右揃えで重ねる
        # （見出しは1段で、空いた高さは下の段へ回る）
        line = self.line_height(panels.META)
        height = max(kit.base * panels.TITLE * 1.5, line * 2 + kit.px(2))
        head = BoxLayout(orientation="horizontal", spacing=kit.px(16), size_hint_y=None, height=height)
        title = kit.label(words["title"], panels.TITLE, wrap=False)
        title.pos_hint = {"center_y": 0.5}
        head.add_widget(title)
        info = BoxLayout(orientation="vertical", spacing=kit.px(2), size_hint_y=None, height=line * 2 + kit.px(2),
                         pos_hint={"center_y": 0.5})
        colors = {"illegal": self.palette["illegal"], "hurt": self.palette["hurt"]}
        pieces = [(model.get("player") or "", panels.DIM)] + [
            (text, colors.get(key, panels.TEXT)) for text, key in model["counts"]]
        place = [(model["gold"], panels.TEXT)] if model.get("gold") else []
        place.append((model["note"], self.palette["accent"] if model.get("mode") else panels.DIM))
        for row in (pieces, place):
            parts = []
            for index, (text, color) in enumerate(row):
                if index:
                    parts.append((HEAD_SEP, panels.FAINT))
                parts.append((text, color))
            label = kit.label(panels.markup(parts), panels.META, use_markup=True, wrap=False)
            label.pos_hint = {"right": 1}
            info.add_widget(label)
        head.add_widget(info)
        close = kit._font(Button(text="×", size_hint=(None, None), size=(kit.px(36), kit.px(36)),
                                 background_color=(0, 0, 0, 0), font_size=kit.base * panels.TITLE))
        close.bind(on_release=lambda *_a: self.view.dismiss())
        close.pos_hint = {"center_y": 0.5}
        head.add_widget(close)
        # 見出しの下に細い線（高さは足さない。見出しと下の段の間に引く）
        from kivy.graphics import Color, Rectangle
        with head.canvas.after:
            Color(*panels.FAINT)
            rule = Rectangle()

        def place_rule(w, _value):
            rule.pos = (w.x, w.y - kit.px(4))
            rule.size = (w.width, max(1, int(round(kit.px(1)))))
        head.bind(pos=place_rule, size=place_rule)
        return head

    def legend(self, model):
        from kivy.graphics import Color, Rectangle
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        kit = self.kit
        words = model["words"]
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.TICK * LEGEND_ROW,
                        spacing=kit.px(6))
        row.add_widget(Widget())
        for color, text in ((self.palette["accent"], words["augment"]), (self.palette["illegal"], words["illegal"]),
                            (self.palette["hurt"], "負傷")):
            chip = Widget(size_hint=(None, None), size=(kit.px(14), kit.px(14)), pos_hint={"center_y": 0.5})
            with chip.canvas:
                Color(*color)
                rect = Rectangle(pos=chip.pos, size=chip.size)
            chip.bind(pos=lambda w, v, r=rect: setattr(r, "pos", v))
            row.add_widget(chip)
            label = kit.label(text, panels.TICK, panels.DIM, wrap=False)
            label.pos_hint = {"center_y": 0.5}
            row.add_widget(label)
            row.add_widget(Widget(size_hint_x=None, width=kit.px(14)))
        row.add_widget(Widget())
        return row

    # ------------------------------------------------------------ 部位の札
    def icon_px(self):
        """アイテムの絵の大きさ（画面の px）。横幅の狭い窓では縮める。窓の大きさが読めなければふだんの大きさ。"""
        width, height = ui.window_size()
        if width and height and width / height < NARROW_ASPECT:
            return float(ITEM_ICON_NARROW)
        return float(ITEM_ICON)

    def line_height(self, scale):
        """字の大きさ `scale` の1行の高さ（Kivy の字の測り方で測る）。"""
        from kivy.core.text import Label as CoreLabel
        options = {"font_size": self.kit.base * scale}
        if self.kit.font_name:
            options["font_name"] = self.kit.font_name
        return float(CoreLabel(**options).get_extents("あ")[1])

    def slot_height(self):
        """札の改造の枠の高さ。64px の絵と、名前1行＋効果 `SLOT_LINES` 行の高いほう（上下の余白込み）。"""
        kit = self.kit
        words = self.line_height(panels.META) + self.line_height(panels.TICK) * SLOT_LINES + kit.px(2)
        return max(self.icon_px(), words) + kit.px(SLOT_PAD) * 2

    def card_height(self):
        """部位の札1枚の高さ（見出しの行と改造の枠の行）。"""
        kit = self.kit
        return kit.px(CARD_PAD) * 2 + kit.base * panels.NAME * 1.15 + kit.px(2) + self.slot_height()

    def column(self, model, parts):
        from kivy.uix.boxlayout import BoxLayout
        kit = self.kit
        box = BoxLayout(orientation="vertical", spacing=kit.px(CARD_GAP), size_hint_x=0.75)
        for part in parts:
            box.add_widget(self.card(part, model["parts"][part]))
        return box

    def card(self, part, data):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        kit = self.kit
        color = self.palette["accent"] if data.get("target") else panels.LINE
        box = kit.framed(BoxLayout(orientation="vertical", padding=(kit.px(10), kit.px(CARD_PAD)), spacing=kit.px(2),
                                   size_hint_y=None, height=self.card_height()),
                         color=color)
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.NAME * 1.15,
                         padding=(kit.px(BAND_PAD), 0, kit.px(BAND_PAD), 0))
        _filled(head, BAND_FILL)
        name = kit.label(data["name"], panels.NAME, wrap=False)
        name.pos_hint = {"center_y": 0.5}
        head.add_widget(name)
        # 侵食・自己修復の次の刻みまでの日数（部位名と状態の間。収まらない分は「…」）
        marks = data.get("wear") or []
        if marks:
            colors = {rules.MINUS: MINUS, rules.PLUS: PLUS}
            mark = kit.label(panels.markup([(text + ("　" if i < len(marks) - 1 else ""), colors.get(kind, panels.DIM))
                                            for i, (text, kind) in enumerate(marks)]),
                             panels.TICK, use_markup=True)
            mark.shorten = True
            mark.shorten_from = "right"
            mark.pos_hint = {"center_y": 0.5}
            head.add_widget(Widget(size_hint_x=None, width=kit.px(10)))
            head.add_widget(mark)
        else:
            head.add_widget(Widget())
        # 状態は怪我のある部位だけ出す（「正常」は出さず、空いた幅を日数の表示に回す）
        text, key = data["state"]
        if key == "hurt":
            hurt = kit.label(text, panels.META, self.palette["hurt"], wrap=False)
            hurt.pos_hint = {"center_y": 0.5}
            head.add_widget(hurt)
        box.add_widget(head)
        row = BoxLayout(orientation="horizontal", spacing=kit.px(SLOT_GAP), size_hint_y=None, height=self.slot_height())
        slots = data["slots"]
        for slot in range(rules.SLOTS):
            record = slots[slot] if slot < len(slots) else None
            # 印は品が入る枠にだけ出す（重ねるときは2つめの枠）。2つめの枠は、埋まっているか入れ先のときだけ
            # 置き、ほかは1つめの枠が札の幅を使う（効果が折り返さないように）
            target = bool(data.get("target")) and data.get("target_slot") == slot
            if record is None and slot > 0 and not target:
                continue
            row.add_widget(self.slot_cell(part, slot, record, target))
        box.add_widget(row)
        return box

    def icon(self, source, size, color, fill=(0.055, 0.055, 0.07, 1)):
        """1色の枠（空きの札）。アイテムの絵はレア度の見せ方の `item_icon` で描く。"""
        from kivy.uix.anchorlayout import AnchorLayout
        from kivy.uix.image import Image
        kit = self.kit
        frame = kit.framed(AnchorLayout(size_hint=(None, None), size=(size, size), padding=kit.px(4)),
                           color=color, fill=fill)
        if isinstance(source, str) and source:
            try:
                image = Image(source=source, fit_mode="contain")
            except TypeError:
                image = Image(source=source, allow_stretch=True, keep_ratio=True)
            frame.add_widget(image)
        return frame

    def item_icon(self, source, size, rarity, mark=None):
        """アイテムの絵を、ゲームのアイテム欄と同じレア度の見せ方で（地は透明、下からのにじみと 2px の枠）。"""
        from kivy.uix.anchorlayout import AnchorLayout
        from kivy.uix.image import Image
        frame = ui.paint_rarity(AnchorLayout(size_hint=(None, None), size=(size, size)), rarity, mark)
        if isinstance(source, str) and source:
            try:
                image = Image(source=source, fit_mode="contain")
            except TypeError:
                image = Image(source=source, allow_stretch=True, keep_ratio=True)
            frame.add_widget(image)
        return frame

    def slot_cell(self, part, slot, record, target):
        from kivy.graphics import Color, Rectangle
        from kivy.uix.boxlayout import BoxLayout
        kit = self.kit
        cell = _classes()["PressBox"](orientation="horizontal", spacing=kit.px(SLOT_GAP),
                                      padding=(kit.px(SLOT_SIDE), kit.px(SLOT_PAD)))
        size = self.icon_px()
        if record is None:
            empty = self.icon(None, size, self.palette["accent"] if target else panels.FAINT)
            empty.pos_hint = {"center_y": 0.5}
            cell.add_widget(empty)
            # 入れ先の枠は強調の色で分かるので、字は「空き」のまま
            label = kit.label("空き", panels.META, self.palette["accent"] if target else panels.FAINT)
            label.pos_hint = {"center_y": 0.5}
            cell.add_widget(label)
            return cell
        if record.get("selected"):
            with cell.canvas.before:
                Color(*SELECTED)
                rect = Rectangle(pos=cell.pos, size=cell.size)
            cell.bind(pos=lambda w, v: setattr(rect, "pos", v), size=lambda w, v: setattr(rect, "size", v))
        # 枠はレア度の色。禁制のアイテムだけ違法の色の枠を重ねる（ほかの違法は名前の字と人体図の縁取りで示す）
        icon = self.item_icon(record.get("image"), size, record.get("rarity"),
                              self.palette["illegal"] if record.get("forbidden") else None)
        icon.pos_hint = {"center_y": 0.5}
        cell.add_widget(icon)
        # 札の枠の高さは決まっているので、名前は1行、効果は2行までにして続きは「…」で切る（全部は説明の窓に出す）。
        # 字の塊は絵の高さの中央に揃える
        words = BoxLayout(orientation="vertical", spacing=kit.px(4), size_hint_y=None, pos_hint={"center_y": 0.5})
        words.bind(minimum_height=words.setter("height"))
        name = kit.label(record["name"], panels.META, self.name_color(record))
        name.shorten = True
        name.shorten_from = "right"
        words.add_widget(name)
        effects = kit.label("", panels.TICK, use_markup=True)
        effects.bind(width=lambda w, value, pieces=record["pieces"]: setattr(
            w, "text", self.clipped_pieces(pieces, value, panels.TICK, SLOT_LINES)))
        words.add_widget(effects)

        def fit(_widget, width):
            # 字の欄が狭いとき（4:3 で約 62px）は効果を外し、名前を2行まで出す（効果は説明の窓と詳しい欄で見る）
            narrow = width < kit.px(NARROW_TEXT)
            if narrow and effects.parent is not None:
                words.remove_widget(effects)
            elif not narrow and effects.parent is None:
                words.add_widget(effects)
            name.shorten = not narrow
            name.max_lines = 2 if narrow else 0
        words.bind(width=fit)
        cell.add_widget(words)
        cell.bind(on_release=lambda *_a: self.on_select("slot", (part, slot)))
        if record.get("tip"):
            panels.hover_tip(kit, cell, lambda record=record: self.tip_markup(record))
        return cell

    def clipped_pieces(self, pieces, width, scale, max_lines):
        """効果の並びを幅 `width` で折り返した markup。`max_lines` 行を超える分は、最後の行の末尾を「…」にして切る。

        字の幅は Kivy の字の測り方（`CoreLabel.get_extents`）で測る。折り返しは効果の切れ目でだけ行う。
        """
        from kivy.core.text import Label as CoreLabel
        if not pieces:
            return ""
        options = {"font_size": self.kit.base * scale}
        if self.kit.font_name:
            options["font_name"] = self.kit.font_name
        measure = CoreLabel(**options).get_extents
        gap = measure("  ")[0]
        more = measure("…")[0]
        lines, current, used = [], [], 0.0
        for piece in pieces:
            size = measure(piece[0])[0]
            if current and used + gap + size > width:
                lines.append(current)
                current, used = [], 0.0
            used += (gap if current else 0) + size
            current.append(piece)
        lines.append(current)
        if len(lines) > max_lines:
            last = lines[max_lines - 1]
            # 「…」が入るまで、最後の行の後ろの効果を外す
            while len(last) > 1 and sum(measure(p[0])[0] for p in last) + gap * len(last) + more > width:
                last.pop()
            lines = lines[:max_lines - 1] + [last]
            return "\n".join(_pieces_markup(line) for line in lines) + "…"
        return "\n".join(_pieces_markup(line) for line in lines)

    def name_color(self, data):
        """改造やアイテムの名前の色。違法（限界施術・禁制）は違法の色、合法は地の字の色。
        禁制かどうかは、アイテムの絵に重ねる違法の色の枠で分ける。"""
        return self.palette["illegal"] if data.get("illegal") or data.get("forbidden") else panels.TEXT

    def tip_markup(self, record):
        """札にマウスを乗せたときの説明の窓の文。下の詳しい欄と同じ順（名前と部位・仕上げ・改造の説明・効果・代償）に並べ、
        元のアイテムの名前と説明は最後に置く。"""
        from kivy.utils import escape_markup
        tip = record["tip"]
        color = self.name_color(record)
        head = "[size={}][color={}]{}[/color][/size]".format(
            int(self.kit.base * panels.NAME), _hex(color), escape_markup(record["name"]))
        if tip.get("tag"):
            head += "　[color={}][{}][/color]".format(_hex(color), escape_markup(tip["tag"]))
        # 部位と仕上げは、詳しい欄と同じく名前の後ろに小さく並べる
        sub = "　".join(s for s in tip.get("sub") or [] if s)
        if sub:
            head += "　[color={}]{}[/color]".format(_hex(panels.DIM), escape_markup(sub))
        lines = [head]
        if tip.get("description"):
            lines.append(escape_markup(tip["description"]))
        # 持ち物の候補は `rows`（方法が2つなら方法ごとの行）、部位の札は効果と代償
        rows = tip.get("rows") or (("効果", tip.get("effects")), ("代償", tip.get("costs")))
        for label, pieces in rows:
            if pieces:
                lines.append("[color={}]{}[/color]　{}".format(_hex(panels.DIM), label, _pieces_markup(pieces)))
        lines.append("[color={}]{}[/color]".format(_hex(panels.DIM), escape_markup(tip["item"])))
        if tip.get("item_description"):
            lines.append("[color={}]{}[/color]".format(_hex(panels.DIM), escape_markup(tip["item_description"])))
        return "\n".join(lines)

    # ------------------------------------------------------------ 下の段
    def candidates(self, model):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.gridlayout import GridLayout
        from kivy.uix.widget import Widget
        kit = self.kit
        box = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(12), spacing=kit.px(8)),
                         )
        box.size_hint_x = 0.3
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.NAME * 1.3)
        head.add_widget(kit.label("持ち物の候補", panels.NAME, wrap=False))
        head.add_widget(Widget())
        head.add_widget(kit.label("適合 {}".format(len(model["candidates"])), panels.META, panels.DIM,
                                  wrap=False))
        box.add_widget(head)
        # 列の数は欄の幅から決める（狭いウィンドウで絵どうしが重ならないように）
        grid = GridLayout(cols=5, size_hint_y=None, spacing=kit.px(8))
        grid.bind(minimum_height=grid.setter("height"),
                  width=lambda w, value: setattr(w, "cols", max(1, int((value + kit.px(8)) // (
                      self.icon_px() + kit.px(CANDIDATE_MARGIN) + kit.px(8))))))
        for row in model["candidates"]:
            grid.add_widget(self.candidate(row))
        if not model["candidates"]:
            box.add_widget(kit.label(model.get("candidate_empty") or "", panels.META, panels.DIM))
        box.add_widget(kit.scroller(grid))
        if model.get("candidate_note"):
            box.add_widget(_rule(kit))
            box.add_widget(kit.label(model["candidate_note"], panels.TICK, panels.FAINT))
        return box

    def candidate(self, row):
        kit = self.kit
        size = self.icon_px()
        cell = _classes()["PressBox"](orientation="vertical", size_hint_y=None,
                                      height=size + kit.base * panels.TICK * 1.6, spacing=kit.px(2))
        chosen = row.get("selected")
        forbidden = row["kind"] == "forbidden"
        # ゲームのアイテム欄と同じレア度の見せ方（下からのにじみと枠）。ゲームが装備中の品に金の枠を重ねる位置に、
        # 選んでいる札は強調の色、禁制のアイテムは違法の色の枠を重ねる（禁制を選んでいるときは強調の色）
        mark = self.palette["accent"] if chosen else (self.palette["illegal"] if forbidden else None)
        icon = self.item_icon(row.get("image"), size, row.get("rarity"), mark)
        icon.pos_hint = {"center_x": 0.5}
        cell.add_widget(icon)
        text_color = self.palette["accent"] if chosen else (self.palette["illegal"] if forbidden else panels.DIM)
        label = kit.label(row["label"], panels.TICK, text_color, halign="center")
        label.shorten = True        # 「頭・禁制」が2行に折れて下の札の字に重ならないよう、1行にする
        cell.add_widget(label)
        cell.bind(on_release=lambda *_a: self.on_select("item", row["key"]))
        if row.get("tip"):
            # 札は絵と部位だけなので、改造の名前と効果はマウスを乗せたときに出す（部位の札と同じ窓）
            panels.hover_tip(kit, cell, lambda row=row: self.tip_markup(row))
        return cell

    def detail(self, model):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.widget import Widget
        from kivy.utils import escape_markup
        kit = self.kit
        box = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(12), spacing=kit.px(6)))
        box.size_hint_x = 0.44
        data = model.get("detail")
        if not data:
            box.add_widget(kit.label("改造かアイテムを選ぶと、ここに詳しく出る。", panels.META, panels.DIM))
            box.add_widget(Widget())
            return box
        # 上の段の高さは、絵と「名前＋説明文」の高いほうに合わせる（説明文が2行でも名前を押し出さない）
        icon_size = self.icon_px()
        top = BoxLayout(orientation="horizontal", spacing=kit.px(12), size_hint_y=None, height=icon_size)
        icon = self.item_icon(data.get("image"), icon_size, data.get("rarity"),
                              self.palette["illegal"] if data.get("forbidden") else None)
        icon.pos_hint = {"top": 1}
        top.add_widget(icon)
        words = BoxLayout(orientation="vertical", spacing=kit.px(6), size_hint_y=None, pos_hint={"top": 1})
        words.bind(minimum_height=words.setter("height"))
        words.bind(height=lambda w, value: setattr(top, "height", max(icon_size, value)))
        name = data["name"]
        color = self.name_color(data)
        pieces = [(name, color)]
        if data.get("illegal"):
            pieces.append(("　[{}]".format(data.get("tag") or model["words"]["illegal"]), color))
        title = panels.markup(pieces)
        # 部位と仕上げは、名前の後ろに小さく並べる（副題の行は作らない）
        sub = "　".join(s for s in data.get("sub") or [] if s)
        if sub:
            title += "　[size={}][color={}]{}[/color][/size]".format(
                int(kit.base * panels.BODY), _hex(panels.DIM), escape_markup(sub))
        words.add_widget(kit.label(title, DETAIL_NAME, use_markup=True))
        words.add_widget(kit.label(data.get("description") or "", panels.BODY))
        top.add_widget(words)
        box.add_widget(top)
        body = kit.column(spacing=4)
        danger_rows = set(data.get("danger_rows") or ())
        # 行は `(字下げ, 項目名, 効果の並び, 項目名の色)`。代償のうち括弧の説明つきのもの（反動・侵食など）は、
        # 親の行の下に字下げした行で「名前｜説明」と出す（1行に詰めると折り返して読みにくかった）
        # 方法ごとの行（通常施術・限界施術）は塊にし、塊の間に線を引く。塊の中は1行目が伸びる効果、2行目が代償
        # （折り返しに任せると、幅によって代償の落ちる位置が変わった）。`None` の項目名は枠を置かず字下げだけ
        methods = set(rules.METHOD_NAMES.values())
        lines = []
        for label, row in data.get("rows") or []:
            if not row:
                continue
            subs = [piece for piece in row if piece[1] == rules.MINUS and _split_paren(piece[0])[1]]
            main = [piece for piece in row if piece not in subs]
            # 違法の改造になる方法の行（限界施術）は、項目名を違法の色にする
            color = self.palette["illegal"] if label in danger_rows else panels.DIM
            if label in methods:
                if lines:
                    lines.append(None)
                gains = [piece for piece in main if piece[1] != rules.MINUS]
                costs = [piece for piece in main if piece[1] == rules.MINUS]
                if gains:
                    lines.append((0, label, gains, color))
                if costs:
                    lines.append((0, None if gains else label, costs, color))
            elif main:
                lines.append((0, label, main, color))
            for text, kind in subs:
                name, words_text = _split_paren(text)
                lines.append((1 if main else 0, name, [(words_text, kind)], panels.DIM))
        chip = max([self.text_width(line[1], panels.META) for line in lines if line and line[1]] or [0]) \
            + kit.px(CHIP_PAD)
        small = kit.base * panels.BODY * PAREN_SCALE

        def note_label(text, kind):
            color = MINUS if kind == rules.MINUS else (PLUS if kind == rules.PLUS else panels.DIM)
            return kit.label("[color={}]{}[/color]".format(
                _hex(color), _paren_markup(text, kit.base * panels.META * PAREN_SCALE)), panels.META, use_markup=True)
        # 警告（押せない理由など）は効果の行より上に出す（下に置くとスクロールの奥に隠れて、ボタンが押せない理由が見えなかった）
        notes = data.get("notes") or []
        for text, kind in notes:
            if kind == rules.MINUS:
                body.add_widget(note_label(text, kind))
        for line in lines:
            if line is None:
                body.add_widget(Widget(size_hint_y=None, height=kit.px(2)))
                body.add_widget(_rule(kit, BLOCK_LINE))
                body.add_widget(Widget(size_hint_y=None, height=kit.px(2)))
                continue
            indent, label, pieces, color = line
            body.add_widget(self.detail_row(label, _rows_markup(pieces, small), chip, color, indent))
        # 良い知らせ、失敗したときの説明は効果の行の後
        for kind_first in (rules.PLUS, None):
            for text, kind in notes:
                if kind != rules.MINUS and (kind == rules.PLUS) == (kind_first == rules.PLUS):
                    body.add_widget(note_label(text, kind))
        box.add_widget(kit.scroller(body))
        # スクロールする欄とボタンの段の間に、すき間と線を入れる（切れた行がボタンにかかって見えた）
        buttons_here = data.get("buttons") or data.get("button")
        if buttons_here:
            box.add_widget(_rule(kit, BLOCK_LINE))
        # ボタンは1つか2つ（裏の事務所の正規のアイテムは通常施術と限界施術）。どちらも欄の半分の幅。
        # 料金と期間・失敗したときの怪我（`lines`。古い形は `side` の1行）は、名前の下に小さい字で重ねる
        buttons = data.get("buttons") or ([data["button"]] if data.get("button") else [])
        if buttons:
            def lines_of(button):
                return list(button.get("lines") or ([button["side"]] if button.get("side") else []))
            count = max(len(lines_of(button)) for button in buttons)
            height = max(kit.base * 2.2, kit.base * BUTTON_LINE * (panels.NAME + panels.TICK * count) + kit.px(5))
            row = BoxLayout(orientation="horizontal", size_hint_y=None, height=height, spacing=kit.px(16))
            for button in buttons:
                # 違法の改造になるボタン（`danger`）は、押せるときは枠と字を違法の色にする
                enabled = button.get("enabled")
                ink = (self.palette["illegal"] if button.get("danger") else panels.TEXT) if enabled else panels.FAINT
                edge = (self.palette["illegal"] if button.get("danger") else panels.LINE) if enabled else panels.FAINT
                lines = lines_of(button)
                press = kit._font(Button(text="" if lines else button["text"], size_hint=(0.5, None), height=height,
                                         font_size=kit.base, background_color=(0, 0, 0, 0), color=ink))
                if lines:
                    press.add_widget(self.button_face(press, button["text"], lines, ink))
                press.disabled = not enabled
                press.bind(on_release=lambda *_a, button=button: self.on_action(button["action"], button["payload"]))
                row.add_widget(kit.framed(press, color=edge, fill=(0.04, 0.04, 0.05, 1)))
            if len(buttons) == 1:
                row.add_widget(Widget(size_hint_x=0.5))
            box.add_widget(row)
        return box

    def text_width(self, text, scale):
        """字の大きさ `scale` で1行に書いたときの幅（Kivy の字の測り方で測る）。"""
        from kivy.core.text import Label as CoreLabel
        options = {"font_size": self.kit.base * scale}
        if self.kit.font_name:
            options["font_name"] = self.kit.font_name
        return float(CoreLabel(**options).get_extents(text or "")[0])

    def detail_row(self, label, markup, chip, color, indent):
        """詳しい欄の1行。左に項目名の枠、右に効果（折り返すと行が高くなる。枠は上に揃える）。"""
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        kit = self.kit
        height = self.line_height(panels.BODY)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=height, spacing=kit.px(CHIP_GAP))
        if indent:
            row.add_widget(Widget(size_hint_x=None, width=kit.px(SUB_INDENT)))
        if label is None:
            # 前の行の続き（代償の行）。枠は置かず、枠の幅だけ空けて字の書き出しを揃える
            row.add_widget(Widget(size_hint_x=None, width=chip))
        else:
            box = _chip(kit, label, chip, height, color)
            box.pos_hint = {"top": 1}
            row.add_widget(box)
        text = kit.label(markup, panels.BODY, use_markup=True)
        text.pos_hint = {"top": 1}
        text.bind(height=lambda w, value: setattr(row, "height", max(height, value)))
        row.add_widget(text)
        return row

    def button_face(self, press, text, lines, ink):
        """ボタンの中身。上に大きい行（名前と成功率）、下に小さい字の行を中央揃えで積む
        （ボタンの位置と大きさに付いて動く）。"""
        from kivy.uix.anchorlayout import AnchorLayout
        from kivy.uix.boxlayout import BoxLayout
        kit = self.kit
        # 日本語の字は字体の上側に余白があるので、上の余白を 0 にして上下の隙間を揃える
        face = BoxLayout(orientation="vertical", padding=(kit.px(8), 0, kit.px(8), kit.px(5)))
        for line, scale in [(text, panels.NAME)] + [(line, panels.TICK) for line in lines]:
            slot = AnchorLayout(size_hint_y=scale)
            slot.add_widget(kit.label(line, scale, ink, wrap=False))
            face.add_widget(slot)

        def follow(*_args):
            face.pos, face.size = press.pos, press.size
        press.bind(pos=follow, size=follow)
        follow()
        return face

    def totals(self, model):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.gridlayout import GridLayout
        from kivy.uix.widget import Widget
        kit = self.kit
        box = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(12), spacing=kit.px(6)))
        box.size_hint_x = 0.26
        box.add_widget(kit.label("合計の効果", panels.NAME))
        content = kit.column(spacing=4)
        stats = model["totals"]["stats"]
        battle = model["totals"]["battle"]
        if not stats and not battle:
            content.add_widget(kit.label("まだ何も入っていない。", panels.META, panels.DIM))
        grid = GridLayout(cols=2, size_hint_y=None, spacing=kit.px(4))
        grid.bind(minimum_height=grid.setter("height"))
        for name, value, kind in stats:
            grid.add_widget(kit.label("{}  [color={}]{}[/color]".format(
                name, _hex(PLUS if kind == rules.PLUS else MINUS), value), TOTAL_STAT, use_markup=True))
        content.add_widget(grid)
        if battle:
            if stats:
                content.add_widget(Widget(size_hint_y=None, height=kit.px(4)))
                content.add_widget(_rule(kit))
                content.add_widget(Widget(size_hint_y=None, height=kit.px(2)))
            content.add_widget(kit.label("戦闘", panels.BODY, panels.DIM))
            lines = GridLayout(cols=2, size_hint_y=None, spacing=kit.px(4))
            lines.bind(minimum_height=lines.setter("height"))
            hints = model["totals"].get("hints") or []
            for index, (text, kind) in enumerate(battle):
                line = kit.label(text, TOTAL_BATTLE, PLUS if kind == rules.PLUS else MINUS)
                lines.add_widget(line)
                if index < len(hints) and hints[index]:
                    # 短い形（`軽減 10%`）では中身が分からないので、マウスを乗せると長い形を出す
                    panels.hover_tip(kit, line, lambda hint=hints[index]: _escape(hint))
            content.add_widget(lines)
        box.add_widget(kit.scroller(content))
        return box


def Figure(parts, palette):
    """人体の図（部位ごとに塗り分け、違法の部位は縁を違法の色でなぞる）。"""
    from kivy.graphics import Color, Ellipse, Line, Mesh
    from kivy.uix.widget import Widget

    widget = Widget()

    def style(part):
        """部位の `(塗り, 縁, 縁の太さ)`。違法の改造のある部位は、縁を部位まるごと違法の色にする（塗りは怪我・改造のまま）。"""
        data = parts.get(part) or {}
        if data.get("hurt"):
            fill, line = palette["hurt_fill"], palette["hurt"]
        elif data.get("augmented"):
            fill, line = palette["fill"], palette["accent"]
        else:
            fill, line = PLAIN_FILL, PLAIN_LINE
        if data.get("illegal"):
            return fill, palette["illegal"], ILLEGAL_WIDTH
        return fill, line, LINE_WIDTH

    def redraw(*_args):
        widget.canvas.clear()
        if widget.width <= 0 or widget.height <= 0:
            return
        scale = min(widget.width / FIG_W, widget.height / FIG_H)
        left = widget.x + (widget.width - FIG_W * scale) / 2.0
        top = widget.y + (widget.height + FIG_H * scale) / 2.0
        cx = FIG_W / 2.0

        def at(x, y):
            return left + (cx + x) * scale, top - (FIG_TOP + y) * scale

        def flat(points, sign=1):
            out = []
            for x, y in points:
                out.extend(at(sign * x, y))
            return out

        def polygon(points, colors, mirror=False):
            fill, line, width = colors
            for sign in ((1, -1) if mirror else (1,)):
                coords = flat(points, sign)
                corners = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
                vertices = []
                for x, y in corners:
                    vertices.extend([x, y, 0, 0])
                indices = [index for triangle in triangulate(corners) for index in triangle]
                if indices:
                    Color(*fill)
                    Mesh(vertices=vertices, indices=indices, mode="triangles")
                Color(*line)
                Line(points=coords, close=True, width=width)

        def oval(spec, colors, mirror=False):
            ox, oy, rx, ry = spec
            fill, line, width = colors
            for sign in ((1, -1) if mirror else (1,)):
                x, y = at(sign * ox, oy)
                if fill is not None:            # None は塗らない（下の部位が透けて見える）
                    Color(*fill)
                    Ellipse(pos=(x - rx * scale, y - ry * scale), size=(rx * 2 * scale, ry * 2 * scale))
                Color(*line)
                Line(ellipse=(x - rx * scale, y - ry * scale, rx * 2 * scale, ry * 2 * scale), width=width)

        with widget.canvas:
            backdrop(at, scale)
            oval(HAND, style("arms"), mirror=True)
            polygon(ARM, style("arms"), mirror=True)
            polygon(FOOT, style("legs"), mirror=True)
            polygon(LEG, style("legs"), mirror=True)
            polygon(TORSO, style("body"))
            polygon(NECK, style("head"))
            oval(HEAD, style("head"))
            # 内臓は胴の中の輪。改造も怪我も無いときは塗らず、胴の色が透けて見える縁の線だけにする
            # （暗い色で塗ると胴に穴が空いて見えた）。入れ先に選んでいる間は強調の色、違法の改造があれば違法の色
            organ_fill, organ_line, organ_width = style("organs")
            organ_data = parts.get("organs") or {}
            if not organ_data.get("augmented") and not organ_data.get("hurt"):
                organ_fill = None
            if organ_data.get("target"):
                organ_line, organ_width = palette["accent"], LINE_WIDTH
            oval(ORGANS, (organ_fill, organ_line, organ_width))
            _fill, sanity_line, _width = style("sanity")
            x, y = at(SANITY[0], SANITY[1])
            radius = SANITY[2] * scale
            Color(*sanity_line)
            for start in range(-65, 66, 14):
                Line(circle=(x, y, radius, start, start + (14 if (parts.get("sanity") or {}).get("augmented")
                                                            else 7)), width=1.4)
            # 入れ先に選んでいる部位は、部位そのものを太い白で縁取る（改造のある部位の縁は強調の色なので、
            # 同じ色では見分けられない）。内臓は上で輪の縁を強調の色にしてある
            def target(part):
                return (parts.get(part) or {}).get("target")

            def outline(points, mirror=False):
                for sign in ((1, -1) if mirror else (1,)):
                    Line(points=flat(points, sign), close=True, width=TARGET_WIDTH)

            def ring(spec, mirror=False):
                ox, oy, rx, ry = spec
                for sign in ((1, -1) if mirror else (1,)):
                    x, y = at(sign * ox, oy)
                    Line(ellipse=(x - rx * scale, y - ry * scale, rx * 2 * scale, ry * 2 * scale), width=TARGET_WIDTH)

            Color(*panels.TEXT)
            if target("head"):
                ring(HEAD)
                outline(NECK)
            if target("arms"):
                outline(ARM, mirror=True)
                ring(HAND, mirror=True)
            if target("legs"):
                outline(LEG, mirror=True)
                outline(FOOT, mirror=True)
            if target("body"):
                outline(TORSO)
            if target("sanity"):
                x, y = at(SANITY[0], SANITY[1])
                Line(circle=(x, y, SANITY[2] * scale, -65, 65), width=TARGET_WIDTH)

    def backdrop(at, scale):
        theme_back = palette["back"]
        if palette is PALETTES[rules.MACHINE]:
            Color(*theme_back)
            x0, y0 = at(-FIG_W / 2.0, -FIG_TOP)
            x1, y1 = at(FIG_W / 2.0, FIG_H - FIG_TOP)
            step = 32 * scale
            x = x0
            while x <= x1 + 0.5:
                Line(points=[x, y0, x, y1], width=1)
                x += step
            y = y1
            while y <= y0 + 0.5:
                Line(points=[x0, y, x1, y], width=1)
                y += step
        elif palette is PALETTES[rules.MAGIC]:
            Color(*theme_back)
            cx, cy = at(0, 236)
            for radius, width in ((158, 1.6), (146, 1.0), (96, 1.0)):
                Line(circle=(cx, cy, radius * scale), width=width)
            for index in range(24):
                angle = math.radians(index * 15)
                Line(points=[cx + math.cos(angle) * 146 * scale, cy + math.sin(angle) * 146 * scale,
                             cx + math.cos(angle) * 158 * scale, cy + math.sin(angle) * 158 * scale], width=1)
            star = []
            for index in range(6):
                angle = math.radians(90 + index * 144)
                star.extend([cx + math.cos(angle) * 146 * scale, cy + math.sin(angle) * 146 * scale])
            Line(points=star, width=1)

    widget.bind(pos=redraw, size=redraw)
    return widget
