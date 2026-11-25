# -*- coding: utf-8 -*-
"""925_labor_overhaul をゲーム抜きで通す。

    python tools/tests/test_wip_labor_overhaul.py

偽の app / Player / Area / Facility / PhaseSpec / HUD / Clock を差し込み、次を確認する。

  規則   … 賃金の倍率（能力値・勤続・街の出来事・危険度）、出来事の表の形、危険の確率、控えの並び
  入口   … 役場と裏の事務所で、ゲームの労働のボタンが同じ場所で差し替わる。ほかの施設では触らない
  役場   … 街の施設ごとの仕事が並ぶ。契約の長さを選び、働くたびに暦と所持金が進み、満了で割増・好感度・勤続が付く
  出来事 … 働く合間に出来事が出て、選択肢の判定の結果で賃金と勤続が動く
  途中   … 途中でやめると勤続が減り、その雇い主が一覧から消える
  裏     … 危険度で賃金が 2〜4 倍。取り締まり・仕返し・裏切りの結果。危険度3は勤続が要る
  連動   … 良い出来事で賃金が変わり、困り事には人手の仕事が出て、務め終えると窓口へ1段の頼みが出る
  続き   … 契約の途中で注入し直しても、働く画面に戻れる
  設定   … mod.json の既定値と入口の定数が一致する
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "labor_overhaul"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を番号を除いた名前で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pick *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_labor_overhaul")
MOD_NAME = "labor_overhaul_mod"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
OFFICE_TYPE = "administrative_office"
UNDERWORLD_TYPE = "underworld_office"
LABOR_TEXT = "労働の募集をみる"
ILLEGAL_TEXT = "闇労働の募集をみる"
EXIT_TEXT = "出る"


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class _GamePhase:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        self.app.game_phases.append(type(self).__name__)
        return None


class JustSetButtonToNormalPhase(_GamePhase):
    pass


class MovePhaseManager(_GamePhase):
    pass


class DisplayLongTermLaborChoice(_GamePhase):
    pass


class DisplayIllegalLaborChoice(_GamePhase):
    pass


class Facility:
    def __init__(self, facility_id, name, facility_type, owner=None):
        self.id = facility_id
        self.name = name
        self.facility_type = facility_type
        self.owner = owner


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, name, facilities):
        self.id = area_id
        self.name = name
        self.nodes = {"0": Node(facilities)}


class Character:
    def __init__(self, name, affinity=10):
        self.name = name
        self.relationship = {"player": {"affinity": affinity}}


class World:
    def __init__(self, areas, characters):
        self.areas = areas
        self.name = "労働の検査の世界"
        self.characters = characters
        self.days_elapsed = 1000


class Player:
    def __init__(self, area, location, gold, score):
        self.name = "検査の主人公"
        self.current_area = area
        self.location = location
        self.area_history = {area.id: {"residency": {}, "achievements": [], "lawfulness": 10}}
        self.gold = gold
        self.life_log = []
        self.ability_scores = {name: score for name in (
            "strength", "constitution", "dexterity", "intelligence", "wisdom", "charisma")}


class InstantaleApp:
    def __init__(self, world, player):
        self.world = world
        self.player = player
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.game_phases = []
        self.saves = 0
        self.ui_updates = 0
        self.is_button_enabled = True
        self.is_adding_text = False
        self.is_popup_window_opened = False
        self.in_battle = False
        self.in_conversation = False
        self.in_shopping = False
        self.highest_cleared_quest_difficulty = 10
        self.hud = HUD_CLS()

    def add_text(self, context):
        self.texts.append(context)

    def update_ui(self, *args):
        self.ui_updates += 1

    def elapse_days(self, days):
        self.world.days_elapsed += days

    def process_choice(self, function, choice_text=""):
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def display_button_load(self, dt):
        return None

    def on_button_press(self, button_index):
        entry = self.buttons[button_index]
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), entry.get("text"))

    def save_game(self):
        self.saves += 1

    # -- 検査用の道具
    def facility_screen(self):
        kind = self.player.location.facility_type
        buttons = []
        if kind == OFFICE_TYPE:
            buttons.append({"text": LABOR_TEXT, "spec": PhaseSpec("DisplayLongTermLaborChoice", [])})
        if kind == UNDERWORLD_TYPE:
            buttons.append({"text": ILLEGAL_TEXT, "spec": PhaseSpec("DisplayIllegalLaborChoice", [])})
        buttons.append({"text": EXIT_TEXT, "spec": PhaseSpec("MovePhaseManager", ["0", "1", "0"])})
        self.buttons = buttons
        self.refresh_choice_buttons(reset_page=True)

    def labels(self):
        return [entry.get("text") for entry in self.buttons]

    def press(self, text):
        """画面に出ている文字列（前方一致）でボタンを押す。"""
        for index, entry in enumerate(self.buttons):
            if (entry.get("text") or "").startswith(text):
                result = self.on_button_press(index)
                CLOCK.settle()
                return result
        raise AssertionError("no button {!r} in {}".format(text, self.labels()))

    def find(self, text):
        for label in self.labels():
            if (label or "").startswith(text):
                return label
        return None


class FakeClock:
    def __init__(self):
        self.intervals = []
        self.onces = []

    def schedule_interval(self, callback, timeout):
        self.intervals.append(callback)

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def tick(self, times=1):
        for _ in range(times):
            self.intervals = [cb for cb in self.intervals if cb(0.3) is not False]

    def run_onces(self):
        for _ in range(8):
            pending, self.onces = self.onces, []
            if not pending:
                return
            for callback in pending:
                callback(0.0)

    def settle(self, times=3):
        for _ in range(times):
            self.run_onces()
            self.tick()
        self.run_onces()


def install_fake_hud():
    name = "scripts.hud.new_hud"
    module = types.ModuleType(name)

    class InstanTaleHUD:
        def __init__(self):
            self.buttons = [types.SimpleNamespace(text="") for _ in range(4)]
            self.painted = []

        def update_button_texts(self, instance, value):
            self.painted.append(list(value))

    module.InstanTaleHUD = InstanTaleHUD
    sys.modules[name] = module
    return InstanTaleHUD


def install_fake_kivy():
    clock = FakeClock()
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    sys.modules.pop("kivy.app", None)
    return clock


def install_fake_functions():
    """ゲームの賃金の式の代わり。難易度 10 で 100。"""
    module = types.ModuleType("scripts.functions")
    module.get_labor_reward = lambda difficulty: 90 + difficulty
    sys.modules["scripts.functions"] = module


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.generation = object()

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator

    def superseded(self):
        return False

    @property
    def state_dir(self):
        return os.path.join(self.out_dir, "state")

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1] else path, exist_ok=True)
        return path

    def read_json(self, path, default=None):
        import instantale_modloader as _ml
        return _ml.read_json(path, default)

    def write_json(self, path, data, indent=1):
        import instantale_modloader as _ml
        return _ml.write_json(path, data, indent=indent)


def load_mod():
    """本番と同じ形（パッケージとして）読み込む。"""
    for name in list(sys.modules):
        if name == MOD_NAME or name.startswith(MOD_NAME + "."):
            sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
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


class Scripted:
    """決めた順に値を返す乱数。尽きたら最後の値を返し続ける。"""

    def __init__(self, *values):
        self.values = list(values) or [0.0]
        self.calls = 0

    def random(self):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


HUD_CLS = install_fake_hud()
CLOCK = install_fake_kivy()
install_fake_functions()

from instantale_modloader import choices, town_events, ui  # noqa: E402

ui.find_app = lambda: getattr(sys.modules["__main__"], "test_app", None)

NUDGES = []


def setup(configure=None, score=11, gold=1000, at=OFFICE_TYPE, rng=None, event=None, keep_state=False,
          big=False):
    """mod を適用し、役場（か裏の事務所）に立っている app を返す。"""
    app_cls = type("InstantaleApp", (InstantaleApp,), {})
    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    for cls in (MovePhaseManager, JustSetButtonToNormalPhase, DisplayLongTermLaborChoice,
                DisplayIllegalLaborChoice, PhaseSpec):
        setattr(main, cls.__name__, cls)

    if not keep_state:
        if os.path.isdir(OUT_DIR):
            shutil.rmtree(OUT_DIR)
        choices.reset()
    os.makedirs(OUT_DIR, exist_ok=True)
    town_events.reset()
    del NUDGES[:]

    office = Facility("1", "検査の役場", OFFICE_TYPE, owner="1")
    smith = Facility("2", "鉄の工房", "blacksmith", owner="5")
    inn = Facility("3", "渡り鳥の宿", "inn", owner="6")
    ward = Facility("4", "通り", "ward")
    twin = Facility("12", "鉄の工房", "blacksmith", owner="15")     # 同じ区画の、同じ名前の施設
    away = Facility("13", "別の区画の工房", "blacksmith", owner="16")  # 繋がっていない区画の施設
    den = Facility("9", "裏の口", UNDERWORLD_TYPE, owner="9")
    here = Area("0", "検査の街", [office, smith, inn, ward, den, twin])
    here.nodes["1"] = Node([away])
    # MOD が建てた施設（ローダの modfacility。主も MOD の id）は雇い主にしない
    here.nodes["0"].facilities["mod:331_facility_investment:0-1"] = Facility(
        "mod:331_facility_investment:0-1", "出資の訓練所", "training_facility",
        owner="mod:331_facility_investment:keeper-0-1")
    if big:     # 施設の多い街（一覧に並べる数より多い）
        for n in range(5):
            shop = Facility("2{}".format(n), "店{}".format(n), "general_store", owner="3{}".format(n))
            here.nodes["0"].facilities[shop.id] = shop
    characters = {"1": Character("役人"), "5": Character("ガルド"), "6": Character("ミーナ"),
                  "9": Character("口利き")}
    world = World({"0": here}, characters)
    player = Player(here, office if at == OFFICE_TYPE else den, gold, score)

    module = load_mod()
    if not keep_state and hasattr(sys, module.STORE_ATTR):
        delattr(sys, module.STORE_ATTR)
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    if rng is not None:
        getattr(sys, module.STORE_ATTR)["rng"] = rng
    if event is not None:
        town_events.declare_current("test_events", ctx, lambda app, area_id: dict(event))

        def nudged(app, area_id, steps, by):
            NUDGES.append((area_id, steps, by))
            return {"name": event.get("name"), "stage": event.get("stage", 1) + steps,
                    "ended": event.get("stage", 1) + steps < 1}
        town_events.on_nudge("test_events", ctx, nudged)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.refresh_choice_buttons", app_cls, "refresh_choice_buttons"),
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
        ("__main__:InstantaleApp.save_game", app_cls, "save_game"),
    ))
    app = app_cls(world, player)
    main.test_app = app
    app.facility_screen()
    CLOCK.settle()
    return module, ctx, app


def bucket_of(module, app):
    worlds = getattr(sys, module.STORE_ATTR)["worlds"]
    return worlds.load(worlds.playthrough(app))


def no_events(module):
    module.EVENT_PERCENT = 0


# ================================================================== 規則
print("\n[規則] 賃金の倍率")
rules = load_mod().rules
check("能力値 11 で 100%", rules.ability_percent(11, 200) == 100.0)
check("能力値 30 で上限", rules.ability_percent(30, 200) == 200.0)
check("能力値 30 を超えても上限のまま", rules.ability_percent(45, 200) == 200.0)
check("能力値 1 で下限 80%", rules.ability_percent(1, 200) == 80.0)
check("上限 100 なら能力値で上がらない", rules.ability_percent(30, 100) == 100.0)
check("能力値が読めなければ基準", rules.ability_average(None, ("strength",)) == 11.0)
check("1回の長さに比例する（30日は 1/3）", rules.term_pay(100, 30) == 33, rules.term_pay(100, 30))
check("倍率を順に掛ける", rules.term_pay(100, 90, (200, 150, 130)) == 390)
check("1 未満にはしない", rules.term_pay(1, 1) == 1)
check("満了の割増は 12.5% と 25%", rules.finish_bonus(400, False) == 50 and rules.finish_bonus(800, True) == 200)
check("人手の仕事に割増は付かない", rules.finish_bonus(400, False, relief=True) == 0)
check("勤続の段の境目", [rules.rank_index(d) for d in (0, 479, 480, 1440, 2880, 99999)] == [0, 0, 1, 2, 3, 3])
check("段ごとの賃金と値引き", rules.rank_pay_percent(1440) == 150 and rules.rank_discount_percent(2880) == 15)
check("次の段までの日数", rules.days_to_next_rank(400) == 80 and rules.days_to_next_rank(5000) is None)
check("日数の言い方", rules.days_in_words(120) == "4ヵ月" and rules.days_in_words(45) == "45日")

print("\n[規則] 街の出来事")
check("出来事が無ければ 100", rules.incident_percent(None) == 100)
check("困り事は賃金を変えない", rules.incident_percent({"good": False, "stage": 3, "tone": "bad"}) == 100)
check("良い出来事は段で上がる", [rules.incident_percent({"good": True, "stage": s, "tone": "good"})
                                 for s in (1, 2, 3, 4)] == [110, 120, 130, 130])
check("良い出来事でも調子の悪い段は 70", rules.incident_percent({"good": True, "stage": 3, "tone": "bad"}) == 70)
check("落ち着いた段は 100", rules.incident_percent({"good": True, "stage": 3, "tone": "calm"}) == 100)

print("\n[規則] 裏の仕事")
check("危険度で 2〜4 倍", [rules.illegal_pay_percent(d, 200, 400) for d in (1, 2, 3)] == [200.0, 300.0, 400.0])
low, mid, high = (rules.risk_percent(2, score, 90) for score in (5, 11, 30))
check("能力値が高いほど危険が起きにくい", low > mid > high, (low, mid, high))
check("基準の能力値では表の値", mid == 18.0, mid)
check("能力値 30 で 4 割まで下がる", abs(high - 18.0 * 0.4) < 1e-9, high)
check("勤続でも下がる", rules.risk_percent(2, 11, 90, 2880) == 18.0 * 0.7)
check("期の長さに比例する", abs(rules.risk_percent(1, 11, 30) - 10.0 / 3) < 1e-9)
check("上限で止まる", rules.risk_percent(3, 1, 720) == 90.0)
check("危険の種類は重みで選ぶ",
      rules.pick_risk(Scripted(0.0), rules.ILLEGAL_JOB_BY_KEY["courier"]) == rules.CRACKDOWN
      and rules.pick_risk(Scripted(0.99), rules.ILLEGAL_JOB_BY_KEY["courier"]) == rules.BETRAYAL
      and rules.pick_risk(Scripted(0.7), rules.ILLEGAL_JOB_BY_KEY["courier"]) == rules.RETALIATION)

print("\n[規則] 出来事の表")
keys = [event["key"] for event in rules.EVENTS]
check("キーが重ならない", len(keys) == len(set(keys)))
check("どの仕事でも起きる出来事が 12 件", sum(1 for event in rules.EVENTS if event["jobs"] is None) == 12)
check("職種ごとに 3 件ずつ", all(
    sum(1 for event in rules.EVENTS if event["jobs"] and row[0] in event["jobs"]) == 3
    for row in rules.OFFICE_JOBS))
broken = []
RESULT_KEYS = {"text", "pay", "tenure", "affinity", "law", "fired"}
for event in rules.EVENTS:
    try:
        event["text"].format(owner="主", place="場")
        if len(event["options"]) < 2:
            broken.append(event["key"] + ": options")
        for option in event["options"]:
            option["label"].format(owner="主", place="場")
            results = [option["win"], option["lose"]] if option["ability"] else [option["result"]]
            if option["ability"] and option["ability"] not in rules.ABILITY_NAMES:
                broken.append(event["key"] + ": ability")
            for result in results:
                result["text"].format(owner="主", place="場")
                if set(result) - RESULT_KEYS or not result["text"]:
                    broken.append(event["key"] + ": result keys")
    except Exception as exc:
        broken.append("{}: {!r}".format(event["key"], exc))
check("どの出来事も文と結果の形が揃っている", not broken, broken)
check("判定の確率は 5〜95 に収まる", rules.check_percent(50, 99) == 95 and rules.check_percent(50, 1) == 20
      and rules.check_percent(10, 1) == 5)
check("能力値1つで 3% 動く", rules.check_percent(50, 12) == 53)
check("1回が短いと出来事の確率も下がる", abs(rules.event_percent(50, 30) - 50 / 3.0) < 1e-9
      and rules.event_percent(50, 90) == 50)
check("直前と同じ出来事は続けない", rules.pick_event(Scripted(0.0), "inn", "rush")["key"] != "rush")

print("\n[規則] 控え")
bucket = rules.new_bucket()
rules.employer_row(bucket, "0/2", "5")
rules.add_tenure(bucket, "0/2", 500)
check("勤続を足す", rules.tenure_days(bucket, "0/2") == 500)
rules.add_tenure(bucket, "0/2", -900)
check("0 より下にはしない", rules.tenure_days(bucket, "0/2") == 0)
rules.add_tenure(bucket, "0/2", 500)
rules.employer_row(bucket, "0/2", "77")
check("雇い主の人物が替わると勤続が消える", rules.tenure_days(bucket, "0/2") == 0)
bucket["contract"] = {"kind": "office", "junk": 1, "place": "場"}
ordered = rules.order_bucket(bucket)
check("書く前に並びを固定する", list(ordered) == list(rules.BUCKET_KEYS)
      and list(ordered["contract"]) == list(rules.CONTRACT_KEYS) and "junk" not in ordered["contract"])

# ================================================================== 入口
print("\n[入口] ゲームのボタンを同じ場所で差し替える")
module, ctx, app = setup(no_events)
check("apply() で例外が出ていない", not ctx.errors, ctx.errors)
check("役場の労働のボタンは同じ文言・同じ位置", app.labels()[0] == LABOR_TEXT and EXIT_TEXT in app.labels(), app.labels())
check("二重に並ばない", app.labels().count(LABOR_TEXT) == 1, app.labels())
app.press(LABOR_TEXT)
check("ゲームの労働の画面へは進まない", "DisplayLongTermLaborChoice" not in app.game_phases, app.game_phases)
check("街の施設ごとの仕事が並ぶ", app.find("鉄の工房の手伝い") and app.find("渡り鳥の宿の手伝い")
      and app.find("検査の役場の手伝い"), app.labels())
check("主の居ない施設は並ばない", not app.find("通り"), app.labels())
check("同じ名前の施設は1つだけ並ぶ", sum(1 for label in app.labels() if label.startswith("鉄の工房")) == 1, app.labels())
check("MOD が建てた施設は並ばない", not app.find("出資の訓練所"), app.labels())
check("繋がっていない区画の施設は並ばない", not app.find("別の区画の工房"), app.labels())
check("裏の事務所は役場の一覧に並ばない", not app.find("裏の口"), app.labels())
check("日雇いの人足が並ぶ", app.find("日雇いの人足(100G)") is not None, app.labels())
check("能力値 11・勤続なしなら素の賃金", app.find("鉄の工房の手伝い(100G)") is not None, app.labels())
check("一覧の本文は1件1行で、効く能力値・賃金・勤続が出る",
      "・鉄の工房の手伝い（筋力・耐久／3ヵ月 100G／勤続 見習い）" in app.texts
      and "・日雇いの人足（3ヵ月 100G）" in app.texts, app.texts)
check("一覧の行に句点を挟まない", not any("。" in text for text in app.texts if text.startswith("・")), app.texts)
app.press("やめる")
check("やめると役場の選択肢に戻る", app.labels()[0] == LABOR_TEXT, app.labels())

_m, _c, strong = setup(no_events, score=30)
strong.press(LABOR_TEXT)
check("能力値 30 なら 2 倍", strong.find("鉄の工房の手伝い(200G)") is not None, strong.labels())
check("人足は能力値で変わらない", strong.find("日雇いの人足(100G)") is not None, strong.labels())

# ================================================================== 役場
print("\n[役場] 契約して務め終える")
module, ctx, app = setup(no_events)
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
check("契約の長さは2つ（割増こみの合計）", app.find("12ヵ月(450G)") and app.find("24ヵ月(1,000G)"), app.labels())
app.press("12ヵ月")
check("働く画面になる", app.find("働く(あと4回)") and app.find("途中でやめる"), app.labels())
day, gold = app.world.days_elapsed, app.player.gold
app.press("働く")
check("1回で 90 日進む", app.world.days_elapsed == day + 90, app.world.days_elapsed - day)
check("1回ぶんの賃金が入る", app.player.gold == gold + 100, app.player.gold - gold)
check("所持金の表示を塗り直す", app.ui_updates > 0, app.ui_updates)
check("残りの回数が減る", app.find("働く(あと3回)") is not None, app.labels())
for _ in range(3):
    app.press("働く")
check("満了で割増が付く（400 + 50）", app.player.gold == gold + 450, app.player.gold - gold)
check("360 日進んだ", app.world.days_elapsed == day + 360)
check("役場の選択肢に戻る", app.labels()[0] == LABOR_TEXT, app.labels())
check("契約は控えから消える", bucket_of(module, app)["contract"] is None)
check("勤続が付く", rules.tenure_days(bucket_of(module, app), "0/2") == 360, bucket_of(module, app))
check("段が上がるまでは知らせない", not any("一人前" in text for text in app.texts), app.texts[-6:])
check("雇い主の好感度が上がる（短い契約 +3）",
      app.world.characters["5"].relationship["player"]["affinity"] == 13)
check("保存を頼んでいる", app.saves > 0, app.saves)
check("人生の記録に1行書く（ゲームの労働と同じ形）", app.player.life_log == [
    {"day_start": day, "day_end": day + 360, "summarized_count": 0,
     "content": "〈労働の記録〉検査の主人公は検査の街の鉄の工房の手伝いを12ヵ月務め上げ、給与を得た。\n"}], app.player.life_log)
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("12ヵ月")
app.press("働く")
app.press("働く")
check("勤続が 480 日に届くと段が上がったと知らせる", any("一人前" in text for text in app.texts), app.texts[-6:])
app.press("途中でやめる")
bucket_of(module, app)["employers"]["0/2"]["barred_until"] = None
rules.add_tenure(bucket_of(module, app), "0/2", 240)     # やめた分を戻す
app.press(LABOR_TEXT)
# 務め終えた1契約ぶん、街の実績で基準が 100 → 102 に上がっている（途中でやめた契約は数えない）
check("勤続の段で賃金が上がる（102 の 125%）", app.find("鉄の工房の手伝い(128G)") is not None, app.labels())
check("勤続はほかの雇い主には付かない", app.find("渡り鳥の宿の手伝い(102G)") is not None, app.labels())

print("\n[役場] 1回の長さと回数を変える")
def monthly(module):
    module.EVENT_PERCENT = 0
    module.TERM_DAYS = 30
    module.SHORT_TERMS = 3
    module.LONG_TERMS = 6


module, ctx, app = setup(monthly)
app.press(LABOR_TEXT)
check("賃金は長さに比例する", app.find("鉄の工房の手伝い(33G)") is not None, app.labels())
app.press("鉄の工房の手伝い")
check("契約は 3ヵ月と 6ヵ月", app.find("3ヵ月(") and app.find("6ヵ月("), app.labels())
app.press("3ヵ月")
day = app.world.days_elapsed
app.press("働く")
check("1回で 30 日進む", app.world.days_elapsed == day + 30)

# ================================================================== 一覧の顔ぶれ
print("\n[一覧] 並ぶのは3件と人足。1ヵ月ごとに入れ替わる")
keys = ["k{}".format(n) for n in range(8)]
first = rules.pick_listed(keys, "世界×主|0|18")
check("3件を選ぶ", len(first) == 3 and set(first) <= set(keys), first)
check("同じ種なら同じ顔ぶれ", rules.pick_listed(keys, "世界×主|0|18") == first)
check("渡した並びの順を保つ", first == [key for key in keys if key in first])
check("候補が3件以下なら全部", rules.pick_listed(keys[:3], "x") == keys[:3] and rules.pick_listed([], "x") == [])
check("入れ替えの回が変わると顔ぶれが変わる",
      len({tuple(rules.pick_listed(keys, "世界×主|0|{}".format(n))) for n in range(12)}) > 1)
check("入れ替えは 30 日ごと", [rules.rotation_of(d) for d in (0, 29, 30, 59, 60)] == [0, 0, 1, 1, 2])


def shown(app):
    """いまの一覧の、施設の仕事の名前。"""
    return [label.split("の手伝い")[0] for label in app.labels() if "の手伝い" in label]


module, ctx, app = setup(no_events, big=True)
app.press(LABOR_TEXT)
first = shown(app)
check("施設の多い街でも施設の仕事は3件", len(first) == 3, app.labels())
check("日雇いの人足はいつも並ぶ", app.find("日雇いの人足") is not None, app.labels())
app.press("やめる")
app.press(LABOR_TEXT)
check("開き直しても同じ顔ぶれ", shown(app) == first, (first, shown(app)))
app.press("やめる")
app.world.days_elapsed += 29 - app.world.days_elapsed % 30
app.press(LABOR_TEXT)
check("同じ月のうちは同じ顔ぶれ", shown(app) == first, (first, shown(app)))
app.press("やめる")
seen = {tuple(first)}
for _ in range(12):
    app.world.days_elapsed += 30
    app.press(LABOR_TEXT)
    seen.add(tuple(shown(app)))
    app.press("やめる")
check("月が替わると顔ぶれが入れ替わる", len(seen) > 1, seen)

print("\n[一覧] 前回務め終えた仕事は追加の枠で並ぶ")
module, ctx, app = setup(no_events, big=True)
app.press(LABOR_TEXT)
done = shown(app)[0]
app.press(done + "の手伝い")
app.press("12ヵ月")
for _ in range(4):
    app.press("働く")
kept = True
extra = False
for _ in range(12):
    app.press(LABOR_TEXT)
    names = shown(app)
    kept = kept and done in names
    extra = extra or len(names) == 4
    app.press("やめる")
    app.world.days_elapsed += 30
check("務め終えた仕事は、月が替わっても並び続ける", kept)
check("選ばれた3件に入っていない月は、4件めとして並ぶ", extra)
check("本文に「前回の仕事」と出る", any(t.startswith("・" + done) and "前回の仕事" in t for t in app.texts), app.texts[-8:])
app.press(LABOR_TEXT)
app.press(done + "の手伝い")
app.press("12ヵ月")
app.press("働く")
app.press("途中でやめる")
check("途中でやめると「前回の仕事」の枠は空く", rules.last_done(bucket_of(module, app), "0") is None,
      bucket_of(module, app)["last_done"])
module, ctx, app = setup(no_events, big=True)
app.press(LABOR_TEXT)
app.press("日雇いの人足")
app.press("12ヵ月")
for _ in range(4):
    app.press("働く")
check("人足を務め終えても「前回の仕事」の枠は付かない", rules.last_done(bucket_of(module, app), "0") is None)

# ================================================================== 出来事
print("\n[出来事] 働く合間に選択肢で応じる")
def always(module):
    module.EVENT_PERCENT = 100


# 乱数: 出来事が起きる(0.0) → 表の先頭(0.0) → 判定に成功(0.0)
module, ctx, app = setup(always, rng=Scripted(0.0))
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("12ヵ月")
gold = app.player.gold
app.press("働く")
check("出来事の文が出る", any("追加で頼まれた" in text and "ガルド" in text for text in app.texts), app.texts[-3:])
check("選択肢に能力値と確率が出る", app.find("引き受ける(耐久 60%)") is not None, app.labels())
check("出来事の間は賃金がまだ入らない", app.player.gold == gold, app.player.gold - gold)
check("出来事は控えに残る", bucket_of(module, app)["contract"]["pending"] == {"event": "rush"})
app.press("引き受ける")
check("成功すると賃金が 30% 増える", app.player.gold == gold + 130, app.player.gold - gold)
check("勤続に 15 日が足される", rules.tenure_days(bucket_of(module, app), "0/2") == 105)
check("働く画面に戻る", app.find("働く(あと3回)") is not None, app.labels())

# 判定に失敗（0.99）
module, ctx, app = setup(always, rng=Scripted(0.0, 0.0, 0.99))
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("12ヵ月")
gold = app.player.gold
app.press("働く")
app.press("引き受ける")
check("失敗すると賃金が 10% 減る", app.player.gold == gold + 90, app.player.gold - gold)

# 盗みの誘いに乗ってしくじる: 出来事(0.0) → 「theft」を引く → 判定に失敗
theft_index = [e["key"] for e in rules.events_for("blacksmith")].index("theft")
pool = len(rules.events_for("blacksmith"))
module, ctx, app = setup(always, rng=Scripted(0.0, (theft_index + 0.5) / pool, 0.99))
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("12ヵ月")
app.press("働く")
check("盗みの誘いが出る", app.find("乗る(器用") is not None, app.labels())
app.press("乗る")
check("しくじると契約が打ち切られる", bucket_of(module, app)["contract"] is None)
check("手配度が下がる", app.player.area_history["0"]["lawfulness"] == 7, app.player.area_history)
check("雇い主の好感度が下がる", app.world.characters["5"].relationship["player"]["affinity"] == 0)
app.press(LABOR_TEXT)
check("暇を出された雇い主は一覧に出ない", not app.find("鉄の工房の手伝い"), app.labels())

module, ctx, app = setup(always, rng=Scripted(0.0))
app.press(LABOR_TEXT)
app.press("日雇いの人足")
app.press("12ヵ月")
app.press("働く")
check("人足には出来事が起きない", app.find("働く(あと3回)") is not None, app.labels())

# ================================================================== 途中でやめる
print("\n[途中] やめると勤続が減り、一覧から消える")
module, ctx, app = setup(no_events)
app.press(LABOR_TEXT)
app.press("渡り鳥の宿の手伝い")
app.press("24ヵ月")
for _ in range(3):
    app.press("働く")
gold = app.player.gold
app.press("途中でやめる")
check("割増は付かない", app.player.gold == gold)
check("やめたことを人生の記録に書く", len(app.player.life_log) == 1
      and "9ヵ月で途中で辞めた" in app.player.life_log[0]["content"], app.player.life_log)
check("勤続が 240 日減る（270 → 30）", rules.tenure_days(bucket_of(module, app), "0/3") == 30)
check("役場の選択肢に戻る", app.labels()[0] == LABOR_TEXT, app.labels())
app.press(LABOR_TEXT)
check("やめた雇い主は一覧に出ない", not app.find("渡り鳥の宿の手伝い"), app.labels())
check("ほかの雇い主は出る", app.find("鉄の工房の手伝い") is not None, app.labels())
app.press("やめる")
app.world.days_elapsed += 181
app.press(LABOR_TEXT)
check("180 日たつと一覧に戻る", app.find("渡り鳥の宿の手伝い") is not None, app.labels())

# ================================================================== 裏の事務所
print("\n[裏] 危険度で賃金が上がり、危険が起きる")
module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.99))
check("闇労働のボタンは同じ文言", app.labels()[0] == ILLEGAL_TEXT, app.labels())
app.press(ILLEGAL_TEXT)
check("ゲームの闇労働の画面へは進まない", "DisplayIllegalLaborChoice" not in app.game_phases)
# 役場の 90 日ぶんは 75。危険度1 は 2倍、危険度2 は 3倍
check("危険度1 は 2 倍", app.find("荷の運び(200G)") and app.find("見張り(200G)"), app.labels())
check("危険度2 は 3 倍", app.find("取り立て(300G)") and app.find("帳簿の細工(300G)"), app.labels())
check("危険度3 は勤続が無いと並ばない", not app.find("押し込みの手引き"), app.labels())
check("本文に危険の種類と確率が出る", "・荷の運び（3ヵ月 200G／危険 取り締まり・仕返し 10%）" in app.texts, app.texts)
app.press("荷の運び")
app.press("9ヵ月")
check("途中でやめる選択肢は無い", app.labels() == ["仕事に出る(あと3期)"], app.labels())
day, gold = app.world.days_elapsed, app.player.gold
for _ in range(3):
    app.press("仕事に出る")
check("危険が起きなければ 3 期ぶん入る", app.player.gold == gold + 600, app.player.gold - gold)
check("270 日進む", app.world.days_elapsed == day + 270)
check("事務所での勤続が付く", rules.tenure_days(bucket_of(module, app), "0/9") == 270)

# 取り締まり: 危険が起きる(0.0) → 種類の先頭(0.0)
module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.0))
app.press(ILLEGAL_TEXT)
app.press("荷の運び")
app.press("9ヵ月")
gold = app.player.gold
app.press("仕事に出る")
check("取り締まり: その期の賃金が入らない", app.player.gold == gold, app.player.gold - gold)
check("取り締まり: 手配度が下がる（危険度1 で 2）", app.player.area_history["0"]["lawfulness"] == 8)
check("取り締まり: 契約は続く", app.find("仕事に出る(あと2期)") is not None, app.labels())
module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.0, 0.99))
app.press(ILLEGAL_TEXT)
app.press("荷の運び")
app.press("9ヵ月")
app.press("仕事に出る")
check("裏切りで失う稼ぎが無いときは額を言わない", "組んでいた者に売られた。役人に名前が伝わり、仕事は打ち切りになった。" in app.texts
      and not any("稼ぎ0G" in x for x in app.texts), app.texts[-4:])

# 仕返し: 取り立て（重み 1:3:1）で 0.5 → 仕返し
module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.0, 0.5))
app.press(ILLEGAL_TEXT)
app.press("取り立て")
app.press("9ヵ月")
gold = app.player.gold
app.press("仕事に出る")
check("仕返し: 賃金2期ぶんを奪われる", app.player.gold == gold - 600, app.player.gold - gold)

# 裏切り: 1期めは無事(0.99)、2期めに危険(0.0)で裏切り(0.99)
module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.99, 0.0, 0.99))
app.press(ILLEGAL_TEXT)
app.press("帳簿の細工")
app.press("9ヵ月")
gold = app.player.gold
app.press("仕事に出る")
check("1期めの賃金が入る", app.player.gold == gold + 300)
app.press("仕事に出る")
check("裏切り: ここまでの稼ぎが消える", app.player.gold == gold, app.player.gold - gold)
check("裏切り: 手配度が 1 下がる", app.player.area_history["0"]["lawfulness"] == 9)
check("裏切り: 失った額を言う", any("ここまでの稼ぎ300Gは消え" in x for x in app.texts), app.texts[-4:])
check("裏切り: 仕事は打ち切り", bucket_of(module, app)["contract"] is None and app.labels()[0] == ILLEGAL_TEXT)
check("裏切り: 勤続が減る（90 + 90 − 90 − 90 = 0）", rules.tenure_days(bucket_of(module, app), "0/9") == 0,
      bucket_of(module, app)["employers"])

module, ctx, app = setup(at=UNDERWORLD_TYPE, score=30, rng=Scripted(0.99))
app.press(ILLEGAL_TEXT)
check("能力値で賃金は変わらない", app.find("荷の運び(200G)") is not None, app.labels())
check("能力値が高いと確率が下がる（10% → 4%）", any(t.startswith("・荷の運び") and "4%）" in t for t in app.texts), app.texts)

module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.99))
rules.add_tenure(bucket_of(module, app), "0/9", 480)
app.press(ILLEGAL_TEXT)
check("勤続があれば危険度3 が並ぶ（4 倍）", app.find("押し込みの手引き(400G)") is not None, app.labels())

print("\n[闇労働] 役場の契約の途中")
module, ctx, app = setup(no_events)
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("24ヵ月")
app.press("働く")
going = dict(bucket_of(module, app)["contract"])
app.player.location = app.world.areas["0"].nodes["0"].facilities["9"]
app.facility_screen()
CLOCK.settle()
app.press(ILLEGAL_TEXT)
check("同じ街の役場の契約が残っていれば裏の仕事は請けられない",
      any("契約がまだ残っている" in t for t in app.texts[-2:]) and app.labels()[0] == ILLEGAL_TEXT,
      (app.texts[-2:], app.labels()))
check("役場の契約は消えない", bucket_of(module, app)["contract"] == going, bucket_of(module, app)["contract"])

# ================================================================== 連動
print("\n[連動] 街の出来事")
good = {"name": "収穫の祭り", "kind": "festival", "good": True, "stage": 2, "last_stage": 3, "tone": "good"}
module, ctx, app = setup(no_events, event=good)
app.press(LABOR_TEXT)
check("良い出来事で賃金が上がる（2段めは 120%）", app.find("鉄の工房の手伝い(120G)") is not None, app.labels())
check("理由を本文に出す", any("収穫の祭り" in t and "120%" in t for t in app.texts), app.texts)
check("良い出来事に人手の仕事は出ない", not app.find("臨時の募集・収穫の祭りの対応"), app.labels())

bad = {"name": "盗賊の横行", "kind": "outlaws", "good": False, "stage": 2, "last_stage": 3, "tone": "bad"}
module, ctx, app = setup(no_events, event=bad)
app.press(LABOR_TEXT)
check("困り事では賃金は変わらない", app.find("鉄の工房の手伝い(100G)") is not None, app.labels())
# 30 日ぶん 25 の 1.5 倍
check("人手の仕事が出る", app.find("臨時の募集・盗賊の横行の対応(50G)") is not None, app.labels())
app.press("臨時の募集・盗賊の横行の対応")
check("人手の仕事は 30 日 × 3 回", app.labels()[0].startswith("3ヵ月("), app.labels())
app.press("3ヵ月")
day = app.world.days_elapsed
for _ in range(3):
    app.press("働く")
check("90 日で務め終える", app.world.days_elapsed == day + 90 and bucket_of(module, app)["contract"] is None)
check("収まる側へ1段の頼みが出る", NUDGES == [("0", -1, module.OWNER)], NUDGES)
check("段の動きは知らせない（出来事を持つ MOD が出す）", not any("収まる方へ" in t for t in app.texts), app.texts[-4:])

module, ctx, app = setup(no_events, event=bad)
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("12ヵ月")
for _ in range(4):
    app.press("働く")
check("ふつうの仕事では出来事を動かさない", NUDGES == [], NUDGES)

module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.99), event=bad)
app.press(ILLEGAL_TEXT)
check("困り事の間は裏の仕事の払いが 1.3 倍", app.find("荷の運び(260G)") is not None, app.labels())
app.press("荷の運び")
app.press("9ヵ月")
for _ in range(3):
    app.press("仕事に出る")
check("裏の仕事を務め終えると悪くなる側へ1段", NUDGES == [("0", 1, module.OWNER)], NUDGES)

def unlinked(module):
    module.EVENT_PERCENT = 0
    module.INCIDENT_LINK = False


module, ctx, app = setup(unlinked, event=good)
app.press(LABOR_TEXT)
check("連動を切ると賃金は変わらない", app.find("鉄の工房の手伝い(100G)") is not None, app.labels())

# ================================================================== 続き
print("\n[続き] 契約の途中で注入し直す")
module, ctx, app = setup(no_events)
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("24ヵ月")
app.press("働く")
module2, ctx2, app2 = setup(no_events, keep_state=True)
check("働く画面のまま戻る", app2.find("働く(あと7回)") is not None, app2.labels())
gold = app2.player.gold
app2.press("働く")
check("続きから働ける", app2.player.gold == gold + 100 and app2.find("働く(あと6回)") is not None, app2.labels())
choices.close_screen(app2)
CLOCK.settle()
app2.facility_screen()
CLOCK.settle()
check("画面が閉じても「仕事に戻る」で戻れる", app2.labels()[0] == "仕事に戻る", app2.labels())
app2.press("仕事に戻る")
check("働く画面に戻る", app2.find("働く(あと6回)") is not None, app2.labels())

# ================================================================== 値引き
print("\n[値引き] 勤続の段のある店")
from instantale_modloader import prices  # noqa: E402

module, ctx, app = setup(no_events)
layer = [entry for entry in prices._item_registry()["adjust"] if entry[0] == module.OWNER]
check("値段の段を一時の段として置く", len(layer) == 1 and layer[0][2] is True, layer)
layer = layer[0][1]
app.player.location = app.world.areas["0"].nodes["0"].facilities["2"]
check("勤続が無ければ触らない", layer(object(), prices.BUY, 1000) is None)
rules.add_tenure(bucket_of(module, app), "0/2", 1440)
check("任される段で 10% 引き", layer(object(), prices.BUY, 1000) == 900)
check("売値には触らない", layer(object(), prices.SELL, 1000) is None)
app.player.location = app.world.areas["0"].nodes["0"].facilities["3"]
check("ほかの店では触らない", layer(object(), prices.BUY, 1000) is None)

# ================================================================== 街の実績
print("\n[街の実績] 務め終えるたびに、その街の賃金の基準が上がる")
bucket = rules.new_bucket()
check("実績の無い街は 0", rules.town_growth(bucket, "0") == 0 and rules.town_done(bucket, "0") == 0)
check("務め終えるたびに積む", rules.add_growth(bucket, "0", 5, 60) == (0, 5)
      and rules.add_growth(bucket, "0", 2, 60) == (5, 7) and rules.town_done(bucket, "0") == 2)
check("上限で止まる", rules.add_growth(bucket, "0", 99, 60) == (7, 60) and rules.add_growth(bucket, "0", 5, 60) == (60, 60))
check("上限を下げても積んだ実績は減らない", rules.add_growth(bucket, "0", 5, 10) == (60, 60))
check("街ごとに別", rules.town_growth(bucket, "1") == 0)
check("書く前の並びに入る", rules.order_bucket(bucket)["towns"] == {"0": {"growth": 60, "done": 5}})

# 偽の賃金の式は 90 + 難易度。難易度 10 の主人公は 100 から始まる
module, ctx, app = setup(no_events)
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("12ヵ月")
for _ in range(4):
    app.press("働く")
check("短い契約を務め終えると実績 +2", bucket_of(module, app)["towns"] == {"0": {"growth": 2, "done": 1}},
      bucket_of(module, app)["towns"])
check("上がったと知らせる", any("賃金の水準が上がった（実績 +2）" in t for t in app.texts), app.texts[-6:])
app.press(LABOR_TEXT)
check("人足の賃金も基準ごと上がる（100 → 102）", app.find("日雇いの人足(102G)") is not None, app.labels())
check("一覧に実績を出す", "この街での労働の実績は +2（務め終えた契約 1 回）" in app.texts, app.texts[-8:])
app.press("日雇いの人足")
app.press("24ヵ月")
for _ in range(8):
    app.press("働く")
check("長い契約は +5。人足も数える", bucket_of(module, app)["towns"]["0"] == {"growth": 7, "done": 2})
app.press(LABOR_TEXT)
app.press("渡り鳥の宿の手伝い")
app.press("12ヵ月")
app.press("働く")
app.press("途中でやめる")
check("途中でやめた契約は数えない", bucket_of(module, app)["towns"]["0"] == {"growth": 7, "done": 2})

module, ctx, app = setup(at=UNDERWORLD_TYPE, rng=Scripted(0.99))
rules.add_growth(bucket_of(module, app), "0", 20, 60)
app.press(ILLEGAL_TEXT)
# 基準 (90 + 10 + 20) = 120 の 90 日ぶんは 120、危険度1 は 2 倍
check("闇労働の賃金にも街の実績が乗る", app.find("荷の運び(240G)") is not None, app.labels())
app.press("荷の運び")
app.press("9ヵ月")
for _ in range(3):
    app.press("仕事に出る")
check("闇労働は実績に数えない", bucket_of(module, app)["towns"]["0"] == {"growth": 20, "done": 1},
      bucket_of(module, app)["towns"])


def no_growth(module):
    module.EVENT_PERCENT = 0
    module.TOWN_GROWTH = False


module, ctx, app = setup(no_growth)
rules.add_growth(bucket_of(module, app), "0", 20, 60)
app.press(LABOR_TEXT)
check("切ると賃金に乗らない", app.find("日雇いの人足(100G)") is not None, app.labels())
app.press("日雇いの人足")
app.press("12ヵ月")
for _ in range(4):
    app.press("働く")
check("切ると積まない", bucket_of(module, app)["towns"]["0"] == {"growth": 20, "done": 1})


def fast_growth(module):
    module.EVENT_PERCENT = 0
    module.GROWTH_SHORT = 30
    module.GROWTH_MAX = 40


module, ctx, app = setup(fast_growth)
for _ in range(2):
    app.press(LABOR_TEXT)
    app.press("日雇いの人足")
    app.press("12ヵ月")
    for _ in range(4):
        app.press("働く")
check("上がる幅と上限を設定で変えられる", bucket_of(module, app)["towns"]["0"] == {"growth": 40, "done": 2})

# ================================================================== 年齢
print("\n[年齢] 素のゲームと同じく、年齢で1回が長くなり、老いると断られる")
check("1回の長さ（月）は 29歳まで 3、5歳ごとに +1", [rules.age_months(a) for a in (18, 29, 30, 34, 35, 44, 45, 50, 54)]
      == [3, 3, 4, 4, 5, 6, 7, 8, 8])
check("年齢が読めなければ 3", rules.age_months(None) == 3 and rules.age_months("x") == 3 and rules.age_months(True) == 3)
check("設定の長さを同じ割合で伸ばす", rules.age_days(90, 50) == 240 and rules.age_days(30, 31) == 40)


def aged(age):
    def configure(module):
        module.EVENT_PERCENT = 0
    configure.age = age
    return configure


def setup_aged(age, **kwargs):
    module, ctx, app = setup(aged(age), **kwargs)
    app.player.age = str(age)
    app.facility_screen()
    CLOCK.settle()
    return module, ctx, app


module, ctx, app = setup_aged(50)
app.press(LABOR_TEXT)
check("50歳の役場は1回 8ヵ月、賃金は変わらない", app.find("鉄の工房の手伝い(100G)") is not None
      and "・鉄の工房の手伝い（筋力・耐久／8ヵ月 100G／勤続 見習い）" in app.texts, app.texts[-6:])
app.press("鉄の工房の手伝い")
check("契約は 32ヵ月と 64ヵ月", app.find("32ヵ月(450G)") and app.find("64ヵ月(1,000G)"), app.labels())
app.press("32ヵ月")
day = app.world.days_elapsed
app.press("働く")
check("1回で 240 日進む", app.world.days_elapsed == day + 240, app.world.days_elapsed - day)

module, ctx, app = setup_aged(55)
check("55歳の役場はゲームのボタンのまま", app.labels()[0] == LABOR_TEXT, app.labels())
app.press(LABOR_TEXT)
check("押すとゲームの労働の画面へ進む（ゲームが断る）", "DisplayLongTermLaborChoice" in app.game_phases, app.game_phases)

module, ctx, app = setup_aged(49, at=UNDERWORLD_TYPE, rng=Scripted(0.99))
app.press(ILLEGAL_TEXT)
check("49歳の闇労働は1期 7ヵ月", any(t.startswith("・荷の運び（7ヵ月") for t in app.texts), app.texts[-6:])
module, ctx, app = setup_aged(50, at=UNDERWORLD_TYPE)
app.press(ILLEGAL_TEXT)
check("50歳の闇労働はゲームのボタンのまま（ゲームが断る）", "DisplayIllegalLaborChoice" in app.game_phases, app.game_phases)

module, ctx, app = setup_aged(54)
app.press(LABOR_TEXT)
app.press("鉄の工房の手伝い")
app.press("32ヵ月")
app.press("働く")
app.player.age = "55"
app.facility_screen()
CLOCK.settle()
check("働いている途中で境目を越えても、続きは働ける", app.find("働く(あと3回)") is not None
      or app.labels()[0] == "仕事に戻る", app.labels())


def no_age(module):
    module.EVENT_PERCENT = 0
    module.AGE_RULES = False


module, ctx, app = setup(no_age)
app.player.age = "70"
app.facility_screen()
CLOCK.settle()
app.press(LABOR_TEXT)
check("年齢の決まりを切ると、70歳でも 3ヵ月で働ける", app.find("鉄の工房の手伝い(100G)") is not None
      and any("3ヵ月 100G" in t for t in app.texts), app.labels())

# ================================================================== 数の設定
print("\n[数の設定] 期間と数値を設定で変える")


def tuned(module):
    module.EVENT_PERCENT = 0
    module.LIST_JOBS = 2
    module.ROTATE_DAYS = 10
    module.SHORT_BONUS_PERCENT = 50.0
    module.SHORT_AFFINITY = 9
    module.RANK2_DAYS = 100
    module.RANK2_PAY_PERCENT = 300
    module.RANK2_DISCOUNT_PERCENT = 40
    module.QUIT_TENURE_DAYS = 50
    module.QUIT_BARRED_DAYS = 20


module, ctx, app = setup(tuned, big=True)
tuned_rules = module.rules
app.press(LABOR_TEXT)
check("一覧の件数を変えられる", len(shown(app)) == 2, app.labels())
check("入れ替えの間隔を変えられる", [tuned_rules.rotation_of(d) for d in (0, 9, 10, 25)] == [0, 0, 1, 2])
name = shown(app)[0]
app.press(name + "の手伝い")
check("満了の割増を変えられる（400 の 50%）", app.find("12ヵ月(600G)") is not None, app.labels())
app.press("12ヵ月")
gold = app.player.gold
for _ in range(4):
    app.press("働く")
check("割増が設定の額で入る", app.player.gold == gold + 600, app.player.gold - gold)
employer = tuned_rules.last_done(bucket_of(module, app), "0")
check("段の日数を変えられる（100 日で一人前）",
      tuned_rules.rank_index(tuned_rules.tenure_days(bucket_of(module, app), employer)) >= 1)
check("段の賃金と値引きを変えられる", tuned_rules.rank_pay_percent(100) == 300
      and tuned_rules.rank_discount_percent(100) == 40)
check("後の段の日数は前の段より小さくならない", [row[1] for row in tuned_rules.RANKS] == [0, 100, 1440, 2880])
app.press(LABOR_TEXT)
app.press(name + "の手伝い")
app.press("12ヵ月")
app.press("働く")
before = tuned_rules.tenure_days(bucket_of(module, app), employer)
day = app.world.days_elapsed
app.press("途中でやめる")
check("やめたときに減る勤続を変えられる",
      tuned_rules.tenure_days(bucket_of(module, app), employer) == before - 50)
check("一覧に出ない日数を変えられる",
      bucket_of(module, app)["employers"][employer]["barred_until"] == day + 20)


def strong_relief(module):
    module.EVENT_PERCENT = 0
    module.RELIEF_STEPS = 2
    module.RELIEF_PAY_PERCENT = 400
    module.RELIEF_TERM_DAYS = 10
    module.RELIEF_TERMS = 2
    module.GOOD_EVENT_STEP_PERCENT = 50
    module.GOOD_EVENT_MAX_PERCENT = 300


module, ctx, app = setup(strong_relief, event=bad)
app.press(LABOR_TEXT)
# 10 日ぶんは 100 * 10 / 90 = 11.1、4 倍で 44
check("臨時の募集の賃金と長さを変えられる", app.find("臨時の募集・盗賊の横行の対応(44G)") is not None, app.labels())
app.press("臨時の募集")
check("臨時の募集の回数を変えられる", app.labels()[0].startswith("20日("), app.labels())
app.press("20日")
for _ in range(2):
    app.press("働く")
check("収まる側へ動かす段階の数を変えられる", NUDGES == [("0", -2, module.OWNER)], NUDGES)
check("良い出来事の賃金の上がり方を変えられる",
      module.rules.incident_percent({"good": True, "stage": 2, "tone": "good"}) == 200
      and module.rules.incident_percent({"good": True, "stage": 9, "tone": "good"}) == 300)


def risky(module):
    module.RISK_PERCENT_1 = 60
    module.CRACKDOWN_LAW = 5
    module.ILLEGAL_STEPS = 0


module, ctx, app = setup(risky, at=UNDERWORLD_TYPE, rng=Scripted(0.5, 0.0), event=bad)
app.press(ILLEGAL_TEXT)
check("危険の確率を変えられる", any(t.startswith("・荷の運び") and "60%）" in t for t in app.texts), app.texts)
app.press("荷の運び")
app.press("9ヵ月")
app.press("仕事に出る")
check("取り締まりで下がる手配度を変えられる", app.player.area_history["0"]["lawfulness"] == 5,
      app.player.area_history)
module, ctx, app = setup(risky, at=UNDERWORLD_TYPE, rng=Scripted(0.99), event=bad)
app.press(ILLEGAL_TEXT)
app.press("荷の運び")
app.press("9ヵ月")
for _ in range(3):
    app.press("仕事に出る")
check("悪くなる側へ動かす段階を 0 にすると頼まない", NUDGES == [], NUDGES)

_m, _c, _a = setup(no_events)
check("設定を既定に戻すと、段の表も既定に戻る", [row[1:4] for row in _m.rules.RANKS] == [
    (0, 100, 0), (480, 125, 5), (1440, 150, 10), (2880, 200, 15)], _m.rules.RANKS)

# ================================================================== 設定
print("\n[設定] mod.json と入口の定数")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
fresh = load_mod()
for key, spec in manifest["settings"].items():
    check("既定値が一致: {}".format(key), getattr(fresh, key, object()) == spec.get("default"),
          (getattr(fresh, key, None), spec.get("default")))
check("素のゲームの長さが既定", fresh.TERM_DAYS == 90 and fresh.SHORT_TERMS == 4 and fresh.LONG_TERMS == 8
      and fresh.ILLEGAL_TERM_DAYS == 90 and fresh.ILLEGAL_TERMS == 3)

print("\n[好感度] 範囲の外に居る相手を範囲へ引き戻さない")
check("100 を超える相手は、上がる場面で下がらない", fresh.affinity_after(160, 3) == 160)
check("100 を超える相手は、下がる場面では下がる", fresh.affinity_after(160, -1) == 159)
check("-100 より下の相手は、下がる場面で上がらない", fresh.affinity_after(-150, -1) == -150)
check("範囲の中は 100 で止まる", fresh.affinity_after(98, 3) == 100 and fresh.affinity_after(10, 3) == 13)

print("\n[勤続] 雇い主の人物が替わったら、控えを消す前でも勤続を数えない（店の値引き）")
seniority = {"employers": {"2:7": {"days": 1440, "owner": "5", "barred_until": None}}}
check("同じ主なら勤続を読む", rules.tenure_days(seniority, "2:7", "5") == 1440)
check("主が替わっていれば 0", rules.tenure_days(seniority, "2:7", "77") == 0)
check("主を渡さなければ今までどおり", rules.tenure_days(seniority, "2:7") == 1440)

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
