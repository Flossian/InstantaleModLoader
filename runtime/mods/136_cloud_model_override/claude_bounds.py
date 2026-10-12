# -*- coding: utf-8 -*-
"""Claude API の応答の数を、ゲームの型の範囲に収める。`claude_side.py` から使う。

## 何が起きているか

ゲームの型には数の範囲がある（効果の `duration` は 3〜5、スキルの `max_uses` は 1〜10 など）。
SDK は型を送る前に `minimum` / `maximum` / `maxItems` を外し、説明文の末尾に `{maximum: 5, minimum: 3}` と書き移す。
API は数の範囲も配列の長さも縛らないので、Claude が範囲の外の値を書くと、
ゲームの型の検証で落ちて頼み全体を送り直す（依頼の生成で `duration` が 2。VERIFICATION.md §3.71）。

## 直し方

応答をゲームの型で読む前（SDK の `post_parser` の前）に、型を辿って範囲の外の値を端に寄せる。

  * 数（`integer` / `number`）: `minimum` / `maximum` の端へ。整数の `exclusiveMinimum` / `exclusiveMaximum` は1つ内側へ
  * 配列: `maxItems` を超えた分を後ろから落とす。`minItems` に足りない配列は、足す中身を作れないので触らない
  * 選択肢（`anyOf`）: 値1つの `enum` の欄（`type` など）が合う選択肢が1つに決まるときだけ、その形で辿る。決まらなければ触らない

文字列にした欄（`claude_grammar.loosen`）は、読み戻した後に、元の型で辿る。
"""

import json
import re

#: SDK が書き移した縛り（説明文の末尾の `{key: value, ...}`）。
NOTE = re.compile(r"\{([^{}]*)\}\Z")
#: 読む縛りの名前。
KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "maxItems")


def bounds(node):
    """説明文に書き移された縛りを {名前: 数} で返す。無ければ空。"""
    text = node.get("description") if isinstance(node, dict) else None
    found = NOTE.search(text) if isinstance(text, str) else None
    out = {}
    if not found:
        return out
    for part in found.group(1).split(","):
        key, sep, value = part.partition(":")
        key = key.strip()
        if not sep or key not in KEYS:
            continue
        try:
            number = float(value.strip())
        except ValueError:
            continue
        out[key] = int(number) if number.is_integer() else number
    return out


def has_bounds(schema):
    """型のどこかに数の縛りの書き移しがあるか（無ければ包まない）。"""
    text = json.dumps(schema, ensure_ascii=False)
    return any("{" + key + ":" in text or ", " + key + ":" in text for key in KEYS)


def _resolve(schema, node):
    seen = 0
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and seen < 16:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            break
        target = schema
        try:
            for key in ref[2:].split("/"):
                target = target[key]
        except (KeyError, TypeError):
            break
        node = target
        seen += 1
    return node


def _fits(option, value, schema):
    """object の値が、値1つの enum の欄と必須の欄で、この選択肢に合うか。"""
    option = _resolve(schema, option)
    if not (isinstance(option, dict) and isinstance(value, dict) and option.get("type") == "object"):
        return False
    props = option.get("properties") or {}
    if any(name not in value for name in option.get("required") or []):
        return False
    if option.get("additionalProperties") is False and any(name not in props for name in value):
        return False
    for name, prop in props.items():
        prop = _resolve(schema, prop)
        enum = prop.get("enum") if isinstance(prop, dict) else None
        if isinstance(enum, list) and len(enum) == 1 and name in value and value[name] != enum[0]:
            return False
    return True


def clamp(value, node, schema, changes):
    """型を辿り、範囲の外の値を端へ寄せた値を返す。寄せた所を `changes` に足す。"""
    node = _resolve(schema, node)
    if not isinstance(node, dict):
        return value
    options = node.get("anyOf")
    if isinstance(options, list):
        if isinstance(value, dict):
            fitting = [option for option in options if _fits(option, value, schema)]
            return clamp(value, fitting[0], schema, changes) if len(fitting) == 1 else value
        if isinstance(value, list):
            arrays = [option for option in options if _resolve(schema, option).get("type") == "array"]
            return clamp(value, arrays[0], schema, changes) if len(arrays) == 1 else value
        numbers = [option for option in options
                   if _resolve(schema, option).get("type") in ("integer", "number")]
        if len(numbers) == 1 and isinstance(value, (int, float)) and not isinstance(value, bool):
            return clamp(value, numbers[0], schema, changes)
        return value
    kind = node.get("type")
    limits = bounds(node)
    if kind in ("integer", "number") and isinstance(value, (int, float)) and not isinstance(value, bool):
        low, high = limits.get("minimum"), limits.get("maximum")
        if kind == "integer":
            if "exclusiveMinimum" in limits:
                low = limits["exclusiveMinimum"] + 1 if low is None else max(low, limits["exclusiveMinimum"] + 1)
            if "exclusiveMaximum" in limits:
                high = limits["exclusiveMaximum"] - 1 if high is None else min(high, limits["exclusiveMaximum"] - 1)
        fixed = value
        if low is not None and fixed < low:
            fixed = low
        if high is not None and fixed > high:
            fixed = high
        if fixed != value:
            changes.append("{}->{}".format(value, fixed))
        return fixed
    if kind == "array" and isinstance(value, list):
        top = limits.get("maxItems")
        if isinstance(top, int) and 0 <= top < len(value):
            changes.append("items {}->{}".format(len(value), top))
            value = value[:top]
        items = node.get("items")
        return [clamp(item, items, schema, changes) for item in value] if isinstance(items, dict) else value
    if kind == "object" and isinstance(value, dict):
        props = node.get("properties") or {}
        for name in list(value):
            if name in props:
                value[name] = clamp(value[name], props[name], schema, changes)
    return value


def fix_response(response, schema):
    """応答の文字のブロックを読み、範囲の外の値を寄せて書き直す。寄せた所の一覧を返す。"""
    changes = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        before = len(changes)
        data = clamp(data, schema, schema, changes)
        if len(changes) > before:
            block.text = json.dumps(data, ensure_ascii=False)
    return changes


def with_clamp(kwargs, schema, report):
    """`options` の `post_parser`（ゲームの型で読む手）の前に、範囲へ寄せる手を挟んだ kwargs の写しを返す。

    寄せたときは `report(寄せた所の一覧)` を呼ぶ。
    """
    options = dict(kwargs.get("options") or {})
    parser = options.get("post_parser")

    def post_parser(response):
        changes = fix_response(response, schema)
        if changes:
            report(changes)
        return parser(response) if callable(parser) else response

    options["post_parser"] = post_parser
    return dict(kwargs, options=options)
