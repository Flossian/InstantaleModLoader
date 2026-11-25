# -*- coding: utf-8 -*-
"""ローダの送り口の窓口 `llm.on_request` と部品 `llm.edit_turn` / `llm.extend_structure`、
選択肢の窓口の一覧の種類 `choices` の `view.listing` をゲーム抜きで通す。

    python tools/tests/test_window_c9.py

  edit_turn        … 先頭の system / 最後の user の1件だけを写して差し替える。edit が None なら次の候補。
                     印がもう入っていれば何もしない。元の list と dict は書き換えない
  extend_structure … 子の型に欄を足す。既にある欄は replace のときだけ上書き。同じ組は使い回し、控えは数で止める。
                     pydantic が無ければ llm_manager の create_model。型でない・作れないときは None
  on_request 順    … fn は登録の新しい順（前の書き換えを次が見る）、after は古い順。memo で前後を受け渡す
  on_request 選ぶ  … managers（名前・集まり・関数）と targets で当てる頼みを選ぶ。当たらなければ引数を読まずに素通し
  on_request 形    … 位置で来ても名前で来ても、来た形のまま差し替える。返却型も差し替える
  on_request 守り  … fn / after の例外・違う型の値は WARN を出して戻し、残りと orig を止めない。ns の頼みに型は入れない。
                     用済みの ctx の登録は呼ばない。同じ持ち主は差し替える。off_request で下げる
  on_request 包み  … 適用の間は包まず、install_requests で全部の送り口に立てる。1つの世代に1回。
                     自分で包む MOD より外側。送り口の中からの入れ子の送りは素通し
  listing          … 一覧を出す関数の orig の前に印を立て（組み直しは orig の中）、別の画面の spec で下ろす。
                     view.listing と choices.listing() が同じ答え。包みは1つの世代に1回
"""
import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import choices, gates, llm  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("ok  " if ok else "FAIL", name, "  " + str(detail) if not ok and detail else ""))
    if not ok:
        failures.append(name)


# ================================================================ edit_turn
print("[edit_turn]")
SYSTEM = {"role": "system", "content": "決まり。\n- vfx: 演出\n- dmg: 数  "}
USER1 = {"role": "user", "content": "一つ目"}
USER2 = {"role": "user", "content": "二つ目"}
MESSAGE = [SYSTEM, USER1, {"role": "assistant", "content": "返事"}, USER2]
SNAPSHOT = [dict(turn) for turn in MESSAGE]

got = llm.edit_turn(MESSAGE, lambda text: text.rstrip() + "\n\n足す決まり")
check("先頭の system の末尾に足す", got is not None and got[0]["content"] == "決まり。\n- vfx: 演出\n- dmg: 数\n\n足す決まり", got)
check("ほかの発言は同じ物のまま", got is not None and all(got[i] is MESSAGE[i] for i in (1, 2, 3)))
check("元の list と dict は書き換えない", MESSAGE == SNAPSHOT and got is not MESSAGE and MESSAGE[0] is SYSTEM)

got = llm.edit_turn(MESSAGE, lambda text: text + "（印）", role="user", last=True)
check("last なら最後の user", got is not None and got[3]["content"] == "二つ目（印）" and got[1] is USER1, got)
check("unless の印が入っていれば何もしない",
      llm.edit_turn(got, lambda text: text + "（印）", role="user", last=True, unless="（印）") is None)

got = llm.edit_turn(MESSAGE, lambda text: text + "!" if text.startswith("二") else None, role="user")
check("edit が None の発言は飛ばして次の候補", got is not None and got[3]["content"] == "二つ目!" and got[1] is USER1, got)
check("role=None なら role を問わない",
      llm.edit_turn(MESSAGE, lambda text: "替え" if text == "返事" else None, role=None)[2]["content"] == "替え")
check("対象が無ければ None", llm.edit_turn([USER1], lambda text: text + "x") is None)
check("全部 None なら None", llm.edit_turn(MESSAGE, lambda text: None, role=None) is None)
check("元と同じなら None", llm.edit_turn(MESSAGE, lambda text: text) is None)
check("list でなければ None", llm.edit_turn("文", lambda text: text + "x") is None and llm.edit_turn(None, str) is None)
check("本文が文字列でない発言は飛ばす",
      llm.edit_turn([{"role": "system", "content": [1]}, dict(SYSTEM)], lambda text: text + "x")[1]["content"].endswith("x"))
