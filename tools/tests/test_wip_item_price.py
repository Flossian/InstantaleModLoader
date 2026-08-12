# -*- coding: utf-8 -*-
"""314_balance_item_price をゲーム抜きで通す。

    python tools/tests/test_item_price.py

偽の app / Character / Item を差し込み、次を確認する。

  軸       … 能力値を持つ品は能力値から、持たない品は value から値が付く
  レア度   … レア度が上がるほど高い（素のゲームはここが効いていない）
  買取     … 売価は買価の SELL_RATE 倍
  鍵       … 既にある鍵だけを書き換える。買価しか無い品に売価を足さない。
             値段の鍵を持たない品には触らない
  水準     … 決めた値付けの目安から外れていないこと（回帰）
  設定     … 全体倍率・買取率・種別倍率・強化・スキル・上下限が効く
  読み取り … `Item` インスタンスでもセーブの辞書でも同じ値になる
  画面     … 売買画面を開いた時点で、古いセーブの品も付け直される
  決済     … 表示と違う額で決済されたら差を直す。成立していない取引には
             触らない。所持金は負にしない
  再適用   … 何度当て直しても値段が動かない（べき等）
"""
import importlib.util
import io
import json
import os
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


MOD_DIR, MOD = find_mod("_balance_item_price")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Item:
    """`scripts.items.Item` の、値段に関わるところだけ。"""

    def __init__(self, name, item_type, attributes, value, rarity,
                 skill=None, upgrade_level=0):
        self.name = name
        self.item_type = item_type
        self.attributes = dict(attributes)
        self.value = value
        self.rarity = rarity
        self.skill = skill
        self.upgrade_level = upgrade_level


class InventoryItem:
    """所持品グリッドの1マス（説明欄はここから `item_instance` を引く）。"""

    def __init__(self, item_instance):
        self.item_instance = item_instance


class Character:
    def __init__(self, name, inventory=None, gold=0):
        self.name = name
        self.inventory = dict(inventory or {})
        self.gold = gold


class InstantaleApp:
    def __init__(self, player):
        self.player = player


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

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

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


def load_mod(path=MOD, name="balance_item_price_mod"):
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


def fresh_mod(**settings):
    """mod を読み直して当て直す（設定は既定に戻してから上書きする）。"""
    sys.modules.pop("balance_item_price_mod", None)
    module = load_mod()
    for key, value in settings.items():
        if not hasattr(module, key):
            raise SystemExit("設定 {!r} がモジュールに無い".format(key))
        setattr(module, key, value)
    if hasattr(sys, module.STORE_ATTR):
        delattr(sys, module.STORE_ATTR)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


# ---------------------------------------------------------------- 呼び出し
def buy_price(ctx, item):
    """店の品として値付けさせて、`買価` を返す。"""
    hook = ctx.hooks["__main__:InstantaleApp.set_shop_price_for_owner"]
    hook(lambda _self, _item, *a, **k: None, None, item)
    return item.attributes.get("買価")


def sell_price(ctx, item):
    """プレイヤーの品として値付けさせて、`売価` を返す。"""
    hook = ctx.hooks["__main__:InstantaleApp.set_shop_price_for_player"]
    hook(lambda _self, _item, *a, **k: None, None, item)
    return item.attributes.get("売価")


def weapon(atk, rarity="common", value=20, detail="small_weapon", **kw):
    return Item("剣", "weapon",
                {"item_detail": detail, "攻撃力": atk, "買価": 0}, value, rarity, **kw)


def armour(defence, rarity="common", value=20, detail="body_armor"):
    return Item("鎧", "wearable",
                {"item_detail": detail, "防御力": defence, "買価": 0}, value, rarity)


def herb(heal, rarity="common", value=3, detail="plant"):
    return Item("薬草", "healing_item",
                {"item_detail": detail, "回復": heal, "疲労負荷": 10, "買価": 0},
                value, rarity)


def material(value, rarity="common", detail="magical_material", key="買価"):
    return Item("素材", "material", {"item_detail": detail, key: 0}, value, rarity)


