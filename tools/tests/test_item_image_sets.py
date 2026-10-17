# -*- coding: utf-8 -*-
"""`128_item_image_variety` の足した絵。分類の一覧・表・埋め込み・絵が揃っているか。

- 分類（`categories.json`。作者の元画像1枚ごと）の並びは本体の `IMAGE_SETS` と同じで、設定の名前・既定値も一致する。
  既定値は本体の `MODES` のどれか。mod.json の settings にも同じ名前・既定値・選べる値で載る
- 表（`build/weapon_asset.json`・`build/medieval.json`・`build/magic.json`・`build/sf.json`・`build/items.json`・`build/wear.json`）の
  鍵は重ならず、1件ずつどの分類に入るかが決まる（一枚絵の名前）。胴の品は服か胴鎧の片方だけに置く
- 分類ごとの辞書 `data/<分類>/<部位>.json`（まとめた形 {"index", "vectors"}、384 次元）の鍵を合わせると、
  その分類に入る表の鍵とちょうど同じ。鍵ごとに `images/<分類>/Assets/images/item_candidates_dark/<部位>/<鍵>.png` があり、
  寸法は部位で決まる（武器は小型 32x32・中型 32x64・大型 32x96・長物 32x128・投擲 32x32、盾・胴鎧と防具類と、ほかの組の頭・服・手・脚は 64x64、
  それ以外（中世の服と矢筒、消耗品と素材・装身具）は 32x32）。絵のフォルダに辞書に無い絵は残っていない
- 分類ごとの元の一枚絵（`categories.json` の `source`）が `assets/item_images/` に在る
- `images/` と `data/` の直下のフォルダは分類だけ（分類の外に置いた絵は探し先に入らない）
- 実行時に `images/<分類>` を Kivy の探し先へ足している

Pillow を使わない（CI に無い）。寸法は PNG の IHDR を読む。
"""
import ast
import json
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, os.pardir, os.pardir, "runtime", "mods", "128_item_image_variety")
WEAPON = {"small_weapon": (32, 32), "medium_weapon": (32, 64), "large_weapon": (32, 96),
          "long_weapon": (32, 128), "throwable_weapon": (32, 32)}


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR", path
    return struct.unpack(">II", head[16:24])


def load(*parts):
    return json.load(open(os.path.join(MOD, *parts), encoding="utf-8"))


source = open(os.path.join(MOD, "item_image_variety.py"), encoding="utf-8").read()
tree = {node.targets[0].id: node.value for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
image_sets = ast.literal_eval(tree["IMAGE_SETS"])
modes = ast.literal_eval(tree["MODES"])
assert "resource_add_path" in source and '"images"' in source, "images/<分類> を探し先に足していない"

cats = load("categories.json")
ASSETS = os.path.join(HERE, os.pardir, os.pardir, "assets", "item_images")
lost = [c["source"] for c in cats if not os.path.isfile(os.path.join(ASSETS, c["source"]))]
assert not lost, ("assets/item_images に元の一枚絵が無い", lost)
assert [(c["id"], c["setting"]) for c in cats] == list(image_sets), "categories.json と IMAGE_SETS が違う"
manifest = load("mod.json")
for c in cats:
    assert c["default"] in modes, c
    assert ast.literal_eval(tree[c["setting"]]) == c["default"], c["setting"]
    decl = manifest["settings"][c["setting"]]
    assert decl["default"] == c["default"] and decl["values"] == list(modes), c["setting"]

by_sheet = {c["sheet"]: c["id"] for c in cats}
rows = load("build", "weapon_asset.json") + load("build", "medieval.json") \
    + load("build", "magic.json") + load("build", "sf.json") + load("build", "items.json") + load("build", "wear.json")
keys = [e["key"] for e in rows]
assert len(keys) == len(set(keys)), "鍵が重なっている"
torso = {}
for e in rows:
    if e.get("pref") in ("clothing", "body_armor"):
        torso.setdefault(e["src"], set()).add(e["pref"])
both = sorted(src for src, prefs in torso.items() if len(prefs) > 1)
assert not both, ("同じ絵を服と胴鎧の両方に置いている", both[:5])
expected = {}
for e in rows:
    sheet = e["src"].partition("@")[0]
    expected.setdefault(by_sheet[sheet], set()).add(e["key"])

ids = {c["id"] for c in cats}
for top in ("images", "data"):
    extra = set(os.listdir(os.path.join(MOD, top))) - ids
    if top == "data":
        extra = {n for n in extra if not n.endswith(".json")}   # 素の辞書に無かった305枚の追加辞書
    assert not extra, (top, sorted(extra))

for c in cats:
    cid = c["id"]
    images = os.path.join(MOD, "images", cid, "Assets", "images", "item_candidates_dark")
    got = set()
    for fname in os.listdir(os.path.join(MOD, "data", cid)):
        folder = fname[:-5]
        blob = load("data", cid, fname)
        assert set(blob) == {"index", "vectors"}, (cid, fname)
        assert all(len(v) == 384 for v in blob["vectors"])
        assert all(0 <= i < len(blob["vectors"]) for i in blob["index"].values())
        on_disk = {n[:-4] for n in os.listdir(os.path.join(images, folder)) if n.endswith(".png")}
        assert on_disk == set(blob["index"]), (cid, folder, sorted(on_disk ^ set(blob["index"]))[:5])
        for key in blob["index"]:
            assert key not in got, (cid, key)
            got.add(key)
            size = png_size(os.path.join(images, folder, key + ".png"))
            if folder in WEAPON:
                assert size == WEAPON[folder], (cid, key, size)
            elif (cid.startswith("wear_") or folder in ("shield", "body_armor")
                  or (not cid.startswith("med_") and folder in ("clothing", "headgear", "gauntlets", "legwear"))):
                assert size == (64, 64), (cid, key, size)
            else:
                assert size == (32, 32), (cid, key, size)
    assert got == expected.get(cid, set()), (cid, sorted(got ^ expected.get(cid, set()))[:5])

print("ok")
