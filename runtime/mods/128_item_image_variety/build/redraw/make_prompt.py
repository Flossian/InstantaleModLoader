# -*- coding: utf-8 -*-
r"""描き直しを頼むプロンプトを書き出す（画像は付けず、文だけで頼む）。

    python runtime/mods/128_item_image_variety/build/redraw/make_prompt.py <手本の名前> ...
    python runtime/mods/128_item_image_variety/build/redraw/make_prompt.py --redo <{"品種/鍵": 一文} の JSON>

`--redo` は、描き直し済みの品を別の物として描き直す（手本の読み違いのやり直し）。決まりの同じ品種どうしを
10品までの組にして `redo_<番号>` の名前で頼み、並びと一文を `refs\redo\<名前>.json` に残す。

品の一文は `refs\captions\<手本の名前>.json`（{鍵: 英語の一文}）。並びは `refs\manifest.json` の鍵の順。
一文は手本の素の絵を見て書き、素の埋め込み（ゲームが選ぶときに比べる、その絵の説明）に近い言い方を選んである。
品種ごとの決まり（武器の向き・服と鎧の形・文字のある品）を足す。
"""
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

WEAPONS = ("small_weapon", "medium_weapon", "large_weapon", "long_weapon", "throwable_weapon")
WRITTEN = ("document", "relic", "scroll")
COMMON_HEAD = "アイテムの絵を{n}品、横一列に描いてください。\n左から順に、次の一覧のとおりです。\n"
RULES = [
    "1品ずつ一覧の品を描く。順番を入れ替えない。品の数を増やしたり減らしたりしない",
    "横一列に並べる。行を分けない",
    "品どうしは十分に離し、重ねない。どの品も画像の端で切れないようにする",
    "背景は真っ黒（#000000）。影・床・台座・枠・光の背景は描かない",
]


def rules_for(slot):
    out = list(RULES[:1])
    if slot == "throwable_weapon":
        out.append("どの品も平らに正面から見た形で描く。立体的に回した向きにしない")
    elif slot in WEAPONS:
        out.append("どの武器も縦向き・刃や先端が上・平らな側面図で描く。斜めや立体的に回した向きにしない")
    if slot == "body_armor":
        out.append("人は描かず、鎧や服だけを正面から見た形で描く。中は空")
        out.append("胴の部分だけを描く。丈は腰まで、肩当ては左右に張り出させ、縦と横はほぼ同じ大きさにする")
    elif slot == "clothing":
        out.append("人は描かず、服だけを正面から見た形で描く。中は空。丈は縦が横の1.3倍まで")
    elif slot == "shield":
        out.append("どの盾も正面から見た形で描く。斜めや横から見た向きにしない")
    elif slot in ("headgear", "gauntlets", "legwear"):
        out.append("人は描かず、品だけを描く。中は空")
    out += RULES[1:]
    if slot in WRITTEN:
        out.append("品に書かれた文字や記号は読めない模様として描く。品の外に文字・番号・説明のラベルは描かない")
    else:
        # 「品に付いている札や紙は描く」と書いた回は、素材・宝物の全品に値札が付いて届いた
        out.append("文字・番号・説明のラベルは描かない。値札・荷札・タグ・紐で吊るした紙も描かない")
    out.append("いつもの絵柄で描く")
    return out


def same_images():
    """{"品種/鍵": 同じ絵の組の "品種/鍵" の集合}（reuse_redraw.py が書く refs\\same_images.json）。"""
    path = os.path.join(WORK, "refs", "same_images.json")
    out = {}
    if os.path.isfile(path):
        for group in json.load(io.open(path, encoding="utf-8")):
            for x in group:
                out[x] = set(group)
    return out


def asked():
    """頼んだがまだ取り込んでいない鍵（refs\\requests にあって sheets に一枚絵の無い手本の分）。"""
    out = set()
    folder = os.path.join(WORK, "refs", "requests")
    if not os.path.isdir(folder):
        return out
    manifest = {s["sheet"]: s for s in json.load(io.open(os.path.join(WORK, "refs", "manifest.json"), encoding="utf-8"))}
    for f in os.listdir(folder):
        name = f[:-5]
        if name in manifest and not os.path.isfile(os.path.join(WORK, "sheets", name + ".png")):
            slot = manifest[name]["slot"]
            out |= {"{}/{}".format(slot, k) for k in json.load(io.open(os.path.join(folder, f), encoding="utf-8"))}
    return out


