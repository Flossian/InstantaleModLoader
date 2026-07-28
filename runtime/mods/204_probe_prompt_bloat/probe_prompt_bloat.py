# -*- coding: utf-8 -*-
"""InstantaleLLMProxy が回避している3つのプロンプト肥大化挙動を実測する。

InstantaleLLMProxy/TECH.md は3つの暫定対策を記録しているが、いずれも HTTP
プロキシを通過する *描画済み* プロンプトを書き換えることで実現されている:

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

プロセス内部では、これらはそもそもテキストではない ― `quest_event_log` 引数、
`messages` リスト、サイドカー起動処理である。そこに手を入れれば文字列マッチも、
ロケールや言い回しで変わり得る区切り文字も不要になる。

この mod は計測しかしない。数値を TECH.md のものと突き合わせてから修正を書く
ためのものである。何も変更せず、何も握り潰さない。
"""

import datetime
import hashlib
import sys

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


def apply(ctx):
    log_path = ctx.out_path("prompt_bloat.log")

    def write(text: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("bloat probe: write failed")

    write("=" * 78)
    write("prompt bloat probe start (pid {})".format(__import__("os").getpid()))

    # ------------------------------------------------------------ EVENTLOG
    def describe_event_log(fn_name, args, kwargs):
        """quest_event_log の実体（型・文字数・ターン数）を記録する。

        リストなのか文字列なのかは、修正方法を左右する決定的な違いである
        （文字列なら区切り文字での分割が避けられない）。
        """
        # 位置引数・キーワード引数のどちらで来るか読めないので両方見る。
        value = kwargs.get("quest_event_log")
        if value is None and len(args) > EVENT_LOG_ARG:
            value = args[EVENT_LOG_ARG]
        if value is None:
            write("{}: quest_event_log not found in call".format(fn_name))
            return
        if isinstance(value, str):
            # 実測ではこちら。区切りの出現回数がそのままターン数になる。
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
            @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False)
            def probe(orig, *args, **kwargs):
                try:
                    describe_event_log(name, args, kwargs)
                except Exception:
                    pass      # 計測の失敗で本体を止めない
                return orig(*args, **kwargs)
            return probe
        # 存在確認は既定値付き getattr で行う（TECH.md §8 の hasattr 禁止）。
        if getattr(sys.modules.get("scripts.llm.llm_manager", object()),
                   fn_name, None) is not None:
            make(fn_name)
            installed += 1
    ctx.log("eventlog probes: {}/{}".format(installed, len(EVENT_TARGETS)))

    # --------------------------------------------------------------- DEDUP
    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template",
              required=False)
    def apply_chat_template(orig, self, model, messages, timeout=None, *args, **kwargs):
        try:
            # 各メッセージを (role, 文字数, 指紋) に潰す。この3つ組が一致すれば
            # 内容がバイト単位で同一ということ。
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

            # 隣接かつ同一かつ閾値以上 ― プロキシの DEDUP 条件と同じ判定。
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
        return orig(self, model, messages, timeout, *args, **kwargs)

    # ------------------------------------------------------------ SIDECAR
    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppSidecar.__init__", required=False)
    def sidecar_init(orig, self, *args, **kwargs):
        # 起動のたびに1行残す。多重起動しているかどうかは行数で分かる。
        write("LlamaCppSidecar.__init__ args={} kwargs={}".format(
            [repr(a)[:80] for a in args], {k: repr(v)[:80] for k, v in kwargs.items()}))
        return orig(self, *args, **kwargs)

    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppSidecar.start", required=False)
    def sidecar_start(orig, self, on_cpu=None, additional_env=None,
                      additional_params=None, *args, **kwargs):
        write("LlamaCppSidecar.start on_cpu={!r} additional_params={!r} env_keys={}".format(
            on_cpu, additional_params,
            # env は値に API キー等が入り得るのでキー名だけ記録する。
            sorted(additional_env) if isinstance(additional_env, dict) else additional_env))
        # 後日 --parallel 1 を注入する際、既存の指定と衝突しないかの下調べ。
        has_parallel = "--parallel" in str(additional_params or "")
        write("   --parallel present in params: {}".format(has_parallel))
        try:
            return orig(self, on_cpu, additional_env, additional_params, *args, **kwargs)
        except Exception as exc:
            # 起動失敗は crash_log.txt で 35 件を占める症状。記録して再送出する。
            write("!! LlamaCppSidecar.start raised {}: {}".format(type(exc).__name__, exc))
            raise

    ctx.log("prompt bloat log: {}".format(log_path))
