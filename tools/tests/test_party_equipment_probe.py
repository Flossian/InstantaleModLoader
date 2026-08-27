# -*- coding: utf-8 -*-
"""223_probe_party_equipment.py をゲーム抜きで通す。

    python tools/tests/test_party_equipment_probe.py

偽の app / Character / InventoryItem / Kivy Clock を差し込み、次を確認する。

  時機   … popup の観測は `show_popup_menu` の戻りでは走らず、次フレームで走る。
           `active_popup` はフックの戻りの後で組まれる（実測: 同期読みは
           popup=None を写した）ので、遅らせないと中身が録れない
  中身   … 次フレームの観測には popup のクラス名とボタン一覧が写る
  相手   … 会話相手の所持品なら owner_is_npc=True（ConversationStartManager 追跡）
  無     … Manager の __init__（item=None）では owner_is_player を True にしない
           （None is None の巻き添えで両方 True と写っていた）
  素通し … 観測に使う値が壊れていても本体の戻り値は変えない

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
# `ui.scheduler` は Kivy が無いとその場で実行する。ここでは「次フレームまで
# 走らない」ことこそを見たいので、記録するだけの Clock を置く。
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


kivy = types.ModuleType("kivy")
clock = types.ModuleType("kivy.clock")
clock.Clock = FakeClock
kivy.clock = clock
sys.modules.setdefault("kivy", kivy)
sys.modules["kivy.clock"] = clock


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


MOD_PATH = find_mod("_probe_party_equipment")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character(object):
    def __init__(self, character_id, name):
        self.id = character_id
        self.name = name
        self.equipments = {}
        self.inventory = {}


class InstantaleApp(object):
    def __init__(self, characters, party):
        self.world = types.SimpleNamespace(characters=characters)
        self.party = party
        self.player = characters["player"]


class FakeButton(object):
    def __init__(self, text):
        self.text = text


class FakePopup(object):
    def __init__(self, buttons):
        self.children = buttons


class FakeLayout(object):
    """本体popup直下の入れ子（FloatLayout 相当）。ボタンはこの中に居る。"""

    def __init__(self, children):
        self.children = children


class InventoryItem(object):
    def __init__(self, item_instance, item_id, is_equipped=False):
        self.item_instance = item_instance
        self.item_id = item_id
        self.is_equipped = is_equipped
        self.active_popup = None

    def show_popup_menu(self, pos):
        # 本体と同じく、この時点では active_popup をまだ組まない。
        return "shown"


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

    def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
        real = ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                    label=label, cap=cap)

        def write(message):
            self.notes.append(str(message))
            return real(message)
        return write

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


def load_mod():
    spec = importlib.util.spec_from_file_location("probe_party_equipment_under_test",
                                                  MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = load_mod()

START = "__main__:ConversationStartManager.__init__"
POPUP = "scripts.hud.new_hud:InventoryItem.show_popup_menu"
EQUIP_INIT = "__main__:ItemEquipManager.__init__"

APP = None   # __main__ の属性として ui.find_app() に見つけてもらう


def build():
    global APP
    characters = {"player": Character("player", "主人公"),
                  "80": Character("80", "エリス")}
    app = InstantaleApp(characters, ["player", "80"])
    APP = app
    out_dir = tempfile.mkdtemp(prefix="party_probe_test_")
    ctx = Ctx(out_dir)
    MOD.apply(ctx)
    return ctx, app, out_dir


# ---------------------------------------------------------------- popup の時機
print("popup 観測の時機")
ctx, app, out_dir = build()
ctx.hooks[START](lambda self, a, cid: None, object(), app, "80")

npc = app.world.characters["80"]


class FakeItem(object):
    def __init__(self):
        self.obtainer = npc
        self.item_type = "weapon"
        self.name = "鉄の剣"
        self.id = "item_0"


item = FakeItem()
widget = InventoryItem(item, "item_0", is_equipped=True)
# 会話相手の装備参照は Item オブジェクトのことがある（実測）。値の形まで写す。
npc.equipments["weapon"] = item

FakeClock.scheduled = []
result = ctx.hooks[POPUP](InventoryItem.show_popup_menu, widget, (0, 0))
check("本体の戻り値を変えない", result == "shown", result)
check("戻りの時点ではまだ観測しない", ctx.notes == [], ctx.notes)
check("次フレームへ1本だけ予約する", len(FakeClock.scheduled) == 1,
      FakeClock.scheduled)

# 本体と同じく、フックの戻りの後で popup が組まれる。
# ボタンは直下ではなく FloatLayout の中（実機のクラッシュ記録の locals より）。
widget.active_popup = FakePopup(
    [FakeLayout([FakeButton("装備する"), FakeButton("捨てる")])])
FakeClock.run_all()

check("観測は1行", len(ctx.notes) == 1, ctx.notes)
line = ctx.notes[0] if ctx.notes else ""
check("popup のクラス名が写る", "popup=FakePopup" in line, line)
check("ボタン一覧が写る", "装備する" in line and "捨てる" in line, line)
check("会話相手の所持品と分かる", "owner_is_npc=True" in line, line)
check("player ではないと分かる", "owner_is_player=False" in line, line)
check("widget の装備印が写る", "is_equipped=True" in line, line)
check("装備欄は鍵だけでなく値の形まで写る",
      "npc_eq={'weapon': FakeItem:'鉄の剣'}" in line, line)
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- Manager の __init__
print("Manager の観測")
ctx, app, out_dir = build()
manager = types.SimpleNamespace(app=app)
ctx.hooks[EQUIP_INIT](lambda self, a: None, manager, app)
line = ctx.notes[0] if ctx.notes else ""
check("item が無い観測でも1行書く", "ItemEquipManager.__init__:after" in line, ctx.notes)
check("owner=None を player と写さない", "owner_is_player=False" in line, line)
check("owner=None を npc と写さない", "owner_is_npc=False" in line, line)
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)


print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
