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
  店の値の圧 … 誰も置かなければ (0.0, None)。いちばん大きい率（1 で止める）。None・真偽・投げた関数・用済みは数えない。
               引き受けた持ち主は生きている間だけ返す
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

    wanted.reset()
    check("誰も率を置かなければ (0.0, None)", wanted.shop_pressure(app) == (0.0, None))
    check("誰も引き受けなければ None", wanted.shop_pressure_taker() is None)
    gone = Ctx()
    wanted.declare_shop_pressure("a", Ctx(), lambda a: 0.1)
    wanted.declare_shop_pressure("b", Ctx(), lambda a: 0.25)
    wanted.declare_shop_pressure("gone", gone, lambda a: 0.9)
    gone.old = True
    check("率はいちばん大きいもの（用済みは数えない）", wanted.shop_pressure(app) == (0.25, "b"),
          wanted.shop_pressure(app))
    wanted.declare_shop_pressure("b", Ctx(), lambda a: None)
    check("同じ持ち主は差し替え、None は置かなかったもの", wanted.shop_pressure(app) == (0.1, "a"))
    wanted.declare_shop_pressure("c", Ctx(), lambda a: 3)
    wanted.declare_shop_pressure("d", Ctx(), lambda a: True)
    check("率は 1 で止め、真偽値は数えない", wanted.shop_pressure(app) == (1.0, "c"))

    def broken(a):
        raise KeyError("area")

    wanted.reset()
    wanted.declare_shop_pressure("x", Ctx(), broken)
    check("率の関数が投げたら置かなかったもの", wanted.shop_pressure(app) == (0.0, None)
          and any("x: KeyError" in row for row in wanted._store()["errors"]))
    retired = Ctx()
    wanted.take_shop_pressure("341", retired)
    check("引き受けた持ち主", wanted.shop_pressure_taker() == "341")
    retired.old = True
    check("用済みの引き受けは数えない", wanted.shop_pressure_taker() is None)
    wanted.reset()

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
