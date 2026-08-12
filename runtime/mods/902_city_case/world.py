# -*- coding: utf-8 -*-
"""ゲームのどこに何があるか。**この MOD の方針は持たない。**

「誰を犯人にするか」「いくら払うか」は入口が決める。ここは引き当てと書き込みの
手順だけを知っている。`307_` の `world.py` と同じ立場（TECH.md §3.1.1.1）。

##### 実測に基づく前提（GAME.md §2.7 / §2.22）

- 施設は `areas[id].nodes[nid].facilities[fid]` の入れ子
- `Facility.owner` は character の id（str）。**主を消すと店が壊れる**
- `Facility.characters` は id の配列。重複が入ることがある
- 死亡の印は `Character.config['is_dead']`。立てても名簿からは外れない
  （読む側が飛ばす）
"""

import sys

from instantale_modloader import frames, ui
from instantale_modloader.state import UNKNOWN_WORLD, world_key


def world_name(app):
    """控えを紐付ける鍵。引けなければ空文字（そのときは紐付けない）。

    **見分け方はローダの語彙**（`state.world_key`）。ここに写した版は
    `app.world` の属性しか見ておらず、**ロード直後は必ず空文字**になっていた
    ― そのとき `app.world` はまだ組み上がっておらず、世界名はセーブ側
    （`world_dict["world_data"]`）にしか無い。空文字は呼び側で「どの世界か
    分からない」の合図なので、控えの世界照合（`current`）と後始末（`sweep`）が
    **ロードした直後だけ黙って素通り**していた（TECH.md §3.2.3・`state.py`）。

    空文字の契約はそのまま保つ。ローダは読めないとき `"_"` を返すので、
    ここで戻し直す ― この MOD は「分からないなら紐付けない」に倒す作りで、
    知らない世界の控えを1つの鍵にまとめてしまうより安全側。
    """
    key = world_key(app)
    return "" if key == UNKNOWN_WORLD else key


def current_facility(app):
    """いま居る施設。**id で持っている場合も引き当てる。**

    `player.location` は施設のオブジェクトとは限らない。セーブでは
    **施設 id の文字列**（`'106'`）で、遊んでいる最中にその施設へ入ると
    Facility そのものに置き換わる。

    ロード直後は前者のままなので、`facility_type_of` が空文字を返し、
    **ギルドに立っているのにボタンが出なかった**（実機）。
    「新しい世界では動くのに、セーブをロードすると動かない」の正体。

    `ui.current_area` は同じ理由で既に両方を引き当てている（そちらの註）。
    施設側にも同じ手当てが要った。
    """
    where = getattr(getattr(app, "player", None), "location", None)
    if where is None or ui.facility_type_of(where):
        return where                    # もうオブジェクト。そのまま使う
    area = ui.current_area(app)
    if area is None:
        return where
    try:
        found, _node = ui.find_facility(area, ui.element_id(where))
    except Exception:
        return where
    return found if found is not None else where


def facility_type(app):
    return ui.facility_type_of(current_facility(app))


def area_id(app):
    return ui.area_id_of(ui.current_area(app))


def facility_name(app, limit=40):
    return ui.facility_name(app, current_facility(app), limit=limit)


def owner_ids(app, max_areas=40):
    """施設の主を務めている character の id。**この人たちは使わない。**

    実測した世界では 35 人中 24 人が主だった（VERIFICATION_LOG.md §2.29）。
    消すと店に話せる相手が居なくなるので、事件のキャストからは外す。
    """
    owners = set()
    try:
        areas = list(ui.world_areas(app).items())[:max_areas]
    except Exception:
        return owners
    for _area_id, area in areas:
        for node in ui.nodes_of(area):
            for _fid, facility in ui.facilities_of(node).items():
                owner = frames.attr(facility, "owner")
                if owner not in (None, frames.MISSING, ""):
                    owners.add(ui.element_id(owner))
    return owners


