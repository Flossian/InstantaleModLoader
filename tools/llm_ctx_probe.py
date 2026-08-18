"""ローカル LLM のコンテキスト長を実測して、このマシンでの最適値を出す。

llama-server を候補の `--ctx-size` で順に起こし、
同じプロンプトを投げて生成速度と VRAM を測る。
VRAM から溢れると Windows は失敗せず共有メモリへ退避するため、
症状は「エラー」ではなく「異様に遅い」になる。
速度が崩れる一段手前を、そのマシンで使える上限とする。

使い方は `llm_ctx_probe.bat` から。
ゲームは終了しておくこと（VRAM とポートを取り合うため）。
測定の背景と実測値は `docs\\VERIFICATION_LOG.md` §2.48 を参照。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 1リクエストぶんのプロンプト。
# 全構成で同じものを使う（比較のため）。
FILLER = "the adventurer walked into the tavern and ordered a drink. "
PROMPT_TOKENS = 4000

# 既定の候補。
# ゲームの既定 16384 から上へ辿る。
DEFAULT_LADDER = [16384, 24576, 32768, 49152, 65536]

# 速度がこの割合を下回ったら「崩壊」とみなし、そこで打ち切る。
COLLAPSE_RATIO = 0.70
# 推奨値に採用する速度の下限（最速比）。
KEEP_RATIO = 0.90
# 推奨値に残す VRAM の余白（MiB）。
DEFAULT_MARGIN = 200
# ゲーム本体の描画ぶんとして差し引く量（MiB）。
DEFAULT_RESERVE = 300

BACKEND_KEYWORD = {"cuda": "cuda", "vulkan": "vulkan", "cpu": "cpu"}


def log(msg: str = "") -> None:
    print(msg, flush=True)


# --- 環境を見つける -------------------------------------------------------

def find_game_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    manifests = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
    for item in sorted(manifests.glob("*.item")):
        try:
            data = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        loc = data.get("InstallLocation", "")
        if "nstantale" in loc or "nstantale" in data.get("DisplayName", ""):
            return Path(loc)
    raise SystemExit("ゲームのフォルダが見つからない。--game-dir で渡すこと。")


def read_live_config(game_dir: Path) -> dict:
    """ゲームが実際に読み書きする config.json。無ければインストール先のもの。"""
    local = Path.home() / "AppData" / "Local" / "Darmabeko" / "Instantale" / "config.json"
    for path in (local, game_dir / "config.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
    return {}


def pick_build_dir(game_dir: Path, backend: str) -> Path:
    """`llama-cpp-completion-cuda` のようなバックエンド名から bin\\ の実体を選ぶ。"""
    keyword = "cpu"
    for key, word in BACKEND_KEYWORD.items():
        if key in backend:
            keyword = word
    candidates = sorted((game_dir / "bin").glob("llama-*-bin-win-*"))
    for path in candidates:
        if keyword in path.name and (path / "llama-server.exe").exists():
            return path
    for path in candidates:
        if (path / "llama-server.exe").exists():
            return path
    raise SystemExit("llama-server.exe が bin\\ に見つからない。")


def find_model(game_dir: Path, name: str | None, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    models = game_dir / "runtime" / "models" / "llama_cpp"
    if name:
        path = models / (name + ".gguf")
        if path.exists():
            return path
    found = sorted(models.glob("*.gguf"), key=lambda p: p.stat().st_size, reverse=True)
    if not found:
        raise SystemExit("GGUF が見つからない。--model で渡すこと。")
    return found[0]


def nvidia_memory() -> tuple[int, int] | None:
    """(使用中, 総量) を MiB で返す。nvidia-smi が無ければ None。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    used, total = out.stdout.strip().splitlines()[0].split(",")
    return int(used), int(total)


def running(*names: str) -> list[str]:
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             timeout=30, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    lowered = out.stdout.lower()
    return [n for n in names if n.lower() in lowered]


# --- 1構成を測る ----------------------------------------------------------

