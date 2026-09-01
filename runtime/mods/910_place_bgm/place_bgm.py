# -*- coding: utf-8 -*-
"""戦闘以外の BGM を、置いてある曲から場所の種類ごとの重みで選んで鳴らす。

何が問題か
----------
素のゲームの BGM は土地に1曲ずつ焼き付いている（`areas[id]["bgm"]`。GAME.md §2.11）。
施設に入っても曲は変わらず、宿屋もギルドも店も同じ土地の曲が鳴り続ける。
土地の曲を変えるにはセーブを書き換えるしかなく（`104_` はそれを新しい土地に限って行う）、
曲を足したり比率を付けたりする手段は無い。

何をするか
----------
曲を鳴らす口はプロセス内に1つしかない（GAME.md §2.11）。

    scripts.sounds:SoundManager.play_music_from_src(self, app, music_src)

`322_battle_bgm` が戦闘曲でやっているのと同じく、ここを包んで
**どの曲を鳴らすかだけを差し替える**。戦闘曲（`/musics/battle/`）は触らない（`322_` の領分）。

戦闘曲と違うのは「いつ鳴らすか」も要ること。
ゲームが土地の曲を鳴らすのは、ロード直後・土地の移動・戦闘の終わり・依頼の終わりの4経路で
（`out/battle_bgm.log` の `caller`。DOC.md §3）、**施設の出入りでは鳴らさない**。
施設ごとの曲を鳴らすには、施設に入った瞬間を自分で捕まえて鳴らし直す必要がある。

    __main__:MovePhaseManager.move_phase   復帰後が「着いた瞬間」（GAME.md §2.6）

これに加えて、1秒ごとの見張りで「場所が変わったのに曲が合っていない」を拾う
（依頼から戻った・ロードした直後など、`move_phase` を通らない経路の取りこぼし用）。
見張りは場所が変わったときにしか動かないので、普段は比較1回で済む。
どちらの経路で切り替わったかはログの `by` に残す（見張りが要るかどうかは実機で数える。DOC.md §4）。

どの曲を鳴らすかの決め方
------------------------
いま居る場所から、上から順に「候補が1曲でもある段」を探し、その段の重みで1曲選ぶ。

    1. 世界ファイルの施設 id      state/musics/place/worlds/<世界>.json の facilities["<土地id>/<施設id>"]
    2. 施設の種類                playlist.json の "inn" / "guild" / "general_store" ...
    3. 世界ファイルの土地 id      同 areas["<土地id>"]
    4. 土地の種類                playlist.json の "area:town" / "area:village" / "area:city" / "area:dungeon"
    5. どこにも無い              ゲームの曲（土地に焼き付いた曲）をそのまま

施設の段（1・2）が土地の段（3・4）より常に先。
ある町の曲を個別に決めていても、その町の宿に入れば宿の曲になる（他の町の宿と同じ体験）。
1 と 3 は「セーブ単位の個別指定」。設定画面の「ワールド個別設定」のタブが書く（DOC.md §5）。
施設の種類は `Facility.facility_type` そのもの（GAME.md §2.7）。
通路（`entrance` / `exit` / `ward` / `dungeon_location`）は施設の段を持たず、土地の曲になる。
土地の種類は `Area.size`（`town` / `village` / `city` / `dungeon`）で、
実行時のオブジェクトに無ければセーブの辞書、それも無ければゲームが渡してきたパスのフォルダ名から取る
（どれで取れたかはログに残す。`Area.size` が実行時に読めるかは未確認。DOC.md §4）。

同じ場所に居る間は鳴らし直さない。
場所は、土地の段なら土地 id、施設の段なら土地 id と施設 id。
宿に入れば宿の曲、通りへ出れば土地の曲に戻り、通りから通りへ歩いても曲は続く。
どの段にも候補が無い土地では、施設を出入りしても曲に触らない（素のゲームと同じ）。

土地の曲は一度決めたら覚える（`AREA_STICKY`）。
素のゲームは土地に曲が付いているので、入るたびに変わるより、その土地の曲として定着するほうが自然。
覚える先は世界ファイルの `chosen`。
依頼で生成されるダンジョン（`size == "dungeon"`）は覚えない。
1回きりの土地で、覚えると依頼のたびに世界ファイルが増える（`104_` が均していた 107 土地のうち大半がこれ）。
施設は既定では覚えず、入るたびに選び直す（`FACILITY_STICKY` で変えられる）。

曲の置き場
----------
    <ゲーム>/Assets/sounds/musics/       素の曲（size/mood の2段と、直下の単発曲）。battle/ は除く
    <ローダ>/state/musics/place/          足したい曲を置く場所。下にフォルダを切ってよい

両方を再帰で走査し、`musics/` からの相対パス（`town/calm/曲.mp3`）を鍵にして1つのプールにする
（同じ鍵なら `state/` 側が勝つ）。
重みは `state/musics/place/playlist.json` に場所の種類ごと・曲ごとに持つ。

    {"playlists": {"inn":       {"宿.mp3": 100, "town/calm/Foo.mp3": 50},
                   "area:town": {"town/calm/Foo.mp3": 100, "town/lively/Bar.wav": 100}}}

重みは比率で、同じ種類の合計に対する割合がそのまま確率になる。
**置いただけでは鳴らない**。設定画面（`tool.py`）で「使う」に入れた曲だけが候補になる。
本体は決めるたびにこのファイルを読むので、ゲームを起動したまま保存しても次の移動から効く。

鳴らし方
--------
選んだ曲が `Assets/` 側なら、ゲームが渡してきたパスの `musics/` から後ろを差し替える
（ゲームが使っている相対パスの形をそのまま保つ）。
`state/` 側なら絶対パスをスラッシュ区切りで渡す（絶対パスが通ることは `322_` で確認済み）。
鳴らせなかった（例外）ときはログに残して素の曲を鳴らし直すので、曲の置き間違いでゲームが黙ることはない。

`106_fix_battle_bgm_restore` はこの MOD より内側で包んでいる（`after`）ので、
戦闘前の曲として控えるのはこの MOD が差し替えた後の曲になり、戦闘の後にそれが戻る。
戻る呼び出しは app でない物を渡してくる（GAME.md §2.11）ので、場所を読むときは走っている app を引き直す。

乱数は MOD 専用の `random.Random`（ゲームの乱数列をずらさない。GAME.md §2.11）。
pygame に触る（鳴っているかを見る・鳴らし直す）のは Kivy の Clock の上だけ（GAME.md §2.1）。
"""

