# -*- coding: utf-8 -*-
"""127_llm_response_speed をゲーム抜きで通す。

    python tools/tests/test_llm_response_speed.py

偽の `popen_sidecar` を差し込み、次を確認する。

  選り分け … llama-server の起動だけを掴む。背景除去のサイドカーは触らない
  既定     … --ctx-size は触らず、--parallel 1 だけを立てる
  合計     … CTX_SIZE を上げたときは「窓 × スロット数」を渡す
  上書き   … 既にある旗は書き換える。無い旗だけ末尾に足す
  渡し方   … 位置引数でもキーワード `args` でも、受け取った形のまま返す
  非破壊   … 呼び出し側のリストを書き換えない
  観測のみ … OBSERVE_ONLY では argv が1文字も変わらない
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_llm_response_speed")
MOD_NAME = "llm_response_speed_mod"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
GAME_ARGV = [
    r"C:\game\bin\llama-b7054-bin-win-cuda-12.4-x64\llama-server.exe",
    "-m", r"C:\game\runtime\models\llama_cpp\gemma-4-26B.gguf",
    "--host", "127.0.0.1", "--port", "56296",
    "--ctx-size", "16384",
    "--reasoning-budget", "0",
    "--alias", "gemma-4-26B", "--no-mmproj",
    "--n-gpu-layers", "999", "--cache-reuse", "256",
    "--temp", "1.0", "--top-p", "0.95", "--top-k", "64",
]

# 背景除去のサイドカー（`request_remove_background:popen_sidecar` の別名から同じ関数へ来る）。
# ここに `--ctx-size` を足したら事故なので、必ず素通しさせる。
REMBG_ARGV = [r"C:\game\bin\rembg\rembg.exe", "--model", "u2net", "--port", "1234"]


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.ready = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の
    # `ctx.logger` をそのまま借りる（検査だけが別経路を通らないように）。
    _mod = None

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def on_ready(self, fn, *, key=None, delay=0.0, force=False):
        self.ready.append(fn)
        return True

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(MOD_NAME, None)
        raise
    return module


def fresh_mod(**settings):
    """mod を読み直して当て直す（設定は既定に戻してから上書きする）。"""
    sys.modules.pop(MOD_NAME, None)
    module = load_mod()
    for key, value in settings.items():
        if getattr(module, key, None) is None:
            raise SystemExit("設定 {!r} がモジュールに無い".format(key))
        setattr(module, key, value)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def launch(ctx, argv, as_kwargs=False):
    """フック越しにサイドカーを起こす。戻り値は元関数が受け取った (args, kwargs)。"""
    seen = {}

    def orig(*a, **kw):
        seen["args"] = a
        seen["kwargs"] = kw
        return "popen"

    hook = ctx.hooks["sidecar_process:popen_sidecar"]
    if as_kwargs:
        result = hook(orig, args=argv, cwd=r"C:\game")
    else:
        result = hook(orig, argv, cwd=r"C:\game")
    seen["result"] = result
    return seen


def flag_value(argv, flag):
    for i, item in enumerate(argv):
        if item == flag and i + 1 < len(argv):
            return argv[i + 1]
    return None


def passed_argv(seen):
    if "args" in seen.get("kwargs", {}):
        return seen["kwargs"]["args"]
    return seen["args"][0]


# ---------------------------------------------------------------- 検査
print("選り分け")
module, ctx = fresh_mod()
seen = launch(ctx, list(REMBG_ARGV))
check("背景除去のサイドカーは素通し", passed_argv(seen) == REMBG_ARGV,
      passed_argv(seen))
check("素通しでは元関数の戻り値をそのまま返す", seen["result"] == "popen")
seen = launch(ctx, "llama-server.exe --ctx-size 16384")
check("文字列で来た argv は触らない（形が読めない）",
      passed_argv(seen) == "llama-server.exe --ctx-size 16384")

print("合計とスロット")
module, ctx = fresh_mod(CTX_SIZE=32768, SLOTS=2)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("--ctx-size は 窓 x スロット数", flag_value(argv, "--ctx-size") == "65536",
      flag_value(argv, "--ctx-size"))
check("--parallel を明示する", flag_value(argv, "--parallel") == "2",
      flag_value(argv, "--parallel"))
check("--ctx-size は1つだけ", argv.count("--ctx-size") == 1)
check("他の引数は落ちない",
      all(item in argv for item in ("--cache-reuse", "256", "--top-k", "64")))

module, ctx = fresh_mod(CTX_SIZE=32768, SLOTS=1)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("スロット1なら合計＝窓", flag_value(argv, "--ctx-size") == "32768",
      flag_value(argv, "--ctx-size"))

print("上書きと追加")
module, ctx = fresh_mod(CTX_SIZE=8192, SLOTS=3)
argv = passed_argv(launch(ctx, GAME_ARGV + ["--parallel", "2"]))
check("既にある --parallel は書き換える", flag_value(argv, "--parallel") == "3",
      flag_value(argv, "--parallel"))
check("--parallel も1つだけ", argv.count("--parallel") == 1)
argv = passed_argv(launch(ctx, [r"C:\bin\llama-server.exe", "-m", "x.gguf"]))
check("旗が無ければ末尾に足す",
      flag_value(argv, "--ctx-size") == "24576"
      and flag_value(argv, "--parallel") == "3", argv)

print("渡し方と非破壊")
module, ctx = fresh_mod(CTX_SIZE=32768, SLOTS=2)
original = list(GAME_ARGV)
seen = launch(ctx, original)
check("位置引数は位置引数のまま返す", "args" not in seen["kwargs"])
check("呼び出し側のリストを書き換えない", original == GAME_ARGV)
seen = launch(ctx, list(GAME_ARGV), as_kwargs=True)
check("キーワードはキーワードのまま返す", "args" in seen["kwargs"])
check("キーワード経路でも書き換わる",
      flag_value(seen["kwargs"]["args"], "--ctx-size") == "65536")
check("他のキーワードは残る", seen["kwargs"].get("cwd") == r"C:\game")

print("統合 KV（SLOTS=0）")
module, ctx = fresh_mod(CTX_SIZE=32768, SLOTS=0)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("--ctx-size は掛けずにそのまま", flag_value(argv, "--ctx-size") == "32768",
      flag_value(argv, "--ctx-size"))
check("--parallel は渡さない", "--parallel" not in argv, argv)
argv = passed_argv(launch(ctx, GAME_ARGV + ["--parallel", "1"]))
check("既にある --parallel は外す", "--parallel" not in argv, argv)
check("外した値は道連れにしない", "1" not in argv[len(GAME_ARGV):], argv)
check("他の引数は落ちない",
      all(item in argv for item in ("--cache-reuse", "256", "--top-k", "64")))

print("既定（速度だけ変える）")
module, ctx = fresh_mod()
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("--ctx-size はゲームの値のまま", flag_value(argv, "--ctx-size") == "16384",
      flag_value(argv, "--ctx-size"))
check("--parallel 1 を立てる", flag_value(argv, "--parallel") == "1",
      flag_value(argv, "--parallel"))
check("--ctx-size を1つも増やさない", argv.count("--ctx-size") == 1)
bare = [r"C:\bin\llama-server.exe", "-m", "x.gguf"]
argv = passed_argv(launch(ctx, list(bare)))
check("旗が無いなら --ctx-size は足さない", "--ctx-size" not in argv, argv)
check("旗が無くても --parallel は足す", flag_value(argv, "--parallel") == "1", argv)

print("チェックポイント（--parallel 1 の副作用打ち消し）")
OLD_HELP = "-cpent, --checkpoint-every-n-tokens N   create a checkpoint every n tokens\n"
NEW_HELP = ("-ctxcp, --ctx-checkpoints, --swa-checkpoints N\n"
            "-cms,  --checkpoint-min-step N          minimum spacing between context checkpoints\n")
CKPT = ("--checkpoint-every-n-tokens", "--checkpoint-min-step", "-cpent", "-cms")
module, ctx = fresh_mod()
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("--help が読めなければどれも足さない（存在しない実行ファイル）",
      not any(flag in argv for flag in CKPT), argv)
check("足さなくても --parallel 1 は立てる", flag_value(argv, "--parallel") == "1", argv)
module.server_help = lambda exe: "--parallel N\n"
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("どちらも知らないサーバには足さない", not any(flag in argv for flag in CKPT), argv)
check("知らなくても他の書き換えは残る", flag_value(argv, "--parallel") == "1", argv)
module.server_help = lambda exe: "--checkpoint-every-n-tokens-max N\n"
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("長い名前の一部には当たらない", not any(flag in argv for flag in CKPT), argv)
module.server_help = lambda exe: NEW_HELP
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("b10642 の形には --checkpoint-min-step 256 を足す",
      flag_value(argv, "--checkpoint-min-step") == "256", argv)
check("  旧い旗は足さない", "--checkpoint-every-n-tokens" not in argv, argv)
argv = passed_argv(launch(ctx, GAME_ARGV + ["-cms", "1024"]))
check("欄で -cms 指定済みなら上書きも追加もしない",
      flag_value(argv, "-cms") == "1024" and "--checkpoint-min-step" not in argv, argv)
module.server_help = lambda exe: OLD_HELP + NEW_HELP
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("両方知っているサーバには旧い旗だけ（先頭の1つ）",
      flag_value(argv, "--checkpoint-every-n-tokens") == "256"
      and "--checkpoint-min-step" not in argv, argv)
module.server_help = lambda exe: OLD_HELP
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("b7054 の形には -cpent を足す",
      flag_value(argv, "--checkpoint-every-n-tokens") == "256",
      flag_value(argv, "--checkpoint-every-n-tokens"))
check("  新しい旗は足さない", "--checkpoint-min-step" not in argv, argv)
argv = passed_argv(launch(ctx, GAME_ARGV + ["--checkpoint-every-n-tokens", "512"]))
check("欄で指定済みなら上書きしない",
      flag_value(argv, "--checkpoint-every-n-tokens") == "512",
      flag_value(argv, "--checkpoint-every-n-tokens"))
check("二重に足さない", argv.count("--checkpoint-every-n-tokens") == 1)
module, ctx = fresh_mod(SLOTS=0)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("統合に戻すときは足さない", not any(flag in argv for flag in CKPT), argv)

print("窓を黙って縮めない")
module, ctx = fresh_mod(CTX_SIZE=0, SLOTS=2)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("SLOTS=2 は合計を倍にして窓を保つ",
      flag_value(argv, "--ctx-size") == "32768", flag_value(argv, "--ctx-size"))
check("--parallel も合わせる", flag_value(argv, "--parallel") == "2")
module, ctx = fresh_mod(CTX_SIZE=0, SLOTS=4)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("SLOTS=4 なら4倍", flag_value(argv, "--ctx-size") == "65536",
      flag_value(argv, "--ctx-size"))
module, ctx = fresh_mod(CTX_SIZE=0, SLOTS=1)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("SLOTS=1 では触らない", flag_value(argv, "--ctx-size") == "16384",
      flag_value(argv, "--ctx-size"))

print("観測のみ")
module, ctx = fresh_mod(OBSERVE_ONLY=True)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("OBSERVE_ONLY では1文字も変えない", argv == GAME_ARGV, argv)

print("MTP の下書き役")
GEMMA = {"general.architecture": "gemma4", "gemma4.embedding_length": 2816,
         "tokenizer.ggml.model": "gemma4", "tokenizer.ggml.tokens": ("array", 262144)}
DRAFT = {"general.architecture": "gemma4-assistant", "gemma4-assistant.nextn_predict_layers": 4,
         "gemma4-assistant.embedding_length_out": 2816,
         "tokenizer.ggml.model": "gemma4", "tokenizer.ggml.tokens": ("array", 262144)}
module = fresh_mod()[0]
for name, value in zip(module.MTP_SAME_KEYS, (16, 512, 512, 256, 256, 1024, 1e6, 1e4, 512, 256)):
    GEMMA["gemma4." + name] = value
    DRAFT["gemma4-assistant." + name] = value
check("同じ構造の本体と下書き役は合う", module.mtp_matches(GEMMA, DRAFT)[0], module.mtp_matches(GEMMA, DRAFT))
qwen = dict(GEMMA, **{"general.architecture": "qwen35"})
check("構造が違えば合わない", not module.mtp_matches(qwen, DRAFT)[0])
wide = dict(GEMMA, **{"gemma4.embedding_length": 5376})
check("幅が違えば合わない（同じ Gemma 4 でも 31B など）", not module.mtp_matches(wide, DRAFT)[0])
swa = dict(GEMMA, **{"gemma4.attention.sliding_window": 512})
check("注意の形が1つでも違えば合わない", not module.mtp_matches(swa, DRAFT)[0])
no_layers = {k: v for k, v in DRAFT.items() if "nextn" not in k}
check("MTP の層が無い下書き役は合わない", not module.mtp_matches(GEMMA, no_layers)[0])
missing = {k: v for k, v in GEMMA.items() if k != "gemma4.rope.freq_base"}
check("読めない値があれば合わない（付けない側に倒す）", not module.mtp_matches(missing, DRAFT)[0])

MTP_HELP = NEW_HELP + "-md, --model-draft FNAME\n--spec-type none,draft-simple,draft-mtp,ngram-mod\n"
DRAFT_PATH = r"C:\game\runtime\models\llama_cpp\mtp\mtp-gemma.gguf"
module, ctx = fresh_mod()
module.server_help = lambda exe: MTP_HELP
module.find_drafter = lambda argv: (DRAFT_PATH, "合う")
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("合う下書き役があれば --model-draft と draft-mtp を足す",
      flag_value(argv, "--model-draft") == DRAFT_PATH and flag_value(argv, "--spec-type") == "draft-mtp"
      and flag_value(argv, "--spec-draft-n-max") == "4", argv)
check("  チェックポイントと --parallel 1 も残る",
      flag_value(argv, "--checkpoint-min-step") == "256" and flag_value(argv, "--parallel") == "1", argv)
argv = passed_argv(launch(ctx, GAME_ARGV + ["--spec-type", "ngram-mod"]))
check("欄で投機の旗が指定済みなら MTP に触らない",
      "--model-draft" not in argv and argv.count("--spec-type") == 1, argv)
module.server_help = lambda exe: NEW_HELP
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("MTP を知らないサーバには足さない", "--model-draft" not in argv, argv)
module.server_help = lambda exe: MTP_HELP
module.find_drafter = lambda argv: (None, "合う下書き役が無い")
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("合う下書き役が無ければ足さない", "--model-draft" not in argv, argv)
module, ctx = fresh_mod(MTP=False)
module.server_help = lambda exe: MTP_HELP
module.find_drafter = lambda argv: (DRAFT_PATH, "合う")
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("設定で切れば足さない", "--model-draft" not in argv, argv)

print("MTP を付けて起きなかったら外して起こし直す")
module, ctx = fresh_mod()
module.server_help = lambda exe: MTP_HELP
module.find_drafter = lambda argv: (DRAFT_PATH, "合う")
module._MTP_STATE.failed.clear()
started = []


def fake_set_ai_models(*_args, **_kwargs):
    seen = launch(ctx, list(GAME_ARGV))           # ゲームはこの中で llama-server を起こす
    started.append(passed_argv(seen))
    if "--model-draft" in started[-1]:
        raise RuntimeError("同梱llama-serverが起動しませんでした")
    return "ok"


result = ctx.hooks["__main__:AIManager.set_ai_models"](fake_set_ai_models, "manager")
check("2回目は MTP を外して起き、ゲームには成功を返す",
      result == "ok" and len(started) == 2 and "--model-draft" in started[0]
      and "--model-draft" not in started[1], started)
check("  WARN を残す", any(level == "WARN" and "MTP" in msg for level, msg in ctx.logs), ctx.logs)
started.clear()
result = ctx.hooks["__main__:AIManager.set_ai_models"](fake_set_ai_models, "manager")
check("  同じ組はこのプロセスの間は付けない（1回で起きる）",
      result == "ok" and len(started) == 1 and "--model-draft" not in started[0], started)


def broken(*_args, **_kwargs):
    launch(ctx, list(REMBG_ARGV))
    raise RuntimeError("別の理由")


raised = False
try:
    ctx.hooks["__main__:AIManager.set_ai_models"](broken, "manager")
except RuntimeError:
    raised = True
check("MTP を付けていない失敗はそのまま上げる（握り潰さない）", raised)
module._MTP_STATE.failed.clear()

print("GGUF の情報を読む")
import struct as _struct  # noqa: E402


def gguf_bytes(pairs):
    out = [b"GGUF", _struct.pack("<I", 3), _struct.pack("<QQ", 0, len(pairs))]
    for key, kind, value in pairs:
        raw = key.encode("utf-8")
        out.append(_struct.pack("<Q", len(raw)) + raw + _struct.pack("<I", kind))
        if kind == 8:
            data = value.encode("utf-8")
            out.append(_struct.pack("<Q", len(data)) + data)
        elif kind == 4:
            out.append(_struct.pack("<I", value))
        elif kind == 9:
            out.append(_struct.pack("<IQ", 8, len(value)))
            for item in value:
                data = item.encode("utf-8")
                out.append(_struct.pack("<Q", len(data)) + data)
    return b"".join(out)


path = os.path.join(OUT_DIR, "meta_probe.gguf")
with open(path, "wb") as fh:
    fh.write(gguf_bytes([("general.architecture", 8, "gemma4"),
                         ("gemma4.embedding_length", 4, 2816),
                         ("tokenizer.ggml.tokens", 9, ["t{}".format(i) for i in range(100)])]))
meta = module.gguf_meta(path)
check("構造の名前・数・長い語彙の並び（数だけ）を読む",
      meta == {"general.architecture": "gemma4", "gemma4.embedding_length": 2816,
               "tokenizer.ggml.tokens": ("array", 100)}, meta)
with open(path, "wb") as fh:
    fh.write(b"NOPE")
module._META_CACHE.clear()
check("GGUF でなければ None", module.gguf_meta(path) is None)
os.remove(path)

print("設定の宣言")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    declared = json.load(fh)["settings"]
module = fresh_mod()[0]
check("mod.json と定数の既定値が一致",
      all(getattr(module, key) == spec["default"]
          for key, spec in declared.items()),
      {key: (getattr(module, key, None), spec["default"])
       for key, spec in declared.items()})

print("")
if failures:
    print("FAILED: {} 件".format(len(failures)))
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通過")
