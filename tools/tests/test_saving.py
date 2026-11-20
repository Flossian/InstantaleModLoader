# -*- coding: utf-8 -*-
"""ローダの窓口 `saving`（ゲームの保存の前後）をゲーム抜きで通す。

    python tools/tests/test_saving.py

  戻す  … 保存の間だけ素の値へ戻し、済んだら掛け直す（投げた回も）
  書く  … 保存が通ったら控えを書き、投げた回は書かない。錠はどちらでも放す
  順    … before は登録の新しい順、after は古い順。app は self
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import saving  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("ok  " if ok else "FAIL", name, "  " + str(detail) if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Ctx(object):
    def __init__(self):
        self.generation = 1
        self.hooks = {}

    def superseded(self):
        return False

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator


class App(object):
    value = "伏せた値"


saving.reset()
ctx = Ctx()
seen_at_save = []
written = []
lock = threading.RLock()


def quiet_before(call):
    seen_at_save.append(("before", call.app.value))
    kept = call.app.value
    call.app.value = "素の値"
    return kept


def quiet_after(call):
    call.app.value = call.memo


def lock_before(call):
    lock.acquire()
    return True


def lock_after(call):
    if not call.memo:
        return
    try:
        if call.error is None:
            written.append("控え")
    finally:
        lock.release()


saving.on("quiet", ctx, before=quiet_before, after=quiet_after, always=True)
saving.on("records", ctx, before=lock_before, after=lock_after, always=True)
hook = ctx.hooks[saving.TARGET]
app = App()


def save(self):
    seen_at_save.append(("save", self.value, lock._is_owned()))
    return "saved"


check("orig の戻り値を返す", hook(save, app) == "saved")
check("保存の間は素の値で、錠を持っている", ("save", "素の値", True) in seen_at_save, seen_at_save)
check("済んだら掛け直す", app.value == "伏せた値")
check("保存が通ったら控えを書く", written == ["控え"])
check("錠を放す", not lock._is_owned())

print("[投げた回]")
written.clear()


def broken(self):
    raise OSError("disk full")


try:
    hook(broken, app)
except OSError:
    check("保存の例外はそのまま流す", True)
else:
    check("保存の例外はそのまま流す", False)
check("投げた回も掛け直す", app.value == "伏せた値")
check("投げた回は控えを書かない", written == [])
check("投げた回も錠を放す", not lock._is_owned())
saving.reset()

print("[外側に立てる]")
import instantale_modloader as ml  # noqa: E402
from instantale_modloader import gates, quest_ends, quest_generation, text_filters  # noqa: E402


class LayeredCtx(Ctx):
    """後から当てた包みが外側になる（本物の wrap と同じ）。"""

    def __init__(self):
        Ctx.__init__(self)
        self.generation = 2

    def wrap(self, target, **kw):
        def decorator(fn):
            previous = self.hooks.get(target)
            if previous is None:
                self.hooks[target] = fn
            else:
                self.hooks[target] = lambda orig, this, *a, _p=previous, _f=fn, **k: _f(
                    lambda obj, *a2, **k2: _p(orig, obj, *a2, **k2), this, *a, **k)
            return fn
        return decorator


gates.reset_all()
layered = LayeredCtx()
order = []
gates.defer_outermost(True)
try:
    saving.on("330", layered, before=lambda call: order.append("330 が伏せる"))
    check("MOD の適用の間は窓口を包まない", saving.TARGET not in layered.hooks)

    # 適用の間に MOD が立てるローダの保存の関所（最初の注入の modnpc）
    def modnpc_gate(orig, self, *args, **kwargs):
        order.append("modnpc が写す")
        return orig(self, *args, **kwargs)
    layered.wrap(saving.TARGET)(modnpc_gate)
finally:
    gates.defer_outermost(False)
built = ml._install_outer_windows(layered)
check("適用の後にローダが4つとも立てる", built == ["quest_generation", "quest_ends", "text_filters", "saving"], built)
check("→ 4つの対象を包む", all(t in layered.hooks for t in (
    saving.TARGET, quest_ends.TARGET, quest_generation.TARGET, text_filters.TARGET)))
layered.hooks[saving.TARGET](lambda self: order.append("保存"), App())
check("窓口はローダの保存の関所より外側（伏せてから写す）",
      order == ["330 が伏せる", "modnpc が写す", "保存"], order)
check("同じ世代では2枚目を立てない", ml._install_outer_windows(layered) and len(order) == 3)
gates.reset_all()

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
