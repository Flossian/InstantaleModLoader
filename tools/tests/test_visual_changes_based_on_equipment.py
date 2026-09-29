# -*- coding: utf-8 -*-
"""408_visual_changes_based_on_equipment をゲーム抜きで通す。

    python tools/tests/test_visual_changes_based_on_equipment.py

偽の app・LLM・画像生成関数を差し込み、次を確認する。

  関所     … SD1.5 へ渡すのは英小文字の4語以内だけ。日本語と括弧は落ち、足す分は60字まで
  足し方   … 元の prompt が文字列でも配列でも足す。元に在る語は足さない
  控え     … fingerprint が合えば LLM に聞かない。要素数を変えると聞き直す。
             装備を外すと項目を消す。使える語が無かった回は控えない。前の版の player_look を片付ける
  対象     … 選ばれている方式の関数だけを包む。読めなければ4つとも
  フック   … NPC の最初の生成は素通し、再生成で足す、主人公は経路を問わず足す、非 ASCII は足さない
"""
import importlib.util
import io
import json
import os
import sys
import types


HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_visual_changes_based_on_equipment")


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        print("  FAIL  {}  {!r}".format(label, detail))
        failures.append(label)


class FakeStore(object):
    def __init__(self):
        self.buckets = {}

    def playthrough(self, app=None):
        return "w|p"

    def load(self, key):
        return self.buckets.setdefault(key, {})

    def save(self, key):
        return True


logs = []
write = logs.append

# ---------------------------------------------------------------- 純関数
print("=== 関所 ===")
mod = load_module("visual_changes_under_test")
check("日本語は落ちる", mod._sd_item("黒い革鎧") == "")
check("大文字は小文字に", mod._sd_item("Black Leather Armor") == "black leather armor")
check("括弧は落ちる", mod._sd_item("(red cape:1.3)") == "")
check("5語は落ちる", mod._sd_item("a b c d e") == "")
check("箇条の印を剥がす", mod._sd_item("- iron helm") == "iron helm")
check("カンマで割る", mod._sd_items(["iron helm, red cape", "鎧"]) == ["iron helm", "red cape"])
check("上限の個数", mod._sd_items(["a1", "b2", "c3"], limit=2) == ["a1", "b2"])
got = mod._sd_items(["x" * 30, "y" * 30, "z"], budget=60)
check("字数の上限", got == ["x" * 30], got)
check("文字列1つも受ける",
      mod._sd_items("steel sword\nround shield") == ["steel sword", "round shield"])
check("重複しない", mod._sd_items(["a1", "A1"]) == ["a1"])

print("=== 足し方 ===")
check("文字列に足す",
      mod._append_prompt("young man, scar", ["iron helm"]) == "young man, scar, iron helm")
check("元に在る語は足さない",
      mod._append_prompt("young man, iron helm", ["iron helm"]) == "young man, iron helm")
check("配列に足す", mod._append_prompt(["young man"], ["iron helm"]) == ["young man", "iron helm"])
check("配列で重複なし", mod._append_prompt(["iron helm"], ["iron helm"]) == ["iron helm"])
check("空の元", mod._append_prompt("", ["iron helm"]) == "iron helm")

print("=== 控え ===")
asked = []


def fake_ask(ctx, rows, count, write):
    asked.append(count)
    return ["black leather armor", "steel sword"][:count]


mod._ask_equipment_prompt = fake_ask
store = FakeStore()
holder = {"name": "テスト"}
rows = [{"name": "革鎧", "description": "", "item_type": "wearable", "sub_type": ""}]
mod.EQUIPMENT_PROMPT_COUNT = 1
p1 = mod._prepare(None, store, write, None, holder, rows)
p2 = mod._prepare(None, store, write, None, holder, rows)
check("1回目は聞く・2回目は控え", p1 == p2 == ["black leather armor"] and asked == [1],
      (p1, p2, asked))
record = store.buckets["w|p"]["characters"]["テスト"]
check("控えに fingerprint と prompts", set(record) == {"fingerprint", "prompts"}, record)
mod.EQUIPMENT_PROMPT_COUNT = 2
p3 = mod._prepare(None, store, write, None, holder, rows)
check("要素数を変えると聞き直す",
      asked == [1, 2] and p3 == ["black leather armor", "steel sword"], (asked, p3))
mod.EQUIPMENT_PROMPT_COUNT = 99
check("設定の上限は3", mod._prompt_count() == 3)
mod.EQUIPMENT_PROMPT_COUNT = 1
check("装備を外すと項目を消す",
      mod._prepare(None, store, write, None, holder, []) == []
      and "テスト" not in store.buckets["w|p"]["characters"])
store.buckets["w|p"]["characters"]["テスト"] = {
    "fingerprint": mod._fingerprint(rows, 1), "prompts": ["黒い鎧"]}
