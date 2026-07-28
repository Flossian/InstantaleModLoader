#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instantale ModLoader の GUI。

やることは3つだけ。

  1. `runtime/mods/` の mod を **適用順に**一覧で見せる
  2. 順序と有効/無効を編集して `load_order.json` に書き戻す
  3. ゲームを起動して、準備が整った時点で注入する

一覧の中身は `mod.json` から読む。**mod のコードは一切 import しない**
（ローダ本体の `_manifest` と同じ理由 ― 一覧を作るためだけに他人の mod の
トップレベルを走らせない）。並び順の意味は上が先で、上から順に適用される。

名前は日本語と英語を別の列に出す。`mod.json` は片方しか持たないことがあるので、
無い側はもう片方で埋める（列が空のまま並ぶより、同じ文字が2つ並ぶ方が読める）。

有効/無効は `load_order.json` の `"disabled"` に入る。フォルダ名を変えて切る
方式（先頭に `_`）を GUI から使うと、`"order"` の中の名前と食い違うため。

注入と起動は watcher.py の処理をそのまま呼ぶ。GUI 側で条件判定を書き直すと
`watch.bat` と挙動がずれるため、待ち方（インタプリタの初期化＋ウィンドウの出現）
は1か所に置いたままにする。

使い方:
    python gui.py       （通常は ..\\InstantaleModLoader.bat から開く）
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import injector
import watcher

# このファイルは tools/ にある。runtime/ と設定は1階層上（配布フォルダの根）。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODS_DIR = os.path.join(ROOT, "runtime", "mods")
ORDER_PATH = os.path.join(MODS_DIR, "load_order.json")
CONFIG_PATH = os.path.join(ROOT, "gui_config.json")

MANIFEST_NAME = "mod.json"

CHECKED, UNCHECKED = "☑", "☐"

FIND_POLL = 1.0      # ゲームのプロセスを探す間隔（秒）
FIND_TRIES = 60      # 何回まで探すか（Epic 経由だと立ち上がりが遅いので長めに）


# --------------------------------------------------------------------------
# mod.json / load_order.json の読み書き
# --------------------------------------------------------------------------
def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _text(value) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _localized(data: dict, key: str, default: str = "") -> tuple[str, str]:
    """`{"en":..., "ja":...}` でも素の文字列でも読み、(日本語, 英語) で返す。

    片方しか無ければもう片方で埋める。素の文字列1つのときは英語として扱う
    （ローダ本体の `_manifest` と同じ規則）。
    """
    value = data.get(key)
    if isinstance(value, dict):
        en, ja = _text(value.get("en")), _text(value.get("ja"))
    else:
        en, ja = _text(value), ""
    en = en or ja or default
    return ja or en, en


def read_mods() -> tuple[list[dict], set[str]]:
    """mods/ の mod を適用順に返す。合わせて無効になっているものの名前も返す。

    順序は `load_order.json`。載っていない mod は捨てずに末尾へ回し、
    載っているが実体が無いものは飛ばす（ローダ本体の `_discover` と同じ規則）。
    **無効なものも一覧には出す**（消えたように見せない）。
    """
    if not os.path.isdir(MODS_DIR):
        return [], set()

    found = []
    for name in sorted(os.listdir(MODS_DIR)):
        if name.startswith("_") or name.startswith("."):
            continue
        if os.path.isfile(os.path.join(MODS_DIR, name, MANIFEST_NAME)):
            found.append(name)

    data = _read_json(ORDER_PATH)
    if isinstance(data, dict):
        order, disabled = data.get("order"), data.get("disabled")
    else:
        order, disabled = data, None
    if not isinstance(order, list):
        order = []
    if not isinstance(disabled, list):
        disabled = []

    known = set(found)
    ordered = [n for n in order if n in known]
    ordered += [n for n in found if n not in ordered]

    mods = []
    for name in ordered:
        manifest = _read_json(os.path.join(MODS_DIR, name, MANIFEST_NAME)) or {}
        if not isinstance(manifest, dict):
            manifest = {}
        name_ja, name_en = _localized(manifest, "name", name)
        desc_ja, desc_en = _localized(manifest, "description")
        mods.append({
            "dir": name,
            "name_ja": name_ja,
            "name_en": name_en,
            "version": _text(manifest.get("version")),
            "author": _text(manifest.get("author")),
            "desc_ja": desc_ja,
            "desc_en": desc_en,
            "entry": _text(manifest.get("entry")) or "mod.py",
        })
    return mods, {n for n in disabled if isinstance(n, str)}


