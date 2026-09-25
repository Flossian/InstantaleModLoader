# -*- coding: utf-8 -*-
"""332_training_custom.py をゲーム抜きで通す。

    python tools/tests/test_training_custom.py

（開発中は `916_training_custom` / `test_wip_training_custom.py` だった。）

偽の app / PhaseSpec / DisplayTrainingChoice / TrainingStartManager /
TrainingPhaseManager を差し込み、次を確認する。

  既定   … 素の設定（300G・3年・1年365日）では表示も所持金も日数も1つも動かない
  代金   … 設定した額でゲームが引き落とす（引数の差し替え）。ボタンもその額で出る。
           代金を引数から取らないビルドでは、差額をその場で戻して WARN を残す
  拒否   … 手持ちが設定額に満たないと、押した時点で断って訓練を始めない
  年数   … 1回の修行の年数を増やすと、ゲーム自身が残り年数をその数から数える
  期間   … 段の実行中だけ `elapse_days` に渡る数が「ベース期間 × 倍率」になる。
           ゲームが数える年数（残り年数・並ぶ選択肢）はそのまま。窓の外の日数送りには触らない
  倍率   … 修行内容ごとに倍率を変えられる。知らない種類はゲームの言う年数が倍率の代わりに立つ
  表示   … ボタンは実際に進む期間で出る。素の設定（ベース1年）ではゲームの表示と同じ文字列
  文言   … 「あと3年間」「一年間、ひたすら鍛錬した」も実際の期間になる。
           AI の描写（文の途中の漢数字）と訓練の外の文言には触らない
  頼み文 … AI へ出ていく頼み文の年数（「残り訓練年数: 3年」など実測の4つ）も実際の期間に。
           人生ログ（「3年間の過酷な基礎訓練」）と土地との縁（「合計2年暮らしている」）は触らない
  再訓練 … 卒業した施設の2回目（実測では断られる）を、施設の `config["trained"]` を
           たたむことで通す。既定ではたたまない。卒業でまた立った印も次の回までにたたむ
  個別   … ワールド個別の控えがある世界では一括設定を上書きし、消せば戻る
  窓口   … ローダの `durations` に訓練の暦を置く（活動ごとの年数は素の値のまま）

偽ゲームの形は実測に合わせてある（`231_probe_training`。GAME.md §2.17）:
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

#: 1段の結果の文言が使う漢数字（実測は `一年間` / `二年間` / `二年`）。
KANJI = {1: "一", 2: "二", 3: "三"}

#: 1段の結果の言い回しは修行内容で違う（実測。全4種とも見た）。
#: **「間」が付かない形があり、年を言わない形もある。**
#:
#:   ただ鍛える     `一年間、ひたすら鍛錬した。0の経験値を得た。`
#:   基礎を積む     `二年を基礎能力の向上に費やした。筋力が上昇した。`
#:   技を磨く       `二年間、技を磨くことに費やした。`
#:   新たな技を学ぶ `新たな技の習得を模索した...` → `スキル「…」を習得した。`（年を言わない）
PHASE_TEXTS = {
    "simple": "{}年間、ひたすら鍛錬した。0の経験値を得た。",
    "fundamental": "{}年を基礎能力の向上に費やした。筋力が上昇した。",
    "train_skill": "{}年間、技を磨くことに費やした。",
    # ゲームは2文に分けて出すが、直す/直さないの判断は1文ずつなので1つにまとめてある。
    "learn_new_skill": "新たな技の習得を模索した...スキル「証明の刃」を習得した。",
}
PHASE_TEXT_DEFAULT = "{}年間、ひたすら鍛錬した。0の経験値を得た。"

#: AI へ渡る頼み文の実測（`output_data` の training_conversation_starter /
#: conversation_facilitator）。前の2つは直す側、後の2つは触らない側。
PROMPT_SAMPLES = (
    "【訓練所の情報】\n- 残り訓練年数: 3年\n- プロフィール: 剣の師範。",
    "今、プレイヤーが訓練施設での訓練を開始しました。"
    "これは費用と3年の時間を費やす代わりに経験値の獲得ができるシステムです。",
    "- 入学の希望を受けた場合: 3年間を費やし、経験値の獲得ができる。",
    "【訓練の記録】\n新たな技の会得に3年を費やした...スキル「証明の刃」を習得した。",
)
KEEP_SAMPLES = (
    "ミツバは指導者レオンのもとで3年間の過酷な基礎訓練を終えた。",
    "- この土地との縁:現在、この土地に合計2年暮らしている。新参だ。",
    "- この土地との縁:3年前まで、この土地に合計3年暮らしていた。",
)


class Facility:
    """訓練所。**断りの根は `config["trained"]`**（実測）。"""

    def __init__(self, name="ゼニスの風", trained=False):
        self.id = "mod:331_facility_investment:6-1"
        self.name = name
        # 一度も卒業していない施設は**キー自体を持たない**（実測）。
        self.config = {"level_of_detail": 0}
        if trained:
            self.config["trained"] = True


class World:
    def __init__(self):
        self.areas = {}
        self.name = "テスト世界"


class Player:
    def __init__(self, gold=100000):
        self.gold = gold
        self.age = 20
        #: 立ち位置。遊んでいる最中は施設の実体（GAME.md §2.7）。
        self.location = Facility()


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
        # 卒業した施設の2回目は断る（実測。代金も日数も動かない）。
        # 見ているのは施設に立つ印で、プレイヤー側の値ではない。
        # **キーの有無**で見る（`False` を入れても断られた。実機）。
        if "trained" in (getattr(self.app.player.location, "config", None) or {}):
            self.app.add_text("十分に学んだ。これ以上ここで得るものはないだろう。")
            self.app.buttons = [
                {"text": "訓練する",
                 "spec": PhaseSpec("DisplayTrainingChoice", ["訓練"])},
                {"text": "出る",
                 "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
            self.app.refresh_choice_buttons(reset_page=True)
            return None
        price = self.training_price if type(self).charge_from_args else GAME_PRICE
        if self.app.player.gold < price:
            self.app.add_text("金が足りない...")
            return None
        self.app.player.gold -= price
        self.training_start()
        return None

    def training_start(self):
        """支度（主の絵と背景）をしてから修行の選択肢を並べる。

        実測の `TrainingStartManager.training_start(self)` に当たる。
        **代金はここでは引かない**（引くのは `execute` の側）。
        """
        self.app.background = "training"          # 支度（実機では主の絵と背景）
        self.app.add_text("訓練を開始した。")
        # AI の描写（漢数字・文の途中）。**書き換えてはいけない**側の実測文。
        self.app.add_text("これからの三年間、私が責任を持ってあなたの研鑽を"
                          "お手伝いいたしましょう。")
        # 実機のビルドは、この1行だけ**差し替える前の回数**で書く
        # （VERIFICATION.md §3.64 の #13）。`stale_start_years` でその癖を再現する。
        said = getattr(self.app, "stale_start_years", None) or self.training_years
        self.app.add_text("あと{}年間。どうする？".format(said))
        self.app.buttons = phase_buttons(self.training_years, "")
        self.app.refresh_choice_buttons(reset_page=True)


class TrainingPhaseManager:
    """その1段。活動の年数 × 365 日が1回で進み、残り年数がその年数ぶん減る。"""

    def __init__(self, app, training_type, remaining_years, training_log):
        self.app = app
        self.training_type = training_type
        self.remaining_years = remaining_years
        self.training_log = training_log

    def execute(self, choice_text):
        years = YEARS_OF.get(self.training_type, 1)
        # 1段の結果（漢数字・行頭）。修行内容ごとの実測の形（`PHASE_TEXTS`）。
        self.app.add_text(
            PHASE_TEXTS.get(self.training_type, PHASE_TEXT_DEFAULT).format(
                KANJI[years]))
        self.app.elapse_days(years * GAME_DAYS_PER_YEAR)
        left = self.remaining_years - years
        if left <= 0:
            config = getattr(self.app.player.location, "config", None)
            if isinstance(config, dict):
                config["trained"] = True
            self.app.add_text("訓練を終えた。卒業だ...")
            self.app.buttons = [{"text": "やった",
                                 "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
        else:
            self.app.add_text("残り{}年間。どうする？".format(left))
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
        #: 日付は世界に1つ（`world.days_elapsed`）。ローダの `ui.game_day` が読む名前。
        self.days_elapsed = 0
        self.elapsed = []
        self.refreshes = 0
        #: 支度で切り替わる背景。`training_start()` が置く（実機ではここが空のままだった）。
        self.background = None

    def add_text(self, context):
        self.texts.append(context)

    def elapse_days(self, days):
        self.elapsed.append(days)
        self.days_elapsed += days
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

    # `llm.wrap_outgoing` はクラウド（APIキー）側の別名も包む。
    # **その別名が引ければ見張りスレッドは立たない**ので、偽の `llm_manager` を
    # 返してその場で当てさせる（`315_` の検査と同じ理由）。
    manager = types.SimpleNamespace(
        send_request=lambda *a, **k: None,
        send_request_with_no_structure=lambda *a, **k: None)

    def resolve(self, target):
        name = target.rpartition(":")[2].rsplit(".", 1)[-1]
        owner = self.manager if "llm_manager" in target else None
        return owner, name, getattr(owner, name, None)

    def superseded(self):
        return False              # テスト中に注入し直しは起きない

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


def setup(configure=None, charge_from_args=True, gold=100000, graduated=False):
    """mod を適用し、訓練所の選択肢が開いた状態の app を返す。

    `graduated=True` は卒業済みの施設（印が立っていて、実測では2回目を断る）。
    """
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
        ("__main__:InstantaleApp.add_text", app_cls, "add_text"),
    ))

    app = app_cls(World())
    app.player.gold = gold
    if graduated:
        app.player.location.config["trained"] = True
    else:
        app.player.location.config.pop("trained", None)
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
check("文言も1文字も変わらない",
      "あと3年間。どうする？" in app.texts
      and "新たな技の習得を模索した...スキル「証明の刃」を習得した。" in app.texts
      and module.reword("一年間、ひたすら鍛錬した。", "simple", phase=True) is None,
      app.texts)
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
print("[期間] ベース期間を変えると、段の暦とボタンの表示が一緒に変わる")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "1ヵ月"))
press(app, START_TEXT)
check("ボタンは実際に進む期間で並ぶ",
      texts_of(app) == ["ただ鍛える(1ヵ月)", "基礎を積む(2ヵ月)", "技を磨く(2ヵ月)",
                        "新たな技を学ぶ(3ヵ月)"], texts_of(app))
check("spec と args は素のまま（セーブに入る側は触らない）",
      specs_of(app)[0] == ("TrainingPhaseManager", ["simple", 3, ""]), specs_of(app))
press(app, "ただ鍛える(1ヵ月)")
check("1倍の修行は30日", app.elapsed == [30], app.elapsed)
check("暦も30日だけ進む", app.days_elapsed == 30, app.days_elapsed)
press(app, "基礎を積む(2ヵ月)")
check("2倍の修行は60日", app.elapsed == [30, 60], app.elapsed)
check("残り年数の減り方はゲームのまま（1年+2年で卒業）",
      texts_of(app) == ["やった"], texts_of(app))
check("エラーなし", not ctx.errors, ctx.errors)

module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "3ヵ月"))
press(app, START_TEXT)
check("3ヵ月ベースでは 3/6/6/9ヵ月",
      texts_of(app) == ["ただ鍛える(3ヵ月)", "基礎を積む(6ヵ月)", "技を磨く(6ヵ月)",
                        "新たな技を学ぶ(9ヵ月)"], texts_of(app))
press(app, "新たな技を学ぶ(9ヵ月)")
check("3倍の修行は270日", app.elapsed == [270], app.elapsed)

print("[期間] 訓練の外の日数送りには触らない")
app.elapse_days(90)
check("素の90日がそのまま通る", app.elapsed == [270, 90], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 倍率
print("[倍率] 修行内容ごとに倍率を変えられる（ゲームが数える年数は動かない）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "SIMPLE_FACTOR", 6),
                         setattr(m, "LEARN_NEW_SKILL_FACTOR", 1)))
press(app, START_TEXT)
check("倍率どおりの長さで並ぶ",
      texts_of(app) == ["ただ鍛える(6ヵ月)", "基礎を積む(2ヵ月)", "技を磨く(2ヵ月)",
                        "新たな技を学ぶ(1ヵ月)"], texts_of(app))
press(app, "新たな技を学ぶ(1ヵ月)")
check("1倍にした修行は30日", app.elapsed == [30], app.elapsed)
check("それでも3年ぶん使って卒業する（年数はゲームの勘定）",
      texts_of(app) == ["やった"], texts_of(app))
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "SIMPLE_FACTOR", 6)))
press(app, START_TEXT)
press(app, "ただ鍛える(6ヵ月)")
check("6倍にした修行は180日", app.elapsed == [180], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[倍率] 知らない修行内容では、ゲームの言う年数が倍率の代わりに立つ")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "1ヵ月"))
check("elapse_days に来た日数から（730日 = 2年 → 2倍）",
      module.phase_days("meditate", game_days=730) == (60, 2),
      module.phase_days("meditate", game_days=730))
check("ボタンの (N年) から（3年 → 3倍）",
      module.phase_days("meditate", years=3) == (90, 3),
      module.phase_days("meditate", years=3))
check("どちらも無ければ1倍", module.phase_days("meditate") == (30, 1),
      module.phase_days("meditate"))

# ================================================================ 文言
print("[文言] 「あと3年間」も実際の期間になる（AI の描写には触らない）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "1ヵ月"))
press(app, START_TEXT)
check("残りの量が期間で出る", "あと3ヵ月。どうする？" in app.texts, app.texts)
check("AI の描写は素のまま",
      "これからの三年間、私が責任を持ってあなたの研鑽をお手伝いいたしましょう。"
      in app.texts, app.texts)
press(app, "ただ鍛える(1ヵ月)")
check("1段の結果も期間で出る",
      "1ヵ月、ひたすら鍛錬した。0の経験値を得た。" in app.texts, app.texts)
check("次の残りも期間で出る", "残り2ヵ月。どうする？" in app.texts, app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[文言] 倍率が素の比と違うときは幅で出す（選び方で長さが変わるため）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "SIMPLE_FACTOR", 6)))
press(app, START_TEXT)
check("最短と最長が出る", "あと3ヵ月〜18ヵ月。どうする？" in app.texts, app.texts)
check("6倍の段の結果はその長さ", module.reword("一年間、ひたすら鍛錬した。",
                                              "simple", phase=True)
      == "6ヵ月、ひたすら鍛錬した。",
      module.reword("一年間、ひたすら鍛錬した。", "simple", phase=True))

print("[文言] 「間」の無い言い方も直す（実機 2026-09-16。基礎を積む）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "1ヵ月"))
press(app, START_TEXT)
press(app, "基礎を積む(2ヵ月)")
check("`二年を…費やした` が期間になる",
      "2ヵ月を基礎能力の向上に費やした。筋力が上昇した。" in app.texts, app.texts)
check("残りの量も期間になる", "残り1ヵ月。どうする？" in app.texts, app.texts)
# 文の途中の漢数字（AI の描写）はこの回も触らない。
check("描写の三年間はそのまま",
      "これからの三年間、私が責任を持ってあなたの研鑽をお手伝いいたしましょう。"
      in app.texts, app.texts)
check("別の意味の年には当てない",
      module.reword("三年後には一人前になれるだろう。", "simple", phase=True) is None)
check("エラーなし", not ctx.errors, ctx.errors)

print("[文言] 切れば素のゲームの言い方のまま")
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "PERIOD_WORDING", False)))
press(app, START_TEXT)
check("年のまま残る", "あと3年間。どうする？" in app.texts, app.texts)
press(app, "ただ鍛える(1ヵ月)")
check("暦は縮んでいる（文言だけがゲームのまま）", app.elapsed == [30], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[文言] 開始の1行が素の回数のままのビルドでも、設定した回数で読み替える")
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "COURSE_YEARS", 6)))
app.stale_start_years = 3                     # ゲームは差し替え前の3を書く
press(app, START_TEXT)
check("設定した6回ぶんで出る", "あと6ヵ月。どうする？" in app.texts, app.texts)
check("素の3で出さない", "あと3ヵ月。どうする？" not in app.texts, app.texts)
check("読み替えたことがログに残る",
      "the start line still said 3 year(s)" in read_log(), read_log()[-400:])
press(app, "ただ鍛える(1ヵ月)")
check("2行目以降はゲームの数のまま（5回ぶん）",
      "残り5ヵ月。どうする？" in app.texts, app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[文言] 回数を変えていなければ読み替えない")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "1ヵ月"))
app.stale_start_years = 3
press(app, START_TEXT)
check("素のままなら3回ぶん", "あと3ヵ月。どうする？" in app.texts, app.texts)
check("読み替えのログも出ない",
      "the start line still said" not in read_log()[-400:], read_log()[-400:])

print("[頼み文] 残りの量の錨も設定した回数で読み替える")
check("6回ぶんで書き換わる",
      module.reprompt("- 残り訓練年数: 3年", budget=6)
      == "- 残り訓練期間: 6ヵ月",
      module.reprompt("- 残り訓練年数: 3年", budget=6))
check("渡さなければ書かれている数のまま",
      module.reprompt("- 残り訓練年数: 3年")
      == "- 残り訓練期間: 3ヵ月",
      module.reprompt("- 残り訓練年数: 3年"))

print("[文言] ベース期間が素のままでも、回数を変えていれば開始の1行は読み替える")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "COURSE_YEARS", 6))
app.stale_start_years = 3
press(app, START_TEXT)
check("素の長さのまま数だけ直る", "あと6年間。どうする？" in app.texts, app.texts)
check("素の3では出さない", "あと3年間。どうする？" not in app.texts, app.texts)
check("頼み文の錨も数だけ直る",
      module.reprompt("- 残り訓練年数: 3年", budget=6) == "- 残り訓練期間: 6年",
      module.reprompt("- 残り訓練年数: 3年", budget=6))
check("渡さなければ素のまま触らない",
      module.reprompt("- 残り訓練年数: 3年") is None,
      module.reprompt("- 残り訓練年数: 3年"))
check("エラーなし", not ctx.errors, ctx.errors)

print("[文言] 訓練の外の文言には触らない")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "1ヵ月"))
app.add_text("あと3年間。どうする？")
check("窓の外は素通し", app.texts[-1] == "あと3年間。どうする？", app.texts[-1])

# ================================================================ 再訓練
print("[再訓練] 切れば印をたたまない（素のゲームのまま断られる）")
module, ctx, app, choice_cls = setup(
    graduated=True, configure=lambda m: setattr(m, "ALLOW_RETRAIN", False))
gold_before = app.player.gold
press(app, START_TEXT)
check("ゲームの断りがそのまま出る",
      "十分に学んだ。これ以上ここで得るものはないだろう。" in app.texts, app.texts)
check("印は立ったまま", app.player.location.config.get("trained") is True,
      app.player.location.config)
check("修行の選択肢は並ばない",
      not [t for t in texts_of(app) if "鍛える" in (t or "")], texts_of(app))
check("所持金も動かない", app.player.gold == gold_before, app.player.gold)
check("エラーなし", not ctx.errors, ctx.errors)

print("[再訓練] 既定（ON）では印をたたんで、ゲームがいつもどおり進める")
module, ctx, app, choice_cls = setup(graduated=True)
check("既定は ON（この MOD を入れると受けられる。他の設定と違い素の値ではない）",
      module.ALLOW_RETRAIN is True, module.ALLOW_RETRAIN)
gold_before = app.player.gold
press(app, START_TEXT)
check("印がキーごと落ちる", "trained" not in app.player.location.config,
      app.player.location.config)
check("他の項目は残る", app.player.location.config.get("level_of_detail") == 0,
      app.player.location.config)
check("断りの文言は出ない",
      not any("十分に学んだ" in t for t in app.texts), app.texts)
check("支度もゲームがする（背景が置かれる）", app.background == "training",
      app.background)
check("修行の選択肢はゲームが組む",
      texts_of(app) == ["ただ鍛える(1年)", "基礎を積む(2年)", "技を磨く(2年)",
                        "新たな技を学ぶ(3年)"], texts_of(app))
check("代金もゲームが引く", gold_before - app.player.gold == 300, app.player.gold)
check("ログに残る", "dropped the 'trained' mark" in read_log(), read_log())
press(app, "ただ鍛える(1年)")
check("押せば暦が進む", app.elapsed == [365], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[再訓練] 卒業でまた立った印も、そのまま立たせておかない")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "ALLOW_RETRAIN", True))
press(app, START_TEXT)
press(app, "新たな技を学ぶ(3年)")
check("卒業した", texts_of(app) == ["やった"], texts_of(app))
check("印は残っていない", "trained" not in app.player.location.config,
      app.player.location.config)

print("[再訓練] 期間と代金の設定はそのまま効く")
module, ctx, app, choice_cls = setup(
    graduated=True, configure=lambda m: (setattr(m, "ALLOW_RETRAIN", True),
                                         setattr(m, "BASE_PERIOD", "1ヵ月"),
                                         setattr(m, "TRAINING_PRICE", 1000)))
gold_before = app.player.gold
press(app, "訓練を受ける(1000G)")
check("選択肢は設定した期間で並ぶ",
      texts_of(app) == ["ただ鍛える(1ヵ月)", "基礎を積む(2ヵ月)", "技を磨く(2ヵ月)",
                        "新たな技を学ぶ(3ヵ月)"], texts_of(app))
check("代金も設定額", gold_before - app.player.gold == 1000, app.player.gold)
press(app, "新たな技を学ぶ(3ヵ月)")
check("暦も設定どおり", app.elapsed == [90], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[再訓練] False が入っていてもキーごと落とす（実機で踏んだ形）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "ALLOW_RETRAIN", True))
app.player.location.config["trained"] = False
press(app, START_TEXT)
check("キーが消える", "trained" not in app.player.location.config,
      app.player.location.config)
check("訓練も始まる", texts_of(app)[0] == "ただ鍛える(1年)", texts_of(app))

print("[再訓練] 印を持たない施設では何もしない")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "ALLOW_RETRAIN", True))
press(app, START_TEXT)
check("ゲームの流れは変わらない", texts_of(app)[0] == "ただ鍛える(1年)", texts_of(app))
check("ログにも何も足さない", "dropped the" not in read_log(), read_log())
check("エラーなし", not ctx.errors, ctx.errors)

print("[再訓練] 施設が読めないときは黙って諦めず WARN を残す")
module, ctx, app, choice_cls = setup(
    graduated=True, configure=lambda m: setattr(m, "ALLOW_RETRAIN", True))
app.player.location = None
press(app, START_TEXT)
check("WARN が残る", "cannot read the config" in read_log(), read_log())
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ 頼み文
print("[頼み文] AI へ出ていく年数も実際の期間になる（過去の記録には触らない）")
module, ctx, app, choice_cls = setup(
    configure=lambda m: setattr(m, "BASE_PERIOD", "1ヵ月"))
said = [module.reprompt(text) for text in PROMPT_SAMPLES[:3]]
check("残り訓練年数が期間になる",
      said[0] == "【訓練所の情報】\n- 残り訓練期間: 3ヵ月\n- プロフィール: 剣の師範。",
      said[0])
check("費用と3年の時間も直る", said[1] and "費用と3ヵ月の時間を費やす" in said[1], said[1])
check("会話の勧誘の説明も直る",
      said[2] == "- 入学の希望を受けた場合: 3ヵ月を費やし、経験値の獲得ができる。", said[2])
record = module.reprompt(PROMPT_SAMPLES[3], "learn_new_skill", phase=True)
check("訓練の記録は段の長さで直る（段の中だけ）",
      record and "新たな技の会得に3ヵ月を費やした" in record, record)
check("段の外では訓練の記録に触らない",
      module.reprompt(PROMPT_SAMPLES[3]) is None,
      module.reprompt(PROMPT_SAMPLES[3]))
for text in KEEP_SAMPLES:
    check("過去の記録は触らない: {}...".format(text[:16]),
          module.reprompt(text, "simple", phase=True) is None,
          module.reprompt(text, "simple", phase=True))

print("[頼み文] 素の設定では1文字も変えない・切れば何もしない")
module, ctx, app, choice_cls = setup()
check("素の設定では触らない",
      all(module.reprompt(text) is None for text in PROMPT_SAMPLES), PROMPT_SAMPLES)
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "LLM_PERIOD_WORDING", False)))
check("切れば何もしない", module.reprompt(PROMPT_SAMPLES[0]) is None,
      module.reprompt(PROMPT_SAMPLES[0]))

# ================================================================ 表示
print("[表示] テンプレートを変えれば日数でも倍率でも出せる")
module, ctx, app, choice_cls = setup(
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "PHASE_BUTTON", "{name}({days}日)")))
press(app, START_TEXT)
check("実日数で並ぶ",
      texts_of(app) == ["ただ鍛える(30日)", "基礎を積む(60日)", "技を磨く(60日)",
                        "新たな技を学ぶ(90日)"], texts_of(app))
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
    fh.write('{"TRAINING_PRICE": 1200, "BASE_PERIOD": "1ヵ月", '
             '"SIMPLE_FACTOR": "6"}')
app.process_choice(choice_cls(app, "訓練"), "訓練する")
CLOCK.settle()
check("代金は控えの値", texts_of(app)[0] == "訓練を受ける(1200G)", texts_of(app))
gold_before = app.player.gold
press(app, "訓練を受ける(1200G)")
check("引き落としも控えの値", gold_before - app.player.gold == 1200, app.player.gold)
check("ベース期間も控えの値", texts_of(app)[0] == "ただ鍛える(1ヵ月)", texts_of(app))
press(app, "ただ鍛える(1ヵ月)")
check("型の違う倍率は一括設定のまま（6倍にはならず1倍の30日）",
      app.elapsed == [30], app.elapsed)
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
    configure=lambda m: (setattr(m, "BASE_PERIOD", "1ヵ月"),
                         setattr(m, "COURSE_YEARS", 10),
                         setattr(m, "LEARN_NEW_SKILL_FACTOR", 4)))
plan = durations.training(app)
check("ベース期間が days_per_year、倍率が activity_years に入る",
      (plan["days_per_year"], plan["course_years"],
       plan["activity_years"]["learn_new_skill"]) == (30, 10, 4), plan)
check("1段の日数も窓口から引ける（4倍 × 30日）",
      durations.training_days(app, "learn_new_skill") == 120,
      durations.training_days(app, "learn_new_skill"))
check("エラーなし", not ctx.errors, ctx.errors)

# ================================================================ まとめ
print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
