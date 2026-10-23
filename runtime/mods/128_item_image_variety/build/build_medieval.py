# -*- coding: utf-8 -*-
"""中世の武器・防具・魔法武器・SF武器の一枚絵 → ゲームの寸法の絵と追加埋め込み辞書（一枚絵ごとの分類）。

切り抜き・寸法合わせ・色の測り方は build_guns.py と同じ部品を使う。違うのは:

- 出力先は元画像1枚ごとの分類（../categories.json）のフォルダ（../images/<分類>/…・../data/<分類>/<品種>.json）
- 鍵は組の頭（`med_` / `mag_` / `sf_`）で始める
- キャプションはゲームの外見文と同じ形で結ぶ（`, a gun.` を付けない）。刃物は色を素材の言葉に直し、
  魔法武器は差し色から属性（炎・氷・闇・自然・聖）を書く。SF武器は光る色か刃の材質を書く
- SF武器の一枚絵は少し傾いて描かれているので、±20 度の中で幅が最小の向きに立てる（`build_guns.stand_up`）
- 防具（盾・胴鎧・服・装身具）の寸法も扱う
- 表の `whole` が真の品は、箱の中の塊を全部取る（2〜3本の投げナイフや矢の束のように、離れて描かれた1組）

入力はリポジトリの assets/item_images の下の一枚絵（作者が画像生成 AI で作ったもの。場所は categories.json の source）と、
1枚ごとの 鍵・品種の希望・一枚絵の上の箱・種類の名前を書いた表（medieval.json / magic.json / sf.json）。

    python build_medieval.py [--set medieval|magic|sf|item] [--sheets <一枚絵のフォルダ>] [--preview <見比べ用の一覧 PNG>]
"""
import argparse
import colorsys
import functools
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_guns as b  # noqa: E402

# 組 → (表, 鍵の頭)。書く分類は表の品が入る分類（build_guns.owned_categories）
SETS = {"medieval": ("medieval.json", "med_"), "magic": ("magic.json", "mag_"), "sf": ("sf.json", "sf_"), "item": ("items.json", "itm_")}
SHEETS = b.SHEETS  # 一枚絵の置き場所は categories.json から（build_guns.SHEETS）
# 消耗品と素材の部位（素の絵はどれも 32x32）
ITEMS = ("food", "drink", "medicine", "plant", "mushroom", "tool", "relic", "scrap", "magical_material", "other_material",
         "creature_part", "document", "scroll", "liquid_material", "treasure", "metal", "gem", "ore", "potion")
# 寸法は部位の素の絵と同じ（build_guns.game_size。盾・胴鎧は 2x2 マスの 64x64、服・装身具・消耗品は 32x32）
SQUARE = ("shield", "body_armor", "clothing", "accessory", "headgear", "gauntlets", "legwear") + ITEMS
# 消耗品と部品の組（item）の頭・服・手・脚・装身具は、防具類と同じく 64x64（作者の指示。中世の服は 32x32 のまま）。
# 装身具は中世の組（矢筒）でも 64x64。1マスの品は画面で 64px に描かれ、32x32 だと2倍に引き伸ばされて粗く見えた
ITEM_WEAR = {f: (64, 64) for f in ("clothing", "headgear", "gauntlets", "legwear", "accessory")}
# 刃や金属の頭を持つ種類。色を素材の言葉に直す
METAL = ("sword", "claymore", "sabre", "rapier", "axe", "hammer", "maul", "mace", "morning star", "spear", "halberd",
         "flail", "dagger", "stiletto", "knife", "cleaver", "machete", "sickle", "scythe", "trident",
         "hauberk", "cuirass", "scale armor", "brigandine", "breastplate", "kite shield", "tower shield")
# 柄が木のことが多い種類。茶色はここでは木の柄、刀剣では革巻きの握り
HAFTED = ("axe", "hammer", "maul", "mace", "morning star", "club", "spear", "halberd", "flail", "scythe", "trident", "staff")
# 木でできた種類（茶色が多ければ wooden と書く）と、防具（茶色は革の帯）
WOODISH = ("staff", "bow", "club", "shield")
ARMOR = ("hauberk", "cuirass", "scale armor", "brigandine", "breastplate", "gambeson", "tunic", "jerkin")
ELEMENT = {"red": "fire", "orange": "fire", "blue": "frost", "cyan": "frost", "purple": "shadow",
           "magenta": "shadow", "green": "nature"}
