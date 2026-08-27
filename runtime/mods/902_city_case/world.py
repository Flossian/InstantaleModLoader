# -*- coding: utf-8 -*-
"""ゲームのどこに何があるか。この MOD の方針は持たない。

「誰を犯人にするか」「いくら払うか」は入口が決める。
ここは引き当てと書き込みの手順だけを知っている。
`307_` の `world.py` と同じ立場（TECH.md §3.1.1.1）。

##### 実測に基づく前提（GAME.md §2.7 / §2.22）

- 施設は `areas[id].nodes[nid].facilities[fid]` の入れ子
- `Facility.owner` は character の id（str）。
  主を消すと店が壊れる
- `Facility.characters` は id の配列。
  重複が入りうる
- 死亡の印は `Character.config['is_dead']`。
  立てても名簿からは外れない（読む側が飛ばす）
"""

import sys

from instantale_modloader import frames, ui
from instantale_modloader.npcs import (
    CHARACTER_KWARGS, NEW_NPC_TEMPLATE, NPC_FIELD_ORDER,
    character_ids, free_id, npc_stores, save_npcs)
from instantale_modloader.npcs import make_npc as npcs_make_npc
from instantale_modloader.state import UNKNOWN_WORLD, world_key


def world_name(app):
    """控えを紐付ける鍵。引けなければ空文字（そのときは紐付けない）。

    見分け方はローダの語彙（`state.world_key`）。
    ここに写した版は `app.world` の属性しか見ておらず、ロード直後は必ず空文字になっていた。
    そのとき `app.world` はまだ組み上がっておらず、世界名はセーブ側（`world_dict["world_data"]`）にしか無い。
    空文字は呼び側で「どの世界か分からない」の合図なので、控えの世界照合（`current`）と後始末（`sweep`）が **ロードした直後だけ黙って素通り**していた（TECH.md §3.2.3・`state.py`）。

    空文字の契約はそのまま保つ。
    ローダは読めないとき `"_"` を返すので、ここで戻し直す。
    この MOD は「分からないなら紐付けない」に倒す作りで、知らない世界の控えを1つの鍵にまとめてしまうより安全側。
    """
    key = world_key(app)
    return "" if key == UNKNOWN_WORLD else key


def current_facility(app):
    """いま居る施設。id で持っている場合も引き当てる。

    `player.location` は施設のオブジェクトとは限らない。
    セーブでは施設 id の文字列（`'106'`）で、遊んでいる最中にその施設へ入ると Facility そのものに置き換わる。

    ロード直後は前者のままなので、`facility_type_of` が空文字を返し、**ギルドに立っているのにボタンが出なかった**（実機）。
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
    """施設の主を務めている character の id。この人たちは使わない。

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

    手がかりの置き場所はここから選ぶ。
    町の構成は世界ごとに違い、闇市や診療所が無い町もある。
    無い施設に手がかりを置くと、その事件は永久に解けなくなる。
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

    生成した NPC を町へ散らすのに使う。
    全員が同じ場所に立っていると、その施設だけ不自然に人が増える。
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
    """その土地の、その種類の施設の主の id。無ければ空文字。

    手がかりの証言者はこの人物。
    **事件を組むときに控えておく**。
    そうすれば「誰が何を知っているか」が id の突き合わせだけで決まり、どう話しかけられたかに依存しなくなる。
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
    """文字列として取り出して詰める。取れなければ空文字。

    渡ってくるのはセーブから読んだ値なので、`MISSING`・`None`・辞書・リストのどれでもありうる。
    ここで受け止めておかないと、頼み文を組む途中で落ちて事件が始まらなくなる（描写の都合で遊びが止まる）。
    """
    if not isinstance(value, str) or value == frames.MISSING:
        return ""
    got = " ".join(value.split())
    return got if len(got) <= limit else got[:limit] + "…"


def area_name(app, target_area_id, limit=40):
    """町の名前。施設の名前ではない。

    以前は「町」として `place_name(..., 'guild')` を渡していた。
    それはギルドの名前で、実機の頼み文に `【町】鉄錆の徴収所` と出ていた。
    町の名前は `Area.name` にある。
    """
    area = ui.world_areas(app).get(str(target_area_id))
    value = frames.attr(area, "name") if area is not None else None
    return _short(value, limit)


def area_notes(app, target_area_id, limit=240):
    """その町の説明。AI に「どこの話か」を教えるために渡す。

    `Area.descriptions` は `overview` / `facilities` を持つ辞書（実セーブで確認）。
    長いので頭だけ使う。
    事件の文章を書かせるのに要るのは「どういう町か」であって、町の全部ではない。
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

    実在の場所を渡すと「その町の事件」になる。
    架空の宿屋を書かれるより、いま歩いている町の宿屋の名前を出したい。
    そのほうが地に足が付く。
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
    """その人物がいま居る施設の名前。分からなければ空文字。

    施設の名簿を舐めて探す。
    **置いたときの id を控えるのではなく、今を見る**。
    ゲームは NPC を動かすことがあるので、控えは古くなりうる。
    名簿は `Facility.characters` に居る（`move_npc_to_facility` が入れる先）。
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
    """その町に居る人物の名前。同じ名前を作らせないために渡す。"""
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
    """その土地でその種類の施設の名前。無ければ None。

    案内に使う。
    「宿屋へ行け」ではなく「『欠けた月亭』へ行け」と言えると、プレイヤーは画面の移動先の文字列とそのまま突き合わせられる。
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
    """退場させる。名簿からは外さない（外すと参照が切れる）。

    実測（VERIFICATION_LOG.md §2.29 / §2.30）: 印を立てても施設の名簿には残り、それでもゲーム内では会話にも呼び出しにも出てこない。
    読む側が飛ばしているので、こちらは印だけ立てればよい。
    戻すこともできる。
    """
    character = character_of(app, npc_id)
    config = config_of(character)
    if config is None:
        return False
    config["is_dead"] = bool(value)
    return True


