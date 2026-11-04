# -*- coding: utf-8 -*-
"""epic 以上の装備品に、生成時に能力値補正（筋力 +3 など）と武器・防具の効果（会心 15% など）を付ける。

## 付け方

武器・防具（`item_type` が weapon / wearable）が作られたときに、レア度で抽選して
`attributes` に「筋力: +3」の形で書く。説明欄にそのまま出て、セーブにも残る。
付くのは epic / legendary / mythic だけで、数と量はレア度ごとに設定で決める。
武器と防具にはさらに、それぞれの効果を「会心: 15%」の形で書く
（種類と決まりは `effects.py`、戦闘への効かせ方は `battle.py`）。

作られた瞬間を拾う経路は `410_` と同じ:

  * `generate_item_from_item_data` の最中に `Item.__init__` で組まれた装備（店の品揃え・クラフト・入手品）
  * テキストで頼んで `master_ai_item_generate` が名付けた装備（入口の外で組まれる）
  * クラフトの取りこぼし（`place_crafted_item`）

`generate_item_from_dict` はセーブからの組み直しにも使われるので見ない。
1つの品を抽選するのは1回だけ。もともと持っている品には付かない。

## 効かせ方

身に着けている品の補正を、ローダの能力値の窓口（`abilities.declare`。TECH.md §3.3.27）に答える。
ゲームが能力値を読む場面（素点・防御・手番の順・依頼の出来事・印象）で、窓口が補正を足して見せる。
能力値そのものは書き換えない。答えるのは主人公と、`APPLY_TO_PARTY` なら仲間。敵には答えない。

身に着けている品は装備欄の窓口（`equipment.gear`。`333_` が答える）から読み、
答えが無ければ本体の `equipments` の weapon / wearable を読む。
合算の仕方は `333_` の合算の設定に関係なく、この MOD の設定で決める。

武器の効果は、主人公と仲間（`APPLY_TO_PARTY`）が身に着けている武器のものを、
その者の1手の中でだけ効かせる（`battle.py`）。
防具の効果は同じ者が身に着けている防具のもので、同じ種類を複数の部位に着ても重ならず、最大の1品だけが効く。
重装の器用の低下は、能力値の窓口へ答える補正に足す。
威光は主人公が着ているときだけ、値段の窓口（`prices.adjust`）で店の買値を下げる。
"""

import os
import sys
import threading
import weakref

from instantale_modloader import abilities, equipment, frames, items, llm, prices, ui

from . import battle, bonus, effects

# ---- 設定（既定値は mod.json の "settings" と一致させること。TECH.md §3.8）----
EPIC_COUNT_MIN = 1          # epic: 補正の数
EPIC_COUNT_MAX = 1
EPIC_AMOUNT_MIN = 1         # epic: 1つあたりの量
EPIC_AMOUNT_MAX = 2
LEGENDARY_COUNT_MIN = 1
LEGENDARY_COUNT_MAX = 2
LEGENDARY_AMOUNT_MIN = 2
LEGENDARY_AMOUNT_MAX = 3
MYTHIC_COUNT_MIN = 2
MYTHIC_COUNT_MAX = 3
MYTHIC_AMOUNT_MIN = 3
MYTHIC_AMOUNT_MAX = 4
STACK_MODE = "sum"          # sum: 全部位を足す / max: 能力値ごとに最大の1品
STAT_CAP = 15               # 能力値ごとの上限（0 で上限なし）
INCLUDE_FREE_SLOTS = False  # 装備欄の自由枠の品も数える（333_ は自由枠を数値に入れない）
APPLY_TO_PARTY = True       # 仲間が身に着けている品の補正も効かせる
WEAPON_EPIC_MIN = 0         # 武器の効果の数（epic）
WEAPON_EPIC_MAX = 1
WEAPON_LEGENDARY_MIN = 1
WEAPON_LEGENDARY_MAX = 1
WEAPON_MYTHIC_MIN = 1
WEAPON_MYTHIC_MAX = 2
EFFECT_SLAYER = True        # 付けてよい武器の効果
EFFECT_CRITICAL = True
EFFECT_LIFESTEAL = True
EFFECT_PIERCE = True
EFFECT_VENOM = True
EFFECT_DAUNT = True
EFFECT_CURSE = True
EFFECT_GROWTH = True
EFFECT_EXECUTE = True
EFFECT_TEAMWORK = True
ARMOR_EPIC_MIN = 0          # 防具の効果の数（epic）
ARMOR_EPIC_MAX = 1
ARMOR_LEGENDARY_MIN = 1
ARMOR_LEGENDARY_MAX = 1
ARMOR_MYTHIC_MIN = 1
ARMOR_MYTHIC_MAX = 2
EFFECT_ENDURE = True        # 付けてよい防具の効果
EFFECT_THORNS = True
EFFECT_REGEN = True
EFFECT_HEAVY = True
EFFECT_GUARDIAN = True
EFFECT_RESIST = True
EFFECT_EVADE = True
EFFECT_DESPERATE = True
EFFECT_BINDING = True
EFFECT_PRESENCE = True

