# -*- coding: utf-8 -*-
"""武器の効果の決まり（ゲームに触らない部分）。抽選・品への書き込み・読み取り・合算・戦闘の数。

入口（`equipment_bonuses.py`）が設定をまとめて渡す。ここは設定を読まない。
戦闘に効かせるのは `battle.py`。
"""

import random
import re

#: 効果の種類。品の説明欄の並びもこの順。
KINDS = ("slayer", "critical", "lifesteal", "pierce", "venom", "daunt")

#: 特効の相手。どれも戦闘の画面で見分けられるもの（敵の段と型は Character に残らない）。
#:   boss      … ボス戦（`in_boss_battle`）
#:   higher    … 自分よりレベルが上の敵
#:   wounded   … 体力が半分以下の敵
#:   unhurt    … 体力が満タンの敵
SLAYER_TARGETS = ("boss", "higher", "wounded", "unhurt")

#: 品の `attributes` に書く名前。読むときは両方の言語を見る。
LABELS = {
    "ja": {"slayer": "特効", "critical": "会心", "lifesteal": "吸収",
           "pierce": "貫通", "venom": "毒", "daunt": "怯み"},
    "en": {"slayer": "Slayer", "critical": "Critical", "lifesteal": "Lifesteal",
           "pierce": "Pierce", "venom": "Venom", "daunt": "Daunt"},
}
_KIND_OF = {label: kind for table in LABELS.values() for kind, label in table.items()}

TARGET_LABELS = {
    "ja": {"boss": "ボス戦", "higher": "格上", "wounded": "手負い", "unhurt": "無傷"},
    "en": {"boss": "boss fights", "higher": "stronger foes",
           "wounded": "wounded foes", "unhurt": "unhurt foes"},
}
_TARGET_OF = {label: target for table in TARGET_LABELS.values()
              for target, label in table.items()}

#: 命中時に付く効果の値の前置き。
ON_HIT = {"ja": "命中時 ", "en": "on hit "}

#: レア度ごとの強さ（%）。並びは epic / legendary / mythic。
#:   slayer    … 特効の相手へのダメージの上乗せ
#:   critical  … 審判の裁きを1段上げる確率
#:   lifesteal … 与えたダメージのうち、自分の体力に戻す割合
#:   pierce    … 敵の防御のうち、無視する割合
#:   venom     … 命中時に毒を付ける確率
#:   daunt     … 命中時に怯ませる確率
STRENGTH = {
    "slayer": (20, 30, 40),
    "critical": (10, 15, 20),
    "lifesteal": (6, 10, 15),
    "pierce": (20, 35, 50),
    "venom": (20, 30, 40),
    "daunt": (20, 30, 40),
}
RARITIES = ("epic", "legendary", "mythic")

#: 合算した後の上限（%）。確率と割合は 100 を超えても意味が無い。
CAPS = {"critical": 100, "lifesteal": 100, "pierce": 100, "venom": 100, "daunt": 100}

#: 毒: 毎ターン最大体力のこの割合（%）を削る。この手数だけ続く。毒では倒れない（体力 1 で止まる）。
VENOM_RATE = 4
VENOM_TURNS = 3
#: 怯み: 怯んだ敵の一撃をこの割合（%）だけ弱める。この手数だけ続く。
DAUNT_CUT = 30
DAUNT_TURNS = 2

#: 状態異常の名前と説明（ゲームの `Character.status` に同じ形で書く）。
STATUS_NAMES = {"ja": {"venom": "毒刃", "daunt": "怯み"},
                "en": {"venom": "Venom", "daunt": "Daunted"}}
STATUS_TEXTS = {
    "ja": {"venom": "毎ターン最大体力の{}%のダメージ".format(VENOM_RATE),
           "daunt": "与えるダメージが{}%下がる".format(DAUNT_CUT)},
    "en": {"venom": "Loses {}% of max HP each turn".format(VENOM_RATE),
           "daunt": "Deals {}% less damage".format(DAUNT_CUT)},
}

#: 審判の裁き（GAME.md §2.10.4）。会心はこの並びで1段上げる。
POWERS = ("weak", "normal", "strong", "very_strong", "extreme")

#: 値段の鍵（`129_`）。効果の行はこの手前に入れる（能力値補正と同じ置き方）。
PRICE_KEYS = ("買価", "売価", "buy price", "sell price", "Buy price", "Sell price")

#: MOD 専用の乱数（ゲームの乱数の並びを動かさない）。
RNG = random.Random()

_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def is_weapon(item):
    return getattr(item, "item_type", None) == "weapon"


def roll(rarity, counts, enabled, rng=None):
    """レア度から効果を引く。`[(種類, 量, 特効の相手 | None)]`。付かなければ空。

    `counts` は `{レア度: (数の下限, 数の上限)}`、`enabled` は付けてよい種類の並び。
    同じ種類は2度引かない。
    """
    if rarity not in RARITIES or rarity not in counts:
        return []
    rng = rng or RNG
    pool = [kind for kind in KINDS if kind in enabled]
    low, high = sorted((max(0, int(counts[rarity][0])), max(0, int(counts[rarity][1]))))
    count = min(rng.randint(low, high), len(pool))
    picked = rng.sample(pool, count)
    tier = RARITIES.index(rarity)
    found = []
    for kind in KINDS:
        if kind in picked:
            target = rng.choice(SLAYER_TARGETS) if kind == "slayer" else None
            found.append((kind, STRENGTH[kind][tier], target))
    return found


