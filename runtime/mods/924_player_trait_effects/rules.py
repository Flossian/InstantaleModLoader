# -*- coding: utf-8 -*-
"""特質を効果へ置き換える決まり（ゲームに触らない部分）。DOC.md。

入口（`player_trait_effects.py`）が設定をまとめて渡す。ここは設定もゲームも読まない。

    効果の一覧   ability（能力値の補正。常に／条件つき）・price（店の買値と売値）
    頼み文       build_messages(player, traits, severity_mode)
    返答の検算   normalize(data, traits, ...)   一覧に無いもの・範囲の外を捨てる
    重さ         weight(effects)                 severity と比べる（記録と「寄せる」に使う）
    表示         effect_text / summary / note_block
"""

import hashlib
import json

#: 能力値の鍵（`ability_scores` の綴り。窓口 `abilities` と同じ）。
STATS = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")

STAT_LABELS = {
    "ja": {"strength": "筋力", "dexterity": "器用", "constitution": "耐久",
           "intelligence": "知力", "wisdom": "判断", "charisma": "魅力"},
    "en": {"strength": "STR", "dexterity": "DEX", "constitution": "CON",
           "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA"},
}

#: 能力値の補正が効く条件。並びは頼み文と表示の順。名前は窓口の場面（`abilities.SITUATIONS`）と同じ。
CONDITIONS = ("always", "quest", "battle", "boss", "low_hp")

#: 前の版の条件 → 今の条件。ダンジョンは依頼の中にしか無く、遊ぶ側に依頼中との違いが見えないのでまとめた。
#: 置き換え済みの控えに残る `dungeon` は読むときに `quest` として扱う。
LEGACY_CONDITIONS = {"dungeon": "quest"}

#: 条件の重さ（常に効く補正を 1 とした割合）。効く場面が狭いほど軽い。
#: 能力値が効く場面（戦闘・依頼の出来事）はほとんど依頼と戦闘の中なので、その2つは大きくは割り引かない
#: （0.5 / 0.75 だった版は、AI が量を大きくして特質1つが mythic の装備並みになった。見本の変換の実測）。
CONDITION_WEIGHT = {"always": 1.0, "quest": 0.8, "battle": 0.8, "boss": 0.4, "low_hp": 0.6}

#: 表示の頭（条件つきの補正だけ）。
CONDITION_PREFIX = {
    "ja": {"quest": "依頼中", "battle": "戦闘中", "boss": "ボス戦で", "low_hp": "体力半分以下で"},
    "en": {"quest": "On quests: ", "battle": "In battle: ", "boss": "In boss fights: ",
           "low_hp": "Below half HP: "},
}

#: 頼み文での条件の説明。
CONDITION_TEXT = {
    "always": "常に",
    "quest": "依頼の最中（道中・ダンジョン・戦闘・出来事）",
    "battle": "戦闘中",
    "boss": "ボス戦",
    "low_hp": "体力が最大の半分以下のとき",
}


def condition_of(effect):
    """効果の条件（前の版の名前は今の名前に直す）。"""
    raw = effect.get("condition") if isinstance(effect, dict) else None
    return LEGACY_CONDITIONS.get(raw, raw) if raw else "always"

PRICE_SIDES = ("buy", "sell")
PRICE_LABELS = {"ja": {"buy": "買値", "sell": "売値"},
                "en": {"buy": "Buy", "sell": "Sell"}}

#: 1つの補正の量の上限（絶対値）。411 の装備の補正（1品で epic +1〜2・legendary +2〜3・mythic +3〜4）に揃える。
AMOUNT_MAX = 3
#: 値段の割合の上限（絶対値、％）。
PERCENT_MAX = 20
#: 値段の重さ（この％で 1）。
PERCENT_PER_WEIGHT = 5.0
#: 重さの目安 = severity × この値。severity 3 で常に +1〜2（epic 相当）、6 で +3（legendary 相当）。
SEVERITY_SCALE = 0.5
#: 決まりの版。量の物差しを変えたら上げる（古い版で置き換えた特質は、次の照合で置き換え直す）。
RULES_VERSION = 2
#: 1つの特質に付ける効果の数の上限。
EFFECTS_PER_TRAIT = 3
#: 数値にならない性質の文の長さの上限。
NOTE_CHARS = 80

