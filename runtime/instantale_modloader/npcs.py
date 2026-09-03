# -*- coding: utf-8 -*-
"""NPC を作って世界に入れる手順（GAME.md §2.23）。

「誰を作るか・何を書くか」は MOD が決める。
ここにあるのは素データの置き場所・採番・組み立て・配置という手順だけ。
`902_` が実測で確立した手順で、`320_` も同じものを要るようになった時点で
ローダへ移した（写して回るものはローダの語彙。TECH.md §3.2.3）。

##### 実測に基づく前提（経緯は `902_` の DOC.md §3 / GAME.md §2.23）

2つの入口を試して、どちらも「作る側」ではなかった:

| 試したもの | 結果 |
|---|---|
| `World.generate_character(id, value)` | `KeyError: '<id>'`。**セーブの `npcs` を id で引く側**で、無い id は引けない |
| `save_area_json:generate_npc(...)` | 例外は出ないが、`world_dict` にも `world.characters` にも何も現れない |

`KeyError` の出方が答えを教えている。
`generate_character` は素データを id で引く。ならば先にそこへ書けばよい:

  1. 空いている id を取る（実在する id とゲームの採番台帳 `index['npc']` の
     大きいほう。取ったら台帳を進める。片方だけ見ると次の町の生成で衝突する）
  2. 素データの辞書すべてにセーブの形で書く（決め打ちすると外す）
  3. `World.generate_character(id, data)` で実行時の `Character` を組む
  4. `move_npc_to_facility` で施設に置く（`302_` が実証済みの経路）

生成した NPC は HP・スキル・装備・立ち絵のいずれも空でよい
（ゲームが会話や戦闘の直前に `ensure_npc_detail_generated` で埋める）。

##### セーブに残る

NPC は独自キーではなくゲーム自身の項目なので壊れないが、
MOD を外しても世界に残る。
README の「MOD を消せば完全に元通り」からは外れる性質なので、
これを使う MOD は自分の DOC.md にその旨を書くこと。
"""

import copy
import sys

from . import ids, ui

#: 生成直後の NPC の素データのひな型。
#:
#: HP・スキル・装備・立ち絵は空でよい。
#: ゲームが会話や戦闘の直前に `ensure_npc_detail_generated` で埋める。
#: だから MOD は軽く作れる。
#:
#: ##### 並び順は「合っていればよい」ではなく、この順でなければならない
#:
#: セーブは辞書をそのまま JSON に落とすので、ここに書いた順がそのまま
#: ファイルの行順になる。
#: そしてセーブを読む側には、項目を上から順に並べて見せる道具がある
#: （別途あるセーブエディタ）。
#: 順番が変わると、項目は全部揃っているのに表示が崩れる。
#:
#: だから項目は「揃えた」だけでは足りない。
#: ゲーム自身が書く順と1つずつ一致させる。
#: 下の並びは実際のセーブから起こしたもの:
#:
#: saves/<世界名>/savedata_plain.json の npcs
#: 51体中50体がこの33項目・この順（残る1体は speech_style が無いだけで
#: 順番は同じ）。
#: プリセットの world_data は先頭29項目までで、後ろの4つ
#: （current_area / current_location / knowledge /
#: display_position_in_battle）は遊び始めてから増える。
#:
#: `make_npc` はこの並びを崩さない。
#: `dict.update` は既にある鍵の位置を動かさないので、
#: **33項目を漏らさず先に持っている**限り順番は保たれる。
#: 逆に1つでも欠けていると、その項目だけが末尾に足されて並びが壊れる。
#: 項目を足すときは必ずこの表の正しい位置へ差し込むこと。
#: 末尾に足さない。
NEW_NPC_TEMPLATE = {
    "name": None,
    "id": None,
    "category": None,
    "profile": None,
    "personality": None,
    "look_description": None,
    "speech_style": None,
    "job": None,
    "state": "",
    # 6つの鍵は必須。空の {} だと `World.generate_character` が
    # `KeyError: 'constitution'` で落ちる（実機。
    # VERIFICATION_LOG.md §2.72）。値は None でよい ―
    # 詳細生成前（level_of_detail=1）の実物がこの形（GAME.md §2.23）。
    "ability_scores": {"strength": None, "dexterity": None,
                       "constitution": None, "intelligence": None,
                       "wisdom": None, "charisma": None},
    "experience_level": None,
    "experience_point": 0,
    "original_max_hp": None,
    "max_hp": None,
    "current_hp": None,
    "age": None,
    "skills": {},
    "equipments": {},
    "weakness": None,
    "location": {"area": None, "node": None, "facility": None},
    "inventory": {},
    "image_src": {"base_normal": None, "base_upscaled": None,
                  "fullbody": None, "opponent": None, "face": None},
    "look": [],
    "memory": {"life_log": "", "memory_archive": [], "session_log": [],
               "prior_area_summary": "", "brief_summary": "ゲーム開始"},
    "life_log": [],
    "current_log": [],
    "relationship": None,
    "initial_location": {"area": None, "node": None, "facility": None},
    "config": {},
    "current_area": None,
    "current_location": None,
    # リストであってディクショナリではない。
    # 実際のセーブでは `[]`。
    "knowledge": [],
    "display_position_in_battle": None,
}

