# -*- coding: utf-8 -*-
"""914_real_estate をゲーム抜きで通す。

    python tools/tests/test_wip_real_estate.py

偽の app / Player / Area / Node / Facility / PhaseSpec / VacationStartManager /
Character / InventoryGrid / HUD / Clock を差し込み、次を確認する。

  窓口   … 役場でだけ「物件を扱う」が出る。宿屋では出ない。塗り直しても増えない
  契約   … 借りると所持金が減り、広場と建物の接続が両側に張られ、控えに1件残る
  一覧   … ゲームが並べなかった建物への道を、`MovePhaseManager` のボタンで足す
  建物   … 中に立つと「滞在する」「保管庫をあける」が出る。他人の施設では出ない
  滞在   … `VacationStartManager` をゲームの経路で起こし、**引かれた宿代を返す**。
           宿屋での宿泊（自分の建物の外）には手を出さない
  家賃   … 日数が進むと期限のぶんだけ引かれ、期限が延びる。何期ぶんか飛んでもまとめて払う
  期限   … 払えなければ契約が切れ、建物が街から消え、保管庫の中身は役場が預かる
  引取   … 役場で料金を払うと預かり品がプレイヤーの持ち物へ戻る
  ロード … `World.__init__` の後に建物が建ち直る（セーブには何も書かない）
  保管庫 … 窓の中で移した品が控えへ写り、`save_game` が走る
  安全   … 印は `mod_` で始まり、`PhaseSpec` に自前のクラス名を書かない
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


MOD = find_mod("_real_estate")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
WORLD_NAME = "テスト世界"
EXIT_TEXT = "出る"
TALK_TEXT = "会話する"
OFFICE_TYPE = "administrative_office"
INN_TYPE = "inn"

#: 宿屋が取る宿代（滞在で返されるのはこの額）。
ROOM_PRICE = 100


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


class MovePhaseManager:
    """施設の移動。`(app, connected_node_id, facility_move_to_id, area_id)`。"""

    def __init__(self, app, connected_node_id=None, facility_move_to_id=None,
                 area_id=None):
        self.app = app
        self.args = [connected_node_id, facility_move_to_id, area_id]

    def execute(self, choice_text):
        self.app.moved.append(list(self.args))
        return None


class DisplayTalkChoice:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class VacationStartManager:
    """宿泊の入口。**宿代を引き、日数を進める**（実測の順。GAME.md §2.17）。"""

    def __init__(self, app, months, quality):
        self.app = app
        self.months = months
        self.quality = quality

    def execute(self, choice_text=""):
        self.app.stays.append((self.months, self.quality))
        self.app.elapse_days(int(self.months) * 30)
        self.app.player.gold -= ROOM_PRICE
        if getattr(self.app, "stay_raises", False):
            # 主のいない施設で実機が踏んだ形（`ufl_method_1` の KeyError: None）。
            self.app.is_button_enabled = False
            raise KeyError(None)
        # ゲームは宿泊が始まると活動の選択肢へ差し替える（GAME.md §2.17）。
        self.app.buttons = [
            {"text": "休養をとる", "spec": PhaseSpec("DisplayTalkChoice", [])},
            {"text": "宿泊を終える", "spec": PhaseSpec("DisplayTalkChoice", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)
        return None


class VacationRestManager:
    """滞在の活動の1つ。日数も金も動かさない（GAME.md §2.17 の実測）。"""

    def __init__(self, app, months, quality):
        self.app = app
        self.months = months
        self.quality = quality

    def execute(self, choice_text=""):
        self.app.rests.append((self.months, self.quality))
        return None


class VacationEndManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.stay_ended += 1
        return None


class Facility:
    """`Facility(app, parent_node, facility_data)`（実測の署名）。"""

    def __init__(self, app, parent_node, facility_data):
        self.app = app
        self.parent_node = parent_node
        for key, value in facility_data.items():
            setattr(self, key, value)
        self.characters = []


class Node:
    def __init__(self, node_id):
        self.id = node_id
        self.facilities = {}


class Area:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name
        self.nodes = {}


class World:
    """`World(save_data_dict, app)`。ロードのたびに作り直される。"""

    def __init__(self, save_data_dict=None, app=None):
        self.name = WORLD_NAME
        self.areas = {}
        self.days_elapsed = 100
        self.characters = {}


class Inventory:
    def __init__(self):
        self.inventory = {}


class Character:
    """`scripts.characters.Character`。引数は全部既定値つきだが、**既定では組めない**。

    実機は `original_ability_scores` を添字で読むので、`None` のままだと
    `TypeError: 'NoneType' object is not subscriptable` で落ちる（GAME.md §2.23）。
    """

    ABILITY_KEYS = ("strength", "dexterity", "constitution", "intelligence",
                    "wisdom", "charisma")

    def __init__(self, name=None, id=None, original_ability_scores=None, **kwargs):
        for key in Character.ABILITY_KEYS:
            original_ability_scores[key]          # 鍵が無ければ KeyError
        self.name = name
        self.id = id
        self.original_ability_scores = original_ability_scores
        self.inventory = Inventory()
        self.equipments = {}
        self.gold = 0


class Item:
    FIELDS = ("name", "item_type", "attributes", "description", "value", "rarity",
              "skill", "upgrade_level", "width_slots", "height_slots", "image_src",
              "grid_pos")

    def __init__(self, data, item_id, obtainer):
        for field in Item.FIELDS:
            setattr(self, field, data.get(field))
        self.id = str(item_id)
        self.obtainer = obtainer
        self.unequipped = 0

    def unequip(self):
        self.unequipped += 1
        for slot, value in list(getattr(self.obtainer, "equipments", {}).items()):
            if value is self or str(value) == self.id:
                self.obtainer.equipments.pop(slot, None)


class InventoryGrid:
    def __init__(self, obtainer):
        self.obtainer = obtainer


class InventoryItem:
    """画面側の1マス。`change_inventory` は**登録を動かすだけ**（本体と同じ）。"""

    def __init__(self, item, grid):
        self.item_instance = item
        self.item_id = item.id
        self.inventory = grid

    def change_inventory(self, new_inventory):
        self.inventory = new_inventory
        return None


def sample_item(name="錆びた銛突き"):
    return {"name": name, "item_type": "weapon",
            "attributes": {"item_detail": "small_weapon", "攻撃力": 326},
            "description": "先端の欠けた銛。", "value": 74, "rarity": "common",
            "skill": None, "upgrade_level": 0, "width_slots": 1, "height_slots": 1,
            "image_src": "Assets/images/item_candidates_dark/small_weapon/263.png",
            "grid_pos": [1, 5]}


class Player:
    def __init__(self, area, location, gold):
        self.name = "テストプレイヤー"
        self.current_area = area
        self.location = location
        self.gold = gold
        self.inventory = Inventory()
        self.equipments = {}
        self.area_history = {}


class InstantaleApp:
    def __init__(self, world, player, world_dict, save_data_dict):
        self.world = world
        self.player = player
        self.world_dict = world_dict
        self.save_data_dict = save_data_dict
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.harmless = 0
        self.refreshes = 0
        self.ui_updates = 0
        self.moved = []
        self.stays = []
        self.rests = []
        self.stay_ended = 0
        self.saves = 0
        self.windows = []
        self.windows_from_clock = []
        self.backgrounds = []
        self.made_items = []
        self.process_choice_calls = []
        self.is_button_enabled = True
        self.stay_raises = False
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
        entry = self.buttons[button_index]
        text = entry.get("text")
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), text)

    def change_background_image_to_inn_room(self, quality):
        self.backgrounds.append(("room", quality))
        return None

    def change_background_image_to_current_location(self, *args):
        self.backgrounds.append(("current", None))
        return None

    def change_background_image_from_location_id(self, location_id):
        self.backgrounds.append(("location", str(location_id)))
        return None

    def elapse_days(self, days):
        self.world.days_elapsed += int(days)
        return None

    def save_game(self):
        self.saves += 1
        return None

    def generate_item_from_dict(self, item_dict, item_id, obtainer):
        item = Item(item_dict, item_id, obtainer)
        obtainer.inventory.inventory[str(item_id)] = item
        self.made_items.append((str(item_id), item.name))
        return item

    def toggle_twin_inventory_window(self, left, right, left_label_text, situation,
                                     *args):
        self.windows.append((left, right, left_label_text, situation))
        # 実機ではここで Kivy の描画に触る。Clock の外から来たら落ちる。
        self.windows_from_clock.append(bool(CLOCK.inside))
        return None

    def close_shopping_window_process(self):
        return None

    # -- テスト用の道具 ----------------------------------------------------
    def facility_screen(self, talk=True):
        """施設に着いたときの選択肢（ゲームが組むもの）。"""
        buttons = []
        if talk:
            buttons.append({"text": TALK_TEXT,
                            "spec": PhaseSpec("DisplayTalkChoice", [])})
        buttons.append({"text": EXIT_TEXT,
                        "spec": PhaseSpec("MovePhaseManager", ["0", "1", "0"])})
        self.buttons = buttons
        self.refresh_choice_buttons(reset_page=True)
        CLOCK.settle()
        return self.buttons

    def press(self, text):
        for index, entry in enumerate(self.buttons):
            if entry.get("text") == text:
                return self.on_button_press(index)
        raise AssertionError("no button {!r} in {}".format(text, self.labels()))

    def has(self, prefix):
        return any((entry.get("text") or "").startswith(prefix)
                   for entry in self.buttons)

    def label_like(self, prefix):
        for entry in self.buttons:
            if (entry.get("text") or "").startswith(prefix):
                return entry.get("text")
        return None

    def labels(self):
        return [entry.get("text") for entry in self.buttons]

    def go(self, facility):
        """その施設に立ってゲームの選択肢を組み直す。"""
        self.player.location = facility
        return self.facility_screen()


BASES = {"app": InstantaleApp, "world": World}


class FakeClock:
    """Kivy の Clock の代わり。**いま Clock の中か**を持つ。

    実機の Kivy は、描画に触る手を Clock（メインスレッド）以外から呼ぶと
    `Cannot change graphics instruction outside the main Kivy thread` を出す。
    偽ゲームは1スレッドなので、旗を立てて同じ境目を作る。
    """

    def __init__(self):
        self.intervals = []
        self.onces = []
        self.inside = False

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
                self.inside = True
                try:
                    callback(0.0)
                finally:
                    self.inside = False

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
            # 2枚並びの窓の右の見出し（ゲームでは「所持品」で固定）。
            self.right_header = types.SimpleNamespace(text="所持品", children=[])
            self.children = [self.right_header]

        def update_button_texts(self, instance, value):
            self.painted.append(list(value))

    module.InstanTaleHUD = InstanTaleHUD
    module.InventoryItem = InventoryItem
    sys.modules[name] = module
    return InstanTaleHUD


def install_fake_characters():
    name = "scripts.characters"
    module = types.ModuleType(name)
    module.Character = Character
    sys.modules[name] = module
    return module


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
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

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

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(path=MOD, name="real_estate_mod"):
    """本番と同じ形（パッケージとして）読み込む（`from . import estate` のため）。"""
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
install_fake_characters()

STATE_DIR = os.path.join(OUT_DIR, "state", "real_estate")
LOG_PATH = os.path.join(OUT_DIR, "real_estate.log")


def read_log():
    try:
        with io.open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def read_state():
    for name in (sorted(os.listdir(STATE_DIR)) if os.path.isdir(STATE_DIR) else []):
        if name.endswith(".json"):
            with io.open(os.path.join(STATE_DIR, name), encoding="utf-8") as fh:
                return json.load(fh)
    return None


def build_world(app_cls, world_cls):
    """街1つ（入口・広場・宿屋・役場）と、隣の街を1つ。"""
    world = world_cls({"world_data": {"name": WORLD_NAME, "days_elapsed": 100}}, None)
    world_dict = {"world_data": {"name": WORLD_NAME, "days_elapsed": 100},
                  "index": {"facility": 20, "npc": 5, "item": 30}}
    save_data_dict = {"world_data": {"name": WORLD_NAME, "days_elapsed": 100},
                      "index": {"facility": 20, "npc": 5, "item": 30}}
    home = Area("0", "始まりの泥濘")
    node = Node("0")
    node.entrance_facility = "0"
    home.nodes["0"] = node
    world.areas["0"] = home
    app = app_cls(world, Player(home, None, 100000), world_dict, save_data_dict)
    app.player.id = "player"
    world.characters["player"] = app.player

    def add(into_node, fid, name, kind, connections):
        facility = Facility(app, into_node, {"name": name, "id": fid,
                                             "description": "",
                                             "facility_type": kind, "tier": None,
                                             "owner": None,
                                             "connections": list(connections),
                                             "config": {"level_of_detail": 0}})
        into_node.facilities[fid] = facility
        return facility

    entrance = add(node, "0", "泥濘の門", "entrance", ["1"])
    hub = add(node, "1", "中央居住区", "ward", ["0", "2", "3"])
    inn = add(node, "2", "泥濘の休み処", INN_TYPE, ["1"])
    office = add(node, "3", "泥濘の徴収所", OFFICE_TYPE, ["1"])

    # 2つ目の街（共有の保管庫を確かめるため）。
    away = Area("1", "灰の街道")
    away_node = Node("1")
    away_node.entrance_facility = "10"
    away.nodes["1"] = away_node
    world.areas["1"] = away
    away_entrance = add(away_node, "10", "街道の門", "entrance", ["11"])
    add(away_node, "11", "石畳の広場", "ward", ["10", "12"])
    away_office = add(away_node, "12", "街道の徴収所", OFFICE_TYPE, ["11"])

    app.player.location = office
    return app, world, {"entrance": entrance, "hub": hub, "inn": inn,
                        "office": office, "area": home, "node": node,
                        "away": away, "away_node": away_node,
                        "away_entrance": away_entrance, "away_office": away_office}


def setup(configure=None, keep_state=False, gold=100000):
    """mod を適用し、役場に立っている app を返す。"""
    if hasattr(sys, "__instantale_real_estate_store__"):
        delattr(sys, "__instantale_real_estate_store__")
    classes = {key: type(base.__name__, (base,), {}) for key, base in BASES.items()}

    main = sys.modules["__main__"]
    main.InstantaleApp = classes["app"]
    main.World = classes["world"]
    main.Facility = Facility
    main.MovePhaseManager = MovePhaseManager
    main.DisplayTalkChoice = DisplayTalkChoice
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec
    main.VacationStartManager = VacationStartManager
    main.VacationEndManager = VacationEndManager
    main.VacationRestManager = VacationRestManager

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
    if not keep_state and os.path.isdir(STATE_DIR):
        for name in os.listdir(STATE_DIR):
            os.remove(os.path.join(STATE_DIR, name))

    module = load_mod()
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.refresh_choice_buttons", classes["app"],
         "refresh_choice_buttons"),
        ("__main__:InstantaleApp.on_button_press", classes["app"], "on_button_press"),
        ("__main__:InstantaleApp.elapse_days", classes["app"], "elapse_days"),
        ("__main__:InstantaleApp.close_shopping_window_process", classes["app"],
         "close_shopping_window_process"),
        ("__main__:World.__init__", classes["world"], "__init__"),
        ("__main__:VacationStartManager.execute", VacationStartManager, "execute"),
        ("__main__:VacationEndManager.execute", VacationEndManager, "execute"),
        ("__main__:VacationRestManager.execute", VacationRestManager, "execute"),
        ("__main__:InstantaleApp.change_background_image_to_current_location",
         classes["app"], "change_background_image_to_current_location"),
        ("__main__:InstantaleApp.change_background_image_from_location_id",
         classes["app"], "change_background_image_from_location_id"),
        ("scripts.hud.new_hud:InventoryItem.change_inventory", InventoryItem,
         "change_inventory"),
    ))
    app, world, places = build_world(classes["app"], classes["world"])
    app.player.gold = gold
    main.test_app = app
    app.facility_screen()
    return module, ctx, app, places, classes


def rent(app, module, label_prefix="週ぎめで借りる"):
    """役場で借りる。押した文字列を返す。"""
    app.press(module.OFFICE_LABEL)
    CLOCK.settle()
    label = app.label_like(label_prefix)
    app.press(label)
    CLOCK.settle()
    return label


def home_of(app, module, places):
    """建った建物。無ければ None。"""
    record = contract_of(module, app)
    if record is None:
        return None
    return places["node"].facilities.get(str(record.get("facility")))


def contract_of(module, app):
    data = read_state()
    contracts = (data or {}).get("contracts") or []
    return contracts[0] if contracts else None


# ================================================================ 検査
print("[窓口]")
module, ctx, app, places, classes = setup()
check("役場で「物件を扱う」が出る", app.has(module.OFFICE_LABEL), app.labels())
before = len(app.buttons)
app.refresh_choice_buttons(True)
CLOCK.settle()
check("塗り直しても増えない", len(app.buttons) == before, app.labels())
app.go(places["inn"])
check("宿屋では出ない", not app.has(module.OFFICE_LABEL), app.labels())
app.go(places["office"])
check("役場に戻ると出る", app.has(module.OFFICE_LABEL), app.labels())
entry = [e for e in app.buttons if e.get("text") == module.OFFICE_LABEL][0]
check("印は mod_ で始まる", module.MARK.startswith("mod_"), module.MARK)
check("PhaseSpec に自前のクラス名を書かない",
      entry["spec"].to_dict()["cls_name"] == "JustSetButtonToNormalPhase",
      entry["spec"].to_dict())

print("[契約]")
app.press(module.OFFICE_LABEL)
CLOCK.settle()
check("借りる・買うが3つ並ぶ",
      sum(app.has(p) for p in ("週ぎめで借りる", "月ぎめで借りる", "建売を買い取る")) == 3,
      app.labels())
label = app.label_like("週ぎめで借りる")
check("家賃が値札に出る", str(module.RENT_WEEK) in (label or ""), label)
gold_before = app.player.gold
app.press(label)
CLOCK.settle()
record = contract_of(module, app)
check("契約が控えに1件残る", record is not None and record.get("kind") == "rent_week",
      record)
check("家賃が引かれた", app.player.gold == gold_before - module.RENT_WEEK,
      app.player.gold)
home = home_of(app, module, places)
check("建物がノードに立った", home is not None,
      list(places["node"].facilities))
check("建物の種類は location",
      home is not None and home.facility_type == "location",
      getattr(home, "facility_type", None))
check("入口から建物へ道が通った",
      home is not None and str(home.id) in list(places["entrance"].connections),
      places["entrance"].connections)
check("建物から入口へ道が通った",
      home is not None and "0" in list(home.connections),
      getattr(home, "connections", None))
check("区画（ward）には繋がない",
      home is not None and str(home.id) not in list(places["hub"].connections),
      places["hub"].connections)
check("施設 id は台帳から採った（台帳が進む）",
      app.world_dict["index"]["facility"] > 20, app.world_dict["index"])
app.press(module.OFFICE_LABEL)
CLOCK.settle()
check("契約中の窓口は確認と解約になる",
      app.has(module.STATUS_LABEL) and app.has(module.RELEASE_LABEL), app.labels())
check("契約中は借りる選択肢が出ない", not app.has("週ぎめで借りる"), app.labels())
app.press(module.CANCEL_LABEL)
CLOCK.settle()
check("「やめる」で役場の選択肢に戻る",
      app.has(module.OFFICE_LABEL) and EXIT_TEXT in app.labels(), app.labels())

print("[一覧]")
app.go(places["entrance"])
check("広場に建物への道が並ぶ",
      home is not None and app.has(home.name), app.labels())
move = [e for e in app.buttons if e.get("text") == getattr(home, "name", None)]
check("道の spec は無害なクラス（セーブに焼かれても押せない）",
      bool(move) and move[0]["spec"].to_dict()["cls_name"]
      == "JustSetButtonToNormalPhase",
      move[0]["spec"].to_dict() if move else None)
app.press(home.name)
CLOCK.settle()
check("押すとゲームの移動が起きる（ノード・施設・エリア）",
      app.moved and app.moved[-1] == ["0", str(home.id), "0"], app.moved)

print("[建物]")
app.go(home)
check("「滞在する」が出る", app.has(module.STAY_LABEL), app.labels())
check("「保管庫をあける」が出る", app.has(module.STORAGE_LABEL), app.labels())
check("ゲームの出口があるときは出口を足さない", not app.has(module.LEAVE_LABEL),
      app.labels())

# 実機のゲームは、自分で足した施設の中では選択肢を1つも作らない（2026-09-11）。
# その画面でも滞在・保管庫・出口が出ること。出口が無いと建物から出られなくなる。
app.player.location = home
app.buttons = []
app.refresh_choice_buttons(True)
CLOCK.settle()
check("選択肢が1つも無い画面でも3つ出る",
      app.has(module.STAY_LABEL) and app.has(module.STORAGE_LABEL)
      and app.has(module.LEAVE_LABEL), app.labels())
app.press(module.LEAVE_LABEL)
CLOCK.settle()
check("「家から出る」でゲームの移動が入口へ起きる",
      app.moved and app.moved[-1] == ["0", "0", "0"], app.moved)
app.go(home)

# 背景。ゲームは自分で足した施設の絵を引けないので、部屋の絵を借りる。
app.go(places["inn"])
app.backgrounds = []
app.go(home)
check("家に立つだけで背景が部屋の絵になる",
      ("room", module.STAY_QUALITY) in app.backgrounds, app.backgrounds)
app.backgrounds = []
app.change_background_image_to_current_location()
check("家では背景が部屋の絵になる",
      app.backgrounds == [("room", module.STAY_QUALITY)], app.backgrounds)
app.backgrounds = []
app.change_background_image_from_location_id(str(home.id))
check("施設 id から決める経路でも同じ",
      app.backgrounds == [("room", module.STAY_QUALITY)], app.backgrounds)
app.backgrounds = []
app.change_background_image_from_location_id(str(places["inn"].id))
check("よその施設の背景には手を出さない",
      app.backgrounds == [("location", str(places["inn"].id))], app.backgrounds)
app.go(places["inn"])
app.backgrounds = []
app.change_background_image_to_current_location()
check("家の外では素のまま", app.backgrounds == [("current", None)], app.backgrounds)
app.go(home)

print("[滞在]")
gold_before = app.player.gold
day_before = app.world.days_elapsed
# 滞在の中で暦が進むので、その場で家賃も引かれる。返るのは宿代だけ。
due = contract_of(module, app)["due"]
rent_due = 0
day_after = day_before + module.STAY_MONTHS * 30
while day_after >= due:
    rent_due += module.RENT_WEEK
    due += 7
app.press(module.STAY_LABEL)
CLOCK.settle()
check("ゲームの宿泊が起きた", app.stays == [(module.STAY_MONTHS, module.STAY_QUALITY)],
      app.stays)
check("滞在のあいだ建物に主が据わる",
      "the owner of" in read_log() and "'player'" in read_log(),
      [l for l in read_log().splitlines() if "owner" in l][:3])
check("宿代は返される", "refunded {}".format(ROOM_PRICE) in read_log(),
      [l for l in read_log().splitlines() if "refund" in l][:3])
check("家賃は返さない", app.player.gold == gold_before - rent_due,
      (app.player.gold, gold_before, rent_due))
check("日数はゲームのまま進む",
      app.world.days_elapsed == day_before + module.STAY_MONTHS * 30,
      app.world.days_elapsed)
check("滞在の最中は自前のボタンを足さない",
      not app.has(module.STAY_LABEL) and not app.has(module.STORAGE_LABEL),
      app.labels())
app.process_choice(VacationRestManager(app, module.STAY_MONTHS, module.STAY_QUALITY),
                   "休養をとる")
CLOCK.settle()
check("活動を1つ終えると滞在が終わる（1泊＝活動1回）", app.stay_ended == 1,
      app.stay_ended)
app.facility_screen()
check("滞在を終えると建物の選択肢に戻る",
      app.has(module.STAY_LABEL) and app.has(module.STORAGE_LABEL), app.labels())
check("据えた主は元へ戻る", getattr(home, "owner", "?") is None,
      getattr(home, "owner", "?"))

print("[保管庫]")
app.go(home)
app.press(module.STORAGE_LABEL)
CLOCK.settle()
check("2枚並びの窓が開く", len(app.windows) == 1, app.windows)
check("場面名はゲームに無い名前", app.windows[-1][3] == "real_estate_storage",
      app.windows[-1][3])
check("窓はメインスレッド（Clock）から開く",
      app.windows_from_clock == [True], app.windows_from_clock)
check("右の見出しが保管庫の名前になる",
      app.hud.right_header.text == getattr(home, "name", None),
      app.hud.right_header.text)
holder = app.windows[-1][1]
player_grid = InventoryGrid(app.player)
holder_grid = InventoryGrid(holder)
item = app.generate_item_from_dict(sample_item(), "item_19", app.player)
app.player.equipments["weapon"] = item
saves_before = app.saves
widget = InventoryItem(item, player_grid)
widget.change_inventory(holder_grid)
CLOCK.settle()
check("預けた品はプレイヤーの持ち物から消える",
      "item_19" not in app.player.inventory.inventory,
      list(app.player.inventory.inventory))
check("預けた品は保管庫の持ち主に移る",
      any(v is item for v in holder.inventory.inventory.values()),
      list(holder.inventory.inventory))
check("装備は外れる", item.unequipped == 1 and not app.player.equipments,
      (item.unequipped, app.player.equipments))
record = contract_of(module, app)
check("控えに品が書かれる",
      isinstance((record or {}).get("storage"), dict) and len(record["storage"]) == 1,
      (record or {}).get("storage"))
check("保存が走る", app.saves > saves_before, (saves_before, app.saves))
app.close_shopping_window_process()
CLOCK.settle()

print("[家賃]")
due_before = contract_of(module, app).get("due")
gold_before = app.player.gold
app.elapse_days(7)
CLOCK.settle()
record = contract_of(module, app)
check("期限が来たら家賃を払う", app.player.gold == gold_before - module.RENT_WEEK,
      app.player.gold)
check("期限が1期ぶん延びる", record.get("due") == due_before + 7, record.get("due"))
gold_before = app.player.gold
app.elapse_days(21)
CLOCK.settle()
record = contract_of(module, app)
check("飛んだ期の分はまとめて払う",
      app.player.gold == gold_before - module.RENT_WEEK * 3, app.player.gold)

print("[期限切れ]")
app.go(places["office"])
app.player.gold = 0
app.elapse_days(7)
CLOCK.settle()
data = read_state() or {}
check("契約が消える", not (data.get("contracts") or []), data.get("contracts"))
check("建物が街から消える",
      str(getattr(home, "id", "")) not in places["node"].facilities,
      list(places["node"].facilities))
check("入口の道も外れる",
      str(getattr(home, "id", "")) not in places["entrance"].connections,
      places["entrance"].connections)
check("保管庫の中身は役場が預かる", len(data.get("seized") or {}) == 1,
      data.get("seized"))

print("[引き取り]")
app.player.gold = 10000
app.facility_screen()
app.press(module.OFFICE_LABEL)
CLOCK.settle()
check("窓口に「預かり品を引き取る」が出る", app.has("預かり品を引き取る"), app.labels())
app.press(app.label_like("預かり品を引き取る"))
CLOCK.settle()
check("引き取り料が引かれた", app.player.gold == 10000 - module.RECLAIM_FEE,
      app.player.gold)
check("品がプレイヤーの持ち物へ戻る",
      any(getattr(v, "name", "") == "錆びた銛突き"
          for v in app.player.inventory.inventory.values()),
      list(app.player.inventory.inventory))
check("預かりは空になる", not (read_state() or {}).get("seized"),
      (read_state() or {}).get("seized"))

print("[ロード]")
module, ctx, app, places, classes = setup()
rent(app, module, "建売を買い取る")
record = contract_of(module, app)
check("買い切りには期限が無い",
      record is not None and record.get("kind") == "owned" and record.get("due") is None,
      record)
built_id = str(record.get("facility"))
# ロード：世界を作り直し、同じ app に据える（`World.__init__` のフックが走る）。
fresh = classes["world"]({"world_data": {"name": WORLD_NAME}}, app)
area = Area("0", "始まりの泥濘")
node = Node("0")
node.entrance_facility = "0"
area.nodes["0"] = node
fresh.areas["0"] = area
for fid, name, kind, conn in (("0", "泥濘の門", "entrance", ["1"]),
                              ("1", "中央居住区", "ward", ["0", "2", "3"]),
                              ("2", "泥濘の休み処", INN_TYPE, ["1"]),
                              ("3", "泥濘の徴収所", OFFICE_TYPE, ["1"])):
    node.facilities[fid] = Facility(app, node, {
        "name": name, "id": fid, "description": "", "facility_type": kind,
        "tier": None, "owner": None, "connections": list(conn),
        "config": {"level_of_detail": 0}})
app.world = fresh
app.player.current_area = area
app.player.location = node.facilities["3"]
app.facility_screen()
check("ロードの後に建物が建ち直る", built_id in node.facilities,
      list(node.facilities))
check("入口の道も張り直る", built_id in node.facilities["0"].connections,
      node.facilities["0"].connections)

print("[繋ぎ直し]")
module, ctx, app, places, classes = setup()
rent(app, module, "週ぎめで借りる")
record = contract_of(module, app)
moved_id = str(record.get("facility"))
est = sys.modules["real_estate_mod.estate"]
moved_home = places["node"].facilities[moved_id]
# 版1（区画に繋いでいた）の形へ戻してから、当て直しに拾わせる。
est.unlink(places["entrance"], moved_id)
est.link(places["hub"], moved_id)
est.unlink(moved_home, "0")
est.link(moved_home, "1")
getattr(sys, module.STATE_STORE_ATTR)["worlds"].load(WORLD_NAME)["contracts"][0]["hub"] = "1"
app.facility_screen()
CLOCK.settle()
check("区画に繋がっていた家が入口へ移る",
      moved_id in places["entrance"].connections
      and moved_id not in places["hub"].connections,
      (places["entrance"].connections, places["hub"].connections))
check("控えの繋ぎ先も入口になる", contract_of(module, app).get("hub") == "0",
      contract_of(module, app))

print("[落ちる宿泊]")
module, ctx, app, places, classes = setup()
rent(app, module, "建売を買い取る")
crash_home = home_of(app, module, places)
app.go(crash_home)
app.player.gold = 50000
gold_before = app.player.gold
app.stay_raises = True
app.press(module.STAY_LABEL)
CLOCK.settle()
check("ゲーム側が落ちても操作が戻る", app.is_button_enabled, app.is_button_enabled)
check("落ちた滞在の宿代は返る", app.player.gold == gold_before, app.player.gold)
check("落ちた後は建物の選択肢に戻る",
      app.has(module.STAY_LABEL) and app.has(module.STORAGE_LABEL), app.labels())
check("落ちたことは記録に残る",
      any("the game's stay failed" in str(e) for e in ctx.errors), ctx.errors[:2])

print("[共有の保管庫]")


def share_on(mod):
    mod.STORAGE_SHARED = True


module, ctx, app, places, classes = setup(configure=share_on)
rent(app, module, "建売を買い取る")
first = home_of(app, module, places)
app.go(first)
app.press(module.STORAGE_LABEL)
CLOCK.settle()
shared_holder = app.windows[-1][1]
check("共有のときは見出しが共有の名前になる",
      app.hud.right_header.text == module.STORAGE_NAME, app.hud.right_header.text)
kept = app.generate_item_from_dict(sample_item("古い羅針盤"), "item_70", app.player)
InventoryItem(kept, InventoryGrid(app.player)).change_inventory(
    InventoryGrid(shared_holder))
CLOCK.settle()
app.close_shopping_window_process()
CLOCK.settle()
data = read_state() or {}
check("共有の側に控える", len(data.get("shared") or {}) == 1, data.get("shared"))
check("建物の側には持たない", not (data["contracts"][0].get("storage") or {}),
      data["contracts"][0].get("storage"))

# 2軒目を別の土地に構える。同じ中身が開く。
app.player.current_area = places["away"]
app.player.location = places["away_office"]
app.facility_screen()
rent(app, module, "週ぎめで借りる")
check("別の土地にもう1軒持てる", len(read_state()["contracts"]) == 2,
      read_state()["contracts"])
second = places["away_node"].facilities[str(read_state()["contracts"][1]["facility"])]
app.go(second)
app.press(module.STORAGE_LABEL)
CLOCK.settle()
other_holder = app.windows[-1][1]
check("別の建物から同じ中身が開く",
      [getattr(v, "name", None)
       for v in other_holder.inventory.inventory.values()] == ["古い羅針盤"],
      list(other_holder.inventory.inventory))
app.close_shopping_window_process()
CLOCK.settle()

# 片方の契約が切れても、もう1軒あるうちは取り上げない。
app.player.gold = 0
app.elapse_days(30)
CLOCK.settle()
data = read_state() or {}
check("1軒失っても共有の中身は残る", len(data.get("shared") or {}) == 1,
      data.get("shared"))
check("役場の預かりは空のまま", not (data.get("seized") or {}), data.get("seized"))

print("[宿屋には手を出さない]")
module, ctx, app, places, classes = setup()
rent(app, module, "建売を買い取る")
app.go(places["inn"])
gold_before = app.player.gold
app.process_choice(VacationStartManager(app, 1, "bunk"), "宿泊する")
CLOCK.settle()
check("宿屋の宿代は引かれたまま", app.player.gold == gold_before - ROOM_PRICE,
      app.player.gold)
check("宿屋では自前のボタンを足さない",
      not app.has(module.STAY_LABEL) and not app.has(module.STORAGE_LABEL),
      app.labels())
ended_before = app.stay_ended
app.process_choice(VacationRestManager(app, 1, "bunk"), "休養をとる")
CLOCK.settle()
check("宿屋の活動では滞在を締めない", app.stay_ended == ended_before,
      (ended_before, app.stay_ended))

print("[例外]")
check("ctx.log_exc に例外が出ていない", not ctx.errors, ctx.errors)
check("ログに WARN が無い", "WARN" not in read_log(),
      [line for line in read_log().splitlines() if "WARN" in line][:4])

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
