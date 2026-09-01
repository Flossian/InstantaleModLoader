# -*- coding: utf-8 -*-
"""戦闘 BGM の選曲画面。`state/musics/battle/playlist.json` を編集する。

    python runtime/mods/322_battle_bgm/tool.py           窓を開く
    python runtime/mods/322_battle_bgm/tool.py --dump    窓を開かず、いま読める一覧を標準出力に出す

`323_` の §4 が決めた契約で動く。
ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見てこのファイルを
サブプロセスで起動し、場所は環境変数で渡す。直接起動したときは自分で探す（`locate()`）。

| 変数 | 中身 |
|---|---|
| `IML_ROOT` | 配布フォルダの根 |
| `IML_STATE_DIR` | `state/` の場所 |
| `IML_GAME_DIR` | ゲーム本体のフォルダ |

画面は3つのタブ（通常戦闘 / ボス戦闘 / 闘技場戦闘）。
どのタブも左右2段で、左が置いてある曲の全部（曲名・置き場）、右がその戦闘で使う曲（曲名・置き場・重み・確率）。
左をダブルクリック（または「使う >>」）で右へ入り、右をダブルクリック（または「<< 外す」）で外れる。
右で選んだ曲の重みは下の欄で変える。
一覧の上の欄で列ごとに絞り込める（曲名は部分一致、置き場は選択、重みは下限）。
絞り込みは表示だけを変え、値は変えない（「全て使う / 全て外す」は表示中の曲だけに効く）。

保存は `playlist.json` への書き込み。
書き方はローダの `write_json`（隣に書いてから差し替える。TECH.md §3.11.1）。
ファイルにあって曲が無い行（曲を一時的に外しているとき）は消さずに残す。
MOD 本体（`battle_bgm.py`）は毎戦闘このファイルを読むので、ゲームを起動したまま保存しても次の戦闘から効く。

`mod.json` の `settings`（新しい曲に付ける重み・同じ曲の連続回避）もこの画面が引き受ける。
ローダの設定画面の「設定…」はこの MOD ではこの画面を開くので、宣言の設定ダイアログは出ない。
値の置き場は他の MOD と同じ `settings/mod_settings.json`（`instantale_modloader.config` 経由。既定と同じ値は書かない）。

MOD 本体は import しない。
ここはゲームの外で走る別プロセスで、本体はゲームの中で走る。
共有したい定数（フォルダ名・拡張子・種類）はこのファイルに写してある。
"""

import io
import json
import os
import sys
import time

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# battle_bgm.py と同じ値。
STATE_SUBDIR = ("musics", "battle")
ASSET_SUBDIR = ("Assets", "sounds", "musics", "battle")
EXTENSIONS = (".mp3", ".ogg", ".wav")
PLAYLIST_NAME = "playlist.json"
DEFAULT_WEIGHT = 100
PLAYLIST_HELP = [
    "戦闘 BGM の重み。曲名 → {normal: 通常戦闘, boss: ボス戦, colosseum: 闘技場}",
    "重みは比率。同じ種類の合計に対する割合が確率になる（合計 100 なら数字がそのままパーセント）",
    "0 はその種類では鳴らない。3種類とも 0 なら素の曲が鳴る",
    "曲は Assets/sounds/musics/battle と state/musics/battle の .mp3 / .ogg / .wav。同名なら state 側",
    "見つけた曲は 0 で書き足される。設定画面で使うに入れるまで鳴らない。書いた数字は消えない",
]

# タブの並び。鍵は playlist.json の項目名。
CATEGORIES = (
    ("normal", "通常戦闘", "依頼中の遭遇・会話からの戦闘・衛兵"),
    ("boss", "ボス戦闘", "依頼のラスボス戦"),
    ("colosseum", "闘技場戦闘", "闘技場の試合"),
)
WHERE_LABEL = {"assets": "ゲーム (Assets)", "state": "state"}
WHERE_ANY = "全て"

