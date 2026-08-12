# -*- coding: utf-8 -*-
"""902_city_case をゲーム抜きで通す。

    python tools/tests/test_city_case.py

`305_` は実機4回かけて直している（VERIFICATION_LOG.md §2.19〜§2.22）。実機の1周は
時間がかかるうえ、外したときに原因の切り分けが難しい。**実機でしか分からない
こと以外は全部ここで潰す。**

  事件     … 真相が最初に固定される／段階が手がかりの数で進む／
              前提の揃わない手がかりは拾えない／別の世界の控えは捨てる
  判定     … 押された選択肢の飛び先ラベルだけで手がかりが立つ。
              **AI もエンジンも介さない**／告発の正否は id の照合だけ
  シーン    … 組んだプログラムが DSL として筋が通っている
              （型・ラベル重複・飛び先・**どの経路も end に着く**）／
              容疑者全員に飛び先がある
  設置     … 施設の種類と段階でボタンが出し分かる／二重にならない／
              印を失った残骸を落とす／事件の町の外では出ない
  安全     … `flag_set` を使わない（セーブを汚さない）／
              ゲームの `free_*` を横取りしない
  共存     … `301_` / `302_` / `305_` / `309_` と印のキーが衝突していない
  名乗り    … mod.json の既定値とコードの定数が一致する
"""
import ast
import re
import traceback
import importlib.util
import io
import json
import os
import random
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
#: 控えと台帳の置き場（本番は配布フォルダ直下の `state/`）。
STATE_DIR = os.path.join(OUT_DIR, "state")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


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
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_city_case")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
GUILD = "guild"
INN = "inn"
MARKET = "underworld_office"
EXIT_TEXT = "出る"


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        # **印は書かれない。**これが残骸の重複を生む（`ui.Screen.prune_stale`）。
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class ConversationStartManager:
    """会話を起こす側。**`300_` が実証した経路**（GAME.md §2.5）。"""

    def __init__(self, app, character_id):
        self.app = app
        self.character_id = character_id

    def execute(self, choice_text):
        self.app.conversations.append(self.character_id)
        return None


class ConversationEndManager:
    """会話を閉じる側。閉じた後に「分かったこと」が出る。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text=""):
        return None


class MovePhaseManager:
    """施設の出口。**施設の選択肢である目印**として mod が spec で見る。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class Facility:
    def __init__(self, facility_id, name, facility_type, owner=None,
                 characters=()):
        self.id = facility_id
        self.name = name
        self.facility_type = facility_type
        self.owner = owner
        self.characters = list(characters)
        self.config = {"level_of_detail": 0}


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, name, facilities):
        self.id = area_id
        self.name = name
        self.nodes = {"0": Node(facilities)}


class Character:
    def __init__(self, character_id, name, category="middle-aged man",
                 profile="素性のある人物。", personality="無口。"):
        self.id = character_id
        self.name = name
        self.category = category
        self.profile = profile
        self.personality = personality
        self.config = {"level_of_detail": 2, "is_player": False,
                       "is_dead": False, "difficulty_level": 4}


def crowd(character_id, name="混乱する村人たち"):
    """個人ではない登場人物。**犯人に選んではいけない。**

    実機で `混乱する村人たち` が犯人に選ばれた。世界には
    群衆や戦闘から生えた相手が混ざる。
    """
    return Character(character_id, name, category="crowd of villagers",
                     profile="", personality="")


class World:
    def __init__(self, areas, characters):
        self.name = "テスト世界"
        self.areas = areas
        self.characters = characters
        self.generated = []
        #: 既存 NPC の素データ。**MOD はここを見つけて書けなければならない。**
        self.npcs = {cid: {"name": c.name, "id": cid} for cid, c in characters.items()}

    #: World 自身が握る素データ（`save_data_dict` 相当）。
    #: **`app.world_dict` とは別物。**
    save = None

    def generate_character(self, character_id, character_value):
        """**セーブに在るデータを id で引いて `Character` を組む側。**

        世界に無い id を渡すと `KeyError`（実機で踏んだ）。
        だから MOD は先に `world_dict['npcs'][id]` へ書いてから呼ぶ。
        その順序を守っているかを、本物と同じ落ち方で検査する。
        """
        if GENERATE_FAILS["on"]:
            raise RuntimeError("generation is unavailable in this build")
        # **実機では `app.world_dict` とは別の辞書を読む**（実測）。
        # MOD が心当たりを全部書くことを検査したいので、
        # ここも World 自身が持つ辞書から引く。
        npcs = self.npcs
        if character_id not in npcs:
            raise KeyError(character_id)
        data = npcs[character_id]
        self.generated.append((character_id, data))
        GENERATED.append(data)
        if COLLIDING_IDS["on"]:
            # **実行時の名簿に載らない実装**の再現。載らないと次の採番が
            # 進まず、2人目が1人目を上書きする（実機で3人とも 47 になった）。
            return None
        self.characters[character_id] = Character(
            character_id, data.get("name", "?"),
            category=data.get("category", "middle-aged man"),
            profile=data.get("profile", ""),
            personality=data.get("personality", ""))
        return self.characters[character_id]


class Player:
    def __init__(self, area, location):
        self.name = "テストプレイヤー"
        self.current_area = area
        self.location = location
        self.gold = 1000


class InstantaleApp:
    def __init__(self, world, player):
        self.world = world
        self.player = player
        # セーブそのもの。**NPC を作る経路がここへ書く**（実セーブのキーに
        # 合わせてある。`209_` の world survey より）。
        self.world_dict = {"npcs": {}, "areas": {}, "quests": {}}
        world.save = self.world_dict
        self.moved = []
        CURRENT_WORLD["world"] = world
        CURRENT_WORLD["ids"] = tuple(world.characters)
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.process_choice_calls = []
        self.conversations = []
        self.is_button_enabled = True
        self.is_adding_text = False
        self.is_popup_window_opened = False
        for flag in ("in_battle", "in_boss_battle", "in_colosseum_battle",
                     "in_conversation", "in_free_input",
                     "in_action_in_conversation", "in_shopping"):
            setattr(self, flag, False)

    def add_text(self, context):
        self.texts.append(context)

    def update_ui(self, *args):
        return None

    def process_choice(self, function, choice_text=""):
        self.process_choice_calls.append((type(function).__name__, choice_text))
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def display_button_load(self, dt):
        return None

    # -- テスト用の道具 ----------------------------------------------------
    def facility_screen(self):
        self.buttons = [{"text": EXIT_TEXT,
                         "spec": PhaseSpec("MovePhaseManager", ["0", "1", "0"])}]
        self.refresh_choice_buttons()
        return self.buttons

    def labels(self):
        return [entry.get("text") for entry in self.buttons]

    def move_npc_to_facility(self, character_id, character_instance,
                             target_facility, target_node=None,
                             register_facility=True):
        """`302_` が実証した配置の経路。名簿に id を足す。"""
        self.moved.append((character_id, getattr(target_facility, "id", None)))
        target_facility.characters.append(character_id)

    def go(self, facility):
        self.player.location = facility


#: 生成が使えないビルドの再現（`generate_character` を失敗させる）。
GENERATE_FAILS = {"on": False}
#: 採番が進まない実装の再現（実機で踏んだ）。
COLLIDING_IDS = {"on": False, "value": "47"}
GENERATED = []


def install_save_area_json():
    """`save_area_json.generate_npc` は**作る側ではなかった**（実測）。

    呼んでも `world_dict` にも `world.characters` にも何も現れない。
    MOD が誤ってこちらへ戻らないよう、呼ばれたら記録だけして何もしない
    ものを置いておく（世界丸ごとを返すところまで本物どおり）。
    """
    module = types.ModuleType("save_area_json")
    calls = []

    def generate_npc(npc_data, world_dict, area_id, facility_id, job=""):
        calls.append((area_id, facility_id))
        return world_dict

    module.generate_npc = generate_npc
    module.calls = calls
    sys.modules["save_area_json"] = module
    return module


#: いま試している世界（偽の生成から触るため）。
CURRENT_WORLD = {"world": None, "ids": ()}
SAVE_AREA = install_save_area_json()

def install_fake_hud():
    name = "scripts.hud.new_hud"
    module = types.ModuleType(name)

    class InstanTaleHUD:
        def __init__(self):
            self.buttons = [types.SimpleNamespace(text="") for _ in range(4)]

    module.InstanTaleHUD = InstanTaleHUD
    sys.modules.setdefault("scripts.hud", types.ModuleType("scripts.hud"))
    sys.modules[name] = module


install_fake_hud()


# ---------------------------------------------------------------- mod を読む
def load_mod(name="city_case_mod"):
    """本番と同じ形（**パッケージとして**）読み込む（`_load_mod_file` と同じ）。

    `submodule_search_locations` を渡すのも、`exec_module` の**前に**
    `sys.modules` へ登録するのもローダと同じ。これが無いと mod の中の
    `from . import case` が落ちる。
    """
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


mod = load_mod()
case_mod = sys.modules["city_case_mod.case"]
ledger_mod = sys.modules["city_case_mod.ledger"]
world_mod = sys.modules["city_case_mod.world"]
patterns_mod = sys.modules["city_case_mod.patterns"]

#: 事件の材料。**本番と同じ経路で読む**（`patterns/*.json` → 足りない束は同梱）。
BOOK = patterns_mod.load(MOD_DIR, defaults=mod.BUILT_IN)
CAST_POOL = BOOK["cast"]
#: 検証で使う固定の2軸（`fake_case` は特徴の割り当てを自分で決めるため）。
AXES = {axis["id"]: axis for axis in BOOK["axes"]}
FIXED_AXES = [AXES[name] for name in ("hair", "build") if name in AXES]
TRAIT_WORDS = {(a["id"], v["id"]): v["word"] for a in BOOK["axes"] for v in a["values"]}
TRAIT_FACTS = {(a["id"], v["id"]): v["fact"] for a in BOOK["axes"] for v in a["values"]}


class FakeCtx:
    api = 1
    version = "test"

    def __init__(self):
        self.hooks = {}
        self.mod_dir = MOD_DIR
        self.config = {}
        self.errors = []

    def out_path(self, name):
        os.makedirs(OUT_DIR, exist_ok=True)
        return os.path.join(OUT_DIR, name)

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def state_path(self, name):
        """永続データの置き場。本番と同じく out/ とは**別のフォルダ**にする。"""
        os.makedirs(STATE_DIR, exist_ok=True)
        return os.path.join(STATE_DIR, name)

    def wrap(self, target, **kwargs):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator

    patch = wrap

    #: 注入し直されたか。検証では常に「今の世代が現役」。真を入れると、
    #: 書き上がった素材を捨てて降りる側（`compose`）を通せる。
    superseded_now = False

    def superseded(self):
        return self.superseded_now

    def log(self, *args, **kwargs):
        return None

    def log_exc(self, *args, **kwargs):
        """**握り潰さない。**

        本番の `ctx.log_exc` はゲームを落とさないために例外を飲むが、検証で
        同じことをすると「何も起きない」だけが見えて原因が分からなくなる。
        ここでは必ず出す。
        """
        self.errors.append(args[0] if args else "")
        traceback.print_exc()

    def on_ready(self, fn, **kwargs):
        return True

    def setting(self, name, default=None):
        return getattr(mod, name, default)


def fresh_world():
    guild = Facility("1", "ギルド", GUILD, owner="10",
                     characters=["10", "20", "21", "22"])
    inn = Facility("2", "宿屋", INN, owner="11", characters=["11", "20"])
    market = Facility("3", "闇市", MARKET, owner="12", characters=["12", "21"])
    area = Area("0", "テストの町", [guild, inn, market])
    characters = {cid: Character(cid, "NPC" + cid)
                  for cid in ("10", "11", "12", "20", "21", "22")}
    world = World({"0": area}, characters)
    player = Player(area, guild)
    app = InstantaleApp(world, player)
    return app, {"guild": guild, "inn": inn, "market": market}


def install(ctx, app):
    """フックを本物と同じ順で当てる（`refresh` の前に挿す）。"""
    def refresh(reset_page=False):
        hook = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"]
        return hook(lambda self, rp=False: InstantaleApp.refresh_choice_buttons(
            self, rp), app, reset_page)
    return refresh


def press(ctx, app, text):
    """画面の文字列でボタンを押す（ゲームの `on_button_press` を通す）。"""
    for index, entry in enumerate(app.buttons):
        if entry.get("text") == text:
            hook = ctx.hooks["__main__:InstantaleApp.on_button_press"]
            return hook(lambda self, i, *a, **k: None, app, index)
    raise AssertionError("no button {!r} in {}".format(text, app.labels()))


