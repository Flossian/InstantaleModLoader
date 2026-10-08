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

# ---- 貸し（その場面のあいだだけ別の品で戦わせる。336_ の脱獄の決行） ----
from instantale_modloader import items, ui  # noqa: E402

calls = []


class ItemEquipManager(object):
    def __init__(self, app):
        pass

    def equip_item(self, it):
        calls.append(("equip", getattr(it, "name", it)))


class ItemUnequipManager(object):
    def __init__(self, app):
        pass

    def unequip_item(self, it):
        calls.append(("unequip", getattr(it, "name", it)))
        player.equipments.pop(it.item_type, None)         # 本体の unequip は種類の枠を落とす


main = sys.modules["__main__"]
main.ItemEquipManager, main.ItemUnequipManager = ItemEquipManager, ItemUnequipManager


def make_item(name, kind, stat, value, key):
    return types.SimpleNamespace(name=name, item_type=kind, id=key,
                                 attributes={"item_detail": "x", stat: value})


sword = make_item("剣", "weapon", "攻撃力", 800, "item_1")
helm = make_item("兜", "wearable", "防御力", 500, "item_2")
shard = make_item("研いだ鉄片", "weapon", "攻撃力", 240, "jailbreak_weapon")
robe = make_item("囚人服", "wearable", "防御力", 150, "jailbreak_wearable")
player = types.SimpleNamespace(name="主人公", equipments={"weapon": sword, "wearable": helm},
                               inventory=types.SimpleNamespace(inventory={"item_1": sword, "item_2": helm}))
app = types.SimpleNamespace(player=player)
ui.find_app = lambda: app
if hasattr(sys, equipment._LOANS_ATTR):
    delattr(sys, equipment._LOANS_ATTR)

# 貸す: 本体の equipments が貸した品を指し、本体の Manager で画面上部を塗り直す
assert equipment.lend(app, player, {"weapon": shard, "wearable": robe}, owner="336") == ["weapon", "wearable"]
assert player.equipments == {"weapon": shard, "wearable": robe}
assert calls == [("equip", "研いだ鉄片"), ("equip", "囚人服")]
assert equipment.loan(app, player) == {"weapon": shard, "wearable": robe}
assert equipment.lender(app, player) == "336" and equipment.lender(app, npc) is None
assert equipment.loan(app, npc) is None and "336" in equipment.owners()
# 数と身に着けている品: 装備欄の MOD の答えより貸しが先。装備欄の MOD が居なければ gear は None（素の読み方が貸しを読む）
combat.declare(combat.ATTACK, lambda a, h: 1200.0, owner="333")
assert combat.attack(app, player) == 240.0 and combat.defense(app, player) == 150.0
assert combat.attack(app, npc) == 1200.0                                    # 貸していない人物は置いた MOD の答え
assert equipment.gear(app, player) is None
equipment.declare(equipment.GEAR, lambda a, h: [("head", helm)], owner="333")
assert equipment.gear(app, player) == [("right_hand", shard), ("body", robe)]

# セーブ: 貸した品の id は元の装備の id に戻す。着け替えた種類と、セーブ以外の書き出しには触らない
data = {"player_data": {"equipments": {"weapon": "jailbreak_weapon", "wearable": "item_9"}}}
assert equipment._restore_in_save(data, logged.append) == ["weapon"]
assert data["player_data"]["equipments"] == {"weapon": "item_1", "wearable": "item_9"}
skeleton = {"npcs": {}}
assert equipment._restore_in_save(skeleton, logged.append) == [] and skeleton == {"npcs": {}}

# 重ねて貸しても、返し先は最初に貸す前の装備
shard2 = make_item("折れた椅子の脚", "weapon", "攻撃力", 100, "jailbreak_weapon")
equipment.lend(app, player, {"weapon": shard2}, owner="336")
assert equipment.loan(app, player)["weapon"] is shard2

