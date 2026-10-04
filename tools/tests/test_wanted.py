# -*- coding: utf-8 -*-
"""ローダの窓口 `wanted`（手配の重さと全域手配の線）をゲーム抜きで通す。

    python tools/tests/test_wanted.py

  重さ     … 0 未満の手配度だけ「0 からどれだけ下か」。平常・0・読めない値は 0。合計は手配された土地だけ
  線       … 誰も置かなければ (None, None)。置いた中でいちばん低い線と、その持ち主を返す
  線が無い … None・0 以下・真偽・文字列は線を置かなかったものとみなす（追手が来ない設定）
  差し替え … 同じ持ち主の登録は差し替える。withdraw で下げる
  用済み   … 登録した ctx が superseded になった登録は数えない（その MOD を切った後の注入）
  例外     … 関数が投げたら線を置かなかったものとみなし、errors に残す
  終わり   … 追手の戦闘の終わりは生きている受け手へ写しで届く。受け手が投げても残りへ届け、errors に残す
  316      … 追手の数え方（hunt.weight_of）が窓口の数え方と同じ
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)
if MODS_DIR not in sys.path:
    sys.path.insert(0, MODS_DIR)

from instantale_modloader import wanted  # noqa: E402

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


class Character:
    def __init__(self, history):
        self.area_history = {key: {"lawfulness": value} for key, value in history.items()}


def main():
    check("手配は 0 からどれだけ下か", wanted.weight_of(-170) == 170 and wanted.weight_of(-1) == 1)
    check("平常・0 は重さ 0", wanted.weight_of(10) == 0 and wanted.weight_of(0) == 0)
    check("読めない値は重さ 0", all(wanted.weight_of(v) == 0 for v in (None, True, "-10")))
    check("合計は手配された土地だけ", wanted.total_of({"1": -30, "2": 10, "3": -15}) == 45)
    check("人物から合計", wanted.total_weight(Character({"1": -30, "2": 10})) == 30)

    wanted.reset()
    app = object()
    check("誰も置かなければ線は無い", wanted.hunted_line(app) == (None, None))

    a, b = Ctx(), Ctx()
    wanted.declare_hunted("a", a, lambda app_: 40)
    wanted.declare_hunted("b", b, lambda app_: 60)
    check("いちばん低い線と持ち主", wanted.hunted_line(app) == (40, "a"))
    wanted.declare_hunted("a", a, lambda app_: 80)
    check("同じ持ち主は差し替える", wanted.hunted_line(app) == (60, "b"))
    wanted.withdraw("b")
    check("下げれば数えない", wanted.hunted_line(app) == (80, "a"))

    a.old = True
    check("用済みの世代の登録は数えない", wanted.hunted_line(app) == (None, None)
          and wanted.owners() == [])
    wanted.declare_hunted("a", Ctx(), lambda app_: 40)
    check("新しい世代が登録し直せば効く", wanted.hunted_line(app) == (40, "a"))

    def broken(app_):
        raise KeyError("hunter")

    wanted.declare_hunted("broken", Ctx(), broken)
    check("投げた登録は線を置かない", wanted.hunted_line(app) == (40, "a"))
    check("投げた理由を残す", any("broken: KeyError" in row for row in wanted._store()["errors"]))

    wanted.reset()
    for value in (None, 0, -5, True, "40"):
        wanted.declare_hunted("odd", Ctx(), lambda app_, v=value: v)
        check("線でない答え {!r} は線を置かない".format(value),
              wanted.hunted_line(app) == (None, None))
    wanted.reset()

    heard = []
    old = Ctx()
    wanted.on_hunt_end("old", old, lambda app_, hunt: heard.append(("old", hunt)))
    wanted.on_hunt_end("new", Ctx(), lambda app_, hunt: heard.append(("new", hunt)))

    def boom(app_, hunt):
        raise ValueError("purse")

    wanted.on_hunt_end("boom", Ctx(), boom)
    old.old = True
    hunt = {"outcome": "won", "difficulty": 45}
    reached = wanted.hunt_ended(app, hunt)
    check("追手の戦闘の終わりは生きている登録へ届く", reached == ["new"] and len(heard) == 1
          and heard[0][1] == hunt, (reached, heard))
    check("受け取るのは写し（受け手が書き換えても知らせた側に響かない）", heard[0][1] is not hunt)
    check("受け手が投げても残りは止めず、理由を残す",
          any("boom: ValueError" in row for row in wanted._store()["errors"]))
    wanted.reset()
    check("忘れたら誰にも届かない", wanted.hunt_ended(app, hunt) == [])

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hunt_for_wanted", os.path.join(MODS_DIR, "316_bounty_hunter", "hunt.py"))
    hunt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hunt)
    values = (-170, -1, 0, 10, None, True)
    check("316 の追手の数え方が窓口と同じ",
          [hunt.weight_of(v) for v in values] == [wanted.weight_of(v) for v in values])

    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
