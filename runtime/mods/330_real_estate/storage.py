# -*- coding: utf-8 -*-
r"""保管庫。預けた品を控えに落とし、開くたびに作り直す。

保管庫の中身は**控えが正**で、持ち主の持ち物には住まわせない。

    預ける   プレイヤーの持ち物から抜き、セーブと同じ形の辞書にして控えへ
    開く     控えから作り直し、窓の右に立つ持ち主に持たせる
    取り出す 持ち主から抜き、プレイヤーの持ち物へ

窓の右に立つのは建物の管理人（`modnpc` の人物）で、窓を開くたびに作り直し、閉じるときに控えへ写す。
管理人が引けないときだけ、その窓の間だけ生きる持ち主（`make_holder`）を立てる。
こちらは `Character` のインスタンスだが、`world.characters` にも `save_data_dict['npcs']` にも登録しない。
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

from instantale_modloader import frames, items


#: ゲームに無い場面名。本体の売買処理をこの窓に掛けないための札（`402_` と同じ）。
SITUATION = "real_estate_storage"

#: 能力値の6つの鍵（GAME.md §2.23）。
#: `Character.__init__` は `original_ability_scores` を**添字で読む**ので、
#: `None` のまま渡すと `TypeError: 'NoneType' object is not subscriptable` で落ちる
#: （VERIFICATION.md §3.62）。値は `None` でよいが、鍵は要る。
ABILITY_KEYS = ("strength", "dexterity", "constitution", "intelligence", "wisdom",
                "charisma")


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
    cls = items.character_class()
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
    if items.inventory_of(holder) is None:
        if write:
            write("WARN storage: the holder has no inventory dict")
        return None
    return holder


def fill(app, holder, records, write=None, skipped=None):
    """控えから持ち主の持ち物を作り直す。作れた件数を返す。

    作り直せない1件（ゲームの更新や MOD の入れ替えで組めなくなった品）は飛ばして先へ進む。
    `skipped` に辞書を渡すと、飛ばした控えを `{鍵: 辞書}` で入れる（閉じるときに控えへ戻すため）。
    渡さなければ従来どおり例外を投げ直す。

    作るのはゲーム自身の `generate_item_from_dict`。
    戻り値で受け取れる実装と、持ち物へ直に入れる実装（`generate_item_in_shopping`
    と同じ形。GAME.md §2.13.1.2）の両方に備える。
    """
    inv = items.inventory_of(holder)
    if inv is None or not isinstance(records, dict):
        return 0
    made = 0
    for key, data in sorted(records.items()):
        if not isinstance(data, dict):
            continue
        try:
            item = app.generate_item_from_dict(dict(data), str(key), holder)
        except Exception as exc:
            if write:
                write("WARN storage: cannot rebuild {} {} ({}: {})".format(
                    key, describe(data), type(exc).__name__, exc))
            if skipped is None:
                raise
            skipped[str(key)] = data
            continue
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
    inv = items.inventory_of(holder)
    if inv is None:
        return None, []
    records, lost = {}, []
    for key, item in inv.items():
        # 落とせない品（属性が JSON にならないもの）は控えに入らない。持ち主から抜くと世界から消える。
        data = items.to_dict(item)
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