def write_order(names: list[str], disabled: set[str]) -> None:
    """順序と無効一覧を書き戻す。

    `disabled` は `names` の並びで書く。差分を見たときに一覧と同じ順で並ぶ方が
    追いやすいのと、無効にした順で溜まっていくのを避けるため。
    """
    off = [n for n in names if n in disabled]
    with open(ORDER_PATH, "w", encoding="utf-8") as fh:
        json.dump({"order": names, "disabled": off}, fh,
                  ensure_ascii=False, indent=2)
        fh.write("\n")


def read_config() -> dict:
    data = _read_json(CONFIG_PATH)
    return data if isinstance(data, dict) else {}


def write_config(data: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class App(ttk.Frame):
    # (キー, 見出し, 幅, 寄せ, 伸ばすか)
    COLUMNS = (
        ("on", "有効", 48, "center", False),
        ("order", "ロード順", 68, "center", False),
        ("name_ja", "Name（日本語）", 220, "w", True),
        ("name_en", "Name (English)", 220, "w", True),
        ("version", "Version", 66, "center", False),
        ("author", "Author", 140, "w", False),
    )

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)

        self.mods: list[dict] = []
        self.disabled: set[str] = set()
        self.drag: str | None = None      # ドラッグ中の mod のフォルダ名
        self.dirty = False
        self.busy = False
        # 注入は別スレッドで動くので、進捗はキュー越しに受け取って
        # メインスレッドの after で描く（tkinter は他スレッドから触れない）。
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()

        self._build()
        self.reload()
        self.after(100, self._drain_events)

    # -- 組み立て ----------------------------------------------------------
    def _build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Instantale ModLoader",
                  font=("Yu Gothic UI", 14, "bold")).pack(side="left")
        ttk.Label(head, text="上にあるものから順に適用される（行をドラッグして並べ替え）",
                  foreground="#666").pack(side="left", padx=(12, 0))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(10, 0))

        self.tree = ttk.Treeview(body, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", selectmode="browse", height=16)
        for key, title, width, anchor, stretch in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        # 無効な行は灰色にする。チェックだけだと、一覧を眺めたときに
        # 「効いていない mod がある」ことに気付きにくい。
        self.tree.tag_configure("off", foreground="#9a9a9a")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        self.tree.bind("<Button-1>", self._on_press)
        self.tree.bind("<B1-Motion>", self._on_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_release)
        self.tree.bind("<space>", lambda _e: self._toggle())
        self.tree.bind("<Double-1>", self._on_double)

        bar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        bar.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=bar.set)

        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        for label, delta in (("▲ 上へ", -1), ("▼ 下へ", 1)):
            ttk.Button(side, text=label, width=10,
                       command=lambda d=delta: self._move(d)).pack(pady=2)
        ttk.Separator(side).pack(fill="x", pady=6)
        ttk.Button(side, text="最上部へ", width=10,
                   command=lambda: self._move_to(0)).pack(pady=2)
        ttk.Button(side, text="最下部へ", width=10,
                   command=lambda: self._move_to(-1)).pack(pady=2)
        ttk.Separator(side).pack(fill="x", pady=6)
        ttk.Button(side, text="有効/無効", width=10,
                   command=self._toggle).pack(pady=2)
        ttk.Button(side, text="全て有効", width=10,
                   command=self._enable_all).pack(pady=2)
        ttk.Separator(side).pack(fill="x", pady=6)
        ttk.Button(side, text="保存", width=10,
                   command=self.save).pack(pady=2)
        ttk.Button(side, text="再読み込み", width=10,
                   command=self.reload).pack(pady=2)

        self.detail = ttk.Label(self, text="", foreground="#444",
                                wraplength=760, justify="left")
        self.detail.pack(fill="x", pady=(8, 0))

        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(10, 0))
        self.launch_btn = ttk.Button(foot, text="Mod を注入してゲームを起動",
                                     command=self.launch)
        self.launch_btn.pack(side="left")
        ttk.Button(foot, text="ゲームの場所を設定…",
                   command=self.choose_game).pack(side="left", padx=6)
        self.status = ttk.Label(foot, text="", foreground="#444")
        self.status.pack(side="left", padx=10)

    # -- 一覧 --------------------------------------------------------------
    def reload(self) -> None:
        self.mods, self.disabled = read_mods()
        self.dirty = False
        self._refresh()
        off = len(self.disabled & {m["dir"] for m in self.mods})
        self._set_status("{} 個の mod（有効 {} / 無効 {}）".format(
            len(self.mods), len(self.mods) - off, off))

    def _refresh(self, keep: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        n = 0
        for mod in self.mods:
            on = mod["dir"] not in self.disabled
            # ロード順の番号は**有効なものだけ**に振る。無効な行に番号があると、
            # 「何番目に読まれるか」が実際とずれて読めてしまう。
            if on:
                n += 1
            self.tree.insert("", "end", iid=mod["dir"],
                             tags=() if on else ("off",),
                             values=(CHECKED if on else UNCHECKED,
                                     n if on else "-",
                                     mod["name_ja"], mod["name_en"],
                                     mod["version"] or "-", mod["author"] or "-"))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)
        self._show_detail()

    def _show_detail(self) -> None:
        mod = self._selected()
        if not mod:
            self.detail.configure(text="")
            return
        state = "無効" if mod["dir"] in self.disabled else "有効"
        desc = mod["desc_ja"] or "（説明なし）"
        if mod["desc_en"] and mod["desc_en"] != mod["desc_ja"]:
            desc += "\n" + mod["desc_en"]
        self.detail.configure(
            text=f"{mod['dir']}  /  entry: {mod['entry']}  /  {state}\n{desc}")

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return next((m for m in self.mods if m["dir"] == sel[0]), None)

    # -- 並べ替え ----------------------------------------------------------
    def _move(self, delta: int) -> None:
        mod = self._selected()
        if not mod:
            return
        i = self.mods.index(mod)
        j = i + delta
        if not 0 <= j < len(self.mods):
            return
        self.mods[i], self.mods[j] = self.mods[j], self.mods[i]
        self._mark_dirty(mod["dir"])

    def _move_to(self, pos: int) -> None:
        mod = self._selected()
        if not mod:
            return
        self.mods.remove(mod)
        self.mods.insert(len(self.mods) if pos < 0 else pos, mod)
        self._mark_dirty(mod["dir"])

    def _mark_dirty(self, keep: str, what: str = "順序を変更した") -> None:
        self.dirty = True
        self._refresh(keep=keep)
        self._set_status(f"{what}（未保存）")

    # -- ドラッグでの並べ替え ------------------------------------------------
    #  行を掴んで動かす。Treeview には並べ替えの仕組みが無いので、
    #  「今どの行の上にいるか」を毎回引いて、その位置へ差し込み直す。
    def _on_press(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row or self.tree.identify_region(event.x, event.y) != "cell":
            self.drag = None
            return
        # 「有効」の列を押したときは並べ替えではなく切り替え。
        if self.tree.identify_column(event.x) == "#1":
            self.drag = None
            self.tree.selection_set(row)
            self._toggle()
            return
        self.drag = row

    def _on_drag(self, event: tk.Event) -> None:
        if not self.drag:
            return
        target = self.tree.identify_row(event.y)
        if not target or target == self.drag:
            return
        mod = next((m for m in self.mods if m["dir"] == self.drag), None)
        if mod is None:
            self.drag = None
            return
        dest = next(i for i, m in enumerate(self.mods) if m["dir"] == target)
        self.mods.remove(mod)
        self.mods.insert(dest, mod)
        self._mark_dirty(mod["dir"])

    def _on_release(self, _event: tk.Event) -> None:
        self.drag = None

    def _on_double(self, event: tk.Event) -> str | None:
        # 見出しのダブルクリックは列幅の自動調整に取られるので、行だけ拾う。
        if self.tree.identify_row(event.y):
            self._toggle()
            return "break"
        return None

    # -- 有効 / 無効 --------------------------------------------------------
    def _toggle(self) -> None:
        mod = self._selected()
        if not mod:
            return
        name = mod["dir"]
        if name in self.disabled:
            self.disabled.discard(name)
            what = f"{mod['name_ja']} を有効にした"
        else:
            self.disabled.add(name)
            what = f"{mod['name_ja']} を無効にした"
        self._mark_dirty(name, what)

    def _enable_all(self) -> None:
        if not self.disabled:
            return
        keep = self.tree.selection()
        self.disabled.clear()
        self._mark_dirty(keep[0] if keep else "", "全て有効にした")

    def save(self) -> None:
        try:
            write_order([m["dir"] for m in self.mods], self.disabled)
        except Exception as exc:
            messagebox.showerror("保存できない", f"{type(exc).__name__}: {exc}")
            return
        self.dirty = False
        off = len(self.disabled & {m["dir"] for m in self.mods})
        self._set_status("load_order.json に保存した"
                         f"（有効 {len(self.mods) - off} / 無効 {off}）")

    # -- 起動と注入 --------------------------------------------------------
    def choose_game(self) -> str:
        """ゲームの exe を選ばせて覚える。Epic の URL でも構わない。"""
        path = filedialog.askopenfilename(
            title="instantale.exe を選ぶ",
            filetypes=[("実行ファイル", "*.exe"), ("すべて", "*.*")])
        if path:
            cfg = read_config()
            cfg["game_path"] = path
            write_config(cfg)
            self._set_status(f"ゲームの場所を覚えた: {path}")
        return path

    def launch(self) -> None:
        if self.busy:
            return
        if self.dirty and messagebox.askyesno(
                "未保存の変更",
                "順序と有効/無効の変更が未保存。保存してから起動する？\n"
                "保存しない場合、注入されるのは保存済みの内容になる。"):
            self.save()

        running = injector.find_processes(injector.TARGET_EXE)
        game_path = ""
        if not running:
            game_path = read_config().get("game_path", "")
            if not game_path or not os.path.isfile(game_path):
                messagebox.showinfo(
                    "ゲームの場所が未設定",
                    "起動するゲームの場所が分からない。\n"
                    "instantale.exe を選んでほしい。")
                game_path = self.choose_game()
                if not game_path:
                    return

        self.busy = True
        self.launch_btn.state(["disabled"])
        threading.Thread(target=self._launch_worker,
                         args=(game_path,), daemon=True).start()

    def _launch_worker(self, game_path: str) -> None:
        """別スレッド。tkinter には触らず、進捗はキューへ流す。"""
        def report(msg: str) -> None:
            self.events.put(("status", msg))

        try:
            if game_path:
                report("ゲームを起動中…")
                subprocess.Popen([game_path], cwd=os.path.dirname(game_path))

            report("ゲームのプロセスを探している…")
            pid = None
            for _ in range(FIND_TRIES):
                procs = injector.find_processes(injector.TARGET_EXE)
                if len(procs) > 1:
                    # 複数動いていると取り違える。injector.py と同じく自動で選ばない。
                    self.events.put(("error", "instantale.exe が複数動いている。"
                                              "1つだけにしてほしい。"))
                    return
                if procs:
                    pid = procs[0][0]
                    break
                time.sleep(FIND_POLL)
            if pid is None:
                self.events.put(("error", "ゲームのプロセスが見つからなかった。"))
                return

            report(f"pid {pid}: 準備待ち（Python の初期化とウィンドウの出現）…")
            if not watcher.wait_until_ready(pid):
                self.events.put(("error", f"pid {pid}: 準備が整わなかった。"))
                return

            report(f"pid {pid}: 注入中…")
            injector.rotate_logs(None, log=report)
            ok = watcher.inject_pid(pid)
            if ok:
                self.events.put(("done", f"pid {pid} に注入した"
                                         "（詳細は out/modloader.log）"))
            else:
                self.events.put(("error", f"pid {pid}: 注入に失敗した"
                                          "（out/bootstrap.log を見る）"))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, msg = self.events.get_nowait()
                if kind == "status":
                    self._set_status(msg)
                elif kind == "done":
                    self._finish(msg)
                elif kind == "error":
                    self._finish(msg)
                    messagebox.showerror("失敗", msg)
        except queue.Empty:
            pass
        self.after(200, self._drain_events)

    def _finish(self, msg: str) -> None:
        self.busy = False
        self.launch_btn.state(["!disabled"])
        self._set_status(msg)

    def _set_status(self, msg: str) -> None:
        self.status.configure(text=msg)


def main() -> int:
    if os.name != "nt":
        print("ERROR: Windows 用。", file=sys.stderr)
        return 2
    root = tk.Tk()
    root.title("Instantale ModLoader")
    root.geometry("880x560")
    root.minsize(720, 420)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
