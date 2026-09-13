# -*- coding: utf-8 -*-
r"""大家（建物の主）を引く。

建物そのものはローダの `modfacility`（TECH.md §5.8）が建てる。
ここに残るのは「その建物の主に誰を据えるか」だけで、これは施設ではなく人の話。

ゲームの宿泊は**主を世界の名簿から引く**（`world.characters[owner]`）。
主のいない施設で起こすと `KeyError: None` でワーカースレッドごと落ち、
画面は「…」のまま戻らない（実機 2026-09-11。DOC.md §3.2）。
だから滞在のあいだだけ主を据える。
"""

from instantale_modloader import ui

#: 契約の相手（役場）の `facility_type`。その主が物件の大家になる。
OFFICE_FACILITY_TYPE = "administrative_office"


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


def at_facility_type(app, kind):
    """いま立っているのがその種類の施設か（役場の窓口を出すため）。"""
    location = getattr(getattr(app, "player", None), "location", None)
    if location is None or isinstance(location, (str, int)):
        return False
    return ui.facility_type_of(location) == kind
