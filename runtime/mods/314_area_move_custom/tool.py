# -*- coding: utf-8 -*-
"""ワールド別設定画面（`mod.json` の "tool"）。

ローダの設定画面（`tools/gui.py`）が「設定…」でこのファイルを別プロセスで開く。
`mod.json` の "settings" もこの画面が引き受ける（`323_` §4 の契約）ので、2段になっている。

    一括設定          全ワールド共通。`settings/mod_settings.json`
                      （`instantale_modloader.config` 経由。既定と同じ値は書かない）
    ワールド個別設定  `state/<この MOD の控え>/<世界>.json`。
                      一括設定と違う項目だけ書く。全部同じならファイルを消す

項目の一覧・型・既定値・上下限・選択肢は `mod.json` の "settings" から読む。
写しを持たないので、本体の定数とずれる場所が増えない
（既定値の突き合わせは今までどおり `tools/check_mods.py` が本体と `mod.json` の間で見る）。

世界の一覧はセーブ（`saves/<フォルダ>/savedata.json`）を復号して `world_data` の名前を取る。
本体の `state.world_key` と同じ見方なので、ゲーム中に読むファイル名と一致する
（フォルダ名は使わない。`324_` の tool.py と同じ）。

このファイルは `130_currency_unit` と `314_area_move_custom` で同じもの
（`tools/tests/test_world_settings_tool.py` が同一であることを確かめる）。
MOD どうしは import しない決まり（TECH.md §3.2.3）なので、共有するならローダ側へ移す。
"""

import io
import json
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
MOD_NAME = os.path.basename(MOD_DIR)                 # mod_settings.json の項の名前
STATE_DIRNAME = MOD_NAME.split("_", 1)[1]            # 控えのフォルダ名は番号無し（本体の定数と同じ）

# セーブの置き場と復号（`324_` の tool.py と同じ）。
DATA_VENDOR = ("Darmabeko", "Instantale")
SAVE_KEY = b"Instantale_Save_Key_2026"
WORLD_NAME_KEYS = ("world_name", "name", "title")    # `state.world_key` と同じ順


# ----------------------------------------------------------------- 場所
def locate():
    """`(root, runtime, state_dir)`。環境変数が無ければ自分の位置から組む。"""
    root = os.environ.get("IML_ROOT") or os.path.normpath(
        os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir))
    state_dir = os.environ.get("IML_STATE_DIR") or os.path.join(root, "state")
    return root, os.path.join(root, "runtime"), state_dir


def loader(runtime):
    """`instantale_modloader` の `(config, state, write_json)`。

    `runtime` に無ければ自分の位置（`runtime/mods/<この MOD>/` の2つ上）から引く。
    """
    for candidate in (runtime, os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir))):
        if os.path.isdir(os.path.join(candidate, "instantale_modloader")) \
                and candidate not in sys.path:
            sys.path.insert(0, candidate)
    from instantale_modloader import config, state, write_json
    return config, state, write_json


def read_decls(config):
    """`mod.json` の "settings" を宣言の形（`config.normalize_decls`）で。"""
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        return config.normalize_decls(json.load(fh).get("settings"))


# ----------------------------------------------------------------- 一括設定
def load_shared(config, runtime, decls):
    """この MOD に効いている一括設定。読めなければ既定。"""
    try:
        chosen = config.load_store(runtime).get(MOD_NAME) or {}
    except Exception:
        chosen = {}
    return config.resolve(decls, chosen)


def save_shared(config, runtime, decls, values):
    """既定と違う値だけを `mod_settings.json` に書く。他の MOD の項は触らない。"""
    store = config.load_store(runtime)
    changed = {k: v for k, v in values.items() if v != decls[k]["default"]}
    if changed:
        store[MOD_NAME] = changed
    else:
        store.pop(MOD_NAME, None)
    config.save_store(runtime, store)


# ----------------------------------------------------------------- 世界
def data_dir():
    env = os.environ.get("IML_INSTANTALE_DATA")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, *DATA_VENDOR)


