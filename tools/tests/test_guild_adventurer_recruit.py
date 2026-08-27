# -*- coding: utf-8 -*-
"""320_guild_adventurer_recruit をゲーム抜きで通す。

    python tools/tests/test_guild_adventurer_recruit.py

偽の app / World / Area / Facility / PhaseSpec / llm_manager / Clock を
差し込み、次を確認する。

  出す     … 冒険者一覧（armed）で人数が閾値以下なら「やめる」の手前に
             募集ボタンが入る
  出さない … 人数が閾値を超える・armed でない（「会話する」の一覧）・
             別の画面の目印があるときは差さない
  残骸     … セーブから復元された印の無い自前ボタンは落としてから差し直す
  募集     … 押すとLLMの答えから冒険者が作られ、素データ2箇所・実行時の
             名簿・ギルドの名簿・Area.adventurer_npcs に入る。
             項目は33個・セーブの並び順のまま
  水準     … 難易度は土地の冒険者と依頼の最大値。76 で刈る
  複数     … 一度に2人の設定なら別々の id で2人入る
  開き直し … 終わったら一覧をゲーム自身の DisplayAdventurerTalkChoice で
             開き直し、補充後の人数が閾値を超えたらボタンは出ない
  不発     … LLMが読めなければ何も作らず、その旨を画面に流す
  安全     … ギルドの無い土地では何もしない
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
from instantale_modloader.npcs import NPC_FIELD_ORDER  # noqa: E402


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
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_guild_adventurer_recruit")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    def __init__(self, app, *args):
        self.app = app


class Facility:
    def __init__(self, facility_id, facility_type, owner=None):
        self.id = facility_id
        self.name = "テスト" + facility_type
        self.facility_type = facility_type
        self.owner = owner
        self.characters = []


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, facilities, adventurer_npcs=(), quests=()):
        self.id = area_id
        self.name = "始まりの泥濘"
        self.descriptions = {"overview": "湿地に囲まれた小さな開拓地。"}
        self.nodes = {"0": Node(facilities)}
        self.adventurer_npcs = list(adventurer_npcs)
        self.quests = list(quests)
        self.resident_npcs = []


class Character:
    def __init__(self, character_id, name, config=None, job=None):
        self.id = character_id
        self.name = name
        self.config = dict(config or {})
        self.job = job


class World:
    """`generate_character` は素データを id で引く（実ゲームと同じ約束）。"""

    def __init__(self, areas, characters, save_data_dict, quests=None):
        self.areas = areas
        self.characters = characters
        self.quests = dict(quests or {})
        self._save_data_dict = save_data_dict

    def generate_character(self, character_id, character_value):
        data = self._save_data_dict["npcs"][character_id]   # 無い id は KeyError
        # 実機は ability_scores の6つの鍵を引く。無いと KeyError: 'constitution'
        # （2026-08-27 実機。VERIFICATION_LOG.md §2.72）。値は None でよい。
        scores = data["ability_scores"]
        for key in ("strength", "dexterity", "constitution",
                    "intelligence", "wisdom", "charisma"):
            scores[key]
        character = Character(str(character_id), data.get("name"),
                              config=data.get("config"), job=data.get("job"))
        self.characters[str(character_id)] = character
        return character


class Player:
    def __init__(self, area):
        self.name = "テストプレイヤー"
        self.current_area = area


class InstantaleApp:
    def __init__(self, world, player, world_dict, save_data_dict,
                 game_variables):
        self.world = world
        self.player = player
        self.world_dict = world_dict
        self.save_data_dict = save_data_dict
        self.game_variables = game_variables
        self.buttons = []
        self.to_display_buttons = []
        self.is_adding_text = False
        self.is_button_enabled = True
        self.is_popup_window_opened = False
        self.said = []
        self.moved = []
        self.processed = []

    def add_text(self, context=None, *args, **kwargs):
        self.said.append(context)

    def refresh_choice_buttons(self, reset_page=False, *args, **kwargs):
        return None

    def process_choice(self, manager, choice_text):
        self.processed.append((type(manager).__name__, choice_text))
        return manager.execute(choice_text)

    def move_npc_to_facility(self, character_id, character_instance,
                             target_facility, target_node=None,
                             register_facility=True):
        self.moved.append((str(character_id),
                           getattr(target_facility, "id", None)))
        target_facility.characters.append(str(character_id))


CURRENT = {"ctx": None}


def rebuild_list(app):
    """ゲームが一覧を組み直す（死亡とパーティを飛ばす読み方）。"""
    area = app.player.current_area
    if isinstance(area, str):
        area = app.world.areas[area]
    party = {str(member) for member in app.game_variables.get("party", [])}
    entries = []
    for cid in area.adventurer_npcs:
        character = app.world.characters.get(str(cid))
        if character is None or character.config.get("is_dead"):
            continue
        if str(cid) in party:
            continue
        entries.append({"text": character.name,
                        "spec": PhaseSpec("ConversationStartManager", [str(cid)])})
    entries.append({"text": "やめる",
                    "spec": PhaseSpec("JustSetButtonToNormalPhase", [])})
    app.buttons = entries


class DisplayAdventurerTalkChoice:
    """一覧の入口。mod のフックが当たっている前提を自分で演じる。"""

    opened = []

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        DisplayAdventurerTalkChoice.opened.append(choice_text)
        ctx = CURRENT["ctx"]
        hook = (ctx.hooks.get("__main__:DisplayAdventurerTalkChoice.execute")
                if ctx is not None else None)
        orig = lambda _self, text: rebuild_list(self.app)   # noqa: E731
        if hook is not None:
            return hook(orig, self, choice_text)
        return orig(self, choice_text)


class FakeClock:
    def __init__(self):
        self.onces = []
        self.intervals = []

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def schedule_interval(self, callback, poll):
        self.intervals.append(callback)

    def run_onces(self):
        for _ in range(8):
            pending, self.onces = self.onces, []
            if not pending:
                return
            for callback in pending:
                callback(0.0)


CLOCK = FakeClock()


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = CLOCK
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    sys.modules.pop("kivy.app", None)


# ---------------------------------------------------------------- 偽 LLM
LLM = {"queue": [], "calls": []}


def install_fake_llm():
    module = types.ModuleType("scripts.llm.llm_manager")

    def create_model(__model_name, **fields):
        # 本物（pydantic）の第1引数は `__model_name`。
        # 素朴に `name` にすると、項目名 name と衝突して本物では起きない
        # TypeError を作ってしまう。
        return ("structure", __model_name, tuple(sorted(fields)))

    def send_request(manager_name, message, structure=None,
                     timeout=None, max_tokens=None):
        LLM["calls"].append((manager_name, message, timeout, max_tokens))
        if not LLM["queue"]:
            return None
        return LLM["queue"].pop(0)

    module.create_model = create_model
    module.send_request = send_request
    sys.modules["scripts.llm.llm_manager"] = module


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def resolve(self, target):
        return None, None, None

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="adventurer_recruit_mod"):
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


def fresh_mod(recruit_min=1, recruit_count=1):
    sys.modules.pop("adventurer_recruit_mod", None)
    module = load_mod()
    module.RECRUIT_MIN = recruit_min
    module.RECRUIT_COUNT = recruit_count
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    CURRENT["ctx"] = ctx
    return module, ctx


# ---------------------------------------------------------------- 舞台作り
GUILD_ID = "27"


def make_world(quest_difficulty=50, with_guild=True, adventurers=None):
    """冒険者3人（死亡1・パーティ1・在席1）のギルドの町。"""
    if adventurers is None:
        adventurers = [
            ("7", "鉄屑のレオン", {"is_dead": True, "difficulty_level": 38}),
            ("8", "影のミラ", {"is_dead": False, "difficulty_level": 30}),
            ("9", "重装のハンス", {"is_dead": False, "difficulty_level": 25}),
        ]
    facilities = [Facility(GUILD_ID, "guild", owner="11")] if with_guild else []
    area = Area("0", facilities,
                adventurer_npcs=[cid for cid, _n, _c in adventurers],
                quests=["q1"])
    characters = {"11": Character("11", "事務官エルザ", job="guild")}
    raw_npcs = {"11": {"name": "事務官エルザ", "id": "11", "job": "guild"}}
    for cid, name, config in adventurers:
        characters[cid] = Character(cid, name, config=config, job="adventure")
        raw_npcs[cid] = {"name": name, "id": cid, "job": "adventure",
                         "config": dict(config)}
    save_data_dict = {"npcs": dict(raw_npcs)}
    world = World({"0": area}, characters, save_data_dict,
                  quests={"q1": {"difficulty": quest_difficulty}})
    world_dict = {"npcs": dict(raw_npcs),
                  "areas": {"0": {"adventurer_npcs":
                                  list(area.adventurer_npcs)}}}
    player = Player(area)
    app = InstantaleApp(world, player, world_dict, save_data_dict,
                        {"party": ["player", "8"]})
    return app, area


def open_list(ctx, app):
    """「冒険者達と話す」を押した（armed → 一覧 → refresh）。"""
    DisplayAdventurerTalkChoice(app).execute("冒険者達と話す")
    return run_refresh(ctx, app)


def run_refresh(ctx, app):
    hook = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"]
    return hook(lambda _self, reset_page=False, *a, **k: None, app)


def open_talk(ctx, app):
    """「会話する」（同じ形の別画面）を押した。"""
    hook = ctx.hooks["__main__:DisplayTalkChoice.execute"]
    hook(lambda _self, text: rebuild_list(app), object(), "会話する")
    return run_refresh(ctx, app)


def press(ctx, app, index):
    hook = ctx.hooks["__main__:InstantaleApp.on_button_press"]
    passed = []
    hook(lambda _self, i, *a, **k: passed.append(i), app, index)
    CLOCK.run_onces()
    return passed


def button_texts(app):
    return [entry.get("text") for entry in app.buttons]


def recruit_index(module, app):
    for index, entry in enumerate(app.buttons):
        if entry.get("text") == module.RECRUIT_LABEL:
            return index
    return None


def answer(name="新星のミラベル"):
    return {"name": name, "profile": "国境の戦から流れてきた傭兵くずれ。",
            "personality": "口は悪いが面倒見はよい。",
            "speech_style": "ぶっきらぼうな短い言い切り。",
            "look_description": "使い込んだ革鎧と大剣。",
            "category": "young woman",
            "look": "young woman, worn leather armor, greatsword, "
                    "short black hair, confident stance"}


# ---------------------------------------------------------------- 検査
def main():
    install_fake_kivy()
    install_fake_llm()
    main_module = sys.modules["__main__"]
    main_module.InstantaleApp = InstantaleApp
    main_module.PhaseSpec = PhaseSpec
    main_module.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main_module.DisplayAdventurerTalkChoice = DisplayAdventurerTalkChoice
    os.makedirs(OUT_DIR, exist_ok=True)

    # -- 出す ------------------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world()
    open_list(ctx, app)                     # 会話できるのはハンス1人
    check("出す: 一覧が1人なら募集ボタンが入る",
          module.RECRUIT_LABEL in button_texts(app), button_texts(app))
    check("出す: 位置は「やめる」の手前",
          button_texts(app)[-2:] == [module.RECRUIT_LABEL, "やめる"],
          button_texts(app))
    check("出す: 2度 refresh しても増えない",
          (run_refresh(ctx, app) or True)
          and button_texts(app).count(module.RECRUIT_LABEL) == 1,
          button_texts(app))

    # -- 出さない --------------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world()
    app.world.characters["7"].config["is_dead"] = False   # 2人になる
    open_list(ctx, app)
    check("出さない: 人数が閾値を超えている",
          module.RECRUIT_LABEL not in button_texts(app), button_texts(app))

    module, ctx = fresh_mod()
    app, area = make_world()
    open_talk(ctx, app)                     # 同じ形だが「会話する」の一覧
    check("出さない: armed でなければ同じ形でも差さない",
          module.RECRUIT_LABEL not in button_texts(app), button_texts(app))

    module, ctx = fresh_mod()
    app, area = make_world()
    DisplayAdventurerTalkChoice(app).execute("冒険者達と話す")
    app.buttons = [{"text": "会話を終了する",
                    "spec": PhaseSpec("ConversationEndManager", ["9"])}]
    run_refresh(ctx, app)
    check("出さない: 別の画面の目印があれば旗を下ろす",
          module.RECRUIT_LABEL not in button_texts(app), button_texts(app))
    rebuild_list(app)
    run_refresh(ctx, app)
    check("出さない: 旗が下りた後は一覧の形でも差さない",
          module.RECRUIT_LABEL not in button_texts(app), button_texts(app))

    # -- 残骸 ------------------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world()
    DisplayAdventurerTalkChoice(app).execute("冒険者達と話す")
    rebuild_list(app)
    app.buttons.insert(len(app.buttons) - 1,
                       {"text": module.RECRUIT_LABEL,
                        "spec": PhaseSpec("JustSetButtonToNormalPhase", [])})
    run_refresh(ctx, app)
    marked = [entry for entry in app.buttons
              if entry.get("text") == module.RECRUIT_LABEL]
    check("残骸: 印の無い復元ボタンは落として差し直す",
          len(marked) == 1 and marked[0].get(module.MARK) is not None, marked)

    # -- 募集 ------------------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world()
    open_list(ctx, app)
    LLM["queue"] = [answer()]
    DisplayAdventurerTalkChoice.opened = []
    passed = press(ctx, app, recruit_index(module, app))
    check("募集: ゲーム側の on_button_press へは流さない", passed == [], passed)
    data = app.save_data_dict["npcs"].get("12")
    check("募集: 素データが save 側に入る（id 12）",
          isinstance(data, dict), sorted(app.save_data_dict["npcs"]))
    check("募集: 素データが world_dict 側にも入る",
          isinstance(app.world_dict["npcs"].get("12"), dict),
          sorted(app.world_dict["npcs"]))
    check("募集: 項目は33個・セーブの並び順のまま",
          data is not None and tuple(data) == NPC_FIELD_ORDER,
          list(data or {}))
    check("募集: 名前と職はLLMの答えのとおり",
          data is not None and data["name"] == "新星のミラベル"
          and data["job"] == "adventure", data)
    check("募集: 実行時の名簿にも組まれる",
          "12" in app.world.characters, sorted(app.world.characters))
    check("募集: ギルドの名簿に置かれる",
          ("12", GUILD_ID) in app.moved
          and "12" in area.nodes["0"].facilities[GUILD_ID].characters,
          app.moved)
    check("募集: Area.adventurer_npcs に入る",
          "12" in area.adventurer_npcs, area.adventurer_npcs)
    check("募集: 素データ側の adventurer_npcs にも入る",
          "12" in app.world_dict["areas"]["0"]["adventurer_npcs"],
          app.world_dict["areas"]["0"])
    config = (data or {}).get("config") or {}
    check("水準: 難易度は土地の最大（依頼の50）",
          config.get("difficulty_level") == 50, config)
    check("募集: 詳細はゲームに埋めさせる（level_of_detail=1）",
          config.get("level_of_detail") == 1, config)
    scores = (data or {}).get("ability_scores") or {}
    check("募集: ability_scores は6つの鍵・値は None",
          sorted(scores) == ["charisma", "constitution", "dexterity",
                             "intelligence", "strength", "wisdom"]
          and all(value is None for value in scores.values()), scores)
    check("募集: 生成直後の実物に合わせる（age 20・レベル整数・現在地）",
          data is not None and data["age"] == 20
          and isinstance(data["experience_level"], int)
          and data["experience_level"] >= 1
          and data["current_area"] == "0"
          and data["current_location"] == GUILD_ID, data)
    check("募集: 関係値は初対面の初期値",
          data is not None
          and data["relationship"]["player"]["relationship"] == ["初対面"],
          (data or {}).get("relationship"))
    check("募集: look は category 先頭のリスト",
          data is not None and isinstance(data["look"], list)
          and data["look"][0] == "young woman" and len(data["look"]) >= 3,
          (data or {}).get("look"))
    check("募集: 到着を画面に流す",
          any("新星のミラベル" in (text or "") for text in app.said), app.said)
    check("開き直し: 一覧をゲーム自身で開き直す",
          DisplayAdventurerTalkChoice.opened == [module.LIST_TEXT],
          DisplayAdventurerTalkChoice.opened)
    run_refresh(ctx, app)
    check("開き直し: 2人になったのでボタンは出ない",
          module.RECRUIT_LABEL not in button_texts(app), button_texts(app))
    # 旗の戻りは挙動で確かめる: 1人減らして開き直せば、もう一度募集できる。
    app.world.characters["9"].config["is_dead"] = True
    open_list(ctx, app)
    LLM["queue"] = [answer("旅装のセロ")]
    press(ctx, app, recruit_index(module, app))
    check("募集: 終われば旗が戻り、もう一度募集できる",
          app.save_data_dict["npcs"].get("13", {}).get("name") == "旅装のセロ",
          sorted(app.save_data_dict["npcs"]))

    # -- 水準の刈り込み ---------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world(quest_difficulty=90)
    open_list(ctx, app)
    LLM["queue"] = [answer("大剣のガザム")]
    press(ctx, app, recruit_index(module, app))
    config = app.save_data_dict["npcs"]["12"]["config"]
    check("水準: 76 で刈る（雇用価格表の定義域）",
          config.get("difficulty_level") == 76, config)

    # -- 複数 ------------------------------------------------------------
    module, ctx = fresh_mod(recruit_count=2)
    app, area = make_world()
    open_list(ctx, app)
    LLM["queue"] = [answer("斥候のフィン"), answer("薬草師リタ")]
    press(ctx, app, recruit_index(module, app))
    check("複数: 2人が別々の id で入る",
          {"12", "13"} <= set(app.save_data_dict["npcs"])
          and app.save_data_dict["npcs"]["13"]["name"] == "薬草師リタ",
          sorted(app.save_data_dict["npcs"]))
    check("複数: 2人とも Area.adventurer_npcs に入る",
          "12" in area.adventurer_npcs and "13" in area.adventurer_npcs,
          area.adventurer_npcs)
    check("複数: 入れ子の辞書を共有しない（深い複製）",
          app.save_data_dict["npcs"]["12"]["ability_scores"]
          is not app.save_data_dict["npcs"]["13"]["ability_scores"],
          "shared dict")

    # -- 不発 ------------------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world()
    open_list(ctx, app)
    LLM["queue"] = []                        # 何も返らない
    before = sorted(app.save_data_dict["npcs"])
    press(ctx, app, recruit_index(module, app))
    check("不発: 何も作らない",
          sorted(app.save_data_dict["npcs"]) == before,
          sorted(app.save_data_dict["npcs"]))
    check("不発: その旨を画面に流す",
          any("応じる者" in (text or "") for text in app.said), app.said)
    LLM["queue"] = [answer("再挑のダン")]
    press(ctx, app, recruit_index(module, app))
    check("不発: 旗が戻り、次の募集はできる",
          app.save_data_dict["npcs"].get("12", {}).get("name") == "再挑のダン",
          sorted(app.save_data_dict["npcs"]))

    # -- 名前の掃除 -------------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world()
    open_list(ctx, app)
    LLM["queue"] = [answer('剣聖"アル/ヴァ"')]
    press(ctx, app, recruit_index(module, app))
    name = app.save_data_dict["npcs"]["12"]["name"]
    check("名前: パスに使えない字を落とす（GAME.md §2.15）",
          name == "剣聖アルヴァ", name)

    # -- 安全 ------------------------------------------------------------
    module, ctx = fresh_mod()
    app, area = make_world(with_guild=False)
    open_list(ctx, app)
    LLM["queue"] = [answer()]
    index = recruit_index(module, app)
    before = sorted(app.save_data_dict["npcs"])
    if index is not None:
        press(ctx, app, index)
    check("安全: ギルドが無い土地では作らない",
          sorted(app.save_data_dict["npcs"]) == before,
          sorted(app.save_data_dict["npcs"]))

    check("例外を握り潰していない", not CURRENT["ctx"].errors,
          CURRENT["ctx"].errors)

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
