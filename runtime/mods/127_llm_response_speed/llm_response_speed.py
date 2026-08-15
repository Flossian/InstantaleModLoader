# -*- coding: utf-8 -*-
r"""ローカルLLMを速くする。**サーバを起こす直前に起動引数を書き換える。**

ゲームは `llama-server` をスロット4本の**統合 KV** で起こす（`--parallel` を
渡さないと llama.cpp が `auto` を選ぶ）。だがこのゲームは並列でほとんど走らない。
実測ではリクエストの94%が逐次で、同時に走ったのは最大2本だった（VERIFICATION_LOG.md §2.48）。
4本ぶんの KV を抱えたまま1本しか使っていない。

`--parallel 1` を渡すと統合が外れて専用の1本になり、KV が減って速くなる。
同じ窓 16384 での実測（VERIFICATION_LOG.md §2.48）:

    統合4スロット（素のゲーム）   KV 1220 MiB   生成 94 / 96 t/s
    --parallel 1                  KV  620 MiB   生成 133 / 127 t/s

**この MOD の本体はこれ。** `--ctx-size` には既定では触らない。窓を広げるのは
任意の追加機能で、KV が増えるぶん VRAM の余裕が要る（GAME.md §2.12.2 と VERIFICATION_LOG.md §2.48）。

## 1本にすると再計算が増える。ので同時に打ち消す

SWA 層は古いトークンを捨てるため、途中から再開するにはその時点のスナップショット
（コンテキストチェックポイント）が要る。スロットを1本に絞ると、種類の違う
プロンプトが同じスロットを奪い合い、**復元に失敗してフル再計算に落ちる回が出る**
（実測で4件に1件、プロンプト処理が +20%）。

`--checkpoint-every-n-tokens 256` を足すとスナップショットが密になり、この失敗が
消える。**VRAM は1バイトも増えない**（ホスト側に載る。実測で 9054 MiB のまま）。
そのため既定で必ず付ける。設定欄で明示されている場合だけ、そちらを尊重する。

設定画面の「サーバーパラメータ」欄からでは窓を変えられない。**ゲームは追加
パラメータを繋ぐ前に `--ctx-size` を取り除く**（実測・GAME.md §2.12.1）。`--parallel`
も `--cache-reuse` もサンプリングもそのまま渡るのに、この1つだけが消える。

```
config.json の欄  --parallel 2 --ctx-size 32768 --cache-reuse 256
実際のコマンドライン
    ... --ctx-size 16384 ... --parallel 2 --cache-reuse 256
                  ^^^^^ ゲームの値が残り、こちらの指定は消えている
```

そこで欄ではなく**プロセスを起こす関数**に仕掛ける。剥がす処理より後ろに
立てば、剥がされようがない。

## どこに仕掛けるか

    sidecar_process:popen_sidecar(*args, **kwargs) -> subprocess.Popen

`subprocess.Popen` の薄い包みで、サイドカーはすべてここを通る。`LlamaCppSidecar`
の `start` ではなくこちらを選ぶ理由は3つ:

- `start` が引数をどう組み立てるかはコンパイル済みで読めない。ここには
  **実際に渡る argv がそのまま**来る
- `llama_cpp_runtime_completion` はローカル実行のときしか import されないが、
  `sidecar_process` は常に載っている（クラウド実行のリコンにも出る）
- 画像生成の背景除去も同じ関数を通る（`request_remove_background:popen_sidecar`
  は from-import の別名）。**だから argv の中身で llama-server を選り分ける**。
  別名はローダの alias_scan が張り替えるので、どちらの経路から来ても掴める

## 窓とスロットは必ず一緒に決める

`--parallel` を書くかどうかで、llama-server の KV の持ち方そのものが変わる
（実測・GAME.md §2.12.2）:

```
--ctx-size 16384                → kv_unified=true。4スロットが 16384 の
                                  プールを共有する。窓は 16384（ゲームの既定）
--ctx-size 16384 --parallel 2   → kv_unified=false。スロットごとに専用の
                                  プールを取り、窓は 16384÷2 = 8192
```

明示すると統合が外れ、SWA 側の確保量が減る（実測で3分の1）。同じ窓でも
KV が変わるので、設定は2つに分けてある:

    SLOTS = 0        --parallel を外す（統合に戻す）
    SLOTS = 1        --parallel 1。専用1本で KV 最小 ← 既定
    SLOTS >= 2       --parallel SLOTS。窓 × スロット数ぶんの KV を取る

    CTX_SIZE = 0     --ctx-size に触らない（ゲームの値のまま） ← 既定
    CTX_SIZE > 0     その値を窓にする。SLOTS>=2 なら合計＝窓×スロット数

**既定（CTX_SIZE=0 / SLOTS=1）は KV がどのモデルでも増えない。** 窓を据え置いた
まま持ち方だけを変えるので、素のゲームに対して確保量は同じか減る。速度だけが
変わるので、環境を選ばない。

`CTX_SIZE` を上げると KV は窓に比例して増える。**単価はモデルの構造で決まる**
（SWA を持つ gemma-4-26B は 20 KiB/token、持たない Qwen3.5-9B は 32 KiB/token）
ので、安全な値はマシンとモデルの組でしか決まらない。`tools\llm_ctx_probe.bat`
が実測して出す。溢れてもエラーにはならず「異様に遅い」になるだけなので、
勘で上げないこと。

## 割り切り

- **効くのは次にサーバが起きるときから。** 既に走っているサーバの引数は
  変えられない。注入がサーバ起動より後なら、ゲーム側で LLM を開き直す
  （設定画面でモデルを選び直す）か、注入してからゲームを起動し直す。
  `apply()` の時点で走っているサーバがあれば、その持ち方を読んでログに出す
  （効いているかを目で確かめるため）
- **窓の拡張は詰まりの根治ではない。** `life_log` が膨らんでプロンプトが窓を
  超える問題は、プロンプト側で外科的に直す（MODS.md）。ここで `CTX_SIZE` を
  上げるのは時間稼ぎでしかないので、既定では触らない
- **`CTX_SIZE` を上げたら VRAM を見ること。** 溢れても Windows は失敗せず
  共有メモリへ退避するので、症状は「エラー」ではなく「異様に遅い」になる。
  `tools\llm_ctx_probe.bat` が安全な上限を実測で出す
"""

