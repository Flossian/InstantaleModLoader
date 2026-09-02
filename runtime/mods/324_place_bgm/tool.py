# -*- coding: utf-8 -*-
"""場所の BGM の選曲画面。`state/musics/place/playlist.json` と `worlds/<世界>.json` を編集する。

    python runtime/mods/324_place_bgm/tool.py           窓を開く
    python runtime/mods/324_place_bgm/tool.py --dump    窓を開かず、いま読める一覧を標準出力に出す

TECH.md §3.12 の契約で動く（`322_battle_bgm` の道具と同じ）。
ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見てこのファイルを
サブプロセスで起動し、場所は環境変数で渡す。直接起動したときは自分で探す（`locate()`）。

| 変数 | 中身 |
|---|---|
| `IML_ROOT` | 配布フォルダの根 |
| `IML_STATE_DIR` | `state/` の場所 |
| `IML_GAME_DIR` | ゲーム本体のフォルダ |
| `IML_INSTANTALE_DATA` | セーブの置き場（無ければ `%LOCALAPPDATA%\\Darmabeko\\Instantale`） |

画面は左右2つ。
左がタブ2つ（一括設定 / ワールド個別設定）で、右がそこで選んだ場所の選曲。
選曲欄を1つにして左だけをタブにしているのは、同じ画面を2つ並べないため。

| タブ | 左に並ぶもの | 書く先 |
|---|---|---|
| 一括設定 | 場所の種類（施設 13 種、土地 4 種） | `playlist.json` |
| ワールド個別設定 | セーブの世界 → 土地 → 施設 | `worlds/<世界>.json` の `playlist` |

右は `322_` と同じ左右2段で、左が置いてある曲の全部（曲名・フォルダ・置き場）、
右がその場所で使う曲（曲名・フォルダ・置き場・重み・確率）。
左をダブルクリック（または「使う >>」）で右へ入り、右をダブルクリック（または「<< 外す」）で外れる。
右で選んだ曲の重みは下の欄で変える。
一覧の上の欄で列ごとに絞り込める（曲名は部分一致、フォルダと置き場は選択、重みは下限）。
絞り込みは表示だけを変え、値は変えない（「全て使う / 全て外す」は表示中の曲だけに効く）。

曲の鍵は `musics/` からの相対パス（`town/calm/曲.mp3`）。
ゲームの曲は 97 曲がフォルダ2段に分かれているので、フォルダで絞れることが要る
（「町の calm を全部使う」がフォルダで絞って「全て使う」の2手で済む）。

ワールド個別設定は `savedata.json` を読んで場所を並べる（復号は GAME.md §2.16。**読むだけで書かない**）。
施設 id は土地の中でしか一意でないので、施設は `土地id/施設id` で持つ（GAME.md §2.7）。
本体が覚えた曲（`chosen`）は右の見出しに出し、「覚えた曲を消す」で次に入ったとき選び直させる。
`worlds/<世界>.json` は本体も書く（`chosen`）ので、保存の直前に読み直して `playlist` だけを差し替える。

保存は `playlist.json` と `worlds/<世界>.json` への書き込み。
書き方はローダの `write_json`（隣に書いてから差し替える。TECH.md §3.11.1）。
ファイルにあって曲が無い行（曲を一時的に外しているとき）は消さずに残す。
MOD 本体（`place_bgm.py`）は決めるたびにこれらを読むので、ゲームを起動したまま保存しても次の移動から効く。

`mod.json` の `settings`（新しい曲に付ける重み・同じ曲の連続回避・土地と施設の曲を覚える）もこの画面が引き受ける。
値の置き場は他の MOD と同じ `settings/mod_settings.json`（`instantale_modloader.config` 経由。既定と同じ値は書かない）。

MOD 本体は import しない。
ここはゲームの外で走る別プロセスで、本体はゲームの中で走る。
共有したい定数（フォルダ名・拡張子・種類）はこのファイルに写してある。
セーブの復号（`decode`）は `323_npc_carryover/carryover.py` と同じ物の写し。
MOD どうしは import しないので（TECH.md §3.2.3）、ローダの語彙（`instantale_modloader.saves` 案)へ寄せるのが筋。`323_` と同時に行う。
"""

import io
import json
import os
import sys
import time

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
MOD_NAME = os.path.basename(MOD_DIR)

# place_bgm.py と同じ値。
STATE_SUBDIR = ("musics", "place")
WORLDS_SUBDIR = "worlds"
ASSET_SUBDIR = ("Assets", "sounds", "musics")
BATTLE_FOLDER = "battle"
EXTENSIONS = (".mp3", ".ogg", ".wav")
PLAYLIST_NAME = "playlist.json"
DEFAULT_WEIGHT = 100
PASSAGE_TYPES = ("entrance", "exit", "ward", "dungeon_location")
SIZE_ALIAS = {"dungeons": "dungeon"}
PLAYLIST_HELP = [
    "場所の BGM の重み。playlists → {場所の種類: {曲: 重み}}",
    "施設の種類: inn guild general_store specialty_shop blacksmith medical_facility administrative_office "
    "underworld_office colosseum slave_market training_facility location free",
    "土地の種類: area:town area:village area:city area:dungeon（施設の段に候補が無いときに使う）",
    "曲は Assets/sounds/musics（battle/ を除く）と state/musics/place の .mp3 / .ogg / .wav。"
    "鍵は musics/ からの相対パス（town/calm/曲.mp3）。同じ鍵なら state 側",
    "重みは比率。同じ種類の合計に対する割合が確率になる（合計 100 なら数字がそのままパーセント）",
    "0 か無ければその種類では鳴らない。どの段にも無ければゲームの曲",
    "土地・施設ごとの個別指定と覚えた曲は worlds/<世界>.json（DOC.md）",
]

# セーブの置き場と復号（`323_` の carryover.py と同じ）。
DATA_VENDOR = ("Darmabeko", "Instantale")
SAVE_KEY = b"Instantale_Save_Key_2026"