#: 頼みの記録の分かれ目（`output_data/<世界>/<主人公>/<名前>/`）。
MANAGER = "mod_trait_effects"

#: この MOD が LLM へ足す文の見出し。二重に足さない目印にも使う。
NOTE_HEAD = "【主人公の特質の効果】"
#: 変換の頼み文の目印（自分の頼みに効果の文を足さないため）。
CONVERTER_MARK = "【特質を効果に置き換える】"

#: severity の扱い。
SEVERITY_MODES = ("guide", "fit", "off")


# ================================================================== 特質の読み方
def trait_fields(trait):
    """特質1つを `(名前, 説明, severity)` にする。名前が無ければ None。"""
    if isinstance(trait, dict):
        name, description, severity = trait.get("name"), trait.get("description"), trait.get("severity")
    else:
        name = getattr(trait, "name", None)
        description = getattr(trait, "description", None)
        severity = getattr(trait, "severity", None)
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(description, str):
        description = ""
    if isinstance(severity, bool) or not isinstance(severity, (int, float)):
        severity = None
    else:
        severity = int(severity)
    return name.strip(), description.strip(), severity


def read_traits(value):
    """人物の `traits` を `[(名前, 説明, severity)]` にする。入れ物の形を決めつけない。"""
    items = value if isinstance(value, (list, tuple)) else (
        list(value.values()) if isinstance(value, dict) else [])
    found = []
    for item in items:
        fields = trait_fields(item)
        if fields is not None:
            found.append(fields)
    return found


