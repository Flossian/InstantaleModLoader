# -*- coding: utf-8 -*-
"""ゲームを動かす道具（`tools/drive/`）の手順を、偽のゲームの中で通す。

    python tools/tests/test_drive_steps.py

  drive.py  … KEEP_OPEN の手順が投げても <done> が届く（待つ側が --wait いっぱい待たない）
  buy.py    … 同名の品を前から持っている・棚に2つあるときも、買えたかを数で答える
  sell.py   … 品が無い・店の側に空きが無いときは、例外でなく理由を書いて止まる
  autoplay  … avoid / stop に空の並びを渡すと既定を外せる。渡さなければ既定のまま
  trade.py  … 額の引けない品に段を呼ばない・段の例外は握る（本体の `prices` と同じ回し方）
  toolshot  … 撮るのに失敗しても窓を閉じて終わる（隠した窓を別プロセスで開く）

手順は本番と同じく `lib.py` の後に同じ名前空間で読む。kivy は偽物を差し込む。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
DRIVE_DIR = os.path.join(TOOLS_DIR, "drive")
RUNTIME_DIR = os.path.normpath(os.path.join(TOOLS_DIR, os.pardir, "runtime"))
for path in (TOOLS_DIR, DRIVE_DIR, RUNTIME_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽の kivy
class FakeClock(object):
    """予約を順に流す。待ちの秒は見ない。"""

    def __init__(self):
        self.queue = []

    def schedule_once(self, fn, _timeout=0):
        self.queue.append(fn)

    def pump(self, limit=200):
        count = 0
        while self.queue and count < limit:
            self.queue.pop(0)(0)
            count += 1


class Widget(object):
    def __init__(self, children=(), text=None):
        self.children = []
        self.parent = None
        self.text = text
        self.x = self.y = 0
        self.width = self.height = 65
        self.center = (32, 32)
        for child in children:
            self.add(child)

    def add(self, child):
        child.parent = self
        self.children.append(child)

    def to_window(self, x, y):
        return (x, y)


class InventoryItem(Widget):
    def __init__(self, item):
        Widget.__init__(self)
        self.item_instance = item


class Grid(Widget):
    def __init__(self, obtainer, free=True):
        Widget.__init__(self)
        self.obtainer = obtainer
        self.rows = self.cols = 4
        self.free = free

    def is_valid_placement(self, gx, gy, w, h):
        return self.free

    def place_new_item(self, *args):
        return None


class Button(Widget):
    def __init__(self, text, on_press):
        Widget.__init__(self, text=text)
        self.on_press = on_press


class Item(object):
    def __init__(self, name, obtainer, attributes=None):
        self.name = name
        self.obtainer = obtainer
        self.attributes = dict(attributes or {})
        self.width_slots = self.height_slots = 1


CLOCK = FakeClock()
SCREEN = Widget()


def install_kivy():
    names = ("kivy", "kivy.base", "kivy.clock", "kivy.core", "kivy.core.window",
             "kivy.input", "kivy.input.providers", "kivy.input.providers.mouse")
    for name in names:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["kivy.base"].EventLoop = object()
    sys.modules["kivy.clock"].Clock = CLOCK
    sys.modules["kivy.core.window"].Window = SCREEN
    sys.modules["kivy.input.providers.mouse"].MouseMotionEvent = object


install_kivy()


def step_ns(args, app):
    """`drive.py` と同じく lib.py の後に手順を読む名前空間。"""
    said = []
    ns = {"__name__": "drive", "ARGS": args, "say": said.append}
    with io.open(os.path.join(DRIVE_DIR, "lib.py"), encoding="utf-8") as fh:
        exec(fh.read(), ns)
    ns["app"] = lambda: app
    ns["_touch"] = lambda points: None
    ns["click"] = lambda widget: widget.on_press()
    return ns, said


def run_step(name, ns, said):
    with io.open(os.path.join(DRIVE_DIR, name), encoding="utf-8") as fh:
        exec(fh.read(), ns)
    ns["main"](said.append)
    CLOCK.pump()
    return said


def fake_app():
    return types.SimpleNamespace(player=types.SimpleNamespace(gold=500),
                                 is_button_enabled=True, is_adding_text=False,
                                 is_popup_window_opened=False)


def has_traceback(said):
    return any("Traceback" in str(line) for line in said)


# ---------------------------------------------------------------- drive.py
print("[drive.py] KEEP_OPEN の手順が投げても <done> が届く")
import drive                                             # noqa: E402

tmp = tempfile.mkdtemp(prefix="drive_steps_")
try:
    lib = os.path.join(tmp, "lib.py")
    script = os.path.join(tmp, "step.py")
    result = os.path.join(tmp, "step.out")
    io.open(lib, "w", encoding="utf-8").close()
    with io.open(script, "w", encoding="utf-8") as fh:
        fh.write("KEEP_OPEN = True\n\ndef main(say):\n    say('start')\n"
                 "    raise RuntimeError('boom')\n")
    payload = drive.PAYLOAD.format(result=result, script=script, lib=lib,
                                   args=json.dumps({}))
    check("注入の中身は ASCII のまま", payload.isascii())
    exec(payload, {"__name__": "__main__"})
    CLOCK.pump()
    with io.open(result, encoding="utf-8") as fh:
        text = fh.read()
    check("例外の中身が結果に残る", "boom" in text, text[-120:])
    check("**<done> で終わる**", text.rstrip().endswith("<done>"), text[-60:])
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- buy.py
print("[buy.py] 買えたかを数で答える")
app = fake_app()
shop = object()
shelf_grid, mine_grid = Grid(shop), Grid(app.player)
shelf_items = [InventoryItem(Item("薬草", shop)), InventoryItem(Item("薬草", shop))]
mine_items = [InventoryItem(Item("薬草", app.player))]
trade = Widget([shelf_grid, mine_grid] + shelf_items + mine_items)


def buy_press():
    # 棚の1つが手持ちへ移り、代金が減る
    shelf_items[0].item_instance.obtainer = app.player
    app.player.gold -= 30


SCREEN.children[:] = []
SCREEN.add(trade)
SCREEN.add(Button("買う", buy_press))
ns, said = step_ns({"item": "薬草"}, app)
run_step("buy.py", ns, said)
after = [line for line in said if str(line).startswith("after:")]
check("例外にならない", not has_traceback(said), said)
check("**前から持っていて棚に2つあっても、買えたと分かる**",
      after and "bought True" in after[0], after)
check("前後の数を並べる", after and "player has 1 -> 2" in after[0]
      and "shop has 2 -> 1" in after[0], after)


# ---------------------------------------------------------------- sell.py
print("[sell.py] 売れないときは理由を書いて止まる")
for label, item_name, free in (("品が無い", "無い品", True),
                               ("店の側に空きが無い", "古い剣", False)):
    app = fake_app()
    shop = object()
    grids = [Grid(shop, free=free), Grid(app.player)]
    SCREEN.children[:] = []
    SCREEN.add(Widget(grids + [InventoryItem(Item("古い剣", app.player))]))
    pressed = []
    SCREEN.add(Button("売る", lambda: pressed.append(True)))
    ns, said = step_ns({"item": item_name}, app)
    run_step("sell.py", ns, said)
    check("{}: 例外にならない".format(label), not has_traceback(said), said)
    check("{}: 理由を書いて止まる".format(label),
          any(str(line).startswith("stop:") for line in said) and said[-1] == "<done>", said)
    check("{}: 「売る」を押さない".format(label), not pressed)


# ---------------------------------------------------------------- autoplay.py
print("[autoplay.py] avoid / stop は空にできる")
real_ui = None
for args, want in (({"avoid": [], "steps": 1}, True), ({"steps": 1}, False)):
    app = fake_app()
    ns, said = step_ns(args, app)
    pressed = []
    real_ui = real_ui or ns["ui"]
    ns["ui"] = types.SimpleNamespace(walk_widgets=real_ui.walk_widgets,
                                     current_quest_id=lambda _app: None)
    ns["choices"] = lambda: ["逃げる"]
    ns["settled"] = lambda: True
    ns["press_choice"] = lambda text: pressed.append(text) or True
    SCREEN.children[:] = []
    run_step("autoplay.py", ns, said)
    if want:
        check("**avoid に [] を渡すと既定の避ける語を外す**", pressed == ["逃げる"], said)
    else:
        check("avoid を渡さなければ既定の避ける語のまま", not pressed, said)

app = fake_app()
ns, said = step_ns({"stop": [], "steps": 1}, app)
ns["ui"] = types.SimpleNamespace(walk_widgets=real_ui.walk_widgets,
                                 current_quest_id=lambda _app: None)
ns["choices"] = lambda: ["会話する"]
ns["settled"] = lambda: True
pressed = []
ns["press_choice"] = lambda text: pressed.append(text) or True
run_step("autoplay.py", ns, said)
check("stop に [] を渡すと既定の止まる語を外す", pressed == ["会話する"], said)


# ---------------------------------------------------------------- trade.py
print("[trade.py] 段の回し方は本体と同じ")
from instantale_modloader import prices as loader_prices  # noqa: E402

app = fake_app()
SCREEN.children[:] = []
SCREEN.add(InventoryItem(Item("薬草", app.player, {loader_prices.SELL: 100})))


def double(item, key, price):
    return price * 2


def broken(item, key, price):
    raise RuntimeError("layer failed")


saved_registry = getattr(sys, loader_prices._ITEM_ATTR, None)
setattr(sys, loader_prices._ITEM_ATTR, {
    "base": None, "ledger": loader_prices._Ledger(),
    "adjust": [("999_double", double, False, None), ("999_broken", broken, False, None)]})
try:
    ns, said = step_ns({"do": "prices"}, app)
    with io.open(os.path.join(DRIVE_DIR, "trade.py"), encoding="utf-8") as fh:
        exec(fh.read(), ns)
    raised = None
    try:
        ns["sell_prices"](said.append)
    except Exception as exc:
        raised = exc
    check("**式の無い品でも段が None で呼ばれない**（ゲームの額から始める）", raised is None, raised)
    line = [l for l in said if str(l).startswith("P ")]
    check("段の結果を並べる", line and "now 200" in line[0], said)
    check("段の例外は握って次へ", line and "999_broken:failed" in line[0], said)
finally:
    if saved_registry is None:
        delattr(sys, loader_prices._ITEM_ATTR)
    else:
        setattr(sys, loader_prices._ITEM_ATTR, saved_registry)


# ---------------------------------------------------------------- toolshot.py
print("[toolshot.py] 撮るのに失敗しても窓を閉じて終わる")
tmp = tempfile.mkdtemp(prefix="toolshot_")
try:
    mod_dir = os.path.join(tmp, "999_x")
    os.makedirs(mod_dir)
    with io.open(os.path.join(mod_dir, "tool.py"), "w", encoding="utf-8") as fh:
        # 隠した窓に1枚だけタブを持つ道具。入口は `main(argv)`、`build_window()` は引数なしの形（343 と同じ）
        fh.write("import tkinter as tk\nfrom tkinter import ttk\n\n"
                 "def build_window():\n"
                 "    root = tk.Tk()\n    root.withdraw()\n"
                 "    nb = ttk.Notebook(root)\n    nb.add(ttk.Frame(nb), text='tab')\n"
                 "    nb.pack()\n    return root\n\n"
                 "def main(argv):\n"
                 "    open('called.txt', 'w').write(repr(argv))\n"
                 "    build_window().mainloop()\n    return 0\n")
    runner = os.path.join(tmp, "run.py")
    with io.open(runner, "w", encoding="utf-8") as fh:
        fh.write("import sys\n"
                 "sys.path.insert(0, {tools!r})\nsys.path.insert(0, {drive!r})\n"
                 "import check_tool_screens as cts\n"
                 "cts.tools_of = lambda only='': [('999_x', {mod!r}, 'tool.py', 'x')]\n"
                 "import toolshot\n"
                 "sys.argv = ['toolshot', '999', 'no-such-tab', '--out', {out!r}]\n"
                 "toolshot.main()\n".format(tools=TOOLS_DIR, drive=DRIVE_DIR, mod=mod_dir,
                                            out=os.path.join(tmp, "shot.png")))
    try:
        done = subprocess.run([sys.executable, runner], capture_output=True, timeout=30)
        finished, code, err = True, done.returncode, done.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        finished, code, err = False, None, ""
    check("**無いタブを頼んでも窓を閉じて終わる**（固まらない）", finished, "30 秒で終わらなかった")
    check("撮れなかったら 0 以外で終わる", finished and code not in (0, None), (code, err[-200:]))
    marker = os.path.join(mod_dir, "called.txt")
    called = io.open(marker, encoding="utf-8").read() if os.path.isfile(marker) else None
    check("道具の main(argv) を空の並びで呼ぶ（build_window の引数の形に依らない）", called == "[]", (called, err[-200:]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\n失敗 {} 件".format(len(failures)))
for name in failures:
    print("  - " + name)
if failures:
    sys.exit(1)
print("すべて通った")
