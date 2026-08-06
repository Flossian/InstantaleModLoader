# -*- coding: utf-8 -*-
"""`state/` に置くデータの**住所**。世界の見分け方と、そこから作るファイル名。

MOD は世界ごとにデータを分けて持つ（依頼の出所・NPC の人物像・店の入れ替え日・
道中の控え）。そのために要るのは2つだけ:

    world_key(app)                  この世界を見分ける鍵（＝世界名）
    world_filename(key, suffix)     鍵から `state/<MOD>/` 配下のファイル名

**この2つは MOD 固有ではなくローダの語彙**なので、ここに1つだけ置く。

## なぜ MOD 側に書かないのか

以前は各 MOD が写していた。`world_key` は5本（`301_` / `305_` / `307_` /
`311_` / `312_`）にコメントごと同じものがあり、`world_filename` に当たるものは
4本にあった。`301_` の docstring は「`311_` と1文字も違ってはいけない」と
書いていた ― **同じファイルを指すため**。

そして**実際にずれた**。`312_` は `strip` と `sub` の順が逆で、120文字での
切り詰めも `"."` / `".."` の除外も無かった。末尾に空白の残る名前を作るので、
Windows が黙って切る先とこちらの持つ文字列がずれる。写した時点で予告されて
いたドリフトが、1つの版で現実になった。

「MOD どうしはコードを共有しない」は**別の話**（TECH.md §3.2.3）。あれは
`instantale_mod_<フォルダ名>` を名指しで import すると番号を振り直した瞬間に
壊れる、という理由で、共有してよい相手は最初からローダ（`instantale_modloader.*`）
だった。**ここはその共有してよい側**にある。

## 予約デバイス名

`CON` / `NUL` / `COM1` のような名前は、不正な文字が1つも無いのに
**パス構成要素にできない**。世界名がこれだと `open()` が失敗し、広い `except`
に吸われて控えが黙って空に倒れる ― 遊びの続きが消えたことに気付けない。

この知識は `110_fix_character_name_path` が既に持っていた。あちらが**直さずに
記録するだけ**にしているのは、対象が画面に出るキャラクタ名だから（名前を発明
することになる）。**こちらはファイル名なので直してよい** ― 中身の鍵には元の
世界名がそのまま入っているので、表示に影響しない。
"""

from __future__ import annotations

import hashlib
import re

#: ファイル名に使えない文字（Windows の禁則＋制御文字）。
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

#: Windows が末尾で黙って切るもの。切られると書いた先と読む先がずれる。
_TRAILING = ". "

#: パス構成要素にできない予約デバイス名（`110_` と同じ表）。大文字小文字を問わない。
RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM{}".format(n) for n in range(1, 10)]
    + ["LPT{}".format(n) for n in range(1, 10)]
)

#: ファイル名の長さの上限（拡張子を除く）。パス全体の上限ではなく、
#: 「1要素が長すぎて作れない」を避けるための目安。
MAX_STEM = 120

#: 世界名が読めなかったときの鍵。**1世界しか使わない前提に落ちる。**
UNKNOWN_WORLD = "_"

#: 世界名を探す場所（`world_dict["world_data"]` の中）。**この順で見る。**
_WORLD_NAME_KEYS = ("world_name", "name", "title")


def world_key(app):
    """この世界を見分ける鍵を返す。読めなければ `"_"`。

    セーブの `world_data` に入っている世界名を使う。実行時のオブジェクト
    （`app.world.name`）より先にセーブ側を見るのは、ロード直後に
    `app.world` がまだ組み上がっていない場合があるため。

    **名前で見分けている。** 外部のデータ改変ツールで世界名を書き換えられると
    控えが行方不明になるが、安定した id は実測できていない（GAME.md §2.7 の
    「決めつけない」に従い、読めた方を使う）。行方不明になったことが分かるよう、
    `311_` は既知の世界ファイルを一覧して1度だけ知らせる形にしている。
    """
    world_dict = getattr(app, "world_dict", None)
    if isinstance(world_dict, dict):
        data = world_dict.get("world_data")
        if isinstance(data, dict):
            for key in _WORLD_NAME_KEYS:
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
    name = getattr(getattr(app, "world", None), "name", None)
    return name if isinstance(name, str) and name else UNKNOWN_WORLD


