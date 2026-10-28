# -*- coding: utf-8 -*-
r"""今の絵を下絵にした描き直し（img2img）と、2つのモデルで続けて描く（txt2img→img2img）の部品。

出口（`stable_diffusion.py` の `make_exit_hook`）と入口の包みから使う。
ここに置くのはゲームに触らない計算だけ（設定の読み方・今の絵の置き場・下絵の形・描き方の決め方）。

    描き直し       使う設定（`IMG2IMG`）が入なら、再生成のボタンに img2img が出る。今の絵を下絵にして描く
    続けて描く     使う設定（`CHAIN`）が入なら、再生成のボタンに出る。1回目をプリセット A で描き、
                   その絵を下絵にプリセット B で描き直す。種類ごとに通常の生成でも使うかを選べる

どちらも sdcpp 系の出口（`init_image` と `strength` を持つ）だけで効く。
diffusers 系の出口には下絵の口が無い（GAME.md §2.33）。
ゲームの2段目（`image_to_image_anime`）は自分で下絵を渡しているので触らない。
今の絵の置き場と出口の下絵の寸法は GAME.md §2.30、確かめは VERIFICATION.md §3.65。
"""
import os

KINDS = ("portrait", "enemy", "background")

#: 設定名の種類の部分（`IMG2IMG_PORTRAIT_PRESET` など）。
PREFIX = {"portrait": "PORTRAIT", "enemy": "ENEMY", "background": "BACKGROUND"}

#: 種類ごとの今の絵。世界のフォルダの下の置き場と、背景を抜いた後の絵のファイル名。
SOURCES = {"portrait": ("characters", "no_bg_image.png"),
           "enemy": ("monsters", "base_image.png"),
           "background": ("backgrounds", "image.png")}

#: 今の絵を探す入口。関数名 → (種類, 名前の引数, その位置)。世界の名前はどれも最初の引数。
#: 敵と立ち絵を互いの絵から描き起こす入口（`*_from_*`）は、ゲームが自分で元の絵を使うので入れない。
ENTRANCES = {"generate_character_image": ("portrait", "name", 1),
             "generate_enemy_image": ("enemy", "name", 2),
             "generate_background_image": ("background", "location", 1),
             "generate_and_save_background": ("background", "location", 1)}

#: 描き直しと続けて描くの強さの既定（mod.json と同じ値）。
#: 描き直しは元の絵に最も近かった 0.5、続けて描くは2つ目のモデルの描き方を少し強めた 0.6。
REDRAW_STRENGTH = 0.5
CHAIN_STRENGTH = 0.6


#: 描き直し（img2img）と続けて描く（txt2img→img2img）を使うかどうか。どちらも入なら、
#: 再生成のボタン（設定の画面と主人公の一覧）にその描き方が出る。種類ごとの欄はその下に出る。
IMG2IMG_SWITCH = "IMG2IMG"
CHAIN_SWITCH = "CHAIN"


def redraw_names(kind):
    """描き直しの種類ごとの設定名（プリセット, 強さ）。"""
    head = "IMG2IMG_" + PREFIX[kind]
    return head + "_PRESET", head + "_STRENGTH"


def chain_names(kind):
    """続けて描くの種類ごとの設定名（1回目のプリセット, 2回目のプリセット, 強さ）。"""
    head = "CHAIN_" + PREFIX[kind]
    return head + "_FIRST", head + "_SECOND", head + "_STRENGTH"


def chain_auto_name(kind):
    """続けて描くを、ボタンを通らない通常の生成（新しい NPC・場所・敵）でも使うかの設定名。"""
    return "CHAIN_" + PREFIX[kind] + "_AUTO"


#: ここで読む設定名すべて（`live.py` が生成のたびに読み直す）。
NAMES = (IMG2IMG_SWITCH,) + tuple(name for kind in KINDS for name in redraw_names(kind)) + \
    (CHAIN_SWITCH,) + tuple(name for kind in KINDS for name in chain_names(kind) + (chain_auto_name(kind),))


def strength_of(value, fallback):
    """強さを 0 より大きく 1 以下に収める。読めなければ既定。"""
    try:
        got = float(value)
    except (TypeError, ValueError):
        return fallback
    if got != got or got <= 0:              # NaN と 0 以下
        return fallback
    return min(got, 1.0)


def derive(values):
    """設定の値を、出口と再生成のボタンで使う形に組む。

    `{"img2img_on": 真偽, "chain_on": 真偽, "redraw": {種類: {"preset", "strength"}},
    "chain": {種類: {"first", "second", "strength"}}, "chain_auto": 通常の生成でも続けて描く種類の集合}`。
    `redraw` と `chain` は全種類（どの種類を描き直すかはゲームが決める）。
    img2img に通常の生成の道は無い（今の絵を下絵にするのは再生成だけ）。
    `chain_auto` は、使う設定（`CHAIN`）が切なら種類ごとの入切が残っていても空。
    """
    got = {"img2img_on": bool(values.get(IMG2IMG_SWITCH)), "chain_on": bool(values.get(CHAIN_SWITCH)),
           "redraw": {}, "chain": {}, "chain_auto": set()}
    for kind in KINDS:
        preset, strength = redraw_names(kind)
        got["redraw"][kind] = {"preset": str(values.get(preset) or "").strip(),
                               "strength": strength_of(values.get(strength), REDRAW_STRENGTH)}
        first, second, strength = chain_names(kind)
        got["chain"][kind] = {"first": str(values.get(first) or "").strip(),
                              "second": str(values.get(second) or "").strip(),
                              "strength": strength_of(values.get(strength), CHAIN_STRENGTH)}
        if got["chain_on"] and values.get(chain_auto_name(kind)):
            got["chain_auto"].add(kind)
    return got


