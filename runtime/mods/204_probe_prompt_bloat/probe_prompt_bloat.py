# -*- coding: utf-8 -*-
"""InstantaleLLMProxy が回避している3つのプロンプト肥大化挙動を実測する。

InstantaleLLMProxy/TECH.md は3つの暫定対策を記録しているが、
いずれも HTTP プロキシを通過する
*描画済み* プロンプトを書き換えることで実現されている:

  EVENTLOG  【今回のイベント内ログ】 ブロックが、今回のではなくクエスト全体の
            フィールドイベントを蓄積する。実測 29 ターン / 8,300 文字超、
            プロンプト全体の 60% 以上。プロキシは描画済みテキストを
            〈プレイヤーの入力〉 で分割して3ターンだけ残す。
  DEDUP     8,374 文字のスキーマ system ブロックがクエスト再生成のたびに
            再追加される（12,161 -> 20,535 -> 28,909 -> ... -> 37,283 文字）。
            ローカル経路（/apply-template -> /completion）でのみ発生。プロキシは
            隣接する同一の 1000 文字以上のブロックを畳む。
  SINGLETON ゲームは1アクションにつき複数の llama-server ラッパを起動し、古い
            ものを確実には止めないため VRAM を食い潰す。プロキシは所有者を1つ
            選び --parallel 1 を強制する（リクエストを直列化し、分割スロット
            ではなく毎回 16384 の全コンテキストを使わせる）。

プロセス内部では、これらはそもそもテキストではない。
`quest_event_log` 引数、`messages` リスト、サイドカー起動処理である。
そこに手を入れれば文字列マッチも、
ロケールや言い回しで変わり得る区切り文字も不要になる。

この mod は計測しかしない。
数値を TECH.md のものと突き合わせてから修正を書くためのものである。
何も変更せず、何も握り潰さない。

版3: 3つの挙動は本体で直った（VERIFICATION_LOG.md §2.3）が、
VERIFICATION.md §3.36 #4（`311_` と `317_` を併せたプロンプト長）をこの記録で測るので残す。
包みをすべて `safe=True` にし、orig の前の記録（サイドカーの `repr` など）を try に入れ、
引数は受け取った形のまま `orig` へ渡すようにした。
見出しの2行は注入のたびに出ていた（1か月で約 2,000 行）ので1プロセス1回にした。

版4: 窓（`--ctx-size`）にどこまで近づいたかをトークン数で残す。
`_apply_chat_template` の字数だけでは窓との距離が分からず、
窓を超えた頼み（llama-server の HTTP 400 `exceed_context_size_error`）もどこにも残らなかった。
`LlamaCppClient.chat` の戻り値から頼みと出力のトークン数を読み、1回につき1行書く（DOC.md）。

    TOKENS prompt=<頼み> cached=<使い回し> predicted=<出力> total=<頼み+出力> done=<止まった理由> truncated=<窓に合わせて切ったか> schema=<型の名前>
    RAISE  chat <例外の型>: <文>                  `chat` が投げた回（窓あふれなら EXCEED を付ける）
    KEYS   chat result keys=[…]                  戻り値の鍵（1プロセス1回。数の在りかを確かめる用）
"""

import hashlib
import os
import sys

from instantale_modloader import frames

# 見出しを「このプロセスで書いた」印。モジュール変数は注入し直すと消えるので `sys` に置く。
HEADER_MARK = "_instantale_probe_prompt_bloat_header"
# 戻り値の鍵を「このプロセスで書いた」印。
KEYS_MARK = "_instantale_probe_prompt_bloat_keys"

CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"
# 頼みのトークン数の在りか。上から順に見る。
# 使い回し（cache_prompt）の分を含む総数でないと窓との距離にならない。
# llama-server の `timings.prompt_n` は新しく処理した分だけなので、`cache_n` を足す。
PROMPT_KEYS = ("prompt_eval_count", "tokens_evaluated")
PREDICTED_KEYS = ("eval_count", "tokens_predicted", "completion_tokens")

TURN_SEPARATOR = "〈プレイヤーの入力〉"
EVENT_LOG_ARG = 2          # referee 2関数いずれも quest_event_log は第3引数
DEDUP_MIN_BLOCK = 1000     # TECH.md と同じ閾値

EVENT_TARGETS = (
    "quest_referee_event_evaluate_new",
    "quest_referee_event_resolve",
    "quest_referee_event_rewrite",
)


def _digest(text: str) -> str:
    """内容の同一性を短い指紋で比較するためのハッシュ（全文はログに出さない）。"""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:10]