def decode(raw):
    """セーブのバイト列を辞書にする。読めなければ None。素の JSON → XOR の順。"""
    for candidate in (raw, None):
        if candidate is None:
            candidate = bytes(b ^ SAVE_KEY[i % len(SAVE_KEY)] for i, b in enumerate(raw))
        try:
            data = json.loads(candidate.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def world_name_of(save, fallback):
    """セーブの世界名（`state.world_key` と同じ見方）。読めなければ `fallback`。"""
    data = save.get("world_data") if isinstance(save, dict) else None
    if isinstance(data, dict):
        for key in WORLD_NAME_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


def list_worlds(base=""):
    """`[(世界名, フォルダ名), ...]`。世界名の順。読めないセーブはフォルダ名を世界名にする。"""
    saves = os.path.join(base or data_dir(), "saves")
    try:
        folders = os.listdir(saves)
    except OSError:
        return []
    worlds = []
    for folder in folders:
        path = os.path.join(saves, folder, "savedata.json")
        if not os.path.isfile(path):
            continue
        try:
            with io.open(path, "rb") as fh:
                save = decode(fh.read())
        except OSError:
            save = None
        worlds.append((world_name_of(save, folder), folder))
    return sorted(worlds, key=lambda t: t[0].casefold())


# ----------------------------------------------------------------- ワールド個別設定
def world_path(state, state_dir, world):
    return os.path.join(state_dir, STATE_DIRNAME, state.world_filename(world, ".json"))


def load_world(decls, path, base):
    """その世界の控えを一括設定に重ねた値。無ければ一括設定のまま。"""
    values = dict(base)
    try:
        with io.open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return values
    if isinstance(record, dict):
        # 本体（`refresh_world`）と同じく型で見る。`coerce` は入力欄の文字列用で、
        # ここで使うと手で書いた `[1, 2]` が文字列として通ってしまう。
        for key, decl in decls.items():
            value = record.get(key)
            if value is not None and type(value) is type(decl["default"]):
                values[key] = value
    return values


def save_world(write_json, path, decls, base, values):
    """一括設定と違う項目だけを書く。全部同じならファイルを消す。書けなければ False。"""
    record = {k: v for k, v in values.items() if k in decls and v != base.get(k)}
    if not record:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return bool(write_json(path, record, indent=2))


def coerce_all(config, decls, raw):
    """入力欄の値を宣言に照らして整える。`(値, 最初の不備)`。不備が無ければ第2要素は空。"""
    values = {}
    for key, decl in decls.items():
        ok, value, why = config.coerce(decl, raw.get(key))
        if not ok:
            return None, "{}: {}".format(decl["label"]["ja"], why)
        values[key] = value
    return values, ""


# ----------------------------------------------------------------- 窓の記憶
def _gui_config_path(root):
    return os.path.join(root, "settings", "gui.json")


def load_window(root):
    """前回の窓の大きさと位置（`settings/gui.json` の `tool_window[MOD 名]`）。無ければ `{}`。"""
    try:
        with io.open(_gui_config_path(root), encoding="utf-8") as fh:
            cfg = json.load(fh)
        entry = (cfg.get("tool_window") or {}).get(MOD_NAME) or {}
        return entry if isinstance(entry, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def save_window(root, write_json, window):
    """窓の大きさと位置を `settings/gui.json` の `tool_window[MOD 名]` に残す（`324_` と同じ）。"""
    try:
        maximized = window.state() == "zoomed"
        if maximized:
            window.state("normal")
            window.update_idletasks()
        geometry = window.geometry()
        path = _gui_config_path(root)
        try:
            with io.open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        windows = cfg.get("tool_window")
        if not isinstance(windows, dict):
            windows = {}
        windows[MOD_NAME] = {"geometry": geometry, "maximized": maximized}
        cfg["tool_window"] = windows
        write_json(path, cfg, indent=2)     # gui.json は設定画面と同じ体裁
    except Exception:
        pass


# ----------------------------------------------------------------- 画面
def shown(value):
    """説明に添える値の見せ方。空は（空）、真偽は ON / OFF、他はそのまま。"""
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return "（空）" if value == "" else str(value)


def scrollable(parent):
    """縦にスクロールする枠。項目が窓に収まらない MOD（`314_` は 16 項目）のため。

    Canvas の中に Frame を置き、幅は Canvas に合わせ、高さは中身に任せる。
    返すのは中身を置く Frame。

    **中身が収まっているときは動かさない。**
    scrollregion を中身の高さのまま渡すと、車輪で枠の外まで動いてしまい、
    上に空きが出たまま戻らない（`130_` は4項目しかないので必ずこうなる）。
    領域の下端を「中身と枠の高いほう」にすると、収まっている間は
    つまみが溝を埋めて動く先が無くなる。
    """
    canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0,
                       background=ttk.Style().lookup("TFrame", "background") or None)
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)

    def overflow():
        """はみ出している高さ（収まっていれば 0）。"""
        return max(0, inner.winfo_reqheight() - canvas.winfo_height())

    def fit(event=None):
        if event is not None and event.widget is canvas:
            canvas.itemconfigure(window, width=event.width)
        canvas.configure(scrollregion=(
            0, 0, canvas.winfo_width(),
            max(inner.winfo_reqheight(), canvas.winfo_height())))
        if not overflow():
            canvas.yview_moveto(0)      # 窓を広げて収まったときに空きを残さない

    inner.bind("<Configure>", fit)
    canvas.bind("<Configure>", fit)

    # 車輪は枠の上に居るときだけ受ける（タブが2つあるので、全体に束ねると裏の枠まで動く）。
    def wheel(event):
        if overflow():
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
    canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    return inner


