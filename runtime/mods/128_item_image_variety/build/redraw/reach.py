# -*- coding: utf-8 -*-
r"""素の絵ごとに、記録の外見文で候補に入った回数を数え、`refs\reach.json`（{"品種/鍵": 回数}）に書く。

    python runtime/mods/128_item_image_variety/build/redraw/reach.py

外見文は 128 の対応表（`state\item_image\*.json`）。今の辞書と 128 の選び方（tools\item_image_probe.py の Picker）で
外見文ごとの候補（似た絵の上位）を出し、そこに入った素の絵を数える。
過去に使われた回数より、この先も出てきそうな絵を表す。頼む順番を決めるのに使う。
"""
import collections
import glob
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(os.path.dirname(HERE))   # 128_item_image_variety
ROOT = os.path.normpath(os.path.join(MOD, os.pardir, os.pardir, os.pardir))
# 取り込みの作業場（手本・描き直しの一枚絵・切り出し・頼んだ記録）。大きいので git の外の local\ に置く
WORK = os.path.join(ROOT, "local", "item_image_redraw")
# 描き直しの絵の置き場（配布物）
OUT = os.path.join(MOD, "images", "redraw")
sys.path.insert(0, os.path.join(ROOT, "runtime", "mods", "128_item_image_variety", "build"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
spec = importlib.util.spec_from_file_location("probe", os.path.join(ROOT, "tools", "item_image_probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def main():
    picker = probe.Picker(probe.find_game_dir(None))
    texts = collections.defaultdict(set)
    for path in glob.glob(os.path.join(ROOT, "state", "item_image", "*.json")):
        for label in (json.load(io.open(path, encoding="utf-8")).get("images") or {}):
            slot, _, text = label.partition("/")
            texts[slot].add(text)
    reach = collections.Counter()
    for slot, group in texts.items():
        if picker.pool(slot) is None:
            continue
        for text in group:
            rk = picker.rank(slot, picker.embed(text))
            for i in rk["qualified"]:
                if rk["cat"].get(rk["keys"][i], "stock") in ("stock", "extra"):
                    reach["{}/{}".format(slot, rk["keys"][i])] += 1
    with io.open(os.path.join(WORK, "refs", "reach.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(sorted(reach.items())), fh, ensure_ascii=False, indent=1)
    print("外見文 {} 件、候補に入った素の絵 {} 枚".format(sum(len(g) for g in texts.values()), len(reach)))


if __name__ == "__main__":
    main()
