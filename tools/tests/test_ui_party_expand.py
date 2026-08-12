# -*- coding: utf-8 -*-
"""116_ui_party_expand をゲーム抜きで通す。

    python tools/tests/test_ui_party_expand.py

偽の `scripts.hud.new_hud` / `InstanTaleHUD` / Kivy（Button・Clock・Window・App）を
差し込んで、次を確認する。

  ボタン   … 4人目が居るときだけ出る。塗り直しのたびに増えない
  子の並び … **HUD 自身の子は増やさない**（ゲームの「画面の最初の子」を変えない）
  枠を足す … 押すと足りないぶんの枠が複製され、`party_cells` に追記される
  中身     … 複製した枠に4人目の立ち絵と名前が入る（対応は埋まった枠から学ぶ）
  ゲーム側 … `party_cells` をなめて塗るビルドでは、塗るのをゲームに任せる
  押下     … 複製した枠を押すと `on_member_label_press` にその添字が渡る
  伸び方   … 帯は下端を動かさず上へ伸び、1人ぶんの高さは変わらない
  不動     … **元から在る枠は1 px も動かない**（比率で割り直すとずれる）
  並び     … 足した枠は元の枠の上に、行の送り幅ぶんずつ積まれる
  枠線     … 足した枠にゲーム自身の `add_border` で枠線が付く（無ければ自前）
  見え方   … 幅の違う立ち絵も、元の枠と同じ位置・同じ大きさに収まる
  頭打ち   … 窓に入らない行数までは伸ばさない
  隠す     … 伸びた帯に重なるボタンは見えなくなり押せなくなる。戻すと元に戻る
  自分     … こちらのボタンは隠さない（隠すと戻す手段が無くなる）
  他人     … **ゲームの選択肢ボタンには触らない**（重なっていても）
  目隠し   … 新しく覆った場所は黒い板で塞ぐ（透けない・押しても抜けない）
  救済     … 隠しっぱなしの相手は、注入し直すと戻る
  増減     … 仲間が増えた・減った・入れ替わったら、その場で枠と中身が追う
  雇い直し … 別れた相手を雇い直しても、ゲーム自身の塗りが落ちない
  復元     … もう一度押すと寸法・枠・`party_cells` が元に戻る
  当て直し … ゲームが帯を組み直しても、広げたままの状態が次の塗りで戻る
  再注入   … MOD 側の控えが作り直されても、広げた後の寸法を設計値と取り違えない
  窓       … 窓の大きさを変えても、控えが新しい寸法に付いてくる
  無傷     … パーティ欄が無いビルドでは何もしない

寸法は実測（`instantale.exe` の定数表）に合わせてある: 窓 2560x1400、
`bottom_info_layout` は size_hint=(0.2, 0.88) の 512x431.2 @ (1996.8, 49)、
`party_cells` は 3 枠で size_hint=(1, 0.33)。
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD = find_mod("_ui_party_expand")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 実測値
WIN_WIDTH, WIN_HEIGHT = 2560.0, 1400.0
PANEL_SIZE = (512.0, 431.2)
PANEL_POS = (1996.8, 49.0)
BASE_ROWS = 3
CELL_HINT_Y = 0.33          # ゲームの実測値（1/3 ではない）
GAME_FONT = "fonts/NotoSansJP.ttf"

ROSTER = [
    ("player", "自分", "portraits/player.png"),
    ("7", "鉄屑のレオン", "portraits/leon.png"),
    ("12", "薬売りのミナ", "portraits/mina.png"),
    ("20", "見習いのカイ", "portraits/kai.png"),
    ("36", "重装のハンス", "portraits/hans.png"),
]
DETAIL = dict((member_id, {"name": name, "image_src": image})
              for member_id, name, image in ROSTER)


# ---------------------------------------------------------------- 偽 Kivy
class FakeWindow(object):
    width = WIN_WIDTH
    height = WIN_HEIGHT
    handlers = []

    @classmethod
    def bind(cls, **kwargs):
        for _event, callback in kwargs.items():
            cls.handlers.append(callback)

    @classmethod
    def unbind(cls, **kwargs):
        for _event, callback in kwargs.items():
            if callback in cls.handlers:
                cls.handlers.remove(callback)

    @classmethod
    def resize(cls, width, height):
        cls.width, cls.height = width, height
        for callback in list(cls.handlers):
            callback(cls, width, height)


class FakeWidget(object):
    """`size` / `pos` が `width` などと繋がっているところだけ本物に似せる。"""

    def __init__(self):
        self.size_hint = (1, 1)
        self.pos_hint = {}
        self.width = 0.0
        self.height = 0.0
        self.x = 0.0
        self.y = 0.0
        self.opacity = 1.0
        self.parent = None
        self.children = []

    @property
    def size(self):
        return (self.width, self.height)

    @property
    def pos(self):
        return (self.x, self.y)

    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def remove_widget(self, widget):
        if widget in self.children:
            self.children.remove(widget)
        if widget.parent is self:
            widget.parent = None


# 立ち絵ごとの幅（絵の縦横比で決まる）。絵を差し替えると幅が変わり、複製した枠
# では `StencilFloatLayout` の切り取りが効かないので枠の外へはみ出す。
PORTRAIT_WIDTH = {"portraits/player.png": 67.0, "portraits/leon.png": 67.0,
                  "portraits/mina.png": 67.0, "portraits/kai.png": 125.0}


class FakeImage(FakeWidget):
    """立ち絵。`source` を入れると、その絵の縦横比で幅が変わる（本物と同じ）。"""

    def __init__(self, source=""):
        FakeWidget.__init__(self)
        self.allow_stretch = True
        self.keep_ratio = True
        self._source = ""
        self.source = source

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value):
        self._source = value
        if value in PORTRAIT_WIDTH:
            self.width = PORTRAIT_WIDTH[value]


class FakeCellLabel(FakeWidget):
    def __init__(self, text=""):
        FakeWidget.__init__(self)
        self.text = text
        self.font_name = GAME_FONT


class FakeCell(FakeWidget):
    """パーティの枠（`ClickableFloatLayout`）。`member_id` と押下先を持つ。"""

    def __init__(self, row=0, absolute=False):
        FakeWidget.__init__(self)
        self.member_id = None
        self.callback = None
        self.disabled = False
        self.canvas = FakeCanvas()
        self.bordered = None
        self.bound = []
        self.size_hint = (1, CELL_HINT_Y)
        self.width = PANEL_SIZE[0]
        self.height = PANEL_SIZE[1] * CELL_HINT_Y
        self.x = PANEL_POS[0]
        self.y = PANEL_POS[1] + (BASE_ROWS - 1 - row) * self.height
        if absolute:
            self.size_hint = (None, None)
            self.pos_hint = {}
        else:
            self.pos_hint = {"center_x": 0.5,
                             "y": (BASE_ROWS - 1 - row) * CELL_HINT_Y}
        stencil = FakeWidget()
        stencil.size_hint = (None, None)
        stencil.width, stencil.height = 67.0, self.height - 4.0
        stencil.x, stencil.y = self.x + 2.0, self.y + 2.0
        label = FakeCellLabel()
        image = FakeImage()
        image.size_hint = (None, None)
        image.height = stencil.height
        image.width = stencil.width
        image.x, image.y = stencil.x, stencil.y
        stencil.add_widget(label)
        stencil.add_widget(image)
        self.add_widget(stencil)

    def bind(self, **kwargs):
        for _event, callback in kwargs.items():
            self.bound.append(callback)

    def image(self):
        return next(c for c in self.children[0].children
                    if isinstance(c, FakeImage))

    def label(self):
        return next(c for c in self.children[0].children
                    if isinstance(c, FakeCellLabel))

    def press(self):
        if self.callback is not None:
            self.callback(self)


class FakeCanvasGroup(object):
    def __init__(self):
        self.instructions = []

    def clear(self):
        self.instructions = []

    def add(self, instruction):
        self.instructions.append(instruction)

    def lines(self):
        return [i for i in self.instructions if isinstance(i, FakeLine)]

    def colors(self):
        return [i for i in self.instructions if isinstance(i, FakeColor)]


class FakeCanvas(object):
    def __init__(self):
        self.after = FakeCanvasGroup()
        self.before = FakeCanvasGroup()


class FakeRectangle(object):
    def __init__(self, pos=(0.0, 0.0), size=(0.0, 0.0)):
        self.pos = tuple(pos)
        self.size = tuple(size)


class FakeBlocker(FakeWidget):
    """黒い板（`kivy.uix.widget.Widget`）。触れられたら触りを止める側。"""

    def __init__(self, size_hint=None, **_kwargs):
        FakeWidget.__init__(self)
        self.size_hint = size_hint or (1, 1)
        self.disabled = False
        self.canvas = FakeCanvas()

    def rect(self):
        for instruction in self.canvas.before.instructions:
            if isinstance(instruction, FakeRectangle):
                return instruction
        return None


class FakeColor(object):
    def __init__(self, *rgba):
        self.rgba = rgba


class FakeLine(object):
    def __init__(self, points=None, width=1.0, **_kwargs):
        self.points = list(points or [])
        self.width = width

    def ys(self):
        return self.points[1::2]


class FakeButton(FakeWidget):
    def __init__(self, text="", size_hint=None, size=None, pos_hint=None):
        FakeWidget.__init__(self)
        self.canvas = FakeCanvas()
        self.background_normal = "atlas://data/images/defaulttheme/button"
        self.background_color = (1, 1, 1, 1)
        self.disabled = False
        self.text = text
        self.size_hint = size_hint or (1, 1)
        self.width, self.height = size or (0.0, 0.0)
        self.pos_hint = dict(pos_hint or {})
        self.font_name = None
        self.font_size = 0
        self.bound = []

    def bind(self, **kwargs):
        for _event, callback in kwargs.items():
            self.bound.append(callback)

    def unbind(self, **kwargs):
        for _event, callback in kwargs.items():
            if callback in self.bound:
                self.bound.remove(callback)

    def press(self):
        for callback in list(self.bound):
            callback(self)


class FakeLabel(FakeWidget):
    """本文のラベル（ボタンのフォントを写す相手）。"""

    def __init__(self):
        FakeWidget.__init__(self)
        self.text = ""
        self.font_name = GAME_FONT


class FakeApp(object):
    """`__main__.InstantaleApp`。押下の行き先と、名簿の増減だけ本物に似せる。"""

    def __init__(self, hud):
        self.hud = hud
        self.pressed = []

    def on_member_label_press(self, label_index):
        self.pressed.append(("index", label_index,
                             self.hud.party_cells[label_index].member_id))

    def process_party_member_choice(self, character_id):
        self.pressed.append(("id", character_id, character_id))

    # 名簿が動く2箇所。**画面（`party_members`）はこの時点ではまだ古い** ―
    # 本物は `update_party_member` が 0.1 秒ごとに入れ直す。
    def add_party_member(self, character_id):
        self.hud.roster.append(character_id)

    def remove_party_member(self, member_id):
        if member_id in self.hud.roster:
            self.hud.roster.remove(member_id)


RUNNING = {"app": None}


class FakeHUD(FakeWidget):
    """本物の `InstanTaleHUD` のうち、この mod から見える部分だけ。

    `paints` でゲーム側の塗り方を変える:
      "three" … `range(0, 3)` で塗る（実機。足した枠は空のまま）
      "cells" … `party_cells` をなめて塗る（足した枠もゲームが塗る）
    """

    def __init__(self, members=4, paints="three", absolute=False,
                 with_panel=True, with_stray=True):
        FakeWidget.__init__(self)
        self.display_text = ""
        self.button_texts = []
        self.paints = paints
        self.text_display = FakeLabel()
        self.root = FakeWidget()
        self.root.size_hint = (None, None)
        self.root.width, self.root.height = WIN_WIDTH, WIN_HEIGHT
        self.root.parent = self
        self.children = [self.root]
        self.party_members = {}
        self.roster = []
        if with_panel:
            self.panel = FakeWidget()
            self.panel.size_hint = (0.2, 0.88)
            self.panel.width, self.panel.height = PANEL_SIZE
            self.panel.x, self.panel.y = PANEL_POS
            self.panel.parent = self.root
            self.root.children.append(self.panel)
            self.party_cells = [FakeCell(row, absolute) for row in range(BASE_ROWS)]
            for index, cell in enumerate(self.party_cells):
                # 本物と同じく `add_widget` で足す（`children` は足した順の逆に
                # 並ぶ）。ゲームはこの並びを逆順にたどって塗る。
                self.panel.add_widget(cell)
                # ゲームの `set_party_member_callback(member_index, callback)`。
                # 押下先はこの属性に入っている。
                cell.callback = (lambda _instance=None, _index=index:
                                 RUNNING["app"].on_member_label_press(_index))
        else:
            self.panel = None
            self.party_cells = []
        # ゲーム自身の選択肢ボタン。**入れ物の中**に居る（HUD 直下ではない）。
        # 帯が伸びると重なる位置に置いてある ― それでも触ってはいけない相手。
        self.right_button_layout = FakeWidget()
        self.right_button_layout.size_hint = (None, None)
        self.right_button_layout.width, self.right_button_layout.height = 512.0, 400.0
        self.right_button_layout.x, self.right_button_layout.y = PANEL_POS[0], 500.0
        self.right_button_layout.parent = self.root
        self.root.children.append(self.right_button_layout)
        self.right_buttons = []
        for index in range(4):
            choice = FakeButton(text="選択肢", size=(500.0, 90.0))
            choice.x = PANEL_POS[0] + 6.0
            choice.y = 510.0 + index * 95.0
            self.right_button_layout.add_widget(choice)
            self.right_buttons.append(choice)
        # 帯のすぐ上に居る他人のボタン（`113_ui_text_expand` の切り替えボタン）。
        if with_stray:
            self.stray = FakeButton(text="", size=(51.0, 51.0))
            self.stray.x = PANEL_POS[0]
            self.stray.y = PANEL_POS[1] + PANEL_SIZE[1] + 60.0
            self.stray.parent = self.root
            self.root.children.append(self.stray)
        else:
            self.stray = None
        # 窓が変わるとゲームは帯も枠も組み直す（どちらも比率で置かれている）。
        self.base = [(widget, widget.pos + widget.size)
                     for widget in ([self.panel] if self.panel else [])
                     + list(self.party_cells)]
        self.set_party(members)
        RUNNING["app"] = FakeApp(self)

    # -- ゲーム側の仕組み ---------------------------------------------------
    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def screen_root(self):
        return self.children[0] if self.children else None

    def set_party(self, count):
        self.roster = [member_id for member_id, _name, _image in ROSTER[:count]]
        self.tick_party()

    def tick_party(self):
        """`InstantaleApp.update_party_member`（0.1 秒ごと）。名簿を画面へ写す。"""
        self.party_members = dict(
            (member_id, dict(DETAIL[member_id])) for member_id in self.roster)
        self.update_party_display(self, self.party_members)

    def paint_cell(self, cell, member_id):
        # ゲームは枠の中身を直に触る。**枠でないものが混じるとここで落ちる**
        # （実機のクラッシュはこの形。GAME.md §2.8）。
        image, label = cell.image(), cell.label()
        cell.member_id = member_id
        data = self.party_members.get(member_id)
        if data is None:
            return
        image.source = data["image_src"]
        label.text = data["name"]

    def update_party_display(self, *_args):
        ids = list(self.party_members)
        if self.paints == "cells":
            # 実機はパーティ欄の子を上から順に塗る（クラッシュ時の locals より、
            # `cells` は 6 件で先頭がこちらの敷いた板だった）。
            cells = list(reversed(self.panel.children)) if self.panel else []
        else:
            cells = self.party_cells[:BASE_ROWS]
        for index, cell in enumerate(cells):
            self.paint_cell(cell, ids[index] if index < len(ids) else None)

    def update_display_text(self, instance, value):
        self.text_display.text = value

    def update_button_texts(self, instance, value):
        self.button_texts = list(value or [])

    def relayout(self):
        """窓が変わったときにゲームが画面を組み直す。"""
        scale_x = FakeWindow.width / WIN_WIDTH
        scale_y = FakeWindow.height / WIN_HEIGHT
        for widget, (x, y, width, height) in self.base:
            widget.x, widget.y = x * scale_x, y * scale_y
            widget.width, widget.height = width * scale_x, height * scale_y

    # -- 画面が塗られる ------------------------------------------------------
    def show(self, text="薄暗い部屋に、腐敗した薬草の臭いが漂う。"):
        self.display_text = text
        self.update_display_text(self, text)

    def toggle_button(self):
        for child in self.root.children + self.children:
            if isinstance(child, FakeButton) and child is not self.stray:
                return child
        return None

    def extras(self):
        if self.panel is None:
            return []
        return [c for c in self.panel.children
                if isinstance(c, FakeCell) and c not in self.party_cells[:BASE_ROWS]]


PRISTINE = (FakeHUD.update_party_display, FakeHUD.update_display_text,
            FakeHUD.update_button_texts)
PRISTINE_APP = (FakeApp.add_party_member, FakeApp.remove_party_member)


class FakeClock(object):
    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, timeout=0):
        self.pending.append(callback)

    def tick(self):
        pending, self.pending = self.pending, []
        for callback in pending:
            callback(0)


CLOCK = FakeClock()


class FakeAppClass(object):
    @staticmethod
    def get_running_app():
        return RUNNING["app"]


def install_fake_kivy():
    """mod は kivy を関数の中で遅延 import する。sys.modules に偽物を置く。"""
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = CLOCK
    window_mod = types.ModuleType("kivy.core.window")
    window_mod.Window = FakeWindow
    button_mod = types.ModuleType("kivy.uix.button")
    button_mod.Button = FakeButton
    graphics_mod = types.ModuleType("kivy.graphics")
    graphics_mod.Color = FakeColor
    graphics_mod.Line = FakeLine
    graphics_mod.Rectangle = FakeRectangle
    widget_mod = types.ModuleType("kivy.uix.widget")
    widget_mod.Widget = FakeBlocker
    app_mod = types.ModuleType("kivy.app")
    app_mod.App = FakeAppClass
    for name, module in (("kivy", types.ModuleType("kivy")),
                         ("kivy.clock", clock_mod),
                         ("kivy.core", types.ModuleType("kivy.core")),
                         ("kivy.core.window", window_mod),
                         ("kivy.uix", types.ModuleType("kivy.uix")),
                         ("kivy.uix.button", button_mod),
                         ("kivy.uix.widget", widget_mod),
                         ("kivy.graphics", graphics_mod),
                         ("kivy.app", app_mod)):
        sys.modules[name] = module


# ---------------------------------------------------------------- 偽ローダ
class FakeCtx(object):
    """`ctx.wrap` だけ本物と同じ形にする（第1引数 orig、第2引数 self）。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.wrapped = {}
        self.errors = []
        self.warnings = []

    def wrap(self, target, **kw):
        def decorate(func):
            module_name, qualname = target.split(":")
            module = sys.modules[module_name]
            cls_name, method = qualname.split(".")
            cls = getattr(module, cls_name)
            original = getattr(cls, method)

            def wrapper(self_, *args, **kwargs):
                return func(original, self_, *args, **kwargs)

            setattr(cls, method, wrapper)
            self.wrapped[target] = wrapper
            return func
        return decorate

    def patch(self, target, **kw):
        raise AssertionError("this mod should not use ctx.patch")

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        if level == "WARN":
            self.warnings.append(msg)

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_ui_party_expand", MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install(mod, ctx):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。"""
    (FakeHUD.update_party_display, FakeHUD.update_display_text,
     FakeHUD.update_button_texts) = PRISTINE
    FakeApp.add_party_member, FakeApp.remove_party_member = PRISTINE_APP
    mod.apply(ctx)


