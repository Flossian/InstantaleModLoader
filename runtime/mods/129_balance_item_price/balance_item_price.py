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
表示も売買もここを読むので、**既にある鍵の値だけを上書きする**。
鍵は新設しない:

  * `買価` と `売価` はゲームが持ち主に応じて付け替える。こちらが足すと
    「店でもないのに売価が付いた品」ができる
  * セーブに残る項目なので、並びも増減もゲームの形のままにしておく

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

## 値付けの後に他の MOD を通す

値段を書く地点が10箇所あり、**書く時点が地点ごとに違う**（画面の2箇所は
描く前なので `orig` の前、残りは `orig` の後）。
包みの勝敗は適用順ではなく `orig` の前に書くか後に書くかで決まるので、
値段へ何かを乗せたい MOD は、外側に居ても内側に居ても半分の地点で負ける。
そこで**書いた直後に呼ぶ口**を1つ持たせてある（`POST_ATTR`）。
こちらがどこで書いても、同じ呼吸で後処理が乗る。
実例は `405_regional_economy` の地域倍率。

## 触らないもの

`get_item_base_price` は包まない。
あれは装備の強化費用（`calculate_modification(item_type, item_price)`）や、
価格から段階を逆算する `get_equipment_level_from_price` の入口でもある。
値段だけ膨らませると逆算側が定義域から外れて
`KeyError` を出しうる（`get_npc_employ_price` の前例。VERIFICATION_LOG.md §2.2）。
売買の値段はこの mod、内部の段階計算はゲーム自身、と分ける。
"""

import sys

from instantale_modloader import ui

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
BUY_KEY = "買価"
SELL_KEY = "売価"
PRICE_KEYS = (BUY_KEY, SELL_KEY)

SKILL_KEY = "スキル"

# 再注入しても1組だけ持つ（件数と、決済のずれの記録）。
STORE_ATTR = "_instantale_item_price_store"

# ---- 値付けの後に割り込む口（他の MOD 向け）-------------------------------
#
# `sys` に置いた**素のリスト**で、中身は `(名乗り, 関数)` の組。
# この MOD が値段を書いた**直後**に、登録された順で呼ぶ。
#
#     fn(item, attributes, why)      # 戻り値は見ない。例外は握って記録する
#
# 使う側（`405_regional_economy` がこの形）:
#
#     hooks = getattr(sys, "_instantale_item_price_post", None)
#     if not isinstance(hooks, list):
#         hooks = []
#         setattr(sys, "_instantale_item_price_post", hooks)
#     hooks[:] = [e for e in hooks if e[0] != 私の名乗り]   # 再注入で重ねない
#     hooks.append((私の名乗り, fn))
#
# **なぜ包み直しではなくこの形なのか。** この MOD は10箇所で値段を書くが、
# 書く時点が地点ごとに違う ― 画面の2箇所（`toggle_twin_inventory_window` /
# `ItemDetailBox.update_content`）は描く前なので `orig` の**前**、残りは
# `orig` の**後**。包みの勝敗は適用順ではなく「`orig` の前に書くか後に書くか」で
# 決まるので、相手がどちらの層に居ても必ず半分の地点で負ける
# （実測: 地域倍率が `shop_owner` / `normalize_shop_inventory_prices` で
# 9回とも消えていた。VERIFICATION.md §3.19）。
# ここを通せば、こちらがどこで書いても同じ呼吸で後処理が乗る。
POST_ATTR = "_instantale_item_price_post"


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


def read_item(item):
    """`Item` インスタンスからもセーブの辞書からも、同じ形で読む。

    セーブを直に読む場面（検査・別 mod）でも同じ関数を通せるようにしてある。
    """
    if isinstance(item, dict):
        attributes = item.get("attributes")

        def field(name):
            return item.get(name)
    else:
        attributes = getattr(item, "attributes", None)

        def field(name):
            return getattr(item, name, None)

    if not isinstance(attributes, dict):
        attributes = {}
    return field, attributes


def apply(ctx):

    # 再注入しても1組だけ持つ器。
    # **鍵の型まで確かめる。**
    # `isinstance(store, dict)` だけを見ると、版の違う器をそのまま握る。
    # `settled` は旗（bool）から集合へ変えたので、旧版が走ったプロセスへ
    # 注入し直すと `label not in False` で TypeError になり、
    # 決済の検算だけが黙って死ぬ（`safe=True` なので画面には出ない）。
    # 手での注入し直しはこのプロジェクトの通常の操作なので、実際に踏む。
    blanks = {"logged": 0, "repriced": 0, "reconciled": 0, "skipped": 0,
              "gold_before": None, "settled": set()}
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = dict(blanks)
        setattr(sys, STORE_ATTR, store)
    else:
        # 足りない鍵と、型の変わった鍵だけを入れ替える（件数は残したい）。
        # `gold_before` は None で始まって数が入るので、型では見ない。
        for name, blank in blanks.items():
            if name not in store or (blank is not None
                                     and not isinstance(store[name], type(blank))):
                store[name] = set() if isinstance(blank, set) else blank

    write = ctx.logger(LOG_BASENAME, stamp=False)

    def note(text):
        """件数を数えつつ、最初の LOG_LIMIT 件だけ書き出す。"""
        store["logged"] += 1
        if store["logged"] <= LOG_LIMIT:
            write(text)
        elif store["logged"] == LOG_LIMIT + 1:
            write("... 以降は件数だけ数える（LOG_LIMIT={}）".format(LOG_LIMIT))

    # 値段の後に割り込む口（宣言は POST_ATTR の説明を参照）。
    # **リストは相手が先に作っていることがある**（適用順はどちらが先とも限らない）
    # ので、無ければ作る・在ればそれを使う、で揃える。
    post_hooks = getattr(sys, POST_ATTR, None)
    if not isinstance(post_hooks, list):
        post_hooks = []
        setattr(sys, POST_ATTR, post_hooks)

    def run_post(item, attributes, why):
        """値段を書いた直後に、登録された後処理を順に通す。

        壊れた後処理でこちらの値付けまで巻き添えにしない。
        1つが投げても残りは通し、記録だけ残す（`safe=True` と同じ考え方）。
        """
        if not post_hooks:
            return
        for entry in list(post_hooks):
            try:
                entry[1](item, attributes, why)
            except Exception:
                ctx.log_exc("item price: post hook {!r} failed".format(
                    entry[0] if isinstance(entry, tuple) and entry else entry))

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

    def reprice(item, why):
        """既にある値段の鍵だけを付け直す。鍵は新設しない。

        戻り値は「実際に書き換えたか」。
        同じ額なら書かない。
        売買画面は同じ品を何度も通るので、変わったときだけ数えたい。
        """
        _field, attributes = read_item(item)
        keys = [key for key in PRICE_KEYS if key in attributes]
        if not keys:
            return False

        price, axis = base_price(item)
        if price is None:
            store["skipped"] += 1
            note("skip {} ({})".format(name_of(item), axis))
            # 値段を組めなかった品でも後処理は通す。
            # 素の値段のまま残る品にも、相手の倍率は乗ってよい。
            run_post(item, attributes, why)
            return False

        changed = False
        for key in keys:
            if key == BUY_KEY:
                new = price
            else:
                new = _round_nice(min(max(price * float(SELL_RATE),
                                          float(MIN_PRICE)), float(MAX_PRICE)))
            old = attributes.get(key)
            if _num(old) == float(new):
                continue
            attributes[key] = new
            changed = True
            note("{} {} {}: {} -> {}  [{}]".format(
                why, key, name_of(item), old, new, axis))
        if changed:
            store["repriced"] += 1
        # **書かなかったときも通す。**
        # 相手が「まだ乗せていない倍率」を持っていることがあり
        # （その土地のプロフィールが後から出来る）、
        # こちらが書いた回だけ呼ぶと、値段が同じ品にいつまでも乗らない。
        run_post(item, attributes, why)
        return changed

    def reprice_inventory(obtainer, why):
        """持ち物ひとまとまりを付け直す。持ち物が引けなければ何もしない。"""
        inventory = getattr(obtainer, "inventory", None)
        if isinstance(inventory, dict):
            items = list(inventory.values())
        elif isinstance(inventory, (list, tuple)):
            items = list(inventory)
        else:
            return 0
        return sum(1 for item in items if reprice(item, why))

    # ---- 値段が書かれる経路 -----------------------------------------------
    # どの経路で書くかは版によって違いうるので、書きうる場所を全部通す。
    # `reprice` は同じ額なら何もしないので、重ねて通っても害が無い。

    @ctx.wrap("__main__:InstantaleApp.set_shop_price_for_owner", safe=True)
    def price_for_owner(orig, self, item_instance=None, *args, **kwargs):
        result = orig(self, item_instance, *args, **kwargs)
        reprice(item_instance, "shop_owner")
        return result

    @ctx.wrap("__main__:InstantaleApp.set_shop_price_for_player", safe=True)
    def price_for_player(orig, self, item_instance=None, *args, **kwargs):
        result = orig(self, item_instance, *args, **kwargs)
        reprice(item_instance, "shop_player")
        return result

    @ctx.wrap("__main__:InstantaleApp.normalize_shop_inventory_prices", safe=True)
    def normalize_prices(orig, self, shop_obtainer=None, player_obtainer=None,
                         *args, **kwargs):
        result = orig(self, shop_obtainer, player_obtainer, *args, **kwargs)
        reprice_inventory(shop_obtainer, "normalize/shop")
        reprice_inventory(player_obtainer, "normalize/player")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_item_data", safe=True)
    def generated_from_data(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        reprice(result, "generated")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_dict", safe=True)
    def generated_from_dict(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        reprice(result, "generated/dict")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_ready_made_data",
              safe=True, required=False)
    def generated_ready_made(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        reprice(result, "generated/ready_made")
        return result

    # ---- 既にセーブに在る品 -----------------------------------------------
    # 上の経路は「これから作られる品」と「ゲームが値付けし直す品」しか通らない。
    # 古いセーブの持ち物は元の値段のまま残るので、画面に出た時点で付け直す。

    if REPRICE_ON_SIGHT:
        @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window", safe=True)
        def twin_window(orig, self, left_inventory_obtainer=None,
                        right_inventory_obtainer=None, left_label_text=None,
                        situation=None, *args, **kwargs):
            # 描く前に直す（描画は `attributes` をそのまま読む）。
            reprice_inventory(left_inventory_obtainer, "window/left")
            reprice_inventory(right_inventory_obtainer, "window/right")
            return orig(self, left_inventory_obtainer, right_inventory_obtainer,
                        left_label_text, situation, *args, **kwargs)

        @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content", safe=True)
        def detail_box(orig, self, item=None, *args, **kwargs):
            reprice(getattr(item, "item_instance", None) or item, "detail")
            return orig(self, item, *args, **kwargs)

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

    def settle(app, item, key, sign, label, expected=None):
        """`orig` の前後で所持金を測り、表示との差を直す。

        `sign` は所持金が動く向き（買うと -1、売ると +1）。
        **動いていなければ取引そのものが成立していない**（買えなかった等）ので何もしない。
        `expected` は `orig` を呼ぶ前に読んだ表示値（`price_on_show`）。
        """
        if expected is None:
            expected = price_on_show(item, key)
        before = store["gold_before"]
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
        player = getattr(app, "player", None)
        try:
            player.gold = (corrected if isinstance(getattr(player, "gold", 0), float)
                           else int(round(corrected)))
        except Exception:
            ctx.log_exc("item price: could not correct gold")
            return
        store["reconciled"] += 1
        ctx.log("item price: {} settled at {:g} but showed {:g}; gold {:g} -> {}"
                .format(label, moved, expected, after, player.gold), level="WARN")
        write("reconcile {} {} shown={:g} moved={:g} gold {:g} -> {}".format(
            label, name_of(item), expected, moved, after, player.gold))

    if RECONCILE_GOLD:
        @ctx.wrap("__main__:InstantaleApp.buy_item", safe=True)
        def buy_item(orig, self, item_instance=None, *args, **kwargs):
            store["gold_before"] = gold_of(self)
            shown = price_on_show(item_instance, BUY_KEY)
            result = orig(self, item_instance, *args, **kwargs)
            settle(self, item_instance, BUY_KEY, -1, "buy", shown)
            return result

        @ctx.wrap("__main__:InstantaleApp.sell_item", safe=True)
        def sell_item(orig, self, item_instance=None, *args, **kwargs):
            store["gold_before"] = gold_of(self)
            shown = price_on_show(item_instance, SELL_KEY)
            result = orig(self, item_instance, *args, **kwargs)
            settle(self, item_instance, SELL_KEY, +1, "sell", shown)
            return result

    write("---- installed  scale={:g} sell_rate={:g} type={} rarity={} post={} ----"
          .format(float(PRICE_SCALE), float(SELL_RATE),
                  {key: round(value, 3) for key, value in sorted(type_mult.items())},
                  {key: round(value, 3) for key, value in sorted(rarity_mult.items())},
                  [entry[0] for entry in post_hooks
                   if isinstance(entry, tuple) and entry] or "-"))
    ctx.log("item price: installed (scale={:g}, sell_rate={:g}, on_sight={}, "
            "reconcile={})".format(float(PRICE_SCALE), float(SELL_RATE),
                                   bool(REPRICE_ON_SIGHT), bool(RECONCILE_GOLD)))
