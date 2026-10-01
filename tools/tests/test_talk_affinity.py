# -*- coding: utf-8 -*-
"""ローダの窓口 `talk_affinity`（会話で上げられる好感度の上限）をゲーム抜きで通す。

    python tools/tests/test_talk_affinity.py

  上限     … 誰も口を出さなければ自分の上限。口を出した中でいちばん低い上限と、その持ち主を返す
  広げない … 自分の上限より高い値は無視する
  差し替え … 同じ持ち主の登録は差し替える。withdraw で下げる
  用済み   … 登録した ctx が superseded になった登録は数えない
  例外     … 関数が投げたら口を出さなかったものとみなし、errors に残す
  数でない … None・真偽・文字列は口を出さなかったものとみなす
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import talk_affinity  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx:
    def __init__(self):
        self.old = False

    def superseded(self):
        return self.old


def only(npc_id, value):
    return lambda app, asked: value if asked == npc_id else None


def main():
    talk_affinity.reset()
    app = object()
    check("誰も口を出さなければ自分の上限", talk_affinity.ceiling(app, "7", 60) == (60, None))

    low, high = Ctx(), Ctx()
    talk_affinity.limit("low", low, only("7", 30))
    talk_affinity.limit("high", high, only("7", 50))
    check("いちばん低い上限と持ち主", talk_affinity.ceiling(app, "7", 60) == (30, "low"))
    check("相手が違えば口を出さない", talk_affinity.ceiling(app, 8, 60) == (60, None))
    check("id は文字列で渡す", talk_affinity.ceiling(app, 7, 60) == (30, "low"))
    check("自分の上限より高い値で広げない", talk_affinity.ceiling(app, "7", 20) == (20, None))

    talk_affinity.limit("low", low, only("7", 40))
    check("同じ持ち主は差し替える", talk_affinity.ceiling(app, "7", 60) == (40, "low"))
    talk_affinity.withdraw("low")
    check("下げれば数えない", talk_affinity.ceiling(app, "7", 60) == (50, "high"))

    high.old = True
    check("用済みの世代の登録は数えない", talk_affinity.ceiling(app, "7", 60) == (60, None)
          and talk_affinity.owners() == [])
    talk_affinity.limit("high", Ctx(), only("7", 50))
    check("新しい世代が登録し直せば効く", talk_affinity.ceiling(app, "7", 60) == (50, "high"))

    def broken(app_, npc_id):
        raise KeyError("rival")

    talk_affinity.limit("broken", Ctx(), broken)
    check("投げた登録は口を出さない", talk_affinity.ceiling(app, "7", 60) == (50, "high"))
    check("投げた理由を残す", any("broken: KeyError" in row
                                  for row in talk_affinity._store()["errors"]))

    talk_affinity.reset()
    for value in (None, True, "10"):
        talk_affinity.limit("odd", Ctx(), lambda app_, npc_id, v=value: v)
        check("数でない答え {!r} は口を出さない".format(value),
              talk_affinity.ceiling(app, "7", 60) == (60, None))
    talk_affinity.reset()

    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
