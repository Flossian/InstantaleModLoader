# -*- coding: utf-8 -*-
"""402_party_inventory_transfer.py の受け渡しをゲーム抜きで通す。

    python tools/tests/test_party_inventory_transfer.py

偽の app / Character / grid / InventoryItem / Kivy Clock を差し込み、次を確認する。

  予約   … `save_game` はその場では走らず、Clock へ1本だけ予約される
  内容   … 予約されたものは呼べる関数（`Screen.guarded` の戻り値ではない）
  抑制   … 続けて動かすと、走るのは最後の1本だけ
  所有   … 移った先の inventory に入り、id と obtainer が同期する
  装備   … 移した品が装備欄に残っていたら、その slot の**キーごと**落ちる

`screen.guarded(fn)` を `screen.schedule` へ渡すと、その場で `fn()` が走ったうえに
戻り値 `None` が予約され、遅延後に `NoneType is not callable` になる。
「その場では走らない」「予約されたものは呼べる」の2つがその回帰を止める。

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402


# ---------------------------------------------------------------- 偽 Kivy
# `ui.Screen.schedule` は Kivy の Clock が無ければ諦める（画面が無いので正しい）。
# ここでは予約そのものを見たいので、記録するだけの Clock を置く。
class FakeClock(object):
    scheduled = []

    @staticmethod
    def schedule_once(fn, delay=0.0):
        FakeClock.scheduled.append((fn, delay))

    @staticmethod
    def run_all():
        pending, FakeClock.scheduled = FakeClock.scheduled, []
        for fn, _delay in pending:
            fn(0)


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    clock = types.ModuleType("kivy.clock")
    clock.Clock = FakeClock
    kivy.clock = clock
    sys.modules.setdefault("kivy", kivy)
    sys.modules["kivy.clock"] = clock


install_fake_kivy()


def find_mod(suffix):
    """mod は **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_party_inventory_transfer")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Item(object):
    def __init__(self, name, item_type="weapon"):
        self.name = name
        self.item_type = item_type
        self.id = None
        self.obtainer = None
        self.unequip_calls = 0

    def unequip(self):
        # 本体 items.py:54 と同じ形。辞書に slot が無ければ KeyError で落ちる。
        self.unequip_calls += 1
        del self.obtainer.equipments[self.item_type]


class Character(object):
    def __init__(self, character_id, name):
        self.id = character_id
        self.name = name
        self.inventory = {}
        self.equipments = {}


class Grid(object):
    """twin inventory の片側。`obtainer` が持ち主。"""

    def __init__(self, obtainer):
        self.obtainer = obtainer


class InventoryItem(object):
    def __init__(self, item_instance, item_id, grid, is_equipped=False):
        self.item_instance = item_instance
        self.item_id = item_id
        self.inventory = grid
        self.is_equipped = is_equipped

    def change_inventory(self, new_inventory):
        self.inventory = new_inventory
        return None


class InstantaleApp(object):
    def __init__(self, characters, party):
        self.world = types.SimpleNamespace(characters=characters)
        self.party = party
        self.player = characters["player"]
        self.buttons = []
        self.saves = 0
        self.opened = []

    def save_game(self):
        self.saves += 1

    def toggle_twin_inventory_window(self, left, right, title, situation):
        self.opened.append((left, right, title, situation))


class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.notes = []

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, message, level="INFO"):
        self.notes.append(str(message))

    def log_exc(self, message):
        self.errors.append(message)

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


