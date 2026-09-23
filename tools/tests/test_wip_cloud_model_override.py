# -*- coding: utf-8 -*-
"""900_cloud_model_override をゲーム抜きで通す。

    python tools/tests/test_wip_cloud_model_override.py

見るのは MOD が自分で決めている所だけ:
送信の1点（`SyncAPIClient.post`）で本文の写しを直すこと、宛先が本家でなければ触らないこと、
差し替え先が断る引数の直し（OpenAI の effort / max_tokens、Claude の sampling・thinking・
tool_choice・effort）。SDK そのものは読み込まない。
"""
import importlib.util
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MODS_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime", "mods"))
folder = [n for n in os.listdir(MODS_DIR) if n.endswith("_cloud_model_override")][0]
folder = os.path.join(MODS_DIR, folder)
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location(
    "cloud_model_override_under_test", os.path.join(folder, manifest["entry"]))
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)
for key, spec_ in manifest["settings"].items():
    assert getattr(MOD, key) == spec_["default"], key
    if spec_["type"] == "choice":
        assert spec_["default"] in spec_["values"], key


class Ctx(object):
    def __init__(self):
        self.hooks = {}
        self.lines = []

    def log(self, text):
        self.lines.append(text)

    def wrap(self, target, **kwargs):
        def deco(fn):
            self.hooks[target] = fn
            return fn
        return deco


class Client(object):
    def __init__(self, base_url):
        self.base_url = base_url


def orig(self, path, *args, **kwargs):
    return kwargs["body"]


OPENAI = Client("https://api.openai.com/v1/")
LOCAL = Client("http://127.0.0.1:8080/v1")

# ---------------------------------------------------------------- 既定: OpenAI だけ仕掛ける
ctx = Ctx()
MOD.apply(ctx)
assert MOD.OPENAI_POST in ctx.hooks and MOD.CLAUDE_POST not in ctx.hooks
post = ctx.hooks[MOD.OPENAI_POST]

body = {"model": "gpt-5", "input": [], "reasoning": {"effort": "minimal"}}
sent = post(orig, OPENAI, "/responses", body=body)
assert sent == {"model": "gpt-6-luna", "input": [], "reasoning": {"effort": "none"}}, sent
assert body["model"] == "gpt-5" and body["reasoning"]["effort"] == "minimal"   # 呼び出し側は変えない

body = {"model": "gpt-5.5", "input": [], "reasoning": {"effort": "none"}}
assert post(orig, OPENAI, "/responses", body=body)["reasoning"] == {"effort": "none"}
assert post(orig, LOCAL, "/responses", body=body) is body                      # 互換サーバーは素通し

sent = post(orig, OPENAI, "/chat/completions",
            body={"model": "gpt-4.1", "messages": [], "max_tokens": 100})
assert sent == {"model": "gpt-6-luna", "messages": [], "max_completion_tokens": 100}, sent

# GPT-6 Astra は none を断るので low へ。GPT-5 以降は temperature 等を外す
fixed = {"model": "gpt-5.5", "input": [], "reasoning": {"effort": "none"}, "temperature": 0.7}
MOD._fix_openai(fixed, "gpt-6-astra", "keep", "/responses")
assert fixed == {"model": "gpt-6-astra", "input": [], "reasoning": {"effort": "low"}}, fixed
fixed = {"model": "gpt-5", "reasoning": {"effort": "minimal"}}
MOD._fix_openai(fixed, "gpt-6-sol", "keep", "/responses")
assert fixed["reasoning"] == {"effort": "none"}, fixed
fixed = {"model": "gpt-4.1", "messages": [], "reasoning_effort": "none"}
MOD._fix_openai(fixed, "gpt-6-astra", "keep", "/chat/completions")
assert fixed["reasoning_effort"] == "low", fixed
assert MOD.openai_generation("gpt-6-sol") == 6 and MOD.openai_generation("gpt-5.6-luna") == 5
assert MOD.openai_generation("gpt-4.1") == 4 and MOD.openai_generation("o3") == 0

# 推論量の指定は同じモデルでも効く
MOD.OPENAI_EFFORT = "low"
MOD.OPENAI_CUSTOM = "gpt-5.5"
ctx = Ctx()
MOD.apply(ctx)
sent = ctx.hooks[MOD.OPENAI_POST](orig, OPENAI, "/responses",
                                  body={"model": "gpt-5.5", "reasoning": {"effort": "none"}})
assert sent == {"model": "gpt-5.5", "reasoning": {"effort": "low"}}, sent
MOD.OPENAI_EFFORT, MOD.OPENAI_CUSTOM = "keep", ""

# off なら仕掛けない
MOD.OPENAI_MODEL = "off"
ctx = Ctx()
MOD.apply(ctx)
assert not ctx.hooks
MOD.OPENAI_MODEL = "gpt-6-luna"

# ---------------------------------------------------------------- Claude
MOD.CLAUDE_MODEL = "claude-opus-5-5"
ctx = Ctx()
MOD.apply(ctx)
post = ctx.hooks[MOD.CLAUDE_POST]
body = {"model": "claude-sonnet-5", "max_tokens": 30000, "temperature": 0.7, "top_k": 5,
        "thinking": {"type": "disabled"}, "tool_choice": {"type": "tool", "name": "x"},
        "messages": []}
sent = post(orig, OPENAI, "/v1/messages", body=body)
assert sent == {"model": "claude-opus-5-5", "max_tokens": 30000, "messages": [],
                "tool_choice": {"type": "auto"}, "output_config": {"effort": "low"}}, sent
assert body["model"] == "claude-sonnet-5" and "temperature" in body
assert post(orig, OPENAI, "/v1/messages/count_tokens", body=body) is body       # 送信以外は触らない

# budget_tokens は adaptive へ
fixed = {"model": "x", "thinking": {"type": "enabled", "budget_tokens": 2048}}
MOD._fix_claude(fixed, "claude-sonnet-5", "keep")
assert fixed == {"model": "claude-sonnet-5", "thinking": {"type": "adaptive"}}, fixed

# 思考を切れるモデルでは disabled を残す
fixed = {"model": "x", "thinking": {"type": "disabled"}}
MOD._fix_claude(fixed, "claude-opus-4-8", "keep")
assert fixed["thinking"] == {"type": "disabled"}

# 古い世代は sampling を残し、Haiku 4.5 には effort を送らない
fixed = {"model": "x", "temperature": 0.5, "output_config": {"effort": "high"}}
MOD._fix_claude(fixed, "claude-haiku-4-5", "low")
assert fixed == {"model": "claude-haiku-4-5", "temperature": 0.5}, fixed

# 知らない名前はいちばん厳しい側
assert MOD.claude_traits("claude-someday-9") == MOD._STRICT
assert MOD.claude_traits("claude-opus-5")["no_disable"] is False
assert MOD.claude_traits("claude-opus-5-5")["no_disable"] is True

# ログは LOG_LIMIT 回まで
MOD.LOG_LIMIT = 1
ctx = Ctx()
MOD.apply(ctx)
for _ in range(3):
    ctx.hooks[MOD.CLAUDE_POST](orig, OPENAI, "/v1/messages",
                               body={"model": "claude-sonnet-5", "messages": []})
assert sum("[claude]" in line for line in ctx.lines) == 1, ctx.lines
MOD.LOG_LIMIT, MOD.CLAUDE_MODEL = 3, "off"

print("ok")
