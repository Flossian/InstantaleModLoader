# -*- coding: utf-8 -*-
"""225_probe_area_quest_difficulty をゲーム抜きで通す。

    python tools/tests/test_area_quest_difficulty_probe.py

偽物の `AreaMoveManager` を組んで `execute` を通し、窓の間に経路の関数
（`write_area_data_to_world_dict`）と `get_quest_difficulties` を呼ぶ。
見るのは、経路の前後で probe 自身が呼ぶ `get_quest_difficulties` が「関数」の行に
混ざらないこと・ゲームが呼んだ分は録れること・`__init__` / `execute` の引数が
来た形のまま本体へ渡ること・ゲームの Manager に属性を足さないこと。
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
                                 if n.endswith("_probe_area_quest_difficulty")][0])
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("area_quest_difficulty_probe_under_test",
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

    def jsonl(self, name, **kw):
        return ml.ModContext.jsonl(self, name, **kw)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


class Area:
    def __init__(self, area_id):
        self.id = area_id
        self.name = "A{}".format(area_id)
        self.config = {"level_of_detail": 0}
        self.quests = []
        self.nodes = {}


class Manager:
    pass


ctx = FakeCtx(tempfile.mkdtemp())
MOD.apply(ctx)
pure_hook = ctx.hooks["scripts.functions:get_quest_difficulties"]
build_hook = ctx.hooks["save_area_json:write_area_data_to_world_dict"]
init_hook = ctx.hooks["__main__:AreaMoveManager.__init__"]
execute_hook = ctx.hooks["__main__:AreaMoveManager.execute"]

game_calls = []


def orig_difficulties(area, world):
    game_calls.append(area.id)
    return [10, 20, 30]


def wrapped_difficulties(area, world):
    # ローダが差し込んだ後の `scripts.functions.get_quest_difficulties`（包みが表に出ている）
    return pure_hook(orig_difficulties, area, world)


area = Area("3")
app = types.SimpleNamespace(world_dict={"areas": {"3": {"size": 1, "config": {}}}},
                            world=types.SimpleNamespace(areas={"3": area}),
                            player=None, area=area)
sys.modules["scripts.functions"] = types.SimpleNamespace(
    get_quest_difficulties=wrapped_difficulties)
MOD.ui.find_app = lambda: app
MOD.ui.world_areas = lambda a: {"3": area}

seen = {}


def orig_build(world_dict, area_id):
    area.config["level_of_detail"] = 1
    # ゲームが自分で呼んだ分。これは録る
    wrapped_difficulties(area, app.world)


def orig_init(self, *args, **kwargs):
    seen["init"] = (args, kwargs)
    self.app = app


def orig_execute(self, *args, **kwargs):
    seen["execute"] = (args, kwargs)
    build_hook(orig_build, app.world_dict, "3")


print("__init__ / execute: 引数を来た形のまま渡す")
manager = Manager()
init_hook(orig_init, manager, app=app, target_area_id="3", mode="carriage")
check("__init__ のキーワードがキーワードのまま届く",
      seen["init"] == ((), {"app": app, "target_area_id": "3", "mode": "carriage"}),
      seen["init"])
check("ゲームの Manager に属性を足さない",
      not any(name.startswith("_probe") for name in vars(manager)), vars(manager))
execute_hook(orig_execute, manager, choice_text="go")
check("execute のキーワードがキーワードのまま届く",
      seen["execute"] == ((), {"choice_text": "go"}), seen["execute"])
execute_hook(orig_execute, manager)
check("引数なしの execute に None を足さない",
      seen["execute"] == ((), {}), seen["execute"])
check("記録で例外を握っていない", not ctx.errors, ctx.errors)

print("窓の間: probe 自身の get_quest_difficulties を録らない")
with io.open(os.path.join(ctx.out_dir, MOD.RECORD_BASENAME), encoding="utf-8") as fh:
    rows = [json.loads(line) for line in fh if line.strip()]
pure_rows = [r for r in rows if r.get("phase") == "関数"]
moves = [r for r in rows if r.get("phase") == "移動"]
check("ゲームが呼んだ分だけが「関数」の行になる（移動2回で2本）",
      len(pure_rows) == 2, len(pure_rows))
check("ゲームは2回呼んだ", game_calls.count("3") >= 2, game_calls)
check("移動の行が録れて、target は __init__ のキーワードから読める",
      len(moves) == 2 and all(r.get("target") == "3" for r in moves),
      [r.get("target") for r in moves])
check("経路の前後の状態に難易度が写る",
      any(r.get("phase") == "経路" and r["after"]["difficulties"] == [10, 20, 30]
          for r in rows))

if failures:
    print("\n{} failure(s)".format(len(failures)))
    sys.exit(1)
print("\nall ok")
