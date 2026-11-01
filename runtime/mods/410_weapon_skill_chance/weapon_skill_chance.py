# -*- coding: utf-8 -*-
"""武器が作られたとき・手に入ったときに、確率でスキルを付ける。

## なぜ要るか

武器のスキル（`Item.skill` と `attributes["スキル"]`）を作るのは
本体の `llm_manager:item_skill_generator(item, usefulness)`。
強さは `functions:get_item_skill_usefulness(item_value, item_rarity)` が決める。

素のゲームでこれが呼ばれるのは店の品揃えだけで、
クラフトとテキスト・イベントで手に入る武器にはスキルが付かない
（ゲームの LLM 記録 `output_data\\...\\item_skill_generator` を生成記録と突き合わせた結果）。

## どうするか

経路ごとに確率を持ち、当たった武器にだけ本体の2本を呼んでスキルを付ける。
スキルの中身（`element` / `skill_type` / `effects` / `max_uses`）は本体の生成に任せ、
こちらでは作らない（TECH.md §6。本体の処理を作り直さない）。
確率には主人公の INT と DEX で倍率を掛けられる（作り手の腕前。`ability_rate`）。
INT と DEX はローダの能力値の窓口から、装備などの補正を足した値で読む（TECH.md §3.3.27）。

  * 店: `ShoppingStartManagerRemake.generate_item_in_shopping` の前後で
    主の持ち物を比べ、増えた武器のうちスキルが無いものだけを抽選する
    （本体の抽選が済んだ後なので、本体が付けた品には重ねない）
  * クラフト: `ItemCraftManager.execute` の最中に
    `generate_item_from_item_data` が作った武器。取りこぼしは `place_crafted_item` で拾う
  * それ以外: 上の2つの外で `generate_item_from_item_data` が主人公に作った武器と、
    テキストで頼んで `master_ai_item_generate` が名付けた武器（イベント・依頼で手に入る品）。
    入口は品を返すとは限らないので、入口の最中に `Item.__init__` で組まれた武器を拾う。
    `generate_item_from_dict` はセーブからの組み直しにも使われるので見ない
  * 上の2つの外で世界の人物（`obtainer`。店の主・仲間など）に作った武器は店の扱い（店の品揃えを
    入れ替える `312_` / `340_` は店の主へ、`407_` は仲間へ作る）。主人公の腕前は掛けない。
    敵（世界の人物に居ない）に作った武器は「それ以外」（戦闘の前に作られ、倒すと手に入る）

## スレッド

スキルの生成は LLM を1回呼ぶ（数秒）。
ワーカースレッド（`execute` の中）ならその場で待つ。品が画面に出る前に付く。
メインスレッドに来たときだけ別スレッドで作り、`Clock` でメインスレッドへ戻して書く
（UI を止めない。GAME.md §2.1）。

失敗したらスキル無しのまま通す。1つの品を抽選するのは1回だけ（外れても引き直さない）。

## 説明欄

スキル付きの品の説明欄には、描く間だけスキル名の下に効果と使用回数の行を足す
（`ShownEffect`。セーブには入らない）。効果の文はゲーム自身の `skill_effect_to_text` を使う。

## 仲間の武器のスキル

仲間（と敵）の技は審判（`referee_npc`）が `Character.skills` から選ぶ。
武器のスキルは品の中（`Item.skill`）に在るだけで、この一覧に入らないので、仲間は使わない。
そこで戦闘の間だけ、仲間が身に着けている品のスキルを `skills` へ足す（`PartySkills`）。

  * 足すのは `BattleStartManager.start_battle` の後、外すのは戦闘の終わり（3つの `end_phase`）。
    審判が選んだ後にゲームがどこでスキルを引くかは確かめていないので、審判の間だけにはしない
  * 足すのは品のスキルの辞書そのもの。ゲームが減らした残り回数は品に残る
  * `save_game` の間は外す（セーブの `skills` に品のスキルを入れない）
  * 宿で休養したとき（`VacationRestManager.execute`）、仲間の身に着けている品の回数を上限へ戻す
    （`137_` が仲間の `skills` を戻すのと同じ所）
  * 使い切ったスキルを審判から隠すのは `137_`。戦闘の開始で足すので、`137_` の絞り込みにもそのまま載る

素のゲームの仲間は装備を持たない（GAME.md §2.31）。仲間に装備させるのは
`402_party_inventory_transfer` / `333_equipment_slots`。
"""

