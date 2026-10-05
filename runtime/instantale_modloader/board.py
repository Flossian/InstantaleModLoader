# -*- coding: utf-8 -*-
"""ギルドの掲示板に出さない依頼を MOD どうしで分け合う窓口。

ゲーム自身の依頼の生成で依頼を作り、ギルドの掲示板からは隠して自分の入口から受けさせる MOD がある
（`913_` の裏の仕事・処刑場からの脱出）。
掲示板の依頼を拾う MOD（`911_` のライバルの狙い）は、世界の依頼の一覧から「その土地の未完了の依頼」を選ぶので、
窓口が無いと掲示板に出ない依頼まで拾う（2026-10-05 の実機。ライバルが裏の仕事を片付けたことになり、
裏の依頼掲示板からも消えた）。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。

    隠す側（apply() の中）   board.declare_kept_off(owner, ctx, fn)
                             fn(app) -> 依頼の id の並び（掲示板に出さない依頼）
    読む側                   ids = board.kept_off(app)        # 文字列の id の集合。誰も置いていなければ空

登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない（`wanted` と同じ）。
`fn` が投げた例外は握り、その MOD は何も置かなかったものとみなす（`errors` に残す）。

置き場は `sys` の属性（`_instantale_board`）。注入し直しをまたいで残る。
"""
import sys

STORE_ATTR = "_instantale_board"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"kept_off": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    return store


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def declare_kept_off(owner, ctx, fn):
    """`owner` が掲示板に出さない依頼の id を返す関数を登録する。同じ持ち主の登録は差し替える。"""
    _store()["kept_off"][str(owner)] = (ctx, fn)


def withdraw(owner):
    """登録を下げる。"""
    _store()["kept_off"].pop(str(owner), None)


def kept_off(app):
    """掲示板に出さない依頼の id（文字列）の集合。誰も置いていなければ空。"""
    store = _store()
    found = set()
    for owner, (ctx, fn) in list(store["kept_off"].items()):
        if not _alive(ctx):
            continue
        try:
            got = fn(app)
        except Exception as exc:
            store["errors"].append("{}: {}: {}".format(owner, type(exc).__name__, exc))
            del store["errors"][:-20]
            continue
        for quest_id in got or ():
            if quest_id is not None and not isinstance(quest_id, bool):
                found.add(str(quest_id))
    return found


def owners():
    """いま数えている登録の持ち主。ログ用。"""
    return [owner for owner, (ctx, _fn) in _store()["kept_off"].items() if _alive(ctx)]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"kept_off": {}, "errors": []})
