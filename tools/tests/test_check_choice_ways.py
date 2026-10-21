# -*- coding: utf-8 -*-
"""`tools/check_mods.py` の `check_choice_ways`（選択肢を自分で包む・窓口の古い口。TECH.md §2.3）をゲーム抜きで通す。

    python tools/tests/test_check_choice_ways.py

  包み   … ctx.wrap の対象の文字列・包む関数へ渡す文字列・unwrap で素の組み直しを引く呼び出しを拾う
  古い口 … choices.provide の presses= / trim= を拾い、宣言型の口と refresh / after / intercept は通す
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))

import check_mods  # noqa: E402

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


TMP = tempfile.mkdtemp(prefix="check_choice_ways_")


def found(source):
    path = os.path.join(TMP, "mod_{}.py".format(len(os.listdir(TMP))))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    return [message for _path, _where, message in check_mods.check_choice_ways(path)]


print("[包み]")
check("ctx.wrap で組み直しを包むと拾う", len(found(
    'def apply(ctx):\n'
    '    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)\n'
    '    def refresh(orig, self, *args):\n'
    '        return orig(self, *args)\n')) == 1)
check("包む関数へ渡す文字列でも拾う（押下）", len(found(
    'def apply(ctx):\n'
    '    hook("__main__:InstantaleApp.on_button_press", "press")\n')) == 1)
check("unwrap で素の組み直しを引くと拾う", len(found(
    'from instantale_modloader import patch\n'
    'def f(app):\n'
    '    raw = patch.unwrap(type(app).refresh_choice_buttons)[0]\n')) == 1)
check("ほかの関数の包みは拾わない", found(
    'def apply(ctx):\n'
    '    @ctx.wrap("__main__:InstantaleApp.save_game", required=False)\n'
    '    def save(orig, self):\n'
    '        return orig(self)\n') == [])

print("[古い口]")
messages = found(
    'from instantale_modloader import choices\n'
    'def apply(ctx):\n'
    '    choices.provide(ctx, None, refresh=f, presses={"x": g}, trim=h)\n')
check("presses= と trim= をそれぞれ拾う", len(messages) == 2 and "presses=" in messages[0]
      and "trim=" in messages[1], messages)
check("宣言型の口と refresh / after / intercept は通す", found(
    'from instantale_modloader import choices\n'
    'def apply(ctx):\n'
    '    choices.provide(ctx, None, refresh=f, after=g, intercept=h, offer=i, screens={"a": j}, claim=k)\n') == [])

shutil.rmtree(TMP, ignore_errors=True)
print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
