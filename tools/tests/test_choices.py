# -*- coding: utf-8 -*-
"""ローダの窓口 `choices`（選択肢のボタン。TECH.md §3.3.14）をゲーム抜きで通す。

    python tools/tests/test_choices.py

  登録   … 持ち主ごとに1つ（差し替え）。組み直しの前は後から登録した MOD が先、後は先に登録した MOD が先
           （包みを重ねていたときと同じ順）。用済みの ctx の登録は呼ばない
  押下   … 印の頭で振り分け、ゲームの押下へは流さない。True を返したらゲーム自身の保存を呼ぶ。印が無ければ素通し。
           intercept はどのボタンでも先に見て、True なら握る（後から登録した MOD が先）
  印     … 保存の直前に印の付いたボタンを控え、ロード（世界の入れ替わり）の後の最初の組み直しでだけ付け直す。
           登録していない MOD の印（`mod_…`）も戻る。同じ文言でも spec が違えば、保存したときと違う画面なら付けない
  ロード … 人物が 0 人のまま組み直しが走ったら見張り、揃い切ったら（名簿を少し待って）1度だけ組み直して塗る。
           続けて世界が入れ替わったら待ち直す。手が空いていなければ組み直さない
  包み   … 1つの世代につき1度だけ包む。世代が変われば包み直す
"""
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))


class FakeClock(object):
    intervals = []
    onces = []

    @staticmethod
    def schedule_interval(fn, interval):
        FakeClock.intervals.append(fn)

    @staticmethod
    def schedule_once(fn, delay=0):
        FakeClock.onces.append(fn)

    @staticmethod
    def tick():
        onces, FakeClock.onces = FakeClock.onces, []
        for fn in onces:
            fn(0)
        pending, FakeClock.intervals = FakeClock.intervals, []
        for fn in pending:
            if fn(0) is not False:
                FakeClock.intervals.append(fn)


kivy = types.ModuleType("kivy")
clock = types.ModuleType("kivy.clock")
clock.Clock = FakeClock
kivy.clock = clock
sys.modules["kivy"] = kivy
sys.modules["kivy.clock"] = clock

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import choices, ui  # noqa: E402


class InstanTaleHUD(object):
    painted = []

    def update_button_texts(self, instance, value):
        InstanTaleHUD.painted.append(list(value))


hud_module = types.ModuleType(ui.HUD_MODULE)
hud_module.InstanTaleHUD = InstanTaleHUD
sys.modules[ui.HUD_MODULE] = hud_module

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


STATE = tempfile.mkdtemp(prefix="choices_test_")


class Ctx(object):
    def __init__(self, mod, generation):
        self._mod = mod
        self.generation = generation
        self.hooks = {}
        self.dead = False
        self.state_dir = STATE
        self.logs = []

    def wrap(self, target, **kw):
        def deco(fn):
            self.hooks[target] = fn
            return fn
        return deco

    def superseded(self):
        return self.dead

    def log(self, line, **kw):
        self.logs.append(line)

    def log_exc(self, line):
        raise AssertionError(line)

    def state_path(self, *parts):
        path = os.path.join(STATE, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        return ml.read_json(path, default)

    def write_json(self, path, data, indent=1):
        return ml.write_json(path, data, indent=indent)


class Spec(object):
    def __init__(self, cls_name, args=()):
        self.cls_name, self.args = cls_name, list(args)


sys.modules["__main__"].PhaseSpec = Spec      # `ui.Screen.button` が spec を組むのに使う


class World(object):
    def __init__(self, characters):
        self.characters = characters
        self.world_name = "試しの世界"


def make_app():
    app = types.SimpleNamespace()
    app.world = World({"1": object()})
    app.player = types.SimpleNamespace(name="主人公")
    app.save_data_dict = {"player_data": {"name": "主人公"}}
    app.party = ["player"]
    app.root = InstanTaleHUD()
    app.saves = 0
    app.refreshed = 0
    app.pressed = []
    app.to_display_buttons = []

    def save_game():
        app.saves += 1
    app.save_game = save_game

    def refresh_choice_buttons(reset_page=False):
        hook = ctx_a.hooks.get(choices.REFRESH_TARGET)
        app.refreshed += 1

        def orig(self, *a, **k):
            app.to_display_buttons = [b.get("text") for b in app.buttons]
        hook(orig, app)
    app.refresh_choice_buttons = refresh_choice_buttons
    return app


choices.reset()
calls = []
ctx_a = Ctx("700_a", "g1")
ctx_b = Ctx("701_b", "g1")
screen_a = ui.Screen(ctx_a, calls.append, tag="a", mark="mod_a_action")
screen_b = ui.Screen(ctx_b, calls.append, tag="b", mark="mod_b_action")


def refresh_a(app, buttons):
    calls.append("a")
    buttons[:] = [e for e in buttons if screen_a.mark_of(e) is None]
    buttons.append(screen_a.button("Aのボタン", mark="a:go"))


def refresh_b(app, buttons):
    calls.append("b")


def after_a(app):
    calls.append("after-a")


def after_b(app):
    calls.append("after-b")


def press_a(app, action):
    app.pressed.append(action)
    return action == "a:pay"


print("[登録]")
choices.provide(ctx_a, screen_a, refresh=refresh_a, after=after_a, presses={"a:": press_a})
choices.provide(ctx_b, screen_b, refresh=refresh_b, after=after_b)
check("包みは1枚（2本目の登録では包まない）",
      set(ctx_a.hooks) == {choices.REFRESH_TARGET, choices.PRESS_TARGET, choices.SAVE_TARGET}
      and not ctx_b.hooks, (sorted(ctx_a.hooks), sorted(ctx_b.hooks)))
app = make_app()
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")}]
choices.on_refresh(ctx_a, app)             # 起動して最初の組み直し（世界が入れ替わった扱い）
calls.clear()
FakeClock.intervals.clear()
hook = ctx_a.hooks[choices.REFRESH_TARGET]
hook(lambda self, *a, **k: calls.append("orig"), app)
check("組み直しの前は後から登録した MOD が先、後は先に登録した MOD が先（包みと同じ順）",
      calls == ["b", "a", "orig", "after-a", "after-b"], calls)
check("自分の印のボタンを差し直しても増えない",
      [e["text"] for e in app.buttons] == ["出る", "Aのボタン"], [e["text"] for e in app.buttons])
choices.provide(ctx_b, screen_b, refresh=lambda app, buttons: calls.append("b2"))
calls.clear()
choices.on_refresh(ctx_a, app)
check("同じ持ち主の登録は差し替え（順は最初の登録のまま）", calls == ["b2", "a"], calls)
ctx_b.dead = True
calls.clear()
choices.on_refresh(ctx_a, app)
check("用済みの ctx の登録は呼ばない", calls == ["a"], calls)
ctx_b.dead = False

print("[押下]")
press = ctx_a.hooks[choices.PRESS_TARGET]
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")},
               screen_a.button("払う", mark="a:pay"), screen_a.button("見る", mark="a:look")]
