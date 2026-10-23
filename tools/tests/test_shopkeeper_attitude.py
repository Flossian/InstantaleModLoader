# -*- coding: utf-8 -*-
"""341_shopkeeper_attitude をゲーム抜きで通す。

    python tools/tests/test_shopkeeper_attitude.py

  決まり   … 応じ方の読み取り、値段の向き（0 は得・2 は損）、控えの使い回し（場面・好感度・手配度・日数）、
             判定が無いときの決め方（平時は普段どおり、手配中は好感度の線）、売買の1行
  頼み     … 店に着くと手が空いてから裏で1回頼む。手配中は窓口 `wanted` の率で手配の頼み、無ければ平時の頼み。
             店でない施設・MOD の人物・平時を切った回では頼まない。頼みに店主の性格・関係・感情・人生ログが載る
  売買     … 判定の向きで値段が動き、初めての回だけ LLM の1文。平時の普段どおりは何も出さない。
             場面・好感度・手配度が変わるか日数が過ぎると頼み直す。頼んでいない店主はその場で頼む（待機表示は重ねない）
  窓口     … 店の値の圧を引き受けたと名乗る
"""
import importlib
import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import arrivals, llm, prices, ui, wanted  # noqa: E402
from instantale_modloader import state as loader_state  # noqa: E402


def load_parts():
    package = types.ModuleType("shopkeeper_attitude_parts")
    package.__path__ = [os.path.join(MODS_DIR, "341_shopkeeper_attitude")]
    sys.modules[package.__name__] = package
    return (importlib.import_module(package.__name__ + ".shopkeeper_attitude"),
            importlib.import_module(package.__name__ + ".attitude"))


mod, attitude = load_parts()
failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({!r})".format(detail) if not cond else ""))
    if not cond:
        failures.append(name)


W, U = attitude.WANTED, attitude.USUAL

# ------------------------------------------------------------------ 決まり
print("決まり")
check("整数の応じ方を読む", [attitude.clamp_stance(v) for v in (0, 1, 2)] == [0, 1, 2])
check("文字列と整数の小数も読む", attitude.clamp_stance(" 2 ") == 2 and attitude.clamp_stance(1.0) == 1)
check("範囲外・真偽値・文字は None",
      [attitude.clamp_stance(v) for v in (3, -1, True, "負ける", None)] == [None] * 5)
check("答えを読む", attitude.parse({"reason": " 常連 ", "stance": 0, "line": "x"}) == (0, "常連", "x"))
check("応じ方の無い答えは None", attitude.parse({"reason": "r"}) is None and attitude.parse("t") is None)
price = lambda key, stance, sell=True: attitude.price("買価", "売価", key, 1000, 0.1, stance, sell)  # noqa: E731
check("0（得）: 買値が下がり売値が上がる", (price("買価", 0), price("売価", 0)) == (900, 1100))
check("2（損）: 買値が上がり売値が下がる", (price("買価", 2), price("売価", 2)) == (1100, 900))
check("1・売値を切った回は触らない", price("買価", 1) is None and price("売価", 2, sell=False) is None)
entry = {"mode": U, "stance": 0, "lawfulness": None, "affinity": 10, "day": 100}
check("平時は手配度が変わっても使える", attitude.fresh(entry, U, -5, 10, 129, 30))
check("日数を過ぎたら使わない", not attitude.fresh(entry, U, 10, 10, 130, 30))
check("場面か好感度が変われば使わない",
      not attitude.fresh(entry, W, None, 10, 101, 30) and not attitude.fresh(entry, U, 10, 20, 101, 30))
wanted_entry = dict(entry, mode=W, lawfulness=-20)
check("手配中は手配度が変われば使わない",
      attitude.fresh(wanted_entry, W, -20, 10, 101, 30) and not attitude.fresh(wanted_entry, W, -30, 10, 101, 30))
check("判定が無いとき: 平時は普段どおり",
      [attitude.fallback(U, a, 40) for a in (100, 0, None)] == [1, 1, 1])
