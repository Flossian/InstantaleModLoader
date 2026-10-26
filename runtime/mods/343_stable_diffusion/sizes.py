# -*- coding: utf-8 -*-
r"""寸法とモデルの系統の計算。MOD 本体（`stable_diffusion.py`）と道具画面で共有する。

ここに在るのは**ゲームにもローダにも依らない計算だけ**で、
ゲームの読み方（どの関数がどの段か）や設定の扱いは本体が持つ。
道具画面はこれを import して、入力した値から実際の出力寸法を出す。
"""
import json
import struct


#: ゲームの値（実測。GAME.md §2.33）。設定がこれと同じなら触らない。
#: 敵・モンスターは立ち絵と違い、**1段・正方形 512x512・LCM**（実機）。
GAME_SIZE = {"portrait": (512, 1024), "enemy": (512, 512), "background": (512, 1024)}
GAME_SAMPLER = {
    "stage1": ("euler_a", 20, 8.0, "default"),
    "stage2": ("dpmpp2m", 15, 7.0, "karras"),
    "lcm": ("lcm", 5, 1.0, "default"),
}

#: 寸法はこの倍数へ丸める。
ROUND_TO = 64

#: 系統を見分ける印（safetensors のヘッダに並ぶテンソルの名前）。
#: **名前でもファイルの大きさでも見分けない。**
#: 名前は当てにならず（`AnythingXL_inkBase` は XL と付くが SD1.5）、
#: 大きさは量子化や差分で動く。中身に在る鍵で決める。
#: 実測: SD1.5 は `cond_stage_model.*` を持ち、
#: SDXL は2つ目のテキスト符号化器（`conditioner.embedders.1.*`）と
#: `model.diffusion_model.label_emb.*` を持つ。
SDXL_MARKS = ("conditioner.embedders.1", "model.diffusion_model.label_emb")
SD15_MARKS = ("cond_stage_model",)

#: ヘッダとして読む上限。safetensors は先頭8バイトがヘッダの長さで、
#: 実測では 150KB（SD1.5）〜365KB（SDXL）。桁違いなら読まない。
MAX_HEADER_BYTES = 64 * 1024 * 1024

#: 系統ごとの、学習の寸法から見て無難な画素数の範囲。**助言に使うだけ。**
#: 外れても壊れるとは限らない
#: （実機: 0.52 メガピクセルの SDXL が正しく出た。
#:  同じ日にマゼンタへ潰れた回の原因は寸法ではなくサンプラーだった）。
SD15_MAX_PIXELS = 1100000
SDXL_MIN_PIXELS = 900000

#: SDXL のときに当てるサンプラー（DLL 版の SDXL プリセットと同じ値）。
SDXL_SAMPLER = ("euler_a", 24, 5.0, "default")

#: 片辺の上限。倍率が幾つでもここで止める。
MAX_SIDE = 2048


def round_side(value, direction=0):
    """`ROUND_TO` の倍数へ丸める。上限で止める。

    `direction` は 0 で四捨五入、1 で切り上げ、-1 で切り捨て。
    安全弁は**寄せた先が範囲に入っていないと意味が無い**ので、
    上げるときは切り上げ、下げるときは切り捨てで呼ぶ。
    """
    if value <= 0:
        return value
    units = float(value) / ROUND_TO
    if direction > 0:
        steps = int(units) + (1 if units > int(units) else 0)
    elif direction < 0:
        steps = int(units)
    else:
        # 四捨五入（`round()` は 0.5 を偶数へ寄せるので、DLL 版と結果が食い違う）。
        steps = int(units + 0.5)
    return max(ROUND_TO, min(steps * ROUND_TO, MAX_SIDE))


def tensor_names(path):
    """safetensors のヘッダに並ぶテンソルの名前。読めなければ None。

    先頭8バイトがヘッダ（JSON）の長さで、その JSON の鍵がテンソルの名前。
    **本体は読まない**（実測で 150〜365KB、0.00 秒）。
    """
    try:
        with open(path, "rb") as fh:
            length = struct.unpack("<Q", fh.read(8))[0]
            if length <= 0 or length > MAX_HEADER_BYTES:
                return None
            header = fh.read(length)
        return set(json.loads(header))
    except Exception:
        return None


def family_of(path):
    """チェックポイントの中身から系統を決める。分からなければ None。"""
    names = tensor_names(path)
    if not names:
        return None
    if any(n.startswith(mark) for mark in SDXL_MARKS for n in names):
        return "sdxl"
    if any(n.startswith(mark) for mark in SD15_MARKS for n in names):
        return "sd15"
    return None


def safe_size(width, height, family):
    """その系統で無難な寸法。いまの寸法で構わなければ `None`。

    **助言のためだけの計算**で、値は動かさない（`add_safety` を参照）。
    比は変えず、画素数だけを範囲へ入れた寸法を返す。
    """
    if family is None or not isinstance(width, int) or not isinstance(height, int):
        return None
    if width <= 0 or height <= 0:
        return None
    pixels = width * height
    if family == "sdxl" and pixels < SDXL_MIN_PIXELS:
        factor = (float(SDXL_MIN_PIXELS) / pixels) ** 0.5
        direction = 1                      # 下限を割らないよう切り上げ
    elif family == "sd15" and pixels > SD15_MAX_PIXELS:
        factor = (float(SD15_MAX_PIXELS) / pixels) ** 0.5
        direction = -1                     # 上限を超えないよう切り捨て
    else:
        return None
    new = (round_side(width * factor, direction),
           round_side(height * factor, direction))
    return None if new == (width, height) else new



def size_scale(kind, short_goal, max_long):
    """その種類の倍率。ゲームの値のままなら 1.0。

    `short_goal` と `max_long` は**ゲームの最終段**（`GAME_SIZE`）に対する目標で、
    先に当たったほうで止める（DLL 版の `goal_short` / `max_long` と同じ考え方）。
    縮小はしない。
    """
    base = GAME_SIZE.get(kind)
    if base is None:
        return 1.0
    short_base, long_base = base
    by_short = float(short_goal) / short_base if short_base else 1.0
    by_long = float(max_long) / long_base if long_base else 1.0
    return max(1.0, min(by_short, by_long))


def scaled(width, height, scale):
    """縦横比を保ったまま倍率を掛ける。掛けられなければ `None`。"""
    if not isinstance(width, int) or not isinstance(height, int):
        return None
    if width <= 0 or height <= 0 or scale <= 1.0:
        return None
    limit = float(MAX_SIDE) / max(width, height)
    factor = min(float(scale), limit)
    if factor <= 1.0:
        return None
    new = (round_side(width * factor), round_side(height * factor))
    return None if new == (width, height) else new


