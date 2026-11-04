# -*- coding: utf-8 -*-
"""411_equipment_bonuses をゲーム抜きで通す。

    python tools/tests/test_equipment_bonuses.py

確認するもの:

  抽選       … レア度ごとの数と量。epic 未満は付かない。同じ能力値は重ならない
  書く・読む … 説明欄に「筋力: +3」で、値段の行の手前に入る。両方の言語の名前を読む
  まとめ方   … sum / max と上限
  付ける     … 生成の入口で1回だけ。もとから補正のある品は引き直さない
  効かせる   … 窓口（abilities）に、主人公と仲間の身に着けている品の補正を答える。敵・仲間を切ったときは答えない
  武器の効果 … 抽選（武器だけ・種類は重ならない・切った種類は付かない）、説明欄の書き読み、まとめ方、
               戦闘の1手（会心・貫通・特効・吸収・毒刃・怯み）。319 の有無に依らない形（get_instant_damage の戻りに足す）
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
                                        "equipment_bonuses"))

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
    _mod = "411_equipment_bonuses"

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


folder, path = find_mod("_equipment_bonuses")
package = os.path.basename(folder)
sys.modules["scripts.languages"] = types.SimpleNamespace(language="ja")
pkg = types.ModuleType("mod411")
pkg.__path__ = [folder]
sys.modules["mod411"] = pkg
bonus = load("mod411.bonus", folder, os.path.join(folder, "bonus.py"))
effects = load("mod411.effects", folder, os.path.join(folder, "effects.py"))
mod = load("mod411.equipment_bonuses", folder, path)

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


def make_weapon(self, rarity):
    item = Item("新しい剣", "weapon", rarity)
    item_init(lambda s, *a, **k: None, item)
    made.append(item)
    return item


effects.RNG.seed(3)
generated(make_weapon, None, "mythic")
check("生成で mythic の武器に効果が付く", 1 <= len(effects.read(made[-1])) <= 2, made[-1].attributes)
check("効果の行も値段の手前", list(made[-1].attributes)[-1] == "買価", made[-1].attributes)
generated(make, None, "mythic")
check("防具には効果が付かない", effects.read(made[-1]) == [], made[-1].attributes)
check("例外が出ていない", not ctx.errors, ctx.errors)

print("武器の効果: 抽選")
counts = {"epic": (0, 1), "legendary": (1, 1), "mythic": (1, 2)}
rng = random.Random(5)
rolls = [effects.roll("mythic", counts, effects.KINDS, rng) for _ in range(300)]
check("mythic は 1〜2 個", all(1 <= len(r) <= 2 for r in rolls))
check("同じ種類は重ならない", all(len({k for k, _, _ in r}) == len(r) for r in rolls))
check("mythic の強さ", all(a == effects.STRENGTH[k][2] for r in rolls for k, a, _ in r))
check("特効には相手が付く", all(t in effects.SLAYER_TARGETS for r in rolls for k, _, t in r if k == "slayer"))
check("6種類とも出る", {k for r in rolls for k, _, _ in r} == set(effects.KINDS))
check("切った種類は付かない", all(k == "critical" for _ in range(50)
                                for k, _, _ in effects.roll("legendary", counts, ["critical"], rng)))
check("rare には付かない", effects.roll("rare", counts, effects.KINDS, rng) == [])

print("武器の効果: 書く・読む")
blade = Item("刃", "weapon", "mythic", {"攻撃力": 30, "筋力": "+3", "買価": 100})
found = [("slayer", 40, "boss"), ("critical", 20, None), ("venom", 40, None)]
check("書けた", effects.write(blade, found, "ja"))
check("能力値補正の後ろ・値段の手前", list(blade.attributes) == ["攻撃力", "筋力", "特効", "会心", "毒", "買価"],
      list(blade.attributes))
check("説明欄の値", (blade.attributes["特効"], blade.attributes["会心"], blade.attributes["毒"])
      == ("ボス戦 +40%", "20%", "命中時 40%"), blade.attributes)
check("読む", effects.read(blade) == found, effects.read(blade))
check("能力値補正の読みと混ざらない", bonus.read(blade) == {"strength": 3})
en = Item("x", "weapon", "epic", {})
effects.write(en, [("slayer", 20, "wounded"), ("lifesteal", 6, None)], "en")
check("英語で書いて読む", effects.read(en) == [("slayer", 20, "wounded"), ("lifesteal", 6, None)], en.attributes)

print("武器の効果: まとめ方")
dagger = Item("短剣", "weapon", "legendary", {"会心": "15%", "特効": "ボス戦 +30%", "貫通": "35%"})
check("sum", effects.combine([blade, dagger], "sum") ==
      {"slayer": {"boss": 70}, "critical": 35, "venom": 40, "pierce": 35})
check("max", effects.combine([blade, dagger], "max")["critical"] == 20)
check("確率は 100 で止める", effects.combine([Item("a", "weapon", "x", {"会心": "80%"}),
                                          Item("b", "weapon", "x", {"会心": "80%"})], "sum")["critical"] == 100)
check("特効: ボス戦かつ手負いは足す", effects.slayer_bonus({"boss": 30, "wounded": 20}, True, 10, 5, 40, 100)
      == (50, ["boss", "wounded"]))
check("特効: 格上", effects.slayer_bonus({"higher": 30}, False, 10, 11, 100, 100) == (30, ["higher"]))
check("特効: 無傷は満タンだけ", effects.slayer_bonus({"unhurt": 30}, False, 10, 5, 99, 100) == (0, []))
check("会心: extreme は上がらない", effects.raise_power("extreme") is None and effects.raise_power("weak") == "normal")

print("武器の効果: 戦闘の1手")
foe = Person("ゴブリン")
foe.current_hp, foe.max_hp, foe.experience_level, foe.status = 500, 500, 5, {}
hero_blade = Item("勇者の剣", "weapon", "mythic", {"会心": "100%", "貫通": "50%", "特効": "無傷 +40%",
                                                  "吸収": "10%", "毒": "命中時 100%", "怯み": "命中時 100%"})
player.current_hp, player.max_hp, player.experience_level = 100, 300, 10
worn[id(player)] = [("weapon", hero_blade), ("wearable", mail)]
said = []
app.current_enemy_dict = {"ゴブリン1": foe}
app.in_boss_battle = False
app.add_text = said.append
ui.character_of = lambda _app, ident: ally if str(ident) == "95" else None
handle = ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"]
calc = ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"]
npc_defense = ctx.hooks["scripts.characters:Character.get_npc_defense"]
instant = ctx.hooks["scripts.functions:get_instant_damage"]
reduce_status = ctx.hooks["__main__:BattlePhaseManager.reduce_status_turns_and_log"]
manager = types.SimpleNamespace(app=app)
seen = {}


def game_turn(self, key, side, action):
    """ゲームの1手の代わり: 素点 → 敵の防御 → ダメージ（引き算の防御）。"""
    calc(lambda s, a: seen.setdefault("power", a["instant_damage"][0]["power"]), self, action)
    defense = npc_defense(lambda s: 100, foe)
    dealt = instant(lambda attack, d: seen.setdefault("defense", d) and attack - d, 400, defense)
    seen["dealt"] = dealt
    if isinstance(dealt, (int, float)):
        foe.current_hp -= dealt


handle(game_turn, manager, "主人公", "味方陣営",
       {"instant_damage": [{"target": "ゴブリン", "power": "normal", "category": "physical"}]})
check("会心で裁きが1段上がる", seen["power"] == "strong", seen)
check("貫通で防御が半分になって渡る", seen["defense"] == 50, seen)
check("特効（無傷）で 1.4 倍", seen["dealt"] == 490, seen)
check("吸収で与えたダメージの1割を戻す", player.current_hp == 149, player.current_hp)
check("毒刃と怯みが敵の status に付く", set(foe.status) == {"毒刃", "怯み"}, foe.status)
check("画面に会心・特効・吸収が出る", said and "会心" in said[-1] and "特効: 無傷" in said[-1]
      and "吸収" in said[-1], said)

seen.clear()
foe_turn_dealt = instant(lambda a, d: a - d, 400, 0)
check("1手の外では触らない", foe_turn_dealt == 400)


def foe_turn(self, key, side, action):
    seen["hit"] = instant(lambda attack, d: attack - d, 300, 100)


handle(foe_turn, manager, "ゴブリン1", "敵側", {})
check("怯んだ敵の一撃は 3割下がる", seen["hit"] == 140, seen)
foe.current_hp = before = 500
reduce_status(lambda s, c: None, manager, foe)
check("毒刃は毎ターン最大体力の4%を削る", before - foe.current_hp == 20, (before, foe.current_hp))
foe.current_hp = 5
reduce_status(lambda s, c: None, manager, foe)
check("毒刃では倒れない", foe.current_hp == 1, foe.current_hp)
del foe.status["怯み"]
seen.clear()
handle(foe_turn, manager, "ゴブリン1", "敵側", {})
check("怯みが消えたら元の一撃", seen["hit"] == 200, seen)

worn[id(player)] = [("wearable", hero_blade)]
hero_blade.item_type = "wearable"
seen.clear()
handle(game_turn, manager, "主人公", "味方陣営",
       {"instant_damage": [{"target": "ゴブリン", "power": "normal", "category": "physical"}]})
check("武器でない品の効果は効かない", seen["power"] == "normal" and seen["defense"] == 100, seen)
check("例外が出ていない（戦闘）", not ctx.errors, ctx.errors)

if failures:
    print("{} failed: {}".format(len(failures), failures))
    sys.exit(1)
print("all ok")
