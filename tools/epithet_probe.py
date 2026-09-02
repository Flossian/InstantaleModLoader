# -*- coding: utf-8 -*-
"""317_reputation の二つ名を実際のローカル LLM で引いて、偏りを測る。

    python tools\\epithet_probe.py                        # llama-server を起こして測る
    python tools\\epithet_probe.py --runs 10              # 場面ごとの回数を変える
    python tools\\epithet_probe.py --model-pattern XL     # 別の GGUF で測る
    python tools\\epithet_probe.py --base-url http://127.0.0.1:1234/v1 --api-model <名前>

ゲーム抜きで llama-server を直接起こす形は `llm_ctx_probe.py` と同じ
（ゲームは終了しておくこと。VRAM とポートを取り合う）。
既に立っているサーバ（LM Studio など）で測るなら `--base-url` を渡す。
base_url は `/v1` まで含めること（欠けると HTTP 200 で無言に失敗する）。

測るものは3つ。

  頼み文     MOD の `build_epithet_messages`（2段目）を**そのまま**使う。
             写すと、MOD 側の頼み文を直したときに測り直した気になれてしまう。
             入力は各地の評判文（1段目の出力に当たる固定の文）で、
             実装と同じく素の出来事は渡さない
  読み取り   MOD の `parse_epithet` を使う。読めない返答の率もそのまま観測になる
  偏り       同じ評判から二つ名がどれだけ同じ形に寄るか。
             場面内の重複・「<地名>の◯◯」の形・末尾の語の使い回しを数える

場面は7つ。善行・悪名・依頼のみ・医療・グレー・複数の土地、
そして「同じ行いで土地の名前だけ違う」対（hero_ash / hero_port）。
対の側は、二つ名が行いではなく**地名から**作られていないかを見るためにある。

`--current 灰の街の盾` を渡すと「いままでの二つ名」を頼み文に載せた形で測れる
（D案の据え置き率。集計に「据え置き」の行が増える）。
2026-08-24 以前の記録（`out\\epithet_probe_*.json`）は旧1段目の頼み文で測ったもの。

サンプリングはゲームの config.json の `llama-cpp-completion-cuda` から読む
（既定 --temp 1.0 --top-p 0.95 --top-k 64）。seed は渡さない＝毎回変わる。

結果は `out\\epithet_probe_<時刻>.json`（生の返答ぜんぶ）と標準出力の集計。
集計の読み方と判断は VERIFICATION_LOG.md §2.63 側に書く。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import importlib.util
import io
import json
import os
import re
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

# サーバの起こし方・ゲームの見つけ方は文脈長プローブと共通。
from llm_ctx_probe import (find_game_dir, find_model, http_json,  # noqa: E402
                           pick_build_dir, read_live_config, running,
                           wait_ready)

#: 場面ごとの既定の回数。temp 1.0 なので回すたびに変わる。
DEFAULT_RUNS = 20

#: 1返答の上限トークン。JSON＋評判120字＋二つ名でこの半分も使わない。
MAX_TOKENS = 300

#: 起動時に使う窓。頼み文は600トークン程度なので狭くてよい
#: （VRAM の崖は `llm_ctx_probe` の領分。ここでは踏まない）。
CTX_SIZE = 8192

#: 二つ名の末尾の語（最後の「の」より後ろ）。使い回しを数える単位。
def tail_of(epithet):
    return epithet.rsplit("の", 1)[-1] if "の" in epithet else epithet


#: 7場面。`build_epithet_messages` が受ける形（`(土地名, 評判文)` の並び）。
#: 評判文は1段目の出力に当たる固定の文で、旧場面（素の出来事）から書き起こした。
#: hero_ash と hero_port は**評判が同じで土地の名前だけ違う**対。
#: two_lands だけが複数の土地（統合の頼み文にしか無い形）。
SCENARIOS = [
    ("hero_ash", "リン", {
        "entries": [("灰の街",
                     "盗賊団を退け、涸れた井戸を掘り直した者としてよく知られている。")],
    }),
    ("hero_port", "リン", {
        "entries": [("遠い港",
                     "盗賊団を退け、涸れた井戸を掘り直した者としてよく知られている。")],
    }),
    ("villain", "リン", {
        "entries": [("灰の街",
                     "商家を襲い、衛兵を殴って逃げた手配者として恐れられている。")],
    }),
    ("quest_only", "リン", {
        "entries": [("白樺の村",
                     "迷子の捜索や薬草の納品など、頼み事を確かに片付ける者と評判になっている。")],
    }),
    ("healer", "リン", {
        "entries": [("泉の町",
                     "流行り病に効く薬を届け、産婆を夜通し手伝った恩人として語られている。")],
    }),
    ("shady", "リン", {
        "entries": [("塩の湊",
                     "喧嘩の仲裁もすれば密輸の見張りも引き受ける、掴みどころのない者と噂されている。")],
    }),
    ("two_lands", "リン", {
        "entries": [("灰の街",
                     "盗賊団を退けた者としてよく知られている。"),
                    ("泉の町",
                     "流行り病に効く薬を届けた恩人として語られている。"),
                    ("塩の湊",
                     "密輸の見張りを引き受けた手配者として警戒されている。")],
    }),
]


def load_mod():
    """317_reputation を検査と同じ形で読む（`test_reputation.py` §find_mod）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith("_reputation")
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("*_reputation が1つに決まらない: {}".format(matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(
        "reputation_mod", os.path.join(folder, entry),
        submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules["reputation_mod"] = module
    spec.loader.exec_module(module)
    return module


def read_sampling(config):
    """config.json の起動引数からサンプリングを読む。読めない項目は既定へ。"""
    line = (config.get("server_parameters") or {}).get(
        "llama-cpp-completion-cuda", "")
    def pick(flag, default):
        found = re.search(re.escape(flag) + r"\s+([\d.]+)", line)
        return float(found.group(1)) if found else default
    return {"temperature": pick("--temp", 1.0),
            "top_p": pick("--top-p", 0.95),
            "top_k": int(pick("--top-k", 64))}


def chat(base_url, api_model, messages, sampling, timeout):
    """OpenAI 互換の chat 1回。返答の本文か None。"""
    body = {"model": api_model, "messages": messages,
            "max_tokens": MAX_TOKENS, "cache_prompt": True}
    body.update(sampling)
    try:
        got = http_json(base_url.rstrip("/") + "/chat/completions", body,
                        timeout=timeout)
        return got["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
        print("      失敗: {}".format(exc), flush=True)
        return None


def collect(module, base_url, api_model, sampling, runs, timeout, jsonl_path,
            current=""):
    """全場面を回して生の結果を集める。1回ごとに JSONL へも追記する。

    追記しておくのは、実行が途中で切れても採取した分が残るようにするため
    （120回で10分を超えることがある）。
    """
    rows = []
    jsonl = io.open(jsonl_path, "a", encoding="utf-8")
    for key, player, item in SCENARIOS:
        messages = module.build_epithet_messages(player, item["entries"], current)
        print("  [{}] {} …".format(
            key, "・".join(name for name, _text in item["entries"])), flush=True)
        for index in range(runs):
            started = time.monotonic()
            raw = chat(base_url, api_model, messages, sampling, timeout)
            parsed = module.parse_epithet(raw)
            name = (parsed or {}).get("epithet", "")
            rows.append({
                "scenario": key,
                "run": index + 1,
                "seconds": round(time.monotonic() - started, 1),
                "raw": raw,
                "epithet": name,
                "description": (parsed or {}).get("description", ""),
                "parsed": parsed is not None,
            })
            jsonl.write(json.dumps(rows[-1], ensure_ascii=False) + "\n")
            jsonl.flush()
            mark = name or ("（二つ名なし）" if parsed is not None else "（読めない）")
            print("      {:2d}/{} {}".format(index + 1, runs, mark), flush=True)
    return rows


def summarize(rows, module, current=""):
    """偏りの集計。返すのは印字用の行の並び。"""
    lines = []
    by_scene = collections.defaultdict(list)
    for row in rows:
        by_scene[row["scenario"]].append(row)

    areas_of = {key: [name for name, _text in item["entries"]]
                for key, _p, item in SCENARIOS}
    player_of = {key: player for key, player, _i in SCENARIOS}
    pooled = []            # (場面, 二つ名)
    unreadable = sum(1 for row in rows if not row["parsed"])
    empty = sum(1 for row in rows if row["parsed"] and not row["epithet"])

    lines.append("---- 場面ごと ----")
    for key, _player, _item in SCENARIOS:
        got = [row["epithet"] for row in by_scene[key] if row["epithet"]]
        pooled.extend((key, name) for name in got)
        counts = collections.Counter(got)
        lines.append("[{}] {}回 / 二つ名 {}件 / 異なり {}種".format(
            key, len(by_scene[key]), len(got), len(counts)))
        for name, count in counts.most_common():
            lines.append("    {:2d}x {}".format(count, name))

    lines.append("")
    lines.append("---- 形の偏り ----")
    total = len(pooled)
    with_area = sum(1 for key, name in pooled
                    if any(area in name for area in areas_of[key]))
    formula = sum(1 for _key, name in pooled if "の" in name)
    lines.append("読めない返答       : {} / {}".format(unreadable, len(rows)))
    lines.append("二つ名が空         : {} / {}".format(empty, len(rows)))
    lines.append("地名をそのまま含む : {} / {}".format(with_area, total))
    lines.append("「…の…」の形       : {} / {}".format(formula, total))
    echo = sum(1 for key, name in pooled
               if module.echoes_material(name, player_of[key], None))
    lines.append("本人の名前の写し   : {} / {}".format(echo, total))
    if current:
        kept = sum(1 for _key, name in pooled if name == current)
        lines.append("据え置き（={!r}）  : {} / {}".format(current, kept, total))

    tails = collections.Counter(tail_of(name) for _key, name in pooled)
    scenes_of = collections.defaultdict(set)
    for key, name in pooled:
        scenes_of[tail_of(name)].add(key)
    lines.append("末尾の語（複数場面に出たものは場面数も）:")
    for tail, count in tails.most_common(12):
        spread = len(scenes_of[tail])
        lines.append("    {:2d}x {}{}".format(
            count, tail, "（{}場面）".format(spread) if spread > 1 else ""))

    # 対になっている hero_ash / hero_port: 行いが同じなので、
    # 二つ名の**地名以外の部分**が重なるかを見る。
    ash = {tail_of(row["epithet"]) for row in by_scene["hero_ash"] if row["epithet"]}
    port = {tail_of(row["epithet"]) for row in by_scene["hero_port"] if row["epithet"]}
    lines.append("同じ行いの対（ash/port）で共通の末尾の語: {}".format(
        "、".join(sorted(ash & port)) or "（無し）"))
    return lines


def main():
    ap = argparse.ArgumentParser(description="317_reputation の二つ名の偏りを測る。")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                    help="場面ごとの回数（既定 %d）" % DEFAULT_RUNS)
    ap.add_argument("--base-url", help="既に立っているサーバを使う（/v1 まで）")
    ap.add_argument("--api-model", default="probe",
                    help="--base-url 側に渡すモデル名（LM Studio では必須）")
    ap.add_argument("--model-pattern", default="gemma",
                    help="起こす GGUF の絞り込み（既定 gemma。複数残れば一覧を出す）")
    ap.add_argument("--model", help="GGUF をフルパスで名指しする")
    ap.add_argument("--game-dir")
    ap.add_argument("--port", type=int, default=51988)
    ap.add_argument("--timeout", type=int, default=180,
                    help="1回の返答を待つ秒数（既定 180）")
    ap.add_argument("--tag", default="", help="出力ファイル名に足す印")
    ap.add_argument("--current", default="",
                    help="「いままでの二つ名」を頼み文に載せて測る（据え置き率の観測）")
    args = ap.parse_args()

    module = load_mod()
    game_dir = find_game_dir(args.game_dir)
    # config.json は `ai_setting` の下に起動引数を持つ（無ければ既定へ落ちる）。
    sampling = read_sampling(read_live_config(game_dir).get("ai_setting") or {})

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, "epithet_probe_{}{}.json".format(
        stamp, ("_" + args.tag) if args.tag else ""))

    proc = None
    handle = None
    try:
        if args.base_url:
            base_url = args.base_url
            api_model = args.api_model
            model_label = "{} @ {}".format(api_model, base_url)
        else:
            busy = running("instantale.exe", "llama-server.exe")
            if busy:
                print("  ゲームが動いている: {}".format(", ".join(busy)))
                print("  VRAM とポートを取り合うので、終了してから実行すること。")
                return 2
            if args.model:
                model = find_model(game_dir, None, args.model)
            else:
                models_dir = os.path.join(game_dir, "runtime", "models", "llama_cpp")
                found = sorted(name for name in os.listdir(models_dir)
                               if name.lower().endswith(".gguf")
                               and args.model_pattern.lower() in name.lower())
                if len(found) != 1:
                    print("  --model-pattern {!r} で1つに決まらない:".format(
                        args.model_pattern))
                    for name in found:
                        print("    " + name)
                    print("  --model-pattern を狭めるか --model で名指しすること。")
                    return 2
                model = os.path.join(models_dir, found[0])
            config = read_live_config(game_dir).get("ai_setting") or {}
            backend = (config.get("local_model_setting") or {}).get(
                "llm_backend", "llama-cpp-completion-cuda")
            server = pick_build_dir(game_dir, backend) / "llama-server.exe"
            print("  モデル: {}".format(os.path.basename(str(model))))
            print("  起動中（読み込みに数分かかることがある）…", flush=True)
            logfile = os.path.join(OUT_DIR, "epithet_probe_server.log")
            handle = io.open(logfile, "w", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [str(server), "-m", str(model),
                 "--host", "127.0.0.1", "--port", str(args.port),
                 "--ctx-size", str(CTX_SIZE), "--n-gpu-layers", "999",
                 "--cache-reuse", "256", "--parallel", "1", "--no-mmproj",
                 # 思考を吐くモデル（gemma-4 heretic 等）は、思考を切らないと
                 # MAX_TOKENS を全部思考に使い切って本文が空になる（VERIFICATION_LOG.md §2.63）。
                 "--reasoning-budget", "0"],
                stdout=handle, stderr=subprocess.STDOUT)
            if not wait_ready(args.port, 300):
                print("  起動しなかった。{} を読むこと。".format(logfile))
                return 1
            base_url = "http://127.0.0.1:{}/v1".format(args.port)
            api_model = "probe"
            model_label = os.path.basename(str(model))

        print("  サンプリング: {}".format(sampling))
        print()
        rows = collect(module, base_url, api_model, sampling, args.runs,
                       args.timeout, out_path.replace(".json", ".jsonl"),
                       current=args.current)
    finally:
        if proc is not None:
            proc.kill()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
        if handle is not None:
            handle.close()

    summary = summarize(rows, module, current=args.current)
    print()
    for line in summary:
        print("  " + line)

    with io.open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "model": model_label,
            "sampling": sampling,
            "runs_per_scenario": args.runs,
            "current": args.current,
            "prompts": {key: module.build_epithet_messages(
                            player, item["entries"], args.current)
                        for key, player, item in SCENARIOS},
            "rows": rows,
            "summary": summary,
        }, fh, ensure_ascii=False, indent=2)
    print()
    print("  生の結果: {}".format(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
