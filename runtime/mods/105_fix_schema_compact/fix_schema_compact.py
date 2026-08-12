# -*- coding: utf-8 -*-
"""プロンプトに埋め込まれたスキーマ説明文を、簡潔な一覧表記に置き換える。

ゲームは llama-server に `json_schema` パラメータ（grammar の実体）を送ると
同時に、**同じスキーマを Python dict の repr としてプロンプト本文にも埋め込んで**
いる。構造の正しさは `json_schema` 側がトークン単位で強制するので、プロンプト側の
説明文に必要なのは「フィールドの意味の手がかり」だけで、全フィールドに付いてくる
`{'title': 'Name', 'type': 'string'}` のような定型句は落としても構造は壊れない。

    元:   {'$defs': {'Location': {'properties': {'name': {'title': 'Name', ...
    後:   Location: name, kind:∈{shop,inn}
          Area: name, locations:Location[], atomosphere:∈{tense,normal}, note?

外部プロキシ（InstantaleLLMProxy）の `Proxy.SchemaFix.cs` にある COMPACT と
同じ処理をプロセス内でやる。判定と出力の書式はプロキシ側に揃えてあるので、
同じスキーマからは同じ一覧が出る。両方を同時に動かしても、先に圧縮した方で
マーカー（`{'$defs':` 等）が消えるため、もう片方は何もしない（二重適用にならない）。

検証は実データ（ゲーム自身が `output_data/` に保存した messages 12,067 件）と実機の
両方で済んでいる。誤爆・欠落とも 0 件、削減率は 72〜73%（VERIFICATION_LOG.md §2.3）。

## どこに仕掛けるか

プロキシは HTTP ボディを見るので `prompt` と `json_schema` が同じ dict に揃って
いる。「json_schema か grammar が既にある時だけ圧縮する」という安全条件は、この
1箇所で判定できていた。プロセス内で同じものが揃っているのは:

    LlamaCppClient._post_with_model_loading_retry(url, payload)   payload に両方ある

ただし `chat(..., stream=True)` の経路がこのヘルパを通るとは限らない。通らなかった
場合に何も起きないのを避けるため、上流の

    LlamaCppClient.chat(model, messages, format=...)              format がスキーマ

にも同じ処理を掛ける。`format` が **dict の時だけ** 対象にする。`"json"` のような
汎用 JSON 指定はフィールド単位の強制をしないので、説明文を削ると手がかりが消える。

どちらが実際に発火したかは `out/prompt_bloat.log` の `[COMPACT] <site>` で分かる。
両方 0 件なら、そもそもこの2つを通っていないということ。

## 割り切り（プロキシと同じ）

- 圧縮するのは **最初に見つかったスキーマ1個だけ**。2個目を含むメッセージは
  実データに 0 件なので、実運用では制約になっていない
- `$defs` の中でも `properties` を持たない定義（Enum クラス単体など）は行を出さず、
  参照側に `$ref` の型名だけが残る。pydantic は `Literal` を参照先ではなく
  プロパティ側に展開するため、これで enum 値は落ちない
- 置換後の方が長くなる場合は何もしない

一時的に止めたいときはファイル名の先頭に `_` を付ける（ローダが読み込まなくなる）。

## クラウド（APIキー）では動かない。**それでよい**

仕掛けているのは `LlamaCppClient` の2点だけで、`llm.wrap_outgoing`（プロバイダ
非依存の口）には載せていない。理由は2つあり、どちらも載せても意味が無い:

* 圧縮してよいかの判定が `json_schema` / `grammar` の有無で、これは llama.cpp の
  payload と format にしか無い。境界の `message` からは分からない
* そもそもクラウドではスキーマ文が**プロンプトに埋まっていない**。Gemini は
  `send_request` の中で足すので境界の外、OpenAI / Claude は API 側に任せていて
  埋め込み自体が無い（GAME.md §2.12）

つまり圧縮する対象がクラウド経路には存在しない。`119_` / `305_` が
「ローカルにしか仕掛けていない」ことで**取りこぼしていた**のとは事情が違う。
"""


