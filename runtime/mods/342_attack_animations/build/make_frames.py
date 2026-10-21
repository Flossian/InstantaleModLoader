# -*- coding: utf-8 -*-
"""攻撃の演出の絵（コマ送りの PNG）を描き直す。

    py -3.13 runtime/mods/342_attack_animations/build/make_frames.py [--sheet 見本の置き場所] [--only 名前 …]

一度きりのビルド用で、実行時には読まれない（実行時に読むのは `../effects/` だけ）。
要るのは Pillow と numpy（ゲームの Python ではなく、手元の Python で走らせる）。

ゲームの物理の演出（`Assets/effects/battle_dark_/slash1` と `blow`）に揃えてある。
1体への演出は 64x64、全体への演出は 128x128 の RGBA を 10〜16 コマ、光の筋と粒だけで描き、
名前は `frame_000.png` から。ゲームは最近傍で拡大するので、どちらも画面ではドット絵のように出る。
描き方は「光を足し合わせる」形で、最後に明るさを不透明度へ直す
（暗い背景の上でゲームの絵と同じように光って見える）。
8倍の大きさで描いてから縮めるので、縁は滑らかになる。

画風は2つ描く（`STYLES`）。`soft` は形を小さく線を細くして明るさを抑えた版、
`bright` ははっきり光る版。書き出し先は `../effects/<画風>/<名前>/`。
どちらを使うかは MOD の設定（`BRIGHT_ART`）で選ぶ。

`--sheet` を渡すと、画風ごとに全部のコマを並べた見本を書く（確かめる用）。
"""

import argparse
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "effects"))

UNIT = 64          # 描く関数が使う座標の大きさ。128 の絵も同じ座標で描き、細かさだけが上がる
SS = 8             # 描くときの倍率
EDGE = 5.0         # 絵の端からこの幅（UNIT の単位）で光を消す（敵の上に出したとき四角い縁が見えないように）

#: 画風。scale は真ん中を中心にした形の大きさ、width は線の太さ、glow はにじみの幅、
#: gain は明るさ、alpha は不透明度の上限。
STYLES = {
    "soft": {"scale": 0.85, "width": 0.7, "glow": 0.6, "gain": 0.6, "alpha": 0.85},
    "bright": {"scale": 1.0, "width": 1.0, "glow": 1.0, "gain": 0.8, "alpha": 1.0},
}

WHITE = (1.0, 1.0, 1.0)
STEEL = (0.86, 0.90, 1.0)
GOLD = (1.0, 0.78, 0.35)
EMBER = (1.0, 0.55, 0.20)
CYAN = (0.35, 0.85, 1.0)
BLOOD = (1.0, 0.45, 0.45)
WOOD = (0.95, 0.85, 0.65)
TOXIC = (0.55, 1.0, 0.35)
VIOLET = (0.75, 0.45, 1.0)


