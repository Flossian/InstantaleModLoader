# -*- coding: utf-8 -*-
"""GUI の更新（gui.py の newer_release / extract_release）をネット抜きで通す。

    python tools/tests/test_update.py

  版の比較 … "v1.11.0" > "1.10.0"、"1.10.0" は "1.9.0" より新しい（文字列比較でない）
  展開     … 頭一段を剥がして上書きする。zip に無いファイルは残る。".." は書かない
  同梱の既定 … 書き換えた *.default.* は手元の名前に改名して残す（111/120/132）。
             配った版のままなら残さない。SHIPPED_DEFAULTS は git の履歴と一致する
"""
import os
import subprocess
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

# 同梱の既定ファイル（*.default.*）
ROOT = os.path.join(HERE, os.pardir, os.pardir)
def git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True).stdout
defaults = git("ls-files", "runtime/mods/*.default.*").decode().split()
assert len(defaults) == len(gui.SHIPPED_DEFAULTS), (defaults, list(gui.SHIPPED_DEFAULTS))
for rel in defaults:
    name = os.path.basename(rel)
    for rev in git("log", "--format=%H", "--", rel).decode().split():
        h = gui.default_hash(git("show", "{}:{}".format(rev, rel)))
        assert h in gui.SHIPPED_DEFAULTS[name], \
            "配った版が SHIPPED_DEFAULTS に無い: {} {} {}".format(name, rev[:7], h)

assert gui.user_name("npc.default.json") == "npc.json"
assert gui.user_name("llm_replacements.default.txt") == "llm_replacements.txt"
assert gui.user_name("mod.json") is None

RULES = "runtime/mods/111_llm_prompt_replace/llm_replacements.default.txt"
with open(os.path.join(ROOT, RULES), "rb") as f:
    pristine = f.read()

with tempfile.TemporaryDirectory() as tmp:
    z = os.path.join(tmp, "full.zip")
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("InstantaleModLoader-9.9.9/" + RULES, b"new default")
    dest = os.path.join(tmp, "dest")
    mod_dir = os.path.join(dest, os.path.dirname(RULES))
    os.makedirs(mod_dir)
    default = os.path.join(mod_dir, "llm_replacements.default.txt")
    user = os.path.join(mod_dir, "llm_replacements.txt")

    # 配った版のまま（改行が違っても同じと見る）→ 残さない
    with open(default, "wb") as f:
        f.write(pristine.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    gui.extract_release(z, dest)
    assert not os.path.exists(user)
    assert open(default, "rb").read() == b"new default"

    # 書き換えて使っていた → 手元の名前に改名して残し、default は新しい版になる
    edited = pristine + b"\nfoo=>bar\n"
    with open(default, "wb") as f:
        f.write(edited)
    gui.extract_release(z, dest)
    assert open(user, "rb").read() == edited
    assert open(default, "rb").read() == b"new default"

    # 手元のファイルが既に在る → 触らない
    with open(default, "wb") as f:
        f.write(b"edited again")
    gui.extract_release(z, dest)
    assert open(user, "rb").read() == edited
    assert open(default, "rb").read() == b"new default"

print("ok")
