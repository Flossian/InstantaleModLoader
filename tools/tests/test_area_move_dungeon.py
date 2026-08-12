# -*- coding: utf-8 -*-
"""307_area_move_dungeon.py をゲーム抜きで通す。

    python tools/tests/test_area_move_dungeon.py

偽の app / PhaseSpec / AreaMoveCofirmation / AreaMoveManager / DisplayQuestChoice /
QuestStart・End・RetireManager / HUD / Clock を差し込み、次を確認する。

  設置   … 移動の確認画面の「やめる」の手前に「危険な道を行く」が出る。
           開き直しても二重にならない。ゲームの徒歩・馬車のボタンには触らない
  押下   … 印で横取りしてゲームの経路（`process_choice`）に乗せる。
           `PhaseSpec` に自前クラス名を書かない
  難易度 … 移動元と移動先の**間**から選ばれ、生成にもクエスト辞書にも同じ値が入る
           （`world.quests` と `world_dict['quests']` の両方）
  生成   … `area_description` に道中の性質が足され、元の記述は残る
  受注   … ゲーム本来の受注画面（`QuestChoiceManager`）へ渡す
  踏破   … クリアしただけでは動かず、**集落の画面に戻ってから**移動が起きる
           （帰還直後に動くと「漁る」を取り上げてしまう）
  日数   … 道中と最後の移動の合計が `TRAVEL_DAYS`（既定14）を超えない。
           道の外の日数送り（訓練・休養）には触らない。着いたら切り詰めも終わる
  放棄   … 移動しない。出発地に留まる
  語彙   … `AreaMoveManager` の `mode` を発明せず、ゲームのボタンの args を写す
  控え   … `out/test/road_travel.json` に残り、**セーブには触らない**。
           注入し直し（apply の再実行）でも道中が消えない
  共存   … `301_` / `302_` / `305_` と印のキーが衝突していない
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
    return os.path.join(folder, entry)


MOD = find_mod("_area_move_dungeon")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
#: 移動元と移動先の難易度。`get_area_average_difficulty` の代わり。
AREA_LEVELS = {"7": 3, "9": 9}

#: 確認画面に並ぶゲーム自身のボタン（実測の形。GAME.md §2.18）。
WALK_TEXT = "徒歩(3ヵ月)"
CARRIAGE_TEXT = "馬車(1000G)"
WALK_MODE = "walk_mode_value"        # **中身は分からない**という前提の値
CARRIAGE_MODE = "carriage_mode_value"


class Area:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name
        self.nodes = {}


class Quest:
    def __init__(self, **kw):
        self.config = {"status": "incomplete"}
        self.__dict__.update(kw)


class World:
    def __init__(self, areas, quests):
        self.areas = areas
        self.quests = quests
        self.name = "テスト世界"


class Player:
    """体力（スタミナ）は `physical_integrity` / `max_physical_integrity`。

    道を行くたびに減り、最大HPもそれに連動して下がる。`current_hp` は戦闘の
    HP で別物（GAME.md §2.19）。
    """

    def __init__(self, area):
        self.current_area = area
        self.physical_integrity = 100
        self.max_physical_integrity = 100
        self.original_max_physical_integrity = 100
        self.exhausted = False
        self.current_hp = 1170
        self.max_hp = 1170


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


#: 徒歩と馬車が進める日数（実測の表示。`徒歩(3ヵ月)` / `馬車(1000G)`＝14日）。
MOVE_DAYS = {WALK_MODE: 90, CARRIAGE_MODE: 14}


class AreaMoveManager:
    """土地から土地への移動。**mod はこれを args ごと写して起こす。**

    日数は `app.elapse_days` を通す（`__main__` にある唯一の日数送りの入口）。
    mod がそこを頭打ちにするので、ここは素直に手段ぶんの日数を要求する。
    """

    def __init__(self, app, target_area_id, mode):
        self.app = app
        self.target_area_id = target_area_id
        self.mode = mode

    def execute(self, choice_text):
        self.app.moved.append((self.target_area_id, self.mode, choice_text))
        # 実機の並び: 出発の一言 → 待機の点 → 到着の合図（GAME.md §2.18）。
        self.app.add_text("徒歩で目指す。長旅だ...")
        self.app.elapse_days(MOVE_DAYS.get(self.mode, 90))
        self.app.add_text("辿り着いた。")
        self.app.player.current_area = self.app.world.areas[str(self.target_area_id)]
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class AreaMoveCofirmation:
    """徒歩と馬車が並ぶ確認画面。"""

    def __init__(self, app, target_area_id):
        self.app = app
        self.target_area_id = target_area_id

    def execute(self, choice_text):
        self.update_button_display()
        return None

    def update_button_display(self):
        self.app.buttons = [
            {"text": WALK_TEXT,
             "spec": PhaseSpec("AreaMoveManager", [self.target_area_id, WALK_MODE])},
            {"text": CARRIAGE_TEXT,
             "spec": PhaseSpec("AreaMoveManager",
                               [self.target_area_id, CARRIAGE_MODE])},
            {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class QuestChoiceManager:
    def __init__(self, app, quest_type, quest_id):
        if quest_type != "settlement_quest":
            # ゲームは他の語彙で KeyError を出す（`301_` が実際に落ちた）。
            raise KeyError(quest_id)
        self.app = app
        self.quest_type = quest_type
        self.quest_id = quest_id

    def execute(self, choice_text):
        self.app.accepted.append((self.quest_type, self.quest_id))
        return None


class QuestStartManager:
    """受注。**ゲームはここで `app.current_quest_data` を入れる**（GAME.md §2.9）。"""

    def __init__(self, app, quest_type, quest_id):
        self.app = app
        self.quest_type = quest_type
        self.quest_id = quest_id

    def execute(self, choice_text):
        self.app.started.append(self.quest_id)
        self.app.current_quest_data = self.app.world.quests[str(self.quest_id)]
        return None


class LootPhaseManager:
    """戦利品。**クエスト完了より前**にある（GAME.md §2.9）。"""

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.looted += 1
        return None


class QuestEndManager:
    """クエスト完了。**引数ゼロ**（GAME.md §2.9）。「帰還する」で起きる。

    実機ではこの中で帰還・報酬・才能まで済み、抜けた先はエリアの**入口**。
    入口の選択肢は隣の施設への `MovePhaseManager` だけで、会話相手も
    「他の土地へ行く」も無い。
    """

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.add_text("パーティは帰還した...")
        self.app.add_text("14354ゴールドの報酬を受け取った。")
        # 終わったクエストは片付けられる（mod は `orig` の前に読む）。
        self.app.current_quest_data = None
        self.app.buttons = [
            {"text": "霧の要塞都市 - 出口",
             "spec": PhaseSpec("MovePhaseManager", ["8", "63", "8"])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class QuestRetireManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.add_text("クエストを放棄した。")
        self.app.current_quest_data = None
        return None


class DisplayQuestChoice:
    """クエスト掲示板。生成の内側で `random_quest_generator` を呼ぶところまで真似る。"""

    next_title = "涸れ谷の隘路"
    next_summary = "涸れ谷を抜けて隣の土地へ至る。"
    #: ゲーム自身が付ける難易度。mod がこれを自分の値で上書きする。
    game_difficulty = 4
    #: 生成の直後に `world_dict['quests']` にも現れるか。
    #: **実機では現れなかった**（`1 store(s)`）ので、既定は False。
    in_both_stores = False

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.opened_board += 1
        return None

    def update_button_display(self):
        return None

    def generate_random_quest(self):
        if self.app.generator_hook is not None:
            self.app.generator_hook("世界の概要", "風鳴りの村", "静かな農村",
                                    "丘陵地帯", "風が吹き抜ける丘陵地帯。",
                                    type(self).game_difficulty)
        new_id = str(max(int(k) for k in self.app.world.quests) + 1)
        quest = Quest(id=new_id, quest_title=type(self).next_title,
                      request_summary=type(self).next_summary,
                      difficulty=type(self).game_difficulty,
                      neighboring_settlement_id="7")
        self.app.world.quests[new_id] = quest
        if type(self).in_both_stores:
            self.app.world_dict["quests"][new_id] = {
                "id": new_id, "quest_title": type(self).next_title,
                "request_summary": type(self).next_summary,
                "difficulty": type(self).game_difficulty}
        return quest

    def register_in_save(self, quest_id):
        """`world_dict['quests']` に後から現れる（実機の並びを真似る）。"""
        quest = self.app.world.quests[str(quest_id)]
        self.app.world_dict["quests"][str(quest_id)] = {
            "id": str(quest_id), "quest_title": quest.quest_title,
            "request_summary": type(self).next_summary,
            "difficulty": type(self).game_difficulty}


class InstantaleApp:
    def __init__(self, world):
        self.world = world
        self.world_dict = {"world_data": {"world_name": "テスト世界"},
                           "quests": {}}
        self.player = Player(world.areas["7"])
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.moved = []
        self.started = []
        self.accepted = []
        self.opened_board = 0
        self.harmless = 0
        self.refreshes = 0
        self.loaded = []
        self.process_choice_calls = []
        self.pressed_by_game = []
        self.generator_hook = None
        self.generated = []          # (area_description, difficulty)
        self.is_button_enabled = True
        self.is_adding_text = False
        self.is_popup_window_opened = False
        self.day = 0
        self.elapsed = []            # 実際にゲームが進めた日数
        self.looted = 0
        self.current_quest_data = None   # クエスト中だけ Quest が入る
        self.hud = HUD_CLS()

    def add_text(self, context):
        self.texts.append(context)

    def elapse_days(self, days):
        """`__main__` にある唯一の日数送りの入口。"""
        self.elapsed.append(days)
        self.day += days
        return None

    def process_choice(self, function, choice_text=""):
        self.process_choice_calls.append((type(function).__name__, choice_text))
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def display_button_load(self, dt):
        self.loaded.append(list(self.to_display_buttons))

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


BASES = {"app": InstantaleApp, "board": DisplayQuestChoice,
         "confirm": AreaMoveCofirmation, "move": AreaMoveManager,
         "start": QuestStartManager, "end": QuestEndManager,
         "retire": QuestRetireManager}


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


def install_fake_functions(levels):
    """`scripts.functions`。**難易度はゲーム自身のヘルパから来る**ことを確かめる用。"""
    name = "scripts.functions"
    module = types.ModuleType(name)
    module.calls = []

    def get_area_average_difficulty(area, world, include_completed=True, fallback=1):
        module.calls.append(getattr(area, "id", None))
        value = levels.get(str(getattr(area, "id", "")))
        if value is None:
            raise KeyError(getattr(area, "id", None))
        return value

    def get_quest_difficulties(area, world, include_completed=True):
        return []

    module.get_area_average_difficulty = get_area_average_difficulty
    module.get_quest_difficulties = get_quest_difficulties
    sys.modules[name] = module
    return module


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def state_path(self, *parts):
        """永続データの置き場。本番と同じく out/ とは**別のフォルダ**にする。"""
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    # 本物の `ctx.write_json` / `write_text` と同じものを使う。ここを自前の
    # open(..., "w") にすると、テストだけが「壊れない書き方」を通らなくなる。
    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="area_move_dungeon_mod"):
    """本番と同じ形（**パッケージとして**）読み込む。

    `submodule_search_locations` を渡すのも、`exec_module` の**前に**
    `sys.modules` へ登録するのもローダと同じ（`_load_mod_file`）。これが
    無いと mod の中の `from . import world` が「親パッケージが無い」で落ちる。
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