# 名前の語 → 属性（魔法武器の名前に書いた属性を、測った色より先に使う）
NAMED_ELEMENT = (("holy", "holy"), ("burning", "fire"), ("flame", "fire"), ("fire", "fire"), ("lava", "fire"), ("molten", "fire"),
                 ("ice", "frost"), ("frost", "frost"), ("shadow", "shadow"), ("void", "shadow"), ("vine", "nature"), ("root", "nature"))


def join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def caption_for(entry, im, magic):
    names = b.color_names(im)
    total = max(1, sum(names.values()))
    body = [k for k, _ in names.most_common() if not k.startswith("glow:") and k != "wood"]
    main = body[0] if body else "grey"
    typ, _, own = entry["type"].partition(" with ")
    extras = [own] if own else []
    metal = any(w in typ for w in METAL)
    woodish = any(w in typ for w in WOODISH) and not metal
    wooden = woodish and names["wood"] / total >= 0.12
    if metal:
        word = {"grey": "steel", "white": "polished steel", "black": "blackened iron", "tan": "bronze",
                "brass": "gilded", "red": "red-lacquered", "blue": "blued steel"}.get(main, main)
    elif wooden:
        word = "wooden"
    elif "quiver" in typ:
        word = "leather"
    else:
        word = {"brass": "gilded"}.get(main, main)
        if names["wood"] / total >= 0.35:
            word = "brown"
    if magic:
        colors = [k[5:] if k.startswith("glow:") else k for k, n in names.most_common()
                  if (k.startswith("glow:") or k in ELEMENT) and n / total >= 0.03]
        color = next((c for c in colors if c in ELEMENT), None)
        element = ELEMENT.get(color) or ("holy" if names["brass"] + names["white"] > 0.2 * total else "arcane")
        # 名前に属性の語があればそれに従う（金の聖剣の金を橙と測って fire にしない）。属性の語そのものが名前にあれば前に重ねない
        words = re.findall(r"[a-z]+", typ)
        element = next((el for w, el in NAMED_ELEMENT if w in words), element)
        word = element if element in words else "%s-enchanted" % element
        if color:
            extras.append("a glowing %s gem" % color)
    elif names["wood"] / total >= 0.12 and not wooden and word not in ("brown", "leather"):
        if any(w in typ for w in ARMOR):
            extras.append("leather straps")
        elif any(w in typ for w in HAFTED):
            extras.append("a wooden haft")
        elif metal:
            extras.append("a leather-wrapped grip")
    if not magic and main != "brass" and names["brass"] / total >= 0.04:
        extras.append("gold fittings")
    if not magic:
        accent = [k for k in body[1:] if k in ("red", "blue", "green", "purple") and names[k] / total >= 0.04]
        if accent:
            extras.append("%s details" % accent[0])
    text = "%s %s" % (word, typ) if word not in typ else typ
    if extras:
        text += " with " + join(extras)
    return "%s %s." % ("An" if text[0] in "aeiou" else "A", text)


SF_MATERIAL = {"grey": "steel", "white": "polished alloy", "black": "black alloy", "tan": "bronze", "brass": "gold-plated"}