# 返す: 元の品に戻して本体の Manager を通す。貸しの間に着け替えた種類はそのまま
other_helm = make_item("帽子", "wearable", "防御力", 50, "item_3")
player.equipments["wearable"] = other_helm
del calls[:]
assert equipment.end_loan(app, player) == ["weapon"]
assert player.equipments == {"weapon": sword, "wearable": other_helm}
assert calls == [("equip", "剣")]
assert equipment.loan(app, player) is None and equipment.end_loan(app, player) == []
assert combat.attack(app, player) == 1200.0

# 何も着けていなかった種類は外す（枠を落とし、画面上部を +0 に）。id の文字列で持っていた種類は文字列に戻す
player.equipments = {"weapon": "item_77"}                                  # ロード直後の形。持ち物から引けない
del calls[:]
equipment.lend(app, player, {"weapon": shard, "wearable": robe}, owner="336")
data = {"player_data": {"equipments": {"weapon": "jailbreak_weapon", "wearable": "jailbreak_wearable"}}}
assert equipment._restore_in_save(data, logged.append) == ["weapon", "wearable"]
assert data["player_data"]["equipments"] == {"weapon": "item_77"}           # 何も無かった防具は書かない
assert equipment.end_loan(app, player) == ["weapon", "wearable"]
assert player.equipments == {"weapon": "item_77"}
assert calls == [("equip", "研いだ鉄片"), ("equip", "囚人服"),
                 ("unequip", "研いだ鉄片"), ("unequip", "囚人服")]

# MOD を外すと貸しも引き上げる。ロードは貸しを捨てる
player.equipments = {"weapon": sword}
equipment.lend(app, player, {"weapon": shard}, owner="336")
assert "loan" in equipment.forget("336") and player.equipments == {"weapon": sword}
assert "336" not in equipment.owners()


class Ctx(object):
    generation = 1

    def __init__(self):
        self.wrapped = {}

    def wrap(self, target, required=True, safe=False):
        def deco(fn):
            self.wrapped[target] = fn
            return fn
        return deco


if hasattr(sys, equipment._GATE_ATTR):
    delattr(sys, equipment._GATE_ATTR)
ctx = Ctx()
assert equipment.install(ctx) == [equipment.SAVE_TARGET, equipment.WORLD_TARGET]
assert equipment.install(Ctx()) == [equipment.SAVE_TARGET, equipment.WORLD_TARGET]   # 同じ世代は1回だけ
equipment.lend(app, player, {"weapon": shard}, owner="336")
written = []
data = {"player_data": {"equipments": {"weapon": "jailbreak_weapon"}}}
ctx.wrapped[equipment.SAVE_TARGET](lambda path, d: written.append(dict(d["player_data"]["equipments"])),
                                   "savedata.json", data)
assert written == [{"weapon": "item_1"}]
ctx.wrapped[equipment.WORLD_TARGET](lambda self, *a: None, object(), {})
assert equipment.loan(app, player) is None

# 持ち物に入れずに作る: 本体が持ち物へ直に入れる作りでも、入った品を抜く。同じ鍵の品が居たら戻す
made = types.SimpleNamespace(name="made")


def maker_into(data, key, owner):
    items.inventory_of(owner)[key] = made


player.inventory.inventory = {"item_1": sword}
app.generate_item_from_dict = maker_into
assert items.make_loose(app, player, {"name": "x"}, "jailbreak_weapon") is made
assert player.inventory.inventory == {"item_1": sword}
app.generate_item_from_dict = lambda data, key, owner: made                # 返すだけの作り
assert items.make_loose(app, player, {"name": "x"}, "item_1") is made
assert player.inventory.inventory == {"item_1": sword}
app.generate_item_from_dict = maker_into                                   # 同じ鍵の品を上書きする作り
assert items.make_loose(app, player, {"name": "x"}, "item_1") is made
assert player.inventory.inventory == {"item_1": sword}
print("ok")