def load_mod():
    spec = importlib.util.spec_from_file_location("party_inventory_transfer_under_test",
                                                  MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = load_mod()

START = "__main__:ConversationStartManager.__init__"
PRESS = "__main__:InstantaleApp.on_button_press"
CHANGE = "scripts.hud.new_hud:InventoryItem.change_inventory"

APP = None   # __main__ の属性として ui.find_app() に見つけてもらう


def open_window():
    """会話 → 受け渡しウィンドウを開くところまで進め、ctx と app を返す。"""
    global APP
    characters = {"player": Character("player", "主人公"),
                  "80": Character("80", "エリス")}
    app = InstantaleApp(characters, ["player", "80"])
    APP = app

    out_dir = tempfile.mkdtemp(prefix="party_transfer_test_")
    ctx = Ctx(out_dir)
    MOD.apply(ctx)

    # 会話が始まると mod が相手の id を控える。
    ctx.hooks[START](lambda self, a, cid: None, object(), app, "80")

    # 「＜アイテムの受け渡し＞」を押す。印はボタン辞書の側に付く。
    app.buttons = [{"text": MOD.LABEL, "spec": None, MOD.MARK: "transfer"}]
    FakeClock.scheduled = []
    ctx.hooks[PRESS](lambda self, index: "orig", app, 0)
    FakeClock.run_all()          # rename_right_header の予約を片付ける
    return ctx, app, out_dir


def move(ctx, app, widget, new_grid):
    """本体の drag/drop と同じ経路で1つ動かす。"""
    def orig(self, new_inventory):
        return self.change_inventory(new_inventory)
    return ctx.hooks[CHANGE](orig, widget, new_grid)


# ---------------------------------------------------------------- 予約
print("受け渡しの保存")
ctx, app, out_dir = open_window()
check("twin inventory が situation=party_transfer で開く",
      app.opened and app.opened[0][3] == "party_transfer", app.opened)

npc = app.world.characters["80"]
player = app.player
sword = Item("鉄の剣")
sword.obtainer = npc
sword.id = "item_0"
npc.inventory["item_0"] = sword
npc.equipments["weapon"] = "item_0"
npc.equipments["wearable"] = "item_9"

npc_grid = Grid(npc)
player_grid = Grid(player)
widget = InventoryItem(sword, "item_0", npc_grid)

FakeClock.scheduled = []
move(ctx, app, widget, player_grid)

check("その場では save_game を呼ばない", app.saves == 0, app.saves)
check("Clock へ1本だけ予約する", len(FakeClock.scheduled) == 1, FakeClock.scheduled)
check("予約されたのは呼べる関数",
      FakeClock.scheduled and callable(FakeClock.scheduled[0][0]),
      FakeClock.scheduled[:1])

check("移った先の inventory に入る", sword in player.inventory.values(), player.inventory)
check("元の inventory からは消える", "item_0" not in npc.inventory, npc.inventory)
check("obtainer が移った先になる", sword.obtainer is player, sword.obtainer)
check("装備欄は slot キーごと落ちる（None を残さない）",
      "weapon" not in npc.equipments, npc.equipments)
check("解除は本体の unequip を通す", sword.unequip_calls == 1, sword.unequip_calls)
check("関係の無い slot は触らない", npc.equipments.get("wearable") == "item_9",
      npc.equipments)
check("渡した widget の装備印を落とす", widget.is_equipped is False,
      widget.is_equipped)

FakeClock.run_all()
check("予約が走ると save_game が1回", app.saves == 1, app.saves)
check("遅延後の呼び出しで例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- 抑制
print("連続移動")
ctx, app, out_dir = open_window()
npc = app.world.characters["80"]
player = app.player

FakeClock.scheduled = []
for n in range(2):
    item = Item("薬草{}".format(n), item_type="consumable")
    item.obtainer = npc
    item.id = "item_{}".format(n)
    npc.inventory[item.id] = item
    move(ctx, app, InventoryItem(item, item.id, Grid(npc)), Grid(player))

check("2回動かしても予約は2本", len(FakeClock.scheduled) == 2, FakeClock.scheduled)
FakeClock.run_all()
check("走るのは最後の1本だけ", app.saves == 1, app.saves)
check("2つとも移っている", len(player.inventory) == 2, player.inventory)
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- 装備参照がオブジェクトのとき
# 実行時の equipments の値は id 文字列とは限らない。エリスの装備武器を渡した
# 実機では、id 文字列との比較では外れず、残った参照が本体 items.py の
# unequip（self.obtainer 基準）を KeyError: 'weapon' で落とした。
print("装備参照が Item オブジェクトの受け渡し")
ctx, app, out_dir = open_window()
npc = app.world.characters["80"]
player = app.player

blade = Item("使い古された鉄の短剣")
blade.obtainer = player
blade.id = "item_155"
player.inventory["item_155"] = blade
player.equipments["weapon"] = blade          # id 文字列ではなく instance そのもの
player.equipments["wearable"] = "item_9"

widget = InventoryItem(blade, "item_155", Grid(player), is_equipped=True)
FakeClock.scheduled = []
move(ctx, app, widget, Grid(npc))

check("オブジェクト参照でも slot キーごと落ちる",
      "weapon" not in player.equipments, player.equipments)
check("オブジェクト参照でも本体の unequip を通す",
      blade.unequip_calls == 1, blade.unequip_calls)
check("id 文字列の他 slot は触らない",
      player.equipments.get("wearable") == "item_9", player.equipments)
check("相手側へ移っている", blade in npc.inventory.values(), npc.inventory)
check("obtainer が相手になる", blade.obtainer is npc, blade.obtainer)
check("装備印を持ち越さない", widget.is_equipped is False, widget.is_equipped)
FakeClock.run_all()
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- unequip の番人
# 実体が別の場所へ移った後、古い「装備中」表示から本体popupの「外す」が飛ぶと、
# 本体 unequip は空の equipments を引いて KeyError で落ちる（実測）。
# 番人は equipments がその品を指している解除だけ本体へ通す。
print("unequip の番人")
ctx, app, out_dir = open_window()
GUARD = "scripts.items:Item.unequip"
npc = app.world.characters["80"]

def native_unequip(self):
    return self.unequip()

fresh_item = Item("儀礼剣")
fresh_item.id = "item_5"
fresh_item.obtainer = npc
npc.inventory["item_5"] = fresh_item
npc.equipments["weapon"] = fresh_item          # 同一instance参照＝正常
ctx.hooks[GUARD](native_unequip, fresh_item)
check("正常な解除は本体へ通す", fresh_item.unequip_calls == 1, fresh_item.unequip_calls)
check("通した結果 slot が落ちる", "weapon" not in npc.equipments, npc.equipments)

stale_item = Item("移された剣")
stale_item.id = "item_6"
stale_item.obtainer = npc                       # equipments は空＝食い違い
result = ctx.hooks[GUARD](native_unequip, stale_item)
check("食い違う解除は本体へ通さない", stale_item.unequip_calls == 0,
      stale_item.unequip_calls)
check("落ちずに None を返す", result is None, result)
check("無視した記録を残す",
      any("stale unequip ignored" in note for note in ctx.notes),
      [n for n in ctx.notes if "unequip" in n])

string_item = Item("符呪の剣")
string_item.id = "item_7"
string_item.obtainer = npc
npc.inventory["item_7"] = string_item
npc.equipments["weapon"] = "item_7"             # id文字列参照（402の装備ボタンの形）
ctx.hooks[GUARD](native_unequip, string_item)
check("id文字列の参照も正常として通す", string_item.unequip_calls == 1,
      string_item.unequip_calls)
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)


print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
