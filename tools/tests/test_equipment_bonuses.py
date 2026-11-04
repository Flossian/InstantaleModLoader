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
from instantale_modloader import abilities, equipment, prices, ui  # noqa: E402

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
            self.hooks.setdefault(target, func)  # 411 の後で prices.install が同じ対象を包む。検査は 411 のものを呼ぶ
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
check("防具に付くのは防具の効果だけ",
      1 <= len(effects.read(made[-1])) <= 2
      and {k for k, _, _ in effects.read(made[-1])} <= set(effects.ARMOR_KINDS), made[-1].attributes)
check("例外が出ていない", not ctx.errors, ctx.errors)

print("武器の効果: 抽選")
counts = {"epic": (0, 1), "legendary": (1, 1), "mythic": (1, 2)}
rng = random.Random(5)
rolls = [effects.roll("mythic", counts, effects.KINDS, rng) for _ in range(300)]
check("mythic は 1〜2 個", all(1 <= len(r) <= 2 for r in rolls))
check("同じ種類は重ならない", all(len({k for k, _, _ in r}) == len(r) for r in rolls))
check("mythic の強さ", all(a == effects.STRENGTH[k][2] for r in rolls for k, a, _ in r))
check("特効には相手が付く", all(t in effects.SLAYER_TARGETS for r in rolls for k, _, t in r if k == "slayer"))
check("どの種類も出る", {k for r in rolls for k, _, _ in r} == set(effects.KINDS))
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
    seen["hp_at_hit"] = player.current_hp
    if isinstance(dealt, (int, float)):
        foe.current_hp -= dealt


handle(game_turn, manager, "主人公", "味方陣営",
       {"instant_damage": [{"target": "ゴブリン", "power": "normal", "category": "physical"}]})
check("会心で裁きが1段上がる", seen["power"] == "strong", seen)
check("貫通で防御が半分になって渡る", seen["defense"] == 50, seen)
check("特効（無傷）で 1.4 倍", seen["dealt"] == 490, seen)
check("吸収で与えたダメージの1割を戻す", player.current_hp == 149, player.current_hp)
check("吸収は当たった時点で戻す（308 の報告より先）", seen["hp_at_hit"] == 149, seen)
check("吸収の出どころを 308 へ控える", ui.take_damage_notes("主人公", 49) == "吸収")
check("毒刃と怯みが敵の status に付く", set(foe.status) == {"毒刃", "怯み"}, foe.status)
check("画面に会心・特効が出て、吸収の行は 411 から出さない",
      said and "会心" in said[-1] and "特効: 無傷" in said[-1] and "吸収" not in said[-1], said)

hits_blade = Item("連撃の剣", "weapon", "mythic", {"吸収": "10%"})
worn[id(player)] = [("weapon", hits_blade)]


def many_hits(self, key, side, action):
    """小さな当たりの連続。1発ごとに丸めると 0 になる量でも、累計で戻す。"""
    for _ in range(3):
        instant(lambda attack, d: attack - d, 4, npc_defense(lambda s: 0, foe))


foe.current_hp, foe.max_hp = 500, 600
player.current_hp = 100
handle(many_hits, manager, "主人公", "味方陣営", {})
check("吸収の端数を当たりごとに捨てない", player.current_hp == 101, player.current_hp)
check("控えの合計が戻した量と合う", ui.take_damage_notes("主人公", 1) == "吸収")
foe.current_hp, player.current_hp = 500, 300
handle(many_hits, manager, "主人公", "味方陣営", {})
check("体力が満タンなら戻さない", player.current_hp == 300, player.current_hp)
check("満タンのときは出どころを控えない", ui.take_damage_notes("主人公", 1) is None)
foe.current_hp, foe.max_hp, foe.status = 500, 500, {"毒刃": foe.status["毒刃"], "怯み": foe.status["怯み"]}
worn[id(player)] = [("weapon", hero_blade), ("wearable", mail)]

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

print("武器の効果: 呪い・成長・処刑・連携")
start_battle = ctx.hooks["__main__:BattleStartManager.start_battle"]


def fresh_foe(hp=500, top=500):
    foe.current_hp, foe.max_hp, foe.status = hp, top, {}
    start_battle(lambda s: None, manager)


def strike(actor, raw=400, defense=100):
    """味方の手。戻りはゲームが受け取るダメージで、敵の体力へ当てる。"""
    def turn(self, key, side, action):
        got = instant(lambda attack, d: attack - d, raw, npc_defense(lambda s: defense, foe))
        seen["dealt"] = got
        foe.current_hp -= got
    handle(turn, manager, actor, "味方陣営", {})
    return seen["dealt"]


