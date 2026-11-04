# -*- coding: utf-8 -*-
"""戦闘の欄が畳まれている間の敵の欄の更新（`instantale_modloader.defeat`）。

ゲームの更新は欄の高さで割るので、高さ 0 のまま走ると 0 除算で落ちる（GAME.md §2.10）。
途中で止めると敵の絵の一覧が欠けるので、高さを与えて最後まで走らせ、欄が戻ったら描き直す。
"""
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import defeat, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  {!r}".format(detail)))
    if not cond:
        failures.append(name)


class FakeClock(object):
    """`schedule_interval` を控えるだけ。回すのは検査の側。"""

    def __init__(self):
        self.ticks = []

    def schedule_interval(self, fn, _seconds):
        self.ticks.append(fn)

    def step(self):
        self.ticks = [fn for fn in self.ticks if fn(0.25) is not False]


class FakeCtx(object):
    generation = "test"

    def __init__(self):
        self.hooks = {}

    def superseded(self):
        return False

    def wrap(self, target, **_options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


clock = FakeClock()
kivy_clock = types.ModuleType("kivy.clock")
kivy_clock.Clock = clock
sys.modules.setdefault("kivy", types.ModuleType("kivy"))
sys.modules["kivy.clock"] = kivy_clock

app = types.SimpleNamespace(current_enemy_dict={"敵": object()})
ui.find_app = lambda: app

defeat.reset()
ctx = FakeCtx()
defeat.install(ctx)
display = ctx.hooks[defeat.ENEMY_DISPLAY_TARGET]

panel = types.SimpleNamespace(height=456.3)
hud = types.SimpleNamespace(top_info_layout_battle=panel)
images = {}
calls = []


def game_update(self, _instance, value):
    """ゲームの更新の代わり: 先に絵の一覧を空にし、欄の高さで割ってから組み直す。"""
    images.clear()
    ratio = 192 / self.top_info_layout_battle.height
    images.update({name: ratio for name in value})
    calls.append(dict(value))


print("畳まれていないとき")
display(game_update, hud, hud, {"敵": 1})
check("そのまま走る", len(calls) == 1 and panel.height == 456.3)
check("見張りを置かない", not clock.ticks)

print("畳まれている間")
panel.height = 0
calls.clear()
display(game_update, hud, hud, {"敵": 2})
check("落ちずに最後まで走る", len(calls) == 1 and "敵" in images, (calls, images))
check("欄の高さは 0 に戻す", panel.height == 0, panel.height)
check("見張りが1本置かれる", len(clock.ticks) == 1, clock.ticks)
display(game_update, hud, hud, {"敵": 3})
check("続けて来ても見張りは増えない", len(clock.ticks) == 1, clock.ticks)
clock.step()
check("畳まれている間は描き直さない", len(calls) == 2 and len(clock.ticks) == 1, calls)

print("欄が戻ったとき")
panel.height = 456.3
clock.step()
check("最後の更新で1回だけ描き直す", len(calls) == 3 and calls[-1] == {"敵": 3}, calls)
check("見張りは終わる", not clock.ticks, clock.ticks)
clock.step()
check("描き直しは重ならない", len(calls) == 3, calls)

print("畳まれたまま戦闘が終わったとき")
panel.height = 0
calls.clear()
display(game_update, hud, hud, {"敵": 4})
saved = app.current_enemy_dict
app.current_enemy_dict = {}
clock.step()
check("見張りは描き直さずに終わる", len(calls) == 1 and not clock.ticks, (calls, clock.ticks))
app.current_enemy_dict = saved

print("切り上げの扱い（敵の一覧が空）")
app.current_enemy_dict = {}
raised = False
try:
    display(game_update, hud, hud, {})
except ZeroDivisionError:
    raised = True
check("切り上げていなければ 0 除算は素通し", raised)
panel.height = 0
defeat._state()["surrendered_at"] = time.monotonic()
check("切り上げの直後は握る", display(game_update, hud, hud, {}) is None)
app.current_enemy_dict = saved
defeat.reset()

if failures:
    print("{} failed: {}".format(len(failures), failures))
    sys.exit(1)
print("all ok")
