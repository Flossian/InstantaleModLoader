# -*- coding: utf-8 -*-
"""342_attack_animations の設定画面（tool.py）の見本を、窓抜きで通す。

    python tools/tests/test_attack_animations_tool.py

  入口   … mod.json の "tool" が tool.py を指し、tool.py が在る
  見本   … 描き足した演出は全部、控えめ・はっきりの両方にコマが在り、同じ演出は両方のコマの数が同じ（左右が揃って動く）
  大きさ … 64 角は等倍、128 角（全体の毒）は半分で、どれも 64 角の升に収まる。17種が6行に収まる
  止め絵 … コマの中ほど（頭はほぼ空、最後は空）。コマが無ければ 0
"""
import importlib.util
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
MOD_DIR = [os.path.join(MODS_DIR, n) for n in os.listdir(MODS_DIR) if n.endswith("_attack_animations")][0]

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def png_width(path):
    """PNG の幅（IHDR を読むだけ。画像の部品は使わない）。"""
    with open(path, "rb") as fh:
        head = fh.read(24)
    return struct.unpack(">I", head[16:20])[0]


spec = importlib.util.spec_from_file_location("attack_animations_tool_under_test", os.path.join(MOD_DIR, "tool.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

print("入口")
manifest = json.load(open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8"))
check("mod.json の tool が tool.py を指す", (manifest.get("tool") or {}).get("entry") == "tool.py", manifest.get("tool"))
check("tool.py が在る", os.path.isfile(os.path.join(MOD_DIR, "tool.py")))
check("見え方の値は設定の選択肢と同じ並び",
      [s for s, _n in tool.STYLES] == manifest["settings"]["ART_STYLE"]["values"], tool.STYLES)

print("見本")
rows = tool.sample_rows(MOD_DIR)
added = sorted(name for name in os.listdir(os.path.join(MOD_DIR, "effects", "soft"))
               if os.path.isdir(os.path.join(MOD_DIR, "effects", "soft", name)))
check("描き足した演出は全部並ぶ", sorted(e for e, _l, _f in rows) == added, (len(rows), len(added)))
check("どの演出も両方の見え方にコマが在る", all(all(frames.get(s) for s, _n in tool.STYLES) for _e, _l, frames in rows),
      [e for e, _l, frames in rows if not all(frames.get(s) for s, _n in tool.STYLES)])
check("同じ演出は控えめとはっきりのコマの数が同じ",
      all(len(set(len(frames[s]) for s, _n in tool.STYLES)) == 1 for _e, _l, frames in rows),
      [(e, {s: len(frames[s]) for s, _n in tool.STYLES}) for e, _l, frames in rows])
check("表示名は全部付いている", all(label for _e, label, _f in rows))

print("大きさ")
shown = set(tool.shown_width(png_width(frames["soft"][0])) for _effect, _label, frames in rows)
check("どれも 64 角の升に収まる（大きい升で列が広がらない）", shown == {tool.SHOWN_SIZE} and tool.SHOWN_SIZE == 64, shown)
check("64 角は等倍、128 角（全体の毒）は半分", tool.scale_for(64) == (1, 1) and tool.scale_for(128) == (1, 2)
      and tool.scale_for(32) == (2, 1))
check("17種が6行に収まる（スクロールしない並び）", -(-len(rows) // tool.PER_ROW) == 6, (len(rows), tool.PER_ROW))

print("止め絵")
check("止め絵はコマの中ほど", tool.still_index(11) == 4 and tool.still_index(16) == 7 and tool.still_index(1) == 0)
check("コマが無ければ 0", tool.still_index(0) == 0)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