try:
    llm.edit_turn(MESSAGE, lambda text: 3)
    check("str でも None でもない戻り値は TypeError", False)
except TypeError:
    check("str でも None でもない戻り値は TypeError", True)


# ================================================================ extend_structure
print("[extend_structure]")
MADE = []


def fake_create_model(name, __base__=None, **fields):
    made = type(name, (__base__,), {"model_fields": dict(__base__.model_fields, **fields)})
    MADE.append((name, fields))
    return made


class Base(object):
    model_fields = {"summary": "str", "vfx": "Literal[slash]"}


fake_pydantic = types.ModuleType("pydantic")
fake_pydantic.create_model = fake_create_model
saved_pydantic = sys.modules.get("pydantic")
sys.modules["pydantic"] = fake_pydantic
try:
    child = llm.extend_structure(Base, {"expression": (str, "Field(説明)")})
    check("子の型に欄を足す", child is not None and issubclass(child, Base)
          and child.model_fields["expression"] == (str, "Field(説明)") and child.__name__ == "Base", child)
    check("同じ組は使い回す", llm.extend_structure(Base, {"expression": (str, "Field(説明)")}) is child and len(MADE) == 1)
    other = llm.extend_structure(Base, {"expression": (str, "Field(別の説明)")})
    check("説明が変われば作り直す", other is not None and other is not child and len(MADE) == 2)
    check("型だけなら必須（...）", llm.extend_structure(Base, {"mood": int}).model_fields["mood"] == (int, ...))
    check("既にある欄は replace でなければ None", llm.extend_structure(Base, {"vfx": (str, None)}) is None)
    wide = llm.extend_structure(Base, {"vfx": ("Literal[slash, burn]", "Field")}, replace=True)
    check("replace なら上書き", wide is not None and wide.model_fields["vfx"] == ("Literal[slash, burn]", "Field"))
    check("name で子の型の名前", llm.extend_structure(Base, {"x": int}, name="Wide").__name__ == "Wide")
    check("型でなければ None", llm.extend_structure(Base(), {"x": int}) is None
          and llm.extend_structure(dict, {"x": int}) is None and llm.extend_structure(Base, {}) is None)
    # 控えは数で止める（作り直される型が溜まらない）
    before = len(MADE)
    for n in range(llm.EXTENDED_LIMIT + 5):
        llm.extend_structure(type("Turn{}".format(n), (Base,), {}), {"x": int})
    check("控えは EXTENDED_LIMIT 組まで", len(llm._EXTENDED) == llm.EXTENDED_LIMIT and len(MADE) == before + llm.EXTENDED_LIMIT + 5,
          len(llm._EXTENDED))

    def broken(name, __base__=None, **fields):
        raise ValueError("作れない")
    fake_pydantic.create_model = broken
    check("作れなければ None", llm.extend_structure(Base, {"broken": int}) is None)
    # pydantic が無ければ llm_manager の create_model
    del sys.modules["pydantic"]
    manager = types.ModuleType(llm.MANAGER_MODULE)
    manager.create_model = fake_create_model
    saved_manager = sys.modules.get(llm.MANAGER_MODULE)
    sys.modules[llm.MANAGER_MODULE] = manager
    try:
        check("pydantic が無ければ llm_manager から", llm.model_factory() is fake_create_model
              and llm.extend_structure(Base, {"from_manager": int}) is not None)
    finally:
        if saved_manager is None:
            sys.modules.pop(llm.MANAGER_MODULE, None)
        else:
            sys.modules[llm.MANAGER_MODULE] = saved_manager
    check("どちらも無ければ None", llm.model_factory() is None and llm.extend_structure(Base, {"none": int}) is None)
finally:
    if saved_pydantic is None:
        sys.modules.pop("pydantic", None)
    else:
        sys.modules["pydantic"] = saved_pydantic


