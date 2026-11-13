# -*- coding: utf-8 -*-
"""立ち絵の顔だけを描き直す（高度設定の「顔だけ描き直す」）ための部品。DOC.md「顔だけ描き直す」。

描き終えた立ち絵の顔を探し、顔の周りを正方形に切り出して拡大し、img2img で描き直して
縁をぼかした楕円で元の絵へ貼り戻す。描き直しそのものは `stable_diffusion.py` の出口がする。

検出器は `models/` に同梱する。YuNet で探し、見つからなければ lbpcascade で探す。

    models/face_detection_yunet_2023mar.onnx   YuNet（opencv_zoo、MIT、Copyright (c) 2020 Shiqi Yu。
                                               許諾文は同じフォルダの LICENSE.yunet.txt、NOTICE）
    models/lbpcascade_animeface.xml            lbpcascade_animeface（nagadomi、MIT。許諾文はファイルの頭）

YuNet は写実寄りの顔（年配・ひげ・眼鏡）も拾う。lbpcascade はアニメの若い顔に強い。
どちらもゲームに入っている OpenCV（cv2）で読む。OpenCV はパスに日本語があるとファイルを
開けないので、中身を読んでから渡す（131_sharp_portrait の `faces.read_yunet` と同じ）。
"""
import io
import os

#: 設定の名前（mod.json）。`live.py` が生成のたびに読み直す。
SWITCH = "FACE_REDRAW"
PRESET = "FACE_REDRAW_PRESET"
STRENGTH = "FACE_REDRAW_STRENGTH"
NAMES = (SWITCH, PRESET, STRENGTH)

#: 強さの既定（mod.json と同じ値）。0.5 では表情や目の形が下絵から離れ始めた。
DEFAULT_STRENGTH = 0.4

#: 検出器のファイル（この MOD のフォルダの下）。
MODELS_DIR = "models"
CASCADE_NAME = "lbpcascade_animeface.xml"
YUNET_NAME = "face_detection_yunet_2023mar.onnx"

#: 顔の枠を何倍の正方形に広げて切り出すか。YuNet の枠は髪を含まず、lbpcascade の枠の
#: 一辺の約 1/1.23 なので、同じ大きさを切り出すように倍率を分けた。
GROW_CASCADE = 2.2
GROW_YUNET = 2.7

#: 貼るときにぼかす縁の幅（切り出しの一辺に対する割合）。
FEATHER = 0.18

#: 切り出しを拡大する一辺（系統の得意な寸法）。
SIDE_SDXL = 1024
SIDE_OTHER = 768

#: YuNet の確信度の下限（0.6 で 68 枚中 67 枚を拾い、残りの1枚は lbpcascade が拾った）。
YUNET_SCORE = 0.6

#: 顔の描き直しのプロンプトで外すタグ（全身を描かせる語）。小文字で比べる。
DROP_TAGS = ("full body", "full-body", "standing", "straight-on")

#: 外したタグの位置（無ければ先頭）に入れるタグ。
FACE_TAGS = "portrait, face focus, close-up"


def models_dir(mod_dir):
    return os.path.join(mod_dir, MODELS_DIR) if mod_dir else ""


def cascade_path(models):
    return os.path.join(models, CASCADE_NAME)


def yunet_path(models):
    return os.path.join(models, YUNET_NAME)


def strength_of(value):
    """強さを 0 より大きく 1 以下に収める。読めなければ既定。"""
    try:
        got = float(value)
    except (TypeError, ValueError):
        return DEFAULT_STRENGTH
    if got != got or got <= 0:              # NaN と 0 以下
        return DEFAULT_STRENGTH
    return min(got, 1.0)


def derive(values):
    """設定の値を出口で使う形に組む。`{"on": 真偽, "preset": 名前, "strength": 強さ}`。"""
    return {"on": bool(values.get(SWITCH)),
            "preset": str(values.get(PRESET) or "").strip(),
            "strength": strength_of(values.get(STRENGTH))}


def config(settings, models):
    """使えるなら、設定に検出器の場所（`cascade`・`yunet`。無ければ None）を足した辞書。

    切ってあるか、検出器が1つも無ければ None。
    """
    if not settings.get("on") or not models:
        return None
    cascade, yunet = cascade_path(models), yunet_path(models)
    cascade = cascade if os.path.isfile(cascade) else None
    yunet = yunet if os.path.isfile(yunet) else None
    if cascade is None and yunet is None:
        return None
    return dict(settings, cascade=cascade, yunet=yunet)


#: 読んだ検出器のファイルの中身（置き場ごと）。検出器そのものは毎回作る（どちらも 3ms ほど。
#: 生成はスレッドを跨ぐので、作った検出器を使い回さない）。
_CONTENTS = {}


