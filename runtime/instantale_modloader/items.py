# -*- coding: utf-8 -*-
r"""持ち物（`Character.inventory`）の読み書き。

**`Character.inventory` は入れ物のオブジェクト**（`scripts.characters:ItemContainer`）で、
その `.inventory` が `{鍵: Item}` の辞書（`402_` が実機で確かめた形）。
品そのものも JSON に落ちないオブジェクトなので、控えに入れるときは
**セーブと同じ形の辞書**に均し、戻すときはゲーム自身に作り直させる
（`InstantaleApp.generate_item_from_dict`。`330_` が実機で通した経路）。

素直に `getattr(character, "inventory")` を控えると、入れ物ごと落ちて
**持ち物が丸ごと消える**（実機。`331_` の店の主人に品が並んでいても控えは空だった）。

記録は GAME.md §2.13（セーブの1件の形）と TECH.md §5.7。

品が生まれた・画面に出た場面の窓口（`on_made` / `on_sight` / `on`）もここに置く（TECH.md §3.3.21）。
品を作るゲームの入口（生成の3入口・`Item.__init__`・クラフトの置き場・マスターAIの品の生成）と、
品が画面に出る所（所持品の画面・説明欄）を、134/409/410/411/128/124/109/405 がそれぞれ自分で包んでいた。
包むのはローダの1枚にし、MOD は品ごとの処理だけを渡す（`gates`）。下の「窓口」の節。
"""

import sys
import threading

from . import frames, gates, log_exc, state

#: セーブの1件が持つ項目（`330_` の保管庫が実機で往復させた12項目）。
ITEM_FIELDS = ("name", "item_type", "attributes", "description", "value", "rarity",
               "skill", "upgrade_level", "width_slots", "height_slots", "image_src",
               "grid_pos")


#: 品のレア度の6段（GAME.md §2.13.2 のスキーマ）。低い順。
RARITIES = ("common", "rare", "magical", "epic", "legendary", "mythic")

#: `generate_item_from_item_data` に渡す `item_type` / `item_sub_type` の語彙。ゲーム自身の品揃えの生成
#: （`shop_item_generator_ordinary`）のスキーマと同じ組（`output_data` の実記録）。入口はこの語彙を受けて
#: `item_detail` に直す（武器は `small` -> `small_weapon`、薬草は `herb` -> `plant`）。
#: 品の `item_detail`（`small_weapon`）を渡すと `small_weapon_weapon` になり、画像の埋め込み
#: `Data/item_embeddings/<item_detail>.json` が見つからずに落ちる（VERIFICATION.md §3.76）。
#: LLM に品を書かせる MOD はこの組から選ばせ、外れたら先頭の細分に寄せる（`312_` と `925_` が使う）。
ITEM_TYPES = {
    "weapon": ("small", "medium", "large", "long", "throwable"),
    "wearable": ("headgear", "body_armor", "legwear", "gauntlets", "shield",
                 "accessory", "clothing"),
    "healing_item": ("food", "drink", "herb", "medicine", "potion"),
    "consumable": ("food", "drink", "herb", "medicine", "potion", "scroll"),
    "utility": ("tool", "document"),
    "material": ("creature_part", "ore", "gem", "treasure", "metal", "creature",
                 "plant", "mushroom", "relic", "scrap", "magical_material",
                 "other_material"),
}


def rarity_rank(value) -> int:
    """レア度の段の番号（common が 0、mythic が 5）。知らない語・読めなければ 0（common とみなす）。

    `value` はレア度の文字列か、`rarity` を持つもの（セーブの形の辞書・`Item`・画面の部品）。
    照合は `RARITIES` との完全一致で、大文字小文字や前後の空白は均さない
    （写しの `336_` の `rarity_rank`・`926_` の `rarity_index` と同じ答え。ゲームが書く語は小文字）。
    """
    if not isinstance(value, str):
        holder = unwrap(value)
        value = (holder.get("rarity") if isinstance(holder, dict)
                 else frames.attr(holder, "rarity", None))
    return RARITIES.index(value) if isinstance(value, str) and value in RARITIES else 0