# --------------------------------------------------------------------------
# 描く道具。座標・太さ・半径は UNIT（64）の大きさのまま渡す
# --------------------------------------------------------------------------
class Frame:
    def __init__(self, size=UNIT, style="bright"):
        self.size = size
        self.style = STYLES[style]
        self.big = size * SS
        self.k = SS * size / UNIT                 # UNIT の 1 → 描く画素
        self.light = np.zeros((self.big, self.big, 3), dtype=np.float32)

    # 画風を効かせた座標と長さ
    def p(self, x, y):
        s = self.style["scale"]
        return ((UNIT / 2 + (x - UNIT / 2) * s) * self.k, (UNIT / 2 + (y - UNIT / 2) * s) * self.k)

    def w(self, width):
        return width * self.style["scale"] * self.style["width"]

    def r(self, radius):
        return radius * self.style["scale"]

    def _mask(self):
        image = Image.new("L", (self.big, self.big), 0)
        return image, ImageDraw.Draw(image)

    def add(self, mask_image, color, strength=1.0, glow=0.0):
        """白黒の形を、色と強さで光として足す。glow は UNIT の単位でのにじみの幅。"""
        mask = np.asarray(mask_image, dtype=np.float32) / 255.0
        self.light += mask[..., None] * np.array(color, dtype=np.float32) * strength
        glow = glow * self.style["glow"] * self.style["scale"]
        if glow > 0:
            blurred = mask_image.filter(ImageFilter.GaussianBlur(glow * self.k))
            soft = np.asarray(blurred, dtype=np.float32) / 255.0
            self.light += soft[..., None] * np.array(color, dtype=np.float32) * strength * 0.9

    def streak(self, x0, y0, x1, y1, width, color, strength=1.0, glow=1.2, taper=True):
        """尖った筋（剣の軌跡・弾の筋）。taper なら両端が細く、真ん中が太い。"""
        image, draw = self._mask()
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        width = self.w(width) / self.style["scale"]      # 座標は p() で縮むので、ここでは太さの率だけ
        points_a, points_b = [], []
        steps = 24
        for i in range(steps + 1):
            t = i / steps
            half = width * (math.sin(math.pi * t) if taper else 1.0) / 2.0
            cx, cy = x0 + dx * t, y0 + dy * t
            points_a.append(self.p(cx + nx * half, cy + ny * half))
            points_b.append(self.p(cx - nx * half, cy - ny * half))
        draw.polygon(points_a + points_b[::-1], fill=255)
        self.add(image, color, strength, glow)

    def wedge(self, tip_x, tip_y, tail_x, tail_y, width, color, strength=1.0, glow=1.0):
        """先が尖り、後ろへ太くなってから消える形（突き・槍）。"""
        image, draw = self._mask()
        dx, dy = tip_x - tail_x, tip_y - tail_y
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        width = self.w(width) / self.style["scale"]
        side_a, side_b = [], []
        steps = 20
        for i in range(steps + 1):
            t = i / steps                      # 0 = 後ろ, 1 = 先
            half = width * (t ** 0.6) * (1.0 - t) * 2.2 / 2.0
            cx, cy = tail_x + dx * t, tail_y + dy * t
            side_a.append(self.p(cx + nx * half, cy + ny * half))
            side_b.append(self.p(cx - nx * half, cy - ny * half))
        draw.polygon(side_a + side_b[::-1], fill=255)
        self.add(image, color, strength, glow)

    def arc(self, cx, cy, radius, start_deg, end_deg, width, color, strength=1.0, glow=1.4):
        """三日月の形の弧（大振りの斬撃）。両端が細い。"""
        image, draw = self._mask()
        width = self.w(width) / self.style["scale"]
        outer, inner = [], []
        steps = 48
        for i in range(steps + 1):
            t = i / steps
            angle = math.radians(start_deg + (end_deg - start_deg) * t)
            half = width * math.sin(math.pi * t) ** 0.8 / 2
            outer.append(self.p(cx + math.cos(angle) * (radius + half), cy + math.sin(angle) * (radius + half)))
            inner.append(self.p(cx + math.cos(angle) * (radius - half), cy + math.sin(angle) * (radius - half)))
        draw.polygon(outer + inner[::-1], fill=255)
        self.add(image, color, strength, glow)

    def _ellipse(self, draw, cx, cy, radius, fill):
        px, py = self.p(cx, cy)
        rr = self.r(radius) * self.k
        draw.ellipse((px - rr, py - rr, px + rr, py + rr), fill=fill)

    def ring(self, cx, cy, radius, width, color, strength=1.0, glow=1.0):
        image, draw = self._mask()
        width = self.w(width) / self.style["scale"]
        self._ellipse(draw, cx, cy, radius + width / 2, 255)
        self._ellipse(draw, cx, cy, max(0.0, radius - width / 2), 0)
        self.add(image, color, strength, glow)

    def dot(self, cx, cy, radius, color, strength=1.0, glow=1.0):
        image, draw = self._mask()
        self._ellipse(draw, cx, cy, radius, 255)
        self.add(image, color, strength, glow)

    def star(self, cx, cy, radius, rays, color, strength=1.0, glow=1.5, turn=0.0, thin=0.18):
        """光の星（当たったところの閃き）。"""
        image, draw = self._mask()
        points = []
        for i in range(rays * 2):
            angle = math.pi * i / rays + turn
            rr = radius if i % 2 == 0 else radius * thin
            points.append(self.p(cx + math.cos(angle) * rr, cy + math.sin(angle) * rr))
        draw.polygon(points, fill=255)
        self.add(image, color, strength, glow)

    def polygon(self, points, color, strength=1.0, glow=0.8):
        image, draw = self._mask()
        draw.polygon([self.p(x, y) for x, y in points], fill=255)
        self.add(image, color, strength, glow)

    def image(self):
        """光を RGBA に直して絵の大きさへ縮める。明るさ＝不透明度、色は明るさで割った値。"""
        light = np.clip(self.light, 0.0, None) * self.style["gain"]
        edge = np.minimum(np.arange(self.big), np.arange(self.big)[::-1]).astype(np.float32) / (EDGE * self.k)
        edge = np.clip(edge, 0.0, 1.0) ** 0.8
        light *= (edge[:, None] * edge[None, :])[..., None]
        brightness = light.max(axis=2)
        alpha = np.clip(brightness, 0.0, 1.0)
        # 強い光ほど白へ寄せる（芯が白く、縁に色が残る）
        overflow = np.clip(brightness - 1.0, 0.0, 1.0)[..., None]
        with np.errstate(invalid="ignore", divide="ignore"):
            color = np.where(alpha[..., None] > 0, light / np.maximum(alpha[..., None], 1e-6), 0.0)
        color = np.clip(color, 0.0, 1.0)
        color = color * (1.0 - overflow) + overflow
        alpha = alpha * self.style["alpha"]
        # 縮めるときは色と不透明度を一緒に平均したいので、乗算済みにしてから縮める
        premul = np.concatenate([color * alpha[..., None], alpha[..., None]], axis=2)
        big = Image.fromarray((premul * 255.0 + 0.5).astype(np.uint8), "RGBA")
        small = big.resize((self.size, self.size), Image.LANCZOS)
        out = np.asarray(small, dtype=np.float32) / 255.0
        a = out[..., 3:4]
        out[..., :3] = np.where(a > 0, out[..., :3] / np.maximum(a, 1e-6), 0.0)
        out = np.clip(out, 0.0, 1.0)
        return Image.fromarray((out * 255.0 + 0.5).astype(np.uint8), "RGBA")


