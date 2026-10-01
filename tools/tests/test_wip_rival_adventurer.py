# -*- coding: utf-8 -*-
"""911_rival_adventurer をゲーム抜きで通す。

    python tools/tests/test_wip_rival_adventurer.py

偽の app / World / Area / Character / 依頼を差し込み、次を確認する。

  部品     … いちばん Lv の近い冒険者を選ぶ／手の届く依頼だけ狙う／成功率は 10〜95% に収まる
             ／態度の段は勝ち数と好感度で上がり、下がらない／記録は新しい順に3件
             ／添え字は題名の括弧を削らずに剥がせる
  現れる   … 掲示板では現れない。片付けた依頼が2件に届いてから、片付けるたびに抽選する。
             外れたら次の片付けでまた引く
  語り     … 文型（LLM 切・失敗）か LLM の語りを1度だけ出す。317 の二つ名、性格・功績を頼み文に使う。
             張り合う理由を本人に渡す
  狙う     … その街の依頼に狙いを付ける。
             物語の依頼・片付いた依頼・301 が会話から作った依頼は狙わない
  掲示板   … 狙われた依頼に添え字が付く。描き直しても増えない。狙いを知らせる文は1度だけ
  先を越す … 期限が来るとライバルが片付け、依頼は掲示板から消える。status は書かない。
             未完了の数から引く。Lv が上がる（実行時と素データ）。知らせる文は1度だけ
  しくじる … 外れたら依頼は残り、休みの間は狙わない。設定で切れば必ず片付ける
  先取り   … 狙われた依頼をプレイヤーが片付けたらプレイヤーの勝ち。態度が1段和らぎ、知らせる
  和らぐ   … 好感度で態度が上がる。知らせるのは次に掲示板を開いたとき。下がらない
  同行     … ライバルが仲間になったら狙いを取り下げる
  居なくなる … 死んだら台帳を空にし、次の片付けで別の冒険者を引く
  会話     … ライバル本人には理由・態度・勝敗・最近の取り合い・狙い、その街の住人には噂を足す
  ロード   … 台帳より古いセーブなら、その後の出来事を忘れる
  控え     … state/rival_adventurer/<世界×主人公>.json
"""
import importlib.util
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state_rival_adventurer")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import modnpc, state, talk_affinity, ui  # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(
            suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_rival_adventurer")
MOD_NAME = "rival_adventurer_mod"

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


class Area:
    def __init__(self, area_id, name, adventurer_npcs=()):
        self.id = area_id
        self.name = name
        self.nodes = {}
        self.adventurer_npcs = list(adventurer_npcs)


class Character:
    def __init__(self, character_id, name, level):
        self.id = character_id
        self.name = name
        self.experience_level = level
        self.profile = "{}の経歴。".format(name)
        self.config = {"is_dead": False}


class World:
    def __init__(self, areas, characters, quests, days):
        self.areas = areas
        self.characters = characters
        self.quests = quests
        self.days_elapsed = days


class Player:
    def __init__(self, area, level):
        self.current_area = area
        self.experience_level = level
        self.name = "検査の主人公"


class InstantaleApp:
    """`__main__` のクラスとして `ui.find_app()` に見つけてもらう。"""

    def __init__(self, world, player, save_data_dict):
        self.world = world
        self.player = player
        self.save_data_dict = save_data_dict
        self.world_dict = save_data_dict
        self.game_variables = {"party": ["player"]}
        self.current_quest_data = None
        self.texts = []
        self.buttons = []

    def add_text(self, text):
        self.texts.append(text)

    def elapse_days(self, days):
        self.world.days_elapsed += days
        self.save_data_dict["world_data"]["days_elapsed"] = self.world.days_elapsed

    def refresh_choice_buttons(self, *args, **kwargs):
        return None

    def process_choice(self, function, choice_text=""):
        self.chosen = getattr(self, "chosen", [])
        self.chosen.append((function, choice_text))


class ConversationStartManager:
    """ゲームの会話の始まり。`ui.cls_of` に見つけてもらう。"""

    def __init__(self, app, character_id):
        self.app = app
        self.character_id = character_id


class Facility:
    def __init__(self, facility_id, facility_type, name):
        self.id = facility_id
        self.facility_type = facility_type
        self.name = name


class Phase:
    def __init__(self, app):
        self.app = app


class Board:
    """`DisplayQuestChoice` の代役。"""

    def __init__(self, app):
        self.app = app

    def get_active_quest_count(self):
        area_id = ui.area_id_of(ui.current_area(self.app))
        # 物語の依頼はゲームでは `story_quests` の側に居て数に入らない（GAME.md §2.9）。
        return sum(1 for quest in self.app.world.quests.values()
                   if quest["neighboring_settlement_id"] == area_id
                   and quest["config"]["status"] == "incomplete"
                   and quest["quest_type"] != "story_quest")


class QuestEnd:
    def __init__(self, app):
        self.app = app


APP = None


class FakeCtx:
    def __init__(self, out_dir, state_dir):
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

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

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        """同じ対象に何本でも積む（modnpc の関所も包む）。この MOD のものは最後。"""
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            return func
        return decorator

    def hook(self, target):
        return self.hooks[target][-1]


class FixedRng:
    """`random()` だけ決まった値を返す乱数。ほかは本物に任せる。"""

    def __init__(self, value, seed=1):
        import random
        self.value = value
        self.inner = random.Random(seed)

    def random(self):
        return self.value

    def __getattr__(self, name):
        return getattr(self.inner, name)


