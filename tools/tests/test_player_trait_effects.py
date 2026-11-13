# -*- coding: utf-8 -*-
"""924_player_trait_effects（開発中）をゲーム抜きで通す。

    python tools/tests/test_wip_player_trait_effects.py

  決まり     … 返答の検算（一覧に無いもの・範囲の外を捨てる、同じ効果を足す、名前の揺れ）、重さ、寄せる
  頼み文     … severity は「渡さない」のときだけ載らない。変換の目印が付く
  能力値     … 変換の前は答えない。常に効く補正と、条件のときだけ効く補正。上限。主人公にだけ答える
  値段       … 買値・売値に割合を掛ける。効果が無ければ触らない
  控え       … 人物欄の行（121 との取り決め）。特質が変われば頼み直し、消えた特質は片付ける
  LLM        … 特質が載った頼みにだけ効果の一覧を足す。二重に足さない。変換の頼みには足さない
  読めない   … 返事が読めなければ何も控えない
"""
import importlib.util
import json
import os
import shutil
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "trait_effects"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import abilities, prices, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix) and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find *{} in {}: {}".format(suffix, MODS_DIR, matches))
    return os.path.join(MODS_DIR, matches[0])


def load(name, folder, path):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    _mod = "924_player_trait_effects"

    def __init__(self, out_dir, mod_dir):
        self.out_dir = out_dir
        self.mod_dir = mod_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def write_json(self, path, data):
        return ml.write_json(path, data, report=self.log_exc)

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, func)
            return func
        return decorator


class Person(object):
    def __init__(self, name, traits):
        self.name = name
        self.traits = traits
        self.profile = "坑道で育った測量士。"
        self.current_hp = 100
        self.max_hp = 100
        self.current_area = None


TRAITS = [
    {"name": "坑夫の勘", "description": "地下で罠や崩落の気配を察知できる。", "severity": 3},
    {"name": "煤の肺", "description": "呼吸器が弱く、持久戦で息切れしやすい。", "severity": -3},
    {"name": "値切りの目", "description": "相手の懐具合を見抜き、安く買い高く売る。", "severity": 2},
]

