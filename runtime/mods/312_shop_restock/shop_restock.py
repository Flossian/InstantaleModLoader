# -*- coding: utf-8 -*-
"""店の品揃えを、ゲーム内の経過日数で入れ替える。

## 何が困るのか

売買画面に並ぶのは、その施設の主（`Facility.owner`）の持ち物そのもの（`InstantaleApp.toggle_twin_inventory_window` の
obtainer が主の Character）。
ゲームは初めてその店を開いたときに品物を作って主へ持たせるが、
入れ替える仕組みが無い。
プレイヤーが売った品はそのまま主の持ち物に積まれるので、
同じ店で何度か売っているとグリッド（4x6 = 24マス）が埋まり切り、
それ以上売れなくなる。
品揃えも初回のまま変わらない。

## 直し方。「消して、ゲーム自身に作らせる」

品物をこちらで作らない。
アイテムの値付け（`set_shop_price_for_owner` /
`normalize_shop_inventory_prices`）も、
世界データからの選び方（`set_item_from_world_data(shop_owner_instance, next_tier)`）も、
難度に応じた段（tier）の決め方も、全部ゲームが持っている。
こちらが写し取って再現すれば、その瞬間からゲームの更新に付いていけなくなる。

やることは1つだけ:

    売買を始める前に、入れ替え日が来ていれば主の持ち物を空にする

初めてその店を開いたときの状態（＝空）に戻すだけなので、
その後はゲーム自身がいつもの経路で品揃えを作る。
プレイヤーが売った品もここで流れる。
店主が仕入れを入れ替えた、という筋になる。

## 空にしたまま補充されなかったときの逃げ道

「空なら作る」がゲームの実際の作りかどうかは、
こちらからは確かめられない（本体は凍結されていてソースが読めない）。
そこで**空にした後、必ず結果を見る**:

```
入れ替え日が来た
  └ 主の持ち物を控えてから空にする
       └ ゲームの売買開始処理（orig）
            └ VERIFY_DELAY 秒後に見る
                 ├ 品物が入っている  → 成功。その日を控えて終わり
                 ├ 空のまま          → 段(tier)を見たことがあれば
                 │                     set_item_from_world_data を自分で呼ぶ
                 └ それでも空        → 控えを戻し、以後この版では空にしない
```

## 品揃え一式を新しく作る（`NEW_STOCK`）

雛形から作り直す限り、現物は新品でも品名は毎回同じになる。
新しい品名はローダの LLM 経路（`llm.ask` + `create_structure`。`405_` と同じ口）で作る。
ゲーム自身の生成関数（`shop_item_generator_ordinary` など）は使わない。
引数の形が読めず、店を開いても呼ばれないので、それを待つと入れ替えが止まる
（版3で一度そうなった。2026-09-10）。

頼み文は場所（世界名・土地・施設の名前と説明）と、雛形の各品の `value`
（世界を作ったときの価値段階）、`item_type` / `item_sub_type` の語彙、前の品名（重複禁止）。
返った1行ずつを `InstantaleApp.generate_item_from_item_data(item_name, description,
item_type, item_sub_type, value, item_appearance, rarity, obtainer)` に渡す。
この入口は画像の選択（`128_`）・値付け（`129_`）・効果（`134_`）を普段どおり通る。

呼ぶのは `execute` の中（ゲームが自分の LLM を呼ぶのと同じ別スレッド）で、空にした直後。
その後ゲームが雛形から作り直すぶん（同じ品名）は `held` に控えて画面を開く側で外す。
LLM が返らない・読めないときは `WARN new stock:` を書いて、雛形から作り直す側に落ちる。

## 作り直されない品はこちらから作らせる

ゲームが埋め直すのは雛形のうち装備以外だけで、
`weapon` / `wearable` は作らない（GAME.md §2.13.1.3）。
空にしたままにすると、入れ替えのたびに店から装備が消え、
通うほど回復アイテムだけの店になる
（実測: 雛形8件の店が入れ替え後4件、雛形10件の店が3件。2026-09-10）。

そこで補充の結果を見たあと、**雛形にあるのに戻ってこなかった品**を
ゲームの生成で作り直す（`InstantaleApp.generate_item_from_dict(item_dict, item_id, obtainer)`。
雛形の1件はセーブのアイテムと同じ形の辞書なので、写しをそのまま渡せる）。
id は採番台帳から採る（`ids.claim`）。
作るのはゲームなので、値段も効果も普段どおり付く（`129_` / `134_` がこの入口を包んでいる）。
埋めるのは雛形にある品だけなので、プレイヤーが売った品は今までどおり流れる。
作れなかったぶんだけ控えの現物を戻す（品揃えが減るよりまし）。

最悪でも「1回だけ空の店を見て、次の来店から元の品揃えが戻る」で止まる。
店が永久に空になることはない。
段(tier)はゲームが `set_item_from_world_data` に渡す値をそのまま覚えて使う。
値の意味は解釈しない（`307_` が移動確認画面の `args` をそのまま写すのと同じ形）。

待ってから見るのは、生成が Clock コールバックへ回される版でも取りこぼさないため。
`orig` の直後に見ると「まだ作っていないだけ」を「補充されなかった」と読み違える。

## 買った品がその場で作り直されるのを止める（`KEEP_SOLD_OUT`）

**ゲームは店を開くたびに、売れた品を作り直して棚へ戻す**
（2026-09-04 に `227_probe_shop_stock` で実測。GAME.md §2.13.1.3）。

```
15:58:18 買 ハルマンの予備のランプ (utility/tool)
15:58:27 開き直し → 品の誕生: id=59 'ハルマンの予備のランプ' 主=ハルマン(118)
         呼び出し元: ShoppingStartManagerRemake.shopping_start_method_1
                     (instantale.py:3159) <- .execute (instantale.py:3281)
```

作っているのはゲーム自身で、採番もゲームの台帳（`index['item']`）。
元にするのは施設の品揃えの雛形（`Facility.config['goods']`）で、
雛形は買っても減らず `stock_update_date` も動かない。
**装備（`weapon` / `wearable`）は作り直されず、それ以外（`healing_item` / `utility`）は
雛形1件につき現物1つまで戻る**（実測7品）。
つまり回復アイテムだけが無限に買える。
`134_balance_item_effects` で回復量を上げていると、そのまま不死身になる。

止め方は、**その来店で増えたぶんのうち雛形にある品だけを外す**:

    prepare（入れ替え日なら空にする）
      └ 開く前の主の持ち物の鍵と、雛形の品名を控える
           └ ゲームの売買開始処理（orig。ここで作り直しが起きる）
                └ 控えに無い鍵のうち、**雛形にある品名のものだけ**外す

名前で見分けるのは、ゲームが棚へ足すものが2種類あるため。
雛形の作り直し（買った品と同じもの）と、
売買のたびに LLM が作る**新しい品**（`shop_additional_item_generator_ordinary`）。
鍵が増えたことだけを見て外すと後者まで消え、品揃えが雛形のまま永久に固定される。
雛形が読めなかったときは増えたぶんを全部外す（売り切れを守る側に倒す）。

雛形（`config['goods']`）には触らない。
あれはゲームが世界を作ったときの骨格で、書き換えると
この MOD を外したときに戻せない（TECH.md §6.4 / world_data の扱い）。

外すのは**売買画面を開く側**（`toggle_twin_inventory_window` の手前。メインスレッド）。
`execute` は別スレッドで走り、画面を開く処理を Clock でメインスレッドへ回してから戻る。
`execute` の戻り際に外すと、メインスレッドが `normalize_shop_inventory_prices` で
主の持ち物の辞書を回している最中に別スレッドから鍵を消すことになり、
`RuntimeError: dictionary changed size during iteration` でゲームごと落ちる
（2026-09-07 に実機。VERIFICATION.md §3.52）。
画面を開く側の手前なら同じスレッドで、しかも辞書を回す前なので、
作り直された品は画面に一度も出ない。

**この MOD 自身の入れ替えは止めない。** 空にした回（`pending` が立っている回）は
素通しする。止めると入れ替えそのものが働かなくなる。
初めて開いた店（控えがまだ無くて持ち物も空）も素通しする。
止めると品揃えが1つも作られない。
一度でも開いた店を買い占めた場合は空のままになり、
戻るのは入れ替えの日が来てからになる。

## 日数と控え

ゲーム内の日付は `app.world.days_elapsed`（世界に1つ。`elapse_days` が進める。
実セーブでは `world_data.days_elapsed`）。
店ごとの最後の入れ替え日は `state/shop_restock/<世界名>.json` に置く:

    {"<主の id>": {"day": 3651, "facility": "30", "count": 8, "tier": 2}}

**セーブには独自の項目を足さない**（TECH.md §6）。
ゲームが自分で持っているのは「主の持ち物」だけで、そこはゲームの形のまま入れ替わる。
この mod を外しても、残るのは普通の品揃えを持った店だけになる。

古いセーブをロードして日付が巻き戻ったときは、
控えをその日に付け直して入れ替えない（次の来店から数え直す）。
控えより先に進んだ日数だけで判断するので、
複数の世界・複数のセーブを行き来しても混ざらない（世界ごとにファイルを分けてある）。
"""

