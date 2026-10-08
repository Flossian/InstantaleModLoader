# -*- coding: utf-8 -*-
"""Claude API の構造化出力で、型から組む文法が大きすぎて断られたときの迂回。`claude_side.py` から使う。

## 何が起きているか

ゲームは Claude へ `messages.parse(output_format=<pydantic の型>)` で頼む。
SDK は型を JSON Schema にして `output_config.format.schema` に載せ、API はそこから出力を縛る文法を組む。
依頼の生成（`random_quest_generator` の `QuestStructure`）は、
敵の一覧 → スキルの一覧 → 効果の3択（`anyOf`）と、選択肢つきの型が上限の無い配列の入れ子の中にあり、
API が `400 The compiled grammar is too large` で断る（GAME.md §2.12）。
ゲームの既定の `claude-sonnet-5` でも `claude-haiku-5-5` でも同じで、差し替えとは関係なく起きる。

## 迂回の仕方

断られた要求だけ、型の選択肢（`anyOf`）を、入れ子の深いものから1つずつ文字列（`{"type": "string"}`）に置き換えて送り直す。
文字列の欄の `description` に、元の選択肢の形（JSON Schema）を書き、
「この形の JSON の object を文字列にして書く」と頼む。
中身を決めない object（`{"type": "object"}`）は API が断る（`additionalProperties` を false にしないといけない）ので、文字列にする。
3択を1つの object にまとめる形と、深い選択肢を外す形も試したが、どちらも文法が大きすぎて断られた（VERIFICATION.md §3.88）。

応答は、ゲームの型で読む前（SDK の `post_parser` の前）に、文字列にした欄を JSON として読み戻して object に戻す。
読み戻せなければ文字列のまま渡し、ゲームの型の検証で失敗して、ゲームのいつもの送り直しに乗る。

通った置き換えは型ごとに覚え、同じ型の次の要求は最初から置き換えて送る（毎回 400 を踏まない）。
"""

import copy
import hashlib
import json

#: API の断りの文言（この文言のときだけ迂回する）。
TOO_LARGE = "compiled grammar is too large"

#: 文字列にした欄の印（`description` の頭）。応答を読み戻すときに、この印の欄だけを戻す。
MARK = "[json-in-string]"

#: 1つの要求で置き換えてみる選択肢の数の上限（送り直しの回数の上限）。
MAX_STEPS = 4


def too_large(exc):
    """文法が大きすぎるという断りか。"""
    return TOO_LARGE in str(getattr(exc, "message", "") or exc)


def schema_of(body):
    """本文の構造化出力の型。無ければ None。"""
    config = body.get("output_config")
    form = config.get("format") if isinstance(config, dict) else None
    schema = form.get("schema") if isinstance(form, dict) else None
    return schema if isinstance(schema, dict) else None


def schema_key(schema):
    return hashlib.sha1(json.dumps(schema, sort_keys=True, ensure_ascii=False)
                        .encode("utf-8")).hexdigest()


def _get(schema, path):
    node = schema
    for key in path:
        node = node[key]
    return node


def _resolve(schema, node):
    """`$ref`（`#/$defs/<名前>`）を引く。引けなければそのまま。"""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 16:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            break
        try:
            node = _get(schema, ref[2:].split("/"))
        except (KeyError, TypeError):
            break
        seen += 1
    return node


def candidates(schema):
    """置き換えの候補（`anyOf` の在り処）を、根からの入れ子の深いものから並べる。

    在り処は型の中のパス（キーの組）。深さは根から `$ref` を辿ったときの入れ子の数の最大。
    """
    depth = {}

    def walk(node, path, level, stack):
        if not isinstance(node, dict):
            return
        if "anyOf" in node and isinstance(node["anyOf"], list) and len(node["anyOf"]) > 1:
            depth[path] = max(depth.get(path, -1), level)
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and ref not in stack:
            target = tuple(ref[2:].split("/"))
            try:
                walk(_get(schema, target), target, level + 1, stack | {ref})
            except (KeyError, TypeError):
                pass
        for key in ("properties",):
            for name, child in (node.get(key) or {}).items():
                walk(child, path + (key, name), level + 1, stack)
        if isinstance(node.get("items"), dict):
            walk(node["items"], path + ("items",), level + 1, stack)
        for index, child in enumerate(node.get("anyOf") or []):
            walk(child, path + ("anyOf", index), level, stack)

    walk(schema, (), 0, frozenset())
    return [path for path, _level in sorted(depth.items(), key=lambda kv: -kv[1])]


