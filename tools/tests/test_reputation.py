# -*- coding: utf-8 -*-
"""317_reputation をゲーム抜きで通す。

    python tools/tests/test_reputation.py

（開発中は `908_reputation` / `test_wip_reputation.py` だった。）

偽の `area_history` と偽の依頼台帳、偽の LLM を差し込み、次を確認する。

  素材    … 土地ごとに achievements・手配度・完了した依頼を拾う。
            よその土地の依頼も、片付いていない依頼も混ざらない。
            survey は全土地を1回の走査で数える
  印      … 行いが増えれば変わる。**滞在日数が増えても変わらない**
            （入れると同じ土地に居るだけで毎日編纂し直すことになる）
  立つか   … 件数が足りない土地では何も注入しない。
            手配されていれば件数に関わらず立つ
  編纂    … 頼み文に素材が載る。JSON・囲み付き・素の文章・壊れた返答を読み分ける
  二つ名   … 世界に1つ。質的な変化（評判の立つ土地の増減・手配の反転・
            行いの倍増）のときだけ編み直す。頼み文にいままでの名を渡し、
            据え置きを既定にする。本人の名前の写しは受けない
  名声    … 依頼・物語の依頼・闘技場を難易度の2乗で数える。悪名は手配が重くなった分の累計で減らない。
            名声＋悪名と土地の数が線に届くまで二つ名を出さない。線を越えた土地に呼び名を付ける。
            評判の画面の中身と、MOD 用のボタンの一覧の行
  長さ    … brief は1文、detailed は3文まで。字数でも頭打ちになる
  注入    … 会話5関数すべてで**複製の** profile に足す。ゲーム側の NPC は変わらない
  素通し   … 評判が無い／profile が文字列でない／複製できない／app が無い
  情景描写  … 既定 OFF では触らない。ON なら player_profile に足す
  控え    … `state/` に書く。土地は `RECORD_KEYS`、二つ名は `EPITHET_RECORD_KEYS`。
            印が変わらなければ編纂し直さない。線を下回った土地の控えは外す
  安全    … LLM が返らなくても前の評判・前の名が残る。本体は必ず1回だけ呼ばれる
  名乗り   … mod.json の既定値とコードの定数が一致する
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
#: 控えの置き場（本番は配布フォルダ直下の `state/`）。
STATE_DIR = os.path.join(OUT_DIR, "state")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                                   # noqa: E402
from instantale_modloader import llm as llm_module                  # noqa: E402
from instantale_modloader import state as state_module              # noqa: E402
from instantale_modloader import ui as ui_module                    # noqa: E402
from instantale_modloader import gates, modnpc                      # noqa: E402
from instantale_modloader import hudicons as hudicons_module        # noqa: E402

CONV = "scripts.llm.llm_manager:"
CONVERSATION_TARGETS = (
    CONV + "conversation_starter",
    CONV + "conversation_starter_in_quest",
    CONV + "conversation_facilitator",
    CONV + "conversation_facilitator_after_retrieval",
    CONV + "conversation_facilitator_in_quest",
)
NARRATOR = CONV + "narrator"
SHEET_TOGGLE = ("scripts.hud.new_hud:"
                "InstanTaleHUD.toggle_character_sheet_visibility")
ELAPSE = "__main__:InstantaleApp.elapse_days"
LOAD_GAME = "__main__:InstantaleApp.load_game_new"
AREA_MOVE = "__main__:AreaMoveManager.execute"
MOVE_PHASE = "__main__:MovePhaseManager.move_phase"


def install_fake_llm_manager():
    """会話5関数を持つ `scripts.llm.llm_manager` を差し込む。

    会話への注入はローダの `modnpc` の関所が受け持ち、関所はいま差し込まれている素の関数の
    署名で引数を名前に直す。モジュールが無いと素通りするので、先頭4引数の並び
    （GAME.md §2.25）だけ揃えて置く。
    """
    name = "scripts.llm.llm_manager"
    module = sys.modules.get(name) or types.ModuleType(name)
    for target in CONVERSATION_TARGETS:
        def site(messages, character_life_log, player, character_instance,
                 *args, **kwargs):
            return "返答"
        setattr(module, target.split(":", 1)[1], site)
    sys.modules[name] = module


install_fake_llm_manager()


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
    return matches[0], folder, os.path.join(folder, entry)


MOD_FOLDER, MOD_DIR, MOD = find_mod("_reputation")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Area(object):
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name
        self.nodes = {}


class Node(object):
    def __init__(self, facilities):
        self.facilities = facilities


class Facility(object):
    """闘技場の施設。戦績は `config` の `current_phase` と `enemy_data`（GAME.md の闘技場の節）。"""

    def __init__(self, facility_type, name, config):
        self.facility_type = facility_type
        self.name = name
        self.config = config


class Character(object):
    """プレイヤーも NPC も同じ `Character`（GAME.md §2.20）。"""

    def __init__(self, name, area_history=None, current_area=None, profile=""):
        # `modnpc` の関所は相手を id で引く（名簿に居なければ `.id`）。
        # id が引けない相手は素通しになるので、名前を id にしておく。
        self.id = name
        self.name = name
        self.area_history = area_history
        self.current_area = current_area
        self.profile = profile


class Uncopyable(Character):
    def __copy__(self):
        raise TypeError("複製できない")


class World(object):
    def __init__(self, areas, quests, days_elapsed=100):
        self.name = "テスト世界"
        self.areas = areas
        self.quests = quests
        self.days_elapsed = days_elapsed


class App(object):
    def __init__(self, player, world):
        self.player = player
        self.world = world
        self.world_dict = {"world_data": {"world_name": world.name},
                           "quests": {}}


def quest(area_id, title, status="completed", difficulty=None):
    data = {"quest_title": title, "neighboring_settlement_id": area_id,
            "config": {"status": status, "level_of_detail": 1}}
    if difficulty is not None:
        data["difficulty"] = difficulty
    return data


def make_app(achievements=("盗賊団を退けた", "井戸を掘り直した"), lawfulness=10,
             total_days=12, quests=None, area_id="3"):
    history = {
        area_id: {"residency": {"total_days": total_days, "last_stay_end": 99},
                  "achievements": list(achievements),
                  "lawfulness": lawfulness},
        "7": {"residency": {"total_days": 1, "last_stay_end": 4},
              "achievements": ["よその土地の手柄"], "lawfulness": 10},
    }
    areas = {"3": Area("3", "灰の街"), "7": Area("7", "遠い港")}
    table = dict(quests or {})
    player = Character("旅人リン", area_history=history, current_area=area_id)
    return App(player, World(areas, table))


class FakeLLM(object):
    """`instantale_modloader.llm` の代わり。`ask` の返答を並べて渡す。

    差し替えるのは `ask` だけで、他の名前は本物へ渡す
    （`parse_json` のような素の関数までここに写すと、
    本物を直したときにこちらだけ古くなる）。
    """

    def __getattr__(self, name):
        return getattr(llm_module, name)

    def __init__(self, replies):
        self.replies = list(replies)
        self.asked = []
        self.others = []

    def ask(self, ctx, manager_name, message, *, timeout=None, structure=None,
            max_tokens=None, label="llm", write=None):
        content = message[0]["content"] if message else ""
        if OTHER_LAND in content and "土地の噂を集める役" in content:
            # よその土地（遠い港）の評判。照合は評判の立つ土地をすべて編む（版6）ので、
            # 並べた返答（いま居る土地と二つ名の分）を使わずに決まった返答を返し、別に控える。
            self.others.append({"manager": manager_name, "message": message})
            return OTHER_REPLY
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

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # 読み書きは**本物**を借りる。控えの並びを実ファイルで見たいので、
    # 辞書に溜める偽物にしない（TECH.md §3.11.1）。
    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def write_json(self, path, data):
        return ml.write_json(path, data, report=self.log_exc)

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="reputation_mod"):
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
    """mod を読み直して当て直す。設定は当てる前に差し替える。

    `sys` に置いた入れ物（ワーカーと控え）も落とす。
    落とさないと前の検査のキャッシュとワーカーが次の検査に残る。

    二つ名の線（名声・土地の数）は、指定が無ければ 0 と1土地に下げる。
    偽の依頼の多くは難易度を持たないので、既定の線のままだと二つ名の検査が素通りする。
    線そのものは「名声」の節で既定の値に戻して見る。
    """
    settings.setdefault("EPITHET_FAME", 0)
    settings.setdefault("EPITHET_MIN_LANDS", 1)
    module = load_mod()
    for key, value in settings.items():
        setattr(module, key, value)
    module.CHECK_INTERVAL = 0.0          # 検査では間引かない
    module.llm = FakeLLM(replies)
    if hasattr(sys, module.STATE_STORE_ATTR):
        delattr(sys, module.STATE_STORE_ATTR)
    ui_module.find_app = lambda: app     # material も同じモジュールを見ている
    # 関所（日数・着いた後の窓口、modnpc の頼み文の包み）は1つの世代に1枚なので、
    # ctx を作り直すたびに忘れさせる（残すと2回目の ctx に包みが渡らない）。
    gates.reset_all()
    if hasattr(sys, modnpc.INSTALLED_ATTR):
        delattr(sys, modnpc.INSTALLED_ATTR)
    ctx = FakeCtx()
    module.apply(ctx)
    return module, ctx


def store_of(module):
    return getattr(sys, module.STATE_STORE_ATTR)


def drain(module, timeout=10.0):
    """編纂のワーカーが仕事を片付けるまで待つ。"""
    return store_of(module)["worker"].drain(timeout)


def trigger(module, ctx, app, target=ELAPSE):
    """素材の照合を起こして、編纂が終わるまで待つ。"""
    calls = []
    ctx.hooks[target](lambda *a, **k: calls.append(a), app, 1)
    drain(module)
    return calls


def call_conv(ctx, target, npc, use_kwargs=False, args_before=3):
    """会話関数を `modnpc` の関所越しに1回呼ぶ。本体が受け取った NPC と、呼ばれた回数を返す。

    関所は素の関数の署名で引数を組み直して本体へ渡すので、
    キーワードで渡した `character_instance` も位置で届くことがある（両方を見る）。
    """
    seen = {}

    def orig(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        seen["times"] = seen.get("times", 0) + 1
        return "返答"

    head = ["messages", "life_log", "player"][:args_before]
    if use_kwargs:
        result = ctx.hooks[target](orig, *head, character_instance=npc,
                                   area_achievements=["成した事"],
                                   area_residency={"total_days": 3})
        got = seen["kwargs"].get("character_instance",
                                  seen["args"][3] if len(seen["args"]) > 3 else None)
    else:
        result = ctx.hooks[target](orig, *head, npc, "worldview")
        got = seen["args"][3]
    return got, seen.get("times", 0), result


#: 控えの鍵。周回（世界×主人公）ごとに持つ（TECH.md §5.4）。`make_app` の主人公は「旅人リン」。
TEST_KEY = "テスト世界" + state_module.PLAYTHROUGH_SEP + "旅人リン"


def cache_file(module, world=TEST_KEY):
    from instantale_modloader.state import world_filename
    return os.path.join(STATE_DIR, module.STATE_DIRNAME, world_filename(world))


def read_cache(module, world=TEST_KEY):
    path = cache_file(module, world)
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def clear_cache(module):
    path = os.path.join(STATE_DIR, module.STATE_DIRNAME)
    if os.path.isdir(path):
        for name in os.listdir(path):
            os.remove(os.path.join(path, name))


AREA_REPLY = json.dumps(
    {"reputation": "盗賊団を退けた者としてよく知られている。"}, ensure_ascii=False)
AREA_REPLY2 = json.dumps(
    {"reputation": "竜を追い払った者と噂されている。"}, ensure_ascii=False)
EPI_REPLY = json.dumps({"epithet": "灰の街の盾",
                        "description": "盗賊団から街を守った盾のような存在、"
                                       "という評判から来た名。"},
                       ensure_ascii=False)
EPI_RENAME = json.dumps({"epithet": "竜追い"}, ensure_ascii=False)
#: よその土地（`make_app` の土地7）の名前と、その評判への決まった返答（`FakeLLM`）。
OTHER_LAND = "遠い港"
OTHER_REPLY = json.dumps({"reputation": "港で荷を守った者と噂されている。"}, ensure_ascii=False)
EPI_EMPTY = json.dumps({"epithet": ""}, ensure_ascii=False)
EPI_ECHO = json.dumps({"epithet": "旅人リン"}, ensure_ascii=False)


def managers(module):
    return [entry["manager"] for entry in module.llm.asked]


def prompt_of(module, index):
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
    # 前回の実行が残した控えを掃除する。
    # 残っていると「素材が変わっていない」と判定され、編纂の検査が素通りする。
    leftovers = os.path.join(STATE_DIR, "reputation")
    if os.path.isdir(leftovers):
        for name in os.listdir(leftovers):
            os.remove(os.path.join(leftovers, name))

    module = load_mod()
    material = sys.modules["reputation_mod.material"]

    print("素材")
    app = make_app(quests={"1": quest("3", "橋の修復"),
                           "2": quest("3", "まだ途中", status="incomplete"),
                           "3": quest("7", "よその依頼")})
    item = material.gather(app, "3")
    check("その土地の achievements を拾う",
          item["achievements"] == ["盗賊団を退けた", "井戸を掘り直した"],
          item["achievements"])
    check("片付いた依頼だけを、その土地のぶんだけ拾う",
          item["quests"] == ["橋の修復"], item["quests"])
    check("手配度と滞在日数も一緒に拾う",
          item["lawfulness"] == 10
          and item["residency"] == {"total_days": 12, "last_stay_end": 99},
          item)
    check("土地の名前を引く", item["area_name"] == "灰の街", item["area_name"])
    check("一度も訪れていない土地は None", material.gather(app, "99") is None)
    empty = make_app(achievements=(), area_id="3")
    check("achievements が空でも落ちない",
          material.gather(empty, "3")["achievements"] == [])
    stats = material.survey(app)
    check("survey が全土地を1回で数える",
          {aid: (item["deeds"], item["wanted"]) for aid, item in stats.items()}
          == {"3": (3, False), "7": (2, False)}, stats)
    check("survey が成した事と完了した依頼を分けて数える",
          (stats["3"]["achievements"], stats["3"]["done"]) == (2, 1), stats["3"])
    app.player.area_history["7"]["lawfulness"] = -5
    check("survey が手配も拾う", material.survey(app)["7"]["wanted"])
    app.player.area_history["7"]["lawfulness"] = 10

    print("印")
    base = material.fingerprint(item)
    item["residency"]["total_days"] = 900
    check("滞在日数が増えても印は変わらない",
          material.fingerprint(item) == base, material.fingerprint(item))
    item["area_name"] = "別名"
    check("土地の名前が変わっても印は変わらない",
          material.fingerprint(item) == base)
    item["achievements"].append("竜を追い払った")
    check("成した事が増えれば印は変わる", material.fingerprint(item) != base)
    item["achievements"].pop()
    item["lawfulness"] = -20
    check("手配度が変われば印は変わる", material.fingerprint(item) != base)
    check("同じ素材からは同じ印",
          material.fingerprint({"achievements": ["a"], "lawfulness": 1,
                                "quests": []})
          == material.fingerprint({"quests": [], "lawfulness": 1,
                                   "achievements": ["a"]}))

    print("立つか")
    check("2件あれば立つ",
          module.qualifies({"achievements": ["a", "b"], "quests": []}))
    check("1件では立たない",
          not module.qualifies({"achievements": ["a"], "quests": []}))
    check("依頼も件数に数える",
          module.qualifies({"achievements": ["a"], "quests": ["q"]}))
    check("手配されていれば1件でも立つ",
          module.qualifies({"achievements": ["a"], "quests": [],
                            "lawfulness": -10}))
    check("平常（10）は手配ではない",
          not module.is_wanted({"lawfulness": 10}))
    check("手配度が読めなくても手配とみなさない",
          not module.is_wanted({"lawfulness": None}))
    check("True を手配度 1 と読まない", not module.is_wanted({"lawfulness": True}))

    print("編纂")
    module, ctx = fresh_mod(app, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app)
    asked = module.llm.asked
    check("評判 → 二つ名の順に2回だけ回る",
          managers(module) == [module.MANAGER_COMPILE, module.MANAGER_EPITHET],
          managers(module))
    prompt = prompt_of(module, 0) if asked else ""
    check("頼み文に成した事が載る", "盗賊団を退けた" in prompt)
    check("頼み文に片付けた依頼が載る", "橋の修復" in prompt)
    check("頼み文に土地の名前が載る", "灰の街" in prompt)
    check("頼み文に手配の状況が言葉で載る（生の数値を渡さない）",
          "咎められていない" in prompt and "lawfulness" not in prompt)
    check("評判の頼み文では二つ名を頼まない", "二つ名" not in prompt)
    check("制限時間を必ず渡す", asked and asked[0]["timeout"] == module.COMPILE_TIMEOUT,
          asked and asked[0]["timeout"])
    check("居ない土地でも評判の立つ土地は編む（その土地に着くまで待たない）",
          len(module.llm.others) == 1
          and (read_cache(module) or {}).get("areas", {}).get("7", {}).get("reputation")
          == "港で荷を守った者と噂されている。", read_cache(module))
    order = [line for line in read_log() if "] 編纂を予約: " + TEST_KEY in line][-2:]
    check("いま居る土地を先に積む",
          len(order) == 2 and order[0].split(" / ")[1].startswith("3")
          and order[1].split(" / ")[1].startswith("7"), order)
    clear_cache(module)
    module, ctx = fresh_mod(app, replies=[AREA_REPLY, EPI_REPLY])
    loaded = []
    ctx.hooks[LOAD_GAME](lambda *a, **k: loaded.append(a), app)
    drain(module)
    check("読み込みの後に照合して編む（移動や日送りを待たない）",
          loaded and managers(module) == [module.MANAGER_COMPILE, module.MANAGER_EPITHET]
          and len(module.llm.others) == 1, (loaded, managers(module)))
    check("囲み付きの JSON も読める",
          module.parse_result("```json\n" + AREA_REPLY + "\n```")
          == {"reputation": "盗賊団を退けた者としてよく知られている。"})
    check("前置き付きの JSON も読める",
          module.parse_result("はい。" + AREA_REPLY)["reputation"].startswith("盗賊団"))
    plain = module.parse_result("この土地では義理堅い者として知られている。")
    check("素の文章は評判文として受ける",
          plain and plain["reputation"].startswith("この土地"), plain)
    check("空の返答は読めないものとして扱う", module.parse_result("") is None)
    check("None は読めないものとして扱う", module.parse_result(None) is None)
    check("評判文が空の JSON は読めないものとして扱う",
          module.parse_result('{"reputation": ""}') is None)

    print("二つ名")
    # -- 印と契機（素の関数） --
    mark = module.epithet_mark({"3": {"deeds": 3, "wanted": False},
                                "7": {"deeds": 1, "wanted": False}})
    check("立つ土地だけが印に入り、合計は全土地で数える",
          mark == {"qualifying": ["3"], "wanted": [], "deeds": 4, "fame": 0}, mark)
    check("立つ土地が無ければ契機にしない",
          module.epithet_due(None, module.epithet_mark(
              {"3": {"deeds": 1, "wanted": False}}))[0] is False)
    check("控えが無ければ初回", module.epithet_due(None, mark) == (True, "初回"))
    same = {"mark": {"qualifying": ["3"], "wanted": [], "deeds": 4}}
    check("同じ印なら立たない", module.epithet_due(same, mark)[0] is False)
    grown = module.epithet_mark({"3": {"deeds": 3, "wanted": False},
                                 "7": {"deeds": 2, "wanted": False}})
    check("評判の立つ土地が増えれば立つ", module.epithet_due(same, grown)[0])
    flipped = module.epithet_mark({"3": {"deeds": 3, "wanted": True}})
    check("手配の反転で立つ", module.epithet_due(
        {"mark": {"qualifying": ["3"], "wanted": [], "deeds": 3}}, flipped)[0])
    doubled = module.epithet_mark({"3": {"deeds": 8, "wanted": False}})
    check("出来事の合計が倍で立つ", module.epithet_due(
        {"mark": {"qualifying": ["3"], "wanted": [], "deeds": 4}}, doubled)[0])
    check("倍に満たなければ立たない", module.epithet_due(
        {"mark": {"qualifying": ["3"], "wanted": [], "deeds": 5}}, doubled)[0]
        is False)

    # -- 返答の読み取り --
    parsed = module.parse_epithet(EPI_REPLY)
    check("JSON の二つ名と説明を読む",
          parsed["epithet"] == "灰の街の盾"
          and parsed["description"].startswith("盗賊団から"), parsed)
    check("説明が無ければ空文字",
          module.parse_epithet('{"epithet": "影"}')
          == {"epithet": "影", "description": ""})
    check("説明は上限で切り詰める（名と違って捨てない）",
          len(module.parse_epithet(json.dumps(
              {"epithet": "影", "description": "あ" * 300},
              ensure_ascii=False))["description"])
          <= module.EPITHET_DESC_CHARS + 1)
    check("鉤括弧付きの二つ名から括弧を外す",
          module.parse_epithet('{"epithet": "「影」"}')["epithet"] == "影")
    check("「なし」は名なし（消費）として読む",
          module.parse_epithet('{"epithet": "なし"}')["epithet"] == "")
    check("短い素の文章は二つ名として受ける（説明は空）",
          module.parse_epithet("影の医者")
          == {"epithet": "影の医者", "description": ""})
    check("上限を超える素の文章は読めない扱い",
          module.parse_epithet("あ" * 30) is None)
    check("上限を超える JSON の二つ名は名なし（消費）",
          module.parse_epithet(json.dumps({"epithet": "あ" * 30},
                                          ensure_ascii=False))["epithet"] == "")
    check("壊れた JSON は読めない扱い", module.parse_epithet("{壊れた") is None)
    check("空の返答は読めない扱い", module.parse_epithet("") is None)
    check("None は読めない扱い", module.parse_epithet(None) is None)

    # -- 長さの狙い --
    module.EPITHET_RANDOM_LENGTH = False
    check("ランダムを切れば狙いは常に上限",
          module.pick_epithet_length() == module.EPITHET_CHARS)
    prompt = module.build_epithet_messages(
        "リン", [("灰の街", "噂。")], "", length=module.pick_epithet_length()
    )[0]["content"]
    check("ランダムを切れば頼み文は「以内」だけ",
          "字以内" in prompt and "字くらい" not in prompt, prompt)
    module.EPITHET_RANDOM_LENGTH = True
    drawn = []
    real_random = module._RNG
    real_cap = module.EPITHET_CHARS

    class FakeRandom(object):
        @staticmethod
        def randint(low, high):
            drawn.append((low, high))
            return 7

    module._RNG = FakeRandom
    try:
        length = module.pick_epithet_length()
        check("狙いは下限〜上限から引く",
              length == 7 and drawn == [(module.EPITHET_LENGTH_FLOOR,
                                         module.EPITHET_CHARS)], drawn)
        prompt = module.build_epithet_messages(
            "リン", [("灰の街", "噂。")], "", length=length)[0]["content"]
        check("頼み文に狙いと上限の両方が載る",
              "全角7字くらい" in prompt
              and "{}字は超えない".format(module.EPITHET_CHARS) in prompt,
              prompt)
        module.EPITHET_CHARS = module.EPITHET_LENGTH_FLOOR
        check("上限が下限まで下がっていれば引かずに上限",
              module.pick_epithet_length() == module.EPITHET_LENGTH_FLOOR
              and len(drawn) == 1, drawn)
    finally:
        module._RNG = real_random
        module.EPITHET_CHARS = real_cap
    prompt = module.build_epithet_messages(
        "リン", [("灰の街", "噂。")], "", length=module.EPITHET_CHARS
    )[0]["content"]
    check("狙いが上限と同じ回は「以内」に畳む",
          "字以内" in prompt and "字くらい" not in prompt, prompt)

    # -- 写しの番人 --
    sources = {"achievements": ["盗賊団を退けた"], "quests": ["沖仲仕の代役"]}
    check("本人の名前そのままは写し",
          module.echoes_material("旅人リン", "旅人リン", sources))
    check("名前の一部だけでも写し",
          module.echoes_material("旅人", "旅人リン", sources))
    check("依頼の題名そのままは写し",
          module.echoes_material("沖仲仕の代役", "旅人リン", sources))
    check("題名から「の」を抜いただけでも写し",
          module.echoes_material("沖仲仕代役", "旅人リン", sources))
    check("題名の派生形は写しとしない",
          not module.echoes_material("橋の修復者", "旅人リン",
                                     {"quests": ["橋の修復"]}))
    check("名前に語が足された形は写しとしない",
          not module.echoes_material("暴走のリン", "リン", sources))
    check("名前を除いた残りが1字なら写し",
          module.echoes_material("大リン", "リン", sources))
    check("創作の二つ名は写しとしない",
          not module.echoes_material("灰の街の盾", "旅人リン", sources))
    check("素材なし（世界の二つ名）でも名前の写しは弾く",
          module.echoes_material("旅人リン", "旅人リン", None))

    # -- 契機から編纂までの通し（土地3=3件・土地7=1件 → 立つのは3だけ） --
    clear_cache(module)
    app_k = make_app(quests={"1": quest("3", "橋の修復")})
    module, ctx = fresh_mod(app_k, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app_k)
    check("初回は評判 → 二つ名の順で編む",
          managers(module) == [module.MANAGER_COMPILE, module.MANAGER_EPITHET],
          managers(module))
    epi_prompt = prompt_of(module, 1)
    check("二つ名の頼み文には各地の評判文が載る",
          "灰の街" in epi_prompt and "盗賊団を退けた者として" in epi_prompt,
          epi_prompt)
    check("頼み文に説明の指示が載る", "description" in epi_prompt, epi_prompt)
    check("説明が控えに載る",
          read_cache(module)["epithet"]["description"].startswith("盗賊団から"),
          read_cache(module)["epithet"])
    check("初回の頼み文に「いままでの二つ名」は無い",
          "いままでの二つ名" not in epi_prompt)
    check("写し禁止の断りが載る", "そのまま二つ名にしない" in epi_prompt)
    trigger(module, ctx, app_k)
    check("質的な変化が無ければ編み直さない", len(module.llm.asked) == 2,
          managers(module))

    app_k.player.area_history["7"]["lawfulness"] = -5
    module.llm.replies = [EPI_REPLY]
    trigger(module, ctx, app_k)
    check("手配の反転で二つ名だけが編み直される",
          managers(module)[2:] == [module.MANAGER_EPITHET], managers(module))
    keep_prompt = prompt_of(module, 2)
    check("2回目からはいままでの名を渡して据え置きを既定にする",
          "いままでの二つ名は「灰の街の盾」" in keep_prompt
          and "同じ名をそのまま返す" in keep_prompt, keep_prompt)
    record = read_cache(module)["epithet"]
    check("反転が印に控えられる", record["mark"]["wanted"] == ["7"],
          record["mark"])

    app_k.player.area_history["7"]["lawfulness"] = 10
    module.llm.replies = [None]
    trigger(module, ctx, app_k)
    check("読めない返答では印を控えない（後で引き直す）",
          read_cache(module)["epithet"]["mark"]["wanted"] == ["7"],
          read_cache(module)["epithet"]["mark"])
    module.llm.replies = [EPI_EMPTY]
    trigger(module, ctx, app_k)
    record = read_cache(module)["epithet"]
    check("名なしの返答は前の名を残して印を消費する",
          record["epithet"] == "灰の街の盾"
          and record["mark"]["wanted"] == [], record)
    check("名を残すときは説明も対で残す",
          record["description"].startswith("盗賊団から"), record)
    asks = len(module.llm.asked)
    trigger(module, ctx, app_k)
    check("消費した後は同じ問いを繰り返さない",
          len(module.llm.asked) == asks, managers(module))

    app_k.player.area_history["3"]["achievements"] += [
        "手柄{}".format(i) for i in range(4)]          # 合計 4 → 8（倍）
    module.llm.replies = [AREA_REPLY2, EPI_RENAME]
    trigger(module, ctx, app_k)
    check("行いが倍になると評判 → 二つ名の順で編み直す",
          managers(module)[-2:] == [module.MANAGER_COMPILE,
                                    module.MANAGER_EPITHET], managers(module))
    check("改名が控えに載る", read_cache(module)["epithet"]["epithet"] == "竜追い",
          read_cache(module)["epithet"])

    app_k.world.quests["9"] = quest("7", "塩の護送")   # 土地7が2件 → 立つ土地が増える
    module.llm.replies = [EPI_ECHO]
    trigger(module, ctx, app_k)
    record = read_cache(module)["epithet"]
    check("本人の名前の写しは捨てて前の名を残す（印は消費）",
          record["epithet"] == "竜追い"
          and record["mark"]["qualifying"] == ["3", "7"], record)

    # 評判の立つ土地（7）で片付けた依頼の題名がそのまま返った回。
    # 番人に素材が渡っていないと、名前の写しだけを弾いてこの名を通してしまう。
    app_k.player.area_history["7"]["lawfulness"] = -5    # 手配の反転で立て直す
    module.llm.replies = [json.dumps({"epithet": "塩の護送"}, ensure_ascii=False)]
    trigger(module, ctx, app_k)
    record = read_cache(module)["epithet"]
    check("依頼の題名の写しも捨てて前の名を残す（印は消費）",
          record["epithet"] == "竜追い" and record["mark"]["wanted"] == ["7"],
          record)
    app_k.player.area_history["7"]["lawfulness"] = 10
    module.llm.replies = [json.dumps({"epithet": "よその土地の手柄"},
                                     ensure_ascii=False)]
    trigger(module, ctx, app_k)
    record = read_cache(module)["epithet"]
    check("成した事の写しも捨てて前の名を残す",
          record["epithet"] == "竜追い" and record["mark"]["wanted"] == [],
          record)

    # -- 引き直し（人物欄のボタンが書く頼みのファイル） --
    from instantale_modloader.state import world_filename
    reroll = os.path.join(STATE_DIR, module.STATE_DIRNAME,
                          world_filename(TEST_KEY, module.REROLL_SUFFIX))
    ml.write_json(reroll, {"reroll": True})
    module.llm.replies = [json.dumps({"epithet": "霧払い"}, ensure_ascii=False)]
    asks = len(module.llm.asked)
    trigger(module, ctx, app_k)
    check("頼みがあれば質的変化なしでも編み直す",
          len(module.llm.asked) == asks + 1
          and read_cache(module)["epithet"]["epithet"] == "霧払い",
          (managers(module)[-1:], read_cache(module)["epithet"]))
    check("頼みのファイルは消される", not os.path.exists(reroll))
    reroll_prompt = prompt_of(module, len(module.llm.asked) - 1)
    check("頼み文はいまの名を除く指示になる",
          "「竜追い」という名は使わない" in reroll_prompt
          and "いままでの二つ名" not in reroll_prompt, reroll_prompt)
    ml.write_json(reroll, {"reroll": True})
    module.llm.replies = [json.dumps({"epithet": "霧払い"}, ensure_ascii=False)]
    trigger(module, ctx, app_k)
    check("除けと言った名が返っても前の名のまま（もう一度押せる）",
          read_cache(module)["epithet"]["epithet"] == "霧払い"
          and not os.path.exists(reroll), read_cache(module)["epithet"])
    check("人物欄の開閉にもフックが在る", SHEET_TOGGLE in ctx.hooks,
          sorted(ctx.hooks))
    ml.write_json(reroll, {"reroll": True})
    module.llm.replies = [json.dumps({"epithet": "峠の渡し手"},
                                     ensure_ascii=False)]
    ctx.hooks[SHEET_TOGGLE](lambda *a, **k: None, object())
    drain(module)
    check("人物欄を閉じた直後に頼みを拾う（間引きを解く）",
          read_cache(module)["epithet"]["epithet"] == "峠の渡し手",
          read_cache(module)["epithet"])

    print("名声")
    # -- 数え方（素の関数）。1件の名声は難易度の2乗÷25 --
    check("依頼1件の名声は難易度の2乗÷25（25 で 25、5 で 1）",
          module.deed_fame(25) == 25 and module.deed_fame(5) == 1 and module.deed_fame(None) == 0)
    check("完了した依頼を1件ずつ数える（成した事は依頼と重なるので足さない）",
          module.area_fame({"done_difficulties": [25, 5], "done": 2, "achievements": 2,
                            "difficulties": [5, 25]}) == 26)
    check("依頼より多い成した事は、その土地の依頼の難易度の中央値で数える",
          module.area_fame({"done": 0, "achievements": 1,
                            "difficulties": [24, 19, 23, 21]}) == 19)
    check("依頼の難易度が分からない土地の余りの成した事は数えない",
          module.area_fame({"achievements": 2, "difficulties": []}) == 0)
    check("物語の依頼は2倍",
          module.area_fame({"story_difficulties": [25]}) == 50)
    check("闘技場は破った相手の格ごとに数える",
          module.area_fame({"arena_ranks": [30, 44]}) == 113)
    check("開始の村の依頼は20件重ねても名声は小さい",
          module.area_fame({"done_difficulties": [3] * 20, "done": 20}) == 7)
    check("名声に手配度は混ぜない",
          module.area_fame({"done_difficulties": [25], "lawfulness": -30}) == 25)

    # -- 悪名（手配の重さが増えた分の累計） --
    ledger, changed = module.accumulate_notoriety({}, {"3": {"lawfulness": -30},
                                                       "7": {"lawfulness": 10}})
    check("初めて見た手配はそのときの重さから数える（平常の土地は載せない）",
          changed and ledger == {"3": {"total": 30, "last": 30}}, ledger)
    ledger, changed = module.accumulate_notoriety(ledger, {"3": {"lawfulness": -20}})
    check("手配が軽くなっても悪名は減らない",
          changed and ledger["3"] == {"total": 30, "last": 20}, ledger)
    ledger, changed = module.accumulate_notoriety(ledger, {"3": {"lawfulness": -35}})
    check("軽くなった後にまた重くなれば、増えた分を足す",
          ledger["3"] == {"total": 45, "last": 35}, ledger)
    ledger, changed = module.accumulate_notoriety(ledger, {"3": {"lawfulness": 10}})
    check("手配が解けても悪名は残る", ledger["3"] == {"total": 45, "last": 0}, ledger)
    _same, changed = module.accumulate_notoriety(ledger, {"3": {"lawfulness": 10}})
    check("変わらなければ書かない", changed is False)

    # -- 線（名声＋悪名と土地の数） --
    stats_f = {"3": {"deeds": 3, "wanted": False, "done_difficulties": [50]},
               "7": {"deeds": 2, "wanted": False, "done_difficulties": [25]},
               "9": {"deeds": 1, "wanted": False}}
    saved = (module.EPITHET_FAME, module.EPITHET_MIN_LANDS, module.LAND_NAME_FAME)
    module.EPITHET_FAME, module.EPITHET_MIN_LANDS, module.LAND_NAME_FAME = 100, 2, 100
    try:
        summary = module.fame_summary(stats_f)
        check("名声の控えは FAME_RECORD_KEYS の並び",
              tuple(summary) == module.FAME_RECORD_KEYS, tuple(summary))
        check("名声と土地の数が線に届けば付く",
              summary["total"] == 125 and summary["lands"] == 2 and summary["eligible"]
              and summary["areas"] == {"3": {"fame": 100, "notoriety": 0, "standing": True},
                                       "7": {"fame": 25, "notoriety": 0, "standing": True}},
              summary)
        stats_f["7"]["deeds"] = 1
        check("名声が足りても土地が1つなら付かない",
              not module.fame_summary(stats_f)["eligible"])
        stats_f["7"]["deeds"] = 2
        stats_f["3"]["done_difficulties"] = [30]
        check("土地が2つでも名声＋悪名が足りなければ付かない",
              not module.fame_summary(stats_f)["eligible"])
        mixed = module.fame_summary(stats_f, {"9": {"total": 40, "last": 40}})
        check("名声が足りなくても悪名と合わせて届けば付く",
              mixed["eligible"] and mixed["notoriety"] == 40
              and mixed["areas"]["9"] == {"fame": 0, "notoriety": 40, "standing": False}, mixed)
        stats_f["5"] = {"deeds": 2, "wanted": False, "achievements": 2}
        check("名声が 0 でも評判の立つ土地は控えに載せる（画面が「まだ編んでいない」と言える）",
              module.fame_summary(stats_f)["areas"]["5"] == {"fame": 0, "notoriety": 0,
                                                             "standing": True},
              module.fame_summary(stats_f)["areas"])
        del stats_f["5"]
        check("線に届かない印では契機にしない",
              module.epithet_due(None, module.epithet_mark(stats_f))
              == (False, "名声が足りない"))
        check("印の名声は名声＋悪名",
              module.epithet_mark(stats_f, {"9": {"total": 40, "last": 40}})["fame"] == 101)
        check("版5までの印は土地の数で見る（1つの土地の中で付いた名）",
              not module.mark_eligible({"qualifying": ["3"], "deeds": 9}))
        check("版5までの印でも土地が2つ以上なら付いていたものとみなす",
              module.mark_eligible({"qualifying": ["3", "7"], "deeds": 9}))
        fame_block = {"areas": {"3": {"fame": 100, "notoriety": 0},
                                "7": {"fame": 25, "notoriety": 75},
                                "9": {"fame": 60, "notoriety": 0}}}
        check("呼び名はその土地の名声＋悪名が線を越えた土地だけ",
              module.land_named(fame_block, "3") and module.land_named(fame_block, "7")
              and not module.land_named(fame_block, "9") and not module.land_named(fame_block, "5"))
        module.USE_LAND_NAMES = False
        check("呼び名を切れば付かない", not module.land_named(fame_block, "3"))
        module.USE_LAND_NAMES = True
    finally:
        module.EPITHET_FAME, module.EPITHET_MIN_LANDS, module.LAND_NAME_FAME = saved

    # -- 素材（依頼の帰属・物語の依頼・闘技場） --
    split = make_app(quests={"1": dict(quest("3", "峠の護送", difficulty=10),
                                       quest_area_id="7")})
    stats_s = material.survey(split)
    check("難易度は1件を1つの土地にだけ足す（件数は両方に数える）",
          stats_s["3"]["done_difficulties"] == [10] and stats_s["7"]["done_difficulties"] == []
          and stats_s["3"]["deeds"] == 3 and stats_s["7"]["deeds"] == 2, stats_s)
    check("片付いていない依頼も土地の格には数える",
          material.survey(make_app(quests={"1": quest("3", "途中", status="incomplete",
                                                      difficulty=7)}))["3"]["difficulties"]
          == [7])
    saga = make_app()
    saga.world.story_quests = {"0": quest("3", "霧の境界の捕食者", difficulty=14),
                               "1": quest("7", "まだの節", status="incomplete", difficulty=28)}
    saga.world.areas["3"].nodes = {"1": Node({"5": Facility("colosseum", "灰の闘技場", {
        "current_phase": 4, "enemy_data": {"0": {"data": {"rank": 30}}, "2": {"data": {"rank": 44}},
                                           "4": {"data": {"rank": 58}}}})})}
    stats_g = material.survey(saga)
    check("果たした物語の依頼を土地に数える（果たしていない節は数えない）",
          stats_g["3"]["story_difficulties"] == [14] and stats_g["7"]["story_difficulties"] == [],
          stats_g)
    check("闘技場は勝った試合の相手の格だけを数える（次の相手は数えない）",
          stats_g["3"]["arena_ranks"] == [30, 44], stats_g["3"])
    check("物語の依頼と闘技場の勝ちも行いの件数に入る",
          stats_g["3"]["deeds"] == 2 + 1 + 2, stats_g["3"])
    item_g = material.gather(saga, "3")
    check("評判の素材に物語の依頼と闘技場の戦績が載る",
          item_g["story"] == ["霧の境界の捕食者"]
          and item_g["arena"] == {"name": "灰の闘技場", "wins": 2, "top_rank": 44}, item_g)
    prompt_g = module.build_messages(item_g, "旅人リン")[0]["content"]
    check("頼み文に物語の依頼と闘技場の戦績が載る",
          "霧の境界の捕食者" in prompt_g and "灰の闘技場で2勝" in prompt_g, prompt_g)
    check("闘技場も物語の依頼も無ければ印は版5と同じ",
          material.fingerprint(material.gather(make_app(), "3"))
          == material.fingerprint({k: v for k, v in material.gather(make_app(), "3").items()
                                   if k not in ("story", "arena")}))

    # -- 線に届くまで（開始の村の駆除だけ） --
    npc = Character("宿の主人", profile="この街で三代続く宿の主人。")
    clear_cache(module)
    app_f = make_app(quests={"1": quest("3", "鼠退治", difficulty=3)})
    module, ctx = fresh_mod(app_f, replies=[AREA_REPLY],
                            EPITHET_FAME=100, EPITHET_MIN_LANDS=2, LAND_NAME_FAME=100)
    trigger(module, ctx, app_f)
    check("線に届かなければ評判だけを編む",
          managers(module) == [module.MANAGER_COMPILE], managers(module))
    check("呼び名の線に届かない土地では呼び名を頼まない",
          "呼び名" not in prompt_of(module, 0), prompt_of(module, 0))
    fame = (read_cache(module) or {}).get("fame", {})
    check("名声を控える（駆除の依頼は誤差）",
          fame.get("total") == 1 and fame.get("eligible") is False
          and fame.get("threshold") == 100 and fame.get("min_lands") == 2, fame)
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("線に届かなければ評判だけを注入する",
          "盗賊団" in got.profile and "二つ名で知られている" not in got.profile
          and "と呼ばれている" not in got.profile, got.profile)
    # 版5までに1つの土地の中で付いた名が控えに残っている。
    data = read_cache(module)
    data["epithet"] = {"epithet": "鼠捕り", "description": "", "day": 3,
                       "updated": "2000-01-01 00:00:00",
                       "mark": {"qualifying": ["3"], "wanted": [], "deeds": 3}}
    data.pop("fame")
    ml.write_json(cache_file(module), data)
    module, ctx = fresh_mod(app_f, EPITHET_FAME=100, EPITHET_MIN_LANDS=2, LAND_NAME_FAME=100)
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("線に届く前に付いた名は注入しない",
          "鼠捕り" not in got.profile and "盗賊団" in got.profile, got.profile)
    check("版5までのファイルは読んだ直後に名声を数えて足す",
          (read_cache(module) or {}).get("fame", {}).get("eligible") is False,
          read_cache(module))
    ml.write_json(reroll, {"reroll": True})
    trigger(module, ctx, app_f)
    check("線に届く前の引き直しの頼みは LLM を回さずに捨てる",
          not module.llm.asked and not os.path.exists(reroll), managers(module))

    # -- 線に届いた（中盤の依頼と、2つ目の土地） --
    app_f.world.quests["2"] = quest("3", "峠の魔獣", difficulty=60)
    app_f.world.quests["3"] = quest("7", "塩の護送", difficulty=30)
    app_f.world.quests["4"] = quest("7", "港の盗賊", difficulty=30)
    module.llm.replies = [AREA_REPLY2, EPI_EMPTY]
    trigger(module, ctx, app_f)
    fame = read_cache(module)["fame"]
    check("名声と土地が届けば付く線に乗る",
          fame["eligible"] and fame["total"] == 216 and fame["lands"] == 2, fame)
    check("届いたら評判 → 二つ名の順で編む",
          managers(module) == [module.MANAGER_COMPILE, module.MANAGER_EPITHET],
          managers(module))
    check("呼び名の線を越えた土地では呼び名も頼む",
          "呼び名を1つ書け" in prompt_of(module, 0) and '"nickname"' in prompt_of(module, 0),
          prompt_of(module, 0))
    check("呼び名が返らなくても欄を作る（照合のたびに頼み直さない）",
          read_cache(module)["areas"]["3"].get("nickname") == "", read_cache(module)["areas"]["3"])
    fresh_prompt = prompt_of(module, 1) if len(module.llm.asked) > 1 else ""
    check("届く前に付いた名はいままでの名として渡さない",
          "いままでの二つ名" not in fresh_prompt and "鼠捕り" not in fresh_prompt,
          fresh_prompt)
    record = read_cache(module)["epithet"]
    check("名が付かなかったとき、届く前の名へ戻さない",
          record["epithet"] == "" and record["mark"]["fame"] == 216, record)
    check("線に届いたことをログに残す",
          any("二つ名が付く線に届いた" in line for line in read_log()),
          read_log()[-3:])
    ml.write_json(reroll, {"reroll": True})
    module.llm.replies = [EPI_REPLY]
    trigger(module, ctx, app_f)
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("線に届いた後は二つ名を注入する",
          "土地を問わず「灰の街の盾」の二つ名で知られている。" in got.profile,
          got.profile)

    # -- 呼び名 --
    nick = json.dumps({"reputation": "峠の魔獣を討った者と噂されている。",
                       "nickname": "峠の守り手"}, ensure_ascii=False)
    app_f.player.area_history["3"]["achievements"].append("峠を開いた")
    module.llm.replies = [nick]
    trigger(module, ctx, app_f)
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("呼び名をその土地の注入に載せる",
          "この土地では「峠の守り手」と呼ばれている。" in got.profile, got.profile)
    app_f.player.area_history["3"]["achievements"].append("橋を架けた")
    module.llm.replies = [json.dumps({"reputation": "橋も架けた者と噂されている。",
                                      "nickname": "灰の街の英雄"}, ensure_ascii=False)]
    asks = len(module.llm.asked)
    trigger(module, ctx, app_f)
    check("2回目からはいまの呼び名を渡して据え置きを既定にする",
          "いまの呼び名は「峠の守り手」" in prompt_of(module, asks), prompt_of(module, asks))
    check("土地の名前を使った呼び名は捨てて前の名を残す",
          read_cache(module)["areas"]["3"]["nickname"] == "峠の守り手",
          read_cache(module)["areas"]["3"])
    # 評判の画面の呼び名の再生成（素材が変わっていないので評判の文は前のまま）
    before_rep = read_cache(module)["areas"]["3"]["reputation"]
    item_n = material.gather(app_f, "3")
    job = {"kind": "area", "world": TEST_KEY, "area_id": "3", "material": item_n,
           "player": "旅人リン", "day": 100, "fingerprint": material.fingerprint(item_n),
           "nickname": True, "exclude_nickname": "峠の守り手", "keep_reputation": True,
           "why": "評判の画面の呼び名の再生成"}
    module.llm.replies = [json.dumps({"reputation": "別の文になった。", "nickname": "峠の灯"},
                                     ensure_ascii=False)]
    asks = len(module.llm.asked)
    store_of(module)["worker"].enqueue(dict(job))
    drain(module)
    check("呼び名の再生成はいまの名を除いて頼む",
          "「峠の守り手」という呼び名は使わない" in prompt_of(module, asks)
          and "いまの呼び名" not in prompt_of(module, asks), prompt_of(module, asks))
    check("呼び名の再生成で呼び名だけが変わり、評判の文は前のまま",
          read_cache(module)["areas"]["3"]["nickname"] == "峠の灯"
          and read_cache(module)["areas"]["3"]["reputation"] == before_rep,
          read_cache(module)["areas"]["3"])
    job["exclude_nickname"] = "峠の灯"
    module.llm.replies = [json.dumps({"reputation": "文。", "nickname": "峠の灯"}, ensure_ascii=False)]
    store_of(module)["worker"].enqueue(dict(job))
    drain(module)
    check("再生成でも同じ名が返ったら前の名のまま（もう一度押せる）",
          read_cache(module)["areas"]["3"]["nickname"] == "峠の灯", read_cache(module)["areas"]["3"])
    data = read_cache(module)
    del data["areas"]["3"]["nickname"]
    ml.write_json(cache_file(module), data)
    module, ctx = fresh_mod(app_f, replies=[nick], EPITHET_FAME=100, EPITHET_MIN_LANDS=2,
                            LAND_NAME_FAME=100)
    trigger(module, ctx, app_f)
    check("呼び名の欄が無い控えは、印が同じでも1回編み直す",
          managers(module)[:1] == [module.MANAGER_COMPILE]
          and read_cache(module)["areas"]["3"].get("nickname") == "峠の守り手",
          (managers(module), read_cache(module)["areas"]["3"]))

    # -- 悪名（通し） --
    app_f.player.area_history["7"]["lawfulness"] = -30
    module.llm.replies = []
    trigger(module, ctx, app_f)
    check("手配された土地の悪名を控えに積む",
          read_cache(module)["notoriety"]["7"] == {"total": 30, "last": 30},
          read_cache(module).get("notoriety"))
    app_f.player.area_history["7"]["lawfulness"] = 10      # 役場で手配を解いた
    trigger(module, ctx, app_f)
    check("手配が解けても悪名は控えに残り、名声の控えにも出る",
          read_cache(module)["notoriety"]["7"]["total"] == 30
          and read_cache(module)["fame"]["areas"]["7"]["notoriety"] == 30
          and read_cache(module)["fame"]["notoriety"] == 30, read_cache(module)["fame"])

    # -- 評判の画面 --
    check("評判の画面の行を MOD 用のボタンの一覧に足す", module.ICON_KEY in hudicons_module.keys(),
          hudicons_module.keys())
    check("行の絵の3枚が揃っている",
          all(os.path.isfile(os.path.join(MOD_DIR, name)) for name in module.ICON_FILES))
    bucket = {"areas": {"3": {"name": "灰の街", "reputation": "盗賊団を退けた者として知られている。",
                              "nickname": "峠の守り手"},
                        "7": {"name": "遠い港", "reputation": "噂がある。", "nickname": "港の影"}},
              "epithet": {"epithet": "灰の街の盾"},
              "fame": {"total": 150, "notoriety": 30, "threshold": 100, "lands": 2, "min_lands": 2,
                       "eligible": True, "land_threshold": 100,
                       "areas": {"3": {"fame": 140, "notoriety": 0, "standing": True},
                                 "7": {"fame": 10, "notoriety": 30},
                                 "9": {"fame": 0, "notoriety": 5}}}}
    model = module.view_model(bucket, {"3": "灰の街", "7": "遠い港", "9": "北の砦"},
                              {"3": 10, "7": -5}, "灰の街")
    check("土地は名声＋悪名の大きい順",
          [row["id"] for row in model["rows"]] == ["3", "7", "9"], model["rows"])
    check("呼び名は線を越えた土地だけ出す",
          model["rows"][0]["nickname"] == "峠の守り手" and model["rows"][1]["nickname"] == "",
          model["rows"])
    check("評判の無い土地も名声か悪名があれば出す",
          model["rows"][2]["reputation"] == "" and model["rows"][2]["name"] == "北の砦")
    check("呼び名の再生成のボタンを置く土地（線を越えて評判も編み終わった土地）を画面に渡す",
          model["rows"][0]["named"] is True and model["rows"][1]["named"] is False
          and model["rows"][2]["named"] is False, model["rows"])
    check("再生成のボタンの絵（さいころ）はローダの共有の絵柄から引ける",
          len(ui_module.icon_strokes(sys.modules["reputation_mod.window"].REROLL_ICON)) == 4)
    check("評判の立つ土地かを画面に渡す（まだ編んでいない／まだ立たないの言い分け）",
          model["rows"][0]["standing"] is True and model["rows"][2]["standing"] is False,
          model["rows"])
    check("手配度と二つ名・数字を画面に渡す",
          model["rows"][1]["lawfulness"] == -5 and model["epithet"] == "灰の街の盾"
          and model["fame"] == 150 and model["notoriety"] == 30 and model["here"] == "灰の街",
          model)
    bucket["fame"]["eligible"] = False
    check("線に届く前は控えに名があっても画面に出さない",
          module.view_model(bucket, {}, {})["epithet"] == "")
    check("控えが空でも画面は組める",
          module.view_model({}, {}, {})["rows"] == [])

    print("長さ")
    module, ctx = fresh_mod(app, INJECT_DETAIL="brief")
    long_reply = "一つ目。二つ目。三つ目。四つ目。"
    check("brief は1文に切る",
          module.clean_reputation(long_reply) == "一つ目。",
          module.clean_reputation(long_reply))
    module, ctx = fresh_mod(app, INJECT_DETAIL="detailed")
    check("detailed は3文まで",
          module.clean_reputation(long_reply) == "一つ目。二つ目。三つ目。",
          module.clean_reputation(long_reply))
    # 切り詰めは `frames.short`（末尾に「…」が1字付く）。
    check("句点が無い返答は字数で頭打ちにする",
          len(module.clean_reputation("あ" * 500)) <= module.char_limit() + 1,
          len(module.clean_reputation("あ" * 500)))
    module, ctx = fresh_mod(app, INJECT_DETAIL="brief")
    check("知らない長さの指定は brief に倒す",
          module.sentence_limit() == 1)
    check("二つ名は切り詰めず、上限超えを捨てる",
          module.clean_epithet("あ" * (module.EPITHET_CHARS + 1)) == ""
          and module.clean_epithet("あ" * module.EPITHET_CHARS)
          == "あ" * module.EPITHET_CHARS)

    print("注入")
    clear_cache(module)
    module, ctx = fresh_mod(app, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app)
    npc = Character("宿の主人", profile="この街で三代続く宿の主人。")
    for target in CONVERSATION_TARGETS:
        got, times, result = call_conv(ctx, target, npc)
        label = target.rsplit(":", 1)[-1]
        check("{} で profile に評判が足される".format(label),
              got is not npc and "灰の街の盾" in getattr(got, "profile", ""),
              getattr(got, "profile", None))
        check("{} で本体は1回だけ呼ばれ、戻り値がそのまま返る".format(label),
              times == 1 and result == "返答")
    check("ゲーム側の NPC は書き換わらない",
          npc.profile == "この街で三代続く宿の主人。", npc.profile)
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("素の profile は消さずに後ろへ足す",
          got.profile.startswith("この街で三代続く宿の主人。"), got.profile)
    check("見出しに土地と人物の名前が入る",
          "【灰の街での旅人リンの評判】" in got.profile, got.profile)
    check("二つ名は世界の名として載る",
          "土地を問わず「灰の街の盾」の二つ名で知られている。" in got.profile,
          got.profile)
    check("好悪は NPC の側が決める、と但し書きが付く",
          "好悪はあなた自身の性格が決める" in got.profile)
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[2], npc,
                                use_kwargs=True)
    check("character_instance がキーワードで来ても足される",
          got is not npc and "灰の街の盾" in got.profile)
    module_off, ctx_off = fresh_mod(app, IN_CONVERSATION=False)
    got, times, _r = call_conv(ctx_off, CONVERSATION_TARGETS[0], npc)
    check("会話を切れば触らない", got is npc and times == 1)
    module_no, ctx_no = fresh_mod(app, USE_EPITHET=False)
    got, _times, _r = call_conv(ctx_no, CONVERSATION_TARGETS[0], npc)
    check("二つ名を切れば評判文だけを足す",
          "灰の街の盾" not in got.profile and "盗賊団" in got.profile,
          got.profile)
    trigger(module_no, ctx_no, app)
    check("二つ名を切れば編纂も走らない",
          module.MANAGER_EPITHET not in managers(module_no),
          managers(module_no))

    print("素通し")
    clear_cache(module)
    module, ctx = fresh_mod(make_app(achievements=("1件だけ",)),
                            replies=[AREA_REPLY])
    fresh_npc = Character("通行人", profile="通りすがり。")
    got, times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], fresh_npc)
    check("評判が無ければ足さない", got is fresh_npc and times == 1)
    check("編纂も走らない（素材が薄い土地）", not module.llm.asked)
    module, ctx = fresh_mod(app, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app)
    odd = Character("木彫りの像", profile=None)
    got, times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], odd)
    check("profile が None なら素の profile 無しとして足す",
          got is not odd and "灰の街の盾" in got.profile)
    odd = Character("数字の人", profile=123)
    got, times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], odd)
    check("profile が文字列でなければ素通し", got is odd and times == 1)
    stuck = Uncopyable("複製できない人", profile="ふつうの人。")
    got, times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], stuck)
    check("複製できなければ素通し", got is stuck and times == 1)
    got, times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], None)
    check("NPC が渡らなければ素通し", got is None and times == 1)
    ui_module.find_app = lambda: None
    got, times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("app が見つからなくても落ちずに素通し", got is npc and times == 1)
    ui_module.find_app = lambda: app
    check("素通しの理由がログに残る",
          any("評判はまだ無い" in line or "素通" in line or "profile" in line
              for line in read_log()), read_log()[-3:])

    print("情景描写")
    module, ctx = fresh_mod(app, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app)
    seen = {}

    def narrator_orig(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return "情景"

    ctx.hooks[NARRATOR](narrator_orig, "log", "action", "旅人の素性", "area", "loc")
    check("既定では情景描写に触らない", seen["args"][2] == "旅人の素性",
          seen["args"][2])
    module, ctx = fresh_mod(app, replies=[AREA_REPLY, EPI_REPLY],
                            IN_NARRATION=True)
    trigger(module, ctx, app)
    ctx.hooks[NARRATOR](narrator_orig, "log", "action", "旅人の素性", "area", "loc")
    check("ON なら player_profile に足す",
          seen["args"][2].startswith("旅人の素性") and "灰の街の盾" in seen["args"][2],
          seen["args"][2])
    ctx.hooks[NARRATOR](narrator_orig, "log", "action",
                        player_profile="旅人の素性")
    check("player_profile がキーワードで来ても足す",
          "灰の街の盾" in seen["kwargs"]["player_profile"])

    print("控え")
    clear_cache(module)
    module, ctx = fresh_mod(app, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app)
    data = read_cache(module)
    check("state\\ にファイルが1つできる", data is not None, cache_file(module))
    record = (data or {}).get("areas", {}).get("3", {})
    # 呼び名の欄は線を越えた土地にだけ作る
    check("土地の鍵の並びが RECORD_KEYS のとおり",
          tuple(record) == tuple(key for key in module.RECORD_KEYS if key != "nickname"),
          tuple(record))
    check("土地の控えに二つ名は持たない", "epithet" not in record, tuple(record))
    check("印を控えている",
          record.get("fingerprint") == material.fingerprint(
              material.gather(app, "3")), record.get("fingerprint"))
    check("編纂した日を控えている", record.get("day") == 100, record.get("day"))
    epithet = (data or {}).get("epithet", {})
    check("二つ名の鍵の並びが EPITHET_RECORD_KEYS のとおり",
          tuple(epithet) == module.EPITHET_RECORD_KEYS, tuple(epithet))
    check("二つ名の印は survey から組んだ質的状態",
          epithet.get("mark") == module.epithet_mark(material.survey(app)),
          epithet.get("mark"))
    trigger(module, ctx, app)
    check("素材が変わらなければ編纂し直さない", len(module.llm.asked) == 2,
          managers(module))
    module.llm.replies = [AREA_REPLY2]
    app.player.area_history["3"]["achievements"].append("竜を追い払った")
    trigger(module, ctx, app)
    check("素材が変われば評判だけ編纂し直す（質的な変化は無い）",
          managers(module)[2:] == [module.MANAGER_COMPILE], managers(module))
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("編纂し直した評判と据え置きの二つ名が注入される",
          "竜を追い払った者" in got.profile and "灰の街の盾" in got.profile,
          got.profile)
    module2, ctx2 = fresh_mod(app)
    got, _times, _r = call_conv(ctx2, CONVERSATION_TARGETS[0], npc)
    check("次に起動しても控えから読める（LLM を回さない）",
          "竜を追い払った者" in got.profile and "灰の街の盾" in got.profile
          and not module2.llm.asked, got.profile)
    other = make_app(area_id="7", achievements=("よその手柄", "もう1件"))
    other.world.name = "べつの世界"
    other.world_dict["world_data"]["world_name"] = "べつの世界"
    module3, ctx3 = fresh_mod(other, replies=[AREA_REPLY])
    got, _times, _r = call_conv(ctx3, CONVERSATION_TARGETS[0], npc)
    check("別の世界の評判は湧かない", got is npc, getattr(got, "profile", ""))
    clear_cache(module)
    app_w = make_app(achievements=("1件だけ",), lawfulness=-10)
    module, ctx = fresh_mod(app_w, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app_w)
    check("手配だけで立った評判を控える",
          "3" in (read_cache(module) or {}).get("areas", {}), read_cache(module))
    app_w.player.area_history["3"]["lawfulness"] = 10      # 役場で手配を解いた
    trigger(module, ctx, app_w)
    check("線を下回った土地の控えを外す",
          "3" not in (read_cache(module) or {}).get("areas", {}), read_cache(module))
    got, times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("線を下回った土地では古い評判を注入しない",
          got is npc and times == 1, getattr(got, "profile", ""))
    module.llm.replies = [AREA_REPLY2]
    app_w.player.area_history["3"]["lawfulness"] = -10     # 同じ素材でまた手配
    trigger(module, ctx, app_w)
    check("再び線に届けば印が同じでも編み直す",
          (read_cache(module) or {}).get("areas", {}).get("3", {}).get("reputation")
          == "竜を追い払った者と噂されている。", read_cache(module))

    print("周回")
    # 評判と二つ名は主人公の行いから編むので、控えは世界×主人公の鍵で持つ。
    # 世界名だけの控えは遊んでいる主人公のものとして移し、元のファイルを消す。
    # 別の主人公の周回が在る世界では移さない（ローダの `WorldStore.adopt`）。
    # ほかの検査が残した周回の控えと混ざらないよう、ここだけの世界名で見る。
    move_world = "移行の検査世界"
    move_mine = move_world + state_module.PLAYTHROUGH_SEP + "旅人リン"
    old_cache = {"areas": {"3": {"area": "3", "name": "灰の街", "day": 90,
                                 "updated": "2000-01-01 00:00:00",
                                 "reputation": "古井戸を守った者と知られている。",
                                 "fingerprint": "0" * 16}},
                 "epithet": {"epithet": "井戸守", "description": "", "day": 90,
                             "updated": "2000-01-01 00:00:00",
                             "mark": {"qualifying": ["3"], "wanted": [], "deeds": 2}}}

    def move_app(player_name="旅人リン"):
        found = make_app()
        found.world.name = move_world
        found.world_dict["world_data"]["world_name"] = move_world
        found.player.name = player_name
        return found

    def drop_move_files():
        folder = os.path.join(STATE_DIR, module.STATE_DIRNAME)
        for name in (os.listdir(folder) if os.path.isdir(folder) else ()):
            if name.startswith(move_world):
                os.remove(os.path.join(folder, name))

    drop_move_files()
    ml.write_json(cache_file(module, move_world), old_cache)
    app_m = move_app()
    module, ctx = fresh_mod(app_m)
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("世界名だけの控えを遊んでいる主人公の周回へ移して読む",
          "古井戸を守った者" in getattr(got, "profile", "")
          and "井戸守" in getattr(got, "profile", ""), getattr(got, "profile", ""))
    moved = dict(read_cache(module, move_mine) or {})
    moved.pop("fame", None)        # 版5までのファイルには無く、読んだ直後に数えて足す
    check("移した先は周回の鍵のファイル", moved == old_cache, moved)
    check("世界名だけの控えは消す", not os.path.exists(cache_file(module, move_world)))
    module, ctx = fresh_mod(move_app("前の主人公"))
    got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
    check("同じ世界の別の主人公には前の主人公の評判を注入しない",
          got is npc, getattr(got, "profile", ""))
    drop_move_files()
    ml.write_json(cache_file(module, move_world), old_cache)
    other_dir = os.path.join(STATE_DIR, "reputation_test_other")
    other_file = os.path.join(other_dir, state_module.world_filename(
        move_world + state_module.PLAYTHROUGH_SEP + "前の主人公"))
    try:
        ml.write_json(other_file, {})
        module, ctx = fresh_mod(move_app())
        got, _times, _r = call_conv(ctx, CONVERSATION_TARGETS[0], npc)
        check("別の主人公の周回が在る世界の控えは移さない",
              os.path.exists(cache_file(module, move_world))
              and read_cache(module, move_mine) is None, read_cache(module, move_mine))
        check("移さなかった世界名だけの控えは注入しない",
              got is npc, getattr(got, "profile", ""))
        check("移さなかった理由をログに残す",
              any("別の主人公の周回" in line for line in read_log()), read_log()[-3:])
    finally:
        if os.path.exists(other_file):
            os.remove(other_file)
        if os.path.isdir(other_dir) and not os.listdir(other_dir):
            os.rmdir(other_dir)
        drop_move_files()

    print("安全")
    clear_cache(module)
    app_s = make_app(quests={"1": quest("3", "橋の修復")})
    module, ctx = fresh_mod(app_s, replies=[AREA_REPLY, EPI_REPLY])
    trigger(module, ctx, app_s)
    before = read_cache(module)["areas"]["3"]["reputation"]
    module.llm.replies = [None]
    app_s.player.area_history["3"]["achievements"].append("別の手柄")
    trigger(module, ctx, app_s)
    check("LLM が返らなくても前の評判が残る",
          read_cache(module)["areas"]["3"]["reputation"] == before,
          read_cache(module)["areas"]["3"]["reputation"])
    module.llm.replies = ["{壊れた"]
    app_s.player.area_history["3"]["achievements"].append("もう1つの手柄")
    trigger(module, ctx, app_s)
    check("壊れた JSON は素の文章として受けない",
          module.parse_result("{壊れた") is None)
    check("壊れた返答でも前の評判が残る",
          read_cache(module)["areas"]["3"]["reputation"] == before)
    check("二つ名も残っている",
          read_cache(module)["epithet"]["epithet"] == "灰の街の盾",
          read_cache(module).get("epithet"))
    check("編纂の失敗で ERROR を出さない", not ctx.errors, ctx.errors)
    calls = []
    ctx.hooks[AREA_MOVE](lambda *a, **k: calls.append(a), app_s, "移動する")
    ctx.hooks[MOVE_PHASE](lambda *a, **k: calls.append(a), app_s)
    check("到着の2経路とも本体を必ず呼ぶ", len(calls) == 2, calls)

    print("名乗り")
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    module = load_mod()
    wrong = [key for key, decl in manifest["settings"].items()
             if getattr(module, key, object()) != decl["default"]]
    check("mod.json の既定値とコードの定数が一致する", not wrong, wrong)
    check("宣言した設定は全部コードに在る",
          all(hasattr(module, key) for key in manifest["settings"]))
    check("choice の既定値が候補に在る",
          manifest["settings"]["INJECT_DETAIL"]["default"]
          in manifest["settings"]["INJECT_DETAIL"]["values"])
    check("長さの表が choice の候補と揃っている",
          sorted(module.DETAIL_SENTENCES) == sorted(module.DETAIL_CHARS)
          == sorted(manifest["settings"]["INJECT_DETAIL"]["values"]),
          sorted(module.DETAIL_SENTENCES))

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all good")
    return 0


def read_log():
    path = os.path.join(OUT_DIR, "reputation.log")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


if __name__ == "__main__":
    sys.exit(main())
