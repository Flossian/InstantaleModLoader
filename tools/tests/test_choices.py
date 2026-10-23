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
import threading
import time
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


def trim_a(app, buttons):
    calls.append("trim-a")
    buttons[:] = [entry for entry in buttons if entry["text"] == "出る"]


choices.provide(ctx_a, screen_a, refresh=refresh_a, after=after_a, presses={"a:": press_a}, trim=trim_a)
calls.clear()
choices.on_refresh(ctx_a, app)
check("trim は全部の MOD の refresh の後（後から登録した MOD が足したボタンまで絞れる）",
      calls == ["b2", "a", "trim-a"] and [e["text"] for e in app.buttons] == ["出る"],
      (calls, [e["text"] for e in app.buttons]))
choices.provide(ctx_a, screen_a, refresh=refresh_a, after=after_a, presses={"a:": press_a})

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

# 会話中のセーブを読んだ途中は、人物が1件だけ居て会話の相手がまだ居ないことがある（VERIFICATION.md §3.102）。
app.world = World({"1": object()})
app.in_conversation = "95"
app.buttons = [{"text": "会話を終了する", "spec": Spec("ConversationEndManager", ["95"])}]
check("会話の相手が人物に居なければ揃っていない", choices.ready(app) is False, app.world.characters)
choices.on_refresh(ctx_a, app)
check("会話の相手が居ないうちは見張りを仕掛ける", len(FakeClock.intervals) == 1, FakeClock.intervals)
app.world.characters["95"] = object()
app.party[:] = ["player", "95"]
before = app.refreshed
for _ in range(4):
    FakeClock.tick()
check("相手が揃ったら1度だけ組み直す", app.refreshed == before + 1 and not FakeClock.intervals,
      (app.refreshed, FakeClock.intervals))
check("会話の相手が居れば揃っている", choices.ready(app) is True, app.world.characters)
app.in_conversation = False
app.party[:] = ["player"]

# 人物は揃っているが名簿がまだ戻っていない、ロードの後の最初の組み直し（名簿を見て組むボタンが出なかった。実機）
FakeClock.intervals[:] = []
choices._store()["after"]["token"] = None
app.world = World({"1": object(), "2": object()})
app.party[:] = ["player"]
app.buttons = [{"text": "出る", "spec": Spec("MovePhaseManager")}]
choices.on_refresh(ctx_a, app)
check("ロードの後の最初の組み直しで名簿が空なら、人物が揃っていても見張りを仕掛ける", len(FakeClock.intervals) == 1,
      FakeClock.intervals)
app.party.append("95")
before = app.refreshed
for _ in range(4):
    FakeClock.tick()
check("名簿が戻ったら1度だけ組み直す", app.refreshed == before + 1 and not FakeClock.intervals,
      (app.refreshed, FakeClock.intervals))
choices.on_refresh(ctx_a, app)
check("世界が替わっていない組み直しでは仕掛けない", not FakeClock.intervals, FakeClock.intervals)
app.party[:] = ["player"]

print("[当て直しの終わり]")
real_find_app, real_find_hud = ui.find_app, ui.find_hud
playing_hud = types.SimpleNamespace(parent=object())
try:
    ui.find_app, ui.find_hud = (lambda: app), (lambda _app: playing_hud)
    choices.provide(ctx_a, None, offer=lambda app, view: None, owner="709_settle")
    FakeClock.onces[:] = []
    before = app.refreshed
    app.is_button_enabled = True
    check("宣言型の登録があり遊ぶ画面なら、当て直しの終わりに1度組み直す", choices.settle(ctx_a) is True)
    FakeClock.tick()
    check("組み直しは次のフレーム", app.refreshed == before + 1, app.refreshed)
    playing_hud.parent = None
    check("題の画面（HUD が付いていない）では組み直さない", choices.settle(ctx_a) is False)
    playing_hud.parent = object()
    app.is_button_enabled = False
    check("待機中は組み直さない（待機の終わりにゲームが組み直す）", choices.settle(ctx_a) is False)
    app.is_button_enabled = True
