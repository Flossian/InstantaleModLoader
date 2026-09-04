# -*- coding: utf-8 -*-
"""134_balance_item_effects をゲーム抜きで通す。

    python tools/tests/test_item_effects.py

偽の app / Character / Item を差し込み、次を確認する。

  作り直し … 細分ごとに正しい鍵が書かれ、`疲労負荷` は 0 で残り、並びは
             item_detail → 効果 → 疲労負荷 → 値段。同じ辞書が中で組み直される
  量       … スタミナは価値段階・レア度・飲み物の割合 × ±10%（生成時に確定）。
             HP は最大 HP の割合（(20 + 価値段階 × 0.3)% × レア度 × ±10%）で品に書かれ、
             使う人の最大 HP から量に直る。ポーションはその 1/4
  薬草     … 増減は生成時に書かれ、向きと大きさが設定どおりの幅に入る。
             2度目の作り直しで引き直さない（べき等）
  触らない … 巻物・細分の無い品・武器には触らない
  使う     … 本体は `usable=True` と `回復` 0 で1回呼ばれ、戻した後に効果が乗る。
             上限で切る・HP は 1 を下回らない・状態異常が消える・文が差し替わる。
             版2 までの品（`回復` が数）は数のまま効く
  拒否     … 本体が消費しなかった（持ち物が減らない）ら効果を乗せない
  説明欄   … 描く間だけ `疲労負荷` が消え、描いた後に並びごと戻る
  文言     … 英語のときは英語の鍵と文
"""
import importlib.util
import io
import json
import os
import random
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
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


MOD_DIR, MOD = find_mod("_balance_item_effects")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def pct_of(text):
    """`"25%"` → 25.0。割合でなければ None。"""
    if isinstance(text, str) and text.endswith("%"):
        return float(text[:-1])
    return None


# ---------------------------------------------------------------- 偽ゲーム
class Character(object):
    def __init__(self, name, hp=100, max_hp=120, stamina=5, cap=10, items=3):
        self.name = name
        self.current_hp = hp
        self.max_hp = max_hp
        self.physical_integrity = stamina
        self.max_physical_integrity = cap
        self.status = {}
        self.inventory = {str(i): object() for i in range(items)}


class Item(object):
    def __init__(self, name, detail, value=3, rarity="common", heal=9, burden=10,
                 item_type="healing_item", obtainer=None, price=("買価", 50)):
        self.id = "7"
        self.name = name
        self.item_type = item_type
        self.value = value
        self.rarity = rarity
        self.attributes = {"item_detail": detail}
        if heal is not None:
            self.attributes["回復"] = heal
        if burden is not None:
            self.attributes["疲労負荷"] = burden
        if price is not None:
            self.attributes[price[0]] = price[1]
        self.obtainer = obtainer


class App(object):
    def __init__(self, player):
        self.player = player
        self.texts = []
        self.ui_updates = 0

    def add_text(self, context):
        self.texts.append(context)

    def update_ui(self, *args):
        self.ui_updates += 1


class Manager(object):
    def __init__(self, app):
        self.app = app


class FakeUI(object):
    def __init__(self, app):
        self.app = app

    def find_app(self):
        return self.app

    def scheduler(self, ctx, tag="mod"):
        def schedule(fn, delay=0.0):
            fn()
        return schedule


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = "134_balance_item_effects"

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def resolve(self, target):
        raise LookupError(target)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="item_effects_mod"):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(app, lang="ja"):
    if hasattr(sys, "_instantale_item_effects_store"):
        delattr(sys, "_instantale_item_effects_store")
    sys.modules["scripts.languages"] = types.SimpleNamespace(language=lang)
    module = load_mod()
    module.ui = FakeUI(app)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    hook = ctx.hooks["__main__:InstantaleApp.add_text"]
    app.add_text = lambda context: hook(App.add_text, app, context)
    return module, ctx


def consume_via(ctx, manager, item, usable=True, consume=True):
    """本体の代わり。`回復` と `疲労負荷` を読んで動き、品を減らし、文を出す。"""
    calls = []

    def fake(self, item_instance, usable_flag):
        attrs = item_instance.attributes
        calls.append((usable_flag, attrs.get("回復"), attrs.get("疲労負荷")))
        owner = item_instance.obtainer
        if not usable_flag:
            self.app.add_text("駄目だ...体がもたない。")
            return None
        owner.current_hp += attrs.get("回復", 0)
        owner.physical_integrity -= attrs.get("疲労負荷", 0)
        if consume:
            owner.inventory.pop(next(iter(owner.inventory)))
        self.app.add_text("{}を消費した。HPを{}だけ回復した。".format(
            item_instance.name, attrs.get("回復", 0)))
        return None

    hook = ctx.hooks["__main__:ItemConsumeManager.consume_item"]
    result = hook(fake, manager, item, usable)
    return result, calls