def unwrap(item):
    """画面の部品（所持品の欄の `InventoryItem`）が来たら、中の品（`item_instance`）を返す。部品でなければそのまま。

    `409_` / `410_` / `411_` の `unwrap` と同じ答え。違いは1つで、`item_instance` の読み取りが投げたとき
    （`frames.attr` の番人の文字列）は部品とみなさず、渡されたものをそのまま返す（写しは番人の文字列を返した）。
    """
    inner = frames.attr(item, "item_instance", None)
    return item if inner is None or isinstance(inner, str) else inner


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


#: 品を作るゲームの入口と、持ち主・鍵の引数（名前と、`self` を除いた位置）。
#: 戻り値は入口で違い、`generate_item_from_item_data` は `(Item, 鍵)`、
#: `generate_item_from_dict` は None（品は渡した鍵で持ち物へ入る）。GAME.md §2.13.1.5。
MAKERS = {
    "generate_item_from_item_data": (("obtainer", 7), None),
    "generate_item_from_dict": (("obtainer", 2), ("item_id", 1)),
    "generate_item_from_ready_made_data": (("obtainer", 0), None),
}


def _is_item(value):
    return not isinstance(value, dict) and isinstance(getattr(value, "attributes", None), dict)


def made_item(entry, result, args, kwargs):
    """品を作る入口 `entry`（`MAKERS` の名前）の呼び出しから、作られた品を取り出す。取れなければ None。

    `@ctx.wrap` の中で `orig` の戻り値と、受け取った引数（`self` を除く）をそのまま渡す。
    """
    if _is_item(result):
        return result
    if isinstance(result, (tuple, list)):
        for value in result:
            if _is_item(value):
                return value
    owner_arg, key_arg = MAKERS.get(entry, (None, None))
    if owner_arg is None or key_arg is None:
        return None
    inv = inventory_of(frames.arg(args, kwargs, *owner_arg))
    key = frames.arg(args, kwargs, *key_arg)
    item = inv.get(str(key)) if inv is not None and key is not None else None
    return item if _is_item(item) else None


#: 控えに入れてよい値か（JSON に落ちるものだけ）。`state` の語彙。
_jsonable = state.jsonable


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


def make_loose(app, owner, data, key, write=None):
    """セーブと同じ形の辞書から品を1つ作り、**持ち物には入れずに**返す。作れなければ None。

    作るのはゲーム自身の `generate_item_from_dict`。持ち物へ直に入れる実装（`rebuild`）なら、
    入った品を抜いて元に戻す。窓口 `equipment.lend` で貸す品を作るのに使う。
    """
    make = getattr(app, "generate_item_from_dict", None) if app is not None else None
    if not callable(make) or not isinstance(data, dict):
        if write:
            write("WARN items: generate_item_from_dict is not available")
        return None
    key = str(key)
    inv = inventory_of(owner)
    before = inv.get(key) if inv is not None else None
    try:
        item = make(dict(data), key, owner)
    except Exception:
        log_exc("items: cannot make {}".format(key))
        return None
    inv = inventory_of(owner)
    landed = inv.get(key) if inv is not None else None
    if landed is not None and landed is not before:
        if item is None:
            item = landed
        if before is None:
            inv.pop(key, None)
        else:
            inv[key] = before
    return item


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


