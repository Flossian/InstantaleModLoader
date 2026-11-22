# -*- coding: utf-8 -*-
"""人体改造の画面を描く（Kivy）。中身は `body_augmentation.build_model` が組んだものをそのまま描く。

部品と色はローダの `panels`（TECH.md §3.3.17）に揃える。並びは次のとおり。

    見出し（題・数・世界観の判定し直し・閉じる）
    左の列: 頭・腕・脚の札 ／ 中央: 人体の図 ／ 右の列: 精神・胴・内臓の札（札は枠を横に2つ）
    下の段: 持ち物の候補 ／ 選んだものの詳細（施術・取り外しのボタン） ／ 合計の効果

見た目の種類（機械・魔術・中立）で、強調の色と図の背景（方眼・魔法陣・無地）だけを替える。
Kivy はゲームの中でだけ読む（関数の中で import する）。
"""
import math

from instantale_modloader import panels

from . import rules

#: 見た目の種類ごとの色。`accent` は改造のある部位と強調、`fill` は図の塗り、`illegal` は違法、`hurt` は怪我。
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


def _hex(color):
    return panels.hex_of(color)


def _pieces_markup(pieces):
    color = {rules.PLUS: PLUS, rules.MINUS: MINUS, rules.PLAIN: panels.TEXT}
    from kivy.utils import escape_markup
    return "  ".join("[color={}]{}[/color]".format(_hex(color.get(kind, panels.TEXT)), escape_markup(text.replace(" ", " ")))
                     for text, kind in pieces)


