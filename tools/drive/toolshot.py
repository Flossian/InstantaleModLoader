# -*- coding: utf-8 -*-
r"""MOD の設定画面（`mod.json` の "tool"）を開き、指定のタブを選んで撮る（ゲームの外で走る）。

    python tools/drive/toolshot.py 911 様子           out/drive/tool_911_様子.png
    python tools/drive/toolshot.py 911 3 --out a.png   タブは番号（0 から）でもよい
    python tools/drive/toolshot.py 343 9 --frame 表情差分 --size 1000x1000
                                                       枠の見出し（の一部）までスクロールし、窓の大きさを決めて撮る

`tools/check_tool_screens.py` は別プロセスで開くので最初のタブしか撮れない
（`ttk.Notebook` のタブを外から送る押下では替えられない。あちらの説明）。
こちらは同じプロセスで道具の `main()` を呼び、`mainloop` に入るところで窓を捕まえて、タブを選んでから撮る。
`build_window()` を直に呼ばないのは、引数の形が道具ごとに違うため（`mod_dir`・`model`・`root_dir, game_dir`・引数なし）。
`main()` は引数なしで道具を開く入口なので、GUI の「設定」ボタンから開いたときと同じ窓になる。
渡す環境変数は設定画面が道具を開くときと同じ（`IML_ROOT` / `IML_STATE_DIR` / `IML_GAME_DIR` / `IML_MOD_SETTINGS`）。
"""
import argparse
import importlib.util
import inspect
import os
import sys
import time
import traceback

#: 窓を組んでから閉じるまでの上限（ミリ秒）。撮る前に固まったときの後始末。
SHOOT_TIMEOUT_MS = 60000
#: `mainloop` に入ってから撮るまでの待ち（ミリ秒）。後回しで組む欄が描かれるのを待つ。
SHOOT_DELAY_MS = 1500

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "runtime"))
import check_tool_screens as cts  # noqa: E402
import instantale_modloader as ml  # noqa: E402


def walk(widget):
    """子孫のウィジェットを深さ優先で。"""
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def notebooks(widget):
    from tkinter import ttk
    return [w for w in walk(widget) if isinstance(w, ttk.Notebook)]


def scroll_to_frame(win, text):
    """見出しに `text` を含む見えている `ttk.Labelframe` の上端まで、祖先のキャンバスを縦にスクロールする。見つかれば True。"""
    import tkinter as tk
    from tkinter import ttk
    frames = [w for w in walk(win) if isinstance(w, (ttk.Labelframe, tk.LabelFrame))
              and text in str(w.cget("text")) and w.winfo_ismapped()]
    if not frames:
        return False
    frame = frames[0]
    canvas = frame.master
    while canvas is not None and not isinstance(canvas, tk.Canvas):
        canvas = canvas.master
    if canvas is None:
        return True                 # スクロールしない画面。そのまま見えている
    win.update()
    box = canvas.bbox("all")
    height = box[3] if box and box[3] > 0 else 1
    top = frame.winfo_rooty() - canvas.winfo_rooty() + canvas.canvasy(0)
    canvas.yview_moveto(max(0.0, (top - 8) / height))
    return True


def run_main(tool):
    """道具の入口を引数なしで呼ぶ（`main(argv)` の形には空の並びを渡す）。"""
    entry = getattr(tool, "main", None)
    if entry is None:
        raise SystemExit("ERROR: the tool has no main()")
    try:
        takes_args = bool(inspect.signature(entry).parameters)
    except (TypeError, ValueError):
        takes_args = False
    try:
        entry([]) if takes_args else entry()
    except SystemExit:
        pass                        # 道具が自分で終わった。撮れたかは呼ぶ側が見る


def main():
    ap = argparse.ArgumentParser(description="open a mod's settings tool on one tab and capture it")
    ap.add_argument("mod", help="part of the mod folder name")
    ap.add_argument("tab", help="tab label or index")
    ap.add_argument("--out", default="", help="png path (default out/drive/tool_<mod>_<tab>.png)")
    ap.add_argument("--frame", default="", help="scroll to the labelframe whose title contains this text")
    ap.add_argument("--size", default="", help="window size WIDTHxHEIGHT before capturing")
    opts = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rows = cts.tools_of(opts.mod)
    if len(rows) != 1:
        raise SystemExit("ERROR: {!r} matches {} tool(s): {}".format(opts.mod, len(rows), [r[0] for r in rows]))
    _name, mod_dir, entry, _label = rows[0]
    os.environ["IML_ROOT"] = ROOT
    os.environ["IML_STATE_DIR"] = ml.state_dir(os.path.join(ROOT, "runtime"))
    os.environ["IML_GAME_DIR"] = os.environ.get("IML_GAME_DIR") or cts.game_dir()
    os.environ["IML_MOD_SETTINGS"] = os.path.join(ROOT, "settings", "mod_settings.json")
    # 道具の作法どおり MOD のフォルダへ移るので、その前に決める
    out = os.path.abspath(opts.out or os.path.join(ROOT, "out", "drive", "tool_{}_{}.png".format(opts.mod, opts.tab)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    os.chdir(mod_dir)
    spec = importlib.util.spec_from_file_location("drive_tool", os.path.join(mod_dir, entry))
    tool = importlib.util.module_from_spec(spec)
    sys.modules["drive_tool"] = tool     # dataclass などが自分のモジュールを引けるように
    spec.loader.exec_module(tool)

    import tkinter as tk
    shot = []
    entered = []
    original_mainloop = tk.Misc.mainloop

    def capture_on_mainloop(widget, n=0):
        if entered:
            return original_mainloop(widget, n)
        entered.append(widget)
        win = widget.winfo_toplevel()

        def close():
            try:
                win.destroy()
            except Exception:
                pass                # 既に閉じている

        def shoot():
            # Tk の後回しの中で投げると、窓を残したまま mainloop が回り続ける。必ず閉じる
            try:
                found = notebooks(win)
                if found:
                    nb = found[0]
                    labels = [nb.tab(t, "text") for t in nb.tabs()]
                    # 見出しの前後に空白を詰めて幅を取る道具がある（343 の ' 高度設定 '）。空白を除いて照らす
                    index = (int(opts.tab) if opts.tab.lstrip("-").isdigit()
                             else [label.strip() for label in labels].index(opts.tab.strip()))
                    nb.select(nb.tabs()[index])
                    picked = labels[index]
                elif opts.tab in ("0", "-"):
                    labels, picked = [], "(no tabs)"     # タブの無い画面（131）は画面全体を撮る
                else:
                    print("ERROR: the tool has no tabs (pass 0 to capture the whole window)")
                    return
                win.update()
                if opts.frame and not scroll_to_frame(win, opts.frame):
                    print("WARN: no frame titled {!r} on this tab".format(opts.frame))
                win.update()
                time.sleep(0.8)
                win.update()
                cts.capture(int(win.wm_frame(), 16), out)
                print("tabs {} -> {} {}".format(labels, picked, out))
                shot.append(out)
            except Exception:
                traceback.print_exc()
            finally:
                close()

        if opts.size:
            win.geometry(opts.size)
        win.after(SHOOT_DELAY_MS, shoot)
        # 撮る前に固まっても窓を残さない
        win.after(SHOOT_TIMEOUT_MS, close)
        try:
            return original_mainloop(widget, n)
        finally:
            close()

    tk.Misc.mainloop = capture_on_mainloop
    try:
        run_main(tool)
    finally:
        tk.Misc.mainloop = original_mainloop
    if not entered:
        print("ERROR: the tool returned without opening a window")
    if not shot:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
