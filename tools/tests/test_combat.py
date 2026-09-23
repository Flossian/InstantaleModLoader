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

# 装備の操作: 置いていなければ None（聞く側が自分で書く）。答えはそのまま返す。例外は None
item = types.SimpleNamespace(id="k1")
assert combat.toggle(app, npc, item) is None and combat.equipped(app, npc, item) is None
combat.declare(combat.TOGGLE, lambda a, h, i: "equipped" if i is item else None, owner="912")
combat.declare(combat.EQUIPPED, lambda a, h, i: True, owner="912")
assert combat.toggle(app, npc, item) == "equipped" and combat.toggle(app, npc, None) is None
assert combat.equipped(app, npc, item) is True
combat.declare(combat.TOGGLE, broken, owner="912")
assert combat.toggle(app, npc, item) is None
assert sorted(combat.forget("912")) == [combat.EQUIPPED, combat.TOGGLE]

# 身に着けている品: 置いていなければ None。組の並びは (部位, 品) の文字列化、品が None の組は落とす。崩れた答えは None
assert combat.gear(app, npc) is None
combat.declare(combat.GEAR, lambda a, h: [("right_hand", item), ("head", None)], owner="912")
assert combat.gear(app, npc) == [("right_hand", item)]
combat.declare(combat.GEAR, lambda a, h: [], owner="912")
assert combat.gear(app, npc) == []                                         # 装備欄はあるが何も着けていない
combat.declare(combat.GEAR, lambda a, h: 5, owner="912")
assert combat.gear(app, npc) is None
combat.declare(combat.GEAR, broken, owner="912")
assert combat.gear(app, npc) is None
assert combat.forget("912") == [combat.GEAR]
print("ok")
