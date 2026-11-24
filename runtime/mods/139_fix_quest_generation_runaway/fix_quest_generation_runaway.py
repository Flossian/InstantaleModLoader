# -*- coding: utf-8 -*-
"""修正: 依頼の生成で、LLM が敵の一覧を書き足し続けて失敗するのを直す。

掲示板の「クエストを探す」などの依頼の生成（`random_quest_generator`。型は `QuestStructure`）は、
ローカル LLM で失敗して送り直されることが多い（実プレイで約35回中15回）。
失敗した回は、敵の一覧（`enemies`）に 48〜54 体を書き足し続け、コンテキストの窓を使い切って
JSON が途中で切れていた。ゲームはそれを捨てて同じ頼みを送り直すので、1回あたり 115〜127 秒待たされる。
ゲームの頼み文は「normal 2〜3種、miniboss 1種」と書いているが、型（grammar の元）に件数の決まりが無いので、
モデルが一度足し始めると止める仕組みが無い。

LLM へ渡す型の写しの並びに件数の上限（`maxItems`）を付ける。llama.cpp の grammar が
「上限を超える要素を書き始めること」をトークン単位で禁じるので、最後の要素は書き終えてから並びが閉じ、
続きの欄（ボス・舞台・イベント）は普通に書かれる。ゲームの受け取り（pydantic の型）には上限が無いので、そのまま読める。
ゲーム抜きの再現（`tools\\quest_gen_probe.py`）で、上限なしは 12 回中 8 回失敗、上限ありは 40 回とも
型どおりに成功した（わざと毎回打ち切らせた 8 回を含む）。経緯と数は DOC.md。

## どこに仕掛けるか

- 内蔵の llama.cpp: `LlamaCppClient.chat(model, messages, format=...)` の1点（`105_` と同じ）
- OpenAI 互換の経路（任意の互換サーバー＝LM Studio など、OpenAI）: openai の SDK の送信の1点
  `SyncAPIClient.post`（`136_` と同じ）。ゲームは `beta.chat.completions.parse` に pydantic の型を渡し、
  SDK がそれを `response_format.json_schema.schema` に直して送る（GAME.md §2.12）。互換サーバーは
  その型から grammar を作るので、内蔵の llama.cpp と同じ暴走が起きる

どちらも、型が依頼の型（`title` が `QuestStructure`）のときだけ、写しに上限を付けて渡す。
ゲームが持っている型の辞書と、SDK が組んだ送信の中身は書き換えない（同じ辞書を次のリクエストでも使いうる）。
Claude と Gemini は別の SDK で送るので、どちらの経路も通らない（Claude の構造化出力は `maxItems` を受け付けない）。
"""

import json

#: 何をしたかを残すログ。
LOG_BASENAME = "quest_generation_runaway.log"

CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"

#: openai の SDK で、全リクエストが最後に通る1点（`136_` と同じ）。
POST_TARGET = "openai._base_client:SyncAPIClient.post"

#: 上限を付ける型（`random_quest_generator` の返却型）。
ROOT_TITLE = "QuestStructure"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
#: 敵の一覧の上限。ゲームが受け取った成功の記録の最大（miniboss 1＋normal 4）。
ENEMY_LIMIT = 5

#: 敵の一覧のほかの並びの上限（`(型の名前, 欄)` -> 上限）。
#: ゲームの頼み文の指示（イベント 1〜3、落とす品 0〜3、技 0〜2）と、
#: 成功の記録の件数（場所 4〜5、画像の語 3、技の効果 1）に余裕を足した値。
#: 敵の一覧のほかで書き続けた回は観測していない。同じ形の穴なので一緒に塞ぐ。
OTHER_LIMITS = {
    ("QuestStructure", "events"): 4,
    ("Area", "locations"): 8,
    ("EnemyData", "skills"): 3,
    ("EnemyData", "drops"): 4,
    ("Look", "image_generation_prompt"): 6,
    ("Skill", "effects"): 3,
    ("TextStatusEffect", "effects_per_turn"): 3,
}


def limits():
    """付ける上限の表。設定が書き込まれた後に読むので関数にしてある。"""
    table = dict(OTHER_LIMITS)
    table[(ROOT_TITLE, "enemies")] = max(1, int(ENEMY_LIMIT))
    return table


def with_max_items(schema, table):
    """型の写しに件数の上限を付ける。`(写し, 付けた欄の一覧)`。付けるものが無ければ `(schema, [])`。

    元の型は書き換えない。既に同じか小さい上限が付いている欄は触らない。
    """
    if not isinstance(schema, dict) or schema.get("title") != ROOT_TITLE:
        return schema, []
    copied = json.loads(json.dumps(schema))
    applied = []
    nodes = [(copied.get("title") or "", copied)]
    defs = copied.get("$defs")
    if isinstance(defs, dict):
        nodes += list(defs.items())
    for name, node in nodes:
        props = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(props, dict):
            continue
        for field, spec in props.items():
            limit = table.get((name, field))
            if limit is None or not isinstance(spec, dict):
                continue
            current = spec.get("maxItems")
            if isinstance(current, int) and not isinstance(current, bool) and current <= limit:
                continue
            spec["maxItems"] = limit
            applied.append("{}.{}<={}".format(name, field, limit))
    return (copied, applied) if applied else (schema, [])


