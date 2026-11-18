# -*- coding: utf-8 -*-
"""103_fix_eventlog_trim をゲーム抜きで通す。

    python tools/tests/test_fix_eventlog_trim.py

確認するもの:

  刈り込み … 位置でも名前でも渡された event log を、直近3ターンに刈り込んで同じ渡し方で返す
  取り違え … 名前で `quest_event_log=None` が渡されたら、位置引数の3番目を event log と取り違えない
"""
import importlib.util
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_PATH = os.path.join(RUNTIME_DIR, "mods", "103_fix_eventlog_trim", "fix_eventlog_trim.py")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def wrap(self, target, **_kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator

    def logger(self, _name):
        return lambda line: None

    def log(self, *_a, **_k):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)


def main():
    manager = types.ModuleType("scripts.llm.llm_manager")
    for name in ("quest_referee_event_evaluate_new", "quest_referee_event_resolve",
                 "quest_referee_event_rewrite"):
        setattr(manager, name, lambda *a, **k: None)
    sys.modules.setdefault("scripts", types.ModuleType("scripts"))
    sys.modules.setdefault("scripts.llm", types.ModuleType("scripts.llm"))
    sys.modules["scripts.llm.llm_manager"] = manager

    spec = importlib.util.spec_from_file_location("fix_eventlog_trim_mod", MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = FakeCtx(tempfile.mkdtemp())
    module.apply(ctx)
    hook = ctx.hooks.get("scripts.llm.llm_manager:quest_referee_event_resolve")
    check("対象を包む", hook is not None, sorted(ctx.hooks))

    sep = module.SEPARATOR
    long_log = "前置き" + "".join("{}ターン{}".format(sep, i) for i in range(6))
    seen = {}

    def orig(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return "ok"

    hook(orig, "a", "b", long_log)
    check("位置で渡された event log を刈り込む", seen["args"][2].count(sep) == module.KEEP_TURNS,
          seen["args"][2])
    hook(orig, "a", "b", quest_event_log=long_log)
    check("名前で渡された event log を刈り込む",
          seen["kwargs"]["quest_event_log"].count(sep) == module.KEEP_TURNS, seen["kwargs"])
    other = "別の引数" + "".join("{}x{}".format(sep, i) for i in range(6))
    hook(orig, "a", "b", other, quest_event_log=None)
    check("名前で None が渡されたら、位置の3番目を event log と取り違えない",
          seen["args"][2] == other and seen["kwargs"].get("quest_event_log") is None, seen)
    check("例外を記録していない", not ctx.errors, ctx.errors)

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