import random
import re
import sys
import threading
import weakref

from instantale_modloader import abilities, equipment, frames, items, llm, ui

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
CHANCE_CRAFT = 20.0         # クラフトで作った武器（%）
CHANCE_OTHER = 5.0          # テキストで作った武器・イベントや依頼で手に入る武器（%）
CHANCE_SHOP = 0.0           # 店の武器でゲームが付けなかったもの（%）
SHOW_EFFECT = True          # 説明欄のスキル名の下に効果と使用回数を出す
PARTY_SKILLS = True         # 仲間も身に着けている武器のスキルを戦闘で使う

# 主人公の INT と DEX で確率に倍率を掛ける（作り手の腕前）。
ABILITY_SCALING = True      # 倍率を使う
ABILITY_SCOPE = "craft_other"   # craft: クラフトだけ / craft_other: テキストで作った品・入手品にも
ABILITY_SURE_AT = 60        # INT か DEX がこれ以上なら 100%（0 で使わない）
ABILITY_ZERO_AT = 10        # INT か DEX がこれ以下なら 0%
ABILITY_DOUBLE_AT = 25      # INT と DEX がどちらもこれ以上なら2段目
ABILITY_TRIPLE_SUM = 61     # INT と DEX の合計がこれ以上なら3段目
ABILITY_DOUBLE_RATE = 2.0   # 2段目の倍率
ABILITY_TRIPLE_RATE = 3.0   # 3段目の倍率

LOG_BASENAME = "weapon_skill_chance.log"
LOG_LIMIT = 300

WEAPON_TYPE = "weapon"

# `attributes` に書くスキル名の鍵。ja は本体が店の品に書く綴り。en は未確認。
SKILL_LABELS = {"ja": "スキル", "en": "Skill"}

# 能力値の名前（`313_` の `ABILITIES` と同じ綴り）。
INT_NAME = "intelligence"
DEX_NAME = "dexterity"

# 説明欄に足す行の名前（描く間だけ足す。セーブには入らない）。
EFFECT_LABELS = {"ja": ("効果", "使用回数"), "en": ("Effect", "Uses")}

# `skill_effect_to_text` が引けないときの自前の言い方（ja のみ。en は綴りのまま）。
TARGET_WORDS = {"single_enemy": "敵単体", "all_enemies": "敵全体", "single_ally": "味方単体",
                "all_allies": "味方全体", "self": "自分", "everyone": "全員"}
POWER_WORDS = {"weak": "弱", "normal": "中", "strong": "強", "very_strong": "特大",
               "extreme": "極大"}
TYPE_WORDS = {"instant_damage": "ダメージ", "instant_heal": "回復", "buff": "強化",
              "debuff": "弱体", "text_status": "状態異常"}

# 値段の鍵（`129_` / GAME.md §2.13.2）。スキル名はこの手前に入れる（実物の並び）。
PRICE_KEYS = ("買価", "売価", "buy price", "sell price", "Buy price", "Sell price")

# 仲間のスキルを足す・外す所。
BATTLE_START_TARGET = "__main__:BattleStartManager.start_battle"
BATTLE_END_TARGETS = ("__main__:BattleEndManager.end_phase",
                      "__main__:BattleEndInColosseum.end_phase",
                      "__main__:BattleEndInFreeAction.end_phase")
SAVE_TARGET = "__main__:InstantaleApp.save_game"
REST_TARGET = "__main__:VacationRestManager.execute"

# 本体の装備の2枠（GAME.md §2.13.3）と、`333_` の自由枠の名前の頭（手に持っていない品）。
EQUIPMENT_TYPES = ("weapon", "wearable")
FREE_PREFIX = "free"

# 再注入しても数え直さない・抽選済みを忘れない。
STORE_ATTR = "_instantale_weapon_skill_chance_store"


def process_store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {}
        setattr(sys, STORE_ATTR, store)
    store.setdefault("rolled", weakref.WeakSet())
    store.setdefault("ids", set())       # weakref を取れない品の控え（id）
    store.setdefault("logged", 0)
    store.setdefault("counts", {})
    store.setdefault("pending", {})      # master_ai_item_generate が名付けた武器
    store.setdefault("local", threading.local())
    store.setdefault("lent", [])         # 仲間の skills へ足したもの [(持ち主, 名前, スキル)]
    return store


