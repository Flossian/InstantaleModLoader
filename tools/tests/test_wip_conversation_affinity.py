# -*- coding: utf-8 -*-
"""912_conversation_affinity をゲーム抜きで通す。

    python tools/tests/test_wip_conversation_affinity.py

偽の app / Character / 会話の要約の関数と、決まった答えを返す LLM を差し込み、次を確認する。

  部品     … 段は -3〜+3 に均す／上がる幅・下がる幅／会話で上げられる上限／1日1回／
             プレイヤーが話したかの見分け
  上がる   … 判定 +2 で好感度 +4。実行時の人物と素データの両方に書く。本文に一言出す
  下がる   … 判定 -3 で好感度 -12。1日1回の制限を受けない
  1日1回   … 同じ日に2回目は上がらない。日数が進めばまた上がる
  上限     … 会話では 60 を越えない。既に越えている相手は上げない
  聞かない … プレイヤーが話していない会話・MOD の NPC・LLM が答えない／読めない答えでは動かさない
  素通し   … 要約の関数には受け取った引数をそのまま渡し、戻り値もそのまま返す
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
STATE_DIR = os.path.join(OUT_DIR, "state_conversation_affinity")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import llm, ui               # noqa: E402

MOD_NAME = "conversation_affinity_mod"
RESOLVER = "scripts.llm.llm_manager:conversation_resolver"


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD = find_mod("_conversation_affinity")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character:
    def __init__(self, character_id, name, affinity):
        self.id = character_id
        self.name = name
        self.personality = "気難しい"
        self.relationship = {"player": {"affinity": affinity, "affinity_text": ["警戒心がある"]}}


class World:
    def __init__(self, characters, days):
        self.characters = characters
        self.days_elapsed = days


class Player:
    name = "検査の主人公"


class InstantaleApp:
    def __init__(self, days=10):
        self.world = World({"7": Character("7", "門番", 0), "8": Character("8", "宿の娘", 58),
                            "9": Character("9", "古老", 80)}, days)
        self.player = Player()
        self.save_data_dict = {"world_data": {"world_name": "好感度の検査世界", "days_elapsed": days},
                               "player_data": {"name": "検査の主人公"},
                               "npcs": {npc_id: {"name": c.name, "relationship": {
                                   "player": {"affinity": c.relationship["player"]["affinity"]}}}
                                   for npc_id, c in self.world.characters.items()}}
        self.world_dict = self.save_data_dict
        self.texts = []

    def add_text(self, text):
        self.texts.append(text)


APP = None


class FakeCtx:
    _mod = None

    def __init__(self):
        self.out_dir = OUT_DIR
        self.state_dir = STATE_DIR
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1] else path, exist_ok=True)
        return path

    def log(self, *a, **k):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


ui.Screen.when_idle = lambda self, app, then, **kw: then()
ANSWERS = []
ASKED = []
SAVED = (llm.ask, llm.create_structure)


def fake_ask(ctx, name, messages, **kw):
    ASKED.append(messages[0]["content"])
    return ANSWERS.pop(0) if ANSWERS else None


def fresh(**settings):
    global APP
    for name in (MOD_NAME,):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(MOD_NAME, MOD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    if hasattr(sys, module.STORE_ATTR):
        delattr(sys, module.STORE_ATTR)
    if os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    for name, value in settings.items():
        setattr(module, name, value)
    ctx = FakeCtx()
    module.apply(ctx)
    APP = InstantaleApp()
    del ASKED[:]
    del ANSWERS[:]
    return module, ctx, APP


SPOKE = [{"role": "user", "content": "<行動: 話しかける>"},
         {"role": "assistant", "content": "何の用だ。"},
         {"role": "user", "content": "門の見張り、いつもご苦労さま。差し入れを持ってきた"},
         {"role": "assistant", "content": "……気が利くじゃないか。"},
         {"role": "user", "content": "<行動: 会話を終了する>"}]
SILENT = [{"role": "user", "content": "<行動: 話しかける>"},
          {"role": "assistant", "content": "何の用だ。"},
          {"role": "user", "content": "<行動: 会話を終了する>"}]


def close(ctx, app, npc_id, messages=SPOKE, answer=None):
    """会話を閉じる（要約の関数を呼ぶ）。要約の戻り値を返す。"""
    if answer is not None:
        ANSWERS.append(answer)
    character = app.world.characters[npc_id]
    seen = {}

    def resolver(player, messages_, life_log, character_instance, emotion, worldview):
        seen["args"] = (player, messages_, life_log, character_instance, emotion, worldview)
        return {"summary": "要約"}

    result = ctx.hooks[RESOLVER](resolver, app.player, messages, {}, character, "警戒心がある", "世界")
    return result, seen


def aff(app, npc_id):
    return app.world.characters[npc_id].relationship["player"]["affinity"]


def saved_aff(app, npc_id):
    return app.save_data_dict["npcs"][npc_id]["relationship"]["player"]["affinity"]


# ---------------------------------------------------------------- 場面
def scene_pure():
    print("[部品]")
    module, _ctx, _app = fresh()
    check("段を均す", [module.clamp_step(v) for v in (5, -9, "+2", "1.0", None, True)]
          == [3, -3, 2, 1, None, None])
    check("無限大は読めない答え", module.clamp_step(float("inf")) is None)
    plan = lambda before, step, today=False: module.plan_change(
        before, step, gain_per_step=2, loss_per_step=4, ceiling=60, gained_today=today)
    check("上がる", plan(10, 2) == (14, "gain"))
    check("下がる", plan(10, -3) == (-2, "loss"))
    check("0 は動かない", plan(10, 0) == (10, "zero"))
    check("上限で止まる", plan(58, 3) == (60, "gain"))
    check("上限以上は上げない", plan(80, 1) == (80, "ceiling"))
    check("上限以上でも下がる", plan(80, -1) == (76, "loss"))
    check("今日はもう上げた", plan(10, 2, True) == (10, "daily"))
    check("下がるのは1日1回の制限を受けない", plan(10, -1, True) == (6, "loss"))
    check("下限で止まる", plan(-98, -1) == (-100, "loss"))
    check("下限より下の相手を下がる判定で上げない", plan(-150, -1) == (-150, "loss"),
          plan(-150, -1))
    check("話したかの見分け", module.player_spoke(SPOKE) and not module.player_spoke(SILENT))


def scene_gain():
    print("[上がる]")
    module, ctx, app = fresh()
    result, seen = close(ctx, app, "7", answer={"change": 2, "reason": "差し入れが嬉しかった"})
    check("好感度 +4（実行時）", aff(app, "7") == 4, aff(app, "7"))
    check("好感度 +4（素データ）", saved_aff(app, "7") == 4, saved_aff(app, "7"))
    check("本文に一言", any("心を開いた" in t for t in app.texts), app.texts)
    check("頼み文に会話と性格", ASKED and "差し入れを持ってきた" in ASKED[0] and "気難しい" in ASKED[0],
          ASKED[:1])
    check("頼み文に今の気持ち", ASKED and "警戒心がある" in ASKED[0])
    check("要約の戻り値はそのまま", result == {"summary": "要約"})
    check("要約の引数はそのまま", seen["args"][1] is SPOKE and seen["args"][4] == "警戒心がある")

    close(ctx, app, "7", answer={"change": 3, "reason": "また来た"})
    check("同じ日に2回目は上がらない", aff(app, "7") == 4, aff(app, "7"))
    app.world.days_elapsed += 30
    close(ctx, app, "7", answer={"change": 1, "reason": "顔なじみ"})
    check("日数が進めばまた上がる", aff(app, "7") == 6, aff(app, "7"))

    module, ctx, app = fresh(DAILY_GAIN_ONCE=False, SHOW_CHANGE=False)
    close(ctx, app, "7", answer={"change": 1, "reason": "a"})
    close(ctx, app, "7", answer={"change": 1, "reason": "b"})
    check("切れば同じ日でも上がる", aff(app, "7") == 4, aff(app, "7"))
    check("一言を切れば出さない", not app.texts, app.texts)
    check("例外が無い", not ctx.errors, ctx.errors)


def scene_loss_and_ceiling():
    print("[下がる・上限]")
    module, ctx, app = fresh()
    close(ctx, app, "7", answer={"change": 2, "reason": "a"})
    close(ctx, app, "7", answer={"change": -3, "reason": "侮辱された"})
    check("判定 -3 で -12（同じ日でも効く）", aff(app, "7") == -8, aff(app, "7"))
    check("下がった一言", any("機嫌を損ねた" in t for t in app.texts), app.texts)
    close(ctx, app, "8", answer={"change": 3, "reason": "a"})
    check("上限 60 で止まる", aff(app, "8") == 60, aff(app, "8"))
    close(ctx, app, "9", answer={"change": 3, "reason": "a"})
    check("上限より上の相手は上げない", aff(app, "9") == 80, aff(app, "9"))

    # 判定を待つ間にゲームが好感度を動かした（依頼のクリアの +20）。
    module, ctx, app = fresh()

    def ask_while_quest_clears(ctx_, name, messages, **kw):
        ASKED.append(messages[0]["content"])
        app.world.characters["7"].relationship["player"]["affinity"] += 20
        return {"change": -1, "reason": "a"}

    llm.ask = ask_while_quest_clears
    try:
        close(ctx, app, "7")
    finally:
        llm.ask = fake_ask
    check("判定を待つ間の変化を消さない（0 +20 -4）", aff(app, "7") == 16, aff(app, "7"))


def scene_skip():
    print("[聞かない]")
    module, ctx, app = fresh()
    close(ctx, app, "7", messages=SILENT, answer={"change": 3, "reason": "a"})
    check("話していない会話は聞かない", not ASKED and aff(app, "7") == 0)
    del ANSWERS[:]
    close(ctx, app, "7")
    check("LLM が答えなければ動かさない", aff(app, "7") == 0)
    close(ctx, app, "7", answer={"change": "たくさん", "reason": "a"})
    check("読めない答えでは動かさない", aff(app, "7") == 0)
    modnpc_like = Character("mod:331_facility_investment:keeper-0-2", "主人", 40)
    ANSWERS.append({"change": 3, "reason": "a"})
    before = len(ASKED)
    ctx.hooks[RESOLVER](lambda *a: {"summary": "x"}, app.player, SPOKE, {}, modnpc_like, "", "")
    check("MOD の NPC は対象にしない", len(ASKED) == before
          and modnpc_like.relationship["player"]["affinity"] == 40)
    check("例外が無い", not ctx.errors, ctx.errors)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    llm.ask = fake_ask
    llm.create_structure = lambda *a, **k: object()
    try:
        scene_pure()
        scene_gain()
        scene_loss_and_ceiling()
        scene_skip()
    finally:
        llm.ask, llm.create_structure = SAVED
    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
