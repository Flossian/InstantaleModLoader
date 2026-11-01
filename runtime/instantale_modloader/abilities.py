# -*- coding: utf-8 -*-
"""能力値の補正の窓口。TECH.md §3.3.27。

装備・食事・一時の強化・病気や怪我のように、能力値を足したり引いたりする MOD が補正を置き、
ゲームが能力値を読む場面の間だけ、補正を足した値を見せる。補正は人物に書き込まない。

    状態から決まる補正   abilities.declare(owner, fn)        # fn(app, holder) -> {"strength": 3, …} | None
    期限つきの補正       entry = abilities.add(app, holder, {"strength": 2}, until=abilities.days(3),
                                               owner=owner, label="宿の朝食")
                         abilities.remove(app, entry)
    読む側               abilities.scores(app, holder)       # 補正を足した6つ
                         abilities.bonus(app, holder)        # 補正だけの合計
                         abilities.breakdown(app, holder)    # [{owner, label, amounts, until}]

期限は `days(n)`（n 日後まで）・`REST`（次に宿で部屋を選ぶまで）・`BATTLE`（次の戦闘の終わりまで）・
`None`（置いた MOD が外すまで）。

## ゲームが読む場面（`244_probe_ability_reads` の実測。VERIFICATION.md §1）

| 場面（この間だけ補正を足して見せる） | 見せる値 | 効くもの |
|---|---|---|
| `BattlePhaseManager.calculate_battle_effect` | `attack_power` / `magic_power` に 筋力 / 知力の補正 × m | 素点 |
| `Character.get_npc_defense` | `ability_scores` | 防御 |
| `BattlePhaseManager.battle` | `ability_scores` | 手番の順（中の `get_initiative` は入れ子の関数で包めない） |
| `QuestEventManager.execute` | `ability_scores` | 依頼の出来事の換算 |
| `document_emotion_scores_new` | 第2引数（主人公の魅力）に補正 | 印象 |

m はレベル係数 (レベル + 5) / 5（GAME.md §2.10.4）。
素点は `attack_power` / `magic_power` しか読まず、`attack_power` はロードで作られた後は組み直されない
（同じ実測）ので、筋力と知力の補正は攻撃力・魔力の側へ直して見せる。

見せ方は `Character` の3つの属性に置いた読み口（データデスクリプタ）。場面の外では素の値を返し、
場面の中では補正を足した写しを返す。書き込みは常に素の値へ通す。
`ability_scores` と `attack_power` はセーブに載らない（載るのは `original_ability_scores`）ので、
足した値が保存に混ざる経路も無い。最大 HP には効かせない。

場面の包みと読み口は、ローダが MOD の層より後（一番外側）に1枚だけ当てる（`boot()` が `install` を呼ぶ）。
MOD が自分の包みの中で読む攻撃力・能力値（`319_` / `333_`）も、補正を足した値になる。
下限は 1（攻撃力が 0 だとゲームの `geometric_mean` が投げる。GAME.md §2.10.2）。上限は置く MOD が決める。

期限つきの補正は `state/abilities/<世界>×<主人公>.json` に持つ（`state.SysWorldStore`）。
置く・外す・期限が来たときに書く。日数の期限は読むたびに比べる。
切った MOD の補正は、`declare` は `boot()` が外し（`durations.forget` の片付け）、
期限つきは読むときに数えない（ファイルには残り、MOD を戻せばまた効く）。

ステータス画面（人物欄）の能力値の行には、補正を `筋力: 30(+4)` の形で書き足す。
"""
import re
import sys
import threading

from . import durations, frames, log, log_exc, state, ui

#: 能力値の鍵（`ability_scores` の綴り）。
ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")

#: 期限: 次に宿で部屋を選ぶまで（`VacationStartManager.execute`。連泊の「まだ宿泊する」も同じ）。
REST = "rest"
#: 期限: 次の戦闘の終わりまで（戦闘の終わり方3つの `end_phase`）。
BATTLE = "battle"


def days(count):
    """期限: いまから `count` 日のあいだ（日数が `count` 進んだら切れる）。"""
    return {"days": int(count)}


