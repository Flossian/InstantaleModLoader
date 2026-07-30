#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instantale の起動を監視して、自動で mod ローダを注入する。

mod はゲームのインタプリタの中で動いているので、ゲームを終了すると一緒に消える。
つまり起動するたびに注入し直す必要がある。これを忘れると、素のゲームが動いて
何も記録されないまま、そのプレイ時間が無駄になる。
このスクリプトはその取りこぼしを防ぐためのもの。

新しいプロセスを見つけたら、注入する前に2つの条件が揃うのを待つ。

  1. python310.dll が読み込まれ、Python の初期化が終わっていること
     （リモートスレッドで Py_IsInitialized を直接呼んで確認する）
  2. 目に見えるウィンドウが存在すること

2 は「Kivy が立ち上がって __main__ の実行が終わった」ことの実用的な目安。
mod は __main__ や kivy.input.providers にパッチを当てるので、これより早く
注入しても対象がまだ存在しない。

使い方:
    python watcher.py                # Ctrl-C で終了するまで監視し続ける
    python watcher.py --interval 3   # 監視の間隔を 3 秒にする（既定は 2 秒）
    python watcher.py --once         # 今動いているゲームに注入して終了する
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import os
import sys
import time
from ctypes import wintypes

import injector
import logrotate

LOG_PATH = os.path.join(injector.OUT_DIR, "watcher.log")

READY_TIMEOUT = 180.0     # 初回起動はモデルの読み込みで時間がかかるので長めに取る
READY_POLL = 1.0


def log(msg: str) -> None:
    """画面とログファイルの両方に出す。"""
    line = "[{}] {}".format(datetime.datetime.now().isoformat(timespec="seconds"), msg)
    print(line, flush=True)
    try:
        os.makedirs(injector.OUT_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass      # ログの失敗で監視を止めない


# --------------------------------------------------------------------------
# 「このプロセスはもうウィンドウを出しているか」の判定
# --------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def has_visible_window(pid: int) -> bool:
    found = False

    def callback(hwnd, _lparam):
        nonlocal found
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found = True
            return False  # False を返すと、そこで列挙が打ち切られる
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def wait_until_ready(pid: int, timeout: float = READY_TIMEOUT) -> bool:
    """Python の初期化とウィンドウの出現が揃うまで待つ。"""
    deadline = time.monotonic() + timeout
    reported_interp = False
    while time.monotonic() < deadline:
        if not injector.find_processes(injector.TARGET_EXE):
            return False  # 待っている間にゲームが終了した
        if injector.interpreter_ready(pid):
            # 段階が進んだことは1回だけ知らせる。毎秒出すと読めなくなる。
            if not reported_interp:
                log(f"  pid {pid}: interpreter initialised, waiting for a window")
                reported_interp = True
            if has_visible_window(pid):
                return True
        time.sleep(READY_POLL)
    return False


# --------------------------------------------------------------------------
def inject_pid(pid: int) -> bool:
    """1つのプロセスに注入して、結果をログに出す。例外は外に投げない。"""
    payload = injector.make_bootstrap(injector.RUNTIME_DIR, injector.OUT_DIR, injector.BOOT_LOG)
    try:
        rc = injector.inject(pid, payload)
    except Exception as exc:
        log(f"  pid {pid}: injection error: {type(exc).__name__}: {exc}")
        return False
    if rc == 0:
        log(f"  pid {pid}: injected, mods applied (see modloader.log)")
        return True
    log(f"  pid {pid}: PyRun_SimpleString returned {rc}; see {injector.BOOT_LOG}")
    return False


def main() -> int:
    injector.use_utf8_console()
    ap = argparse.ArgumentParser(description="Auto-inject the Instantale mod loader.")
    ap.add_argument("--interval", type=float, default=2.0, help="poll seconds (default 2)")
    ap.add_argument("--once", action="store_true", help="handle the running game, then exit")
    logrotate.add_arguments(ap)
    args = ap.parse_args()

    if not (sys.maxsize > 2 ** 32):
        print("ERROR: run this with 64-bit Python; instantale.exe is x64.", file=sys.stderr)
        return 2

    os.makedirs(injector.OUT_DIR, exist_ok=True)
    log("watcher started; polling every {}s for {}".format(args.interval, injector.TARGET_EXE))
    if not logrotate.enabled(args.log_rotate):
        log("log rotate: disabled; out/*.log will keep growing")

    handled: set[int] = set()
    try:
        while True:
            alive = {pid for pid, _ in injector.find_processes(injector.TARGET_EXE)}
            # 終了した pid を記録から外す。
            # これでゲームを再起動したときに、改めて注入されるようになる。
            # （pid は使い回されることがあるので、外しておかないと取り違えの元にもなる）
            handled &= alive

            fresh = sorted(alive - handled)
            if fresh:
                # ゲームが起動した時点が1世代の境目。ここで前回のログを退避して、
                # この起動の記録が空のファイルから始まるようにする。
                # 同時に2つ見つけた場合でも入れ替えは1回だけ（ログは共用のため）。
                injector.rotate_logs(args.log_rotate, log=log)

            for pid in fresh:
                log(f"new game process: pid {pid}")
                # 準備待ちに入る前に記録しておく。
                # 待って失敗した場合に、同じプロセスを何度も掴み直さないため。
                handled.add(pid)
                if wait_until_ready(pid):
                    inject_pid(pid)
                else:
                    log(f"  pid {pid}: never became ready within {READY_TIMEOUT:.0f}s; skipped")

            if args.once:
                return 0 if handled else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("watcher stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
