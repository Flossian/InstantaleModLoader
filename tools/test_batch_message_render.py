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
# 色の指定が付いたままの文字列を、markup を切った状態で描いた回数。
# **1回でもあれば、その瞬間 `[color=#808080]` が文字として画面に出ている**
# （実機で見えた ― 場面転換のときに一瞬タグが出る）。
TAG_LEAKS = []


def uncolored(markup):
    """色の指定を外した見た目の文字列。"""
    return re.sub(r"\[/?color(?:=#[0-9a-fA-F]+)?\]", "", markup)


def grayed(markup):
    """灰色にされている部分だけを集める。"""
    return "".join(re.findall(
        r"\[color=#808080\](.*?)\[/color\]", markup, re.S))


def packed(text):
    """空白を落とす（ゲームが足す改行の有無で検査が揺れないように）。"""
    return "".join(text.split())


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


class FakeWindow(object):
    """クリックの見張りの結び先。

    **結び直しで手が積み重ならないこと**を検査するために、外した手が
    ちゃんと消えるところまで写している（注入し直すたびに1本増えると、
    1クリックで複数の本文が打ち切られる）。
    """

    def __init__(self):
        self.handlers = []

    def bind(self, **kwargs):
        for name, callback in kwargs.items():
            self.handlers.append((name, callback))

    def unbind(self, **kwargs):
        for name, callback in kwargs.items():
            if (name, callback) in self.handlers:
                self.handlers.remove((name, callback))

    def click(self):
        """画面が押された。**戻り値は捨てない** ― 真を返す手があると、
        Kivy はそこで配送を止めてボタンが押せなくなる。"""
        swallowed = False
        for name, callback in list(self.handlers):
            if name == "on_touch_down" and callback(self, object()):
                swallowed = True
        return swallowed


WINDOW = FakeWindow()


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
    core_window = types.ModuleType("kivy.core.window")
    core_window.Window = WINDOW
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = clock
    sys.modules["kivy.animation"] = animation
    sys.modules["kivy.graphics"] = graphics
    sys.modules["kivy.core"] = core
    sys.modules["kivy.core.text"] = core_text
    sys.modules["kivy.core.window"] = core_window
    sys.modules["kivy.utils"] = utils


class Label(object):
    def __init__(self):
        self.text = ""
        self._markup = False
        self.opacity = 1
        self.font_size = 10
        self.font_name = "fake"
        self.line_height = 1.0
        self.text_size = (40, None)
        self.texture_size = (40, 10)
        self.height = 10
        # 寸法を計算し直した回数。**Kivy はテクスチャの作り直しを次のフレーム
        # へ回す**ので、1文字ずつなら1フレームの遅れは見えないが、まとめて
        # 足したときは高さが古いままになり、増えた行が枠から切り落とされる
        # （実機で踏んだ ― 本文もラベルも正しいのに「クリックした時点で
        # 打ち切られる」）。飛ばした後に計算し直したかをここで数える。
        self.texture_updates = 0

    @property
    def markup(self):
        return self._markup

    @markup.setter
    def markup(self, value):
        # **色を外すなら、素の本文に戻してから。** タグの付いた文字列を
        # 残したまま markup を切ると、その状態で描かれた瞬間にタグが文字として
        # 出る。順序の間違いはここでしか捕まらない（描く側は次のフレーム）。
        if not value and "[color=" in self.text:
            TAG_LEAKS.append(self.text[:40])
        self._markup = value

    def set_text(self, text):
        self.text = text
        height = text_height(
            text, self.font_size, self.line_height, self.text_size[0])
        self.texture_size = (self.text_size[0], height)
        self.height = height

    def texture_update(self):
        self.texture_updates += 1
        if not self.markup and "[color=" in self.text:
            TAG_LEAKS.append(self.text[:40])
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