STATE_PATH = os.path.join(OUT_DIR, "state", "road_travel.json")
LOG_PATH = os.path.join(OUT_DIR, "road_travel.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(configure=None, levels=None, keep_state=False, mod=None):
    """mod を適用し、移動の確認画面の手前に立っている app を返す。

    クラスは毎回作り直す（前のテストで載せたフックを持ち越さない。派生元は
    `BASES` から引く ― `sys.modules['__main__']` は直接実行時にはこのテスト自身）。
    `keep_state=True` は控えのファイルを残す（注入し直しの検証用）。
    """
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    board_cls = type("DisplayQuestChoice", (BASES["board"],), {})
    confirm_cls = type("AreaMoveCofirmation", (BASES["confirm"],), {})
    move_cls = type("AreaMoveManager", (BASES["move"],), {})
    start_cls = type("QuestStartManager", (BASES["start"],), {})
    end_cls = type("QuestEndManager", (BASES["end"],), {})
    retire_cls = type("QuestRetireManager", (BASES["retire"],), {})

    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.DisplayQuestChoice = board_cls
    main.AreaMoveCofirmation = confirm_cls
    main.AreaMoveManager = move_cls
    main.QuestStartManager = start_cls
    main.QuestEndManager = end_cls
    main.QuestRetireManager = retire_cls
    main.QuestChoiceManager = QuestChoiceManager
    main.LootPhaseManager = LootPhaseManager
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec

    os.makedirs(OUT_DIR, exist_ok=True)
    # ログは追記なので、消しておかないと前回の実行の行を数えてしまう。
    for path in (LOG_PATH,) + ((STATE_PATH,) if not keep_state else ()):
        if os.path.exists(path):
            os.remove(path)

    install_fake_functions(levels if levels is not None else AREA_LEVELS)

    module = mod if mod is not None else load_mod()
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:AreaMoveCofirmation.update_button_display", confirm_cls,
         "update_button_display"),
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
        ("__main__:InstantaleApp.refresh_choice_buttons", app_cls,
         "refresh_choice_buttons"),
        ("__main__:QuestStartManager.__init__", start_cls, "__init__"),
        ("__main__:QuestEndManager.execute", end_cls, "execute"),
        ("__main__:QuestRetireManager.execute", retire_cls, "execute"),
        ("__main__:AreaMoveManager.__init__", move_cls, "__init__"),
        ("__main__:InstantaleApp.elapse_days", app_cls, "elapse_days"),
        ("__main__:InstantaleApp.add_text", app_cls, "add_text"),
    ))

    world = World({"7": Area("7", "風鳴りの村"), "9": Area("9", "陽光の砦")},
                  {"39": Quest(id="39", quest_title="水底の警備")})
    app = app_cls(world)
    app.world_dict["quests"] = {"39": {"id": "39"}}
    main.current_app = app

    raw = ctx.hooks.get(
        "scripts.llm.llm_manager_world_generate:random_quest_generator")

    def plain_generator(world_overview, settlement_name, settlement_overview,
                        structure, area_description, difficulty, *a, **kw):
        app.generated.append((area_description, difficulty))
        return {"quest_title": board_cls.next_title}

    def generator(*args, **kwargs):
        if raw is None:
            return plain_generator(*args, **kwargs)
        return raw(plain_generator, *args, **kwargs)

    app.generator_hook = generator
    return module, ctx, app, (confirm_cls, board_cls, end_cls, retire_cls,
                              start_cls, move_cls)


