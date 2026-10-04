# -*- coding: utf-8 -*-
r"""LLM リクエストの重なり（多重送信）を実プレイで数える。

`127_llm_response_speed` は llama-server を
`--parallel 1`（専用1スロット）で起こす。
同時に2本来ると2本目はキューで待つ。
この「同時に2本」が実プレイでどれだけ起こるかが、
待ちの実害を決める（VERIFICATION_LOG.md §2.48）。

外から `/slots` をポーリングする測り方は、分解能より短い間隔を見分けられず、
「先行の完了直後に次が来た連鎖」と「本当に同時に居た」を区別できない。
プロセスの中で送信を包めば、この境界は正確になる。

## どこに仕掛けるか

ローカル（llama.cpp）で本文が通る3点（`instantale_modloader/llm.py` の定数と同じ）:

    llama_cpp_runtime_completion:LlamaCppClient.chat
    llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template
    llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry

どれが通るかはビルドと経路で変わり、1リクエストの中で入れ子にも通るので、
スレッド印で外側の1回だけを数える（`llm.py` の「1回の推論で1回だけ」と同じ手）。
`llm_manager:send_request*` を包まないのも同じ理由。
ローカル実行では内部で別スレッドへ降りるため印が届かず、二重に数えてしまう。
したがって **この probe が数えるのはローカル実行だけ**（クラウド経路は対象外）。

## ログの読み方（`out\llm_overlap.log`）

    START <関数> in_flight=<本数>   リクエスト開始。本数は自分を含む
    END <関数> <秒>                 終了と所要時間（キュー待ちを含む壁時計）
    OVERLAP in_flight=<本数>        開始時点で先行が走っていた＝真の多重送信

OVERLAP が1行も無ければ、その区間のリクエストは完全に逐次だった。
ゲームを起動するたびに勝手に録れるので、合間合間に遊ぶスタイルでも母数が貯まる。
集計は行数を数えるだけ（END の行数＝リクエスト数、OVERLAP の行数＝重なり）。

## 応答の中身（版3）

依頼の生成（掲示板の「クエストを探す」）で、同じ頼みが 100〜120 秒かかって失敗し、
ゲームが送り直す回が多い（成功は 20〜30 秒。このログの START / END を 8/22〜10/4 で突き合わせた。
VERIFICATION.md の版の表の 216 2→3）。
失敗した回の応答は output_data に残らないので、`chat` の戻り値をここで写す。

    RESULT chat <秒> sys=<見出し> msgs=<件数>/<字数> format=<型> options=<…> timeout=<…>
           -> <型> chars=<応答の字数> done=<止まった理由> tokens=<出力トークン数>
    SLOW   chat <秒> tail=<応答の末尾>              `SLOW_SECONDS` 秒を超えた回だけ
    RAISE  chat <秒> <例外の型>: <文>                 `chat` が投げた回（時間切れなど）

- 見出しは先頭の system の頭 `HEAD_CHARS` 字。どの頼みか（依頼の生成か）を見分ける
- 応答は辞書（Ollama 形の `message.content` / OpenAI 形の `choices[0]`）を見る。
  流し読み（ジェネレータ）は**読まない**（読むとゲームの分を消費する）。型だけ書く
- 引数も戻り値も触らない。写すのは字数と末尾だけ
"""

import threading
import time

#: 応答の末尾を写す回の下限（秒）。成功は 20〜60 秒、失敗は 100 秒以上だった。
SLOW_SECONDS = 60.0
#: 末尾として写す字数。ループしているならここに繰り返しが見える。
TAIL_CHARS = 400
#: 頼みの見出しとして写す字数。
HEAD_CHARS = 40

#: ローカルで本文が通る3点。
#: `instantale_modloader/llm.py` の定数と揃えてある。
TARGETS = (
    "llama_cpp_runtime_completion:LlamaCppClient.chat",
    "llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template",
    "llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry",
)

LOG_BASENAME = "llm_overlap.log"


def _arg(args, kwargs, name, index):
    """`chat(self, model, messages, format, options, stream, timeout, …)` の引数を読む。"""
    if name in kwargs:
        return kwargs[name]
    return args[index] if len(args) > index else None