finally:
    ui.find_app, ui.find_hud = real_find_app, real_find_hud
    choices.withdraw("709_settle")
    FakeClock.onces[:] = []

print("[包み]")
ctx_c = Ctx("700_a", "g2")
choices.provide(ctx_c, screen_a, refresh=refresh_a, presses={"a:": press_a})
check("世代が変われば包み直す", choices.REFRESH_TARGET in ctx_c.hooks, sorted(ctx_c.hooks))
check("差し替えた後の持ち主の数は2つのまま", len(choices.providers()) == 2, choices.providers())
# 注入し直しで適用順が変わった（b を a より先に読む）。並びは新しい世代の登録の順になる。
choices.provide(Ctx("701_b", "g3"), screen_b, refresh=refresh_b, after=after_b)
choices.provide(Ctx("700_a", "g3"), screen_a, refresh=refresh_a, presses={"a:": press_a})
check("注入し直すと並びは新しい世代の適用順", choices.providers() == ["701_b", "700_a"], choices.providers())

print("[右の欄]")


class PanelHUD(InstanTaleHUD):
    """ゲームの HUD の右の欄の開け閉め（実機）: `in_battle` の間は右の欄に触らない。"""

    def __init__(self, opacity, left=4):
        self.right_button_layout = types.SimpleNamespace(opacity=opacity)
        self.buttons = [object()] * left
        self.button_texts = None             # 塗り終わりの比べ先が無ければ「塗り終わった」
        self.in_battle = True
        self.calls = []

    def update_button_texts(self, instance, value):
        self.calls.append((self.in_battle, list(value)))
        if not self.in_battle:
            self.right_button_layout.opacity = 1 if len(value) > len(self.buttons) else 0


class PanelApp(object):
    def __init__(self, opacity, texts, enabled=True):
        self.root = PanelHUD(opacity)
        self.to_display_buttons = list(texts)
        self.is_button_enabled = enabled

    @property
    def loads(self):
        """畳み直しの呼び出し（旗を下ろした update_button_texts）の回数。"""
        return [value for flag, value in self.root.calls if flag is False]


FakeClock.intervals[:] = []
battle = ["攻撃", "スキル・防御", "発言する", "逃げる"]
left_open = PanelApp(1, battle)
check("左に収まるのに開いている右の欄は取り残し", ui.right_panel_left_open(left_open))
check("溢れた選択肢を出している右の欄は取り残しではない",
      not ui.right_panel_left_open(PanelApp(1, battle + ["やめる"])))
check("閉じている右の欄は取り残しではない", not ui.right_panel_left_open(PanelApp(0, battle)))
check("右の欄が読めなければ触らない", not ui.right_panel_left_open(types.SimpleNamespace()))
waiting = PanelApp(1, battle, enabled=False)
check("待機中は畳まない（点送りが増える）", not ui.fold_right_panel(waiting) and waiting.loads == [])

ctx_p = Ctx("702_p", "g3")
choices.arm_fold(ctx_p, waiting)
FakeClock.tick()
check("待機中は見張りを続ける", waiting.loads == [] and len(FakeClock.intervals) == 1, waiting.loads)
waiting.is_button_enabled = True
FakeClock.tick()
check("手が空いたら1度だけ畳み直す（旗を下ろしてゲームの update_button_texts を今の選択肢で）",
      waiting.loads == [battle] and not FakeClock.intervals, (waiting.loads, FakeClock.intervals))
check("畳み直した後は右の欄が閉じ、HUD の in_battle は元に戻る",
      waiting.root.right_button_layout.opacity == 0 and waiting.root.in_battle is True)
stuck = PanelHUD(1)
stuck.update_button_texts(None, battle)
check("旗を立てたままのゲームの呼び出しでは閉じない（実機の取り残しの形）",
      stuck.right_button_layout.opacity == 1)