def talk_to(ctx, app, npc_id):
    """その人物と会話する。**話しかけ方は問わない** ―
    こちらのボタンからでも、ゲーム本来の「会話する」からでも同じ経路。"""
    hook = ctx.hooks["scripts.llm.llm_manager:conversation_facilitator"]
    character = app.world.characters[npc_id]
    seen = {}

    def fake(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return None

    hook(fake, "m", "log", "p", character, "w", "s", "r", "a", [], [],
         "instr", "既存の知識")
    return seen


def greet(ctx, app, npc_id):
    """会話に**入るだけ**（第一声）。プレイヤーはまだ何も尋ねていない。

    実機ではこの後すぐ抜けられる。そのとき何も手に入らないことが要点
    （利用者の指摘）。
    """
    hook = ctx.hooks["scripts.llm.llm_manager:conversation_starter"]
    character = app.world.characters[npc_id]
    seen = {}

    def fake(messages, *args, **kwargs):
        seen["messages"] = messages
        return None

    hook(fake, [{"role": "user", "content": "はじめまして"}],
         "log", "p", character, "rel", "w", "s", "a", "ach")
    return seen


def end_conversation(ctx, app):
    """会話を閉じる。**分かったことはここで出る。**

    相手の分からないマネージャ（`in_conversation_id` を持たない版）を模す。
    """
    hook = ctx.hooks["__main__:ConversationEndManager.execute"]
    manager = ConversationEndManager(app)
    return hook(lambda self, *a, **k: None, manager)


def end_conversation_with(ctx, app, npc_id):
    """**誰との**会話が閉じたのかが分かる版。

    実機の `ConversationEndManager.__init__(self, app, in_conversation_id, ...)`
    に合わせる。これがあると、印を付けた相手と閉じた会話が同じかを
    突き合わせられる。
    """
    hook = ctx.hooks["__main__:ConversationEndManager.execute"]
    manager = ConversationEndManager(app)
    manager.in_conversation_id = str(npc_id)
    return hook(lambda self, *a, **k: None, manager)


def accuse(ctx, app, npc_id):
    """告発する。**自前のボタンを2回押すだけ**（ゲームの場面を使わない）。

    「犯人を告発する」で容疑者が並び、その1人を押すと決着する。
    以前はゲームの自由施設に場面を組ませていたが、`scripts.free_facility` が
    まだ import されていないと押しても詰む作りだった（利用者の指摘）。判定は元から id の突き合わせだけなので、経路だけ変えた。
    """
    app.facility_screen()
    refresh_for(ctx, app)
    press(ctx, app, mod.ACCUSE_LABEL)
    # 実機では `screen.refresh` が**フック済みの** `refresh_choice_buttons` を
    # 呼ぶので並びが組み直される。検証では素のメソッドなので、ここで通す。
    refresh_for(ctx, app)
    label = [t for t in app.labels()
             if str(t).startswith(mod.ACCUSE_ONE_PREFIX)
             and "（" in str(t) and mod.game.name_of(app, npc_id) in str(t)]
    if not label:
        raise AssertionError(
            "容疑者 {} のボタンが出ていない: {}".format(npc_id, app.labels()))
    return press(ctx, app, label[0])


def refresh_for(ctx, app):
    """その app のボタンを組み直す（`install` を通していない場面で使う）。"""
    hook = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"]
    return hook(lambda self, *a, **k: None, app)


def clean_record(ctx):
    """控えと台帳の両方を消す。**節どうしを独立させる。**

    台帳（`city_case_cast.json`）も消さないと、前の節が作って決着させずに
    放り出したキャストが残り、後の節の検査に混ざる。実際に混ざった。
    """
    for basename in (mod.RECORD_BASENAME, mod.LEDGER_BASENAME):
        path = ctx.state_path(basename)
        if os.path.exists(path):
            os.remove(path)


# ================================================================== 事件
def solvable_when_complete(case):
    """**真の話を全部集めれば1人に絞れるか。**詰んだ事件を出していないか。

    思い違いの証言（`wrong`）は**信じない前提**で数える。それを信じると
    誰も残らないのが正しい挙動なので、ここで一緒に立てると常に落ちる。
    """
    probe = json.loads(json.dumps(case))
    for clue in probe["clues"]:
        clue["found"] = not clue.get("wrong")
    return case_mod.solvable(probe)


def has_mistake(case):
    return any(clue.get("wrong") for clue in case.get("clues", []))


def open_case_at(ctx, app):
    """ギルドで噂を聞いて事件を1件始める。"""
    app.facility_screen()
    hook = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"]
    hook(lambda self, rp=False: InstantaleApp.refresh_choice_buttons(self, rp),
         app, False)
    press(ctx, app, mod.START_LABEL)
    return case_mod.load(ctx.state_path(mod.RECORD_BASENAME))


print("[事件] 真相が最初に固定され、事実で容疑者が消える")
CAST = [dict(base, traits={'sex': base['sex'],
                          'hair': ('dark' if i % 2 == 0 else 'light'),
                          'build': ('large' if i < 2 else 'small')})
        for i, base in enumerate(CAST_POOL)]
FACTS = None


def tell_of(traits):
    """特徴を1行に（MOD 側と同じ規則）。"""
    return "{}{}{}".format(
        TRAIT_WORDS.get(("hair", traits.get("hair")), ""),
        TRAIT_WORDS.get(("build", traits.get("build")), ""),
        mod.SEX_WORDS.get(traits.get("sex"), ""))


def fake_case(culprit=0, n=4):
    """`plan_facts` と同じ規則で組んだ事件（`open_case` を通さずに調べる）。"""
    cast = [dict(spec, npc_id=str(100 + i)) for i, spec in enumerate(CAST[:n])]
    # **裏取りの経路を通すための仕掛け。**特徴が犯人と完全に一致する者を
    # 1人置くと、その人は特徴では消せないので裏取りの事実が生まれる。
    #
    # MOD 本体はこういう置き方を**していない**（かつては「双子」として必ず
    # 1人置いていたが、面白さに寄与しないので外した）。ここは
    # `case.py` の裏取りまわりを調べるための作り物。
    if len(cast) > 1:
        cast[1]["traits"] = dict(cast[culprit]["traits"])
    facts = []
    others = [i for i in range(len(cast)) if i != culprit]
    for key, value in sorted(cast[culprit].get("traits", {}).items()):
        text = TRAIT_FACTS.get((key, value))
        drops = [i for i in others
                 if cast[i].get("traits", {}).get(key) != value]
        if not text or not drops or len(drops) == len(others):
            continue
        facts.append({"kind": "trait", "fact": text, "drops": drops})
    left = set(others)
    for fact in facts:
        left -= set(fact["drops"])
    if left:
        facts.append({"kind": "alibi", "fact": "alibi", "drops": sorted(left)})
    clues = [{"id": "c{}".format(i + 1), "label": "clue_c{}".format(i + 1),
              "at_type": INN, "intro": "", "ask": "", "prompt": "",
              "kind": f["kind"], "fact": f["fact"],
              "eliminates": [cast[j]["npc_id"] for j in f["drops"]]}
             for i, f in enumerate(facts)]
    case = case_mod.build("W", "0", cast[culprit]["npc_id"],
                          [{"id": m["npc_id"],
                            "tell": tell_of(m["traits"]),
                            "claim": "その晩は酒場に居た"} for m in cast],
                          clues, 500)
    return case, cast, facts


case0, cast0, facts0 = fake_case()
check("犯人が最初に決まっている", case0["culprit"] == "100", case0["culprit"])
check("容疑者に特徴が添えられている",
      all(s["tell"] for s in case0["suspects"]), case0["suspects"])
check("**どの事実も単独では犯人を特定できない**",
      all(len(f["drops"]) < len(cast0) - 1 for f in facts0),
      [(f["fact"], f["drops"]) for f in facts0])
check("特徴で消せない者が居れば裏取りの事実が生まれる（作り物の仕掛け）",
      any(f["kind"] == "alibi" for f in facts0),
      [f["kind"] for f in facts0])
check("最初は誰も除外されていない",
      len(case_mod.remaining(case0)) == len(cast0), case_mod.remaining(case0))
check("最初は解けない", not case_mod.solvable(case0))

for clue in case0["clues"]:
    case_mod.mark_found(case0, clue["id"])
check("**全部集めれば必ず1人に絞れる**",
      case_mod.solvable(case0), case_mod.remaining(case0))
check("残るのは犯人", case_mod.remaining(case0) == [case0["culprit"]],
      case_mod.remaining(case0))

print("\n[事件] 順番は強制しない")
case1, _cast1, _f1 = fake_case()
last = case1["clues"][-1]
check("最後の手がかりからでも拾える",
      case_mod.pending_at(case1, last["at_type"]) is not None)
case_mod.mark_found(case1, last["id"])
check("拾った分だけ容疑者が減る",
      len(case_mod.remaining(case1)) < len(case1["suspects"]),
      case_mod.remaining(case1))

print("\n[事件] 途中でも告発できる／外したら終わり")
case2, _c2, _f2 = fake_case()
check("**手がかり0でも告発できる状態**", case_mod.is_active(case2))
wrong = [s["id"] for s in case2["suspects"] if s["id"] != case2["culprit"]][0]
check("別人を告げたら失敗", case_mod.close(case2, wrong) is False)
check("**一度外したら終わり**", not case_mod.is_active(case2), case2["stage"])

case3, _c3, _f3 = fake_case()
check("犯人を告げたら成功", case_mod.close(case3, case3["culprit"]) is True)
check("決着したら閉じる", case3["stage"] == case_mod.CLOSED)

print("\n[事件] 別の世界の控えは使わない")
check("同じ世界なら使う", case_mod.belongs_to(case0, "W"))
check("違う世界なら使わない", not case_mod.belongs_to(case0, "別世界"))
check("壊れた控えは空として読む",
      case_mod.load(os.path.join(OUT_DIR, "no_such_file.json"))["stage"]
      == case_mod.NONE)

# ================================================================== 設置と判定
print("\n[設置] 施設と段階でボタンが出し分かる")
ctx = FakeCtx()
clean_record(ctx)
mod.apply(ctx)
app, places = fresh_world()
#: 事件が始まる前から居た NPC。**この人たちは誰も退場してはいけない。**
existing_ids = sorted(app.world.characters)
refresh = install(ctx, app)

app.facility_screen()
refresh()
check("ギルドで「気になる噂を聞く」が出る",
      mod.START_LABEL in app.labels(), app.labels())
check("「出る」の手前に入る",
      app.labels().index(mod.START_LABEL) < app.labels().index(EXIT_TEXT),
      app.labels())

before = len(app.buttons)
refresh()
check("塗り直しても二重にならない", len(app.buttons) == before, app.labels())

app.go(places["inn"])
app.facility_screen()
refresh()
check("事件を受ける前は宿屋に何も出ない",
      not any(screen_label in mod.OUR_LABELS for screen_label in app.labels()),
      app.labels())

app.go(places["guild"])
app.facility_screen()
refresh()
press(ctx, app, mod.START_LABEL)
found = mod.__dict__ and case_mod.load(ctx.state_path(mod.RECORD_BASENAME))
check("押すと事件が始まる", found["stage"] == case_mod.INVESTIGATING, found)
check("犯人が容疑者に含まれている",
      found["culprit"] in case_mod.suspect_ids(found), found["suspects"])
check("**全部集めれば1人に絞れる**（詰んだ事件を出さない）",
      solvable_when_complete(found), found["clues"])
check("**施設の主を犯人にしない**",
      found["culprit"] not in ("10", "11", "12"), found["culprit"])
check("控えが書かれている",
      os.path.exists(ctx.state_path(mod.RECORD_BASENAME)))

app.facility_screen()
refresh()
check("事件中のギルドには噂のボタンを出さない",
      mod.START_LABEL not in app.labels(), app.labels())

app.go(places["inn"])
app.facility_screen()
refresh()
check("**聞き込みのボタンは出さない**（普通に話しかければよい）",
      "聞き込み" not in "".join(app.labels()), app.labels())

app.go(places["market"])
app.facility_screen()
refresh()
check("**どこから調べてもよい**（順番を強制しない）",
      case_mod.pending_at(case_mod.load(ctx.state_path(mod.RECORD_BASENAME)),
                          MARKET) is not None)

print("\n[案内] **次にどこへ行けばよいかを必ず示す**")
# 利用者の指摘: 行き先が分からないと無策で歩き回ることになる。
opened = [str(t) for t in app.texts]
inn_name = places["inn"].name
market_name = places["market"].name
check("事件を受けた直後に行き先が出る",
      any(inn_name in t or market_name in t for t in opened), opened)
check("**施設は種類ではなく名前で示す**",
      not any(t.strip() in (INN, MARKET, GUILD) for t in opened), opened)

app.go(places["guild"])
app.facility_screen()
refresh()
check("依頼元では進み具合を確かめられる",
      mod.STATUS_LABEL in app.labels(), app.labels())
app.texts[:] = []
press(ctx, app, mod.STATUS_LABEL)
status = [str(t) for t in app.texts]
check("いま何が分かっているかが出る",
      any(mod.STATUS_HEAD_TEXT in t for t in status), status)
check("次の行き先が出る",
      any(inn_name in t or market_name in t for t in status), status)

print("\n[判定] 押された飛び先ラベルだけで手がかりが立つ")
app.go(places["inn"])
app.facility_screen()
refresh()
check("**普通に話しかけるだけで成立する**（専用のボタンは無い）",
      not any("聞き込み" in str(label) for label in app.labels()),
      app.labels())

app.texts[:] = []
seen = talk_to(ctx, app, places["inn"].owner)
inn_fact = [c["fact"] for c in found["clues"] if c["at_type"] == INN][0]
check("**知っている人物には事実が渡る**",
      inn_fact in str(seen["args"][11]), seen["args"][11])
# **「尋ねられたら」にしない。**手がかりは「会話した」という行動で立つので、
# 条件付きの指示だと、尋ねなかったときに何も言われないまま手がかりだけ出る
# （利用者の指摘）。
check("**必ず自分から言う指示になっている**（条件付きにしない）",
      "必ず" in str(seen["args"][11])
      and "尋ねられたら" not in str(seen["args"][11]),
      seen["args"][11])
check("元から在った知識を消さない", "既存の知識" in str(seen["args"][11]),
      seen["args"][11])
end_conversation(ctx, app)
found = case_mod.load(ctx.state_path(mod.RECORD_BASENAME))
check("**c1 が立った**",
      case_mod.clue_by_id(found, "c1")["found"], found["clues"])
gained = [str(t) for t in app.texts]
check("画面に分かったことが出た",
      any("分かったこと" in t for t in gained), gained)
check("**手がかりを得た直後に次の行き先が出る**",
      any(places["market"].name in t for t in gained), gained)

# **知らない印のボタンは素通しする。**ゲームや他の MOD のボタンを
# 横取りしないための最低条件。
passed_through = {"n": 0}


def orig_press(self, index, *a, **k):
    passed_through["n"] += 1
    return "ゲームが処理した"


press_hook = ctx.hooks["__main__:InstantaleApp.on_button_press"]
app.buttons.append({"text": "ゲームのボタン", "spec": None})
press_hook(orig_press, app, len(app.buttons) - 1)
check("知らないボタンは素通しする", passed_through["n"] == 1, passed_through)
found = case_mod.load(ctx.state_path(mod.RECORD_BASENAME))
check("知らないボタンでは手がかりが増えない",
      case_mod.found_count(found) == 1, found["clues"])
app.buttons.pop()
for target in ("scripts.llm.llm_manager:conversation_starter",
               "scripts.llm.llm_manager:conversation_facilitator",
               "scripts.llm.llm_manager:conversation_facilitator_after_retrieval"):
    check("会話に知識を差し込む口がある: {}".format(target.rsplit(":", 1)[-1]),
          target in ctx.hooks, sorted(ctx.hooks))

print("\n[会話] **知っていることを NPC に喋らせる**")
# 聞き込みの最中だけ、ゲーム自身の `retrieved_knowledge` に事実を足す。
# 証言者を id で見分けるので、**キーワードで渡されても効く。**
app.go(places["market"])
app.facility_screen()
refresh()
facilitator = ctx.hooks["scripts.llm.llm_manager:conversation_facilitator"]
seen = {}


def fake_facilitator(*args, **kwargs):
    seen["args"], seen["kwargs"] = args, kwargs
    return None


facilitator(fake_facilitator, "m", "log", "p",
            app.world.characters[places["market"].owner],
            "w", "s", "r", "a", [], [], "instr", retrieved_knowledge="")
check("キーワードで渡されても事実が載る",
      "必ず話すこと" in str(seen["kwargs"].get("retrieved_knowledge")),
      seen["kwargs"])

app.go(places["market"])
app.facility_screen()
refresh()
check("c1 を得ても闇市の手がかりは残っている",
      case_mod.pending_at(case_mod.load(ctx.state_path(mod.RECORD_BASENAME)),
                          MARKET) is not None)
talk_to(ctx, app, places["market"].owner)
end_conversation(ctx, app)
found = case_mod.load(ctx.state_path(mod.RECORD_BASENAME))
check("**集めるほど容疑者が減る**",
      len(case_mod.remaining(found)) < len(found["suspects"]),
      case_mod.remaining(found))

print("\n[判定] 告発（**自前のボタンだけで組む**。ゲームの場面を使わない）")
app.go(places["guild"])
app.facility_screen()
refresh()
check("ギルドで「犯人を告発する」が出る",
      mod.ACCUSE_LABEL in app.labels(), app.labels())

# 押すと容疑者が並ぶ。以前はここでゲームの自由施設に場面を組ませていたが、
# `scripts.free_facility` がまだ import されていないと「調べていることは無い」
# と出て詰む作りだった（利用者の指摘）。
press(ctx, app, mod.ACCUSE_LABEL)
refresh()
one = [t for t in app.labels() if str(t).startswith(mod.ACCUSE_ONE_PREFIX)]
check("**容疑者が全員ボタンとして並ぶ**",
      len(one) == len(case_mod.suspect_ids(found)), app.labels())
check("**特徴が添えてある**（集めた事実と突き合わせるため）",
      all("（" in str(t) and "）" in str(t) for t in one), one)
check("やめるボタンがある", mod.LEAVE_LABEL in app.labels(), app.labels())
check("諦めるボタンがある", mod.GIVE_UP_LABEL in app.labels(), app.labels())
check("容疑者を並べている間は「犯人を告発する」を重ねて出さない",
      mod.ACCUSE_LABEL not in app.labels(), app.labels())
# **告発は専用の画面にする。**施設の選択肢に混ぜると、誰を告発するかを
# 選ぶ場面なのに「出る」や「会話する」が並んで、何をする画面なのか分から
# なくなる（利用者の指摘）。
check("**ゲームの選択肢は出さない（この画面はこちらのものだけ）**",
      all(str(t).startswith(mod.ACCUSE_ONE_PREFIX)
          or t in (mod.LEAVE_LABEL, mod.GIVE_UP_LABEL)
          for t in app.labels()),
      app.labels())
check("並ぶのは容疑者＋やめる＋諦めるの数だけ",
      len(app.labels()) == len(case_mod.suspect_ids(found)) + 2, app.labels())

# やめれば元の画面に戻れる（**選び直せないのは決着だけ**）。
press(ctx, app, mod.LEAVE_LABEL)
refresh()
check("やめると元のボタンに戻る",
      mod.ACCUSE_LABEL in app.labels()
      and not [t for t in app.labels()
               if str(t).startswith(mod.ACCUSE_ONE_PREFIX)],
      app.labels())
check("**預かったゲームの選択肢も戻る**",
      EXIT_TEXT in app.labels(), app.labels())
still = case_mod.load(ctx.state_path(mod.RECORD_BASENAME))
check("やめても事件は続いている",
      case_mod.is_active(still), still["stage"])
check("手がかりも消えていない",
      case_mod.total_count(still) == case_mod.total_count(found),
      still["clues"])

culprit = found["culprit"]
cast_ids = list(case_mod.suspect_ids(found))
gold_before = app.player.gold
accuse(ctx, app, culprit)
found = case_mod.load(ctx.state_path(mod.RECORD_BASENAME))
check("決着した", found["stage"] == case_mod.CLOSED, found["stage"])
# **決着したらキャストは世界から消える**（印を立てて残すのではなく）。
# 印だけだと繰り返し遊ぶぶんセーブに溜まり続ける（利用者の指摘）。
check("**犯人が世界から消えた**", culprit not in app.world.characters,
      sorted(app.world.characters))
check("**無実の容疑者も残らない**",
      not [i for i in cast_ids if i in app.world.characters],
      [i for i in cast_ids if i in app.world.characters])
check("**セーブの npcs からも消えた**",
      not [i for i in cast_ids if i in app.world_dict["npcs"]],
      sorted(app.world_dict["npcs"]))
check("**施設の名簿からも消えた**",
      not [i for i in cast_ids
           for node in app.world.areas["0"].nodes.values()
           for f in node.facilities.values() if i in f.characters],
      [(f.id, f.characters) for node in app.world.areas["0"].nodes.values()
       for f in node.facilities.values()])
check("台帳が空になった",
      not ledger_mod.ids(ledger_mod.load(ctx.state_path(mod.LEDGER_BASENAME)),
                         world_mod.world_name(app)),
      ledger_mod.load(ctx.state_path(mod.LEDGER_BASENAME)))
check("報酬が入った", app.player.gold == gold_before + mod.REWARD_GOLD,
      (gold_before, app.player.gold))
check("**元から居た NPC は誰も消えていない／退場していない**",
      all(cid in app.world.characters
          and not app.world.characters[cid].config["is_dead"]
          for cid in existing_ids),
      [cid for cid in existing_ids if cid not in app.world.characters
       or app.world.characters[cid].config["is_dead"]])

print("\n[設置] **闇市の無い町でも通しで解ける**")
ctx3 = FakeCtx()
clean_record(ctx3)
mod.apply(ctx3)
guild_only = Facility("1", "ギルド", GUILD, owner="10",
                      characters=["10", "20", "21", "22"])
inn_only = Facility("2", "宿屋", INN, owner="11", characters=["11", "20"])
small_area = Area("0", "小さな町", [guild_only, inn_only])
small_world = World({"0": small_area},
                    {cid: Character(cid, "NPC" + cid)
                     for cid in ("10", "11", "20", "21", "22")})
small_app = InstantaleApp(small_world, Player(small_area, guild_only))
small_refresh = install(ctx3, small_app)

small_app.facility_screen()
small_refresh()
press(ctx3, small_app, mod.START_LABEL)
small_case = case_mod.load(ctx3.state_path(mod.RECORD_BASENAME))
check("事件が始まる", small_case["stage"] == case_mod.INVESTIGATING, small_case)
check("手がかりが在る施設だけに置かれた",
      all(c["at_type"] in (GUILD, INN) for c in small_case["clues"]),
      [c["at_type"] for c in small_case["clues"]])

for clue in list(small_case["clues"]):
    target = inn_only if clue["at_type"] == INN else guild_only
    small_app.go(target)
    small_app.facility_screen()
    small_refresh()
    talk_to(ctx3, small_app, target.owner)
    end_conversation(ctx3, small_app)

small_case = case_mod.load(ctx3.state_path(mod.RECORD_BASENAME))
check("**集めた分だけ絞れる**", case_mod.found_count(small_case) > 0,
      small_case["clues"])
small_app.go(guild_only)
small_app.facility_screen()
small_refresh()
check("告発できる", mod.ACCUSE_LABEL in small_app.labels(), small_app.labels())
accuse(ctx3, small_app, small_case["culprit"])
small_case = case_mod.load(ctx3.state_path(mod.RECORD_BASENAME))
check("**闇市が無くても解決まで通る**",
      small_case["stage"] == case_mod.CLOSED, small_case["stage"])

print("\n[キャスト] **既存 NPC を犯人にしない**（既定＝生成）")
check("容疑者の数だけ下地が用意されている",
      len(CAST_POOL) >= mod.SUSPECT_COUNT,
      (len(CAST_POOL), mod.SUSPECT_COUNT))
check("**下地に特徴を焼き付けていない**（事件ごとに割り当てるため）",
      all("traits" not in base and "tell" not in base
          for base in CAST_POOL),
      [sorted(base) for base in CAST_POOL][:1])
check("名前にファイル名で使えない文字が無い（`110_` の対象）",
      all(not set(spec["name"]) & set('"<>:|?*/\\')
          for spec in CAST_POOL),
      [spec["name"] for spec in CAST_POOL])

ctxg = FakeCtx()
clean_record(ctxg)
mod.apply(ctxg)
del GENERATED[:]        # ここまでの節の生成物は数えない
appg, placesg = fresh_world()
existing = set(appg.world.characters)
existing_ids = sorted(existing)
refreshg = install(ctxg, appg)
appg.facility_screen()
refreshg()
press(ctxg, appg, mod.START_LABEL)
caseg = case_mod.load(ctxg.state_path(mod.RECORD_BASENAME))
check("事件が始まる", caseg["stage"] == case_mod.INVESTIGATING, caseg["stage"])
check("**犯人は生成された人物**", caseg["culprit"] not in existing,
      (caseg["culprit"], sorted(existing)))
check("**容疑者に既存 NPC が1人も居ない**",
      not (set(case_mod.suspect_ids(caseg)) & existing),
      set(case_mod.suspect_ids(caseg)) & existing)
check("生成された人数が容疑者の数と合う",
      len(GENERATED) == mod.SUSPECT_COUNT, len(GENERATED))
check("**セーブに書いてから generate_character を呼んでいる**",
      len(appg.world.generated) == mod.SUSPECT_COUNT,
      len(appg.world.generated))
check("**セーブ（world_dict['npcs']）に登録された**",
      len(appg.world_dict["npcs"]) == mod.SUSPECT_COUNT,
      sorted(appg.world_dict["npcs"]))
check("空いている id が使われた",
      not (set(appg.world_dict["npcs"]) & existing),
      sorted(appg.world_dict["npcs"]))
placed = {facility_id for _npc_id, facility_id in appg.moved}
check("**全員を同じ場所に立たせない**", len(placed) > 1, appg.moved)
check("**施設に配置している**（move_npc_to_facility を通す）",
      len(appg.moved) == mod.SUSPECT_COUNT, appg.moved)
check("生成物は生きている",
      all(not data["config"]["is_dead"] for data in GENERATED))
check("**空いている id を使う**（ゲームの採番と衝突させない）",
      all(data["id"] not in existing for data in GENERATED),
      [data.get("id") for data in GENERATED])
# 実測: `generate_npc` はセーブに入る名前とは違う呼び方で
# いくつかの項目を読む。埋めないと説明も立ち絵も空になる。
check("**立ち絵の元（look）を持たせている**",
      all(data.get("look") for data in GENERATED),
      [data.get("look") for data in GENERATED])
check("**推理用の項目はセーブに書かない**（traits / tell は MOD の都合）",
      all("traits" not in data and "tell" not in data for data in GENERATED),
      [sorted(data) for data in GENERATED][:1])
# 実測: 戻り値は `world_dict` そのもの。id は返らないので名前で引き当てる。
check("**戻り値から id を取ろうとしない**（名前で引き当てる）",
      all(str(npc_id) in appg.world_dict["npcs"]
          for npc_id in case_mod.suspect_ids(caseg)),
      (case_mod.suspect_ids(caseg), sorted(appg.world_dict["npcs"])))
check("**容疑者の id が全員違う**（実機で全員 47 になった）",
      len(set(case_mod.suspect_ids(caseg))) == len(case_mod.suspect_ids(caseg)),
      case_mod.suspect_ids(caseg))
check("控えの犯人が、生成した人物のいずれかである",
      appg.world_dict["npcs"][caseg["culprit"]]["name"]
      in [base["name"] for base in CAST_POOL],
      appg.world_dict["npcs"][caseg["culprit"]]["name"])
check("施設の名簿に載った",
      all(npc_id in sum((f.characters for node in appg.world.areas["0"].nodes.values()
                         for f in node.facilities.values()), [])
          for npc_id in appg.world_dict["npcs"]),
      sorted(appg.world_dict["npcs"]))

print("\n[キャスト] **セーブの項目の並び順を崩さない**")
# セーブは辞書をそのまま JSON に落とすので、**書いた順がそのままファイルの
# 行順になる。**セーブを読む側には項目を上から順に並べて見せる道具（別途ある
# セーブエディタ）があり、順番が変わると項目は全部揃っているのに表示が崩れる。
#
# 下の並びは**実際のセーブから起こしたもの**で、MOD 側の定義は見ていない
# （見てしまうと「自分と同じか」を確かめるだけの検査になる）:
#
#     saves/<世界名>/savedata_plain.json の npcs
#     51体中50体がこの33項目・この順（残る1体は speech_style が無いだけ）
SAVE_NPC_FIELDS = (
    "name", "id", "category", "profile", "personality", "look_description",
    "speech_style", "job", "state", "ability_scores", "experience_level",
    "experience_point", "original_max_hp", "max_hp", "current_hp", "age",
    "skills", "equipments", "weakness", "location", "inventory", "image_src",
    "look", "memory", "life_log", "current_log", "relationship",
    "initial_location", "config", "current_area", "current_location",
    "knowledge", "display_position_in_battle",
)

check("下地が33項目を漏らさず持っている（1つでも欠けると末尾に足されて崩れる）",
      set(world_mod.NEW_NPC_TEMPLATE) == set(SAVE_NPC_FIELDS),
      (sorted(set(SAVE_NPC_FIELDS) - set(world_mod.NEW_NPC_TEMPLATE)),
       sorted(set(world_mod.NEW_NPC_TEMPLATE) - set(SAVE_NPC_FIELDS))))
check("**下地の並びがゲームのセーブと1つずつ一致する**",
      tuple(world_mod.NEW_NPC_TEMPLATE) == SAVE_NPC_FIELDS,
      [(i, a, b) for i, (a, b) in
       enumerate(zip(SAVE_NPC_FIELDS, tuple(world_mod.NEW_NPC_TEMPLATE)))
       if a != b])
check("**作った NPC もその並びで書かれている**",
      all(tuple(data) == SAVE_NPC_FIELDS for data in GENERATED),
      [[(i, a, b) for i, (a, b) in enumerate(zip(SAVE_NPC_FIELDS, tuple(data)))
        if a != b] for data in GENERATED][:1])
# `knowledge` は実際のセーブでは `[]`。`{}` だと型が違う。
check("knowledge はリスト（辞書ではない）",
      all(isinstance(data.get("knowledge"), list) for data in GENERATED),
      [type(data.get("knowledge")).__name__ for data in GENERATED][:1])
# 並びを保っているのは「テンプレートが全項目を持っているから」であって、
# `update` が賢いからではない。1つ抜いたら本当に崩れることを確かめておく。
short = dict(world_mod.NEW_NPC_TEMPLATE)
del short["name"]
short.update({"name": "後から足した"})
check("再現: 下地に無い項目は末尾へ回る（だから漏らせない）",
      tuple(short)[-1] == "name", tuple(short)[-3:])

print("\n[キャスト] **採番が進まない実装でも、同じ人物を並べない**")
# 実機で `generate_npc` が3回とも 47 を返し、容疑者が全員同じ人物になった。
# 気付かずに進めると選択肢が全部同じ文字列になる。
COLLIDING_IDS["on"] = True
ctxc = FakeCtx()
clean_record(ctxc)
mod.apply(ctxc)
appc, placesc = fresh_world()
refreshc = install(ctxc, appc)
appc.facility_screen()
refreshc()
press(ctxc, appc, mod.START_LABEL)
casec = case_mod.load(ctxc.state_path(mod.RECORD_BASENAME))
if casec.get("suspects"):
    ids = case_mod.suspect_ids(casec)
    check("**容疑者が重複しない**", len(set(ids)) == len(ids), ids)
else:
    check("**容疑者を作れないなら事件を始めない**",
          casec["stage"] == case_mod.NONE, casec["stage"])
COLLIDING_IDS["on"] = False

print("\n[キャスト] 生成できなければ事件を始めない")
GENERATE_FAILS["on"] = True
ctxb = FakeCtx()
clean_record(ctxb)
mod.apply(ctxb)
gb = Facility("1", "ギルド", GUILD, owner="10", characters=["10"])
ib = Facility("2", "宿屋", INN, owner="11", characters=["11"])
ab = Area("0", "町", [gb, ib])
wb = World({"0": ab}, {"10": Character("10", "主1"),
                       "11": Character("11", "主2"),
                       "20": Character("20", "既存の人物")})
appb = InstantaleApp(wb, Player(ab, gb))
refreshb = install(ctxb, appb)
appb.facility_screen()
refreshb()
press(ctxb, appb, mod.START_LABEL)
caseb = case_mod.load(ctxb.state_path(mod.RECORD_BASENAME))
check("**生成に失敗したら事件を始めない**",
      caseb["stage"] == case_mod.NONE, caseb["stage"])
check("既存 NPC に落ちない（愛着のある相手を犯人にしない）",
      caseb.get("culprit") is None, caseb.get("culprit"))
GENERATE_FAILS["on"] = False

print("\n[設置] **群衆を犯人にしない**（GENERATE_CAST を切ったとき）")
ctx4 = FakeCtx()
clean_record(ctx4)
mod.apply(ctx4)
g4 = Facility("1", "ギルド", GUILD, owner="10", characters=["10", "33", "20"])
i4 = Facility("2", "宿屋", INN, owner="11", characters=["11", "33", "20"])
a4 = Area("0", "群衆の町", [g4, i4])
w4 = World({"0": a4}, {"10": Character("10", "主1"), "11": Character("11", "主2"),
                       "20": Character("20", "まともな人物"),
                       "33": crowd("33")})
app4 = InstantaleApp(w4, Player(a4, g4))
before4 = sorted(w4.characters)        # 事件が始まる前から居た者
refresh4 = install(ctx4, app4)
app4.facility_screen()
refresh4()
press(ctx4, app4, mod.START_LABEL)
case4 = case_mod.load(ctx4.state_path(mod.RECORD_BASENAME))
# キャストは必ず生成するので、**元から居た者は誰も巻き込まれない。**
# 群衆（`混乱する村人たち`）が犯人に選ばれた件は、既存 NPC から選ぶ設計を
# やめたことで根本から無くなった（利用者の指摘）。
check("**群衆は犯人にならない**", case4.get("culprit") != "33", case4.get("culprit"))
check("群衆は容疑者にも並ばない", "33" not in case_mod.suspect_ids(case4),
      case4.get("suspects"))
check("**元から居た NPC は1人も容疑者にならない**",
      not (set(case_mod.suspect_ids(case4)) & set(before4)),
      (case_mod.suspect_ids(case4), before4))

print("\n[深化] **事件ごとに顔ぶれと特徴が変わる**")
# 下地に特徴を焼き付けていないので、同じ人物でも回によって片腕だったり
# 大柄だったりする。2件目が同じ事件にならないための作り。
layouts = set()
tells = set()
for _round in range(12):
    ctxv = FakeCtx()
    clean_record(ctxv)
    mod.apply(ctxv)
    appv, _placesv = fresh_world()
    refreshv = install(ctxv, appv)
    appv.facility_screen()
    refreshv()
    press(ctxv, appv, mod.START_LABEL)
    casev = case_mod.load(ctxv.state_path(mod.RECORD_BASENAME))
    if casev.get("stage") != case_mod.INVESTIGATING:
        continue
    names = tuple(appv.world_dict["npcs"][i]["name"]
                  for i in case_mod.suspect_ids(casev))
    layouts.add(names)
    tells.update(s["tell"] for s in casev["suspects"])
check("**顔ぶれが毎回同じにならない**", len(layouts) > 1, layouts)
check("**特徴の組み合わせも変わる**", len(tells) > 2, sorted(tells))
check("特徴は必ず言葉になる", all(t for t in tells), tells)

print("\n[深化] **どの手がかりも抜けない**（無駄足になる手がかりを出さない）")
# 「全部集めれば絞れる」だけを条件にしていた頃は、**同じ働きしかしない
# 手がかり**が並んだ。実機で出た事件:
#
#     c1 犯人の髪は黒かった -> 78, 79 が消える
#     c2 犯人は女だった     -> 78, 79 が消える  ← c1 と全く同じ
#     c3 ハルカのアリバイ   -> 77 が消える
#
# 集めれば犯人に決まるので当時の条件は全部通る。それでも c1 と c2 は
# どちらか一方で足り、もう片方のために町を横断するぶんは丸ごと無駄足。


def without(case, skipped):
    """1本だけ抜いて残り全部を集めた状態で、絞れているか。"""
    kept = [dict(c, found=(c["id"] != skipped)) for c in case["clues"]]
    return case_mod.solvable(dict(case, clues=kept))


def redundant_pairs(case):
    """同じ容疑者しか消さない手がかりの組。"""
    marks = [(c["id"], frozenset(str(i) for i in c["eliminates"]))
             for c in case["clues"]]
    return [(a[0], b[0]) for i, a in enumerate(marks)
            for b in marks[i + 1:] if a[1] == b[1]]


rounds, thin, waste, dup = 0, [], [], []
mistaken, muddled = 0, []
for _round in range(30):
    ctxn = FakeCtx()
    clean_record(ctxn)
    mod.apply(ctxn)
    appn, _placesn = fresh_world()
    refreshn = install(ctxn, appn)
    appn.facility_screen()
    refreshn()
    press(ctxn, appn, mod.START_LABEL)
    casen = case_mod.load(ctxn.state_path(mod.RECORD_BASENAME))
    if casen.get("stage") != case_mod.INVESTIGATING:
        continue
    rounds += 1
    if len(casen["clues"]) < 2:
        thin.append(len(casen["clues"]))
    dup.extend(redundant_pairs(casen))
    # **思い違いが混じる事件では話が変わる。**嘘を1本外せば解けるのが正しい
    # 挙動なので、「どれも抜けない」は真の話だけの事件に対して見る。
    droppable = [clue["id"] for clue in casen["clues"]
                 if without(casen, clue["id"])]
    if has_mistake(casen):
        mistaken += 1
        wrong_ids = [c["id"] for c in casen["clues"] if c.get("wrong")]
        # 外して1人残るのは**嘘の1本だけ**でなければならない。
        if droppable != wrong_ids or len(wrong_ids) != 1:
            muddled.append((droppable, wrong_ids,
                            [(c["id"], c["fact"]) for c in casen["clues"]]))
    else:
        for clue_id in droppable:
            waste.append((clue_id, case_mod.clue_by_id(casen, clue_id)["fact"]))

check("事件が組める（30回まわして毎回）", rounds == 30, rounds)
check("**同じ働きしかしない手がかりが並ばない**", not dup, dup[:4])
check("**1本でも欠けたら絞れない**（真の話だけの事件）", not waste, waste[:4])
check("思い違いの事件も出る", mistaken > 0, (mistaken, rounds))
check("**思い違いは1本に決まる**（外し方が一意）", not muddled, muddled[:1])
check("手がかりが1本だけの事件を出さない", not thin, thin)

# 上の失敗を**再現する形**で持っておく。実機で出た配り方をそのまま組むと、
# 判定が「重複あり」「抜いても解ける」を確かに掴まえること。
broken = case_mod.build("W", "0", "76",
                        [{"id": i, "tell": "", "claim": ""}
                         for i in ("76", "77", "78", "79")],
                        [{"id": "c1", "label": "clue_c1", "at_type": INN,
                          "kind": "trait", "fact": "犯人の髪は黒かった",
                          "eliminates": ["78", "79"]},
                         {"id": "c2", "label": "clue_c2", "at_type": INN,
                          "kind": "trait", "fact": "犯人は女だった",
                          "eliminates": ["78", "79"]},
                         {"id": "c3", "label": "clue_c3", "at_type": INN,
                          "kind": "alibi", "fact": "ハルカのアリバイ",
                          "eliminates": ["77"]}], 500)
check("再現: 実機で出た事件は「集めれば解ける」を満たしている",
      solvable_when_complete(broken), broken["clues"])
check("再現: それでも重複を掴まえる",
      redundant_pairs(broken) == [("c1", "c2")], redundant_pairs(broken))
check("再現: c1 を抜いても解けてしまうことを掴まえる",
      without(broken, "c1") and without(broken, "c2"), broken["clues"])
check("再現: 裏取りの c3 は抜けない", not without(broken, "c3"), broken["clues"])

print("\n[深化] **容疑者が自分の言い分を語る**")
ctxs = FakeCtx()
clean_record(ctxs)
mod.apply(ctxs)
apps, placess = fresh_world()
refreshs = install(ctxs, apps)
apps.facility_screen()
refreshs()
press(ctxs, apps, mod.START_LABEL)
cases = case_mod.load(ctxs.state_path(mod.RECORD_BASENAME))
check("全員に言い分がある",
      all(s.get("claim") for s in cases["suspects"]), cases["suspects"])
claims = [s["claim"] for s in cases["suspects"]]
check("言い分が重複しない", len(set(claims)) == len(claims), claims)
culprit_claim = case_mod.suspect_by_id(cases, cases["culprit"])["claim"]
check("**犯人の言い分だけ裏が取れない場所**",
      culprit_claim in BOOK["whereabouts"]["alone"], culprit_claim)
check("無実の者は人前に居たと言う",
      all(s["claim"] in BOOK["whereabouts"]["public"]
          for s in cases["suspects"] if s["id"] != cases["culprit"]),
      claims)

apps.texts[:] = []
seen_s = talk_to(ctxs, apps, cases["culprit"])
check("**容疑者に話しかけると立場を語る**",
      "この人物の立場" in str(seen_s["args"][11]), seen_s["args"][11])
check("言い分がそのまま渡る", culprit_claim in str(seen_s["args"][11]),
      seen_s["args"][11])
end_conversation(ctxs, apps)
after_s = case_mod.load(ctxs.state_path(mod.RECORD_BASENAME))
check("**容疑者との会話では手がかりが立たない**（言い分は事実ではない）",
      case_mod.found_count(after_s) == 0, after_s["clues"])
check("聞いた言い分は控えに残る",
      case_mod.suspect_by_id(after_s, cases["culprit"]).get("heard") is True,
      after_s["suspects"])
check("画面にも出る",
      any("言い分" in str(t) for t in apps.texts), apps.texts)

print("\n[控え] **古い控えに証言者を埋める**")
# 控えは注入をまたいで残るので、進行中の事件は古い版が作ったものでありうる。
# 証言者（`witness`）を後から足したとき、進行中の事件は `None` のままで
# 照合が永久に外れた（実機）。捨てずに埋めて続けられるようにする。
ctx6 = FakeCtx()
old_case = case_mod.build(
    "テスト世界", "0", "20", [{"id": "20", "tell": "t"}],
    [{"id": "c1", "at_type": INN, "label": "clue_c1", "intro": "", "ask": "",
      "prompt": "", "kind": "trait", "fact": "犯人は片腕だった",
      "eliminates": []}], 500)
check("**証言者を持たない控えが在りうる**",
      not old_case["clues"][0].get("witness"), old_case["clues"][0])
case_mod.save(ctx6.state_path(mod.RECORD_BASENAME), old_case)
mod.apply(ctx6)
app6, places6 = fresh_world()
refresh6 = install(ctx6, app6)
app6.facility_screen()
refresh6()
filled = case_mod.load(ctx6.state_path(mod.RECORD_BASENAME))
check("読んだ時点で埋まる",
      filled["clues"][0].get("witness") == places6["inn"].owner,
      filled["clues"][0].get("witness"))
seen6 = talk_to(ctx6, app6, places6["inn"].owner)
check("**埋めた後は知識が渡る**",
      "必ず話すこと" in str(seen6["args"][11]), seen6["args"][11])

print("\n[控え] **解けない事件は捨てる**")
# 古い版が組んだ控え（闇市が無い町なのに闇市を指している）を置いて読ませる。
ctx5 = FakeCtx()
stale = case_mod.build("テスト世界", "0", "20", [{"id": "20", "tell": "t"}],
                       [{"id": "c1", "at_type": MARKET, "label": "clue_c1",
                         "intro": "", "ask": "", "prompt": "", "kind": "trait",
                         "fact": "", "eliminates": []}], 500)
case_mod.save(ctx5.state_path(mod.RECORD_BASENAME), stale)
mod.apply(ctx5)
app5, places5 = fresh_world()
# 闇市を持たない町にする。
app5.world.areas["0"].nodes["0"].facilities.pop("3")
refresh5 = install(ctx5, app5)
app5.facility_screen()
refresh5()
after = case_mod.load(ctx5.state_path(mod.RECORD_BASENAME))
check("拾えない手がかりを抱えた控えは捨てられる",
      after["stage"] == case_mod.NONE, after["stage"])
check("捨てた後は新しい事件を受けられる",
      mod.START_LABEL in app5.labels(), app5.labels())

print("\n[材料] **事件のパターンを JSON から引いて組み合わせる**")
# 材料がコードに焼き付いていると、何度遊んでも同じ形の事件になる。
# `patterns/*.json` に出して、**1件ごとに抽選して組み合わせる**（利用者の
# 要望）。ファイルを足せば幅が増え、コードは触らなくてよい。
check("同梱の JSON が全部読める",
      all(BOOK.get(key) for key in ("incidents", "axes", "whereabouts", "cast")),
      {k: bool(BOOK.get(k)) for k in ("incidents", "axes", "whereabouts", "cast")})
check("**事件の題材が複数ある**", len(BOOK["incidents"]) >= 4,
      [i["noun"] for i in BOOK["incidents"]])
check("**特徴の軸が複数ある**（引き合わせで形が変わる）",
      len(BOOK["axes"]) >= 4, [a["id"] for a in BOOK["axes"]])
check("下地の人物が容疑者の数より多い",
      len(BOOK["cast"]) > mod.SUSPECT_COUNT, len(BOOK["cast"]))

# --- 材料そのものの筋が通っているか ---
for axis in BOOK["axes"]:
    check("軸 {} に値が2つ以上ある".format(axis["id"]),
          len(axis["values"]) >= 2, axis)
    for value in axis["values"]:
        check("  {}.{} に語・文・言い換えが揃っている".format(axis["id"], value["id"]),
              value["word"] and value["fact"] and value["hints"], value)
# **言い換えの語が軸をまたいで衝突していないか。**衝突していると、その語を
# 使った正しい文が「他の特徴に触れた」と誤判定されて捨てられる。
seen_hints = {}
clashes = []
for axis in BOOK["axes"]:
    for value in axis["values"]:
        for hint in value["hints"]:
            for other, owner in seen_hints.items():
                if owner == axis["id"]:
                    continue
                if hint in other or other in hint:
                    clashes.append((axis["id"], hint, owner, other))
            seen_hints[hint] = axis["id"]
check("**言い換えの語が軸をまたいで衝突しない**", not clashes, clashes[:4])

# 犯人だけが言う場所に、人目のある場所が混ざっていないか。
alone = BOOK["whereabouts"]["alone"]
public = BOOK["whereabouts"]["public"]
check("**裏が取れない場所と取れる場所が混ざっていない**",
      not (set(alone) & set(public)), sorted(set(alone) & set(public)))
check("犯人の言い分を配れるだけある", len(alone) >= 1 and len(public) >= mod.SUSPECT_COUNT,
      (len(alone), len(public)))
check("名前に記号を含む人物が居ない（立ち絵が作られなくなる）",
      not [p for p in BOOK["cast"] if set(p["name"]) & set('"<>:|?*/\\')],
      [p["name"] for p in BOOK["cast"]][:3])

# --- 抽選 ---
drawn = [tuple(sorted(a["id"] for a in patterns_mod.draw_axes(BOOK, random)))
         for _ in range(40)]
check("**引くたびに軸の組み合わせが変わる**", len(set(drawn)) > 1,
      sorted(set(drawn))[:4])
check("引く本数は決めた数まで",
      all(len(d) <= patterns_mod.AXES_PER_CASE for d in drawn),
      sorted({len(d) for d in drawn}))
nouns = {patterns_mod.draw_incident(BOOK, random)["noun"] for _ in range(40)}
check("**引くたびに題材も変わる**", len(nouns) > 1, sorted(nouns))

# --- 壊れていても止まらない ---
# **受け皿に落ちたときこそ確かめる。**実機で `KeyError: 'short'` を踏んだ。
# 既定値を素通しで使っていたので、読み方の側にしか無い項目が
# 抜けていた。しかも普段はファイルが読めるので、**受け皿の道だけが壊れて
# いる**という見つけにくい形だった。
fallback = patterns_mod.load(os.path.join(OUT_DIR, "no_such_dir"),
                             defaults=mod.BUILT_IN)
check("ファイルが無ければ同梱に戻る", fallback["axes"], fallback.get("axes"))
check("**受け皿も同じ形になっている**（読み方を通す）",
      all("short" in v and "hints" in v and "look" in v
          for a in fallback["axes"] for v in a["values"]),
      [v for a in fallback["axes"] for v in a["values"]][:1])
check("受け皿だけで表が組める（実機で落ちた経路）",
      len(patterns_mod.tables_for(fallback["axes"])) == 5,
      fallback["axes"][:1])
check("受け皿の言い分・題材・顔ぶれも揃う",
      fallback.get("whereabouts", {}).get("public")
      and fallback.get("incidents") and fallback.get("cast"),
      {k: bool(fallback.get(k)) for k in ("whereabouts", "incidents", "cast")})
# **`mod_dir` が None でも落ちない。**`ctx.mod_dir` は apply() の外で None に
# なる。控え忘れるとここに来る（実機で踏んだ）。
none_dir = patterns_mod.load(None, defaults=mod.BUILT_IN)
check("**mod_dir が None でも受け皿で組める**",
      len(patterns_mod.tables_for(none_dir["axes"])) == 5, none_dir["axes"][:1])

# --- **受け皿だけで通しで解けるか。** ---
# ここが今回の抜けだった。検証の `FakeCtx` は常に `mod_dir` を持っていたので、
# 受け皿の道が**一度も通しで走っていなかった**。実機は `mod_dir` が None に
# なってそこへ落ち、`KeyError: 'short'` で事件が始まらなかった。


class BareCtx(FakeCtx):
    """材料のファイルが1つも読めない環境（`mod_dir` が無い）。"""

    mod_dir = None


ctxz = BareCtx()
clean_record(ctxz)
mod.apply(ctxz)
appz, placesz = fresh_world()
refreshz = install(ctxz, appz)
appz.facility_screen()
refreshz()
press(ctxz, appz, mod.START_LABEL)
casez = case_mod.load(ctxz.state_path(mod.RECORD_BASENAME))
check("**材料が1つも読めなくても事件は始まる**",
      case_mod.is_active(casez), casez.get("stage"))
check("  そのときも全部集めれば絞れる",
      solvable_when_complete(casez), casez.get("clues"))
check("  特徴も出る", all(s["tell"] for s in casez.get("suspects", [])),
      casez.get("suspects"))
check("  フックの中で例外が出ていない", not ctxz.errors, ctxz.errors[:2])
check("値が1つの軸は使わない（誰も絞れない）",
      not patterns_mod.read_axes(
          {"axes": [{"id": "x", "values": [{"id": "a", "word": "あ",
                                            "fact": "い"}]}]}))
check("語や文の欠けた値は落とす",
      not patterns_mod.read_axes(
          {"axes": [{"id": "x", "values": [{"id": "a", "word": "あ"},
                                           {"id": "b", "fact": "い"}]}]}))
check("noun の無い題材は使わない",
      not patterns_mod.read_incidents({"incidents": [{"id": "x"}]}))
check("片方しか無い whereabouts は使わない",
      patterns_mod.read_whereabouts({"public": ["あ"]}) is None)
check("名前に記号のある人物は読み飛ばす",
      not patterns_mod.read_cast(
          {"cast": [{"name": 'あ"い', "sex": "man"}]}))
check("**壊れた JSON でも例外にしない**",
      isinstance(patterns_mod.read_axes({"axes": "こわれている"}), list))

print("\n[材料] **一覧に無意味な語を並べない**")
# 実機で「犯人の特徴が被りすぎ」（利用者の指摘）。
#
#   68 エレーナ  足を引きずる・低い声・煙のにおい・女   ← 犯人
#   69 リナ      足を引きずる・低い声・煙のにおい・女   ← 双子（設計どおり）
#   70 ゴードン  足を引きずる・甲高い声・煙のにおい・男
#   71 オーリン  足を引きずる・低い声・潮のにおい・男
#
# `足を引きずる` が4人とも同じ。**読む側には意味の無い1語**。
# 原因は本数。特徴で見分けるのは犯人以外の「人数-1」人で、手がかりは1本が
# 1人だけを消す形に落ち着くから、それより多く引いた軸は必ず余る。
# （当時は双子を置いていたので「人数-2」だった。双子は外した。）
check("**容疑者4人なら軸は3本**（1人1本、余らせない）",
      patterns_mod.axes_wanted(4) == 3, patterns_mod.axes_wanted(4))
check("3人なら2本", patterns_mod.axes_wanted(3) == 2, patterns_mod.axes_wanted(3))
check("2人なら1本", patterns_mod.axes_wanted(2) == 1, patterns_mod.axes_wanted(2))
check("多くても上限で頭打ち",
      patterns_mod.axes_wanted(12) == patterns_mod.AXES_PER_CASE,
      patterns_mod.axes_wanted(12))

# **一覧に「全員同じ語」が並ばないこと。**これが利用者から見た症状そのもの。
ctxv = FakeCtx()
clean_record(ctxv)
mod.apply(ctxv)
appv2, _pv2 = fresh_world()
refreshv2 = install(ctxv, appv2)
flat = []
for _round in range(12):
    appv2.facility_screen()
    refreshv2()
    if mod.START_LABEL not in appv2.labels():
        break
    press(ctxv, appv2, mod.START_LABEL)
    casev2 = case_mod.load(ctxv.state_path(mod.RECORD_BASENAME))
    if not case_mod.is_active(casev2):
        break
    tells = [s["tell"].split("・") for s in casev2["suspects"]]
    if len(tells) > 1:
        shared = set(tells[0])
        for row in tells[1:]:
            shared &= set(row)
        if shared:
            flat.append((sorted(shared), [s["tell"] for s in casev2["suspects"]]))
    # 次の事件を受けられるように畳む
    appv2.facility_screen()
    refreshv2()
    press(ctxv, appv2, mod.ACCUSE_LABEL)
    refreshv2()
    press(ctxv, appv2, mod.GIVE_UP_LABEL)
    refreshv2()

check("**全員に共通する語が一覧に出ない**（意味の無い語を並べない）",
      not flat, flat[:2])

print("\n[設置] **セーブをロードした直後でも出る**")
# 実機で「新しい世界では動くのに、既存のセーブをロードすると機能しない」
# （利用者の指摘）。
#
# `player.location` は施設のオブジェクトとは限らない。**セーブでは施設 id の
# 文字列**（'106'）で、遊んでいる最中にその施設へ入ると Facility に置き換わる。
# ロード直後は前者のままなので、施設の種類が引けずボタンが出なかった。
ctxl2 = FakeCtx()
clean_record(ctxl2)
mod.apply(ctxl2)
appl2, placesl2 = fresh_world()
refreshl2 = install(ctxl2, appl2)

# **ロード直後を再現する** ― 施設ではなく id の文字列を持たせる。
appl2.player.location = str(placesl2["guild"].id)
check("再現: そのままでは施設の種類が引けない形",
      isinstance(appl2.player.location, str), appl2.player.location)
check("**id の文字列でも施設の種類が引ける**",
      world_mod.facility_type(appl2) == GUILD,
      world_mod.facility_type(appl2))
appl2.facility_screen()
refreshl2()
check("**ロード直後でもギルドにボタンが出る**",
      mod.START_LABEL in appl2.labels(), appl2.labels())
press(ctxl2, appl2, mod.START_LABEL)
casel2 = case_mod.load(ctxl2.state_path(mod.RECORD_BASENAME))
check("そのまま事件を受けられる", case_mod.is_active(casel2), casel2.get("stage"))
check("事件の町も正しく引けている",
      casel2.get("area") == world_mod.area_id(appl2),
      (casel2.get("area"), world_mod.area_id(appl2)))

# 施設のオブジェクトを持っている普段の形でも、今までどおり動く。
appl2.go(placesl2["guild"])
check("オブジェクトを持っているときはそのまま",
      world_mod.facility_type(appl2) == GUILD,
      world_mod.facility_type(appl2))
# 引き当てられない id でも落ちない。
appl2.player.location = "存在しない施設"
check("引き当てられなくても落ちない",
      world_mod.facility_type(appl2) == "", world_mod.facility_type(appl2))

print("\n[案内] **容疑者がどこに居るかを一覧に出す**")
# 名前と特徴だけだと、聞き込みに回る前に町を端から探すことになる
# （利用者の指摘）。絞り込みは推理でも、**人を見つけるのは作業**。
ctxr2 = FakeCtx()
clean_record(ctxr2)
mod.apply(ctxr2)
appr2, placesr2 = fresh_world()
refreshr2 = install(ctxr2, appr2)
appr2.facility_screen()
refreshr2()
appr2.texts[:] = []
press(ctxr2, appr2, mod.START_LABEL)
caser2 = case_mod.load(ctxr2.state_path(mod.RECORD_BASENAME))
roster = [str(t) for t in appr2.texts if "・" in str(t) and "（" in str(t)]

check("容疑者の行が人数ぶん出る",
      len(roster) == len(case_mod.suspect_ids(caser2)), roster)
check("**どの行にも居場所が書いてある**",
      all("に居る" in line for line in roster), roster)
# **控えではなく今の名簿から引く**ので、実際に居る施設と一致する。
places_now = {}
for node in appr2.world.areas[caser2["area"]].nodes.values():
    for facility in node.facilities.values():
        for member in facility.characters:
            places_now[str(member)] = facility.name
check("**書いてある場所が実際に居る施設と一致する**",
      all(places_now.get(s["id"], "") and
          places_now[s["id"]] in line
          for s, line in zip(caser2["suspects"], roster)),
      [(s["id"], places_now.get(s["id"]), line)
       for s, line in zip(caser2["suspects"], roster)][:2])

# 動かされても今の場所が出る（控えを持たず、その都度探しているか）。
moved_id = case_mod.suspect_ids(caser2)[0]
for node in appr2.world.areas[caser2["area"]].nodes.values():
    for facility in node.facilities.values():
        if moved_id in facility.characters:
            facility.characters.remove(moved_id)
placesr2["market"].characters.append(moved_id)
appr2.texts[:] = []
appr2.facility_screen()
refreshr2()
press(ctxr2, appr2, mod.STATUS_LABEL)
moved_line = [str(t) for t in appr2.texts
              if mod.game.name_of(appr2, moved_id) in str(t) and "に居る" in str(t)]
check("**動かされたら新しい場所が出る**（控えを持たない）",
      moved_line and placesr2["market"].name in moved_line[0], moved_line)

# 名簿から見つからないときは、その1行だけ場所を出さない（落ちない）。
for node in appr2.world.areas[caser2["area"]].nodes.values():
    for facility in node.facilities.values():
        if moved_id in facility.characters:
            facility.characters.remove(moved_id)
check("居場所が分からなければ黙って省く",
      mod.game.facility_of(appr2, caser2["area"], moved_id) == "",
      mod.game.facility_of(appr2, caser2["area"], moved_id))

print("\n[設置] **旗が立ちっぱなしでも、画面が施設ならボタンを出す**")
# 実機で `in_shopping` が立ちっぱなしだった（セーブをロード後、ギルドに立って
# 施設の選択肢が出ているのに下りない）。旗だけを見ていると
# **その世界では二度とボタンが出ない**。
#
#   no button: busy: ['in_shopping'] (facility='guild' area='7')
#         'クエスト掲示板'  spec=DisplayQuestChoice
#         '出る'          spec=MovePhaseManager
#         '会話する'       spec=DisplayTalkChoice
ctxf = FakeCtx()
clean_record(ctxf)
mod.apply(ctxf)
appf, placesf = fresh_world()
refreshf = install(ctxf, appf)
appf.facility_screen()
refreshf()
check("前提: 普段はボタンが出る", mod.START_LABEL in appf.labels(), appf.labels())

appf.in_shopping = True                 # **下りない旗**を再現する
appf.facility_screen()
refreshf()
check("**旗が立っていても施設の画面なら出る**",
      mod.START_LABEL in appf.labels(), appf.labels())
press(ctxf, appf, mod.START_LABEL)
casef = case_mod.load(ctxf.state_path(mod.RECORD_BASENAME))
check("そのまま事件を受けられる", case_mod.is_active(casef), casef.get("stage"))

# **施設の画面でないときは今までどおり旗を尊重する。**
# 会話や戦闘の最中に割り込まないための歯止めなので、そこは緩めない。
appf.buttons = [{"text": "何か", "spec": None}]
before_len = len(appf.buttons)
refreshf()
check("施設の画面でなければ足さない", len(appf.buttons) == before_len,
      appf.labels())
appf.in_shopping = False

print("\n[事件] **思い違いの証言**")
# 真の事実だけだと、遊びは最後まで「当てはまらない者を消す」で終わる。
# 属性の語彙を増やしても操作は同じなので、そこが天井だった（利用者の指摘）。
# 1本を間違いにすると、**すべて信じたときに誰も残らない** ―
# そこで「どれが嘘か」を探すことになり、消し込みが推論に変わる。

# 犯人 '0'、他は3人。c1/c2 が真、c3 が思い違い（犯人まで消してしまう）。
liar = case_mod.build("W", "0", "0",
                      [{"id": str(i), "tell": "", "claim": ""} for i in range(4)],
                      [{"id": "c1", "label": "l1", "at_type": INN, "kind": "trait",
                        "fact": "犯人は小柄だった", "eliminates": ["1", "2"]},
                       {"id": "c2", "label": "l2", "at_type": INN, "kind": "trait",
                        "fact": "犯人は袋を提げていた", "eliminates": ["3"]},
                       {"id": "c3", "label": "l3", "at_type": INN, "kind": "trait",
                        "fact": "犯人の髪は黒かった", "wrong": True,
                        # 嘘は犯人を消す。ただし**真の話が単独で消す者を
                        # 巻き込むと一意に決まらなくなる**（c1 だけが消す
                        # '1'/'2' を残すと、c1 を外しても1人になってしまう）。
                        "eliminates": ["0", "3"]}], 500)
for clue in liar["clues"]:
    clue["found"] = True

check("**全部信じると誰も残らない**", not case_mod.remaining(liar),
      case_mod.remaining(liar))
check("食い違いとして立つ", case_mod.contradicted(liar))
left = case_mod.without_each(liar)
check("**嘘を外すと犯人1人が残る**", left["c3"] == ["0"], left)
check("真の話を外しても1人にはならない",
      all(len(rest) != 1 for key, rest in left.items() if key != "c3"), left)
check("**外し方は1つに決まる**",
      [k for k, rest in left.items() if len(rest) == 1] == ["c3"], left)
# 犯人を引く細工をしていたら、この食い違いは見えない。
check("犯人も除外の対象に数える（引く細工をしない）",
      "0" in case_mod.eliminated(liar), case_mod.eliminated(liar))

# 真の話だけの事件は今までどおり
plain2, _c, _f = fake_case()
for clue in plain2["clues"]:
    clue["found"] = True
check("真の話だけなら食い違わない", not case_mod.contradicted(plain2),
      case_mod.remaining(plain2))

# --- 実際に組まれる事件で確かめる ---
ctxm2 = FakeCtx()
clean_record(ctxm2)
mod.apply(ctxm2)
appm2, _pm2 = fresh_world()
refreshm2 = install(ctxm2, appm2)
mixed, bad2, rounds3, sample = 0, [], 0, None
for _round in range(30):
    appm2.facility_screen()
    refreshm2()
    if mod.START_LABEL not in appm2.labels():
        break
    press(ctxm2, appm2, mod.START_LABEL)
    casem2 = case_mod.load(ctxm2.state_path(mod.RECORD_BASENAME))
    if not case_mod.is_active(casem2):
        break
    rounds3 += 1
    if has_mistake(casem2):
        mixed += 1
        probe2 = json.loads(json.dumps(casem2))
        for clue in probe2["clues"]:
            clue["found"] = True
        if not case_mod.contradicted(probe2):
            bad2.append(("食い違わない", probe2["clues"]))
        else:
            left2 = case_mod.without_each(probe2)
            once = [k for k, rest in left2.items() if len(rest) == 1]
            wrong = [c["id"] for c in probe2["clues"] if c.get("wrong")]
            if once != wrong or left2[once[0]] != [probe2["culprit"]]:
                bad2.append((once, wrong, left2))
            elif sample is None:
                sample = probe2          # 画面の知らせを見るのに使う
    appm2.facility_screen()
    refreshm2()
    press(ctxm2, appm2, mod.ACCUSE_LABEL)
    refreshm2()
    press(ctxm2, appm2, mod.GIVE_UP_LABEL)
    refreshm2()

check("事件を続けて受けられる", rounds3 >= 10, rounds3)
check("**思い違いの事件が出る**", mixed > 0, (mixed, rounds3))
check("**どの事件も外し方が一意**（当てずっぽうにならない）", not bad2, bad2[:1])

# **食い違ったら画面で知らせる。**控えを置いてから読み直させる
# （`current` は控えをファイルから読むので、実機と同じ経路を通る）。
if sample is not None:
    ctxm3 = FakeCtx()
    case_mod.save(ctxm3.state_path(mod.RECORD_BASENAME), sample)
    mod.apply(ctxm3)
    appm3, _pm3 = fresh_world()
    refreshm3 = install(ctxm3, appm3)
    appm3.facility_screen()
    refreshm3()
    appm3.texts[:] = []
    press(ctxm3, appm3, mod.STATUS_LABEL)
    said = [str(t) for t in appm3.texts]
    check("**食い違いを画面で知らせる**",
          any(mod.CONTRADICTION_TEXT in t for t in said), said[-3:])
check("知らせる文に答えを書いていない",
      "思い違い" in mod.CONTRADICTION_TEXT
      and "c1" not in mod.CONTRADICTION_TEXT, mod.CONTRADICTION_TEXT)


print("\n[材料] **一覧だけで犯人が割れない**")
# 双子（犯人と特徴が完全に一致する者）を外したあと、代わりに**もっと悪い漏れ**が
# 出た。全員を「犯人と1軸だけ違う」形に置くと条件は綺麗に満たされるが、
# **犯人が「どの軸でも多数派」の唯一の1人**になる:
#
#   犯人   a1 b1 c1   ← a1 が3人、b1 が3人、c1 が3人
#   2人目  a2 b1 c1
#   3人目  a1 b2 c1
#   4人目  a1 b1 c2
#
# 一覧を眺めるだけで手がかり0本で当てられる。だから配り方を自由にして、
# **どの手がかりを使うかを後で選ぶ**（`choose_facts`）形にした。
ctxw2 = FakeCtx()
clean_record(ctxw2)
mod.apply(ctxw2)
appw3, _pw3 = fresh_world()
refreshw3 = install(ctxw2, appw3)
twins, obvious, thin, rounds2 = 0, 0, 0, 0
for _round in range(24):
    appw3.facility_screen()
    refreshw3()
    if mod.START_LABEL not in appw3.labels():
        break
    press(ctxw2, appw3, mod.START_LABEL)
    casew3 = case_mod.load(ctxw2.state_path(mod.RECORD_BASENAME))
    if not case_mod.is_active(casew3):
        break
    rounds2 += 1
    rows = [s["tell"].split("・") for s in casew3["suspects"]]
    ids = [s["id"] for s in casew3["suspects"]]
    if len(set(map(tuple, rows))) != len(rows):
        twins += 1
    if len(casew3["clues"]) < 2:
        thin += 1
    if len({len(r) for r in rows}) == 1:
        cols = list(zip(*rows))
        modal = [ids[i] for i in range(len(rows))
                 if all(sum(1 for v in col if v == col[i]) * 2 > len(col)
                        for col in cols)]
        if modal == [casew3["culprit"]]:
            obvious += 1
    appw3.facility_screen()
    refreshw3()
    press(ctxw2, appw3, mod.ACCUSE_LABEL)
    refreshw3()
    press(ctxw2, appw3, mod.GIVE_UP_LABEL)
    refreshw3()

check("事件を続けて受けられる", rounds2 >= 8, rounds2)
check("**特徴が完全に一致する者を置かない**（双子はやめた）", not twins,
      (twins, rounds2))
check("**「どの軸でも多数派」で犯人が割れない**", not obvious, (obvious, rounds2))
check("手がかりは2本以上", not thin, (thin, rounds2))

print("\n[描写] **LLM に書かせる ― ただし論理は渡さない**")
# ゲームの `send_request(manager_name, message, structure)` は名前・頼み文・
# 応答の型を全部こちらで決められる汎用の入口（リコンの署名より）。既存の
# プロンプトを流用せず、この MOD 専用の頼み文を投げる。
#
# **境界はここ。**渡すのは「見た目の指定」と「触れてほしくない語」だけで、
# 犯人も手がかりの効き先も1文字も渡さない。だから何が返っても事件は壊れない。
writer = sys.modules["city_case_mod.writer"]

PEOPLE = [{"sex": "woman", "hair": "dark", "build": "large",
           "words": "黒髪の大柄な女"},
          {"sex": "man", "hair": "light", "build": "small",
           "words": "色の淡い髪の小柄な男"}]
ORDERS = [{"word": "黒髪", "avoid": ["大柄", "小柄", "男", "女"]}]
prompt = writer.build_prompt("試しの世界", "試しの町", PEOPLE, ORDERS, 2)

check("**頼み文に犯人が入っていない**",
      "犯人は" not in prompt.replace("犯人が誰かは書かないこと", "")
      .replace("誰が犯人かは決めないでください", "")
      .replace("目撃情報に犯人の名前", ""),
      [line for line in prompt.splitlines() if "犯人は" in line])
check("見た目の指定は渡している",
      all(p["words"] in prompt for p in PEOPLE), prompt[:200])
check("**触れてほしくない語を渡している**",
      all(w in prompt for w in ORDERS[0]["avoid"]), prompt)
# **その町の実データを渡す。**架空の宿屋を書かれるより、いま歩いている町の
# 宿屋の名前が出るほうが「その町の事件」になる（利用者の判断）。
PLACE = {"world_notes": "霧に沈んだ辺境の世界。",
         "area_notes": "錆びた配管が突き出す停滞した町。",
         "facilities": [("錆びた錨亭", "inn", "隻眼のバルド"),
                        ("鉄錆の徴収所", "guild", "ギルド長")],
         "residents": ["隻眼のバルド", "ギルド長"]}
grounded = writer.build_prompt("試しの世界", "境界の街レムリア", PEOPLE, ORDERS, 2,
                               place=PLACE)
check("**町の名前を渡す**（施設の名前ではない）",
      "【町】境界の街レムリア" in grounded,
      [l for l in grounded.splitlines() if l.startswith("【町】")])
check("町と世界の説明を渡す",
      PLACE["area_notes"] in grounded and PLACE["world_notes"] in grounded)
check("**実在の場所を渡す**", "錆びた錨亭" in grounded and "隻眼のバルド" in grounded,
      grounded[:400])
check("新しい場所を作らせない",
      "新しい場所を作らないでください" in grounded)
check("**既に居る人物と同じ名前を付けさせない**",
      "同じ名前を付けないでください" in grounded)
# 取れないものがある世界でも組める（説明の無い世界・施設の読めない町）。
bare = writer.build_prompt("世界", "町", PEOPLE, ORDERS, 2, place={})
check("実データが無くても頼み文は組める",
      "【町】町" in bare and "実在する場所" not in bare, bare[:200])
check("place を渡さなくても組める",
      "【町】町" in writer.build_prompt("世界", "町", PEOPLE, ORDERS, 2))

check("独自の名前で呼ぶ（ゲームのマネージャ名を使わない）",
      writer.MANAGER_NAME.startswith("mod_")
      and writer.MANAGER_NAME not in ("conversation_starter", "narrator"),
      writer.MANAGER_NAME)

# --- 返事の読み取り。**形を決めつけない** ---
GOOD = {"premise": "宿場町で夜ごと荷が消えている。",
        "people": [{"name": "石工のハルカ", "job": "石工",
                    "profile": "石を刻んで暮らす女。", "personality": "無駄口を叩かない。",
                    "look": "black hair, large build, stone dust"},
                   {"name": "薬売りのコルム", "job": "薬売り",
                    "profile": "町から町へ薬を売り歩く男。", "personality": "如才ない。",
                    "look": "pale blond hair, small build, satchel"}],
        "facts": ["見たのは黒髪の者だったよ。"]}
check("辞書をそのまま読める", writer.as_dict(GOOD) == GOOD)
check("JSON 文字列でも読める",
      writer.as_dict(json.dumps(GOOD, ensure_ascii=False)) == GOOD)
check("読めないものは None", writer.as_dict(12345) is None)


class FakeModel:
    """pydantic のインスタンスで返ってくる版を模す。"""

    def model_dump(self):
        return GOOD


check("pydantic のインスタンスでも読める",
      writer.as_dict(FakeModel()) == GOOD)

people = writer.read_people(GOOD, 2)
check("人物を読み取れる", people and len(people) == 2, people)
check("premise を読み取れる", writer.read_premise(GOOD), writer.read_premise(GOOD))

# --- 検算。**落ちたら下地に戻る** ---
check("人数が足りなければ捨てる",
      writer.read_people(GOOD, 4) is None)
check("項目が欠けていたら捨てる",
      writer.read_people(
          {"people": [dict(GOOD["people"][0], name=""), GOOD["people"][1]]},
          2) is None)
# `110_fix_character_name_path` の対象。立ち絵だけが無言で作られなくなる。
check("**名前に禁止文字が入っていたら捨てる**（立ち絵が作られなくなる）",
      writer.read_people(
          {"people": [dict(GOOD["people"][0], name='石工の"ハルカ'),
                      GOOD["people"][1]]}, 2) is None)
check("同じ名前が並んでいたら捨てる",
      writer.read_people(
          {"people": [GOOD["people"][0], dict(GOOD["people"][1],
                                              name="石工のハルカ")]}, 2) is None)
check("長すぎる文は切る",
      len(writer.read_people(
          {"people": [dict(GOOD["people"][0], profile="あ" * 999),
                      GOOD["people"][1]]}, 2)[0]["profile"])
      <= writer.LIMITS["profile"], writer.LIMITS)

# 目撃情報の検算は**1本ずつ**。1本の失敗で他まで捨てない。
check("頼んだ語が入っていれば通る",
      writer.read_facts(GOOD, ORDERS) == ["見たのは黒髪の者だったよ。"])
check("頼んだ語が無ければその1本だけ捨てる",
      writer.read_facts({"facts": ["何も見ていないね。"]}, ORDERS) == [None])

# **言い換えを認める。**実機で3本とも落ちた:
#   「大柄な」を頼んで『大きな影が路地を通り過ぎるのを見た。』
#   「黒髪」を頼んで  『暗闇の中で、黒い髪が揺れているのが見えた。』
# どちらも中身は正しい仕事なのに、語の完全一致で捨てていた。
LOOSE = [{"word": "黒髪", "accept": ["黒髪", "黒い髪"],
          "avoid": ["大柄", "大きな", "小柄", "男", "女"]}]
check("**言い換えでも通す**（黒髪 → 黒い髪）",
      writer.read_facts({"facts": ["暗闇の中で、黒い髪が揺れているのが見えた。"]},
                        LOOSE) == ["暗闇の中で、黒い髪が揺れているのが見えた。"],
      writer.read_facts({"facts": ["暗闇の中で、黒い髪が揺れているのが見えた。"]},
                        LOOSE))
BIG = [{"word": "大柄", "accept": ["大柄", "大きな", "大男"],
        "avoid": ["黒髪", "黒い髪", "小柄", "男", "女"]}]
check("**言い換えでも通す**（大柄な → 大きな）",
      writer.read_facts({"facts": ["夜中に、大きな影が路地を通り過ぎるのを見た。"]},
                        BIG) == ["夜中に、大きな影が路地を通り過ぎるのを見た。"])
# **緩めた側と締めた側は同じ語彙を見る。**言い換えを広く認めても、
# 他の特徴に触れた文は落ちる。
check("言い換えを認めても、他の特徴に触れたら落ちる",
      writer.read_facts({"facts": ["黒い髪の大きな影を見た。"]}, LOOSE) == [None])
check("認める語を頼み文にも書く（検算と同じ表を見せる）",
      "黒い髪" in writer.build_prompt("w", "a", PEOPLE, LOOSE, 2),
      writer.build_prompt("w", "a", PEOPLE, LOOSE, 2)[-300:])
# **これがいちばん効く検算。**渡していない特徴まで書かれると、その1文で
# 犯人が決まってしまい推理が消える。
check("**他の特徴が漏れていたらその1本を捨てる**",
      writer.read_facts({"facts": ["黒髪の大柄な男を見た。"]}, ORDERS) == [None])
two = [ORDERS[0], {"word": "小柄", "avoid": ["黒髪", "男", "女"]}]
check("落ちるのは落ちた1本だけ",
      writer.read_facts({"facts": ["黒髪の者を見た。", "背の高い男だった。"]},
                        two) == ["黒髪の者を見た。", None])
check("facts が無くても長さは揃える",
      writer.read_facts({}, two) == [None, None])

# --- 呼べない環境では黙って使わない ---
# **実機で踏んだ形**。素の文字列を渡すとゲームの中で
#   TypeError: can only concatenate list (not "str") to list
# になる（`send_request_on_id`）。しかも例外はゲームが内部で立てた別スレッドで
# 起きるので、**呼んだ側には戻らず永久に待たされる。**
sent = {}


def capture(manager_name, message, structure, **kwargs):
    sent["manager"], sent["message"] = manager_name, message
    sent["timeout"] = kwargs.get("timeout")
    return None


# 送信口はローダ（`llm.ask`）が `llm_manager` の別名から引く。プロバイダを
# 名指ししないので、ここでもその別名を置いて確かめる（TECH.md §5.3）。
_manager_name = "scripts.llm.llm_manager"
_saved_manager = sys.modules.get(_manager_name)
_manager = types.ModuleType(_manager_name)
_manager.send_request = capture
_manager.create_model = lambda *a, **k: None
sys.modules[_manager_name] = _manager
try:
    writer.ask(ctx, "頼み文", object(), timeout=12.0)
finally:
    if _saved_manager is None:
        sys.modules.pop(_manager_name, None)
    else:
        sys.modules[_manager_name] = _saved_manager
check("**messages はリストで渡す**（文字列を渡すとゲーム内で TypeError）",
      isinstance(sent.get("message"), list), type(sent.get("message")).__name__)
check("中身は role/content の形",
      isinstance(sent["message"][0], dict)
      and sent["message"][0].get("role") == "user"
      and sent["message"][0].get("content") == "頼み文",
      sent.get("message"))
check("timeout を必ず渡す（返らない推論で止まらないため）",
      sent.get("timeout") == 12.0, sent.get("timeout"))
check("既にリストならそのまま通す",
      writer.as_messages([{"role": "user", "content": "x"}])
      == [{"role": "user", "content": "x"}])

check("send_request が無ければ使わない", not writer.available(None))
check("create_model だけでは使わない",
      not writer.available(types.SimpleNamespace(create_model=lambda *a, **k: None)))
check("両方あれば使う",
      writer.available(types.SimpleNamespace(
          create_model=lambda *a, **k: None, send_request=lambda *a, **k: None)))
# 例外は呼び出し側へ漏らさない（**落ちるくらいなら定型の事件を出す**）。
boom = types.SimpleNamespace(
    create_model=lambda *a, **k: None,
    send_request=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no model")))
check("**呼んで落ちても None を返すだけ**",
      writer.ask(boom, "m", None) is None)

print("\n[描写] **書かせたものが実際に事件へ載る（偽の LLM で通す）**")


def install_fake_llm(reply):
    """`scripts.llm.llm_manager` の代わり。**送られた頼み文を控える。**"""
    seen = {"name": None, "message": None, "structure": None}

    def create_model(model_name, **fields):
        return {"__model__": model_name, "fields": sorted(fields)}

    def send_request(manager_name, message, structure, **kwargs):
        seen["name"], seen["message"] = manager_name, message
        seen["structure"] = structure
        return reply(message) if callable(reply) else reply

    module = types.ModuleType("scripts.llm.llm_manager")
    module.create_model = create_model
    module.send_request = send_request
    parent = sys.modules.setdefault("scripts", types.ModuleType("scripts"))
    llm_parent = sys.modules.setdefault("scripts.llm",
                                        types.ModuleType("scripts.llm"))
    llm_parent.llm_manager = module
    parent.llm = llm_parent
    sys.modules["scripts.llm.llm_manager"] = module
    return module, seen


WRITTEN = {
    "premise": "宿場町で夜ごと荷が消えている。麻袋がひとつ、跡形もなく。",
    "people": [{"name": "縄綯いのセシル", "job": "縄綯い",
                "profile": "港で縄を綯って暮らす。", "personality": "無口。",
                "look": "black hair, large build, rope"},
               {"name": "湯屋のミナ", "job": "湯屋", "profile": "湯を沸かす。",
                "personality": "世話好き。", "look": "black hair, large build"},
               {"name": "荷改めのトビー", "job": "荷改め", "profile": "荷を検める。",
                "personality": "細かい。", "look": "pale hair, small build"},
               {"name": "鍛冶のガルド", "job": "鍛冶", "profile": "鉄を打つ。",
                "personality": "無骨。", "look": "pale hair, small build"}],
}


def reply_to(seen):
    """**頼み文を読んで**それに沿った目撃情報を返す偽の LLM。

    どの軸（髪/体格/性別）が頼まれるかは事件ごとに変わるので、決め打ちの
    文を返す偽物では検算を通ったり通らなかったりする（実際に不安定になった）。
    頼み文から「『◯◯』ということだけを伝える文」を拾って組み立てる。
    """
    def build():
        wanted = re.findall(r"件目: 「(.+?)」ということだけ",
                            str(seen.get("message") or ""))
        return dict(WRITTEN,
                    facts=["{}の者を見かけた。".format(w) for w in wanted])
    return build


def prompt_aware(message):
    """**頼み文を読んで**それに沿った目撃情報を返す偽の LLM。

    どの軸（髪/体格/性別）が頼まれるかは事件ごとに変わるので、決め打ちの文を
    返す偽物では検算を通ったり通らなかったりする（実際に不安定になった）。
    頼み文から「『◯◯』ということだけを伝える文」を拾って組み立てる。
    """
    wanted = re.findall(r"件目: 「(.+?)」ということだけ", str(message or ""))
    return dict(WRITTEN,
                facts=["{}の者を見かけた。".format(word) for word in wanted])


llm_module, seen_request = install_fake_llm(prompt_aware)
try:
    ctxw = FakeCtx()
    clean_record(ctxw)
    mod.apply(ctxw)
    appw, placesw = fresh_world()
    refreshw = install(ctxw, appw)
    appw.facility_screen()
    refreshw()
    appw.texts[:] = []
    press(ctxw, appw, mod.START_LABEL)
    casew = case_mod.load(ctxw.state_path(mod.RECORD_BASENAME))

    check("事件が成立する", casew.get("stage") == case_mod.INVESTIGATING, casew)
    check("**この MOD 専用の名前で呼んでいる**",
          seen_request["name"] == writer.MANAGER_NAME, seen_request["name"])
    check("**新規の頼み文を投げている**（既存プロンプトの流用ではない）",
          "目撃情報" in str(seen_request["message"])
          and "容疑者" in str(seen_request["message"]),
          str(seen_request["message"])[:120])
    check("応答の型を自分で組んでいる",
          isinstance(seen_request["structure"], dict)
          and "people" in seen_request["structure"]["fields"],
          seen_request["structure"])

    names = [appw.world_dict["npcs"][i]["name"]
             for i in case_mod.suspect_ids(casew)]
    check("**書かせた人物が実際に町へ立つ**",
          all(n in [p["name"] for p in WRITTEN["people"]] for n in names), names)
    check("下地の名前は使われていない",
          not [n for n in names if n in [b["name"] for b in CAST_POOL]], names)
    check("**書かせたあらましが画面に出る**",
          any(WRITTEN["premise"] in str(t) for t in appw.texts),
          [str(t) for t in appw.texts][:4])
    facts_now = [c["fact"] for c in casew["clues"]]
    # 特徴の手がかりには書かせた文が載る。**どの軸が頼まれるかは事件ごとに
    # 変わる**ので、偽の LLM は頼み文を読んでそれに沿った文を返している。
    check("検算に通った文はそのまま使われる",
          any(t.endswith("の者を見かけた。") for t in facts_now), facts_now)
    # **一般の不変条件。**1つの手がかりが2つ以上の軸に触れていたら、
    # その1文で犯人が決まってしまい推理が消える。
    axes = {}
    for (axis, _value), word in TRAIT_WORDS.items():
        axes.setdefault(axis, []).append(word.rstrip("の"))
    for text in facts_now:
        touched = [axis for axis, words in axes.items()
                   if any(w in text for w in words)]
        check("  手がかりが触れる特徴は1つまで: {!r}".format(text[:24]),
              len(touched) <= 1, touched)
    # **裏取りは書かせない。**名前を挙げてその人物を消す文なのに、名前は
    # 同じ応答で作らせている最中でまだ無い（`writer.wanted_facts`）。
    alibi = [c["fact"] for c in casew["clues"] if c["kind"] == "alibi"]
    check("**裏取りの文は書かせず、名前を挙げた定型のまま**",
          all(any(n in text for n in names) for text in alibi), alibi)
    # **ここが要点。**他の特徴を漏らした文は捨てて定型に戻る。
    # 通してしまうと、その1文で犯人が決まって推理が消える。
    check("**特徴が漏れた文は捨てて定型に戻る**",
          "あれは黒髪の大柄な女だったよ。" not in facts_now, facts_now)

    # **論理は LLM に渡していないので、何が返っても事件は成立する。**
    check("全部集めれば1人に絞れる", solvable_when_complete(casew), casew["clues"])
    check("犯人が容疑者に含まれる",
          casew["culprit"] in case_mod.suspect_ids(casew), casew)
    check("特徴（tell）はコードが決めたまま",
          all(s["tell"] for s in casew["suspects"]), casew["suspects"])

    # --- 壊れた応答でも事件は始まる（**下地に戻る**）---
    for label, reply in (("None を返す", None),
                         ("空の辞書", {}),
                         ("人数が足りない", {"people": WRITTEN["people"][:1]}),
                         ("文字列のごみ", "<<not json>>")):
        install_fake_llm(reply)
        ctxb = FakeCtx()
        clean_record(ctxb)
        mod.apply(ctxb)
        appb, _pb = fresh_world()
        refreshb = install(ctxb, appb)
        appb.facility_screen()
        refreshb()
        press(ctxb, appb, mod.START_LABEL)
        caseb = case_mod.load(ctxb.state_path(mod.RECORD_BASENAME))
        check("**{}でも事件は始まる**（下地に戻る）".format(label),
              caseb.get("stage") == case_mod.INVESTIGATING, caseb.get("stage"))
        check("  そのとき下地の人物が使われる ({})".format(label),
              all(appb.world_dict["npcs"][i]["name"]
                  in [b["name"] for b in CAST_POOL]
                  for i in case_mod.suspect_ids(caseb)),
              [appb.world_dict["npcs"][i]["name"]
               for i in case_mod.suspect_ids(caseb)])
finally:
    for name in ("scripts.llm.llm_manager", "scripts.llm"):
        sys.modules.pop(name, None)

print("\n[描写] **書かせている間はボタンをロックする**")
# 「噂の出どころを当たっている……」の最中に選択肢が押せると、書いている途中で
# 町を出たり別の依頼を受けたりできてしまう（利用者の指摘）。
# ゲーム自身と同じ待機表示（`.` → `..` → `...`）を出し、`is_button_enabled` を
# 落とす（`ui.Screen.busy_on`）。
#
# この節だけ**偽の Clock を入れて非同期の経路を通す。**入れないと
# `has_clock()` が偽になり、その場で待つ同期の道を通ってしまってロックを
# 一度も踏まない。


class FakeClock:
    """`Clock.schedule_once` を**手で進められる**形にする。

    すぐ走らせてしまうと「待っている状態」が再現できないので、溜めておいて
    テスト側から `run_due()` で進める。
    """

    def __init__(self):
        self.pending = []

    def schedule_once(self, fn, delay=0):
        self.pending.append((delay, fn))

    def schedule_interval(self, fn, poll):
        return None       # 待機表示のアニメーション。検証では回さない

    def run_due(self, upto=None):
        """予約を走らせる。`upto` を渡すとその秒数までのものだけ。"""
        ready = [(d, f) for d, f in self.pending
                 if upto is None or d <= upto]
        self.pending = [(d, f) for d, f in self.pending if (d, f) not in ready]
        for _delay, fn in sorted(ready, key=lambda pair: pair[0]):
            fn(0)
        return len(ready)


def install_fake_clock():
    clock = FakeClock()
    module = types.ModuleType("kivy.clock")
    module.Clock = clock
    parent = sys.modules.setdefault("kivy", types.ModuleType("kivy"))
    parent.clock = module
    sys.modules["kivy.clock"] = module
    return clock


clock = install_fake_clock()
install_fake_llm(prompt_aware)
try:
    check("偽の Clock が入った（非同期の経路を通す）", mod.has_clock())
    ctxa = FakeCtx()
    clean_record(ctxa)
    mod.apply(ctxa)
    appa, placesa = fresh_world()
    refresha = install(ctxa, appa)
    appa.facility_screen()
    refresha()
    appa.is_button_enabled = True
    appa.texts[:] = []

    press(ctxa, appa, mod.START_LABEL)
    # ここではまだ書き込みが返っていない（`schedule_once` を進めていない）。
    check("**待っている間はボタンが押せない**",
          appa.is_button_enabled is False, appa.is_button_enabled)
    check("待っていることを画面に出す",
          any(mod.WRITING_TEXT in str(t) for t in appa.texts),
          [str(t) for t in appa.texts])
    case_now = case_mod.load(ctxa.state_path(mod.RECORD_BASENAME))
    check("まだ事件は始まっていない",
          not case_mod.is_active(case_now), case_now.get("stage"))
    # **押しても何も起きない。**印のあるボタンは組み替えていないので、
    # そもそも並びが変わらない。
    refresha()
    check("待っている間は選択肢を組み替えない",
          mod.START_LABEL in appa.labels() or not appa.labels(), appa.labels())

    # 書き込みが返ってくる（スレッドが `schedule_once` で戻す）。
    for _ in range(6):
        if case_mod.is_active(case_mod.load(ctxa.state_path(mod.RECORD_BASENAME))):
            break
        time.sleep(0.05)
        clock.run_due(upto=0)
    casea = case_mod.load(ctxa.state_path(mod.RECORD_BASENAME))
    check("返ってきたら事件が始まる",
          case_mod.is_active(casea), casea.get("stage"))
    check("**返ってきたらボタンが戻る**",
          appa.is_button_enabled is True, appa.is_button_enabled)

    # --- **注入し直されたら書き上がった素材を捨てる** ---
    # 書き手のスレッドは LLM を待つあいだ最長 `LLM_TIMEOUT + BUSY_GRACE`
    # 生き残る。その間に注入し直されると、**古い世代の続き**が新しい世代と
    # 同じ `state/city_case.json` に書き込んでしまう（TECH.md §3.6.1）。
    ctxo = FakeCtx()
    clean_record(ctxo)
    mod.apply(ctxo)
    appo, _po = fresh_world()
    refresho = install(ctxo, appo)
    appo.facility_screen()
    refresho()
    appo.is_button_enabled = True
    ctxo.superseded_now = True          # 押した後に新しい注入が来た体
    press(ctxo, appo, mod.START_LABEL)
    for _ in range(6):
        time.sleep(0.05)
        clock.run_due(upto=0)
    caseo = case_mod.load(ctxo.state_path(mod.RECORD_BASENAME))
    check("**古い世代は控えに書かない**（新しい注入の側を上書きしない）",
          not case_mod.is_active(caseo), caseo.get("stage"))
    ctxo.superseded_now = False


    # --- **返ってこなくても操作を返す** ---
    # `send_request` が固まったときに押せない画面のまま放置しないための網。
    stuck = threading.Event()
    install_fake_llm(lambda message: stuck.wait(30) or prompt_aware(message))
    ctxs2 = FakeCtx()
    clean_record(ctxs2)
    mod.apply(ctxs2)
    apps2, _ps2 = fresh_world()
    refreshs2 = install(ctxs2, apps2)
    apps2.facility_screen()
    refreshs2()
    apps2.is_button_enabled = True
    apps2.texts[:] = []
    press(ctxs2, apps2, mod.START_LABEL)
    check("固まっている間はロックされている",
          apps2.is_button_enabled is False, apps2.is_button_enabled)
    # 見張りの予約（`LLM_TIMEOUT + BUSY_GRACE` 秒後）だけを走らせる。
    fired = clock.run_due()
    check("見張りが予約されている", fired >= 1, fired)
    check("**返ってこなくてもボタンは戻る**",
          apps2.is_button_enabled is True, apps2.is_button_enabled)
    check("そのわけを画面に出す",
          any(mod.WRITING_SLOW_TEXT in str(t) for t in apps2.texts),
          [str(t) for t in apps2.texts])
    stuck.set()

    # --- **予約が飛ばなくても解ける**（実機で飛ばなかった）---
    # `Clock` の予約だけに頼ると、それが飛ばなかったときロックが残る。
    # 画面を塗り直すたびに期限を点検する経路が最後の砦。
    stuck2 = threading.Event()
    install_fake_llm(lambda message: stuck2.wait(30) or prompt_aware(message))
    ctxd = FakeCtx()
    clean_record(ctxd)
    mod.apply(ctxd)
    appd, _pd = fresh_world()
    refreshd = install(ctxd, appd)
    appd.facility_screen()
    refreshd()
    appd.is_button_enabled = True
    appd.texts[:] = []
    press(ctxd, appd, mod.START_LABEL)
    check("固まっている（予約は握りつぶす）",
          appd.is_button_enabled is False, appd.is_button_enabled)
    clock.pending = []            # **見張りの予約を捨てる**（飛ばなかった状況）
    appd.facility_screen()
    refreshd()                    # 期限前なのでまだ解けない
    check("期限前は解けない", appd.is_button_enabled is False,
          appd.is_button_enabled)
    real_mono = mod.time.monotonic
    mod.time.monotonic = lambda: real_mono() + mod.LLM_TIMEOUT + mod.BUSY_GRACE + 1
    try:
        appd.facility_screen()
        refreshd()                # 期限を過ぎたので、塗り直しの経路で解ける
    finally:
        mod.time.monotonic = real_mono
    check("**予約が飛ばなくても、塗り直しの経路で解ける**",
          appd.is_button_enabled is True, appd.is_button_enabled)
    stuck2.set()
finally:
    # **止めた偽物のスレッドが戻り切るのを待つ。**先に Clock を外すと、
    # 戻ってきたスレッドが `schedule` に失敗して検証の出力にトレースバックが
    # 流れる（失敗ではないが、本物の異常と紛らわしい）。
    for _ in range(40):
        if not [t for t in threading.enumerate()
                if t.name == "city_case_writer"]:
            break
        time.sleep(0.05)
    for name in ("kivy.clock", "kivy"):
        sys.modules.pop(name, None)
    for name in ("scripts.llm.llm_manager", "scripts.llm"):
        sys.modules.pop(name, None)

check("後始末: Clock を外した（他の節は同期の道を通る）", not mod.has_clock())

print("\n[会話] **話した相手と、得た手がかりが噛み合う**")
# 実機で「会話内容と得られたヒントが全くかみ合わない」（利用者の指摘）。原因は2つあった。
# どちらも再現する形で持っておく。
ctxm = FakeCtx()
clean_record(ctxm)
mod.apply(ctxm)
appm, placesm = fresh_world()
refreshm = install(ctxm, appm)
appm.facility_screen()
refreshm()
press(ctxm, appm, mod.START_LABEL)
casem = case_mod.load(ctxm.state_path(mod.RECORD_BASENAME))

# ① **印が残ったまま、別人との会話で発火する。**
# 印は LLM の呼び出しで立ち、会話終了で消える。会話が閉じられずに終わると
# 印が残り、次に誰かとの会話を閉じた瞬間に前の相手の手がかりが出ていた。
witness = [c["witness"] for c in casem["clues"]][0]
other = [i for i in case_mod.suspect_ids(casem)][0]
talk_to(ctxm, appm, witness)             # 証言者と話し始める（印が立つ）
appm.texts[:] = []
end_conversation_with(ctxm, appm, other)  # 別人との会話が閉じる
after = case_mod.load(ctxm.state_path(mod.RECORD_BASENAME))
check("**別人との会話では手がかりが立たない**",
      not any(c["found"] for c in after["clues"]),
      [(c["id"], c["found"]) for c in after["clues"]])
check("画面にも出ない", not [t for t in appm.texts if "分かったこと" in str(t)],
      [str(t) for t in appm.texts])
# 印は消えない ― 相手のところへ戻れば、その会話を閉じたときに出る。
end_conversation_with(ctxm, appm, witness)
back = case_mod.load(ctxm.state_path(mod.RECORD_BASENAME))
check("**相手のところへ戻れば立つ**（印を捨ててしまわない）",
      any(c["found"] for c in back["clues"]),
      [(c["id"], c["found"]) for c in back["clues"]])

# ② **古い印は捨てる。**相手の id が取れない版でも効く最後の網。
ctxt = FakeCtx()
clean_record(ctxt)
mod.apply(ctxt)
appt, placest = fresh_world()
swept_hook = install(ctxt, appt)
appt.facility_screen()
swept_hook()
press(ctxt, appt, mod.START_LABEL)
caset = case_mod.load(ctxt.state_path(mod.RECORD_BASENAME))
talk_to(ctxt, appt, caset["clues"][0]["witness"])
real_monotonic = mod.time.monotonic
mod.time.monotonic = lambda: real_monotonic() + mod.ASK_TTL + 1
try:
    end_conversation(ctxt, appt)
finally:
    mod.time.monotonic = real_monotonic
stale = case_mod.load(ctxt.state_path(mod.RECORD_BASENAME))
check("**古くなった印では手がかりが立たない**",
      not any(c["found"] for c in stale["clues"]),
      [(c["id"], c["found"]) for c in stale["clues"]])

# ③ **相手が分からない版でも壊れない。**`frames.attr` は属性が無いと
# `'<missing>'` という**文字列**を返す。これを id として扱うと
# 「相手は居るが誰とも一致しない」になり、手がかりが永久に立たなくなる。
ctxu = FakeCtx()
clean_record(ctxu)
mod.apply(ctxu)
appu, placesu = fresh_world()
refreshu = install(ctxu, appu)
appu.facility_screen()
refreshu()
press(ctxu, appu, mod.START_LABEL)
caseu = case_mod.load(ctxu.state_path(mod.RECORD_BASENAME))
talk_to(ctxu, appu, caseu["clues"][0]["witness"])
end_conversation(ctxu, appu)          # 相手の分からないマネージャ
plain = case_mod.load(ctxu.state_path(mod.RECORD_BASENAME))
check("**相手が分からなくても手がかりは立つ**（MISSING を id 扱いしない）",
      any(c["found"] for c in plain["clues"]),
      [(c["id"], c["found"]) for c in plain["clues"]])

print("\n[判定] **諦める（調べを打ち切る）**")
# 「やめる」との違いは戻れるかどうか。こちらは事件そのものが消える。
ctxx = FakeCtx()
clean_record(ctxx)
mod.apply(ctxx)
appx, placesx = fresh_world()
existing_x = sorted(appx.world.characters)
refreshx = install(ctxx, appx)
appx.facility_screen()
refreshx()
press(ctxx, appx, mod.START_LABEL)
casex = case_mod.load(ctxx.state_path(mod.RECORD_BASENAME))
cast_x = list(case_mod.suspect_ids(casex))
culprit_x = casex["culprit"]
gold_x = appx.player.gold
appx.texts[:] = []

appx.facility_screen()
refreshx()
press(ctxx, appx, mod.ACCUSE_LABEL)
refreshx()
press(ctxx, appx, mod.GIVE_UP_LABEL)
refreshx()
gave = case_mod.load(ctxx.state_path(mod.RECORD_BASENAME))
check("**諦めると事件が終わる**", not case_mod.is_active(gave), gave["stage"])
check("**キャストは全員引き上げる**",
      not [i for i in cast_x if i in appx.world_dict["npcs"]],
      [i for i in cast_x if i in appx.world_dict["npcs"]])
check("元から居た NPC は巻き添えにならない",
      all(cid in appx.world.characters for cid in existing_x),
      [cid for cid in existing_x if cid not in appx.world.characters])
check("報酬は出ない", appx.player.gold == gold_x,
      (gold_x, appx.player.gold))
# **真相は明かさない。**告発すれば当たっても外れても分かるので、
# そこまで踏み込まずに降りたなら答えは持ち帰れないほうが筋が通る。
said = " ".join(str(t) for t in appx.texts)
check("**真相は明かさない**（犯人の名を出さない）",
      mod.game.name_of(appx, culprit_x) not in said, said[:160])
check("打ち切ったことは画面に出す", mod.ABANDON_TEXT in said, said[:160])
check("**諦めた後は新しい事件を受けられる**",
      mod.START_LABEL in appx.labels(), appx.labels())
check("預かったゲームの選択肢も戻っている",
      EXIT_TEXT in appx.labels(), appx.labels())

# **告発の途中で別の場所へ移っても、預かり物が漏れない。**
# 戻す相手がもう居ないのに書き戻すと、関係の無い画面に古い選択肢が生える。
ctxy = FakeCtx()
clean_record(ctxy)
mod.apply(ctxy)
appy, placesy = fresh_world()
refreshy = install(ctxy, appy)
appy.facility_screen()
refreshy()
press(ctxy, appy, mod.START_LABEL)
appy.facility_screen()
refreshy()
press(ctxy, appy, mod.ACCUSE_LABEL)
refreshy()
check("告発の画面に入っている",
      [t for t in appy.labels() if str(t).startswith(mod.ACCUSE_ONE_PREFIX)],
      appy.labels())
appy.go(placesy["inn"])              # 途中で宿屋へ移る
appy.facility_screen()               # ゲームが自分で画面を組み直す
before_inn = list(appy.labels())
refreshy()
check("**移った先に告発のボタンが漏れない**",
      not [t for t in appy.labels()
           if str(t).startswith(mod.ACCUSE_ONE_PREFIX)
           or t in (mod.LEAVE_LABEL, mod.GIVE_UP_LABEL)],
      appy.labels())
check("移った先の選択肢を壊さない",
      all(t in appy.labels() for t in before_inn), (before_inn, appy.labels()))
appy.go(placesy["guild"])
appy.facility_screen()
refreshy()
check("ギルドへ戻れば告発からやり直せる",
      mod.ACCUSE_LABEL in appy.labels(), appy.labels())

print("\n[会話] **入って即抜けても何も手に入らない**")
# 実機で踏んだ。会話に入って即抜けるだけで手がかりも言い分も
# 手に入った。とくに言い分は**犯人だけ裏の取れない場所**を言うので、
# 4人を出入りするだけで聞き込みを一切せずに犯人が割れた。
ctxq = FakeCtx()
clean_record(ctxq)
mod.apply(ctxq)
appq, placesq = fresh_world()
refreshq = install(ctxq, appq)
appq.facility_screen()
refreshq()
press(ctxq, appq, mod.START_LABEL)
caseq = case_mod.load(ctxq.state_path(mod.RECORD_BASENAME))
witness_q = caseq["clues"][0]["witness"]
suspect_q = case_mod.suspect_ids(caseq)[0]

# --- 証言者のところへ入って、何も尋ねずに抜ける ---
appq.texts[:] = []
seen_greet = greet(ctxq, appq, witness_q)
end_conversation_with(ctxq, appq, witness_q)
after_q = case_mod.load(ctxq.state_path(mod.RECORD_BASENAME))
check("**尋ねずに抜けたら手がかりは立たない**",
      not any(c["found"] for c in after_q["clues"]),
      [(c["id"], c["found"]) for c in after_q["clues"]])
check("画面にも「分かったこと」は出ない",
      not [t for t in appq.texts if "分かったこと" in str(t)],
      [str(t) for t in appq.texts])
# **匂わせはする。**誰に聞けばよいか分からないまま歩かせない。
greeted = str(seen_greet.get("messages"))
check("第一声では「何か知っていそう」だけ伝える",
      "尋ねられるまでは自分から詳しく話さない" in greeted, greeted[-200:])
check("**第一声に事実そのものを載せない**",
      after_q["clues"][0]["fact"] not in greeted, greeted[-200:])

# --- 容疑者のところへ入って、何も尋ねずに抜ける ---
appq.texts[:] = []
greet(ctxq, appq, suspect_q)
end_conversation_with(ctxq, appq, suspect_q)
claims_q = case_mod.load(ctxq.state_path(mod.RECORD_BASENAME))
check("**尋ねずに抜けたら言い分も漏れない**（犯人がタダで割れない）",
      not case_mod.heard_claims(claims_q),
      [s for s in claims_q["suspects"] if s.get("heard")])
check("言い分も画面に出ない",
      not [t for t in appq.texts if "言い分" in str(t)],
      [str(t) for t in appq.texts])

# --- 実際に尋ねれば、ちゃんと手に入る ---
appq.texts[:] = []
talk_to(ctxq, appq, witness_q)
end_conversation_with(ctxq, appq, witness_q)
earned = case_mod.load(ctxq.state_path(mod.RECORD_BASENAME))
check("**尋ねれば手がかりが立つ**",
      any(c["found"] for c in earned["clues"]),
      [(c["id"], c["found"]) for c in earned["clues"]])
appq.texts[:] = []
talk_to(ctxq, appq, suspect_q)
end_conversation_with(ctxq, appq, suspect_q)
heard_q = case_mod.load(ctxq.state_path(mod.RECORD_BASENAME))
check("**尋ねれば言い分が聞ける**", case_mod.heard_claims(heard_q),
      [s for s in heard_q["suspects"] if s.get("heard")])

print("\n[後始末] **繰り返してもセーブが太らない**")
# 以前は犯人に印を立て、無実の者は町に残していた。1件につき3体ずつ増え続け、
# 繰り返し遊ぶとセーブが太る（1体 1.4〜8KB、`npcs` はセーブの約2割。
# 利用者の指摘）。害はバイト数より、町が見知らぬ人で埋まって
# 土地の人物一覧やゲームの組む文脈に効いてくることのほう。
ctxr = FakeCtx()
clean_record(ctxr)
mod.apply(ctxr)
appr, placesr = fresh_world()
refreshr = install(ctxr, appr)
baseline = len(appr.world_dict["npcs"])
counts = []
for _round in range(5):
    appr.go(placesr["guild"])
    appr.facility_screen()
    refreshr()
    press(ctxr, appr, mod.START_LABEL)
    caser = case_mod.load(ctxr.state_path(mod.RECORD_BASENAME))
    if caser.get("stage") != case_mod.INVESTIGATING:
        continue
    during = len(appr.world_dict["npcs"])
    accuse(ctxr, appr, caser["culprit"])
    counts.append((during, len(appr.world_dict["npcs"])))

check("5件とも事件が成立した", len(counts) == 5, counts)
check("**事件中は容疑者のぶんだけ増える**",
      all(during == baseline + mod.SUSPECT_COUNT for during, _after in counts),
      (baseline, counts))
check("**決着すれば必ず元の人数に戻る**",
      all(after == baseline for _during, after in counts),
      (baseline, counts))
check("台帳も溜まらない",
      not ledger_mod.ids(ledger_mod.load(ctxr.state_path(mod.LEDGER_BASENAME)),
                         world_mod.world_name(appr)),
      ledger_mod.load(ctxr.state_path(mod.LEDGER_BASENAME)))

print("\n[後始末] **置き去りを起動時に掃除する**")
# 台帳が無い時代に作られた NPC が既にセーブに居る（利用者の環境で4体）。
# 印を NPC 自身に持たせられない（項目を足すと33項目の並びが壊れる）ので、
# **下地の名前と完全一致**するものを拾う。
ctxl = FakeCtx()
clean_record(ctxl)
mod.apply(ctxl)
appl, placesl = fresh_world()
survivors = sorted(appl.world.characters)
# 台帳より前に作られたぶんを模す（下地の名前をそのまま持つ NPC）。
stray_name = CAST_POOL[0]["name"]
stray = "900"
appl.world_dict["npcs"][stray] = dict(world_mod.NEW_NPC_TEMPLATE,
                                      id=stray, name=stray_name)
appl.world.characters[stray] = types.SimpleNamespace(
    id=stray, name=stray_name,
    config={"level_of_detail": 2, "is_player": False,
            "is_dead": False, "difficulty_level": 4})
refreshl = install(ctxl, appl)
appl.facility_screen()
refreshl()
check("**名前で拾って掃除される**", stray not in appl.world_dict["npcs"],
      sorted(appl.world_dict["npcs"]))
check("**元から居た NPC は巻き添えにならない**",
      all(cid in appl.world.characters for cid in survivors),
      [cid for cid in survivors if cid not in appl.world.characters])

# 進行中の事件のキャストには手を出さない（掃除は世界ごとに1回だが、
# 事件を受けた後で走っても消してはいけない）。
ctxk = FakeCtx()
clean_record(ctxk)
mod.apply(ctxk)
appk, placesk = fresh_world()
refreshk = install(ctxk, appk)
appk.facility_screen()
refreshk()
press(ctxk, appk, mod.START_LABEL)
casek = case_mod.load(ctxk.state_path(mod.RECORD_BASENAME))
alive = list(case_mod.suspect_ids(casek))
mod.apply(ctxk)                  # 読み直しを模して掃除をもう一度走らせる
refreshk2 = install(ctxk, appk)
appk.facility_screen()
refreshk2()
check("**進行中の事件のキャストは掃除されない**",
      all(i in appk.world_dict["npcs"] for i in alive),
      [i for i in alive if i not in appk.world_dict["npcs"]])

print("\n[後始末] パーティーに居る者は消さない")
# 容疑者が仲間になる経路は用意していないが、他の MOD やゲーム側の都合で
# 入りうる。連れ歩いている相手を消すと参照が切れて何が起きるか分からない。
ctxp = FakeCtx()
clean_record(ctxp)
mod.apply(ctxp)
appp, placesp = fresh_world()
refreshp = install(ctxp, appp)
appp.facility_screen()
refreshp()
press(ctxp, appp, mod.START_LABEL)
casep = case_mod.load(ctxp.state_path(mod.RECORD_BASENAME))
guarded = case_mod.suspect_ids(casep)[0]
appp.party = [guarded, "player"]
accuse(ctxp, appp, casep["culprit"])
check("**パーティーに居る容疑者は残る**", guarded in appp.world_dict["npcs"],
      (guarded, sorted(appp.world_dict["npcs"])))
check("それ以外のキャストは消える",
      not [i for i in case_mod.suspect_ids(casep)
           if i != guarded and i in appp.world_dict["npcs"]],
      sorted(appp.world_dict["npcs"]))

print("\n[判定] 外したときは何も起きない")
ctx2 = FakeCtx()
clean_record(ctx2)
mod.apply(ctx2)
app2, places2 = fresh_world()
refresh2 = install(ctx2, app2)
app2.facility_screen()
refresh2()
press(ctx2, app2, mod.START_LABEL)
found2 = case_mod.load(ctx2.state_path(mod.RECORD_BASENAME))
cast2 = list(case_mod.suspect_ids(found2))
innocent = [n for n in cast2 if n != found2["culprit"]][0]
existing2 = sorted(set(app2.world.characters) - set(cast2))
gold2 = app2.player.gold
accuse(ctx2, app2, innocent)
check("外したら報酬は出ない", app2.player.gold == gold2)
# **外しても決着は決着。**当てたときと同じくキャストは引き上げる ―
# 残すと、二度と使わない人物が町に溜まっていく（利用者の指摘）。
check("**外したときもキャストは世界から消える**",
      not [i for i in cast2 if i in app2.world.characters],
      [i for i in cast2 if i in app2.world.characters])
check("**外したときもセーブから消える**",
      not [i for i in cast2 if i in app2.world_dict["npcs"]],
      sorted(app2.world_dict["npcs"]))
check("**元から居た NPC は巻き添えにならない**",
      all(cid in app2.world.characters for cid in existing2),
      [cid for cid in existing2 if cid not in app2.world.characters])

# ================================================================== 安全
print("\n[安全] ゲームのものに触らない")
# **自由施設にはもう一切手を出さない。**告発の場面をやめた時点で、
# ゲームの `free_*` を横取りする理由が無くなった（利用者の判断）。
# 横取りしていた頃は、`scripts.free_facility` がまだ import されていないと
# 告発ボタンを押しても「調べていることは無い」と出て詰む作りでもあった。
check("**自由施設のフックを1つも持たない**",
      not [t for t in ctx.hooks if "free_facility" in t],
      [t for t in ctx.hooks if "free_facility" in t])
check("シーンの道具立てを持ち込んでいない",
      not hasattr(mod, "PROGRAM_ID") and "city_case_mod.scenes" not in sys.modules,
      [n for n in dir(mod) if "PROGRAM" in n])
# **この検証は `scripts.free_facility` を一度も作らずに通っている。**
# 以前はそれが import されていないと告発で詰む作りだったので、
# 「無くても通しで解ける」ことをここで担保する。
check("**`scripts.free_facility` が無い環境で全部通っている**",
      "scripts.free_facility" not in sys.modules,
      sorted(m for m in sys.modules if "free_facility" in m))

def live_strings(path):
    """**実際に使われる文字列だけ**を集める（docstring と註釈は数えない）。

    素朴に本文を検索すると、「ここへは書かない」と説明した docstring まで
    引っかかる（最初の版がそれで誤検知した）。見たいのはコードの中身。
    """
    with io.open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), feature_version=(3, 10))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value not in docstrings}