# Python 表記／JSON 表記のどちらでも拾う。プロキシの SchemaMarkers と同じ。
SCHEMA_MARKERS = ("{'$defs':", "{'properties':", '{"$defs":', '{"properties":')

AUDIT_FIRST_N = 5        # 最初の数回だけ、圧縮後の一覧を全文ログに残す

# payload に prompt と json_schema が揃う地点（プロキシと同じ判定ができる）。
POST_TARGET = "llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry"
# 上流の保険。stream 経路が上を通らなかった場合はこちらが効く。
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"


# --------------------------------------------------------------------------
# Python リテラル／JSON の共通パーサ
# --------------------------------------------------------------------------
# ast.literal_eval は「式ちょうど1個」しか受け取れないので、プロンプトの途中から
# 読み始めて「どこで終わったか」を返すことができない。終端位置が分からないと
# 置換範囲を決められないため、プロキシ側と同じ再帰下降パーサを持つ。
# True/False/None と true/false/null、シングル／ダブルクォート、末尾カンマ、
# タプル表記まで受ける（＝両表記のスーパーセット）。

class _LiteralError(ValueError):
    pass


_WHITESPACE = " \t\r\n"
_DIGITS = "0123456789"
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0"}
_WORDS = (("True", True), ("False", False), ("None", None),
          ("true", True), ("false", False), ("null", None))


def _skip_ws(text, i):
    length = len(text)
    while i < length and text[i] in _WHITESPACE:
        i += 1
    return i


def _match_word(text, i, word):
    """位置 i がちょうど word か。直後が英数字/_ なら別の識別子の一部とみなす。

    `None` と `NoneOfThem` を取り違えないための判定。
    """
    if not text.startswith(word, i):
        return False
    end = i + len(word)
    if end < len(text):
        following = text[end]
        if following.isalnum() or following == "_":
            return False
    return True


def _parse_string(text, i):
    quote = text[i]
    i += 1
    length = len(text)
    out = []
    while True:
        if i >= length:
            raise _LiteralError("unterminated string")
        char = text[i]
        if char == quote:
            return "".join(out), i + 1
        if char != "\\":
            out.append(char)
            i += 1
            continue
        if i + 1 >= length:
            raise _LiteralError("truncated escape")
        escaped = text[i + 1]
        simple = _ESCAPES.get(escaped)
        if simple is not None:
            out.append(simple)
            i += 2
        elif escaped == "x":
            out.append(chr(int(text[i + 2:i + 4], 16)))
            i += 4
        elif escaped == "u":
            out.append(chr(int(text[i + 2:i + 6], 16)))
            i += 6
        else:
            # \' \" \\ \/ など。エスケープを外した文字そのもの。
            out.append(escaped)
            i += 2


def _parse_number(text, i):
    start = i
    length = len(text)
    if i < length and text[i] in "+-":
        i += 1
    is_float = False
    while i < length:
        char = text[i]
        if char in _DIGITS:
            i += 1
        elif char in ".eE":
            is_float = True
            i += 1
        elif char in "+-" and text[i - 1] in "eE":
            i += 1
        else:
            break
    raw = text[start:i]
    # maxLength などの整数を 3.0 の形に変えないよう、整数は int のまま保つ。
    return (float(raw) if is_float else int(raw)), i


