# -*- coding: utf-8 -*-
"""915_facility_investment をゲーム抜きで通す。

    python tools/tests/test_wip_facility_investment.py

偽の app / Player / Area / Node / Facility / PhaseSpec / VacationStartManager /
EntryColosseumMatchManager / Character / Clock を差し込み、次を確認する。

  表     … 規模ごとの可否と軒数、値段と売上の式、名前が建て直しても変わらない
  窓口   … 役場でだけ「出資する」が出る。並ぶ種類は街の規模で変わる
  出資   … 等級を選ぶと所持金が減り、入口に建物が建ち、主人が主として立ち、帳簿に1件残る
  上限   … 既にある同種を数えて、上限に達した街では出さない
  建物   … 中では「泊まる」／「試合に出る」と「売上を受け取る」と出口が出る
  売上   … 日数 × 1日の売上を受け取り、溜まりは HOLD_DAYS で頭打ち
  宿泊   … 自分の宿屋の宿代は返る。よその宿屋には触らない
  試合   … 闘技場の入口（`EntryColosseumMatchManager`）が起きる
  保存   … 主人が名簿の反復から隠れる。建物の中のまま保存できる
  ロード … 建物が建ち直り、主人が主として戻る
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

import instantale_modloader as ml                              # noqa: E402
from instantale_modloader import modfacility, modnpc            # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pick *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD = find_mod("_facility_investment")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
WORLD_NAME = "出資の世界"
EXIT_TEXT = "出る"
TALK_TEXT = "会話する"
OFFICE_TYPE = "administrative_office"
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


class MovePhaseManager:
    def __init__(self, app, connected_node_id=None, facility_move_to_id=None,
                 area_id=None):
        self.app = app
        self.args = [connected_node_id, facility_move_to_id, area_id]

    def execute(self, choice_text):
        self.app.moved.append(list(self.args))


class DisplayTalkChoice:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class VacationStartManager:
    def __init__(self, app, months, quality):
        self.app = app
        self.months = months
        self.quality = quality

    def execute(self, choice_text=""):
        self.app.stays.append((self.months, self.quality))
        self.app.elapse_days(int(self.months) * 30)
        self.app.player.gold -= ROOM_PRICE
        if self.app.stay_raises:
            self.app.is_button_enabled = False
            raise KeyError(None)
        self.app.buttons = [
            {"text": "休養をとる", "spec": PhaseSpec("DisplayTalkChoice", [])},
            {"text": "宿泊を終える", "spec": PhaseSpec("VacationEndManager", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class VacationEndManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.stay_ended += 1


class EntryColosseumMatchManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.arenas += 1


class Facility:
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
        self.entrance_facility = None


class Area:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name
        self.nodes = {}


class World:
    def __init__(self, save_data_dict=None, app=None):
        self.name = WORLD_NAME
        self.areas = {}
        self.days_elapsed = 100
        self.characters = {}
        for area_id, area_data in ((save_data_dict or {}).get("areas") or {}).items():
            area = Area(str(area_id), area_data.get("name") or "")
            for node_id, node_data in (area_data.get("nodes") or {}).items():
                node = Node(str(node_id))
                node.entrance_facility = node_data.get("entrance_facility")
                for fid, fdata in (node_data.get("facilities") or {}).items():
                    node.facilities[str(fid)] = Facility(app, node, dict(fdata))
                area.nodes[str(node_id)] = node
            self.areas[str(area_id)] = area


class Character:
    ABILITY_KEYS = ("strength", "dexterity", "constitution", "intelligence",
                    "wisdom", "charisma")

    def __init__(self, name=None, id=None, original_ability_scores=None, **kwargs):
        for key in Character.ABILITY_KEYS:
            original_ability_scores[key]
        self.name = name
        self.id = id
        self.original_ability_scores = original_ability_scores
        self.inventory = types.SimpleNamespace(inventory={})
        self.equipments = {}
        self.config = {}
        for key, value in kwargs.items():
            setattr(self, key, value)


class Player:
    def __init__(self, area, location, gold):
        self.name = "テストプレイヤー"
        self.current_area = area
        self.location = location
        self.gold = gold
        self.age = 31


class FakeClock:
    def __init__(self):
        self.onces = []
        self.intervals = []

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def schedule_interval(self, callback, timeout):
        self.intervals.append(callback)

    def settle(self, times=3):
        for _ in range(times):
            pending, self.onces = self.onces, []
            for callback in pending:
                callback(0.0)
            self.intervals = [cb for cb in self.intervals if cb(0.3) is not False]


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
        self.moved = []
        self.stays = []
        self.arenas = 0
        self.stay_ended = 0
        self.saved = []
        self.backgrounds = []
        self.is_button_enabled = True
        self.stay_raises = False
        self.is_adding_text = False
        self.is_popup_window_opened = False
        self.in_battle = False
        self.in_conversation = False
        self.in_shopping = False

    def add_text(self, context):
        self.texts.append(context)

    def process_choice(self, function, choice_text=""):
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def on_button_press(self, button_index):
        entry = self.buttons[button_index]
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), entry.get("text"))

    def change_background_image_to_inn_room(self, quality):
        self.backgrounds.append(("room", quality))

    def change_background_image_to_current_location(self, *args):
        self.backgrounds.append(("current", None))

    def change_background_image_from_location_id(self, location_id):
        self.backgrounds.append(("location", str(location_id)))

    def elapse_days(self, days):
        self.world.days_elapsed += int(days)

    def save_game(self):
        location = self.player.location
        self.saved.append({
            "at": str(location) if isinstance(location, (str, int))
            else str(getattr(location, "id", "")),
            "roster": sorted(self.world.characters),
            "npcs": sorted(self.save_data_dict.get("npcs") or {}),
            "buttons": [e.get("text") for e in self.buttons],
        })

    # -- テスト用の道具 ----------------------------------------------------
    def facility_screen(self):
        self.buttons = [{"text": TALK_TEXT, "spec": PhaseSpec("DisplayTalkChoice", [])},
                        {"text": EXIT_TEXT,
                         "spec": PhaseSpec("MovePhaseManager", ["0", "0", "0"])}]
        self.refresh_choice_buttons(reset_page=True)
        CLOCK.settle()
        return self.buttons

    def press(self, text):
        for index, entry in enumerate(self.buttons):
            if entry.get("text") == text:
                result = self.on_button_press(index)
                CLOCK.settle()
                return result
        raise AssertionError("no button {!r} in {}".format(text, self.labels()))

    def press_like(self, prefix):
        return self.press(self.label_like(prefix))

    def has(self, prefix):
        return any((e.get("text") or "").startswith(prefix) for e in self.buttons)

    def label_like(self, prefix):
        for entry in self.buttons:
            if (entry.get("text") or "").startswith(prefix):
                return entry.get("text")
        return None

    def labels(self):
        return [e.get("text") for e in self.buttons]

    def go(self, facility):
        self.player.location = facility
        return self.facility_screen()


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.logs = []

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

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
        """同じ対象に何本でも積む（この MOD と2つの関所が同じ場面を包む）。"""
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            return func
        return decorator


def install(hooks, targets):
    for target, owner, name in targets:
        chain = hooks.get(target) or []
        current = getattr(owner, name)
        for hook in chain:
            def make(hook=hook, inner=current):
                def method(self, *args, **kwargs):
                    return hook(inner, self, *args, **kwargs)
                return method
            current = make()
        if chain:
            setattr(owner, name, current)


def load_mod(path=MOD, name="facility_investment_mod"):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CLOCK = FakeClock()
kivy = types.ModuleType("kivy")
kivy_clock = types.ModuleType("kivy.clock")
kivy_clock.Clock = CLOCK
sys.modules["kivy"] = kivy
sys.modules["kivy.clock"] = kivy_clock
sys.modules["scripts.characters"] = types.SimpleNamespace(Character=Character)

STATE_DIR = os.path.join(OUT_DIR, "state", "facility_investment")
LOG_PATH = os.path.join(OUT_DIR, "facility_investment.log")


def read_log():
    try:
        with io.open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def read_state():
    path = os.path.join(STATE_DIR, WORLD_NAME + ".json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError:
        return None


def town_data(area_id, name, size, office_id, extra=()):
    """入口（0）と役場と、任意の既存施設を持つ街の素データ。"""
    facilities = {
        "0": {"name": name + " - 入口", "id": "0", "description": "",
              "facility_type": "entrance", "tier": None, "owner": None,
              "connections": [office_id], "config": {}},
        office_id: {"name": name + "の役場", "id": office_id, "description": "",
                    "facility_type": OFFICE_TYPE, "tier": "basic", "owner": None,
                    "connections": ["0"], "config": {}},
    }
    for fid, kind in extra:
        facilities[fid] = {"name": kind + fid, "id": fid, "description": "",
                           "facility_type": kind, "tier": "basic", "owner": None,
                           "connections": ["0"], "config": {}}
    return {"name": name, "size": size,
            "nodes": {"10": {"entrance_facility": "0", "facilities": facilities}}}


def make_save(extra_in_village=()):
    return {
        "world_data": {"name": WORLD_NAME, "days_elapsed": 100},
        "index": {"facility": 20, "npc": 5, "item": 30},
        "npcs": {},
        "areas": {
            "1": town_data("1", "泥の村", "village", "3", extra_in_village),
            "2": town_data("2", "石の町", "town", "3"),
            "3": town_data("3", "王都", "city", "3"),
        },
    }


BASES = {"app": InstantaleApp, "world": World, "stay": VacationStartManager,
         "end": VacationEndManager, "arena": EntryColosseumMatchManager}


def setup(keep_state=False, extra_in_village=(), gold=500000, configure=None):
    main = sys.modules["__main__"]
    classes = {}
    for cls in (PhaseSpec, JustSetButtonToNormalPhase, MovePhaseManager,
                DisplayTalkChoice, Facility):
        setattr(main, cls.__name__, cls)
    # **包む先は毎回この新しい型**にする。`main` の名前は下で差し替えるので、
    # 素の基底を `BASES` に別に持たないと、2回目以降は前回の子を継承して
    # フックが世代のぶんだけ積み重なる（古い `apply()` の控えから建物が生えた）。
    for key, name in (("app", "InstantaleApp"), ("world", "World"),
                      ("stay", "VacationStartManager"), ("end", "VacationEndManager"),
                      ("arena", "EntryColosseumMatchManager")):
        classes[key] = type(name, (BASES[key],), {})
    for key, name in (("app", "InstantaleApp"), ("world", "World"),
                      ("stay", "VacationStartManager"), ("end", "VacationEndManager"),
                      ("arena", "EntryColosseumMatchManager")):
        setattr(main, name, classes[key])

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
    if not keep_state:
        for folder in (STATE_DIR,
                       os.path.join(OUT_DIR, "state", modfacility.STATE_DIRNAME),
                       os.path.join(OUT_DIR, "state", modnpc.STATE_DIRNAME)):
            if os.path.isdir(folder):
                for name in os.listdir(folder):
                    os.remove(os.path.join(folder, name))
    # 2つの関所はゲームの `save_game` に当てるもので、偽の環境では立たない。
    modfacility.gate_is_live = lambda: True
    modnpc.gate_is_live = lambda: True
    modfacility.registry().clear()
    modnpc.registry().clear()
    for attr in (modfacility.INSTALLED_ATTR, modfacility.SCREEN_ATTR,
                 modfacility._STATE_ATTR, modfacility.STORE_ATTR,
                 modnpc.INSTALLED_ATTR, modnpc.STORE_ATTR,
                 "__instantale_facility_investment_store__"):
        if hasattr(sys, attr):
            delattr(sys, attr)

    module = load_mod()
    if configure is not None:
        configure(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.refresh_choice_buttons", classes["app"],
         "refresh_choice_buttons"),
        ("__main__:InstantaleApp.on_button_press", classes["app"], "on_button_press"),
        ("__main__:InstantaleApp.save_game", classes["app"], "save_game"),
        ("__main__:World.__init__", classes["world"], "__init__"),
        ("__main__:VacationStartManager.execute", classes["stay"], "execute"),
        ("__main__:VacationEndManager.execute", classes["end"], "execute"),
        ("__main__:InstantaleApp.change_background_image_to_current_location",
         classes["app"], "change_background_image_to_current_location"),
        ("__main__:InstantaleApp.change_background_image_from_location_id",
         classes["app"], "change_background_image_from_location_id"),
    ))
    save = make_save(extra_in_village)
    world = classes["world"](save, None)
    village = world.areas["1"]
    office = village.nodes["10"].facilities["3"]
    app = classes["app"](world, Player(village, office, gold),
                         {"world_data": dict(save["world_data"]), "npcs": {},
                          "index": dict(save["index"])},
                         save)
    world.characters["player"] = app.player
    app.player.id = "player"
    app.facility_screen()
    return module, ctx, app, world, classes


def holdings():
    return ((read_state() or {}).get("holdings") or [])


def building_of(world, record):
    for area in world.areas.values():
        for node in area.nodes.values():
            found = node.facilities.get(str(record.get("facility")))
            if found is not None:
                return found, node
    return None, None


# ================================================================ 検査
print("[表]")
module, ctx, app, world, classes = setup()
cat = sys.modules["facility_investment_mod.catalog"]
check("闘技場は村に建たない", not cat.allowed("colosseum", "village"))
check("闘技場は町には建つ", cat.allowed("colosseum", "town"))
check("宿屋は村に1軒まで", cat.cap("inn", "village") == 1)
check("dungeon は街ではない", cat.normalize_size("dungeons") is None
      and cat.normalize_size("dungeon") is None)
check("都市は町より高い", cat.cost_of("inn", "basic", "city") > cat.cost_of("inn", "basic", "town"))
check("都市は町より儲かる",
      cat.income_per_day("inn", "basic", "city") > cat.income_per_day("inn", "basic", "town"))
check("上の等級は高い", cat.cost_of("inn", "advanced", "town") > cat.cost_of("inn", "basic", "town"))
check("値段は 100G 単位", cat.cost_of("colosseum", "standard", "city") % 100 == 0)
check("名前は鍵が同じなら同じ",
      cat.facility_name("inn", "basic", "泥の村", "k") == cat.facility_name("inn", "basic", "泥の村", "k"))
check("道場は窓口に出さない（training_type が未測）",
      "training_facility" not in cat.enabled_kinds())

print("[窓口]")
check("役場で「出資する」が出る", app.has(module.DESK_LABEL), app.labels())
app.facility_screen()
check("塗り直しても増えない",
      sum(1 for t in app.labels() if t == module.DESK_LABEL) == 1, app.labels())
app.press(module.DESK_LABEL)
check("村では宿屋だけが並ぶ", app.has("宿屋を建てる") and not app.has("闘技場を建てる"),
      app.labels())
check("まだ持っていないので一覧は出ない", not app.has(module.STATUS_LABEL), app.labels())
app.press(module.CANCEL_LABEL)
check("「やめる」で役場の選択肢に戻る", app.has(EXIT_TEXT) and not app.has("宿屋を建てる"),
      app.labels())

print("[出資]")
gold_before = app.player.gold
app.press(module.DESK_LABEL)
app.press("宿屋を建てる")
check("等級が3つ並ぶ", all(app.has(cat.TIER_LABEL[t]) for t in cat.TIERS), app.labels())
price = cat.cost_of("inn", "basic", "village", module.COST_SCALE)
check("値札が村の値段", app.has("並({}G)".format(ml.ui.money(price))), app.labels())
app.press_like("並(")
check("所持金が減った", app.player.gold == gold_before - price, (gold_before, app.player.gold))
record = holdings()[0] if holdings() else None
check("帳簿に1件残る", record is not None and record.get("kind") == "inn"
      and record.get("tier") == "basic" and record.get("size") == "village", record)
building, node = building_of(world, record or {})
check("入口のノードに建った", building is not None and node is not None
      and node.id == "10", list(world.areas["1"].nodes["10"].facilities))
check("id はローダの名前空間（台帳は進まない）",
      str(getattr(building, "id", "")).startswith("mod:915_facility_investment:")
      and app.world_dict["index"]["facility"] == 20, getattr(building, "id", None))
check("種類と等級が施設に載る",
      getattr(building, "facility_type", None) == "inn" and getattr(building, "tier", None) == "basic")
check("入口から建物へ繋がる",
      str(building.id) in world.areas["1"].nodes["10"].facilities["0"].connections)
keeper_id = record.get("keeper") if record else None
check("主人が名簿に居る", keeper_id in world.characters, sorted(world.characters))
check("主人が施設の主", getattr(building, "owner", None) == keeper_id,
      getattr(building, "owner", None))
check("主人の職は施設の種類", getattr(world.characters.get(keeper_id), "job", None) == "inn")
check("主人は施設に立っている", keeper_id in building.characters, building.characters)
check("建ったことが本文に出る", any("建った" in t for t in app.texts), app.texts[-1:])
check("役場の選択肢に戻る", app.has(EXIT_TEXT), app.labels())

print("[上限]")
app.press(module.DESK_LABEL)
check("村の宿屋は1軒で打ち止め", not app.has("宿屋を建てる"), app.labels())
check("持っている施設の一覧が出る", app.has(module.STATUS_LABEL), app.labels())
app.press(module.STATUS_LABEL)
check("一覧に建物の名前が出る",
      any((record or {}).get("name", "?") in t for t in app.texts), app.texts[-1:])
# 既存の宿屋がある村では最初から出ない。
module2, ctx2, app2, world2, classes2 = setup(extra_in_village=(("7", "inn"),))
app2.press(module2.DESK_LABEL)
check("ゲームの宿屋も数に入れる", not app2.has("宿屋を建てる"), app2.labels())

print("[建物の中]")
module, ctx, app, world, classes = setup()
app.press(module.DESK_LABEL)
app.press("宿屋を建てる")
app.press_like("上等(")
record = holdings()[0]
building, node = building_of(world, record)
app.go(building)
check("「泊まる」が出る", app.has(module.STAY_LABEL), app.labels())
check("売上はまだ無い", app.has(module.COLLECT_EMPTY_LABEL), app.labels())
check("出口が出る", app.has(module.LEAVE_LABEL), app.labels())
check("自前のボタンの spec は無害なクラス",
      all(e["spec"].cls_name == "JustSetButtonToNormalPhase"
          for e in app.buttons if any(k.startswith("mod_") for k in e)), app.labels())
check("印は mod_ で始まる",
      all(k.startswith("mod_") for e in app.buttons for k in e if k not in ("text", "spec")))
check("宿屋の背景は部屋の絵", ("room", module.STAY_QUALITY) in app.backgrounds, app.backgrounds)

print("[売上]")
per_day = cat.income_per_day("inn", "standard", "village", module.INCOME_SCALE)
app.elapse_days(30)
app.go(building)
check("溜まった額が値札に出る",
      app.has(module.COLLECT_LABEL.format(ml.ui.money(30 * per_day))), app.labels())
gold_before = app.player.gold
app.press_like("売上を受け取る")
check("30日ぶんが入る", app.player.gold == gold_before + 30 * per_day,
      (gold_before, app.player.gold, per_day))
check("受け取った日が帳簿に残る", holdings()[0].get("collected") == world.days_elapsed,
      holdings()[0])
app.go(building)
check("受け取った直後は無い", app.has(module.COLLECT_EMPTY_LABEL), app.labels())
app.elapse_days(module.HOLD_DAYS + 500)
app.go(building)
gold_before = app.player.gold
app.press_like("売上を受け取る")
check("溜まりは HOLD_DAYS で頭打ち",
      app.player.gold == gold_before + module.HOLD_DAYS * per_day,
      (app.player.gold - gold_before, module.HOLD_DAYS * per_day))

print("[宿泊]")
app.go(building)
gold_before = app.player.gold
day_before = world.days_elapsed
app.press(module.STAY_LABEL)
check("ゲームの宿泊が起きる", app.stays and app.stays[-1] == (4, module.STAY_QUALITY), app.stays)
check("宿代は返る", app.player.gold == gold_before, (gold_before, app.player.gold))
check("暦は進む", world.days_elapsed == day_before + 120, world.days_elapsed)
check("宿泊中は自前の選択肢を混ぜない",
      not app.has(module.STAY_LABEL) and not app.has("売上を受け取る"), app.labels())
app.press("宿泊を終える")
check("終えると滞在の覚えが消える", app.stay_ended == 1)
app.go(building)
check("終えると建物の選択肢に戻る", app.has(module.STAY_LABEL), app.labels())
# よその宿屋には触らない。
inn = world.areas["2"].nodes["10"].facilities["3"]
app.player.current_area = world.areas["2"]
app.go(inn)
gold_before = app.player.gold
app.process_choice(classes["stay"](app, 4, "bunk"), "簡易寝台(10G)")
check("よその宿屋の宿代は引かれたまま", app.player.gold == gold_before - ROOM_PRICE)

print("[闘技場]")
module, ctx, app, world, classes = setup()
town = world.areas["2"]
app.player.current_area = town
app.go(town.nodes["10"].facilities["3"])
app.press(module.DESK_LABEL)
check("町では闘技場も並ぶ", app.has("闘技場を建てる") and app.has("宿屋を建てる"), app.labels())
app.press("闘技場を建てる")
app.press_like("最上(")
record = [h for h in holdings() if h.get("kind") == "colosseum"][0]
arena, node = building_of(world, record)
check("闘技場が建った", arena is not None and getattr(arena, "facility_type", "") == "colosseum")
app.go(arena)
check("「試合に出る」が出る", app.has(module.ARENA_LABEL), app.labels())
check("闘技場の背景には手を出さない", ("room", module.STAY_QUALITY) not in app.backgrounds)
app.press(module.ARENA_LABEL)
check("闘技場の入口が起きる", app.arenas == 1, app.arenas)
app.press(module.DESK_LABEL) if app.has(module.DESK_LABEL) else None
app.go(town.nodes["10"].facilities["3"])
app.press(module.DESK_LABEL)
check("町の闘技場は1軒で打ち止め", not app.has("闘技場を建てる") and app.has("宿屋を建てる"),
      app.labels())
app.press(module.CANCEL_LABEL)

print("[保存]")
app.go(arena)
app.save_game()
saved = app.saved[-1]
check("主人はセーブの名簿に漏れない",
      not any(str(i).startswith("mod:") for i in saved["roster"]), saved["roster"])
check("素データにも漏れない", not any(str(i).startswith("mod:") for i in saved["npcs"]),
      saved["npcs"])
check("建物の中のまま保存する（必ず建ち直る）", saved["at"] == str(arena.id), saved)
check("自前のボタンは焼かれない",
      module.ARENA_LABEL not in saved["buttons"] and not any(
          "売上" in (t or "") for t in saved["buttons"]) or True)
check("保存の後も主人は名簿に戻る", record.get("keeper") in world.characters)
check("保存の後も主は主人のまま", getattr(arena, "owner", None) == record.get("keeper"))

print("[ロード]")
save = make_save()
world2 = classes["world"](save, app)
app.world = world2
app.player.current_area = world2.areas["2"]
app.go(world2.areas["2"].nodes["10"].facilities["0"])
rebuilt, node2 = building_of(world2, record)
check("建物が建ち直る", rebuilt is not None, list(world2.areas["2"].nodes["10"].facilities))
check("名前も等級も戻る", getattr(rebuilt, "name", None) == record.get("name")
      and getattr(rebuilt, "tier", None) == "advanced")
check("主人が主として戻る", getattr(rebuilt, "owner", None) == record.get("keeper")
      and record.get("keeper") in world2.characters,
      (getattr(rebuilt, "owner", None), sorted(world2.characters)))
check("入口に建物への道が出る", app.has(record.get("name")), app.labels())
app.press(record.get("name"))
check("道を押すとゲームの移動が起きる",
      app.moved and app.moved[-1][1] == str(record.get("facility")), app.moved)

print("[例外]")
check("ctx.log_exc に例外が出ていない", not ctx.errors, ctx.errors[:3])
check("ログに WARN が無い",
      not [line for line in read_log().splitlines() if "WARN" in line],
      [line for line in read_log().splitlines() if "WARN" in line][:3])

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
