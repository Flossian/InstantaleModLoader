# -*- coding: utf-8 -*-
"""依頼の生成（`random_quest_generator`）がローカル LLM で失敗する回を、ゲーム抜きで再現して録る。

    python tools\\quest_gen_probe.py                      # 最新の記録を 20 回投げる
    python tools\\quest_gen_probe.py --runs 40 --record 3 # output_data の 3.json を 40 回
    python tools\\quest_gen_probe.py --record all --runs 10
    python tools\\quest_gen_probe.py --base-url http://127.0.0.1:51990  # 既に立っているサーバ

実プレイでは、同じ頼みが 100〜120 秒かかって失敗し、ゲームが送り直す回が多い
（約35回中15回。成功は 20〜30 秒。VERIFICATION.md §3.81）。
ゲームで「生成 → 片付ける」を繰り返すのは手間なので、同じ頼みを llama-server へ直に繰り返し投げる。

## 実機に合わせているもの

- 頼み文: ゲーム自身の記録 `output_data\\unknown\\unknown\\random_quest_generator\\N.json` の messages
- 型: 頼み文の system に埋め込まれた辞書の書き方から取り出す（`105_` の解析）。grammar の元（`json_schema`）に渡す
- 送る直前の書き換え: `105_` の型の圧縮と `111_` の置換を、実機の `chat` と同じに当てる
- サーバ: ゲームの config.json が指すモデルを、`127_` が書き換えた後と同じ引数で起こす
- 送り方: `/apply-template` で書式を当て、`/completion` に `prompt` と `json_schema` と `n_predict=16384`
  （ゲームの `LlamaCppClient._apply_chat_template` → `_post_with_model_loading_retry` と同じ並び）

ゲームは終了しておくこと（VRAM とポートを取り合う）。

## 出力

- 標準出力: 1回1行（秒・トークン・止まった理由・JSON として読めたか）と集計
- `out\\quest_gen_probe_<時刻>.jsonl`: 1回ごとの記録（途中で止めても残る）
- `out\\quest_gen_probe_<時刻>_fail<N>.txt`: 失敗した回の応答の全文
  失敗の回は、切れた位置（どの欄を書いていたか）と末尾の繰り返しも記録する
"""

from __future__ import annotations

import argparse
import datetime
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.join(ROOT, "out")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from epithet_probe import read_sampling                              # noqa: E402
from llm_ctx_probe import (find_game_dir, http_json, pick_build_dir,  # noqa: E402
                           read_live_config, running, wait_ready)
from npc_variety_probe import apply_replacements, load_replace_rules  # noqa: E402

MANAGER = "random_quest_generator"
#: ゲームが渡す出力の上限（216_ の実測 `options={n_predict=16384}`）。
N_PREDICT = 16384
#: 127_ が書き換えた後の起動引数（`out\\llm_speed.log` の REWRITE と同じ）。サンプリングは config.json から。
CTX_SIZE = 16384
SERVER_FLAGS = ["--ctx-size", str(CTX_SIZE), "--reasoning-budget", "0",
                "--chat-template-kwargs", '{"enable_thinking": false}',
                "--no-mmproj", "--n-gpu-layers", "999", "--cache-reuse", "256",
                "--parallel", "1", "--checkpoint-every-n-tokens", "256"]
TAIL_CHARS = 600

# ------------------------------------------------------------------ 頼み
def records_dir(game_dir):
    return os.path.join(str(game_dir), "output_data", "unknown", "unknown", MANAGER)


def record_paths(game_dir, which):
    folder = records_dir(game_dir)
    paths = sorted(glob.glob(os.path.join(folder, "*.json")),
                   key=lambda p: int(os.path.basename(p).split(".")[0]))
    if not paths:
        raise SystemExit("記録が無い: {}".format(folder))
    if which == "all":
        return paths
    if which in (None, "", "latest"):
        return paths[-1:]
    chosen = []
    for number in str(which).split(","):
        wanted = os.path.join(folder, "{}.json".format(number.strip()))
        if not os.path.isfile(wanted):
            raise SystemExit("記録が無い: {}".format(wanted))
        chosen.append(wanted)
    return chosen


