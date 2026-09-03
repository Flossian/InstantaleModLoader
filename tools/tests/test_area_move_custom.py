# -*- coding: utf-8 -*-
"""314_area_move_custom.py をゲーム抜きで通す。

    python tools/tests/test_area_move_custom.py

偽の app / PhaseSpec / AreaMoveCofirmation / AreaMoveManager を差し込み、
次を確認する。

  ラベル … 日数を設定した手段はテンプレートで表示し直す（`徒歩(14日)`）。
           馬車は**既定でも**日数を足す（`馬車(1000G・14日)`。素のゲームは
           馬車の所要日数をどこにも出さないため）。徒歩は素のままなら元のまま。
           spec と args には触らない。開き直しても表示は同じ（二重に化けない）
  日数   … `execute` の窓の間だけ `elapse_days` に渡る数が設定値になる。
           複数回呼ばれても合計が設定値を超えない。窓の外の日数送りには触らない。
           -1 の手段はゲームのまま
  料金   … ゲームが取った額を設定額に直す（前後の差分方式）。手持ちが
           「設定額以上・素の運賃未満」なら建て替えてから精算する。ゲームが
           取らなかったら建て替えは返す。float の所持金でも型が保たれる
  拒否   … 手持ちが設定額に満たないと、押した時点で断って移動を起こさない
  文言   … 窓の間の出発・到着の文言がテンプレートに置き換わる。待機表示の
           点には触らない。テンプレートが空ならゲームのまま
  語彙   … `mode` が実測値に当たらないときは表示・日数・料金・文言の
           **すべて**がゲームのまま（表示だけ変わる食い違いを作らない）

馬車の偽物は「運賃 1000G を `execute` の中で引き、
足りなければ乗せない」という **仮定**で作ってある。
実機の徴収箇所と時機は `217_probe_area_move` の実測待ち（GAME.md §2.18）。
仮定が外れていた場合に直すのは mod 側で、このテストの偽物は実測に合わせて書き直す。
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
from instantale_modloader import state as mstate       # noqa: E402


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


MOD = find_mod("_area_move_custom")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
#: 確認画面の実測の形（GAME.md §2.18）。
#: mode の実値も実測どおり。
WALK_TEXT = "徒歩(3ヵ月)"
CARRIAGE_TEXT = "馬車(1000G)"
WALK_MODE = "on_foot"
CARRIAGE_MODE = "coach"

#: 既定でも mod が馬車のボタンに足す表示（素のラベルには所要日数が無い）。
CARRIAGE_SHOWN = "馬車(1000G・14日)"
#: 料金を 300 に変えたときの表示（日数は素の 14 のまま）。
CARRIAGE_300 = "馬車(300G・14日)"

#: ゲーム自身が進める日数と運賃（徒歩 90 は実測。馬車の値は仮定）。
MOVE_DAYS = {WALK_MODE: 90, CARRIAGE_MODE: 14}
GAME_FARE = 1000


class Area:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name
        self.nodes = {}


class World:
    def __init__(self, areas):
        self.areas = areas
        self.name = "テスト世界"


class Player:
    def __init__(self, area):
        self.current_area = area
        self.gold = 5000


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class AreaMoveManager:
    """土地から土地への移動。**運賃の徴収は仮定**（モジュール docstring 参照）。"""

    #: 馬車が運賃を引かないビルドの想定（徴収が別の場所にあるケース）。
    charge = True
    #: 残高チェックに通っても移動そのものが成立しないケース。
    blocked = False

    def __init__(self, app, target_area_id, mode):
        self.app = app
        self.target_area_id = target_area_id
        self.mode = mode

    def execute(self, choice_text):
        if type(self).blocked:
            return None
        if self.mode == CARRIAGE_MODE and type(self).charge:
            if self.app.player.gold < GAME_FARE:
                self.app.add_text("路銀が足りない。")
                return None
            self.app.player.gold -= GAME_FARE
        self.app.moved.append((self.target_area_id, self.mode, choice_text))
        if self.mode == CARRIAGE_MODE:
            # 実測の文言（2026-08-17、217_）。
            # **金額はゲームの運賃で焼き込まれ、
            # mod が料金をいくらに直しても 1000 のまま**という実挙動を真似る。
            self.app.add_text("{}ゴールドを支払った。快適な旅だ...".format(GAME_FARE))
            # 支払い直後にゲームが所持金の表示を描き直す瞬間の値。
            # **ここが既に差し引き後の正しい値**でないと、
            # 「文言は500なのに画面は
            # 1000減っている」に見える（実機で踏んだ）。
            self.app.gold_after_payment = self.app.player.gold
            # 手掛かり（DEPART/ARRIVE_MARKS）のどれにも当たらない文言。
            self.app.add_text("並んで座る客たちの話を聞いた。")
        else:
            self.app.add_text("徒歩で目指す。長旅だ...")
        self.app.add_text(".")
        self.app.add_text("..")
        self.app.elapse_days(MOVE_DAYS.get(self.mode, 90))
        self.app.add_text("辿り着いた。")
        self.app.player.current_area = self.app.world.areas[str(self.target_area_id)]
        return None


class DoubleElapseMoveManager(AreaMoveManager):
    """日数送りを2回に割って呼ぶビルドの想定（予算方式の検証用）。"""

    def execute(self, choice_text):
        self.app.moved.append((self.target_area_id, self.mode, choice_text))
        self.app.add_text("徒歩で目指す。長旅だ...")
        self.app.elapse_days(50)
        self.app.elapse_days(40)
        self.app.add_text("辿り着いた。")
        self.app.player.current_area = self.app.world.areas[str(self.target_area_id)]
        return None


class CappedElapseMoveManager(AreaMoveManager):
    """外側（`307_` の予算）が素の 90 を減らした後の数が来る想定。

    素の値と違う数を距離補正で**増やしてはいけない**（307_ の上限を破る）。
    """

    def execute(self, choice_text):
        self.app.moved.append((self.target_area_id, self.mode, choice_text))
        self.app.add_text("徒歩で目指す。長旅だ...")
        self.app.elapse_days(14)
        self.app.add_text("辿り着いた。")
        self.app.player.current_area = self.app.world.areas[str(self.target_area_id)]
        return None


class AreaMoveCofirmation:
    """徒歩と馬車が並ぶ確認画面。"""

    walk_mode = WALK_MODE
    carriage_mode = CARRIAGE_MODE

    def __init__(self, app, target_area_id):
        self.app = app
        self.target_area_id = target_area_id

    def execute(self, choice_text):
        self.update_button_display()
        return None

    def update_button_display(self):
        cls = type(self)
        self.app.buttons = [
            {"text": WALK_TEXT,
             "spec": PhaseSpec("AreaMoveManager",
                               [self.target_area_id, cls.walk_mode])},
            {"text": CARRIAGE_TEXT,
             "spec": PhaseSpec("AreaMoveManager",
                               [self.target_area_id, cls.carriage_mode])},
            {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class InstantaleApp:
    def __init__(self, world):
        self.world = world
        self.player = Player(world.areas["7"])
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.moved = []
        self.gold_after_payment = None
        self.refreshes = 0
        self.day = 0
        self.elapsed = []

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


BASES = {"app": InstantaleApp, "confirm": AreaMoveCofirmation,
         "move": AreaMoveManager, "move2": DoubleElapseMoveManager,
         "move3": CappedElapseMoveManager}


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
        # `WorldStore(own=False)` が他 MOD の控えを組み立てるときに読む
        # （本物の ModContext と同じ属性名。TECH.md §3.11）。
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`307_` のテストと同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    # 読みは本物の `read_json` をそのまま借りる（`write_json` と同じ理由）。
    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="area_move_custom_mod"):
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
LOG_PATH = os.path.join(OUT_DIR, "area_move_custom.log")

# 325_road_opening の控え（`state/road_opening/<世界>.json`）。距離補正はこれを
# **読むだけ**なので、テストは本物と同じ場所に同じ形で置く。ファイル名の作り方は
# ローダの語彙（`state.world_filename`。揺れると「読めない」＝道が無い扱いになる）。
ROADS_DIR = os.path.join(OUT_DIR, "state", "road_opening")
ROADS_PATH = os.path.join(ROADS_DIR, mstate.world_filename("テスト世界"))


def write_roads(records):
    """325_ が開いた道の控えを作る。None で消す（＝325_ を入れていない状態）。"""
    if records is None:
        if os.path.exists(ROADS_PATH):
            os.remove(ROADS_PATH)
        return
    os.makedirs(ROADS_DIR, exist_ok=True)
    with open(ROADS_PATH, "w", encoding="utf-8") as fh:
        json.dump({"roads": records, "commissions": [], "pending": None},
                  fh, ensure_ascii=False)


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(configure=None, double_elapse=False, roads=None):
    """mod を適用し、移動の確認画面が開いた状態の app を返す。

    `roads` は 325_ が開いた道の控え（無ければファイルごと消す＝素の状態）。
    """
    write_roads(roads)
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    confirm_cls = type("AreaMoveCofirmation", (BASES["confirm"],), {})
    move_cls = type("AreaMoveManager",
                    (BASES["move2" if double_elapse == True else
                           "move3" if double_elapse == "capped" else "move"],),
                    {})

    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.AreaMoveCofirmation = confirm_cls
    main.AreaMoveManager = move_cls
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
        ("__main__:AreaMoveCofirmation.update_button_display", confirm_cls,
         "update_button_display"),
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
        ("__main__:AreaMoveManager.__init__", move_cls, "__init__"),
        ("__main__:AreaMoveManager.execute", move_cls, "execute"),
        ("__main__:InstantaleApp.elapse_days", app_cls, "elapse_days"),
        ("__main__:InstantaleApp.add_text", app_cls, "add_text"),
    ))

    world = World({"7": Area("7", "風鳴りの村"), "9": Area("9", "陽光の砦")})
    app = app_cls(world)
    main.current_app = app
    app.process_choice(confirm_cls(app, "9"), "陽光の砦")
    CLOCK.settle()
    return module, ctx, app, confirm_cls, move_cls


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
print("[既定] 素のゲームから何も変えない（馬車のボタンにだけ日数が足される）")
module, ctx, app, confirm_cls, move_cls = setup()
check("徒歩のボタンは元のまま", texts_of(app)[0] == WALK_TEXT, texts_of(app))
check("馬車のボタンは既定でも日数が出る", texts_of(app)[1] == CARRIAGE_SHOWN,
      texts_of(app))
check("「やめる」には触らない", texts_of(app)[2] == "やめる")
check("spec の args は元のまま",
      app.buttons[0]["spec"].args == ["9", WALK_MODE]
      and app.buttons[1]["spec"].args == ["9", CARRIAGE_MODE])
before = texts_of(app)
app.process_choice(confirm_cls(app, "9"), "陽光の砦")
CLOCK.settle()
check("開き直しても表示は同じ", texts_of(app) == before, texts_of(app))
press(app, WALK_TEXT)
check("徒歩の日数もゲームのまま（90日）", app.elapsed == [90], app.elapsed)
check("文言もゲームのまま", "徒歩で目指す。長旅だ..." in app.texts, app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[既定] 馬車の挙動はゲームのまま（表示に日数が足されるだけ）")
module, ctx, app, confirm_cls, move_cls = setup()
press(app, CARRIAGE_SHOWN)
check("日数はゲームの値のまま", app.elapsed == [14], app.elapsed)
check("運賃はゲームのまま 1000 引かれる", app.player.gold == 4000,
      app.player.gold)
check("馬車の文言も見た目はゲームのまま",
      "1000ゴールドを支払った。快適な旅だ..." in app.texts, app.texts)

print("[ラベル] 呼び名・料金・日数のフル指定")


def full(module):
    module.WALK_DAYS = 30
    module.COACH_DAYS = 7
    module.COACH_PRICE = 500
    module.COACH_NAME = "竜車"


module, ctx, app, confirm_cls, move_cls = setup(configure=full)
check("徒歩(30日)", texts_of(app)[0] == "徒歩(30日)", texts_of(app))
check("竜車(500G・7日)", texts_of(app)[1] == "竜車(500G・7日)", texts_of(app))

print("[ラベル] 料金だけ変える（日数はゲームのまま）")


def fare_only(module):
    module.COACH_PRICE = 300


module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
check("徒歩は元のまま", texts_of(app)[0] == WALK_TEXT, texts_of(app))
check("馬車は設定した料金と素の日数が出る", texts_of(app)[1] == CARRIAGE_300,
      texts_of(app))

print("[ラベル] 呼び名だけ変える（日数は素のまま）")


def rename_only(module):
    module.WALK_NAME = "踏破行"


module, ctx, app, confirm_cls, move_cls = setup(configure=rename_only)
check("徒歩の呼び名だけ差し替わる", texts_of(app)[0] == "踏破行(3ヵ月)",
      texts_of(app))

print("[語彙] mode が実測値に当たらないビルド（設定は変えてあるのに、何もしない）")


def unknown_modes(module):
    module.WALK_DAYS = 14
    module.COACH_PRICE = 300


module, ctx, app, confirm_cls, move_cls = setup(configure=unknown_modes)
confirm_cls.walk_mode = "walk_v2"
confirm_cls.carriage_mode = "coach_v2"
app.process_choice(confirm_cls(app, "9"), "陽光の砦")
CLOCK.settle()
check("ラベルはゲームのまま", texts_of(app)[:2] == [WALK_TEXT, CARRIAGE_TEXT],
      texts_of(app))
check("unknown mode がログに残る", "unknown mode" in read_log())
press(app, WALK_TEXT)
check("日数もゲームのまま（90日）", app.elapsed == [90], app.elapsed)
check("文言もゲームのまま", "徒歩で目指す。長旅だ..." in app.texts, app.texts)

# ================================================================ 日数
print("[日数] 徒歩を14日に変える")


def walk14(module):
    module.WALK_DAYS = 14


module, ctx, app, confirm_cls, move_cls = setup(configure=walk14)
check("ボタンに実際の日数が出る", texts_of(app)[0] == "徒歩(14日)", texts_of(app))
press(app, "徒歩(14日)")
check("elapse_days に 14 が渡る", app.elapsed == [14], app.elapsed)
check("暦も14日進む", app.day == 14, app.day)
check("目的地に着いている", app.player.current_area.id == "9")
check("窓の外の日数送りには触らない",
      (app.elapse_days(30) or app.elapsed[-1]) == 30, app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[日数] 2回に割って呼ばれても合計が設定値")
module, ctx, app, confirm_cls, move_cls = setup(configure=walk14,
                                                double_elapse=True)
press(app, "徒歩(14日)")
check("50+40 が 14+0 に切り詰まる", app.elapsed == [14, 0], app.elapsed)
check("暦は14日だけ進む", app.day == 14, app.day)

# ================================================================ 料金
print("[料金] 設定額300を1回で引き落とす（手持ち5000）")
module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
press(app, CARRIAGE_300)
check("移動が成立する", app.player.current_area.id == "9")
check("300だけ引かれる", app.player.gold == 4700, app.player.gold)
check("支払いの瞬間から差し引き後の値（1000引いて返す動きをしない）",
      app.gold_after_payment == 4700, app.gold_after_payment)
check("文言の金額も実際の引き落としに揃う",
      "300ゴールドを支払った。快適な旅だ..." in app.texts, app.texts)
check("ゲームの言う1000は出ない",
      not any("1000ゴールド" in t for t in app.texts), app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[料金] 手持ちが素の運賃未満でも乗れる（手持ち600・設定300）")
module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
app.player.gold = 600
press(app, CARRIAGE_300)
check("素の残高チェックに弾かれず移動できる", app.player.current_area.id == "9",
      app.texts)
check("300だけ引かれる", app.player.gold == 300, app.player.gold)
check("支払いの瞬間から差し引き後の値", app.gold_after_payment == 300,
      app.gold_after_payment)

print("[料金] ラベルを組み直さないビルドでも素の運賃を見失わない")
module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
hook = ctx.hooks["__main__:AreaMoveCofirmation.update_button_display"]
hook(lambda self: None, confirm_cls(app, "9"))   # 組み直し無しでもう一度来る
CLOCK.settle()
check("自分の書いた300Gを読み直しても表示は同じ", texts_of(app)[1] == CARRIAGE_300,
      texts_of(app))
app.player.gold = 600
press(app, CARRIAGE_300)
check("前払い調整が素の運賃(1000)基準のまま効く",
      app.player.current_area.id == "9" and app.player.gold == 300,
      (app.player.gold, app.texts))

print("[料金] 前払いしたのに移動が成立しなかったら返す")
module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
move_cls.blocked = True
app.player.gold = 600
press(app, CARRIAGE_300)
check("移動していない", not app.moved, app.moved)
check("所持金が元に戻る", app.player.gold == 600, app.player.gold)
move_cls.blocked = False

print("[料金] 運賃を引かないビルドでは触らない")
module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
move_cls.charge = False
press(app, CARRIAGE_300)
check("移動は成立する", app.player.current_area.id == "9")
check("所持金には触らない", app.player.gold == 5000, app.player.gold)
move_cls.charge = True

print("[料金] float の所持金でも型が保たれる")
module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
app.player.gold = 5000.0
press(app, CARRIAGE_300)
check("float のまま 4700.0", app.player.gold == 4700.0
      and isinstance(app.player.gold, float), app.player.gold)

print("[拒否] 手持ちが設定額に満たない")
module, ctx, app, confirm_cls, move_cls = setup(configure=fare_only)
app.player.gold = 200
press(app, CARRIAGE_300)
check("移動を起こさない", not app.moved, app.moved)
check("所持金はそのまま", app.player.gold == 200, app.player.gold)
check("断りの一言が出る", any("足りない" in t for t in app.texts), app.texts)
check("確認画面はそのまま残る", texts_of(app)[0] == WALK_TEXT, texts_of(app))

# ================================================================ 文言
print("[文言] 出発・到着をテンプレートに")


def wording(module):
    module.WALK_DAYS = 10
    module.WALK_NAME = "船"
    module.WALK_DEPART_TEXT = "{name}で{target}へ向かう。{days}日の旅だ。"
    module.ARRIVE_TEXT = "{target}に着いた。"


module, ctx, app, confirm_cls, move_cls = setup(configure=wording)
press(app, "船(10日)")
check("出発の文言が置き換わる", "船で陽光の砦へ向かう。10日の旅だ。" in app.texts,
      app.texts)
check("元の出発の文言は出ない",
      not any("徒歩で目指す" in t for t in app.texts), app.texts)
check("到着の文言が置き換わる", "陽光の砦に着いた。" in app.texts, app.texts)
check("元の到着の文言は出ない", "辿り着いた。" not in app.texts, app.texts)
check("待機表示の点はそのまま", "." in app.texts and ".." in app.texts, app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[文言] テンプレートを空にすればゲームのまま（料金を変えていても）")


def muted_coach(module):
    module.COACH_PRICE = 300
    module.COACH_DEPART_TEXT = ""


module, ctx, app, confirm_cls, move_cls = setup(configure=muted_coach)
press(app, CARRIAGE_300)
check("空テンプレートなら文言はゲームのまま（金額のずれも承知の上）",
      "1000ゴールドを支払った。快適な旅だ..." in app.texts, app.texts)
check("到着もゲームのまま", "辿り着いた。" in app.texts, app.texts)
check("引き落としは設定額のまま", app.player.gold == 4700, app.player.gold)
check("手掛かりに当たらない文言は素通し",
      "並んで座る客たちの話を聞いた。" in app.texts, app.texts)
check("素通りした文言がログに残る", "text passing through" in read_log())

print("[文言] 窓の外の add_text には触らない")
module, ctx, app, confirm_cls, move_cls = setup(configure=wording)
app.add_text("船で目指す話をしている。")
check("窓の外は素通し", app.texts[-1] == "船で目指す話をしている。", app.texts)

# ================================================================ 距離補正
#: 325_ が「7 と 9 の間に挟む街2つ」の道を開いた控え（実物と同じ形）。
FAR_ROAD = [{"from": "7", "to": "9", "hops": 2, "via": "pay",
             "from_name": "風鳴りの村", "to_name": "陽光の砦"}]

print("[距離] 倍加（既定）: 挟む街2つで3倍。日数は上限（徒歩90・馬車30）で頭打ち")
module, ctx, app, confirm_cls, move_cls = setup(roads=FAR_ROAD)
check("徒歩は 270 が上限90に削られ、素の値と同じなので表示も素のまま",
      texts_of(app)[0] == WALK_TEXT, texts_of(app))
check("馬車は料金3倍・日数は 42 が上限30", texts_of(app)[1] == "馬車(3000G・30日)",
      texts_of(app))
press(app, WALK_TEXT)
check("徒歩の日数は素の 90 のまま（上限＝素の値）", app.elapsed == [90], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[距離] 上限を上げれば増える方向が効く（徒歩120・馬車60）")


def high_caps(module):
    module.WALK_DAYS_MAX = 120
    module.COACH_DAYS_MAX = 60


module, ctx, app, confirm_cls, move_cls = setup(configure=high_caps, roads=FAR_ROAD)
check("徒歩 270 -> 上限120", texts_of(app)[0] == "徒歩(120日)", texts_of(app))
check("馬車 42 は上限60の内側", texts_of(app)[1] == "馬車(3000G・42日)",
      texts_of(app))
press(app, "徒歩(120日)")
check("elapse_days が 90 -> 120 に増える", app.elapsed == [120], app.elapsed)
check("暦も120日進む", app.day == 120, app.day)
check("エラーなし", not ctx.errors, ctx.errors)

print("[距離] 倍加の馬車: 引き落としは1回で3000、日数は上限30")
module, ctx, app, confirm_cls, move_cls = setup(roads=FAR_ROAD)
press(app, "馬車(3000G・30日)")
check("移動が成立する", app.player.current_area.id == "9")
check("3000だけ引かれる", app.player.gold == 2000, app.player.gold)
check("支払いの瞬間から差し引き後の値", app.gold_after_payment == 2000,
      app.gold_after_payment)
check("日数は上限の30日", app.elapsed == [30] and app.day == 30,
      (app.elapsed, app.day))
check("文言の金額も3000",
      "3000ゴールドを支払った。快適な旅だ..." in app.texts, app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[距離] 手持ちが実効額に満たないと断る（3000 > 2500）")
module, ctx, app, confirm_cls, move_cls = setup(roads=FAR_ROAD)
app.player.gold = 2500
press(app, "馬車(3000G・30日)")
check("移動を起こさない", not app.moved, app.moved)
check("所持金はそのまま", app.player.gold == 2500, app.player.gold)
check("断りの一言に実効額が出る", any("3000" in t for t in app.texts), app.texts)

print("[距離] 加算: 1街ごとに7日・500G")


def hop_add(module):
    module.HOP_SCALING = "add"


module, ctx, app, confirm_cls, move_cls = setup(configure=hop_add, roads=FAR_ROAD)
check("徒歩 90+14=104 は上限90で素のまま", texts_of(app)[0] == WALK_TEXT,
      texts_of(app))
check("馬車 2000G・28日（上限30の内側）", texts_of(app)[1] == "馬車(2000G・28日)",
      texts_of(app))
press(app, "馬車(2000G・28日)")
check("elapse_days が 28", app.elapsed == [28], app.elapsed)

print("[距離] off にすれば補正なし")


def hop_off(module):
    module.HOP_SCALING = "off"


module, ctx, app, confirm_cls, move_cls = setup(configure=hop_off, roads=FAR_ROAD)
check("徒歩は素のまま", texts_of(app)[0] == WALK_TEXT, texts_of(app))
check("馬車は素の表示のまま", texts_of(app)[1] == CARRIAGE_SHOWN, texts_of(app))
press(app, WALK_TEXT)
check("日数も素のまま", app.elapsed == [90], app.elapsed)

print("[距離] 開いた道でなければ補正なし（控えはあるが別の区間）")
module, ctx, app, confirm_cls, move_cls = setup(
    roads=[{"from": "7", "to": "8", "hops": 3, "via": "pay"}])
check("徒歩は素のまま", texts_of(app)[0] == WALK_TEXT, texts_of(app))
check("馬車も素の表示のまま", texts_of(app)[1] == CARRIAGE_SHOWN, texts_of(app))
press(app, WALK_TEXT)
check("日数も素のまま", app.elapsed == [90], app.elapsed)
check("エラーなし", not ctx.errors, ctx.errors)

print("[距離] 外側(307_)が減らした日数は増やさない")
module, ctx, app, confirm_cls, move_cls = setup(configure=high_caps,
                                                roads=FAR_ROAD,
                                                double_elapse="capped")
press(app, "徒歩(120日)")
check("14 のまま通す（120 に膨らませない）", app.elapsed == [14], app.elapsed)
check("暦も14日だけ進む", app.day == 14, app.day)

# ================================================================ まとめ
print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
