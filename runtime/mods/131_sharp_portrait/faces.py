# -*- coding: utf-8 -*-
"""顔の検出。ゲームの中（`sharp_portrait.py`）と外（`tool.py`）の両方が使う。

ローダも `cv2` も import しない。`cv2` / `numpy` は呼ぶ側が渡す
（ゲームの中では同梱のもの、道具では手元に入れたもの）。

ゲームの検出は OpenCV のカスケードを既定の感度（1.1 / 3）で掛けているだけで、
`lbpcascade_animeface.xml` → `haarcascade_frontalface_alt.xml` の順に2回呼ぶ
（手元の再現で 93% 一致。VERIFICATION.md §3.43）。
ここにあるのはその再現と、外れた絵を拾い直すための前処理・位置の絞り。
"""

import os

#: 顔の箱の一辺（`detect_face_coordinates` の `crop_size` の既定）。
FACE_CROP = 256

#: 顔の検出をやり直すときの前処理。座標系を変えないものだけ。
#: 順は手元の 58 体で誤検出の少なかった順。
FACE_PREPS = ("均一化", "CLAHE", "ガンマ0.6", "ぼかし3")

#: 全身の立ち絵なので、顔は上寄りで程よい幅。
#: 箱の中心の高さの上限と、幅の範囲（どちらも画像に対する比）。
FACE_TOP = 0.40
FACE_WIDTH = (0.08, 0.60)

#: ゲームのカスケードの置き場（ゲームのフォルダからの相対）と、試す順。
CASCADE_DIR = os.path.join("runtime", "models", "face_recognition")
CASCADES = ("lbpcascade_animeface.xml", "haarcascade_frontalface_alt.xml")


def short_name(cascade):
    """ログ用の短い名前。`lbpcascade_animeface.xml` → `lbp`。"""
    return os.path.basename(cascade).split("cascade")[0]


def preprocess(gray, name, cv2, np):
    """座標系を変えない前処理。`gray` は8ビット1チャネル。`None` ならそのまま。"""
    if name is None:
        return gray
    if name == "均一化":
        return cv2.equalizeHist(gray)
    if name == "CLAHE":
        return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    if name == "ガンマ0.6":
        table = np.array([((i / 255.0) ** 0.6) * 255 for i in range(256)]).astype(np.uint8)
        return cv2.LUT(gray, table)
    if name == "ぼかし3":
        return cv2.GaussianBlur(gray, (3, 3), 0)
    raise ValueError(name)


def pick_face(gray, cascade):
    """上寄りで程よい幅の箱のうち、一番上のもの `(x, y, w, h)`。無ければ None。"""
    height, width = gray.shape[:2]
    boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3)
    fit = [b for b in boxes
           if (b[1] + b[3] / 2.0) / height < FACE_TOP
           and FACE_WIDTH[0] < b[2] / float(width) < FACE_WIDTH[1]]
    if not fit:
        return None
    return tuple(int(v) for v in min(fit, key=lambda b: b[1]))


def crop_box(face, size, crop=FACE_CROP):
    """顔の箱 `(x, y, w, h)` を中心に一辺 `crop` の正方形 `(左, 上, 右, 下)`。絵の中に収める。

    ゲームの `detect_face_coordinates` が返す形と同じ
    （実機の戻り値 `(140, 0, 396, 256)` は上端で止まっている。
    端数は切り捨て: 箱 `(201, 136, 81, 81)` → 中心 241.5 → 左 113、
    `(192, 85, 117, 117)` → 中心 250.5 → 左 122。どちらも実機の戻り値と一致）。
    """
    x, y, w, h = face
    width, height = size
    left = min(max(int(x + w / 2.0 - crop / 2.0), 0), max(width - crop, 0))
    top = min(max(int(y + h / 2.0 - crop / 2.0), 0), max(height - crop, 0))
    return (left, top, left + crop, top + crop)


def detect(gray, cascades, cv2, np, preps=(None,) + FACE_PREPS):
    """前処理とカスケードを順に試し、最初に通った `(前処理, カスケード名, 箱)` を返す。

    `cascades` は `{ファイル名: cv2.CascadeClassifier}`（`CASCADES` の順に見る）。
    `preps` の先頭が `None` なら素の絵から試す（ゲームの検出の再現）。
    何も無ければ None。
    """
    for prep in preps:
        done = preprocess(gray, prep, cv2, np)
        for name in CASCADES:
            cascade = cascades.get(name)
            if cascade is None:
                continue
            box = pick_face(done, cascade)
            if box is not None:
                return prep, name, box
    return None