def show_confirmation(app, confirm_cls, target="9"):
    app.process_choice(confirm_cls(app, target), "陽光の砦")
    CLOCK.settle()


def index_of(app, text):
    for index, entry in enumerate(app.buttons):
        if entry.get("text") == text:
            return index
    return -1


def press(app, text):
    index = index_of(app, text)
    if index < 0:
        raise AssertionError("no such button: {!r} in {}".format(
            text, [b.get("text") for b in app.buttons]))
    app.on_button_press(index)
    CLOCK.settle()


def road_entry(app):
    for entry in app.buttons:
        if entry.get("text") == "危険な道を行く":
            return entry
    return None


def enter_settlement(app, entrance=False):
    """集落の選択肢が出ている状態にする。

    `entrance=True` は帰還先の**入口**（実機ではここに戻される）。隣の施設への
    移動しか無く、会話相手も「他の土地へ行く」も無い。
    """
    if entrance:
        app.buttons = [
            {"text": "霧の要塞都市 - 出口",
             "spec": PhaseSpec("MovePhaseManager", ["8", "63", "8"])},
        ]
    else:
        app.buttons = [
            {"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])},
            {"text": "他の土地へ行く", "spec": PhaseSpec("DisplayAreaMoveChoice", [])},
        ]
    app.refresh_choice_buttons(reset_page=True)
    CLOCK.settle()


def new_quest_id(app):
    ids = sorted(app.world.quests, key=lambda v: int(v))
    return ids[-1]


def run_road(app, classes, clear=True, quest_days=0):
    """出発 → 受注 → 開始 → 完了/放棄 → 集落へ戻る、を通す。

    `quest_days` は道中のクエストが進める日数（本物はイベントや戦闘で進む）。
    """
    confirm_cls, board_cls, end_cls, retire_cls, start_cls, _move_cls = classes
    show_confirmation(app, confirm_cls)
    press(app, "危険な道を行く")
    quest_id = new_quest_id(app)
    start_cls(app, "settlement_quest", quest_id).execute("受ける")
    CLOCK.settle()
    if quest_days:
        app.elapse_days(quest_days)
    if clear:
        # 実機の並び: 戦利品 → 帰還する（＝ QuestEndManager）。
        LootPhaseManager(app).execute("漁る")
        end_cls(app).execute("帰還する")
    else:
        retire_cls(app).execute("撤退する")
    CLOCK.settle()
    return quest_id


# ================================================================== 検証
print("=== 確認画面に足す ===")
mod, ctx, app, classes = setup()
confirm_cls = classes[0]
show_confirmation(app, confirm_cls)
texts = [entry.get("text") for entry in app.buttons]
check("「危険な道を行く」が出る", "危険な道を行く" in texts, texts)
check("「やめる」の手前に入る",
      texts.index("危険な道を行く") == texts.index("やめる") - 1, texts)
check("ゲームの徒歩・馬車のボタンは残る",
      texts[:2] == [WALK_TEXT, CARRIAGE_TEXT], texts)
entry = road_entry(app)
check("自前のクラス名を PhaseSpec に書かない",
      entry["spec"].to_dict()["cls_name"] == "JustSetButtonToNormalPhase",
      entry["spec"].to_dict())
check("印はこの mod のもの", entry.get("mod_road_action") == "road", entry)
check("画面も塗り直す", app.hud.painted and "危険な道を行く" in app.hud.painted[-1],
      app.hud.painted[-1:])
show_confirmation(app, confirm_cls)
check("開き直しても二重にならない",
      [t for t in [e.get("text") for e in app.buttons]
       if t == "危険な道を行く"] == ["危険な道を行く"],
      [e.get("text") for e in app.buttons])
check("例外を1つも出していない", not ctx.errors, ctx.errors)

# セーブから復元された残骸（印が落ちている）を掴めること。確認画面が一覧を
# 組み直すビルドではゲーム自身が消しているので**保険**だが、組み直さない
# ビルドで二重化しないことをここで担保する。
from instantale_modloader import ui as _ui
screen = _ui.Screen(ctx, lambda m: None, tag="t", mark=mod.MARK)
restored = [{"text": mod.ROAD_LABEL,
             "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
check("印の落ちた自前ボタンを残骸として掴む",
      screen.prune_stale(restored, mod.OUR_LABELS) == [mod.ROAD_LABEL], restored)
check("確認画面の掃除が仕掛けてある",
      "prune_stale(buttons, OUR_LABELS)" in open(MOD, encoding="utf-8").read())

# 他 MOD の生きているボタン（印のキーだけが違う）を巻き込まないこと。
foreign = [{"text": mod.ROAD_LABEL, "mod_pardon_action": "open",
            "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
check("他の MOD の印が付いたボタンは落とさない",
      screen.prune_stale(foreign, mod.OUR_LABELS) == [], foreign)
check("掃除に使う文言はこちらにしか無いものだけ",
      mod.OUR_LABELS == (mod.ROAD_LABEL,), mod.OUR_LABELS)
# ゲームの徒歩・馬車・やめるを巻き込まないこと（掃除は毎回この画面で走る）。
vanilla = [{"text": WALK_TEXT, "spec": PhaseSpec("AreaMoveManager", [])},
           {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
check("ゲームのボタンは1枚も落とさない",
      screen.prune_stale(vanilla, mod.OUR_LABELS) == [], vanilla)

print("=== 押すとゲームの経路に乗る ===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
kinds = [name for name, _text in app.process_choice_calls]
check("process_choice を通す", "RoadPhase" in kinds, app.process_choice_calls)
check("押しただけでは移動しない", app.moved == [], app.moved)
check("道中のクエストが1件できる", len(app.world.quests) == 2, app.world.quests)
check("受注画面へ渡す", app.accepted == [("settlement_quest", "40")], app.accepted)
check("例外も出ない", not ctx.errors, ctx.errors)

print("=== 体力が足りなければ断る ===")
mod, ctx, app, classes = setup()
app.player.physical_integrity = 32       # 3分の1を下回っている
app.player.exhausted = True
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("断る文言を出す",
      any("今は体力が無い" in text for text in app.texts), app.texts)
check("  → 体力の数字も出す",
      any("体力 32/100" in text for text in app.texts), app.texts)
check("**ダンジョンを生成しない**", len(app.world.quests) == 1, app.world.quests)
check("  → LLM も呼ばない", app.generated == [], app.generated)
check("  → 受注画面も開かない", app.accepted == [], app.accepted)
check("控えも作らない",
      json.load(open(STATE_PATH, encoding="utf-8"))["pending"] is None
      if os.path.exists(STATE_PATH) else True,
      "state file")
check("確認画面はそのまま（徒歩・馬車は選べる）",
      [e.get("text") for e in app.buttons][:2] == [WALK_TEXT, CARRIAGE_TEXT],
      [e.get("text") for e in app.buttons])
check("記録に残す", "refused: not enough stamina" in read_log(), read_log()[-400:])
check("例外も出ない", not ctx.errors, ctx.errors)

mod, ctx, app, classes = setup()
app.player.physical_integrity = 33       # ちょうど3分の1
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("ちょうど 33% なら行ける", len(app.world.quests) == 2, app.world.quests)

mod, ctx, app, classes = setup()
app.player.physical_integrity = 0
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("0 でも落ちない（断るだけ）", not ctx.errors, ctx.errors)
check("  → 生成しない", len(app.world.quests) == 1, app.world.quests)

mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "STAMINA_MIN_PERCENT", 0))
app.player.physical_integrity = 0
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("0% 設定なら体力を見ない", len(app.world.quests) == 2, app.world.quests)

mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "STAMINA_MIN_PERCENT", 80))
app.player.physical_integrity = 70
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("しきい値は設定で変えられる", len(app.world.quests) == 1, app.world.quests)

mod, ctx, app, classes = setup()
del app.player.physical_integrity        # 読めないビルド
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("**読めなければ通す**（遊びを止めない）", len(app.world.quests) == 2,
      app.world.quests)
check("  → WARN として残す", "cannot read physical_integrity" in read_log(),
      read_log()[-400:])

mod, ctx, app, classes = setup()
app.player.physical_integrity = 100
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("体力があれば普段どおり", len(app.world.quests) == 2, app.world.quests)
check("  → 体力を記録に残す", "stamina: 100/100 (100%)" in read_log(),
      read_log()[-600:])

print("=== 難易度は移動元と移動先の間 ===")
seen = set()
for _ in range(24):
    mod, ctx, app, classes = setup()
    show_confirmation(app, classes[0])
    press(app, "危険な道を行く")
    seen.add(app.generated[-1][1])
check("生成に渡す難易度が 3〜9 に収まる",
      seen and min(seen) >= 3 and max(seen) <= 9, sorted(seen))
check("同じ値ばかりではない（抽選している）", len(seen) > 1, sorted(seen))

mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
rolled = app.generated[-1][1]
check("クエストの difficulty に同じ値が入る",
      getattr(app.world.quests[quest_id], "difficulty") == rolled,
      (getattr(app.world.quests[quest_id], "difficulty"), rolled))
check("ゲーム自身の値（4）を上書きしている",
      rolled != 4 or "difficulty 4 -> 4" in read_log(), rolled)
check("生成直後は片方の格納先にしか居ないことを記録する",
      "only 1 of 1 store(s)" in read_log() or "only 1 of 2" in read_log()
      or "(1 store(s))" in read_log(), read_log()[-500:])
# 実機と同じ並び: セーブ側の辞書には後から現れる。受注の時点で揃える。
classes[1](app).register_in_save(quest_id)
classes[4](app, "settlement_quest", quest_id).execute("受ける")
CLOCK.settle()
check("**セーブ側の辞書にも**同じ値が入る（受注の時点で揃える）",
      app.world_dict["quests"][quest_id]["difficulty"] == rolled,
      app.world_dict["quests"][quest_id])
check("クエスト辞書に独自キーを足さない",
      set(app.world_dict["quests"][quest_id])
      <= {"id", "quest_title", "request_summary", "difficulty"},
      app.world_dict["quests"][quest_id])

mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "DIFFICULTY_MODE", "destination"))
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("destination なら移動先に合わせる", app.generated[-1][1] == 9, app.generated)

mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "DIFFICULTY_MODE", "harder"))
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("harder なら高いほう", app.generated[-1][1] == 9, app.generated)

