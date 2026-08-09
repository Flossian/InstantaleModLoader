# -*- coding: utf-8 -*-
"""この MOD が作った NPC の台帳。**セーブの外に持つ。**

##### なぜ外部ファイルなのか

「この NPC は MOD が作った」という印を、**NPC 自身には持たせられない。**
セーブの NPC は33項目の決まった並びで、項目を1つ足すとその並びが壊れる
（`world.NEW_NPC_TEMPLATE` / GAME.md §2.23）。並びが変わるとセーブを上から
順に見せる道具の表示が崩れるので、独自の項目を混ぜる選択肢は無い。

だから印は外に置く。**セーブはゲームの形のまま、MOD の都合は `out/` に。**
この MOD が事件の控え（`city_case.json`）で既にやっていることと同じ考え方。

##### 何を持つか

世界ごとに、作った NPC の id と名前。事件が決着したら消して回るための
名簿であって、**これが無いと誰を掃除してよいか分からなくなる。**

```json
{"worlds": {"<世界名>": [{"id": "76", "name": "流れ者のミレイユ"}, ...]}}
```

##### 台帳が消えても詰まないようにする

`out/` は消されうるし、台帳より前に作られた NPC も居る。そのため掃除は
**台帳に載っている者**と、**下地の名前と一致する者**の両方を見る
（`legacy_ids`）。名前は「流れ者のミレイユ」のように固有性が高く、
ゲーム自身が同じ名前を作る見込みは薄い。
"""

from instantale_modloader import read_json, write_json


def empty():
    return {"worlds": {}}


def load(path):
    """台帳を読む。壊れていたら**記録してから**空として扱う（控えと同じ方針）。

    ここで例外にすると、台帳が1文字壊れただけで MOD が丸ごと死ぬ。
    台帳を失う損害は「掃除し損ねる」だけで、事件そのものは動く。
    「無い（初回）」と「在るのに読めない」の区別は `read_json` が持つ。
    """
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("worlds"), dict):
        return empty()
    return data


def save(path, book):
    # 隣に書いてから差し替える（`write_json`）。素朴な open(..., "w") だと
    # 書いている最中に落ちた瞬間に台帳が消える。親フォルダも作ってくれる。
    return write_json(path, book, indent=2)


def entries(book, world_name):
    got = book.get("worlds", {}).get(world_name)
    return got if isinstance(got, list) else []


def ids(book, world_name, kept=None):
    """控えている id。`kept` で「町に残す」印での絞り込みができる。

    `kept=None` は全部、`False` は掃除してよいものだけ、`True` は残すもの
    だけ。掃除の対象は `False` のほうで拾い、**`True` のぶんは名前で拾う
    掃除からも外す**ために全部（`None`）も要る。
    """
    out = []
    for row in entries(book, world_name):
        if row.get("id") is None:
            continue
        if kept is not None and bool(row.get("keep")) != kept:
            continue
        out.append(str(row.get("id")))
    return out


def keep(book, world_name, npc_id):
    """**掃除の対象から外す。**町の住人として残すと決めた者に立てる。

    消さずに台帳から外すのでは足りない。台帳から消えた者は「台帳より前に
    作られた者」として名前で拾われ、次の起動で消えてしまう。**残すことを
    台帳に書いておく**必要がある。
    """
    for row in entries(book, world_name):
        if str(row.get("id")) == str(npc_id):
            row["keep"] = True
            return True
    return False


def add(book, world_name, npc_id, name):
    """作った1体を控える。**作った直後に呼ぶこと。**

    事件の控えに書く前に落ちても掃除できるように、`make_npc` が通った
    その場で足す。二重に足さない。
    """
    rows = book.setdefault("worlds", {}).setdefault(world_name, [])
    if any(str(row.get("id")) == str(npc_id) for row in rows):
        return False
    rows.append({"id": str(npc_id), "name": name})
    return True


def drop(book, world_name, npc_id):
    """掃除できた1体を台帳から外す。"""
    rows = book.get("worlds", {}).get(world_name)
    if not isinstance(rows, list):
        return False
    keep = [row for row in rows if str(row.get("id")) != str(npc_id)]
    if len(keep) == len(rows):
        return False
    book["worlds"][world_name] = keep
    return True


def legacy_ids(npcs, names, skip=()):
    """台帳より前に作られたぶんを、**下地の名前**で拾う。

    `npcs` は素データの辞書、`names` は下地の名前の集合。`skip` に挙げた id
    （進行中の事件のキャスト）は外す。

    名前で拾うのは当てずっぽうに近いので、**下地に実在する名前と完全一致**
    することだけを条件にする。部分一致や似た名前は拾わない。
    """
    out = []
    for npc_id, data in (npcs or {}).items():
        if str(npc_id) in {str(i) for i in skip}:
            continue
        if isinstance(data, dict) and data.get("name") in names:
            out.append(str(npc_id))
    return sorted(out, key=lambda value: (len(value), value))
