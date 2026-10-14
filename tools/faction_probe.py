# -*- coding: utf-8 -*-
"""339_town_factions の頼み文を、実際のローカル LLM と実セーブで通して確かめる。

    python tools\\faction_probe.py                         # llama-server を起こして全世界を測る
    python tools\\faction_probe.py --runs 3               # 世界ごとの回数
    python tools\\faction_probe.py --world テストワールド   # 世界を絞る（何度でも）
    python tools\\faction_probe.py --base-url http://127.0.0.1:1234/v1 --api-model <名前>

ゲーム抜きで llama-server を直接起こす形は `epithet_probe.py` と同じ（ゲームは終了しておくこと。VRAM とポートを取り合う）。
セーブは読むだけで書かない。

測るもの:

  頼み文     MOD の `material.py` の頼み文と材料を**そのまま**使う（抽出・初回の判定・情勢）
  型         MOD が `create_model` で作るのと同じ形の JSON スキーマで縛る（ローカルでは並びに件数の上限も付く）。
             ゲームの `send_request` と同じく、スキーマの文を system の末尾に足す
  台帳       MOD の `rules.build_ledger` で組む。根拠が材料に見つからずに捨てた項目の数も出す
  揺れ       同じ世界を何回か通して、勢力の顔ぶれ・街の割り振り・初回の段がどれだけ変わるか
  漏れ       世界の設定に「出力しない」と書かれた部分の語が、画面に出る文（名前・説明・呼び名・噂）に混ざるか
             （`--leak-words` で語を渡す。既定は無し）

`--add-factions` を付けた世界は、世界の概要を勢力つきの文に差し替えて測る（実機の確認と同じ試しの世界）。

`--anthropic-model claude-haiku-5-5` を付けると、llama-server を起こさず Claude API（Messages API）を直接呼ぶ。
キーは環境変数 `ANTHROPIC_API_KEY`、無ければ `--api-key-file` のファイルの中身（どちらも画面と結果に書かない）。
型は道具の入力スキーマとして渡して答えを縛る（クラウドの形なので並びの件数の上限は付けない）。

結果は `out\\faction_probe_<時刻>.json`（生の返答ぜんぶ）と標準出力の集計。
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.join(ROOT, "out")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from llm_ctx_probe import (find_game_dir, find_model, http_json, popen_server,  # noqa: E402
                           pick_build_dir, read_live_config, running, wait_ready)
from epithet_probe import read_sampling  # noqa: E402
from instantale_modloader import llm, saves  # noqa: E402

CTX_SIZE = 16384
MAX_TOKENS = 4000
DEFAULT_RUNS = 2

#: `--add-factions` で差し替える概要（実機の確認で使ったのと同じ文）。
FACTION_OVERVIEW = (
    "光に満ちた交易の地と、その陰で使い捨てにされる冒険者たちの世界。"
    "この世界には三つの勢力がある。"
    "カスティアとヴァルガルドを治める「白翼騎士団」は、秩序と信仰を重んじる軍事組織で、異端と無法者を決して許さない。"
    "オーラム・ソラリス・ゼニスを結ぶ「黄金商会」は、富と学問を握る商人の連合で、利益のためなら誰とでも手を組む。"
    "ミストルとアビスの港を根城にする「霧の船団」は、海賊と密輸人の寄り合いで、白翼騎士団とは長年敵対している。"
    "白翼騎士団と黄金商会は互いを利用しあっているが、心の底では信用していない。"
    "エテルナとリヴェールは、どの勢力にも属さない中立の地である。")


def load_mod():
    """339 を検査と同じ形で読む（パッケージとして。`material` と `rules` を使う）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith("_town_factions")
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("*_town_factions が1つに決まらない: {}".format(matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(
        "town_factions_probe", os.path.join(folder, entry), submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules["town_factions_probe"] = module
    spec.loader.exec_module(module)
    return module, sys.modules["town_factions_probe.material"], sys.modules["town_factions_probe.rules"]


def schemas(limits):
    """MOD の `structure()` と同じ形の JSON スキーマ。`limits` があればローカルの形（並びに件数の上限つき）。"""
    def obj(fields):
        return {"type": "object", "properties": fields, "required": list(fields)}

    def listed(item, key):
        shape = {"type": "array", "items": item}
        if limits:
            shape["maxItems"] = limits[key]
        return shape

    text, number = {"type": "string"}, {"type": "integer"}
    return {
        "extract": obj({
            "factions": listed(obj({"name": text, "name_type": text, "kind": text, "note": text,
                                    "evidence": text}),
                               "factions"),
            "towns": listed(obj({"town_id": text, "faction": text, "evidence": text}), "towns"),
            "relations": listed(obj({"a": text, "b": text, "stage": number, "evidence": text}),
                                "relations"),
            "peaceful": text}),
        "standing": obj({"standings": listed(
            obj({"faction": text, "stage": number, "title": text, "reason": text}), "standings")}),
        "design": obj({
            "factions": listed(obj({"name": text, "kind": text, "note": text,
                                    "towns": {"type": "array", "items": text}}), "factions"),
            "relations": listed(obj({"a": text, "b": text, "stage": number}), "relations"),
            "peaceful": text}),
        "adjust": obj({"standings": listed(
            obj({"faction": text, "adjust": number, "title": text, "reason": text}), "standings")}),
        "trend": obj({"happened": text, "a": text, "b": text, "direction": text, "rumor": text,
                      "reason": text}),
    }


def with_schema(messages, schema):
    """ゲームの `send_request` と同じく、スキーマの文を system の末尾に足す。"""
    out = [dict(m) for m in messages]
    out[0]["content"] += "\n\n出力は次の JSON スキーマに従う JSON だけを書くこと。\n" + json.dumps(
        schema, ensure_ascii=False)
    return out


def chat(base_url, api_model, messages, schema, sampling, timeout):
    """OpenAI 互換の chat 1回。`(辞書 / None, 生の本文, 秒)`。"""
    body = {"model": api_model, "messages": with_schema(messages, schema),
            "max_tokens": MAX_TOKENS, "cache_prompt": True,
            "response_format": {"type": "json_schema", "json_schema": {"schema": schema}}}
    body.update(sampling)
    started = time.monotonic()
    try:
        got = http_json(base_url.rstrip("/") + "/chat/completions", body, timeout=timeout)
        raw = got["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
        return None, "error: {}".format(exc), round(time.monotonic() - started, 1)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    return (data if isinstance(data, dict) else None), raw, round(time.monotonic() - started, 1)


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def anthropic_chat(api_key, model, messages, schema, timeout):
    """Messages API 1回。答えは道具 `answer` の入力として受ける。`(辞書 / None, 生の本文, 秒)`。"""
    shaped = with_schema(messages, schema)
    body = {"model": model, "max_tokens": MAX_TOKENS, "system": shaped[0]["content"],
            "messages": [{"role": m["role"], "content": m["content"]} for m in shaped[1:]],
            "tools": [{"name": "answer", "description": "答えを返す", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "answer"}}
    request = urllib.request.Request(
        ANTHROPIC_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-api-key": api_key,
                 "anthropic-version": ANTHROPIC_VERSION})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            got = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        return None, "error: HTTP {} {}".format(exc.code, detail), round(time.monotonic() - started, 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, "error: {}".format(exc), round(time.monotonic() - started, 1)
    seconds = round(time.monotonic() - started, 1)
    for block in got.get("content") or []:
        if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
            return block["input"], json.dumps(block["input"], ensure_ascii=False), seconds
    return None, json.dumps(got, ensure_ascii=False)[:2000], seconds


def read_api_key(path):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key and path:
        with io.open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
    return key


def life_log_of(player):
    """セーブのライフログを、ゲームの `get_life_log_text` に近い形の文にする。"""
    rows = []
    for entry in player.get("life_log") or []:
        if isinstance(entry, dict):
            rows.append("{}〜{}日目: {}".format(entry.get("day_start"), entry.get("day_end"),
                                              entry.get("content")))
    return "\n".join(rows)


def player_material(save):
    player = save.get("player_data") or {}
    history = player.get("area_history") or {}
    lawfulness = {str(k): v.get("lawfulness") for k, v in history.items() if isinstance(v, dict)}
    achievements = {str(k): list(v.get("achievements") or []) for k, v in history.items()
                    if isinstance(v, dict) and v.get("achievements")}
    return {"player_name": player.get("name") or "主人公", "profile": player.get("profile") or "",
            "lawfulness": lawfulness, "achievements": achievements,
            "life_log": life_log_of(player)}


def shown_texts(ledger, judged, trend):
    """画面や会話に出る文（漏れを数える対象）。"""
    texts = []
    for f in ledger.get("factions") or []:
        texts += [f.get("name", ""), f.get("kind", ""), f.get("note", "")]
    for row in (judged or {}).get("standings") or []:
        texts += [str(row.get("title") or ""), str(row.get("reason") or "")]
    if trend:
        texts.append(str(trend.get("rumor") or ""))
    return texts


def run_world(name, save, material, rules, args, ask, leak_words):
    towns = material.towns_of(save)
    text = material.extract_material(save)
    results = []
    for index in range(args.runs):
        row = {"world": name, "run": index + 1}
        extracted, raw, seconds = ask(material.extract_messages(text), "extract")
        row.update(extract_seconds=seconds, extract_raw=raw)
        if extracted is None:
            row["status"] = "unreadable"
            results.append(row)
            print("    {} run {}: 抽出が読めない（{}s）".format(name, index + 1, seconds), flush=True)
            continue
        ledger, dropped = rules.build_ledger(extracted, text, [t[0] for t in towns],
                                             day=(save.get("world_data") or {}).get("days_elapsed"),
                                             town_names={t[0]: t[1] for t in towns})
        offered = sum(len(extracted.get(key) or []) for key in ("factions", "towns", "relations"))
        row.update(status=ledger["status"], dropped=dropped, offered=offered,
                   factions=[f["name"] for f in ledger["factions"]],
                   towns={t: r["faction"] for t, r in ledger["towns"].items()},
                   relations={k: v["stage"] for k, v in ledger["relations"].items()
                              if v.get("from_setting")},
                   peaceful=ledger.get("peaceful"))
        judged = trend = None
        if ledger["status"] == rules.NONE and args.design:
            designed, raw, seconds = ask(material.design_messages(text), "design")
            row.update(design_seconds=seconds, design_raw=raw)
            if designed is not None:
                ledger = rules.build_designed_ledger(
                    designed, [t[0] for t in towns],
                    peaceful=str(extracted.get("peaceful") or "").lower() == "true")
                row.update(status=ledger["status"], factions=[f["name"] for f in ledger["factions"]],
                           towns={t: r["faction"] for t, r in ledger["towns"].items()}, designed=True)
        if ledger["status"] == rules.READY:
            # 最初の立ち位置は MOD と同じ分け方（新しいキャラは出自、途中導入は規則の土台＋調整。VERIFICATION.md §3.98「最初の見られ方」）
            p = player_material(save)
            records = rules.town_records((save.get("player_data") or {}).get("area_history") or {})
            if rules.is_new_character(records):
                standing_text = material.standing_material(
                    save, ledger, player_name=p["player_name"], profile=p["profile"],
                    lawfulness={}, achievements={}, life_log="")
                judged, raw, seconds = ask(material.standing_messages(standing_text), "standing")
                rules.apply_initial_standing(ledger, judged)
                row["standing_kind"] = "background"
            else:
                baseline = rules.baseline_scores(ledger, records)
                standing_text = material.adjust_material(
                    save, ledger, baseline, records, player_name=p["player_name"],
                    profile=p["profile"], rules=rules)
                judged, raw, seconds = ask(material.adjust_messages(standing_text), "adjust")
                rules.apply_adjusted_standing(ledger, baseline, judged)
                row["standing_kind"] = "deeds"
            row.update(standing_seconds=seconds, standing_raw=raw,
                       standing_chars=len(standing_text))
            row["standing"] = {f["name"]: (ledger["standing"][f["id"]]["score"],
                                           ledger["standing"][f["id"]]["title"])
                               for f in ledger["factions"]}
            # 主人公の評判の行はゲームと同じ形で渡す（`town_factions.trend`）
            standings = ["- {}: {}".format(f["name"], rules.STANDING_NAMES[rules.standing_stage(
                ledger["standing"][f["id"]]["score"])]) for f in ledger["factions"]]
            trend_text = material.trend_material(save, ledger, incidents=[], standings=standings)
            trend, raw, seconds = ask(material.trend_messages(trend_text, 30), "trend")
            # MOD が採るか（`town_factions.trend` と同じ読み方）
            names = {f["name"]: f["id"] for f in ledger["factions"]}
            kept = bool(trend) and llm.truthy(trend.get("happened"), unknown=True) \
                and rules.faction_id_of(names, trend.get("a")) is not None \
                and rules.faction_id_of(names, trend.get("b")) is not None \
                and bool(rules.to_int(trend.get("direction"), -1, 1))
            row.update(trend_seconds=seconds, trend=trend, trend_kept=kept)
        leaked = sorted({w for t in shown_texts(ledger, judged, trend) for w in leak_words if w in t})
        row["leaked"] = leaked
        results.append(row)
        print("    {} run {}: {} 勢力 {} 街 {} 関係 {} 捨てた {}/{} 段 {} 情勢 {} 漏れ {} （{}s）".format(
            name, index + 1, row["status"], row["factions"], len(row["towns"]), row["relations"],
            len(dropped), offered, row.get("standing"),
            "{}（{}）".format((trend or {}).get("rumor", "")[:40],
                             "採る" if row.get("trend_kept") else "採らない") if trend else "-",
            leaked or "なし",
            row["extract_seconds"]), flush=True)
    return results


def main():
    ap = argparse.ArgumentParser(description="339_town_factions の頼み文をローカル LLM で通す。")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    ap.add_argument("--world", action="append", help="測る世界（saves の下のフォルダ名）。省くと全部")
    ap.add_argument("--add-factions", action="append", default=[],
                    help="概要を勢力つきの文に差し替えて測る世界（--world にも数える）")
    ap.add_argument("--leak-words", default="", help="漏れとして数える語（読点区切り）")
    ap.add_argument("--design", action="store_true",
                    help="勢力が読み取れない世界に組織を設計させる（「この世界に組織を設ける」と同じ）")
    ap.add_argument("--anthropic-model", help="Claude API を直接呼ぶ（例 claude-haiku-5-5）")
    ap.add_argument("--api-key-file", help="ANTHROPIC_API_KEY が無いときにキーを読むファイル")
    ap.add_argument("--base-url", help="既に立っているサーバを使う（/v1 まで）")
    ap.add_argument("--api-model", default="probe")
    ap.add_argument("--model-pattern", default="Eclipsed")
    ap.add_argument("--model")
    ap.add_argument("--game-dir")
    ap.add_argument("--port", type=int, default=51989)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    module, material, rules = load_mod()
    limits = module.LIST_LIMITS
    shapes = schemas(None if args.anthropic_model else limits)
    api_key = ""
    if args.anthropic_model:
        api_key = read_api_key(args.api_key_file)
        if not api_key:
            print("  キーが無い: 環境変数 ANTHROPIC_API_KEY か --api-key-file で渡すこと。")
            return 2
    leak_words = [w for w in args.leak_words.split("、") if w]
    game_dir = find_game_dir(args.game_dir)
    sampling = read_sampling(read_live_config(game_dir).get("ai_setting") or {})

    worlds = []
    names = (args.world or []) + [w for w in args.add_factions if w not in (args.world or [])]
    for folder in saves.list_worlds():
        if names and folder not in names:
            continue
        save = saves.read_save(folder)
        if not isinstance(save, dict):
            print("  {} のセーブが読めない".format(folder))
            continue
        if folder in args.add_factions:
            save = json.loads(json.dumps(save))
            save["world_data"]["overview"] = FACTION_OVERVIEW
            folder = folder + "（勢力つき）"
        worlds.append((folder, save))

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, "faction_probe_{}.json".format(stamp))
    proc = handle = None
    rows = []
    try:
        if args.anthropic_model:
            base_url, api_model, label = None, None, args.anthropic_model
        elif args.base_url:
            base_url, api_model, label = args.base_url, args.api_model, args.api_model
        else:
            busy = running("instantale.exe", "llama-server.exe")
            if busy:
                print("  ゲームが動いている: {}。終了してから実行すること。".format(", ".join(busy)))
                return 2
            if args.model:
                model = find_model(game_dir, None, args.model)
            else:
                models_dir = os.path.join(game_dir, "runtime", "models", "llama_cpp")
                found = sorted(n for n in os.listdir(models_dir) if n.lower().endswith(".gguf")
                               and args.model_pattern.lower() in n.lower())
                if len(found) != 1:
                    print("  --model-pattern で1つに決まらない: {}".format(found))
                    return 2
                model = os.path.join(models_dir, found[0])
            config = read_live_config(game_dir).get("ai_setting") or {}
            backend = (config.get("local_model_setting") or {}).get(
                "llm_backend", "llama-cpp-completion-cuda")
            server = pick_build_dir(game_dir, backend) / "llama-server.exe"
            label = os.path.basename(str(model))
            print("  モデル: {}（起動中）".format(label), flush=True)
            handle = io.open(os.path.join(OUT_DIR, "faction_probe_server.log"), "w",
                             encoding="utf-8", errors="replace")
            proc = popen_server(
                [str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(args.port),
                 "--ctx-size", str(CTX_SIZE), "--n-gpu-layers", "999", "--cache-reuse", "256",
                 "--parallel", "1", "--no-mmproj", "--reasoning-budget", "0"],
                stdout=handle, stderr=subprocess.STDOUT)
            if not wait_ready(args.port, 300, proc):
                print("  起動しなかった。out\\faction_probe_server.log を読むこと。")
                return 1
            base_url, api_model = "http://127.0.0.1:{}/v1".format(args.port), "probe"
        print("  {}: {}  世界 {} 個 × {} 回".format(
            "モデル" if args.anthropic_model else "サンプリング",
            label if args.anthropic_model else sampling, len(worlds), args.runs), flush=True)

        def ask(messages, kind):
            if args.anthropic_model:
                return anthropic_chat(api_key, args.anthropic_model, messages, shapes[kind],
                                      args.timeout)
            return chat(base_url, api_model, messages, shapes[kind], sampling, args.timeout)

        for name, save in worlds:
            print("  [{}]".format(name), flush=True)
            rows += run_world(name, save, material, rules, args, ask, leak_words)
            with io.open(out_path, "w", encoding="utf-8") as fh:
                json.dump({"model": label, "sampling": sampling, "rows": rows}, fh,
                          ensure_ascii=False, indent=1)
    finally:
        if proc is not None:
            proc.kill()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        if handle is not None:
            handle.close()
    print()
    print("  結果: {}".format(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