# ---------------------------------------------------------------------------
# 窓口: 品が生まれた・画面に出た（TECH.md §3.3.21）
# ---------------------------------------------------------------------------
#
#     items.on_made(ctx, fn, owner=owner, sources=None, write=write)   # 品が生まれたら1品ずつ
#     items.on_sight(ctx, fn, owner=owner, wheres=None, write=write)   # 品が画面に出る前に1品ずつ
#     items.on(owner, ctx, (items.DETAIL,), before=fn, after=fn)       # 入口そのものの前後（gates の形）
#     fn(call)    # call.item / call.source（on_made）・call.where / call.holder（on_sight）・call.entry …
#
# 生成の入口の戻り値は品とは限らない（`(Item, 鍵)` や None。GAME.md §2.13.1.5）ので、入口の間に
# `Item.__init__` で組まれた品を控えておき、入口を抜けたところで渡す（410/411 が各自で持っていた組み立て）。
# 入口の外で組まれた品は、直前にマスターAIが名付けた品だけ拾う（セーブからの組み直しも `Item.__init__` を通る）。
#
# | `call.source`（on_made） | いつ | |
# |---|---|---|
# | `generated` / `generated/dict` / `generated/ready_made` | 生成の3入口の `orig` の後 | 入口の間に組まれた品と、戻り値・持ち物の鍵から引いた品（`made_item`）。入れ子なら一番外の入口を抜けたときに1回。出どころは品が組まれた一番内側の入口 |
# | `crafted` | `InstanTaleHUD.place_crafted_item` の `orig` の前 | 引数の品（生成の入口を通らない版に備える。入口も通れば2回来る） |
# | `named` | 入口の外の `Item.__init__` の後 | 直前の `master_ai_item_generate` が返した名前（新しい20件）と同じ名前の品 |
#
# | `call.where`（on_sight） | いつ | `call.holder` |
# |---|---|---|
# | `window/left` / `window/right` | 2列の画面（`twin_windows.OPEN`）を開く前。左の品を全部、次に右 | 左右の持ち主 |
# | `window/center` | 所持品の画面（`toggle_center_inventory_window`）を開く前 | 主人公 |
# | `detail` | 説明欄（`ItemDetailBox.update_content`）を描く前 | None |
#
# - 品ごとの呼び出しは、登録の順（`on_made` は古い順＝ `after` の順、`crafted` と `on_sight` は新しい順＝ `before` の順）に、
#   登録ごとに品を全部渡してから次の登録へ移る（各 MOD が自分で包んでいたときと同じ）
# - `call.item` は画面の部品なら中の品（`unwrap`）。説明欄は引数のままのものを `call.raw_item` に置く
# - `call.entry` は入口の呼び出し（`gates.Call`）。`call.app` / `call.obtainer` / `call.args` も入口のものを写す
# - 1品の処理が投げても、残りの品とほかの登録・`orig` は止めない（記録して `write` に WARN）
# - 前後の処理は引数も戻り値も変えない。品の中身を直すのは構わない（品はゲームの持ち物そのもの）
# - 関所は全 MOD の適用の後にローダが一番外側に立てる（`gates.defer_outermost`）。値段の関所（`prices`）は同じ対象を
#   自分で包んでいて、適用順で前後に分かれる MOD（410 は内側、134/405/409/411 は外側）の書く時点が今と同じになるのは外側
# - 包むのは、どれかの種類に登録があるときだけ。そのときは8つを全部包む（品の控えは入口と `Item.__init__` の組で働く）
# - 2列の画面は `twin_windows.OPEN` の関所に `<持ち主>:sight` の名で載る（同じ関数を2枚で包まない）

#: 包む関数の種類（`on` の `kinds`）。
FROM_ITEM_DATA = "from_item_data"
FROM_DICT = "from_dict"
FROM_READY_MADE = "from_ready_made"
BUILD = "build"
PLACE_CRAFTED = "place_crafted"
MASTER_ITEM = "master_item"
CENTER = "center"
DETAIL = "detail"
KINDS = (FROM_ITEM_DATA, FROM_DICT, FROM_READY_MADE, BUILD, PLACE_CRAFTED, MASTER_ITEM, CENTER, DETAIL)

