# -*- coding: utf-8 -*-
r"""建物そのもの。街のノードへ施設を1つ足し、要らなくなったら外す。

街の作りは `エリア → ノード → 施設` の入れ子で、施設どうしは `connections`
（同じノードの中の施設 id の配列）で繋がっている（GAME.md §2.7）。
実データの街は `entrance` から `ward`（広場）へ入り、宿・ギルド・役場は
その広場から枝分かれしている:

    entrance(0) ─ ward(1) ─ inn(2) / guild(3) / administrative_office(4) / ...
                      └──── location(9)      ← ここへ並べる

したがって建物を1軒足すのは、

    1. 施設の素データ（8項目）を `location` の形で組む
    2. `Facility(app, parent_node, facility_data)` で実行時の施設にする
    3. ノードの `facilities` に入れ、広場の `connections` に id を足す

の3手で済む。素データの形は実セーブの `location` そのまま
（`tier` は null、`owner` は null、`config` は `level_of_detail` だけ）。

## セーブには書かない

`world_data.json` は世界の骨格で、書き戻すと同じ世界の別のキャラクタにまで乗る
（TECH.md §3.11）。`save_data_dict` にも書かない。
触るのは実行中の `Area` / `Node` / `Facility` だけで、
契約の控えは `state\real_estate\<世界名>.json` に持ち、ロードのたびに当て直す
（`325_road_opening` が道の開通で採っているのと同じ形）。

**MOD を外せば街は素のまま**になる。建物も、そこへ繋がる道も残らない。

## id はゲームの台帳から採る

施設 id は土地の中でしか一意でないが、採番は世界の `index['facility']` が持っている。
ここを進めずに `max + 1` で採ると、ゲームが次に施設を作ったとき同じ番号を踏む
（NPC で実際に起きた事故。GAME.md §2.23）。`ids.claim` を通す。

## 接続は新しい list に差し替える

実行時の `connections` が骨格側の list と同じものを指している可能性があるので、
中身を書き換えず `list(...)` を作って属性ごと差し替える（`325_` / `321_` と同じ）。
"""

import sys

from instantale_modloader import ui


#: 足す施設の種類。主のいない場所（`ward` / `location` / `entrance` / `exit` の仲間）で、
#: ゲーム側の出し分け（売買・訓練・宿泊）に一切引っ掛からない。
#: 中の選択肢はこの MOD が足す（GAME.md §2.7 の一覧）。
FACILITY_TYPE = "location"

#: 建物を繋ぐ先に選ぶ施設の種類。この順に探す。
#: **エリアの入口を最優先**にする。
#: 街に着いて最初に立つのが入口で、そこから各区画（`ward`）へ枝分かれしている。
#: 自分の家は区画の奥ではなく、着いてすぐ入れる場所に置く。
HUB_TYPES = ("entrance", "ward")

#: 施設の素データの項目と並び。実セーブの `location` と同じ順
#: （順番が変わると、項目が揃っていてもセーブエディタの表示が崩れる。GAME.md §2.23）。
FACILITY_FIELDS = ("name", "id", "description", "facility_type", "tier", "owner",
                   "connections", "config")


def main_module():
    """ゲーム本体のモジュール（`instantale.py`）。"""
    return sys.modules.get("__main__")


def facility_class():
    """`__main__.Facility`。引けなければ None（呼ぶ側はそこで諦める）。"""
    return getattr(main_module(), "Facility", None)


def node_id_of(node):
    return str(getattr(node, "id", "")) if node is not None else ""


def facility_id_of(facility):
    return str(getattr(facility, "id", "")) if facility is not None else ""


def connections_of(facility):
    """施設の接続。読めなければ空の配列。"""
    value = getattr(facility, "connections", None)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def link(facility, other_id):
    """接続を1本足す。既に在れば何もしない。足したら True。"""
    if facility is None or not other_id:
        return False
    other_id = str(other_id)
    current = connections_of(facility)
    if other_id in current:
        return False
    try:
        facility.connections = current + [other_id]
    except Exception:
        return False
    return True


def unlink(facility, other_id):
    """接続を1本外す。外したら True。"""
    if facility is None or not other_id:
        return False
    other_id = str(other_id)
    current = connections_of(facility)
    if other_id not in current:
        return False
    try:
        facility.connections = [c for c in current if c != other_id]
    except Exception:
        return False
    return True


