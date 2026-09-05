# -*- coding: utf-8 -*-
"""GUI の更新（gui.py の newer_release / extract_release）をネット抜きで通す。

    python tools/tests/test_update.py

  版の比較 … "v1.11.0" > "1.10.0"、"1.10.0" は "1.9.0" より新しい（文字列比較でない）
  展開     … 頭一段を剥がして上書きする。zip に無いファイルは残る。".." は書かない
"""
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))
import gui  # noqa: E402

assert gui._vtuple("v1.11.0") > gui._vtuple("1.10.0")
assert gui._vtuple("1.10.0") > gui._vtuple("1.9.0")

with tempfile.TemporaryDirectory() as tmp:
    z = os.path.join(tmp, "full.zip")
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("InstantaleModLoader-9.9.9/", "")
        f.writestr("InstantaleModLoader-9.9.9/tools/gui.py", "new")
        f.writestr("InstantaleModLoader-9.9.9/runtime/mods/999_x/mod.json", "{}")
        f.writestr("InstantaleModLoader-9.9.9/../evil.txt", "x")
    dest = os.path.join(tmp, "dest")
    os.makedirs(os.path.join(dest, "runtime", "mods", "900_mine"))
    with open(os.path.join(dest, "runtime", "mods", "900_mine", "mod.json"), "w") as f:
        f.write("mine")
    os.makedirs(os.path.join(dest, "tools"))
    with open(os.path.join(dest, "tools", "gui.py"), "w") as f:
        f.write("old")

    assert gui.extract_release(z, dest) == 2
    assert open(os.path.join(dest, "tools", "gui.py")).read() == "new"
    assert open(os.path.join(dest, "runtime", "mods", "999_x", "mod.json")).read() == "{}"
    assert open(os.path.join(dest, "runtime", "mods", "900_mine", "mod.json")).read() == "mine"
    assert not os.path.exists(os.path.join(tmp, "evil.txt"))
    assert not os.path.exists(os.path.join(dest, "evil.txt"))

print("ok")
