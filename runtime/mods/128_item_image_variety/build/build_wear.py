# -*- coding: utf-8 -*-
"""防具・服・装身具の一枚絵 → ゲームの寸法の絵と追加埋め込み辞書（一枚絵ごとの分類。../categories.json）。

入力は作者が画像生成 AI で作った黒背景の一枚絵（ジャンルごとに31枚）と、1品ごとの表 wear.json
（鍵・品種・一枚絵の上の箱・種類の名前）。分類は箱のある一枚絵で決まる（../categories.json）。
一枚絵はリポジトリの assets/item_images/wear に置き、配布物（zip）には入れない。置き場所は引数 --sheets か環境変数 ITEM_IMAGE_ASSETS で替えられる。

- 箱の中にほぼ収まる塊を全部取る（左右一組の靴や耳飾り）。表の `rect` が真の箱は、隣と接していて
  塊では分けられなかったもの。箱の中の画素をそのまま取る
- 寸法はどれも 64x64。胴の品は表の部位のとおり胴鎧か服の片方に置く（鎧・防弾ベストのように身を守る品が胴鎧）
- キャプションは「色 + 種類の名前」。ジャンルの組は種類の名前にジャンルの語（school / cyberpunk / western など）を入れてある
- 絵と辞書は元画像1枚ごとの分類のフォルダに書く（`build_guns.write_categories`）

    python build_wear.py [--sheets <一枚絵のフォルダ>] [--preview <見比べ用の一覧 PNG の置き場所>]
"""
import argparse
import functools
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_guns as b  # noqa: E402

# 防具・服・装身具はどれも 64x64（作者の指示。1マスの品は画面で 64px に描かれるので、縮めずにそのまま出る）
SIZE = (64, 64)
COLOR = {"grey": "grey", "white": "white", "black": "black", "tan": "beige", "brass": "gold", "wood": "brown",
         "red": "red", "orange": "orange", "olive green": "olive", "green": "green", "blue": "blue",
         "purple": "purple", "magenta": "magenta"}


def cut(folder, sheet_files, src, rect):
    name, _, box = src.partition("@")
    x0, y0, x1, y1 = map(int, box.split(","))
    b.SHEETS[name] = sheet_files[name]
    rgb_all, lab_all, boxes = b.sheet_of(folder, name)
    rgb = rgb_all[y0:y1, x0:x1]
    if rect:
        # 箱の画素をそのまま取る。ただし箱の左右の端に掛かった細い塊（隣の絵の端）と、小さな塊は捨てる
        keep = rgb.max(2) > b.SHEET_THRESHOLD
        lab, n = b.label(keep)
        if n:
            sizes = np.bincount(lab.ravel())[1:]
            width = keep.shape[1]
            for i in range(1, n + 1):
                ys, xs = np.nonzero(lab == i)
                edge = xs.min() == 0 or xs.max() == width - 1
                if sizes[i - 1] < 0.1 * sizes.max() or (edge and xs.max() - xs.min() + 1 < 0.3 * width):
                    keep[lab == i] = False
    else:
        lab = lab_all[y0:y1, x0:x1]
        keep = np.zeros(lab.shape, bool)
        for i in np.unique(lab[lab > 0]):
            cx0, cy0, cx1, cy1 = boxes[i]
            if cx0 >= x0 - 6 and cy0 >= y0 - 6 and cx1 <= x1 + 6 and cy1 <= y1 + 6:
                keep |= lab == i
    keep = b.fill_enclosed(keep, rgb)
    im = Image.fromarray(np.dstack([rgb, np.where(b.grow(keep, b.RING), 255, 0).astype(np.uint8)]))
    return im.crop(im.getbbox())


def caption_for(entry, im):
    # 表の plain が真の品は種類の名前だけ（名前に色が入っていて、測った色と重なる品）
    if entry.get("plain"):
        return "%s %s." % ("An" if entry["type"][0] in "aeiou" else "A", entry["type"])
    names = b.color_names(im)
    total = max(1, sum(names.values()))
    ranked = [(COLOR.get(k[5:] if k.startswith("glow:") else k), n) for k, n in names.most_common()]
    ranked = [(c, n) for c, n in ranked if c]
    merged = Counter()
    for c, n in ranked:
        merged[c] += n
    order = [c for c, _ in merged.most_common()]
    main = order[0] if order else "grey"
    # 名前に入っている色は前に重ねない（「white white helmet」にしない）
    named = set(re.findall(r"[a-z]+", entry["type"].lower()))
    text = entry["type"] if main in named else "%s %s" % (main, entry["type"])
    accent = [c for c in order[1:] if c not in ("grey", "black", main) and c not in named and merged[c] / total >= 0.12]
    if accent:
        text += " with %s details" % accent[0]
    return "%s %s." % ("An" if text[0] in "aeiou" else "A", text)


_cut_cache = {}


def make_entry(e, sheets, sheet_files):
    """表の1行から絵を作る（プロセスの中で動く。build_guns.parallel_map）。戻り値は (絵の配列, キャプション)。
    同じ箱の切り抜きはプロセスの中で1回（同じ箱を使う行がある）。"""
    src_key = (e["src"], bool(e.get("rect")))
    if src_key not in _cut_cache:
        _cut_cache[src_key] = cut(sheets, sheet_files, e["src"], e.get("rect"))
    im = _cut_cache[src_key]
    w, h = SIZE
    return b.shrink(im, w, h, 255), caption_for(e, im)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheets", default=str(b.ASSET_ROOT))
    ap.add_argument("--preview")
    args = ap.parse_args()

    entries = json.load(open(HERE / "wear.json", encoding="utf-8"))
    # 一枚絵の呼び名（w00…）→ assets/item_images の下の相対パス。categories.json の source から
    sheet_files = dict(b.SHEETS)
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys)), "鍵が重なっている"

    made = {}
    jobs = b.parallel_map(functools.partial(make_entry, sheets=args.sheets, sheet_files=sheet_files), entries)
    for e, (arr, caption) in zip(entries, jobs):
        e["caption"] = caption
        made[e["key"]] = Image.fromarray(arr)

    for e in entries:
        e["folder"] = e["pref"]
    b.write_categories(entries, made, b.owned_categories(entries))
    with open(HERE / "wear_captions.txt", "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write("%s\t%s\t%s\n" % (e["key"], e["pref"], e["caption"]))

    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
        for cid in sorted({b.category_of(e) for e in entries}):
            setname = cid
            tiles = [made[e["key"]] for e in entries if b.category_of(e) == cid and e["pref"] != "body_armor"]
            cols = 40
            rows = (len(tiles) + cols - 1) // cols
            sheet = Image.new("RGBA", (cols * 66, rows * 66), (40, 40, 40, 255))
            for i, t in enumerate(tiles):
                sheet.alpha_composite(t, ((i % cols) * 66 + 1, (i // cols) * 66 + 1))
            sheet.save(
                os.path.join(args.preview, "wear_%s.png" % setname))
    print("done")


if __name__ == "__main__":
    main()
