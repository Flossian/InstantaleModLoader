# -*- coding: utf-8 -*-
"""アイテムの効果を、分類ごとに作り直す。

## 何をする MOD か

素のゲームのアイテムは、分類や細分が違っても効き方がほとんど同じか、効き方そのものが無い。
この MOD は分類ごとに効き方を決め直す。
ファイルは分類ごとに分け、この入口が方針（設定・文言・フックの設置）を持つ。

| ファイル | 分類 | 状態 |
| --- | --- | --- |
| `healing.py` | 回復アイテム（`healing_item` / `consumable` の食料・飲み物・医薬品・ポーション・薬草・きのこ） | この版 |
| （未着手） | 武器・防具の付加効果、道具・書物・素材の効果 | 次以降 |

## 回復アイテム（この版）

素のゲームの回復アイテムは細分に関係なく全部同じ動きをする。
`attributes` の `疲労負荷`（スタミナ。`physical_integrity`）を払って `回復` ぶん HP を戻す。
その `疲労負荷` は品の `value` 3〜75 の全域で 9〜11 しか無く（GAME.md §2.13.2）、
スタミナの上限はレベル1で 10（§2.19）。
**序盤は回復アイテム1つでスタミナが尽きる**うえ、食料も薬も同じ物になっている。

細分（`attributes["item_detail"]`）で効き方を分け、スタミナは払わない。

| 細分 | 効き方 | `attributes` に書くもの |
| --- | --- | --- |
| `food` / `drink` | スタミナ回復 | `スタミナ回復: N` |
| `medicine` | HP 回復 | `回復: N%`（最大 HP に対する割合。生成時に確定） |
| `potion` | HP 回復（医薬品の 1/4）＋ 状態異常の解除 | `回復: N%` / `状態異常: 解除` |
| `plant` / `mushroom` | HP とスタミナが増減。**幅は生成時に決めて品に書く** | `HP増減: ±N%` / `スタミナ増減: ±N` |

スタミナの量は `value`（1〜70 の価値段階。その土地の依頼の難易度と同じ）から組む。
スタミナの上限はレベルとほぼ同じ速さで伸びる（Lv1 で 10、Lv30 で 26、Lv73 で 50）ので、
`value` に比例させておけば、その土地で買った食料はその土地に居るレベルの
半分ほどを戻す釣り合いになる。

HP は**最大 HP に対する割合**で書く（`回復: 25%`）。
素のゲームの `回復` は価値段階 3 で 9、70 で 349 と 40 倍近く伸びるのに、HP は序盤でも
数百・Lv60 で 1400 ほどで、固定値だと序盤の薬が数% しか戻らない。
割合なら HP の伸び方（未計測）に寄りかからずに済み、スタミナ（上限がレベルに追従する）と
同じ釣り合いになる。価値段階の意味を残すため、割合の中に価値段階を入れる:

    医薬品     = (20% + 価値段階 × 0.3%) × レア度 × ±10%
    ポーション = その 1/4
    薬草       = 医薬品の底の 0.5〜1.0 倍。向きは 7 割で増

レア度倍率は 6 段（common 0.8 / rare 0.9 / magical 1.0 / epic 1.1 / legendary 1.25 / mythic 1.5）で、
スタミナと HP の両方に掛かる。乱数（±10%）はどれも**生成時に1回**引いて品に書く。
同じ品を使えば毎回同じ量。量に直すのは使う時で、使う人の最大 HP を底にする。

薬草・きのこの増減は**生成した時点で乱数を引いて `attributes` に書く**。
使う時に引くと、同じ品を見て買ったのに結果が違う「見えないくじ」になる。
書いておけば説明欄にそのまま出るし、セーブにも残る。

### 本体をどう通すか（`226_` の実測より）

右クリックの「消費」は `Item.consume` → 本体が `usable`（スタミナ ≥ `疲労負荷`）を決める →
別スレッドで `ItemConsumeManager.consume_item(item, usable)`。
`consume_item` は `attributes` を**使う時に**読み、
スタミナを払い、HP を戻し、持ち物から外し、「…を消費した。HPを N だけ回復した。」を出す。
`usable` が偽なら「駄目だ...体がもたない。」だけ出して何もしない。
上限（`update_max_hp` / `update_max_physical_integrity`）はこの間に走らない。

そこで本体は**呼ぶ**（持ち物から外す・画面を塗る手順を本体に任せる）が、
効果は本体に何もさせない:

  * `疲労負荷` は品に **0 で書き残す**（本体の `usable` が常に真になり、払う量も 0）。
    説明欄では 0 の行を伏せる
  * `回復` は `consume_item` の**間だけ 0** にして呼び、戻す（`healing.Silenced`）
  * 本体が出す1行は、同じスレッドの `add_text` で差し替える
    （品の名前を含む最初の1行だけ。差し替えたら旗を立てる）
  * 持ち物の数が減っていれば（＝本体が消費したら）、こちらの効果を乗せる

「本体を呼ばずに全部書く」ほうが素直に見えるが、
品を外す・グリッドを空ける・窓を塗り直す手順は本体しか知らない。
効果だけを 0 にして通すほうが、本体の更新にも強い。

### 触らないもの

`scroll`（巻物）と、細分の無い品（本体同梱の `healing_herb`）は素のまま。
値段は `129_` が付ける（食料は `回復` を失うので価値段階の軸に落ちる。それでよい）。
ポーションの**バフ**は、能力値の実行時の持ち方（`ability_scores` / `original_ability_scores` /
`update_ability_scores` の関係）が測れていないので、この版では状態異常の解除だけ。
"""