# 正本へ書いたとき、ゲームが画面を塗り直すか。**実機では塗り直さなかった**
# （`out/modloader.log`: 正本は 57552 → 57569 に伸びたのに、画面は打ちかけの
# 7文字のまま ＝ NPC の名前だけが残った）。どちらでも本文が出ることを求める。
REPAINT_ON_WRITE = [True]

# 打ち出しの最中に、ゲームが本文どおりでない文字を混ぜる版か。**実機がこれ
# だった**（正本の末尾が本文の先頭からの切り出しと文字単位で一致しない ―
# `out/modloader.log` の `tail=` に本文には無い改行が混ざっていた）。位置合わせを
# 「本文が始まった時点の長さ」に置き換える動機そのものなので、写しておく。
INSERT_BREAKS = [False]

# 塗り直しが「渡された値」ではなくゲーム自身の控えを見る版か。ここまで外れて
# いても、最後にラベルへ足して見た目だけは揃える（`ensure_shown`）。
IGNORE_VALUE = [False]


class HUD(object):
    """本文の正本（`display_text`）を持っているのは HUD 側。

    `117_message_text_integrity` が実機で通している経路がこれ（あちらは塗り直し
    の中で `self.display_text` を読んでいる）。**app 側にこの名前は無い**ので、
    app から読もうとする MOD はここで落ちる ― 実際に落ちていた（クリックでの
    打ち切りが毎回見送られていた）。
    """

    def __init__(self):
        self.text_display = Label()
        self.scroll_view = ScrollView()
        self._display_text = ""
        self.height_updates = 0

    @property
    def display_text(self):
        return self._display_text

    @display_text.setter
    def display_text(self, value):
        self._display_text = value
        if REPAINT_ON_WRITE[0]:
            self.update_display_text(self, value)

    def update_label_height(self):
        self.height_updates += 1
        self.text_display.height = self.text_display.texture_size[1]

    def update_display_text(self, _instance=None, value=None):
        """本文が変わるたびにゲームがラベルを塗り直す経路。

        Kivy のプロパティ監視で呼ばれるので、**1文字進むたびに生の本文が
        ラベルへ入り直す** ＝ MOD が付けた色はそのたびに消える。逐次表示の
        色付けはここを包んで直しているので、その形を写しておく。

        `IGNORE_VALUE` は「ゲームが渡された値ではなく自分の控えから塗る」版。
        そこまで外れていても本文が画面に出ることを求める（最後の砦の検査）。
        """
        if IGNORE_VALUE[0]:
            return
        self.text_display.set_text(value)


class InstantaleApp(object):
    """本物の流し込みの骨格を写す（`out/text_viewport.log` の実測に基づく）。

    肝は **待ち行列から取り除くのは鎖の最後の呼び出しだ** という点。実機のログでは
    `to_add_text_list` が 1 のまま流し込みが続き、0 に減るのは `index == len(context)`
    の呼び出しと同時だった。ここを写しておかないと、行列を放置する MOD でも
    このテストを全通してしまう。

    1文字ぶんの続きは `add_text_display` 自身が次を予約する（実機と同じ）。
    予約を写しておかないと、鎖を止める側（クリックでの打ち切り）が検査できない。
    """

    def __init__(self):
        self.hud = HUD()
        self.is_adding_text = False
        self.to_add_text_list = []
        self.immediate_calls = []
        self.original_calls = []
        self.message_separator = ""
        self.base_text = ""     # いまの本文が始まる前の本文
        self.typed = 0          # ゲームが自分で数えている「打った文字数」

    @property
    def shown_text(self):
        """検査用の読み口。**`app.display_text` は生やさない**（ゲームに無い）。"""
        return self.hud.display_text

    def add_text(self, context):
        self.to_add_text_list.append(context)

    def process_text_queue(self, dt):
        if self.is_adding_text or not self.to_add_text_list:
            return
        self.is_adding_text = True
        self.add_text_display(dt, self.to_add_text_list[0], -1)

    def add_text_immediately(self, content):
        self.immediate_calls.append(content)
        text = self.hud.display_text
        if text:
            text += self.message_separator
        self.hud.display_text = text + content

    def add_text_display(self, _dt, context, index=-1):
        self.original_calls.append(index)
        position = index + 1
        if index == -1:
            self.base_text = self.hud.display_text
            self.typed = 0
        if position < len(context):
            char = context[position]
            if INSERT_BREAKS[0] and char == "。":
                char += chr(10)     # ゲームが自分で足す改行
            self.hud.display_text = self.hud.display_text + char
            self.typed = position + 1
            CLOCK.schedule_once(
                lambda dt: self.add_text_display(dt, context, position),
                TEXT_SPEED)
            return
        if REBUILD_ON_FINISH[0]:
            # 終端の呼び出しが、ゲーム自身の控えから本文を組み直す版。
            # **終端より先に本文を書く MOD は、ここで書いた分を失う**
            # （クリックした瞬間に本文が縮んで見える）。実機で何が起きるかは
            # 分からないので、どちらでも結果が同じになることを求める。
            self.hud.display_text = self.base_text + context[:self.typed]
        if self.to_add_text_list:
            self.to_add_text_list.pop(0)
        self.is_adding_text = False


