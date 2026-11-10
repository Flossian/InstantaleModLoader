# -*- coding: utf-8 -*-
r"""描き直しの一枚絵を切り出して、素の絵の鍵に戻す。

    python runtime/mods/128_item_image_variety/build/redraw/build_redraw.py [手本の名前 ...] [--skip 番号,番号]

`sheets\<手本の名前>.png`（make_refs.py の手本を画像生成 AI で描き直した黒背景の一枚絵）を読み、
品を左から右（行が分かれていれば上の行から）の順に切り出して、`refs\manifest.json` の同じ手本の鍵に当てる。
数が合わない一枚絵は書かずに知らせる。名前を渡さなければ `sheets\` にある全部。

- 書く先は `images\redraw\<品種>\<鍵>.png`。寸法は 128_item_image_variety の足した絵と同じ決まり
  （武器と消耗品・素材は素の絵の 1.5 倍、服・頭・手・脚・装身具は 64x64、盾と胴鎧は素の寸法）。
  縮め方・色の数・パレットの PNG も 128 の build_guns と同じ
- 小型武器は縦向き（刃が上）で頼み、ここで右上へ 45 度に倒す（素の絵の多くが斜め。素の絵が縦長の品は倒さない）。投擲武器は倒さない
- 光る刃の外の暗いにじみをはがす処理（128 の peel_glow）は、表 PEEL に載せた品だけに通す
- `--cols` に品の境目の x を渡すと、隙間で数えずにその位置で区切る（左右2つで1品の品が、隣の品との隙間より広く離れて描かれたとき）
- `--skip` に並びの番号（1 から）を渡すと、その品は書かない（描き直しが手本と違う品。前に書いた絵があれば消す）
- `previews\<手本の名前>.png` に、上の段に素の絵・下の段に描き直しを、ゲームの画面と同じ大きさで並べる
- 名前が `redo_` で始まる一枚絵は、描き直し済みの品を別の物として描き直した分（make_prompt.py --redo で頼んだ分）。
  並びは `refs\redo\<名前>.json`（{"品種/鍵": 一文}）で、品種の混ざった組を品ごとの品種へ書く。
  一文は、その鍵の載った手本の `refs\captions\<手本>.json` へ書き替える（build_redraw_data.py がこの一文で埋め込む）。
  `--skip` で外した品は、前の描き直しを残す
"""
import argparse
import io
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(os.path.dirname(HERE))   # 128_item_image_variety
ROOT = os.path.normpath(os.path.join(MOD, os.pardir, os.pardir, os.pardir))
# 取り込みの作業場（手本・描き直しの一枚絵・切り出し・頼んだ記録）。大きいので git の外の local\ に置く
WORK = os.path.join(ROOT, "local", "item_image_redraw")
# 描き直しの絵の置き場（配布物）
OUT = os.path.join(MOD, "images", "redraw")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "runtime", "mods", "128_item_image_variety", "build"))
import build_guns as b  # noqa: E402
from build_medieval import ITEM_WEAR  # noqa: E402
from item_image_probe import find_game_dir  # noqa: E402

THRESHOLD = b.SHEET_THRESHOLD     # これより明るい画素を絵とみなす（128 の一枚絵と同じ）
ROW_GAP = 24                      # 行を分ける、絵の無い横の帯の高さ
MERGE_GAP = 16                    # これより狭い隙間の塊は同じ品（首飾りの粒・離れた飾り）
SPECK = 40                        # これより小さい塊は塵として捨てる
PREVIEW_ZOOM = 2                  # 一覧は素の絵の寸法の何倍で描くか（ゲームはマス1つを約64px で描く）
PREVIEW_BG = (24, 33, 28)
DIAG = ("small_weapon",)   # 縦向きで頼み、作るときに 45 度へ倒す品種（素の絵が正方形の品だけ。縦長の品は縦のまま）。
# 投擲武器は手裏剣・爆弾・手榴弾など形がまちまちなので倒さず、描かれた向きのまま使う
# 128 の peel_glow（光る刃の外の暗いにじみをはがす）を通す品（品種/鍵）。描き直しは黒背景に光を描かないよう頼んでいて、
# にじみの付く品は少ない。全部の武器に通すと、刃の暗い側が芯と同じ色合いの品（青・紫・赤の刃）で刃の半分を削った。
# 武器の描き直し 30品で、通して黒い縁やにじみが取れたのがこの5品、残りは削れたか差が無かった
PEEL = {"small_weapon/235", "small_weapon/80", "large_weapon/134", "long_weapon/58", "long_weapon/56"}


