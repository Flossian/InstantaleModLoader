# -*- coding: utf-8 -*-
"""銃の絵 → ゲームの寸法の絵と追加埋め込み辞書。

入力は表 weapon_asset.json（README.md の「銃の絵」）:

- 画像生成 AI で作った一枚絵（黒背景。22枚）。1枚ごとに 鍵・品種の希望（pref）・
  一枚絵の上の箱・回転・種類の名前。品種は縦横比で決め、キャプションは種類と絵から測った色で組む。
  一枚絵はリポジトリの assets/item_images に置き、配布物（zip）には入れない。置き場所は引数 --sheets か
  環境変数 ITEM_IMAGE_ASSETS で替えられる。銃の一枚絵は `guns/`（categories.json の source）

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
import functools
import io
import json
import os
import re
import sys
from collections import Counter, deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

try:  # 塊数えの速い実装（無くても作れる。label を参照）
    import cv2 as _cv2
except ImportError:
    _cv2 = None
try:
    from scipy import ndimage as _ndimage
except ImportError:
    _ndimage = None

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from minibert import MiniLM  # noqa: E402

MOD = HERE.parent
REPO = MOD.parents[2]
# 分類（`../categories.json`。1分類が元画像を何枚か持つ。`sources`）。絵と辞書は分類ごとのフォルダに書く
CATEGORIES = json.load(open(MOD / "categories.json", encoding="utf-8"))
CATEGORY_OF_SHEET = {s["sheet"]: c["id"] for c in CATEGORIES for s in c["sources"]}


def owned_categories(entries):
    """表の品が入る分類（道具が受け持つ分類。まとまりの名前では決めない）。"""
    return sorted({category_of(e) for e in entries})


def category_of(entry):
    """表の1件の分類。元素材の一枚絵（`<一枚絵>@箱`）の分類。"""
    return CATEGORY_OF_SHEET[entry["src"].partition("@")[0]]


def save_palette(im, path):
    """色の数だけのパレットを持つ PNG で書く（画素はそのまま。255色に減色した絵なので、透明と合わせて必ず収まる）。
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
    1つの分類にほかの道具の品が入ることがある（防具の分類に、中世の表の盾と胴鎧や、消耗品の表の籠手と装甲が入る）。
    `owned` の分類から先に消すのは、この道具の品（鍵の頭が entries の鍵の頭と同じもの）の絵と埋め込みだけで、
    ほかの道具の品は残して書き足す（表から消した品・品種の変わった品は残らない）。"""
    mine = tuple(sorted({e["key"].split("_", 1)[0] + "_" for e in entries}))
    # どの表にも無い鍵（表から外した一枚絵の品）も消す。鍵の頭ごと表から消えると mine では拾えなかった
    listed = set()
    for name in ("weapon_asset.json", "medieval.json", "magic.json", "sf.json", "items.json", "wear.json"):
        path = HERE / name
        if path.exists():
            listed |= {row["key"] for row in json.load(open(path, encoding="utf-8"))}

    def ours(key):
        return key.startswith(mine) or key not in listed
    kept = {}   # (分類, 品種) -> {ほかの道具の品の鍵: ベクトル}
    for cid in owned:
        root = MOD / "images" / cid
        if root.exists():
            for png in root.rglob("*.png"):
                if ours(png.stem):
                    png.unlink()
        data = MOD / "data" / cid
        if data.exists():
            for path in data.glob("*.json"):
                blob = json.load(open(path, encoding="utf-8"))
                others = {k: blob["vectors"][i] for k, i in blob["index"].items() if not ours(k)}
                if others:
                    kept[(cid, path.stem)] = others
                path.unlink()
    groups = {}
    for e in entries:
        cid = category_of(e)
        assert cid in owned, (e["key"], cid)
        out = MOD / "images" / cid / "Assets" / "images" / "item_candidates_dark" / e["folder"] / (e["key"] + ".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        save_palette(made[e["key"]], out)
        groups.setdefault((cid, e["folder"]), []).append(e)
    counts = {}
    # 埋め込みはキャプションごとに独立なので、まとめて並べて作る（parallel_map）
    unique = sorted({e["caption"] for e in entries})
    cache = dict(zip(unique, parallel_map(embed_caption, unique)))
    for cid, folder in sorted(set(groups) | set(kept)):
        group = groups.get((cid, folder), [])
        vectors, pos, index = [], {}, {}
        for c in sorted({e["caption"] for e in group}):
            pos[c] = len(vectors)
            vectors.append(cache[c])
        for e in group:
            index[e["key"]] = pos[e["caption"]]
        for key, vec in sorted(kept.get((cid, folder), {}).items()):
            index[key] = len(vectors)
            vectors.append(vec)
        data = MOD / "data" / cid
        data.mkdir(parents=True, exist_ok=True)
        json.dump({"index": index, "vectors": vectors},
                  open(data / (folder + ".json"), "w", encoding="utf-8"), separators=(",", ":"))
        counts[cid] = counts.get(cid, 0) + len(group)
    # 品の無くなったフォルダを消す
    for cid in owned:
        for top in ("images", "data"):
            root = MOD / top / cid
            if root.exists():
                for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                    if not any(d.iterdir()):
                        d.rmdir()
                if not any(root.iterdir()):
                    root.rmdir()
    for cid in owned:
        print("%-22s %4d件" % (cid, counts.get(cid, 0)))


SIZES = {"small_weapon": (32, 32), "medium_weapon": (32, 64),
         "large_weapon": (32, 96), "long_weapon": (32, 128),
         "throwable_weapon": (32, 32)}
PREF = {"small": "small_weapon", "medium": "medium_weapon",
        "large": "large_weapon", "long": "long_weapon",
        "throwable": "throwable_weapon"}
# 武器の絵は素の絵の寸法の SCALE 倍。2倍（ゲームがマス1つを描く画面の大きさ）で作るとゲームの中で素の絵より
# きめ細かく浮き、素の絵と同じ寸法（1）では 64x64 の防具類よりきめが粗く見えたので、間の 1.5 倍にした（VERIFICATION.md §3.26）。
# 防具類と消耗品は倍にしない。呼ぶ側は素の絵の寸法（SIZES・game_size）と倍率を渡し、shrink が倍にする。
# 占めるマスの数は品の種類で決まり、絵の寸法では変わらない
SCALE = 1.5
# 消耗品・素材などのアイテム類（build_medieval.ITEMS の部位）の倍率。武器と同じ 1.5 倍を試す（VERIFICATION.md §3.26）。
# 1 に戻すと素の絵と同じ寸法になる。防具類・盾・装身具は倍にしない
ITEM_SCALE = 1.5
GAME_SIZES = dict(SIZES, shield=(64, 64), body_armor=(64, 64))


def game_size(folder):
    """その部位の素の絵の寸法（武器は SIZES、盾と胴鎧は 64x64、ほかは 32x32）。"""
    return GAME_SIZES.get(folder, (32, 32))


def scaled(n, scale=SCALE):
    """素の絵の寸法 n の、倍率 scale で出す絵での画素数（端数は四捨五入）。"""
    return int(round(n * scale))


# 縦長の箱の高さに対して絵が 75% 以上に届く箱を、希望の箱から小さいほうへ探す
FILL = 0.75
# 一枚絵の置き場所（リポジトリの assets/item_images。環境変数 ITEM_IMAGE_ASSETS か各道具の --sheets で替えられる）
ASSET_ROOT = Path(os.environ.get("ITEM_IMAGE_ASSETS") or (REPO / "assets" / "item_images"))
# 一枚絵の呼び名 → 置き場所（ASSET_ROOT の下の相対パス）。categories.json の sources の source そのまま
SHEETS = {s["sheet"]: s["source"] for c in CATEGORIES for s in c["sources"]}
# 黒背景の一枚絵で、これより明るい画素を絵とみなす（背景は 0）
SHEET_THRESHOLD = 12


# ---- 共通 ---------------------------------------------------------------

def parallel_map(func, items):
    """items を func に通した結果のリスト（同じ並び）。品ごとの処理は互いに独立なので、プロセスを並べて流す。
    func はモジュールの一番外の関数（か functools.partial）で、結果は pickle できる値にする。
    本数は環境変数 ITEM_IMAGE_WORKERS（既定はコア数 - 1。1 で並べない＝切り分け用）。
    numpy の行列計算は各プロセスの中で1スレッドに抑える（抑えないと numpy 自身の並列と重なって遅くなる）。"""
    items = list(items)
    workers = int(os.environ.get("ITEM_IMAGE_WORKERS") or max(1, (os.cpu_count() or 2) - 1))
    if workers <= 1 or len(items) < 8:
        return [func(x) for x in items]
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "1")
    from concurrent.futures import ProcessPoolExecutor
    # 並びのまま塊で渡す（表は一枚絵ごとに並んでいるので、1本のプロセスが同じ一枚絵を続けて切り、塊数えの控えが効く）
    chunk = max(1, len(items) // (workers * 4))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(func, items, chunksize=chunk))


_model = [None]


def embed_caption(caption):
    """キャプション1つの埋め込み（小数4桁）。プロセスごとにモデルを1回読む。"""
    if _model[0] is None:
        _model[0] = MiniLM()
    return [round(float(x), 4) for x in _model[0].embed(caption)]


def label(mask, diagonal=True):
    """連結成分。戻り値は (ラベルの配列, 個数)。ラベルは 1 から（番号の付け方は使う側が当てにしない）。
    OpenCV（cv2）か scipy があればその C の実装を使う（作り直しの時間の約9割が素の Python の塊数えだった。
    VERIFICATION.md §3.26）。どちらも無ければ素の Python で数える。"""
    mask = np.asarray(mask, bool)
    if _cv2 is not None:
        n, lab = _cv2.connectedComponents(mask.astype(np.uint8), connectivity=8 if diagonal else 4)
        return lab.astype(np.int32), int(n) - 1
    if _ndimage is not None:
        lab, n = _ndimage.label(mask, structure=np.ones((3, 3), int) if diagonal else None)
        return lab.astype(np.int32), int(n)
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
    """本体から離れた小さな点を消す。
    暗い縁は付けない（付けると太く重い絵になり、細い線と細かい陰影のゲームの素材より粗く見えた。VERIFICATION.md §3.26）。"""
    lab, n = label(canvas[..., 3] > 0)
    if n:
        sizes = np.bincount(lab.ravel())[1:]
        for i, size in enumerate(sizes, 1):
            if size < max(4, 0.08 * sizes.max()):
                canvas[lab == i] = 0
    return canvas


def hole_max(scale):
    """埋める穴の大きさの上限（出す絵の画素数）。2倍の寸法で決めた 6px を寸法の2乗で縮めた値（1倍で 2px、1.5 倍で 3px）。
    引き金の輪や輪の内側のような、もともと空いている所はこれより大きい。"""
    return max(1, round(6 / (2 / scale) ** 2))


def spread_colors(rgb, known, passes):
    """known でない画素の色を、known な4近傍の色の平均で passes 段だけ外へ広げた色の配列（広がらない所は元のまま）。"""
    rgb = rgb.copy()
    known = known.copy()
    h, w = known.shape
    for _ in range(passes):
        acc = np.zeros_like(rgb)
        cnt = np.zeros(known.shape, np.float32)
        for dy, dx in CROSS:
            sl_dst = (slice(max(dy, 0), h + min(dy, 0)), slice(max(dx, 0), w + min(dx, 0)))
            sl_src = (slice(max(-dy, 0), h + min(-dy, 0)), slice(max(-dx, 0), w + min(-dx, 0)))
            k = known[sl_src]
            acc[sl_dst] += rgb[sl_src] * k[..., None]
            cnt[sl_dst] += k
        new = ~known & (cnt > 0)
        rgb[new] = acc[new] / cnt[new][:, None]
        known |= new
    return rgb


def small_holes(opaque, limit):
    """外（絵の端の外）とつながらない透明の塊のうち、limit 画素以下のもの。"""
    lab, n = label(np.pad(~opaque, 1, constant_values=True), diagonal=False)
    lab = lab[1:-1, 1:-1]
    outside = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) | {0}
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    holes = np.zeros(opaque.shape, bool)
    for i in range(1, n + 1):
        if i not in outside and sizes[i] <= limit:
            holes |= lab == i
    return holes


def shrink(im, box_w, box_h, colors, scale=1):
    """縁の分を空けて箱に収まるよう縮め、輪郭を軽く立ててから色数を落とし、真ん中に置く。
    box_w・box_h は素の絵の寸法で、出す絵はその scale 倍（武器は SCALE、ほかは 1）。
    ゲームの素材は 1枚に 200〜700 色あり、32色まで落とすと陰影が段になって粗く見えた。呼ぶ側は 255 色で呼ぶ（透明の色と合わせてパレットの 256 色に収まる）。"""
    box_w, box_h = scaled(box_w, scale), scaled(box_h, scale)
    s = min((box_w - 2 * scale) / im.width, (box_h - 2 * scale) / im.height)
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    # 透明の縁が黒く滲まないよう、色は前乗せで縮める
    a = np.asarray(im).astype(np.float32) / 255
    pre = Image.fromarray((np.dstack([a[..., :3] * a[..., 3:], a[..., 3:]]) * 255).round().astype(np.uint8))
    sm = np.asarray(pre.resize((nw, nh), Image.LANCZOS)).astype(np.float32) / 255
    al = sm[..., 3]
    rgb = np.clip(np.where(al[..., None] > 1e-3, sm[..., :3] / np.maximum(al[..., None], 1e-3), 0), 0, 1)
    alpha = al >= 0.45
    # 透明の色を不透明な隣の色で外へ広げる。黒のままだと、輪郭を立てたときに縁と黒の差を強めて白い粒が並んだ
    rgb = np.where(alpha[..., None], rgb, spread_colors(rgb, alpha, 4))
    # 外とつながらない小さな透明の穴（一枚絵の真っ黒に近い影を背景とみなした跡）を埋める。2倍の寸法で点の抜けとして見えた
    alpha = alpha | small_holes(alpha, hole_max(scale))
    q = Image.fromarray((rgb * 255).round().astype(np.uint8))
    # 10分の1ほどに縮めると細部が溶けるので、縮めた後に輪郭を軽く立てる（強くすると白くにじむ）
    q = q.filter(ImageFilter.UnsharpMask(radius=1.0, percent=80, threshold=0))
    q = q.quantize(colors=colors, method=Image.MEDIANCUT).convert("RGB")
    out = np.dstack([np.asarray(q), np.where(alpha, 255, 0).astype(np.uint8)])
    out[~alpha] = 0
    canvas = np.zeros((box_h, box_w, 4), np.uint8)
    ox, oy = (box_w - nw) // 2, (box_h - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = out
    return finish(canvas)


def peel_glow(im, scale, width=3.0, strong=False):
    """光る武器の外側の暗いにじみをはがす。一枚絵の光る刃や銃は、周りに光と同じ色合いの暗いにじみが描かれていて、
    明るさで切り抜くと縮めた後に黒い波の縁として残った（VERIFICATION.md §3.26）。
    光の芯（橙・黄・金を除く、明るさ 0.7 超・鮮やかさ 0.55 超の画素）が 2% に満たない品は触らない。
    芯のいちばん多い色相 ±35 度で、明るさ 0.2 未満・鮮やかさ 0.35 超の画素を、透明に接するものから順に、
    出す絵の約 width px 分（scale は出す絵 / 元の絵の比）まではがす。黒い本体（色の無い黒）は残る。
    芯と同じ色の暗い部分を本体に持つ品（暗い赤の弓）も削るので、表の `halo: false` で外す。
    strong（表の `glow: true`）は、刃の片側に幅の広い黒い帯が描かれた光る刃のため。白っぽい光（明るさ 0.8 超なら鮮やかさを問わない）も芯に数え、
    はがす幅を2倍、真っ黒を外す芯からの距離をさらに2倍にする。鋼の刃と色で見分けられないので、品を表で指す。"""
    a = np.asarray(im).copy()
    f = a[..., :3].astype(np.float32) / 255
    op = a[..., 3] > 0
    mx, mn = f.max(2), f.min(2)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    hue = np.degrees(np.arctan2(np.sqrt(3) * (f[..., 1] - f[..., 2]), 2 * f[..., 0] - f[..., 1] - f[..., 2])) % 360
    # 橙・黄・金は真鍮や金の飾りと取り違えるので芯に数えないが、白に近いほど明るく鮮やかなもの（オレンジの光の刃・炎）は数える。
    # 数えないとオレンジの光の刃の黒いにじみが残った。明るさ 0.9 超・鮮やかさ 0.7 超の橙・黄を持つ武器 29品で、塗った銃と爆発物は変わらなかった
    warm = (hue >= 15) & (hue < 70)
    core = op & (mx > 0.7) & (sat > 0.55) & (~warm | ((mx > 0.9) & (sat > 0.7)))
    vivid = core.copy()
    if strong:
        core = core | (op & (mx > 0.8))
        width *= 2
    if core.sum() < 0.02 * op.sum():
        return im
    # 色の付いたにじみの色相は鮮やかな芯から（白っぽい光だけの品は色の付いたにじみを見ない）
    main = (np.argmax(np.bincount((hue[vivid] // 30).astype(int), minlength=12)) + 0.5) * 30 if vivid.any() else None
    passes = max(1, round(width / scale))
    # 芯から同じ距離の中の真っ黒（最大値 0.12 未満）も、にじみの外の太い黒枠として外す。
    # 2倍の寸法で光る刃の黒枠が太く見えた。芯の無い品の黒（刀の柄・弓）は触らない
    near = core.copy()
    for _ in range(passes * (2 if strong else 1)):
        p = np.pad(near, 1)
        near = near | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    halo = (mx < 0.12) & near
    if main is not None:
        halo |= (mx < 0.2) & (sat > 0.35) & (np.abs((hue - main + 180) % 360 - 180) < 35)
    for _ in range(passes):
        # 透明に接する画素（端の外も透明とみなす）
        pad = np.pad(op, 1)
        edge = op & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
        drop = edge & halo
        if not drop.any():
            break
        op &= ~drop
    a[..., 3] = np.where(op, 255, 0)
    return Image.fromarray(a)


def muzzle_up(im):
    """細い側（銃口）が上になるよう、必要なら 180 度回す。"""
    a = np.asarray(im)[..., 3] > 128
    q = max(1, a.shape[0] // 5)
    return im.rotate(180) if a[:q].sum() > a[-q:].sum() else im


# ---- 画像生成 AI で作った一枚絵 -----------------------------------------------

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


# 品の中に閉じた透明の塊を穴として残す、明るさの平均の上限。依頼の背景は #000000 で、一枚絵の背景は 0〜4、
# 本当に空いている所（弓の弦の内側・銃床の穴）は平均 0.6〜1.7、明るさで背景とみなした品の中の暗い影は平均 3 以上だった
OPEN_HOLE_MEAN = 2.5


def fill_enclosed(keep, rgb):
    """外周の背景とつながらない透明の塊のうち、中が背景より明るいもの（品の中の暗い影）を品の範囲に戻す。
    明るさだけで切ると、品の中の暗い影が穴になり、2倍の寸法で点の抜けとして見えた（VERIFICATION.md §3.26）。"""
    lab, n = label(np.pad(~keep, 1, constant_values=True), diagonal=False)
    lab = lab[1:-1, 1:-1]
    outside = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) | {0}
    m = rgb.max(2).astype(np.float32)
    sums = np.bincount(lab.ravel(), weights=m.ravel(), minlength=n + 1)
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    fill = np.zeros(n + 1, bool)
    for i in range(1, n + 1):
        if i not in outside and sums[i] / max(1, sizes[i]) >= OPEN_HOLE_MEAN:
            fill[i] = True
    return keep | fill[lab]


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
    keep = fill_enclosed(keep, rgb)
    return Image.fromarray(np.dstack([rgb, np.where(grow(keep, RING), 255, 0).astype(np.uint8)]))


def extract_whole(folder, src):
    """箱の中にほぼ収まる塊を全部取る（離れて描かれた1組の品や、本体から離れて描かれた照準器・弾倉）。
    縁の暗い画素も 1px 取り込む。表の `whole` が真の品に使う。"""
    name, _, box = src.partition("@")
    x0, y0, x1, y1 = map(int, box.split(","))
    rgb_all, lab_all, boxes = sheet_of(folder, name)
    lab = lab_all[y0:y1, x0:x1]
    keep = np.zeros(lab.shape, bool)
    for i in np.unique(lab[lab > 0]):
        cx0, cy0, cx1, cy1 = boxes[i]
        if cx0 >= x0 - 6 and cy0 >= y0 - 6 and cx1 <= x1 + 6 and cy1 <= y1 + 6:
            keep |= lab == i
    keep = fill_enclosed(keep, rgb_all[y0:y1, x0:x1])
    return Image.fromarray(np.dstack([rgb_all[y0:y1, x0:x1], np.where(grow(keep, RING), 255, 0).astype(np.uint8)]))


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


# 金や真鍮は茶色、銀は黒や白に測られる。名前にこの語がある品は、測った色を前に付けない（「brown gold ring」にしない）。
# 「gold-trimmed」のように語の一部のときは飾りの色なので、本体の色は付ける
METAL_COLOR = {"gold", "golden", "gilded", "gilt", "silver", "silvery", "brass", "bronze", "copper", "platinum"}


def names_metal_color(name):
    return bool(METAL_COLOR & set(name.lower().split()))


# 名前がこの色の語で始まる品（「red velvet coat」「navy beret」）も、測った色を前に付けない（「brown red velvet coat」にしない）。
# 色の語の前の light・dark などは飛ばす。「red-wrapped」のように語の一部のときは飾りの色なので数えない
COLOR_WORDS = {"grey", "gray", "white", "black", "beige", "tan", "brown", "red", "crimson", "scarlet", "orange", "yellow", "olive",
               "green", "teal", "cyan", "blue", "navy", "purple", "violet", "magenta", "pink", "khaki", "ivory"}


def names_own_color(name):
    """名前が自分の色を持つ品か（金属の色の語を含むか、色の語で始まる）。"""
    words = name.lower().split()
    while words and words[0] in ("light", "dark", "pale", "deep", "bright"):
        words = words[1:]
    return names_metal_color(name) or bool(words and words[0] in COLOR_WORDS)


# 対で数える品（名前の頭の最後の語）。「A brown leather gloves」にせず「A pair of brown leather gloves」にする
PAIRED = {"gloves", "gauntlets", "boots", "shoes", "sandals", "slippers", "greaves", "sabatons", "bracers", "vambraces", "cuisses", "chausses",
          "trousers", "pants", "breeches", "leggings", "shorts", "tights", "earrings", "glasses", "goggles", "spectacles", "binoculars",
          "scissors", "tongs", "pliers", "knuckles", "manacles", "guards", "socks", "stockings", "chopsticks",
          "headphones", "earmuffs", "earphones"}
# 冠詞を付けない複数の品（「Patched peasant clothes.」）
UNCOUNTED = {"clothes", "robes", "garments", "rags"}
# 母音字で始まっても子音で読む語（「A one-piece axe」）
CONSONANT_SOUND = ("one", "uni", "use", "usu", "eu", "ure", "ura")


def sentence(text):
    """外見文と同じ形の英文（冠詞＋本文、句点は付けない）。対の品は A pair of、数えない品は冠詞なし。"""
    head = text.split(" with ")[0].lower()
    words = head.split()
    if not re.match(r"(pair|pairs|set|bundle|pile|stack|handful|bunch|sack|string|cluster|row) of ", head) and words:
        if words[-1] in PAIRED:
            return "A pair of " + text
        if words[-1] in UNCOUNTED:
            return text[0].upper() + text[1:]
    low = text.lower()
    vowel = low[0] in "aeiou" and not low.startswith(CONSONANT_SOUND)
    return ("An " if vowel else "A ") + text


def add_with(text, phrase):
    """本文に「with …」を足す。本文にもう with があれば and でつなぐ（「with purple mist with a faint glow」にしない）。"""
    return text + (" and " if " with " in text else " with ") + phrase


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
    # 投げ物の茶色は瓶や紙の色で、木の部位ではない。表の `wood` が偽の品は茶色を数えない
    # （錆びた金属の茶色を木と測る一枚絵。木の部位は種類の名前に書く）
    wood = entry.get("wood", entry["pref"] != "throwable")
    if wood and names["wood"] / total >= 0.12:
        extras.append("a wooden grip" if entry["pref"] == "small" else "a wooden stock")
    accent = [k for k in body[1:] if k not in ("black", "grey")
              and names[k] / total >= (0.25 if k == "white" else 0.12)]
    if accent:
        extras.append("%s details" % accent[0])
    glows = [k for k, _ in names.most_common() if k.startswith("glow:")]
    if glows and (glow_wanted or names[glows[0]] / total >= 0.02):
        extras.append("glowing %s lights" % glows[0][5:])
    # 種類の名前に色の語があれば、本体の色は付けない
    colored = any(w in noun.split() for w in ("black", "grey", "white", "tan", "red", "blue")) or names_own_color(noun)
    text = noun if colored else "%s %s" % (color, noun)
    if extras:
        text += " with " + (extras[0] if len(extras) == 1 else ", ".join(extras[:-1]) + " and " + extras[-1])
    # 表の `melee` が真の品（企業の一揃いの警棒・刃物・大槌・クロスボウ）は銃の結びを付けない
    if entry.get("melee"):
        return "%s." % sentence(text)
    # 結びの語で銃・爆発物の側に寄せる（剣の外見文と重ならないように）
    return "%s, %s." % (sentence(text), "an explosive" if entry["pref"] == "throwable" else "a gun")


def build_sheet_entry(entry, folder_dir):
    im = (extract_whole if entry.get("whole") else extract)(folder_dir, entry["src"])
    if entry.get("diag"):
        # 縦に描かれた拳銃は、ゲームの小型と同じく銃口を右上へ 45 度に倒す（32x32 に大きく入る）
        im = stand_up(im, entry.get("rot", 0)).rotate(-45, resample=Image.BICUBIC, expand=True)
        im = im.crop(im.getbbox())
    elif "rotate" in entry:
        im = stand_up(im, entry["rotate"])
    else:
        # 縦向きの絵はもともと銃口が上（依頼の決まり）。細い側で上下を決めると、
        # 銃口の広がったラッパ銃を逆さにする
        im = im.crop(im.getbbox())
    entry["folder"] = choose_folder(entry["pref"], im)
    entry["caption"] = caption_for(entry, im)
    box_w, box_h = SIZES[entry["folder"]]
    if entry.get("halo", True):
        im = peel_glow(im, min((box_w - 2) / im.width, (box_h - 2) / im.height), strong=bool(entry.get("glow")))
    return Image.fromarray(shrink(im, box_w, box_h, 255, SCALE))


def sheet_entry_job(entry, sheets):
    """build_sheet_entry をプロセスの中で動かす口。戻り値は (絵の配列, 部位, キャプション)（表の行の書き換えを持ち帰る）。"""
    im = build_sheet_entry(entry, sheets)
    return np.asarray(im), entry["folder"], entry["caption"]


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
    for e, (arr, folder, caption) in zip(entries, parallel_map(functools.partial(sheet_entry_job, sheets=args.sheets), entries)):
        e["folder"], e["caption"] = folder, caption
        made[e["key"]] = Image.fromarray(arr)
    for e in entries:
        w, h = SIZES[e["folder"]]
        assert made[e["key"]].size == (scaled(w), scaled(h)), (e["key"], made[e["key"]].size)
    write_categories(entries, made, owned_categories(entries))
    # 見直し用にキャプションの一覧を書く（配布物の外で見るためのもの）
    with open(HERE / "guns_captions.txt", "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write("%s\t%s\t%s\n" % (e["key"], e["folder"], e["caption"]))

    if args.preview:
        cols = 24
        rows = {}
        for e in entries:
            rows.setdefault(e["folder"], []).append(made[e["key"]])
        step = scaled(32) + 4
        height = sum(((len(v) + cols - 1) // cols) * (scaled(SIZES[f][1]) + 4) for f, v in rows.items())
        sheet = Image.new("RGBA", (cols * step, height), (40, 40, 40, 255))
        y = 0
        for folder in SIZES:
            tiles = rows.get(folder, [])
            for i, t in enumerate(tiles):
                sheet.alpha_composite(t, ((i % cols) * step + 2, y + (i // cols) * (scaled(SIZES[folder][1]) + 4) + 2))
            y += ((len(tiles) + cols - 1) // cols) * (scaled(SIZES[folder][1]) + 4)
        sheet.save(args.preview)
    print("done")


if __name__ == "__main__":
    main()
