# -*- coding: utf-8 -*-
r"""背景の切り抜きを、ゲームのフォルダにあるモデルで行う。

ゲームは立ち絵と敵の背景を rembg のサイドカー（`bin\rembg_sidecar\rembg.exe`）で抜く。
サイドカーが受け付ける引数は alpha matting の入切だけで、モデルは同梱の `u2net`（汎用）に固定。
u2net は人物の周りの飾り（後光・渦・結晶）と人物の区別がつかず、人物ごと削ることがある。
ゲームのフォルダの `runtime\models\u2net\` にはアニメ絵向けの `isnet-anime.onnx` もあるが、使われていない。
isnet-anime は人物に強い代わりに、生き物や機械の敵を本体ごと薄くする（VERIFICATION.md §3.43）。

ここではゲームに同梱の onnxruntime で両方を動かす。前処理は rembg の各セッションと同じ。
CPU で動かし、VRAM は使わない。
numpy と onnxruntime は使うときに読み込む（検査の環境に無くても import で落ちない）。

敵は2つのモデルの濃いほうを取り、薄い所を濃くする（`merged_mask`）。
片方が見落とした本体をもう片方が拾い、半透明も残りにくい。
抜き残しの黒い地は残るが、暗い本体を欠けさせないため削らない（§3.43）。
"""
import os
import threading

#: ゲームのフォルダからのモデルの置き場。
MODEL_DIR = os.path.join("runtime", "models", "u2net")

#: モデルごとの (ファイル, 入力の一辺, 平均, 標準偏差)。rembg の DisSession / U2netSession と同じ。
ISNET = ("isnet-anime.onnx", 1024, (0.485, 0.456, 0.406), (1.0, 1.0, 1.0))
U2NET = ("u2net.onnx", 320, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225))

#: 濃くするときの下限と上限（0〜255）。下限以下は透明に、上限以上は不透明にする。
HARDEN_LOW = 24
HARDEN_HIGH = 160

_SESSIONS = {}
_LOCK = threading.Lock()


def model_path(game_dir, model=ISNET):
    """モデルのファイルの絶対パス。"""
    return os.path.join(game_dir or "", MODEL_DIR, model[0])


def session(path):
    """推論の部品（1つのファイルに1つ。最初の1回だけ作る）。"""
    with _LOCK:
        got = _SESSIONS.get(path)
        if got is None:
            import onnxruntime
            got = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
            _SESSIONS[path] = got
        return got


def mask(image, sess, model=ISNET):
    """本体の濃さ（0〜255 の L 画像、元の寸法）。"""
    import numpy as np
    from PIL import Image

    side = model[1]
    small = image.convert("RGB").resize((side, side), Image.LANCZOS)
    arr = np.asarray(small).astype(np.float32)
    arr = arr / max(float(arr.max()), 1e-6)
    out = np.zeros_like(arr)
    for channel in range(3):
        out[:, :, channel] = (arr[:, :, channel] - model[2][channel]) / model[3][channel]
    tensor = out.transpose((2, 0, 1))[None]
    pred = sess.run(None, {sess.get_inputs()[0].name: tensor})[0][:, 0, :, :]
    top, bottom = float(pred.max()), float(pred.min())
    pred = (pred - bottom) / max(top - bottom, 1e-6)
    made = Image.fromarray((np.squeeze(pred) * 255).astype("uint8"), "L")
    return made.resize(image.size, Image.LANCZOS)


def merged_mask(first, second):
    """2つの L 画像の濃いほうを取り、薄い所を濃くした L 画像。"""
    import numpy as np
    from PIL import Image

    top = np.maximum(np.asarray(first), np.asarray(second)).astype(np.float32)
    scaled = (top - HARDEN_LOW) / (HARDEN_HIGH - HARDEN_LOW)
    return Image.fromarray((np.clip(scaled, 0.0, 1.0) * 255).astype("uint8"), "L")


def cut(image, path):
    """isnet-anime で背景を抜いた RGBA の絵。"""
    got = image.convert("RGBA")
    got.putalpha(mask(image, session(path), ISNET))
    return got


def cut_merged(image, game_dir):
    """2つのモデルを合わせて背景を抜いた RGBA の絵。"""
    first = mask(image, session(model_path(game_dir, ISNET)), ISNET)
    second = mask(image, session(model_path(game_dir, U2NET)), U2NET)
    got = image.convert("RGBA")
    got.putalpha(merged_mask(first, second))
    return got
