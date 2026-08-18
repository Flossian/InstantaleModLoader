# -*- coding: utf-8 -*-
"""クエスト中に膨れ上がる【今回のイベント内ログ】を、直近3ターンぶんに刈り込む。

このブロックは名前に反して、
今回のイベントだけでなくクエスト全体のフィールドイベントを持ち続ける。
フィールドイベントは仕様上3ターン以内に決着するので、
それ以前の履歴は判定に影響しないまま溜まっていく一方になる。
外部プロキシ側の計測では 29 ターン / 8,300 文字を超え、
プロンプト全体の 6割以上を占めていた。

本ビルドでもクエストの序盤で既に溜まり始めている（VERIFICATION_LOG.md §2.3）。

注意点として、quest_event_log はターンのリストではなく1本の文字列である。
そのためこの修正でも、プロキシと同じく「〈プレイヤーの入力〉」で分割するしかなく、
区切り文字に依存する点は解消できていない。

それでも中で直す価値はある。
プロキシは組み上がったプロンプト全体を書き換えるので、
他の部分がたまたま同じ文字列を含んでいると巻き込む可能性がある。
こちらは該当する引数1つしか触らないので、その心配が無い。

最初の区切りより前の前置きは必ず残し、落とすのは丸ごと1ターン単位。
しかもターン数が KEEP_TURNS を超えているときだけ。
"""


SEPARATOR = "〈プレイヤーの入力〉"
KEEP_TURNS = 3            # フィールドイベントは仕様上3ターンで決着する
EVENT_LOG_INDEX = 2       # 対象3関数とも quest_event_log は3番目の引数
EVENT_LOG_KEYWORD = "quest_event_log"
AUDIT_FIRST_N = 3         # 最初の数回だけ、刈り込み前後をログに出す
LOG_BASENAME = "prompt_bloat.log"   # 102_ / 105_ と同じログに時系列で並べる

TARGETS = (
    "quest_referee_event_evaluate_new",
    "quest_referee_event_resolve",
    "quest_referee_event_rewrite",
)


def trim_event_log(text, keep=KEEP_TURNS):
    """前置き + 直近 keep ターンだけを残す。戻り値は (結果, 落としたターン数)。"""
    # 文字列でない、または区切りが1つも無い場合は判断材料が無いので触らない。
    if not isinstance(text, str) or SEPARATOR not in text:
        return text, 0

    # split の先頭要素は最初の区切りより前の前置き。
    # それ以降が各ターン。
    parts = text.split(SEPARATOR)
    preamble, turns = parts[0], parts[1:]
    if len(turns) <= keep:
        return text, 0

    # join で組み直すことで、前置きと最初のターンの間の区切りも自然に復元される。
    # 前置きが空文字列のケースでも、先頭に区切りが付く形がそのまま保たれる。
    trimmed = SEPARATOR.join([preamble] + turns[-keep:])
    return trimmed, len(turns) - keep


def apply(ctx):
    import sys

    module = sys.modules.get("scripts.llm.llm_manager")
    if module is None:
        ctx.log("scripts.llm.llm_manager not loaded; skipping", level="WARN")
        return

    state = {"trims": 0}

    # 102_fix_prompt_dedup.py と同じファイルに書く。
    # プロンプトが膨らむ原因は複数あるので、1本にまとめた方が読みやすい。
    write = ctx.logger(LOG_BASENAME)

    installed = 0
    for fn_name in TARGETS:
        # 存在確認は既定値付き getattr で行う（TECH.md §6 の hasattr 禁止）。
        if getattr(module, fn_name, None) is None:
            ctx.log("  {} not present; skipped".format(fn_name), level="WARN")
            continue

        # ループ変数を閉じ込めるために関数で包んでいる。
        # これをやらないと、Python のクロージャの性質上、
        # 作られた全ラッパがループ終了後の fn_name（＝最後の1つ）を参照してしまう。
        def make(name):
            @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False)
            def fix(orig, *args, **kwargs):
                # 呼び出し側がキーワード引数で渡すか位置引数で渡すかは、
                # コンパイル済みなので読めない。
                # 両方を見て、どちらでも無ければ何もせずに素通しする。
                original = kwargs.get(EVENT_LOG_KEYWORD)
                from_kwargs = original is not None
                if not from_kwargs and len(args) > EVENT_LOG_INDEX:
                    original = args[EVENT_LOG_INDEX]

                if not isinstance(original, str):
                    return orig(*args, **kwargs)

                try:
                    trimmed, dropped = trim_event_log(original)
                except Exception:
                    # 刈り込みに失敗しても呼び出しは通す。
                    # 推論を止めない。
                    ctx.log_exc("eventlog: trim failed in {}; passing through".format(name))
                    return orig(*args, **kwargs)

                if not dropped:
                    return orig(*args, **kwargs)

                state["trims"] += 1
                write("[EVENTLOG] {}: dropped {} turn(s), kept {} | {} -> {} chars "
                      "(saved {})".format(name, dropped, KEEP_TURNS,
                                          len(original), len(trimmed),
                                          len(original) - len(trimmed)))
                # 最初の数回は前後の先頭・末尾を残す。
                # ターンの切れ目と前置きの扱いが正しいか、目で確認するため。
                if state["trims"] <= AUDIT_FIRST_N:
                    write("    head before: {!r}".format(original[:160]))
                    write("    head after : {!r}".format(trimmed[:160]))
                    write("    tail after : {!r}".format(trimmed[-160:]))

                # 受け取ったときと同じ渡し方で差し替える。
                # 位置引数の場合、タプルは書き換えられないのでリスト経由で組み直す。
                if from_kwargs:
                    kwargs[EVENT_LOG_KEYWORD] = trimmed
                else:
                    args = list(args)
                    args[EVENT_LOG_INDEX] = trimmed
                    args = tuple(args)
                return orig(*args, **kwargs)
            return fix

        make(fn_name)
        installed += 1

    # 注入時の自己テスト。
    # 実際の刈り込みはクエストが3ターンを超えるまで起きないので、
    # 作ったデータで先に確認しておく。
    sample = "PRE" + "".join("{}turn{}".format(SEPARATOR, i) for i in range(7))
    trimmed, dropped = trim_event_log(sample)
    expected = SEPARATOR.join(["PRE", "turn4", "turn5", "turn6"])
    short = "PRE" + SEPARATOR + "only"          # ターン数が少ないので変化しないはず
    unchanged, zero = trim_event_log(short)

    if trimmed == expected and dropped == 4 and unchanged == short and zero == 0:
        ctx.log("verified: 7 turns -> 3 (dropped 4), preamble preserved, "
                "short logs untouched")
    else:
        ctx.log("VERIFY FAILED: got {!r} dropped={} / short {!r} dropped={}".format(
            trimmed, dropped, unchanged, zero), level="ERROR")

    ctx.log("eventlog trim installed on {}/{} target(s)".format(installed, len(TARGETS)))
