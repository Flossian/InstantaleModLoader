# -*- coding: utf-8 -*-
"""`instantale_modloader.combat`（戦闘の数の窓口）。

置く側と聞く側が互いの名前を知らずに繋がること、誰も置いていなければ None になること、
壊れた答え（例外・負の数・数でない）は None に落ちること、後から置いたほうが勝つことを見る。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))

from instantale_modloader import combat  # noqa: E402

if hasattr(sys, combat._ATTR):
    delattr(sys, combat._ATTR)
app = types.SimpleNamespace()
npc = types.SimpleNamespace(name="仲間")
logged = []

assert combat.attack(app, npc) is None and combat.defense(app, npc) is None   # 誰も置いていない
assert combat.source_of(combat.ATTACK) == ""

combat.declare(combat.ATTACK, lambda a, h: 480.0 if h is npc else None, owner="912", write=logged.append)
combat.declare(combat.DEFENSE, lambda a, h: "bad", owner="912")
assert combat.attack(app, npc) == 480.0
assert combat.attack(app, types.SimpleNamespace()) is None                  # 装備が無い人物
assert combat.attack(app, None) is None
assert combat.defense(app, npc) is None                                     # 数でない答えは None
assert combat.source_of(combat.ATTACK) == "912"

def broken(a, h):
    raise RuntimeError("boom")
combat.declare(combat.DEFENSE, broken, owner="912")
assert combat.defense(app, npc) is None                                     # 例外も None（ゲームのまま）
combat.declare(combat.DEFENSE, lambda a, h: -5, owner="912")
assert combat.defense(app, npc) is None                                     # 0 以下は装備なし扱い

before = combat.declare(combat.ATTACK, lambda a, h: 1.0, owner="other", write=logged.append)
assert before == "912" and any("now decided by 'other'" in l for l in logged)
assert combat.attack(app, npc) == 1.0                                       # 後勝ち
assert combat.forget("other", write=logged.append) == [combat.ATTACK]
assert combat.attack(app, npc) is None and combat.source_of(combat.ATTACK) == ""
assert combat.forget("912") == [combat.DEFENSE]

# 敵ごとの率。受け持つ MOD は何本でもよく、最初に数を返した答えを使う。誰も答えなければ聞く側の既定
hunter, guard = types.SimpleNamespace(name="刺客"), types.SimpleNamespace(name="衛兵")
assert combat.enemy_percent(combat.ENEMY_HP, app, hunter, 250) == 250
assert combat.enemy_scaler() == ""
combat.declare_enemy(combat.ENEMY_HP, lambda a, e: None, owner="first")
combat.declare_enemy(combat.ENEMY_HP, lambda a, e: 312.5 if e is hunter else None, owner="339")
combat.declare_enemy(combat.ENEMY_DAMAGE, lambda a, e: 20 if e is hunter else None, owner="339")
assert combat.enemy_percent(combat.ENEMY_HP, app, hunter, 250) == 312.5
assert combat.enemy_percent(combat.ENEMY_HP, app, guard, 250) == 250            # 受け持たない敵は既定
assert combat.enemy_percent(combat.ENEMY_DAMAGE, app, hunter, 12) == 20.0
assert combat.enemy_percent(combat.ENEMY_DAMAGE, app, None, 12) == 12
combat.declare_enemy(combat.ENEMY_DAMAGE, broken, owner="first")
assert combat.enemy_percent(combat.ENEMY_DAMAGE, app, hunter, 12) == 20.0       # 例外の答えは飛ばす
try:
    combat.declare_enemy("attack", lambda a, e: 1, owner="x")
    raise AssertionError("declare_enemy took a non-enemy kind")
except ValueError:
    pass
combat.declare(combat.ENEMY_SCALER, lambda a, h: True, owner="319")
assert combat.enemy_scaler() == "319"
assert "339" in combat.owners() and "first" in combat.owners()
assert sorted(combat.forget("339")) == [combat.ENEMY_DAMAGE, combat.ENEMY_HP]
assert combat.enemy_percent(combat.ENEMY_HP, app, hunter, 250) == 250
combat.forget("first")
combat.forget("319")
assert combat.enemy_scaler() == "" and combat.owners() == []

print("ok")
