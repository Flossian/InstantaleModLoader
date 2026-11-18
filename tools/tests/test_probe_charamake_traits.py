# -*- coding: utf-8 -*-
"""246_probe_charamake_traits の記録をゲーム抜きで通す。

    python tools/tests/test_probe_charamake_traits.py

確認するもの:

  前後の対 … `StoryApp` の入口は before の行を書いた回に必ず after の行も書く。
             `character_vm` がまだ無い回（before が None）でも after が抜けない
  件数     … before の行だけが枠を使う
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402

folder = os.path.join(MODS_DIR, [n for n in os.listdir(MODS_DIR)
                                 if n.endswith("_probe_charamake_traits")][0])
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("charamake_traits_probe_under_test",
                                              os.path.join(folder, manifest["entry"]))
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx:
    _mod = None

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        return os.path.join(self.out_dir, *parts)

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def read_log(ctx):
    try:
        with io.open(ctx.out_path(MOD.LOG_BASENAME), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


class StoryApp(object):
    """作成画面の器。`character_vm` は呼ばれた中で作られる。"""

    def load_character_preset(self, preset):
        self.character_vm = types.SimpleNamespace(traits=["剛力"])
        return "loaded"


if hasattr(sys, MOD.SEEN_ATTR):
    delattr(sys, MOD.SEEN_ATTR)
try:
    ctx = FakeCtx(tempfile.mkdtemp())
    MOD.apply(ctx)
    hook = ctx.hooks[MOD.STORY_APP + ".load_character_preset"]

    print("character_vm がまだ無い回でも after の行を書く")
    app = StoryApp()
    result = hook(StoryApp.load_character_preset, app, "preset1")
    text = read_log(ctx)
    check("本体の戻りをそのまま返す", result == "loaded", result)
    check("before の行がある", "load_character_preset args=" in text and "before=None" in text, text)
    check("after の行がある", "load_character_preset after=" in text, text)
    check("after に作られた特質が出る", "剛力" in text, text)
    check("枠は1つだけ使う", getattr(sys, MOD.SEEN_ATTR)["lines"] == 1,
          getattr(sys, MOD.SEEN_ATTR))
    check("記録で例外を握っていない", not ctx.errors, ctx.errors)
finally:
    if hasattr(sys, MOD.SEEN_ATTR):
        delattr(sys, MOD.SEEN_ATTR)

print()
if failures:
    print("FAILED ({}):".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all passed")
