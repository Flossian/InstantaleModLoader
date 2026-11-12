# -*- coding: utf-8 -*-
r"""立ち絵の顔だけを描き直す（ADetailer と同じやり方）ための部品。

描き終えた立ち絵の顔を探し、顔の周りを正方形に切り出して拡大し、img2img で描き直して
縁をぼかした楕円で元の絵へ貼り戻す。描き直しそのものは `stable_diffusion.py` の出口がする。

**検出器のファイルを置いたときだけ動く**（同梱しない。設定画面と DOC.md にも出さない）。
どちらか1つ置けば動く。両方置けば YuNet で探し、見つからなければ lbpcascade で探す。

    state\stable_diffusion\models\face\face_detection_yunet_2023mar.onnx   YuNet
    state\stable_diffusion\models\face\lbpcascade_animeface.xml             lbpcascade
    state\stable_diffusion\face.json                                         任意。無ければ既定の値

YuNet は opencv_zoo の顔検出モデル（MIT、Copyright (c) 2020 Shiqi Yu）。
https://github.com/opencv/opencv_zoo の `models/face_detection_yunet/` から置く。
写実寄りの顔（年配・ひげ・眼鏡）も拾う。読むのは cv2 の `FaceDetectorYN`。
lbpcascade は nagadomi の lbpcascade_animeface（MIT。許諾文はファイルの頭にある）。
https://github.com/nagadomi/lbpcascade_animeface から置く。
アニメの若い顔に強く、写実寄りの顔は拾えないことが多い。
読むのはゲームに入っている OpenCV（cv2）の `CascadeClassifier`。

`face.json` の項目（どれも省ける）:

    enabled   false で止める（ファイルを消さずに切る）。既定 true
    strength  描き直しの強さ。既定 0.4（0.5 では表情や目の形が下絵から離れ始めた）
    preset    描くプリセットの名前。空なら、その立ち絵を最後に描いたプリセット
    grow      lbpcascade の顔の枠を何倍の正方形に広げて切り出すか。既定 2.2
    grow_yunet  YuNet の顔の枠を何倍に広げるか。既定 2.7（YuNet の枠は髪を含まず、
              lbpcascade の枠の一辺の約 1/1.23。同じ大きさを切り出すように合わせた）
    feather   貼るときにぼかす縁の幅（切り出しの一辺に対する割合）。既定 0.18
    side      切り出しを拡大する一辺。0 なら SDXL 1024・ほか 768

顔が見つからない絵、描き直しに失敗した絵は元の絵のまま返す。
"""
import io
import json
import os

#: 検出器のファイル名と置き場（`state\stable_diffusion\models\` の下）。
CASCADE_NAME = "lbpcascade_animeface.xml"
YUNET_NAME = "face_detection_yunet_2023mar.onnx"
CASCADE_DIR = "face"
CONFIG_NAME = "face.json"

DEFAULTS = {"enabled": True, "strength": 0.4, "preset": "", "grow": 2.2, "grow_yunet": 2.7,
            "feather": 0.18, "side": 0}

#: YuNet の確信度の下限（0.6 で 68 枚中 67 枚を拾い、残りの1枚は lbpcascade が拾った）。
YUNET_SCORE = 0.6

#: 顔の描き直しのプロンプトで外すタグ（全身を描かせる語）。小文字で比べる。
DROP_TAGS = ("full body", "full-body", "standing", "straight-on")

#: 外したタグの位置（無ければ先頭）に入れるタグ。
FACE_TAGS = "portrait, face focus, close-up"


def base_dir(state_dir):
    return os.path.join(state_dir, "stable_diffusion")


def cascade_path(state_dir):
    return os.path.join(base_dir(state_dir), "models", CASCADE_DIR, CASCADE_NAME)


def yunet_path(state_dir):
    return os.path.join(base_dir(state_dir), "models", CASCADE_DIR, YUNET_NAME)


def _number(value, fallback, low, high):
    try:
        got = float(value)
    except (TypeError, ValueError):
        return fallback
    if got != got or got <= low or got > high:
        return fallback
    return got