def load_compact_module():
    """`105_` を読む（型の取り出しと圧縮は実機と同じ関数を使う）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_fix_schema_compact"))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location("schema_compact_mod", os.path.join(folder, entry))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_request(path, compact_mod, replace):
    """`(送る messages, json_schema, 元の messages)`。"""
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    original = [{"role": m["role"], "content": m["content"]} for m in data["messages"]]
    schema = None
    sent = []
    for message in original:
        text = message["content"]
        start = compact_mod.find_schema_start(text)
        if schema is None and start >= 0:
            schema, _end = compact_mod.parse_literal(text, start)
        result = compact_mod.compact_embedded_schema(text)
        sent.append({"role": message["role"], "content": result[0] if result else text})
    if not isinstance(schema, dict):
        raise SystemExit("型が頼み文から読めない: {}".format(path))
    sent, _hits = apply_replacements(replace, sent)
    return sent, schema, original


def load_runaway_module():
    """`139_` を読む（`--max-items` は MOD と同じ表と関数で上限を付ける）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith("_fix_quest_generation_runaway"))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location("quest_runaway_mod", os.path.join(folder, entry))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ 失敗の読み取り
def open_path(text):
    """途中で切れた JSON の、いま書いている位置（鍵と添え字の並び）。"""
    stack = []          # [("obj", 直近の鍵) | ("arr", 添え字)]
    in_string = escape = False
    key_buffer = None
    last_string = ""
    expecting_key = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
                last_string = key_buffer or ""
                key_buffer = None
            else:
                if key_buffer is not None:
                    key_buffer += char
            continue
        if char == '"':
            in_string = True
            key_buffer = ""
        elif char == "{":
            stack.append(["obj", None])
            expecting_key = True
        elif char == "[":
            stack.append(["arr", 0])
        elif char in "}]":
            if stack:
                stack.pop()
        elif char == ":":
            if stack and stack[-1][0] == "obj":
                stack[-1][1] = last_string
        elif char == ",":
            if stack and stack[-1][0] == "arr":
                stack[-1][1] += 1
    path = []
    for kind, value in stack:
        path.append(str(value) if kind == "obj" else "[{}]".format(value))
    return "/".join(p for p in path if p not in ("None",)) + (" (文字列の途中)" if in_string else "")


def repeated_tail(text, max_unit=200, min_repeats=3):
    """末尾で同じ並びが続いていれば `(並び, 回数)`。無ければ None。"""
    tail = text[-4000:]
    best = None
    for unit in range(2, max_unit + 1):
        piece = tail[-unit:]
        count = 1
        while tail.endswith(piece * (count + 1)):
            count += 1
        if count >= min_repeats and (best is None or unit * count > len(best[0]) * best[1]):
            best = (piece, count)
    return best


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def schema_errors(value, node, root, path="$", limit=20):
    """型（pydantic が吐く JSON Schema の範囲）に照らした食い違いの一覧。空なら型どおり。

    見るのは type / properties / required / enum / const / $ref / anyOf / items と、件数の上限。
    jsonschema が手元に無いので、ゲームの型が使っている範囲だけを見る。
    """
    errors = []

    def add(message):
        if len(errors) < limit:
            errors.append(message)

    def walk(value, node, path):
        if len(errors) >= limit or not isinstance(node, dict):
            return
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            walk(value, (root.get("$defs") or {}).get(name, {}), path)
            return
        if "anyOf" in node:
            for option in node["anyOf"]:
                trial = schema_errors(value, option, root, path, limit=1)
                if not trial:
                    return
            add("{}: どの候補（anyOf）にも当たらない: {}".format(path, json.dumps(value, ensure_ascii=False)[:80]))
            return
        if "const" in node and value != node["const"]:
            add("{}: {!r} は {!r} でない".format(path, value, node["const"]))
        if "enum" in node and value not in node["enum"]:
            add("{}: {!r} は候補 {} に無い".format(path, value, node["enum"]))
        kind = node.get("type")
        if kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                add("{}: 整数でない: {!r}".format(path, value))
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                add("{}: 数でない: {!r}".format(path, value))
        elif kind in _TYPES and not isinstance(value, _TYPES[kind]):
            add("{}: {} でない: {}".format(path, kind, type(value).__name__))
            return
        if isinstance(value, dict):
            props = node.get("properties") or {}
            for key in node.get("required") or []:
                if key not in value:
                    add("{}: 必須の {} が無い".format(path, key))
            for key, sub in value.items():
                if key in props:
                    walk(sub, props[key], "{}.{}".format(path, key))
        elif isinstance(value, list):
            if "maxItems" in node and len(value) > node["maxItems"]:
                add("{}: {}件（上限 {}）".format(path, len(value), node["maxItems"]))
            for index, item in enumerate(value):
                walk(item, node.get("items") or {}, "{}[{}]".format(path, index))

    walk(value, node, path)
    return errors


def judge(content, schema):
    """`(読めたか, 理由)`。JSON として読め、型に照らして全項目が型どおりなら読めた。"""
    try:
        data = json.loads(content)
    except ValueError as exc:
        return False, "JSON として読めない: {}".format(str(exc)[:80])
    if not isinstance(data, dict):
        return False, "辞書でない"
    errors = schema_errors(data, schema, schema)
    if errors:
        return False, "型と食い違う: {}".format("; ".join(errors[:3]))
    return True, "ok"