# ---------------------------------------------------------------- 作り直し
print("[作り直し]")
player = Character("エリス", items=40)
app = App(player)
module, ctx = fresh_mod(app)

food = Item("干し肉", "food", value=70, heal=349, obtainer=player, price=("売価", 1044))
attrs = food.attributes
check("food reworked",
      module.kind_of(food) == "stamina" and module.rework(food, "t", rng=random.Random(1)))
check("same dict object", food.attributes is attrs)
check("food keys in order",
      list(food.attributes) == ["item_detail", "スタミナ回復", "疲労負荷", "売価"],
      list(food.attributes))
check("food burden is 0", food.attributes["疲労負荷"] == 0)
check("food lost 回復", "回復" not in food.attributes)
# (3 + 70*0.35) * 0.8 = 22.0 → ±10% で 20〜24
check("food stamina 70 common = 22 +-10% (20..24)",
      20 <= food.attributes["スタミナ回復"] <= 24, food.attributes["スタミナ回復"])
check("marked as reworked", module.is_reworked(food.attributes))
check("second rework is a no-op", module.rework(food, "t") is False)
lows = highs = 0
for i in range(300):
    f = Item("肉{}".format(i), "food", value=70, obtainer=player)
    module.rework(f, "t", rng=random.Random(500 + i))
    got = f.attributes["スタミナ回復"]
    if not (20 <= got <= 24):
        check("stamina variance stays within +-10%", False, got)
        break
    lows += got < 22
    highs += got > 22
else:
    check("stamina variance stays within +-10%", True)
check("stamina variance actually spreads", lows > 20 and highs > 20, (lows, highs))

drink = Item("水", "drink", value=3, obtainer=player)
module.rework(drink, "t", rng=random.Random(2))
# (3 + 1.05) * 0.8 * 0.7 = 2.27 → 2
check("drink 3 common = 2", drink.attributes["スタミナ回復"] == 2, drink.attributes)
rare_food = Item("上等な肉", "food", value=20, rarity="magical", obtainer=player)
module.rework(rare_food, "t", rng=random.Random(2))
# (3 + 7) * 1.0 = 10 → 9〜11
check("food 20 magical = 10 +-10% (9..11)",
      9 <= rare_food.attributes["スタミナ回復"] <= 11, rare_food.attributes)
mythic_food = Item("竜の肉", "food", value=70, rarity="mythic", obtainer=player)
module.rework(mythic_food, "t", rng=random.Random(2))
# 27.5 * 1.5 = 41.25 → 37〜45
check("food 70 mythic = 41 +-10% (37..45)",
      37 <= mythic_food.attributes["スタミナ回復"] <= 45, mythic_food.attributes)

# ---------------------------------------------------------------- HP は割合
print("[HP は割合]")
medicine = Item("煎じ薬", "medicine", value=75, heal=435, burden=10, obtainer=player,
                rarity="rare")
module.rework(medicine, "t", rng=random.Random(3))
check("medicine keeps 回復 in place",
      list(medicine.attributes) == ["item_detail", "回復", "疲労負荷", "買価"],
      medicine.attributes)
med_pct = pct_of(medicine.attributes["回復"])
# (20 + 75*0.3) * 0.9 = 38.25 → 34.4〜42.1
check("medicine rare: written as a percent within (34.4..42.1)",
      med_pct is not None and 34.4 <= med_pct <= 42.1, medicine.attributes)
lows = highs = 0
for i in range(300):
    m = Item("薬{}".format(i), "medicine", value=70, obtainer=player, rarity="magical")
    module.rework(m, "t", rng=random.Random(100 + i))
    got = pct_of(m.attributes["回復"])
    # (20 + 21) * 1.0 = 41 → 36.9〜45.1
    if got is None or not (36.9 <= got <= 45.1):
        check("hp variance stays within +-10%", False, m.attributes)
        break
    lows += got < 41
    highs += got > 41
else:
    check("hp variance stays within +-10%", True)
