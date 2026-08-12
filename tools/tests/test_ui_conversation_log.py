# -*- coding: utf-8 -*-
"""122_ui_conversation_log をゲーム抜きで通す。

    python tools/test_ui_conversation_log.py

偽の `__main__.InstantaleApp` / `scripts.hud.new_hud` / Kivy（Button・Label・
ScrollView・ModalView・Clock・Window）を差し込んで、次を確認する。

  合図     … 本文の始まり（`index == -1`）だけを1件として控える。続きでは増えない
  重複     … 同じ本文が続けて来ても2件にならない
  無傷     … ゲームの `add_text_display` は同じ引数でそのまま呼ばれる
  保存     … `state/conversation_log/<世界>.jsonl` に1行1件で追記される
  読み直し … 注入し直して控えを失っても、ファイルから読み戻せる
  世界     … 世界が変われば別のファイルになる（混ざらない）
  上限     … 件数の上限を超えたら古いほうから落ち、ファイルも畳み直される
  壊れ行   … 壊れた行はその行だけ捨てて、残りは読める
  ボタン   … 1枚だけ足される（塗り直しのたびに増えない）
  子の並び … **HUD 自身の子は増やさない**（ゲームの「画面の最初の子」を変えない）
  隣       … 113 のボタンのすぐ左に、下端と高さを揃えて並ぶ
  追従     … 113 のボタンが動いたら、塗り直しを待たずに付いていく
  代替     … 113 が入っていなければ 113 と同じ場所（本文の枠の内側・右上）
  絵柄     … 背景なし・白い線の本のアイコンがボタンの中に描かれる
  窓       … 押すと窓が開き、控えた本文が古い順に入っている。もう一度押すと閉じる
  追記     … 開いている間に来た本文が、その場で窓に足される
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


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


MOD = find_mod("_ui_conversation_log")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def close(value, expected, tolerance=0.01):
    return abs(float(value) - float(expected)) < tolerance


# ---------------------------------------------------------------- 実測値
WIN_WIDTH, WIN_HEIGHT = 2560.0, 1440.0
FRAME_SIZE = (1400.0, 300.0)          # 本文の枠（スクロールする入れ物）
FRAME_POS = (580.0, 120.0)
GAME_FONT = "fonts/NotoSansJP.ttf"    # 本文のフォント（豆腐にしないために写す）
FONT_SIZE = 27.0
EXPAND_BUTTON_SIZE = 44.0             # 113 のボタン（大きさの設定を変えた場合）
EXPAND_BUTTON_POS = (1900.0, 380.0)

NARRATION = "薄暗い部屋に、腐敗した薬草の臭いが漂う。"
NARRATION2 = "不衛生な医者: ……ちっ、また新しい客か。"


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


#: いま `with canvas.xxx:` の中に居る描画先。本物の Kivy は with の中で作られた
#: 命令を自動でその canvas に積むので、偽物でも同じにしておく（`add()` を明示的に
#: 呼ぶ経路（アイコンの描き直し）とどちらも通す）。
ACTIVE = []


class FakeCanvasGroup(object):
    """`canvas.before` / `canvas.after`。with 文でも使える（本物と同じ）。"""

    def __init__(self):
        self.instructions = []

    def __enter__(self):
        ACTIVE.append(self)
        return self

    def __exit__(self, *_args):
        ACTIVE.pop()
        return False

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
        self.before = FakeCanvasGroup()
        self.after = FakeCanvasGroup()


class FakeInstruction(object):
    """`with canvas.xxx:` の中で作られたら、その canvas に積まれる（本物と同じ）。"""

    def _register(self):
        if ACTIVE:
            ACTIVE[-1].add(self)


class FakeColor(FakeInstruction):
    def __init__(self, *rgba):
        self.rgba = rgba
        self._register()


class FakeLine(FakeInstruction):
    def __init__(self, points=None, width=1.0, rectangle=None, **_kwargs):
        self.points = list(points or [])
        self.width = width
        self.rectangle = rectangle
        self._register()

    def xs(self):
        return self.points[0::2]

    def ys(self):
        return self.points[1::2]


class FakeRectangle(FakeInstruction):
    def __init__(self, pos=(0, 0), size=(0, 0)):
        self.pos, self.size = pos, size
        self._register()


class FakeWidget(object):
    """Kivy のプロパティのうち、この mod が頼っている振る舞いだけ写したもの。

    `size` / `pos` が `width` などと繋がっていること、`bind()` した相手に
    変化が配られること（mod は 113 のボタンの `pos` に束ねて追従する）。
    """

    def __init__(self, **kwargs):
        object.__setattr__(self, "_binds", {})
        # `width` などは `size` の通知に使うので、束ねる前に素で置いておく。
        for name in ("width", "height", "x", "y"):
            object.__setattr__(self, name, 0.0)
        self.size_hint = (1, 1)
        self.pos_hint = {}
        self.parent = None
        self.children = []
        self.canvas = FakeCanvas()
        self.opacity = 1.0
        for name, value in kwargs.items():
            setattr(self, name, value)

    def __setattr__(self, name, value):
        if name == "size":
            object.__setattr__(self, "width", value[0])
            object.__setattr__(self, "height", value[1])
        elif name == "pos":
            object.__setattr__(self, "x", value[0])
            object.__setattr__(self, "y", value[1])
        else:
            object.__setattr__(self, name, value)
        self._fire(name, value)
        if name in ("x", "y"):
            self._fire("pos", self.pos)
        if name in ("width", "height"):
            self._fire("size", self.size)

    def _fire(self, name, value):
        for callback in list(self._binds.get(name, ())):
            callback(self, value)

    @property
    def size(self):
        return (self.width, self.height)

    @property
    def pos(self):
        return (self.x, self.y)

    def bind(self, **kwargs):
        for name, callback in kwargs.items():
            self._binds.setdefault(name, []).append(callback)

    def unbind(self, **kwargs):
        for name, callback in kwargs.items():
            handlers = self._binds.get(name, [])
            if callback in handlers:
                handlers.remove(callback)

    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def remove_widget(self, widget):
        if widget in self.children:
            self.children.remove(widget)
        if widget.parent is self:
            widget.parent = None


class FakeLabel(FakeWidget):
    def __init__(self, **kwargs):
        FakeWidget.__init__(self)
        self.text = ""
        self.text_size = (FRAME_SIZE[0], None)
        self.texture_size = [FRAME_SIZE[0], 0.0]
        self.font_name = GAME_FONT
        self.font_size = FONT_SIZE
        self.line_height = 1.8
        for name, value in kwargs.items():
            setattr(self, name, value)

    def __setattr__(self, name, value):
        FakeWidget.__setattr__(self, name, value)
        if name in ("text", "text_size"):
            self.texture_update()

    def texture_update(self):
        text = getattr(self, "text", "")
        wrap = getattr(self, "text_size", (0, None))
        lines = text.count("\n") + 1 if text else 0
        object.__setattr__(self, "texture_size", [wrap[0] or 0.0, lines * 30.0])
        self._fire("texture_size", self.texture_size)


class FakeScrollView(FakeWidget):
    def __init__(self, **kwargs):
        FakeWidget.__init__(self)
        self.scroll_y = 1.0
        self.do_scroll_y = True
        self.do_scroll_x = True
        self.width, self.height = FRAME_SIZE
        for name, value in kwargs.items():
            setattr(self, name, value)


class FakeBoxLayout(FakeWidget):
    def __init__(self, **kwargs):
        FakeWidget.__init__(self)
        self.orientation = "horizontal"
        self.padding = 0
        self.spacing = 0
        for name, value in kwargs.items():
            setattr(self, name, value)


class FakeButton(FakeWidget):
    def __init__(self, text="", size_hint=None, size=None, pos_hint=None, **kwargs):
        FakeWidget.__init__(self)
        self.background_normal = "atlas://data/images/defaulttheme/button"
        self.background_color = (1, 1, 1, 1)
        self.text = text
        self.size_hint = size_hint or (1, 1)
        self.width, self.height = size or (0.0, 0.0)
        self.pos_hint = dict(pos_hint or {})
        self.font_name = None
        self.font_size = 0
        for name, value in kwargs.items():
            setattr(self, name, value)

    def press(self):
        """本物の押下（`on_release`）と同じく、束ねられた側を呼ぶ。"""
        for callback in list(self._binds.get("on_release", ())):
            callback(self)


class FakeModalView(FakeWidget):
    opened = []

    def __init__(self, **kwargs):
        FakeWidget.__init__(self)
        self.auto_dismiss = True
        self.is_open = False
        for name, value in kwargs.items():
            setattr(self, name, value)

    def open(self):
        self.is_open = True
        FakeModalView.opened.append(self)

    def dismiss(self):
        self.is_open = False
        if self in FakeModalView.opened:
            FakeModalView.opened.remove(self)
        for callback in list(self._binds.get("on_dismiss", ())):
            callback(self)


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
    modules = {
        "kivy": types.ModuleType("kivy"),
        "kivy.clock": types.ModuleType("kivy.clock"),
        "kivy.core": types.ModuleType("kivy.core"),
        "kivy.core.window": types.ModuleType("kivy.core.window"),
        "kivy.uix": types.ModuleType("kivy.uix"),
        "kivy.uix.button": types.ModuleType("kivy.uix.button"),
        "kivy.uix.label": types.ModuleType("kivy.uix.label"),
        "kivy.uix.boxlayout": types.ModuleType("kivy.uix.boxlayout"),
        "kivy.uix.scrollview": types.ModuleType("kivy.uix.scrollview"),
        "kivy.uix.modalview": types.ModuleType("kivy.uix.modalview"),
        "kivy.graphics": types.ModuleType("kivy.graphics"),
    }
    modules["kivy.clock"].Clock = CLOCK
    modules["kivy.core.window"].Window = FakeWindow
    modules["kivy.uix.button"].Button = FakeButton
    modules["kivy.uix.label"].Label = FakeLabel
    modules["kivy.uix.boxlayout"].BoxLayout = FakeBoxLayout
    modules["kivy.uix.scrollview"].ScrollView = FakeScrollView
    modules["kivy.uix.modalview"].ModalView = FakeModalView
    modules["kivy.graphics"].Color = FakeColor
    modules["kivy.graphics"].Line = FakeLine
    modules["kivy.graphics"].Rectangle = FakeRectangle
    sys.modules.update(modules)


# ---------------------------------------------------------------- 偽ゲーム
class InstantaleApp(object):
    """本物の `InstantaleApp` のうち、この mod から見える部分だけ。"""

    def __init__(self, world="オルステッド"):
        self.world_dict = {"world_data": {"world_name": world}}
        self.calls = []

    def add_text_display(self, dt, context, index=-1):
        """本文の打ち出し。ゲーム側の仕事はここでは何もしない。"""
        self.calls.append((dt, context, index))
        return "done"


class FakeHUD(FakeWidget):
    """本物の `InstanTaleHUD` のうち、この mod から見える部分だけ。"""

    def __init__(self, with_expand_button=True, with_label=True):
        FakeWidget.__init__(self)
        self.display_text = ""
        # 実機と同じ入れ子: HUD の子は FloatLayout 1枚だけ。
        self.root = FakeWidget()
        self.root.size_hint = (None, None)
        self.root.width, self.root.height = WIN_WIDTH, WIN_HEIGHT
        self.root.parent = self
        self.scroll = FakeScrollView()
        self.scroll.size_hint = (None, None)
        self.scroll.width, self.scroll.height = FRAME_SIZE
        self.scroll.x, self.scroll.y = FRAME_POS
        self.scroll.parent = self.root
        self.children = [self.root]
        self.root.children = [self.scroll]
        if with_label:
            self.text_display = FakeLabel()
            self.text_display.parent = self.scroll
            self.scroll.children = [self.text_display]
        else:
            self.text_display = None
        if with_expand_button:
            # 113 が置いたボタン（HUD の控えと同じ名前で持つ）。
            other = FakeButton(size_hint=(None, None),
                               size=(EXPAND_BUTTON_SIZE, EXPAND_BUTTON_SIZE))
            other.x, other.y = EXPAND_BUTTON_POS
            self.root.add_widget(other)
            self.expand_button = other
            setattr(self, "_instantale_expand_button", other)
        else:
            self.expand_button = None

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

    def show(self, text=NARRATION):
        self.display_text = text
        self.update_display_text(self, text)

    def repaint(self):
        self.update_button_texts(self, ["会話する", "出る"])

    def log_button(self):
        for child in self.root.children:
            if isinstance(child, FakeButton) and child is not self.expand_button:
                return child
        return None


PRISTINE = (FakeHUD.update_display_text, FakeHUD.update_button_texts,
            InstantaleApp.add_text_display)


# ---------------------------------------------------------------- 偽ローダ
class FakeCtx(object):
    """`ctx.wrap` だけ本物と同じ形にする（第1引数 orig、第2引数 self）。"""

    def __init__(self, out_dir, state_dir):
        self.out_dir = out_dir
        self.state_dir = state_dir
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

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        if level == "WARN":
            self.warnings.append(msg)

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    # 本物の `ctx.write_json` / `write_text` と同じものを使う。ここを自前の
    # open(..., "w") にすると、テストだけが「壊れない書き方」を通らなくなる。
    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_ui_conversation_log", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install(mod, ctx):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。"""
    (FakeHUD.update_display_text, FakeHUD.update_button_texts,
     InstantaleApp.add_text_display) = PRISTINE
    mod.apply(ctx)


