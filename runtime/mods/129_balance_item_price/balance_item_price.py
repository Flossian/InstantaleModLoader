# -*- coding: utf-8 -*-
"""アイテムの値段を、その品自身（種別・能力値・レア度・価値段階）から付け直す。

## 何が困るのか

素のゲームの値付けは **レア度をほとんど見ていない**。
同じ価値段階(`value`)なら common でも mythic でもほぼ同じ額になり、
店への売値はさらに低い（GAME.md §2.13.2）。
宿の一番安い部屋（簡易寝台）が 10G、一番高い部屋（高級個室）が 1000G。
どちらも3ヶ月ぶんの前払い。
という物価の中で、伝説級の戦利品が「高級個室ひと部屋ぶんにも満たない」ことになる。
拾った品を売っても意味が無く、店に並ぶ品を買う理由も薄い。

## 直し方。元の値段に倍率を掛けるのではなく、こちらで組み直す

元の額を倍にするだけでは、**壊れている比率をそのまま引き伸ばす**ことになる（レア度が効いていないのは倍率では直らない）。
そこで値段は次の式で一から組み、素の値段は読まない。

    値段 = 基準額(種別)            ← 能力値があればそれ、無ければ value から
         × レア度倍率
         × 種別グループの倍率
         × スキル倍率 / 強化倍率
         × 全体倍率

能力値を持つ品（武器の攻撃力・防具の防御力・回復量）は能力値を軸にし、
持たない品（素材・財宝・道具）は `value` を軸にする。
`value` は 1〜70 の価値段階で、その品が出たクエストの難易度と一致する（GAME.md
§2.13.2）。

軸を2本に分けているのは、同じ段階でも強い品は高い、という当たり前を通すため。
指数（`STAT_EXP_*` / `VALUE_EXP`）で伸びを付けてあるのは、
実データの値段が能力値に対して直線ではなく上に反っているのに合わせたもの。

## どこに書くか

値段はアイテムの `attributes` にゲーム自身が書いている。
店が売る品は `買価`、プレイヤーが売る品は `売価`（GAME.md §2.13.2）。

**この mod は値段を書かない。式を1枚置くだけ。**
書くのはローダの関所（`instantale_modloader.prices`）で、
`base_for(item)` が返した買価・売価を、関所が地点ごとに書く。

値段を書く対象は8つ、書く地点はその中に10あり、**書く時点が地点ごとに違う**
（画面の2対象＝3地点は描く前なので `orig` の前、残り6対象＝7地点は後）。
包みの勝敗は適用順ではなく `orig` の前に書くか後に書くかで決まるので、
値段を触る mod が別々に包むと、相手がどちらの層に居ても半分の地点で負ける
（TECH.md §3.3.1。実測は VERIFICATION.md §3.19.1 で、
`405_regional_economy` の地域倍率が買値だけ9回とも消えていた）。
8箇所を関所が1枚だけ包むことで、式を置く側も倍率を乗せる側も層を考えなくてよい。

## 決済とのずれ

`buy_item` / `sell_item` が表示どおりの額で決済しているかは、
こちらからは確かめられない（本体は凍結されていてソースが読めない）。
払わせてから見る:

    売買の前後で所持金を測る
      ├ 動いた額が表示と違えば、その差だけ直して WARN に残す
      │    └ 所持金が負にならない範囲で（払えないほど高い品を買えた場合）
      └ 合っていれば何もしない（1回目だけ「表示どおり」と記録に残す）

**合っていた回も1度は書く**のが要点。
ずれた回しか書かないと、ログの上では「合っていた」と
「一度も売買していない」が区別できず、
決済がこちらの値段を読んでいるのかを後から確かめようがない。

## 触らないもの

`get_item_base_price` は包まない。
あれは装備の強化費用（`calculate_modification(item_type, item_price)`）や、
価格から段階を逆算する `get_equipment_level_from_price` の入口でもある。
値段だけ膨らませると逆算側が定義域から外れて
`KeyError` を出しうる（`get_npc_employ_price` の前例。VERIFICATION_LOG.md §2.2）。
売買の値段はこの mod、内部の段階計算はゲーム自身、と分ける。
"""

