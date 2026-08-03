# -*- coding: utf-8 -*-
"""118_batch_message_render をゲーム抜きで通す。

    python tools/test_batch_message_render.py
"""

import importlib.util
import io
import json
import math
import os
import re
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


MOD_PATH = find_mod("_batch_message_render")

FAILURES = []
COLORS = []
RECTANGLES = []


def check(name, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


class FakeClock(object):
    def __init__(self):
        self.pending = []
        self.now = 0

    def schedule_once(self, callback, delay):
        self.pending.append((self.now + delay, callback))

    def tick(self):
        if not self.pending:
            return
        next_at = min(when for when, _callback in self.pending)
        self.now = next_at
        pending = [
            callback for when, callback in self.pending if when <= self.now]
        self.pending = [
            (when, callback)
            for when, callback in self.pending if when > self.now]
        for callback in pending:
            callback(0)

    def drain(self, limit=100, maximum_delay=1):
        count = 0
        while (self.pending and count < limit
               and min(when for when, _callback in self.pending) - self.now
               <= maximum_delay):
            self.tick()
            count += 1
        return count

    def advance(self, seconds):
        self.now += seconds
        while True:
            pending = [
                callback for when, callback in self.pending if when <= self.now]
            self.pending = [
                (when, callback)
                for when, callback in self.pending if when > self.now]
            if not pending:
                return
            for callback in pending:
                callback(0)


class FakeAnimation(object):
    starts = []
    cancelled = []

    def __init__(self, opacity, duration):
        self.opacity = opacity
        self.duration = duration

    @classmethod
    def cancel_all(cls, label):
        cls.cancelled.append(label)

    def start(self, label):
        # 開始時点の opacity を控えてから動かす。どこから明るくなるかが
        # 「本文全体が一度消えるか否か」を決めるので、そこを検査したい。
        self.starts.append((label, self.duration, label.opacity))
        label.opacity = self.opacity


CLOCK = FakeClock()


class CanvasPart(object):
    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False


class Canvas(object):
    def __init__(self):
        self.after = CanvasPart()


class Color(object):
    def __init__(self, *rgba):
        self.rgba = rgba
        COLORS.append(self)


class Rectangle(object):
    def __init__(self, pos, size):
        self.pos = pos
        self.size = size
        RECTANGLES.append(self)


def text_height(text, font_size, line_height, width):
    if not text:
        return 0
    columns = max(1, int(float(width) / float(font_size)))
    lines = 0
    for part in text.split("\n"):
        lines += max(1, int(math.ceil(float(len(part)) / columns)))
    return lines * float(font_size) * float(line_height)


class CoreLabel(object):
    def __init__(self, text, font_size, text_size, line_height, **_kwargs):
        height = text_height(text, font_size, line_height, text_size[0])
        self.texture = types.SimpleNamespace(size=(text_size[0], height))

    def refresh(self):
        pass


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    clock = types.ModuleType("kivy.clock")
    animation = types.ModuleType("kivy.animation")
    graphics = types.ModuleType("kivy.graphics")
    core = types.ModuleType("kivy.core")
    core_text = types.ModuleType("kivy.core.text")
    utils = types.ModuleType("kivy.utils")
    clock.Clock = CLOCK
    animation.Animation = FakeAnimation
    graphics.Color = Color
    graphics.Rectangle = Rectangle
    core_text.Label = CoreLabel
    utils.escape_markup = lambda text: (
        text.replace("&", "&amp;").replace("[", "&bl;").replace("]", "&br;"))
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = clock
    sys.modules["kivy.animation"] = animation
    sys.modules["kivy.graphics"] = graphics
    sys.modules["kivy.core"] = core
    sys.modules["kivy.core.text"] = core_text
    sys.modules["kivy.utils"] = utils


class Label(object):
    def __init__(self):
        self.text = ""
        self.markup = False
        self.opacity = 1
        self.font_size = 10
        self.font_name = "fake"
        self.line_height = 1.0
        self.text_size = (40, None)
        self.texture_size = (40, 10)
        self.height = 10

    def set_text(self, text):
        self.text = text
        height = text_height(
            text, self.font_size, self.line_height, self.text_size[0])
        self.texture_size = (self.text_size[0], height)
        self.height = height

    def texture_update(self):
        visible = re.sub(r"\[/?color(?:=#[0-9a-fA-F]+)?\]", "", self.text)
        height = text_height(
            visible, self.font_size, self.line_height, self.text_size[0])
        self.texture_size = (self.text_size[0], height)
        self.height = height


class ScrollView(object):
    def __init__(self, height=20):
        self.x = 10
        self.y = 20
        self.width = 40
        self.height = height
        self.canvas = Canvas()
        self.scroll_history = []
        self._scroll_y = 0

    @property
    def pos(self):
        return (self.x, self.y)

    @property
    def size(self):
        return (self.width, self.height)

    @property
    def scroll_y(self):
        return self._scroll_y

    @scroll_y.setter
    def scroll_y(self, value):
        self._scroll_y = value
        self.scroll_history.append(value)


class HUD(object):
    def __init__(self):
        self.text_display = Label()
        self.scroll_view = ScrollView()

    def update_label_height(self):
        self.text_display.height = self.text_display.texture_size[1]


class InstantaleApp(object):
    """本物の流し込みの骨格を写す（`out/text_viewport.log` の実測に基づく）。

    肝は **待ち行列から取り除くのは鎖の最後の呼び出しだ** という点。実機のログでは
    `to_add_text_list` が 1 のまま流し込みが続き、0 に減るのは `index == len(context)`
    の呼び出しと同時だった。ここを写しておかないと、行列を放置するモッドが
    このテストを全通してしまう ― 実際に一度そうなった。
    """

    def __init__(self):
        self.hud = HUD()
        self.display_text = ""
        self.is_adding_text = False
        self.to_add_text_list = []
        self.immediate_calls = []
        self.original_calls = []
        self.message_separator = ""

    def add_text(self, context):
        self.to_add_text_list.append(context)

    def process_text_queue(self, dt):
        if self.is_adding_text or not self.to_add_text_list:
            return
        self.is_adding_text = True
        self.add_text_display(dt, self.to_add_text_list[0], -1)

    def add_text_immediately(self, content):
        self.immediate_calls.append(content)
        if self.display_text:
            self.display_text += self.message_separator
        self.display_text += content
        self.hud.text_display.set_text(self.display_text)

    def add_text_display(self, _dt, context, index=-1):
        self.original_calls.append(index)
        position = index + 1
        if position < len(context):
            self.display_text += context[position]
            self.hud.text_display.set_text(self.display_text)
            return
        if self.to_add_text_list:
            self.to_add_text_list.pop(0)
        self.is_adding_text = False


PRISTINE = InstantaleApp.add_text_display


class FakeCtx(object):
    def __init__(self):
        self.errors = []
        self.wrapped = {}

    def wrap(self, target, **_kwargs):
        def decorate(func):
            original = InstantaleApp.add_text_display

            def wrapper(self, *args, **kwargs):
                return func(original, self, *args, **kwargs)

            InstantaleApp.add_text_display = wrapper
            self.wrapped[target] = wrapper
            return func
        return decorate

    def log(self, _message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)


def load_mod():
    spec = importlib.util.spec_from_file_location("batch_message_render_test", MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(module, ctx):
    InstantaleApp.add_text_display = PRISTINE
    module.apply(ctx)


def install_fake_hud_module():
    """`ui.find_hud` は型で HUD を見分けるので、その型を置いておく。

    **ここを `ui` のモックで済ませてはいけない。** 以前は
    `module.ui.hud_of = lambda app: app.hud` と書いてあり、`ui` に存在しない
    関数を偽物で埋めていたため、実機で `AttributeError` を出して落ちるコードが
    このテストを全通していた（`out/live_crashes.log`）。本物の `find_hud` を
    通せば、同じ種類の取り違えはここで捕まる。
    """
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = HUD
    sys.modules[module.__name__] = module


def run():
    CLOCK.pending = []
    CLOCK.now = 0
    FakeAnimation.starts = []
    FakeAnimation.cancelled = []
    del COLORS[:]
    del RECTANGLES[:]
    install_fake_kivy()
    install_fake_hud_module()
    ctx = FakeCtx()
    mod = load_mod()
    mod.monotonic_time = lambda: CLOCK.now
    install(mod, ctx)
    check("hooked character streaming",
          "__main__:InstantaleApp.add_text_display" in ctx.wrapped)

    app = InstantaleApp()
    text = "一括で表示する本文"
    app.add_text(text)
    app.process_text_queue(0)
    check("uses immediate display once", app.immediate_calls == [text], app.immediate_calls)
    check("hands the finish back to the game",
          app.original_calls == [len(text)], app.original_calls)
    check("the game clears the streaming flag", app.is_adding_text is False,
          app.is_adding_text)
    check("the game empties the queue", app.to_add_text_list == [], app.to_add_text_list)
    check("writes the full text immediately", app.hud.text_display.text == text,
          app.hud.text_display.text)
    check("covers the viewport before the batch becomes visible",
          bool(RECTANGLES) and RECTANGLES[-1].size == app.hud.scroll_view.size
          and COLORS[-1].rgba == mod.OVERLAY_RGBA,
          (RECTANGLES[-1].size, COLORS[-1].rgba))
    CLOCK.tick()
    check("starts at the first page of the new text",
          app.hud.scroll_view.scroll_y == 1,
          app.hud.scroll_view.scroll_history)
    CLOCK.tick()
    check("reveals one rendered line at a time",
          RECTANGLES[-1].size[1] == (
              app.hud.scroll_view.height - app.hud.text_display.font_size),
          RECTANGLES[-1].size)
    CLOCK.drain()
    check("keeps the new text start at the viewport top",
          app.hud.scroll_view.scroll_y == 1
          and RECTANGLES[-1].size == (0, 0)
          and COLORS[-1].rgba == (0, 0, 0, 0),
          (app.hud.scroll_view.scroll_history,
           RECTANGLES[-1].size, COLORS[-1].rgba))
    check("does not rebuild text character by character",
          app.immediate_calls == [text] and app.original_calls == [len(text)])
    check("does not use whole-label fade on the normal path",
          not FakeAnimation.starts, FakeAnimation.starts)

    app = InstantaleApp()
    app.add_text("短文")
    app.process_text_queue(0)
    CLOCK.tick()
    check("a short message only covers its own bottom region",
          RECTANGLES[-1].size == (app.hud.scroll_view.width, 10)
          and app.hud.scroll_view.scroll_y == 0,
          (RECTANGLES[-1].size, app.hud.scroll_view.scroll_history))
    CLOCK.drain()

    # 本文が流れている最中に注入し直すと、こちらが始めていない鎖の続きが飛んでくる。
    # 続きは `add_text_display` 自身が次を積んでいるので、捨てると鎖が途切れて
    # `is_adding_text` が True のまま残り、ゲームが操作を受け付けなくなる。
    app = InstantaleApp()
    app.is_adding_text = True
    app.to_add_text_list = ["注入前から流れていた本文"]
    app.add_text_display(0, "注入前から流れていた本文", 3)
    check("an in-flight stream is handed back to the game",
          app.original_calls == [3] and not app.immediate_calls,
          (app.original_calls, app.immediate_calls))
    check("the game is still allowed to finish it",
          app.is_adding_text is True, app.is_adding_text)

    # 実機で踏んだ不具合そのもの。行列の先頭が取り除かれないと、二通目が永久に
    # 出ないまま一通目が何度も再表示される（`out/text_viewport.log`）。
    app = InstantaleApp()
    app.add_text("一つ目")
    app.process_text_queue(0)
    app.add_text("二つ目")
    app.process_text_queue(0)
    check("a second message still reaches the screen",
          app.immediate_calls == ["一つ目", "二つ目"], app.immediate_calls)
    check("the queue never piles up", app.to_add_text_list == [], app.to_add_text_list)
    CLOCK.drain()
    check("only the newest response controls the overlay",
          RECTANGLES[-1].size == (0, 0)
          and app.hud.scroll_view.scroll_y == 0,
          (RECTANGLES[-1].size, app.hud.scroll_view.scroll_history))
    check("recent messages stay white",
          app.hud.text_display.markup is False
          and app.hud.text_display.text == "一つ目二つ目",
          app.hud.text_display.text)

    # 長文保護の末尾表示では、追跡済みの主人公文の後ろにNPC応答だけが
    # 対応付け不能な末尾として残ることがある。その末尾も追加直後は白くする。
    recent_app = app
    app = InstantaleApp()

    def with_untracked_response(content):
        app.immediate_calls.append(content)
        app.display_text += content
        app.hud.text_display.set_text(content + "\nNPCの返答")

    app.add_text_immediately = with_untracked_response
    app.add_text("主人公の発言")
    app.process_text_queue(0)
    CLOCK.drain()
    check("an untracked trailing response stays white",
          app.hud.text_display.markup is False
          and app.hud.text_display.text == "主人公の発言\nNPCの返答",
          app.hud.text_display.text)

    # 主人公の入力は、表示上はNPC応答より前でも、内部では後から本文ラベルへ
    # 差し込まれることがある。追加順で照合するとNPC応答を既存本文と誤認し、
    # 直後なのに灰色化してしまう。
    app = InstantaleApp()

    def reordered_immediate(content):
        app.immediate_calls.append(content)
        app.display_text += content
        shown = content if len(app.immediate_calls) == 1 else "主人公の発言\nNPCの返答"
        app.hud.text_display.set_text(shown)

    app.add_text_immediately = reordered_immediate
    app.add_text("NPCの返答")
    app.process_text_queue(0)
    CLOCK.drain()
    app.add_text("主人公の発言")
    app.process_text_queue(0)
    CLOCK.drain()
    check("display-order insertion keeps both new messages white",
          app.hud.text_display.text
          == "主人公の発言[color=#808080]\n[/color]NPCの返答",
          app.hud.text_display.text)

    app = recent_app
    CLOCK.advance(mod.FRESH_SECONDS)
    check("idle time does not change message colors",
          app.hud.text_display.markup is False
          and app.hud.text_display.text == "一つ目二つ目",
          app.hud.text_display.text)
    app.add_text("[三つ目]")
    app.process_text_queue(0)
    CLOCK.drain()
    check("a new message grays expired text without interpreting brackets",
          app.hud.text_display.text
          == "[color=#808080]一つ目[/color][color=#808080]二つ目[/color]"
          "&bl;三つ目&br;",
          app.hud.text_display.text)

    app = InstantaleApp()
    app.add_text("先行分")
    app.process_text_queue(0)
    CLOCK.drain()
    CLOCK.advance(15)
    app.add_text("後続分")
    app.process_text_queue(0)
    CLOCK.drain()
    CLOCK.advance(5)
    check("a later segment remains white after the first expires",
          app.hud.text_display.text
          == "[color=#808080]先行分[/color]後続分",
          app.hud.text_display.text)

    app = InstantaleApp()
    app.add_text("再適用前1")
    app.process_text_queue(0)
    CLOCK.drain()
    CLOCK.advance(5)
    app.add_text("再適用前2")
    app.process_text_queue(0)
    CLOCK.drain()
    reloaded_mod = load_mod()
    reloaded_mod.monotonic_time = lambda: CLOCK.now
    install(reloaded_mod, ctx)
    app.add_text("再適用後")
    app.process_text_queue(0)
    CLOCK.drain()
    check("re-applying the mod preserves recent message timestamps",
          app.hud.text_display.markup is False
          and app.hud.text_display.text == "再適用前1再適用前2再適用後",
          app.hud.text_display.text)

    app = InstantaleApp()
    app.message_separator = "\n"
    app.add_text("区切り前")
    app.process_text_queue(0)
    CLOCK.drain()
    CLOCK.advance(15)
    app.add_text("区切り後")
    app.process_text_queue(0)
    CLOCK.drain()
    check("a display-only separator does not gray a later message",
          app.hud.text_display.text
          == "[color=#808080]区切り前[/color][color=#808080]\n[/color]区切り後",
          app.hud.text_display.text)
    CLOCK.advance(5)
    check("idle time leaves separator colors unchanged",
          app.hud.text_display.text
          == "[color=#808080]区切り前[/color][color=#808080]\n[/color]区切り後",
          app.hud.text_display.text)

    app = InstantaleApp()
    app.add_text_immediately = None
    app.add_text("予備")
    app.process_text_queue(0)
    check("falls back when immediate display is unavailable",
          app.original_calls == [-1], app.original_calls)

    FakeAnimation.starts = []
    app = InstantaleApp()

    def transformed_immediate(content):
        app.immediate_calls.append(content)
        app.display_text += content
        app.hud.text_display.set_text("表示側で変換された本文")

    app.add_text_immediately = transformed_immediate
    app.add_text("原文")
    app.process_text_queue(0)
    CLOCK.drain()
    check("falls back to a readable fade when the new range is unknown",
          len(FakeAnimation.starts) == 1
          and FakeAnimation.starts[0][1] == mod.FADE_SECONDS
          and FakeAnimation.starts[0][2] == mod.FADE_FROM,
          FakeAnimation.starts)

    check("no exception was swallowed", not ctx.errors, ctx.errors)

    # リビールは `Clock.schedule_once` で後のフレームへ渡す ＝ `ctx.wrap(safe=True)`
    # の守備範囲の外。ここで投げるとゲームごと落ちるので、コールバック自身が
    # 例外を止め、従来の短いフェードへ落ちることを確かめる。
    app = InstantaleApp()
    original_find_hud = mod.ui.find_hud

    def broken_find_hud(_app):
        raise RuntimeError("HUD lookup failed")

    mod.ui.find_hud = broken_find_hud
    try:
        app.add_text("リビールが壊れても本文は出る")
        app.process_text_queue(0)
        CLOCK.drain()
        reached_the_game = False
    except Exception:
        reached_the_game = True
    finally:
        mod.ui.find_hud = original_find_hud
    check("a failing reveal never reaches the game", not reached_the_game)
    check("the failing fallback is logged instead", len(ctx.errors) == 1, ctx.errors)
    check("the text is displayed even if the reveal fails",
          app.hud.text_display.text == "リビールが壊れても本文は出る",
          app.hud.text_display.text)

    print()
    if FAILURES:
        print("FAILED: {}".format(", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
