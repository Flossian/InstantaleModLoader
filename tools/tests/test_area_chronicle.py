# -*- coding: utf-8 -*-
"""321_area_chronicle をゲーム抜きで通す。

    python tools/tests/test_area_chronicle.py

偽の世界（`Area.descriptions` 付き）と偽の LLM を差し込み、次を確認する。

  素の関数  … achievements の均し・返答の読み分け・膨張の線・控えの並び
  クリア    … QuestEndManager.execute の後に編纂が走り、
              descriptions が**新しい dict で**差し替わる。
              元の dict は書き換えない（world_data.json へ焼かれる側かもしれない）
  保険      … クリアを取りこぼしても、到着の照合で `done` の遅れから立ち直る
  読めない  … 壊れた返答では前の文面が残り、`done` も進まない（次で再挑戦）
  膨張      … 元の2倍を超えた欄は捨てて前の文面を残す。もう片方は受ける
  絞り      … 新しい功績が多いときは直近 RECENT_DEEDS 件だけを頼み文に載せる
  ロード    … World.__init__ の後に `state\\` の文面が当て直される
  第一声    … 既定 OFF では触らない。ON なら**複製の** profile に1行を足す。
              年代記の無い土地では足さない
  名乗り    … mod.json の既定値とコードの定数が一致する
"""
import importlib.util
import io
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                                   # noqa: E402
from instantale_modloader import llm as llm_module                  # noqa: E402
from instantale_modloader import ui as ui_module                    # noqa: E402

QUEST_END = "__main__:QuestEndManager.execute"
WORLD_INIT = "__main__:World.__init__"
AREA_MOVE = "__main__:AreaMoveManager.execute"
ELAPSE = "__main__:InstantaleApp.elapse_days"
STARTER = "scripts.llm.llm_manager:conversation_starter"


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
    return matches[0], folder, os.path.join(folder, entry)


MOD_FOLDER, MOD_DIR, MOD = find_mod("_area_chronicle")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
OVERVIEW0 = "深い霧に閉ざされた湿地の町。"
FACILITIES0 = ("町の中心には古い宿があり、旅人は霧を恐れて長居しない。\n"
               "商店の棚は薄く、霧の主の噂が交易を細らせている。")


class Area(object):
    def __init__(self, area_id, name, overview=OVERVIEW0, facilities=FACILITIES0):
        self.id = area_id
        self.name = name
        self.nodes = {}
        self.descriptions = {"overview": overview, "facilities": facilities}


class Character(object):
    def __init__(self, name, area_history=None, current_area=None, profile=""):
        self.name = name
        self.area_history = area_history
        self.current_area = current_area
        self.profile = profile


class World(object):
    def __init__(self, areas, days_elapsed=100):
        self.name = "テスト世界"
        self.areas = areas
        self.quests = {}
        self.days_elapsed = days_elapsed


class App(object):
    def __init__(self, player, world):
        self.player = player
        self.world = world
        self.world_dict = {"world_data": {"world_name": world.name}}
        self.current_quest_data = None


def make_app(achievements=("霧の主を討ち、湿地の霧は晴れた",), area_id="3"):
    history = {area_id: {"achievements": list(achievements), "lawfulness": 10}}
    areas = {"3": Area("3", "霧の湿地"), "7": Area("7", "遠い港")}
    player = Character("旅人リン", area_history=history, current_area=area_id)
    return App(player, World(areas))


class FakeLLM(object):
    """`ask` だけを差し替える。

    他の名前は本物の `instantale_modloader.llm` へ渡す
    （`parse_json` のような素の関数までここに写すと、
    本物を直したときにこちらだけ古くなる）。
    """

    def __getattr__(self, name):
        return getattr(llm_module, name)

    def __init__(self, replies):
        self.replies = list(replies)
        self.asked = []

    def ask(self, ctx, manager_name, message, *, timeout=None, structure=None,
            max_tokens=None, label="llm", write=None):
        self.asked.append({"manager": manager_name, "message": message,
                           "timeout": timeout})
        return self.replies.pop(0) if self.replies else None


class FakeCtx(object):
    _mod = MOD_FOLDER

    def __init__(self):
        self.out_dir = OUT_DIR
        self.state_dir = STATE_DIR
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.ready = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def write_json(self, path, data):
        return ml.write_json(path, data, report=self.log_exc)

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def on_ready(self, fn, **kw):
        self.ready.append(fn)
        return True

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="area_chronicle_mod"):
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(app, replies=(), **settings):
    """mod を読み直して当て直す。`sys` に置いた入れ物（ワーカーと控え）も落とす。"""
    module = load_mod()
    for key, value in settings.items():
        setattr(module, key, value)
    module.CHECK_INTERVAL = 0.0          # 検査では間引かない
    module.WAIT_POLL = 0.01              # 要約待ちの間隔も詰める
    module.llm = FakeLLM(replies)
    if hasattr(sys, module.STATE_STORE_ATTR):
        delattr(sys, module.STATE_STORE_ATTR)
    ui_module.find_app = lambda: app
    ctx = FakeCtx()
    module.apply(ctx)
    return module, ctx


