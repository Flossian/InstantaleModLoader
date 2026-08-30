# -*- coding: utf-8 -*-
r"""NPC のエクスポートとインポートの画面。

    python runtime/mods/908_npc_carryover/tool.py           窓を開く
    python runtime/mods/908_npc_carryover/tool.py --dump    窓を開かず、いま読めるものを標準出力に出す

DOC.md §4 が決めた契約で動く（`322_battle_bgm` の道具と同じ）。
ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見てこのファイルを
サブプロセスで起動し、場所は環境変数で渡す。直接起動したときは自分で探す（`locate()`）。

##### 画面は2つ

| タブ | すること |
|---|---|
| エクスポート | 別の世界のセーブを読んで NPC を一覧し、チェックした全員を zip にする |
| インポート | 書き出した zip を選び、どの世界へ持ち込むかを予約する |

どちらの一覧も**列ごとのフィルター**を持つ。
1世界の NPC は 100体を超える（実セーブで 103体）ので、
名前で探すだけでは足りない。

> フィルターの欄は一覧の**上**に置く。
> 見出しの真下に欄を差し込む形は `ttk.Treeview` には無い
> （`322_` の選曲画面も同じ理由で上に置いている）。

##### 名前の重複はここで見る

置き先は世界名で選ぶので、**予約するその場**で置き先の `savedata.json` を読んで
名前を突き合わせられる。ゲームを起動する前に分かれば手戻りが無い。
画面を開き直したときも予約を全部見直す
（予約してから置き先の世界を遊ぶと、その間にゲームや `320_` が新しい NPC を作る）。

##### セーブは読むだけ

このファイルが書くのは `state/npc_carryover/` の中だけ
（zip と `pending.json`）。
`savedata.json` にも `worlds/` にも書かない。

MOD 本体（`npc_carryover.py`）は import しない。
ここはゲームの外で走る別プロセスで、本体はゲームの中で走る。
両方が要るもの（セーブの復号・zip の形・予約の形）は `carryover.py` にある。
"""

import base64
import io
import json
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
MOD_NAME = os.path.basename(MOD_DIR)

# 自分の隣（carryover.py）を import できるようにする。
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)

#: `mod.json` の "settings" と同じ名前・同じ既定値（`npc_carryover.py` の定数と同じ）。
SETTING_DEFAULTS = {"INHERIT_MEMORY": True, "INHERIT_RELATIONSHIP": True,
                    "INHERIT_LIFE_LOG": True, "ANNOUNCE": True}

#: 引き継ぎの選択（予約に書く鍵 → 画面の文言 → 設定の名前）。
INHERIT_ROWS = (
    ("memory", "記憶（人物像・あなたについての記録・社会関係）", "INHERIT_MEMORY"),
    ("relationship", "好感度・関係", "INHERIT_RELATIONSHIP"),
    ("life_log", "経歴", "INHERIT_LIFE_LOG"),
)

ANY = "すべて"

#: プレビューに出す顔画像の一辺（px）。
#: 実物の `face_image.png` が 165×165 なので、そのまま収まる大きさにしてある
#: （Tk の `PhotoImage` は整数分の1にしか縮められない。165 を 132 の枠に
#: 入れようとすると `subsample(2)` で 82px まで落ちる）。
FACE_BOX = 165

#: 顔画像が無い NPC の枠に出す文字。
FACE_EMPTY = "顔画像なし"


def _add_loader_path(root):
    """ローダ（`instantale_modloader`）を import できるようにする。

    置き場は `IML_ROOT/runtime`。無ければ自分の位置から（2つ上）。
    `322_` の道具と同じ探し方。
    """
    for runtime in (os.path.join(root, "runtime") if root else "",
                    os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir))):
        if runtime and os.path.isdir(os.path.join(runtime, "instantale_modloader")) \
                and runtime not in sys.path:
            sys.path.insert(0, runtime)


def locate():
    """(root, state_dir, game_dir)。環境変数が無ければ自分で探す。"""
    root = os.environ.get("IML_ROOT") or os.path.normpath(
        os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir))
    state_dir = os.environ.get("IML_STATE_DIR") or os.path.join(root, "state")
    game_dir = os.environ.get("IML_GAME_DIR") or ""
    return root, state_dir, game_dir


def load_settings(root):
    """この MOD に効いている設定。読めなければ既定。"""
    values = dict(SETTING_DEFAULTS)
    try:
        _add_loader_path(root)
        from instantale_modloader import config
        chosen = config.load_store(os.path.join(root, "runtime")).get(MOD_NAME) or {}
    except Exception:
        chosen = {}
    for key in values:
        if isinstance(chosen.get(key), bool):
            values[key] = chosen[key]
    return values


def save_settings(root, values):
    """既定と違う値だけを `mod_settings.json` に書く。他の MOD の項は触らない。"""
    try:
        _add_loader_path(root)
        from instantale_modloader import config
        runtime = os.path.join(root, "runtime")
        store = config.load_store(runtime)
        chosen = {key: value for key, value in values.items()
                  if SETTING_DEFAULTS.get(key) != value}
        if chosen:
            store[MOD_NAME] = chosen
        else:
            store.pop(MOD_NAME, None)
        config.save_store(runtime, store)
        return True
    except Exception:
        return False


# ---- 窓の大きさと位置（`settings/gui.json` の `tool_window`）---------------

def _gui_config_path(root):
    return os.path.join(root, "settings", "gui.json")


def load_window(root):
    try:
        with io.open(_gui_config_path(root), encoding="utf-8") as fh:
            entry = (json.load(fh).get("tool_window") or {}).get(MOD_NAME) or {}
        return entry if isinstance(entry, dict) else {}
    except (OSError, ValueError):
        return {}