LOG_BASENAME = "equipment_bonuses.log"
LOG_LIMIT = 300

#: 自由枠（`333_` の部位名）。
FREE_PREFIX = "free"

STORE_ATTR = "_instantale_equipment_bonuses_store"


def process_store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {}
        setattr(sys, STORE_ATTR, store)
    store.setdefault("rolled", weakref.WeakSet())
    store.setdefault("ids", set())        # weakref を取れない品の控え（id）
    store.setdefault("logged", 0)
    store.setdefault("counts", {})
    store.setdefault("pending", {})       # master_ai_item_generate が名付けた装備
    store.setdefault("local", threading.local())
    return store


def language():
    """ゲームの表示言語。`scripts.languages.language`。読めなければ ja。"""
    module = sys.modules.get("scripts.languages")
    lang = getattr(module, "language", None) if module is not None else None
    return "en" if lang == "en" else "ja"


def tiers():
    """設定をレア度の表にまとめる（apply の後で読む。トップレベルで組むと既定値で固まる）。"""
    return {
        "epic": (EPIC_COUNT_MIN, EPIC_COUNT_MAX, EPIC_AMOUNT_MIN, EPIC_AMOUNT_MAX),
        "legendary": (LEGENDARY_COUNT_MIN, LEGENDARY_COUNT_MAX,
                      LEGENDARY_AMOUNT_MIN, LEGENDARY_AMOUNT_MAX),
        "mythic": (MYTHIC_COUNT_MIN, MYTHIC_COUNT_MAX, MYTHIC_AMOUNT_MIN, MYTHIC_AMOUNT_MAX),
    }


def weapon_counts():
    """武器の効果の数の表（`tiers` と同じく apply の後で読む）。"""
    return {"epic": (WEAPON_EPIC_MIN, WEAPON_EPIC_MAX),
            "legendary": (WEAPON_LEGENDARY_MIN, WEAPON_LEGENDARY_MAX),
            "mythic": (WEAPON_MYTHIC_MIN, WEAPON_MYTHIC_MAX)}


def armor_counts():
    """防具の効果の数の表。"""
    return {"epic": (ARMOR_EPIC_MIN, ARMOR_EPIC_MAX),
            "legendary": (ARMOR_LEGENDARY_MIN, ARMOR_LEGENDARY_MAX),
            "mythic": (ARMOR_MYTHIC_MIN, ARMOR_MYTHIC_MAX)}


def enabled_effects():
    flags = {"slayer": EFFECT_SLAYER, "critical": EFFECT_CRITICAL, "lifesteal": EFFECT_LIFESTEAL,
             "pierce": EFFECT_PIERCE, "venom": EFFECT_VENOM, "daunt": EFFECT_DAUNT,
             "curse": EFFECT_CURSE, "growth": EFFECT_GROWTH, "execute": EFFECT_EXECUTE,
             "teamwork": EFFECT_TEAMWORK}
    return [kind for kind in effects.WEAPON_KINDS if flags.get(kind)]


def enabled_armor_effects():
    flags = {"endure": EFFECT_ENDURE, "thorns": EFFECT_THORNS, "regen": EFFECT_REGEN,
             "heavy": EFFECT_HEAVY, "guardian": EFFECT_GUARDIAN, "resist": EFFECT_RESIST,
             "evade": EFFECT_EVADE, "desperate": EFFECT_DESPERATE, "binding": EFFECT_BINDING,
             "presence": EFFECT_PRESENCE}
    return [kind for kind in effects.ARMOR_KINDS if flags.get(kind)]