import os

# 1リクエストが使える窓（トークン）。0 は `--ctx-size` に触らない。
# **`mod.json` の "default" と揃えること**（`tools/check_mods.py` が AST で
# 突き合わせる。TECH.md §3.8.3）。
CTX_SIZE = 0

# 同時に持つスロット数。0 は `--parallel` を外す（統合 KV に戻す）。
# 1 以上はスロットごとに専用の KV を確保する（docstring の表を参照）。
# **`mod.json` の "default" と揃えること。**
SLOTS = 1

# 何も書き換えず、実際の起動引数を記録するだけにする。
# **`mod.json` の "default" と揃えること。**
OBSERVE_ONLY = False

# 起動引数をログに出す回数。
LOG_LIMIT = 5

LOG_BASENAME = "llm_speed.log"

#: サイドカーの起動が必ず通る1点。
TARGET = "sidecar_process:popen_sidecar"

#: argv がローカルLLMのものだと判定する目印。実行ファイル名で見る。
EXE_MARK = "llama-server"

CTX_FLAG = "--ctx-size"
PARALLEL_FLAG = "--parallel"

#: チェックポイントを取る間隔（トークン）。`--parallel 1` の副作用を打ち消す。
#: 詳しくは docstring の「1本にすると再計算が増える」。
CHECKPOINT_FLAG = "--checkpoint-every-n-tokens"
CHECKPOINT_EVERY = 256

#: ゲームが必ず渡してくる `--ctx-size`。`SLOTS>=2` で窓を保つ計算に使う。
GAME_CTX_SIZE = 16384


def is_llama_server(argv):
    """この argv が llama-server の起動か。**実行ファイル名で判定する。**

    背景除去のサイドカーも同じ関数を通るので、ここを緩めると無関係な
    プロセスに `--ctx-size` を足すことになる。
    """
    if not isinstance(argv, (list, tuple)):
        return False
    for item in argv:
        try:
            text = str(item).lower()
        except Exception:
            continue
        # 実行ファイルは argv[0] に来るはずだが、位置は決め打ちしない
        # （ゲームが python 経由で起こす形に変わっても拾えるように）。
        if EXE_MARK in os.path.basename(text) or EXE_MARK in text:
            return True
    return False


