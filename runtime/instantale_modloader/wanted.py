# -*- coding: utf-8 -*-
"""手配の重さの数え方と、全域手配の線を MOD どうしで分け合う窓口。

手配度の**読み方**は `ui`（`lawfulness_of` / `lawfulness_by_area`。TECH.md §5.1.3）。
ここに置くのは、複数の MOD が同じ物差しで比べる必要がある**数え方**と**線**。

追手を出す MOD（`316_bounty_hunter`）は「全ての土地の手配の重さの合計がこれ以上なら、どこに居ても追ってくる」線を持つ。
手配を時とともに戻す MOD は、その線より下へ戻して全域手配を解いてしまわないよう、線を知りたい。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。

    線を置く側（apply() の中）   wanted.declare_hunted(owner, ctx, fn)
                                 fn(app) -> 合計の線（数）/ None（いまは全域から追わない）
    線を読む側                   line, by = wanted.hunted_line(app)   # 誰も置いていなければ (None, None)
    重さ                         wanted.weight_of(手配度) / wanted.total_weight(character)
    追手の戦闘の終わり           wanted.on_hunt_end(owner, ctx, fn) / wanted.hunt_ended(app, hunt)

線を置く MOD が入っていない（切った・適用に失敗した）ときは `(None, None)` が返り、
全域手配というもの自体が無い扱いになる。
複数の MOD が置いたら**いちばん低い線**を返す（どれか1つの条件を満たせば追われるので）。

登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない
（新しい世代でその MOD が外れたとき、前の世代の登録が残って効き続けないように。`talk_affinity` と同じ）。
`fn` が投げた例外は握り、その MOD は線を置かなかったものとみなす（`errors` に残す）。

置き場は `sys` の属性（`_instantale_wanted`）。注入し直しをまたいで残る。
"""
import sys

from . import ui

STORE_ATTR = "_instantale_wanted"

#: 手配とみなす境界。これ未満の手配度が手配（GAME.md §2.20。`309_` の既定と同じ）。
WANTED_BELOW = 0


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"hunted": {}, "hunt_end": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    return store


# --------------------------------------------------------------------------
# 重さ
# --------------------------------------------------------------------------
def weight_of(lawfulness):
    """その土地の手配の重さ。手配度が `WANTED_BELOW` からどれだけ下か。手配されていなければ 0。

    読めない値（`None`・真偽値・文字列）は 0。読めなかったことを手配の重さに変換しない。
    `309_` の罰金・`316_` の追手の条件と強さが、この物差しで数える。
    """
    if isinstance(lawfulness, bool) or not isinstance(lawfulness, (int, float)):
        return 0
    return max(0, WANTED_BELOW - int(lawfulness))


def total_of(values):
    """手配度の並び（または `{土地: 手配度}`）の重さの合計。"""
    if isinstance(values, dict):
        values = values.values()
    return sum(weight_of(value) for value in values)


def total_weight(character):
    """その人物の全ての土地の重さの合計。読めなければ 0。"""
    return total_of(ui.lawfulness_by_area(character))


# --------------------------------------------------------------------------
# 全域手配の線
# --------------------------------------------------------------------------
def declare_hunted(owner, ctx, fn):
    """`owner` の全域手配の線を返す関数を登録する。同じ持ち主の登録は差し替える。"""
    _store()["hunted"][str(owner)] = (ctx, fn)


def withdraw(owner):
    """登録を下げる。"""
    _store()["hunted"].pop(str(owner), None)


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def hunted_line(app):
    """`(線, 決めた持ち主)`。誰も線を置いていなければ `(None, None)`。"""
    store = _store()
    line, by = None, None
    for owner, (ctx, fn) in list(store["hunted"].items()):
        if not _alive(ctx):
            continue
        try:
            got = fn(app)
        except Exception as exc:
            store["errors"].append("{}: {}: {}".format(owner, type(exc).__name__, exc))
            del store["errors"][:-20]
            continue
        if isinstance(got, bool) or not isinstance(got, (int, float)):
            continue
        got = int(got)      # 弾く判定も比べるのも、返す値（切り捨てた整数）で行う
        if got <= 0:
            continue
        if line is None or got < line:
            line, by = got, owner
    return line, by


def owners():
    """いま数えている登録の持ち主。ログ用。"""
    return [owner for owner, (ctx, _fn) in _store()["hunted"].items() if _alive(ctx)]


# --------------------------------------------------------------------------
# 追手の戦闘の終わり
# --------------------------------------------------------------------------
# どの戦闘が追手の戦闘かを知っているのは、追手を出した MOD だけ
# （ゲーム自身の衛兵と同じ戦闘を使うので、戦闘の側からは見分けられない）。
# 追手を出した MOD が終わりを知らせ、追手の戦闘に何かを足したい MOD が受ける。
#
#     受ける側（apply() の中）   wanted.on_hunt_end(owner, ctx, fn)    fn(app, hunt)
#     知らせる側（戦闘の終わり） wanted.hunt_ended(app, hunt)
#
# `hunt` は辞書: `{"by": 知らせた持ち主, "outcome": "won" / "escaped" / その他,
#                 "difficulty": 追手の難易度, "here": 今いる土地の重さ, "total": 重さの合計}`。
# `outcome` はゲームの `BattleEndManager(app, end_type)` の `end_type` をそのまま渡す。
def on_hunt_end(owner, ctx, fn):
    """追手の戦闘の終わりを受ける関数を登録する。同じ持ち主の登録は差し替える。"""
    _store().setdefault("hunt_end", {})[str(owner)] = (ctx, fn)


def hunt_ended(app, hunt):
    """追手の戦闘が終わったことを知らせる。受けた持ち主の名前を返す。

    受ける側が投げた例外は握って `errors` に残す（知らせる側の戦闘の後始末を止めない）。
    """
    store = _store()
    reached = []
    for owner, (ctx, fn) in list(store.setdefault("hunt_end", {}).items()):
        if not _alive(ctx):
            continue
        try:
            fn(app, dict(hunt or {}))
            reached.append(owner)
        except Exception as exc:
            store["errors"].append("{}: {}: {}".format(owner, type(exc).__name__, exc))
            del store["errors"][:-20]
    return reached


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"hunted": {}, "hunt_end": {}, "errors": []})
