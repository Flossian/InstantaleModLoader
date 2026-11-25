# -*- coding: utf-8 -*-
"""会話で上げられる好感度の上限を、NPC ごとに MOD どうしで分け合う窓口。

会話の中身で好感度を上下させる MOD は、全員に同じ上限を使う。
だが、別の MOD が好感度を「関係の進み具合」の目安に使っている相手は、会話だけで上限まで上がると
その MOD が用意した機会（出来事・張り合いなど）を飛ばして関係が進んでしまう。
そこで、相手を持つ MOD がその相手の上限を狭められるようにする。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。

    上限を狭める側（apply() の中）   talk_affinity.limit(owner, ctx, fn)
                                     fn(app, npc_id) -> その相手の上限（数）/ None（口を出さない）
    好感度を上げる側（上げる直前）   value, by = talk_affinity.ceiling(app, npc_id, 自分の上限)

`ceiling` は自分の上限と、口を出した MOD の上限のうち**いちばん低いもの**を返す（`by` はそれを決めた持ち主。
自分の上限のままなら None）。
上限は狭めるだけで、広げることはできない。
下げる方には関わらない。

登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
注入し直したときは新しい世代が登録し直す。
登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない
（新しい世代でその MOD が外れたとき、前の世代の登録が残って効き続けないように）。
`fn` が投げた例外は握り、その MOD は口を出さなかったものとみなす（`errors` に残す）。

置き場は `sys` の属性（`_instantale_talk_affinity`）。注入し直しをまたいで残る。
"""
import sys

from . import frames, state, window_call, window_entries, window_put

STORE_ATTR = "_instantale_talk_affinity"


def _store():
    return state.process_store(STORE_ATTR, {"limits": {}, "errors": []})


def limit(owner, ctx, fn):
    """`owner` の相手の上限を決める関数を登録する。同じ持ち主の登録は差し替える。"""
    window_put(_store()["limits"], owner, (ctx, fn))


def withdraw(owner):
    """登録を下げる。"""
    _store()["limits"].pop(str(owner), None)


def ceiling(app, npc_id, default):
    """`(上限, 決めた持ち主)`。誰も狭めなければ `(default, None)`。"""
    store = _store()
    value, by = default, None
    for owner, (_ctx, fn) in window_entries(store["limits"]):
        ok, got = window_call(store, owner, fn, app, str(npc_id))
        if not ok:
            continue
        if not frames.is_number(got):
            continue
        if got < value:
            value, by = got, owner
    return value, by


def owners():
    """いま数えている登録の持ち主。ログ用。"""
    return [owner for owner, _entry in window_entries(_store()["limits"])]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"limits": {}, "errors": []})
