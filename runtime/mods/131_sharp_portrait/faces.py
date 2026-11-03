# -*- coding: utf-8 -*-
"""顔の検出。ゲームの中（`sharp_portrait.py`）と外（`tool.py`）の両方が使う。

ローダも `cv2` も import しない。`cv2` / `numpy` は呼ぶ側が渡す
（ゲームの中では同梱のもの、道具では手元に入れたもの）。

ゲームの検出は OpenCV のカスケードを既定の感度（1.1 / 3）で掛けているだけで、
`lbpcascade_animeface.xml` → `haarcascade_frontalface_alt.xml` の順に2回呼ぶ
（手元の再現で 93% 一致。VERIFICATION.md §3.43）。
ここにあるのはその再現と、外れた絵を拾い直すための前処理・位置の絞り、
それより先に掛ける YuNet（OpenCV の深層学習の顔検出。版19）。

YuNet は opencv_zoo の `face_detection_yunet_2023mar.onnx`（MIT。`models/` に同梱、NOTICE）。
手元の 695 体で、カスケードが服や鎧を顔として拾う絵・外す絵の大半で顔を当て、
目の大きいアニメ寄りの顔ではカスケード（lbp）のほうが当たる。
だから YuNet を先に、外したらカスケードへ落とす（VERIFICATION.md §3.43「YuNet」）。
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

#: YuNet のモデル（MOD のフォルダからの相対）と、ログに出す名前。
YUNET_MODEL = os.path.join("models", "face_detection_yunet_2023mar.onnx")
YUNET = "YuNet"

#: YuNet の顔として採る確かさの下限。0.6 未満を見ても拾いは 651 → 661 体で、中身は見ていない。
YUNET_SCORE = 0.6


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


def read_yunet(path, np):
    """YuNet のモデルのバイト列。読めなければ None。

    OpenCV はパスに日本語があるとファイルを開けないことがあるので、
    ファイルは numpy で読み、検出器にはバイト列で渡す（`make_yunet`）。
    """
    try:
        return np.fromfile(path, dtype=np.uint8)
    except (OSError, ValueError):
        return None


def make_yunet(model, cv2, np):
    """モデルのバイト列から YuNet の検出器を作る。作れなければ None。

    1回 3ms ほどで作れるので、呼ぶ側はスレッドを跨いで使い回さずに毎回作ってよい。
    バイト列から作る形はゲームの OpenCV 4.10 にもある（`cv2.pyd` の文書文字列で確認）。
    """
    if model is None:
        return None
    try:
        return cv2.FaceDetectorYN.create("onnx", model, np.array([], dtype=np.uint8),
                                         (320, 320), YUNET_SCORE, 0.3, 50)
    except Exception:
        return None


def yunet_face(bgr, detector):
    """YuNet が見つけた顔のうち、上寄りで程よい幅の箱で一番確かなもの `((x, y, w, h), 確かさ)`。

    `bgr` は8ビット3チャネル（OpenCV の並び）。無ければ None。
    位置の絞りはカスケードと同じ（`FACE_TOP` / `FACE_WIDTH`）。
    """
    height, width = bgr.shape[:2]
    detector.setInputSize((width, height))
    _count, found = detector.detect(bgr)
    if found is None:
        return None
    fit = [f for f in found
           if (f[1] + f[3] / 2.0) / height < FACE_TOP
           and FACE_WIDTH[0] < f[2] / float(width) < FACE_WIDTH[1]]
    if not fit:
        return None
    best = max(fit, key=lambda f: f[14])
    return tuple(int(v) for v in best[:4]), float(best[14])


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
