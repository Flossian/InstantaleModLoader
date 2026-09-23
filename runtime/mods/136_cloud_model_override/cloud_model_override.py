# -*- coding: utf-8 -*-
"""ゲームが知らないクラウドモデルを使う。**送信直前にモデル名を差し替える**。

OpenAI と Claude（Anthropic）の両方に対応する。
差し替え先は設定画面の一覧から選ぶ（一覧に無い名前は「一覧に無いモデル」の欄に書く）。

## なぜ送信直前か

ゲームのクラウド対応は、
モデル名の一覧がコード定数として埋まっている（`scripts.hud.hud_option:OptionLLMScreen.get_cloud_llm_list` と
`hud_auto_configuration` に同じ表がある）。
一覧に無い名前は設定画面から選べない。

一覧に足すだけでは足りない。
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

そこで名前を足しに行かず、**ゲームには一覧にある名前のまま持たせ、
HTTP に出る直前で差し替える**。
ゲームは一覧のモデルのつもりで要求を組み立て、実際に飛ぶのは選んだモデル。

## どこに仕掛けるか

    openai._base_client:SyncAPIClient.post
    anthropic._base_client:SyncAPIClient.post

どちらの SDK も同じ生成器（Stainless）で作られていて、
`responses.parse` も `messages.create` も最後はここを通る。
`body` は送信するそのままの dict なので、`body["model"]` を書き換えれば済む。

`body` の dict は写しを作って差し替える。
SDK が呼び出し側の dict をそのまま握っている場合に、
こちらの都合で中身を変えないため。

## 名前だけでは通らないもの

新しいモデルほど、古いモデルが受けていた引数を 400 で断る。
ゲームが組み立てた要求は**ゲームが選んだモデル向け**なので、
差し替え先が断る引数だけを直してから送る（`_fix_openai` / `_fix_claude`）。

  * OpenAI: `effort="minimal"` は GPT-5（無印）系だけのもの。他へ送るなら `"none"` に読み替える。
    GPT-6 Astra は `"none"` も断るので `"low"` に上げる。
    GPT-5 以降は `temperature` / `top_p` / `top_logprobs` を断り、
    古い経路（chat.completions）の `max_tokens` は `max_completion_tokens` に移す
  * Claude: 新しい世代は `temperature` / `top_p` / `top_k` と
    `thinking.budget_tokens` を断る。Opus 5.5 / Fable 系は思考を切れず、
    強制ツール指定（`tool_choice` の `any` / `tool`）も断る

## 割り切り

  * ゲーム内のコスト表示はずれる。価格計算はゲームが選んだモデルの
    単価で行う。表示だけの話で、実際の課金には関わらない
  * 設定画面の表示もゲームが選んだモデルのまま
  * OpenAI 互換の別サーバー（`any_server` / Alibaba）も `openai` の SDK を通るので、
    **宛先が api.openai.com のときだけ**差し替える
"""

try:
    from urllib.parse import urlsplit
except Exception:  # pragma: no cover - 3.10 には必ず在る
    urlsplit = None

# 「差し替えない」を表す選択肢。一覧の先頭に置く。
OFF = "off"

# OpenAI で実際に送るモデル。
# **`mod.json` の "default" と "values" に揃えること**（`tools/check_mods.py` が
# AST で突き合わせる。TECH.md §3.8.3）。
OPENAI_MODEL = "gpt-6-luna"

# 一覧に無い OpenAI のモデル名。空でなければ OPENAI_MODEL より優先する。
OPENAI_CUSTOM = ""

# OpenAI の推論量。"keep" はゲームが組み立てた値のまま。
OPENAI_EFFORT = "keep"

# Claude で実際に送るモデル。既定は差し替えない（ゲームの一覧に claude-sonnet-5 が在る）。
CLAUDE_MODEL = "off"

# 一覧に無い Claude のモデル名。空でなければ CLAUDE_MODEL より優先する。
CLAUDE_CUSTOM = ""

# Claude の推論量（output_config.effort）。"keep" はゲームが組み立てた値のまま。
# ゲームは応答の速さが効くので low を既定にしてある。
CLAUDE_EFFORT = "low"

# 差し替えをログに出す回数。
# 毎回出すとログが埋まるので先頭だけ。
LOG_LIMIT = 3

#: 各 SDK で、全リクエストが最後に通る1点。
OPENAI_POST = "openai._base_client:SyncAPIClient.post"
CLAUDE_POST = "anthropic._base_client:SyncAPIClient.post"

#: OpenAI の本家。互換サーバー宛てはここに当たらない。
OPENAI_HOST = "api.openai.com"

