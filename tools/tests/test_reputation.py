# -*- coding: utf-8 -*-
"""317_reputation をゲーム抜きで通す。

    python tools/tests/test_reputation.py

（開発中は `908_reputation` / `test_wip_reputation.py` だった。2026-08-24 に正式化。）

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
  長さ    … brief は1文、detailed は3文まで。字数でも頭打ちになる
  注入    … 会話5関数すべてで**複製の** profile に足す。ゲーム側の NPC は変わらない
  素通し   … 評判が無い／profile が文字列でない／複製できない／app が無い
  情景描写  … 既定 OFF では触らない。ON なら player_profile に足す
  控え    … `state/` に書く。土地は `RECORD_KEYS`、二つ名は `EPITHET_RECORD_KEYS`。
            印が変わらなければ編纂し直さない
  安全    … LLM が返らなくても前の評判・前の名が残る。本体は必ず1回だけ呼ばれる
  名乗り   … mod.json の既定値とコードの定数が一致する
"""
import importlib.util
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
#: 控えの置き場（本番は配布フォルダ直下の `state/`）。
STATE_DIR = os.path.join(OUT_DIR, "state")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                                   # noqa: E402
from instantale_modloader import ui as ui_module                    # noqa: E402

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
AREA_MOVE = "__main__:AreaMoveManager.execute"
MOVE_PHASE = "__main__:MovePhaseManager.move_phase"


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


class Character(object):
    """プレイヤーも NPC も同じ `Character`（GAME.md §2.20）。"""

    def __init__(self, name, area_history=None, current_area=None, profile=""):
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


def quest(area_id, title, status="completed"):
    return {"quest_title": title, "neighboring_settlement_id": area_id,
            "config": {"status": status, "level_of_detail": 1}}


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
    """`instantale_modloader.llm` の代わり。`ask` の返答を並べて渡す。"""

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

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

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
    """
    module = load_mod()
    for key, value in settings.items():
        setattr(module, key, value)
    module.CHECK_INTERVAL = 0.0          # 検査では間引かない
    module.llm = FakeLLM(replies)
    if hasattr(sys, module.STATE_STORE_ATTR):
        delattr(sys, module.STATE_STORE_ATTR)
    ui_module.find_app = lambda: app     # material も同じモジュールを見ている
    ctx = FakeCtx()
    module.apply(ctx)
    return module, ctx


def store_of(module):
    return getattr(sys, module.STATE_STORE_ATTR)


def drain(module, timeout=10.0):
    """編纂のワーカーが仕事を片付けるまで待つ。"""
    jobs = store_of(module)["jobs"]
    deadline = time.monotonic() + timeout
    while jobs.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return not jobs.unfinished_tasks


def trigger(module, ctx, app, target=ELAPSE):
    """素材の照合を起こして、編纂が終わるまで待つ。"""
    calls = []
    ctx.hooks[target](lambda *a, **k: calls.append(a), app, 1)
    drain(module)
    return calls


def call_conv(ctx, target, npc, use_kwargs=False, args_before=3):
    """会話関数を1回呼ぶ。本体が受け取った NPC と、呼ばれた回数を返す。"""
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
        got = seen["kwargs"].get("character_instance")
    else:
        result = ctx.hooks[target](orig, *head, npc, "worldview")
        got = seen["args"][3]
    return got, seen.get("times", 0), result


def cache_file(module, world="テスト世界"):
    from instantale_modloader.state import world_filename
    return os.path.join(STATE_DIR, module.STATE_DIRNAME, world_filename(world))


def read_cache(module, world="テスト世界"):
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
          stats == {"3": {"deeds": 3, "wanted": False},
                    "7": {"deeds": 2, "wanted": False}}, stats)
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
          mark == {"qualifying": ["3"], "wanted": [], "deeds": 4}, mark)
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
    real_random = module.random
    real_cap = module.EPITHET_CHARS

    class FakeRandom(object):
        @staticmethod
        def randint(low, high):
            drawn.append((low, high))
            return 7

    module.random = FakeRandom
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
        module.random = real_random
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

    # -- 引き直し（人物欄のボタンが書く頼みのファイル） --
    from instantale_modloader.state import world_filename
    reroll = os.path.join(STATE_DIR, module.STATE_DIRNAME,
                          world_filename("テスト世界", module.REROLL_SUFFIX))
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
    check("土地の鍵の並びが RECORD_KEYS のとおり",
          tuple(record) == module.RECORD_KEYS, tuple(record))
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