def live_names(path):
    with io.open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), feature_version=(3, 10))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} \
        | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


SOURCES = [MOD] + [os.path.join(MOD_DIR, name)
                   for name in ("case.py", "world.py", "ledger.py")]
used_strings = set()
for path in SOURCES:
    used_strings |= live_strings(path)

check("**flag_set を使わない**（施設の config に残るため）",
      "flag_set" not in used_strings, sorted(s for s in used_strings
                                             if "flag" in s))
# `world_dict` そのものは触ってよい ― NPC を作る経路（`generate_npc`）が
# それを引数に取るため。**禁じたいのはシーンのプログラムを焼き付けること。**
check("**free_facility_programs へ書き込まない**（セーブに残るため）",
      "free_facility_programs" not in used_strings,
      sorted(s for s in used_strings if "free_facility" in s))
# **自由施設そのものに触らない。**告発を自前のボタンへ移したので、
# ゲームの `free_*` を引く経路は1つも要らなくなった。
check("**`free_facility` の名前がコードに出てこない**",
      not [s for s in used_strings if "free_facility" in s],
      sorted(s for s in used_strings if "free_facility" in s))
check("effect / elapse / call_phase も使わない",
      not ({"effect", "elapse", "call_phase"} & used_strings),
      sorted({"effect", "elapse", "call_phase"} & used_strings))