class Form(object):
    """宣言から作る入力欄の一組。bool は Checkbutton、choice は Combobox、他は Entry。

    `note_of(key)` は項目の説明の後ろに足す一言（既定や一括設定の値）。
    """

    def __init__(self, parent, decls, note_of):
        self.decls = decls
        self.vars = {}
        parent.columnconfigure(1, weight=1)
        row = 0
        for key, decl in decls.items():
            ttk.Label(parent, text=decl["label"]["ja"]).grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=(6, 0))
            if decl["type"] == "bool":
                var = tk.BooleanVar()
                ttk.Checkbutton(parent, variable=var).grid(row=row, column=1, sticky="w", pady=(6, 0))
            elif decl["type"] == "choice":
                var = tk.StringVar()
                ttk.Combobox(parent, textvariable=var, values=decl["values"],
                             state="readonly").grid(row=row, column=1, sticky="ew", pady=(6, 0))
            else:
                var = tk.StringVar()
                ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=(6, 0))
            self.vars[key] = var
            note = " ".join(t for t in (decl["note"]["ja"], note_of(key)) if t)
            if note:
                ttk.Label(parent, text=note, style="Faint.TLabel", wraplength=640,
                          justify="left").grid(row=row + 1, column=1, sticky="w", padx=(0, 8))
            row += 2

    def get(self):
        return {key: var.get() for key, var in self.vars.items()}

    def set(self, values):
        for key, var in self.vars.items():
            var.set(values[key] if isinstance(var, tk.BooleanVar) else str(values[key]))


