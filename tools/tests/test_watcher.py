# -*- coding: utf-8 -*-
"""起動の見張り（`tools/watcher.py`）を、プロセスと注入を差し替えて通す。

    python tools/tests/test_watcher.py

  結果     … 注入の戻り値 0 は INJECTED、INJECT_PENDING は PENDING（失敗ではない）、
             それ以外と例外は FAILED
  入れ替え … ログの入れ替えは注入の直前。準備が整わずに諦めた pid では入れ替えない。
             同時に見つけた複数の pid でも1回だけ
  前から   … 見張りを始めた時点で動いていたゲームにも注入するが、ログは入れ替えない
             （注入済みかもしれない。そのプレイの記録を途中で分けない）
  --once   … 注入に失敗・準備が整わない・ゲームが無い、は終了コード 1。保留は 0。
             頼まれた注入なので入れ替える（injector.py と同じ）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import injector                                        # noqa: E402
import watcher                                         # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Stop(Exception):
    pass


def run(polls, results=None, ready=lambda pid: True, once=False):
    """`polls` の順に「動いている pid」を見せて main を回す。起きたことの並びと終了コード。"""
    events = []
    results = results or {}
    state = {"poll": 0}

    def find_processes(_exe):
        polls_left = polls[min(state["poll"], len(polls) - 1)]
        return [(pid, "instantale.exe") for pid in polls_left]

    def inject(pid, _payload, dry_run=False):
        events.append(("inject", pid))
        rc = results.get(pid, 0)
        if isinstance(rc, Exception):
            raise rc
        return rc

    def sleep(_seconds):
        state["poll"] += 1
        if state["poll"] >= len(polls):
            raise KeyboardInterrupt

    def wait_until_ready(pid, timeout=None):
        return ready(pid)

    saved = (injector.find_processes, injector.inject, injector.rotate_logs,
             injector.use_utf8_console, watcher.wait_until_ready, watcher.log,
             time.sleep, sys.argv)
    injector.find_processes = find_processes
    injector.inject = inject
    injector.rotate_logs = lambda *a, **k: events.append(("rotate",))
    injector.use_utf8_console = lambda: None
    watcher.wait_until_ready = wait_until_ready
    watcher.log = lambda msg: None
    time.sleep = sleep
    sys.argv = ["watcher.py"] + (["--once"] if once else [])
    try:
        rc = watcher.main()
    finally:
        (injector.find_processes, injector.inject, injector.rotate_logs,
         injector.use_utf8_console, watcher.wait_until_ready, watcher.log,
         time.sleep, sys.argv) = saved
    return events, rc


print("[結果]")
saved_inject = injector.inject
saved_log = watcher.log
watcher.log = lambda msg: None
try:
    for label, rc, want in (("0 は注入済み", 0, watcher.INJECTED),
                            ("保留は失敗ではない", injector.INJECT_PENDING, watcher.PENDING),
                            ("-1 は失敗", -1, watcher.FAILED),
                            ("例外も失敗", OSError("boom"), watcher.FAILED)):
        def fake(pid, payload, dry_run=False, rc=rc):
            if isinstance(rc, Exception):
                raise rc
            return rc
        injector.inject = fake
        check(label, watcher.inject_pid(1) == want, watcher.inject_pid(1))
finally:
    injector.inject = saved_inject
    watcher.log = saved_log

print("[見張り]")
events, rc = run([[100], [100, 200], [200, 300, 400], [300, 400]])
check("前から動いていた pid は入れ替えずに注入、後から来た pid は入れ替えてから注入",
      events == [("inject", 100), ("rotate",), ("inject", 200),
                 ("rotate",), ("inject", 300), ("inject", 400)], events)
check("Ctrl-C で 0", rc == 0, rc)
events, rc = run([[], [500], []], ready=lambda pid: False)
check("準備が整わなかった pid では入れ替えも注入もしない", events == [], events)
events, rc = run([[], [600, 700]], ready=lambda pid: pid == 700)
check("同時に来た2つのうち整った方の直前で1回だけ入れ替える",
      events == [("rotate",), ("inject", 700)], events)

print("[--once]")
events, rc = run([[100]], once=True)
check("注入できたら 0（頼まれた注入なので入れ替える）",
      rc == 0 and events == [("rotate",), ("inject", 100)], (rc, events))
events, rc = run([[100]], results={100: injector.INJECT_PENDING}, once=True)
check("保留は 0", rc == 0, rc)
events, rc = run([[100]], results={100: -1}, once=True)
check("注入に失敗したら 1", rc == 1, rc)
events, rc = run([[100]], results={100: OSError("denied")}, once=True)
check("注入で例外なら 1", rc == 1, rc)
events, rc = run([[100]], ready=lambda pid: False, once=True)
check("準備が整わなければ 1", rc == 1 and events == [], (rc, events))
events, rc = run([[]], once=True)
check("ゲームが無ければ 1", rc == 1, rc)

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
