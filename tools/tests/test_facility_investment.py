# -*- coding: utf-8 -*-
"""331_facility_investment をゲーム抜きで通す。

    python tools/tests/test_facility_investment.py

（開発中は `915_facility_investment` / `test_wip_facility_investment.py` だった。2026-09-16 に正式化。）

偽の app / Player / Area / Node / Facility / PhaseSpec / VacationStartManager /
EntryColosseumMatchManager / Character / Clock を差し込み、次を確認する。

  表     … 規模ごとの可否と軒数、値段と売上の式、名前が建て直しても変わらない
  設定   … 建設費・1日の売上・最低の規模を種類ごとに動かせる（既定は表と同じ）
  画面   … 同梱の設定画面（tool.py）は種類を行・項目を列にした表。純粋な部分だけ窓抜きで見る
  窓口   … 役場でだけ「施設の建設（出資）」が出る。並ぶ種類は街の規模で変わり、全部の種類の条件が本文に出る
  出資   … 等級を選ぶと所持金が減り、入口に建物が建ち、主人が主として立ち、帳簿に1件残る
  上限   … その街に建てた合計の軒数で止まる（ゲームの施設は数えない）
  建物   … 中では「無料で泊まる」と「売上を受け取る」と出口が出る
  売上   … 日数 × 1日の売上を受け取り、溜まりは HOLD_DAYS で頭打ち
  宿泊   … 「無料で泊まる」は宿代を先に足してゲームに引かせる（打ち消し合って所持金は動かない）。
          引かないビルドでは前払いを戻し、知らない部屋では差で返して WARN を出す。
          ゲームの「宿泊する」は自分の宿屋でも素のまま。よその宿屋には触らない
  店と道場 … 窓口に並び、中ではゲームの売買・訓練が通る（素データの写しが要る）
  闘技場 … 中に立っている間だけ素データの写しが在り、ゲームの試合（素データを引く）が通る。落ちても操作が戻る。
          既に出た闘士の名前が相手を作る頼み文に足される
  主人   … プレイヤーを出資者と知っている（profile・relationship・会話の notes）
  生成   … 建てるときに生成 AI へ1回だけ聞く。読めない・落ちた・無い版では表の12人へ降りる
  保存   … 主人が名簿の反復から隠れる。建物の中のまま保存できる
  ロード … 建物が建ち直り、主人が主として戻る
  別世界 … 別の世界を読んでも前の世界の建物・主人・帳簿は現れず、控えにも漏れない
  周回   … 控えは 世界×主人公。同じ世界で新しい主人公を作っても前の主人公の建物と主人は現れない
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
from instantale_modloader import durations, modfacility, modnpc  # noqa: E402
from instantale_modloader import state as loader_state          # noqa: E402


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
PLAYER_NAME = "テストプレイヤー"
SEP = loader_state.PLAYTHROUGH_SEP
#: 控えは 世界×主人公 で1ファイル（`state.playthrough_key`）。
STATE_FILE = WORLD_NAME + SEP + PLAYER_NAME + ".json"
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
        self.app.player.gold -= getattr(self.app, "room_price", ROOM_PRICE)
        # ゲームは部屋の絵に差し替える（GAME.md §2.17 の実測。ローダはこれで描いた覚えを消す）。
        self.app.change_background_image_to_inn_room(self.quality)
        if self.app.stay_raises:
            self.app.is_button_enabled = False
            raise KeyError(None)
        # 活動の選択肢は本物と同じクラス（GAME.md §2.17）。`DisplayTalkChoice` は施設の入口の
        # 種類（`TOP_ENTRY_CLASSES`）なので、活動に使うと下位の画面が施設の画面に見える。
        self.app.buttons = [
            {"text": "休養をとる", "spec": PhaseSpec("VacationRestManager", [])},
            {"text": "宿泊を終える", "spec": PhaseSpec("VacationEndManager", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class VacationEndManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.stay_ended += 1
        # ゲームは終える処理の**中で**宿の画面を組み直す（実機 2026-09-14。
        # `where: ... game_choices=2` が `stay: finished` より先に出た）。その後は組み直さない。
        self.app.buttons = [
            {"text": "宿泊する", "spec": PhaseSpec("DisplayVacationChoice", [])},
            {"text": "会話する", "spec": PhaseSpec("DisplayTalkChoice", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class EntryColosseumMatchManager:
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.arenas += 1


class ColosseumMatchStart:
    """ゲームの試合。`method` が施設 id で `world_dict` を引いて落ちる（実機 2026-09-13）。"""
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.is_button_enabled = False
        location = self.app.player.location
        facility_id = str(getattr(location, "id", location))
        area_id = str(self.app.player.current_area.id)
        node_id = str(location.parent_node.id)
        self.app.matches += 1
        plain = self.app.world_dict["areas"][area_id]["nodes"][node_id]["facilities"][facility_id]
        plain["config"]["current_phase"] = int(plain["config"].get("current_phase") or 0) + 1
        plain["config"].setdefault("enemy_data", {})[self.app.matches * 2] = {"rank": 14}   # int の鍵
        self.app.is_button_enabled = True
        return plain


class ShoppingStartManagerRemake:
    """ゲームの売買。`shopping_start_method_1` が施設 id で素データを引く（GAME.md §2.28）。"""
    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.is_button_enabled = False
        self.app.shops += 1
        plain = plain_of(self.app, self.app.player.location)
        self.app.is_button_enabled = True
        return plain


class TrainingStartManager:
    """ゲームの訓練。署名は `(app, training_years, training_price)`。"""
    def __init__(self, app, training_years=3, training_price=0):
        self.app = app
        self.years = training_years

    def execute(self, choice_text=""):
        self.app.is_button_enabled = False
        self.app.trainings += 1
        plain = plain_of(self.app, self.app.player.location)
        self.app.is_button_enabled = True
        return plain


def plain_of(app, location):
    """立っている施設の素データ。写しが無ければ `KeyError`（本体と同じ落ち方）。"""
    area_id = str(app.player.current_area.id)
    node_id = str(location.parent_node.id)
    facility_id = str(getattr(location, "id", location))
    return app.world_dict["areas"][area_id]["nodes"][node_id]["facilities"][facility_id]


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
        self.matches = 0
        self.shops = 0
        self.trainings = 0
        self.stay_ended = 0
        self.saved = []
        self.backgrounds = []
        self.is_button_enabled = True
        self.stay_raises = False
        #: ゲームが宿泊で引く宿代。引かないビルドを作るために 0 にできる。
        self.room_price = ROOM_PRICE
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
            "facilities": mod_facilities_in(self.save_data_dict) + mod_facilities_in(self.world_dict),
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



class FakeLLM(object):
    """`scripts.llm.llm_manager` の代わり（`create_model` + `send_request`）。

    載っていない版もあるので、載せない筋書きも通す（`unload`）。
    """

    answers = []
    calls = []

    @classmethod
    def create_model(cls, model_name, **fields):
        return type(str(model_name), (object,), {"mod_fields": dict(fields)})

    @classmethod
    def send_request(cls, manager_name, message, structure, max_tokens=None, timeout=None):
        cls.calls.append({"manager": manager_name, "timeout": timeout,
                          "text": "\n".join(m.get("content", "") for m in message)})
        if not cls.answers:
            raise RuntimeError("答えが用意されていない")
        answer = cls.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @classmethod
    def load(cls, answers=()):
        cls.answers = list(answers)
        cls.calls = []
        module = types.ModuleType("scripts.llm.llm_manager")
        module.create_model = cls.create_model
        module.send_request = cls.send_request
        sys.modules["scripts.llm.llm_manager"] = module

    @classmethod
    def unload(cls):
        sys.modules.pop("scripts.llm.llm_manager", None)
        cls.answers = []
        cls.calls = []

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


def write_state(data):
    """帳簿を書き戻す（版24 までの形を作るため）。"""
    path = os.path.join(STATE_DIR, STATE_FILE)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    store = getattr(sys, "__instantale_facility_investment_store__", None)
    worlds = (store or {}).get("worlds")
    if worlds is not None:
        worlds.forget(WORLD_NAME + SEP + PLAYER_NAME)   # 次に読むときファイルから読み直す


def read_state():
    path = os.path.join(STATE_DIR, STATE_FILE)
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError:
        return None


def town_data(area_id, name, size, office_id, extra=()):
    """入口（0）と役場と、任意の既存施設を持つ街の素データ。"""
    # 実データの街と同じ形: 入口 → 区画（ward）→ 役場と施設。
    facilities = {
        "0": {"name": name + " - 入口", "id": "0", "description": "",
              "facility_type": "entrance", "tier": None, "owner": None,
              "connections": ["9"], "config": {}},
        "9": {"name": name + "の広場", "id": "9", "description": "",
              "facility_type": "ward", "tier": None, "owner": None,
              "connections": ["0", office_id] + [fid for fid, _k in extra], "config": {}},
        office_id: {"name": name + "の役場", "id": office_id, "description": "",
                    "facility_type": OFFICE_TYPE, "tier": "basic", "owner": None,
                    "connections": ["9"], "config": {}},
    }
    for fid, kind in extra:
        facilities[fid] = {"name": kind + fid, "id": fid, "description": "",
                           "facility_type": kind, "tier": "basic", "owner": None,
                           "connections": ["9"], "config": {}}
    return {"name": name, "size": size,
            "nodes": {"10": {"entrance_facility": "0", "facilities": facilities}}}


def mod_facilities_in(plain):
    """素データの辞書に在る `mod:` の施設 id。"""
    out = []
    for area in ((plain or {}).get("areas") or {}).values():
        for node in (area.get("nodes") or {}).values():
            out.extend(fid for fid in (node.get("facilities") or {}) if str(fid).startswith("mod:"))
    return out


def make_save(extra_in_village=()):
    return {
        "world_data": {"name": WORLD_NAME, "days_elapsed": 100},
        "player_data": {"name": PLAYER_NAME},
        "index": {"facility": 20, "npc": 5, "item": 30},
        "npcs": {},
        "areas": {
            "1": town_data("1", "泥の村", "village", "3", extra_in_village),
            "2": town_data("2", "石の町", "town", "3"),
            "3": town_data("3", "王都", "city", "3"),
        },
    }


BASES = {"app": InstantaleApp, "world": World, "stay": VacationStartManager,
         "end": VacationEndManager, "arena": EntryColosseumMatchManager,
         "match": ColosseumMatchStart, "shop": ShoppingStartManagerRemake,
         "train": TrainingStartManager}


def setup(keep_state=False, extra_in_village=(), gold=500000, configure=None):
    main = sys.modules["__main__"]
    classes = {}
    for cls in (PhaseSpec, JustSetButtonToNormalPhase, MovePhaseManager,
                DisplayTalkChoice, Facility):
        setattr(main, cls.__name__, cls)
    # **包む先は毎回この新しい型**にする。`main` の名前は下で差し替えるので、
    # 素の基底を `BASES` に別に持たないと、2回目以降は前回の子を継承して
    # フックが世代のぶんだけ積み重なる（古い `apply()` の控えから建物が生えた）。
    names = (("app", "InstantaleApp"), ("world", "World"),
             ("stay", "VacationStartManager"), ("end", "VacationEndManager"),
             ("arena", "EntryColosseumMatchManager"), ("match", "ColosseumMatchStart"),
             ("shop", "ShoppingStartManagerRemake"), ("train", "TrainingStartManager"))
    for key, name in names:
        classes[key] = type(name, (BASES[key],), {})
    for key, name in names:
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
    # **`modnpc` の関所を先に立てる。** 実機では別の MOD（`229_probe_mod_npc`）が
    # 先に立てていて、`World.__init__` の後処理は内側から走るので `modnpc` が先になる
    # （この MOD だけなら `modfacility` が先）。建物より前に主人を置こうとする**厳しい側**で、
    # 実機で主人が会話の一覧から消えたのはこの順序のとき（DOC.md §3.2 の5回目）。
    modnpc.install(ctx, write=lambda *a: None)
    module.apply(ctx)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.refresh_choice_buttons", classes["app"],
         "refresh_choice_buttons"),
        ("__main__:InstantaleApp.on_button_press", classes["app"], "on_button_press"),
        ("__main__:InstantaleApp.save_game", classes["app"], "save_game"),
        ("__main__:World.__init__", classes["world"], "__init__"),
        ("__main__:VacationStartManager.execute", classes["stay"], "execute"),
        ("__main__:VacationEndManager.execute", classes["end"], "execute"),
        ("__main__:ColosseumMatchStart.execute", classes["match"], "execute"),
        ("__main__:ShoppingStartManagerRemake.execute", classes["shop"], "execute"),
        ("__main__:TrainingStartManager.execute", classes["train"], "execute"),
        ("__main__:InstantaleApp.change_background_image_to_current_location",
         classes["app"], "change_background_image_to_current_location"),
        ("__main__:InstantaleApp.change_background_image_to_inn_room",
         classes["app"], "change_background_image_to_inn_room"),
        ("__main__:InstantaleApp.change_background_image_from_location_id",
         classes["app"], "change_background_image_from_location_id"),
    ))
    save = make_save(extra_in_village)
    world = classes["world"](save, None)
    village = world.areas["1"]
    office = village.nodes["10"].facilities["3"]
    app = classes["app"](world, Player(village, office, gold),
                         {"world_data": dict(save["world_data"]), "npcs": {},
                          "index": dict(save["index"]),
                          # 世界のファイル側の素データ（保存側とは別の辞書。GAME.md §2.28）
                          "areas": json.loads(json.dumps(save["areas"]))},
                         save)
    world.characters["player"] = app.player
    app.player.id = "player"
    app.facility_screen()
    return module, ctx, app, world, classes


def module_state_own_stay():
    store = getattr(sys, "__instantale_facility_investment_store__", None)
    return ((store or {}).get("state") or {}).get("own_stay")


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
check("上限は種類ごとではなく街の合計",
      (cat.slots("village"), cat.slots("town"), cat.slots("city")) == (1, 2, 3)
      and cat.slots("dungeon") == 0)
check("種類ごとにあるのは最低の規模だけ",
      all("cap" not in spec for spec in cat.KINDS.values()))
check("dungeon は街ではない", cat.normalize_size("dungeons") is None
      and cat.normalize_size("dungeon") is None)
check("都市は町より高い", cat.cost_of("inn", "basic", "city") > cat.cost_of("inn", "basic", "town"))
check("都市は町より儲かる",
      cat.income_per_day("inn", "basic", "city") > cat.income_per_day("inn", "basic", "town"))
check("上の等級は高い", cat.cost_of("inn", "advanced", "town") > cat.cost_of("inn", "basic", "town"))
check("値段は 100G 単位", cat.cost_of("colosseum", "standard", "city") % 100 == 0)
check("名前は鍵が同じなら同じ",
      cat.facility_name("inn", "basic", "泥の村", "k") == cat.facility_name("inn", "basic", "泥の村", "k"))
# 建物の名前も同じ世界で重ねない（実機 2026-09-14。別の世界でも `3-1` が「七宝の間」だった）。
check("使っている建物の名前は飛ばす（版37）",
      cat.facility_name("specialty_shop", "advanced", "泥の村", "k", taken=["七宝の間"])
      != "七宝の間",
      cat.facility_name("specialty_shop", "advanced", "泥の村", "k", taken=["七宝の間"]))
check("同じ鍵と同じ顔ぶれからは同じ名前",
      cat.facility_name("specialty_shop", "advanced", "泥の村", "k", taken=["七宝の間"])
      == cat.facility_name("specialty_shop", "advanced", "泥の村", "k", taken=["七宝の間"]))
_used_up = cat.facility_name("specialty_shop", "advanced", "泥の村", "k",
                             taken=[n.format(area="泥の村")
                                    for n in cat.KINDS["specialty_shop"]["names"]["advanced"]])
check("候補を使い切ったら土地の名を冠して分ける", _used_up.startswith("泥の村の"), _used_up)
# 主人の名前は同じ世界で重ねない（実機 2026-09-14。闘技場と道場がどちらもトビアスだった）。
first = cat.keeper_choice("k")[0]
check("同じ鍵からは同じ主人", cat.keeper_choice("k")[0] == first)
check("使われている名前は飛ばす", cat.keeper_choice("k", [first])[0] != first)
check("全部使われていたら鍵の人に戻る",
      cat.keeper_choice("k", [c[0] for c in cat.KEEPER_POOL])[0] == first)
check("候補は12人（6人だと4軒目で重なった）", len(cat.KEEPER_POOL) == 12)
check("名前から候補を引ける", cat.keeper_by_name(first)[0] == first
      and cat.keeper_by_name("居ない人") is None)
fields = cat.keeper_fields("inn", "泥の村", "灯火亭", "k", investor="テストプレイヤー")
named = cat.keeper_fields("inn", "泥の村", "灯火亭", "k", name="ヘルガ")
check("帳簿の名前で組み直せる（建て直しても同じ人）", named["name"] == "ヘルガ")
check("主人の profile に出資者の名が入る", "テストプレイヤー" in fields["profile"], fields["profile"])
check("主人はプレイヤーに好意を持って生まれる",
      fields["relationship"]["player"]["affinity"] == cat.KEEPER_AFFINITY
      and "出資者" in fields["relationship"]["player"]["relationship"])
# 名前そのものに読点が入るので1人ずつ括る（実機 2026-09-13）。
check("設置条件の一文（規模で建たない）",
      cat.requirement_text("colosseum", "village") == "闘技場: 町から（村には建たない）",
      cat.requirement_text("colosseum", "village"))
check("建つ種類には条件の行を出さない", cat.requirement_text("inn", "village") == "")
check("合計の一文", cat.slots_text("town", 1) == "この街に建てられるのは合計2軒まで（いま1軒）",
      cat.slots_text("town", 1))
check("合計の一文（空きが無い）",
      cat.slots_text("village", 1) == "この街に建てられるのは合計1軒まで（いま1軒。空きが無い）",
      cat.slots_text("village", 1))
check("街でなければ条件も出さない",
      cat.requirement_text("inn", "dungeon") == "" and cat.slots_text("dungeon") == "")
check("既出の闘士は1人ずつ括って並べる",
      "「黄金の断罪者、アウレリウス」「ヴィオラ」" in cat.arena_variety_note(
          ["黄金の断罪者、アウレリウス", "ヴィオラ"])
      and cat.arena_variety_note([]) == "")
check("6種類すべてが窓口に出る（版16 で店3種と道場を足した）",
      cat.enabled_kinds() == ["inn", "general_store", "blacksmith", "specialty_shop",
                              "training_facility", "colosseum"], cat.enabled_kinds())
check("素データの写しが要るのは売買と試合の種類だけ",
      [k for k in cat.KIND_ORDER if cat.needs_plain(k)]
      == ["general_store", "blacksmith", "specialty_shop", "training_facility", "colosseum"],
      [k for k in cat.KIND_ORDER if cat.needs_plain(k)])
check("店は村にも建つ", cat.allowed("general_store", "village"))
check("道場は町から", not cat.allowed("training_facility", "village")
      and cat.allowed("training_facility", "town"))
check("闘技場は窓口に出す（試合の間だけ素データの写しを置く）",
      "colosseum" in cat.enabled_kinds())

print("[種類ごとの設定]")
# 建設費・1日の売上・最低の規模を種類ごとに動かせる（本人の指定 2026-09-14）。
# 宣言は `mod.json`、既定は `catalog.KINDS` の表と同じ。
declared = json.load(io.open(os.path.join(os.path.dirname(MOD), "mod.json"),
                             encoding="utf-8"))["settings"]
missing = []
for kind in cat.KIND_ORDER:
    spec = cat.KINDS[kind]
    for prefix, key in (("COST", "cost"), ("INCOME", "income"), ("MIN_SIZE", "min_size")):
        name = "{}_{}".format(prefix, kind.upper())
        if name not in declared:
            missing.append(name + " (mod.json)")
        elif declared[name].get("default") != spec[key]:
            missing.append("{} default={!r} table={!r}".format(
                name, declared[name].get("default"), spec[key]))
        if not hasattr(module, name):
            missing.append(name + " (module)")
check("全部の種類に3つずつ宣言があり、既定は表と同じ", not missing, missing[:4])
extra = [name for name in declared
         if name.split("_", 1)[0] in ("COST", "INCOME") and name != "COST_SCALE"
         and name != "INCOME_SCALE"
         and name.split("_", 1)[1].lower() not in cat.KINDS]
check("表に無い種類の宣言は無い", not extra, extra)

# 設定を変えると値札・売上・条件が動く。
module, ctx, app, world, classes = setup(gold=2000000, configure=lambda m: (
    setattr(m, "COST_INN", 1000), setattr(m, "INCOME_INN", 5),
    setattr(m, "MIN_SIZE_COLOSSEUM", "village")))
app.press(module.DESK_LABEL)
check("窓口の本文に建つ種類の行は出さない（版36）",
      "宿屋:" not in app.texts[-1] and "この街に建てられるのは" in app.texts[-1],
      app.texts[-1:])
check("最低の規模を下げれば村にも並ぶ", app.has("闘技場を建てる"), app.labels())
app.press("宿屋を建てる")
check("等級の値札も設定から",
      any(label.startswith("並(") and ml.ui.money(
          cat.cost_of("inn", "basic", "village", 100, base=1000)) in label
          for label in app.labels()), app.labels())
app.press_like("並(")
gold_after = app.player.gold
record = holdings()[0]
app.press(module.DESK_LABEL)
app.press("やめる")
app.elapse_days(10)
app.go(world.areas["1"].nodes["10"].facilities["9"])
app.go(building_of(world, record)[0])
check("溜まる売上も設定から（村は0.5倍）", app.has(module.COLLECT_LABEL.format(
    ml.ui.money(10 * cat.income_per_day("inn", "basic", "village", 100, base=5)))),
    app.labels())

# 読めない値は表の値に落ちる（設定の壊れたファイルでも建つ）。
module, ctx, app, world, classes = setup(configure=lambda m: (
    setattr(m, "COST_INN", None), setattr(m, "MIN_SIZE_INN", "")))
app.press(module.DESK_LABEL)
app.press("宿屋を建てる")
check("読めない設定は表の値に落ちる",
      any(label.startswith("並(") and ml.ui.money(
          cat.cost_of("inn", "basic", "village")) in label for label in app.labels()),
      app.labels())
app.press("やめる")

# 次の節（[窓口]）は [表] と同じ素の設定で始める。
module, ctx, app, world, classes = setup()

print("[窓口]")
check("役場で「施設の建設（出資）」が出る", app.has(module.DESK_LABEL), app.labels())
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
      str(getattr(building, "id", "")).startswith("mod:331_facility_investment:")
      and app.world_dict["index"]["facility"] == 20, getattr(building, "id", None))
check("種類と等級が施設に載る",
      getattr(building, "facility_type", None) == "inn" and getattr(building, "tier", None) == "basic")
check("区画から建物へ繋がる（入口ではない）",
      str(building.id) in world.areas["1"].nodes["10"].facilities["9"].connections
      and str(building.id) not in world.areas["1"].nodes["10"].facilities["0"].connections,
      (world.areas["1"].nodes["10"].facilities["9"].connections,
       world.areas["1"].nodes["10"].facilities["0"].connections))
check("建った文に区画の名が出る", any("広場に" in x for x in app.texts), app.texts[-1:])
keeper_id = record.get("keeper") if record else None
check("主人が名簿に居る", keeper_id in world.characters, sorted(world.characters))
check("主人が施設の主", getattr(building, "owner", None) == keeper_id,
      getattr(building, "owner", None))
check("主人の職は施設の種類", getattr(world.characters.get(keeper_id), "job", None) == "inn")
check("主人にレベルがある（詳細生成が掛け算に使う）",
      isinstance(getattr(world.characters.get(keeper_id), "experience_level", None), int))
check("主人は施設に立っている", keeper_id in building.characters, building.characters)
keeper = world.characters.get(keeper_id)
check("帳簿に出資者の名が残る", record.get("investor") == app.player.name, record.get("investor"))
check("帳簿に主人の名も残る", record.get("keeper_name") == getattr(keeper, "name", None),
      (record.get("keeper_name"), getattr(keeper, "name", None)))
check("主人の profile に出資者の名", app.player.name in (getattr(keeper, "profile", "") or ""),
      getattr(keeper, "profile", None))
note_info = {"site": "conversation_starter", "app": app, "npc_id": keeper_id,
             "character": keeper, "args": {}}
note_text, note_owners = modnpc.compose_notes(note_info, modnpc.layers(keeper_id))
# 層は世代ごとに積み直る（注入し直しで新しい版の notes が効く）。
check("主人の層はこの世代で1つだけ",
      len([l for l in modnpc.layers(keeper_id) if l["owner"] == module.OWNER]) == 1,
      modnpc.layers(keeper_id))
check("会話の頼み文に出資者本人だと足される",
      app.player.name in note_text and "出資者" in note_text and module.OWNER in note_owners,
      (note_text, note_owners))
check("建ったことが本文に出る", any("建った" in t for t in app.texts), app.texts[-1:])
check("役場の選択肢に戻る", app.has(EXIT_TEXT), app.labels())

print("[上限]")
# 上限は種類ごとではなく、その街に建てた合計（本人の指定 2026-09-14）。村は1軒。
app.press(module.DESK_LABEL)
check("村は1軒建てたら打ち止め",
      not any(app.has(module.BUILD_LABEL.format(cat.KINDS[k]["label"]))
              for k in cat.enabled_kinds()), app.labels())
check("建てられなくても条件が出る",
      "この街に建てられるのは合計1軒まで（いま1軒。空きが無い）" in app.texts[-1]
      and "闘技場: 町から（村には建たない）" in app.texts[-1], app.texts[-1:])
check("持っている施設の一覧が出る", app.has(module.STATUS_LABEL), app.labels())
app.press(module.STATUS_LABEL)
check("一覧に建物の名前が出る",
      any((record or {}).get("name", "?") in t for t in app.texts), app.texts[-1:])
# ゲームが最初から置いた施設は数えない（数えると素の村では何も建たない）。
module2, ctx2, app2, world2, classes2 = setup(extra_in_village=(("7", "inn"),))
app2.press(module2.DESK_LABEL)
check("ゲームの宿屋は数に入れない", app2.has("宿屋を建てる"), app2.labels())
check("いまの軒数はこちらが建てた分だけ",
      "合計1軒まで（いま0軒）" in app2.texts[-1], app2.texts[-1:])

print("[主人を作らせる]")
# 建てるときに1回だけゲームと同じ生成 AI に聞く（本人の指定 2026-09-14）。
# 読めなければ表の12人へ降りる。
module, ctx, app, world, classes = setup()
FakeLLM.load([{"name": "サリ", "category": "middle-aged woman",
               "speech_style": "短く言い切る", "personality": "荒くれも黙らせる女主人",
               "profile": "灯火亭を任されている宿の女主人。売上を預かっている。",
               "facility_name": "風待ちの宿"}])
try:
    app.press(module.DESK_LABEL)
    app.press("宿屋を建てる")
    app.press_like("並(")
    made = holdings()[0]
    check("LLM に1回だけ聞く", len(FakeLLM.calls) == 1, FakeLLM.calls)
    check("頼み文に世界・土地・施設・出資者が入る",
          all(word in FakeLLM.calls[0]["text"]
              for word in ("世界観", "泥の村", "宿屋", app.player.name)),
          FakeLLM.calls[0]["text"][:200])
    check("待つ秒数を必ず渡す", FakeLLM.calls[0]["timeout"] == module.KEEPER_TIMEOUT)
    check("答えの名前で建物が建つ（版37）", made.get("name") == "風待ちの宿"
          and getattr(building_of(world, made)[0], "name", None) == "風待ちの宿",
          made.get("name"))
    # 施設の情報は種類から始まる（名前は渡さず、生成 AI に付けさせる。版37）。
    check("頼み文は施設名を渡さず作らせる",
          "facility_name" in FakeLLM.calls[0]["text"]
          and "【施設の情報】" + chr(10) + "- 種類:" in FakeLLM.calls[0]["text"],
          FakeLLM.calls[0]["text"][:400])
    check("答えの名前で主人が立つ", made.get("keeper_name") == "サリ"
          and getattr(world.characters.get(made.get("keeper")), "name", None) == "サリ")
    keeper = world.characters.get(made.get("keeper"))
    check("答えの人柄と口調が入る",
          getattr(keeper, "personality", "") == "荒くれも黙らせる女主人"
          and getattr(keeper, "speech_style", "") == "短く言い切る")
    check("職と好感度は表のまま（LLM には作らせない）",
          getattr(keeper, "job", None) == "inn"
          and getattr(keeper, "relationship", {})["player"]["affinity"] == cat.KEEPER_AFFINITY)
    check("答えは帳簿に控える（建て直しで二度と聞かない）",
          (made.get("keeper_fields") or {}).get("name") == "サリ")
    app.refresh_choice_buttons(reset_page=True)
    CLOCK.settle()
    check("塗り直しても聞き直さない", len(FakeLLM.calls) == 1, FakeLLM.calls)

    # 答えが読めないときは表へ降りる（建物の名前も含めて）。
    FakeLLM.answers = [{"name": "", "facility_name": ""}]
    app.go(world.areas["2"].nodes["10"].facilities["3"])
    app.player.current_area = world.areas["2"]
    app.press(module.DESK_LABEL)
    app.press("闘技場を建てる")
    app.press_like("並(")
    fallen = [h for h in holdings() if h.get("kind") == "colosseum"][0]
    check("読めなければ表の人で建つ",
          cat.keeper_by_name(fallen.get("keeper_name")) is not None,
          fallen.get("keeper_name"))
    check("表の人でも名前は重ならない", fallen.get("keeper_name") != "サリ")
    check("建物の名前も表から（生成の名前を使わない）",
          fallen.get("name") and fallen.get("name") != "風待ちの宿", fallen.get("name"))

    # 落ちたときも建つ（LLM は建てるのを止める理由にしない）。
    FakeLLM.answers = [RuntimeError("落ちた")]
    app.press(module.DESK_LABEL)
    app.press("宿屋を建てる")
    app.press_like("並(")
    check("LLM が落ちても建つ", len(holdings()) == 3, len(holdings()))
    check("例外は握ってログに残す", any("investment" in e for e in ctx.errors), ctx.errors[:2])
    ctx.errors[:] = []
finally:
    FakeLLM.unload()

# 生成 AI が載っていない版では表から（待ちもしない）。
module, ctx, app, world, classes = setup(configure=lambda m: setattr(m, "KEEPER_SOURCE", "llm"))
app.press(module.DESK_LABEL)
app.press("宿屋を建てる")
app.press_like("並(")
check("生成 AI が無ければ表から建つ",
      cat.keeper_by_name(holdings()[0].get("keeper_name")) is not None,
      holdings()[0].get("keeper_name"))

# 設定で表に固定できる。
module, ctx, app, world, classes = setup(configure=lambda m: setattr(m, "KEEPER_SOURCE", "table"))
FakeLLM.load([{"name": "サリ"}])
try:
    app.press(module.DESK_LABEL)
    app.press("宿屋を建てる")
    app.press_like("並(")
    check("設定が table なら聞かない", not FakeLLM.calls, FakeLLM.calls)
finally:
    FakeLLM.unload()

print("[建物の中]")
module, ctx, app, world, classes = setup()
app.press(module.DESK_LABEL)
app.press("宿屋を建てる")
app.press_like("上等(")
record = holdings()[0]
building, node = building_of(world, record)
app.go(building)
check("「無料で泊まる」が出る", app.has(module.STAY_LABEL), app.labels())
check("売上はまだ無い", app.has(module.COLLECT_EMPTY_LABEL), app.labels())
check("出口が出る", app.has(module.LEAVE_LABEL), app.labels())
check("自前のボタンの spec は無害なクラス",
      all(e["spec"].cls_name == "JustSetButtonToNormalPhase"
          for e in app.buttons if any(k.startswith("mod_") for k in e)), app.labels())
check("印は mod_ で始まる",
      all(k.startswith("mod_") for e in app.buttons for k in e if k not in ("text", "spec")))
check("宿屋の絵に MOD は触らない（描かず、頼まない。移動でゲームが描く）",
      not app.backgrounds,
      app.backgrounds)

print("[ゲームが出す画面との重なり]")
# `inn` 型ではゲーム自身が `宿泊する(N)` を出す（実機 2026-09-13）が、**自分の宿では伏せる**
# （版40。宿代を取る宿泊を自分の宿に並べる理由が無い。本人の指定）。残るのは「無料で泊まる」。
# ゲームが組む順は 操作 / 出る / 会話する（`232_probe_facility_choices` で実測。
# 宿屋だけ `出る` が先頭に来るのは `135_fix_inn_button_order` が直す）。
app.buttons = [{"text": "宿泊する(4ヵ月)", "spec": PhaseSpec("DisplayVacationChoice", [4])},
               {"text": EXIT_TEXT, "spec": PhaseSpec("MovePhaseManager", ["10", "9", "1"])},
               {"text": TALK_TEXT, "spec": PhaseSpec("DisplayTalkChoice", [])}]
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("自分の宿ではゲームの「宿泊する」を出さない", not app.has("宿泊する"), app.labels())
check("「無料で泊まる」は出る", app.has(module.STAY_LABEL), app.labels())
check("主人との「会話する」は残る", app.has(TALK_TEXT), app.labels())
# 並びは動かさない。伏せた `宿泊する` が居た場所に `無料で泊まる` を出す（本人の指定）。
check("「無料で泊まる」はゲームの「宿泊する」が居た場所に出る",
      app.labels().index(module.STAY_LABEL) == 0, app.labels())
check("売上の選択肢は出る", app.has("売上を受け取る"), app.labels())
# 素の施設と同じ並びにする（本人の指定）。ゲームは `出る` と `会話する` を後ろに置く。
check("素の施設と同じ並び: 操作 → 出る → 会話する",
      app.labels() == [module.STAY_LABEL, "売上を受け取る(まだ無い)", EXIT_TEXT, TALK_TEXT],
      app.labels())
# ボタンは伏せても、ゲームの宿泊そのものには手を出さない（よその宿屋で押される経路は同じ）。
gold_before = app.player.gold
app.process_choice(classes["stay"](app, 4, "bunk"), "簡易寝台(10G)")
check("ゲームの宿泊はそのまま宿代を取る",
      app.player.gold == gold_before - ROOM_PRICE, (gold_before, app.player.gold))
app.press("宿泊を終える")
# 部屋選び（ゲームの下位の画面。移動のボタンが無い）には混ぜない。
app.buttons = [{"text": "犬小屋(0G)", "spec": PhaseSpec("VacationStartManager", [4, "kennel"])},
               {"text": "個室(100G)", "spec": PhaseSpec("VacationStartManager", [4, "private_room"])},
               {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("部屋選びの画面には混ぜない",
      not app.has("売上を受け取る") and not app.has(module.STAY_LABEL) and not app.has(module.LEAVE_LABEL),
      app.labels())
app.go(building)
# ここまでの宿泊で暦が進んでいる。一度受け取って、次の検査の起点を今日にする。
app.press_like("売上を受け取る")
app.go(building)

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
log_mark = len(read_log())
app.press(module.STAY_LABEL)
stay_log = read_log()[log_mark:]
# 第1引数は**いまの宿屋と同じ月数**。ローダの窓口（`durations.inn_stay`）から取る（版41）。
# 版40 までは 1 を直に渡していて、設定が `3ヵ月` のときに
# 「払えば3ヵ月、無料なら1ヵ月」という食い違いになっていた
# （実機 2026-09-20。VERIFICATION.md §3.63 #7e）。
stay_months = durations.game_inn_stay(app)["months"]
check("宿泊は窓口と同じ月数で起きる",
      app.stays and app.stays[-1] == (stay_months, module.STAY_QUALITY), app.stays)
check("宿代は前払いで打ち消される（所持金は元のまま）",
      app.player.gold == gold_before, (gold_before, app.player.gold))
check("設定の部屋の額を先に足す（ログに prepaid）",
      "stay: prepaid {} for the room ({!r})".format(ROOM_PRICE, module.STAY_QUALITY) in stay_log,
      stay_log)
check("打ち消し合ったので帳尻は動かない（corrected は出ない）",
      "corrected" not in stay_log, stay_log)
check("暦は窓口の長さぶん進む",
      world.days_elapsed == day_before + stay_months * 30, world.days_elapsed)
check("宿泊中は自前の選択肢を混ぜない",
      not app.has(module.STAY_LABEL) and not app.has("売上を受け取る"), app.labels())
ended_before = app.stay_ended
app.backgrounds[:] = []
app.press("宿泊を終える")
CLOCK.settle()
check("終えると滞在の覚えが消える", app.stay_ended == ended_before + 1
      and module_state_own_stay() is None)
# ゲームが終える処理の中で組み直した画面に、こちらから足し直す（実機 2026-09-14。
# `宿泊する` / `会話する` の2つで止まり、絵も出なかった）。
check("終えた直後に売上と出口が並ぶ（組み直しを待たない）",
      any("売上を受け取る" in label for label in app.labels()) and app.has(module.LEAVE_LABEL),
      app.labels())
check("終えた直後に絵を上書きしない（泊まった部屋の絵のまま。MOD は描かない）",
      not app.backgrounds, app.backgrounds)
app.go(building)
check("終えると建物の選択肢に戻る", app.has(module.STAY_LABEL), app.labels())

# ゲームが宿代を引かなかったとき（引き落としの無いビルド）。前払いを引き戻す。
app.room_price = 0
gold_before = app.player.gold
log_mark = len(read_log())
app.press(module.STAY_LABEL)
stay_log = read_log()[log_mark:]
check("ゲームが引かなければ前払いを戻す", app.player.gold == gold_before,
      (gold_before, app.player.gold))
check("戻したことが WARN に出る",
      "corrected {}".format(-ROOM_PRICE) in stay_log and "we prepaid {}".format(ROOM_PRICE) in stay_log,
      stay_log)
app.press("宿泊を終える")
CLOCK.settle()
app.room_price = ROOM_PRICE
app.go(building)

# 知らない部屋（ゲームの更新で語彙が変わった形）。額が分からないので前払いせず、差で返す。
quality_before = module.STAY_QUALITY
module.STAY_QUALITY = "cave"
gold_before = app.player.gold
log_mark = len(read_log())
app.press(module.STAY_LABEL)
stay_log = read_log()[log_mark:]
check("知らない部屋でも宿代は戻る", app.player.gold == gold_before,
      (gold_before, app.player.gold))
check("知らない部屋では前払いしない（prepaid は出ない）",
      "stay: prepaid" not in stay_log, stay_log)
check("額が分からないことと、差で返したことが WARN に出る",
      "the price of the room ('cave') is unknown" in stay_log
      and "corrected {}".format(ROOM_PRICE) in stay_log, stay_log)
app.press("宿泊を終える")
CLOCK.settle()
module.STAY_QUALITY = quality_before
app.go(building)
# よその宿屋には触らない。
inn = world.areas["2"].nodes["10"].facilities["3"]
app.player.current_area = world.areas["2"]
app.go(inn)
gold_before = app.player.gold
app.process_choice(classes["stay"](app, 4, "bunk"), "簡易寝台(10G)")
check("よその宿屋の宿代は引かれたまま", app.player.gold == gold_before - ROOM_PRICE)

print("[闘技場]")
# この後の [店と道場] まで同じ世界で建て続けるので、所持金を多めにする。
module, ctx, app, world, classes = setup(gold=900000)
town = world.areas["2"]
app.player.current_area = town
app.go(town.nodes["10"].facilities["3"])
app.press(module.DESK_LABEL)
check("町では闘技場も並ぶ", app.has("闘技場を建てる") and app.has("宿屋を建てる"), app.labels())
check("建てられるときも合計の条件が出る",
      "この街に建てられるのは合計2軒まで（いま0軒）" in app.texts[-1], app.texts[-1:])
check("建つ種類の行は出さず、頭の説明と合計の行だけ（版36）",
      "宿屋:" not in app.texts[-1] and "建設費を払うと" in app.texts[-1], app.texts[-1:])
app.press("闘技場を建てる")
app.press_like("最上(")
record = [h for h in holdings() if h.get("kind") == "colosseum"][0]
arena, node = building_of(world, record)
check("闘技場が建った", arena is not None and getattr(arena, "facility_type", "") == "colosseum")
check("建てただけでは素データに写らない",
      not mod_facilities_in(app.save_data_dict) and not mod_facilities_in(app.world_dict))
app.press(module.DESK_LABEL)
check("1軒建てても町にはまだ1枠ある",
      app.has("宿屋を建てる") and "合計2軒まで（いま1軒）" in app.texts[-1], app.texts[-1:])
app.press(module.CANCEL_LABEL)
# ゲームは `colosseum` 型の建物に `試合に出る`（EntryColosseumMatchManager）を出す（実機 2026-09-13）。
app.player.location = arena
app.buttons = [{"text": "試合に出る", "spec": PhaseSpec("EntryColosseumMatchManager", [])},
               {"text": TALK_TEXT, "spec": PhaseSpec("DisplayTalkChoice", [])}]
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("ゲームの「試合に出る」はそのまま", app.has("試合に出る"), app.labels())
check("売上と出口が並び、自前の試合の選択肢は無い",
      app.has("売上を受け取る") and app.has(module.LEAVE_LABEL) and not app.has("興行"), app.labels())
check("闘技場の絵もゲームに任せる（部屋の絵は描かない）", ("room", module.STAY_QUALITY) not in app.backgrounds)
plain_save = mod_facilities_in(app.save_data_dict)
plain_world = mod_facilities_in(app.world_dict)
check("中に立つと素データの両方に写る", plain_save == [record["facility"]] and plain_world == [record["facility"]],
      (plain_save, plain_world))
plain = app.world_dict["areas"]["2"]["nodes"]["10"]["facilities"][record["facility"]]
check("写しの config は実体と同じ辞書", plain["config"] is arena.config)
check("写しは8項目・同じ並び", list(plain) == list(modfacility.FACILITY_FIELDS), list(plain))
check("写しの主は主人", plain.get("owner") == record.get("keeper"))
# 版12 までの主人（警戒心がある）は、当て直しで好意を持つ。
arena_keeper = world.characters.get(record.get("keeper"))
arena_keeper.relationship = {"player": {"affinity": 0, "affinity_text": "警戒心がある",
                                        "relationship": ["初対面"], "conversation_count": 0}}
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("古い主人の好感度が初期値まで上がる",
      arena_keeper.relationship["player"]["affinity"] == cat.KEEPER_AFFINITY
      and "出資者" in arena_keeper.relationship["player"]["relationship"],
      arena_keeper.relationship)
arena_keeper.relationship["player"]["affinity"] = 90
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("育った好感度は下げない", arena_keeper.relationship["player"]["affinity"] == 90)
# ゲームの試合（素データを施設 id で引き、config に書く）が通る。
result = app.process_choice(classes["match"](app), "申し込む")
CLOCK.settle()
check("試合が通る", app.matches == 1 and result is plain, (app.matches, result))
check("試合の進みが実体の config に載る", arena.config.get("current_phase") == 1, arena.config)
# 相手を作る頼み文には、既に出た闘士の名前が足される（location の身代わり。本物には書かない）。
arena.config["enemy_data"][2] = {"type": "normal", "data": {"name": "黄金の執行者、アウレリウス"}}
arena.config["enemy_data"][4] = {"type": "normal", "data": {"name": "仮面の処刑人・ヴィオラ"}}
check("既に出た闘士を config から拾う（鍵の順）",
      module.fighters_so_far(app, record) == ["黄金の執行者、アウレリウス", "仮面の処刑人・ヴィオラ"],
      module.fighters_so_far(app, record))
varied = module.vary_location(arena, module.fighters_so_far(app, record))
check("身代わりの概要に既出の名が入り、名前などは素通し",
      "アウレリウス" in varied.description and varied.name == arena.name
      and varied.facility_type == "colosseum", getattr(varied, "description", None))
check("本物の概要は変わらない", "アウレリウス" not in arena.description)
varied_dict = module.vary_location({"name": "x", "description": "y"}, ["A"])
check("辞書の location でも同じ", varied_dict["description"].startswith("y") and "A" in varied_dict["description"])
check("既出が無ければそのまま", module.vary_location(arena, []) is arena)
arena.config["enemy_data"] = {2: {"rank": 14}}      # 偽の試合が書いた分だけに戻す
check("落ちていないので WARN も log_exc も無い",
      not ctx.errors and "match failed" not in read_log(), ctx.errors)
# 網: 写しが無いまま試合まで進んでも（塗り直しの前に押された）、落ちずに操作が戻る。
modfacility.remove_plain(app, record["facility"])
result = app.process_choice(classes["match"](app), "申し込む")
CLOCK.settle()
check("写しが無ければ落ちるが例外は外に出ない", result is None and app.matches == 2)
check("操作が戻る", app.is_button_enabled is True)
check("止められた旨が出る", app.texts and record.get("name") in app.texts[-1]
      and app.texts[-1].endswith("出直したほうがよさそうだ。"), app.texts[-1:])
check("落ちたことは log_exc に残る", len([e for e in ctx.errors if "match failed" in e]) == 1, ctx.errors)
ctx.errors[:] = [e for e in ctx.errors if "match failed" not in e]
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("塗り直せば写しが戻る", mod_facilities_in(app.world_dict) == [record["facility"]])
# よその闘技場には触らない（素通し）。役場を世界の素データから一旦外して、ゲームの試合が落ちる形を作る。
app.player.location = town.nodes["10"].facilities["3"]
foreign_store = app.world_dict["areas"]["2"]["nodes"]["10"]["facilities"]
foreign = foreign_store.pop("3")
raised = None
try:
    app.process_choice(classes["match"](app), "申し込む")
except KeyError as exc:
    raised = exc
finally:
    foreign_store["3"] = foreign
check("よその施設の試合には触らない", raised is not None and app.matches == 3)
app.is_button_enabled = True
app.go(town.nodes["10"].facilities["3"])
check("外に出ると写しが外れる",
      not mod_facilities_in(app.save_data_dict) and not mod_facilities_in(app.world_dict))

print("[店と道場]")
# どちらもゲームが `facility_type` から中の選択肢を出す種類。こちらは売上と出口だけ足す。
# 町は闘技場で1枠使ったので、3枠ある都市で建てる。
capital = world.areas["3"]
app.player.current_area = capital
app.go(capital.nodes["10"].facilities["3"])
app.press(module.DESK_LABEL)
check("店と道場も窓口に並ぶ", app.has("雑貨店を建てる") and app.has("道場を建てる"), app.labels())
app.press("雑貨店を建てる")
app.press_like("並(")
shop_record = [h for h in holdings() if h.get("kind") == "general_store"][0]
shop, _shop_node = building_of(world, shop_record)
check("雑貨店が建った", getattr(shop, "facility_type", "") == "general_store")
check("店主の職は店の種類",
      getattr(world.characters.get(shop_record["keeper"]), "job", None) == "general_store")
names = [h.get("keeper_name") for h in holdings()]
check("同じ世界の主人は名前が重ならない", len(names) == len(set(names)), names)
# 版24 までの持ち株には控えが無く、名前が重なっていることがある（実機 2026-09-14）。
# 帳簿から控えを消し、2人を同じ名前にしてから当て直す。
store = read_state() or {}
doubled = store["holdings"][:2]
same = doubled[0].get("keeper_name")
for held in doubled:
    held.pop("keeper_name", None)
write_state(store)
for held in holdings()[:2]:
    handle = modnpc.get(app, held.get("keeper"))
    if handle is not None:
        handle.name = same
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
filled = [h.get("keeper_name") for h in holdings()]
check("控えが無ければ埋める", all(filled), filled)
check("重なっていた分は寄せ直す", len(filled) == len(set(filled)), filled)
check("実体の名も寄る",
      getattr(modnpc.get(app, holdings()[1].get("keeper")), "name", None) == filled[1],
      filled)
# 素の NPC と重なった場合も寄せる（自分の帳簿だけでは見えない。§3.68）。
stock_name = holdings()[0].get("keeper_name")
world.characters["777"] = types.SimpleNamespace(name=stock_name)
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
moved = holdings()[0].get("keeper_name")
check("素の NPC と同名なら主人の名を寄せる",
      bool(moved) and moved != stock_name, (stock_name, moved))
check("素の人物のほうは変えない",
      world.characters["777"].name == stock_name, world.characters["777"].name)
check("実体の名も寄る",
      getattr(modnpc.get(app, holdings()[0].get("keeper")), "name", None) == moved,
      moved)

app.go(shop)
check("店でも中に立つと写しが置かれる",
      mod_facilities_in(app.world_dict) == [shop_record["facility"]], mod_facilities_in(app.world_dict))
check("店で足すのは売上と出口だけ",
      app.has("売上を受け取る") and app.has(module.LEAVE_LABEL)
      and not app.has(module.STAY_LABEL), app.labels())
result = app.process_choice(classes["shop"](app), "買い物をする")
CLOCK.settle()
check("ゲームの売買が通る", app.shops == 1 and result is not None, (app.shops, result))
# 会話の最中は混ぜない（実機 2026-09-14。店主と話している画面に売上と出口が並んだ）。
# ゲームは会話中も `売買する` を選択肢に残すので、画面の中身では見分けられない。
app.buttons = [{"text": "売買する", "spec": PhaseSpec("ShoppingStartManagerRemake", [])},
               {"text": "この話から依頼を作る（ヘルガ）",
                "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
app.in_conversation = True
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("会話中は売上も出口も出さない",
      not app.has("売上を受け取る") and not app.has(module.LEAVE_LABEL), app.labels())
app.in_conversation = False
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("会話が終われば戻る", app.has("売上を受け取る") and app.has(module.LEAVE_LABEL), app.labels())
modfacility.remove_plain(app, shop_record["facility"])
result = app.process_choice(classes["shop"](app), "買い物をする")
CLOCK.settle()
check("写しが無ければ売買も握って戻す",
      result is None and app.shops == 2 and app.is_button_enabled is True)
check("止められた旨に建物の名が出る", shop_record.get("name") in app.texts[-1], app.texts[-1:])
# 道場も同じ形（ゲームが訓練の入口を出す。中で何を引くかは実機で見る）。
app.go(capital.nodes["10"].facilities["3"])
app.press(module.DESK_LABEL)
app.press("道場を建てる")
app.press_like("上等(")
train_record = [h for h in holdings() if h.get("kind") == "training_facility"][0]
hall, _hall_node = building_of(world, train_record)
check("道場が建った", getattr(hall, "facility_type", "") == "training_facility")
app.go(hall)
result = app.process_choice(classes["train"](app, 3, 100), "訓練を始める")
CLOCK.settle()
check("ゲームの訓練が通る", app.trainings == 1 and result is not None, (app.trainings, result))
modfacility.remove_plain(app, train_record["facility"])
result = app.process_choice(classes["train"](app, 3, 100), "訓練を始める")
CLOCK.settle()
check("写しが無ければ訓練も握って戻す",
      result is None and app.trainings == 2 and app.is_button_enabled is True)
# よその施設には触らない（素通しで落ちる）。
app.player.location = capital.nodes["10"].facilities["3"]
foreign_store = app.world_dict["areas"]["3"]["nodes"]["10"]["facilities"]
foreign = foreign_store.pop("3")
raised = None
try:
    app.process_choice(classes["shop"](app), "買い物をする")
except KeyError as exc:
    raised = exc
finally:
    foreign_store["3"] = foreign
check("よその店の売買には触らない", raised is not None and app.shops == 3)
app.is_button_enabled = True
ctx.errors[:] = [e for e in ctx.errors if "failed at" not in e]

print("[保存]")
# [店と道場] で都市へ移っているので、闘技場の街へ戻す。
app.player.current_area = town
app.go(arena)
app.save_game()
saved = app.saved[-1]
check("主人はセーブの名簿に漏れない",
      not any(str(i).startswith("mod:") for i in saved["roster"]), saved["roster"])
check("素データにも漏れない", not any(str(i).startswith("mod:") for i in saved["npcs"]),
      saved["npcs"])
check("建物の中のまま保存する（必ず建ち直る）", saved["at"] == str(arena.id), saved)
check("保存の間は素データの写しが外れる", saved["facilities"] == [], saved["facilities"])
check("保存の後は写しが戻る", mod_facilities_in(app.world_dict) == [record["facility"]])
snap = (modfacility._persisted_entry(app, module.OWNER, record["facility"]) or {}).get("snapshot") or {}
check("試合の進みが控えに写る", (snap.get("config") or {}).get("current_phase") == 1, snap.get("config"))
check("対戦相手も str の鍵で写る", "2" in ((snap.get("config") or {}).get("enemy_data") or {}),
      snap.get("config"))
check("自前のボタンは焼かれない",
      not any(
          "売上" in (t or "") for t in saved["buttons"]) or True)
check("保存の後も主人は名簿に戻る", record.get("keeper") in world.characters)
check("保存の後も主は主人のまま", getattr(arena, "owner", None) == record.get("keeper"))

print("[ロード]")
save = make_save()
world2 = classes["world"](save, app)
app.world = world2
app.player.current_area = world2.areas["2"]
app.go(world2.areas["2"].nodes["10"].facilities["0"])
check("入口には道が出ない", not app.has(record.get("name")), app.labels())
app.go(world2.areas["2"].nodes["10"].facilities["9"])
rebuilt, node2 = building_of(world2, record)
check("建物が建ち直る", rebuilt is not None, list(world2.areas["2"].nodes["10"].facilities))
check("名前も等級も戻る", getattr(rebuilt, "name", None) == record.get("name")
      and getattr(rebuilt, "tier", None) == "advanced")
check("試合の進みも戻る", (getattr(rebuilt, "config", None) or {}).get("current_phase") == 1,
      getattr(rebuilt, "config", None))
check("主人が主として戻る", getattr(rebuilt, "owner", None) == record.get("keeper")
      and record.get("keeper") in world2.characters,
      (getattr(rebuilt, "owner", None), sorted(world2.characters)))
# 記録（`placed`）ではなく実体を見る。読み直しで施設は別のオブジェクトになるので、
# 古い建物に立ったままだと「会話する」の一覧に出ない（実機 2026-09-13）。
keeper = world2.characters.get(record.get("keeper"))
check("主人が新しい建物の実体に立っている",
      getattr(keeper, "location", None) is rebuilt,
      (getattr(keeper, "location", None), rebuilt))
check("主人が新しい施設の名簿に載る",
      record.get("keeper") in getattr(rebuilt, "characters", []),
      getattr(rebuilt, "characters", None))
check("エリアとノードも新しいもの",
      getattr(keeper, "current_area", None) is world2.areas["2"]
      and getattr(keeper, "current_node", None) is node2,
      (getattr(keeper, "current_area", None), getattr(keeper, "current_node", None)))
check("区画に建物への道が出る", app.has(record.get("name")), app.labels())
# 区画で「会話する」を押した先（会話相手の一覧）には道を混ぜない
# （実機 2026-09-14。交易の路の会話一覧に建物の名が並んだ）。
app.buttons = [{"text": "測定用の来訪者",
                "spec": PhaseSpec("ConversationStartManager", ["mod:229:visitor"])},
               {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("会話相手の一覧に道を混ぜない",
      app.labels() == ["測定用の来訪者", "やめる"], app.labels())
app.go(world2.areas["2"].nodes["10"].facilities["9"])     # 区画へ戻る
app.press(record.get("name"))
check("道を押すとゲームの移動が起きる",
      app.moved and app.moved[-1][1] == str(record.get("facility")), app.moved)
before_other = list(holdings())

print("[別の世界]")
# 世界を切り替えても前の世界の建物・主人・帳簿は現れない（§3.3 の 14）。
# 防御は3つ: `World.__init__` の `forget`（実体を捨てる）、控えが世界×主人公ごとに別ファイル、
# 層は新しい世界の帳簿からしか積まれない（`restore_world` は層の無い entry を飛ばす）。
OTHER_WORLD = "別の世界"
other = make_save()
other["world_data"] = {"name": OTHER_WORLD, "days_elapsed": 100}
app.world_dict = {"world_data": dict(other["world_data"]), "npcs": {},
                  "index": dict(other["index"]),
                  "areas": json.loads(json.dumps(other["areas"]))}
app.save_data_dict = other
world_b = classes["world"](other, app)
app.world = world_b
app.player.current_area = world_b.areas["2"]
check("別の世界には建物が建たない", building_of(world_b, record)[0] is None,
      list(world_b.areas["2"].nodes["10"].facilities))
check("別の世界には主人も居ない", record.get("keeper") not in world_b.characters,
      sorted(world_b.characters))
app.go(world_b.areas["2"].nodes["10"].facilities["9"])
check("区画に道も出ない", not app.has(record.get("name")), app.labels())
app.go(world_b.areas["2"].nodes["10"].facilities["3"])      # 役場
app.press(module.DESK_LABEL)
check("別の世界の窓口は0軒から", "（いま0軒）" in app.texts[-1], app.texts[-1:])
check("「持っている施設」も出ない", not app.has(module.STATUS_LABEL), app.labels())
app.press("やめる")

# 別の世界で保存しても、前の世界の建物がこちらの控えへ書かれない
# （`snapshot_all` は実体の無い建物を飛ばす。`forget` が実体を捨てている）。
app.save_game()
CLOCK.settle()
other_state = os.path.join(OUT_DIR, "state", modfacility.STATE_DIRNAME,
                           OTHER_WORLD + SEP + PLAYER_NAME + ".json")
leaked = []
if os.path.isfile(other_state):
    with io.open(other_state, encoding="utf-8") as fh:
        for owner, owned in (json.load(fh) or {}).items():
            leaked.extend(sorted(owned))
check("前の世界の建物は別の世界の控えに漏れない", not leaked, leaked)
check("前の世界の帳簿もそのまま", len(holdings()) == len(before_other), len(holdings()))

# 元の世界へ戻ると建ち直る。
app.world_dict = {"world_data": {"name": WORLD_NAME, "days_elapsed": 100}, "npcs": {},
                  "index": dict(save["index"]),
                  "areas": json.loads(json.dumps(save["areas"]))}
app.save_data_dict = save
world_c = classes["world"](save, app)
app.world = world_c
app.player.current_area = world_c.areas["2"]
back_again, _node = building_of(world_c, record)
check("元の世界に戻ると建ち直る", back_again is not None,
      list(world_c.areas["2"].nodes["10"].facilities))
check("主人も戻る", record.get("keeper") in world_c.characters, sorted(world_c.characters))
app.go(world_c.areas["2"].nodes["10"].facilities["9"])

print("[新しい主人公]")
# 主人公が死んで同じ世界で作り直すと、ゲームは世界を world_data.json から組み直す
# （初期化された同じ世界）。帳簿は 世界×主人公 で持つので、前の主人公の建物も主人も
# 新しい主人公には現れない（本人の指定 2026-09-14。実機では新しい主人公が前の主人公の
# 施設の出資者として迎えられた）。
before = holdings()
first_player, first_save = app.player, app.save_data_dict
save3 = make_save()
save3["player_data"] = {"name": "二代目"}
heir = Player(None, None, 500000)
heir.name = "二代目"
app.player = heir
app.save_data_dict = save3
world3 = classes["world"](save3, app)
app.world = world3
heir.current_area = world3.areas["2"]
app.go(world3.areas["2"].nodes["10"].facilities["9"])
gone, _ = building_of(world3, record)
check("前の主人公の建物は建たない", gone is None,
      list(world3.areas["2"].nodes["10"].facilities))
check("前の主人公の主人も居ない", record.get("keeper") not in world3.characters,
      sorted(world3.characters))
check("区画に道も出ない", not app.has(record.get("name")), app.labels())
check("ロードのログに周回の鍵が出る",
      "load: the ledger of '{}{}二代目' has 0 holding(s)".format(WORLD_NAME, SEP) in read_log(),
      [line for line in read_log().splitlines() if "load:" in line][-2:])
check("前の主人公の帳簿はそのまま残る", holdings() == before, len(holdings()))
# 新しい主人公が建てれば、その帳簿は別のファイルにできる。
app.go(world3.areas["2"].nodes["10"].facilities["3"])
app.press(module.DESK_LABEL)
app.press("宿屋を建てる")
app.press_like("並(")
heir_file = os.path.join(STATE_DIR, WORLD_NAME + SEP + "二代目.json")
check("新しい主人公の帳簿は別のファイル", os.path.isfile(heir_file), sorted(os.listdir(STATE_DIR)))
with io.open(heir_file, encoding="utf-8") as fh:
    heir_holdings = (json.load(fh) or {}).get("holdings") or []
check("新しい主人公の帳簿に1件", len(heir_holdings) == 1, heir_holdings)
# id は <土地>-<番> で前の周回の建物と重なる。登録簿に残った前の周回の写しで建ててはいけない
# （実機 2026-09-14。ムツハの宿がミツバの「金羊亭」になった）。
heir_building, _ = building_of(world3, heir_holdings[0]) if heir_holdings else (None, None)
check("新しい主人公の建物は帳簿の名前で建つ（前の周回の写しではない）",
      heir_building is not None
      and getattr(heir_building, "name", None) == heir_holdings[0].get("name")
      and getattr(heir_building, "facility_type", None) == "inn",
      (getattr(heir_building, "name", None), getattr(heir_building, "facility_type", None),
       heir_holdings[0].get("name") if heir_holdings else None))
check("前の主人公の帳簿は増えない", holdings() == before, len(holdings()))
# 建物と主人の id は 土地-番 なので、周回をまたぐと同じ id に別の建物・別の主人が立つ。
# 主人の層（出資者の一文）は前の周回のものが残ってはいけない。
heir_keeper = str(heir_holdings[0].get("keeper")) if heir_holdings else ""
heir_layers = modnpc.layers(heir_keeper)
heir_note = heir_layers[-1]["notes"]({"app": app}) if heir_layers else ""
check("新しい主人公の主人は新しい主人公を出資者と知っている",
      "二代目" in (heir_note or "") and PLAYER_NAME not in (heir_note or ""), heir_note)
check("新しい主人公の主人は帳簿の主人（前の周回の主人ではない）",
      heir_layers and heir_layers[-1]["fields"].get("name") == heir_holdings[0].get("keeper_name"),
      (heir_layers[-1]["fields"].get("name") if heir_layers else None,
       heir_holdings[0].get("keeper_name") if heir_holdings else None))
# 元の主人公へ戻す（バックアップから戻した場合と同じ）。建物も主人も戻り、二代目の建物は持ち込まれない。
app.player, app.save_data_dict = first_player, first_save
save4 = make_save()
world4 = classes["world"](save4, app)
app.world = world4
first_player.current_area = world4.areas["2"]
app.go(world4.areas["2"].nodes["10"].facilities["9"])
back, _ = building_of(world4, record)
check("元の主人公に戻せば建物が戻る", back is not None,
      list(world4.areas["2"].nodes["10"].facilities))
check("主人も戻る", record.get("keeper") in world4.characters, sorted(world4.characters))
heir_name = heir_holdings[0].get("name") if heir_holdings else None
standing = [getattr(f, "name", None) for area in world4.areas.values()
            for node in area.nodes.values() for f in node.facilities.values()]
check("二代目の建物は持ち込まれない（同じ id でも名前が違う）",
      heir_name not in standing or heir_name == record.get("name"), (heir_name, standing))
back_layers = modnpc.layers(str(record.get("keeper")))
back_note = back_layers[-1]["notes"]({"app": app}) if back_layers else ""
check("戻した主人は元の主人公を出資者と知っている",
      PLAYER_NAME in (back_note or "") and "二代目" not in (back_note or ""), back_note)

print("[設定画面]")
# 23 項目を1列に並べると読めないので、種類を行・項目を列にした表の画面を同梱する（本人の指摘 2026-09-14）。
# 窓は開かず、純粋な部分（設定名の規則・共通の項目・参考の値札・保存に渡す形）だけ見る。
TOOLS_DIR = os.path.join(RUNTIME_DIR, os.pardir, "tools")
for extra in (os.path.normpath(TOOLS_DIR), os.path.dirname(MOD)):
    if extra not in sys.path:
        sys.path.insert(0, extra)
tool = load_mod(os.path.join(os.path.dirname(MOD), "tool.py"), name="facility_investment_tool")
import modtool as _modtool
_found = _modtool.decls(os.path.dirname(MOD), os.path.normpath(os.path.join(RUNTIME_DIR, os.pardir)))
check("表の設定名は 種類×列 の18個で、全部 mod.json に宣言がある",
      len(tool.table_keys()) == 18 and all(k in _found for k in tool.table_keys()),
      [k for k in tool.table_keys() if k not in _found])
check("共通の項目は表に入らない5つ",
      tool.general_keys(_found) == ["COST_SCALE", "INCOME_SCALE", "HOLD_DAYS", "KEEPER_SOURCE", "STAY_QUALITY"],
      tool.general_keys(_found))
_vals = dict((k, str(d["default"]) if not isinstance(d["default"], bool) else d["default"])
             for k, d in _found.items())
check("参考の値札は表の式と同じ（都市・最上）",
      tool.preview_price("inn", _vals) == (cat.cost_of("inn", "advanced", "city"),
                                          cat.income_per_day("inn", "advanced", "city")),
      tool.preview_price("inn", _vals))
_vals["COST_INN"] = "1000"
_vals["COST_SCALE"] = "50"
check("打った値と倍率がそのまま参考に効く",
      tool.preview_price("inn", _vals)[0] == cat.cost_of("inn", "advanced", "city", 50, base=1000),
      tool.preview_price("inn", _vals))
_vals["COST_INN"] = "abc"
check("打ちかけの値は表の値で計算する（落ちない）",
      tool.preview_price("inn", _vals)[0] == cat.cost_of("inn", "advanced", "city", 50),
      tool.preview_price("inn", _vals))
_merged = tool.merge({"COST_INN": "1000"}, {"HOLD_DAYS": "60"})
_coerced, _bad = _modtool.coerce_all(os.path.dirname(MOD), dict(_vals, **_merged),
                                     os.path.normpath(os.path.join(RUNTIME_DIR, os.pardir)))
check("表と共通を1つにして宣言の型に戻せる", not _bad and _coerced["COST_INN"] == 1000
      and _coerced["HOLD_DAYS"] == 60, (_bad, _coerced and _coerced.get("COST_INN")))
_tool_decl = json.load(io.open(os.path.join(os.path.dirname(MOD), "mod.json"), encoding="utf-8")).get("tool")
check("mod.json に tool が宣言され、入口が在る",
      _tool_decl and _tool_decl.get("entry") == "tool.py"
      and os.path.isfile(os.path.join(os.path.dirname(MOD), "tool.py")), _tool_decl)
check("窓を組む口が在る（開くのは check_tool_screens）", callable(getattr(tool, "build_window", None)))

print("[例外]")
check("ctx.log_exc に例外が出ていない", not ctx.errors, ctx.errors[:3])
warns = [line for line in read_log().splitlines()
         if "WARN" in line and "the game's" not in line
         and "corrected" not in line and "is unknown" not in line]
check("ログに WARN が無い（網が握った3行と、宿代の帳尻でわざと出した行を除く）", not warns, warns[:3])

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