TARGETS = {
    FROM_ITEM_DATA: "__main__:InstantaleApp.generate_item_from_item_data",
    FROM_DICT: "__main__:InstantaleApp.generate_item_from_dict",
    FROM_READY_MADE: "__main__:InstantaleApp.generate_item_from_ready_made_data",
    BUILD: "scripts.items:Item.__init__",
    PLACE_CRAFTED: "scripts.hud.new_hud:InstanTaleHUD.place_crafted_item",
    MASTER_ITEM: "scripts.llm.llm_manager:master_ai_item_generate",
    CENTER: "__main__:InstantaleApp.toggle_center_inventory_window",
    DETAIL: "scripts.hud.new_hud:ItemDetailBox.update_content",
}
LABELS = {
    FROM_ITEM_DATA: "generate_item_from_item_data",
    FROM_DICT: "generate_item_from_dict",
    FROM_READY_MADE: "generate_item_from_ready_made_data",
    BUILD: "Item.__init__",
    PLACE_CRAFTED: "place_crafted_item",
    MASTER_ITEM: "master_ai_item_generate",
    CENTER: "toggle_center_inventory_window",
    DETAIL: "ItemDetailBox.update_content",
}

#: 品の出どころ（`on_made` の `call.source` と `sources`）。
GENERATED = "generated"
GENERATED_DICT = "generated/dict"
GENERATED_READY_MADE = "generated/ready_made"
CRAFTED = "crafted"
NAMED = "named"
SOURCES = (GENERATED, GENERATED_DICT, GENERATED_READY_MADE, CRAFTED, NAMED)

#: 品が出た画面（`on_sight` の `call.where` と `wheres`）。
WINDOW_LEFT = "window/left"
WINDOW_RIGHT = "window/right"
WINDOW_CENTER = "window/center"
SIGHT_DETAIL = "detail"
WHERES = (WINDOW_LEFT, WINDOW_RIGHT, WINDOW_CENTER, SIGHT_DETAIL)

#: 品ごとの呼び出しの `call.kind`。
MADE = "made"
SIGHT = "sight"

#: 生成の入口ごとの `MAKERS` の名前と出どころ。
_ENTRIES = {
    FROM_ITEM_DATA: ("generate_item_from_item_data", GENERATED),
    FROM_DICT: ("generate_item_from_dict", GENERATED_DICT),
    FROM_READY_MADE: ("generate_item_from_ready_made_data", GENERATED_READY_MADE),
}

#: `Item.__init__` の引数名の並び（GAME.md §2.13.2）。
ITEM_INIT_ARGS = ("name", "item_type", "attributes", "description", "value", "size", "image_src",
                  "rarity", "skill", "obtainer", "id", "grid_pos", "upgrade_level")

#: マスターAIが名付けた品の名前を控える数。組まれずに終わった名前を溜めない（410/411 と同じ数）。
PENDING_LIMIT = 20

GATES = {kind: gates.Gate("items." + kind, TARGETS[kind], "_instantale_items_" + kind, outermost=True)
         for kind in KINDS}

_STORE_ATTR = "_instantale_items_window"
_MADE_SUFFIX = ":made"
_SIGHT_SUFFIX = ":sight"


def _store():
    """名前の控え・錠・スレッドごとの入口の積み（`sys` に置く。注入し直しをまたいで残る）。"""
    return state.process_store(_STORE_ATTR, {"pending": {}, "lock": threading.Lock(),
                                             "local": threading.local()})


def _stack():
    """このスレッドで走っている生成の入口の積み（`[{"source", "items"}]`）。"""
    local = _store()["local"]
    stack = getattr(local, "frames", None)
    if not isinstance(stack, list):
        stack = []
        local.frames = stack
    return stack


def _note_name(name):
    store = _store()
    with store["lock"]:
        pending = store["pending"]
        pending[name] = True
        while len(pending) > PENDING_LIMIT:
            pending.pop(next(iter(pending)))


def _take_name(name):
    """控えた名前なら消して True。"""
    if not isinstance(name, str) or not name:
        return False
    store = _store()
    with store["lock"]:
        return store["pending"].pop(name, None) is not None


def pending_names():
    """マスターAIが名付けて、まだ組まれていない品の名前（古い順）。ログと検査用。"""
    store = _store()
    with store["lock"]:
        return list(store["pending"])


def _find_app():
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def _app_of(obj):
    app = frames.attr(obj, "app", None)
    return app if app is not None and not isinstance(app, str) else _find_app()