def facility_types_in(app, target_area_id):
    """その土地に実在する `facility_type` の集合。

    **手がかりの置き場所はここから選ぶ。**町の構成は世界ごとに違い、
    闇市や診療所が無い町もある（利用者の報告）。無い施設に
    手がかりを置くと、その事件は永久に解けなくなる。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    if area is None:
        return set()
    kinds = set()
    for node in ui.nodes_of(area):
        for _fid, facility in ui.facilities_of(node).items():
            kind = ui.facility_type_of(facility)
            if kind:
                kinds.add(kind)
    return kinds


def facilities_in(app, target_area_id):
    """その土地の `(facility_id, facility_type)` の一覧。

    生成した NPC を町に散らすのに使う。全員が同じ場所に立っていると、
    その施設だけ不自然に人が増える。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    if area is None:
        return []
    out = []
    for node in ui.nodes_of(area):
        for facility_id, facility in ui.facilities_of(node).items():
            out.append((str(facility_id), ui.facility_type_of(facility)))
    return out


def owner_in(app, target_area_id, facility_type):
    """その土地の、その種類の施設の**主の id**。無ければ空文字。

    手がかりの証言者はこの人物。**事件を組むときに控えておく** ―
    そうすれば「誰が何を知っているか」が id の突き合わせだけで決まり、
    どう話しかけられたかに依存しなくなる。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    if area is None:
        return ""
    for node in ui.nodes_of(area):
        for _fid, facility in ui.facilities_of(node).items():
            if ui.facility_type_of(facility) != facility_type:
                continue
            owner = frames.attr(facility, "owner")
            if owner not in (None, frames.MISSING, ""):
                return ui.element_id(owner)
    return ""


def _short(value, limit):
    """文字列として取り出して詰める。**取れなければ空文字。**

    渡ってくるのはセーブから読んだ値なので、`MISSING`・`None`・辞書・
    リストのどれでもありうる。ここで受け止めておかないと、頼み文を組む
    途中で落ちて**事件が始まらなくなる**（描写の都合で遊びが止まる）。
    """
    if not isinstance(value, str) or value == frames.MISSING:
        return ""
    got = " ".join(value.split())
    return got if len(got) <= limit else got[:limit] + "…"


def area_name(app, target_area_id, limit=40):
    """**町の名前。**施設の名前ではない。

    以前は「町」として `place_name(..., 'guild')` を渡していた ― それは
    ギルドの名前で、実機の頼み文に `【町】鉄錆の徴収所` と出ていた。町の名前は `Area.name` にある。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    value = frames.attr(area, "name") if area is not None else None
    return _short(value, limit)


def area_notes(app, target_area_id, limit=240):
    """その町の説明。**AI に「どこの話か」を教えるために渡す。**

    `Area.descriptions` は `overview` / `facilities` を持つ辞書（実セーブで
    確認）。長いので頭だけ使う ― 事件の文章を書かせるのに要るのは
    「どういう町か」であって、町の全部ではない。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    notes = frames.attr(area, "descriptions") if area is not None else None
    if isinstance(notes, dict):
        for key in ("overview", "facilities", "description"):
            got = _short(notes.get(key), limit)
            if got:
                return got
        return ""
    return _short(notes, limit)


def world_notes(app, limit=240):
    """世界の説明。町より一段上の文脈。"""
    for holder in (getattr(app, "world_dict", None),
                   getattr(app, "save_data_dict", None)):
        if not isinstance(holder, dict):
            continue
        data = holder.get("world_data")
        if isinstance(data, dict):
            for key in ("overview", "structure_description", "story"):
                got = _short(data.get(key), limit)
                if got:
                    return got
    return ""


def facility_notes(app, target_area_id, limit=12):
    """その町に実在する施設の `(名前, 種類, 主の名前)`。

    **実在の場所を渡すと「その町の事件」になる。**架空の宿屋の名前を
    書かれるより、いま歩いている町の宿屋の名前が出るほうが地に足が付く。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    if area is None:
        return []
    out = []
    for node in ui.nodes_of(area):
        for _fid, facility in ui.facilities_of(node).items():
            name = _short(frames.attr(facility, "name"), 40)
            if not name:
                continue
            owner = frames.attr(facility, "owner")
            owner_id = (ui.element_id(owner)
                        if owner not in (None, frames.MISSING, "") else "")
            out.append((name, ui.facility_type_of(facility) or "",
                        name_of(app, owner_id) if owner_id else ""))
            if len(out) >= limit:
                return out
    return out