import os
import sys

from instantale_modloader import prices, ui

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
PRICE_SCALE = 1.0          # 最後に全体へ掛かる倍率
SELL_RATE = 0.4            # 店に売るときの割合（買価に対して）

# item_type ごとの倍率。1.0 が「上の式そのまま」で、装備を下げ素材を上げてある
# のは実プレイで詰めた結果。装備は店で買うより拾うほうが早く、素材と財宝は
# 売り先がそこしかないので、同じ額なら素材側を厚くしたほうが釣り合う。
MULT_WEAPON = 0.7
MULT_WEARABLE = 0.7
MULT_HEALING_ITEM = 1.0
MULT_CONSUMABLE = 1.0
MULT_UTILITY = 0.8
MULT_MATERIAL = 1.5

RARITY_RARE = 1.4          # common は常に 1.0（基準）
RARITY_MAGICAL = 2.0
RARITY_EPIC = 3.0
RARITY_LEGENDARY = 4.5
RARITY_MYTHIC = 7.0

UPGRADE_STEP = 0.3         # 強化1段あたりの上乗せ
SKILL_MULT = 1.5           # スキル付きの倍率

MIN_PRICE = 5
MAX_PRICE = 9999999

REPRICE_ON_SIGHT = True    # 既にある品も、画面に出た時点で付け直す
RECONCILE_GOLD = True      # 決済額が表示とずれたら所持金を直す

LOG_BASENAME = "item_price.log"

# 1回の起動でログに残す付け直しの件数。
# 売買画面を開くたびに全品を通るので、上限が無いとログが数万行になる。
# 超えたぶんは数だけ数える。
LOG_LIMIT = 200

# ---- 値付けの表 -----------------------------------------------------------
#
# GUI から変えられるのは上の倍率だけで、この表はここを直す（TECH.md §3.8.2。
# 辞書の設定は宣言しない）。鍵は `attributes["item_detail"]`、つまりゲームが
# アイテムに書く細分の名前。語彙は本体の
# `Assets/images/item_candidates_dark/` のフォルダ名と、店の品揃え生成の
# スキーマから取ってある（GAME.md §2.13.2）。
#
#   STAT  … 能力値1点あたりの額。攻撃力・防御力・回復量を持つ品で使う
#   VALUE … 価値段階(value) 1 あたりの額。能力値を持たない品で使う
#
# 同じ名前が種別をまたぐことがある（`plant` は回復する薬草にも、ただの素材にも
# なる）ので、1行に両方の数を持たせて、能力値の有無で使い分ける。
RATES = {
    # 武器（攻撃力）
    "small_weapon":     (5.0, 61.0),
    "medium_weapon":    (5.0, 61.0),
    "large_weapon":     (5.2, 64.0),
    "long_weapon":      (5.2, 64.0),
    "throwable_weapon": (3.5, 43.0),
    # 防具・装身具（防御力）
    "headgear":         (4.0, 49.0),
    "body_armor":       (4.5, 55.0),
    "legwear":          (4.0, 49.0),
    "gauntlets":        (4.0, 49.0),
    "shield":           (4.5, 55.0),
    "accessory":        (5.5, 67.0),
    "clothing":         (3.5, 43.0),
    # 飲食・薬（回復量。回復しない同名の品は VALUE 側を使う）
    "food":             (5.0, 5.0),
    "drink":            (5.5, 6.0),
    "plant":            (6.5, 6.0),
    "mushroom":         (5.0, 3.5),
    "medicine":         (7.0, 7.0),
    "potion":           (7.5, 8.0),
    "scroll":           (8.0, 9.0),
    # 道具・書物
    "tool":             (5.0, 6.0),
    "document":         (5.0, 6.0),
    # 素材・財宝
    "creature":         (4.0, 5.0),
    "creature_part":    (4.0, 4.0),
    "ore":              (4.0, 5.0),
    "metal":            (4.0, 5.0),
    "gem":              (4.0, 15.0),
    "treasure":         (4.0, 23.0),
    "relic":            (4.0, 18.0),
    "scrap":            (4.0, 4.0),
    "magical_material": (4.0, 6.5),
    "liquid_material":  (4.0, 4.0),
    "other_material":   (4.0, 3.5),
}