# ------------------------------------------------------------------ 送信
def generate(base, messages, schema, sampling, timeout):
    applied = http_json(base + "/apply-template", {"messages": messages}, timeout=60)
    prompt = applied.get("prompt")
    if not isinstance(prompt, str):
        raise RuntimeError("apply-template の答えに prompt が無い: {}".format(list(applied)))
    body = {"prompt": prompt, "json_schema": schema, "n_predict": N_PREDICT,
            "cache_prompt": True}
    body.update(sampling)
    started = time.time()
    got = http_json(base + "/completion", body, timeout=timeout)
    seconds = time.time() - started
    timings = got.get("timings") or {}
    return {"seconds": round(seconds, 2), "content": got.get("content") or "",
            "stop_type": got.get("stop_type"), "tokens": got.get("tokens_predicted")
            or timings.get("predicted_n"), "prompt_tokens": timings.get("prompt_n"),
            "tok_per_s": timings.get("predicted_per_second")}


def main():
    ap = argparse.ArgumentParser(description="依頼の生成の失敗をゲーム抜きで再現する。")
    ap.add_argument("--runs", type=int, default=20, help="1つの記録あたりの回数（既定 20）")
    ap.add_argument("--record", default="latest", help="output_data の記録の番号（1,2,3 のように並べてよい） / latest / all")
    ap.add_argument("--no-replace", action="store_true", help="111_ の置換を当てない（比較用）")
    ap.add_argument("--max-items", action="store_true",
                    help="139_ と同じ件数の上限を型の並びに付けて送る（上限は 139_ の既定値。設定画面で変えた値は読まない。変えるなら --enemies）")
    ap.add_argument("--enemies", type=int, default=None,
                    help="--max-items の敵の上限（139_ の設定 ENEMY_LIMIT）を差し替える（わざと打ち切らせる試し）")
    ap.add_argument("--save-all", action="store_true", help="成功した回の出力も全文を保存する")
    ap.add_argument("--base-url", help="既に立っているサーバ（http://127.0.0.1:<port>。/v1 は付けない）")
    ap.add_argument("--model-pattern", default=None, help="起こす GGUF の絞り込み（既定は config.json のモデル名）")
    ap.add_argument("--game-dir")
    ap.add_argument("--port", type=int, default=51990)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    game_dir = find_game_dir(args.game_dir)
    settings = read_live_config(game_dir).get("ai_setting") or {}
    sampling = read_sampling(settings)
    compact_mod = load_compact_module()
    replace = None if args.no_replace else load_replace_rules()
    requests = [(path,) + build_request(path, compact_mod, replace)
                for path in record_paths(game_dir, args.record)]
    if args.max_items:
        runaway = load_runaway_module()
        if args.enemies is not None:
            runaway.ENEMY_LIMIT = args.enemies
        limited = []
        for path, messages, schema, original in requests:
            schema, applied = runaway.with_max_items(schema, runaway.limits())
            limited.append((path, messages, schema, original))
        requests = limited
        print("  件数の上限: {}".format(", ".join(applied)))

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.join(OUT_DIR, "quest_gen_probe_{}{}".format(
        stamp, ("_" + args.tag) if args.tag else ""))
    proc = handle = None
    rows = []
    try:
        if args.base_url:
            base = args.base_url.rstrip("/")
        else:
            busy = running("instantale.exe", "llama-server.exe")
            if busy:
                print("  ゲームか LLM サーバが動いている: {}。終了してから実行すること。".format(", ".join(busy)))
                return 2
            pattern = args.model_pattern or ((settings.get("local_model_setting") or {})
                                             .get("local_llm") or {}).get("name") or ""
            models_dir = os.path.join(str(game_dir), "runtime", "models", "llama_cpp")
            found = sorted(name for name in os.listdir(models_dir)
                           if name.lower().endswith(".gguf") and pattern.lower() in name.lower())
            if len(found) != 1:
                print("  --model-pattern {!r} で1つに決まらない: {}".format(pattern, found))
                return 2
            model = os.path.join(models_dir, found[0])
            backend = (settings.get("local_model_setting") or {}).get("llm_backend",
                                                                       "llama-cpp-completion-cuda")
            server = pick_build_dir(game_dir, backend) / "llama-server.exe"
            print("  モデル: {}".format(found[0]))
            print("  起動中（読み込みに数分かかることがある）…", flush=True)
            handle = io.open(base_name + "_server.log", "w", encoding="utf-8", errors="replace")
            command = [str(server), "-m", model, "--host", "127.0.0.1", "--port", str(args.port),
                       "--temp", str(sampling["temperature"]), "--top-p", str(sampling["top_p"]),
                       "--top-k", str(sampling["top_k"])] + SERVER_FLAGS
            proc = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)
            if not wait_ready(args.port, 600, proc):
                print("  起動しなかった。{} を読むこと。".format(base_name + "_server.log"))
                return 1
            base = "http://127.0.0.1:{}".format(args.port)

        print("  サンプリング: {}  出力の上限: {}".format(sampling, N_PREDICT))
        print("  置換ルール: {}".format(replace[2] if replace else "当てない"))
        jsonl = io.open(base_name + ".jsonl", "a", encoding="utf-8")
        failures = 0
        for path, messages, schema, _original in requests:
            name = os.path.basename(path)
            print("  記録 {}（送る字数 {}）".format(name, sum(len(m["content"]) for m in messages)))
            for run in range(1, args.runs + 1):
                try:
                    got = generate(base, messages, schema, sampling, args.timeout)
                except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
                    print("    {:>3}: 送信に失敗 {}".format(run, exc), flush=True)
                    continue
                ok, why = judge(got["content"], schema)
                row = {"record": name, "run": run, "ok": ok, "why": why,
                       "seconds": got["seconds"], "tokens": got["tokens"],
                       "stop_type": got["stop_type"], "chars": len(got["content"]),
                       "prompt_tokens": got["prompt_tokens"], "tok_per_s": got["tok_per_s"]}
                if ok and args.save_all:
                    keep_path = "{}_ok_{}_{}.json".format(base_name, name.split(".")[0], run)
                    with io.open(keep_path, "w", encoding="utf-8") as fh:
                        fh.write(got["content"])
                    row["file"] = os.path.basename(keep_path)
                if ok:
                    # 成功した回の件数（上限に張り付いていないか＝無理に止めていないかを見る）。
                    data = json.loads(got["content"])
                    enemies = data.get("enemies") or []
                    row["counts"] = {
                        "events": len(data.get("events") or []),
                        "enemies": len(enemies),
                        "types": [str(e.get("type")) for e in enemies if isinstance(e, dict)],
                        "locations": len((data.get("area") or {}).get("locations") or []),
                        "drops": [len(((e.get("data") or {}).get("drops")) or [])
                                  for e in enemies if isinstance(e, dict)],
                    }
                if not ok:
                    failures += 1
                    row["at"] = open_path(got["content"])
                    rep = repeated_tail(got["content"])
                    row["repeat"] = {"unit": rep[0], "times": rep[1]} if rep else None
                    row["tail"] = got["content"][-TAIL_CHARS:]
                    fail_path = "{}_fail{}.txt".format(base_name, failures)
                    with io.open(fail_path, "w", encoding="utf-8") as fh:
                        fh.write(got["content"])
                    row["file"] = os.path.basename(fail_path)
                rows.append(row)
                jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
                jsonl.flush()
                line = "    {:>3}: {:>6.1f}s {:>6} tok {:>5} {} {}".format(
                    run, got["seconds"], got["tokens"] or "?", got["stop_type"] or "?",
                    "OK " if ok else "NG ", json.dumps(row.get("counts"), ensure_ascii=False) if ok else why)
                if not ok:
                    line += "\n         位置: {}  繰り返し: {}".format(
                        row["at"], (repr(row["repeat"]["unit"][:60]) + " ×{}".format(row["repeat"]["times"]))
                        if row["repeat"] else "無し")
                print(line, flush=True)
        jsonl.close()
    finally:
        if proc is not None:
            proc.kill()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        if handle is not None:
            handle.close()

    if rows:
        ok_rows = [r for r in rows if r["ok"]]
        ng_rows = [r for r in rows if not r["ok"]]
        def stats(group, key):
            values = sorted(r[key] for r in group if isinstance(r.get(key), (int, float)))
            return "{}〜{}（中央 {}）".format(values[0], values[-1], values[len(values) // 2]) if values else "-"
        print()
        print("  成功 {} / 失敗 {}（全 {} 回）".format(len(ok_rows), len(ng_rows), len(rows)))
        print("  成功: 秒 {}  トークン {}".format(stats(ok_rows, "seconds"), stats(ok_rows, "tokens")))
        if ng_rows:
            print("  失敗: 秒 {}  トークン {}".format(stats(ng_rows, "seconds"), stats(ng_rows, "tokens")))
            for r in ng_rows:
                print("    {} #{} {} 位置={} 繰り返し={} -> {}".format(
                    r["record"], r["run"], r["stop_type"], r["at"],
                    (repr(r["repeat"]["unit"][:40]) + "×{}".format(r["repeat"]["times"])) if r["repeat"] else "無し",
                    r["file"]))
        print("  記録: {}.jsonl".format(base_name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
