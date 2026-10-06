# -*- coding: utf-8 -*-
r"""MOD の設定画面（`mod.json` の "tool"）を組み、指定のタブを選んで撮る（ゲームの外で走る）。

    python tools/drive/toolshot.py 911 様子           out/drive/tool_911_様子.png
    python tools/drive/toolshot.py 911 3 --out a.png   タブは番号（0 から）でもよい

`tools/check_tool_screens.py` は別プロセスで開くので最初のタブしか撮れない
（`ttk.Notebook` のタブを外から送る押下では替えられない。あちらの説明）。
こちらは同じプロセスで窓を組み（`tool.py` の `build_window()`）、タブを選んでから撮る。
渡す環境変数は設定画面が道具を開くときと同じ（`IML_ROOT` / `IML_STATE_DIR` / `IML_GAME_DIR` / `IML_MOD_SETTINGS`）。
"""
import argparse
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "runtime"))
import check_tool_screens as cts  # noqa: E402
import instantale_modloader as ml  # noqa: E402


def notebooks(widget):
    from tkinter import ttk
    found = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Notebook):
            found.append(child)
        found.extend(notebooks(child))
    return found


def main():
    ap = argparse.ArgumentParser(description="open a mod's settings tool on one tab and capture it")
    ap.add_argument("mod", help="part of the mod folder name")
    ap.add_argument("tab", help="tab label or index")
    ap.add_argument("--out", default="", help="png path (default out/drive/tool_<mod>_<tab>.png)")
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
    os.chdir(mod_dir)
    spec = importlib.util.spec_from_file_location("drive_tool", os.path.join(mod_dir, entry))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    root = tool.build_window(mod_dir)
    out = opts.out or os.path.join(ROOT, "out", "drive", "tool_{}_{}.png".format(opts.mod, opts.tab))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    def shoot():
        found = notebooks(root)
        if not found:
            print("ERROR: the tool has no tabs")
            root.destroy()
            return
        nb = found[0]
        labels = [nb.tab(t, "text") for t in nb.tabs()]
        index = int(opts.tab) if opts.tab.lstrip("-").isdigit() else labels.index(opts.tab)
        nb.select(nb.tabs()[index])
        root.update()
        time.sleep(0.8)
        root.update()
        cts.capture(int(root.wm_frame(), 16), out)
        print("tabs {} -> {} {}".format(labels, labels[index], out))
        root.destroy()

    root.after(1500, shoot)
    root.mainloop()


if __name__ == "__main__":
    main()
