# -*- coding: utf-8 -*-
"""911_quest_from_world をゲーム抜きで通す。

    python tools/tests/test_wip_quest_from_world.py

見ているのは、この MOD が自分で決めている所だけ:
当たった回は街の3欄が差し替わり、外れた回は6引数が素通しになる。
`world_overview` / 街の名前 / 難易度はどちらでも変わらない。
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

folder = [n for n in os.listdir(MODS_DIR) if n.endswith("_quest_from_world")][0]
folder = os.path.join(MODS_DIR, folder)
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("quest_from_world_under_test",
                                              os.path.join(folder, manifest["entry"]))
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)

# mod.json と定数の既定値が揃っている
for key, spec_ in manifest["settings"].items():
    assert getattr(MOD, key) == spec_["default"], key

# roll の両端
assert not MOD.roll(0, rnd=lambda: 0.0)
assert MOD.roll(100, rnd=lambda: 0.999)
assert MOD.roll(30, rnd=lambda: 0.29)
assert not MOD.roll(30, rnd=lambda: 0.30)


class Ctx(object):
    def __init__(self):
        self.hooks = {}
        self.lines = []

    def logger(self, name):
        return self.lines.append

    def log(self, line):
        self.lines.append(line)

    def wrap(self, target, **kw):
        def deco(fn):
            self.hooks[target] = fn
            return fn
        return deco


ARGS = ("world", "town", "overview", "structure", "area", 7)


def orig(*a, **kw):
    return a, kw


ctx = Ctx()
MOD.apply(ctx)
hook = ctx.hooks["scripts.llm.llm_manager_world_generate:random_quest_generator"]

MOD.CHANCE_PERCENT = 0
a, kw = hook(orig, *ARGS, extra=1)
assert a == ARGS and kw == {"extra": 1}, a

MOD.CHANCE_PERCENT = 100
a, kw = hook(orig, *ARGS)
n = MOD.NOTE_WORLD_ONLY
assert a == ("world", "town", n, n, n, 7), a
assert any(l.startswith("world-only") for l in ctx.lines)
print("ok")