said.clear()
fresh_foe()
worn[id(player)] = [("weapon", Item("呪剣", "weapon", "mythic", {"呪い": "+50%・当てると体力 -3%"}))]
player.current_hp, player.max_hp = 100, 300
check("呪い: 与えるダメージ +50%", strike("主人公") == 450, seen)
check("呪い: 当てた手で最大体力の3%を失う", player.current_hp == 91, player.current_hp)
check("呪い: 出どころを 308 へ控える", ui.take_damage_notes("主人公", 9) == "呪い")
player.current_hp = 2
strike("主人公")
check("呪いでは倒れない", player.current_hp == 1, player.current_hp)

fresh_foe()
blade = Item("育つ剣", "weapon", "mythic", {})
effects.write(blade, [("growth", 5, None)], "ja")
check("成長: 説明欄の形", blade.attributes["成長"] == "+5% / 10体（討伐 0）", blade.attributes)
check("成長: 量を読む", effects.read(blade) == [("growth", 5, None)], effects.read(blade))
worn[id(player)] = [("weapon", blade)]
foe.current_hp = 100
strike("主人公")
check("成長: 倒すと討伐数が増える", effects.kills_of(blade) == 1, blade.attributes)
foe.current_hp = 1000
foe.max_hp = 1000
strike("主人公")
check("成長: 倒していなければ増えない", effects.kills_of(blade) == 1, blade.attributes)
blade.attributes["成長"] = "+5% / 10体（討伐 9）"
foe.current_hp = 100
said.clear()
strike("主人公")
check("成長: 節目で本文に出る", any("討伐 10" in text for text in said), said)
foe.current_hp = 1000
check("成長: 10体で +5%", strike("主人公") == 315, seen)
blade.attributes["成長"] = "+5% / 10体（討伐 900）"
foe.current_hp = 5000
foe.max_hp = 5000
check("成長: 上乗せは 100% で止まる", strike("主人公") == 600, seen)
english = Item("en", "weapon", "mythic", {})
effects.write(english, [("growth", 3, None)], "en")
check("成長: 英語の討伐数", effects.add_kill(english) == 1 and "(1 kills)" in english.attributes["Growth"],
      english.attributes)

fresh_foe()
worn[id(player)] = [("weapon", Item("処刑の斧", "weapon", "mythic", {"処刑": "残り体力 15% 以下"}))]
foe.current_hp = 450
check("処刑: 残りが割合を超えるなら普通のダメージ", strike("主人公") == 300, seen)
foe.current_hp = 370
foe.max_hp = 1000
check("処刑: 残りが割合以下なら敵の体力ぶん", strike("主人公") == 370 and foe.current_hp == 0, seen)
foe.current_hp = 200
check("処刑: もともと倒せる一撃は変えない", strike("主人公") == 300, seen)

said.clear()
fresh_foe(hp=5000, top=5000)
pair = Item("連携の剣", "weapon", "mythic", {"連携": "味方の後 +30%"})
worn[id(player)] = [("weapon", pair)]
worn[id(ally)] = [("weapon", sword)]
check("連携: 味方が当てる前は上がらない", strike("主人公") == 300, seen)
check("連携: 自分の直後は上がらない", strike("主人公") == 300, seen)
strike("仲間")
check("連携: 味方の直後は上がる", strike("主人公") == 390, seen)
check("連携: 本文に出る", any("連携" in text for text in said), said)

print("防具の効果")
worn[id(player)] = [("wearable", Item("鎧", "wearable", "mythic", {}))]
worn[id(ally)] = []
ally.current_hp, ally.max_hp, ally.status = 500, 500, {}
player.current_hp, player.max_hp = 500, 500


def blow(target, raw=200, npc=None):
    """敵の手。主人公は防具の値で来る（`get_npc_defense` を通らない）。仲間は通る。"""
    def turn(self, key, side, action):
        calc(lambda s, a: ({target: raw},), self, action)
        defense = npc_defense(lambda s: 50, npc) if npc is not None else 500
        got = instant(lambda attack, d: attack, raw, defense)
        seen["taken"] = got
        (npc if npc is not None else player).current_hp -= got
    handle(turn, manager, "ゴブリン1", "敵側", {})
    return seen["taken"]


def wear(holder, *attributes):
    worn[id(holder)] = [("wearable", Item("防具{}".format(i), "wearable", "mythic", a))
                        for i, a in enumerate(attributes)]


