# -*- coding: utf-8 -*-
"""計測: 品物のレベルを誰が決めているかを録る。ゲームは変えない。

`318_area_difficulty_growth` を書くために、土地の難易度と品物の関係を
実セーブから突き合わせた（GAME.md §2.13.1）。
そこまでで分かったのは**店の側**で、クラフトの側は関数の名前と引数の名前しか手掛かりが無い。

    分かっている   店に並ぶ品の value は、その土地の依頼の難易度以外の数を取らない
                   （実セーブ3世界・店23軒。完了済みの依頼も母数に入る）
    読めていない   その値を選んでいる呼び出しの実引数
                   （`get_area_quest_difficulty_for_tier` の `tier` が
                     施設の basic/standard/advanced なのか、品物ごとの段なのか）
    読めていない   クラフトの成果物の性能を決める式
                   （`ItemCraftManager.calculate_modification(item_type, item_price)`
                     の戻り値が何なのか。数なのか辞書なのかも未確認）

この MOD が録るのはその2つ。
`318_` は在庫にもクラフトにも手を触れずに依頼の難易度だけを動かすので、
**ここで測った内容がそのまま「本当に下流へ流れたか」の答え**になる。

| 何を録るか | 見どころ |
| --- | --- |
| 土地の難易度を返す4関数 | 引数と戻り値。誰が `tier` に何を渡しているか |
| 難易度 ↔ 値段 ↔ レベルの変換 | `get_*_price(難易度)` と `get_*_level_from_price(値段)` の対応表 |
| 店の品揃えの生成 | `set_item_from_world_data` の `next_tier` と、生成後に主が持っている品の value |
| クラフト | `calculate_modification` の実引数と**戻り値の型**、素材と成果物の value |

200番台の約束どおり読み取りだけ。`safe=True` と握り潰しで、
記録に失敗しても本体は必ず1回呼ぶ。

出力は `out/item_level.log`（読む用）と `out/item_level.jsonl`（1件1行）。
純関数の対応表は**同じ引数の組を1度しか書かない**（`220_` と同じ形）。
"""

import datetime
import json

from instantale_modloader import frames, ui

LOG_BASENAME = "item_level.log"
RECORD_BASENAME = "item_level.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
TABLE_SAMPLES = 120
ITEM_SAMPLES = 200

# 土地の難易度を返す4関数。
# 店の品揃えも `318_` もここを源にしている。
# 引数のどれが `tier` なのかは名前で分かるので、実値だけ欲しい。
AREA_TARGETS = (
    "get_quest_difficulties",
    "get_active_quest_difficulties",
    "get_area_average_difficulty",
    "get_area_quest_difficulty_for_tier",
)

# 難易度 → 値段（3種）と、値段 → レベル（3種）。
# この6本が対になっているなら、品物の value は難易度そのものだと言い切れる。
PRICE_TARGETS = (
    "get_equipment_price",
    "get_heal_item_price",
    "get_other_item_price",
    "get_equipment_level_from_price",
    "get_heal_item_level_from_price",
    "get_other_item_level_from_price",
)

# 品物1つの数値を作る側。クラフトの成果物がここを通るかを見る。
SPEC_TARGETS = (
    "get_item_base_price",
    "get_randomized_item_price",
    "get_weapon_spec",
    "get_heal_spec",
    "get_item_skill_usefulness",
)


def item_brief(item, limit=40):
    """品物1つを数で写す。説明文と画像は要らない。"""
    if item is None:
        return None
    if isinstance(item, dict):
        get = item.get
    else:
        def get(name, default=None):
            return frames.attr(item, name, default)
    attributes = get("attributes", None)
    return {
        "name": frames.short(get("name", ""), limit),
        "item_type": get("item_type", None),
        "value": get("value", None),
        "rarity": get("rarity", None),
        "upgrade_level": get("upgrade_level", None),
        "attributes": attributes if isinstance(attributes, dict) else None,
    }