def _held(holder):
    """持ち主の持ち物の品（並びのまま）。入れ物・辞書・list のどれでも。読めなければ []。"""
    inv = inventory_of(holder)
    if inv is not None:
        return list(inv.values())
    raw = frames.attr(holder, "inventory", None) if holder is not None else None
    return list(raw) if isinstance(raw, (list, tuple)) else []


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    unknown = [kind for kind in kinds if kind not in GATES]
    if not kinds or unknown:
        raise ValueError("items: kinds must be among {}, got {!r}".format(KINDS, kinds))
    return kinds


def _choose(values, allowed, what):
    """`sources` / `wheres` を tuple に揃える。None は全部。知らない語は ValueError。"""
    if values is None:
        return None
    if isinstance(values, str):
        values = (values,)
    values = tuple(values)
    unknown = [value for value in values if value not in allowed]
    if not values or unknown:
        raise ValueError("items: {} must be among {}, got {!r}".format(what, allowed, values))
    return values


def _owner_of(ctx, owner, what):
    owner = owner or getattr(ctx, "mod_name", None)
    if not isinstance(owner, str) or not owner:
        raise ValueError("items: {}() needs owner= (ctx.mod_name is None outside apply())".format(what))
    return owner


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None, enter=None, leave=None):
    """入口そのものの前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    品ごとに受けるなら `on_made` / `on_sight` を使う。こちらは品に触らない後始末（説明欄の大きさ・
    クラフトの画面の並べ直し）、`Item.__init__` のすべての呼び出し、`orig` の間だけ値を変える形（`enter` / `leave`）用。

    | 種類 | 包む関数 | `call.obj` | `call.app` | 足す属性（`orig` の前に読む） |
    |---|---|---|---|---|
    | `FROM_ITEM_DATA` / `FROM_DICT` / `FROM_READY_MADE` | 生成の3入口 | app | app | `source`・`obtainer`（引数の持ち主）。`after` では `made`（`[(品, 出どころ)]`。入れ子の内側の入口では空） |
    | `BUILD` | `Item.__init__` | 組んでいる品 | None | `item`（= `call.obj`）・`obtainer`。`after` では `named`（名付けた品なら True） |
    | `PLACE_CRAFTED` | `InstanTaleHUD.place_crafted_item` | HUD | HUD の `app` か走っている app | `item`（引数の品。部品なら中の品）・`raw_item` |
    | `MASTER_ITEM` | `master_ai_item_generate`（関数） | None | None | `after` では `name` / `item_type`（返った品の名前と種別） |
    | `CENTER` | `toggle_center_inventory_window` | app | app | `holder`（主人公） |
    | `DETAIL` | `ItemDetailBox.update_content` | 説明欄 | 走っている app | `item`（部品なら中の品）・`raw_item`（引数のまま） |

    呼ぶ順と `memo` / `always` / `enter` / `leave` の決まりは `gates`。
    """
    for kind in _kinds_of(kinds):
        GATES[kind].on(owner, ctx, before=before, after=after, always=always, write=write,
                       enter=enter, leave=leave)
    install(ctx, write)


def off(owner, kinds=KINDS):
    """`on` の登録を下げる。"""
    for kind in _kinds_of(kinds):
        GATES[kind].withdraw(owner)


def owners(kind):
    """生きている登録の持ち主（登録の古い順）。`on_made` / `on_sight` の登録は名前の後ろに `:made` / `:sight` が付く。"""
    return GATES[_kinds_of(kind)[0]].owners()


def _deliver(owner, fn, call, write):
    try:
        fn(call)
    except Exception as exc:
        log_exc("items.{}: the hook of {} failed".format(call.kind, owner))
        if callable(write):
            try:
                write("WARN items.{}: the hook raised {}: {}".format(call.kind, type(exc).__name__, exc))
            except Exception:
                pass


