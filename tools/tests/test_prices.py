# -*- coding: utf-8 -*-
"""`instantale_modloader.prices`（ゲームが決めている値段の窓口）。

    python tools/tests/test_prices.py

置く側と読む側が互いの名前を知らずに繋がること、
誰も置いていなければゲームの値が答えになること、
知らない部屋では「分からない」（None）を返して 0 と混ぜないこと、
壊れた答えはゲームの値へ落ちること、
片付けが `durations.forget` の1本で済むことを見る。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))

from instantale_modloader import durations, prices  # noqa: E402


def check(label, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", label,
                              "" if cond else "  <- {!r}".format(detail)))
    return bool(cond)


def main():
    ok = True
    app = types.SimpleNamespace(player=types.SimpleNamespace(gold=500, age=25))
    logs = []

    print("[ゲームの値]")
    ok &= check("個室は 100", prices.inn_room(app, "private_room") == 100)
    ok &= check("犬小屋は 0", prices.inn_room(app, "kennel") == 0)
    ok &= check("高級個室は 1000", prices.inn_room(app, "luxury_suite") == 1000)
    ok &= check("知らない部屋は None（0 と混ぜない）",
                prices.inn_room(app, "tent") is None)
    ok &= check("誰も置いていなければ持ち主は空",
                prices.source_of(prices.INN_ROOM) == "")

    print("[置いた側が勝つ]")

    def room(app, quality=None):
        return {"price": 7} if quality == "private_room" else None

    prices.declare(prices.INN_ROOM, room, owner="315_test", write=logs.append)
    ok &= check("置いた額が返る", prices.inn_room(app, "private_room") == 7)
    ok &= check("None を返した部屋はゲームの値",
                prices.inn_room(app, "bunk") == 10)
    ok &= check("持ち主を名乗る", prices.source_of(prices.INN_ROOM) == "315_test")

    print("[壊れた答えはゲームの値へ落ちる]")
    prices.declare(prices.INN_ROOM, lambda app, quality=None: {"price": "たくさん"},
                   owner="broken")
    logs[:] = []
    ok &= check("数でなければゲームの値",
                prices.inn_room(app, "private_room", write=logs.append) == 100)
    ok &= check("そのとき WARN が出る",
                any("WARN prices" in line for line in logs), logs)

    prices.declare(prices.INN_ROOM, lambda app, quality=None: 1 / 0, owner="raises")
    logs[:] = []
    ok &= check("例外を投げてもゲームの値",
                prices.inn_room(app, "private_room", write=logs.append) == 100)

    prices.declare(prices.INN_ROOM, lambda app, quality=None: {"price": -5},
                   owner="minus")
    ok &= check("負の額は 0 に均す", prices.inn_room(app, "private_room") == 0)

    print("[片付け]")
    durations.forget("minus")
    ok &= check("外せばゲームの値に戻る",
                prices.inn_room(app, "private_room") == 100
                and prices.source_of(prices.INN_ROOM) == "")

    def stay(app):
        return {"months": 1, "days": 7, "length": "1週間"}

    durations.declare(durations.INN_STAY, stay, owner="315_test")
    prices.declare(prices.INN_ROOM, room, owner="315_test")
    durations.forget("315_test")
    ok &= check("期間と値段が同じ forget で外れる",
                prices.source_of(prices.INN_ROOM) == ""
                and durations.source_of(durations.INN_STAY) == "")

    print("all ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
