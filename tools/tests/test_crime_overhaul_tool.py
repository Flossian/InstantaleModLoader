# -*- coding: utf-8 -*-
"""336_crime_overhaul の設定画面（tool.py）を窓抜きで通す。

    python tools/tests/test_crime_overhaul_tool.py

  振り分け … 宣言の設定がどれも1つの機能にだけ入り、漏れが無い。入切は機能の先頭
  状態     … 入切のある機能はその値、裁判（入切が無い）は中の入切の立ち方で ON / 一部 / OFF
  変更     … 既定から変えた項目の数。数として同じ値（"5" と "5.0"）は変えていない、打ちかけは変えた
  保存     … 画面の値（文字列）を `coerce_all` がそのまま型に戻せる
  宣言     … mod.json に tool が宣言され、窓を組む口が在る（開くのは check_tool_screens）
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
RUNTIME_DIR = os.path.join(ROOT_DIR, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
MOD_DIR = [os.path.join(MODS_DIR, n) for n in os.listdir(MODS_DIR) if n.endswith("_crime_overhaul")][0]

for path in (RUNTIME_DIR, os.path.join(ROOT_DIR, "tools")):
    if path not in sys.path:
        sys.path.insert(0, path)

import modtool  # noqa: E402

spec = importlib.util.spec_from_file_location("crime_overhaul_tool_under_test", os.path.join(MOD_DIR, "tool.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


found = modtool.decls(MOD_DIR, ROOT_DIR)
grouped = tool.group_keys(found)
defaults = dict((k, d["default"]) for k, d in found.items())
by_key = dict((g[0], g) for g in tool.GROUPS)

print("[振り分け]")
placed = [key for keys in grouped.values() for key in keys]
check("宣言の設定がどれもちょうど1つの機能に入る",
      sorted(placed) == sorted(found) and len(placed) == len(set(placed)),
      sorted(set(found) - set(placed)))
check("どの機能にも入らない設定は無い", tool.orphans(found) == [], tool.orphans(found))
check("どの機能にも項目が在る", all(grouped[g[0]] for g in tool.GROUPS),
      [g[0] for g in tool.GROUPS if not grouped[g[0]]])
check("入切は機能の先頭（裁判を除く）",
      all(grouped[g[0]][0] == g[2] for g in tool.GROUPS if g[2]),
      [(g[0], grouped[g[0]][0]) for g in tool.GROUPS if g[2] and grouped[g[0]][0] != g[2]])
check("入切の設定名は宣言に在る真偽",
      all(found.get(g[2], {}).get("type") == "bool" for g in tool.GROUPS if g[2]))
check("盗品の値は店で盗むの機能に入る", "STOLEN_SELL_PCT" in grouped["theft"], grouped["theft"])
check("脱獄で下がる手配度は脱獄、脱出で下がる手配度は脱出に入る",
      "JAILBREAK_LOSS" in grouped["jailbreak"] and "RESCUE_LOSS" in grouped["rescue"])
check("時効の知らせは時効に入る（宣言の最後に在っても）", "STATUTE_NOTICE" in grouped["statute"])
check("衛兵の買収と裁判の袖の下は別の機能",
      all(k.startswith("GUARD_BRIBE_") for k in grouped["guard"])
      and not any(k.startswith("GUARD_") for k in grouped["trial"]))

print("[状態]")
values = dict((k, str(v) if not isinstance(v, bool) else v) for k, v in defaults.items())
check("既定では全部 ON",
      all(tool.group_state(g, grouped[g[0]], found, values) == "ON" for g in tool.GROUPS))
off = dict(values, LOOT_ENABLED=False)
check("入切を切ると OFF", tool.group_state(by_key["loot"], grouped["loot"], found, off) == "OFF")
check("入切以外の真偽は状態に効かない",
      tool.group_state(by_key["loot"], grouped["loot"], found, dict(values, LOOT_NOTICE=False)) == "ON")
partial = dict(values, TRIAL_LAWYER_ENABLED=False)
check("裁判は手を1つ切ると一部", tool.group_state(by_key["trial"], grouped["trial"], found, partial) == "一部")
none = dict(values, **dict((k, False) for k in grouped["trial"] if found[k]["type"] == "bool"))
check("裁判は手を全部切ると OFF", tool.group_state(by_key["trial"], grouped["trial"], found, none) == "OFF")
check("画面の文字列の真偽も読める",
      tool.group_state(by_key["loot"], grouped["loot"], found, dict(values, LOOT_ENABLED="0")) == "OFF")

print("[変更]")
check("既定のままなら 0", all(tool.changed_count(grouped[g[0]], found, values) == 0 for g in tool.GROUPS))
check("値を1つ変えると 1",
      tool.changed_count(grouped["loot"], found, dict(values, LOOT_SHARE_PETTY="7")) == 1)
check("数として同じなら変えていない（5 と 5.0）",
      tool.changed_count(grouped["loot"], found, dict(values, LOOT_SHARE_PETTY="5.0")) == 0)
check("打ちかけ（空）は変えたと数える",
      tool.changed_count(grouped["loot"], found, dict(values, LOOT_SHARE_PETTY="")) == 1)
check("入切を切るのも1つ", tool.changed_count(grouped["loot"], found, off) == 1)

print("[保存]")
coerced, bad = modtool.coerce_all(MOD_DIR, dict(values, LOOT_SHARE_PETTY="7", LOOT_ENABLED=False), ROOT_DIR)
check("画面の値を型に戻せる", not bad and coerced["LOOT_SHARE_PETTY"] == 7 and coerced["LOOT_ENABLED"] is False,
      bad)

print("[宣言]")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    tool_decl = json.load(fh).get("tool")
check("mod.json に tool が宣言され、入口が在る",
      bool(tool_decl) and tool_decl.get("entry") == "tool.py"
      and os.path.isfile(os.path.join(MOD_DIR, "tool.py")), tool_decl)
check("窓を組む口が在る（開くのは check_tool_screens）", callable(getattr(tool, "build_window", None)))

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
