#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
動いている Instantale に mod ローダを注入する。

Instantale は Nuitka の standalone ビルドで、Python コードは全て
instantale.exe の中にネイティブコードとして取り込まれている。
そのためディスク上に書き換えられる .pyc は存在しない。

一方で CPython 本体は python310.dll として動的リンクされたまま残っていて、
C API がそのままエクスポートされている。そこで、ゲームの中で既に動いている
インタプリタを借りて、こちらの Python コードを流し込む。やっているのは
実質的にこれだけ。

    PyGILState_STATE s = PyGILState_Ensure();
    PyRun_SimpleString(<ブートストラップのコード>);
    PyGILState_Release(s);

この3行ぶんの機械語（74 バイト）を対象プロセスのメモリに書き込み、
CreateRemoteThread で実行する。この方式には次の利点がある。

  * C コンパイラが要らない（機械語をバイト列として直接組み立てる）
  * 管理者権限が要らない（同じユーザーの同じ整合性レベルのプロセスなので）
  * ゲームのフォルダを一切書き換えない
    ブートストラップは sys.path をこのフォルダの runtime/ に向けるだけなので、
    Epic のアップデートや修復で壊れることがない

使い方:
    python injector.py              # 動いている instantale.exe に注入する
    python injector.py --pid 1234   # プロセスを指定して注入する
    python injector.py --dry-run    # アドレスの解決だけ行い、何も書き込まない
    python injector.py --unload     # 当てたパッチを剥がす（ゲームは終了しない）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
from ctypes import wintypes

import logrotate

# このファイルは tools/ にあるので、1階層上がった所が配布フォルダの根。
# runtime/ と out/ はそこに置かれる（ここを間違えると tools/runtime を見に行く）。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME_DIR = os.path.join(ROOT, "runtime")
OUT_DIR = os.path.join(ROOT, "out")
BOOT_LOG = os.path.join(OUT_DIR, "bootstrap.log")

TARGET_EXE = "instantale.exe"
PYTHON_DLL = "python310.dll"
NEEDED_EXPORTS = ("PyGILState_Ensure", "PyRun_SimpleString", "PyGILState_Release")


# --------------------------------------------------------------------------
# DLL から関数のアドレスを調べる
# --------------------------------------------------------------------------
_rva_cache: dict[tuple[str, tuple[str, ...]], dict[str, int]] = {}


def export_rvas(dll_path: str, wanted: tuple[str, ...]) -> dict[str, int]:
    """DLL ファイルを直接読んで、関数名からその RVA を求める。

    RVA（Relative Virtual Address）はモジュール先頭からの相対位置。
    実際のアドレスはロードされた先の base に足せば求まる。
    base は動いているプロセスから取得するので、ASLR があっても問題ない。
    """
    cache_key = (dll_path, tuple(wanted))
    if cache_key in _rva_cache:
        return _rva_cache[cache_key]

    with open(dll_path, "rb") as fh:
        buf = fh.read()

    # PE ファイルの先頭は DOS ヘッダ。その 0x3C の位置に、本体である
    # PE ヘッダへのオフセットが入っている。
    pe = struct.unpack_from("<I", buf, 0x3C)[0]
    if buf[pe:pe + 4] != b"PE\0\0":
        raise RuntimeError(f"not a PE file: {dll_path}")

    # 0x20B は 64bit（PE32+）を表す。32bit の DLL を掴んでいたらここで止める。
    magic = struct.unpack_from("<H", buf, pe + 24)[0]
    if magic != 0x20B:
        raise RuntimeError(f"expected PE32+ (x64), got magic 0x{magic:X}")

    n_sections = struct.unpack_from("<H", buf, pe + 6)[0]
    opt_size = struct.unpack_from("<H", buf, pe + 20)[0]
    # オプショナルヘッダの先頭から 112 バイト目が、最初のデータディレクトリ
    # （＝エクスポートテーブル）の位置。
    export_rva = struct.unpack_from("<I", buf, pe + 24 + 112)[0]

    # ファイル上の位置と RVA は一致しないので、変換用にセクション表を読む。
    sections = []
    sec_off = pe + 24 + opt_size
    for i in range(n_sections):
        o = sec_off + i * 40                      # セクションヘッダは1つ 40 バイト
        vsize, vaddr, _rsize, raw = struct.unpack_from("<IIII", buf, o + 8)
        sections.append((vaddr, vsize, raw))

    def rva_to_off(rva: int) -> int:
        """RVA を、このファイルの中での位置に変換する。"""
        for vaddr, vsize, raw in sections:
            if vaddr <= rva < vaddr + vsize:
                return raw + (rva - vaddr)
        raise RuntimeError(f"rva 0x{rva:X} outside all sections")

    exp = rva_to_off(export_rva)
    n_funcs, n_names = struct.unpack_from("<II", buf, exp + 20)
    funcs_rva, names_rva, ords_rva = struct.unpack_from("<III", buf, exp + 28)

    funcs_off = rva_to_off(funcs_rva)
    names_off = rva_to_off(names_rva)
    ords_off = rva_to_off(ords_rva)

    found: dict[str, int] = {}
    remaining = set(wanted)
    # 名前のテーブルを順に見ていく。
    # i 番目の名前に対応する序数が、アドレステーブルの添字になっている。
    for i in range(n_names):
        name_off = rva_to_off(struct.unpack_from("<I", buf, names_off + i * 4)[0])
        end = buf.index(b"\0", name_off)
        name = buf[name_off:end].decode("ascii")
        if name not in remaining:
            continue
        ordinal = struct.unpack_from("<H", buf, ords_off + i * 2)[0]
        if ordinal >= n_funcs:
            raise RuntimeError(f"ordinal {ordinal} out of range for {name}")
        found[name] = struct.unpack_from("<I", buf, funcs_off + ordinal * 4)[0]
        remaining.discard(name)
        if not remaining:
            break      # 必要なものが揃ったら、残り数千件は見ない

    if remaining:
        raise RuntimeError(f"exports not found in {dll_path}: {sorted(remaining)}")
    _rva_cache[cache_key] = found
    return found