import os
import random
import sys

from instantale_modloader import ui
from instantale_modloader.state import WorldStore, world_key

# GUI から変えられる値。mod.json の "settings" と同じ名前・同じ既定値。
DEFAULT_WEIGHT = 100     # 設定画面で「使う」に入れたときの重み（本体は読まない。宣言と既定値を揃えるためにある）
AVOID_REPEAT = True      # 候補が2曲以上あれば前回と同じ曲は選ばない（覚える場所には効かない）
AREA_STICKY = True       # 土地ごとに最初に選んだ曲を覚える（ダンジョンは除く）
FACILITY_STICKY = False  # 施設ごとに最初に選んだ曲を覚える

LOG_BASENAME = "place_bgm.log"
LOG_TAG = "[PLACEBGM]"

PLAYLIST_NAME = "playlist.json"
STATE_SUBDIR = ("musics", "place")
WORLDS_DIRNAME = "musics/place/worlds"      # `state/` からの相対。世界ごとの控え（個別指定と覚えた曲）
ASSET_SUBDIR = ("Assets", "sounds", "musics")
MUSIC_DIR_MARK = "/musics/"
BATTLE_DIR_MARK = "/musics/battle/"
BATTLE_FOLDER = "battle"
EXTENSIONS = (".mp3", ".ogg", ".wav")

# 施設の段を持つ `facility_type`（GAME.md §2.7 の実在する型から通路を除いたもの。
# `training_facility` と `free` は実セーブと §2.21 にある）。
FACILITY_CATEGORIES = (
    "inn", "guild", "general_store", "specialty_shop", "blacksmith",
    "medical_facility", "administrative_office", "underworld_office",
    "colosseum", "slave_market", "training_facility", "location", "free",
)
# 主のいない通路。施設の段を持たず、土地の曲になる。
PASSAGE_TYPES = ("entrance", "exit", "ward", "dungeon_location")

AREA_PREFIX = "area:"
AREA_SIZES = ("town", "village", "city", "dungeon")
SIZE_DUNGEON = "dungeon"
SIZE_ALIAS = {"dungeons": "dungeon"}        # フォルダは複数形、セーブは単数（GAME.md §2.11）
AREA_CATEGORIES = tuple(AREA_PREFIX + s for s in AREA_SIZES)
CATEGORIES = FACILITY_CATEGORIES + AREA_CATEGORIES

