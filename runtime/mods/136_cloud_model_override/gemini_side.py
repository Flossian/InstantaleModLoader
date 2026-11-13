# -*- coding: utf-8 -*-
"""Gemini API 宛ての差し替え。入口（`cloud_model_override.py`）の `apply()` から `install()` を呼ぶ。

設定の値は引数で受け取り、入口の定数は読まない。

## ゲームの Gemini の経路

ゲームの設定画面のプロバイダ名は「Gemini API(Experimental)」（`config.json` の `cloud_llm_provider`）。
送信は `scripts.llm.request_llm_inference_gemini_test_streaming` で、
`google.genai`（ゲームの同梱は 1.52.0）の `models.generate_content_stream` を呼ぶ。
SDK はモデル名を本文ではなく **URL のパス**（`models/<モデル>:streamGenerateContent?alt=sse`）に入れ、
本文の dict（`generationConfig` など）と一緒に `BaseApiClient.request_streamed` へ渡す。
ストリームでない `generate_content` は `BaseApiClient.request` を通る。
この2つを包み、パスのモデル名と本文の思考の指定を直す。

## 名前だけでは通らないもの

ゲームが `generationConfig.thinkingConfig` に入れる値は、ゲームで選んだモデルで決まる
（送信モジュールの `_build_config` をゲームの中で呼んで確かめた。2026-11-13。VERIFICATION §3.71）。

    gemini-3.5-flash / 3-flash-preview / 3.1-flash-lite   thinking_budget=0（思考しない。`reasoning_models`）
    gemini-3.1-pro-preview                                 thinking_level=LOW
    gemma-4-31b-it / gemma-4-26b-a4b-it                     thinking_level=minimal（temperature=1.0 も）

`thinking_budget: 0` は、受けるモデルにはそのまま送る（Gemini 4 Hydrogen と 3.6 Flash は受けて思考せずに答えた）。
受けないモデル（3.5 Flash-Lite は `400 Request contains an invalid argument.`）と、受けるか測れていないモデルには、
外していちばん浅い段階で送る。
思考の段階はモデルごとに受ける幅が違い、Gemini 4 と Gemini 3.8 Flash は `minimal` を持たない
（Google の Thinking のページの表。2026-11-13。素のまま送ると `400 Thinking level MINIMAL is not supported`）。
受けない段階は、受ける幅のいちばん近い端へ寄せる（minimal なら low）。

## キャッシュ

Gemini 4 は暗黙のキャッシュだけで、印を付ける指定は無い（明示のキャッシュは受けない）。触らない。
"""

import re

try:
    from urllib.parse import urlsplit
except Exception:  # pragma: no cover - 3.10 には必ず在る
    urlsplit = None

#: SDK で、生成の要求が最後に通る2点（ストリームと一括）。
STREAMED = "google.genai._api_client:BaseApiClient.request_streamed"
REQUEST = "google.genai._api_client:BaseApiClient.request"

#: Gemini API の本家。Vertex AI や互換サーバー宛てはここに当たらない。
HOST = "generativelanguage.googleapis.com"

#: 生成の要求のパス。`models/<モデル>:<動詞>` の形だけを書き換える（トークン数の見積もりなどは触らない）。
PATH = re.compile(r"^(?P<head>/?models/)(?P<model>[^:/?]+)(?P<verb>:(?:streamGenerateContent|generateContent))(?P<tail>.*)$")

#: 思考の段階の並び（浅い順）。
LEVELS = ("minimal", "low", "medium", "high", "xhigh")

#: いちばん新しい世代の幅。表に無い名前に当てる（minimal を持たない側・thinking_budget を受けない側に倒す）。
STRICT = (("low", "medium", "high"), "medium", False)