def save_window(root, window):
    """窓の大きさと位置を残す。設定画面が自分の窓を覚えるのと同じ場所。"""
    path = _gui_config_path(root)
    try:
        with io.open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            return
    except (OSError, ValueError):
        cfg = {}
    try:
        maximized = window.state() == "zoomed"
    except Exception:
        maximized = False
    windows = cfg.get("tool_window")
    if not isinstance(windows, dict):
        windows = {}
    windows[MOD_NAME] = {"geometry": window.geometry(), "maximized": maximized}
    cfg["tool_window"] = windows
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".writing"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


# ---- 中身 -----------------------------------------------------------------

class Model(object):
    """画面が見せるもの。tkinter を知らない（`--dump` からも使う）。"""

    def __init__(self):
        self.root, self.state_dir, self.game_dir = locate()
        _add_loader_path(self.root)
        import carryover
        self.C = carryover
        self.settings = load_settings(self.root)
        self.worlds = carryover.list_worlds()
        self.save = None            # 読んでいる世界のセーブ
        self.world = ""             # その世界名
        self.packages = []          # 書き出してある zip
        self.pending = carryover.load_pending(self.state_dir)
        #: 置き先の世界の名前（重複の検査に使う）。世界名 -> {名前}
        self._names = {}

    # -- エクスポート側 ---------------------------------------------------

    def open_world(self, world):
        """1世界ぶんのセーブを読む。読めたか。"""
        self.save = self.C.read_save(world) if world else None
        self.world = world if self.save else ""
        return self.save is not None

    def rows(self):
        """一覧の1行ぶん。

        `[(id, 名前, エリア, 施設, 職, 分類, レベル, 好感度, 関係の言葉)]`。
        エリアと施設は**いま滞在している場所**（`location_of`）。
        """
        out = []
        for npc_id, npc in self.C.npcs_of(self.save).items():
            if not isinstance(npc, dict):
                continue
            affinity, text = self.C.affinity_of(npc)
            area, facility = self.C.location_of(self.save, npc)
            out.append((str(npc_id), npc.get("name") or "", area, facility,
                        npc.get("job") or "", npc.get("category") or "",
                        self.C.level_of(npc), affinity, text))
        out.sort(key=lambda row: (row[1].lower(), int(row[0]) if row[0].isdigit() else 0))
        return out

    def places(self):
        """その世界に出てくる `(エリアの一覧, 施設の一覧)`（フィルターの選択肢）。"""
        areas, facilities = set(), set()
        for npc in self.C.npcs_of(self.save).values():
            if not isinstance(npc, dict):
                continue
            area, facility = self.C.location_of(self.save, npc)
            if area:
                areas.add(area)
            if facility:
                facilities.add(facility)
        return sorted(areas), sorted(facilities)

    def affinity_texts(self):
        """その世界に出てくる関係の言葉（フィルターの選択肢）。

        語彙は決め打たない ― セーブから拾う。
        `affinity_text` は文字列のことも配列のこともあるので `affinity_of` を通す。
        """
        found = set()
        for npc in self.C.npcs_of(self.save).values():
            if isinstance(npc, dict):
                _affinity, text = self.C.affinity_of(npc)
                if text:
                    found.add(text)
        return sorted(found)

    def export_dir(self):
        return os.path.join(self.C.carryover_dir(self.state_dir),
                            self.C.safe_name(self.world or "unknown"))

    def export(self, npc_ids):
        """チェックした NPC を書き出す。`(書けた数, [失敗の説明])`。"""
        npcs = self.C.npcs_of(self.save)
        folder = self.export_dir()
        done, failed = 0, []
        for npc_id in npc_ids:
            npc = npcs.get(str(npc_id))
            if not isinstance(npc, dict):
                failed.append("{}: セーブに居ない".format(npc_id))
                continue
            try:
                dest = self.C.free_path(folder, npc.get("name") or npc_id)
                self.C.export(npc, self.world, npc_id, dest,
                              extra=self.bundle_of(npc_id))
                done += 1
            except OSError as exc:
                failed.append("{}: {}".format(npc.get("name") or npc_id, exc))
        if done:
            self.reload_packages()
        return done, failed

    def memories_of(self, npc_id):
        """`311_` と `403_` がその NPC について持っている記録。無ければ空。

        書き出す時点で同梱するのは、後から元の世界の `state/` が消えても
        zip だけで済むようにするため（DOC.md §7）。
        """
        extra = {}
        for dirname, key in (("npc_profiles", "profile"),
                             ("npc_social_memory", "social")):
            record = self._memory_record(dirname, npc_id)
            if record is not None:
                extra[key] = record
        return extra

    def bundle_of(self, npc_id):
        """zip の `carryover.json` に入れるもの（記憶＋居場所）。

        居場所（エリア名・施設名）を同梱するのは、取り込む側で
        **元の世界のセーブを読まずに済ませる**ため。
        元の世界を消しても zip だけで誰なのか分かる。
        """
        extra = self.memories_of(npc_id)
        npc = self.C.npcs_of(self.save).get(str(npc_id))
        area, facility = self.C.location_of(self.save, npc)
        if area or facility:
            extra["where"] = {"area": area, "facility": facility}
        return extra

    def _memory_record(self, dirname, npc_id):
        from instantale_modloader import state as loader_state
        path = os.path.join(self.state_dir, dirname,
                            loader_state.world_filename(self.world))
        try:
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        record = data.get(str(npc_id))
        return record if isinstance(record, dict) else None

    # -- インポート側 -----------------------------------------------------

    def reload_packages(self):
        self.packages = self.C.list_packages(
            self.C.carryover_dir(self.state_dir))
        return self.packages

    def names_in(self, world):
        """置き先の世界に居る名前。読めなければ `None`（＝検査できない）。"""
        if world in self._names:
            return self._names[world]
        save = self.C.read_save(world)
        found = None
        if save is not None:
            found = {npc.get("name") for npc in self.C.npcs_of(save).values()
                     if isinstance(npc, dict) and npc.get("name")}
        self._names[world] = found
        return found

    def collides(self, name, world):
        """置き先に同名が居るか。`True` / `False` / `None`（検査できない）。"""
        names = self.names_in(world)
        return None if names is None else (name in names)

    def forget_names(self):
        """検査の控えを捨てる（世界を遊んだ後に見直すため）。"""
        self._names.clear()

    def reserve(self, package, world, inherit):
        """予約を1件足す。足したものを返す。"""
        row = self.C.reservation(
            self.C.relative_zip(self.state_dir, package.path), world, inherit)
        row["name"] = package.name
        row["source_world"] = package.source_world
        self.pending.append(row)
        self.save_pending()
        return row

    def drop(self, rows):
        """予約を消す。消した数。"""
        before = len(self.pending)
        drop = {id(row) for row in rows}
        self.pending = [row for row in self.pending if id(row) not in drop]
        if len(self.pending) != before:
            self.save_pending()
        return before - len(self.pending)

    def save_pending(self):
        return self.C.save_pending(self.state_dir, self.pending)

    def recheck(self):
        """`pending` の全件を検査し直す。`[(予約, 衝突しているか)]`。"""
        self.forget_names()
        out = []
        for row in self.pending:
            if row.get("status") != self.C.PENDING:
                out.append((row, False))
                continue
            hit = self.collides(row.get("name") or "", row.get("target_world") or "")
            out.append((row, bool(hit)))
        return out


