# -*- coding: utf-8 -*-
"""105_fix_schema_compact をゲーム抜きで通す。

    python tools/tests/test_schema_compact.py

  一覧   … pydantic の形（$defs / $ref / enum / 配列 / Optional）が2行の一覧になる
  無加工 … スキーマの無い文・壊れた dict は None（触らない）
  べき等 … 圧縮後をもう一度通しても None
  両表記 … JSON 表記（true/null・ダブルクォート）でも同じ一覧が出る
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


spec = importlib.util.spec_from_file_location("fix_schema_compact", find_mod("_fix_schema_compact"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


PRE, POST = "出力は次の構造に従うこと: ", "\n以上を守ること。"

# 一覧
result = mod.compact_embedded_schema(PRE + mod._SAMPLE_SCHEMA + POST)
check("compacts sample", result is not None)
compacted, listing = result or ("", "")
check("listing text", listing == mod._EXPECTED, listing)
check("surroundings kept", compacted == PRE + mod._EXPECTED + POST, compacted)

# 無加工
check("no schema -> None", mod.compact_embedded_schema("自由に書いてよい。") is None)
check("broken dict -> None", mod.compact_embedded_schema("{'$defs': {'A': ") is None)

# べき等
check("idempotent", mod.compact_embedded_schema(compacted) is None)

# 両表記
as_json = json.dumps(mod.parse_literal(mod._SAMPLE_SCHEMA, 0)[0], ensure_ascii=False)
check("json spelling", mod.compact_embedded_schema(PRE + as_json + POST) == result)

# 型の書き出し
check("const", mod.describe_field({"const": "x"}) == '="x"')
check("string[]", mod.describe_field({"type": "array", "items": {"type": "string"}}) == "string[]")
check("union?", mod.describe_field({"anyOf": [{"$ref": "#/$defs/A"}, {"enum": [1, 2]}, {"type": "null"}]})
      == "A|∈{1,2}?")

if failures:
    raise SystemExit("FAILED: {}".format(failures))
print("all ok")