closed = PanelApp(0, battle)
choices.arm_fold(ctx_p, closed)
FakeClock.tick()
check("閉じていれば何もしない", closed.loads == [] and not FakeClock.intervals, closed.loads)
first, second = PanelApp(1, battle, enabled=False), PanelApp(1, battle)
choices.arm_fold(ctx_p, first)
choices.arm_fold(ctx_p, second)
FakeClock.tick()
FakeClock.tick()
check("新しい組み直しが前の見張りを差し替える", first.loads == [] and second.loads == [battle],
      (first.loads, second.loads))
check("偽の HUD に右の欄が無ければ見張らない",
      choices.arm_fold(ctx_p, types.SimpleNamespace(root=InstanTaleHUD())) is False)
FakeClock.intervals[:] = []

print("[宣言型の口]")
choices.reset()
FakeClock.onces[:] = []
ctx_d = Ctx("710_d", "g9")
ctx_e = Ctx("711_e", "g9")
pressed = []


def end_button(partner="95"):
    return {"text": "会話を終了する", "spec": Spec("ConversationEndManager", [partner, "user", "x"])}


def offer_d(app, view):
    pressed.append(("offer-d", view.partner))
    if view.partner != "95":
        return None
    return [choices.Choice("Dの1", on_press=lambda app: pressed.append("d1"), place=choices.before("ConversationEndManager")),
            choices.Choice("Dの2", on_press=lambda app: pressed.append("d2") or True,
                           place=choices.before("ConversationEndManager"))]


def offer_e(app, view):
    return [choices.Choice("Eの頭", place=choices.FIRST), choices.Choice("Eの尻", marks={"mod_e_action": "tail"})]


def legacy_refresh(app, buttons):
    if not any(b.get("text") == "古い口" for b in buttons):
        buttons.append({"text": "古い口", "spec": Spec("JustSetButtonToNormalPhase"), "mod_old_action": "x"})


choices.provide(ctx_d, None, offer=offer_d, labels=("Dの",))
choices.provide(ctx_e, None, offer=offer_e, refresh=legacy_refresh)
app = make_app()
ctx_a = ctx_d                                  # make_app の組み直しは ctx_a の包みを通る
app.in_conversation = False
app.world.characters["95"] = object()
game = [{"text": "売買する", "spec": Spec("ShoppingStartManagerRemake")}, end_button()]
app.buttons = list(game)
app.refresh_choice_buttons()
texts = [b["text"] for b in app.buttons]
check("offer は置き場所に従って並ぶ（先に登録した MOD が先・同じ置き場所は出した順）",
      texts == ["Eの頭", "売買する", "Dの1", "Dの2", "会話を終了する", "古い口", "Eの尻"], texts)
app.refresh_choice_buttons()
check("組み直しても増えない（前に組んだものを外してから作る）",
      [b["text"] for b in app.buttons] == texts, [b["text"] for b in app.buttons])
check("ゲームのボタンは同じ物のまま（書き換えない）", app.buttons[1] is game[0] and app.buttons[4] is game[1])
check("組んだボタンの spec は無害な既存クラス",
      ui.spec_cls_name(app.buttons[2]) == "JustSetButtonToNormalPhase" and choices.ours(app.buttons[2]))
press = ctx_d.hooks[choices.PRESS_TARGET]
flowed = []
saves = app.saves
press(lambda self, i: flowed.append(i), app, 2)
check("押すとボタンの処理を呼び、ゲームへ流さない", pressed[-1] == "d1" and not flowed, (pressed, flowed))
press(lambda self, i: flowed.append(i), app, 3)
FakeClock.tick()
check("処理が True を返したらゲーム自身の保存を呼ぶ", pressed[-1] == "d2" and app.saves == saves + 1, app.saves)
press(lambda self, i: flowed.append(i), app, 4)
check("ゲームのボタンは素通し", flowed == [4], flowed)
save = ctx_d.hooks[choices.SAVE_TARGET]
written = []
app.buttons_backup = list(app.buttons)
save(lambda self: written.append(([b["text"] for b in self.buttons], [b["text"] for b in self.buttons_backup])), app)
check("保存の間は宣言型のボタンを外す（buttons_backup も）",
      written == [(["売買する", "会話を終了する", "古い口"], ["売買する", "会話を終了する", "古い口"])], written)