def store_of(module):
    return getattr(sys, module.STATE_STORE_ATTR)


def drain(module, timeout=10.0):
    return store_of(module)["worker"].drain(timeout)


def clear_quest(app, area_id="3"):
    """クリアの体裁を作る。`current_quest_data` に依頼を置いてフックを呼ぶ形。"""
    app.current_quest_data = {"quest_title": "霧の主の討伐",
                              "neighboring_settlement_id": area_id,
                              "config": {"status": "completed"}}
    return types.SimpleNamespace(app=app)


def read_cache(module, world="テスト世界"):
    from instantale_modloader.state import world_filename
    path = os.path.join(STATE_DIR, module.STATE_DIRNAME, world_filename(world))
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def clear_cache(module):
    path = os.path.join(STATE_DIR, module.STATE_DIRNAME)
    if os.path.isdir(path):
        for name in os.listdir(path):
            os.remove(os.path.join(path, name))


REPLY = json.dumps({"overview": "霧の晴れた湿地の町。",
                    "facilities": "町の中心には古い宿があり、旅人の行き来が戻りつつある。\n"
                                  "商店の棚には品が増え、交易の再開が噂されている。"},
                   ensure_ascii=False)
REPLY2 = json.dumps({"overview": "盗賊の消えた湿地の町。",
                     "facilities": "街道が開き、商店の棚はさらに賑わっている。"},
                    ensure_ascii=False)


def prompt_of(module, index=0):
    return module.llm.asked[index]["message"][0]["content"]


# ------------------------------------------------------------------ 検査
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    saved_find_app = ui_module.find_app
    try:
        return run()
    finally:
        ui_module.find_app = saved_find_app