import random
import sys
import threading

from instantale_modloader import frames, ui

from . import healing

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
STAMINA_BASE = 3.0          # 食料のスタミナ回復の底
STAMINA_PER_VALUE = 0.35    # 価値段階 1 あたりの上乗せ
DRINK_RATE = 0.7            # 飲み物は食料のこの割合
STAMINA_VARIANCE = 0.1      # スタミナ回復の幅。±この割合で生成時に1回引く
HP_BASE_PCT = 20.0          # 医薬品の HP 回復の底（最大 HP の %）
HP_PCT_PER_VALUE = 0.3      # 価値段階 1 あたりの上乗せ（%）
HP_VARIANCE = 0.1           # HP 回復の幅。±この割合で生成時に1回引く
POTION_HP_RATE = 0.25       # ポーションの HP 回復は医薬品のこの割合（解除が本分。同量だと医薬品を選ぶ理由が無い）
RARITY_COMMON = 0.8         # レア度倍率（6段）。スタミナと HP の両方に掛かる
RARITY_RARE = 0.9
RARITY_MAGICAL = 1.0
RARITY_EPIC = 1.1
RARITY_LEGENDARY = 1.25
RARITY_MYTHIC = 1.5
HERB_GOOD_CHANCE = 0.7      # 薬草・きのこの増減が「増」になる確率（HP とスタミナで別々に引く）
HERB_MIN_RATE = 0.5         # 増減の大きさの下限（食料・医薬品の量に対する割合）
POTION_CLEARS_STATUS = True # ポーションで状態異常（`Character.status`）を空にする
REWORK_ON_SIGHT = True      # 既にセーブに在る品も、画面に出た時点で作り直す

LOG_BASENAME = "item_effects.log"
LOG_LIMIT = 300

# 画面に出す文言。分類ごとの語彙はそれぞれのファイルが持ち、文はここ。
TEXTS = {
    "ja": {"consumed": "{name}を消費した。",
           "stamina_up": "スタミナが{n}回復した。", "stamina_down": "スタミナが{n}減った。",
           "hp_up": "HPが{n}回復した。", "hp_down": "HPが{n}減った。",
           "cleared": "状態異常が消えた。", "nothing": "何も起きなかった。"},
    "en": {"consumed": "Consumed {name}.",
           "stamina_up": "Stamina +{n}.", "stamina_down": "Stamina -{n}.",
           "hp_up": "HP +{n}.", "hp_down": "HP -{n}.",
           "cleared": "Ailments cured.", "nothing": "Nothing happened."},
}

# 再注入しても1組だけ持つ（件数）。
STORE_ATTR = "_instantale_item_effects_store"

# 分類ごとのファイルが公開している読み方。検査と他の分類から同じ名前で引けるように。
kind_of = healing.kind_of
is_reworked = healing.is_reworked
effect_of = healing.effect_of


def language():
    """ゲームの表示言語。`scripts.languages.language`。読めなければ ja。"""
    module = sys.modules.get("scripts.languages")
    lang = getattr(module, "language", None) if module is not None else None
    return "en" if lang == "en" else "ja"


def labels_of():
    return healing.LABELS[language()]


