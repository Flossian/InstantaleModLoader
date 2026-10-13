# -*- coding: utf-8 -*-
"""掲示板の依頼を、主人公以外の誰が狙い・片付けたか、どれが急ぎかを MOD どうしで分け合う窓口。

依頼を片付ける人物を持つ MOD（ライバルの冒険者など）と、依頼の行方を見て世界を動かす MOD（街の出来事など）がある。
後者が前者の控えのファイルを直に読むと、控えの形を変えたとき・片付ける人物が増えたときに黙って読めなくなる。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。

    片付ける側（apply() の中）   quest_claims.declare(owner, ctx, fn)
                                 fn(app) -> [{"quest", "area", "title", "by", "state", "day"}, …]
                                 state は AIMING（狙っている）/ DONE（片付けた）。by は片付ける人物の名前
    急ぎを置く側（apply() の中） quest_claims.declare_urgent(owner, ctx, fn)
                                 fn(app) -> [{"quest", "area", "why"}, …]   # 放っておくと悪くなる依頼など
    読む側                       quest_claims.claims(app, state=None)     # 行の並び（`owner` を足して返す）
                                 quest_claims.claim_of(app, quest_id, state=None)   # その依頼の行か None
                                 quest_claims.urgent(app)                 # 急ぎの行の並び（`owner` を足して返す）

依頼の id は文字列にそろえて返す。
登録は持ち主ごとに1つ（同じ持ち主の登録は差し替える）。
登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない（`board` / `talk_affinity` と同じ）。
`fn` が投げた例外は握り、その MOD は何も置かなかったものとみなす（`errors` に残す）。
**`fn` は控えの錠を取らずに読むだけにする。**
読む側は自分の控えの錠を持ったまま聞きに来る（ライバルが狙いを決める最中など）。
置く側も錠を取ると、2つの MOD が互いの錠を逆の順に取り合い、別々のスレッドで固まるおそれがある。
1つの依頼に別々の持ち主の行が重なったら、`claim_of` は片付けた行を狙っている行より先に返す。

置き場は `sys` の属性（`_instantale_quest_claims`）。注入し直しをまたいで残る。
"""
import sys

STORE_ATTR = "_instantale_quest_claims"

AIMING = "aiming"
DONE = "done"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"claims": {}, "urgent": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    store.setdefault("claims", {})
    store.setdefault("urgent", {})
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
    """`owner` が狙っている・片付けた依頼の行を返す関数を登録する。同じ持ち主の登録は差し替える。"""
    _store()["claims"][str(owner)] = (ctx, fn)


def declare_urgent(owner, ctx, fn):
    """`owner` が急ぎとする依頼の行を返す関数を登録する。同じ持ち主の登録は差し替える。"""
    _store()["urgent"][str(owner)] = (ctx, fn)


def withdraw(owner):
    """`owner` の登録を両方とも下げる。"""
    store = _store()
    store["claims"].pop(str(owner), None)
    store["urgent"].pop(str(owner), None)


def _rows(kind, app):
    store = _store()
    out = []
    for owner, (ctx, fn) in list(store[kind].items()):
        if not _alive(ctx):
            continue
        try:
            got = fn(app)
        except Exception as exc:
            store["errors"].append("{} {}: {}: {}".format(kind, owner, type(exc).__name__, exc))
            del store["errors"][:-20]
            continue
        for row in got or ():
            if not isinstance(row, dict) or row.get("quest") in (None, ""):
                continue
            row = dict(row)
            row["quest"] = str(row["quest"])
            if row.get("area") is not None:
                row["area"] = str(row["area"])
            row["owner"] = owner
            out.append(row)
    return out


def claims(app, state=None):
    """狙っている・片付けた依頼の行。`state` を渡せばその状態だけ。"""
    rows = _rows("claims", app)
    if state is not None:
        rows = [row for row in rows if row.get("state") == state]
    return rows


def claim_of(app, quest_id, state=None):
    """その依頼の行。片付けた行を先に返す。誰も置いていなければ None。"""
    rows = [row for row in claims(app, state) if row["quest"] == str(quest_id)]
    rows.sort(key=lambda row: 0 if row.get("state") == DONE else 1)
    return rows[0] if rows else None


def urgent(app):
    """急ぎの依頼の行。"""
    return _rows("urgent", app)


def owners():
    """いま数えている登録の持ち主（片付ける側・急ぎを置く側）。ログ用。"""
    store = _store()
    return ([owner for owner, (ctx, _fn) in store["claims"].items() if _alive(ctx)],
            [owner for owner, (ctx, _fn) in store["urgent"].items() if _alive(ctx)])


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"claims": {}, "urgent": {}, "errors": []})