# 左の一覧。群 → (鍵, 表示名, 説明)。鍵は playlist.json の項目名。
GROUPS = (
    ("facility", "施設", (
        ("inn", "宿屋", "宿泊・休息。入っている間だけ鳴る"),
        ("guild", "ギルド", "依頼・仲間の募集"),
        ("general_store", "雑貨店", ""),
        ("specialty_shop", "専門店", ""),
        ("blacksmith", "鍛冶屋", ""),
        ("medical_facility", "医療施設", ""),
        ("administrative_office", "役場", ""),
        ("underworld_office", "裏の事務所", ""),
        ("colosseum", "闘技場", "受付の間。試合そのものは戦闘曲（322_）"),
        ("slave_market", "奴隷市場", ""),
        ("training_facility", "訓練施設", ""),
        ("location", "名所", "主のいない場所（location）"),
        ("free", "自由生成施設", "ゲームが作る施設（GAME.md §2.21）"),
    )),
    ("area", "土地", (
        ("area:town", "町", "施設の段に候補が無いときに鳴る。土地ごとに覚える"),
        ("area:village", "村", "同上"),
        ("area:city", "都市", "同上"),
        ("area:dungeon", "ダンジョン", "依頼で入る土地。1回きりなので覚えない"),
    )),
)
CATEGORY_KEYS = tuple(key for _g, _label, rows in GROUPS for key, _n, _d in rows)
CATEGORY_LABEL = dict((key, name) for _g, _label, rows in GROUPS for key, name, _d in rows)
CATEGORY_NOTE = dict((key, note) for _g, _label, rows in GROUPS for key, _n, note in rows)
SIZE_LABEL = {"town": "町", "village": "村", "city": "都市", "dungeon": "ダンジョン"}

WHERE_LABEL = {"assets": "ゲーム (Assets)", "state": "state"}
LEFT_WIDTH = 330            # 左（場所の一覧）の初めの幅（px）。仕切りは動かせる
ANY = "全て"
ROOT_FOLDER = "（直下）"

# mod.json の "settings" と同じ名前・同じ既定値（place_bgm.py の定数と同じ）。
SETTING_DEFAULTS = {"DEFAULT_WEIGHT": DEFAULT_WEIGHT, "AVOID_REPEAT": True,
                    "AREA_STICKY": True, "FACILITY_STICKY": False}
BOOL_SETTINGS = (
    ("AVOID_REPEAT", "前回と同じ曲を続けて選ばない"),
    ("AREA_STICKY", "土地の曲は一度決めたら覚える"),
    ("FACILITY_STICKY", "施設の曲は一度決めたら覚える"),
)