def fingerprint(fields):
    """特質の印。名前・説明・severity のどれかが変われば変わる（変換し直す合図）。"""
    name, description, severity = fields
    raw = json.dumps([name, description, severity], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ================================================================== 頼み文
def build_messages(player_name, profile, traits, severity_mode="guide", profile_chars=400):
    """変換の頼み文。`traits` は `[(名前, 説明, severity)]`。"""
    stat_lines = "\n".join("  - {}（{}）".format(key, STAT_LABELS["ja"][key]) for key in STATS)
    condition_lines = "\n".join("  - {}: {}".format(key, CONDITION_TEXT[key]) for key in CONDITIONS)
    trait_lines = []
    for name, description, severity in traits:
        line = "- 名前: {}\n  説明: {}".format(name, description or "（説明なし）")
        if severity_mode != "off" and severity is not None:
            line += "\n  severity: {}".format(severity)
        trait_lines.append(line)
    profile = (profile or "").strip()
    if len(profile) > profile_chars:
        profile = profile[:profile_chars] + "…"
    severity_rules = ""
    if severity_mode != "off":
        severity_rules = (
            "- severity は特質の強さの目安（-10〜+10）。正なら利点が代償より重く、負なら代償が利点より重い。\n"
            "  効果の重さの合計（利点を正、代償を負として足したもの）が severity の半分に近くなるようにする"
            "（severity 4 なら 2、-2 なら -1）。severity が正なら合計を正に、負なら合計を負にする。\n"
            "  重さ: 常に効く能力値の補正は量の絶対値。条件つきは量の絶対値に、依頼の最中は {quest:g}、"
            "戦闘中は {battle:g}、体力が半分以下は {low_hp:g}、ボス戦は {boss:g} を掛ける。"
            "値段は割合の絶対値を {per:g} で割る。\n"
        ).format(per=PERCENT_PER_WEIGHT, **CONDITION_WEIGHT)
    user = (
        CONVERTER_MARK + "\n"
        "ゲームのキャラクターの特質（文で書かれた性質）を、ゲームが数値で処理できる効果に置き換える。\n\n"
        "【キャラクター】\n- 名前: {name}\n- 経歴: {profile}\n\n"
        "【置き換える特質】\n{traits}\n\n"
        "【使える効果】\n"
        "1. ability（能力値の補正）\n"
        "  stat は次の6つのどれか:\n{stats}\n"
        "  amount は -{amax}〜+{amax} の整数（0 は使わない）。\n"
        "  condition は効く場面。次のどれか:\n{conditions}\n"
        "  能力値が効く場面: 筋力は物理攻撃の威力、知力は魔法攻撃の威力、器用は戦闘で動く順番、"
        "6つすべてが受けるダメージの軽さと依頼の出来事（罠・探索・交渉・説得など）の成否、"
        "魅力は会話や依頼の後に相手が抱く印象。\n"
        "  察知・知識・勘は判断か知力、身のこなしは器用、体の丈夫さは耐久、人あたりや交渉は魅力に置く。\n"
        "2. price（店での値段）\n"
        "  side は buy（店から買う値段）か sell（店に売る値段）。\n"
        "  percent は -{pmax}〜+{pmax} の整数。buy の負は安く買える、sell の正は高く売れる。\n\n"
        "【決まり】\n"
        "- 説明に書かれていることだけを効果にする。書かれていない利点を足さない。\n"
        "- 「ただし」「その代わり」などの代償は、不利になる場面が説明に書かれているときだけ効果にする（負の量）。"
        "condition はその場面に合わせる。\n"
        "- 不利になる場面の書かれていない代償（見た目・噂・本人にも分からない・体の徴が広がる など）は"
        "数値にせず、note に書く。\n"
        "- 1つの特質に付ける効果は 0〜{per} 個。数値にならない性質しか無い特質は effects を空にする。\n"
        "- 同じ能力値を、重なって同時に効く条件（依頼の最中と戦闘中、戦闘中とボス戦 など）に分けて付けない。"
        "1つの条件にまとめる。\n"
        "- 効果にできない性質（特定の相手の態度、特定の属性への弱さなど）は、note に場面と結果を1文で書く"
        "（{note}字まで）。無ければ空にする。\n"
        "  note はキャラクターの性質だけを書く。置き換えの説明（「効果にしていない」など）や、説明に無い出来事は書かない。\n"
        "{severity}"
        "\n【出力】\n"
        "次の JSON だけを出力する。特質の名前はそのまま写す。\n"
        '{{"traits": [{{"name": "特質の名前", "effects": ['
        '{{"kind": "ability", "stat": "wisdom", "amount": 2, "condition": "always"}}, '
        '{{"kind": "price", "side": "buy", "percent": -5}}], "note": ""}}]}}'
    ).format(name=player_name or "（不明）", profile=profile or "（なし）",
             traits="\n".join(trait_lines), stats=stat_lines, conditions=condition_lines,
             amax=AMOUNT_MAX, pmax=PERCENT_MAX, per=EFFECTS_PER_TRAIT, note=NOTE_CHARS,
             severity=severity_rules)
    system = "あなたはTRPGのルールの設計者。文で書かれた性質を、ゲームの決まった効果の組み合わせに置き換える。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ================================================================== 返答の検算
def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(round(float(value.strip().lstrip("+"))))
        except ValueError:
            return None
    return None


def clean_effect(raw):
    """返答の効果1つを決まった形にする。使えなければ None。"""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind == "ability":
        stat = str(raw.get("stat") or "").strip().lower()
        amount = _int(raw.get("amount"))
        condition = str(raw.get("condition") or "always").strip().lower()
        condition = LEGACY_CONDITIONS.get(condition, condition)
        if stat not in STATS or not amount or condition not in CONDITIONS:
            return None
        amount = max(-AMOUNT_MAX, min(AMOUNT_MAX, amount))
        return {"kind": "ability", "stat": stat, "amount": amount, "condition": condition}
    if kind == "price":
        side = str(raw.get("side") or "").strip().lower()
        percent = _int(raw.get("percent"))
        if side not in PRICE_SIDES or not percent:
            return None
        percent = max(-PERCENT_MAX, min(PERCENT_MAX, percent))
        return {"kind": "price", "side": side, "percent": percent}
    return None


def _merge(effects):
    """同じ能力値・同じ条件、同じ値段の側の効果を1つに足す（足した結果が 0 なら落とす）。"""
    merged = {}
    order = []
    for effect in effects:
        key = (effect["kind"], effect.get("stat"), effect.get("condition"), effect.get("side"))
        if key not in merged:
            merged[key] = dict(effect)
            order.append(key)
            continue
        field = "amount" if effect["kind"] == "ability" else "percent"
        limit = AMOUNT_MAX if field == "amount" else PERCENT_MAX
        total = merged[key][field] + effect[field]
        merged[key][field] = max(-limit, min(limit, total))
    return [merged[key] for key in order
            if merged[key].get("amount" if key[0] == "ability" else "percent")]


def _one_per_stat(effects):
    """1つの特質の中で、同じ能力値の利点と代償をそれぞれ1つまでにする（量の大きい方を残す）。

    条件はどれも同時に立ちうる（依頼の中の戦闘、体力が減った戦闘）ので、`依頼中判断+3・戦闘中判断+2`
    は依頼の中の戦闘で +5 になっていた（見本の変換で 159件中 25件。頼み文で止めても 14件残った）。
    同じ量なら効く場面の広い方（重さの割合が大きい方）を残す。並びは元のまま。
    """
    best = {}
    for index, effect in enumerate(effects):
        if effect.get("kind") != "ability":
            continue
        key = (effect["stat"], effect["amount"] > 0)
        rank = (abs(effect["amount"]), CONDITION_WEIGHT.get(effect.get("condition"), 0))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, index)
    keep = {index for _rank, index in best.values()}
    return [effect for index, effect in enumerate(effects)
            if effect.get("kind") != "ability" or index in keep]