check("保存の後は元に戻る", [b["text"] for b in app.buttons] == texts, [b["text"] for b in app.buttons])
check("marks はほかの MOD が見分ける印としてボタンに付く", app.buttons[-1].get("mod_e_action") == "tail", app.buttons[-1])
rows = choices._worlds(ctx_d).load(choices._worlds(ctx_d).playthrough(app)).get("marks") or []
check("宣言型のボタンの印は持ち越しに控えない（古い口の印だけ）", [r["text"] for r in rows] == ["古い口"], rows)
app.buttons_backup = None
# 前の版がセーブに焼いた自前のボタン（無害な spec・印なし・登録した文言）
app.buttons = [{"text": "Dの1", "spec": Spec("JustSetButtonToNormalPhase")},
               {"text": "Dの1", "spec": Spec("DisplayTalkChoice")}] + list(game)
app.refresh_choice_buttons()
texts = [b["text"] for b in app.buttons]
check("前の版の残骸は外して組み直す（spec の違うゲームのボタンは残す）",
      texts.count("Dの1") == 2 and ui.spec_cls_name(app.buttons[1]) == "DisplayTalkChoice"
      and choices.ours(app.buttons[texts.index("Dの1", 2)]), texts)
# 人物が揃う前（ロードの途中）は MOD の関数を呼ばない
pressed[:] = []
characters, app.world.characters = app.world.characters, {}
app.buttons = list(game)
app.refresh_choice_buttons()
check("人物が揃う前は offer を呼ばない", not pressed and [b["text"] for b in app.buttons]
      == ["売買する", "会話を終了する", "古い口"], (pressed, [b["text"] for b in app.buttons]))
app.world.characters = characters
FakeClock.intervals[:] = []
choices._store()["after"]["token"] = None
choices._store()["restore"] = None


def claim_e(app, view):
    if view.partner != "95":
        return None
    return [choices.Choice("受けて立つ", on_press=lambda app: pressed.append("fight"), base=view.end_entry)]


def claim_low(app, view):
    return [choices.Choice("負ける方")]


choices.provide(Ctx("712_low", "g9"), None, claim=claim_low, priority=1)
choices.provide(ctx_e, None, claim=claim_e, priority=50, refresh=legacy_refresh)
app.buttons = list(game)
app.refresh_choice_buttons()
check("claim は priority の大きい登録が勝ち、その画面はそのボタンだけ（ほかの MOD・古い口のボタンも出さない）",
      [b["text"] for b in app.buttons] == ["受けて立つ"], [b["text"] for b in app.buttons])
check("base のボタンは spec をそのまま使い、ゲームのボタンの文言は変えない",
      app.buttons[0]["spec"] is game[1]["spec"] and game[1]["text"] == "会話を終了する")
written = []
save(lambda self: written.append([b["text"] for b in self.buttons]), app)
check("claim の下のゲームの一覧がセーブに残る", written == [["売買する", "会話を終了する", "古い口"]], written)
press(lambda self, i: flowed.append(i), app, 0)
check("claim のボタンはその処理を呼ぶ", pressed[-1] == "fight", pressed)
app.buttons = [game[0], end_button("30")]
app.refresh_choice_buttons()
check("claim しない画面は低い claim へ", [b["text"] for b in app.buttons] == ["負ける方"],
      [b["text"] for b in app.buttons])
choices.withdraw("712_low")
choices.provide(ctx_e, None, offer=offer_e, refresh=legacy_refresh)

print("[MOD だけの画面]")


