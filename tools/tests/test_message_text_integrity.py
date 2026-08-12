# -*- coding: utf-8 -*-
"""117_message_text_integrity をゲーム抜きで通す。

    python tools/tests/test_message_text_integrity.py
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


MOD_PATH = find_mod("_message_text_integrity")

FAILURES = []
PREFIX = "案内: "
VISIBLE_CHARS = 100


def check(name, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


class FakeLabel(object):
    def __init__(self):
        self.text = ""
        self.texture_updates = 0

    def texture_update(self):
        self.texture_updates += 1


class FakeHUD(object):
    def __init__(self, has_label=True):
        self.display_text = ""
        self.source_text = None
        self.height_updates = 0
        if has_label:
            self.text_display = FakeLabel()

    def update_display_text(self, instance, value):
        self.display_text = self.source_text if self.source_text is not None else value
        if hasattr(self, "text_display"):
            self.text_display.text = PREFIX + value[:VISIBLE_CHARS]

    def update_label_height(self):
        self.height_updates += 1

    def show(self, text):
        self.update_display_text(self, text)


PRISTINE = FakeHUD.update_display_text


class FakeClock(object):
    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, _timeout=0):
        self.pending.append(callback)

    def tick(self):
        pending, self.pending = self.pending, []
        for callback in pending:
            callback(0)


CLOCK = FakeClock()


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    clock = types.ModuleType("kivy.clock")
    clock.Clock = CLOCK
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = clock


class FakeCtx(object):
    def __init__(self):
        self.errors = []
        self.wrapped = {}

    def wrap(self, target, **_kwargs):
        def decorate(func):
            original = FakeHUD.update_display_text

            def wrapper(self_, *args, **kwargs):
                return func(original, self_, *args, **kwargs)

            FakeHUD.update_display_text = wrapper
            self.wrapped[target] = wrapper
            return func
        return decorate

    def log(self, _message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)


def load_mod():
    spec = importlib.util.spec_from_file_location("message_text_integrity_test", MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(module, ctx):
    FakeHUD.update_display_text = PRISTINE
    module.apply(ctx)


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    sys.modules[module.__name__] = module

    ctx = FakeCtx()
    mod = load_mod()
    install(mod, ctx)
    check("hooked update_display_text",
          "scripts.hud.new_hud:InstanTaleHUD.update_display_text" in ctx.wrapped)

    hud = FakeHUD()
    hud.show("短文")
    long_text = "長文" * (mod.DISPLAY_CHARS // 2 + 100)
    bounded = PREFIX + mod.TRUNCATED_NOTICE + long_text[-mod.DISPLAY_CHARS:]
    hud.show(long_text)
    check("limits the display to the latest text",
          hud.text_display.text == bounded, repr(hud.text_display.text))
    check("leaves the game's display text unchanged",
          hud.display_text == long_text, repr(hud.display_text))
    check("height recalculation is deferred", hud.height_updates == 0,
          hud.height_updates)
    CLOCK.tick()
    check("height recalculation follows display limiting", hud.height_updates == 1,
          hud.height_updates)
    # テクスチャの作り直しは Kivy 自身が（テキストを変えた時点で）次のフレームに
    # 予約する。ここで自分でも呼ぶと1文字ごとに二度手間になり、実機で 15ms x 1回
    # ぶん打ち出しが遅くなる（VERIFICATION_LOG.md §2.34）。
    check("does not rebuild the texture itself (kivy already does)",
          hud.text_display.texture_updates == 0, hud.text_display.texture_updates)

    hud = FakeHUD()
    hud.show("短文")
    hud.source_text = long_text
    hud.update_display_text(hud, "短縮された描画引数")
    check("uses the game's display text as the canonical source",
          hud.text_display.text == bounded, repr(hud.text_display.text))
    CLOCK.tick()

    hud = FakeHUD()
    hud.show("開始")
    before = len(CLOCK.pending)
    typed = "逐次更新でも短い本文はそのまま表示する。"
    for index in range(1, len(typed) + 1):
        hud.show(typed[:index])
    check("incremental updates keep short text unchanged",
          hud.text_display.text == PREFIX + typed, repr(hud.text_display.text))
    check("short incremental updates do not recompute height",
          len(CLOCK.pending) - before == 0, len(CLOCK.pending) - before)
    CLOCK.tick()

    hud.show("")
    next_text = "画面を切り替えた後の新しい長文です。" * 3
    hud.show(next_text)
    check("clearing the text does not retain older content",
          "逐次更新" not in hud.text_display.text
          and next_text in hud.text_display.text, repr(hud.text_display.text))

    missing = FakeHUD(has_label=False)
    missing.show(long_text)
    check("a HUD without the target label is left alone", missing.display_text == long_text)
    check("no exception was swallowed", not ctx.errors, ctx.errors)

    print()
    if FAILURES:
        print("FAILED: {}".format(", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