def pending_keys(entry, claimed=None, same=None):
    """まだ描き直しの無い鍵（自分で描き直した絵も、reuse_redraw.py が写した絵も無いもの）。
    同じ絵の組の別の1枚を頼んである（claimed）なら抜く。取り込んで reuse_redraw.py を回せば写せる。"""
    folder = os.path.join(OUT, entry["slot"])
    out = []
    for k in entry["keys"]:
        if os.path.isfile(os.path.join(folder, k + ".png")):
            continue
        me = "{}/{}".format(entry["slot"], k)
        if claimed is not None and (same or {}).get(me, {me}) & claimed:
            continue
        out.append(k)
        if claimed is not None:
            claimed.add(me)
    return out


def prompt(name, manifest, claimed=None, same=None):
    """頼む文。描き直し済みの品と、同じ絵を別に頼んである品は抜き、頼んだ鍵の並びを refs\\requests に残す
    （build_redraw.py がこの並びで戻す）。頼む品が無ければ None。"""
    entry = manifest[name]
    keys = pending_keys(entry, claimed, same)
    if not keys:
        return None
    caps = json.load(io.open(os.path.join(WORK, "refs", "captions", name + ".json"), encoding="utf-8"))
    os.makedirs(os.path.join(WORK, "refs", "requests"), exist_ok=True)
    with io.open(os.path.join(WORK, "refs", "requests", name + ".json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(keys, fh, ensure_ascii=False)
    lines = [COMMON_HEAD.format(n=len(keys))]
    lines += ["{}. {}".format(i, caps[k]) for i, k in enumerate(keys, 1)]
    lines.append("")
    lines += ["- " + r for r in rules_for(entry["slot"])]
    return "\n".join(lines)


def redo_prompts(path, per_sheet=10):
    """描き直し済みの品を別の物として描き直す文（読み違いのやり直し）。path は {"品種/鍵": 一文}。
    決まりの同じ品種どうしを per_sheet 品までの組（品数はなるべく揃える）にして redo_<番号> の名前で頼み、
    並びと一文を refs\\redo\\<名前>.json に残す（build_redraw.py がこの並びで戻し、一文を手本の captions へ書く）。"""
    want = json.load(io.open(path, encoding="utf-8"))
    groups = {}
    for sk in want:
        groups.setdefault(tuple(rules_for(sk.split("/", 1)[0])), []).append(sk)
    folder = os.path.join(WORK, "refs", "redo")
    os.makedirs(folder, exist_ok=True)
    # 番号は前に頼んだ分の続きから（1 から振ると、取り込み済みの redo_01 の並びと一枚絵を上書きする）
    taken = [int(f[5:-5]) for f in os.listdir(folder) if f.startswith("redo_") and f.endswith(".json") and f[5:-5].isdigit()]
    out, n = [], max(taken, default=0)
    for rules, keys in groups.items():
        count = -(-len(keys) // per_sheet)
        size = -(-len(keys) // count)
        for i in range(0, len(keys), size):
            chunk = keys[i:i + size]
            n += 1
            name = "redo_{:02d}".format(n)
            with io.open(os.path.join(folder, name + ".json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump({sk: want[sk] for sk in chunk}, fh, ensure_ascii=False, indent=1)
            lines = [COMMON_HEAD.format(n=len(chunk))]
            lines += ["{}. {}".format(j, want[sk]) for j, sk in enumerate(chunk, 1)]
            lines.append("")
            lines += ["- " + r for r in rules]
            out.append("### {}（{}）\n```\n{}\n```\n".format(name, "・".join(dict.fromkeys(sk.split("/", 1)[0] for sk in chunk)),
                                                          "\n".join(lines)))
    return out


def main():
    if sys.argv[1:2] == ["--redo"]:
        for text in redo_prompts(sys.argv[2]):
            print(text)
        return
    manifest = {s["sheet"]: s for s in json.load(io.open(os.path.join(WORK, "refs", "manifest.json"), encoding="utf-8"))}
    same = same_images()
    # 同じ回の手本どうしと、頼んでまだ届いていない手本の品とで、同じ絵を二度頼まない
    claimed = asked() - {"{}/{}".format(manifest[n]["slot"], k) for n in sys.argv[1:] for k in manifest[n]["keys"]}
    for name in sys.argv[1:]:
        text = prompt(name, manifest, claimed, same)
        print("### {}\n全部描き直し済み（頼まなくてよい）\n".format(name) if text is None else "### {}\n```\n{}\n```\n".format(name, text))


if __name__ == "__main__":
    main()
