# -*- coding: utf-8 -*-
r"""MOD 同梱の設定画面（`mod.json` の "tool"）を実際に開いて撮る。TECH.md §3.12。

    python tools/check_tool_screens.py              開いて撮る（python と pythonw の両方）
    python tools/check_tool_screens.py --window     窓の記憶の往復（最大化して閉じて開き直す）
    python tools/check_tool_screens.py --only 322   名前に 322 を含む MOD だけ（check_mods.py と同じ書式）

撮ったものは `out\tool_screens\` に置く（消してよい。`out\` の決まりどおり）。

##### なぜ要るか

道具は6本あり、土台（`tools\modtool.py` と `instantale_modloader.saves`）を共有している。
そこを触ると6画面すべてに効くのに、**オフラインの検査はこの経路を通らない**:

- 起動は `gui.py` が `[sys.executable, <MOD>/tool.py]` を**別プロセス**で開く形で、
  渡すのは環境変数だけ（`gui.py` の `_open_tool`）。import では通らない
- 配布物のローダは `pythonw` で走る。`sys.stderr` が `None` になるので、
  そこへ書く道が在ると**無反応で死ぬ**（traceback がどこにも出ない）
- 窓が組めても中身が空、という壊れ方がある。それは撮って見るしかない

##### 何を機械が決め、何を目で見るか

| | どちらが決めるか |
|---|---|
| 窓が出たか・落ちなかったか | 機械（`--only` を付けなければ 6本 × 2 通り） |
| 窓の寸法が最大化で壊れないか | 機械（`--window`） |
| 一覧に中身が入っているか・崩れていないか | **目**。だから撮る |

`--window` は本物の `settings\gui.json` を書くので、退避してから触り、終わりに戻す。

##### 撮り方

Pillow を入れずに Win32 の `PrintWindow` で撮る（窓が裏や画面の外に居ても撮れる）。
PNG は zlib で自分で組む。外部の依存を増やさないため。

撮るのは**最初のタブだけ**。
外から `Ctrl+Tab` を送る形は試して駄目だった ―
`ttk.Notebook` の巡回はノートブック自身が焦点を持っているときの割り当てなので、
別プロセスへ送ると**ウィジェットの焦点だけが動いて**タブは変わらない
（4枚撮れたのに違いは焦点の枠だけ、という形で気付いた）。
座標でタブ見出しを叩けば動くが、MOD ごとの位置を持つことになる。
他のタブを見たいときはローダの設定画面から開いて自分で押すこと。
"""
import argparse
import ctypes
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME_DIR = os.path.join(ROOT, "runtime")
OUT_DIR = os.path.join(ROOT, "out", "tool_screens")
GUI_JSON = os.path.join(ROOT, "settings", "gui.json")
sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                        # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_RENDERFULLCONTENT = 2
DIB_RGB_COLORS = 0
BI_RGB = 0
SRCCOPY = 0x00CC0020
SW_MAXIMIZE = 3
SW_RESTORE = 9
WM_CLOSE = 0x0010

#: 窓が出るまで待つ上限（秒）。曲 115 本・NPC 100 人超を読む道具があるので短くしない。
OPEN_TIMEOUT = 40

#: 窓が出てから撮るまでの待ち。一覧を読み終わらせる。
SETTLE = 1.5

#: 「空の窓」と見なす PNG の大きさ（バイト）。単色の窓はここを下回る。
BLANK_BYTES = 3000

failures = []


def check(label, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + label + ("  " + str(detail) if detail else ""))
    if not ok:
        failures.append(label)


# ----------------------------------------------------------------- 撮る
class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _write_png(path, width, height, bgra):
    """BGRA の生データ（上から下）を PNG で書く。"""
    rows = bytearray()
    for y in range(height):
        rows.append(0)                                    # filter: None
        line = bgra[y * width * 4:(y + 1) * width * 4]
        for x in range(0, len(line), 4):
            rows += bytes((line[x + 2], line[x + 1], line[x]))   # BGR -> RGB

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(rows), 6)))
        fh.write(chunk(b"IEND", b""))


