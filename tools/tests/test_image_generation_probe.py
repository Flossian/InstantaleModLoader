# -*- coding: utf-8 -*-
"""230_probe_image_generation の import の直後の当て直しをゲーム抜きで通す。

    python tools/tests/test_image_generation_probe.py

確認するもの:

  絞り込み … 当てる先を持たないモジュール（diffusers の部分モジュールなど）の import では
             `sys.modules` をなめない（manager の目印を1件も引かない）
  当て直し … manager と sdcpp の `_logger` の import では今まで通り当てる
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

folder = os.path.join(MODS_DIR, [n for n in os.listdir(MODS_DIR)
                                 if n.endswith("_probe_image_generation")][0])
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("image_generation_probe_under_test",
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
    def __init__(self):
        self.hooks, self.rows, self.lines, self.errors = {}, [], [], []

    def logger(self, name, **kw):
        return self.lines.append

    def jsonl(self, name, **kw):
        return self.rows.append

    def read_json(self, path, default=None):
        return default

    def superseded(self):
        return False

    def refresh_status(self):
        pass

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


class CountingModule(types.ModuleType):
    """manager の目印を引かれた回数を数える（`sys.modules` をなめたかの目印）。"""
    touched = 0

    def __getattr__(self, name):
        if name in MOD.MANAGER_MARKS:
            CountingModule.touched += 1
        raise AttributeError(name)


added = ["image_generation.counting_probe", "image_generation.fake_manager",
         "sdcpp_fake._logger"]
saved_meta = list(sys.meta_path)
had_inventory = getattr(sys, MOD.INVENTORY_MARK, None)
try:
    setattr(sys, MOD.INVENTORY_MARK, True)
    sys.modules["image_generation.counting_probe"] = CountingModule(
        "image_generation.counting_probe")
    ctx = FakeCtx()
    MOD.apply(ctx)
    observer = [f for f in sys.meta_path if getattr(f, MOD.OBSERVER_MARK, None)][0]
    after = observer._after

    print("当てる先を持たないモジュールの import では sys.modules をなめない")
    CountingModule.touched = 0
    after("diffusers.models.unet_fake", types.ModuleType("diffusers.models.unet_fake"))
    check("manager の目印を引かない", CountingModule.touched == 0, CountingModule.touched)
    check("import_done の行は書く",
          any(r.get("event") == "import_done" and r.get("module") == "diffusers.models.unet_fake"
              for r in ctx.rows))

    print("manager と _logger の import では当てる")
    manager = types.ModuleType("image_generation.fake_manager")
    manager.load_sd_pipeline = lambda *a, **k: None
    manager.generate_image_anime = lambda *a, **k: None
    sys.modules[manager.__name__] = manager
    after(manager.__name__, manager)
    check("manager の関数に当たる",
          "image_generation.fake_manager:load_sd_pipeline" in ctx.hooks
          and "image_generation.fake_manager:generate_image_anime" in ctx.hooks, sorted(ctx.hooks))
    logger = types.ModuleType("sdcpp_fake._logger")
    logger.log_event = lambda *a, **k: None
    sys.modules[logger.__name__] = logger
    after(logger.__name__, logger)
    check("_logger の log_event に当たる", "sdcpp_fake._logger:log_event" in ctx.hooks,
          sorted(ctx.hooks))
    check("例外を握っていない", not ctx.errors, ctx.errors)
finally:
    sys.meta_path[:] = saved_meta
    for name in added:
        sys.modules.pop(name, None)
    if had_inventory is None:
        if hasattr(sys, MOD.INVENTORY_MARK):
            delattr(sys, MOD.INVENTORY_MARK)

print()
if failures:
    print("FAILED ({}):".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all passed")