def _inline(schema, node, stack=()):
    """`$ref` を中へ展開し、`title` を落とした写し（説明文に書く形）。"""
    node = _resolve(schema, node) if isinstance(node, dict) and "$ref" in node else node
    if isinstance(node, dict):
        ref_key = id(node)
        if ref_key in stack:
            return {}
        out = {}
        for key, value in node.items():
            if key == "title":
                continue
            out[key] = _inline(schema, value, stack + (ref_key,))
        return out
    if isinstance(node, list):
        return [_inline(schema, value, stack) for value in node]
    return node


def _prune_defs(schema):
    """使われなくなった `$defs` を落とす。"""
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return
    used, pending = set(), [json.dumps({k: v for k, v in schema.items() if k != "$defs"})]
    while pending:
        chunk = pending.pop()
        for name in defs:
            if name not in used and '"#/$defs/{}"'.format(name) in chunk:
                used.add(name)
                pending.append(json.dumps(defs[name]))
    schema["$defs"] = {name: value for name, value in defs.items() if name in used}
    if not schema["$defs"]:
        del schema["$defs"]


def loosen(schema, paths):
    """`paths` の選択肢を文字列の欄にした型の写しを返す。"""
    out = copy.deepcopy(schema)
    for path in paths:
        try:
            node = _get(out, path)
        except (KeyError, TypeError):
            continue
        shapes = _inline(schema, {"anyOf": node.get("anyOf")})["anyOf"]
        text = (MARK + " この欄は JSON の object を1つ、文字列にして書く（文字列の中の \" は \\\" にする）。"
                "object の形は次のどれか1つ（JSON Schema）: "
                + json.dumps(shapes, ensure_ascii=False, separators=(",", ":")))
        if node.get("description"):
            text = node["description"] + " " + text
        node.clear()
        node.update({"type": "string", "description": text})
    _prune_defs(out)
    return out


def with_schema(body, schema):
    """本文の写しに型を差し込む（元の本文は変えない）。"""
    body = dict(body)
    config = dict(body["output_config"])
    form = dict(config["format"])
    form["schema"] = schema
    config["format"] = form
    body["output_config"] = config
    return body


def decode(value, node, schema):
    """型を辿り、印の付いた文字列の欄を JSON として読み戻す。読めなければそのまま。"""
    node = _resolve(schema, node)
    if not isinstance(node, dict):
        return value
    if isinstance(value, str) and str(node.get("description", "")).find(MARK) >= 0:
        try:
            return json.loads(value)
        except ValueError:
            return value
    for option in node.get("anyOf") or []:
        value = decode(value, option, schema)
    if isinstance(value, dict):
        props = node.get("properties") or {}
        for name in list(value):
            if name in props:
                value[name] = decode(value[name], props[name], schema)
    elif isinstance(value, list) and isinstance(node.get("items"), dict):
        value = [decode(item, node["items"], schema) for item in value]
    return value


def fix_response(response, schema):
    """応答の文字のブロックを読み、印の欄を object に戻して書き直す。戻した数を返す。"""
    fixed = 0
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
        decoded = decode(data, schema, schema)
        block.text = json.dumps(decoded, ensure_ascii=False)
        fixed += 1
    return fixed


def with_decoder(kwargs, schema):
    """`options` の `post_parser`（ゲームの型で読む手）の前に、読み戻しを挟んだ kwargs の写しを返す。"""
    options = dict(kwargs.get("options") or {})
    parser = options.get("post_parser")

    def post_parser(response):
        fix_response(response, schema)
        return parser(response) if callable(parser) else response

    options["post_parser"] = post_parser
    return dict(kwargs, options=options)
