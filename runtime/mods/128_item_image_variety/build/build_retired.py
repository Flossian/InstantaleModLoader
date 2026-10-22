# -*- coding: utf-8 -*-
"""外した絵の付け替え先の表 `data/retired.json` を書く。

描き直しで一枚絵を外すと、その鍵の絵は消える（`build_guns.write_categories`）。
ゲームは品に絵の相対パス（`image_src`）を持たせてセーブするので、既にその絵が付いた品は絵が見つからず白い枠になる。
本体（`item_image_variety.py`）は、絵の見つからない品を見つけたらこの表で今ある絵に付け替える。

表は `{"<品種>/<外した鍵>": "<今ある鍵>"}`。外した鍵は、git の履歴にある分類の辞書（`data/<分類>/<品種>.json`）に
一度でも載って、今の辞書に無い鍵。付け替え先は、同じ品種の今ある絵のうち、辞書のベクトル（キャプションの埋め込み）が
いちばん近い絵。外した鍵の最後の分類が今もあれば、その分類の中から選ぶ。

    python build_retired.py

一枚絵を外したら、ほかの build_*.py の後で回す（git に載った版の辞書を読むので、外す前の版をコミットしてから）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MOD = HERE.parent
REPO = MOD.parents[2]
REL = "runtime/mods/128_item_image_variety/data/"


def git(*args):
    return subprocess.run(("git",) + args, cwd=REPO, capture_output=True, check=True).stdout


def vectors_of(blob):
    """辞書1つの {鍵: ベクトル}。まとめた形（index と vectors）にも、鍵ごとの形にも備える。"""
    data = json.loads(blob)
    if isinstance(data, dict) and "index" in data and "vectors" in data:
        return {key: data["vectors"][i] for key, i in data["index"].items()}
    return data if isinstance(data, dict) else {}


def entries(path_text):
    """`data/<分類>/<品種>.json` の (分類, 品種)。分類の無い同梱の追加辞書（素の絵）は None。"""
    parts = path_text[len(REL):].split("/")
    if len(parts) != 2 or not parts[1].endswith(".json"):
        return None
    return parts[0], parts[1][:-5]


def main():
    # 今の辞書
    now = {}          # 品種 -> {鍵: (分類, ベクトル)}
    for cat_dir in sorted((MOD / "data").iterdir()):
        if not cat_dir.is_dir():
            continue
        for f in sorted(cat_dir.glob("*.json")):
            for key, vec in vectors_of(f.read_text(encoding="utf-8")).items():
                now.setdefault(f.stem, {})[key] = (cat_dir.name, vec)
    current = {(slot, key) for slot, table in now.items() for key in table}

    # 履歴の辞書（古い版から順に読み、新しい版の値で上書きする）
    gone = {}         # (品種, 鍵) -> (最後の分類, ベクトル)
    commits = git("log", "--reverse", "--format=%H", "--", REL).decode().split()
    for commit in commits:
        for path_text in git("ls-tree", "-r", "--name-only", commit, "--", REL).decode().splitlines():
            where = entries(path_text)
            if where is None:
                continue
            cat, slot = where
            for key, vec in vectors_of(git("show", "{}:{}".format(commit, path_text))).items():
                if (slot, key) not in current:
                    gone[(slot, key)] = (cat, vec)

    out = {}
    for (slot, key), (cat, vec) in sorted(gone.items()):
        table = now.get(slot)
        if not table:
            continue
        keys = sorted(table)
        same = [k for k in keys if table[k][0] == cat]
        pool = same or keys
        mat = np.array([table[k][1] for k in pool], dtype=np.float32)
        mat /= np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
        q = np.array(vec, dtype=np.float32)
        q /= max(float(np.linalg.norm(q)), 1e-12)
        out["{}/{}".format(slot, key)] = pool[int(np.argmax(mat @ q))]
    path = MOD / "data" / "retired.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("retired {} keys from {} commits".format(len(out), len(commits)))


if __name__ == "__main__":
    sys.exit(main())
