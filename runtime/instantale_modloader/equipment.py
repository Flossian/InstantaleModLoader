# -*- coding: utf-8 -*-
"""装備欄の窓口。

装備欄を持つ MOD（`333_equipment_slots`）が答える関数を置き、仲間の装備を扱う MOD
（`402_` の受け渡し、`401_` の審判への文、`407_` の初期装備、`408_` の見た目）が聞く。
MOD どうしは import しない（TECH.md §3.2.3）ので、両者はここで繋がる。

    置く側   equipment.declare(equipment.GEAR, fn, owner="333_…")   # fn(app, holder) -> [(部位, 品)] | None
    聞く側   worn = equipment.gear(app, holder)                     # [(部位, 品)] | None

答える MOD が居なければ None を返し、聞く側は素の読み方（`equipments` の weapon / wearable）に戻るか、
自分で書く。戦闘の数（装備の攻撃力・防御力）はここではなく `combat` の窓口が持つ。

本体は仲間の `equipments` を保存しない（GAME.md §2.13.3）。ロードをまたいで仲間の装備を持つのは
装備欄の MOD の控えだけなので、仲間に装備させたい MOD は `equip` を通す。

同じ種類を 2 本の MOD が置いたら後から置いたほうが勝ち、その旨をログに残す（`combat` と同じ）。
置き場は `sys` の属性で、注入し直しをまたいで残る。
"""
import sys

from . import log_exc

#: 仲間の品を、開いている受け渡しの窓の上で装備欄へ入れる／戻す。
#: `fn(app, holder, item)` → "equipped" / "unequipped" / 断りの文字列。
#: 窓が無い・装備欄が無ければ None（聞く側が自分で `equipments` を書く）。
TOGGLE = "toggle"
#: 仲間の品を、窓を開かずに装備欄へ入れる。`fn(app, holder, item)` → "equipped" / 断りの文字列、
#: 装備欄が無ければ None。入れた品は装備欄の MOD の控えに載り、ロードをまたいで残る。
EQUIP = "equip"
#: 仲間の品が装備欄に居るか。`fn(app, holder, item)` → bool、装備欄が無ければ None。
EQUIPPED = "equipped"
#: 身に着けている品の一覧。`fn(app, holder)` → `[(部位, 品), ...]`（部位は装備欄の MOD の名前、並びが優先順）。
#: 装備欄を使っていなければ None（聞く側は `equipments` の weapon / wearable を読む）。主人公にも答える。
GEAR = "gear"

_ATTR = "_instantale_equipment"


def _registry():
    """`{種類: (持ち主, 関数)}`。`sys` に置いて注入し直しをまたぐ。"""
    found = getattr(sys, _ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, _ATTR, found)
    return found


def declare(kind, fn, owner="", write=None):
    """その種類を答える関数を置く。置き換えたら前の持ち主を返す。"""
    if not callable(fn):
        raise TypeError("declare() needs a callable, got {!r}".format(type(fn)))
    registry = _registry()
    previous = registry.get(str(kind))
    registry[str(kind)] = (str(owner or ""), fn)
    before = previous[0] if previous else None
    if write and before and before != str(owner or ""):
        write("equipment: {!r} is now decided by {!r} (was {!r})".format(kind, owner, before))
    return before


def forget(owner, write=None):
    """その持ち主が置いたものを全部外す（MOD を外したとき）。外した種類を返す。"""
    registry = _registry()
    gone = [kind for kind, (who, _fn) in registry.items() if who == str(owner)]
    for kind in gone:
        registry.pop(kind, None)
    if write and gone:
        write("equipment: {!r} no longer decides {}".format(owner, gone))
    return gone


def source_of(kind):
    """その種類を決めている MOD の名前。誰も置いていなければ `""`。"""
    entry = _registry().get(str(kind))
    return entry[0] if entry else ""


def _call(kind, app, *args):
    entry = _registry().get(str(kind))
    if entry is None:
        return None
    owner, fn = entry
    try:
        return fn(app, *args)
    except Exception:
        log_exc("equipment: {!r} by {!r} failed; treated as not handled".format(kind, owner))
        return None


def toggle(app, holder, item):
    """仲間の品を、開いている窓の上で装備欄へ入れる／戻す。装備欄の MOD が無ければ None（聞く側が自分で書く）。"""
    return _call(TOGGLE, app, holder, item)


def equip(app, holder, item):
    """仲間の品を、窓を開かずに装備欄へ入れる。装備欄の MOD が無ければ None。

    答えは "equipped"（入った・もう入っていた）か、入れられなかった理由の文字列。
    """
    if holder is None or item is None:
        return None
    return _call(EQUIP, app, holder, item)


def equipped(app, holder, item):
    """仲間の品が装備欄に居るか。装備欄の MOD が無ければ None。"""
    return _call(EQUIPPED, app, holder, item)


def gear(app, holder):
    """身に着けている品 `[(部位, 品), ...]`。装備欄の MOD が無い／その人物が装備欄を使っていなければ None。

    形の崩れた答え（並びでない、組が 2 つでない）は None にする（聞く側は素の読み方に戻る）。
    """
    answer = _call(GEAR, app, holder)
    if answer is None:
        return None
    try:
        return [(str(name), item) for name, item in answer if item is not None]
    except (TypeError, ValueError):
        return None
