# -*- coding: utf-8 -*-
"""315_vacation_custom.py をゲーム抜きで通す。

    python tools/tests/test_vacation_custom.py

偽の app / PhaseSpec / DisplayVacationChoice / VacationStartManager /
VacationRestManager / VacationEndManager を差し込む。
**偽ゲームは実機の実測に合わせてある**（`out/vacation.log`。
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
#: 実測の部屋。
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
        if getattr(self.app, "keyword_text", False):
            # キーワード引数で出すビルド（315 版9 は受け取った形のまま文言を差し替える）。
            self.app.add_text(context="{}ヵ月泊まることにした。".format(self.months))
        else:
            self.app.add_text("{}ヵ月泊まることにした。".format(self.months))
        self.app.change_background_image_to_inn_room(self.quality)
        if type(self).elapses:
            self.app.elapse_days(int(str(self.months)) * 30)
        self.write_keeper_memory()
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

    def write_keeper_memory(self):
        """宿の主の記録（実物の形）。終わりは今日、始まりは月数×30日前で、日数の切り詰めを知らない。"""
        location = getattr(self.app.player, "location", None)
        if location is None:
            return
        keeper = self.app.world.characters[location.owner]
        names = {"kennel": "犬小屋", "bunk": "簡易寝台", "private_room": "個室", "luxury_suite": "高級個室"}
        span = int(str(self.months)) * 30
        end = self.app.world.days_elapsed
        keeper.life_log.append({
            "day_start": end - span,
            "day_end": end,
            "summarized_count": 0,
            "content": "〈生活の記憶〉{}を{}に{}ヵ月間宿泊させた。\n".format(
                "エリス", names[str(self.quality)], self.months)})


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
        logs = getattr(self.app.player, "life_log", None)
        if logs is not None:
            # 実物の形。終わりは今日、始まりは月数×30日前で、日数の切り詰めを知らない。
            today = self.app.world.days_elapsed
            logs.append({"day_start": today - int(str(self.months)) * 30, "day_end": today,
                         "summarized_count": 0,
                         "content": "〈生活の記憶〉エリスはしばらく休暇をとり、回復につとめた。\n"})
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
        world = getattr(self, "world", None)
        if isinstance(getattr(world, "days_elapsed", None), int):
            world.days_elapsed += days
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
    _seq = 0

    def __init__(self, out_dir):
        self.out_dir = out_dir
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

    # ログは本物の `ctx.logger` をそのまま借りる（`314_` のテストと同じ理由）。
    _mod = None

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        """同じ対象に2枚当たったら層にする（本物は後から当てたほうが外側。TECH.md §3.3）。

        `325_` は日数送りに「後処理だけ」の包みを持ち、日数そのものは
        ローダの関所が渡す。1枚しか覚えないと、後から当てたほうだけが残る。
        """
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
    global REST_CLS
    REST_CLS = rest_cls
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

print("[ラベル] 部屋のボタンの形を変えても自己検証は通る")
module, ctx, app, choice_cls, start_cls = setup(
    configure=lambda m: setattr(m, "ROOM_BUTTON", "{name}【{price}G】"))
check("VERIFY FAILED が出ない",
      not [msg for level, msg in ctx.logs if level == "ERROR"], ctx.logs)

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
keyword_choice = choice_cls(app=app, period_months=GAME_MONTHS)
check("キーワード引数で作られても月数を 6 に差し替える",
      keyword_choice.period_months == 6, keyword_choice.period_months)

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
module_kw, ctx_kw, app_kw, _choice_kw, _start_kw = setup(configure=week1)
app_kw.keyword_text = True
press(app_kw, "宿泊する(1週間)")
press(app_kw, "個室(100G)")
check("キーワード引数の文言も実際の期間に直す",
      "1週間泊まることにした。" in app_kw.texts
      and not any("1ヵ月泊まる" in t for t in app_kw.texts), app_kw.texts)
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

# ================================================================ 宿の主
#: ゲームの指示文（exe の定数。GAME.md §2.17）。
INN_INSTRUCTION = ("  - 宿泊をしたいと伝えられた場合: casual_responseとして返答を記述。"
                   "3ヵ月単位の宿泊が可能であり、前払いで簡易寝台10G、個室100G、高級個室1000Gが必要な事を説明する。"
                   "本当にやるならこの会話からではなく手続きをとる必要があること、"
                   "犬小屋なら宿泊無料であることも状況に応じて伝えていいが、伝えなくてもいい。"
                   "絶対に値引きや期間の変更はしないし聞かれるまで言及もしない。")
TALK = "scripts.llm.llm_manager:conversation_facilitator"
TALK_AFTER = "scripts.llm.llm_manager:conversation_facilitator_after_retrieval"


def keeper_talk(ctx, instruction, target=TALK, keyword=False):
    """宿の主との会話の LLM を呼び、届いた指示文を返す。"""
    seen = []

    def orig(*args, **kwargs):
        seen.append(kwargs["response_prompt_instruction"] if "response_prompt_instruction" in kwargs
                    else args[10 if target == TALK else 9])
        return "answer"

    if keyword:
        result = ctx.hooks[target](orig, "messages", response_prompt_instruction=instruction)
    else:
        args = ["a{}".format(i) for i in range(10 if target == TALK else 9)] + [instruction, "last"]
        result = ctx.hooks[target](orig, *args)
    check("元の関数の答えを返す", result == "answer", result)
    return seen[0]


def rooms_renamed(module):
    module.STAY_LENGTH = "1週間"
    module.AGE_SCALING = False
    module.SIMPLE_NAME = "相部屋"
    module.SIMPLE_PRICE = 20
    module.KENNEL_NAME = "馬房の隅"
    module.KENNEL_PRICE = 5


print("[宿の主] 素の設定なら指示文は変わらない")
module, ctx, app, choice_cls, start_cls = setup()
check("素の設定の期間は年齢の月数のまま", keeper_talk(ctx, INN_INSTRUCTION) == INN_INSTRUCTION,
      keeper_talk(ctx, INN_INSTRUCTION))
check("書き換えの行が出ない", "keeper:" not in read_log(), read_log())

print("[宿の主] 1週間の設定で「1週間単位」と言う（報告の不具合）")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
got = keeper_talk(ctx, INN_INSTRUCTION)
check("1週間単位", "1週間単位の宿泊が可能" in got, got)
check("3ヵ月は残らない", "3ヵ月" not in got, got)
check("値段は素のまま", "前払いで簡易寝台10G、個室100G、高級個室1000Gが必要な事" in got, got)
check("前後の文はそのまま", got.endswith("絶対に値引きや期間の変更はしないし聞かれるまで言及もしない。")
      and got.startswith("  - 宿泊をしたいと伝えられた場合"), got)
got = keeper_talk(ctx, INN_INSTRUCTION, target=TALK_AFTER)
check("_after_retrieval も同じ（位置が違う）", "1週間単位の宿泊が可能" in got, got)
got = keeper_talk(ctx, INN_INSTRUCTION, keyword=True)
check("キーワード引数で来ても差し替える", "1週間単位の宿泊が可能" in got, got)
other = "  - 雑談や質問の場合: casual_responseとして返答を記述。"
check("宿の主の文でなければ触らない", keeper_talk(ctx, other) == other)
check("エラーなし", not ctx.errors, ctx.errors)

print("[宿の主] 年齢の加算まで揃う（30代の1週間は2週間）")
module, ctx, app, choice_cls, start_cls = setup(configure=week1, age=31)
got = keeper_talk(ctx, INN_INSTRUCTION)
check("2週間単位", "2週間単位の宿泊が可能" in got, got)

print("[宿の主] 月単位でもボタンと同じ期間を言う")
module, ctx, app, choice_cls, start_cls = setup(configure=month6)
got = keeper_talk(ctx, INN_INSTRUCTION)
check("6ヵ月単位", "6ヵ月単位の宿泊が可能" in got, got)

print("[宿の主] 部屋の名前と宿代を変えると指示文も揃う")
module, ctx, app, choice_cls, start_cls = setup(configure=rooms_renamed)
got = keeper_talk(ctx, INN_INSTRUCTION)
check("相部屋20G", "前払いで相部屋20G、個室100G、高級個室1000Gが必要な事" in got, got)
check("有料の犬小屋は無料と言わない", "馬房の隅なら5Gで泊まれることも" in got and "宿泊無料" not in got, got)

print("[宿の主] 通貨の表記が先に直っていても当たる（130 は tr の段で直す）")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
yen = INN_INSTRUCTION.replace("10G", "10円").replace("100G", "100円").replace("1000G", "1000円")
got = keeper_talk(ctx, yen)
check("1週間単位", "1週間単位の宿泊が可能" in got, got)

print("[宿の主] 文が変わったビルドでは触らず、ログに残す")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
module_state = getattr(sys, module.STATE_STORE_ATTR)
module_state.pop("terms_missed", None)
changed = "  - 宿泊をしたいと伝えられた場合: 3ヵ月ごとの料金を説明する。"
check("そのまま渡す", keeper_talk(ctx, changed) == changed)
check("当たらなかったことが残る", "WARN keeper: the inn terms did not match" in read_log(), read_log())

print("[宿の主] 設定を切れば指示文に触らない")
module, ctx, app, choice_cls, start_cls = setup(configure=week1_no_llm)
check("フックを置かない", TALK not in ctx.hooks and TALK_AFTER not in ctx.hooks)


def inn_with_keeper(app):
    """主のいる宿に立たせる。戻り値は主。"""
    keeper = type("Keeper", (), {})()
    keeper.life_log = []
    world = type("World", (), {})()
    world.characters = {"9": keeper}
    world.days_elapsed = 100
    app.world = world
    location = type("Facility", (), {})()
    location.owner = "9"
    app.player.location = location
    return keeper


print("[記録] 1週間の宿泊は「1週間」「7日」と残る（報告の不具合の2つ目）")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
keeper = inn_with_keeper(app)
old = {"day_start": 10, "day_end": 40, "summarized_count": 0,
       "content": "〈生活の記憶〉エリスを個室に1ヵ月間宿泊させた。\n"}
keeper.life_log.append(dict(old))
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
entry = keeper.life_log[-1]
check("期間が1週間", entry["content"] == "〈生活の記憶〉エリスを個室に1週間宿泊させた。\n", entry)
check("範囲が7日", (entry["day_start"], entry["day_end"]) == (100, 107), entry)
check("前からある記録には触らない", keeper.life_log[0] == old, keeper.life_log[0])
press(app, "まだ宿泊する")
entry = keeper.life_log[-1]
check("連泊の2回目も直る", "1週間宿泊させた" in entry["content"]
      and entry["day_end"] - entry["day_start"] == 7, entry)
check("エラーなし", not ctx.errors, ctx.errors)

print("[記録] 範囲が今日で終わらない記録は、範囲を触らずログに残す")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
keeper = inn_with_keeper(app)
real_write = start_cls.write_keeper_memory


def shifted(self):
    real_write(self)
    self.app.world.characters["9"].life_log[-1]["day_end"] += 1


start_cls.write_keeper_memory = shifted
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
entry = keeper.life_log[-1]
check("範囲はゲームのまま", (entry["day_start"], entry["day_end"]) == (77, 108), entry)
check("文は直る", "1週間宿泊させた" in entry["content"], entry)
check("ログに残る", "WARN memory: the range" in read_log(), read_log())

print("[記録] 月単位は部屋の名前だけ直し、範囲はゲームのまま")
module, ctx, app, choice_cls, start_cls = setup(
    configure=lambda m: (month6(m), setattr(m, "SIMPLE_NAME", "相部屋")))
keeper = inn_with_keeper(app)
press(app, "宿泊する(6ヵ月)")
press(app, "相部屋(10G)")
entry = keeper.life_log[-1]
check("相部屋に6ヵ月間", entry["content"] == "〈生活の記憶〉エリスを相部屋に6ヵ月間宿泊させた。\n", entry)
check("範囲は180日", entry["day_end"] - entry["day_start"] == 180, entry)

print("[記録] 素の設定なら何も変わらない")
module, ctx, app, choice_cls, start_cls = setup()
keeper = inn_with_keeper(app)
press(app, STAY_TEXT)
press(app, "個室(100G)")
entry = keeper.life_log[-1]
check("文はゲームのまま", entry["content"] == "〈生活の記憶〉エリスを個室に3ヵ月間宿泊させた。\n", entry)
check("範囲もゲームのまま", entry["day_end"] - entry["day_start"] == 90, entry)

print("[記録] 週単位なら主人公の活動の記録の範囲も実際の日数になる")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
keeper = inn_with_keeper(app)
oneday = {"day_start": 90, "day_end": 90, "summarized_count": 0, "content": "〈生活の記憶〉前からある記録。\n"}
old_long = {"day_start": 10, "day_end": 40, "summarized_count": 0, "content": "〈生活の記憶〉前からある長い記録。\n"}
app.player.life_log = [dict(oneday), dict(old_long)]
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
press(app, "休養をとる")
entry = app.player.life_log[-1]
check("範囲が7日", (entry["day_start"], entry["day_end"]) == (100, 107), entry)
check("文はゲームのまま", entry["content"] == "〈生活の記憶〉エリスはしばらく休暇をとり、回復につとめた。\n", entry)
check("前からある記録には触らない", app.player.life_log[:2] == [oneday, old_long], app.player.life_log[:2])
check("ログに残る", "memory: player" in read_log(), read_log())
check("エラーなし", not ctx.errors, ctx.errors)

print("[記録] 宿泊の間に増えた短い記録は伸ばさない")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
keeper = inn_with_keeper(app)
app.player.life_log = []
real_exec = REST_CLS.execute


def rest_with_short(self, *args, **kwargs):
    self.app.player.life_log.append({"day_start": 107, "day_end": 107, "summarized_count": 0,
                                     "content": "〈生活の記憶〉1日の出来事。\n"})
    return real_exec(self, *args, **kwargs)


REST_CLS.execute = rest_with_short
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
press(app, "休養をとる")
short = app.player.life_log[0]
check("1日の記録はそのまま", (short["day_start"], short["day_end"]) == (107, 107), short)
long_entry = app.player.life_log[-1]
check("長い記録だけ縮む", (long_entry["day_start"], long_entry["day_end"]) == (100, 107), long_entry)

print("[記録] 月単位なら主人公の記録の範囲はゲームのまま")
module, ctx, app, choice_cls, start_cls = setup(configure=month6)
keeper = inn_with_keeper(app)
app.player.life_log = []
press(app, "宿泊する(6ヵ月)")
press(app, "個室(100G)")
press(app, "休養をとる")
entry = app.player.life_log[-1]
check("範囲は180日", entry["day_end"] - entry["day_start"] == 180, entry)

print("[記録] 主のいない建物では何もしない")
module, ctx, app, choice_cls, start_cls = setup(configure=week1)
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
check("エラーなし", not ctx.errors, ctx.errors)

print("[記録] 設定を切れば直さない")
module, ctx, app, choice_cls, start_cls = setup(configure=week1_no_llm)
keeper = inn_with_keeper(app)
press(app, "宿泊する(1週間)")
press(app, "個室(100G)")
check("ゲームの文のまま", "1ヵ月間宿泊させた" in keeper.life_log[-1]["content"], keeper.life_log[-1])

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

print("[語彙] 料金だけ変わったビルドで設定が既定のままなら、ゲームの額のまま")
module, ctx, app, choice_cls, start_cls = setup()
choice_cls.rooms = (ROOMS[0], ("簡易寝台(20G)", "bunk", 20)) + ROOMS[2:]
GAME_PRICES["bunk"] = 20
press(app, STAY_TEXT)
check("ラベルはゲームの 20G のまま（既定の 10G へ書き換えない）",
      texts_of(app)[1] == "簡易寝台(20G)", texts_of(app))
press(app, "簡易寝台(20G)")
check("ゲームの 20G が引かれる（前払いで差を埋めない）",
      app.player.gold == 4980, app.player.gold)
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

# ================================================================ 宿代の段（ローダ）
print("[段] 宿代の段（prices.adjust_inn）が設定額の上に掛かる")
prices = ml.prices
module, ctx, app, choice_cls, start_cls = setup(configure=luxury300)
prices.adjust_inn("test_layer", lambda _app, quality, price: price * 1.5
                  if quality in ("luxury_suite", "private_room") else None)
press(app, STAY_TEXT)
check("設定額 300 の 1.5 倍で表示", texts_of(app)[3] == "王侯の間(450G)", texts_of(app))
check("素の部屋にも段が掛かる（個室 100 → 150）", texts_of(app)[2] == "個室(150G)",
      texts_of(app))
check("段の掛からない部屋はゲームのまま", texts_of(app)[1] == "簡易寝台(10G)", texts_of(app))
check("窓口の答えも段を通した額", prices.inn_room(app, "luxury_suite") == 450
      and prices.inn_room_base(app, "luxury_suite") == 300,
      (prices.inn_room(app, "luxury_suite"), prices.inn_room_base(app, "luxury_suite")))
press(app, "個室(150G)")
check("1回でちょうど段を通した額（150）が引かれる", app.player.gold == 4850, app.player.gold)
check("支払いの瞬間から差し引き後の値", app.gold_after_payment == 4850, app.gold_after_payment)
prices.forget_inn_prices("test_layer")
check("段を外すと答えは設定額へ戻る", prices.inn_room(app, "luxury_suite") == 300,
      prices.inn_room(app, "luxury_suite"))

print("[段] 段で上がった額に手持ちが足りなければ断る")
module, ctx, app, choice_cls, start_cls = setup()
prices.adjust_inn("test_layer", lambda _app, quality, price: price * 3)
app.player.gold = 250
press(app, STAY_TEXT)
check("素の個室が 300 で表示", texts_of(app)[2] == "個室(300G)", texts_of(app))
press(app, "個室(300G)")
check("宿泊を起こさない", not app.stays, app.stays)
check("断りの一言が出る", any("足りない" in t for t in app.texts), app.texts)
check("所持金はそのまま", app.player.gold == 250, app.player.gold)
prices.forget_inn_prices("test_layer")

print("[段] ロードで戻った段つきの文言を、起動し直した後に素の額と取り違えない")
# 部屋選びの画面で保存したセーブには段を掛けた文言（個室(125G)）が残る。
# 起動し直して段が外れた（設定を変えた・MOD を切った）状態で読むと、ゲームの書式のままなので
# 素の額 125 と控え、ゲームが引く 100 との差 25 を前払いで足していた（実機で 75 しか減らなかった）。
module, ctx, app, choice_cls, start_cls = setup()
prices.adjust_inn("test_layer", lambda _app, quality, price: price * 1.25
                  if quality == "private_room" else None)
press(app, STAY_TEXT)
check("段の掛かった文言で保存される", texts_of(app)[2] == "個室(125G)", texts_of(app))
saved = [dict(entry) for entry in app.buttons]
prices.forget_inn_prices("test_layer")
inn_store = prices._inn_store()
inn_store["observed"].clear()            # 起動し直した（控えはプロセスの中だけ）
inn_store["ours"].clear()
loaded_world = type("World", (), {})()   # 世界の読み込み（choices が見分ける）
loaded_world.characters = {}             # 実機と同じく、読み込みの途中の組み直しでは人物が 0 人
app.world = loaded_world
app.buttons = saved
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
loaded_world.characters = {"1": object()}   # 人物が揃った後の組み直し（実機ではここで書き直して控えていた）
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("段つきの額を素の額として控えない", "private_room" not in inn_store["observed"],
      dict(inn_store["observed"]))
check("文言は素の額へ戻る", texts_of(app)[2] == "個室(100G)", texts_of(app))
gold = app.player.gold
press(app, "個室(100G)")
check("素の額 100 がちょうど引かれる", app.player.gold == gold - 100, (gold, app.player.gold))
check("人物が揃った後の組み直しで印は下りる", ml.choices.from_save() is False
      and not ml.choices._store().get("save_pending"), ml.choices._store().get("save_pending"))

for after, label in ((200, "設定を別の額に変えた"), (100, "設定を素の額へ戻した")):
    print("[部屋] 部屋選びの画面で保存し、{}後に起動し直して読んでも、保存時の額を素の額と取り違えない".format(label))
    # 315 だけの経路。部屋の名前は変えず宿代だけ 150 にしていると、保存した文言（個室(150G)）は
    # ゲームの書式のままで、起動し直した後の設定（200 や素の 100）で 315 が書く文言とも違う。
    # v1.15.0 の 315 はこれを素の額 150 と控え、ゲームが引く 100 との差を前払いで足していた。
    module, ctx, app, choice_cls, start_cls = setup(configure=lambda m: setattr(m, "PRIVATE_PRICE", 150))
    press(app, STAY_TEXT)
    check("315 の額の文言で保存される", texts_of(app)[2] == "個室(150G)", texts_of(app))
    saved = [dict(entry) for entry in app.buttons]
    inn_store = prices._inn_store()
    inn_store["observed"].clear()            # 起動し直した（控えはプロセスの中だけ）
    inn_store["ours"].clear()
    module.PRIVATE_PRICE = after             # 起動し直す間に設定を変えた
    loaded_world = type("World", (), {})()
    loaded_world.characters = {}
    app.world = loaded_world
    app.buttons = saved
    app.refresh_choice_buttons(reset_page=True)
    CLOCK.settle()
    loaded_world.characters = {"1": object()}
    app.refresh_choice_buttons(reset_page=True)
    CLOCK.settle()
    check("保存時の額を素の額として控えない", inn_store["observed"].get("private_room") in (None, 100),
          dict(inn_store["observed"]))
    shown = "個室({}G)".format(after)
    check("文言は今の設定の額", texts_of(app)[2] == shown, texts_of(app))
    gold = app.player.gold
    press(app, shown)
    check("今の設定の額 {} がちょうど引かれる".format(after), app.player.gold == gold - after,
          (gold, app.player.gold))

# ================================================================ まとめ
print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