def on_made(ctx, fn, *, owner=None, sources=None, write=None):
    """品が生まれたら1品ずつ `fn(call)` を呼ぶ。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    `call.item` は品（`Item`）、`call.source` は出どころ（`SOURCES`。上の節の表）、
    `call.obtainer` は入口の引数の持ち主（`crafted` は None、`named` は `Item.__init__` の引数）、
    `call.app` は入口の app（`named` は走っている app）、`call.entry` は入口の呼び出し。

    `sources` で受ける出どころを絞る（None は全部）。セーブからの組み直しで品を作り直す
    `generated/dict` を受けたくない MOD（新しい品にだけ抽選する 410/411）は、受ける分だけを渡す。
    `owner` を省くと `ctx.mod_name`。
    """
    owner = _owner_of(ctx, owner, "on_made")
    if not callable(fn):
        raise TypeError("items: on_made() needs a callable, got {!r}".format(type(fn)))
    wanted = _choose(sources, SOURCES, "sources")
    key = owner + _MADE_SUFFIX

    def deliver(call, pairs, app=None):
        for item, source in pairs:
            if wanted is not None and source not in wanted:
                continue
            one = gates.Call(MADE, call.obj, call.args, call.kwargs,
                             app=app if app is not None else call.app,
                             item=item, source=source, entry=call,
                             obtainer=getattr(call, "obtainer", None))
            _deliver(owner, fn, one, write)

    def after_make(call):
        deliver(call, getattr(call, "made", None) or ())

    def after_build(call):
        if getattr(call, "named", False):
            deliver(call, [(call.obj, NAMED)], app=_find_app())

    def before_place(call):
        item = getattr(call, "item", None)
        if item is not None:
            deliver(call, [(item, CRAFTED)])

    plan = (
        (FROM_ITEM_DATA, GENERATED, {"after": after_make}),
        (FROM_DICT, GENERATED_DICT, {"after": after_make}),
        (FROM_READY_MADE, GENERATED_READY_MADE, {"after": after_make}),
        (BUILD, NAMED, {"after": after_build}),
        (PLACE_CRAFTED, CRAFTED, {"before": before_place}),
    )
    for kind, source, hooks in plan:
        # 入れ子の内側で組まれた品は外の入口の出どころでなく、組まれた入口の出どころで来る。
        # 外の入口で渡すので、生成の3入口は1つでも受けるなら3つとも載る。
        needed = (wanted is None or source in wanted
                  or (kind in _ENTRIES and any(s in wanted for s in (GENERATED, GENERATED_DICT,
                                                                      GENERATED_READY_MADE))))
        if needed:
            GATES[kind].on(key, ctx, write=write, **hooks)
        else:
            GATES[kind].withdraw(key)
    install(ctx, write)


def off_made(owner):
    """`on_made` の登録を下げる。"""
    for kind in KINDS:
        GATES[kind].withdraw(str(owner) + _MADE_SUFFIX)


def made_owners():
    """`on_made` の生きている登録の持ち主（登録の古い順）。"""
    return _suffixed_owners(_MADE_SUFFIX, [GATES[kind] for kind in KINDS])


