# -*- coding: utf-8 -*-
"""OpenAI API 宛ての差し替え。入口（`cloud_model_override.py`）の `apply()` から `install()` を呼ぶ。

設定の値は引数で受け取り、入口の定数は読まない。

## ゲームの OpenAI の経路

`scripts.llm.request_llm_inference_openai:send_request` は、**モデル名で
API の経路を分けている**:

    gpt-5 / -mini / -nano                          responses.parse, effort="minimal"
    gpt-5.5 / 5.4 / 5.4-mini / 5.4-nano / 5.2 / 5.1  responses.parse, effort="none"
    それ以外                                        beta.chat.completions, max_tokens=

3番目は古い経路で、GPT-5 系は
`max_tokens` を受け付けない（`max_completion_tokens` が要る）。
`config.json` の `cloud_llm` に新しい名前を直接書くと、
名前は届くが壊れた経路に落ちる。
加えて価格表（`calculate_price`）にも無いキーになる。

## 名前だけでは通らないもの

`effort="minimal"` は GPT-5（無印）系だけのもの。他へ送るなら `"none"` に読み替える。
GPT-6 Astra と GPT-6.1 Sol は `"none"` も断るので `"low"` に上げる。
GPT-5 以降は `temperature` / `top_p` / `top_logprobs` を断り、
古い経路（chat.completions）の `max_tokens` は `max_completion_tokens` に移す。

OpenAI 互換の別サーバー（`any_server` / Alibaba）も `openai` の SDK を通るので、
**宛先が api.openai.com のときだけ**差し替える。

## プロンプトキャッシュ

GPT-5.6 以降の既定（`prompt_cache_options.mode` が `implicit`）は、
最新の発言の末尾に OpenAI が自動で印を置き、書き込みを入力の 1.25 倍で払う。
ゲームの頼みは末尾が毎回変わるので当たらない（2026-10-08 の実機で、19回で読み出し 0、
入力の 98% を書き込み。`prefix_cache.py`）。
設定で `explicit` に切り替え、印を置かない（`off`）か、
前回の同じ種類の頼みと先頭から一致した所にだけ置く（`stable`）。
GPT-5.5 以前は書き込みの割増しが無く、`prompt_cache_options` も受けないので触らない。
"""

import json

from .prefix_cache import Prefixes

try:
    from urllib.parse import urlsplit
except Exception:  # pragma: no cover - 3.10 には必ず在る
    urlsplit = None

#: SDK で、全リクエストが最後に通る1点。
POST = "openai._base_client:SyncAPIClient.post"

#: OpenAI の本家。互換サーバー宛てはここに当たらない。
HOST = "api.openai.com"

#: effort="minimal" を受けるのは GPT-5 無印の3つだけ。
MINIMAL_OK = ("gpt-5", "gpt-5-mini", "gpt-5-nano")

#: effort="none" を断るモデル（前方一致）。いちばん浅い "low" に上げる。
NO_NONE = ("gpt-6-astra", "gpt-6.1-sol")

#: 推論モデル（GPT-5 以降）が断るサンプリング系の引数。
SAMPLING = ("temperature", "top_p", "top_logprobs")


#: キャッシュの設定値。KEEP はゲームのまま（何も送らない）。
CACHE_KEEP, CACHE_OFF, CACHE_STABLE = "keep", "off", "stable"

#: `prompt_cache_options` を受ける最初の版。
CACHE_OPTIONS_SINCE = (5, 6)

#: 印を置ける発言の役。assistant の発言（output_text）には置かない。
MARKABLE_ROLES = ("system", "developer", "user")


def version(model):
    """`gpt-6.1-sol` -> (6, 1)、`gpt-5.6-luna` -> (5, 6)、`gpt-6-luna` -> (6, 0)。読めなければ None。"""
    if not model.startswith("gpt-"):
        return None
    head = model[4:].split("-", 1)[0].split(".")
    if not all(part.isdigit() for part in head) or not head:
        return None
    numbers = [int(part) for part in head] + [0]
    return numbers[0], numbers[1]


