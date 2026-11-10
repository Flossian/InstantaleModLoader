# -*- coding: utf-8 -*-
r"""描き直しの絵の説明（埋め込み）と、絵ごとの色・材質の語の組を書く。

    python runtime/mods/128_item_image_variety/build/redraw/build_redraw_data.py

- `data\redraw\<品種>.json`: `images\redraw\<品種>\<鍵>.png` の描き直しを頼んだ一文（作業場の `refs\captions\<手本>.json`。
  写した絵は写しの元の一文。`refs\reused.json`）の埋め込み。形は足した絵の辞書と同じ {"index": {鍵: 番号}, "vectors": [...]}
  （同じ一文は1つのベクトルにまとめる）。本体は描き直しのある素の絵の説明を、これに替えて選ぶ
- `data\redraw\captions.json`: 埋め込んだ一文（{品種: {鍵: 一文}}）。測る道具と見直し用
- `data\tags.json`: 説明文の分かる絵の (色, 材質) の語の組。`own` は足した絵（`*_captions.txt`）と追加辞書の絵
  （`captions_<品種>*.json`）、`redraw` は描き直しの一文から。語の組の決まりは本体の `tags_of`

一文の無い描き直しは書かずに知らせる（その鍵は素の説明のまま選ばれ、表示だけ描き直しになる）。
"""
import glob
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(os.path.dirname(HERE))   # 128_item_image_variety
ROOT = os.path.normpath(os.path.join(MOD, os.pardir, os.pardir, os.pardir))
WORK = os.path.join(ROOT, "local", "item_image_redraw")
OUT = os.path.join(MOD, "images", "redraw")
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "runtime"))
import build_guns as b  # noqa: E402

spec = importlib.util.spec_from_file_location("iiv_build", os.path.join(MOD, "item_image_variety.py"))
body = importlib.util.module_from_spec(spec)
spec.loader.exec_module(body)


def load(path):
    return json.load(io.open(path, encoding="utf-8"))


def write(path, data):
    """1行に1項目で書く（作り直したときの git の差分を目で追えるように）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def write_vectors(path, index, vectors):
    """埋め込みの辞書。足した絵の辞書と同じ書き方（`build_guns.write_lines`。index の鍵1つ・vectors の1本が1行）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    b.write_lines(path, {"index": dict(sorted(index.items())), "vectors": vectors}, depth=2)


def main():
    captions = {}
    for path in glob.glob(os.path.join(WORK, "refs", "captions", "*.json")):
        slot = os.path.basename(path).rsplit("_", 1)[0]
        for key, text in load(path).items():
            captions[(slot, key)] = text
    reused_path = os.path.join(WORK, "refs", "reused.json")
    reused = {tuple(a.split("/", 1)): tuple(s.split("/", 1)) for a, s in load(reused_path).items()} if os.path.isfile(reused_path) else {}

    used, missing = {}, []
    for slot in sorted(os.listdir(OUT)):
        for name in sorted(os.listdir(os.path.join(OUT, slot))):
            if not name.endswith(".png"):
                continue
            key = name[:-4]
            text = captions.get((slot, key)) or captions.get(reused.get((slot, key), (None, None)))
            if text:
                used.setdefault(slot, {})[key] = text
            else:
                missing.append("{}/{}".format(slot, key))

    old = os.path.join(MOD, "data", body.REDRAW_DIRNAME)
    for path in glob.glob(os.path.join(old, "*.json")):
        os.remove(path)
    cache = {}
    for slot, table in used.items():
        index, vectors, row = {}, [], {}
        for key, text in sorted(table.items()):
            if text not in row:
                if text not in cache:
                    cache[text] = b.embed_caption(text)
                row[text] = len(vectors)
                vectors.append(cache[text])
            index[key] = row[text]
        write_vectors(os.path.join(old, slot + ".json"), index, vectors)
    write(os.path.join(old, "captions.json"), used)

    # 足した絵の説明文（鍵 → 文）と、追加辞書の絵の説明文（品種ごと）
    added = {}
    for path in glob.glob(os.path.join(os.path.dirname(HERE), "*captions*.txt")):
        for line in io.open(path, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                added[parts[0]] = parts[-1]
    own = {}
    for cat in load(os.path.join(MOD, "categories.json")):
        for path in glob.glob(os.path.join(MOD, "data", cat["id"], "*.json")):
            slot = os.path.basename(path)[:-5]
            for key in load(path)["index"]:
                text = body.tags_text(body.tags_of(added.get(key, "")))
                if text:
                    own.setdefault(slot, {})[key] = text
    for path in glob.glob(os.path.join(os.path.dirname(HERE), "captions_*.json")):
        name = os.path.basename(path)[len("captions_"):-5]
        slot = name if os.path.isfile(os.path.join(MOD, "data", name + ".json")) else name.rsplit("_", 1)[0]
        for key, text in load(path).items():
            tags = body.tags_text(body.tags_of(text))
            if tags:
                own.setdefault(slot, {})[key] = tags
    redraw = {}
    for slot, table in used.items():
        for key, text in table.items():
            tags = body.tags_text(body.tags_of(text))
            if tags:
                redraw.setdefault(slot, {})[key] = tags
    write(os.path.join(MOD, "data", body.TAGS_FILE), {"own": own, "redraw": redraw})

    print("描き直しの埋め込み {} 枚（一文 {} 種）、語の組 own {} 枚・redraw {} 枚".format(
        sum(len(t) for t in used.values()), len(cache), sum(len(t) for t in own.values()), sum(len(t) for t in redraw.values())))
    if missing:
        print("一文の無い描き直し {} 枚（素の説明のまま選ばれる）: {}".format(len(missing), ", ".join(missing)))


if __name__ == "__main__":
    main()
