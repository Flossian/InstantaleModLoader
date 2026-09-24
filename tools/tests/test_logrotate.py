# -*- coding: utf-8 -*-
"""ログの世代管理（`tools/logrotate.py`）を一時フォルダで通す。

    python tools/tests/test_logrotate.py

  世代送り … `名前.log` → `.1` → `.2` … と1つずつ後ろへ。keep を超えた最古は消える。keep 0 は消すだけ
  対象     … out/ 直下の空でない `*.log` だけ。サブフォルダ・status.json・空のログには触らない
  優先順位 … コマンドライン > 環境変数 > settings/loader.json > 既定値（ROTATE_LOGS）。
             環境変数の読めない値は無いものとして下へ落ちる
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import logrotate                                       # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


print("[世代送り]")
with tempfile.TemporaryDirectory() as tmp:
    log = os.path.join(tmp, "a.log")
    write(log, "3回目")
    write(log + ".1", "2回目")
    write(log + ".2", "1回目")
    logrotate._shift(log, 2)
    check("本体は .1 へ", read(log + ".1") == "3回目")
    check(".1 は .2 へ", read(log + ".2") == "2回目")
    check("keep を超えた最古は消える", not os.path.exists(log + ".3"))
    check("本体は無くなる（次の書き込みが空から始まる）", not os.path.exists(log))
    write(log, "x")
    logrotate._shift(log, 0)
    check("keep 0 は退避せず消す", not os.path.exists(log) and read(log + ".1") == "3回目")

print("[対象]")
with tempfile.TemporaryDirectory() as out:
    write(os.path.join(out, "modloader.log"), "中身")
    write(os.path.join(out, "empty.log"), "")
    write(os.path.join(out, "status.json"), "{}")
    os.makedirs(os.path.join(out, "test"))
    write(os.path.join(out, "test", "inner.log"), "中身")
    said = []
    count = logrotate.rotate(out, cli_override=True, keep=1, log=said.append)
    check("送ったのは空でない直下の .log だけ", count == 1, count)
    check("modloader.log は .1 へ", read(os.path.join(out, "modloader.log.1")) == "中身")
    check("空のログはそのまま", os.path.exists(os.path.join(out, "empty.log"))
          and not os.path.exists(os.path.join(out, "empty.log.1")))
    check("status.json とサブフォルダには触らない",
          os.path.exists(os.path.join(out, "status.json"))
          and os.path.exists(os.path.join(out, "test", "inner.log")))
    write(os.path.join(out, "modloader.log"), "次")
    check("切っていれば何もしない", logrotate.rotate(out, cli_override=False) == 0
          and read(os.path.join(out, "modloader.log")) == "次")
    check("無いフォルダは 0", logrotate.rotate(os.path.join(out, "none"), cli_override=True) == 0)

print("[優先順位]")
saved_env = os.environ.pop(logrotate.ENV_VAR, None)
saved_flag = logrotate.settings_flag
try:
    def stored(value):
        logrotate.settings_flag = lambda: value

    stored(None)
    check("何も無ければ既定値", logrotate.enabled() is logrotate.ROTATE_LOGS)
    stored(not logrotate.ROTATE_LOGS)
    check("設定ファイルは既定値より強い", logrotate.enabled() is (not logrotate.ROTATE_LOGS))
    os.environ[logrotate.ENV_VAR] = "1"
    stored(False)
    check("環境変数は設定ファイルより強い", logrotate.enabled() is True)
    os.environ[logrotate.ENV_VAR] = "off"
    stored(True)
    check("環境変数の off", logrotate.enabled() is False)
    check("コマンドラインは環境変数より強い", logrotate.enabled(True) is True)
    os.environ[logrotate.ENV_VAR] = "maybe"
    stored(False)
    check("環境変数の読めない値は設定ファイルへ落ちる", logrotate.enabled() is False)
finally:
    logrotate.settings_flag = saved_flag
    os.environ.pop(logrotate.ENV_VAR, None)
    if saved_env is not None:
        os.environ[logrotate.ENV_VAR] = saved_env

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
