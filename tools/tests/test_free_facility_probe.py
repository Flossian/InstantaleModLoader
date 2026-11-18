# -*- coding: utf-8 -*-
"""209_probe_free_facility の定数のダンプをゲーム抜きで通す。

    python tools/tests/test_free_facility_probe.py

確認するもの:

  生成側待ち … 実行側（`scripts.free_facility`）だけが在る回は印を立てずに待つ。
               生成側（`llm_manager_free_facility`）が来た後の apply() で `_DSL_SPEC` が書かれる
  1回だけ   … 書いた後の apply() では書き直さない
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
                                 if n.endswith("_probe_free_facility")][0])
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("free_facility_probe_under_test",
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

    def on_ready(self, func):
        pass

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


for mark in (MOD.DUMP_MARK, MOD.SURVEY_MARK, MOD.COUNTS_MARK):
    if hasattr(sys, mark):
        delattr(sys, mark)
saved = {name: sys.modules.get(name) for name in (MOD.MODULE, MOD.GEN_MODULE)}
try:
    sys.modules[MOD.MODULE] = types.SimpleNamespace(
        __file__="free_facility.py", PROGRAM_VERSION=1)
    sys.modules.pop(MOD.GEN_MODULE, None)
    ctx = FakeCtx(tempfile.mkdtemp())

    print("生成側が未読込の回は印を立てずに待つ")
    MOD.apply(ctx)
    check("印が立っていない", not getattr(sys, MOD.DUMP_MARK, False))
    check("ダンプを書いていない", "engine dump" not in read_log(ctx), read_log(ctx)[:200])

    print("生成側が来た後の apply() で _DSL_SPEC が書かれる")
    sys.modules[MOD.GEN_MODULE] = types.SimpleNamespace(
        _DSL_SPEC="DSL_SPEC_BODY", _EXAMPLE_PROGRAM={"steps": []})
    MOD.apply(ctx)
    text = read_log(ctx)
    check("印が立つ", getattr(sys, MOD.DUMP_MARK, False))
    check("_DSL_SPEC の全文が出る", "DSL_SPEC_BODY" in text, text[-300:])
    check("生成側の「未読込」を書いていない", "not imported yet" not in text)

    print("書いた後の apply() では書き直さない")
    size = len(text)
    MOD.apply(ctx)
    check("ログが増えない", len(read_log(ctx)) == size)
    check("記録で例外を握っていない", not ctx.errors, ctx.errors)
finally:
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    for mark in (MOD.DUMP_MARK, MOD.SURVEY_MARK, MOD.COUNTS_MARK):
        if hasattr(sys, mark):
            delattr(sys, mark)

print()
if failures:
    print("FAILED ({}):".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all passed")
