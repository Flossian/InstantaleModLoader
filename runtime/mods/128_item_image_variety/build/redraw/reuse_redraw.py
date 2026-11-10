# -*- coding: utf-8 -*-
r"""同じ素の絵の描き直しを写す。

    python runtime/mods/128_item_image_variety/build/redraw/reuse_redraw.py

ゲームは同じ絵を別の品種のフォルダにも置いている（手袋の絵が服のフォルダに、針金の輪が装身具のフォルダに、など）。
形（不透明の範囲）が同じで、色の差（不透明の画素の RGB の差の平均）が LIMIT 未満の素の絵を同じ絵とみなし、
片方の描き直しがあれば、その切り出し（`cuts\`）を写す先の品種の決まりで縮め直して `images\redraw\` に書く。
写した組は `refs\reused.json` に残す（写す先 → 写した元）。
自分で描き直した絵（`cuts\` にある鍵）は上書きしない。写した絵は、元を描き直せば次の実行で作り直す。
"""
import collections
import io
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_redraw import OUT, PEEL, WORK, find_game_dir, render  # noqa: E402

LIMIT = 13.0     # 同じ絵の組は 11〜12 に固まっていた（色だけ少し違う写し）


def main():
    stock_root = os.path.join(str(find_game_dir(None)), "Assets", "images", "item_candidates_dark")
    cuts = os.path.join(WORK, "cuts")
    drawn = {(s, f[:-4]) for s in os.listdir(cuts) for f in os.listdir(os.path.join(cuts, s))} if os.path.isdir(cuts) else set()
    groups = collections.defaultdict(list)
    for slot in os.listdir(stock_root):
        folder = os.path.join(stock_root, slot)
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if f.endswith(".png"):
                a = np.asarray(Image.open(os.path.join(folder, f)).convert("RGBA")).astype(np.int16)
                groups[(a.shape, (a[..., 3] > 0).tobytes())].append((slot, f[:-4], a))
    reused = {}
    # 同じ絵の組（make_prompt.py が、組の中の1枚だけを頼むのに使う）。差が LIMIT 未満の組をつないだまとまり
    parent = {}

    def root(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for items in groups.values():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i][2], items[j][2]
                if float(np.abs(a[..., :3] - b[..., :3])[a[..., 3] > 0].mean()) < LIMIT:
                    parent[root("{}/{}".format(*items[i][:2]))] = root("{}/{}".format(*items[j][:2]))
    same = collections.defaultdict(list)
    for x in list(parent):
        same[root(x)].append(x)
    with io.open(os.path.join(WORK, "refs", "same_images.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(sorted(sorted(g) for g in same.values() if len(g) > 1), fh, ensure_ascii=False, indent=1)
    for items in groups.values():
        if len(items) < 2:
            continue
        for slot, key, a in items:
            if (slot, key) in drawn:
                continue
            mask = a[..., 3] > 0
            best = None
            for s2, k2, b in items:
                if (s2, k2) in drawn:
                    diff = float(np.abs(a[..., :3] - b[..., :3])[mask].mean())
                    if diff < LIMIT and (best is None or diff < best[0]):
                        best = (diff, s2, k2)
            if best is None:
                continue
            im = Image.open(os.path.join(cuts, best[1], best[2] + ".png")).convert("RGBA")
            out_dir = os.path.join(OUT, slot)
            os.makedirs(out_dir, exist_ok=True)
            # にじみをはがすかは、写した元の品で決める（PEEL は描き直した品の鍵で書いている）
            render(im, slot, (a.shape[1], a.shape[0]), "{}/{}".format(best[1], best[2]) in PEEL).save(os.path.join(out_dir, key + ".png"))
            reused["{}/{}".format(slot, key)] = "{}/{}".format(best[1], best[2])
    with io.open(os.path.join(WORK, "refs", "reused.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(reused, fh, ensure_ascii=False, indent=1, sort_keys=True)
    by_slot = collections.Counter(k.partition("/")[0] for k in reused)
    print("写した {} 枚（自分で描き直した {} 枚から）: {}".format(len(reused), len(drawn), dict(sorted(by_slot.items()))))


if __name__ == "__main__":
    main()
