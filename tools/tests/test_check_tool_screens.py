# -*- coding: utf-8 -*-
"""設定画面の撮影検査（`tools/check_tool_screens.py`）の後始末を、窓を開かずに通す。

    python tools/tests/test_check_tool_screens.py

  起動 … 道具の出力をパイプのまま読まずに詰まらせない。落ちた道具は終了コードで分かる
  窓   … 窓の記憶の往復の途中で撮るのに失敗しても、開いた窓は必ず閉じ、gui.json を戻す

撮影そのもの（実際に窓を開く）は手で走らせる検査なので、ここでは窓まわりを偽物に替える。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import check_tool_screens as cts                       # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("[起動] 出力の多い道具でも詰まらない")
with tempfile.TemporaryDirectory() as tmp:
    with io.open(os.path.join(tmp, "tool.py"), "w", encoding="utf-8") as fh:
        # パイプの受け皿（数 KB〜64KB）を超える量を書いてから落ちる道具
        fh.write("import sys\n"
                 "sys.stdout.write('o' * 300000)\n"
                 "sys.stderr.write('e' * 300000 + 'boom')\n"
                 "sys.exit(3)\n")
    saved_timeout = cts.OPEN_TIMEOUT
    cts.OPEN_TIMEOUT = 5
    try:
        cts.launch(tmp, "tool.py", sys.executable)
        got = None
    except RuntimeError as exc:
        got = str(exc)
    finally:
        cts.OPEN_TIMEOUT = saved_timeout
    check("落ちた道具は終了コードで知らせる（時間切れにならない）",
          got is not None and got.startswith("終了コード 3"), got and got[:80])


print("[窓] 撮るのに失敗しても窓は閉じる")
calls = {"launch": 0, "shut": 0}
view = {"zoomed": False}


def fake_launch(mod_dir, entry, exe):
    calls["launch"] += 1
    # 2回目は覚えた最大化のまま開く
    view["zoomed"] = calls["launch"] == 2
    return (object(), 100 + calls["launch"], "道具")


def fake_window_size(hwnd):
    return (1600, 900) if view["zoomed"] else (800, 600)


class FakeUser32(object):
    def ShowWindow(self, hwnd, cmd):
        view["zoomed"] = cmd == cts.SW_MAXIMIZE


def fake_capture(hwnd, path):
    raise RuntimeError("PrintWindow failed")


with tempfile.TemporaryDirectory() as tmp:
    gui = os.path.join(tmp, "gui.json")
    original = {"game_path": "C:/game/Instantale.exe", "tool_window": {}}
    with io.open(gui, "w", encoding="utf-8") as fh:
        json.dump(original, fh)

    def fake_shut(proc, hwnd):
        # 道具は閉じるときに窓の記憶を書く（最大化と、最大化前の寸法）
        calls["shut"] += 1
        with io.open(gui, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg.setdefault("tool_window", {})["999_x"] = {"maximized": True,
                                                       "geometry": "800x600+10+10"}
        with io.open(gui, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    saved = {name: getattr(cts, name) for name in
             ("GUI_JSON", "launch", "window_size", "user32", "capture", "shut", "SETTLE")}
    cts.GUI_JSON = gui
    cts.launch = fake_launch
    cts.window_size = fake_window_size
    cts.user32 = FakeUser32()
    cts.capture = fake_capture
    cts.shut = fake_shut
    cts.SETTLE = 0
    escaped = None
    print("  （次の「FAIL  開き直して撮る」は撮影をわざと失敗させたもの）")
    try:
        cts.check_window_memory([("999_x", tmp, "tool.py", "道具")])
    except Exception as exc:
        escaped = exc
    finally:
        for name, value in saved.items():
            setattr(cts, name, value)
    check("撮るのに失敗しても検査は止まらない", escaped is None, escaped)
    check("**開いた窓は全部閉じる**", calls["shut"] == calls["launch"] == 2, calls)
    check("撮れなかったことは失敗として残る", "開き直して撮る" in cts.failures, cts.failures)
    with io.open(gui, encoding="utf-8") as fh:
        check("gui.json は元に戻る", json.load(fh) == original)


print("\n失敗 {} 件".format(len(failures)))
for name in failures:
    print("  - " + name)
if failures:
    sys.exit(1)
print("すべて通った")