# ================================================================ on_request
class Ctx(object):
    """後から当てた包みが外側になる（本物の wrap と同じ）。送り口は self を取らない。"""
    seq = 0

    def __init__(self):
        Ctx.seq += 1
        self.generation = "c9-{}".format(Ctx.seq)
        self.hooks = {}
        self.dead = False
        self._mod = None

    def superseded(self):
        return self.dead

    def resolve(self, target):
        return None, None, object()

    def log(self, *a, **k):
        pass

    def log_exc(self, *a, **k):
        pass

    def wrap(self, target, **kw):
        def decorator(fn):
            previous = self.hooks.get(target)
            if previous is None:
                self.hooks[target] = fn
            else:
                self.hooks[target] = lambda orig, *a, _p=previous, _f=fn, **k: _f(
                    lambda *a2, **k2: _p(orig, *a2, **k2), *a, **k)
            return fn
        return decorator


SEND = "scripts.llm.llm_manager:send_request"
SEND_NS = "scripts.llm.llm_manager:send_request_with_no_structure"
BATTLE = "scripts.llm.llm_manager_battle:send_request"
SENT = []


def game_send(manager_name, message, structure=None, **kw):
    SENT.append((manager_name, message, structure, kw))
    return {"answer": manager_name}


def game_send_ns(manager_name, message, **kw):
    SENT.append((manager_name, message, None, kw))
    return "text"


class Struct(object):
    pass


class Wider(Struct):
    pass


print("[on_request 包み]")
llm.reset_requests()
gates.defer_outermost(True)
ctx = Ctx()
mod_seen = []


def mod_own(orig, *args, **kwargs):
    """自分で送り口を包む MOD（`336_` の盗みの判定の形）。窓口より内側に居る。"""
    mod_seen.append(args[1] if len(args) > 1 else kwargs.get("message"))
    return orig(*args, **kwargs)


ctx.wrap(SEND)(mod_own)
WARNS = []
llm.on_request("300_old", ctx, lambda req: llm.edit_turn(req.message, lambda t: t + "+old"), write=WARNS.append)
check("適用の間は包まない", ctx.hooks.get(SEND) is mod_own)
gates.defer_outermost(False)
check("install_requests で立てる", llm.install_requests(ctx) is True
      and all(target in ctx.hooks for target, _ns in llm.MANAGER_SEND_TARGETS))
check("1つの世代に1回", llm.install_requests(ctx) is False)
check("gates.reset_all の対象", llm.REQUESTS_ATTR in gates._INSTALL_ATTRS)
SENT.clear()
ctx.hooks[SEND](game_send, "talk", [{"role": "system", "content": "S"}], Struct)
check("自分で包む MOD より外側（MOD は書き換えた後の messages を見る）",
      mod_seen and mod_seen[-1][0]["content"] == "S+old" and SENT[-1][1][0]["content"] == "S+old", (mod_seen, SENT))

print("[on_request 順]")
order = []


def fn_new(req):
    order.append(("new", req.message[0]["content"]))
    req.memo["new"] = "控え"
    return llm.edit_turn(req.message, lambda t: t + "+new")


def after_old(req):
    order.append(("after-old", req.result))


def after_new(req):
    order.append(("after-new", req.memo.get("new")))


llm.on_request("300_old", ctx, lambda req: (order.append(("old", req.message[0]["content"])),
                                            llm.edit_turn(req.message, lambda t: t + "+old"))[1], after=after_old)
llm.on_request("301_new", ctx, fn_new, after=after_new)
check("持ち主は登録の古い順", llm.request_owners() == ["300_old", "301_new"], llm.request_owners())
SENT.clear()
result = ctx.hooks[SEND](game_send, "talk", [{"role": "system", "content": "S"}], Struct)
check("fn は新しい順で、前の書き換えを次が見る",
      order[:2] == [("new", "S"), ("old", "S+new")] and SENT[-1][1][0]["content"] == "S+new+old", order)
check("after は古い順で、答えと memo を読む",
      order[2:] == [("after-old", {"answer": "talk"}), ("after-new", "控え")], order)