#: モデルごとの (受ける段階, 送らないときの既定, thinking_budget を受けるか)。前方一致なので、長い名前を先に並べる。
#: 段階と既定は Google の Thinking のページの表（2026-11-13）。thinking_budget は実測（受けると測れたものだけ True）。
#: ゲームの一覧のモデルは、ゲームが送っている値から読んだ。
TRAITS = (
    ("gemini-4-hydrogen", (("low", "medium", "high", "xhigh"), "high", True)),
    ("gemini-4-carbon", (("low", "medium", "high"), "medium", False)),
    ("gemini-4-argon", (("low", "medium", "high"), "medium", False)),
    ("gemini-3.8-flash", (("low", "medium", "high"), "medium", False)),
    ("gemini-3.6-flash", (("minimal", "low", "medium", "high"), "medium", True)),
    ("gemini-3.5-flash-lite", (("minimal", "low", "medium", "high"), "minimal", False)),
    ("gemini-3.5-flash", (("minimal", "low", "medium", "high"), "medium", True)),
    ("gemini-3.1-pro", (("low", "medium", "high"), "high", False)),
    ("gemini-3.1-flash-lite", (("minimal", "low", "medium", "high"), "minimal", True)),
    ("gemini-3-flash", (("minimal", "low", "medium", "high"), "high", True)),
)

#: thinking_budget を 0 のまま送るときのタイトル画面の書き方。
BUDGET_ZERO = "budget 0"

#: 推論量を送らず API の既定に任せるときの書き方（タイトル画面。`claude_side` と同じ）。
DEFAULT_TEXT = "既定"
DEFAULT_MARK = "（既定）"


def traits(model):
    """差し替え先の (受ける段階, 既定, thinking_budget を受けるか)。知らない名前は新しい世代の幅に倒す。"""
    for prefix, found in TRAITS:
        if model == prefix or model.startswith(prefix + "-"):
            return found
    return STRICT


def uses_levels(model):
    """思考を段階で表すモデルか（Gemini 3 以降）。Gemma などは段階の指定を触らない。"""
    return model.startswith("gemini-") and not model.startswith(("gemini-1", "gemini-2"))


def fit_level(model, value):
    """`value` を差し替え先が受ける段階に寄せる。受けない段階は近い端へ。読めない値はそのまま返す。"""
    text = str(getattr(value, "value", value) or "").strip().lower()
    if text not in LEVELS:
        return value
    allowed = traits(model)[0]
    if text in allowed:
        return text
    rank = LEVELS.index(text)
    if rank < LEVELS.index(allowed[0]):
        return allowed[0]
    return allowed[-1]


def _first_key(mapping, *names):
    for name in names:
        if name in mapping:
            return name
    return None


def fix(body, target, effort):
    """本文の写しを target 向けに直す。書き換えた項目名の列を返す。

    本文の `generationConfig` と `thinkingConfig` は写しを作ってから書き換える（呼び出し側の dict は変えない）。
    SDK は `thinkingConfig` の中身を snake_case（`thinking_level`）のまま出すので、両方の綴りを見る。
    """
    changed = []
    if not uses_levels(target):
        return changed
    config_key = _first_key(body, "generationConfig", "generation_config")
    config = dict(body.get(config_key) or {}) if config_key else {}
    thinking_key = _first_key(config, "thinkingConfig", "thinking_config")
    thinking = dict(config.get(thinking_key) or {}) if thinking_key else {}
    level_key = _first_key(thinking, "thinking_level", "thinkingLevel") or "thinking_level"
    budget_key = _first_key(thinking, "thinking_budget", "thinkingBudget")
    wants = None if effort == "keep" else effort
    if budget_key is not None and (wants is not None or not traits(target)[2]):
        # 段階を指定するとき（両方は送らない）と、差し替え先が上限を受けないときは外して段階で送る。
        # ゲームが送る上限は 0（思考しない）なので、いちばん浅い段階に読む
        changed.append("-thinking_budget {}".format(thinking.pop(budget_key)))
        if wants is None and level_key not in thinking:
            wants = traits(target)[0][0]

    current = thinking.get(level_key)
    new = fit_level(target, wants or current) if (wants or current) else None
    if new is not None and str(getattr(current, "value", current) or "").lower() != new:
        thinking[level_key] = new
        changed.append("thinking_level {}->{}".format(getattr(current, "value", current), new))

    if changed:
        if thinking:
            config[thinking_key or "thinkingConfig"] = thinking
        elif thinking_key:
            del config[thinking_key]
        body[config_key or "generationConfig"] = config
    return changed