fresh_foe(hp=5000, top=5000)
check("防具の効果なし: 素のまま", blow("主人公") == 200, seen)
wear(player, {"重装": "受けるダメージ -30%・器用 -4"})
check("重装: 主人公（素点で照らす）", blow("主人公") == 140, seen)
check("重装: 器用が下がる", abilities.bonus(app, player).get("dexterity") == -4, abilities.bonus(app, player))
wear(ally, {"重装": "受けるダメージ -20%・器用 -4"})
check("重装: 仲間（防御の持ち主で分かる）", blow("仲間", npc=ally) == 160, seen)
wear(player, {"重装": "受けるダメージ -20%・器用 -4"}, {"重装": "受けるダメージ -30%・器用 -4"})
player.current_hp = 500
check("同じ効果を2つ着ても重ならず、最大の1品だけ", blow("主人公") == 140, seen)
check("器用の低下も1回ぶん", abilities.bonus(app, player).get("dexterity") == -4, abilities.bonus(app, player))
check("武器の連携は防具に数えない", "teamwork" not in mod.current_armor(app, player))

wear(player, {"不屈": "100% 戦闘に1回"})
player.current_hp = 100
said.clear()
check("不屈: 倒れる一撃を体力 1 で耐える", blow("主人公") == 99 and player.current_hp == 1, seen)
check("不屈: 本文に出る", any("不屈" in text for text in said), said)
player.current_hp = 100
check("不屈: 戦闘に1回", blow("主人公") == 200, seen)
start_battle(lambda s: None, manager)
player.current_hp = 300
check("不屈: 倒れない一撃は変えない", blow("主人公") == 200 and player.current_hp == 100, seen)
player.current_hp = 100
check("不屈: 次の戦闘でまた効く", blow("主人公") == 99, seen)

wear(player, {"棘": "受けたダメージの 40%"})
player.current_hp = 500
foe.current_hp = 1000
check("棘: 受けるダメージは変えない", blow("主人公") == 200, seen)
check("棘: 受けた割合を敵に返す", foe.current_hp == 920, foe.current_hp)
check("棘: 出どころを 308 へ控える", ui.take_damage_notes("ゴブリン", 80) == "棘")
foe.current_hp = 10
player.current_hp = 500
blow("主人公")
check("棘では敵を倒さない", foe.current_hp == 1, foe.current_hp)
foe.current_hp = 1000

wear(player, {"再生": "毎ターン 最大体力の 4%"})
player.current_hp = 100
reduce_status(lambda s, c: None, manager, player)
check("再生: 最大体力の4%が戻る", player.current_hp == 120, player.current_hp)
check("再生: 出どころを 308 へ控える", ui.take_damage_notes("主人公", 20) == "再生")
player.current_hp = 499
reduce_status(lambda s, c: None, manager, player)
check("再生: 最大で止まる", player.current_hp == 500, player.current_hp)
player.current_hp = 0
reduce_status(lambda s, c: None, manager, player)
check("再生: 倒れている者は戻さない", player.current_hp == 0, player.current_hp)
hp_before = foe.current_hp
reduce_status(lambda s, c: None, manager, foe)
check("再生: 敵には効かない", foe.current_hp == hp_before, foe.current_hp)

wear(ally)
wear(player, {"守護": "仲間をかばう 100%"})
player.current_hp, ally.current_hp = 500, 500
said.clear()
check("守護: 仲間への一撃は 0 になる", blow("仲間", npc=ally) == 0 and ally.current_hp == 500, seen)
check("守護: 肩代わりした者が受ける", player.current_hp == 300, player.current_hp)
check("守護: 本文に出る", any("かばった" in text for text in said), said)
player.current_hp, ally.current_hp = 100, 500
check("守護: 倒れるなら肩代わりしない", blow("仲間", npc=ally) == 200 and player.current_hp == 100, seen)
wear(player, {"守護": "仲間をかばう 100%", "重装": "受けるダメージ -50%・器用 -4"})
player.current_hp, ally.current_hp = 150, 500
blow("仲間", npc=ally)
check("守護: 肩代わりした者の重装で受ける", player.current_hp == 50, player.current_hp)
wear(ally, {"守護": "仲間をかばう 100%"})
wear(player)
player.current_hp, ally.current_hp = 500, 500
check("守護: 主人公への一撃も仲間がかばう", blow("主人公") == 0 and ally.current_hp == 300, seen)
player.current_hp = 500
worn[id(player)] = [("weapon", Item("飾り", "wearable", "mythic", {"守護": "仲間をかばう 100%"}))]