def enabled(settings):
    """再生成のボタンに出す描き方（txt2img のほかに。使う設定が入のものだけ）。"""
    return (["img2img"] if settings["img2img_on"] else []) + (["chain"] if settings["chain_on"] else [])


def name_of(value):
    """入口の名前の引数を、フォルダの名前へ。文字列でなければ `name` の属性を見る。"""
    if isinstance(value, str):
        return value
    got = getattr(value, "name", None)
    return got if isinstance(got, str) else None


def source_path(worlds_dir, kind, world, name):
    """今の絵の場所（在るかどうかは見ない）。組めなければ None。"""
    name = name_of(name)
    if kind not in SOURCES or not worlds_dir or not isinstance(world, str) or not world or not name:
        return None
    folder, filename = SOURCES[kind]
    return os.path.join(worlds_dir, world, folder, name, filename)


def plan(kind, stage, params, settings, has_source, forced=None):
    """この1回の描き方。`"redraw"`・`"chain"`・None（ふだんどおり）。

    `params` は出口の引数（名前で引ける形）。`settings` は `derive` の戻り値。
    下絵の口（`init_image` と `strength`）が無い出口と、もう下絵が渡っている回
    （ゲームの2段目や、敵と立ち絵を互いの絵から描き起こす回）は触らない。
    `forced` は再生成のボタンで選んだ描き方（`txt2img` / `img2img` / `chain`）。
    描き直しはボタンで選んだ回だけ。
    選んでいなければ、続けて描くの「通常の生成でも使う」が入の種類だけ続けて描く。
    """
    if kind not in KINDS or stage == "stage2":
        return None
    if "init_image" not in params or "strength" not in params:
        return None
    if params.get("init_image") is not None:
        return None
    if forced == "txt2img":
        return None
    if forced == "img2img":
        return "redraw" if has_source else None
    if forced == "chain":
        return "chain"
    if kind in settings["chain_auto"]:
        return "chain"
    return None


def conf_for(settings, mode, kind):
    """その種類の描き方の設定（プリセットと強さ）。"""
    return settings["redraw" if mode == "redraw" else "chain"][kind]


#: 設定の画面の再生成のボタンの文字（ゲームの「画像の再生成」を描き方ごとに分ける）。
BUTTON_LABELS = {"txt2img": "画像の再生成(txt2img)",
                 "img2img": "画像の再生成(img2img)",
                 "chain": "画像の再生成(txt2img→img2img)"}

#: ボタンを足したときの並べ方（上下の余白・ボタンの高さ・間）。
#: ゲームは 62.5 / 100 / 50 で3つ並べ、枠の中心は画面の高さの 0.68 にある。
#: 同じ並べ方で5つにすると枠の上が画面からはみ出すので詰める。
STACK_MARGIN = 50
STACK_HEIGHT = 80
STACK_GAP = 25


def stack(count, margin=STACK_MARGIN, height=STACK_HEIGHT, gap=STACK_GAP):
    """ボタンを縦に並べるときの (枠の高さ, 上から順の各ボタンの枠の上端からの下端の距離)。"""
    total = 2 * margin + count * height + max(count - 1, 0) * gap
    bottoms = [margin + (index + 1) * height + index * gap for index in range(count)]
    return total, bottoms


def load_image(path):
    """今の絵を読んで写しを返す（ファイルを掴んだままにしない）。"""
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        return image.copy()


def as_init(image, width, height):
    """下絵の形へ。透過は黒地に置き、出口が描く寸法に合わせる。

    ゲームは立ち絵と敵を黒地で描かせ（`black background`）、背景を抜いた絵を残す。
    寸法が出口の `width` / `height` と違うと、ネイティブの側で落ちる（GAME.md §2.33）。
    """
    from PIL import Image

    got = image
    if got.mode in ("RGBA", "LA", "PA") or (got.mode == "P" and "transparency" in got.info):
        rgba = got.convert("RGBA")
        back = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        back.alpha_composite(rgba)
        got = back
    got = got.convert("RGB")
    if got.size != (width, height):
        got = got.resize((width, height), Image.LANCZOS)
    return got


def first_image(result):
    """出口の戻り値から絵を1枚。sdcpp 系は絵の一覧を返す。絵でなければ None。"""
    if isinstance(result, (list, tuple)):
        result = result[0] if result else None
    return result if hasattr(result, "size") and hasattr(result, "convert") else None
