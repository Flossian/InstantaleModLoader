# -*- coding: utf-8 -*-
"""関所（ゲームの関数を1枚だけ包み、MOD の前後の処理を順に呼ぶ）の共通部品。TECH.md §3.3.21。

窓口（`loads` のロードと新規開始・`durations` の日数の後・`arrivals` の着いた後）が使う。
MOD は直接は使わず、それぞれの窓口の `on` から登録する。

同じ関数を十数本の MOD が自分で包んでいた（`elapse_days` 13本・`load_game_new` 12本など。
VERIFICATION.md §3.113）。ゲームの更新で名前や引数が変わると十数か所を直すことになるので、
包むのはローダの1枚にし、MOD は前後の処理だけを渡す。

    gate = gates.Gate("loads.load", "__main__:InstantaleApp.load_game_new", "_instantale_gate_load")
    gate.on(owner, ctx, before=fn, after=fn, always=False, write=write)
    gate.install(ctx, wrapper, write)            # 1つの世代につき1枚
    return gate.run(call, lambda: orig(self, *args, **kwargs))   # 包みの中で

呼ぶ順は、MOD が自分で包んでいたときの重なりと同じにする。
後から当てた MOD ほど外側に載るので、`before` は登録の新しい順、`after` は古い順。
登録の順は `apply()` の順（＝適用順）で、注入し直すと全員が今の順で登録し直す。

| 決まり | 理由 |
|---|---|
| 登録は持ち主ごとに1つ。同じ持ち主は差し替える | 注入し直すたびに `apply()` が走る |
| 登録した ctx が用済み（`ctx.superseded()`）なら呼ばない | 切った MOD・前の世代の登録が残っても効かない |
| `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る | 前と後で控えを受け渡す形（暦の前後の差など）が多い |
| `after` は `orig` が返ったときだけ。`always=True` なら投げたときも呼ぶ（`call.error` に例外） | `try/finally` で包んでいた形（ロード中の印・移動中の窓） |
| 前後の処理の例外は握って記録し、残りの MOD と `orig` は止めない | 1本の不具合でゲームの処理を巻き込まない |
| 前後の処理は引数も戻り値も変えない | 変えるものは用途ごとの窓口（`durations.claim_days` など）で決める |

置き場は `sys` の属性（窓口ごとの名前）。注入し直しをまたいで残る。
"""
import sys

from . import log_exc

#: 作った関所の全部と、窓口が自分で持つ包みの印（`durations` の日数の関所・`arrivals` の番号の包み）。
#: `reset_all` が忘れる先。
_GATES = []
_INSTALL_ATTRS = []


def track_install_attr(attr):
    """窓口が自分で持つ包みの印（`sys` の属性名）を `reset_all` の対象に足す。"""
    if attr not in _INSTALL_ATTRS:
        _INSTALL_ATTRS.append(attr)


def reset_all():
    """関所の登録と包みの印を全部忘れる（検査用）。

    包みは1つの世代につき1枚なので、偽の ctx を作り直して `apply()` し直す検査では、
    2回目の ctx に包みが渡らない（本番は1回の boot に ctx が1つで、世代と同じ意味になる）。
    そういう検査は `apply()` の前にこれを呼ぶ。
    """
    for gate in list(_GATES):
        gate.reset()
    for attr in _INSTALL_ATTRS:
        if hasattr(sys, attr):
            delattr(sys, attr)


def alive(ctx):
    """登録した ctx がまだ用済みでないか。`superseded` を持たない ctx（検査の偽物）は生きているとみなす。"""
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


class Call(object):
    """1回の呼び出し。前後の処理に1つだけ渡る。

    | 属性 | |
    |---|---|
    | `kind` | 窓口の中の種類（`loads.LOAD` など） |
    | `obj` | 包んだ関数の `self`（関数なら None） |
    | `app` | 走っている app。読めなければ None |
    | `args` / `kwargs` | 包んだ関数が受け取った引数（`self` を除く）。書き換えても `orig` には効かない |
    | `result` | `orig` の戻り値（`after` から読む） |
    | `error` | `orig` が投げた例外（`always=True` の `after` から読む）。返ったときは None |
    | `memo` | 同じ持ち主の `before` が返した値 |

    窓口が足す属性（`days`・`save_data` など）は、それぞれの窓口の説明にある。
    """

    def __init__(self, kind, obj, args, kwargs, app=None, **extra):
        self.kind = kind
        self.obj = obj
        self.args = tuple(args)
        self.kwargs = dict(kwargs)
        self.app = app
        self.result = None
        self.error = None
        self.memo = None
        for name, value in extra.items():
            setattr(self, name, value)


