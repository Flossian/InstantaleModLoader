# -*- coding: utf-8 -*-
"""ローダの窓口 `battles`（戦闘の組み立て・始まり・1手・終わりの前後）をゲーム抜きで通す。

    python tools/tests/test_window_c1.py

  終わり  … END は終わり方3つに同じ処理を載せ、call.kind で終わり方を分ける。end_type は orig の前と後に読む
  順      … before は登録の新しい順、after は古い順。before の戻り値は同じ持ち主の after へ
  投げた回 … orig が投げたら always=True の after だけ来る（call.error）。例外はそのまま流す
  握る    … 前後の処理の例外は握って記録し、残りの MOD と orig を止めない
  引数    … BUILD は enemy_type / enemy_content と app を引数から、1手は character_key などを名前でも位置でも読む
  包み    … 1つの世代につき種類ごとに1枚。用済みの ctx の登録は呼ばない。下げた登録は呼ばない
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import battles, ui  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("ok  " if ok else "FAIL", name, "  " + str(detail) if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Ctx(object):
    seq = 0

    def __init__(self):
        Ctx.seq += 1
        self.generation = Ctx.seq
        self.hooks = {}
        self.gone = False

    def superseded(self):
        return self.gone

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator


class App(object):
    pass


class EndManager(object):
    def __init__(self, app, end_type=None):
        self.app = app
        if end_type is not None:
            self.end_type = end_type


def end_orig(self):
    return "ended"


battles.reset()
ctx = Ctx()
seen = []

print("[終わり方3つ]")


def after_all(call):
    seen.append(("all", call.kind, call.label, call.end_type, call.result))


battles.on("a", ctx, (battles.END,), after=after_all)
check("END は3つの関所を立てる",
      all(battles.TARGETS[kind] in ctx.hooks for kind in battles.ENDS) and len(ctx.hooks) == 3, ctx.hooks)
check("owners(END) は3つの和", battles.owners(battles.END) == ["a"], battles.owners(battles.END))
app = App()
for kind, end_type in ((battles.END_NORMAL, "won"), (battles.END_FREE, None), (battles.END_COLOSSEUM, None)):
    ctx.hooks[battles.TARGETS[kind]](end_orig, EndManager(app, end_type))
check("call.kind で終わり方を分け、label と end_type が載る", seen == [
    ("all", battles.END_NORMAL, "BattleEndManager.end_phase", "won", "ended"),
    ("all", battles.END_FREE, "BattleEndInFreeAction.end_phase", None, "ended"),
    ("all", battles.END_COLOSSEUM, "BattleEndInColosseum.end_phase", None, "ended"),
], seen)

print("[一部の終わり方・順・memo]")
seen.clear()


def before_b(call):
    seen.append(("before", "b", call.end_type))
    return "memo-b"


def after_b(call):
    seen.append(("after", "b", call.memo, call.end_type))


def before_c(call):
    seen.append(("before", "c"))
    return "memo-c"


def after_c(call):
    seen.append(("after", "c", call.memo, type(call.error).__name__ if call.error else None))


battles.on("b", ctx, (battles.END_NORMAL,), before=before_b, after=after_b)
battles.on("c", ctx, battles.END_NORMAL, before=before_c, after=after_c, always=True)
check("同じ世代では関所を増やさない", len(ctx.hooks) == 3, ctx.hooks)


def changing_orig(self):
    self.end_type = "escaped"
    return "ended"


ctx.hooks[battles.TARGETS[battles.END_NORMAL]](changing_orig, EndManager(app, "won"))
check("before は新しい順で、end_type は orig の前の値", seen[:2] == [("before", "c"), ("before", "b", "won")], seen)
check("after は古い順で、memo と orig の後に読み直した end_type", seen[2:] == [
    ("all", battles.END_NORMAL, "BattleEndManager.end_phase", "escaped", "ended"),
    ("after", "b", "memo-b", "escaped"),
    ("after", "c", "memo-c", None),
], seen[2:])
seen.clear()
ctx.hooks[battles.TARGETS[battles.END_FREE]](end_orig, EndManager(app))
check("END_NORMAL だけの登録は他の終わり方に来ない",
      seen == [("all", battles.END_FREE, "BattleEndInFreeAction.end_phase", None, "ended")], seen)

print("[投げた回]")
seen.clear()


def broken(self):
    self.end_type = "escaped"
    raise RuntimeError("boom")


try:
    ctx.hooks[battles.TARGETS[battles.END_NORMAL]](broken, EndManager(app, "won"))
except RuntimeError:
    check("orig の例外はそのまま流す", True)
else:
    check("orig の例外はそのまま流す", False)
check("always=True の after だけ来る（call.error）",
      [row for row in seen if row[0] != "before"] == [("after", "c", "memo-c", "RuntimeError")], seen)

print("[前後の処理の例外]")
seen.clear()
notes = []


def bad_before(call):
    raise ValueError("bad")


battles.on("d", ctx, (battles.START,), before=bad_before, write=notes.append)


def after_e(call):
    seen.append(("after", "e", call.app is app, call.label))


battles.on("e", ctx, (battles.START,), after=after_e)
result = ctx.hooks[battles.TARGETS[battles.START]](lambda self: "started", EndManager(app))
check("握って orig と残りの MOD を止めない", result == "started" and seen == [
    ("after", "e", True, "BattleStartManager.start_battle")], seen)
check("例外は登録の write に WARN で残す", any(n.startswith("WARN battles.start") for n in notes), notes)

print("[組み立て]")
seen.clear()


def build_before(call):
    seen.append(("before", call.app, call.enemy_type, call.enemy_content, call.obj.__dict__.get("ready")))


def build_after(call):
    seen.append(("after", call.enemy_type, call.obj.ready))


battles.on("f", ctx, (battles.BUILD,), before=build_before, after=build_after)
hook = ctx.hooks[battles.TARGETS[battles.BUILD]]


class Builder(object):
    pass


def build_orig(self, app, enemy_type=None, enemy_content=None):
    self.app = app
    self.ready = True


hook(build_orig, Builder(), app, "guard", None)
hook(build_orig, Builder(), app, enemy_type="specific", enemy_content=["npc_1"])
check("引数を位置でも名前でも読み、before はまだ組み上がる前", seen == [
    ("before", app, "guard", None, None), ("after", "guard", True),
    ("before", app, "specific", ["npc_1"], None), ("after", "specific", True),
], seen)
seen.clear()
found = App()
real_find = ui.find_app
ui.find_app = lambda: found
try:
    hook(lambda self, *a, **k: None, Builder(), enemy_type="guard")
finally:
    ui.find_app = real_find
check("引数に app が無ければ ui.find_app()", seen and seen[0][1] is found, seen)

print("[1手]")
seen.clear()


def action_before(call):
    seen.append((call.kind, call.character_key, call.character_side, call.battle_action,
                 getattr(call, "effect_to_enemies", "-")))


battles.on("g", ctx, (battles.ACTION, battles.EFFECT), before=action_before)
ctx.hooks[battles.TARGETS[battles.ACTION]](lambda self, *a, **k: None, EndManager(app), "勇者", "味方陣営",
                                           {"vfx": "slash"})
ctx.hooks[battles.TARGETS[battles.EFFECT]](lambda self, *a, **k: None, EndManager(app), "敵1",
                                           character_side="敵側", battle_action={}, effect_to_enemies=[1])
check("1手と効果の引数を名前でも位置でも読む", seen == [
    (battles.ACTION, "勇者", "味方陣営", {"vfx": "slash"}, "-"),
    (battles.EFFECT, "敵1", "敵側", {}, [1]),
], seen)

print("[enter / leave]")
seen.clear()
battles.on("h", ctx, (battles.START,), enter=lambda call: seen.append("enter") or "m",
           leave=lambda call: seen.append(("leave", call.memo)))
ctx.hooks[battles.TARGETS[battles.START]](lambda self: seen.append("orig"), EndManager(app))
check("enter / leave は orig のすぐ前と後で、after より先",
      seen == ["enter", "orig", ("leave", "m"), ("after", "e", True, "BattleStartManager.start_battle")], seen)

print("[app の引き方]")
seen.clear()


class Broken(object):
    @property
    def app(self):
        raise RuntimeError("no app")


ui.find_app = lambda: found
try:
    ctx.hooks[battles.TARGETS[battles.START]](lambda self: None, Broken())
finally:
    ui.find_app = real_find
check("self.app が読めなければ ui.find_app()", ("after", "e", False, "BattleStartManager.start_battle") in seen
      and battles.GATES[battles.START] is not None, seen)

print("[登録の出し入れ]")
try:
    battles.on("x", ctx, ("nope",), after=after_all)
except ValueError:
    check("知らない種類は ValueError", True)
else:
    check("知らない種類は ValueError", False)
try:
    battles.on("x", ctx, (battles.START,))
except ValueError:
    check("前後の処理が1つも無ければ ValueError", True)
else:
    check("前後の処理が1つも無ければ ValueError", False)
battles.on("b", ctx, (battles.END_NORMAL,), after=lambda call: seen.append("b2"))
check("同じ持ち主は種類ごとに差し替える（数は増えず、登録の新しい側へ移る。gates の決まり）",
      battles.owners(battles.END_NORMAL) == ["a", "c", "b"]
      and battles.owners(battles.END_FREE) == ["a"], battles.owners(battles.END_NORMAL))
seen.clear()
battles.off("c", (battles.END,))
battles.off("a")
ctx.hooks[battles.TARGETS[battles.END_NORMAL]](end_orig, EndManager(app, "won"))
check("下げた登録は呼ばない", seen == ["b2"], seen)

print("[世代]")
seen.clear()
ctx.gone = True
ctx2 = Ctx()
battles.on("z", ctx2, (battles.END_NORMAL,), after=lambda call: seen.append("z"))
check("新しい世代では包み直す", battles.TARGETS[battles.END_NORMAL] in ctx2.hooks, ctx2.hooks)
ctx2.hooks[battles.TARGETS[battles.END_NORMAL]](end_orig, EndManager(app, "won"))
check("用済みの ctx の登録は呼ばない", seen == ["z"], seen)
check("owners は生きている登録だけ", battles.owners(battles.END) == ["z"], battles.owners(battles.END))
battles.reset()
check("reset で全部忘れる", battles.owners(battles.END) == [] and battles.GATES[battles.START].installed() is None)

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