mod, ctx, app, classes = setup(
    configure=lambda m: (setattr(m, "DIFFICULTY_MODE", "destination"),
                         setattr(m, "DIFFICULTY_OFFSET", 3)))
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("下駄が効く", app.generated[-1][1] == 12, app.generated)

mod, ctx, app, classes = setup(levels={})
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("難易度が引けなくても落ちない", not ctx.errors, ctx.errors)
check("  → 1 に落ちる", app.generated[-1][1] == 1, app.generated)
check("  → 落ちたことを WARN で残す",
      "no difficulty available" in read_log(), read_log()[-400:])

print("=== 依頼概要に移動先を明記する ===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
summary = getattr(app.world.quests[quest_id], "request_summary")
check("LLM が書いた本文は残る",
      summary.startswith("涸れ谷を抜けて隣の土地へ至る。"), summary)
check("末尾に移動先を明記する",
      "※このクエストをクリアすると「陽光の砦」に移動します。" in summary, summary)
check("記録に残す", "note: added the arrival line" in read_log(), read_log()[-500:])

# セーブ側の辞書に後から現れたぶんも、受注の時点で揃える。
classes[1](app).register_in_save(quest_id)
classes[4](app, "settlement_quest", quest_id).execute("受ける")
CLOCK.settle()
check("**セーブ側の辞書にも**入る",
      "移動します" in app.world_dict["quests"][quest_id]["request_summary"],
      app.world_dict["quests"][quest_id])
check("二重に足さない（受注でもう一度呼んでも1回だけ）",
      getattr(app.world.quests[quest_id], "request_summary").count("移動します") == 1,
      getattr(app.world.quests[quest_id], "request_summary"))

mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "NOTE_IN_SUMMARY", False))
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("設定を切れば足さない",
      "移動します" not in getattr(
          app.world.quests[new_quest_id(app)], "request_summary"),
      getattr(app.world.quests[new_quest_id(app)], "request_summary"))