def forget_store(mod):
    """MOD が `sys` に持っている控えを落とす（ゲームを起動し直した状態）。"""
    if hasattr(sys, mod.STATE_STORE_ATTR):
        delattr(sys, mod.STATE_STORE_ATTR)


def running(app):
    """走っているアプリを1つだけ `__main__` に置く（`ui.find_app` が探す形）。"""
    setattr(sys.modules["__main__"], "the_running_app", app)


def opened_view():
    return FakeModalView.opened[0] if FakeModalView.opened else None


def view_parts(view):
    """開いている窓の `(地の箱, ScrollView, 本文の Label 一覧)`。

    本文は Label 1枚ではなく縦に並べた複数枚（テクスチャの上限。`view_blocks`）。
    偽の `add_widget` も本物と同じく先頭挿入なので、**逆順が上から下の並び**。
    """
    if view is None or not view.children:
        return None, None, []
    root = view.children[0]
    scrolls = [c for c in root.children if isinstance(c, FakeScrollView)]
    if not scrolls or not scrolls[0].children:
        return root, (scrolls[0] if scrolls else None), []
    column = scrolls[0].children[0]
    return root, scrolls[0], list(reversed(column.children))


def view_body(view):
    """窓に出ている本文を、並び順のままひと繋ぎにしたもの。"""
    return "\n\n".join(label.text for label in view_parts(view)[2])