LEVEL_GAME = "game"                         # どの段にも候補が無い ＝ ゲームの曲
WORLD_AREA_PREFIX = "world:area:"
WORLD_FACILITY_PREFIX = "world:facility:"

# 覚える鍵の群（`memory` の前半）→ 世界ファイルの項目名。
MEMORY_GROUPS = {"area": "areas", "facility": "facilities"}

# 見張りの間隔（秒）。場所が変わったときだけ働く。0 で見張りなし。
POLL_INTERVAL = 1.0

BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")

# 世代をまたいで持つもの（再注入で曲を鳴らし直さないため）。
STORE_ATTR = "_instantale_place_bgm"

# 自分が出したパスの控えの上限。
MAX_ISSUED = 64

PLAYLIST_HELP = [
    "場所の BGM の重み。playlists → {場所の種類: {曲: 重み}}",
    "施設の種類: inn guild general_store specialty_shop blacksmith medical_facility administrative_office "
    "underworld_office colosseum slave_market training_facility location free",
    "土地の種類: area:town area:village area:city area:dungeon（施設の段に候補が無いときに使う）",
    "曲は Assets/sounds/musics（battle/ を除く）と state/musics/place の .mp3 / .ogg / .wav。"
    "鍵は musics/ からの相対パス（town/calm/曲.mp3）。同じ鍵なら state 側",
    "重みは比率。同じ種類の合計に対する割合が確率になる（合計 100 なら数字がそのままパーセント）",
    "0 か無ければその種類では鳴らない。どの段にも無ければゲームの曲",
    "土地・施設ごとの個別指定と覚えた曲は worlds/<世界>.json（DOC.md §5）",
]

_rng = random.Random()


# --------------------------------------------------------------- 純関数
def is_battle_track(src):
    """ゲームが渡してきたパスが戦闘曲か。`106_` / `322_` と同じ判定。"""
    if not isinstance(src, str) or not src:
        return False
    return BATTLE_DIR_MARK in ("/" + src.replace("\\", "/").lstrip("/")).lower()


def game_root():
    """ゲーム本体のフォルダ。`Assets/sounds/musics` が在る場所を探す（`322_` と同じ順）。"""
    seen = []
    for get in (os.getcwd,
                lambda: os.path.dirname(os.path.abspath(sys.executable)),
                lambda: sys.prefix):
        try:
            base = get()
        except Exception:
            continue
        if not base or base in seen:
            continue
        seen.append(base)
        if os.path.isdir(os.path.join(base, *ASSET_SUBDIR)):
            return base
    return None


def list_tracks(folder):
    """フォルダ以下の曲を再帰で集める。{相対パス(スラッシュ区切り): 絶対パス}。

    直下の `battle/` は戦闘曲の置き場なので除く（`106_` が `/musics/battle/` で戦闘曲と見なす）。
    """
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


def scan_tracks(asset_dir, state_dir):
    """{鍵: (絶対パス, 置き場)} を作る。同じ鍵なら `state` が勝つ。"""
    found = {}
    for folder, where in ((asset_dir, "assets"), (state_dir, "state")):
        for key, path in list_tracks(folder).items():
            found[key] = (path, where)
    return found


def coerce_weight(value):
    """重みを 0 以上の数にする。読めない値は 0。"""
    if isinstance(value, bool):
        return 100.0 if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number < 0:      # NaN / 負
        return 0.0
    return number


def candidates(playlist, found):
    """[(鍵, 重み)]。`playlist` は {鍵: 重み}。プールに実在し、重みが正のものだけ。"""
    if not isinstance(playlist, dict):
        return []
    out = []
    for key, value in playlist.items():
        if key not in found:
            continue
        weight = coerce_weight(value)
        if weight > 0:
            out.append((key, weight))
    out.sort(key=lambda item: item[0])
    return out


def pick(pool, last=None, avoid_repeat=True, rng=None):
    """重み付きで1曲選ぶ。`pool` は [(鍵, 重み)]。空なら None。"""
    rng = rng or _rng
    if not pool:
        return None
    if avoid_repeat and last is not None and len(pool) >= 2:
        pool = [item for item in pool if item[0] != last] or pool
    total = sum(w for _k, w in pool)
    roll = rng.random() * total
    for key, weight in pool:
        roll -= weight
        if roll < 0:
            return key
    return pool[-1][0]