def size_rule(slot, stock):
    """(箱の幅, 箱の高さ, 倍率)。箱は素の絵の寸法で、出す絵は倍率をかけた寸法（128 の足した絵と同じ）。"""
    if slot in ITEM_WEAR:
        return ITEM_WEAR[slot] + (1,)
    if slot in ("shield", "body_armor"):
        return stock + (1,)
    if slot in b.SIZES:
        return stock + (b.SCALE,)
    return stock + (b.ITEM_SCALE,)


def bands(mask):
    """絵のある行の帯 [(y0, y1)]。ROW_GAP 以上の空きで分ける。"""
    rows = np.flatnonzero(mask.any(1))
    if not len(rows):
        return []
    out, start = [], rows[0]
    for a, c in zip(rows, rows[1:]):
        if c - a > ROW_GAP:
            out.append((start, a + 1))
            start = c
    out.append((start, rows[-1] + 1))
    return out


def items(rgb, merge_gap=MERGE_GAP):
    """品の箱 [(x0, y0, x1, y1)] を読む順に。塊を横の区間にまとめ、merge_gap より狭い隙間はつなぐ。"""
    mask = rgb.max(2) > THRESHOLD
    lab, n = b.label(mask)
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    out = []
    for y0, y1 in bands(mask):
        spans = []
        for i in np.unique(lab[y0:y1][lab[y0:y1] > 0]):
            if sizes[i] < SPECK:
                continue
            ys, xs = np.nonzero(lab == i)
            spans.append([xs.min(), xs.max() + 1, ys.min(), ys.max() + 1])
        spans.sort()
        merged = []
        for s in spans:
            if merged and s[0] - merged[-1][1] < merge_gap:
                m = merged[-1]
                m[1], m[2], m[3] = max(m[1], s[1]), min(m[2], s[2]), max(m[3], s[3])
            else:
                merged.append(list(s))
        out += [(x0, y0_, x1, y1_) for x0, x1, y0_, y1_ in merged]
    return out, lab


def merge_to(boxes, n):
    """箱が多すぎるとき、同じ行で隙間のいちばん狭い隣どうしをつないで n 個にする。"""
    boxes = [list(x) for x in boxes]
    while len(boxes) > n:
        gaps = [(boxes[i + 1][0] - boxes[i][2], i) for i in range(len(boxes) - 1)
                if boxes[i + 1][0] > boxes[i][0]]          # 次の行の頭とはつながない
        if not gaps:
            break
        _, i = min(gaps)
        a, c = boxes[i], boxes.pop(i + 1)
        boxes[i] = [min(a[0], c[0]), min(a[1], c[1]), max(a[2], c[2]), max(a[3], c[3])]
    return [tuple(x) for x in boxes]


def split_to(boxes, n, mask):
    """箱が足りないとき（隣の品と触れて1つの塊になった）、いちばん幅の広い箱を、真ん中寄りで絵の画素の
    いちばん少ない列で割って n 個にする。"""
    boxes = [list(x) for x in boxes]
    while len(boxes) < n:
        i = max(range(len(boxes)), key=lambda j: boxes[j][2] - boxes[j][0])
        x0, y0, x1, y1 = boxes[i]
        cols = mask[y0:y1, x0:x1].sum(0)
        lo, hi = int(len(cols) * 0.25), int(len(cols) * 0.75)
        if hi <= lo:
            break
        at = x0 + lo + int(np.argmin(cols[lo:hi]))
        boxes[i:i + 1] = [[x0, y0, at, y1], [at, y0, x1, y1]]
    return [tuple(x) for x in boxes]


def boxes_at(rgb, cols):
    """品の境目の x（一枚絵の画素）で区切った箱。区切りごとに絵のある範囲へ詰める（1行の一枚絵だけ）。"""
    mask = rgb.max(2) > THRESHOLD
    lab, _ = b.label(mask)
    edges = [0] + list(cols) + [rgb.shape[1]]
    out = []
    for x0, x1 in zip(edges, edges[1:]):
        ys, xs = np.nonzero(mask[:, x0:x1])
        out.append((x0 + xs.min(), ys.min(), x0 + xs.max() + 1, ys.max() + 1))
    return out, lab


