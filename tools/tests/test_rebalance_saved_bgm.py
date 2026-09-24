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

    xored = os.path.join(tmp, "world_data.json")
    with open(xored, "wb") as fh:
        fh.write(R.encode(world()))
    data, obfuscated, round_trip = R.read_world(xored)
    check("難読化を見分けて往復が一致する", obfuscated and round_trip, (obfuscated, round_trip))
    with contextlib.redirect_stdout(io.StringIO()):
        R.process(xored, policy, POOL, True)
    data, obfuscated, _ = R.read_world(xored)
    check("難読化されていたものは難読化して書き戻す",
          obfuscated and data["areas"]["2"]["bgm"] != SAME, data)

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
