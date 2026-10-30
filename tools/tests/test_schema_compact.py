# -*- coding: utf-8 -*-
"""105_fix_schema_compact をゲーム抜きで通す。

    python tools/tests/test_schema_compact.py

  骨組み … pydantic の形（$defs / $ref / enum / 配列 / Optional）が2行の骨組みになる
  型と値 … 素の型・null・enum の値・数の範囲・件数・説明を落とさない
  無加工 … スキーマの無い文・壊れた dict・下限より短いスキーマは None（触らない）
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

# 見本は短いので、長さの下限を外して通す。
def compact(prompt):
    return mod.compact_embedded_schema(prompt, min_chars=0)


# 一覧
result = compact(PRE + mod._SAMPLE_SCHEMA + POST)
check("compacts sample", result is not None)
compacted, listing = result or ("", "")
check("listing text", listing == mod._EXPECTED, listing)
check("surroundings kept", compacted == PRE + mod._EXPECTED + POST, compacted)

# 無加工
check("no schema -> None", compact("自由に書いてよい。") is None)
check("broken dict -> None", compact("{'$defs': {'A': ") is None)

# 短いスキーマは元の書き方のまま。
# 書き方を変えるだけで会話進行の雇用の答えが寄った（DOC.md）。
check("short schema kept", mod.compact_embedded_schema(PRE + mod._SAMPLE_SCHEMA + POST) is None)
long_schema = mod._SAMPLE_SCHEMA.replace("'title': 'Area'", "'title': 'Area', 'x': '" + "a" * mod.MIN_SCHEMA_CHARS + "'")
check("long schema compacted", mod.compact_embedded_schema(PRE + long_schema + POST) is not None)

# べき等
check("idempotent", compact(compacted) is None)

# 両表記
as_json = json.dumps(mod.parse_literal(mod._SAMPLE_SCHEMA, 0)[0], ensure_ascii=False)
check("json spelling", compact(PRE + as_json + POST) == result)

# 型の書き出し
check("const", mod.describe_field({"const": "x"}) == '"x"')
check("string[]", mod.describe_field({"type": "array", "items": {"type": "string"}}) == "string[]")
check("union", mod.describe_field({"anyOf": [{"$ref": "#/$defs/A"}, {"enum": [1, 2]}, {"type": "null"}]})
      == "A|1|2|null")
check("plain types", [mod.describe_field({"type": t}) for t in ("string", "boolean", "integer")]
      == ["string", "boolean", "integer"])
check("nullable bool", mod.describe_field({"anyOf": [{"type": "boolean"}, {"type": "null"}]})
      == "boolean|null")
check("enum array", mod.describe_field({"type": "array", "items": {"enum": ["a", "b"]}})
      == '("a"|"b")[]')

# 数の範囲・件数・説明（版2）。
# grammar が縛る範囲でも、文から見えないと値の選び方が変わり得る（DOC.md）。
check("int range", mod.describe_field({"type": "integer", "minimum": -4, "maximum": 9}) == "integer(-4..9)")
check("int single", mod.describe_field({"type": "integer", "minimum": 1, "maximum": 1}) == "integer(1)")
check("int lower only", mod.describe_field({"type": "integer", "minimum": 1}) == "integer(>=1)")
check("exclusive", mod.describe_field({"type": "number", "exclusiveMinimum": 0, "maximum": 1})
      == "number(>0,<=1)")
check("nullable range", mod.describe_field({"anyOf": [{"type": "integer", "minimum": 1, "maximum": 5},
                                                      {"type": "null"}]}) == "integer(1..5)|null")
check("item count", mod.describe_field({"type": "array", "items": {"$ref": "#/$defs/E"},
                                        "minItems": 1, "maxItems": 3}) == "E[1..3]")
check("exact count", mod.describe_field({"type": "array", "items": {"enum": ["a", "b"]},
                                         "minItems": 5, "maxItems": 5}) == '("a"|"b")[5]')
check("max count", mod.describe_field({"type": "array", "items": {"type": "string"}, "maxItems": 8})
      == "string[<=8]")
check("description", mod.describe_field({"enum": ["slash", "fire"], "description": "Pick one."})
      == '"slash"|"fire" /* Pick one. */')
check("example", mod.describe_field({"type": "string", "example": "火"}) == 'string /* e.g. "火" */')
check("default dropped", mod.describe_field({"anyOf": [{"type": "string"}, {"type": "null"}], "default": None})
      == "string|null")

# 会話進行の型（main_026）。
# 版1は accepted の型と enum の値の引用符を落とし、ローカル LLM が雇用の誘いに
# casual_response を返す回が増えた（DOC.md）。
reply = {"properties": {
    "type": {"enum": ["casual_response", "recruitment_response"], "type": "string"},
    "accepted": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
    "statement": {"type": "string"},
    "call_free_action": {"type": "boolean"}},
    "required": ["type", "accepted", "statement", "call_free_action"]}
check("reply line", mod.type_line("Reply", reply)
      == 'Reply = {"type": "casual_response"|"recruitment_response", "accepted": boolean|null, '
         '"statement": string, "call_free_action": boolean}', mod.type_line("Reply", reply))

if failures:
    raise SystemExit("FAILED: {}".format(failures))
print("all ok")
