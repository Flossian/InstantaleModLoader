# -*- coding: utf-8 -*-
"""戦闘の効果（会心・吸収・軽減・不屈など）の窓口。TECH.md §3.3.28。

装備の効果（`411_equipment_bonuses`）や主人公の特質（`346_player_trait_effects`）のように、
戦闘の一撃に効く効果を持つ MOD が「この人物の効果」を答え、ローダが戦闘の中で効かせる。
効かせる処理はここに1つだけあるので、答える MOD どうしは互いを入れていなくても働く
（処理の共有のために MOD を依存させない）。

    置く側   battle_effects.declare(owner, fn, on_kill=None)
             # fn(app, holder) -> {"critical": 15, "slayer": {"boss": 30}, …} | None
             # on_kill(app, actor, defender) -> [本文に出す行]（倒したときに呼ぶ。無くてよい）
    読む側   battle_effects.effects_for(app, holder)   # 全員の答えを足した辞書

答えの鍵は `KINDS`、値は % の数（特効だけは `{相手: %}`）。数でない値は無視する。
持ち主ごとの答えは足し、確率と割合は `CAPS` で止める。同じ持ち主の中の足し方（全部足す・最大の1品）は答える側が決める。

## 効かせ方

1手（`handle_battle_situation`）の中で、手番の者の効果を読み、ゲームの計算の入口と出口に足す。
攻める側の効果は味方（主人公と仲間）の手だけで、敵の効果は聞かない。

    会心   `calculate_battle_effect` の前に、敵に当たる裁きを1段上げる
    貫通   `get_instant_damage(素点, 防御)` に渡る敵の防御を減らす
    特効・呪い・成長・連携   `get_instant_damage` の戻り（その一撃のダメージ）に上乗せする（足してから1回掛ける）
    処刑   上乗せの後、敵の残りが割合以下なら敵の体力ぶんに引き上げる
    吸収   敵に当てるたびに、その手で与えたダメージ（敵の残りの体力まで）の割合を戻す
    呪い   当てた手で1回、自分の最大体力の割合を失う（体力 1 で止まる）
    成長   倒すたびに `on_kill` を呼ぶ（数えるのは答えた MOD）
    毒刃   命中した敵の `status` にゲームと同じ形で書き、毎手の減算の前に体力を削る
    怯み   命中した敵の `status` に書き、その敵の一撃を弱める

受ける側の効果は、敵の手で味方が受ける一撃に効く。

    軽業   一撃を無効にする
    軽減・重装・呪縛・火事場   受けるダメージを減らす。足さずに順に掛ける（足すと種類が違うだけで 100% に届く）
    守護   仲間への一撃を、別の者がこの確率で肩代わりする（その者の効果で受け、倒れるなら肩代わりしない）
    不屈   倒れる一撃を体力 1 で耐える（戦闘に1回）
    棘     受けたダメージの割合を、その敵に返す（敵は体力 1 で止まる。手番の中で敵を消さない）
    再生   毎手の減算の前に、最大体力の割合を戻す
    耐性   敵の手の `resolve_battle_effect` で新しく付いた状態異常を外す

重装の器用の低下、呪縛の与えるダメージの低下（ここで引く）、威光の買値は、品の効果の代償・付録。
器用と買値は答えた MOD が能力値と値段の窓口に置く。

ダメージの経路は GAME.md §2.10.2。敵の防御は `get_npc_defense` で聞かれた直後に
`get_instant_damage` へ渡るので、直前に聞かれた敵を受け側とする（`319_` と同じ見分け方）。
味方の受け側は、防御の値で分からないとき（主人公は防具の値で来る）、
`calculate_battle_effect` の戻り（対象 → 素点）を素点で照らす。これも `319_` と同じ。
`319_` は `get_instant_damage` の戻りを自分の式に差し替える。この窓口はローダが MOD の層より後
（能力値の窓口の内側）に当てるので、差し替えた後のダメージに足す。

体力を動かす効果は、1手の報告（`308_`）より先に動かし、`ui.note_damage` で出どころを控える。行は 308 が出す。
行のログは `out/battle_effects.log`。

切った MOD の答えは、`boot()` が外す（`durations.forget` の片付け）。置き場は `sys` の属性で、注入し直しをまたいで残る。
"""
import random
import sys
import threading

from . import durations, frames, log, log_exc, ui

