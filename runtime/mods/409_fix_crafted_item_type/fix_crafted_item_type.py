# -*- coding: utf-8 -*-
"""クラフトで作られた飲食物・薬の `item_type` を `healing_item` に直す。

## なぜ要るか

右クリックの「消費」（`ItemPopupMenu.on_consume_item` → `Item.consume` →
`ItemConsumeManager.consume_item`）が出るのは `item_type` が `healing_item` の品だけ。
ところがクラフト（`item_craft_generator`。LLM の構造化出力）は、
飲食物・薬でも `item_type` を `consumable` で返すことがある。

その結果、作った品にはメニューに「消費」が無く、
`134_balance_item_effects` が効果（`スタミナ回復` / `回復: N%`）を
`attributes` に書いてあっても発動する入口が無い。
裏付けは VERIFICATION.md §3.104。

## どう直すか

`item_detail` が飲食物・薬（`food` / `drink` / `medicine` / `potion` /
`plant` / `mushroom`。`134_` の `KIND_BY_DETAIL` と同じ6つ）で、
`item_type` が `consumable` の品を `healing_item` に書き換える。
`scroll`（巻物）は `consumable` のまま触らない（回復アイテムではない）。

`consumable` の品には、本体が `healing_item` に書く `回復` と `疲労負荷` が無い。
本体の消費はこの2つを読む（`134_` の `Silenced` が `回復` を必ず在る状態で通すのも同じ理由）ので、
どちらも無い品には、本体の `get_heal_spec(value)` と
`get_heal_physical_integrity_barden(value)` で店の回復アイテムと同じ値を足す。
`134_` が先に作り直した品（`疲労負荷: 0` が在る）には足さない。
並びは本体と同じ `item_detail` → `回復` → `疲労負荷` → 残り（値段など）。

直す場所は2系統:

  * 生成の入口3本（`129_` / `134_` と同じ）とクラフトの置き場。これから作られる品はここで直る
  * 画面に出た時点（持ち物の窓2種と説明欄）。古いセーブに残っている品はここで直る。
    セーブは実行時の `item_type` をそのまま焼くので、一度直れば次からはそのまま

何度呼ばれても同じ結果（既に `healing_item` なら何もしない）。
"""

import sys

from instantale_modloader import frames, items

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
FIX_ON_SIGHT = True         # 古いセーブの品も、画面に出た時点で直す

LOG_BASENAME = "crafted_item_type.log"
LOG_LIMIT = 200

# 直す対象。`134_balance_item_effects` の `KIND_BY_DETAIL` と同じ6つ。
# `scroll` は回復アイテムではないので `consumable` のまま。
HEALING_DETAILS = ("food", "drink", "medicine", "potion", "plant", "mushroom")

WRONG_TYPE = "consumable"
RIGHT_TYPE = "healing_item"

# 本体と `134_` が書く鍵（綴りは `134_` の `HEAL_KEYS` / `BURDEN_KEYS` / `LABELS` と同じ）。
HEAL_KEYS = ("回復", "recovery", "Recovery")
BURDEN_KEYS = ("疲労負荷", "fatigue", "Fatigue", "fatigue burden")
LABELS = {"ja": ("回復", "疲労負荷"), "en": ("recovery", "fatigue")}

# 再注入しても数え直さない（`134_` と同じ形）。
STORE_ATTR = "_instantale_crafted_item_type_store"


def unwrap(item):
    """画面の部品（`InventoryItem`）が来たら中の `item_instance` を取り出す。"""
    inner = frames.attr(item, "item_instance", None)
    return item if inner is None else inner


def field_of(item, name):
    """`Item` インスタンスとセーブの辞書の両方から1項目を読む。"""
    if isinstance(item, dict):
        return item.get(name)
    return frames.attr(item, name, None)


def needs_fix(item):
    """直すべき品なら True。"""
    item = unwrap(item)
    attributes = field_of(item, "attributes")
    if field_of(item, "item_type") != WRONG_TYPE or not isinstance(attributes, dict):
        return False
    return attributes.get("item_detail") in HEALING_DETAILS


def language():
    """ゲームの表示言語。`scripts.languages.language`。読めなければ ja（`134_` と同じ）。"""
    module = sys.modules.get("scripts.languages")
    lang = getattr(module, "language", None) if module is not None else None
    return "en" if lang == "en" else "ja"


def game_heal_values(value):
    """本体が `healing_item` に書く `(回復, 疲労負荷)`。関数が引けなければ None。"""
    module = sys.modules.get("scripts.functions")
    spec = getattr(module, "get_heal_spec", None)
    burden = getattr(module, "get_heal_physical_integrity_barden", None)
    if not callable(spec) or not callable(burden):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return spec(value), burden(value)