check("判定が無いとき: 手配中は好感度の線",
      [attitude.fallback(W, a, 40) for a in (40, 39, None)] == [1, 0, 0])
check("1行: LLM の1文に値段の動き",
      attitude.notice(U, 0, 10, True, "ガルド", "ガルドは笑った。") == "ガルドは笑った。（買値 -10%・売値 +10%）")
check("1行: 決まった文", attitude.notice(U, 2, 10, False, "ガルド") == "ガルドは値をふっかけてきた。（買値 +10%）"
      and attitude.notice(W, 1, 10, True, "ガルド") == "ガルドは手配書の顔に気づいたが、いつもどおりの値で応じてくれた。")
check("1行: 平時の普段どおりは空", attitude.notice(U, 1, 10, True, "ガルド") == "")
usual_prompt = attitude.prompt(U, "町A", "ヨルグ", "流れ者", 10, 0, "ガルド", "雑貨屋", "強欲", "べらんめえ",
                               ["初対面"], "警戒心がある", 0, 2, "")
check("平時の頼みに手配の行は無い", "手配度" not in usual_prompt and "根拠が無ければ 1" in usual_prompt)
wanted_prompt = attitude.prompt(W, "町A", "ヨルグ", "流れ者", -30, 30, "ガルド", "雑貨屋", "強欲", "べらんめえ",
                                ["初対面"], ["警戒心がある"], 0, 2, "")
check("手配中の頼みに手配度と重さ", "手配度 -30" in wanted_prompt and "手配の重さ 30" in wanted_prompt
      and "根拠が無ければ 0" in wanted_prompt)