#: 読み口を置く属性。素点は攻撃力・魔力、ほかは能力値を読む。
FIELDS = ("ability_scores", "attack_power", "magic_power")
#: 攻撃力・魔力のもとになる能力値（GAME.md §2.10.4）。
POWER_OF = {"attack_power": "strength", "magic_power": "intelligence"}

CHARACTER_MODULE = "scripts.characters"
CHARACTER_CLASS = "Character"
#: 補正を足して見せる場面。
SCOPES = (
    "__main__:BattlePhaseManager.calculate_battle_effect",
    "scripts.characters:Character.get_npc_defense",
    "__main__:BattlePhaseManager.battle",
    "__main__:QuestEventManager.execute",
)
CHARISMA_TARGET = "scripts.functions:document_emotion_scores_new"
REST_TARGET = "__main__:VacationStartManager.execute"
BATTLE_END_TARGETS = (
    "__main__:BattleEndManager.end_phase",
    "__main__:BattleEndInFreeAction.end_phase",
    "__main__:BattleEndInColosseum.end_phase",
)
SHEET_TARGET = "scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility"
#: 人物欄の能力値の Label（`212_probe_character_sheet` の採寸）。
SHEET_LABEL = "character_sheet_attributes"
#: 人物欄の行の名前 → 鍵。英語は頭の3文字で引く。
SHEET_NAMES = {"筋力": "strength", "器用": "dexterity", "耐久": "constitution",
               "知力": "intelligence", "判断": "wisdom", "魅力": "charisma"}