# --------------------------------------------------------------------------
# Win32 API の準備
# --------------------------------------------------------------------------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

PROCESS_ACCESS = 0x0043A  # VM_OPERATION|VM_READ|VM_WRITE|CREATE_THREAD|QUERY_INFORMATION
MEM_COMMIT_RESERVE = 0x3000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READ = 0x20
WAIT_TIMEOUT = 0x102


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


# ハンドルやポインタを扱う API には、必ず argtypes を明示しておく。
# 指定しないと ctypes は Python の int を 32bit の C int として渡すので、
# 64bit のハンドルやアドレスが黙って切り捨てられる。
# 原因が分かりにくい不具合になりやすいので、面倒でも全部書いている。
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeThread.restype = wintypes.BOOL
for _fn in ("Process32FirstW", "Process32NextW"):
    getattr(kernel32, _fn).argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    getattr(kernel32, _fn).restype = wintypes.BOOL
for _fn in ("Module32FirstW", "Module32NextW"):
    getattr(kernel32, _fn).argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    getattr(kernel32, _fn).restype = wintypes.BOOL

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                    wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                   wintypes.DWORD]
kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.VirtualProtectEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.CreateRemoteThread.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]


def _check(ok, what: str):
    """Win32 API の失敗を、GetLastError 付きの例外に変える。"""
    if not ok:
        err = ctypes.get_last_error()
        raise ctypes.WinError(err, f"{what} failed")
    return ok


def find_processes(exe_name: str) -> list[tuple[int, str]]:
    """指定した実行ファイル名で動いているプロセスを全て返す。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    _check(snap != INVALID_HANDLE_VALUE, "CreateToolhelp32Snapshot(process)")
    try:
        entry = PROCESSENTRY32W()
        # dwSize の設定は必須。設定し忘れると API が失敗する。
        entry.dwSize = ctypes.sizeof(entry)
        out = []
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                out.append((entry.th32ProcessID, entry.szExeFile))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return out
    finally:
        kernel32.CloseHandle(snap)


def find_module(pid: int, module_name: str) -> tuple[int, str]:
    """対象プロセスの中のモジュールについて (ロード先アドレス, フルパス) を返す。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    _check(snap != INVALID_HANDLE_VALUE, f"CreateToolhelp32Snapshot(module, pid={pid})")
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szModule.lower() == module_name.lower():
                # ポインタのままでは足し算ができないので、整数に変換して返す。
                return ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value, entry.szExePath
            ok = kernel32.Module32NextW(snap, ctypes.byref(entry))
        raise RuntimeError(f"{module_name} is not loaded in pid {pid}")
    finally:
        kernel32.CloseHandle(snap)


