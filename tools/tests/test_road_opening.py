# -*- coding: utf-8 -*-
"""325_road_opening をゲーム抜きで通す。

    python tools/tests/test_road_opening.py

偽の app / World / PhaseSpec / DisplayAreaMoveChoice / AreaMoveCofirmation /
AreaMoveManager / DisplayQuestChoice / QuestStart・End・RetireManager / HUD / Clock を
差し込み、次を確認する。

  距離   … BFS で「間に挟む街の数」が数えられ、金額と難易度に加算される。
           孤立した街は ISOLATED_HOPS。開いた道も算入される
  一覧   … 「他の土地へ行く」の「やめる」の手前に「新たな道を探す」が出る。
           開き直しても二重にならない。候補は size が街のものだけで、
           現在地と既に繋がっている街は出ない
  お金   … 支払うと両側の connections に対称に足され、**新しい list に差し替わる**
           （骨格の辞書の list には触らない）。控えが書かれ、1行と功績が出て、
           一覧を開き直すとその街が並ぶ。足りなければ断る
  ロード … World.__init__ のあとに控えから当て直す。MOD を外せば素のまま
  踏破   … 道中のクエストが距離補正どおりの難易度で作られ、受注で本決まりになり、
           完了で道が開いてその街へ移動する（日数は ARRIVAL_DAYS）。放棄では開かない
  共存   … 印のキーと state のフォルダ名が他の MOD と重ならない
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


MOD = find_mod("_road_opening")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
#: 素の世界（9街の木 + 冒険エリア + エディタで足した孤立の街）。
#: connections は文字列 id の配列で双方向（実データ。HANDOVER §3）。
STOCK = {
    "0": ("開始の町", "town", ["1", "4", "7"]),
    "1": ("風鳴りの村", "village", ["0", "2"]),
    "2": ("陽光の砦", "town", ["1", "3"]),
    "3": ("霧の要塞都市", "city", ["2"]),
    "4": ("潮騒の村", "village", ["0", "5"]),
    "5": ("石切りの町", "town", ["4", "6"]),
    "6": ("黄金の砂漠", "city", ["5"]),
    "7": ("湖畔の町", "town", ["0", "8"]),
    "8": ("雪原の街", "town", ["7"]),
    "9": ("古い坑道", "dungeon", []),
    "21": ("雲上神殿", "village", []),
}

#: 街ごとの依頼の難易度（`get_quest_difficulties` の答え）。無い街は未訪問。
QUESTS = {"0": [2, 3, 4], "1": [4, 6], "2": [20, 25], "3": [50, 55], "7": [38, 40],
          "8": [68, 70]}


class Area:
    """実行時の `Area`。**`size` は持たない**（実機で読めない。GAME.md §2.7）。

    土地の種類は `app.world_dict["areas"][id]["size"]` から読む側を通す。
    `size=` を渡した個体だけ属性を持つ（純粋関数の検査用）。
    """

    def __init__(self, area_id, name, size, connections):
        self.id = area_id
        self.name = name
        if size is not None:
            self.size = size
        self.nodes = {}
        self.connections = connections   # 骨格と同じ list を共有させる（差し替えを確かめる）


class Quest:
    def __init__(self, **kw):
        self.config = {"status": "incomplete"}
        self.__dict__.update(kw)


def make_world_dict():
    return {
        "world_data": {"world_name": "テスト世界", "days_elapsed": 100},
        "areas": {aid: {"id": aid, "name": n, "size": s, "connections": list(c)}
                  for aid, (n, s, c) in STOCK.items()},
        "quests": {},
    }


class World:
    """`World.__init__(self, save_data_dict, app)`（targets.txt の実シグネチャ）。"""

    def __init__(self, save_data_dict, app):
        self.areas = {}
        for aid, raw in save_data_dict["areas"].items():
            # 実行時の list が骨格側と共有されている可能性を真似る。
            self.areas[aid] = Area(aid, raw["name"], None, raw["connections"])
        self.quests = {}
        self.name = save_data_dict["world_data"]["world_name"]
        self.days_elapsed = save_data_dict["world_data"]["days_elapsed"]


class Player:
    def __init__(self, area):
        self.current_area = area
        self.gold = 10000
        self.physical_integrity = 100
        self.max_physical_integrity = 100
        self.original_max_physical_integrity = 100
        self.area_history = {"0": {"residency": {}, "achievements": ["町を救った。"],
                                   "lawfulness": 10}}


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        self.app.harmless += 1
        return None


class DisplayAreaMoveChoice:
    """行き先の一覧。`connections` を読む（実測。HANDOVER §3）。"""

    #: ゲームが骨格の辞書（world_dict）から読むビルドを真似るか。
    from_skeleton = False

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.update_button_display()
        return None

    def update_button_display(self):
        current = self.app.player.current_area
        if type(self).from_skeleton:
            links = self.app.world_dict["areas"][current.id]["connections"]
        else:
            links = current.connections
        buttons = []
        for area_id in links:
            area = self.app.world.areas[str(area_id)]
            buttons.append({"text": area.name,
                            "spec": PhaseSpec("AreaMoveCofirmation", [str(area_id)])})
        buttons.append({"text": "やめる",
                        "spec": PhaseSpec("JustSetButtonToNormalPhase", [])})
        self.app.buttons = buttons
        self.app.refresh_choice_buttons(reset_page=True)


class AreaMoveCofirmation:
    def __init__(self, app, target_area_id):
        self.app = app
        self.target_area_id = target_area_id

    def execute(self, choice_text):
        self.app.confirmed.append(str(self.target_area_id))
        self.app.buttons = [
            {"text": "徒歩(3ヵ月)",
             "spec": PhaseSpec("AreaMoveManager", [self.target_area_id, "on_foot"])},
            {"text": "馬車(1000G)",
             "spec": PhaseSpec("AreaMoveManager", [self.target_area_id, "coach"])},
            {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class AreaMoveManager:
    #: 組めない（語彙が変わった）ビルドを真似るか。
    reject_mode = None

    def __init__(self, app, target_area_id, mode):
        if type(self).reject_mode is not None and mode == type(self).reject_mode:
            raise ValueError(mode)
        self.app = app
        self.target_area_id = target_area_id
        self.mode = mode

    def execute(self, choice_text):
        self.app.moved.append((str(self.target_area_id), self.mode, choice_text))
        self.app.add_text("徒歩で目指す。長旅だ...")
        self.app.elapse_days(90 if self.mode == "on_foot" else 14)
        self.app.add_text("辿り着いた。")
        self.app.player.current_area = self.app.world.areas[str(self.target_area_id)]
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class QuestChoiceManager:
    def __init__(self, app, quest_type, quest_id):
        if quest_type != "settlement_quest":
            raise KeyError(quest_id)
        self.app = app
        self.quest_id = quest_id

    def execute(self, choice_text):
        self.app.accepted.append(self.quest_id)
        return None


class QuestStartManager:
    def __init__(self, app, quest_type, quest_id):
        self.app = app
        self.quest_id = quest_id

    def execute(self, choice_text):
        self.app.current_quest_data = self.app.world.quests[str(self.quest_id)]
        return None


class QuestEndManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.add_text("パーティは帰還した...")
        self.app.current_quest_data = None
        self.app.buttons = [
            {"text": "町 - 出口", "spec": PhaseSpec("MovePhaseManager", ["0", "1", "0"])},
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
    next_title = "雲へ至る隘路"
    game_difficulty = 4

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.opened_board += 1
        return None

    def update_button_display(self):
        return None

    def generate_random_quest(self):
        if self.app.generator_hook is not None:
            self.app.generator_hook("世界の概要", "開始の町", "静かな町", "丘陵地帯",
                                    "風が吹き抜ける丘陵地帯。", type(self).game_difficulty)
        ids = [int(k) for k in self.app.world.quests] or [0]
        new_id = str(max(ids) + 1)
        quest = Quest(id=new_id, quest_title=type(self).next_title,
                      request_summary="道を切り開く。", difficulty=type(self).game_difficulty,
                      neighboring_settlement_id="0")
        self.app.world.quests[new_id] = quest
        return quest


class InstantaleApp:
    def __init__(self, world_dict):
        self.world_dict = world_dict
        self.world = World(world_dict, self)
        self.player = Player(self.world.areas["0"])
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.moved = []
        self.confirmed = []
        self.accepted = []
        self.opened_board = 0
        self.harmless = 0
        self.refreshes = 0
        self.process_choice_calls = []
        self.generator_hook = None
        self.generated = []
        self.is_button_enabled = True
        self.is_adding_text = False
        self.is_popup_window_opened = False
        self.elapsed = []
        self.current_quest_data = None
        self.ui_updates = 0
        self.pages = 0
        self.hud = HUD_CLS()

    def add_text(self, context):
        self.texts.append(context)

    def elapse_days(self, days):
        self.elapsed.append(days)
        self.world.days_elapsed += int(days)
        return None

    def update_ui(self, *args):
        self.ui_updates += 1

    def process_choice(self, function, choice_text=""):
        self.process_choice_calls.append((type(function).__name__, choice_text))
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def on_button_press(self, button_index):
        """ゲーム本来の押下。地図の枠が `'next'` ならページ送り（`206_` の記録。GAME.md §2.2）。"""
        mapping = getattr(self, "display_button_map", None)
        if isinstance(mapping, list) and 0 <= button_index < len(mapping)                 and not isinstance(mapping[button_index], int):
            self.pages += 1
            return None
        entry = self.buttons[button_index]
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), entry.get("text"))


BASES = {"app": InstantaleApp, "world": World, "choice": DisplayAreaMoveChoice,
         "confirm": AreaMoveCofirmation, "move": AreaMoveManager,
         "board": DisplayQuestChoice, "start": QuestStartManager,
         "end": QuestEndManager, "retire": QuestRetireManager}


class FakeClock:
    def __init__(self):
        self.intervals = []
        self.onces = []

    def schedule_interval(self, callback, timeout):
        self.intervals.append(callback)

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def tick(self):
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
            self.buttons = [types.SimpleNamespace(text="") for _ in range(6)]
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


def install_fake_functions(quests):
    name = "scripts.functions"
    module = types.ModuleType(name)

    def get_quest_difficulties(area, world, include_completed=True):
        return list(quests.get(str(getattr(area, "id", "")), []))

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
        self.ready = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def on_ready(self, fn, **kw):
        self.ready.append(fn)
        fn()
        return True

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="road_opening_mod"):
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

STATE_DIR = os.path.join(OUT_DIR, "state", "road_opening")
LOG_PATH = os.path.join(OUT_DIR, "road_opening.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def state_file():
    for name in sorted(os.listdir(STATE_DIR)) if os.path.isdir(STATE_DIR) else []:
        if name.endswith(".json"):
            with open(os.path.join(STATE_DIR, name), encoding="utf-8") as fh:
                return json.load(fh)
    return None


def setup(configure=None, keep_state=False, world_dict=None):
    """mod を適用し、開始の町に立っている app を返す。"""
    if hasattr(sys, "__instantale_road_opening_store__"):
        delattr(sys, "__instantale_road_opening_store__")
    classes = {key: type(base.__name__, (base,), {}) for key, base in BASES.items()}
    main = sys.modules["__main__"]
    main.InstantaleApp = classes["app"]
    main.World = classes["world"]
    main.DisplayAreaMoveChoice = classes["choice"]
    main.AreaMoveCofirmation = classes["confirm"]
    main.AreaMoveManager = classes["move"]
    main.DisplayQuestChoice = classes["board"]
    main.QuestStartManager = classes["start"]
    main.QuestEndManager = classes["end"]
    main.QuestRetireManager = classes["retire"]
    main.QuestChoiceManager = QuestChoiceManager
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
    if not keep_state and os.path.isdir(STATE_DIR):
        for name in os.listdir(STATE_DIR):
            os.remove(os.path.join(STATE_DIR, name))

    install_fake_functions(QUESTS)
    module = load_mod()
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    main.current_app = None
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:DisplayAreaMoveChoice.update_button_display", classes["choice"],
         "update_button_display"),
        ("__main__:InstantaleApp.on_button_press", classes["app"], "on_button_press"),
        ("__main__:InstantaleApp.refresh_choice_buttons", classes["app"],
         "refresh_choice_buttons"),
        ("__main__:InstantaleApp.add_text", classes["app"], "add_text"),
        ("__main__:InstantaleApp.elapse_days", classes["app"], "elapse_days"),
        ("__main__:World.__init__", classes["world"], "__init__"),
        ("__main__:QuestStartManager.__init__", classes["start"], "__init__"),
        ("__main__:QuestEndManager.execute", classes["end"], "execute"),
        ("__main__:QuestRetireManager.execute", classes["retire"], "execute"),
        ("__main__:AreaMoveManager.__init__", classes["move"], "__init__"),
    ))
    app = classes["app"](world_dict or make_world_dict())
    main.current_app = app

    raw = ctx.hooks.get("scripts.llm.llm_manager_world_generate:random_quest_generator")

    def plain_generator(world_overview, settlement_name, settlement_overview,
                        structure, area_description, difficulty, *a, **kw):
        app.generated.append((area_description, difficulty))
        return {"quest_title": classes["board"].next_title}

    def generator(*args, **kwargs):
        if raw is None:
            return plain_generator(*args, **kwargs)
        return raw(plain_generator, *args, **kwargs)

    app.generator_hook = generator
    return module, ctx, app, classes


def open_list(app, classes):
    app.process_choice(classes["choice"](app), "他の土地へ行く")
    CLOCK.settle()


def texts(app):
    return [b.get("text") for b in app.buttons]


def press(app, text):
    for index, entry in enumerate(app.buttons):
        if entry.get("text") == text or entry.get("text", "").startswith(text):
            app.on_button_press(index)
            CLOCK.settle()
            return
    raise AssertionError("no such button: {!r} in {}".format(text, texts(app)))


def links(app, aid):
    return list(app.world.areas[aid].connections)


# ================================================================ 距離補正
print("[距離]")
module = load_mod()
graph = module.build_graph({aid: Area(aid, n, s, list(c)) for aid, (n, s, c) in STOCK.items()})
check("hops 0->1 (adjacent) = 0", module.hops_between(graph, "0", "1") == 0)
check("hops 0->2 = 1", module.hops_between(graph, "0", "2") == 1)
check("hops 3->6 (leaf to leaf) = 5", module.hops_between(graph, "3", "6") == 5)
check("hops 3->8 = 4", module.hops_between(graph, "3", "8") == 4)
check("hops to an isolated town = None", module.hops_between(graph, "0", "21") is None)
check("hops to itself = None", module.hops_between(graph, "0", "0") is None)
check("price = base + per_hop * hops", module.price_for(3) == 2000 + 3000)
module.DIFFICULTY_MODE = "harder"
check("difficulty harder + hops", module.difficulty_for(3, 20, 2) == 20 + 4)
module.DIFFICULTY_MODE = "destination"
check("difficulty destination", module.difficulty_for(3, 20, 0) == 20)
check("difficulty destination unknown -> origin", module.difficulty_for(3, None, 1) == 5)
module.DIFFICULTY_MODE = "between"
import random as _random
rolled = {module.difficulty_for(3, 6, 0, _random.Random(i)) for i in range(40)}
check("difficulty between rolls inside [3, 6]", rolled and rolled <= {3, 4, 5, 6}, rolled)
check("difficulty both unknown -> minimum", module.difficulty_for(None, None, 0) == 1)
module.DIFFICULTY_OFFSET = 3
check("offset applies", module.difficulty_for(5, 5, 0) == 8)
module.DIFFICULTY_OFFSET = 0
shared = ["1"]
probe = Area("x", "x", "town", shared)
check("add_connection replaces the list", module.add_connection(probe, "2")
      and probe.connections == ["1", "2"] and shared == ["1"])
check("add_connection is idempotent", not module.add_connection(probe, "2"))
sizes = module.parse_sizes("village, Town,city")
check("parse_sizes", sizes == {"village", "town", "city"})
check("is_town by the runtime attribute", module.is_town(None, Area("9", "d", "dungeon", []), sizes) is False
      and module.is_town(None, Area("21", "v", "village", []), sizes))
fake_app = types.SimpleNamespace(world_dict={"areas": {"21": {"size": "village"}, "9": {"size": "dungeon"}}})
check("is_town falls back to world_dict when Area has no size (the real case)",
      module.is_town(fake_app, Area("21", "v", None, []), sizes)
      and not module.is_town(fake_app, Area("9", "d", None, []), sizes)
      and module.size_of(fake_app, Area("30", "x", None, [])) is None)
fake_app2 = types.SimpleNamespace(save_data_dict={"areas": {21: {"size": "City"}}}, world_dict={})
check("save_data_dict wins and int keys are matched", module.size_of(fake_app2, Area("21", "v", None, [])) == "city")
check("fmt survives unknown names", module.fmt("{a}/{typo}", a="x") == "x/{typo}")

# ================================================================ 一覧
print("[一覧]")
module, ctx, app, classes = setup()
check("self check passed", any("verified:" in m for _l, m in ctx.logs), ctx.logs[-3:])
open_list(app, classes)
check("search button before やめる",
      texts(app)[-2:] == ["新たな道を探す", "やめる"], texts(app))
check("game's destinations untouched",
      texts(app)[:3] == ["風鳴りの村", "潮騒の村", "湖畔の町"], texts(app))
check("search button carries the mark", app.buttons[-2].get("mod_road_opening") == "search")
check("search button uses the harmless spec",
      app.buttons[-2]["spec"].cls_name == "JustSetButtonToNormalPhase")
open_list(app, classes)
check("reopened list has one search button",
      texts(app).count("新たな道を探す") == 1, texts(app))

press(app, "新たな道を探す")
names = texts(app)
check("candidates are unlinked towns only (no dungeon, no neighbours, no self)",
      [n.split("（")[0] for n in names[:-1]] ==
      ["陽光の砦", "霧の要塞都市", "石切りの町", "黄金の砂漠", "雪原の街", "雲上神殿"], names)
check("last is やめる", names[-1] == "やめる")
by_name = {n.split("（")[0]: n for n in names[:-1]}
check("price grows with hops: 陽光の砦 1 hop", "3,000G" in by_name["陽光の砦"], by_name)
check("price grows with hops: 黄金の砂漠 2 hops", "4,000G" in by_name["黄金の砂漠"], by_name)
check("isolated town uses ISOLATED_HOPS (5)", "7,000G" in by_name["雲上神殿"], by_name)
check("candidate entries carry the target id",
      app.buttons[0].get("mod_road_opening_target") == "2")
log = read_log()
check("log names the levels", "levels origin=3 target=22" in log, log[-600:])
# 候補が8枠に収まらないとき、ゲームは最後の枠に「次」を出し、地図のその枠は 'next' になる。
# その押下は横取りせずゲームへ渡す（実機で 325 が 8 つ目の候補を押したことにしていた）。
app.display_button_map = list(range(7)) + ["next"]
app.on_button_press(7)
CLOCK.settle()
check("pressing the page-turn slot is passed to the game", app.pages == 1
      and texts(app)[0].startswith("陽光の砦"), (app.pages, texts(app)[:2]))
check("no means screen opened by the page turn", "means:" not in read_log().split("candidates:")[-1])
app.display_button_map = None
press(app, "やめる")
check("やめる is the game's harmless button", app.harmless == 1)

# ================================================================ お金
print("[お金]")
module, ctx, app, classes = setup()
skeleton_links = app.world_dict["areas"]["0"]["connections"]
open_list(app, classes)
press(app, "新たな道を探す")
press(app, "黄金の砂漠")
means = texts(app)
check("means: pay / dungeon / back", means[0].startswith("お金を支払い委託する（4,000G／14日）")
      and means[1].startswith("自ら切り拓く（難易度 ") and means[2] == "やめる", means)
app.player.gold = 3999
press(app, "お金を支払い")
check("refused when short of gold", any("足りない" in t for t in app.texts), app.texts)
check("still on the means screen after refusal", texts(app)[0].startswith("お金を支払い委託する"), texts(app))
check("no edge when refused", "6" not in links(app, "0"))
app.player.gold = 5000
app.texts = []
# 連打: 次のフレームで選択肢が差し替わる前に同じボタンをもう一度押す（実機で二重に徴収された）。
index = [i for i, b in enumerate(app.buttons) if b.get("text", "").startswith("お金を支払い委託する")][0]
app.on_button_press(index)
app.on_button_press(index)
CLOCK.settle()
check("gold deducted once for two presses", app.player.gold == 1000, app.player.gold)
check("second press refused as already commissioned", "already commissioned" in read_log()
      and any("委託済み" in t for t in app.texts), app.texts)
check("commission line names the days", any("開通まで 14日" in t for t in app.texts), app.texts)
check("no edge yet (commissioned, not opened)", "6" not in links(app, "0"))
saved = state_file()
check("commission recorded with the due day", saved["commissions"]
      and saved["commissions"][0]["to"] == "6" and saved["commissions"][0]["due_day"] == 114
      and not saved["roads"], saved)
check("no announce before the due day", not any("道が開かれた" in t for t in app.texts))
app.elapse_days(7)
check("7 days later still closed", "6" not in links(app, "0") and state_file()["commissions"])
app.texts = []
app.elapse_days(7)
CLOCK.settle()
check("edge added on both sides when the day comes", "6" in links(app, "0") and "0" in links(app, "6"),
      (links(app, "0"), links(app, "6")))
check("commission cleared", not state_file()["commissions"])
check("opened via commission", state_file()["roads"][0]["via"] == "commission"
      and state_file()["roads"][0]["price"] == 4000, state_file()["roads"])
check("skeleton list untouched (new list, not mutated)",
      skeleton_links == ["1", "4", "7"] and app.world_dict["areas"]["6"]["connections"] == ["5"],
      skeleton_links)
check("announce line", "開始の町と黄金の砂漠を結ぶ道が開かれた。" in app.texts, app.texts)
check("achievement written to the visited side only",
      app.player.area_history["0"]["achievements"][-1] == "開始の町と黄金の砂漠を結ぶ新しい道が開かれた。"
      and "6" not in app.player.area_history)
check("update_ui called", app.ui_updates == 1)
# 動作中の旗: フェーズが走っている間の自前ボタンの押下は捨てる
module_state = getattr(sys, "__instantale_road_opening_store__")["state"]
module_state["acting"] = True
before = len(app.process_choice_calls)
app.on_button_press(texts(app).index("新たな道を探す"))
module_state["acting"] = False
check("presses while acting are ignored", len(app.process_choice_calls) == before
      and "still running" in read_log())
saved = state_file()
check("state records the road", saved and saved["roads"] and saved["roads"][0]["from"] == "0"
      and saved["roads"][0]["to"] == "6" and saved["roads"][0]["day"] == 114, saved)
check("state has no pending", saved and saved.get("pending") is None)
open_list(app, classes)
check("move list shows the new road",
      "黄金の砂漠" in texts(app) and texts(app)[-2:] == ["新たな道を探す", "やめる"], texts(app))
press(app, "黄金の砂漠")
check("the new destination opens the game's confirmation", app.confirmed == ["6"])
# 距離は開いた道も算入する: 0-6 が繋がったので 5 は隣の隣に
open_list(app, classes)
press(app, "新たな道を探す")
names = {n.split("（")[0]: n for n in texts(app)[:-1]}
check("hops recomputed over the opened road (石切りの町 now 1 hop)", "3,000G" in names["石切りの町"], names)
check("黄金の砂漠 no longer a candidate", "黄金の砂漠" not in names)

# 世界の骨格から読むビルドでも、開いた道はこちらがボタンを足す
classes["choice"].from_skeleton = True
open_list(app, classes)
check("fallback lists the opened road when the game reads the skeleton",
      "黄金の砂漠" in texts(app) and "WARN list: the game did not list" in read_log(), texts(app))
entry = [b for b in app.buttons if b.get("text") == "黄金の砂漠"][0]
check("fallback button uses the game's own spec", entry["spec"].cls_name == "AreaMoveCofirmation"
      and entry["spec"].args == ["6"])
classes["choice"].from_skeleton = False

# 即時（PAY_DAYS=0）
module, ctx, app, classes = setup(configure=lambda m: setattr(m, "PAY_DAYS", 0))
open_list(app, classes)
press(app, "新たな道を探す")
press(app, "黄金の砂漠")
check("PAY_DAYS=0: label without days", texts(app)[0] == "お金を支払い委託する（4,000G）", texts(app))
app.player.gold = 5000
index = [i for i, b in enumerate(app.buttons) if b.get("text", "").startswith("お金を支払い委託する")][0]
app.on_button_press(index)
app.on_button_press(index)
CLOCK.settle()
check("PAY_DAYS=0: opened at once, charged once", app.player.gold == 1000
      and "6" in links(app, "0") and "0" in links(app, "6")
      and state_file()["roads"][0]["via"] == "pay" and "already linked" in read_log(),
      (app.player.gold, links(app, "0")))
check("PAY_DAYS=0: announce and achievement", any("道が開かれた" in t for t in app.texts)
      and app.player.area_history["0"]["achievements"][-1].endswith("新しい道が開かれた。"))
check("PAY_DAYS=0: list reopened with the road", "黄金の砂漠" in texts(app), texts(app))

# ================================================================ ロード
print("[ロード]")
module, ctx, app, classes = setup(keep_state=True)
check("fresh world starts stock (nothing applied at construction without hook)", True)
app2 = classes["app"](make_world_dict())
check("World.__init__ re-applies the road", "6" in links(app2, "0") and "0" in links(app2, "6"),
      (links(app2, "0"), links(app2, "6")))
check("load logged", "load: applied 2 edge(s)" in read_log(), read_log()[-400:])
plain = BASES["world"](make_world_dict(), None)   # 素の World（`main.World` は差し替え済み）
check("without the mod the world is stock", plain.areas["0"].connections == ["1", "4", "7"])
check("no errors so far", not ctx.errors, ctx.errors)

# ================================================================ 踏破
print("[踏破]")
module, ctx, app, classes = setup()
open_list(app, classes)
press(app, "新たな道を探す")
press(app, "霧の要塞都市")           # 3: 0-1-2-3 → 挟む街 2、適正 52
module.DIFFICULTY_MODE = "harder"
press(app, "自ら切り拓く")
check("quest generated with the road brief",
      app.generated and "霧の要塞都市" in app.generated[0][0] and "新しい道" in app.generated[0][0],
      app.generated)
expected = 52 + 2 * 2
check("difficulty = harder(3, 52) + 2 hops * 2", app.generated[0][1] == expected, app.generated)
qid = max(app.world.quests, key=int)
quest = app.world.quests[qid]
check("difficulty written to the quest", quest.difficulty == expected, quest.difficulty)
check("arrival note appended", "「開始の町」と「霧の要塞都市」を結ぶ道が開き" in quest.request_summary,
      quest.request_summary)
check("acceptance opened", app.accepted == [qid], app.accepted)
saved = state_file()
check("pending recorded as offered", saved["pending"]["stage"] == "offered"
      and saved["pending"]["target_id"] == "3", saved["pending"])
check("no road yet", "3" not in links(app, "0"))
# 受注
app.process_choice(classes["start"](app, "settlement_quest", qid), "受注")
check("armed on quest start", state_file()["pending"]["stage"] == "armed")
# 完了
app.texts = []
app.process_choice(classes["end"](app), "帰還する")
CLOCK.settle()
check("road opened on clear", "3" in links(app, "0") and "0" in links(app, "3"),
      (links(app, "0"), links(app, "3")))
check("announce on clear", any("開始の町と霧の要塞都市を結ぶ道が開かれた" in t for t in app.texts), app.texts)
check("no achievement on the dungeon route",
      app.player.area_history["0"]["achievements"] == ["町を救った。"])
check("moved to the target with the measured mode", app.moved == [("3", "on_foot", "霧の要塞都市")], app.moved)
check("arrival took ARRIVAL_DAYS", app.elapsed == [14], app.elapsed)
check("long-journey line muted, arrival kept",
      "徒歩で目指す。長旅だ..." not in app.texts and "辿り着いた。" in app.texts, app.texts)
check("player is at the target", app.player.current_area.id == "3")
saved = state_file()
check("pending cleared after arrival", saved["pending"] is None, saved)
check("road recorded via dungeon", saved["roads"][0]["via"] == "dungeon"
      and saved["roads"][0]["difficulty"] == expected and saved["roads"][0]["quest_id"] == qid, saved)
check("arrival note kept on the cleared quest", "※このクエストをクリアすると" in quest.request_summary)
check("no errors", not ctx.errors, ctx.errors)

# 放棄
print("[放棄]")
module, ctx, app, classes = setup()
open_list(app, classes)
press(app, "新たな道を探す")
press(app, "雪原の街")
press(app, "自ら切り拓く")
qid = max(app.world.quests, key=int)
app.process_choice(classes["start"](app, "settlement_quest", qid), "受注")
app.texts = []
app.process_choice(classes["retire"](app), "放棄")
CLOCK.settle()
check("no road on retire", "8" not in links(app, "0") and not app.moved)
check("retire line", any("開かれなかった" in t for t in app.texts), app.texts)
check("pending dropped", state_file()["pending"] is None)
check("arrival note stripped", "※このクエストをクリアすると" not in app.world.quests[qid].request_summary,
      app.world.quests[qid].request_summary)

# 体力
module, ctx, app, classes = setup()
app.player.physical_integrity = 20
open_list(app, classes)
press(app, "新たな道を探す")
press(app, "雪原の街")
press(app, "自ら切り拓く")
check("refused on low stamina", any("体力が無い" in t for t in app.texts) and not app.generated)
check("back on the means screen", texts(app)[0].startswith("お金を支払い委託する"), texts(app))

# 移動を組めないビルドは確認画面へ落とす
module, ctx, app, classes = setup()
classes["move"].reject_mode = "on_foot"
open_list(app, classes)
press(app, "新たな道を探す")
press(app, "雪原の街")
press(app, "自ら切り拓く")
qid = max(app.world.quests, key=int)
app.process_choice(classes["start"](app, "settlement_quest", qid), "受注")
app.process_choice(classes["end"](app), "帰還する")
CLOCK.settle()
check("fallback: road open and confirmation screen shown",
      "8" in links(app, "0") and app.confirmed == ["8"] and not app.moved, (app.confirmed, app.moved))
check("fallback: pending cleared", state_file()["pending"] is None)
classes["move"].reject_mode = None

# 別の手段で移動したら道中は捨てる
module, ctx, app, classes = setup()
open_list(app, classes)
press(app, "新たな道を探す")
press(app, "雪原の街")
press(app, "自ら切り拓く")
app.process_choice(classes["move"](app, "1", "on_foot"), "徒歩")
check("pending dropped when travelling by other means", state_file()["pending"] is None)

# ================================================================ 共存
print("[共存]")
others = {}
for name in os.listdir(MODS_DIR):
    folder = os.path.join(MODS_DIR, name)
    if name.startswith("325_") or not os.path.isdir(folder):
        continue
    for fname in os.listdir(folder):
        if fname.endswith(".py"):
            with io.open(os.path.join(folder, fname), encoding="utf-8") as fh:
                others[name + "/" + fname] = fh.read()
check("mark key unique", not any('"mod_road_opening"' in src for src in others.values()))
# 控えのフォルダを**自分の控えとして**持つのは 325_ だけ。読むだけの相手
# （`WorldStore(..., own=False)`。`314_` の距離補正が `roads` の hops を読む）は
# 同じ名前を書いてよい（TECH.md §3.2.3 の「同じファイルを読んで繋がる」）。
check("state dir unique",
      not any('"road_opening"' in src and "own=False" not in src
              for src in others.values()))

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all passed")
