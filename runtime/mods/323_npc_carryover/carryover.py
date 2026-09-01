# -*- coding: utf-8 -*-
r"""NPC のエクスポートとインポートの土台。**画面もフックも持たない。**

`tool.py`（ゲームの外の画面）と `npc_carryover.py`（ゲームの中の取り込み）の
両方がここを使う。
片方に書いて片方へ写すと必ずずれるので、両方が要るものは全部ここに置く
（`state.py` の冒頭と同じ理由）。

##### ゲームのデータはインストール先には無い

    %LOCALAPPDATA%\Darmabeko\Instantale\
    ├─ saves\<世界名>\savedata.json          遊んでいる世界（`app.save_data_dict`）
    ├─ worlds\<世界名>\world_data.json       世界の骨格（`app.world_dict`）
    └─ worlds\<世界名>\characters\<名前>\    立ち絵（`image_src` が絶対パスで指す先）

`IML_GAME_DIR`（設定画面が渡すゲーム本体の場所）はインストール先＝
`instantale.exe` の隣で、**セーブはそこには無い**
（2026-08-30 実機。Epic 版のインストール先の下に `saves` も `worlds` も無かった）。
だから場所は別に探す。

##### セーブは XOR で難読化されている（GAME.md §2.16）

    plain[i] = cipher[i] ^ b"Instantale_Save_Key_2026"[i % 24]

ゲーム自身の `scripts.save_codec` には素の JSON へ落ちる読み方
（`read_json_with_obfuscation_fallback`）があるので、こちらも
**素で読めたらそれ、駄目なら XOR** の順で読む（手元の5世界は全部 XOR だった）。

**書き戻さない。**
この MOD がセーブを触るのはゲームの中（実行中の辞書）だけで、
ファイルとしての `savedata.json` は読むだけ。
だからセーブエディタが課している復号→再暗号化の検算は要らない。

##### エクスポートの形は SaveEditor と同じ（`Tools/NpcPortability.cs`）

    <名前>.zip
    ├─ npc.json         {"format": "instantale_npc", "version": 1, ...}
    ├─ images/*.png     worlds\<元の世界>\characters\<名前>\ の中身
    └─ carryover.json   この MOD だけが読む追加分（311_ / 403_ の記憶）

`carryover.json` は向こうが読まない余分なファイルなので、互換は壊れない。
エディタが書いた zip をこの MOD で取り込めるし、逆もできる。
"""

from __future__ import annotations

import io
import json
import os
import zipfile

# ---- ゲームのデータの場所 ------------------------------------------------

#: セーブと世界の置き場（`%LOCALAPPDATA%\Darmabeko\Instantale`）。
DATA_VENDOR = ("Darmabeko", "Instantale")

#: セーブの難読化の鍵（GAME.md §2.16）。
SAVE_KEY = b"Instantale_Save_Key_2026"

#: エクスポートの形式の名前。SaveEditor の `NpcPortability.Format` と同じ文字列。
PACKAGE_FORMAT = "instantale_npc"
PACKAGE_VERSION = 1

#: zip の中の名前。
NPC_ENTRY = "npc.json"
IMAGE_PREFIX = "images/"
EXTRA_ENTRY = "carryover.json"
FACE_IMAGE = "face_image.png"

#: `state/` の中のフォルダ名と予約のファイル名。
STATE_DIRNAME = "npc_carryover"
PENDING_NAME = "pending.json"

#: 予約の状態。
PENDING = "pending"
PLACED = "placed"
SKIPPED = "skipped"

#: 置ける施設（`npc_carryover.py` の「誰をどこへ」。実セーブに出る `facility_type`）。
PLACEABLE_TYPES = ("guild", "inn")

#: 画像として持ち出す拡張子。
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def data_dir(override: str = "") -> str:
    r"""`saves\` と `worlds\` の親。

    `override`（または環境変数 `IML_INSTANTALE_DATA`）が在ればそちら。
    無ければ `%LOCALAPPDATA%\Darmabeko\Instantale`。
    """
    if override:
        return override
    env = os.environ.get("IML_INSTANTALE_DATA")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, *DATA_VENDOR)


def saves_dir(base: str = "") -> str:
    return os.path.join(base or data_dir(), "saves")


def worlds_dir(base: str = "") -> str:
    return os.path.join(base or data_dir(), "worlds")


