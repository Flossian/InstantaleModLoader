# -*- coding: utf-8 -*-
"""140_fix_busy_dots_speed をゲーム抜きで通す。

    python tools/tests/test_fix_busy_dots_speed.py

確認するもの:

  止める   … 待機中に、ほかの点送りが Clock に載っていれば `orig` を呼ばない
  続ける   … ほかに載っていなければ（Clock は呼ぶ前に自分を外す）`orig` を呼ぶ
  明けた後 … `is_button_enabled` が真なら、載っていても `orig` を呼ぶ
  数えない … 繰り返しの予約・別の関数・別の app の予約
  壊れた値 … Clock が読めなくても `orig` を呼ぶ
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "fix_busy_dots_speed"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Event:
    def __init__(self, callback, loop=False):
        self.callback = callback
        self.loop = loop

    def get_callback(self):
        return self.callback


class Clock:
    def __init__(self):
        self.events = []
        self.broken = False

    def get_events(self):
        if self.broken:
            raise RuntimeError("cannot read")
        return list(self.events)


class App:
    def __init__(self, enabled=False):
        self.is_button_enabled = enabled

    def display_button_load(self, dt=0):
        return "painted"


class FakeCtx:
    _mod = "140_fix_busy_dots_speed"

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

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

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


clock = Clock()
saved_modules = {name: sys.modules.get(name) for name in ("kivy", "kivy.clock")}
kivy = types.ModuleType("kivy")
kivy_clock = types.ModuleType("kivy.clock")
kivy_clock.Clock = clock
kivy.clock = kivy_clock
sys.modules["kivy"] = kivy
sys.modules["kivy.clock"] = kivy_clock

path = os.path.join(MODS_DIR, "140_fix_busy_dots_speed", "fix_busy_dots_speed.py")
spec = importlib.util.spec_from_file_location("fix_busy_dots_speed", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ctx = FakeCtx(OUT_DIR)
mod.apply(ctx)
# 偽の kivy は apply の間だけ置く（同じプロセスで走るほかの検査に残さない）。
for name, module in saved_modules.items():
    if module is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = module
hook = ctx.hooks.get("__main__:InstantaleApp.display_button_load")
check("display_button_load を包む", hook is not None, list(ctx.hooks))
calls = []


def orig(self, *args, **kwargs):
    calls.append(args)
    return "painted"


def run(app, events):
    clock.events = events
    del calls[:]
    return hook(orig, app, 0.3)


print("止める・続ける")
app = App(enabled=False)
result = run(app, [Event(app.display_button_load)])
check("ほかに1本載っていれば止める", result is None and not calls, (result, calls))
result = run(app, [])
check("ほかに載っていなければ続ける", result == "painted" and calls == [(0.3,)], (result, calls))
result = run(app, [Event(app.display_button_load), Event(app.display_button_load)])
check("2本載っていても止める", result is None and not calls, (result, calls))

print("明けた後")
done = App(enabled=True)
result = run(done, [Event(done.display_button_load)])
check("is_button_enabled が真なら呼ぶ", result == "painted" and calls, (result, calls))

print("数えないもの")
result = run(app, [Event(app.display_button_load, loop=True)])
check("繰り返しの予約は数えない", result == "painted", result)
result = run(app, [Event(lambda dt: None), Event(app.display_button_load.__func__.__get__(App()))])
check("別の関数・別の app の予約は数えない", result == "painted", result)

print("壊れた値")
clock.broken = True
result = run(app, [Event(app.display_button_load)])
clock.broken = False
check("Clock が読めなければ呼ぶ", result == "painted" and not ctx.errors, (result, ctx.errors))

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
