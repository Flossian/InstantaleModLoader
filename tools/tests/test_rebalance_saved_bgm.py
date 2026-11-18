# -*- coding: utf-8 -*-
"""既存セーブの BGM を均す道具（`tools/rebalance_saved_bgm.py`）をゲーム抜きで通す。

    python tools/tests/test_rebalance_saved_bgm.py

  読み込み … import しただけでは MOD を探さない（`--help` が MOD の有無に左右されない）
  探し方   … 番号を除いたフォルダ名で引き、入口は mod.json の "entry" から組む。
             mod.json の無いフォルダは数えない。見つからない・2つ以上は止める
  選び方   … 104_ の入口をそのまま読み込み、同じ関数（balance_world）を使う
  書き込み … --apply が無ければ書かない。書くときは控えを取り、読んだときと同じ形式
             （素の JSON は素の JSON、難読化されていれば難読化）で書き戻す
"""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import rebalance_saved_bgm as R                        # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def exits(fn, *args):
    """SystemExit の code。出なければ None。"""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn(*args)
    except SystemExit as exc:
        return exc.code if exc.code is not None else 0
    return None


print("[探し方]")
path = R.find_mod("_balance_area_bgm")
folder = os.path.dirname(path)
with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
    entry = json.load(fh)["entry"]
check("入口は mod.json の entry", os.path.basename(path) == entry and os.path.isfile(path), path)
check("番号付きのフォルダ", os.path.basename(folder).endswith("_balance_area_bgm"), folder)
with tempfile.TemporaryDirectory() as mods:
    check("見つからなければ止める", isinstance(exits(R.find_mod, "_x", mods), str))
    os.makedirs(os.path.join(mods, "101_x"))
    check("mod.json の無いフォルダは数えない", isinstance(exits(R.find_mod, "_x", mods), str))
    with io.open(os.path.join(mods, "101_x", "mod.json"), "w", encoding="utf-8") as fh:
        json.dump({"entry": "x.py"}, fh)
    check("1つなら入口のパス", R.find_mod("_x", mods) == os.path.join(mods, "101_x", "x.py"))
    os.makedirs(os.path.join(mods, "201_x"))
    with io.open(os.path.join(mods, "201_x", "mod.json"), "w", encoding="utf-8") as fh:
        json.dump({"entry": "x.py"}, fh)
    check("番号違いの同名が2つなら止める", "ambiguous" in str(exits(R.find_mod, "_x", mods)))

print("[読み込み]")
check("--help は MOD を読まずに通る", exits(R.main, ["--help"]) == 0)
policy = R.load_policy()
check("104_ の選び方を借りられる",
      all(hasattr(policy, n) for n in ("balance_world", "scan_pool", "parse_bgm")))

print("[書き込み]")
POOL = {"town": [("calm", "a.mp3"), ("calm", "b.mp3"), ("lively", "c.mp3")]}
SAME = "Assets/sounds/musics/town/calm/a.mp3"


def world():
    return {"areas": {"1": {"size": "town", "bgm": SAME}, "2": {"size": "town", "bgm": SAME}}}


with tempfile.TemporaryDirectory() as tmp:
    plain = os.path.join(tmp, "plain.json")
    with io.open(plain, "w", encoding="utf-8") as fh:
        json.dump(world(), fh)
    before = open(plain, "rb").read()
    with contextlib.redirect_stdout(io.StringIO()):
        wrote = R.process(plain, policy, POOL, False)
    check("--apply が無ければ書かない", wrote is False and open(plain, "rb").read() == before)
    with contextlib.redirect_stdout(io.StringIO()):
        wrote = R.process(plain, policy, POOL, True)
    with io.open(plain, encoding="utf-8") as fh:
        areas = json.load(fh)["areas"]
    check("--apply で書く（素の JSON のまま）", wrote is True and areas["2"]["bgm"] != SAME, areas)
    check("控えを残す", any(n.startswith("plain.json.bak-") for n in os.listdir(tmp)))

    xored = os.path.join(tmp, "savedata.json")
    with open(xored, "wb") as fh:
        fh.write(R.encode(world()))
    data, obfuscated, round_trip = R.read_world(xored)
    check("難読化を見分けて往復が一致する", obfuscated and round_trip, (obfuscated, round_trip))
    with contextlib.redirect_stdout(io.StringIO()):
        R.process(xored, policy, POOL, True)
    data, obfuscated, _ = R.read_world(xored)
    check("難読化されていたものは難読化して書き戻す",
          obfuscated and data["areas"]["2"]["bgm"] != SAME, data)

    # 骨格は MOD も道具も書かない（TECH.md）。渡されても拒む。
    skeleton = os.path.join(tmp, "world_data.json")
    with open(skeleton, "wb") as fh:
        fh.write(R.encode(world()))
    before = open(skeleton, "rb").read()
    with contextlib.redirect_stdout(io.StringIO()):
        wrote = R.process(skeleton, policy, POOL, True)
    check("**world_data.json（骨格）には --apply でも書かない**",
          wrote is False and open(skeleton, "rb").read() == before)

print("[探す先]")
with tempfile.TemporaryDirectory() as tmp:
    # 本物の置き場と同じ形: saves/<フォルダ>/savedata.json と worlds/<世界名>/world_data.json
    for mood, name in (("calm", "a.mp3"), ("calm", "b.mp3"), ("lively", "c.mp3")):
        folder = os.path.join(tmp, "game", "Assets", "sounds", "musics", "town", mood)
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, name), "wb").close()
    save = os.path.join(tmp, "saves", "w1", "savedata.json")
    skeleton = os.path.join(tmp, "worlds", "w1", "world_data.json")
    for path in (save, skeleton):
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as fh:
            fh.write(R.encode(world()))
    skeleton_before = open(skeleton, "rb").read()
    with contextlib.redirect_stdout(io.StringIO()):
        R.main(["--saves", os.path.join(tmp, "saves"), "--game", os.path.join(tmp, "game"),
                "--apply"])
    data, obfuscated, round_trip = R.read_world(save)
    check("**セーブ（savedata.json）の areas[*].bgm を均す**",
          obfuscated and round_trip and data["areas"]["2"]["bgm"] != SAME, data)
    check("骨格には触らない", open(skeleton, "rb").read() == skeleton_before)
    # 前の版の引数（--worlds）でも止まらず、同じ親の saves を読んで警告を出す（外すのは v2.0.0）
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            R.main(["--worlds", os.path.join(tmp, "worlds"), "--game", os.path.join(tmp, "game")])
        stopped = None
    except SystemExit as exc:
        stopped = exc
    check("前の引数 --worlds でも止まらず、隣の saves を読んで警告を出す",
          stopped is None and "--worlds is deprecated" in out.getvalue()
          and os.path.join(tmp, "saves") in out.getvalue(), (stopped, out.getvalue()[-300:]))
    # 本物の標準出力でも警告が残る（StringIO では stdout の差し替えが飛ぶので、別プロセスで見る）
    import subprocess
    got = subprocess.run(
        [sys.executable, os.path.join(TOOLS_DIR, "rebalance_saved_bgm.py"),
         "--worlds", os.path.join(tmp, "worlds"), "--game", os.path.join(tmp, "game")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = got.stdout.decode("utf-8", "replace")
    check("別プロセスの標準出力にも --worlds の警告が出る",
          got.returncode == 0 and "--worlds is deprecated" in text, (got.returncode, text[-300:]))

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