def set_flag(argv, flag, value):
    """`flag` の値を `value` にする。無ければ末尾に足す。戻り値は (新しい列, 前の値)。

    llama-server は同じ旗が2回出ると**後勝ち**なので末尾に足すだけでも効くが、
    それだとログを見たときに前後どちらが効いているのか読み取れない。既にある
    ものを書き換える形にして、コマンドラインが常に1つだけを持つようにする。
    """
    out = list(argv)
    text = str(value)
    for i, item in enumerate(out):
        if str(item) != flag:
            continue
        if i + 1 < len(out):
            before = str(out[i + 1])
            out[i + 1] = text
            return out, before
        # 旗だけあって値が無い（壊れた並び）。値を足して形を整える。
        out.append(text)
        return out, None
    out.extend([flag, text])
    return out, None


def has_flag(argv, flag):
    """`flag` が既に argv にあるか。設定欄からの指定を上書きしないための確認。"""
    return any(str(item) == flag for item in argv)


def drop_flag(argv, flag):
    """`flag` とその値を取り除く。戻り値は (新しい列, 取り除いた値)。

    統合 KV にするには `--parallel` が**無い**必要がある。設定画面の
    「サーバーパラメータ」欄から `--parallel 1` が渡ってくる実例があるので
    （実測・GAME.md §2.12.1）、足すだけでなく消せないと `SLOTS=0` が効かない。
    """
    out = []
    removed = None
    skip = False
    for i, item in enumerate(argv):
        if skip:
            skip = False
            continue
        if str(item) == flag:
            if i + 1 < len(argv):
                removed = str(argv[i + 1])
                skip = True
            continue
        out.append(item)
    return out, removed


def plan_flags(ctx_size, slots):
    """設定から実際に渡す値を決める。戻り値は (--ctx-size の値, --parallel の値)。

    どちらも None は「その旗に触らない」。`--ctx-size` の None はゲームの値を
    そのまま通す、`--parallel` の None は旗を外す（統合 KV に戻す）。
    """
    slots = max(0, int(slots))
    parallel = slots if slots >= 1 else None
    if ctx_size <= 0:
        # `--ctx-size` は合計なので、2本以上でそのまま渡すと窓が黙って
        # スロット数ぶん縮む（実測・VERIFICATION_LOG.md §2.48）。ゲームの値を
        # 引き伸ばして窓を保つ。
        if slots >= 2:
            return GAME_CTX_SIZE * slots, parallel
        return None, parallel
    # スロットごとに専用の窓を持たせるときだけ、合計へ引き伸ばす。
    return ctx_size * max(1, slots), parallel


def rewrite_argv(argv, ctx_size=None, slots=None):
    """llama-server の argv を書き換える。戻り値は (新しい列, 変更の説明のリスト)。

    **書き換えないときは受け取った列をそのまま返す**（同一オブジェクト）。
    呼び出し側はそれを見て「素通しした」と判断できる。
    """
    ctx_size = CTX_SIZE if ctx_size is None else ctx_size
    slots = SLOTS if slots is None else slots

    if not is_llama_server(argv):
        return argv, []
    # 設定で切った状態を、フックを外さずに作れるようにする。
    if OBSERVE_ONLY:
        return argv, []

    total, parallel = plan_flags(ctx_size, slots)
    new_argv = argv
    changes = []
    if total is not None:
        new_argv, before = set_flag(new_argv, CTX_FLAG, total)
        changes.append((CTX_FLAG, before, str(total)))
    if parallel is None:
        # 統合 KV は `--parallel` が無いことが条件。既にあれば消す。
        new_argv, before = drop_flag(new_argv, PARALLEL_FLAG)
        if before is not None:
            changes.append((PARALLEL_FLAG, before, "(外した)"))
    else:
        new_argv, before = set_flag(new_argv, PARALLEL_FLAG, parallel)
        changes.append((PARALLEL_FLAG, before, str(parallel)))
        # スロットを絞ると SWA のチェックポイント復元に失敗してフル再計算が
        # 混ざる。密に取らせると消える（VRAM は増えない。ホスト側に載る）。
        if CHECKPOINT_EVERY > 0 and not has_flag(argv, CHECKPOINT_FLAG):
            new_argv, before = set_flag(new_argv, CHECKPOINT_FLAG, CHECKPOINT_EVERY)
            changes.append((CHECKPOINT_FLAG, before, str(CHECKPOINT_EVERY)))
    if not changes:
        return argv, []
    return new_argv, changes