flowed = []
result = press(lambda self, i: flowed.append(i), app, 2)
check("印の頭で振り分け、ゲームの押下へは流さない", app.pressed == ["a:look"] and not flowed and result is None,
      (app.pressed, flowed))
saves = app.saves
press(lambda self, i: flowed.append(i), app, 1)
FakeClock.tick()
check("True を返したらゲーム自身の保存を呼ぶ", app.saves == saves + 1, app.saves)
press(lambda self, i: flowed.append(i), app, 0)
check("印の無いボタンはゲームの押下へ素通し", flowed == [0], flowed)
seen = []


def intercept_b(app, entry, index):
    seen.append(("b", entry.get("text")))
    return entry.get("text") == "出る" and getattr(app, "refuse", False)


def intercept_a(app, entry, index):
    seen.append(("a", entry.get("text")))
    return False


choices.provide(ctx_a, screen_a, refresh=refresh_a, after=after_a, presses={"a:": press_a}, intercept=intercept_a)
choices.provide(ctx_b, screen_b, refresh=refresh_b, after=after_b, intercept=intercept_b)
flowed.clear()
press(lambda self, i: flowed.append(i), app, 0)
check("intercept はどのボタンでも先に見る（後から登録した MOD が先）。握らなければ素通し",
      seen == [("b", "出る"), ("a", "出る")] and flowed == [0], (seen, flowed))
app.refuse = True
flowed.clear()
press(lambda self, i: flowed.append(i), app, 0)
check("intercept が True なら握る（ゲームへ流さない）", flowed == [], flowed)
app.refuse = False

print("[印の持ち越し]")
save = ctx_a.hooks[choices.SAVE_TARGET]
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")},
               screen_a.button("Aのボタン", mark="a:go"),
               {"text": "他のMOD", "spec": Spec("JustSetButtonToNormalPhase"), "mod_other_action": "x:1"}]
save(lambda self: None, app)
rows = choices._worlds(ctx_a).load(choices._worlds(ctx_a).playthrough(app)).get("marks")
check("保存の直前に印の付いたボタンを控える", [r["text"] for r in rows or []] == ["Aのボタン", "他のMOD"], rows)
# ロード: 世界が入れ替わり、ボタンは印の落ちた形で戻る（文言と spec だけ）
app.world = World({"1": object()})
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")},
               {"text": "Aのボタン", "spec": Spec("JustSetButtonToNormalPhase")},
               {"text": "他のMOD", "spec": Spec("JustSetButtonToNormalPhase")}]
calls.clear()
choices.on_refresh(ctx_a, app)
texts = [(e["text"], screen_a.mark_of(e), e.get("mod_other_action")) for e in app.buttons]
check("ロードの後の最初の組み直しで印を付け直す（残骸が残らず、二重にもならない）",
      texts == [("出る", None, None), ("他のMOD", None, "x:1"), ("Aのボタン", "a:go", None)], texts)
