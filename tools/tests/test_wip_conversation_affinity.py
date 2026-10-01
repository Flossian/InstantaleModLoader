# -*- coding: utf-8 -*-
"""912_conversation_affinity をゲーム抜きで通す。

    python tools/tests/test_wip_conversation_affinity.py

偽の app / Character / 会話の要約の関数 / 送信の境目（`llm_manager:send_request`）と、
pydantic の代わりの型（`create_model` で子の型を作れる）を差し込み、次を確認する。

  部品     … 段は -3〜+3 に均す／上がる幅・下がる幅／会話で上げられる上限／1日1回／
             プレイヤーが話したかの見分け／system への書き足し／答えを元の型へ戻す
  相乗り   … 要約の頼みの型に判定の2項目を足し、先頭の system に決まりを書き足す。
             ゲームの持つ本文は書き換えない。ゲームへは元の型・要約だけで返す
  上がる   … 判定 +2 で好感度 +4。実行時の人物と素データの両方に書く。本文には何も出さない
  下がる   … 判定 -3 で好感度 -12。1日1回の制限を受けない
  1日1回   … 同じ日に2回目は上がらない。日数が進めばまた上がる
  上限     … 会話では 60 を越えない。既に越えている相手は上げない。
             ローダの窓口（`talk_affinity`）で他の MOD が狭めた相手はその上限で止まる
  判定する … プレイヤーが何か言った・開け閉め以外の行動（贈り物・売り買い）をした会話
  足さない … プレイヤーが話していない会話・MOD の NPC・system の無い頼み・要約以外の送信・要約の外の送信
  失敗     … 足した形で失敗したら元の頼みで送り直し、要約は返す。読めない答えでは動かさない
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
STATE_DIR = os.path.join(OUT_DIR, "state_conversation_affinity")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import llm, talk_affinity    # noqa: E402

MOD_NAME = "conversation_affinity_mod"
RESOLVER = "scripts.llm.llm_manager:conversation_resolver"
SEND = "scripts.llm.llm_manager:send_request"


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


# ---------------------------------------------------------------- pydantic の代わり
class FakeModel:
    """`create_model` の作る型の代わり。項目が足りなければ作れない（検証に落ちる）。"""
    fields = ()

    def __init__(self, **data):
        missing = [name for name in self.fields if name not in data]
        if missing:
            raise ValueError("missing {}".format(missing))
        for name in self.fields:
            setattr(self, name, data[name])

    @classmethod
    def model_validate(cls, data):
        return cls(**data)

    def model_dump(self):
        return {name: getattr(self, name) for name in self.fields}


def create_model(name, __base__=FakeModel, **fields):
    return type(name, (__base__,), {"fields": tuple(__base__.fields) + tuple(fields)})


Result = create_model("Result", summary=(str, ...))
MANAGER = types.ModuleType("scripts.llm.llm_manager")
MANAGER.create_model = create_model


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

    def resolve(self, target):
        return None, None, object()         # 別名はもう生えている

    def superseded(self):
        return False

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


#: 送信の境目に来た `(manager_name, message, structure)`。
SENT = []
#: 判定の答え。`{"change", "reason"}`・None（答えない＝足した型に落ちる）・例外。
ANSWERS = []
SYSTEM = {"role": "system", "content": "あなたはRPGのGMです。会話を要約して下さい。\n【門番の情報】性格: 気難しい"}


def game_send(manager_name, message, structure, **kw):
    """ゲームの send_request の代わり。型に判定の項目があれば答えを入れて返す。"""
    SENT.append((manager_name, message, structure))
    data = {"summary": "要約"}
    if "affinity_change" in structure.fields:
        answer = ANSWERS.pop(0) if ANSWERS else None
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            data.update(affinity_change=answer["change"], affinity_reason=answer["reason"])
    return structure(**data)


def fresh(**settings):
    global APP
    talk_affinity.reset()
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
    del SENT[:]
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


def close(ctx, app, npc_id, messages=SPOKE, answer=None, character=None, system=True,
          send=None):
    """会話を閉じる（要約の関数が送信の境目を通る）。`(要約の戻り値, 見たもの)` を返す。"""
    if answer is not None:
        ANSWERS.append(answer)
    character = character or app.world.characters[npc_id]
    seen = {}
    prompt = ([SYSTEM] if system else []) + list(messages)
    seen["prompt_before"] = [dict(turn) for turn in prompt]

    def resolver(player, messages_, life_log, character_instance, emotion, worldview):
        seen["args"] = (player, messages_, life_log, character_instance, emotion, worldview)
        result = ctx.hooks[SEND](send or game_send, "conversation_resolver", prompt, Result)
        seen["prompt_after"] = prompt
        return result

    result = ctx.hooks[RESOLVER](resolver, app.player, messages, {}, character, "警戒心がある", "世界")
    return result, seen


def aff(app, npc_id):
    return app.world.characters[npc_id].relationship["player"]["affinity"]


def saved_aff(app, npc_id):
    return app.save_data_dict["npcs"][npc_id]["relationship"]["player"]["affinity"]


def extended_sent():
    return [structure for _name, _message, structure in SENT if "affinity_change" in structure.fields]


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
    check("話したかの見分け", module.player_engaged(SPOKE) and not module.player_engaged(SILENT))
    engaged = lambda *texts: module.player_engaged([{"role": "user", "content": t} for t in texts])
    check("贈り物の行動は判定する", engaged("<行動: 話しかける>", "<行動: 花束を渡した。>"))
    check("売り買いの行動は判定する", engaged("<行動: 錆びたショートソードを購入した。>"))
    check("全角の印でも読む", engaged("＜行動：花束を渡した＞")
          and not engaged("＜行動：話しかける＞", "＜行動：会話を終了する＞"))
    check("取引を終えただけは判定しない",
          not engaged("<行動: 話しかける>", "<行動: 取引を終了する>", "<行動: 会話を終了する>"))
    check("行動の中身を読む", module.action_of("<行動: 会話を終了する>") == "会話を終了する"
          and module.action_of("こんにちは") is None)

    original = [{"role": "user", "content": "a"}, dict(SYSTEM), {"role": "system", "content": "b"}]
    added = module.with_instruction(original, "足す")
    check("先頭の system に書き足す", added[1]["content"].endswith("\n\n足す")
          and added[2]["content"] == "b" and len(added) == 3, added)
    check("元の本文は書き換えない", original[1]["content"] == SYSTEM["content"])
    check("system が無ければ足さない", module.with_instruction([{"role": "user", "content": "a"}], "x")
          is None)

    child = create_model("Result", __base__=Result, affinity_change=(int, ...),
                         affinity_reason=(str, ...))
    raw = child(summary="要約", affinity_change=1, affinity_reason="r")
    back = module.restore(raw, Result)
    check("型で返れば元の型に戻す", type(back) is Result and back.model_dump() == {"summary": "要約"},
          type(back))
    check("辞書で返れば項目を抜く", module.restore(
        {"summary": "s", "affinity_change": 1, "affinity_reason": "r"}, Result) == {"summary": "s"})
    check("JSON で返れば項目を抜く", json.loads(module.restore(
        '{"summary": "s", "affinity_change": 1, "affinity_reason": "r"}', Result)) == {"summary": "s"})


def scene_ride():
    print("[相乗り]")
    module, ctx, app = fresh()
    result, seen = close(ctx, app, "7", answer={"change": 2, "reason": "差し入れが嬉しかった"})
    check("LLM は要約の1回だけ", len(SENT) == 1, len(SENT))
    name, message, structure = SENT[0]
    check("足した型で送る", "affinity_change" in structure.fields and issubclass(structure, Result)
          and structure.fields[0] == "summary", getattr(structure, "fields", None))
    check("system に判定の決まりを書き足す", "affinity_change" in message[0]["content"]
          and "門番" in message[0]["content"] and "検査の主人公" in message[0]["content"]
          and message[0]["content"].startswith(SYSTEM["content"]), message[0]["content"][:80])
    check("会話の本文はそのまま", message[1:] == SPOKE)
    check("ゲームの持つ本文は書き換えない", seen["prompt_after"] == seen["prompt_before"])
    check("ゲームへは元の型で返す", type(result) is Result, type(result))
    check("要約だけを返す", result.model_dump() == {"summary": "要約"}, result.model_dump())
    check("要約の引数はそのまま", seen["args"][1] is SPOKE and seen["args"][4] == "警戒心がある")

    close(ctx, app, "7", answer={"change": 1, "reason": "a"})
    check("足した型は使い回す", extended_sent()[0] is extended_sent()[1])

    # 要約以外の送信・要約の外の送信には触らない。
    del SENT[:]
    ctx.hooks[SEND](game_send, "conversation_facilitator", [dict(SYSTEM)], Result)
    check("要約の外の送信は素通し", SENT[-1][2] is Result
          and SENT[-1][1][0]["content"] == SYSTEM["content"])

    def resolver_sends_twice(*args):
        ctx.hooks[SEND](game_send, "something_else", [dict(SYSTEM)], Result)
        return ctx.hooks[SEND](game_send, "conversation_resolver", [dict(SYSTEM)] + SPOKE, Result)

    del SENT[:]
    ANSWERS.append({"change": 0, "reason": "a"})
    ctx.hooks[RESOLVER](resolver_sends_twice, app.player, SPOKE, {},
                        app.world.characters["8"], "", "")
    check("要約の中でも別の manager は素通し", SENT[0][2] is Result and SENT[1][2] is not Result,
          [s[0] for s in SENT])
    check("例外が無い", not ctx.errors, ctx.errors)


def scene_gain():
    print("[上がる]")
    module, ctx, app = fresh()
    close(ctx, app, "7", answer={"change": 2, "reason": "差し入れが嬉しかった"})
    check("好感度 +4（実行時）", aff(app, "7") == 4, aff(app, "7"))
    check("好感度 +4（素データ）", saved_aff(app, "7") == 4, saved_aff(app, "7"))
    check("本文には何も出さない", not app.texts, app.texts)

    close(ctx, app, "7", answer={"change": 3, "reason": "また来た"})
    check("同じ日に2回目は上がらない", aff(app, "7") == 4, aff(app, "7"))
    app.world.days_elapsed += 30
    close(ctx, app, "7", answer={"change": 1, "reason": "顔なじみ"})
    check("日数が進めばまた上がる", aff(app, "7") == 6, aff(app, "7"))

    module, ctx, app = fresh(DAILY_GAIN_ONCE=False)
    close(ctx, app, "7", answer={"change": 1, "reason": "a"})
    close(ctx, app, "7", answer={"change": 1, "reason": "b"})
    check("切れば同じ日でも上がる", aff(app, "7") == 4, aff(app, "7"))
    check("例外が無い", not ctx.errors, ctx.errors)


def scene_loss_and_ceiling():
    print("[下がる・上限]")
    module, ctx, app = fresh()
    close(ctx, app, "7", answer={"change": 2, "reason": "a"})
    close(ctx, app, "7", answer={"change": -3, "reason": "侮辱された"})
    check("判定 -3 で -12（同じ日でも効く）", aff(app, "7") == -8, aff(app, "7"))
    close(ctx, app, "8", answer={"change": 3, "reason": "a"})
    check("上限 60 で止まる", aff(app, "8") == 60, aff(app, "8"))
    close(ctx, app, "9", answer={"change": 3, "reason": "a"})
    check("上限より上の相手は上げない", aff(app, "9") == 80, aff(app, "9"))

    # 他の MOD が窓口で上限を狭めた相手。
    module, ctx, app = fresh()

    class Owner:
        def superseded(self):
            return False

    talk_affinity.limit("another_mod", Owner(), lambda app_, npc_id: 4 if npc_id == "7" else None)
    close(ctx, app, "7", answer={"change": 3, "reason": "a"})
    check("窓口で狭めた上限で止まる", aff(app, "7") == 4, aff(app, "7"))
    close(ctx, app, "8", answer={"change": 1, "reason": "a"})
    check("狭めていない相手は元の上限", aff(app, "8") == 60, aff(app, "8"))
    close(ctx, app, "7", answer={"change": -1, "reason": "a"})
    check("狭めた相手も下がる方は効く", aff(app, "7") == 0, aff(app, "7"))
    log = io.open(os.path.join(OUT_DIR, module.LOG_BASENAME), encoding="utf-8").read()
    check("狭めた持ち主をログに残す", "ceiling 4 by another_mod" in log)

    # 要約を待つ間にゲームが好感度を動かした（依頼のクリアの +20）。
    module, ctx, app = fresh()

    def send_while_quest_clears(manager_name, message, structure, **kw):
        app.world.characters["7"].relationship["player"]["affinity"] += 20
        return game_send(manager_name, message, structure, **kw)

    close(ctx, app, "7", answer={"change": -1, "reason": "a"}, send=send_while_quest_clears)
    check("要約を待つ間の変化を消さない（0 +20 -4）", aff(app, "7") == 16, aff(app, "7"))


def scene_skip():
    print("[足さない・失敗]")
    module, ctx, app = fresh()
    result, _seen = close(ctx, app, "7", messages=SILENT, answer={"change": 3, "reason": "a"})
    check("開け閉めだけの会話には足さない", not extended_sent() and aff(app, "7") == 0)
    check("そのときも要約は返る", type(result) is Result)
    del ANSWERS[:]

    modnpc_like = Character("mod:331_facility_investment:keeper-0-2", "主人", 40)
    close(ctx, app, "7", answer={"change": 3, "reason": "a"}, character=modnpc_like)
    check("MOD の NPC には足さない", not extended_sent()
          and modnpc_like.relationship["player"]["affinity"] == 40)
    del ANSWERS[:]

    close(ctx, app, "7", answer={"change": 3, "reason": "a"}, system=False)
    check("system の無い頼みには足さない", not extended_sent() and aff(app, "7") == 0)
    del ANSWERS[:]

    del SENT[:]
    result, _seen = close(ctx, app, "7")       # 答えない＝足した型の検証に落ちる
    check("足した形で失敗したら元の頼みで送り直す", len(SENT) == 2 and SENT[1][2] is Result,
          [s[2].fields for s in SENT])
    check("送り直した要約を返す", type(result) is Result and result.summary == "要約")
    check("答えが無ければ動かさない", aff(app, "7") == 0)

    del SENT[:]
    result, _seen = close(ctx, app, "7", answer=TimeoutError("slow"))
    check("送信の例外でも要約は返る", type(result) is Result and len(SENT) == 2)

    close(ctx, app, "7", answer={"change": "たくさん", "reason": "a"})
    check("読めない答えでは動かさない", aff(app, "7") == 0)
    check("例外が無い", not ctx.errors, ctx.errors)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    saved_manager = sys.modules.get(MANAGER.__name__)
    sys.modules[MANAGER.__name__] = MANAGER
    try:
        scene_pure()
        scene_ride()
        scene_gain()
        scene_loss_and_ceiling()
        scene_skip()
    finally:
        if saved_manager is None:
            sys.modules.pop(MANAGER.__name__, None)
        else:
            sys.modules[MANAGER.__name__] = saved_manager
    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
