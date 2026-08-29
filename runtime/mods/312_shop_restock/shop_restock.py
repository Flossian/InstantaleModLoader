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

最悪でも「1回だけ空の店を見て、次の来店から元の品揃えが戻る」で止まる。
店が永久に空になることはない。
段(tier)はゲームが `set_item_from_world_data` に渡す値をそのまま覚えて使う。
値の意味は解釈しない（`307_` が移動確認画面の `args` をそのまま写すのと同じ形）。

待ってから見るのは、生成が Clock コールバックへ回される版でも取りこぼさないため。
`orig` の直後に見ると「まだ作っていないだけ」を「補充されなかった」と読み違える。

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

from instantale_modloader import frames, ui
from instantale_modloader.state import WorldStore, world_key

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
RESTOCK_DAYS = 30            # 何日経ったら品揃えを入れ替えるか
RESTOCK_ON_FIRST_SHOP = False  # 初めて開いた店をその場で入れ替えるか

LOG_BASENAME = "shop_restock.log"

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
            "auto_refill": None,  # None=未確認 / True=補充される / False=されない
        }
        setattr(sys, STORE_ATTR, store)

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
        """入れ替え日が来ていれば主の持ち物を空にする。控えは `pending` に置く。"""
        store["pending"] = None
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

    ctx.log("shop restock: every {} in-game day(s); log goes to out/{}".format(
        RESTOCK_DAYS, LOG_BASENAME))