def load_mod():
    for name in (MOD_NAME, MOD_NAME + ".rivalry"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(roll=0.0, keep_state=False, **settings):
    """MOD を読み直して apply する。`roll` は成否の乱数（小さいほど成功）。

    既定でライバルは必ず現れ（確率 100%）、語りは文型（LLM 切）にする。
    """
    settings.setdefault("RIVAL_CHANCE_PERCENT", 100)
    settings.setdefault("INTRO_USE_LLM", False)
    module = load_mod()
    if not keep_state:
        if hasattr(sys, module.STORE_ATTR):
            delattr(sys, module.STORE_ATTR)
        if os.path.isdir(STATE_DIR):
            shutil.rmtree(STATE_DIR)
    modnpc.registry().clear()
    for name, value in settings.items():
        setattr(module, name, value)
    module._store()["rng"] = FixedRng(roll)
    ctx = FakeCtx(OUT_DIR, STATE_DIR)
    module.apply(ctx)
    return module, ctx


# 手が空くのを待たずにその場で走らせる（Kivy の Clock が無いので）。
ui.Screen.when_idle = lambda self, app, then, **kw: then()


# ---------------------------------------------------------------- 舞台作り
WORLD_NAME = "ライバルの検査世界"


def quest(quest_id, area_id, title, difficulty, status="incomplete", kind="normal_quest"):
    return {"quest_title": title, "difficulty": difficulty,
            "neighboring_settlement_id": area_id, "quest_type": kind, "id": quest_id,
            "config": {"status": status, "level_of_detail": 1}}


def make_world(days=100, player_level=30, cleared=2):
    """街0（冒険者3人・依頼3件）と街1（冒険者1人・依頼1件）。片付いた依頼を `cleared` 件。"""
    areas = {"0": Area("0", "始まりの街", ["10", "11", "12"]),
             "1": Area("1", "灰の都", ["13"])}
    levels = {"10": 5, "11": 28, "12": 60, "13": 33}
    characters = {npc_id: Character(npc_id, "冒険者{}".format(npc_id), level)
                  for npc_id, level in levels.items()}
    characters["11"].personality = "負けず嫌い"
    quests = {
        "1": quest("1", "0", "古城（北）の亡霊", 30),
        "3": quest("3", "0", "物語の依頼", 25, kind="story_quest"),
        "4": quest("4", "0", "竜の巣", 70),
        "5": quest("5", "1", "灰の狼", 31),
    }
    for index in range(cleared):
        qid = str(90 + index)
        quests[qid] = quest(qid, "1", "片付いた依頼{}".format(index), 20, status="completed")
    npcs = {npc_id: {"name": c.name, "experience_level": c.experience_level,
                     "config": {"is_dead": False}}
            for npc_id, c in characters.items()}
    save = {"world_data": {"world_name": WORLD_NAME, "days_elapsed": days,
                           "overview": "剣と魔法の世界"},
            "player_data": {"name": "検査の主人公"},
            "npcs": npcs}
    world = World(areas, characters, quests, days)
    app = InstantaleApp(world, Player(areas["0"], player_level), save)
    app.player.area_history = {"1": {"achievements": ["検査の主人公が灰の狼の群れを退けた。"]}}
    return app


def use(app):
    global APP
    APP = app


def finish_quest(ctx, module, app, quest_id=None):
    """依頼を1件片付ける（`QuestEndManager.execute`）。背景の語りも待つ。"""
    app.current_quest_data = app.world.quests.get(quest_id) if quest_id else None

    def finish(self):
        app.current_quest_data = None
        if quest_id:
            app.world.quests[quest_id]["config"]["status"] = "completed"

    ctx.hook("__main__:QuestEndManager.execute")(finish, QuestEnd(app))
    worker = module._store()["worker"]
    if worker is not None:
        worker.drain(10.0)


def appear(ctx, module, app):
    """ライバルを出す（片付けた依頼が足りている世界で、もう1件片付けて当てる）。"""
    app.world.quests["80"] = quest("80", "1", "通りがかりの依頼", 10)
    finish_quest(ctx, module, app, "80")


def open_board(ctx, app):
    """掲示板を開き、依頼のボタンを並べて描き直す（ゲームの順）。"""
    ctx.hook("__main__:DisplayQuestChoice.__init__")(lambda self, app: None, Board(app), app)
    area_id = ui.area_id_of(ui.current_area(app))
    app.buttons = [{"text": q["quest_title"],
                    "spec": PhaseSpec("QuestChoiceManager", ["settlement_quest", qid])}
                   for qid, q in sorted(app.world.quests.items())
                   if q["neighboring_settlement_id"] == area_id
                   and q["config"]["status"] == "incomplete"
                   and q["quest_type"] != "story_quest"]
    app.buttons.append({"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])})
    redraw(ctx, app)


def redraw(ctx, app):
    ctx.hook("__main__:InstantaleApp.refresh_choice_buttons")(
        InstantaleApp.refresh_choice_buttons, app)


def board_texts(app):
    return [entry["text"] for entry in app.buttons]


def active_count(ctx, app):
    return ctx.hook("__main__:DisplayQuestChoice.get_active_quest_count")(
        Board.get_active_quest_count, Board(app))


def elapse(ctx, app, days):
    ctx.hook("__main__:InstantaleApp.elapse_days")(InstantaleApp.elapse_days, app, days)


def bucket_of(module, app):
    store = module._store()["worlds"]
    return store.load(store.playthrough(app))


def notes_for(app, npc_id):
    layer = [l for l in modnpc.layers(modnpc.ANY) if l["owner"].endswith("_rival_adventurer")]
    return layer[0]["notes"]({"app": app, "npc_id": npc_id, "site": "conversation_starter"})


def ready(**settings):
    """MOD を読み、世界を作り、ライバルを出したところまで。"""
    module, ctx = fresh_mod(**settings)
    app = make_world()
    use(app)
    appear(ctx, module, app)
    return module, ctx, app


# ---------------------------------------------------------------- 場面
def scene_pure():
    print("[部品]")
    module = load_mod()
    rivalry = sys.modules[MOD_NAME + ".rivalry"]
    rng = FixedRng(0.0)
    check("いちばん Lv の近い人", rivalry.pick_rival([("1", 5), ("2", 28), ("3", 60)], 30, rng) == "2")
    check("Lv が読めない人は選ばない", rivalry.pick_rival([("1", None)], 30, rng) is None)
    check("手の届く依頼だけ", rivalry.pick_target([("a", 50), ("b", 70)], 40, 10, rng) == "a")
    check("届く依頼が無ければ狙わない", rivalry.pick_target([("b", 70)], 40, 10, rng) is None)
    check("成功率の上限", rivalry.success_chance(99, 1, 60, 3) == 0.95)
    check("成功率の下限", rivalry.success_chance(1, 99, 60, 3) == 0.10)
    check("同じ Lv なら基準", rivalry.success_chance(30, 30, 60, 3) == 0.60)
    check("勝ち数から見た段", [rivalry.stance_from_wins(n, 1) for n in (0, 1, 2, 5)] == [0, 1, 2, 3])
    check("好感度から見た段", [rivalry.stance_from_affinity(a, 30) for a in (0, 29, 30, 65, 200)]
          == [0, 0, 1, 2, 3])
    check("好感度が読めなければ 0", rivalry.stance_from_affinity(None, 30) == 0)
    bucket = rivalry.new_bucket()
    bucket["rival"] = {"id": "1", "stance": 2}
    check("段は下げない", rivalry.raise_stance(bucket, 1) is None and rivalry.stance_of(bucket) == 2)
    for n in range(5):
        rivalry.push_history(bucket, "依頼{}".format(n), rivalry.RIVAL_WON, n, 3)
    check("記録は新しい順に3件", [r["title"] for r in rivalry.history_of(bucket)]
          == ["依頼4", "依頼3", "依頼2"])
    rivalry.push_topic(bucket, rivalry.TOPIC_INTRO, "", 0, 3)
    for kind in (rivalry.TOPIC_TAKEN, rivalry.TOPIC_LOST, rivalry.TOPIC_CLEANUP):
        rivalry.push_topic(bucket, kind, "依頼", 1, 3)
    check("初対面の種はほかの種に押し出されない",
          [t["kind"] for t in rivalry.topics_of(bucket)] == ["cleanup", "lost", "taken", "intro"]
          and rivalry.next_topic(bucket)["kind"] == "intro",
          rivalry.topics_of(bucket))
    rivalry.push_topic(bucket, rivalry.TOPIC_TAKEN, "依頼2", 2, 2)
    check("初対面のほかは上限まで（新しい順）",
          [t["kind"] for t in rivalry.topics_of(bucket)] == ["taken", "cleanup", "intro"],
          rivalry.topics_of(bucket))
    rivalry.taken_of(bucket)["5"] = {"area": "0", "day": 100, "title": "依頼5"}
    check("当日の決着は噂になる", [q for q, _row, _ago in rivalry.rumors_in(bucket, "0", 100, 90)]
          == ["5"])
    check("日数 0 なら当日の決着も噂にしない", rivalry.rumors_in(bucket, "0", 100, 0) == [])
    pattern =module.suffix_pattern(module.TARGET_SUFFIX)
    marked = "古城（北）の亡霊" + rivalry.format_text(module.TARGET_SUFFIX, name="冒険者11", days=12)
    check("添え字だけ剥がす（題名の括弧は残す）", pattern.sub("", marked) == "古城（北）の亡霊",
          pattern.sub("", marked))


def scene_draw():
    print("[現れる]")
    talk_affinity.reset()
    module, ctx = fresh_mod()
    app = make_world(cleared=0)
    use(app)
    open_board(ctx, app)
    check("掲示板を開いてもライバルは現れない", bucket_of(module, app).get("rival") is None)
    check("ライバルが居なければ会話の上限に口を出さない",
          talk_affinity.ceiling(app, "11", 60) == (60, None))
    app.world.quests["81"] = quest("81", "1", "一件目", 10)
    finish_quest(ctx, module, app, "81")
    check("1件目では抽選しない", bucket_of(module, app).get("rival") is None)
    app.world.quests["82"] = quest("82", "1", "二件目", 10)
    finish_quest(ctx, module, app, "82")
    rival = bucket_of(module, app).get("rival") or {}
    check("2件目で抽選して当たる", rival.get("id") == "11", rival)
    check("最初は見下している", rival.get("stance") == 0, rival)
    check("ライバルとの会話で上げられるのは 30 まで",
          talk_affinity.ceiling(app, "11", 60) == (30, module.OWNER),
          talk_affinity.ceiling(app, "11", 60))
    check("ライバル以外には口を出さない", talk_affinity.ceiling(app, "12", 60) == (60, None))
    check("上限は狭めるだけ", talk_affinity.ceiling(app, "11", 20) == (20, None))

    module, ctx = fresh_mod(RIVAL_CHANCE_PERCENT=0)
    app = make_world(cleared=5)
    use(app)
    for qid in ("83", "84", "85"):
        app.world.quests[qid] = quest(qid, "1", "依頼" + qid, 10)
        finish_quest(ctx, module, app, qid)
    check("外れ続ければ現れない", bucket_of(module, app).get("rival") is None)
    module.RIVAL_CHANCE_PERCENT = 100
    app.world.quests["86"] = quest("86", "1", "依頼86", 10)
    finish_quest(ctx, module, app, "86")
    check("外れた後も次の片付けでまた引く", (bucket_of(module, app).get("rival") or {}).get("id") == "11")
    check("例外が無い", not ctx.errors, ctx.errors)


def scene_intro():
    print("[登場の語り]")
    module, ctx, app = ready()
    rival = bucket_of(module, app)["rival"]
    check("文型の語りが出る（LLM 切）", any("値踏み" in t for t in app.texts), app.texts)
    check("二つ名が無ければ活躍の噂", "活躍の噂" in rival.get("intro", ""), rival.get("intro"))
    check("理由も文型で入る", "格の違い" in (rival.get("reason") or ""), rival.get("reason"))
    before = len(app.texts)
    open_board(ctx, app)
    check("語りは1度だけ", not any("値踏み" in t for t in app.texts[before:]), app.texts[before:])

    # 317 の二つ名があれば使う。317 の控えは周回（世界×主人公）ごとなので周回の鍵で読む。
    # 世界名だけの控えは読まない（移すのは 317。移す前に読むと前の主人公の名を使いうる）。
    # 控えのフォルダは `fresh_mod` が消すので、当て直した後に作る。
    folder = os.path.join(STATE_DIR, "reputation")
    module, ctx = fresh_mod()
    app = make_world()
    use(app)
    os.makedirs(folder, exist_ok=True)
    with io.open(os.path.join(folder, state.world_filename(state.world_key(app))), "w",
                 encoding="utf-8") as fh:
        json.dump({"epithet": {"epithet": "前の主人公の名"}}, fh, ensure_ascii=False)
    appear(ctx, module, app)
    rival = bucket_of(module, app)["rival"]
    check("世界名だけの 317 の控えは読まない",
          "前の主人公の名" not in rival.get("intro", ""), rival.get("intro"))
    module, ctx = fresh_mod()
    app = make_world()
    use(app)
    os.makedirs(folder, exist_ok=True)
    with io.open(os.path.join(folder, state.world_filename(state.playthrough_key(app))), "w",
                 encoding="utf-8") as fh:
        json.dump({"epithet": {"epithet": "秩序の剣"}}, fh, ensure_ascii=False)
    appear(ctx, module, app)
    rival = bucket_of(module, app)["rival"]
    check("二つ名を語りに使う", "「秩序の剣」の二つ名" in rival.get("intro", ""), rival.get("intro"))

    # LLM が書いた語りと理由
    module, ctx = fresh_mod(INTRO_USE_LLM=True)
    asked = []

    def fake_ask(ctx_, name, messages, **kw):
        asked.append(messages[0]["content"])
        return {"narration": "掲示板の前で、冒険者11が鼻で笑った。「噂ほどじゃなさそうだな」",
                "reason": "噂ばかり先行する新顔に格の違いを見せつけたいから"}

    saved = (module.llm.ask, module.llm.create_structure)
    module.llm.ask = fake_ask
    module.llm.create_structure = lambda *a, **k: object()
    try:
        app = make_world()
        use(app)
        appear(ctx, module, app)
        rival = bucket_of(module, app)["rival"]
        check("LLM に1回だけ頼む", len(asked) == 1, len(asked))
        check("頼み文に人物の性格", asked and "負けず嫌い" in asked[0], asked[:1])
        check("頼み文に功績の文", asked and "灰の狼の群れを退けた" in asked[0], asked[:1])
        check("LLM の語りが出る", any("鼻で笑った" in t for t in app.texts), app.texts)
        check("理由の「から」を落とす",
              rival.get("reason") == "噂ばかり先行する新顔に格の違いを見せつけたい", rival.get("reason"))
        note = notes_for(app, "11")
        check("理由が本人に渡る",
              note and "理由は、噂ばかり先行する新顔に格の違いを見せつけたいから。" in note, note)

        module.llm.ask = lambda *a, **k: None
        module, ctx = fresh_mod(INTRO_USE_LLM=True)
        app = make_world()
        use(app)
        appear(ctx, module, app)
        check("LLM が返らなければ文型", "値踏み" in bucket_of(module, app)["rival"].get("intro", ""))
        check("例外が無い", not ctx.errors, ctx.errors)
    finally:
        # `module.llm` はローダのモジュールそのもの。後の場面に持ち越さない。
        module.llm.ask, module.llm.create_structure = saved


def scene_aim():
    print("[狙う・掲示板]")
    module, ctx, app = ready()
    open_board(ctx, app)
    bucket = bucket_of(module, app)
    target = bucket.get("target") or {}
    check("その街の手の届く依頼を狙う", target.get("quest") == "1", target)
    check("期限は 20〜40 日後", 120 <= target.get("due_day", 0) <= 140, target)
    texts = board_texts(app)
    check("狙われた依頼に添え字", texts[0].startswith("古城（北）の亡霊（冒険者11が狙っている"), texts)
    redraw(ctx, app)
    check("描き直しても添え字は1つ", board_texts(app)[0].count("が狙っている") == 1, board_texts(app))
    check("狙いを知らせる文が出る", any("目を付けた" in t for t in app.texts), app.texts)
    before = len(app.texts)
    open_board(ctx, app)
    check("知らせる文は1度だけ", len(app.texts) == before, app.texts[before:])
    check("例外が無い", not ctx.errors, ctx.errors)


def scene_skip_conversation_quest():
    print("[会話から作った依頼は狙わない]")
    # 301 の控えは周回（世界×主人公）の鍵の下を読む。世界名だけの鍵の分は読まない
    # （移すのは 301。依頼の id は作り直した周回で振り直されるので、前の主人公の分かもしれない）。
    clients = os.path.join(STATE_DIR, "quest_clients.json")
    module, ctx = fresh_mod()
    app = make_world()
    use(app)
    os.makedirs(STATE_DIR, exist_ok=True)
    with io.open(clients, "w", encoding="utf-8") as fh:
        json.dump({state.world_key(app): {"1": {"npc_id": "10", "npc_name": "依頼人"}}},
                  fh, ensure_ascii=False)
    appear(ctx, module, app)
    open_board(ctx, app)
    target = bucket_of(module, app).get("target")
    check("世界名だけの鍵の 301 の控えは読まない", target is not None, target)
    module, ctx = fresh_mod()
    app = make_world()
    use(app)
    os.makedirs(STATE_DIR, exist_ok=True)
    with io.open(clients, "w", encoding="utf-8") as fh:
        json.dump({state.playthrough_key(app): {"1": {"npc_id": "10", "npc_name": "依頼人"}}},
                  fh, ensure_ascii=False)
    appear(ctx, module, app)
    open_board(ctx, app)
    target = bucket_of(module, app).get("target")
    check("301 の依頼しか届かないので狙わない", target is None, target)


def scene_taken():
    print("[先を越す]")
    module, ctx, app = ready()
    open_board(ctx, app)
    check("掲示板を開いた時点の未完了は2件", active_count(ctx, app) == 2, active_count(ctx, app))
    elapse(ctx, app, 45)
    bucket = bucket_of(module, app)
    check("ライバルが片付けた", "1" in bucket.get("taken", {}), bucket)
    check("依頼の status は書かない", app.world.quests["1"]["config"]["status"] == "incomplete")
    check("Lv が上がる（実行時）", app.world.characters["11"].experience_level == 29)
    check("Lv が上がる（素データ）", app.save_data_dict["npcs"]["11"]["experience_level"] == 29)
    check("勝ち数", bucket["score"]["rival"] == 1, bucket["score"])
    check("取り合いの記録", bucket["history"][0]["outcome"] == "rival", bucket["history"])
    open_board(ctx, app)
    check("掲示板から消える", not any(t.startswith("古城") for t in board_texts(app)), board_texts(app))
    check("未完了の数から引く", active_count(ctx, app) == 1, active_count(ctx, app))
    check("先を越された文が出る", any("剥がされている" in t for t in app.texts), app.texts)
    before = len(app.texts)
    open_board(ctx, app)
    check("先を越された文は1度だけ", not any("剥がされている" in t for t in app.texts[before:]),
          app.texts[before:])
    check("間を置く間は次を狙わない", bucket.get("target") is None, bucket.get("target"))
    elapse(ctx, app, 10)
    check("間が明けても、届かない依頼（竜の巣 70）しか無ければ狙わない",
          bucket.get("target") is None, bucket.get("target"))
    app.world.quests["6"] = quest("6", "0", "街道の盗賊", 35)
    elapse(ctx, app, 1)
    check("届く依頼が出れば次を狙う", (bucket.get("target") or {}).get("quest") == "6",
          bucket.get("target"))
    check("例外が無い", not ctx.errors, ctx.errors)


def scene_failure():
    print("[しくじる]")
    module, ctx, app = ready(roll=0.99)
    open_board(ctx, app)
    elapse(ctx, app, 45)
    bucket = bucket_of(module, app)
    check("外れたら依頼は残る", "1" not in bucket.get("taken", {}), bucket.get("taken"))
    check("休みに入る", bucket.get("away_until") == 175, bucket.get("away_until"))
    check("Lv は上がらない", app.world.characters["11"].experience_level == 28)
    open_board(ctx, app)
    check("休みの間は狙わない", bucket.get("target") is None, bucket.get("target"))
    check("しくじった文が出る", any("しくじって" in t for t in app.texts), app.texts)
    note = notes_for(app, "11")
    check("本人は怪我を知っている", note and "怪我" in note, note)
    check("本人はしくじった依頼を覚えている",
          note and "「古城（北）の亡霊」はあなたがしくじった" in note, note)

    module, ctx, app = ready(roll=0.99, FAILURE_ENABLED=False)
    open_board(ctx, app)
    elapse(ctx, app, 45)
    check("切れば必ず片付ける", "1" in bucket_of(module, app).get("taken", {}))


def scene_player_wins():
    print("[先取り・態度]")
    module, ctx, app = ready()
    open_board(ctx, app)
    note = notes_for(app, "11")
    check("初めは見下す文", note and "見下していて" in note, note)
    finish_quest(ctx, module, app, "1")
    bucket = bucket_of(module, app)
    check("プレイヤーの勝ち", bucket["score"]["player"] == 1, bucket["score"])
    check("狙いは消える", bucket.get("target") is None)
    check("先に片付けた文が出る", any("先に片付けた" in t for t in app.texts), app.texts)
    check("1勝で態度が1段和らぐ", bucket["rival"]["stance"] == 1, bucket["rival"])
    check("和らいだことを知らせる", any("棘が抜けた" in t for t in app.texts), app.texts)
    note = notes_for(app, "11")
    check("一目置く文に替わる", note and "認め始めていて" in note and "見下して" not in note, note)
    check("本人は先を越された依頼を覚えている",
          note and "「古城（北）の亡霊」は検査の主人公に先を越された" in note, note)
    before = len(app.texts)
    open_board(ctx, app)
    check("和らいだ知らせは繰り返さない", not any("棘が抜けた" in t for t in app.texts[before:]),
          app.texts[before:])
    elapse(ctx, app, 45)
    check("片付いた依頼は取られない", "1" not in bucket.get("taken", {}))

    print("[尻拭い]")
    module, ctx, app = ready(roll=0.99)
    open_board(ctx, app)
    elapse(ctx, app, 45)
    check("ライバルがしくじった", (bucket_of(module, app).get("failure") or {}).get("quest") == "1")
    finish_quest(ctx, module, app, "1")
    bucket = bucket_of(module, app)
    check("尻拭いの文が出る", any("代わりに片付けた" in t for t in app.texts), app.texts)
    check("尻拭いで1段和らぐ", bucket["rival"]["stance"] == 1, bucket["rival"])
    check("和らいだことを知らせる", any("棘が抜けた" in t for t in app.texts), app.texts)
    check("先取りには数えない", bucket["score"]["player"] == 0, bucket["score"])
    note = notes_for(app, "11")
    check("本人は尻拭いされたことを覚えている",
          note and "「古城（北）の亡霊」はあなたがしくじった後、検査の主人公に片付けられた" in note, note)
    app.world.quests["1"]["config"]["status"] = "incomplete"
    finish_quest(ctx, module, app, "1")
    check("1つのしくじりにつき1度だけ", bucket["rival"]["stance"] == 1, bucket["rival"])

    module, ctx, app = ready(roll=0.99, CLEANUP_SOFTENS=False)
    open_board(ctx, app)
    elapse(ctx, app, 45)
    finish_quest(ctx, module, app, "1")
    bucket = bucket_of(module, app)
    check("切れば尻拭いでは和らがない", bucket["rival"]["stance"] == 0, bucket["rival"])
    check("切っても尻拭いの文は出る", any("代わりに片付けた" in t for t in app.texts), app.texts)

    print("[掲示板の経路で和らいだ知らせ]")
    module, ctx, app = ready()
    open_board(ctx, app)
    app.world.quests["1"]["config"]["status"] = "completed"   # 依頼の終わりの包みを通らずに片付いた
    elapse(ctx, app, 1)
    bucket = bucket_of(module, app)
    check("先取りに数える", bucket["score"]["player"] == 1 and bucket["rival"]["stance"] == 1,
          bucket["score"])
    check("知らせはまだ出していない印のまま", bucket["rival"]["stance_told"] is False, bucket["rival"])
    open_board(ctx, app)
    check("次に掲示板を開いたときに知らせる", any("棘が抜けた" in t for t in app.texts), app.texts)

    print("[会話で和らぐ]")
    module, ctx, app = ready()
    app.world.characters["11"].relationship = {"player": {"affinity": 65}}
    note = notes_for(app, "11")
    bucket = bucket_of(module, app)
    check("好感度 65 で2段", bucket["rival"]["stance"] == 2, bucket["rival"])
    check("会話の文にすぐ効く", note and "対等な好敵手" in note, note)
    check("会話の最中には知らせない", not any("格下とは見ていない" in t for t in app.texts))
    open_board(ctx, app)
    check("次に掲示板を開いたときに知らせる", any("格下とは見ていない" in t for t in app.texts),
          app.texts)
    app.world.characters["11"].relationship = {"player": {"affinity": 0}}
    notes_for(app, "11")
    check("好感度が下がっても段は戻らない", bucket["rival"]["stance"] == 2)


def scene_party():
    print("[同行]")
    module, ctx, app = ready()
    open_board(ctx, app)
    app.game_variables["party"].append("11")
    elapse(ctx, app, 5)
    bucket = bucket_of(module, app)
    check("仲間になったら狙いを取り下げる", bucket.get("target") is None, bucket.get("target"))
    check("ライバルはそのまま", (bucket.get("rival") or {}).get("id") == "11")
    check("同行中は噂を足さない", notes_for(app, "10") is None)


def scene_gone():
    print("[居なくなる]")
    module, ctx, app = ready()
    open_board(ctx, app)
    elapse(ctx, app, 45)
    check("死ぬ前に依頼を1件片付けている", "1" in bucket_of(module, app)["taken"])
    check("片付けた依頼に名前を残す", bucket_of(module, app)["taken"]["1"].get("by") == "冒険者11")
    app.world.characters["11"].config["is_dead"] = True
    elapse(ctx, app, 1)
    bucket = bucket_of(module, app)
    check("死んだら台帳を空にする", bucket.get("rival") is None and bucket["score"]["rival"] == 0
          and bucket["history"] == [], bucket)
    check("片付けた依頼は残す", "1" in bucket["taken"], bucket["taken"])
    open_board(ctx, app)
    check("掲示板には戻らない", not any(t.startswith("古城") for t in board_texts(app)), board_texts(app))
    rumor = notes_for(app, "10")
    check("ライバルが居なくても噂は残る", rumor and "冒険者11" in rumor, rumor)
    app.world.quests["87"] = quest("87", "1", "依頼87", 10)
    finish_quest(ctx, module, app, "87")
    rival = bucket_of(module, app).get("rival") or {}
    check("次の片付けで別の冒険者を引く", rival.get("id") in ("10", "12", "13"), rival)
    check("選び直しても片付けた依頼は残す", "1" in bucket_of(module, app)["taken"])
    rumor = notes_for(app, "12" if rival.get("id") != "12" else "10")
    check("噂は片付けた本人の名前のまま", rumor and "冒険者11" in rumor, rumor)


def scene_notes():
    print("[会話]")
    module, ctx, app = ready()
    open_board(ctx, app)
    note = notes_for(app, "11")
    check("本人に勝敗", note and "0回" in note, note)
    check("本人に狙い", note and "古城（北）の亡霊" in note, note)
    check("片付ける前は噂が無い", notes_for(app, "10") is None)
    elapse(ctx, app, 45)
    rumor = notes_for(app, "10")
    check("その街の住人に噂", rumor and "冒険者11" in rumor and "古城（北）の亡霊" in rumor, rumor)
    app.player.current_area = app.world.areas["1"]
    check("別の街では噂にしない", notes_for(app, "13") is None)


def scene_load():
    print("[ロード]")
    module, ctx, app = ready()
    open_board(ctx, app)
    elapse(ctx, app, 45)
    check("片付けた状態", "1" in bucket_of(module, app).get("taken", {}))
    # 片付ける前の日付のセーブを読んだ
    module, ctx = fresh_mod(keep_state=True)
    old = make_world(days=110)
    use(old)
    ctx.hook("__main__:InstantaleApp.load_game_new")(lambda self: None, old)
    redraw(ctx, old)
    bucket = bucket_of(module, old)
    check("後の出来事を忘れる", "1" not in bucket.get("taken", {}), bucket.get("taken"))
    check("記録も巻き戻す", bucket.get("history") == [], bucket.get("history"))
    check("忘れた決着が決めた「次に狙ってよい日」も忘れる", bucket.get("next_day") is None,
          bucket.get("next_day"))
    check("ライバルは残る", (bucket.get("rival") or {}).get("id") == "11")


def scene_store():
    print("[控え]")
    module, ctx, app = ready()
    open_board(ctx, app)
    key = state.playthrough_key(app)
    path = os.path.join(STATE_DIR, "rival_adventurer", state.world_filename(key))
    check("控えのファイル", os.path.isfile(path), path)
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    check("鍵の並び", list(data) == ["rival", "target", "taken", "failure", "score", "history",
                                    "topics", "away_until", "next_day"], list(data))
    check("ライバルの鍵の並び", list(data["rival"])[:4] == ["id", "name", "chosen_day", "stance"],
          list(data["rival"]))


def arrive(ctx, app, facility, area="0", other=None):
    """施設に着く。ローダの包みの番号送りは手で行う（検査の ctx の包みは最後の1つしか呼ばない）。"""
    from instantale_modloader import arrivals
    app.player.location = facility
    app.player.current_area = app.world.areas[area]
    app.buttons = []
    app.chosen = []
    arrivals._store()["serial"] += 1
    if other is not None:
        arrivals.offer(app, other[0], other[1])
    ctx.hook("__main__:MovePhaseManager.move_phase")(lambda self: None, Phase(app))
    return app.chosen


def opening(ctx, app, npc_id="11"):
    """第一声の頼み文に渡る最後の1件。"""
    seen = {}
    ctx.hook("scripts.llm.llm_manager:conversation_starter")(
        lambda messages, *a, **k: seen.setdefault("messages", messages),
        [{"role": "user", "content": "<行動: 話しかける>"}], {}, app.player,
        app.world.characters[npc_id])
    return seen["messages"][-1]["content"]


def scene_approach():
    print("[ギルドで声をかける]")
    from instantale_modloader import arrivals
    arrivals.reset()
    guild = Facility("g0", "guild", "始まりの会館")
    inn = Facility("i0", "inn", "灯り亭")
    module, ctx, app = ready()
    check("初対面の種がある", [t["kind"] for t in bucket_of(module, app)["topics"]] == ["intro"])
    check("宿では声をかけない", not arrive(ctx, app, inn))
    chosen = arrive(ctx, app, guild)
    check("ギルドに入ると声をかける", len(chosen) == 1 and chosen[0][0].character_id == "11"
          and chosen[0][1] == "冒険者11", chosen)
    line = opening(ctx, app)
    check("第一声はライバルの方から", "あなたの方から声をかけた" in line and "名乗りを上げに来た" in line,
          line)
    check("読み替えは1回だけ", opening(ctx, app) == "<行動: 話しかける>")
    check("種は1回で使い切る", bucket_of(module, app)["topics"] == [])
    check("種が無ければ声をかけない", not arrive(ctx, app, guild))
    check("ほかの人との会話の第一声は変えない", opening(ctx, app, "10") == "<行動: 話しかける>")

    open_board(ctx, app)
    finish_quest(ctx, module, app, "1")
    chosen = arrive(ctx, app, guild)
    check("先を越されたら言いに来る", len(chosen) == 1, chosen)
    line = opening(ctx, app)
    check("用件は先を越された依頼", "古城（北）の亡霊" in line and "先に片付けられ" in line, line)

    print("[声をかけない・譲る]")
    module, ctx, app = ready()
    check("ライバルの居ない街のギルドでは声をかけない",
          not arrive(ctx, app, Facility("g1", "guild", "灰の会館"), area="1"))
    check("優先度の高いほかの申し出には譲る",
          not arrive(ctx, app, guild, other=("someone_else", 20)))
    check("譲ったら種は残る", [t["kind"] for t in bucket_of(module, app)["topics"]] == ["intro"])
    chosen = arrive(ctx, app, guild, other=("300_event_facility_arrival", 0))
    check("300 の申し出には勝つ", len(chosen) == 1, chosen)
    check("勝った後も申し出は残す（300 が後から確かめても譲る）",
          arrivals.winner(app) == "911_rival_adventurer", arrivals.offers(app))

    print("[声かけの取りこぼし]")
    module, ctx, app = ready()
    bucket_of(module, app)["rival"]["intro"] = ""
    check("登場の語りが書けるまで声をかけない", not arrive(ctx, app, guild))
    bucket_of(module, app)["rival"]["intro"] = "語り"

    def boom(function, choice_text=""):
        raise RuntimeError("phase machine said no")

    app.process_choice = boom
    arrive(ctx, app, guild)
    check("会話を始められなければ種は残る",
          [t["kind"] for t in bucket_of(module, app)["topics"]] == ["intro"],
          bucket_of(module, app)["topics"])
    del app.process_choice
    ctx.errors[:] = []
    arrive(ctx, app, guild)
    check("第一声の前は種を残す", [t["kind"] for t in bucket_of(module, app)["topics"]] == ["intro"],
          bucket_of(module, app)["topics"])
    module._store()["approach"]["at"] -= module.APPROACH_TTL + 1
    check("印が古ければ第一声は変えない", opening(ctx, app) == "<行動: 話しかける>")
    check("第一声を読み替えられなければ種は残る",
          [t["kind"] for t in bucket_of(module, app)["topics"]] == ["intro"],
          bucket_of(module, app)["topics"])

    app.world.characters["11"].config["is_dead"] = True
    arrive(ctx, app, guild)
    key = module._store()["worlds"].playthrough(app)
    path = os.path.join(STATE_DIR, "rival_adventurer", state.world_filename(key))
    with io.open(path, encoding="utf-8") as fh:
        check("居なくなったら台帳を書く（声かけの経路でも）", json.load(fh)["rival"] is None)

    module, ctx, app = ready(APPROACH_CHANCE_PERCENT=0)
    check("確率 0 なら声をかけない", not arrive(ctx, app, guild))
    module, ctx, app = ready()
    app.game_variables["party"].append("11")
    check("仲間にしている間は声をかけない", not arrive(ctx, app, guild))
    arrivals.reset()


def scene_tool():
    print("[設定画面の部品]")
    spec = importlib.util.spec_from_file_location("rival_tool", os.path.join(MOD_DIR, "tool.py"))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        declared = set(json.load(fh)["settings"])
    placed = set(tool.text_keys()) | set(tool.number_keys())
    check("全部の設定がどこかのタブに載る", declared == placed,
          (sorted(declared - placed), sorted(placed - declared)))
    check("文と数の振り分けが重ならない", not set(tool.text_keys()) & set(tool.number_keys()))
    check("見本に仮の値が入る", tool.sample_of("{name}が{title}") == "リオが古城の亡霊")
    check("知らない変数は残す", tool.sample_of("{nope}") == "{nope}")
    check("欄の改行を落とす", tool.one_line("一行目\n二行目\n") == "一行目二行目")
    check("ライバル前の様子", "まだ現れていない" in tool.describe({"rival": None}))
    module, ctx, app = ready(roll=0.99)
    open_board(ctx, app)
    elapse(ctx, app, 45)
    finish_quest(ctx, module, app, "1")
    text = tool.describe(bucket_of(module, app))
    check("様子に名前と態度", "冒険者11" in text and "1 一目置く" in text, text)
    check("様子に取り合いの記録", "しくじった後をプレイヤーが片付けた" in text, text)
    check("様子に話の種", "話の種: " in text and "尻拭いされた（「古城（北）の亡霊」）" in text, text)
    files = tool.state_files(STATE_DIR)
    check("様子の控えの一覧", len(files) == 1 and files[0][1].endswith(".json"), files)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    scene_pure()
    scene_draw()
    scene_intro()
    scene_aim()
    scene_skip_conversation_quest()
    scene_taken()
    scene_failure()
    scene_player_wins()
    scene_party()
    scene_gone()
    scene_notes()
    scene_load()
    scene_store()
    scene_approach()
    scene_tool()
    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