def ease(t):
    return 1.0 - (1.0 - t) ** 2


def fade(t, start):
    """start から 1 にかけて 1 → 0。"""
    if t <= start:
        return 1.0
    return max(0.0, 1.0 - (t - start) / (1.0 - start))


def sparks(frame, cx, cy, t, count, seed, color, spread=22.0, size=0.9, gravity=10.0, start=0.0):
    """当たったところから飛び散る火花。t は 0..1。"""
    rng = random.Random(seed)
    local = (t - start) / (1.0 - start) if t > start else -1.0
    if local < 0:
        return
    for _ in range(count):
        angle = rng.uniform(0, math.tau)
        speed = rng.uniform(0.5, 1.0) * spread
        life = rng.uniform(0.6, 1.0)
        if local > life:
            continue
        k = ease(local / life)
        x = cx + math.cos(angle) * speed * k
        y = cy + math.sin(angle) * speed * k + gravity * (local / life) ** 2
        tail = 3.0 * (1.0 - local / life)
        frame.streak(x - math.cos(angle) * tail, y - math.sin(angle) * tail, x, y,
                     size, color, strength=1.2 * (1.0 - local / life), glow=0.6, taper=False)


# --------------------------------------------------------------------------
# 演出ごとの描き方。(コマ数, 1コマを描く関数, 絵の大きさ)
# --------------------------------------------------------------------------
def thrust(frame, i, n):
    """突き。左下から細い穂先が走り、先で小さく弾ける。"""
    t = i / (n - 1)
    sx, sy, ex, ey = 6.0, 58.0, 44.0, 20.0
    reach = ease(min(1.0, t / 0.4))
    tip_x, tip_y = sx + (ex - sx) * reach, sy + (ey - sy) * reach
    tail = 34.0 * (1.0 - 0.5 * max(0.0, (t - 0.4) / 0.6))
    ux, uy = (ex - sx), (ey - sy)
    length = math.hypot(ux, uy)
    ux, uy = ux / length, uy / length
    alpha = fade(t, 0.45)
    frame.wedge(tip_x, tip_y, tip_x - ux * tail, tip_y - uy * tail, 7.0, STEEL, alpha * 1.3)
    for offset, delay in ((-6.0, 0.05), (6.0, 0.1)):
        if t > delay:
            nx, ny = -uy * offset, ux * offset
            frame.streak(tip_x - ux * 30 + nx, tip_y - uy * 30 + ny,
                         tip_x - ux * 8 + nx, tip_y - uy * 8 + ny, 1.0, WHITE, alpha * 0.6, glow=0.4)
    if t >= 0.35:
        local = (t - 0.35) / 0.65
        frame.star(ex + ux * 2, ey + uy * 2, 4.0 + 9.0 * ease(local), 4, WHITE,
                   1.3 * (1.0 - local), glow=1.2, turn=math.radians(45 * local))


def heavy_slash(frame, i, n):
    """重い一撃。大きな三日月が左上から右下へ振り抜かれ、広がって消える。"""
    t = i / (n - 1)
    sweep = ease(min(1.0, t / 0.45))
    start, end = -160.0, 20.0
    head = start + (end - start) * sweep
    tail = start + (end - start) * max(0.0, sweep - 0.75)
    grow = 1.0 + 0.15 * max(0.0, (t - 0.45) / 0.55)
    alpha = fade(t, 0.5)
    frame.arc(36, 36, 22.0 * grow, tail, head, 9.0, STEEL, alpha * 1.3, glow=1.8)
    frame.arc(36, 36, 22.0 * grow, tail, head, 3.0, WHITE, alpha * 1.2, glow=0.5)
    if t > 0.3:
        sparks(frame, 52, 44, t, 6, 11, WHITE, spread=14.0, gravity=6.0, start=0.3)