# 表に無い細分（本体が語彙を増やしたとき）に使う。
DEFAULT_RATE = (4.0, 5.0)

# 伸び方。
# 装備は能力値に対して上に反り、回復量はほぼ比例する。
STAT_EXP_EQUIP = 1.4
STAT_EXP_HEAL = 1.0
VALUE_EXP = 1.3

# 種別ごとに「どの能力値を値段の軸にするか」。
# ここに無い種別は value 軸。
STAT_KEY_BY_TYPE = {
    "weapon": ("攻撃力", STAT_EXP_EQUIP),
    "wearable": ("防御力", STAT_EXP_EQUIP),
    "healing_item": ("回復", STAT_EXP_HEAL),
    "consumable": ("回復", STAT_EXP_HEAL),
}

# `attributes` のうち値段の鍵。
# ゲームは持ち主に応じてどちらか一方だけを書く。
BUY_KEY = prices.BUY
SELL_KEY = prices.SELL

SKILL_KEY = "スキル"

# 再注入しても1組だけ持つ（件数と、決済のずれの記録）。
STORE_ATTR = "_instantale_item_price_store"

def _num(value):
    """数として読めれば float、読めなければ None（文字列で入ることがある）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _round_nice(price):
    """桁に応じて丸める。店先に並ぶ額なので、大きい数まで1の位を出さない。"""
    price = int(round(price))
    if price >= 10000:
        return int(round(price / 100.0)) * 100
    if price >= 1000:
        return int(round(price / 10.0)) * 10
    return price


#: `Item` インスタンスからもセーブの辞書からも同じ形で読む（ローダの語彙）。
#: 決済の突き合わせ（`price_on_show` / `name_of`）がこれを通る。
read_item = prices.read_item


def apply(ctx):

    # 再注入しても1組だけ持つ器。
    # **鍵の型まで確かめる。**
    # `isinstance(store, dict)` だけを見ると、版の違う器をそのまま握る。
    # `settled` は旗（bool）から集合へ変えたので、旧版が走ったプロセスへ
    # 注入し直すと `label not in False` で TypeError になり、
    # 決済の検算だけが黙って死ぬ（`safe=True` なので画面には出ない）。
    # 手での注入し直しはこのプロジェクトの通常の操作なので、実際に踏む。
    blanks = {"logged": 0, "reconciled": 0, "skipped": 0, "settled": set()}
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = dict(blanks)
        setattr(sys, STORE_ATTR, store)
    else:
        # 足りない鍵と、型の変わった鍵だけを入れ替える（件数は残したい）。
        for name, blank in blanks.items():
            if name not in store or not isinstance(store[name], type(blank)):
                store[name] = set() if isinstance(blank, set) else blank

    write = ctx.logger(LOG_BASENAME, stamp=False)

    def note(text):
        """件数を数えつつ、最初の LOG_LIMIT 件だけ書き出す。"""
        store["logged"] += 1
        if store["logged"] <= LOG_LIMIT:
            write(text)
        elif store["logged"] == LOG_LIMIT + 1:
            write("... 以降は件数だけ数える（LOG_LIMIT={}）".format(LOG_LIMIT))


    # 表は apply() の中で組む。
    # 設定はモジュールのグローバルへ書き込まれるので、
    # トップレベルで組むと既定値のまま固まる（TECH.md §3.8.2）。
    rarity_mult = {
        "common": 1.0,
        "rare": float(RARITY_RARE),
        "magical": float(RARITY_MAGICAL),
        "epic": float(RARITY_EPIC),
        "legendary": float(RARITY_LEGENDARY),
        "mythic": float(RARITY_MYTHIC),
    }
    type_mult = {
        "weapon": float(MULT_WEAPON),
        "wearable": float(MULT_WEARABLE),
        "healing_item": float(MULT_HEALING_ITEM),
        "consumable": float(MULT_CONSUMABLE),
        "utility": float(MULT_UTILITY),
        "material": float(MULT_MATERIAL),
    }

    def name_of(item):
        field, attributes = read_item(item)
        return "{!r}/{}/{}".format(
            field("name"), attributes.get("item_detail"), field("rarity"))

    def base_price(item):
        """買価（店が売る値段）と、値段の軸を返す。組めなければ `(None, 理由)`。"""
        field, attributes = read_item(item)
        item_type = field("item_type")
        detail = attributes.get("item_detail")
        stat_rate, value_rate = RATES.get(detail, DEFAULT_RATE)

        stat_key, stat_exp = STAT_KEY_BY_TYPE.get(item_type, (None, 0.0))
        stat = _num(attributes.get(stat_key)) if stat_key else None
        if stat is not None and stat > 0:
            price = stat_rate * (stat ** stat_exp)
            axis = "{}={:g}".format(stat_key, stat)
        else:
            value = _num(field("value"))
            if value is None or value <= 0:
                return None, "value も能力値も読めない"
            price = value_rate * (value ** VALUE_EXP)
            axis = "value={:g}".format(value)

        price *= rarity_mult.get(field("rarity"), 1.0)
        price *= type_mult.get(item_type, 1.0)

        if attributes.get(SKILL_KEY) or field("skill"):
            price *= float(SKILL_MULT)

        upgrade = _num(field("upgrade_level")) or 0.0
        if upgrade > 0:
            price *= 1.0 + float(UPGRADE_STEP) * upgrade

        price *= float(PRICE_SCALE)
        price = min(max(price, float(MIN_PRICE)), float(MAX_PRICE))
        return _round_nice(price), axis

    def base_for(item):
        """買価と売価を組む。**組めなければ None**（ゲームの額が軸になる）。

        書くのはローダの関所（`prices.install`）で、ここは式だけを返す。
        値段を書く地点は10あり、書く時点が地点ごとに `orig` の前後で
        混ざっているので、書く側を1枚に寄せないと乗る側が必ず半分負ける
        （TECH.md §3.3.1。実測は VERIFICATION.md §3.19.1）。
        """
        price, axis = base_price(item)
        if price is None:
            store["skipped"] += 1
            note("skip {} ({})".format(name_of(item), axis))
            return None
        sell = _round_nice(min(max(price * float(SELL_RATE), float(MIN_PRICE)),
                               float(MAX_PRICE)))
        return {prices.BUY: price, prices.SELL: sell, "axis": axis}

    # ---- 値段が書かれる経路 -----------------------------------------------
    # 8箇所を包むのはローダの関所。ここは式を1枚置くだけで、
    # 地点ごとの `orig` の前後も、乗ってくる他の MOD の段も関所が引き受ける。
    #
    # `REPRICE_ON_SIGHT` は画面に出た品を付け直すかどうか。
    # 包む対象は関所が持つので、**頼む側の性質**として渡す。
    prices.install(ctx, write)
    prices.declare_base(os.path.basename(getattr(ctx, "mod_dir", "") or
                                         "129_balance_item_price"),
                        base_for, on_sight=bool(REPRICE_ON_SIGHT), write=note)
    # ---- 決済とのずれ -----------------------------------------------------

    # 所持金の読み方はローダの語彙（`309_` / `902_` と共有）。
    gold_of = ui.gold_of

    def price_on_show(item, key):
        """表示されていた値段。**`orig` を呼ぶ前に読むこと。**

        ゲームは値段を持ち主で決めており（買価は店主側、売価は持ち主側）、
        `buy_item` / `sell_item` はまさにその持ち主を移す処理なので、
        呼んだ後では鍵が入れ替わっている。読もうとした鍵が消えていて
        `None` になり、検算が黙って降りる。
        """
        _field, attributes = read_item(item)
        return _num(attributes.get(key))

    def settle(app, item, key, sign, label, before, expected=None):
        """`orig` の前後で所持金を測り、表示との差を直す。

        `sign` は所持金が動く向き（買うと -1、売ると +1）。
        **動いていなければ取引そのものが成立していない**（買えなかった等）ので何もしない。
        `before` は `orig` を呼ぶ前の所持金。取引ごとの局所に持つ
        （プロセスで1つの器に置くと、別スレッドの取引が重なったとき他方の「前」を読む）。
        `expected` は `orig` を呼ぶ前に読んだ表示値（`price_on_show`）。
        """
        if expected is None:
            expected = price_on_show(item, key)
        after = gold_of(app)
        if expected is None or before is None or after is None:
            return
        moved = (after - before) * sign
        if moved <= 0:
            return                              # 取引が成立していない
        gap = expected - moved
        if abs(gap) < 0.5:
            # 表示どおりに決済されている。**1回目だけ記録に残す**。
            # ずれた回しか書かないと「合っていた」と「一度も売買していない」が
            # ログの上で同じ（どちらも `reconcile` が0行）になり、
            # 決済がこちらの値段を読んでいるかを後から確かめられない。
            if label not in store["settled"]:
                # 売りと買いで別に数える。
                # 1つの旗を共有すると、片方が確かめられた時点でもう片方の
                # 検算行が永久に出なくなり、「合っていた」のか
                # 「一度も売買していない」のかが分からなくなる。
                store["settled"].add(label)
                write("settled {} {} shown={:g}（表示どおり。以後この行は出さない）"
                      .format(label, name_of(item), expected))
            return
        corrected = max(after + gap * sign, 0.0)
        # 型を保って書く（float の所持金は float のまま）。
        gold = ui.set_gold(app, corrected, on_error=lambda msg: ctx.log(
            "item price: could not correct gold: " + msg, level="WARN"))
        if gold is None:
            return
        store["reconciled"] += 1
        ctx.log("item price: {} settled at {:g} but showed {:g}; gold {:g} -> {}"
                .format(label, moved, expected, after, gold), level="WARN")
        write("reconcile {} {} shown={:g} moved={:g} gold {:g} -> {}".format(
            label, name_of(item), expected, moved, after, gold))

    if RECONCILE_GOLD:
        @ctx.wrap("__main__:InstantaleApp.buy_item", safe=True)
        def buy_item(orig, self, item_instance=None, *args, **kwargs):
            before = gold_of(self)
            shown = price_on_show(item_instance, BUY_KEY)
            result = orig(self, item_instance, *args, **kwargs)
            settle(self, item_instance, BUY_KEY, -1, "buy", before, shown)
            return result

        @ctx.wrap("__main__:InstantaleApp.sell_item", safe=True)
        def sell_item(orig, self, item_instance=None, *args, **kwargs):
            before = gold_of(self)
            shown = price_on_show(item_instance, SELL_KEY)
            result = orig(self, item_instance, *args, **kwargs)
            settle(self, item_instance, SELL_KEY, +1, "sell", before, shown)
            return result

    base_owner, layers = prices.item_price_sources()
    write("---- installed  scale={:g} sell_rate={:g} type={} rarity={} "
          "layers={} ----".format(
              float(PRICE_SCALE), float(SELL_RATE),
              {key: round(value, 3) for key, value in sorted(type_mult.items())},
              {key: round(value, 3) for key, value in sorted(rarity_mult.items())},
              layers or "-"))
    ctx.log("item price: installed (scale={:g}, sell_rate={:g}, on_sight={}, "
            "reconcile={})".format(float(PRICE_SCALE), float(SELL_RATE),
                                   bool(REPRICE_ON_SIGHT), bool(RECONCILE_GOLD)))
