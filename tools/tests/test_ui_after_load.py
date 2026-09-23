# -*- coding: utf-8 -*-
"""`ui.refresh_choices_after_load`（ロードのあと、名簿が復元されてから選択肢を 1 度組み直す）。

名簿が空の間は待ち、同行者が入ったら 1 度だけ `refresh_choice_buttons()` を呼ぶこと、
そのとき画面（HUD の `update_button_texts`）にも塗ること、
2 本の MOD が呼んでも 1 度であること、同行者の居ないセーブでは上限で 1 度呼ぶことを見る。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))


class FakeClock(object):
    intervals = []

    @staticmethod
    def schedule_interval(fn, interval):
        FakeClock.intervals.append(fn)

    @staticmethod
    def tick():
        pending, FakeClock.intervals = FakeClock.intervals, []
        for fn in pending:
            if fn(0) is not False:
                FakeClock.intervals.append(fn)


kivy = types.ModuleType("kivy")
clock = types.ModuleType("kivy.clock")
clock.Clock = FakeClock
kivy.clock = clock
sys.modules.setdefault("kivy", kivy)
sys.modules["kivy.clock"] = clock

from instantale_modloader import ui  # noqa: E402


class InstanTaleHUD(object):
    painted = []

    def update_button_texts(self, instance, value):
        InstanTaleHUD.painted.append(list(value))


hud_module = types.ModuleType(ui.HUD_MODULE)
hud_module.InstanTaleHUD = InstanTaleHUD
sys.modules[ui.HUD_MODULE] = hud_module


class Ctx(object):
    def __init__(self):
        self.hooks = {}

    def wrap(self, target, **kw):
        def deco(fn):
            self.hooks[target] = fn
            return fn
        return deco

    def log_exc(self, line):
        raise AssertionError(line)


if hasattr(sys, ui._AFTER_LOAD_ATTR):
    delattr(sys, ui._AFTER_LOAD_ATTR)
logged = []
app = types.SimpleNamespace(party=["player"], refreshed=0, root=InstanTaleHUD(),
                            to_display_buttons=["old", "stale"])


def refresh():
    app.refreshed += 1
    app.to_display_buttons = ["fresh"]


app.refresh_choice_buttons = refresh

ctx_a, ctx_b = Ctx(), Ctx()
ui.refresh_choices_after_load(ctx_a, logged.append, tries=4)
ui.refresh_choices_after_load(ctx_b, logged.append, tries=4)
load_a = ctx_a.hooks["__main__:InstantaleApp.load_game_new"]
load_b = ctx_b.hooks["__main__:InstantaleApp.load_game_new"]

# 2 本の層が同じロードを見張る。名簿が空の間は組み直さない
assert load_a(lambda self: "loaded", app) == "loaded"
assert load_b(lambda self: "loaded", app) == "loaded"
FakeClock.tick()
assert app.refreshed == 0
app.party.append("78")                                   # 名簿が復元された
FakeClock.tick()
assert app.refreshed == 1, app.refreshed                 # 2 層あっても 1 度
FakeClock.tick()
assert app.refreshed == 1 and not FakeClock.intervals
assert any("refreshed the choices after the load" in l for l in logged)
assert InstanTaleHUD.painted == [["fresh"]], InstanTaleHUD.painted   # 画面にも塗った
assert any("via hud.update_button_texts" in l for l in logged), logged

# 同行者の居ないセーブ: 上限で 1 度
app.party[:] = ["player"]
load_a(lambda self: None, app)
for _ in range(4):
    FakeClock.tick()
assert app.refreshed == 2 and not FakeClock.intervals
print("ok")
