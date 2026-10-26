# -*- coding: utf-8 -*-
"""336_crime_overhaul の選択肢を、ローダの窓口 `choices`（TECH.md §3.3.14 の宣言型の口）に載せて通す。

    python tools/tests/test_crime_choices.py

336 を丸ごと apply し、窓口の包み（組み直し・押下・保存）を偽のゲームに載せて流す。

  脱獄   … 服役の画面に「脱獄を試みる」。押すと一覧（MOD だけの画面）。一覧で保存するとセーブには年ごとの画面が残り、
           読み直しても一覧に戻る。「やめる」で年ごとの画面に戻る。年ごとの画面が替われば一覧は閉じる。
           一覧の間は牢の中だと窓口 `confinement` に知らせる
  事務所 … 公式の「裏の依頼掲示板」の枠を同じ場所で差し替える（ゲームのボタンは書き換えない。保存の間は元に戻る）。
           押すと裏の仕事の一覧（MOD だけの画面）。依頼のボタンの押下はゲームへ流す。
           作ってから 30 日経った仕事は、日数が進んだ後に取り下げる（受けている最中と片付けた仕事は除く）
  前の版 … 前の版の印（`mod_crime_incentive_action`）のボタンは、自前のものは外し、ゲームのボタンからは印だけ外す
"""
import importlib
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


class FakeClock(object):
    onces, intervals = [], []

    @staticmethod
    def schedule_once(fn, delay=0):
        FakeClock.onces.append(fn)

    @staticmethod
    def schedule_interval(fn, interval):
        FakeClock.intervals.append(fn)

    @staticmethod
    def tick():
        onces, FakeClock.onces = FakeClock.onces, []
        for fn in onces:
            fn(0)


kivy = types.ModuleType("kivy")
kivy_clock = types.ModuleType("kivy.clock")
kivy_clock.Clock = FakeClock
kivy.clock = kivy_clock
sys.modules.setdefault("kivy", kivy)
sys.modules["kivy.clock"] = kivy_clock

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import choices, confinement, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({})".format(detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


STATE = tempfile.mkdtemp(prefix="crime_choices_test_")
OUT = tempfile.mkdtemp(prefix="crime_choices_out_")
matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_overhaul"))
MOD_DIR = os.path.join(MODS_DIR, matches[0])


