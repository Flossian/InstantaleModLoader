# -*- coding: utf-8 -*-
r"""持ち物（`Character.inventory`）の読み書き。

**`Character.inventory` は入れ物のオブジェクト**（`scripts.characters:ItemContainer`）で、
その `.inventory` が `{鍵: Item}` の辞書（`402_` が実機で確かめた形）。
品そのものも JSON に落ちないオブジェクトなので、控えに入れるときは
**セーブと同じ形の辞書**に均し、戻すときはゲーム自身に作り直させる
（`InstantaleApp.generate_item_from_dict`。`914_` が実機で通した経路）。

素直に `getattr(character, "inventory")` を控えると、入れ物ごと落ちて
**持ち物が丸ごと消える**（実機 2026-09-14。`915_` の店の主人に品が並んでいても控えは空だった）。

記録は GAME.md §2.13（セーブの1件の形）と TECH.md §5.7。
"""

import sys

from . import log_exc

#: セーブの1件が持つ項目（`914_` の保管庫が実機で往復させた12項目）。
ITEM_FIELDS = ("name", "item_type", "attributes", "description", "value", "rarity",
               "skill", "upgrade_level", "width_slots", "height_slots", "image_src",
               "grid_pos")


def character_class():
    """`scripts.characters.Character`。引けなければ None。"""
    module = sys.modules.get("scripts.characters")
    return getattr(module, "Character", None) if module is not None else None


def inventory_of(owner):
    """持ち物の実体 `{鍵: Item}`。辞書を直接持つ形にも備える。読めなければ None。"""
    if owner is None:
        return None
    holder = getattr(owner, "inventory", None)
    if isinstance(holder, dict):
        return holder
    inner = getattr(holder, "inventory", None)
    return inner if isinstance(inner, dict) else None


def _jsonable(value):
    """控えに入れてよい値か。JSON に落ちるものだけ。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_jsonable(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _jsonable(v) for k, v in value.items())
    return False


def _size_of(item, index):
    """幅・高さ。`width_slots` / `height_slots` が無ければ `size` から読む。"""
    name = ("width_slots", "height_slots")[index]
    value = getattr(item, name, None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    size = getattr(item, "size", None)
    if isinstance(size, (list, tuple)) and len(size) > index:
        value = size[index]
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 1


def to_dict(item):
    """品1つをセーブと同じ形の辞書にする。落とせなければ None。

    落とせない品（属性が JSON にならないもの）は**控えない**。
    控えに入らない品を持ち主から抜くと、その品は世界から消える。
    """
    if item is None:
        return None
    data = {}
    for field in ITEM_FIELDS:
        if field == "width_slots":
            data[field] = _size_of(item, 0)
            continue
        if field == "height_slots":
            data[field] = _size_of(item, 1)
            continue
        value = getattr(item, field, None)
        if isinstance(value, tuple):
            value = list(value)
        if not _jsonable(value):
            return None
        data[field] = value
    return data


def to_records(owner, write=None):
    """持ち主の持ち物を控えの形にする。`({鍵: 辞書}, 落とせなかった鍵)`。

    入れ物が読めなければ `(None, [])`（控えを空で上書きしない。呼ぶ側が見分けられる）。
    """
    inv = inventory_of(owner)
    if inv is None:
        return None, []
    records, lost = {}, []
    for key, item in inv.items():
        data = to_dict(item)
        if data is None:
            lost.append(str(key))
            continue
        records[str(key)] = data
    if lost and write:
        write("items: {} item(s) could not be written down: {}".format(
            len(lost), ", ".join(sorted(lost))))
    return records, lost


def rebuild(app, owner, records, write=None):
    """控えから持ち主の持ち物を作り直す。作れた件数を返す。

    作るのはゲーム自身の `generate_item_from_dict`。
    戻り値で受け取れる実装と、持ち物へ直に入れる実装（`generate_item_in_shopping` と
    同じ形。GAME.md §2.13.1.2）の両方に備える。
    """
    inv = inventory_of(owner)
    if inv is None or not isinstance(records, dict) or not records:
        return 0
    make = getattr(app, "generate_item_from_dict", None)
    if not callable(make):
        if write:
            write("WARN items: generate_item_from_dict is not available")
        return 0
    made = 0
    for key, data in sorted(records.items()):
        if not isinstance(data, dict):
            continue
        try:
            item = make(dict(data), str(key), owner)
        except Exception:
            log_exc("items: cannot rebuild {}".format(key))
            if write:
                write("WARN items: cannot rebuild {} {!r}".format(key, data.get("name")))
            continue
        if item is not None and inv.get(str(key)) is not item:
            inv[str(key)] = item
        if str(key) in inv:
            made += 1
        elif write:
            write("WARN items: {} did not land in the owner".format(key))
    return made