def on_sight(ctx, fn, *, owner=None, wheres=None, write=None):
    """品が画面に出る前に1品ずつ `fn(call)` を呼ぶ。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    既にセーブに在る品は生成の入口を通らないので、画面に出た時点で直す MOD（134/409）が使う。
    `call.item` は品、`call.where` は画面（`WHERES`。上の節の表）、`call.holder` は持ち主（説明欄は None）、
    `call.app` / `call.obj` / `call.entry` は画面を開く呼び出しのもの。

    `wheres` で受ける画面を絞る（None は全部）。`owner` を省くと `ctx.mod_name`。
    2列の画面は `twin_windows.OPEN` の関所に載る。
    """
    from . import twin_windows
    owner = _owner_of(ctx, owner, "on_sight")
    if not callable(fn):
        raise TypeError("items: on_sight() needs a callable, got {!r}".format(type(fn)))
    wanted = _choose(wheres, WHERES, "wheres")
    key = owner + _SIGHT_SUFFIX

    def wants(where):
        return wanted is None or where in wanted

    def deliver(call, where, holder, things):
        if not wants(where):
            return
        for item in things:
            item = unwrap(item)
            if item is None:
                continue
            one = gates.Call(SIGHT, call.obj, call.args, call.kwargs, app=call.app,
                             item=item, where=where, holder=holder, entry=call)
            _deliver(owner, fn, one, write)

    def before_twin(call):
        left, right = getattr(call, "left", None), getattr(call, "right", None)
        deliver(call, WINDOW_LEFT, left, _held(left))
        deliver(call, WINDOW_RIGHT, right, _held(right))

    def before_center(call):
        holder = getattr(call, "holder", None)
        deliver(call, WINDOW_CENTER, holder, _held(holder))

    def before_detail(call):
        deliver(call, SIGHT_DETAIL, None, [getattr(call, "item", None)])

    if wants(WINDOW_LEFT) or wants(WINDOW_RIGHT):
        twin_windows.on(key, ctx, (twin_windows.OPEN,), before=before_twin, write=write)
    else:
        twin_windows.off(key, (twin_windows.OPEN,))
    for kind, where, hook in ((CENTER, WINDOW_CENTER, before_center), (DETAIL, SIGHT_DETAIL, before_detail)):
        if wants(where):
            GATES[kind].on(key, ctx, before=hook, write=write)
        else:
            GATES[kind].withdraw(key)
    install(ctx, write)


def off_sight(owner):
    """`on_sight` の登録を下げる。"""
    from . import twin_windows
    key = str(owner) + _SIGHT_SUFFIX
    twin_windows.off(key, (twin_windows.OPEN,))
    for kind in (CENTER, DETAIL):
        GATES[kind].withdraw(key)


def sight_owners():
    """`on_sight` の生きている登録の持ち主（登録の古い順）。"""
    from . import twin_windows
    return _suffixed_owners(_SIGHT_SUFFIX, [twin_windows.GATES[twin_windows.OPEN], GATES[CENTER], GATES[DETAIL]])


def _suffixed_owners(suffix, gate_list):
    rows = {}
    for gate in gate_list:
        for owner, entry in gate.entries():
            if owner.endswith(suffix):
                name = owner[:-len(suffix)]
                rows[name] = min(rows.get(name, entry.get("seq", 0)), entry.get("seq", 0))
    return [name for name, _seq in sorted(rows.items(), key=lambda row: row[1])]


def reset():
    """全部忘れる（検査用）。`on_sight` が載る `twin_windows` の登録は `twin_windows.reset()` で。"""
    for gate in GATES.values():
        gate.reset()
    if hasattr(sys, _STORE_ATTR):
        delattr(sys, _STORE_ATTR)


# -- 包み -------------------------------------------------------------------
def _collect(call, entry, source, invoke):
    """生成の入口の `orig` を、組まれる品を控えながら呼ぶ。抜けたら `call.made` を決める。"""
    stack = _stack()
    frame = {"source": source, "items": []}
    stack.append(frame)
    try:
        result = invoke()
    finally:
        for index in range(len(stack) - 1, -1, -1):
            if stack[index] is frame:
                del stack[index]
                break
    try:
        found = list(frame["items"])
        item = made_item(entry, result, call.args, call.kwargs)
        if item is not None:
            found.append((item, source))
        made, seen = [], set()
        for thing, origin in found:
            if id(thing) not in seen:
                seen.add(id(thing))
                made.append((thing, origin))
        if stack:
            stack[-1]["items"].extend(made)      # 外の入口がまとめて渡す
        else:
            for thing, _origin in made:           # ここで渡すので、後の組み直しで名付けた品として拾い直さない
                _take_name(frames.attr(thing, "name", None))
            call.made = made
    except Exception:
        log_exc("items: cannot collect the items made by {}".format(entry))
    return result


def _make_wrapper(kind):
    gate = GATES[kind]
    entry, source = _ENTRIES[kind]
    owner_name, owner_index = MAKERS[entry][0]

    def make(orig, self, *args, **kwargs):
        call = gates.Call(kind, self, args, kwargs, app=self, label=LABELS[kind], source=source,
                          obtainer=frames.arg(args, kwargs, owner_name, owner_index), made=[])
        return gate.run(call, lambda: _collect(call, entry, source,
                                               lambda: orig(self, *args, **kwargs)))
    return make


