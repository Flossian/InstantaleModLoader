# -*- coding: utf-8 -*-
"""回復アイテム（`healing_item` / `consumable`）の作り直しと効果。

この MOD の分類ごとのファイルの1つ。
知っているのは**ゲームの回復アイテムがどう書かれているか**
（`attributes` の鍵・細分・キャラクタのどの項目を動かすか）。
知らないのは方針（量の式の係数・確率・文言）で、それは入口から `rules` と `lang` で受け取る。
ログも書かない（TECH.md §3.1.1.1）。

`rules` は入口が設定から組む辞書:

    rarity（{レア度: 倍率}。スタミナと HP の両方に掛かる）
    stamina_base / stamina_per_value / drink_rate / stamina_variance
    hp_base_pct / hp_pct_per_value / hp_variance / potion_hp_rate
    herb_good_chance / herb_min_rate / potion_clears_status

`lang` は `LABELS` の1行（こちらが書く鍵の綴り）。

HP は**最大 HP に対する割合**で品に書く（`回復: 25%`）。
量に直すのは使う時（`effect_of(item, owner)`）。
作り直す前の品（数で書かれた `回復`）はそのまま数として効く。
"""

import random

from instantale_modloader import frames

ITEM_TYPES = ("healing_item", "consumable")

# 細分（`attributes["item_detail"]`）→ 効き方の種類。
KIND_BY_DETAIL = {
    "food": "stamina",
    "drink": "stamina",
    "medicine": "hp",
    "potion": "potion",
    "plant": "herb",
    "mushroom": "herb",
}

# 本体が書く鍵。日本語は実測、英語は本体同梱の `ready_made_items_english` の綴り。
HEAL_KEYS = ("回復", "recovery", "Recovery")
BURDEN_KEYS = ("疲労負荷", "fatigue", "Fatigue", "fatigue burden")

# こちらが書く鍵。言語ごとに1行。
LABELS = {
    "ja": {"stamina": "スタミナ回復", "hp_delta": "HP増減",
           "stamina_delta": "スタミナ増減", "ailment": "状態異常", "clear": "解除",
           "heal": "回復", "burden": "疲労負荷"},
    "en": {"stamina": "stamina", "hp_delta": "HP change",
           "stamina_delta": "stamina change", "ailment": "ailments", "clear": "cured",
           "heal": "recovery", "burden": "fatigue"},
}

OWN_KEY_NAMES = ("stamina", "hp_delta", "stamina_delta", "ailment")