def inventory_values(character):
    """持ち物の value を並べる。品揃えの段を見るのに要るのはこれだけ。"""
    inventory = frames.attr(character, "inventory", None)
    if isinstance(inventory, dict):
        items = list(inventory.values())
    elif isinstance(inventory, (list, tuple)):
        items = list(inventory)
    else:
        return None
    values = []
    for item in items:
        value = item.get("value") if isinstance(item, dict) \
            else frames.attr(item, "value", None)
        values.append(value)
    return values


def apply(ctx):
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    # 同じ引数の組は1度しか書かない（表を作るのが目的で、回数は要らない）。
    seen = {"table": {}, "items": 0}

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("item level probe: record failed")

    def brief(value):
        """引数を短く写す。オブジェクトは id と名前だけ。"""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return frames.short(value, 60)
        if isinstance(value, (list, tuple)):
            return [brief(item) for item in list(value)[:8]]
        if isinstance(value, dict):
            return {str(key): brief(value[key]) for key in list(value)[:8]}
        return frames.short(frames.describe_instance(value), 80)

    def table(group, name, args, kwargs, result):
        """純関数の対応表。同じ引数の組は1度だけ。"""
        if TABLE_SAMPLES <= 0 or len(seen["table"]) >= TABLE_SAMPLES:
            return
        shown = [brief(value) for value in args]
        shown_kwargs = {key: brief(value) for key, value in kwargs.items()}
        key = json.dumps([name, shown, shown_kwargs], ensure_ascii=False,
                         sort_keys=True, default=str)
        if key in seen["table"]:
            return
        seen["table"][key] = True
        record({"at": now(), "phase": group, "func": name,
                "args": shown, "kwargs": shown_kwargs,
                "result": brief(result),
                "result_type": type(result).__name__})
        write("{}: {}({}) -> {!r} [{}]".format(
            group, name,
            ", ".join([json.dumps(value, ensure_ascii=False, default=str)
                       for value in shown]
                      + ["{}={}".format(key, json.dumps(value, ensure_ascii=False,
                                                        default=str))
                         for key, value in shown_kwargs.items()]),
            brief(result), type(result).__name__))

    def watch_pure(group, name):
        @ctx.wrap("scripts.functions:{}".format(name), required=False, safe=True)
        def pure(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                table(group, name, args, kwargs, result)
            except Exception:
                # 記録に失敗しても戻り値は素通しする（値付けを止めない）。
                pass
            return result
        return pure

    for name in AREA_TARGETS:
        watch_pure("土地の難易度", name)
    for name in PRICE_TARGETS:
        watch_pure("値段とレベル", name)
    for name in SPEC_TARGETS:
        watch_pure("品物の数値", name)

    def take_item_slot():
        if ITEM_SAMPLES <= 0 or seen["items"] >= ITEM_SAMPLES:
            return False
        seen["items"] += 1
        return True

    # ------------------------------------------------------------ 店の品揃え
    def shop_context(app, owner):
        """主の居る施設と土地。`tier` の正体を確かめるのに要る。"""
        area = ui.current_area(app)
        facility = frames.attr(frames.attr(app, "player", None), "location", None)
        return {"owner": {"id": frames.attr(owner, "id", None),
                          "name": frames.short(frames.attr(owner, "name", ""), 40)},
                "facility": {"id": frames.attr(facility, "id", None),
                             "type": ui.facility_type_of(facility),
                             "tier": frames.attr(facility, "tier", None)},
                "area": {"id": ui.area_id_of(area),
                         "name": frames.attr(area, "name", None)}}

    @ctx.wrap("__main__:ShoppingStartManagerRemake.set_item_from_world_data",
              required=False, safe=True)
    def set_item_from_world_data(orig, self, shop_owner_instance, next_tier=None,
                                 *args, **kwargs):
        before = inventory_values(shop_owner_instance)
        result = orig(self, shop_owner_instance, next_tier, *args, **kwargs)
        try:
            if take_item_slot():
                app = frames.attr(self, "app", None) or ui.find_app()
                row = {"at": now(), "phase": "店の品揃え",
                       "next_tier": brief(next_tier),
                       "before": before,
                       "after": inventory_values(shop_owner_instance)}
                row.update(shop_context(app, shop_owner_instance))
                record(row)
                write("店の品揃え: {} tier={!r} 施設={} value {} -> {}".format(
                    row["owner"]["name"], next_tier,
                    "{}/{}".format(row["facility"]["type"], row["facility"]["tier"]),
                    before, row["after"]))
        except Exception:
            ctx.log_exc("item level probe: cannot record the restock")
        return result

    @ctx.wrap("__main__:ShoppingStartManagerRemake.generate_item_in_shopping",
              required=False, safe=True)
    def generate_item_in_shopping(orig, self, item_data=None,
                                  shop_owner_instance=None, item_stock_tier=None,
                                  *args, **kwargs):
        result = orig(self, item_data, shop_owner_instance, item_stock_tier,
                      *args, **kwargs)
        try:
            if take_item_slot():
                record({"at": now(), "phase": "店の品1つ",
                        "item_stock_tier": brief(item_stock_tier),
                        "item_data": brief(item_data),
                        "result": item_brief(result) if result is not None
                        else None,
                        "result_type": type(result).__name__})
                write("店の品1つ: tier={!r} -> {}".format(
                    item_stock_tier, item_brief(result)))
        except Exception:
            ctx.log_exc("item level probe: cannot record the generated item")
        return result

    # ------------------------------------------------------------ クラフト
    @ctx.wrap("__main__:ItemCraftManager.calculate_modification",
              required=False, safe=True)
    def calculate_modification(orig, self, item_type=None, item_price=None,
                               *args, **kwargs):
        """**この MOD の主目的**。引数の実値と、戻り値の型を録る。

        名前は `item_price` だが、渡っているのが素材の合計なのか1つぶんなのか、
        戻り値が数なのか辞書なのかが読めていない。
        `318_` がクラフトへ直接手を出さずに済むかは、ここの答えで決まる。
        """
        result = orig(self, item_type, item_price, *args, **kwargs)
        try:
            table("クラフトの式", "ItemCraftManager.calculate_modification",
                  (item_type, item_price), kwargs, result)
        except Exception:
            pass
        return result

    @ctx.wrap("scripts.llm.llm_manager:item_craft_generator",
              required=False, safe=True)
    def item_craft_generator(orig, material_list=None, *args, **kwargs):
        materials = None
        try:
            if isinstance(material_list, (list, tuple)):
                materials = [item_brief(item) for item in material_list[:8]]
        except Exception:
            materials = None
        result = orig(material_list, *args, **kwargs)
        try:
            if take_item_slot():
                record({"at": now(), "phase": "クラフトの生成",
                        "materials": materials,
                        "args": [brief(value) for value in args],
                        "result": brief(result),
                        "result_type": type(result).__name__})
                write("クラフトの生成: 素材 {} -> {}".format(materials, brief(result)))
        except Exception:
            ctx.log_exc("item level probe: cannot record the craft request")
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.place_crafted_item",
              required=False, safe=True)
    def place_crafted_item(orig, self, generated_item=None,
                           generated_item_id=None, *args, **kwargs):
        try:
            if take_item_slot():
                record({"at": now(), "phase": "クラフトの成果物",
                        "id": brief(generated_item_id),
                        "item": item_brief(generated_item)})
                write("クラフトの成果物: {}".format(item_brief(generated_item)))
        except Exception:
            ctx.log_exc("item level probe: cannot record the crafted item")
        return orig(self, generated_item, generated_item_id, *args, **kwargs)

    ctx.log("item level probe: table<={} item<={}; log goes to out/{} and out/{}"
            .format(TABLE_SAMPLES, ITEM_SAMPLES, LOG_BASENAME, RECORD_BASENAME))
