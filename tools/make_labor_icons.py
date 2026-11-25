# -*- coding: utf-8 -*-
"""925_labor_overhaul の闇労働のアイコンを、生成 AI の一枚絵から切り出して書く。

    python tools/make_labor_icons.py

一枚絵は仕事の種類ごとに1枚（2x2 の4場面を黒い隙間で区切ったもの）で、`local/labor_icons/sheet_<種類>.png` に置く
（大きいので git の外。共有リンクから取った原寸）。黒い隙間を探して4枚に切り、`ICON_SIZE` に縮めて
`runtime/mods/925_labor_overhaul/icons/<種類>_<番号>.png` に書く。番号は 左上・右上・左下・右下 の順に 1〜4。
"""
import os
import sys

from PIL import Image, ImageStat

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
SOURCE = os.path.join(ROOT, "local", "labor_icons")
OUT = os.path.join(ROOT, "runtime", "mods", "925_labor_overhaul", "icons")
#: 札の欄は約 132px（拡縮の倍率で 1.5 倍まで）。縮めて置く大きさ。
ICON_SIZE = 256
#: 隙間とみなす明るさの平均（0〜255）。
GAP_LEVEL = 14
#: 一枚絵の名前（`sheet_<名前>.png`）と、仕事の種類のキー（rules.ILLEGAL_JOBS の key）。
SHEETS = (("courier2", "courier"), ("lookout", "lookout"), ("collector", "collector"),
          ("insider", "insider"), ("breakin", "breakin"))


def runs(indexes):
    found = []
    for index in indexes:
        if found and index == found[-1][1] + 1:
            found[-1][1] = index
        else:
            found.append([index, index])
    return found


def gaps(gray, axis):
    width, height = gray.size
    count = width if axis == 0 else height
    dark = [i for i in range(count)
            if ImageStat.Stat(gray.crop((i, 0, i + 1, height) if axis == 0 else (0, i, width, i + 1))).mean[0]
            < GAP_LEVEL]
    return runs(dark)


def edges(found, size):
    """外の縁の内側と、真ん中の隙間の両端。"""
    start = found[0][1] + 1 if found and found[0][0] == 0 else 0
    end = found[-1][0] if found and found[-1][1] == size - 1 else size
    middle = min(found, key=lambda r: abs((r[0] + r[1]) / 2.0 - size / 2.0))
    return start, middle[0], middle[1] + 1, end


def tiles(path):
    image = Image.open(path).convert("RGB")
    gray = image.convert("L")
    left, mid_l, mid_r, right = edges(gaps(gray, 0), image.size[0])
    top, mid_t, mid_b, bottom = edges(gaps(gray, 1), image.size[1])
    boxes = [(left, top, mid_l, mid_t), (mid_r, top, right, mid_t),
             (left, mid_b, mid_l, bottom), (mid_r, mid_b, right, bottom)]
    return [image.crop(box) for box in boxes]


def main():
    os.makedirs(OUT, exist_ok=True)
    missing = []
    for sheet, key in SHEETS:
        path = os.path.join(SOURCE, "sheet_{}.png".format(sheet))
        if not os.path.isfile(path):
            missing.append(path)
            continue
        for number, tile in enumerate(tiles(path), 1):
            side = min(tile.size)
            square = tile.crop(((tile.size[0] - side) // 2, (tile.size[1] - side) // 2,
                                (tile.size[0] + side) // 2, (tile.size[1] + side) // 2))
            out = os.path.join(OUT, "{}_{}.png".format(key, number))
            square.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS).save(out, optimize=True)
            print("wrote", os.path.relpath(out, ROOT))
    if missing:
        print("missing:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