import sys
import typing

from instantale_modloader import frames, ids, llm, ui
from instantale_modloader.state import WorldStore, world_key

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
RESTOCK_DAYS = 30            # 何日経ったら品揃えを入れ替えるか
RESTOCK_ON_FIRST_SHOP = False  # 初めて開いた店をその場で入れ替えるか
KEEP_SOLD_OUT = True         # 買った品をゲームに作り直させないか
NEW_STOCK = True             # 入れ替えのとき品揃え一式を LLM に新しく作らせるか

LOG_BASENAME = "shop_restock.log"

# 新しい品揃えは、ローダの LLM 経路（`llm.ask`。`405_` と同じ口）で作る。
# 品1つずつは、ゲームの `generate_item_from_item_data(...)` に渡して棚へ入れる
# （画像の選択・値付け・効果はその入口で普段どおり付く）。
MANAGER_NAME = "mod_shop_restock"
NEW_STOCK_TIMEOUT = 120      # LLM を待つ秒数（`405_` と同じ。返らなければ雛形から作り直す）
NEW_STOCK_MIN = 4            # 1回の入れ替えで作る品の数の下限・上限（雛形の件数に合わせる）
NEW_STOCK_MAX = 12

# `item_type` / `item_sub_type` の語彙。実セーブ6世界の品と `129_` の分類から。
# LLM にはこの組から選ばせ、外れたら先頭の細分に寄せる。
ITEM_TYPES = {
    "weapon": ("small_weapon", "medium_weapon", "long_weapon", "large_weapon"),
    "wearable": ("body_armor", "accessory", "clothing"),
    "healing_item": ("food", "drink", "potion", "medicine", "plant"),
    "utility": ("tool", "document"),
    "material": ("ore", "gem", "relic", "magical_material", "creature_part"),
}
RARITIES = ("common", "rare", "magical", "epic", "legendary", "mythic")

