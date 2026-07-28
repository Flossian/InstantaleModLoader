#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instantale ModLoader の GUI。

やることは6つ。

  1. `runtime/mods/` の mod を **適用順に**一覧で見せる
  2. 順序と有効/無効を編集して `load_order.json` に書き戻す
  3. **mod ごとの設定**を編集して `mod_settings.json` に書き戻す
  4. ゲームを起動して、準備が整った時点で注入する
  5. 注入の**結果**を出す（何本入ったか、何が失敗したか、どこが重なったか）
  6. mod を追加する / 外す

一覧の中身は `mod.json` から読む。**mod のコードは一切 import しない**
（一覧を作るためだけに他人の mod のトップレベルを走らせない）。並び順の意味は
上が先で、上から順に適用される。

探索と適用順の判定は**ローダ本体の `discover()` を呼ぶ**。以前はここに同じ規則
（`_` で始まるフォルダを除く / `mod.json` を持つものだけ / 未記載は末尾）を
書き写していたので、片方だけ直すと一覧と実際の適用順がずれた。

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
import shutil
import subprocess
import sys
import queue
import threading
import time
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk

# このファイルは tools/ にある。runtime/ と設定は1階層上（配布フォルダの根）。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "runtime"))

import injector                                  # noqa: E402
import watcher                                   # noqa: E402
import instantale_modloader as ml                # noqa: E402
from instantale_modloader import config as C     # noqa: E402

RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
ORDER_PATH = os.path.join(MODS_DIR, ml.ORDER_NAME)
STATUS_PATH = os.path.join(ROOT, "out", ml.STATUS_NAME)

# 利用者が選んだものは全部 settings/ に集める（mod ごとの設定は
# instantale_modloader.config が同じフォルダへ mod_settings.json を書く）。
# ここに入るのは「このウィンドウの覚えていること」＝ゲームの場所と窓の大きさ。
SETTINGS_DIR = C.settings_dir(RUNTIME_DIR)
CONFIG_PATH = os.path.join(SETTINGS_DIR, "gui.json")

CHECKED, UNCHECKED = "☑", "☐"

FIND_POLL = 1.0      # ゲームのプロセスを探す間隔（秒）
FIND_TRIES = 60      # 何回まで探すか（Epic 経由だと立ち上がりが遅いので長めに）

# 適用結果（status.json の ["mods"]）を日本語にする。
RESULT_TEXT = {
    "ok": "適用",
    "no-entry": "入口なし",
    "load-error": "読込失敗",
    "apply-error": "適用失敗",
    "no-apply": "apply なし",
    "api-too-new": "ローダが古い",
    "api-too-old": "mod が古い",
}


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# --------------------------------------------------------------------------
# mod の一覧・順序・設定
# --------------------------------------------------------------------------
def read_mods() -> dict:
    """一覧に出すものを揃えて返す。判定はローダの `discover()` に任せる。

    **無効なものも一覧には出す**（消えたように見せない）。適用順の番号は
    有効なものだけに振る。
    """
    found = ml.discover(MODS_DIR)
    disabled = set(found["disabled"])

    mods = []
    for name in found["listed"]:
        manifest = found["manifests"].get(name) or {}
        mods.append({
            "dir": name,
            "name_ja": (manifest.get("name") or {}).get("ja") or name,
            "name_en": (manifest.get("name") or {}).get("en") or name,
            "desc_ja": (manifest.get("description") or {}).get("ja") or "",
            "desc_en": (manifest.get("description") or {}).get("en") or "",
            "version": manifest.get("version") or "",
            "author": manifest.get("author") or "",
            "entry": manifest.get("entry") or "mod.py",
            "api": manifest.get("api", ml.DEFAULT_API),
            "settings": manifest.get("settings") or {},
            "after": manifest.get("after") or [],
            "before": manifest.get("before") or [],
        })
    return {"mods": mods, "disabled": disabled, "problems": found["problems"]}


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
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def update_config(**values) -> None:
    """`settings/gui.json` の一部だけ書き換える。

    覚えていることが増えても（窓の位置・ゲームの場所…）互いを消さないように、
    必ず読んでから書く。**書けなくても止めない** ― 設定を残せないことと、
    ウィンドウが使えないことは別の話なので。
    """
    try:
        cfg = read_config()
        cfg.update(values)
        write_config(cfg)
    except Exception:
        pass


