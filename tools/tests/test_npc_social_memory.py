# -*- coding: utf-8 -*-
"""403_npc_social_memory.py をゲーム抜きで通す。

    python tools/tests/test_npc_social_memory.py

見ているのは、この MOD が自分で決めている所だけ。

  検証   … 応答の正規化は1本（`normalize_result`）で、構造化・非構造化の
           どちらから来ても同じ検証（id・自己参照・重複方向）を通る
  件数   … 残す事実の数は設定（`FACT_LOG_LIMIT`）に従い、プロンプトにも同じ数が載る
  経路   … 構造化出力を1度使えなかった provider では、以後その経路を試さない
  注入   … 会話へ足すのは NPC の**浅い複製**で、世界の NPC 本体と messages は触らない
  参照   … 311 の state は読むだけ。書き換わったら読み直す

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
from instantale_modloader import llm         # noqa: E402


# ---------------------------------------------------------------- 偽 Kivy
# `ui.Screen.schedule` は Kivy の Clock が無ければ諦める。
# ここでは次フレームの予約をその場で捌きたいので、記録するだけの Clock を置く。
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


MOD_PATH = find_mod("_npc_social_memory")
MANIFEST_PATH = os.path.join(os.path.dirname(MOD_PATH), "mod.json")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load_mod():
    spec = importlib.util.spec_from_file_location("npc_social_memory_under_test",
                                                  MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = load_mod()


# ---------------------------------------------------------------- 応答の正規化
print("応答の正規化")
IDS = ["80", "81"]

check("changed=false は「変更なし」で空",
      MOD.normalize_result({"changed": "false", "relations": [{"x": 1}]}, IDS) == [])
check("relations がリストでなければ読めない扱い",
      MOD.normalize_result({"changed": "true", "relations": "なし"}, IDS) is None)
check("参加していない id は落とす",
      MOD.normalize_result({"changed": "true", "relations": [
          {"observer_id": "80", "target_id": "99", "relationship": "怪しむ"}]}, IDS) == [])
check("自己参照は落とす",
      MOD.normalize_result({"changed": "true", "relations": [
          {"observer_id": "80", "target_id": "80", "relationship": "自分"}]}, IDS) == [])
check("同じ方向が2度来たら後を落とす",
      len(MOD.normalize_result({"changed": "true", "relations": [
          {"observer_id": "80", "target_id": "81", "relationship": "一つ目"},
          {"observer_id": "80", "target_id": "81", "relationship": "二つ目"}]}, IDS)) == 1)
check("A→B と B→A は別に残す",
      len(MOD.normalize_result({"changed": "true", "relations": [
          {"observer_id": "80", "target_id": "81", "relationship": "警戒"},
          {"observer_id": "81", "target_id": "80", "relationship": "好意"}]}, IDS)) == 2)


class Row(object):
    """provider によっては行が pydantic のモデルのまま返る。"""

    def __init__(self, **fields):
        self._fields = fields

    def model_dump(self):
        return dict(self._fields)


rows = MOD.normalize_result({"changed": "true", "relations": [
    Row(observer_id="80", target_id="81", relationship="警戒", new_facts=["嘘をついた"])]}, IDS)
check("モデルのまま返った行も辞書として読む",
      rows == [("80", "81", "警戒", ["嘘をついた"])], rows)

fenced = ('```json\n{"changed":"true","relations":[{"observer_id":"80",'
          '"target_id":"81","relationship":"警戒","new_facts":["嘘をついた"]}]}\n```')
check("非構造化応答も同じ検証を通る",
      MOD.parse_result(fenced, IDS) == [("80", "81", "警戒", ["嘘をついた"])],
      MOD.parse_result(fenced, IDS))
check("非構造化でも参加していない id は落とす",
      MOD.parse_result(fenced, ["80"]) == [], MOD.parse_result(fenced, ["80"]))
check("JSON でなければ読めない扱い", MOD.parse_result("わかりません", IDS) is None)


# ---------------------------------------------------------------- 設定と実装
print("設定と実装")
with io.open(MANIFEST_PATH, encoding="utf-8") as fh:
    MANIFEST = json.load(fh)
for key in ("CONVERSATION_TURNS", "RELATION_CHARS", "FACT_LOG_LIMIT",
            "INJECT_PARTY_PROFILE", "MAX_PARTICIPANTS"):
    check("{} の既定値がコードと mod.json で同じ".format(key),
          getattr(MOD, key) == MANIFEST["settings"][key]["default"],
          (getattr(MOD, key), MANIFEST["settings"][key]["default"]))


# ---------------------------------------------------------------- 偽ゲーム
class Character(object):
    def __init__(self, character_id, name, profile=""):
        self.id = character_id
        self.name = name
        self.profile = profile
        self.personality = ""
        self.job = ""


class World(object):
    def __init__(self, name, characters):
        self.name = name
        self.characters = characters


class InstantaleApp(object):
    def __init__(self, world_name="テスト世界"):
        self.world = World(world_name, {
            "player": Character("player", "主人公"),
            "80": Character("80", "エリス", "宿の娘。"),
            "81": Character("81", "ガルド", "傭兵。"),
        })
        self.party = ["player", "80", "81"]
        self.in_conversation = "80"
        self.current_conversation_history = [
            {"role": "user", "content": "ガルドをどう思う？"},
            {"role": "assistant", "content": "……あの人は信用できない。"},
        ]
        self.player = self.world.characters["player"]


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
        return self._under(self.out_dir, parts)

    def state_path(self, *parts):
        return self._under(self.state_dir, parts)

    @staticmethod
    def _under(root, parts):
        path = os.path.join(root, *parts)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        real = ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

        def write(message):
            self.notes.append(str(message))
            return real(message)
        return write

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


APP = None   # __main__ の属性として ui.find_app() に見つけてもらう

TURN = "__main__:ConversationPhaseManager.conversation_continued"
FACILITATOR = "scripts.llm.llm_manager:conversation_facilitator"


def fresh(app):
    """store を捨ててから当て直す。前の筋書きの控えを持ち越さない。"""
    global APP
    APP = app
    if hasattr(sys, MOD.STORE_ATTR):
        delattr(sys, MOD.STORE_ATTR)
    out_dir = tempfile.mkdtemp(prefix="npc_social_test_")
    ctx = Ctx(out_dir)
    MOD.apply(ctx)
    return ctx, out_dir


def state_file(ctx, app):
    from instantale_modloader.state import world_filename
    return os.path.join(ctx.state_dir, MOD.STATE_DIRNAME,
                        world_filename(app.world.name))


class FakeLLM(object):
    """`llm.ask` / `llm.create_structure` の差し替え。呼ばれ方を控える。"""

    def __init__(self, structured=None, plain=None, buildable=True):
        self.structured = structured
        self.plain = plain
        self.buildable = buildable
        self.calls = []          # (structured か, messages)

    def create_structure(self, ctx, name, fields, *, label="llm"):
        return dict if self.buildable else None

    def ask(self, ctx, manager_name, message, *, timeout, structure=None,
            max_tokens=None, label="llm", write=None):
        self.calls.append(("structured" if structure is not None else "plain", message))
        return self.structured if structure is not None else self.plain


def run_turn(ctx, app, fake):
    """会話が1ターン進んだところまで流し、抽出を終わらせる。"""
    original_ask, original_create = llm.ask, llm.create_structure
    llm.ask, llm.create_structure = fake.ask, fake.create_structure
    try:
        FakeClock.scheduled = []
        ctx.hooks[TURN](lambda self, text: None,
                        types.SimpleNamespace(app=app), "ガルドをどう思う？")
        FakeClock.run_all()          # enqueue を捌く
        store = getattr(sys, MOD.STORE_ATTR)
        store["jobs"].join()         # ワーカーが片付けるまで待つ
    finally:
        llm.ask, llm.create_structure = original_ask, original_create


def answer(facts):
    return {"changed": "true", "relations": [
        {"observer_id": "80", "target_id": "81",
         "relationship": "信用していない。", "new_facts": facts}]}


# ---------------------------------------------------------------- 抽出
print("抽出と保存")
app = InstantaleApp()
ctx, out_dir = fresh(app)
fake = FakeLLM(structured=answer(["嘘をついた"]))
run_turn(ctx, app, fake)

check("構造化経路を1回だけ呼ぶ",
      [kind for kind, _ in fake.calls] == ["structured"], fake.calls and
      [kind for kind, _ in fake.calls])
saved = json.load(io.open(state_file(ctx, app), encoding="utf-8"))
record = saved.get("80", {}).get("relations", {}).get("81", {})
check("関係が方向つきで残る", record.get("relationship") == "信用していない。", saved)
check("事実も残る", [x["text"] for x in record.get("facts", [])] == ["嘘をついた"], record)
check("逆方向は勝手に作らない", "81" not in saved, list(saved))

prompt = fake.calls[0][1][0]["content"]
check("プロンプトの件数は設定と同じ",
      "最大{}件".format(MOD.FACT_LOG_LIMIT) in prompt,
      [l for l in prompt.split("\n") if "new_facts" in l][:1])
check("relationship の目標長も設定と同じ",
      "おおむね{}文字".format(MOD.RELATION_CHARS) in prompt,
      [l for l in prompt.split("\n") if "おおむね" in l][:1])
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- 件数の上限
print("残す件数")
app = InstantaleApp()
ctx, out_dir = fresh(app)
many = ["事実{}".format(n) for n in range(MOD.FACT_LOG_LIMIT + 5)]
run_turn(ctx, app, FakeLLM(structured=answer(many)))
saved = json.load(io.open(state_file(ctx, app), encoding="utf-8"))
facts = saved["80"]["relations"]["81"]["facts"]
check("設定の件数までしか残さない", len(facts) == MOD.FACT_LOG_LIMIT, len(facts))
check("残るのは新しい方",
      facts[-1]["text"] == many[-1], facts[-1]["text"])
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- 経路の切り替え
print("構造化経路を使えない provider")
app = InstantaleApp()
ctx, out_dir = fresh(app)
plain = json.dumps(answer(["剣を折った"]), ensure_ascii=False)
fake = FakeLLM(structured=None, plain=plain)
run_turn(ctx, app, fake)
check("1回目は構造化を試してから降りる",
      [kind for kind, _ in fake.calls] == ["structured", "plain"],
      [kind for kind, _ in fake.calls])

fake.calls = []
run_turn(ctx, app, fake)
check("2回目からは構造化を試さない",
      [kind for kind, _ in fake.calls] == ["plain"],
      [kind for kind, _ in fake.calls])
saved = json.load(io.open(state_file(ctx, app), encoding="utf-8"))
check("非構造化でも保存まで届く",
      saved["80"]["relations"]["81"]["relationship"] == "信用していない。", saved)
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- 注入
print("会話への注入")
app = InstantaleApp()
ctx, out_dir = fresh(app)

profiles = os.path.join(ctx.state_dir, MOD.PROFILE_STATE_DIRNAME)
os.makedirs(profiles, exist_ok=True)
from instantale_modloader.state import world_filename       # noqa: E402
profile_path = os.path.join(profiles, world_filename(app.world.name))


def write_311(profile, about_player="値切ってくる客だと思っている。"):
    with io.open(profile_path, "w", encoding="utf-8", newline="") as fh:
        json.dump({"81": {"name": "ガルド", "profile": profile,
                          "about_player": about_player}}, fh, ensure_ascii=False)


write_311("金にうるさい傭兵。")

seen = {}


def facilitator(messages, life_log, player, character_instance, *args, **kwargs):
    seen["npc"] = character_instance
    seen["messages"] = messages
    return "返事"


messages = [{"role": "user", "content": "こんばんは"}]
npc = app.world.characters["80"]
ctx.hooks[FACILITATOR](facilitator, messages, [], app.player, npc)

check("会話に足すのは複製で、世界の NPC 本体は触らない",
      seen["npc"] is not npc and npc.profile == "宿の娘。", npc.profile)
check("同行者の見出しを足す", MOD.HEADING in seen["npc"].profile, seen["npc"].profile)
check("311 の人物像を読む", "金にうるさい傭兵。" in seen["npc"].profile,
      seen["npc"].profile)
check("同行者から見たプレイヤーもプレイヤー名つきで載せる",
      "ガルドから見た主人公: 値切ってくる客だと思っている。" in seen["npc"].profile,
      seen["npc"].profile)
check("messages は書き換えない", seen["messages"] is messages, seen["messages"])

write_311("戦働きしか知らない古参の傭兵。金の話はしない。")
ctx.hooks[FACILITATOR](facilitator, messages, [], app.player, npc)
check("311 が書き換えたら読み直す",
      "戦働きしか知らない" in seen["npc"].profile, seen["npc"].profile)
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(out_dir, ignore_errors=True)


print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
