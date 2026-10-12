# -*- coding: utf-8 -*-
"""338_town_incidents の窓のアイコン（注意の標識）を描く。開発用。

    python tools/make_town_incidents_icon.py

ゲームの左下のアイコン（32×32 のドット絵。くすんだ色・暗い縁・陰影）に揃えて、
黄色の三角に黒い縁と感嘆符の標識（JIS Z 8210 の「一般注意」の形）を描く。
出すのは普段・乗せた時・押した時の3枚（ゲームの `dark_icon_*` / `_hover` / `_click` と同じ組）。
乗せた時は色の部分にじかに接する白い縁、押した時は形全体を白くする（ゲームの3枚と同じ作り）。

絵は 32×32 で描き、書き出す前に 40×40（ゲームのボタンの大きさ）へ自分で引き伸ばす。
ゲームは 32×32 の絵を 40px のボタンへ近いドットで引き伸ばし、4列に1列（0・4・8…番）を 2px にする。
その並びは中心に対して左右対称でないので、左右対称の標識がゲームの中では崩れて見えた。
こちらで太らせる列を中心に対して左右対称に選んでおけば（`DOUBLED`）、ゲームは 40×40 をそのまま描く。
太る列の数はゲームの絵と同じ8列なので、ドットの粒の大きさは変わらない。
"""
import os
import random

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime", "mods", "338_town_incidents"))
SIZE = 32
#: 書き出す大きさ（ゲームの左下のボタンの大きさ。GAME.md §2.34）。
OUT_SIZE = 40
#: 2px にする列（行も同じ）。中心（15 と 16 の境目）に対して左右対称な8つ。
DOUBLED = (1, 5, 9, 13, 18, 22, 26, 30)

OUTLINE = (38, 28, 24)
TOP = (236, 196, 84)
MID = (214, 168, 58)
LOW = (178, 130, 40)
MARK = (44, 32, 26)
SHINE = (250, 226, 150)

#: 三角の外形と、黒い縁の内側。三角は上へ行くほど細く、四角い絵より細く見えるので、底辺を絵の幅近くまで広げる。
#: 外形は乗せた時の白い縁の1ドットを外に残す。黒い縁は1ドット（乗せた時に白へ替わる）。
OUTER = ((16.0, 1.0), (0.5, 30.4), (31.5, 30.4))
INNER = ((16.0, 3.4), (2.6, 29.3), (29.4, 29.3))

#: むらの強さ。ゲームの絵はほぼ全部のドットが違う色で、隣り合うドットの色の差（r+g+b）の中央値が 29〜30。
#: これより弱いと、ドットの粒がゲームの絵より細かく見えた。
GRAIN_SCALE = (0.9, 1.1)
GRAIN_CHANNEL = 5
GRAIN_DARK = 9


def inside(x, y, apex, left, right):
    """点 (x+0.5, y+0.5) が三角の中か。"""
    px, py = x + 0.5, y + 0.5

    def side(a, b):
        return (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])

    s1, s2, s3 = side(apex, left), side(left, right), side(right, apex)
    return (s1 <= 0 and s2 <= 0 and s3 <= 0) or (s1 >= 0 and s2 >= 0 and s3 >= 0)


def is_border(x, y):
    """黒い縁のドットか（外形の内側で、縁の内側の三角の外）。"""
    return inside(x, y, *OUTER) and not inside(x, y, *INNER)


