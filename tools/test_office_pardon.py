# -*- coding: utf-8 -*-
"""309_office_pardon をゲーム抜きで通す。

    python tools/test_office_pardon.py

偽の app / Player / Area / Facility / PhaseSpec / MovePhaseManager / HUD / Clock を
差し込み、次を確認する。

  設置   … 役場で手配されているときだけ「罰金を納めて手配を解く」が出る。
           「出る」の手前に入り、塗り直しても二重にならない。
           ギルドでは出ない／手配が無ければ出ない／会話中は出ない
  押下   … 印で横取りしてゲームの経路（`process_choice`）に乗せる。
           `PhaseSpec` に自前クラス名を書かない
  金額   … 罰金 = 1点あたりの額 × 手配度（閾値との差）。0円設定なら無料
  支払   … 所持金が減り、手配度が `RESTORE_TO` に戻り、ボタンが消える。
           **今いるエリアの手配度しか動かない**
  拒否   … 所持金が足りなければ何も動かない。所持金が読めなくても何も動かない
  取消   … 「やめておく」で元の選択肢に戻り、値は1つも動かない
  安全   … 手配度を下げる方向には動かさない。書けなかったら金も取らない
  共存   … `301_` / `302_` / `305_` / `307_` と印のキーが衝突していない
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
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
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_office_pardon")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
#: 実セーブの形（`player_data.area_history`。GAME.md §2.20）。
OFFICE_TYPE = "administrative_office"
EXIT_TEXT = "出る"
TALK_TEXT = "会話する"


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    """自前ボタンに持たせる無害な spec の相手。mod 無しで押されても害が無い。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        self.app.harmless += 1
        return None


