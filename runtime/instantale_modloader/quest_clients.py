# -*- coding: utf-8 -*-
"""依頼の依頼人（実在の人物）を MOD どうしで分け合う窓口。

依頼のデータには依頼人の id が無く、`client_name` は名前の文字列だけ（GAME.md §2.9）。
ゲームが作る依頼の依頼人は実在の人物と結びついておらず、名前の一部で照らすと同名の別人を拾う
（同じ世界で「エドワード」が3つの拠点の依頼人に現れ、実在のエドワードは1人だった）。
依頼人が実在の人物だと分かっているのは、その依頼を作った MOD だけ
（`301_` の会話から作った依頼、`336_` の裏の仕事と処刑場からの脱出）。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。

    置く側（apply() の中）   quest_clients.declare(owner, ctx, fn)
                             fn(app) -> [{"quest", "npc_id", "npc_name"}, …]
    読む側                   quest_clients.client_of(app, quest_id)   # その依頼の行か None
                             quest_clients.clients(app)               # 行の並び（`owner` を足して返す）

依頼の id と人物の id は文字列にそろえて返す。
登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない（`board` / `quest_claims` と同じ）。
`fn` が投げた例外は握り、その MOD は何も置かなかったものとみなす（`errors` に残す）。
`fn` は控えの錠を待たずに読むだけにする（`quest_claims` と同じ理由。読む側は会話の組み立ての最中に聞きに来る）。

置き場は `sys` の属性（`_instantale_quest_clients`）。注入し直しをまたいで残る。
"""
import sys

STORE_ATTR = "_instantale_quest_clients"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"clients": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    store.setdefault("clients", {})
    store.setdefault("errors", [])
    return store


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def declare(owner, ctx, fn):
    """`owner` が依頼人を知っている依頼の行を返す関数を登録する。同じ持ち主の登録は差し替える。"""
    _store()["clients"][str(owner)] = (ctx, fn)


def withdraw(owner):
    """登録を下げる。"""
    _store()["clients"].pop(str(owner), None)


def clients(app):
    """依頼人の分かっている依頼の行。"""
    store = _store()
    out = []
    for owner, (ctx, fn) in list(store["clients"].items()):
        if not _alive(ctx):
            continue
        try:
            got = fn(app)
        except Exception as exc:
            store["errors"].append("{}: {}: {}".format(owner, type(exc).__name__, exc))
            del store["errors"][:-20]
            continue
        for row in got or ():
            if not isinstance(row, dict) or row.get("quest") in (None, ""):
                continue
            if row.get("npc_id") in (None, "") and not row.get("npc_name"):
                continue
            row = dict(row)
            row["quest"] = str(row["quest"])
            row["npc_id"] = "" if row.get("npc_id") in (None, "") else str(row["npc_id"])
            row["npc_name"] = row.get("npc_name") or ""
            row["owner"] = owner
            out.append(row)
    return out


def client_of(app, quest_id):
    """その依頼の行。誰も置いていなければ None。"""
    quest_id = str(quest_id)
    for row in clients(app):
        if row["quest"] == quest_id:
            return row
    return None


def owners():
    """いま数えている登録の持ち主。ログ用。"""
    return [owner for owner, (ctx, _fn) in _store()["clients"].items() if _alive(ctx)]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"clients": {}, "errors": []})