def facility_of(app, target_area_id, npc_id, limit=40):
    """その人物が**いま居る施設の名前**。分からなければ空文字。

    施設の名簿を舐めて探す。**置いたときの id を控えるのではなく、今を見る** ―
    ゲームは NPC を動かすことがあるので、控えは古くなりうる。名簿は
    `Facility.characters` に居る（`move_npc_to_facility` が入れる先）。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    if area is None or not npc_id:
        return ""
    want = str(npc_id)
    for node in ui.nodes_of(area):
        for _fid, facility in ui.facilities_of(node).items():
            roster = frames.attr(facility, "characters")
            for member in (roster if isinstance(roster, list) else []):
                if str(ui.element_id(member)) == want:
                    return _short(frames.attr(facility, "name"), limit)
    return ""


def resident_names(app, target_area_id, limit=12):
    """その町に居る人物の名前。**同じ名前を作らせないために渡す。**"""
    area = ui.world_areas(app).get(str(target_area_id))
    if area is None:
        return []
    seen = []
    for node in ui.nodes_of(area):
        for _fid, facility in ui.facilities_of(node).items():
            roster = frames.attr(facility, "characters")
            for member in (roster if isinstance(roster, list) else []):
                name = name_of(app, ui.element_id(member))
                if name and name not in seen:
                    seen.append(name)
                    if len(seen) >= limit:
                        return seen
    return seen


def place_name(app, target_area_id, facility_type, limit=40):
    """その土地でその種類の施設の**名前**。無ければ None。

    案内に使う。「宿屋へ行け」ではなく「『欠けた月亭』へ行け」と言えると、
    プレイヤーは画面の移動先の文字列とそのまま突き合わせられる。
    種類の名前しか出せないと、施設が複数ある町で結局迷う。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    if area is None:
        return None
    for node in ui.nodes_of(area):
        for _fid, facility in ui.facilities_of(node).items():
            if ui.facility_type_of(facility) == facility_type:
                name = ui.facility_name(app, facility, limit=limit)
                if isinstance(name, str) and name.strip():
                    return name
    return None


def character_of(app, npc_id):
    return ui.character_of(app, npc_id)


def name_of(app, npc_id, limit=40):
    return ui.character_name(app, npc_id, limit=limit)


def config_of(character):
    config = frames.attr(character, "config")
    return config if isinstance(config, dict) else None


def is_dead(character):
    config = config_of(character)
    return bool(config.get("is_dead")) if config else False


def set_dead(app, npc_id, value=True):
    """退場させる。**名簿からは外さない**（外すと参照が切れる）。

    実測（VERIFICATION_LOG.md §2.29 / §2.30）: 印を立てても施設の名簿には残り、
    それでもゲーム内では会話にも呼び出しにも出てこない。読む側が飛ばして
    いるので、こちらは印だけ立てればよい。戻すこともできる。
    """
    character = character_of(app, npc_id)
    config = config_of(character)
    if config is None:
        return False
    config["is_dead"] = bool(value)
    return True