def close(value, expected, tolerance=0.01):
    return abs(value - expected) < tolerance


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    module.upx = lambda value: value        # 拡縮しないビルドとして扱う

    def add_border(widget):
        """ゲーム自身の枠線（`scripts.hud.new_hud:add_border`）。"""
        widget.bordered = True

    module.add_border = add_border
    sys.modules["scripts.hud.new_hud"] = module

    # 名簿が動く2箇所は `__main__.InstantaleApp` に居る（GAME.md §2.8）。
    sys.modules["__main__"].InstantaleApp = FakeApp

    ctx = FakeCtx(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "party_expand"))
    mod = load_mod()
    install(mod, ctx)
    for target in ("update_party_display", "update_display_text", "update_button_texts"):
        check("hooked " + target,
              "scripts.hud.new_hud:InstanTaleHUD." + target in ctx.wrapped)
    for target in ("add_party_member", "remove_party_member"):
        check("hooked " + target,
              "__main__:InstantaleApp." + target in ctx.wrapped)

    # -- 仲間が3人までのときはボタンを出さない ------------------------------
    hud = FakeHUD(members=3)
    hud.show()
    quiet = hud.toggle_button()
    check("with room to spare the button is hidden and cannot be pressed",
          quiet is not None and quiet.opacity == 0 and quiet.disabled is True,
          quiet and (quiet.opacity, quiet.disabled))
    check("a party that fits leaves the panel exactly as the game built it",
          hud.panel.size == PANEL_SIZE and len(hud.party_cells) == BASE_ROWS,
          (hud.panel.size, len(hud.party_cells)))

    # -- 4人目が居るとボタンが出る -------------------------------------------
    hud = FakeHUD(members=4)
    hud.show()
    button = hud.toggle_button()
    check("a fourth member brings the button out",
          button is not None and button.opacity == 1 and button.disabled is False,
          button and (button.opacity, button.disabled))
    for _ in range(5):
        hud.show()
        hud.update_button_texts(hud, ["会話する", "出る"])
    check("repainting does not add a second button",
          sum(1 for c in hud.root.children if isinstance(c, FakeButton)
              and c is not hud.stray) == 1,
          [type(c).__name__ for c in hud.root.children])
    # 素の HUD の子は FloatLayout 1枚だけ。そこへ足すと「画面の最初の子」を取る
    # 側から見える相手が変わり、アイテムの移動・装備が壊れる（`113_` の実例）。
    check("the HUD's own child list is left exactly as the game built it",
          hud.children == [hud.root] and hud.screen_root() is hud.root,
          [type(c).__name__ for c in hud.children])
    check("the button sits just above the party panel, right aligned",
          close(button.x + button.width, PANEL_POS[0] + PANEL_SIZE[0])
          and close(button.y, PANEL_POS[1] + PANEL_SIZE[1] + mod.PANEL_GAP),
          (button.pos, hud.panel.pos, hud.panel.size))
    check("the button borrows the game's font", button.font_name == GAME_FONT,
          button.font_name)

    def icon_apex(widget):
        lines = widget.canvas.after.lines()
        if not lines:
            return None
        ys = lines[0].ys()
        return ys[len(ys) // 2] - (ys[0] + ys[-1]) / 2.0

    check("the collapsed icon points up", (icon_apex(button) or 0) > 0,
          icon_apex(button))

    # -- 押すと枠が増える ----------------------------------------------------
    ROW_PITCH = PANEL_SIZE[1] * CELL_HINT_Y      # 隣り合う枠の y の差
    was = [(cell.pos, cell.size) for cell in hud.party_cells]
    top_image = hud.party_cells[0].image()
    top_box = (top_image.pos, top_image.size)
    button.press()
    extras = hud.extras()
    check("pressing adds exactly the missing slot", len(extras) == 1,
          [type(c).__name__ for c in hud.panel.children])
    check("the copy is the same kind of widget as the game's own cell",
          extras and type(extras[0]) is type(hud.party_cells[0]),
          extras and type(extras[0]).__name__)
    check("the copy is appended to party_cells (so presses route by index)",
          hud.party_cells[BASE_ROWS:] == extras, len(hud.party_cells))
    check("the panel grew by exactly one row",
          close(hud.panel.height, PANEL_SIZE[1] + ROW_PITCH), hud.panel.size)
    check("the panel grew upwards only (its bottom edge did not move)",
          hud.panel.pos == PANEL_POS, hud.panel.pos)
    check("the panel width is left to the game",
          hud.panel.size_hint[0] == 0.2 and close(hud.panel.width, PANEL_SIZE[0]),
          (hud.panel.size_hint, hud.panel.width))
    # 比率で割り直すと 0.33 と 1/4 の差で元の枠が数 px 動く（実機で確認）。
    check("not one of the game's own rows moved by a single pixel",
          [(cell.pos, cell.size) for cell in hud.party_cells[:BASE_ROWS]] == was,
          (was, [(c.pos, c.size) for c in hud.party_cells[:BASE_ROWS]]))
    check("the added row sits one pitch above the topmost row",
          extras and close(extras[0].x, was[0][0][0])
          and close(extras[0].y, was[0][0][1] + ROW_PITCH)
          and extras[0].size == was[0][1],
          extras and (extras[0].pos, extras[0].size)),
    check("the added row is given the game's own border",
          extras and extras[0].bordered is True,
          extras and extras[0].bordered)
    check("a wider portrait is aligned to the same box as the game's rows",
          extras and extras[0].image().size == top_box[1]
          and close(extras[0].image().x, top_box[0][0])
          and close(extras[0].image().y, top_box[0][1] + ROW_PITCH),
          extras and (extras[0].image().pos, extras[0].image().size, top_box))
    check("the fourth member's portrait is in the added row",
          extras and extras[0].image().source == ROSTER[3][2],
          extras and extras[0].image().source)
    check("the fourth member's name is in the added row",
          extras and extras[0].label().text == ROSTER[3][1],
          extras and extras[0].label().text)
    check("the added row knows whose it is",
          extras and extras[0].member_id == ROSTER[3][0],
          extras and extras[0].member_id)
    check("pressing flips the icon over", (icon_apex(button) or 0) < 0,
          icon_apex(button))

    # -- 足した枠を押すとゲーム自身の経路に流れる ----------------------------
    RUNNING["app"].pressed = []
    extras[0].press()
    check("pressing an added row goes through on_member_label_press with its index",
          RUNNING["app"].pressed == [("index", BASE_ROWS, ROSTER[3][0])],
          RUNNING["app"].pressed)

    # -- 帯に重なるボタンは隠れて押せない ------------------------------------
    check("a button the grown panel covers is hidden",
          hud.stray.opacity == 0 and hud.stray.disabled is True,
          (hud.stray.opacity, hud.stray.disabled))
    check("this mod's own button stays visible and pressable",
          button.opacity == 1 and button.disabled is False,
          (button.opacity, button.disabled))
    # 木を降りて `disabled` を持つものを全部拾う作りだと、ゲームの選択肢まで
    # 掴んで、畳んだ後も押せなくなる（GAME.md §2.8）。
    check("the game's own choice buttons are never touched, even when covered",
          all(c.opacity == 1 and c.disabled is False for c in hud.right_buttons),
          [(c.opacity, c.disabled) for c in hud.right_buttons])
    # 足した枠は背景を持たないので、下のゲームの選択肢が透けて見え、押せてしまう
    # （下の「会話する」の文字が枠に重なって出る）。黒い板1枚で塞ぐ。
    blockers = [c for c in hud.root.children if isinstance(c, FakeBlocker)]
    blocker = blockers[0] if blockers else None
    gained = (PANEL_POS[0], PANEL_POS[1] + PANEL_SIZE[1], PANEL_SIZE[0], ROW_PITCH)
    check("the newly covered area is filled with a blocker",
          blocker is not None and blocker.pos == gained[:2]
          and blocker.size == gained[2:],
          blocker and (blocker.pos, blocker.size, gained))
    check("the blocker is painted, so nothing shows through it",
          blocker is not None and blocker.rect() is not None
          and blocker.rect().size == gained[2:],
          blocker and [type(i).__name__ for i in blocker.canvas.before.instructions])
    check("the blocker swallows touches (it is disabled)",
          blocker is not None and blocker.disabled is True,
          blocker and blocker.disabled)
    # **帯の中に入れてはいけない。** ゲームは帯の子を1つずつ「パーティの枠」と
    # して塗るので、枠の形をしていない板が混じるとそこで落ちる（GAME.md §2.8）。
    check("the blocker is not a child of the party panel",
          not [c for c in hud.panel.children if isinstance(c, FakeBlocker)],
          [type(c).__name__ for c in hud.panel.children])
    # 帯のすぐ後ろ＝描画は帯より下・触りの判定は帯より後。枠の押下は枠に届く。
    check("the blocker sits directly behind the panel",
          blocker is not None
          and hud.root.children.index(blocker)
          == hud.root.children.index(hud.panel) + 1,
          [type(c).__name__ for c in hud.root.children])
    check("the panel and its rows are not hidden by that sweep",
          hud.panel.opacity == 1 and all(c.opacity == 1 for c in hud.party_cells),
          (hud.panel.opacity, [c.opacity for c in hud.party_cells]))

    # -- ゲームが帯を組み直しても広げたまま ----------------------------------
    hud.panel.height = PANEL_SIZE[1]
    hud.show()
    check("a panel the game reset is expanded again on the next paint",
          close(hud.panel.height, PANEL_SIZE[1] + ROW_PITCH), hud.panel.size)
    check("that does not pile up more copies", len(hud.extras()) == 1,
          len(hud.extras()))

    # -- もう一度押すと元に戻る ----------------------------------------------
    button.press()
    check("pressing again restores the panel size", hud.panel.size == PANEL_SIZE,
          hud.panel.size)
    check("pressing again removes the copies", hud.extras() == [], hud.extras())
    check("pressing again removes the blocker",
          not [c for c in hud.root.children if isinstance(c, FakeBlocker)],
          [type(c).__name__ for c in hud.root.children])
    check("pressing again puts party_cells back to three",
          len(hud.party_cells) == BASE_ROWS, len(hud.party_cells))
    check("pressing again restores the rows' own size_hint and pos_hint",
          all(close(cell.size_hint[1], CELL_HINT_Y) for cell in hud.party_cells)
          and [close(cell.pos_hint["y"], y) for cell, y in
               zip(hud.party_cells, (2 * CELL_HINT_Y, CELL_HINT_Y, 0.0))] == [True] * 3,
          [(c.size_hint, c.pos_hint) for c in hud.party_cells])
    check("pressing again brings the covered button back",
          hud.stray.opacity == 1 and hud.stray.disabled is False,
          (hud.stray.opacity, hud.stray.disabled))
    check("pressing again flips the icon back", (icon_apex(button) or 0) > 0,
          icon_apex(button))
    hud.show()
    check("a restored panel is left alone by later paints",
          hud.panel.size == PANEL_SIZE and hud.extras() == [], hud.panel.size)

    # -- 注入し直しても、広げた後の寸法を設計値と取り違えない ----------------
    button.press()
    grown = hud.panel.size
    install(mod, ctx)                  # MOD 側の控えは作り直される
    hud.show()
    check("re-injecting keeps the expanded size (it does not grow again)",
          hud.panel.size == grown and len(hud.extras()) == 1,
          (grown, hud.panel.size, len(hud.extras())))
    hud.toggle_button().press()        # 付け替えられた押下先で戻す
    check("re-injecting still restores the original design size",
          hud.panel.size == PANEL_SIZE and hud.extras() == [], hud.panel.size)

    # -- 仲間が減れば黙って畳む ----------------------------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    hud.toggle_button().press()
    hud.set_party(3)                   # 4人目が離脱した
    check("a member leaving collapses the panel again",
          hud.panel.size == PANEL_SIZE and hud.extras() == [],
          (hud.panel.size, len(hud.extras())))
    check("and the covered button comes back with it",
          hud.stray.opacity == 1 and hud.stray.disabled is False,
          (hud.stray.opacity, hud.stray.disabled))

    # -- `party_cells` をなめて塗るビルド ------------------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4, paints="cells")
    hud.show()
    hud.toggle_button().press()
    extras = hud.extras()
    check("when the game paints party_cells itself, the copy is filled by the game",
          extras and extras[0].member_id == ROSTER[3][0]
          and extras[0].image().source == ROSTER[3][2],
          extras and (extras[0].member_id, extras[0].image().source))

    # -- `pos_hint` を持たない枠は座標で並べる -------------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4, absolute=True)
    hud.show()
    hud.toggle_button().press()
    extras = hud.extras()
    rows = extras + hud.party_cells[:BASE_ROWS]
    row_height = PANEL_SIZE[1] * CELL_HINT_Y
    check("rows without a pos_hint are stacked the same way",
          all(close(cell.height, row_height) for cell in rows)
          and [close(cell.y, PANEL_POS[1] + (3 - index) * row_height)
               for index, cell in enumerate(rows)] == [True] * 4,
          [(c.pos, c.size) for c in rows])
    hud.toggle_button().press()
    check("those rows are put back where they were",
          [close(cell.y, PANEL_POS[1] + (BASE_ROWS - 1 - index) * row_height)
           for index, cell in enumerate(hud.party_cells)] == [True] * 3,
          [c.pos for c in hud.party_cells])

    # -- 窓に入らない行数までは伸ばさない ------------------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.party_members = dict(("m{}".format(i), {"name": "x", "image_src": "y.png"})
                             for i in range(20))
    hud.show()
    hud.toggle_button().press()
    check("the panel never grows past what the window can hold",
          hud.panel.height <= WIN_HEIGHT * mod.MAX_FILL + 0.01, hud.panel.size)
    check("and never past the configured number of rows",
          len(hud.party_cells) <= mod.MAX_ROWS, len(hud.party_cells))

    # -- 窓の大きさが変わったとき --------------------------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    hud.toggle_button().press()
    CLOCK.tick()
    FakeWindow.resize(WIN_WIDTH * 0.5, WIN_HEIGHT * 0.5)
    hud.relayout()                     # ゲームが組み直す
    CLOCK.tick()                       # 次のフレーム: 畳んで控えを捨てる
    hud.relayout()
    CLOCK.tick()                       # その次: 新しい寸法で控え直して当て直す
    CLOCK.tick()
    check("the panel is expanded from the new design after a resize",
          close(hud.panel.height, (PANEL_SIZE[1] + ROW_PITCH) * 0.5), hud.panel.size)
    hud.toggle_button().press()
    check("restoring after a resize returns the new design size",
          close(hud.panel.height, PANEL_SIZE[1] * 0.5), hud.panel.size)
    FakeWindow.width, FakeWindow.height = WIN_WIDTH, WIN_HEIGHT

    # -- 控えだけ失われた帯（畳むのに失敗した後など） ------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    hud.toggle_button().press()
    grown = hud.panel.size
    setattr(hud.panel, mod.DESIGN_ATTR, None)      # 控えだけ消える
    warnings = len(ctx.warnings)
    hud.show()
    hud.show()
    check("an expanded panel with no design is left alone, not re-captured",
          hud.panel.size == grown, (grown, hud.panel.size))
    check("that state is reported once", len(ctx.warnings) - warnings == 1,
          ctx.warnings)

    # -- 設定 ----------------------------------------------------------------
    mod.START_EXPANDED = True
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    check("START_EXPANDED opens the panel without a press",
          close(hud.panel.height, PANEL_SIZE[1] + ROW_PITCH), hud.panel.size)
    mod.START_EXPANDED = False

    mod.HIDE_COVERED = False
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    hud.toggle_button().press()
    check("HIDE_COVERED off leaves the covered button alone",
          hud.stray.opacity == 1 and hud.stray.disabled is False,
          (hud.stray.opacity, hud.stray.disabled))
    mod.HIDE_COVERED = True

    mod.ALWAYS_SHOW_BUTTON = True
    install(mod, ctx)
    hud = FakeHUD(members=3)
    hud.show()
    check("ALWAYS_SHOW_BUTTON keeps the button out even when the party fits",
          hud.toggle_button().opacity == 1
          and hud.toggle_button().disabled is False,
          (hud.toggle_button().opacity, hud.toggle_button().disabled))
    mod.ALWAYS_SHOW_BUTTON = False

    mod.ICON = "文字"
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    button = hud.toggle_button()
    check("the text setting still gives a labelled button",
          button.text == mod.LABEL_EXPAND and not button.canvas.after.lines(),
          (button.text, button.canvas.after.instructions))
    button.press()
    check("the text setting swaps the label on press",
          button.text == mod.LABEL_RESTORE, button.text)

    for kind in ("山形", "矢印", "人", "枠"):
        mod.ICON = kind
        install(mod, ctx)
        hud = FakeHUD(members=4)
        hud.show()
        drawn = hud.toggle_button().canvas.after
        check("the {} icon is drawn".format(kind),
              bool(drawn.lines())
              and [c.rgba[:3] for c in drawn.colors()] == [(1, 1, 1)],
              drawn.instructions)
    mod.ICON = "二重山形"

    mod.BUTTON_CORNER = mod.IN_PANEL
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    button = hud.toggle_button()
    check("the in-panel setting puts the button inside the panel's top right",
          close(button.x + button.width,
                PANEL_POS[0] + PANEL_SIZE[0] - mod.PANEL_INSET)
          and close(button.y + button.height,
                    PANEL_POS[1] + PANEL_SIZE[1] - mod.PANEL_INSET),
          (button.pos, hud.panel.pos, hud.panel.size))
    button.press()
    check("the in-panel button rides up with the panel",
          close(button.y + button.height,
                hud.panel.y + hud.panel.height - mod.PANEL_INSET),
          (button.pos, hud.panel.size))

    mod.BUTTON_CORNER = "左下"
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    check("a corner setting is used as-is",
          hud.toggle_button().pos_hint == {"x": 0.005, "y": 0.005},
          hud.toggle_button().pos_hint)
    mod.BUTTON_CORNER = mod.ON_PANEL

    # -- 仲間が増減したら、その場で当て直す ----------------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    hud.toggle_button().press()
    app = RUNNING["app"]
    check("four members means one added row", len(hud.extras()) == 1,
          len(hud.extras()))
    app.add_party_member("36")         # 5人目を雇った
    hud.tick_party()                   # ゲームが画面へ写す（0.1 秒周期）
    CLOCK.tick()
    check("hiring a member adds a row on the spot", len(hud.extras()) == 2,
          len(hud.extras()))
    check("the new member is in one of the added rows",
          "36" in [c.member_id for c in hud.extras()],
          [c.member_id for c in hud.extras()])
    app.remove_party_member("36")      # 別れた
    hud.tick_party()
    CLOCK.tick()
    check("a member leaving takes the row away on the spot",
          len(hud.extras()) == 1, len(hud.extras()))
    # 人数が同じままでも顔ぶれは入れ替わる。行数の変化だけを合図にすると、
    # ここで**古い顔が残る**。
    app.remove_party_member("20")
    app.add_party_member("36")
    hud.tick_party()
    CLOCK.tick()
    check("a swap that keeps the head count still updates the face",
          hud.extras()[0].member_id == "36"
          and hud.extras()[0].image().source == DETAIL["36"]["image_src"],
          (hud.extras()[0].member_id, hud.extras()[0].image().source))

    # -- 別れた仲間を雇い直す（実機で落ちた場面） ----------------------------
    # ゲームはパーティ欄の子を1つずつ「枠」として塗るので、枠でないものを
    # 帯に置くとその瞬間に落ちる（`paint_cell` が中身を直に触る）。
    install(mod, ctx)
    hud = FakeHUD(members=5, paints="cells")
    hud.show()
    hud.toggle_button().press()
    app = RUNNING["app"]
    app.remove_party_member("36")
    hud.tick_party()
    CLOCK.tick()
    app.add_party_member("36")         # 同じ相手を雇い直す
    hud.tick_party()
    CLOCK.tick()
    check("re-hiring a member does not crash the game's own painter",
          len(hud.extras()) == 2
          and "36" in [c.member_id for c in hud.party_cells],
          ([c.member_id for c in hud.party_cells], len(hud.extras())))

    # -- 隠しっぱなしの相手は、注入し直せば戻る ------------------------------
    # どの道でも押せないボタンを画面に残さない（残ると遊べなくなる）。
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    stranded = hud.stray
    setattr(stranded, mod.HIDDEN_ATTR, (1.0, False))
    stranded.opacity, stranded.disabled = 0.0, True
    install(mod, ctx)                  # 注入し直す
    hud.show()
    check("a widget left hidden is restored on the next injection",
          stranded.opacity == 1.0 and stranded.disabled is False,
          (stranded.opacity, stranded.disabled))

    # 置き去りの黒い板も同じ。残るとそこの触りを止め続ける。
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    stray_blocker = FakeBlocker()
    setattr(stray_blocker, "_instantale_party_block_rect", FakeRectangle())
    hud.panel.add_widget(stray_blocker)
    install(mod, ctx)
    hud.show()
    check("a blocker left behind is removed on the next injection",
          stray_blocker not in hud.panel.children,
          [type(c).__name__ for c in hud.panel.children])

    # -- 枠線を引く関数が無いビルド ------------------------------------------
    borderer = module.add_border
    del module.add_border
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    hud.toggle_button().press()
    extras = hud.extras()
    check("without the game's add_border a border is drawn here instead",
          extras and extras[0].bordered is None
          and bool(extras[0].canvas.after.lines()),
          extras and extras[0].canvas.after.instructions)
    module.add_border = borderer

    # -- 立ち絵を枠いっぱいに入れる設定 --------------------------------------
    mod.PORTRAIT_FIT = mod.FIT_FILL
    install(mod, ctx)
    hud = FakeHUD(members=4)
    hud.show()
    hud.toggle_button().press()
    extras = hud.extras()
    check("the fill setting stops the portrait keeping its ratio",
          extras and extras[0].image().keep_ratio is False,
          extras and extras[0].image().keep_ratio)
    mod.PORTRAIT_FIT = mod.FIT_INSIDE

    # -- 他の MOD が HUD 直下に置いたウィジェット ------------------------------
    # 「`children` の先頭を採る」形で除外を自分のボタンだけにすると、他の MOD が
    # HUD 直下へ足したウィジェットの**中**へ入り込む。Kivy の `children` は
    # 新しい順なので、ゲームの `FloatLayout` は最後尾から探すのが正しい
    # （VERIFICATION_LOG.md §2.33）。
    install(mod, ctx)
    hud = FakeHUD(members=4)
    other = FakeWidget()
    setattr(other, "_instantale_expand_callback", object())   # 113_ の印
    hud.add_widget(other)
    hud.show()
    hud.update_button_texts(hud, ["会話する", "出る"])
    button = hud.toggle_button()
    check("another mod's widget on the HUD is not used as the host",
          button is not None and button.parent is hud.root,
          (type(button.parent).__name__ if button is not None else None,
           [type(c).__name__ for c in hud.children]))
    check("and nothing is nested inside it",
          other.children == [], [type(c).__name__ for c in other.children])

    # -- ゲームが一時的に出している窓 ------------------------------------------
    # 掴むとその窓が消えるときにボタンも道連れになる（VERIFICATION_LOG.md §2.31）。
    install(mod, ctx)
    hud = FakeHUD(members=4)
    popup = FakeWidget()
    hud.add_widget(popup)                       # Kivy の既定は先頭挿入
    hud.show()
    hud.update_button_texts(hud, ["会話する", "出る"])
    button = hud.toggle_button()
    check("a transient window on the HUD is not used as the host",
          button is not None and button.parent is hud.root,
          (type(button.parent).__name__ if button is not None else None,
           [type(c).__name__ for c in hud.children]))
    check("the button survives that window going away",
          (hud.remove_widget(popup) or True) and button in hud.root.children,
          [type(c).__name__ for c in hud.root.children])

    # -- パーティ欄が無いビルドでは何もしない --------------------------------
    install(mod, ctx)
    hud = FakeHUD(members=4, with_panel=False)
    hud.show()
    hud.update_button_texts(hud, ["会話する"])
    check("a build without a party panel is left untouched",
          hud.toggle_button() is None and hud.children == [hud.root],
          [type(c).__name__ for c in hud.root.children])

    check("no exception was swallowed", not ctx.errors, ctx.errors[:1])

    print()
    if failures:
        print("FAILED: {}".format(", ".join(failures)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