def cross_slash(frame, i, n):
    """十字の連撃。1本目と2本目が続けて走り、交点が光る。"""
    t = i / (n - 1)
    for (x0, y0, x1, y1), begin in (((10, 12, 54, 52), 0.0), ((54, 12, 10, 52), 0.22)):
        if t < begin:
            continue
        local = (t - begin) / (1.0 - begin)
        reach = ease(min(1.0, local / 0.3))
        alpha = fade(local, 0.3) ** 1.5
        frame.streak(x0, y0, x0 + (x1 - x0) * reach, y0 + (y1 - y0) * reach, 5.0, STEEL,
                     alpha * 1.1, glow=1.2)
    if t > 0.35:
        local = (t - 0.35) / 0.65
        frame.star(32, 32, 5.0 + 8.0 * ease(local), 4, WHITE, 1.3 * (1.0 - local) ** 1.5, glow=1.5,
                   turn=math.radians(45))


def claw(frame, i, n):
    """爪。斜めの3本の筋が少しずつずれて走る。"""
    t = i / (n - 1)
    for k, offset in enumerate((-9.0, 0.0, 9.0)):
        begin = 0.07 * k
        if t < begin:
            continue
        local = (t - begin) / (1.0 - begin)
        reach = ease(min(1.0, local / 0.35))
        alpha = fade(local, 0.4)
        x0, y0 = 46 + offset, 10 + offset * 0.3
        x1, y1 = 18 + offset, 54 + offset * 0.3
        frame.streak(x0, y0, x0 + (x1 - x0) * reach, y0 + (y1 - y0) * reach, 3.6, WHITE,
                     alpha * 1.2, glow=1.0)
        frame.streak(x0, y0, x0 + (x1 - x0) * reach, y0 + (y1 - y0) * reach, 6.0, BLOOD,
                     alpha * 0.35, glow=1.6)


def bite(frame, i, n):
    """噛みつき。上下の顎の弧が牙ごと閉じ、閉じたところが赤く光る。"""
    t = i / (n - 1)
    close = ease(min(1.0, t / 0.35))
    alpha = fade(t, 0.4) ** 1.3
    gap = 17.0 * (1.0 - close)
    for side in (-1, 1):
        # 顎は真ん中がいちばん開き、口の端（左右）で上下が合う弧。side=-1 が上顎
        opening = gap + 3.0
        rim = []
        for k in range(25):
            u = k / 24.0
            x = 10 + 44 * u
            rim.append((x, 32 + side * opening * (1 - (2 * u - 1) ** 2)))
        for (x0, y0), (x1, y1) in zip(rim, rim[1:]):
            frame.streak(x0, y0, x1, y1, 2.4, WHITE, alpha * 0.9, glow=0.6, taper=False)
        for k, u in enumerate((0.22, 0.4, 0.6, 0.78)):
            x = 10 + 44 * u
            y = 32 + side * opening * (1 - (2 * u - 1) ** 2)
            long = 8.0 if k in (0, 3) else 5.5
            frame.polygon([(x - 2.4, y), (x + 2.4, y), (x, y - side * long)], WHITE, alpha * 1.1, glow=0.6)
    if t > 0.3:
        local = (t - 0.3) / 0.7
        frame.star(32, 32, 6.0 + 10.0 * ease(local), 6, BLOOD, 1.0 * (1.0 - local), glow=1.5)
        frame.star(32, 32, 4.0 + 6.0 * ease(local), 6, WHITE, 1.1 * (1.0 - local), glow=0.6)
        sparks(frame, 32, 32, t, 6, 17, BLOOD, spread=14.0, gravity=8.0, start=0.3)


def whip(frame, i, n):
    """鞭。波打つ線が左下から伸び、先が伸び切ったところで弾ける。"""
    t = i / (n - 1)
    reveal = ease(min(1.0, t / 0.4))
    alpha = fade(t, 0.5)
    wave = 9.0 * (1.0 - 0.7 * reveal)

    def point(u):
        return (6 + 46 * u, 54 - 32 * u + wave * math.sin(math.tau * 1.2 * u) * (1.0 - u * 0.6))

    steps = 28
    shown = max(1, int(steps * reveal))
    for k in range(shown):
        u0, u1 = k / steps, (k + 1) / steps
        (x0, y0), (x1, y1) = point(u0), point(u1)
        width = 2.6 * (1.0 - 0.65 * u0)
        frame.streak(x0, y0, x1, y1, width, WOOD, alpha * 1.1, glow=0.5, taper=False)
        frame.streak(x0, y0, x1, y1, width * 0.4, WHITE, alpha * 0.8, glow=0.2, taper=False)
    if t >= 0.36:
        local = (t - 0.36) / 0.64
        tx, ty = point(1.0)
        frame.star(tx, ty, 3.0 + 9.0 * ease(local), 4, WHITE, 1.4 * (1.0 - local), glow=1.2,
                   turn=math.radians(20))
        frame.ring(tx, ty, 2.0 + 9.0 * ease(local), 1.0, WHITE, 0.9 * (1.0 - local), glow=0.6)