print("防具の効果: 耐性・軽業・火事場・呪縛・威光")
wear(ally)
wear(player, {"軽業": "一撃をかわす 100%"})
player.current_hp = 500
said.clear()
check("軽業: 一撃が 0 になる", blow("主人公") == 0 and player.current_hp == 500, seen)
check("軽業: 本文に出る", any("かわした" in text for text in said), said)

wear(player, {"火事場": "受けるダメージ -40%（体力 30% 以下）"})
player.current_hp = 151
check("火事場: 体力が多い間は効かない", blow("主人公") == 200, seen)
player.current_hp = 150
player.max_hp = 500
check("火事場: 体力 30% 以下で効く", blow("主人公") == 120, seen)

wear(player, {"呪縛": "受けるダメージ -50%・与えるダメージ -20%"})
player.current_hp = 500
check("呪縛: 受けるダメージが下がる", blow("主人公") == 100, seen)
fresh_foe(hp=5000, top=5000)
check("呪縛: 与えるダメージも下がる", strike("主人公") == 240, seen)

wear(player, {"重装": "受けるダメージ -30%・器用 -4"}, {"呪縛": "受けるダメージ -50%・与えるダメージ -20%"},
     {"火事場": "受けるダメージ -40%（体力 30% 以下）"})
player.current_hp = 100
check("軽減は足さずに順に掛ける", blow("主人公") == 42, seen)
check("種類が違えば全部効いても 0 にならない", effects.after_cuts(200, [90, 90, 90]) >= 1)

wear(player, {"耐性": "状態異常を防ぐ 100%"})
player.status = {"もとから": {"duration": 3}}
ally.status = {}
resolve = ctx.hooks["__main__:BattlePhaseManager.resolve_battle_effect"]


def curses(self):
    player.status["麻痺"] = {"duration": 2}
    ally.status["麻痺"] = {"duration": 2}


said.clear()
handle(lambda s, k, side, a: resolve(curses, s), manager, "ゴブリン1", "敵側", {})
check("耐性: 新しい状態異常を防ぐ", set(player.status) == {"もとから"}, player.status)
check("耐性: 着ていない者には付く", "麻痺" in ally.status, ally.status)
check("耐性: 本文に出る", any("防いだ" in text for text in said), said)
player.status = {}
handle(lambda s, k, side, a: resolve(curses, s), manager, "主人公", "味方陣営", {})
check("耐性: 味方の手で付いたものは外さない", "麻痺" in player.status, player.status)

wear(player, {"威光": "店の買値 -10%"})
registry = sys.modules["instantale_modloader.prices"]._item_registry()
layer = next(entry[1] for entry in registry["adjust"] if entry[0] == package)
ui.find_app = lambda: app
check("威光: 買値が下がる", layer(None, prices.BUY, 1000) == 900)
check("威光: 売値は触らない", layer(None, prices.SELL, 1000) is None)
wear(player)
check("威光: 着ていなければ触らない", layer(None, prices.BUY, 1000) is None)
wear(ally, {"威光": "店の買値 -10%"})
check("威光: 仲間が着ていても効かない", layer(None, prices.BUY, 1000) is None)

print("防具の効果: 抽選")
armor_rolls = [effects.roll("mythic", {"mythic": (2, 2)}, effects.ARMOR_KINDS, rng) for _ in range(300)]
check("防具の10種類とも出る", {k for r in armor_rolls for k, _, _ in r} == set(effects.ARMOR_KINDS))
check("武器10種・防具10種", len(effects.WEAPON_KINDS) == 10 and len(effects.ARMOR_KINDS) == 10)
for kind in effects.KINDS:
    check_item = Item("読み取り", "wearable", "mythic", {})
    effects.write(check_item, [(kind, 7, "boss" if kind == "slayer" else None)], "ja")
    english_item = Item("読み取り", "wearable", "mythic", {})
    effects.write(english_item, [(kind, 7, "boss" if kind == "slayer" else None)], "en")
    check("書いて読む: " + kind, effects.read(check_item) == [(kind, 7, "boss" if kind == "slayer" else None)]
          and effects.read(english_item) == effects.read(check_item), (check_item.attributes, english_item.attributes))
check("例外が出ていない（新しい効果）", not ctx.errors, ctx.errors)

if failures:
    print("{} failed: {}".format(len(failures), failures))
    sys.exit(1)
print("all ok")