def build_window(title, blurb):
    """窓を組む。`title` は見出し、`blurb` はその下の一言（MOD ごとに違うのはこの2つだけ）。"""
    root_dir, runtime, state_dir = locate()
    config, state, write_json = loader(runtime)
    decls = read_decls(config)
    shared = load_shared(config, runtime, decls)
    worlds = list_worlds()

    root = tk.Tk()
    root.title(title)
    root.minsize(760, 520)
    remembered = load_window(root_dir)
    root.geometry(remembered.get("geometry") or "900x640")
    if remembered.get("maximized"):
        try:
            root.state("zoomed")
        except Exception:
            pass

    # 配色と書体は設定画面のものを借りる。無ければ素の Tk。
    try:
        sys.path.insert(0, os.path.join(root_dir, "tools"))
        import gui as loader_gui
        loader_gui.setup_theme(root)
    except Exception:
        pass

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)

    # --- 見出し
    ttk.Label(outer, text=title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel", wraplength=860, justify="left",
              text=blurb + " 設定は2段。一括設定はどの世界でも効き、"
                   "ワールド個別設定はその世界で一括設定と違う項目だけを持つ").pack(anchor="w", pady=(0, 6))

    # --- 下段のボタン。一覧より先に詰める（窓が低いときに押し出されないように）。
    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)

    # ---- 一括設定
    bulk = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(bulk, text="  一括設定  ")
    ttk.Label(bulk, text="どの世界でも効く。settings\\mod_settings.json に入る（他の MOD と同じ）",
              style="Sub.TLabel").pack(anchor="w", pady=(0, 4))
    bulk_bottom = ttk.Frame(bulk)
    bulk_bottom.pack(side="bottom", fill="x", pady=(6, 0))
    body = ttk.Frame(bulk, padding=(0, 0, 6, 0))    # 入力欄は grid、外は pack。混ぜないための子枠
    body.pack(fill="both", expand=True)
    shared_form = Form(scrollable(body), decls, lambda k: "既定: " + shown(decls[k]["default"]))
    shared_form.set(shared)
    ttk.Button(bulk_bottom, text="既定に戻す", command=lambda: shared_form.set(
        {k: d["default"] for k, d in decls.items()})).pack(side="right")

    # ---- ワールド個別設定
    per_world = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(per_world, text="  ワールド個別設定  ")
    ttk.Label(per_world, text="その世界だけ。一括設定と違う項目だけが state\\{}\\<世界名>.json に入る。"
              "セーブは世界名を読むだけで書かない".format(STATE_DIRNAME),
              style="Sub.TLabel").pack(anchor="w", pady=(0, 4))
    picker = ttk.Frame(per_world)
    picker.pack(fill="x", pady=(0, 4))
    ttk.Label(picker, text="世界", style="Group.TLabel").pack(side="left", padx=(0, 8))
    names = [name for name, _folder in worlds]
    world_var = tk.StringVar(value=names[0] if names else "")
    world_box = ttk.Combobox(picker, textvariable=world_var, values=names, state="readonly")
    world_box.pack(side="left", fill="x", expand=True)
    world_bottom = ttk.Frame(per_world)
    world_bottom.pack(side="bottom", fill="x", pady=(6, 0))
    world_status = ttk.Label(world_bottom, style="Faint.TLabel")
    world_status.pack(side="left", fill="x", expand=True)
    world_status.configure(text="{} 世界（{}）".format(len(worlds), os.path.join(data_dir(), "saves"))
                           if worlds else "世界が見つからない: " + os.path.join(data_dir(), "saves"))
    body = ttk.Frame(per_world, padding=(0, 0, 6, 0))
    body.pack(fill="both", expand=True)
    world_form = Form(scrollable(body), decls, lambda k: "一括設定: " + shown(shared[k]))
    ttk.Button(world_bottom, text="一括設定に戻す",
               command=lambda: world_form.set(shared)).pack(side="right")

    saved = {"shared": dict(shared), "world": dict(shared), "name": world_var.get()}

    def path_of(name):
        return world_path(state, state_dir, name) if name else ""

    def load_world_into_form(name):
        values = load_world(decls, path_of(name), shared) if name else dict(shared)
        world_form.set(values)
        saved["world"] = dict(values)
        saved["name"] = name

    def dirty():
        return shared_form.get() != {k: str(v) if not isinstance(v, bool) else v
                                     for k, v in saved["shared"].items()} \
            or world_form.get() != {k: str(v) if not isinstance(v, bool) else v
                                    for k, v in saved["world"].items()}

    def on_world(_event=None):
        name = world_var.get()
        if name == saved["name"]:
            return
        if world_form.get() != {k: str(v) if not isinstance(v, bool) else v
                                for k, v in saved["world"].items()} \
                and not messagebox.askyesno("世界の切替", "この世界の未保存の変更を破棄しますか？", parent=root):
            world_var.set(saved["name"])
            return
        load_world_into_form(name)

    world_box.bind("<<ComboboxSelected>>", on_world)
    load_world_into_form(world_var.get())

    status = ttk.Label(footer, style="Faint.TLabel",
                       text="一括設定は次の注入から、ワールド個別設定は次にその世界を見たときから効く")
    status.pack(side="left")

    def save():
        """一括設定と、選んでいる世界の個別設定を両方書く。どちらかが不備なら何も書かない。"""
        nonlocal shared
        new_shared, bad = coerce_all(config, decls, shared_form.get())
        if bad:
            messagebox.showerror("一括設定を確かめてください", bad, parent=root)
            return False
        new_world, bad = coerce_all(config, decls, world_form.get())
        if bad:
            messagebox.showerror("ワールド個別設定を確かめてください", bad, parent=root)
            return False
        try:
            save_shared(config, runtime, decls, new_shared)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", "{}\n{}: {}".format(
                config.store_path(runtime), type(exc).__name__, exc), parent=root)
            return False
        shared = new_shared
        saved["shared"] = dict(new_shared)
        name = world_var.get()
        if name and not save_world(write_json, path_of(name), decls, shared, new_world):
            messagebox.showerror("保存に失敗しました", path_of(name), parent=root)
            return False
        saved["world"] = dict(new_world) if name else dict(shared)
        status.configure(text="保存しました {}  {}".format(
            time.strftime("%H:%M:%S"), path_of(name) if name else config.store_path(runtime)))
        return True

    def close():
        save_window(root_dir, write_json, root)
        if dirty():
            answer = messagebox.askyesnocancel("未保存の変更", "変更を保存してから閉じますか？", parent=root)
            if answer is None:
                return
            if answer and not save():
                return
        root.destroy()

    ttk.Button(footer, text="閉じる", command=close).pack(side="right")
    ttk.Button(footer, text="保存", style="Accent.TButton", command=save).pack(side="right", padx=(0, 6))
    root.protocol("WM_DELETE_WINDOW", close)
    return root


def main():
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    build_window(manifest["name"]["ja"], manifest["description"]["ja"]).mainloop()


if __name__ == "__main__":
    main()
