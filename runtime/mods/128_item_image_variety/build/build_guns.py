# -*- coding: utf-8 -*-
"""銃の絵 → ゲームの寸法の絵と追加埋め込み辞書。

入力は表 weapon_asset.json（README.md の「銃の絵」）:

- 作者が画像生成 AI で作った一枚絵（黒背景。8枚）。1枚ごとに 鍵・品種の希望（pref）・
  一枚絵の上の箱・回転・種類の名前。品種は縦横比で決め、キャプションは種類と絵から測った色で組む。
  一枚絵はリポジトリの assets/item_images に置き、配布物（zip）には入れない。置き場所は引数 --sheets か
  環境変数 ITEM_IMAGE_ASSETS で替えられる。銃の一枚絵は `武器/銃/`（categories.json の source）

出力:

- ../images/<分類>/Assets/images/item_candidates_dark/<品種>/<鍵>.png（分類は元画像1枚ごと。../categories.json）
  （ゲームは絵を `Assets/images/item_candidates_dark/<品種>/<鍵>.png` の相対パスで引く。
  実行時に `../images/<分類>` を Kivy の探し先に足すので、ゲームのフォルダには置かない）
- ../data/<分類>/<品種>.json（同じキャプションのベクトルを1つにまとめた形 {"index": {鍵: 番号}, "vectors": [...]}）

寸法はゲームの素材に合わせる（小型 32x32 / 中型 32x64 / 大型 32x96 / 長物 32x128）。
横向きの絵は銃口を上にして縦に立てる。小型の 32x32 は斜めの絵のまま。
色は元のまま（彩度を落とさない。どの素材も、元のままでゲームの _dark の素材より彩度が低い）。

    python build_guns.py [--sheets <一枚絵のフォルダ>] [--preview <見比べ用の一覧 PNG の書き出し先>]
"""
import argparse
import colorsys
import io
import json
import os
import sys
from collections import Counter, deque
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from minibert import MiniLM  # noqa: E402

MOD = HERE.parent
REPO = MOD.parents[2]
# 分類（元画像1枚＝1分類。`../categories.json`）。絵と辞書は分類ごとのフォルダに書く
CATEGORIES = json.load(open(MOD / "categories.json", encoding="utf-8"))
CATEGORY_OF_SHEET = {c["sheet"]: c["id"] for c in CATEGORIES}


def category_of(entry):
    """表の1件の分類。元素材の一枚絵（`<一枚絵>@箱`）の分類。"""
    return CATEGORY_OF_SHEET[entry["src"].partition("@")[0]]


def save_palette(im, path):
    """色の数だけのパレットを持つ PNG で書く（画素はそのまま。32色に減色した絵なので 256 色に必ず収まる）。
    フルカラーの PNG の約3分の1の大きさになる。ゲームの Kivy（SDL2）で読むと元と同じ画素になる（VERIFICATION.md §3.26）。"""
    a = np.asarray(im.convert("RGBA"))
    colors, index = np.unique(a.reshape(-1, 4), axis=0, return_inverse=True)
    assert len(colors) <= 256, (path, len(colors))
    pal = Image.fromarray(index.reshape(a.shape[:2]).astype(np.uint8), "P")
    pal.putpalette(colors[:, :3].flatten().tolist())
    pal.save(path, "PNG", optimize=True, transparency=bytes(colors[:, 3].tolist()))