def swap_path(path, target):
    """`models/<モデル>:<動詞>` のモデル名を target にした (新しいパス, 元のモデル名)。生成の要求でなければ None。"""
    found = PATH.match(str(path or ""))
    if not found:
        return None
    return found.group("head") + target + found.group("verb") + found.group("tail"), found.group("model")


def model_in(path):
    """パスから元のモデル名を読む。生成の要求でなければ None。"""
    found = PATH.match(str(path or ""))
    return found.group("model") if found else None


#: ゲームが組む思考の指定（モデル名で決まる。送信モジュールの `_build_config` をゲームの中で呼んで確かめた）。
GAME_BUDGET_ZERO = ("gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.1-flash-lite")
GAME_LEVEL_LOW = ("gemini-3.1-pro-preview",)
GAME_LEVEL_MINIMAL = ("gemma-4-31b-it", "gemma-4-26b-a4b-it")


def game_level(game_model):
    """ゲームが組む思考の指定。段階なら段階の名前、`thinking_budget: 0` なら BUDGET_ZERO、分からなければ None。"""
    if game_model in GAME_BUDGET_ZERO:
        return BUDGET_ZERO
    if game_model in GAME_LEVEL_LOW:
        return "low"
    if game_model in GAME_LEVEL_MINIMAL:
        return "minimal"
    return None


def sent_effort(target, setting, game_model=None):
    """実際に送る思考の指定（タイトル画面に出す）。

    keep はゲームの組んだ値のまま（`game_model` で決まる）。上限 0 を受けないモデルにはいちばん浅い段階を出す。
    段階を送らないなら API の既定を印つきで出す。差し替えないときは target に game_model を、setting に keep を渡す。
    """
    if not (uses_levels(target or "") or target in GAME_LEVEL_MINIMAL):
        return None
    value = setting if setting and setting != "keep" else game_level(game_model)
    if value == BUDGET_ZERO:
        return BUDGET_ZERO if target == game_model or traits(target)[2] else traits(target)[0][0]
    if value:
        return fit_level(target, value) if uses_levels(target) else value
    for prefix, found in TRAITS:
        if target == prefix or target.startswith(prefix + "-"):
            return found[1] + DEFAULT_MARK
    return DEFAULT_TEXT


def _host(client):
    """SDK のクライアントの宛先のホスト名。Vertex AI 宛てなら空。"""
    try:
        if getattr(client, "vertexai", False):
            return ""
        options = getattr(client, "_http_options", None)
        url = options.get("base_url") if isinstance(options, dict) else getattr(options, "base_url", None)
    except Exception:
        return ""
    url = str(url or "")
    if urlsplit is None:
        return url
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def install(ctx, target, effort, log):
    """生成の要求の2点を包む。差し替え先も推論量の指定も無ければ何も仕掛けない。

    推論量は差し替えるときだけ効かせる（OpenAI / Claude と同じ）。
    required / safe の理由は `openai_side.install` と同じ。
    """
    if not target:
        return

    def rewrite(self, path, request_dict):
        """(パス, 本文) を直した組と、ログの材料を返す。触らないなら None。"""
        if not isinstance(request_dict, dict) or _host(self) != HOST:
            return None
        swapped = swap_path(path, target)
        if swapped is None:
            return None
        new_path, source = swapped
        body = dict(request_dict)
        changed = fix(body, target, effort)
        if source == target and not changed:
            return None
        log.swap("gemini", source, target, path, changed)
        return new_path, body

    @ctx.wrap(STREAMED, required=False, safe=True, alias_scan=False)
    def gemini_streamed(orig, self, http_method, path, request_dict, *args, **kwargs):
        found = rewrite(self, path, request_dict)
        if found is None:
            return orig(self, http_method, path, request_dict, *args, **kwargs)
        return orig(self, http_method, found[0], found[1], *args, **kwargs)

    @ctx.wrap(REQUEST, required=False, safe=True, alias_scan=False)
    def gemini_request(orig, self, http_method, path, request_dict, *args, **kwargs):
        found = rewrite(self, path, request_dict)
        if found is None:
            return orig(self, http_method, path, request_dict, *args, **kwargs)
        return orig(self, http_method, found[0], found[1], *args, **kwargs)