def matches(text, needle):
    return not needle or needle.lower() in (text or "").lower()


def at_least(value, floor):
    if floor in (None, ""):
        return True
    try:
        return value is not None and int(value) >= int(floor)
    except (TypeError, ValueError):
        return True


def sort_value(text):
    """見出しで並べ替えるときの鍵。

    数字で始まる欄（レベル・親密度 `42 仲間だと感じている`）は数で、それ以外は文字列で並べる。
    空欄は昇順でも降順でも**末尾**に置く（空を先頭に集めると、見たい行が画面の外へ行く）。
    戻り値は (空か, 数の欄でないか, 数, 文字列) の組。
    """
    text = "" if text is None else str(text).strip()
    if not text:
        return (1, 1, 0.0, "")
    head = text.split(" ", 1)[0]
    try:
        return (0, 0, float(head), text)
    except ValueError:
        return (0, 1, 0.0, text)


def same(value, chosen):
    return chosen in (ANY, "", None) or value == chosen


def affinity_label(affinity, text):
    """一覧に出す親密度。`42 仲間だと感じている`。"""
    if affinity is None and not text:
        return ""
    return "{} {}".format("" if affinity is None else affinity, text).strip()


# ---- 画面 -----------------------------------------------------------------

def build_window(model):
    import tkinter as tk
    from tkinter import messagebox, ttk

    window = tk.Tk()
    window.title("NPCのエクスポート / インポート")
    window.minsize(940, 640)
    remembered = load_window(model.root)
    window.geometry(remembered.get("geometry") or "1180x760")
    if remembered.get("maximized"):
        try:
            window.state("zoomed")
        except Exception:
            pass

    # 配色と書体は設定画面のものを借りる。無ければ素の Tk。
    checks = None
    try:
        sys.path.insert(0, os.path.join(model.root, "tools"))
        import gui as loader_gui
        loader_gui.setup_theme(window)
        checks = loader_gui.check_images(window)      # (入, 切)
    except Exception:
        checks = None

    on_image, off_image = checks if checks else (None, None)

    def check_cell(on):
        """チェックの絵。借りられなければ字で示す。"""
        if on_image is not None:
            return {"image": on_image if on else off_image, "text": ""}
        return {"text": "■" if on else "□"}

    outer = ttk.Frame(window, padding=10)
    outer.pack(fill="both", expand=True)
    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)
    status = ttk.Label(outer, style="Faint.TLabel", anchor="w")
    status.pack(fill="x", pady=(6, 0))

    def say(text):
        status.configure(text=text)

    # ================================================== エクスポート
    export_tab = ttk.Frame(tabs, padding=10)
    tabs.add(export_tab, text="エクスポート")

    top = ttk.Frame(export_tab)
    top.pack(fill="x")
    ttk.Label(top, text="元の世界").pack(side="left")
    world_var = tk.StringVar(value=model.worlds[0] if model.worlds else "")
    world_box = ttk.Combobox(top, textvariable=world_var, values=model.worlds,
                             state="readonly", width=28)
    world_box.pack(side="left", padx=(6, 10))
    ttk.Label(top, style="Sub.TLabel",
              text="{} の世界。セーブは読むだけで書き換えない".format(
                  model.C.saves_dir())).pack(side="left")

    # フィルターの欄は列と1対1で7つ。1段に並べると窓を広げないと隠れるので
    # 2段に分ける。上が「誰か」、下が「どこに居るか・どれくらいの相手か」。
    bar = ttk.Frame(export_tab)
    bar.pack(fill="x", pady=(8, 0))
    bar2 = ttk.Frame(export_tab)
    bar2.pack(fill="x", pady=(2, 4))
    filters = {}
    for row, key, label, width in (
            (bar, "name", "名前", 14), (bar, "job", "職", 14),
            (bar, "category", "分類", 14),
            (bar2, "area", "エリア", 14), (bar2, "facility", "施設", 16),
            (bar2, "level", "レベル ≧", 5), (bar2, "affinity", "親密度", 14)):
        ttk.Label(row, text=label, style="Group.TLabel").pack(side="left")
        var = tk.StringVar(value="" if key in ("name", "level") else ANY)
        if key == "name":
            widget = ttk.Entry(row, textvariable=var, width=width)
        elif key == "level":
            widget = ttk.Spinbox(row, from_=0, to=200, width=width, textvariable=var)
        else:
            widget = ttk.Combobox(row, textvariable=var, values=[ANY],
                                  state="readonly", width=width)
        widget.pack(side="left", padx=(4, 10))
        filters[key] = {"var": var, "widget": widget}
    shown = ttk.Label(bar, style="Faint.TLabel")
    shown.pack(side="right")

    def sortable(tree, columns):
        """一覧の見出しを押すと、その欄で昇順、もう一度押すと降順に並べ替える。

        並べ替えは既にある行を `move` で並べ直すだけなので、印（チェック）も選択も保たれる。
        一覧を作り直したあとは `apply()` を呼ぶと同じ並びに戻る。
        空欄は末尾（`sort_value`）。
        """
        state = {"column": None, "reverse": False}
        titles = {col: tree.heading(col, "text") for col in columns}

        def apply():
            column = state["column"]
            if column is None:
                return
            items = list(tree.get_children(""))
            items.sort(key=lambda iid: sort_value(tree.set(iid, column)))
            if state["reverse"]:
                # 降順でも空欄は末尾のまま。
                filled = [i for i in items if sort_value(tree.set(i, column))[0] == 0]
                empty = [i for i in items if sort_value(tree.set(i, column))[0] == 1]
                items = list(reversed(filled)) + empty
            for index, iid in enumerate(items):
                tree.move(iid, "", index)
            for col in columns:
                mark = ""
                if col == column:
                    mark = " ▼" if state["reverse"] else " ▲"
                tree.heading(col, text=titles[col] + mark)

        def click(column):
            if state["column"] == column:
                state["reverse"] = not state["reverse"]
            else:
                state["column"], state["reverse"] = column, False
            apply()

        for col in columns:
            tree.heading(col, command=lambda c=col: click(c))
        return apply

    body = ttk.Frame(export_tab)
    body.pack(fill="both", expand=True)

    columns = ("name", "area", "facility", "job", "category", "level", "affinity")
    tree = ttk.Treeview(body, columns=columns, show="tree headings",
                        selectmode="browse")
    tree.heading("#0", text="")
    tree.column("#0", width=34, minwidth=34, stretch=False, anchor="center")
    for col, head, width, anchor, stretch in (
            ("name", "名前", 170, "w", True),
            ("area", "エリア", 130, "w", False),
            ("facility", "施設", 150, "w", False),
            ("job", "職", 110, "w", False),
            ("category", "分類", 130, "w", False),
            ("level", "レベル", 62, "e", False),
            ("affinity", "親密度", 150, "w", False)):
        tree.heading(col, text=head)
        tree.column(col, width=width, anchor=anchor, stretch=stretch)
    resort_list = sortable(tree, columns)
    scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)

    side = ttk.Frame(body, width=330)
    side.pack(side="right", fill="y", padx=(10, 0))
    side.pack_propagate(False)
    scroll.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)

    def info_panel(parent):
        """NPC 1人の欄。エクスポートとインポートで同じ並び（顔 → 名前 → 情報 → 本文 → 同梱物）。

        顔画像は Tk 8.6 が PNG をそのまま読めるので画像の部品は要らない。
        `PhotoImage` は参照を持っていないと捨てられるので、ラベル自身に括り付ける。
        下敷きに空の画像を敷いたままにするのは、枠の大きさを画素で決めるため。
        画像を外すと `width` / `height` は**文字数**として読まれるので、
        同じ数字が別の大きさになる（165 が 165文字ぶんの幅になる）。
        `compound="center"` で、絵が無いときだけ文字が下敷きの上に出る。

        伸び縮みするのは本文だけ。窓が低いときに欠けるのも本文で、
        同梱物の行は本文の後に置くが `pack` の順は本文より先（下端側から詰める）。
        戻り値は (顔, 名前, 情報, 本文, 同梱物) のウィジェット。
        """
        blank = tk.PhotoImage(width=FACE_BOX, height=FACE_BOX)
        face = tk.Label(parent, borderwidth=1, relief="solid", anchor="center",
                        compound="center", foreground="#646b76",
                        image=blank, text=FACE_EMPTY)
        face.pack(pady=(0, 8))
        face.blank = blank
        face.image = None
        name = ttk.Label(parent, style="InfoName.TLabel", anchor="w")
        name.pack(fill="x")
        meta = ttk.Label(parent, style="Sub.TLabel", anchor="w", justify="left")
        meta.pack(fill="x", pady=(2, 6))
        bundle = ttk.Label(parent, style="Sub.TLabel", anchor="w", justify="left")
        bundle.pack(side="bottom", fill="x")
        ttk.Separator(parent).pack(side="bottom", fill="x", pady=6)
        text = tk.Text(parent, height=2, wrap="word", relief="flat",
                       borderwidth=0, highlightthickness=0)
        text.pack(fill="both", expand=True)
        text.configure(state="disabled")
        return face, name, meta, text, bundle

    face_box, preview_name, preview_meta, preview_text, preview_bundle = info_panel(side)

    bottom = ttk.Frame(export_tab)
    bottom.pack(fill="x", pady=(8, 0))
    dest_label = ttk.Label(bottom, style="Faint.TLabel", anchor="w")
    dest_label.pack(side="left", fill="x", expand=True)
    export_button = ttk.Button(bottom, text="エクスポート", style="Accent.TButton")
    export_button.pack(side="right")

    checked = set()             # チェックの入っている npc id
    visible = []                # いま出ている行（順番どおり）

    def visible_rows():
        name = filters["name"]["var"].get().strip()
        job = filters["job"]["var"].get()
        category = filters["category"]["var"].get()
        floor = filters["level"]["var"].get().strip()
        affinity = filters["affinity"]["var"].get()
        area_filter = filters["area"]["var"].get()
        facility_filter = filters["facility"]["var"].get()
        out = []
        for row in model.rows():
            (_npc_id, npc_name, area, facility, npc_job, npc_category,
             level, _value, text) = row
            if not matches(npc_name, name) or not same(npc_job, job) \
                    or not same(npc_category, category) \
                    or not at_least(level, floor) or not same(text, affinity) \
                    or not same(area, area_filter) \
                    or not same(facility, facility_filter):
                continue
            out.append(row)
        return out

    def refresh_list(keep=True):
        if not keep:
            checked.clear()
        del visible[:]
        tree.delete(*tree.get_children())
        for row in visible_rows():
            (npc_id, npc_name, area, facility, npc_job, npc_category,
             level, value, text) = row
            visible.append(npc_id)
            tree.insert("", "end", iid=npc_id,
                        values=(npc_name, area, facility, npc_job, npc_category,
                                "" if level is None else level,
                                affinity_label(value, text)),
                        **check_cell(npc_id in checked))
        resort_list()
        total = len(model.C.npcs_of(model.save))
        shown.configure(text="{}/{}人を表示・{}人にチェック".format(
            len(visible), total, len(checked)))
        export_button.configure(
            text="チェックした{}人をエクスポート".format(len(checked))
            if checked else "エクスポート",
            state="normal" if checked else "disabled")
        dest_label.configure(text="保存先: {}".format(model.export_dir()))

    def refill_filters():
        areas, facilities = model.places()
        filters["job"]["widget"].configure(
            values=[ANY] + model.C.jobs_of(model.save))
        filters["category"]["widget"].configure(
            values=[ANY] + model.C.categories_of(model.save))
        filters["affinity"]["widget"].configure(
            values=[ANY] + model.affinity_texts())
        filters["area"]["widget"].configure(values=[ANY] + areas)
        filters["facility"]["widget"].configure(values=[ANY] + facilities)
        for key in ("job", "category", "affinity", "area", "facility"):
            if filters[key]["var"].get() not in filters[key]["widget"].cget("values"):
                filters[key]["var"].set(ANY)

    def choose_world(*_args):
        world = world_var.get()
        if not model.open_world(world):
            say("{} のセーブを読めなかった".format(world))
            refill_filters()
            refresh_list(keep=False)
            return
        refill_filters()
        refresh_list(keep=False)
        say("{}: NPC {}人".format(world, len(model.C.npcs_of(model.save))))

    def fit_face(photo):
        """顔画像を枠に合わせる。大きければ縮め、小さければ伸ばす。

        Tk の `PhotoImage` は**整数倍**しか持たない（`subsample` と `zoom`）ので、
        枠にぴたりとは合わない。合わせるのは「はみ出さない範囲で最大の整数倍」。

        実データの `face_image.png` は 194件中 144件が 165×165 だが、
        **40件は 32×64 の小さい版**（残り10件はまちまち）。
        等倍のまま出すと、5体に1体が枠の隅の小さな絵になる。
        """
        if photo is None:
            return None
        width, height = photo.width(), photo.height()
        if not width or not height:
            return None
        try:
            if max(width, height) > FACE_BOX:
                step = (max(width, height) + FACE_BOX - 1) // FACE_BOX
                return photo.subsample(step, step)
            times = min(FACE_BOX // width, FACE_BOX // height)
            if times > 1:
                return photo.zoom(times, times)
        except Exception:
            return photo
        return photo

    def load_face(path):
        """ファイルから顔画像を読む。読めなければ None（プレビューを止めない）。"""
        try:
            return fit_face(tk.PhotoImage(file=path))
        except Exception:
            return None

    def load_face_data(blob):
        """zip から取り出した顔画像。読めなければ None。

        Tk の `PhotoImage` は base64 の PNG を `data=` で受ける。
        いったんファイルに落とさずに済む。
        """
        if not blob:
            return None
        try:
            return fit_face(
                tk.PhotoImage(data=base64.b64encode(blob).decode("ascii")))
        except Exception:
            return None

    def put_face(box, photo):
        """顔の枠に絵を入れる。無ければ枠だけ残して文字にする。"""
        box.image = photo               # 参照を握っていないと捨てられる
        box.configure(image=photo if photo is not None else box.blank,
                      text="" if photo is not None else FACE_EMPTY)

    def show_face(npc):
        """その NPC の `face_image.png` を出す。無ければ枠だけ残して空にする。"""
        photo = None
        if isinstance(npc, dict):
            folder = model.C.image_dir_of(npc, model.world)
            path = os.path.join(folder, model.C.FACE_IMAGE) if folder else ""
            if path and os.path.isfile(path):
                photo = load_face(path)
        put_face(face_box, photo)

    def show_preview(npc_id):
        npc = model.C.npcs_of(model.save).get(str(npc_id))
        preview_text.configure(state="normal")
        preview_text.delete("1.0", "end")
        show_face(npc)
        if not isinstance(npc, dict):
            preview_name.configure(text="")
            preview_meta.configure(text="")
            preview_bundle.configure(text="")
            preview_text.configure(state="disabled")
            return
        affinity, text = model.C.affinity_of(npc)
        config = npc.get("config") if isinstance(npc.get("config"), dict) else {}
        preview_name.configure(text=npc.get("name") or "")
        area, facility = model.C.location_of(model.save, npc)
        preview_meta.configure(text="元の世界: {}\n滞在: {}\n職: {} ・ 分類: {}\n"
                                    "レベル: {} ・ 難易度: {}\n親密度: {}".format(
                                        model.world,
                                        " / ".join(x for x in (area, facility) if x)
                                        or "（記録なし）",
                                        npc.get("job") or "",
                                        npc.get("category") or "",
                                        model.C.level_of(npc),
                                        config.get("difficulty_level"),
                                        affinity_label(affinity, text) or "（記録なし）"))
        preview_text.insert("1.0", npc.get("profile") or "")
        preview_text.configure(state="disabled")
        images = model.C.image_files(model.world, npc)
        memories = model.memories_of(npc_id)
        preview_bundle.configure(
            text="エクスポートに含まれるもの\n画像 {}枚\n記憶: {}".format(
                len(images),
                "、".join(sorted(memories)) if memories else "（無し）"))

    def toggle(npc_id):
        if npc_id in checked:
            checked.discard(npc_id)
        else:
            checked.add(npc_id)
        tree.item(npc_id, **check_cell(npc_id in checked))
        refresh_counts()

    def refresh_counts():
        total = len(model.C.npcs_of(model.save))
        shown.configure(text="{}/{}人を表示・{}人にチェック".format(
            len(visible), total, len(checked)))
        export_button.configure(
            text="チェックした{}人をエクスポート".format(len(checked))
            if checked else "エクスポート",
            state="normal" if checked else "disabled")

    def on_click(event):
        """チェックの列を押したら入り切り。それ以外はプレビュー。"""
        item = tree.identify_row(event.y)
        if not item:
            return
        if tree.identify_column(event.x) == "#0":
            toggle(item)
            return "break"
        show_preview(item)

    def toggle_all():
        """見出しのチェック。表示中の全員に入れる／全部外す。"""
        if all(npc_id in checked for npc_id in visible) and visible:
            for npc_id in visible:
                checked.discard(npc_id)
        else:
            checked.update(visible)
        for npc_id in visible:
            tree.item(npc_id, **check_cell(npc_id in checked))
        refresh_counts()

    def do_export():
        if not checked:
            return
        done, failed = model.export(sorted(checked, key=lambda k: int(k) if k.isdigit() else 0))
        checked.clear()
        refresh_list()
        reload_import()
        if failed:
            messagebox.showwarning(
                "書き出せなかったものがある",
                "{}人を書き出した。\n\n".format(done) + "\n".join(failed[:10]))
        say("{}人をエクスポートした（{}）".format(done, model.export_dir()))

    tree.heading("#0", text="", command=toggle_all)
    tree.bind("<Button-1>", on_click)
    tree.bind("<<TreeviewSelect>>", lambda _e: show_preview(tree.focus()))
    tree.bind("<space>", lambda _e: toggle(tree.focus()) if tree.focus() else None)
    export_button.configure(command=do_export)
    world_box.bind("<<ComboboxSelected>>", choose_world)
    for key in filters:
        filters[key]["var"].trace_add("write", lambda *_a: refresh_list())

    # ================================================== インポート
    import_tab = ttk.Frame(tabs, padding=10)
    tabs.add(import_tab, text="インポート")

    pane = ttk.Frame(import_tab)
    pane.pack(fill="both", expand=True)

    left = ttk.Frame(pane)
    left.pack(side="left", fill="both", expand=True)
    ttk.Label(left, text="エクスポート済みのNPC", style="Group.TLabel").pack(anchor="w")

    pbar = ttk.Frame(left)
    pbar.pack(fill="x", pady=(4, 4))
    pfilters = {}
    for key, label, width in (("name", "名前", 12), ("world", "元の世界", 14),
                              ("job", "職", 12), ("level", "レベル ≧", 5),
                              ("affinity", "親密度", 14)):
        ttk.Label(pbar, text=label, style="Group.TLabel").pack(side="left")
        var = tk.StringVar(value="" if key in ("name", "level") else ANY)
        if key == "name":
            widget = ttk.Entry(pbar, textvariable=var, width=width)
        elif key == "level":
            widget = ttk.Spinbox(pbar, from_=0, to=200, width=width, textvariable=var)
        else:
            widget = ttk.Combobox(pbar, textvariable=var, values=[ANY],
                                  state="readonly", width=width)
        widget.pack(side="left", padx=(4, 8))
        pfilters[key] = {"var": var, "widget": widget}
    pshown = ttk.Label(pbar, style="Faint.TLabel")
    pshown.pack(side="right")

    plist = ttk.Frame(left)
    plist.pack(fill="both", expand=True)
    pcolumns = ("name", "world", "job", "level", "affinity")
    ptree = ttk.Treeview(plist, columns=pcolumns, show="headings", selectmode="browse")
    for col, head, width, anchor, stretch in (
            ("name", "名前", 170, "w", True), ("world", "元の世界", 130, "w", False),
            ("job", "職", 100, "w", False), ("level", "レベル", 60, "e", False),
            ("affinity", "親密度", 150, "w", False)):
        ptree.heading(col, text=head)
        ptree.column(col, width=width, anchor=anchor, stretch=stretch)
    resort_packages = sortable(ptree, pcolumns)
    pscroll = ttk.Scrollbar(plist, orient="vertical", command=ptree.yview)
    ptree.configure(yscrollcommand=pscroll.set)
    pscroll.pack(side="right", fill="y")
    ptree.pack(side="left", fill="both", expand=True)

    # 予約の一覧は左の列の下。右の列（NPC の欄と予約の操作）を全高にするため、
    # 横幅いっぱいには広げない。
    ttk.Separator(left).pack(fill="x", pady=8)
    ttk.Label(left, text="予約の一覧（世界をロードしたときに配置される）",
              style="Group.TLabel").pack(anchor="w")
    plan = ttk.Frame(left)
    plan.pack(fill="x")          # 高さは rtree の行数ぶん。余りは上の一覧へ
    rcolumns = ("name", "source", "target", "inherit", "status")
    rtree = ttk.Treeview(plan, columns=rcolumns, show="headings", height=6,
                         selectmode="browse")
    for col, head, width, stretch in (
            ("name", "NPC", 150, False), ("source", "元の世界", 120, False),
            ("target", "置き先", 130, False), ("inherit", "引き継ぎ", 190, True),
            ("status", "状態", 160, False)):
        rtree.heading(col, text=head)
        rtree.column(col, width=width, stretch=stretch)
    resort_plan = sortable(rtree, rcolumns)
    rscroll = ttk.Scrollbar(plan, orient="vertical", command=rtree.yview)
    rtree.configure(yscrollcommand=rscroll.set)
    rscroll.pack(side="right", fill="y")
    rtree.pack(side="left", fill="both", expand=True)
    rtree.tag_configure("warn", foreground="#9a5b00")
    rtree.tag_configure("done", foreground="#646b76")

    plan_bar = ttk.Frame(left)
    plan_bar.pack(fill="x", pady=(6, 0))
    drop_button = ttk.Button(plan_bar, text="一覧から消す")
    drop_button.pack(side="left")
    recheck_button = ttk.Button(plan_bar, text="全予約を検査し直す")
    recheck_button.pack(side="left", padx=(6, 0))
    plan_note = ttk.Label(plan_bar, style="Warn.TLabel", anchor="e")
    plan_note.pack(side="right", fill="x", expand=True)

    right = ttk.Frame(pane, width=330)
    right.pack(side="right", fill="y", padx=(10, 0))
    right.pack_propagate(False)

    # 予約の操作は欄の**下端に先に**据える。pack は並べた順に場所を取るので、
    # 上から順に置くと窓が低いときに末尾（引き継ぐもの・予約ボタン）から欠ける。
    reserve = ttk.Frame(right)
    reserve.pack(side="bottom", fill="x")
    ttk.Separator(reserve).pack(fill="x", pady=4)
    ttk.Label(reserve, text="予約の内容", style="Group.TLabel").pack(anchor="w")

    target_row = ttk.Frame(reserve)
    target_row.pack(fill="x", pady=(4, 4))
    ttk.Label(target_row, text="置き先の世界").pack(side="left")
    target_var = tk.StringVar(value=model.worlds[0] if model.worlds else "")
    target_box = ttk.Combobox(target_row, textvariable=target_var,
                              values=model.worlds, state="readonly", width=20)
    target_box.pack(side="left", padx=(6, 0))

    verdict = ttk.Label(reserve, anchor="w", justify="left", style="Sub.TLabel")
    verdict.pack(fill="x", pady=(0, 4))

    ttk.Label(reserve, text="引き継ぐもの", style="Group.TLabel").pack(anchor="w")
    inherit_vars = {}
    for key, label, setting in INHERIT_ROWS:
        var = tk.BooleanVar(value=bool(model.settings.get(setting, True)))
        ttk.Checkbutton(reserve, text=label, variable=var).pack(anchor="w")
        inherit_vars[key] = var

    reserve_button = ttk.Button(reserve, text="インポートを予約する",
                                style="Accent.TButton")
    reserve_button.pack(anchor="e", pady=(6, 0))

    # 選んだ NPC の中身。エクスポート側と同じ部品で、zip から出す。
    ttk.Label(right, text="選んでいるNPC", style="Group.TLabel").pack(anchor="w", pady=(0, 4))
    pkg_face, pkg_name, pkg_meta, pkg_text, pkg_bundle = info_panel(right)

    pvisible = []       # いま出ている zip の場所（行の iid）

    def package_of(key):
        return next((p for p in model.packages if p.path == key), None)

    def reload_import(keep_selection=True):
        model.reload_packages()
        worlds = sorted({p.source_world for p in model.packages if p.source_world})
        jobs = sorted({p.job for p in model.packages if p.job})
        texts = sorted({p.affinity[1] for p in model.packages if p.affinity[1]})
        pfilters["world"]["widget"].configure(values=[ANY] + worlds)
        pfilters["job"]["widget"].configure(values=[ANY] + jobs)
        pfilters["affinity"]["widget"].configure(values=[ANY] + texts)
        for key in ("world", "job", "affinity"):
            if pfilters[key]["var"].get() not in pfilters[key]["widget"].cget("values"):
                pfilters[key]["var"].set(ANY)
        refresh_packages()
        refresh_plan()

    def refresh_packages():
        name = pfilters["name"]["var"].get().strip()
        world = pfilters["world"]["var"].get()
        job = pfilters["job"]["var"].get()
        floor = pfilters["level"]["var"].get().strip()
        text_filter = pfilters["affinity"]["var"].get()
        del pvisible[:]
        ptree.delete(*ptree.get_children())
        for package in model.packages:
            affinity, text = package.affinity
            if not matches(package.name, name) or not same(package.source_world, world) \
                    or not same(package.job, job) or not at_least(package.level, floor) \
                    or not same(text, text_filter):
                continue
            pvisible.append(package.path)
            ptree.insert("", "end", iid=package.path,
                         values=(package.name, package.source_world, package.job,
                                 "" if package.level is None else package.level,
                                 affinity_label(affinity, text)))
        resort_packages()
        pshown.configure(text="{}/{}件を表示".format(len(pvisible), len(model.packages)))
        check_collision()

    def inherit_label(row):
        chosen = row.get("inherit") or {}
        names = [label.split("（")[0] for key, label, _s in INHERIT_ROWS
                 if chosen.get(key)]
        return "・".join(names) if names else "（無し）"

    def refresh_plan():
        rtree.delete(*rtree.get_children())
        warned = 0
        for index, row in enumerate(model.pending):
            status = row.get("status")
            tag = ""
            text = {model.C.PENDING: "待機", model.C.PLACED: "配置済み",
                    model.C.SKIPPED: "見送り"}.get(status, str(status))
            if status == model.C.PLACED:
                tag = "done"
                if row.get("placed_at"):
                    text = "配置済み（{}）".format(row["placed_at"])
            elif status == model.C.SKIPPED:
                tag = "warn"
                text = "見送り: {}".format(row.get("reason") or "")
            elif model.collides(row.get("name") or "", row.get("target_world") or ""):
                tag = "warn"
                text = "同名の人物が居る"
                warned += 1
            rtree.insert("", "end", iid=str(index),
                         values=(row.get("name") or "", row.get("source_world") or "",
                                 row.get("target_world") or "", inherit_label(row), text),
                         tags=(tag,) if tag else ())
        resort_plan()
        plan_note.configure(
            text="{}件が同名で見送りになる。先住側をセーブエディタで改名するか、"
                 "置き先を変えると予約できる".format(warned) if warned else "")

    def show_package(package):
        """選んだ zip の中身を右に出す。未選択なら空にする。"""
        pkg_text.configure(state="normal")
        pkg_text.delete("1.0", "end")
        if package is None:
            put_face(pkg_face, None)
            pkg_name.configure(text="")
            pkg_meta.configure(text="")
            pkg_bundle.configure(text="")
            pkg_text.configure(state="disabled")
            return
        put_face(pkg_face, load_face_data(package.images.get(model.C.FACE_IMAGE)))
        affinity, text = package.affinity
        area, facility = package.where
        config = package.npc.get("config")
        config = config if isinstance(config, dict) else {}
        pkg_name.configure(text=package.name)
        pkg_meta.configure(text="元の世界: {}\n滞在: {}\n職: {} ・ 分類: {}\n"
                                "レベル: {} ・ 難易度: {}\n親密度: {}".format(
                                    package.source_world or "（不明）",
                                    " / ".join(x for x in (area, facility) if x)
                                    or "（記録なし）",
                                    package.job or "",
                                    package.npc.get("category") or "",
                                    package.level,
                                    config.get("difficulty_level"),
                                    affinity_label(affinity, text) or "（記録なし）"))
        pkg_text.insert("1.0", package.npc.get("profile") or "")
        pkg_text.configure(state="disabled")
        memories = [key for key in ("profile", "social") if package.extra.get(key)]
        pkg_bundle.configure(
            text="この zip に入っているもの\n画像 {}枚\n記憶: {}".format(
                len(package.images),
                "、".join(memories) if memories else "（無し）"))

    def check_collision(*_args):
        key = ptree.focus()
        package = package_of(key)
        show_package(package)
        world = target_var.get()
        if package is None or not world:
            verdict.configure(text="", style="Sub.TLabel")
            reserve_button.configure(state="disabled")
            return
        hit = model.collides(package.name, world)
        if hit is None:
            verdict.configure(text="{} のセーブを読めない。検査できない".format(world),
                              style="Warn.TLabel")
            reserve_button.configure(state="disabled")
        elif hit:
            verdict.configure(
                text="{} には同名の「{}」が既に居る。\nこのままでは予約できない"
                     "（先住側を改名するか、置き先を変える）".format(world, package.name),
                style="Warn.TLabel")
            reserve_button.configure(state="disabled")
        else:
            verdict.configure(text="同名の人物は居ない（いま検査した）",
                              style="Sub.TLabel")
            reserve_button.configure(state="normal")

    def do_reserve():
        package = package_of(ptree.focus())
        world = target_var.get()
        if package is None or not world:
            return
        if model.collides(package.name, world):
            check_collision()
            return
        inherit = {key: bool(var.get()) for key, var in inherit_vars.items()}
        model.reserve(package, world, inherit)
        refresh_plan()
        say("「{}」を {} へ予約した".format(package.name, world))

    def drop_label(row):
        """その行を消すことが何を意味するか。

        まだ置いていない予約なら「取り消す」でよいが、
        置いた後の行を消しても**世界の NPC は消えない**（消えるのは控えだけ）。
        同じ言葉で両方を指すと、置いた NPC を消すボタンに見える。
        """
        if row is None:
            return "一覧から消す"
        if row.get("status") == model.C.PENDING:
            return "予約を取り消す"
        return "記録を消す"

    def selected_row():
        item = rtree.focus()
        try:
            return model.pending[int(item)] if item else None
        except (ValueError, IndexError):
            return None

    def on_plan_select(*_args):
        row = selected_row()
        drop_button.configure(text=drop_label(row),
                              state="normal" if row is not None else "disabled")

    def do_drop():
        row = selected_row()
        if row is None:
            return
        placed = row.get("status") != model.C.PENDING
        model.drop([row])
        refresh_plan()
        on_plan_select()
        if placed:
            say("「{}」の記録を消した（世界に居る NPC はそのまま。"
                "消すならセーブエディタで）".format(row.get("name") or ""))
        else:
            say("「{}」の予約を取り消した（zip は消さない）".format(
                row.get("name") or ""))

    def do_recheck():
        model.recheck()
        refresh_plan()
        check_collision()
        say("予約を検査し直した（{}件）".format(len(model.pending)))

    ptree.bind("<<TreeviewSelect>>", check_collision)
    rtree.bind("<<TreeviewSelect>>", on_plan_select)
    target_box.bind("<<ComboboxSelected>>", check_collision)
    reserve_button.configure(command=do_reserve)
    drop_button.configure(command=do_drop)
    recheck_button.configure(command=do_recheck)
    for key in pfilters:
        pfilters[key]["var"].trace_add("write", lambda *_a: refresh_packages())

    def on_close():
        # 引き継ぎの選択は次に開いたときの既定にする（mod.json の settings）。
        values = dict(model.settings)
        for key, _label, setting in INHERIT_ROWS:
            values[setting] = bool(inherit_vars[key].get())
        save_settings(model.root, values)
        save_window(model.root, window)
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", on_close)

    on_plan_select()
    show_package(None)
    if model.worlds:
        choose_world()
    else:
        say("{} に世界が無い".format(model.C.saves_dir()))
    reload_import()
    return window


# ---- 窓を開かずに中身を見る（検証用）--------------------------------------

def dump(model):
    out = ["data: {}".format(model.C.data_dir()),
           "state: {}".format(model.C.carryover_dir(model.state_dir)),
           "worlds: {}".format(", ".join(model.worlds) or "<none>")]
    for world in model.worlds:
        if not model.open_world(world):
            out.append("  {}: セーブを読めない".format(world))
            continue
        rows = model.rows()
        out.append("  {}: NPC {}人 / 職 {} / 親密度の語彙 {}".format(
            world, len(rows), len(model.C.jobs_of(model.save)),
            len(model.affinity_texts())))
    packages = model.reload_packages()
    out.append("packages: {}".format(len(packages)))
    for package in packages[:20]:
        affinity, text = package.affinity
        out.append("  {} <- {} (job={} level={} {})".format(
            package.name, package.source_world, package.job, package.level,
            affinity_label(affinity, text)))
    out.append("pending: {}".format(len(model.pending)))
    for row in model.pending:
        out.append("  {} -> {} [{}]".format(row.get("name"),
                                            row.get("target_world"),
                                            row.get("status")))
    return "\n".join(out)


def main(argv):
    model = Model()
    if "--dump" in argv:
        text = dump(model)
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("utf-8", "replace").decode("utf-8", "replace"))
        return 0
    build_window(model).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