def arrow_shot(frame, i, n):
    """矢。左下から飛び込んで刺さり、羽が震えて消える。"""
    t = i / (n - 1)
    sx, sy, ex, ey = -10.0, 70.0, 38.0, 26.0
    fly = ease(min(1.0, t / 0.3))
    ux, uy = ex - sx, ey - sy
    length = math.hypot(ux, uy)
    ux, uy = ux / length, uy / length
    nx, ny = -uy, ux
    tip_x, tip_y = sx + (ex - sx) * fly, sy + (ey - sy) * fly
    alpha = fade(t, 0.55)
    shaft = 22.0
    tail_x, tail_y = tip_x - ux * shaft, tip_y - uy * shaft
    if t < 0.3:
        frame.streak(tail_x - ux * 20, tail_y - uy * 20, tail_x, tail_y, 2.0, WHITE, 0.5, glow=0.8)
    frame.streak(tail_x, tail_y, tip_x - ux * 3, tip_y - uy * 3, 1.4, WOOD, alpha * 1.3,
                 glow=0.4, taper=False)
    frame.polygon([(tip_x + ux * 1.5, tip_y + uy * 1.5),
                   (tip_x - ux * 4 + nx * 2.6, tip_y - uy * 4 + ny * 2.6),
                   (tip_x - ux * 4 - nx * 2.6, tip_y - uy * 4 - ny * 2.6)], STEEL, alpha * 1.4)
    wobble = math.sin(t * 40.0) * 1.4 * (1.0 if t > 0.3 else 0.0)
    for side in (-1, 1):
        frame.polygon([(tail_x + ux * 5, tail_y + uy * 5),
                       (tail_x + nx * (3.2 * side) + nx * wobble, tail_y + ny * (3.2 * side) + ny * wobble),
                       (tail_x - ux * 2 + nx * (2.5 * side), tail_y - uy * 2 + ny * (2.5 * side))],
                      WHITE, alpha * 0.9, glow=0.4)
    if t >= 0.3:
        local = (t - 0.3) / 0.7
        frame.star(ex, ey, 3.0 + 9.0 * ease(local), 4, WHITE, 1.3 * (1.0 - local),
                   glow=1.2, turn=math.radians(20))
        sparks(frame, ex, ey, t, 5, 7, STEEL, spread=12.0, gravity=4.0, start=0.3)


def thrown_weapon(frame, i, n):
    """投擲。回る刃が左から飛び込み、当たって火花を散らす。"""
    t = i / (n - 1)
    sx, sy, ex, ey = -6.0, 44.0, 34.0, 30.0
    fly = ease(min(1.0, t / 0.4))
    x, y = sx + (ex - sx) * fly, sy + (ey - sy) * fly
    spin = t * 10.0
    alpha = fade(t, 0.5)
    if t < 0.45:
        frame.streak(x - 22, y + 7, x - 4, y + 1.5, 2.6, WHITE, 0.45, glow=1.0)
    frame.star(x, y, 7.0, 4, STEEL, alpha * 1.4, glow=0.8, turn=spin, thin=0.28)
    frame.dot(x, y, 1.4, WHITE, alpha, glow=0.3)
    if t >= 0.38:
        local = (t - 0.38) / 0.62
        frame.star(ex, ey, 4.0 + 8.0 * ease(local), 6, WHITE, 1.2 * (1.0 - local), glow=1.2)
        sparks(frame, ex, ey, t, 7, 13, GOLD, spread=16.0, gravity=8.0, start=0.38)


