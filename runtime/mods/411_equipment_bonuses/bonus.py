# -*- coding: utf-8 -*-
"""能力値補正の決まり（ゲームに触らない部分）。抽選・品への書き込み・読み取り・合算。

入口（`equipment_bonuses.py`）が設定をまとめて渡す。ここは設定を読まない。
"""

import random

#: 能力値の鍵（`ability_scores` の綴り。GAME.md の NPC ひな型と `313_` の `ABILITIES`）。
ABILITIES = ("strength", "dexterity", "constitution",
             "intelligence", "wisdom", "charisma")

#: 品の `attributes` に書く名前。読むときは両方の言語を見る。
LABELS = {
    "ja": {"strength": "筋力", "dexterity": "器用", "constitution": "耐久",
           "intelligence": "知力", "wisdom": "判断", "charisma": "魅力"},
    "en": {"strength": "STR", "dexterity": "DEX", "constitution": "CON",
           "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA"},
}
_KEY_OF = {label: key for table in LABELS.values() for key, label in table.items()}

#: 補正が付く品の種類（本体が読む装備の2つ。GAME.md §2.13.3）。
EQUIPMENT_TYPES = ("weapon", "wearable")

#: 値段の鍵（`129_`）。補正の行はこの手前に入れる（`801_` のスキル名と同じ置き方）。
PRICE_KEYS = ("買価", "売価", "buy price", "sell price", "Buy price", "Sell price")

#: MOD 専用の乱数（ゲームの乱数の並びを動かさない）。
RNG = random.Random()


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def is_equipment(item):
    return getattr(item, "item_type", None) in EQUIPMENT_TYPES


def roll(rarity, tiers, rng=None):
    """レア度から補正を引く。`{能力値: 量}`。付かないレア度なら空。

    `tiers` は `{レア度: (数の下限, 数の上限, 量の下限, 量の上限)}`。
    同じ能力値は2度引かない（数が6を超えても6つまで）。
    """
    tier = tiers.get(rarity)
    if not tier:
        return {}
    rng = rng or RNG
    count_lo, count_hi, amount_lo, amount_hi = tier
    count_lo, count_hi = sorted((max(0, int(count_lo)), max(0, int(count_hi))))
    amount_lo, amount_hi = sorted((max(1, int(amount_lo)), max(1, int(amount_hi))))
    count = min(rng.randint(count_lo, count_hi), len(ABILITIES))
    picked = rng.sample(ABILITIES, count)
    return {key: rng.randint(amount_lo, amount_hi) for key in ABILITIES if key in picked}


def read(item):
    """品に書かれた補正 `{能力値: 量}`。無ければ空。"""
    attributes = getattr(item, "attributes", None)
    if not isinstance(attributes, dict):
        return {}
    found = {}
    for label, raw in attributes.items():
        key = _KEY_OF.get(label)
        if key is None:
            continue
        if isinstance(raw, str):
            try:
                raw = int(raw.strip().replace("＋", "+"))
            except ValueError:
                continue
        raw = _num(raw)
        if raw:
            found[key] = found.get(key, 0) + int(raw)
    return found


def write(item, bonus, lang):
    """補正を品の `attributes` に書く。辞書は同じものの中で並べ直す（参照を替えない）。

    書けたら True。値は `+3` の文字列（説明欄にそのまま出る）。
    """
    attributes = getattr(item, "attributes", None)
    if not isinstance(attributes, dict) or not bonus:
        return False
    labels = LABELS.get(lang) or LABELS["ja"]
    rows = [(k, v) for k, v in attributes.items() if k not in _KEY_OF]
    at = next((i for i, (k, _) in enumerate(rows) if k in PRICE_KEYS), len(rows))
    added = [(labels[key], "+{}".format(bonus[key])) for key in ABILITIES if bonus.get(key)]
    attributes.clear()
    attributes.update(rows[:at] + added + rows[at:])
    return True


def combine(items, mode, cap):
    """身に着けている品の補正をまとめる。`{能力値: 量}`。

    `mode` は "sum"（全部足す）か "max"（能力値ごとに最大の1品）。
    `cap` は能力値ごとの上限（0 以下で上限なし）。
    """
    total = {}
    for item in items:
        for key, amount in read(item).items():
            if mode == "max":
                total[key] = max(total.get(key, 0), amount)
            else:
                total[key] = total.get(key, 0) + amount
    if cap and cap > 0:
        total = {key: min(amount, int(cap)) for key, amount in total.items()}
    return {key: amount for key, amount in total.items() if amount}

