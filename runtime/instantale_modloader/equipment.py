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
そのため切った MOD のぶんは、ローダの `boot()` と `unload` が `durations.forget` の片付けとして外す。

##### 貸し（その場面のあいだだけ別の品で戦わせる）

    貸す側   equipment.install(ctx)                                  # apply() の中。セーブとロードの包み
             equipment.lend(app, holder, {"weapon": 品, "wearable": 品}, owner="336_…")
             equipment.end_loan(app, holder)                         # 場面が終わったら返す
    聞く側   equipment.loan(app, holder)                             # {"weapon": 品, …} | None
             equipment.lender(app, holder)                           # 貸した MOD の名前 | None

貸しの間は本体の `equipments` の weapon / wearable が貸した品を指し、`gear` と `combat` の答えも
貸した品になる。装備欄の MOD（`333_`）は貸しの間、本体の装備を装備欄から組み直さない。
貸した品は持ち物にもセーブにも入れない。セーブには元の装備の id を書くので、
貸しの途中で落ちてもロードすれば元の装備に戻る。ロード（`World.__init__`）は貸しを捨てる。
`336_crime_overhaul` が脱獄の決行の戦闘で使う（VERIFICATION.md §3.91）。
"""
import sys

from . import durations
from . import log, log_exc

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
_LOANS_ATTR = "_instantale_equipment_loans"
_GATE_ATTR = "_instantale_equipment_gate"

#: 本体が読む装備の2つと、その能力値の名前（GAME.md §2.13.3）。
GAME_KEYS = (("weapon", "攻撃力"), ("wearable", "防御力"))
#: 貸した品を `gear` で答えるときの部位（装備欄の MOD の部位の名前）。
LOAN_REGIONS = {"weapon": "right_hand", "wearable": "body"}

SAVE_TARGET = "scripts.save_codec:write_obfuscated_json_file"
WORLD_TARGET = "__main__:World.__init__"


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
    """その持ち主が置いたもの・貸したものを全部外す（MOD を外したとき）。外した種類を返す。"""
    registry = _registry()
    gone = [kind for kind, (who, _fn) in registry.items() if who == str(owner)]
    for kind in gone:
        registry.pop(kind, None)
    if write and gone:
        write("equipment: {!r} no longer decides {}".format(owner, gone))
    lent = [entry for entry in _loans() if entry["owner"] == str(owner)]
    if lent:
        from . import ui
        app = ui.find_app()
        for entry in lent:
            end_loan(app, entry["holder"], write=write)
        gone.append("loan")
    return gone


# 片付けの入口は `durations.forget` の1本に保つ（`prices` と同じ）。
durations.on_forget(forget)


def owners():
    """何かを置いている持ち主の名前。

    ローダの `boot()` が、今回適用されなかった MOD のぶんを外すのに使う
    （登録簿は注入をまたいで残るので、切った MOD の旧い関数に聞き続けないように）。
    """
    return sorted({who for who, _fn in _registry().values()} | {entry["owner"] for entry in _loans()})


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
    貸しの間は、装備欄の MOD が居れば貸した品を答える（居なければ None。素の読み方が貸した品を読む）。
    """
    lent = loan(app, holder)
    if lent is not None:
        if str(GEAR) not in _registry():
            return None
        return [(LOAN_REGIONS[key], item) for key, item in lent.items()]
    answer = _call(GEAR, app, holder)
    if answer is None:
        return None
    try:
        return [(str(name), item) for name, item in answer if item is not None]
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ 貸し
def _loans():
    """`[{"holder", "owner", "items": {種類: 品}, "kept": {種類: 元の値}}]`。`sys` に置いて注入し直しをまたぐ。"""
    found = getattr(sys, _LOANS_ATTR, None)
    if not isinstance(found, list):
        found = []
        setattr(sys, _LOANS_ATTR, found)
    return found


def _entry(holder):
    for entry in _loans():
        if entry["holder"] is holder:
            return entry
    return None


