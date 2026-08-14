# -*- coding: utf-8 -*-
r"""LLM リクエストの重なり（多重送信）を実プレイで数える。

`127_llm_response_speed` は llama-server を `--parallel 1`（専用1スロット）で
起こす。同時に2本来ると2本目はキューで待つ ― この「同時に2本」が実プレイで
どれだけ起こるかが、待ちの実害を決める（VERIFICATION_LOG.md §2.48）。

外から `/slots` をポーリングする測り方は、分解能より短い間隔を見分けられず、
「先行の完了直後に次が来た連鎖」と「本当に同時に居た」を区別できない。
プロセスの中で送信を包めば、この境界は正確になる。

## どこに仕掛けるか

ローカル（llama.cpp）で本文が通る3点（`instantale_modloader/llm.py` の定数と同じ）:

    llama_cpp_runtime_completion:LlamaCppClient.chat
    llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template
    llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry

どれが通るかはビルドと経路で変わり、1リクエストの中で入れ子にも通るので、
スレッド印で**外側の1回だけ**を数える（`llm.py` の「1回の推論で1回だけ」と
同じ手）。`llm_manager:send_request*` を包まないのも同じ理由 ― ローカル実行では
内部で別スレッドへ降りるため印が届かず、二重に数えてしまう。したがって
**この probe が数えるのはローカル実行だけ**（クラウド経路は対象外）。

## ログの読み方（`out\llm_overlap.log`）

    START <関数> in_flight=<本数>   リクエスト開始。本数は自分を含む
    END <関数> <秒>                 終了と所要時間（キュー待ちを含む壁時計）
    OVERLAP in_flight=<本数>        開始時点で先行が走っていた＝真の多重送信

OVERLAP が1行も無ければ、その区間のリクエストは完全に逐次だった。ゲームを
起動するたびに勝手に録れるので、合間合間に遊ぶスタイルでも母数が貯まる。
集計は行数を数えるだけ（END の行数＝リクエスト数、OVERLAP の行数＝重なり）。
"""

import threading
import time

#: ローカルで本文が通る3点。`instantale_modloader/llm.py` の定数と揃えてある。
TARGETS = (
    "llama_cpp_runtime_completion:LlamaCppClient.chat",
    "llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template",
    "llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry",
)

LOG_BASENAME = "llm_overlap.log"


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    lock = threading.Lock()
    state = {"in_flight": 0, "count": 0, "overlaps": 0, "peak": 0}
    local = threading.local()

    def make(target):
        short = target.rsplit(".", 1)[-1]

        @ctx.wrap(target, required=False)
        def probe(orig, *args, **kwargs):
            # 入れ子の内側（chat の中の _post 等）は数えない。
            if getattr(local, "inside", False):
                return orig(*args, **kwargs)
            local.inside = True
            t0 = time.time()
            try:
                with lock:
                    state["in_flight"] += 1
                    n = state["in_flight"]
                    state["count"] += 1
                    if n > state["peak"]:
                        state["peak"] = n
                    if n >= 2:
                        state["overlaps"] += 1
                write("START {} in_flight={}".format(short, n))
                if n >= 2:
                    write("OVERLAP in_flight={} ({})".format(n, short))
            except Exception:
                pass  # 計測の失敗で本体を止めない
            try:
                return orig(*args, **kwargs)
            finally:
                local.inside = False
                try:
                    with lock:
                        state["in_flight"] -= 1
                    write("END {} {:.2f}s".format(short, time.time() - t0))
                except Exception:
                    pass

        return probe

    for target in TARGETS:
        make(target)
    ctx.log("llm overlap probe: 3 target(s) armed or deferred "
            "(log: {})".format(LOG_BASENAME))