check("hp variance actually spreads", lows > 20 and highs > 20, (lows, highs))
for rarity, lo, hi in (("common", 14.8, 18.2), ("epic", 20.4, 24.9),
                       ("legendary", 23.2, 28.3), ("mythic", 27.8, 34.0)):
    m = Item("薬", "medicine", value=3, obtainer=player, rarity=rarity)
    module.rework(m, "t", rng=random.Random(5))
    got = pct_of(m.attributes["回復"])
    check("medicine value 3 rarity " + rarity, got is not None and lo <= got <= hi,
          m.attributes)

consumable_med = Item("軟膏", "medicine", value=18, heal=None, burden=None,
                      item_type="consumable", obtainer=player)
module.rework(consumable_med, "t", rng=random.Random(4))
got = pct_of(consumable_med.attributes.get("回復"))
# (20 + 5.4) * 0.8 = 20.32 → 18.3〜22.4
check("consumable medicine without 回復 still gets a percent (18.3..22.4)",
      got is not None and 18.3 <= got <= 22.4, consumable_med.attributes)

potion = Item("薬瓶", "potion", value=72, heal=408, burden=11, obtainer=player,
              rarity="magical")
module.rework(potion, "t", rng=random.Random(6))
check("potion keys",
      list(potion.attributes) == ["item_detail", "回復", "状態異常", "疲労負荷", "買価"]
      and potion.attributes["状態異常"] == "解除", potion.attributes)
got = pct_of(potion.attributes["回復"])
# (20 + 21.6) * 1.0 / 4 = 10.4 → 9.4〜11.4
check("potion heals a quarter of medicine (9.4..11.4%)",
      got is not None and 9.4 <= got <= 11.4, potion.attributes)
check("percent text has at most one decimal",
      all(len(s.split(".")[1]) <= 2 for s in
          [potion.attributes["回復"], medicine.attributes["回復"]] if "." in s))

# 割合 → 量（使う人の最大 HP が底）
big = Character("巨人", hp=100, max_hp=2000)
small = Character("小人", hp=10, max_hp=50)
pct_item = Item("薬", "medicine", value=3, obtainer=None)
pct_item.attributes.clear()
pct_item.attributes.update({"item_detail": "medicine", "回復": "25%", "疲労負荷": 0})
check("percent scales with max_hp", module.effect_of(pct_item, big) == (500, 0, False)
      and module.effect_of(pct_item, small) == (12, 0, False),
      (module.effect_of(pct_item, big), module.effect_of(pct_item, small)))
check("percent without an owner counts as 0", module.effect_of(pct_item) == (0, 0, False))
no_max = types.SimpleNamespace(current_hp=200)
check("falls back to current_hp when max_hp is missing",
      module.effect_of(pct_item, no_max) == (50, 0, False))
old_item = Item("古い薬", "medicine", value=3, obtainer=None)
old_item.attributes.clear()
old_item.attributes.update({"item_detail": "medicine", "回復": 435, "疲労負荷": 0})
check("numeric 回復 (version 2 items) still counts as an amount",
      module.effect_of(old_item, small) == (435, 0, False))

# ---------------------------------------------------------------- 薬草
print("[薬草]")
rng = random.Random(7)
ups = downs = 0
for i in range(200):
    herb = Item("薬草{}".format(i), "plant", value=70, heal=349, obtainer=player)
    module.rework(herb, "t", rng=rng)
    hp_text = herb.attributes["HP増減"]
    st = herb.attributes["スタミナ増減"]
    hp = float(hp_text.rstrip("%"))
    # common: HP の底 41 * 0.8 = 32.8% → ±16.4〜32.8。スタミナの底 22 → ±11〜22
    if not (hp_text[0] in "+-" and 16.3 <= abs(hp) <= 32.9 and 11 <= abs(st) <= 22):
        check("herb magnitude in range", False, herb.attributes)
        break
    ups += hp > 0
    downs += hp < 0
else:
    check("herb magnitude in range", True)
check("herb sign follows HERB_GOOD_CHANCE (0.7)", 110 <= ups <= 170, (ups, downs))
herb = Item("きのこ", "mushroom", value=10, heal=30, obtainer=player)
module.rework(herb, "t", rng=random.Random(1))
first = dict(herb.attributes)
module.rework(herb, "t", rng=random.Random(2))
check("herb roll is not redone", herb.attributes == first, (first, herb.attributes))
check("herb keys in order",
      list(herb.attributes) == ["item_detail", "HP増減", "スタミナ増減", "疲労負荷", "買価"],
      list(herb.attributes))