def unwrap(item):
    """画面の部品（`InventoryItem`）が来たら中の `item_instance` を取り出す。"""
    inner = frames.attr(item, "item_instance", None)
    return item if inner is None else inner


def worn_items(app, holder):
    """身に着けている品の並び（同じ品は1度だけ）。"""
    found = []
    gear = equipment.gear(app, holder)
    if gear is not None:
        for region, item in gear:
            if not INCLUDE_FREE_SLOTS and str(region).startswith(FREE_PREFIX):
                continue
            found.append(item)
    else:
        equipments = frames.attr(holder, "equipments", None)
        if isinstance(equipments, dict):
            inventory = items.inventory_of(holder) or {}
            for key in bonus.EQUIPMENT_TYPES:
                ref = equipments.get(key)
                if isinstance(ref, (str, int)) and not isinstance(ref, bool):
                    ref = inventory.get(str(ref))      # ロード直後は id の文字列
                if ref is not None:
                    found.append(ref)
    unique, seen = [], set()
    for item in found:
        if id(item) not in seen:
            seen.add(id(item))
            unique.append(item)
    return unique


def current_bonus(app, holder):
    return bonus.combine(worn_items(app, holder), STACK_MODE, STAT_CAP)


def current_armor(app, holder):
    """身に着けている防具の効果（答える相手でなければ空）。同じ種類は最大の1品だけが効く。"""
    if not answers_for(app, holder):
        return {}
    return effects.combine([item for item in worn_items(app, holder) if effects.is_armor(item)], "max")


def current_effects(app, holder):
    """身に着けている武器の効果（答える相手でなければ空）。"""
    if not answers_for(app, holder):
        return {}
    return effects.combine([item for item in worn_items(app, holder) if effects.is_weapon(item)],
                           STACK_MODE)


def answers_for(app, holder):
    """補正を答える相手か。主人公と、`APPLY_TO_PARTY` なら仲間。"""
    player = frames.attr(app, "player", None)
    if holder is player:
        return holder is not None
    if not APPLY_TO_PARTY:
        return False
    ident = abilities.holder_key(app, holder)
    return ident is not None and ident in {str(m) for m in ui.party_member_ids(app)}


