# -*- coding: utf-8 -*-
"""ローダの窓口 `quest_ends`（依頼が終わる場面の前後）をゲーム抜きで通す。

    python tools/tests/test_quest_ends.py

  読む    … 終わる依頼の id と中身は orig の前に読んで渡す（orig の中で片付いても読める）
  順      … before は登録の新しい順、after は古い順。before の戻り値は同じ持ち主の after へ
  投げた回 … orig が投げたら always=True の after だけ来る（call.error）。例外はそのまま流す
  包み    … 1つの世代につき1枚。self.app が無ければ ui.find_app()
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import quest_ends, ui  # noqa: E402

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

    def superseded(self):
        return False

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator


class App(object):
    def __init__(self):
        self.current_quest_data = {"id": 7, "quest_title": "題"}


class Manager(object):
    def __init__(self, app):
        self.app = app


def orig(self):
    # ゲームは依頼の終わりの中で今の依頼を片付ける。
    self.app.current_quest_data = None
    return "done"


seen = []
quest_ends.reset()
ctx = Ctx()


def before_a(call):
    seen.append(("before", "a", call.quest_id))
    return "memo-a"


def after_a(call):
    seen.append(("after", "a", call.memo, call.quest_id, call.result))


def before_b(call):
    seen.append(("before", "b", call.quest and call.quest.get("quest_title")))
    return "memo-b"


def after_b(call):
    seen.append(("after", "b", call.memo, type(call.error).__name__ if call.error else None))


quest_ends.on("a", ctx, before=before_a, after=after_a)
quest_ends.on("b", ctx, before=before_b, after=after_b, always=True)
hook = ctx.hooks[quest_ends.TARGET]
check("1つの世代に1枚", quest_ends.install(ctx) == [quest_ends.TARGET] and len(ctx.hooks) == 1)
app = App()
result = hook(orig, Manager(app))
check("orig の戻り値を返す", result == "done")
check("before は新しい順で、終わる依頼を orig の前に読んで渡す",
      seen[:2] == [("before", "b", "題"), ("before", "a", "7")], seen[:2])
check("after は古い順で、memo と戻り値が渡る",
      seen[2:] == [("after", "a", "memo-a", "7", "done"), ("after", "b", "memo-b", None)], seen[2:])

print("[投げた回]")
seen.clear()


def broken(self):
    raise RuntimeError("boom")


try:
    hook(broken, Manager(App()))
except RuntimeError:
    check("orig の例外はそのまま流す", True)
else:
    check("orig の例外はそのまま流す", False)
check("always=True の after だけ来る（call.error）",
      [row for row in seen if row[0] == "after"] == [("after", "b", "memo-b", "RuntimeError")], seen)

print("[app の引き方]")
seen.clear()
found = App()
real_find = ui.find_app
ui.find_app = lambda: found
try:
    hook(lambda self: "done", type("QE", (), {})())
finally:
    ui.find_app = real_find
check("self.app が無ければ ui.find_app()", ("before", "a", "7") in seen, seen)
seen.clear()
quest_ends.off("a")
quest_ends.off("b")
check("下げた登録は呼ばない", hook(orig, Manager(App())) == "done" and seen == [])
quest_ends.reset()

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
