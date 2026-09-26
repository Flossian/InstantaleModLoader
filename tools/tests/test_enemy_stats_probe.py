# -*- coding: utf-8 -*-
"""236_probe_enemy_stats をゲーム抜きで通す。

    python tools/tests/test_enemy_stats_probe.py

偽の app / Character を差し込み、次を確認する。

  生まれ方 … 能力値を作る関数の引数と戻りが `gen` の行になり、戻りは変えない
  開始     … 戦闘の開始で敵と味方の値（防御・スキルの強度と残り回数を含む）が `start` の行になる。
             大きい項目（人生ログなど）は写さない
  1手      … 裁きと、その中で呼ばれた `get_instant_damage` の引数と戻りが1つの `act` の行に束なる。
             `calculate` の中で `resolve` が呼ばれても1行
  素通し   … 写しで例外が出ても本体の戻り値は変えない
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod は **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


spec = importlib.util.spec_from_file_location("probe_enemy_stats_under_test",
                                              find_mod("_probe_enemy_stats"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx(object):
    def __init__(self):
        self.hooks, self.rows, self.lines, self.errors = {}, [], [], []

    def logger(self, name, **kw):
        return self.lines.append

    def jsonl(self, name, **kw):
        return self.rows.append

    def out_path(self, *parts):
        return os.path.join("out", *parts)

    def log(self, msg, level="INFO"):
        self.lines.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


class Character(object):
    def __init__(self, name, hp, defense, level=77):
        self.name = name
        self.max_hp = hp
        self.current_hp = hp
        self.experience_level = level
        self.original_ability_scores = {"strength": 30, "constitution": 12}
        self.life_log = ["長い人生" * 100]
        self.skills = {"通常攻撃": {"effects": [{"type": "instant_damage", "power": "weak",
                                             "target_type": "single_enemy"}],
                                "current_uses": 16, "max_uses": 16}}
        self._defense = defense

    def get_npc_defense(self):
        return self._defense


golem = Character("ゴーレム", 1376, 243)
hero = Character("アーリ", 1832, 390, level=83)
app = types.SimpleNamespace(current_enemy_dict={"ゴーレム": golem}, player=hero,
                            in_battle=1, in_boss_battle=0)
mod.ui = types.SimpleNamespace(find_app=lambda: app, party_member_ids=lambda a: [],
                               character_of=lambda a, i: None,
                               current_area=lambda a: types.SimpleNamespace(name="砂漠"))
ctx = Ctx()
mod.apply(ctx)

print("生まれ方")
got = ctx.hooks["scripts.functions:get_character_attributes"](
    lambda base, arch: {"strength": 14}, 11.37, "balanced")
row = ctx.rows[-1]
check("戻りはそのまま", got == {"strength": 14})
check("gen の行に関数名・引数・戻り", row["kind"] == "gen" and row["fn"] == "get_character_attributes"
      and row["args"] == [11.37, "balanced"] and row["result"] == {"strength": 14}, row)

print("開始")
manager = types.SimpleNamespace(app=app, enemy_type="in_quest")
check("戻りはそのまま", ctx.hooks["__main__:BattleStartManager.start_battle"](
    lambda self: "started", manager) == "started")
row = ctx.rows[-1]
enemy = row.get("enemies", {}).get("ゴーレム", {})
check("start の行に敵の防御と HP", row["kind"] == "start" and enemy.get("get_npc_defense") == 243
      and enemy["fields"].get("max_hp") == 1376, row)
check("戦闘の種類と旗", row["enemy_type"] == "in_quest" and row["flags"] == ["in_battle"], row)
check("スキルは強度と残り回数に縮める",
      enemy["fields"]["skills"]["通常攻撃"] == [("instant_damage", "weak", "single_enemy"), 16, 16],
      enemy["fields"].get("skills"))
check("大きい項目は写さない", "life_log" not in enemy["fields"], sorted(enemy["fields"]))
check("味方も写す", [a["name"] for a in row["allies"]] == ["アーリ"], row["allies"])

print("1手")
action = {"actor": "ゴーレム", "instant_damage": [
    {"target": ["アーリ"], "power": "strong", "multiplier": 1, "category": "physical"}]}
math = ctx.hooks["scripts.functions:get_instant_damage"]


def resolve_like(self, battle_action):
    return math(lambda a, d: a - d, 966, 533)


def calculate_like(self, battle_action):
    return ctx.hooks["__main__:BattlePhaseManager.resolve_battle_effect"](
        resolve_like, self, battle_action)


before = len(ctx.rows)
got = ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"](
    calculate_like, object(), action)
acts = [r for r in ctx.rows[before:] if r["kind"] == "act"]
check("戻りはそのまま", got == 433)
check("入れ子でも1行に束ねる", len(acts) == 1, acts)
check("裁きと素の引数が並ぶ", acts and acts[0]["actor"] == "ゴーレム"
      and acts[0]["entries"][0]["power"] == "strong"
      and acts[0]["calls"] == [{"fn": "get_instant_damage", "args": [966, 533], "result": 433}],
      acts)
check("calculate の行に battle_action の全体と戻り（版2）",
      acts and acts[0]["action"]["actor"] == "ゴーレム"
      and acts[0]["action"]["instant_damage"][0]["power"] == "strong"
      and acts[0]["result"] == 433 and "action_after" in acts[0], acts)
check("この試しでは resolve が HP を動かしていないので、変化は無しと録る",
      acts and acts[0]["state_changed"] == {}, acts)


def calculate_hurts(self, battle_action):
    hero.current_hp -= 100          # 素点の段が HP を動かしたら、それが残ること
    battle_action["raw"] = 777      # battle_action に書き込んだら、呼んだ後の写しに残ること
    return None


before = len(ctx.rows)
ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"](
    calculate_hurts, types.SimpleNamespace(app=app), dict(action))
act = [r for r in ctx.rows[before:] if r["kind"] == "act"][0]
check("calculate の前後で HP が動けば state_changed に出る",
      act["state_changed"].get("アーリ", [{}, {}])[1].get("current_hp") == 1732, act["state_changed"])
check("battle_action への書き込みは呼んだ後の写しに出る",
      act["action_after"].get("raw") == 777 and "raw" not in act["action"], act)
hero.current_hp = 1832
check("手の外の計算は録らない",
      math(lambda a, d: a - d, 10, 3) == 7 and len(ctx.rows) == before + 1)

print("素通し")


class Broken(Character):
    def get_npc_defense(self):
        raise RuntimeError("boom")


app.current_enemy_dict = {"壊れ": Broken("壊れ", 10, 0)}
check("写しで例外が出ても戻りはそのまま", ctx.hooks["__main__:BattleStartManager.start_battle"](
    lambda self: "ok", manager) == "ok")
check("防御の例外は値の代わりに残る",
      ctx.rows[-1]["enemies"]["壊れ"]["get_npc_defense"] == "error: RuntimeError", ctx.rows[-1])

print("試し打ち（版3）")


class FakePhase(object):
    """ゲームの BattlePhaseManager の代わり。素点だけ返す（版2の実機の戻りの形）。"""
    seen = []

    def calculate_battle_effect(self, battle_action):
        FakePhase.seen.append(getattr(self, "app", None))
        entry = battle_action["instant_damage"][0]
        raw = {"weak": 100, "normal": 120, "strong": 150, "very_strong": 170,
               "extreme": 230}[entry["power"]] * entry["multiplier"]
        if entry["category"] == "magical":
            raw += 1
        return [{entry["target"]: raw}, {}, {}, {}, {}, {}, {}, {}]


app.current_enemy_dict = {"ゴーレム": golem}
mod.ui.cls_of = lambda name: FakePhase if name == "BattlePhaseManager" else None
mod.DRY_RUN, mod.DRY_REPEAT = True, 2
before = len(ctx.rows)
check("戻りはそのまま", ctx.hooks["__main__:BattleStartManager.start_battle"](
    lambda self: "go", manager) == "go")
dry = [r for r in ctx.rows[before:] if r["kind"] == "dry"]
plans = len(mod.DRY_POWERS) * len(mod.DRY_CATEGORIES) + len(mod.DRY_EXTRA)
check("敵と味方の全員ぶん、全部の通りを録る", len(dry) == plans * 2, len(dry))
row = [r for r in dry if r["actor"] == "ゴーレム" and r["power"] == "extreme"
       and r["category"] == "magical"][0]
check("敵の手は主人公を相手に、回数ぶんの素点が並ぶ", row["raws"] == [231, 231] and row["side"] == "enemy", row)
row = [r for r in dry if r["actor"] == "アーリ" and r["multiplier"] == 2][0]
check("味方の手は先頭の敵を相手に、倍率も渡る", row["raws"] == [200, 200] and row["side"] == "ally", row)
check("実体には app を持たせて呼ぶ", FakePhase.seen and FakePhase.seen[-1] is app)
check("試し打ちの間は act の行を録らない", not [r for r in ctx.rows[before:] if r["kind"] == "act"])


class HurtingPhase(FakePhase):
    def calculate_battle_effect(self, battle_action):
        hero.current_hp -= 1
        return FakePhase.calculate_battle_effect(self, battle_action)


mod.ui.cls_of = lambda name: HurtingPhase
before = len(ctx.rows)
ctx.hooks["__main__:BattleStartManager.start_battle"](lambda self: "go", manager)
check("前後で HP が動いたら止めて記録に残す",
      [r["kind"] for r in ctx.rows[before:]][-1] == "dry_abort", [r["kind"] for r in ctx.rows[before:]])
before = len(ctx.rows)
ctx.hooks["__main__:BattleStartManager.start_battle"](lambda self: "go", manager)
check("止めた後は試し打ちしない", not [r for r in ctx.rows[before:] if r["kind"].startswith("dry")])
hero.current_hp = 1832
mod.DRY_RUN = False

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all good")