# ---------------------------------------------------------------------------
# 種類と決まり
# ---------------------------------------------------------------------------
#: 攻める側の効果（手番の者の効果）。
WEAPON_KINDS = ("slayer", "critical", "lifesteal", "pierce", "venom", "daunt",
                "curse", "growth", "execute", "teamwork")
#: 受ける側の効果（`presence` は値段の効果で、戦闘では使わない）。
ARMOR_KINDS = ("endure", "thorns", "regen", "heavy", "guardian",
               "resist", "evade", "desperate", "binding", "presence")
#: 品に付かない種類（特質など）。`reduce` は代償の無い軽減。
EXTRA_KINDS = ("reduce",)
KINDS = WEAPON_KINDS + ARMOR_KINDS + EXTRA_KINDS

#: 特効の相手。どれも戦闘の画面で見分けられるもの（敵の段と型は Character に残らない）。
#:   boss      … ボス戦（`in_boss_battle`）
#:   higher    … 自分よりレベルが上の敵
#:   wounded   … 体力が半分以下の敵
#:   unhurt    … 体力が満タンの敵
SLAYER_TARGETS = ("boss", "higher", "wounded", "unhurt")

LABELS = {
    "ja": {"slayer": "特効", "critical": "会心", "lifesteal": "吸収",
           "pierce": "貫通", "venom": "毒", "daunt": "怯み",
           "curse": "呪い", "growth": "成長", "execute": "処刑", "teamwork": "連携",
           "endure": "不屈", "thorns": "棘", "regen": "再生", "heavy": "重装", "guardian": "守護",
           "resist": "耐性", "evade": "軽業", "desperate": "火事場", "binding": "呪縛", "presence": "威光",
           "reduce": "軽減"},
    "en": {"slayer": "Slayer", "critical": "Critical", "lifesteal": "Lifesteal",
           "pierce": "Pierce", "venom": "Venom", "daunt": "Daunt",
           "curse": "Cursed", "growth": "Growth", "execute": "Execute", "teamwork": "Teamwork",
           "endure": "Endure", "thorns": "Thorns", "regen": "Regen", "heavy": "Heavy", "guardian": "Guardian",
           "resist": "Resist", "evade": "Nimble", "desperate": "Last Stand", "binding": "Binding",
           "presence": "Presence", "reduce": "Ward"},
}

TARGET_LABELS = {
    "ja": {"boss": "ボス戦", "higher": "格上", "wounded": "手負い", "unhurt": "無傷"},
    "en": {"boss": "boss fights", "higher": "stronger foes",
           "wounded": "wounded foes", "unhurt": "unhurt foes"},
}

#: 命中時に付く効果の値の前置き。
ON_HIT = {"ja": "命中時 ", "en": "on hit "}

#: 足した後の上限（%）。確率と割合は 100 を超えても意味が無い。軽減は 90 で止める（全部を消さない）。
CAPS = {"critical": 100, "lifesteal": 100, "pierce": 100, "venom": 100, "daunt": 100,
        "execute": 100, "endure": 100, "thorns": 100, "regen": 100, "heavy": 100, "guardian": 100,
        "resist": 100, "evade": 100, "desperate": 100, "binding": 100, "presence": 100, "reduce": 90}

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
#: 重装: 下がる器用（答えた MOD が能力値の窓口に置く）。
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

#: 窓口専用の乱数（ゲームの乱数の並びを動かさない）。
RNG = random.Random()

#: 1手の帳簿の名前（ゲームの値）。
ALLY_SIDE = "味方陣営"
ENEMY_SIDE = "敵側"

#: 毒刃・怯みの帳簿の上限（戦闘の始めに空にする。取りこぼしても溜めない）。
MARK_LIMIT = 200

#: 行のログの名前。
LOG_BASENAME = "battle_effects.log"

_REGISTRY_ATTR = "_instantale_battle_effects"


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def language():
    """ゲームの表示言語。`scripts.languages.language`。読めなければ ja。"""
    module = sys.modules.get("scripts.languages")
    lang = getattr(module, "language", None) if module is not None else None
    return "en" if lang == "en" else "ja"


def text_of(kind, amount, target, lang):
    """効果の値の表記。`ボス戦 +30%`・`15%`・`命中時 30%`。名前（`LABELS`）は含まない。"""
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
    if kind == "reduce":
        return ("受けるダメージ -{}%" if ja else "-{}% damage taken").format(amount)
    return "{}%".format(amount)