def remove_npc(app, npc_id, write=None):
    """**NPC を世界から完全に消す。**`set_dead` と違い、痕跡を残さない。

    ##### なぜ印を立てるだけでは足りないのか

    `set_dead` は名簿に残す（上）。事件が1件で終わるならそれでよかったが、
    **繰り返し遊ぶとセーブが太り続ける。**実測（利用者の指摘）:

    - 生成直後の NPC が約 1.4KB、ゲームが中身を埋めると 3〜8KB
    - `npcs` はセーブ全体の約2割（実セーブ 790KB / 51体で計測）
    - 1件につき4体。10件遊べば 40体増える

    害はバイト数より**町が見知らぬ人で埋まる**ことのほうが大きい。人数は
    土地の人物一覧にも、ゲームが組む文脈にも効く。

    ##### 消せる根拠

    **セーブの施設は名簿を持っていない。**実セーブを見ると `facility` の項目は
    `name` / `id` / `description` / `facility_type` / `owner` / `connections` /
    `config` だけで、誰が居るかは NPC 側の `location` から実行時に組み直されて
    いる。だから `npcs` から消せば、次に読み込んだ世界には現れない。
    クエストも `client_name`（名前の文字列）で持っていて id では参照しない。

    実行時のぶん（`world.characters` と、施設に組まれた名簿）も同時に外す ―
    こちらを残すと、そのセッションの間だけ幽霊が立ち続ける。

    ##### 消さない場合

    **パーティーに居る者は消さない。**プレイヤーが連れ歩いている相手を
    消すと、パーティーの参照が切れて何が起きるか分からない。事件の容疑者が
    仲間になる経路は用意していないが、他の MOD やゲーム側の都合で入りうる。
    """
    npc_id = str(npc_id)
    if npc_id in {str(i) for i in (ui.party_ids(app) or [])}:
        if write:
            write("remove_npc: {} is in the party; left alone".format(npc_id))
        return False

    gone = []
    for where, store in npc_stores(app):
        if npc_id in store:
            try:
                del store[npc_id]
                gone.append(where)
            except Exception:
                pass
        elif int_key(store, npc_id) is not None:
            try:
                del store[int_key(store, npc_id)]
                gone.append(where + "(int)")
            except Exception:
                pass

    rosters = _drop_from_rosters(app, npc_id)
    if write:
        write("remove_npc: {} removed from {} store(s) {} and {} roster(s)"
              .format(npc_id, len(gone), gone or "-", rosters))
    return bool(gone)


def int_key(store, npc_id):
    """id が数値の鍵で入っている場合の受け皿（辞書によって型が違う）。"""
    try:
        number = int(npc_id)
    except (TypeError, ValueError):
        return None
    return number if number in store else None


def _drop_from_rosters(app, npc_id):
    """施設に組まれた実行時の名簿から外す。戻り値は外した箇所の数。

    セーブには無い構造なので、消し忘れてもファイルは汚れない。ただし
    そのセッションの間だけ、消したはずの人物が施設に立ち続ける。
    """
    dropped = 0
    for _area_id, area in (ui.world_areas(app) or {}).items():
        for node in ui.nodes_of(area):
            for _fid, facility in ui.facilities_of(node).items():
                roster = frames.attr(facility, "characters")
                if not isinstance(roster, list):
                    continue
                keep = [m for m in roster
                        if str(ui.element_id(m)) != str(npc_id)]
                if len(keep) != len(roster):
                    roster[:] = keep
                    dropped += 1
    return dropped


# 所持金の読み書きはローダの語彙（`309_` / `901_` と共有。TECH.md §3.2.3）。
# ローダ版は `bool` も弾く ― `True` は `int` なので、素朴な判定だと
# `gold = True` を所持金 1 として通してしまう。
gold_of = ui.gold_of


def add_gold(app, amount):
    """報酬を渡す。**DSL の `gold_add` ではなくこちらで払う。**

    DSL 側で払うと `prices` / `payouts` の宣言と経済の規則
    （仕様書 DESIGN RULES 3）に従うことになり、事件の報酬という
    一度きりの支払いには噛み合わない。`309_` が実証した経路で直接渡す。
    """
    return ui.add_gold(app, amount)


