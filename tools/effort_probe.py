# -*- coding: utf-8 -*-
"""Claude の推論量（effort）と思考の有無で、ゲームの頼みの出来・時間・トークンがどう変わるかをゲーム抜きで録る。

    python tools\\effort_probe.py                         # Haiku 5.5・8通り・頼み8種・各3回
    python tools\\effort_probe.py --runs 1 --kinds narrator,referee_npc
    python tools\\effort_probe.py --configs off-medium,off-high

## 送るもの

- 頼み: ゲーム自身の記録 `output_data\\*\\*\\<頼みの種類>\\N.json` のうち、型の指示が付いた最新の回
  （Claude で動いた回の記録には型の指示が残らないので、型はローカルで動いた回から取る）。
  1つ目の system（`必ず日本語で、以下のjson形式で出力すること: {...}`）は Claude では `output_config.format` に移る。
  残りの system はつないで `system` に、user / assistant はそのまま。user が無ければゲームの定数の一文を置く
- 語り（`narrator`）は型の無い頼み。最新の回をそのまま送る
- 本文の形はゲームの古い経路（Sonnet 5 などを選んだとき）に合わせ、`136_` の送信口を通す（差し替え・const の戻し・
  文法が大きすぎるときの迂回・数の範囲の寄せがそのまま効く）。`max_tokens` はゲームの既定の 30000
- 思考なし: `thinking: disabled`（`136_` が effort を足す）。思考あり: `thinking: adaptive`。
  Haiku 5.5 は xhigh / max で思考を切る指定を断るので、その2段は思考ありだけ

鍵は環境変数 `ANTHROPIC_API_KEY`（実行するターミナルだけで設定する）。ゲームは動いていてよい（ゲームには触らない）。

## 出力

- 標準出力: 1回1行と、設定×頼みの集計（秒・出力トークン・型どおりか・文字数）と、設定ごとの費用
- `out\\effort_probe_<時刻>.jsonl`: 1回ごとの記録（応答の全文を含む。途中で止めても残る）
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime
import glob
import importlib.util
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODS_DIR = os.path.join(ROOT, "runtime", "mods")
OUT_DIR = os.path.join(ROOT, "out")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import quest_gen_probe as qgp                                        # noqa: E402
from item_image_probe import claude_schema, embedded_schema           # noqa: E402
from llm_ctx_probe import find_game_dir                               # noqa: E402

MODEL = "claude-haiku-5-5"
#: 1MTok あたりの単価（入力・出力。Haiku 5.5 の 100K 以下）
PRICE = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}
MAX_TOKENS = 30000
#: ゲームが user の無い頼みに置く一文（exe の定数）
NO_USER = "Carry out the task described in the system instructions."
FORMAT_HEAD = "json形式で出力すること"

KINDS = ("narrator", "conversation_facilitator", "conversation_resolver", "referee_player_attack_new_new",
         "referee_npc", "random_quest_generator", "mod_town_factions_people", "shop_item_generator_ordinary")
#: `--kinds gm` で足す、GM に近い頼み（自由行動の判定・街の詳細の生成）
GM_KINDS = ("quest_referee_with_free_action", "create_settlement_detail")
#: 街の詳細の生成で、どの街にも要る施設の種類（ゲームの頼みの必須5種）
REQUIRED_FACILITIES = ("inn", "guild", "administrative_office", "medical_facility", "general_store")
#: (名前, 思考, effort)
CONFIGS = (("off-low", "disabled", "low"), ("off-medium", "disabled", "medium"), ("off-high", "disabled", "high"),
           ("on-low", "adaptive", "low"), ("on-medium", "adaptive", "medium"), ("on-high", "adaptive", "high"),
           ("on-xhigh", "adaptive", "xhigh"), ("on-max", "adaptive", "max"))


# ------------------------------------------------------------------ 頼み
def _messages(path):
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [{"role": m["role"], "content": m["content"]} for m in data["messages"] if isinstance(m.get("content"), str)]


def _has_format(messages):
    return bool(messages) and FORMAT_HEAD in messages[0]["content"]


def load_request(game_dir, spec, compact):
    """`spec` は頼みの種類（最新の記録）か `種類:番号`（その記録）。

    Claude で動いた回の記録には型の指示が残らないので、型は同じ種類の、型の指示が付いた最新の記録から取る。
    """
    kind, _, number = spec.partition(":")
    paths = sorted(glob.glob(os.path.join(str(game_dir), "output_data", "*", "*", kind, "*.json")),
                   key=os.path.getmtime, reverse=True)
    if number:
        paths = [p for p in paths if os.path.basename(p) == number + ".json"] + paths
    if not paths:
        raise SystemExit("記録が無い: {}".format(spec))
    path = paths[0]
    messages = _messages(path)
    schema = None
    if _has_format(messages):
        schema = embedded_schema(messages[:1], compact)
        messages = messages[1:]
    elif kind != "narrator":
        source = next((p for p in paths if _has_format(_messages(p))), None)
        if source is None:
            raise SystemExit("型の指示が付いた記録が無い: {}".format(kind))
        schema = embedded_schema(_messages(source)[:1], compact)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"] or [{"role": "user", "content": NO_USER}]
    return {"kind": spec, "record": os.path.basename(path), "system": system, "messages": turns, "schema": schema}


def body_of(request, thinking):
    body = {"model": "claude-sonnet-5", "max_tokens": MAX_TOKENS, "system": request["system"],
            "messages": request["messages"], "thinking": {"type": thinking}}
    if request["schema"] is not None:
        body["output_config"] = {"format": {"type": "json_schema", "schema": claude_schema(request["schema"])}}
    return body


# ------------------------------------------------------------------ 送信
class Block(object):
    def __init__(self, data):
        self.type, self.text = data.get("type"), data.get("text")


class Message(object):
    def __init__(self, data):
        self.raw = data
        self.content = [Block(b) for b in data.get("content", [])]


class Sender(object):
    """`136_` の Claude の送信口を、effort ごとに包み直して持つ。"""

    def __init__(self, key, model):
        folder = os.path.join(MODS_DIR, "136_cloud_model_override")
        spec = importlib.util.spec_from_file_location("cmo_effort", os.path.join(folder, "cloud_model_override.py"),
                                                      submodule_search_locations=[folder])
        self.mod = importlib.util.module_from_spec(spec)
        sys.modules["cmo_effort"] = self.mod
        spec.loader.exec_module(self.mod)
        self.side = sys.modules["cmo_effort.claude_side"]
        self.key, self.model = key, model
        self.lines = []
        self.posts = {}
        self.local = threading.local()

    def post_for(self, effort):
        if effort not in self.posts:
            mod = self.mod
            mod.CLAUDE_MODEL, mod.CLAUDE_CUSTOM, mod.CLAUDE_CACHE, mod.CLAUDE_EFFORT = "off", self.model, "off", effort
            mod.TITLE_MODEL = False
            sender = self

            class Ctx(object):
                generation = "probe"
                mod_dir = os.path.join(MODS_DIR, "136_cloud_model_override")
                hooks = {}

                def on_ready(self, fn, **kwargs):
                    return True

                def log(self, text, **kwargs):
                    sender.lines.append(text)

                def log_exc(self, text, **kwargs):
                    sender.lines.append(text)

                def wrap(self, target, **kwargs):
                    def deco(fn):
                        self.hooks[target] = fn
                        return fn
                    return deco

            ctx = Ctx()
            ctx.hooks = {}
            mod.apply(ctx)
            self.posts[effort] = ctx.hooks[self.side.POST]
        return self.posts[effort]

    def _send(self, _self, path, *args, **kwargs):
        body = kwargs["body"]
        self.local.sent.append({"model": body.get("model"), "thinking": body.get("thinking"),
                                "effort": (body.get("output_config") or {}).get("effort")})
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode("utf-8"),
                                     headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                              "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                raw = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RuntimeError("HTTP {} {}".format(exc.code, exc.read()[:400].decode("utf-8", "replace")))
        usage = raw.get("usage") or {}
        for k in ("input_tokens", "output_tokens"):
            self.local.usage[k] += usage.get(k) or 0
        message = Message(raw)
        parser = (kwargs.get("options") or {}).get("post_parser")
        if callable(parser):
            parser(message)
        return message

    def generate(self, request, thinking, effort):
        self.local.usage = collections.Counter()
        self.local.sent = []

        class Client(object):
            base_url = "https://api.anthropic.com"
        started = time.time()
        reply = self.post_for(effort)(self._send, Client(), "/v1/messages", body=body_of(request, thinking), options={})
        seconds = round(time.time() - started, 1)
        text = "".join(b.text or "" for b in reply.content if b.type == "text")
        return {"seconds": seconds, "content": text, "stop": reply.raw.get("stop_reason"),
                "input_tokens": self.local.usage["input_tokens"], "output_tokens": self.local.usage["output_tokens"],
                "sent": self.local.sent}


# ------------------------------------------------------------------ 判定
def text_chars(value):
    """応答の中の文字列の字数の合計（語りや台詞の長さの目安）。"""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(text_chars(v) for v in value.values())
    if isinstance(value, list):
        return sum(text_chars(v) for v in value)
    return 0


def facility_types(value):
    """応答の中の `type` の値をすべて集める（街の詳細の生成で、必須の施設がそろったかを見る）。"""
    found = set()
    if isinstance(value, dict):
        if isinstance(value.get("type"), str):
            found.add(value["type"])
        for sub in value.values():
            found |= facility_types(sub)
    elif isinstance(value, list):
        for sub in value:
            found |= facility_types(sub)
    return found


def check(request, content):
    """`(型どおりか, 理由, 字数, 欠けた必須施設)`。欠けは街の詳細の生成だけ（他は None）。"""
    if request["schema"] is None:
        return True, "ok", len(content.strip()), None
    ok, reason = qgp.judge(content, request["schema"])
    try:
        data = json.loads(content)
    except ValueError:
        return ok, reason, len(content), None
    missing = None
    if request["kind"].partition(":")[0] == "create_settlement_detail":
        missing = [t for t in REQUIRED_FACILITIES if t not in facility_types(data)]
    return ok, reason, text_chars(data), missing


# ------------------------------------------------------------------ 集計
def summarize(rows, model):
    price_in, price_out = PRICE.get(model, (0.0, 0.0))
    by = collections.defaultdict(list)
    for row in rows:
        by[(row["config"], row["kind"])].append(row)
    configs = [c[0] for c in CONFIGS if any(r["config"] == c[0] for r in rows)]
    kinds = [k for k in KINDS + GM_KINDS if any(r["kind"] == k for r in rows)]
    kinds += sorted({r["kind"] for r in rows} - set(kinds))
    print("\n## 頼みごと（中央値の秒 / 平均の出力トークン / 型どおり / 平均の字数）")
    for kind in kinds:
        print("\n" + kind)
        for config in configs:
            group = by.get((config, kind)) or []
            done = [r for r in group if "error" not in r]
            if not group:
                continue
            if not done:
                print("  {:<11} 失敗 {}".format(config, group[0]["error"][:100]))
                continue
            secs = sorted(r["seconds"] for r in done)
            judged = [r for r in done if r.get("missing") is not None]
            print("  {:<11} {:>6.1f}s  out {:>6.0f}  ok {}/{}  字 {:>5.0f}{}{}".format(
                config, secs[len(secs) // 2], sum(r["output_tokens"] for r in done) / len(done),
                sum(1 for r in done if r["ok"]), len(group), sum(r["chars"] for r in done) / len(done),
                "  stop:" + ",".join(sorted({r["stop"] for r in done if r["stop"] != "end_turn"}))
                if any(r["stop"] != "end_turn" for r in done) else "",
                "  必須5種そろい {}/{}".format(sum(1 for r in judged if not r["missing"]), len(judged))
                if judged else ""))
    print("\n## 設定ごと（全頼みの合計）")
    for config in configs:
        done = [r for r in rows if r["config"] == config and "error" not in r]
        if not done:
            continue
        tin = sum(r["input_tokens"] for r in done)
        tout = sum(r["output_tokens"] for r in done)
        cost = tin / 1e6 * price_in + tout / 1e6 * price_out
        print("  {:<11} 回 {:>3}  入力 {:>8}  出力 {:>7}  ${:.4f}  1回 ${:.5f}  秒の合計 {:>6.0f}  型どおり {}/{}".format(
            config, len(done), tin, tout, cost, cost / len(done), sum(r["seconds"] for r in done),
            sum(1 for r in done if r["ok"]), len(done)))


def main():
    ap = argparse.ArgumentParser(description="effort と思考の有無で、ゲームの頼みの出来を比べる。")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--configs", default=",".join(c[0] for c in CONFIGS))
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--game-dir")
    ap.add_argument("--summary", help="既存の jsonl から集計だけ出す")
    args = ap.parse_args()

    if args.summary:
        with io.open(args.summary, encoding="utf-8") as fh:
            summarize([json.loads(line) for line in fh if line.strip()], args.model)
        return

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit("鍵が無い: 環境変数 ANTHROPIC_API_KEY")

    game_dir = find_game_dir(args.game_dir)
    compact = qgp.load_compact_module()
    kinds = []
    for k in args.kinds.split(","):
        kinds += list(KINDS + GM_KINDS) if k == "all" else list(GM_KINDS) if k == "gm" else [k] if k else []
    configs = [c for c in CONFIGS if c[0] in args.configs.split(",")]
    requests = [load_request(game_dir, kind, compact) for kind in kinds]
    for request in requests:
        print("  {:<32} {}  system {}字  turns {}  型 {}".format(
            request["kind"], request["record"], len(request["system"]), len(request["messages"]),
            "あり" if request["schema"] else "なし"), flush=True)

    sender = Sender(key, args.model)
    for _name, _thinking, effort in configs:
        sender.post_for(effort)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "effort_probe_{}.jsonl".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
    jobs = [(config, request, run) for run in range(args.runs) for request in requests for config in configs]
    rows = []
    lock = threading.Lock()

    def work(job):
        (name, thinking, effort), request, run = job
        row = {"config": name, "thinking": thinking, "effort": effort, "kind": request["kind"],
               "record": request["record"], "run": run + 1, "model": args.model}
        try:
            got = sender.generate(request, thinking, effort)
            ok, reason, chars, missing = check(request, got["content"])
            row.update(got, ok=ok, reason=reason, chars=chars, missing=missing)
            line = "{:<11} {:<32} #{} {:>6.1f}s out {:>6} {} {}{}".format(
                name, request["kind"], run + 1, got["seconds"], got["output_tokens"], "ok" if ok else "NG", reason[:60],
                "" if missing is None else "  欠け: " + (",".join(missing) or "なし"))
        except Exception as exc:
            row.update(error=str(exc)[:500])
            line = "{:<11} {:<32} #{} 失敗 {}".format(name, request["kind"], run + 1, str(exc)[:120])
        with lock:
            rows.append(row)
            with io.open(out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(line, flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, jobs))
    summarize(rows, args.model)
    print("\n記録: {}".format(out_path))


if __name__ == "__main__":
    main()
