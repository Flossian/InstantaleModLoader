# -*- coding: utf-8 -*-
"""窓口の登録簿の共通の形（ローダの内部用。MOD は使わない）。

2つの形がある。

| 形 | 中身 | 使う窓口 |
|---|---|---|
| `KindRegistry` | `{種類: (持ち主, 関数)}`。種類ごとに答える MOD を1つ置く。後から置いたほうが勝つ | `combat` / `durations` / `equipment` |
| `OwnerRegistry` | `{持ち主: 値}`。持ち主ごとに1つ置き、置いた順に聞く | `abilities` / `battle_effects` |

どちらも各窓口に同じ本体が写されていて、違うのはログの頭と `sys` の属性名だけだった。
置き場は `sys` の属性（`state.process_store`）で、注入し直しをまたいで残る。
前の世代の登録を引き継ぐためで、切った MOD のぶんはローダの `boot()` と `unload` が
`durations.forget` の片付けとして外す。属性名は写しのときのまま変えない（前の版のローダが置いた登録を引き継ぐ）。

`forget` と `owners` は各窓口に残す。登録簿のほかに片付ける物（敵ごとの登録・貸し・日数の望み）と、
ログを書く時点が窓口ごとに違うため。ここは登録簿の分（`drop` / `owners`）だけを持つ。
"""
from . import state


class KindRegistry(object):
    """`{種類: (持ち主, 関数)}` の登録簿。`attr` は置き場の `sys` の属性名、`tag` はログの頭。"""

    def __init__(self, attr, tag):
        self.attr = attr
        self.tag = tag

    def table(self) -> dict:
        """`{種類: (持ち主, 関数)}`。`sys` に置いて注入し直しをまたぐ。"""
        return state.process_store(self.attr, {})

    def declare(self, kind, fn, owner="", write=None):
        """その種類を答える関数を置く。置き換えたら前の持ち主を返す（前が居なければ None）。

        前の持ち主と違う持ち主が置き換えたときだけ `write` に1行書く。
        """
        if not callable(fn):
            raise TypeError("declare() needs a callable, got {!r}".format(type(fn)))
        registry = self.table()
        previous = registry.get(str(kind))
        registry[str(kind)] = (str(owner or ""), fn)
        before = previous[0] if previous else None
        if write and before and before != str(owner or ""):
            write("{}: {!r} is now decided by {!r} (was {!r})".format(self.tag, kind, owner, before))
        return before

    def entry(self, kind):
        """その種類の `(持ち主, 関数)`。誰も置いていなければ None。"""
        return self.table().get(str(kind))

    def drop(self, owner) -> list:
        """その持ち主が置いた種類を外す。外した種類を置いた順に返す（ログは書かない）。"""
        registry = self.table()
        gone = [kind for kind, (who, _fn) in registry.items() if who == str(owner)]
        for kind in gone:
            registry.pop(kind, None)
        return gone

    def owners(self) -> set:
        """何かを置いている持ち主の名前の集合。"""
        return {who for who, _fn in self.table().values()}

    def source_of(self, kind) -> str:
        """その種類を決めている MOD の名前。誰も置いていなければ `""`。"""
        entry = self.entry(kind)
        return entry[0] if entry else ""


class OwnerRegistry(object):
    """`{持ち主: 値}` の登録簿。置いた順に並ぶ。

    `label` は `drop` が返す種類の名前（`durations.forget` の答えに並ぶ）。
    置き直しても元の位置に残る（`window_put` のように後ろへは回さない）。
    `battle_effects` の倒した時の行の並びがこの順に従うので、写しのときの並びを変えない。
    持ち主の鍵は、置くときは `str(owner or "")`、外すときは `str(owner)`（写しのとき両方そうだった）。
    """

    def __init__(self, attr, label):
        self.attr = attr
        self.label = label

    def table(self) -> dict:
        """`{持ち主: 値}`（置いた順）。`sys` に置いて注入し直しをまたぐ。"""
        return state.process_store(self.attr, {})

    def put(self, owner, value) -> None:
        """持ち主の値を置く。同じ持ち主は差し替える。"""
        self.table()[str(owner or "")] = value

    def drop(self, owner) -> list:
        """その持ち主の値を外す。外したら `[label]`、無ければ `[]`。"""
        if self.table().pop(str(owner), None) is None:
            return []
        return [self.label]

    def owners(self) -> list:
        """置いている持ち主の名前（置いた順）。"""
        return list(self.table())