# --------------------------------------------------------------------------
# 注入する機械語（x64）
# --------------------------------------------------------------------------
def build_stub(ensure: int, run: int, release: int, code_addr: int) -> bytes:
    """リモートスレッドとして実行する機械語を組み立てる。

    スレッドの開始位置には call で入ってくるので、入った直後のスタックは
    16 バイト境界から 8 ずれている。sub rsp, 0x38 で境界に揃えつつ、
    Windows x64 の呼び出し規約が要求する 0x20 バイトの領域（シャドウスペース）と、
    値を一時的に置くための 2 スロットをまとめて確保している。
    """
    def mov_rax(v: int) -> bytes:
        return b"\x48\xB8" + struct.pack("<Q", v)

    def mov_rcx(v: int) -> bytes:
        return b"\x48\xB9" + struct.pack("<Q", v)

    return b"".join([
        b"\x48\x83\xEC\x38",          # sub  rsp, 0x38
        mov_rax(ensure), b"\xFF\xD0",  # call PyGILState_Ensure
        b"\x48\x89\x44\x24\x20",      # mov  [rsp+0x20], rax   ; GIL の状態を保存
        mov_rcx(code_addr),           # 第1引数 = 流し込むコードのアドレス
        mov_rax(run), b"\xFF\xD0",     # call PyRun_SimpleString
        b"\x48\x89\x44\x24\x28",      # mov  [rsp+0x28], rax   ; 戻り値を保存
        b"\x48\x8B\x4C\x24\x20",      # mov  rcx, [rsp+0x20]   ; 保存した GIL の状態
        mov_rax(release), b"\xFF\xD0",  # call PyGILState_Release
        b"\x8B\x44\x24\x28",          # mov  eax, [rsp+0x28]   ; スレッドの終了コードにする
        b"\x48\x83\xC4\x38",          # add  rsp, 0x38
        b"\xC3",                      # ret
    ])


# --------------------------------------------------------------------------
# 流し込む Python コード
#
# 【注意】この文字列は C の文字列としてリモートプロセスへ渡すため、
# ASCII だけで書く必要がある。make_bootstrap() が isascii() で検査していて、
# 違反すると注入を中止する。そのためこのテンプレートの中のコメントだけは
# 日本語にできない。
# --------------------------------------------------------------------------
BOOTSTRAP_TEMPLATE = r'''
import sys, os, datetime, traceback

_LOG = __LOG__

def _w(msg):
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write("[" + datetime.datetime.now().isoformat() + "] " + msg + "\n")
    except Exception:
        pass

try:
    _rt = __RUNTIME__
    if _rt not in sys.path:
        sys.path.insert(0, _rt)
    # drop any previous copy so re-injection picks up edited sources
    for _name in [k for k in list(sys.modules)
                  if k == "instantale_modloader" or k.startswith("instantale_modloader.")]:
        del sys.modules[_name]
    import instantale_modloader
    __CALL__
    _w("bootstrap ok")
except BaseException:
    _w("bootstrap FAILED\n" + traceback.format_exc())
'''

# 流し込むコードの最後の1行。注入の目的はこれだけが違う。
#
# unload 側も同じ経路（モジュールを落として再 import）を通る。パッチを剥がすための
# 記録は sys に置いてあるので（patch.py の _undo_log）、ローダを読み直しても
# 前回の注入で当てた分を剥がせる。
CALLS = {
    "boot": "instantale_modloader.boot(__OUT__)",
    "unload": "instantale_modloader.unload(__OUT__)",
}


def make_bootstrap(runtime_dir: str, out_dir: str, log_path: str,
                   action: str = "boot") -> bytes:
    """テンプレートにパスを埋め込み、NUL 終端付きのバイト列にする。"""
    # repr() を使うのは、クォートやバックスラッシュを正しくエスケープするため。
    # Windows のパスには "\" が入るので、そのまま埋めると壊れる。
    src = (BOOTSTRAP_TEMPLATE
           .replace("__CALL__", CALLS[action])
           .replace("__LOG__", repr(log_path))
           .replace("__RUNTIME__", repr(runtime_dir))
           .replace("__OUT__", repr(out_dir)))
    data = src.encode("utf-8")
    if not src.isascii():
        # 日本語を含むパスでも repr() の時点でエスケープされるはずだが、
        # C の文字列として渡す以上、ここは明示的に確認しておく。
        raise RuntimeError("bootstrap must stay ASCII-only")
    return data + b"\0"