# mod.json の "settings" と同じ名前・同じ既定値（battle_bgm.py の定数と同じ）。
MOD_NAME = os.path.basename(MOD_DIR)
SETTING_DEFAULTS = {"DEFAULT_WEIGHT": DEFAULT_WEIGHT, "AVOID_REPEAT": True}


def _add_loader_path(root):
    """ローダ（`instantale_modloader`）を import できるようにする。

    置き場は `IML_ROOT/runtime`。無ければ自分の位置から（`runtime/mods/<この MOD>/` の2つ上）。
    後者があるのは、`IML_ROOT` が別の場所を指していても（検証・手元の実験）ローダは同梱のものを使えるようにするため。
    """
    for runtime in (os.path.join(root, "runtime") if root else "",
                    os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir))):
        if runtime and os.path.isdir(os.path.join(runtime, "instantale_modloader")) \
                and runtime not in sys.path:
            sys.path.insert(0, runtime)


def _config_module(root):
    _add_loader_path(root)
    from instantale_modloader import config
    return config


def load_settings(root):
    """この MOD に効いている設定。読めなければ既定。"""
    values = dict(SETTING_DEFAULTS)
    try:
        chosen = _config_module(root).load_store(os.path.join(root, "runtime")).get(MOD_NAME) or {}
    except Exception:
        chosen = {}
    if isinstance(chosen.get("DEFAULT_WEIGHT"), (int, float)) and not isinstance(chosen["DEFAULT_WEIGHT"], bool):
        values["DEFAULT_WEIGHT"] = max(0, int(chosen["DEFAULT_WEIGHT"]))
    if isinstance(chosen.get("AVOID_REPEAT"), bool):
        values["AVOID_REPEAT"] = chosen["AVOID_REPEAT"]
    return values


def save_settings(root, values):
    """既定と違う値だけを `mod_settings.json` に書く。他の MOD の項は触らない。"""
    try:
        config = _config_module(root)
        runtime = os.path.join(root, "runtime")
        store = config.load_store(runtime)
        changed = dict((k, v) for k, v in values.items() if v != SETTING_DEFAULTS.get(k))
        if changed:
            store[MOD_NAME] = changed
        else:
            store.pop(MOD_NAME, None)
        config.save_store(runtime, store)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------- 場所
def locate():
    """(root, state_dir, game_dir)。環境変数が無ければ自分で探す。"""
    root = os.environ.get("IML_ROOT") or os.path.normpath(
        os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir))
    state_dir = os.environ.get("IML_STATE_DIR") or os.path.join(root, "state")
    game_dir = os.environ.get("IML_GAME_DIR") or ""
    if not game_dir:
        try:
            with io.open(os.path.join(root, "settings", "gui.json"), encoding="utf-8") as fh:
                game_path = json.load(fh).get("game_path") or ""
            if game_path:
                game_dir = os.path.dirname(game_path)
        except (OSError, ValueError):
            game_dir = ""
    return root, state_dir, game_dir


def list_tracks(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names
                  if n.lower().endswith(EXTENSIONS)
                  and os.path.isfile(os.path.join(folder, n)))


def scan(asset_dir, state_dir):
    """[(曲名, 置き場)]。同名なら state 側。"""
    found = {}
    for folder, where in ((asset_dir, "assets"), (state_dir, "state")):
        if folder:
            for name in list_tracks(folder):
                found[name] = where
    return sorted(found.items())


def load_playlist(path):
    """ファイル全体。無い・読めないときは空の辞書。"""
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def weight_of(entry, category):
    try:
        value = float((entry or {}).get(category, 0))
    except (TypeError, ValueError):
        return 0
    return int(value) if value > 0 else 0


def matches(name, where, weight, name_filter, where_filter, min_weight):
    """絞り込み。曲名は部分一致（大文字小文字を見ない）、置き場は一致、重みは下限。"""
    if name_filter and name_filter.lower() not in name.lower():
        return False
    if where_filter and where_filter != WHERE_ANY and WHERE_LABEL.get(where, where) != where_filter:
        return False
    if min_weight and weight < min_weight:
        return False
    return True