#: セーブに書くときの項目の並び。
#: `NEW_NPC_TEMPLATE` の定義順がそのまま
#: 正解なので、そこから起こす（二重に持つと必ず片方が古くなる）。
NPC_FIELD_ORDER = tuple(NEW_NPC_TEMPLATE)

#: `config` の既定。
#: 実機で観測した生成直後の NPC に合わせてある。
#: `level_of_detail` を 1 にして渡すと、詳細（HP・スキル）は最初の会話や
#: 戦闘の直前にゲーム自身が埋める。
DEFAULT_CONFIG = {"level_of_detail": 2, "is_player": False,
                  "is_dead": False, "difficulty_level": 4}


def character_ids(app):
    """実行時の名簿に載っている id。"""
    characters = getattr(getattr(app, "world", None), "characters", None)
    return set(characters) if isinstance(characters, dict) else set()


def npc_stores(app, max_depth=2):
    """NPC の素データが入っていそうな辞書を全部集める。

    `[(どこにあるか, 辞書), ...]`。

    ##### なぜ探すのか

    `World.generate_character(id, value)` は id で素データを引くが、
    どこから引くのかが分からない。
    `app.world_dict['npcs']` に書いてから呼んでも `KeyError` のままだった（実測）。
    `World.__init__` は `save_data_dict` を受け取っているので、
    `app.world_dict` とは別の辞書を握っている可能性が高い。

    `902_` が2回続けて「ここだろう」と決め打って外しているので、決め打ちをやめる。
    `302_` がパーティ名簿でやっているのと同じ手（`ui.party_stores`）で、
    心当たりを全部集めて全部に書く。
    余分に書いても、同じ id に同じ値が入るだけで害が無い。

    ##### 見分け方は2つ持つ

    もとは「実行時の名簿（`world.characters`）と鍵が重なるか」の1つだった。
    これは**ロードの直後には効かない** ―
    その時点の `world.characters` はまだ埋まっておらず、
    突き合わせる相手が居ないので、素データを1つも見つけられない。

    > 実機（`323_` の作業で踏んだ）:
    > ロード直後に呼んだ `npc_stores` が `<none>` を返し、
    > `save_data_dict['npcs']`（GAME.md §2.23 が「★ ここが本体」と書いている辞書）へ
    > 何も書かれないまま `generate_character` が `KeyError` で落ちた。
    > 直後に測った `224_` の記録では `world.characters` は**1件**だった。

    そこで**中身の形**でも見分ける。
    値が NPC の素データなら `name` と `ability_scores` を持っている
    （`ability_scores` の6鍵が無いと `generate_character` が落ちるので、
    これは「素データである」ことの必要条件そのもの）。

    2つのどちらかに当たれば素データとみなす。
    名簿が埋まっている場面では前と同じ結果になり、
    空の場面でだけ余計に見つかる。
    """
    seen, out = set(), []
    known = character_ids(app)

    def looks_like_npcs(value):
        """NPC の素データの辞書か。

        既存の character id が鍵になっているか、
        中身が NPC の形（`name` と `ability_scores` を持つ）か。
        """
        if not isinstance(value, dict) or not value:
            return False
        keys = {str(key) for key in value}
        if known & keys:
            return True
        sample = next(iter(value.values()), None)
        return (isinstance(sample, dict) and "name" in sample
                and "ability_scores" in sample)

    def visit(holder, label, depth):
        if depth > max_depth or id(holder) in seen:
            return
        seen.add(id(holder))
        try:
            items = (holder.items() if isinstance(holder, dict)
                     else vars(holder).items())
        except Exception:
            return
        for name, value in list(items):
            if not isinstance(value, dict):
                continue
            where = "{}.{}".format(label, name)
            if name in ("characters", "npcs") and looks_like_npcs(value):
                out.append((where, value))
            elif "npcs" in value and looks_like_npcs(value.get("npcs")):
                out.append((where + "['npcs']", value["npcs"]))
            elif depth < max_depth:
                visit(value, where, depth + 1)

    world = getattr(app, "world", None)
    if world is not None:
        visit(world, "world", 0)
    visit(app, "app", 0)
    return out


