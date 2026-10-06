# -*- coding: utf-8 -*-
"""起動の段を1行で返す。ARGS: {"world": 世界の札に出る名前（の一部）}

    STAGE title     題の画面（「開始する」が見える）
    STAGE worlds    世界の一覧（ARGS の名前が見える）
    STAGE playing … 場所が読めて手が空いた。続けて画面の選択肢
    STAGE gameover  ゲームオーバーの画面
    STAGE busy      それ以外（読み込み中・本文を流している最中など）
"""


def main(say):
    a = app()
    player = getattr(a, "player", None) if a is not None else None
    loc = getattr(player, "location", None) if player is not None else None
    texts = {getattr(n, "text", None) for n in ui.walk_widgets(Window)} if a is not None else set()
    world = ARGS.get("world") or ""
    if a is not None and gameover():
        say("STAGE gameover")
    elif loc is not None and getattr(a, "world", None) is not None and idle():
        say("STAGE playing {}".format(choices()))
    elif "開始する" in texts:
        say("STAGE title")
    elif world and any(isinstance(t, str) and world in t for t in texts):
        say("STAGE worlds")
    else:
        say("STAGE busy")
