# -*- coding: utf-8 -*-
"""ローダの窓口 battle_effects（TECH.md §3.3.28）の、品に付かない種類をゲーム抜きで通す。

    python tools/tests/test_battle_effects.py

品の効果（会心・棘など）の戦闘の1手は test_equipment_bonuses.py、特質の答え方は test_player_trait_effects.py。
ここは答える MOD を偽物にして、特質だけに開く種類を確かめる。

  表記・上限   … 名前と値の表記。隙・反動・回復阻害の上限
  脆さ         … 軽減の後に上乗せする
  隙           … 手の最初の一撃で1回だけ引き、その手の敵への一撃をすべて 0 にする。行が出る
  回復         … ゲームの回復（resolve_battle_effect の前後の増え）と、窓口の吸収・再生を直す。窓口の分を二重に直さない
  反動         … check_battle_end が終わりを返したときに1回。逃げて預かりに入った者も払う。体力 1 で止まる
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "battle_effects"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import battle_effects, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx:
    def __init__(self):
        self.hooks = {}
        self.lines = []

    def logger(self, name, **kw):
        return self.lines.append

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, func)
            return func
        return decorator


class FixedRng:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


errors = []
battle_effects.log_exc = errors.append

ctx = FakeCtx()
battle_effects.install(ctx)
handle = ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"]
calc = ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"]
npc_defense = ctx.hooks["scripts.characters:Character.get_npc_defense"]
instant = ctx.hooks["scripts.functions:get_instant_damage"]
resolve = ctx.hooks["__main__:BattlePhaseManager.resolve_battle_effect"]
reduce_status = ctx.hooks["__main__:BattlePhaseManager.reduce_status_turns_and_log"]
start_battle = ctx.hooks["__main__:BattleStartManager.start_battle"]
battle_end = ctx.hooks.get("__main__:BattlePhaseManager.check_battle_end")

player = types.SimpleNamespace(name="試験太郎", current_hp=100, max_hp=300, status={}, experience_level=10)
foe = types.SimpleNamespace(name="ゴブリン", current_hp=500, max_hp=500, status={}, experience_level=5)
said = []
app = types.SimpleNamespace(player=player, party={"player": player}, current_enemy_dict={"ゴブリン1": foe},
                            escaped_member_in_battle={}, in_boss_battle=False, add_text=said.append)
manager = types.SimpleNamespace(app=app)
ui.party_member_ids = lambda _app: []
ui.find_app = lambda: app

answers = {}
battle_effects.declare("test", lambda _app, holder: answers.get(id(holder)))


def give(**found):
    answers[id(player)] = found


def enemy_hit(raw=200):
    """敵の手。主人公への一撃（防具の値で来るので素点で照らす）。"""
    seen = {}

    def turn(self, key, side, action):
        calc(lambda s, a: ({"試験太郎": raw},), self, action)
        seen["hit"] = instant(lambda attack, defense: attack, raw, 0)
    handle(turn, manager, "ゴブリン1", "敵側", {})
    return seen["hit"]


def strike(hits=1, raw=400, defense=100):
    """主人公の手。戻りは敵が受けるダメージの並び。"""
    got = []

    def turn(self, key, side, action):
        for _ in range(hits):
            dealt = instant(lambda attack, d: attack - d, raw, npc_defense(lambda s: defense, foe))
            foe.current_hp -= dealt
            got.append(dealt)
    handle(turn, manager, "試験太郎", "味方陣営", {})
    return got


print("表記・上限")
check("名前", battle_effects.LABELS["ja"]["fragile"] == "脆さ" and battle_effects.LABELS["ja"]["falter"] == "隙"
      and battle_effects.LABELS["ja"]["unmend"] == "回復阻害" and battle_effects.LABELS["ja"]["backlash"] == "反動"
      and battle_effects.LABELS["ja"]["mend"] == "回復促進")
check("値の表記", battle_effects.text_of("fragile", 10, None, "ja") == "受けるダメージ +10%"
      and battle_effects.text_of("falter", 5, None, "ja") == "攻撃が空を切る 5%"
      and battle_effects.text_of("unmend", 30, None, "ja") == "受ける回復 -30%"
      and battle_effects.text_of("backlash", 10, None, "ja") == "戦闘の後 最大体力の 10% を失う")
check("代償の種類", set(battle_effects.COST_KINDS) == {"fragile", "falter", "unmend", "backlash"}
      and all(kind in battle_effects.KINDS for kind in battle_effects.COST_KINDS))
check("隙・反動は 50、回復阻害は 90 で止まる",
      battle_effects.merge([{"falter": 40, "backlash": 30, "unmend": 60}, {"falter": 40, "backlash": 30, "unmend": 60}])
      == {"falter": 50, "backlash": 50, "unmend": 90})
check("回復の増減", battle_effects.heal_rate({"mend": 30, "unmend": 50}) == -20
      and battle_effects.healed(100, -20) == 80 and battle_effects.healed(100, -100) == 0
      and battle_effects.healed(0, 30) == 0)

print("脆さ")
give(fragile=20)
check("受けるダメージ +20%（200 → 240）", enemy_hit() == 240)
give(fragile=20, reduce=15)
check("軽減の後に上乗せ（200 × 0.85 × 1.2 = 204）", enemy_hit() == 204)
give()
check("効果が無ければ触らない", enemy_hit() == 200)

print("隙")
battle_effects.RNG = FixedRng(0.0)
said.clear()
foe.current_hp = 500
give(falter=10)
check("引けば、その手の敵への一撃がすべて 0", strike(hits=2) == [0, 0])
check("行は手に1回", sum("空を切った" in text for text in said) == 1, said)
check("敵の体力は減らない", foe.current_hp == 500, foe.current_hp)
battle_effects.RNG = FixedRng(0.99)
said.clear()
check("引かなければ普段どおり", strike() == [300] and not any("空を切った" in text for text in said))
battle_effects.RNG = FixedRng(0.0)
give(falter=10, critical=100)
said.clear()


def critical_turn(self, key, side, action):
    calc(lambda s, a: ({},), self, {"instant_damage": [{"target": "ゴブリン1", "power": "normal"}]})
    foe.current_hp -= instant(lambda attack, d: attack - d, 400, npc_defense(lambda s: 100, foe))


handle(critical_turn, manager, "試験太郎", "味方陣営", {})
check("空振りの手では会心の行も出ない", not any("会心" in text for text in said), said)
battle_effects.RNG = FixedRng(0.99)

print("回復")


def heal_by(amount):
    def game_resolve(self, *args):
        player.current_hp += amount
    return game_resolve


player.current_hp, player.max_hp = 100, 300
give(unmend=50)
said.clear()
handle(lambda s, k, side, a: resolve(heal_by(100), s), manager, "試験太郎", "味方陣営", {})
check("回復阻害: ゲームの回復 100 → 50", player.current_hp == 150, player.current_hp)
check("回復阻害: 行が出る", any("阻害され" in text and "50" in text for text in said), said)
give(mend=30)
player.current_hp = 100
handle(lambda s, k, side, a: resolve(heal_by(100), s), manager, "試験太郎", "味方陣営", {})
check("回復促進: 100 → 130", player.current_hp == 230, player.current_hp)
player.current_hp = 250
handle(lambda s, k, side, a: resolve(heal_by(40), s), manager, "試験太郎", "味方陣営", {})
check("回復促進: 最大体力で止まる", player.current_hp == 300, player.current_hp)
give(unmend=50)
player.current_hp = 100
handle(lambda s, k, side, a: resolve(heal_by(-30), s), manager, "ゴブリン1", "敵側", {})
check("減った手は触らない", player.current_hp == 70, player.current_hp)

give(unmend=50, lifesteal=50)
player.current_hp, foe.current_hp = 100, 500


def lifesteal_turn(self, key, side, action):
    def game_resolve(manager_self, *args):
        foe.current_hp -= instant(lambda attack, d: attack - d, 400, npc_defense(lambda s: 100, foe))
    resolve(game_resolve, self)


handle(lifesteal_turn, manager, "試験太郎", "味方陣営", {})
check("吸収も直し、二重には直さない（300 の 50% = 150 → 75）", player.current_hp == 175, player.current_hp)

give(unmend=50, regen=10)
player.current_hp = 100
reduce_status(lambda s, c: None, manager, player)
check("再生も直す（最大 300 の 10% = 30 → 15）", player.current_hp == 115, player.current_hp)

give()
player.status = {}
battle_effects._mark(player, "venom", 3)
player.current_hp = 200
import threading  # noqa: E402
sys._instantale_probe_dry_run_thread = threading.get_ident()
try:
    reduce_status(lambda s, c: None, manager, player)
finally:
    sys._instantale_probe_dry_run_thread = None
check("試し打ちの最中は毒刃で体力を削らない", player.current_hp == 200, player.current_hp)
reduce_status(lambda s, c: None, manager, player)
check("  → 試し打ちでなければ削る", player.current_hp < 200, player.current_hp)
player.status = {}

print("反動")
check("戦闘の終わりを包む", battle_end is not None)
give(backlash=10)
player.current_hp = 200
said.clear()
start_battle(lambda s: None, manager)
battle_end(lambda s: None, manager)
check("終わっていなければ払わない", player.current_hp == 200, player.current_hp)
battle_end(lambda s: True, manager)
check("終わりで最大体力の 10% を失う（200 → 170）", player.current_hp == 170, player.current_hp)
check("行が出る", any("反動" in text and "30" in text for text in said), said)
battle_end(lambda s: True, manager)
check("同じ戦闘では1回だけ", player.current_hp == 170, player.current_hp)
strike()
battle_end(lambda s: True, manager)
check("次の戦闘の手が来れば、また払う", player.current_hp == 140, player.current_hp)
app.party, app.escaped_member_in_battle = {}, {"player": player}
start_battle(lambda s: None, manager)
battle_end(lambda s: True, manager)
check("逃げて預かりに入った主人公も払う", player.current_hp == 110, player.current_hp)
app.party, app.escaped_member_in_battle = {"player": player}, {}
player.current_hp = 5
start_battle(lambda s: None, manager)
battle_end(lambda s: True, manager)
check("体力 1 で止まる", player.current_hp == 1, player.current_hp)
give()
player.current_hp = 200
start_battle(lambda s: None, manager)
battle_end(lambda s: True, manager)
check("効果が無ければ触らない", player.current_hp == 200, player.current_hp)

check("握り潰した例外が無い", not errors, errors[:2])
print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