def apply(ctx):
    store = process_store()
    local = store["local"]
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "411_equipment_bonuses"
    write = ctx.logger(LOG_BASENAME)

    def note(text):
        store["logged"] += 1
        if store["logged"] <= LOG_LIMIT:
            write(text)
        elif store["logged"] == LOG_LIMIT + 1:
            write("... 以降は件数だけ数える（LOG_LIMIT={}）".format(LOG_LIMIT))

    def count(key):
        store["counts"][key] = store["counts"].get(key, 0) + 1

    def name_of(item):
        return frames.short(frames.attr(item, "name", ""), 40)

    # ================================================================ 付ける
    def first_roll(item):
        """まだ抽選していない品なら印を付けて True。"""
        try:
            if item in store["rolled"]:
                return False
            store["rolled"].add(item)
            return True
        except TypeError:
            if id(item) in store["ids"]:
                return False
            store["ids"].add(id(item))
            return True

    def roll(item, source):
        item = unwrap(item)
        if item is None or isinstance(item, dict) or not bonus.is_equipment(item):
            return
        if not first_roll(item) or bonus.read(item) or effects.read(item):
            return
        rarity = str(frames.attr(item, "rarity", "") or "").lower()
        rolled = bonus.roll(rarity, tiers())
        if effects.is_weapon(item):
            found = effects.roll(rarity, weapon_counts(), enabled_effects())
        elif effects.is_armor(item):
            found = effects.roll(rarity, armor_counts(), enabled_armor_effects())
        else:
            found = []
        if not rolled and not found:
            count("plain/" + (rarity or "?"))
            return
        lang = language()
        if rolled and bonus.write(item, rolled, lang):
            count("bonus/" + rarity)
        if found and effects.write(item, found, lang):
            count("effect/" + rarity)
        note("{}: {!r} [{}] +{} {}".format(source, name_of(item), rarity, rolled,
                                           ["{} {}".format(k, effects.text_of(k, a, t, lang)) for k, a, t in found]))

    @ctx.wrap("scripts.items:Item.__init__", safe=True, required=False)
    def item_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if not bonus.is_equipment(self):
            return result
        collect = getattr(local, "collect", None)
        if isinstance(collect, list):
            collect.append(self)
            return result
        name = frames.attr(self, "name", None)
        if isinstance(name, str) and store["pending"].pop(name, None) is not None:
            roll(self, "text")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_item_data", safe=True)
    def generated(orig, self, *args, **kwargs):
        outer, local.collect = getattr(local, "collect", None), []
        try:
            result = orig(self, *args, **kwargs)
        finally:
            created, local.collect = local.collect, outer
        made = [item for item in created + [unwrap(result)]
                if item is not None and not isinstance(item, dict) and bonus.is_equipment(item)]
        for item in made:                 # ここで見たので、後の組み直しで拾い直さない
            store["pending"].pop(frames.attr(item, "name", None), None)
        if isinstance(outer, list):
            outer.extend(made)            # 外側の入口がまとめて見る
        else:
            for item in made:
                roll(item, "generated")
        return result

    @ctx.wrap("scripts.llm.llm_manager:master_ai_item_generate", safe=True,
              required=False)
    def master_item(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        data = llm.as_dict(result) or {}
        category = data.get("item_category")
        kind = category.get("type") if isinstance(category, dict) else None
        name = data.get("item_name")
        if kind in bonus.EQUIPMENT_TYPES and isinstance(name, str) \
                and getattr(local, "collect", None) is None:
            pending = store["pending"]
            pending[name] = True
            while len(pending) > 20:      # 組まれずに終わった名前を溜めない
                pending.pop(next(iter(pending)))
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.place_crafted_item",
              safe=True, required=False)
    def crafted(orig, self, *args, **kwargs):
        roll(frames.arg(args, kwargs, "generated_item", 0), "craft")
        return orig(self, *args, **kwargs)

    # ================================================================ 効かせる
    def answer(app, holder):
        """身に着けている品の補正（窓口が場面ごとに聞く）。"""
        if not answers_for(app, holder):
            return None
        found = current_bonus(app, holder)
        if current_armor(app, holder).get("heavy"):
            found["dexterity"] = found.get("dexterity", 0) - effects.HEAVY_DEX
        return found or None

    abilities.declare(owner, answer, write=write)
    battle.install(ctx, write, current_effects, current_armor, language)

    # ================================================================ 威光
    def presence_now():
        app = ui.find_app()
        player = frames.attr(app, "player", None)
        return current_armor(app, player).get("presence", 0) if player is not None else 0

    def presence_layer(_item, key, price):
        """主人公が威光の防具を着ていれば買値を下げる。触らないなら None。"""
        percent = presence_now()
        if key != prices.BUY or not percent:
            return None
        return max(1, int(round(price * (1.0 - percent / 100.0))))

    prices.install(ctx, write)
    prices.adjust(owner, presence_layer, temporary=True, write=write)
    seen_presence = {"percent": 0}

    @ctx.wrap("__main__:ShoppingStartManagerRemake.execute", required=False, safe=True)
    def shop_start(orig, self, *args, **kwargs):
        """着替えた後に店へ入ったら、古い額が残らないように組み直す。"""
        percent = presence_now()
        if percent != seen_presence["percent"]:
            seen_presence["percent"] = percent
            prices.refresh("presence armor")
        return orig(self, *args, **kwargs)

    write("installed tiers={} weapon={} effects={} armor={} effects={} mode={} cap={} free={} party={}".format(
        tiers(), weapon_counts(), enabled_effects(), armor_counts(), enabled_armor_effects(),
        STACK_MODE, STAT_CAP, INCLUDE_FREE_SLOTS, APPLY_TO_PARTY))
    ctx.log("equipment bonuses: installed (mode={}, cap={}, counts={})".format(
        STACK_MODE, STAT_CAP, store["counts"]))