def config(state_dir):
    """使える状態なら設定の辞書（`cascade` と `yunet` に検出器の場所。置いていなければ None）。

    検出器が1つも無いか、切ってあれば None。
    """
    if not state_dir:
        return None
    cascade, yunet = cascade_path(state_dir), yunet_path(state_dir)
    cascade = cascade if os.path.isfile(cascade) else None
    yunet = yunet if os.path.isfile(yunet) else None
    if cascade is None and yunet is None:
        return None
    raw = {}
    path = os.path.join(base_dir(state_dir), CONFIG_NAME)
    if os.path.isfile(path):
        try:
            raw = json.load(io.open(path, encoding="utf-8")) or {}
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
    if raw.get("enabled", DEFAULTS["enabled"]) is False:
        return None
    side = raw.get("side", DEFAULTS["side"])
    return {"cascade": cascade, "yunet": yunet,
            "strength": _number(raw.get("strength"), DEFAULTS["strength"], 0.0, 1.0),
            "preset": str(raw.get("preset") or "").strip(),
            "grow": _number(raw.get("grow"), DEFAULTS["grow"], 1.0, 8.0),
            "grow_yunet": _number(raw.get("grow_yunet"), DEFAULTS["grow_yunet"], 1.0, 8.0),
            "feather": _number(raw.get("feather"), DEFAULTS["feather"], 0.0, 0.45),
            "side": int(_number(side, 0, 63, 4096)) // 64 * 64 if side else 0}


#: 読んだ検出器（置き場ごと。描くたびに読まない）。
_CLASSIFIERS = {}


def _classifier(path):
    import cv2
    got = _CLASSIFIERS.get(path)
    if got is None:
        got = cv2.CascadeClassifier(path)
        if got.empty():
            raise ValueError("検出器を読めない: {}".format(path))
        _CLASSIFIERS[path] = got
    return got


def _largest(boxes):
    x, y, w, h = max(boxes, key=lambda f: f[2] * f[3])[:4]
    return int(x), int(y), int(w), int(h)


def find_cascade(image, cascade):
    """lbpcascade でいちばん大きい顔の枠 (x, y, w, h)。見つからなければ None。

    小さい絵の顔は拾いにくいので、まず近い候補の数 4 で探し、無ければ 2 に緩める
    （比べの8枚では 4 で7枚、2 で残りの1枚が当たった）。
    """
    import cv2
    import numpy as np

    gray = cv2.equalizeHist(np.asarray(image.convert("L")))
    finder = _classifier(cascade)
    short = min(gray.shape[:2])
    least = max(24, short // 20)
    for neighbors in (4, 2):
        found = finder.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=neighbors,
                                        minSize=(least, least))
        if len(found):
            return _largest(found)
    return None


#: 読んだ YuNet（置き場ごと。寸法は描くたびに合わせる）。
_YUNETS = {}


def find_yunet(image, model):
    """YuNet でいちばん大きい顔の枠 (x, y, w, h)。見つからなければ None。"""
    import cv2
    import numpy as np

    size = (int(image.size[0]), int(image.size[1]))
    det = _YUNETS.get(model)
    if det is None:
        det = cv2.FaceDetectorYN.create(model, "", size, YUNET_SCORE, 0.3, 5000)
        _YUNETS[model] = det
    det.setInputSize(size)
    _, found = det.detect(cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR))
    if found is None or not len(found):
        return None
    return _largest(found)


def find(image, conf):
    """顔の枠と、それを広げる倍率 ((x, y, w, h), 倍率)。見つからなければ None。

    YuNet を置いていれば先に YuNet、見つからなければ lbpcascade で探す。
    """
    if conf.get("yunet"):
        box = find_yunet(image, conf["yunet"])
        if box is not None:
            return box, conf["grow_yunet"]
    if conf.get("cascade"):
        box = find_cascade(image, conf["cascade"])
        if box is not None:
            return box, conf["grow"]
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


def side_for(conf, family):
    """切り出しを拡大する一辺。設定が 0 なら系統の得意な寸法。"""
    if conf.get("side"):
        return conf["side"]
    return 1024 if family == "sdxl" else 768


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


def paste(base, face, left, top, size, feather):
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
