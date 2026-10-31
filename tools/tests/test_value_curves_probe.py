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
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

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


class Ctx(object):
    def __init__(self):
        self.hooks, self.rows, self.lines, self.errors = {}, [], [], []

    def logger(self, name, **kw):
        return self.lines.append

    def jsonl(self, name, **kw):
        return self.rows.append

    def out_path(self, *parts):
        return os.path.join("out", *parts)

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

print("切り")
clamp = ctx.hooks["scripts.functions:clamp_quest_difficulty_value"]
for _arg in (50, 83, 83):
    clamp(clamp_quest_difficulty_value, _arg)
cut = rows("clamp")
check("切った回だけ、組ごとに1回", len(cut) == 1 and cut[0]["arg"] == 83 and cut[0]["result"] == 76, cut)

print()
print("FAILED: {}".format(failures) if failures else "all ok")
sys.exit(1 if failures else 0)