def confirm_screen(app, view, data):
    if view.partner != data.get("partner"):
        return None
    return [choices.Choice("はい", on_press=lambda app: pressed.append("yes")),
            choices.Choice("やめる", on_press=lambda app: choices.close_screen(app, "710_d"))]


choices.provide(ctx_d, None, offer=offer_d, labels=("Dの",), screens={"confirm": confirm_screen})
app.buttons = list(game)
app.refresh_choice_buttons()
FakeClock.onces[:] = []
InstanTaleHUD.painted[:] = []
choices.open_screen(app, "710_d", "confirm", {"partner": "95"})
FakeClock.tick()
check("開くと次のフレームで組み直して塗る（その画面のボタンだけ）",
      [b["text"] for b in app.buttons] == ["はい", "やめる"]
      and InstanTaleHUD.painted and InstanTaleHUD.painted[-1] == ["はい", "やめる"],
      ([b["text"] for b in app.buttons], InstanTaleHUD.painted))
check("下のゲームの一覧は書き換えない", choices.game_buttons(app)[:2] == game, choices.game_buttons(app))
written = []
save(lambda self: written.append([b["text"] for b in self.buttons]), app)
check("MOD だけの画面で保存すると、セーブには下のゲームの一覧が残る",
      written == [["売買する", "会話を終了する", "古い口"]], written)
# ロード: 世界が入れ替わり、セーブの一覧（ゲームのボタン）が戻る。メモリの控えは消えた（起動し直した）形
app.world = World(dict(characters))
app.buttons = [dict(b) for b in game]
choices._store()["opened"].clear()
app.refresh_choice_buttons()
check("ロードの後も控えから同じ画面を組む", [b["text"] for b in app.buttons] == ["はい", "やめる"],
      [b["text"] for b in app.buttons])
press(lambda self, i: flowed.append(i), app, 1)
FakeClock.tick()
check("閉じるとゲームの一覧に戻る（戻り先を失わない）",
      [b["text"] for b in app.buttons] == ["Eの頭", "売買する", "Dの1", "Dの2", "会話を終了する", "古い口", "Eの尻"]
      and choices.opened(app) is None, [b["text"] for b in app.buttons])
choices.open_screen(app, "710_d", "confirm", {"partner": "95"}, repaint_now=False)
app.buttons = [game[0], end_button("30")]
app.refresh_choice_buttons()
check("画面の関数が None を返したら閉じる", choices.opened(app) is None
      and "はい" not in [b["text"] for b in app.buttons], [b["text"] for b in app.buttons])
# 持ち主の居ないボタン（切った・注入し直しの途中）は握って組み直す
app.buttons = list(game)
app.refresh_choice_buttons()
choices.withdraw("710_d")
flowed[:] = []
refreshed = app.refreshed
press(lambda self, i: flowed.append(i), app, 2)
FakeClock.tick()
check("持ち主の居ないボタンはゲームへ流さず、組み直す", not flowed and app.refreshed > refreshed
      and "Dの1" not in [b["text"] for b in app.buttons], [b["text"] for b in app.buttons])

print("[古い口の WARN]")
choices.reset()
ctx_w = Ctx("730_w", "g11")
choices.provide(ctx_w, None, refresh=lambda app, buttons: None, presses={"x": lambda app, action: None},
                trim=lambda app, buttons: None)
check("presses= / trim= を渡した登録は WARN で移行を促す（持ち主と移行先）",
      any("730_w" in line and "presses=, trim=" in line and "offer / screens / claim" in line for line in ctx_w.logs),
      ctx_w.logs)
ctx_w.logs[:] = []
choices.provide(ctx_w, None, refresh=lambda app, buttons: None, offer=lambda app, view: None)
check("宣言型の口と refresh だけなら WARN は出ない", not any("older way" in line for line in ctx_w.logs), ctx_w.logs)

print("[差し替え・位置・文言の関数・画面の替わり]")
choices.reset()
FakeClock.onces[:] = []
ctx_f = Ctx("720_f", "g10")
ctx_a = ctx_f
surrender = {"text": "大人しく捕まる", "spec": Spec("TrialStartManager")}
resist = {"text": "抵抗する！", "spec": Spec("BattleStartManager", ["guard"])}
state_f = {"refused": False}