def rules_of():
    """設定を分類ごとのファイルへ渡す形にまとめる（設定はこの入口だけが読む）。"""
    return {
        "rarity": {"common": float(RARITY_COMMON), "rare": float(RARITY_RARE),
                   "magical": float(RARITY_MAGICAL), "epic": float(RARITY_EPIC),
                   "legendary": float(RARITY_LEGENDARY), "mythic": float(RARITY_MYTHIC)},
        "stamina_base": float(STAMINA_BASE),
        "stamina_per_value": float(STAMINA_PER_VALUE),
        "drink_rate": float(DRINK_RATE),
        "stamina_variance": float(STAMINA_VARIANCE),
        "hp_base_pct": float(HP_BASE_PCT),
        "hp_pct_per_value": float(HP_PCT_PER_VALUE),
        "hp_variance": float(HP_VARIANCE),
        "potion_hp_rate": float(POTION_HP_RATE),
        "herb_good_chance": float(HERB_GOOD_CHANCE),
        "herb_min_rate": float(HERB_MIN_RATE),
        "potion_clears_status": bool(POTION_CLEARS_STATUS),
    }


def rework(item, why="", rng=random):
    """品1つを作り直す。書いた効果の辞書か、何もしなければ False（記録は呼ぶ側）。"""
    return healing.rework(item, rules_of(), labels_of(), rng)


def compose(item_name, planned, clear):
    texts = TEXTS[language()]
    parts = [texts["consumed"].format(name=item_name)]
    if planned["stamina"] > 0:
        parts.append(texts["stamina_up"].format(n=planned["stamina"]))
    elif planned["stamina"] < 0:
        parts.append(texts["stamina_down"].format(n=-planned["stamina"]))
    if planned["hp"] > 0:
        parts.append(texts["hp_up"].format(n=planned["hp"]))
    elif planned["hp"] < 0:
        parts.append(texts["hp_down"].format(n=-planned["hp"]))
    if clear and planned["clear"]:
        parts.append(texts["cleared"])
    if len(parts) == 1:
        parts.append(texts["nothing"])
    return "".join(parts) if language() == "ja" else " ".join(parts)