def make_prompt() -> str:
    # 1トークン ≒ 3語弱。
    # 多めに作っても構わない（窓には収まる）。
    repeats = max(1, PROMPT_TOKENS // 12)
    return FILLER * repeats


def http_json(url: str, payload: dict | None = None, timeout: int = 900) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def wait_ready(port: int, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if http_json("http://127.0.0.1:%d/health" % port, timeout=5).get("status") == "ok":
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(2)
    return False


def parse_server_log(text: str) -> dict:
    def first(pattern: str) -> float:
        m = re.search(pattern, text)
        return float(m.group(1)) if m else 0.0

    kv = sum(float(x) for x in re.findall(
        r"KV buffer size\s*=\s*([\d.]+) MiB", text))
    return {
        "model_mib": first(r"model buffer size\s*=\s*([\d.]+) MiB"),
        "kv_mib": kv,
        "compute_mib": first(r"CUDA0 compute buffer size\s*=\s*([\d.]+) MiB"),
        "n_ctx_seq": first(r"n_ctx_seq\s*=\s*(\d+)"),
        "unified": "kv_unified    = true" in text,
    }


def probe(server: Path, model: Path, ctx: int, parallel: int | None,
          port: int, logdir: Path, prompt: str, load_timeout: int) -> dict | None:
    """1構成ぶん起こして測る。起動しなければ None。"""
    logfile = logdir / ("llm_ctx_probe_%d_%s.log" % (ctx, parallel or "auto"))
    cmd = [str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
           "--ctx-size", str(ctx), "--n-gpu-layers", "999", "--cache-reuse", "256",
           "--no-mmproj"]
    if parallel:
        cmd += ["--parallel", str(parallel)]

    handle = logfile.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)
    try:
        if not wait_ready(port, load_timeout):
            return None
        used = nvidia_memory()
        result = {"ctx": ctx, "parallel": parallel or "auto",
                  "vram_mib": used[0] if used else 0}
        handle.flush()
        result.update(parse_server_log(logfile.read_text(encoding="utf-8", errors="replace")))

        best_pp = best_tg = 0.0
        for _ in range(2):
            body = http_json("http://127.0.0.1:%d/completion" % port,
                             {"prompt": prompt, "n_predict": 128, "cache_prompt": False})
            timings = body.get("timings", {})
            best_pp = max(best_pp, float(timings.get("prompt_per_second") or 0))
            best_tg = max(best_tg, float(timings.get("predicted_per_second") or 0))
        result["pp"] = best_pp
        result["tg"] = best_tg
        return result
    finally:
        proc.kill()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        handle.close()
        time.sleep(6)  # VRAM が返るのを待つ


# --- 本体 -----------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="ローカル LLM のコンテキスト長の最適値を実測する。")
    ap.add_argument("--game-dir")
    ap.add_argument("--model")
    ap.add_argument("--parallel", type=int, default=1,
                    help="スロット数。0 で --parallel を渡さない（統合 KV）")
    ap.add_argument("--ctx-list", help="候補をカンマ区切りで（既定 %s）"
                    % ",".join(str(c) for c in DEFAULT_LADDER))
    ap.add_argument("--reserve", type=int, default=DEFAULT_RESERVE,
                    help="ゲーム本体の描画ぶんとして差し引く MiB（既定 %d）" % DEFAULT_RESERVE)
    ap.add_argument("--margin", type=int, default=DEFAULT_MARGIN,
                    help="推奨値に残す余白 MiB（既定 %d）" % DEFAULT_MARGIN)
    ap.add_argument("--port", type=int, default=51987)
    ap.add_argument("--load-timeout", type=int, default=300)
    args = ap.parse_args()

    busy = running("instantale.exe", "llama-server.exe")
    if busy:
        log("  ゲームが動いている: %s" % ", ".join(busy))
        log("  VRAM とポートを取り合うので、終了してから実行すること。")
        return 2

    game_dir = find_game_dir(args.game_dir)
    config = read_live_config(game_dir)
    local = config.get("ai_setting", {}).get("local_model_setting", {})
    backend = local.get("llm_backend", "llama-cpp-completion-cpu")
    build_dir = pick_build_dir(game_dir, backend)
    model = find_model(game_dir, (local.get("local_llm") or {}).get("name"), args.model)
    ladder = ([int(x) for x in args.ctx_list.split(",")] if args.ctx_list
              else list(DEFAULT_LADDER))
    parallel = args.parallel or None

    mem = nvidia_memory()
    total = mem[1] if mem else 0
    log("  ゲーム     : %s" % game_dir)
    log("  バックエンド: %s -> %s" % (backend, build_dir.name))
    log("  モデル     : %s (%.2f GiB)" % (model.name, model.stat().st_size / 1024**3))
    if mem:
        log("  VRAM       : 総量 %d MiB / 実行前の使用 %d MiB" % (total, mem[0]))
    else:
        log("  VRAM       : nvidia-smi が無いので速度だけで判定する")
    log("  スロット   : %s" % (parallel or "auto（--parallel を渡さない）"))
    log("  候補       : %s" % ", ".join(str(c) for c in ladder))
    log()
    log("  1構成ごとにモデルを読み直すので数分かかる。")
    log()

    outdir = Path(__file__).resolve().parent.parent / "out"
    outdir.mkdir(exist_ok=True)
    prompt = make_prompt()
    rows: list[dict] = []
    peak_tg = 0.0

    for ctx in ladder:
        log("  [%d/%d] --ctx-size %d を測定中..." % (len(rows) + 1, len(ladder), ctx))
        row = probe(build_dir / "llama-server.exe", model, ctx, parallel,
                    args.port, outdir, prompt, args.load_timeout)
        if row is None:
            log("        起動しなかった。ここで打ち切る。")
            break
        rows.append(row)
        peak_tg = max(peak_tg, row["tg"])
        log("        窓 %d / KV %.0f MiB / VRAM %d MiB / 生成 %.1f t/s"
            % (row["n_ctx_seq"] or ctx, row["kv_mib"], row["vram_mib"], row["tg"]))
        if row["tg"] < peak_tg * COLLAPSE_RATIO:
            log("        速度が崩れた（最速比 %.0f%%）。VRAM から溢れている。"
                % (100 * row["tg"] / peak_tg))
            break

    if not rows:
        log("  一度も測れなかった。")
        return 1

    # 推奨値。
    # 速度が最速比 KEEP_RATIO 以上で、余白が足りる中の最大。
    best = None
    slow_only = False   # 速度で落ちたものがあるか
    tight_only = False  # 余白で落ちたものがあるか
    for row in rows:
        if row["tg"] < peak_tg * KEEP_RATIO:
            slow_only = True
            continue
        if total and total - row["vram_mib"] - args.reserve < args.margin:
            tight_only = True
            continue
        best = row

    log()
    log("  ---- 実測 ----")
    log("  %9s %8s %10s %8s %9s %9s" % ("ctx", "窓", "KV(MiB)", "VRAM", "生成t/s", "処理t/s"))
    for row in rows:
        mark = "  <-" if best is row else ""
        log("  %9d %8d %10.0f %8d %9.1f %9.0f%s"
            % (row["ctx"], row["n_ctx_seq"] or row["ctx"], row["kv_mib"],
               row["vram_mib"], row["tg"], row["pp"], mark))
    log()

    if best is None:
        log("  ---- 推奨できる設定が無い ----")
        if tight_only:
            log("  速度は保てているが、ゲーム本体のぶん（%d MiB）を引くと余白が"
                % args.reserve)
            log("  %d MiB を切る。遊んでいる最中に溢れる。" % args.margin)
        if slow_only:
            log("  一番狭い候補でもう速度が落ちている。モデルが VRAM に対して大きい。")
        if parallel is None:
            log()
            log("  --parallel 1 で測り直すこと。スロットを1本にすると KV が減るので、")
            log("  同じ窓でも収まることがある（このゲームは並列でほとんど走らない）。")
        else:
            log()
            log("  候補を下げて測り直すか（例: --ctx-list 8192,12288,16384）、")
            log("  小さいモデルを使うこと。")
        return 0

    log("  ---- 推奨 ----")
    if total:
        log("  VRAM 総量 %d − 実測 %d − ゲーム描画ぶん %d = 余白 %d MiB"
            % (total, best["vram_mib"], args.reserve,
               total - best["vram_mib"] - args.reserve))
    log()
    log("  127_llm_response_speed の設定:")
    log("      1リクエストが使えるトークン : %d" % (best["n_ctx_seq"] or best["ctx"]))
    log("      同時に持つスロット数        : %s" % (parallel or 4))
    log()
    log("  日本語でおよそ %d 字ぶん（1トークン≒1.6字）。"
        % int((best["n_ctx_seq"] or best["ctx"]) * 1.6))
    if total and best["vram_mib"] > total - args.reserve - args.margin * 2:
        log("  余白が薄い。ゲームを動かした状態で nvidia-smi を一度見ておくこと。")
    log()
    log("  サーバのログは out\\llm_ctx_probe_*.log に残してある。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"], capture_output=True)
        log("\n  中断した。起動していた llama-server は落とした。")
        sys.exit(130)
