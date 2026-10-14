# -*- coding: utf-8 -*-
"""画面左下のアイコンの絵を描き出す（ゲームのアイコンと同じ作り）。

    python tools\\draw_hud_icons.py          # 下の表の絵を全部書き出す
    python tools\\draw_hud_icons.py --sheet out\\hud_icons.png   # 見比べ用の一覧（8倍）も書く

作りはゲームの `Assets\\images\\icons\\interface\\dark_icon_*.png` に揃える（2026-10-13 に読んだ）:

  大きさ    32×32 のドット（2倍に拡大した粗いドットにしない）
  質感      ドットごとに明るさと色味を少しずつ散らし、平らな面を作らない
  輪郭      黒い縁取りは引かず、端のドットを暗い青紫に寄せて輪郭にする
  陰影      左上から光。影の側は青紫に寄せ、彩度は低め
  乗せた時  絵の外側に1周白い縁
  押した時  普段と同じ形を白一色で

乱数の種は絵ごとに固定なので、何度書き出しても同じ絵になる。
"""

import argparse
import math
import os
import random

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
N = 32
WHITE = (255, 255, 255, 255)
#: 影と輪郭を寄せる色（ゲームのハンマーの暗いところの色）。
SHADOW = (58, 60, 90)
NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def mix(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def clamp(c):
    return tuple(max(0, min(255, int(v))) for v in c)


class Grid:
    """各マスに (基の色, 明るさ) を持つ。"""

    def __init__(self):
        self.cells = {}

    def put(self, x, y, color, light=1.0):
        if 0 <= x < N and 0 <= y < N:
            self.cells[(x, y)] = (color, light)

    def drop(self, x, y):
        self.cells.pop((x, y), None)


SILVER = (150, 153, 174)
GOLD = (196, 152, 66)
GOLD_EDGE = (120, 82, 40)


def gear():
    """左下の MOD 用のボタン（歯車。歯は8枚で銀、右下に金のプラス）。"""
    g = Grid()
    cx, cy = 13.5, 13.5
    for y in range(N):
        for x in range(N):
            dx, dy = x - cx, y - cy
            r = math.hypot(dx, dy)
            off = abs(((math.atan2(dy, dx) + math.pi / 8) % (math.pi / 4)) - math.pi / 8)
            tooth = off < 0.27
            if r <= 3.0 or r > (11.8 if tooth else 8.8):
                continue
            light = 1.0 - 0.042 * (dx + dy)             # 左上から光（ゲームの絵と同じく、明るいのは角だけ）
            if r < 4.3:
                light *= 0.62                            # 穴の内側の縁
            g.put(x, y, SILVER, light)
    plus = ({(x, y) for x in (24, 25) for y in range(20, 30)}
            | {(x, y) for y in (24, 25) for x in range(20, 30)})
    for x, y in list(g.cells):
        if any(abs(x - px) <= 1 and abs(y - py) <= 1 for px, py in plus):
            g.drop(x, y)                                 # プラスとの間に隙間
    for x, y in plus:
        g.put(x, y, GOLD, 1.12 if (x == 24 or y == 24) else 0.86)
    return g


def flag():
    """`339_town_factions` の行の絵（旗）。"""
    g = Grid()
    pole = (150, 112, 74)
    for y in range(5, 30):
        g.put(6, y, pole, 1.1)
        g.put(7, y, pole, 0.85)
    for x, y in ((6, 3), (7, 3), (6, 4), (7, 4)):
        g.put(x, y, GOLD, 1.15 if x == 6 else 0.9)
    red = (178, 58, 54)
    for x in range(8, 28):
        t = (x - 8) / 19.0
        top = 6 + round(2.0 * math.sin(t * 2 * math.pi))
        light = 1.12 - 0.32 * (0.5 + 0.5 * math.cos(t * 2 * math.pi))
        for y in range(top, top + 13):
            g.put(x, y, red, light * (0.86 if y >= top + 11 else 1.0))
    for x, y in ((17, 10), (16, 11), (17, 11), (18, 11), (15, 12), (16, 12), (17, 12), (18, 12), (19, 12),
                 (16, 13), (17, 13), (18, 13), (17, 14)):
        g.put(x, y, GOLD, 1.0 if x <= 17 else 0.82)
    return g


def house():
    """ローダの「家と施設」の行の絵（家。赤茶の屋根・木の壁・扉と灯りの窓）。"""
    g = Grid()
    roof = (160, 74, 56)
    wall = (176, 140, 98)
    door = (104, 70, 46)
    lamp = (226, 190, 96)
    for y in range(4, 10):                        # 煙突（屋根の右の斜面から出る）
        for x in (21, 22):
            g.put(x, y, (130, 112, 104), 1.0 if x == 21 else 0.8)
    for y in range(15, 29):                       # 壁
        for x in range(8, 25):
            g.put(x, y, wall, 1.08 - 0.022 * (x - 8) - 0.012 * (y - 15))
    for y in range(4, 16):                        # 屋根（左上から光）
        half = (y - 3) * 1.05
        for x in range(int(round(16 - half)), int(round(16 + half)) + 1):
            g.put(x, y, roof, 1.18 - 0.03 * (x - 4) if x < 16 else 0.92 - 0.012 * (x - 16))
    for y in range(20, 29):                       # 扉
        for x in range(14, 19):
            g.put(x, y, door, 0.95 if x < 17 else 0.78)
    g.put(17, 24, GOLD, 1.1)                      # 扉の取っ手
    for y in range(18, 22):                       # 灯りの窓
        for x in range(9, 13):
            g.put(x, y, lamp, 1.15 if (x, y) == (9, 18) else 1.0)
    for y in range(18, 22):
        for x in range(21, 25):
            g.put(x, y, lamp, 0.95)
    return g


#: (書き出す先の頭（拡張子と _hover / _click の前まで）, 絵, 乱数の種)
ICONS = (
    (os.path.join(ROOT, "runtime", "instantale_modloader", "assets", "hud_mods"), gear, 11),
    (os.path.join(ROOT, "runtime", "mods", "339_town_factions", "icon_flag"), flag, 7),
    (os.path.join(ROOT, "runtime", "instantale_modloader", "assets", "holdings"), house, 13),
)


def render(g, seed):
    """`(普段, 乗せた時, 押した時)` の 32×32 の絵。"""
    rnd = random.Random(seed)
    shape = set(g.cells)
    edge = {p for p in shape if any((p[0] + dx, p[1] + dy) not in shape for dx, dy in NEIGHBORS)}
    ring = {(x, y) for y in range(N) for x in range(N)
            if (x, y) not in shape and any((x + dx, y + dy) in shape for dx, dy in NEIGHBORS)}
    normal = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    for (x, y), (base, light) in sorted(g.cells.items()):
        light *= 1.0 + rnd.uniform(-0.09, 0.09)          # 明るさを散らす
        color = mix(base, SHADOW, max(0.0, min(0.75, (1.0 - light) * 1.4)))   # 暗いほど青紫へ
        color = tuple(v * min(light, 1.25) for v in color)
        color = tuple(v + rnd.uniform(-9, 9) for v in color)                  # 色味を散らす
        if (x, y) in edge:
            color = mix(clamp(color), GOLD_EDGE if base == GOLD else SHADOW, 0.55)   # 輪郭は暗く
        normal.putpixel((x, y), clamp(color) + (255,))
    hover = normal.copy()
    for p in ring:
        hover.putpixel(p, WHITE)
    click = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    for p in shape:
        click.putpixel(p, WHITE)
    return normal, hover, click


def main():
    ap = argparse.ArgumentParser(description="画面左下のアイコンの絵を書き出す")
    ap.add_argument("--sheet", help="見比べ用の一覧（8倍）の書き出し先")
    args = ap.parse_args()
    drawn = []
    for head, fn, seed in ICONS:
        os.makedirs(os.path.dirname(head), exist_ok=True)
        images = render(fn(), seed)
        for suffix, image in zip(("", "_hover", "_click"), images):
            path = head + suffix + ".png"
            image.save(path)
            print(path)
        drawn.append(images)
    if args.sheet:
        sheet = Image.new("RGBA", ((N * 8 + 20) * 3, (N * 8 + 20) * len(drawn)), (8, 8, 10, 255))
        for row, images in enumerate(drawn):
            for col, image in enumerate(images):
                big = image.resize((N * 8, N * 8), Image.NEAREST)
                sheet.paste(big, (col * (N * 8 + 20), row * (N * 8 + 20)), big)
        sheet.save(args.sheet)
        print(args.sheet)


if __name__ == "__main__":
    main()
