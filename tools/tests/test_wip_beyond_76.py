# -*- coding: utf-8 -*-
"""923_beyond_76（開発中）と窓口 value_limits をゲーム抜きで通す。

    python tools/tests/test_wip_beyond_76.py

偽の `scripts.functions`（76 で止まる曲線）を置き、包みを実際にその関数へ差し込んで確かめる。

  延ばす     … 報酬・日雇い・値段は (f(76) − f(66)) / 10 の傾きで 100 まで。76 までは素のまま。名前の引数でも
  乱数の数   … 武器と回復は定数の傾き。素の関数は 76 で1回だけ呼ぶ（乱数の進みが変わらない）
  才能点     … 10 段ごとに +1。0 で延ばさない
  逆引き     … 76 の値段より高ければ傾きで割って段に足す。100 で止まる
  設定       … 報酬を切れば報酬だけ素、品を切れば品と窓口の上限が素
  窓口       … 誰もいなければ 76。用済み・投げた登録は数えない。いちばん大きい値
  品を作る MOD … 921 の「価値として「N」を指定」は窓口の上限で切る
"""
import importlib.util
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "beyond_76"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import value_limits  # noqa: E402

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


# ----------------------------------------------------------------- 偽のゲーム
CALLS = []


def capped(v):
    return min(76, v)


def make_functions():
    fn = types.ModuleType("scripts.functions")
    fn.QUEST_DIFFICULTY_VALUE_MAX = 76

    def get_quest_reward(quest_difficulty):
        return 18178 + int((capped(quest_difficulty) - 66) * 603.8) if quest_difficulty >= 66 else 100

    def get_labor_reward(cleared_quest_difficulty):
        return 69 + 5 * capped(cleared_quest_difficulty)

    def get_equipment_price(quest_difficulty):
        return 10 * capped(quest_difficulty) * capped(quest_difficulty) // 10

    def get_heal_item_price(quest_difficulty):
        return 3 * capped(quest_difficulty)

    def get_other_item_price(quest_difficulty):
        return 3 * capped(quest_difficulty)

    def get_weapon_spec(value):
        CALLS.append(("weapon", value))
        return 4 * capped(value) + 10

    def get_heal_spec(value):
        CALLS.append(("heal", value))
        return 5.0 * capped(value)

    def get_talent_point_quest_clear(cleared_quest_difficulty):
        return 12 if cleared_quest_difficulty >= 67 else 5

    def level_from(price_fn):
        def inverse(item_price):
            best = 0
            for level in range(0, 77):
                if price_fn(level) <= item_price:
                    best = level
            return best
        return inverse

    for name, value in list(locals().items()):
        if name.startswith("get_"):
            setattr(fn, name, value)
    fn.get_equipment_level_from_price = level_from(get_equipment_price)
    fn.get_heal_item_level_from_price = level_from(get_heal_item_price)
    fn.get_other_item_level_from_price = level_from(get_other_item_price)
    return fn


class FakeCtx:
    _mod = "923_beyond_76"

    def __init__(self, out_dir, functions):
        self.out_dir = out_dir
        self.functions = functions
        self.errors = []
        self.logs = []
        self.gone = False

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def superseded(self):
        return self.gone

    def wrap(self, target, **kw):
        module, _, name = target.partition(":")
        assert module == "scripts.functions", target

        def decorator(func):
            orig = getattr(self.functions, name)
            setattr(self.functions, name, lambda *a, **k: func(orig, *a, **k))
            return func
        return decorator


