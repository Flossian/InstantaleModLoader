# -*- coding: utf-8 -*-
"""注入の本体（`tools/injector.py` の PE 解析・スタブ・後始末）をゲーム抜きで見る。

    python tools/tests/test_injector_stub.py

  PE 解析 … `export_rvas` の RVA に読み込み先を足すと、実際の関数の位置になる。
             いま動いている Python の DLL で突き合わせる（ゲームの python310.dll と同じ形式）
  スタブ  … 74 バイト。`sub rsp,0x38` で 16 バイト境界に揃う。3つの関数と流し込む文字列の
             アドレスが、組んだ位置にそのまま入る
  後始末  … スタブの完走を確かめられたときだけリモートのメモリを解放する。
             時間切れ・待ちの失敗・終了コードが読めない・まだ動いている（STILL_ACTIVE）は
             保留（INJECT_PENDING）で、メモリを残す。解放すると動いているコードを消してゲームが落ちる
"""
import ctypes
import os
import struct
import sys
import tempfile
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import injector                                        # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ----------------------------------------------------------------- PE 解析
print("[PE 解析]")
buf = ctypes.create_unicode_buffer(32768)
ctypes.windll.kernel32.GetModuleFileNameW(ctypes.c_void_p(sys.dllhandle), buf, len(buf))
dll_path = buf.value
names = ("Py_IsInitialized",) + injector.NEEDED_EXPORTS
rvas = injector.export_rvas(dll_path, names)
for name in names:
    actual = ctypes.cast(getattr(ctypes.pythonapi, name), ctypes.c_void_p).value
    check("{}: 読み込み先 + RVA が実際の位置".format(name),
          sys.dllhandle + rvas[name] == actual,
          (hex(sys.dllhandle), hex(rvas[name]), hex(actual or 0)))
try:
    injector.export_rvas(dll_path, ("No_Such_Export_Anywhere",))
    check("無い名前は例外", False)
except RuntimeError as exc:
    check("無い名前は例外（名前を添える）", "No_Such_Export_Anywhere" in str(exc), exc)
with tempfile.TemporaryDirectory() as tmp:
    fake = os.path.join(tmp, "fake.dll")
    with open(fake, "wb") as fh:
        fh.write(b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x40) + b"XX\0\0" + b"\0" * 64)
    try:
        injector.export_rvas(fake, ("Py_IsInitialized",))
        check("PE でないファイルは例外", False)
    except RuntimeError as exc:
        check("PE でないファイルは例外", "not a PE" in str(exc), exc)

# ----------------------------------------------------------------- スタブ
print("[スタブ]")
ENSURE, RUN, RELEASE, CODE = (0x1111111111111111, 0x2222222222222222,
                              0x3333333333333333, 0x4444444444444444)
stub = injector.build_stub(ENSURE, RUN, RELEASE, CODE)
check("74 バイト", len(stub) == 74, len(stub))
check("先頭は sub rsp,0x38", stub[:4] == b"\x48\x83\xEC\x38", stub[:4].hex())
# 入った直後の rsp は 16 の倍数 + 8（call が戻り先を積んだ分）。
# 引いた後に 16 の倍数になっていないと、呼んだ先の SSE 命令で落ちることがある。
check("sub rsp の量で 16 バイト境界に揃う", (8 + stub[3]) % 16 == 0, stub[3])
check("末尾は add rsp,0x38; ret", stub[-5:] == b"\x48\x83\xC4\x38\xC3", stub[-5:].hex())
for label, value, prefix in (("PyGILState_Ensure", ENSURE, b"\x48\xB8"),
                             ("流し込む文字列", CODE, b"\x48\xB9"),
                             ("PyRun_SimpleString", RUN, b"\x48\xB8"),
                             ("PyGILState_Release", RELEASE, b"\x48\xB8")):
    check("{} のアドレスが mov の即値に入る".format(label),
          prefix + struct.pack("<Q", value) in stub)


# ----------------------------------------------------------------- 後始末
print("[後始末]")


class FakeKernel32:
    """`inject()` が呼ぶ Win32 API の替え玉。解放した領域を数える。"""

    def __init__(self, wait, exit_code=0, exit_ok=True):
        self.wait, self.exit_code, self.exit_ok = wait, exit_code, exit_ok
        self.freed = []
        self._next = 0x10000

    def OpenProcess(self, *_a):
        return 0x44

    def VirtualAllocEx(self, _h, _addr, size, *_a):
        self._next += 0x1000
        return self._next

    def WriteProcessMemory(self, *_a):
        return 1

    def VirtualProtectEx(self, *_a):
        return 1

    def CreateRemoteThread(self, *_a):
        return 0x88

    def WaitForSingleObject(self, _thread, _ms):
        return self.wait

    def GetExitCodeThread(self, _thread, ref):
        if not self.exit_ok:
            return 0
        ref._obj.value = self.exit_code
        return 1

    def VirtualFreeEx(self, _h, mem, *_a):
        self.freed.append(mem)
        return 1

    def CloseHandle(self, _h):
        return 1


WAIT_FAILED = 0xFFFFFFFF
saved = (injector.kernel32, injector.find_module, injector.export_rvas)
injector.find_module = lambda pid, name: (0x7FF000000000, "python310.dll")
injector.export_rvas = lambda path, wanted: {n: 0x100 * (i + 1) for i, n in enumerate(wanted)}
payload = injector.make_bootstrap("C:\\rt", "C:\\out", "C:\\out\\bootstrap.log")
try:
    for label, fake, want_rc, want_freed in (
            ("完走して 0", FakeKernel32(injector.WAIT_OBJECT_0, 0), 0, 2),
            ("完走して -1（bootstrap が例外）", FakeKernel32(injector.WAIT_OBJECT_0, 0xFFFFFFFF), -1, 2),
            ("時間切れは保留", FakeKernel32(injector.WAIT_TIMEOUT), injector.INJECT_PENDING, 0),
            ("待ちの失敗も保留", FakeKernel32(WAIT_FAILED), injector.INJECT_PENDING, 0),
            ("終了コードが読めなければ保留",
             FakeKernel32(injector.WAIT_OBJECT_0, exit_ok=False), injector.INJECT_PENDING, 0),
            ("まだ動いている（STILL_ACTIVE）は保留",
             FakeKernel32(injector.WAIT_OBJECT_0, injector.STILL_ACTIVE),
             injector.INJECT_PENDING, 0)):
        injector.kernel32 = fake
        rc = injector.inject(1234, payload)
        check("{}: 戻り値 {}".format(label, want_rc), rc == want_rc, rc)
        check("{}: 解放 {} 領域".format(label, want_freed), len(fake.freed) == want_freed,
              fake.freed)
finally:
    injector.kernel32, injector.find_module, injector.export_rvas = saved

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