def draw():
    """普段の絵。影は付けない（ゲームの絵にも影は無く、半透明のドットも無い）。"""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    px = image.load()
    rng = random.Random(918)          # むらは毎回同じ（描き直しても絵が変わらない）

    def light(color):
        """明るい面。ドットごとに明るさと色味を揺らす。"""
        k = rng.uniform(*GRAIN_SCALE)
        return tuple(max(0, min(255, int(value * k + rng.randint(-GRAIN_CHANNEL, GRAIN_CHANNEL))))
                     for value in color) + (255,)

    def dark(color):
        """暗い縁と感嘆符。掛け算では揺れが小さいので足し算で揺らす。"""
        shift = rng.randint(-GRAIN_DARK, GRAIN_DARK)
        return tuple(max(0, min(255, value + shift + rng.randint(-3, 3))) for value in color) + (255,)

    for y in range(SIZE):
        for x in range(SIZE):
            if is_border(x, y):
                px[x, y] = dark(OUTLINE)
            elif inside(x, y, *INNER):
                band = (y - 3) / 27.0
                px[x, y] = light(TOP if band < 0.35 else MID if band < 0.75 else LOW)
    # 左の縁の光
    for y in range(8, 26):
        x = int(16 - (y - 3.0) * (14.0 / 26.5)) + 1
        if 0 <= x < SIZE and inside(x, y, *INNER):
            px[x, y] = light(SHINE)
    # 感嘆符。三角の中心（15 と 16 のドットの境目）に左右対称に置く（縦棒も点も2ドット幅）。
    # 3ドット幅にすると中心から半ドットずれ、下で細らせた所が欠けて見えた。
    # 棒は上を4ドット、下を2ドットにして先細りにする（2ドットのままだと、ゲームの大きさでは細く見えた）。
    # 点は棒の上の太さに揃えて 4×3（2×2 では小さすぎた）。棒との間は2ドット空ける。
    for y in range(10, 20):
        for x in ((14, 15, 16, 17) if 11 <= y <= 16 else (15, 16)):
            px[x, y] = dark(MARK)
    for y in (22, 23, 24):
        for x in (14, 15, 16, 17):
            px[x, y] = dark(MARK)
    return image


WHITE = (255, 255, 255, 255)


def hover(image):
    """乗せた時。白い縁を色の部分にじかに接して付ける（ゲームの `_hover` と同じ見え方）。

    標識の外側の黒い縁を白に塗り替え、そのさらに外の透明なドットにも、斜めを含めて白を1ドット足す。
    黒い縁の外に白を付けるだけだと、白と黄色の間の黒い縁が隙間に見えた。
    縁のドットは色ではなく形で見分ける（縁の色もむらで揺れている）。
    """
    out = image.copy()
    src, dst = image.load(), out.load()

    def solid(x, y):
        return 0 <= x < SIZE and 0 <= y < SIZE and src[x, y][3] >= 200

    around4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    around8 = around4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))
    for y in range(SIZE):
        for x in range(SIZE):
            if solid(x, y):
                if is_border(x, y) and any(not solid(x + dx, y + dy) for dx, dy in around4):
                    dst[x, y] = WHITE
            elif any(solid(x + dx, y + dy) for dx, dy in around8):
                dst[x, y] = WHITE
    return out


def click(image):
    """形を白い影絵にする。"""
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    src, dst = image.load(), out.load()
    for y in range(SIZE):
        for x in range(SIZE):
            if src[x, y][3] >= 200:
                dst[x, y] = WHITE
    return out


def upscale(image):
    """32×32 を 40×40 へ、`DOUBLED` の列と行を2つにして引き伸ばす（ドットはにじませない）。"""
    order = []
    for index in range(SIZE):
        order.extend([index, index] if index in DOUBLED else [index])
    assert len(order) == OUT_SIZE
    out = Image.new("RGBA", (OUT_SIZE, OUT_SIZE), (0, 0, 0, 0))
    src, dst = image.load(), out.load()
    for y, sy in enumerate(order):
        for x, sx in enumerate(order):
            dst[x, y] = src[sx, sy]
    return out


def main():
    base = draw()
    for suffix, image in (("", base), ("_hover", hover(base)), ("_click", click(base))):
        path = os.path.join(OUT_DIR, "icon_caution{}.png".format(suffix))
        upscale(image).save(path)
        print(path)


if __name__ == "__main__":
    main()