# --------------------------------------------------------------------------
# ウィンドウの大きさと位置
# --------------------------------------------------------------------------
GEOMETRY_DEFAULT = "1000x620"


def _on_screen(root: tk.Misc, geom: str) -> bool:
    """`WxH+X+Y` が画面の中に居るか。

    覚えた位置をそのまま使うと、モニタを外した後などに**画面の外へ出て掴めない
    窓**になる。左上が仮想デスクトップの内側にあることだけ確かめて、外れていたら
    位置は捨てて大きさだけ使う。
    """
    try:
        size, x, y = geom.replace("+-", "+~").split("+")
        x, y = int(x.replace("~", "-")), int(y.replace("~", "-"))
        w, h = (int(v) for v in size.split("x"))
    except Exception:
        return False
    # 仮想デスクトップ（マルチモニタ全体）の範囲。左と上は負にもなりうる。
    vw, vh = root.winfo_vrootwidth(), root.winfo_vrootheight()
    vx, vy = root.winfo_vrootx(), root.winfo_vrooty()
    # タイトルバーを掴める程度に見えていれば良しとする。
    return (vx - w + 120 <= x <= vx + vw - 120) and (vy <= y <= vy + vh - 60)


def restore_geometry(root: tk.Tk, cfg: dict) -> None:
    """前回の大きさと位置に戻す。壊れた値なら既定で開く。"""
    geom = cfg.get("window")
    if isinstance(geom, str) and "x" in geom:
        root.geometry(geom if _on_screen(root, geom) else geom.split("+")[0])
    else:
        root.geometry(GEOMETRY_DEFAULT)
    if cfg.get("window_maximized"):
        root.state("zoomed")


def read_status() -> dict:
    """`out/status.json` を読む。無ければ空。

    これがこのウィンドウと「実際に動いたゲーム」の唯一の接点。ローダが boot の
    最後に書き出す（`instantale_modloader.write_status`）。注入が成功したかどうかと
    **mod が入ったかどうかは別の話**なので、ここを読まないと「28個中3個が失敗」を
    利用者に出せない。
    """
    data = _read_json(STATUS_PATH)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# mod の追加
