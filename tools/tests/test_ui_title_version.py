# -*- coding: utf-8 -*-
"""126_ui_title_version をゲーム抜きで通す。

    python tools/tests/test_ui_title_version.py

偽の Kivy（Label / Window）と偽の `scripts.hud.hud_start:StartScreen` を差し込み、
次を確認する。

  注入時     … もう出ているタイトル画面を窓から辿って見つけ、その場で足す
  組み直し   … 後から組まれるタイトル画面にも足す（タイトルへ戻る経路）
  重ね置き   … 注入し直しても、ラベルは1枚のまま置き換わる
  文字       … {version} と {mods} が実際の値に置き換わる
  置き場所   … 設定した隅が pos_hint に出る。知らない名前なら右上へ落ちる
  フォント   … 画面のウィジェットからフォント名を写す（日本語が豆腐にならない）
  大きさ     … ラベルの箱を文字の大きさに合わせる（既定の 100x100 のままにしない）
  無傷       … ゲームが組んだ子には触らない。Kivy が引けなければ何もしない
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

import instantale_modloader as ml            # noqa: E402


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


MOD = find_mod("_ui_title_version")
MOD_NAME = "mod_ui_title_version"
FONT = "NotoSansJP-Regular.ttf"

failures = []
checked = 0


def check(name, cond, detail=""):
    global checked
    checked += 1
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽 Kivy
class FakeWidget(object):
    def __init__(self, **kwargs):
        self.children = []
        self.parent = None
        self.size = [100.0, 100.0]
        for name, value in kwargs.items():
            setattr(self, name, value)

    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def remove_widget(self, widget):
        if widget in self.children:
            self.children.remove(widget)
            widget.parent = None


class FakeLabel(FakeWidget):
    """`Label` のうち、この mod が触る分だけ。文字の箱の配り方も本物に似せる。"""

    def __init__(self, **kwargs):
        self.text = ""
        self.font_size = 10.0
        self.font_name = None
        self.texture_size = None
        self.bound = {}
        FakeWidget.__init__(self, **kwargs)

    def bind(self, **kwargs):
        for event, callback in kwargs.items():
            self.bound.setdefault(event, []).append(callback)

    def texture_update(self):
        self.texture_size = [len(self.text) * self.font_size * 0.5,
                             self.font_size * 1.2]
        for callback in self.bound.get("texture_size", []):
            callback(self, self.texture_size)


class FakeButton(FakeWidget):
    """タイトルのボタン。フォントを写す相手になる。"""

    def __init__(self, text):
        FakeWidget.__init__(self)
        self.text = text
        self.font_name = FONT


class FakeImage(FakeWidget):
    """タイトルのロゴ。**フォントを持たない**（写す相手として選ばれない）。"""


class FakeDefaultFontLabel(FakeWidget):
    """Kivy の既定フォントのまま置かれたウィジェット。

    既定（Roboto）に日本語は無いので、これを写しても写さなかったのと同じ。
    見つけても採らず、先を見に行くこと。
    """

    def __init__(self):
        FakeWidget.__init__(self)
        self.font_name = "Roboto"


class FakeWindow(object):
    children = []


class FakeStartScreen(FakeWidget):
    """`scripts.hud.hud_start:StartScreen`。組み上がると子が並んでいる。"""

    def __init__(self, *callbacks, **kwargs):
        FakeWidget.__init__(self, **kwargs)
        # 先に足したものほど後ろに残る＝フォントを探すとき最初に当たる。
        # 既定フォントのウィジェットとロゴを、本物のボタンより手前に置いてある。
        self.add_widget(FakeDefaultFontLabel())
        self.add_widget(FakeImage())
        for text in ("開始する", "続きから", "世界", "設定"):
            self.add_widget(FakeButton(text))
        self.built = list(self.children)


PRISTINE_INIT = FakeStartScreen.__init__


def install_fake_kivy():
    """mod は Kivy を関数の中で遅延 import する。sys.modules に偽物を置く。"""
    kivy = types.ModuleType("kivy")
    uix = types.ModuleType("kivy.uix")
    label_mod = types.ModuleType("kivy.uix.label")
    label_mod.Label = FakeLabel
    core = types.ModuleType("kivy.core")
    window_mod = types.ModuleType("kivy.core.window")
    window_mod.Window = FakeWindow
    for name, module in (("kivy", kivy), ("kivy.uix", uix),
                         ("kivy.uix.label", label_mod), ("kivy.core", core),
                         ("kivy.core.window", window_mod)):
        sys.modules[name] = module


def install_fake_game():
    module = types.ModuleType("scripts.hud.hud_start")
    module.StartScreen = FakeStartScreen
    sys.modules.setdefault("scripts", types.ModuleType("scripts"))
    sys.modules.setdefault("scripts.hud", types.ModuleType("scripts.hud"))
    sys.modules["scripts.hud.hud_start"] = module


# ---------------------------------------------------------------- 偽ローダ
class FakeCtx(object):
    """`ctx.wrap` は本物と同じ形（第1引数 orig、第2引数 self）で当てる。"""

    def __init__(self, generation="gen1"):
        self.version = "9.9.9"
        self.generation = generation
        self.api = 1
        self.logs = []
        self.errors = []
        self.ready = []

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
            return func
        return decorate

    def patch(self, target, **kw):
        raise AssertionError("this mod should not use ctx.patch")

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def on_ready(self, fn, *, key=None, delay=0.0, force=False):
        self.ready.append((key, fn))
        return True


def load_mod():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


def install(generation="gen1", **settings):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。"""
    FakeStartScreen.__init__ = PRISTINE_INIT
    sys.modules.pop(MOD_NAME, None)
    module = load_mod()
    for name, value in settings.items():
        if not hasattr(module, name):
            raise SystemExit("設定 {!r} がモジュールに無い".format(name))
        setattr(module, name, value)
    ctx = FakeCtx(generation)
    module.apply(ctx)
    return module, ctx


