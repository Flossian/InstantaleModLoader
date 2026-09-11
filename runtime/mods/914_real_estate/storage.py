# -*- coding: utf-8 -*-
r"""保管庫。預けた品を控えに落とし、開くたびに作り直す。

保管庫の中身は**持ち主を作らずに持つ**。
世界に NPC を1人増やせばセーブの正規の形で残せるが、そのために人が1人生まれ、
契約が切れたときに消す責任までこちらが持つことになる。
代わりに採ったのは、

    預ける   プレイヤーの持ち物から抜き、セーブと同じ形の辞書にして控えへ
    開く     控えから作り直し、**その窓の間だけ生きる持ち主**に持たせる
    取り出す 持ち主から抜き、プレイヤーの持ち物へ

という形。窓の持ち主（`holder`）は `Character` のインスタンスだが、
`world.characters` にも `save_data_dict['npcs']` にも登録しない。
セーブには一切現れず、窓を閉じれば消える。

## 品の形はセーブと同じ

実セーブの `player_data.inventory` の1件（GAME.md §2.13）:

    "item_19": {"name": ..., "item_type": "weapon", "attributes": {...},
                "description": ..., "value": 74, "rarity": "common",
                "skill": {...} or null, "upgrade_level": 0,
                "width_slots": 1, "height_slots": 1,
                "image_src": "Assets/images/...", "grid_pos": [1, 5]}

作り直すのはゲーム自身の `InstantaleApp.generate_item_from_dict(item_dict, item_id, obtainer)`。
こちらで `Item(...)` を組まないのは、引数の並びが更新で変わりうるため。

## 窓はゲームの売買窓を借りる

`toggle_twin_inventory_window(左, 右, 左の見出し, 場面名)`。
場面名をゲームに無い名前（`real_estate_storage`）にしておくと、
本体の売買処理（値段・所持金）がこの窓に掛からない（`402_` と同じ借り方）。

本体の `InventoryItem.change_inventory` は**画面側の登録を動かすだけ**なので、
持ち物の実体（`inventory` の辞書）・`Item.id`・`Item.obtainer` はこちらで揃える。
"""

import sys

from instantale_modloader import frames


#: セーブの持ち物1件の項目と並び（実セーブ。GAME.md §2.13）。
#: 並びを保つのは、控えがそのままセーブと見比べられる形であってほしいから。
ITEM_FIELDS = ("name", "item_type", "attributes", "description", "value", "rarity",
               "skill", "upgrade_level", "width_slots", "height_slots", "image_src",
               "grid_pos")

#: ゲームに無い場面名。本体の売買処理をこの窓に掛けないための札（`402_` と同じ）。
SITUATION = "real_estate_storage"

#: 能力値の6つの鍵（GAME.md §2.23）。
#: `Character.__init__` は `original_ability_scores` を**添字で読む**ので、
#: `None` のまま渡すと `TypeError: 'NoneType' object is not subscriptable` で落ちる
#: （実機 2026-09-11。窓が開かなかった原因）。値は `None` でよいが、鍵は要る。
ABILITY_KEYS = ("strength", "dexterity", "constitution", "intelligence", "wisdom",
                "charisma")


def character_class():
    """`scripts.characters.Character`。引けなければ None。"""
    module = sys.modules.get("scripts.characters")
    return getattr(module, "Character", None) if module is not None else None


def inventory_dict(owner):
    """持ち物の実体 `{item_id: Item}`。

    `Character.inventory` は `Inventory` オブジェクトで、その `.inventory` が辞書
    （`402_` が実機で確かめた形）。辞書を直接持つ形にも備える。読めなければ None。
    """
    if owner is None:
        return None
    inv = getattr(owner, "inventory", None)
    if isinstance(inv, dict):
        return inv
    inner = getattr(inv, "inventory", None)
    return inner if isinstance(inner, dict) else None


def _plain(value):
    """控えに入れてよい値か。JSON に落ちるものだけを通す。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_plain(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _plain(v) for k, v in value.items())
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


def item_to_dict(item):
    """アイテムをセーブと同じ形の辞書にする。落とせなければ None。

    落とせない品（属性が JSON にならないもの）は**預けさせない**。
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
        if not _plain(value):
            return None
        data[field] = value
    return data


def describe(data):
    """控えの1件をログ用の1行に。"""
    if not isinstance(data, dict):
        return "?"
    return "{!r}({})".format(frames.short(data.get("name"), 40), data.get("item_type"))


