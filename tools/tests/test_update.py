# -*- coding: utf-8 -*-
"""GUI の更新（gui.py の newer_release / extract_release）をネット抜きで通す。

    python tools/tests/test_update.py

  版の比較 … "v1.11.0" > "1.10.0"、"1.10.0" は "1.9.0" より新しい（文字列比較でない）
  更新内容 … 前に開いた版から今の版までの Release を新しい順に選ぶ。下書き・pre-release は出さない。
             本文の Markdown を行の種類と太字・コードに分ける（太字の中のコードも）。
             一度取れた一覧は控えに残し、今の版が入っている間は取りに行かない
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
# 字の付いたタグ（実在する）で落ちない。数字の部分で比べる。
assert gui._vtuple("v1.9.0a") == gui._vtuple("1.9.0")

# 更新内容に出す版。GitHub の並び（新しい順）のまま渡す。
RELEASES = [{"tag_name": t, "body": "# " + t, "html_url": "u/" + t,
             "draft": False, "prerelease": False}
            for t in ("v1.14.0", "v1.13.0", "v1.12.0", "v1.9.0", "v1.9.0b", "v1.9.0a",
                      "v1.8.0")]
RELEASES.insert(0, {"tag_name": "v1.15.0", "body": "", "draft": True})
RELEASES.insert(0, {"tag_name": "v1.16.0", "body": "", "prerelease": True})


def picked(since, current):
    return [n["tag"] for n in gui.pick_notes(RELEASES, since, current)]


assert picked("1.12.0", "1.14.0") == ["v1.14.0", "v1.13.0"]        # 飛ばした間も出す
assert picked("1.8.0", "1.9.0") == ["v1.9.0", "v1.9.0b", "v1.9.0a"]
assert picked(None, "1.13.0") == ["v1.13.0"]                        # 前の版を覚えていない
assert picked("1.13.0", "1.13.0") == []
assert picked("1.14.0", "1.16.0") == []                             # 下書き・pre-release は出さない
assert gui.pick_notes(RELEASES, "1.13.0", "1.14.0")[0]["url"] == "u/v1.14.0"
# 見出しの無い本文（v1.7.1 以前に実在する）には版名の見出しを補う。
assert gui.pick_notes([{"tag_name": "v1.7.1", "body": "本文"}], "1.7.0", "1.7.1")[0]["body"] \
    == "# v1.7.1\n\n本文"

# 控え。一度取れたら、今の版が入っている間はネットに出ない。
calls = []


def fake_fetch():
    calls.append(1)
    return [dict(r, assets=["重い"]) for r in RELEASES]


def offline():
    raise OSError("offline")


with tempfile.TemporaryDirectory() as tmp:
    cache = os.path.join(tmp, "out", "release_notes.json")
    got = gui.fetch_notes("1.12.0", "1.14.0", cache=cache, fetch=fake_fetch)
    assert [n["tag"] for n in got] == ["v1.14.0", "v1.13.0"] and len(calls) == 1
    # 2回目は控えから。取りに行けなくても出る。範囲の分（飛ばした間）も控えで足りる
    got = gui.fetch_notes("1.12.0", "1.14.0", cache=cache, fetch=offline)
    assert [n["tag"] for n in got] == ["v1.14.0", "v1.13.0"]
    assert [n["tag"] for n in gui.fetch_notes(None, "1.13.0", cache=cache, fetch=offline)] \
        == ["v1.13.0"]
    assert "assets" not in gui._read_json(cache)[0]               # 要る分だけ残す
    # 控えに無い版（次の版へ上げた後）は取り直して控えを置き換える
    newer = [{"tag_name": "v1.17.0", "body": "# v1.17.0"}] + RELEASES
    got = gui.fetch_notes("1.14.0", "1.17.0", cache=cache, fetch=lambda: newer)
    assert [n["tag"] for n in got] == ["v1.17.0"]
    assert gui._read_json(cache)[0]["tag_name"] == "v1.17.0"
    # 取れなければ例外のまま（呼ぶ側が黙るか知らせるかを決める）。控えも壊さない
    try:
        gui.fetch_notes(None, "1.18.0", cache=cache, fetch=offline)
        raise AssertionError("例外にならない")
    except OSError:
        pass
    assert gui._read_json(cache)[0]["tag_name"] == "v1.17.0"

# 本文の Markdown。行の種類と、行の中の太字・コード。
runs = gui.markdown_runs("# T\n\n## 追加\n\n- **333 装備** 枠\n説明の行\n\n"
                         "地の文 `state\\` です\n\n\n末尾\n"
                         "- **窓口を足しました（`combat`）**")
assert runs == [
    ("T", ("h1",)), ("\n", ("h1",)), ("\n", ("gap",)),
    ("追加", ("h2",)), ("\n", ("h2",)), ("\n", ("gap",)),
    ("・", ("item",)), ("333 装備", ("item", "bold")), (" 枠", ("item",)), ("\n", ("item",)),
    ("説明の行", ("cont",)), ("\n", ("cont",)), ("\n", ("gap",)),
    ("地の文 ", ("para",)), ("state\\", ("para", "code")), (" です", ("para",)),
    ("\n", ("para",)),
    ("\n", ("gap",)),                                                # 空行が続いても1つ
    ("末尾", ("para",)), ("\n", ("para",)),
    # 太字の中のコード。バッククォートを字のまま残さない
    ("・", ("item",)), ("窓口を足しました（", ("item", "bold")),
    ("combat", ("item", "bold", "code")), ("）", ("item", "bold")), ("\n", ("item",)),
], runs

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