def gun_shot(frame, i, n):
    """銃撃。一瞬の弾の筋のあと、当たったところで黄色い閃きと火花。"""
    ex, ey = 34.0, 30.0
    if i == 0:
        frame.streak(-4, 50, ex, ey, 2.2, GOLD, 1.4, glow=1.2, taper=False)
        frame.streak(-4, 50, ex, ey, 0.9, WHITE, 1.4, glow=0.3, taper=False)
        frame.dot(ex, ey, 3.0, WHITE, 1.5, glow=1.5)
        return
    local = (i - 1) / (n - 2)
    flash = max(0.0, 1.0 - local / 0.35)
    if flash > 0:
        frame.star(ex, ey, 6.0 + 10.0 * (1.0 - flash), 6, GOLD, 1.8 * flash, glow=2.0,
                   turn=math.radians(15))
        frame.dot(ex, ey, 3.5 * flash + 1.0, WHITE, 1.6 * flash, glow=1.2)
    frame.ring(ex, ey, 3.0 + 14.0 * ease(local), 1.2, EMBER, 0.9 * (1.0 - local), glow=0.8)
    sparks(frame, ex, ey, local, 9, 21, GOLD, spread=20.0, gravity=12.0)


def rapid_fire(frame, i, n):
    """連射。弾の筋と小さな着弾が2コマおきに4発続く。"""
    rng = random.Random(9)
    hits = [(31 + rng.uniform(-9, 9), 30 + rng.uniform(-8, 8), rng.uniform(-6, 6)) for _ in range(4)]
    life = 6
    for k, (x, y, dy) in enumerate(hits):
        j = i - 2 * k
        if j < 0 or j > life:
            continue
        if j == 0:
            frame.streak(-4, 50 + dy, x, y, 1.6, GOLD, 1.2, glow=0.9, taper=False)
            frame.streak(-4, 50 + dy, x, y, 0.6, WHITE, 1.2, glow=0.2, taper=False)
        local = j / life
        flash = max(0.0, 1.0 - local / 0.5)
        if flash > 0:
            frame.star(x, y, 3.0 + 5.0 * (1.0 - flash), 4, GOLD, 1.6 * flash, glow=1.2,
                       turn=math.radians(25 * k))
            frame.dot(x, y, 1.6 * flash + 0.4, WHITE, 1.4 * flash, glow=0.6)
        sparks(frame, x, y, local, 4, 60 + k, EMBER, spread=10.0, size=0.7, gravity=7.0)


def shotgun_blast(frame, i, n):
    """散弾。いくつもの小さな着弾が少しずつずれて弾ける。"""
    t = i / (n - 1)
    rng = random.Random(5)
    hits = [(32 + rng.uniform(-15, 15), 32 + rng.uniform(-13, 13), rng.uniform(0.0, 0.18))
            for _ in range(7)]
    if i == 0:
        for x, y, _ in hits:
            frame.streak(-6, 52, x, y, 0.9, GOLD, 0.7, glow=0.6, taper=False)
    for k, (x, y, begin) in enumerate(hits):
        if t < begin:
            continue
        local = (t - begin) / (1.0 - begin)
        flash = max(0.0, 1.0 - local / 0.4)
        if flash > 0:
            frame.star(x, y, 3.0 + 4.0 * (1.0 - flash), 4, GOLD, 1.6 * flash, glow=1.2,
                       turn=math.radians(30 * k))
            frame.dot(x, y, 1.6 * flash + 0.5, WHITE, 1.5 * flash, glow=0.6)
        sparks(frame, x, y, local, 3, 40 + k, EMBER, spread=8.0, size=0.7, gravity=6.0)


def sniper_shot(frame, i, n):
    """狙撃。画面を横切る細い一線のあと、当たったところに輪が広がる。"""
    t = i / (n - 1)
    ex, ey = 32.0, 32.0
    if i <= 1:
        strength = 1.6 if i == 0 else 0.8
        frame.streak(-6, 6, 70, 58, 1.6, WHITE, strength, glow=1.0, taper=False)
        frame.streak(-6, 6, 70, 58, 4.0, CYAN, strength * 0.4, glow=1.4, taper=False)
    flash = max(0.0, 1.0 - t / 0.3)
    if flash > 0:
        frame.star(ex, ey, 5.0 + 12.0 * (1.0 - flash), 4, WHITE, 1.8 * flash, glow=1.8,
                   turn=math.radians(34))
    for k, delay in enumerate((0.0, 0.18)):
        if t < delay:
            continue
        r = (t - delay) / (1.0 - delay)
        frame.ring(ex, ey, 2.0 + 24.0 * ease(r), 1.4 - 0.5 * k, WHITE, 1.1 * (1.0 - r), glow=0.8)