#: effort="minimal" を受けるのは GPT-5 無印の3つだけ。
OPENAI_MINIMAL_OK = ("gpt-5", "gpt-5-mini", "gpt-5-nano")

#: effort="none" を断るモデル（前方一致）。いちばん浅い "low" に上げる。
OPENAI_NO_NONE = ("gpt-6-astra",)

#: 推論モデル（GPT-5 以降）が断るサンプリング系の引数。
OPENAI_SAMPLING = ("temperature", "top_p", "top_logprobs")


def openai_generation(model):
    """`gpt-6-sol` -> 6、`gpt-5.6-luna` -> 5。読めなければ 0。"""
    if not model.startswith("gpt-"):
        return 0
    head = model[4:].split("-", 1)[0].split(".", 1)[0]
    return int(head) if head.isdigit() else 0


def _openai_effort(target, value):
    """差し替え先が受ける推論量に寄せる。"""
    if value == "minimal" and target not in OPENAI_MINIMAL_OK:
        value = "none"
    if value == "none" and target.startswith(OPENAI_NO_NONE):
        value = "low"
    return value

# --------------------------------------------------------------------------
# Claude のモデルごとの制約
# --------------------------------------------------------------------------
# sampling:     temperature / top_p / top_k を断る
# budget:       thinking {type: enabled, budget_tokens} を断る
# no_disable:   thinking {type: disabled} を断る（思考を切れない）
# no_forced:    tool_choice の any / tool を断る
# effort:       output_config.effort を受ける
_STRICT = {"sampling": True, "budget": True, "no_disable": True,
           "no_forced": True, "effort": True}
_CLAUDE_TRAITS = (
    # 前方一致なので、長い名前を先に並べる。
    ("claude-opus-5-5", _STRICT),
    ("claude-fable-5-1", _STRICT),
    ("claude-mythos-5-1", _STRICT),
    ("claude-fable-5", dict(_STRICT, no_forced=False)),
    ("claude-mythos-5", dict(_STRICT, no_forced=False)),
    ("claude-opus-5", dict(_STRICT, no_disable=False, no_forced=False)),
    ("claude-sonnet-5", dict(_STRICT, no_disable=False, no_forced=False)),
    ("claude-opus-4-8", dict(_STRICT, no_disable=False, no_forced=False)),
    ("claude-opus-4-7", dict(_STRICT, no_disable=False, no_forced=False)),
    ("claude-opus-4-6", {"sampling": False, "budget": False, "no_disable": False,
                         "no_forced": False, "effort": True}),
    ("claude-sonnet-4-6", {"sampling": False, "budget": False, "no_disable": False,
                           "no_forced": False, "effort": True}),
    ("claude-haiku-4-5", {"sampling": False, "budget": False, "no_disable": False,
                          "no_forced": False, "effort": False}),
)


def claude_traits(model):
    """差し替え先の制約。知らない名前は**いちばん厳しい側**に倒す。

    一覧に無い名前を書くのは新しいモデルを試すときなので、
    新しい世代の制約を当てておく方が 400 を踏みにくい。
    """
    for prefix, traits in _CLAUDE_TRAITS:
        if model == prefix or model.startswith(prefix + "-"):
            return traits
    return _STRICT


def _pick(custom, chosen):
    """一覧に無い名前が書いてあればそちら、無ければ一覧の選択。"off" は None。"""
    name = (custom or "").strip() or (chosen or "").strip()
    if not name or name == OFF:
        return None
    return name


# --------------------------------------------------------------------------
# 要求本文の手直し
# --------------------------------------------------------------------------
def _fix_openai(body, target, effort, path):
    """OpenAI の本文を target 向けに直す。書き換えた項目名の列を返す。"""
    changed = []
    body["model"] = target
    wants = None if effort == "keep" else effort

    # responses API: reasoning={"effort": ...}
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and "effort" in reasoning:
        value = reasoning.get("effort")
        new = _openai_effort(target, wants or value)
        if new != value:
            body["reasoning"] = dict(reasoning, effort=new)
            changed.append("effort {}->{}".format(value, new))
    elif wants and str(path or "").rstrip("/").endswith("/responses"):
        new = _openai_effort(target, wants)
        body["reasoning"] = dict(reasoning or {}, effort=new)
        changed.append("effort ->{}".format(new))

    # chat.completions: reasoning_effort / max_tokens
    if "reasoning_effort" in body or (wants and "messages" in body):
        value = body.get("reasoning_effort")
        new = wants or value
        if new is not None:
            new = _openai_effort(target, new)
        if new is not None and new != value:
            body["reasoning_effort"] = new
            changed.append("reasoning_effort {}->{}".format(value, new))
    if openai_generation(target) >= 5:
        for key in OPENAI_SAMPLING:
            if key in body:
                del body[key]
                changed.append("-" + key)
        if "max_tokens" in body:
            body.setdefault("max_completion_tokens", body.pop("max_tokens"))
            changed.append("max_tokens->max_completion_tokens")
    return changed