def _write_default(text):
    """窓口の記録は `modloader.log` へ。"""
    try:
        log(text, level="WARN" if text.startswith("WARN") else "INFO")
    except Exception:
        pass


def _native(app, name, item):
    """本体の ItemEquipManager / ItemUnequipManager の中身を呼ぶ（画面上部の Atk/Def の塗り直しと効果音）。

    `execute` は所持品の窓を閉じるので、中身の `equip_item` / `unequip_item` を直に呼ぶ（`333_` と同じ）。
    本体の unequip は品の種類の枠（`equipments[item_type]`）を落とす（GAME.md §2.13.3）。
    """
    cls = getattr(sys.modules.get("__main__"), name, None)
    if app is None or not isinstance(cls, type):
        return False
    method = "equip_item" if name == "ItemEquipManager" else "unequip_item"
    try:
        getattr(cls(app), method)(item)
        return True
    except Exception:
        log_exc("equipment: {}.{} failed".format(name, method))
        return False


def _resolve(holder, ref):
    """`equipments` の値（実体か、セーブから読んだ直後の id の文字列）を品にする。引けなければ None。"""
    if ref is None or not isinstance(ref, (str, int)):
        return ref
    from . import items
    return (items.inventory_of(holder) or {}).get(str(ref))


def _ref_id(ref):
    """`equipments` の値をセーブの形（id の文字列）にする。読めなければ None。"""
    if ref is None:
        return None
    if isinstance(ref, (str, int)):
        return str(ref)
    value = getattr(ref, "id", None)
    return str(value) if value is not None else None


def _is_player(app, holder):
    return app is not None and holder is not None and holder is getattr(app, "player", None)


def lend(app, holder, items, owner="", write=None):
    """`items`（`{"weapon": 品, "wearable": 品}`）を貸す。貸した種類の一覧を返す。

    品は持ち物に入れずに作ったもの（`items.make_loose`）を渡す。持ち物に居る品を貸すと、
    セーブに元の装備の id を書いても、その品は持ち物としてセーブに残る。
    同じ人物へ重ねて貸したときは、最初に貸す前の装備を返し先として残す。
    """
    write = write or _write_default
    eq = getattr(holder, "equipments", None) if holder is not None else None
    if not isinstance(eq, dict) or not isinstance(items, dict):
        write("WARN equipment: cannot lend to {!r}".format(getattr(holder, "name", holder)))
        return []
    entry = _entry(holder)
    if entry is None:
        entry = {"holder": holder, "owner": str(owner or ""), "items": {}, "kept": {}}
        _loans().append(entry)
    lent = []
    for key, _stat in GAME_KEYS:
        item = items.get(key)
        if item is None:
            continue
        if key not in entry["items"]:
            entry["kept"][key] = eq.get(key)          # None は「何も着けていなかった」
        entry["items"][key] = item
        eq[key] = item
        if _is_player(app, holder):
            _native(app, "ItemEquipManager", item)
        lent.append(key)
    if not entry["items"]:
        _loans().remove(entry)
    write("equipment: {!r} lent {} to {!r} (kept {})".format(
        owner, ", ".join("{}={!r}".format(key, getattr(entry["items"][key], "name", None)) for key in lent),
        getattr(holder, "name", None), {key: _ref_id(entry["kept"].get(key)) for key in lent}))
    return lent


def loan(app, holder):
    """貸している品 `{種類: 品}`。貸していなければ None。"""
    entry = _entry(holder) if holder is not None else None
    return dict(entry["items"]) if entry is not None and entry["items"] else None


def lender(app, holder):
    """その人物に品を貸している MOD の名前。貸していなければ None。"""
    entry = _entry(holder) if holder is not None else None
    return entry["owner"] if entry is not None else None


