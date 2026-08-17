# -*- coding: utf-8 -*-
"""キャプション → 追加埋め込み辞書のビルドと検証。

- 入力: scratchpad/captions_*.json（{stem: 英語キャプション}）
- 出力: runtime/mods/128_item_image_variety/data/<folder>.json
  （素の辞書と同形式: {stem: [384 floats]}）
- 検証:
  1) 欠落一覧（missing_images.json）と完全一致すること
  2) 自己検索: 各キャプションで合成辞書を検索し、自分が top1 に来る率
     （キャプション同士が似すぎていないか＝新規画像が実際に選ばれ得るかの目安）
"""
import glob
import io
import json
import os
import sys

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
from minibert import MiniLM

MOD_DATA = os.path.join(os.path.dirname(SP), "data")
EMB_DIR = r"C:\Program Files\Epic Games\Instantaleq6Ve7\Data\item_embeddings"

CAPTION_FILES = {
    "document": ["captions_document.json"],
    "creature_part": ["captions_creature_part_a.json", "captions_creature_part_b.json"],
    "mushroom": ["captions_mushroom.json"],
    "accessory": ["captions_accessory.json"],
}

missing = json.load(open(os.path.join(SP, "missing_images.json"), encoding="utf-8"))
model = MiniLM()
os.makedirs(MOD_DATA, exist_ok=True)

for folder, files in CAPTION_FILES.items():
    captions = {}
    for f in files:
        captions.update(json.load(open(os.path.join(SP, f), encoding="utf-8")))
    expected = {os.path.splitext(os.path.basename(p))[0] for p in missing[folder]}
    got = set(captions)
    assert got == expected, (folder, sorted(expected - got)[:5], sorted(got - expected)[:5])

    out = {}
    for stem, cap in captions.items():
        v = model.embed(cap)
        out[stem] = [float(x) for x in v]
    path = os.path.join(MOD_DATA, folder + ".json")
    json.dump(out, open(path, "w", encoding="utf-8"))

    # 自己検索: 合成辞書（素+追加）の中で、自分のキャプションが自分を引くか
    base = json.load(open(os.path.join(EMB_DIR, folder + ".json"), encoding="utf-8"))
    merged = dict(base)
    merged.update(out)
    keys = list(merged)
    m = np.array([merged[k] for k in keys], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    hit = 0
    for stem, cap in captions.items():
        v = np.array(out[stem], dtype=np.float32)
        v /= np.linalg.norm(v)
        if keys[int(np.argmax(m @ v))] == stem:
            hit += 1
    size_kb = os.path.getsize(path) // 1024
    print(f"{folder}: {len(out)}件 -> {path} ({size_kb}KB)  自己検索 top1 {hit}/{len(out)}")
print("done")
