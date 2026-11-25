# -*- coding: utf-8 -*-
"""店の品揃えの傾向を、MOD どうしで分け合う窓口。

ゲームの品揃えは LLM が作り、どの店にも「武器と防具を1つずつ必ず作る」と頼んでいる（GAME.md §2.13.1.2）。
店の種類ごとの傾向（鍛冶屋は武器と防具、宿屋は食べ物など）を決める MOD と、
品揃えを別の頼みで作り直す MOD（入れ替え）が別々に居るので、傾向の一文をここで仲介する。
MOD どうしは import しない（TECH.md §3.2.3）。

    傾向を決める側（apply() の中）   shop_lineup.provide(owner, ctx, fn, exclude=None)
                                     fn(facility) -> その店に並べる品の傾向の一文 / None（口を出さない）
                                     exclude(facility) -> その店に並べない品の種類（`item_type` の並び）/ None
    傾向に一文を足す側（apply() の中） shop_lineup.add_note(owner, ctx, fn)
                                     fn(facility) -> 傾向の後ろに足す一文（句点で終える）/ None（その店では足さない）
    品を作る側（頼みを組むとき）     text, by = shop_lineup.lineup_of(facility)
                                     kinds = shop_lineup.excluded_of(facility)   並べない種類（作っても棚に入れない）
                                     notes = shop_lineup.notes_of(facility)      足す文（傾向か既定の文の後ろに繋ぐ）

`lineup_of` は登録の新しいものから聞き、最初に文を返した登録を使う（`by` はその持ち主）。
`excluded_of` は同じ順で聞き、最初に並びを返した登録の種類（小文字）を返す。誰も返さなければ空。
誰も口を出さなければ `(None, None)` で、作る側は自分の既定の文を使う。

足す文（`add_note`）は、傾向を決める MOD とは別の MOD が「この店にはこういう品も置いてほしい」と
頼むための口（`350_` が診療所に体に組み込む素材を置かせる）。傾向は1つの文を選ぶが、足す文は全部を
登録の古いものから順に繋ぐ（句点で終わっていない文には「。」を足す）。
`lineup_of` の返り値に足す文は入らない。品を作る側（`340_`・`312_`）が `notes_of` を読んで、
傾向の文（無ければ自分の既定の文）の後ろに1回だけ繋ぐ。

登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は聞かない。
`fn` が投げた例外は握り、その MOD は口を出さなかったものとみなす（`errors` に残す）。

置き場は `sys` の属性（`_instantale_shop_lineup`）。注入し直しをまたいで残る。
"""
import sys

from . import state, window_call, window_entries, window_put

STORE_ATTR = "_instantale_shop_lineup"
#: 足す文の終わりとみなす字。これで終わっていない文には「。」を足す。
SENTENCE_ENDS = ("。", "．", ".", "！", "？", "!", "?")


def _store():
    # 足す文の口（`notes`）の無い前の版の置き場を引き継いだときも、足りない鍵は足される（`state.process_store`）。
    return state.process_store(STORE_ATTR, {"providers": {}, "notes": {}, "errors": []})


def provide(owner, ctx, fn, exclude=None):
    """`owner` の傾向を決める関数を登録する。同じ持ち主の登録は差し替える（新しい登録として後ろへ）。"""
    window_put(_store()["providers"], owner, (ctx, fn, exclude))


def withdraw(owner):
    """登録を下げる。"""
    _store()["providers"].pop(str(owner), None)


def add_note(owner, ctx, fn):
    """`owner` の足す文を答える関数を登録する。同じ持ち主の登録は差し替える。"""
    window_put(_store()["notes"], owner, (ctx, fn))


def withdraw_note(owner):
    """足す文の登録を下げる。"""
    _store()["notes"].pop(str(owner), None)


def notes_of(facility):
    """その店に足す文（登録の古いものから繋いだもの）。誰も足さなければ空文字。

    句点などで終わっていない文には「。」を足す（次の指示と繋がって読まれないように）。
    """
    store = _store()
    found = []
    for owner, entry in window_entries(store["notes"]):
        _ctx, fn = (tuple(entry) + (None, None))[:2]
        got = _ask(store, owner, fn, facility)
        if isinstance(got, str) and got.strip():
            text = got.strip()
            found.append(text if text.endswith(SENTENCE_ENDS) else text + "。")
    return "".join(found)


def lineup_of(facility):
    """`(傾向の一文, 決めた持ち主)`。誰も傾向を返さなければ `(None, None)`。足す文は `notes_of` で読む。"""
    store = _store()
    for owner, entry in window_entries(store["providers"], newest_first=True):
        _ctx, fn, _exclude = _parts(entry)
        got = _ask(store, owner, fn, facility)
        if isinstance(got, str) and got.strip():
            return got.strip(), owner
    return None, None


def excluded_of(facility):
    """その店に並べない品の種類（小文字の `item_type` の tuple）。誰も口を出さなければ空。"""
    store = _store()
    for owner, entry in window_entries(store["providers"], newest_first=True):
        _ctx, _fn, exclude = _parts(entry)
        if exclude is None:
            continue
        got = _ask(store, owner, exclude, facility)
        if isinstance(got, (list, tuple, set, frozenset)):
            return tuple(sorted(str(kind).strip().lower() for kind in got if kind))
    return ()


def _parts(entry):
    """登録の `(ctx, fn, exclude)`。注入し直す前の版の2つ組（`exclude` が無い）も読む。"""
    return (tuple(entry) + (None, None, None))[:3]


def _ask(store, owner, fn, facility):
    """登録の関数を1つ呼ぶ（用済みの ctx は `window_entries` が除いてある）。呼べない・投げた関数は None。"""
    if not callable(fn):
        return None
    _ok, got = window_call(store, owner, fn, facility)
    return got


def owners():
    """いま聞いている登録の持ち主。ログ用。"""
    return [owner for owner, _entry in window_entries(_store()["providers"])]


def note_owners():
    """足す文を登録している持ち主。ログ用。"""
    return [owner for owner, _entry in window_entries(_store()["notes"])]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"providers": {}, "notes": {}, "errors": []})
