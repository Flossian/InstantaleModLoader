# -*- coding: utf-8 -*-
"""ローダの窓口 `quest_claims`（主人公以外が狙い・片付けた依頼と、急ぎの依頼）をゲーム抜きで通す。

    python tools/tests/test_quest_claims.py

  置く・読む … 行に持ち主を足して返す。依頼の id は文字列にそろえる。状態で絞れる
  重なり     … 1つの依頼に狙っている行と片付けた行があれば、片付けた行を先に返す
  急ぎ       … 急ぎの行も同じ形で返す。片付ける側の登録とは別に数える
  差し替え   … 同じ持ち主の登録は差し替える。withdraw で両方下げる
  用済み     … 登録した ctx が superseded になった登録は数えない
  例外・形   … 関数が投げたら置かなかったものとみなし errors に残す。依頼の id が無い行は捨てる
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import quest_claims  # noqa: E402

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
    quest_claims.reset()
    app = object()
    check("誰も置いていなければ空", quest_claims.claims(app) == [] and quest_claims.urgent(app) == []
          and quest_claims.claim_of(app, "1") is None)

    rival = Ctx()
    quest_claims.declare("rival", rival, lambda a: [
        {"quest": 7, "area": 3, "title": "狼退治", "by": "ガラン", "state": quest_claims.AIMING, "day": 10},
        {"quest": "8", "area": "3", "title": "護衛", "by": "ガラン", "state": quest_claims.DONE, "day": 5}])
    rows = quest_claims.claims(app)
    check("行に持ち主を足し、id と土地は文字列", [(r["quest"], r["area"], r["owner"]) for r in rows]
          == [("7", "3", "rival"), ("8", "3", "rival")], rows)
    check("状態で絞る", [r["quest"] for r in quest_claims.claims(app, quest_claims.DONE)] == ["8"])
    check("依頼の id で引く（数でも文字列でも）", quest_claims.claim_of(app, 7)["by"] == "ガラン"
          and quest_claims.claim_of(app, "9") is None)

    other = Ctx()
    quest_claims.declare("other", other, lambda a: [
        {"quest": "7", "area": "3", "by": "ミラ", "state": quest_claims.DONE, "day": 12}])
    check("片付けた行を狙っている行より先に返す", quest_claims.claim_of(app, "7")["by"] == "ミラ")
    check("状態を指定すればその状態の行", quest_claims.claim_of(app, "7", quest_claims.AIMING)["by"] == "ガラン")

    events = Ctx()
    quest_claims.declare_urgent("events", events, lambda a: [{"quest": 21, "area": 5, "why": "疫病（悪化）"}])
    check("急ぎの行", quest_claims.urgent(app) == [{"quest": "21", "area": "5", "why": "疫病（悪化）",
                                                  "owner": "events"}], quest_claims.urgent(app))
    check("急ぎは片付ける側に混ざらない", all(r["owner"] != "events" for r in quest_claims.claims(app)))
    check("持ち主の一覧", quest_claims.owners() == (["rival", "other"], ["events"]), quest_claims.owners())

    quest_claims.declare("other", other, lambda a: [])
    check("同じ持ち主は差し替える", quest_claims.claim_of(app, "7")["by"] == "ガラン")
    quest_claims.withdraw("events")
    check("withdraw で急ぎも下げる", quest_claims.urgent(app) == [])

    rival.old = True
    check("用済みの世代の登録は数えない", quest_claims.claims(app) == [])
    quest_claims.declare("rival", Ctx(), lambda a: [{"quest": "1", "state": quest_claims.DONE}])
    check("新しい世代が登録し直せば効く", [r["quest"] for r in quest_claims.claims(app)] == ["1"])

    def broken(a):
        raise KeyError("ledger")

    quest_claims.declare("broken", Ctx(), broken)
    check("投げた登録は何も置かない", [r["owner"] for r in quest_claims.claims(app)] == ["rival"])
    check("投げた理由を残す", any("broken: KeyError" in row for row in quest_claims._store()["errors"]))
    quest_claims.declare("odd", Ctx(), lambda a: [None, "x", {"area": "1"}, {"quest": ""}, {"quest": "2"}])
    check("依頼の id が無い行は捨てる", [r["quest"] for r in quest_claims.claims(app) if r["owner"] == "odd"]
          == ["2"])
    quest_claims.reset()

    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
