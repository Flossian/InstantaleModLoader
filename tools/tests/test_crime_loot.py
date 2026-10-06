# -*- coding: utf-8 -*-
"""336_crime_overhaul の盗みの稼ぎ（loot.py）の、行動の開け閉めをゲーム抜きで通す。

    python tools/tests/test_crime_loot.py

  閉じ忘れ … 要約まで届かずに開いたままの行動を、店に入るとき・ロードのときに閉じる
             （行動の始めの所持金を次の行動へ持ち越さない）
  閉じた後 … 閉じている行動には何もしない（行を書かない）
"""
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import llm, ui  # noqa: E402


def load_loot():
    matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_overhaul"))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *_crime_overhaul: {}".format(matches))
    package = types.ModuleType("crime_overhaul_loot_parts")
    package.__path__ = [os.path.join(MODS_DIR, matches[0])]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + ".loot")


loot = load_loot()
failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({!r})".format(detail) if not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx(object):
    def __init__(self):
        self.hooks = {}

    def wrap(self, target, **options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate

    def log_exc(self, msg):
        raise AssertionError(msg)


class Cfg(object):
    LOOT_ENABLED = True


class App(object):
    gold = 1000
    save_data_dict = {"world_data": {"world_name": "W"}, "player_data": {"name": "P"}}
    world_dict = {"world_data": {"world_name": "W"}}


app = App()
lines = []
ctx = FakeCtx()
env = types.SimpleNamespace(ctx=ctx, write=lines.append, screen=None, cfg=Cfg(),
                            area_difficulty=lambda a: 1, quest_reward=lambda d: 100,
                            refresh_gold=lambda a: None)
saved = (ui.find_app, ui.gold_of, llm.watch_aliases)
ui.find_app = lambda: app
ui.gold_of = lambda a: a.gold
llm.watch_aliases = lambda *a, **k: []
try:
    loot.install(env)
    facilitator = next(fn for target, fn in ctx.hooks.items()
                       if target.endswith(loot.FACILITATOR_TARGETS[0]))
    shop = ctx.hooks["__main__:ShoppingStartManagerRemake.execute"]
    load = ctx.hooks["__main__:InstantaleApp.load_game_new"]

    print("閉じ忘れ")
    facilitator(lambda *a, **k: {"process": []})      # 行動が始まり、要約まで届かない（戦闘から逃げた など）
    shop(lambda *a, **k: "shop", None)
    check("店に入るとき、開いたままの行動を閉じる",
          any("closed the action left open (a shop opened; gold at its start 1000)" in line
              for line in lines), lines)
    del lines[:]
    shop(lambda *a, **k: "shop", None)
    check("閉じている行動には何もしない", lines == [], lines)

    facilitator(lambda *a, **k: {"process": []})
    load(lambda *a, **k: "loaded", None)
    check("ロードのとき、開いたままの行動を閉じる",
          any("a save was loaded" in line for line in lines), lines)
    check("包みは元の処理を素通しする", shop(lambda *a, **k: "shop", None) == "shop"
          and load(lambda *a, **k: "loaded", None) == "loaded")
finally:
    ui.find_app, ui.gold_of, llm.watch_aliases = saved

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