def save_path(world: str, base: str = "") -> str:
    return os.path.join(saves_dir(base), world, "savedata.json")


def characters_dir(world: str, name: str = "", base: str = "") -> str:
    r"""`worlds\<世界>\characters\<名前>\`。名前を省くと `characters\` まで。

    `image_src` が指しているのはここ（実セーブで確認。**絶対パス**で入っている）。
    """
    parts = [worlds_dir(base), world, "characters"]
    if name:
        parts.append(safe_name(name))
    return os.path.join(*parts)


def list_worlds(base: str = "") -> list:
    """`savedata.json` を持つ世界の名前。名前順。"""
    root = saves_dir(base)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = [name for name in names
             if os.path.isfile(os.path.join(root, name, "savedata.json"))]
    return sorted(found, key=lambda text: text.lower())


# ---- 名前をファイル名にする ----------------------------------------------

def safe_name(name: str, fallback: str = "npc") -> str:
    r"""ファイル名・フォルダ名に使える形。

    名前はそのままフォルダ名になる（GAME.md §2.15）ので、
    エディタの `SafePath.FileName` と同じく区切りも予約デバイス名も潰す。

    **ローダの `state.world_filename` は使わない。**
    あちらは均した結果が元と違うとき短い印（`a/b` → `a_b-3ec69c85`）を足す。
    別の世界の控えを取り違えないための仕掛けで、そこでは正しい。
    ここが要るのは逆に**ゲームが書いたフォルダ名と同じ文字列**なので、
    印を足すと当たらなくなる。
    普通の名前（使えない字を含まない）はどちらも素通しなので、
    違いが出るのは壊れた名前のときだけ。
    """
    from instantale_modloader import state as loader_state
    if not isinstance(name, str):
        return fallback
    stem = loader_state._UNSAFE.sub("_", name.strip()).rstrip(loader_state._TRAILING)
    if not stem or stem in (".", ".."):
        return fallback
    if len(stem) > loader_state.MAX_STEM:
        stem = stem[:loader_state.MAX_STEM].rstrip(loader_state._TRAILING) or fallback
    if stem.upper() in loader_state.RESERVED:
        stem = "_" + stem
    return stem


#: 記録されたパスを繋ぎ直すときの目印。
#: この語から後ろがゲームのデータの中の位置で、前は機械ごとの事情。
PATH_MARKERS = ("worlds", "characters")


def local_path(recorded: str, base: str = "") -> str:
    r"""別の機械で書かれた絶対パスを、こちらのデータの場所へ繋ぎ直す。

    `image_src` は**書いた機械の絶対パス**で入っている。

        C:\Users\Owner\AppData\Local\Darmabeko\Instantale\worlds\X\characters\Y\face_image.png
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ ここは機械ごとの事情
                                                          ~~~~~~~~~~~~~~~~~~~~ ここが中の位置

    別の機械で作られた世界を持ってくると、前半が他人のユーザー名を指したまま
    こちらには存在しない。
    後半（`worlds\` から先）は同じなので、前半を落として
    こちらの `data_dir()` に繋ぎ直せば当たる。

    > 実データ（2026-08-30）: ペルディションは 95人中 93人の `image_src` が
    > `C:\Users\Owner\...` を指していた。繋ぎ直すと 92人ぶんの顔が見つかる。

    目印が見つからないパス（形が違う）はそのまま返す。
    """
    if not isinstance(recorded, str) or not recorded.strip():
        return ""
    parts = recorded.replace("\\", "/").split("/")
    for marker in PATH_MARKERS:
        lowered = [part.lower() for part in parts]
        if marker in lowered:
            cut = lowered.index(marker)
            return os.path.join(data_dir(base), *parts[cut:])
    return recorded


def image_dir_of(npc, world: str = "", base: str = "") -> str:
    r"""その NPC の立ち絵のフォルダ。**実際に在るものを返す**。無ければ空文字。

    見るのは2つ。

      1. `image_src` に記録されたフォルダを、こちらのデータの場所へ
         繋ぎ直したもの（`local_path`）。**ゲーム自身が書いた名前**なので、
         消毒の仕方がこちらとずれていても当たる
      2. `worlds\<世界>\characters\<名前>\`。
         まだ `image_src` の無い NPC（初回の会話まで絵は作られない）はこちら
    """
    npc = npc if isinstance(npc, dict) else {}
    name = npc.get("name") or ""
    tried = []

    def add(folder):
        if folder and folder not in tried:
            tried.append(folder)

    src = npc.get("image_src")
    if isinstance(src, dict):
        for value in src.values():
            if isinstance(value, str) and value.strip():
                add(os.path.dirname(local_path(value, base)))
    if world and name:
        add(characters_dir(world, name, base))
        add(os.path.join(worlds_dir(base), world, "characters", name))
    for folder in tried:
        if os.path.isdir(folder):
            return folder
    return ""