def offer_f(app, view):
    found = next((b for b in view.buttons if ui.spec_cls_name(b) == "TrialStartManager"), None)
    if found is None:
        return None
    if state_f["refused"]:
        return [choices.Choice("大人しく捕まる", base=found, replace=True,
                               on_press=lambda app: pressed.append("surrender"))]
    return [choices.Choice("金を握らせる", place=choices.at(1), on_press=lambda app: pressed.append("bribe"))]


choices.provide(ctx_f, None, offer=offer_f, labels=lambda app: ["動く文言"])
app = make_app()
app.in_conversation = False
app.buttons = [surrender, resist]
app.refresh_choice_buttons()
check("at(n) はゲームのボタンの n 番目の手前", [b["text"] for b in app.buttons] == ["大人しく捕まる", "金を握らせる", "抵抗する！"],
      [b["text"] for b in app.buttons])
state_f["refused"] = True
app.refresh_choice_buttons()
check("replace はゲームのボタンの場所に出し、元のボタンは出さない",
      [b["text"] for b in app.buttons] == ["大人しく捕まる", "抵抗する！"] and choices.ours(app.buttons[0])
      and app.buttons[0]["spec"] is surrender["spec"], app.buttons)
check("保存の前に外すと元のゲームのボタンに戻る", choices.strip(app.buttons) == ([surrender, resist], True),
      choices.strip(app.buttons))
# 後から登録した MOD は、位置と種類をゲームの一覧で数える。先の MOD が差し替えた所もゲームのボタンとして数える
ctx_g = Ctx("721_g", "g10")
choices.provide(ctx_g, None, offer=lambda app, view: [
    choices.Choice("後の at(1)", place=choices.at(1)),
    choices.Choice("後の before", place=choices.before("TrialStartManager"))])
app.refresh_choice_buttons()
check("差し替えた位置も数える（at は差し替えの後ろ・before は差し替えの手前）",
      [b["text"] for b in app.buttons] == ["後の before", "大人しく捕まる", "後の at(1)", "抵抗する！"],
      [b["text"] for b in app.buttons])
choices.withdraw("721_g")
app.buttons = [surrender, resist]
app.refresh_choice_buttons()

# 保存の間は宣言型のボタンを外し、終わったら戻す。保存の間にゲームが一覧を代入していたら、それを潰さない
save_f = ctx_f.hooks[choices.SAVE_TARGET]
composed = app.buttons
seen_in_save = []
save_f(lambda self: seen_in_save.append(list(self.buttons)), app)
check("保存の間は外し、終わったら組んだ一覧へ戻す",
      seen_in_save == [[surrender, resist]] and app.buttons is composed, (seen_in_save, app.buttons))
assigned = [{"text": "次の画面", "spec": Spec("MovePhaseManager")}]


def save_while_game_assigns(self):
    self.buttons = assigned             # ワーカーの execute が次の画面の一覧を代入した（ゲームの代入は鍵を持たない）


save_f(save_while_game_assigns, app)
check("保存の間にゲームが代入した一覧は潰さない", app.buttons is assigned, app.buttons)
app.buttons = [surrender, resist]
app.refresh_choice_buttons()

# 組み直しが重なっても、それぞれの MOD 関数には自分の組み直しの引数が見える
refresh_f = ctx_f.hooks[choices.REFRESH_TARGET]
first_in, seen_refreshing = threading.Event(), {}


def orig_first(self, *a, **k):
    first_in.set()
    time.sleep(0.3)                     # この間に後の組み直しが入ってくる
    seen_refreshing["first"] = choices.refreshing()


def orig_second(self, *a, **k):
    seen_refreshing["second"] = choices.refreshing()