def in_pool(key, pool):
    return key is not None and any(key == k for k, _w in pool)


def normalize_size(value):
    """`Area.size` を土地の種類に均す。読めなければ None。"""
    if not isinstance(value, str) or not value:
        return None
    size = SIZE_ALIAS.get(value.strip().lower(), value.strip().lower())
    return size if size in AREA_SIZES else None


def size_from_src(src):
    """ゲームが渡してきたパスのフォルダ名から土地の種類を取る。取れなければ None。"""
    if not isinstance(src, str):
        return None
    lowered = src.replace("\\", "/").lower()
    at = lowered.rfind(MUSIC_DIR_MARK)
    if at < 0:
        return None
    rest = lowered[at + len(MUSIC_DIR_MARK):].split("/")
    return normalize_size(rest[0]) if len(rest) >= 2 else None


def facility_category(facility_type):
    """施設の種類の段の鍵。通路と未知の型は None（土地の曲になる）。"""
    if not isinstance(facility_type, str):
        return None
    return facility_type if facility_type in FACILITY_CATEGORIES else None


def is_area_level(level):
    return isinstance(level, str) and (level.startswith(AREA_PREFIX)
                                       or level.startswith(WORLD_AREA_PREFIX))


def place_of(level, info):
    """その段で「同じ場所」と見なす単位。土地の段は土地、施設の段は土地と施設。"""
    place = "{}|{}".format(info.get("world") or "", info.get("area_id") or "")
    if is_area_level(level):
        return place
    return place + "/" + (info.get("facility_id") or "")


def level_keys(info, world_bucket, playlists,
               area_sticky=None, facility_sticky=None):
    """場所から、上から順に見る段の [(段の鍵, {曲: 重み}, 覚える鍵)] を組む。

    `info` は `context_of` の戻り。`world_bucket` は世界ファイルの中身（無ければ None）。
    覚える鍵は、その段で選んだ曲を世界ファイルに覚えるときの名前。覚えない段は None。
    ダンジョンは土地の段を覚えない（1回きりの土地。世界ファイルが依頼のたびに増える）。
    """
    area_sticky = AREA_STICKY if area_sticky is None else area_sticky
    facility_sticky = FACILITY_STICKY if facility_sticky is None else facility_sticky
    area_id = info.get("area_id") or ""
    facility_id = info.get("facility_id") or ""
    size = info.get("size")
    area_memory = ("area:" + area_id
                   if area_sticky and area_id and size != SIZE_DUNGEON else None)
    facility_memory = ("facility:" + area_id + "/" + facility_id
                       if facility_sticky and area_id and facility_id else None)
    levels = []
    bucket = world_bucket if isinstance(world_bucket, dict) else {}
    if area_id and facility_id:
        entry = (bucket.get("facilities") or {}).get(area_id + "/" + facility_id) or {}
        levels.append((WORLD_FACILITY_PREFIX + area_id + "/" + facility_id,
                       entry.get("playlist") if isinstance(entry, dict) else None,
                       facility_memory))
    category = facility_category(info.get("facility_type"))
    if category:
        levels.append(("facility:" + category, (playlists or {}).get(category), facility_memory))
    if area_id:
        entry = (bucket.get("areas") or {}).get(area_id) or {}
        levels.append((WORLD_AREA_PREFIX + area_id,
                       entry.get("playlist") if isinstance(entry, dict) else None,
                       area_memory))
    if size:
        levels.append((AREA_PREFIX + size, (playlists or {}).get(AREA_PREFIX + size), area_memory))
    return levels


def resolve(levels, found):
    """候補が1曲でもある最初の段。(段の鍵, [(鍵, 重み)], 覚える鍵)。無ければ (None, [], None)。"""
    for level, playlist, memory in levels:
        pool = candidates(playlist, found)
        if pool:
            return level, pool, memory
    return None, [], None


def rewrite_src(original_src, key, path, where):
    """鳴らすパスを組む。

    `assets` 側はゲームが渡してきたパスの `musics/` から後ろを差し替える
    （相対パスの形を保つ）。渡されたパスに `musics/` が無ければ既定の形。
    `state` 側は絶対パス。区切りはスラッシュ。
    """
    if where == "assets":
        src = original_src.replace("\\", "/") if isinstance(original_src, str) else ""
        at = src.lower().rfind(MUSIC_DIR_MARK)
        if at >= 0:
            return src[:at + len(MUSIC_DIR_MARK)] + key
        return "/".join(ASSET_SUBDIR) + "/" + key
    return os.path.abspath(path).replace("\\", "/")