def parse_literal(text, i):
    """text[i:] の先頭にあるリテラルを (値, 次の位置) として返す。"""
    i = _skip_ws(text, i)
    length = len(text)
    if i >= length:
        raise _LiteralError("end of input")
    char = text[i]

    if char == "{":
        # dict は挿入順を保つ（3.7 以降）。スキーマの定義順がそのまま一覧の順になる。
        mapping = {}
        i = _skip_ws(text, i + 1)
        if i < length and text[i] == "}":
            return mapping, i + 1
        while True:
            key, i = parse_literal(text, i)
            if not isinstance(key, str):
                raise _LiteralError("non-string dict key")
            i = _skip_ws(text, i)
            if i >= length or text[i] != ":":
                raise _LiteralError("missing ':' after key")
            mapping[key], i = parse_literal(text, i + 1)
            i = _skip_ws(text, i)
            if i >= length:
                raise _LiteralError("unterminated dict")
            if text[i] == ",":
                i = _skip_ws(text, i + 1)
                if i < length and text[i] == "}":
                    return mapping, i + 1      # 末尾カンマ
                continue
            if text[i] == "}":
                return mapping, i + 1
            raise _LiteralError("bad dict separator at {}".format(i))

    if char in "[(":
        closing = "]" if char == "[" else ")"
        items = []
        i = _skip_ws(text, i + 1)
        if i < length and text[i] == closing:
            return items, i + 1
        while True:
            value, i = parse_literal(text, i)
            items.append(value)
            i = _skip_ws(text, i)
            if i >= length:
                raise _LiteralError("unterminated sequence")
            if text[i] == ",":
                i = _skip_ws(text, i + 1)
                if i < length and text[i] == closing:
                    return items, i + 1
                continue
            if text[i] == closing:
                return items, i + 1
            raise _LiteralError("bad sequence separator at {}".format(i))

    if char in "'\"":
        return _parse_string(text, i)

    for word, value in _WORDS:
        if _match_word(text, i, word):
            return value, i + len(word)

    if char in "+-" or char in _DIGITS:
        return _parse_number(text, i)

    raise _LiteralError("unknown literal at {}".format(i))


# --------------------------------------------------------------------------
# JSON Schema → 一覧表記
# --------------------------------------------------------------------------
def find_schema_start(prompt):
    """埋め込みスキーマの開始位置。見つからなければ -1。"""
    best = -1
    for marker in SCHEMA_MARKERS:
        index = prompt.find(marker)
        if index >= 0 and (best < 0 or index < best):
            best = index
    return best


def _text_of(value):
    """enum 値や const をそのまま書き出すときの表記。"""
    return "" if value is None else str(value)


def describe_field(field):
    """フィールド1つ分の「意味の手がかり」だけを短く書き出す。

    素の string / integer / boolean / number には何も付けない。既定の型として
    扱われるので、書いても手がかりが増えないため。enum・$ref・配列・共用体だけ残す。
    """
    if not isinstance(field, dict):
        return ""

    if "const" in field:
        return '="{}"'.format(_text_of(field["const"]))

    if "$ref" in field:
        ref = field["$ref"]
        # "#/$defs/Location" -> "Location"
        return ref.rpartition("/")[2] if isinstance(ref, str) else ""

    if "enum" in field:
        values = field["enum"]
        if not isinstance(values, list):
            return ""
        return "∈{" + ",".join(_text_of(v) for v in values) + "}"

    if "anyOf" in field:
        options = field["anyOf"]
        if not isinstance(options, list):
            return ""
        parts = []
        nullable = False
        for option in options:
            if not isinstance(option, dict):
                continue
            if option.get("type") == "null":
                # Optional[...] は型の選択肢ではなく末尾の ? で表す。
                nullable = True
                continue
            described = describe_field(option)
            if described:
                parts.append(described)
        joined = "|".join(parts)
        return joined + "?" if nullable and joined else joined

    if "items" in field:
        items = field["items"]
        inner = describe_field(items)
        if not inner and isinstance(items, dict):
            # 要素が素の型なら、その型名だけは残す（string[] と list の区別が付く）。
            item_type = items.get("type")
            inner = item_type if isinstance(item_type, str) else ""
        return inner + "[]"

    return ""


def type_line(type_name, type_schema):
    """1つの型を "Name: f1, f2:desc, f3?" の1行にする。properties が無ければ None。"""
    if not isinstance(type_schema, dict):
        return None
    properties = type_schema.get("properties")
    if not isinstance(properties, dict):
        return None

    required = type_schema.get("required")
    required = set(required) if isinstance(required, list) else set()

    fields = []
    for name, schema in properties.items():
        described = describe_field(schema)
        # 必須でないものだけ末尾に ? を付ける。required の方が多数派なので、
        # 印を付けるのは任意側にした方が短くなる。
        fields.append("{}{}{}".format(name,
                                      ":" + described if described else "",
                                      "" if name in required else "?"))
    return "{}: {}".format(type_name, ", ".join(fields))


