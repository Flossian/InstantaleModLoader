# -*- coding: utf-8 -*-
"""916_training_custom.py をゲーム抜きで通す（開発中の MOD。TECH.md §2.6）。

    python tools/tests/test_wip_training_custom.py

偽の app / PhaseSpec / DisplayTrainingChoice / TrainingStartManager /
TrainingPhaseManager を差し込み、次を確認する。

  既定   … 素の設定（300G・3年・1年365日）では表示も所持金も日数も1つも動かない
  代金   … 設定した額でゲームが引き落とす（引数の差し替え）。ボタンもその額で出る。
           代金を引数から取らないビルドでは、差額をその場で戻して WARN を残す
  拒否   … 手持ちが設定額に満たないと、押した時点で断って訓練を始めない
  年数   … 1回の修行の年数を増やすと、ゲーム自身が残り年数をその数から数える
  日数   … 段の実行中だけ `elapse_days` に渡る数が「年数 × 設定の1年」になる。
           活動の年数（1年・2年・3年）はゲームのまま。窓の外の日数送りには触らない
  表示   … 修行内容のボタンは既定では1文字も変えず、`{days}` を入れたときだけ実日数が出る
  個別   … ワールド個別の控えがある世界では一括設定を上書きし、消せば戻る
  窓口   … ローダの `durations` に訓練の暦を置く（活動ごとの年数は素の値のまま）

偽ゲームの形は実測に合わせてある（`231_probe_training`、2026-09-15。GAME.md §2.17）:
代金は `TrainingStartManager.execute` で引かれて日数は進まず、
暦は各段の `TrainingPhaseManager.execute` で「活動の年数 × 365 日」が1回で進む。
活動の年数と残り年数の減り方は**ゲームの内側**に在るので、偽物もその形にしてある
（引数には出てこない ＝ MOD は触れない）。
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
from instantale_modloader import durations             # noqa: E402


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


MOD = find_mod("_training_custom")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
#: 実測の値（GAME.md §2.17）。
GAME_PRICE = 300
GAME_COURSE_YEARS = 3
GAME_DAYS_PER_YEAR = 365

#: 修行内容。**年数はゲームの内側の表**で、引数には出てこない（実測どおり）。
ACTIVITIES = (("simple", "ただ鍛える", 1),
              ("fundamental", "基礎を積む", 2),
              ("train_skill", "技を磨く", 2),
              ("learn_new_skill", "新たな技を学ぶ", 3))
YEARS_OF = dict((key, years) for key, _name, years in ACTIVITIES)

START_TEXT = "訓練を受ける({}G)".format(GAME_PRICE)
PHASE_LOG = "一年間、ひたすら鍛錬した。0の経験値を得た。"


class World:
    def __init__(self):
        self.areas = {}
        self.name = "テスト世界"


class Player:
    def __init__(self, gold=100000):
        self.gold = gold
        self.age = 20


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


def phase_buttons(remaining, log):
    """残り年数に**収まる活動だけ**が並ぶ（実測どおり）。"""
    entries = [{"text": "{}({}年)".format(name, years),
                "spec": PhaseSpec("TrainingPhaseManager", [key, remaining, log])}
               for key, name, years in ACTIVITIES if years <= remaining]
    if not entries:
        entries = [{"text": "やった",
                    "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
    return entries


class DisplayTrainingChoice:
    def __init__(self, app, training_type):
        self.app = app
        self.training_type = training_type

    def execute(self, choice_text):
        self.update_button_display()
        return None

    def update_button_display(self):
        self.app.buttons = [
            {"text": START_TEXT,
             "spec": PhaseSpec("TrainingStartManager",
                               [GAME_COURSE_YEARS, GAME_PRICE])},
            {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class TrainingStartManager:
    """年月と代金を払って教わる。代金はここで1回引かれ、日数は進まない（実測）。"""

    #: 代金を引数から引くビルド（実測はこちら）。False は別の場所で額を決めるビルド。
    charge_from_args = True

    def __init__(self, app, training_years, training_price):
        self.app = app
        self.training_years = training_years
        self.training_price = training_price

    def execute(self, choice_text):
        price = self.training_price if type(self).charge_from_args else GAME_PRICE
        if self.app.player.gold < price:
            self.app.add_text("金が足りない...")
            return None
        self.app.player.gold -= price
        self.app.add_text("あと{}年間。どうする？".format(self.training_years))
        self.app.buttons = phase_buttons(self.training_years, "")
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class TrainingPhaseManager:
    """その1段。活動の年数 × 365 日が1回で進み、残り年数がその年数ぶん減る。"""

    def __init__(self, app, training_type, remaining_years, training_log):
        self.app = app
        self.training_type = training_type
        self.remaining_years = remaining_years
        self.training_log = training_log

    def execute(self, choice_text):
        years = YEARS_OF.get(self.training_type, 1)
        self.app.add_text("鍛えた。")
        self.app.elapse_days(years * GAME_DAYS_PER_YEAR)
        left = self.remaining_years - years
        if left <= 0:
            self.app.add_text("訓練を終えた。卒業だ...")
            self.app.buttons = [{"text": "やった",
                                 "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
        else:
            self.app.buttons = phase_buttons(left, PHASE_LOG)
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class InstantaleApp:
    def __init__(self, world):
        self.world = world
        self.player = Player()
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.day = 0
        self.elapsed = []
        self.refreshes = 0

    def add_text(self, context):
        self.texts.append(context)

    def elapse_days(self, days):
        self.elapsed.append(days)
        self.day += days
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


BASES = {"app": InstantaleApp, "choice": DisplayTrainingChoice,
         "start": TrainingStartManager, "phase": TrainingPhaseManager}


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
    _seq = 0

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.logs = []
        # 世代は apply() ごとに違う（本物の `ctx.generation`）。
        # ローダの日数送りの関所は世代で「もう立てたか」を見るので、
        # ここが同じ値だと 2本目以降の apply() で関所が立たない（durations.install）。
        FakeCtx._seq += 1
        self.generation = FakeCtx._seq

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`314_` のテストと同じ理由）。
    _mod = None

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        """同じ対象に2枚当たったら層にする（本物は後から当てたほうが外側）。"""
        def decorator(func):
            previous = self.hooks.get(target)
            if previous is None:
                self.hooks[target] = func
                return func

            def layered(orig, this, *args, _prev=previous, _func=func, **kwargs):
                def inner(obj, *a, **kw2):
                    return _prev(orig, obj, *a, **kw2)
                return _func(inner, this, *args, **kwargs)

            self.hooks[target] = layered
            return func
        return decorator


def load_mod(path=MOD, name="training_custom_mod"):
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
LOG_PATH = os.path.join(OUT_DIR, "training_custom.log")


def read_log():
    try:
        with io.open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(configure=None, charge_from_args=True, gold=100000):
    """mod を適用し、訓練所の選択肢が開いた状態の app を返す。"""
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    choice_cls = type("DisplayTrainingChoice", (BASES["choice"],), {})
    start_cls = type("TrainingStartManager", (BASES["start"],),
                     {"charge_from_args": charge_from_args})
    phase_cls = type("TrainingPhaseManager", (BASES["phase"],), {})

    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.DisplayTrainingChoice = choice_cls
    main.TrainingStartManager = start_cls
    main.TrainingPhaseManager = phase_cls
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
        ("__main__:DisplayTrainingChoice.update_button_display", choice_cls,
         "update_button_display"),
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
        ("__main__:TrainingStartManager.__init__", start_cls, "__init__"),
        ("__main__:TrainingStartManager.execute", start_cls, "execute"),
        ("__main__:TrainingPhaseManager.__init__", phase_cls, "__init__"),
        ("__main__:TrainingPhaseManager.execute", phase_cls, "execute"),
        ("__main__:InstantaleApp.elapse_days", app_cls, "elapse_days"),
    ))

    app = app_cls(World())
    app.player.gold = gold
    main.current_app = app
    app.process_choice(choice_cls(app, "訓練"), "訓練する")
    CLOCK.settle()
    return module, ctx, app, choice_cls


def texts_of(app):
    return [entry.get("text") for entry in app.buttons]


def press(app, text):
    for index, entry in enumerate(app.buttons):
        if entry.get("text") == text:
            app.on_button_press(index)
            CLOCK.settle()
            return
    raise AssertionError("no such button: {!r} in {}".format(text, texts_of(app)))


def specs_of(app):
    return [(entry["spec"].cls_name, list(entry["spec"].args))
            for entry in app.buttons if "spec" in entry]


# ================================================================ 既定
print("[既定] 素の設定では表示も所持金も日数も1つも動かない")
module, ctx, app, choice_cls = setup()
check("訓練のボタンは素のまま", texts_of(app)[0] == START_TEXT, texts_of(app))
gold_before = app.player.gold
press(app, START_TEXT)
check("代金は素の300G", gold_before - app.player.gold == 300, app.player.gold)
check("修行内容も素のまま",
      texts_of(app) == ["ただ鍛える(1年)", "基礎を積む(2年)", "技を磨く(2年)",
                        "新たな技を学ぶ(3年)"], texts_of(app))
check("spec と args には触らない",
      specs_of(app)[0] == ("TrainingPhaseManager", ["simple", 3, ""]), specs_of(app))
press(app, "新たな技を学ぶ(3年)")
check("暦も素のまま（3年 × 365日）", app.elapsed == [1095], app.elapsed)
check("卒業までいく", texts_of(app) == ["やった"], texts_of(app))
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 代金
print("[代金] 設定した額でゲームが引き落とす（引数の差し替え）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "TRAINING_PRICE", 5000))
check("ボタンも設定額で出る", texts_of(app)[0] == "訓練を受ける(5000G)", texts_of(app))
check("spec の args は素のまま（セーブに入る側は触らない）",
      specs_of(app)[0] == ("TrainingStartManager", [3, 300]), specs_of(app))
gold_before = app.player.gold
press(app, "訓練を受ける(5000G)")
check("代金は設定額", gold_before - app.player.gold == 5000, app.player.gold)
check("ログに1回の引き落としとして残る", "price: charged 5000" in read_log(), read_log())
check("修行はいつもどおり始まる", texts_of(app)[0] == "ただ鍛える(1年)", texts_of(app))
check("エラーなし", not ctx.errors, ctx.errors)

print("[代金] 0 にすればタダ")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "TRAINING_PRICE", 0))
gold_before = app.player.gold
press(app, texts_of(app)[0])
check("1Gも動かない", app.player.gold == gold_before, app.player.gold)

print("[代金] 引数から引かないビルドでは、差額をその場で戻して WARN を残す")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "TRAINING_PRICE", 50), charge_from_args=False)
gold_before = app.player.gold
press(app, texts_of(app)[0])
check("所持金は設定額しか減らない", gold_before - app.player.gold == 50, app.player.gold)
check("WARN が残る", "WARN price: the game charged its own 300" in read_log(),
      read_log())
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 拒否
print("[拒否] 手持ちが設定額に満たないと、押した時点で断って訓練を始めない")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "TRAINING_PRICE", 5000), gold=1000)
press(app, "訓練を受ける(5000G)")
check("所持金は動かない", app.player.gold == 1000, app.player.gold)
check("画面もそのまま", texts_of(app)[0] == "訓練を受ける(5000G)", texts_of(app))
check("断りの一言が出る",
      any("足りない" in text for text in app.texts), app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 年数
print("[年数] 1回の修行の年数を増やすと、ゲーム自身がその数から数える")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "COURSE_YEARS", 6))
press(app, START_TEXT)
check("残り年数は設定した6年",
      specs_of(app)[0] == ("TrainingPhaseManager", ["simple", 6, ""]), specs_of(app))
press(app, "新たな技を学ぶ(3年)")
check("3年使っても卒業しない（残り3年）",
      texts_of(app) == ["ただ鍛える(1年)", "基礎を積む(2年)", "技を磨く(2年)",
                        "新たな技を学ぶ(3年)"], texts_of(app))
press(app, "新たな技を学ぶ(3年)")
check("2段目で卒業", texts_of(app) == ["やった"], texts_of(app))
check("暦は3年ぶんが2回", app.elapsed == [1095, 1095], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 日数
print("[日数] 1年の長さを変えると、段の暦だけが縮む（活動の年数はゲームのまま）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "DAYS_PER_YEAR", 30))
press(app, START_TEXT)
check("修行内容の表示は既定では変わらない",
      texts_of(app)[0] == "ただ鍛える(1年)", texts_of(app))
press(app, "ただ鍛える(1年)")
check("1年の活動は30日", app.elapsed == [30], app.elapsed)
check("暦も30日だけ進む", app.day == 30, app.day)
press(app, "基礎を積む(2年)")
check("2年の活動は60日", app.elapsed == [30, 60], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "DAYS_PER_YEAR", 30))
press(app, START_TEXT)
press(app, "新たな技を学ぶ(3年)")
check("3年の活動は90日", app.elapsed == [90], app.elapsed)

print("[日数] 訓練の外の日数送りには触らない")
app.elapse_days(90)
check("素の90日がそのまま通る", app.elapsed == [90, 90], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 表示
print("[表示] `{days}` を入れたときだけ実日数が出る")
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "DAYS_PER_YEAR", 30),
                         setattr(m, "PHASE_BUTTON", "{name}({days}日)")))
press(app, START_TEXT)
check("実日数で並ぶ",
      texts_of(app) == ["ただ鍛える(30日)", "基礎を積む(60日)", "技を磨く(60日)",
                        "新たな技を学ぶ(90日)"], texts_of(app))
check("spec と args は素のまま",
      specs_of(app)[0] == ("TrainingPhaseManager", ["simple", 3, ""]), specs_of(app))
press(app, "ただ鍛える(30日)")
check("押せば実日数どおり進む", app.elapsed == [30], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ ワールド個別設定
print("[個別] 控えがある世界では一括設定を上書きし、消せば戻る（控えは同梱の tool.py が書く）")
module, ctx, app, choice_cls = setup()
world_file = os.path.join(ctx.state_dir, module.SETTINGS_DIRNAME,
                          app.world.name + ".json")
os.makedirs(os.path.dirname(world_file), exist_ok=True)
with io.open(world_file, "w", encoding="utf-8") as fh:
    fh.write('{"TRAINING_PRICE": 1200, "DAYS_PER_YEAR": "30"}')
app.process_choice(choice_cls(app, "訓練"), "訓練する")
CLOCK.settle()
check("代金は控えの値", texts_of(app)[0] == "訓練を受ける(1200G)", texts_of(app))
gold_before = app.player.gold
press(app, "訓練を受ける(1200G)")
check("引き落としも控えの値", gold_before - app.player.gold == 1200, app.player.gold)
press(app, "ただ鍛える(1年)")
check("型の違う値は一括設定のまま（1年は365日）", app.elapsed == [365], app.elapsed)
os.remove(world_file)
app.process_choice(choice_cls(app, "訓練"), "訓練する")
CLOCK.settle()
check("控えを消せば一括設定へ戻る", texts_of(app)[0] == START_TEXT, texts_of(app))
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 窓口
print("[窓口] ローダの durations に訓練の暦を置く（TECH.md §3.3.2）")
module, ctx, app, choice_cls = setup()
plan = durations.training(app)
check("素のままなら素の値（365日・3年）",
      (plan["days_per_year"], plan["course_years"]) == (365, 3), plan)
check("活動ごとの年数は素の値のまま",
      plan["activity_years"] == {"simple": 1, "fundamental": 2,
                                 "train_skill": 2, "learn_new_skill": 3}, plan)
check("置いたのはこの MOD", bool(plan["source"]), plan)
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "DAYS_PER_YEAR", 30),
                         setattr(m, "COURSE_YEARS", 10)))
plan = durations.training(app)
check("設定を変えれば窓口の答えも変わる",
      (plan["days_per_year"], plan["course_years"]) == (30, 10), plan)
check("1段の日数も窓口から引ける（3年 × 30日）",
      durations.training_days(app, "learn_new_skill") == 90,
      durations.training_days(app, "learn_new_skill"))
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ まとめ
print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