def _build_wrapper():
    gate = GATES[BUILD]

    def build(orig, self, *args, **kwargs):
        stack = _stack()
        # 品の数だけ通る（セーブの読み込みでも）。控えも登録も無ければ何もしない。
        if not stack and not _store()["pending"] and not gate.entries():
            return orig(self, *args, **kwargs)
        call = gates.Call(BUILD, self, args, kwargs, app=None, label=LABELS[BUILD], item=self,
                          obtainer=frames.arg(args, kwargs, "obtainer", ITEM_INIT_ARGS), named=False)

        def invoke():
            result = orig(self, *args, **kwargs)
            try:
                if stack:
                    stack[-1]["items"].append((self, stack[-1]["source"]))
                elif _take_name(frames.attr(self, "name", None)):
                    call.named = True
            except Exception:
                log_exc("items: cannot note a built item")
            return result
        return gate.run(call, invoke)
    return build


def _master_wrapper():
    gate = GATES[MASTER_ITEM]

    def master(orig, *args, **kwargs):
        call = gates.Call(MASTER_ITEM, None, args, kwargs, app=None, label=LABELS[MASTER_ITEM],
                          name=None, item_type=None)

        def invoke():
            result = orig(*args, **kwargs)
            try:
                from . import llm
                data = llm.as_dict(result) or {}
                category = data.get("item_category")
                kind = category.get("type") if isinstance(category, dict) else None
                name = data.get("item_name")
                call.item_type = kind if isinstance(kind, str) else None
                call.name = name if isinstance(name, str) and name else None
                # 生成の入口の中で名付けた品は、入口が控えて渡す。
                if call.name is not None and not _stack():
                    _note_name(call.name)
            except Exception:
                log_exc("items: cannot read the item named by master_ai_item_generate")
            return result
        return gate.run(call, invoke)
    return master


def _screen_wrapper(kind):
    gate = GATES[kind]

    def screen(orig, self, *args, **kwargs):
        if not gate.entries():
            return orig(self, *args, **kwargs)
        try:
            if kind == CENTER:
                call = gates.Call(kind, self, args, kwargs, app=self, label=LABELS[kind],
                                  holder=frames.attr(self, "player", None))
            else:
                name = "generated_item" if kind == PLACE_CRAFTED else "item"
                raw = frames.arg(args, kwargs, name, 0)
                app = _app_of(self) if kind == PLACE_CRAFTED else _find_app()
                call = gates.Call(kind, self, args, kwargs, app=app, label=LABELS[kind],
                                  item=unwrap(raw) if raw is not None else None, raw_item=raw)
        except Exception:
            log_exc("items: cannot read the call of {}".format(LABELS[kind]))
            return orig(self, *args, **kwargs)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return screen


def _wrapper(kind):
    if kind in _ENTRIES:
        return _make_wrapper(kind)
    if kind == BUILD:
        return _build_wrapper()
    if kind == MASTER_ITEM:
        return _master_wrapper()
    return _screen_wrapper(kind)


def install(ctx, write=None):
    """関所を立てる。種類ごとに1つの世代につき1枚。包んだ対象の名前を返す。

    どの種類にも生きている登録が無ければ包まない（`Item.__init__` はセーブの読み込みで品の数だけ通るので、
    使う MOD が無ければ素のままにする）。登録があれば8つを全部包む（品の控えは生成の入口・`Item.__init__`・
    マスターAIの品の生成の組で働く）。MOD の適用の間は包まず、全 MOD の後にローダが外側に立てる（`gates.defer_outermost`）。
    """
    if not any(gate.entries() for gate in GATES.values()):
        return []
    done = []
    for kind in KINDS:
        for target in GATES[kind].install(ctx, _wrapper(kind), write):
            if target not in done:
                done.append(target)
    return done