def write_categories(entries, made, owned):
    """絵を `images/<分類>/Assets/images/item_candidates_dark/<品種>/<鍵>.png` に、
    埋め込みを `data/<分類>/<品種>.json`（同じキャプションのベクトルを1つにまとめた形、小数4桁）に書く。
    `owned` の分類のフォルダは先に消してから書く（表から消した品・品種の変わった品が残らない）。"""
    import shutil
    for cid in owned:
        for top in ("images", "data"):
            if (MOD / top / cid).exists():
                shutil.rmtree(MOD / top / cid)
    model = MiniLM()
    cache = {}
    groups = {}
    for e in entries:
        cid = category_of(e)
        assert cid in owned, (e["key"], cid)
        out = MOD / "images" / cid / "Assets" / "images" / "item_candidates_dark" / e["folder"] / (e["key"] + ".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        save_palette(made[e["key"]], out)
        groups.setdefault((cid, e["folder"]), []).append(e)
    counts = {}
    for (cid, folder), group in sorted(groups.items()):
        captions = sorted({e["caption"] for e in group})
        for c in captions:
            if c not in cache:
                cache[c] = [round(float(x), 4) for x in model.embed(c)]
        pos = {c: i for i, c in enumerate(captions)}
        data = MOD / "data" / cid
        data.mkdir(parents=True, exist_ok=True)
        json.dump({"index": {e["key"]: pos[e["caption"]] for e in group},
                   "vectors": [cache[c] for c in captions]},
                  open(data / (folder + ".json"), "w", encoding="utf-8"), separators=(",", ":"))
        counts[cid] = counts.get(cid, 0) + len(group)
    for cid in owned:
        print("%-22s %4d件" % (cid, counts.get(cid, 0)))
EMB_DIR = Path(r"C:\Program Files\Epic Games\Instantaleq6Ve7\Data\item_embeddings")

SIZES = {"small_weapon": (32, 32), "medium_weapon": (32, 64),
         "large_weapon": (32, 96), "long_weapon": (32, 128),
         "throwable_weapon": (32, 32)}
PREF = {"small": "small_weapon", "medium": "medium_weapon",
        "large": "large_weapon", "long": "long_weapon",
        "throwable": "throwable_weapon"}
# 縦長の箱の高さに対して絵が 75% 以上に届く箱を、希望の箱から小さいほうへ探す
FILL = 0.75
OUTLINE = (16, 14, 18, 255)
# 一枚絵の置き場所（リポジトリの assets/item_images。環境変数 ITEM_IMAGE_ASSETS か各道具の --sheets で替えられる）
ASSET_ROOT = Path(os.environ.get("ITEM_IMAGE_ASSETS") or (REPO / "assets" / "item_images"))
# 一枚絵の呼び名 → 置き場所（ASSET_ROOT の下の相対パス）。categories.json の source そのまま
SHEETS = {c["sheet"]: c["source"] for c in CATEGORIES}
# 黒背景の一枚絵で、これより明るい画素を絵とみなす（背景は 0）
SHEET_THRESHOLD = 12


# ---- 共通 ---------------------------------------------------------------

def label(mask, diagonal=True):
    """連結成分。戻り値は (ラベルの配列, 個数)。"""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    steps = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)]
    if not diagonal:
        steps = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        n += 1
        lab[y0, x0] = n
        q = deque([(y0, x0)])
        while q:
            y, x = q.popleft()
            for dy, dx in steps:
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = n
                    q.append((ny, nx))
    return lab, n


def grow(mask, steps):
    """mask を steps の向きへ 1px ずつ広げる。端で折り返さない
    （np.roll で広げると、絵の一番下の行が一番上へ回って横棒になる）。"""
    h, w = mask.shape
    out = mask.copy()
    for dy, dx in steps:
        out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] |=             mask[max(-dy, 0):h + min(-dy, 0), max(-dx, 0):w + min(-dx, 0)]
    return out


CROSS = ((1, 0), (-1, 0), (0, 1), (0, -1))
RING = CROSS + ((1, 1), (1, -1), (-1, 1), (-1, -1))


def finish(canvas):
    """本体から離れた小さな点を消し、1px の暗い縁を付ける（ドット絵の輪郭）。"""
    lab, n = label(canvas[..., 3] > 0)
    if n:
        sizes = np.bincount(lab.ravel())[1:]
        for i, size in enumerate(sizes, 1):
            if size < max(4, 0.08 * sizes.max()):
                canvas[lab == i] = 0
    m = canvas[..., 3] > 0
    canvas[grow(m, CROSS) & ~m] = OUTLINE
    return canvas


def shrink(im, box_w, box_h, colors):
    """縁の分を空けて箱に収まるよう縮め、色数を落として真ん中に置く。"""
    s = min((box_w - 2) / im.width, (box_h - 2) / im.height)
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    # 透明の縁が黒く滲まないよう、色は前乗せで縮める
    a = np.asarray(im).astype(np.float32) / 255
    pre = Image.fromarray((np.dstack([a[..., :3] * a[..., 3:], a[..., 3:]]) * 255).round().astype(np.uint8))
    sm = np.asarray(pre.resize((nw, nh), Image.LANCZOS)).astype(np.float32) / 255
    al = sm[..., 3]
    rgb = np.where(al[..., None] > 1e-3, sm[..., :3] / np.maximum(al[..., None], 1e-3), 0)
    alpha = al >= 0.45
    q = Image.fromarray((np.clip(rgb, 0, 1) * 255).round().astype(np.uint8))
    q = q.quantize(colors=colors, method=Image.MEDIANCUT).convert("RGB")
    out = np.dstack([np.asarray(q), np.where(alpha, 255, 0).astype(np.uint8)])
    out[~alpha] = 0
    canvas = np.zeros((box_h, box_w, 4), np.uint8)
    ox, oy = (box_w - nw) // 2, (box_h - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = out
    return finish(canvas)


