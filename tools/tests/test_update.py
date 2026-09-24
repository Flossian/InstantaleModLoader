# -*- coding: utf-8 -*-
"""GUI の更新（gui.py の newer_release / extract_release）をネット抜きで通す。

    python tools/tests/test_update.py

  版の比較 … "v1.11.0" > "1.10.0"、"1.10.0" は "1.9.0" より新しい（文字列比較でない）
  展開     … 頭一段を剥がして上書きする。zip に無いファイルは残る。".." と別のドライブ名は書かない
  MOD の追加 … install_from_zip も ".." とドライブ名を書かない。mods/ の外を指す mod は断る。
             入れ子の mod.json は外側の mod の中身として写す
  同梱の既定 … 書き換えた *.default.* は手元の名前に改名して残す（111/120/132）。
             配った版のままなら残さない。SHIPPED_DEFAULTS は git の履歴と一致する
  配る tools … make_dist.bat の一覧にあるファイルが読む tools\\ のモジュールも一覧にある
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

# 一時フォルダと別のドライブ名（同じドライブ名なら join は dest の中に留まる）。
OTHER_DRIVE = "R:" if tempfile.gettempdir().upper().startswith("Q:") else "Q:"

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

    # dest と別のドライブ名を持つ要素。`os.path.join(dest, "Q:", ...)` は dest を捨てて
    # そのドライブを指す。`..` を見るだけでは止まらない。
    z2 = os.path.join(tmp, "drive.zip")
    with zipfile.ZipFile(z2, "w") as f:
        f.writestr("InstantaleModLoader-9.9.9/{}/evil_drive.txt".format(OTHER_DRIVE), "x")
        f.writestr("InstantaleModLoader-9.9.9/tools/ok.txt", "ok")
    assert gui.extract_release(z2, dest) == 1
    assert open(os.path.join(dest, "tools", "ok.txt")).read() == "ok"

# MOD の追加（install_from_zip）。展開先は偽の mods/ に差し替える。
with tempfile.TemporaryDirectory() as tmp:
    saved_mods_dir = gui.MODS_DIR
    gui.MODS_DIR = os.path.join(tmp, "mods")
    os.makedirs(gui.MODS_DIR)
    drive = OTHER_DRIVE
    try:
        def zipped(name, entries):
            path = os.path.join(tmp, name)
            with zipfile.ZipFile(path, "w") as f:
                for entry, data in entries:
                    f.writestr(entry, data)
            return path

        # `..` とドライブ名を含む要素は書かない。ほかは入る。
        added = gui.install_from_zip(zipped("slip.zip", [
            ("slip/mod.json", "{}"), ("slip/a.py", "a"),
            ("slip/../../evil.txt", "x"), ("slip/{}/evil_drive.txt".format(drive), "x")]))
        assert added == ["slip"], added
        assert open(os.path.join(gui.MODS_DIR, "slip", "a.py")).read() == "a"
        assert not os.path.exists(os.path.join(tmp, "evil.txt"))
        assert sorted(os.listdir(os.path.join(gui.MODS_DIR, "slip"))) == ["a.py", "mod.json"]

        # mod のフォルダそのものが mods/ の外を指すものは、何も書かずに断る。
        for bad in ("../mod.json", "{}/mod.json".format(drive)):
            try:
                gui.install_from_zip(zipped("bad.zip", [(bad, "{}")]))
            except ValueError:
                pass
            else:
                raise AssertionError("mods/ の外を指す zip を受けた: " + bad)
        assert not os.path.exists(os.path.join(tmp, "mod.json"))

        # 入れ子の mod.json は外側の mod の中身。mods/ 直下に2つ目として置かない。
        added = gui.install_from_zip(zipped("nest.zip", [
            ("outer/mod.json", "{}"), ("outer/sub/mod.json", "{}"), ("outer/sub/x.py", "x")]))
        assert added == ["outer"], added
        assert os.path.isfile(os.path.join(gui.MODS_DIR, "outer", "sub", "x.py"))
        assert not os.path.exists(os.path.join(gui.MODS_DIR, "sub"))
        # 中身だけの zip でも同じ（zip の名前がフォルダ名）。
        added = gui.install_from_zip(zipped("flat.zip", [
            ("mod.json", "{}"), ("sub2/mod.json", "{}")]))
        assert added == ["flat"], added
        assert os.path.isfile(os.path.join(gui.MODS_DIR, "flat", "sub2", "mod.json"))
        assert not os.path.exists(os.path.join(gui.MODS_DIR, "sub2"))
        # 並んだ mod（配布の mods zip の形）はそれぞれ入る。
        added = gui.install_from_zip(zipped("pack.zip", [
            ("InstantaleMods-9.9.9/101_a/mod.json", "{}"),
            ("InstantaleMods-9.9.9/102_b/mod.json", "{}")]))
        assert added == ["101_a", "102_b"], added
    finally:
        gui.MODS_DIR = saved_mods_dir

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

# 配る tools（make_dist.bat の一覧）。
# 一覧は許可制で、書いていない tools\ のファイルは黙って落ちる。
# 配ったファイルが tools\ の別のモジュールを読むなら、それも一覧に要る
# （check_mods.py が mods_meta.py を読むのに入れていなかった）。
import ast  # noqa: E402
import re   # noqa: E402

TOOLS = os.path.join(ROOT, "tools")
with open(os.path.join(ROOT, "make_dist.bat"), encoding="utf-8") as f:
    script = f.read()
block = re.search(r'for %%f in \(([^)]*)\) do \(\s*\n\s*if not exist "tools\\%%f"', script)
assert block, "make_dist.bat の tools の一覧が見つからない"
shipped = set(block.group(1).split())
assert "gui.py" in shipped and "check_mods.py" in shipped, shipped
for name in sorted(shipped):
    assert os.path.isfile(os.path.join(TOOLS, name)), "一覧にあるが tools\\ に無い: " + name


def local_imports(path):
    """そのファイルが import する tools\\ のモジュール（関数の中の import も含む）。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n + ".py" for n in names if os.path.isfile(os.path.join(TOOLS, n + ".py"))}


for name in sorted(shipped):
    path = os.path.join(TOOLS, name)
    if name.endswith(".py"):
        needed = local_imports(path)
    else:
        # .bat が起こす .py（watch.bat → watcher.py）。
        with open(path, encoding="utf-8") as f:
            needed = {m + ".py" for m in re.findall(r"\b(\w+)\.py\b", f.read())
                      if os.path.isfile(os.path.join(TOOLS, m + ".py"))}
    missing = needed - shipped
    assert not missing, "{} が読むのに配らない: {}".format(name, sorted(missing))
with open(os.path.join(ROOT, "InstantaleModLoader.bat"), encoding="utf-8") as f:
    assert "tools\\gui.py" in f.read()

print("ok")
