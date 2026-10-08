# -*- coding: utf-8 -*-
"""Claude API 宛ての差し替えとプロンプトキャッシュ。入口（`cloud_model_override.py`）の `apply()` から `install()` を呼ぶ。

設定の値は引数で受け取り、入口の定数は読まない。

## 名前だけでは通らないもの

新しい世代は `temperature` / `top_p` / `top_k` と
`thinking.budget_tokens` を断る。Opus 5.5 / Fable 系は思考を切れず、
強制ツール指定（`tool_choice` の `any` / `tool`）も断る。
Sonnet 5.5 も `disabled` を断るが、`between_tools` で思考を切れる。
Opus 5 / Haiku 5.5 / Sonnet 5.5 は、effort が `xhigh` / `max` だと思考を切る指定を断る。
モデルごとの違いは `TRAITS` の表にまとめ、`fix()` は表を引くだけにしてある。

## プロンプトキャッシュ

ゲームは Claude へ `cache_control` を送らない（送信モジュールの定数に無い。
応答の `cache_read_input_tokens` は読んで価格に入れている）。
設定で、前回の同じ種類の頼みと先頭から一致した所に印を付ける（`mark_shared_prefix`。
置き方の理由は `prefix_cache.py`。system の末尾では、末尾が毎回変わるので当たらない）。
差し替え先を `off` にしていても、キャッシュだけを効かせられる。
"""

import json

from .prefix_cache import Prefixes

#: SDK で、全リクエストが最後に通る1点。
POST = "anthropic._base_client:SyncAPIClient.post"

# --------------------------------------------------------------------------
# モデルごとの制約
# --------------------------------------------------------------------------
# sampling:     temperature / top_p / top_k を断る
# budget:       thinking {type: enabled, budget_tokens} を断る
# no_disable:   thinking {type: disabled} を断る（思考を切れない）
# between_tools: disabled の代わりに thinking {type: between_tools} で思考を切れる
# off_upto_high: 思考を切る指定（disabled / between_tools）は effort が high 以下のときだけ受ける
# no_forced:    tool_choice の any / tool を断る
# effort:       output_config.effort を受ける
STRICT = {"sampling": True, "budget": True, "no_disable": True,
          "between_tools": False, "off_upto_high": False, "no_forced": True, "effort": True}
TRAITS = (
    # 前方一致なので、長い名前を先に並べる。
    ("claude-opus-5-5", STRICT),
    ("claude-sonnet-5-5", dict(STRICT, between_tools=True, off_upto_high=True)),
    ("claude-haiku-5-5", dict(STRICT, no_disable=False, off_upto_high=True,
                              no_forced=False)),
    ("claude-fable-5-1", STRICT),
    ("claude-mythos-5-1", STRICT),
    ("claude-fable-5", dict(STRICT, no_forced=False)),
    ("claude-mythos-5", dict(STRICT, no_forced=False)),
    ("claude-opus-5", dict(STRICT, no_disable=False, off_upto_high=True, no_forced=False)),
    ("claude-sonnet-5", dict(STRICT, no_disable=False, no_forced=False)),
    ("claude-opus-4-8", dict(STRICT, no_disable=False, no_forced=False)),
    ("claude-opus-4-7", dict(STRICT, no_disable=False, no_forced=False)),
    ("claude-opus-4-6", {"sampling": False, "budget": False, "no_disable": False,
                         "between_tools": False, "off_upto_high": False,
                         "no_forced": False, "effort": True}),
    ("claude-sonnet-4-6", {"sampling": False, "budget": False, "no_disable": False,
                           "between_tools": False, "off_upto_high": False,
                           "no_forced": False, "effort": True}),
    ("claude-haiku-4-5", {"sampling": False, "budget": False, "no_disable": False,
                          "between_tools": False, "off_upto_high": False,
                          "no_forced": False, "effort": False}),
)

#: キャッシュの設定値 -> cache_control の ttl（None は API の既定の5分）。
CACHE_TTL = {"5m": None, "1h": "1h"}