def window_size(hwnd):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def capture(hwnd, path):
    """窓を撮って `path` に置く。`(幅, 高さ, バイト数)`。"""
    width, height = window_size(hwnd)
    if width <= 0 or height <= 0:
        raise RuntimeError("窓の大きさが取れない: {}x{}".format(width, height))
    screen_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT):
            gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, 0, 0, SRCCOPY)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height                 # 負で上から下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(mem_dc, bitmap, 0, height, buf,
                           ctypes.byref(info), DIB_RGB_COLORS) == 0:
            raise RuntimeError("GetDIBits が 0 を返した")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_png(path, width, height, buf.raw)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, screen_dc)
    return width, height, os.path.getsize(path)


# ----------------------------------------------------------------- 起動する
def visible_windows(pid):
    """その pid が持つ、題名のある見えているトップレベル窓の `[(hwnd, 題名)]`。"""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value:
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(each, 0)
    return found


def game_dir():
    r"""`gui.py` が渡すのと同じ値（`settings\gui.json` の `game_path` の親）。"""
    try:
        with io.open(GUI_JSON, encoding="utf-8") as fh:
            return os.path.dirname(json.load(fh).get("game_path", "") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def launch(mod_dir, entry, exe):
    """`gui.py` の `_open_tool` と同じ形で開く。`(proc, hwnd, 題名)`。

    渡すものは引数ではなく環境変数（§3.12）。`cwd` は MOD のフォルダ。
    """
    env = dict(os.environ)
    env["IML_ROOT"] = ROOT
    env["IML_STATE_DIR"] = ml.state_dir(RUNTIME_DIR)
    env["IML_GAME_DIR"] = game_dir()
    env["IML_MOD_SETTINGS"] = os.path.join(ROOT, "settings", "mod_settings.json")
    proc = subprocess.Popen([exe, os.path.join(mod_dir, entry)], cwd=mod_dir, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(OPEN_TIMEOUT * 2):
        time.sleep(0.5)
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise RuntimeError("終了コード {}: {}".format(
                proc.returncode, (err or out or b"").decode("utf-8", "replace").strip()[:400]))
        found = visible_windows(proc.pid)
        if found:
            time.sleep(SETTLE)
            return (proc,) + found[0]
    proc.kill()
    raise RuntimeError("窓が {} 秒で出なかった".format(OPEN_TIMEOUT))


def shut(proc, hwnd):
    """閉じる。窓の記憶はここで書かれるので、殺す前に WM_CLOSE を送る。"""
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    for _ in range(20):
        time.sleep(0.5)
        if proc.poll() is not None:
            return
    proc.kill()


# ----------------------------------------------------------------- 見る
def tools_of(only=""):
    """`"tool"` を宣言している MOD。`discover()` が唯一の出所（§1.3）。"""
    found = ml.discover()
    rows = []
    for name in found.get("order") or sorted(found.get("manifests") or {}):
        manifest = (found.get("manifests") or {}).get(name) or {}
        tool = manifest.get("tool") or {}
        if not tool.get("entry"):
            continue
        if only and only not in name:
            continue
        # `dirs` は MOD の**親**（`local/` の MOD もここで在り処が分かる）。
        parent = (found.get("dirs") or {}).get(name) or found["mods_dir"]
        mod_dir = os.path.join(parent, name)
        rows.append((name, mod_dir, tool["entry"], (tool.get("label") or {}).get("ja", "")))
    return rows


def check_open(rows):
    """開いて撮る。配布物のローダに合わせて `pythonw` も試す。"""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    runners = [(sys.executable, "python")]
    if os.path.isfile(pythonw):
        runners.append((pythonw, "pythonw"))
    else:
        print("  note  pythonw.exe が無いので python だけで見る")

    for name, mod_dir, entry, label in rows:
        print("[{}]  {}".format(name, label))
        for exe, tag in runners:
            try:
                proc, hwnd, title = launch(mod_dir, entry, exe)
            except Exception as exc:
                check("{} で開く".format(tag), False, exc)
                continue
            shot = None
            try:
                shot = capture(hwnd, os.path.join(OUT_DIR, "{}_{}.png".format(name, tag)))
            except Exception as exc:
                check("{} で撮る".format(tag), False, exc)
            finally:
                shut(proc, hwnd)
            if shot:
                width, height, nbytes = shot
                check("{} で開いて撮れた".format(tag), nbytes >= BLANK_BYTES,
                      "題名 {!r} / {}x{} / {} bytes{}".format(
                          title, width, height, nbytes,
                          " ← 空かもしれない" if nbytes < BLANK_BYTES else ""))


def check_window_memory(rows):
    """最大化して閉じ、開き直す。寸法が壊れないこと（TECH.md §3.12 / VERIFICATION.md §3.58）。"""
    if not os.path.isfile(GUI_JSON):
        check("gui.json が在る", False, GUI_JSON)
        return
    backup = GUI_JSON + ".check_tool_screens"
    shutil.copy(GUI_JSON, backup)
    try:
        with io.open(backup, encoding="utf-8") as fh:
            before = json.load(fh)
        for name, mod_dir, entry, label in rows:
            print("[{}]  {}".format(name, label))
            try:
                proc, hwnd, _title = launch(mod_dir, entry, sys.executable)
            except Exception as exc:
                check("開く", False, exc)
                continue
            plain = window_size(hwnd)
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            time.sleep(SETTLE)
            zoomed = window_size(hwnd)
            check("最大化で大きくなる", zoomed[0] > plain[0] and zoomed[1] > plain[1],
                  "{}x{} → {}x{}".format(plain[0], plain[1], zoomed[0], zoomed[1]))
            shut(proc, hwnd)

            try:
                with io.open(GUI_JSON, encoding="utf-8") as fh:
                    cfg = json.load(fh)
            except (OSError, ValueError) as exc:
                check("gui.json が読める", False, exc)
                continue
            entry_saved = (cfg.get("tool_window") or {}).get(name) or {}
            if not entry_saved:
                check("窓の記憶が残る", False, "tool_window に {} が無い（この道具は覚えない）".format(name))
                continue
            check("最大化を覚える", entry_saved.get("maximized") is True, entry_saved)
            size = str(entry_saved.get("geometry", "")).split("+")[0]
            wide = 0
            try:
                wide = int(size.split("x")[0])
            except ValueError:
                pass
            # ここが壊れていた道: 最大化中の geometry() は画面いっぱいの寸法を返す。
            check("残るのは最大化前の寸法（画面いっぱいの値ではない）",
                  0 < wide < zoomed[0],
                  "{}  （素 {}x{} / 最大化 {}x{}）".format(
                      entry_saved.get("geometry"), plain[0], plain[1], zoomed[0], zoomed[1]))
            check("他の覚えごとを落とさない",
                  all(cfg.get(key) == value for key, value in before.items()
                      if key != "tool_window"),
                  sorted(set(before) - set(cfg)) or "無し")

            try:
                proc, hwnd, _title = launch(mod_dir, entry, sys.executable)
            except Exception as exc:
                check("開き直す", False, exc)
                continue
            reopened = window_size(hwnd)
            capture(hwnd, os.path.join(OUT_DIR, "{}_maximized.png".format(name)))
            check("最大化で開く", reopened[0] >= zoomed[0] - 20 and reopened[1] >= zoomed[1] - 20,
                  "{}x{}".format(*reopened))
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(SETTLE)
            restored = window_size(hwnd)
            check("元に戻すと最大化前の寸法になる",
                  abs(restored[0] - plain[0]) <= 20 and abs(restored[1] - plain[1]) <= 20,
                  "{}x{}  （素は {}x{}）".format(restored[0], restored[1], plain[0], plain[1]))
            shut(proc, hwnd)
    finally:
        shutil.copy(backup, GUI_JSON)
        os.remove(backup)
        print("\ngui.json は元に戻した")


def main(argv):
    parser = argparse.ArgumentParser(
        description="MOD 同梱の設定画面を開いて撮る（TECH.md §3.12）")
    parser.add_argument("--only", default="",
                        help="名前にこれを含む MOD だけ（check_mods.py と同じ書式）")
    parser.add_argument("--window", action="store_true",
                        help="窓の記憶の往復を見る（settings\\gui.json を退避して戻す）")
    args = parser.parse_args(argv)

    rows = tools_of(args.only)
    if not rows:
        print('"tool" を宣言している MOD が無い{}'.format(
            "（--only {}）".format(args.only) if args.only else ""))
        return 1
    print("道具 {} 本 / 撮ったものは {}\n".format(len(rows), OUT_DIR))

    if args.window:
        check_window_memory(rows)
    else:
        check_open(rows)

    print()
    if failures:
        print("{} 件 失敗: {}".format(len(failures), ", ".join(failures)))
        return 1
    print("全て通った。**撮ったものを目で見ること**（中身が空・崩れは機械では分からない）:")
    print("   ", OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