class AugmentWindow(object):
    """画面1つ。`render(model)` で中身を描き直す（見立てや判定が済んだとき）。"""

    def __init__(self, app, model, *, on_select, on_action, on_redetect, on_dismiss=None, write=None):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.modalview import ModalView
        self.app = app
        self.on_select = on_select
        self.on_action = on_action
        self.on_redetect = on_redetect
        self.write = write
        self.kit = panels.Kit(app)
        self.view = ModalView(size_hint=panels.SIZE_HINT, auto_dismiss=True)
        for name in ("background_color", "overlay_color"):
            try:
                setattr(self.view, name, (0, 0, 0, 0.5))
            except Exception:
                pass
        kit = self.kit
        self.root = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(20), spacing=kit.px(12)),
                               fill=panels.BG)
        self.view.add_widget(self.root)
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
        self.root.clear_widgets()
        self.root.add_widget(self.header(model))
        top = BoxLayout(orientation="horizontal", spacing=kit.px(16), size_hint_y=0.56)
        top.add_widget(self.column(model, rules.LEFT_PARTS))
        middle = BoxLayout(orientation="vertical", spacing=kit.px(4), size_hint_x=0.5)
        middle.add_widget(Figure(model["parts"], self.palette))
        middle.add_widget(self.legend(model))
        top.add_widget(middle)
        top.add_widget(self.column(model, rules.RIGHT_PARTS))
        self.root.add_widget(top)
        bottom = BoxLayout(orientation="horizontal", spacing=kit.px(16), size_hint_y=0.44)
        bottom.add_widget(self.candidates(model))
        bottom.add_widget(self.detail(model))
        bottom.add_widget(self.totals(model))
        self.root.add_widget(bottom)

    def header(self, model):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.widget import Widget
        kit = self.kit
        words = model["words"]
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        height=kit.base * panels.TITLE * 1.5 + kit.base * panels.META * 1.6)
        head = BoxLayout(orientation="horizontal", spacing=kit.px(16))
        head.add_widget(kit.label(words["title"], panels.TITLE, wrap=False))
        head.add_widget(Widget())
        if model.get("theme_auto"):
            text = "世界観を判定中……" if model.get("theme_pending") else "世界観を判定し直す"
            redo = kit._font(Button(text=text, size_hint=(None, None), font_size=kit.base * panels.TICK,
                                    background_color=(0, 0, 0, 0), color=panels.DIM,
                                    height=kit.base * panels.TITLE))
            redo.width = kit.base * panels.TICK * (len(text) + 2)
            redo.pos_hint = {"center_y": 0.5}
            redo.disabled = bool(model.get("theme_pending"))
            redo.bind(on_release=lambda *_a: self.on_redetect())
            head.add_widget(kit.framed(redo, color=panels.FAINT))
        close = kit._font(Button(text="×", size_hint=(None, None), size=(kit.px(36), kit.px(36)),
                                 background_color=(0, 0, 0, 0), font_size=kit.base * panels.TITLE))
        close.bind(on_release=lambda *_a: self.view.dismiss())
        head.add_widget(close)
        box.add_widget(head)
        sub = BoxLayout(orientation="horizontal", spacing=kit.px(16))
        colors = {"illegal": self.palette["illegal"], "hurt": self.palette["hurt"]}
        pieces = [(model.get("player") or "", panels.DIM)] + [
            (text, colors.get(key, panels.TEXT)) for text, key in model["counts"]]
        sub.add_widget(kit.label(panels.markup([(t + "　", c) for t, c in pieces]), panels.META,
                                 use_markup=True, wrap=False))
        sub.add_widget(Widget())
        sub.add_widget(kit.label(model["note"], panels.META,
                                 self.palette["accent"] if model.get("mode") else panels.DIM, wrap=False))
        box.add_widget(sub)
        return box

    def legend(self, model):
        from kivy.graphics import Color, Rectangle
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        kit = self.kit
        words = model["words"]
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.TICK * 1.6,
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
    def column(self, model, parts):
        from kivy.uix.boxlayout import BoxLayout
        kit = self.kit
        box = BoxLayout(orientation="vertical", spacing=kit.px(14), size_hint_x=0.75)
        for part in parts:
            box.add_widget(self.card(part, model["parts"][part]))
        return box

    def card(self, part, data):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        kit = self.kit
        color = self.palette["accent"] if data.get("target") else panels.LINE
        box = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(10), spacing=kit.px(6)), color=color)
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.NAME * 1.3)
        head.add_widget(kit.label(data["name"], panels.NAME, wrap=False))
        head.add_widget(Widget())
        text, key = data["state"]
        head.add_widget(kit.label(text, panels.META, self.palette["hurt"] if key == "hurt" else panels.DIM,
                                  wrap=False))
        box.add_widget(head)
        row = BoxLayout(orientation="horizontal", spacing=kit.px(10))
        slots = data["slots"]
        for slot in range(rules.SLOTS):
            record = slots[slot] if slot < len(slots) else None
            # 印は品が入る枠にだけ出す（重ねるときは2つめの枠）
            target = bool(data.get("target")) and data.get("target_slot") == slot
            if record is None and slot > 0 and not target:
                row.add_widget(Widget())
                continue
            row.add_widget(self.slot_cell(part, slot, record, target))
        box.add_widget(row)
        return box

    def icon(self, source, size, color):
        from kivy.uix.anchorlayout import AnchorLayout
        from kivy.uix.image import Image
        kit = self.kit
        frame = kit.framed(AnchorLayout(size_hint=(None, None), size=(size, size), padding=kit.px(4)),
                           color=color, fill=(0.055, 0.055, 0.07, 1))
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
        cell = _classes()["PressBox"](orientation="horizontal", spacing=kit.px(10), padding=kit.px(4))
        size = kit.px(52)
        if record is None:
            cell.add_widget(self.icon(None, size, self.palette["accent"] if target else panels.FAINT))
            label = kit.label("ここに入る" if target else "空き", panels.META,
                              self.palette["accent"] if target else panels.FAINT)
            label.pos_hint = {"center_y": 0.5}
            cell.add_widget(label)
            return cell
        if record.get("selected"):
            with cell.canvas.before:
                Color(*SELECTED)
                rect = Rectangle(pos=cell.pos, size=cell.size)
            cell.bind(pos=lambda w, v: setattr(rect, "pos", v), size=lambda w, v: setattr(rect, "size", v))
        illegal = record.get("illegal")
        cell.add_widget(self.icon(record.get("image"), size, self.palette["illegal"] if illegal else panels.LINE))
        words = BoxLayout(orientation="vertical", spacing=kit.px(2))
        words.add_widget(kit.label(record["name"], panels.META, self.palette["illegal"] if illegal else panels.TEXT))
        words.add_widget(kit.label(_pieces_markup(record["pieces"]), panels.TICK, use_markup=True))
        cell.add_widget(words)
        cell.bind(on_release=lambda *_a: self.on_select("slot", (part, slot)))
        return cell

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
        grid = GridLayout(cols=5, size_hint_y=None, spacing=kit.px(8))
        grid.bind(minimum_height=grid.setter("height"))
        for row in model["candidates"]:
            grid.add_widget(self.candidate(row))
        if not model["candidates"]:
            box.add_widget(kit.label(model.get("candidate_empty") or "", panels.META, panels.DIM))
        box.add_widget(kit.scroller(grid))
        if model.get("candidate_note"):
            box.add_widget(kit.label(model["candidate_note"], panels.TICK, panels.FAINT))
        return box

    def candidate(self, row):
        kit = self.kit
        size = kit.px(60)
        cell = _classes()["PressBox"](orientation="vertical", size_hint_y=None,
                                      height=size + kit.base * panels.TICK * 1.6, spacing=kit.px(2))
        chosen = row.get("selected")
        color = self.palette["accent"] if chosen else panels.LINE
        icon = self.icon(row.get("image"), size, color)
        icon.pos_hint = {"center_x": 0.5}
        cell.add_widget(icon)
        text_color = self.palette["accent"] if chosen else panels.DIM
        cell.add_widget(kit.label(row["label"], panels.TICK, text_color, halign="center"))
        cell.bind(on_release=lambda *_a: self.on_select("item", row["key"]))
        return cell

    def detail(self, model):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.widget import Widget
        kit = self.kit
        box = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(12), spacing=kit.px(6)))
        box.size_hint_x = 0.44
        data = model.get("detail")
        if not data:
            box.add_widget(kit.label("改造かアイテムを選ぶと、ここに詳しく出る。", panels.META, panels.DIM))
            box.add_widget(Widget())
            return box
        top = BoxLayout(orientation="horizontal", spacing=kit.px(12), size_hint_y=None, height=kit.px(88))
        top.add_widget(self.icon(data.get("image"), kit.px(84),
                                 self.palette["illegal"] if data.get("illegal") else panels.LINE))
        words = BoxLayout(orientation="vertical", spacing=kit.px(2))
        name = data["name"]
        pieces = [(name, self.palette["illegal"] if data.get("illegal") else panels.TEXT)]
        if data.get("illegal"):
            pieces.append(("　[{}]".format(model["words"]["illegal"]), self.palette["illegal"]))
        words.add_widget(kit.label(panels.markup(pieces), panels.NAME, use_markup=True))
        words.add_widget(kit.label("　".join(s for s in data.get("sub") or [] if s), panels.META, panels.DIM))
        words.add_widget(kit.label(data.get("description") or "", panels.META))
        top.add_widget(words)
        box.add_widget(top)
        body = kit.column(spacing=4)
        for label, row in data.get("rows") or []:
            if not row:
                continue
            body.add_widget(kit.label("[color={}]{}[/color]　{}".format(
                _hex(panels.DIM), label, _pieces_markup(row)), panels.META, use_markup=True))
        for text, kind in data.get("notes") or []:
            body.add_widget(kit.label(text, panels.TICK, MINUS if kind == rules.MINUS else (
                PLUS if kind == rules.PLUS else panels.DIM)))
        box.add_widget(kit.scroller(body))
        button = data.get("button")
        if button:
            press = kit._font(Button(text=button["text"], size_hint=(None, None),
                                     size=(kit.px(260), kit.base * 2.2), font_size=kit.base,
                                     background_color=(0, 0, 0, 0),
                                     color=panels.TEXT if button.get("enabled") else panels.FAINT))
            press.disabled = not button.get("enabled")
            press.bind(on_release=lambda *_a: self.on_action(button["action"], button["payload"]))
            box.add_widget(kit.framed(press, color=panels.LINE if button.get("enabled") else panels.FAINT,
                                      fill=(0.04, 0.04, 0.05, 1)))
        return box

    def totals(self, model):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.gridlayout import GridLayout
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
                name, _hex(PLUS if kind == rules.PLUS else MINUS), value), panels.BODY, use_markup=True))
        content.add_widget(grid)
        if battle:
            content.add_widget(kit.label("戦闘", panels.META, panels.DIM))
            lines = GridLayout(cols=2, size_hint_y=None, spacing=kit.px(4))
            lines.bind(minimum_height=lines.setter("height"))
            for text, kind in battle:
                lines.add_widget(kit.label(text, panels.META, PLUS if kind == rules.PLUS else MINUS))
            content.add_widget(lines)
        box.add_widget(kit.scroller(content))
        return box


