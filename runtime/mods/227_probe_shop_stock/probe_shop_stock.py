# -*- coding: utf-8 -*-
r"""計測: 買った品が店の棚に戻るのは誰の仕業かを録る。ゲームは変えない。

##### 何を決めるための計測か

店で買うと所持金は表示どおり引かれ、品は手持ちに入る。
ところが売買画面を開き直すと、同じ品がまた棚に並ぶ。
セーブを復号して突き合わせたところ、次まで分かっている
（2026-09-04。`新テストワールド` / 施設 287「砂塵の荷物屋」/ 主 118）:

| 分かっていること | 根拠 |
| --- | --- |
| 買った品は**正しく手持ちへ移る**（鍵 `item_45` のまま） | `player_data/inventory/item_45` |
| 棚に残るのは**別の現物**で、鍵が `item_` の付かない裸の数字（`53`） | `npcs/118/inventory/53` |
| その裸の数字は**ゲームの採番台帳から採られている**（`index.item` が 54 へ進む） | `index` |
| 中身は店の**品揃えの雛形そのまま**（`回復: 269` / `疲労負荷: 9`） | `areas/3/nodes/45/facilities/287/config/goods[2]` と一致 |
| 雛形（`goods` 8件）は買っても減らない。`stock_update_date` は初回来店の日のまま | 同じ `config` |
| `134_` が作り直した跡が無い（`疲労負荷` が 0 になっていない） | 上の `attributes` |
| 買うたびに1つ増える | `405_` の一覧が 10 → 12 → 13。同じ糧食が手持ちに4つ（`item_45` / `53` / `54` / `55`） |

つまり**「移った後に、雛形からもう1つ作られて棚へ入る」**。
分かっていないのはその1点だけ:

    録れていない   その現物を作っているのは誰か（どの関数が、どこから呼ばれて)
    録れていない   作られるのはいつか（買った瞬間か、画面を閉じたときか、次に開くときか）
    録れていない   `config['goods']` は買ったときに減るはずのものなのか
                   （減らないから作り直されるのか、減らないのが正しくて別の門が要るのか）

同梱 MOD で持ち物へ品を足すものは無い（`grep` で `inventory` を書き換えるのは
`312_`（入れ替え日に空にする）と `402_`（受け渡しの2人の間だけ）の2本きりで、
どちらもこの場面のログが空）。
`generate_item_in_shopping` は買った時刻に1件も走っていない（`221_`）。
それでも品が増えている以上、作っている口はまだ見えていない。

##### なぜ既存の記録では出ないのか

品を作る3つの入口（`generate_item_from_dict` / `_item_data` / `_ready_made_data`）は
`129_` と `134_` が包んでいるが、**どちらも `orig` の戻り値しか見ていない**。
`generate_item_in_shopping` が `None` を返して品を主の持ち物へ直に入れるのと同じ作りなら
（GAME.md §2.13.1.2）、戻り値は `None` になり、両方とも何もせずに素通りする。
作られたことも、値段が付いたことも、どのログにも残らない。

そこで**品の生まれ方そのもの**を見る。`scripts.items:Item.__init__` は
`(name, item_type, attributes, description, value, size, image_src, rarity,
skill, obtainer, id, ...)` を受け取るので、
生まれた瞬間に「誰の持ち物として」「どの id で」作られたかがそのまま出る。
呼び出し元は `frames.caller()` でゲーム側のフレームだけを並べる。

##### 録るもの

| 何を録るか | 見どころ |
| --- | --- |
| `Item.__init__` | 生まれた品の id・名前・`obtainer`・`attributes`・**呼び出し元の連鎖**。棚に戻る現物がどこで生まれるか |
| 店の場面の境目 | `ShoppingStartManagerRemake.execute` / `shopping_start_method_1` / `toggle_twin_inventory_window` / `buy_item` / `sell_item` / `close_shopping_window_process` の前後で、主と手持ちの鍵の増減 |
| 品揃えの雛形 | その施設の `config['goods']` の件数・`stock_tier`・`stock_update_date` と今日の日数。買って減るのか、いつ書き換わるのか |
| 採番台帳 | `index['item']` の前後。裸の数字を採ったのが誰の仕事か |
| 生成の入口 | `generate_item_from_*` / `generate_item_in_shopping` / `set_item_from_world_data` の呼び出しと、その間の持ち物の増減（戻り値が `None` でも増減で分かる） |

**どの境目の中で増えたか**が答えになる。
内側の包みほど範囲が狭いので、増分が出た一番内側の行が「作った場所」を指す。

200番台の約束どおり読み取りだけ。
`safe=True` と握り潰しで、記録に失敗しても本体は必ず1回呼ぶ。

出力は `out/shop_stock.log`（読む用）と `out/shop_stock.jsonl`（1件1行）。
"""