check("orig の戻り値をそのまま返す", result == {"answer": "talk"})

print("[on_request 形]")
llm.reset_requests()
ctx2 = Ctx()
llm.install_requests(ctx2)
llm.on_request("310_struct", ctx2, lambda req: setattr(req, "structure", Wider) if req.structure is Struct else None)
SENT.clear()
ctx2.hooks[SEND](game_send, manager_name="talk", message=[{"role": "user", "content": "u"}], structure=Struct, extra=1)
check("名前で来たら名前のまま差し替える", SENT[-1][2] is Wider and SENT[-1][3] == {"extra": 1}, SENT[-1])
SENT.clear()
ctx2.hooks[SEND](game_send, "talk", [{"role": "user", "content": "u"}], Struct, extra=2)
check("位置で来たら位置のまま", SENT[-1][2] is Wider and SENT[-1][3] == {"extra": 2}, SENT[-1])
seen_req = {}
llm.on_request("311_peek", ctx2, lambda req: seen_req.update(
    name=req.manager_name, ns=req.ns, target=req.target, structure=req.structure, provider=req.provider))
SENT.clear()
original = [{"role": "user", "content": "u"}]
ctx2.hooks[SEND_NS](game_send_ns, "plain", original)
check("ns の頼みは structure が None で ns が真", seen_req.get("ns") is True and seen_req.get("structure") is None
      and seen_req.get("target") == SEND_NS and seen_req.get("name") == "plain", seen_req)
check("書き換えなければ同じ list を渡す", SENT[-1][1] is original)

print("[on_request 選ぶ]")
check("send_targets は部品と構造の有無で選ぶ",
      llm.send_targets("llm_manager_battle") == (BATTLE, BATTLE + "_with_no_structure")
      and llm.send_targets("llm_manager", ns=False) == (SEND,)
      and llm.send_targets("llm_manager", ns=True) == (SEND_NS,)
      and llm.send_targets() == tuple(target for target, _ns in llm.MANAGER_SEND_TARGETS)
      and llm.send_targets("llm_manager_world_generate", "llm_manager", ns=False)
      == (SEND, "scripts.llm.llm_manager_world_generate:send_request"),
      llm.send_targets("llm_manager_battle"))
try:
    llm.send_targets("llm_manager_shop")
    check("知らない部品は ValueError", False)
except ValueError:
    check("知らない部品は ValueError", True)
llm.reset_requests()
ctx3 = Ctx()
llm.install_requests(ctx3)
hits = []
llm.on_request("320_one", ctx3, lambda req: hits.append(("one", req.manager_name)), managers="referee_attack")
llm.on_request("321_set", ctx3, lambda req: hits.append(("set", req.manager_name)),
               managers=("colosseum_battle_summarizer", "guard_battle_summarizer"))
llm.on_request("322_fn", ctx3, lambda req: hits.append(("fn", req.manager_name)),
               managers=lambda name: name.startswith("referee_"))
llm.on_request("323_battle", ctx3, lambda req: hits.append(("battle", req.target)),
               targets=llm.send_targets("llm_manager_battle", ns=False))
for target, name in ((BATTLE, "referee_attack"), (SEND, "guard_battle_summarizer"), (SEND, "talk")):
    ctx3.hooks[target](game_send, name, [{"role": "user", "content": "u"}], Struct)
check("managers と targets で選ぶ", hits == [
    ("battle", BATTLE), ("fn", "referee_attack"), ("one", "referee_attack"),
    ("set", "guard_battle_summarizer")], hits)
hits.clear()
ctx3.hooks[SEND](game_send, None, [{"role": "user", "content": "u"}], Struct)
check("名前の無い頼みは managers を持つ登録に当てない", hits == [], hits)
for bad in (dict(targets=("nowhere:send",)), dict(managers=()), dict()):
    try:
        llm.on_request("329_bad", ctx3, None if not bad else (lambda req: None), **bad)
        check("書き間違いは登録で投げる {}".format(sorted(bad)), False)
    except (ValueError, TypeError):
        check("書き間違いは登録で投げる {}".format(sorted(bad) or "fn も after も無い"), True)