def make_holder(app, name, write=None):
    """窓の右側に立てる持ち主。**世界には登録しない**（セーブに現れない）。

    `Character(...)` は全引数に既定値があるが、**既定のままでは組めない**。
    `original_ability_scores` を添字で読むので、`None` のままだと落ちる
    （GAME.md §2.23。実機で窓が開かなかった原因）。
    値は `None` でよいが鍵は要るので、6つを持った辞書を渡す。
    値まで要る版のために、数を入れた形も試す。

    持ち物の入れ物が読めない個体なら None を返す（窓を開かない）。
    """
    cls = character_class()
    if cls is None:
        if write:
            write("WARN storage: scripts.characters.Character is not available")
        return None
    attempts = (
        ("scores=None", {"original_ability_scores": dict.fromkeys(ABILITY_KEYS)}),
        ("scores=10", {"original_ability_scores": dict.fromkeys(ABILITY_KEYS, 10)}),
        ("name only", {}),
    )
    holder, failures = None, []
    for label, extra in attempts:
        try:
            holder = cls(name=name, **extra)
        except Exception as exc:
            failures.append("{} -> {}: {}".format(label, type(exc).__name__, exc))
            continue
        if write and failures:
            write("storage: the holder was built with {} (after {})".format(
                label, "; ".join(failures)))
        break
    if holder is None:
        if write:
            write("WARN storage: cannot build the holder ({})".format(
                "; ".join(failures)))
        return None
    if inventory_dict(holder) is None:
        if write:
            write("WARN storage: the holder has no inventory dict")
        return None
    return holder


def fill(app, holder, records, write=None):
    """控えから持ち主の持ち物を作り直す。作れた件数を返す。

    作るのはゲーム自身の `generate_item_from_dict`。
    戻り値で受け取れる実装と、持ち物へ直に入れる実装（`generate_item_in_shopping`
    と同じ形。GAME.md §2.13.1.2）の両方に備える。
    """
    inv = inventory_dict(holder)
    if inv is None or not isinstance(records, dict):
        return 0
    made = 0
    for key, data in sorted(records.items()):
        if not isinstance(data, dict):
            continue
        try:
            item = app.generate_item_from_dict(dict(data), str(key), holder)
        except Exception:
            if write:
                write("WARN storage: cannot rebuild {} {}".format(key, describe(data)))
            raise
        if item is not None and inv.get(str(key)) is not item:
            inv[str(key)] = item
        if str(key) in inv:
            made += 1
        elif write:
            write("WARN storage: {} did not land in the holder".format(key))
    if write:
        write("storage: rebuilt {}/{} item(s)".format(made, len(records)))
    return made


def dump(holder, write=None):
    """持ち主の持ち物を控えの形にする。`({鍵: 辞書}, 落とせなかった鍵)`。"""
    inv = inventory_dict(holder)
    if inv is None:
        return None, []
    records, lost = {}, []
    for key, item in inv.items():
        data = item_to_dict(item)
        if data is None:
            lost.append(str(key))
            continue
        records[str(key)] = data
    if lost and write:
        write("WARN storage: {} item(s) could not be written down: {}".format(
            len(lost), lost))
    return records, lost


def key_for_instance(inv, item, fallback_id=None):
    """持ち物の辞書からその品の鍵を引く。同一性 → id の順（`402_` と同じ）。"""
    if not isinstance(inv, dict):
        return None
    for key, value in inv.items():
        if value is item:
            return key
    if fallback_id is not None and str(fallback_id) in inv:
        return str(fallback_id)
    return None


def free_key(inv, preferred=None):
    """その持ち物の中で空いている鍵。`preferred` が空いていればそれ。

    保管庫とプレイヤーの間の移動でしか使わないので、世界の採番（`index['item']`）は
    進めない（品は新しく生まれていない。同じ品が別の入れ物へ移るだけ）。
    """
    if not isinstance(inv, dict):
        return str(preferred or "item_0")
    if preferred is not None and str(preferred) not in inv:
        return str(preferred)
    base = str(preferred or "item")
    if base and base[-1].isdigit():
        head = base.rstrip("0123456789")
        number = int(base[len(head):] or 0)
    else:
        head, number = (base + "_") if base else "item_", 0
    while True:
        number += 1
        candidate = "{}{}".format(head, number)
        if candidate not in inv:
            return candidate
