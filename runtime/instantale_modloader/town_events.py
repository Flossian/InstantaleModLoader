# -*- coding: utf-8 -*-
"""街で起きている出来事を、MOD どうしで読み・動かす窓口。

街の出来事を持つ MOD（`338_town_incidents`）と、その街での行動で出来事に関わる MOD
（労働で人手を出す・裏の仕事で悪化させる）がある。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。
読む側は出来事の MOD の保存データの形を知らず、出来事の MOD は相手の名前を知らない。

    出来事を持つ側（apply() の中）
        town_events.declare_current(owner, ctx, fn)   fn(app, area_id) -> 出来事の辞書 / None
        town_events.on_nudge(owner, ctx, fn)          fn(app, area_id, steps, by) -> 結果の辞書 / None
    関わる側
        event = town_events.current(app, area_id)     # 誰も置いていなければ None
        result = town_events.nudge(app, area_id, steps, by)   # 誰も受けなければ None

出来事の辞書（`current` の答え。足りないキーは読む側が既定で扱う）:

    name        呼び名（画面に出せる文）
    kind        種類の英名（出来事の MOD の取り決め。読む側は見分けに使わない）
    good        良い出来事か（解決の依頼が無い）
    stage       いまの段（1 から）
    last_stage  最後の段
    tone        いまの段の調子。"good" / "bad" / "calm"
    by          答えた持ち主（窓口が入れる）

`nudge` の `steps` は、負なら収まる側へ・正なら悪くなる側へ動かす段の数。
結果の辞書は `{"name", "stage", "ended"}`（`ended` は出来事が収まって終わったか）。
段が動いた・収まったことの画面の知らせは、出来事を持つ側が出す。関わる側は結果を文にしない（同じ知らせが2本出る）。

登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない。
登録の関数が送出した例外は握り、その MOD は答えなかったものとみなす（`errors` に残す）。
複数の MOD が答えたら、先に登録した MOD の答えを使う。

置き場は `sys` の属性（`_instantale_town_events`）。注入し直しをまたいで残る。
"""
from . import state, window_call, window_entries, window_put

STORE_ATTR = "_instantale_town_events"

TONES = ("good", "bad", "calm")


def _store():
    return state.process_store(STORE_ATTR, {"current": {}, "nudge": {}, "errors": []})


def _int(value, default):
    """整数ならその値、そうでなければ `default`。

    `frames.is_number` に寄せないのは答えが違うため。段は整数だけを受け、`2.0` のような float も弾く。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def declare_current(owner, ctx, fn):
    """その街でいま起きている出来事を答える関数を登録する。同じ持ち主の登録は差し替える。"""
    window_put(_store()["current"], owner, (ctx, fn))


def on_nudge(owner, ctx, fn):
    """出来事の段を動かす頼みを受ける関数を登録する。同じ持ち主の登録は差し替える。"""
    window_put(_store()["nudge"], owner, (ctx, fn))


def withdraw(owner):
    """登録を下げる。"""
    store = _store()
    store["current"].pop(str(owner), None)
    store["nudge"].pop(str(owner), None)


def current(app, area_id):
    """その街でいま起きている出来事。誰も答えなければ None。

    形を揃えて返す（`stage` は 1 以上、`last_stage` は `stage` 以上、`tone` は `TONES` のどれか）。
    """
    if area_id is None:
        return None
    store = _store()
    for owner, (_ctx, fn) in window_entries(store["current"]):
        ok, got = window_call(store, owner, fn, app, str(area_id))
        if not ok or not isinstance(got, dict):
            continue
        stage = max(1, _int(got.get("stage"), 1))
        tone = got.get("tone")
        name = got.get("name")
        return {
            "name": name if isinstance(name, str) else "",
            "kind": got.get("kind") if isinstance(got.get("kind"), str) else "",
            "good": got.get("good") is True,
            "stage": stage,
            "last_stage": max(stage, _int(got.get("last_stage"), stage)),
            "tone": tone if tone in TONES else ("good" if got.get("good") is True else "bad"),
            "by": owner,
        }
    return None


def nudge(app, area_id, steps, by):
    """その街の出来事を `steps` 段だけ動かすよう頼む。受けた MOD の結果を返す。誰も受けなければ None。

    `steps` が 0・数でないときは誰にも頼まない。
    """
    steps = _int(steps, 0)
    if area_id is None or steps == 0:
        return None
    store = _store()
    for owner, (_ctx, fn) in window_entries(store["nudge"]):
        ok, got = window_call(store, owner, fn, app, str(area_id), steps, str(by or ""))
        if not ok or not isinstance(got, dict):
            continue
        name = got.get("name")
        return {"name": name if isinstance(name, str) else "",
                "stage": max(1, _int(got.get("stage"), 1)),
                "ended": got.get("ended") is True,
                "by": owner}
    return None


def owners():
    """いま数えている登録の持ち主。ログ用。"""
    store = _store()
    names = [owner for owner, _entry in window_entries(store["current"])]
    names += [owner for owner, _entry in window_entries(store["nudge"])
              if owner not in names]
    return names


def reset():
    """登録を全部消す（検査用）。"""
    store = _store()
    store["current"].clear()
    store["nudge"].clear()
    del store["errors"][:]