print("[on_request 守り]")
llm.reset_requests()
ctx4 = Ctx()
llm.install_requests(ctx4)
WARNS = []
calls = []


def fn_ok(req):
    calls.append("ok")
    return llm.edit_turn(req.message, lambda t: t + "+ok", role="user")


def fn_raises(req):
    calls.append("raises")
    req.message = [{"role": "user", "content": "壊した"}]
    raise RuntimeError("壊れた")


def fn_bad_type(req):
    calls.append("bad")
    req.structure = "型ではない"


def after_raises(req):
    calls.append("after-raises")
    raise RuntimeError("後で壊れた")


llm.on_request("330_ok", ctx4, fn_ok, after=lambda req: calls.append("after-ok"))
llm.on_request("331_raises", ctx4, fn_raises, after=after_raises, write=WARNS.append)
llm.on_request("332_bad", ctx4, fn_bad_type, write=WARNS.append)
SENT.clear()
ctx4.hooks[SEND](game_send, "talk", [{"role": "user", "content": "u"}], Struct)
check("投げた・違う型の MOD は戻して続ける", calls == ["bad", "raises", "ok", "after-ok", "after-raises"]
      and SENT[-1][1][0]["content"] == "u+ok" and SENT[-1][2] is Struct, (calls, SENT[-1]))
check("WARN を MOD のログに", len(WARNS) == 3 and all(line.startswith("WARN ") for line in WARNS), WARNS)
llm.on_request("333_ns", ctx4, lambda req: setattr(req, "structure", Wider), write=WARNS.append)
SENT.clear()
ctx4.hooks[SEND_NS](game_send_ns, "plain", [{"role": "user", "content": "u"}])
check("ns の頼みに型を入れたら戻す", "structure" not in SENT[-1][3] and any("no structure" in line for line in WARNS), WARNS[-1:])
llm.on_request("334_list", ctx4, lambda req: "文字列", write=WARNS.append)
WARNS.clear()
SENT.clear()
ctx4.hooks[SEND](game_send, "talk", [{"role": "user", "content": "u"}], Struct)
check("list でない戻り値は戻す", SENT[-1][1][0]["content"] == "u+ok" and any("list of messages" in line for line in WARNS), WARNS)
for owner in ("331_raises", "332_bad", "333_ns", "334_list"):
    llm.off_request(owner)
check("off_request で下げる", llm.request_owners() == ["330_ok"], llm.request_owners())
dead = Ctx()
dead.dead = True
llm.on_request("335_dead", dead, lambda req: calls.append("dead"))
calls.clear()
ctx4.hooks[SEND](game_send, "talk", [{"role": "user", "content": "u"}], Struct)
check("用済みの ctx の登録は呼ばない", "dead" not in calls and llm.request_owners() == ["330_ok"], calls)
llm.on_request("330_ok", ctx4, lambda req: calls.append("replaced"))
calls.clear()
ctx4.hooks[SEND](game_send, "talk", [{"role": "user", "content": "u"}], Struct)
check("同じ持ち主は差し替える", calls == ["replaced"], calls)


def orig_raises(*a, **k):
    raise ConnectionError("通信")


calls.clear()
try:
    ctx4.hooks[SEND](orig_raises, "talk", [{"role": "user", "content": "u"}], Struct)
    check("orig の例外はそのまま流し、after は呼ばない", False)
except ConnectionError:
    check("orig の例外はそのまま流し、after は呼ばない", calls == ["replaced"], calls)

print("[on_request 入れ子・素通し]")
llm.reset_requests()
ctx5 = Ctx()
llm.install_requests(ctx5)
counted = []
llm.on_request("340_count", ctx5, lambda req: counted.append(req.manager_name))
inner = []


def nesting_orig(manager_name, message, structure=None, **kw):
    # 送り口の中から送り口がもう一度呼ばれる（言語の包み・別名の入れ子）
    if not inner:
        inner.append(1)
        return ctx5.hooks[SEND](game_send, manager_name, message, structure)
    return game_send(manager_name, message, structure)