import datetime
import json

from instantale_modloader import frames, ui

LOG_BASENAME = "shop_stock.log"
RECORD_BASENAME = "shop_stock.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
ITEM_SAMPLES = 200
BOUNDARY_SAMPLES = 400
CALLER_DEPTH = 8

# 売買画面の場面名（`405_` と同じ）。
TRADE_SITUATION = "shop"

# 品揃えの雛形が入っている施設の設定の鍵。
GOODS_KEYS = ("goods", "stock_tier", "stock_update_date")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record_path = ctx.out_path(RECORD_BASENAME)
    seen = {"items": 0, "boundaries": 0}
    state = {"in_shop": 0}

    def plain(value):
        """記録に出せる形にする。`_` で始まる鍵（現物への参照）は落とす。"""
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items()
                    if not str(key).startswith("_")}
        return value

    def record(row):
        """1件1行の JSON。読む用のログとは別に、後から数えるために残す。"""
        row = plain(row)
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("shop stock probe: cannot write the record")

    def take(bucket, limit):
        if limit <= 0 or seen[bucket] >= limit:
            return False
        seen[bucket] += 1
        return True

    # ------------------------------------------------------------ ゲームを読む
    def app_of(holder):
        app = frames.attr(holder, "app", None)
        return app if app is not None else ui.find_app()

    def inventory_of(obtainer):
        """持ち物の実体（`{item_id: 品}`）。**毎回引き直す**（掴んだままにしない）。"""
        if obtainer is None:
            return None
        inventory = frames.attr(obtainer, "inventory", None)
        if isinstance(inventory, dict):
            return inventory
        inner = frames.attr(inventory, "inventory", None)
        return inner if isinstance(inner, dict) else None

    def name_of(value):
        name = frames.attr(value, "name", None)
        return name if isinstance(name, str) and name else "?"

    def who(obtainer):
        if obtainer is None:
            return "-"
        return "{}({})".format(name_of(obtainer), frames.attr(obtainer, "id", "?"))

    def facility_of(app):
        """プレイヤーが今いる施設。ロード直後は id の文字列なので引き直す。"""
        location = frames.attr(frames.attr(app, "player", None), "location", None)
        if location is None:
            return None
        if not isinstance(location, str):
            return location
        try:
            return ui.find_facility(ui.current_area(app), location)[0]
        except Exception:
            return None

    def shop_owner(app):
        facility = facility_of(app)
        owner = frames.attr(facility, "owner", None) if facility is not None else None
        if owner is None:
            return None
        return ui.character_of(app, str(owner))

    def goods_brief(app):
        """今の施設の品揃えの雛形。買って減るのかを見るのが目的。"""
        facility = facility_of(app)
        config = frames.attr(facility, "config", None) if facility is not None else None
        if not isinstance(config, dict):
            return None
        out = {"facility": frames.attr(facility, "id", None)}
        for key in GOODS_KEYS:
            value = config.get(key)
            if key == "goods":
                out["goods"] = ([g.get("name") for g in value
                                 if isinstance(g, dict)]
                                if isinstance(value, list) else
                                frames.repr_value(value))
            else:
                out[key] = value
        out["day"] = ui.game_day(app)
        return out

    def index_item(app):
        """ゲームの採番台帳。`index['item']` を持っている辞書を全部。

        `ids.stores()` が返すのは `(どこにあるか, index の辞書)` の並び
        （根の辞書ではない）。
        """
        out = {}
        try:
            from instantale_modloader import ids
            for label, index in ids.stores(app):
                if isinstance(index, dict) and "item" in index:
                    out[label] = index["item"]
        except Exception:
            return None
        return out

    def listing(obtainer):
        """持ち物を `({鍵: 名前}, {鍵: 現物})` で写す。

        現物そのものを控えるのは、**同じ鍵のまま中身が別の現物に入れ替わった**
        場合を「変化なし」と読まないため。
        `id()` の数を控えて後で比べてはいけない。
        消えた現物の番地は次の現物に割り当て直されることがあり、
        入れ替えが「同じ番地」に見える（実際に検査で外した）。
        控えは境目1つのあいだだけ生きる。
        """
        inventory = inventory_of(obtainer)
        if not isinstance(inventory, dict):
            return None, None
        names, refs = {}, {}
        for key, value in inventory.items():
            names[str(key)] = name_of(value)
            refs[str(key)] = value
        return names, refs

    def snap(app):
        if app is None:
            return None
        owner = shop_owner(app)
        shop_names, shop_refs = listing(owner)
        player_names, player_refs = listing(frames.attr(app, "player", None))
        return {"shop_who": who(owner), "shop": shop_names,
                "player": player_names,
                "index": index_item(app), "goods": goods_brief(app),
                # 記録には出さない（現物への参照。`record` が `_` で始まる鍵を落とす）。
                "_refs": {"shop": shop_refs, "player": player_refs}}

    # ------------------------------------------------------------ 差分
    def side_diff(before, after, before_refs, after_refs):
        """片側の持ち物の増減。`(増えた, 減った, 中身が入れ替わった)`。"""
        if not isinstance(before, dict) or not isinstance(after, dict):
            return [], [], []
        added = ["{}={}".format(key, after[key])
                 for key in after if key not in before]
        gone = ["{}={}".format(key, before[key])
                for key in before if key not in after]
        swapped = ["{}={}->{}".format(key, before[key], after[key])
                   for key in after
                   if key in before
                   and (before_refs or {}).get(key) is not (after_refs or {}).get(key)]
        return added, gone, swapped

    def describe(before, after):
        """境目1つぶんの変化を短い文にする。何も動いていなければ空。"""
        parts = []
        before_refs = before.get("_refs") or {}
        after_refs = after.get("_refs") or {}
        for side, label in (("shop", "店"), ("player", "手持ち")):
            added, gone, swapped = side_diff(
                before.get(side), after.get(side),
                before_refs.get(side), after_refs.get(side))
            if added or gone or swapped:
                bits = []
                if added:
                    bits.append("+" + ",".join(added))
                if gone:
                    bits.append("-" + ",".join(gone))
                if swapped:
                    bits.append("!" + ",".join(swapped))
                parts.append("{} {}".format(label, " ".join(bits)))
        if before.get("index") != after.get("index"):
            parts.append("index {} -> {}".format(before.get("index"),
                                                 after.get("index")))
        old_goods = (before.get("goods") or {}).get("goods")
        new_goods = (after.get("goods") or {}).get("goods")
        if old_goods != new_goods:
            parts.append("goods {} -> {}".format(
                len(old_goods) if isinstance(old_goods, list) else old_goods,
                len(new_goods) if isinstance(new_goods, list) else new_goods))
        old_date = (before.get("goods") or {}).get("stock_update_date")
        new_date = (after.get("goods") or {}).get("stock_update_date")
        if old_date != new_date:
            parts.append("stock_update_date {} -> {}".format(old_date, new_date))
        return "  ".join(parts)

    def around(label, app, call):
        """`orig` を1回だけ呼び、その間の増減を録る。例外は素通しする。"""
        before = snap(app)
        try:
            return call()
        finally:
            try:
                after = snap(app)
                if before is not None and after is not None:
                    changed = describe(before, after)
                    if changed and take("boundaries", BOUNDARY_SAMPLES):
                        write("境目 {}: {}".format(label, changed))
                        record({"at": now(), "phase": "境目", "where": label,
                                "changed": changed, "before": before,
                                "after": after})
            except Exception:
                ctx.log_exc("shop stock probe: cannot record a boundary")

    # ------------------------------------------------------------ 品の生まれ方
    @ctx.wrap("scripts.items:Item.__init__", required=False, safe=True)
    def item_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            # 店の場面の中は全部録る。外は枠の中だけ（ロードで何百も生まれる）。
            if state["in_shop"] > 0 or take("items", ITEM_SAMPLES):
                attributes = frames.attr(self, "attributes", None)
                row = {"at": now(), "phase": "品の誕生",
                       "id": frames.attr(self, "id", None),
                       "name": frames.short(name_of(self), 40),
                       "obtainer": who(frames.attr(self, "obtainer", None)),
                       "attributes": attributes if isinstance(attributes, dict)
                       else frames.repr_value(attributes),
                       "in_shop": state["in_shop"] > 0,
                       "caller": frames.caller(CALLER_DEPTH)}
                record(row)
                write("品の誕生: id={!r} {!r} 主={} {} 呼び出し元: {}".format(
                    row["id"], row["name"], row["obtainer"],
                    row["attributes"], row["caller"]))
        except Exception:
            ctx.log_exc("shop stock probe: cannot record a new item")
        return result

    # ------------------------------------------------------------ 店の場面
    @ctx.wrap("__main__:ShoppingStartManagerRemake.execute", safe=True)
    def shopping_execute(orig, self, *args, **kwargs):
        app = app_of(self)
        state["in_shop"] += 1
        try:
            return around("ShoppingStartManagerRemake.execute", app,
                          lambda: orig(self, *args, **kwargs))
        finally:
            state["in_shop"] -= 1

    @ctx.wrap("__main__:ShoppingStartManagerRemake.shopping_start_method_1",
              required=False, safe=True)
    def shopping_start_method_1(orig, self, *args, **kwargs):
        app = app_of(self)
        state["in_shop"] += 1
        try:
            return around("shopping_start_method_1", app,
                          lambda: orig(self, *args, **kwargs))
        finally:
            state["in_shop"] -= 1

    @ctx.wrap("__main__:ShoppingStartManagerRemake.set_item_from_world_data",
              required=False, safe=True)
    def set_item_from_world_data(orig, self, shop_owner_instance=None,
                                 next_tier=None, *args, **kwargs):
        app = app_of(self)
        return around("set_item_from_world_data(tier={!r})".format(next_tier), app,
                      lambda: orig(self, shop_owner_instance, next_tier,
                                   *args, **kwargs))

    @ctx.wrap("__main__:ShoppingStartManagerRemake.generate_item_in_shopping",
              required=False, safe=True)
    def generate_item_in_shopping(orig, self, item_data=None,
                                  shop_owner_instance=None,
                                  item_stock_tier=None, *args, **kwargs):
        app = app_of(self)
        return around("generate_item_in_shopping(tier={!r})".format(item_stock_tier),
                      app, lambda: orig(self, item_data, shop_owner_instance,
                                        item_stock_tier, *args, **kwargs))

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_dict", safe=True)
    def generate_item_from_dict(orig, self, *args, **kwargs):
        return around("generate_item_from_dict", self,
                      lambda: orig(self, *args, **kwargs))

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_item_data", safe=True)
    def generate_item_from_item_data(orig, self, *args, **kwargs):
        return around("generate_item_from_item_data", self,
                      lambda: orig(self, *args, **kwargs))

    @ctx.wrap("__main__:InstantaleApp.generate_item_from_ready_made_data",
              required=False, safe=True)
    def generate_item_from_ready_made_data(orig, self, *args, **kwargs):
        return around("generate_item_from_ready_made_data", self,
                      lambda: orig(self, *args, **kwargs))

    @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window", safe=True)
    def twin_window(orig, self, left_inventory_obtainer=None,
                    right_inventory_obtainer=None, left_label_text=None,
                    situation=None, *args, **kwargs):
        try:
            if situation == TRADE_SITUATION:
                write("売買画面: 左={} 右={} 雛形={}".format(
                    who(left_inventory_obtainer), who(right_inventory_obtainer),
                    goods_brief(self)))
        except Exception:
            ctx.log_exc("shop stock probe: cannot record the trade window")
        return around("toggle_twin_inventory_window({!r})".format(situation), self,
                      lambda: orig(self, left_inventory_obtainer,
                                   right_inventory_obtainer, left_label_text,
                                   situation, *args, **kwargs))

    @ctx.wrap("__main__:InstantaleApp.buy_item", safe=True)
    def buy_item(orig, self, item_instance=None, *args, **kwargs):
        return around("buy_item({!r})".format(frames.short(name_of(item_instance), 24)),
                      self, lambda: orig(self, item_instance, *args, **kwargs))

    @ctx.wrap("__main__:InstantaleApp.sell_item", safe=True)
    def sell_item(orig, self, item_instance=None, *args, **kwargs):
        return around("sell_item({!r})".format(frames.short(name_of(item_instance), 24)),
                      self, lambda: orig(self, item_instance, *args, **kwargs))

    @ctx.wrap("__main__:InstantaleApp.close_shopping_window_process",
              required=False, safe=True)
    def close_shopping(orig, self, *args, **kwargs):
        return around("close_shopping_window_process", self,
                      lambda: orig(self, *args, **kwargs))

    @ctx.wrap("scripts.items:Item.buy", required=False, safe=True)
    def item_buy(orig, self, *args, **kwargs):
        return around("Item.buy({!r})".format(frames.short(name_of(self), 24)),
                      ui.find_app(), lambda: orig(self, *args, **kwargs))

    @ctx.wrap("scripts.items:Item.sell", required=False, safe=True)
    def item_sell(orig, self, *args, **kwargs):
        return around("Item.sell({!r})".format(frames.short(name_of(self), 24)),
                      ui.find_app(), lambda: orig(self, *args, **kwargs))

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.change_inventory",
              required=False, safe=True)
    def change_inventory(orig, self, new_inventory=None, *args, **kwargs):
        item = frames.attr(self, "item_instance", None)
        return around("change_inventory({!r} -> {})".format(
            frames.short(name_of(item), 24),
            who(frames.attr(new_inventory, "obtainer", None))),
            ui.find_app(), lambda: orig(self, new_inventory, *args, **kwargs))

    ctx.log("shop stock probe: watching the shop path; "
            "records go to out/{} and out/{}".format(
                LOG_BASENAME, RECORD_BASENAME))