print("\n[安全] 残骸の掃除")
app3, places3 = fresh_world()
refresh3 = install(ctx, app3)
app3.facility_screen()
# セーブから復元されたボタン（**印が落ちている**）を混ぜる。
app3.buttons.insert(0, {"text": mod.START_LABEL,
                        "spec": PhaseSpec("JustSetButtonToNormalPhase", [])})
refresh3()
check("印を失った残骸は1つに戻る",
      app3.labels().count(mod.START_LABEL) == 1, app3.labels())

# ================================================================== 共存
print("\n[共存] 印のキーが他の mod と衝突していない")
marks = {}
for folder in sorted(os.listdir(MODS_DIR)):
    path = os.path.join(MODS_DIR, folder, "mod.json")
    if not os.path.isfile(path):
        continue
    with io.open(path, encoding="utf-8") as fh:
        entry = json.load(fh).get("entry")
    source_path = os.path.join(MODS_DIR, folder, entry or "")
    if not os.path.isfile(source_path):
        continue
    with io.open(source_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MARK = "):
                marks.setdefault(line.split("=", 1)[1].strip(), []).append(folder)
                break
check("どの印も1つの mod しか使っていない",
      all(len(owners) == 1 for owners in marks.values()), marks)
check("この mod の印が一覧に入っている",
      '"{}"'.format(mod.MARK) in marks or "'{}'".format(mod.MARK) in marks,
      sorted(marks))

# ================================================================== 名乗り
print("\n[名乗り] mod.json とコードの既定値が一致する")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
fresh = load_mod(name="city_case_defaults")
for key, spec in manifest.get("settings", {}).items():
    check("既定値が一致: {}".format(key),
          getattr(fresh, key, object()) == spec.get("default"),
          (getattr(fresh, key, None), spec.get("default")))

print("\n[世界の鍵] ロード直後でも引ける")
# **ロード直後は `app.world` がまだ組み上がっていない**（世界名はセーブ側の
# `world_dict["world_data"]` にしかない）。`app.world` の属性しか見ない版は
# ここで空文字を返し、控えの世界照合と後始末が黙って素通りしていた。
_loading = types.SimpleNamespace(
    world=None, world_dict={"world_data": {"world_name": "灰の街"}})
check("`app.world` がまだ無くてもセーブ側から引ける",
      world_mod.world_name(_loading) == "灰の街",
      world_mod.world_name(_loading))
_running = types.SimpleNamespace(
    world=types.SimpleNamespace(name="鉄錆の town"), world_dict={})
check("走っている世界は今までどおり `app.world` から引ける",
      world_mod.world_name(_running) == "鉄錆の town",
      world_mod.world_name(_running))
check("どちらからも引けなければ空文字（＝紐付けない）",
      world_mod.world_name(types.SimpleNamespace(world=None, world_dict={})) == "",
      world_mod.world_name(types.SimpleNamespace(world=None, world_dict={})))

print("\n[ログ] 何が起きたか残る")
log_path = ctx.out_path(mod.LOG_BASENAME)
check("ログが書かれている", os.path.exists(log_path)
      and os.path.getsize(log_path) > 0, log_path)
check("フックの中で例外が出ていない", not ctx.errors, ctx.errors)

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
