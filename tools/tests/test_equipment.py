# -*- coding: utf-8 -*-
"""`instantale_modloader.equipment`（装備欄の窓口）。

置く側と聞く側が互いの名前を知らずに繋がること、誰も置いていなければ None になること、
壊れた答え（例外・崩れた形）は None に落ちること、後から置いたほうが勝つこと、
戦闘の数の窓口（`combat`）とは置き場が別であることを見る。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))

from instantale_modloader import combat, equipment  # noqa: E402

for module in (combat, equipment):
    if hasattr(sys, module._ATTR):
        delattr(sys, module._ATTR)
app = types.SimpleNamespace()
npc = types.SimpleNamespace(name="仲間")
item = types.SimpleNamespace(id="k1")
logged = []


def broken(*_args):
    raise RuntimeError("boom")


# 装備の操作: 置いていなければ None（聞く側が自分で書く）。答えはそのまま返す。例外は None
assert equipment.toggle(app, npc, item) is None and equipment.equipped(app, npc, item) is None
assert equipment.equip(app, npc, item) is None
equipment.declare(equipment.TOGGLE, lambda a, h, i: "equipped" if i is item else None, owner="912")
equipment.declare(equipment.EQUIPPED, lambda a, h, i: True, owner="912")
assert equipment.toggle(app, npc, item) == "equipped" and equipment.toggle(app, npc, None) is None
assert equipment.equipped(app, npc, item) is True
equipment.declare(equipment.TOGGLE, broken, owner="912")
assert equipment.toggle(app, npc, item) is None
assert sorted(equipment.forget("912")) == [equipment.EQUIPPED, equipment.TOGGLE]

# 窓を開かずに入れる: 答えはそのまま。持ち主か品が無ければ聞かずに None。例外は None
asked = []
equipment.declare(equipment.EQUIP, lambda a, h, i: asked.append(i) or "no free slot", owner="912")
assert equipment.equip(app, npc, item) == "no free slot" and asked == [item]
assert equipment.equip(app, None, item) is None and equipment.equip(app, npc, None) is None
assert asked == [item]
equipment.declare(equipment.EQUIP, broken, owner="912")
assert equipment.equip(app, npc, item) is None
assert equipment.source_of(equipment.EQUIP) == "912"
assert equipment.forget("912", write=logged.append) == [equipment.EQUIP]
assert equipment.source_of(equipment.EQUIP) == "" and any("no longer decides" in l for l in logged)

# 身に着けている品: 置いていなければ None。組の並びは (部位, 品) の文字列化、品が None の組は落とす。崩れた答えは None
assert equipment.gear(app, npc) is None
equipment.declare(equipment.GEAR, lambda a, h: [("right_hand", item), ("head", None)], owner="912")
assert equipment.gear(app, npc) == [("right_hand", item)]
equipment.declare(equipment.GEAR, lambda a, h: [], owner="912")
assert equipment.gear(app, npc) == []                                     # 装備欄はあるが何も着けていない
equipment.declare(equipment.GEAR, lambda a, h: 5, owner="912")
assert equipment.gear(app, npc) is None
equipment.declare(equipment.GEAR, broken, owner="912")
assert equipment.gear(app, npc) is None
before = equipment.declare(equipment.GEAR, lambda a, h: [], owner="other", write=logged.append)
assert before == "912" and any("now decided by 'other'" in l for l in logged)   # 後勝ち
assert equipment.forget("other") == [equipment.GEAR]

# 戦闘の数とは置き場が別。片方を置いても、外しても、もう片方には効かない
combat.declare(combat.ATTACK, lambda a, h: 10.0, owner="912")
equipment.declare(equipment.GEAR, lambda a, h: [("head", item)], owner="912")
assert equipment.forget("912") == [equipment.GEAR]
assert combat.attack(app, npc) == 10.0
assert combat.forget("912") == [combat.ATTACK]
assert not hasattr(combat, "gear") and not hasattr(combat, "toggle")
print("ok")
