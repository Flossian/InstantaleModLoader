# -*- coding: utf-8 -*-
r"""描き直しを頼む手本の一枚絵を書く。

    python runtime/mods/128_item_image_variety/build/redraw/make_refs.py [--per 10]

ゲームの素の絵（`Assets\images\item_candidates_dark\<品種>\*.png`）を、品種ごとに `--per` 枚ずつ
黒の背景に1行に並べ、`refs\<品種>_<番号>.png` に書く。手本には文字も番号も入れない（描き直しに写り込む）。
並べる順と鍵は `refs\manifest.json` に残し、build_redraw.py が描き直しの絵をこの順で鍵に戻す。

順番は遊んだ記録で使われた回数の多い順。数えるのは 128 の対応表（`state\item_image\*.json`）の
外見文 → 絵（部位をまたいだ写し `xs.<元の部位>.<鍵>` は元の絵に数える）と、旧版の累計 `legacy_counts`。
品種の中を回数順に区切り、一枚絵どうしは載せた絵の回数の合計で並べる（`manifest.json` の並び）。
"""
import argparse
import collections
import glob
import io
import json
import os
import sys

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
from item_image_probe import find_game_dir  # noqa: E402

CROSS_PREFIX = "xs."
GAP = 80              # 絵どうしと縁の黒の幅（手本の画素）


def zoom(size):
    """手本で素の絵を何倍に広げるか。32 幅は5倍、武器（縦長）は4倍、64 幅は3倍。"""
    w, h = size
    if w >= 64:
        return 3
    return 4 if h > w else 5


def uses():
    """(品種, 鍵) ごとの使われた回数。"""
    count = collections.Counter()
    for path in glob.glob(os.path.join(ROOT, "state", "item_image", "*.json")):
        data = json.load(io.open(path, encoding="utf-8"))
        for label, key in (data.get("images") or {}).items():
            slot = label.partition("/")[0]
            if key.startswith(CROSS_PREFIX):
                slot, _, key = key[len(CROSS_PREFIX):].partition(".")
            count[(slot, key)] += 1
        for label, n in (data.get("legacy_counts") or {}).items():
            slot, _, key = label.partition("/")
            if isinstance(n, int):
                count[(slot, key)] += n
    return count


def numeric(key):
    return (0, int(key)) if key.isdigit() else (1, key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=10, help="1枚に並べる絵の数")
    args = ap.parse_args()
    src = os.path.join(str(find_game_dir(None)), "Assets", "images", "item_candidates_dark")
    out = os.path.join(WORK, "refs")
    os.makedirs(out, exist_ok=True)
    count = uses()
    sheets = []
    for slot in sorted(os.listdir(src)):
        folder = os.path.join(src, slot)
        if not os.path.isdir(folder):
            continue
        keys = [f[:-4] for f in os.listdir(folder) if f.endswith(".png")]
        if not keys:
            continue
        keys.sort(key=lambda k: (-count[(slot, k)], numeric(k)))
        for n, at in enumerate(range(0, len(keys), args.per), 1):
            part = keys[at:at + args.per]
            ims = [Image.open(os.path.join(folder, k + ".png")).convert("RGBA") for k in part]
            f = max(zoom(im.size) for im in ims)
            cells = [im.resize((im.width * f, im.height * f), Image.NEAREST) for im in ims]
            width = GAP + sum(c.width + GAP for c in cells)
            height = 2 * GAP + max(c.height for c in cells)
            sheet = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            x = GAP
            for c in cells:
                sheet.alpha_composite(c, (x, GAP + (height - 2 * GAP - c.height) // 2))
                x += c.width + GAP
            name = "{}_{:03d}".format(slot, n)
            sheet.convert("RGB").save(os.path.join(out, name + ".png"))
            sheets.append({"sheet": name, "slot": slot, "keys": part,
                           "uses": sum(count[(slot, k)] for k in part)})
    sheets.sort(key=lambda s: (-s["uses"], s["sheet"]))
    with io.open(os.path.join(out, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(sheets, fh, ensure_ascii=False, indent=1)
    used = sum(1 for s in sheets for k in s["keys"] if count[(s["slot"], k)])
    print("{} sheets, {} images ({} used in the records) -> {}".format(
        len(sheets), sum(len(s["keys"]) for s in sheets), used, out))
    for s in sheets[:15]:
        print("  {:<22} uses {:>4}".format(s["sheet"], s["uses"]))


if __name__ == "__main__":
    main()