def save_npcs(app):
    """素データの辞書を1つにまとめて返す（読む用）。

    置き場所は1つではない（`npc_stores`）ので、
    掃除の対象を探すときは全部を重ねて見る。
    書くのには使わない。
    書くほうは `npc_stores` を回して全部に書く。
    """
    merged = {}
    for where, store in npc_stores(app):
        if "characters" in where.rsplit(".", 1)[-1]:
            continue                    # 実行時の名簿。素データではない
        for npc_id, data in store.items():
            if isinstance(data, dict):
                merged.setdefault(str(npc_id), data)
    return merged


def index_stores(app):
    """採番台帳を持つ辞書を全部集める。本体は `ids.stores`。"""
    return ids.stores(app)


def next_game_id(app):
    """ゲーム自身が次に振る NPC の id（`index['npc']`）。本体は `ids.counter`。"""
    return ids.counter(app, "npc")


def free_id(app, npcs=None):
    """まだ使われていない id。台帳は進めない（進めるのは `make_npc`）。

    本体は `ids.next_id`。実在 id の最大値+1 と台帳 `index['npc']` の
    大きいほうを採る。実在 id だけを見て `max + 1` で採ると、次の町の生成で
    ゲームが同じ番号を踏む（`ids` の冒頭。VERIFICATION_LOG.md §2.77）。
    """
    if npcs is None:
        npcs = save_npcs(app)
    return ids.next_id(app, "npc", used=list(npcs or {}))


def advance_index(app, npc_id, write=None):
    """採番台帳の `npc` を `npc_id + 1` まで進める。本体は `ids.advance`。"""
    moved = ids.advance(app, "npc", npc_id)
    if write and moved:
        write("make_npc: index['npc'] -> {} via {}".format(
            int(str(npc_id)) + 1, moved))
    return moved


def make_npc(app, fields, area, facility, config=None, write=None):
    """NPC を1体作って世界に入れる。作れたら id、作れなければ None。

    | 引数 | |
    |---|---|
    | `fields` | NPC の項目（`NEW_NPC_TEMPLATE` の鍵に合わせる）。id と場所はこちらで埋める |
    | `area` / `facility` | 置き先の id（str）。施設はそのエリアの中のもの |
    | `config` | `config` へ足すもの。`DEFAULT_CONFIG` の上に重なる |
    | `write` | 呼んだ MOD 自身のログ関数。無くてよい |

    連続で呼ぶときは、返った id が前と同じでないか呼び側で確かめること
    （`902_` の実機で、3人が全員同じ id になり前の人物を上書きしていた）。

    失敗しても壊れないように、途中で落ちたら書いた分を取り消す。
    """
    world_dict = getattr(app, "world_dict", None)
    if not isinstance(world_dict, dict):
        if write:
            write("make_npc: app.world_dict is not a dict; cannot create")
        return None
    world = getattr(app, "world", None)
    build = getattr(world, "generate_character", None)
    if not callable(build):
        if write:
            write("make_npc: World.generate_character is not available")
        return None

    npcs = world_dict.get("npcs")
    if not isinstance(npcs, dict):
        npcs = {}
        world_dict["npcs"] = npcs

    # 台帳が実在に追いついていなければ記録に残す（ゲームが踏む前に気づくため）。
    ids.audit(app, write)
    npc_id = free_id(app, npcs)
    # 並び順を崩さない。
    # テンプレートが33項目を全部持っているので、上書きだけしている限り
    # 位置は動かない（`dict.update` は既存の鍵を動かさない）。
    # テンプレートに無い鍵だけが末尾に足されて並びを壊すので、
    # そうなったら記録に残す。
    # 黙って通すと、セーブを上から順に見せる道具の表示が崩れてから
    # 気づくことになる。
    # 深い複製にするのは、浅い複製だと入れ子（ability_scores / memory /
    # image_src …）が作った NPC 全員で同じ辞書になるから。
    # ゲームが1人の能力値を埋めると全員が同じ値になる。
    data = copy.deepcopy(NEW_NPC_TEMPLATE)
    fields = {key: value for key, value in dict(fields or {}).items()
              if key != "config"}
    stray = [key for key in fields if key not in NEW_NPC_TEMPLATE]
    if stray and write:
        write("make_npc: spec has field(s) the template does not know; they "
              "will be appended and break the save field order: {}"
              .format(sorted(stray)))
    data.update(fields)
    data["id"] = npc_id
    data["initial_location"] = {"area": str(area), "node": None,
                                "facility": str(facility)}
    # 生成直後の実物は current_area / current_location にも id が入っている
    # （実セーブの level_of_detail=1 の個体。GAME.md §2.23）。
    data["current_area"] = str(area)
    data["current_location"] = str(facility)
    data["config"] = dict(DEFAULT_CONFIG, **dict(config or {}))

    # 心当たりの辞書すべてに書く。
    # どこから引かれるか分からないので、1箇所に賭けない。
    # 同じ id に同じ値が入るだけなので、余分に書いても害は無い。
    stores = npc_stores(app)
    wrote = []
    for where, store in stores:
        if "characters" in where.rsplit(".", 1)[-1]:
            continue                    # 実行時の名簿。素データは入れない
        store[npc_id] = data
        wrote.append(where)
    npcs.setdefault(npc_id, data)
    if write:
        write("make_npc: npc stores = {}".format(
            [(where, len(store)) for where, store in stores] or "<none>"))

    character = None
    try:
        character = build(npc_id, data)
    except Exception as exc:
        if write:
            write("make_npc: generate_character({}) failed: {}: {} "
                  "(wrote to {})".format(npc_id, type(exc).__name__, exc,
                                         wrote or "nothing"))
    if character is None:
        character = ui.character_of(app, npc_id)
    if character is None:
        # 最後の手段: `Character` を直に組む。
        # コンストラクタは `scripts.characters` に完全な署名で露出している
        # （リコンより）。
        # ゲームの登録処理を経ないぶん行儀は悪いが、`generate_character` が
        # どこを読んでいるか分からない以上、これが確実に通る唯一の道。
        character = _build_character(app, npc_id, data, write)
        if character is None:
            for where, store in stores:
                store.pop(npc_id, None)
            npcs.pop(npc_id, None)
            if write:
                write("make_npc: could not create {}".format(npc_id))
            return None
        characters = getattr(getattr(app, "world", None), "characters", None)
        if isinstance(characters, dict):
            characters[npc_id] = character

    # ゲームの採番台帳を進める。ここを忘れると次の町の生成で
    # ゲームがこの番号を踏み、店主の素データが差し替わる（`free_id`）。
    advance_index(app, npc_id, write)

    placed = _place(app, npc_id, character, area, facility, write)
    if write:
        write("make_npc: created {} {!r} at {}/{} (placed={} npcs={})".format(
            npc_id, data.get("name"), area, facility, placed, len(npcs)))
    return npc_id