def _one_line(text, limit):
    text = str(text).replace("\r", " ").replace("\n", "⏎")
    return text if len(text) <= limit else text[:limit] + "…"


def request_summary(args, kwargs):
    """頼みの形（見出し・件数・字数・format・options・timeout）。"""
    messages = _arg(args, kwargs, "messages", 2)
    fmt = _arg(args, kwargs, "format", 3)
    options = _arg(args, kwargs, "options", 4)
    timeout = _arg(args, kwargs, "timeout", 6)
    head, count, chars = "", 0, 0
    if isinstance(messages, list):
        count = len(messages)
        for turn in messages:
            content = turn.get("content") if isinstance(turn, dict) else None
            if isinstance(content, str):
                chars += len(content)
                if not head and turn.get("role") == "system":
                    head = content.strip()[:HEAD_CHARS]
    if isinstance(fmt, dict):
        fmt_text = "dict({} keys)".format(len(fmt))
    else:
        fmt_text = type(fmt).__name__ if fmt is not None else "None"
    if isinstance(options, dict):
        options_text = "{" + ", ".join("{}={}".format(k, options[k]) for k in sorted(options)
                                       if not isinstance(options[k], (dict, list))) + "}"
    else:
        options_text = repr(options)
    return "sys={} msgs={}/{} format={} options={} timeout={}".format(
        _one_line(head, HEAD_CHARS), count, chars, fmt_text, _one_line(options_text, 200), timeout)


def response_parts(result):
    """`(型, 本文, 止まった理由, 出力トークン数)`。読めない形は本文 None。流し読みは読まない。"""
    kind = type(result).__name__
    if isinstance(result, str):
        return kind, result, None, None
    if not isinstance(result, dict):
        return kind, None, None, None
    text = None
    message = result.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        text = message["content"]
    elif isinstance(result.get("content"), str):
        text = result["content"]
    done = result.get("done_reason") or result.get("finish_reason") or result.get("stop_type")
    tokens = result.get("eval_count") or result.get("tokens_predicted")
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        first = choices[0]
        inner = first.get("message")
        if text is None and isinstance(inner, dict) and isinstance(inner.get("content"), str):
            text = inner["content"]
        done = done or first.get("finish_reason")
    usage = result.get("usage")
    if tokens is None and isinstance(usage, dict):
        tokens = usage.get("completion_tokens")
    timings = result.get("timings")
    if tokens is None and isinstance(timings, dict):
        tokens = timings.get("predicted_n")
    if text is None or tokens is None:
        # どこに数が入っているか分からない形。鍵を書いておく（実機ではトークン数が None だった）。
        kind += "{" + ",".join(sorted(str(k) for k in result)[:12]) + "}"
    return kind, text, done, tokens


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
                result = orig(*args, **kwargs)
            except BaseException as exc:
                if short == "chat":
                    try:
                        write("RAISE chat {:.2f}s {} | {}: {}".format(
                            time.time() - t0, request_summary(args, kwargs),
                            type(exc).__name__, _one_line(exc, 300)))
                    except Exception:
                        pass
                raise
            finally:
                local.inside = False
                try:
                    with lock:
                        state["in_flight"] -= 1
                    write("END {} {:.2f}s".format(short, time.time() - t0))
                except Exception:
                    pass
            if short == "chat":
                try:
                    seconds = time.time() - t0
                    kind, text, done, tokens = response_parts(result)
                    write("RESULT chat {:.2f}s {} -> {} chars={} done={} tokens={}".format(
                        seconds, request_summary(args, kwargs), kind,
                        len(text) if text is not None else None, done, tokens))
                    if seconds >= SLOW_SECONDS and text:
                        write("SLOW chat {:.2f}s tail={}".format(
                            seconds, _one_line(text[-TAIL_CHARS:], TAIL_CHARS + 10)))
                except Exception:
                    pass
            return result

        return probe

    for target in TARGETS:
        make(target)
    ctx.log("llm overlap probe: 3 target(s) armed or deferred "
            "(log: {})".format(LOG_BASENAME))
