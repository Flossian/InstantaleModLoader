# -*- coding: utf-8 -*-
"""`mod.json` の `author` の読み方。**この規則は1箇所にだけ置く。**

読む側が3つある:

    tools/build_mods.py   MODS.md の「提供」節の導入文
    tools/list_mods.py    MODLIST.md の提供の一覧
    tools/check_mods.py   NOTICE に提供者が載っているかの検算

3つに写すと、`author` の書き方が増えたとき（連名・別名）に片方だけが古くなる。
実際 MODS.md の「この節の3本は MoririnJP 様の提供」は**数えずに固定の文字列**で
書かれていて、4本目（`404_party_talk`）が入った時点で嘘になっていた。

## `author` の書き方

カンマ区切りの人名。**先頭の名前が出どころ**で、そこで種別が決まる:

    "author": "R01/Flossian"                このプロジェクトの著作物
    "author": "MoririnJP"                   提供されたもの
    "author": "MoririnJP, R01/Flossian"     提供されたものを共同で仕上げたもの
    "author": "R01/Flossian, MoririnJP"     こちらの MOD に提案（PR）を取り込んだもの

`SELF` 以外の名前が1つでも入っていれば**提供か提案**として扱う。
最後の2つを分けるのは、**MOD そのものの出どころが違う**ため
（`404_party_talk` は提供された MOD をこちらで仕上げたもの、
`130_currency_unit` はこちらの MOD へワールド別設定の提案を取り込んだもの）。
著作権はどちらでも提供者に残る（NOTICE）が、
「提供を受けた MOD」の一覧に自作を混ぜると本数が実態と合わなくなる。
名乗りは任意なので（`tools/check_mods.py` が問題として数えない）、
`author` が無い MOD は自分のものとして扱う ―
**他人の著作物を自分のものへ倒すことは無い**（書いていなければ提供ではない）。
"""

from __future__ import annotations

import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODS_DIR = os.path.join(ROOT, "runtime", "mods")
DOCS_DIR = os.path.join(ROOT, "docs")

#: このプロジェクトの著作者。`LICENSE` の Copyright 行と同じ綴りにすること。
#: ここが食い違うと、自分の MOD が全部「提供」に見える。
SELF = "R01/Flossian"

#: 提供者の名を文章に出すときの敬称。
HONORIFIC = "様"

#: `runtime/mods/_template/mod.json` が置いている見本の名前。
#: 作者として数えない（`_template` は MOD ではなく雛形で、
#: 数えると「提供を受けた MOD」に見本が1本混ざる）。
PLACEHOLDER_AUTHORS = ("your name here",)


def manifest(folder: str, mods_dir: str = MODS_DIR) -> dict:
    """`mod.json` を読む。読めなければ空の dict（名乗りは任意なので止めない）。"""
    path = os.path.join(mods_dir, folder, "mod.json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def authors(data: dict) -> list:
    """`author` を人名の並びにする。書いていなければ空。"""
    raw = data.get("author")
    if not isinstance(raw, str):
        return []
    return [name.strip() for name in raw.split(",") if name.strip()]


def contributors(data: dict) -> list:
    """`SELF` 以外の作者。空なら提供ではない。

    雛形が置いている見本の名前（`PLACEHOLDER_AUTHORS`）は数えない。
    """
    return [name for name in authors(data)
            if name != SELF and name not in PLACEHOLDER_AUTHORS]


#: `credit_kind()` の返り値。
GIVEN = "given"          #: MOD そのものの提供
SHARED = "shared"        #: 提供を受けたものを共同で仕上げたもの
PROPOSED = "proposed"    #: こちらの MOD へ提案（PR）を取り込んだもの


def credit_kind(data: dict) -> str:
    """提供・提案の種別。どちらでもなければ空。

    見分けるのは **`author` の先頭の名前（＝出どころ）**。
    先頭が `SELF` なら MOD 自体はこちらの著作物で、続く名前は提案を出した人。
    先頭が他人なら MOD そのものの提供で、`SELF` が続けば共同で仕上げたもの。
    """
    names = authors(data)
    if not contributors(data):
        return ""
    if names[0] == SELF:
        return PROPOSED
    return SHARED if SELF in names else GIVEN


def contributed(folders, mods_dir: str = MODS_DIR) -> list:
    """`(フォルダ, [提供者], 種別)` の並び。**渡された順を保つ。**

    種別は `credit_kind()` の3つ（`GIVEN` / `SHARED` / `PROPOSED`）。
    """
    out = []
    for folder in folders:
        data = manifest(folder, mods_dir)
        names = contributors(data)
        if names:
            out.append((folder, names, credit_kind(data)))
    return out


def names_of(rows) -> list:
    """`contributed()` の結果から、提供者の名を重複なく**出てきた順**に。

    並べ替えないのは、名の並びが「誰が最初に提供したか」を保つため。
    """
    seen, out = set(), []
    for _folder, names, _kind in rows:
        for name in names:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def credit(names) -> str:
    """人名の並びを文章に出す形（`MoririnJP 様`／`A 様・B 様`）。

    名と敬称の間を空けるのは、docs の他の箇所（欧文は前後を空ける）と揃えるため。
    """
    return "・".join(name + " " + HONORIFIC for name in names)
