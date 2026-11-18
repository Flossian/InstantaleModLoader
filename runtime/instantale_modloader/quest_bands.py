# -*- coding: utf-8 -*-
"""街ごとの依頼の難易度の範囲を、MOD どうしで分け合う窓口。

ゲームは街の依頼の難易度を、街の id ごとに決まった範囲から引く（GAME.md §2.9）。
初訪問の3件は `random.sample(range(lo, hi), 3)`、「クエストを探す」の1件は `random.randint(lo, hi - 1)`。
id 9 以降の街には範囲が無く、1〜76 の全域から引く。
その範囲を決め直す MOD と、範囲を読んで見せる MOD（訪ねる前の街の見積もり）が別々に居るので、
範囲をここで仲介する。MOD どうしは import しない（TECH.md §3.2.3）。

    範囲を決める側（apply() の中）   quest_bands.provide(owner, ctx, fn)
                                     fn(app, area_id) -> (下限, 上限)（両端を含む）/ None（口を出さない）
    範囲を読む側                     band, by = quest_bands.band_of(app, area_id)

`band_of` は登録の新しいものから聞き、最初に範囲を返した登録を使う（`by` はその持ち主）。
誰も口を出さなければ `(None, None)` で、読む側はゲームの範囲（自分の表）を使う。

登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は聞かない。
`fn` が投げた例外は握り、その MOD は口を出さなかったものとみなす（`errors` に残す）。
返した値が2つの整数でなければ、口を出さなかったものとみなす。

置き場は `sys` の属性（`_instantale_quest_bands`）。注入し直しをまたいで残る。
"""
import sys

from . import window_call, window_entries, window_put

STORE_ATTR = "_instantale_quest_bands"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"providers": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    return store


def provide(owner, ctx, fn):
    """`owner` の範囲を決める関数を登録する。同じ持ち主の登録は差し替える（新しい登録として後ろへ）。"""
    window_put(_store()["providers"], owner, (ctx, fn))


def withdraw(owner):
    """登録を下げる。"""
    _store()["providers"].pop(str(owner), None)


def _band(value):
    """`(下限, 上限)` の形なら整数の組、違えば None。逆さまは並べ直す。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    low, high = value
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (low, high)):
        return None
    return (low, high) if low <= high else (high, low)


def band_of(app, area_id):
    """`((下限, 上限), 決めた持ち主)`。誰も口を出さなければ `(None, None)`。"""
    store = _store()
    for owner, (_ctx, fn) in window_entries(store["providers"], newest_first=True):
        if not callable(fn):
            continue
        ok, got = window_call(store, owner, fn, app, None if area_id is None else str(area_id))
        got = _band(got) if ok else None
        if got is not None:
            return got, owner
    return None, None


def owners():
    """いま聞いている登録の持ち主。ログ用。"""
    return [owner for owner, _entry in window_entries(_store()["providers"])]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"providers": {}, "errors": []})
