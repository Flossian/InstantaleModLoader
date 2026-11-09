# -*- coding: utf-8 -*-
"""人間の敵（闘技場の相手など）の絵の手当て（版28。VERIFICATION.md §3.65・GAME.md §2.30）。

ゲームは敵の絵を本文 `…,creature,(<種族>:1.1), <外見>…` で頼み、否定に必ず
`nsfw,human,girl,women,people,…` を入れる。種族が人間だと本文と否定がぶつかり、
女性の相手は顔を避けた首から下の絵になった。
敵の絵は正方形で頼むので、立った人の全身は上か下が切れた。

  * 否定: 種族が人間なら `human` / `people` を、女性なら `girl` / `women` も外す
  * 寸法: 人間の敵で正方形なら、同じ高さの縦長（幅は高さの 2/3）で描き、
    左右の端の色で正方形に埋め戻してゲームへ返す（ゲームの工程は正方形のまま）
"""
import re

#: ゲームの敵の本文から種族を読む。`creature,(human woman:1.1)` の括弧の中。
RACE = re.compile(r"\bcreature\s*,\s*\(([^():]+):[0-9.]+\)", re.IGNORECASE)

#: 種族の語にこれが1つでもあれば人間（の形の相手）として扱う。
HUMAN_WORDS = frozenset(("human", "humans", "man", "men", "woman", "women", "girl", "girls",
                         "boy", "boys", "male", "female", "person", "people", "elf", "humanoid"))

#: 女性の語。否定の `girl` / `women` も外す。
FEMALE_WORDS = frozenset(("woman", "women", "girl", "girls", "female"))

#: 人間なら否定から外す語と、女性なら足して外す語。
ALWAYS_DROP = ("human", "people")
FEMALE_DROP = ("girl", "women")

#: 縦長にするときの 幅 / 高さ。
TALL_RATIO = 2.0 / 3.0


def race_of(prompt):
    """ゲームの敵の本文の種族。読めなければ None。"""
    if not isinstance(prompt, str):
        return None
    found = RACE.search(prompt)
    return found.group(1).strip() if found else None


def words(text):
    return set(re.findall(r"[a-z]+", str(text or "").lower()))


def is_human(race):
    return bool(race) and bool(words(race) & HUMAN_WORDS)


def strip_negative(negative, race):
    """種族とぶつかる語を否定から外す。外す語が無ければ元の文のまま返す。"""
    if not isinstance(negative, str) or not is_human(race):
        return negative
    drop = set(ALWAYS_DROP)
    if words(race) & FEMALE_WORDS:
        drop.update(FEMALE_DROP)
    parts = negative.split(",")
    kept = [part for part in parts if part.strip().lower() not in drop]
    if len(kept) == len(parts):
        return negative
    return ",".join(kept).lstrip(" ,")


def tall_size(width, height, round_side):
    """正方形なら同じ高さの縦長の寸法。正方形でなければ None。"""
    if not isinstance(width, int) or not isinstance(height, int) or width != height or width <= 0:
        return None
    narrow = round_side(height * TALL_RATIO)
    if narrow <= 0 or narrow >= height:
        return None
    return narrow, height


def pad_square(image):
    """縦長の絵の左右を端の色で埋めて正方形にする。絵でなければそのまま返す。"""
    size = getattr(image, "size", None)
    if size is None or not callable(getattr(image, "crop", None)):
        return image
    width, height = size
    if width >= height:
        return image
    from PIL import Image

    edge = list(image.crop((0, 0, 1, height)).getdata()) + \
        list(image.crop((width - 1, 0, width, height)).getdata())
    if edge and isinstance(edge[0], int):
        color = sorted(edge)[len(edge) // 2]
    else:
        color = tuple(sorted(px[i] for px in edge)[len(edge) // 2] for i in range(len(edge[0])))
    out = Image.new(image.mode, (height, height), color)
    out.paste(image, ((height - width) // 2, 0))
    return out


def pad_result(result):
    """出口の戻り値（sdcpp 系は絵の一覧）の絵を、どれも正方形に埋め戻す。"""
    if isinstance(result, list):
        return [pad_square(image) for image in result]
    if isinstance(result, tuple):
        return tuple(pad_square(image) for image in result)
    return pad_square(result)
