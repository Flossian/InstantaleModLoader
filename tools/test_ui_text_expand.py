# -*- coding: utf-8 -*-
"""113_ui_text_expand.py をゲーム抜きで通す。

    python tools/test_ui_text_expand.py

偽の `scripts.hud.new_hud` / `InstanTaleHUD` / Kivy（Button・Clock・Window）を
差し込んで、次を確認する。

  ボタン   … 1枚だけ足される（塗り直しのたびに増えない）
  子の並び … **HUD 自身の子は増やさない**（ゲームの「画面の最初の子」を変えない）
  絵柄     … 背景なし・白い線のアイコン。押すと上下が入れ替わる。どの絵柄も描ける
  文字     … 絵柄に「文字」を選ぶと、今までどおり文字のボタンになる
  置き場所 … 既定ではキャラの欄（枠の右隣）の上。無ければ立ち絵の上、それも無ければ隅
  不動     … 会話で立ち絵が差し替わってもボタンは動かない（実機で左へ飛んだ）
  拡張     … 枠が倍率どおりに（上へ）広がる
  幅       … 幅倍率 1.0 では幅・`size_hint_x`・折り返し幅のどれにも触らない
  枠線     … 同じ矩形に置かれた枠線・背景も一緒に広がる（実機で本文だけ広がった）
  頭打ち   … 窓からはみ出さない（広げた枠は窓の内側に収まる）
  向き     … 下端は動かず**上へだけ**伸びる（枠の下の入力欄・状態表示をずらさない）
  寄せ     … `pos_hint` を持つ枠では位置に触らない（ゲームの寄せ方に任せる）
  復元     … もう一度押すと size_hint / size / pos / text_size が元の値に戻る
  当て直し … ゲームが枠を組み直しても、広げたままの状態が次の塗りで戻る
  ゲーム側 … ゲームの採寸が枠を戻すビルドでは、それを呼ぶのをやめて自分で採寸する
  窓       … 窓の大きさを変えても、ボタンの位置と枠の控えが新しい寸法に付いてくる
  再注入   … MOD 側の控えが作り直されても、広げた後の寸法を設計値と取り違えない
  無傷     … 本文のラベルが見つからないビルドでは何もしない

寸法は実測（GAME.md §2.3）に合わせてある: 窓 2560x1440、
本文のラベルは `hud.text_display`、その折り返し幅は `text_size=[1340.8, None]`。
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
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


MOD = find_mod("_ui_text_expand")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 実測値
WIN_WIDTH, WIN_HEIGHT = 2560.0, 1440.0
FRAME_SIZE = (1400.0, 300.0)          # 本文の枠（スクロールする入れ物）
FRAME_POS = (580.0, 120.0)           # 画面の下寄り（実機と同じく下端が近い）
WRAP_WIDTH = 1340.8                   # out/text_spacing.log の text_size[0]
GAME_FONT = "fonts/NotoSansJP.ttf"    # 本文のフォント（豆腐にしないために写す）

NARRATION = "薄暗い部屋に、腐敗した薬草の臭いが漂う。\n不衛生な医者: ……ちっ、また新しい客か。"


# ---------------------------------------------------------------- 偽 Kivy
class FakeWindow(object):
    """窓。`on_resize` を束ねられるところまで本物に似せる。"""

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
        """窓の大きさを変えて `on_resize` を配る（本物と同じ順）。"""
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


class FakeLabel(FakeWidget):
    def __init__(self, parent=None):
        FakeWidget.__init__(self)
        self.text = ""
        self.text_size = (WRAP_WIDTH, None)
        self.texture_size = [WRAP_WIDTH, 0]
        self.font_name = GAME_FONT
        self.line_height = 1.8
        self.parent = parent
        self.updates = 0

    def texture_update(self):
        self.updates += 1
        lines = self.text.count("\n") + 1 if self.text else 0
        self.texture_size = [self.text_size[0], lines * 70.0]


class FakeScroll(FakeWidget):
    """本文を載せている枠（`scroll_y` / `do_scroll_y` を持つ ＝ mod が探す相手）。"""

    def __init__(self, pos_hint=None):
        FakeWidget.__init__(self)
        self.scroll_y = 1.0
        self.do_scroll_y = True
        self.size_hint = (None, None)
        self.width, self.height = FRAME_SIZE
        self.x, self.y = FRAME_POS
        self.pos_hint = dict(pos_hint or {})


class FakeCanvasGroup(object):
    """`canvas.after`。mod は clear して描き直す。"""

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


class FakeColor(object):
    def __init__(self, *rgba):
        self.rgba = rgba


class FakeLine(object):
    def __init__(self, points=None, width=1.0, **_kwargs):
        self.points = list(points or [])
        self.width = width

    def xs(self):
        return self.points[0::2]

    def ys(self):
        return self.points[1::2]


class FakeButton(FakeWidget):
    def __init__(self, text="", size_hint=None, size=None, pos_hint=None):
        FakeWidget.__init__(self)
        self.canvas = FakeCanvas()
        self.background_normal = "atlas://data/images/defaulttheme/button"
        self.background_color = (1, 1, 1, 1)
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
        """本物の押下（`on_release`）と同じく、束ねられた側を呼ぶ。"""
        for callback in list(self.bound):
            callback(self)


class FakeBorder(FakeWidget):
    """本文の枠と同じ場所に置かれた枠線（`add_border` が描いている相手）。

    これが付いてこないと、増えた本文が枠の外へはみ出す（VERIFICATION.md §2.26）。
    ぴったり同じ矩形ではなく、数 px 大きく置く。
    """

    def __init__(self):
        FakeWidget.__init__(self)
        self.size_hint = (None, None)
        self.width, self.height = FRAME_SIZE[0] + 6.0, FRAME_SIZE[1] + 6.0
        self.x, self.y = FRAME_POS[0] - 3.0, FRAME_POS[1] - 3.0


class FakePortrait(FakeWidget):
    """立ち絵。mod は `hud.image_portrait` と同じ `source` を手がかりに探す。"""

    def __init__(self, source):
        FakeWidget.__init__(self)
        self.source = source
        self.size_hint = (None, None)
        self.width, self.height = 260.0, 300.0
        self.x, self.y = 2050.0, 600.0


class FakeHUD(FakeWidget):
    """本物の `InstanTaleHUD` のうち、この mod から見える部分だけ。

    `sizer` で「ゲーム自身の採寸」の振る舞いを差し替える:
      "follow" … 枠の幅に合わせて折り返し幅を入れ直す（追従するビルド）
      "none"   … 何もしない（mod 側が折り返し幅を入れる）
      "reset"  … 枠の寸法まで設計値に戻す（mod がこの呼び出しをやめる相手）
    """

    def __init__(self, pos_hint=None, sizer="follow", with_label=True,
                 with_portrait=True, with_panel=True):
        FakeWidget.__init__(self)
        self.display_text = ""
        self.image_portrait = "portraits/elie.png"
        self.button_texts = []
        self.height_updates = 0
        self.sizer_calls = 0
        self.sizer = sizer
        # 実機と同じ入れ子（`out/text_expand.log` の `frame neighbours:`）:
        # HUD の子は **FloatLayout 1枚だけ**で、中身はその下にぶら下がる。
        self.root = FakeWidget()
        self.root.size_hint = (None, None)
        self.root.width, self.root.height = WIN_WIDTH, WIN_HEIGHT
        self.root.parent = self
        self.scroll = FakeScroll(pos_hint)
        self.scroll.parent = self.root
        # 本文の枠と同じ場所に居るもの（枠線）と、関係ないもの（下の入力欄）。
        self.border = FakeBorder()
        self.border.parent = self.root
        self.elsewhere = FakeWidget()
        self.elsewhere.size_hint = (None, None)
        self.elsewhere.width, self.elsewhere.height = 900.0, 120.0
        self.elsewhere.x, self.elsewhere.y = 600.0, 120.0
        self.elsewhere.parent = self.root
        self.children = [self.root]
        self.root.children = [self.scroll, self.border, self.elsewhere]
        if with_portrait:
            self.portrait = FakePortrait(self.image_portrait)
            self.portrait.parent = self.root
            self.root.children.append(self.portrait)
        else:
            self.portrait = None
        # 立ち絵が見つからないビルド用の受け皿（枠の右隣に並ぶ入れ物）。
        if with_panel:
            self.panel = FakeWidget()
            self.panel.size_hint = (None, None)
            self.panel.width, self.panel.height = 400.0, 300.0
            self.panel.x, self.panel.y = 2100.0, 60.0
            self.panel.parent = self.root
            self.root.children.append(self.panel)
        else:
            self.panel = None
        if with_label:
            self.text_display = FakeLabel(parent=self.scroll)
            self.scroll.children = [self.text_display]
        else:
            self.text_display = None
            self.scroll.children = []
        # 窓の大きさに対する設計（`relayout` で入れ直す。ゲームと同じ振る舞い）。
        self.base = [(widget, widget.pos + widget.size)
                     for widget in (self.scroll, self.border, self.elsewhere)
                     + ((self.panel,) if self.panel else ())
                     + ((self.portrait,) if self.portrait else ())]

    def relayout(self):
        """窓が変わったときにゲームが画面を組み直す。寸法も位置も新しくなる。"""
        scale_x = FakeWindow.width / WIN_WIDTH
        scale_y = FakeWindow.height / WIN_HEIGHT
        for widget, (x, y, width, height) in self.base:
            widget.x, widget.y = x * scale_x, y * scale_y
            widget.width, widget.height = width * scale_x, height * scale_y

    # -- ゲーム側の仕組み ---------------------------------------------------
    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def screen_root(self):
        """ゲームが「画面の最初の子」として取る相手（`get_current_screen_root`）。"""
        return self.children[0] if self.children else None

    def update_display_text(self, instance, value):
        if self.text_display is not None:
            self.text_display.text = value

    def update_button_texts(self, instance, value):
        self.button_texts = list(value or [])

    def update_label_height(self, *args):
        self.height_updates += 1
        if self.text_display is not None:
            self.text_display.height = self.text_display.texture_size[1]

    def update_text_display_size(self, *args):
        self.sizer_calls += 1
        if self.text_display is None:
            return
        if self.sizer == "follow":
            self.text_display.text_size = (self.scroll.width - 59.2, None)
        elif self.sizer == "reset":
            self.scroll.width, self.scroll.height = FRAME_SIZE

    # -- 画面が塗られる ------------------------------------------------------
    def show(self, text=NARRATION):
        self.display_text = text
        self.update_display_text(self, text)

    def repaint(self):
        self.update_button_texts(self, ["会話する", "出る"])

    def toggle_button(self):
        for parent in (self.root, self):
            for child in getattr(parent, "children", []):
                if isinstance(child, FakeButton):
                    return child
        return None


PRISTINE = (FakeHUD.update_display_text, FakeHUD.update_button_texts)


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


def install_fake_kivy():
    """mod は kivy を関数の中で遅延 import する。sys.modules に偽物を置く。"""
    kivy = types.ModuleType("kivy")
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = CLOCK
    window_mod = types.ModuleType("kivy.core.window")
    window_mod.Window = FakeWindow
    button_mod = types.ModuleType("kivy.uix.button")
    button_mod.Button = FakeButton
    graphics_mod = types.ModuleType("kivy.graphics")
    graphics_mod.Color = FakeColor
    graphics_mod.Line = FakeLine
    for name, module in (("kivy", kivy), ("kivy.clock", clock_mod),
                         ("kivy.core", types.ModuleType("kivy.core")),
                         ("kivy.core.window", window_mod),
                         ("kivy.uix", types.ModuleType("kivy.uix")),
                         ("kivy.uix.button", button_mod),
                         ("kivy.graphics", graphics_mod)):
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

    def log(self, msg, level="INFO"):
        if level == "WARN":
            self.warnings.append(msg)

    def log_exc(self, msg):
        # 握り潰しの中で例外が出ていたらテストとしては失敗にしたい。
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_ui_text_expand", MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install(mod, ctx):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。"""
    FakeHUD.update_display_text, FakeHUD.update_button_texts = PRISTINE
    mod.apply(ctx)