def loan_value(app, holder, key):
    """貸している品の能力値（weapon は攻撃力、wearable は防御力）。その種類を貸していなければ None。"""
    lent = loan(app, holder)
    item = lent.get(key) if lent else None
    if item is None:
        return None
    attrs = getattr(item, "attributes", None)
    try:
        return float((attrs if isinstance(attrs, dict) else {}).get(dict(GAME_KEYS)[key], 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def end_loan(app, holder, write=None):
    """貸した品を引き上げ、元の装備に戻す。戻した種類の一覧を返す。

    貸しの間に持ち主が着け替えた種類（`equipments` が貸した品を指していない）はそのまま残す。
    装備欄の MOD は、ここで呼ぶ本体の Manager の後に装備欄から組み直す（`333_` の `resync_after_native`）。
    """
    write = write or _write_default
    entry = _entry(holder) if holder is not None else None
    if entry is None:
        return []
    _loans().remove(entry)                              # 先に外す（Manager の後の組み直しが貸しを見ない）
    eq = getattr(holder, "equipments", None)
    player = _is_player(app, holder)
    back = []
    for key, item in entry["items"].items():
        if not isinstance(eq, dict) or eq.get(key) is not item:
            continue
        kept = entry["kept"].get(key)
        real = _resolve(holder, kept)
        if player and real is None:
            _native(app, "ItemUnequipManager", item)    # 枠を落とし、画面上部を (+0) に
        if kept is None:
            eq.pop(key, None)
        else:
            eq[key] = kept
        if player and real is not None:
            _native(app, "ItemEquipManager", real)
        back.append(key)
    write("equipment: {!r} took back {} from {!r}".format(
        entry["owner"], ", ".join(back) or "nothing", getattr(holder, "name", None)))
    return back


def _restore_in_save(data, write):
    """書き出すセーブの主人公の `equipments` を、貸す前の装備の id に戻す。戻した種類を返す。"""
    loans = _loans()
    player_data = data.get("player_data") if loans and isinstance(data, dict) else None
    eq = player_data.get("equipments") if isinstance(player_data, dict) else None
    if not isinstance(eq, dict):
        return []
    from . import ui
    app = ui.find_app()
    entry = _entry(getattr(app, "player", None)) if app is not None else None
    if entry is None:
        return []
    restored = []
    for key, item in entry["items"].items():
        saved = eq.get(key)
        if saved is not None and saved is not item and str(saved) != str(getattr(item, "id", "")):
            continue                                    # 貸した品ではない（貸しの間に着け替えた）
        kept = _ref_id(entry["kept"].get(key))
        if kept is None:
            eq.pop(key, None)
        else:
            eq[key] = kept
        restored.append(key)
    if restored:
        write("equipment: the save keeps the own equipment instead of the loan ({})".format(
            ", ".join(restored)))
    return restored


def install(ctx):
    """貸しのためにセーブの書き出しとロードを包む。何本の MOD が呼んでも1つの世代に1回だけ（`guards` と同じ）。"""
    generation = getattr(ctx, "generation", None)
    done = getattr(sys, _GATE_ATTR, None)
    if isinstance(done, dict) and done.get("generation") == generation:
        return list(done.get("targets") or [])

    def write_save(orig, file_path, data, *args, **kwargs):
        try:
            _restore_in_save(data, _write_default)
        except Exception:
            log_exc("equipment: cannot keep the own equipment in the save")
        return orig(file_path, data, *args, **kwargs)

    def world_loaded(orig, self, *args, **kwargs):
        """ロード・新規開始。前の周回の貸しは捨てる（持ち主の実体ごと入れ替わる）。"""
        loans = _loans()
        if loans:
            _write_default("equipment: dropped {} loan(s) of the previous play".format(len(loans)))
            del loans[:]
        return orig(self, *args, **kwargs)

    ctx.wrap(SAVE_TARGET, required=False, safe=True)(write_save)
    ctx.wrap(WORLD_TARGET, required=False, safe=True)(world_loaded)
    targets = [SAVE_TARGET, WORLD_TARGET]
    setattr(sys, _GATE_ATTR, {"generation": generation, "targets": targets})
    _write_default("equipment: wrapped {}".format(", ".join(targets)))
    return targets
