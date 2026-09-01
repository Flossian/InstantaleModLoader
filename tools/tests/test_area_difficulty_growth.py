# -*- coding: utf-8 -*-
"""318_area_difficulty_growth をゲーム抜きで通す。

    python tools/tests/test_area_difficulty_growth.py

偽の app / World / Quest / QuestEndManager / DisplayQuestChoice / Clock を差し込み、
次を確認する。

  数える     … クリア1回ごとに STEP_MIN〜STEP_MAX の乱数を積む。出所の土地に付く
  幅         … 引く値は必ず最小〜最大の中。上下を逆に設定しても壊れない
  ロード     … セーブを読み込んだ直後に、世界じゅうの土地をまとめて寄せ直す
  生成       … 新しい依頼は素の帯で生まれるので、作らせたその場で上げ直す
  上げる     … その土地の依頼が「素の値 + 積んだ上昇量」になる。世界の雛形には書かない
  他の土地   … 数えた土地以外は動かない
  上限       … MAX_BONUS と難易度の上限を超えない
  範囲       … SCOPE=incomplete では完了済みの依頼を動かさない
  戻す       … ROLLBACK を入れると素の値へ戻り、控えごと消える
  控え       … `state/area_difficulty/<世界名>.json` に世界ごとに分かれて残る
  知らせ     … 上がった回に1行ずつ出る。上限に達したら黙る
  安全       … 依頼の土地が読めない場面では何もしない
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state_area_difficulty")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


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


MOD_DIR, MOD = find_mod("_area_difficulty_growth")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Quest:
    """`world.quests` に入っている側（インスタンス）。"""

    def __init__(self, quest_id, area_id, difficulty, status="incomplete"):
        self.id = quest_id
        self.neighboring_settlement_id = area_id
        self.difficulty = difficulty
        self.quest_type = "normal_quest"
        self.config = {"status": status, "level_of_detail": 0}


def quest_dict(quest):
    """`world_dict['quests']` に入っている側（セーブに出るほう）。"""
    return {"quest_title": "試しの依頼" + quest.id,
            "difficulty": quest.difficulty,
            "neighboring_settlement_id": quest.neighboring_settlement_id,
            "id": quest.id,
            "quest_type": quest.quest_type,
            "config": dict(quest.config)}


class Area:
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name
        self.nodes = {}


class World:
    def __init__(self, name, areas, quests, days_elapsed):
        self.name = name
        self.areas = areas
        self.quests = quests
        self.days_elapsed = days_elapsed


class Player:
    def __init__(self, area_id):
        self.name = "試しのプレイヤー"
        self.current_area = area_id


class InstantaleApp:
    def __init__(self, world, world_dict, player):
        self.world = world
        self.world_dict = world_dict
        self.player = player
        self.texts = []

    def add_text(self, text):
        self.texts.append(text)


class QuestEndManager:
    """`execute` が「依頼が片付いた」印を立てるだけの入れ物。"""

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        quest = getattr(self.app, "current_quest_data", None)
        if quest is not None:
            quest.config["status"] = "completed"
            store = self.app.world_dict["quests"].get(quest.id)
            if store is not None:
                store["config"]["status"] = "completed"
        self.app.current_quest_data = None
        return "ended"


class DisplayQuestChoice:
    def __init__(self, app):
        self.app = app


class FakeClock:
    def __init__(self):
        self.onces = []

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def schedule_interval(self, callback, timeout=0):
        self.onces.append(callback)

    def unschedule(self, callback):
        self.onces = [entry for entry in self.onces if entry is not callback]

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


def install_fake_functions(minimum=0, maximum=76):
    """`scripts.functions` の上限・下限。MOD はこちらを先に見る。"""
    module = types.ModuleType("scripts.functions")
    module.QUEST_DIFFICULTY_VALUE_MIN = minimum
    module.QUEST_DIFFICULTY_VALUE_MAX = maximum
    scripts = sys.modules.get("scripts") or types.ModuleType("scripts")
    scripts.functions = module
    sys.modules["scripts"] = scripts
    sys.modules["scripts.functions"] = module


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

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、検査だけが別のログ処理を通ることになる。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1]
                    else path, exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    # 本物の `ctx.write_json` / `read_json` と同じものを使う。
    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def fixed(step, **rest):
    """1回ぶんの上昇量を `step` に固定した設定。乱数を回さずに測るため。"""
    settings = {"STEP_MIN": step, "STEP_MAX": step, "ANNOUNCE": ""}
    settings.update(rest)
    return settings


def load_mod(path=MOD, name="area_difficulty_growth_mod"):
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


# ---------------------------------------------------------------- 舞台作り
def make_world(quests, world_name="試しの世界", area_id="0", days=100):
    """`quests` は `[(id, 土地id, 難易度, 状態)]`。"""
    made = [Quest(qid, aid, difficulty, status)
            for qid, aid, difficulty, status in quests]
    by_id = {quest.id: quest for quest in made}
    areas = {}
    for quest in made:
        areas.setdefault(quest.neighboring_settlement_id,
                         Area(quest.neighboring_settlement_id,
                              "土地" + quest.neighboring_settlement_id))
    areas.setdefault(area_id, Area(area_id, "土地" + area_id))
    world = World(world_name, areas, by_id, days)
    world_dict = {"world_data": {"name": world_name, "days_elapsed": days},
                  "quests": {quest.id: quest_dict(quest) for quest in made}}
    return InstantaleApp(world, world_dict, Player(area_id))


def fresh(settings=None):
    """設定を差し替えて MOD を読み直し、フックを取り付ける。"""
    install_fake_kivy()
    install_fake_functions()
    module = load_mod()
    for name, value in (settings or {}).items():
        setattr(module, name, value)
    ctx = FakeCtx(OUT_DIR, STATE_DIR)
    module.apply(ctx)
    return module, ctx


def end_quest(ctx, app, quest_id):
    """依頼を片付ける（MOD のフック越しに `QuestEndManager.execute` を呼ぶ）。"""
    app.current_quest_data = app.world.quests.get(quest_id)
    manager = QuestEndManager(app)
    hook = ctx.hooks["__main__:QuestEndManager.execute"]
    result = hook(lambda self, *a, **kw: QuestEndManager.execute(self, *a, **kw),
                  manager, "帰還する")
    CLOCK.run_onces()
    return result


def open_board(ctx, app):
    hook = ctx.hooks["__main__:DisplayQuestChoice.__init__"]
    return hook(lambda self, app_, *a, **kw: DisplayQuestChoice.__init__(self, app_),
                DisplayQuestChoice.__new__(DisplayQuestChoice), app)


def difficulties(app, area_id):
    """`(生きた一覧, 世界の雛形)` の難易度を id 順で。

    雛形（`world_dict['quests']`）は**触られてはいけない**側。
    書くと世界のファイルに焼かれて、同じ世界の別のキャラクタにまで乗る。
    """
    ids = sorted((qid for qid, quest in app.world.quests.items()
                  if quest.neighboring_settlement_id == area_id),
                 key=lambda value: int(value))
    return ([app.world.quests[qid].difficulty for qid in ids],
            [app.world_dict["quests"][qid]["difficulty"]
             for qid in ids if qid in app.world_dict["quests"]])


def search_quest(ctx, app, born_id, born_area, born_difficulty):
    """依頼を1件作らせる（生成はゲームの側の仕事なので偽物で演じる）。

    **素の帯で生まれる**のが実機で測れた挙動（VERIFICATION_LOG.md §2.66）。
    生成の中で `random_quest_generator` も1度呼ばれる。
    """
    calls = []

    def generate(self, *args, **kwargs):
        gen = ctx.hooks.get(
            "scripts.llm.llm_manager_world_generate:random_quest_generator")
        if gen is not None:
            gen(lambda *a, **kw: calls.append(a[5]) or {"quest_title": "作られた依頼"},
                "世界", "町", "概要", "構造", "土地の説明", born_difficulty)
        quest = Quest(born_id, born_area, born_difficulty)
        app.world.quests[quest.id] = quest
        return quest

    hook = ctx.hooks["__main__:DisplayQuestChoice.generate_random_quest"]
    hook(generate, DisplayQuestChoice.__new__(DisplayQuestChoice))
    return calls


def load_world(ctx, app):
    """セーブを読み込む（`World.__init__` を通す）。

    実機では難易度がセーブに残らないので、ここは**素へ戻った状態**から始まる。
    """
    save_data_dict = {"world_data": {"name": app.world.name},
                      "quests": {}}
    hook = ctx.hooks["__main__:World.__init__"]
    hook(lambda self, *a, **kw: None, app.world, save_data_dict, app)
    return app


def accept_quest(ctx, app, quest_id):
    """受注の入口（`QuestChoiceManager.__init__`）を通す。"""
    hook = ctx.hooks["__main__:QuestChoiceManager.__init__"]
    seen = {}

    def init(self, app_, quest_type, qid, *args, **kwargs):
        seen["difficulty"] = app_.world.quests[qid].difficulty
        return None

    hook(init, object(), app, "settlement_quest", str(quest_id))
    return seen.get("difficulty")


def state_file(world_name="試しの世界"):
    from instantale_modloader.state import world_filename
    path = os.path.join(STATE_DIR, "area_difficulty", world_filename(world_name))
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


BAND = [("1", "0", 3, "incomplete"),
        ("2", "0", 4, "incomplete"),
        ("3", "0", 5, "incomplete"),
        ("4", "1", 50, "incomplete")]


def reset():
    shutil.rmtree(STATE_DIR, ignore_errors=True)


# ---------------------------------------------------------------- 検査
print("318_area_difficulty_growth")

# -- 数える ---------------------------------------------------------------
reset()
module, ctx = fresh(fixed(3))
app = make_world(BAND)
end_quest(ctx, app, "1")
check("1回のクリアで上がる", difficulties(app, "0")[0] == [6, 7, 8],
      difficulties(app, "0")[0])
check("回数は控えに残る", (state_file() or {}).get("0", {}).get("cleared") == 1,
      state_file())

end_quest(ctx, app, "2")
end_quest(ctx, app, "3")
raised, template = difficulties(app, "0")
check("クリアのたびに積む", raised == [12, 13, 14], raised)
check("世界の雛形には書かない", template == [3, 4, 5], template)
check("積んだ上昇量が控えに残る",
      (state_file() or {}).get("0", {}).get("bonus") == 9, state_file())
check("素の難易度を控えている",
      state_file()["0"].get("base") == {"1": 3, "2": 4, "3": 5},
      state_file()["0"].get("base"))
check("他の土地は動かない", difficulties(app, "1")[0] == [50],
      difficulties(app, "1"))
check("例外を出していない", not ctx.errors, ctx.errors)

# -- 生成 -----------------------------------------------------------------
# 新しい依頼は**素の帯で生まれる**（実機。VERIFICATION_LOG.md §2.66）。
# 生成のその場で上げないと、いま作らせた依頼だけ素の難易度で差し出される。
passed = search_quest(ctx, app, born_id="9", born_area="0", born_difficulty=4)
check("生まれた依頼をその場で上げる",
      app.world.quests["9"].difficulty == 13, app.world.quests["9"].difficulty)
check("頼み文へ渡す難易度も上げる", passed == [13], passed)
check("素の値はそのまま控える",
      state_file()["0"]["base"].get("9") == 4, state_file()["0"].get("base"))

# 次のクリアでは、生まれた依頼も一緒に上がる。
end_quest(ctx, app, "9")
check("次のクリアでは新旧そろって上がる",
      difficulties(app, "0")[0] == [15, 16, 17, 16], difficulties(app, "0")[0])

# -- 受注 -----------------------------------------------------------------
# ロードすると難易度は素へ戻る（セーブに残らない）。受注の入口で書き直す。
for quest in app.world.quests.values():
    if quest.neighboring_settlement_id == "0":
        quest.difficulty = state_file()["0"]["base"][quest.id]
check("ロード直後は素の難易度", difficulties(app, "0")[0] == [3, 4, 5, 4],
      difficulties(app, "0")[0])
check("受注の直前に書き直す", accept_quest(ctx, app, "1") == 15,
      accept_quest(ctx, app, "1"))

# -- 幅 -------------------------------------------------------------------
# 引く値は必ず最小〜最大の中。乱数そのものは信用してよいので、
# 見るのは「幅から出ないこと」と「幅が逆でも壊れないこと」だけ。
reset()
module, ctx = fresh({"STEP_MIN": 3, "STEP_MAX": 10, "ANNOUNCE": ""})
app = make_world(BAND)
steps = []
for _ in range(40):
    before = (state_file() or {}).get("0", {}).get("bonus", 0)
    end_quest(ctx, app, "1")
    steps.append((before, state_file()["0"]["bonus"] - before))
# 積むのは `min(MAX_BONUS, bonus + 引いた値)` なので、上限（60）に届く回は
# 差分が頭を削られて幅の外へ出る。先頭の8回で数えると、引きが強い回
# （先頭7回の合計が 57 を超える回。40回に1回ほど出る）で落ちた。
# 見るのは削られようのない回、つまり `bonus` が 60-10 以下だったときだけ。
drawn = [s for before, s in steps if before <= 60 - 10]
check("引いた値が 3〜10 に収まる", all(3 <= s <= 10 for s in drawn), drawn)
check("同じ値ばかりにならない", len(set(drawn)) > 1, drawn)
check("上限（既定60）で止まる", state_file()["0"]["bonus"] == 60,
      state_file()["0"]["bonus"])
check("上限に達したら以後は積まない", steps[-1][1] == 0,
      [s for _, s in steps[-3:]])

reset()
module, ctx = fresh({"STEP_MIN": 10, "STEP_MAX": 3, "ANNOUNCE": ""})
app = make_world(BAND)
end_quest(ctx, app, "1")
check("幅が逆でも最小に倒す", state_file()["0"]["bonus"] == 10,
      state_file()["0"]["bonus"])

reset()
module, ctx = fresh({"STEP_MIN": 0, "STEP_MAX": 0, "ANNOUNCE": ""})
app = make_world(BAND)
end_quest(ctx, app, "1")
check("0〜0 なら上がらない", difficulties(app, "0")[0] == [3, 4, 5],
      difficulties(app, "0")[0])

# -- ロード ---------------------------------------------------------------
# 掲示板を通らない読み手（店の品揃えが筆頭）が素の値を見ないよう、
# ロードの1回で世界ぜんぶを寄せ直す。
reset()
module, ctx = fresh(fixed(5))
app = make_world(BAND)
end_quest(ctx, app, "1")
end_quest(ctx, app, "4")
check("2つの土地が上がっている",
      (difficulties(app, "0")[0], difficulties(app, "1")[0]) == ([8, 9, 10], [55]),
      (difficulties(app, "0")[0], difficulties(app, "1")[0]))

for quest in app.world.quests.values():
    quest.difficulty = state_file()[quest.neighboring_settlement_id]["base"][quest.id]
check("ロード直前は素の難易度",
      (difficulties(app, "0")[0], difficulties(app, "1")[0]) == ([3, 4, 5], [50]),
      (difficulties(app, "0")[0], difficulties(app, "1")[0]))

load_world(ctx, app)
check("ロードで世界ぜんぶが戻る",
      (difficulties(app, "0")[0], difficulties(app, "1")[0]) == ([8, 9, 10], [55]),
      (difficulties(app, "0")[0], difficulties(app, "1")[0]))
check("行ったことのない土地も寄る（店の品揃えが読む先）",
      difficulties(app, "1")[0] == [55], difficulties(app, "1")[0])
check("雛形はロードでも触らない", difficulties(app, "0")[1] == [3, 4, 5],
      difficulties(app, "0")[1])

# -- 上限 -----------------------------------------------------------------
reset()
module, ctx = fresh(fixed(10, MAX_BONUS=20))
app = make_world(BAND)
for _ in range(5):
    end_quest(ctx, app, "1")
check("MAX_BONUS で頭打ちになる", difficulties(app, "0")[0] == [23, 24, 25],
      difficulties(app, "0")[0])

reset()
module, ctx = fresh(fixed(10, MAX_BONUS=76))
app = make_world([("1", "0", 70, "incomplete"), ("2", "0", 74, "incomplete")])
for _ in range(4):
    end_quest(ctx, app, "1")
check("難易度の上限を超えない", difficulties(app, "0")[0] == [76, 76],
      difficulties(app, "0")[0])

reset()
module, ctx = fresh(fixed(10, MAX_BONUS=76, DIFFICULTY_LIMIT=30))
app = make_world(BAND)
for _ in range(5):
    end_quest(ctx, app, "1")
check("DIFFICULTY_LIMIT が天井になる", difficulties(app, "0")[0] == [30, 30, 30],
      difficulties(app, "0")[0])

# -- 範囲 -----------------------------------------------------------------
reset()
module, ctx = fresh(fixed(5, SCOPE="incomplete"))
app = make_world([("1", "0", 3, "completed"),
                  ("2", "0", 4, "incomplete"),
                  ("3", "0", 5, "incomplete")])
end_quest(ctx, app, "2")
# 片付けた依頼はゲーム自身が `completed` にするので、
# incomplete のときは**いま終わらせた依頼も**動かない（残るのは 3 だけ）。
check("incomplete では完了済みを動かさない",
      difficulties(app, "0")[0] == [3, 4, 10], difficulties(app, "0")[0])

reset()
module, ctx = fresh(fixed(5, SCOPE="all"))
app = make_world([("1", "0", 3, "completed"),
                  ("2", "0", 4, "incomplete"),
                  ("3", "0", 5, "incomplete")])
end_quest(ctx, app, "2")
check("all では完了済みも上がる（在庫の母数）",
      difficulties(app, "0")[0] == [8, 9, 10], difficulties(app, "0")[0])

# -- 戻す -----------------------------------------------------------------
reset()
module, ctx = fresh(fixed(4))
app = make_world(BAND)
end_quest(ctx, app, "1")
end_quest(ctx, app, "2")
check("戻す前は上がっている", difficulties(app, "0")[0] == [11, 12, 13],
      difficulties(app, "0")[0])

module, ctx = fresh(fixed(4, ROLLBACK=True))
open_board(ctx, app)
check("ROLLBACK で素の値へ戻る", difficulties(app, "0")[0] == [3, 4, 5],
      difficulties(app, "0")[0])
check("控えが消える", "0" not in (state_file() or {}), state_file())

# 範囲を絞っていても、上げた後に片付いた依頼は戻る（範囲の外へ出ても取り残さない）。
reset()
module, ctx = fresh(fixed(5, SCOPE="incomplete"))
app = make_world([("1", "0", 3, "incomplete"),
                  ("2", "0", 4, "incomplete"),
                  ("3", "0", 5, "incomplete")])
end_quest(ctx, app, "1")
end_quest(ctx, app, "2")
check("範囲を絞ると片付いた側は据え置かれる",
      difficulties(app, "0")[0] == [3, 9, 15], difficulties(app, "0")[0])

module, ctx = fresh(fixed(5, SCOPE="incomplete", ROLLBACK=True))
open_board(ctx, app)
check("上げた後に片付いた依頼も戻る", difficulties(app, "0")[0] == [3, 4, 5],
      difficulties(app, "0")[0])

# -- 世界が混ざらない -----------------------------------------------------
reset()
module, ctx = fresh(fixed(5))
first = make_world(BAND, world_name="第一の世界")
second = make_world(BAND, world_name="第二の世界")
end_quest(ctx, first, "1")
check("別の世界は動かない", difficulties(second, "0")[0] == [3, 4, 5],
      difficulties(second, "0")[0])
check("控えは世界ごとに分かれる",
      state_file("第一の世界") is not None and state_file("第二の世界") is None,
      [state_file("第一の世界"), state_file("第二の世界")])

# -- 知らせ ---------------------------------------------------------------
reset()
module, ctx = fresh(fixed(3, MAX_BONUS=6, ANNOUNCE="手応えが増している。"))
app = make_world(BAND)
end_quest(ctx, app, "1")
end_quest(ctx, app, "2")
check("上がった回に1行ずつ出る",
      app.texts == ["手応えが増している。"] * 2, app.texts)
end_quest(ctx, app, "3")
check("上限に達したら黙る", len(app.texts) == 2, app.texts)

# -- 安全 -----------------------------------------------------------------
reset()
module, ctx = fresh(fixed(5))
app = make_world(BAND)
app.current_quest_data = None
manager = QuestEndManager(app)
hook = ctx.hooks["__main__:QuestEndManager.execute"]
hook(lambda self, *a, **kw: QuestEndManager.execute(self, *a, **kw), manager, "帰還する")
CLOCK.run_onces()
check("依頼が読めない回は何もしない", difficulties(app, "0")[0] == [3, 4, 5],
      difficulties(app, "0")[0])
check("控えも作らない", state_file() is None, state_file())

# 難易度が数でない依頼（壊れたセーブ）を混ぜても落ちない。
broken = Quest("8", "0", None)
app.world.quests["8"] = broken
app.world_dict["quests"]["8"] = quest_dict(broken)
end_quest(ctx, app, "1")
check("難易度が数でない依頼は素通し",
      app.world.quests["8"].difficulty is None, app.world.quests["8"].difficulty)
check("残りは上がる", difficulties(app, "0")[0][:3] == [8, 9, 10],
      difficulties(app, "0")[0])

# 生きた一覧が引けない版では何もしない（雛形へ書かない）。
reset()
module, ctx = fresh(fixed(5))
app = make_world(BAND)
app.world.quests = None
app.current_quest_data = Quest("1", "0", 3)
manager = QuestEndManager(app)
ctx.hooks["__main__:QuestEndManager.execute"](
    lambda self, *a, **kw: "ended", manager, "帰還する")
CLOCK.run_onces()
check("生きた一覧が無ければ雛形へ書かない",
      [q["difficulty"] for q in app.world_dict["quests"].values()] == [3, 4, 5, 50],
      [q["difficulty"] for q in app.world_dict["quests"].values()])
check("例外を出していない（安全側）", not ctx.errors, ctx.errors)

reset()
print()
if failures:
    print("FAILED: " + ", ".join(failures))
    raise SystemExit(1)
print("all ok")