print("=== 紐付けが切れたら文言も消す ===")


def summary_of(app, quest_id):
    return getattr(app.world.quests[quest_id], "request_summary")


mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
classes[1](app).register_in_save(quest_id)
classes[4](app, "settlement_quest", quest_id).execute("受ける")   # 揃えておく
CLOCK.settle()
classes[4](app, "settlement_quest", "39").execute("受ける")       # 別の依頼を受けた
CLOCK.settle()
check("別の依頼を受けたら文言を消す", "移動します" not in summary_of(app, quest_id),
      summary_of(app, quest_id))
check("  → LLM の本文は残す",
      summary_of(app, quest_id) == "涸れ谷を抜けて隣の土地へ至る。",
      summary_of(app, quest_id))
check("  → セーブ側からも消す",
      "移動します" not in app.world_dict["quests"][quest_id]["request_summary"],
      app.world_dict["quests"][quest_id])
check("  → 記録に残す", "note: removed the arrival line" in read_log(),
      read_log()[-500:])

mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
app.process_choice(classes[5](app, "9", WALK_MODE), WALK_TEXT)   # 普通に移動した
CLOCK.settle()
check("普通に移動したら文言を消す", "移動します" not in summary_of(app, quest_id),
      summary_of(app, quest_id))

mod, ctx, app, classes = setup()
run_road(app, classes, clear=False)                              # 放棄
check("放棄でも文言を消す",
      "移動します" not in summary_of(app, new_quest_id(app)),
      summary_of(app, new_quest_id(app)))