def run():
    module = load_mod()

    print("素の関数")
    check("achievements を文字列に均す",
          module.achievements_of({"achievements": ["霧は晴れた", 7, ""]})
          == ["霧は晴れた", "7"])
    check("読めない記録は空のリスト",
          module.achievements_of(None) == []
          and module.achievements_of({"achievements": "1件だけの文字列"})
          == ["1件だけの文字列"])
    check("clean_text が行ごとに空白を畳む",
          module.clean_text("  a  b \n\n c　d ") == "a b\nc d")
    check("acceptable が空と膨張を弾く",
          not module.acceptable("", "元の文", 100)
          and module.acceptable("x" * 200, "元の文", 100)
          and not module.acceptable("x" * 201, "元の文", 100))
    check("JSON の返答を読む",
          module.parse_result(REPLY)["overview"] == "霧の晴れた湿地の町。")
    check("囲み付きの返答も読む",
          module.parse_result("```json\n" + REPLY + "\n```") is not None)
    check("片方の欄しか無い返答は在る方だけ受ける",
          module.parse_result('{"overview": "概要だけ"}')
          == {"overview": "概要だけ", "facilities": ""})
    check("素の文章は受けない（どちらの欄か決められない）",
          module.parse_result("霧が晴れました。") is None
          and module.parse_result("{こわれた json") is None
          and module.parse_result(None) is None)
    check("控えの並びが RECORD_KEYS の順",
          list(module.ordered_record({"done": 1, "area": "3", "extra": 9,
                                      "name": "霧の湿地"}))
          == ["area", "name", "done", "extra"])
    messages = module.build_messages({
        "area_name": "霧の湿地", "overview": OVERVIEW0,
        "facilities": FACILITIES0, "deeds": ["霧の主を討った"]})
    body = messages[0]["content"]
    check("頼み文に現行の案内文と新しい功績が載る",
          OVERVIEW0 in body and "霧を恐れて長居しない" in body
          and "- 霧の主を討った" in body)
    check("頼み文が長さの上限を言う", "字以内" in body)

    print("クリアで編纂が走る")
    clear_cache(module)
    app = make_app()
    module, ctx = fresh_mod(app, replies=(REPLY,))
    area = app.world.areas["3"]
    before = area.descriptions
    ctx.hooks[QUEST_END](lambda self: None, clear_quest(app))
    check("編纂が終わる", drain(module))
    check("manager_name が自前", module.llm.asked
          and module.llm.asked[0]["manager"] == module.MANAGER_COMPILE)
    check("timeout をキーワードで渡している",
          module.llm.asked[0]["timeout"] == module.COMPILE_TIMEOUT)
    check("descriptions が差し替わる",
          area.descriptions["overview"] == "霧の晴れた湿地の町。"
          and "品が増え" in area.descriptions["facilities"])
    check("元の dict は書き換えない（骨格と共有されうる側）",
          area.descriptions is not before
          and before["overview"] == OVERVIEW0)
    cache = read_cache(module)
    check("控えに done と文面が入る",
          cache and cache["3"]["done"] == 1
          and cache["3"]["overview"] == "霧の晴れた湿地の町。")
    check("控えの並びが固定", list(cache["3"]) == list(module.RECORD_KEYS))
    check("エラーが出ていない", not ctx.errors, ctx.errors)

    print("取りこぼしの保険（到着の照合）")
    clear_cache(module)
    app = make_app()
    module, ctx = fresh_mod(app, replies=(REPLY,))
    ctx.hooks[AREA_MOVE](lambda self, choice: None,
                         types.SimpleNamespace(app=app), None)
    check("到着の照合だけでも編纂が走る", drain(module) and module.llm.asked)
    check("done が進む", read_cache(module)["3"]["done"] == 1)
    asked = len(module.llm.asked)
    ctx.hooks[ELAPSE](lambda self, days: None, app, 1)
    drain(module)
    check("織り込み済みなら何もしない", len(module.llm.asked) == asked)

    print("読めない返答")
    clear_cache(module)
    app = make_app()
    module, ctx = fresh_mod(app, replies=("こわれた返答", REPLY))
    area = app.world.areas["3"]
    ctx.hooks[QUEST_END](lambda self: None, clear_quest(app))
    drain(module)
    check("前の文面が残る", area.descriptions["overview"] == OVERVIEW0)
    check("done は進まない（次で再挑戦できる）", read_cache(module) is None)
    ctx.hooks[ELAPSE](lambda self, days: None, app, 1)
    drain(module)
    check("次の照合で立ち直る",
          area.descriptions["overview"] == "霧の晴れた湿地の町。"
          and read_cache(module)["3"]["done"] == 1)

    print("膨張の却下")
    clear_cache(module)
    app = make_app()
    bloated = json.dumps({"overview": "長" * 500,
                          "facilities": "町は静けさを取り戻した。"},
                         ensure_ascii=False)
    module, ctx = fresh_mod(app, replies=(bloated,))
    area = app.world.areas["3"]
    ctx.hooks[QUEST_END](lambda self: None, clear_quest(app))
    drain(module)
    check("膨らんだ欄は前の文面のまま",
          area.descriptions["overview"] == OVERVIEW0)
    check("もう片方の欄は受ける",
          area.descriptions["facilities"] == "町は静けさを取り戻した。")
    check("受けた組で done が進む", read_cache(module)["3"]["done"] == 1)

    print("功績の絞り")
    clear_cache(module)
    deeds = ["功績その{}".format(n) for n in range(1, 9)]
    app = make_app(achievements=deeds)
    module, ctx = fresh_mod(app, replies=(REPLY,), RECENT_DEEDS=2)
    ctx.hooks[QUEST_END](lambda self: None, clear_quest(app))
    drain(module)
    body = prompt_of(module)
    check("直近2件だけが頼み文に載る",
          "功績その7" in body and "功績その8" in body
          and "功績その6" not in body)
    check("done は総数まで進む", read_cache(module)["3"]["done"] == 8)

    print("ロード時の当て直し")
    app2 = make_app()                      # 素の文面で作り直した世界
    module, ctx = fresh_mod(app2)          # 控えは前の検査のものが残っている
    world2 = app2.world
    save_dict = {"world_data": {"world_name": "テスト世界"}}
    ctx.hooks[WORLD_INIT](lambda self, d, a: None, world2, save_dict, app2)
    check("控えの文面が当たる",
          world2.areas["3"].descriptions["overview"] == "霧の晴れた湿地の町。")
    check("控えの無い土地は素のまま",
          world2.areas["7"].descriptions["overview"] == OVERVIEW0)

    print("第一声への差し込み")
    npc = Character("店主", profile="口の固い店主。")
    seen = {}

    def orig(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        seen["times"] = seen.get("times", 0) + 1
        return "第一声"

    # 既定 OFF（controls は前の検査の控えが残ったまま＝年代記は在る）
    module, ctx = fresh_mod(app2)
    ctx.hooks[STARTER](orig, "messages", "log", "player", npc, "relationship")
    check("既定 OFF では触らない", seen["args"][3] is npc)
    # ON ＋ 年代記あり
    module, ctx = fresh_mod(app2, MENTION_IN_STARTER=True)
    ctx.hooks[STARTER](orig, "messages", "log", "player", npc, "relationship")
    got = seen["args"][3]
    check("複製の profile に1行が足される",
          got is not npc and got.profile.endswith(module.MENTION_TEXT)
          and got.profile.startswith("口の固い店主。"))
    check("本体の NPC は変わらない", npc.profile == "口の固い店主。")
    ctx.hooks[STARTER](orig, "messages", "log", "player",
                       character_instance=npc)
    check("キーワード渡しでも複製に足す",
          seen["kwargs"]["character_instance"] is not npc
          and seen["kwargs"]["character_instance"].profile.endswith(
              module.MENTION_TEXT))
    # ON ＋ 年代記なし
    clear_cache(module)
    module, ctx = fresh_mod(app2, MENTION_IN_STARTER=True)
    ctx.hooks[STARTER](orig, "messages", "log", "player", npc, "relationship")
    check("年代記の無い土地では足さない", seen["args"][3] is npc)
    check("本体は必ず1回だけ呼ばれる", seen["times"] == 4)

    print("名乗り")
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    module = load_mod()
    wrong = [key for key, decl in manifest["settings"].items()
             if getattr(module, key, object()) != decl["default"]]
    check("mod.json の既定値とコードの定数が一致する", not wrong, wrong)
    check("宣言した設定は全部コードに在る",
          all(hasattr(module, key) for key in manifest["settings"]))

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