def hub_of(area):
    """建物を繋ぐ先。`(ノード, 施設)`。見つからなければ `(None, None)`。

    ノード自身が「ここが入口だ」と持っている（実データの `entrance_facility`）ので、
    まずそれを引く。種類で探すのはその後（入口を持たないノードのため）。
    """
    for node in ui.nodes_of(area):
        entrance_id = getattr(node, "entrance_facility", None)
        if entrance_id is None:
            continue
        facility = ui.facilities_of(node).get(str(entrance_id))
        if facility is not None and ui.facility_type_of(facility) in HUB_TYPES:
            return node, facility
    for kind in HUB_TYPES:
        for node in ui.nodes_of(area):
            for facility in ui.facilities_of(node).values():
                if ui.facility_type_of(facility) == kind:
                    return node, facility
    return None, None


def hub_in(area, node_id, facility_id):
    """控えに書いてある広場を引き当てる。引けなければ `hub_of` に落ちる。"""
    if node_id and facility_id:
        for node in ui.nodes_of(area):
            if node_id_of(node) != str(node_id):
                continue
            facility = ui.facilities_of(node).get(str(facility_id))
            if facility is not None:
                return node, facility
    return hub_of(area)


def rehome(app, area, facility_id, hub_id, write=None):
    """建っている建物を、いま選ぶべき繋ぎ先へ繋ぎ直す。`(ノード, 繋ぎ先)`。

    繋ぎ先の選び方が変わったとき（`ward` から入口へ移したとき）、
    **既に建っている家も次のロードで移る**ようにするための道。
    変わっていなければ何もしない。
    """
    facility, node = ui.find_facility(area, facility_id)
    if facility is None:
        return None, None
    new_node, new_hub = hub_of(area)
    if new_hub is None:
        return node, None
    new_id = facility_id_of(new_hub)
    if str(hub_id or "") == new_id:
        return node, new_hub
    old_hub = ui.facilities_of(node).get(str(hub_id)) if node is not None else None
    if old_hub is not None:
        unlink(old_hub, facility_id)
    unlink(facility, hub_id)
    link(new_hub, facility_id)
    link(facility, new_id)
    if write:
        write("rehomed: id={} {!r}({}) -> {!r}({})".format(
            facility_id, getattr(old_hub, "name", ""), hub_id,
            getattr(new_hub, "name", ""), new_id))
    return (new_node or node), new_hub


def template(facility_id, name, description, hub_id):
    """施設の素データ。実セーブの `location` と同じ8項目・同じ並び。"""
    return {
        "name": name,
        "id": str(facility_id),
        "description": description,
        "facility_type": FACILITY_TYPE,
        "tier": None,
        "owner": None,
        "connections": [str(hub_id)],
        "config": {"level_of_detail": 0},
    }


def existing(area, facility_id):
    """その建物が既にこの世界に立っているか。`(施設, ノード)`。"""
    return ui.find_facility(area, facility_id)


def build(app, area, facility_id, name, description, node, hub, write=None):
    """建物を1軒立てる。立った施設を返す。立てられなければ None。

    `node` / `hub` は `hub_of` / `hub_in` が返したもの。
    ノードの `facilities` に入れ、広場との接続を両側に張る。
    """
    cls = facility_class()
    if cls is None:
        if write:
            write("WARN build: __main__.Facility is not available")
        return None
    if node is None or hub is None:
        if write:
            write("WARN build: no hub facility in area {!r}".format(ui.area_id_of(area)))
        return None
    facilities = ui.facilities_of(node)
    if not isinstance(facilities, dict):
        if write:
            write("WARN build: node {!r} has no facilities dict".format(node_id_of(node)))
        return None
    hub_id = facility_id_of(hub)
    data = template(facility_id, name, description, hub_id)
    try:
        facility = cls(app, node, data)
    except Exception:
        if write:
            write("WARN build: Facility(...) raised for {!r}".format(facility_id))
        raise
    facilities[str(facility_id)] = facility
    link(hub, facility_id)
    link(facility, hub_id)
    if write:
        write("built: {!r} id={} in area {!r} node {!r} off hub {!r}({})".format(
            name, facility_id, ui.area_id_of(area), node_id_of(node),
            getattr(hub, "name", ""), hub_id))
    return facility