def labels_of(screen, mod):
    return [child for child in screen.children
            if getattr(child, mod.LABEL_ATTR, None) is not None]


def run():
    install_fake_kivy()
    install_fake_game()
    # `status()` が数える相手（この注入で当たった mod）。
    ml._state["mods"] = {"100_a": "ok", "101_b": "ok", "102_c": "apply-error"}

    # -- 注入した時点で出ている画面 -----------------------------------------
    print("\n[注入時]")
    open_screen = FakeStartScreen()
    root = FakeWidget()
    root.add_widget(open_screen)
    FakeWindow.children = [root]

    mod, ctx = install()
    check("apply() だけでは画面に触らない", not labels_of(open_screen, mod))
    for _key, fn in ctx.ready:
        fn()
    found = labels_of(open_screen, mod)
    check("もう出ているタイトル画面を辿って足す", len(found) == 1, open_screen.children)
    check("文字はローダの版", found and found[0].text == "modloader v9.9.9",
          found and found[0].text)
    check("ゲームが組んだ子はそのまま残る",
          open_screen.children[1:] == open_screen.built, open_screen.children)
    check("足すのはいちばん手前（描画は最後）",
          bool(found) and open_screen.children[0] is found[0])
    check("on_ready のキーに世代が入っている",
          bool(ctx.ready) and ctx.ready[0][0].endswith(":gen1"), ctx.ready)
    check("握り潰された例外は無い", not ctx.errors, ctx.errors)

    # -- タイトルへ戻ったときに組み直される画面 -----------------------------
    print("\n[組み直し]")
    fresh = FakeStartScreen()
    check("後から組まれた画面にも足す", len(labels_of(fresh, mod)) == 1, fresh.children)
    check("ゲームの子は全部揃っている", len(fresh.children) == len(fresh.built) + 1)

    # -- 注入し直し ---------------------------------------------------------
    print("\n[重ね置き]")
    mod, ctx = install(generation="gen2")
    for _key, fn in ctx.ready:
        fn()
    for _key, fn in ctx.ready:
        fn()                      # 当て直し（TECH.md §3.4）で2回流れても
    check("注入し直してもラベルは1枚", len(labels_of(open_screen, mod)) == 1,
          open_screen.children)
    check("ゲームの子は増えも減りもしない",
          open_screen.children[1:] == open_screen.built, open_screen.children)

    # -- 文字 ---------------------------------------------------------------
    print("\n[文字]")
    mod, ctx = install(TEXT="MOD {mods} 本 / v{version}")
    text = labels_of(FakeStartScreen(), mod)[0].text
    check("{mods} は当たった mod の本数（失敗したものは数えない）",
          text == "MOD 2 本 / v9.9.9", text)

    # -- 置き場所と見た目 ---------------------------------------------------
    print("\n[置き場所]")
    from instantale_modloader import ui

    mod, ctx = install(CORNER="左下", FONT_SIZE=20, ALPHA=0.3)
    corner = labels_of(FakeStartScreen(), mod)[0]
    check("設定した隅が pos_hint に出る", corner.pos_hint == ui.CORNERS["左下"],
          corner.pos_hint)
    check("濃さと大きさが効く",
          corner.color == (1.0, 1.0, 1.0, 0.3) and corner.font_size == 20.0,
          (corner.color, corner.font_size))

    mod, ctx = install(CORNER="まんなか")
    fallback = labels_of(FakeStartScreen(), mod)[0]
    check("知らない隅の名前は右上へ落ちる", fallback.pos_hint == ui.CORNERS["右上"],
          fallback.pos_hint)

    print("\n[フォントと大きさ]")
    mod, ctx = install()
    label = labels_of(FakeStartScreen(), mod)[0]
    check("画面のウィジェットからフォントを写す（Kivy の既定は採らない）",
          label.font_name == FONT, label.font_name)
    check("箱を文字の大きさに合わせる（既定の 100x100 で隅に寄らない）",
          label.size == label.texture_size, (label.size, label.texture_size))

    # -- Kivy が引けない環境 -------------------------------------------------
    print("\n[無傷]")
    saved = sys.modules.pop("kivy.uix.label")
    sys.modules["kivy.uix.label"] = None      # import が失敗する形
    try:
        bare = FakeStartScreen()
        check("Label が作れなければ何も足さない", not labels_of(bare, mod), bare.children)
        check("ゲームの子はそのまま", bare.children == bare.built)
    finally:
        sys.modules["kivy.uix.label"] = saved

    print()
    if failures:
        print("FAILED: {} / {}".format(len(failures), checked))
        for name in failures:
            print("  - " + name)
        return 1
    print("all {} checks passed".format(checked))
    return 0


if __name__ == "__main__":
    sys.exit(run())