def remove_npc(app, npc_id, write=None):
    """NPC を世界から完全に消す。`set_dead` と違い、痕跡を残さない。

    ##### なぜ印を立てるだけでは足りないのか

    `set_dead` は名簿に残す（上）。
    事件が1件で終わるならそれでよかったが、繰り返し遊ぶとセーブが太り続ける。
    実測:

    - 生成直後の NPC が約 1.4KB、ゲームが中身を埋めると 3〜8KB
    - `npcs` はセーブ全体の約2割（実セーブ 790KB / 51体で計測）
    - 1件につき4体。
      10件遊べば 40体増える

    害はバイト数より町が見知らぬ人で埋まることのほうが大きい。
    人数は土地の人物一覧にも、ゲームが組む文脈にも効く。

    ##### 消せる根拠

    セーブの施設は名簿を持っていない。
    実セーブを見ると `facility` の項目は `name` / `id` / `description` / `facility_type` / `owner` / `connections` / `config` だけで、誰が居るかは NPC 側の `location` から実行時に組み直されている。
    だから `npcs` から消せば、次に読み込んだ世界には現れない。
    クエストも `client_name`（名前の文字列）で持っていて id では参照しない。

    実行時のぶん（`world.characters` と、施設に組まれた名簿）も同時に外す。
    こちらを残すと、そのセッションの間だけ幽霊が立ち続ける。

    ##### 消さない場合

    パーティーに居る者は消さない。
    プレイヤーが連れ歩いている相手を消すと、パーティーの参照が切れて何が起きるか分からない。
    事件の容疑者が仲間になる経路は用意していないが、他の MOD やゲーム側の都合で入りうる。
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

    セーブには無い構造なので、消し忘れてもファイルは汚れない。
    ただしそのセッションの間だけ、消したはずの人物が施設に立ち続ける。
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
# ローダ版は `bool` も弾く。
# `True` は `int` なので、素朴な判定だと `gold = True` を所持金 1 として通してしまう。
gold_of = ui.gold_of


def add_gold(app, amount):
    """報酬を渡す。DSL の `gold_add` ではなくこちらで払う。

    DSL 側で払うと、`prices` / `payouts` の宣言と経済の規則（仕様書 DESIGN RULES 3）へ従うことになる。
    事件の報酬は一度きりの支払いなので、そこへは噛み合わない。
    `309_` が実証した経路で直接渡す。
    """
    return ui.add_gold(app, amount)


# --------------------------------------------------------------------------
# NPC を作る
# --------------------------------------------------------------------------
# 手順そのもの（素データの置き場所・採番・ひな型・組み立て・配置）は、
# `320_` も同じものを要るようになった時点でローダへ移した
# （写して回るものはローダの語彙。TECH.md §3.2.3）。
# 実測の経緯は DOC.md §3 と `instantale_modloader/npcs.py` の docstring。
# 上の import はこのファイルの既存の呼び名を保つためのもの。


def make_npc(app, spec, area, facility, write=None):
    """NPC を1体作って世界に入れる。作れたら id、作れなければ None。

    手順はローダ（`npcs.make_npc`）。
    ここに残るのはこの MOD の判断だけ:
    spec の推理用の項目（traits / tell）は NPC の項目に混ぜない。
    `config` は既定（`npcs.DEFAULT_CONFIG`）の上に重ねる。
    """
    fields = {key: value for key, value in spec.items()
              if key not in ("traits", "tell", "config")}
    return npcs_make_npc(app, fields, area, facility,
                         config=spec.get("config"), write=write)