class Ctx(object):
    def __init__(self):
        self._mod = matches[0]
        self.mod_dir = MOD_DIR
        self.generation = "g1"
        self.hooks = {}
        self.errors = []
        self.state_dir = STATE
        self.lines = []

    def wrap(self, target, **kw):
        def deco(fn):
            self.hooks.setdefault(target, []).append(fn)
            return fn
        return deco

    def hook(self, target):
        return self.hooks[target][-1]

    def logger(self, name, **kw):
        return self.lines.append

    def log(self, line, **kw):
        self.lines.append(line)

    def log_exc(self, line):
        import traceback
        self.errors.append(line + "\n" + traceback.format_exc())

    def superseded(self):
        return False

    def on_ready(self, fn):
        pass

    def state_path(self, *parts):
        path = os.path.join(STATE, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def out_path(self, *parts):
        path = os.path.join(OUT, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        return ml.read_json(path, default)

    def write_json(self, path, data, indent=1):
        return ml.write_json(path, data, indent=indent)


class Spec(object):
    def __init__(self, cls_name, args=()):
        self.cls_name, self.args = cls_name, list(args)


main = sys.modules["__main__"]
main.PhaseSpec = Spec


class World(object):
    def __init__(self):
        self.characters = {"1": types.SimpleNamespace(name="誰か", config={}, relationship={})}
        self.world_name = "検査の世界"
        self.quests = {}


def make_app(ctx):
    app = types.SimpleNamespace()
    app.world = World()
    app.player = types.SimpleNamespace(name="主人公", location=None, inventory={}, gold=5000)
    app.save_data_dict = {"player_data": {"name": "主人公"}}
    app.party = ["player"]
    app.in_conversation = False
    app.to_display_buttons = []
    app.buttons = []
    app.pressed = []
    app.written = []

    def refresh_choice_buttons(reset_page=False):
        ctx.hook(choices.REFRESH_TARGET)(
            lambda self, *a, **k: setattr(app, "to_display_buttons", [b.get("text") for b in app.buttons]), app)

    def save_game():
        ctx.hook(choices.SAVE_TARGET)(lambda self: app.written.append([b.get("text") for b in app.buttons]), app)

    def press(index):
        return ctx.hook(choices.PRESS_TARGET)(lambda self, i: app.pressed.append(app.buttons[i].get("text")), app, index)

    app.refresh_choice_buttons = refresh_choice_buttons
    app.save_game = save_game
    app.press = press
    app.process_choice = lambda phase, text: app.pressed.append(("phase", type(phase).__name__, text))
    return app


def texts(app):
    return [b.get("text") for b in app.buttons]


def index_of(app, text):
    return next(i for i, b in enumerate(app.buttons) if str(b.get("text") or "").startswith(text))


choices.reset()
confinement.reset()
package = types.ModuleType("crime_overhaul_choices_parts")
package.__path__ = [MOD_DIR]
sys.modules[package.__name__] = package
entry = importlib.import_module(package.__name__ + ".crime_overhaul")
prison = importlib.import_module(package.__name__ + ".prison")
jailbreak = importlib.import_module(package.__name__ + ".jailbreak")
office = importlib.import_module(package.__name__ + ".office")
common = importlib.import_module(package.__name__ + ".common")
ctx = Ctx()
entry.apply(ctx)
app = make_app(ctx)
ui.find_app = lambda *a, **k: app
check("apply で例外が無い", not ctx.errors, ctx.errors)

# ================================================================ 脱獄
print("[脱獄] 服役の画面と、MOD だけの画面の一覧")
serve = {"text": jailbreak.SERVE_TEXT, "spec": Spec(jailbreak.SERVE_SPEC, [3, "窃盗", "店で盗んだ", 5])}
year = [serve]
app.buttons = list(year)
app.refresh_choice_buttons()
menu_label = jailbreak.MENU_LABEL.format(prep=0, max=entry.JAILBREAK_PREP_MAX)
check("服役の画面に「脱獄を試みる」（「服役する」の後ろ）", texts(app)[:2] == [jailbreak.SERVE_TEXT, menu_label], texts(app))
app.press(index_of(app, "脱獄を試みる"))
FakeClock.tick()
check("押すと一覧（備え・決行・やめる）", jailbreak.BACK_LABEL in texts(app) and jailbreak.SERVE_TEXT not in texts(app)
      and any(t.startswith("脱獄を決行する") for t in texts(app)), texts(app))
check("一覧の間は牢の中だと知らせる", bool(confinement.why(app)[0]), confinement.why(app))
app.save_game()
check("一覧で保存すると、セーブには年ごとの画面（「服役する」）が残る", app.written[-1] == [jailbreak.SERVE_TEXT],
      app.written)
# ロード: 世界が入れ替わり、セーブの一覧（年ごとの画面）が戻る。メモリの控えは消えた（起動し直した）形
app.world = World()
app.buttons = [{"text": jailbreak.SERVE_TEXT, "spec": Spec(jailbreak.SERVE_SPEC, [3, "窃盗", "店で盗んだ", 5])}]
choices._store()["opened"].clear()
app.refresh_choice_buttons()
check("読み直しても一覧に戻る", jailbreak.BACK_LABEL in texts(app) and jailbreak.SERVE_TEXT not in texts(app), texts(app))
app.press(index_of(app, jailbreak.BACK_LABEL))
FakeClock.tick()
check("「やめる」で年ごとの画面に戻る", texts(app)[:2] == [jailbreak.SERVE_TEXT, menu_label], texts(app))
check("戻った後は牢の中だと知らせない（年ごとの画面は服役のボタンで分かる）", not confinement.why(app)[0],
      confinement.why(app))
app.press(index_of(app, "脱獄を試みる"))
FakeClock.tick()
app.buttons[:] = []                         # ゲームが別の画面へ進んだ（年ごとの画面ではない）
app.buttons.append({"text": "出る", "spec": Spec("MovePhaseManager", ["1", "2", "3"])})
app.refresh_choice_buttons()
check("年ごとの画面が替われば一覧は閉じる", choices.opened(app) is None and texts(app) == ["出る"], texts(app))

# ================================================================ 事務所
print("[事務所] 公式の枠の差し替えと、裏の仕事の一覧")
facility = types.SimpleNamespace(id="9", facility_type=office.OFFICE_FACILITY_TYPE, owner="1", name="裏の事務所")
app.player.location = facility
board = {"text": office.OFFICIAL_BOARD_LABEL + "（※未実装）", "spec": Spec(office.NOT_IMPLEMENTED_SPEC)}
leave = {"text": "出る", "spec": Spec("MovePhaseManager", ["1", "2", "3"])}
unlocked = office.underworld.unlocked
office.underworld.unlocked = lambda weight, pct: [office.underworld.KINDS[0]]   # 種類が1つ解禁されている
try:
    app.buttons = [board, leave]
    app.refresh_choice_buttons()
    check("公式の枠を同じ場所で差し替える", texts(app) == [office.OFFICIAL_BOARD_LABEL, "出る"]
          and choices.ours(app.buttons[0]) and app.buttons[0]["spec"] is board["spec"], texts(app))
    check("ゲームのボタンは書き換えない", board["text"] == office.OFFICIAL_BOARD_LABEL + "（※未実装）", board)
    app.save_game()
    check("保存の間は元の枠に戻る", app.written[-1] == [office.OFFICIAL_BOARD_LABEL + "（※未実装）", "出る"], app.written)
    app.press(0)
    FakeClock.tick()
    check("押すと裏の仕事の一覧（探す・やめる）", texts(app) == [office.SEARCH_LABEL, office.BACK_LABEL], texts(app))
    app.save_game()
    check("一覧で保存すると、セーブには事務所の選択肢が残る",
          app.written[-1] == [office.OFFICIAL_BOARD_LABEL + "（※未実装）", "出る"], app.written)
    app.press(1)
    FakeClock.tick()
    check("「やめる」で事務所の選択肢に戻る", texts(app) == [office.OFFICIAL_BOARD_LABEL, "出る"], texts(app))
    with common._store()["worlds"].lock:
        key = common._store()["worlds"].playthrough(app)
        common._store()["worlds"].load(key)["underworld"] = {"77": {"kind": "smuggle", "area": ""}}
    app.world.quests = {"77": types.SimpleNamespace(quest_title="密輸の仕事", config={"status": "incomplete"})}
    ui_quest_of = ui.quest_of
    ui.quest_of = lambda a, qid: a.world.quests.get(str(qid))
    try:
        app.press(0)
        FakeClock.tick()
        check("片付けていない仕事はゲームの依頼のボタンの写しで並ぶ", texts(app)[0] == "密輸の仕事"
              and ui.spec_cls_name(app.buttons[0]) == office.QUEST_CHOICE_SPEC, texts(app))
        app.pressed[:] = []
        app.press(0)
        check("依頼のボタンの押下はゲームへ流す", app.pressed == ["密輸の仕事"], app.pressed)
    finally:
        ui.quest_of = ui_quest_of
    choices.close_screen(app)
finally:
    office.underworld.unlocked = unlocked

print("[事務所] 受注期限（作ってから {} 日で取り下げる）".format(office.DEADLINE_DAYS))
from instantale_modloader import durations  # noqa: E402


def days_after():
    """日数が進んだ後の処理（ローダの日数の関所に置いた 336 の登録）を呼ぶ。"""
    entry = next(entry for owner, entry in durations.DAYS_HOOKS.entries() if owner.endswith(":underworld"))
    entry["after"](types.SimpleNamespace(app=app, memo=None))


with common._store()["worlds"].lock:
    key = common._store()["worlds"].playthrough(app)
    bucket = common._store()["worlds"].load(key)
    bucket["underworld"] = {"90": {"kind": "smuggle", "area": "", "day": 100},       # 期限に届く
                            "91": {"kind": "smuggle", "area": "", "day": 101},       # 1日足りない
                            "92": {"kind": "smuggle", "area": "", "day": 100},       # 受けている最中
                            "93": {"kind": "smuggle", "area": ""}}                    # 版3までの控え（作った日が無い）
app.world.quests = {qid: types.SimpleNamespace(id=qid, quest_title="仕事" + qid, config={"status": "incomplete"})
                    for qid in ("90", "91", "92", "93")}
app.save_data_dict["quests"] = {qid: {"id": qid} for qid in app.world.quests}
app.world.days_elapsed = 130
app.current_quest_data = {"id": "92"}
ui_quest_of = ui.quest_of
ui.quest_of = lambda a, qid: a.world.quests.get(str(qid))
try:
    days_after()
    jobs = common._store()["worlds"].load(key)["underworld"]
    check("{} 日経った仕事は世界から片付け、控えからも外す".format(office.DEADLINE_DAYS),
          "90" not in jobs and "90" not in app.world.quests and "90" not in app.save_data_dict["quests"],
          (sorted(jobs), sorted(app.world.quests)))
    check("期限に届いていない仕事は残る", "91" in jobs and "91" in app.world.quests, sorted(jobs))
    check("受けている最中の仕事は取り下げない", "92" in jobs and "92" in app.world.quests, sorted(jobs))
    check("作った日の無い控えは、見た日を作った日にする", jobs.get("93", {}).get("day") == 130, jobs.get("93"))
    app.current_quest_data = None
    days_after()
    check("帰還した後の見回りで、期限の過ぎた仕事を取り下げる", "92" not in jobs and "92" not in app.world.quests
          and "91" in jobs and "93" in jobs, sorted(jobs))
    app.world.quests["91"].config["status"] = "completed"
    app.world.days_elapsed = 200
    days_after()
    check("片付けた仕事は取り下げない（帰還の側で片付く）", "91" in app.world.quests and "93" not in app.world.quests,
          sorted(app.world.quests))
finally:
    ui.quest_of = ui_quest_of
    app.current_quest_data = None
    del app.world.days_elapsed
    app.save_data_dict.pop("quests", None)
    with common._store()["worlds"].lock:
        common._store()["worlds"].load(key).pop("underworld", None)

print("[事務所] 依頼人の窓口と、片付いた依頼の控え")
from instantale_modloader import board as loader_board  # noqa: E402
from instantale_modloader import quest_clients  # noqa: E402
with common._store()["worlds"].lock:
    key = common._store()["worlds"].playthrough(app)
    bucket = common._store()["worlds"].load(key)
    bucket["underworld"] = {"77": {"kind": "smuggle", "area": "", "client": "1"},
                            "76": {"kind": "smuggle", "area": ""}}           # 版3までの控え（依頼人の id が無い）
    bucket["rescue"] = {"quest": "80", "area": "", "name": "手引き役", "title": "処刑場からの脱出", "client": "5"}
    common.remember_past(bucket, "78", "済んだ密輸", client="1")
    common.remember_past(bucket, "79", "前の周回の題", client="1")
check("片付いた依頼の控えに題と依頼人を残す",
      bucket[common.PAST_KEY]["78"] == {"title": "済んだ密輸", "client": "1", "name": ""}, bucket.get(common.PAST_KEY))
app.world.quests = {"77": types.SimpleNamespace(quest_title="密輸の仕事", config={"status": "incomplete"}),
                    "78": types.SimpleNamespace(quest_title="済んだ密輸", config={"status": "completed"}),
                    "79": types.SimpleNamespace(quest_title="別の依頼", config={"status": "completed"}),
                    # 版3までに片付けた裏の仕事（控えに無い）。依頼人の欄は事務所の主（人物 1「誰か」）の名前
                    "81": types.SimpleNamespace(quest_title="古い密輸", client_name="誰か", config={"status": "completed"}),
                    "82": types.SimpleNamespace(quest_title="表の依頼", client_name="別人", config={"status": "completed"}),
                    "83": types.SimpleNamespace(quest_title="受けていない依頼", client_name="誰か",
                                                config={"status": "incomplete"})}
app.world.areas = {"6": types.SimpleNamespace(nodes={"1": types.SimpleNamespace(facilities={"9": facility})})}
ui_quest_of = ui.quest_of
ui.quest_of = lambda a, qid: a.world.quests.get(str(qid))
try:
    rows = {row["quest"]: row for row in quest_clients.clients(app)}
    check("進行中の裏の仕事の依頼人（事務所の主）を置く", rows.get("77", {}).get("npc_id") == "1", rows)
    check("依頼人の id が無い控えは置かない", "76" not in rows, rows)
    check("処刑場からの脱出の手引き役を置く",
          rows.get("80", {}).get("npc_id") == "5" and rows["80"].get("npc_name") == "手引き役", rows)
    check("片付いた依頼の依頼人も置く", rows.get("78", {}).get("npc_id") == "1", rows)
    check("題の変わった id（作り直した周回）は置かない", "79" not in rows, rows)
    hidden = loader_board.kept_off(app)
    check("片付いた裏の仕事も掲示板に出さない依頼に数える", {"76", "77", "78", "80"} <= hidden and "79" not in hidden,
          sorted(hidden))
    check("控えに無い片付いた古い仕事（依頼人の欄が事務所の主）も数える", "81" in hidden, sorted(hidden))
    check("依頼人の欄がほかの人物の依頼と、片付いていない依頼は数えない", "82" not in hidden and "83" not in hidden,
          sorted(hidden))
    check("古い仕事の依頼人は事務所の主", rows.get("81", {}).get("npc_id") == "1", rows)
finally:
    del app.world.areas
    ui.quest_of = ui_quest_of
    with common._store()["worlds"].lock:
        for name in ("underworld", "rescue", common.PAST_KEY):
            bucket.pop(name, None)

# ================================================================ 前の版
print("[前の版] セーブから戻った前の版の印")
app.player.location = None
surrender = {"text": "大人しく捕まる", "spec": Spec("TrialStartManager", ["罪状", "出来事"]),
             common.MARK: "encounter:surrender"}
old = {"text": "盗品を売る", "spec": Spec(ui.SAFE_CLS), common.MARK: "fence:open"}
app.buttons = [surrender, old, {"text": "抵抗する！", "spec": Spec("BattleStartManager", ["guard", None])}]
app.refresh_choice_buttons()
check("前の版の印の自前のボタンは外し、ゲームのボタンからは印だけ外す",
      "盗品を売る" not in texts(app) and "大人しく捕まる" in texts(app) and common.MARK not in surrender, texts(app))
check("例外が無い", not ctx.errors, ctx.errors)

choices.reset()
confinement.reset()
shutil.rmtree(STATE, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)
print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
