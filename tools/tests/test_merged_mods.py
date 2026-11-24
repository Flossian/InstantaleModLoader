# -*- coding: utf-8 -*-
"""ローダの `merge_settings`（別の MOD に取り込んだ MOD の設定を、取り込んだ MOD へ移す）をゲーム抜きで通す。

    python tools/tests/test_merged_mods.py

仮の配布フォルダ（`runtime/mods`・`settings`）を作り、次を確認する。

  移す     … 古い名前の値が新しい名前へ移り、古い MOD の項は設定から消える。表に無い名前は移さない
  入切     … 順序ファイルで古い MOD を切っていたら、取り込んだ機能の入切を切る
  上書き   … 取り込んだ MOD の側で既に選んでいる値は上書きしない
  1回だけ  … 済んだ名前を `loader.json` に残し、2回目は何もしない。後から古い MOD を切っても入切は動かない
  守り     … 取り込んだ MOD が無い・古いフォルダが取り込む前の版（`superseded` が違う）なら何もしない
  入口     … `drop_renamed` の中で移す（戻りには含めない。フォルダは消さない）
  表       … ローダの表の新しい名前が、取り込んだ MOD の `mod.json` に宣言されている。外した設定（None）は捨てる
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402

INTO = "336_crime_overhaul"
HUNTER = "316_bounty_hunter"
PARDON = "309_office_pardon"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + repr(detail)) if not cond else ""))
    if not cond:
        failures.append(name)


def make_root(superseded=INTO, with_into=True, store=None, disabled=()):
    """仮の配布フォルダ。`(root, mods)` を返す。"""
    root = tempfile.mkdtemp(prefix="iml_merged_")
    mods = os.path.join(root, "runtime", "mods")
    names = [HUNTER, PARDON] + ([INTO] if with_into else [])
    for name in names:
        os.makedirs(os.path.join(mods, name))
        manifest = {"entry": "x.py"}
        if name != INTO and superseded is not None:
            manifest["superseded"] = superseded
        ml.write_json(os.path.join(mods, name, "mod.json"), manifest)
    ml.write_json(os.path.join(mods, ml.ORDER_NAME), {"order": names, "disabled": list(disabled)})
    os.makedirs(os.path.join(root, "settings"))
    if store is not None:
        ml.write_json(os.path.join(root, "settings", "mod_settings.json"), store)
    return root, mods


def read(root, name):
    path = os.path.join(root, "settings", name)
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    print("[移す]")
    root, mods = make_root(store={
        HUNTER: {"COOLDOWN_DAYS": 30, "CHANCE_PERCENT": 50, "NOT_IN_TABLE": 1},
        PARDON: {"PRICE_PER_POINT": 500, "WANTED_THRESHOLD": 5},
        INTO: {"LOOT_ENABLED": False, "HUNTER_START_TOTAL": 60},
        "100_keep": {"X": 1},
    }, disabled=[PARDON])
    try:
        moved = ml.merge_settings(mods)
        check("移した古い MOD の名前を返す", sorted(moved) == [PARDON, HUNTER], moved)
        store = read(root, "mod_settings.json")
        into = store.get(INTO, {})
        check("古い名前の値が新しい名前へ移る",
              into.get("HUNTER_COOLDOWN_DAYS") == 30 and into.get("HUNTER_CHANCE_PCT") == 50
              and into.get("PARDON_PRICE_PER_POINT") == 500, into)
        check("表に無い名前は移さない", "NOT_IN_TABLE" not in into, into)
        check("外した設定（新しい名前が None）は移さずに捨てる",
              "WANTED_THRESHOLD" not in into and "PARDON_WANTED_THRESHOLD" not in into, into)
        check("取り込んだ MOD の既存の値は残る", into.get("LOOT_ENABLED") is False, into)
        check("古い MOD の項は設定から消える", HUNTER not in store and PARDON not in store, sorted(store))
        check("ほかの MOD の設定は残る", store.get("100_keep") == {"X": 1}, store)
        check("切っていた古い MOD は、取り込んだ機能の入切を切る", into.get("PARDON_ENABLED") is False, into)
        check("切っていなかった古い MOD の入切は書かない（既定のまま）", "HUNTER_ENABLED" not in into, into)
        flags = read(root, "loader.json") or {}
        check("済んだ名前を loader.json に残す",
              sorted(flags.get(ml.MERGED_FLAG) or []) == [PARDON, HUNTER], flags)

        print("[1回だけ]")
        order_path = os.path.join(mods, ml.ORDER_NAME)
        ml.write_json(order_path, {"order": [], "disabled": [HUNTER, PARDON]})
        store[INTO].pop("PARDON_ENABLED")
        ml.write_json(os.path.join(root, "settings", "mod_settings.json"), store)
        check("2回目は何もしない", ml.merge_settings(mods) == [])
        store = read(root, "mod_settings.json")
        check("後から古い MOD を切っても、取り込んだ機能の入切は動かない",
              "HUNTER_ENABLED" not in store[INTO] and "PARDON_ENABLED" not in store[INTO], store[INTO])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("[上書き]")
    root, mods = make_root(store={HUNTER: {"COOLDOWN_DAYS": 30}, INTO: {"HUNTER_COOLDOWN_DAYS": 5}},
                           disabled=[HUNTER])
    try:
        os.makedirs(os.path.join(root, "settings"), exist_ok=True)
        store = dict(read(root, "mod_settings.json"))
        store[INTO]["HUNTER_ENABLED"] = True
        ml.write_json(os.path.join(root, "settings", "mod_settings.json"), store)
        ml.merge_settings(mods)
        into = read(root, "mod_settings.json")[INTO]
        check("取り込んだ MOD の側で選んだ値は上書きしない", into.get("HUNTER_COOLDOWN_DAYS") == 5, into)
        check("入切も、選んであれば上書きしない", into.get("HUNTER_ENABLED") is True, into)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("[守り]")
    root, mods = make_root(superseded=None, store={HUNTER: {"COOLDOWN_DAYS": 30}})
    try:
        check("古いフォルダが取り込む前の版なら何もしない", ml.merge_settings(mods) == [])
        check("そのときは設定も触らない",
              read(root, "mod_settings.json") == {HUNTER: {"COOLDOWN_DAYS": 30}}, read(root, "mod_settings.json"))
        check("済んだ印も付けない", read(root, "loader.json") is None, read(root, "loader.json"))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    root, mods = make_root(with_into=False, store={HUNTER: {"COOLDOWN_DAYS": 30}})
    try:
        check("取り込んだ MOD が無ければ何もしない", ml.merge_settings(mods) == [])
    finally:
        shutil.rmtree(root, ignore_errors=True)
    root, mods = make_root(store=None)
    try:
        shutil.rmtree(os.path.join(mods, HUNTER))
        moved = ml.merge_settings(mods)
        check("古いフォルダが無くても、設定が無ければ印だけ付けて済ませる",
              sorted(moved) == [PARDON, HUNTER] and read(root, "mod_settings.json") is None,
              (moved, read(root, "mod_settings.json")))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    root, mods = make_root(store=None)
    try:
        with io.open(os.path.join(root, "settings", "mod_settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{ broken")
        check("設定ファイルが読めなければ移さず、印も付けない",
              ml.merge_settings(mods) == [] and read(root, "loader.json") is None)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("[入口]")
    root, mods = make_root(store={HUNTER: {"COOLDOWN_DAYS": 30}})
    try:
        dropped = ml.drop_renamed(mods)
        check("drop_renamed の戻りには含めない", HUNTER not in dropped and PARDON not in dropped, dropped)
        check("フォルダは消さない", os.path.isdir(os.path.join(mods, HUNTER)))
        check("drop_renamed の中で移す",
              read(root, "mod_settings.json") == {INTO: {"HUNTER_COOLDOWN_DAYS": 30}},
              read(root, "mod_settings.json"))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("[3本を1本へ（348）]")
    party = ("304_quest_end_keep_party", "303_quest_end_party_to_guild", "302_leave_party_in_conversation")
    party_into = "348_party_departure"

    def party_root(store, disabled=()):
        root = tempfile.mkdtemp(prefix="iml_merged_party_")
        mods = os.path.join(root, "runtime", "mods")
        for name in party + (party_into,):
            os.makedirs(os.path.join(mods, name))
            manifest = {"entry": "x.py"}
            if name != party_into:
                manifest["superseded"] = party_into
            ml.write_json(os.path.join(mods, name, "mod.json"), manifest)
        ml.write_json(os.path.join(mods, ml.ORDER_NAME), {"order": list(party), "disabled": list(disabled)})
        os.makedirs(os.path.join(root, "settings"))
        ml.write_json(os.path.join(root, "settings", "mod_settings.json"), store)
        return root, mods

    root, mods = party_root({party[2]: {"RETURN_TO_INITIAL_LOCATION": True},
                             party[1]: {"ALSO_ON_QUEST_RETIRE": True},
                             party[0]: {"ALSO_ON_QUEST_RETIRE": False}}, disabled=[party[1]])
    try:
        check("3本とも移す", sorted(ml.merge_settings(mods)) == sorted(party))
        into = read(root, "mod_settings.json").get(party_into, {})
        check("303 を切っていたら、302 の値によらず「近くに残す」は切", into.get("STAY_NEARBY") is False, into)
        check("放棄の扱いは先に移す 304 の値が残る", into.get("ALSO_ON_QUEST_RETIRE") is False, into)
        check("切っていなかった 304・302 の入切は書かない",
              "KEEP_PARTY" not in into and "FAREWELL_ENABLED" not in into, into)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    root, mods = party_root({party[2]: {"RETURN_TO_INITIAL_LOCATION": False}})
    try:
        ml.merge_settings(mods)
        into = read(root, "mod_settings.json").get(party_into, {})
        check("303 が入っていれば、302 の「雇った場所に戻す」の値が「近くに残す」になる",
              into.get("STAY_NEARBY") is False, into)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("[表]")
    for old, (into, switch, renames) in ml.MERGED_MODS.items():
        with io.open(os.path.join(MODS_DIR, into, "mod.json"), encoding="utf-8") as fh:
            declared = json.load(fh).get("settings", {})
        with io.open(os.path.join(MODS_DIR, old, "mod.json"), encoding="utf-8") as fh:
            old_manifest = json.load(fh)
        missing = [name for name in [new for new in renames.values() if new] + [switch] if name not in declared]
        check("{} の新しい名前と入切が {} に宣言されている".format(old, into), not missing, missing)
        unknown = [name for name in renames if name not in old_manifest.get("settings", {})]
        check("{} の古い名前がどれも古い mod.json に在る".format(old), not unknown, unknown)
        left = [name for name in old_manifest.get("settings", {}) if name not in renames]
        check("{} の設定は全部移す先がある".format(old), not left, left)
        check("{} は {} を取り込み先として伏せてある".format(old, into),
              old_manifest.get("superseded") == into, old_manifest.get("superseded"))
        same = [name for name in renames if renames[name]
                and old_manifest["settings"][name].get("default") != declared.get(renames[name], {}).get("default")]
        check("{} の既定値が移す先と同じ".format(old), not same, same)

    print()
    if failures:
        print("失敗 {} 件".format(len(failures)))
        for name in failures:
            print("  - " + name)
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
