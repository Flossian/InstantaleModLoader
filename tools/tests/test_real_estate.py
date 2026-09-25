# -*- coding: utf-8 -*-
"""330_real_estate をゲーム抜きで通す。

    python tools/tests/test_real_estate.py

（開発中は `914_real_estate` / `test_wip_real_estate.py` だった。）

偽の app / Player / Area / Node / Facility / PhaseSpec / VacationStartManager /
Character / InventoryGrid / HUD / Clock / llm_manager を差し込み、次を確認する。

  窓口   … 役場でだけ「家を借りる・買う」が出る。宿屋では出ない。塗り直しても増えない
  契約   … 借りると所持金が減り、広場と建物の接続が両側に張られ、控えに1件残る
  一覧   … ゲームが並べなかった建物への道を、`MovePhaseManager` のボタンで足す
  建物   … 中に立つと「滞在する」「保管庫をあける」が出る。他人の施設では出ない
  管理人 … 契約ごとに1人立ち、建物の主になる。素性は契約の1回だけ生成 AI に聞き、
           読めない・落ちた・無い版では表の12人へ降りる。ロードでは聞き直さない。
           解約すると `modnpc` から降り、控えからも消える
  滞在   … `VacationStartManager` をゲームの経路で起こし、**宿代を前払いする**
           （ゲームが引いて所持金が元に戻る）。主はその建物の管理人で、
           管理人が居ない契約でだけ役人を借りる（WARN）。宿屋での宿泊には手を出さない
  帳尻   … ゲームが引かなかったときは前払いを引き戻す。額が分からない等級では
           差で返し、WARN を残す
  家賃   … 日数が進むと期限のぶんだけ引かれ、期限が延びる。何期ぶんか飛んでもまとめて払う。
           滞在の最中に来た期限は見送り、滞在が終わってから1回で払う
  期限   … 払えなければ契約が切れ、建物が街から消え、保管庫の中身は役場が預かる
  引取   … 役場で料金を払うと預かり品がプレイヤーの持ち物へ戻る
  ロード … `World.__init__` の後に建物が建ち直る（セーブには何も書かない）
  周回   … 控えは 世界×主人公（同じ世界で新しい主人公を作っても前の主人公の家は現れない）
  保管庫 … 窓の右に立つのは管理人。移した品は往復とも控えへ写り、`save_game` が走る。
           窓を閉じると管理人の手元は空になる（`modnpc` の控えと二重にしない）
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
from instantale_modloader import durations            # noqa: E402
from instantale_modloader import llm as loader_llm    # noqa: E402
from instantale_modloader import modfacility            # noqa: E402
from instantale_modloader import modnpc                 # noqa: E402
from instantale_modloader import state as loader_state  # noqa: E402


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

#: LLM へ出ていく文章を捕まえる口はローダが持っている（`llm.wrap_outgoing`）。
#: 偽の環境には送り口も `ctx.resolve` も無いので、掛けるのはやめて
#: **渡された書き換えの関数だけ**を控え、こちらから直に呼ぶ。
PROMPT_HOOK = {}


def capture_outgoing(ctx, rewrite, **kwargs):
    PROMPT_HOOK["rewrite"] = rewrite
    return None


loader_llm.wrap_outgoing = capture_outgoing

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
WORLD_NAME = "テスト世界"
PLAYER_NAME = "テストプレイヤー"
SEP = loader_state.PLAYTHROUGH_SEP
#: 控えは 世界×主人公 で1ファイル（`state.playthrough_key`）。
STATE_FILE = WORLD_NAME + SEP + PLAYER_NAME + ".json"
EXIT_TEXT = "出る"
TALK_TEXT = "会話する"
OFFICE_TYPE = "administrative_office"
INN_TYPE = "inn"

#: 宿屋が取る宿代（滞在で返されるのはこの額）。
ROOM_PRICE = 100

#: 役場の役人の id（家の主として据えられる相手）。
CLERK_ID = "9"


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
        # **主の記録に書き足す**（実機の形。素データの欄が `None` のままだと
        # `AttributeError: 'NoneType' object has no attribute 'append'` で落ち、
        # 画面には「落ち着かない。今日は出直したほうがよさそうだ。」が出た）。
        here = self.app.player.location
        owner = getattr(here, "owner", None)
        keeper = (getattr(getattr(self.app, "world", None), "characters", None)
                  or {}).get(str(owner)) if owner else None
        if keeper is not None:
            keeper.current_log.append("<宿泊: 客を迎えた。>")
        # 進む日数は月数×30。ただし `315_vacation_custom` の週単位は
        # ゲームに渡す月数を1にしたまま、日数だけを縮める（実機）。
        days = getattr(self.app, "short_stay_days", None) or int(self.months) * 30
        self.app.elapse_days(int(days))
        # 徴収を止めたビルドの代わり（`stay_charges` を落とすと引かない）。
        # 前払いした額が宙に浮かないかを見る。
        if getattr(self.app, "stay_charges", True):
            self.app.player.gold -= ROOM_PRICE
        # 同じ区間で他の MOD が引いた額（暦が進んで来た家賃など）。
        self.app.player.gold -= getattr(self.app, "other_charge", 0)
        # ゲームは部屋の絵に差し替える（GAME.md §2.17 の実測）。
        self.app.change_background_image_to_inn_room(self.quality)
        if getattr(self.app, "stay_raises", False):
            # 主のいない施設で実機が踏んだ形（`ufl_method_1` の KeyError: None）。
            self.app.is_button_enabled = False
            raise KeyError(None)
        # ゲームは宿泊が始まると活動の選択肢へ差し替える（GAME.md §2.17）。
        # 活動の選択肢は本物と同じクラス（GAME.md §2.17）。`DisplayTalkChoice` は施設の入口の
        # 種類（`TOP_ENTRY_CLASSES`）なので、活動に使うと下位の画面が施設の画面に見える。
        self.app.buttons = [
            {"text": "休養をとる", "spec": PhaseSpec("VacationRestManager", [])},
            {"text": "他者と交流", "spec": PhaseSpec("VacationSocializeManager",
                                                 [self.months, self.quality])},
            # 実機ではここに載るのは `NotImplementedManager`（押しても何も起きない
            # 未実装の置き場所。実測）。`ItemCraftManager` ではない。
            {"text": "アイテム作成", "spec": PhaseSpec("NotImplementedManager", [])},
            {"text": "宿泊を終える", "spec": PhaseSpec("VacationEndManager", [])},
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


class VacationSocializeManager:
    """社交の入口。自分の家では選択肢ごと出さない。"""

    def __init__(self, app, months, quality):
        self.app = app
        self.months = months
        self.quality = quality

    def execute(self, choice_text=""):
        self.app.socialized += 1
        return None


class NotImplementedManager:
    """ゲームの未実装の置き場所。宿泊の `アイテム作成` はこれが載っている（実測）。

    自分の家では選択肢ごと出さない。
    """

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text=""):
        self.app.crafted += 1
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
        # 実機の `World.__init__` は、**返る時点でエリアと施設を組み終えている**
        # （だから MOD の当て直しがそこを読める）。セーブに `areas` があれば組む。
        for area_id, area_data in ((save_data_dict or {}).get("areas") or {}).items():
            area = Area(str(area_id), area_data.get("name") or "")
            for node_id, node_data in (area_data.get("nodes") or {}).items():
                node = Node(str(node_id))
                node.entrance_facility = node_data.get("entrance_facility")
                for fid, fdata in (node_data.get("facilities") or {}).items():
                    node.facilities[str(fid)] = Facility(app, node, dict(fdata))
                area.nodes[str(node_id)] = node
            self.areas[str(area_id)] = area


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
        self.gold = 0
        self.config = {}
        # 残りの項目（`job` / `profile` / `relationship` …）は素直に属性へ。
        # `modnpc.build` はひな型の33項目を名前で渡してくる（**kwargs を持つ型には全部）。
        for key, value in kwargs.items():
            setattr(self, key, value)
        # 入れ物は本体と同じく `__init__` の外で持つ（ひな型の `inventory` で潰さない）。
        self.inventory = Inventory()
        self.equipments = {}


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
        self.age = 31                      # 30代＝素のゲームなら 3+1=4ヵ月
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
        self.socialized = 0
        self.crafted = 0
        self.stay_ended = 0
        self.saves = 0
        self.saved_at = []
        self.saved_buttons = []
        self.saved_shopping = []
        self.windows = []
        self.windows_from_clock = []
        self.backgrounds = []
        self.background_raises = False
        self.made_items = []
        self.process_choice_calls = []
        self.is_button_enabled = True
        self.stay_raises = False
        #: 他 MOD が宿泊の日数を縮めている形（`315_vacation_custom` の週単位）。
        self.short_stay_days = None
        self.is_adding_text = False
        # 本文の流し込みを真似るか（実機は流している間 `is_adding_text` が立つ）。
        self.streaming = False
        self.is_popup_window_opened = False
        self.in_battle = False
        self.in_conversation = False
        self.in_shopping = False
        self.hud = HUD_CLS()

    # -- ゲーム自身の入口 --------------------------------------------------
    def add_text(self, context):
        self.texts.append(context)
        if self.streaming:
            self.is_adding_text = True

    def finish_text(self):
        """流し込みが終わったことにする。"""
        self.is_adding_text = False
        CLOCK.settle()

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
        if getattr(self, "background_raises", False):
            # 本体は `self.app` を読むが `InstantaleApp` にその属性は無い
            # （実機。自分の家で社交を選ぶとここを通る）。
            raise AttributeError("'InstantaleApp' object has no attribute 'app'")
        self.backgrounds.append(("location", str(location_id)))
        return None

    def elapse_days(self, days):
        self.world.days_elapsed += int(days)
        return None

    def save_game(self):
        # セーブに焼かれるのは、そのときの立ち位置と選択肢と旗
        # （`game_variables["buttons"]` / `in_shopping`。実セーブで確認）。
        self.saves += 1
        location = self.player.location
        self.saved_at.append(str(location) if isinstance(location, (str, int))
                             else str(getattr(location, "id", "")))
        self.saved_buttons.append([entry.get("text") for entry in self.buttons])
        self.saved_shopping.append(self.in_shopping)
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


#: setup のたびに作り直すクラス。**包む先は毎回この新しい型**にする。
#: 使い回すと、古い mod インスタンスの包みが内側に積もり、
#: そちらが自分の控え（別の `store`）のまま同じファイルへ書いて結果を汚す。
BASES = {"app": InstantaleApp, "world": World,
         "stay": VacationStartManager, "end": VacationEndManager,
         "rest": VacationRestManager, "item": InventoryItem}


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
    def send_request(cls, manager_name, message, structure, max_tokens=None,
                     timeout=None):
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
        """**同じ対象に何本でも積む。**

        建物はローダの `modfacility` が持つので（TECH.md §5.8）、
        `save_game` / `World.__init__` / `refresh_choice_buttons` /
        `on_button_press` / 背景の2つは、この MOD と関所の両方が包む。
        1本しか覚えないと、後から来たほうが前のを黙って消す。
        """
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
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
    """積まれた順に重ねる（先に宣言したものが内側。ローダの `wrap` と同じ）。"""
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


HUD_CLS = install_fake_hud()
CLOCK = install_fake_kivy()
install_fake_characters()

STATE_DIR = os.path.join(OUT_DIR, "state", "real_estate")
#: 建物の控えはローダが持つ（`modfacility`）。契約の控えとは別のフォルダ。
FACILITY_STATE_DIR = os.path.join(OUT_DIR, "state", modfacility.STATE_DIRNAME)
#: 管理人の控えもローダが持つ（`modnpc`）。
NPC_STATE_DIR = os.path.join(OUT_DIR, "state", modnpc.STATE_DIRNAME)
LOG_PATH = os.path.join(OUT_DIR, "real_estate.log")


def read_log():
    try:
        with io.open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def read_state(world=None):
    """控え。`world` を渡すとその世界のぶんだけ読む（世界ごとに1ファイル）。"""
    for name in (sorted(os.listdir(STATE_DIR)) if os.path.isdir(STATE_DIR) else []):
        if not name.endswith(".json"):
            continue
        if world is not None and name != world + SEP + PLAYER_NAME + ".json":
            continue
        with io.open(os.path.join(STATE_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    return None


def read_npc_state():
    """管理人の控え（ローダの `modnpc` が持つ）。無ければ None。"""
    for name in (sorted(os.listdir(NPC_STATE_DIR))
                 if os.path.isdir(NPC_STATE_DIR) else []):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(NPC_STATE_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    return None


def state_files():
    """控えのファイル名（世界ごとに1つできているか）。"""
    return sorted(n for n in (os.listdir(STATE_DIR) if os.path.isdir(STATE_DIR) else [])
                  if n.endswith(".json"))


def build_world(app_cls, world_cls):
    """街1つ（入口・広場・宿屋・役場）と、隣の街を1つ。"""
    world = world_cls({"world_data": {"name": WORLD_NAME, "days_elapsed": 100},
                       "player_data": {"name": PLAYER_NAME}}, None)
    world_dict = {"world_data": {"name": WORLD_NAME, "days_elapsed": 100},
                  "index": {"facility": 20, "npc": 5, "item": 30}}
    save_data_dict = {"world_data": {"name": WORLD_NAME, "days_elapsed": 100},
                      "player_data": {"name": PLAYER_NAME},
                      "index": {"facility": 20, "npc": 5, "item": 30}}
    home = Area("0", "始まりの泥濘")
    node = Node("0")
    node.entrance_facility = "0"
    home.nodes["0"] = node
    world.areas["0"] = home
    app = app_cls(world, Player(home, None, 100000), world_dict, save_data_dict)
    app.player.id = "player"
    world.characters["player"] = app.player
    # 役場の役人。物件を貸した側なので、家の主にはこの人が立つ。
    clerk = Character(name="泥濘の役人",
                      original_ability_scores=dict.fromkeys(Character.ABILITY_KEYS))
    clerk.id = CLERK_ID
    world.characters[CLERK_ID] = clerk
    # その土地の住人（役場が引けないときのフォールバックの相手）。
    resident = Character(name="泥濘の住人",
                         original_ability_scores=dict.fromkeys(Character.ABILITY_KEYS))
    world.characters["41"] = resident
    home.resident_npcs = ["41"]

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
    office.owner = CLERK_ID

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
    main.VacationStartManager = classes["stay"]
    main.VacationEndManager = classes["end"]
    main.VacationRestManager = classes["rest"]
    main.VacationSocializeManager = VacationSocializeManager
    main.NotImplementedManager = NotImplementedManager

    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)
    if not keep_state:
        for folder in (STATE_DIR, FACILITY_STATE_DIR, NPC_STATE_DIR):
            if os.path.isdir(folder):
                for name in os.listdir(folder):
                    os.remove(os.path.join(folder, name))

    # 2つの関所はゲームの `save_game` に当てるもので、偽の環境には無い。
    # `spawn` は関所が今の世代で立っていなければ建てないので、立っていることにする。
    modfacility.gate_is_live = lambda: True
    modnpc.gate_is_live = lambda: True
    modfacility.registry().clear()
    modnpc.registry().clear()
    for attr in (modfacility.INSTALLED_ATTR, modfacility.SCREEN_ATTR,
                 modfacility._STATE_ATTR, modfacility.STORE_ATTR,
                 modnpc.INSTALLED_ATTR, modnpc.STORE_ATTR):
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
        ("__main__:InstantaleApp.elapse_days", classes["app"], "elapse_days"),
        ("__main__:InstantaleApp.save_game", classes["app"], "save_game"),
        ("__main__:InstantaleApp.close_shopping_window_process", classes["app"],
         "close_shopping_window_process"),
        ("__main__:World.__init__", classes["world"], "__init__"),
        ("__main__:VacationStartManager.__init__", classes["stay"], "__init__"),
        ("__main__:VacationStartManager.execute", classes["stay"], "execute"),
        ("__main__:VacationEndManager.execute", classes["end"], "execute"),
        ("__main__:VacationRestManager.execute", classes["rest"], "execute"),
        ("__main__:InstantaleApp.change_background_image_to_current_location",
         classes["app"], "change_background_image_to_current_location"),
        ("__main__:InstantaleApp.change_background_image_to_inn_room",
         classes["app"], "change_background_image_to_inn_room"),
        ("__main__:InstantaleApp.change_background_image_from_location_id",
         classes["app"], "change_background_image_from_location_id"),
        ("scripts.hud.new_hud:InventoryItem.change_inventory", classes["item"],
         "change_inventory"),
    ))
    app, world, places = build_world(classes["app"], classes["world"])
    app.player.gold = gold
    main.test_app = app
    app.facility_screen()
    return module, ctx, app, places, classes


def rent(app, module, label_prefix="借りる"):
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


def module_state(module):
    """mod が控えている旗の束（`sys` に置かれている）。"""
    store = getattr(sys, "__instantale_real_estate_store__", None)
    return (store or {}).get("state") or {}


def contract_of(module, app):
    data = read_state()
    contracts = (data or {}).get("contracts") or []
    return contracts[0] if contracts else None


def keeper_name_of(module, app):
    """いまの契約に控えてある管理人の名前。"""
    return ((contract_of(module, app) or {}).get("keeper") or {}).get("name")


# ================================================================ 検査
print("[窓口]")
module, ctx, app, places, classes = setup()
check("役場で「家を借りる・買う」が出る", app.has(module.OFFICE_LABEL),
      app.labels())
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
check("借りる・買うが2つ並ぶ",
      sum(app.has(p) for p in ("借りる", "建売を買い取る")) == 2,
      app.labels())
label = app.label_like("借りる")
check("家賃が値札に出る", "{:,}".format(module.RENT_PRICE) in (label or ""), label)
check("宿屋を見ていない世界では年齢から見積もる（31歳＝4ヵ月）",
      durations.game_inn_stay(app)["months"] == 4, durations.game_inn_stay(app)["months"])
stay_len = durations.game_inn_stay(app)["months"] * module.DAYS_PER_MONTH
check("値札の日数は宿泊4回ぶん",
      "{}日".format(stay_len * module.RENT_STAYS) in (label or ""), label)
gold_before = app.player.gold
# 代金を引けなかった回は契約も建物も残さない（控えは引けてから書く）
real_add_gold = module.ui.add_gold
module.ui.add_gold = lambda *args, **kwargs: None
try:
    app.press(label)
    CLOCK.settle()
finally:
    module.ui.add_gold = real_add_gold
check("引けなかった回は契約が控えに残らない", contract_of(module, app) is None,
      contract_of(module, app))
check("引けなかった回は建物も立たない", home_of(app, module, places) is None,
      list(places["node"].facilities))
app.press(module.OFFICE_LABEL)
CLOCK.settle()
label = app.label_like("借りる")
saves_before = app.saves
app.press(label)
CLOCK.settle()
record = contract_of(module, app)
check("契約が控えに1件残る", record is not None and record.get("kind") == "rent",
      record)
check("家賃が引かれた", app.player.gold == gold_before - module.RENT_PRICE,
      app.player.gold)
check("契約の後にゲームの保存が走る（所持金と控えを揃える）", app.saves > saves_before,
      (saves_before, app.saves))
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
check("施設 id はローダの名前空間（ゲームの台帳を進めない）",
      str(getattr(home, "id", "")).startswith(modfacility.PREFIX)
      and app.world_dict["index"]["facility"] == 20,
      (getattr(home, "id", None), app.world_dict["index"]))
app.press(module.OFFICE_LABEL)
CLOCK.settle()
check("契約中の窓口は確認と解約になる",
      app.has(module.STATUS_LABEL) and app.has(module.RELEASE_LABEL), app.labels())
check("契約中は借りる選択肢が出ない", not app.has("借りる"), app.labels())
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
# 主を据えるとゲームが `会話する` を出すが、管理人は一覧に出さない人なので誰も並ばない。
# 大家とは話さない（本人の指定）ので、家では伏せる（`modfacility` の `hide`）。
check("家では「会話する」を出さない", not app.has(TALK_TEXT), app.labels())
check("よその施設の「会話する」には触らない",
      TALK_TEXT in [entry.get("text") for entry in app.go(places["inn"])],
      app.labels())
app.go(home)

# 実機のゲームは、自分で足した施設の中では選択肢を1つも作らない。
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

# 背景。MOD は描かず、頼みもしない（建物へ入る移動でゲームが自分で描く。頼むと2枚になる）。
app.go(places["inn"])
app.backgrounds = []
app.go(home)
app.facility_screen()
app.facility_screen()
check("家に立っても MOD は絵に触らない", app.backgrounds == [], app.backgrounds)

# 本体の id 引きは MOD の施設を引けない（`self.app` で落ちる）ので、名前で引く経路へ回す。
app.backgrounds = []
app.change_background_image_from_location_id(str(home.id))
check("家の id は名前で引く経路へ回る", app.backgrounds == [("current", None)], app.backgrounds)
app.backgrounds = []
app.change_background_image_from_location_id(str(places["inn"].id))
check("よその施設の背景には手を出さない",
      app.backgrounds == [("location", str(places["inn"].id))], app.backgrounds)
app.go(places["inn"])
app.backgrounds = []
app.change_background_image_to_current_location()
check("家の外では素のまま", app.backgrounds == [("current", None)], app.backgrounds)

# 本体が落ちる経路（社交の相手の場所を出そうとしたとき）。握って先へ通す。
app.go(home)
app.background_raises = True
app.backgrounds = []
failed = False
try:
    app.change_background_image_from_location_id(str(places["inn"].id))
except AttributeError:
    failed = True
check("本体の背景の差し替えが落ちても外へ出さない", not failed, "AttributeError が出た")
check("本体の不具合として記録する",
      "the game's own change_background_image_from_location_id raised" in read_log(),
      [l for l in read_log().splitlines() if "background" in l][-3:])
app.background_raises = False
app.go(home)

print("[滞在]")
gold_before = app.player.gold
day_before = app.world.days_elapsed
months_now = durations.game_inn_stay(app)["months"]
day_after = day_before + months_now * module.DAYS_PER_MONTH
app.press(module.STAY_LABEL)
CLOCK.settle()
check("ゲームの宿泊が宿屋と同じ月数で起きた",
      app.stays == [(months_now, module.STAY_QUALITY)], app.stays)
keeper_id = (contract_of(module, app).get("keeper") or {}).get("id")
check("滞在の主は建物の管理人", getattr(home, "owner", None) == keeper_id,
      (getattr(home, "owner", None), keeper_id))
check("役人は借りない（役場の役人が主にならない）",
      getattr(home, "owner", None) != CLERK_ID, getattr(home, "owner", None))
# (a) 宿代は前払いしてある。ゲームが引くと所持金は元に戻る。
check("宿代を先に足す",
      "stay: prepaid {} for the room".format(ROOM_PRICE) in read_log(),
      [l for l in read_log().splitlines() if "prepaid" in l][:3])
check("ゲームが引いて所持金が元に戻る", app.player.gold == gold_before,
      (app.player.gold, gold_before))
check("帳尻の直しは要らない", "corrected" not in read_log(),
      [l for l in read_log().splitlines() if "corrected" in l][:3])
check("日数はゲームのまま進む",
      app.world.days_elapsed == day_after,
      app.world.days_elapsed)
check("滞在の最中は自前のボタンを足さない",
      not app.has(module.STAY_LABEL) and not app.has(module.STORAGE_LABEL),
      app.labels())
check("滞在の選択肢から交流が消える", not app.has("他者と交流"), app.labels())
check("アイテム作成も消える", not app.has("アイテム作成"), app.labels())
check("ほかの活動は残る", app.has("休養をとる") and app.has("宿泊を終える"),
      app.labels())
app.process_choice(classes["rest"](app, months_now, module.STAY_QUALITY),
                   "休養をとる")
CLOCK.settle()
check("活動を1つ終えると滞在が終わる（1泊＝活動1回）", app.stay_ended == 1,
      app.stay_ended)
app.facility_screen()
check("滞在を終えると建物の選択肢に戻る",
      app.has(module.STAY_LABEL) and app.has(module.STORAGE_LABEL), app.labels())
# 主は据えたままでよい（保存のときはローダが名簿から引き上げる。TECH.md §5.7）。
check("滞在の後も主は管理人のまま",
      getattr(home, "owner", None) == keeper_id, getattr(home, "owner", None))

print("[保管庫]")
app.go(home)
app.press(module.STORAGE_LABEL)
CLOCK.settle()
check("2枚並びの窓が開く", len(app.windows) == 1, app.windows)
check("場面名はゲームに無い名前", app.windows[-1][3] == "real_estate_storage",
      app.windows[-1][3])
check("窓はメインスレッド（Clock）から開く",
      app.windows_from_clock == [True], app.windows_from_clock)
# 見出しに管理人の名前は出さない（名乗る場面が無いため。本人の指定）。
check("右の見出しは建物の名前", app.hud.right_header.text == getattr(home, "name", None),
      app.hud.right_header.text)
check("見出しに管理人の名前は出さない",
      app.hud.right_header.text != keeper_name_of(module, app),
      app.hud.right_header.text)
holder = app.windows[-1][1]
check("窓の右に立つのは管理人",
      getattr(holder, "name", None) == keeper_name_of(module, app),
      getattr(holder, "name", None))
check("窓の右は名簿に居るその人", app.world.characters.get(keeper_id) is holder,
      (holder, app.world.characters.get(keeper_id)))
player_grid = InventoryGrid(app.player)
holder_grid = InventoryGrid(holder)
item = app.generate_item_from_dict(sample_item(), "item_19", app.player)
app.player.equipments["weapon"] = item
saves_before = app.saves
widget = classes["item"](item, player_grid)
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
# 取り出す向きも同じ窓で通る（持ち主が管理人になっても往復は変わらない）。
widget.change_inventory(player_grid)
CLOCK.settle()
check("取り出すとプレイヤーの持ち物へ戻る",
      any(v is item for v in app.player.inventory.inventory.values()),
      list(app.player.inventory.inventory))
check("控えからも消える", not (contract_of(module, app) or {}).get("storage"),
      (contract_of(module, app) or {}).get("storage"))
widget.change_inventory(holder_grid)             # 以降の節はまた1点を預けた状態で
CLOCK.settle()
app.close_shopping_window_process()
CLOCK.settle()
check("窓を閉じたら管理人は品を持たない",
      not holder.inventory.inventory, list(holder.inventory.inventory))
check("預けた品は 330 の控えにだけ残る",
      len((contract_of(module, app) or {}).get("storage") or {}) == 1,
      (contract_of(module, app) or {}).get("storage"))

print("[家賃]")
term = contract_of(module, app).get("term")
check("契約の周期は宿泊4回ぶん",
      term == stay_len * module.RENT_STAYS, (term, stay_len))
# 宿屋の1泊ぶん過ごしただけでは家賃は来ない（それでは借りる意味が無い）。
gold_before = app.player.gold
app.elapse_days(stay_len)
CLOCK.settle()
check("滞在1回ぶんでは家賃が来ない", app.player.gold == gold_before, app.player.gold)
due_before = contract_of(module, app).get("due")
gold_before = app.player.gold
saves_before = app.saves
app.elapse_days(term)
CLOCK.settle()
record = contract_of(module, app)
check("期限が来たら家賃を払う", app.player.gold == gold_before - module.RENT_PRICE,
      app.player.gold)
check("期限が1期ぶん延びる", record.get("due") == due_before + term, record.get("due"))
check("家賃を払ったらゲームの保存が走る", app.saves > saves_before, (saves_before, app.saves))
# 引けなかった回は期限を延ばさない
module.ui.add_gold = lambda *args, **kwargs: None
try:
    app.elapse_days(term)
    CLOCK.settle()
finally:
    module.ui.add_gold = real_add_gold
check("家賃を引けなかった回は期限が延びない",
      contract_of(module, app).get("due") == due_before + term,
      contract_of(module, app).get("due"))
app.elapse_days(0)
CLOCK.settle()
check("次の機会に払い、期限が延びる",
      contract_of(module, app).get("due") == due_before + term * 2,
      contract_of(module, app).get("due"))
gold_before = app.player.gold
app.elapse_days(term * 3)
CLOCK.settle()
record = contract_of(module, app)
check("飛んだ期の分はまとめて払う",
      app.player.gold == gold_before - module.RENT_PRICE * 3, app.player.gold)

print("[期限切れ]")
app.go(places["office"])
app.player.gold = 0
app.elapse_days(contract_of(module, app).get("term"))
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

print("[中に居るあいだの期限切れ]")
module, ctx, app, places, classes = setup()
rent(app, module)
inside_home = home_of(app, module, places)
app.go(inside_home)
check("家の中に立てた", app.has(module.STAY_LABEL), app.labels())
app.player.gold = 0
app.elapse_days(contract_of(module, app).get("term"))
CLOCK.settle()
check("中に居るあいだは取り壊さない",
      str(inside_home.id) in places["node"].facilities,
      list(places["node"].facilities))
check("契約は切れて控えに残る",
      (contract_of(module, app) or {}).get("lapsed") is True, contract_of(module, app))
app.buttons = []
app.refresh_choice_buttons(True)
CLOCK.settle()
check("切れた家からも出られる", app.has(module.LEAVE_LABEL), app.labels())
check("切れた家では滞在できない", not app.has(module.STAY_LABEL), app.labels())
check("切れた家では保管庫も開けない", not app.has(module.STORAGE_LABEL), app.labels())
app.press(module.LEAVE_LABEL)
CLOCK.settle()
check("出ると入口へ移る", app.moved and app.moved[-1] == ["0", "0", "0"], app.moved)
app.go(places["entrance"])
check("出た後に建物が消える",
      str(inside_home.id) not in places["node"].facilities,
      list(places["node"].facilities))
check("控えからも契約が消える", contract_of(module, app) is None, read_state())

print("[取り残されたとき]")
module, ctx, app, places, classes = setup()
# 版12 までの期限切れが作っていた形。街から消えた施設に立ち、選択肢が1つも無い。
ghost = Facility(app, places["node"], {"name": "消えた家", "id": "99",
                                       "description": "", "facility_type": "location",
                                       "tier": None, "owner": None,
                                       "connections": [], "config": {}})
app.player.location = ghost
app.buttons = []
app.refresh_choice_buttons(True)
CLOCK.settle()
# 層ごと消えた場所なので、文言はローダの既定（もう「家」ではない）。
EXIT_LABEL = modfacility.DEFAULT_EXIT_LABEL
check("消えた建物からも出口が出る", app.has(EXIT_LABEL), app.labels())
app.press(EXIT_LABEL)
CLOCK.settle()
check("入口へ戻れる", app.moved and app.moved[-1] == ["0", "0", "0"], app.moved)
check("取り残されたことがログに残る",
      "stranded=True" in read_log(), read_log()[-200:])
# 実機で取り残された画面には選択肢が4つ残っていた（どれも街へは戻れない）。
app.player.location = ghost
app.facility_screen()
check("選択肢が残っていても出口を出す", app.has(EXIT_LABEL), app.labels())
app.go(places["office"])
check("よその施設では出口を出さない", not app.has(module.LEAVE_LABEL), app.labels())

print("[流し込みの最中]")
module, ctx, app, places, classes = setup()
rent(app, module)
app.go(places["office"])
app.press(module.OFFICE_LABEL)
CLOCK.settle()
app.streaming = True                       # ここから本文が流れ続ける
app.press(module.RELEASE_LABEL)            # 解約。戻すのは `screen.say` の直後
CLOCK.settle()
check("本文が流れている間は窓口を足さない", not app.has(module.OFFICE_LABEL),
      app.labels())
check("戻す先はゲームの選択肢", EXIT_TEXT in app.labels(), app.labels())
app.finish_text()
check("手が空いたら窓口が戻る", app.has(module.OFFICE_LABEL), app.labels())
check("窓口は1つだけ",
      len([t for t in app.labels() if t == module.OFFICE_LABEL]) == 1, app.labels())
app.facility_screen()
CLOCK.settle()
check("見張りは残らない", not module_state(module).get("retry"),
      module_state(module).get("retry"))

print("[世界ごとに分ける]")
OTHER_WORLD = "もう一つの世界"
module, ctx, app, places, classes = setup()
rent(app, module)
first = home_of(app, module, places)
check("1つ目の世界に建った", first is not None, list(places["node"].facilities))
check("控えは 世界×主人公 の名前のファイル", state_files() == [STATE_FILE],
      state_files())


def other_town():
    """2つ目の世界の街（入口と区画だけ）。"""
    return {"0": {"name": "別の泥濘",
                  "nodes": {"0": {"entrance_facility": "0",
                                  "facilities": {
                                      "0": {"name": "別の門", "id": "0",
                                            "description": "",
                                            "facility_type": "entrance",
                                            "tier": None, "owner": None,
                                            "connections": ["1"], "config": {}},
                                      "1": {"name": "別の区画", "id": "1",
                                            "description": "",
                                            "facility_type": "ward",
                                            "tier": None, "owner": None,
                                            "connections": ["0"], "config": {}}}}}}}


# 別の世界をロードする。世界の鍵はセーブの `world_data.name`（`state.world_key`）。
save_b = {"world_data": {"name": OTHER_WORLD}, "areas": other_town(),
          "player_data": {"name": PLAYER_NAME, "location": "1", "current_area": "0"},
          "game_variables": {"buttons": []}}
app.world_dict = {"world_data": {"name": OTHER_WORLD},
                  "index": {"facility": 40, "npc": 5, "item": 30}}
app.save_data_dict = save_b
world_b = classes["world"](save_b, app)
app.world = world_b
area_b = world_b.areas["0"]
node_b = area_b.nodes["0"]
app.player.current_area = area_b
app.go(node_b.facilities["1"])
check("別の世界には建物が建たない",
      str(getattr(first, "id", "")) not in node_b.facilities,
      list(node_b.facilities))
check("別の世界では契約が無い", contract_of(module, app) is None
      or read_state(OTHER_WORLD) is None, read_state(OTHER_WORLD))
check("1つ目の世界の控えはそのまま",
      len((read_state(WORLD_NAME) or {}).get("contracts") or []) == 1,
      read_state(WORLD_NAME))

# 2つ目の世界でも借りられる。控えは別のファイル。
office_b = Facility(app, node_b, {"name": "別の徴収所", "id": "2", "description": "",
                                  "facility_type": OFFICE_TYPE, "tier": None,
                                  "owner": None, "connections": ["1"],
                                  "config": {}})
node_b.facilities["2"] = office_b
app.go(office_b)
rent(app, module)
check("控えが世界ごとに1つずつできる",
      state_files() == sorted([STATE_FILE, OTHER_WORLD + SEP + PLAYER_NAME + ".json"]),
      state_files())
check("2つ目の世界の契約はそちらの控えに入る",
      len((read_state(OTHER_WORLD) or {}).get("contracts") or []) == 1,
      read_state(OTHER_WORLD))
check("1つ目の世界の控えは増えない",
      len((read_state(WORLD_NAME) or {}).get("contracts") or []) == 1,
      read_state(WORLD_NAME))
second = node_b.facilities.get(
    str((read_state(OTHER_WORLD)["contracts"][0]).get("facility")))
check("2つ目の世界にも建った", second is not None, list(node_b.facilities))
check("2つ目の世界でも台帳は進まない",
      app.world_dict["index"]["facility"] == 40, app.world_dict["index"])

# 1つ目の世界へ戻す。建物は建ち直り、2つ目の建物は持ち込まれない。
app.world_dict = {"world_data": {"name": WORLD_NAME},
                  "index": {"facility": 30, "npc": 5, "item": 30}}
back_world = classes["world"]({"world_data": {"name": WORLD_NAME},
                               "player_data": {"name": PLAYER_NAME}}, app)
app.world = back_world
back_world.areas["0"] = places["area"]
app.player.current_area = places["area"]
app.go(places["hub"])
check("戻ると1つ目の世界の建物が建ち直る",
      str(getattr(first, "id", "")) in places["node"].facilities,
      list(places["node"].facilities))
check("2つ目の世界の建物は持ち込まれない",
      str(getattr(second, "id", "")) not in node_b.facilities
      or node_b.facilities[str(second.id)] is not places["node"].facilities.get(
          str(second.id)),
      (list(node_b.facilities), list(places["node"].facilities)))
check("1つ目の街に建物が増えていない",
      len([f for f in places["node"].facilities
           if str(f).startswith(modfacility.PREFIX)]) == 1,
      list(places["node"].facilities))

print("[宿屋を見る前から他 MOD の設定に合わせる]")
# ローダは mod を `instantale_mod_<フォルダ名>` で `sys.modules` に載せ、
# 選んだ設定値を入口のグローバルへ書き込む。
# 330 はそこにある `compute_stay` をそのまま呼ぶ（式は写さない）。
PLAN = {"length": "1週間"}


def fake_length(app):
    """宿泊の長さを変える MOD が窓口に置く関数の代わり。設定と年齢は向こうが見る。"""
    if PLAN["length"] == "1週間":
        return {"unit": "week", "count": 1, "months": 1, "days": 7,
                "length": "1週間"}
    if PLAN["length"] == "4ヵ月":
        return {"unit": "month", "count": 4, "months": 4, "days": None,
                "length": "4ヵ月"}
    return None                                  # 「デフォルト」


# 変える MOD はローダの窓口に答えを置く（330 はその MOD の名前を知らない）。
durations.declare(durations.INN_STAY, fake_length, owner="fake_315")
try:
    module, ctx, app, places, classes = setup()
    app.press(module.OFFICE_LABEL)
    CLOCK.settle()
    check("値札は借りた日数（7日）の4回ぶん",
          "{}日".format(7 * module.RENT_STAYS) in (app.label_like("借りる") or ""),
          app.label_like("借りる"))
    check("誰が決めた長さかがログに残る",
          "stay length: 1 month(s)/7 day(s) by fake_315" in read_log(),
          read_log()[-300:])
    app.press(module.CANCEL_LABEL)
    CLOCK.settle()
    rent(app, module)
    borrowed_home = home_of(app, module, places)
    app.go(borrowed_home)
    app.press(module.STAY_LABEL)
    CLOCK.settle()
    check("自分の家の滞在も借りた月数で起きる", app.stays and app.stays[-1][0] == 1,
          app.stays)
    app.process_choice(classes["rest"](app, 1, module.STAY_QUALITY), "休養をとる")
    CLOCK.settle()

    # 月単位の設定なら、その月数をそのまま使う（日数は月数×30）。
    PLAN["length"] = "4ヵ月"
    module, ctx, app, places, classes = setup()
    app.press(module.OFFICE_LABEL)
    CLOCK.settle()
    check("月単位の設定はその月数で数える",
          "{}日".format(4 * 30 * module.RENT_STAYS) in (app.label_like("借りる") or ""),
          app.label_like("借りる"))
    app.press(module.CANCEL_LABEL)
    CLOCK.settle()

    # 「デフォルト」なら借りない（年齢からの見積もりに戻る）。
    PLAN["length"] = "デフォルト"
    module, ctx, app, places, classes = setup()
    app.press(module.OFFICE_LABEL)
    CLOCK.settle()
    check("「デフォルト」なら年齢の見積もりに戻る",
          "{}日".format(durations.game_inn_stay(app)["months"] * 30 * module.RENT_STAYS)
          in (app.label_like("借りる") or ""), app.label_like("借りる"))
    app.press(module.CANCEL_LABEL)
    CLOCK.settle()
finally:
    durations.forget("fake_315")

# その MOD が入っていなければ、今までどおり年齢から見積もる。
module, ctx, app, places, classes = setup()
app.press(module.OFFICE_LABEL)
CLOCK.settle()
check("入れていなければ年齢からの見積もり",
      "{}日".format(durations.game_inn_stay(app)["months"] * 30 * module.RENT_STAYS)
      in (app.label_like("借りる") or ""), app.label_like("借りる"))
app.press(module.CANCEL_LABEL)
CLOCK.settle()

print("[宿泊の長さはローダの窓口が決める]")
# 版40 から、月数も日数も窓口（`durations.inn_stay`）の答えをそのまま使う。
# 宿屋の部屋のボタンからの観測は控えるだけで、答えにはしない。
# 観測を答えにしていた版39 までは、設定を変えても控えが古いままだった
# （`3ヵ月` に戻した後も1ヵ月で滞在していた。実機。VERIFICATION.md §3.62 #11）。
module, ctx, app, places, classes = setup()
window_months = durations.game_inn_stay(app)["months"]
guess = window_months * module.DAYS_PER_MONTH * module.RENT_STAYS
app.press(module.OFFICE_LABEL)
CLOCK.settle()
check("値札は窓口の長さの4回ぶん",
      "{}日".format(guess) in (app.label_like("借りる") or ""),
      app.label_like("借りる"))
app.press(module.CANCEL_LABEL)
CLOCK.settle()


def room_menu(months):
    """宿屋で「宿泊する」を押したときにゲームが組む部屋の選択肢。

    spec は `VacationStartManager(app, months, quality)`。
    ここに載っている月数は**観測**で、控えはするが答えにはしない（版40）。
    """
    app.buttons = [
        {"text": "犬小屋(0G)", "spec": PhaseSpec("VacationStartManager",
                                               [months, "kennel"])},
        {"text": "個室(100G)", "spec": PhaseSpec("VacationStartManager",
                                               [months, "private_room"])},
        {"text": EXIT_TEXT, "spec": PhaseSpec("MovePhaseManager", ["0", "1", "0"])},
    ]
    app.refresh_choice_buttons(reset_page=True)
    CLOCK.settle()


# 宿屋で部屋の選択肢を開くと控えるが、答えは窓口のまま。
app.go(places["inn"])
room_menu(2)
check("宿屋の部屋のボタンの月数は控える",
      (read_state() or {}).get("stay_months") == 2, read_state())
check("控えたことがログに残る",
      "stay length: the inn's rooms are booked by 2 month(s)" in read_log(),
      read_log()[-300:])

app.go(places["office"])
rent(app, module)
signed = contract_of(module, app)
check("契約の周期は窓口の長さの4回ぶん（部屋のボタンでは動かない）",
      (signed or {}).get("term") == guess, signed)

home_now = home_of(app, module, places)
app.go(home_now)
app.press(module.STAY_LABEL)
CLOCK.settle()
check("自分の家の滞在も窓口と同じ月数で起きる",
      app.stays and app.stays[-1][0] == window_months, app.stays)
check("観測と窓口が食い違ったら1度だけ残す",
      "but the window says {}".format(window_months) in read_log(),
      read_log()[-400:])
check("誰が決めた長さかもログに残る",
      "stay length: {} month(s) by the game's own rule".format(window_months)
      in read_log(), read_log()[-400:])
app.process_choice(classes["rest"](app, window_months, module.STAY_QUALITY),
                   "休養をとる")
CLOCK.settle()

print("[設定を変えたらその場で効く]")
# `1ヵ月` と `1週間` はどちらもゲームに渡る月数が 1 なので、
# 月数を見張る形（版39 の `stay_days_for`）では見分けられなかった。
# 窓口は日数も持っているので、切り替えた時点で効く。
module, ctx, app, places, classes = setup()
app.go(places["inn"])
room_menu(1)                                 # `1ヵ月` の頃の観測を控えに残す
app.process_choice(classes["stay"](app, 1, "private_room"), "個室(100G)")
CLOCK.settle()
check("月単位のうちは30日で数える",
      (read_state() or {}).get("stay_days") == 30, read_state())
PLAN["length"] = "1週間"
durations.declare(durations.INN_STAY, fake_length, owner="fake_315")
try:
    app.go(places["office"])
    rent(app, module)
    check("週単位へ変えたら契約の周期は7日の4回ぶん",
          (contract_of(module, app) or {}).get("term") == 7 * module.RENT_STAYS,
          contract_of(module, app))
    short_home = home_of(app, module, places)
    app.go(short_home)
    app.press(module.STAY_LABEL)
    CLOCK.settle()
    check("自分の家の滞在も週単位の月数（1）で起こす",
          app.stays and app.stays[-1][0] == 1, app.stays)
    app.process_choice(classes["rest"](app, 1, module.STAY_QUALITY), "休養をとる")
    CLOCK.settle()
finally:
    durations.forget("fake_315")
    PLAN["length"] = "デフォルト"

print("[自分の家の滞在で月数を学び直さない]")
# ゲームは滞在の最中の画面に `まだ宿泊する`（`VacationStartManager`）を並べる。
# そこに載っているのは**いま走っている滞在の月数**で、宿屋の値ではない。
module, ctx, app, places, classes = setup()
app.go(places["inn"])
room_menu(1)
check("宿屋で月数を覚えた", (read_state() or {}).get("stay_months") == 1, read_state())
app.go(places["office"])
rent(app, module)
loop_home = home_of(app, module, places)
app.go(loop_home)
app.press(module.STAY_LABEL)
CLOCK.settle()
app.buttons = app.buttons + [
    {"text": "まだ宿泊する", "spec": PhaseSpec("VacationStartManager",
                                          [3, "private_room"])}]
app.refresh_choice_buttons(reset_page=True)
CLOCK.settle()
check("家の中の「まだ宿泊する」からは覚えない",
      (read_state() or {}).get("stay_months") == 1, read_state())
app.process_choice(classes["rest"](app, 1, module.STAY_QUALITY), "休養をとる")
CLOCK.settle()

print("[MOD が建てた宿では数えない]")
# `331_facility_investment` の「無料で泊まる」のような、MOD の建物の中の宿泊。
module, ctx, app, places, classes = setup()
app.go(places["inn"])
room_menu(2)
app.process_choice(classes["stay"](app, 2, "private_room"), "個室(100G)")
CLOCK.settle()
before_days = (read_state() or {}).get("stay_days")
app.go(places["office"])                     # 宿泊の活動の画面を畳む
rent(app, module)
mod_inn = home_of(app, module, places)
app.go(mod_inn)                              # MOD が建てた建物の中
app.short_stay_days = 90
app.process_choice(classes["stay"](app, 3, "private_room"), "無料で泊まる")
CLOCK.settle()
check("MOD の建物の中の宿泊は数えない",
      (read_state() or {}).get("stay_days") == before_days, read_state())
app.short_stay_days = None

print("[ゲームの採番で建てていた頃の控え]")
# 版19 までの控え。建物の id がゲームの台帳から採った整数で、もう建て直せない。
module, ctx, app, places, classes = setup()
os.makedirs(STATE_DIR, exist_ok=True)
legacy = {"contracts": [{"area": "0", "area_name": "始まりの泥濘", "node": "0",
                         "hub": "0", "facility": "77", "kind": "rent",
                         "name": "借りている家", "rent": 500, "term": 7,
                         "since": 100, "due": 104, "storage": {},
                         "notified": None, "at": "2026-09-11T00:00:00"}],
          "seized": {}}
with io.open(os.path.join(STATE_DIR, STATE_FILE), "w",
             encoding="utf-8") as fh:
    fh.write(json.dumps(legacy, ensure_ascii=False))
module, ctx, app, places, classes = setup(keep_state=True)
check("整数の id を指す契約は落とす", contract_of(module, app) is None,
      read_state())
check("落としたことがログに残る",
      "from before the loader owned the buildings" in read_log(),
      read_log()[-200:])
check("その家は建たない", "77" not in places["node"].facilities,
      list(places["node"].facilities))

print("[消えた家の id がセーブに残らない]")
module, ctx, app, places, classes = setup()
rent(app, module)
doomed = home_of(app, module, places)
app.go(doomed)
app.save_game()
check("生きた契約なら家の中のまま保存する", app.saved_at[-1] == str(doomed.id),
      app.saved_at)
app.player.gold = 0
app.elapse_days(contract_of(module, app).get("term"))
CLOCK.settle()
app.save_game()
check("建て直されない家では入口の id で保存する", app.saved_at[-1] == "0", app.saved_at)
check("保存の後は元の場所に戻す", app.player.location is doomed, app.player.location)
app.player.location = Facility(app, places["node"], {
    "name": "消えた家", "id": "98", "description": "", "facility_type": "location",
    "tier": None, "owner": None, "connections": [], "config": {}})
app.save_game()
check("街に無い施設に立っていても入口の id で保存する", app.saved_at[-1] == "0",
      app.saved_at)
# ロードは選択肢を組み直さない。立ち位置だけ直しても空なら動けない。
check("入口の選択肢も一緒に焼く", app.saved_buttons[-1] == ["中央居住区"],
      app.saved_buttons[-1])
check("保存の後は選択肢も元に戻す",
      [e.get("text") for e in app.buttons] != app.saved_buttons[-1],
      app.labels())

print("[保管庫を開いたまま保存]")
module, ctx, app, places, classes = setup()
rent(app, module)
open_home = home_of(app, module, places)
app.go(open_home)
app.press(module.STORAGE_LABEL)
CLOCK.settle()
check("窓が開いている", app.windows, app.windows)
app.in_shopping = True                     # 売買の窓を借りているあいだの旗
app.save_game()
check("売買中の旗は下ろして保存する", app.saved_shopping[-1] is False,
      app.saved_shopping)
check("保存の後は旗を戻す", app.in_shopping is True, app.in_shopping)

print("[消えた家に立ったままのセーブを読む]")
module, ctx, app, places, classes = setup()
# 実機はここで落ちた。ゲームは引けなかった施設に
# 'facilityが見つからない' という文字列を入れ、その後 .name を読む。
town = {"0": {"name": "始まりの泥濘",
              "nodes": {"0": {"entrance_facility": "0",
                              "facilities": {
                                  "0": {"name": "泥濘の門", "id": "0",
                                        "description": "", "facility_type": "entrance",
                                        "tier": None, "owner": None,
                                        "connections": ["1"], "config": {}},
                                  "1": {"name": "中央居住区", "id": "1",
                                        "description": "", "facility_type": "ward",
                                        "tier": None, "owner": None,
                                        "connections": ["0"], "config": {}}}}}}}
save_data = {"world_data": {"name": WORLD_NAME}, "areas": town,
             "player_data": {"name": PLAYER_NAME, "location": "313", "current_area": "0"},
             "game_variables": {"buttons": []}}
app.world = classes["world"](save_data, app)
check("消えた施設に立っていたら入口へ直す",
      save_data["player_data"]["location"] == "0", save_data["player_data"])
check("直したことがログに残る", "not in the town" in read_log(), read_log()[-300:])
check("入口の選択肢も入れる",
      [b.get("text") for b in save_data["game_variables"]["buttons"]]
      == ["中央居住区"], save_data["game_variables"]["buttons"])
check("選択肢はセーブと同じ形（`cls_name` と `args`）",
      save_data["game_variables"]["buttons"][0]["spec"]
      == {"cls_name": "MovePhaseManager", "args": ["0", "1", "0"]},
      save_data["game_variables"]["buttons"][0])
save_data = {"world_data": {"name": WORLD_NAME}, "areas": town,
             "player_data": {"name": PLAYER_NAME, "location": "1", "current_area": "0"},
             "game_variables": {"buttons": []}}
app.world = classes["world"](save_data, app)
check("街にある施設はそのまま", save_data["player_data"]["location"] == "1",
      save_data["player_data"])
check("そのままのときは選択肢にも触らない",
      save_data["game_variables"]["buttons"] == [],
      save_data["game_variables"])
# 契約が生きていると、当て直しで建物が入口に繋がる。
# その道はセーブに無い施設への道なので、焼く選択肢には入れない。
module, ctx, app, places, classes = setup()
rent(app, module)
save_data = {"world_data": {"name": WORLD_NAME}, "areas": town,
             "player_data": {"name": PLAYER_NAME, "location": "313", "current_area": "0"},
             "game_variables": {"buttons": []}}
app.world = classes["world"](save_data, app)
labels = [b.get("text") for b in save_data["game_variables"]["buttons"]]
check("建て直した建物は選択肢に焼かない",
      labels == ["中央居住区"], labels)

print("[ロード]")
module, ctx, app, places, classes = setup()
rent(app, module, "建売を買い取る")
record = contract_of(module, app)
check("買い切りには期限が無い",
      record is not None and record.get("kind") == "owned" and record.get("due") is None,
      record)
built_id = str(record.get("facility"))
# ロード：世界を作り直し、同じ app に据える（`World.__init__` のフックが走る）。
fresh = classes["world"]({"world_data": {"name": WORLD_NAME},
                          "player_data": {"name": PLAYER_NAME}}, app)
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


def module_keeper_name(record):
    """控えに残っている管理人の名前（実体はまだ組まない）。"""
    return ((record or {}).get("keeper") or {}).get("name")


def reload_world(app, classes, module):
    """世界を作り直して同じ app に据える（`World.__init__` のフックが走る）。

    実機のロードと同じ順（ローダが建て直し、そのあと画面が組まれる）を通す。
    返すのは新しい世界と、その街のノード。
    """
    fresh = classes["world"]({"world_data": {"name": WORLD_NAME},
                              "player_data": {"name": PLAYER_NAME}}, app)
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
    return fresh, node


print("[中に居るまま切れて、出ずに終了したセーブを読む]")
module, ctx, app, places, classes = setup()
rent(app, module)
app.go(home_of(app, module, places))
app.player.gold = 0
app.elapse_days(contract_of(module, app).get("term"))
CLOCK.settle()
lapsed_id = str((contract_of(module, app) or {}).get("facility"))
check("契約は切れて控えに残る",
      (contract_of(module, app) or {}).get("lapsed") is True, contract_of(module, app))


def facility_state_ids():
    """ローダの建物の控え（`state\\modfacility`）にあるこの MOD の建物 id。"""
    found = []
    for name in (sorted(os.listdir(FACILITY_STATE_DIR))
                 if os.path.isdir(FACILITY_STATE_DIR) else []):
        if not name.endswith(".json"):
            continue
        with io.open(os.path.join(FACILITY_STATE_DIR, name), encoding="utf-8") as fh:
            found.extend(((json.load(fh) or {}).get(module.OWNER) or {}).keys())
    return found


check("建物はローダの控えに居る", lapsed_id in facility_state_ids(), facility_state_ids())
# 出ずに終了した形（`pending_demolish` はメモリだけ）。ロードで片付くこと。
fresh, node = reload_world(app, classes, module)
CLOCK.settle()
check("切れた契約はロードで控えから消える",
      not (read_state() or {}).get("contracts"), read_state())
check("切れた建物は建て直さない", lapsed_id not in node.facilities, list(node.facilities))
check("ローダの建物の控えからも消える",
      lapsed_id not in facility_state_ids(), facility_state_ids())
app.press(module.OFFICE_LABEL)
CLOCK.settle()
check("役場は借りる側に戻る",
      any(label.startswith("借りる") for label in app.labels()), app.labels())
check("エラーなし", not ctx.errors, ctx.errors)

print("[管理人]")
# 建物ごとに管理人（賃貸なら大家）を1人立て、その人を建物の主にする。
# 版31 までは滞在のあいだだけ役場の役人を借りていた（VERIFICATION.md §3.62）。
ANSWER = {"name": "セルマ", "category": "old woman",
          "speech_style": "短く言い切る",
          "personality": "この通りに長く住んでいる",
          "profile": "家の鍵を預かっている。住人が留守のあいだも建物を見ている。"}
FakeLLM.load([dict(ANSWER)])
try:
    module, ctx, app, places, classes = setup()
    rent(app, module)
    record = contract_of(module, app)
    keeper = (record or {}).get("keeper") or {}
    kept_home = home_of(app, module, places)
    check("契約すると管理人が控えに残る", bool(keeper.get("id")), record)
    check("id は土地ごとに1人（`keeper-<土地 id>`）",
          keeper.get("id") == "mod:{}:keeper-0".format(module.OWNER),
          keeper.get("id"))
    check("生成 AI に聞くのは契約の1回だけ", len(FakeLLM.calls) == 1, FakeLLM.calls)
    check("答えの名前で立つ",
          keeper.get("name") == ANSWER["name"] and keeper.get("source") == "llm",
          keeper)
    check("賃貸の立場は大家", keeper.get("role") == "大家", keeper.get("role"))
    check("頼み文に立場と住人の名が入る",
          "大家" in FakeLLM.calls[0]["text"]
          and PLAYER_NAME in FakeLLM.calls[0]["text"],
          FakeLLM.calls[0]["text"][:200])
    check("頼み文は専用の名前で残る（他 MOD と混ざらない）",
          FakeLLM.calls[0]["manager"] == module.KEEPER_MANAGER,
          FakeLLM.calls[0]["manager"])
    # **世界には登録しない**（保管庫の持ち主と同じ扱い）。
    # 素のゲームの名簿は「居る人は必ずどこかの施設に立っている」形なので、
    # 居場所を持たない人物をそこへ足さない。
    check("契約しただけでは名簿に載らない",
          keeper.get("id") not in app.world.characters, sorted(app.world.characters))
    check("建物の名簿にも載らない",
          keeper.get("id") not in getattr(kept_home, "characters", []),
          getattr(kept_home, "characters", None))
    check("契約しただけでは主にもならない",
          getattr(kept_home, "owner", None) != keeper.get("id"),
          getattr(kept_home, "owner", None))
    # ロードし直しても同じ人（控えに持っているので聞き直さない）。
    calls_before = len(FakeLLM.calls)
    world2, node2 = reload_world(app, classes, module)
    CLOCK.settle()
    record = contract_of(module, app)
    # ロード後の建物は**新しい世界のノード**に建つ（古い方の実体は掴まない）。
    rebuilt = node2.facilities.get(str(record.get("facility")))
    same = module_keeper_name(record)
    check("ロードの後も控えの名前は同じ", same == ANSWER["name"], same)
    check("ロードでは生成 AI を呼び直さない", len(FakeLLM.calls) == calls_before,
          FakeLLM.calls)
    check("ロードしても名簿には載っていない",
          keeper.get("id") not in world2.characters, sorted(world2.characters))

    # 滞在の**あいだだけ**名簿に載り、主になる（役人へ落ちない）。
    app.go(rebuilt)
    app.press(module.STAY_LABEL)
    CLOCK.settle()
    check("滞在はその人を主として起きる", app.stays, app.stays)
    check("滞在の最中だけ主は管理人",
          getattr(rebuilt, "owner", None) == keeper.get("id"),
          getattr(rebuilt, "owner", None))
    check("滞在の最中だけ名簿にも載る",
          keeper.get("id") in world2.characters, sorted(world2.characters))
    person = world2.characters.get(keeper.get("id"))
    check("答えの人柄と話し方がそのまま入る",
          getattr(person, "personality", None) == ANSWER["personality"]
          and getattr(person, "speech_style", None) == ANSWER["speech_style"],
          (getattr(person, "personality", None), getattr(person, "speech_style", None)))
    check("立場が job に入る", getattr(person, "job", None) == "大家",
          getattr(person, "job", None))
    check("詳細生成が掛け算に使う値が埋まっている",
          isinstance(getattr(person, "original_ability_scores", None), dict),
          getattr(person, "original_ability_scores", None))
    # 素データの33項目は埋める（ゲームは主の記録に `append` する。実機で落ちた）。
    check("記録の欄が埋まっている（None に append しない）",
          isinstance(getattr(person, "current_log", None), list)
          and isinstance(getattr(person, "memory", None), dict),
          (getattr(person, "current_log", None), getattr(person, "memory", None)))
    check("どこにも立たせない（会話の一覧に出ない）",
          not hasattr(getattr(person, "location", None), "facility_type")
          and getattr(person, "current_location", None) is None,
          (getattr(person, "location", None),
           getattr(person, "current_location", None)))
    check("役人を借りた WARN は出ない", "has no keeper" not in read_log(),
          [l for l in read_log().splitlines() if "keeper" in l][:4])
    app.process_choice(classes["rest"](app, app.stays[-1][0], module.STAY_QUALITY),
                       "休養をとる")
    CLOCK.settle()

    # 名前が世界の誰かと重なっていたら、次に据えるときに空いている名前へ寄せる
    # （ゲームは立ち絵のフォルダと LLM の列挙を名前で引く。VERIFICATION.md §3.68）。
    world2.characters["777"] = types.SimpleNamespace(name=ANSWER["name"])
    app.go(rebuilt)
    app.press(module.STAY_LABEL)
    CLOCK.settle()
    renamed = module_keeper_name(contract_of(module, app))
    check("同名の人物が居たら管理人の名を寄せる",
          bool(renamed) and renamed != ANSWER["name"], renamed)
    check("実体の名も変わる",
          getattr(world2.characters.get(keeper.get("id")), "name", None) == renamed,
          getattr(world2.characters.get(keeper.get("id")), "name", None))
    check("控えの素データにも書く（建て直しで戻らない）",
          (((contract_of(module, app) or {}).get("keeper") or {}).get("fields")
           or {}).get("name") == renamed,
          ((contract_of(module, app) or {}).get("keeper") or {}).get("fields"))
    check("素の人物のほうは変えない",
          world2.characters["777"].name == ANSWER["name"],
          world2.characters["777"].name)
    check("寄せたことがログに残る", "was renamed" in read_log(),
          [l for l in read_log().splitlines() if "renamed" in l][:3])
    app.process_choice(classes["rest"](app, app.stays[-1][0], module.STAY_QUALITY),
                       "休養をとる")
    CLOCK.settle()

    # 解約すると `modnpc` から降り、記録からも消える。
    app.go(node2.facilities["3"])
    app.press(module.OFFICE_LABEL)
    CLOCK.settle()
    app.press(module.RELEASE_LABEL)
    CLOCK.settle()
    check("解約すると控えから契約が消える", contract_of(module, app) is None,
          read_state())
    check("管理人も名簿から降りる", keeper.get("id") not in world2.characters,
          sorted(world2.characters))
    check("登録簿からも消える", not modnpc.layers(keeper.get("id")),
          modnpc.layers(keeper.get("id")))
    check("降ろしたことがログに残る", "is no longer the keeper" in read_log(),
          [l for l in read_log().splitlines() if "keeper" in l][-3:])
    check("控えにも残らない（次の周回で増えない）",
          keeper.get("id") not in json.dumps(read_npc_state() or {},
                                             ensure_ascii=False),
          read_npc_state())

    # 建売の管理人は「管理人」。同じ世界では名前も重ならない。
    FakeLLM.answers = [dict(ANSWER, name="ハルカ")]
    app.press(module.OFFICE_LABEL)
    CLOCK.settle()
    app.press(app.label_like("建売を買い取る"))
    CLOCK.settle()
    bought = contract_of(module, app) or {}
    check("建売の立場は管理人", (bought.get("keeper") or {}).get("role") == "管理人",
          bought.get("keeper"))
    # 立場は素性の文に持たせる（滞在の描写を書く AI はここを読む）。
    check("買った家の管理人は雇い主に仕える形で書かれる",
          "雇い主" in ((bought.get("keeper") or {}).get("fields") or {})
          .get("profile", ""),
          ((bought.get("keeper") or {}).get("fields") or {}).get("profile"))

    # 答えが読めなければ表の12人へ落ちる（名前が無いまま登録しない）。
    FakeLLM.answers = [{"name": "", "category": "", "speech_style": "",
                        "personality": "", "profile": ""}]
    module, ctx, app, places, classes = setup()
    rent(app, module)
    fallen = (contract_of(module, app) or {}).get("keeper") or {}
    check("読めない答えは表の人へ落ちる", fallen.get("source") == "table", fallen)
    check("表の人にも名前がある",
          module.landlord.keeper_by_name(fallen.get("name")) is not None,
          fallen.get("name"))
    check("落ちたことがログに残る", "using the table" in read_log(),
          [l for l in read_log().splitlines() if "keeper" in l][:3])

    # 生成が落ちても契約は通る。
    FakeLLM.answers = [RuntimeError("生成に失敗")]
    module, ctx, app, places, classes = setup()
    rent(app, module)
    crashed = (contract_of(module, app) or {}).get("keeper") or {}
    check("生成が落ちても管理人は立つ", crashed.get("source") == "table"
          and bool(crashed.get("name")), crashed)
    check("控えに名前まで入る（滞在のときにこの人が主になる）",
          bool((crashed.get("fields") or {}).get("name")), crashed)
finally:
    FakeLLM.unload()

# 生成 AI が載っていない版。構造が作れないので、聞かずに表から選ぶ。
module, ctx, app, places, classes = setup()
rent(app, module)
plain = (contract_of(module, app) or {}).get("keeper") or {}
check("生成 AI が無い版でも管理人は立つ", plain.get("source") == "table"
      and bool(plain.get("name")), plain)
check("聞けないことがログに残る", "cannot build the structure" in read_log(),
      [l for l in read_log().splitlines() if "keeper" in l][:3])

# 設定で表から選ぶ。生成 AI が居ても聞かない。
FakeLLM.load([dict(ANSWER)])
try:
    module, ctx, app, places, classes = setup(
        configure=lambda m: setattr(m, "KEEPER_SOURCE", "table"))
    rent(app, module)
    picked = (contract_of(module, app) or {}).get("keeper") or {}
    check("table なら聞かない", not FakeLLM.calls, FakeLLM.calls)
    check("table でも管理人は立つ", picked.get("source") == "table"
          and module.landlord.keeper_by_name(picked.get("name")) is not None, picked)
    # 2軒目は別の名前（同じ世界で名前を重ねない）。
    app.player.current_area = places["away"]
    app.go(places["away_office"])
    rent(app, module)
    both = [(c.get("keeper") or {}).get("name")
            for c in (read_state() or {}).get("contracts") or []]
    check("同じ世界で名前が重ならない", len(both) == 2 and both[0] != both[1], both)
finally:
    FakeLLM.unload()

print("[管理人が居ない契約]")
# 版31 までの控え（管理人の欄が無い）。滞在のときに1人決めて控えへ書く
# （決めるのが契約のときだけだと、前からある契約は役人を借り続ける。実機で踏んだ）。
module, ctx, app, places, classes = setup()
rent(app, module)
legacy_contract = dict(contract_of(module, app))
legacy_contract.pop("keeper", None)
os.makedirs(STATE_DIR, exist_ok=True)
with io.open(os.path.join(STATE_DIR, STATE_FILE), "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"contracts": [legacy_contract], "seized": {}},
                        ensure_ascii=False))
module, ctx, app, places, classes = setup(keep_state=True)
old_home = home_of(app, module, places)
check("管理人が居なくても建物は建つ", old_home is not None,
      list(places["node"].facilities))
app.go(old_home)
app.press(module.STAY_LABEL)
CLOCK.settle()
late = (contract_of(module, app) or {}).get("keeper") or {}
check("滞在のときに管理人が決まる", bool(late.get("name")), late)
check("決めたことがログに残る",
      "was signed before the house had a keeper" in read_log(),
      [l for l in read_log().splitlines() if "keeper" in l][:3])
check("控えにも書き足す（次の滞在では聞き直さない）",
      ((read_state() or {}).get("contracts") or [{}])[0].get("keeper", {}).get("name")
      == late.get("name"),
      (read_state() or {}).get("contracts"))
check("役人は借りない", getattr(old_home, "owner", None) != CLERK_ID,
      getattr(old_home, "owner", None))
check("役人を借りた WARN も出ない", "has no keeper; borrowing" not in read_log(),
      [l for l in read_log().splitlines() if "borrowing" in l][:3])
app.process_choice(classes["rest"](app, app.stays[-1][0], module.STAY_QUALITY),
                   "休養をとる")
CLOCK.settle()
check("決まった管理人が主のまま残る",
      getattr(old_home, "owner", "?") == (late.get("id")),
      getattr(old_home, "owner", "?"))
app.facility_screen()
app.press(module.STORAGE_LABEL)
CLOCK.settle()
check("保管庫も開く", len(app.windows) == 1, app.windows)
check("見出しは建物の名前のまま",
      app.hud.right_header.text == getattr(old_home, "name", None)
      and app.hud.right_header.text != late.get("name"),
      app.hud.right_header.text)
app.close_shopping_window_process()
CLOCK.settle()

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
check("共有なら見出しは保管庫の名前",
      app.hud.right_header.text == module.STORAGE_NAME
      and app.hud.right_header.text != getattr(shared_holder, "name", None),
      app.hud.right_header.text)
kept = app.generate_item_from_dict(sample_item("古い羅針盤"), "item_70", app.player)
classes["item"](kept, InventoryGrid(app.player)).change_inventory(
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
rent(app, module, "借りる")
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
app.elapse_days(max(c.get("term") or 0 for c in read_state()["contracts"]))
CLOCK.settle()
data = read_state() or {}
check("1軒失っても共有の中身は残る", len(data.get("shared") or {}) == 1,
      data.get("shared"))
check("役場の預かりは空のまま", not (data.get("seized") or {}), data.get("seized"))

print("[ゲームが引かなかったとき]")
# (b) 徴収が `execute` の外にあるビルド。前払いした額をそのまま引き戻す。
module, ctx, app, places, classes = setup()
rent(app, module, "建売を買い取る")
app.go(home_of(app, module, places))
app.stay_charges = False
gold_before = app.player.gold
app.press(module.STAY_LABEL)
CLOCK.settle()
check("前払いが宙に浮かない", app.player.gold == gold_before,
      (app.player.gold, gold_before))
check("引き戻したことが WARN で残る",
      "WARN stay: the room cost 0 but we prepaid {}; corrected -{}".format(
          ROOM_PRICE, ROOM_PRICE) in read_log(),
      [l for l in read_log().splitlines() if "corrected" in l][:3])
app.stay_charges = True

print("[滞在の中の他の引き落とし]")
# 前払いした回に余分に減ったぶんは差で返さない（`331_` の宿に泊まる間に来た家賃と同じ形）
app.go(home_of(app, module, places))
app.other_charge = 70
gold_before = app.player.gold
log_mark = len(read_log())
app.press(module.STAY_LABEL)
CLOCK.settle()
check("余分に減ったぶんは返さない", app.player.gold == gold_before - 70,
      (app.player.gold, gold_before))
check("返さなかったことが WARN で残る",
      "leaving it alone" in read_log()[log_mark:] and "corrected" not in read_log()[log_mark:],
      read_log()[log_mark:][-400:])
app.other_charge = 0

print("[額が分からない等級]")
# (c) ローダの窓口が額を答えられない等級。前払いはせず、引かれた差で返す。
UNKNOWN_QUALITY = "silk_bed"
module, ctx, app, places, classes = setup(
    configure=lambda m: setattr(m, "STAY_QUALITY", UNKNOWN_QUALITY))
rent(app, module, "建売を買い取る")
app.go(home_of(app, module, places))
gold_before = app.player.gold
app.press(module.STAY_LABEL)
CLOCK.settle()
check("等級が分からなくても宿代は取られない", app.player.gold == gold_before,
      (app.player.gold, gold_before))
check("前払いできなかったことを書く",
      "WARN stay: no price for the room ('{}')".format(UNKNOWN_QUALITY) in read_log(),
      [l for l in read_log().splitlines() if "no price" in l][:3])
check("差で返したことも書く",
      "WARN stay: the room cost {} but we prepaid 0; corrected {}".format(
          ROOM_PRICE, ROOM_PRICE) in read_log(),
      [l for l in read_log().splitlines() if "corrected" in l][:3])

print("[滞在の家賃は滞在の後で]")
# (d) 滞在の中では暦が進むだけ。期限が来ていても引くのは滞在が終わってから1回。
module, ctx, app, places, classes = setup()
rent(app, module)
term = contract_of(module, app)["term"]
stay_len = durations.game_inn_stay(app)["months"] * module.DAYS_PER_MONTH
app.elapse_days(term - stay_len)               # あと1回の滞在で期限が来る
CLOCK.settle()
due_before = contract_of(module, app)["due"]
gold_before = app.player.gold
check("ここまでは家賃が来ていない",
      app.world.days_elapsed < due_before, (app.world.days_elapsed, due_before))
app.go(home_of(app, module, places))
app.press(module.STAY_LABEL)
CLOCK.settle()
check("滞在の最中に期限が来ても引かない", app.player.gold == gold_before,
      (app.player.gold, gold_before, module.RENT_PRICE))
check("期限を過ぎている", app.world.days_elapsed >= due_before,
      (app.world.days_elapsed, due_before))
check("見送ったことがログに残る", "rent: postponed" in read_log(),
      [l for l in read_log().splitlines() if "rent" in l][:3])
check("期限はまだ延びていない", contract_of(module, app)["due"] == due_before,
      contract_of(module, app)["due"])
app.process_choice(classes["rest"](app, durations.game_inn_stay(app)["months"],
                                   module.STAY_QUALITY), "休養をとる")
CLOCK.settle()
check("滞在が終わると家賃を1回だけ引く",
      app.player.gold == gold_before - module.RENT_PRICE,
      (app.player.gold, gold_before, module.RENT_PRICE))
check("期限が1期ぶん延びる", contract_of(module, app)["due"] == due_before + term,
      (contract_of(module, app)["due"], due_before, term))
app.facility_screen()
CLOCK.settle()
check("画面を組み直しても二重に引かない",
      app.player.gold == gold_before - module.RENT_PRICE, app.player.gold)

print("[宿屋には手を出さない]")
module, ctx, app, places, classes = setup()
rent(app, module, "建売を買い取る")
app.go(places["inn"])
gold_before = app.player.gold
app.process_choice(classes["stay"](app, 1, "bunk"), "宿泊する")
CLOCK.settle()
check("宿屋の宿代は引かれたまま", app.player.gold == gold_before - ROOM_PRICE,
      app.player.gold)
check("宿屋では自前のボタンを足さない",
      not app.has(module.STAY_LABEL) and not app.has(module.STORAGE_LABEL),
      app.labels())
ended_before = app.stay_ended
app.process_choice(classes["rest"](app, 1, "bunk"), "休養をとる")
CLOCK.settle()
check("宿屋の活動では滞在を締めない", app.stay_ended == ended_before,
      (ended_before, app.stay_ended))

print("[滞在の描写に場所を添える]")
# ゲームは描写を頼むとき、エリアの一覧は渡すのに**どこに泊まったかを渡さない**
# （実測。GAME.md §2.17）。街に自分の家があると、
# 宿屋に泊まったのに自宅で過ごした話になる（実機）。
PROMPT_HOOK.clear()
module, ctx, app, places, classes = setup()
rewrite = PROMPT_HOOK.get("rewrite")
check("ローダの書き換えの口に載せている", callable(rewrite), PROMPT_HOOK)

STAY_PROMPT = ("今プレイヤーキャラの{}はこのエリアで数ヵ月の宿泊をし、疲労を回復した。"
               "【エリアの構造】\n{{'name': '始まりの泥濘'}}").format(PLAYER_NAME)


def asked(text=STAY_PROMPT):
    """ゲームが送る文章を1本通す。書き換えなければ None。"""
    if not callable(rewrite):
        return None
    result = rewrite([text], "chat")
    return result[0] if result else None


app.go(places["inn"])
check("家が無い街の宿泊には触らない", asked() is None, asked())

app.go(places["office"])
rent(app, module, "建売を買い取る")
house = home_of(app, module, places)
app.go(places["inn"])
inn_line = asked()
check("宿屋での宿泊には宿屋の名前を添える",
      inn_line is not None and "泥濘の休み処" in inn_line, inn_line)
check("自分の家ではないと書く",
      inn_line is not None and module.OWNED_NAME in inn_line
      and "ではない" in inn_line, inn_line)
check("エリアの一覧より前に置く",
      inn_line is not None
      and inn_line.index(module.SCENE_HEAD) < inn_line.index(module.SCENE_MARK),
      inn_line)
check("二度は足さない", asked(inn_line) is None, asked(inn_line))

# 足した1文を抜くと、ゲームの頼み文がそのまま戻る（本文はどこも削っていない）。
without = (inn_line.split(module.SCENE_HEAD)[0] + module.SCENE_MARK
           + inn_line.split(module.SCENE_MARK, 1)[1]) if inn_line else ""
check("ゲームの頼み文は1文字も削らない", without == STAY_PROMPT, without)

where = app.player.location
app.player.location = None
check("どこに立っているか読めなければ触らない", asked() is None, asked())
app.player.location = where

app.go(house)
home_line = asked()
check("自分の家での滞在は自分の家だと書く",
      home_line is not None and module.OWNED_NAME in home_line
      and "宿屋ではない" in home_line, home_line)

check("エリアの一覧が無い文章には触らない",
      asked("今プレイヤーキャラはこのエリアで数ヵ月の宿泊をした。") is None)
check("滞在の頼み文でなければ触らない",
      asked("依頼の相手を選ぶ。【エリアの構造】\n{'name': '始まりの泥濘'}") is None)

print("[例外]")
check("ctx.log_exc に例外が出ていない", not ctx.errors, ctx.errors)
check("ログに WARN が無い", "WARN" not in read_log(),
      [line for line in read_log().splitlines() if "WARN" in line][:4])

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