def find_boxes(rgb, n):
    """品の箱を n 個に揃える。隙間をつなぐ幅を狭めながら数え、多ければ狭い隙間でつなぎ、足りなければ割る。"""
    boxes, lab = [], None
    for gap in (MERGE_GAP, 8, 4, 2, 1):
        boxes, lab = items(rgb, gap)
        boxes = merge_to(boxes, n)
        if len(boxes) >= n:
            return boxes, lab
    return split_to(boxes, n, rgb.max(2) > THRESHOLD), lab


def cut(rgb, lab, box):
    """箱の中の品を取った RGBA（縁の 1px と、品の中の暗い影も入れる。128 の extract_whole と同じ）。
    箱に収まる塊は丸ごと、隣の品と触れて箱からはみ出す塊は、3割以上が箱の中にあれば箱の中の分だけ取る。"""
    x0, y0, x1, y1 = box
    sub = lab[y0:y1, x0:x1]
    keep = np.zeros(sub.shape, bool)
    for i in np.unique(sub[sub > 0]):
        ys, xs = np.nonzero(lab == i)
        if xs.min() >= x0 - 2 and xs.max() < x1 + 2 and ys.min() >= y0 - 2 and ys.max() < y1 + 2:
            keep |= sub == i
        elif (sub == i).sum() >= 0.3 * len(xs):
            keep |= sub == i
    part = rgb[y0:y1, x0:x1]
    keep = b.fill_enclosed(keep, part)
    im = Image.fromarray(np.dstack([part, np.where(b.grow(keep, b.RING), 255, 0).astype(np.uint8)]))
    return im.crop(im.getbbox())


def render(im, slot, stock_size, peel=False):
    """切り出した品（縦向き）を、その品種の決まりで出す絵にする（Image）。peel は PEEL に載った品のとき True。"""
    if slot in DIAG and stock_size[0] == stock_size[1]:
        # 縦向きで頼んだ小型と投擲は、素の絵と 128 の足した絵と同じく刃先を右上へ 45 度に倒す（32x32 に大きく入る）
        im = b.stand_up(im, 0).rotate(-45, resample=Image.BICUBIC, expand=True)
        im = im.crop(im.getbbox())
    box_w, box_h, scale = size_rule(slot, stock_size)
    if peel and slot in b.SIZES:
        im = b.peel_glow(im, min((box_w - 2) / im.width, (box_h - 2) / im.height))
    return Image.fromarray(b.shrink(im, box_w, box_h, 255, scale))


def sheet_keys(name, entry):
    """一枚絵に描かれた品の鍵の並び。make_prompt.py が描き直し済みの品を抜いて頼んだときは、その並び（refs\\requests）。"""
    path = os.path.join(WORK, "refs", "requests", name + ".json")
    if os.path.isfile(path):
        return json.load(io.open(path, encoding="utf-8"))
    return entry["keys"]


def preview(items, stock_root, skip, path):
    """上に素の絵、下に描き直し（無ければ空き）を、ゲームの画面と同じ大きさで並べる。items は [(品種, 鍵), ...]。"""
    cells = []
    for n, (slot, key) in enumerate(items, 1):
        st = Image.open(os.path.join(stock_root, slot, key + ".png")).convert("RGBA")
        w, h = st.width * PREVIEW_ZOOM, st.height * PREVIEW_ZOOM
        if slot in ITEM_WEAR:
            w, h = 64 * PREVIEW_ZOOM // 2 * 2, 64 * PREVIEW_ZOOM // 2 * 2
        new_path = os.path.join(OUT, slot, key + ".png")
        new = Image.open(new_path).convert("RGBA") if n not in skip and os.path.isfile(new_path) else None
        # どちらも同じ箱（画面の大きさ）に収めて比べる。ドット絵なので近い画素で広げる
        cells.append((st.resize((w, h), Image.NEAREST), new.resize((w, h), Image.NEAREST) if new else None))
    gap = 24
    width = gap + sum(c[0].width + gap for c in cells)
    height = 3 * gap + 2 * max(c[0].height for c in cells)
    sheet = Image.new("RGBA", (width, height), PREVIEW_BG + (255,))
    x = gap
    top = max(c[0].height for c in cells)
    for st, new in cells:
        sheet.alpha_composite(st, (x, gap))
        if new is not None:
            sheet.alpha_composite(new, (x, 2 * gap + top))
        x += st.width + gap
    sheet.convert("RGB").save(path)


def load_redo(name):
    """redo_ の一枚絵の並び [(品種, 鍵, 一文), ...]。並びの記録が無ければ None。"""
    path = os.path.join(WORK, "refs", "redo", name + ".json")
    if not os.path.isfile(path):
        return None
    return [tuple(sk.split("/", 1)) + (text,) for sk, text in json.load(io.open(path, encoding="utf-8")).items()]