def caption_sf(entry, im):
    """SF武器: 光る部分があればその色、無ければ刃や頭の材質。差し色も足す。"""
    names = b.color_names(im)
    total = max(1, sum(names.values()))
    typ = entry["type"]
    part = ("blade" if any(w in typ for w in ("sword", "dagger", "knife", "machete")) else
            "head" if any(w in typ for w in ("hammer", "axe", "mace", "spear", "scythe", "halberd", "tomahawk",
                                             "hatchet", "pick", "shovel", "wrench")) else
            "edge" if any(w in typ for w in ("disc", "star", "throwing blade")) else
            "core" if any(w in typ for w in ("stun", "shock")) else
            "shaft" if any(w in typ for w in ("baton", "bat", "club", "crowbar", "pry bar")) else "core")
    glow = Counter({k[5:]: n for k, n in names.items() if k.startswith("glow:")})
    # color_names は赤と橙を光として数えないので、鮮やかで明るい赤・橙をここで数える
    a = np.asarray(im).astype(np.float32) / 255
    px = a[a[..., 3] > 0.5][:, :3]
    glowing = any(w in typ for w in ("energy", "plasma", "stun", "shock"))
    for r, g, bl in px[:: max(1, len(px) // 4000)] if glowing else ():   # 赤く塗った斧やレンチは光らせない
        h, s, v = colorsys.rgb_to_hsv(r, g, bl)
        if s > 0.5 and v > 0.85 and (h * 360 < 40 or h * 360 >= 345):
            glow["red" if h * 360 < 15 or h * 360 >= 345 else "orange"] += 1
    glow = [k for k, n in glow.most_common() if n / total >= 0.02]
    body = [k for k, _ in names.most_common() if not k.startswith("glow:")]
    if not glow and "energy" in typ:
        # 光る刃の芯は白く飛び、色は縁にだけ残る。縁の色を光の色とする
        glow = [k for k in body if k in ("red", "orange", "blue", "green", "purple", "magenta")][:1]
    extras = []
    if glow:
        extras.append("a glowing %s %s" % (glow[0], part))
    else:
        # 茶色を木と読むのはバットと棍棒だけ（錆びた刃・橙の縁・赤いバールの茶色を wooden と書かない）
        wood_ok = any(w in typ.split() for w in ("bat", "club"))
        main = next((k for k in body if k in SF_MATERIAL or (k == "wood" and wood_ok)), "grey")
        word = "wooden" if main == "wood" or (main == "tan" and "bat" in typ) else SF_MATERIAL[main]
        extras.append("a %s %s" % (word, part))
    accent = [k for k in body if k in ("red", "orange", "brass", "blue", "green", "purple") and names[k] / total >= 0.04
              and k not in glow[:1] and not (glow[:1] == ["cyan"] and k == "blue")]
    if accent:
        extras.append("%s details" % {"brass": "yellow"}.get(accent[0], accent[0]))
    text = "%s with %s" % (typ, join(extras))
    return "%s %s." % ("An" if text[0] in "aeiou" else "A", text)


ITEM_COLOR = {"grey": "grey", "white": "white", "black": "black", "tan": "beige", "brass": "golden", "wood": "brown",
              "red": "red", "orange": "orange", "olive green": "olive", "green": "green", "blue": "blue", "purple": "purple",
              "magenta": "pink"}


def caption_item(entry, im):
    """消耗品と部品: 「色 + 種類の名前」。光る部分があれば光る色を足す。表の plain が真の品は種類の名前だけ（名前に色や材質が入っている）。"""
    names = b.color_names(im)
    total = max(1, sum(names.values()))
    text = entry["type"]
    if not entry.get("plain"):
        body = [k for k, _ in names.most_common() if not k.startswith("glow:") and k in ITEM_COLOR]
        # 名前に入っている色・金属の色・名前の頭の色は前に重ねない（「brown brown biscuit」「brown gold bar」「brown red oni mask」にしない）
        if body and ITEM_COLOR[body[0]] not in re.findall(r"[a-z]+", text.lower()) and not b.names_own_color(text):
            text = "%s %s" % (ITEM_COLOR[body[0]], text)
    glow = [k[5:] for k, n in names.most_common() if k.startswith("glow:") and n / total >= 0.03]
    if glow and "glow" not in text:
        text += " with a faint %s glow" % glow[0]
    return "%s %s." % ("An" if text[0] in "aeiou" else "A", text)


def choose(entry, im):
    if entry["pref"] in SQUARE:
        return entry["pref"]
    return b.choose_folder(entry["pref"], im)


def make_entry(e, sheets, kind):
    """表の1行から絵を作る（プロセスの中で動く。build_guns.parallel_map）。戻り値は (絵の配列, 部位, キャプション)。"""
    magic, sf, item = kind == "magic", kind == "sf", kind == "item"
    im = (b.extract_whole if e.get("whole") else b.extract)(sheets, e["src"])
    im = im.crop(im.getbbox())  # 縦向きの絵はもともと刃が上（依頼の決まり）。斜めの絵はそのまま
    if e.get("diag"):
        # 縦に描かれた短い武器と投げナイフは、ゲームの小型・投擲と同じく刃先を右上へ 45 度に倒す（32x32 に大きく入る）
        # rot があれば先に回す（逆さに描かれた苦無は rot: 180）
        im = b.stand_up(im, e.get("rot", 0)).rotate(-45, resample=Image.BICUBIC, expand=True)
        im = im.crop(im.getbbox())
    elif e.get("rot"):
        # 斜めに描かれた長い品（和風の金棒）を立てる。rot は左回りの角度で、±20 度の中で幅が最小の向きに寄せる
        im = b.stand_up(im, e["rot"])
    elif sf and e["pref"] != "throwable":
        im = b.stand_up(im, 0)
    e["folder"] = choose(e, im)
    e["caption"] = caption_item(e, im) if item else caption_sf(e, im) if sf else caption_for(e, im, magic)
    box_w, box_h = (ITEM_WEAR.get(e["folder"]) if item or e["folder"] == "accessory" else None) or b.game_size(e["folder"])
    if e["folder"] in b.SIZES and e.get("halo", True):
        # 武器だけ（結晶や薬瓶は色の付いた暗い面が多く、はがすと削れる）
        im = b.peel_glow(im, min((box_w - 2) / im.width, (box_h - 2) / im.height), strong=bool(e.get("glow")))
    # 武器は SCALE 倍、消耗品・素材などのアイテム類は ITEM_SCALE 倍（作者の指示）。盾・防具・装身具は素の寸法
    scale = b.SCALE if e["folder"] in b.SIZES else b.ITEM_SCALE if e["folder"] in ITEMS else 1
    return b.shrink(im, box_w, box_h, 255, scale), e["folder"], e["caption"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="medieval", choices=sorted(SETS))
    ap.add_argument("--sheets", default=str(b.ASSET_ROOT))
    ap.add_argument("--preview")
    args = ap.parse_args()
    table, prefix = SETS[args.set]
    magic = args.set == "magic"
    sf = args.set == "sf"
    item = args.set == "item"

    b.SHEETS.update(SHEETS)
    entries = json.load(open(HERE / table, encoding="utf-8"))
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys)), "鍵が重なっている"
    assert all(k.startswith(prefix) for k in keys), "鍵は %s で始める" % prefix
    made = {}
    jobs = b.parallel_map(functools.partial(make_entry, sheets=args.sheets, kind=args.set), entries)
    for e, (arr, folder, caption) in zip(entries, jobs):
        e["folder"], e["caption"] = folder, caption
        made[e["key"]] = Image.fromarray(arr)
    b.write_categories(entries, made, b.owned_categories(entries))
    with open(HERE / ("%s_captions.txt" % args.set), "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write("%s\t%s\t%s\n" % (e["key"], e["folder"], e["caption"]))

    if args.preview:
        cols = 20
        rows = {}
        for e in entries:
            rows.setdefault(e["folder"], []).append(made[e["key"]])
        # 箱の寸法は作った絵から取る
        box = {f: (max(t.width for t in v), max(t.height for t in v)) for f, v in rows.items()}
        step = {f: max(36, box[f][0] + 4) for f in box}
        height = sum(((len(v) + cols - 1) // cols) * (box[f][1] + 4) for f, v in rows.items())
        sheet = Image.new("RGBA", (cols * 68, height), (40, 40, 40, 255))
        y = 0
        for folder, tiles in rows.items():
            for i, t in enumerate(tiles):
                sheet.alpha_composite(t, ((i % cols) * step[folder] + 2, y + (i // cols) * (box[folder][1] + 4) + 2))
            y += ((len(tiles) + cols - 1) // cols) * (box[folder][1] + 4)
        sheet.resize((sheet.width * 2, sheet.height * 2), Image.NEAREST).save(args.preview)
    print("done")


if __name__ == "__main__":
    main()
