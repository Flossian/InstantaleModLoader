# -*- coding: utf-8 -*-
"""`mod.json` の "state"（GUI の行の右クリック「state/ を開く」が開く先）をゲーム抜きで通す。

    python tools/tests/test_state_decl.py

  均し … 文字列1つも並びで受け、`\\` は `/` に直し、重複を落とす。外へ出るもの（絶対・ドライブ・..）は捨てる
  検査 … 並びでない・外へ出る宣言、ソースに無い名前を check_mods が問題に数える。`("musics", "battle")` の形は通す
  GUI  … state_targets は在るものだけを返す
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))

import check_mods  # noqa: E402
import gui  # noqa: E402
import instantale_modloader as ml  # noqa: E402

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


print("[均し]")
check("文字列1つ", ml._state_decl("m", "npc_profiles") == ["npc_profiles"])
check("区切りを / に", ml._state_decl("m", ["musics\\battle", "musics/place/"]) == ["musics/battle", "musics/place"])
check("重複を落とす", ml._state_decl("m", ["a", "a/", "a"]) == ["a"])
check("外へ出るものを捨てる",
      ml._state_decl("m", ["../out", "/abs", "C:/x", "", ".", 3, "ok.json"]) == ["ok.json"])
check("並びでなければ空", ml._state_decl("m", {"a": 1}) == [] and ml._state_decl("m", None) == [])

print("[検査]")
TMP = tempfile.mkdtemp(prefix="state_decl_")


def problems(state, source):
    name = "999_probe_{}".format(len(os.listdir(TMP)))
    folder = os.path.join(TMP, name)
    os.makedirs(folder)
    with io.open(os.path.join(folder, "mod.json"), "w", encoding="utf-8") as fh:
        json.dump({"entry": "mod.py", "api": 1, "kind": "feature", "state": state}, fh)
    with io.open(os.path.join(folder, "mod.py"), "w", encoding="utf-8") as fh:
        fh.write(source)
    manifest = ml._manifest(TMP, name)
    found, _notes = check_mods.check_manifest(name, manifest)
    return [message for _path, _where, message in found if '"state"' in message]


saved = check_mods.MODS_DIR
check_mods.MODS_DIR = TMP
try:
    check("ソースに在る名前は通す", problems(["npc_profiles"], 'DIR = "npc_profiles"\n') == [])
    check("段に分けた形も通す", problems(["musics/battle"], 'SUB = ("musics", "battle")\n') == [])
    check("ファイル名も通す", problems(["road_travel.json"], "NAME = 'road_travel.json'\n") == [])
    got = problems(["npc_profile"], 'DIR = "npc_profiles"\n')
    check("打ち間違いを拾う", len(got) == 1 and "npc_profile" in got[0], got)
    got = problems(["../out"], 'X = ".."\n')
    check("外へ出る宣言を拾う", len(got) == 1, got)
    got = problems({"a": 1}, "")
    check("並びでない宣言を拾う", len(got) == 1, got)
finally:
    check_mods.MODS_DIR = saved
    shutil.rmtree(TMP, ignore_errors=True)

print("[GUI]")
STATE = tempfile.mkdtemp(prefix="state_targets_")
saved = gui.STATE_DIR
gui.STATE_DIR = STATE
try:
    os.makedirs(os.path.join(STATE, "musics", "battle"))
    with io.open(os.path.join(STATE, "clients.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    got = gui.state_targets({"state": ["musics/battle", "clients.json", "not_yet"]})
    check("在るものだけ", [rel for rel, _path in got] == ["musics/battle", "clients.json"], got)
    check("実パスは state/ の下", got and got[0][1] == os.path.join(STATE, "musics", "battle"), got)
    check("宣言が無ければ空", gui.state_targets({}) == [])
finally:
    gui.STATE_DIR = saved
    shutil.rmtree(STATE, ignore_errors=True)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