# --------------------------------------------------------------------------
# 注入の本体
# --------------------------------------------------------------------------
def inject(pid: int, payload: bytes, dry_run: bool = False) -> int:
    # ロード先アドレスは動いているプロセスから、RVA はディスク上の DLL から取る。
    # この2つを足したものが、呼び出すべき実際のアドレスになる。
    base, dll_path = find_module(pid, PYTHON_DLL)
    print(f"  {PYTHON_DLL} base : 0x{base:016X}")
    print(f"  {PYTHON_DLL} path : {dll_path}")

    rvas = export_rvas(dll_path, NEEDED_EXPORTS)
    addrs = {name: base + rva for name, rva in rvas.items()}
    for name in NEEDED_EXPORTS:
        print(f"  {name:<20} : 0x{addrs[name]:016X}  (rva 0x{rvas[name]:X})")

    if dry_run:
        print("  dry-run: nothing written")
        return 0

    handle = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    _check(handle, f"OpenProcess(pid={pid})")

    code_mem = stub_mem = None
    try:
        # 1. 流し込む Python コードを書き込む。
        #    これはデータなので、読み書きできれば十分（実行属性は不要）。
        code_mem = kernel32.VirtualAllocEx(handle, None, len(payload),
                                           MEM_COMMIT_RESERVE, PAGE_READWRITE)
        _check(code_mem, "VirtualAllocEx(bootstrap)")
        written = ctypes.c_size_t(0)
        _check(kernel32.WriteProcessMemory(handle, code_mem, payload, len(payload),
                                           ctypes.byref(written)),
               "WriteProcessMemory(bootstrap)")

        # 2. 機械語を組み立てて書き込む。
        #    1 で確保したアドレスを、引数として機械語の中に埋め込む。
        stub = build_stub(addrs["PyGILState_Ensure"], addrs["PyRun_SimpleString"],
                          addrs["PyGILState_Release"], code_mem)
        stub_mem = kernel32.VirtualAllocEx(handle, None, len(stub),
                                           MEM_COMMIT_RESERVE, PAGE_READWRITE)
        _check(stub_mem, "VirtualAllocEx(stub)")
        _check(kernel32.WriteProcessMemory(handle, stub_mem, stub, len(stub),
                                           ctypes.byref(written)),
               "WriteProcessMemory(stub)")

        # 3. 書き込み終わってから「読み取り+実行」に変更する。
        #    最初から書き込みも実行もできる状態（RWX）で確保すると、
        #    それ自体がセキュリティソフトに強く警戒される形になるため。
        old = wintypes.DWORD(0)
        _check(kernel32.VirtualProtectEx(handle, stub_mem, len(stub),
                                         PAGE_EXECUTE_READ, ctypes.byref(old)),
               "VirtualProtectEx(stub -> RX)")

        print(f"  stub {len(stub)} bytes at 0x{stub_mem:016X}, "
              f"bootstrap {len(payload)} bytes at 0x{code_mem:016X}")

        thread = kernel32.CreateRemoteThread(handle, None, 0, stub_mem, None, 0, None)
        _check(thread, "CreateRemoteThread")
        try:
            if kernel32.WaitForSingleObject(thread, 30000) == WAIT_TIMEOUT:
                # AI の推論が長く走っていると、GIL が握られたままになって
                # ここまで到達しないことがある。放っておけば後から完走するので、
                # メモリは解放せずに残す。
                # 解放してしまうと、実行中のコードそのものを消すことになり、
                # ゲームが落ちる。
                print("  WARNING: stub still running after 30s (GIL contention?)")
                print("           leaving remote memory allocated on purpose")
                stub_mem = code_mem = None
                return -2
            rc = wintypes.DWORD(0)
            kernel32.GetExitCodeThread(thread, ctypes.byref(rc))
            # スレッドの終了コードは PyRun_SimpleString の戻り値（0 が成功）。
            # 符号付きとして解釈する。
            return ctypes.c_int32(rc.value).value
        finally:
            kernel32.CloseHandle(thread)
    finally:
        # 完走していれば両方とも用済み。
        # タイムアウトした場合だけ上で None にしてあるので、ここを素通りする。
        for mem in (stub_mem, code_mem):
            if mem:
                kernel32.VirtualFreeEx(handle, mem, 0, MEM_RELEASE)
        kernel32.CloseHandle(handle)