def _add_loader_path(root):
    """ローダ（`instantale_modloader`）を import できるようにする。

    置き場は `IML_ROOT/runtime`。無ければ自分の位置から（`runtime/mods/<この MOD>/` の2つ上）。
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
    for key, _label in BOOL_SETTINGS:
        if isinstance(chosen.get(key), bool):
            values[key] = chosen[key]
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
    """フォルダ以下の曲を再帰で集める。{相対パス: 絶対パス}。直下の battle/ は除く。"""
    found = {}
    if not folder or not os.path.isdir(folder):
        return found
    for dirpath, dirnames, filenames in os.walk(folder):
        rel_dir = os.path.relpath(dirpath, folder).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
            dirnames[:] = sorted(d for d in dirnames if d.lower() != BATTLE_FOLDER)
        else:
            dirnames.sort()
        for name in sorted(filenames):
            if not name.lower().endswith(EXTENSIONS):
                continue
            key = rel_dir + "/" + name if rel_dir else name
            found[key] = os.path.join(dirpath, name)
    return found


def scan(asset_dir, state_dir):
    """{鍵: 置き場}。同じ鍵なら state 側。"""
    found = {}
    for folder, where in ((asset_dir, "assets"), (state_dir, "state")):
        for key in list_tracks(folder):
            found[key] = where
    return found


def folder_of(key):
    return key.rsplit("/", 1)[0] if "/" in key else ROOT_FOLDER


def name_of(key):
    return key.rsplit("/", 1)[-1]


def load_json(path):
    """ファイル全体。無い・読めないときは空の辞書。"""
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


load_playlist = load_json


def weight_of(playlist, key):
    try:
        value = float((playlist or {}).get(key, 0))
    except (TypeError, ValueError):
        return 0
    return int(value) if value > 0 else 0


def matches(key, where, weight, name_filter, folder_filter, where_filter, min_weight):
    """絞り込み。曲名は部分一致（大文字小文字を見ない）、フォルダと置き場は一致、重みは下限。"""
    if name_filter and name_filter.lower() not in key.lower():
        return False
    if folder_filter and folder_filter != ANY and folder_of(key) != folder_filter:
        return False
    if where_filter and where_filter != ANY and WHERE_LABEL.get(where, where) != where_filter:
        return False
    if min_weight and weight < min_weight:
        return False
    return True


def id_sort_key(value):
    """id を数として並べる（ローダの `ui.id_sort_key` と同じ）。"""
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def place_sort_key(key):
    return tuple(id_sort_key(part) for part in str(key).split("/"))


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


def world_filename(root, key):
    """世界の鍵からファイル名。ローダの `state.world_filename`（本体と同じ規則）。無ければ素の名前。"""
    try:
        _add_loader_path(root)
        from instantale_modloader import state
        return state.world_filename(key)
    except Exception:
        return key + ".json"


# ----------------------------------------------------------------- セーブを読む
def data_dir(override=""):
    r"""`saves\` の親。`override`（または環境変数 `IML_INSTANTALE_DATA`）が在ればそちら。"""
    if override:
        return override
    env = os.environ.get("IML_INSTANTALE_DATA")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, *DATA_VENDOR)


def saves_dir(base=""):
    return os.path.join(base or data_dir(), "saves")


def save_path(world, base=""):
    return os.path.join(saves_dir(base), world, "savedata.json")


def list_worlds(base=""):
    """`savedata.json` を持つ世界のフォルダ名。名前順。"""
    try:
        names = os.listdir(saves_dir(base))
    except OSError:
        return []
    return sorted((n for n in names if os.path.isfile(save_path(n, base))), key=lambda t: t.lower())


def xor(raw):
    return bytes(byte ^ SAVE_KEY[index % len(SAVE_KEY)] for index, byte in enumerate(raw))


def decode(raw):
    """セーブのバイト列を辞書にする。読めなければ None。素の JSON → XOR の順。"""
    for candidate in (raw, None):
        text = None
        try:
            text = (candidate if candidate is not None else xor(raw)).decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def read_save(world, base=""):
    try:
        with io.open(save_path(world, base), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    return decode(raw)


def places_of(save):
    """セーブから場所の木を組む。{"name": 世界名, "areas": [{"id", "name", "size", "facilities": [...]}]}。

    土地は id の数の順。施設は通路（`PASSAGE_TYPES`）を除き、id の数の順。
    世界名は `world_data.name`（本体の `state.world_key` と同じ見方）。
    """
    save = save if isinstance(save, dict) else {}
    world_data = save.get("world_data") if isinstance(save.get("world_data"), dict) else {}
    name = ""
    for key in ("world_name", "name", "title"):
        value = world_data.get(key)
        if isinstance(value, str) and value:
            name = value
            break
    areas = []
    raw_areas = save.get("areas") if isinstance(save.get("areas"), dict) else {}
    for aid in sorted(raw_areas, key=id_sort_key):
        area = raw_areas[aid]
        if not isinstance(area, dict):
            continue
        size = area.get("size") if isinstance(area.get("size"), str) else ""
        size = SIZE_ALIAS.get(size.lower(), size.lower())
        facilities = []
        nodes = area.get("nodes") if isinstance(area.get("nodes"), dict) else {}
        for node in nodes.values():
            raw = node.get("facilities") if isinstance(node, dict) and isinstance(node.get("facilities"), dict) else {}
            for fid, facility in raw.items():
                if not isinstance(facility, dict):
                    continue
                ftype = facility.get("facility_type") if isinstance(facility.get("facility_type"), str) else ""
                if ftype in PASSAGE_TYPES:
                    continue
                facilities.append({"id": str(fid), "type": ftype,
                                   "name": facility.get("name") if isinstance(facility.get("name"), str) else ""})
        facilities.sort(key=lambda f: id_sort_key(f["id"]))
        areas.append({"id": str(aid), "size": size,
                      "name": area.get("name") if isinstance(area.get("name"), str) else "",
                      "facilities": facilities})
    return {"name": name, "areas": areas}


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
    """窓の大きさと位置を `settings/gui.json` の `tool_window[MOD 名]` に残す。"""
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
    """一覧の中身。

    `pool` は {鍵: 置き場}、`playlists` は {種類: {鍵: 重み}}（プールにある曲だけ）。
    `world_playlists` は {(世界の鍵, 群, 場所): {鍵: 重み}}。群は `"areas"` / `"facilities"`、
    場所は土地 id か `土地id/施設id`。開いた世界のぶんだけ入る。
    """

    def __init__(self, root=None, state_root=None, game_dir=None, data_root=None):
        found = locate()
        self.root = root or found[0]
        self.state_root = state_root or found[1]
        self.game_dir = found[2] if game_dir is None else game_dir
        self.state_dir = os.path.join(self.state_root, *STATE_SUBDIR)
        self.worlds_dir = os.path.join(self.state_dir, WORLDS_SUBDIR)
        self.asset_dir = os.path.join(self.game_dir, *ASSET_SUBDIR) if self.game_dir else ""
        self.playlist_path = os.path.join(self.state_dir, PLAYLIST_NAME)
        self.data_root = data_dir(data_root or "")
        self.pool = {}
        self.playlists = {}
        self.file = {}
        self.saved = {}
        self.settings = dict(SETTING_DEFAULTS)
        self.saved_settings = dict(SETTING_DEFAULTS)
        self.worlds = []
        self.places = {}            # フォルダ名 -> places_of の戻り（読めなければ None）
        self.world_keys = {}        # フォルダ名 -> 世界の鍵
        self.world_files = {}       # 世界の鍵 -> 読んだときの worlds/<世界>.json
        self.world_playlists = {}
        self.world_saved = {}
        self.cleared = set()        # 覚えた曲を消す (鍵, 群, 場所)
        self.reload()

    @property
    def default_weight(self):
        return self.settings.get("DEFAULT_WEIGHT", DEFAULT_WEIGHT)

    def reload(self):
        self.settings = load_settings(self.root)
        self.saved_settings = dict(self.settings)
        self.file = load_json(self.playlist_path)
        stored = self.file.get("playlists")
        if not isinstance(stored, dict):
            stored = {}
        self.pool = scan(self.asset_dir, self.state_dir)
        self.playlists = {}
        for category in CATEGORY_KEYS:
            playlist = stored.get(category)
            playlist = playlist if isinstance(playlist, dict) else {}
            # 見つけただけの曲は 0（左の一覧に出るだけ）。「使う」で初めて重みが付く。
            self.playlists[category] = dict((key, weight_of(playlist, key)) for key in self.pool)
        self.saved = self.snapshot()
        self.worlds = list_worlds(self.data_root)
        self.places = {}
        self.world_keys = {}
        self.world_files = {}
        self.world_playlists = {}
        self.world_saved = {}
        self.cleared = set()

    # -- 一括設定 ---------------------------------------------------------

    def snapshot(self):
        return dict((category, dict((k, w) for k, w in sorted(playlist.items()) if w > 0))
                    for category, playlist in self.playlists.items())

    def world_snapshot(self):
        return dict((target, dict((k, w) for k, w in sorted(playlist.items()) if w > 0))
                    for target, playlist in self.world_playlists.items())

    def dirty(self):
        return (self.snapshot() != self.saved or self.settings != self.saved_settings
                or self.world_snapshot() != self.world_saved or bool(self.cleared))

    def used(self, category):
        return sorted(k for k, w in self.playlists.get(category, {}).items() if w > 0)

    def folders(self):
        return sorted(set(folder_of(key) for key in self.pool))

    def to_json(self):
        """書き出す全体。ファイルにあって曲が無い行は残す。知らない項目も残す。"""
        stored = self.file.get("playlists")
        merged = dict(stored) if isinstance(stored, dict) else {}
        for category in CATEGORY_KEYS:
            existing = merged.get(category)
            existing = existing if isinstance(existing, dict) else {}
            out = dict((k, v) for k, v in existing.items() if k not in self.pool)
            out.update((k, w) for k, w in self.playlists[category].items() if w > 0)
            merged[category] = dict(sorted(out.items()))
        # `_help` は毎回いまの文に置き換える（古い版の説明が残ると意味がずれる）。
        return {"_help": list(PLAYLIST_HELP), "playlists": merged}

    def shares(self, weights):
        """{鍵: 確率(%)}。重みの合計に対する割合。"""
        total = sum(weights.values())
        if total <= 0:
            return dict((key, 0.0) for key in weights)
        return dict((key, 100.0 * w / total) for key, w in weights.items())

    # -- ワールド個別設定 -------------------------------------------------

    def world_path(self, key):
        return os.path.join(self.worlds_dir, world_filename(self.root, key))

    def open_world(self, folder):
        """世界を開く。セーブを読み、`worlds/<世界>.json` の指定を載せる。戻りは places（読めなければ None）。"""
        if folder in self.places:
            return self.places[folder]
        places = places_of(read_save(folder, self.data_root)) if os.path.isfile(save_path(folder, self.data_root)) else None
        if places is not None and not places["areas"]:
            places = None
        self.places[folder] = places
        if places is None:
            return None
        key = places["name"] or folder
        self.world_keys[folder] = key
        data = load_json(self.world_path(key))
        self.world_files[key] = data
        for group in ("areas", "facilities"):
            entries = data.get(group) if isinstance(data.get(group), dict) else {}
            for place, entry in entries.items():
                playlist = entry.get("playlist") if isinstance(entry, dict) else None
                self.world_weights(key, group, place, playlist if isinstance(playlist, dict) else {})
        return places

    def world_weights(self, key, group, place, stored=None):
        """その場所の {鍵: 重み}。無ければ作る（全曲 0）。"""
        target = (key, group, place)
        weights = self.world_playlists.get(target)
        if weights is None:
            weights = dict((k, weight_of(stored, k)) for k in self.pool)
            self.world_playlists[target] = weights
            self.world_saved[target] = dict((k, w) for k, w in sorted(weights.items()) if w > 0)
        return weights

    def world_used(self, key, group, place):
        return sorted(k for k, w in self.world_playlists.get((key, group, place), {}).items() if w > 0)

    def chosen_of(self, key, group, place):
        """本体が覚えた曲の鍵。消す予定なら空。"""
        if (key, group, place) in self.cleared:
            return ""
        entry = ((self.world_files.get(key) or {}).get(group) or {}).get(place)
        chosen = entry.get("chosen") if isinstance(entry, dict) else None
        track = chosen.get("track") if isinstance(chosen, dict) else None
        return track if isinstance(track, str) else ""

    def clear_chosen(self, key, group, place):
        if self.chosen_of(key, group, place):
            self.cleared.add((key, group, place))
            return True
        return False

    def name_of_place(self, folder, group, place):
        places = self.places.get(folder) or {"areas": []}
        for area in places["areas"]:
            if group == "areas" and area["id"] == place:
                return area["name"]
            if group == "facilities":
                for facility in area["facilities"]:
                    if area["id"] + "/" + facility["id"] == place:
                        return facility["name"]
        return ""

    def to_world_json(self, key):
        """1世界ぶんの書き出し。本体が書いた `chosen` を保つため、直前に読み直す。"""
        data = load_json(self.world_path(key))
        folder = next((f for f, k in self.world_keys.items() if k == key), "")
        for group in ("areas", "facilities"):
            entries = data.get(group)
            if not isinstance(entries, dict):
                entries = {}
                data[group] = entries
            for (wkey, g, place), weights in self.world_playlists.items():
                if wkey != key or g != group:
                    continue
                entry = entries.get(place)
                if not isinstance(entry, dict):
                    entry = {}
                existing = entry.get("playlist") if isinstance(entry.get("playlist"), dict) else {}
                out = dict((k, v) for k, v in existing.items() if k not in self.pool)
                out.update((k, w) for k, w in weights.items() if w > 0)
                if out:
                    entry["playlist"] = dict(sorted(out.items()))
                else:
                    entry.pop("playlist", None)
                if (key, group, place) in self.cleared:
                    entry.pop("chosen", None)
                name = self.name_of_place(folder, group, place)
                if name:
                    entry["name"] = name
                if entry.get("playlist") or entry.get("chosen"):
                    entries[place] = entry
                else:
                    entries.pop(place, None)
            data[group] = dict(sorted(entries.items(), key=lambda item: place_sort_key(item[0])))
        ordered = {"areas": data.pop("areas"), "facilities": data.pop("facilities")}
        ordered.update(data)
        return ordered

    def save(self):
        """playlist.json・mod_settings.json・開いた世界の worlds/<世界>.json。どれかが書けなければ False。"""
        data = self.to_json()
        if not write_json(self.root, self.playlist_path, data):
            return False
        self.file = data
        self.saved = self.snapshot()
        for key in sorted(set(k for k, _g, _p in self.world_playlists)):
            world = self.to_world_json(key)
            if not write_json(self.root, self.world_path(key), world):
                return False
            self.world_files[key] = world
        self.world_saved = self.world_snapshot()
        self.cleared = set()
        if self.settings != self.saved_settings:
            if not save_settings(self.root, self.settings):
                return False
            self.saved_settings = dict(self.settings)
        return True


# ----------------------------------------------------------------- 画面
def build_window(model):
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("街・施設BGMの選曲")
    root.minsize(1000, 640)
    remembered = load_window(model.root)
    root.geometry(remembered.get("geometry") or "1380x860")
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
    ttk.Label(outer, text="街・施設BGMの選曲", style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel",
              text="施設の種類と土地の種類ごとに、鳴らす曲と重みを決める。重みは比率で、"
                   "同じ種類の合計に対する割合が確率になる。施設に候補が無ければ土地の曲、"
                   "土地にも無ければゲームの曲").pack(anchor="w", pady=(0, 6))

    places = ttk.Frame(outer)
    places.pack(fill="x")

    def place_row(label, path, ok, parent=places):
        # 「開く」はボタンではなくリンク風のラベル（行の高さを文字1行分に抑える）。
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=0)
        ttk.Label(row, text=label, width=14, style="Group.TLabel").pack(side="left")
        # 「開く」を先に詰める。後に詰めるとパスの長さに押し出されて、窓が狭いとき見えなくなる。
        link = ttk.Label(row, text="開く", style="Sub.TLabel", cursor="hand2")
        link.pack(side="right", padx=(8, 0))
        ttk.Label(row, text=path or "（未設定）",
                  style="TLabel" if ok else "Warn.TLabel").pack(side="left", fill="x", expand=True)

        def open_folder(_event=None):
            target = path if os.path.isdir(path) else os.path.dirname(path)
            if target and os.path.isdir(target):
                os.startfile(target)
        link.bind("<Button-1>", open_folder)

    place_row("ゲームの曲", model.asset_dir, os.path.isdir(model.asset_dir))
    place_row("足した曲", model.state_dir, os.path.isdir(model.state_dir))
    place_row("設定ファイル", model.playlist_path, os.path.isfile(model.playlist_path))

    # --- MOD の設定（mod.json の "settings"）。
    ttk.Separator(outer).pack(fill="x", pady=6)
    opts = ttk.Frame(outer)
    opts.pack(fill="x")
    ttk.Label(opts, text="「使う」に入れたときの重み", style="Group.TLabel").pack(side="left")
    default_var = tk.StringVar(value=str(model.default_weight))
    ttk.Spinbox(opts, from_=0, to=1000, width=6, textvariable=default_var).pack(
        side="left", padx=(6, 18))
    bool_vars = {}
    for key, label in BOOL_SETTINGS:
        var = tk.BooleanVar(value=bool(model.settings.get(key, SETTING_DEFAULTS[key])))
        bool_vars[key] = var
        ttk.Checkbutton(opts, text=label, variable=var).pack(side="left", padx=(0, 14))

    def on_default(*_a):
        try:
            model.settings["DEFAULT_WEIGHT"] = max(0, int(float(default_var.get() or 0)))
        except ValueError:
            return
        update_title()

    def on_bool(key):
        def handler(*_a):
            model.settings[key] = bool(bool_vars[key].get())
            update_title()
        return handler

    default_var.trace_add("write", on_default)
    for key in bool_vars:
        bool_vars[key].trace_add("write", on_bool(key))

    ttk.Separator(outer).pack(fill="x", pady=6)

    # --- 下段のボタン。一覧より先に詰める（窓が低いときに押し出されないように）。
    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    def update_title():
        root.title("街・施設BGMの選曲" + ("（未保存）" if model.dirty() else ""))

    # --- 左: タブ（一括設定 / ワールド個別設定）、右: 選んだ場所の選曲
    main = ttk.PanedWindow(outer, orient="horizontal")
    main.pack(fill="both", expand=True)

    tabs = ttk.Notebook(main)
    main.add(tabs, weight=1)

    # ---- 一括設定
    bulk = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(bulk, text="  一括設定  ")
    ttk.Label(bulk, text="場所の種類ごと。どの世界でも効く", style="Sub.TLabel").pack(anchor="w", pady=(0, 4))
    kinds = ttk.Treeview(bulk, columns=("count",), show="tree headings", selectmode="browse")
    kinds.heading("#0", text="種類")
    kinds.heading("count", text="使う曲")
    kinds.column("#0", width=150, stretch=True)
    kinds.column("count", width=48, anchor="e", stretch=False)
    kinds.pack(fill="both", expand=True)
    for group, label, rows in GROUPS:
        kinds.insert("", "end", iid="group:" + group, text=label, open=True, values=("",))
        for key, name, _note in rows:
            kinds.insert("group:" + group, "end", iid=key, text=name, values=("",))

    # ---- ワールド個別設定
    per_world = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(per_world, text="  ワールド個別設定  ")
    ttk.Label(per_world, text="その土地・その施設だけの曲。一括設定より先に見る", style="Sub.TLabel").pack(
        anchor="w", pady=(0, 4))
    world_bottom = ttk.Frame(per_world)
    world_bottom.pack(side="bottom", fill="x", pady=(6, 0))
    world_tree = ttk.Treeview(per_world, columns=("count",), show="tree headings", selectmode="browse")
    world_tree.heading("#0", text="世界 / 土地 / 施設")
    world_tree.heading("count", text="使う曲")
    world_tree.column("#0", width=200, stretch=True)
    world_tree.column("count", width=48, anchor="e", stretch=False)
    world_scroll = ttk.Scrollbar(per_world, orient="vertical", command=world_tree.yview)
    world_tree.configure(yscrollcommand=world_scroll.set)
    world_scroll.pack(side="right", fill="y")
    world_tree.pack(fill="both", expand=True)
    world_status = ttk.Label(world_bottom, style="Faint.TLabel")
    world_status.pack(side="left", fill="x", expand=True)
    forget_button = ttk.Button(world_bottom, text="覚えた曲を消す", state="disabled")
    forget_button.pack(side="right")

    #: 世界の木の行の id。"w:<フォルダ>" / "a:<フォルダ>|<土地id>" / "f:<フォルダ>|<土地id>/<施設id>" / "d:<フォルダ>"
    LOADING = "…"

    def fill_worlds():
        world_tree.delete(*world_tree.get_children())
        for folder in model.worlds:
            iid = "w:" + folder
            world_tree.insert("", "end", iid=iid, text=folder, values=("",))
            world_tree.insert(iid, "end", iid=iid + "|" + LOADING, text=LOADING)
        world_status.configure(text="{} 世界（{}）".format(len(model.worlds), saves_dir(model.data_root))
                               if model.worlds else "世界が見つからない: " + saves_dir(model.data_root))

    def world_target_of(iid):
        """行の id から (フォルダ, 群, 場所)。世界の行やダンジョンの束は None。"""
        if not iid or ":" not in iid or "|" not in iid:
            return None
        kind, rest = iid.split(":", 1)
        folder, place = rest.split("|", 1)
        if kind == "a":
            return folder, "areas", place
        if kind == "f":
            return folder, "facilities", place
        return None

    def populate_world(folder):
        iid = "w:" + folder
        for child in world_tree.get_children(iid):
            world_tree.delete(child)
        places = model.open_world(folder)
        if places is None:
            world_tree.insert(iid, "end", iid=iid + "|" + "読めない", text="（セーブを読めない）")
            return
        key = model.world_keys[folder]
        dungeons = [a for a in places["areas"] if a["size"] == "dungeon"]
        for area in places["areas"]:
            if area["size"] == "dungeon":
                continue
            insert_area(iid, folder, area)
        if dungeons:
            bundle = "d:" + folder
            world_tree.insert(iid, "end", iid=bundle, text="ダンジョン（{}）".format(len(dungeons)), values=("",))
            for area in dungeons:
                insert_area(bundle, folder, area)
        if key != folder:
            world_tree.item(iid, text="{}（{}）".format(folder, key))
        refresh_world_rows()

    def insert_area(parent, folder, area):
        aid = "a:" + folder + "|" + area["id"]
        text = "{}（{}）".format(area["name"] or "土地 " + area["id"], SIZE_LABEL.get(area["size"], area["size"] or "?"))
        world_tree.insert(parent, "end", iid=aid, text=text, values=("",), open=area["size"] != "dungeon")
        for facility in area["facilities"]:
            fid = "f:" + folder + "|" + area["id"] + "/" + facility["id"]
            label = CATEGORY_LABEL.get(facility["type"], facility["type"] or "?")
            world_tree.insert(aid, "end", iid=fid, text="{}「{}」".format(label, facility["name"]), values=("",))

    def refresh_world_rows():
        for (key, group, place), weights in model.world_playlists.items():
            folder = next((f for f, k in model.world_keys.items() if k == key), None)
            if folder is None:
                continue
            iid = ("a:" if group == "areas" else "f:") + folder + "|" + place
            if world_tree.exists(iid):
                used = sum(1 for w in weights.values() if w > 0)
                world_tree.set(iid, "count", str(used) if used else "")

    def on_world_open(_event=None):
        iid = world_tree.focus()
        if iid.startswith("w:") and "|" not in iid:
            children = world_tree.get_children(iid)
            if len(children) == 1 and children[0].endswith("|" + LOADING):
                populate_world(iid[2:])

    fill_worlds()

    # ---- 右: 選曲
    editor = ttk.Frame(main, padding=(8, 0, 0, 0))
    main.add(editor, weight=4)

    head = ttk.Label(editor, text="左で場所を選ぶ", style="Sub.TLabel")
    head.pack(anchor="w", pady=(0, 6))

    bottom = ttk.Frame(editor)
    bottom.pack(side="bottom", fill="x", pady=(8, 0))

    panes = ttk.PanedWindow(editor, orient="horizontal")
    panes.pack(fill="both", expand=True)

    #: いま編集している場所。None / ("category", 鍵) / ("world", 世界の鍵, 群, 場所, 見出し)
    current = {"target": None, "quiet": False, "remember": {}}
    where_choices = [ANY] + [WHERE_LABEL[k] for k in ("assets", "state")]

    def make_list(parent, columns, headings, widths, with_weight):
        box = ttk.Frame(parent)
        # 絞り込みの欄は2行。1行に並べると右の欄が狭いときに置き場と重みが切れる。
        bar = ttk.Frame(box)
        bar.pack(fill="x", pady=(0, 2))
        ttk.Label(bar, text="曲名", style="Group.TLabel").pack(side="left")
        name_var = tk.StringVar()
        ttk.Entry(bar, textvariable=name_var, width=14).pack(side="left", padx=(4, 8))
        ttk.Label(bar, text="フォルダ", style="Group.TLabel").pack(side="left")
        folder_var = tk.StringVar(value=ANY)
        folder_box = ttk.Combobox(bar, textvariable=folder_var, values=[ANY] + model.folders(),
                                  state="readonly", width=14)
        folder_box.pack(side="left", padx=(4, 0))
        bar2 = ttk.Frame(box)
        bar2.pack(fill="x", pady=(0, 4))
        ttk.Label(bar2, text="置き場", style="Group.TLabel").pack(side="left")
        where_var = tk.StringVar(value=ANY)
        ttk.Combobox(bar2, textvariable=where_var, values=where_choices,
                     state="readonly", width=13).pack(side="left", padx=(4, 8))
        min_var = tk.StringVar()
        if with_weight:
            ttk.Label(bar2, text="重み ≧", style="Group.TLabel").pack(side="left")
            ttk.Spinbox(bar2, from_=0, to=1000, width=5, textvariable=min_var).pack(
                side="left", padx=(4, 8))
        shown = ttk.Label(bar2, style="Faint.TLabel")
        shown.pack(side="right")

        tree = ttk.Treeview(box, columns=columns, show="headings", selectmode="browse")
        for col, heading, (width, anchor, stretch) in zip(columns, headings, widths):
            tree.heading(col, text=heading)
            tree.column(col, width=width, anchor=anchor, stretch=stretch)
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set, height=8)
        scroll.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        for var in (name_var, folder_var, where_var, min_var):
            var.trace_add("write", lambda *_a: refresh())
        return box, {"tree": tree, "name": name_var, "folder": folder_var, "folder_box": folder_box,
                     "where": where_var, "min": min_var, "shown": shown}

    pool_box, pool = make_list(
        panes, ("name", "folder", "where"), ("曲名", "フォルダ", "置き場"),
        ((220, "w", True), (130, "w", False), (110, "w", False)), False)
    used_box, used = make_list(
        panes, ("name", "folder", "where", "weight", "share"),
        ("曲名", "フォルダ", "置き場", "重み", "確率"),
        ((200, "w", True), (120, "w", False), (110, "w", False), (56, "e", False), (56, "e", False)), True)
    panes.add(pool_box, weight=1)
    panes.add(used_box, weight=1)

    ttk.Button(bottom, text="全て使う", command=lambda: set_all(None)).pack(side="left")
    ttk.Button(bottom, text="使う >>", width=8,
               command=lambda: move(pool["tree"].focus(), True)).pack(side="left", padx=(6, 12))
    ttk.Button(bottom, text="全て外す", command=lambda: set_all(0)).pack(side="right")
    ttk.Button(bottom, text="<< 外す", width=8,
               command=lambda: move(used["tree"].focus(), False)).pack(side="right", padx=(6, 6))
    weight_var = tk.StringVar()
    spin = ttk.Spinbox(bottom, from_=0, to=1000, width=6, textvariable=weight_var)
    spin.pack(side="right", padx=(6, 12))
    ttk.Label(bottom, text="選んだ曲の重み").pack(side="right")
    summary = ttk.Label(bottom, style="Faint.TLabel")
    summary.pack(side="left", fill="x", expand=True)

    def target():
        return current["target"]

    def weights():
        """いま編集している場所の {鍵: 重み}。場所が無ければ空。"""
        t = target()
        if not t:
            return {}
        if t[0] == "category":
            return model.playlists[t[1]]
        return model.world_weights(t[1], t[2], t[3])

    def filter_of(part):
        try:
            min_weight = int(float(part["min"].get() or 0))
        except ValueError:
            min_weight = 0
        return part["name"].get().strip(), part["folder"].get(), part["where"].get(), min_weight

    def visible(part):
        """その一覧の絞り込みに掛かる鍵。"""
        name_f, folder_f, where_f, min_w = filter_of(part)
        current_weights = weights()
        return [key for key in sorted(model.pool)
                if matches(key, model.pool[key], current_weights.get(key, 0), name_f, folder_f, where_f, min_w)]

    def remembered_weight(key):
        return current["remember"].get((target(), key)) or model.default_weight or DEFAULT_WEIGHT

    def move(key, use):
        """右へ入れる（use=True）／右から外す。外した重みは覚えておき、戻すと元に戻る。"""
        current_weights = weights()
        if not current_weights or not key or key not in model.pool:
            return
        if use:
            if current_weights[key] <= 0:
                current_weights[key] = remembered_weight(key)
        elif current_weights[key] > 0:
            current["remember"][(target(), key)] = current_weights[key]
            current_weights[key] = 0
        refresh(keep=key if use else None)

    def set_all(value):
        """表示中の曲だけに効く（絞り込んでから押せば、その範囲だけ入り切りできる）。"""
        current_weights = weights()
        if not current_weights:
            return
        for key in visible(pool if value is None else used):
            if value == 0:
                if current_weights[key] > 0:
                    current["remember"][(target(), key)] = current_weights[key]
                current_weights[key] = 0
            elif current_weights[key] <= 0:
                current_weights[key] = remembered_weight(key)
        refresh()

    def refresh_kinds():
        for key in CATEGORY_KEYS:
            kinds.set(key, "count", str(len(model.used(key))) if model.used(key) else "")

    def refresh(keep=None):
        t = target()
        pool["tree"].delete(*pool["tree"].get_children())
        used["tree"].delete(*used["tree"].get_children())
        refresh_kinds()
        refresh_world_rows()
        if not t:
            head.configure(text="左で場所を選ぶ")
            summary.configure(text="")
            pool["shown"].configure(text="")
            used["shown"].configure(text="")
            forget_button.configure(state="disabled")
            update_title()
            return
        if t[0] == "category":
            note = CATEGORY_NOTE.get(t[1]) or ""
            head.configure(text="{}{}".format(CATEGORY_LABEL.get(t[1], t[1]), "　" + note if note else ""))
            forget_button.configure(state="disabled")
        else:
            chosen = model.chosen_of(t[1], t[2], t[3])
            head.configure(text="{}　この場所だけの曲。使う曲が無ければ一括設定の段へ落ちる{}".format(
                t[4], "。覚えた曲: " + name_of(chosen) if chosen else ""))
            forget_button.configure(state="normal" if chosen else "disabled")
        current_weights = weights()
        shares = model.shares(current_weights)
        pool_keys = visible(pool)
        used_all = sorted(k for k, w in current_weights.items() if w > 0)
        used_keys = [k for k in visible(used) if current_weights.get(k, 0) > 0]
        for key in pool_keys:
            pool["tree"].insert("", "end", iid=key, values=(
                name_of(key), folder_of(key), WHERE_LABEL.get(model.pool[key], model.pool[key])),
                tags=("on" if current_weights.get(key, 0) > 0 else "off",))
        for key in used_keys:
            used["tree"].insert("", "end", iid=key, values=(
                name_of(key), folder_of(key), WHERE_LABEL.get(model.pool[key], model.pool[key]),
                current_weights[key], "{:.0f}%".format(shares[key])))
        # 右に入っている曲は左では薄く出す（同じ曲が2度見えても迷わないように）。
        pool["tree"].tag_configure("on", foreground="#646b76")
        pool["shown"].configure(text="{} / {} 曲".format(len(pool_keys), len(model.pool)))
        used["shown"].configure(text="{} / {} 曲".format(len(used_keys), len(used_all)))
        total = sum(current_weights.values())
        if not model.pool:
            text = "曲が見つからない。上の2つのフォルダに .mp3 / .ogg / .wav を置く"
        elif not used_all:
            if t[0] == "world":
                text = "使う曲が無い。一括設定の段（{}）が使われる".format("土地の種類" if t[2] == "areas" else "施設の種類")
            elif t[1].startswith("area:"):
                text = "使う曲が無い。この種類の土地では土地に焼き付いた曲が鳴る"
            else:
                text = "使う曲が無い。この種類の施設では土地の曲がそのまま続く"
        else:
            text = "{} 曲中 {} 曲を使う。重みの合計 {}".format(len(model.pool), len(used_all), total)
        summary.configure(text=text)
        if keep and used["tree"].exists(keep):
            used["tree"].selection_set(keep)
            used["tree"].focus(keep)
        else:
            current["quiet"] = True
            weight_var.set("")
            current["quiet"] = False
        update_title()

    def on_kind(_event=None):
        item = kinds.focus()
        current["target"] = ("category", item) if item in CATEGORY_KEYS else None
        refresh()

    def on_world_select(_event=None):
        iid = world_tree.focus()
        found = world_target_of(iid)
        if found is None:
            current["target"] = None
        else:
            folder, group, place = found
            key = model.world_keys.get(folder)
            if key is None:
                current["target"] = None
            else:
                current["target"] = ("world", key, group, place, world_tree.item(iid, "text"))
        refresh()

    def on_tab(_event=None):
        if tabs.index(tabs.select()) == 0:
            on_kind()
        else:
            on_world_select()

    def forget():
        t = target()
        if t and t[0] == "world" and model.clear_chosen(t[1], t[2], t[3]):
            refresh()

    forget_button.configure(command=forget)

    def on_select(_event):
        key = used["tree"].focus()
        if key and key in model.pool and target():
            current["quiet"] = True
            weight_var.set(str(weights()[key]))
            current["quiet"] = False

    def on_weight(*_args):
        if current["quiet"] or not target():
            return
        key = used["tree"].focus()
        if not key or key not in model.pool:
            return
        try:
            value = max(0, int(float(weight_var.get() or 0)))
        except ValueError:
            return
        current_weights = weights()
        if current_weights[key] != value:
            current_weights[key] = value
            refresh(keep=key)

    kinds.bind("<<TreeviewSelect>>", on_kind)
    world_tree.bind("<<TreeviewSelect>>", on_world_select)
    world_tree.bind("<<TreeviewOpen>>", on_world_open)
    tabs.bind("<<NotebookTabChanged>>", on_tab)
    pool["tree"].bind("<Double-1>", lambda e: move(pool["tree"].identify_row(e.y), True))
    used["tree"].bind("<Double-1>", lambda e: move(used["tree"].identify_row(e.y), False))
    used["tree"].bind("<<TreeviewSelect>>", on_select)
    weight_var.trace_add("write", on_weight)
    spin.bind("<Return>", on_weight)

    status = ttk.Label(footer, style="Faint.TLabel",
                       text="保存すると次の移動から効く（ゲームを起動したままでよい）")
    status.pack(side="left")

    def rescan():
        if model.dirty() and not messagebox.askyesno(
                "再走査", "未保存の変更があります。破棄して読み直しますか？", parent=root):
            return
        model.reload()
        default_var.set(str(model.default_weight))
        for key, var in bool_vars.items():
            var.set(bool(model.settings.get(key, SETTING_DEFAULTS[key])))
        for part in (pool, used):
            part["folder_box"].configure(values=[ANY] + model.folders())
        fill_worlds()
        current["target"] = None
        on_tab()
        status.configure(text="読み直しました（{} 曲）".format(len(model.pool)))

    def save():
        if model.save():
            refresh()
            status.configure(text="保存しました {}  {}".format(
                time.strftime("%H:%M:%S"), model.playlist_path))
        else:
            messagebox.showerror("保存に失敗しました",
                                 "{} か worlds\\ に書けませんでした。".format(model.playlist_path), parent=root)

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
    ttk.Button(footer, text="保存", style="Accent.TButton", command=save).pack(side="right", padx=(0, 6))
    ttk.Button(footer, text="再走査", command=rescan).pack(side="right", padx=(0, 6))
    root.protocol("WM_DELETE_WINDOW", close)

    # 最初は宿屋を出しておく（いちばん使われる種類）。
    kinds.selection_set("inn")
    kinds.focus("inn")
    on_kind()

    # 左右の仕切り。2つのタブは同じ Notebook なので、放っておくと幅の広いほうの木に
    # 引きずられて種類の一覧まで広くなる。左は一覧が読める幅に留め、残りを選曲欄へ。
    # 窓が実寸になる前に置くと 0 に丸められて左が畳まれるので、実寸になった最初の
    # `<Configure>` で1回だけ置く。
    sash = {"id": None}

    def place_sash(_event=None):
        if main.winfo_width() < LEFT_WIDTH * 2:
            return
        try:
            main.sashpos(0, LEFT_WIDTH)
        except Exception:
            pass
        if sash["id"]:
            main.unbind("<Configure>", sash["id"])
            sash["id"] = None

    sash["id"] = main.bind("<Configure>", place_sash, add="+")

    if not model.pool:
        messagebox.showinfo("街・施設BGMの選曲",
                            "曲が1つも見つからない。\n\n{}\n{}\n\nに .mp3 / .ogg / .wav を置いて「再走査」".format(
                                model.asset_dir or "（ゲームの場所が未設定）", model.state_dir),
                            parent=root)
    return root


def dump(model):
    print("assets  : {}".format(model.asset_dir))
    print("state   : {}".format(model.state_dir))
    print("playlist: {} ({})".format(model.playlist_path,
                                      "exists" if os.path.isfile(model.playlist_path) else "missing"))
    print("saves   : {} ({} world(s))".format(saves_dir(model.data_root), len(model.worlds)))
    print("pool    : {} track(s)".format(len(model.pool)))
    print()
    for key in sorted(model.pool):
        print("  {:<8} {}".format(model.pool[key], key))
    print()
    for category in CATEGORY_KEYS:
        shares = model.shares(model.playlists[category])
        used = ", ".join("{} {:.0f}%".format(k, shares[k]) for k in model.used(category))
        print("{:<22} {}: {}".format(category, CATEGORY_LABEL.get(category, ""),
                                     used or "(なし)"))
    for folder in model.worlds:
        places = model.open_world(folder)
        if places is None:
            print("\n{}: (セーブを読めない)".format(folder))
            continue
        key = model.world_keys[folder]
        settlements = [a for a in places["areas"] if a["size"] != "dungeon"]
        print("\n{}: {} 土地（うちダンジョン {}）".format(
            folder, len(places["areas"]), len(places["areas"]) - len(settlements)))
        for area in settlements:
            used = model.world_used(key, "areas", area["id"])
            chosen = model.chosen_of(key, "areas", area["id"])
            print("  {:<4} {}（{}） {}{}".format(
                area["id"], area["name"], SIZE_LABEL.get(area["size"], area["size"]),
                ", ".join(used) if used else "", " 覚えた曲: " + chosen if chosen else ""))
            for facility in area["facilities"]:
                place = area["id"] + "/" + facility["id"]
                used = model.world_used(key, "facilities", place)
                if used:
                    print("       {} {}「{}」 {}".format(place, CATEGORY_LABEL.get(facility["type"], facility["type"]),
                                                       facility["name"], ", ".join(used)))


def main(argv):
    model = Model()
    if "--dump" in argv:
        dump(model)
        return 0
    build_window(model).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
