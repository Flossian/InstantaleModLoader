# -*- coding: utf-8 -*-
"""913_crime_incentive の脱獄の決まり（jailbreak.py）をゲーム抜きで通す。

    python tools/tests/test_wip_crime_jailbreak.py

  引数   … 「服役する」のボタンの引数（残り年数・罪状・出来事・刑期）を読む。形の違うものは読まない
  延ばす … 残り年数と刑期を同じだけ延ばす。罪状と出来事はそのまま
  確率   … 備えが実る確率は能力値 15 で基準、露見は備えの数で上がり、上限で止まる
  相手   … 決行の相手は備え1つごとに弱く、下限は2割、難易度は1未満にならない
  判定   … 露見は実るかより先に引く。金で買う備えは露見せず必ず実る。備えは上限で止まる
  画面   … ボタンの文言の頭が、残骸を見分ける頭と揃っている
"""
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def load_parts():
    """MOD のフォルダを名前の付いたパッケージとして読み、部品だけを取り出す。"""
    matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_incentive"))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *_crime_incentive: {}".format(matches))
    package = types.ModuleType("crime_incentive_parts")
    package.__path__ = [os.path.join(MODS_DIR, matches[0])]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + ".jailbreak")


jailbreak = load_parts()
failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({})".format(detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Seq(object):
    """決まった順に値を返す乱数。"""

    def __init__(self, *values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0)


def scene_args():
    print("引数")
    args = [3, "指名手配者としての逮捕", "周辺地域で罪を重ねた。", 4]
    check("実機の並びを読む", jailbreak.serve_args(args) == (3, args[1], args[2], 4))
    check("タプルでも読む", jailbreak.serve_args(tuple(args)) == (3, args[1], args[2], 4))
    check("数の要る所が数でなければ読まない", jailbreak.serve_args(["3", "x", "y", 4]) is None)
    check("真偽値は数とみなさない", jailbreak.serve_args([True, "x", "y", 4]) is None)
    check("長さが違えば読まない", jailbreak.serve_args([3, "x", "y"]) is None)
    check("並びでなければ読まない", jailbreak.serve_args(None) is None)


def scene_extend():
    print("延ばす")
    args = (3, "罪状", "出来事", 4)
    check("残り年数と刑期を延ばす", jailbreak.extended(args, 2) == [5, "罪状", "出来事", 6])
    check("0 年なら変わらない", jailbreak.extended(args, 0) == [3, "罪状", "出来事", 4])
    check("負の年数は 0 とみなす", jailbreak.extended(args, -3) == [3, "罪状", "出来事", 4])


def scene_chance():
    print("確率")
    check("能力値 15 で基準", jailbreak.prep_chance(15, 50, 3) == 50)
    check("1点ごとに動く", jailbreak.prep_chance(20, 50, 3) == 65)
    check("読めない能力値は基準", jailbreak.prep_chance(None, 50, 3) == 50)
    check("上限で止まる", jailbreak.prep_chance(60, 50, 3) <= 95)
    check("露見は備えで上がる", jailbreak.detect_chance(0, 10, 5) == 10
          and jailbreak.detect_chance(2, 10, 5) == 20)
    check("露見も上限で止まる", jailbreak.detect_chance(100, 10, 5) <= 95)
    check("負の備えは 0 とみなす", jailbreak.detect_chance(-2, 10, 5) == 10)
    check("看守の額は報酬の割合", jailbreak.bribe_cost(24216, 50) == 12108)
    check("報酬が読めなければ 0", jailbreak.bribe_cost(None, 50) == 0)


def scene_ease():
    print("相手")
    check("備えが無ければそのまま", jailbreak.eased_difficulty(76, 0, 15) == 76)
    check("備え2つで3割弱い（実機の 76 → 53）", jailbreak.eased_difficulty(76, 2, 15) == 53)
    check("下限は2割", jailbreak.eased_difficulty(76, 10, 15) == 15)
    check("1未満にしない", jailbreak.eased_difficulty(1, 5, 15) == 1)
    check("読めない難易度は None", jailbreak.eased_difficulty(None, 2, 15) is None)


def scene_roll():
    print("判定")
    check("露見を先に引く（露見すれば 0）",
          jailbreak.roll_prep(Seq(0.0, 0.0), 3, 5, 95, 10) == (0, "detected"))
    check("露見しなければ実るか引く",
          jailbreak.roll_prep(Seq(0.99, 0.0), 3, 5, 95, 10) == (4, "ok"))
    check("実らなければそのまま",
          jailbreak.roll_prep(Seq(0.99, 0.99), 3, 5, 50, 10) == (3, "fail"))
    check("金で買う備えは乱数を引かずに実る",
          jailbreak.roll_prep(Seq(), 3, 5, None, 90) == (4, "ok"))
    check("上限で止まる", jailbreak.roll_prep(Seq(), 5, 5, None, 0) == (5, "ok"))


def scene_labels():
    print("画面")
    texts = [prep["label"].format(chance=50, detect=10, cost="1,000") for prep in jailbreak.PREPS]
    texts.append(jailbreak.BREAK_LABEL.format(prep=0, max=5))
    texts.append(jailbreak.MENU_LABEL.format(prep=0, max=5))
    check("どの文言も残骸の頭のどれかで始まる",
          all(any(text.startswith(head) for head in jailbreak.LABEL_HEADS) for text in texts),
          texts)
    check("ゲームの「服役する」は残骸の頭に含まれない",
          not any(jailbreak.SERVE_TEXT.startswith(head) for head in jailbreak.LABEL_HEADS))
    check("他の画面にもある「やめる」は残骸の頭に含まれない",
          not any(jailbreak.BACK_LABEL.startswith(head) for head in jailbreak.LABEL_HEADS))


def main():
    scene_args()
    scene_extend()
    scene_chance()
    scene_ease()
    scene_roll()
    scene_labels()
    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
