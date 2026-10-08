# -*- coding: utf-8 -*-
"""136_cloud_model_override をゲーム抜きで通す。

    python tools/tests/test_cloud_model_override.py

見るのは MOD が自分で決めている所だけ:
送信の1点（`SyncAPIClient.post`）で本文の写しを直すこと、宛先が本家でなければ触らないこと、
差し替え先が断る引数の直し（OpenAI の effort / max_tokens、Claude の sampling・thinking・
tool_choice・effort）。SDK そのものは読み込まない。
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)
folder = [n for n in os.listdir(MODS_DIR) if n.endswith("_cloud_model_override")][0]
folder = os.path.join(MODS_DIR, folder)
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
# ローダと同じくパッケージとして読む（入口が `from . import openai_side` で隣を引くため）。
NAME = "cloud_model_override_under_test"
spec = importlib.util.spec_from_file_location(
    NAME, os.path.join(folder, manifest["entry"]), submodule_search_locations=[folder])
MOD = importlib.util.module_from_spec(spec)
sys.modules[NAME] = MOD
spec.loader.exec_module(MOD)
OA = sys.modules[NAME + ".openai_side"]
CL = sys.modules[NAME + ".claude_side"]
for key, spec_ in manifest["settings"].items():
    assert getattr(MOD, key) == spec_["default"], key
    if spec_["type"] == "choice":
        assert spec_["default"] in spec_["values"], key
# モデルの一覧は、off 以外すべてに表示名（Claude Opus 5.5 / GPT-6.1 Sol の形）を付ける
for key in ("OPENAI_MODEL", "CLAUDE_MODEL"):
    spec_ = manifest["settings"][key]
    named = set(spec_["value_labels"])
    assert named == set(spec_["values"]) - {MOD.OFF}, (key, set(spec_["values"]) ^ named)
    assert all(label.startswith(("GPT-", "Claude ")) for label in spec_["value_labels"].values()), key


class Ctx(object):
    generation = "gen1"
    mod_dir = folder

    def __init__(self):
        self.hooks = {}
        self.lines = []
        self.ready = []

    def on_ready(self, fn, *, key=None, **kwargs):
        self.ready.append((key, fn))
        return True

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

# ---------------------------------------------------------------- 既定: キャッシュは両方オン
# Claude は差し替え先が off でも、キャッシュのために包む
assert (MOD.OPENAI_CACHE, MOD.CLAUDE_CACHE) == ("stable", "5m")
ctx = Ctx()
MOD.apply(ctx)
assert OA.POST in ctx.hooks and CL.POST in ctx.hooks
assert any("cache stable" in line and "cache 5m" in line for line in ctx.lines), ctx.lines

# ここから下の差し替えの検査は、キャッシュを切って本文の形だけを見る（キャッシュは後ろの節）
MOD.OPENAI_CACHE, MOD.CLAUDE_CACHE = "keep", "off"
ctx = Ctx()
MOD.apply(ctx)
# Claude 側は差し替えもキャッシュも切っていても包む（文法が大きすぎるときの迂回）
assert OA.POST in ctx.hooks and CL.POST in ctx.hooks
post = ctx.hooks[OA.POST]

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
OA.fix(fixed, "gpt-6-astra", "keep", "/responses")
assert fixed == {"model": "gpt-6-astra", "input": [], "reasoning": {"effort": "low"}}, fixed
fixed = {"model": "gpt-5", "reasoning": {"effort": "minimal"}}
OA.fix(fixed, "gpt-6-sol", "keep", "/responses")
assert fixed["reasoning"] == {"effort": "none"}, fixed
fixed = {"model": "gpt-4.1", "messages": [], "reasoning_effort": "none"}
OA.fix(fixed, "gpt-6-astra", "keep", "/chat/completions")
assert fixed["reasoning_effort"] == "low", fixed
# GPT-6.1 Sol も none を断る（GPT-6 Sol は受ける）
fixed = {"model": "gpt-5.5", "input": [], "reasoning": {"effort": "none"}}
OA.fix(fixed, "gpt-6.1-sol", "keep", "/responses")
assert fixed["reasoning"] == {"effort": "low"}, fixed
assert OA.generation("gpt-6.1-sol") == 6
assert OA.generation("gpt-6-sol") == 6 and OA.generation("gpt-5.6-luna") == 5
assert OA.generation("gpt-4.1") == 4 and OA.generation("o3") == 0

# 応答の usage（キャッシュの書き込み・読み出し）をログに出す。差し替えない回も測る
class OADetails(object):
    cached_tokens, cache_write_tokens = 1024, 0


class OAUsage(object):
    input_tokens, input_tokens_details = 2000, OADetails()


class OAReply(object):
    usage = OAUsage()


ctx = Ctx()
MOD.apply(ctx)
reply_ = ctx.hooks[OA.POST](lambda self, path, *a, **k: OAReply(), OPENAI, "/responses",
                            body={"model": "gpt-6-luna", "input": []})
assert isinstance(reply_, OAReply)
assert any("[openai] usage: input 2000 / cache write 0 / cache read 1024" in line
           for line in ctx.lines), ctx.lines
assert not any("[openai] gpt-6-luna -> " in line for line in ctx.lines), ctx.lines
assert OA.usage_line(object()) is None

# 推論量の指定は同じモデルでも効く
MOD.OPENAI_EFFORT = "low"
MOD.OPENAI_CUSTOM = "gpt-5.5"
ctx = Ctx()
MOD.apply(ctx)
sent = ctx.hooks[OA.POST](orig, OPENAI, "/responses",
                                  body={"model": "gpt-5.5", "reasoning": {"effort": "none"}})
assert sent == {"model": "gpt-5.5", "reasoning": {"effort": "low"}}, sent
MOD.OPENAI_EFFORT, MOD.OPENAI_CUSTOM = "keep", ""

# off なら OpenAI の送信を包まない（タイトル画面の包みは、前の1行を外すために残る）。
# Claude は文法が大きすぎるときの迂回のために包み、型の無い要求は本文をそのまま渡す
MOD.OPENAI_MODEL = "off"
ctx = Ctx()
MOD.apply(ctx)
assert OA.POST not in ctx.hooks and CL.POST in ctx.hooks, ctx.hooks
plain = {"model": "claude-sonnet-5", "max_tokens": 10, "messages": []}
assert ctx.hooks[CL.POST](orig, OPENAI, "/v1/messages", body=plain) == plain
assert not any("[claude]" in line for line in ctx.lines), ctx.lines
MOD.OPENAI_MODEL = "gpt-6-luna"

# ---------------------------------------------------------------- Claude
MOD.CLAUDE_MODEL = "claude-opus-5-5"
ctx = Ctx()
MOD.apply(ctx)
post = ctx.hooks[CL.POST]
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
CL.fix(fixed, "claude-sonnet-5", "keep")
assert fixed == {"model": "claude-sonnet-5", "thinking": {"type": "adaptive"}}, fixed

# 思考を切れるモデルでは disabled を残す
fixed = {"model": "x", "thinking": {"type": "disabled"}}
CL.fix(fixed, "claude-opus-4-8", "keep")
assert fixed["thinking"] == {"type": "disabled"}

# Sonnet 5.5 は disabled を between_tools で言い直す。xhigh / max では受けないので外す
fixed = {"model": "x", "thinking": {"type": "disabled"}, "temperature": 0.7,
         "tool_choice": {"type": "any"}}
CL.fix(fixed, "claude-sonnet-5-5", "low")
assert fixed == {"model": "claude-sonnet-5-5", "thinking": {"type": "between_tools"},
                 "tool_choice": {"type": "auto"}, "output_config": {"effort": "low"}}, fixed
fixed = {"model": "x", "thinking": {"type": "disabled"}, "output_config": {"effort": "max"}}
CL.fix(fixed, "claude-sonnet-5-5", "keep")
assert "thinking" not in fixed, fixed
assert CL.traits("claude-sonnet-5")["between_tools"] is False

# Haiku 5.5 は disabled と強制ツール指定を受けるが、sampling は断る。xhigh / max では disabled も断る
fixed = {"model": "x", "thinking": {"type": "disabled"}, "temperature": 0.7,
         "tool_choice": {"type": "any"}}
CL.fix(fixed, "claude-haiku-5-5", "low")
assert fixed == {"model": "claude-haiku-5-5", "thinking": {"type": "disabled"},
                 "tool_choice": {"type": "any"}, "output_config": {"effort": "low"}}, fixed
for target in ("claude-haiku-5-5", "claude-opus-5"):
    fixed = {"model": "x", "thinking": {"type": "disabled"}, "output_config": {"effort": "xhigh"}}
    CL.fix(fixed, target, "keep")
    assert "thinking" not in fixed, (target, fixed)
assert CL.traits("claude-haiku-4-5")["sampling"] is False

# 古い世代は sampling を残し、Haiku 4.5 には effort を送らない
fixed = {"model": "x", "temperature": 0.5, "output_config": {"effort": "high"}}
CL.fix(fixed, "claude-haiku-4-5", "low")
assert fixed == {"model": "claude-haiku-4-5", "temperature": 0.5}, fixed

# 知らない名前はいちばん厳しい側
assert CL.traits("claude-someday-9") == CL.STRICT
assert CL.traits("claude-opus-5")["no_disable"] is False
assert CL.traits("claude-opus-5-5")["no_disable"] is True

# ログは LOG_LIMIT 回まで
MOD.LOG_LIMIT = 1
ctx = Ctx()
MOD.apply(ctx)
for _ in range(3):
    ctx.hooks[CL.POST](orig, OPENAI, "/v1/messages",
                               body={"model": "claude-sonnet-5", "messages": []})
assert sum("[claude]" in line for line in ctx.lines) == 1, ctx.lines
MOD.LOG_LIMIT, MOD.CLAUDE_MODEL = 3, "off"

# ---------------------------------------------------------------- 印の置き方（prefix_cache）
# 先頭から一致した所に置く。最初の1回は置かない。一致が延びても境目は動かさない
PC = sys.modules[NAME + ".prefix_cache"]
SHARED = "定" * 1200
prefixes = PC.Prefixes()
turn1 = [("system", SHARED + "一回目の状況", True), ("user", "やあ", True)]
turn2 = [("system", SHARED + "二回目の状況と続き", True), ("user", "やあ", True)]
turn3 = [("system", SHARED + "二回目の状況と続きのさらに先", True), ("user", "やあ", True)]
assert prefixes.cut(turn1) is None
assert prefixes.cut(turn2) == (0, 1200), prefixes.families
assert prefixes.cut(turn3) == (0, 1200), "一致が延びても境目は動かさない"
assert prefixes.cut([("user", "別の種類の頼み" * 10, True)]) is None, "短い一致は別の種類"
# 一致が境目より短くなったら引き直す
turn4 = [("system", SHARED[:1100] + "変わった", True)]
assert prefixes.cut(turn4) == (0, 1100), prefixes.families[0]
# 印を置けない区切り（assistant の発言など）に当たったら、手前の置ける区切りの終わりまで戻す
segs = [("system", SHARED, True), ("assistant", "返事" * 300, False)]
assert PC.locate(segs, 1500) == (0, 1200)
assert PC.locate(segs, 900) is None, "最小に届かなければ置かない"
assert PC.common_chars([("system", "ab", True)], [("user", "ab", True)]) == 0, "役が違えばそこで止める"

# ---------------------------------------------------------------- Claude のプロンプトキャッシュ
# 差し替え先が off でもキャッシュだけで仕掛ける。前回と同じ所までに印を付け、呼び出し側は変えない
class Usage(object):
    input_tokens, cache_creation_input_tokens, cache_read_input_tokens = 10, 0, 900


class Reply(object):
    usage = Usage()


MOD.CLAUDE_CACHE = "5m"
ctx = Ctx()
MOD.apply(ctx)
assert CL.POST in ctx.hooks
seen = {}


def reply(self, path, *args, **kwargs):
    seen["body"] = kwargs["body"]
    return Reply()


def talk(system, *messages):
    body = {"model": "claude-sonnet-5", "system": system,
            "messages": [{"role": "user", "content": m} for m in messages]}
    ctx.hooks[CL.POST](reply, OPENAI, "/v1/messages", body=body)
    return body, seen["body"]


body, sent = talk(SHARED + "一回目", "やあ")
assert sent["system"] == SHARED + "一回目", "最初の1回は印を置かない"
body, sent = talk(SHARED + "二回目", "やあ")
assert sent["system"] == [
    {"type": "text", "text": SHARED, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "二回目"}], sent["system"]
assert body["system"] == SHARED + "二回目", "呼び出し側は変えない"
assert any("cache mark 1200/" in line for line in ctx.lines), ctx.lines
assert any("cache read 900" in line for line in ctx.lines), ctx.lines

# 1h は ttl を付ける。ゲームが自分で cache_control を付けていたら触らない
fixed = {"system": [{"type": "text", "text": SHARED}], "messages": []}
prefixes = PC.Prefixes()
assert CL.mark_shared_prefix(dict(fixed), prefixes, "1h") == []
changed = CL.mark_shared_prefix(fixed, prefixes, "1h")
assert changed == ["cache mark 1200/1200 1h"], changed
assert fixed["system"] == [{"type": "text", "text": SHARED,
                            "cache_control": {"type": "ephemeral", "ttl": "1h"}}], fixed
assert CL.mark_shared_prefix(fixed, prefixes, None) == [], "付いていれば触らない"

# 差し替えと一緒に効く。usage が読めない応答でも落ちない
MOD.CLAUDE_MODEL = "claude-opus-5-5"
ctx = Ctx()
MOD.apply(ctx)
for tail in ("一", "二"):
    sent = ctx.hooks[CL.POST](orig, OPENAI, "/v1/messages",
                              body={"model": "claude-sonnet-5", "system": SHARED + tail, "messages": []})
assert sent["model"] == "claude-opus-5-5" and "cache_control" in sent["system"][0], sent
MOD.CLAUDE_MODEL, MOD.CLAUDE_CACHE = "off", "off"

# ---------------------------------------------------------------- OpenAI のプロンプトキャッシュ
assert OA.version("gpt-6.1-sol") == (6, 1) and OA.version("gpt-6-luna") == (6, 0)
assert OA.version("gpt-5.6-terra") == (5, 6) and OA.version("o3") is None
assert OA.takes_cache_options("gpt-6-luna") and OA.takes_cache_options("gpt-5.6-luna")
assert not OA.takes_cache_options("gpt-5.5") and not OA.takes_cache_options("my-model")


def ask(system, user="やあ", model="gpt-5.5"):
    return ctx.hooks[OA.POST](orig, OPENAI, "/responses", body={
        "model": model, "text": {"format": {"type": "json_schema"}},
        "input": [{"role": "system", "content": system}, {"role": "user", "content": user}]})


# off: 印を置かずに explicit へ（書き込みの割増しが無くなる）
MOD.OPENAI_CACHE = "off"
ctx = Ctx()
MOD.apply(ctx)
sent = ask(SHARED + "一回目")
assert sent["prompt_cache_options"] == {"mode": "explicit"}, sent
assert "prompt_cache_breakpoint" not in json.dumps(sent), sent

# stable: 2回目から前回と同じ所までに印
MOD.OPENAI_CACHE = "stable"
ctx = Ctx()
MOD.apply(ctx)
sent = ask(SHARED + "一回目")
assert sent["prompt_cache_options"] == {"mode": "explicit"}
assert sent["input"][0]["content"] == SHARED + "一回目", "最初の1回は印を置かない"
sent = ask(SHARED + "二回目")
assert sent["input"][0]["content"] == [
    {"type": "input_text", "text": SHARED, "prompt_cache_breakpoint": {"mode": "explicit"}},
    {"type": "input_text", "text": "二回目"}], sent["input"][0]
assert sent["input"][1] == {"role": "user", "content": "やあ"}

# GPT-5.5 以前へ送るときと、chat.completions の経路では触らない
MOD.OPENAI_MODEL = "gpt-5.5"
ctx = Ctx()
MOD.apply(ctx)
sent = ask(SHARED + "一回目")
assert "prompt_cache_options" not in sent, sent
assert OA.apply_cache({"input": []}, "gpt-6-luna", "off", PC.Prefixes(), "/chat/completions") == []
MOD.OPENAI_MODEL, MOD.OPENAI_CACHE = "gpt-6-luna", "keep"

# keep（既定）は何も足さない
ctx = Ctx()
MOD.apply(ctx)
assert "prompt_cache_options" not in ask(SHARED + "一回目", model="gpt-6-luna")

# ---------------------------------------------------------------- タイトル画面の1行
# 出すのは、API キーでクラウドを使っていて、そのプロバイダを差し替えているときだけ
NAMES = MOD.model_names(folder)
assert NAMES["claude-sonnet-5-5"] == "Claude Sonnet 5.5" and NAMES["gpt-6.1-sol"] == "GPT-6.1 Sol"
cloud = {"inference": "cloud_api_key", "provider": "Claude API", "model": "claude-sonnet-5"}
t = MOD.title_text(cloud, "gpt-6-luna", "claude-haiku-5-5", NAMES, claude_effort="medium")
assert t == "LLM: Claude Haiku 5.5 / effort: medium", t
t = MOD.title_text(cloud, "gpt-6-luna", "claude-sonnet-5-5", NAMES)
assert t == "LLM: Claude Sonnet 5.5 / effort: ゲームのまま", t
assert MOD.title_text(cloud, None, "claude-haiku-4-5", NAMES, claude_effort="low") ==     "LLM: Claude Haiku 4.5", "推論量を受けないモデルには付けない"
openai_cloud = dict(cloud, provider="OpenAI API", model="gpt-5.5")
t = MOD.title_text(openai_cloud, "gpt-6-luna", None, NAMES)
assert t == "LLM: GPT-6 Luna / effort: none", "keep ならゲームが組む値（gpt-5.5 は none）"
t = MOD.title_text(openai_cloud, "gpt-6-astra", None, NAMES)
assert t == "LLM: GPT-6 Astra / effort: low", "差し替え先の読み替え（Astra は none を断る）を通す"
t = MOD.title_text(dict(openai_cloud, model="gpt-5"), "gpt-6-luna", None, NAMES)
assert t == "LLM: GPT-6 Luna / effort: none", "gpt-5 の minimal も読み替える"
t = MOD.title_text(dict(openai_cloud, model="gpt-4.1"), "gpt-6-luna", None, NAMES)
assert t == "LLM: GPT-6 Luna", "推論量を送らない経路では付けない"
t = MOD.title_text(openai_cloud, "gpt-6-luna", None, NAMES, openai_effort="high")
assert t == "LLM: GPT-6 Luna / effort: high", t
assert MOD.title_text(cloud, "gpt-6-luna", "my-claude", NAMES, claude_effort="low") ==     "LLM: my-claude / effort: low", "一覧に無い名前はそのまま"
assert MOD.title_text(cloud, "gpt-6-luna", None, NAMES) is None, "そのプロバイダを差し替えていなければ出さない"
assert MOD.title_text(dict(cloud, inference="local"), "gpt-6-luna", "claude-sonnet-5-5", NAMES) is None
assert MOD.title_text(dict(cloud, provider="Gemini API(Experimental)"), "gpt-6-luna", "x", NAMES) is None
assert MOD.title_text(None, "gpt-6-luna", None, NAMES) is None

# apply() はタイトル画面を包み、開いている画面へは on_ready で付ける。世代を鍵に混ぜる
ctx = Ctx()
MOD.apply(ctx)
from instantale_modloader import llm, ui
assert ui.TITLE_INIT in ctx.hooks
assert ctx.ready and ctx.ready[-1][0] == "136_cloud_model_override:title:gen1", ctx.ready

# 設定を読む部品（ローダの llm.game_choice）
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "config.json")
    with io.open(path, "w", encoding="utf-8-sig") as fh:
        json.dump({"ai_setting": {"llm_inference": "cloud_api_key", "cloud_model_setting": {
            "cloud_llm_provider": "Claude API", "cloud_llm": "claude-sonnet-5"}}}, fh)
    assert llm.game_choice(path) == cloud, llm.game_choice(path)
    assert llm.game_choice(os.path.join(tmp, "missing.json")) is None

# ---------------------------------------------------------------- 文法が大きすぎるときの迂回
GR = sys.modules[NAME + ".claude_grammar"]
EFFECT_SHAPES = [{"$ref": "#/$defs/Damage"}, {"$ref": "#/$defs/Buff"}]
QUEST = {
    "type": "object", "additionalProperties": False, "required": ["title", "enemies"],
    "properties": {
        "title": {"type": "string"},
        "enemies": {"type": "array", "items": {"$ref": "#/$defs/Enemy"}},
        "loot": {"anyOf": [{"$ref": "#/$defs/Damage"}, {"type": "string"}]}},
    "$defs": {
        "Enemy": {"type": "object", "additionalProperties": False, "properties": {
            "skills": {"type": "array", "items": {"$ref": "#/$defs/Skill"}}}},
        "Skill": {"type": "object", "additionalProperties": False, "properties": {
            "effects": {"type": "array", "items": {"anyOf": EFFECT_SHAPES}}}},
        "Damage": {"type": "object", "title": "Damage", "additionalProperties": False,
                   "properties": {"type": {"type": "string", "enum": ["damage"]}}},
        "Buff": {"type": "object", "title": "Buff", "additionalProperties": False,
                 "properties": {"type": {"type": "string", "enum": ["buff"]},
                                "turns": {"type": "integer"}}}}}

# 深い選択肢（効果の2択）が先、浅い選択肢（loot）が後
order = GR.candidates(QUEST)
assert order == [("$defs", "Skill", "properties", "effects", "items"), ("properties", "loot")], order
narrow = GR.loosen(QUEST, order[:1])
item = narrow["$defs"]["Skill"]["properties"]["effects"]["items"]
assert item["type"] == "string" and item["description"].startswith(GR.MARK), item
assert '"enum":["buff"]' in item["description"] and "title" not in item["description"], item
assert "Buff" not in narrow["$defs"] and "Damage" in narrow["$defs"], sorted(narrow["$defs"])  # loot が Damage を使う
assert "anyOf" in QUEST["$defs"]["Skill"]["properties"]["effects"]["items"]  # 元の型は変えない

# 読み戻し: 印の欄だけ object に戻す。読めない文字列はそのまま
answer = {"title": "{\"not\": \"decoded\"}", "enemies": [{"skills": [
    {"effects": ["{\"type\": \"buff\", \"turns\": 3}", "broken{"]}]}]}
back = GR.decode(json.loads(json.dumps(answer)), narrow, narrow)
assert back["enemies"][0]["skills"][0]["effects"] == [{"type": "buff", "turns": 3}, "broken{"], back
assert back["title"] == answer["title"], back


class Block(object):
    def __init__(self, text):
        self.type, self.text = "text", text


class Message(object):
    def __init__(self, text):
        self.content = [Block(text)]
        self.usage = None


class Api(object):
    """文法の上限を真似る。効果の選択肢が残っていたら断り、通れば効果を文字列で返す。"""

    def __init__(self):
        self.calls = []

    def __call__(self, self_, path, *args, **kwargs):
        schema = kwargs["body"]["output_config"]["format"]["schema"]
        self.calls.append(schema)
        if "anyOf" in json.dumps(schema["$defs"]["Skill"]):
            raise RuntimeError("Error code: 400 - The compiled grammar is too large, which would cause ...")
        reply = Message(json.dumps(answer))
        parser = kwargs.get("options", {}).get("post_parser")
        return parser(reply) if callable(parser) else reply


MOD.CLAUDE_MODEL, MOD.CLAUDE_CACHE = "off", "off"
ctx = Ctx()
MOD.apply(ctx)
post = ctx.hooks[CL.POST]
api = Api()
parsed = []
request = {"model": "claude-haiku-5-5", "max_tokens": 100, "messages": [],
           "output_config": {"format": {"type": "json_schema", "schema": QUEST}}}
reply = post(api, OPENAI, "/v1/messages", body=request,
             options={"post_parser": lambda message: parsed.append(json.loads(message.content[0].text)) or "parsed"})
assert reply == "parsed" and len(api.calls) == 2, (reply, len(api.calls))
assert parsed[0]["enemies"][0]["skills"][0]["effects"][0] == {"type": "buff", "turns": 3}, parsed
assert any("grammar too large; sent with 1 choice(s)" in line for line in ctx.lines), ctx.lines
assert request["output_config"]["format"]["schema"] is QUEST  # 呼び出し側の本文は変えない
# 同じ型の2回目は最初から置き換えて送る（断られない）
api.calls[:] = []
post(api, OPENAI, "/v1/messages", body=request, options={})
assert len(api.calls) == 1 and "anyOf" not in json.dumps(api.calls[0]["$defs"]["Skill"]), api.calls


# 別の理由の 400 は送り直さない。型の無い要求も送り直さない
def other_error(self_, path, *args, **kwargs):
    raise RuntimeError("Error code: 400 - max_tokens is too large")


fresh = dict(request, output_config={"format": {"type": "json_schema", "schema": dict(QUEST, title="Other")}})
try:
    post(other_error, OPENAI, "/v1/messages", body=fresh, options={})
    raise AssertionError("別の理由の 400 を握った")
except RuntimeError as exc:
    assert "max_tokens" in str(exc)
MOD.CLAUDE_MODEL, MOD.CLAUDE_CACHE = "claude-opus-5-5", "5m"


print("ok")
