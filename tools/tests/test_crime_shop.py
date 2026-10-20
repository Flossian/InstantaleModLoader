# -*- coding: utf-8 -*-
"""336_crime_overhaul の「店が委縮して値を下げる」（law.py）を、ゲーム抜きで通す。

    python tools/tests/test_crime_shop.py

  委縮する   … 手配中の土地で、店の主の好感度が設定に届かなければ、買値が下がり売値が上がる。店に入ると1行
  委縮しない … 好感度が設定以上なら値段はそのまま。店に入ると、気づいたうえで応じる1行
  好感度     … 読めない好感度（関係が無い・真偽値）と主のいない施設は委縮する側
  窓口       … 率をローダの窓口 `wanted` に置く（切れば置かない）。引き受けた MOD が居れば、値段も1行も出さない
"""
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import prices, ui, wanted  # noqa: E402


def load_parts():
    matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_overhaul"))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *_crime_overhaul: {}".format(matches))
    package = types.ModuleType("crime_overhaul_shop_parts")
    package.__path__ = [os.path.join(MODS_DIR, matches[0])]
    sys.modules[package.__name__] = package
    return (importlib.import_module(package.__name__ + ".law"),
            importlib.import_module(package.__name__ + ".rules"))


law, rules = load_parts()
failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({!r})".format(detail) if not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx(object):
    def __init__(self):
        self.hooks = {}

    def wrap(self, target, **options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate

    def log_exc(self, msg):
        raise AssertionError(msg)

    def superseded(self):
        return False


class Cfg(object):
    INTIMIDATION_ENABLED = True
    INTIMIDATION_PER_POINT = 0.5
    INTIMIDATION_CAP = 30
    INTIMIDATION_SELL = True
    INTIMIDATION_NOTICE = True
    INTIMIDATION_FRIEND_AFFINITY = 40
    STATUTE_ENABLED = False


class Person(object):
    def __init__(self, name, affinity=None):
        self.name = name
        self.relationship = {"player": {"affinity": affinity}} if affinity is not None else {}


class Facility(object):
    def __init__(self, owner):
        self.owner = owner


class Screen(object):
    def __init__(self):
        self.said = []

    def schedule(self, fn):
        fn()

    def say(self, app, text):
        self.said.append(text)


class App(object):
    pass


app = App()
keeper = Person("ガルド")
app.world = types.SimpleNamespace(characters={"7": keeper})
here = {"facility": Facility("7"), "lawfulness": -20}
lines = []
screen = Screen()
ctx = FakeCtx()
layers = []
env = types.SimpleNamespace(ctx=ctx, write=lines.append, screen=screen, worlds=None, cfg=Cfg(),
                            owner="336_crime_overhaul",
                            here_lawfulness=lambda a: here["lawfulness"],
                            area_label=lambda a, i: "町A", price_layers=[],
                            current_facility=lambda a: here["facility"])
saved = (prices.install, prices.adjust, ui.find_app)
prices.install = lambda *a, **k: None
prices.adjust = lambda owner, fn, **k: layers.append(fn)
ui.find_app = lambda: app
wanted.reset()
try:
    law.install(env)
    layer = layers[0]
    shop = ctx.hooks[law.SHOP_TARGET]

    def enter():
        del lines[:]
        del screen.said[:]
        return shop(lambda self: "shop", types.SimpleNamespace(app=app))

    print("委縮する")
    keeper.relationship = {"player": {"affinity": 39}}
    check("好感度が足りなければ買値が下がる（手配度 -20 で 10%）", layer(None, rules.BUY, 1000) == 900,
          layer(None, rules.BUY, 1000))
    check("売値が上がる", layer(None, rules.SELL, 1000) == 1100, layer(None, rules.SELL, 1000))
    check("店に入ると元の処理を素通しする", enter() == "shop")
    check("怯える1行", screen.said == [law.SHOP_NOTICE_TEXT.format(
        pct=10, sell=law.SHOP_NOTICE_SELL.format(pct=10))], screen.said)
    check("ログは率", any(line.startswith("shop: lawfulness -20 -> buy -10%") for line in lines), lines)

    print("委縮しない")
    keeper.relationship = {"player": {"affinity": 40}}
    check("好感度が設定ちょうどなら値段はそのまま", layer(None, rules.BUY, 1000) is None
          and layer(None, rules.SELL, 1000) is None)
    enter()
    check("気づいたうえで応じる1行（店主の名前）",
          screen.said == [law.SHOP_FRIEND_TEXT.format(name="ガルド")], screen.said)
    check("ログに好感度", any("ガルド has affinity 40 -> no fear" in line for line in lines), lines)
    Cfg.INTIMIDATION_NOTICE = False
    enter()
    check("知らせを切ると1行も出ない", screen.said == [], screen.said)
    Cfg.INTIMIDATION_NOTICE = True

    print("手配の無い土地")
    here["lawfulness"] = 10
    enter()
    check("親しい店主でも何も出ない", screen.said == [] and lines == [], (screen.said, lines))
    here["lawfulness"] = -20

    print("好感度が読めない")
    keeper.relationship = {}
    check("関係が無ければ委縮する", layer(None, rules.BUY, 1000) == 900)
    keeper.relationship = {"player": {"affinity": True}}
    check("真偽値は好感度に数えない", layer(None, rules.BUY, 1000) == 900)
    here["facility"] = Facility(None)
    keeper.relationship = {"player": {"affinity": 100}}
    check("主のいない施設は委縮する", layer(None, rules.BUY, 1000) == 900)
    here["facility"] = Facility("99")
    check("引けない主は委縮する", layer(None, rules.BUY, 1000) == 900)
    here["facility"] = Facility("7")

    print("窓口")
    keeper.relationship = {"player": {"affinity": 0}}
    check("率を窓口に置く（手配度 -20 で 0.1）", wanted.shop_pressure(app) == (0.1, "336_crime_overhaul"),
          wanted.shop_pressure(app))
    here["lawfulness"] = -60
    check("率は上限で止まる（30%）", wanted.shop_pressure(app)[0] == 0.3, wanted.shop_pressure(app))
    here["lawfulness"] = -20
    Cfg.INTIMIDATION_ENABLED = False
    check("切ると率を置かない", wanted.shop_pressure(app) == (0.0, None))
    Cfg.INTIMIDATION_ENABLED = True
    wanted.take_shop_pressure("341_shopkeeper_attitude", FakeCtx())
    check("引き受けた MOD が居れば値段を動かさない", layer(None, rules.BUY, 1000) is None)
    enter()
    check("引き受けた MOD が居れば1行も出さない", screen.said == [], screen.said)
    check("ログに渡した先", any("rate 10% handed to 341_shopkeeper_attitude" in line for line in lines), lines)
finally:
    prices.install, prices.adjust, ui.find_app = saved
    wanted.reset()

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
