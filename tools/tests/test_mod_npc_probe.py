# -*- coding: utf-8 -*-
"""229_probe_mod_npc をゲーム抜きで通す。

    python tools/tests/test_mod_npc_probe.py

偽の `ctx` と偽の世界を差し込み、次を確認する。

  経路   … `apply()` が最後まで通り、関所（ローダ側）と probe 自身の包みが登録される
  置く   … 施設に入ると人物が名簿と施設に載り、その施設の主に notes の層が載る（本物の profile は触らない）
  隠す   … 保存の包みの中では名簿に `mod:` が無く、被せも素へ戻っている
  漏れ   … 書かれたセーブに `mod:` の鍵があれば `leaked` に出る（見つけられることの確認）
  会話   … 会話の入口を通ると記録が1行ずつ増える
  変えない … 包んだ本体はどれも1回だけ呼ばれ、戻り値はそのまま通る

背景: `apply()` の中の未定義名は次の起動まで潜伏する。
probe は実機でしか仕事をしないので、ここで1度通しておかないと
「デバッグモードで起動したら何も録れていなかった」になる。
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

from instantale_modloader import modnpc  # noqa: E402

RECORD_NAME = "mod_npc.jsonl"
LOG_NAME = "mod_npc.log"

ABILITY_KEYS = ("strength", "dexterity", "constitution", "intelligence",
                "wisdom", "charisma")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_probe_mod_npc")


# ---------------------------------------------------------------- 偽ゲーム
class Character(object):
    def __init__(self, name, character_id=None):
        # 実機の `Character` は自分でも `id` を持つ（GAME.md §2.7）。
        # 頼み文の関数は `character_instance` しか渡してこないので、
        # 名簿が引けない場面ではここが相手を見分ける唯一の手掛かりになる。
        self.id = character_id
        self.name = name
        self.profile = "この街で宿を継いだ"
        self.personality = None
        self.job = None
        self.look_description = None
        self.speech_style = None
        self.image_src = None
        self.knowledges = None
        self.relationship = {"player": {"affinity": 0}}
        self.current_log = ["前の会話の要約"]
        self.life_log = []
        self.skills = {}
        self.original_ability_scores = dict.fromkeys(ABILITY_KEYS, 10)


class Facility(object):
    def __init__(self, facility_id, owner):
        self.id = facility_id
        self.characters = []
        self.owner = owner


class Area(object):
    def __init__(self, area_id, facilities):
        self.id = area_id
        self.nodes = {"0": types.SimpleNamespace(facilities=facilities)}


class World(object):
    def __init__(self, characters, areas):
        self.characters = characters
        self.areas = areas


class App(object):
    def __init__(self):
        self.owner = Character("宿の主", "7")
        self.facility = Facility("5", owner="7")
        self.area = Area("1", {"5": self.facility})
        self.world = World({"7": self.owner}, {"1": self.area})
        self.player = types.SimpleNamespace(current_area=self.area,
                                            location=self.facility)
        self.save_data_dict = {"npcs": {"7": {"name": "宿の主"}}}
        self.world_dict = {"npcs": {"7": {"name": "宿の主"}}}
        self.saved = []

    def save_game(self):
        """保存の瞬間に見えているものを控える。"""
        self.saved.append({
            "roster": sorted(self.world.characters),
            "facility": list(self.facility.characters),
            "owner": self.facility.owner,
            "profile": self.owner.profile,
        })
        return "saved"


class FakeCtx(object):
    def __init__(self, out_dir, generation):
        self.out_dir = out_dir
        self.generation = generation
        self.hooks = {}
        self.errors = []
        self.logs = []

    _mod = "229_probe_mod_npc"

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

    def jsonl(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.jsonl(self, name, **kw)

    def warner(self, tag):
        import instantale_modloader as _ml
        return _ml.ModContext.warner(self, tag)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def superseded(self):
        return False

    def on_ready(self, fn, **kw):
        self.ready = fn            # 走らせない（施設に居る前提の処理）

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            return func
        return decorator


class FakeSaves(object):
    """ディスクのセーブの代わり。`leaked` の見つけ方だけを試す。"""

    def __init__(self, npcs, characters):
        self.data = {"npcs": npcs, "characters": characters}
        self.read = 0

    def world_names(self):
        return [("測定用の世界", "folder")]

    def read_save(self, folder, base=""):
        self.read += 1
        return self.data


def load_mod(name="mod_npc_probe_mod"):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh(generation, npcs=None, characters=None):
    """probe を読み直して `apply()` を通す。`(module, ctx, app, saves)`。"""
    for name in (RECORD_NAME, LOG_NAME):
        path = os.path.join(OUT_DIR, name)
        if os.path.exists(path):
            os.remove(path)
    modnpc.purge()
    # 関所はゲームの `save_game` に当てるもので、偽の環境には無い。
    # `spawn` は関所が今の世代で立っていなければ載せない（実機で踏んだ穴）ので、
    # ここでは立っていることにする。
    modnpc.gate_is_live = lambda: True
    sys.modules["scripts.characters"] = types.SimpleNamespace(
        Character=lambda **kw: _build(**kw))
    module = load_mod()
    saves = FakeSaves(npcs if npcs is not None else {"7": {}},
                      characters if characters is not None else {"7": {}})
    module.saves = saves
    module.state = types.SimpleNamespace(world_key=lambda app: "測定用の世界")
    ctx = FakeCtx(OUT_DIR, generation)
    app = App()
    module.apply(ctx)
    return module, ctx, app, saves


def _build(**kwargs):
    """`Character(**kwargs)` の代役。6鍵が無ければ落ちる。"""
    scores = kwargs.get("original_ability_scores")
    for key in ABILITY_KEYS:
        scores[key]
    character = Character.__new__(Character)
    Character.__init__(character, kwargs.get("name"))
    for key, value in kwargs.items():
        setattr(character, key, value)
    character.current_log = []
    return character


def records():
    """probe が書いた jsonl を1行ずつ読む。"""
    path = os.path.join(OUT_DIR, RECORD_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def at(rows, kind):
    return [row for row in rows if row.get("at") == kind]


def main():
    print("経路: apply() が通り、関所と probe の包みが登録される")
    module, ctx, app, saves = fresh("gen1")
    check("MOD の NPC が登録された",
          module.OWNER in str(modnpc.entries()),
          modnpc.entries())
    check("関所が保存を包む", modnpc.SAVE_TARGET in ctx.hooks)
    check("関所が世界を包む", modnpc.WORLD_TARGET in ctx.hooks)
    check("関所が会話の頼み文を包む",
          "scripts.llm.llm_manager:conversation_starter" in ctx.hooks)
    check("関所がパーティ加入を包む", modnpc.PARTY_TARGET in ctx.hooks)
    check("probe が選択肢の組み直しを包む",
          "__main__:InstantaleApp.refresh_choice_buttons" in ctx.hooks)
    check("例外は出ていない", ctx.errors == [], ctx.errors)

    print("置く: 施設に入ると名簿と施設に載り、主に notes の層が載る")
    refresh = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"][0]
    calls = []
    refresh(lambda self, *a, **kw: calls.append(self) or "orig", app)
    npc_id = modnpc.make_id(module.OWNER, "visitor")
    check("本体は1回だけ呼ばれる", calls == [app], calls)
    check("名簿に載る", npc_id in app.world.characters)
    check("施設に載る", app.facility.characters == [npc_id])
    owner_layer = modnpc._layer_of(module.OWNER, "7")
    check("主に notes の層が載る", owner_layer is not None and callable(owner_layer["notes"]))
    check("本物の profile は触らない", module.OVERRIDE_MARK not in app.owner.profile)
    check("place が録れている", at(records(), "place") != [])
    check("dress が録れている", at(records(), "dress") != [])

    print("仲間: MOD の NPC は加入できない（素データがセーブに無いので組み立てられない）")
    join = ctx.hooks[modnpc.PARTY_TARGET][0]
    joined = []
    check("MOD の NPC は断られる",
          join(lambda self, cid, *a, **kw: joined.append(cid), app, npc_id) is None
          and joined == [])
    check("正規 NPC はそのまま通す",
          join(lambda self, cid, *a, **kw: joined.append(cid) or "joined", app, "7") == "joined"
          and joined == ["7"])

    print("隠す: 保存の包みの中では何も見えない")
    save_hooks = ctx.hooks[modnpc.SAVE_TARGET]
    # 内側が関所、外側が probe（宣言した順に並ぶ）。実機と同じ重なりで通す。
    result = save_hooks[1](lambda self, *a, **kw: save_hooks[0](
        lambda s, *aa, **kk: s.save_game(), self), app)
    seen = app.saved[-1] if app.saved else {}
    check("保存が通る", result == "saved", result)
    check("保存の瞬間、名簿に mod: が無い",
          all(not key.startswith("mod:") for key in seen.get("roster", [])),
          seen.get("roster"))
    check("保存の瞬間、施設の名簿が素", seen.get("facility") == [])
    check("保存の瞬間、主が元の値", seen.get("owner") == "7")
    check("保存の瞬間、被せが素の文",
          module.OVERRIDE_MARK not in seen.get("profile", ""))
    check("保存の後、名簿に戻る", npc_id in app.world.characters)
    check("保存の前後で本物の profile は変わらない",
          app.owner.profile == "この街で宿を継いだ")
    check("セーブを読み直した", saves.read == 1, saves.read)
    disk = at(records(), "disk")
    check("disk が録れている", len(disk) == 1, disk)
    check("漏れていない（セーブ全体で mod: が0件）",
          disk and disk[0]["leaked"] == {"npcs": [], "characters": [], "anywhere": 0},
          disk)

    print("漏れ: セーブに mod: が残っていれば見つける")
    module, ctx, app, saves = fresh("gen2", npcs={"7": {}, "mod:x:y": {}})
    refresh = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"][0]
    refresh(lambda self, *a, **kw: None, app)
    save_hooks = ctx.hooks[modnpc.SAVE_TARGET]
    save_hooks[1](lambda self, *a, **kw: save_hooks[0](
        lambda s, *aa, **kk: s.save_game(), self), app)
    disk = at(records(), "disk")
    check("leaked に出る", disk and disk[0]["leaked"]["npcs"] == ["mod:x:y"], disk)
    # `npcs` の外（選択肢・パーティ・敵）に焼かれた分もセーブ全体の数で捕まえる。
    check("セーブ全体の数にも出る", disk and disk[0]["leaked"]["anywhere"] >= 1, disk)
    check("出どころの鍵が並ぶ", disk and "npcs" in (disk[0]["leaked"].get("where") or []), disk)

    print("会話: 入口を通ると記録が増える")
    module, ctx, app, saves = fresh("gen3")
    refresh = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"][0]
    refresh(lambda self, *a, **kw: None, app)
    start = ctx.hooks[modnpc.CONVERSATION_START_TARGET][0]
    start(lambda self, *a, **kw: "started", types.SimpleNamespace(), app, npc_id)
    check("conversation_start が録れている", at(records(), "conversation_start") != [])

    end = ctx.hooks[modnpc.CONVERSATION_END_TARGET][0]
    manager = types.SimpleNamespace(app=app)
    end(lambda self, *a, **kw: "ended", manager, npc_id)
    ended = at(records(), "conversation_end")
    check("conversation_end が録れている", ended != [])
    check("記憶の件数が載る", ended and ended[0]["current_log"] == 0, ended)

    detail = ctx.hooks[modnpc.DETAIL_TARGET][0]
    passed = []
    detail(lambda self, character, *a, **kw: passed.append(character),
           app, app.world.characters[npc_id])
    rows = at(records(), "detail")
    check("detail が録れている", rows != [])
    check("既定では本体へ通す（素データの写しを置いてから）",
          passed == [app.world.characters[npc_id]]
          and npc_id in app.save_data_dict["npcs"], passed)
    check("素の NPC はそのまま通す",
          detail(lambda self, character, *a, **kw: "generated",
                 app, app.owner) == "generated")

    prompt = ctx.hooks["scripts.llm.llm_manager:conversation_starter"][0]

    seen_profiles = []

    def starter(messages, character_life_log, player, character_instance,
                character_relationship=None, worldview=None, world_story=None,
                area_residency=None, area_achievements=None):
        seen_profiles.append(getattr(character_instance, "profile", ""))
        return "answer"

    # 署名は `orig` ではなく対象名から引く（ローダの `_bind`）ので、
    # 差し込み先のモジュールを偽で置く。
    llm_manager = types.SimpleNamespace(conversation_starter=starter)
    sys.modules["scripts.llm.llm_manager"] = llm_manager
    answer = prompt(starter, [{"content": app.owner.profile}], "", "player",
                    app.owner)
    prompts = at(records(), "prompt")
    check("頼み文がそのまま通る", answer == "answer", answer)
    check("prompt が録れている", prompts != [])
    check("被せの印は本体へ渡る複製の profile に載る（notes）",
          seen_profiles and module.OVERRIDE_MARK in seen_profiles[-1], seen_profiles)
    check("本物の profile には載らない", module.OVERRIDE_MARK not in app.owner.profile)

    print("内側に別 MOD のラッパが居ても名前で引ける")
    # 実機 2026-09-12: `conversation_starter` には9本が載っていて、229 の `orig` は
    # 内側 MOD の `(*args, **kwargs)`。署名は素の関数から取らないと名前が1つも引けない。
    def inner(*args, **kwargs):
        return starter(*args, **kwargs)
    inner.__original__ = starter
    llm_manager.conversation_starter = inner
    before = len(at(records(), "prompt"))
    answer = prompt(inner, [{"content": app.owner.profile}], "", "player",
                    app.owner)
    check("内側のラッパ越しでも通る", answer == "answer", answer)
    check("内側のラッパ越しでも録れる",
          len(at(records(), "prompt")) == before + 1)

    sys.modules.pop("scripts.llm.llm_manager", None)
    modnpc.purge()
    print("PASS" if not failures else "FAIL: " + ", ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
