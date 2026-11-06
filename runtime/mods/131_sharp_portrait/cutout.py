# -*- coding: utf-8 -*-
r"""背景の切り抜きを isnet-anime で行う（版27）。

ゲームは立ち絵と敵の背景を rembg のサイドカー（`bin\rembg_sidecar\rembg.exe`）で抜く。
サイドカーが受け付ける引数は alpha matting の入切だけで、モデルは同梱の `u2net`（汎用）に固定。
u2net は人物の周りの飾り（後光・渦・結晶）と人物の区別がつかず、人物ごと削ることがあった
（SoteMix の立ち絵8人のうち2人で体の大半が消えた。VERIFICATION.md §3.65）。
ゲームのフォルダにはアニメ絵向けの `runtime\models\u2net\isnet-anime.onnx` があるが、使われていない。

ここではゲームに同梱の onnxruntime でそれを動かす。前処理は rembg の `DisSession` と同じ
（1024x1024 に縮め、最大値で割って平均を引く。標準偏差は 1）。
CPU で1枚 0.4〜0.6 秒（サイドカーの matting 入りは 1.8〜14 秒）。VRAM は使わない。
numpy と onnxruntime は使うときに読み込む（検査の環境に無くても import で落ちない）。
"""
import os
import threading

#: ゲームのフォルダからのモデルの場所。
MODEL_FILE = os.path.join("runtime", "models", "u2net", "isnet-anime.onnx")

#: 入力の寸法と平均（rembg の DisSession と同じ）。
SIZE = 1024
MEAN = (0.485, 0.456, 0.406)

_SESSIONS = {}
_LOCK = threading.Lock()


def model_path(game_dir):
    """isnet-anime のファイルの絶対パス。"""
    return os.path.join(game_dir or "", MODEL_FILE)


def session(path):
    """推論の部品（1つのファイルに1つ。最初の1回だけ作る）。"""
    with _LOCK:
        got = _SESSIONS.get(path)
        if got is None:
            import onnxruntime
            got = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
            _SESSIONS[path] = got
        return got


def mask(image, sess):
    """人物の濃さ（0〜255 の L 画像、元の寸法）。"""
    import numpy as np
    from PIL import Image

    small = image.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    arr = np.asarray(small).astype(np.float32)
    arr = arr / max(float(arr.max()), 1e-6)
    for channel in range(3):
        arr[:, :, channel] -= MEAN[channel]
    tensor = arr.transpose((2, 0, 1))[None]
    pred = sess.run(None, {sess.get_inputs()[0].name: tensor})[0][:, 0, :, :]
    top, bottom = float(pred.max()), float(pred.min())
    pred = (pred - bottom) / max(top - bottom, 1e-6)
    out = Image.fromarray((np.squeeze(pred) * 255).astype("uint8"), "L")
    return out.resize(image.size, Image.LANCZOS)


def cut(image, path):
    """背景を抜いた RGBA の絵。"""
    got = image.convert("RGBA")
    got.putalpha(mask(image, session(path)))
    return got