def text_of(kind, amount, target, lang):
    """説明欄の値。`ボス戦 +30%`・`15%`・`命中時 30%`。"""
    lang = lang if lang in LABELS else "ja"
    if kind == "slayer":
        return "{} +{}%".format(TARGET_LABELS[lang].get(target, target), amount)
    if kind in ("venom", "daunt"):
        return "{}{}%".format(ON_HIT[lang], amount)
    return "{}%".format(amount)


def write(item, found, lang):
    """効果を品の `attributes` に書く。辞書は同じものの中で並べ直す（参照を替えない）。

    値段の行の手前（能力値補正の後ろ）に入る。書けたら True。
    """
    attributes = getattr(item, "attributes", None)
    if not isinstance(attributes, dict) or not found:
        return False
    labels = LABELS.get(lang) or LABELS["ja"]
    rows = [(k, v) for k, v in attributes.items() if k not in _KIND_OF]
    at = next((i for i, (k, _) in enumerate(rows) if k in PRICE_KEYS), len(rows))
    added = [(labels[kind], text_of(kind, amount, target, lang)) for kind, amount, target in found]
    attributes.clear()
    attributes.update(rows[:at] + added + rows[at:])
    return True


def read(item):
    """品に書かれた効果 `[(種類, 量, 特効の相手 | None)]`。無ければ空。"""
    attributes = getattr(item, "attributes", None)
    if not isinstance(attributes, dict):
        return []
    found = []
    for label, raw in attributes.items():
        kind = _KIND_OF.get(label)
        if kind is None or not isinstance(raw, str):
            continue
        number = _NUMBER.search(raw)
        if number is None:
            continue
        amount = float(number.group(1))
        amount = int(amount) if amount.is_integer() else amount
        target = None
        if kind == "slayer":
            target = next((t for label_, t in _TARGET_OF.items() if raw.strip().startswith(label_)), None)
            if target is None:
                continue
        if amount > 0:
            found.append((kind, amount, target))
    return found


def combine(items, mode):
    """身に着けている武器の効果をまとめる。

    `{"slayer": {相手: %}, "critical": %, ...}`。無い種類は入らない。
    `mode` は "sum"（全部足す）か "max"（種類ごとに最大の1品）。確率と割合は `CAPS` で止める。
    """
    total = {}
    for item in items:
        for kind, amount, target in read(item):
            if kind == "slayer":
                table = total.setdefault("slayer", {})
                table[target] = (max(table.get(target, 0), amount) if mode == "max"
                                 else table.get(target, 0) + amount)
            else:
                total[kind] = (max(total.get(kind, 0), amount) if mode == "max"
                               else total.get(kind, 0) + amount)
    for kind, cap in CAPS.items():
        if kind in total:
            total[kind] = min(total[kind], cap)
    return total


def slayer_bonus(table, boss_fight, actor_level, enemy_level, enemy_hp, enemy_max_hp):
    """特効の上乗せ（%）と当てはまった相手の並び。当てはまる相手が複数なら足す。"""
    if not table:
        return 0, []
    hits = []
    if boss_fight:
        hits.append("boss")
    if _num(actor_level) is not None and _num(enemy_level) is not None \
            and enemy_level > actor_level:
        hits.append("higher")
    if _num(enemy_hp) is not None and _num(enemy_max_hp) and enemy_max_hp > 0:
        if enemy_hp * 2 <= enemy_max_hp:
            hits.append("wounded")
        elif enemy_hp >= enemy_max_hp:
            hits.append("unhurt")
    matched = [target for target in hits if table.get(target)]
    return sum(table[target] for target in matched), matched


def raise_power(power):
    """裁きを1段上げる。extreme・知らない語はそのまま（上げられないので None）。"""
    if power not in POWERS or power == POWERS[-1]:
        return None
    return POWERS[POWERS.index(power) + 1]


def pierced(defense, percent):
    """貫通の後の防御。"""
    if _num(defense) is None or not percent:
        return defense
    return defense * max(0.0, 1.0 - percent / 100.0)


def scaled(damage, percent):
    """ダメージに上乗せ（負の % なら引く）。整数のダメージは整数に戻す。0 は 0 のまま。

    小数のダメージは3桁で丸める（掛け算の誤差 489.99999… が体力と画面へ漏れないように）。
    """
    if _num(damage) is None or not damage or not percent:
        return damage
    value = damage * (1.0 + percent / 100.0)
    return max(1, int(round(value))) if isinstance(damage, int) else max(1.0, round(value, 3))


def drained(dealt, percent):
    """吸収で戻す体力。"""
    if _num(dealt) is None or dealt <= 0 or not percent:
        return 0
    return int(round(dealt * percent / 100.0))


def venom_tick(max_hp):
    """毒の1手ぶんのダメージ。"""
    if _num(max_hp) is None or max_hp <= 0:
        return 0
    return max(1, int(round(max_hp * VENOM_RATE / 100.0)))


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value