def clean_note(value):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text if len(text) <= NOTE_CHARS else text[:NOTE_CHARS] + "…"


def normalize(data, traits):
    """返答を `{名前: {"effects": [...], "note": str}}` にする。読めなければ None。

    `traits` は頼んだ特質（`[(名前, 説明, severity)]`）。名前が合わない返答は捨てる。
    名前の揺れ（前後の空白・括弧書き）は、頼んだ名前が1つに決まるときだけ拾う。
    """
    if not isinstance(data, dict):
        return None
    entries = data.get("traits")
    if not isinstance(entries, list):
        return None
    wanted = [name for name, _d, _s in traits]
    found = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if name not in wanted:
            close = [w for w in wanted if name and (name in w or w in name)]
            if len(close) != 1:
                continue
            name = close[0]
        effects = [clean_effect(raw) for raw in (entry.get("effects") or [])
                   if isinstance(entry.get("effects"), list)]
        effects = _one_per_stat(_merge([effect for effect in effects if effect is not None]))[:EFFECTS_PER_TRAIT]
        found[name] = {"effects": effects, "note": clean_note(entry.get("note"))}
    return found


# ================================================================== 重さ
def effect_weight(effect):
    """効果1つの重さ（利点は正、代償は負）。"""
    if effect.get("kind") == "ability":
        return effect["amount"] * CONDITION_WEIGHT.get(condition_of(effect), 1.0)
    if effect.get("kind") == "price":
        sign = -1 if effect.get("side") == "buy" else 1
        return sign * effect["percent"] / PERCENT_PER_WEIGHT
    return 0.0


def weight(effects):
    """効果の重さの合計。severity と比べる値。"""
    return round(sum(effect_weight(effect) for effect in effects or ()), 2)


def target_weight(severity):
    """severity に見合う重さの合計（目安）。"""
    return round(severity * SEVERITY_SCALE, 2)


def fit_to_severity(effects, severity):
    """効果の重さを severity の目安（`target_weight`）に寄せる（severity の扱い「寄せる」）。

    重さと severity の向きが同じときだけ、量を同じ割合で縮める・伸ばす（各量の上限は守る）。
    向きが違う（利点だけの文に負の severity が付いた など）・重さが 0・severity が無いときは触らない。
    戻り値は `(効果, 寄せたか)`。
    """
    total = weight(effects)
    if severity is None or not severity or not total or (total > 0) != (severity > 0):
        return list(effects), False
    rate = target_weight(severity) / total
    if abs(rate - 1.0) < 0.15:
        return list(effects), False
    fitted = []
    for effect in effects:
        effect = dict(effect)
        field, limit = ("amount", AMOUNT_MAX) if effect["kind"] == "ability" else ("percent", PERCENT_MAX)
        value = int(round(effect[field] * rate))
        if value == 0:
            value = 1 if effect[field] > 0 else -1
        effect[field] = max(-limit, min(limit, value))
        fitted.append(effect)
    return fitted, True


