# -*- coding: utf-8 -*-
"""315_vacation_custom.py をゲーム抜きで通す。

    python tools/tests/test_vacation_custom.py

偽の app / PhaseSpec / DisplayVacationChoice / VacationStartManager /
VacationRestManager / VacationEndManager を差し込む。
**偽ゲームは実機の実測に合わせてある**（2026-08-18、`out/vacation.log`。
GAME.md §2.17）:

  部屋は4つ  犬小屋(0G)=kennel / 簡易寝台(10G)=bunk /
             個室(100G)=private_room / 高級個室(1000G)=luxury_suite
  宿代       `VacationStartManager.execute` の中で1回引かれる
  日数       同じ `execute` の中で `elapse_days(months*30)` が1回
             （宿泊の開始で全期間ぶん進み、その後の活動では動かない）
  連泊       `まだ宿泊する` は `VacationStartManager` をもう1周
             ＝**宿代も日数ももう1回**
  型         `period_months` は int、`quality` は str

確認するもの:

  ラベル … 名前か宿代を**変えた**部屋だけテンプレートで表示し直す
           （`王侯の間(300G)`）。素のままの部屋と `やめる` には触らない。
           spec と args には触らない。開き直しても表示は同じ（二重に化けない）。
           連泊の `まだ宿泊する`（料金の無いラベル）には触らない
  期間   … 選択式。「デフォルト」ならゲームのまま。月単位は
           `period_months` の差し替えで、表示・args・文言・日数がすべて揃う。
           週単位は months=1 ＋ 日数の予算で、`elapse_days` に渡る日数が
           設定の日数に切り詰まる。「1ヵ月泊まることにした。」も実際の期間に
           置き換わる。窓の外の日数送りには触らない。連泊の2周目も同じ日数。
           予算が1日も使われないまま宿泊が終わったら WARN が残る
  年齢   … 加算ON: 20代まで±0・30代+1・40代+2・50代以上+3（週/月）。
           上限 4週間 / 6ヵ月。加算OFFなら選んだ期間そのまま。年齢が読めない
           ときは加算なしでログに残る。月単位でゲームと同じ月数になったら
           表示も args も**何も動かない**（食い違いを作らない）
  宿代   … ゲームが取る前に前払い調整し、引き落としは1回でちょうど設定額。
           手持ちが「設定額以上・素の宿代未満」でも泊まれる。ゲームが
           取らなかったら建て替えは返す。連泊の再徴収も設定額になる。
           **タダの犬小屋を有料にしても**1回でちょうど設定額。
           float の所持金でも型が保たれる
  拒否   … 手持ちが設定額に満たないと、押した時点で断って宿泊を起こさない
  語彙   … `quality` も料金も実測の値に当たらないビルドでは、表示・宿代の
           **すべて**がゲームのまま（表示だけ変わる食い違いを作らない）。
           料金だけ変わったビルドでは quality で当てて設定を通す
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD = find_mod("_vacation_custom")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
#: 実測の部屋（2026-08-18）。
#: ラベルと quality の対まで実機どおり。
ROOMS = (("犬小屋(0G)", "kennel", 0),
         ("簡易寝台(10G)", "bunk", 10),
         ("個室(100G)", "private_room", 100),
         ("高級個室(1000G)", "luxury_suite", 1000))
ROOM_TEXTS = tuple(text for text, _q, _p in ROOMS)
GAME_PRICES = {quality: price for _t, quality, price in ROOMS}

#: 実測のプロンプト（`out/events.log`）。
#: ゲームが月数を文に焼き込んでいる。
REST_PROMPT = ("今プレイヤーキャラのエリスはこのエリアで数ヵ月の宿泊をし、"
               "疲労を回復した。労働、訓練、アイテム作成、研究などの行為はせず、"
               "社交もほどほどに、ただ穏やかに心身を休めることに努めた。")

#: 錨（`の宿泊` / `の滞在`）に当たらない宿泊まわりの文（実文言は未読）。
UNKNOWN_PROMPT = "エリスはこの宿で長い休息をとった。その滞在を描写せよ。"

#: LLM へ出ていく本文が通るローダの仕掛け先（ローカル経路の入口）。
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"

#: 素の宿泊期間（実測: 20代=3ヵ月・31歳=4ヵ月）。
#: 既定の偽プレイヤーは25歳。
GAME_MONTHS = 3
STAY_TEXT = "宿泊する({}ヵ月)".format(GAME_MONTHS)


class Player:
    def __init__(self):
        self.gold = 5000
        self.age = 25


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class DisplayVacationChoice:
    """部屋選びの画面。実測の4部屋を並べる。"""

    rooms = ROOMS

    def __init__(self, app, period_months):
        self.app = app
        self.period_months = period_months

    def execute(self, choice_text):
        self.update_button_display()
        return None

    def update_button_display(self):
        self.app.buttons = [
            {"text": text,
             "spec": PhaseSpec("VacationStartManager",
                               [self.period_months, quality])}
            for text, quality, _price in type(self).rooms
        ] + [{"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
        self.app.refresh_choice_buttons(reset_page=True)


class VacationStartManager:
    """部屋を決めて宿泊を始める。**宿代も日数もここで1回**（実測）。"""

    #: 宿代を `execute` の中で引かないビルドの想定（徴収が別の場所にあるケース）。
    charge = True
    #: 日数送りが `elapse_days` を通らないビルドの想定（`307_` が踏んだ形）。
    elapses = True

    def __init__(self, app, months, quality):
        self.app = app
        self.months = months
        self.quality = quality

    def execute(self, choice_text):
        price = GAME_PRICES.get(str(self.quality))
        if type(self).charge and price is not None:
            if self.app.player.gold < price:
                self.app.add_text("宿代が足りない。")
                return None
            self.app.player.gold -= price
            # 支払い直後にゲームが所持金の表示を描き直す瞬間の値（`314_` のテストと同じ検証点
            # ― ここが既に差し引き後の正しい値であること）。
            self.app.gold_after_payment = self.app.player.gold
        self.app.stays.append((self.months, self.quality, choice_text))
        self.app.add_text("{}ヵ月泊まることにした。".format(self.months))
        self.app.change_background_image_to_inn_room(self.quality)
        if type(self).elapses:
            self.app.elapse_days(int(str(self.months)) * 30)
        self.app.add_text("何をして過ごす？")
        self.app.buttons = [
            {"text": "休養をとる",
             "spec": PhaseSpec("VacationRestManager",
                               [self.months, self.quality])},
            {"text": "まだ宿泊する",
             "spec": PhaseSpec("VacationStartManager",
                               [self.months, self.quality])},
            {"text": "宿泊を終える", "spec": PhaseSpec("VacationEndManager", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class VacationRestManager:
    """休養。実測では**日数も金も動かない**（描写が出るだけ）。"""

    #: 窓の中で追加に送る本文（錨に当たらない文を試すため）。
    extra_prompts = ()

    def __init__(self, app, months, quality):
        self.app = app
        self.months = months
        self.quality = quality

    def execute(self, choice_text):
        # 実測では描写の LLM 呼び出しが `execute` の中で起きる（応答が窓の
        # texts に入っていた）。
        self.app.send_prompt(REST_PROMPT)
        for prompt in type(self).extra_prompts:
            self.app.send_prompt(prompt)
        self.app.add_text("静かな時を過ごした。")
        return None


class VacationEndManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.add_text("宿泊を終えた。")
        return None


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class InstantaleApp:
    def __init__(self):
        self.player = Player()
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.stays = []
        self.backgrounds = []
        self.gold_after_payment = None
        self.refreshes = 0
        self.day = 0
        self.elapsed = []
        self.sent = []
        self.chat_hook = None

    def send_prompt(self, prompt):
        """LLM へ本文を送る（`LlamaCppClient.chat` の経路を偽物で再現）。

        ローダの `llm.wrap_outgoing` が仕掛けたフックを通す。
        **送られた messages をそのまま控える**ので、書き換えの有無が読める。
        """
        messages = [{"role": "system", "content": "あなたはゲームマスターである。"},
                    {"role": "user", "content": prompt}]
        sent = self.sent

        def orig(_self, _model, msgs, _format=None, *args, **kwargs):
            sent.append(msgs)
            return None

        if self.chat_hook is None:
            orig(None, None, messages)
        else:
            self.chat_hook(orig, object(), "model", messages, None)
        return self.sent[-1][1]["content"]

    def add_text(self, context):
        self.texts.append(context)

    def elapse_days(self, days):
        self.elapsed.append(days)
        self.day += days
        return None

    def change_background_image_to_inn_room(self, quality):
        self.backgrounds.append(quality)
        return None

    def process_choice(self, function, choice_text=""):
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def on_button_press(self, button_index):
        entry = self.buttons[button_index]
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), entry.get("text"))

    def open_inn_menu(self, period_months=GAME_MONTHS):
        """施設の画面。`宿泊する(3ヵ月)` のボタンが並ぶ（描き手のクラスは
        未実測なので、偽物では app が直接組む ― mod は spec でしか見ない）。"""
        self.buttons = [
            {"text": "宿泊する({}ヵ月)".format(period_months),
             "spec": PhaseSpec("DisplayVacationChoice", [period_months])},
            {"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])},
            {"text": "出る", "spec": PhaseSpec("MovePhaseManager", [])},
        ]
        self.refresh_choice_buttons(reset_page=True)


BASES = {"app": InstantaleApp, "choice": DisplayVacationChoice,
         "start": VacationStartManager, "rest": VacationRestManager,
         "end": VacationEndManager}


class FakeClock:
    def __init__(self):
        self.onces = []

    def schedule_interval(self, callback, timeout):
        pass

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def settle(self):
        for _ in range(8):
            pending, self.onces = self.onces, []
            if not pending:
                return
            for callback in pending:
                callback(0.0)


def install_fake_kivy():
    clock = FakeClock()
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    sys.modules.pop("kivy.app", None)
    return clock


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`314_` のテストと同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator

    # `llm.wrap_outgoing` はクラウド（APIキー）側の別名も包む。
    # **その別名が引ければ見張りスレッドは立たない**ので、
    # 偽の `llm_manager` を返してその場で当てさせる（立たせると、
    # テストの間ポーリングし続けたうえ `superseded()` が無くて log_exc に落ちる）。
    manager = types.SimpleNamespace(
        send_request=lambda *a, **k: None,
        send_request_with_no_structure=lambda *a, **k: None)

    def resolve(self, target):
        name = target.rpartition(":")[2].rsplit(".", 1)[-1]
        owner = self.manager if "llm_manager" in target else None
        return owner, name, getattr(owner, name, None)

    def superseded(self):
        return False              # テスト中に注入し直しは起きない


def load_mod(path=MOD, name="vacation_custom_mod"):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def install(hooks, targets):
    for target, owner, name in targets:
        hook = hooks.get(target)
        if hook is None:
            continue
        original = getattr(owner, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(owner, name, make())


CLOCK = install_fake_kivy()
LOG_PATH = os.path.join(OUT_DIR, "vacation_custom.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(configure=None, age=25):
    """mod を適用し、宿の施設の画面が開いた状態の app を返す。"""
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    choice_cls = type("DisplayVacationChoice", (BASES["choice"],), {})
    start_cls = type("VacationStartManager", (BASES["start"],), {})
    rest_cls = type("VacationRestManager", (BASES["rest"],), {})
    end_cls = type("VacationEndManager", (BASES["end"],), {})

    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.DisplayVacationChoice = choice_cls
    main.VacationStartManager = start_cls
    main.VacationRestManager = rest_cls
    main.VacationEndManager = end_cls
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)

    module = load_mod()
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.refresh_choice_buttons", app_cls,
         "refresh_choice_buttons"),
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
        ("__main__:InstantaleApp.elapse_days", app_cls, "elapse_days"),
        ("__main__:InstantaleApp.add_text", app_cls, "add_text"),
        ("__main__:DisplayVacationChoice.__init__", choice_cls, "__init__"),
        ("__main__:VacationStartManager.__init__", start_cls, "__init__"),
        ("__main__:VacationStartManager.execute", start_cls, "execute"),
        ("__main__:VacationRestManager.execute", rest_cls, "execute"),
        ("__main__:VacationEndManager.execute", end_cls, "execute"),
    ))

    app = app_cls()
    app.player.age = age
    app.chat_hook = ctx.hooks.get(CHAT_TARGET)
    main.current_app = app
    app.open_inn_menu()
    CLOCK.settle()
    globals()["REST_CLS"] = rest_cls
    return module, ctx, app, choice_cls, start_cls


def texts_of(app):
    return [entry.get("text") for entry in app.buttons]


def press(app, text):
    for index, entry in enumerate(app.buttons):
        if entry.get("text") == text:
            app.on_button_press(index)
            CLOCK.settle()
            return
    raise AssertionError("no such button: {!r} in {}".format(text, texts_of(app)))


# ================================================================ ラベル
print("[既定] 素のゲームから何も変えない")
module, ctx, app, choice_cls, start_cls = setup()
check("施設の画面は元のまま", texts_of(app) == [STAY_TEXT, "会話する", "出る"],
      texts_of(app))
press(app, STAY_TEXT)
check("部屋4つのボタンは元のまま",
      texts_of(app) == list(ROOM_TEXTS) + ["やめる"], texts_of(app))
check("spec の args は元のまま（months は int）",
      app.buttons[0]["spec"].args == [3, "kennel"]
      and app.buttons[3]["spec"].args == [3, "luxury_suite"],
      [entry["spec"].args for entry in app.buttons])
press(app, "個室(100G)")
check("宿代はゲームのまま 100 引かれる", app.player.gold == 4900,
      app.player.gold)
check("文言もゲームのまま", "3ヵ月泊まることにした。" in app.texts, app.texts)
check("背景の quality もゲームのまま", app.backgrounds == ["private_room"],
      app.backgrounds)
check("日数送りもゲームのまま（90日）", app.elapsed == [90], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[ラベル] 名前と宿代を変えた部屋だけ表示し直す")


def luxury300(module):
    module.LUXURY_NAME = "王侯の間"
    module.LUXURY_PRICE = 300


module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
press(app, STAY_TEXT)
check("変えた部屋だけ新しい表示", texts_of(app)[3] == "王侯の間(300G)",
      texts_of(app))
check("素のままの部屋は元のまま",
      texts_of(app)[:3] == list(ROOM_TEXTS[:3]), texts_of(app))
check("「やめる」には触らない", texts_of(app)[4] == "やめる")
check("spec の args は元のまま",
      app.buttons[3]["spec"].args == [3, "luxury_suite"])
before = texts_of(app)
press(app, "やめる")
app.open_inn_menu()
CLOCK.settle()
press(app, STAY_TEXT)
check("開き直しても表示は同じ", texts_of(app) == before, texts_of(app))
check("エラーなし", not ctx.errors, ctx.errors)

print("[ラベル] 犬小屋（実測で見つかった4つ目の部屋）も変えられる")


def kennel_paid(module):
    module.KENNEL_NAME = "馬房の隅"
    module.KENNEL_PRICE = 5


module, ctx, app, choice_cls, start_cls = setup(configure=kennel_paid)
press(app, STAY_TEXT)
check("犬小屋の表示が変わる", texts_of(app)[0] == "馬房の隅(5G)", texts_of(app))
check("unknown room は出ない", "unknown room" not in read_log())
press(app, "馬房の隅(5G)")
check("タダの部屋を有料にしても1回でちょうど設定額",
      app.player.gold == 4995, app.player.gold)
check("支払いの瞬間から差し引き後の値", app.gold_after_payment == 4995,
      app.gold_after_payment)
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 宿代
print("[宿代] 設定額300を1回で引き落とす（手持ち5000）")
module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
press(app, STAY_TEXT)
press(app, "王侯の間(300G)")
check("宿泊が始まる", app.stays and app.stays[0][1] == "luxury_suite", app.stays)
check("300だけ引かれる", app.player.gold == 4700, app.player.gold)
check("支払いの瞬間から差し引き後の値（1000引いて返す動きをしない）",
      app.gold_after_payment == 4700, app.gold_after_payment)
check("エラーなし", not ctx.errors, ctx.errors)

print("[宿代] 手持ちが素の宿代未満でも泊まれる（手持ち600・設定300）")
module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
app.player.gold = 600
press(app, STAY_TEXT)
press(app, "王侯の間(300G)")
check("素の残高チェックに弾かれず泊まれる", bool(app.stays), app.texts)
check("300だけ引かれる", app.player.gold == 300, app.player.gold)

print("[宿代] 連泊（まだ宿泊する）の再徴収も設定額になる")
module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
press(app, STAY_TEXT)
press(app, "王侯の間(300G)")
check("まだ宿泊する のラベルには触らない", "まだ宿泊する" in texts_of(app),
      texts_of(app))
press(app, "まだ宿泊する")
check("2周目も300だけ引かれる", app.player.gold == 4400, app.player.gold)
check("2周とも泊まっている", len(app.stays) == 2, app.stays)

print("[宿代] 宿代を引かないビルドでは触らない")
module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
start_cls.charge = False
press(app, STAY_TEXT)
press(app, "王侯の間(300G)")
check("宿泊は始まる", bool(app.stays), app.stays)
check("所持金には触らない", app.player.gold == 5000, app.player.gold)
check("建て替えを返したことがログに残る", "did not charge" in read_log())
start_cls.charge = True

print("[宿代] float の所持金でも型が保たれる")
module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
app.player.gold = 5000.0
press(app, STAY_TEXT)
press(app, "王侯の間(300G)")
check("float のまま 4700.0", app.player.gold == 4700.0
      and isinstance(app.player.gold, float), app.player.gold)

print("[拒否] 手持ちが設定額に満たない")
module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
app.player.gold = 200
press(app, STAY_TEXT)
press(app, "王侯の間(300G)")
check("宿泊を起こさない", not app.stays, app.stays)
check("所持金はそのまま", app.player.gold == 200, app.player.gold)
check("断りの一言が出る", any("足りない" in t for t in app.texts), app.texts)
check("部屋選びはそのまま残る", texts_of(app)[0] == ROOM_TEXTS[0],
      texts_of(app))

# ================================================================ 期間（月単位）
print("[期間] 6ヵ月にする（20代＝加算なし。表示・args・文言・日数がすべて揃う）")


def month6(module):
    module.STAY_LENGTH = "6ヵ月"


module, ctx, app, choice_cls, start_cls = setup(configure=month6)
check("宿泊のボタンが 6ヵ月 になる", texts_of(app)[0] == "宿泊する(6ヵ月)",
      texts_of(app))
press(app, "宿泊する(6ヵ月)")
check("部屋ボタンの args の月数も 6（型も int のまま）",
      app.buttons[2]["spec"].args == [6, "private_room"],
      [entry["spec"].args for entry in app.buttons])
press(app, "個室(100G)")
check("文言も 6ヵ月", "6ヵ月泊まることにした。" in app.texts, app.texts)
check("日数送りも 6ヵ月ぶん（180日）", app.elapsed == [180], app.elapsed)
check("宿代はゲームのまま 100", app.player.gold == 4900, app.player.gold)
check("エラーなし", not ctx.errors, ctx.errors)

print("[期間] 1ヵ月・40代（+2ヵ月）＝ゲームの3ヵ月と同じになり、何も動かない")


def month1(module):
    module.STAY_LENGTH = "1ヵ月"


module, ctx, app, choice_cls, start_cls = setup(configure=month1, age=42)
check("表示はゲームのまま（3ヵ月のまま＝食い違いなし）",
      texts_of(app)[0] == STAY_TEXT, texts_of(app))
press(app, STAY_TEXT)
check("args も 3 のまま", app.buttons[2]["spec"].args == [3, "private_room"],
      [entry["spec"].args for entry in app.buttons])
press(app, "個室(100G)")
check("日数送りも 3ヵ月ぶん（90日）", app.elapsed == [90], app.elapsed)

print("[期間] 4ヵ月・60代（+3）は上限の 6ヵ月で止まる")


def month4(module):
    module.STAY_LENGTH = "4ヵ月"


module, ctx, app, choice_cls, start_cls = setup(configure=month4, age=61)
check("宿泊のボタンが 6ヵ月", texts_of(app)[0] == "宿泊する(6ヵ月)",
      texts_of(app))
press(app, "宿泊する(6ヵ月)")
press(app, "個室(100G)")
check("日数送りも 180日", app.elapsed == [180], app.elapsed)

# ================================================================ 期間（週単位）
print("[期間] 1週間・20代（months=1 ＋ 日数の予算）")


def week1(module):
    module.STAY_LENGTH = "1週間"


module, ctx, app, choice_cls, start_cls = setup(configure=week1)
check("宿泊のボタンが 1週間 になる", texts_of(app)[0] == "宿泊する(1週間)",
      texts_of(app))
press(app, "宿泊する(1週間)")
check("部屋ボタンの args の月数は 1（ゲームの最小単位）",
      app.buttons[2]["spec"].args == [1, "private_room"],
      [entry["spec"].args for entry in app.buttons])
press(app, "個室(100G)")
check("文言は実際の期間（1週間泊まることにした。）",
      "1週間泊まることにした。" in app.texts, app.texts)
check("ゲームの 1ヵ月 の文言は出ない",
      not any("1ヵ月泊まる" in t for t in app.texts), app.texts)
check("日数送りが 7日 に切り詰まる", app.elapsed == [7], app.elapsed)
check("暦も7日だけ進む", app.day == 7, app.day)
check("宿代は1回ぶん（100G）", app.player.gold == 4900, app.player.gold)
press(app, "宿泊を終える")
check("窓の外の日数送りには触らない",
      (app.elapse_days(50) or app.elapsed[-1]) == 50, app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[期間] 連泊の2周目も同じ日数（予算の積み直し）")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
press(app, "まだ宿泊する")
check("7日＋7日", app.elapsed == [7, 7], app.elapsed)
check("2周とも泊まっている", len(app.stays) == 2, app.stays)
check("宿代も2回ぶん（100+100）", app.player.gold == 4800, app.player.gold)

print("[期間] 日数送りが elapse_days を通らないビルドは WARN で分かる")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
start_cls.elapses = False
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
press(app, "宿泊を終える")
check("暦は動かない（ゲーム任せ）", app.elapsed == [], app.elapsed)
check("WARN が残る", "no elapse_days call" in read_log())
start_cls.elapses = True

# ================================================================ 年齢
print("[年齢] 30代は 1週間 → 2週間")
module, ctx, app, choice_cls, start_cls = setup(configure=week1, age=31)
check("宿泊のボタンが 2週間", texts_of(app)[0] == "宿泊する(2週間)",
      texts_of(app))
press(app, "宿泊する(2週間)")
press(app, "個室(100G)")
check("文言も 2週間", "2週間泊まることにした。" in app.texts, app.texts)
check("日数送りは 14日", app.elapsed == [14], app.elapsed)

print("[年齢] 50代の 2週間 は上限の 4週間で止まる")


def week2(module):
    module.STAY_LENGTH = "2週間"


module, ctx, app, choice_cls, start_cls = setup(configure=week2, age=55)
check("宿泊のボタンが 4週間", texts_of(app)[0] == "宿泊する(4週間)",
      texts_of(app))
press(app, "宿泊する(4週間)")
press(app, "個室(100G)")
check("日数送りは 28日", app.elapsed == [28], app.elapsed)

print("[年齢] 加算OFFなら選んだ期間そのまま（実機の設定と同じ形）")


def week2_no_age(module):
    module.STAY_LENGTH = "2週間"
    module.AGE_SCALING = False


module, ctx, app, choice_cls, start_cls = setup(configure=week2_no_age, age=31)
check("宿泊のボタンが 2週間", texts_of(app)[0] == "宿泊する(2週間)",
      texts_of(app))
press(app, "宿泊する(2週間)")
press(app, "個室(100G)")
check("日数送りは 14日", app.elapsed == [14], app.elapsed)

print("[年齢] 年齢が読めないときは加算なしでログに残る")
module, ctx, app, choice_cls, start_cls = setup(configure=week1, age="若い")
check("宿泊のボタンは加算なしの 1週間", texts_of(app)[0] == "宿泊する(1週間)",
      texts_of(app))
check("読めなかったことがログに残る",
      "cannot read app.player.age" in read_log())

# ================================================================ AIの描写
print("[描写] 週単位の間だけ、LLM へ出ていく本文の月数を実際の期間に直す")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
check("ローダの仕掛け口に乗っている", app.chat_hook is not None)
check("窓の外では素通し", app.send_prompt(REST_PROMPT) == REST_PROMPT)
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
press(app, "休養をとる")
sent = app.sent[-1][1]["content"]
check("「数ヵ月の宿泊」が「1週間の宿泊」になる", "1週間の宿泊" in sent, sent)
check("ゲームの「数ヵ月」は残らない", "数ヵ月" not in sent, sent)
check("それ以外の本文はそのまま",
      sent.replace("1週間の宿泊", "数ヵ月の宿泊") == REST_PROMPT, sent)
check("system 側の本文には触らない",
      app.sent[-1][0]["content"] == "あなたはゲームマスターである。")
check("書き換えがログに残る", "prompt at chat" in read_log())
press(app, "宿泊を終える")
check("宿泊が終われば素通しに戻る", app.send_prompt(REST_PROMPT) == REST_PROMPT)
check("エラーなし", not ctx.errors, ctx.errors)

print("[描写] 30代の2週間なら「2週間の宿泊」（年齢の加算まで揃う）")
module, ctx, app, choice_cls, start_cls = setup(configure=week1, age=31)
press(app, "宿泊する(2週間)")
press(app, "個室(100G)")
press(app, "休養をとる")
check("2週間の宿泊", "2週間の宿泊" in app.sent[-1][1]["content"],
      app.sent[-1][1]["content"])

print("[描写] 月単位の宿泊では触らない（ゲームの「数ヵ月」で正しい）")
module, ctx, app, choice_cls, start_cls = setup(configure=month6)
press(app, "宿泊する(6ヵ月)")
press(app, "個室(100G)")
press(app, "休養をとる")
check("素通し", app.sent[-1][1]["content"] == REST_PROMPT,
      app.sent[-1][1]["content"])

print("[描写] 設定を切れば何もしない")


def week1_no_llm(module):
    module.STAY_LENGTH = "1週間"
    module.LLM_STAY_WORDING = False


module, ctx, app, choice_cls, start_cls = setup(configure=week1_no_llm)
check("仕掛け口に乗らない", app.chat_hook is None)
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
press(app, "休養をとる")
check("素通し", app.sent[-1][1]["content"] == REST_PROMPT,
      app.sent[-1][1]["content"])
check("日数の切り詰めは効いたまま", app.elapsed == [7], app.elapsed)

print("[描写] 錨に当たらない宿泊の文はログに残す（実文言を実機から拾うため）")
LIFELOG = "エリスは数ヵ月前に霧の要塞都市へ来た。数ヵ月にわたり依頼をこなした。"
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
REST_CLS.extra_prompts = (UNKNOWN_PROMPT, LIFELOG)
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
press(app, "休養をとる")
check("本文はそのまま送られる",
      app.sent[1][1]["content"] == UNKNOWN_PROMPT, app.sent[1][1]["content"])
check("拾えなかったことがログに残る", "kept as is" in read_log())
check("人生ログの「数ヵ月」には当たらない（錨が無い）",
      app.sent[2][1]["content"] == LIFELOG, app.sent[2][1]["content"])
check("エラーなし", not ctx.errors, ctx.errors)
REST_CLS.extra_prompts = ()

# ================================================================ 語彙
print("[語彙] quality も料金も当たらないビルド（設定は変えてあるのに、何もしない）")


def simple5(module):
    module.SIMPLE_PRICE = 5


module, ctx, app, choice_cls, start_cls = setup(configure=simple5)
choice_cls.rooms = (("簡易寝台(20G)", "bunk_v2", 20),) + ROOMS[2:]
GAME_PRICES["bunk_v2"] = 20
press(app, STAY_TEXT)
check("当たらない部屋のラベルはゲームのまま",
      texts_of(app)[0] == "簡易寝台(20G)", texts_of(app))
check("unknown room がログに残る", "unknown room" in read_log())
gold = app.player.gold
press(app, "簡易寝台(20G)")
check("宿代もゲームのまま（触らない）", app.player.gold == gold - 20,
      app.player.gold)
choice_cls.rooms = ROOMS
GAME_PRICES.pop("bunk_v2", None)

print("[語彙] 料金だけ変わったビルドでも quality で当てて設定を通す")
module, ctx, app, choice_cls, start_cls = setup(configure=simple5)
choice_cls.rooms = (ROOMS[0], ("簡易寝台(20G)", "bunk", 20)) + ROOMS[2:]
GAME_PRICES["bunk"] = 20
press(app, STAY_TEXT)
check("設定した 5G で表示される", texts_of(app)[1] == "簡易寝台(5G)",
      texts_of(app))
press(app, "簡易寝台(5G)")
check("画面に出ていた 20G ではなく設定の 5G が引かれる",
      app.player.gold == 4995, app.player.gold)
choice_cls.rooms = ROOMS
GAME_PRICES["bunk"] = 10

print("[語彙] 手持ちが「設定額以上・素の宿代未満」でも当たった部屋は泊まれる")
module, ctx, app, choice_cls, start_cls = setup(configure=simple5)
app.player.gold = 8
press(app, STAY_TEXT)
check("簡易寝台が 5G 表示になる", texts_of(app)[1] == "簡易寝台(5G)",
      texts_of(app))
press(app, "簡易寝台(5G)")
check("素の10Gに満たなくても泊まれて、5だけ引かれる", app.player.gold == 3,
      (app.player.gold, app.texts))

# ================================================================ まとめ
print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
