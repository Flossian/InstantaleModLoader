# -*- coding: utf-8 -*-
"""ローダの窓口 `text_filters`（本文の1行の書き換え・出さない）をゲーム抜きで通す。

    python tools/tests/test_text_filters.py

  順    … 登録の新しい順に通し、前の MOD の差し替えを次の MOD が見る
  出さない … DROP で残りの MOD と orig を呼ばない
  形    … 位置で来ても名前で来ても、来た形のまま差し替える。app（self）を渡す
  守り  … fn が投げてもその MOD は何もしなかったことにして続ける。用済みの ctx の登録は呼ばない
  包み  … 1つの世代につき1枚。登録が無ければ素通し
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import text_filters as tf  # noqa: E402

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


shown = []


def orig(self, context=None, *args, **kwargs):
    shown.append((context, args, kwargs))
    return "drawn"


app = object()
tf.reset()
ctx = Ctx()
tf.install(ctx)
hook = ctx.hooks[tf.TARGET]
check("1つの世代に1枚", tf.install(ctx) is False)
check("登録が無ければ素通し", hook(orig, app, "そのまま") == "drawn" and shown[-1][0] == "そのまま")

print("[順]")
seen = []
tf.on("old", ctx, lambda text, a: (seen.append(("old", text, a is app)), text + "+old")[1])
tf.on("new", ctx, lambda text, a: (seen.append(("new", text)), text.replace("A", "B"))[1])
tf.on("none", ctx, lambda text, a: None)
shown.clear()
hook(orig, app, "A行")
check("新しい順に通し、前の差し替えを次が見る", seen == [("new", "A行"), ("old", "B行", True)], seen)
check("差し替えた文を orig へ渡す", shown[-1][0] == "B行+old")

print("[形]")
shown.clear()
hook(orig, app, context="A行", extra=1)
check("名前で来たら名前のまま", shown[-1] == ("B行+old", (), {"extra": 1}), shown[-1])
shown.clear()
hook(orig, app, "A行", 3)
check("位置で来たら位置のまま（残りの引数も渡す）", shown[-1] == ("B行+old", (3,), {}), shown[-1])
shown.clear()
hook(orig, app)
check("文が来ていなければ足さない", shown[-1] == (None, (), {}), shown[-1])

print("[出さない]")
tf.on("drop", ctx, lambda text, a: tf.DROP if isinstance(text, str) and "伏せる" in text else None)
seen.clear()
shown.clear()
result = hook(orig, app, "伏せる行")
check("DROP なら orig を呼ばず None", result is None and shown == [])
check("→ 古い登録は呼ばない", seen == [])
tf.off("drop")

print("[守り]")
lines = []
tf.on("bad", ctx, lambda text, a: 1 / 0, write=lines.append)
shown.clear()
hook(orig, app, "A行")
check("投げた MOD は飛ばして続ける", shown[-1][0] == "B行+old")
check("→ 投げたことを残す", any("WARN text_filters" in line for line in lines), lines)
tf.off("bad")
dead = Ctx()
tf.on("dead", dead, lambda text, a: "死んだ")
dead.dead = True
shown.clear()
hook(orig, app, "A行")
check("用済みの ctx の登録は呼ばない", shown[-1][0] == "B行+old" and "dead" not in tf.owners())
tf.reset()

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