def short(value):
    if not isinstance(value, str) or not value:
        return repr(value)
    parts = value.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else value


def _place_sort_key(key):
    return tuple(ui.id_sort_key(part) for part in str(key).split("/"))


def order_world(bucket):
    """世界ファイルの並びを固定する（土地 id 順、施設は土地 id / 施設 id 順）。"""
    out = {}
    for group in ("areas", "facilities"):
        entries = bucket.get(group) if isinstance(bucket, dict) else None
        if not isinstance(entries, dict):
            entries = {}
        out[group] = dict(sorted(entries.items(), key=lambda item: _place_sort_key(item[0])))
    for key, value in (bucket or {}).items():
        if key not in out:
            out[key] = value
    return out


def empty_world():
    return {"areas": {}, "facilities": {}}


def normalize_world(bucket):
    """世界ファイルの形を揃える。(控え, 直したか)。"""
    if not isinstance(bucket, dict):
        return empty_world(), True
    changed = False
    for group in ("areas", "facilities"):
        if not isinstance(bucket.get(group), dict):
            bucket[group] = {}
            changed = True
    return bucket, changed


def memory_entry(bucket, memory, create=False):
    """覚える鍵（`area:7` / `facility:7/106`）が指す世界ファイルの項目。無ければ None（`create` なら作る）。"""
    if not memory or ":" not in memory or not isinstance(bucket, dict):
        return None
    group, place = memory.split(":", 1)
    name = MEMORY_GROUPS.get(group)
    if name is None:
        return None
    entries = bucket.get(name)
    if not isinstance(entries, dict):
        if not create:
            return None
        entries = {}
        bucket[name] = entries
    entry = entries.get(place)
    if not isinstance(entry, dict):
        if not create:
            return None
        entry = {}
        entries[place] = entry
    return entry


# --------------------------------------------------------------- 場所を読む
def context_of(app, game_src=None):
    """いま居る場所。読めない項目は空。

    `player.location` / `player.current_area` はロード直後は id の文字列（GAME.md §2.7）なので、
    どちらでも引き当てる（`ui.current_area` / `ui.find_facility`）。
    """
    info = {"world": "", "area_id": "", "area_name": "", "size": None, "size_by": "",
            "facility_id": "", "facility_type": "", "facility_name": "", "area_bgm": None}
    player = getattr(app, "player", None)
    if player is None:
        return info
    try:
        info["world"] = world_key(app)
    except Exception:
        info["world"] = ""
    area = ui.current_area(app)
    area_id = ui.area_id_of(area)
    if not area_id:
        raw = getattr(player, "current_area", None)
        area_id = str(raw) if isinstance(raw, (str, int)) and str(raw) else ""
    info["area_id"] = area_id
    name = getattr(area, "name", None)
    info["area_name"] = name if isinstance(name, str) else ""
    bgm = getattr(area, "bgm", None)
    info["area_bgm"] = bgm if isinstance(bgm, str) and bgm else None
    info["size"], info["size_by"] = size_of(app, area, area_id, game_src or info["area_bgm"])

    location = getattr(player, "location", None)
    facility = location
    if isinstance(location, (str, int)):
        facility, _node = ui.find_facility(area, location)
        if facility is None:
            info["facility_id"] = str(location)
    if facility is not None:
        fid = getattr(facility, "id", None)
        if fid not in (None, ""):
            info["facility_id"] = str(fid)
        info["facility_type"] = ui.facility_type_of(facility)
        name = getattr(facility, "name", None)
        info["facility_name"] = name if isinstance(name, str) else ""
    return info


def size_of(app, area, area_id, src):
    """土地の種類と、どこから取れたか。(size, 'area' / 'save' / 'world' / 'src' / '')。"""
    size = normalize_size(getattr(area, "size", None))
    if size:
        return size, "area"
    for attr, label in (("save_data_dict", "save"), ("world_dict", "world")):
        container = getattr(app, attr, None)
        areas = container.get("areas") if isinstance(container, dict) else None
        if not isinstance(areas, dict) or not area_id:
            continue
        entry = areas.get(str(area_id))
        if entry is None:
            for key, value in areas.items():
                if str(key) == str(area_id):
                    entry = value
                    break
        size = normalize_size(entry.get("size")) if isinstance(entry, dict) else None
        if size:
            return size, label
    size = size_from_src(src)
    if size:
        return size, "src"
    return None, ""