# 1文字ぶんの間隔（実機の `app.text_speed` の既定は 0.07）。
TEXT_SPEED = 0.07

# 終端の呼び出しの振る舞いを切り替える（上の `add_text_display` を参照）。
REBUILD_ON_FINISH = [False]

PRISTINE = InstantaleApp.add_text_display
PRISTINE_UPDATE = HUD.update_display_text


class FakeCtx(object):
    # 包む相手は1つではないので、対象名から持ち主を引く（対象名の綴りを
    # 間違えた MOD がテストを全通しないように、知らない名前は落とす）。
    TARGETS = {
        "__main__:InstantaleApp.add_text_display":
            (lambda: InstantaleApp, "add_text_display"),
        "scripts.hud.new_hud:InstanTaleHUD.update_display_text":
            (lambda: HUD, "update_display_text"),
    }

    def __init__(self):
        self.errors = []
        self.wrapped = {}
        self.logs = []

    def wrap(self, target, **_kwargs):
        owner, name = self.TARGETS[target]
        owner = owner()

        def decorate(func):
            original = getattr(owner, name)

            def wrapper(self, *args, **kwargs):
                return func(original, self, *args, **kwargs)

            setattr(owner, name, wrapper)
            self.wrapped[target] = wrapper
            return func
        return decorate

    def log(self, message, level="INFO"):
        self.logs.append((level, message))

    def log_exc(self, message):
        self.errors.append(message)


