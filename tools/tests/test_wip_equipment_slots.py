# -*- coding: utf-8 -*-
"""912_equipment_slots をゲーム抜きで通す。

    python tools/tests/test_wip_equipment_slots.py

見るのは MOD が自分で決めている所だけ:
部位の矩形と受け入れ（収まる・種類が合う・空いている）、本体へ渡す1つ（合算せず最高値）、
ドロップの可否（`is_valid_placement` の包み）と控えの更新、断ったドロップの置き直し、
右クリックの「装備」「外す」の移動、開いたときの控えの突き合わせ。

本体の座標は「y は下から」、控えは「y は上から」。偽物のグリッドは本体と同じ約束で動く
（`place_existing_item` は `item.grid_pos = [x, 下から y]` で置く。DOC.md §4）。
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

folder = [n for n in os.listdir(MODS_DIR) if n.endswith("_equipment_slots")][0]
folder = os.path.join(MODS_DIR, folder)
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
PKG = "equipment_slots_under_test"
pkg = types.ModuleType(PKG)
pkg.__path__ = [folder]
sys.modules[PKG] = pkg
spec = importlib.util.spec_from_file_location(
    PKG + ".entry", os.path.join(folder, manifest["entry"]),
    submodule_search_locations=[folder])
MOD = importlib.util.module_from_spec(spec)
MOD.__package__ = PKG
spec.loader.exec_module(MOD)
rules = MOD.rules
COLS, ROWS = rules.COLS, rules.ROWS
for key, spec_ in manifest["settings"].items():
    assert getattr(MOD, key) == spec_["default"], key


# ---------------------------------------------------------------- 偽物（本体の約束を写す）
class Item(object):
    def __init__(self, id, item_type, detail, stat=0, size=(1, 1)):
        self.id = id
        self.name = id
        self.item_type = item_type
        self.attributes = {"item_detail": detail}
        if item_type == "weapon":
            self.attributes["攻撃力"] = stat
        elif item_type == "wearable":
            self.attributes["防御力"] = stat
        self.width_slots, self.height_slots = size
        self.obtainer = None
        self.grid_pos = None

    def to_dict(self):
        return {"id": self.id, "name": self.name}


class Node(object):
    """Kivy のウィジェットの偽物。子・親・透明度だけ。"""
    def __init__(self, **kw):
        self.children = []
        self.parent = None
        self.opacity = 1.0
        self.width = 100.0
        self.__dict__.update(kw)

    def add_widget(self, w):
        self.children.insert(0, w)
        w.parent = self

    def remove_widget(self, w):
        if w in self.children:
            self.children.remove(w)
            w.parent = None


class Grid(Node):
    """InventoryGrid の偽物。cols x rows、添字は 下からの y * cols + x。"""
    def __init__(self, cols, rows, obtainer, **kw):
        Node.__init__(self, **kw)
        self.cols, self.rows = cols, rows
        self.obtainer = obtainer
        self.situation = None
        self.taken = set()

    def slots_for(self, x, gy, w, h):
        return [(gy + dy) * self.cols + x + dx for dy in range(h) for dx in range(w)]

    def is_valid_placement(self, x, gy, w, h):
        if x < 0 or gy < 0 or x + w > self.cols or gy + h > self.rows:
            return False
        return not (set(self.slots_for(x, gy, w, h)) & self.taken)

    def occupy(self, widget, x, gy):
        idx = self.slots_for(x, gy, widget.width_slots, widget.height_slots)
        widget.current_slots = list(idx)
        self.taken |= set(idx)

    def place_existing_item(self, widget):
        x, gy = widget.item_instance.grid_pos
        self.occupy(widget, x, gy)

    def place_new_item(self, widget):
        for gy in range(self.rows):
            for x in range(self.cols):
                if self.is_valid_placement(x, gy, widget.width_slots, widget.height_slots):
                    self.occupy(widget, x, gy)
                    widget.item_instance.grid_pos = [x, gy]
                    return True
        raise RuntimeError("full")

    def try_place_item(self, widget, pos):
        """本体と同じく is_valid_placement を通し、通れば占有する。pos は (x, 下から y)。"""
        x, gy = pos
        if not HOOKS["valid"](Grid.is_valid_placement, self, x, gy,
                              widget.width_slots, widget.height_slots):
            return False
        self.occupy(widget, x, gy)
        return True


class Widget(Node):
    """InventoryItem の偽物。"""
    def __init__(self, item, grid, cell=None):
        Node.__init__(self)
        self.item_instance = item
        self.item_id = item.id
        self.inventory = grid
        self.width_slots, self.height_slots = item.width_slots, item.height_slots
        self.current_slots = []
        self.is_equipped = False
        if cell is not None:
            grid.occupy(self, cell[0], cell[1])
            item.grid_pos = list(cell)
        grid.parent.add_widget(self)

    def clear_current_slots(self):
        self.inventory.taken -= set(self.current_slots)
        self.current_slots = []

    def change_inventory(self, new):
        return HOOKS["change"](lambda s, n: setattr(s, "inventory", n), self, new)

    def hide_popup_menu(self):
        self.hidden = True


class Player(object):
    def __init__(self):
        self.name = "テスト"
        self.inventory = types.SimpleNamespace(inventory={})
        self.equipments = {}

    def give(self, *items):
        for item in items:
            item.obtainer = self
            self.inventory.inventory[item.id] = item
        return items


class Ctx(object):
    def __init__(self, root):
        self.hooks = {}
        self.lines = []
        self.root = root

    def logger(self, name, **kw):
        return self.lines.append

    def log(self, line):
        self.lines.append(line)

    def log_exc(self, line):
        # 画面の組み立ては Kivy が要る。ゲームの外では建てられないのが正しい
        if "kivy unavailable" in line or "panel failed" in line:
            self.lines.append(line)
            return
        raise AssertionError(line)

    def wrap(self, target, **kw):
        def deco(fn):
            self.hooks[target] = fn
            return fn
        return deco

    def state_path(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        try:
            return json.load(io.open(path, encoding="utf-8"))
        except Exception:
            return default

    def write_json(self, path, data, **kw):
        io.open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
        return True


def saved_positions():
    d = os.path.join(root, "equipment_slots")
    return json.load(io.open(os.path.join(d, os.listdir(d)[0]), encoding="utf-8"))["テスト"]


# ---------------------------------------------------------------- 純粋な決まり
sword = Item("w1", "weapon", "long_weapon", 580, size=(1, 4))
dagger = Item("w2", "weapon", "small_weapon", 451)
shield = Item("s1", "wearable", "shield", 300)
helm = Item("h1", "wearable", "headgear", 439)
ring = Item("r1", "wearable", "accessory", 120)
herb = Item("x1", "healing_item", "herb")

assert rules.region_at(0, 0) is None and rules.region_at(2, 0) == "head"
assert rules.fits(sword, 4, 2) == "right_hand"
assert rules.fits(sword, 4, 3) is None                   # 1段下げるとはみ出す
assert rules.fits(sword, 2, 2) == "body"                # 胴も縦4なので収まるが、種類で弾く
assert rules.accepts(sword, 2, 2, {}) == "wrong region body for hand"
assert rules.accepts(shield, 1, 5, {}) is None
assert rules.accepts(helm, 0, 2, {}) == "wrong region left_hand for head"
assert rules.accepts(helm, 2, 0, {"head": "other"}) == "region head occupied"
assert rules.accepts(herb, 2, 0, {}) == "not equipment"

items = {"w1": sword, "w2": dagger, "s1": shield, "h1": helm, "r1": ring}
slots = rules.slots_from_positions(
    {"w1": [4, 2], "w2": [0, 2], "h1": [2, 0], "r1": [4, 6], "s1": [9, 9]}, items)
assert slots == {"right_hand": "w1", "left_hand": "w2", "head": "h1", "accessory1": "r1"}, slots
assert rules.best(slots, items, "weapon") is sword
assert rules.best(slots, items, "wearable") is helm          # 439 > 120。合算しない
assert rules.best({}, items, "wearable") is None
assert rules.fit_scale(ring, "accessory1") == 2 and rules.fit_scale(sword, "right_hand") == 1
assert rules.fit_scale(shield, "left_hand") == 2                       # 偽の盾は 1×1 なので 2 倍
assert rules.fit_scale(Item("q", "wearable", "clothing", 1, size=(1, 2)), "body") == 2

# ---------------------------------------------------------------- 画面と本体の偽物
root = tempfile.mkdtemp()
ctx = Ctx(root)
MOD.apply(ctx)
HOOKS = {
    "valid": ctx.hooks["scripts.hud.new_hud:InventoryGrid.is_valid_placement"],
    "change": ctx.hooks["scripts.hud.new_hud:InventoryItem.change_inventory"],
}
try_place_hook = ctx.hooks["scripts.hud.new_hud:InventoryGrid.try_place_item"]
popup_equip = ctx.hooks["scripts.hud.new_hud:ItemPopupMenu.on_equip_item"]
popup_unequip = ctx.hooks["scripts.hud.new_hud:ItemPopupMenu.on_unequip_item"]
toggle = ctx.hooks["scripts.hud.new_hud:InstanTaleHUD.toggle_center_inventory_visibility"]

player = Player()
player.give(sword, dagger, shield, helm, ring, herb)

# ui.find_hud は scripts.hud.new_hud.InstanTaleHUD の型で見分ける。偽の module を差す
hud_mod = types.ModuleType("scripts.hud.new_hud")


class InstanTaleHUD(Node):
    pass


hud_mod.InstanTaleHUD = InstanTaleHUD
hud_mod.InventoryGrid = Grid
sys.modules["scripts.hud.new_hud"] = hud_mod

hud = InstanTaleHUD()
host = Node()                    # HUD 直下の FloatLayout（overlay_host が返す）
hud.add_widget(host)
button_bar = Node()              # 右側の選択肢の入れ物
hud.right_buttons = [Node(disabled=False) for _ in range(4)]
for _b in hud.right_buttons:
    button_bar.add_widget(_b)
window = Node()                  # 所持品の窓
host.add_widget(window)
main = Grid(4, 6, player)
window.add_widget(main)
app = types.SimpleNamespace(player=player, world=types.SimpleNamespace(name="世界"), root=hud)
MOD.ui.find_app = lambda: app

# 装備欄（build_panel は Kivy が要るので、同じ形を手で組む）
panel = Node()
host.add_widget(panel)
mine = Grid(COLS, ROWS, player)
setattr(mine, MOD.GRID_ATTR, True)
panel.add_widget(mine)
mine.x, mine.y = 0.0, 0.0
setattr(panel, MOD.PANEL_ATTR, {"grid": mine, "labels": {}, "cell": 65.0, "gap": 1.0})


touch_down = ctx.hooks["scripts.hud.new_hud:InventoryItem.on_touch_down"]


class Touch(object):
    def __init__(self):
        self.pos = (0, 0)
        self.grab_current = None


def on_touch_up():
    """本体の on_touch_up の中から下見が呼ばれる形を作る（包みは呼び出し元の名前を見る）。"""
    return HOOKS["valid"](Grid.is_valid_placement, mine, *CHECK)


def drop(widget, x, y_top):
    """本体のドロップ: touch_down → 下見（is_valid_placement）→ 通れば try_place_item → change_inventory。"""
    global CHECK
    gy = ROWS - y_top - widget.height_slots
    widget.collide_point = lambda *a: True
    touch_down(lambda self, t: None, widget, Touch())
    widget.clear_current_slots()
    CHECK = (x, gy, widget.width_slots, widget.height_slots)
    if not on_touch_up():
        widget.inventory.occupy(widget, *widget.item_instance.grid_pos) if False else None
        return
    try_place_hook(lambda self, w, pos: Grid.try_place_item(self, w, (x, gy)),
                   mine, widget, (x * 65.0 + 1, gy * 65.0 + 1))
    widget.change_inventory(mine)


w_sword = Widget(sword, main, (0, 0))
w_dagger = Widget(dagger, main, (1, 0))
w_helm = Widget(helm, main, (2, 0))
w_ring = Widget(ring, main, (3, 0))
w_herb = Widget(herb, main, (0, 4))

# ドロップ: 兜を頭へ
drop(w_helm, 2, 0)
assert w_helm.inventory is mine and w_helm.parent is window
assert player.equipments["wearable"] is helm
assert "h1" not in player.inventory.inventory                # 装備中は持ち物の辞書に居ない
assert w_helm.is_equipped and not w_sword.is_equipped
# 剣を右手へ、短剣を左手へ: weapon は攻撃力の高い剣
drop(w_sword, 4, 2)
drop(w_dagger, 0, 2)
assert player.equipments["weapon"] is sword
# 断る: 指輪を頭（兜が居る。種類違いなので置換もしない）へ → 下見で落ち、本体が元へ戻す
drop(w_ring, 3, 1)
assert w_ring.inventory is main and "r1" in player.inventory.inventory and w_helm.inventory is mine
drop(w_herb, 2, 4)
assert w_herb.inventory is main
# 置換: 別の兜を頭へ落とすと、元の兜は所持品の空きへ出て新しい兜が入る
helm2 = Item("h2", "wearable", "headgear", 900)
player.give(helm2)
w_helm2 = Widget(helm2, main, (2, 4))
drop(w_helm2, 2, 0)
assert w_helm2.inventory is mine and w_helm.inventory is main and w_helm.current_slots
assert player.equipments["wearable"] is helm2
# 所持品に空きが無ければ置換しない（下見で落ち、本体が元へ戻す）
main.is_valid_placement = lambda *a: False
drop(w_helm, 2, 0)
assert w_helm2.inventory is mine and w_helm.inventory is main
assert any("refused: no room" in l for l in ctx.lines)
del main.is_valid_placement
# 元に戻す（以降の試験は h1 が頭に居る前提）
w_helm.item_instance.grid_pos = [2, 4]
main.occupy(w_helm, 2, 4)
drop(w_helm, 2, 0)
assert w_helm.inventory is mine and w_helm2.inventory is main
# 断る: 装備欄の中で兜を胴へ → 元の頭へ戻る
drop(w_helm, 2, 2)
assert w_helm.inventory is mine and rules.top_left(w_helm.current_slots) == (2, ROWS - 1 - 0 - 0)
assert saved_positions() == {"h1": [2, 0], "w1": [4, 2], "w2": [0, 2]}, saved_positions()

# 右クリックの「装備」: 指輪は装飾1へ。盾は両手が埋まっているので右手の剣と入れ替え
popup_equip(None, types.SimpleNamespace(item=w_ring))
assert w_ring.inventory is mine and player.equipments["wearable"] is helm   # 439 > 120
assert w_ring.size == (129.0, 129.0) and w_ring.pos == (4 * 65.0, (ROWS - 6 - 2) * 65.0)   # 1x1 は 2x2 に広がる
w_shield = Widget(shield, main, (3, 0))
popup_equip(None, types.SimpleNamespace(item=w_shield))
assert w_shield.inventory is mine and w_sword.inventory is main
assert player.equipments["weapon"] is dagger                  # 剣が外れて短剣へ繰り下がる
assert player.equipments["wearable"] is helm                  # 盾 300 < 兜 439
# 右クリックの「外す」: 兜を外すと防具は盾へ
popup_unequip(None, types.SimpleNamespace(item=w_helm))
assert w_helm.inventory is main and player.equipments["wearable"] is shield
# 装備でない品はプレイヤーの品なので MOD が受けて何もしない。他人の品は本体へ素通し
passed = []
popup_equip(lambda self: passed.append(self), types.SimpleNamespace(item=Widget(herb, main)))
assert not passed
other = Widget(Item("n1", "weapon", "small_weapon", 1), main)
other.item_instance.obtainer = object()
popup_equip(lambda self: passed.append(self), types.SimpleNamespace(item=other))
assert passed

# セーブ: 書き出す JSON の player_data.inventory へ装備欄の品を足す。生きている辞書には触らない
write_hook = ctx.hooks["scripts.save_codec:write_obfuscated_json_file"]
data = {"player_data": {"inventory": {k: {"name": k} for k in player.inventory.inventory}}}
written = {}
write_hook(lambda path, d: written.update(d), "savedata.json", data)
assert set(written["player_data"]["inventory"]) >= {"w1", "s1", "w2"}
assert written["player_data"]["inventory"]["s1"] == {"id": "s1", "name": "s1"}
assert "s1" not in player.inventory.inventory and "w1" in player.inventory.inventory
write_hook(lambda path, d: None, "world_data.json", {"areas": {}})   # セーブ以外はそのまま

# 開いたとき: 売った品は控えから落ち、本体が装備しているのに控えに無い品は拾われる
MOD.CONTAINER.pop("w2").obtainer = None                      # 売った品は持ち主が変わる
player.equipments = {"weapon": sword}
toggle(lambda self: None, hud)
positions = saved_positions()
assert "w2" not in positions and positions["s1"] == [4, 2], positions
assert positions["w1"] == [0, 2], positions                  # 右手は盾なので、空いている左手へ
assert "w1" not in player.inventory.inventory and MOD.CONTAINER["w1"] is sword
assert any(l.startswith("dropped 'w2'") for l in ctx.lines)
assert any(l.startswith("adopted 'w1'") for l in ctx.lines)

# 開いている間は右側の選択肢を親ごと隠し、閉じると戻す。各ボタンの disabled は触らない
assert button_bar.disabled is True and all(b.opacity == 0.0 for b in hud.right_buttons)
assert all(b.disabled is False for b in hud.right_buttons)
host.remove_widget(window)                                   # 閉じた（窓が無い）
toggle(lambda self: None, hud)
assert button_bar.disabled is False and all(b.opacity == 1.0 for b in hud.right_buttons)
host.add_widget(window)

print("ok")
