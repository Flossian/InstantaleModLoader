# -*- coding: utf-8 -*-
"""ローダの窓口 `arrivals`（施設に着いた場面で誰が話しかけるか）をゲーム抜きで通す。

    python tools/tests/test_arrivals.py

  申し出   … いちばん優先度の高い持ち主が勝つ。同じ優先度なら先に申し出た方。同じ持ち主は差し替える
  取りやめ … withdraw で下げると、譲っていた側が勝つ
  番号     … install の包みが移動ごとに番号を進め、前の到着の申し出を持ち越さない（同じ施設でも）
  包み無し … install が無ければ施設と時刻で見分ける
  素通し   … 包みは受け取った引数をそのまま orig へ渡し、戻り値も返す
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import arrivals  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Facility:
    def __init__(self, facility_id):
        self.id = facility_id


class Area:
    id = "3"


class Player:
    def __init__(self):
        self.current_area = Area()
        self.location = Facility("10")


class App:
    def __init__(self):
        self.player = Player()


class Ctx:
    generation = 7

    def __init__(self):
        self.hooks = {}

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def main():
    app = App()

    print("[申し出]")
    arrivals.reset()
    ctx = Ctx()
    arrivals.install(ctx)
    move = ctx.hooks[arrivals.MOVE_TARGET]
    check("包みは1世代に1枚", arrivals.install(Ctx()) == [arrivals.MOVE_TARGET]
          and len(ctx.hooks) == 1)
    got = move(lambda self, *a, **k: ("orig", a, k), "phase", 1, x=2)
    check("包みは素通し", got == ("orig", (1,), {"x": 2}), got)
    check("申し出が無ければ None", arrivals.winner(app) is None)
    arrivals.offer(app, "low", 0)
    arrivals.offer(app, "high", 10)
    check("優先度の高い方が勝つ", arrivals.winner(app) == "high")
    arrivals.offer(app, "high", -1)
    check("同じ持ち主は差し替える", arrivals.winner(app) == "low", arrivals.offers(app))
    arrivals.offer(app, "high", 0)
    check("同じ優先度なら先に申し出た方", arrivals.winner(app) == "low")

    print("[取りやめ]")
    arrivals.offer(app, "high", 10)
    arrivals.withdraw(app, "high")
    check("下げれば譲っていた側が勝つ", arrivals.winner(app) == "low")

    print("[番号]")
    arrivals.offer(app, "high", 10)
    move(lambda self: None, "phase")
    check("次の移動では前の申し出を持ち越さない（同じ施設でも）", arrivals.winner(app) is None,
          arrivals.offers(app))

    print("[包み無し]")
    arrivals.reset()
    arrivals.offer(app, "high", 10)
    check("同じ施設なら同じ到着", arrivals.winner(app) == "high")
    app.player.location = Facility("11")
    check("施設が変われば別の到着", arrivals.winner(app) is None)
    arrivals.offer(app, "high", 10)
    arrivals._store()["at"] -= arrivals.WINDOW_SECONDS + 1
    check("窓を過ぎれば別の到着", arrivals.winner(app) is None)
    arrivals.reset()

    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