def traits(model):
    """差し替え先の制約。知らない名前は**いちばん厳しい側**に倒す。

    一覧に無い名前を書くのは新しいモデルを試すときなので、
    新しい世代の制約を当てておく方が 400 を踏みにくい。
    """
    for prefix, found in TRAITS:
        if model == prefix or model.startswith(prefix + "-"):
            return found
    return STRICT


# --------------------------------------------------------------------------
# 要求本文の手直し
# --------------------------------------------------------------------------
def fix(body, target, effort):
    """本文を target 向けに直す。書き換えた項目名の列を返す。"""
    changed = []
    body["model"] = target
    found = traits(target)

    if found["sampling"]:
        for key in ("temperature", "top_p", "top_k"):
            if key in body:
                del body[key]
                changed.append("-" + key)

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        kind = thinking.get("type")
        if kind == "enabled" and found["budget"]:
            # 固定の予算は廃止。同じ「考える」は adaptive で表す。
            new = {"type": "adaptive"}
            if "display" in thinking:
                new["display"] = thinking["display"]
            body["thinking"] = new
            changed.append("thinking enabled->adaptive")
        elif kind == "disabled" and (found["no_disable"] or found["off_upto_high"]):
            config = body.get("output_config")
            sent = effort if effort != "keep" else (
                config.get("effort") if isinstance(config, dict) else None)
            if found["off_upto_high"] and sent in ("xhigh", "max"):
                # この推論量では切る指定そのものを断る。
                del body["thinking"]
                changed.append("-thinking disabled")
            elif found["between_tools"]:
                # 切り方が別の名前になっただけ。ゲームの「考えない」をそのまま通す。
                body["thinking"] = {"type": "between_tools"}
                changed.append("thinking disabled->between_tools")
            elif found["no_disable"]:
                # 切れないモデルには指定ごと外す（既定で adaptive になる）。
                # 速さは effort で抑える。
                del body["thinking"]
                changed.append("-thinking disabled")

    choice = body.get("tool_choice")
    if (found["no_forced"] and isinstance(choice, dict)
            and choice.get("type") in ("any", "tool")):
        new = {"type": "auto"}
        if "disable_parallel_tool_use" in choice:
            new["disable_parallel_tool_use"] = choice["disable_parallel_tool_use"]
        body["tool_choice"] = new
        changed.append("tool_choice {}->auto".format(choice.get("type")))

    config = body.get("output_config")
    if found["effort"]:
        if effort != "keep":
            config = dict(config) if isinstance(config, dict) else {}
            if config.get("effort") != effort:
                config["effort"] = effort
                body["output_config"] = config
                changed.append("effort ->{}".format(effort))
    elif isinstance(config, dict) and "effort" in config:
        config = dict(config)
        del config["effort"]
        if config:
            body["output_config"] = config
        else:
            del body["output_config"]
        changed.append("-effort")
    return changed