# --------------------------------------------------------------------------
def install_from_zip(zip_path: str) -> list[str]:
    """zip を `runtime/mods/` へ展開する。入っている mod のフォルダ名を返す。

    `mod.json` を持つフォルダを mod とみなす（ローダの探索規則と同じ）。zip の
    作り方は2通りあるので両方受ける。

        mymod.zip/mod.json              中身だけを固めたもの → zip 名をフォルダ名にする
        mymod.zip/mymod/mod.json        フォルダごと固めたもの → その名前を使う

    展開先を絶対パスで検査してから書く。zip の中の名前は信用できない
    （`../` を含む細工で mods/ の外へ書ける）。
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise ValueError("zip が空")

        # mod.json の位置から、mod のフォルダがどこかを決める。
        roots = set()
        for name in names:
            parts = name.replace("\\", "/").split("/")
            if parts[-1] != ml.MANIFEST_NAME:
                continue
            roots.add("/".join(parts[:-1]))
        if not roots:
            raise ValueError("{} が見つからない（mod の zip ではない）".format(
                ml.MANIFEST_NAME))

        installed = []
        for root in sorted(roots):
            if root == "":
                # 中身だけの zip。zip のファイル名をフォルダ名にする。
                folder = os.path.splitext(os.path.basename(zip_path))[0]
            else:
                folder = root.split("/")[-1]
            dest = os.path.abspath(os.path.join(MODS_DIR, folder))
            if not dest.startswith(os.path.abspath(MODS_DIR) + os.sep):
                raise ValueError("展開先が mods/ の外を指している: {}".format(folder))

            prefix = (root + "/") if root else ""
            members = [n for n in names if n.replace("\\", "/").startswith(prefix)]
            for member in members:
                rel = member.replace("\\", "/")[len(prefix):]
                if not rel or rel.startswith("../") or ".." in rel.split("/"):
                    continue
                target = os.path.abspath(os.path.join(dest, rel))
                if not target.startswith(dest + os.sep) and target != dest:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
            installed.append(folder)
        return installed


def install_from_folder(src: str) -> str:
    """フォルダを `runtime/mods/` へ複製する。フォルダ名を返す。"""
    src = os.path.abspath(src)
    if not os.path.isfile(os.path.join(src, ml.MANIFEST_NAME)):
        raise ValueError("{} が無い（mod のフォルダではない）".format(ml.MANIFEST_NAME))
    folder = os.path.basename(src.rstrip(os.sep))
    dest = os.path.join(MODS_DIR, folder)
    if os.path.abspath(dest) == src:
        return folder      # もう mods/ の中にある
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return folder


# --------------------------------------------------------------------------
# 設定のウィンドウ
# --------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    """1つの mod の設定を編集する。宣言（`mod.json` の "settings"）に従って組む。

    既定と同じ値は**書かない**。`mod_settings.json` に残すのは利用者が変えたものだけで、
    そうしておくと mod の既定値が新しい版で変わったときにその変更が届く
    （全部書き出すと、既定を上書きし続ける形になって永久に古い値で動く）。

    窓の幅と位置は覚える（`settings/gui.json` の `settings_window`）。**mod ごとでは
    なく1つ**で、どの mod の設定を開いても同じ場所に出る ― 設定を見て回るときに
    毎回同じ所に出る方が追いやすい。高さは覚えない。設定の数で変わるので、
    覚えた高さを当てると項目が切れるか余白が空く。
    """

    def __init__(self, master: tk.Misc, mod: dict, chosen: dict):
        super().__init__(master)
        self.title("{} の設定".format(mod["name_ja"]))
        self.transient(master)
        self.resizable(True, False)
        self.mod = mod
        self.result: dict | None = None
        self.vars: dict[str, tuple[dict, tk.Variable]] = {}

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=mod["dir"], foreground="#666").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        row = 1
        for name, decl in mod["settings"].items():
            value = chosen.get(name, decl["default"])
            ttk.Label(frame, text=decl["label"]["ja"]).grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            widget, var = self._field(frame, decl, value)
            widget.grid(row=row, column=1, sticky="ew", pady=3)
            self.vars[name] = (decl, var)
            row += 1
            note = decl["note"]["ja"]
            if note:
                ttk.Label(frame, text=note, foreground="#777",
                          wraplength=420, justify="left").grid(
                    row=row, column=1, sticky="w", pady=(0, 6))
                row += 1
            # 既定値を必ず見せる。「元に戻したい」ときに何に戻すのかが分かるように。
            ttk.Label(frame, text="既定: {!r}".format(decl["default"]),
                      foreground="#999").grid(row=row, column=1, sticky="w")
            row += 1

        frame.columnconfigure(1, weight=1)
        bar = ttk.Frame(frame)
        bar.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="既定に戻す", command=self._reset).pack(side="left", padx=4)
        ttk.Button(bar, text="キャンセル", command=self._close).pack(side="left", padx=4)
        ttk.Button(bar, text="OK", command=self._ok).pack(side="left")

        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._restore()
        self.grab_set()
        self.wait_window()

    # -- 窓の幅と位置 --------------------------------------------------------
    def _restore(self) -> None:
        geom = read_config().get("settings_window")
        if not isinstance(geom, str) or "x" not in geom:
            return          # 覚えていなければ Tk の既定の置き場所に任せる
        # 中身を組み終えてからでないと、必要な高さが分からない。
        self.update_idletasks()
        try:
            width = int(geom.split("x")[0])
        except ValueError:
            return
        width = max(width, self.winfo_reqwidth())
        size = "{}x{}".format(width, self.winfo_reqheight())
        if "+" in geom and _on_screen(self, geom):
            pos = geom[geom.index("+"):]
        else:
            # 覚えた位置が画面の外。大きさだけ指定すると画面の左上に張り付くので、
            # 呼び出し元の窓に重ねる（Tk が transient に選ぶのと同じ見え方）。
            master = self.master
            pos = "+{}+{}".format(master.winfo_rootx() + 60,
                                  master.winfo_rooty() + 60)
        self.geometry(size + pos)

    def _close(self) -> None:
        update_config(settings_window=self.geometry())
        self.destroy()

    def _field(self, parent, decl, value):
        kind = decl["type"]
        if kind == "bool":
            var = tk.BooleanVar(value=bool(value))
            return ttk.Checkbutton(parent, variable=var), var
        if kind == "choice":
            var = tk.StringVar(value="" if value is None else str(value))
            box = ttk.Combobox(parent, textvariable=var, state="readonly",
                               values=[str(v) for v in decl["values"]])
            return box, var
        # int / float / str。空欄は allow_null の設定で「未指定」を表す。
        var = tk.StringVar(value="" if value is None else str(value))
        return ttk.Entry(parent, textvariable=var), var

    def _reset(self) -> None:
        for name, (decl, var) in self.vars.items():
            default = decl["default"]
            if decl["type"] == "bool":
                var.set(bool(default))
            else:
                var.set("" if default is None else str(default))

    def _ok(self) -> None:
        chosen, bad = {}, []
        for name, (decl, var) in self.vars.items():
            raw = var.get()
            if isinstance(raw, str) and raw.strip() == "":
                raw = None      # 空欄 = 未指定
            ok, value, why = C.coerce(decl, raw)
            if not ok:
                bad.append("{}: {}".format(decl["label"]["ja"], why))
                continue
            # 既定と同じなら書かない（上記の docstring 参照）。
            if value != decl["default"]:
                chosen[name] = value
        if bad:
            messagebox.showerror("値を使えない", "\n".join(bad), parent=self)
            return
        self.result = chosen
        self._close()


# --------------------------------------------------------------------------
# GUI 本体
# --------------------------------------------------------------------------
class App(ttk.Frame):
    # (キー, 見出し, 幅, 寄せ, 伸ばすか)
    COLUMNS = (
        ("on", "有効", 44, "center", False),
        ("order", "順", 40, "center", False),
        ("name_ja", "Name（日本語）", 200, "w", True),
        ("name_en", "Name (English)", 200, "w", True),
        ("cfg", "設定", 44, "center", False),
        ("version", "Ver", 46, "center", False),
        ("author", "Author", 120, "w", False),
        ("state", "前回", 76, "center", False),
    )

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)

        self.mods: list[dict] = []
        self.disabled: set[str] = set()
        self.problems: list[str] = []
        self.settings: dict = {}          # mod_settings.json の中身
        self.status: dict = {}            # out/status.json の中身
        self.drag: str | None = None      # ドラッグ中の mod のフォルダ名
        self.dirty = False
        self.busy = False
        # 注入は別スレッドで動くので、進捗はキュー越しに受け取って
        # メインスレッドの after で描く（tkinter は他スレッドから触れない）。
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        # 最大化していない状態の大きさと位置。最大化中の値を覚えると、次に
        # 開いたときに画面いっぱいの「普通の窓」になってしまうので分けて持つ。
        self.geom = master.geometry()

        self._build()
        self.reload()
        # 進捗を拾う繰り返し。閉じるときに止める（止めないと、消えた widget を
        # 相手に1回だけ走って Tk がエラーを吐く）。
        self._tick = self.after(100, self._drain_events)

        master.bind("<Configure>", self._on_configure)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- 組み立て ----------------------------------------------------------
    def _build(self) -> None:
        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Instantale ModLoader",
                  font=("Yu Gothic UI", 14, "bold")).pack(side="left")
        ttk.Label(head, text="上にあるものから順に適用される（行をドラッグして並べ替え）",
                  foreground="#666").pack(side="left", padx=(12, 0))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(8, 0))

        self.tree = ttk.Treeview(body, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", selectmode="browse", height=15)
        for key, title, width, anchor, stretch in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        # 無効な行は灰色にする。チェックだけだと、一覧を眺めたときに
        # 「効いていない mod がある」ことに気付きにくい。
        self.tree.tag_configure("off", foreground="#9a9a9a")
        # 前回の注入で入らなかった mod は赤。ログを開かずに気付けるように。
        self.tree.tag_configure("bad", foreground="#b00020")
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
            ttk.Button(side, text=label, width=13,
                       command=lambda d=delta: self._move(d)).pack(pady=2)
        ttk.Button(side, text="最上部へ", width=13,
                   command=lambda: self._move_to(0)).pack(pady=2)
        ttk.Button(side, text="最下部へ", width=13,
                   command=lambda: self._move_to(-1)).pack(pady=2)
        ttk.Separator(side).pack(fill="x", pady=6)
        ttk.Button(side, text="有効/無効", width=13,
                   command=self._toggle).pack(pady=2)
        ttk.Button(side, text="全て有効", width=13,
                   command=self._enable_all).pack(pady=2)
        self.cfg_btn = ttk.Button(side, text="設定…", width=13,
                                  command=self._edit_settings)
        self.cfg_btn.pack(pady=2)
        ttk.Separator(side).pack(fill="x", pady=6)
        ttk.Button(side, text="MOD を追加…", width=13,
                   command=self.add_mod).pack(pady=2)
        self.open_btn = ttk.Button(side, text="MOD を開く", width=13,
                                   command=self.open_mod_dir)
        self.open_btn.pack(pady=2)
        ttk.Button(side, text="mods/ を開く", width=13,
                   command=self.open_mods_dir).pack(pady=2)
        ttk.Separator(side).pack(fill="x", pady=6)
        ttk.Button(side, text="保存", width=13,
                   command=self.save).pack(pady=2)
        ttk.Button(side, text="再読み込み", width=13,
                   command=self.reload).pack(pady=2)

        self.detail = ttk.Label(self, text="", foreground="#444",
                                wraplength=860, justify="left")
        self.detail.pack(fill="x", pady=(8, 0))
        # 宣言と実体のずれ（順序・依存・非互換）。何も無ければ行ごと出さない。
        self.warn = ttk.Label(self, text="", foreground="#a05000",
                              wraplength=860, justify="left")
        self.warn.pack(fill="x")

        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=(10, 0))
        self.launch_btn = ttk.Button(foot, text="Mod を注入してゲームを起動",
                                     command=self.launch)
        self.launch_btn.pack(side="left")
        ttk.Button(foot, text="ゲームの場所を設定…",
                   command=self.choose_game).pack(side="left", padx=6)
        self.unload_btn = ttk.Button(foot, text="MOD を外す", command=self.unload)
        self.unload_btn.pack(side="left")
        self.status_label = ttk.Label(foot, text="", foreground="#444")
        self.status_label.pack(side="left", padx=10)

    # -- 一覧 --------------------------------------------------------------
    def reload(self) -> None:
        found = read_mods()
        self.mods = found["mods"]
        self.disabled = found["disabled"]
        self.problems = found["problems"]
        self.settings = C.load_store(RUNTIME_DIR)
        self.status = read_status()
        self.dirty = False
        self._refresh()

        off = len(self.disabled & {m["dir"] for m in self.mods})
        msg = "{} 個の mod（有効 {} / 無効 {}）".format(
            len(self.mods), len(self.mods) - off, off)
        results = self.status.get("mods") or {}
        if results:
            bad = sorted(k for k, v in results.items() if v != "ok")
            ok = len(results) - len(bad)
            msg += " ｜ 前回の注入: {}/{} 適用".format(ok, len(results))
            if bad:
                msg += "（失敗 {}）".format(len(bad))
        self._set_status(msg)
        self._show_warnings()

    def _show_warnings(self) -> None:
        lines = list(self.problems)
        patches = self.status.get("patches") or {}
        unresolved = patches.get("unresolved") or []
        if unresolved:
            # 対象が見つからない＝ゲーム側が変わった可能性を最初に疑う。
            lines.append("前回の注入で対象が見つからなかったフック {} 件"
                         "（ゲームが更新された可能性）".format(len(unresolved)))
        self.warn.configure(text="\n".join("⚠ " + line for line in lines))

    def _refresh(self, keep: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        results = self.status.get("mods") or {}
        n = 0
        for mod in self.mods:
            name = mod["dir"]
            on = name not in self.disabled
            # 適用順の番号は**有効なものだけ**に振る。無効な行に番号があると、
            # 「何番目に読まれるか」が実際とずれて読めてしまう。
            if on:
                n += 1
            result = results.get(name, "")
            tags = ()
            if not on:
                tags = ("off",)
            elif result and result != "ok":
                tags = ("bad",)
            # 設定を持つ mod だけ印を出す。持たない mod で「設定…」を押しても
            # 何も無いことが、一覧の時点で分かるように。
            changed = bool(self.settings.get(name))
            cfg = ("●" if changed else "○") if mod["settings"] else ""
            self.tree.insert("", "end", iid=name, tags=tags,
                             values=(CHECKED if on else UNCHECKED,
                                     n if on else "-",
                                     mod["name_ja"], mod["name_en"], cfg,
                                     mod["version"] or "-", mod["author"] or "-",
                                     RESULT_TEXT.get(result, result or "-")))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)
        self._show_detail()

    def _show_detail(self) -> None:
        mod = self._selected()
        if not mod:
            self.detail.configure(text="")
            self.cfg_btn.state(["disabled"])
            self.open_btn.state(["disabled"])
            return
        self.cfg_btn.state(["!disabled"] if mod["settings"] else ["disabled"])
        self.open_btn.state(["!disabled"])

        state = "無効" if mod["dir"] in self.disabled else "有効"
        bits = ["{}  /  entry: {}  /  API {}  /  {}".format(
            mod["dir"], mod["entry"], mod["api"], state)]
        desc = mod["desc_ja"] or "（説明なし）"
        if mod["desc_en"] and mod["desc_en"] != mod["desc_ja"]:
            desc += "\n" + mod["desc_en"]
        bits.append(desc)

        # 適用順の制約は一覧の並びからは読めないので、選んだときに出す。
        for key, word in (("after", "これより後に適用"), ("before", "これより先に適用")):
            if mod[key]:
                bits.append("{}: {}".format(word, ", ".join(mod[key])))
        chosen = self.settings.get(mod["dir"]) or {}
        if chosen:
            bits.append("変更した設定: " + ", ".join(
                "{}={!r}".format(k, v) for k, v in sorted(chosen.items())))

        # 前回の注入で当てた対象。どこを触る mod なのかは、これが一番確か。
        by_mod = ((self.status.get("patches") or {}).get("by_mod") or {})
        targets = by_mod.get(mod["dir"]) or []
        if targets:
            shown = ", ".join(targets[:4])
            if len(targets) > 4:
                shown += " ほか {} 件".format(len(targets) - 4)
            bits.append("前回当てた対象: " + shown)
        self.detail.configure(text="\n".join(bits))

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
        column = self.tree.identify_column(event.x)
        # 「有効」の列を押したときは並べ替えではなく切り替え。
        if column == "#1":
            self.drag = None
            self.tree.selection_set(row)
            self._toggle()
            return
        # 「設定」の列は設定を開く。
        if column == "#5":
            self.drag = None
            self.tree.selection_set(row)
            self._edit_settings()
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

    # -- 設定 --------------------------------------------------------------
    def _edit_settings(self) -> None:
        mod = self._selected()
        if not mod:
            return
        if not mod["settings"]:
            messagebox.showinfo(
                "設定は無い",
                "{} は GUI から変えられる設定を宣言していない。\n"
                "コード側の定数を直接編集する形になる。".format(mod["name_ja"]))
            return
        chosen = dict(self.settings.get(mod["dir"]) or {})
        dialog = SettingsDialog(self.winfo_toplevel(), mod, chosen)
        if dialog.result is None:
            return          # キャンセル

        store = C.load_store(RUNTIME_DIR)
        store[mod["dir"]] = dialog.result
        try:
            C.save_store(RUNTIME_DIR, store)
        except Exception as exc:
            messagebox.showerror("保存できない", f"{type(exc).__name__}: {exc}")
            return
        self.settings = C.load_store(RUNTIME_DIR)
        self._refresh(keep=mod["dir"])
        if dialog.result:
            self._set_status("{} の設定を保存した（{} 件変更・次の注入から効く）".format(
                mod["name_ja"], len(dialog.result)))
        else:
            self._set_status("{} の設定を既定に戻した".format(mod["name_ja"]))

    # -- mod の追加とフォルダ ------------------------------------------------
    def add_mod(self) -> None:
        path = filedialog.askopenfilename(
            title="mod の zip を選ぶ（フォルダを入れたい場合はキャンセル）",
            filetypes=[("zip", "*.zip"), ("すべて", "*.*")])
        try:
            if path:
                added = install_from_zip(path)
            else:
                folder = filedialog.askdirectory(title="mod のフォルダを選ぶ")
                if not folder:
                    return
                added = [install_from_folder(folder)]
        except Exception as exc:
            messagebox.showerror("追加できない", f"{type(exc).__name__}: {exc}")
            return

        self.reload()
        # 置いただけで動く（順序ファイルに無い mod は末尾に回る）が、順序は
        # 保存しておかないと宣言されないままになる。
        self.save()
        self._set_status("追加した: {}（一覧の末尾・次の注入から効く）".format(
            ", ".join(added)))
        if added and self.tree.exists(added[0]):
            self.tree.selection_set(added[0])
            self.tree.see(added[0])

    def open_mod_dir(self) -> None:
        """選んでいる mod のフォルダをエクスプローラで開く。

        設定欄に載らない値（辞書やタプル）を直に編むときと、同梱データや
        `mod.json` を覗くときの入口。一覧で選んでいるものと同じ場所が開く。
        """
        mod = self._selected()
        if not mod:
            self._set_status("先に一覧から mod を選ぶ")
            return
        self._open(os.path.join(MODS_DIR, mod["dir"]))

    def open_mods_dir(self) -> None:
        self._open(MODS_DIR)

    def _open(self, path: str) -> None:
        try:
            os.startfile(path)              # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("開けない", f"{type(exc).__name__}: {exc}")

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

        self._start(self._launch_worker, game_path)

    def unload(self) -> None:
        """当てたパッチを剥がす。ゲームは終了しない。

        「完全に元通り」ではないことを押す前に断る。剥がせるのは差し替えた関数だけで、
        既に起きた副作用やセーブに書かれた値は戻らない。
        """
        if self.busy:
            return
        procs = injector.find_processes(injector.TARGET_EXE)
        if not procs:
            messagebox.showinfo("ゲームが動いていない",
                                "外す相手が居ない（MOD はプロセスと一緒に消える）。")
            return
        if len(procs) > 1:
            messagebox.showerror("複数動いている",
                                 "instantale.exe が複数動いている。1つだけにしてほしい。")
            return
        if not messagebox.askyesno(
                "MOD を外す",
                "当てたパッチを剥がして素の動作に戻す（ゲームは終了しない）。\n\n"
                "戻らないものがある:\n"
                "  ・起動時に一度だけ走った処理の結果\n"
                "  ・MOD がセーブに書いた値（パーティ・依頼など）\n\n"
                "完全に素のゲームで確かめたいなら、注入せずに起動し直すこと。"):
            return
        self._start(self._unload_worker, procs[0][0])

    def _start(self, worker, arg) -> None:
        self.busy = True
        self.launch_btn.state(["disabled"])
        self.unload_btn.state(["disabled"])
        threading.Thread(target=worker, args=(arg,), daemon=True).start()

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
                self.events.put(("done", f"pid {pid} に注入した"))
            else:
                self.events.put(("error", f"pid {pid}: 注入に失敗した"
                                          "（out/bootstrap.log を見る）"))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def _unload_worker(self, pid: int) -> None:
        try:
            self.events.put(("status", f"pid {pid}: パッチを剥がしている…"))
            payload = injector.make_bootstrap(
                injector.RUNTIME_DIR, injector.OUT_DIR, injector.BOOT_LOG,
                action="unload")
            rc = injector.inject(pid, payload)
            if rc == 0:
                self.events.put(("done", f"pid {pid}: MOD を外した"))
            else:
                self.events.put(("error", f"pid {pid}: 外せなかった"
                                          f"（PyRun_SimpleString が {rc}）"))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, msg = self.events.get_nowait()
                if kind == "status":
                    self._set_status(msg)
                elif kind == "done":
                    self._finish(msg, reload=True)
                elif kind == "error":
                    self._finish(msg)
                    messagebox.showerror("失敗", msg)
        except queue.Empty:
            pass
        self._tick = self.after(200, self._drain_events)

    def _finish(self, msg: str, reload: bool = False) -> None:
        self.busy = False
        self.launch_btn.state(["!disabled"])
        self.unload_btn.state(["!disabled"])
        if reload:
            # 結果（status.json）はローダが書き出す。少し待ってから読む
            # ―注入が返った直後はまだ boot の途中のことがある。
            self.after(1500, self.reload)
        self._set_status(msg)

    def _set_status(self, msg: str) -> None:
        self.status_label.configure(text=msg)

    # -- 窓の大きさと位置 ----------------------------------------------------
    def _on_configure(self, event: tk.Event) -> None:
        # 中の部品が動いたときにも飛んでくるので、窓そのものの分だけ拾う。
        master = self.winfo_toplevel()
        if event.widget is master and master.state() == "normal":
            self.geom = master.geometry()

    def _on_close(self) -> None:
        master = self.winfo_toplevel()
        update_config(window=self.geom,
                      window_maximized=(master.state() == "zoomed"))
        if self._tick is not None:
            self.after_cancel(self._tick)
            self._tick = None
        master.destroy()


def main() -> int:
    if os.name != "nt":
        print("ERROR: Windows 用。", file=sys.stderr)
        return 2
    root = tk.Tk()
    root.title("Instantale ModLoader")
    root.minsize(820, 480)
    # 大きさと位置は閉じるときに settings/gui.json へ入る（App._on_close）。
    restore_geometry(root, read_config())
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
