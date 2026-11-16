# -*- coding: utf-8 -*-
"""ローダの `title_version`（タイトル画面の版の1行）をゲーム抜きで通す。

    python tools/tests/test_title_version.py

偽の Kivy（Label / Window）と偽の `scripts.hud.hud_start:StartScreen` を差し込み、
次を確認する。

  注入時     … もう出ているタイトル画面をウィンドウから辿って見つけ、その場で足す
  組み直し   … 後から組まれるタイトル画面にも足す（タイトルへ戻る経路）
  重ね置き   … 注入し直しても、ラベルは1枚のまま置き換わる
  積み       … 同じ隅の他の MOD の1行（`136_`）と同じ箱に縦に積み、この行が縁側
  フォント   … 画面のウィジェットからフォント名を写す（日本語が豆腐にならない）
  大きさ     … ラベルの箱を文字の大きさに合わせる（既定の 100x100 のままにしない）
  無傷       … ゲームが組んだ子には触らない。Kivy が引けなければ何もしない
  片付け     … MOD だった頃の `126_ui_title_version` のフォルダ・順序ファイルの行・設定値を外す
"""
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402
from instantale_modloader import title_version, ui   # noqa: E402

FONT = "NotoSansJP-Regular.ttf"
OLD = "126_ui_title_version"

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
    """`Label` のうち、ここで触る分だけ。文字の箱の配り方も本物に似せる。"""

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


class FakeBoxLayout(FakeWidget):
    """`BoxLayout`。縦に積む箱（`ui.corner_label` が隅ごとに1つ作る）。"""


class FakeButton(FakeWidget):
    """タイトルのボタン。フォントを写す相手になる。"""

    def __init__(self, text):
        FakeWidget.__init__(self)
        self.text = text
        self.font_name = FONT


class FakeImage(FakeWidget):
    """タイトルのロゴ。**フォントを持たない**（写す相手として選ばれない）。"""


class FakeDefaultFontLabel(FakeWidget):
    """Kivy の既定フォントのまま置かれたウィジェット。見つけても採らず、先を見に行くこと。"""

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
        self.add_widget(FakeDefaultFontLabel())
        self.add_widget(FakeImage())
        for text in ("開始する", "続きから", "世界", "設定"):
            self.add_widget(FakeButton(text))
        self.built = list(self.children)


PRISTINE_INIT = FakeStartScreen.__init__


