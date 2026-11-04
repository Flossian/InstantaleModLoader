# -*- coding: utf-8 -*-
"""武器と防具の効果の決まり（ゲームに触らない部分）。抽選・品への書き込み・読み取り・合算・戦闘の数。

入口（`equipment_bonuses.py`）が設定をまとめて渡す。ここは設定を読まない。
戦闘に効かせるのは `battle.py`。
"""

import random
import re

#: 効果の種類。品の説明欄の並びもこの順。
WEAPON_KINDS = ("slayer", "critical", "lifesteal", "pierce", "venom", "daunt",
                "curse", "growth", "execute", "teamwork")
ARMOR_KINDS = ("endure", "thorns", "regen", "heavy", "guardian",
               "resist", "evade", "desperate", "binding", "presence")
KINDS = WEAPON_KINDS + ARMOR_KINDS

#: 特効の相手。どれも戦闘の画面で見分けられるもの（敵の段と型は Character に残らない）。
#:   boss      … ボス戦（`in_boss_battle`）
#:   higher    … 自分よりレベルが上の敵
#:   wounded   … 体力が半分以下の敵
#:   unhurt    … 体力が満タンの敵
SLAYER_TARGETS = ("boss", "higher", "wounded", "unhurt")

#: 品の `attributes` に書く名前。読むときは両方の言語を見る。
LABELS = {
    "ja": {"slayer": "特効", "critical": "会心", "lifesteal": "吸収",
           "pierce": "貫通", "venom": "毒", "daunt": "怯み",
           "curse": "呪い", "growth": "成長", "execute": "処刑", "teamwork": "連携",
           "endure": "不屈", "thorns": "棘", "regen": "再生", "heavy": "重装", "guardian": "守護",
           "resist": "耐性", "evade": "軽業", "desperate": "火事場", "binding": "呪縛", "presence": "威光"},
    "en": {"slayer": "Slayer", "critical": "Critical", "lifesteal": "Lifesteal",
           "pierce": "Pierce", "venom": "Venom", "daunt": "Daunt",
           "curse": "Cursed", "growth": "Growth", "execute": "Execute", "teamwork": "Teamwork",
           "endure": "Endure", "thorns": "Thorns", "regen": "Regen", "heavy": "Heavy", "guardian": "Guardian",
           "resist": "Resist", "evade": "Nimble", "desperate": "Last Stand", "binding": "Binding",
           "presence": "Presence"},
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
#:   curse     … 与えるダメージの上乗せ（当てた手で自分の体力が減る）
#:   growth    … 敵を GROWTH_STEP 体倒すごとの、与えるダメージの上乗せ
#:   execute   … 当てた後の敵の体力がこの割合以下なら倒す
#:   teamwork  … 直前の味方が当てた敵へのダメージの上乗せ
#:   endure    … 倒れる一撃を体力 1 で耐える確率（戦闘に1回）
#:   thorns    … 受けたダメージのうち、相手に返す割合
#:   regen     … 毎ターン戻る、最大体力の割合
#:   heavy     … 受けるダメージを減らす割合（器用が HEAVY_DEX 下がる）
#:   guardian  … 仲間への一撃を肩代わりする確率
#:   resist    … 敵の手で付いた状態異常を防ぐ確率
#:   evade     … 敵の一撃を無効にする確率
#:   desperate … 体力が DESPERATE_AT 以下の間、受けるダメージを減らす割合
#:   binding   … 受けるダメージを減らす割合（与えるダメージが BINDING_COST 下がる）
#:   presence  … 店の買値を下げる割合（主人公が着ているときだけ）
STRENGTH = {
    "slayer": (20, 30, 40),
    "critical": (10, 15, 20),
    "lifesteal": (6, 10, 15),
    "pierce": (20, 35, 50),
    "venom": (20, 30, 40),
    "daunt": (20, 30, 40),
    "curse": (30, 40, 50),
    "growth": (3, 4, 5),
    "execute": (10, 15, 20),
    "teamwork": (20, 30, 40),
    "endure": (50, 75, 100),
    "thorns": (20, 30, 40),
    "regen": (2, 3, 4),
    "heavy": (20, 25, 30),
    "guardian": (15, 25, 35),
    "resist": (30, 50, 70),
    "evade": (10, 15, 20),
    "desperate": (20, 30, 40),
    "binding": (30, 40, 50),
    "presence": (5, 7, 10),
}
RARITIES = ("epic", "legendary", "mythic")

#: 合算した後の上限（%）。確率と割合は 100 を超えても意味が無い。
CAPS = {"critical": 100, "lifesteal": 100, "pierce": 100, "venom": 100, "daunt": 100,
        "execute": 100, "endure": 100, "thorns": 100, "regen": 100, "heavy": 100, "guardian": 100,
        "resist": 100, "evade": 100, "desperate": 100, "binding": 100, "presence": 100}

#: 毒: 毎ターン最大体力のこの割合（%）を削る。この手数だけ続く。毒では倒れない（体力 1 で止まる）。
VENOM_RATE = 4
VENOM_TURNS = 3
#: 怯み: 怯んだ敵の一撃をこの割合（%）だけ弱める。この手数だけ続く。
DAUNT_CUT = 30
DAUNT_TURNS = 2
#: 呪い: 当てた手で、自分の最大体力のこの割合（%）を失う。呪いでは倒れない（体力 1 で止まる）。
CURSE_COST = 3
#: 成長: この数を倒すごとに伸びる。上乗せはこの % で止める。
GROWTH_STEP = 10
GROWTH_CAP = 100
#: 重装: 下がる器用。
HEAVY_DEX = 4
#: 火事場: 体力がこの割合（%）以下の間に効く。
DESPERATE_AT = 30
#: 呪縛: 与えるダメージがこの割合（%）下がる。
BINDING_COST = 20

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
#: 成長の説明欄の討伐数（`討伐 34` / `(34 kills)`）。
_KILLS = re.compile(r"(討伐 |\()(\d+)( kills\))?")


def is_weapon(item):
    return getattr(item, "item_type", None) == "weapon"


def is_armor(item):
    return getattr(item, "item_type", None) == "wearable"


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
    ja = lang == "ja"
    if kind == "slayer":
        return "{} +{}%".format(TARGET_LABELS[lang].get(target, target), amount)
    if kind in ("venom", "daunt"):
        return "{}{}%".format(ON_HIT[lang], amount)
    if kind == "curse":
        return ("+{}%・当てると体力 -{}%" if ja else "+{}%, costs {}% HP per attack").format(amount, CURSE_COST)
    if kind == "growth":
        return ("+{}% / {}体（討伐 0）" if ja else "+{}% per {} kills (0 kills)").format(amount, GROWTH_STEP)
    if kind == "execute":
        return ("残り体力 {}% 以下" if ja else "below {}% HP").format(amount)
    if kind == "teamwork":
        return ("味方の後 +{}%" if ja else "+{}% after an ally").format(amount)
    if kind == "endure":
        return ("{}% 戦闘に1回" if ja else "{}% once per battle").format(amount)
    if kind == "thorns":
        return ("受けたダメージの {}%" if ja else "{}% of damage taken").format(amount)
    if kind == "regen":
        return ("毎ターン 最大体力の {}%" if ja else "{}% of max HP each turn").format(amount)
    if kind == "heavy":
        return ("受けるダメージ -{}%・器用 -{}" if ja else "-{}% damage taken, DEX -{}").format(amount, HEAVY_DEX)
    if kind == "guardian":
        return ("仲間をかばう {}%" if ja else "{}% to shield an ally").format(amount)
    if kind == "resist":
        return ("状態異常を防ぐ {}%" if ja else "{}% to block a status").format(amount)
    if kind == "evade":
        return ("一撃をかわす {}%" if ja else "{}% to dodge a blow").format(amount)
    if kind == "desperate":
        return ("受けるダメージ -{}%（体力 {}% 以下）" if ja else "-{}% damage taken (at {}% HP or less)").format(
            amount, DESPERATE_AT)
    if kind == "binding":
        return ("受けるダメージ -{}%・与えるダメージ -{}%" if ja else "-{}% damage taken, -{}% damage dealt").format(
            amount, BINDING_COST)
    if kind == "presence":
        return ("店の買値 -{}%" if ja else "-{}% shop prices").format(amount)
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


def kills_of(item):
    """成長の討伐数。成長の無い品は 0。"""
    attributes = getattr(item, "attributes", None)
    if isinstance(attributes, dict):
        for label, raw in attributes.items():
            if _KIND_OF.get(label) == "growth" and isinstance(raw, str):
                found = _KILLS.search(raw)
                if found is not None:
                    return int(found.group(2))
    return 0


def growth_percent(rate, kills):
    """成長の上乗せ（%）。"""
    return min(GROWTH_CAP, rate * (kills // GROWTH_STEP))


def add_kill(item):
    """成長の品の討伐数を1増やす（説明欄に書いたものがそのまま残る）。増えた後の討伐数。無ければ None。"""
    attributes = getattr(item, "attributes", None)
    if not isinstance(attributes, dict):
        return None
    for label, raw in attributes.items():
        if _KIND_OF.get(label) == "growth" and isinstance(raw, str):
            found = _KILLS.search(raw)
            if found is None:
                return None
            kills = int(found.group(2)) + 1
            attributes[label] = _KILLS.sub(
                lambda m: "{}{}{}".format(m.group(1), kills, m.group(3) or ""), raw, count=1)
            return kills
    return None


def combine(items, mode):
    """身に着けている品の効果をまとめる。

    `{"slayer": {相手: %}, "critical": %, ...}`。無い種類は入らない。
    `mode` は "sum"（全部足す）か "max"（種類ごとに最大の1品）。確率と割合は `CAPS` で止める。
    成長は `growth` が討伐数から出した今の上乗せ、`growth_items` が討伐数を数える品の並び。
    """
    total = {}
    for item in items:
        for kind, amount, target in read(item):
            if kind == "slayer":
                table = total.setdefault("slayer", {})
                table[target] = (max(table.get(target, 0), amount) if mode == "max"
                                 else table.get(target, 0) + amount)
                continue
            if kind == "growth":
                total.setdefault("growth_items", []).append(item)
                amount = growth_percent(amount, kills_of(item))
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


def share_of_max(max_hp, percent):
    """最大体力の割合（少なくとも 1）。毒・呪い・再生の量。"""
    if _num(max_hp) is None or max_hp <= 0 or not percent:
        return 0
    return max(1, int(round(max_hp * percent / 100.0)))


def venom_tick(max_hp):
    """毒の1手ぶんのダメージ。"""
    return share_of_max(max_hp, VENOM_RATE)


def after_cuts(damage, cuts):
    """軽減の割合（%）の並びを順に掛けた後のダメージ。足さないので、種類が違っても 0 にはならない。"""
    factor = 1.0
    for cut in cuts:
        if cut:
            factor *= max(0.0, 1.0 - cut / 100.0)
    return scaled(damage, (factor - 1.0) * 100.0) if factor < 1.0 else damage


def is_desperate(hp, max_hp):
    """火事場が効く体力か。"""
    return _num(hp) is not None and _num(max_hp) is not None and max_hp > 0 \
        and 0 < hp and hp * 100 <= max_hp * DESPERATE_AT


def should_execute(hp, dealt, max_hp, percent):
    """当てた後の敵の体力が割合以下で、まだ倒れていないか。"""
    if not percent or _num(hp) is None or _num(dealt) is None or _num(max_hp) is None or max_hp <= 0:
        return False
    left = hp - dealt
    return 0 < left and left * 100 <= max_hp * percent


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value