# --------------------------------------------------------------------------
# NPC を作る
# --------------------------------------------------------------------------
#: 生成直後の NPC が実際に持っていた形（実機で観測。
#: `out/character_state.log` の `generate_character('35')`）。
#: **HP・スキル・装備・画像は空でよい** ― ゲームが会話や戦闘の直前に
#: `ensure_npc_detail_generated` で埋める。だから MOD は軽く作れる。
#:
#: ##### 並び順は「合っていればよい」ではなく、**この順でなければならない**
#:
#: セーブは辞書をそのまま JSON に落とすので、**ここに書いた順がそのまま
#: ファイルの行順になる。**そしてセーブを読む側には、項目を上から順に並べて
#: 見せる道具がある（別途あるセーブエディタ）。順番が変わると、項目は全部
#: 揃っているのに**表示が崩れる。**
#:
#: だから項目は「揃えた」だけでは足りない。**ゲーム自身が書く順と1つずつ
#: 一致させる。**下の並びは実際のセーブから起こしたもの:
#:
#:     saves/<世界名>/savedata_plain.json の npcs
#:     51体中50体がこの33項目・この順（残る1体は speech_style が無いだけで
#:     順番は同じ）。プリセットの world_data は先頭29項目までで、後ろの4つ
#:     （current_area / current_location / knowledge /
#:     display_position_in_battle）は遊び始めてから増える。
#:
#: **`make_npc` はこの並びを崩さない。**`dict.update` は既にある鍵の位置を
#: 動かさないので、**33項目を漏らさず先に持っている**限り順番は保たれる。
#: 逆に1つでも欠けていると、その項目だけが末尾に足されて並びが壊れる。
#: 項目を足すときは必ずこの表の正しい位置へ差し込むこと ― 末尾に足さない。
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
    "ability_scores": {},
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
    # **リストであってディクショナリではない。**実際のセーブでは `[]`。
    "knowledge": [],
    "display_position_in_battle": None,
}

#: セーブに書くときの項目の並び。`NEW_NPC_TEMPLATE` の定義順がそのまま
#: 正解なので、そこから起こす（二重に持つと必ず片方が古くなる）。
NPC_FIELD_ORDER = tuple(NEW_NPC_TEMPLATE)


def character_ids(app):
    """実行時の名簿に載っている id。"""
    characters = getattr(getattr(app, "world", None), "characters", None)
    return set(characters) if isinstance(characters, dict) else set()


