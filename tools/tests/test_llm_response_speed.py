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

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

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
module, ctx = fresh_mod()
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("既定で -cpent を足す",
      flag_value(argv, "--checkpoint-every-n-tokens") == "256",
      flag_value(argv, "--checkpoint-every-n-tokens"))
argv = passed_argv(launch(ctx, GAME_ARGV + ["--checkpoint-every-n-tokens", "512"]))
check("欄で指定済みなら上書きしない",
      flag_value(argv, "--checkpoint-every-n-tokens") == "512",
      flag_value(argv, "--checkpoint-every-n-tokens"))
check("二重に足さない", argv.count("--checkpoint-every-n-tokens") == 1)
module, ctx = fresh_mod(SLOTS=0)
argv = passed_argv(launch(ctx, list(GAME_ARGV)))
check("統合に戻すときは足さない", "--checkpoint-every-n-tokens" not in argv, argv)

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