def Figure(parts, palette):
    """人体の図（部位ごとに塗り分け、違法の部位は縁を違法の色でなぞる）。"""
    from kivy.graphics import Color, Ellipse, Line, Mesh
    from kivy.uix.widget import Widget

    widget = Widget()

    def style(part):
        data = parts.get(part) or {}
        if data.get("hurt"):
            return palette["hurt_fill"], palette["hurt"]
        if data.get("augmented"):
            return palette["fill"], palette["accent"]
        return PLAIN_FILL, PLAIN_LINE

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

        def polygon(points, colors, mirror=False, edge=None):
            fill, line = colors
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
                Line(points=coords, close=True, width=1.2)
                if edge is not None:
                    Color(*palette["illegal"])
                    Line(points=flat(edge, sign), width=2.0)

        def oval(spec, colors, mirror=False, width=1.2):
            ox, oy, rx, ry = spec
            fill, line = colors
            for sign in ((1, -1) if mirror else (1,)):
                x, y = at(sign * ox, oy)
                Color(*fill)
                Ellipse(pos=(x - rx * scale, y - ry * scale), size=(rx * 2 * scale, ry * 2 * scale))
                Color(*line)
                Line(ellipse=(x - rx * scale, y - ry * scale, rx * 2 * scale, ry * 2 * scale), width=width)

        def illegal(part):
            return (parts.get(part) or {}).get("illegal")

        with widget.canvas:
            backdrop(at, scale)
            oval(HAND, style("arms"), mirror=True)
            polygon(ARM, style("arms"), mirror=True, edge=ARM[1:4] if illegal("arms") else None)
            polygon(FOOT, style("legs"), mirror=True)
            polygon(LEG, style("legs"), mirror=True, edge=LEG[1:5] if illegal("legs") else None)
            polygon(TORSO, style("body"), edge=TORSO[1:5] if illegal("body") else None)
            if illegal("body"):
                Color(*palette["illegal"])
                Line(points=flat([(-x, y) for x, y in TORSO[1:5]]), width=2.0)
            polygon(NECK, style("head"))
            oval(HEAD, style("head"))
            if illegal("head"):
                x, y = at(0, HEAD[1])
                Color(*palette["illegal"])
                # Kivy の弧は真上が 0 度で時計回り。頭の上側をなぞる
                Line(circle=(x, y, HEAD[3] * scale, -70, 70), width=2.0)
            # 内臓は輪の縁の色で示す。入れ先に選んでいる間は強調の色、違法の改造があれば違法の色
            organ_fill, organ_line = style("organs")
            organ_width = 1.2
            if (parts.get("organs") or {}).get("target"):
                organ_line = palette["accent"]
            elif illegal("organs"):
                organ_line, organ_width = palette["illegal"], 2.0
            oval(ORGANS, (organ_fill if (parts.get("organs") or {}).get("augmented") else (0.08, 0.08, 0.09, 1),
                          organ_line), width=organ_width)
            _fill, sanity_line = style("sanity")
            if illegal("sanity"):
                sanity_line = palette["illegal"]
            x, y = at(SANITY[0], SANITY[1])
            radius = SANITY[2] * scale
            Color(*sanity_line)
            for start in range(-65, 66, 14):
                Line(circle=(x, y, radius, start, start + (14 if (parts.get("sanity") or {}).get("augmented")
                                                            else 7)), width=1.4)
            # 内臓は輪の縁を強調の色にするだけ（印の輪を重ねると内臓の輪とずれて見える）
            for part, (px, py) in (("head", (-28, 38)), ("arms", (-70, 170)), ("legs", (-36, 345)),
                                   ("sanity", (42, 2)), ("body", (36, 118))):
                if (parts.get(part) or {}).get("target"):
                    tx, ty = at(px, py)
                    Color(*palette["accent"])
                    Line(circle=(tx, ty, 9 * scale + 4), width=2.0)

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
