# -*- coding: utf-8 -*-
"""この MOD が作った NPC の台帳。セーブの外に、世界ごとに持つ。

##### なぜ外部ファイルなのか

「この NPC は MOD が作った」という印を、NPC 自身には持たせられない。
セーブの NPC は33項目の決まった並びで、
項目を1つ足すとその並びが壊れる（`world.NEW_NPC_TEMPLATE` / GAME.md §2.23）。
並びが変わるとセーブを上から順に見せる道具の表示が崩れるので、
独自の項目を混ぜる選択肢は無い。

だから印は外に置く。
セーブはゲームの形のまま、MOD の都合は `state/` に。
この MOD が事件の控え（`case.py`）で既にやっていることと同じ考え方。

##### 何を持つか

作った NPC の id と名前を並べたもの。
事件が決着したら消して回るための名簿であって、
これが無いと誰を掃除してよいか分からなくなる。

```json
[{"id": "76", "name": "流れ者のミレイユ"}, ...]
```

ファイルは1世界に1つで、出し入れはローダの `state.WorldStore` が持つ
（`state/city_case/<世界>.cast.json`）。
**世界の区別をこのファイルの中に持たない。**
以前は `{"worlds": {...}}` で1ファイルにまとめていたが、
置き場所の決め方はローダの語彙で、MOD ごとに別の形を持つ理由が無い
（TECH.md §3.2.3。`WorldStore` の docstring に、写された9本で既にずれていた経緯がある）。

##### 台帳が消えても詰まないようにする

`state/` は消されうるし、台帳より前に作られた NPC も居る。
そのため掃除は台帳に載っている者と、
下地の名前と一致する者の両方を見る（`legacy_ids`）。
名前は「流れ者のミレイユ」のように固有性が高く、
ゲーム自身が同じ名前を作る見込みは薄い。
"""


def ids(rows, kept=None):
    """控えている id。`kept` で「町に残す」印での絞り込みができる。

    `kept=None` は全部、`False` は掃除してよいものだけ、`True` は残すものだけ。
    掃除の対象は
    `False` のほうで拾い、**`True` のぶんは名前で拾う掃除からも外す**ために全部（`None`）も要る。
    """
    out = []
    for row in rows or ():
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        if kept is not None and bool(row.get("keep")) != kept:
            continue
        out.append(str(row.get("id")))
    return out


def keep(rows, npc_id):
    """掃除の対象から外す。町の住人として残すと決めた者に立てる。

    消さずに台帳から外すのでは足りない。
    台帳から消えた者は「台帳より前に作られた者」として名前で拾われ、
    次の起動で消えてしまう。
    **残すことを台帳に書いておく**必要がある。
    """
    for row in rows or ():
        if isinstance(row, dict) and str(row.get("id")) == str(npc_id):
            row["keep"] = True
            return True
    return False


def add(rows, npc_id, name):
    """作った1体を控える。作った直後に呼ぶこと。

    事件の控えに書く前に落ちても掃除できるように、`make_npc` が通ったその場で足す。
    二重に足さない。
    """
    if any(isinstance(row, dict) and str(row.get("id")) == str(npc_id)
           for row in rows):
        return False
    rows.append({"id": str(npc_id), "name": name})
    return True


def drop(rows, npc_id):
    """掃除できた1体を台帳から外す。**その場で書き換える**（控えは呼び側が書く）。"""
    left = [row for row in rows
            if not (isinstance(row, dict) and str(row.get("id")) == str(npc_id))]
    if len(left) == len(rows):
        return False
    rows[:] = left
    return True


def legacy_ids(npcs, names, skip=()):
    """台帳より前に作られたぶんを、下地の名前で拾う。

    `npcs` は素データの辞書、`names` は下地の名前の集合。
    `skip` に挙げた id（進行中の事件のキャスト）は外す。

    名前で拾うのは当てずっぽうに近いので、**下地に実在する名前と完全一致** することだけを条件にする。
    部分一致や似た名前は拾わない。
    """
    out = []
    for npc_id, data in (npcs or {}).items():
        if str(npc_id) in {str(i) for i in skip}:
            continue
        if isinstance(data, dict) and data.get("name") in names:
            out.append(str(npc_id))
    return sorted(out, key=lambda value: (len(value), value))