SHEET_PREFIX = {"str": "strength", "dex": "dexterity", "con": "constitution",
                "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
#: `名前: 数` の行。前に書き足した `(+4)` は剥がしてから書き直す（開き直しで重ならない）。
SHEET_LINE = re.compile(r"^(?P<head>\s*(?P<name>[^:：]+?)\s*[:：]\s*)(?P<value>-?\d+(?:\.\d+)?)"
                        r"(?:\([+-]\d+\))?(?P<tail>.*)$")

STATE_DIRNAME = "abilities"
STORE_ATTR = "_instantale_abilities_store"
_KEY_OVERRIDE_ATTR = "_instantale_abilities_key_override"
_REGISTRY_ATTR = "_instantale_abilities"
#: 読み口が引く今の世代の関数と、世代をまたぐ印。読み口のクラスは前の世代のものが残るので、中身はここから引く。
_HOOKS_ATTR = "_instantale_abilities_hooks"
#: 読み口の作り。中身を変えたら上げる（前の世代の読み口を置き直す）。
FIELD_VERSION = 1
FIELD_MARK = "_instantale_abilities_field"

_stores = state.SysWorldStore(STORE_ATTR, STATE_DIRNAME, _KEY_OVERRIDE_ATTR)
_local = threading.local()


def _hooks():
    found = getattr(sys, _HOOKS_ATTR, None)
    if not isinstance(found, dict):
        found = {"missing": object(), "view": None}
        setattr(sys, _HOOKS_ATTR, found)
    return found


def _registry():
    """`{持ち主: 関数}`（置いた順）。`sys` に置いて注入し直しをまたぐ。"""
    found = getattr(sys, _REGISTRY_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, _REGISTRY_ATTR, found)
    return found


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _amounts(raw):
    """`{能力値: 量}` に均す。知らない鍵・数でない量・0 は落とす。"""
    if not isinstance(raw, dict):
        return {}
    found = {}
    for key, value in raw.items():
        key = str(key).lower()
        value = _number(value)
        if key in ABILITIES and value:
            found[key] = value
    return found


def _add_into(total, amounts):
    for key, value in amounts.items():
        total[key] = total.get(key, 0) + value


# ---------------------------------------------------------------------------
# 置く側
# ---------------------------------------------------------------------------
def declare(owner, fn, write=None):
    """状態から決まる補正を答える関数を置く（装備など。毎回聞く）。

    `fn(app, holder)` は `{能力値: 量}` か、補正が無ければ None を返す。持ち主ごとに1つで、置き直すと差し替わる。
    """
    if not callable(fn):
        raise TypeError("declare() needs a callable, got {!r}".format(type(fn)))
    _registry()[str(owner or "")] = fn
    if write:
        write("abilities: {!r} answers the ability bonuses".format(owner))


def forget(owner, write=None):
    """その持ち主の `declare` を外す。外した種類を返す（`durations.forget` の片付け）。"""
    if _registry().pop(str(owner), None) is None:
        return []
    if write:
        write("abilities: {!r} no longer answers the ability bonuses".format(owner))
    return ["abilities"]


# 片付けの入口は `durations.forget` の1本に保つ（`combat` と同じ）。
durations.on_forget(forget)


def owners():
    """`declare` を置いている持ち主（`boot()` が今回適用されなかった MOD のぶんを外すのに使う）。"""
    return sorted(_registry())


def holder_key(app, holder):
    """期限つきの補正の宛先。主人公は `player`、ほかは人物の id の文字列。分からなければ None。"""
    if isinstance(holder, str):
        return holder or None
    if holder is None:
        return None
    if holder is frames.attr(app, "player", None):
        return ui.PLAYER_ID
    ident = holder.__dict__.get("id") if hasattr(holder, "__dict__") else None
    if ident is None or isinstance(ident, bool):
        return None
    return str(ident)


def _bucket(app):
    """`(周回の鍵, 控え)`。控えが無いか周回が分からなければ `(None, None)`。"""
    key, bucket = _stores.bucket(app)
    if bucket is None:
        return None, None
    if not isinstance(bucket.get("entries"), list):
        bucket["entries"] = []
    if not isinstance(bucket.get("next"), int):
        bucket["next"] = 1
    return key, bucket


def _save(key, bucket):
    found = _stores.store()
    return bool(found is not None and found.save(key, bucket))


def _expired(entry, today):
    until = entry.get("until")
    if isinstance(until, dict) and "day" in until:
        day = _number(until.get("day"))
        return day is not None and today is not None and today >= day
    return False


def add(app, holder, amounts, until=None, owner="", label=""):
    """期限つきの補正を置く。置いた補正の id（文字列）を返す。置けなければ None。

    `until` は `days(n)` / `REST` / `BATTLE` / None（置いた MOD が `remove` するまで）。
    """
    who = holder_key(app, holder)
    amounts = _amounts(amounts)
    if who is None or not amounts:
        return None
    today = ui.game_day(app)
    if isinstance(until, dict) and "days" in until:
        count = _number(until.get("days"))
        if count is None or today is None:
            log("abilities: cannot date the bonus of {!r} (day {!r}); not added".format(
                owner, today), level="WARN")
            return None
        stored_until = {"day": today + int(count)}
    elif until in (REST, BATTLE, None):
        stored_until = until
    else:
        raise ValueError("abilities.add(): unknown until {!r}".format(until))
    found = _stores.store()
    key, bucket = _bucket(app)
    if key is None:
        return None
    with found.lock:
        entry_id = "e{}".format(bucket["next"])
        bucket["next"] += 1
        bucket["entries"] = [e for e in bucket["entries"] if not _expired(e, today)]
        bucket["entries"].append({"id": entry_id, "holder": who, "owner": str(owner or ""),
                                  "label": str(label or ""), "amounts": amounts,
                                  "until": stored_until, "added": today})
        _save(key, bucket)
    log("abilities: {} {} +{} until {} by {!r} ({})".format(
        entry_id, who, amounts, stored_until, owner, label))
    return entry_id


def remove(app, entry_id):
    """期限つきの補正を外す。外せたら True。"""
    found = _stores.store()
    key, bucket = _bucket(app)
    if key is None:
        return False
    with found.lock:
        before = len(bucket["entries"])
        bucket["entries"] = [e for e in bucket["entries"] if e.get("id") != str(entry_id)]
        if len(bucket["entries"]) == before:
            return False
        _save(key, bucket)
    log("abilities: {} removed".format(entry_id))
    return True


def entries(app, holder=None, owner=None):
    """いま効いている期限つきの補正の写し。`holder` / `owner` で絞れる。"""
    key, bucket = _bucket(app)
    if key is None:
        return []
    who = holder_key(app, holder) if holder is not None else None
    today = ui.game_day(app)
    applied = _applied()
    found = []
    for entry in list(bucket["entries"]):
        if not isinstance(entry, dict) or _expired(entry, today):
            continue
        if who is not None and entry.get("holder") != who:
            continue
        if owner is not None and entry.get("owner") != str(owner):
            continue
        if applied is not None and entry.get("owner") not in applied:
            continue
        found.append(dict(entry, amounts=dict(entry.get("amounts") or {})))
    return found


def _applied():
    """今回適用された MOD の名前。起動の途中などで分からなければ None（絞らない）。"""
    try:
        from . import _state
    except ImportError:
        return None
    mods = _state.get("mods") if isinstance(_state, dict) else None
    if not isinstance(mods, dict) or not mods:
        return None
    return {name for name, verdict in mods.items() if verdict == "ok"} | {""}


def expire(app, kind):
    """その期限（`REST` / `BATTLE`）の補正を外す。外した数を返す。"""
    found = _stores.store()
    key, bucket = _bucket(app)
    if key is None:
        return 0
    with found.lock:
        today = ui.game_day(app)
        kept = [e for e in bucket["entries"]
                if isinstance(e, dict) and e.get("until") != kind and not _expired(e, today)]
        gone = len(bucket["entries"]) - len(kept)
        if gone:
            bucket["entries"] = kept
            _save(key, bucket)
    if gone:
        log("abilities: {} bonus(es) ended at the {}".format(gone, kind))
    return gone


# ---------------------------------------------------------------------------
# 読む側
# ---------------------------------------------------------------------------
def _parts(app, holder):
    """`[(持ち主, 名前, {能力値: 量}, 期限)]`。`declare` の答えと期限つきの補正。"""
    parts = []
    for owner, fn in list(_registry().items()):
        try:
            answer = _amounts(fn(app, holder))
        except Exception:
            log_exc("abilities: the bonus by {!r} failed; treated as none".format(owner))
            continue
        if answer:
            parts.append((owner, owner, answer, None))
    who = holder_key(app, holder)
    if who is not None:
        for entry in entries(app, who):
            amounts = _amounts(entry.get("amounts"))
            if amounts:
                parts.append((entry.get("owner", ""), entry.get("label") or entry.get("owner", ""),
                              amounts, entry.get("until")))
    return parts


def bonus(app, holder):
    """補正だけの合計 `{能力値: 量}`（0 は省く）。"""
    if holder is None:
        return {}
    total = {}
    for _owner, _label, amounts, _until in _parts(app, holder):
        _add_into(total, amounts)
    return {key: value for key, value in total.items() if value}


def breakdown(app, holder):
    """補正の内訳 `[{owner, label, amounts, until}]`（画面に内訳を出す用）。"""
    if holder is None:
        return []
    return [{"owner": owner, "label": label, "amounts": dict(amounts), "until": until}
            for owner, label, amounts, until in _parts(app, holder)]


def raw(holder, name):
    """読み口を通さない素の値（場面の中から呼んでも補正を足さない）。"""
    for klass in type(holder).__mro__:
        found = vars(klass).get(name)
        if found is not None and getattr(found, FIELD_MARK, False):
            try:
                return found.raw(holder)
            except AttributeError:
                return None
    return getattr(holder, name, None)


def scores(app, holder):
    """補正を足した能力値（6つ。下限 1）。素の能力値が読めなければ None。"""
    base = raw(holder, "ability_scores") if holder is not None else None
    if not isinstance(base, dict):
        return None
    return _boosted(base, bonus(app, holder))


def _boosted(base, amounts):
    if not amounts:
        return dict(base)
    found = dict(base)
    for key, value in amounts.items():
        current = _number(found.get(key))
        if current is not None:
            found[key] = max(1, current + value)
    return found


def level_rate(holder):
    """レベル係数 m = (レベル + 5) / 5（GAME.md §2.10.4）。読めなければ None。"""
    level = _number(getattr(holder, "experience_level", None))
    return None if level is None else (level + 5) / 5.0


def power_bonus(holder, name, amounts):
    """攻撃力・魔力に足す量（もとの能力値の補正 × m）。足さなければ 0。"""
    amount = amounts.get(POWER_OF.get(name))
    rate = level_rate(holder)
    if not amount or rate is None:
        return 0
    return amount * rate


# ---------------------------------------------------------------------------
# 読み口と場面
# ---------------------------------------------------------------------------
def in_scope():
    """このスレッドが、補正を足して見せる場面の中にいるか。"""
    return getattr(_local, "depth", 0) > 0


def _view(obj, name, value):
    """読み口が返す値。場面の外では素の値。"""
    if not in_scope():
        return value
    busy = getattr(_local, "busy", None)
    if busy is None:
        busy = _local.busy = set()
    if id(obj) in busy:
        return value        # 補正を答える関数がこの人物の値を読んだ（入れ子では素の値）
    busy.add(id(obj))
    try:
        amounts = bonus(ui.find_app(), obj)
    finally:
        busy.discard(id(obj))
    if not amounts:
        return value
    if name == "ability_scores":
        return _boosted(value, amounts) if isinstance(value, dict) else value
    current = _number(value)
    extra = power_bonus(obj, name, amounts)
    if current is None or not extra:
        return value
    return max(1, int(round(current + extra)))


def _make_field(hooks):
    """読み口のクラス。値の置き場は今までどおりインスタンスの `__dict__`（前にあった読み口があればそれ）。"""
    missing = hooks["missing"]

    class AbilityField(object):
        def __init__(self, name, inner):
            self.name = name
            self.inner = inner
            self.version = FIELD_VERSION
            setattr(self, FIELD_MARK, True)

        def raw(self, obj):
            inner = self.inner
            if inner is not missing and hasattr(type(inner), "__get__"):
                return inner.__get__(obj, type(obj))
            try:
                return obj.__dict__[self.name]
            except KeyError:
                if inner is missing:
                    raise AttributeError(self.name)
                return inner

        def __get__(self, obj, owner=None):
            if obj is None:
                return self
            value = self.raw(obj)
            view = hooks.get("view")
            if view is None:
                return value
            try:
                return view(obj, self.name, value)
            except Exception:
                return value

        def __set__(self, obj, value):
            inner = self.inner
            if inner is not missing and hasattr(type(inner), "__set__"):
                inner.__set__(obj, value)
            else:
                obj.__dict__[self.name] = value

        def __delete__(self, obj):
            inner = self.inner
            if inner is not missing and hasattr(type(inner), "__delete__"):
                inner.__delete__(obj)
                return
            try:
                obj.__dict__.pop(self.name)
            except KeyError:
                raise AttributeError(self.name)

    return AbilityField


def _install_fields():
    """`Character` に読み口を置く（置いてあれば中身の関数だけ今の世代へ）。置いた属性の数を返す。"""
    hooks = _hooks()
    hooks["view"] = _view
    module = sys.modules.get(CHARACTER_MODULE)
    cls = getattr(module, CHARACTER_CLASS, None) if module is not None else None
    if not isinstance(cls, type):
        log("abilities: {}.{} is not loaded; the bonuses are not shown to the game".format(
            CHARACTER_MODULE, CHARACTER_CLASS), level="WARN")
        return 0
    field = None
    placed = 0
    for name in FIELDS:
        current = vars(cls).get(name, hooks["missing"])
        if getattr(current, FIELD_MARK, False):
            if getattr(current, "version", None) == FIELD_VERSION:
                placed += 1
                continue
            current = current.inner          # 前の作りの読み口は剥がして、中身を引き継ぐ
        elif current is not hooks["missing"] and isinstance(current, property):
            log("abilities: {}.{} is a property; not shown with bonuses".format(
                CHARACTER_CLASS, name), level="WARN")
            continue
        if field is None:
            field = _make_field(hooks)
        setattr(cls, name, field(name, current))
        placed += 1
    return placed


def _scope(orig, *args, **kwargs):
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    try:
        return orig(*args, **kwargs)
    finally:
        _local.depth = depth


def _charisma(orig, *args, **kwargs):
    """印象の計算に渡る主人公の魅力に、魅力の補正を足す。

    呼ぶ側が場面の外で `ability_scores` を読んで渡す（会話と依頼の終わり）。
    渡った値が主人公の素の魅力と同じときだけ足す（ほかの値で呼ばれたら触らない）。
    """
    try:
        if not in_scope():
            value = frames.arg(args, kwargs, "player_charisma", 1)
            app = ui.find_app()
            player = frames.attr(app, "player", None)
            base = raw(player, "ability_scores") if player is not None else None
            own = _number(base.get("charisma")) if isinstance(base, dict) else None
            amount = bonus(app, player).get("charisma") if player is not None else None
            if amount and _number(value) is not None and own is not None and value == own:
                new = max(1, value + amount)
                if len(args) > 1:
                    args = args[:1] + (new,) + tuple(args[2:])
                else:
                    kwargs = dict(kwargs, player_charisma=new)
    except Exception:
        log_exc("abilities: cannot add the charisma bonus; passed as it is")
    return orig(*args, **kwargs)


def sheet_text(text, amounts):
    """人物欄の能力値の文字に補正を書き足す（`筋力: 30(+4)`）。補正の無い行・読めない行はそのまま。"""
    if not isinstance(text, str):
        return text
    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = SHEET_LINE.match(line)
        if match is None:
            continue
        name = match.group("name").strip()
        key = SHEET_NAMES.get(name) or SHEET_PREFIX.get(name[:3].lower())
        if key is None:
            continue
        amount = amounts.get(key)
        mark = ""
        if amount:
            amount = int(amount) if float(amount).is_integer() else round(amount, 1)
            mark = "({}{})".format("+" if amount > 0 else "", amount)
        lines[index] = "{}{}{}{}".format(match.group("head"), match.group("value"), mark,
                                         match.group("tail"))
    return "\n".join(lines)


def _sheet_holder(app, hud):
    """人物欄に出ている相手（名前で引く。`121_` と同じ）。"""
    player = frames.attr(app, "player", None)
    data = frames.attr(hud, "visible_character_sheet_data", None)
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip() or getattr(player, "name", None) == name:
        return player
    characters = frames.attr(frames.attr(app, "world", None), "characters", None)
    if isinstance(characters, dict):
        for character in characters.values():
            if getattr(character, "name", None) == name:
                return character
    return player


def _sheet(orig, self, *args, **kwargs):
    result = orig(self, *args, **kwargs)
    try:
        label = frames.attr(self, SHEET_LABEL, None)
        text = getattr(label, "text", None)
        if isinstance(text, str) and text:
            app = ui.find_app()
            amounts = bonus(app, _sheet_holder(app, self))
            new = sheet_text(text, amounts)
            if new != text:
                label.text = new
    except Exception:
        log_exc("abilities: cannot show the bonuses on the character sheet")
    return result


def _rest(orig, self, *args, **kwargs):
    try:
        expire(frames.attr(self, "app", None) or ui.find_app(), REST)
    except Exception:
        log_exc("abilities: cannot end the bonuses until the rest")
    return orig(self, *args, **kwargs)


def _battle_end(orig, self, *args, **kwargs):
    try:
        return orig(self, *args, **kwargs)
    finally:
        try:
            expire(frames.attr(self, "app", None) or ui.find_app(), BATTLE)
        except Exception:
            log_exc("abilities: cannot end the bonuses until the battle end")


def detach():
    """読み口を素通しにする（ローダを外したとき。読み口のクラスはそのまま残る）。"""
    _hooks()["view"] = None


def install(ctx):
    """1つの世代に1回。`boot()` が MOD の適用の後に呼ぶ（一番外側に当たる）。"""
    _stores.bind(ctx)
    placed = _install_fields()
    for target in SCOPES:
        ctx.wrap(target, required=False, safe=True)(_scope)
    ctx.wrap(CHARISMA_TARGET, required=False, safe=True)(_charisma)
    ctx.wrap(REST_TARGET, required=False, safe=True)(_rest)
    for target in BATTLE_END_TARGETS:
        ctx.wrap(target, required=False, safe=True)(_battle_end)
    ctx.wrap(SHEET_TARGET, required=False, safe=True)(_sheet)
    log("abilities: {} field(s) on {}; {} answer(s) ({})".format(
        placed, CHARACTER_CLASS, len(_registry()), ", ".join(owners()) or "none"))
    return placed