# ================================================================== 効かせる
def ability_totals(records, active, cap):
    """効いている条件 `active` のもとでの能力値の補正の合計 `{能力値: 量}`。

    `records` は特質ごとの控え（`effects` を持つ辞書）の並び。`cap` は能力値ごとの上限（0 で無し）。
    """
    totals = {}
    for record in records:
        for effect in record.get("effects") or ():
            if effect.get("kind") != "ability" or condition_of(effect) not in active:
                continue
            totals[effect["stat"]] = totals.get(effect["stat"], 0) + effect["amount"]
    if cap:
        totals = {key: max(-cap, min(cap, value)) for key, value in totals.items()}
    return {key: value for key, value in totals.items() if value}


def price_percent(records, side, cap):
    """値段の割合の合計（％）。`cap` は上限（0 で無し）。"""
    total = 0
    for record in records:
        for effect in record.get("effects") or ():
            if effect.get("kind") == "price" and effect.get("side") == side:
                total += effect["percent"]
    if cap:
        total = max(-cap, min(cap, total))
    return total


def priced(price, percent):
    """`price` に `percent`％を掛けた額（1 より下げない）。"""
    return max(1, int(round(price * (1.0 + percent / 100.0))))


# ================================================================== 表示
def effect_text(effect, lang="ja"):
    labels = STAT_LABELS.get(lang) or STAT_LABELS["ja"]
    if effect.get("kind") == "ability":
        prefix = "" if condition_of(effect) == "always" else \
            (CONDITION_PREFIX.get(lang) or CONDITION_PREFIX["ja"]).get(condition_of(effect), "")
        return "{}{}{:+d}".format(prefix, labels[effect["stat"]], effect["amount"])
    if effect.get("kind") == "price":
        names = PRICE_LABELS.get(lang) or PRICE_LABELS["ja"]
        return "{}{:+d}%".format(names[effect["side"]], effect["percent"])
    return ""


def summary(record, lang="ja"):
    """特質1つの効果の短い文（人物欄に出す）。効果が無ければ空。"""
    texts = [effect_text(effect, lang) for effect in record.get("effects") or ()]
    return ("・" if lang == "ja" else ", ").join(text for text in texts if text)


def note_block(entries):
    """LLM へ足す文。`entries` は `[(名前, 控え)]`。効果も性質の文も無ければ空。"""
    lines = []
    for name, record in entries:
        parts = []
        text = summary(record, "ja")
        if text:
            parts.append(text)
        note = record.get("note")
        if note:
            parts.append(note)
        if parts:
            lines.append("- {}: {}".format(name, "／".join(parts)))
    if not lines:
        return ""
    return "\n".join([
        NOTE_HEAD,
        "特質はゲームの数値に次のとおり反映されている（能力値はゲームが計算に足す）。"
        "描写と判定はこれと食い違わないようにし、文の性質は関わる場面で判定と描写に反映する。",
    ] + lines)


def display_row(name, record, lang="ja"):
    """人物欄（`121_`）が読む1行 `{"name", "text", "effects"}`。DOC.md の「記録の置き場」。

    `text` は効果をつないだ1文、`effects` は効果ごとの `{"text", "condition"}` の並び。
    説明の窓は1行ずつ出し、`condition` が窓口の場面（`abilities.situations`）に無ければ灰色にする。
    値段の効果の条件は `always`。
    """
    effects = []
    for effect in (record or {}).get("effects") or ():
        text = effect_text(effect, lang)
        if text:
            effects.append({"text": text,
                            "condition": condition_of(effect) if effect.get("kind") == "ability" else "always"})
    return {"name": name, "text": summary(record or {}, lang), "effects": effects}