def context_key(info):
    """場所が変わったかを見るための鍵。"""
    return (info.get("world"), info.get("area_id"), info.get("facility_id"),
            info.get("facility_type"), info.get("size"))


def describe(info):
    return "area {}{} ({}{}) fac {}{}{}".format(
        info.get("area_id") or "?",
        " " + info["area_name"] if info.get("area_name") else "",
        info.get("size") or "?",
        " by " + info["size_by"] if info.get("size_by") else "",
        info.get("facility_id") or "?",
        " " + info["facility_name"] if info.get("facility_name") else "",
        " (" + info["facility_type"] + ")" if info.get("facility_type") else "")


# ----------------------------------------------------------------- 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag=LOG_TAG)
    warn_once = ctx.warner("place bgm")

    # 置き場所は apply() の中で確定させておく（`ctx` の値はここでしか当てにならない）。
    playlist_path = ctx.state_path(*(STATE_SUBDIR + (PLAYLIST_NAME,)))
    state_dir = os.path.dirname(playlist_path)
    root = game_root()
    asset_dir = os.path.join(root, *ASSET_SUBDIR) if root else None
    if asset_dir is None:
        ctx.log("place bgm: Assets/sounds/musics not found; "
                "only state/musics/place will be used", level="WARN")

    # 世代をまたいで持つもの。再注入しても鳴っている曲を鳴らし直さない。
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "worlds": WorldStore(ctx, WORLDS_DIRNAME, default=empty_world,
                                 normalize=normalize_world, order=order_world, write=write),
            "playing": (None, None, None),  # (段の鍵, 曲の鍵, 場所)。None は未知（注入直後）
            "last": {},                # 段の鍵 -> 前回選んだ曲（AVOID_REPEAT 用）
            "issued": [],              # 自分が出したパス
            "game_src": None,          # ゲームが最後に渡してきた戦闘以外の曲
            "current_src": None,       # 最後に鳴らしたパス（自分のもゲームのも）
            "manager": None,
            "passthrough": False,      # 自分の鳴らし直しを包みで素通しする印
            "last_ctx": None,          # 見張りが最後に見た場所
        }
        setattr(sys, STORE_ATTR, store)
    worlds = store["worlds"].rebind(ctx, write)

    def load_pool():
        """走査して playlist を読む。戻りは (playlists, found)。"""
        found = scan_tracks(asset_dir, state_dir)
        data = ctx.read_json(playlist_path, None)
        playlists = data.get("playlists") if isinstance(data, dict) else None
        if not isinstance(playlists, dict):
            playlists = {}
        for category, playlist in playlists.items():
            if not isinstance(playlist, dict):
                continue
            for key in playlist:
                if key not in found and coerce_weight(playlist[key]) > 0:
                    warn_once(category + ":" + key,
                              "playlist.json の {} にある曲が見つからない: {}".format(category, key))
        return playlists, found

    def ensure_playlist():
        if os.path.isfile(playlist_path):
            return False
        data = {"_help": list(PLAYLIST_HELP),
                "playlists": dict((c, {}) for c in CATEGORIES)}
        return ctx.write_json(playlist_path, data)

    def is_mine(src):
        return isinstance(src, str) and src in store["issued"]

    def issue(path):
        if path not in store["issued"]:
            store["issued"].append(path)
            del store["issued"][:-MAX_ISSUED]

    def in_battle(app):
        if any(getattr(app, flag, False) for flag in BATTLE_FLAGS):
            return True
        return is_battle_track(store.get("current_src"))

    def audible(sound):
        try:
            return sound is not None and sound.get_num_channels() > 0
        except Exception:
            return False

    def world_bucket(info):
        """世界ファイル。設定画面が書き換えていれば読み直す（更新時刻で見る）。"""
        if not info.get("world"):
            return None
        try:
            return worlds.load(info["world"], fresh=True)
        except Exception:
            ctx.log_exc("place bgm: world file could not be read")
            return None

    def remembered(info, level, memory, pool):
        """覚えている曲がその段の候補に残っていればそれ。"""
        if not memory or not info.get("world"):
            return None
        entry = memory_entry(worlds.cached(info["world"]), memory)
        chosen = entry.get("chosen") if isinstance(entry, dict) else None
        if not isinstance(chosen, dict) or chosen.get("level") != level:
            return None
        track = chosen.get("track")
        return track if in_pool(track, pool) else None

    def remember(info, level, memory, track):
        if not memory or not info.get("world"):
            return
        with worlds.lock:
            bucket = worlds.load(info["world"])
            entry = memory_entry(bucket, memory, create=True)
            if entry is None:
                return
            group = memory.split(":", 1)[0]
            name = info.get("facility_name") if group == "facility" else info.get("area_name")
            if name and not entry.get("name"):
                entry["name"] = name
            entry["chosen"] = {"level": level, "track": track}
            worlds.save(info["world"])

    def choose(info, level, pool, memory):
        """段の中から1曲。戻りは (曲の鍵, 場所, 決め方)。

        覚えている曲があればそれ。
        同じ場所で同じ段の曲が鳴っていれば続ける（`kept`。覚える段ならそれを覚える）。
        どちらでもなければ重みで選ぶ（`picked`）。
        """
        place = place_of(level, info)
        playing_level, playing_key, playing_place = store["playing"]
        still = (playing_level == level and playing_place == place
                 and in_pool(playing_key, pool))
        if memory:
            track = remembered(info, level, memory, pool)
            if track is not None:
                return track, place, "remembered"
            if still:
                remember(info, level, memory, playing_key)
                return playing_key, place, "kept"
            track = pick(pool, None, False)
            remember(info, level, memory, track)
            return track, place, "picked"
        if still:
            return playing_key, place, "kept"
        track = pick(pool, store["last"].get(level), AVOID_REPEAT)
        store["last"][level] = track
        return track, place, "picked"

    def play(app, src):
        """自分から鳴らし直す。包みは素通しになる。"""
        manager = store.get("manager") or getattr(app, "sound_manager", None)
        if manager is None:
            raise RuntimeError("SoundManager not seen yet")
        store["passthrough"] = True
        try:
            return manager.play_music_from_src(app, src)
        finally:
            store["passthrough"] = False

    def game_track(info):
        """ゲームの曲へ戻すときのパス。土地に焼き付いた曲を優先する。"""
        return info.get("area_bgm") or store.get("game_src")

    # ----------------------------------------------------- ゲームが鳴らす経路
    @ctx.wrap("scripts.sounds:SoundManager.play_music_from_src")
    def play_music_from_src(orig, self, app, music_src, *args, **kwargs):
        store["manager"] = self
        if store.get("passthrough") or is_battle_track(music_src):
            store["current_src"] = music_src
            return orig(self, app, music_src, *args, **kwargs)
        real_app = ui.find_app() or app
        if not is_mine(music_src):
            store["game_src"] = music_src
        base_src = music_src if not is_mine(music_src) else (store.get("game_src") or music_src)
        level = track = place = chosen = None
        detail = ""
        try:
            info = context_of(real_app, base_src)
            store["last_ctx"] = context_key(info)
            playlists, found = load_pool()
            level, pool, memory = resolve(level_keys(info, world_bucket(info), playlists), found)
            if level is not None:
                track, place, how = choose(info, level, pool, memory)
                path, where = found[track]
                chosen = rewrite_src(base_src, track, path, where)
                detail = "{} | {} {} of {} | {} | by game".format(
                    level, how, track, len(pool), describe(info))
            else:
                detail = "{} | by game".format(describe(info))
        except Exception:
            ctx.log_exc("place bgm: choosing failed; playing the game's track")
            chosen = None
        if chosen is not None:
            try:
                result = orig(self, app, chosen, *args, **kwargs)
            except Exception:
                ctx.log_exc("place bgm: {} could not be played; "
                            "falling back to the game's track".format(chosen))
                write("FAILED {} -> {}".format(short(chosen), short(music_src)))
            else:
                issue(chosen)
                store["playing"] = (level, track, place)
                store["current_src"] = chosen
                write("{} <- {} ({})".format(
                    short(chosen), detail,
                    "same as the game's" if chosen == music_src else "replaced " + short(music_src)))
                return result
        else:
            store["playing"] = (LEVEL_GAME, None, None)
            write("game's track {} <- no candidate | {}".format(short(music_src), detail or "?"))
        store["current_src"] = music_src
        return orig(self, app, music_src, *args, **kwargs)

    # ----------------------------------------------------- 場所が変わった
    def reconcile(why, app=None):
        """いま居る場所に曲を合わせる。Clock の上（メインスレッド）で呼ぶこと。"""
        app = app or ui.find_app()
        if app is None or in_battle(app):
            return
        if not audible(getattr(app, "music", None)):
            return                  # 何も鳴っていない ＝ ゲームが切り替えの最中か題名画面。ゲームの play に任せる
        info = context_of(app)
        store["last_ctx"] = context_key(info)
        playing_level = store["playing"][0]
        playlists, found = load_pool()
        level, pool, memory = resolve(level_keys(info, world_bucket(info), playlists), found)
        if level is None:
            if playing_level in (LEVEL_GAME, None):
                store["playing"] = (LEVEL_GAME, None, None)
                return
            target = game_track(info)
            if not target:
                write("back to the game's track wanted but none known | {} | by {}".format(
                    describe(info), why))
                return
            try:
                play(app, target)
            except Exception:
                ctx.log_exc("place bgm: could not go back to the game's track")
                return
            store["playing"] = (LEVEL_GAME, None, None)
            store["current_src"] = target
            write("{} <- {} | back to the game's track | {} | by {}".format(
                short(target), LEVEL_GAME, describe(info), why))
            return
        track, place, how = choose(info, level, pool, memory)
        if (level, track, place) == store["playing"]:
            return                  # 同じ場所で同じ曲。続ける
        path, where = found[track]
        src = rewrite_src(store.get("game_src") or info.get("area_bgm"), track, path, where)
        try:
            play(app, src)
        except Exception:
            ctx.log_exc("place bgm: {} could not be played".format(src))
            write("FAILED {} | {} | by {}".format(short(src), describe(info), why))
            return
        issue(src)
        store["playing"] = (level, track, place)
        store["current_src"] = src
        write("{} <- {} | {} {} of {} | {} | by {}".format(
            short(src), level, how, track, len(pool), describe(info), why))

    def reconcile_guarded(why, app=None):
        try:
            reconcile(why, app)
        except Exception:
            ctx.log_exc("place bgm: reconcile failed ({})".format(why))

    def schedule(why, app=None):
        """メインスレッドへ回す。Clock が無ければその場で。"""
        try:
            from kivy.clock import Clock
        except Exception:
            reconcile_guarded(why, app)
            return
        Clock.schedule_once(lambda _dt: reconcile_guarded(why, app), 0)

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def move_phase(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        # マネージャは app を持っている（`__init__(self, app)`）。走っている app を探すより確か。
        schedule("move_phase", getattr(self, "app", None))
        return result

    # ----------------------------------------------------- 見張り
    def start_poll():
        if POLL_INTERVAL <= 0:
            return
        try:
            from kivy.clock import Clock
        except Exception:
            return

        def poll(_dt):
            if ctx.superseded():
                return False
            try:
                app = ui.find_app()
                if app is None or in_battle(app):
                    return True
                key = context_key(context_of(app))
                if key != store["last_ctx"]:
                    store["last_ctx"] = key
                    reconcile_guarded("poll", app)
            except Exception:
                ctx.log_exc("place bgm: poll failed")
            return True

        Clock.schedule_interval(poll, POLL_INTERVAL)

    ctx.on_ready(start_poll, key="place_bgm:poll:{}".format(ctx.generation))

    # ----------------------------------------------------- 起動時
    def boot_sync():
        try:
            created = ensure_playlist()
            _playlists, found = load_pool()
            where = {}
            for _key, (_path, w) in found.items():
                where[w] = where.get(w, 0) + 1
            write("pool: {} track(s) (assets {}, state {}) playlist={}{}".format(
                len(found), where.get("assets", 0), where.get("state", 0), playlist_path,
                " (new file)" if created else ""))
        except Exception:
            ctx.log_exc("place bgm: boot sync failed")

    ctx.on_ready(boot_sync, key="place_bgm:boot_sync")

    ctx.log("place bgm: state={} assets={} area_sticky={} facility_sticky={} "
            "avoid_repeat={} poll={}s".format(state_dir, asset_dir, AREA_STICKY,
                                              FACILITY_STICKY, AVOID_REPEAT, POLL_INTERVAL))