def energy_beam(frame, i, n):
    """光線。左から右へ太い光の帯が走り、膨らんでから細って消える。"""
    t = i / (n - 1)
    reach = ease(min(1.0, t / 0.2))
    swell = math.sin(math.pi * min(1.0, t / 0.75)) if t < 0.75 else 0.0
    width = 2.0 + 9.0 * swell
    x1 = 2 + 62 * reach
    if swell > 0:
        frame.streak(2, 34, x1, 30, width + 5.0, CYAN, 0.8, glow=2.2, taper=False)
        frame.streak(2, 34, x1, 30, width * 0.45, WHITE, 1.4, glow=0.8, taper=False)
    rng = random.Random(i * 7 + 3)
    for _ in range(6):
        if t > 0.9:
            break
        x = rng.uniform(4, 60) * reach
        y = 32 + rng.uniform(-1, 1) * (width / 2 + 3)
        frame.dot(x, y, rng.uniform(0.5, 1.1), WHITE, 1.2 * (1.0 - t), glow=0.6)
    if t > 0.2:
        local = (t - 0.2) / 0.8
        frame.ring(36, 31, 4.0 + 16.0 * ease(local), 1.4, CYAN, 1.0 * (1.0 - local), glow=1.0)


def energy_slash(frame, i, n):
    """光の刃。青白く光る斬撃が走り、残像と光の粒が残る。"""
    t = i / (n - 1)
    reach = ease(min(1.0, t / 0.35))
    alpha = fade(t, 0.35)
    x0, y0, x1, y1 = 12, 12, 52, 50
    hx, hy = x0 + (x1 - x0) * reach, y0 + (y1 - y0) * reach
    frame.streak(x0, y0, hx, hy, 6.0, CYAN, alpha * 0.9, glow=2.0)
    frame.streak(x0, y0, hx, hy, 2.0, WHITE, alpha * 1.3, glow=0.5)
    if t > 0.1:
        ghost = fade(t, 0.2) * 0.5
        frame.streak(x0 + 5, y0 - 3, hx + 5, hy - 3, 3.0, CYAN, ghost, glow=1.2)
    rng = random.Random(31)
    for _ in range(8):
        u = rng.uniform(0.1, 0.9)
        if u > reach:
            continue
        drift = max(0.0, t - u * 0.35) * 10.0
        px = x0 + (x1 - x0) * u + rng.uniform(-2, 2)
        py = y0 + (y1 - y0) * u - drift
        frame.dot(px, py, rng.uniform(0.5, 1.0), WHITE, 1.1 * fade(t, 0.5), glow=0.7)


def shockwave(frame, i, n):
    """衝撃波。真ん中の閃きから輪が3重に広がり、放射の筋が走る。"""
    t = i / (n - 1)
    if t < 0.3:
        flash = 1.0 - t / 0.3
        frame.dot(32, 32, 2.0 + 4.0 * t / 0.3, WHITE, 1.5 * flash, glow=1.6)
    for k, delay in enumerate((0.0, 0.15, 0.3)):
        if t < delay:
            continue
        r = (t - delay) / (1.0 - delay)
        frame.ring(32, 32, 3.0 + 26.0 * ease(r), 2.4 * (1.0 - r) + 0.6, WHITE,
                   (1.2 - 0.25 * k) * (1.0 - r), glow=0.9)
    if t < 0.6:
        local = t / 0.6
        for k in range(8):
            angle = math.tau * k / 8 + math.radians(22.5)
            r0, r1 = 6.0 + 20.0 * ease(local), 11.0 + 22.0 * ease(local)
            frame.streak(32 + math.cos(angle) * r0, 32 + math.sin(angle) * r0,
                         32 + math.cos(angle) * r1, 32 + math.sin(angle) * r1,
                         1.4, WHITE, 0.9 * (1.0 - local), glow=0.4)


def poison(frame, i, n):
    """毒。毒の塊が弾けて滴が飛び、泡が浮いて割れる。"""
    t = i / (n - 1)
    if t < 0.5:
        local = t / 0.5
        frame.dot(32, 34, 3.0 + 7.0 * ease(local), TOXIC, 0.9 * (1.0 - local), glow=1.6)
        frame.dot(32, 34, 1.5 + 3.0 * ease(local), WHITE, 0.7 * (1.0 - local), glow=0.6)
    rng = random.Random(23)
    for k in range(9):
        angle = rng.uniform(math.pi * 1.05, math.pi * 1.95)
        speed = rng.uniform(10, 20)
        local = t / rng.uniform(0.6, 0.9)
        if local > 1.0:
            continue
        x = 32 + math.cos(angle) * speed * ease(local)
        y = 34 + math.sin(angle) * speed * ease(local) + 14.0 * local ** 2
        frame.dot(x, y, 1.6 * (1.0 - 0.5 * local), TOXIC if k % 3 else VIOLET, 1.2 * (1.0 - local), glow=0.6)
    # 泡は出る時刻・昇る高さ・割れる時刻をばらし、最後のコマまでに割れ終える
    for k in range(6):
        begin = 0.12 + 0.07 * k
        life = rng.uniform(0.35, 0.5)
        x = 20 + rng.uniform(0, 24)
        height = rng.uniform(12, 22)
        local = (t - begin) / life
        if local < 0 or local > 1.0:
            continue
        y = 42 - height * local
        radius = 1.2 + 1.4 * local
        if local < 0.8:
            frame.ring(x, y, radius, 0.7, VIOLET if k % 2 else TOXIC, 1.0 * (1.0 - local * 0.5), glow=0.5)
        else:
            frame.star(x, y, radius + 1.5, 4, TOXIC, 1.6 * (1.0 - local), glow=0.5)


