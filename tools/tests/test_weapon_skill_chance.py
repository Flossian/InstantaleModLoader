# -*- coding: utf-8 -*-
"""410_weapon_skill_chance をゲーム抜きで通す。

    python tools/tests/test_weapon_skill_chance.py

確認するもの:

  倍率       … INT と DEX の段（必ず・0・3倍・2倍・そのまま）。窓口の補正を足した値で読む
  経路       … 主人公と敵に作った武器は other、世界の人物（店の主・仲間）は shop、クラフトの最中は craft、
               店の入口の中は入口を抜けてから shop。店には倍率を掛けない
  付ける     … 当たればゲームのスキル生成の結果を付け、説明欄のスキル名は値段の手前。外れも引き直さない
  説明欄     … 描く間だけ効果と使用回数の行を足し、抜けたら戻す
  仲間       … 戦闘の間だけ身に着けている品のスキルを技に足す。同じ名前の技は本人を残す。
               保存の間は外す。休養で回数を戻す
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "weapon_skill_chance"))

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


class Item(object):
    def __init__(self, name, item_type="weapon", value=40, rarity="rare", skill=None):
        self.name = name
        self.item_type = item_type
        self.value = value
        self.rarity = rarity
        self.skill = skill
        self.description = "試しの品"
        self.attributes = {"item_detail": "small_weapon", "攻撃力": 100, "売価": 900}


class Person(object):
    def __init__(self, name, ident=None, intelligence=16, dexterity=16):
        self.name = name
        if ident is not None:
            self.id = ident
        self.ability_scores = {"strength": 10, "dexterity": dexterity, "constitution": 10,
                               "intelligence": intelligence, "wisdom": 10, "charisma": 10}
        self.skills = {}


class FakeCtx:
    _mod = "410_weapon_skill_chance"

    def __init__(self, out_dir):
        self.out_dir = out_dir
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


generated_for = []


def item_skill_generator(item, usefulness):
    generated_for.append((getattr(item, "name", item), usefulness))
    return {"name": "{}の技".format(getattr(item, "name", "?")), "element": "fire",
            "skill_type": "attack", "max_uses": 3,
            "effects": [{"type": "instant_damage", "target_type": "single_enemy", "power": "normal"}]}


sys.modules["scripts.languages"] = types.SimpleNamespace(language="ja", skill_effect_to_text=lambda e: "")
sys.modules["scripts.functions"] = types.SimpleNamespace(get_item_skill_usefulness=lambda v, r: "normal")
sys.modules["scripts.llm.llm_manager"] = types.SimpleNamespace(item_skill_generator=item_skill_generator)

folder, path = find_mod("_weapon_skill_chance")
spec = importlib.util.spec_from_file_location("mod410", path)
mod = importlib.util.module_from_spec(spec)
sys.modules["mod410"] = mod
spec.loader.exec_module(mod)
for attr in (mod.STORE_ATTR, abilities._REGISTRY_ATTR):
    if hasattr(sys, attr):
        delattr(sys, attr)

player = Person("主人公", intelligence=16, dexterity=16)
ally = Person("仲間", ident=95)
keeper = Person("店主", ident=3)
enemy = Person("敵の剣士")
app = types.SimpleNamespace(player=player,
                            world=types.SimpleNamespace(characters={"3": keeper, "95": ally}))
ui.find_app = lambda: app
ui.party_member_ids = lambda _app: ["95"]
ui.character_of = lambda _app, cid: ally if str(cid) == "95" else None

print("倍率")
check("どちらかが 60 以上なら必ず", mod.ability_rate(60, 5) == (None, "sure"))
check("どちらかが 10 以下なら 0", mod.ability_rate(30, 10) == (0.0, "low"))
check("合計 61 以上は3倍", mod.ability_rate(31, 30) == (3.0, "triple"))
check("どちらも 25 以上は2倍", mod.ability_rate(25, 25) == (2.0, "double"))
check("それ以外はそのまま", mod.ability_rate(16, 16) == (1.0, "base"))
check("能力値は素の値", mod.score_of(player, "intelligence") == 16)
abilities.declare("411_gear", lambda a, h: {"intelligence": 10, "dexterity": 9} if h is player else None)
check("窓口の補正を足して読む", mod.score_of(player, "intelligence") == 26
      and mod.score_of(player, "dexterity") == 25)

print("経路")
shutil.rmtree(OUT_DIR, ignore_errors=True)
ctx = FakeCtx(OUT_DIR)
mod.CHANCE_CRAFT, mod.CHANCE_OTHER, mod.CHANCE_SHOP = 100.0, 100.0, 0.0
mod.apply(ctx)
generated = ctx.hooks["__main__:InstantaleApp.generate_item_from_item_data"]
craft = ctx.hooks["__main__:ItemCraftManager.execute"]
shopping = ctx.hooks["__main__:ShoppingStartManagerRemake.generate_item_in_shopping"]
item_init = ctx.hooks["scripts.items:Item.__init__"]


def make(name):
    def maker(self, *args, **kwargs):
        item = Item(name)
        item_init(lambda s, *a, **k: None, item)
        return item
    return maker


def in_worker(fn):
    """抽選はワーカースレッドならその場で付ける（メインスレッドは Clock へ回す）。"""
    out = []
    thread = threading.Thread(target=lambda: out.append(fn()))
    thread.start()
    thread.join()
    return out[0]


def gen(name, obtainer):
    args = (name, "説明", "weapon", "small_weapon", 40, "外見", "rare", obtainer)
    return in_worker(lambda: generated(make(name), app, *args))


hero_item = gen("主人公の剣", player)
check("主人公に作った武器は other（100% で付く）", bool(hero_item.skill), hero_item.attributes)
check("スキル名は値段の手前", list(hero_item.attributes)[-2:] == ["スキル", "売価"])
keeper_item = gen("店主の剣", keeper)
check("主人公以外に作った武器は shop（0% で付かない）", keeper_item.skill is None)
ally_item = gen("仲間の剣", ally)
check("仲間に作った武器も shop", ally_item.skill is None)
enemy_item = gen("敵の剣", enemy)
check("敵（世界の人物に居ない）に作った武器は other", bool(enemy_item.skill))
mod.CHANCE_SHOP = 100.0
mod.ABILITY_SCALING = True
player.ability_scores.update(intelligence=5, dexterity=5)       # 倍率なら 0% になる
abilities.forget("411_gear")
shop_item = gen("店主の槍", keeper)
check("店には倍率を掛けない（0% の段でも 100%）", bool(shop_item.skill))
mod.CHANCE_SHOP = 0.0
other_item = gen("拾った斧", player)
check("主人公の武器には倍率が掛かる（0% の段）", other_item.skill is None)
player.ability_scores.update(intelligence=16, dexterity=16)

crafted = in_worker(lambda: craft(lambda self, *a: generated(
    make("作った剣"), app, "作った剣", "", "weapon", "small_weapon", 40, "", "rare", player), None))
check("クラフトの最中は craft", bool(crafted.skill))
log = io.open(os.path.join(OUT_DIR, mod.LOG_BASENAME), encoding="utf-8").read()
check("ログに経路と受け取る人が残る", "craft: '作った剣' won" in log and "other: '主人公の剣' won" in log
      and "for='店主'" in log)

owner = Person("店の主", ident=4)
owner.inventory = types.SimpleNamespace(inventory={})


def stock(self, item_data, shop_owner_instance, tier):
    item = generated(make("店の短剣"), app, "店の短剣", "", "weapon", "small_weapon", 40, "", "rare",
                     shop_owner_instance)
    shop_owner_instance.inventory.inventory["item_9"] = item


before = len(generated_for)
mod.CHANCE_SHOP = 100.0
in_worker(lambda: shopping(stock, None, {}, owner, 1))
check("店の入口の中は入口を抜けてから1回だけ抽選", len(generated_for) == before + 1
      and bool(owner.inventory.inventory["item_9"].skill))
again = len(generated_for)
in_worker(lambda: generated(lambda self, *a: hero_item, app, "主人公の剣", "", "weapon", "", 40, "", "rare", player))
check("スキルのある品は引き直さない", len(generated_for) == again)

print("説明欄")
shown = mod.ShownEffect(hero_item)
with shown:
    rows = list(hero_item.attributes)
check("描く間だけ効果と使用回数", rows == ["item_detail", "攻撃力", "スキル", "効果", "使用回数", "売価"], rows)
check("抜けたら戻す", list(hero_item.attributes) == ["item_detail", "攻撃力", "スキル", "売価"])
check("効果の文（自前の言い方）", "敵単体に中威力のダメージを与える。" in mod.effect_text(hero_item.skill))

print("仲間")
worn = {id(ally): [("right_hand", hero_item), ("free1", crafted)]}
equipment.gear = lambda _app, holder: worn.get(id(holder))
ally.skills = {"作った剣の技": {"name": "本人の技"}}
start = ctx.hooks[mod.BATTLE_START_TARGET]
start(lambda self, *a: None, types.SimpleNamespace(app=app))
check("戦闘の間だけ技に足す（自由枠は数えない）", ally.skills.get("主人公の剣の技") is hero_item.skill
      and len(ally.skills) == 2)
save = ctx.hooks[mod.SAVE_TARGET]
seen = []
save(lambda self: seen.append(dict(ally.skills)), app)
check("保存の間は外す", "主人公の剣の技" not in seen[0] and "主人公の剣の技" in ally.skills)
hero_item.skill["current_uses"] = 0
rest = ctx.hooks[mod.REST_TARGET]
rest(lambda self, *a: None, types.SimpleNamespace(app=app))
check("休養で回数を戻す", hero_item.skill["current_uses"] == 3)
end = ctx.hooks[mod.BATTLE_END_TARGETS[0]]
end(lambda self: None, None)
check("戦闘の終わりで外す（本人の技は残る）", list(ally.skills) == ["作った剣の技"])
check("例外が出ていない", not ctx.errors, ctx.errors)

if failures:
    print("{} failed: {}".format(len(failures), failures))
    sys.exit(1)
print("all ok")