# ---------------------------------------------------------------------------
# 戦闘の数（ゲームに触らない）
# ---------------------------------------------------------------------------
def growth_percent(rate, kills):
    """成長の上乗せ（%）。"""
    return min(GROWTH_CAP, rate * (kills // GROWTH_STEP))


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


# ---------------------------------------------------------------------------
# 置く側
# ---------------------------------------------------------------------------
def _registry():
    """`{持ち主: {"fn", "on_kill"}}`（置いた順）。`sys` に置いて注入し直しをまたぐ。"""
    found = getattr(sys, _REGISTRY_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, _REGISTRY_ATTR, found)
    return found


def declare(owner, fn, on_kill=None, write=None):
    """人物の戦闘の効果を答える関数を置く。持ち主ごとに1つで、置き直すと差し替わる。

    `fn(app, holder)` は `{種類: %}`（特効は `{"slayer": {相手: %}}`）か、効果が無ければ None を返す。
    戦闘の計算の中から毎回呼ばれるので、重い処理を置かない。
    `on_kill(app, actor, defender)` は、`actor` の手で敵を倒したときに呼ぶ（成長の討伐数など）。
    本文に出す行の並びを返してよい。
    """
    if not callable(fn):
        raise TypeError("declare() needs a callable, got {!r}".format(type(fn)))
    if on_kill is not None and not callable(on_kill):
        raise TypeError("on_kill must be callable, got {!r}".format(type(on_kill)))
    _registry()[str(owner or "")] = {"fn": fn, "on_kill": on_kill}
    if write:
        write("battle effects: {!r} answers the battle effects".format(owner))


def forget(owner, write=None):
    """その持ち主の `declare` を外す。外した種類を返す（`durations.forget` の片付け）。"""
    if _registry().pop(str(owner), None) is None:
        return []
    if write:
        write("battle effects: {!r} no longer answers the battle effects".format(owner))
    return ["battle_effects"]


# 片付けの入口は `durations.forget` の1本に保つ（`combat` / `abilities` と同じ）。
durations.on_forget(forget)


def owners():
    return list(_registry())


_failed = set()


def _answer(owner, entry, app, holder):
    try:
        found = entry["fn"](app, holder)
    except Exception:
        if owner not in _failed:
            _failed.add(owner)
            log_exc("battle effects: the answer of {!r} failed (logged once)".format(owner))
        return None
    return found if isinstance(found, dict) else None


def merge(answers):
    """答えの並びを足して1つの辞書にする（特効は相手ごとに足す）。確率と割合は `CAPS` で止める。"""
    total = {}
    for found in answers:
        for kind, value in (found or {}).items():
            if kind == "slayer" and isinstance(value, dict):
                table = total.setdefault("slayer", {})
                for target, percent in value.items():
                    if target in SLAYER_TARGETS and _num(percent) and percent > 0:
                        table[target] = table.get(target, 0) + percent
                continue
            if kind in KINDS and kind != "slayer" and _num(value) and value > 0:
                total[kind] = total.get(kind, 0) + value
    for kind, cap in CAPS.items():
        if kind in total:
            total[kind] = min(total[kind], cap)
    if not total.get("slayer"):
        total.pop("slayer", None)
    return total


def effects_for(app, holder):
    """`holder` の戦闘の効果（全員の答えを足したもの）。誰も答えなければ空。"""
    if holder is None:
        return {}
    return merge([_answer(owner, entry, app, holder) for owner, entry in list(_registry().items())])


def _on_kill(app, actor, defender):
    lines = []
    for owner, entry in list(_registry().items()):
        hook = entry.get("on_kill")
        if hook is None:
            continue
        try:
            found = hook(app, actor, defender)
        except Exception:
            log_exc("battle effects: on_kill of {!r} failed".format(owner))
            continue
        if isinstance(found, (list, tuple)):
            lines.extend(str(line) for line in found if line)
    return lines


# ---------------------------------------------------------------------------
# 効かせる（ローダが1つの世代に1回 `install` する）
# ---------------------------------------------------------------------------
_state = {"action": None, "last_defense": None, "marks": {}, "last_hit": None, "endured": set(),
          "write": None}


def _write(text):
    writer = _state.get("write")
    try:
        (writer or log)(text)
    except Exception:
        pass


def _dry_run():
    """計測 MOD の試し打ちの最中か（GAME.md §2.10.4 の印）。"""
    return getattr(sys, "_instantale_probe_dry_run_thread", None) == threading.get_ident()


def _say(app, text):
    try:
        app.add_text(text)
    except Exception:
        log_exc("battle effects: add_text failed")


def _enemies_of(app):
    found = frames.attr(app, "current_enemy_dict", None)
    return found if isinstance(found, dict) else {}


def _is_enemy(app, holder):
    return any(found is holder for found in _enemies_of(app).values())


def _enemy_names(app):
    names = set()
    for key, holder in _enemies_of(app).items():
        names.add(str(key))
        name = frames.attr(holder, "name", None)
        if name:
            names.add(str(name))
    return names


def _actor_of(app, character_key, character_side):
    """1手の主。敵は `current_enemy_dict` の鍵、味方は表示名で引く（`319_` と同じ）。"""
    if character_side == ENEMY_SIDE:
        return _enemies_of(app).get(character_key)
    player = frames.attr(app, "player", None)
    if player is not None and str(frames.attr(player, "name", "")) == str(character_key):
        return player
    for member_id in ui.party_member_ids(app):
        member = ui.character_of(app, member_id)
        if member is not None and str(frames.attr(member, "name", "")) == str(character_key):
            return member
    return None


def _allies_of(app):
    """戦闘に出ている味方（逃げて一覧から外れた主人公は入らない）。"""
    found = []
    player = frames.attr(app, "player", None)
    party = frames.attr(app, "party", None)
    if player is not None and (not isinstance(party, dict) or "player" in party):
        found.append(player)
    for member_id in ui.party_member_ids(app):
        member = ui.character_of(app, member_id)
        if member is not None:
            found.append(member)
    return found


def _name_of(holder, fallback="?"):
    name = frames.attr(holder, "name", None)
    return str(name) if name else str(fallback)


def _field(holder, name):
    """`battle_action` とその中身は dict（変換後の形。GAME.md §2.10.2）。属性でも読む。"""
    return holder.get(name) if isinstance(holder, dict) else frames.attr(holder, name, None)


def _hp_of(holder):
    return _num(frames.attr(holder, "current_hp", None)), _num(frames.attr(holder, "max_hp", None))


def _label(kind):
    return LABELS[language()][kind]


def _lose_hp(holder, amount, kind):
    """`kind` の出どころで体力を減らす。体力 1 で止まり、これでは倒れない。減らした量を返す。"""
    hp = _num(frames.attr(holder, "current_hp", None))
    if hp is None or hp <= 1 or not amount:
        return 0
    new_hp = int(max(1, hp - amount))
    ui.note_damage(_name_of(holder), hp - new_hp, _label(kind))
    holder.current_hp = new_hp
    return hp - new_hp


def _gain_hp(holder, amount, kind):
    """`kind` の出どころで体力を戻す（最大まで。倒れている者は戻さない）。戻した量を返す。"""
    hp, top = _hp_of(holder)
    if hp is None or hp <= 0 or not amount:
        return 0
    new_hp = int(min(top, hp + amount)) if top else int(hp + amount)
    if new_hp <= hp:
        return 0
    ui.note_damage(_name_of(holder), new_hp - hp, _label(kind))
    holder.current_hp = new_hp
    return new_hp - hp


# ------------------------------------------------------------ 毒刃・怯み
def _status_name(kind):
    return STATUS_NAMES[language()][kind]


def _marked(holder, kind):
    """`holder` に、この窓口が付けた `kind` がまだ乗っているか。"""
    key = (id(holder), kind)
    if _state["marks"].get(key) is not holder:
        return False
    statuses = frames.attr(holder, "status", None)
    if isinstance(statuses, dict) and _status_name(kind) in statuses:
        return True
    del _state["marks"][key]           # ゲームが期限で消した
    return False


def _mark(holder, kind, turns):
    """ゲーム自身の入れ物（`Character.status`）へ同じ形で書く。期限の減算と削除はゲームに任せる。"""
    statuses = frames.attr(holder, "status", None)
    if not isinstance(statuses, dict):
        return False
    lang = language()
    name = STATUS_NAMES[lang][kind]
    statuses[name] = {"status_name": name,
                      "description": STATUS_TEXTS[lang][kind],
                      "duration": int(turns)}
    if len(_state["marks"]) >= MARK_LIMIT:
        _state["marks"].clear()
    _state["marks"][(id(holder), kind)] = holder
    return True


# ------------------------------------------------------------ 1手
def _open_action(app, character_key, character_side):
    side = ENEMY_SIDE if character_side == ENEMY_SIDE else ALLY_SIDE
    actor = _actor_of(app, character_key, character_side)
    found = effects_for(app, actor) if side == ALLY_SIDE and actor is not None else {}
    _state["action"] = {"app": app, "side": side, "actor": actor,
                        "name": _name_of(actor, character_key), "effects": found,
                        "notes": [], "dealt": 0, "drained": 0, "slayer": [],
                        "plan": {},       # 対象 → 素点（calculate_battle_effect の戻り）
                        "foes": [],       # この手で当てた敵
                        "teamwork": False, "cursed": False}
    _state["last_defense"] = None


def _close_action():
    action, _state["action"] = _state["action"], None
    _state["last_defense"] = None
    if action is None:
        return
    lang = language()
    if action["slayer"]:
        labels = [TARGET_LABELS[lang][t] for t in action["slayer"]]
        action["notes"].append(("（特効: {}）" if lang == "ja" else "(Slayer: {})")
                               .format("・".join(labels) if lang == "ja" else ", ".join(labels)))
    if action["teamwork"]:
        action["notes"].append("（連携）" if lang == "ja" else "(Teamwork)")
    if action["foes"]:
        _state["last_hit"] = (action["actor"], action["foes"])
    if action["notes"]:
        _say(action["app"], "\n".join(action["notes"]))


def _drain(action):
    """吸収の体力を戻す。戻す量は1手の累計から決める（小さな当たりが続いても端数を捨てない）。"""
    owed = drained(action["dealt"], action["effects"].get("lifesteal")) - action["drained"]
    if action["actor"] is None or owed <= 0:
        return
    action["drained"] += owed
    gained = _gain_hp(action["actor"], owed, "lifesteal")
    if gained:
        _write("lifesteal: {} dealt {} -> +{}".format(action["name"], action["dealt"], gained))


def _pay_curse(action):
    """呪いの代償。当てた手で1回だけ。"""
    if action["cursed"] or not action["effects"].get("curse") or action["actor"] is None:
        return
    action["cursed"] = True
    cost = share_of_max(_num(frames.attr(action["actor"], "max_hp", None)), CURSE_COST)
    lost = _lose_hp(action["actor"], cost, "curse")
    if lost:
        _write("curse: {} loses {}".format(action["name"], lost))


def _count_kill(action, defender):
    """倒したことを答えた MOD に知らせる（成長の討伐数など）。返った行を本文に出す。"""
    action["notes"].extend(_on_kill(action["app"], action["actor"], defender))


def _guarded(fn, *args):
    try:
        fn(*args)
    except Exception:
        log_exc("battle effects: {} failed".format(getattr(fn, "__name__", "effect")))


def _handle_battle_situation(orig, self, *args, **kwargs):
    if _dry_run():
        return orig(self, *args, **kwargs)
    try:
        _open_action(getattr(self, "app", None) or ui.find_app(),
                     frames.arg(args, kwargs, "character_key", 0),
                     frames.arg(args, kwargs, "character_side", 1))
    except Exception:
        _state["action"] = None
        log_exc("battle effects: cannot open the action")
    try:
        return orig(self, *args, **kwargs)
    finally:
        try:
            _close_action()
        except Exception:
            log_exc("battle effects: cannot close the action")


# ------------------------------------------------------------ 会心・素点の控え
def _calculate_battle_effect(orig, self, *args, **kwargs):
    action = _state["action"] if not _dry_run() else None
    try:
        chance = action["effects"].get("critical") if action else None
        if chance and RNG.random() * 100 < chance:
            battle_action = frames.arg(args, kwargs, "battle_action", 0)
            names = _enemy_names(action["app"])
            raised = []
            for entry in _field(battle_action, "instant_damage") or []:
                target, power = _field(entry, "target"), _field(entry, "power")
                higher = raise_power(power)
                if str(target) not in names or higher is None:
                    continue
                if isinstance(entry, dict):
                    entry["power"] = higher
                else:
                    setattr(entry, "power", higher)
                raised.append("{} {}->{}".format(target, power, higher))
            if raised:
                action["notes"].append(("（{}の会心の一撃）" if language() == "ja"
                                        else "({} lands a critical hit)").format(action["name"]))
                _write("critical: {} ({}%) {}".format(action["name"], chance, "; ".join(raised)))
    except Exception:
        log_exc("battle effects: critical failed")
    result = orig(self, *args, **kwargs)
    if action is not None and isinstance(result, (list, tuple)) and result and isinstance(result[0], dict):
        action["plan"] = {str(target): raw for target, raw in result[0].items()
                          if _num(raw) is not None}
    return result


# ------------------------------------------------------------ 受け側の見分け
def _get_npc_defense(orig, self, *args, **kwargs):
    result = orig(self, *args, **kwargs)
    if _state["action"] is not None:
        _state["last_defense"] = (self, result)
    return result


def _enemy_hit_by(action, defense):
    """味方の手で受ける敵。分からなければ None。"""
    last = _state["last_defense"]
    if last is not None and last[1] == defense and _is_enemy(action["app"], last[0]):
        return last[0]
    return None


def _ally_hit_by(action, attack, defense):
    """敵の手で受ける味方。分からなければ None。"""
    plan, app = action["plan"], action["app"]
    target = next((name for name, raw in plan.items() if raw == attack), None)
    if target is not None:
        del plan[target]
    last = _state["last_defense"]
    if last is not None and last[1] == defense and any(last[0] is ally for ally in _allies_of(app)):
        return last[0]
    return _actor_of(app, target, ALLY_SIDE) if target is not None else None


def _get_instant_damage(orig, *args, **kwargs):
    action = _state["action"]
    if action is None or _dry_run():
        return orig(*args, **kwargs)
    try:
        attack = frames.arg(args, kwargs, "attack", 0)
        defense = frames.arg(args, kwargs, "defense", 1)
        if action["side"] == ENEMY_SIDE:
            ally = _ally_hit_by(action, attack, defense)
        else:
            foe = _enemy_hit_by(action, defense)
    except Exception:
        log_exc("battle effects: cannot read the hit")
        return orig(*args, **kwargs)
    if action["side"] == ENEMY_SIDE:
        return _enemy_turn_hit(action, ally, orig(*args, **kwargs))
    if foe is None:
        return orig(*args, **kwargs)
    return _ally_turn_hit(action, foe, defense, orig, args, kwargs)


# ------------------------------------------------------------ 味方の手
def _ally_turn_hit(action, defender, defense, orig, args, kwargs):
    found = action["effects"]
    hp, top = _hp_of(defender)
    pierce = found.get("pierce")
    if pierce and _num(defense) is not None:
        # 来た側（位置かキーワード）のまま差し替える（TECH.md の規則の表「包みは受けた引数をそのまま」）
        args, kwargs, _ = frames.replace_arg(args, kwargs, "defense", 1, pierced(defense, pierce))
    result = orig(*args, **kwargs)
    try:
        app = action["app"]
        dealt = result
        percent, matched = slayer_bonus(
            found.get("slayer"), bool(frames.attr(app, "in_boss_battle", False)),
            _num(frames.attr(action["actor"], "experience_level", None)),
            _num(frames.attr(defender, "experience_level", None)), hp, top)
        percent += found.get("curse", 0) + found.get("growth", 0)
        if found.get("binding"):
            percent -= BINDING_COST
        last = _state["last_hit"]
        teamed = found.get("teamwork") and last is not None and last[0] is not action["actor"] \
            and any(foe is defender for foe in last[1])
        if teamed:
            percent += found["teamwork"]
        if percent and _num(result):
            dealt = scaled(result, percent)
            for target in matched:
                if target not in action["slayer"]:
                    action["slayer"].append(target)
            action["teamwork"] = action["teamwork"] or bool(teamed)
        executed = _num(dealt) and dealt > 0 and should_execute(hp, dealt, top, found.get("execute"))
        if executed:
            dealt = hp
            action["notes"].append(("（{}を処刑した）" if language() == "ja"
                                    else "({} is executed)").format(_name_of(defender)))
        if _num(dealt) and dealt > 0:
            action["foes"].append(defender)
            action["dealt"] += min(dealt, hp) if hp is not None and hp > 0 else dealt
            _guarded(_drain, action)
            _guarded(_pay_curse, action)
            if hp is not None and 0 < hp <= dealt:
                _guarded(_count_kill, action, defender)
            for kind, turns in (("venom", VENOM_TURNS), ("daunt", DAUNT_TURNS)):
                chance = found.get(kind)
                if chance and RNG.random() * 100 < chance and _mark(defender, kind, turns):
                    _write("{}: {} -> {} ({}%)".format(kind, action["name"], _name_of(defender), chance))
        if found:
            _write("hit: {} -> {} def={}{} dmg={} -> {}{}{}".format(
                action["name"], _name_of(defender), defense,
                " pierced to {:g}".format(pierced(defense, pierce)) if pierce and _num(defense) is not None else "",
                result, dealt, " +{}% {}".format(percent, matched) if percent else "",
                " executed" if executed else ""))
        return dealt
    except Exception:
        log_exc("battle effects: the attacking effects failed; damage left as is")
        return result


# ------------------------------------------------------------ 敵の手（怯み・受ける側）
def _enemy_turn_hit(action, ally, result):
    dealt = _weakened(action, result)
    if ally is None or not _num(dealt) or dealt <= 0:
        return dealt
    try:
        return _defend(action, ally, dealt)
    except Exception:
        log_exc("battle effects: the defending effects failed; damage left as is")
        return dealt


def _defend(action, ally, dealt):
    """受ける側の効果を通した、ゲームが `ally` に当てるダメージ。"""
    app = action["app"]
    worn = effects_for(app, ally)
    chance = worn.get("evade")
    if chance and RNG.random() * 100 < chance:
        action["notes"].append(("（{}は身軽にかわした）" if language() == "ja"
                                else "({} dodges the blow)").format(_name_of(ally)))
        _write("evade: {} dodges {}".format(_name_of(ally), dealt))
        return 0 if isinstance(dealt, int) else 0.0
    guard = _guard_for(app, ally, dealt)
    if guard is not None:
        amount = _reduced(app, guard, dealt)
        lost = _lose_hp(guard, amount, "guardian")
        action["notes"].append(("（{}が{}をかばった）" if language() == "ja"
                                else "({} shields {})").format(_name_of(guard), _name_of(ally)))
        _write("guardian: {} takes {} for {}".format(_name_of(guard), lost, _name_of(ally)))
        _give_thorns(action, effects_for(app, guard), amount)
        return 0 if isinstance(dealt, int) else 0.0
    amount = _reduced(app, ally, dealt)
    hp = _num(frames.attr(ally, "current_hp", None))
    chance = worn.get("endure")
    if chance and hp is not None and 0 < hp <= amount and id(ally) not in _state["endured"] \
            and RNG.random() * 100 < chance:
        _state["endured"].add(id(ally))
        amount = max(0, hp - 1)
        action["notes"].append(("（{}は不屈で踏みとどまった）" if language() == "ja"
                                else "({} endures the blow)").format(_name_of(ally)))
        _write("endure: {} {} -> {} (hp {})".format(_name_of(ally), dealt, amount, hp))
    _give_thorns(action, worn, amount)
    return amount


def _reduced(app, holder, dealt, quiet=False):
    """軽減・重装・呪縛・火事場の後のダメージ。"""
    worn = effects_for(app, holder)
    hp, top = _hp_of(holder)
    cuts = [worn.get("reduce"), worn.get("heavy"), worn.get("binding"),
            worn.get("desperate") if is_desperate(hp, top) else None]
    amount = after_cuts(dealt, cuts)
    if amount != dealt and not quiet:
        _write("cut: {} {} -> {} {}".format(_name_of(holder), dealt, amount, [c for c in cuts if c]))
    return amount


def _guard_for(app, ally, dealt):
    """`ally` への一撃を肩代わりする者。受けても倒れない者だけ。"""
    for other in _allies_of(app):
        if other is ally:
            continue
        chance = effects_for(app, other).get("guardian")
        hp = _num(frames.attr(other, "current_hp", None))
        if chance and hp is not None and hp > _reduced(app, other, dealt, quiet=True) \
                and RNG.random() * 100 < chance:
            return other
    return None


def _give_thorns(action, worn, taken):
    """棘。受けた量の割合を手番の敵へ返す。"""
    rate = worn.get("thorns")
    enemy = action["actor"]
    if not rate or enemy is None or not _num(taken) or taken <= 0:
        return
    lost = _lose_hp(enemy, max(1, int(round(taken * rate / 100.0))), "thorns")
    if lost:
        _write("thorns: {} loses {}".format(_name_of(enemy), lost))


def _weakened(action, result):
    """怯んだ敵の一撃を弱める。"""
    actor = action["actor"]
    if actor is None or not _num(result) or not _marked(actor, "daunt"):
        return result
    lowered = scaled(result, -DAUNT_CUT)
    _write("daunt: {} {} -> {}".format(action["name"], result, lowered))
    return lowered


# ------------------------------------------------------------ 耐性
def _statuses_of(holder):
    found = frames.attr(holder, "status", None)
    return found if isinstance(found, dict) else None


def _resolve_battle_effect(orig, self, *args, **kwargs):
    action = _state["action"]
    if action is None or action["side"] != ENEMY_SIDE or _dry_run():
        return orig(self, *args, **kwargs)
    watched = []
    try:
        for ally in _allies_of(action["app"]):
            chance = effects_for(action["app"], ally).get("resist")
            current = _statuses_of(ally)
            if chance and current is not None:
                watched.append((ally, chance, set(current)))
    except Exception:
        log_exc("battle effects: cannot read the statuses")
    result = orig(self, *args, **kwargs)
    try:
        for ally, chance, before in watched:
            current = _statuses_of(ally) or {}
            for name in [key for key in current if key not in before]:
                if RNG.random() * 100 < chance:
                    del current[name]
                    action["notes"].append(("（{}は{}を防いだ）" if language() == "ja"
                                            else "({} resists {})").format(_name_of(ally), name))
                    _write("resist: {} blocks {}".format(_name_of(ally), name))
    except Exception:
        log_exc("battle effects: resist failed")
    return result


# ------------------------------------------------------------ 毎手の減算の前（毒刃・再生）
def _reduce_status_turns_and_log(orig, self, *args, **kwargs):
    # ゲームの減算より先に動かす（`319_` の継続ダメージと同じ置き方。期限0で消える前の手も削る）
    try:
        character = frames.arg(args, kwargs, "character", 0)
        if character is not None and _marked(character, "venom"):
            hp = _num(frames.attr(character, "current_hp", None))
            amount = venom_tick(_num(frames.attr(character, "max_hp", None)))
            if hp is not None and hp > 1 and amount:
                new_hp = int(max(1, hp - amount))      # 毒刃では倒れない
                try:
                    ui.note_damage(_name_of(character), hp - new_hp, _status_name("venom"))
                except Exception:
                    pass
                character.current_hp = new_hp
                _write("venom tick: {} {} -> {}".format(_name_of(character), hp, new_hp))
        if character is not None and not _dry_run():
            app = getattr(self, "app", None) or ui.find_app()
            rate = effects_for(app, character).get("regen")
            top = _num(frames.attr(character, "max_hp", None))
            gained = _gain_hp(character, share_of_max(top, rate), "regen") if rate else 0
            if gained:
                _write("regen: {} +{}".format(_name_of(character), gained))
    except Exception:
        log_exc("battle effects: status tick failed")
    return orig(self, *args, **kwargs)


def _start_battle(orig, self, *args, **kwargs):
    _state["marks"].clear()
    _state["endured"].clear()
    _state["action"] = None
    _state["last_defense"] = None
    _state["last_hit"] = None
    return orig(self, *args, **kwargs)


#: 包む対象と包み。
TARGETS = (
    ("__main__:BattlePhaseManager.handle_battle_situation", _handle_battle_situation),
    ("__main__:BattlePhaseManager.calculate_battle_effect", _calculate_battle_effect),
    ("scripts.characters:Character.get_npc_defense", _get_npc_defense),
    ("scripts.functions:get_instant_damage", _get_instant_damage),
    ("__main__:BattlePhaseManager.resolve_battle_effect", _resolve_battle_effect),
    ("__main__:BattlePhaseManager.reduce_status_turns_and_log", _reduce_status_turns_and_log),
    ("__main__:BattleStartManager.start_battle", _start_battle),
)


def install(ctx):
    """1つの世代に1回。`boot()` が MOD の適用の後、能力値の窓口の前に呼ぶ（MOD の層の外、能力値の内）。

    答える MOD が居なくても包む（どの手でも、効果が空なら何もしない）。
    """
    _state.update({"action": None, "last_defense": None, "last_hit": None})
    _state["marks"] = {}
    _state["endured"] = set()
    logger = getattr(ctx, "logger", None)
    try:
        _state["write"] = logger(LOG_BASENAME) if callable(logger) else None
    except Exception:
        _state["write"] = None
    for target, hook in TARGETS:
        ctx.wrap(target, required=False, safe=True)(hook)
    log("battle effects: {} answer(s) ({})".format(len(_registry()), ", ".join(owners()) or "none"))
    return len(TARGETS)