# 触らない
print("[触らない]")
scroll = Item("巻物", "scroll", item_type="consumable", obtainer=player)
check("scroll untouched", module.rework(scroll, "t") is False
      and scroll.attributes.get("疲労負荷") == 10)
ready = Item("薬草", None, heal=30, burden=None, item_type="consumable", obtainer=player)
ready.attributes.pop("item_detail")
check("ready-made herb (no item_detail) untouched", module.rework(ready, "t") is False)
weapon = Item("短剣", "small_weapon", item_type="weapon", obtainer=player)
check("weapon untouched", module.rework(weapon, "t") is False)

# ---------------------------------------------------------------- 使う
print("[使う]")
manager = Manager(app)
player.physical_integrity = 5
player.current_hp = 100
n_before = len(player.inventory)
result, calls = consume_via(ctx, manager, food)
check("original called once, usable forced True, 回復 0, 疲労負荷 0",
      calls == [(True, 0, 0)], calls)
check("回復 not left behind on food", "回復" not in food.attributes, food.attributes)
check("stamina capped at max (5 + 22 -> 10)", player.physical_integrity == 10,
      player.physical_integrity)
check("hp untouched by food", player.current_hp == 100, player.current_hp)
check("item consumed by the game", len(player.inventory) == n_before - 1)
check("text replaced", app.texts[-1] == "干し肉を消費した。スタミナが5回復した。",
      app.texts[-1])
check("ui refreshed", app.ui_updates == 1, app.ui_updates)

player.current_hp = 100
consume_via(ctx, manager, medicine)
check("medicine heals to max (100 + 38% of 120 -> 120)", player.current_hp == 120,
      player.current_hp)
check("medicine keeps its percent after use", pct_of(medicine.attributes["回復"]) == med_pct)
check("medicine text", app.texts[-1] == "煎じ薬を消費した。HPが20回復した。",
      app.texts[-1])
check("game never saw the percent string", calls[-1][1] == 0)

player.current_hp = 10
player.max_hp = 1000
expected = int(round(1000 * med_pct / 100.0))
consume_via(ctx, manager, medicine)
check("medicine heals its percent of max_hp", player.current_hp == 10 + expected,
      (player.current_hp, expected))
check("medicine text carries the amount",
      app.texts[-1] == "煎じ薬を消費した。HPが{}回復した。".format(expected), app.texts[-1])
player.max_hp = 120

player.current_hp = 200          # 上限を超えている（GAME.md §2.19 の観測）
consume_via(ctx, manager, medicine)
check("hp above max is not reduced", player.current_hp == 200, player.current_hp)
check("nothing-happened text", app.texts[-1] == "煎じ薬を消費した。何も起きなかった。",
      app.texts[-1])

player.current_hp = 10
player.status = {"泥濘の拘束": {"description": "…", "duration": 3}}
potion_heal = int(round(120 * pct_of(potion.attributes["回復"]) / 100.0))
consume_via(ctx, manager, potion)
check("potion heals its percent and clears status",
      player.current_hp == 10 + potion_heal and player.status == {},
      (player.current_hp, potion_heal, player.status))
check("potion text",
      app.texts[-1] == "薬瓶を消費した。HPが{}回復した。状態異常が消えた。".format(potion_heal),
      app.texts[-1])

bad = Item("毒きのこ", "mushroom", value=10, heal=30, obtainer=player)
bad.attributes.clear()
bad.attributes.update({"item_detail": "mushroom", "HP増減": "-90%", "スタミナ増減": -3,
                       "疲労負荷": 0, "買価": 10})
player.current_hp = 40
player.physical_integrity = 2
consume_via(ctx, manager, bad)
check("bad herb: hp floors at 1, stamina floors at 0",
      player.current_hp == 1 and player.physical_integrity == 0,
      (player.current_hp, player.physical_integrity))
check("bad herb text", app.texts[-1] == "毒きのこを消費した。スタミナが2減った。HPが39減った。",
      app.texts[-1])

# 古いセーブの品（作り直す前）を直接使う
old = Item("古いパン", "food", value=3, heal=9, burden=10, obtainer=player)
player.physical_integrity = 0
consume_via(ctx, manager, old)
check("old item reworked on use", "スタミナ回復" in old.attributes, old.attributes)
check("old item effect applied", player.physical_integrity == old.attributes["スタミナ回復"],
      (player.physical_integrity, old.attributes))

