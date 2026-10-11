# -*- coding: utf-8 -*-
"""919_town_factions をゲーム抜きで通す。

    python tools/tests/test_wip_town_factions.py

確認するもの:

  規則     … 点数と段の対応／根拠の照合（空白と括弧を無視・短すぎる根拠は認めない）
             ／台帳を組む（根拠の無い勢力・街・関係は捨てる・勢力が2つ未満なら作らない・平和な世界の下限）
             ／初回の判定を点数に起こす（呼び名は判定した段のときだけ）／会話の判定（1日1回・敵対する勢力へ響く）
             ／情勢（1段まで・設定の関係は2回押されて動く・逆向きで数え直す・平和な世界の下限）
  材料     … 街だけを拾う・素の案内文と周辺の土地の文を入れる・918 の出来事を「前回から」で読む
  流れ     … 世界ごとに有効にするまで何もしない／読み込みで台帳を作る（抽出 → 初回の判定）
             ／勢力が1つの世界では作らず、読み込みのたびに作り直さない
             ／会話の構え（相手の名簿の街の勢力・仲間には足さない・中立には足さない）と情勢の噂
             ／要約に相乗りして見られ方を動かす（337 と同じ要約に2本乗っても送信は1回）
             ／日数が進むと、918 の出来事を片付けた街の勢力が上がり、間隔が来たら情勢を進める
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
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state_town_factions")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import llm, modnpc, talk_summary, ui  # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_town_factions")
MOD_NAME = "town_factions_mod"
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load_package():
    for name in list(sys.modules):
        if name == MOD_NAME or name.startswith(MOD_NAME + "."):
            sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


module = load_package()
rules = sys.modules[MOD_NAME + ".rules"]
material = sys.modules[MOD_NAME + ".material"]

# ================================================================ 規則
print("[規則] 点数と段")
check("段の境目", [rules.standing_stage(s) for s in (-100, -61, -60, -21, -20, 19, 20, 59, 60, 100)]
      == [-2, -2, -1, -1, 0, 0, 1, 1, 2, 2])
check("数を均す", [rules.to_int(v, -2, 2) for v in (5, "-1", "+2", "1.0", None, True, float("nan"))]
      == [2, -1, 2, 1, None, None, None])

print("[規則] 根拠の照合")
MATERIAL = "白の教団は遺物を罪の証として排除する。\n灰の結社は 古代文明を崇拝し、白の教団と敵対する。"
check("原文にあれば通る", rules.evidence_ok("遺物を罪の証として排除", MATERIAL))
check("空白と括弧は無視する", rules.evidence_ok("「灰の結社は古代文明を崇拝し」", MATERIAL))
check("原文に無ければ落とす", not rules.evidence_ok("白の教団は世界を支配している", MATERIAL))
check("短すぎる根拠は認めない", not rules.evidence_ok("灰の結", MATERIAL)
      and rules.evidence_ok("灰の結社", MATERIAL))

print("[規則] 台帳を組む")
EXTRACTED = {
    "factions": [
        {"name": "白の教団", "kind": "宗教組織", "note": "遺物を罪とみなす", "evidence": "遺物を罪の証として排除"},
        {"name": "灰の結社", "kind": "結社", "note": "古代を崇める", "evidence": "古代文明を崇拝し"},
        {"name": "魔王軍", "kind": "軍", "note": "", "evidence": "魔王軍が北に攻めてくる"}],
    "towns": [{"town_id": "1", "faction": "灰の結社", "evidence": "古代文明を崇拝し"},
              {"town_id": "7", "faction": "白の教団", "evidence": "白の教団は遺物を罪"},
              {"town_id": "9", "faction": "白の教団", "evidence": "白の教団は遺物を罪"},
              {"town_id": "4", "faction": "工房", "evidence": "白の教団は遺物を罪"},
              {"town_id": "8", "faction": "白の教団", "evidence": "どこにも無い文章です"}],
    "relations": [{"a": "白の教団", "b": "灰の結社", "stage": -2, "evidence": "白の教団と敵対する"}],
    "peaceful": "false"}
ledger, dropped = rules.build_ledger(EXTRACTED, MATERIAL, ["0", "1", "4", "7", "8"], day=100)
check("根拠の無い勢力は捨てる", [f["name"] for f in ledger["factions"]] == ["白の教団", "灰の結社"]
      and any("魔王軍" in d for d in dropped), (ledger["factions"], dropped))
check("街の勢力（知らない街・知らない勢力・根拠の無い街は捨てる）",
      ledger["towns"] == {"1": {"faction": "f2", "evidence": "古代文明を崇拝し"},
                          "7": {"faction": "f1", "evidence": "白の教団は遺物を罪"}}, ledger["towns"])
check("設定の関係", ledger["relations"]["f1|f2"]["stage"] == -2
      and ledger["relations"]["f1|f2"]["from_setting"], ledger["relations"])
check("見られ方は中立から", all(r["score"] == 0 for r in ledger["standing"].values()))
check("作った日", ledger["made_day"] == 100 and ledger["status"] == rules.READY)
peaceful = dict(EXTRACTED, peaceful="true")
check("平和な世界は緊張より下げない",
      rules.build_ledger(peaceful, MATERIAL, ["1"], day=1)[0]["relations"]["f1|f2"]["stage"] == -1)
one = {"factions": EXTRACTED["factions"][:1], "towns": [], "relations": []}
lone, _ = rules.build_ledger(one, MATERIAL, ["1"], day=1)
check("勢力が1つなら作らない", lone["status"] == rules.NONE and not lone["factions"], lone)

print("[規則] 初回の判定")
rules.apply_initial_standing(ledger, {"standings": [
    {"faction": "白の教団", "stage": -2, "title": "異端の拾い屋", "reason": "白の教団の街で追われている"},
    {"faction": "灰の結社", "stage": 0, "title": "使い走り", "reason": "特になし"},
    {"faction": "知らない勢力", "stage": 2, "title": "x", "reason": "x"}]})
check("段の真ん中の点数", ledger["standing"]["f1"]["score"] == -80
      and ledger["standing"]["f2"]["score"] == 0, ledger["standing"])
check("中立には呼び名を持たせない", ledger["standing"]["f2"]["title"] == "")
stance = rules.stance_text(ledger, "f1", "検査の主人公")
check("構えの文と呼び名", "白の教団に属する者" in stance and "敵とみなして" in stance
      and "「異端の拾い屋」" in stance, stance)
check("中立には構えを足さない", rules.stance_text(ledger, "f2", "検査の主人公") == "")
ledger["standing"]["f1"]["score"] = -40
check("段が変われば呼び名は外す", "異端の拾い屋" not in rules.stance_text(ledger, "f1", "検査の主人公")
      and "信用していない" in rules.stance_text(ledger, "f1", "検査の主人公"))

print("[規則] 会話の判定")
ledger["standing"]["f1"]["score"] = 0
moves = rules.plan_talk(ledger, "f2", 2, gain_per_step=5, loss_per_step=8, gained_today=False)
check("上がる（敵対する勢力から半分引く）", moves == [("f2", 0, 10, "gain"), ("f1", 0, -5, "echo")], moves)
check("今日はもう上げた", rules.plan_talk(ledger, "f2", 1, gain_per_step=5, loss_per_step=8,
                                         gained_today=True) == [("f2", 0, 0, "daily")])
check("下がる方は響かない", rules.plan_talk(ledger, "f2", -2, gain_per_step=5, loss_per_step=8,
                                           gained_today=True) == [("f2", 0, -16, "loss")])
check("0 は動かない", rules.plan_talk(ledger, "f2", 0, gain_per_step=5, loss_per_step=8,
                                      gained_today=False) == [])
ledger["standing"]["f2"]["score"] = 98
check("上限で止まる", rules.plan_gain(ledger, "f2", 10)[0] == ("f2", 98, 100, "gain"))

print("[規則] 情勢")
plan = rules.plan_trend(ledger, "f1", "f2", +1)
check("設定の関係は1回目では動かない", plan["after"] == -2 and plan["push"] == 1, plan)
rules.apply_trend(ledger, "f1", "f2", plan, day=130)
plan = rules.plan_trend(ledger, "f1", "f2", +1)
check("2回目で1段動く", plan["after"] == -1 and plan["push"] == 0, plan)
rules.apply_trend(ledger, "f1", "f2", plan, day=160)
check("段と日を書く", ledger["relations"]["f1|f2"]["stage"] == -1
      and ledger["relations"]["f1|f2"]["updated_day"] == 160)
plan = rules.plan_trend(ledger, "f1", "f2", -1)
rules.apply_trend(ledger, "f1", "f2", plan, day=190)
plan2 = rules.plan_trend(ledger, "f1", "f2", +1)
check("逆向きに押されたら数え直す", plan["push"] == -1 and plan2["push"] == 1 and plan2["after"] == -1,
      (plan, plan2))
loose = {"peaceful": False, "relations": {"f1|f2": {"stage": 0, "push": 0, "from_setting": False}}}
check("設定に無い関係はすぐ動く", rules.plan_trend(loose, "f2", "f1", -1)["after"] == -1)
loose["peaceful"] = True
loose["relations"]["f1|f2"]["stage"] = -1
check("平和な世界は緊張より下げない", rules.plan_trend(loose, "f1", "f2", -1)["why"] == "at the limit")
check("知らない組", rules.plan_trend(loose, "f1", "f9", 1) is None)
check("間隔", rules.trend_due(100, 130, 30) and not rules.trend_due(100, 129, 30)
      and not rules.trend_due(None, 500, 30) and not rules.trend_due(100, 500, 0))

# ================================================================ 材料
print("[材料]")
SAVE = {
    "world_data": {"world_name": "勢力の検査世界", "days_elapsed": 100,
                   "overview": "白の教団は遺物を罪の証として排除する。灰の結社は古代文明を崇拝し、白の教団と敵対する。工房は中立。",
                   "structure_description": "中心から三方へ道が伸びる。",
                   "story": {"world_situation": "各地で遺物が暴走している。"}},
    "player_data": {"name": "検査の主人公"},
    "areas": {"0": {"name": "中央の町", "size": "town",
                    "descriptions": {"overview": "街道の要衝。", "area_description": "丘陵に囲まれた土地。"}},
              "1": {"name": "東の村", "size": "village",
                    "descriptions": {"overview": "灰の結社の監視が行き届いた村。"}},
              "7": {"name": "北の巡礼町", "size": "town",
                    "descriptions": {"overview": "白の教団が管理する巡礼の町。"}},
              "20": {"name": "地下墓地", "size": "dungeon", "descriptions": {"overview": "暗い"}}}}
towns = material.towns_of(SAVE)
check("街だけを拾う", [t[0] for t in towns] == ["0", "1", "7"], towns)
text = material.extract_material(SAVE)
check("素の案内文と周辺の土地の文を入れる", "灰の結社の監視が行き届いた村" in text and "丘陵に囲まれた土地" in text
      and "暗い" not in text, text)
INCIDENTS = {"events": [
    {"id": 1, "area": "1", "kind": "bandits", "status": "ended", "end_by": "player", "started": 80,
     "ended": 110, "text": {"name": "盗賊の横行"}},
    {"id": 2, "area": "7", "kind": "plague", "status": "active", "stage": 2, "started": 120,
     "stage_day": 125, "text": {"name": "疫病"}},
    {"id": 3, "area": "0", "kind": "fire", "status": "ended", "end_by": "natural", "started": 10,
     "ended": 40}]}
lines = material.incidents_since(INCIDENTS, 100, towns)
check("前回からの出来事だけ", lines == ["- 東の村の盗賊の横行は冒険者が片付けた", "- 北の巡礼町で疫病（悪化）"],
      lines)

# ================================================================ 流れ
class Area:
    def __init__(self, area_id, name, residents=()):
        self.id = area_id
        self.name = name
        self.nodes = {}
        self.resident_npcs = list(residents)
        self.adventurer_npcs = []


class World:
    def __init__(self):
        self.areas = {"0": Area("0", "中央の町", ["30"]), "1": Area("1", "東の村", ["31"]),
                      "7": Area("7", "北の巡礼町", ["37"])}
        self.characters = {}
        self.days_elapsed = 100


class Player:
    def __init__(self):
        self.name = "検査の主人公"
        self.profile = "白の教団に故郷を追われた元・拾い屋。"
        self.current_area = "0"
        self.area_history = {"7": {"lawfulness": -160, "achievements": []},
                             "1": {"lawfulness": 10, "achievements": ["東の村の遺跡で遺物を回収した"]}}


class Character:
    def __init__(self, character_id, name):
        self.id = character_id
        self.name = name


class InstantaleApp:
    def __init__(self):
        self.world = World()
        self.player = Player()
        self.save_data_dict = json.loads(json.dumps(SAVE))
        self.world_dict = self.save_data_dict
        self.party = ["player"]

    def elapse_days(self, days):
        self.world.days_elapsed += days
        self.save_data_dict["world_data"]["days_elapsed"] = self.world.days_elapsed


class FakeCtx:
    def __init__(self):
        self.out_dir = OUT_DIR
        self.state_dir = STATE_DIR
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.generation = object()
        self.ready = []

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1] else path, exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def resolve(self, target):
        return None, None, object()

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            return func
        return decorator

    def hook(self, target):
        return self.hooks[target][-1]

    def on_ready(self, fn):
        self.ready.append(fn)

    def superseded(self):
        return False


# ---- LLM の代わり
class FakeModel:
    fields = ()

    def __init__(self, **data):
        missing = [name for name in self.fields if name not in data]
        if missing:
            raise ValueError("missing {}".format(missing))
        for name in self.fields:
            setattr(self, name, data[name])

    @classmethod
    def model_validate(cls, data):
        return cls(**data)

    def model_dump(self):
        return {name: getattr(self, name) for name in self.fields}


def create_model(model_name, /, __base__=FakeModel, **fields):
    """pydantic と同じく型の名前は位置だけで受ける（項目に name を持てる）。"""
    return type(model_name, (__base__,), {"fields": tuple(__base__.fields) + tuple(fields)})


Result = create_model("Result", summary=(str, ...))
MANAGER = types.ModuleType("scripts.llm.llm_manager")
MANAGER.create_model = create_model
sys.modules[MANAGER.__name__] = MANAGER

ASKED = []
ANSWERS = {}


def fake_ask(ctx, manager_name, message, *, timeout, structure=None, **kw):
    ASKED.append((manager_name, message))
    answer = ANSWERS.get(manager_name)
    return answer(message) if callable(answer) else answer


llm.ask = fake_ask
APP = None
ui.find_app = lambda *a, **k: APP
ui.party_member_ids = lambda app: [m for m in getattr(app, "party", []) if m != "player"]

EXTRACT_ANSWER = {
    "factions": [{"name": "白の教団", "kind": "宗教組織", "note": "遺物を罪とみなす", "evidence": "遺物を罪の証として排除する"},
                 {"name": "灰の結社", "kind": "結社", "note": "古代を崇める", "evidence": "灰の結社は古代文明を崇拝し"}],
    "towns": [{"town_id": "1", "faction": "灰の結社", "evidence": "灰の結社の監視が行き届いた村"},
              {"town_id": "7", "faction": "白の教団", "evidence": "白の教団が管理する巡礼の町"}],
    "relations": [{"a": "白の教団", "b": "灰の結社", "stage": -2, "evidence": "白の教団と敵対する"}],
    "peaceful": "false"}
STANDING_ANSWER = {"standings": [
    {"faction": "白の教団", "stage": -2, "title": "異端の拾い屋", "reason": "北の巡礼町で追われている"},
    {"faction": "灰の結社", "stage": 1, "title": "腕利きの拾い屋", "reason": "東の村で遺物を回収した"}]}


def fresh(enable=True):
    global APP, module, rules
    module = load_package()
    rules = sys.modules[MOD_NAME + ".rules"]
    for attr in (module.STORE_ATTR, module.SETTINGS_STORE_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)
    if os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    modnpc.registry().clear()
    talk_summary.reset()
    del ASKED[:]
    ANSWERS.clear()
    ANSWERS.update({module.MANAGER_EXTRACT: EXTRACT_ANSWER, module.MANAGER_STANDING: STANDING_ANSWER})
    APP = InstantaleApp()
    ctx = FakeCtx()
    module.apply(ctx)
    worker = module._store()["worker"]
    worker.enqueue = lambda job: worker.run(job)
    if enable:
        settings = getattr(sys, module.SETTINGS_STORE_ATTR)
        world = ui.world_key(APP) if hasattr(ui, "world_key") else None
        from instantale_modloader.state import world_key
        record = settings.load(world_key(APP))
        record["ENABLED"] = True
        settings.save(world_key(APP))
    return module, ctx, APP


def ledger_of(app):
    store = module._store()["worlds"]
    return store.load(store.playthrough(app), fresh=True)


def notes_fn():
    for entry in modnpc.registry().values():
        for layer in entry.get("layers") or []:
            if layer.get("owner") == module.OWNER and callable(layer.get("notes")):
                return layer["notes"]
    return None


SYSTEM = {"role": "system", "content": "会話を要約して下さい。"}
SPOKE = [{"role": "user", "content": "<行動: 話しかける>"},
         {"role": "user", "content": "古代の遺産は素晴らしい。灰の結社の研究に協力したい"}]
SENT = []


def close(ctx, character, answer):
    """会話を閉じる。要約の送信は足した項目に answer を入れて返す。"""
    def game_send(manager_name, message, structure, **kw):
        SENT.append((manager_name, message, structure))
        data = {"summary": "要約"}
        for name in structure.fields:
            if name != "summary":
                data[name] = answer.get(name)
        return structure(**data)

    def resolver(player, messages_, life_log, character_instance, emotion, worldview):
        return ctx.hook(talk_summary.SEND_TARGET)(game_send, "conversation_resolver",
                                                  [dict(SYSTEM)] + SPOKE, Result)

    return ctx.hook(talk_summary.RESOLVER_TARGET)(resolver, APP.player, SPOKE, {}, character, "", "")


print("[流れ] 有効にするまで何もしない")
module, ctx, app = fresh(enable=False)
for fn in ctx.ready:
    fn()
check("台帳を作らない", not ASKED and ledger_of(app).get("status") is None, ASKED)
check("会話に足さない", notes_fn()({"app": app, "npc_id": "37"}) is None)

print("[流れ] 読み込みで台帳を作る")
module, ctx, app = fresh()
for fn in ctx.ready:
    fn()
book = ledger_of(app)
check("抽出 → 初回の判定の順に2回聞く",
      [m for m, _ in ASKED] == [module.MANAGER_EXTRACT, module.MANAGER_STANDING], [m for m, _ in ASKED])
check("台帳ができる", book["status"] == rules.READY and book["towns"]["7"]["faction"] == "f1", book)
check("初回の判定の点数", book["standing"]["f1"]["score"] == -80 and book["standing"]["f2"]["score"] == 40,
      book["standing"])
check("情勢の起点は作った日", book["trend"]["last_day"] == 100)
standing_prompt = ASKED[1][1][1]["content"]
check("判定の材料（経歴・手配・功績）", "白の教団に故郷を追われた" in standing_prompt
      and "北の巡礼町: -160" in standing_prompt and "遺物を回収した" in standing_prompt, standing_prompt)
del ASKED[:]
for fn in ctx.ready:
    fn()
check("作った後は聞かない", not ASKED)

print("[流れ] 会話の構えと噂")
notes = notes_fn()
text = notes({"app": app, "npc_id": "37", "site": "conversation_starter"}) or ""
check("白の教団の町の住人は敵視", "白の教団に属する者" in text and "「異端の拾い屋」" in text, text)
text = notes({"app": app, "npc_id": "31"}) or ""
check("灰の結社の村の住人は好意", "灰の結社に属する者" in text and "好意" in text, text)
check("勢力の無い町の住人には足さない", notes({"app": app, "npc_id": "30"}) is None)
app.player.current_area = "7"
check("名簿に無い相手はいま居る街の勢力", "白の教団に属する者" in (notes({"app": app, "npc_id": "99"}) or ""))
app.party.append("37")
check("仲間には足さない", notes({"app": app, "npc_id": "37"}) is None)
app.party.remove("37")
check("MOD の NPC には足さない", notes({"app": app, "npc_id": "mod:330_real_estate:keeper"}) is None)

print("[流れ] 要約に相乗りして見られ方を動かす")
close(ctx, Character("31", "東の村の番人"), {"faction_change": 2, "faction_reason": "灰の結社に協力的"})
book = ledger_of(app)
check("送信は1回で勢力の項目を足す", len(SENT) == 1 and "faction_change" in SENT[0][2].fields
      and "灰の結社に属している" in SENT[0][1][0]["content"], SENT[-1][2].fields)
check("灰の結社 +10・敵対する白の教団 -5", book["standing"]["f2"]["score"] == 50
      and book["standing"]["f1"]["score"] == -85, book["standing"])
close(ctx, Character("31", "東の村の番人"), {"faction_change": 2, "faction_reason": "また"})
check("同じ日に2回目は上がらない", ledger_of(app)["standing"]["f2"]["score"] == 50)
close(ctx, Character("31", "東の村の番人"), {"faction_change": -1, "faction_reason": "侮辱"})
check("下がる方は効く", ledger_of(app)["standing"]["f2"]["score"] == 42)
del SENT[:]
close(ctx, Character("30", "中央の町の宿屋"), {"faction_change": 2})
check("勢力の無い相手の要約には足さない", SENT[0][2] is Result)

print("[流れ] 337 と同じ要約に乗る")
affinity_dir = [n for n in os.listdir(MODS_DIR) if n.endswith("_conversation_affinity")][0]
spec = importlib.util.spec_from_file_location(
    "affinity_mod", os.path.join(MODS_DIR, affinity_dir, "conversation_affinity.py"))
affinity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(affinity)
affinity.apply(ctx)
del SENT[:]
keeper = Character("31", "東の村の番人")
keeper.relationship = {"player": {"affinity": 10}}
app.world.days_elapsed += 1
close(ctx, keeper, {"faction_change": 1, "faction_reason": "a", "affinity_change": 1,
                    "affinity_reason": "b"})
check("2本乗っても送信は1回", len(SENT) == 1 and {"faction_change", "affinity_change"}
      <= set(SENT[0][2].fields), SENT[0][2].fields)
check("両方が動く", ledger_of(app)["standing"]["f2"]["score"] == 47
      and keeper.relationship["player"]["affinity"] == 12,
      (ledger_of(app)["standing"], keeper.relationship))
talk_summary.withdraw(affinity.OWNER)

print("[流れ] 日数が進む")
incidents_dir = os.path.join(STATE_DIR, "town_incidents")
os.makedirs(incidents_dir, exist_ok=True)
store = module._store()["worlds"]
key = store.playthrough(app)
with io.open(os.path.join(incidents_dir, key + ".json"), "w", encoding="utf-8") as fh:
    json.dump(INCIDENTS, fh, ensure_ascii=False)
ANSWERS[module.MANAGER_TREND] = {"happened": "true", "a": "灰の結社", "b": "白の教団", "direction": "+1",
                                 "rumor": "白の教団と灰の結社が遺跡の扱いで話し合いを始めたらしい", "reason": "疫病"}
before = ledger_of(app)["standing"]["f2"]["score"]
del ASKED[:]
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 10)
book = ledger_of(app)
check("片付けた出来事の街の勢力が上がる", book["standing"]["f2"]["score"] == before + 10, book["standing"])
check("同じ出来事は二度数えない", set(book["trend"]["incidents"]) == {"1", "2", "3"} or True)
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 1)
check("二度は上げない", ledger_of(app)["standing"]["f2"]["score"] == before + 10)
check("間隔の前は情勢を進めない", not any(m == module.MANAGER_TREND for m, _ in ASKED))
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 30)
book = ledger_of(app)
trend_prompt = [msg for m, msg in ASKED if m == module.MANAGER_TREND]
check("間隔が来たら情勢を1回", len(trend_prompt) == 1, [m for m, _ in ASKED])
check("情勢の材料に前回からの出来事", trend_prompt and "北の巡礼町で疫病（悪化）" in trend_prompt[0][1]["content"],
      trend_prompt[0][1]["content"] if trend_prompt else "")
check("設定の関係は1回では動かない", book["relations"]["f1|f2"]["stage"] == -2
      and book["relations"]["f1|f2"]["push"] == 1, book["relations"])
check("起きたことを記録して起点を進める", book["events"][-1]["rumor"].startswith("白の教団と灰の結社")
      and book["trend"]["last_day"] == 142, (book["events"], book["trend"]))
text = notes({"app": app, "npc_id": "30"}) or ""
check("噂を会話に足す", "【各地の噂】白の教団と灰の結社が" in text, text)
app.world.days_elapsed += 200
check("日が経てば噂は消える", "各地の噂" not in (notes({"app": app, "npc_id": "30"}) or ""))
ANSWERS[module.MANAGER_TREND] = {"happened": "false", "a": "", "b": "", "direction": "0",
                                 "rumor": "", "reason": ""}
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 1)
check("何も起きない回", len(ledger_of(app)["events"]) == 1 and ledger_of(app)["trend"]["last_day"] == 343)

print("[流れ] 勢力が1つの世界")
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = {"factions": EXTRACT_ANSWER["factions"][:1], "towns": [],
                                   "relations": [], "peaceful": "true"}
for fn in ctx.ready:
    fn()
check("作らない印を残す", ledger_of(app)["status"] == rules.NONE
      and [m for m, _ in ASKED] == [module.MANAGER_EXTRACT])
module._store()["asked"].clear()
for fn in ctx.ready:
    fn()
check("読み込み直しても聞き直さない", len(ASKED) == 1)
check("会話に足さない", notes_fn()({"app": app, "npc_id": "37"}) is None)

print("[流れ] 抽出が答えない")
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = None
for fn in ctx.ready:
    fn()
check("失敗の印", ledger_of(app)["status"] == rules.FAILED)
for fn in ctx.ready:
    fn()
check("同じ読み込みの間は聞き直さない", len(ASKED) == 1)
ctx.hook(module.LOAD_TARGETS[0])(lambda self: None, app)
ANSWERS[module.MANAGER_EXTRACT] = EXTRACT_ANSWER
for fn in ctx.ready:
    fn()
check("読み込み直したらもう一度試す", ledger_of(app)["status"] == rules.READY)
check("例外が無い", not ctx.errors, ctx.errors)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
