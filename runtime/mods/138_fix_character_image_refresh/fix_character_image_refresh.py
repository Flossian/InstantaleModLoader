# -*- coding: utf-8 -*-
"""再生成した立ち絵と顔を、その場で表示に読み直す（DOC.md）。

ゲームは人物の絵を `characters\\<名前>\\` の決まったファイルへ上書きする。
表示している画像の `source` が同じままなので、Kivy のキャッシュが古い絵を出し続ける。
描き終わった後に、その人物のフォルダを指している HUD 上の画像を読み直す。
"""

import re

from instantale_modloader import frames, imagegen, ui


LOG_BASENAME = "character_image_refresh.log"

# 人物の絵を描く関数。画像生成の方式ごとに別のモジュールに居るが、どの方式かはローダの
# `imagegen` が吸収する（GAME.md §2.33）。
FAMILIES = imagegen.FAMILIES
FUNCS = ("generate_character_image", "generate_character_image_from_enemy")

#: ゲームが選んでいる画像生成の方式。読めなければ None（ローダの `imagegen.backend`）。
config_backend = imagegen.backend


def targets_for(backend):
    """包む対象。方式が分からなければ4つとも（入っていない方式は待つだけ）。"""
    return tuple(module + ":" + func
                 for module in imagegen.creature_modules(backend) for func in FUNCS)

# 立ち絵を出している HUD の画像。人物欄の右の立ち絵と、会話の立ち絵。
# 木のどこに居るかに頼らず名前でも引く。
PORTRAIT_WIDGETS = ("character_image_right", "character_image")

# 読み直す画像を探すとき HUD の木を何段まで降りるか（パーティー欄の顔は数段下）。
MAX_WALK_DEPTH = 16


def shows_character(source, name):
    """その画像の source が、この人物の画像フォルダの中を指しているか。"""
    if not isinstance(source, str) or not isinstance(name, str) or not name:
        return False
    parts = re.split(r"[\\/]", source)
    return len(parts) >= 3 and parts[-2] == name and parts[-3] == "characters"


def character_images(hud, name):
    """HUD の上で、この人物の画像フォルダを source に持つ画像（立ち絵・顔）。"""
    found, seen = [], set()
    stack = [(hud, 0)] + [(frames.attr(hud, attr, None), 0) for attr in PORTRAIT_WIDGETS]
    while stack:
        widget, depth = stack.pop()
        if widget is None or id(widget) in seen:
            continue
        seen.add(id(widget))
        if shows_character(frames.attr(widget, "source", None), name):
            found.append(widget)
        if depth < MAX_WALK_DEPTH:
            stack.extend((child, depth + 1) for child in ui.children_of(widget))
    return found


def reload_image(widget):
    """キャッシュを捨てて読み直す。`reload` の無い版では source を付け直す。"""
    reload = getattr(widget, "reload", None)
    if callable(reload):
        reload()
        return
    source = widget.source
    widget.source = ""
    widget.source = source


def refresh(app, name, write):
    """この人物の立ち絵と顔を読み直す。読み直した数を返す。"""
    hud = ui.find_hud(app) if app is not None else None
    if hud is None:
        return 0
    refreshed = 0
    for widget in character_images(hud, name):
        try:
            reload_image(widget)
        except Exception:
            write("could not reload {} for {}".format(type(widget).__name__, name))
            continue
        refreshed += 1
    if refreshed:
        write("reloaded {} image(s) of {}".format(refreshed, name))
    return refreshed


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="character image refresh:")
    schedule = ui.scheduler(ctx, "character image refresh")

    def install(target):
        @ctx.wrap(target, required=False, safe=True)
        def generated(orig, *args, **kwargs):
            # 描くのは背景スレッド。読み直しは描き終わった後にメインスレッドで。
            result = orig(*args, **kwargs)
            name = frames.arg(args, kwargs, "name", 1, None)
            app = ui.find_app()
            if isinstance(name, str) and name and app is not None:
                schedule(lambda: refresh(app, name, write))
            return result

    backend = config_backend()
    if backend not in FAMILIES:
        write("backend {!r} is unknown; wrapping all families".format(backend))
    for target in targets_for(backend):
        install(target)

    ctx.log("character image refresh: installed ({})".format(backend))