def unwrap(item):
    """画面の部品（`InventoryItem`）が来たら中の `item_instance` を取り出す。"""
    inner = frames.attr(item, "item_instance", None)
    return item if inner is None else inner


def is_bare_weapon(item):
    """スキルの無い武器（`Item` インスタンス）なら True。"""
    if item is None or isinstance(item, dict):
        return False
    if frames.attr(item, "item_type", None) != WEAPON_TYPE:
        return False
    return not frames.attr(item, "skill", None)


def language():
    """ゲームの表示言語。`scripts.languages.language`。読めなければ ja。"""
    module = sys.modules.get("scripts.languages")
    lang = getattr(module, "language", None) if module is not None else None
    return "en" if lang == "en" else "ja"


def usefulness_of(item):
    """本体の `get_item_skill_usefulness(value, rarity)`。引けなければ None。"""
    module = sys.modules.get("scripts.functions")
    func = getattr(module, "get_item_skill_usefulness", None)
    if not callable(func):
        return None
    value = frames.attr(item, "value", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return func(value, frames.attr(item, "rarity", None) or "common")


def item_brief(item):
    """`item_skill_generator` に辞書で渡すときの形（LLM 記録の頼み文の項目）。"""
    return {
        "name": frames.attr(item, "name", ""),
        "type": frames.attr(item, "item_type", WEAPON_TYPE),
        "item_type": frames.attr(item, "item_type", WEAPON_TYPE),
        "description": frames.attr(item, "description", ""),
        "value": frames.attr(item, "value", None),
        "rarity": frames.attr(item, "rarity", None),
        "attributes": frames.attr(item, "attributes", None),
    }


def generate_skill(item, usefulness):
    """本体の `item_skill_generator` でスキルを1つ作る。作れなければ None。

    本体が渡す `item` の形は決め打ちしない。`Item` で読めなければ辞書で頼み直す
    （どちらも LLM へ送る前の読み取りで落ちるので、推論は1回で済む）。
    """
    module = sys.modules.get("scripts.llm.llm_manager")
    func = getattr(module, "item_skill_generator", None)
    if not callable(func):
        return None
    try:
        raw = func(item, usefulness)
    except (AttributeError, TypeError, KeyError):
        raw = func(item_brief(item), usefulness)
    skill = llm.as_dict(raw)
    if not isinstance(skill, dict):
        return None
    skill = dict(skill)
    if not skill.get("name") or not isinstance(skill.get("effects"), list):
        return None
    if not skill["effects"]:
        return None
    uses = skill.get("max_uses")
    if isinstance(uses, bool) or not isinstance(uses, int) or uses < 1:
        uses = 5                            # 本体の生成で最も多い回数
        skill["max_uses"] = uses
    skill.setdefault("current_uses", uses)
    return skill


def attach(item, skill):
    """品にスキルを書く。`attributes` は同じ辞書の中で並べ直す（参照を替えない）。"""
    setattr(item, "skill", skill)
    attributes = frames.attr(item, "attributes", None)
    if not isinstance(attributes, dict):
        return
    key = SKILL_LABELS[language()]
    rebuilt = [(k, v) for k, v in attributes.items()
               if k not in PRICE_KEYS and k not in SKILL_LABELS.values()]
    rebuilt.append((key, skill["name"]))
    rebuilt.extend((k, v) for k, v in attributes.items() if k in PRICE_KEYS)
    attributes.clear()
    attributes.update(rebuilt)


def score_of(character, attribute):
    """キャラクタの能力値。読めなければ None（`313_` の `score_of` と同じ順に当たる）。

    最初はローダの能力値の窓口（装備などの補正を足した値）に聞く。
    """
    if character is None:
        return None
    raw = None
    boosted = abilities.scores(ui.find_app(), character)
    if isinstance(boosted, dict) and boosted.get(attribute) is not None:
        raw = boosted.get(attribute)
    for name in ("ability_scores", "original_ability_scores"):
        if raw is not None:
            break
        table = getattr(character, name, None)
        if isinstance(table, dict) and table.get(attribute) is not None:
            raw = table.get(attribute)
            break
    if raw is None:
        raw = getattr(character, "attribute_" + attribute, None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw


def ability_rate(intelligence, dexterity):
    """INT と DEX から確率の倍率と段の名前を返す。

    段は上から順に見る: どちらかがとても高い → 必ず（倍率 None）/ どちらかが低い → 0 /
    合計が高い → 3段目 / どちらも高い → 2段目 / それ以外 → 1倍。
    """
    if ABILITY_SURE_AT > 0 and (intelligence >= ABILITY_SURE_AT or dexterity >= ABILITY_SURE_AT):
        return None, "sure"
    if intelligence <= ABILITY_ZERO_AT or dexterity <= ABILITY_ZERO_AT:
        return 0.0, "low"
    if intelligence + dexterity >= ABILITY_TRIPLE_SUM:
        return float(ABILITY_TRIPLE_RATE), "triple"
    if intelligence >= ABILITY_DOUBLE_AT and dexterity >= ABILITY_DOUBLE_AT:
        return float(ABILITY_DOUBLE_RATE), "double"
    return 1.0, "base"


def fallback_effect_text(effect):
    """ゲームの関数が文にしなかった効果1つの言い方（ゲームの「敵単体に中威力のダメージを与える。」に寄せる）。"""
    kind = effect.get("type")
    target = TARGET_WORDS.get(effect.get("target_type"), str(effect.get("target_type")))
    power = POWER_WORDS.get(effect.get("power"), "")
    turns = effect.get("duration")
    turns = "（{}ターン）".format(turns) if isinstance(turns, int) else ""
    if kind == "instant_damage":
        return "{}に{}威力のダメージを与える。".format(target, power)
    if kind == "instant_heal":
        return "{}を{}威力で回復する。".format(target, power)
    if kind in ("buff", "debuff"):
        attribute = str(effect.get("attribute_type") or "").upper()
        verb = "上げる" if kind == "buff" else "下げる"
        return "{}の{}を{}威力で{}{}。".format(target, attribute, power, verb, turns)
    if kind == "text_status":
        return "{}に状態異常「{}」を与える{}。".format(
            target, effect.get("status_name") or "?", turns)
    return "{}に{}。".format(target, TYPE_WORDS.get(kind, str(kind)))


def effect_text(skill):
    """スキルの効果を文にする。効果ごとにゲーム自身の `skill_effect_to_text` を先に使う。

    ゲームの関数は強化・弱体を文にしないことがあるので、効果を1つずつ頼み、
    空なら自前の言い方で埋める。
    """
    effects = skill.get("effects")
    if not isinstance(effects, list) or not effects:
        return None
    module = sys.modules.get("scripts.languages")
    func = getattr(module, "skill_effect_to_text", None)
    parts = []
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        text = None
        if callable(func):
            try:
                text = func([effect])
            except Exception:
                text = None
        if isinstance(text, str) and text.strip():
            parts.append("".join(line.strip() for line in text.splitlines()))
        elif language() == "ja":
            parts.append(fallback_effect_text(effect))
        else:
            parts.append("{} {} {}".format(effect.get("target_type"), effect.get("power"),
                                           effect.get("type")))
    return "\n".join(parts) if parts else None


def worn_items(app, holder):
    """身に着けている品の並び（同じ品は1度だけ。`411_` の読み方と同じ）。"""
    found = []
    gear = equipment.gear(app, holder)
    if gear is not None:
        found = [item for region, item in gear if not str(region).startswith(FREE_PREFIX)]
    else:
        equipments = frames.attr(holder, "equipments", None)
        if isinstance(equipments, dict):
            inventory = items.inventory_of(holder) or {}
            for key in EQUIPMENT_TYPES:
                ref = equipments.get(key)
                if isinstance(ref, (str, int)) and not isinstance(ref, bool):
                    ref = inventory.get(str(ref))      # ロード直後は id の文字列
                if ref is not None:
                    found.append(ref)
    unique, seen = [], set()
    for item in found:
        if id(item) not in seen:
            seen.add(id(item))
            unique.append(item)
    return unique


def worn_skills(app, holder):
    """身に着けている品のスキル `[(名前, スキルの辞書)]`。"""
    found = []
    for item in worn_items(app, holder):
        skill = frames.attr(unwrap(item), "skill", None)
        if isinstance(skill, dict) and isinstance(skill.get("name"), str) and skill["name"]:
            found.append((skill["name"], skill))
    return found


def party_members(app):
    """主人公を除いた同行者の Character。"""
    found = []
    for member_id in ui.party_member_ids(app):
        member = ui.character_of(app, member_id)
        if member is not None:
            found.append(member)
    return found


class PartySkills(object):
    """戦闘の間だけ、仲間の `skills` へ身に着けている品のスキルを足す。"""

    def __init__(self, lent):
        self.lent = lent        # [(持ち主, 名前, スキル)]。注入し直しをまたぐので store に置く

    def lend(self, app):
        """足す。足した `[(仲間の名前, スキル名)]` と、名前がかぶって足さなかったものを返す。"""
        added, clashed = [], []
        for member in party_members(app):
            skills = frames.attr(member, "skills", None)
            if not isinstance(skills, dict):
                continue
            for name, skill in worn_skills(app, member):
                if skills.get(name) is skill:
                    continue
                if name in skills:      # 本人の技と同じ名前なら本人の技を残す
                    clashed.append((frames.attr(member, "name", "?"), name))
                    continue
                uses = skill.get("max_uses")
                if isinstance(uses, int) and not isinstance(uses, bool):
                    skill.setdefault("current_uses", uses)
                skills[name] = skill
                self.lent.append((member, name, skill))
                added.append((frames.attr(member, "name", "?"), name))
        return added, clashed

    def take_back(self):
        """足したものを外す。外した数を返す。"""
        taken = 0
        for member, name, skill in self.lent:
            skills = frames.attr(member, "skills", None)
            if isinstance(skills, dict) and skills.get(name) is skill:
                del skills[name]
                taken += 1
        del self.lent[:]
        return taken

    def hidden(self):
        """セーブの間だけ外す。外したものを返す（`restore` に渡す）。"""
        gone = []
        for member, name, skill in self.lent:
            skills = frames.attr(member, "skills", None)
            if isinstance(skills, dict) and skills.get(name) is skill:
                del skills[name]
                gone.append((skills, name, skill))
        return gone

    @staticmethod
    def restore(gone):
        for skills, name, skill in gone:
            if name not in skills:
                skills[name] = skill


def refill_worn(app):
    """仲間の身に着けている品のスキルの回数を上限へ戻す。戻したものを返す。"""
    changed = []
    for member in party_members(app):
        for name, skill in worn_skills(app, member):
            uses, left = skill.get("max_uses"), skill.get("current_uses")
            if isinstance(uses, bool) or not isinstance(uses, int) or uses < 1:
                continue
            if left != uses:
                skill["current_uses"] = uses
                changed.append((frames.attr(member, "name", "?"), name, left, uses))
    return changed


class ShownEffect(object):
    """説明欄を描く間だけ、スキル名の下に効果と使用回数を足す。並びごと控えて戻す。

    `134_` の `healing.Hidden` と同じ形（`attributes` は同じ辞書の中で組み直して戻す）。
    """

    def __init__(self, item):
        self.attributes = None
        item = unwrap(item)
        skill = frames.attr(item, "skill", None) if item is not None else None
        attributes = frames.attr(item, "attributes", None) if item is not None else None
        if not isinstance(skill, dict) or not isinstance(attributes, dict):
            return
        text = effect_text(skill)
        if not text:
            return
        effect_key, uses_key = EFFECT_LABELS[language()]
        if effect_key in attributes or uses_key in attributes:
            return
        added = [(effect_key, text)]
        uses, left = skill.get("max_uses"), skill.get("current_uses")
        if isinstance(uses, int) and not isinstance(uses, bool) and uses > 0:
            added.append((uses_key, "{}/{}".format(left if isinstance(left, int) else uses,
                                                   uses)))
        rows = list(attributes.items())
        at = next((i + 1 for i, (key, _) in enumerate(rows)
                   if key in SKILL_LABELS.values()), None)
        if at is None:
            at = next((i for i, (key, _) in enumerate(rows) if key in PRICE_KEYS),
                      len(rows))
        self.attributes = attributes
        self.effect_key = effect_key
        self.saved = rows
        self.shown = rows[:at] + added + rows[at:]

    def __enter__(self):
        if self.attributes is not None:
            self.attributes.clear()
            self.attributes.update(self.shown)
        return self

    def __exit__(self, *exc):
        if self.attributes is not None:
            self.attributes.clear()
            self.attributes.update(self.saved)
        return False

    def tighten(self, label):
        """「効果: 」の空白で折り返されて名前だけの行が残るのを防ぐ。

        Kivy は空白で折り返すので、日本語の長い文が丸ごと次の行へ送られる。
        空白を全角のコロンに替えて、名前のすぐ後ろから文を続ける。
        箱の大きさは `109_` がこの後で測る（mod.json の before）。
        """
        if self.attributes is None or label is None:
            return
        text = frames.attr(label, "text", None)
        if not isinstance(text, str):
            return
        tight = re.sub(re.escape(self.effect_key) + r":[ \t]+",
                       self.effect_key + "：" if language() == "ja" else self.effect_key + ":",
                       text, count=1)
        if tight != text:
            label.text = tight


def apply(ctx):
    store = process_store()
    local = store["local"]
    write = ctx.logger(LOG_BASENAME)

    def note(text):
        store["logged"] += 1
        if store["logged"] <= LOG_LIMIT:
            write(text)
        elif store["logged"] == LOG_LIMIT + 1:
            write("... 以降は件数だけ数える（LOG_LIMIT={}）".format(LOG_LIMIT))

    def count(key):
        store["counts"][key] = store["counts"].get(key, 0) + 1

    def first_roll(item):
        """まだ抽選していない品なら印を付けて True。"""
        try:
            if item in store["rolled"]:
                return False
            store["rolled"].add(item)
            return True
        except TypeError:
            ident = id(item)
            if ident in store["ids"]:
                return False
            store["ids"].add(ident)
            return True

    def make_and_attach(item, source, usefulness):
        name = frames.short(frames.attr(item, "name", ""), 40)
        try:
            skill = generate_skill(item, usefulness)
        except Exception as exc:
            count("failed")
            note("{}: {!r} skill generation failed: {}: {}".format(
                source, name, type(exc).__name__, frames.short(exc, 120)))
            return None
        if skill is None:
            count("failed")
            note("{}: {!r} skill generation returned nothing usable".format(source, name))
            return None
        return skill

    def finish(item, source, skill):
        if not is_bare_weapon(item):     # 待つ間に本体が付けていたら譲る
            note("{}: {!r} already has a skill, kept the game's".format(
                source, frames.short(frames.attr(item, "name", ""), 40)))
            return
        attach(item, skill)
        count("attached/" + source)
        effects = ",".join(str(e.get("type")) + "/" + str(e.get("power"))
                           for e in skill["effects"] if isinstance(e, dict))
        note("{}: {!r} +skill {!r} [{} / {}] {} uses={}".format(
            source, frames.short(frames.attr(item, "name", ""), 40),
            frames.short(skill.get("name"), 40), skill.get("skill_type"),
            skill.get("element"), effects, skill.get("max_uses")))

    def scaled(chance, source):
        """主人公の INT と DEX で倍率を掛けた確率（%。100 で頭打ち）。"""
        if not ABILITY_SCALING or chance <= 0 or source == "shop":
            return chance               # 店の武器には掛けない（mod.json の ABILITY_SCOPE の説明）
        if source != "craft" and ABILITY_SCOPE != "craft_other":
            return chance
        player = frames.attr(ui.find_app(), "player", None)
        intelligence = score_of(player, INT_NAME)
        dexterity = score_of(player, DEX_NAME)
        if intelligence is None or dexterity is None:
            note("{}: INT/DEX unreadable, base chance {}% kept".format(source, chance))
            return chance
        rate, tier = ability_rate(intelligence, dexterity)
        result = 100.0 if rate is None else min(100.0, chance * rate)
        note("{}: INT={} DEX={} -> {} x{} : {}% -> {}%".format(
            source, intelligence, dexterity, tier, rate, chance, result))
        return result

    def roll(item, source, chance):
        """抽選して、当たればスキルを付ける。"""
        item = unwrap(item)
        if not is_bare_weapon(item) or not first_roll(item):
            return
        count("rolled/" + source)
        try:
            chance = float(chance)
        except (TypeError, ValueError):
            return
        chance = scaled(chance, source)
        if chance <= 0 or random.random() * 100.0 >= chance:
            note("{}: {!r} missed the {}% roll".format(
                source, frames.short(frames.attr(item, "name", ""), 40), chance))
            return
        usefulness = usefulness_of(item)
        if usefulness is None:
            note("{}: usefulness unavailable, skipped".format(source))
            return
        note("{}: {!r} won the {}% roll (value={} rarity={} usefulness={})".format(
            source, frames.short(frames.attr(item, "name", ""), 40), chance,
            frames.attr(item, "value", None), frames.attr(item, "rarity", None),
            usefulness))

        if threading.current_thread() is not threading.main_thread():
            skill = make_and_attach(item, source, usefulness)
            if skill is not None:
                finish(item, source, skill)
            return

        # メインスレッドでは待たない。作り終えたら Clock でメインへ戻して書く。
        def work():
            skill = make_and_attach(item, source, usefulness)
            if skill is None:
                return
            from kivy.clock import Clock
            Clock.schedule_once(lambda dt: finish(item, source, skill), 0)

        threading.Thread(target=work, name="instantale_weapon_skill_chance",
                         daemon=True).start()

    def context():
        return getattr(local, "source", None)

    # ---- 店: 本体の抽選の後で、増えた武器を見る ---------------------------
    @ctx.wrap("__main__:ShoppingStartManagerRemake.generate_item_in_shopping",
              safe=True, required=False)
    def in_shopping(orig, self, *args, **kwargs):
        owner = frames.arg(args, kwargs, "shop_owner_instance", 1)
        before = set(map(id, (items.inventory_of(owner) or {}).values()))
        outer, local.source = context(), "shop"
        try:
            result = orig(self, *args, **kwargs)
        finally:
            local.source = outer
        if CHANCE_SHOP > 0:
            for item in list((items.inventory_of(owner) or {}).values()):
                if id(item) not in before:
                    roll(item, "shop", CHANCE_SHOP)
        return result

    # ---- クラフト: 最中の印 ---------------------------------------------
    @ctx.wrap("__main__:ItemCraftManager.execute", safe=True, required=False)
    def craft_execute(orig, self, *args, **kwargs):
        outer, local.source = context(), "craft"
        try:
            return orig(self, *args, **kwargs)
        finally:
            local.source = outer

    def chance_of(source):
        return {"craft": CHANCE_CRAFT, "other": CHANCE_OTHER, "shop": CHANCE_SHOP}.get(source)

    def source_of(app, obtainer):
        """入口の外で作られた武器の経路。世界の人物（店の主・仲間など）なら shop、ほか（主人公・敵・分からない）は other。

        敵は世界の人物に居ないので other（戦闘の前に作られて、倒すと手に入る品）。
        """
        if obtainer is None or obtainer is frames.attr(app, "player", None):
            return "other"
        characters = frames.attr(frames.attr(app, "world", None), "characters", None)
        if isinstance(characters, dict) and any(c is obtainer for c in characters.values()):
            return "shop"
        return "other"

    # ---- 品が組まれた瞬間（`128_` と同じ所）--------------------------------
    # 生成の入口が品を返すとは限らない（店の入口は None を返して持ち主へ入れる）ので、
    # 入口の最中に組まれた武器をここで控えておき、入口を抜けたところで抽選する。
    # 入口の外で組まれた武器は、直前に `master_ai_item_generate` が名付けたものだけ拾う
    # （セーブからの組み直しも `Item.__init__` を通るので、それ以外は見ない）。
    @ctx.wrap("scripts.items:Item.__init__", safe=True, required=False)
    def item_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if not is_bare_weapon(self):
            return result
        collect = getattr(local, "collect", None)
        if isinstance(collect, list):
            collect.append(self)
            return result
        name = frames.attr(self, "name", None)
        if isinstance(name, str) and store["pending"].pop(name, None) is not None:
            roll(self, "other", CHANCE_OTHER)
        return result

    # ---- 生成の入口（LLM が書いた品を組む1本）-----------------------------
    @ctx.wrap("__main__:InstantaleApp.generate_item_from_item_data", safe=True)
    def generated(orig, self, *args, **kwargs):
        outer, local.collect = getattr(local, "collect", None), []
        try:
            result = orig(self, *args, **kwargs)
        finally:
            created, local.collect = local.collect, outer
        in_shopping = context() == "shop"
        obtainer = frames.arg(args, kwargs, "obtainer", 7)
        source = context() or source_of(self, obtainer)
        weapons = [item for item in created + [unwrap(result)] if is_bare_weapon(item)]
        for item in weapons:             # ここで見たので、後の組み直しで拾い直さない
            store["pending"].pop(frames.attr(item, "name", None), None)
        note("entry: generate_item_from_item_data source={} type={!r} weapons={} result={} for={!r}"
             .format(source, frames.arg(args, kwargs, "item_type", 2), len(weapons),
                     type(result).__name__, frames.short(frames.attr(obtainer, "name", None), 30)))
        if isinstance(outer, list):
            outer.extend(weapons)        # 外側の入口がまとめて見る
        elif not in_shopping:            # 店の入口の中は generate_item_in_shopping の後で見る
            for item in weapons:
                roll(item, source, chance_of(source))
        return result

    # ---- テキストで頼んだ品（マスターAIの品の生成）---------------------------
    @ctx.wrap("scripts.llm.llm_manager:master_ai_item_generate", safe=True,
              required=False)
    def master_item(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        data = llm.as_dict(result) or {}
        category = data.get("item_category")
        kind = category.get("type") if isinstance(category, dict) else None
        name = data.get("item_name")
        note("entry: master_ai_item_generate {!r} type={!r}".format(
            frames.short(name, 40), kind))
        if kind == WEAPON_TYPE and isinstance(name, str) and context() is None:
            pending = store["pending"]
            pending[name] = True
            while len(pending) > 20:     # 組まれずに終わった名前を溜めない
                pending.pop(next(iter(pending)))
        return result

    # ---- クラフトの取りこぼし（入口を通らない版に備える）--------------------
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.place_crafted_item",
              safe=True, required=False)
    def crafted(orig, self, *args, **kwargs):
        roll(frames.arg(args, kwargs, "generated_item", 0), "craft", CHANCE_CRAFT)
        return orig(self, *args, **kwargs)

    # ---- 説明欄: スキル名の下に効果と使用回数 -----------------------------
    # 箱の大きさは `109_` が描いた後の文字で測り直すので、その内側で足す（mod.json の before）。
    if SHOW_EFFECT:
        @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content", safe=True)
        def detail_box(orig, self, *args, **kwargs):
            with ShownEffect(frames.arg(args, kwargs, "item", 0)) as shown:
                result = orig(self, *args, **kwargs)
            shown.tighten(frames.attr(self, "attributes_label", None))
            return result

    # ---- 仲間の武器のスキル（戦闘の間だけ skills へ足す）----------------------
    party = PartySkills(store["lent"])
    if not PARTY_SKILLS and party.lent:  # 設定を切って注入し直したら残りを外す
        party.take_back()

    if PARTY_SKILLS:
        @ctx.wrap(BATTLE_START_TARGET, safe=True, required=False)
        def start_battle(orig, self, *args, **kwargs):
            party.take_back()            # 前の戦闘の残り（終わりを通らなかった戦闘）
            result = orig(self, *args, **kwargs)
            try:
                added, clashed = party.lend(getattr(self, "app", None) or ui.find_app())
                for member, name in added:
                    note("battle: {} can use {!r}".format(member, frames.short(name, 40)))
                for member, name in clashed:
                    note("battle: {} already has a skill named {!r}, kept their own".format(
                        member, frames.short(name, 40)))
            except Exception:
                ctx.log_exc("weapon skill chance: cannot lend weapon skills to the party")
            return result

        def end_phase(orig, self, *args, **kwargs):
            try:
                return orig(self, *args, **kwargs)
            finally:
                if party.lent:
                    note("battle end: took back {} weapon skill(s)".format(party.take_back()))

        for target in BATTLE_END_TARGETS:
            ctx.wrap(target, safe=True, required=False)(end_phase)

        @ctx.wrap(SAVE_TARGET, safe=True, required=False)
        def save_game(orig, self, *args, **kwargs):
            gone = party.hidden()        # セーブの skills に品のスキルを入れない
            try:
                return orig(self, *args, **kwargs)
            finally:
                PartySkills.restore(gone)

        @ctx.wrap(REST_TARGET, safe=True, required=False)
        def rest(orig, self, *args, **kwargs):
            # 戻すのは orig の前（この行動の保存に戻した回数を載せる。`137_` と同じ）
            try:
                for member, name, before, after in refill_worn(
                        getattr(self, "app", None) or ui.find_app()):
                    note("rest: {} {!r} {} -> {}".format(
                        member, frames.short(name, 40), before, after))
            except Exception:
                ctx.log_exc("weapon skill chance: cannot refill the party's weapon skills")
            return orig(self, *args, **kwargs)

    write("installed (craft={}%, other={}%, shop={}%)".format(
        CHANCE_CRAFT, CHANCE_OTHER, CHANCE_SHOP))
    ctx.log("weapon skill chance: installed (craft={}%, other={}%, shop={}%, counts={})"
            .format(CHANCE_CRAFT, CHANCE_OTHER, CHANCE_SHOP, store["counts"]))