def _clean(key: str) -> str:
    """使える文字だけにした語幹。**この時点ではまだ一意ではない。**

      1. 前後の空白を落とす
      2. 使えない文字を `_` に置き換える
      3. 末尾の `.` と空白を落とす ― **Windows が黙って切る**ので、残すと
         書いた先と読む先がずれる
      4. 空・`"."`・`".."` は `"_"` に倒す（どれもファイル名にできない）
      5. 長すぎれば切り、切った結果また末尾が `.` になったら落とす
      6. 予約デバイス名なら `_` を前に付ける（`CON` → `_CON`）
    """
    name = _UNSAFE.sub("_", key.strip()).rstrip(_TRAILING)
    if not name or name in (".", ".."):
        name = UNKNOWN_WORLD
    if len(name) > MAX_STEM:
        name = name[:MAX_STEM].rstrip(_TRAILING) or UNKNOWN_WORLD
    if name.upper() in RESERVED:
        name = "_" + name
    return name


def _digest(key: str) -> str:
    """鍵そのものから作る短い印。**同じ鍵からは必ず同じ値**（`hash()` は使わない
    ― プロセスごとに変わるので、次に起動したとき同じファイルを指せない）。"""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def world_filename(key, suffix: str = ".json") -> str:
    """世界の鍵から `state/<MOD>/` 配下のファイル名を作る。

    **同じ鍵からは必ず同じ名前が出る。** 複数の MOD が同じファイルを指すため
    （`301_` が `311_` の `npc_profiles/<世界>.json` を読む）。ここが揺れると
    「読めない」＝相手のデータが無いことになる。

    **違う鍵からは必ず違う名前が出る。** 使える文字に均すだけだと単射でなく、
    別の世界の控えを自分のものとして読む事故が起きる:

        "a/b" と "a\\b"          どちらも "a_b"
        "CON" と "_CON"          予約名を避けるために付けた印が実在の名前と衝突
        120文字を超える2つの世界  先頭が同じなら切り詰め後は同じ

    そこで**均した結果が元の鍵と違うときだけ**、鍵そのものから作った短い印を
    後ろに付ける（`灰の街_東.json` はそのまま、`a/b` は `a_b-3f2a91c4.json`）。
    普通の世界名（使えない文字が無く、短く、予約名でない）は**印が付かない**ので、
    置き場所を分ける前から遊んでいる人のファイルもそのまま引ける。

    印が付く側は**一度だけ空から始まる**。別の世界の記憶を自分のものとして
    読むより、巻き戻るほうがよい（`ctx._adopt_from_out` と同じ判断）。

    **印の形をした鍵は分けていない。** 均す必要が無い鍵には印を付けないので、
    最初から印の形をした世界名は、印を付けた別の鍵と重なりうる:

        world_filename("a/b")            -> a_b-3ec69c85.json
        world_filename("a_b-3ec69c85")   -> a_b-3ec69c85.json   同じ

    直すなら「印の形に見える鍵にも印を付ける」ことになるが、そうすると
    **普通の名前にも印が付く場合が増え**（`-` と16進8桁で終わる世界名は
    ありえる）、置き場所を分ける前のファイルを引けなくなる側の損が大きい。
    上の衝突は**この関数の出力をそのまま世界名にする**という、故意でしか
    起こせない操作でしか作れない。割に合わないので直さず、ここに書いておく。

    > `301_` の `quest_clients.json` はこの心配が無い。あちらは単一ファイルで、
    > **世界名が中身のキー**になっている（`data[world_key(app)]`）。世界ごとに
    > ファイルを分ける形（`311_` / `312_` / `122_`）だけが、名前の一意性に
    > 頼っている。
    """
    key = key if isinstance(key, str) else ""
    stem = _clean(key)
    if stem != key:
        # 均した時点で別の鍵と重なりうる。元に戻せる印を足して分ける。
        room = MAX_STEM - 9         # "-" + 8桁ぶんを空ける
        stem = "{}-{}".format(stem[:room].rstrip(_TRAILING) or UNKNOWN_WORLD,
                              _digest(key))
    return stem + suffix