def takes_cache_options(model):
    found = version(model)
    return found is not None and found >= CACHE_OPTIONS_SINCE


def _dump(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(value)


def segments(body):
    """responses の本文を区切りの列にする（`claude_side.segments` と同じ形）。

    置き場は `(input の番号, 部分の番号 or None)`。出力の形（`text`）と instructions は比べるためだけに入れる。
    """
    segs, spots = [], []
    for key in ("tools", "text", "instructions"):
        if body.get(key):
            segs.append((key, _dump(body[key]), False))
            spots.append(None)
    items = body.get("input")
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    for number, item in enumerate(items or []):
        role = item.get("role") if isinstance(item, dict) else None
        content = item.get("content") if isinstance(item, dict) else None
        markable = role in MARKABLE_ROLES
        if isinstance(content, str):
            segs.append((str(role), content, markable))
            spots.append((number, None))
        elif isinstance(content, list):
            for index, part in enumerate(content):
                if isinstance(part, dict) and part.get("type") == "input_text" \
                        and isinstance(part.get("text"), str):
                    segs.append((str(role), part["text"], markable))
                else:
                    segs.append((str(role), _dump(part), False))
                spots.append((number, index))
        else:
            segs.append((str(role), _dump(item), False))
            spots.append(None)
    return segs, spots


def mark_shared_prefix(body, prefixes):
    """前回の同じ種類の頼みと先頭から一致した所に印を置く。書き換えた項目名の列を返す。

    印は input の文字の部分に `prompt_cache_breakpoint: {"mode": "explicit"}`。
    本文の途中なら2つに分けて前半に付ける（読む中身は変わらない）。
    """
    if isinstance(body.get("input"), str):
        body["input"] = [{"role": "user", "content": body["input"]}]
    segs, spots = segments(body)
    cut = prefixes.cut(segs)
    if cut is None:
        return []
    index, chars = cut
    number, part = spots[index]
    items = list(body["input"])
    item = dict(items[number])
    items[number] = item
    body["input"] = items
    content = item["content"]
    parts = [{"type": "input_text", "text": content}] if part is None else list(content)
    at = 0 if part is None else part
    text = parts[at]["text"]
    head = dict(parts[at], text=text[:chars], prompt_cache_breakpoint={"mode": "explicit"})
    rest = [{"type": "input_text", "text": text[chars:]}] if chars < len(text) else []
    item["content"] = parts[:at] + [head] + rest + parts[at + 1:]
    total = sum(len(s[1]) for s in segs)
    shared = sum(len(s[1]) for s in segs[:index]) + chars
    return ["cache mark {}/{}".format(shared, total)]


def apply_cache(body, target, cache, prefixes, path):
    """キャッシュの設定を本文に効かせる。書き換えた項目名の列を返す。"""
    if cache not in (CACHE_OFF, CACHE_STABLE) or not takes_cache_options(target):
        return []
    if not str(path or "").rstrip("/").endswith("/responses"):
        return []
    options = body.get("prompt_cache_options")
    if isinstance(options, dict) and options.get("mode") == "explicit":
        return []       # ゲームが自分で決めている
    body["prompt_cache_options"] = dict(options or {}, mode="explicit")
    if cache == CACHE_OFF:
        return ["cache off"]
    return mark_shared_prefix(body, prefixes) or ["cache explicit"]


def generation(model):
    """`gpt-6-sol` -> 6、`gpt-5.6-luna` -> 5。読めなければ 0。"""
    if not model.startswith("gpt-"):
        return 0
    head = model[4:].split("-", 1)[0].split(".", 1)[0]
    return int(head) if head.isdigit() else 0


def _effort(target, value):
    """差し替え先が受ける推論量に寄せる。"""
    if value == "minimal" and target not in MINIMAL_OK:
        value = "none"
    if value == "none" and target.startswith(NO_NONE):
        value = "low"
    return value


def fix(body, target, effort, path):
    """本文を target 向けに直す。書き換えた項目名の列を返す。"""
    changed = []
    body["model"] = target
    wants = None if effort == "keep" else effort

    # responses API: reasoning={"effort": ...}
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and "effort" in reasoning:
        value = reasoning.get("effort")
        new = _effort(target, wants or value)
        if new != value:
            body["reasoning"] = dict(reasoning, effort=new)
            changed.append("effort {}->{}".format(value, new))
    elif wants and str(path or "").rstrip("/").endswith("/responses"):
        new = _effort(target, wants)
        body["reasoning"] = dict(reasoning or {}, effort=new)
        changed.append("effort ->{}".format(new))

    # chat.completions: reasoning_effort / max_tokens
    if "reasoning_effort" in body or (wants and "messages" in body):
        value = body.get("reasoning_effort")
        new = wants or value
        if new is not None:
            new = _effort(target, new)
        if new is not None and new != value:
            body["reasoning_effort"] = new
            changed.append("reasoning_effort {}->{}".format(value, new))
    if generation(target) >= 5:
        for key in SAMPLING:
            if key in body:
                del body[key]
                changed.append("-" + key)
        if "max_tokens" in body:
            body.setdefault("max_completion_tokens", body.pop("max_tokens"))
            changed.append("max_tokens->max_completion_tokens")
    return changed


def usage_line(result):
    """応答の usage から、入力とキャッシュの数を1行にする。読めなければ None。

    responses は `input_tokens_details`、chat.completions は `prompt_tokens_details` に入る。
    `cache_write_tokens` は GPT-5.6 以降の書き込み（1.25 倍で払う分）。
    """
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    details = (getattr(usage, "input_tokens_details", None)
               or getattr(usage, "prompt_tokens_details", None))
    total = getattr(usage, "input_tokens", None)
    if total is None:
        total = getattr(usage, "prompt_tokens", None)
    return "input {} / cache write {} / cache read {}".format(
        total,
        getattr(details, "cache_write_tokens", None),
        getattr(details, "cached_tokens", None))


def _host(client):
    try:
        url = str(getattr(client, "base_url", "") or "")
    except Exception:
        return ""
    if urlsplit is None:
        return url
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def install(ctx, target, effort, cache, log):
    """送信の1点を包む。差し替えもキャッシュの指定も無ければ何も仕掛けない。

    `cache` は設定の値（`keep` / `off` / `stable`）。
    required=False: 使っていないプロバイダの SDK は読み込まれない。
    モジュールが現れた時点でローダが当て直すので、ここでは黙って見送る。
    safe=True: ここが壊れても素の送信に落とす（LLM が止まる方が損害が大きい）。
    """
    if not (target or cache in (CACHE_OFF, CACHE_STABLE)):
        return
    prefixes = Prefixes()

    @ctx.wrap(POST, required=False, safe=True, alias_scan=False)
    def openai_post(orig, self, path=None, *args, **kwargs):
        body = kwargs.get("body")
        # 同じモデルを選んでいても通す（推論量の指定だけを効かせるため）。
        if not (isinstance(body, dict) and body.get("model")
                and _host(self) == HOST):
            return orig(self, path, *args, **kwargs)
        source = body.get("model")
        sent_to = target or source
        # 呼び出し側の dict は変えず、浅い写しを直す。
        body = dict(body)
        changed = fix(body, target, effort, path) if target else []
        changed += apply_cache(body, sent_to, cache, prefixes, path)
        if not (source == sent_to and not changed):
            kwargs = dict(kwargs, body=body)
            log.swap("openai", source, sent_to, path, changed)
        result = orig(self, path, *args, **kwargs)
        if log.wants("usage"):
            # キャッシュが当たっているかは応答の usage でしか分からない（GPT-5.6 以降は既定で
            # 最新の発言の末尾に印が付く。DOC.md「プロンプトキャッシュ」）。
            # ここで投げると safe=True が素の送信をやり直し、同じ頼みを2回払うので握る。
            try:
                line = usage_line(result)
            except Exception:
                line = None
            if line:
                log.write("usage", "[openai] usage: " + line)
        return result
