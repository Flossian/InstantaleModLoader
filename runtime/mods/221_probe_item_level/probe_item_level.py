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
| 土地の難易度を返す2関数 | 引数と戻り値。誰が `tier` に何を渡しているか |
| 難易度 ↔ 値段 ↔ レベルの変換 | `get_*_price(難易度)` と `get_*_level_from_price(値段)` の対応表 |
| 店の品揃えの生成 | `generate_item_in_shopping` に渡る段（`item_stock_tier`） |
| クラフト | `calculate_modification` の実引数と**戻り値の型**、素材と成果物の value |

200番台の約束どおり読み取りだけ。`safe=True` と握り潰しで、
記録に失敗しても本体は必ず1回呼ぶ。
包みは受け取った引数をそのまま本体へ渡す（キーワードで来た引数を位置へ直さない）。

出力は `out/item_level.log`（読む用）と `out/item_level.jsonl`（1件1行）。
純関数の対応表は**同じ引数の組を1度しか書かない**（`220_` と同じ形）。

版3: 1回目の計測で鎖は端まで繋がり（VERIFICATION_LOG.md §2.67）、残る問いは
`tier` の式とクラフトの倍率の式だけになったので、それに要らない記録を外した。

- 土地の難易度の `get_quest_difficulties` / `get_active_quest_difficulties` を外した。
  引数に土地を持つので組が土地ごとに分かれ、注入のたびに40行前後を書き直していた
  （1か月で約5,700行・`.jsonl` の7割）
- `set_item_from_world_data` の包みを外した。品揃えを作るのは
  `generate_item_in_shopping` で（GAME.md §2.13.1.2）、1か月で一度も来なかった
- `generate_item_in_shopping` の戻り値の列を外した。いつも `None`（品は主の持ち物へ入る）
- 「同じ組は1度だけ」と件数の控えを `sys` に置いて、注入し直しで数え直さないようにした
- クラフトの依頼は、件数の枠を先に取ってから素材を写す
"""

import datetime
import json
import sys

from instantale_modloader import frames

LOG_BASENAME = "item_level.log"
RECORD_BASENAME = "item_level.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
TABLE_SAMPLES = 120
ITEM_SAMPLES = 200

# 1プロセスで数を持ち越す置き場（注入し直しと遅延の当て直しで数え直さない）。
STORE_ATTR = "_instantale_probe_item_level"

# 土地の難易度を返す関数。
# `tier` の式を出すための2本だけを残した（版3）。
AREA_TARGETS = (
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

# 引数の並び（out/recon/targets.txt より）。
SHOPPING_ARGS = ("item_data", "shop_owner_instance", "item_stock_tier")
MODIFICATION_ARGS = ("item_type", "item_price")
CRAFT_ARGS = ("material_list", "prompt")
PLACE_ARGS = ("generated_item", "generated_item_id")


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


def process_store():
    """1プロセスで持ち越す控え。`sys` に置いて注入し直しをまたぐ（版3）。

    `table` は書いた引数の組、`items` は写した品物の件数。
    """
    found = getattr(sys, STORE_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, STORE_ATTR, found)
    found.setdefault("table", {})
    found.setdefault("items", 0)
    return found


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # 同じ引数の組は1度しか書かない（表を作るのが目的で、回数は要らない）。
    seen = process_store()

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    #: 1件1行の JSON。後から数えるための表（ローダの語彙）。
    record = ctx.jsonl(RECORD_BASENAME)

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
    @ctx.wrap("__main__:ShoppingStartManagerRemake.generate_item_in_shopping",
              required=False, safe=True)
    def generate_item_in_shopping(orig, self, *args, **kwargs):
        """品揃えの1品に渡る段。作った品は主の持ち物へ入り、戻り値は `None`（版3で列を外した）。"""
        result = orig(self, *args, **kwargs)
        try:
            if take_item_slot():
                tier = frames.arg(args, kwargs, "item_stock_tier", SHOPPING_ARGS)
                record({"at": now(), "phase": "店の品1つ",
                        "item_stock_tier": brief(tier),
                        "item_data": brief(frames.arg(args, kwargs, "item_data",
                                                      SHOPPING_ARGS))})
                write("店の品1つ: tier={!r}".format(tier))
        except Exception:
            ctx.log_exc("item level probe: cannot record the generated item")
        return result

    # ------------------------------------------------------------ クラフト
    @ctx.wrap("__main__:ItemCraftManager.calculate_modification",
              required=False, safe=True)
    def calculate_modification(orig, self, *args, **kwargs):
        """**この MOD の主目的**。引数の実値と、戻り値の型を録る。

        名前は `item_price` だが、渡っているのが素材の合計なのか1つぶんなのか、
        戻り値が数なのか辞書なのかが読めていない。
        `318_` がクラフトへ直接手を出さずに済むかは、ここの答えで決まる。
        """
        result = orig(self, *args, **kwargs)
        try:
            table("クラフトの式", "ItemCraftManager.calculate_modification",
                  (frames.arg(args, kwargs, "item_type", MODIFICATION_ARGS),
                   frames.arg(args, kwargs, "item_price", MODIFICATION_ARGS)),
                  {}, result)
        except Exception:
            pass
        return result

    @ctx.wrap("scripts.llm.llm_manager:item_craft_generator",
              required=False, safe=True)
    def item_craft_generator(orig, *args, **kwargs):
        # 件数の枠を先に取る。枠が無ければ素材を写さない（版3）。
        slot = False
        materials = None
        try:
            slot = take_item_slot()
            material_list = frames.arg(args, kwargs, "material_list", CRAFT_ARGS)
            if slot and isinstance(material_list, (list, tuple)):
                materials = [item_brief(item) for item in material_list[:8]]
        except Exception:
            materials = None
        result = orig(*args, **kwargs)
        try:
            if slot:
                record({"at": now(), "phase": "クラフトの生成",
                        "materials": materials,
                        "args": [brief(value) for value in
                                 (args if "material_list" in kwargs else args[1:])],
                        "result": brief(result),
                        "result_type": type(result).__name__})
                write("クラフトの生成: 素材 {} -> {}".format(materials, brief(result)))
        except Exception:
            ctx.log_exc("item level probe: cannot record the craft request")
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.place_crafted_item",
              required=False, safe=True)
    def place_crafted_item(orig, self, *args, **kwargs):
        try:
            if take_item_slot():
                item = frames.arg(args, kwargs, "generated_item", PLACE_ARGS)
                record({"at": now(), "phase": "クラフトの成果物",
                        "id": brief(frames.arg(args, kwargs, "generated_item_id",
                                               PLACE_ARGS)),
                        "item": item_brief(item)})
                write("クラフトの成果物: {}".format(item_brief(item)))
        except Exception:
            ctx.log_exc("item level probe: cannot record the crafted item")
        return orig(self, *args, **kwargs)

    ctx.log("item level probe: table<={} item<={}; log goes to out/{} and out/{}"
            .format(TABLE_SAMPLES, ITEM_SAMPLES, LOG_BASENAME, RECORD_BASENAME))