def _content(path, binary):
    got = _CONTENTS.get(path)
    if got is None:
        if binary:
            import numpy as np
            got = np.fromfile(path, dtype=np.uint8)
        else:
            with io.open(path, encoding="utf-8") as fh:
                got = fh.read()
        _CONTENTS[path] = got
    return got


def _largest(boxes):
    x, y, w, h = max(boxes, key=lambda f: f[2] * f[3])[:4]
    return int(x), int(y), int(w), int(h)


def make_cascade(path):
    """lbpcascade の検出器（ファイルの中身から作る）。"""
    import cv2
    storage = cv2.FileStorage(_content(path, False), cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
    got = cv2.CascadeClassifier()
    if not got.read(storage.getFirstTopLevelNode()) or got.empty():
        raise ValueError("検出器を読めない: {}".format(path))
    return got


def make_yunet(path, size):
    """YuNet の検出器（ファイルの中身から作る）。"""
    import cv2
    import numpy as np
    return cv2.FaceDetectorYN.create("onnx", _content(path, True), np.array([], dtype=np.uint8),
                                     size, YUNET_SCORE, 0.3, 5000)


def find_cascade(image, path):
    """lbpcascade でいちばん大きい顔の枠 (x, y, w, h)。見つからなければ None。

    小さい絵の顔は拾いにくいので、まず近い候補の数 4 で探し、無ければ 2 に緩める
    （比べの8枚では 4 で7枚、2 で残りの1枚が当たった）。
    """
    import cv2
    import numpy as np

    gray = cv2.equalizeHist(np.asarray(image.convert("L")))
    finder = make_cascade(path)
    short = min(gray.shape[:2])
    least = max(24, short // 20)
    for neighbors in (4, 2):
        found = finder.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=neighbors,
                                        minSize=(least, least))
        if len(found):
            return _largest(found)
    return None


def find_yunet(image, path):
    """YuNet でいちばん大きい顔の枠 (x, y, w, h)。見つからなければ None。"""
    import cv2
    import numpy as np

    det = make_yunet(path, (int(image.size[0]), int(image.size[1])))
    _, found = det.detect(cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR))
    if found is None or not len(found):
        return None
    return _largest(found)


def find(image, conf):
    """顔の枠と、それを広げる倍率 ((x, y, w, h), 倍率)。見つからなければ None。

    YuNet があれば先に YuNet、見つからなければ lbpcascade で探す。
    """
    if conf.get("yunet"):
        box = find_yunet(image, conf["yunet"])
        if box is not None:
            return box, GROW_YUNET
    if conf.get("cascade"):
        box = find_cascade(image, conf["cascade"])
        if box is not None:
            return box, GROW_CASCADE
    return None


def square(box, grow, width, height):
    """顔の枠を grow 倍の正方形へ広げ、絵の中へ収める。(左, 上, 一辺)。"""
    x, y, w, h = box
    side = int(round(max(w, h) * grow))
    side = max(1, min(side, width, height))
    cx, cy = x + w / 2.0, y + h / 2.0
    left = int(round(min(max(cx - side / 2.0, 0), width - side)))
    top = int(round(min(max(cy - side / 2.0, 0), height - side)))
    return left, top, side


def side_for(family):
    """切り出しを拡大する一辺。"""
    return SIDE_SDXL if family == "sdxl" else SIDE_OTHER


def face_prompt(prompt):
    """全身を描かせるタグを外し、顔の寄りのタグを入れる。外すタグが無ければ先頭に入れる。"""
    if not isinstance(prompt, str):
        return prompt
    tags = [tag.strip() for tag in prompt.split(",")]
    kept, at = [], None
    for tag in tags:
        if tag.lower() in DROP_TAGS:
            if at is None:
                at = len(kept)
            continue
        kept.append(tag)
    kept.insert(0 if at is None else at, FACE_TAGS)
    return ", ".join(tag for tag in kept if tag)


def paste(base, face, left, top, size, feather=FEATHER):
    """描き直した顔を元の大きさに戻し、縁をぼかした楕円で貼った写しを返す（元の絵は触らない）。"""
    from PIL import Image, ImageDraw, ImageFilter

    out = base.copy()
    patch = face.convert(out.mode).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    pad = int(size * feather)
    ImageDraw.Draw(mask).ellipse((pad, pad, size - pad, size - pad), fill=255)
    if pad > 1:
        mask = mask.filter(ImageFilter.GaussianBlur(pad / 2.0))
    out.paste(patch, (left, top), mask)
    return out


def replace_first(result, image):
    """出口の戻り値の1枚目を差し替えた写し（sdcpp 系は絵の一覧を返す）。"""
    if isinstance(result, list):
        return [image] + list(result[1:])
    if isinstance(result, tuple):
        return (image,) + tuple(result[1:])
    return image
