# -*- coding: utf-8 -*-
"""1114_message_viewport_height をゲーム抜きで通す。

    python tools/test_message_viewport_height.py

実測した構成（ScrollView → 下端を `y` で固定する FloatLayout）で、標準高さの
1.5倍化、下端保持、再描画後の再適用、再注入時の累積防止を確認する。
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


def mod_path():
    folder = os.path.join(MODS_DIR, "1114_message_viewport_height")
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


MOD = mod_path()
FAILURES = []


def check(name, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


def close(actual, expected):
    return (isinstance(actual, (int, float))
            and isinstance(expected, (int, float))
            and abs(actual - expected) < 0.0001)


class Widget(object):
    def __init__(self, parent=None):
        self.parent = parent
        self.children = []
        self.canvas = FakeCanvas()
        self.bindings = {}

    def add_widget(self, widget):
        widget.parent = self
        self.children.append(widget)

    def bind(self, **callbacks):
        self.bindings.update(callbacks)

    def to_window(self, _x, _y):
        return self.pos


class FakeCanvasBefore(object):
    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False


class FakeCanvas(object):
    def __init__(self):
        self.before = FakeCanvasBefore()
        self.after = FakeCanvasBefore()


class FakeColor(object):
    def __init__(self, *rgba):
        self.rgba = rgba


class FakeRectangle(object):
    def __init__(self, pos, size):
        self.pos = pos
        self.size = size


class FakeLine(object):
    def __init__(self, points, width):
        self.points = points
        self.width = width


class FakeWindow(object):
    height = 720


class FakeButton(Widget):
    def __init__(self, **kwargs):
        super(FakeButton, self).__init__()
        for name, value in kwargs.items():
            setattr(self, name, value)
        self.width, self.height = self.size
        self.x, self.y = (0, 0)
        self.on_release = None

    def bind(self, **callbacks):
        super(FakeButton, self).bind(**callbacks)
        self.on_release = callbacks.get("on_release", self.on_release)

    def release(self):
        self.on_release(self)


class FakeHUD(object):
    def __init__(self):
        self.viewport_parent = Widget()
        self.viewport_parent.height = 720
        self.viewport_parent.pos = (0, 0)
        self.viewport = Widget(parent=self.viewport_parent)
        self.viewport.size_hint = (0.53, 0.62)
        self.viewport.pos_hint = {"center_x": 0.5, "y": 0.36}
        self.viewport.pos = (270, 109)
        self.viewport.size = (611, 187)
        self.scroll_view = Widget(parent=self.viewport)
        self.scroll_view.pos = self.viewport.pos
        self.scroll_view.size = self.viewport.size
        self.text_display = Widget(parent=self.scroll_view)
        self.calls = []

    def _reset_viewport(self, name):
        self.calls.append(name)
        self.viewport.size_hint = (0.53, 0.62)
        self.viewport.pos_hint = {"center_x": 0.5, "y": 0.36}

    def update_text_display_size(self, *args):
        self._reset_viewport("size")

    def _on_scroll_resize(self, instance, value):
        self._reset_viewport("scroll")

    def _on_text_input_layout_resize(self, *args):
        self._reset_viewport("input")

    def update_display_text(self, instance, value):
        self._reset_viewport("display")


PRISTINE = {
    name: getattr(FakeHUD, name)
    for name in ("update_text_display_size", "_on_scroll_resize",
                 "_on_text_input_layout_resize", "update_display_text")
}


class FakeCtx(object):
    def __init__(self):
        self.wrapped = {}
        self.errors = []

    def wrap(self, target, **_kwargs):
        def decorate(func):
            _module, qualified = target.split(":")
            _cls, method = qualified.split(".")
            original = getattr(FakeHUD, method)

            def wrapper(self, *args, **kwargs):
                return func(original, self, *args, **kwargs)

            setattr(FakeHUD, method, wrapper)
            self.wrapped[target] = wrapper
            return func
        return decorate

    def log(self, _message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_message_viewport_height", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    graphics = types.ModuleType("kivy.graphics")
    graphics.Color = FakeColor
    graphics.Rectangle = FakeRectangle
    graphics.Line = FakeLine
    uix = types.ModuleType("kivy.uix")
    button = types.ModuleType("kivy.uix.button")
    button.Button = FakeButton
    core = types.ModuleType("kivy.core")
    window = types.ModuleType("kivy.core.window")
    window.Window = FakeWindow
    sys.modules["kivy"] = kivy
    sys.modules["kivy.graphics"] = graphics
    sys.modules["kivy.uix"] = uix
    sys.modules["kivy.uix.button"] = button
    sys.modules["kivy.core"] = core
    sys.modules["kivy.core.window"] = window


def install(module, ctx):
    for name, method in PRISTINE.items():
        setattr(FakeHUD, name, method)
    module.apply(ctx)


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    sys.modules[module.__name__] = module

    mod = load_mod()
    ctx = FakeCtx()
    install(mod, ctx)
    hud = FakeHUD()
    hud.update_text_display_size()

    expected = {
        "scripts.hud.new_hud:InstanTaleHUD.update_text_display_size",
        "scripts.hud.new_hud:InstanTaleHUD._on_scroll_resize",
        "scripts.hud.new_hud:InstanTaleHUD._on_text_input_layout_resize",
        "scripts.hud.new_hud:InstanTaleHUD.update_display_text",
    }
    check("all layout refresh paths are wrapped", set(ctx.wrapped) == expected)
    check("height hint is scaled to 1.5 times",
          close(hud.viewport.size_hint[1], 0.62 * 1.5), hud.viewport.size_hint)
    check("the lower edge hint stays unchanged",
          close(hud.viewport.pos_hint["y"], 0.36), hud.viewport.pos_hint)
    button = getattr(hud.viewport, "__instantale_message_viewport_toggle__")
    background = getattr(hud.scroll_view, "__instantale_message_viewport_background__")
    check("a top-right toggle is attached to the viewport",
          button.parent is hud.viewport
          and button.pos_hint == {"right": 0.985, "top": 0.985}, button.pos_hint)
    icon = getattr(button, "__instantale_message_viewport_icon__")
    check("toggle uses four vector lines instead of font text",
          len(icon["lines"]) == 4 and not hasattr(button, "text"), icon)
    check("the background is translucent black at opacity 0.5",
          background["color"].rgba == (0, 0, 0, 0.5),
          background["color"].rgba)

    button.release()
    check("toggle expands to the window top minus 10px",
          hud.viewport.size_hint[1] is None
          and close(hud.viewport.height, 720 - 720 * 0.36 - 10),
          (hud.viewport.size_hint, hud.viewport.height))
    check("toggle preserves the lower edge at full height",
          close(hud.viewport.pos_hint["y"], 0.36), hud.viewport.pos_hint)
    FakeWindow.height = 800
    hud.viewport_parent.height = 800
    hud._on_scroll_resize(hud.scroll_view, (1280, 800))
    check("fullscreen height is recalculated after layout resize",
          close(hud.viewport.height, 800 - 800 * 0.36 - 10), hud.viewport.height)
    expanded = [line.points for line in icon["lines"]]
    button.release()
    collapsed = [line.points for line in icon["lines"]]
    check("toggle icon changes while full height is active", expanded != collapsed, expanded)
    check("toggle returns to the normal height",
          close(hud.viewport.size_hint[1], 0.62 * 1.5), hud.viewport.size_hint)

    hud._on_scroll_resize(hud.scroll_view, (100, 100))
    hud._on_text_input_layout_resize()
    hud.update_display_text(hud, "本文")
    check("every game layout reset is re-applied",
          close(hud.viewport.size_hint[1], 0.62 * 1.5), hud.viewport.size_hint)
    check("original methods still run",
          hud.calls == ["size", "scroll", "scroll", "input", "display"], hud.calls)

    mod.HEIGHT_SCALE = 2.0
    install(mod, FakeCtx())
    hud.update_text_display_size()
    check("re-injection does not multiply an already scaled hint",
          close(hud.viewport.size_hint[1], 0.62 * 2.0), hud.viewport.size_hint)
    check("no resize error was swallowed", not ctx.errors, ctx.errors)

    print()
    if FAILURES:
        print("FAILED: {}".format(", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