class Gate(object):
    """1つの対象の関所。窓口がモジュール変数に1つ持つ。"""

    def __init__(self, name, target, attr):
        self.name = name
        self.target = target
        self.attr = attr
        _GATES.append(self)

    # -- 登録 -----------------------------------------------------------------
    def _store(self):
        store = getattr(sys, self.attr, None)
        if not isinstance(store, dict):
            store = {"hooks": {}, "seq": 0, "gate": None}
            setattr(sys, self.attr, store)
        return store

    def on(self, owner, ctx, before=None, after=None, always=False, write=None):
        """前後の処理を登録する。同じ持ち主の登録は差し替える。差し替えたら True。"""
        if before is None and after is None:
            raise ValueError("{}: on() needs before= or after=".format(self.name))
        for fn in (before, after):
            if fn is not None and not callable(fn):
                raise TypeError("{}: on() needs callables, got {!r}".format(self.name, type(fn)))
        store = self._store()
        store["seq"] += 1
        previous = store["hooks"].get(str(owner))
        store["hooks"][str(owner)] = {
            "ctx": ctx, "before": before, "after": after, "always": bool(always),
            "write": write, "seq": store["seq"],
        }
        return previous is not None

    def withdraw(self, owner):
        """登録を下げる。下げたら True。"""
        return self._store()["hooks"].pop(str(owner), None) is not None

    def entries(self):
        """生きている登録を `(持ち主, 登録)` で、登録の古い順に。"""
        hooks = self._store()["hooks"]
        rows = [(owner, entry) for owner, entry in list(hooks.items()) if alive(entry.get("ctx"))]
        rows.sort(key=lambda row: row[1].get("seq", 0))
        return rows

    def owners(self):
        """生きている登録の持ち主（登録の古い順）。ログと検査用。"""
        return [owner for owner, _entry in self.entries()]

    def reset(self):
        """全部忘れる（検査用）。"""
        setattr(sys, self.attr, {"hooks": {}, "seq": 0, "gate": None})

    # -- 包み -----------------------------------------------------------------
    def installed(self):
        """包みの状態。`{"generation", "targets"}` か、立っていなければ None。"""
        done = self._store().get("gate")
        return done if isinstance(done, dict) else None

    def install(self, ctx, wrapper, write=None):
        """対象を `wrapper` で1枚だけ包む。1つの世代につき1回。包んだ対象の名前を返す。"""
        generation = getattr(ctx, "generation", None)
        done = self.installed()
        if done is not None and done.get("generation") == generation:
            return list(done.get("targets") or [])
        ctx.wrap(self.target, required=False)(wrapper)
        self._store()["gate"] = {"generation": generation, "targets": [self.target]}
        if write:
            write("{}: the gate was declared on {}".format(self.name, self.target))
        return [self.target]

    def _call(self, owner, entry, side, call):
        fn = entry.get(side)
        try:
            return fn(call)
        except Exception as exc:
            log_exc("{}: the {} hook of {} failed".format(self.name, side, owner))
            target = entry.get("write")
            if callable(target):
                try:
                    target("WARN {}: the {} hook raised {}: {}".format(
                        self.name, side, type(exc).__name__, exc))
                except Exception:
                    pass
            return None

    def run(self, call, invoke):
        """前の処理 → `invoke()`（＝ `orig`）→ 後の処理。`invoke()` の戻り値を返し、例外はそのまま流す。"""
        rows = self.entries()
        if not rows:
            return invoke()
        memos = {}
        for owner, entry in reversed(rows):
            if entry.get("before") is not None:
                memos[owner] = self._call(owner, entry, "before", call)
        try:
            result = invoke()
        except BaseException as exc:
            call.error = exc
            for owner, entry in rows:
                if entry.get("after") is not None and entry.get("always"):
                    call.memo = memos.get(owner)
                    self._call(owner, entry, "after", call)
            raise
        call.result = result
        for owner, entry in rows:
            if entry.get("after") is not None:
                call.memo = memos.get(owner)
                self._call(owner, entry, "after", call)
        call.memo = None
        return result
