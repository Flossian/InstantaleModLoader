# -*- coding: utf-8 -*-
"""ローダの窓口 `quest_generation`（依頼の生成に渡す引数を分け合う）をゲーム抜きで通す。

    python tools/tests/test_quest_generation.py

  順      … 生成ごとの書き換え（新しい順）→ 1回だけの rewrite（置いた順の逆）→ brief と difficulty（置いた順）→ orig → after
  1回     … 当てたら下げる。期限切れは当てずに捨てて1行残す。cancel で下げる。置き直すと差し替える
  形      … 位置で来ても名前で来ても、来た形のまま渡す。来ていない引数は足さない
  守り    … rewrite / after が投げても残りと orig は止めない。用済みの ctx の登録は当てない。int でない難易度は外す
  包み    … 1つの世代につき1枚。apply() の外の offer では包みを立てない
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import quest_generation as qg  # noqa: E402

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
        self.dead = False

    def superseded(self):
        return self.dead

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator


calls = []


def orig(*args, **kwargs):
    calls.append((args, kwargs))
    return {"quest_title": "題"}


POS = ("世界", "街", "街の概要", "街の構造", "周辺", 10)


def area_of(call):
    args, kwargs = call
    return kwargs["area_description"] if "area_description" in kwargs else args[4]


def difficulty_of(call):
    args, kwargs = call
    return kwargs["quest_difficulty"] if "quest_difficulty" in kwargs else args[5]


lines = []
write = lines.append

print("[包み]")
qg.reset()
ctx = Ctx()
check("apply() の中の install で包む", qg.install(ctx, write) and qg.TARGET in ctx.hooks)
check("同じ世代では2枚目を包まない", qg.install(ctx, write) is False)
hook = ctx.hooks[qg.TARGET]
other = Ctx()
qg.offer("x", other, brief="b")
check("offer では包みを立てない", qg.TARGET not in other.hooks)
qg.cancel("x")

print("[順]")
order = []
qg.on("328", ctx, lambda r: (order.append("328"), r.set("area_description", "伏せた")), write=write)
qg.on("999", ctx, lambda r: order.append("999"), write=write)
qg.offer("318", ctx, rewrite=lambda r: (order.append("318"), r.set("quest_difficulty", r.get("quest_difficulty") + 5)),
         tag="search", write=write)
qg.offer("325", ctx, brief="+道", difficulty=40, after=lambda res: order.append("after325"), write=write)
qg.offer("338", ctx, brief="+出来事", after=lambda res: order.append("after338:" + res["quest_title"]), write=write)
calls.clear()
result = hook(orig, *POS)
check("生成ごとの書き換えは新しい順、1回の rewrite はその後", order[:3] == ["999", "328", "318"], order)
check("after は orig の後に置いた順", order[3:] == ["after325", "after338:題"], order)
check("伏せた後に brief を置いた順で足す", area_of(calls[-1]) == "伏せた+道+出来事", area_of(calls[-1]))
check("明示の難易度が上乗せより勝つ", difficulty_of(calls[-1]) == 40, difficulty_of(calls[-1]))
check("orig の戻り値を返す", result == {"quest_title": "題"})
check("当てた offer は下げる", not any(qg.pending(o) for o in ("318", "325", "338")))
calls.clear()
hook(orig, *POS)
check("次の生成には当てない（生成ごとの書き換えだけ）", area_of(calls[-1]) == "伏せた" and difficulty_of(calls[-1]) == 10)
qg.off("328")
qg.off("999")

print("[形]")
qg.offer("325", ctx, brief="+道", difficulty=30)
calls.clear()
hook(orig, world_overview="世界", settlement_name="街", settlement_overview="概要",
     settlement_structure_description="構造", area_description="周辺", quest_difficulty=5)
args, kwargs = calls[-1]
check("名前で来たら名前のまま渡す", args == () and kwargs["area_description"] == "周辺+道" and kwargs["quest_difficulty"] == 30)
lines.clear()
qg.offer("325", ctx, brief="+道", write=write)
calls.clear()
hook(orig, "世界", "街")
check("来ていない引数は足さない", calls[-1] == (("世界", "街"), {}), calls[-1])
check("→ 足せなかったことを1行残す", any("was not passed" in line for line in lines), lines)

print("[1回]")
qg.offer("325", ctx, brief="+古い")
qg.offer("325", ctx, brief="+新しい")
calls.clear()
hook(orig, *POS)
check("置き直すと差し替える", area_of(calls[-1]) == "周辺+新しい", area_of(calls[-1]))
qg.offer("325", ctx, brief="+道")
check("cancel で下げる", qg.cancel("325") and not qg.pending("325"))
calls.clear()
hook(orig, *POS)
check("→ 下げたものは当てない", area_of(calls[-1]) == "周辺")
lines.clear()
qg.offer("325", ctx, brief="+道", ttl=-1, write=write)
calls.clear()
hook(orig, *POS)
check("期限切れは当てない", area_of(calls[-1]) == "周辺")
check("→ 1行残す", any("stale request" in line for line in lines), lines)
check("→ 捨てる", not qg.pending("325"))

print("[守り]")
lines.clear()
qg.on("bad", ctx, lambda r: 1 / 0, write=write)
qg.offer("325", ctx, brief="+道", after=lambda res: 1 / 0, write=write)
calls.clear()
result = hook(orig, *POS)
check("rewrite と after が投げても orig は呼ぶ", area_of(calls[-1]) == "周辺+道" and result == {"quest_title": "題"})
check("→ 投げたことを残す", sum("WARN quest_generation" in line for line in lines) == 2, lines)
qg.off("bad")
dead = Ctx()
qg.on("dead", dead, lambda r: r.set("area_description", "死んだ"))
qg.offer("dead2", dead, brief="+死んだ")
dead.dead = True
calls.clear()
hook(orig, *POS)
check("用済みの ctx の登録は当てない", area_of(calls[-1]) == "周辺", area_of(calls[-1]))
check("→ owners にも出さない", "dead" not in qg.owners())
lines.clear()
qg.offer("325", ctx, brief="+道", difficulty="12", write=write)
calls.clear()
hook(orig, *POS)
check("int でない難易度は外し、brief は当てる", difficulty_of(calls[-1]) == 10 and area_of(calls[-1]) == "周辺+道")
check("→ 外したことを残す", any("is not an int" in line for line in lines), lines)
qg.offer("325", ctx, difficulty=True)
calls.clear()
hook(orig, *POS)
check("真偽値は難易度にしない", difficulty_of(calls[-1]) == 10)
qg.reset()

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