def set_caption(manifest, slot, key, text):
    """鍵の載った手本の refs\\captions\\<手本>.json の一文を書き替える。"""
    for entry in manifest.values():
        if entry["slot"] == slot and key in entry["keys"]:
            path = os.path.join(WORK, "refs", "captions", entry["sheet"] + ".json")
            caps = json.load(io.open(path, encoding="utf-8")) if os.path.isfile(path) else {}
            caps[key] = text
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(caps, fh, ensure_ascii=False, indent=1)
            return
    print("  {}/{}: 手本に無い鍵（一文は書かない）".format(slot, key))


def write_item(rgb, lab, box, slot, key, stock_root):
    """箱の中の品を切り出し、切り出し（cuts）と出す絵（images\\redraw）を書く。"""
    im = cut(rgb, lab, box)
    # 縮める前の切り出しを残す。同じ素の絵が別の品種にもあれば、reuse_redraw.py がこれを縮め直して写す
    cut_dir = os.path.join(WORK, "cuts", slot)
    os.makedirs(cut_dir, exist_ok=True)
    im.save(os.path.join(cut_dir, key + ".png"))
    out_dir = os.path.join(OUT, slot)
    os.makedirs(out_dir, exist_ok=True)
    b.save_palette(render(im, slot, Image.open(os.path.join(stock_root, slot, key + ".png")).size,
                          "{}/{}".format(slot, key) in PEEL), os.path.join(out_dir, key + ".png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="手本の名前（例 accessory_001）。無ければ sheets の全部")
    ap.add_argument("--skip", default="", help="書かない品の並びの番号（1 から、カンマ区切り）。名前を1つ渡したときだけ")
    ap.add_argument("--cols", default="", help="品の境目の x（一枚絵の画素、カンマ区切り、品の数より1つ少なく）。"
                                              "左右2つで1品の品が離れて描かれ、隙間だけでは切り分けを間違えるとき。名前を1つ渡したときだけ")
    args = ap.parse_args()
    manifest = {s["sheet"]: s for s in json.load(io.open(os.path.join(WORK, "refs", "manifest.json"), encoding="utf-8"))}
    sheets_dir = os.path.join(WORK, "sheets")
    names = args.names or sorted(f[:-4] for f in os.listdir(sheets_dir) if f.endswith(".png"))
    skip = {int(x) for x in args.skip.split(",") if x.strip()}
    cols = [int(x) for x in args.cols.split(",") if x.strip()]
    if (skip or cols) and len(names) != 1:
        sys.exit("--skip と --cols は手本の名前を1つだけ渡したときに使う")
    stock_root = os.path.join(str(find_game_dir(None)), "Assets", "images", "item_candidates_dark")
    os.makedirs(os.path.join(WORK, "previews"), exist_ok=True)
    for name in names:
        redo = load_redo(name) if name.startswith("redo_") else None
        entry = manifest.get(name)
        if redo is not None:
            items = [(slot, key) for slot, key, _ in redo]
        elif entry is not None:
            items = [(entry["slot"], key) for key in sheet_keys(name, entry)]
        else:
            print("{}: refs\\manifest.json に無い".format(name))
            continue
        rgb = np.asarray(Image.open(os.path.join(sheets_dir, name + ".png")).convert("RGB"))
        boxes, lab = boxes_at(rgb, cols) if cols else find_boxes(rgb, len(items))
        if len(boxes) != len(items):
            print("{}: 品が {} 個に切れた（手本は {} 個）。書かない".format(name, len(boxes), len(items)))
            continue
        for n, ((slot, key), box) in enumerate(zip(items, boxes), 1):
            path = os.path.join(OUT, slot, key + ".png")
            if n in skip:
                # 読み違いのやり直しで外した品は前の描き直しを残す（消すと素の絵に戻る）
                if redo is None and os.path.isfile(path):
                    os.remove(path)
                continue
            write_item(rgb, lab, box, slot, key, stock_root)
            if redo is not None:
                set_caption(manifest, slot, key, redo[n - 1][2])
        preview(items, stock_root, skip, os.path.join(WORK, "previews", name + ".png"))
        print("{}: {} 品を書いた（{}）{}".format(name, len(items) - len(skip), "・".join(sorted({s for s, _ in items})),
                                          "、書かない番号 " + ",".join(map(str, sorted(skip))) if skip else ""))


if __name__ == "__main__":
    main()
