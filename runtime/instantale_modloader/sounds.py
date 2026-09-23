# -*- coding: utf-8 -*-
"""曲まわりの共通部品。ゲームの曲の置き場所と、戦闘曲の見分け方。

`104_balance_area_bgm` / `106_fix_battle_bgm_restore` / `322_battle_bgm` /
`324_place_bgm` に同じ本体が写されていて、docstring が「`106_` と同じ判定」と
互いを参照していた（写して回るものはローダの語彙。TECH.md §3.2.3）。

**曲の一覧の作り方（`list_tracks`）は置かない。**
`322_` はフォルダ直下だけ、`324_` は再帰で `battle/` を除く。名前が同じだけで仕様が違う。

設定画面（`tool.py`。ゲームの外）はローダを import できないことがあるので、
あちらの `EXTENSIONS` は各自で持ったままにしてある。
"""
import os
import sys

#: 曲として拾う拡張子（小文字）。
EXTENSIONS = (".mp3", ".ogg", ".wav")

#: 曲の置き場（ゲーム本体のフォルダからの相対）。
MUSIC_SUBDIR = ("Assets", "sounds", "musics")

#: この文字列をパスに含む曲だけが戦闘曲（`Assets/sounds/musics/battle/` 配下）。
BATTLE_DIR_MARK = "/musics/battle/"


def game_root(subdir=MUSIC_SUBDIR):
    """ゲーム本体のフォルダ。`subdir` が在る場所を探す。見つからなければ None。

    リコンの結果ではゲームプロセスのカレントディレクトリがゲーム本体のフォルダだったので、
    まずそこを見る。カレント → 実行ファイルの隣 → `sys.prefix` の順。
    """
    seen = []
    for get in (os.getcwd,
                lambda: os.path.dirname(os.path.abspath(sys.executable)),
                lambda: sys.prefix):
        try:
            base = get()
        except Exception:
            continue
        if not base or base in seen:
            continue
        seen.append(base)
        if os.path.isdir(os.path.join(base, *subdir)):
            return base
    return None


def is_battle_track(src):
    """ゲームが渡してきたパスが戦闘曲か。区切りと大文字小文字は問わない。"""
    if not isinstance(src, str) or not src:
        return False
    return BATTLE_DIR_MARK in ("/" + src.replace("\\", "/").lstrip("/")).lower()


def coerce_weight(value):
    """重みを 0 以上の数にする。読めない値は 0。`True` は 100、`False` は 0。"""
    if isinstance(value, bool):
        return 100.0 if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number < 0:      # NaN / 負
        return 0.0
    return number


def audible(sound):
    """その Sound が今鳴っているか（1つ以上のチャンネルで）。ここが唯一の真実。"""
    try:
        return sound is not None and sound.get_num_channels() > 0
    except Exception:
        return False