# ---- セーブの読み --------------------------------------------------------

def xor(raw: bytes) -> bytes:
    key = SAVE_KEY
    size = len(key)
    return bytes(byte ^ key[index % size] for index, byte in enumerate(raw))


def _utf8(raw: bytes):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode(raw: bytes):
    """セーブのバイト列を辞書にする。読めなければ `None`。

    素の JSON → XOR の順。
    ゲーム自身の `read_json_with_obfuscation_fallback` と同じ向き。
    """
    for text in (_utf8(raw), _utf8(xor(raw))):
        if text is None:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def read_save(world: str, base: str = ""):
    """1世界ぶんの `savedata.json`。読めなければ `None`。"""
    try:
        with io.open(save_path(world, base), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    return decode(raw)


# ---- セーブの中を読む ----------------------------------------------------

def npcs_of(save) -> dict:
    npcs = (save or {}).get("npcs")
    return npcs if isinstance(npcs, dict) else {}


def affinity_of(npc) -> tuple:
    """`(好感度, 関係を表す言葉)`。読めなければ `(None, "")`。

    `affinity_text` は**文字列のことも配列のこともある**
    （実セーブで両方あった。ヴェスティア 103人で str 57 / list 46）。
    配列のときの先頭が関係の側で、2つ目以降は魅力の話なので採らない。
    """
    player = ((npc or {}).get("relationship") or {}).get("player")
    if not isinstance(player, dict):
        return None, ""
    value = player.get("affinity")
    affinity = value if isinstance(value, int) and not isinstance(value, bool) else None
    text = player.get("affinity_text")
    if isinstance(text, list):
        text = next((item for item in text if isinstance(item, str)), "")
    return affinity, text if isinstance(text, str) else ""


def level_of(npc):
    value = (npc or {}).get("experience_level")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def jobs_of(save) -> list:
    """その世界に出てくる `job` の一覧（画面のフィルター用）。"""
    found = {npc.get("job") for npc in npcs_of(save).values()
             if isinstance(npc, dict) and isinstance(npc.get("job"), str)}
    return sorted(found)


def categories_of(save) -> list:
    found = {npc.get("category") for npc in npcs_of(save).values()
             if isinstance(npc, dict) and isinstance(npc.get("category"), str)}
    return sorted(found)


def find_facility(area, facility_id):
    """エリアの辞書から施設を id で引く。無ければ `None`。"""
    if not isinstance(area, dict) or facility_id is None:
        return None
    for node in (area.get("nodes") or {}).values():
        if not isinstance(node, dict):
            continue
        facility = (node.get("facilities") or {}).get(str(facility_id))
        if isinstance(facility, dict):
            return facility
    return None


def location_of(save, npc) -> tuple:
    """その NPC が居る `(エリア名, 施設名)`。読めなかったほうは空文字。

    見るのは `current_area` / `current_location`、欠けていれば
    `initial_location`（生成直後の個体はそちらにしか入っていないことがある）。
    実セーブのヴェスティアでは 103人全員がこの順で解決できた。
    """
    if not isinstance(npc, dict):
        return "", ""
    areas = (save or {}).get("areas")
    areas = areas if isinstance(areas, dict) else {}
    initial = npc.get("initial_location")
    initial = initial if isinstance(initial, dict) else {}

    area_id = npc.get("current_area")
    if area_id is None:
        area_id = initial.get("area")
    facility_id = npc.get("current_location")
    if facility_id is None:
        facility_id = initial.get("facility")

    area = areas.get(str(area_id)) if area_id is not None else None
    facility = find_facility(area, facility_id)
    return ((area or {}).get("name") or "",
            (facility or {}).get("name") or "")


def name_exists(npcs, name: str, except_id: str = None) -> bool:
    """同じ名前の NPC が居るか。エディタの `NameExists` と同じ完全一致。"""
    if not name:
        return False
    for npc_id, npc in (npcs or {}).items():
        if except_id is not None and str(npc_id) == str(except_id):
            continue
        if isinstance(npc, dict) and npc.get("name") == name:
            return True
    return False


# ---- エクスポート（zip の書き）------------------------------------------

def export(npc, world: str, npc_id: str, dest: str, extra=None,
           base: str = "") -> str:
    r"""NPC 1体を zip に書き出す。書けたら書いた場所を返す。

    | 引数 | |
    |---|---|
    | `npc` | セーブの `npcs[<id>]` そのまま（**この中では書き換えない**） |
    | `world` / `npc_id` | 出どころ。取り込み側の表示と記録に使う |
    | `dest` | 書き出す先の zip |
    | `extra` | `carryover.json` に入れる追加分（記憶）。無ければ書かない |

    `display_position_in_battle` だけ落とす（エディタの `SanitizeForExport` と
    同じ。戦闘中の並び順は持ち越さない）。
    `relationship` と `life_log` は**入れたまま**出して、
    引き継ぐかどうかはインポートのときに決める。
    """
    record = json.loads(json.dumps(npc))          # 深い複製（元を触らない）
    if "display_position_in_battle" in record:
        record["display_position_in_battle"] = None
    wrapper = {"format": PACKAGE_FORMAT,
               "version": PACKAGE_VERSION,
               "source_world": world,
               "original_id": str(npc_id),
               "original_name": record.get("name") or "",
               "npc": record}

    folder = os.path.dirname(dest)
    if folder:
        os.makedirs(folder, exist_ok=True)
    # 隣に書いてから差し替える（TECH.md §3.11.1）。
    tmp = dest + ".writing"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(NPC_ENTRY, json.dumps(wrapper, ensure_ascii=False, indent=1))
        for path, arcname in image_files(world, record, base):
            zf.write(path, IMAGE_PREFIX + arcname)
        if extra:
            zf.writestr(EXTRA_ENTRY,
                        json.dumps(extra, ensure_ascii=False, indent=1))
    os.replace(tmp, dest)
    return dest


def image_files(world: str, npc, base: str = "") -> list:
    r"""その NPC の立ち絵。`[(実パス, ファイル名)]`。

    場所は `image_dir_of`（`image_src` の絶対パス由来）。
    """
    folder = image_dir_of(npc, world, base) if isinstance(npc, dict) else         characters_dir(world, npc or "", base)
    if not folder:
        return []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for entry in sorted(names):
        if not entry.lower().endswith(IMAGE_SUFFIXES):
            continue
        path = os.path.join(folder, entry)
        if os.path.isfile(path):
            out.append((path, entry))
    return out


def free_path(folder: str, name: str) -> str:
    """`<名前>.zip`。同じ名前が在れば `<名前>(2).zip`。"""
    os.makedirs(folder, exist_ok=True)
    stem = safe_name(name)
    path = os.path.join(folder, stem + ".zip")
    index = 2
    while os.path.exists(path) and index < 1000:
        path = os.path.join(folder, "{}({}).zip".format(stem, index))
        index += 1
    return path


# ---- インポート（zip の読み）--------------------------------------------

class Package(object):
    """zip から読んだ1体ぶん。`npc` はセーブの形の辞書。"""

    def __init__(self, path, npc, source_world, original_id, original_name,
                 images=None, extra=None):
        self.path = path
        self.npc = npc
        self.source_world = source_world
        self.original_id = original_id
        self.original_name = original_name
        self.images = images or {}          # ファイル名 -> 中身
        self.extra = extra or {}

    @property
    def name(self):
        return self.npc.get("name") or self.original_name

    @property
    def job(self):
        return self.npc.get("job") or ""

    @property
    def level(self):
        return level_of(self.npc)

    @property
    def affinity(self):
        return affinity_of(self.npc)

    @property
    def where(self):
        """書き出したときに居た `(エリア名, 施設名)`。

        `carryover.json` に同梱してあるものを読む（zip だけで済ませるため）。
        入っていない zip ―
        この項目を足す前に書き出したもの、セーブエディタが書いたもの ―
        では空が返る。呼び側が元の世界のセーブから引き直せばよい。
        """
        where = self.extra.get("where")
        if not isinstance(where, dict):
            return "", ""
        return str(where.get("area") or ""), str(where.get("facility") or "")


def read_package(path: str, with_images: bool = True):
    """zip を読む。形式が違えば `None`（壊れた zip で画面を止めない）。"""
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                raw = zf.read(NPC_ENTRY)
            except KeyError:
                return None
            try:
                wrapper = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
            if not isinstance(wrapper, dict) \
                    or wrapper.get("format") != PACKAGE_FORMAT:
                return None
            npc = wrapper.get("npc")
            if not isinstance(npc, dict):
                return None
            images = {}
            for info in zf.infolist():
                if info.is_dir() or not info.filename.startswith(IMAGE_PREFIX):
                    continue
                # zip の中の名前は信頼しない（`..\` で外へ書ける）。
                # 取り出すのはファイル名だけ（エディタの `PlaceImages` と同じ）。
                entry = os.path.basename(info.filename.replace("\\", "/"))
                if not entry:
                    continue
                if with_images or entry == FACE_IMAGE:
                    images[entry] = zf.read(info)
            try:
                extra = json.loads(zf.read(EXTRA_ENTRY).decode("utf-8"))
            except (KeyError, ValueError, UnicodeDecodeError):
                extra = {}
    except (OSError, zipfile.BadZipFile):
        return None
    return Package(path, npc,
                   wrapper.get("source_world") or "",
                   str(wrapper.get("original_id") or ""),
                   wrapper.get("original_name") or npc.get("name") or "",
                   images, extra if isinstance(extra, dict) else {})


def list_packages(folder: str, with_images: bool = False) -> list:
    r"""フォルダの直下と1階層下の zip を全部読む。読めないものは飛ばす。

    1階層下まで見るのは `state\npc_carryover\<元の世界>\` に分けて置くため
    （SaveEditor の `npc\<ワールド名>\` と同じ並び）。
    """
    out = []
    for path in zip_paths(folder):
        package = read_package(path, with_images=with_images)
        if package is not None:
            out.append(package)
    out.sort(key=lambda item: (item.source_world.lower(), item.name.lower()))
    return out


def zip_paths(folder: str) -> list:
    """フォルダの直下と1階層下の zip の場所。"""
    paths = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return paths
    for entry in entries:
        path = os.path.join(folder, entry)
        if os.path.isfile(path) and entry.lower().endswith(".zip"):
            paths.append(path)
        elif os.path.isdir(path):
            try:
                inner = sorted(os.listdir(path))
            except OSError:
                continue
            paths.extend(os.path.join(path, name) for name in inner
                         if name.lower().endswith(".zip")
                         and os.path.isfile(os.path.join(path, name)))
    return paths


# ---- 予約（pending.json）------------------------------------------------

def carryover_dir(state_dir: str) -> str:
    return os.path.join(state_dir, STATE_DIRNAME)


def pending_path(state_dir: str) -> str:
    return os.path.join(carryover_dir(state_dir), PENDING_NAME)


def load_pending(state_dir: str) -> list:
    """予約の一覧。読めなければ空。"""
    try:
        with io.open(pending_path(state_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("pending")
    return ([row for row in data if isinstance(row, dict)]
            if isinstance(data, list) else [])


def save_pending(state_dir: str, rows) -> bool:
    """予約を書き出す。落ちても壊れないように隣に書いてから差し替える。"""
    path = pending_path(state_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".writing"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(list(rows), fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def reservation(zip_rel: str, target_world: str, inherit=None) -> dict:
    r"""予約1件。`zip` は `state\npc_carryover\` からの相対で持つ。

    絶対パスで持たないのは、配布フォルダごと別の場所へ移しても予約が生きるため。
    """
    return {"zip": zip_rel.replace("\\", "/"),
            "target_world": target_world,
            "status": PENDING,
            "inherit": dict(inherit or {})}


def package_path(state_dir: str, row) -> str:
    """予約の `zip`（相対）から実際の場所。"""
    return os.path.join(carryover_dir(state_dir),
                        str((row or {}).get("zip") or "").replace("/", os.sep))


def relative_zip(state_dir: str, path: str) -> str:
    r"""実際の場所から `state\npc_carryover\` の相対へ。"""
    try:
        return os.path.relpath(path, carryover_dir(state_dir)).replace("\\", "/")
    except ValueError:
        return os.path.basename(path)
