# -*- coding: utf-8 -*-
"""武器と防具の効果の決まり（ゲームに触らない部分）。抽選・品への書き込み・読み取り・合算。

入口（`equipment_bonuses.py`）が設定をまとめて渡す。ここは設定を読まない。
戦闘に効かせるのはローダの戦闘の効果の窓口（`battle_effects`。TECH.md §3.3.28）。
種類の名前・戦闘の数・表記は窓口のものを使う（同じ名前でここから読めるように残してある）。
"""

import re

from instantale_modloader import battle_effects as _battle

# 411 の中と検査がこの名前で読む（中身は窓口のもの）。
BINDING_COST = _battle.BINDING_COST
CURSE_COST = _battle.CURSE_COST
DAUNT_CUT = _battle.DAUNT_CUT
DAUNT_TURNS = _battle.DAUNT_TURNS
DESPERATE_AT = _battle.DESPERATE_AT
GROWTH_CAP = _battle.GROWTH_CAP
GROWTH_STEP = _battle.GROWTH_STEP
HEAVY_DEX = _battle.HEAVY_DEX
ON_HIT = _battle.ON_HIT
POWERS = _battle.POWERS
SLAYER_TARGETS = _battle.SLAYER_TARGETS
STATUS_NAMES = _battle.STATUS_NAMES
STATUS_TEXTS = _battle.STATUS_TEXTS
TARGET_LABELS = _battle.TARGET_LABELS
VENOM_RATE = _battle.VENOM_RATE
VENOM_TURNS = _battle.VENOM_TURNS
after_cuts = _battle.after_cuts
drained = _battle.drained
growth_percent = _battle.growth_percent
is_desperate = _battle.is_desperate
pierced = _battle.pierced
raise_power = _battle.raise_power
scaled = _battle.scaled
share_of_max = _battle.share_of_max
should_execute = _battle.should_execute
slayer_bonus = _battle.slayer_bonus
text_of = _battle.text_of
venom_tick = _battle.venom_tick

#: 効果の種類。品の説明欄の並びもこの順（窓口の並びと同じ）。
WEAPON_KINDS = _battle.WEAPON_KINDS
ARMOR_KINDS = _battle.ARMOR_KINDS
KINDS = WEAPON_KINDS + ARMOR_KINDS

#: 品の `attributes` に書く名前（品に付く種類だけ）。読むときは両方の言語を見る。
LABELS = {lang: {kind: table[kind] for kind in KINDS} for lang, table in _battle.LABELS.items()}
_KIND_OF = {label: kind for table in LABELS.values() for kind, label in table.items()}
_TARGET_OF = {label: target for table in TARGET_LABELS.values()
              for target, label in table.items()}

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

#: 合算した後の上限（%）（品に付く種類だけ）。
CAPS = {kind: cap for kind, cap in _battle.CAPS.items() if kind in KINDS}

#: 値段の鍵（`129_`）。効果の行はこの手前に入れる（能力値補正と同じ置き方）。
PRICE_KEYS = ("買価", "売価", "buy price", "sell price", "Buy price", "Sell price")

#: MOD 専用の乱数（ゲームの乱数の並びを動かさない）。戦闘の窓口と同じもの。
RNG = _battle.RNG

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


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value