def apply(ctx):
    blanks = {"logged": 0, "reworked": 0, "consumed": 0, "refused": 0}
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = dict(blanks)
        setattr(sys, STORE_ATTR, store)
    else:
        for name, blank in blanks.items():
            store.setdefault(name, blank)

    write = ctx.logger(LOG_BASENAME, stamp=False)
    schedule = ui.scheduler(ctx, "item effects")

    def note(text):
        store["logged"] += 1
        if store["logged"] <= LOG_LIMIT:
            write(text)
        elif store["logged"] == LOG_LIMIT + 1:
            write("... 以降は件数だけ数える（LOG_LIMIT={}）".format(LOG_LIMIT))

    def name_of(item):
        field, attributes = healing.read_item(item)
        return "{!r}/{}/{}".format(
            field("name"), (attributes or {}).get("item_detail"), field("rarity"))

    # 同じスレッドで走っている `consume_item` の控え。
    # `add_text` の差し替えは、この控えを持つスレッドの、品の名前を含む最初の1行だけ。
    active = {}

    # ---- 品を作り直す --------------------------------------------------
    def rework_one(item, why, rng=random):
        effects = rework(item, why, rng)
        if effects:
            store["reworked"] += 1
            note("{} {} -> {}".format(why, name_of(item), effects))
            return True
        return False

    def rework_inventory(obtainer, why):
        inventory = frames.attr(obtainer, "inventory", None)
        if isinstance(inventory, dict):
            items = list(inventory.values())
        elif isinstance(inventory, (list, tuple)):
            items = list(inventory)
        else:
            return 0
        return sum(1 for item in items if rework_one(item, why))

    # ---- 生成の経路（`129_` と同じ3本）---------------------------------
    @ctx.wrap("__main__:InstantaleApp.generate_item_from_item_data", safe=True)
    def generated_from_data(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        rework_one(result, "generated")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_dict", safe=True)
    def generated_from_dict(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        rework_one(result, "generated/dict")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_ready_made_data",
              safe=True, required=False)
    def generated_ready_made(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        rework_one(result, "generated/ready_made")
        return result

    # ---- 既にセーブに在る品 ----------------------------------------------
    if REWORK_ON_SIGHT:
        @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window", safe=True)
        def twin_window(orig, self, left_inventory_obtainer=None,
                        right_inventory_obtainer=None, left_label_text=None,
                        situation=None, *args, **kwargs):
            rework_inventory(left_inventory_obtainer, "window/left")
            rework_inventory(right_inventory_obtainer, "window/right")
            return orig(self, left_inventory_obtainer, right_inventory_obtainer,
                        left_label_text, situation, *args, **kwargs)

        @ctx.wrap("__main__:InstantaleApp.toggle_center_inventory_window",
                  safe=True, required=False)
        def center_window(orig, self, *args, **kwargs):
            rework_inventory(frames.attr(self, "player", None), "window/center")
            return orig(self, *args, **kwargs)

    # ---- 説明欄: 作り直し済みの品の `疲労負荷: 0` を伏せる -----------------
    @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content", safe=True)
    def detail_box(orig, self, item=None, *args, **kwargs):
        if REWORK_ON_SIGHT:
            rework_one(item, "detail")
        _field, attributes = healing.read_item(item)
        with healing.Hidden(attributes):
            return orig(self, item, *args, **kwargs)

    # ---- 使う ------------------------------------------------------------
    def refresh(app):
        """HUD の数字を塗り直す。本体は自分の変更ぶんしか塗っていない。"""
        updater = frames.attr(app, "update_ui", None)
        if callable(updater):
            def run():
                try:
                    updater()
                except Exception:
                    ctx.log_exc("item effects: update_ui failed")
            schedule(run)

    @ctx.wrap("__main__:ItemConsumeManager.consume_item", safe=True)
    def consume_item(orig, self, item_instance=None, usable=None, *args, **kwargs):
        kind = kind_of(item_instance)
        if kind is None:
            return orig(self, item_instance, usable, *args, **kwargs)
        field, attributes = healing.read_item(item_instance)
        if not is_reworked(attributes):
            rework_one(item_instance, "consume")

        app = frames.attr(self, "app", None) or ui.find_app()
        owner = field("obtainer")
        if owner is None:
            owner = frames.attr(app, "player", None)
        # 割合で書かれた HP は、使う人の最大 HP から量に直す。
        hp_delta, stamina_delta, clear = effect_of(item_instance, owner)
        planned = healing.plan(owner, hp_delta, stamina_delta, clear)
        item_name = frames.text_of(item_instance, "name") or ""
        ticket = {"name": item_name, "shown": False,
                  "text": compose(item_name, planned, clear)}
        before = healing.inventory_size(owner)

        # 本体には何も回復させない。`回復` を間だけ 0 に、`usable` は真で通す。
        ident = threading.get_ident()
        active[ident] = ticket
        try:
            with healing.Silenced(attributes, labels_of()):
                result = orig(self, item_instance, True, *args, **kwargs)
        finally:
            active.pop(ident, None)

        after = healing.inventory_size(owner)
        consumed = True if before is None or after is None else after < before
        if not consumed:
            store["refused"] += 1
            note("refused {} (inventory {} -> {})".format(name_of(item_instance),
                                                          before, after))
            return result

        healing.commit(owner, planned)
        store["consumed"] += 1
        note("consumed {} kind={} planned={} owner={}".format(
            name_of(item_instance), kind, planned,
            frames.short(frames.attr(owner, "name", ""), 30)))
        if not ticket["shown"]:
            # 本体が1行も出さなかった（版が変わった等）。こちらで出す。
            adder = frames.attr(app, "add_text", None)
            if callable(adder):
                try:
                    adder(ticket["text"])
                except Exception:
                    ctx.log_exc("item effects: add_text failed")
        refresh(app)
        return result

    @ctx.wrap("__main__:InstantaleApp.add_text", safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        ticket = active.get(threading.get_ident())
        if (ticket is not None and not ticket["shown"]
                and isinstance(context, str) and ticket["name"]
                and ticket["name"] in context):
            ticket["shown"] = True
            context = ticket["text"]
        return orig(self, context, *args, **kwargs)

    write("---- installed  healing: stamina={:g}+{:g}*value drink={:g} var={:g} | "
          "hp={:g}%+{:g}%*value var={:g} potion={:g} | rarity={} herb good={:g} min={:g} "
          "potion_clears={} on_sight={} ----"
          .format(float(STAMINA_BASE), float(STAMINA_PER_VALUE), float(DRINK_RATE),
                  float(STAMINA_VARIANCE), float(HP_BASE_PCT), float(HP_PCT_PER_VALUE),
                  float(HP_VARIANCE), float(POTION_HP_RATE),
                  [float(RARITY_COMMON), float(RARITY_RARE), float(RARITY_MAGICAL),
                   float(RARITY_EPIC), float(RARITY_LEGENDARY), float(RARITY_MYTHIC)],
                  float(HERB_GOOD_CHANCE), float(HERB_MIN_RATE),
                  bool(POTION_CLEARS_STATUS), bool(REWORK_ON_SIGHT)))
    ctx.log("item effects: installed (healing: stamina {:g}+{:g}*value, hp {:g}%+{:g}%*value, "
            "on_sight={})".format(float(STAMINA_BASE), float(STAMINA_PER_VALUE),
                                  float(HP_BASE_PCT), float(HP_PCT_PER_VALUE),
                                  bool(REWORK_ON_SIGHT)))