mod, ctx, app, classes = setup()
run_road(app, classes)                                           # 踏破して到着
check("**踏破して着いた道の文言は残す**（その時点では本当だった）",
      "移動します" in summary_of(app, new_quest_id(app)),
      summary_of(app, new_quest_id(app)))

mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
first = new_quest_id(app)
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
second = new_quest_id(app)
check("道を選び直したら前の道の文言を消す",
      "移動します" not in summary_of(app, first), summary_of(app, first))
check("  → 新しい道には付いている", "移動します" in summary_of(app, second),
      summary_of(app, second))
check("例外も出ない", not ctx.errors, ctx.errors)

mod, ctx, app, classes = setup()
classes[1].next_summary = 123             # 文字列でない依頼概要
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
check("文字列でない依頼概要には触らない",
      getattr(app.world.quests[new_quest_id(app)], "request_summary") == 123,
      getattr(app.world.quests[new_quest_id(app)], "request_summary"))
check("  → WARN として残す", "not appending" in read_log(), read_log()[-400:])
check("  → 例外は出さない", not ctx.errors, ctx.errors)

print("=== 生成プロンプト ===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
description = app.generated[-1][0]
check("元の記述は残る", description.startswith("風が吹き抜ける丘陵地帯。"), description[:60])
check("移動元と移動先が入る",
      "風鳴りの村" in description and "陽光の砦" in description, description[-300:])
check("道中が舞台だと指示する", "道のり" in description or "道中" in description,
      description[-300:])
check("印は1回で使い切る（掲示板の生成は素通し）",
      classes[1](app).generate_random_quest() is not None
      and "風鳴りの村" not in app.generated[-1][0], app.generated[-1][0][-200:])

print("=== 踏破したら着く ===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
classes[4](app, "settlement_quest", quest_id).execute("受ける")
CLOCK.settle()
check("受注しただけでは移動しない", app.moved == [], app.moved)
check("受注の時点で難易度を書き直す（生成直後は片方にしか居ないことがある）",
      "armed: difficulty" in read_log(), read_log()[-400:])
LootPhaseManager(app).execute("漁る")
check("戦利品はクエスト完了より前（取り上げていない）", app.looted == 1, app.looted)
classes[2](app).execute("帰還する")
CLOCK.settle()
check("**帰還処理が済んだその場で移動する**（街を歩き直させない）",
      len(app.moved) == 1, app.moved)
check("  → 目的地はゲームのボタンの args そのまま",
      app.moved[0][:2] == ("9", WALK_MODE), app.moved[0])
check("  → 現在地が移動先になる", app.player.current_area.id == "9",
      app.player.current_area.id)
check("移動は1回だけ（画面が塗り直されても再発しない）",
      (enter_settlement(app), len(app.moved))[1] == 1, app.moved)
check("例外も出ない", not ctx.errors, ctx.errors)

print("=== 完了の瞬間を捉え損ねたとき（保険の経路）===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
classes[4](app, "settlement_quest", quest_id).execute("受ける")
CLOCK.settle()
# ゲームを再起動して控えだけが残っている状態（`ready` から始まる）を作る。
mod2, ctx2, app2, classes2 = setup(keep_state=True)
state = json.load(open(STATE_PATH, encoding="utf-8"))
state["pending"]["stage"] = "ready"
json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
mod3, ctx3, app3, classes3 = setup(keep_state=True)
enter_settlement(app3, entrance=True)
check("**帰還先の入口でも拾える**（MovePhaseManager しか無い画面）",
      len(app3.moved) == 1, app3.moved)
check("例外も出ない", not ctx3.errors, ctx3.errors)

print("=== 注入し直しをまたいでも道が切れない ===")
# 実機で起きた並びの回帰。道中クエストの生成の最中に注入が入ると、生成を終えた
# **古い層**が控えを書き、**新しい層**はそれを持っていない ― 受注しても踏破しても
# 移動しなくなる（VERIFICATION.md §3.13）。
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
# ここで注入し直し（新しい層は控えをファイルから読む）。
mod2, ctx2, app2, classes2 = setup(keep_state=True)
app2.world.quests[quest_id] = app.world.quests[quest_id]
app2.world_dict["quests"][quest_id] = app.world_dict["quests"].get(quest_id, {})
classes2[4](app2, "settlement_quest", quest_id).execute("受ける")
CLOCK.settle()
LootPhaseManager(app2).execute("漁る")
classes2[2](app2).execute("帰還する")
CLOCK.settle()
check("注入し直した後でも受注が道と結び付く", app2.moved and app2.moved[0][0] == "9",
      app2.moved)

# さらに厳しい形: 受注そのものを取りこぼす（受注の瞬間に注入が挟まった場合）。
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
app.current_quest_data = app.world.quests[quest_id]   # ゲームは既に進行中
mod3, ctx3, app3, classes3 = setup(keep_state=True)
app3.world.quests[quest_id] = app.world.quests[quest_id]
app3.current_quest_data = app.world.quests[quest_id]
app3.refresh_choice_buttons(reset_page=True)          # 画面が組み直された
CLOCK.settle()
check("**受注の合図を取りこぼしても、進行中のクエストから拾い直す**",
      "the game is running quest" in read_log(), read_log()[-500:])
LootPhaseManager(app3).execute("漁る")
classes3[2](app3).execute("帰還する")
CLOCK.settle()
check("  → 踏破すれば移動する", app3.moved and app3.moved[0][0] == "9", app3.moved)
check("例外も出ない", not ctx3.errors, ctx3.errors)

mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
quest_id = new_quest_id(app)
app.current_quest_data = Quest(id="39", quest_title="別の依頼")
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
LootPhaseManager(app).execute("漁る")
classes[2](app).execute("帰還する")
CLOCK.settle()
check("別のクエストを進めていたなら拾わない", app.moved == [], app.moved)
check("  → その旨を残す", "not moving" in read_log(), read_log()[-400:])

print("=== 危険だが早い（日数の上限）===")
mod, ctx, app, classes = setup()
run_road(app, classes)
check("徒歩の3ヵ月がそのまま乗らない", app.day <= 14, app.elapsed)
check("上限ぶんは進む（14日）", app.day == 14, app.elapsed)
check("ゲームには減らした数を渡す（呼ばないのではなく）",
      app.elapsed == [14], app.elapsed)
check("移動そのものは起きている", app.moved and app.moved[0][0] == "9", app.moved)

mod, ctx, app, classes = setup()
run_road(app, classes, quest_days=4)
check("道中で使ったぶんも予算から引く", app.day == 14, app.elapsed)
check("  → 内訳は 4 + 10", app.elapsed == [4, 10], app.elapsed)
check("  → 切り詰めた記録が残る", "days: 90 -> 10" in read_log(), read_log()[-600:])

mod, ctx, app, classes = setup()
run_road(app, classes, quest_days=20)
check("道中だけで使い切ったら移動は0日", app.day == 14, app.elapsed)
check("  → 予算を超えた要求は 0 になる", app.elapsed == [14, 0], app.elapsed)

mod, ctx, app, classes = setup(configure=lambda m: setattr(m, "TRAVEL_DAYS", 0))
run_road(app, classes, quest_days=5)
check("上限 0 なら1日も進まない", app.day == 0, app.elapsed)
check("  → それでも着く", app.moved and app.moved[0][0] == "9", app.moved)

mod, ctx, app, classes = setup(configure=lambda m: setattr(m, "TRAVEL_DAYS", 365))
run_road(app, classes)
check("上限を上げれば素のまま通る", app.day == 90, app.elapsed)

print("=== 到着の文言 ===")
mod, ctx, app, classes = setup()
run_road(app, classes)
check("「徒歩で目指す。長旅だ...」を出さない",
      not any("長旅だ" in text for text in app.texts), app.texts)
check("  → 伏せたことを記録に残す", "muted while arriving" in read_log(),
      read_log()[-400:])
check("到着の合図（辿り着いた。）は残す",
      any("辿り着いた" in text for text in app.texts), app.texts)
check("道が抜けたことはこちらから出す",
      any("道は抜けた" in text for text in app.texts), app.texts)
app.add_text("徒歩で目指す。長旅だ...")
check("**道の外の同じ文言には触らない**（普通の徒歩移動）",
      any("長旅だ" in text for text in app.texts), app.texts)

mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "HIDE_TRAVEL_TEXT", False))
run_road(app, classes)
check("設定を切ればゲームの文言がそのまま出る",
      any("長旅だ" in text for text in app.texts), app.texts)

print("=== 道の外の日数には触らない ===")
mod, ctx, app, classes = setup()
app.elapse_days(90)
check("道を選んでいなければ素通し", app.elapsed == [90], app.elapsed)
run_road(app, classes)
before = app.day
app.elapse_days(90)
check("着いた後の日数送りも素通し（訓練・休養を巻き込まない）",
      app.day == before + 90, app.elapsed)
check("  → 控えはもう残っていない",
      json.load(open(STATE_PATH, encoding="utf-8"))["pending"] is None,
      open(STATE_PATH, encoding="utf-8").read())
check("到着を記録に残す", "arrived: '陽光の砦' reached" in read_log(),
      read_log()[-600:])

mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
app.elapse_days(30)
check("受注する前（道を選んだだけ）も素通し", app.elapsed == [30], app.elapsed)

print("=== 放棄したら着かない ===")
mod, ctx, app, classes = setup()
run_road(app, classes, clear=False)
check("移動しない", app.moved == [], app.moved)
check("引き返したと出す",
      any("引き返した" in text for text in app.texts), app.texts)
app.elapse_days(90)
check("放棄の後は日数も素通しに戻る", app.elapsed[-1:] == [90], app.elapsed)
check("例外も出ない", not ctx.errors, ctx.errors)

print("=== 受けなかった道 ===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
classes[4](app, "settlement_quest", "39").execute("受ける")   # 別の依頼を受けた
CLOCK.settle()
classes[2](app).execute("帰還する")
CLOCK.settle()
enter_settlement(app)
check("別のクエストを終えても移動しない", app.moved == [], app.moved)
check("  → 控えを捨てたと残す", "another quest started" in read_log(),
      read_log()[-400:])

print("=== 普通に移動したら道は忘れる ===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
app.process_choice(classes[5](app, "9", WALK_MODE), WALK_TEXT)
CLOCK.settle()
check("徒歩で行ったなら控えは残らない",
      json.load(open(STATE_PATH, encoding="utf-8"))["pending"] is None,
      open(STATE_PATH, encoding="utf-8").read())
check("  → その旨を残す", "travelled by other means" in read_log(),
      read_log()[-400:])

print("=== 到着に使う手段を選べる ===")
mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "ARRIVAL_MODE", "carriage"))
run_road(app, classes)
check("馬車のボタンの args を写す", app.moved and app.moved[0][1] == CARRIAGE_MODE,
      app.moved)

mod, ctx, app, classes = setup(
    configure=lambda m: setattr(m, "WALK_MODES", (WALK_MODE,)))
run_road(app, classes)
check("mode の実測値があればそれで選ぶ", app.moved and app.moved[0][1] == WALK_MODE,
      app.moved)
check("  → 選び方を残す", "via mode" in read_log(), read_log()[-600:])

print("=== 控え（out/ に持つ。セーブには触らない）===")
mod, ctx, app, classes = setup()
show_confirmation(app, classes[0])
press(app, "危険な道を行く")
saved = json.load(open(STATE_PATH, encoding="utf-8"))["pending"]
check("控えが out/ に残る", saved and saved["target_area_id"] == "9", saved)
check("  → 目的地の名前も持つ", saved.get("target_area_name") == "陽光の砦", saved)
check("  → ゲームの args をそのまま持つ",
      saved.get("args") == ["9", WALK_MODE], saved)
check("world_dict には道中の印を書かない",
      "road" not in json.dumps(app.world_dict, ensure_ascii=False),
      app.world_dict)

# 注入し直し（apply の再実行）でも道中が消えないこと。
quest_id = new_quest_id(app)
mod2, ctx2, app2, classes2 = setup(keep_state=True)
app2.world.quests[quest_id] = app.world.quests[quest_id]
check("注入し直しても控えが生き残る",
      "restored" in read_log(), read_log()[-400:])
classes2[4](app2, "settlement_quest", quest_id).execute("受ける")
CLOCK.settle()
LootPhaseManager(app2).execute("漁る")
classes2[2](app2).execute("帰還する")
CLOCK.settle()
check("  → その道を踏破すれば着く", app2.moved and app2.moved[0][0] == "9",
      app2.moved)

print("=== 他の mod との共存 ===")
mod = load_mod()
check("印のキーが 301_ / 302_ / 305_ と別",
      mod.MARK not in ("mod_action", "mod_party_action", "mod_mini_action"),
      mod.MARK)
check("quest_type は実測済みの語彙だけ", mod.QUEST_TYPE == "settlement_quest",
      mod.QUEST_TYPE)

print()
if failures:
    print("失敗 {} 件: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
