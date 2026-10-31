# -*- coding: utf-8 -*-
"""品の価値と依頼の難易度が、どこまで数に効くかの窓口。

ゲームは報酬・才能点・品の値段・武器と回復の数を、難易度と価値が 76（`QUEST_DIFFICULTY_VALUE_MAX`）の値で止める
（VERIFICATION.md §3.112）。だから品を作る MOD（`312_` / `340_` / `921_`）は、LLM が返した価値やゲームが頼む価値を
76 で切ってきた。76 を超えた分を数に効かせる MOD（`923_beyond_76`）が入ったときだけ、その上限を上げる。
MOD どうしは import しない（TECH.md §3.2.3）。

    効かせる側（apply() の中）   value_limits.provide(owner, ctx, fn)     fn() -> 数に効く上限（整数）
    切る側                       top = value_limits.value_max()           誰も出していなければゲームの 76
                                 by = value_limits.raised_by()            上げている持ち主 / None

`value_max` は生きている登録の中でいちばん大きい値を返す。ゲームの 76 より下にはしない。
登録は持ち主ごとに1つ。登録した ctx が用済み（`ctx.superseded()`）になった登録は数えない。
`fn` が投げたら出さなかったものとみなす（`errors` に残す）。

置き場は `sys` の属性（`_instantale_value_limits`）。注入し直しをまたいで残る。
"""
import sys

STORE_ATTR = "_instantale_value_limits"

#: ゲームの上限（`scripts.functions.QUEST_DIFFICULTY_VALUE_MAX`）。読めないときの値。
GAME_MAX = 76


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"providers": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    return store


def provide(owner, ctx, fn):
    """`owner` の上限を出す関数を登録する。同じ持ち主の登録は差し替える。"""
    providers = _store()["providers"]
    providers.pop(str(owner), None)
    providers[str(owner)] = (ctx, fn)


def withdraw(owner):
    _store()["providers"].pop(str(owner), None)


def game_max():
    """ゲームの上限。読めなければ 76。"""
    functions = sys.modules.get("scripts.functions")
    value = getattr(functions, "QUEST_DIFFICULTY_VALUE_MAX", None) if functions else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else GAME_MAX


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def _offers():
    store = _store()
    out = []
    for owner, (ctx, fn) in list(store["providers"].items()):
        if not callable(fn) or not _alive(ctx):
            continue
        try:
            got = fn()
        except Exception as exc:
            store["errors"].append("{}: {}: {}".format(owner, type(exc).__name__, exc))
            del store["errors"][:-20]
            continue
        if isinstance(got, int) and not isinstance(got, bool):
            out.append((got, owner))
    return out


def value_max():
    """数に効く価値と難易度の上限。誰も上げていなければゲームの 76。"""
    base = game_max()
    return max([base] + [value for value, _owner in _offers()])


def raised_by():
    """ゲームの上限より上げている持ち主。いなければ None。"""
    base = game_max()
    best = None
    for value, owner in _offers():
        if value > base and (best is None or value > best[0]):
            best = (value, owner)
    return best[1] if best else None


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"providers": {}, "errors": []})