check("登録していない MOD の印も戻る", any(t[2] == "x:1" for t in texts), texts)
app.buttons.append({"text": "他のMOD", "spec": Spec("JustSetButtonToNormalPhase")})
choices.on_refresh(ctx_a, app)
check("付け直すのはロードの後の最初の組み直しだけ（後から並んだ同じボタンには付けない）",
      [e.get("mod_other_action") for e in app.buttons].count("x:1") == 1
      and app.buttons[-1].get("mod_other_action") is None,
      [(e["text"], e.get("mod_other_action")) for e in app.buttons])
# 同じ文言でも spec が違えば付けない
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")},
               {"text": "他のMOD", "spec": Spec("JustSetButtonToNormalPhase"), "mod_other_action": "x:1"}]
save(lambda self: None, app)
app.world = World({"1": object()})
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")},
               {"text": "他のMOD", "spec": Spec("MovePhaseManager")}]
choices.on_refresh(ctx_a, app)
check("同じ文言でも spec が違えば付けない", app.buttons[1].get("mod_other_action") is None, app.buttons)
# 保存したときと違う画面なら付けない（同じ世界で同じ名前の主人公を作り直したときなど）
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")},
               {"text": "他のMOD", "spec": Spec("JustSetButtonToNormalPhase"), "mod_other_action": "x:1"}]
save(lambda self: None, app)
app.world = World({"1": object()})
app.buttons = [{"text": "冒険者達と話す", "spec": Spec("DisplayTalkChoice")},
               {"text": "他のMOD", "spec": Spec("JustSetButtonToNormalPhase")}]
choices.on_refresh(ctx_a, app)
check("保存したときと違う画面なら付けない", app.buttons[1].get("mod_other_action") is None, app.buttons)
FakeClock.intervals.clear()
choices._store()["after"]["token"] = None

print("[ロードの後]")
InstanTaleHUD.painted[:] = []
app.world = World({})                      # ロードの途中: 人物が 0 人
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")}]
before = app.refreshed
choices.on_refresh(ctx_a, app)
check("人物が 0 人なら見張りを仕掛ける", len(FakeClock.intervals) == 1, FakeClock.intervals)
choices.on_refresh(ctx_a, app)
check("二重には仕掛けない", len(FakeClock.intervals) == 1, FakeClock.intervals)
for _ in range(5):
    FakeClock.tick()
check("人物が揃うまでは組み直さない", app.refreshed == before, app.refreshed)
app.world.characters["1"] = object()       # 人物が揃った（名簿は空のまま＝同行者なし）
for _ in range(choices.PARTY_TICKS + 2):
    FakeClock.tick()
check("揃ったら名簿を少し待って1度だけ組み直す", app.refreshed == before + 1 and not FakeClock.intervals,
      (app.refreshed, FakeClock.intervals))
check("組み直した一覧を画面に塗る", InstanTaleHUD.painted and InstanTaleHUD.painted[-1] == ["出る", "Aのボタン"],
      InstanTaleHUD.painted)
app.world = World({})
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")}]
choices.on_refresh(ctx_a, app)
app.world.characters["1"] = object()
app.party.append("78")                     # 同行者が戻った
app.is_button_enabled = False              # その間にプレイヤーが押して待機に入った
before = app.refreshed
for _ in range(3):
    FakeClock.tick()
check("手が空いていなければ組み直さない（待機の終わりにゲームが組み直す）",
      app.refreshed == before and not FakeClock.intervals, (app.refreshed, FakeClock.intervals))

app.is_button_enabled = True
app.party[:] = ["player"]
app.world = World({})
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")}]
choices.on_refresh(ctx_a, app)                 # 1つ目の読み込みの途中
app.world.characters["1"] = object()
FakeClock.tick()
app.world = World({"1": object(), "2": object()})   # 続けて別の世界を読み込んだ
before = app.refreshed
FakeClock.tick()
check("続けて世界が入れ替わったら、組み直さずに待ち直す", app.refreshed == before and FakeClock.intervals,
      (app.refreshed, FakeClock.intervals))
for _ in range(choices.PARTY_TICKS + 4):
    FakeClock.tick()
check("待ち直した後に1度だけ組み直す", app.refreshed == before + 1 and not FakeClock.intervals,
      (app.refreshed, FakeClock.intervals))

print("[包み]")
ctx_c = Ctx("700_a", "g2")
choices.provide(ctx_c, screen_a, refresh=refresh_a, presses={"a:": press_a})
check("世代が変われば包み直す", choices.REFRESH_TARGET in ctx_c.hooks, sorted(ctx_c.hooks))
check("差し替えた後の持ち主の数は2つのまま", len(choices.providers()) == 2, choices.providers())
# 注入し直しで適用順が変わった（b を a より先に読む）。並びは新しい世代の登録の順になる。
choices.provide(Ctx("701_b", "g3"), screen_b, refresh=refresh_b, after=after_b)
choices.provide(Ctx("700_a", "g3"), screen_a, refresh=refresh_a, presses={"a:": press_a})
check("注入し直すと並びは新しい世代の適用順", choices.providers() == ["701_b", "700_a"], choices.providers())

choices.reset()
shutil.rmtree(STATE, ignore_errors=True)
print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