def _dump(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(value)


def segments(body):
    """本文を、API が控えを組む順（tools → system → messages）の区切りの列にする。

    戻りは `(区切りの列, 置き場の列)`。区切りは `(役, 本文, 印を置けるか)`、
    置き場は印を付けるときに本文のどこを書き換えるか（`(場所, 番号, ブロックの番号 or None)`）。
    印を置けるのは文字のブロックだけ。tools と出力の形は比べるためだけに入れる
    （変われば控えも変わるので、違う種類として見分けたい）。
    """
    segs, spots = [], []
    for key in ("tools", "output_format"):
        if body.get(key):
            segs.append((key, _dump(body[key]), False))
            spots.append(None)
    config = body.get("output_config")
    if isinstance(config, dict) and config.get("format"):
        segs.append(("format", _dump(config["format"]), False))
        spots.append(None)

    def add(role, content, where):
        if isinstance(content, str):
            segs.append((role, content, True))
            spots.append(where + (None,))
        elif isinstance(content, list):
            for index, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "text" \
                        and isinstance(block.get("text"), str):
                    segs.append((role, block["text"], True))
                else:
                    segs.append((role, _dump(block), False))
                spots.append(where + (index,))

    add("system", body.get("system"), ("system", None))
    for number, message in enumerate(body.get("messages") or []):
        if isinstance(message, dict):
            add(str(message.get("role")), message.get("content"), ("messages", number))
    return segs, spots


def _split_blocks(blocks, index, chars, mark):
    """文字のブロックの列の index 番目を chars 字で分け、前半に印を付けた新しい列を返す。"""
    block = blocks[index]
    text = block["text"]
    head = dict(block, text=text[:chars], cache_control=mark)
    rest = [{"type": "text", "text": text[chars:]}] if chars < len(text) else []
    return list(blocks[:index]) + [head] + rest + list(blocks[index + 1:])


def mark_shared_prefix(body, prefixes, ttl):
    """前回の同じ種類の頼みと先頭から一致した所に cache_control を付ける。書き換えた項目名の列を返す。

    置き方は `prefix_cache.py`。ゲームが自分で cache_control を付けていたら触らない。
    """
    if "cache_control" in _dump(body.get("system")) + _dump(body.get("messages")):
        return []
    segs, spots = segments(body)
    cut = prefixes.cut(segs)
    if cut is None:
        return []
    index, chars = cut
    place, number, block = spots[index]
    mark = {"type": "ephemeral"}
    if ttl:
        mark["ttl"] = ttl
    if place == "system":
        content = body["system"]
    else:
        messages = list(body["messages"])
        message = dict(messages[number])
        messages[number] = message
        body["messages"] = messages
        content = message["content"]
    blocks = [{"type": "text", "text": content}] if block is None else content
    blocks = _split_blocks(blocks, 0 if block is None else block, chars, mark)
    if place == "system":
        body["system"] = blocks
    else:
        message["content"] = blocks
    total = sum(len(s[1]) for s in segs)
    shared = sum(len(s[1]) for s in segs[:index]) + chars
    return ["cache mark {}/{}{}".format(shared, total, " " + ttl if ttl else "")]


def usage_line(result):
    """応答の usage から、キャッシュの書き込みと読み出しの数を1行にする。読めなければ None。"""
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    return "input {} / cache write {} / cache read {}".format(
        getattr(usage, "input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "cache_read_input_tokens", None))


def install(ctx, target, effort, cache, log):
    """送信の1点を包む。差し替えもキャッシュも無ければ何も仕掛けない。

    `cache` は設定の値（`off` / `5m` / `1h`）。`CACHE_TTL` に無い値は `off` と同じ。
    required / safe の理由は `openai_side.install` と同じ。
    """
    caching = cache in CACHE_TTL
    if not (target or caching):
        return
    prefixes = Prefixes()

    @ctx.wrap(POST, required=False, safe=True, alias_scan=False)
    def claude_post(orig, self, path=None, *args, **kwargs):
        body = kwargs.get("body")
        if not (isinstance(body, dict) and body.get("model")
                and str(path or "").rstrip("/").endswith("/messages")):
            return orig(self, path, *args, **kwargs)
        source = body.get("model")
        sent_to = target or source
        body = dict(body)
        changed = []
        if target:
            changed += fix(body, target, effort)
        if caching:
            changed += mark_shared_prefix(body, prefixes, CACHE_TTL[cache])
        if source == sent_to and not changed:
            return orig(self, path, *args, **kwargs)
        log.swap("claude", source, sent_to, path, changed)
        result = orig(self, path, *args, **dict(kwargs, body=body))
        if caching and log.wants("usage"):
            # 当たったかは応答の usage でしか分からない。
            # ここで投げると safe=True が素の送信をやり直し、同じ頼みを2回払うので握る。
            try:
                line = usage_line(result)
            except Exception:
                line = None
            if line:
                log.write("usage", "[claude] usage: " + line)
        return result