def npc_stores(app, max_depth=2):
    """NPC の素データが入っていそうな辞書を**全部**集める。

    `[(どこにあるか, 辞書), ...]`。

    ##### なぜ探すのか

    `World.generate_character(id, value)` は id で素データを引くが、
    **どこから引くのかが分からない。**`app.world_dict['npcs']` に書いてから
    呼んでも `KeyError` のままだった（実測）。`World.__init__` は
    `save_data_dict` を受け取っているので、`app.world_dict` とは別の辞書を
    握っている可能性が高い。

    2回続けて「ここだろう」と決め打って外しているので、**決め打ちをやめる。**
    `302_` がパーティ名簿でやっているのと同じ手（`ui.party_stores`）で、
    心当たりを全部集めて全部に書く。余分に書いても、同じ id に同じ値が
    入るだけで害が無い。
    """
    seen, out = set(), []
    known = character_ids(app)

    def looks_like_npcs(value):
        """既存の character id が鍵になっている辞書か。"""
        if not isinstance(value, dict) or not value:
            return False
        keys = {str(key) for key in value}
        return bool(known & keys)

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

    置き場所は1つではない（`npc_stores`）ので、掃除の対象を探すときは
    全部を重ねて見る。**書くのには使わない** ― 書くほうは
    `npc_stores` を回して全部に書く。
    """
    merged = {}
    for where, store in npc_stores(app):
        if "characters" in where.rsplit(".", 1)[-1]:
            continue                    # 実行時の名簿。素データではない
        for npc_id, data in store.items():
            if isinstance(data, dict):
                merged.setdefault(str(npc_id), data)
    return merged


def free_id(app, npcs):
    """まだ使われていない id。**セーブと実行時の両方を見る。**

    ゲームは遊んでいる最中にも NPC を作る（新しい町の生成で 37〜46 が
    生えた。実測）。片方だけ見ると、その採番と衝突する。
    """
    largest = -1
    for key in list(character_ids(app)) + list(npcs or {}):
        try:
            largest = max(largest, int(str(key)))
        except (TypeError, ValueError):
            continue
    return str(largest + 1)


def make_npc(app, spec, area, facility, write=None):
    """NPC を1体作って世界に入れる。

    ##### なぜ自分で書くのか（実測）

    2つの入口を試して、どちらも作る側ではなかった。

    | 試したもの | 結果 |
    |---|---|
    | `World.generate_character(id, value)` | `KeyError: '<id>'`。**セーブの `npcs` を id で引く側**で、無い id は引けない |
    | `save_area_json:generate_npc(...)` | 例外は出ないが、**`world_dict` にも `world.characters` にも何も現れない**。返るのは `world_dict` そのもの |

    後者を「作れた」と読んでいたのは誤りで、同じ瞬間にゲームが別の NPC を
    作っていたのを拾っていた。世界丸ごとのダンプを検索しても、渡した名前は
    1件も入っていない。

    `KeyError` の出方が答えを教えている ― **`generate_character` は
    `world_dict['npcs'][id]` を読む。**ならば先にそこへ書けばよい。

      1. 空いている id を取る（ゲームの採番と衝突しないよう両方を見る）
      2. `world_dict['npcs'][id]` にセーブの形で書く
      3. `World.generate_character(id, data)` で実行時の `Character` を作る
      4. `move_npc_to_facility` で施設に置く（`302_` が実証済みの経路）

    形は実機で観測した生成直後の NPC に合わせてある（`NEW_NPC_TEMPLATE`）。

    ##### セーブに残る

    NPC は独自キーではなくゲーム自身の項目なので壊れないが、MOD を外しても
    世界に残る。README の「MOD を消せば完全に元通り」からは外れる性質。

    失敗しても壊れないように、途中で落ちたら**書いた分を取り消す**。
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

    npc_id = free_id(app, npcs)
    # **並び順を崩さない。**テンプレートが33項目を全部持っているので、
    # 上書きだけしている限り位置は動かない（`dict.update` は既存の鍵を
    # 動かさない）。テンプレートに無い鍵だけが末尾に足されて並びを壊すので、
    # そうなったら記録に残す ― 黙って通すと、セーブを上から順に見せる道具の
    # 表示が崩れてから気づくことになる。
    data = dict(NEW_NPC_TEMPLATE)
    fields = {key: value for key, value in spec.items()
              if key not in ("traits", "tell")}
    stray = [key for key in fields if key not in NEW_NPC_TEMPLATE]
    if stray and write:
        write("make_npc: spec has field(s) the template does not know; they "
              "will be appended and break the save field order: {}"
              .format(sorted(stray)))
    data.update(fields)
    data["id"] = npc_id
    data["initial_location"] = {"area": str(area), "node": None,
                                "facility": str(facility)}
    data["config"] = dict({"level_of_detail": 2, "is_player": False,
                           "is_dead": False, "difficulty_level": 4},
                          **dict(spec.get("config") or {}))

    # **心当たりの辞書すべてに書く。**どこから引かれるか分からないので、
    # 1箇所に賭けない。同じ id に同じ値が入るだけなので、
    # 余分に書いても害は無い。
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
        character = character_of(app, npc_id)
    if character is None:
        # **最後の手段: `Character` を直に組む。**コンストラクタは
        # `scripts.characters` に完全な署名で露出している（リコンより）。
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

    placed = _place(app, npc_id, character, area, facility, write)
    if write:
        write("make_npc: created {} {!r} at {}/{} (placed={} npcs={})".format(
            npc_id, data.get("name"), area, facility, placed, len(npcs)))
    return npc_id


#: `Character.__init__` に実在する引数だけ（リコンの署名より）。
#: **セーブの項目名とは違うものがある** ― `ability_scores` は
#: `original_ability_scores`、`knowledge` は `knowledges`。
#: `traits` は Character 側にもあるが**意味が違う**（こちらの推理用の
#: 特徴は渡さない。`make_npc` が spec から外している）。
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

    載らなくても事件は成立する（告発は一覧から選ぶ）ので、失敗しても
    NPC は捨てない。ただし会話には出てこなくなるので記録は残す。
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
