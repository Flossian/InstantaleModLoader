# -*- coding: utf-8 -*-
"""注入の前段（プロセスと DLL の一覧取り）の再試行を見る。

    python tools/tests/test_injector_snapshot.py

`CreateToolhelp32Snapshot` は、対象の DLL 一覧が動いている最中だと
`ERROR_BAD_LENGTH`(24) で失敗する。
MSDN が「成功するまで再試行せよ」と書いている種類の失敗で、権限の問題ではない。
ただし Python 側では `PermissionError: [WinError 24]` として上がってくるので、
そのままだと権限や多重起動を疑うことになる。

実際に踏んだ（2026-08-21、pid 21260）:

    injection error: PermissionError: [WinError 24]
    CreateToolhelp32Snapshot(module, pid=21260) failed

このゲームは起動直後に torch / arrow / onnx などを大量に読むので、
一覧が落ち着くまでの窓に当たる。

確認するもの:

  すぐ成功   … 再試行しない。待たない
  一時的な失敗 … `ERROR_BAD_LENGTH` の間は待って粘り、成功したらその handle を返す
  諦め       … 粘り切っても駄目なら `ERROR_BAD_LENGTH` のまま投げる（握り潰さない）
  別のエラー … 権限などは1回目でそのまま投げる。待っても変わらないので粘らない
"""
import ast
import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))

if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import injector                                        # noqa: E402

ERROR_ACCESS_DENIED = 5
HANDLE = 0x1234

failures = []
passed = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    (passed if cond else failures).append(name)


class FakeKernel32:
    """`CreateToolhelp32Snapshot` だけを持つ替え玉。

    `results` の先頭から1つずつ返す。尽きたら最後の値を返し続ける。
    """

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def CreateToolhelp32Snapshot(self, flags, pid):
        self.calls += 1
        index = min(self.calls - 1, len(self.results) - 1)
        return self.results[index]


def run(results, last_error):
    """`_snapshot` を替え玉の上で回して、(戻り値か例外, 呼んだ回数, 待った回数)。"""
    waits = []

    real_kernel32 = injector.kernel32
    real_get_last_error = ctypes.get_last_error
    real_sleep = injector.time.sleep

    fake = FakeKernel32(results)
    injector.kernel32 = fake
    ctypes.get_last_error = lambda: last_error
    injector.time.sleep = lambda seconds: waits.append(seconds)
    try:
        try:
            outcome = injector._snapshot(injector.TH32CS_SNAPMODULE, 4321, "snapshot")
        except OSError as exc:
            outcome = exc
    finally:
        injector.kernel32 = real_kernel32
        ctypes.get_last_error = real_get_last_error
        injector.time.sleep = real_sleep
    return outcome, fake.calls, len(waits)


def main():
    bad = injector.INVALID_HANDLE_VALUE

    print("\n[すぐ成功]")
    outcome, calls, waits = run([HANDLE], ERROR_ACCESS_DENIED)
    check("handle をそのまま返す", outcome == HANDLE, outcome)
    check("1回しか呼ばない", calls == 1, calls)
    check("待たない", waits == 0, waits)

    print("\n[一時的な ERROR_BAD_LENGTH]")
    outcome, calls, waits = run([bad, bad, bad, HANDLE], injector.ERROR_BAD_LENGTH)
    check("粘って handle を返す", outcome == HANDLE, outcome)
    check("成功するまで呼び直す", calls == 4, calls)
    check("失敗のたびに待つ", waits == 3, waits)

    print("\n[ずっと ERROR_BAD_LENGTH]")
    outcome, calls, waits = run([bad], injector.ERROR_BAD_LENGTH)
    check("最後は投げる", isinstance(outcome, OSError), outcome)
    check("握り潰さない（24 のまま）",
          getattr(outcome, "winerror", None) == injector.ERROR_BAD_LENGTH, outcome)
    check("回数の上限で止まる", calls == injector.SNAPSHOT_ATTEMPTS, calls)
    check("最後の1回の後は待たない",
          waits == injector.SNAPSHOT_ATTEMPTS - 1, waits)

    print("\n[別のエラー]")
    outcome, calls, waits = run([bad], ERROR_ACCESS_DENIED)
    check("1回目でそのまま投げる", isinstance(outcome, OSError), outcome)
    check("エラー番号を変えない",
          getattr(outcome, "winerror", None) == ERROR_ACCESS_DENIED, outcome)
    check("粘らない", calls == 1, calls)
    check("待たない", waits == 0, waits)

    print("\n[使う側]")
    # 直に呼んでよいのは `_snapshot` の中だけ。
    # 他所へ増えると、そこだけ再試行が効かないまま残る。
    tree = ast.parse(open(injector.__file__, encoding="utf-8").read())
    callers = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "CreateToolhelp32Snapshot"):
                callers.add(node.name)
    check("直に呼ぶのは _snapshot だけ", callers == {"_snapshot"}, sorted(callers))

    print("\n{} check(s), {} failure(s)".format(
        len(passed) + len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