class MovePhaseManager:
    """施設の出口。**施設の選択肢である目印**として mod が spec で見る。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class DisplayTalkChoice:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class Facility:
    def __init__(self, facility_id, name, facility_type):
        self.id = facility_id
        self.name = name
        self.facility_type = facility_type


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, name, facilities):
        self.id = area_id
        self.name = name
        self.nodes = {"0": Node(facilities)}


class World:
    def __init__(self, areas):
        self.areas = areas
        self.name = "テスト世界"


class Player:
    """`area_history` はエリア id ごとに1件（実セーブの形）。"""

    def __init__(self, area, location, history, gold):
        self.name = "テストプレイヤー"
        self.current_area = area
        self.location = location
        self.area_history = history
        self.gold = gold


class InstantaleApp:
    def __init__(self, world, player):
        self.world = world
        self.player = player
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.harmless = 0
        self.refreshes = 0
        self.ui_updates = 0
        self.process_choice_calls = []
        self.pressed_by_game = []
        self.is_button_enabled = True
        self.is_adding_text = False
        self.is_popup_window_opened = False
        self.in_battle = False
        self.in_conversation = False
        self.in_shopping = False
        self.hud = HUD_CLS()

    # -- ゲーム自身の入口 --------------------------------------------------
    def add_text(self, context):
        self.texts.append(context)

    def update_ui(self, *args):
        self.ui_updates += 1

    def process_choice(self, function, choice_text=""):
        self.process_choice_calls.append((type(function).__name__, choice_text))
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def display_button_load(self, dt):
        return None

    def on_button_press(self, button_index):
        """ゲーム本来の押下処理。**spec からマネージャを組んで process_choice に渡す。**"""
        entry = self.buttons[button_index]
        text = entry.get("text")
        self.pressed_by_game.append(text)
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), text)

    # -- テスト用の道具 ----------------------------------------------------
    def facility_screen(self, extra_talk=True):
        """施設に着いたときの選択肢（ゲームが組むもの）。"""
        buttons = []
        if extra_talk:
            buttons.append({"text": TALK_TEXT,
                            "spec": PhaseSpec("DisplayTalkChoice", [])})
        buttons.append({"text": EXIT_TEXT,
                        "spec": PhaseSpec("MovePhaseManager", ["0", "1", "0"])})
        self.buttons = buttons
        self.refresh_choice_buttons(reset_page=True)
        return self.buttons

    def press(self, text):
        """画面に出ている文字列でボタンを押す。無ければ AssertionError。"""
        for index, entry in enumerate(self.buttons):
            if entry.get("text") == text:
                return self.on_button_press(index)
        raise AssertionError("no button {!r} in {}".format(
            text, [e.get("text") for e in self.buttons]))

    def labels(self):
        return [entry.get("text") for entry in self.buttons]


BASES = {"app": InstantaleApp}


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

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="office_pardon_mod"):
    """本番と同じ形（**パッケージとして**）読み込む。

    `submodule_search_locations` を渡すのも、`exec_module` の**前に**
    `sys.modules` へ登録するのもローダと同じ（`_load_mod_file`）。これが
    無いと mod の中の `from . import record` が落ちる。
    """
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


HUD_CLS = install_fake_hud()
CLOCK = install_fake_kivy()

LOG_PATH = os.path.join(OUT_DIR, "office_pardon.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(configure=None, lawfulness=-3, gold=100000, facility_type=OFFICE_TYPE,
          history=None):
    """mod を適用し、役場に立っている app を返す。

    クラスは毎回作り直す（前のテストで載せたフックを持ち越さない）。
    """
    app_cls = type("InstantaleApp", (BASES["app"],), {})

    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.MovePhaseManager = MovePhaseManager
    main.DisplayTalkChoice = DisplayTalkChoice
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec

    os.makedirs(OUT_DIR, exist_ok=True)
    # ログは追記なので、消しておかないと前回の実行の行を数えてしまう。
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)

    office = Facility("6", "泥濘の徴収所", facility_type)
    other = Facility("7", "余所の役場", OFFICE_TYPE)
    here = Area("0", "始まりの泥濘", [office])
    away = Area("1", "灰の街道", [other])
    world = World({"0": here, "1": away})
    if history is None:
        history = {"0": {"residency": {"total_days": 9}, "achievements": [],
                         "lawfulness": lawfulness},
                   "1": {"residency": {"total_days": 1}, "achievements": [],
                         "lawfulness": -5}}
    player = Player(here, office, history, gold)

    module = load_mod()
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.refresh_choice_buttons", app_cls,
         "refresh_choice_buttons"),
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
    ))
    app = app_cls(world, player)
    main.test_app = app
    app.facility_screen()
    CLOCK.settle()
    return module, ctx, app


def pardon_label(app):
    """mod が足したボタンの文字列。無ければ None。"""
    for entry in app.buttons:
        if entry.get(MARK_KEY):
            return entry.get("text")
    return None


MARK_KEY = "mod_pardon_action"


# ================================================================== 設置
print("\n[設置] 役場で手配されているときだけ出る")
module, ctx, app = setup()
label = pardon_label(app)
check("役場で手配されていればボタンが出る", label is not None, app.labels())
check("罰金が文字列に入る（1000G × 手配度3）",
      label is not None and "3,000" in label, label)
check("「出る」の手前に入る",
      app.labels().index(label) < app.labels().index(EXIT_TEXT), app.labels())
check("ゲーム自身のボタンは残っている",
      TALK_TEXT in app.labels() and EXIT_TEXT in app.labels(), app.labels())
check("to_display_buttons にも出る（orig の前に挿している）",
      label in app.to_display_buttons, app.to_display_buttons)
check("到着を1行知らせる",
      any("手配された者" in text for text in app.texts), app.texts)
check("apply() で例外を握り潰していない", not ctx.errors, ctx.errors)

before = len(app.buttons)
app.refresh_choice_buttons(reset_page=True)
app.refresh_choice_buttons(reset_page=True)
check("塗り直しても二重にならない", len(app.buttons) == before, app.labels())
check("知らせるのは1回だけ",
      sum(1 for text in app.texts if "手配された者" in text) == 1, app.texts)

print("\n[設置] 出さない場面")
_m, _c, guild = setup(facility_type="guild")
check("役場でなければ出ない", pardon_label(guild) is None, guild.labels())

_m, _c, clean = setup(lawfulness=10)
check("手配されていなければ出ない", pardon_label(clean) is None, clean.labels())

_m, _c, zero = setup(lawfulness=0)
check("手配度0（閾値ちょうど）でも出ない", pardon_label(zero) is None, zero.labels())

_m, _c, busy = setup()
busy.in_conversation = True
busy.buttons = []
busy.facility_screen()
check("会話中は出ない", pardon_label(busy) is None, busy.labels())

_m, _c, nomap = setup(history={"1": {"lawfulness": -5}})
check("そのエリアの記録が無ければ出ない", pardon_label(nomap) is None, nomap.labels())

_m, _c, broken = setup(history={"0": {"residency": {}, "achievements": []}})
check("手配度の項目が無ければ出ない", pardon_label(broken) is None, broken.labels())

_m, _c, noface = setup()
noface.buttons = [{"text": "クエスト掲示板",
                   "spec": PhaseSpec("DisplayQuestChoice", [])}]
noface.refresh_choice_buttons(reset_page=True)
check("施設の選択肢でなければ出ない（移動のボタンが無い）",
      pardon_label(noface) is None, noface.labels())

# ================================================================== 押下
print("\n[押下] 印で横取りする")
module, ctx, app = setup()
label = pardon_label(app)
entry = [e for e in app.buttons if e.get(MARK_KEY)][0]
check("spec に自前のクラス名を書かない",
      entry["spec"].to_dict()["cls_name"] == "JustSetButtonToNormalPhase",
      entry["spec"].to_dict())
app.press(label)
CLOCK.settle()
check("ゲーム側の押下処理へ流していない", app.harmless == 0, app.harmless)
check("process_choice に自前のフェーズを渡す",
      any(name == "PardonPhase" for name, _text in app.process_choice_calls),
      app.process_choice_calls)
check("確認の選択肢は2つ", len(app.buttons) == 2, app.labels())
check("金額つきの支払いボタンが出る",
      any("3,000ゴールドを納める" == text for text in app.labels()), app.labels())
check("やめる選択肢が出る", "やめておく" in app.labels(), app.labels())
check("確認画面も自前のクラス名を書かない",
      all(e["spec"].to_dict()["cls_name"] == "JustSetButtonToNormalPhase"
          for e in app.buttons),
      [e["spec"].to_dict() for e in app.buttons])
check("金額と手配度を画面に出す",
      any("手配度 3" in text for text in app.texts), app.texts)

# ================================================================== 取消
print("\n[取消] 何も動かさずに戻る")
app.press("やめておく")
CLOCK.settle()
check("元の選択肢に戻る",
      TALK_TEXT in app.labels() and EXIT_TEXT in app.labels(), app.labels())
check("手配が残っていればボタンも戻る", pardon_label(app) is not None, app.labels())
check("所持金は動かない", app.player.gold == 100000, app.player.gold)
check("手配度は動かない", app.player.area_history["0"]["lawfulness"] == -3,
      app.player.area_history["0"])

# ================================================================== 支払
print("\n[支払] 罰金を納める")
module, ctx, app = setup()
app.press(pardon_label(app))
CLOCK.settle()
app.press("3,000ゴールドを納める")
CLOCK.settle()
check("所持金が罰金ぶん減る", app.player.gold == 100000 - 3000, app.player.gold)
check("手配度が10に戻る", app.player.area_history["0"]["lawfulness"] == 10,
      app.player.area_history["0"])
check("**今いるエリア以外は動かない**",
      app.player.area_history["1"]["lawfulness"] == -5,
      app.player.area_history["1"])
check("記録の他の項目に触らない",
      app.player.area_history["0"].get("residency") == {"total_days": 9},
      app.player.area_history["0"])
check("所持金の表示を更新する", app.ui_updates == 1, app.ui_updates)
check("納めたことを画面に出す",
      any("3,000ゴールドを納めた" in text for text in app.texts), app.texts)
check("手配が解けたことを画面に出す",
      any("手配は取り消された" in text for text in app.texts), app.texts)
check("元の選択肢に戻る",
      TALK_TEXT in app.labels() and EXIT_TEXT in app.labels(), app.labels())
check("ボタンはもう出ない", pardon_label(app) is None, app.labels())
check("例外を握り潰していない", not ctx.errors, ctx.errors)

# ================================================================== 拒否
print("\n[拒否] 払えないなら何も動かさない")
module, ctx, app = setup(gold=2999)
app.press(pardon_label(app))
CLOCK.settle()
app.press("3,000ゴールドを納める")
CLOCK.settle()
check("所持金は動かない", app.player.gold == 2999, app.player.gold)
check("手配度は動かない", app.player.area_history["0"]["lawfulness"] == -3,
      app.player.area_history["0"])
check("断ったことを画面に出す",
      any("書き換える帳面はありません" in text for text in app.texts), app.texts)
check("罰金と手持ちを添える",
      any("手持ち 2,999" in text for text in app.texts), app.texts)
check("元の選択肢に戻る", pardon_label(app) is not None, app.labels())

print("\n[拒否] 所持金が読めないなら何も動かさない")
module, ctx, app = setup()
app.press(pardon_label(app))
CLOCK.settle()
app.player.gold = None
app.press("3,000ゴールドを納める")
CLOCK.settle()
check("手配度は動かない", app.player.area_history["0"]["lawfulness"] == -3,
      app.player.area_history["0"])
check("書き換えられなかったと出す",
      any("帳面は書き換えられなかった" in text for text in app.texts), app.texts)

print("\n[拒否] 押すまでの間に手配が消えていたら請求しない")
module, ctx, app = setup()
app.press(pardon_label(app))
CLOCK.settle()
app.player.area_history["0"]["lawfulness"] = 10
app.press("3,000ゴールドを納める")
CLOCK.settle()
check("所持金は動かない", app.player.gold == 100000, app.player.gold)
check("もう手配は無いと出す",
      any("手配はもう残っていない" in text for text in app.texts), app.texts)

# ================================================================== 金額
print("\n[金額] 設定で変えられる")


def configure(price=None, threshold=None, restore=None, announce=None):
    def apply_settings(module):
        if price is not None:
            module.PRICE_PER_POINT = price
        if threshold is not None:
            module.WANTED_THRESHOLD = threshold
        if restore is not None:
            module.RESTORE_TO = restore
        if announce is not None:
            module.ANNOUNCE_ON_ARRIVAL = announce
    return apply_settings


module, ctx, app = setup(configure=configure(price=250), lawfulness=-4)
check("1点あたりの額を変えると罰金も変わる（250 × 4）",
      "1,000" in (pardon_label(app) or ""), pardon_label(app))

module, ctx, app = setup(configure=configure(price=0))
check("0円設定なら無料の文言になる",
      pardon_label(app) == "手配を解いてもらう", pardon_label(app))
app.press(pardon_label(app))
CLOCK.settle()
app.press("0ゴールドを納める")
CLOCK.settle()
check("無料でも手配は解ける", app.player.area_history["0"]["lawfulness"] == 10,
      app.player.area_history["0"])
check("無料なら所持金は動かない", app.player.gold == 100000, app.player.gold)
check("無料なら「納めた」とは言わない",
      not any("ゴールドを納めた" in text for text in app.texts), app.texts)

module, ctx, app = setup(configure=configure(threshold=5), lawfulness=3)
check("閾値を上げると手配とみなす範囲が広がる（罰金は差の2点ぶん）",
      "2,000" in (pardon_label(app) or ""), pardon_label(app))

module, ctx, app = setup(configure=configure(restore=1))
app.press(pardon_label(app))
CLOCK.settle()
app.press("3,000ゴールドを納める")
CLOCK.settle()
check("戻す先も変えられる", app.player.area_history["0"]["lawfulness"] == 1,
      app.player.area_history["0"])

module, ctx, app = setup(configure=configure(restore=-10))
app.press(pardon_label(app))
CLOCK.settle()
app.press("3,000ゴールドを納める")
CLOCK.settle()
check("**手配度を下げる方向には動かさない**",
      app.player.area_history["0"]["lawfulness"] == -3,
      app.player.area_history["0"])

module, ctx, app = setup(configure=configure(announce=False))
check("知らせを切れる",
      not any("手配された者" in text for text in app.texts), app.texts)
check("切っても罰金のボタンは出る", pardon_label(app) is not None, app.labels())

# ============================================================== 記録の形
print("\n[記録] 辞書でなくても読み書きする")


class HistoryEntry(object):
    def __init__(self, lawfulness):
        self.lawfulness = lawfulness


module, ctx, app = setup(history={"0": HistoryEntry(-2)})
check("属性で持っていても読める", pardon_label(app) is not None, app.labels())
app.press(pardon_label(app))
CLOCK.settle()
app.press("2,000ゴールドを納める")
CLOCK.settle()
check("属性で持っていても書ける",
      app.player.area_history["0"].lawfulness == 10,
      app.player.area_history["0"].lawfulness)

module, ctx, app = setup(history={"0": {"lawfulness": True}})
check("True を手配度1と読まない", pardon_label(app) is None, app.labels())

module, ctx, app = setup(history={0: {"lawfulness": -1}})
check("id が数値でも引き当てる", pardon_label(app) is not None, app.labels())

# ================================================================== 共存
print("\n[共存] 他の mod と印が衝突しない")
marks = {}
for name in sorted(os.listdir(MODS_DIR)):
    entry_dir = os.path.join(MODS_DIR, name)
    manifest = os.path.join(entry_dir, "mod.json")
    if not os.path.isfile(manifest):
        continue
    with io.open(manifest, encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    path = os.path.join(entry_dir, entry)
    if not os.path.isfile(path):
        continue
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MARK = "):
                marks.setdefault(line.split("=", 1)[1].strip(), []).append(name)
                break
check("印のキーが他の mod と重なっていない",
      all(len(owners) == 1 for owners in marks.values()), marks)
check("この mod の印が一覧に入っている", '"{}"'.format(MARK_KEY) in marks
      or "'{}'".format(MARK_KEY) in marks, sorted(marks))

# ================================================================== 名乗り
print("\n[名乗り] mod.json とコードの既定値が一致する")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
fresh = load_mod(name="office_pardon_defaults")
for key, spec in manifest.get("settings", {}).items():
    check("既定値が一致: {}".format(key),
          getattr(fresh, key, object()) == spec.get("default"),
          (getattr(fresh, key, None), spec.get("default")))

# ================================================================== ログ
print("\n[ログ] 何が起きたか残る")
text = read_log()
check("ログが書かれている", bool(text.strip()), LOG_PATH)

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