def compact_schema_text(root):
    """スキーマ全体を、型ごとに1行の一覧に落とす。"""
    lines = []
    defs = root.get("$defs")
    if isinstance(defs, dict):
        for name, schema in defs.items():
            line = type_line(name, schema)
            if line:
                lines.append(line)
    title = root.get("title")
    line = type_line(title if isinstance(title, str) and title else "Structure", root)
    if line:
        lines.append(line)
    return "\n".join(lines)


def compact_embedded_schema(prompt):
    """プロンプト内の埋め込みスキーマを一覧表記に置き換える。

    戻り値は (置き換え後のプロンプト, 差し込んだ一覧) の組。対象が無い・
    解析できない・圧縮にならない場合は None（＝無加工）。
    """
    start = find_schema_start(prompt)
    if start < 0:
        return None                      # スキーマ説明の無いリクエストには作用しない

    try:
        schema, end = parse_literal(prompt, start)
    except Exception:
        return None                      # 読めない形は安全側で触らない
    if not isinstance(schema, dict):
        return None

    try:
        compact = compact_schema_text(schema).rstrip("\n")
    except Exception:
        return None                      # 未知の形も同様
    if not compact:
        return None

    new_prompt = prompt[:start] + compact + prompt[end:]
    if len(new_prompt) >= len(prompt):
        return None                      # 縮まないなら変更しない
    return new_prompt, compact


# --------------------------------------------------------------------------
# 注入
# --------------------------------------------------------------------------
def _message_parts(message):
    try:
        return message.get("role", "?"), message.get("content", "")
    except Exception:
        return "?", None


def apply(ctx):
    log_path = ctx.out_path("prompt_bloat.log")
    state = {"hits": 0}

    write = ctx.logger("prompt_bloat.log")

    def report(site, before_text, after_text, compact):
        state["hits"] += 1
        write("[COMPACT] {} | スキーマ説明を圧縮 {} -> {} 文字 (削減 {})".format(
            site, len(before_text), len(after_text), len(before_text) - len(after_text)))
        # 最初の数回だけ、差し込んだ一覧をそのまま残す。
        # 「フィールド名と enum が本当に残っているか」を目で確認するため。
        if state["hits"] <= AUDIT_FIRST_N:
            for line in compact.split("\n"):
                write("    {}".format(line))

    # -------------------------------------------------- payload（プロキシと同位置）
    @ctx.wrap(POST_TARGET, required=False)
    def post_with_retry(orig, self, url, payload, timeout=None, *args, **kwargs):
        try:
            # json_schema / grammar が付いている＝構造は grammar が強制する。
            # プロキシの安全条件と同じ。付いていないリクエストは触らない。
            if isinstance(payload, dict) and ("json_schema" in payload or "grammar" in payload):
                prompt = payload.get("prompt")
                if isinstance(prompt, str):
                    result = compact_embedded_schema(prompt)
                    if result is not None:
                        new_prompt, compact = result
                        # 呼び出し元の dict は変えず、浅いコピーを渡す。
                        # payload を組み立てた側が他の用途で持ち続けている可能性があるため。
                        payload = dict(payload)
                        payload["prompt"] = new_prompt
                        report("payload " + str(url), prompt, new_prompt, compact)
        except Exception:
            # 圧縮に失敗してもプロンプトはそのまま送る。長いだけなら推論は通るので、
            # ここで止める方が損害が大きい。
            ctx.log_exc("compact: payload pass failed; sending prompt untouched")
        return orig(self, url, payload, timeout, *args, **kwargs)

    # ------------------------------------------------------ messages（上流の保険）
    @ctx.wrap(CHAT_TARGET, required=False)
    def chat(orig, self, model, messages, format=None, *args, **kwargs):
        try:
            # format が dict のときだけ＝実体のあるスキーマが渡っているとき。
            # "json" のような汎用指定はフィールド単位の強制をしないので対象外。
            # messages の型もここで確かめる。想定外の型は「触らない」が正しく、
            # 下の except に落とすと呼び出しのたびにトレースバックでログが埋まる。
            if isinstance(format, dict) and format and isinstance(messages, list):
                new_messages = []
                changed = False
                for message in messages:
                    role, content = _message_parts(message)
                    if isinstance(content, str):
                        result = compact_embedded_schema(content)
                        if result is not None:
                            new_content, compact = result
                            # message そのものは書き換えない。会話履歴として
                            # ゲーム側が同じ dict を保持している可能性があるため。
                            replacement = dict(message)
                            replacement["content"] = new_content
                            new_messages.append(replacement)
                            changed = True
                            report("chat " + str(role), content, new_content, compact)
                            continue
                    new_messages.append(message)
                if changed:
                    messages = new_messages
        except Exception:
            ctx.log_exc("compact: messages pass failed; sending prompt untouched")
        return orig(self, model, messages, format, *args, **kwargs)

    # 注入した時点で、作ったデータを使って正しさを確かめておく。
    # 実経路はゲームが構造付きリクエストを出したときにしか通らず、起動直後に
    # 通る保証が無いため（102_ と同じ方針）。
    _verify(ctx)

    # required=False なので、対象が無くても警告だけ出て先へ進む。
    # 実際にどちらへ仕掛かったのかはここで確かめてログに残す。
    armed = []
    for target in (POST_TARGET, CHAT_TARGET):
        try:
            _owner, _name, value = ctx.resolve(target)
        except Exception:
            value = None
        if value is not None:
            armed.append(target.rpartition(".")[2])
    ctx.log("schema compact: armed on {} | log {}".format(
        ", ".join(armed) if armed else "nothing (both targets missing)", log_path))