def describe(argv):
    """ログ用に argv を1行へ潰す。パスは長いので実行ファイル名だけにする。"""
    parts = []
    for item in argv:
        text = str(item)
        if os.sep in text or (os.altsep and os.altsep in text):
            text = os.path.basename(text)
        parts.append(text)
    return " ".join(parts)


def running_flags():
    """既に走っている llama-server の (ctx, parallel) を読む。読めなければ None。

    注入がサーバ起動より後だと、この MOD は次の起動まで効かない。それを
    「効いていない」と誤読しないための材料をログに出す。
    """
    try:
        import psutil
    except Exception:
        return None

    def value_of(cmdline, flag):
        for i, item in enumerate(cmdline):
            if str(item) == flag and i + 1 < len(cmdline):
                return str(cmdline[i + 1])
        return None

    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            info = proc.info or {}
            if EXE_MARK not in (info.get("name") or "").lower():
                continue
            cmdline = info.get("cmdline") or []
            return value_of(cmdline, CTX_FLAG), value_of(cmdline, PARALLEL_FLAG)
    except Exception:
        return None
    return None


def apply(ctx):
    state = {"seen": 0}
    write = ctx.logger(LOG_BASENAME)

    @ctx.wrap(TARGET, required=False, safe=True)
    def popen(orig, *args, **kwargs):
        # `popen_sidecar` は Popen の薄い包みなので、argv は第1位置引数か
        # キーワード `args` のどちらかで来る。受け取ったのと同じ渡し方で返す。
        from_kwargs = "args" in kwargs
        argv = kwargs.get("args") if from_kwargs else (args[0] if args else None)

        if not is_llama_server(argv):
            return orig(*args, **kwargs)

        state["seen"] += 1
        loud = state["seen"] <= LOG_LIMIT
        if loud:
            write("[LAUNCH] {}".format(describe(argv)))

        new_argv, changes = rewrite_argv(argv)
        if not changes:
            if loud:
                write("    観測のみ（CTX_SIZE={}）".format(CTX_SIZE))
            return orig(*args, **kwargs)

        if loud:
            for flag, before, after in changes:
                write("    {} {} -> {}".format(
                    flag, before if before is not None else "(無し)", after))
            write("[REWRITE] {}".format(describe(new_argv)))

        if from_kwargs:
            kwargs = dict(kwargs, args=new_argv)
        else:
            args = (new_argv,) + tuple(args[1:])
        return orig(*args, **kwargs)

    def report():
        """既に走っているサーバの持ち方をログに出す（プロセスにつき1回）。"""
        current = running_flags()
        if current is None:
            return
        ctx_now, par_now = current
        want_ctx, want_par = plan_flags(CTX_SIZE, SLOTS)
        ok = ((want_ctx is None or ctx_now == str(want_ctx))
              and (str(want_par) if want_par else None) == par_now)
        shown = "{}={} {}={}".format(CTX_FLAG, ctx_now, PARALLEL_FLAG,
                                     par_now if par_now else "(無し)")
        if ok:
            ctx.log("llm context size: 稼働中のサーバは既に " + shown)
        else:
            ctx.log("llm context size: 稼働中のサーバは {}。次にサーバが起きる"
                    "ときから効く".format(shown), level="WARN")

    ctx.on_ready(report)

    if OBSERVE_ONLY:
        ctx.log("llm context size: 観測のみ（OBSERVE_ONLY）")
    else:
        total, parallel = plan_flags(CTX_SIZE, SLOTS)
        plan = []
        if total is not None:
            plan.append("{} {}".format(CTX_FLAG, total))
        plan.append("{} {}".format(PARALLEL_FLAG, parallel) if parallel
                    else "{} を外す".format(PARALLEL_FLAG))
        ctx.log("llm context size: installed ({}{})".format(
            " ".join(plan),
            "" if CTX_SIZE > 0 else "。窓はゲームの値のまま"))