def response_format_schema(body):
    """openai の SDK の送信の中身から、構造化出力の型を取り出す。無ければ None。"""
    if not isinstance(body, dict):
        return None
    fmt = body.get("response_format")
    if not isinstance(fmt, dict) or fmt.get("type") != "json_schema":
        return None
    spec = fmt.get("json_schema")
    schema = spec.get("schema") if isinstance(spec, dict) else None
    return schema if isinstance(schema, dict) else None


def with_limited_response_format(body, table):
    """送信の中身の写しの型に件数の上限を付ける。`(写し, 付けた欄の一覧)`。付けるものが無ければ `(body, [])`。

    写すのは型までの道筋（body・`response_format`・`json_schema`）だけで、messages などは同じものを渡す。
    """
    schema = response_format_schema(body)
    if schema is None:
        return body, []
    limited, applied = with_max_items(schema, table)
    if not applied:
        return body, []
    fmt = dict(body["response_format"])
    fmt["json_schema"] = dict(fmt["json_schema"], schema=limited)
    return dict(body, response_format=fmt), applied


def looks_like_quest(schema):
    """依頼の型らしいか（敵とボスの欄を持つ）。名前が変わったことに気づくためだけに使う。"""
    props = schema.get("properties") if isinstance(schema, dict) else None
    return isinstance(props, dict) and "enemies" in props and "boss" in props


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    seen = {"count": 0, "unknown": False}

    def note(applied, schema, route):
        """付けた回は1行、依頼らしいのに名前が違う型は WARN を1度だけ残す。"""
        if applied:
            seen["count"] += 1
            write("limited {} #{} ({}): {}".format(ROOT_TITLE, seen["count"], route, ", ".join(applied)))
        elif (isinstance(schema, dict) and schema.get("title") != ROOT_TITLE
              and not seen["unknown"] and looks_like_quest(schema)):
            # 依頼の型らしいのに名前が違う。ゲームの更新で型の名前が変わった合図。
            seen["unknown"] = True
            write("WARN a quest-like schema titled {!r}; left untouched".format(schema.get("title")))

    @ctx.wrap(CHAT_TARGET, required=False)
    def chat(orig, self, model, messages, format=None, *args, **kwargs):
        try:
            if isinstance(format, dict):
                limited, applied = with_max_items(format, limits())
                if applied:
                    format = limited
                note(applied, format, "llama.cpp")
        except Exception:
            ctx.log_exc("quest generation runaway: cannot limit the schema; sending it as is")
        return orig(self, model, messages, format, *args, **kwargs)

    # required=False: 使っていないプロバイダの SDK は読み込まれない。現れた時点でローダが当て直す。
    # alias_scan=False: クラスの属性だけを替える（`136_` と同じ。SDK の資源が掴んだ手はローダが掴み直す）。
    # 受けた引数はそのまま orig へ渡し、型を替えるときだけ body の写しを差し込む。
    @ctx.wrap(POST_TARGET, required=False, safe=True, alias_scan=False)
    def openai_post(orig, self, *args, **kwargs):
        body = kwargs.get("body")
        schema = response_format_schema(body)
        if schema is not None:
            limited, applied = with_limited_response_format(body, limits())
            if applied:
                kwargs = dict(kwargs, body=limited)
            note(applied, schema, "openai")
        return orig(self, *args, **kwargs)

    # 注入した時点で、表と写しの作り方を確かめておく（実経路は依頼を作るまで通らない）。
    sample = {"title": ROOT_TITLE, "type": "object",
              "properties": {"enemies": {"type": "array", "items": {"$ref": "#/$defs/Enemy"}}},
              "$defs": {"EnemyData": {"properties": {"drops": {"type": "array"}}}}}
    limited, applied = with_max_items(sample, limits())
    ok = (limited is not sample and "maxItems" not in json.dumps(sample)
          and limited["properties"]["enemies"]["maxItems"] == max(1, int(ENEMY_LIMIT))
          and limited["$defs"]["EnemyData"]["properties"]["drops"]["maxItems"] == 4
          and with_max_items({"title": "Other", "properties": {}}, limits())[1] == [])
    body = {"messages": [], "response_format": {"type": "json_schema",
                                                "json_schema": {"name": ROOT_TITLE, "schema": sample}}}
    sent, sent_applied = with_limited_response_format(body, limits())
    ok = (ok and sent is not body and sent["messages"] is body["messages"]
          and body["response_format"]["json_schema"]["schema"] is sample
          and response_format_schema(sent)["properties"]["enemies"]["maxItems"] == max(1, int(ENEMY_LIMIT))
          and sent_applied == applied)
    if ok:
        ctx.log("quest generation runaway: enemies <= {} and {} other list(s) on {} (log {})".format(
            max(1, int(ENEMY_LIMIT)), len(OTHER_LIMITS), ROOT_TITLE, LOG_BASENAME))
    else:
        ctx.log("quest generation runaway: self-check failed ({})".format(applied), level="ERROR")