def muzzle_up(im):
    """細い側（銃口）が上になるよう、必要なら 180 度回す。"""
    a = np.asarray(im)[..., 3] > 128
    q = max(1, a.shape[0] // 5)
    return im.rotate(180) if a[:q].sum() > a[-q:].sum() else im


# ---- 作者が画像生成 AI で作った一枚絵 -----------------------------------------------

_big = {}


def sheet_of(folder, name):
    """一枚絵の色と、一枚絵全体での塊（ラベル）と、塊ごとの箱。"""
    if name not in _big:
        rgb = np.asarray(Image.open(Path(folder) / SHEETS[name]).convert("RGB"))
        lab, n = label(rgb.max(2) > SHEET_THRESHOLD)
        boxes = np.zeros((n + 1, 4), int)
        ys, xs = np.nonzero(lab)
        ids = lab[ys, xs]
        boxes[:, 0] = boxes[:, 1] = 1 << 30
        np.minimum.at(boxes[:, 0], ids, xs)
        np.minimum.at(boxes[:, 1], ids, ys)
        np.maximum.at(boxes[:, 2], ids, xs + 1)
        np.maximum.at(boxes[:, 3], ids, ys + 1)
        _big[name] = (rgb, lab, boxes)
    return _big[name]


def extract(folder, src):
    """一枚絵の箱から1丁を切り抜く。箱の中でいちばん大きい塊と、その塊の箱にほぼ収まる塊だけを取る。
    塊は一枚絵全体で数えるので、箱の線で切れた隣の銃は「はみ出す塊」として外れる。
    縁の暗い画素も 1px 取り込む。"""
    name, _, box = src.partition("@")
    x0, y0, x1, y1 = map(int, box.split(","))
    rgb_all, lab_all, boxes = sheet_of(folder, name)
    lab = lab_all[y0:y1, x0:x1]
    ids, counts = np.unique(lab[lab > 0], return_counts=True)
    main = int(ids[np.argmax(counts)])
    bx0, by0, bx1, by1 = boxes[main]
    bx0, by0, bx1, by1 = bx0 - 6, by0 - 6, bx1 + 6, by1 + 6
    keep = np.zeros(lab.shape, bool)
    for i in ids:
        cx0, cy0, cx1, cy1 = boxes[i]
        inside = cx0 >= bx0 and cy0 >= by0 and cx1 <= bx1 and cy1 <= by1
        if i == main or inside:
            keep |= lab == i
    rgb = rgb_all[y0:y1, x0:x1]
    return Image.fromarray(np.dstack([rgb, np.where(grow(keep, RING), 255, 0).astype(np.uint8)]))


def stand_up(im, angle):
    """angle 度回して銃口を上にし、±20 度の中で幅が最小になる角度に寄せる（箱に一番長く入る向き）。
    一枚絵の銃はほぼ水平に描かれていて、斜めに見えるのは見下ろした描き方のため。
    銃身の線で角度を決めると、かえって傾く。"""
    small = im.copy()
    small.thumbnail((300, 300))

    def width(d):
        r = small.rotate(angle + d, resample=Image.BILINEAR, expand=True)
        bb = r.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
        return bb[2] - bb[0]
    best = min((d / 2 for d in range(-40, 41)), key=lambda d: (width(d), abs(d)))
    im = im.rotate(angle + best, resample=Image.BICUBIC, expand=True) if best else im.rotate(angle, expand=True)
    return im.crop(im.getbbox())


def choose_folder(pref, im):
    """希望の箱から小さいほうへ、絵が箱の高さの FILL 以上に届く箱を選ぶ。正方形の箱（小型・投擲）はそのまま。"""
    if pref in ("small", "throwable"):
        return PREF[pref]
    order = ["medium_weapon", "large_weapon", "long_weapon"]
    aspect = im.height / im.width
    for folder in reversed(order[:order.index(PREF[pref]) + 1]):
        box_w, box_h = SIZES[folder]
        if folder == "medium_weapon" or aspect >= FILL * box_h / box_w:
            return folder


# 色の名前（色相は度）。暗すぎる画素は輪郭と影なので数えない。
def color_names(im):
    a = np.asarray(im).astype(np.float32) / 255
    px = a[a[..., 3] > 0.5][:, :3]
    names = Counter()
    for r, g, b in px[:: max(1, len(px) // 4000)]:
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        h *= 360
        if v < 0.18:
            continue
        if s > 0.5 and v > 0.75 and 90 <= h < 345:
            names["glow:" + ("green" if h < 170 else "cyan" if h < 200 else "blue" if h < 250
                             else "purple" if h < 300 else "magenta")] += 1
        elif s < 0.3:
            names["black" if v < 0.4 else "grey" if v < 0.7 else "white"] += 1
        elif 5 <= h < 50 and v >= 0.6 and s < 0.5:
            names["tan"] += 1
        elif 38 <= h < 60 and s >= 0.35:
            names["brass"] += 1
        elif 5 <= h < 40 and v < 0.8:
            names["wood"] += 1
        elif 60 <= h < 100:
            names["olive green"] += 1
        elif h < 5 or h >= 345:
            names["red"] += 1
        elif h < 40:
            names["orange"] += 1
        elif h < 170:
            names["green"] += 1
        elif h < 250:
            names["blue"] += 1
        elif h < 300:
            names["purple"] += 1
        else:
            names["magenta"] += 1
    return names


def caption_for(entry, im):
    """種類の名前と測った色から、ゲームの外見文と同じ形の英語一文を組む。"""
    names = color_names(im)
    total = max(1, sum(names.values()))
    typ = entry["type"]
    glow_wanted = typ.endswith(" with neon lights") or " and neon lights" in typ
    typ = typ.replace(" with neon lights", "").replace(" and neon lights", "")
    noun, _, extra = typ.partition(" with ")
    extras = [extra] if extra else []
    body = [k for k, _ in names.most_common() if not k.startswith("glow:") and k != "wood"]
    color = body[0] if body else "black"
    # 投げ物の茶色は瓶や紙の色で、木の部位ではない
    if names["wood"] / total >= 0.12 and entry["pref"] != "throwable":
        extras.append("a wooden grip" if entry["pref"] == "small" else "a wooden stock")
    accent = [k for k in body[1:] if k not in ("black", "grey")
              and names[k] / total >= (0.25 if k == "white" else 0.12)]
    if accent:
        extras.append("%s details" % accent[0])
    glows = [k for k, _ in names.most_common() if k.startswith("glow:")]
    if glows and (glow_wanted or names[glows[0]] / total >= 0.02):
        extras.append("glowing %s lights" % glows[0][5:])
    # 種類の名前に色の語があれば、本体の色は付けない
    colored = any(w in noun.split() for w in ("brass", "black", "grey", "white", "tan", "red", "blue"))
    text = noun if colored else "%s %s" % (color, noun)
    if extras:
        text += " with " + (extras[0] if len(extras) == 1 else ", ".join(extras[:-1]) + " and " + extras[-1])
    article = "An" if text[0] in "aeiou" else "A"
    # 結びの語で銃・爆発物の側に寄せる（剣の外見文と重ならないように）
    return "%s %s, %s." % (article, text, "an explosive" if entry["pref"] == "throwable" else "a gun")


def build_sheet_entry(entry, folder_dir):
    im = extract(folder_dir, entry["src"])
    if "rotate" in entry:
        im = stand_up(im, entry["rotate"])
    else:
        # 縦向きの絵はもともと銃口が上（依頼の決まり）。細い側で上下を決めると、
        # 銃口の広がったラッパ銃を逆さにする
        im = im.crop(im.getbbox())
    entry["folder"] = choose_folder(entry["pref"], im)
    entry["caption"] = caption_for(entry, im)
    box_w, box_h = SIZES[entry["folder"]]
    return Image.fromarray(shrink(im, box_w, box_h, 32))


# ---- 本体 ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheets", default=str(ASSET_ROOT))
    ap.add_argument("--preview")
    args = ap.parse_args()

    entries = json.load(open(HERE / "weapon_asset.json", encoding="utf-8"))
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys)), "鍵が重なっている"
    assert all(k.startswith("gun_") for k in keys), "鍵は gun_ で始める"
    made = {}
    for e in entries:
        made[e["key"]] = build_sheet_entry(e, args.sheets)
    for e in entries:
        assert made[e["key"]].size == SIZES[e["folder"]], (e["key"], made[e["key"]].size)
    write_categories(entries, made, [c["id"] for c in CATEGORIES if c["group"] == "銃"])
    # 見直し用にキャプションの一覧を書く（配布物の外で見るためのもの）
    with open(HERE / "guns_captions.txt", "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write("%s\t%s\t%s\n" % (e["key"], e["folder"], e["caption"]))

    if args.preview:
        cols = 24
        rows = {}
        for e in entries:
            rows.setdefault(e["folder"], []).append(made[e["key"]])
        height = sum(((len(v) + cols - 1) // cols) * (SIZES[f][1] + 4) for f, v in rows.items())
        sheet = Image.new("RGBA", (cols * 36, height), (40, 40, 40, 255))
        y = 0
        for folder in SIZES:
            tiles = rows.get(folder, [])
            for i, t in enumerate(tiles):
                sheet.alpha_composite(t, ((i % cols) * 36 + 2, y + (i // cols) * (SIZES[folder][1] + 4) + 2))
            y += ((len(tiles) + cols - 1) // cols) * (SIZES[folder][1] + 4)
        sheet.resize((sheet.width * 2, sheet.height * 2), Image.NEAREST).save(args.preview)
    print("done")


if __name__ == "__main__":
    main()