# --------------------------------------------------------------------------
# 自己検証
# --------------------------------------------------------------------------
# pydantic が吐く形をそのまま縮めたもの。$defs / $ref / enum / 配列 / Optional と、
# 全フィールドに付く title・type の定型句を1つずつ含めてある。
_SAMPLE_SCHEMA = (
    "{'$defs': {'Location': {'properties': "
    "{'name': {'title': 'Name', 'type': 'string'}, "
    "'kind': {'enum': ['shop', 'inn'], 'title': 'Kind', 'type': 'string'}}, "
    "'required': ['name', 'kind'], 'title': 'Location', 'type': 'object'}}, "
    "'properties': {'name': {'title': 'Name', 'type': 'string'}, "
    "'locations': {'items': {'$ref': '#/$defs/Location'}, 'title': 'Locations', "
    "'type': 'array'}, "
    "'atomosphere': {'enum': ['tense', 'normal'], 'title': 'Atomosphere', "
    "'type': 'string'}, "
    "'note': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, "
    "'title': 'Note'}}, "
    "'required': ['name', 'locations', 'atomosphere'], 'title': 'Area', "
    "'type': 'object'}"
)

_EXPECTED = ("Location: name, kind:∈{shop,inn}\n"
             "Area: name, locations:Location[], atomosphere:∈{tense,normal}, note?")


def _verify(ctx):
    prompt = "出力は次の構造に従うこと: " + _SAMPLE_SCHEMA + "\n以上を守ること。"
    result = compact_embedded_schema(prompt)

    if result is None:
        ctx.log("VERIFY FAILED: sample schema was not compacted", level="ERROR")
        return
    compacted, _compact_text = result

    expected = "出力は次の構造に従うこと: " + _EXPECTED + "\n以上を守ること。"
    if compacted != expected:
        ctx.log("VERIFY FAILED: unexpected compaction\n  got      {!r}\n  expected {!r}".format(
            compacted, expected), level="ERROR")
        return

    # スキーマ説明を持たないプロンプトには作用しないこと（自由文出力を壊さない）。
    if compact_embedded_schema("ここに構造の指定は無い。自由に書いてよい。") is not None:
        ctx.log("VERIFY FAILED: compacted a prompt with no embedded schema", level="ERROR")
        return

    # 2回目は何も残っていない。プロキシと同時に動かしても二重適用にならない根拠。
    if compact_embedded_schema(compacted) is not None:
        ctx.log("VERIFY FAILED: compaction is not idempotent", level="ERROR")
        return

    ctx.log("verified: schema compaction {} -> {} chars ({:.0%} smaller), "
            "idempotent, no-op without a schema".format(
                len(prompt), len(compacted), 1 - len(compacted) / len(prompt)))