def interpreter_ready(pid: int) -> bool:
    """対象プロセスで Python の初期化が終わっていれば True。

    Py_IsInitialized はグローバルなフラグを読むだけなので、GIL を持たない
    リモートスレッドから呼んでも安全。しかも引数なしで int を返すという形が
    スレッド関数とほぼ同じなので、そのままスレッドの開始位置として起動でき、
    結果はスレッドの終了コードとして受け取れる。
    """
    try:
        base, dll_path = find_module(pid, PYTHON_DLL)
    except Exception:
        return False  # python310.dll がまだ読み込まれていない

    try:
        rva = export_rvas(dll_path, ("Py_IsInitialized",))["Py_IsInitialized"]
    except Exception:
        return False

    handle = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not handle:
        return False
    try:
        # メモリの確保も書き込みも要らない。DLL にある関数を直接起点にする。
        thread = kernel32.CreateRemoteThread(handle, None, 0, base + rva, None, 0, None)
        if not thread:
            return False
        try:
            if kernel32.WaitForSingleObject(thread, 5000) == WAIT_TIMEOUT:
                return False
            rc = wintypes.DWORD(0)
            kernel32.GetExitCodeThread(thread, ctypes.byref(rc))
            return rc.value != 0
        finally:
            kernel32.CloseHandle(thread)
    finally:
        kernel32.CloseHandle(handle)


def rotate_logs(cli_override: bool | None = None, log=None) -> int:
    """out/ のログを1世代送る。注入の直前に1回だけ呼ぶこと。

    watcher.py からも同じ入口を使う（out ディレクトリの決め方を1か所にまとめる）。
    切り替え方は logrotate.py を参照。
    """
    return logrotate.rotate(OUT_DIR, cli_override=cli_override, log=log)


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject the Instantale mod loader.")
    ap.add_argument("--pid", type=int, help="target pid (default: the running instantale.exe)")
    ap.add_argument("--dry-run", action="store_true", help="resolve addresses, write nothing")
    ap.add_argument("--unload", action="store_true",
                    help="revert the patches instead of applying mods")
    logrotate.add_arguments(ap)
    args = ap.parse_args()

    # 32bit の Python からは 64bit のプロセスに注入できないので、先に弾く。
    if not (sys.maxsize > 2 ** 32):
        print("ERROR: run this with 64-bit Python; instantale.exe is x64.", file=sys.stderr)
        return 2
    if not os.path.isdir(RUNTIME_DIR):
        print(f"ERROR: runtime dir missing: {RUNTIME_DIR}", file=sys.stderr)
        return 2

    if args.pid:
        pid = args.pid
    else:
        procs = find_processes(TARGET_EXE)
        if not procs:
            print(f"ERROR: {TARGET_EXE} is not running.", file=sys.stderr)
            return 1
        # 複数動いている場合は自動で選ばない。
        # 間違ったプロセスに注入すると取り返しがつかないため、明示させる。
        if len(procs) > 1:
            print(f"ERROR: {len(procs)} instances found "
                  f"({', '.join(str(p) for p, _ in procs)}); pass --pid.", file=sys.stderr)
            return 1
        pid = procs[0][0]

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"target pid          : {pid}")
    print(f"runtime dir         : {RUNTIME_DIR}")
    print(f"out dir             : {OUT_DIR}")

    action = "unload" if args.unload else "boot"
    # 注入 = 1世代の境目。書き込みが始まる前にここで入れ替える。
    # --dry-run は「何も書かない」ための指定なので、ログにも触らない。
    # --unload はこのプレイの続きなので**入れ替えない**（剥がした記録を、
    # そのとき当てた記録と同じファイルに残したい）。
    if not args.dry_run and not args.unload:
        rotate_logs(args.log_rotate, log=lambda msg: print(f"  {msg}"))

    payload = make_bootstrap(RUNTIME_DIR, OUT_DIR, BOOT_LOG, action=action)
    rc = inject(pid, payload, dry_run=args.dry_run)

    if args.dry_run:
        return 0
    if rc == 0:
        print("\nOK: PyRun_SimpleString returned 0.")
        if args.unload:
            print("mods reverted; on_ready side effects and game-state writes remain.")
        print(f"See {BOOT_LOG} and {os.path.join(OUT_DIR, 'modloader.log')}")
    elif rc == -2:
        # 上の GIL 待ちで時間切れになったケース。失敗ではなく保留。
        print("\nPENDING: see the logs once the game becomes idle.")
    else:
        print(f"\nFAILED: PyRun_SimpleString returned {rc} "
              f"(the bootstrap raised; check {BOOT_LOG}).")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
