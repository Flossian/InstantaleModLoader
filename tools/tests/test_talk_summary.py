# -*- coding: utf-8 -*-
"""ローダの窓口 `talk_summary`（会話の要約への相乗り）をゲーム抜きで通す。

    python tools/tests/test_talk_summary.py

偽の要約の関数と送信の境目（`llm_manager:send_request`）、pydantic の代わりの型を差し込み、次を確認する。

  1本     … 乗った MOD の項目を足した子の型で送り、先頭の system に決まりを書き足す。ゲームへは元の型で返す
  2本     … 2本乗っても送信は1回。型は両方の項目を持ち、決まりは乗った順に並ぶ。答えは持ち主ごとに分けて渡す
  乗らない … begin が None の MOD は乗せない。誰も乗らなければ型も本文も触らない。要約以外の送信は素通し
  重なり   … 先に乗った MOD と項目名が重なった MOD は、その回は乗せない（errors に残す）
  失敗     … 足した形が失敗したら元の頼みで1回だけ送り直し、settle には None と status "retried"
  降りる   … begin / settle が投げた MOD だけ降りる。用済みの ctx の登録は数えない。withdraw で下げる
  包み     … 同じ世代で何度 install しても1組
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import talk_summary, ui  # noqa: E402

RESOLVER = talk_summary.RESOLVER_TARGET
SEND = talk_summary.SEND_TARGET
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- pydantic の代わり
class FakeModel:
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


CREATED = []


def create_model(name, __base__=FakeModel, **fields):
    made = type(name, (__base__,), {"fields": tuple(__base__.fields) + tuple(fields)})
    CREATED.append(made)
    return made


Result = create_model("Result", summary=(str, ...))
MANAGER = types.ModuleType("scripts.llm.llm_manager")
MANAGER.create_model = create_model


# ---------------------------------------------------------------- 偽ゲーム
class Character:
    def __init__(self, character_id, name):
        self.id = character_id
        self.name = name


class Player:
    name = "検査の主人公"


class App:
    player = Player()


APP = App()


class FakeCtx:
    _mod = None

    def __init__(self, generation="g1"):
        self.generation = generation
        self.hooks = {}
        self.errors = []
        self.old = False

    def log(self, *a, **k):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def resolve(self, target):
        return None, None, object()

    def superseded(self):
        return self.old

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


SENT = []
#: 送信の境目が答える足した項目の値。例外なら投げる。
REPLY = {}
SYSTEM = {"role": "system", "content": "会話を要約して下さい。"}
SPOKE = [{"role": "user", "content": "こんにちは"}, {"role": "assistant", "content": "やあ"}]


def game_send(manager_name, message, structure, **kw):
    SENT.append((manager_name, message, structure))
    if isinstance(REPLY.get("raise"), Exception) and structure is not Result:
        raise REPLY["raise"]
    data = {"summary": "要約"}
    for name in structure.fields:
        if name != "summary":
            data[name] = REPLY.get(name)
    return structure(**data)


def close(ctx, character=None, send=None, system=True):
    prompt = ([dict(SYSTEM)] if system else []) + list(SPOKE)

    def resolver(player, messages_, life_log, character_instance, emotion, worldview):
        return ctx.hooks[SEND](send or game_send, "conversation_resolver", prompt, Result)

    return ctx.hooks[RESOLVER](resolver, APP.player, SPOKE, {}, character or Character("7", "門番"),
                               "", "")


class Rider:
    """乗る MOD の代わり。begin の答えと、settle に来たものを持つ。"""

    def __init__(self, owner, fields, text="決まり", ctx=None):
        self.owner, self.fields, self.text = owner, fields, text
        self.ctx = ctx or FakeCtx()
        self.seen = []
        self.boom_begin = self.boom_settle = False

    def begin(self, talk):
        if self.boom_begin:
            raise RuntimeError("begin")
        return self.text

    def settle(self, talk, answer):
        if self.boom_settle:
            raise RuntimeError("settle")
        self.seen.append((talk["status"], answer, talk["npc_id"], talk["name"], talk["player"]))

    def ride(self):
        talk_summary.ride(self.owner, self.ctx, self.begin, self.settle, self.fields)
        return self


def fresh():
    talk_summary.reset()
    del SENT[:]
    REPLY.clear()
    ctx = FakeCtx()
    talk_summary.install(ctx)
    return ctx


def main():
    saved_manager = sys.modules.get(MANAGER.__name__)
    sys.modules[MANAGER.__name__] = MANAGER
    saved_find = ui.find_app
    ui.find_app = lambda *a, **k: APP
    try:
        print("[1本]")
        ctx = fresh()
        one = Rider("a", {"a_change": int}, "【決まりA】").ride()
        REPLY.update(a_change=2)
        result = close(ctx)
        check("送信は1回", len(SENT) == 1, len(SENT))
        _name, message, structure = SENT[0]
        check("足した型で送る", structure.fields == ("summary", "a_change")
              and issubclass(structure, Result), structure.fields)
        check("system に書き足す", message[0]["content"] == SYSTEM["content"] + "\n\n【決まりA】",
              message[0]["content"])
        check("会話の本文はそのまま", message[1:] == SPOKE)
        check("ゲームへは元の型で返す", type(result) is Result and result.model_dump() == {"summary": "要約"})
        check("settle に自分の項目", one.seen == [("answered", {"a_change": 2}, "7", "門番", "検査の主人公")],
              one.seen)
        close(ctx)
        check("同じ顔ぶれの型は使い回す", SENT[0][2] is SENT[1][2])

        print("[2本]")
        ctx = fresh()
        a = Rider("a", {"a_change": int}, "【決まりA】").ride()
        b = Rider("b", {"b_step": int, "b_reason": str}, "【決まりB】").ride()
        REPLY.update(a_change=1, b_step=-2, b_reason="理由")
        close(ctx)
        check("2本でも送信は1回", len(SENT) == 1, len(SENT))
        check("型は両方の項目", SENT[0][2].fields == ("summary", "a_change", "b_step", "b_reason"),
              SENT[0][2].fields)
        check("決まりは乗った順", SENT[0][1][0]["content"].endswith("【決まりA】\n\n【決まりB】"))
        check("答えは持ち主ごと", a.seen[0][1] == {"a_change": 1}
              and b.seen[0][1] == {"b_step": -2, "b_reason": "理由"}, (a.seen, b.seen))

        print("[乗らない]")
        ctx = fresh()
        quiet = Rider("q", {"q": int}, None).ride()
        result = close(ctx)
        check("誰も乗らなければ元の型", SENT[0][2] is Result and SENT[0][1][0] == SYSTEM)
        check("乗らなかった MOD に settle は来ない", quiet.seen == [])
        a = Rider("a", {"a_change": int}).ride()
        del SENT[:]
        ctx.hooks[SEND](game_send, "conversation_facilitator", [dict(SYSTEM)], Result)
        check("要約の外の送信は素通し", SENT[0][2] is Result)
        del SENT[:]
        close(ctx, system=False)
        check("system が無ければ足さない", SENT[0][2] is Result and a.seen[-1][:2] == ("no_system", None),
              a.seen)

        print("[重なり]")
        ctx = fresh()
        first = Rider("first", {"x": int}).ride()
        second = Rider("second", {"x": int, "y": str}).ride()
        REPLY.update(x=3)
        close(ctx)
        check("重なった MOD は乗せない", SENT[0][2].fields == ("summary", "x") and second.seen == []
              and first.seen[0][1] == {"x": 3}, (SENT[0][2].fields, second.seen))
        check("重なりを errors に残す", any("second" in e for e in talk_summary._store()["errors"]))

        print("[失敗]")
        ctx = fresh()
        a = Rider("a", {"a_change": int}).ride()
        b = Rider("b", {"b_step": int}).ride()
        REPLY["raise"] = TimeoutError("slow")
        result = close(ctx)
        check("送り直しは1回だけ", len(SENT) == 2 and SENT[1][2] is Result, [s[2].fields for s in SENT])
        check("送り直した要約を返す", type(result) is Result and result.summary == "要約")
        check("settle には None と retried", a.seen[0][:2] == ("retried", None)
              and b.seen[0][:2] == ("retried", None), (a.seen, b.seen))

        print("[降りる]")
        ctx = fresh()
        a = Rider("a", {"a_change": int}).ride()
        b = Rider("b", {"b_step": int}).ride()
        a.boom_begin = True
        REPLY.update(b_step=1)
        close(ctx)
        check("begin が投げた MOD だけ降りる", SENT[0][2].fields == ("summary", "b_step")
              and b.seen[0][1] == {"b_step": 1} and a.ctx.errors, a.ctx.errors)
        a.boom_begin, a.boom_settle = False, True
        close(ctx)
        check("settle が投げても他の MOD に届く", b.seen[-1][1] == {"b_step": 1} and len(a.ctx.errors) == 2)
        b.ctx.old = True
        del SENT[:]
        close(ctx)
        check("用済みの ctx の登録は数えない", SENT[0][2].fields == ("summary", "a_change"),
              SENT[0][2].fields)
        talk_summary.withdraw("a")
        check("withdraw で下げる", talk_summary.owners() == [])

        print("[包み]")
        talk_summary.reset()
        first_ctx = FakeCtx("g9")
        talk_summary.install(first_ctx)
        later_ctx = FakeCtx("g9")
        talk_summary.install(later_ctx)
        check("同じ世代では1組", RESOLVER in first_ctx.hooks and not later_ctx.hooks)
        newer_ctx = FakeCtx("g10")
        talk_summary.install(newer_ctx)
        check("新しい世代では包み直す", RESOLVER in newer_ctx.hooks and SEND in newer_ctx.hooks)
        check("例外が無い", not ctx.errors and not first_ctx.errors, ctx.errors + first_ctx.errors)
    finally:
        ui.find_app = saved_find
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