def _fix_claude(body, target, effort):
    """Claude の本文を target 向けに直す。書き換えた項目名の列を返す。"""
    changed = []
    body["model"] = target
    traits = claude_traits(target)

    if traits["sampling"]:
        for key in ("temperature", "top_p", "top_k"):
            if key in body:
                del body[key]
                changed.append("-" + key)

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        kind = thinking.get("type")
        if kind == "enabled" and traits["budget"]:
            # 固定の予算は廃止。同じ「考える」は adaptive で表す。
            new = {"type": "adaptive"}
            if "display" in thinking:
                new["display"] = thinking["display"]
            body["thinking"] = new
            changed.append("thinking enabled->adaptive")
        elif kind == "disabled" and traits["no_disable"]:
            # 切れないモデルには指定ごと外す（既定で adaptive になる）。
            # 速さは effort で抑える。
            del body["thinking"]
            changed.append("-thinking disabled")

    choice = body.get("tool_choice")
    if (traits["no_forced"] and isinstance(choice, dict)
            and choice.get("type") in ("any", "tool")):
        new = {"type": "auto"}
        if "disable_parallel_tool_use" in choice:
            new["disable_parallel_tool_use"] = choice["disable_parallel_tool_use"]
        body["tool_choice"] = new
        changed.append("tool_choice {}->auto".format(choice.get("type")))

    config = body.get("output_config")
    if traits["effort"]:
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


def apply(ctx):
    openai_target = _pick(OPENAI_CUSTOM, OPENAI_MODEL)
    claude_target = _pick(CLAUDE_CUSTOM, CLAUDE_MODEL)
    openai_effort = (OPENAI_EFFORT or "keep").strip() or "keep"
    claude_effort = (CLAUDE_EFFORT or "keep").strip() or "keep"

    state = {"logged": 0}

    def note(provider, source, target, path, changed):
        state["logged"] += 1
        if state["logged"] <= LOG_LIMIT:
            ctx.log("cloud model override: [{}] {} -> {} ({}){}".format(
                provider, source, target, path,
                " fixed: " + ", ".join(changed) if changed else ""))

    # 差し替え先が無い側は、フックごと仕掛けない。
    # 「何もしない包み」を残すより、通り道を素のままにしておく方が安い。
    #
    # required=False: 使っていないプロバイダの SDK は読み込まれない。
    # モジュールが現れた時点でローダが当て直すので、ここでは黙って見送る。
    # safe=True: ここが壊れても素の送信に落とす（LLM が止まる方が損害が大きい）。
    if openai_target:
        @ctx.wrap(OPENAI_POST, required=False, safe=True, alias_scan=False)
        def openai_post(orig, self, path=None, *args, **kwargs):
            body = kwargs.get("body")
            # 同じモデルを選んでいても通す（推論量の指定だけを効かせるため）。
            if (isinstance(body, dict) and body.get("model")
                    and _host(self) == OPENAI_HOST):
                source = body.get("model")
                # 呼び出し側の dict は変えず、浅い写しを直す。
                body = dict(body)
                changed = _fix_openai(body, openai_target, openai_effort, path)
                if source == openai_target and not changed:
                    return orig(self, path, *args, **kwargs)
                kwargs = dict(kwargs, body=body)
                note("openai", source, openai_target, path, changed)
            return orig(self, path, *args, **kwargs)

    if claude_target:
        @ctx.wrap(CLAUDE_POST, required=False, safe=True, alias_scan=False)
        def claude_post(orig, self, path=None, *args, **kwargs):
            body = kwargs.get("body")
            if (isinstance(body, dict) and body.get("model")
                    and str(path or "").rstrip("/").endswith("/messages")):
                source = body.get("model")
                body = dict(body)
                changed = _fix_claude(body, claude_target, claude_effort)
                if source == claude_target and not changed:
                    return orig(self, path, *args, **kwargs)
                kwargs = dict(kwargs, body=body)
                note("claude", source, claude_target, path, changed)
            return orig(self, path, *args, **kwargs)

    ctx.log("cloud model override: openai={} (effort {}) / claude={} (effort {})".format(
        openai_target or OFF, openai_effort, claude_target or OFF, claude_effort))