def poison_aoe(frame, i, n):
    """毒の全体。低く広がる毒の霧に、泡がいくつも浮いて割れる。"""
    t = i / (n - 1)
    rise = math.sin(math.pi * min(1.0, t / 0.9)) if t < 0.9 else 0.0
    rng = random.Random(41)
    # 霧は形の縁を見せず、にじみだけで見せる（画風の glow で細らないよう、形を小さくにじみを大きく）
    for k in range(11):
        x = 6 + 52 * k / 10 + rng.uniform(-2, 2)
        y = 46 + rng.uniform(-3, 3) - 4.0 * t
        radius = 1.5 + 2.5 * rise + rng.uniform(0, 1)
        frame.dot(x, y, radius, TOXIC if k % 2 else VIOLET, 0.3 * rise, glow=6.0)
    for k in range(12):
        begin = rng.uniform(0.05, 0.6)
        life = rng.uniform(0.25, 0.4)
        x = rng.uniform(8, 56)
        height = rng.uniform(16, 30)
        local = (t - begin) / life
        if local < 0 or local > 1.0:
            continue
        y = 48 - height * local
        radius = 0.9 + 1.4 * local
        if local < 0.8:
            frame.ring(x, y, radius, 0.6, VIOLET if k % 2 else TOXIC, 0.9, glow=0.5)
        else:
            frame.star(x, y, radius + 1.2, 4, TOXIC, 1.5 * (1.0 - local), glow=0.4)


EFFECTS = {
    "thrust": (10, thrust, 64),
    "heavy_slash": (11, heavy_slash, 64),
    "cross_slash": (12, cross_slash, 64),
    "energy_slash": (11, energy_slash, 64),
    "claw": (10, claw, 64),
    "bite": (11, bite, 64),
    "whip": (11, whip, 64),
    "arrow_shot": (11, arrow_shot, 64),
    "thrown_weapon": (11, thrown_weapon, 64),
    "gun_shot": (10, gun_shot, 64),
    "rapid_fire": (14, rapid_fire, 64),
    "shotgun_blast": (12, shotgun_blast, 64),
    "sniper_shot": (10, sniper_shot, 64),
    "energy_beam": (12, energy_beam, 64),
    "shockwave": (11, shockwave, 64),
    "poison": (12, poison, 64),
    "poison_aoe": (16, poison_aoe, 128),
}


def render(name, style):
    count, draw, size = EFFECTS[name]
    frames = []
    for i in range(count):
        frame = Frame(size, style)
        draw(frame, i, count)
        frames.append(frame.image())
    return frames


def write(style, name, frames):
    folder = os.path.join(OUT_DIR, style, name)
    if os.path.isdir(folder):
        for old in os.listdir(folder):
            if old.lower().endswith(".png"):
                os.remove(os.path.join(folder, old))
    os.makedirs(folder, exist_ok=True)
    for i, image in enumerate(frames):
        image.save(os.path.join(folder, "frame_{:03d}.png".format(i)), optimize=True)


def sheet(all_frames, path, cell=192):
    columns = max(len(frames) for frames in all_frames.values())
    canvas = Image.new("RGBA", (columns * cell, len(all_frames) * cell), (24, 24, 32, 255))
    for row, (name, frames) in enumerate(all_frames.items()):
        for col, image in enumerate(frames):
            big = image.resize((cell, cell), Image.NEAREST)
            canvas.alpha_composite(big, (col * cell, row * cell))
    canvas.convert("RGB").save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sheet", help="画風ごとの見本（sheet_<画風>.png）を書くフォルダ")
    parser.add_argument("--only", nargs="*", help="描き直す演出の名前（省くと全部）")
    args = parser.parse_args()
    names = args.only or list(EFFECTS)
    for style in STYLES:
        rendered = {}
        for name in names:
            rendered[name] = render(name, style)
            write(style, name, rendered[name])
        print("{}: {}".format(style, ", ".join("{}={}".format(n, len(f)) for n, f in rendered.items())))
        if args.sheet:
            sheet(rendered, os.path.join(args.sheet, "sheet_{}.png".format(style)))


if __name__ == "__main__":
    main()
