# -*- coding: utf-8 -*-
"""312_shop_restock をゲーム抜きで通す。

    python tools/tests/test_shop_restock.py

偽の app / World / Facility / Character / ShoppingStartManagerRemake /
Clock を差し込み、次を確認する。

  初回     … 初めて開いた店は入れ替えない。その日を基準として控えるだけ
  未到来   … 日数が足りなければ持ち物に触らない
  入替     … 日数が経った店は、ゲームの生成が走る前に空になる。プレイヤーが
             売った品は残らず、控えの日が今日に進む
  巻戻し   … 古いセーブで日付が戻ったら、控えを付け直すだけで空にしない
  逃げ道   … 空にしても補充されない作りなら、控えを戻して以後は空にしない
  直呼び   … 段(tier)を見たことがあれば set_item_from_world_data を自分で呼ぶ
  控え     … `state/shop_restock/<世界名>.json` に鍵の並びのまま書かれる。
             世界が違えば混ざらない
  安全     … 主が引けない・日数が読めない場面では何もしない（品物は無事）
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state_shop_restock")

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


MOD_DIR, MOD = find_mod("_shop_restock")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Facility:
    def __init__(self, facility_id, facility_type, owner):
        self.id = facility_id
        self.name = "テスト" + facility_type
        self.facility_type = facility_type
        self.owner = owner


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, facilities):
        self.id = area_id
        self.nodes = {"0": Node(facilities)}


class Character:
    """主もプレイヤーも同じ形（実ゲームと同じく `Character` 1種）。"""

    def __init__(self, character_id, name, inventory=None):
        self.id = character_id
        self.name = name
        self.inventory = dict(inventory or {})


class World:
    def __init__(self, name, areas, characters, days_elapsed):
        self.name = name
        self.areas = areas
        self.characters = characters
        self.days_elapsed = days_elapsed


class Player:
    def __init__(self, area, location):
        self.name = "テストプレイヤー"
        self.current_area = area
        self.location = location
        self.inventory = {}


class InstantaleApp:
    def __init__(self, world, player):
        self.world = world
        self.player = player


class ShoppingStartManagerRemake:
    """売買の入口。**品揃えの生成は「持ち物が空なら作る」**（素のゲームの想定）。

    `refills=False` で「空にしても作らない作り」を演じる（逃げ道の検査）。
    `defer=True` で生成を次のフレームへ回す（Clock 経由の版）。
    """

    counter = [0]

    def __init__(self, app, refills=True, defer=False, tier=2, tops_up=False):
        self.app = app
        self.refills = refills
        self.defer = defer
        self.tier = tier
        self.tops_up = tops_up
        self.generated = []
        self.topped_up = []

    def execute(self, choice_text):
        location = self.app.player.location
        if isinstance(location, str):
            location = self.app.world.areas["0"].nodes["0"].facilities.get(location)
        owner = self.app.world.characters.get(getattr(location, "owner", None))
        if owner is not None and not owner.inventory and self.refills:
            if self.defer:
                CLOCK.schedule_once(
                    lambda dt: self.set_item_from_world_data(owner, self.tier), 0)
            else:
                self.set_item_from_world_data(owner, self.tier)
        elif owner is not None and owner.inventory and self.tops_up:
            # 素のゲームの作り直し（実測。VERIFICATION.md §3.52）。
            # 開くたびに雛形から1つ作って棚へ入れる。鍵は `item_` の付かない裸の数字。
            self.counter[0] += 1
            key = str(50 + self.counter[0])
            owner.inventory[key] = {"name": "作り直された薬"}
            self.topped_up.append(key)
        return "shopping"

    def set_item_from_world_data(self, shop_owner_instance, next_tier):
        """ゲーム自身の生成。呼ばれた回数だけ新しい品物を入れる。"""
        self.generated.append((getattr(shop_owner_instance, "id", None), next_tier))
        for _ in range(3):
            self.counter[0] += 1
            item_id = "item_{}".format(1000 + self.counter[0])
            shop_owner_instance.inventory[item_id] = {
                "name": "生成品" + str(self.counter[0]), "value": 3}
        return None


class FakeClock:
    def __init__(self):
        self.onces = []

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def run_onces(self):
        for _ in range(8):
            pending, self.onces = self.onces, []
            if not pending:
                return
            for callback in pending:
                callback(0.0)


CLOCK = FakeClock()


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = CLOCK
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    sys.modules.pop("kivy.app", None)


class FakeCtx:
    def __init__(self, out_dir, state_dir):
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1]
                    else path, exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    # 本物の `ctx.write_json` / `write_text` と同じものを使う。
    # ここを自前の open(..., "w") にすると、
    # テストだけが「壊れない書き方」を通らなくなる。
    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="shop_restock_mod"):
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
SHOP_ID = "30"
OWNER_ID = "16"


def make_world(days, stock, world_name="テスト世界", location_as_id=False):
    facility = Facility(SHOP_ID, "general_store", OWNER_ID)
    owner = Character(OWNER_ID, "欲深きバルト", stock)
    area = Area("0", [facility])
    world = World(world_name, {"0": area}, {OWNER_ID: owner}, days)
    player = Player("0", SHOP_ID if location_as_id else facility)
    app = InstantaleApp(world, player)
    return app, owner, facility


def fresh_mod(restock_days=30, first_visit=False, keep_state=False,
              keep_sold_out=True):
    """mod を読み直して当て直す。**世代をまたぐ控えは毎回捨てる。**

    ログも捨てる（`ctx.logger` は追記なので、
    残すと前の場面の行を今の場面の記録として読んでしまう）。
    """
    sys.modules.pop("shop_restock_mod", None)
    module = load_mod()
    if not keep_state:
        for attr in list(vars(sys)):
            if attr == module.STORE_ATTR:
                delattr(sys, attr)
    module.RESTOCK_DAYS = restock_days
    module.RESTOCK_ON_FIRST_SHOP = first_visit
    module.KEEP_SOLD_OUT = keep_sold_out
    log_path = os.path.join(OUT_DIR, module.LOG_BASENAME)
    if os.path.exists(log_path):
        os.remove(log_path)
    ctx = FakeCtx(OUT_DIR, STATE_DIR)
    module.apply(ctx)
    return module, ctx


def read_log(module):
    path = os.path.join(OUT_DIR, module.LOG_BASENAME)
    if not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def shop(ctx, manager, choice_text="商品を見せてもらう"):
    """売買を1回開く（mod のフックを通した経路）。"""
    hook = ctx.hooks["__main__:ShoppingStartManagerRemake.execute"]
    tier_hook = ctx.hooks.get(
        "__main__:ShoppingStartManagerRemake.set_item_from_world_data")
    if tier_hook is not None and not getattr(manager, "_tier_hooked", False):
        original = manager.set_item_from_world_data

        def wrapped(owner, tier, *args, **kwargs):
            return tier_hook(lambda _self, o, t, *a, **k: original(o, t),
                             manager, owner, tier, *args, **kwargs)

        manager.set_item_from_world_data = wrapped
        manager._tier_hooked = True
    result = hook(lambda _self, text: ShoppingStartManagerRemake.execute(manager, text),
                  manager, choice_text)
    CLOCK.run_onces()      # 生成を Clock へ回す版のため
    CLOCK.run_onces()      # 補充の確認（VERIFY_DELAY のコールバック）
    return result


def state_file(world_name="テスト世界"):
    path = os.path.join(STATE_DIR, "shop_restock", world_name + ".json")
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def reset_state():
    shutil.rmtree(os.path.join(STATE_DIR, "shop_restock"), ignore_errors=True)


# ---------------------------------------------------------------- 検査
def main():
    install_fake_kivy()
    sys.modules["__main__"].InstantaleApp = InstantaleApp
    os.makedirs(OUT_DIR, exist_ok=True)

    # -- 初回の来店 ------------------------------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={"item_1": {"name": "元の品"}})
    manager = ShoppingStartManagerRemake(app)
    shop(ctx, manager)
    check("初回: 素の品揃えを入れ替えない",
          list(owner.inventory) == ["item_1"], owner.inventory)
    record = (state_file() or {}).get(OWNER_ID)
    check("初回: 基準の日を控える", record is not None and record["day"] == 100, record)
    check("初回: 控えの鍵はこの並び",
          record is not None and list(record) == list(module.RECORD_KEYS), record)

    # -- 日数が足りない --------------------------------------------------
    app.world.days_elapsed = 129
    owner.inventory["item_sold"] = {"name": "プレイヤーが売った品"}
    shop(ctx, manager)
    check("未到来: 持ち物に触らない",
          sorted(owner.inventory) == ["item_1", "item_sold"], owner.inventory)
    check("未到来: 控えの日も動かない",
          (state_file() or {}).get(OWNER_ID, {}).get("day") == 100, state_file())

    # -- 日数が経った ----------------------------------------------------
    app.world.days_elapsed = 130
    shop(ctx, manager)
    check("入替: 元の品もプレイヤーが売った品も残らない",
          "item_1" not in owner.inventory and "item_sold" not in owner.inventory,
          owner.inventory)
    check("入替: ゲーム自身の生成で品物が入っている",
          len(owner.inventory) == 3, owner.inventory)
    check("入替: 生成はゲームの経路（set_item_from_world_data）を通った",
          manager.generated and manager.generated[-1][0] == OWNER_ID,
          manager.generated)
    check("入替: 控えの日が今日に進む",
          (state_file() or {}).get(OWNER_ID, {}).get("day") == 130, state_file())
    check("入替: 空にした時点の件数ではなく入替後の件数を控える",
          (state_file() or {}).get(OWNER_ID, {}).get("count") == 3, state_file())
    check("入替: 段(tier)も控える",
          (state_file() or {}).get(OWNER_ID, {}).get("tier") == 2, state_file())

    # -- 生成が次のフレームへ回る版 --------------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=200, stock={"item_1": {"name": "元の品"}})
    manager = ShoppingStartManagerRemake(app, defer=True)
    shop(ctx, manager)                      # 初回（基準）
    app.world.days_elapsed = 260
    shop(ctx, manager)
    check("遅れて生成する版でも入れ替わる", len(owner.inventory) == 3, owner.inventory)

    # -- 日付の巻き戻し --------------------------------------------------
    app.world.days_elapsed = 10
    before = dict(owner.inventory)
    shop(ctx, manager)
    check("巻戻し: 品物に触らない", owner.inventory == before, owner.inventory)
    check("巻戻し: 控えをその日に付け直す",
          (state_file() or {}).get(OWNER_ID, {}).get("day") == 10, state_file())

    # -- 空にしても補充しない作り ----------------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=300, stock={"item_1": {"name": "元の品"},
                                                       "item_2": {"name": "元の品2"}})
    manager = ShoppingStartManagerRemake(app, refills=False)
    shop(ctx, manager)                      # 初回（基準）
    app.world.days_elapsed = 400
    shop(ctx, manager)
    check("逃げ道: 補充されなければ控えを戻す",
          sorted(owner.inventory) == ["item_1", "item_2"], owner.inventory)
    check("逃げ道: 控えの日は進めない",
          (state_file() or {}).get(OWNER_ID, {}).get("day") == 300, state_file())
    warned = [msg for level, msg in ctx.logs if level == "WARN"]
    check("逃げ道: 警告を残す", bool(warned), ctx.logs)
    app.world.days_elapsed = 500
    before = dict(owner.inventory)
    shop(ctx, manager)
    check("逃げ道: 二度と空にしない", owner.inventory == before, owner.inventory)

    # -- 段(tier)を見ていれば自分で呼ぶ ----------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=300, stock={"item_1": {"name": "元の品"}})
    manager = ShoppingStartManagerRemake(app)
    shop(ctx, manager)                      # 初回（基準。生成は走らない）
    manager.set_item_from_world_data(owner, 5)   # ゲームが段を渡す場面を1度通す
    CLOCK.run_onces()
    manager.refills = False                 # 以後、空にしても自分では作らない
    app.world.days_elapsed = 400
    shop(ctx, manager)
    check("直呼び: 段を覚えていれば自分で生成する",
          len(owner.inventory) == 3 and manager.generated[-1][1] == 5,
          (owner.inventory, manager.generated))
    check("直呼び: 控えの日も進む",
          (state_file() or {}).get(OWNER_ID, {}).get("day") == 400, state_file())

    # -- 買った品の作り直しを止める（KEEP_SOLD_OUT）----------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={"item_1": {"name": "薬"}})
    manager = ShoppingStartManagerRemake(app, tops_up=True)
    shop(ctx, manager)
    check("補充止め: ゲームが作り直したぶんを外す",
          list(owner.inventory) == ["item_1"], owner.inventory)
    check("補充止め: ゲーム自身は作っている（止めたのはこちら）",
          manager.topped_up, manager.topped_up)
    check("補充止め: 記録に残る", "kept sold out" in read_log(module),
          read_log(module))
    shop(ctx, manager)
    check("補充止め: 何度開いても増えない",
          list(owner.inventory) == ["item_1"], owner.inventory)

    # 切れば素のゲームのまま。
    module, ctx = fresh_mod(keep_sold_out=False)
    shop(ctx, manager)
    check("補充止め: 切ると素のまま増える",
          len(owner.inventory) == 2, owner.inventory)
    check("補充止め: 切ったときは記録も出ない",
          "kept sold out" not in read_log(module), read_log(module))

    # -- 止めても初回の品揃えは作らせる ----------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={})
    manager = ShoppingStartManagerRemake(app)
    shop(ctx, manager)
    check("初回の空の店: 品揃えを作らせる", len(owner.inventory) == 3,
          owner.inventory)
    check("初回の空の店: 外した記録は出ない",
          "kept sold out" not in read_log(module), read_log(module))

    # -- 一度開いた店を買い占めたら空のまま ------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={"item_1": {"name": "薬"}})
    manager = ShoppingStartManagerRemake(app)
    shop(ctx, manager)                      # 初回（控えを作る）
    owner.inventory.clear()                 # 買い占めた
    shop(ctx, manager)
    check("買い占め: 品揃えは戻らない", owner.inventory == {}, owner.inventory)
    app.world.days_elapsed = 200
    shop(ctx, manager)
    check("買い占め: 入れ替えの日には作り直す（止めない）",
          len(owner.inventory) == 3, owner.inventory)

    # -- 世界が違えば混ざらない ------------------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app_a, owner_a, _ = make_world(days=100, stock={"item_1": {}},
                                   world_name="世界A")
    app_b, owner_b, _ = make_world(days=100, stock={"item_1": {}},
                                   world_name="世界B")
    shop(ctx, ShoppingStartManagerRemake(app_a))
    shop(ctx, ShoppingStartManagerRemake(app_b))
    check("控え: 世界ごとに別のファイル",
          state_file("世界A") is not None and state_file("世界B") is not None,
          os.listdir(os.path.join(STATE_DIR, "shop_restock")))

    # -- 主が引けない / 日数が読めない -----------------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={"item_1": {}})
    facility.owner = None
    shop(ctx, ShoppingStartManagerRemake(app))
    check("安全: 主が引けなければ何もしない",
          list(owner.inventory) == ["item_1"] and state_file() is None,
          owner.inventory)

    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={"item_1": {}})
    app.world.days_elapsed = None
    shop(ctx, ShoppingStartManagerRemake(app))
    check("安全: 日数が読めなければ何もしない",
          list(owner.inventory) == ["item_1"] and state_file() is None,
          owner.inventory)

    # -- ロード直後（location が施設 id の文字列）------------------------
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={"item_1": {}},
                                      location_as_id=True)
    manager = ShoppingStartManagerRemake(app)
    shop(ctx, manager)
    app.world.days_elapsed = 200
    shop(ctx, manager)
    check("ロード直後: 施設 id の文字列からでも主を引ける",
          len(owner.inventory) == 3 and "item_1" not in owner.inventory,
          owner.inventory)

    check("例外を握り潰していない", not ctx.errors, ctx.errors)

    # -- Clock が使えない（予約が取れない）------------------------------
    # 予約できないまま抜けると、空にした店の控えが更新されず毎回まっさらになる。
    # その場で決着を付けること。
    reset_state()
    module, ctx = fresh_mod()
    app, owner, facility = make_world(days=100, stock={"item_1": {}})
    manager = ShoppingStartManagerRemake(app)
    shop(ctx, manager)                      # 初回（基準）
    app.world.days_elapsed = 200
    saved_clock = sys.modules.pop("kivy.clock")
    try:
        shop(ctx, manager)
    finally:
        sys.modules["kivy.clock"] = saved_clock
    check("Clock が無くても決着を付ける",
          len(owner.inventory) == 3
          and (state_file() or {}).get(OWNER_ID, {}).get("day") == 200,
          (owner.inventory, state_file()))

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