def _first_int(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def token_counts(result):
    """`chat` の戻り値から (頼み, 使い回し, 出力, 止まった理由) を読む。読めない数は None。

    頼みは使い回しの分を含む総数。
    """
    if not isinstance(result, dict):
        return None, None, None, None
    # `tokens_evaluated` も `timings.prompt_n` も新しく処理した分だけ（実機で cached=5767 のとき 258）。
    fresh = _first_int(result, PROMPT_KEYS)
    predicted = _first_int(result, PREDICTED_KEYS)
    cached = None
    timings = result.get("timings")
    if isinstance(timings, dict):
        cached = _first_int(timings, ("cache_n",))
        if fresh is None:
            fresh = _first_int(timings, ("prompt_n",))
        if predicted is None:
            predicted = _first_int(timings, ("predicted_n",))
    usage = result.get("usage")
    if isinstance(usage, dict):
        if fresh is None:
            fresh = _first_int(usage, ("prompt_tokens",))
        if predicted is None:
            predicted = _first_int(usage, ("completion_tokens",))
    prompt = fresh + (cached or 0) if fresh is not None else None
    done = result.get("done_reason") or result.get("stop_type") or result.get("finish_reason")
    return prompt, cached, predicted, done


def schema_label(fmt):
    """どの頼みかを型の名前で表す（`title` と `$defs` の先頭3つ）。"""
    if not isinstance(fmt, dict):
        return type(fmt).__name__ if fmt is not None else "-"
    defs = fmt.get("$defs")
    names = list(defs)[:3] if isinstance(defs, dict) else []
    return "{}[{}]".format(fmt.get("title") or "?", ",".join(str(n) for n in names))


def apply(ctx):
    log_path = ctx.out_path("prompt_bloat.log")

    write = ctx.logger("prompt_bloat.log")

    if not getattr(sys, HEADER_MARK, False):
        setattr(sys, HEADER_MARK, True)
        write("=" * 78)
        write("prompt bloat probe start (pid {})".format(os.getpid()))

    # ------------------------------------------------------------ EVENTLOG
    def describe_event_log(fn_name, args, kwargs):
        """quest_event_log の実体（型・文字数・ターン数）を記録する。

        リストなのか文字列なのかは、
        修正方法を左右する決定的な違いである（文字列なら区切り文字での分割が避けられない）。
        """
        # 位置引数・キーワード引数のどちらで来るか読めないので両方見る。
        value = kwargs.get("quest_event_log")
        if value is None and len(args) > EVENT_LOG_ARG:
            value = args[EVENT_LOG_ARG]
        if value is None:
            write("{}: quest_event_log not found in call".format(fn_name))
            return
        if isinstance(value, str):
            # 実測ではこちら。
            # 区切りの出現回数がそのままターン数になる。
            turns = value.count(TURN_SEPARATOR)
            write("{}: quest_event_log str chars={} turns={} (separator {!r})".format(
                fn_name, len(value), turns, TURN_SEPARATOR))
        elif isinstance(value, (list, tuple)):
            total = 0
            for item in value:
                try:
                    total += len(item if isinstance(item, str) else str(item))
                except Exception:
                    pass
            write("{}: quest_event_log {} items={} approx_chars={}".format(
                fn_name, type(value).__name__, len(value), total))
            # 形を掴むために先頭3件だけ抜粋する。
            for index, item in enumerate(value[:3]):
                preview = (item if isinstance(item, str) else repr(item))[:160]
                write("    [{}] {}".format(index, preview.replace("\n", "\\n")))
        else:
            write("{}: quest_event_log type={} repr={}".format(
                fn_name, type(value).__name__, repr(value)[:200]))

    installed = 0
    for fn_name in EVENT_TARGETS:
        # ループ変数を閉じ込める工場関数。
        def make(name):
            @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False, safe=True)
            def probe(orig, *args, **kwargs):
                try:
                    describe_event_log(name, args, kwargs)
                except Exception:
                    pass      # 計測の失敗で本体を止めない
                return orig(*args, **kwargs)
            return probe
        # 存在確認をしてから登録しない。
        # `scripts.llm.llm_manager` は最初の LLM リクエストまで
        # import されないので、
        # ここで確認すると常に「無い」になり、**フックが1本も登録されないまま保留の見張りも立たない**。
        # `required=False` に任せれば、モジュールが未 import なら保留し、
        # 在るのに属性が無ければ黙って降りる（TECH.md §3.4 の表）。
        make(fn_name)
        installed += 1
    ctx.log("eventlog probes: {} target(s) armed or deferred".format(installed))

    # --------------------------------------------------------------- DEDUP
    # 版3: 引数は受け取った形のまま `orig` へ渡す。
    # 版2は `timeout` を位置に直して渡していた（キーワードで来た呼び出しでは形が変わる）。
    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template",
              required=False, safe=True)
    def apply_chat_template(orig, self, *args, **kwargs):
        try:
            messages = frames.arg(args, kwargs, "messages", 1)
            # 各メッセージを (role, 文字数, 指紋) に潰す。
            # この3つ組が一致すれば内容がバイト単位で同一ということ。
            blocks = []
            total = 0
            for message in messages or ():
                try:
                    role = message.get("role", "?")
                    content = message.get("content", "")
                except Exception:
                    role, content = "?", str(message)
                content = content if isinstance(content, str) else str(content)
                total += len(content)
                blocks.append((role, len(content), _digest(content)))

            # 隣接かつ同一かつ閾値以上。
            # プロキシの DEDUP 条件と同じ判定。
            repeats = [i for i in range(1, len(blocks))
                       if blocks[i] == blocks[i - 1] and blocks[i][1] >= DEDUP_MIN_BLOCK]
            write("_apply_chat_template: {} message(s), total_chars={} layout={}".format(
                len(blocks), total,
                ", ".join("{}:{}:{}".format(r, n, d) for r, n, d in blocks[:12])))
            if repeats:
                write("   >>> {} adjacent duplicate block(s) >={} chars at index {} "
                      "-- this is the DEDUP condition".format(
                          len(repeats), DEDUP_MIN_BLOCK, repeats))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------- TOKENS
    # 版4: 窓にどこまで近づいたか。
    # 引数も戻り値も触らない。流し読み（ジェネレータ）の戻り値は読まない（読むとゲームの分を消費する）。
    @ctx.wrap(CHAT_TARGET, required=False, safe=True)
    def chat_tokens(orig, self, *args, **kwargs):
        try:
            label = schema_label(frames.arg(args, kwargs, "format", 2))
        except Exception:
            label = "?"
        try:
            result = orig(self, *args, **kwargs)
        except BaseException as exc:
            try:
                text = str(exc).replace("\n", " ")[:300]
                exceed = "exceed" in text.lower() and "context" in text.lower()
                write("RAISE chat {}{}: {} schema={}".format(
                    "EXCEED " if exceed else "", type(exc).__name__, text, label))
            except Exception:
                pass
            raise
        try:
            if isinstance(result, dict):
                if not getattr(sys, KEYS_MARK, False):
                    setattr(sys, KEYS_MARK, True)
                    timings = result.get("timings")
                    write("KEYS chat result keys={} timings={}".format(
                        sorted(str(k) for k in result)[:30],
                        sorted(str(k) for k in timings)[:30] if isinstance(timings, dict) else None))
                prompt, cached, predicted, done = token_counts(result)
                total = (prompt + predicted) if prompt is not None and predicted is not None else None
                write("TOKENS prompt={} cached={} predicted={} total={} done={} truncated={} schema={}".format(
                    prompt, cached, predicted, total, done, result.get("truncated"), label))
                if result.get("error"):
                    write("ERROR chat {} schema={}".format(str(result.get("error"))[:300], label))
        except Exception:
            pass
        return result

    # ------------------------------------------------------------ SIDECAR
    # 版3: 記録は try に入れた。
    # 版2は `repr` などが投げると、その例外でサイドカーの起動ごと止まる形だった。
    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppSidecar.__init__",
              required=False, safe=True)
    def sidecar_init(orig, self, *args, **kwargs):
        # 起動のたびに1行残す。
        # 多重起動しているかどうかは行数で分かる。
        try:
            write("LlamaCppSidecar.__init__ args={} kwargs={}".format(
                [repr(a)[:80] for a in args], {k: repr(v)[:80] for k, v in kwargs.items()}))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppSidecar.start",
              required=False, safe=True)
    def sidecar_start(orig, self, *args, **kwargs):
        try:
            on_cpu = frames.arg(args, kwargs, "on_cpu", 0)
            additional_env = frames.arg(args, kwargs, "additional_env", 1)
            additional_params = frames.arg(args, kwargs, "additional_params", 2)
            write("LlamaCppSidecar.start on_cpu={!r} additional_params={!r} env_keys={}".format(
                on_cpu, additional_params,
                # env は値に API キー等が入り得るのでキー名だけ記録する。
                # dict でなければ型名だけにする（版3。版2は値ごと書いていた）。
                sorted(additional_env) if isinstance(additional_env, dict)
                else type(additional_env).__name__))
            # 後日 --parallel 1 を注入する際、既存の指定と衝突しないかの下調べ。
            has_parallel = "--parallel" in str(additional_params or "")
            write("   --parallel present in params: {}".format(has_parallel))
        except Exception:
            pass
        try:
            return orig(self, *args, **kwargs)
        except Exception as exc:
            # 起動失敗は crash_log.txt で 35 件を占める症状。
            # 記録して再送出する。
            try:
                write("!! LlamaCppSidecar.start raised {}: {}".format(
                    type(exc).__name__, exc))
            except Exception:
                pass
            raise

    ctx.log("prompt bloat log: {}".format(log_path))
