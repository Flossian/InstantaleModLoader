# -*- coding: utf-8 -*-
"""227_probe_shop_stock をゲーム抜きで通す。

    python tools/tests/test_shop_stock_probe.py

偽の app / Character / Item / Facility を差し込み、次を確認する。

  素通し   … 包んだ関数は必ず1回だけ呼ばれ、戻り値と例外がそのまま通る
  境目     … 持ち物が動いた呼び出しだけが記録に出る（動かない呼び出しは書かない）
  増減     … 増えた鍵・減った鍵・同じ鍵のまま中身が入れ替わった鍵を書き分ける
  雛形     … 施設の goods / stock_tier / stock_update_date と採番台帳を写す
  誕生     … Item.__init__ の id・持ち主・attributes・呼び出し元を写す
  枠       … 店の外の誕生は ITEM_SAMPLES で止まり、店の場面の中は止まらない
  安全     … 施設も主も引けない場面で例外を出さない
"""
import importlib.util
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_probe_shop_stock")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Item:
    def __init__(self, item_id, name, attributes=None, obtainer=None):
        self.id = item_id
        self.name = name
        self.attributes = dict(attributes or {})
        self.obtainer = obtainer


class Character:
    def __init__(self, character_id, name, inventory=None):
        self.id = character_id
        self.name = name
        self.inventory = dict(inventory or {})


class Facility:
    def __init__(self, facility_id, owner, config):
        self.id = facility_id
        self.name = "砂塵の荷物屋"
        self.facility_type = "general_store"
        self.owner = owner
        self.config = config


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, facilities):
        self.id = area_id
        self.nodes = {"45": Node(facilities)}


class World:
    def __init__(self, areas, characters, days_elapsed):
        self.name = "テスト世界"
        self.areas = areas
        self.characters = characters
        self.days_elapsed = days_elapsed


class ShoppingStartManagerRemake:
    """売買の入口。**`app` を持っている**（probe はここから app を引く）。"""

    def __init__(self, app):
        self.app = app


class InstantaleApp:
    def __init__(self, world, player, index):
        self.world = world
        self.player = player
        # 採番台帳。`ids.stores` はこの形（`index` を持つ根の辞書）を探す。
        self.world_dict = {"index": dict(index)}
        self.save_data_dict = {"index": dict(index)}


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="shop_stock_probe_mod"):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


# ---------------------------------------------------------------- 舞台作り
EXECUTE = "__main__:ShoppingStartManagerRemake.execute"
BUY = "__main__:InstantaleApp.buy_item"
WINDOW = "__main__:InstantaleApp.toggle_twin_inventory_window"
ITEM_INIT = "scripts.items:Item.__init__"
LOG_NAME = "shop_stock.log"
RECORD_NAME = "shop_stock.jsonl"

GOODS = [{"name": "乾いた砂の糧食"}, {"name": "砂漠のハーブティー"}]


def fresh(module, **settings):
    """設定を差し替えて apply() をやり直す。前の場面のログは捨てる。"""
    for name, value in settings.items():
        setattr(module, name, value)
    for leftover in (LOG_NAME, RECORD_NAME):
        path = os.path.join(OUT_DIR, leftover)
        if os.path.exists(path):
            os.remove(path)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return ctx


def stage(shop_stock=(), player_stock=()):
    owner = Character("118", "ハルマン", {i.id: i for i in shop_stock})
    player = Character("0", "ミツバ", {i.id: i for i in player_stock})
    facility = Facility("287", "118", {"goods": list(GOODS), "stock_tier": 1,
                                       "stock_update_date": 404})
    area = Area("3", [facility])
    world = World({"3": area}, {"118": owner}, 404)
    app = InstantaleApp(world, player, {"item": 51})
    player.location = facility
    player.current_area = area
    return app, owner, player