check("控えの日本語も落ちる", mod._prepare(None, store, write, None, holder, rows) == [])
mod._ask_equipment_prompt = lambda *a: []
store2 = FakeStore()
mod._prepare(None, store2, write, None, holder, rows)
check("使える語が無い回は控えない",
      "テスト" not in store2.load("w|p").get("characters", {}))


store3 = FakeStore()
store3.buckets["w|p"] = {"player_look": {"source": "x", "prompts": ["old"]}, "characters": {}}
mod._ask_equipment_prompt = fake_ask
mod._prepare(None, store3, write, None, holder, rows)
check("前の版の player_look を片付ける", "player_look" not in store3.buckets["w|p"],
      store3.buckets["w|p"])

print("=== 対象 ===")
config = os.path.join(os.environ.get("TEMP", HERE), "test_408_config.json")
with io.open(config, "w", encoding="utf-8") as fh:
    json.dump({"ai_setting": {"local_model_setting": {"sd_backend": {"name": "sdcpp_vulkan"}}}}, fh)
check("config.json の方式を読む", mod.config_backend(config) == "sdcpp_vulkan")
os.remove(config)
check("読めなければ None", mod.config_backend(config) is None)
check("選ばれた方式だけ",
      mod.image_targets("diffusers_openvino")
      == ["image_generation.diffusers_openvino.image_generation_creature:generate_character_image"])
check("分からなければ4つとも", len(mod.image_targets(None)) == 4)


# ---------------------------------------------------------------- フック
print("=== フック ===")
mod = load_module("visual_changes_under_test_hooks")
del logs[:]


class Ctx(object):
    def __init__(self):
        self.wraps = {}

    def logger(self, *args, **kwargs):
        return logs.append

    def log(self, message):
        logs.append(message)

    def log_exc(self, message):
        logs.append("EXC " + message)

    def wrap(self, target, **kwargs):
        def decorator(fn):
            self.wraps[target] = fn
            return fn
        return decorator


hook_store = FakeStore()
mod._store = lambda ctx, write: hook_store
mod._ask_equipment_prompt = lambda ctx, rows, count, write: ["black leather armor"]
mod.config_backend = lambda path=None: "sdcpp_cuda"

hero = types.SimpleNamespace(
    name="主", id="0", category="young woman",
    equipments={"weapon": None, "wearable": "i1"},
    inventory={"i1": {"name": "革鎧", "description": "黒い革", "item_type": "wearable"}})
npc = types.SimpleNamespace(name="甲", id="5",
                            equipments={"weapon": {"name": "剣"}, "wearable": None})
app = types.SimpleNamespace(
    player=hero, world=types.SimpleNamespace(name="W", characters={"5": npc}),
    world_dict=None)
mod.ui.find_app = lambda: app

ctx = Ctx()
mod.apply(ctx)
target = "image_generation.sdcppcuda.image_generation_creature:generate_character_image"
check("選ばれた方式の関数を1つ包む", list(ctx.wraps) == [target], list(ctx.wraps))
hook = ctx.wraps[target]
seen = []


def orig(*args, **kwargs):
    seen.append((args, kwargs))
    return ("big", "small")


mod._is_regeneration = lambda: False
result = hook(orig, "W", "甲", "young man", ["young man", "scar"])
check("NPC の最初の生成は素通し",
      seen[-1][0][3] == ["young man", "scar"] and result == ("big", "small"))
hook(orig, "W", "主", "young woman", ["pale skin"])
check("主人公は再生成の経路でなくても足す（335 の描き直し）",
      seen[-1][0][3] == ["pale skin", "black leather armor"], seen[-1])

mod._is_regeneration = lambda: True
hook(orig, "W", "甲", "young man", "young man, scar")
check("NPC の再生成で足す（文字列）",
      seen[-1][0][3] == "young man, scar, black leather armor", seen[-1])
hook(orig, "W", "甲", "young man", ["young man"])
check("NPC の再生成で足す（配列）",
      seen[-1][0][3] == ["young man", "black leather armor"], seen[-1])
hook(orig, "W", "乙", "young man", ["young man"])
check("名簿に居ない人物は素通し", seen[-1][0][3] == ["young man"], seen[-1])

mod._prepare = lambda *a: ["黒"]
hook(orig, "W", "甲", "young man", ["young man"])
check("非ASCIIは足さない",
      seen[-1][0][3] == ["young man"] and any("non-ASCII" in str(line) for line in logs))
check("例外なし", not any(str(line).startswith("EXC") for line in logs),
      [line for line in logs if str(line).startswith("EXC")])

print("\n{} failure(s)".format(len(failures)))
sys.exit(1 if failures else 0)
