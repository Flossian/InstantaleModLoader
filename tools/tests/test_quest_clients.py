# -*- coding: utf-8 -*-
"""ローダの窓口 `quest_clients`（依頼の依頼人）をゲーム抜きで通す。

    python tools/tests/test_quest_clients.py

  置く・読む … 行に持ち主を足して返す。依頼の id と人物の id は文字列にそろえる。client_of はその依頼の行
  差し替え   … 同じ持ち主の登録は差し替える。withdraw で下げる
  用済み     … 登録した ctx が superseded になった登録は数えない
  例外・形   … 関数が投げたら置かなかったものとみなし errors に残す。依頼の id か依頼人の無い行は捨てる
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import quest_clients  # noqa: E402

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


def main():
    quest_clients.reset()
    app = object()
    check("誰も置いていなければ空", quest_clients.clients(app) == [] and quest_clients.client_of(app, "1") is None)

    print("置く・読む")
    crime = Ctx()
    quest_clients.declare("336_crime_overhaul", crime, lambda a: [
        {"quest": 7, "npc_id": 88},
        {"quest": "8", "npc_id": None, "npc_name": "手引き役"}])
    rows = quest_clients.clients(app)
    check("行に持ち主を足す", [r["owner"] for r in rows] == ["336_crime_overhaul"] * 2, rows)
    check("依頼の id と人物の id は文字列にそろえる", rows[0]["quest"] == "7" and rows[0]["npc_id"] == "88", rows)
    check("人物の id が無ければ空文字、名前はそのまま", rows[1]["npc_id"] == "" and rows[1]["npc_name"] == "手引き役",
          rows)
    check("client_of はその依頼の行", (quest_clients.client_of(app, 8) or {}).get("npc_name") == "手引き役")
    check("置かれていない依頼は None", quest_clients.client_of(app, "9") is None)

    print("差し替え")
    quest_clients.declare("336_crime_overhaul", crime, lambda a: [{"quest": "9", "npc_id": "1"}])
    check("同じ持ち主の登録は差し替える", [r["quest"] for r in quest_clients.clients(app)] == ["9"])
    check("いま数えている持ち主", quest_clients.owners() == ["336_crime_overhaul"], quest_clients.owners())
    quest_clients.withdraw("336_crime_overhaul")
    check("withdraw で下げる", quest_clients.clients(app) == [])

    print("用済み")
    old = Ctx()
    quest_clients.declare("336_crime_overhaul", old, lambda a: [{"quest": "1", "npc_id": "2"}])
    old.old = True
    check("用済みの世代の登録は数えない", quest_clients.clients(app) == [])
    quest_clients.declare("336_crime_overhaul", Ctx(), lambda a: [{"quest": "1", "npc_id": "2"}])
    check("新しい世代が登録し直せば効く", [r["quest"] for r in quest_clients.clients(app)] == ["1"])

    print("例外・形")

    def broken(a):
        raise KeyError("ledger")

    quest_clients.declare("broken", Ctx(), broken)
    check("投げた登録は何も置かない", [r["owner"] for r in quest_clients.clients(app)] == ["336_crime_overhaul"])
    check("投げた理由を残す", any("broken: KeyError" in row for row in quest_clients._store()["errors"]))
    quest_clients.declare("odd", Ctx(), lambda a: [None, "x", {"npc_id": "1"}, {"quest": ""},
                                                    {"quest": "3"}, {"quest": "4", "npc_id": "", "npc_name": ""},
                                                    {"quest": "5", "npc_id": 0}])
    check("依頼の id か依頼人の無い行は捨てる",
          [r["quest"] for r in quest_clients.clients(app) if r["owner"] == "odd"] == ["5"])
    quest_clients.reset()

    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