#: `Character.__init__` に実在する引数だけ（リコンの署名より）。
#: **セーブの項目名とは違うものがある**。
#: `ability_scores` は
#: `original_ability_scores`、`knowledge` は `knowledges`。
CHARACTER_KWARGS = (
    "name", "id", "category", "profile", "personality", "job",
    "look_description", "look", "speech_style", "state", "age",
    "experience_level", "experience_point", "initial_location", "config",
)


def _build_character(app, npc_id, data, write=None):
    """`Character` を直に組む。`generate_character` が通らないときの最後の手段。"""
    module = sys.modules.get("scripts.characters")
    cls = getattr(module, "Character", None)
    if cls is None:
        if write:
            write("    scripts.characters.Character is not available")
        return None
    kwargs = {key: data[key] for key in CHARACTER_KWARGS if key in data}
    scores = data.get("ability_scores")
    if scores:
        kwargs["original_ability_scores"] = scores
    try:
        character = cls(**kwargs)
    except Exception as exc:
        if write:
            write("    Character(**{}) failed: {}: {}".format(
                sorted(kwargs), type(exc).__name__, exc))
        return None
    if write:
        write("    built Character directly for {}".format(npc_id))
    return character


def _place(app, npc_id, character, area, facility, write=None):
    """施設の名簿に載せる。`302_` が実証した経路を通す。

    載らなくても NPC そのものは世界に居る（`world.characters` から引ける）ので、
    失敗しても捨てない。
    ただし施設の会話には出てこなくなるので記録は残す。
    """
    move = getattr(app, "move_npc_to_facility", None)
    area_obj = ui.world_areas(app).get(str(area))
    if not callable(move) or area_obj is None:
        return False
    try:
        target, node = ui.find_facility(area_obj, str(facility))
    except Exception:
        target, node = None, None
    if target is None:
        return False
    try:
        move(npc_id, character, target, node)
        return True
    except Exception as exc:
        if write:
            write("    move_npc_to_facility failed: {}: {}".format(
                type(exc).__name__, exc))
        return False