def install_fake_kivy():
    """部品は Kivy を関数の中で遅延 import する。sys.modules に偽物を置く。"""
    kivy = types.ModuleType("kivy")
    uix = types.ModuleType("kivy.uix")
    label_mod = types.ModuleType("kivy.uix.label")
    label_mod.Label = FakeLabel
    box_mod = types.ModuleType("kivy.uix.boxlayout")
    box_mod.BoxLayout = FakeBoxLayout
    core = types.ModuleType("kivy.core")
    window_mod = types.ModuleType("kivy.core.window")
    window_mod.Window = FakeWindow
    for name, module in (("kivy", kivy), ("kivy.uix", uix),
                         ("kivy.uix.label", label_mod), ("kivy.uix.boxlayout", box_mod),
                         ("kivy.core", core),
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
        raise AssertionError("title_version should not use ctx.patch")

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def on_ready(self, fn, *, key=None, delay=0.0, force=False):
        self.ready.append((key, fn))
        return True


def install(generation="gen1"):
    """注入をやり直す。**ラッパーを重ねない**（本物は世代管理で置き換える）。"""
    FakeStartScreen.__init__ = PRISTINE_INIT
    ctx = FakeCtx(generation)
    title_version.install(ctx)
    return ctx


def labels_of(screen, attr=None):
    """画面の隅の箱に積まれた版の1行。"""
    attr = attr or title_version.LABEL_ATTR
    found = []
    for holder in [screen] + list(screen.children):
        found.extend(child for child in holder.children
                     if getattr(child, attr, None) is not None)
    return found


def check_drop_absorbed():
    print("\n[片付け]")
    root = tempfile.mkdtemp(prefix="iml_absorbed_")
    try:
        mods = os.path.join(root, "runtime", "mods")
        os.makedirs(os.path.join(mods, OLD))
        os.makedirs(os.path.join(mods, "100_keep"))
        for name in (OLD, "100_keep"):
            ml.write_json(os.path.join(mods, name, "mod.json"), {"entry": "x.py"})
        ml.write_json(os.path.join(mods, ml.ORDER_NAME),
                      {"order": ["001_a", OLD, "100_keep"], "disabled": [OLD, "100_keep"]})
        os.makedirs(os.path.join(root, "settings"))
        store = os.path.join(root, "settings", "mod_settings.json")
        ml.write_json(store, {OLD: {"CORNER": "左下"}, "100_keep": {"X": 1}})

        dropped = ml.drop_renamed(mods)
        check("取り込んだ MOD の名前を返す", OLD in dropped, dropped)
        check("旧フォルダが消える", not os.path.isdir(os.path.join(mods, OLD)))
        check("他の MOD のフォルダは残る", os.path.isdir(os.path.join(mods, "100_keep")))
        with open(os.path.join(mods, ml.ORDER_NAME), encoding="utf-8") as fh:
            order = json.load(fh)
        check("順序ファイルの行が消え、他は並びのまま",
              order["order"] == ["001_a", "100_keep"], order["order"])
        check("切っていた印も消え、他の MOD の印は残る",
              order["disabled"] == ["100_keep"], order["disabled"])
        with open(store, encoding="utf-8") as fh:
            values = json.load(fh)
        check("設定値が消え、他の MOD の設定は残る",
              values == {"100_keep": {"X": 1}}, values)
        check("2回目は何もしない", ml.drop_renamed(mods) == [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run():
    install_fake_kivy()
    install_fake_game()

    # -- 注入した時点で出ている画面 -----------------------------------------
    print("\n[注入時]")
    open_screen = FakeStartScreen()
    root = FakeWidget()
    root.add_widget(open_screen)
    FakeWindow.children = [root]

    ctx = install()
    check("install() だけでは画面に触らない", not labels_of(open_screen))
    for _key, fn in ctx.ready:
        fn()
    found = labels_of(open_screen)
    check("もう出ているタイトル画面を辿って足す", len(found) == 1, open_screen.children)
    check("文字はローダの版", found and found[0].text == "modloader v9.9.9",
          found and found[0].text)
    check("右上に置く", bool(found) and found[0].parent.pos_hint == ui.CORNERS["右上"],
          found and found[0].parent.pos_hint)
    check("ゲームが組んだ子はそのまま残る",
          open_screen.children[1:] == open_screen.built, open_screen.children)
    check("足すのはいちばん手前の隅の箱（描画は最後）",
          bool(found) and open_screen.children[0] is found[0].parent)
    check("on_ready のキーに世代が入っている",
          bool(ctx.ready) and ctx.ready[0][0].endswith(":gen1"), ctx.ready)
    check("握り潰された例外は無い", not ctx.errors, ctx.errors)

    # -- タイトルへ戻ったときに組み直される画面 -----------------------------
    print("\n[組み直し]")
    fresh = FakeStartScreen()
    check("後から組まれた画面にも足す", len(labels_of(fresh)) == 1, fresh.children)
    check("ゲームの子は全部揃っている", len(fresh.children) == len(fresh.built) + 1)

    # -- 注入し直し ---------------------------------------------------------
    print("\n[重ね置き]")
    ctx = install(generation="gen2")
    for _key, fn in ctx.ready:
        fn()
    for _key, fn in ctx.ready:
        fn()                      # 当て直し（TECH.md §3.4）で2回流れても
    check("注入し直してもラベルは1枚", len(labels_of(open_screen)) == 1,
          open_screen.children)
    check("ゲームの子は増えも減りもしない",
          open_screen.children[1:] == open_screen.built, open_screen.children)

    print("\n[フォントと大きさ]")
    install()
    label = labels_of(FakeStartScreen())[0]
    check("画面のウィジェットからフォントを写す（Kivy の既定は採らない）",
          label.font_name == FONT, label.font_name)
    check("箱を文字の大きさに合わせる（既定の 100x100 で隅に寄らない）",
          label.size == label.texture_size, (label.size, label.texture_size))
    check("濃さと大きさは MOD だった頃の既定値",
          label.color == (1.0, 1.0, 1.0, 0.55) and label.font_size == 14.0,
          (label.color, label.font_size))

    # -- 同じ隅の他の MOD の1行と積む -----------------------------------------
    print("\n[積み]")
    install()
    screen = FakeStartScreen()
    other = ui.corner_label(screen, "_instantale_other", "LLM: X", corner="右上", order=10)
    mine = labels_of(screen)[0]
    check("同じ隅の1行は同じ箱に入る", other.parent is mine.parent, screen.children)
    check("版の行が縁側（上の隅なら上＝先に足した側）",
          mine.parent.children[-1] is mine, [c.text for c in mine.parent.children])
    check("右の隅は右寄せ", mine.pos_hint == {"right": 1}, mine.pos_hint)
    box = mine.parent
    check("箱は2行を収める大きさ",
          box.size == (max(mine.size[0], other.size[0]), mine.size[1] + other.size[1]), box.size)
    ui.corner_label(screen, title_version.LABEL_ATTR, "again", corner="右上")
    check("置き直しても1枚のまま、他の MOD の行は残る",
          len(labels_of(screen)) == 1 and other.parent is box, box.children)
    ui.remove_corner_label(screen, "_instantale_other")
    check("他の MOD が外すと版の行だけ残る", len(box.children) == 1, box.children)

    # -- Kivy が引けない環境 -------------------------------------------------
    print("\n[無傷]")
    saved = sys.modules.pop("kivy.uix.label")
    sys.modules["kivy.uix.label"] = None      # import が失敗する形
    try:
        bare = FakeStartScreen()
        check("Label が作れなければ何も足さない", not labels_of(bare), bare.children)
        check("ゲームの子はそのまま", bare.children == bare.built)
    finally:
        sys.modules["kivy.uix.label"] = saved

    check_drop_absorbed()

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