def load_mod():
    spec = importlib.util.spec_from_file_location("batch_message_render_test", MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(module, ctx, batch_mode="click", fresh_mode="seconds"):
    InstantaleApp.add_text_display = PRISTINE
    HUD.update_display_text = PRISTINE_UPDATE
    module.BATCH_MODE = batch_mode
    module.FRESH_MODE = fresh_mode
    # 打ち切り直後の見張りは読み取りだけの計測で、Clock に予約を置く。
    # 「クリックの後にティックが残っていないこと」の検査と混ざるので切る。
    module.WATCH_AFTER_SKIP = False
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
    del TAG_LEAKS[:]
    install_fake_kivy()
    install_fake_hud_module()
    ctx = FakeCtx()
    mod = load_mod()
    mod.monotonic_time = lambda: CLOCK.now
    install(mod, ctx)
    check("hooked character streaming",
          "__main__:InstantaleApp.add_text_display" in ctx.wrapped)
    check("hooked the repaint that erases the colors",
          "scripts.hud.new_hud:InstanTaleHUD.update_display_text" in ctx.wrapped)

    # -- 逐次表示（既定）----------------------------------------------------
    app = InstantaleApp()
    text = "逐次で流れる本文"
    app.add_text(text)
    app.process_text_queue(0)
    check("the game keeps typing the message out",
          app.immediate_calls == [] and app.hud.text_display.text == text[:1],
          (app.immediate_calls, app.hud.text_display.text))
    CLOCK.tick()
    check("one character per tick", app.hud.text_display.text == text[:2],
          app.hud.text_display.text)
    CLOCK.drain()
    check("the whole message arrives on its own",
          app.hud.text_display.text == text, app.hud.text_display.text)
    check("the game finishes the sequential message",
          app.is_adding_text is False and app.to_add_text_list == []
          and app.original_calls == list(range(-1, len(text))),
          (app.is_adding_text, app.to_add_text_list, app.original_calls))
    check("nothing is left ticking", not CLOCK.pending, CLOCK.pending)

    # -- クリックで打ち切る -------------------------------------------------
    check("a click with no message running is harmless",
          WINDOW.click() is False)

    app = InstantaleApp()
    text = "クリックで一括表示にする本文"
    app.add_text(text)
    app.process_text_queue(0)
    CLOCK.tick()
    CLOCK.tick()
    typed = app.hud.text_display.text
    settle_before = (app.hud.text_display.texture_updates,
                     app.hud.height_updates)
    check("the click is not swallowed", WINDOW.click() is False)
    CLOCK.tick()
    check("the label is measured again after skipping ahead",
          app.hud.text_display.texture_updates > settle_before[0]
          and app.hud.height_updates > settle_before[1],
          (settle_before, app.hud.text_display.texture_updates,
           app.hud.height_updates))
    check("a click shows the rest of the message at once",
          typed == text[:3] and app.hud.text_display.text == text,
          (typed, app.hud.text_display.text))
    # 終端がどの `index` なのかは決め打ちにしない（実測できていない）。
    # 求めるのは「ゲームが本文を終えたこと」だけ。
    check("the game finished the skipped message",
          app.is_adding_text is False and app.to_add_text_list == [],
          (app.original_calls, app.is_adding_text, app.to_add_text_list))
    check("the skipped message is not written twice",
          app.shown_text == text, app.shown_text)
    # 1文字ずつの鎖は1回ごとに次を予約するので、自分で回した後には予約が残る。
    # **それを捨てられていないと、終端を何度も踏んで次の本文が消える。**
    settled_text = app.shown_text
    CLOCK.drain()
    check("the leftover ticks do nothing",
          app.shown_text == settled_text and app.to_add_text_list == []
          and app.is_adding_text is False,
          (app.shown_text, app.to_add_text_list, app.is_adding_text))

    # 打ち切りは1通ぶん。次の本文はまた最初から逐次で流れる。
    app.add_text("次の本文")
    app.process_text_queue(0)
    check("the next message starts typing again",
          app.hud.text_display.text == text + "次", app.hud.text_display.text)
    CLOCK.drain()

    # 打ち出しの最中に改行を足すゲームでの色。控えた本文をそのまま探すと、
    # 見つかる本文と見つからない本文が混ざり、白・灰・白・灰と交互になる
    # （実機で出た症状そのもの）。
    INSERT_BREAKS[0] = True
    install(mod, ctx, "click", "sessions")
    mod.FRESH_SESSIONS = 1
    app = InstantaleApp()
    # 本文そのものが改行を含み、そのうえゲームが別の場所へ改行を足す ＝
    # 控えたままの本文では見つからない（実機で出た症状の再現条件）。
    br = chr(10)
    for message in ("朝の話。" + br + "晴れた。", "昼の話。" + br + "曇った。",
                    "夜の話。" + br + "雨だ。"):
        app.add_text(message)
        app.process_text_queue(0)
        CLOCK.drain()
    shown = app.hud.text_display.text
    check("reshaped messages still gray from the oldest",
          packed(grayed(shown)) == "朝の話。晴れた。昼の話。曇った。", shown)
    check("the newest reshaped message stays white",
          packed(uncolored(shown)) == "朝の話。晴れた。昼の話。曇った。夜の話。雨だ。"
          and "夜の話" not in grayed(shown), shown)
    INSERT_BREAKS[0] = False
    install(mod, ctx)

    # 打ち出しの最中に改行を足すゲーム（実機がこれだった）。本文の先頭からの
    # 切り出しと文字単位で一致しないので、`endswith` での当てずっぽうは効かない。
    INSERT_BREAKS[0] = True
    app = InstantaleApp()
    app.add_text("前の本文。")
    app.process_text_queue(0)
    CLOCK.drain()
    reshaped = "最初の文。次の文が続く。おしまい"
    app.add_text(reshaped)
    app.process_text_queue(0)
    for _ in range(6):
        CLOCK.tick()            # 「。」を1つ越えるまで打たせる
    check("the game really reshaped the text",
          not app.shown_text.endswith(reshaped[:6]), app.shown_text)
    WINDOW.click()
    CLOCK.tick()
    check("a reshaped message is still finished by the click",
          "".join(app.shown_text.split())
          == "".join(("前の本文。" + reshaped).split()), app.shown_text)
    check("the reshaped message is not typed twice",
          app.shown_text.count("おしまい") == 1, app.shown_text)
    INSERT_BREAKS[0] = False

    # 正本へ書いても画面が塗り直されないゲーム（実機がこれだった）。
    REPAINT_ON_WRITE[0] = False
    app = InstantaleApp()
    silent = "「鉄屑の」カイ「その話は聞いた」"
    app.add_text(silent)
    app.process_text_queue(0)
    CLOCK.tick()
    WINDOW.click()
    CLOCK.tick()
    check("the skipped message reaches the screen even without a repaint",
          app.hud.text_display.text == silent, app.hud.text_display.text)
    check("the canonical text is complete too",
          app.shown_text == silent, app.shown_text)

    REPAINT_ON_WRITE[0] = True

    # 終端の呼び出しが本文を組み直すゲームでも、結果は同じでなければならない
    # （先に書いてから終端を渡すと、ここで書いた分が消える）。
    REBUILD_ON_FINISH[0] = True
    app = InstantaleApp()
    app.add_text("先に出ていた本文")
    app.process_text_queue(0)
    CLOCK.drain()
    rebuilt = "組み直されても残る本文"
    app.add_text(rebuilt)
    app.process_text_queue(0)
    CLOCK.tick()
    WINDOW.click()
    CLOCK.tick()
    check("a skip survives a finishing call that rebuilds the text",
          app.shown_text == "先に出ていた本文" + rebuilt, app.shown_text)
    check("the rebuilding game still ends the message",
          app.is_adding_text is False and app.to_add_text_list == [],
          (app.is_adding_text, app.to_add_text_list))
    REBUILD_ON_FINISH[0] = False


    # -- 逐次表示の最中の色 -------------------------------------------------
    app = InstantaleApp()
    app.add_text("[一つ目]")
    app.process_text_queue(0)
    CLOCK.drain()
    check("brackets survive while there is nothing to gray",
          app.hud.text_display.markup is False
          and app.hud.text_display.text == "[一つ目]",
          (app.hud.text_display.markup, app.hud.text_display.text))
    CLOCK.advance(mod.FRESH_SECONDS)
    app.add_text("[二つ目]")
    app.process_text_queue(0)
    check("older text grays from the first character of the next message",
          app.hud.text_display.text == "[color=#808080]&bl;一つ目&br;[/color]&bl;",
          app.hud.text_display.text)
    CLOCK.drain()
    check("the message being typed stays white to the end",
          app.hud.text_display.text
          == "[color=#808080]&bl;一つ目&br;[/color]&bl;二つ目&br;",
          app.hud.text_display.text)

    CLOCK.advance(mod.FRESH_SECONDS)
    app.add_text("三つ目")
    app.process_text_queue(0)
    CLOCK.tick()
    WINDOW.click()
    CLOCK.tick()
    check("a skipped message keeps the colors of the older text",
          app.hud.text_display.text
          == "[color=#808080]&bl;一つ目&br;[/color]"
             "[color=#808080]&bl;二つ目&br;[/color]三つ目",
          app.hud.text_display.text)

    # 注入し直すと、こちらが始めていない鎖の続きが飛んでくる（逐次でも同じ）。
    app = InstantaleApp()
    app.is_adding_text = True
    app.to_add_text_list = ["注入前から流れていた本文"]
    app.add_text_display(0, "注入前から流れていた本文", 3)
    check("an in-flight sequential stream is handed back to the game",
          app.original_calls == [3] and app.is_adding_text is True,
          (app.original_calls, app.is_adding_text))
    CLOCK.pending = []

    # -- セッション数で色を変える -------------------------------------------
    install(mod, ctx, "click", "sessions")
    mod.FRESH_SESSIONS = 1
    app = InstantaleApp()
    for message in ("一つ目", "二つ目", "三つ目"):
        app.add_text(message)
        app.process_text_queue(0)
        CLOCK.drain()
    check("one session of white keeps only the newest message white",
          app.hud.text_display.text
          == "[color=#808080]一つ目[/color][color=#808080]二つ目[/color]三つ目",
          app.hud.text_display.text)

    mod.FRESH_SESSIONS = 2
    started_at = CLOCK.now
    app = InstantaleApp()
    for message in ("一つ目", "二つ目", "三つ目"):
        app.add_text(message)
        app.process_text_queue(0)
        CLOCK.drain()
    check("two sessions of white keep the last two messages white",
          app.hud.text_display.text == "[color=#808080]一つ目[/color]二つ目三つ目",
          app.hud.text_display.text)
    # 秒数で見ていたら、この短さでは1つも灰色にならない ＝ セッション数で
    # 決めていることの裏取り。
    check("sessions do not wait for the clock",
          CLOCK.now - started_at < mod.FRESH_SECONDS,
          CLOCK.now - started_at)

    mod.FRESH_SESSIONS = 0
    app = InstantaleApp()
    app.add_text("追加直後でも灰色")
    app.process_text_queue(0)
    CLOCK.drain()
    check("zero sessions grays the message it belongs to",
          app.hud.text_display.text == "[color=#808080]追加直後でも灰色[/color]",
          app.hud.text_display.text)

    check("re-applying does not stack click watchers",
          len([handler for handler in WINDOW.handlers
               if handler[0] == "on_touch_down"]) == 1, WINDOW.handlers)

    # -- 一括表示 ------------------------------------------------------------
    install(mod, ctx, "always")
    check("the click watcher is dropped when messages are always batched",
          not WINDOW.handlers, WINDOW.handlers)

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
        app.hud.display_text += content
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
        app.hud.display_text += content
        shown = content if len(app.immediate_calls) == 1 else "主人公の発言\nNPCの返答"
        app.hud.text_display.set_text(shown)

    app.add_text_immediately = reordered_immediate
    app.add_text("NPCの返答")
    app.process_text_queue(0)
    CLOCK.drain()
    app.add_text("主人公の発言")
    app.process_text_queue(0)
    CLOCK.drain()
    # 灰色にするものが1つも無いので、ラベルには手を触れない（本文と本文の間の
    # 改行だけを灰色にしても何も見えないのに、組み直しの代金だけ掛かる）。
    check("display-order insertion keeps both new messages white",
          app.hud.text_display.markup is False
          and app.hud.text_display.text == "主人公の発言\nNPCの返答",
          (app.hud.text_display.markup, app.hud.text_display.text))

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
    install(reloaded_mod, ctx, "always")
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
        app.hud.display_text += content
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
    check("no color tag is ever drawn as text", not TAG_LEAKS, TAG_LEAKS[:3])

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