def num(value):
    """数として読めれば float、読めなければ None（文字列で入ることがある）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def parse_amount(value):
    """`attributes` の値を `(数, 割合か)` で読む。`"25%"` → (25.0, True)、`435` → (435.0, False)。

    読めなければ `(None, False)`。
    """
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            return num(text[:-1]), True
    return num(value), False


def format_percent(pct):
    """割合を品に書く形。`25%` / `4.6%`（小数は1桁まで。`.0` は付けない）。"""
    rounded = round(float(pct), 1)
    if rounded == int(rounded):
        return "{}%".format(int(rounded))
    return "{:.1f}%".format(rounded)


def format_signed_percent(pct):
    text = format_percent(abs(pct))
    return ("-" if pct < 0 else "+") + text


def read_item(item):
    """`Item` インスタンスからもセーブの辞書からも、同じ形で読む（`129_` と同じ）。"""
    inner = frames.attr(item, "item_instance", None)
    if inner is not None:
        item = inner
    if isinstance(item, dict):
        attributes = item.get("attributes")

        def field(name):
            return item.get(name)
    else:
        attributes = getattr(item, "attributes", None)

        def field(name):
            return getattr(item, name, None)

    if not isinstance(attributes, dict):
        attributes = None
    return field, attributes


def find_key(attributes, candidates):
    for key in candidates:
        if key in attributes:
            return key
    return None


def own_key(attributes, name):
    """こちらが書いた鍵。言語を跨いで探す（別の言語で作られた品もある）。"""
    for table in LABELS.values():
        if table[name] in attributes:
            return table[name]
    return None


def kind_of(item):
    """この分類が扱う細分なら種類を、扱わないなら None。"""
    field, attributes = read_item(item)
    if attributes is None or field("item_type") not in ITEM_TYPES:
        return None
    return KIND_BY_DETAIL.get(attributes.get("item_detail"))


def is_reworked(attributes):
    """作り直し済みか。`疲労負荷` を 0 で書き残すのが印（本体は 9〜11 を書く）。"""
    burden_key = find_key(attributes, BURDEN_KEYS)
    if burden_key is None or num(attributes.get(burden_key)) != 0.0:
        return False
    if find_key(attributes, HEAL_KEYS) is not None:
        return True
    return any(own_key(attributes, name) is not None
               for name in ("stamina", "hp_delta", "stamina_delta"))


def rarity_mult(rarity, rules):
    return float(rules["rarity"].get(rarity, 1.0))


def spread(rng, variance):
    """±variance の乱数。0 なら 1.0。"""
    variance = float(variance)
    if variance <= 0:
        return 1.0
    return rng.uniform(1.0 - variance, 1.0 + variance)


def stamina_amount(value, rarity, detail, rules, rng=random):
    """スタミナの回復量。(底 + 価値段階 × 上乗せ) × レア度 × 飲み物の割合 × (1 ± stamina_variance)。

    乱数は生成時に1回引いて品に書く。
    """
    amount = (float(rules["stamina_base"])
              + float(rules["stamina_per_value"]) * value) * rarity_mult(rarity, rules)
    if detail == "drink":
        amount *= float(rules["drink_rate"])
    amount *= spread(rng, rules["stamina_variance"])
    return max(1, int(round(amount)))


def hp_percent(value, rarity, rules, rng=random, rate=1.0):
    """HP の回復の割合（最大 HP の %）。(底 + 価値段階 × 上乗せ) × レア度 × rate × (1 ± hp_variance)。"""
    pct = (float(rules["hp_base_pct"])
           + float(rules["hp_pct_per_value"]) * value) * rarity_mult(rarity, rules)
    pct *= float(rate)
    pct *= spread(rng, rules["hp_variance"])
    return max(0.1, pct)


def signed_roll(magnitude, rng, rules):
    """増減の幅を1つ引く。大きさは `herb_min_rate`〜1.0 倍、向きは `herb_good_chance`。"""
    size = magnitude * rng.uniform(float(rules["herb_min_rate"]), 1.0)
    return size if rng.random() < float(rules["herb_good_chance"]) else -size


def rework(item, rules, lang, rng=random):
    """`attributes` を細分の効き方に書き換える。作り直し済み・対象外なら False。

    戻り値は書いた効果の辞書（記録用）。
    鍵の並びは `item_detail` → 効果 → `疲労負荷` 0 → 残り（値段など）。
    辞書は同じものを中で組み直す（品が握っている参照を替えない）。
    """
    kind = kind_of(item)
    if kind is None:
        return False
    field, attributes = read_item(item)
    if is_reworked(attributes):
        return False

    detail = attributes.get("item_detail")
    value = num(field("value")) or 1.0
    rarity = field("rarity")
    heal_key = find_key(attributes, HEAL_KEYS) or lang["heal"]
    burden_key = find_key(attributes, BURDEN_KEYS) or lang["burden"]
    no_spread = dict(rules, hp_variance=0.0, stamina_variance=0.0)

    effects = {}
    if kind == "stamina":
        effects[lang["stamina"]] = stamina_amount(value, rarity, detail, rules, rng)
    elif kind == "hp":
        effects[heal_key] = format_percent(hp_percent(value, rarity, rules, rng))
    elif kind == "potion":
        # 医薬品より少なく戻す。同量だと医薬品を選ぶ理由が無くなる。
        effects[heal_key] = format_percent(
            hp_percent(value, rarity, rules, rng, rules["potion_hp_rate"]))
        if rules["potion_clears_status"]:
            effects[lang["ailment"]] = lang["clear"]
    elif kind == "herb":
        # 幅は signed_roll が持つので、底には乱数を掛けない（レア度は掛ける）。
        effects[lang["hp_delta"]] = format_signed_percent(
            signed_roll(hp_percent(value, rarity, no_spread), rng, rules))
        stamina = signed_roll(stamina_amount(value, rarity, "food", no_spread), rng, rules)
        effects[lang["stamina_delta"]] = int(round(stamina)) or (1 if stamina > 0 else -1)

    drop = set(HEAL_KEYS) | set(BURDEN_KEYS) | set(effects)
    for table in LABELS.values():
        drop.update(table[name] for name in OWN_KEY_NAMES)
    rest = [(key, val) for key, val in attributes.items()
            if key != "item_detail" and key not in drop]

    rebuilt = []
    if "item_detail" in attributes:
        rebuilt.append(("item_detail", attributes["item_detail"]))
    rebuilt.extend(effects.items())
    rebuilt.append((burden_key, 0))
    rebuilt.extend(rest)
    attributes.clear()
    attributes.update(rebuilt)
    return effects


def max_hp_of(owner):
    """割合を量に直す底。`max_hp` が読めなければ `current_hp`。どちらも無ければ None。"""
    for name in ("max_hp", "current_hp"):
        got = num(frames.attr(owner, name, None))
        if got is not None and got > 0:
            return got
    return None


def effect_of(item, owner=None):
    """品から効果を読む。`(HP の増減, スタミナの増減, 状態異常を消すか)`。

    割合で書かれた HP は `owner` の最大 HP から量に直す。
    `owner` が無い（読めない）ときは割合の項を 0 と見なす。
    """
    _field, attributes = read_item(item)
    if attributes is None:
        return 0, 0, False
    base = max_hp_of(owner) if owner is not None else None
    hp = stamina = 0

    def add_hp(raw):
        amount, is_pct = parse_amount(raw)
        if amount is None:
            return 0
        if is_pct:
            return int(round(base * amount / 100.0)) if base else 0
        return int(round(amount))

    key = own_key(attributes, "stamina")
    if key is not None:
        stamina += int(num(attributes.get(key)) or 0)
    key = own_key(attributes, "stamina_delta")
    if key is not None:
        stamina += int(num(attributes.get(key)) or 0)
    key = own_key(attributes, "hp_delta")
    if key is not None:
        hp += add_hp(attributes.get(key))
    key = find_key(attributes, HEAL_KEYS)
    if key is not None:
        hp += add_hp(attributes.get(key))
    clear = own_key(attributes, "ailment") is not None
    return hp, stamina, clear


def inventory_size(character):
    inventory = frames.attr(character, "inventory", None)
    if isinstance(inventory, (dict, list, tuple)):
        return len(inventory)
    return None


def plan(owner, hp_delta, stamina_delta, clear):
    """実際に動く量（上限と下限で切った後）。本体を呼ぶ前に決める。

    HP は 1 を下回らせない（薬草で死なせない）。
    既に上限を超えている HP は減らさない（GAME.md §2.19 の観測）。
    """
    out = {"hp": 0, "stamina": 0, "clear": 0}
    stamina = num(frames.attr(owner, "physical_integrity", None))
    cap = num(frames.attr(owner, "max_physical_integrity", None))
    if stamina is not None and stamina_delta:
        target = stamina + stamina_delta
        if cap is not None and target > max(cap, stamina):
            target = max(cap, stamina)
        if target < 0:
            target = 0
        out["stamina"] = int(round(target - stamina))
    hp = num(frames.attr(owner, "current_hp", None))
    cap = num(frames.attr(owner, "max_hp", None))
    if hp is not None and hp_delta:
        target = hp + hp_delta
        if cap is not None and target > max(cap, hp):
            target = max(cap, hp)
        if target < 1:
            target = min(hp, 1)
        out["hp"] = int(round(target - hp))
    status = frames.attr(owner, "status", None)
    if clear and isinstance(status, dict):
        out["clear"] = len(status)
    return out


def commit(owner, planned):
    """決めた量を書く。型は元の型に合わせる。"""
    for attr_name, key in (("physical_integrity", "stamina"), ("current_hp", "hp")):
        delta = planned[key]
        if not delta:
            continue
        current = frames.attr(owner, attr_name, None)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            continue
        new = current + delta
        setattr(owner, attr_name, new if isinstance(current, float) else int(new))
    if planned["clear"]:
        status = frames.attr(owner, "status", None)
        if isinstance(status, dict):
            status.clear()


class Silenced(object):
    """本体の `consume_item` を呼ぶ間だけ `回復` を 0 にする。抜けたら元に戻す。

        with Silenced(attributes, lang):
            orig(...)

    鍵が無かった品には足してから抜く（本体は `attributes["回復"]` を読む。
    無いときの挙動は測っていないので、必ず在る状態で通す）。
    割合の文字列（`25%`）も本体には読ませない。
    """

    def __init__(self, attributes, lang):
        self.attributes = attributes
        self.key = find_key(attributes, HEAL_KEYS)
        self.had = self.key is not None
        if not self.had:
            self.key = lang["heal"]
        self.old = attributes.get(self.key)

    def __enter__(self):
        self.attributes[self.key] = 0
        return self

    def __exit__(self, *exc):
        if self.had:
            self.attributes[self.key] = self.old
        else:
            self.attributes.pop(self.key, None)
        return False


class Hidden(object):
    """説明欄を描く間だけ `疲労負荷: 0` を伏せる。並びごと控えて戻す。

    抜いた鍵を後ろへ足し直すと項目の並びが変わる
    （セーブの並びはゲームの形のまま保つ。`129_` と同じ理由）。
    """

    def __init__(self, attributes):
        self.attributes = attributes
        self.saved = None
        if attributes is not None and is_reworked(attributes):
            key = find_key(attributes, BURDEN_KEYS)
            if key is not None:
                self.saved = list(attributes.items())
                self.key = key

    def __enter__(self):
        if self.saved is not None:
            del self.attributes[self.key]
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            self.attributes.clear()
            self.attributes.update(self.saved)
        return False