def write_json(root, path, data, indent=1):
    """ローダの `write_json`（tmp → fsync → replace）で書く。無ければ同じ手順を自前で踏む。"""
    try:
        _add_loader_path(root)
        import instantale_modloader as ml
        return bool(ml.write_json(path, data, indent=indent))
    except Exception:
        pass
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _gui_config_path(root):
    return os.path.join(root, "settings", "gui.json")


def load_window(root):
    """前回の窓の大きさと位置。{"geometry": "WxH+X+Y", "maximized": bool}。無ければ空。"""
    try:
        with io.open(_gui_config_path(root), encoding="utf-8") as fh:
            cfg = json.load(fh)
        entry = (cfg.get("tool_window") or {}).get(MOD_NAME) or {}
        return entry if isinstance(entry, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def save_window(root, window):
    """窓の大きさと位置を `settings/gui.json` の `tool_window[MOD 名]` に残す。

    他の覚え書き（ゲームの場所・ローダの窓）を消さないよう、読んでから書く。
    残せなくても止めない（窓が使えないことと設定が残らないことは別）。
    """
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
        write_json(root, path, cfg, indent=2)     # gui.json は設定画面と同じ体裁
    except Exception:
        pass


class Model(object):
    """一覧の中身。{曲名: {"where": 置き場, 種類: 重み}}。"""

    def __init__(self, root=None, state_root=None, game_dir=None):
        found = locate()
        self.root = root or found[0]
        self.state_root = state_root or found[1]
        self.game_dir = found[2] if game_dir is None else game_dir
        self.state_dir = os.path.join(self.state_root, *STATE_SUBDIR)
        self.asset_dir = os.path.join(self.game_dir, *ASSET_SUBDIR) if self.game_dir else ""
        self.playlist_path = os.path.join(self.state_dir, PLAYLIST_NAME)
        self.rows = {}
        self.file = {}
        self.saved = {}
        self.settings = dict(SETTING_DEFAULTS)
        self.saved_settings = dict(SETTING_DEFAULTS)
        self.reload()

    @property
    def default_weight(self):
        return self.settings.get("DEFAULT_WEIGHT", DEFAULT_WEIGHT)

    def reload(self):
        self.settings = load_settings(self.root)
        self.saved_settings = dict(self.settings)
        self.file = load_playlist(self.playlist_path)
        tracks = self.file.get("tracks")
        if not isinstance(tracks, dict):
            tracks = {}
        self.rows = {}
        for name, where in scan(self.asset_dir, self.state_dir):
            entry = tracks.get(name)
            row = {"where": where}
            for key, _label, _note in CATEGORIES:
                # 見つけただけの曲は 0（左の一覧に出るだけ）。「使う」で初めて重みが付く。
                row[key] = weight_of(entry, key) if entry is not None else 0
            self.rows[name] = row
        self.saved = self.to_tracks()

    def to_tracks(self):
        tracks = {}
        for name in sorted(self.rows):
            row = self.rows[name]
            tracks[name] = dict((key, row[key]) for key, _l, _n in CATEGORIES)
        return tracks

    def dirty(self):
        return self.to_tracks() != self.saved or self.settings != self.saved_settings

    def to_json(self):
        """書き出す全体。ファイルにあって曲が無い行は残す。"""
        tracks = self.file.get("tracks")
        merged = dict(tracks) if isinstance(tracks, dict) else {}
        merged.update(self.to_tracks())
        # `_help` は毎回いまの文に置き換える（古い版の説明が残ると意味がずれる）。
        return {"_help": list(PLAYLIST_HELP), "tracks": dict(sorted(merged.items()))}

    def save(self):
        """playlist.json と mod_settings.json の両方。どちらかが書けなければ False。"""
        data = self.to_json()
        if not write_json(self.root, self.playlist_path, data):
            return False
        self.file = data
        self.saved = self.to_tracks()
        if self.settings != self.saved_settings:
            if not save_settings(self.root, self.settings):
                return False
            self.saved_settings = dict(self.settings)
        return True

    def shares(self, category):
        """{曲名: 確率(%)}。重みの合計に対する割合。"""
        total = sum(row[category] for row in self.rows.values())
        if total <= 0:
            return dict((name, 0.0) for name in self.rows)
        return dict((name, 100.0 * row[category] / total) for name, row in self.rows.items())


# ----------------------------------------------------------------- 画面
def build_window(model):
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("戦闘BGMの選曲")
    # 大きさと位置は前回のものを使う（`settings/gui.json` の `tool_window`。
    # ローダの設定画面が自分の窓を覚えるのと同じ場所）。無ければ既定。
    root.minsize(900, 620)
    remembered = load_window(model.root)
    root.geometry(remembered.get("geometry") or "1280x820")
    if remembered.get("maximized"):
        try:
            root.state("zoomed")
        except Exception:
            pass

    # 配色と書体は設定画面のものを借りる。無ければ素の Tk。
    try:
        sys.path.insert(0, os.path.join(model.root, "tools"))
        import gui as loader_gui
        loader_gui.setup_theme(root)
    except Exception:
        pass

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)

    # --- 見出しと置き場
    ttk.Label(outer, text="戦闘BGMの選曲", style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel",
              text="戦闘の種類ごとに、鳴らす曲と重みを決める。重みは比率で、"
                   "同じ種類の合計に対する割合が確率になる").pack(anchor="w", pady=(0, 8))

    places = ttk.Frame(outer)
    places.pack(fill="x")

    def place_row(label, path, ok):
        row = ttk.Frame(places)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=14, style="Group.TLabel").pack(side="left")
        ttk.Label(row, text=path or "（未設定）",
                  style="TLabel" if ok else "Warn.TLabel").pack(side="left", fill="x", expand=True)

        def open_folder():
            if path and os.path.isdir(path):
                os.startfile(path)
        ttk.Button(row, text="開く", command=open_folder, width=6).pack(side="right")

    place_row("ゲームの曲", model.asset_dir, os.path.isdir(model.asset_dir))
    place_row("足した曲", model.state_dir, os.path.isdir(model.state_dir))
    place_row("設定ファイル", model.playlist_path, os.path.isfile(model.playlist_path))

    # --- MOD の設定（mod.json の "settings"）。
    # ローダの設定画面の「設定…」はこの MOD ではこの窓を開くので、ここで引き受ける。
    ttk.Separator(outer).pack(fill="x", pady=8)
    opts = ttk.Frame(outer)
    opts.pack(fill="x")
    ttk.Label(opts, text="「使う」に入れたときの重み", style="Group.TLabel").pack(side="left")
    default_var = tk.StringVar(value=str(model.default_weight))
    ttk.Spinbox(opts, from_=0, to=1000, width=6, textvariable=default_var).pack(
        side="left", padx=(6, 4))
    ttk.Label(opts, text="（置いただけの曲は左の一覧に出るだけで鳴らない。右へ入れた曲がこの重みで始まる）",
              style="Faint.TLabel").pack(side="left", padx=(0, 18))
    avoid_var = tk.BooleanVar(value=bool(model.settings.get("AVOID_REPEAT", True)))
    ttk.Checkbutton(opts, text="前回と同じ曲を続けて選ばない", variable=avoid_var).pack(side="left")

    def on_default(*_a):
        try:
            model.settings["DEFAULT_WEIGHT"] = max(0, int(float(default_var.get() or 0)))
        except ValueError:
            return
        update_title()

    def on_avoid(*_a):
        model.settings["AVOID_REPEAT"] = bool(avoid_var.get())
        update_title()

    default_var.trace_add("write", on_default)
    avoid_var.trace_add("write", on_avoid)

    ttk.Separator(outer).pack(fill="x", pady=8)

    # --- 下段のボタン。
    # タブより先に詰める（窓が低いときにタブに押し出されないように）。
    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    # --- タブ
    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)

    tabs = {}
    where_choices = [WHERE_ANY] + [WHERE_LABEL[k] for k in ("assets", "state")]

    def update_title():
        root.title("戦闘BGMの選曲" + ("（未保存）" if model.dirty() else ""))

    def make_tab(category, label, note):
        frame = ttk.Frame(notebook, padding=(8, 8))
        notebook.add(frame, text="  {}  ".format(label))
        ttk.Label(frame, text=note, style="Sub.TLabel").pack(anchor="w", pady=(0, 6))

        # 下段: 選んだ曲の重み・入り切り・まとめて入り切り。
        # 一覧より先に詰めておく（後だと窓が低いときに一覧に押し出される）。
        bottom = ttk.Frame(frame)
        bottom.pack(side="bottom", fill="x", pady=(8, 0))

        # 左が置いてある曲の全部、右がこの戦闘で使う曲。
        panes = ttk.PanedWindow(frame, orient="horizontal")
        panes.pack(fill="both", expand=True)

        def make_list(parent, columns, headings, widths, with_weight):
            box = ttk.Frame(parent)
            # 絞り込みの欄。列ごとに1つ。
            bar = ttk.Frame(box)
            bar.pack(fill="x", pady=(0, 4))
            ttk.Label(bar, text="曲名", style="Group.TLabel").pack(side="left")
            name_var = tk.StringVar()
            ttk.Entry(bar, textvariable=name_var, width=18).pack(side="left", padx=(4, 10))
            ttk.Label(bar, text="置き場", style="Group.TLabel").pack(side="left")
            where_var = tk.StringVar(value=WHERE_ANY)
            ttk.Combobox(bar, textvariable=where_var, values=where_choices,
                         state="readonly", width=13).pack(side="left", padx=(4, 10))
            min_var = tk.StringVar()
            if with_weight:
                ttk.Label(bar, text="重み ≧", style="Group.TLabel").pack(side="left")
                ttk.Spinbox(bar, from_=0, to=1000, width=5, textvariable=min_var).pack(
                    side="left", padx=(4, 10))
            shown = ttk.Label(bar, style="Faint.TLabel")
            shown.pack(side="right")

            tree = ttk.Treeview(box, columns=columns, show="headings", selectmode="browse")
            for col, head, (width, anchor, stretch) in zip(columns, headings, widths):
                tree.heading(col, text=head)
                tree.column(col, width=width, anchor=anchor, stretch=stretch)
            scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set, height=8)
            scroll.pack(side="right", fill="y")
            tree.pack(side="left", fill="both", expand=True)
            for var in (name_var, where_var, min_var):
                var.trace_add("write", lambda *_a: refresh(category))
            return box, {"tree": tree, "name": name_var, "where": where_var,
                         "min": min_var, "shown": shown}

        pool_box, pool = make_list(
            panes, ("name", "where"), ("曲名", "置き場"),
            ((240, "w", True), (130, "w", False)), False)
        used_box, used = make_list(
            panes, ("name", "where", "weight", "share"), ("曲名", "置き場", "重み", "確率"),
            ((220, "w", True), (130, "w", False), (64, "e", False), (64, "e", False)), True)
        panes.add(pool_box, weight=1)
        panes.add(used_box, weight=1)

        # 左の一覧の操作は左に、右の一覧の操作は右に寄せる。
        ttk.Button(bottom, text="全て使う",
                   command=lambda: set_all(category, None)).pack(side="left")
        ttk.Button(bottom, text="使う >>", width=8,
                   command=lambda: move(category, pool["tree"].focus(), True)).pack(
                       side="left", padx=(6, 12))
        ttk.Button(bottom, text="全て外す", command=lambda: set_all(category, 0)).pack(side="right")
        ttk.Button(bottom, text="<< 外す", width=8,
                   command=lambda: move(category, used["tree"].focus(), False)).pack(
                       side="right", padx=(6, 6))
        weight_var = tk.StringVar()
        spin = ttk.Spinbox(bottom, from_=0, to=1000, width=6, textvariable=weight_var)
        spin.pack(side="right", padx=(6, 12))
        ttk.Label(bottom, text="選んだ曲の重み").pack(side="right")
        summary = ttk.Label(bottom, style="Faint.TLabel")
        summary.pack(side="left", fill="x", expand=True)

        tabs[category] = {"pool": pool, "used": used, "weight_var": weight_var,
                          "spin": spin, "summary": summary, "remember": {},
                          "quiet": False}

        def on_select(_event):
            item = used["tree"].focus()
            if item and item in model.rows:
                tab = tabs[category]
                tab["quiet"] = True
                weight_var.set(str(model.rows[item][category]))
                tab["quiet"] = False

        def on_weight(*_args):
            if tabs[category]["quiet"]:
                return
            item = used["tree"].focus()
            if not item or item not in model.rows:
                return
            try:
                value = max(0, int(float(weight_var.get() or 0)))
            except ValueError:
                return
            if model.rows[item][category] != value:
                model.rows[item][category] = value
                refresh(category, keep=item)

        pool["tree"].bind("<Double-1>",
                          lambda e: move(category, pool["tree"].identify_row(e.y), True))
        used["tree"].bind("<Double-1>",
                          lambda e: move(category, used["tree"].identify_row(e.y), False))
        used["tree"].bind("<<TreeviewSelect>>", on_select)
        weight_var.trace_add("write", on_weight)
        spin.bind("<Return>", on_weight)

    def move(category, name, use):
        """右へ入れる（use=True）／右から外す。外した重みは覚えておき、戻すと元に戻る。"""
        if not name or name not in model.rows:
            return
        tab = tabs[category]
        row = model.rows[name]
        if use:
            if row[category] <= 0:
                row[category] = tab["remember"].get(name) or model.default_weight or DEFAULT_WEIGHT
        elif row[category] > 0:
            tab["remember"][name] = row[category]
            row[category] = 0
        refresh(category, keep=name if use else None)

    def filter_of(side):
        try:
            min_weight = int(float(side["min"].get() or 0))
        except ValueError:
            min_weight = 0
        return side["name"].get().strip(), side["where"].get(), min_weight

    def visible(category, side):
        """その一覧の絞り込みに掛かる曲名。"""
        name_f, where_f, min_w = filter_of(side)
        return [name for name in sorted(model.rows)
                if matches(name, model.rows[name]["where"], model.rows[name][category],
                           name_f, where_f, min_w)]

    def set_all(category, value):
        """表示中の曲だけに効く（絞り込んでから押せば、その範囲だけ入り切りできる）。"""
        tab = tabs[category]
        names = visible(category, tab["pool"] if value is None else tab["used"])
        for name in names:
            row = model.rows[name]
            if value == 0:
                if row[category] > 0:
                    tab["remember"][name] = row[category]
                row[category] = 0
            elif row[category] <= 0:
                row[category] = tab["remember"].get(name) or model.default_weight or DEFAULT_WEIGHT
        refresh(category)

    def refresh(category, keep=None):
        tab = tabs[category]
        pool, used = tab["pool"], tab["used"]
        shares = model.shares(category)
        pool["tree"].delete(*pool["tree"].get_children())
        used["tree"].delete(*used["tree"].get_children())
        pool_names = visible(category, pool)
        used_all = [n for n in sorted(model.rows) if model.rows[n][category] > 0]
        used_names = [n for n in visible(category, used) if model.rows[n][category] > 0]
        for name in pool_names:
            row = model.rows[name]
            pool["tree"].insert("", "end", iid=name,
                                values=(name, WHERE_LABEL.get(row["where"], row["where"])),
                                tags=("on" if row[category] > 0 else "off",))
        for name in used_names:
            row = model.rows[name]
            used["tree"].insert("", "end", iid=name, values=(
                name, WHERE_LABEL.get(row["where"], row["where"]),
                row[category], "{:.0f}%".format(shares[name])))
        # 右に入っている曲は左では薄く出す（同じ曲が2度見えても迷わないように）。
        pool["tree"].tag_configure("on", foreground="#646b76")
        pool["shown"].configure(text="{} / {} 曲".format(len(pool_names), len(model.rows)))
        used["shown"].configure(text="{} / {} 曲".format(len(used_names), len(used_all)))
        total = sum(r[category] for r in model.rows.values())
        if not model.rows:
            text = "曲が見つからない。上の2つのフォルダに .mp3 / .ogg / .wav を置く"
        elif not used_all:
            text = "使う曲が無い。この種類の戦闘では素の曲が鳴る"
        else:
            text = "{} 曲中 {} 曲を使う。重みの合計 {}".format(len(model.rows), len(used_all), total)
        tab["summary"].configure(text=text)
        if keep and used["tree"].exists(keep):
            used["tree"].selection_set(keep)
            used["tree"].focus(keep)
        else:
            tab["quiet"] = True
            tab["weight_var"].set("")
            tab["quiet"] = False
        update_title()

    for key, label, note in CATEGORIES:
        make_tab(key, label, note)
        refresh(key)

    status = ttk.Label(footer, style="Faint.TLabel",
                       text="保存すると次の戦闘から効く（ゲームを起動したままでよい）")
    status.pack(side="left")

    def rescan():
        if model.dirty() and not messagebox.askyesno(
                "再走査", "未保存の変更があります。破棄して読み直しますか？", parent=root):
            return
        model.reload()
        default_var.set(str(model.default_weight))
        avoid_var.set(bool(model.settings.get("AVOID_REPEAT", True)))
        for key, _l, _n in CATEGORIES:
            refresh(key)
        status.configure(text="読み直しました（{} 曲）".format(len(model.rows)))

    def save():
        if model.save():
            for key, _l, _n in CATEGORIES:
                refresh(key)
            status.configure(text="保存しました {}  {}".format(
                time.strftime("%H:%M:%S"), model.playlist_path))
        else:
            messagebox.showerror("保存に失敗しました",
                                 "{} に書けませんでした。".format(model.playlist_path), parent=root)

    def close():
        save_window(model.root, root)
        if model.dirty():
            answer = messagebox.askyesnocancel(
                "未保存の変更", "変更を保存してから閉じますか？", parent=root)
            if answer is None:
                return
            if answer and not model.save():
                return
        root.destroy()

    ttk.Button(footer, text="閉じる", command=close).pack(side="right")
    ttk.Button(footer, text="保存", style="Accent.TButton",
               command=save).pack(side="right", padx=(0, 6))
    ttk.Button(footer, text="再走査", command=rescan).pack(side="right", padx=(0, 6))
    root.protocol("WM_DELETE_WINDOW", close)

    if not model.rows:
        messagebox.showinfo("戦闘BGMの選曲",
                            "曲が1つも見つからない。\n\n{}\n{}\n\nに .mp3 / .ogg / .wav を置いて「再走査」".format(
                                model.asset_dir or "（ゲームの場所が未設定）", model.state_dir),
                            parent=root)
    return root


def dump(model):
    print("assets  : {}".format(model.asset_dir))
    print("state   : {}".format(model.state_dir))
    print("playlist: {} ({})".format(model.playlist_path,
                                      "exists" if os.path.isfile(model.playlist_path) else "missing"))
    print()
    head = "{:<44} {:<8}".format("track", "where") + "".join(
        " {:>10}".format(label) for _k, label, _n in CATEGORIES)
    print(head)
    for name in sorted(model.rows):
        row = model.rows[name]
        print("{:<44} {:<8}".format(name[:44], row["where"]) + "".join(
            " {:>10}".format(row[key]) for key, _l, _n in CATEGORIES))
    for key, label, _n in CATEGORIES:
        shares = model.shares(key)
        print("{}: {}".format(label, ", ".join(
            "{} {:.0f}%".format(n, s) for n, s in sorted(shares.items()) if s > 0) or "(素の曲)"))


def main(argv):
    model = Model()
    if "--dump" in argv:
        dump(model)
        return 0
    build_window(model).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