REPLY = {"traits": [
    {"name": "坑夫の勘", "effects": [{"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "always"}],
     "note": ""},
    {"name": "煤の肺", "effects": [{"kind": "ability", "stat": "constitution", "amount": -2, "condition": "battle"}],
     "note": "密閉された場所では息が切れやすい"},
    {"name": "値切りの目", "effects": [{"kind": "price", "side": "buy", "percent": -5},
                                    {"kind": "price", "side": "sell", "percent": 5}], "note": ""},
]}


# ================================================================== 決まり
folder = find_mod("_player_trait_effects")
package = os.path.basename(folder)
sys.modules["scripts.languages"] = types.SimpleNamespace(language="ja")
pkg = types.ModuleType("mod924")
pkg.__path__ = [folder]
sys.modules["mod924"] = pkg
rules = load("mod924.rules", folder, os.path.join(folder, "rules.py"))

print("返答の検算")
fields = rules.read_traits(TRAITS)
check("特質を読む", [f[0] for f in fields] == ["坑夫の勘", "煤の肺", "値切りの目"] and fields[1][2] == -3)
check("名前の無い特質は捨てる", rules.read_traits([{"description": "x"}, "文字列", None]) == [])
check("印は文で変わる", rules.fingerprint(fields[0]) != rules.fingerprint(
    ("坑夫の勘", "地下で罠を察知できる。", 3)))
messy = {"traits": [
    {"name": " 坑夫の勘 ", "effects": [
        {"kind": "ability", "stat": "wisdom", "amount": "+2", "condition": "always"},
        {"kind": "ability", "stat": "wisdom", "amount": 9, "condition": "always"},
        {"kind": "ability", "stat": "luck", "amount": 2},
        {"kind": "ability", "stat": "dexterity", "amount": 0},
        {"kind": "ability", "stat": "dexterity", "amount": 1, "condition": "at_night"},
        {"kind": "resist", "percent": 30}]},
    {"name": "煤の肺（重い）", "effects": [{"kind": "price", "side": "buy", "percent": -50}], "note": "x" * 200},
    {"name": "知らない特質", "effects": [{"kind": "ability", "stat": "wisdom", "amount": 1}]},
]}
parsed = rules.normalize(messy, fields)
check("同じ能力値・条件は足して上限で止める",
      parsed["坑夫の勘"]["effects"] == [{"kind": "ability", "stat": "wisdom", "amount": 3, "condition": "always"}],
      parsed.get("坑夫の勘"))
check("名前の揺れは1つに決まるときだけ拾う", "煤の肺" in parsed and "知らない特質" not in parsed, list(parsed))
check("値段の割合は上限で止める", parsed["煤の肺"]["effects"][0]["percent"] == -rules.PERCENT_MAX)
check("性質の文は長さで切る", len(parsed["煤の肺"]["note"]) <= rules.NOTE_CHARS + 1)
check("形の違う返答は読めない", rules.normalize({"x": 1}, fields) is None and rules.normalize("文", fields) is None)
check("効果は1つの特質に3つまで", len(rules.normalize({"traits": [{"name": "坑夫の勘", "effects": [
    {"kind": "ability", "stat": s, "amount": 1} for s in rules.STATS]}]}, fields)["坑夫の勘"]["effects"])
      == rules.EFFECTS_PER_TRAIT)
stacked = rules.normalize({"traits": [{"name": "坑夫の勘", "effects": [
    {"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "battle"},
    {"kind": "ability", "stat": "wisdom", "amount": 3, "condition": "quest"},
    {"kind": "ability", "stat": "wisdom", "amount": -1, "condition": "low_hp"}]}]}, fields)["坑夫の勘"]["effects"]
check("同じ能力値の利点は1つ（大きい方）、代償は別に1つ",
      stacked == [{"kind": "ability", "stat": "wisdom", "amount": 3, "condition": "quest"},
                  {"kind": "ability", "stat": "wisdom", "amount": -1, "condition": "low_hp"}], stacked)
tied = rules.normalize({"traits": [{"name": "坑夫の勘", "effects": [
    {"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "boss"},
    {"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "always"}]}]}, fields)["坑夫の勘"]["effects"]
check("同じ量なら効く場面の広い方", tied == [{"kind": "ability", "stat": "wisdom", "amount": 2,
                                              "condition": "always"}], tied)

print("重さ")
check("常に効く補正は量", rules.weight([{"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "always"}]) == 2)
check("戦闘中・依頼中は 0.8", rules.weight([{"kind": "ability", "stat": "constitution", "amount": -2,
                                         "condition": "battle"}]) == -1.6
      and rules.weight([{"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "quest"}]) == 1.6)
check("ボス戦 0.4・体力半分以下 0.6",
      rules.weight([{"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "boss"}]) == 0.8
      and rules.weight([{"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "low_hp"}]) == 1.2)
check("買値の値下げは得（-5% で 1）",
      rules.weight([{"kind": "price", "side": "buy", "percent": -5}]) == 1
      and rules.weight([{"kind": "price", "side": "sell", "percent": 5}]) == 1)
check("目安の重さは severity の半分", rules.target_weight(3) == 1.5 and rules.target_weight(-4) == -2)
big = [{"kind": "ability", "stat": "wisdom", "amount": 3, "condition": "always"},
       {"kind": "ability", "stat": "strength", "amount": 3, "condition": "always"}]
fitted, done = rules.fit_to_severity(big, 4)
check("寄せる: 同じ向きなら目安（severity の半分）まで縮める", done and rules.weight(fitted) == 2, fitted)
check("寄せる: 向きが逆なら触らない", rules.fit_to_severity(big, -3) == (big, False))
check("寄せる: 目安に近ければ触らない", rules.fit_to_severity(big, 12) == (big, False))
check("寄せる: 0 にはしない", all(e["amount"] for e in rules.fit_to_severity(big, 1)[0]))
check("量の上限は ±3", rules.clean_effect({"kind": "ability", "stat": "wisdom", "amount": 9})["amount"] == 3
      and rules.clean_effect({"kind": "ability", "stat": "wisdom", "amount": -7})["amount"] == -3)
guide_text = rules.build_messages("x", "", [("a", "b", 4)], "guide")[1]["content"]
check("頼み文: 目安は severity の半分、量は ±3", "severity の半分" in guide_text and "-3〜+3" in guide_text
      and "依頼の最中は 0.8" in guide_text)

print("頼み文")
guide = rules.build_messages("試験太郎", "経歴", fields, "guide")[1]["content"]
off = rules.build_messages("試験太郎", "経歴", fields, "off")[1]["content"]
check("目安として渡すと severity が載る", "severity: -3" in guide and "重さ" in guide)
check("渡さないと載らない", "severity" not in off)
check("変換の目印が付く", rules.CONVERTER_MARK in guide)

print("表示")
records = [rules.normalize(REPLY, fields)[name] for name in ("坑夫の勘", "煤の肺", "値切りの目")]
check("効果の文", [rules.summary(r) for r in records] == ["判断+2", "戦闘中耐久-2", "買値-5%・売値+5%"],
      [rules.summary(r) for r in records])
block = rules.note_block(list(zip(["坑夫の勘", "煤の肺", "値切りの目"], records)))
check("LLM へ足す文に効果と性質の文", block.startswith(rules.NOTE_HEAD) and "戦闘中耐久-2／密閉" in block, block)
check("効果も文も無ければ足さない", rules.note_block([("癖", {"effects": [], "note": ""})]) == "")

# ================================================================== MOD を通す
print("MOD")
shutil.rmtree(OUT_DIR, ignore_errors=True)
mod = load("mod924.player_trait_effects", folder, os.path.join(folder, "player_trait_effects.py"))
for attr in (abilities._REGISTRY_ATTR, mod.STORE_ATTR):
    if hasattr(sys, attr):
        delattr(sys, attr)
replies = []
asked = []


def fake_ask(ctx, manager, messages, **kw):
    asked.append(messages[-1]["content"])
    return replies.pop(0) if replies else None


mod.llm.ask = fake_ask
rewrites = []
mod.wrap_outgoing = lambda ctx, fn, **kw: rewrites.append(fn)

player = Person("試験太郎", [dict(t) for t in TRAITS])
ally = Person("仲間", [dict(TRAITS[0])])
app = types.SimpleNamespace(world_dict={"world_data": {"name": "試験世界"}}, player=player,
                            current_quest_data=None, in_battle=None, in_boss_battle=None, world=None)
ui.find_app = lambda: app
ctx = FakeCtx(OUT_DIR, folder)
mod.apply(ctx)
toggle = ctx.hooks["scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility"]
state_path = os.path.join(ctx.state_dir, mod.STATE_DIRNAME, "試験世界×試験太郎.json")


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def stored():
    return ctx.read_json(state_path, None) or {}


check("窓口に答えを置いた", package in abilities.owners(), abilities.owners())
check("変換の前は答えない", abilities.bonus(app, player) == {})
replies.append(json.dumps(REPLY, ensure_ascii=False))
toggle(lambda self: None, None)
check("変換して控える", wait_for(lambda: len(stored().get("traits", {})) == 3), stored())
check("頼みは1回", len(asked) == 1, len(asked))
check("常に効く補正", abilities.bonus(app, player) == {"wisdom": 2}, abilities.bonus(app, player))
app.in_battle = "normal"
check("戦闘中だけ効く補正", abilities.bonus(app, player) == {"wisdom": 2, "constitution": -2},
      abilities.bonus(app, player))
app.in_battle = None
check("仲間には答えない", abilities.bonus(app, ally) == {})

print("条件")
legacy_fx = {"kind": "ability", "stat": "strength", "amount": 2, "condition": "dungeon"}
quest_fx = {"kind": "ability", "stat": "dexterity", "amount": 1, "condition": "quest"}
low_fx = {"kind": "ability", "stat": "charisma", "amount": 1, "condition": "low_hp"}
worlds = sys.modules["mod924.player_trait_effects"].process_store(ctx)["worlds"]
with worlds.lock:
    bucket = worlds.load("試験世界×試験太郎")
    first = bucket["traits"][rules.fingerprint(fields[0])]
    first["effects"] = first["effects"] + [legacy_fx, quest_fx, low_fx]
check("条件の名前は窓口の場面と同じ", set(rules.CONDITIONS) == set(abilities.SITUATIONS))
check("依頼の外では依頼中の補正は効かない", "dexterity" not in abilities.bonus(app, player)
      and "strength" not in abilities.bonus(app, player))
check("場面: 依頼の外", abilities.situations(app, player) == {"always"})
app.current_quest_data = {"id": 5}
check("依頼の最中に効く（前の版のダンジョンの条件も依頼中として効く）",
      abilities.bonus(app, player).get("dexterity") == 1 and abilities.bonus(app, player).get("strength") == 2,
      abilities.bonus(app, player))
player.current_hp = 50
check("体力が半分以下で効く", abilities.bonus(app, player).get("charisma") == 1, abilities.bonus(app, player))
check("場面: 依頼中・体力半分以下", abilities.situations(app, player) == {"always", "quest", "low_hp"})
player.current_hp, app.current_quest_data = 100, None
check("返答のダンジョンの条件は依頼中に直す",
      rules.clean_effect(legacy_fx)["condition"] == "quest" and rules.effect_text(legacy_fx) == "依頼中筋力+2")
check("人物欄の行は効果ごとに条件を持つ（値段はいつでも）",
      rules.display_row("x", {"effects": [legacy_fx, {"kind": "price", "side": "buy", "percent": -5}]})["effects"]
      == [{"text": "依頼中筋力+2", "condition": "quest"}, {"text": "買値-5%", "condition": "always"}])
with worlds.lock:
    first["effects"] = first["effects"][:1]
mod.STAT_CAP = 1
check("上限", abilities.bonus(app, player) == {"wisdom": 1})
mod.STAT_CAP = 10
mod.USE_ABILITIES = False
check("切れば答えない", abilities.bonus(app, player) == {})
mod.USE_ABILITIES = True

registry = sys.modules["instantale_modloader.prices"]._item_registry()
layer = next(entry[1] for entry in registry["adjust"] if entry[0] == package)
check("買値が下がる", layer(None, prices.BUY, 1000) == 950, layer(None, prices.BUY, 1000))
check("売値が上がる", layer(None, prices.SELL, 1000) == 1050, layer(None, prices.SELL, 1000))

data = stored()
check("人物欄の行（121 との取り決め）",
      data.get("display") == [
          {"name": "坑夫の勘", "text": "判断+2", "effects": [{"text": "判断+2", "condition": "always"}]},
          {"name": "煤の肺", "text": "戦闘中耐久-2",
           "effects": [{"text": "戦闘中耐久-2", "condition": "battle"}]},
          {"name": "値切りの目", "text": "買値-5%・売値+5%",
           "effects": [{"text": "買値-5%", "condition": "always"}, {"text": "売値+5%", "condition": "always"}]}],
      data.get("display"))
check("severity と重さと決まりの版を控える", data["traits"][rules.fingerprint(fields[1])]["weight"] == -1.6
      and data["traits"][rules.fingerprint(fields[1])]["rules"] == rules.RULES_VERSION)

print("LLM へ足す文")
rewrite = rewrites[0]
prompt = ["system", "- 特質: [{'name': '坑夫の勘', 'description': '地下で罠や崩落の気配を察知できる。'}]"]
out = rewrite(prompt, "test")
check("特質が載った文の後ろに足す", out is not None and out[1].startswith(prompt[1]) and rules.NOTE_HEAD in out[1],
      out)
check("二重に足さない", rewrite(out, "test") is None)
check("特質が載っていなければ足さない", rewrite(["system", "ただの会話"], "test") is None)
check("変換の頼みには足さない", rewrite([rules.CONVERTER_MARK + " 坑夫の勘 地下で罠や崩落の気配"], "test") is None)
mod.NOTES_TO_AI = False
check("切れば足さない", rewrite(prompt, "test") is None)
mod.NOTES_TO_AI = True

print("特質が変わる")
player.traits[0]["description"] = "地下で罠を察知できる。"
replies.append(json.dumps({"traits": [{"name": "坑夫の勘", "effects": [
    {"kind": "ability", "stat": "intelligence", "amount": 1, "condition": "quest"}], "note": ""}]},
    ensure_ascii=False))
toggle(lambda self: None, None)
check("文が変われば頼み直す", wait_for(lambda: len(asked) == 2) and wait_for(
    lambda: any(r["text"] == "依頼中知力+1" for r in stored().get("display", []))), stored().get("display"))
check("古い文の控えは片付ける", len(stored().get("traits", {})) == 3, list(stored().get("traits", {})))
player.traits = player.traits[1:]
toggle(lambda self: None, None)
check("消えた特質は片付ける", wait_for(lambda: len(stored().get("traits", {})) == 2), list(stored().get("traits", {})))
check("片付けても頼まない", len(asked) == 2, len(asked))

print("読めない返事")
player.traits.append({"name": "古傷", "description": "雨の日に膝が痛む。", "severity": -1})
replies.append("ふさわしい効果はありません")
toggle(lambda self: None, None)
check("読めなければ控えない", wait_for(lambda: len(asked) == 3) and len(stored().get("traits", {})) == 2)

print("決まりの版")
lung = rules.fingerprint(rules.read_traits([TRAITS[1]])[0])
with worlds.lock:
    worlds.load("試験世界×試験太郎")["traits"][lung]["rules"] = 1
replies.append(json.dumps({"traits": [{"name": "煤の肺", "effects": [
    {"kind": "ability", "stat": "constitution", "amount": -1, "condition": "battle"}], "note": ""}]},
    ensure_ascii=False))
before_asked = len(asked)
toggle(lambda self: None, None)
check("古い版で置き換えた特質は頼み直す",
      wait_for(lambda: stored().get("traits", {}).get(lung, {}).get("rules") == rules.RULES_VERSION)
      and len(asked) == before_asked + 1 and "煤の肺" in asked[-1] and "値切りの目" not in asked[-1],
      stored().get("traits", {}).get(lung))

check("握り潰した例外が無い", not ctx.errors, ctx.errors[:2])
print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