def main():
    print("== 314_balance_item_price ==")

    # -- 軸：能力値がある品は能力値から、無い品は value から ----------------
    module, ctx = fresh_mod()
    weak, strong = weapon(23, value=3), weapon(96, value=3)
    check("同じ value でも攻撃力が高いほうが高い",
          buy_price(ctx, strong) > buy_price(ctx, weak),
          (buy_price(ctx, weak), buy_price(ctx, strong)))

    low, high = material(4), material(24)
    check("能力値の無い品は value で伸びる",
          buy_price(ctx, high) > buy_price(ctx, low),
          (buy_price(ctx, low), buy_price(ctx, high)))

    # 能力値が 0 や欠落でも値段は付く（value に落ちる）。
    broken = Item("刃の折れた剣", "weapon",
                  {"item_detail": "small_weapon", "攻撃力": 0, "買価": 0},
                  20, "common")
    check("能力値が 0 なら value から付ける", buy_price(ctx, broken) > 0,
          broken.attributes)

    nameless = Item("謎の品", "weapon", {"item_detail": "small_weapon", "買価": 0},
                    None, "common")
    check("value も能力値も無ければ値段に触らない",
          nameless.attributes["買価"] == 0, nameless.attributes)

    unknown = Item("見たことのない品", "material",
                   {"item_detail": "brand_new_thing", "買価": 0}, 20, "common")
    check("表に無い細分でも値段が付く（既定の率に落ちる）",
          buy_price(ctx, unknown) > 0, unknown.attributes)

    # -- レア度 --------------------------------------------------------------
    module, ctx = fresh_mod()
    ladder = [buy_price(ctx, weapon(96, rarity=r))
              for r in ("common", "rare", "magical", "epic", "legendary", "mythic")]
    check("レア度が上がるほど高い", ladder == sorted(ladder) and len(set(ladder)) == 6,
          ladder)
    check("mythic は common の7倍（既定）",
          abs(ladder[5] / float(ladder[0]) - 7.0) < 0.05, ladder)

    # -- 買取率 --------------------------------------------------------------
    module, ctx = fresh_mod()
    shop_side = buy_price(ctx, material(24, "magical"))
    player_side = sell_price(ctx, material(24, "magical", key="売価"))
    check("売価は買価の4割（既定）",
          abs(player_side / float(shop_side) - 0.4) < 0.02,
          (shop_side, player_side))

    module, ctx = fresh_mod(SELL_RATE=0.6)
    shop_side = buy_price(ctx, material(24, "magical"))
    player_side = sell_price(ctx, material(24, "magical", key="売価"))
    check("買取率の設定が効く",
          abs(player_side / float(shop_side) - 0.6) < 0.02,
          (shop_side, player_side))

    # -- 鍵を新設しない ------------------------------------------------------
    module, ctx = fresh_mod()
    only_buy = material(24)
    buy_price(ctx, only_buy)
    check("買価しか無い品に売価を足さない", "売価" not in only_buy.attributes,
          only_buy.attributes)

    only_sell = material(24, key="売価")
    sell_price(ctx, only_sell)
    check("売価しか無い品に買価を足さない", "買価" not in only_sell.attributes,
          only_sell.attributes)

    quest_item = Item("依頼の証文", "utility", {"item_detail": "document"},
                      20, "common")
    before = dict(quest_item.attributes)
    buy_price(ctx, quest_item)
    check("値段の鍵を持たない品には触らない", quest_item.attributes == before,
          quest_item.attributes)

    # -- 水準（決めた目安からの回帰）----------------------------------------
    # 目安を外れたら、表を直したのか事故なのかをここで気付けるようにする。
    module, ctx = fresh_mod()
    targets = [
        ("短剣 common v3 atk23", buy_price(ctx, weapon(23, value=3)), 350),
        ("短剣 common v20 atk96", buy_price(ctx, weapon(96, value=20)), 3000),
        ("短剣 magical v48 atk245",
         buy_price(ctx, weapon(245, "magical", value=48)), 20000),
        ("薬草 common v3 回復8", buy_price(ctx, herb(8, value=3)), 60),
        ("薬 magical v48 回復198",
         buy_price(ctx, herb(198, "magical", value=48, detail="potion")), 2500),
        ("魔法素材 magical v24 買", buy_price(ctx, material(24, "magical")), 800),
        ("魔法素材 magical v24 売",
         sell_price(ctx, material(24, "magical", key="売価")), 320),
        ("財宝 mythic v66 売",
         sell_price(ctx, material(66, "mythic", "treasure", key="売価")), 15000),
    ]
    for label, got, want in targets:
        check("目安の3割以内: {}（目安 {:,}）".format(label, want),
              0.7 <= got / float(want) <= 1.3, "実際 {}".format(got))

    # 素のゲームより安くなる品があってはいけない（この mod の目的は値上げ）。
    vanilla = [
        ("短剣 common v3 atk23", buy_price(ctx, weapon(23, value=3)), 72),
        ("革鎧 common v3 防20", buy_price(ctx, armour(20, value=3)), 65),
        ("薬草 common v3 回復8", buy_price(ctx, herb(8, value=3)), 14),
        ("がらくた common v3", buy_price(ctx, material(3, detail="scrap")), 14),
        ("文書 common v4", buy_price(ctx, material(4, detail="document")), 18),
        ("魔法素材 common v1", buy_price(ctx, material(1)), 3),
    ]
    for label, got, was in vanilla:
        check("素のゲームより安くならない: {}（素 {}）".format(label, was),
              got >= was, "実際 {}".format(got))

    # -- 設定 ---------------------------------------------------------------
    module, ctx = fresh_mod()
    plain = buy_price(ctx, weapon(96))
    module, ctx = fresh_mod(PRICE_SCALE=2.0)
    check("全体倍率が効く", abs(buy_price(ctx, weapon(96)) / float(plain) - 2.0) < 0.02,
          buy_price(ctx, weapon(96)))

    module, ctx = fresh_mod(MULT_WEAPON=0.5)
    scaled = buy_price(ctx, weapon(96))
    same = buy_price(ctx, armour(96))
    module, ctx = fresh_mod()
    check("種別の倍率は、その種別にだけ効く",
          abs(scaled / float(plain) - 0.5) < 0.02 and same == buy_price(ctx, armour(96)),
          (scaled, plain, same))

    module, ctx = fresh_mod()
    check("強化した装備は高い",
          buy_price(ctx, weapon(96, upgrade_level=3)) > buy_price(ctx, weapon(96)),
          buy_price(ctx, weapon(96, upgrade_level=3)))
    check("スキル付きは高い",
          buy_price(ctx, weapon(96, skill="斬撃")) > buy_price(ctx, weapon(96)),
          buy_price(ctx, weapon(96, skill="斬撃")))

    module, ctx = fresh_mod(MIN_PRICE=50, MAX_PRICE=100)
    check("下限が効く", buy_price(ctx, material(1)) >= 50, buy_price(ctx, material(1)))
    check("上限が効く", buy_price(ctx, weapon(500, "mythic")) <= 100,
          buy_price(ctx, weapon(500, "mythic")))

    # -- 読み取り（インスタンスとセーブの辞書）------------------------------
    module, ctx = fresh_mod()
    instance = weapon(96, "magical", value=20)
    as_dict = {"name": "剣", "item_type": "weapon", "value": 20, "rarity": "magical",
               "skill": None, "upgrade_level": 0,
               "attributes": {"item_detail": "small_weapon", "攻撃力": 96, "買価": 0}}
    hook = ctx.hooks["__main__:InstantaleApp.set_shop_price_for_owner"]
    hook(lambda _self, _item, *a, **k: None, None, as_dict)
    check("セーブの辞書でもインスタンスでも同じ値段",
          buy_price(ctx, instance) == as_dict["attributes"]["買価"],
          (instance.attributes, as_dict["attributes"]))

    # -- 画面（古いセーブの品を開いたとき）----------------------------------
    module, ctx = fresh_mod()
    old = weapon(96, "mythic")
    old.attributes["買価"] = 468              # 素のゲームが付けた値段
    shop = Character("店主", {"item_1": old})
    player = Character("勇者", {})
    window = ctx.hooks["__main__:InstantaleApp.toggle_twin_inventory_window"]
    window(lambda *a, **k: "opened", InstantaleApp(player), shop, player, "店", "shop")
    check("売買画面を開くと、古い品も付け直される",
          old.attributes["買価"] > 468, old.attributes)

    detail = ctx.hooks["scripts.hud.new_hud:ItemDetailBox.update_content"]
    hovered = weapon(96, "mythic")
    hovered.attributes["買価"] = 468
    detail(lambda _self, _item, *a, **k: None, None, InventoryItem(hovered))
    check("説明欄に出した品も付け直される", hovered.attributes["買価"] > 468,
          hovered.attributes)

    module, ctx = fresh_mod(REPRICE_ON_SIGHT=False)
    check("切れば画面の経路は当たらない",
          "__main__:InstantaleApp.toggle_twin_inventory_window" not in ctx.hooks,
          sorted(ctx.hooks))

    # -- 決済 ---------------------------------------------------------------
    module, ctx = fresh_mod()
    buy_hook = ctx.hooks["__main__:InstantaleApp.buy_item"]
    sell_hook = ctx.hooks["__main__:InstantaleApp.sell_item"]

    def game_buy(app, item, paid):
        """ゲーム側の決済（表示と違う額を引く版）。"""
        def orig(_self, _item, *a, **k):
            app.player.gold -= paid
            return "bought"
        return buy_hook(orig, app, item)

    def game_sell(app, item, paid):
        def orig(_self, _item, *a, **k):
            app.player.gold += paid
            return "sold"
        return sell_hook(orig, app, item)

    shown = weapon(96)
    app = InstantaleApp(Character("勇者", gold=100000))
    buy_price(ctx, shown)
    game_buy(app, shown, paid=468)            # 素の値段で決済してしまった
    check("買値のずれを直す（表示どおり払う）",
          app.player.gold == 100000 - shown.attributes["買価"],
          (app.player.gold, shown.attributes["買価"]))

    shown = material(24, "mythic", key="売価")
    app = InstantaleApp(Character("勇者", gold=0))
    sell_price(ctx, shown)
    game_sell(app, shown, paid=20)
    check("売値のずれを直す（表示どおり受け取る）",
          app.player.gold == shown.attributes["売価"],
          (app.player.gold, shown.attributes["売価"]))

    shown = weapon(96)
    app = InstantaleApp(Character("勇者", gold=100000))
    buy_price(ctx, shown)
    game_buy(app, shown, paid=shown.attributes["買価"])
    check("表示どおりに決済されていれば何もしない",
          app.player.gold == 100000 - shown.attributes["買価"], app.player.gold)

    shown = weapon(96)
    app = InstantaleApp(Character("勇者", gold=50))
    buy_price(ctx, shown)
    game_buy(app, shown, paid=0)              # 買えなかった（所持金が動かない）
    check("成立していない取引には触らない", app.player.gold == 50, app.player.gold)

    shown = weapon(245, "mythic")
    app = InstantaleApp(Character("勇者", gold=100))
    buy_price(ctx, shown)
    game_buy(app, shown, paid=50)             # 素の値段なら買えてしまう高額品
    check("所持金は負にしない", app.player.gold == 0, app.player.gold)

    module, ctx = fresh_mod(RECONCILE_GOLD=False)
    check("切れば決済の経路は当たらない",
          "__main__:InstantaleApp.buy_item" not in ctx.hooks, sorted(ctx.hooks))

    # -- べき等 -------------------------------------------------------------
    module, ctx = fresh_mod()
    item = weapon(96, "magical")
    first = buy_price(ctx, item)
    for _ in range(5):
        buy_price(ctx, item)
    check("何度通しても値段が動かない", item.attributes["買価"] == first,
          (first, item.attributes["買価"]))

    module, ctx = fresh_mod()
    again = buy_price(ctx, weapon(96, "magical"))
    check("当て直しても同じ値段", again == first, (first, again))

    check("例外を握り潰していない", not ctx.errors, ctx.errors)

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