ctx5.hooks[SEND](nesting_orig, "talk", [{"role": "user", "content": "u"}], Struct)
check("1回の頼みに1度だけ当てる", counted == ["talk"], counted)
box = []
worker = threading.Thread(target=lambda: box.append(ctx5.hooks[SEND](game_send, "other", [], Struct)))
worker.start()
worker.join()
check("別のスレッドの頼みには当てる", counted == ["talk", "other"], counted)
llm.reset_requests()
llm.install_requests(ctx5)


def reads_nothing(*a, **k):
    raise AssertionError("登録が無いのに引数を読んだ")


plain_args = ("talk", [{"role": "user", "content": "u"}], Struct)
SENT.clear()
check("登録が無ければ素通し", ctx5.hooks[SEND](game_send, *plain_args) == {"answer": "talk"} and SENT[-1][1] is plain_args[1])


# ================================================================ listing
print("[listing]")


class Spec(object):
    def __init__(self, cls_name, args=()):
        self.cls_name, self.args = cls_name, list(args)


def button(text, cls_name, *args):
    return {"text": text, "spec": Spec(cls_name, args)}


class LCtx(object):
    def __init__(self, generation):
        self.generation = generation
        self.hooks = {}
        self.logs = []

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator

    def log(self, line, **kw):
        self.logs.append(line)

    def log_exc(self, line):
        raise AssertionError(line)

    def superseded(self):
        return False


choices.reset()
lctx = LCtx("L1")
check("包みを立てる", choices.install_listing(lctx) is True
      and set(lctx.hooks) == {target for target, _kind in choices.LISTING_TARGETS}, sorted(lctx.hooks))
check("1つの世代に1回", choices.install_listing(lctx) is False and choices.install_listing(LCtx("L1")) is False)
check("最初はどちらでもない", choices.listing() is None)
TALK, ADV = choices.LISTING_TARGETS[0][0], choices.LISTING_TARGETS[1][0]
seen_inside = []


def execute(self, *a, **k):
    seen_inside.append(choices.listing())     # 一覧の組み直しは orig の中で走る
    return "listed"


check("orig の戻り値を返す", lctx.hooks[TALK](execute, object()) == "listed")
check("会話するの一覧は orig の前に talk", seen_inside[-1] == choices.LISTING_TALK == "talk")
lctx.hooks[ADV](execute, object())
check("冒険者の一覧は orig の前に adventurer", seen_inside[-1] == choices.LISTING_ADVENTURER == "adventurer")

LIST = [button("戦士", "ConversationStartManager", "7"), button("やめる", "JustSetButtonToNormalPhase")]
app = types.SimpleNamespace(buttons=list(LIST))
view = choices.View(app, LIST)
check("一覧のままなら view.listing は印のまま", view.listing == "adventurer" and choices.listing() == "adventurer")
OTHER = [button("会話する", "DisplayTalkChoice"), button("冒険者と話す", "DisplayAdventurerTalkChoice"),
         button("移動する", "MovePhaseManager")]
view = choices.View(app, OTHER)
check("別の画面の spec が見えたら下ろす", view.listing is None and choices.listing() is None)
for spec in choices.LISTING_OTHER_SPECS:
    lctx.hooks[TALK](execute, object())
    check("下ろす spec {}".format(spec), choices.View(app, [button("x", spec)]).listing is None)
lctx.hooks[TALK](execute, object())
check("冒険者の一覧を出すと talk から替わる",
      (lctx.hooks[ADV](execute, object()), choices.listing())[1] == "adventurer")

# 組み直しの包みの前半（MOD の refresh より前に下ろす）
seen_refresh = []
choices.provide(lctx, None, refresh=lambda a, buttons: seen_refresh.append(choices.listing()), owner="350_refresh")
app = types.SimpleNamespace(buttons=[button("会話を終える", "ConversationEndManager")],
                            world=types.SimpleNamespace(characters={"1": object()}), in_conversation=None,
                            party=["player"])
choices.on_refresh(lctx, app)
check("refresh の古い口より前に下ろす", seen_refresh == [None], seen_refresh)
choices.reset()
check("reset で忘れる", choices.listing() is None)


if failures:
    print("\nFAILED ({}): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("\nall ok")