def load(functions, **settings):
    path = os.path.join(find_mod("_beyond_76"), "beyond_76.py")
    spec = importlib.util.spec_from_file_location("beyond_76_mod", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for key, value in settings.items():
        setattr(module, key, value)
    ctx = FakeCtx(os.path.join(OUT_DIR, "out"), functions)
    module.apply(ctx)
    return module, ctx


def setup(**settings):
    value_limits.reset()
    raw = make_functions()
    patched = make_functions()
    sys.modules["scripts.functions"] = patched
    module, ctx = load(patched, **settings)
    return raw, patched, module, ctx


# ----------------------------------------------------------------- 検査
def test_extend():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    raw, fn, module, ctx = setup()
    check("76 までは素のまま", all(fn.get_quest_reward(v) == raw.get_quest_reward(v) for v in range(0, 77))
          and all(fn.get_equipment_price(v) == raw.get_equipment_price(v) for v in range(0, 77)))
    slope = (raw.get_quest_reward(76) - raw.get_quest_reward(66)) / 10.0
    check("報酬: 76 の値 + 傾き × 超えた分", fn.get_quest_reward(85) == int(round(raw.get_quest_reward(76) + slope * 9)),
          (fn.get_quest_reward(85), raw.get_quest_reward(76), slope))
    check("報酬: 100 で止まる", fn.get_quest_reward(120) == fn.get_quest_reward(100))
    check("名前の引数でも延ばす", fn.get_labor_reward(cleared_quest_difficulty=86) == raw.get_labor_reward(76) + 50)
    check("値段の基準も延ばす", fn.get_equipment_price(80) > raw.get_equipment_price(76))
    check("数でない値は素通し", fn.get_labor_reward(76.0) == raw.get_labor_reward(76.0))

    CALLS.clear()
    weapon = fn.get_weapon_spec(90)
    check("武器: 定数の傾き", weapon == int(round(raw.get_weapon_spec(76) + module.WEAPON_SLOPE * 14)), weapon)
    check("武器: 素の関数は 76 で1回だけ", [c for c in CALLS if c[0] == "weapon"][:1] == [("weapon", 76)]
          and len([c for c in CALLS if c[0] == "weapon"]) == 2)       # 2回目は上の比べの raw
    CALLS.clear()
    heal = fn.get_heal_spec(80)
    check("回復: 素の型（float）のまま", isinstance(heal, float) and abs(heal - (380.0 + module.HEAL_SLOPE * 4)) < 1e-6, heal)
    check("回復: 素の関数は1回", CALLS == [("heal", 76)], CALLS)

    check("才能点: 10 段ごとに +1", (fn.get_talent_point_quest_clear(76), fn.get_talent_point_quest_clear(85),
                                   fn.get_talent_point_quest_clear(86), fn.get_talent_point_quest_clear(100))
          == (12, 12, 13, 14))

    top_price = raw.get_equipment_price(76)
    check("逆引き: 76 までは素のまま", fn.get_equipment_level_from_price(top_price) == 76
          and fn.get_equipment_level_from_price(1000) == raw.get_equipment_level_from_price(1000))
    above = fn.get_equipment_level_from_price(fn.get_equipment_price(86))
    check("逆引き: 延ばした値段は延ばした段へ戻る", above in (85, 86), above)
    check("逆引き: 100 で止まる", fn.get_equipment_level_from_price(10 ** 7) == 100)
    check("窓口: 品を延ばしている間は 100", value_limits.value_max() == 100 and value_limits.raised_by() == "923_beyond_76")
    check("記録: ログに延ばした行", os.path.exists(os.path.join(OUT_DIR, "out", "beyond_76.log")))
    check("例外を記録していない", not ctx.errors, ctx.errors)


def test_settings():
    raw, fn, module, ctx = setup(REWARDS=False)
    check("報酬を切る: 報酬は素", fn.get_quest_reward(90) == raw.get_quest_reward(90)
          and fn.get_talent_point_quest_clear(96) == 12)
    check("報酬を切る: 品は延ばす", fn.get_weapon_spec(90) > raw.get_weapon_spec(76))
    raw, fn, module, ctx = setup(ITEMS=False)
    check("品を切る: 品は素", fn.get_equipment_price(90) == raw.get_equipment_price(90)
          and fn.get_equipment_level_from_price(10 ** 7) == 76)
    check("品を切る: 窓口は 76", value_limits.value_max() == 76 and value_limits.raised_by() is None)
    raw, fn, module, ctx = setup(TALENT_EVERY=0)
    check("才能点 0: 延ばさない", fn.get_talent_point_quest_clear(100) == 12)


def test_window():
    value_limits.reset()
    sys.modules.pop("scripts.functions", None)
    alive = types.SimpleNamespace(superseded=lambda: False)
    gone = types.SimpleNamespace(superseded=lambda: True)
    check("窓口: 誰もいなければ 76", value_limits.value_max() == 76 and value_limits.raised_by() is None)
    value_limits.provide("a", alive, lambda: 90)
    value_limits.provide("b", gone, lambda: 120)
    value_limits.provide("c", alive, lambda: 1 / 0)
    value_limits.provide("d", alive, lambda: 50)
    check("窓口: 生きている中でいちばん大きい値", value_limits.value_max() == 90 and value_limits.raised_by() == "a")
    check("窓口: 投げた登録は errors に", value_limits._store()["errors"])
    value_limits.withdraw("a")
    check("窓口: 76 より下にはしない", value_limits.value_max() == 76)
    value_limits.reset()


def test_prompt_errors():
    path = os.path.join(find_mod("_fix_prompt_errors"), "fix_prompt_errors.py")
    spec = importlib.util.spec_from_file_location("fix_prompt_errors_for_beyond", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value_limits.reset()
    check("921: 素は 76 で切る", module.fix_value_request("価値として「83」を指定") == "価値として「76」を指定")
    value_limits.provide("923_beyond_76", types.SimpleNamespace(superseded=lambda: False), lambda: 100)
    check("921: 923 があれば 83 のまま", module.fix_value_request("価値として「83」を指定") == "価値として「83」を指定")
    check("921: 100 を超えれば 100", module.fix_value_request("価値として「130」を指定") == "価値として「100」を指定")
    value_limits.reset()


def main():
    test_extend()
    test_settings()
    test_window()
    test_prompt_errors()
    sys.modules.pop("scripts.functions", None)
    print()
    if failures:
        print("{} FAILED: {}".format(len(failures), ", ".join(failures)))
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
