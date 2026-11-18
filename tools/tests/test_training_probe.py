# -*- coding: utf-8 -*-
"""231_probe_training の窓をゲーム抜きで通す。

    python tools/tests/test_training_probe.py

確認するもの:

  同じ物を外す … 中身の等しい窓が入れ子で開いても、閉じるのはその窓そのもの（`is`）。
                 外側の窓の間の日数送りは外側の行に入り、閉じた後の並びは空
  注入し直し   … 古い世代の `execute` が開いた窓に、新しい世代の `elapse_days` が書き込める。
                 `__init__` の引数も世代をまたいで引ける
  素通し       … 引数は来た形のまま本体へ渡る
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
                                 if n.endswith("_probe_training")][0])
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("training_probe_under_test",
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

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def reset_marks():
    for mark in (getattr(MOD, "STORE_ATTR", None), MOD.OUTSIDE_MARK):
        if mark and hasattr(sys, mark):
            delattr(sys, mark)


app = types.SimpleNamespace(buttons=[], player=None)
MOD.ui = types.SimpleNamespace(find_app=lambda: app, gold_of=lambda a: 100,
                               game_day=lambda a: 1, spec_args=lambda e: None)
EXECUTE = "__main__:TrainingPhaseManager.execute"
INIT = "__main__:TrainingPhaseManager.__init__"
ELAPSE = "__main__:InstantaleApp.elapse_days"


class Manager:
    pass


reset_marks()
try:
    print("中身の等しい窓が入れ子で開いても、閉じるのはその窓そのもの")
    ctx = FakeCtx()
    MOD.apply(ctx)
    execute = ctx.hooks[EXECUTE]
    elapse = ctx.hooks[ELAPSE]
    seen = {}

    def inner_orig(self, *args, **kwargs):
        return None

    def outer_orig(self, *args, **kwargs):
        seen["outer"] = (args, kwargs)
        execute(inner_orig, Manager())
        elapse(lambda self, *a, **k: None, app, 3)

    execute(outer_orig, Manager(), choice_text="go")
    rows = [r for r in ctx.rows if r.get("phase") == "execute"]
    check("外側の窓の間の日数送りは外側の行に入る",
          len(rows) == 2 and rows[-1]["elapse_days_calls"] == [3],
          [r["elapse_days_calls"] for r in rows])
    elapse(lambda self, *a, **k: None, app, 7)
    check("閉じた後に窓が残らない（後の日数送りは窓の外の行になる）",
          any("elapse_days(7) outside any training window" in str(l) for l in ctx.lines),
          [l for l in ctx.lines if "elapse_days" in str(l)])
    check("キーワードはキーワードのまま本体へ渡る", seen["outer"] == ((), {"choice_text": "go"}),
          seen["outer"])

    print("注入し直し: 古い世代の窓に新しい世代の包みが書き込める")
    reset_marks()
    old = FakeCtx()
    MOD.apply(old)
    manager = Manager()
    old.hooks[INIT](lambda self, *a, **k: None, manager, app, "技を磨く", 2, [])
    new = FakeCtx()
    MOD.apply(new)

    def orig_with_new_hooks(self, *args, **kwargs):
        new.hooks[ELAPSE](lambda self, *a, **k: None, app, 5)

    old.hooks[EXECUTE](orig_with_new_hooks, manager)
    rows = [r for r in old.rows if r.get("phase") == "execute"]
    check("新しい世代の elapse_days が開いていた窓に入る",
          rows and rows[-1]["elapse_days_calls"] == [5],
          [r["elapse_days_calls"] for r in rows])
    check("窓の外の行にしない",
          not any("outside any training window" in str(l) for l in new.lines), new.lines)
    new.hooks[EXECUTE](lambda self, *a, **k: None, manager)
    rows = [r for r in new.rows if r.get("phase") == "execute"]
    check("古い世代の __init__ が控えた引数を新しい世代の execute が引ける",
          rows and (rows[-1]["init_args"] or [None])[0] == "'技を磨く'",
          rows and rows[-1]["init_args"])
    check("記録で例外を握っていない", not (ctx.errors or old.errors or new.errors),
          ctx.errors + old.errors + new.errors)
finally:
    reset_marks()

print()
if failures:
    print("FAILED ({}):".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all passed")
