# -*- coding: utf-8 -*-
"""店の品揃えの傾向を、MOD どうしで分け合う窓口。

ゲームの品揃えは LLM が作り、どの店にも「武器と防具を1つずつ必ず作る」と頼んでいる（GAME.md §2.13.1.2）。
店の種類ごとの傾向（鍛冶屋は武器と防具、宿屋は食べ物など）を決める MOD と、
品揃えを別の頼みで作り直す MOD（入れ替え）が別々に居るので、傾向の一文をここで仲介する。
MOD どうしは import しない（TECH.md §3.2.3）。

    傾向を決める側（apply() の中）   shop_lineup.provide(owner, ctx, fn, exclude=None)
                                     fn(facility) -> その店に並べる品の傾向の一文 / None（口を出さない）
                                     exclude(facility) -> その店に並べない品の種類（`item_type` の並び）/ None
    品を作る側（頼みを組むとき）     text, by = shop_lineup.lineup_of(facility)
                                     kinds = shop_lineup.excluded_of(facility)   並べない種類（作っても棚に入れない）

`lineup_of` は登録の新しいものから聞き、最初に文を返した登録を使う（`by` はその持ち主）。
`excluded_of` は同じ順で聞き、最初に並びを返した登録の種類（小文字）を返す。誰も返さなければ空。
誰も口を出さなければ `(None, None)` で、作る側は自分の既定の文を使う。

登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は聞かない。
`fn` が投げた例外は握り、その MOD は口を出さなかったものとみなす（`errors` に残す）。

置き場は `sys` の属性（`_instantale_shop_lineup`）。注入し直しをまたいで残る。
"""
import sys

STORE_ATTR = "_instantale_shop_lineup"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"providers": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    return store


def provide(owner, ctx, fn, exclude=None):
    """`owner` の傾向を決める関数を登録する。同じ持ち主の登録は差し替える（新しい登録として後ろへ）。"""
    providers = _store()["providers"]
    providers.pop(str(owner), None)
    providers[str(owner)] = (ctx, fn, exclude)


def withdraw(owner):
    """登録を下げる。"""
    _store()["providers"].pop(str(owner), None)


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def lineup_of(facility):
    """`(傾向の一文, 決めた持ち主)`。誰も口を出さなければ `(None, None)`。"""
    store = _store()
    for owner, entry in reversed(list(store["providers"].items())):
        ctx, fn, _exclude = _parts(entry)
        got = _ask(store, owner, ctx, fn, facility)
        if isinstance(got, str) and got.strip():
            return got.strip(), owner
    return None, None


def excluded_of(facility):
    """その店に並べない品の種類（小文字の `item_type` の tuple）。誰も口を出さなければ空。"""
    store = _store()
    for owner, entry in reversed(list(store["providers"].items())):
        ctx, _fn, exclude = _parts(entry)
        if exclude is None:
            continue
        got = _ask(store, owner, ctx, exclude, facility)
        if isinstance(got, (list, tuple, set, frozenset)):
            return tuple(sorted(str(kind).strip().lower() for kind in got if kind))
    return ()


def _parts(entry):
    """登録の `(ctx, fn, exclude)`。注入し直す前の版の2つ組（`exclude` が無い）も読む。"""
    return (tuple(entry) + (None, None, None))[:3]


def _ask(store, owner, ctx, fn, facility):
    """登録の関数を1つ呼ぶ。用済みの ctx・投げた関数は None（投げたら `errors` に残す）。"""
    if not callable(fn) or not _alive(ctx):
        return None
    try:
        return fn(facility)
    except Exception as exc:
        store["errors"].append("{}: {}: {}".format(owner, type(exc).__name__, exc))
        del store["errors"][:-20]
        return None


def owners():
    """いま聞いている登録の持ち主。ログ用。"""
    return [owner for owner, entry in _store()["providers"].items() if _alive(entry[0])]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"providers": {}, "errors": []})