def close(value, expected, tolerance=0.01):
    return abs(value - expected) < tolerance


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    module.upx = lambda value: value        # 拡縮しないビルドとして扱う
    sys.modules["scripts.hud.new_hud"] = module

    ctx = FakeCtx(os.path.join(HERE, os.pardir, "out", "test", "text_expand"))
    mod = load_mod()
    install(mod, ctx)
    check("hooked update_display_text",
          "scripts.hud.new_hud:InstanTaleHUD.update_display_text" in ctx.wrapped)
    check("hooked update_button_texts",
          "scripts.hud.new_hud:InstanTaleHUD.update_button_texts" in ctx.wrapped)
    wide, tall = mod.WIDTH_SCALE, mod.HEIGHT_SCALE

    # -- ボタンが1枚だけ足される ---------------------------------------------
    hud = FakeHUD()
    hud.show()
    button = hud.toggle_button()
    check("a toggle button is added to the HUD", button is not None)
    for _ in range(5):
        hud.show()
        hud.repaint()
    check("repainting does not add a second button",
          sum(1 for c in hud.root.children if isinstance(c, FakeButton)) == 1,
          [type(c).__name__ for c in hud.root.children])
    # 素の HUD の子は FloatLayout 1枚だけ。そこへ足すと「画面の最初の子」を取る
    # 側から見える相手が変わり、アイテムの移動・装備が壊れる（VERIFICATION.md §2.33）。
    check("the HUD's own child list is left exactly as the game built it",
          hud.children == [hud.root] and hud.screen_root() is hud.root,
          [type(c).__name__ for c in hud.children])
    check("the button rides inside the game's root layout, on top",
          hud.root.children[0] is button, [type(c).__name__ for c in hud.root.children])
    def icon_apex(widget):
        """山形の頂点が上向きなら正、下向きなら負（真ん中の点と両端の差）。"""
        lines = widget.canvas.after.lines()
        if not lines:
            return None
        ys = lines[0].ys()
        if len(ys) < 3:
            return None
        return ys[len(ys) // 2] - (ys[0] + ys[-1]) / 2.0

    check("the button shows an icon instead of text", button.text == "", button.text)
    check("the icon has no background",
          button.background_normal == "" and button.background_color[3] == 0,
          (button.background_normal, button.background_color))
    check("the icon is drawn in white",
          [c.rgba[:3] for c in button.canvas.after.colors()] == [(1, 1, 1)],
          [c.rgba for c in button.canvas.after.colors()])
    check("the icon is drawn inside the button",
          all(button.x <= x <= button.x + button.width
              and button.y <= y <= button.y + button.height
              for line in button.canvas.after.lines()
              for x, y in zip(line.xs(), line.ys())),
          [line.points for line in button.canvas.after.lines()])
    check("the collapsed icon points up", (icon_apex(button) or 0) > 0,
          icon_apex(button))
    check("the button borrows the game's font", button.font_name == GAME_FONT,
          button.font_name)
    check("the button sits just above the character panel",
          close(button.x, hud.panel.x)
          and close(button.y, hud.panel.y + hud.panel.height + mod.PORTRAIT_GAP),
          (button.pos, hud.panel.pos))
    # 会話に入ると立ち絵は差し替わる（実機ではここでボタンが画面の左へ飛んだ）。
    where = button.pos
    hud.image_portrait = "portraits/johan.png"
    hud.portrait.source = "portraits/johan.png"
    hud.portrait.x, hud.portrait.y = 20.0, 700.0
    hud.portrait.width, hud.portrait.height = 900.0, 700.0
    hud.show()
    check("the button does not move when the portrait changes",
          button.pos == where, (where, button.pos))

    # -- 押すと広がる ---------------------------------------------------------
    button.press()
    frame = hud.scroll
    check("the frame grows by the configured scale",
          close(frame.height, FRAME_SIZE[1] * tall), frame.size)
    check("pressing flips the icon over",
          (icon_apex(button) or 0) < 0, icon_apex(button))
    check("a width scale of 1.0 leaves the width exactly as it was",
          close(frame.width, FRAME_SIZE[0]) and close(hud.border.width,
                                                      FRAME_SIZE[0] + 6.0),
          (frame.size, hud.border.size))
    check("a width scale of 1.0 leaves size_hint_x to the game",
          frame.size_hint[0] == FakeScroll(None).size_hint[0], frame.size_hint)
    check("a width scale of 1.0 does not touch the wrap width",
          close(hud.text_display.text_size[0], WRAP_WIDTH),
          hud.text_display.text_size)
    check("the game's own sizer is not called when only the height changes",
          hud.sizer_calls == 0, hud.sizer_calls)
    check("the border drawn around the frame grows with it",
          close(hud.border.height, (FRAME_SIZE[1] + 6.0) * tall), hud.border.size)
    check("the border still surrounds the frame",
          hud.border.x <= frame.x and hud.border.y <= frame.y
          and hud.border.x + hud.border.width >= frame.x + frame.width,
          (hud.border.pos, hud.border.size, frame.pos, frame.size))
    check("a widget somewhere else on the HUD is not touched",
          hud.elsewhere.size == (900.0, 120.0) and hud.elsewhere.pos == (600.0, 120.0),
          (hud.elsewhere.size, hud.elsewhere.pos))
    check("the height is recomputed by the game's own method",
          hud.height_updates > 0, hud.height_updates)

    # -- 上端を保って窓の内側へ ----------------------------------------------
    CLOCK.tick()                       # 位置を窓に収める予約を流す
    check("the frame stays inside the window",
          frame.x >= 0 and frame.y >= 0
          and frame.x + frame.width <= WIN_WIDTH
          and frame.y + frame.height <= WIN_HEIGHT, (frame.pos, frame.size))
    check("the frame grew upwards only (its bottom edge did not move)",
          close(frame.x, FRAME_POS[0]) and close(frame.y, FRAME_POS[1]), frame.pos)
    check("nothing below the frame was pushed around",
          close(hud.border.y, FRAME_POS[1] - 3.0), hud.border.y)

    # -- ゲームが枠を組み直しても広げたまま ----------------------------------
    frame.width, frame.height = FRAME_SIZE
    hud.show()
    check("a frame the game reset is expanded again on the next paint",
          close(frame.width, FRAME_SIZE[0] * wide), frame.size)

    # -- もう一度押すと元に戻る ----------------------------------------------
    button.press()
    check("pressing again restores the original size",
          frame.size == FRAME_SIZE, frame.size)
    check("pressing again restores the original position",
          frame.pos == FRAME_POS, frame.pos)
    check("pressing again restores the wrap width",
          close(hud.text_display.text_size[0], WRAP_WIDTH), hud.text_display.text_size)
    check("pressing again restores the size_hint",
          frame.size_hint == (None, None), frame.size_hint)
    check("pressing again restores the border too",
          close(hud.border.height, FRAME_SIZE[1] + 6.0)
          and close(hud.border.x, FRAME_POS[0] - 3.0),
          (hud.border.size, hud.border.pos))
    check("pressing again flips the icon back", (icon_apex(button) or 0) > 0,
          icon_apex(button))
    hud.show()
    check("a restored frame is left alone by later paints",
          frame.size == FRAME_SIZE, frame.size)

    # -- 注入し直しても、広げた後の寸法を設計値と取り違えない ------------------
    button.press()
    expanded = frame.size
    install(mod, ctx)                  # MOD 側の控えは作り直される
    hud.show()
    check("re-injecting keeps the expanded size (it does not grow again)",
          frame.size == expanded, frame.size)
    hud.toggle_button().press()        # 付け替えられた押下先で戻す
    check("re-injecting still restores the original design size",
          frame.size == FRAME_SIZE, frame.size)

    # -- 窓より大きくは広げない ----------------------------------------------
    install(mod, ctx)
    mod.WIDTH_SCALE, mod.HEIGHT_SCALE = 8.0, 8.0    # 窓より大きい倍率
    install(mod, ctx)
    hud = FakeHUD()
    hud.show()
    hud.toggle_button().press()
    CLOCK.tick()
    check("a huge scale is capped by the window",
          hud.scroll.width <= WIN_WIDTH * mod.MAX_FILL + 0.01
          and hud.scroll.height <= WIN_HEIGHT * mod.MAX_FILL + 0.01, hud.scroll.size)
    mod.WIDTH_SCALE, mod.HEIGHT_SCALE = wide, tall

    # -- pos_hint を持つ枠では位置に触らない ---------------------------------
    install(mod, ctx)
    hud = FakeHUD(pos_hint={"center_x": 0.5, "top": 0.92})
    hud.show()
    hud.toggle_button().press()
    CLOCK.tick()
    check("a frame with a pos_hint keeps its position and hint",
          hud.scroll.pos == FRAME_POS
          and hud.scroll.pos_hint == {"center_x": 0.5, "top": 0.92},
          (hud.scroll.pos, hud.scroll.pos_hint))
    check("a frame with a pos_hint still grows",
          close(hud.scroll.height, FRAME_SIZE[1] * tall), hud.scroll.size)

    # -- ゲームの採寸が枠を戻すビルド（横にも広げる設定のときの話） ----------
    mod.WIDTH_SCALE = 1.5
    install(mod, ctx)
    hud = FakeHUD(sizer="reset")
    hud.show()
    hud.toggle_button().press()
    check("a sizer that resets the frame does not win",
          close(hud.scroll.width, FRAME_SIZE[0] * 1.5), hud.scroll.size)
    check("the wrap width is then computed here",
          close(hud.text_display.text_size[0],
                hud.scroll.width - (FRAME_SIZE[0] - WRAP_WIDTH)),
          hud.text_display.text_size)
    calls = hud.sizer_calls
    hud.toggle_button().press()
    hud.toggle_button().press()
    check("the game's sizer is not called again once it has fought back",
          hud.sizer_calls == calls, (calls, hud.sizer_calls))
    check("the frame is still expanded after that round trip",
          close(hud.scroll.width, FRAME_SIZE[0] * 1.5), hud.scroll.size)
    check("a build whose sizer resets the frame is reported once",
          sum(1 for w in ctx.warnings if "sizer" in w) == 1, ctx.warnings)

    # -- 折り返し幅を入れないビルド ------------------------------------------
    install(mod, ctx)
    hud = FakeHUD(sizer="none")
    hud.show()
    hud.toggle_button().press()
    check("the wrap width is computed here when the game does not follow",
          close(hud.text_display.text_size[0],
                hud.scroll.width - (FRAME_SIZE[0] - WRAP_WIDTH)),
          hud.text_display.text_size)
    hud.toggle_button().press()
    check("that wrap width is restored too",
          close(hud.text_display.text_size[0], WRAP_WIDTH), hud.text_display.text_size)
    check("the width is restored as well when it was widened",
          close(hud.scroll.width, FRAME_SIZE[0]), hud.scroll.size)
    mod.WIDTH_SCALE = wide

    # -- 最初から広げておく設定 ----------------------------------------------
    mod.START_EXPANDED = True
    install(mod, ctx)
    hud = FakeHUD()
    hud.show()
    check("START_EXPANDED opens the frame without a press",
          close(hud.scroll.height, FRAME_SIZE[1] * tall), hud.scroll.size)
    check("START_EXPANDED draws the icon the other way up",
          (icon_apex(hud.toggle_button()) or 0) < 0,
          icon_apex(hud.toggle_button()))
    mod.START_EXPANDED = False

    # -- 絵柄を「文字」にする設定 --------------------------------------------
    mod.ICON = mod.AS_TEXT
    install(mod, ctx)
    hud = FakeHUD()
    hud.show()
    button = hud.toggle_button()
    check("the text setting still gives a labelled button",
          button.text == mod.LABEL_EXPAND and not button.canvas.after.lines(),
          (button.text, button.canvas.after.instructions))
    button.press()
    check("the text setting swaps the label on press",
          button.text == mod.LABEL_RESTORE, button.text)

    # -- 絵柄はどれを選んでも白い線になる ------------------------------------
    for kind in ("山形", "矢印", "伸縮", "枠"):
        mod.ICON = kind
        install(mod, ctx)
        hud = FakeHUD()
        hud.show()
        drawn = hud.toggle_button().canvas.after
        check("the {} icon is drawn".format(kind),
              bool(drawn.lines())
              and [c.rgba[:3] for c in drawn.colors()] == [(1, 1, 1)],
              drawn.instructions)
    mod.ICON = "二重山形"

    # -- 枠の右上に置く設定 --------------------------------------------------
    mod.BUTTON_CORNER = mod.IN_FRAME
    install(mod, ctx)
    hud = FakeHUD()
    hud.show()
    button = hud.toggle_button()
    check("the in-frame setting puts the button inside the frame's top right",
          close(button.x + button.width,
                hud.scroll.x + hud.scroll.width - mod.FRAME_INSET)
          and close(button.y + button.height,
                    hud.scroll.y + hud.scroll.height - mod.FRAME_INSET),
          (button.pos, hud.scroll.pos, hud.scroll.size))
    button.press()
    check("the in-frame button rides up with the frame",
          close(button.y + button.height,
                hud.scroll.y + hud.scroll.height - mod.FRAME_INSET),
          (button.pos, hud.scroll.size))
    check("the icon follows the button when it moves",
          all(button.x <= x <= button.x + button.width
              for line in button.canvas.after.lines() for x in line.xs()),
          [line.points for line in button.canvas.after.lines()])
    mod.BUTTON_CORNER = mod.ON_PORTRAIT

    # -- 古い版が HUD 直下に足したボタン --------------------------------------
    # ゲームを起動したまま新しい版を注入したときに、置き場所が直ること。
    install(mod, ctx)
    hud = FakeHUD()
    stray = FakeButton(text="拡張", size=(30.0, 30.0))
    hud.add_widget(stray)                       # 古い版のやり方
    setattr(hud, mod.BUTTON_ATTR, stray)
    hud.show()
    check("a button left on the HUD by an older version is moved off it",
          hud.children == [hud.root] and stray in hud.root.children,
          ([type(c).__name__ for c in hud.children],
           [type(c).__name__ for c in hud.root.children]))
    check("moving it does not leave a second button behind",
          sum(1 for c in hud.root.children if isinstance(c, FakeButton)) == 1,
          [type(c).__name__ for c in hud.root.children])

    # -- 他の MOD が HUD 直下に置いたウィジェット ------------------------------
    # `children` の**先頭**（＝いちばん新しい子）を置き場所に採り、除外を自分の
    # ボタンだけにすると、HUD へウィジェットを足す MOD が2本になった時点で
    # 相手のボタンの中へ入り込む（VERIFICATION.md §2.33）。
    install(mod, ctx)
    hud = FakeHUD()
    other = FakeButton(text="パーティ", size=(30.0, 30.0))
    setattr(other, "_instantale_party_icon", object())   # 116_ の印
    hud.add_widget(other)
    hud.show()
    button = hud.toggle_button()
    check("another mod's widget on the HUD is not used as the host",
          button is not None and button.parent is hud.root,
          (type(button.parent).__name__ if button is not None else None,
           [type(c).__name__ for c in hud.children]))
    check("and nothing is nested inside it",
          other.children == [], [type(c).__name__ for c in other.children])

    # -- ゲームが一時的に出している窓 ------------------------------------------
    # 消えるときにこちらのボタンも道連れになる。ゲームの `FloatLayout` は画面が
    # 組まれた時点で居る ＝ `children` の最後尾なので、そこから探せば避けられる。
    install(mod, ctx)
    hud = FakeHUD()
    popup = FakeWidget()
    hud.add_widget(popup)                       # Kivy の既定は先頭挿入
    hud.show()
    button = hud.toggle_button()
    check("a transient window on the HUD is not used as the host",
          button is not None and button.parent is hud.root,
          (type(button.parent).__name__ if button is not None else None,
           [type(c).__name__ for c in hud.children]))
    check("the button survives that window going away",
          (hud.remove_widget(popup) or True) and button.parent is hud.root
          and button in hud.root.children,
          [type(c).__name__ for c in hud.root.children])

    # -- 窓の大きさが変わったとき --------------------------------------------
    # 塗り直しは来ないので、窓の `on_resize` を拾えていないと、ボタンは古い座標に
    # 取り残され、枠の控えも古い窓の値のまま残る（VERIFICATION.md §2.26）。
    install(mod, ctx)
    hud = FakeHUD()
    hud.show()
    hud.toggle_button().press()
    CLOCK.tick()
    button = hud.toggle_button()
    FakeWindow.resize(WIN_WIDTH * 0.5, WIN_HEIGHT * 0.5)   # 窓を半分に
    hud.relayout()                       # ゲームが組み直す
    CLOCK.tick()                         # 次のフレーム: 畳んで控えを捨てる
    hud.relayout()                       # 畳んだぶんをゲームが入れ直す
    CLOCK.tick()                         # その次: 新しい寸法で控え直して当て直す
    CLOCK.tick()                         # 窓に収める予約
    check("the frame is expanded from the new design after a resize",
          close(hud.scroll.height, FRAME_SIZE[1] * 0.5 * tall), hud.scroll.size)
    check("the button follows the panel after a resize",
          close(button.x, hud.panel.x)
          and close(button.y, hud.panel.y + hud.panel.height + mod.PORTRAIT_GAP),
          (button.pos, hud.panel.pos))
    button.press()
    check("pressing restore after a resize returns the new design size",
          close(hud.scroll.height, FRAME_SIZE[1] * 0.5), hud.scroll.size)
    FakeWindow.width, FakeWindow.height = WIN_WIDTH, WIN_HEIGHT

    # -- 控えだけ失われた枠（畳むのに失敗した後など） ------------------------
    # 広がっている枠から控え直すと、その寸法が設計値になって二度と戻せなくなる。
    install(mod, ctx)
    hud = FakeHUD()
    hud.show()
    hud.toggle_button().press()
    CLOCK.tick()
    grown = hud.scroll.size
    setattr(hud.scroll, mod.DESIGN_ATTR, None)      # 控えだけ消える
    warnings = len(ctx.warnings)
    hud.show()
    hud.show()
    check("an expanded frame with no design is left alone, not re-captured",
          hud.scroll.size == grown, (grown, hud.scroll.size))
    check("that state is reported once", len(ctx.warnings) - warnings == 1,
          ctx.warnings)

    # -- 立ち絵を `source` で見つけられないビルド ----------------------------
    # `frames.MISSING` は文字列（"<missing>"）なので、既定値のまま照合すると
    # **`source` を持たないウィジェットが全部一致**する。これでボタンが画面の
    # 一番上に貼り付く（VERIFICATION.md §2.26）。
    install(mod, ctx)
    hud = FakeHUD(with_portrait=True, with_panel=False)
    hud.show()
    button = hud.toggle_button()
    check("a widget with no source is never mistaken for the portrait",
          not close(button.y, hud.border.y + hud.border.height + mod.PORTRAIT_GAP),
          button.pos)
    check("without a character panel the portrait is used instead",
          close(button.x, hud.portrait.x)
          and close(button.y, hud.portrait.y + hud.portrait.height + mod.PORTRAIT_GAP),
          (button.pos, hud.portrait.pos))

    # -- 立ち絵も右の入れ物も無い画面 ----------------------------------------
    install(mod, ctx)
    hud = FakeHUD(with_portrait=False, with_panel=False)
    hud.show()
    check("with neither, the button falls back to a corner",
          hud.toggle_button().pos_hint == mod.CORNERS["右上"],
          hud.toggle_button().pos_hint)

    # -- 隅を直に指定する設定 ------------------------------------------------
    mod.BUTTON_CORNER = "左下"
    install(mod, ctx)
    hud = FakeHUD()
    hud.show()
    check("a corner setting is used as-is",
          hud.toggle_button().pos_hint == mod.CORNERS["左下"],
          hud.toggle_button().pos_hint)
    mod.BUTTON_CORNER = mod.ON_PORTRAIT

    # -- 本文のラベルが無いビルドでは何もしない ------------------------------
    install(mod, ctx)
    hud = FakeHUD(with_label=False)
    hud.show("(このビルドは本文をラベルに入れない)")
    hud.repaint()
    check("a build without a narration label is left untouched",
          hud.toggle_button() is None and hud.scroll.size == FRAME_SIZE,
          hud.scroll.size)

    check("no exception was swallowed", not ctx.errors, ctx.errors[:1])

    print()
    if failures:
        print("FAILED: {}".format(", ".join(failures)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
