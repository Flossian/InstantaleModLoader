# -*- coding: utf-8 -*-
"""主人公が閉じ込められている（牢の中など）ことを MOD どうしで分け合う窓口。

ゲームは服役中も居場所を動かさず、閉じ込められているという旗も持たない（GAME.md §2.20）。
牢の中で会話を起こす MOD（`336_` の同房の囚人）があり、その会話に別の MOD が場を動かす選択肢
（`301_` の「この話から依頼を作る」「依頼を受ける」）を足すと、依頼を受けて牢から出られてしまう
（2026-10-05 の実機。牢の中の会話にこの2つが並んだ）。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。

    閉じ込める側（apply() の中）   confinement.declare(owner, ctx, fn)
                                   fn(app) -> 理由の文字列（閉じ込めている）/ None（閉じ込めていない）
    読む側                         why, by = confinement.why(app)    # 誰も閉じ込めていなければ (None, None)

読む側は、閉じ込められている間は場を動かす選択肢（依頼・移動・雇用など）を足さない。
登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない（`wanted` と同じ）。
`fn` が投げた例外は握り、その MOD は閉じ込めていないものとみなす（`errors` に残す）。

置き場は `sys` の属性（`_instantale_confinement`）。注入し直しをまたいで残る。
"""
import sys

from . import state, window_call, window_entries, window_put

STORE_ATTR = "_instantale_confinement"


def _store():
    return state.process_store(STORE_ATTR, {"confiners": {}, "errors": []})


def declare(owner, ctx, fn):
    """`owner` が主人公を閉じ込めているかを返す関数を登録する。同じ持ち主の登録は差し替える（新しい登録として後ろへ）。"""
    window_put(_store()["confiners"], owner, (ctx, fn))


def withdraw(owner):
    """登録を下げる。"""
    _store()["confiners"].pop(str(owner), None)


def why(app):
    """`(理由, 閉じ込めている持ち主)`。誰も閉じ込めていなければ `(None, None)`。"""
    store = _store()
    for owner, (_ctx, fn) in window_entries(store["confiners"]):
        ok, got = window_call(store, owner, fn, app)
        if ok and isinstance(got, str) and got:
            return got, owner
    return None, None


def owners():
    """いま数えている登録の持ち主。ログ用。"""
    return [owner for owner, _entry in window_entries(_store()["confiners"])]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"confiners": {}, "errors": []})