# ------------------------------------------------------------------ 組み立て
class FakeCtx(object):
    def __init__(self):
        self.hooks = {}
        self.mod_dir = os.path.join(MODS_DIR, "341_shopkeeper_attitude")

    def wrap(self, target, **options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate

    def logger(self, name):
        return lines.append

    def log(self, *args, **kwargs):
        pass

    def log_exc(self, msg):
        raise AssertionError(msg)

    def superseded(self):
        return False


class Person(object):
    def __init__(self, name, affinity=None):
        self.name = name
        self.profile = "港町の雑貨屋の主。"
        self.personality = "強欲で抜け目がない"
        self.speech_style = "べらんめえ口調"
        self.relationship = {"player": {"affinity": affinity, "affinity_text": ["警戒心がある"],
                                        "relationship": ["初対面"], "conversation_count": 3}}


class Facility(object):
    def __init__(self, owner, shop=True):
        self.owner = owner
        self.choices = {"売買する": None} if shop else {"会話する": None}


class Screen(object):
    def __init__(self, *args, **kwargs):
        self.said = []
        self.busy = []

    def schedule(self, fn):
        fn()

    def say(self, app, text):
        self.said.append(text)

    def busy_on(self, app):
        self.busy.append("on")

    def busy_off(self, app, restore=True):
        self.busy.append("off")

    def when_idle(self, app, then, **kwargs):
        then()


class Worlds(object):
    def __init__(self, *args, **kwargs):
        self.lock = threading.RLock()
        self.data = {}

    def rebind(self, ctx, write=None):
        return self

    def playthrough(self, app):
        return "W/P"

    def load(self, playthrough):
        return self.data.setdefault(playthrough, {})

    def save(self, playthrough):
        pass


class App(object):
    pass


app = App()
keeper = Person("ガルド", 10)
app.player = types.SimpleNamespace(name="ヨルグ", profile="流れ者。", location=None)
app.world = types.SimpleNamespace(characters={"7": keeper, "mod:9": Person("影", 0)})
here = {"facility": Facility("7"), "lawfulness": 10, "day": 100, "pressure": None}
lines = []
screens = []
asked = []
answers = []
layers = []


def fake_ask(ctx_, manager, message, **kwargs):
    asked.append((manager, message[0]["content"], kwargs.get("structure")))
    return answers.pop(0) if answers else None


def make_screen(*args, **kwargs):
    screen = Screen()
    screens.append(screen)
    return screen


saved = (prices.install, prices.adjust, ui.find_app, ui.game_day, ui.current_area, ui.find_facility,
         ui.area_id_of, ui.area_record, ui.lawfulness_of, ui.Screen, llm.ask, llm.create_structure,
         loader_state.WorldStore)
prices.install = lambda *a, **k: None
prices.adjust = lambda owner, fn, **k: layers.append(fn)
ui.find_app = lambda: app
ui.game_day = lambda a: here["day"]
ui.current_area = lambda a: types.SimpleNamespace(name="町A")
ui.area_id_of = lambda area: "1"
ui.area_record = lambda player, area_id: {"lawfulness": here["lawfulness"]}
ui.lawfulness_of = lambda record: record["lawfulness"]
ui.Screen = make_screen
llm.ask = fake_ask
llm.create_structure = lambda ctx_, name, fields, **k: (name, tuple(fields))
loader_state.WorldStore = Worlds
sys.modules[mod.__name__]._store()["worlds"] = None
wanted.reset()
try:
    app.player.location = here["facility"]
    ctx = FakeCtx()
    mod.apply(ctx)
    screen = screens[-1]
    worlds = sys.modules[mod.__name__]._store()["worlds"]
    layer = layers[0]
    shop = ctx.hooks[mod.SHOP_TARGET]
    # 着いた後はローダの窓口 `arrivals` に預ける（包むのはローダの1枚）
    arrive = ctx.hooks[arrivals.MOVE_TARGET]
    check("店の値の圧を引き受けたと名乗る", wanted.shop_pressure_taker() == "341_shopkeeper_attitude")
    wanted.declare_shop_pressure("336_crime_overhaul", FakeCtx(), lambda a: here["pressure"])

    def wait_judging():
        for thread in threading.enumerate():
            if thread.name == "shopkeeper_attitude":
                thread.join(5)

    def come(facility=None):
        if facility is not None:
            here["facility"] = facility
        app.player.location = here["facility"]
        result = arrive(lambda self: "moved", None)
        wait_judging()
        return result

    def press_shop():
        del lines[:]
        del screen.said[:]
        del screen.busy[:]
        return shop(lambda self: "shop", types.SimpleNamespace(app=app))

    def prices_now():
        return layer(None, "買価", 1000), layer(None, "売価", 1000)

    print("平時")
    answers.append({"reason": "強欲でよそ者をカモと見た", "stance": 2, "line": "ガルドはにやりと笑った。"})
    check("移動を素通しする", come() == "moved")
    check("店に着くと1回頼む", len(asked) == 1 and asked[0][0] == mod.MANAGER_NAME, asked)
    text = asked[0][1]
    check("頼みに店主の性格・口調・関係・感情・好感度・会話の回数",
          all(word in text for word in ("強欲で抜け目がない", "べらんめえ口調", "初対面", "警戒心がある",
                                        "好感度 10", "会話した回数: 3")), text)
    check("平時の頼み", "どんな値で応じるか" in text and "手配度" not in text, text)
    check("型は理由・応じ方・1文", asked[0][2] == ("ModShopkeeperAttitude", ("reason", "stance", "line")))
    come()
    check("控えが使える間は着いても頼まない", len(asked) == 1)
    check("売買を素通しする", press_shop() == "shop")
    check("ふっかける値段（平時 10%）", prices_now() == (1100, 900), prices_now())
    check("初めての回は LLM の1文", screen.said == ["ガルドはにやりと笑った。（買値 +10%・売値 -10%）"], screen.said)
    check("ログに場面と出どころ", any("shop [usual]" in l and "overcharge [stored]" in l for l in lines), lines)
    check("控えがあれば待機表示を出さない", screen.busy == [])
    press_shop()
    check("2回目からは決まった文", screen.said == ["ガルドは値をふっかけてきた。（買値 +10%・売値 -10%）"], screen.said)

    keeper.relationship["player"]["affinity"] = 30
    answers.append({"reason": "特に根拠なし", "stance": 1, "line": "ガルドは淡々と応じた。"})
    come()
    check("好感度が変わると着いた時に頼み直す", len(asked) == 2)
    press_shop()
    check("普段どおりは値段も1行も出さない", prices_now() == (None, None) and screen.said == [],
          (prices_now(), screen.said))

    print("手配中")
    here["pressure"] = 0.15
    here["lawfulness"] = -30
    answers.append({"reason": "強欲", "stance": 2, "line": "ガルドは手配書を指で弾いた。"})
    come()
    check("率が置かれていれば手配の頼み", len(asked) == 3 and "手配度 -30" in asked[-1][1], asked[-1][1])
    press_shop()
    check("足元を見る値段（336 の率 15%）", prices_now() == (1150, 850), prices_now())
    check("手配中の1文", screen.said == ["ガルドは手配書を指で弾いた。（買値 +15%・売値 -15%）"], screen.said)
    here["lawfulness"] = -40
    here["pressure"] = 0.2
    answers.append({"reason": "罪が重く怖い", "stance": 0, "line": ""})
    press_shop()
    check("手配度が変わると売買の時にその場で頼む（待機表示は重ねない。ゲームの execute の中）", len(asked) == 4 and screen.busy == [],
          (len(asked), screen.busy))
    check("怯える値段（20%）と決まった文", prices_now() == (800, 1200)
          and screen.said == ["手配書の顔に気づいた店主は、怯えたように値を改めた。（買値 -20%・売値 +20%）"],
          (prices_now(), screen.said))

    print("日数")
    here["day"] = 131
    answers.append({"reason": "恩がある", "stance": 1, "line": "ガルドは黙って頷いた。"})
    come()
    check("日数を過ぎたら頼み直し、古い控えは消える",
          len(asked) == 5 and worlds.data["W/P"]["7"]["day"] == 131, len(asked))
    press_shop()
    check("手配中の動じないは1文を出す", screen.said == ["ガルドは黙って頷いた。"], screen.said)

    print("頼まない")
    here["pressure"] = None
    here["lawfulness"] = 10
    come(Facility("7", shop=False))
    check("店でない施設では頼まない", len(asked) == 5)
    come(Facility("mod:9"))
    check("MOD の人物の店では頼まない", len(asked) == 5)
    press_shop()
    check("MOD の人物の店は平時なら普段どおり", prices_now() == (None, None) and screen.said == [])
    mod.USUAL_ENABLED = False
    come(Facility("7"))
    check("平時を切ると平時は頼まない", len(asked) == 5)
    press_shop()
    check("平時を切ると値段も1行も無い", prices_now() == (None, None) and lines == [], lines)
    mod.USUAL_ENABLED = True

    print("頼めない")
    here["pressure"] = 0.1
    here["lawfulness"] = -20
    keeper.relationship["player"]["affinity"] = 19
    press_shop()
    check("LLM が答えなければ手配中は好感度の線（既定 20。19 は怯える）", mod.FRIEND_AFFINITY == 20 and prices_now() == (900, 1100)
          and any("cowed [fallback]" in l for l in lines), lines)
    check("答えが読めなかったことをログに残す", any("no usable answer" in l for l in lines), lines)

    print("日数が読めない")
    worlds.data["W/P"]["99"] = {"mode": "usual", "stance": 1, "lawfulness": None, "affinity": 0, "day": 131,
                                "line": "", "told": True}
    here["day"] = None
    answers.append({"reason": "読み込みの途中", "stance": 1, "line": ""})
    before_asks = len(asked)
    come(Facility("7"))
    check("日数が読めない間に答えが返っても、ほかの店主の控えを消さない",
          len(asked) == before_asks + 1 and "99" in worlds.data["W/P"] and "7" in worlds.data["W/P"],
          (len(asked) - before_asks, sorted(worlds.data["W/P"])))
finally:
    (prices.install, prices.adjust, ui.find_app, ui.game_day, ui.current_area, ui.find_facility,
     ui.area_id_of, ui.area_record, ui.lawfulness_of, ui.Screen, llm.ask, llm.create_structure,
     loader_state.WorldStore) = saved
    wanted.reset()

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
