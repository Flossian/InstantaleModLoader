# -*- coding: utf-8 -*-
"""LLM に送るプロンプトの中で、同じブロックが重複しているのを取り除く。

症状としては、クエストを再生成するたびにスキーマの system ブロック
（8,250 文字ほど）が積み増しされていき、プロンプトがどんどん膨らむ。
外部プロキシ（InstantaleLLMProxy）では、組み上がった後のプロンプト文字列を
走査して「隣り合う同一の 1000 文字以上の並び」を探すことで対処していた。

ゲームのプロセスの中で見ると、この重複はもう一段手前で、はっきりした形で
現れている。**同じ内容のメッセージが messages リストに2つ入っているだけ**
（実測では最大3コピーまで増える。VERIFICATION.md §2.3）。

だからテキストを走査する必要はなく、(role, content) を比べて重複を落とせば済む。
隣のブロックを巻き込む心配も無い。

判定条件（完全一致・隣接・1000 文字以上）はプロキシ側と揃えてあるので、
結果も同じになる。同じ内容のコピーが減るだけなので、プロンプトの意味は変わらない。

直していないこと: そもそもクエスト再生成のたびにブロックを追加している処理。
これはリクエストごとの増加を打ち消すだけで、発生源は止めていない。
その点はプロキシ版と同じ割り切りをしている。
"""

import datetime
import hashlib

MIN_BLOCK_CHARS = 1000   # プロキシ側と同じ閾値
AUDIT_FIRST_N = 5        # 最初の数回だけ、残った内容を詳しくログに出す


def _content(message):
    """メッセージから (role, content) を取り出す。

    dict でないものや、content が文字列でないものが来てもここで吸収するので、
    呼び出し側は型を気にしなくてよい。
    """
    try:
        role = message.get("role", "?")
        content = message.get("content", "")
    except Exception:
        return "?", str(message)
    return role, content if isinstance(content, str) else str(content)


def collapse_adjacent(messages, min_chars=MIN_BLOCK_CHARS):
    """直前に残したメッセージと同じ内容のものを落とす。

    戻り値は (残したリスト, 落としたものの情報)。
    """
    if not messages:
        return messages, []

    kept = []
    removed = []
    for index, message in enumerate(messages):
        role, content = _content(message)
        # 比較相手は「元のリストの1つ前」ではなく「最後に残したもの」。
        # こうしておくと3個以上続いていても1個にまとめられる。
        if kept:
            prev_role, prev_content = _content(kept[-1])
            if role == prev_role and content == prev_content and len(content) >= min_chars:
                removed.append((index, role, len(content)))
                continue
        kept.append(message)
    return kept, removed


def apply(ctx):
    log_path = ctx.out_path("prompt_bloat.log")
    state = {"collapses": 0}

    def write(text: str) -> None:
        # プロンプト関係の計測は modloader.log とは分けて prompt_bloat.log に出す。
        # 103_fix_eventlog_trim.py も同じファイルに書くので、時系列で並べて読める。
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("dedup: write failed")

    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template")
    def _apply_chat_template(orig, self, model, messages, timeout=None, *args, **kwargs):
        try:
            kept, removed = collapse_adjacent(messages)
        except Exception:
            # 重複除去に失敗しても、プロンプトはそのまま送る。
            # 多少長くても推論は通るので、ここで止める方が損害が大きい。
            ctx.log_exc("dedup: collapse failed; passing prompt through untouched")
            return orig(self, model, messages, timeout, *args, **kwargs)

        if removed:
            state["collapses"] += 1
            before = sum(len(_content(m)[1]) for m in messages)
            after = sum(len(_content(m)[1]) for m in kept)
            write("[DEDUP] removed {} duplicate block(s) {} | {} -> {} msgs, "
                  "{} -> {} chars (saved {})".format(
                      len(removed),
                      ["idx{} {} {}c".format(i, r, n) for i, r, n in removed],
                      len(messages), len(kept), before, after, before - after))
            # 最初の数回だけ、残ったメッセージの role・文字数・ハッシュを並べる。
            # 「消すべきでないものまで消していないか」を目で確認するため。
            if state["collapses"] <= AUDIT_FIRST_N:
                for index, message in enumerate(kept):
                    role, content = _content(message)
                    write("    kept[{}] {}:{}:{}".format(
                        index, role, len(content),
                        hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()[:10]))
            messages = kept

        return orig(self, model, messages, timeout, *args, **kwargs)

    # 注入した時点で、作ったデータを使って動作を確認しておく。
    # 実際の経路はゲームが重複を起こしたときにしか通らず、起動直後に
    # 通る保証がないため、ここで正しさを確かめておく。
    sample = [
        {"role": "system", "content": "S" * 8250},
        {"role": "system", "content": "S" * 8250},
        {"role": "system", "content": "T" * 1898},
        {"role": "user", "content": "U" * 1850},
        {"role": "user", "content": "U" * 1850},      # 1850 >= 1000 なので落ちる
        {"role": "user", "content": "x"},
        {"role": "user", "content": "x"},             # 短いので落とさない
    ]
    kept, removed = collapse_adjacent(sample)
    expected_kept, expected_removed = 5, 2
    if len(kept) == expected_kept and len(removed) == expected_removed:
        ctx.log("verified: collapse_adjacent drops exact adjacent >={}c repeats, "
                "keeps short ones ({} -> {} messages)".format(
                    MIN_BLOCK_CHARS, len(sample), len(kept)))
    else:
        ctx.log("VERIFY FAILED: expected {} kept / {} removed, got {} / {}".format(
            expected_kept, expected_removed, len(kept), len(removed)), level="ERROR")
