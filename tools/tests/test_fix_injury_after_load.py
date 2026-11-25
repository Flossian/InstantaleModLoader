# -*- coding: utf-8 -*-
"""読み込みの後に、怪我を掛けた能力値へ組み直す（`149_fix_injury_after_load`）。

ゲームは読み込みで `ability_scores` を元の能力値から作り直し、怪我の倍率を掛けない（GAME.md §2.19.1）。
読み込みの後に `update_ability_scores` を1回呼ぶ。体力の上限には触らない。
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import gates, loads  # noqa: E402

MOD_FILE = os.path.join(RUNTIME_DIR, "mods", "149_fix_injury_after_load", "fix_injury_after_load.py")
PARTS = ("head", "body", "arms", "legs", "organs", "sanity")
BASE = {"strength": 30, "dexterity": 30, "constitution": 30, "intelligence": 16, "wisdom": 22, "charisma": 30}

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  {!r}".format(detail)))
    if not cond:
        failures.append(name)


class FakePlayer(object):
    """怪我の倍率を `update_ability_scores` で掛ける主人公。倍率は胴体と腕の分だけ持つ。"""

    RATES = {("body", "hurt"): (("constitution", 0.95),),
             ("arms", "damaged"): (("strength", 0.95), ("dexterity", 0.85))}

    def __init__(self, **stages):
        self.body_parts = {part: {"injury": 0, "stage": "intact", "state": "", "description": ""}
                           for part in PARTS}
        for part, (injury, stage) in stages.items():
            self.body_parts[part].update({"injury": injury, "stage": stage})
        self.original_ability_scores = dict(BASE)
        self.ability_scores = dict(BASE)        # 読み込みの直後は元の値のまま
        self.max_physical_integrity = 47.0
        self.physical_integrity = 47.0
        self.updates = 0

    def update_ability_scores(self):
        self.updates += 1
        scores = dict(self.original_ability_scores)
        for part, info in self.body_parts.items():
            for ability, rate in self.RATES.get((part, info["stage"]), ()):
                scores[ability] = round(scores[ability] * rate)
        self.ability_scores = scores

    def update_max_physical_integrity(self):
        raise AssertionError("体力の上限は組み直さない")


class FakeApp(object):
    def __init__(self, player):
        self.player = player
        self.loaded = 0

    def load_game_new(self, *args, **kwargs):
        self.loaded += 1
        self.player.ability_scores = dict(self.player.original_ability_scores)
        return "loaded"


class FakeCtx(object):
    def __init__(self):
        self.hooks = {}
        self.errors = []
        self.lines = []         # 関所が立ったことを知らせるローダの行も入る
        self.mod_dir = os.path.dirname(MOD_FILE)
        self.mod_name = os.path.basename(self.mod_dir)

    def logger(self, _name, **_options):
        return self.lines.append

    def own(self):
        """この MOD が書いた行（ローダの関所の行を除く）。"""
        return [line for line in self.lines if not line.startswith("loads.")]

    def log(self, _text, level="INFO"):
        pass

    def log_exc(self, text):
        self.errors.append(text)

    def superseded(self):
        return False

    def wrap(self, target, **_options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


def setup(player):
    """MOD を当て、関所を通る `load_game_new` を持つ app を返す。"""
    spec = importlib.util.spec_from_file_location("fix_injury_after_load", MOD_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gates.reset_all()
    ctx = FakeCtx()
    mod.apply(ctx)
    app_cls = type("InstantaleApp", (FakeApp,), {})
    hook = ctx.hooks[loads.TARGETS[loads.LOAD]]
    original = app_cls.load_game_new
    app_cls.load_game_new = lambda self, *args, **kwargs: hook(original, self, *args, **kwargs)
    return mod, ctx, app_cls(player)


print("[怪我あり] 読み込みの後に能力値を組み直す")
player = FakePlayer(body=(12, "hurt"), arms=(35, "damaged"))
mod, ctx, app = setup(player)
result = app.load_game_new("世界")
check("ゲームの読み込みはそのまま走り、戻り値も変えない", app.loaded == 1 and result == "loaded", (app.loaded, result))
check("怪我を掛けた能力値になる",
      player.ability_scores == dict(BASE, strength=28, dexterity=26, constitution=28), player.ability_scores)
check("組み直しは1回だけ", player.updates == 1, player.updates)
check("体力の上限には触らない", player.max_physical_integrity == 47.0 and player.physical_integrity == 47.0)
check("元の能力値は変えない", player.original_ability_scores == BASE)
check("変わった能力値をログに残す",
      len(ctx.own()) == 1 and "body hurt, arms damaged" in ctx.own()[0]
      and "strength 30 -> 28" in ctx.own()[0] and "dexterity 30 -> 26" in ctx.own()[0], ctx.lines)
app.load_game_new("世界")
check("読み込み直しても同じ値（重ねて下がらない）",
      player.ability_scores == dict(BASE, strength=28, dexterity=26, constitution=28) and player.updates == 2,
      (player.ability_scores, player.updates))
check("例外を出していない", not ctx.errors, ctx.errors)

print("\n[怪我なし] 何もしない")
fine = FakePlayer()
mod, ctx, app = setup(fine)
app.load_game_new("世界")
check("組み直しを呼ばない", fine.updates == 0 and fine.ability_scores == BASE and not ctx.own(),
      (fine.updates, ctx.lines))
empty = FakePlayer()
empty.body_parts = {}
mod, ctx, app = setup(empty)
app.load_game_new("世界")
check("部位を持たない人物でも落ちない", empty.updates == 0 and not ctx.errors, ctx.errors)

print("\n[形] 読めないものは怪我に数えない")
check("段階が intact・空・文字列でない部位は入れない",
      mod.injured({"a": {"stage": "intact"}, "b": {"stage": ""}, "c": {"stage": 3}, "d": "壊れ",
                   "e": {"stage": "hurt"}}) == [("e", "hurt")]
      and mod.injured(None) == [] and mod.injured("<missing>") == [])
check("変わった能力値だけを拾う",
      mod.changes({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == [("b", 2, 3)] and mod.changes(None, {}) == [])

print("\n[壊れ] 組み直しを呼べない・投げるときもゲームの読み込みは通す")
broken = FakePlayer(body=(12, "hurt"))
broken.update_ability_scores = None
mod, ctx, app = setup(broken)
check("呼べないときは WARN を残して読み込みを通す",
      app.load_game_new("世界") == "loaded" and any("WARN" in line for line in ctx.lines) and not ctx.errors,
      (ctx.lines, ctx.errors))


def boom():
    raise RuntimeError("壊れた")


raising = FakePlayer(body=(12, "hurt"))
raising.update_ability_scores = boom
mod, ctx, app = setup(raising)
check("投げたときは握り潰して記録し、読み込みを通す",
      app.load_game_new("世界") == "loaded" and len(ctx.errors) == 1, ctx.errors)

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