def demolish(app, area, facility_id, node_id=None, hub_id=None, write=None):
    """建物を取り壊す。外せたら True。

    広場との接続を切り、ノードの `facilities` から落とす。
    **プレイヤーがその建物の中に居るときは呼ばない**（呼ぶ側が確かめる）。
    居るまま外すと、出口の無い施設に立ったままになる。
    """
    facility, node = ui.find_facility(area, facility_id)
    if facility is None:
        return False
    hub = None
    if hub_id:
        hub = ui.facilities_of(node).get(str(hub_id)) if node is not None else None
    if hub is None:
        _hub_node, hub = hub_in(area, node_id or node_id_of(node), hub_id)
    if hub is not None:
        unlink(hub, facility_id)
    facilities = ui.facilities_of(node)
    if isinstance(facilities, dict):
        facilities.pop(str(facility_id), None)
    if write:
        write("demolished: id={} in area {!r}".format(facility_id, ui.area_id_of(area)))
    return True


def id_list_of(holder, *names):
    """`characters` / `resident_npcs` のような id の配列を読む。重複は落とす。"""
    found = []
    for name in names:
        value = getattr(holder, name, None)
        if not isinstance(value, (list, tuple, set)):
            continue
        for item in value:
            key = str(item)
            if key and key not in found:
                found.append(key)
    return found


#: 契約の相手（役場）の `facility_type`。その主が物件の大家になる。
OFFICE_FACILITY_TYPE = "administrative_office"


def office_owner(area, roster):
    """その土地の役場の主の id。名簿に居なければ None。"""
    for node in ui.nodes_of(area):
        for facility in ui.facilities_of(node).values():
            if ui.facility_type_of(facility) != OFFICE_FACILITY_TYPE:
                continue
            owner = getattr(facility, "owner", None)
            if owner is not None and str(owner) in roster:
                return str(owner)
    return None


def owner_candidate(app, area=None, facility=None, write=None):
    """`facility.owner` に据えられる character id。見つからなければ None。

    ゲームの宿泊は**主を世界の名簿から引く**（`world.characters[owner]`）。
    主のいない施設で起こすと `KeyError: None` でワーカースレッドごと落ち、
    画面は「…」のまま戻らない（実機 2026-09-11。DOC.md §3.2）。

    据えるのは**その土地の役場の主**。
    物件を貸したのも売ったのも役場なので、大家として立つのはそこの役人になる。
    引けない土地のために、その建物・その土地の住人へ順に落ちる
    （**名簿に在る id しか返さない**。在らぬ id を据えると同じ `KeyError` になる）。
    """
    roster = getattr(getattr(app, "world", None), "characters", None)
    if not isinstance(roster, dict) or not roster:
        if write:
            write("WARN owner: the world has no character roster")
        return None
    key = office_owner(area, roster)
    if key is not None:
        return key
    for holder in (facility, area):
        if holder is None:
            continue
        for item in id_list_of(holder, "characters", "resident_npcs",
                               "adventurer_npcs"):
            if item in roster:
                if write:
                    write("owner: no clerk in the office; borrowing {!r} from {}"
                          .format(item, type(holder).__name__))
                return item
    key = str(next(iter(roster)))
    if write:
        write("owner: nobody else was reachable; borrowing {!r}".format(key))
    return key


def player_is_inside(app, facility_id):
    """プレイヤーがその建物の中に立っているか。

    `player.location` は施設のオブジェクトとは限らず、ロード直後は id の文字列
    （GAME.md §2.7）。どちらでも見分ける。
    """
    location = getattr(getattr(app, "player", None), "location", None)
    if location is None:
        return False
    if isinstance(location, (str, int)):
        return str(location) == str(facility_id)
    return facility_id_of(location) == str(facility_id)


def move_spec_args(area, facility_id):
    """`MovePhaseManager` に渡す引数。`[ノードid, 施設id, エリアid]`。

    実測した「出る」のボタンと同じ形（GAME.md §2.2）。
    引き当てられなければ None（そのときはボタンを作らない）。
    """
    facility, node = ui.find_facility(area, facility_id)
    if facility is None or node is None:
        return None
    return [node_id_of(node), str(facility_id), ui.area_id_of(area)]
