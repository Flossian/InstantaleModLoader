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
import json
import os
import sys
import threading
import time

try:
    from . import img2img
except ImportError:          # 道具画面（tool.py）は隣のファイルを直に import する
    import img2img

#: 設定の名前（mod.json）。`live.py` が生成のたびに読み直す。
SWITCH = "FACE_REDRAW"
PRESET = "FACE_REDRAW_PRESET"
STRENGTH = "FACE_REDRAW_STRENGTH"
#: 一括生成で、既に在る立ち絵にも顔の描き直しをかけるか（顔の描き直しが入のときだけ効く）。
EXISTING = "FACE_REDRAW_EXISTING"
NAMES = (SWITCH, PRESET, STRENGTH, EXISTING)

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
    """強さを 0 より大きく 1 以下に収める。読めなければ既定（`img2img.strength_of`）。"""
    return img2img.strength_of(value, DEFAULT_STRENGTH)


def derive(values):
    """設定の値を出口で使う形に組む。`{"on": 真偽, "preset": 名前, "strength": 強さ}`。"""
    return {"on": bool(values.get(SWITCH)),
            "preset": str(values.get(PRESET) or "").strip(),
            "strength": strength_of(values.get(STRENGTH)),
            "existing": bool(values.get(EXISTING))}


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


def cut(image, found, family):
    """見つけた顔（`find` の戻り値）の周りを正方形に切り出し、系統の得意な一辺へ拡大する。

    `(左, 上, 一辺, 拡大した一辺, 切り出した絵)`。立ち絵の顔の描き直し・既存の立ち絵の描き直し・表情差分で共通。
    """
    from PIL import Image

    box, grow = found
    left, top, size = square(box, grow, image.size[0], image.size[1])
    side = side_for(family)
    crop = image.convert("RGB").crop((left, top, left + size, top + size)).resize((side, side), Image.LANCZOS)
    return left, top, size, side, crop


def redraw_args(side, crop, strength, prompt_of=None):
    """切り出しを描き直すときに出口へ足す引数を返す関数（出口の `draw` の `extra`）。

    寸法・下絵・強さと、プロンプトを `prompt_of`（既定は `face_prompt`）で顔の寄りに直したもの。
    """
    prompt_of = prompt_of or face_prompt

    def extra(params, changes):
        got = {"width": side, "height": side, "init_image": crop, "strength": strength}
        prompt = changes.get("prompt", params.get("prompt"))
        if isinstance(prompt, str):
            got["prompt"] = prompt_of(prompt)
        return got
    return extra


# ---- 顔の描き直しを済ませた立ち絵の控え ------------------------------------------

#: 控えのファイル（`state\stable_diffusion\` の下）。`{世界: {名前: {"sha1": 元の絵の印, "result": 結果}}}`。
#: 印は描き直した後の `generated_image.png` の SHA-1（ローダの `expressions.stamp`）。
#: 印が今の絵と合う人物には、一括生成で既存の立ち絵へかけ直さない（立ち絵が描き直されたら印が外れる）。
DONE_NAME = "face_redraw_done.json"

#: ゲームの立ち絵のファイル（人物のフォルダの下。GAME.md §2.30）。背景を抜いた絵は `img2img.SOURCES`。
SMALL_NAME = "pixelated_image_original.png"
SHOWN_NAME = "reduced_color_image.png"
ICON_NAME = "face_image.png"

#: 控えに書く結果。
REDRAWN = "redrawn"
NO_FACE = "no-face"

#: 控えを書く錠の置き場（`sys` の属性。注入し直した後も、走り続ける古い世代の仕事と同じ錠を使う）。
DONE_LOCK_ATTR = "_mod343_face_done_lock"

#: 控えが在るのに読めないときのやり直し（道具画面・ウイルス対策が開いている間の一時的な失敗）。
READ_RETRIES = 5
READ_RETRY_WAIT = 0.1


def _done_lock():
    return sys.__dict__.setdefault(DONE_LOCK_ATTR, threading.Lock())


def load_done(path):
    """控えを読む。無ければ空、在るのに読めなければ None（ロック・破損）。

    読めない控えを空とみなすと、書く側はほかの人物の控えを消して書き戻し、決める側は全員を未処理と数える。
    一時的なロックに備えて短くやり直す。
    """
    for attempt in range(READ_RETRIES):
        try:
            with io.open(path, encoding="utf-8-sig") as fh:
                got = json.load(fh)
            return got if isinstance(got, dict) else None
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            if attempt + 1 < READ_RETRIES:
                time.sleep(READ_RETRY_WAIT)
    return None


def is_done(done, world, name, stamp):
    """今の元の絵（印 `stamp`）に、描き直しを済ませた（または顔が見つからなかった）控えがあるか。"""
    if not isinstance(done, dict):
        return False
    row = (done.get(world) or {}).get(name) if isinstance(done.get(world), dict) else None
    return bool(stamp) and isinstance(row, dict) and row.get("sha1") == stamp


def record_done(path, world, name, stamp, result):
    """控えに1人ぶん書く。書けたら True。

    控えが在るのに読めなければ書かない（ほかの人物の控えを消さない）。
    書き方はローダの `write_json`（ゲームの中でだけ呼ぶので、道具画面のためにここで import する）。
    """
    if not path or not world or not name or not stamp:
        return False
    from instantale_modloader import write_json
    with _done_lock():
        done = load_done(path)
        if done is None:
            return False
        rows = done.get(world) if isinstance(done.get(world), dict) else {}
        rows[name] = {"sha1": stamp, "result": result}
        done[world] = rows
        return write_json(path, done)


#: 切り出しの位置を探すときの、1画素1色あたりの二乗誤差の上限（透明な画素の色の違いが残るので 0 にしない。
#: 合う位置と合わない位置の差は桁で違う。VERIFICATION.md §3.132）。
LOCATE_ERROR = 1.0


def locate(full, piece):
    """`piece` が `full` のどこを切り出した絵か。`(左, 上)`。大きさが合わない・見つからないなら None。

    ゲームの顔アイコン（`face_image.png`）は立ち絵からの切り出しなので、描き直した立ち絵から同じ位置で切り直すのに使う。
    """
    import cv2
    import numpy as np

    big = np.asarray(full.convert("RGBA"), dtype=np.float32)
    small = np.asarray(piece.convert("RGBA"), dtype=np.float32)
    if small.shape[0] > big.shape[0] or small.shape[1] > big.shape[1] or small.size == 0:
        return None
    least, _most, at, _where = cv2.minMaxLoc(cv2.matchTemplate(big, small, cv2.TM_SQDIFF))
    if least / small.size > LOCATE_ERROR:
        return None
    return int(at[0]), int(at[1])


def replace_first(result, image):
    """出口の戻り値の1枚目を差し替えた写し（sdcpp 系は絵の一覧を返す）。"""
    if isinstance(result, list):
        return [image] + list(result[1:])
    if isinstance(result, tuple):
        return (image,) + tuple(result[1:])
    return image