def lines_of(path):
    with io.open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


def run():
    install_fake_kivy()
    hud_module = types.ModuleType("scripts.hud.new_hud")
    hud_module.InstanTaleHUD = FakeHUD
    hud_module.upx = lambda value: value      # 拡縮しないビルドとして扱う
    sys.modules["scripts.hud.new_hud"] = hud_module
    # `__main__:InstantaleApp` を wrap の対象にする（本物と同じ在り処）。
    setattr(sys.modules["__main__"], "InstantaleApp", InstantaleApp)

    out_dir = os.path.join(HERE, os.pardir, "out", "test", "conversation_log")
    state_dir = os.path.join(out_dir, "state")
    if os.path.isdir(state_dir):
        shutil.rmtree(state_dir)
    ctx = FakeCtx(out_dir, state_dir)
    mod = load_mod()
    forget_store(mod)
    install(mod, ctx)
    check("hooked add_text_display",
          "__main__:InstantaleApp.add_text_display" in ctx.wrapped)
    check("hooked update_display_text",
          "scripts.hud.new_hud:InstanTaleHUD.update_display_text" in ctx.wrapped)

    log_path = os.path.join(state_dir, mod.STATE_DIRNAME,
                            ml.state.world_filename("オルステッド", ".jsonl"))

    # -- 控える --------------------------------------------------------------
    app = InstantaleApp()
    result = app.add_text_display(0, NARRATION)
    check("the game's own call is left alone",
          result == "done" and app.calls == [(0, NARRATION, -1)], app.calls)
    app.add_text_display(0, NARRATION, 3)          # 打ち出しの続き
    app.add_text_display(0, NARRATION)             # 同じ本文の打ち直し
    app.add_text_display(0, NARRATION2)
    check("one entry per message", len(lines_of(log_path)) == 2, lines_of(log_path))
    entries = [json.loads(line) for line in lines_of(log_path)]
    check("the messages are kept in order",
          [e["text"] for e in entries] == [NARRATION, NARRATION2],
          [e["text"] for e in entries])
    check("each entry carries the time it arrived",
          all(isinstance(e.get("t"), str) and len(e["t"]) >= 16 for e in entries),
          entries)

    # -- ボタン --------------------------------------------------------------
    hud = FakeHUD()
    hud.show()
    button = hud.log_button()
    check("a log button is added to the HUD", button is not None)
    for _ in range(5):
        hud.show()
        hud.repaint()
    check("repainting does not add a second button",
          sum(1 for c in hud.root.children
              if isinstance(c, FakeButton) and c is not hud.expand_button) == 1,
          [type(c).__name__ for c in hud.root.children])
    # 素の HUD の子は FloatLayout 1枚だけ。増やすと「画面の最初の子」を取る側から
    # 見える相手が変わり、アイテムの移動・装備が壊れる（VERIFICATION_LOG.md §2.33）。
    check("the HUD's own child list is left exactly as the game built it",
          hud.children == [hud.root] and hud.screen_root() is hud.root,
          [type(c).__name__ for c in hud.children])

    other = hud.expand_button
    check("the button sits just left of 113's, bottoms aligned",
          close(button.x, other.x - button.width - mod.BUTTON_GAP)
          and close(button.y, other.y),
          (button.pos, other.pos))
    check("the button matches 113's height",
          close(button.height, EXPAND_BUTTON_SIZE) and close(button.width, button.height),
          (button.size, other.size))

    # 113 のボタンは枠が伸びると動く。塗り直しを待たずに付いていく。
    other.pos = (1200.0, 900.0)
    check("the button follows 113's when it moves",
          close(button.x, 1200.0 - button.width - mod.BUTTON_GAP)
          and close(button.y, 900.0), (button.pos, other.pos))

    # -- 絵柄 ----------------------------------------------------------------
    check("the button shows an icon instead of text", button.text == "", button.text)
    check("the icon has no background",
          button.background_normal == "" and button.background_color[3] == 0,
          (button.background_normal, button.background_color))
    check("the icon is drawn in white",
          [c.rgba[:3] for c in button.canvas.after.colors()] == [(1, 1, 1)],
          [c.rgba for c in button.canvas.after.colors()])
    check("the icon is drawn inside the button",
          button.canvas.after.lines()
          and all(button.x <= x <= button.x + button.width
                  and button.y <= y <= button.y + button.height
                  for line in button.canvas.after.lines()
                  for x, y in zip(line.xs(), line.ys())),
          [line.points for line in button.canvas.after.lines()])
    check("the book icon is drawn with more than one stroke",
          len(button.canvas.after.lines()) >= 2,
          len(button.canvas.after.lines()))
    check("the button borrows the game's font", button.font_name == GAME_FONT,
          button.font_name)

    # -- 113 が入っていない画面 ----------------------------------------------
    plain = FakeHUD(with_expand_button=False)
    plain.show()
    alone = plain.log_button()
    check("without 113 the button goes where 113 would sit",
          alone is not None
          and close(alone.x, FRAME_POS[0] + FRAME_SIZE[0] - alone.width
                    - mod.FRAME_INSET)
          and close(alone.y, FRAME_POS[1] + FRAME_SIZE[1] - alone.height
                    - mod.FRAME_INSET),
          None if alone is None else (alone.pos, FRAME_POS, FRAME_SIZE))
    check("without 113 the button uses its own size",
          alone is not None and close(alone.height, mod.BUTTON_SIZE),
          None if alone is None else alone.size)

    # -- 読む窓 --------------------------------------------------------------
    button.press()
    CLOCK.tick()
    check("pressing the button opens a window", len(FakeModalView.opened) == 1,
          FakeModalView.opened)
    view = opened_view()
    root, scroll, labels = view_parts(view)
    body = view_body(view)
    first = labels[0] if labels else None
    check("the window shows the messages oldest first",
          body.index(NARRATION) < body.index(NARRATION2), body[:120])
    check("the window starts at the newest message",
          scroll is not None and close(scroll.scroll_y, 0.0),
          None if scroll is None else scroll.scroll_y)
    check("the window borrows the game's font",
          first is not None and first.font_name == GAME_FONT,
          None if first is None else first.font_name)
    check("the window's text is the same size as the narration by default",
          first is not None and close(mod.FONT_SCALE, 1.0)
          and close(first.font_size, FONT_SIZE),
          None if first is None else (first.font_size, FONT_SIZE, mod.FONT_SCALE))

    # 枠線は地の色の後（`canvas.after`）に、窓の矩形どおりに引く。
    border = root.canvas.after.lines() if root is not None else []
    check("the window is outlined in white",
          len(border) == 1 and border[0].rectangle is not None
          and [round(v) for v in border[0].rectangle]
          == [round(root.x), round(root.y), round(root.width), round(root.height)],
          [line.rectangle for line in border])
    check("the outline is white",
          root is not None
          and [c.rgba[:3] for c in root.canvas.after.colors()] == [(1, 1, 1)],
          None if root is None else [c.rgba for c in root.canvas.after.colors()])
    # 窓の寸法が決まるのは中身が並んだ後。線はそこへ付いていく。
    root.size = (900.0, 700.0)
    check("the outline follows the window's size",
          border and [round(v) for v in border[0].rectangle][2:] == [900, 700],
          None if not border else border[0].rectangle)

    # 開いている間に来た本文は、その場で足される。
    app.add_text_display(0, "その後、扉が軋んで開いた。")
    CLOCK.tick()
    check("a message that arrives while the window is open shows up",
          "扉が軋んで" in view_body(view), view_body(view)[-60:])

    button.press()
    check("pressing the button again closes the window",
          not FakeModalView.opened and view is not None and not view.is_open,
          FakeModalView.opened)

    # -- 注入し直し / 読み直し ------------------------------------------------
    # ゲームを起動し直した状態。まだ1件も控えていないので、窓に出す世界は
    # 走っているアプリから引く（`ui.find_app` は `__main__` を見る）。
    running(app)
    forget_store(mod)
    install(mod, ctx)
    fresh = FakeHUD()
    fresh.show()
    fresh_button = fresh.log_button()
    fresh_button.press()
    CLOCK.tick()
    reloaded_body = view_body(opened_view())
    check("the log survives a reload of the mod",
          NARRATION in reloaded_body and "扉が軋んで" in reloaded_body,
          reloaded_body[:120])
    fresh_button.press()

    # 壊れた行（書き込みの途中で落ちた最後の1行）はその行だけ捨てる。
    with io.open(log_path, "a", encoding="utf-8") as fh:
        fh.write('{"t": "2026-08-06T12:00:00", "text": "途中で落ち')
    running(app)
    forget_store(mod)
    install(mod, ctx)
    broken_hud = FakeHUD()
    broken_hud.show()
    broken_button = broken_hud.log_button()
    broken_button.press()
    CLOCK.tick()
    broken_body = view_body(opened_view())
    check("a half-written line does not take the rest of the log with it",
          NARRATION in broken_body, broken_body[:120])
    broken_button.press()

    # -- 上限と畳み直し -------------------------------------------------------
    # 設定はローダがモジュールのグローバルへ書き込む（TECH.md §3.8）。同じ形で
    # 上限を下げて、古いものが落ちること・ファイルが伸び続けないことを見る。
    mod.MAX_ENTRIES, mod.COMPACT_RATIO = 5, 2
    forget_store(mod)
    install(mod, ctx)
    many = InstantaleApp(world="トリム")
    many_path = os.path.join(state_dir, mod.STATE_DIRNAME,
                             ml.state.world_filename("トリム", ".jsonl"))
    for index in range(20):
        many.add_text_display(0, "本文 {}".format(index))
    kept = [json.loads(line)["text"] for line in lines_of(many_path)]
    check("the file is folded back instead of growing forever",
          len(kept) <= mod.MAX_ENTRIES * mod.COMPACT_RATIO, len(kept))
    check("the newest messages are the ones that stay",
          kept[-1] == "本文 19" and "本文 0" not in kept, kept)
    running(many)
    forget_store(mod)
    install(mod, ctx)
    reread = FakeHUD()
    reread.show()
    reread_button = reread.log_button()
    reread_button.press()
    CLOCK.tick()
    last_body = view_body(opened_view())
    check("re-reading the folded file keeps at most the limit",
          last_body.count("── ") <= mod.MAX_ENTRIES, last_body.count("── "))
    reread_button.press()

    # -- 窓の大きさが変わっても置き場所が付いてくる --------------------------
    mod.MAX_ENTRIES, mod.COMPACT_RATIO = 500, 3
    hud.expand_button.pos = (1500.0, 500.0)
    FakeWindow.resize(1920.0, 1080.0)
    CLOCK.tick()
    check("the button stays next to 113's after the window is resized",
          close(button.x, 1500.0 - button.width - mod.BUTTON_GAP)
          and close(button.y, 500.0), (button.pos, hud.expand_button.pos))

    # -- 長いログを1枚に入れない ---------------------------------------------
    # Kivy の Label は中身を1枚のテクスチャに焼くので、GPU の上限を超えると
    # **何も描かれない**（実機で踏んでいる。VERIFICATION.md §3.21）。
    long_app = InstantaleApp(world="ノルン")
    running(long_app)
    forget_store(mod)
    install(mod, ctx)
    for index in range(120):
        long_app.add_text_display(0, "長い本文 {} ".format(index) + "あ" * 400)
    long_hud = FakeHUD()
    long_hud.show()
    long_button = long_hud.log_button()
    long_button.press()
    CLOCK.tick()
    long_labels = view_parts(opened_view())[2]
    check("a long log is split across several labels",
          len(long_labels) > 1, len(long_labels))
    check("no single label goes over the chunk limit",
          long_labels
          and max(len(label.text) for label in long_labels) <= mod.VIEW_CHUNK_CHARS,
          None if not long_labels else max(len(l.text) for l in long_labels))
    check("the labels are in order, oldest at the top",
          view_body(opened_view()).index("長い本文 0 ")
          < view_body(opened_view()).index("長い本文 119 "),
          [label.text[:12] for label in long_labels[:3]])
    # 開いたままでも、来たぶんだけ足す（全部組み直さない）。
    grew = len(long_labels[-1].text)
    long_app.add_text_display(0, "続きの本文")
    CLOCK.tick()
    tail = view_parts(opened_view())[2]
    check("a new message extends the last label or starts a new one",
          "続きの本文" in tail[-1].text
          and (len(tail) == len(long_labels) or len(tail) == len(long_labels) + 1)
          and len(tail[-1].text) <= mod.VIEW_CHUNK_CHARS,
          (len(long_labels), len(tail), grew, len(tail[-1].text)))
    long_button.press()

    # -- 枠線を切る -----------------------------------------------------------
    mod.VIEW_BORDER = False
    button.press()
    CLOCK.tick()
    plain_root = view_parts(opened_view())[0]
    check("the outline can be switched off",
          plain_root is not None and not plain_root.canvas.after.lines(),
          None if plain_root is None else
          [line.rectangle for line in plain_root.canvas.after.lines()])
    button.press()
    mod.VIEW_BORDER = True

    # -- 世界ごとに分かれる ---------------------------------------------------
    before = len(lines_of(log_path))
    other_app = InstantaleApp(world="ヴァルド")
    other_app.add_text_display(0, "別の世界の本文")
    other_path = os.path.join(state_dir, mod.STATE_DIRNAME,
                              ml.state.world_filename("ヴァルド", ".jsonl"))
    check("another world gets its own file, and does not mix in",
          os.path.isfile(other_path) and len(lines_of(log_path)) == before,
          os.listdir(os.path.join(state_dir, mod.STATE_DIRNAME)))

    check("nothing was swallowed by an exception handler", not ctx.errors,
          "\n".join(ctx.errors))


if __name__ == "__main__":
    run()
    print("")
    if failures:
        print("FAILED: " + ", ".join(failures))
        raise SystemExit(1)
    print("all checks passed")
