# -*- coding: utf-8 -*-
"""240_probe_stamina をゲーム抜きで通す。

    python tools/tests/test_stamina_probe.py

確認するもの:

  素通り   … 見張りを置いても値の読み書きは変わらない。生まれたときの書き込みは録らない
  書き込み … 主人公と同行者の体力が変わると `set` 行に旧値・新値・差・呼び出し元が出る。他人は録らない
  被弾     … 依頼の最中だけ、HP の減りと回復と最低値を人ごとに数える
  依頼     … 出たときに難易度が `quest_start` 行に、クリアの前後が `quest_end` 行に被弾つきで出る
  当て直し … もう一度 apply しても見張りは積み上がらず、1回の書き込みは1行
  外した後 … 用済みの書き手は書かない
  例外     … 書き手が落ちても値は書かれる
  property … クラスが自分で持つ property は上書きしない
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "240_probe_stamina")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "stamina_probe"))
RECORD_NAME = "stamina.jsonl"

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Character(object):
    """見張りはこのクラスに置かれる（偽の `scripts.characters`）。"""

    def __init__(self, character_id, level, pi, max_pi, hp):
        self.id = character_id
        self.experience_level = level
        self.max_physical_integrity = max_pi
        self.physical_integrity = pi
        self.current_hp = hp
        self.max_hp = hp
        self.exhausted = False
        self.body_parts = {"head": {"injury": 0, "stage": "intact"},
                           "arms": {"injury": 0, "stage": "intact"}}


characters_module = types.ModuleType("scripts.characters")
characters_module.Character = Character
sys.modules["scripts.characters"] = characters_module


class World(object):
    def __init__(self, characters):
        self.characters = characters


class App(object):
    def __init__(self):
        self.player = Character("player", 40, 30, 30, 1000)
        self.member = Character("7", 35, 28, 28, 800)
        self.stranger = Character("9", 20, 20, 20, 500)
        self.world = World({"7": self.member, "9": self.stranger})
        self.current_quest_data = None
        self.in_battle = False


class Manager(object):
    def __init__(self, app):
        self.app = app


class FakeUI(object):
    def __init__(self, app, real):
        self._app = app
        self._real = real

    def find_app(self):
        return self._app

    def party_member_ids(self, app):
        return ["7"]

    def character_name(self, app, character_id, *args, **kwargs):
        return {"player": "主", "7": "連れ"}.get(str(character_id), "?")

    def __getattr__(self, name):
        return getattr(self._real, name)


class FakeCtx(object):
    _mod = "240_probe_stamina"

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.retired = False

    def superseded(self):
        return self.retired

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def jsonl(self, name, **kw):
        return ml.ModContext.jsonl(self, name, **kw)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    name = "stamina_probe_mod"
    sys.modules.pop(name, None)
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(name, os.path.join(MOD_DIR, entry))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def apply(app):
    module = load_mod()
    module.ui = FakeUI(app, ml.ui)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def records(kind=None):
    path = os.path.join(OUT_DIR, RECORD_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [row for row in rows if kind is None or row.get("kind") == kind]


def spend_on_clear(character):
    """クリアで上限の半分を引く（素のゲームの見かけの式）。呼び出し元の名前を固定する。"""
    character.physical_integrity -= character.max_physical_integrity // 2


shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)
app = App()
module, ctx = apply(app)

print("素通り")
check("見張りがクラスに置かれた",
      type(vars(Character)["physical_integrity"]).__name__ == "StaminaWatch")
check("インスタンスの値をそのまま返す", app.player.physical_integrity == 30
      and app.player.current_hp == 1000)
check("値はインスタンスの __dict__ に入ったまま", vars(app.player)["physical_integrity"] == 30)
fresh = Character("8", 1, 10, 10, 100)
check("生まれたときの書き込みは録らない", not records("set") and fresh.physical_integrity == 10,
      records("set"))
check("無い属性は AttributeError", getattr(Manager(app), "physical_integrity", "none") == "none")

print("書き込み")
spend_on_clear(app.player)
sets = records("set")
check("主人公の体力が変わると set 行", len(sets) == 1 and sets[0]["who"] == "player"
      and sets[0]["old"] == "30" and sets[0]["new"] == "15" and sets[0]["delta"] == -15, sets)
check("呼び出し元が出る", sets and "spend_on_clear" in sets[0]["caller"], sets)
check("上限とレベルが出る", sets and sets[0]["max"] == 30 and sets[0]["level"] == 40, sets)
check("値は書かれた", app.player.physical_integrity == 15)
app.member.physical_integrity = 20
check("同行者の体力も録る", records("set")[-1]["who"] == "7", records("set")[-1:])
count = len(records("set"))
app.stranger.physical_integrity = 1
check("同行していない人は録らない", len(records("set")) == count)
app.player.physical_integrity = 15
check("同じ値の書き直しは録らない", len(records("set")) == count)
app.player.max_physical_integrity = 31
check("上限の変化も録る", records("set")[-1]["attr"] == "max_physical_integrity")

print("被弾")
app.player.current_hp = 900            # 依頼の外の被弾は数えない
app.current_quest_data = {"id": "12", "difficulty": "45", "quest_type": "normal_quest",
                          "quest_title": "沼の主"}
start = ctx.hooks["__main__:QuestStartManager.start_quest"]
result = start(lambda self: "started", Manager(app))
check("出た地点の戻り値はそのまま", result == "started")
starts = records("quest_start")
check("出たときに難易度が出る", len(starts) == 1 and starts[0]["quest"]["difficulty"] == "45"
      and starts[0]["people"]["player"]["pi"] == 15, starts)
ctx.hooks["__main__:QuestStartManager.execute"](lambda self: None, Manager(app))
check("出た地点が2つ呼ばれても1行", len(records("quest_start")) == 1)
app.player.current_hp = 600
app.player.current_hp = 700
app.player.current_hp = 400
app.member.current_hp = 500
app.stranger.current_hp = 10
app.player.body_parts["arms"].update({"injury": 3, "stage": "hurt"})

print("依頼")


def clear(self):
    spend_on_clear(self.app.player)
    self.app.current_quest_data = None
    return "cleared"


result = ctx.hooks["__main__:QuestEndManager.execute"](clear, Manager(app))
check("クリアの戻り値はそのまま", result == "cleared")
ends = records("quest_end")
end = ends[-1] if ends else {}
check("クリアの行が出る", len(ends) == 1, ends)
check("依頼の難易度と種類", end.get("quest", {}).get("difficulty") == "45"
      and end.get("quest", {}).get("type") == "normal_quest", end.get("quest"))
check("前後の体力", end.get("before", {}).get("player", {}).get("pi") == 15
      and end.get("after", {}).get("player", {}).get("pi") == 0, end)
hits = end.get("damage", {})
check("主人公の被弾（減り・回復・最低値）",
      hits.get("player") == {"loss": 600, "gain": 100, "min": 400, "writes": 3}, hits)
check("同行者の被弾", hits.get("7", {}).get("loss") == 300, hits)
check("他人の被弾は数えない", "9" not in hits, hits)
check("部位の怪我が出る", end.get("before", {}).get("player", {}).get("body")
      == {"arms": [3, "hurt"]}, end.get("before"))
check("クリアの中の set 行は依頼の id 付き", records("set")[-1]["quest"] == "12",
      records("set")[-1:])
app.player.current_hp = 300           # 依頼の外。次の依頼に持ち越さない
app.current_quest_data = {"id": "13", "difficulty": "5", "quest_type": "random_quest"}
start(lambda self: None, Manager(app))
result = ctx.hooks["__main__:QuestRetireManager.execute"](lambda self: "retired", Manager(app))
retire = records("quest_retire")
check("放棄の行が出る（戻り値はそのまま）", result == "retired" and len(retire) == 1, retire)
check("依頼の外の被弾は次の依頼に入らない", retire and retire[-1]["damage"] == {}, retire)

print("当て直し")
app.player.physical_integrity = 30
watch = vars(Character)["physical_integrity"]
module, ctx = apply(app)
check("見張りは置き直さない", vars(Character)["physical_integrity"] is watch)
count = len(records("set"))
app.player.physical_integrity = 29
check("1回の書き込みは1行", len(records("set")) == count + 1, len(records("set")) - count)

print("外した後")
ctx.retired = True
count = len(records())
app.player.physical_integrity = 28
check("用済みの書き手は書かない", len(records()) == count, records()[count:])
check("値は書かれる", app.player.physical_integrity == 28)
module, ctx = apply(app)

print("例外")
store = getattr(sys, module.STORE)
sink = store["sink"]


def broken(*_args):
    raise RuntimeError("boom")


store["sink"] = broken
app.player.physical_integrity = 5
store["sink"] = sink
check("書き手が落ちても値は書かれる", app.player.physical_integrity == 5)
check("記録で落ちていない", not ctx.errors, ctx.errors)

print("property")


class Computed(object):
    @property
    def current_hp(self):
        return 77

    def __init__(self):
        self.physical_integrity = 3


characters_module.Character = Computed
store["version"] = None
module, ctx = apply(app)
check("property は上書きしない", isinstance(vars(Computed)["current_hp"], property)
      and Computed().current_hp == 77)
check("他の属性には置く", type(vars(Computed)["physical_integrity"]).__name__ == "StaminaWatch")
characters_module.Character = Character

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
