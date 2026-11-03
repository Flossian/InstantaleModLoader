# -*- coding: utf-8 -*-
"""243_probe_value_curves をゲーム抜きで通す。

    python tools/tests/test_value_curves_probe.py

偽の `scripts.functions` と app を差し込み、次を確認する。

  曲線     … 76 で止まる関数は `plateau_from` が 76、乱数で揺らぐ関数は最小・中央・最大、
             75 以上で投げる関数は最初の例外が 75。定数も1行
  逆引き   … 正引きの戻りと、その最大より上を渡す
  品の値段 … 持ち物の写しに価値と能力値を差し替えて渡し、元の品は変えない
  1回だけ  … 2回目のロードでは流し直さない
  乱数     … 流した後の乱数の列は、流す前と同じ
  出どころ … 品を作る入口の中の `get_weapon_spec` が `link` の行に束なる
  切り     … 実際に切った回だけ、呼び出し元と引数の組ごとに1回
"""
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml       # noqa: E402
from instantale_modloader import gates  # noqa: E402


def find_mod(suffix):
    """mod は **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽のゲーム
functions = types.ModuleType("scripts.functions")
functions.QUEST_DIFFICULTY_VALUE_MAX = 76
functions.QUEST_DIFFICULTY_VALUE_MIN = 0


def clamp_quest_difficulty_value(value):
    return max(0, min(76, value))


def get_quest_reward(quest_difficulty):
    return clamp_quest_difficulty_value(quest_difficulty) * 300


def get_weapon_spec(value):
    base = 16 + 4.3 * min(value, 76)
    return int(base * random.uniform(0.9, 1.1))


def get_talent_point_by_quest_level(cleared_quest_difficulty):
    if cleared_quest_difficulty >= 75:
        raise KeyError(cleared_quest_difficulty)
    return 1


def get_equipment_price(quest_difficulty):
    return min(quest_difficulty, 76) * 2


def get_equipment_level_from_price(item_price):
    table = {v * 2: v for v in range(0, 77)}
    return table[item_price]


def get_item_base_price(item_instance):
    return item_instance.value * 10 + item_instance.attributes.get("攻撃力", 0)


def get_enemy_exp_lvl(enemy_tier, quest_difficulty):
    return min(quest_difficulty, 100) + 1


for _f in (clamp_quest_difficulty_value, get_quest_reward, get_weapon_spec,
           get_talent_point_by_quest_level, get_equipment_price,
           get_equipment_level_from_price, get_item_base_price, get_enemy_exp_lvl):
    setattr(functions, _f.__name__, _f)
sys.modules["scripts.functions"] = functions


class Item(object):
    def __init__(self, item_type, value, attributes):
        self.item_type = item_type
        self.value = value
        self.attributes = attributes
        self.rarity = "common"


class App(object):
    def __init__(self):
        self.sword = Item("weapon", 30, {"item_detail": "small_weapon", "攻撃力": 120})
        self.player = types.SimpleNamespace(inventory={"item_0": self.sword})


OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "value_curves"))
shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)


class Ctx(object):
    def __init__(self):
        self.hooks, self.rows, self.lines, self.errors = {}, [], [], []

    def logger(self, name, **kw):
        return self.lines.append

    def jsonl(self, name, **kw):
        return self.rows.append

    def out_path(self, *parts):
        return os.path.join(OUT_DIR, *parts)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def log(self, msg, level="INFO"):
        self.lines.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


spec = importlib.util.spec_from_file_location("probe_value_curves_under_test",
                                              find_mod("_probe_value_curves"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.REPEAT = 4
for _mark in (mod.SWEPT_MARK, mod.COUNT_MARK):
    if hasattr(sys, _mark):
        delattr(sys, _mark)

gates.reset_all()
ctx = Ctx()
mod.apply(ctx)
app = App()
load = ctx.hooks.get("__main__:InstantaleApp.load_game_new")
check("ロードの関所が立つ", callable(load), sorted(ctx.hooks))


def rows(kind):
    return [row for row in ctx.rows if row.get("kind") == kind]


def curve(name):
    found = [row for row in rows("curve") if row["fn"] == name]
    return found[0] if found else None


random.seed(1234)
expected_next = [random.random() for _ in range(3)]
random.seed(1234)
load(lambda *a, **k: None, app)
after = [random.random() for _ in range(3)]

print("曲線")
reward = curve("get_quest_reward")
check("76 で止まる関数は plateau_from が 76", reward and reward["plateau_from"] == 76, reward and reward["plateau_from"])
check("0〜100 を全部測る", reward and [v for v, _c in reward["table"]] == list(range(0, 101)))
spec_row = curve("get_weapon_spec")
cell = dict((v, c) for v, c in spec_row["table"])[50] if spec_row else {}
check("揺らぐ関数は最小・中央・最大", {"min", "median", "max"} <= set(cell), cell)
talent = curve("get_talent_point_by_quest_level")
check("75 以上で投げる関数は最初の例外が 75", talent and talent["first_error"][0] == 75, talent and talent["first_error"])
enemy = curve("get_enemy_exp_lvl:boss")
check("段ごとの敵は段ごとに測る", enemy and enemy["plateau_from"] == 100, enemy and enemy["plateau_from"])
check("無い関数は missing の行", any(r["fn"] == "get_labor_reward" for r in rows("missing")))
const = rows("const")
check("定数が1行", len(const) == 1 and const[0]["values"]["QUEST_DIFFICULTY_VALUE_MAX"] == 76, const)

print("逆引き")
inverse = [r for r in rows("inverse") if r["fn"] == "get_equipment_level_from_price"]
probed = [p for p, _c in inverse[0]["table"]] if inverse else []
check("正引きの戻りとその上を渡す", probed[-2:] == [153, 304], probed[-3:])
check("定義域の外は例外として残る",
      inverse and "error" in dict((p, c) for p, c in inverse[0]["table"])[153], inverse)

print("品の値段")
prices = [r for r in rows("item_price") if r.get("item_type") == "weapon"]
table = dict((v, c) for v, c in prices[0]["table"]) if prices else {}
check("写しに価値と能力値を差し替えて渡す",
      table.get(100, {}).get("base", {}).get("result") == 100 * 10 + table[100]["stat"],
      table.get(100))
check("元の品は変えない", app.sword.value == 30 and app.sword.attributes["攻撃力"] == 120,
      (app.sword.value, app.sword.attributes))
check("持ち物に無い種類は飛ばした行", any(r.get("skipped") for r in rows("item_price")))

print("乱数と1回だけ")
check("流した後の乱数の列は流す前と同じ", after == expected_next, (after, expected_next))
check("流している間の印は外れている", not hasattr(sys, mod.DRY_THREAD_MARK))
count = len(rows("curve"))
load(lambda *a, **k: None, app)
check("2回目のロードでは流し直さない", len(rows("curve")) == count, len(rows("curve")))
check("例外を出していない", not ctx.errors, ctx.errors)

print("出どころ")
spec_hook = ctx.hooks["scripts.functions:get_weapon_spec"]
entry = ctx.hooks["__main__:InstantaleApp.generate_item_from_item_data"]


def make(self, data):
    stat = spec_hook(get_weapon_spec, data["value"])
    return Item("wearable", data["value"], {"item_detail": "body_armor", "防御力": stat})


made = entry(make, app, {"value": 24})
links = rows("link")
check("入口の中の get_weapon_spec が link の行に束なる",
      links and links[-1]["spec_calls"][0][0] == "get_weapon_spec"
      and links[-1]["stats"]["防御力"] == links[-1]["spec_calls"][0][2], links)
check("戻りは変えない", isinstance(made, Item) and made.value == 24)
spec_hook(get_weapon_spec, 10)
check("入口の外の呼び出しは束ねない", len(rows("link")) == len(links))

print("出どころ（戻りが品でない入口）")
init_hook = ctx.hooks["scripts.items:Item.__init__"]


def build(item_type, value, attributes):
    """ゲームの `Item(...)` の代わり。`Item.__init__` の包みを通して組む。"""
    item = Item.__new__(Item)
    init_hook(Item.__init__, item, item_type, value, attributes)
    item.name = "鉄の剣"
    return item


def make_tuple(self, data):
    stat = spec_hook(get_weapon_spec, data["value"])
    return (build("weapon", data["value"], {"item_detail": "small_weapon", "攻撃力": stat}), True)


before_links = len(rows("link"))
result = entry(make_tuple, app, {"value": 40})
tuple_links = rows("link")[before_links:]
check("戻りは変えない（tuple）", isinstance(result, tuple) and result[1] is True)
check("窓の間に組まれた品が link の行に出る",
      len(tuple_links) == 1 and tuple_links[0]["item_type"] == "weapon"
      and tuple_links[0]["value"] == 40 and tuple_links[0]["stats"]["攻撃力"] == tuple_links[0]["spec_calls"][0][2],
      tuple_links)
check("同じ品を2度数えない（組まれた品と戻りの中の品）",
      tuple_links and len(tuple_links[0]["items"]) == 1, tuple_links)
shapes = [r for r in rows("link_result") if r["entry"] == "generate_item_from_item_data"]
check("戻りの形は入口ごとに1回", len(shapes) == 1, shapes)
from_dict = ctx.hooks["__main__:InstantaleApp.generate_item_from_dict"]
before_links = len(rows("link"))
from_dict(lambda self, data: None, app, {})
check("品が取れない回は link を書かず数えない",
      len(rows("link")) == before_links
      and mod.counts()["link"].get("generate_item_from_dict", 0) == 0, mod.counts()["link"])
from_dict(lambda self, data: (build("material", 3, {"item_detail": "ore"}), None, 7), app, {})
shapes = [r for r in rows("link_result") if r["entry"] == "generate_item_from_dict"]
check("戻りの形は要素ごとの型名と品の名前",
      len(shapes) == 1 and shapes[0]["result"]["type"] == "NoneType", shapes)
check("2回目の入口では戻りの形を書かない",
      len([r for r in rows("link_result") if r["entry"] == "generate_item_from_dict"]) == 1)
check("組まれた品は入口ごとに数える",
      mod.counts()["link"].get("generate_item_from_dict") == 1 and rows("link")[-1]["item_type"] == "material",
      mod.counts()["link"])
mod.LINK_SAMPLES = 1
before_links = len(rows("link"))
from_dict(lambda self, data: build("material", 4, {"item_detail": "ore"}), app, {})
check("上限は入口ごと（上限の後は書かない）", len(rows("link")) == before_links)
ready = ctx.hooks["__main__:InstantaleApp.generate_item_from_ready_made_data"]
ready(lambda self, data: build("weapon", 5, {"item_detail": "small_weapon"}), app, {})
check("別の入口はまだ書ける", len(rows("link")) == before_links + 1
      and rows("link")[-1]["entry"] == "generate_item_from_ready_made_data", rows("link")[-1:])
mod.LINK_SAMPLES = 60

print("切り")
clamp = ctx.hooks["scripts.functions:clamp_quest_difficulty_value"]
for _arg in (50, 83, 83):
    clamp(clamp_quest_difficulty_value, _arg)
cut = rows("clamp")
check("切った回だけ、組ごとに1回", len(cut) == 1 and cut[0]["arg"] == 83 and cut[0]["result"] == 76, cut)
mod.CLAMP_SAMPLES = 1
clamp(clamp_quest_difficulty_value, 90)
check("上限の後は書かない", len(rows("clamp")) == 1, rows("clamp"))
mod.CLAMP_SAMPLES = 40

print("指紋（次のプロセス）")
check("全表と指紋の控えを書く", os.path.isfile(os.path.join(OUT_DIR, mod.CURVES_BASENAME)))


def sweep_again():
    """1プロセス1回の印を外し、別のプロセスとして流し直す。"""
    if hasattr(sys, mod.SWEPT_MARK):
        delattr(sys, mod.SWEPT_MARK)
    fresh = Ctx()
    mod.apply(fresh)
    fresh.hooks["__main__:InstantaleApp.load_game_new"](lambda *a, **k: None, app)
    return fresh


gates.reset_all()
second = sweep_again()
curves2 = [r for r in second.rows if r.get("kind") == "curve"]
same2 = [r for r in second.rows if r.get("kind") == "curve_same"]
check("前回と同じなら全表を出さない（乱数を引く関数も）", not curves2, [r["fn"] for r in curves2])
check("同じ曲線は1行にまとめる", len(same2) == 1 and "get_weapon_spec" in same2[0]["fns"], same2)
functions.get_quest_reward = lambda quest_difficulty: min(quest_difficulty, 90) * 300
setattr(sys, mod.DRY_THREAD_MARK, 12345)              # 別の試しが置いた印
gates.reset_all()
third = sweep_again()
curves3 = [r for r in third.rows if r.get("kind") == "curve"]
check("変わった曲線だけ全表を出す", [r["fn"] for r in curves3] == ["get_quest_reward"], [r["fn"] for r in curves3])
check("前の値を添える", curves3 and curves3[0]["previous"]["plateau_from"] == 76
      and curves3[0]["plateau_from"] == 90, curves3 and curves3[0].get("previous"))
check("試し打ちの印は置く前の値へ戻す", getattr(sys, mod.DRY_THREAD_MARK, None) == 12345)
delattr(sys, mod.DRY_THREAD_MARK)
check("例外を出していない（2回目以降）", not second.errors and not third.errors, (second.errors, third.errors))

print()
print("FAILED: {}".format(failures) if failures else "all ok")
sys.exit(1 if failures else 0)