def read(ctx, name=LOG_NAME):
    path = os.path.join(ctx.out_dir, name)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def main():
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    module = load_mod()

    # ---- 素通しと、動かない呼び出しは書かないこと ----------------------
    ctx = fresh(module)
    food = Item("item_45", "乾いた砂の糧食", {"回復": 269, "疲労負荷": 9})
    app, owner, player = stage(shop_stock=(food,))
    calls = []

    def quiet(self, *args, **kwargs):
        calls.append("quiet")
        return "答え"

    result = ctx.hooks[EXECUTE](quiet, ShoppingStartManagerRemake(app), "売買する")
    check("素通し: 戻り値がそのまま", result == "答え", result)
    check("素通し: 1回だけ呼ぶ", calls == ["quiet"], calls)
    check("動かない呼び出しは書かない", "境目" not in read(ctx), read(ctx))

    # ---- 買った品が移り、雛形からもう1つ増える（報告された壊れ方）------
    def leaky_buy(self, item_instance, *args, **kwargs):
        # 手持ちへ移す（本体の正しい側）
        self.player.inventory[item_instance.id] = item_instance
        owner.inventory.pop(item_instance.id, None)
        # 雛形からもう1つ作って棚へ入れ、採番台帳を進める（見えていない側）
        fresh_item = Item("51", "乾いた砂の糧食", {"回復": 269, "疲労負荷": 9},
                          obtainer=owner)
        owner.inventory["51"] = fresh_item
        for store in (self.world_dict, self.save_data_dict):
            store["index"]["item"] = 52
        return True

    ctx.hooks[BUY](leaky_buy, app, food)
    log = read(ctx)
    check("境目: 買ったところが記録に出る", "境目 buy_item" in log, log)
    check("境目: 棚に増えた鍵が出る", "+51=乾いた砂の糧食" in log, log)
    check("境目: 棚から減った鍵が出る", "-item_45=乾いた砂の糧食" in log, log)
    check("境目: 手持ちに増えた鍵が出る", "手持ち +item_45" in log, log)
    check("境目: 採番台帳の前後が出る", "index " in log, log)
    check("例外を出していない", not ctx.errors, ctx.errors)

    row = [json.loads(line) for line in read(ctx, RECORD_NAME).splitlines()]
    check("記録: jsonl に境目が1件", len(row) == 1, len(row))
    if row:
        goods = (row[0].get("before") or {}).get("goods") or {}
        check("雛形: goods を写す", goods.get("goods") == ["乾いた砂の糧食",
                                                          "砂漠のハーブティー"], goods)
        check("雛形: stock_update_date と今日を写す",
              goods.get("stock_update_date") == 404 and goods.get("day") == 404,
              goods)
        check("雛形: 主を写す", row[0]["before"]["shop_who"] == "ハルマン(118)",
              row[0]["before"]["shop_who"])

    # ---- 同じ鍵のまま中身が入れ替わった場合 ----------------------------
    ctx = fresh(module)
    tea = Item("item_47", "砂漠のハーブティー", {"回復": 100})
    app, owner, player = stage(shop_stock=(tea,))

    def swap(self, *args, **kwargs):
        owner.inventory["item_47"] = Item("item_47", "別物", {})
        return None

    ctx.hooks[EXECUTE](swap, ShoppingStartManagerRemake(app), "売買する")
    check("入れ替え: 同じ鍵の差し替えを書く",
          "!item_47=砂漠のハーブティー->別物" in read(ctx), read(ctx))

    # ---- 品の誕生 -------------------------------------------------------
    ctx = fresh(module)
    app, owner, player = stage()
    born = Item.__new__(Item)

    def init(self, *args, **kwargs):
        Item.__init__(self, "51", "乾いた砂の糧食",
                      {"回復": 269, "疲労負荷": 9}, owner)
        return None

    ctx.hooks[ITEM_INIT](init, born)
    log = read(ctx)
    check("誕生: id と名前を書く", "id='51'" in log and "乾いた砂の糧食" in log, log)
    check("誕生: 持ち主を書く", "主=ハルマン(118)" in log, log)
    check("誕生: 素の attributes を書く", "'疲労負荷': 9" in log, log)
    check("誕生: 呼び出し元を書く", "呼び出し元:" in log, log)

    # ---- 枠（店の外は ITEM_SAMPLES で止まる）---------------------------
    ctx = fresh(module, ITEM_SAMPLES=2)
    app, owner, player = stage()
    for n in range(5):
        one = Item.__new__(Item)
        ctx.hooks[ITEM_INIT](
            lambda self, n=n: Item.__init__(self, str(60 + n), "量産品", {}, owner),
            one)
    check("枠: 店の外は ITEM_SAMPLES で止まる",
          read(ctx).count("品の誕生") == 2, read(ctx).count("品の誕生"))

    # ---- 主も施設も引けない場面 ----------------------------------------
    ctx = fresh(module)
    empty = InstantaleApp(World({}, {}, 0), Character("0", "ミツバ"), {})
    empty.player.location = None
    out = ctx.hooks[EXECUTE](lambda self, *a, **k: "無事",
                             ShoppingStartManagerRemake(empty), "売買する")
    check("安全: 施設が無くても素通しする", out == "無事", out)
    check("安全: 例外を出していない", not ctx.errors, ctx.errors)

    # 設定を既定へ戻してから終える。
    fresh(module, ITEM_SAMPLES=200)

    print("")
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