runner = threading.Thread(target=lambda: refresh_f(orig_first, app, reset_page="first"))
runner.start()
first_in.wait(2)
refresh_f(orig_second, app, reset_page="second")
runner.join(5)
check("組み直しが重なっても、引数は自分の組み直しのもの",
      seen_refreshing.get("first") == ((), {"reset_page": "first"})
      and seen_refreshing.get("second") == ((), {"reset_page": "second"}), seen_refreshing)
press = ctx_f.hooks[choices.PRESS_TARGET]
flowed = []
press(lambda self, i: flowed.append(i), app, 0)
check("差し替えたボタンはその処理を呼ぶ（ゲームの TrialStartManager へ流さない）",
      pressed[-1] == "surrender" and not flowed, (pressed, flowed))
app.buttons = [surrender, {"text": "動く文言（12G）", "spec": Spec("JustSetButtonToNormalPhase")}, resist]
app.refresh_choice_buttons()
check("labels は関数でもよい（前の版の残骸を外す）", "動く文言（12G）" not in [b["text"] for b in app.buttons],
      [b["text"] for b in app.buttons])


def menu_f(app, view, data):
    return [choices.Choice("備える"), choices.Choice("やめる", on_press=lambda app: choices.close_screen(app, "720_f"))]


choices.provide(ctx_f, None, offer=offer_f, screens={"menu": menu_f})
year = [{"text": "服役する", "spec": Spec("PrisonManager", [3])}, {"text": "他の MOD", "spec": Spec(
    "JustSetButtonToNormalPhase"), "mod_other_action": "x"}]
app.buttons = list(year)
app.refresh_choice_buttons()
choices.open_screen(app, "720_f", "menu", {})
FakeClock.tick()
check("画面を開いたときのゲームの一覧の形を控える（MOD の印と無害な spec は数えない）",
      choices.opened(app)["game"] == ["PrisonManager"], choices.opened(app))
app.buttons = [{"text": "服役する", "spec": Spec("PrisonManager", [2])}]
app.refresh_choice_buttons()
check("同じ形の一覧なら画面を続ける（年数のような引数が変わっても）", [b["text"] for b in app.buttons] == ["備える", "やめる"],
      [b["text"] for b in app.buttons])
app.buttons = [{"text": "外へ出る", "spec": Spec("MovePhaseManager")}]
app.refresh_choice_buttons()
check("ゲームが一覧の形を替えたら（別の画面へ進んだら）閉じる",
      choices.opened(app) is None and [b["text"] for b in app.buttons] == ["外へ出る"], [b["text"] for b in app.buttons])

print("[ローダの部品の並び]")
choices.reset()
gen1 = [Ctx("301_x", 1), Ctx("330_y", 1), Ctx("402_z", 1)]
choices.provide(gen1[0], screen_a, refresh=refresh_a)
choices.provide(gen1[1], screen_a, refresh=refresh_a)
choices.provide(gen1[1], screen_a, refresh=refresh_a, owner="loader:modfacility")   # 1回目: 330 の apply の中から
choices.provide(gen1[2], screen_a, refresh=refresh_a)
first = choices.providers()
gen2 = [Ctx("loader", 2), Ctx("301_x", 2), Ctx("330_y", 2), Ctx("402_z", 2)]
choices.provide(gen2[0], screen_a, refresh=refresh_a, owner="loader:modfacility")   # 2回目: MOD の前の立て直しから
for c in gen2[1:]:
    choices.provide(c, screen_a, refresh=refresh_a)
second = choices.providers()
check("ローダの部品は MOD より先（1回目の起動）", first == ["loader:modfacility", "301_x", "330_y", "402_z"], first)
check("2回目の起動でも同じ並び", second == first, second)
choices.provide(Ctx("prices", 2), screen_a, refresh=refresh_a, owner="prices:inn")
check("`loader:` で始まらない持ち主は今までどおり末尾", choices.providers()[-1] == "prices:inn", choices.providers())

choices.reset()
shutil.rmtree(STATE, ignore_errors=True)
print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
