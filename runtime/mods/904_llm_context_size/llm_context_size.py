# -*- coding: utf-8 -*-
"""ローカルLLMの窓を広げる。**サーバを起こす直前に起動引数を書き換える。**

ゲームは `llama-server` を必ず `--ctx-size 16384` で起こす。日本語でおよそ
26,000字ぶんで、普段の会話プロンプト（実測 6,600 トークン前後）には足りるが、
`life_log` が育った NPC との会話はこれを超える。超えた要求はエラーになり、
ゲームは『リクエストを再実行』を出す。押しても**同じプロンプトを送り直すだけ**
なので必ず同じ所で落ちる ― 詰まって見えるのはこのため（DOC.md §1）。

設定画面の「サーバーパラメータ」欄からは直せない。**ゲームは追加パラメータを
繋ぐ前に `--ctx-size` を取り除く**（実測・DOC.md §2）。`--parallel` も
`--cache-reuse` もサンプリングもそのまま渡るのに、この1つだけが消える。

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
（実測・DOC.md §3）:

```
--ctx-size 16384                → kv_unified=true。4スロットが 16384 の
                                  プールを共有する。窓は 16384（ゲームの既定）
--ctx-size 16384 --parallel 2   → kv_unified=false。スロットごとに専用の
                                  プールを取り、窓は 16384÷2 = 8192
```

明示すると統合が外れ、確保量がスロット数倍になる。同じ窓・同じスロット数でも
KV が変わるので、`SLOTS` は3通りの意味を持たせてある:

    SLOTS = 0   --ctx-size CTX_SIZE                    統合。窓=CTX_SIZE、4本で共有
    SLOTS = 1   --ctx-size CTX_SIZE --parallel 1       専用1本。KV が最小
    SLOTS >= 2  --ctx-size (CTX_SIZE*SLOTS) --parallel SLOTS   専用 SLOTS 本

既定は 32768 × 1 本。**スロットを増やすのはこのゲームでは損**で、実測では
llama-server 宛のリクエストの94%が逐次、同時に走ったのは最大2本だった
（DOC.md §7）。1本にすると KV が最小になり、その余りを窓に回せる。

適正値はマシンごとに違う。`tools\llm_ctx_probe.bat` が実測して出す。

## 割り切り

- **効くのは次にサーバが起きるときから。** 既に走っているサーバの引数は
  変えられない。注入がサーバ起動より後なら、ゲーム側で LLM を開き直す
  （設定画面でモデルを選び直す）か、注入してからゲームを起動し直す。
  `apply()` の時点で走っているサーバがあれば、その `--ctx-size` を読んで
  ログに出す ― 効いているかを目で確かめるため
- **窓を広げても `life_log` は増え続ける。** これは時間稼ぎで、根治は
  プロンプト側の刈り込み（DOC.md §5）
- **VRAM を見ること。** 溢れても Windows は失敗せず共有メモリへ退避するので、
  症状は「エラー」ではなく「異様に遅い」になる。増やしたあとは
  `nvidia-smi` の空きを1度見る
"""

import os

# 1リクエストが使える窓（トークン）。0 で書き換えを止め、観測だけにする。
# **`mod.json` の "default" と揃えること**（`tools/check_mods.py` が AST で
# 突き合わせる。TECH.md §3.8.3）。
CTX_SIZE = 32768

# 同時に持つスロット数。0 は `--parallel` を渡さない（統合 KV）。
# 1 以上はスロットごとに専用の KV を確保する（docstring の表を参照）。
# **`mod.json` の "default" と揃えること。**
SLOTS = 1

# 起動引数をログに出す回数。
LOG_LIMIT = 5

LOG_BASENAME = "llm_context.log"

#: サイドカーの起動が必ず通る1点。
TARGET = "sidecar_process:popen_sidecar"

#: argv がローカルLLMのものだと判定する目印。実行ファイル名で見る。
EXE_MARK = "llama-server"

CTX_FLAG = "--ctx-size"
PARALLEL_FLAG = "--parallel"


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


def drop_flag(argv, flag):
    """`flag` とその値を取り除く。戻り値は (新しい列, 取り除いた値)。

    統合 KV にするには `--parallel` が**無い**必要がある。設定画面の
    「サーバーパラメータ」欄から `--parallel 1` が渡ってくる実例があるので
    （実測・DOC.md §2）、足すだけでなく消せないと `SLOTS=0` が効かない。
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

    `--parallel` が None なら「渡さない」。統合 KV になり、窓は指定値がその
    ままスロットごとの窓になる（docstring の表を参照）。
    """
    slots = max(0, int(slots))
    if slots == 0:
        return ctx_size, None
    return ctx_size * slots, slots


def rewrite_argv(argv, ctx_size=None, slots=None):
    """llama-server の argv を書き換える。戻り値は (新しい列, 変更の説明のリスト)。

    **書き換えないときは受け取った列をそのまま返す**（同一オブジェクト）。
    呼び出し側はそれを見て「素通しした」と判断できる。
    """
    ctx_size = CTX_SIZE if ctx_size is None else ctx_size
    slots = SLOTS if slots is None else slots

    if not is_llama_server(argv):
        return argv, []
    # 0 以下は「観測だけ」。設定で切った状態を、フックを外さずに作れるようにする。
    if ctx_size <= 0:
        return argv, []

    total, parallel = plan_flags(ctx_size, slots)
    changes = []
    new_argv, before = set_flag(argv, CTX_FLAG, total)
    changes.append((CTX_FLAG, before, str(total)))
    if parallel is None:
        # 統合 KV は `--parallel` が無いことが条件。既にあれば消す。
        new_argv, before = drop_flag(new_argv, PARALLEL_FLAG)
        if before is not None:
            changes.append((PARALLEL_FLAG, before, "(外した)"))
    else:
        new_argv, before = set_flag(new_argv, PARALLEL_FLAG, parallel)
        changes.append((PARALLEL_FLAG, before, str(parallel)))
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


def running_context_size():
    """既に走っている llama-server の `--ctx-size` を読む。読めなければ None。

    注入がサーバ起動より後だと、この MOD は次の起動まで効かない。それを
    「効いていない」と誤読しないための材料をログに出す。
    """
    try:
        import psutil
    except Exception:
        return None
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            info = proc.info or {}
            name = (info.get("name") or "").lower()
            if EXE_MARK not in name:
                continue
            cmdline = info.get("cmdline") or []
            for i, item in enumerate(cmdline):
                if str(item) == CTX_FLAG and i + 1 < len(cmdline):
                    return str(cmdline[i + 1])
            return "?"
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
        """既に走っているサーバの窓をログに出す（プロセスにつき1回）。"""
        current = running_context_size()
        if current is None:
            return
        want = str(plan_flags(CTX_SIZE, SLOTS)[0])
        if current == want:
            ctx.log("llm context size: 稼働中のサーバは既に {}={}".format(
                CTX_FLAG, current))
        else:
            ctx.log("llm context size: 稼働中のサーバは {}={}（狙いは {}）。"
                    "次にサーバが起きるときから効く".format(CTX_FLAG, current, want),
                    level="WARN")

    ctx.on_ready(report)

    if CTX_SIZE <= 0:
        ctx.log("llm context size: 観測のみ（CTX_SIZE=0）")
    else:
        total, parallel = plan_flags(CTX_SIZE, SLOTS)
        ctx.log("llm context size: installed (window {} x {} -> {} {}{})".format(
            CTX_SIZE,
            "{} slot(s)".format(parallel) if parallel else "shared 4 slot(s)",
            CTX_FLAG, total,
            " {} {}".format(PARALLEL_FLAG, parallel) if parallel else ""))
