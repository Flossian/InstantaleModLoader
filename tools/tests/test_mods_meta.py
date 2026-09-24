# -*- coding: utf-8 -*-
"""`mod.json` の author の読み方（`tools/mods_meta.py`）を偽の mods フォルダで通す。

    python tools/tests/test_mods_meta.py

  人名   … カンマ区切り。空白を落とす。書いていない・文字列でないなら空
  種別   … 先頭の名前が出どころ。他人だけなら提供（given）、他人が先で SELF が続けば共同（shared）、
           SELF が先なら提案（proposed）。SELF だけ・雛形の見本の名前だけは提供ではない
  並び   … contributed は渡された順を保つ。names_of は出てきた順に重複なく
  読めない mod.json … 空として扱う（名乗りは任意なので止めない）
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import mods_meta as M                                  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


SELF = M.SELF

print("[人名]")
check("カンマ区切りで空白を落とす",
      M.authors({"author": " A ,B,, " + SELF}) == ["A", "B", SELF])
check("書いていなければ空", M.authors({}) == [])
check("文字列でなければ空", M.authors({"author": ["A"]}) == [])
check("SELF と見本の名前は提供者に数えない",
      M.contributors({"author": SELF + ", " + M.PLACEHOLDER_AUTHORS[0]}) == [])

print("[種別]")
for label, author, want in (("他人だけは提供", "A", M.GIVEN),
                            ("他人が先で SELF が続けば共同", "A, " + SELF, M.SHARED),
                            ("SELF が先なら提案", SELF + ", A", M.PROPOSED),
                            ("SELF だけは提供ではない", SELF, ""),
                            ("書いていなければ提供ではない", None, "")):
    data = {} if author is None else {"author": author}
    check(label, M.credit_kind(data) == want, M.credit_kind(data))

print("[並び]")
with tempfile.TemporaryDirectory() as mods:
    for folder, author in (("103_c", "B"), ("101_a", "A, " + SELF), ("102_b", SELF),
                           ("104_d", SELF + ", A")):
        os.makedirs(os.path.join(mods, folder))
        with io.open(os.path.join(mods, folder, "mod.json"), "w", encoding="utf-8") as fh:
            json.dump({"author": author}, fh)
    os.makedirs(os.path.join(mods, "105_broken"))
    with io.open(os.path.join(mods, "105_broken", "mod.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    rows = M.contributed(["103_c", "101_a", "102_b", "104_d", "105_broken", "106_none"], mods)
    check("渡された順を保ち、提供でないものは外す",
          rows == [("103_c", ["B"], M.GIVEN), ("101_a", ["A"], M.SHARED),
                   ("104_d", ["A"], M.PROPOSED)], rows)
    check("names_of は出てきた順に重複なく", M.names_of(rows) == ["B", "A"], M.names_of(rows))
    check("読めない mod.json は空", M.manifest("105_broken", mods) == {})
check("敬称を付けて・で繋ぐ", M.credit(["A", "B"]) == "A " + M.HONORIFIC + "・B " + M.HONORIFIC)

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
