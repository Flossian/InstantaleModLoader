# -*- coding: utf-8 -*-
r"""ディスクに在る**セーブの読み方**。置き場所を決めて、難読化を解いて、辞書にする。

`state.py` が「これから書く控えの置き場」を決めるのに対して、
こちらは「ゲームが既に書いたセーブを読む」側。

**この知識はローダの語彙**（TECH.md §3.2.3）。
要る場所がゲームの中と外に分かれているので、どちらからも引ける所に1つだけ置く:

    ゲームの中   `323_npc_carryover` が別の世界の NPC を読む
    ゲームの外   MOD 同梱の設定画面（`tool.py`）が世界の一覧を出す。
                 別プロセスなので `app` が無く、ディスクから読むしかない
    単体の道具   `tools\rebalance_saved_bgm.py`

写した先はいつかずれる。実際に鍵（`SAVE_KEY`）が5箇所にあった。

##### ゲームのデータはインストール先には無い

    %LOCALAPPDATA%\Darmabeko\Instantale\
    ├─ saves\<フォルダ>\savedata.json        遊んでいる世界（`app.save_data_dict`）
    └─ worlds\<フォルダ>\world_data.json     世界の骨格（`app.world_dict`）

`IML_GAME_DIR`（設定画面が渡すゲーム本体の場所）は `instantale.exe` の隣で、
**セーブはそこには無い**（実機で確認。Epic 版のインストール先の下に
`saves` も `worlds` も無かった）。だから場所は別に探す。

##### セーブは XOR で難読化されている（GAME.md §2.16）

    plain[i] = cipher[i] ^ b"Instantale_Save_Key_2026"[i % 24]

ゲーム自身の `scripts.save_codec` に素の JSON へ落ちる読み方
（`read_json_with_obfuscation_fallback`）があるので、こちらも
**素で読めたらそれ、駄目なら XOR** の順で読む。

##### 書き戻さない

読むだけ。セーブへの書き込みはゲーム自身に任せる
（`world_data.json` を書き戻さないのと同じ判断。TECH.md §3.11）。

##### フォルダ名と世界名は別

`saves\` の下のフォルダ名は世界名とは限らない。
控えの鍵に使うのは**セーブの中の世界名**で、その見方は `state.world_key_of_dict()` が
1つだけ持っている。ここでは写さずに借りる。
"""

from __future__ import annotations

import io
import json
import os

from . import state

#: セーブと世界の置き場（`%LOCALAPPDATA%\Darmabeko\Instantale`）。
DATA_VENDOR = ("Darmabeko", "Instantale")

#: セーブの難読化の鍵（GAME.md §2.16）。**ここが唯一の在り処**。
SAVE_KEY = b"Instantale_Save_Key_2026"


# ---- 置き場所 ------------------------------------------------------------

def data_dir(override: str = "") -> str:
    r"""`saves\` と `worlds\` の親。

    `override`（または環境変数 `IML_INSTANTALE_DATA`）が在ればそちら。
    無ければ `%LOCALAPPDATA%\Darmabeko\Instantale`。
    """
    if override:
        return override
    env = os.environ.get("IML_INSTANTALE_DATA")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, *DATA_VENDOR)


def saves_dir(base: str = "") -> str:
    """遊んでいる世界の親。`base` を渡せるのは検査が仮の置き場を指すため。"""
    return os.path.join(base or data_dir(), "saves")


def worlds_dir(base: str = "") -> str:
    r"""世界の骨格と立ち絵の親。`saves\` とは別の木（GAME.md §2.16）。"""
    return os.path.join(base or data_dir(), "worlds")


def save_path(world: str, base: str = "") -> str:
    r"""`world` は `saves\` の下の**フォルダ名**。世界名ではない。

    2つが同じことも多いが、外部のツールで世界名を書き換えると食い違う。
    世界名が要るときは `world_names()` が対応を返す。
    """
    return os.path.join(saves_dir(base), world, "savedata.json")


# ---- 難読化を解く --------------------------------------------------------

def xor(raw: bytes) -> bytes:
    """難読化の掛け外し。**同じ関数で往復する**（XOR なので読みにも書きにも使える）。

    `key` と `size` をループの外に出しているのは、
    100 MB を超えるセーブで属性引きが積み上がるため。
    """
    key = SAVE_KEY
    size = len(key)
    return bytes(byte ^ key[index % size] for index, byte in enumerate(raw))


def _utf8(raw: bytes):
    """UTF-8 として読めれば文字列、駄目なら `None`。

    分けてあるのは、`decode` が「素で駄目なら XOR」を試すのに
    **例外ではなく `None` で分岐したい**ため。
    XOR を掛ける前のバイト列はほぼ確実に UTF-8 として壊れているので、
    ここは失敗するのが普通の道。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode(raw: bytes):
    """セーブのバイト列を辞書にする。読めなければ `None`。

    素の JSON → XOR の順。
    ゲーム自身の `read_json_with_obfuscation_fallback` と同じ向き。
    **向きを合わせているのは、素で保存されたセーブも在りうる**ため
    （手元の世界は全部 XOR だったが、ゲーム側が両方読むので決めつけない）。
    """
    for text in (_utf8(raw), _utf8(xor(raw))):
        if text is None:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        # 辞書でなければ「読めた」と認めない。
        # XOR を掛けた結果が偶然 `123` のような JSON になることがあり、
        # そこで打ち切ると本当の中身に辿り着けない。
        if isinstance(data, dict):
            return data
    return None


def read_save(world: str, base: str = ""):
    """1世界ぶんの `savedata.json`。読めなければ `None`。

    **落とさない。** 呼び側は画面を組んでいる途中なので、
    1つのセーブが読めないだけで一覧ごと出ないのは割に合わない。
    """
    try:
        with io.open(save_path(world, base), "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    return decode(raw)


# ---- 世界の一覧 ----------------------------------------------------------

def list_worlds(base: str = "") -> list:
    r"""`savedata.json` を持つ**フォルダ名**。名前順。セーブは読まない。

    読まないので速い。世界名が要らない用途（在るかどうかだけ見る）はこちら。
    `savedata.json` の実在で絞るのは、`saves\` の下に
    セーブでないフォルダやファイルが混ざるため（実測で在った）。
    """
    root = saves_dir(base)
    try:
        names = os.listdir(root)
    except OSError:
        return []                      # まだ1度も遊んでいない、または置き場が違う
    found = [name for name in names
             if os.path.isfile(os.path.join(root, name, "savedata.json"))]
    return sorted(found, key=lambda text: text.lower())


def world_names(base: str = "") -> list:
    """`[(世界名, フォルダ名), ...]`。世界名の順。

    世界名はセーブの中から取る（`state.world_key_of_dict`）。
    **鍵を探す順をここに写さない**のが要点で、写した版が `name` の1鍵しか見ておらず、
    `world_name` や `title` で名前を持つ世界を取りこぼした実例がある。

    読めないセーブは**フォルダ名を世界名にする**
    ― 画面に出す一覧なので、読めない世界を黙って落とすより並べたほうが分かる。

    全部のセーブを復号するので `list_worlds` より重い（手元の6世界で目に見える差は無い）。
    並べ替えが `casefold` なのは、`lower` では大文字小文字が同じに揃わない
    言語があるため（世界名は本人が付けるので何が来るか決まらない）。
    """
    found = []
    for folder in list_worlds(base):
        save = read_save(folder, base)
        found.append((state.world_key_of_dict(save, folder), folder))
    return sorted(found, key=lambda pair: pair[0].casefold())
