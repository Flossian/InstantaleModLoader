# -*- coding: utf-8 -*-
"""戦闘の数（人物ごとの装備の攻撃力・防御力）の窓口。

装備を持つ MOD（`912_equipment_slots`）が「この人物の装備は攻撃力いくつ・防御力いくつ」を
答える関数を置き、戦闘を組む MOD（`319_battle_tactics`）が聞く。
MOD どうしは import しない（TECH.md §3.2.3）ので、両者はここで繋がる。

    置く側   combat.declare(combat.ATTACK, fn, owner="912_…")   # fn(app, holder) -> 数 | None
    聞く側   value = combat.attack(app, holder)                 # 数 | None

答えは**装備の側の値だけ**（武器の攻撃力・防具の防御力。合算するならその結果）。
本人の能力や体力と組み合わせて 1 発の数にするのは聞く側の仕事。
`None` は「誰も置いていない」か「その人物は何も装備していない」で、聞く側はゲームのままにする。

素のゲームでは、プレイヤーの武器・防具だけが数に入る（GAME.md §2.10.2）。仲間の装備を
数に入れるのは MOD の判断（912 DOC.md §3.4）で、公式が NPC に武器を参照させない理由は
審判 LLM の文脈の肥大であり、数の側の理由ではない。

同じ種類を 2 本の MOD が置いたら後から置いたほうが勝ち、その旨をログに残す（`durations` と同じ）。
置き場は `sys` の属性で、注入し直しをまたいで残る。
"""
import sys

from . import log_exc

#: 装備の攻撃力。答えは float。
ATTACK = "attack"
#: 装備の防御力。答えは float。
DEFENSE = "defense"

_ATTR = "_instantale_combat"


def _registry():
    """`{種類: (持ち主, 関数)}`。`sys` に置いて注入し直しをまたぐ。"""
    found = getattr(sys, _ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, _ATTR, found)
    return found


def declare(kind, fn, owner="", write=None):
    """その種類を答える関数を置く。置き換えたら前の持ち主を返す。

    `fn(app, holder)` は float か、**装備が無ければ None** を返す。
    """
    if not callable(fn):
        raise TypeError("declare() needs a callable, got {!r}".format(type(fn)))
    registry = _registry()
    previous = registry.get(str(kind))
    registry[str(kind)] = (str(owner or ""), fn)
    before = previous[0] if previous else None
    if write and before and before != str(owner or ""):
        write("combat: {!r} is now decided by {!r} (was {!r})".format(kind, owner, before))
    return before


def forget(owner, write=None):
    """その持ち主が置いたものを全部外す（MOD を外したとき）。外した種類を返す。"""
    registry = _registry()
    gone = [kind for kind, (who, _fn) in registry.items() if who == str(owner)]
    for kind in gone:
        registry.pop(kind, None)
    if write and gone:
        write("combat: {!r} no longer decides {}".format(owner, gone))
    return gone


def source_of(kind):
    """その種類を決めている MOD の名前。誰も置いていなければ `""`。"""
    entry = _registry().get(str(kind))
    return entry[0] if entry else ""


def _ask(kind, app, holder):
    entry = _registry().get(str(kind))
    if entry is None or holder is None:
        return None
    owner, fn = entry
    try:
        value = fn(app, holder)
    except Exception:
        log_exc("combat: {!r} by {!r} failed; treated as no equipment".format(kind, owner))
        return None
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def attack(app, holder):
    """この人物の装備の攻撃力。誰も置いていない／装備が無ければ None。"""
    return _ask(ATTACK, app, holder)


def defense(app, holder):
    """この人物の装備の防御力。誰も置いていない／装備が無ければ None。"""
    return _ask(DEFENSE, app, holder)