# 世界ごとの控えの置き場。
# `state/` の下（消すと入れ替えの間隔が巻き戻る）。
STATE_DIRNAME = "shop_restock"

# 空にした後、補充されたかを見るまでの待ち（秒）。
# 生成が次のフレームへ回される版でも取りこぼさないだけの間を取る。
VERIFY_DELAY = 1.0

# 控えの鍵の並び。
# 読む側（人間・別 mod）のために固定する。
RECORD_KEYS = ("day", "facility", "count", "tier")


def ordered_bucket(bucket):
    """控えを書く前に並びを固定する（`RECORD_KEYS` の順。知らない鍵は落とす）。

    順が動くと `state/` の差分を読んだときに全行が動いて見える。
    """
    out = {}
    for owner_id, record in bucket.items():
        if isinstance(record, dict):
            out[owner_id] = {k: record[k] for k in RECORD_KEYS if k in record}
    return out

# 再注入しても1組だけ持つ（世代をまたいで覚えていたい: 段(tier)と、
# 「この版は空にしても補充しない」の判定）。
STORE_ATTR = "_instantale_shop_restock_store"


def apply(ctx):
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "tiers": {},        # 主の id -> ゲームが渡した段(tier)
            # 世界ごとの控え。出し入れはローダの語彙（`state.WorldStore`）で、
            # 世代をまたいで持つのでプロセス側に置く（`rebind` で繋ぎ替える）。
            "worlds": WorldStore(ctx, STATE_DIRNAME, order=ordered_bucket),
            "pending": None,    # 空にして結果待ちの1件
            "held": None,       # 開く前の鍵の控え。画面を開く側が受け取って消す
            "auto_refill": None,  # None=未確認 / True=補充される / False=されない
            # 今開いている店の控えがまだ無いか（＝初めて開く店か）。
            # `prepare` が毎回書き直す。空の店の品揃えを作らせるかの判定に使う。
            "fresh": True,
        }
        setattr(sys, STORE_ATTR, store)
    # 前の世代が作った控えには無い鍵を足す（版を上げた直後の1回だけ効く）。
    store.setdefault("fresh", True)
    store.setdefault("held", None)

    write = ctx.logger(LOG_BASENAME, stamp=False)
    worlds = store["worlds"].rebind(ctx, write)

    # ボタンは出さないので `mark` は要らない。
    # `schedule` と例外の握りだけ借りる。
    screen = ui.Screen(ctx, write, tag="shop restock")

    # ------------------------------------------------------------ 世界と控え

    def bucket_of(app):
        return worlds.of(app)

    def save_bucket(key, bucket):
        """1世界分を書き出す。並びは `ordered_bucket`、書き方は `ctx.write_json()`。"""
        return worlds.save(key, bucket)

    # ------------------------------------------------------------ ゲームを読む
    def app_of(manager):
        app = getattr(manager, "app", None)
        return app if app is not None else ui.find_app()

    def facility_of(app):
        """プレイヤーが今いる施設。ロード直後は id の文字列なので引き当て直す。"""
        location = getattr(getattr(app, "player", None), "location", None)
        if location is None:
            return None
        if not isinstance(location, str):
            return location
        # `find_facility` は `(施設, ノード)` を返す。
        # 施設だけを取る。
        return ui.find_facility(ui.current_area(app), location)[0]

    def inventory_of(character):
        """持ち物の実体（`{item_id: アイテム}`）。毎回引き直すこと。

        ゲームが新しい辞書を割り当て直す作りだった場合、
        掴んだままの参照は古い辞書を指す（空にしたはずが元の辞書、
        という壊れ方になる）。
        """
        if character is None:
            return None
        inventory = getattr(character, "inventory", None)
        if isinstance(inventory, dict):
            return inventory
        inner = getattr(inventory, "inventory", None)
        if isinstance(inner, dict):
            return inner
        return None

    def shop_owner(app):
        """今の施設の主を (id, Character) で返す。引けなければ (None, None)。"""
        facility = facility_of(app)
        if facility is None:
            return None, None
        owner = getattr(facility, "owner", None)
        if owner is None:
            return None, None
        owner_id = str(owner)
        return owner_id, ui.character_of(app, owner_id)

    def label(app, owner_id, facility):
        return "{}({}) @ {}".format(
            ui.character_name(app, owner_id), owner_id,
            getattr(facility, "id", "?"))

    # ------------------------------------------------------------ 入れ替え本体
    def prepare(manager):
        """入れ替え日が来ていれば主の持ち物を空にする。控えは `pending` に置く。

        ついでに `fresh`（この店の控えがまだ無い＝初めて開く店か）も書き直す。
        **控えを書く前の状態で決めること。** ここで基準を書いてから見ると、
        初めて開いた店も「控えのある店」になり、`KEEP_SOLD_OUT` が
        初回の品揃えまで外してしまう（店が永久に空になる）。
        引けなかったときは安全側の True（ゲームに作らせる）。
        """
        store["pending"] = None
        store["fresh"] = True
        app = app_of(manager)
        if app is None:
            return
        if RESTOCK_DAYS <= 0:
            return

        facility = facility_of(app)
        owner_id, owner = shop_owner(app)
        if owner is None:
            write("skip: cannot resolve the shop owner "
                  "(facility={})".format(getattr(facility, "id", None)))
            return

        day = ui.game_day(app)
        if day is None:
            write("skip: world.days_elapsed is unreadable")
            return

        inventory = inventory_of(owner)
        if inventory is None:
            write("skip: {} has no readable inventory".format(
                label(app, owner_id, facility)))
            return

        key, bucket = bucket_of(app)
        record = bucket.get(owner_id)
        record = record if isinstance(record, dict) else None
        last = record.get("day") if record else None
        last = last if isinstance(last, int) and not isinstance(last, bool) else None
        store["fresh"] = record is None

        if last is None:
            # 初めて開いた店。
            # ここを基準の日にする。
            # 素の品揃えを1度も見ずに入れ替えるのは、
            # 店を1軒も見ていない人から初回の面白みを奪う。
            if not RESTOCK_ON_FIRST_SHOP:
                bucket[owner_id] = ordered_record(day, facility, len(inventory), None)
                save_bucket(key, bucket)
                write("first visit: {} day={} items={} (baseline only)".format(
                    label(app, owner_id, facility), day, len(inventory)))
                return
        elif day < last:
            # 古いセーブをロードした。
            # 控えを今日に付け直して数え直す。
            bucket[owner_id] = ordered_record(day, facility, len(inventory),
                                              record.get("tier") if record else None)
            save_bucket(key, bucket)
            write("rewound: {} day={} < last={}; baseline reset".format(
                label(app, owner_id, facility), day, last))
            return
        elif day - last < RESTOCK_DAYS:
            write("not due: {} day={} last={} (needs {})".format(
                label(app, owner_id, facility), day, last, RESTOCK_DAYS))
            return

        if store["auto_refill"] is False and owner_id not in store["tiers"]:
            # 空にしても補充されない版だと分かっていて、自分で呼ぶ手段も無い。
            write("held back: {} would be left empty on this build".format(
                label(app, owner_id, facility)))
            return

        snapshot = dict(inventory)
        try:
            inventory.clear()
        except Exception:
            ctx.log_exc("shop restock: cannot clear the inventory")
            return

        store["pending"] = {
            "manager": manager, "app": app, "owner_id": owner_id, "owner": owner,
            "facility": facility, "day": day, "last": last, "snapshot": snapshot,
            "world": key,
        }
        write("cleared: {} day={} last={} items={}".format(
            label(app, owner_id, facility), day, last, len(snapshot)))

    def ordered_record(day, facility, count, tier):
        return {"day": day, "facility": str(getattr(facility, "id", "")),
                "count": count, "tier": tier}

    # ------------------------------------------------------------ 作り直しを止める
    def item_name(item):
        """品名。実機は `Item`、セーブから直に読んだものは辞書。"""
        name = (item.get("name") if isinstance(item, dict)
                else getattr(item, "name", None))
        return name if isinstance(name, str) and name else "?"

    def held_keys(manager, always=False):
        """開く前の主の持ち物の鍵を控える。素通しするときは None。

        `always` は新しい品揃えを作った直後用。`KEEP_SOLD_OUT` が切れていても
        雛形からの作り直し（同じ品名）は外さないと、新旧が混ざる。

        素通しするのは1つだけ:
        **初めて開く店で、持ち物がまだ空のとき**（品揃えを作らせる回）。
        既に品を持っている店は、控えがあってもなくても止める側に入れる
        （古いセーブから始めた場合も、増えたぶんだけを外せる）。
        """
        if not KEEP_SOLD_OUT and not always:
            return None
        app = app_of(manager)
        if app is None:
            return None
        owner_id, owner = shop_owner(app)
        inventory = inventory_of(owner)
        if inventory is None:
            return None
        if not inventory and store["fresh"]:
            return None
        facility = facility_of(app)
        return {"app": app, "owner_id": owner_id, "owner": owner,
                "facility": facility, "keys": set(inventory),
                "goods": goods_names(facility)}

    def goods_names(facility):
        """その施設の品揃えの雛形にある品名。読めなければ None。

        作り直しかどうかの目印はこれ1つ。
        ゲームは開くたびに**雛形の品**を作り直すが、それとは別に、売買のたびに
        新しい品を LLM で作って棚へ足す（`shop_additional_item_generator_ordinary`。
        GAME.md §2.13.1.3）。
        名前で見分けないと、その新しい品まで外すことになり、
        品揃えが雛形のまま永久に固定される。
        """
        entries = goods_entries(facility)
        if entries is None:
            return None
        return {entry.get("name") for entry in entries}

    def goods_entries(facility):
        """雛形の1件ずつ。読めなければ None。名前の無い行は数えない。"""
        config = frames.attr(facility, "config", None)
        goods = config.get("goods") if isinstance(config, dict) else None
        if not isinstance(goods, list):
            return None
        return [entry for entry in goods
                if isinstance(entry, dict) and entry.get("name")]

    def drop_refilled(held):
        """この来店で増えたぶんのうち、**雛形にある品だけ**を外す。

        持ち物は**引き直す**（掴んだ辞書ではなく今の実体を見る）。
        雛形が読めなかったときは増えたぶんを全部外す（売り切れを守る側に倒す）。
        """
        if not held:
            return
        inventory = inventory_of(held["owner"])
        if not isinstance(inventory, dict):
            return
        added = [key for key in list(inventory) if key not in held["keys"]]
        if not added:
            return
        goods = held.get("goods")
        dropped, kept = [], []
        for key in added:
            item = inventory.get(key)
            name = item_name(item)
            if goods is not None and name not in goods:
                # 雛形に無い＝買った品の作り直しではない（ゲームが新しく作った品）。
                kept.append("{}={}".format(key, name))
                continue
            try:
                del inventory[key]
                dropped.append("{}={}".format(key, name))
            except Exception:
                ctx.log_exc("shop restock: cannot drop a refilled item")
        who = label(held["app"], held["owner_id"], held["facility"])
        if dropped:
            write("kept sold out: {} dropped {} refilled item(s): {}".format(
                who, len(dropped), ", ".join(dropped)))
        if kept:
            write("new stock kept: {} left {} new item(s): {}".format(
                who, len(kept), ", ".join(kept)))

    def build_one(app, owner, entry):
        """雛形1件から、ゲームの生成で品を1つ作る。作れたら鍵、駄目なら None。

        使うのは `InstantaleApp.generate_item_from_dict(item_dict, item_id, obtainer)`。
        雛形の1件はセーブのアイテムと同じ形の辞書なので、そのまま渡せる
        （値段も効果も `129_` / `134_` がこの入口を包んでいるので普段どおり付く）。
        id は採番台帳から採る（`ids.claim`。自分で決めるとゲームの採番と衝突する）。
        雛形は写しを渡す。ゲームに書き換えられると世界の骨格が変わる。
        """
        maker = frames.attr(app, "generate_item_from_dict")
        if maker is frames.MISSING:
            return None
        inventory = inventory_of(owner)
        if not isinstance(inventory, dict):
            return None
        before = set(inventory)
        item_key = ids.claim(app, "item")
        try:
            item = maker(dict(entry), item_key, owner)
        except Exception:
            ctx.log_exc("shop restock: generate_item_from_dict failed")
            return None
        # 持ち物は引き直す（ゲームが辞書を割り当て直していることがある）。
        inventory = inventory_of(owner)
        if not isinstance(inventory, dict):
            return None
        added = set(inventory) - before
        if added:
            return sorted(added)[0]
        if item is None:
            return None
        # ゲームが棚へ入れない作りなら、採った鍵で自分で入れる。
        try:
            inventory[item_key] = item
        except Exception:
            ctx.log_exc("shop restock: cannot place the generated item")
            return None
        return item_key

    def refill_missing(pending, inventory):
        """雛形にあるのに作り直されなかった品を、ゲームの生成で埋める。

        ゲームは空にした店を埋め直すとき、**装備（`weapon` / `wearable`）を作らない**
        （GAME.md §2.13.1.3）。放っておくと入れ替えのたびに店から装備が消え、
        通うほど回復アイテムだけの店になる（実測: 雛形8件の店が入れ替え後4件、
        雛形10件の店が3件。2026-09-10）。

        埋めるのは**雛形にある品**だけ。プレイヤーが売った品は雛形に無いので、
        今までどおり入れ替えで流れる（この MOD の元の目的）。
        作れなかったぶんは控えの現物を戻す（品揃えが減るよりまし）。
        """
        entries = goods_entries(pending["facility"])
        if not entries:
            return
        app = pending["app"]
        owner = pending["owner"]
        present = {item_name(item) for item in inventory.values()}
        built, restored = [], []
        for entry in entries:
            name = entry.get("name")
            if not name or name in present:
                continue
            key = build_one(app, owner, entry)
            if key is not None:
                built.append("{}={}".format(key, name))
                present.add(name)
                continue
            # 作れなかった。控えに同じ品が居れば戻す。
            for old_key, item in pending["snapshot"].items():
                if item_name(item) != name or old_key in inventory:
                    continue
                try:
                    inventory[old_key] = item
                except Exception:
                    ctx.log_exc("shop restock: cannot put the old stock back")
                    break
                restored.append("{}={}".format(old_key, name))
                present.add(name)
                break
        who = label(pending["app"], pending["owner_id"], pending["facility"])
        if built:
            write("built missing: {} generated {} item(s): {}".format(
                who, len(built), ", ".join(built)))
        if restored:
            write("kept in stock: {} put {} unbuilt item(s) back: {}".format(
                who, len(restored), ", ".join(restored)))

    # ------------------------------------------------------------ 新しい品揃え
    def stock_structure():
        """LLM の返却型。`Literal` は使わない（空だと pydantic が落ちる。llm.py）。"""
        item = llm.create_structure(ctx, "ShopStockItem", {
            "item_name": (str, ...), "description": (str, ...),
            "item_type": (str, ...), "item_sub_type": (str, ...),
            "value": (int, ...), "item_appearance": (str, ...),
            "rarity": (str, ...)}, label="shop restock")
        if item is None:
            return None
        return llm.create_structure(ctx, "ShopStock", {
            "items": (typing.List[item], ...)}, label="shop restock")

    def stock_messages(pending, values):
        """入れ替え1回ぶんの頼み文。場所は施設と土地、価値は雛形の値をそのまま。"""
        app = pending["app"]
        facility = pending["facility"]
        area = ui.current_area(app)
        old_names = sorted({item_name(i) for i in pending["snapshot"].values()}
                           - {"?"})
        types = "、".join("{}: {}".format(t, "/".join(subs))
                          for t, subs in ITEM_TYPES.items())
        text = (
            "あなたはRPGの売買イベントの管理者だ。\n【指示】\n"
            "店主が仕入れを入れ替えた。この店に新しく並ぶ商品を{n}個作れ。"
            "前の品揃えと同じ名前の商品は作らない。武器と防具をそれぞれ1つ以上含める。\n\n"
            "【場所】\n世界: {world}\n土地: {area}\n{area_desc}\n"
            "店: {shop}（{kind}）\n{shop_desc}\n\n"
            "【アイテムの生成要素】\n"
            "- item_name: 日本語で。\n"
            "- description: 日本語で1〜2文。\n"
            "- item_type と item_sub_type: 次の組から選ぶ。{types}\n"
            "- value: アイテムの価値。次の値を順に1つずつ使う: {values}。"
            "この世界における最低が1で最高が70。高いほど強力であったり、特別であったりする。\n"
            "- item_appearance: 見た目を日本語で短く（画像の選択に使う）。\n"
            "- rarity: {rarities} のいずれか。value が低いほど common。\n"
            "- 前の品揃え（同じ名前を作らない）: {old}\n"
        ).format(
            n=len(values),
            world=frames.short(frames.attr(getattr(app, "world", None), "name", ""), 60),
            area=frames.short(frames.attr(area, "name", ""), 60),
            area_desc=frames.short(frames.attr(area, "description", "") or "", 600),
            shop=frames.short(frames.attr(facility, "name", ""), 60),
            kind=frames.attr(facility, "facility_type", ""),
            shop_desc=frames.short(frames.attr(facility, "description", "") or "", 400),
            types=types, values=values, rarities="/".join(RARITIES),
            old="、".join(old_names) or "無し")
        return [{"role": "system", "content": text},
                {"role": "user", "content": "＜入れ替え＞"}]

    def stock_values(pending):
        """作る品の数と価値。雛形の各品の `value`（世界を作ったときの価値段階）。"""
        entries = goods_entries(pending["facility"]) or []
        values = [e.get("value") for e in entries
                  if isinstance(e.get("value"), int)][:NEW_STOCK_MAX]
        while values and len(values) < NEW_STOCK_MIN:
            values.append(values[len(values) % len(values)])
        return values

    def tidy(row, value):
        """LLM の1行をゲームの入口の引数へ。語彙から外れた値は寄せる。"""
        if not isinstance(row, dict):
            return None
        name = frames.short(str(row.get("item_name") or "").strip(), 40)
        if not name:
            return None
        item_type = str(row.get("item_type") or "")
        if item_type not in ITEM_TYPES:
            item_type = "utility"
        sub = str(row.get("item_sub_type") or "")
        if sub not in ITEM_TYPES[item_type]:
            sub = ITEM_TYPES[item_type][0]
        got = row.get("value")
        if isinstance(got, int) and not isinstance(got, bool):
            value = max(1, min(70, got))
        rarity = str(row.get("rarity") or "")
        if rarity not in RARITIES:
            rarity = "common"
        return (name, str(row.get("description") or ""), item_type, sub,
                value, str(row.get("item_appearance") or name), rarity)

    def generate_lineup(pending):
        """LLM に品揃え一式を作らせ、1品ずつゲームの入口で主の棚へ入れる。作れた数。"""
        app = pending["app"]
        owner = pending["owner"]
        maker = frames.attr(app, "generate_item_from_item_data")
        values = stock_values(pending)
        if maker is frames.MISSING or not values:
            return 0
        structure = stock_structure()
        if structure is None:
            return 0
        # LLM を待つ間はゲームと同じ待機表示（点のアニメーション）を出し、
        # 選択肢と自由入力を塞ぐ（`ui.Screen`。`301_` / `316_` と同じ口）。
        # 解くときに選択肢を塗り直さないのは、この直後にゲームが売買画面を出すため
        # （塗ると一瞬だけ古い選択肢が見える）。
        screen.busy_on(app)
        try:
            raw = llm.ask(ctx, MANAGER_NAME, stock_messages(pending, values),
                          timeout=NEW_STOCK_TIMEOUT, structure=structure,
                          label="shop restock", write=write)
        finally:
            screen.busy_off(app, restore=False)
        rows = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(rows, list) or not rows:
            write("WARN new stock: the LLM returned no items ({})".format(
                type(raw).__name__))
            return 0
        made = []
        for index, row in enumerate(rows[:len(values)]):
            fields = tidy(row, values[index])
            if fields is None:
                continue
            before = set(inventory_of(owner) or {})
            try:
                item = maker(*(fields + (owner,)))
            except Exception:
                ctx.log_exc("shop restock: generate_item_from_item_data failed")
                continue
            inventory = inventory_of(owner)
            if not isinstance(inventory, dict):
                continue
            if set(inventory) - before:
                made.append(fields[0])
                continue
            if item is None:
                continue
            # ゲームが棚へ入れない作りなら、台帳から採った鍵で自分で入れる。
            try:
                inventory[ids.claim(app, "item")] = item
                made.append(fields[0])
            except Exception:
                ctx.log_exc("shop restock: cannot place the generated item")
        write("new stock: {} generated {} of {} item(s): {}".format(
            label(app, pending["owner_id"], pending["facility"]),
            len(made), len(rows), "、".join(made)))
        return len(made)

    def verify(pending):
        """空にした後どうなったかを見る。ここで必ず決着を付ける。"""
        app = pending["app"]
        owner_id = pending["owner_id"]
        owner = pending["owner"]
        facility = pending["facility"]
        who = label(app, owner_id, facility)

        inventory = inventory_of(owner)
        if inventory is None:
            write("WARN restore: {} inventory became unreadable".format(who))
            return

        if not inventory:
            # ゲームは空のままにした。
            # 段(tier)を見たことがあれば自分で呼ぶ。
            tier = store["tiers"].get(owner_id)
            if tier is not None:
                filled = regenerate(pending, tier)
                # `or {}` にしてはいけない。
                # ここへ来るのは在庫が空のときなので、生成が実らなければ
                # `inventory_of` は空の**実体**を返す。`or` はそれを捨てて
                # 別の入れ物を掴ませる。以降の `update` が本物に届かなくなり、
                # 控えを戻したつもりで店の中身を失う。
                refilled = inventory_of(owner)
                if refilled is not None:
                    inventory = refilled
                if filled and inventory:
                    write("regenerated by hand: {} tier={} items={} "
                          "(shows from the next visit)".format(
                              who, tier, len(inventory)))

        if inventory:
            if not pending.get("generated"):
                # 新しい品揃えを作れた回は雛形を足さない（同じ品名が戻る）。
                refill_missing(pending, inventory)
            key, bucket = bucket_of(app)
            bucket[owner_id] = ordered_record(pending["day"], facility,
                                              len(inventory),
                                              store["tiers"].get(owner_id))
            save_bucket(key, bucket)
            if store["auto_refill"] is None:
                store["auto_refill"] = True
            write("restocked: {} day={} items={}".format(
                who, pending["day"], len(inventory)))
            return

        # 補充されなかった。
        # 控えを戻して、以後この版では空にしない。
        store["auto_refill"] = False
        try:
            inventory.update(pending["snapshot"])
            ok = True
        except Exception:
            ctx.log_exc("shop restock: cannot restore the inventory")
            ok = False
        ctx.log("shop restock: this build did not refill an emptied shop; "
                "restock disabled ({} items {})".format(
                    len(pending["snapshot"]), "restored" if ok else "LOST"),
                level="WARN")
        # **`ok` を見て書く。**
        # ここは控えを戻す唯一の場所なので、戻せなかったことが分かるのは
        # このログだけ。常に `restored` と書くと、ローダのログだけが `LOST`
        # と言い、MOD のログは戻ったと言う。後から読む側は、
        # 店の中身が消えた回とそうでない回を見分けられない。
        write("WARN not refilled: {} - {} {} item(s), "
              "restock disabled for this run".format(
                  who, "restored" if ok else "LOST", len(pending["snapshot"])))

    def regenerate(pending, tier):
        """ゲーム自身の生成経路を直に呼ぶ。呼べたら True。"""
        manager = pending["manager"]
        method = frames.attr(manager, "set_item_from_world_data")
        if method is frames.MISSING:
            return False
        try:
            method(pending["owner"], tier)
            return True
        except Exception:
            ctx.log_exc("shop restock: set_item_from_world_data failed")
            return False

    # ------------------------------------------------------------ フック
    @ctx.wrap("__main__:ShoppingStartManagerRemake.execute", safe=True)
    def shopping_start(orig, self, *args, **kwargs):
        try:
            prepare(self)
        except Exception:
            ctx.log_exc("shop restock: prepare failed")
            store["pending"] = None
        # 空にした回（入れ替え）は素通しする。
        # ここで止めると、こちらが空にした店をゲームが埋め直したぶんまで外して
        # 入れ替えそのものが働かなくなる。
        store["held"] = None
        if store["pending"] is None:
            try:
                store["held"] = held_keys(self)
            except Exception:
                ctx.log_exc("shop restock: cannot read the stock before opening")
        elif NEW_STOCK:
            # 空にした直後、ここ（ゲームが LLM を呼ぶのと同じ別スレッド）で
            # 新しい品揃えを作る。作れたら、この後ゲームが雛形から作り直す
            # ぶん（同じ品名）は画面を開く側で外す（`held` に今の鍵を控える）。
            try:
                if generate_lineup(store["pending"]) > 0:
                    store["pending"]["generated"] = True
                    store["held"] = held_keys(self, always=True)
            except Exception:
                ctx.log_exc("shop restock: cannot generate the new stock")
        # 控えを外すのは `before_window`（画面を開く側）。
        # ここ（別スレッド）で外すと、メインスレッドが持ち物の辞書を
        # 回している最中に鍵が消えてゲームごと落ちる（モジュール冒頭の説明）。
        # 決着は `finally` で付ける。
        # `orig` が投げた場合、`safe=True` は素の動作に落としてくれるが、
        # **この後ろの行は実行されない**。空にした店の控えが更新されないまま残り、
        # 次の来店でもまた空にすることになる（毎回まっさらな店になる）。
        # 空にしたのは `orig` の手前なので、後始末も投げ方に関わらず要る。
        try:
            return orig(self, *args, **kwargs)
        finally:
            pending = store["pending"]
            store["pending"] = None
            if pending is not None:
                # Clock が使えない場面で予約が取れないときはその場で決着させる。
                if not screen.schedule(lambda: verify(pending), VERIFY_DELAY):
                    screen.guarded(lambda: verify(pending))

    @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window",
              required=False, safe=True)
    def before_window(orig, self, left_inventory_obtainer=None, *args, **kwargs):
        """売買画面が組み上がる手前で、その来店で増えたぶんを外す。

        ここはメインスレッドで、ゲームが持ち物の辞書を回すより前。
        控えは主が一致したときだけ使う（`402_` の受け渡しの窓は素通し）。
        """
        held = store.get("held")
        if held is not None:
            owner = held["owner"]
            same = (left_inventory_obtainer is owner
                    or (owner is not None and str(getattr(
                        left_inventory_obtainer, "id", None)) == held["owner_id"]))
            if same:
                store["held"] = None
                try:
                    drop_refilled(held)
                except Exception:
                    ctx.log_exc("shop restock: cannot drop the refilled stock")
        return orig(self, left_inventory_obtainer, *args, **kwargs)

    @ctx.wrap("__main__:ShoppingStartManagerRemake.set_item_from_world_data",
              required=False, safe=True)
    def observe_tier(orig, self, shop_owner_instance, next_tier, *args, **kwargs):
        try:
            owner_id = getattr(shop_owner_instance, "id", None)
            if owner_id is not None and isinstance(next_tier, (int, float, str)):
                known = store["tiers"].get(str(owner_id))
                store["tiers"][str(owner_id)] = next_tier
                if known != next_tier:
                    write("tier: owner={} tier={!r}".format(owner_id, next_tier))
        except Exception:
            ctx.log_exc("shop restock: cannot record the stock tier")
        return orig(self, shop_owner_instance, next_tier, *args, **kwargs)

    ctx.log("shop restock: every {} in-game day(s), keep_sold_out={}, "
            "new_stock={}; log goes to out/{}".format(
                RESTOCK_DAYS, bool(KEEP_SOLD_OUT), bool(NEW_STOCK),
                LOG_BASENAME))
