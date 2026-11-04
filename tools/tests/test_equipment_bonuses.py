# -*- coding: utf-8 -*-
"""411_equipment_ability_bonus をゲーム抜きで通す。

    python tools/tests/test_equipment_ability_bonus.py

確認するもの:

  抽選       … レア度ごとの数と量。epic 未満は付かない。同じ能力値は重ならない
  書く・読む … 説明欄に「筋力: +3」で、値段の行の手前に入る。両方の言語の名前を読む
  まとめ方   … sum / max と上限
  付ける     … 生成の入口で1回だけ。もとから補正のある品は引き直さない
  効かせる   … 窓口（abilities）に、主人公と仲間の身に着けている品の補正を答える。敵・仲間を切ったときは答えない
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "equipment_ability_bonus"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import abilities, equipment, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


def load(name, folder, path):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Item(object):
    def __init__(self, name, item_type, rarity, attributes=None):
        self.name = name
        self.item_type = item_type
        self.rarity = rarity
        self.attributes = attributes if attributes is not None else {"攻撃力": 30, "買価": 100}


class Person(object):
    def __init__(self, name, ident=None):
        self.name = name
        if ident is not None:
            self.id = ident
        self.equipments = {}


class FakeCtx:
    _mod = "411_equipment_ability_bonus"

    def __init__(self, out_dir, mod_dir):
        self.out_dir = out_dir
        self.mod_dir = mod_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


folder, path = find_mod("_equipment_ability_bonus")
package = os.path.basename(folder)
sys.modules["scripts.languages"] = types.SimpleNamespace(language="ja")
pkg = types.ModuleType("mod411")
pkg.__path__ = [folder]
sys.modules["mod411"] = pkg
bonus = load("mod411.bonus", folder, os.path.join(folder, "bonus.py"))
mod = load("mod411.equipment_ability_bonus", folder, path)

print("抽選")
tiers = {"epic": (1, 1, 1, 2), "legendary": (1, 2, 2, 3), "mythic": (2, 3, 3, 4)}
rng = random.Random(4)
rolls = [bonus.roll("mythic", tiers, rng) for _ in range(200)]
check("mythic は 2〜3 個", all(2 <= len(r) <= 3 for r in rolls))
check("mythic は 3〜4", all(3 <= v <= 4 for r in rolls for v in r.values()))
check("rare には付かない", bonus.roll("rare", tiers, rng) == {})
check("数が6を超えても6つまで", len(bonus.roll("x", {"x": (9, 9, 1, 1)}, rng)) == 6)

print("書く・読む")
sword = Item("剣", "weapon", "epic")
check("書けた", bonus.write(sword, {"strength": 3, "wisdom": 1}, "ja"))
check("値段の手前に入る", list(sword.attributes) == ["攻撃力", "筋力", "判断", "買価"])
check("説明欄の値は +3", sword.attributes["筋力"] == "+3")
check("読む", bonus.read(sword) == {"strength": 3, "wisdom": 1})
check("英語の名前も読む", bonus.read(Item("x", "weapon", "epic", {"STR": "+2", "CHA": 1}))
      == {"strength": 2, "charisma": 1})
check("武器・防具だけが装備", bonus.is_equipment(sword) and not bonus.is_equipment(Item("薬", "healing_item", "epic")))

print("まとめ方")
mail = Item("鎧", "wearable", "mythic", {"筋力": "+4", "器用": "+2"})
check("sum", bonus.combine([sword, mail], "sum", 0) == {"strength": 7, "wisdom": 1, "dexterity": 2})
check("max", bonus.combine([sword, mail], "max", 0) == {"strength": 4, "wisdom": 1, "dexterity": 2})
check("上限", bonus.combine([sword, mail], "sum", 5)["strength"] == 5)

print("付ける・効かせる")
for attr in (abilities._REGISTRY_ATTR, mod.STORE_ATTR):
    if hasattr(sys, attr):
        delattr(sys, attr)
player = Person("主人公")
ally = Person("仲間", ident=95)
enemy = Person("敵", ident=7)
app = types.SimpleNamespace(player=player)
ui.party_member_ids = lambda _app: ["95"]
worn = {id(player): [("weapon", sword), ("free1", mail)], id(ally): [("wearable", mail)],
        id(enemy): [("weapon", sword)]}
equipment.gear = lambda _app, holder: worn.get(id(holder))
ctx = FakeCtx(OUT_DIR, folder)
mod.apply(ctx)
check("窓口に答えを置いた", abilities.owners() == [package])
check("主人公（自由枠は数えない）", abilities.bonus(app, player) == {"strength": 3, "wisdom": 1})
check("仲間", abilities.bonus(app, ally) == {"strength": 4, "dexterity": 2})
check("敵には答えない", abilities.bonus(app, enemy) == {})
mod.INCLUDE_FREE_SLOTS = True
check("自由枠も数える設定", abilities.bonus(app, player) == {"strength": 7, "wisdom": 1, "dexterity": 2})
mod.APPLY_TO_PARTY = False
check("仲間を切ると答えない", abilities.bonus(app, ally) == {})
mod.APPLY_TO_PARTY = True
mod.INCLUDE_FREE_SLOTS = False

generated = ctx.hooks["__main__:InstantaleApp.generate_item_from_item_data"]
item_init = ctx.hooks["scripts.items:Item.__init__"]
made = []


def make(self, rarity):
    item = Item("新しい盾", "wearable", rarity)
    item_init(lambda s, *a, **k: None, item)
    made.append(item)
    return item


mod.bonus.RNG.seed(1)
generated(make, None, "legendary")
check("生成で legendary に付く", bool(bonus.read(made[-1])), made[-1].attributes)
shield = made[-1]
before = dict(shield.attributes)


def again(self):
    item_init(lambda s, *a, **k: None, shield)
    return shield


mod.bonus.RNG.seed(2)
generated(again, None)
check("同じ品は引き直さない", dict(shield.attributes) == before, shield.attributes)
mod.process_store()["rolled"].discard(shield)
generated(again, None)
check("もとから補正のある品にも足さない", dict(shield.attributes) == before, shield.attributes)
generated(make, None, "common")
check("common には付かない", bonus.read(made[-1]) == {})
check("例外が出ていない", not ctx.errors, ctx.errors)

if failures:
    print("{} failed: {}".format(len(failures), failures))
    sys.exit(1)
print("all ok")