def fill_heal_keys(item):
    """`回復` も `疲労負荷` も無ければ本体の値で足す。足したら `(回復, 疲労負荷)`。

    辞書は同じものを中で組み直す（品が握っている参照を替えない）。
    """
    attributes = field_of(item, "attributes")
    if not isinstance(attributes, dict):
        return None
    if any(key in attributes for key in HEAL_KEYS + BURDEN_KEYS):
        return None
    values = game_heal_values(field_of(item, "value"))
    if values is None:
        return None
    heal_key, burden_key = LABELS[language()]
    rebuilt = []
    if "item_detail" in attributes:
        rebuilt.append(("item_detail", attributes["item_detail"]))
    rebuilt.append((heal_key, values[0]))
    rebuilt.append((burden_key, values[1]))
    rebuilt.extend((key, val) for key, val in attributes.items() if key != "item_detail")
    attributes.clear()
    attributes.update(rebuilt)
    return values


def fix(item):
    """`item_type` を `healing_item` に書き換える。

    直さなければ None、直したら `回復` と `疲労負荷` を足したかどうか
    （足した値の組か False）。
    """
    if not needs_fix(item):
        return None
    item = unwrap(item)
    if isinstance(item, dict):
        item["item_type"] = RIGHT_TYPE
    else:
        setattr(item, "item_type", RIGHT_TYPE)
    return fill_heal_keys(item) or False


def apply(ctx):
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"logged": 0, "fixed": 0}
        setattr(sys, STORE_ATTR, store)

    write = ctx.logger(LOG_BASENAME)

    def note(item, why, filled):
        store["fixed"] += 1
        store["logged"] += 1
        if store["logged"] <= LOG_LIMIT:
            item = unwrap(item)
            attributes = field_of(item, "attributes")
            detail = attributes.get("item_detail") if isinstance(attributes, dict) \
                else None
            write("{}: {!r}/{} consumable -> healing_item{}".format(
                why, frames.short(field_of(item, "name"), 60), detail,
                " (+heal {}/{})".format(*filled) if filled else ""))
        elif store["logged"] == LOG_LIMIT + 1:
            write("... 以降は件数だけ数える（LOG_LIMIT={}）".format(LOG_LIMIT))

    def fix_one(item, why):
        filled = fix(item)
        if filled is None:
            return False
        note(item, why, filled)
        return True

    def fix_inventory(obtainer, why):
        # 持ち主（`Character`）でも入れ物（`ItemContainer`）でも中の辞書を引く。
        inventory = items.inventory_of(obtainer)
        if inventory is None:
            return 0
        return sum(1 for item in list(inventory.values()) if fix_one(item, why))

    # ---- 生成の入口（`129_` / `134_` と同じ3本）---------------------------
    @ctx.wrap("__main__:InstantaleApp.generate_item_from_item_data", safe=True)
    def generated_from_data(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        fix_one(result, "generated")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_dict", safe=True)
    def generated_from_dict(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        fix_one(result, "generated/dict")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_ready_made_data",
              safe=True, required=False)
    def generated_ready_made(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        fix_one(result, "generated/ready_made")
        return result

    # ---- クラフトの成果物が持ち物へ置かれる瞬間 ----------------------------
    # 生成の入口を通らない版があっても、置かれる前にここで捕まえる。
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.place_crafted_item",
              safe=True, required=False)
    def crafted(orig, self, *args, **kwargs):
        generated_item = kwargs.get("generated_item", args[0] if args else None)
        fix_one(generated_item, "crafted")
        return orig(self, *args, **kwargs)

    # ---- 既にセーブに在る品（画面に出た時点で直す）-------------------------
    if FIX_ON_SIGHT:
        @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window", safe=True)
        def twin_window(orig, self, *args, **kwargs):
            names = ("left_inventory_obtainer", "right_inventory_obtainer")
            for index, name in enumerate(names):
                obtainer = kwargs.get(name, args[index] if len(args) > index else None)
                fix_inventory(obtainer, "window/" + name.split("_")[0])
            return orig(self, *args, **kwargs)

        @ctx.wrap("__main__:InstantaleApp.toggle_center_inventory_window",
                  safe=True, required=False)
        def center_window(orig, self, *args, **kwargs):
            fix_inventory(frames.attr(self, "player", None), "window/center")
            return orig(self, *args, **kwargs)

        @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content", safe=True)
        def detail_box(orig, self, *args, **kwargs):
            fix_one(kwargs.get("item", args[0] if args else None), "detail")
            return orig(self, *args, **kwargs)

    ctx.log("crafted item type: installed (details={}, on_sight={}, fixed so far={})"
            .format(",".join(HEALING_DETAILS), bool(FIX_ON_SIGHT), store["fixed"]))
