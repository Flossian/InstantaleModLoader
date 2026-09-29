# -*- coding: utf-8 -*-
"""407_npc_starting_equipment をゲーム抜きで通す。

    python tools/tests/test_npc_starting_equipment.py

偽の app / World / Character / Clock / LLM を差し込み、次を確認する。

  加入     … 本体の加入はその場で通り、装備を待たない
  生成     … 武器の細分を item_detail の綴り（medium_weapon）で返されても、
             ゲームへは medium を渡して武器が作られる
  装備     … 装備欄の MOD が居れば窓口 equipment.equip に品を渡す。居なければ持ち物に
             渡すだけで、equipments は書かない（本体は仲間の equipments を保存しない）
  頼み文   … 細分の一覧を見せ、素材用の語（creature_part）を含まない
  確定     … 印は save_game が通った後に state へ書かれる。保存が落ちたら書かれない
  ロード   … 保存されなかった印と、LLM 待ちの仕事は捨てる。ロード中の加入は対象外
  再加入   … 両方渡したNPCにはLLMを呼ばない。片方だけなら欠けた種類だけ頼む
  不足     … 欠けた種類だけを聞き直す。上限を超えたら渡した種類だけを印に残す
  離脱     … LLM を待つ間に離脱したNPCには渡さない
  身に着け … もう身に着けている種類は作らない（装備欄の答え、無ければ equipments）
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
OUT_DIR = os.path.join(ROOT, "out", "test")
STATE_DIR = os.path.join(OUT_DIR, "state_npc_starting_equipment")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import equipment             # noqa: E402

MODS_DIR = os.path.join(RUNTIME_DIR, "mods")


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return folder, os.path.join(folder, json.load(fh)["entry"])


MOD_DIR, MOD = find_mod("_npc_starting_equipment")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
# ゲームの `Data/item_embeddings/` にある埋め込み（test_shop_restock.py と同じ一覧）。
EMBEDDINGS = frozenset((
    "accessory", "body_armor", "clothing", "creature", "creature_part", "document",
    "drink", "food", "gauntlets", "gem", "headgear", "large_weapon", "leg_armor",
    "legwear", "liquid_material", "long_weapon", "magical_material", "medicine",
    "medium_weapon", "metal", "mushroom", "ore", "other_material", "plant", "potion",
    "relic", "scrap", "scroll", "shield", "small_weapon", "throwable_weapon", "tool",
    "treasure",
))


class Character:
    def __init__(self, character_id, name):
        self.id = character_id
        self.name = name
        self.category = "人間"
        self.job = "傭兵"
        self.profile = "北の砦で雇われていた傭兵。"
        self.personality = "無口"
        self.look_description = "日に焼けた大柄な男"
        self.experience_level = 12
        self.config = {"difficulty_level": 10}
        self.inventory = {}
        self.equipments = {}


class World:
    def __init__(self, characters=None):
        self.characters = characters or {}


class Item:
    """ゲームの `Item`（実行時の品）。種類は `attributes['item_detail']`。"""

    def __init__(self, item_id, name, description, item_type, item_detail, value, rarity):
        self.id = item_id
        self.name = name
        self.description = description
        self.item_type = item_type
        self.attributes = {"item_detail": item_detail}
        self.value = value
        self.rarity = rarity


class InstantaleApp:
    def __init__(self, world):
        self.world = world
        self.party = ["player"]
        self.game_variables = {"party": self.party}
        self.world_dict = {"world_data": {"world_name": "テスト世界"}}
        self.save_data_dict = {"player_data": {"name": "テスト主人公"}}
        self.saves = 0
        self.fail_save = False
        self.counter = 0
        self.generated = []

    def add_party_member(self, character_id):
        if str(character_id) not in self.party:
            self.party.append(str(character_id))

    def save_game(self):
        """保存。仲間の equipments は何を入れても `{}` で書く（GAME.md §2.13.3）。持ち物は書く。"""
        if self.fail_save:
            raise RuntimeError("save failed")
        self.saves += 1
        self.saved = {cid: {"equipments": {}, "inventory": sorted(c.inventory)}
                      for cid, c in self.world.characters.items()}

    def load_game_new(self, joins=()):
        """ロードの途中で名簿を組み直す形を演じる（既存の仲間が add_party_member を通る）。"""
        for npc_id in joins:
            self.add_party_member(npc_id)

    def generate_item_from_item_data(self, item_name, description, item_type,
                                     item_sub_type, value, item_appearance,
                                     rarity, obtainer):
        """ゲームの生成。武器は細分に `_weapon` を足し、埋め込みが無ければ落ちる。"""
        item_detail = item_sub_type + "_weapon" if item_type == "weapon" else item_sub_type
        self.generated.append((item_type, item_sub_type))
        if item_detail not in EMBEDDINGS:
            raise FileNotFoundError("Data/item_embeddings/{}.json".format(item_detail))
        self.counter += 1
        key = "item_{}".format(900 + self.counter)
        obtainer.inventory[key] = Item(key, item_name, description, item_type,
                                       item_detail, value, rarity)
        return obtainer.inventory[key]


CLASSES = {"InstantaleApp": InstantaleApp, "World": World}
PRISTINE = {(cls_name, name): cls.__dict__[name]
            for cls_name, cls in CLASSES.items()
            for name in ("add_party_member", "save_game", "load_game_new", "__init__")
            if name in cls.__dict__}


class FakeClock:
    def __init__(self):
        self.onces = []

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def run_onces(self):
        pending, self.onces = self.onces, []
        for callback in pending:
            callback(0.0)


CLOCK = FakeClock()
RUNNING = [None]


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = CLOCK
    kivy_app = types.ModuleType("kivy.app")

    class App:
        @staticmethod
        def get_running_app():
            return RUNNING[0]

    kivy_app.App = App
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    sys.modules["kivy.app"] = kivy_app


class FakeLLM:
    """ローダの LLM 経路を演じる。返答は積んだ順に1つずつ使い、尽きたら最後を繰り返す。"""

    def __init__(self):
        self.asked = []
        self.replies = []

    def create_structure(self, ctx, name, fields, label="llm"):
        return name

    def ask(self, ctx, manager_name, message, *, timeout, structure=None,
            max_tokens=None, label="llm", write=None):
        self.asked.append((manager_name, message))
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0] if self.replies else None


LLM = FakeLLM()


def install_fake_llm():
    from instantale_modloader import llm
    llm.create_structure = LLM.create_structure
    llm.ask = LLM.ask


def weapon(sub="medium_weapon", name="古びた長剣"):
    return {"item_type": "weapon", "item_name": name, "description": "使い込まれた剣。",
            "item_sub_type": sub, "rarity": "rare",
            "item_appearance": "A worn longsword."}


def wearable(sub="body_armor", name="傷だらけの革鎧"):
    return {"item_type": "wearable", "item_name": name, "description": "傷の多い鎧。",
            "item_sub_type": sub, "rarity": "common",
            "item_appearance": "A scarred leather armor."}


class FakeCtx:
    def __init__(self, out_dir, state_dir):
        self.out_dir = out_dir
        self.state_dir = state_dir
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
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1]
                    else path, exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def superseded(self):
        return False

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        """偽のクラスへ実際に差し込む（本体の呼び出しがフックを通るように）。"""
        def decorator(func):
            cls_name, name = target.split(":", 1)[1].split(".")
            orig = PRISTINE[(cls_name, name)]

            def installed(obj, *args, **kwargs):
                return func(orig, obj, *args, **kwargs)
            setattr(CLASSES[cls_name], name, installed)
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="npc_starting_equipment_mod"):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 舞台作り
NPC_ID = "64"


def fresh(keep_state=False):
    """mod を読み直して当て直す。世代をまたぐ控えは毎回捨てる。"""
    for (cls_name, name), func in PRISTINE.items():
        setattr(CLASSES[cls_name], name, func)
    module = load_mod()
    for attr in (module.STATE_STORE, module.RUNTIME_STORE):
        if hasattr(sys, attr):
            delattr(sys, attr)
    if not keep_state and os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    log_path = os.path.join(OUT_DIR, module.LOG_BASENAME)
    if os.path.exists(log_path):
        os.remove(log_path)
    CLOCK.onces = []
    LLM.asked = []
    LLM.replies = [{"items": [weapon(), wearable()]}]
    ctx = FakeCtx(OUT_DIR, STATE_DIR)
    module.apply(ctx)
    npc = Character(NPC_ID, "寡黙なガルド")
    app = InstantaleApp(World({NPC_ID: npc}))
    RUNNING[0] = app
    return module, ctx, app, npc


def settle(module):
    """背景ワーカーとメインスレッドの予約が尽きるまで回す。"""
    worker = getattr(sys, module.RUNTIME_STORE)["worker"]
    for _ in range(20):
        worker.jobs.join()
        if not CLOCK.onces:
            return
        CLOCK.run_onces()


def marks_on_disk(module):
    path = os.path.join(STATE_DIR, module.STATE_DIRNAME)
    if not os.path.isdir(path):
        return {}
    out = {}
    for name in os.listdir(path):
        with io.open(os.path.join(path, name), encoding="utf-8") as fh:
            out[name] = json.load(fh).get("npcs", {})
    return out


def read_log(module):
    path = os.path.join(OUT_DIR, module.LOG_BASENAME)
    if not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


install_fake_kivy()
install_fake_llm()

class FakeSlots:
    """装備欄の MOD（333）の代わり。窓口 `equipment` に EQUIP と GEAR を置く。"""

    def __init__(self, answer="equipped"):
        self.answer = answer
        self.asked = []
        self.worn = {}
        equipment.declare(equipment.EQUIP, self.equip, owner="test_slots")
        equipment.declare(equipment.GEAR, self.gear, owner="test_slots")

    def equip(self, app, holder, item):
        self.asked.append((holder.id, item))
        if self.answer == "equipped":
            self.worn.setdefault(holder.id, []).append(("slot", item))
        return self.answer

    def gear(self, app, holder):
        return self.worn.get(holder.id)

    def close(self):
        equipment.forget("test_slots")


def handed_types(npc):
    return sorted(getattr(item, "item_type", "?") for item in npc.inventory.values()
                  if isinstance(item, Item))


GIVEN_BOTH = {"generated": True, "given": ["weapon", "wearable"]}

# ============================================================ 加入と生成
print("=== 加入と生成（装備欄の MOD が居ない） ===")
module, ctx, app, npc = fresh()
app.add_party_member(NPC_ID)
check("本体の加入はその場で通る（装備を待たない）", NPC_ID in app.party, app.party)
check("加入の時点ではまだ何も渡していない", not npc.inventory, npc.inventory)
settle(module)
check("LLM は1回だけ呼ばれる", len(LLM.asked) == 1, len(LLM.asked))
check("武器は medium でゲームへ渡る（medium_weapon_weapon にしない）",
      ("weapon", "medium") in app.generated and
      ("weapon", "medium_weapon") not in app.generated, app.generated)
check("武器と防具が持ち物に入る", handed_types(npc) == ["weapon", "wearable"], npc.inventory)
check("equipments は書かない（本体が保存しない）", npc.equipments == {}, npc.equipments)
sword = next((i for i in npc.inventory.values() if i.item_type == "weapon"), None)
check("武器の item_detail は medium_weapon",
      getattr(sword, "attributes", {}).get("item_detail") == "medium_weapon", sword)
check("説明は LLM の日本語の説明（英語の外見文を混ぜない）",
      getattr(sword, "description", None) == "使い込まれた剣。", sword)
check("価値段階は NPC の難易度とレベルから決まる（max(10, 12-5)=10）",
      getattr(sword, "value", None) == 10, sword)
check("渡した後にゲームの save_game を呼ぶ", app.saves == 1, app.saves)
check("品は持ち物としてセーブに入る",
      set(npc.inventory) <= set(app.saved.get(NPC_ID, {}).get("inventory", [])), app.saved)
check("印は保存の後に state へ書かれる",
      marks_on_disk(module) == {"テスト世界×テスト主人公.json": {NPC_ID: GIVEN_BOTH}},
      marks_on_disk(module))
check("持ち物に渡しただけだと記録する", "-> inventory" in read_log(module), read_log(module))
check("例外は出ない", not ctx.errors, ctx.errors)

system = LLM.asked[0][1][0]["content"]
user = LLM.asked[0][1][1]["content"]
check("頼み文に武器の細分の一覧がある", "small / medium / long / large / throwable" in system, system)
check("頼み文に防具の細分の一覧がある", "body_armor" in system and "gauntlets" in system, system)
check("素材用の説明が残っていない", "creature_part" not in system and "}" not in system, system)
check("user には人物の項目が渡る", "寡黙なガルド" in user and "傭兵" in user, user)

# ============================================================ 装備欄の MOD
print("=== 装備欄の MOD が居る ===")
module, ctx, app, npc = fresh()
slots = FakeSlots()
try:
    app.add_party_member(NPC_ID)
    settle(module)
    check("窓口に2品とも渡す", [type(i).__name__ for _h, i in slots.asked] == ["Item", "Item"]
          and all(h == NPC_ID for h, _i in slots.asked), slots.asked)
    check("窓口へ渡すのは持ち物に入った品そのもの",
          all(npc.inventory.get(i.id) is i for _h, i in slots.asked), slots.asked)
    check("equipments は窓口の側に任せる（407 は書かない）", npc.equipments == {}, npc.equipments)
    check("窓口の答えを記録する", read_log(module).count("-> equipped") == 2, read_log(module))
    check("印は同じ", marks_on_disk(module) ==
          {"テスト世界×テスト主人公.json": {NPC_ID: GIVEN_BOTH}}, marks_on_disk(module))
finally:
    slots.close()

print("=== 装備欄が断る ===")
module, ctx, app, npc = fresh()
slots = FakeSlots(answer="no free slot")
try:
    app.add_party_member(NPC_ID)
    settle(module)
    check("断られても品は持ち物に残り、渡したことにする",
          handed_types(npc) == ["weapon", "wearable"] and
          marks_on_disk(module) == {"テスト世界×テスト主人公.json": {NPC_ID: GIVEN_BOTH}},
          (npc.inventory, marks_on_disk(module)))
    check("断りの理由を記録する", "-> no free slot" in read_log(module), read_log(module))
finally:
    slots.close()

# ============================================================ 再加入
print("=== 再加入 ===")
module, ctx, app, npc = fresh()
app.add_party_member(NPC_ID)
settle(module)
app.party.remove(NPC_ID)
npc.inventory.clear()                  # 渡した品を売った・譲った
app.add_party_member(NPC_ID)
settle(module)
check("両方渡したNPCにはLLMを呼ばない", len(LLM.asked) == 1, len(LLM.asked))

print("=== 読み直した世代でも印を読む ===")
module, ctx, app, npc = fresh(keep_state=True)
app.add_party_member(NPC_ID)
settle(module)
check("state の印だけで済んだとみなす", not LLM.asked, len(LLM.asked))

# ============================================================ 保存が落ちる
print("=== 保存が落ちる ===")
module, ctx, app, npc = fresh()
app.fail_save = True
app.add_party_member(NPC_ID)
settle(module)
check("品は渡る", handed_types(npc) == ["weapon", "wearable"], npc.inventory)
check("保存が落ちたら印を書かない", marks_on_disk(module) == {}, marks_on_disk(module))
check("保存待ちの印で二重には頼まない",
      (app.party.remove(NPC_ID), app.add_party_member(NPC_ID),
       settle(module), len(LLM.asked))[-1] == 1, len(LLM.asked))
World(None)            # ロード（World.__init__）
check("ロードで保存されなかった印を捨てる", "unsaved marks dropped" in read_log(module),
      read_log(module))
app.fail_save = False
app.save_game()
check("捨てた印は後の保存でも書かれない", marks_on_disk(module) == {}, marks_on_disk(module))

# ============================================================ ロード中の加入
print("=== ロード中の加入 ===")
module, ctx, app, npc = fresh()
app.load_game_new(joins=[NPC_ID])
settle(module)
check("ロードの途中の加入は対象外", NPC_ID in app.party and not LLM.asked,
      (app.party, len(LLM.asked)))

# ============================================================ LLM を待つ間のロード
print("=== LLM を待つ間のロード ===")
module, ctx, app, npc = fresh()
worker = getattr(sys, module.RUNTIME_STORE)["worker"]
app.add_party_member(NPC_ID)
worker.jobs.join()
World(None)            # 返答がメインスレッドへ戻る前にロード
CLOCK.run_onces()
settle(module)
check("前の世界の仕事は捨てる", not npc.inventory and not app.generated,
      (npc.inventory, app.generated))

# ============================================================ 不足
print("=== 不足 ===")
module, ctx, app, npc = fresh()
LLM.replies = [{"items": [wearable()]}, {"items": [weapon(sub="long")]}]
app.add_party_member(NPC_ID)
settle(module)
check("欠けた武器だけを聞き直す", len(LLM.asked) == 2, len(LLM.asked))
retry_system = LLM.asked[-1][1][0]["content"] if len(LLM.asked) > 1 else ""
check("聞き直しの頼み文は武器だけ", "身に着けている武器を" in retry_system
      and "body_armor" not in retry_system, retry_system)
check("聞き直しで両方揃う", handed_types(npc) == ["weapon", "wearable"], npc.inventory)
check("揃ったら両方の印を書く", marks_on_disk(module) ==
      {"テスト世界×テスト主人公.json": {NPC_ID: GIVEN_BOTH}}, marks_on_disk(module))

print("=== 不足のまま上限 ===")
module, ctx, app, npc = fresh()
LLM.replies = [{"items": [wearable()]}]
app.add_party_member(NPC_ID)
settle(module)
check("聞き直しは上限まで（最初の1回＋2回）",
      len(LLM.asked) == 1 + module.MAX_ITEM_RETRIES, len(LLM.asked))
check("防具は渡して保存する", handed_types(npc) == ["wearable"] and app.saves >= 1,
      (npc.inventory, app.saves))
check("渡した種類だけを印に残す", marks_on_disk(module) == {"テスト世界×テスト主人公.json": {
    NPC_ID: {"generated": False, "given": ["wearable"]}}}, marks_on_disk(module))
check("不足を記録する", "starting equipment incomplete" in read_log(module), read_log(module))
LLM.asked.clear()
LLM.replies = [{"items": [weapon(), wearable()]}]
app.party.remove(NPC_ID)
app.add_party_member(NPC_ID)
settle(module)
check("再加入では欠けた武器だけを頼む", len(LLM.asked) == 1 and
      "身に着けている武器を" in LLM.asked[0][1][0]["content"], LLM.asked)
check("防具は二重に渡さない", handed_types(npc) == ["weapon", "wearable"], npc.inventory)
check("揃ったら generated になる", marks_on_disk(module) ==
      {"テスト世界×テスト主人公.json": {NPC_ID: GIVEN_BOTH}}, marks_on_disk(module))

print("=== LLM が返らない ===")
module, ctx, app, npc = fresh()
LLM.replies = [None]
app.add_party_member(NPC_ID)
settle(module)
check("何も渡さず、上限で止まる", not npc.inventory and
      len(LLM.asked) == 1 + module.MAX_ITEM_RETRIES, (npc.inventory, len(LLM.asked)))
check("何も渡さなければ印も保存も無い", marks_on_disk(module) == {} and app.saves == 0,
      (marks_on_disk(module), app.saves))

print("=== 細分が語彙の外 ===")
module, ctx, app, npc = fresh()
LLM.replies = [{"items": [weapon(sub="creature_part"), wearable(sub="cape")]}]
app.add_party_member(NPC_ID)
settle(module)
check("武器は medium、防具は body_armor に寄せる",
      ("weapon", "medium") in app.generated and ("wearable", "body_armor") in app.generated,
      app.generated)

# ============================================================ 離脱
print("=== 離脱 ===")
module, ctx, app, npc = fresh()
worker = getattr(sys, module.RUNTIME_STORE)["worker"]
app.add_party_member(NPC_ID)
worker.jobs.join()
app.party.remove(NPC_ID)
settle(module)
check("LLM を待つ間に離脱したNPCには渡さない", not npc.inventory and not app.generated,
      (npc.inventory, app.generated))

# ============================================================ もう身に着けている
print("=== もう身に着けている（equipments） ===")
module, ctx, app, npc = fresh()
npc.inventory["item_1"] = {"name": "持参の槍"}
npc.equipments["weapon"] = "item_1"
app.add_party_member(NPC_ID)
settle(module)
check("空いている防具だけを頼む", "身に着けている防具を" in LLM.asked[0][1][0]["content"],
      LLM.asked[0][1][0]["content"])
check("身に着けている武器には触らない", npc.equipments == {"weapon": "item_1"}, npc.equipments)

print("=== もう身に着けている（装備欄） ===")
module, ctx, app, npc = fresh()
slots = FakeSlots()
try:
    spear = Item("item_2", "持参の槍", "", "weapon", "long_weapon", 5, "common")
    slots.worn[NPC_ID] = [("right_hand", spear)]
    app.add_party_member(NPC_ID)
    settle(module)
    check("装備欄の答えを見て、防具だけを頼む",
          len(LLM.asked) == 1 and "身に着けている防具を" in LLM.asked[0][1][0]["content"],
          LLM.asked)
    check("身に着けている槍は窓口へ渡し直さない",
          [i.item_type for _h, i in slots.asked] == ["wearable"], slots.asked)
finally:
    slots.close()

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all passed")