# 拒否: 本体が消費しなかった
print("[拒否]")
player.physical_integrity = 0
result, calls = consume_via(ctx, manager, drink, consume=False)
check("refused: no effect", player.physical_integrity == 0, player.physical_integrity)
check("refused: logged", "refused" in io.open(
    os.path.join(OUT_DIR, "item_effects.log"), encoding="utf-8").read())

# 本体が文を出さなかったとき
print("[文が無いとき]")


def silent(self, item_instance, usable_flag):
    item_instance.obtainer.inventory.pop(next(iter(item_instance.obtainer.inventory)))


hook = ctx.hooks["__main__:ItemConsumeManager.consume_item"]
player.inventory = {"a": 1, "b": 2}
player.physical_integrity = 1
salt = Item("塩", "food", value=3, obtainer=player)
hook(silent, manager, salt, True)
check("text added by the mod when the game says nothing",
      app.texts[-1] == "塩を消費した。スタミナが{}回復した。".format(salt.attributes["スタミナ回復"]),
      app.texts[-1])

# 他の品は素通し
print("[素通し]")
hook_calls = []


def pass_through(self, item_instance, usable_flag):
    hook_calls.append(usable_flag)
    return "orig"


check("scroll passes through untouched",
      hook(pass_through, manager, scroll, False) == "orig" and hook_calls == [False])

# ---------------------------------------------------------------- 説明欄
print("[説明欄]")
seen = {}


def fake_update(self, item):
    seen["keys"] = list(item.attributes)


box = ctx.hooks["scripts.hud.new_hud:ItemDetailBox.update_content"]
box(fake_update, object(), medicine)
check("burden hidden while drawing", "疲労負荷" not in seen["keys"], seen["keys"])
check("burden restored after drawing, order kept",
      list(medicine.attributes) == ["item_detail", "回復", "疲労負荷", "買価"],
      list(medicine.attributes))
fresh = Item("新しいパン", "food", value=3, obtainer=player)
box(fake_update, object(), fresh)
check("detail box reworks old items first",
      "スタミナ回復" in seen["keys"] and "疲労負荷" not in seen["keys"], seen["keys"])
box(fake_update, object(), scroll)
check("scroll keeps its burden in the box", "疲労負荷" in seen["keys"], seen["keys"])

# 窓を開いたとき
twin = ctx.hooks["__main__:InstantaleApp.toggle_twin_inventory_window"]
shop = Character("店主")
shop.inventory = {"x": Item("干し魚", "food", value=5, obtainer=shop)}
twin(lambda *a, **k: None, app, shop, player, "店", "shop")
check("shop items reworked on window open",
      "スタミナ回復" in shop.inventory["x"].attributes)

# ---------------------------------------------------------------- 英語
print("[英語]")
player_en = Character("Eris")
app_en = App(player_en)
module_en, ctx_en = fresh_mod(app_en, lang="en")
food_en = Item("Bread", "food", value=3, heal=None, burden=None, obtainer=player_en)
food_en.attributes["recovery"] = 9
food_en.attributes["fatigue"] = 10
module_en.rework(food_en, "t", rng=random.Random(1))
check("english keys", list(food_en.attributes) == ["item_detail", "stamina", "fatigue", "買価"],
      list(food_en.attributes))
manager_en = Manager(app_en)
player_en.physical_integrity = 1


def fake_en(self, item_instance, usable_flag):
    item_instance.obtainer.inventory.pop(next(iter(item_instance.obtainer.inventory)))
    self.app.add_text("Consumed Bread. Recovered 0 HP.")


ctx_en.hooks["__main__:ItemConsumeManager.consume_item"](fake_en, manager_en, food_en, True)
check("english text",
      app_en.texts[-1] == "Consumed Bread. Stamina +{}.".format(food_en.attributes["stamina"]),
      app_en.texts[-1])
check("japanese-made item read under english",
      module_en.effect_of(food, player) == (0, food.attributes["スタミナ回復"], False),
      module_en.effect_of(food, player))
med_en = Item("Salve", "medicine", value=3, heal=None, burden=None, obtainer=player_en)
module_en.rework(med_en, "t", rng=random.Random(1))
check("english medicine gets a percent under the english key",
      pct_of(med_en.attributes.get("recovery")) is not None, med_en.attributes)

# ---------------------------------------------------------------- 結果
print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
