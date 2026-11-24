# -*- coding: utf-8 -*-
"""置き先まわりの小さな部品。入口（依頼の解散）と `farewell.py`（会話の別れ）が共有する。

ゲームの形（`initial_location` の持ち方・`get_party_leave_facility` の戻り値）は GAME.md §2.8。
"""


def home_of(character):
    """雇った場所を `(エリア id, 施設 id)` で返す。読めなければ空文字の組。

    セーブでは `initial_location = {"area": "7", "node": null, "facility": "127"}`。
    辞書でない持ち方をしていても読む。
    """
    value = getattr(character, "initial_location", None)
    if isinstance(value, dict):
        return str(value.get("area") or ""), str(value.get("facility") or "")
    if value is not None:
        return (str(getattr(value, "area", "") or ""),
                str(getattr(value, "facility", "") or ""))
    return "", ""


def unpack(value):
    """`get_party_leave_facility` の答えを `(施設, ノード)` にほどく。

    実機は `(施設, ノード)` のタプルで、そのまま `move_npc_to_facility` に渡すと落ちる。
    中身は解釈せず、長さだけで分ける。
    """
    if isinstance(value, (tuple, list)):
        if len(value) >= 2:
            return value[0], value[1]
        if len(value) == 1:
            return value[0], None
        return None, None
    return value, None


def reshape(value, facility, node):
    """ゲームの答え `value` と同じ形で `(施設, ノード)` を返す。

    ゲームが答えられなかった（`None`）ときは実機の形（タプル）で返す。
    """
    if isinstance(value, tuple):
        return (facility, node) if len(value) >= 2 else (facility,)
    if isinstance(value, list):
        return [facility, node] if len(value) >= 2 else [facility]
    if value is None:
        return (facility, node)
    return facility


def character_key(character):
    """Character から id を読む。読めなければ空文字。"""
    for attr in ("id", "character_id", "npc_id"):
        found = getattr(character, attr, None)
        if isinstance(found, (str, int)):
            return str(found)
    return ""
