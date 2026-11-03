# -*- coding: utf-8 -*-
r"""計測: ゲームが能力値をどこで読み、派生の値（攻撃力・魔力・防御・最大 HP）をいつ組み直すか。ゲームは変えない。

能力値の補正の窓口（ローダの `abilities`）と、`802_equipment_ability_bonus` の乗せ替えの下調べ。
窓口は能力値を書き換えず、ゲームが読む入口で足す作りにしたい。そのために入口の位置を決める。

`222_` / `236_` の記録では、`ability_scores` が `original_ability_scores` より1低い仲間が5件あり
（どれも HP が減っていた）、Lv80 の仲間の `attack_power` は筋力 30 のまま 500・490・510 と動いていた。
ゲームは元の値から派生の値を組み直しているが、いつ・何からかは記録に無い。

| 問い | 見るところ |
|---|---|
| 派生の値を組み直すのは誰か・いつか | `recompute`（`update_ability_scores` / `update_max_hp` の前後）と `set`（属性の書き込み） |
| 戦闘の素点は `attack_power` を読むか、能力値から組み直すか | `try_power` |
| 仲間の防御は `ability_scores` と `original_ability_scores` のどちらを読むか | `try_defense` |
| 負傷や消耗での目減りは何を下げるか | `convert`（`calculate_attribute`）と `recompute` |
| 印象の魅力はどの持ち方から来るか | `charisma`（`document_emotion_scores_new`） |

録るもの（1件＝1行）

    recompute   `Character.update_ability_scores` / `update_max_hp` の前後で変わった項目。誰か、呼び出し元、
                どの計算の内側か（素点・防御・手の解決）、スレッド
    set         `ability_scores` / `original_ability_scores` / `attack_power` / `magic_power` / `max_hp` /
                `original_max_hp` への代入（240_ と同じくクラスにデータデスクリプタを置いて拾う）。
                旧値 → 新値（辞書は変わった鍵だけ）、誰か、呼び出し元、MOD が書いたか。生まれたときの初回は書かない。
                辞書の中身だけを書き換える経路は拾えないので、`recompute` の前後の差で見る
    convert     `Character.calculate_attribute(attribute_score)` の引数と戻り、そのときの HP・スタミナ・部位の怪我
    charisma    `document_emotion_scores_new` に渡った魅力と、主人公の `ability_scores` / `original_ability_scores` の魅力。
                2つの持ち方が同じ値のときは見分けがつかない（`distinct: false`）。
                ローダの能力値の窓口（`abilities`）は一番外側でこの第2引数に魅力の補正を足すので、
                補正（`abilities.bonus` の魅力）と、`ability_scores` に補正を足した値（`boosted`）も並べ、
                渡った値がそれと同じなら `matches` に `ability_scores+bonus` を入れる
    try_power   戦闘の開始で1プロセス1回（相手の名前とレベルごと）。包む前の素の `calculate_battle_effect` に
                normal の1手を渡し、乱数を同じ状態に戻してから、そのまま／攻撃力（魔法は魔力）だけ2倍／
                `ability_scores` の筋力（魔法は知力）だけ +10／`original_ability_scores` の同じ値だけ +10 で素点を比べる。
                そのままを2回呼んで同じ素点でなければ、乱数を戻せていないので判定しない（`noisy`）
    try_defense 同じく1回。包む前の素の `get_npc_defense` を、そのまま／`ability_scores` を6つとも +10／
                `original_ability_scores` を6つとも +10 で呼び比べる
    try_abort   試しの前後で HP か状態異常が動いた。以後は試さない

    out\ability_reads.log     読む用
    out\ability_reads.jsonl   後から数える用

試しで変えた値はインスタンスの `__dict__` へ直に置き、呼んだらすぐ同じ物に戻す（見張りにも掛からない）。
戻すのは、置いた値がそのまま残っているときだけ（試しの間に別のスレッドが書いた値は消さない）。
乱数の状態は試しの前に控えて、終わったら戻す（`236_` の `rng_saved` と同じ形）。
試しの間は `sys` の `DRY_THREAD_MARK` に自分のスレッドの id を置く（`222_` は記録を控える）。外すときは置く前の値へ戻す。
見張りは1プロセスに1組（`sys` の `STORE`）。注入し直しても積み上がらず、書き手（`sink`）だけ差し替わる。
引数は受け取ったまま `orig` へ渡す。state\ もセーブも動かさない。
"""
import datetime
import random
import sys
import threading

from instantale_modloader import abilities, frames, patch, ui

LOG_BASENAME = "ability_reads.log"
RECORD_BASENAME = "ability_reads.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
#: 1つの種類（と呼び出し元）につき何件まで録るか（1プロセスあたり）。
SAMPLES = 30
#: 戦闘の開始で素点と防御を試すか。
TRY_AT_BATTLE = True

#: 能力値の鍵（`ability_scores` の綴り）。
ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
#: 書き込みを見張る属性。
WATCHED = ("ability_scores", "original_ability_scores", "attack_power", "magic_power",
           "max_hp", "original_max_hp")
#: 前後を比べる項目。
SNAPSHOT = WATCHED + ("current_hp", "physical_integrity", "max_physical_integrity",
                      "experience_level", "defense")
#: 組み直しの関数（`scripts.characters:Character`）。
RECOMPUTE = ("update_ability_scores", "update_max_hp")
#: 内側かどうかを数える計算。`(対象, 名前)`。
INSIDE = (
    ("__main__:BattlePhaseManager.calculate_battle_effect", "calculate_battle_effect"),
    ("__main__:BattlePhaseManager.resolve_battle_effect", "resolve_battle_effect"),
    ("scripts.characters:Character.get_npc_defense", "get_npc_defense"),
)
CHARISMA_TARGET = "scripts.functions:document_emotion_scores_new"
BATTLE_START = "__main__:BattleStartManager.start_battle"

#: 試しで能力値に足す量と、攻撃力・魔力に掛ける倍率。
BUMP = 10
POWER_RATE = 2.0
#: 試しの素点の手（物理は攻撃力と筋力、魔法は魔力と知力）。
PLANS = (("physical", "attack_power", "strength"), ("magical", "magic_power", "intelligence"))

#: 見張りを置くクラス。
CHARACTER_MODULE = "scripts.characters"
CHARACTER_CLASS = "Character"

#: 見張りの置き場（`sys` の属性名。注入し直しても同じ物を使う）。
STORE = "__instantale_probe_ability_reads__"
#: 見張りの作り。中身を変えたら上げる（古い見張りを置き直す）。
WATCH_VERSION = 1
#: ローダの能力値の読み口（`abilities.py`）の印と、その「値が無い」の印の置き場。
ABILITY_FIELD_MARK = "_instantale_abilities_field"
ABILITY_HOOKS = "_instantale_abilities_hooks"
#: 試し済みの相手（側・名前・レベル）。1プロセス1回にする。
TRIED_MARK = "_instantale_probe_244_tried"
#: 試しの間だけ置くスレッドの印（`236_` と同じ名前）。
DRY_THREAD_MARK = "_instantale_probe_dry_run_thread"
#: 乱数の控えを探すモジュール。
RNG_MODULES = ("__main__", "scripts.functions")
CALLER_DEPTH = 6


def _store():
    store = getattr(sys, STORE, None)
    if not isinstance(store, dict):
        store = {"sink": None, "version": None, "installed": [], "counts": {}}
        setattr(sys, STORE, store)
    store.setdefault("counts", {})
    return store


#: 「値が無い」の番人。プロセスで1つ（注入し直してもクラスの見張りは前の番人を握る）。
_MISSING = _store().setdefault("missing", object())


def _make_watch(store):
    """属性1つの見張り（データデスクリプタ）のクラス。`240_` の `StaminaWatch` と同じ形。

    値はインスタンスの `__dict__` に置く（ゲームが今まで置いていた場所）。
    書き込みは記録より先に済ませ、書き手の例外はゲームへ流さない。
    """
    class AbilityWatch(object):
        def __init__(self, name, fallback):
            self.name = name
            self.fallback = fallback
            self.version = WATCH_VERSION

        def __get__(self, obj, owner=None):
            if obj is None:
                return self
            try:
                return obj.__dict__[self.name]
            except KeyError:
                if self.fallback is _MISSING:
                    raise AttributeError(self.name)
                return self.fallback

        def __set__(self, obj, value):
            old = obj.__dict__.get(self.name, _MISSING)
            obj.__dict__[self.name] = value
            sink = store.get("sink")
            if sink is not None and old is not _MISSING:
                try:
                    sink(obj, self.name, old, value)
                except Exception:
                    pass

        def __delete__(self, obj):
            try:
                obj.__dict__.pop(self.name)
            except KeyError:
                raise AttributeError(self.name)

    return AbilityWatch


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def plain(value, depth=0):
    """JSON に載る形へ縮める。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return frames.short(value, 80)
    if depth >= 3:
        return frames.short(repr(value), 80)
    if isinstance(value, dict):
        return {str(k): plain(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [plain(v, depth + 1) for v in list(value)[:20]]
    return frames.short(repr(value), 80)


def changes(old, new):
    """旧値 → 新値。辞書は変わった鍵だけ `{鍵: [旧, 新]}`、それ以外は `[旧, 新]`。"""
    if isinstance(old, dict) and isinstance(new, dict):
        keys = list(old) + [k for k in new if k not in old]
        return {str(k): [plain(old.get(k)), plain(new.get(k))] for k in keys
                if old.get(k) != new.get(k)}
    return [plain(old), plain(new)]


def snapshot(obj):
    """前後を比べる項目の写し（辞書は浅く写す）。"""
    found = {}
    holder = getattr(obj, "__dict__", None)
    if not isinstance(holder, dict):
        return found
    for name in SNAPSHOT:
        value = holder.get(name, _MISSING)
        if value is _MISSING:
            continue
        found[name] = dict(value) if isinstance(value, dict) else value
    return found


def body_of(character):
    """部位ごとの `[injury, stage]`。intact で injury が 0 の部位は省く（`240_` と同じ）。"""
    parts = frames.attr(character, "body_parts", None)
    out = {}
    if not isinstance(parts, dict):
        return out
    for part, row in parts.items():
        if not isinstance(row, dict):
            continue
        injury, stage = row.get("injury"), row.get("stage")
        if stage == "intact" and not injury:
            continue
        out[str(part)] = [plain(injury), plain(stage)]
    return out


def level_rate(character):
    level = _number(frames.attr(character, "experience_level", None))
    return None if level is None else (level + 5) / 5.0


def average_times_rate(table, character):
    """6能力の平均 × レベル係数（GAME.md §2.10.4 の防御の式）。"""
    rate = level_rate(character)
    if rate is None or not isinstance(table, dict):
        return None
    values = [_number(table.get(k)) for k in ABILITIES]
    if any(v is None for v in values):
        return None
    return round(sum(values) / float(len(values)) * rate, 3)


def rng_saved():
    """いまの乱数の状態を控える。`[(戻す関数, 状態, 名前)]`（`236_` と同じ形）。"""
    saved = [(random.setstate, random.getstate(), "random")]
    numpy = sys.modules.get("numpy")
    legacy = getattr(getattr(numpy, "random", None), "get_state", None)
    if callable(legacy):
        try:
            saved.append((numpy.random.set_state, legacy(), "numpy.random"))
        except Exception:
            pass
    seen = {id(getattr(random, "_inst", None))}
    for name in RNG_MODULES:
        module = sys.modules.get(name)
        try:
            items = list(vars(module).items()) if module is not None else []
        except Exception:
            items = []
        for key, value in items:
            if isinstance(value, random.Random) and id(value) not in seen:
                seen.add(id(value))
                saved.append((value.setstate, value.getstate(), "{}.{}".format(name, key)))
    return saved


def rng_restore(saved):
    for setter, state, _name in saved:
        try:
            setter(state)
        except Exception:
            pass


class Swapped(object):
    """試しの間だけ、インスタンスの `__dict__` の値を差し替えて、同じ物に戻す。

    戻すのは、置いた値（同じ物）がまだ残っているときだけ。試しの間に別のスレッドが同じ属性を
    書いていたら、その値を消さずに残す（戻すと、その書き込みが無かったことになる）。
    """

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value
        self.old = _MISSING

    def __enter__(self):
        self.old = self.obj.__dict__.get(self.name, _MISSING)
        self.obj.__dict__[self.name] = self.value
        return self

    def __exit__(self, *exc):
        holder = self.obj.__dict__
        if holder.get(self.name, _MISSING) is not self.value:
            return False        # 別の書き込みが入った。そちらを残す
        if self.old is _MISSING:
            holder.pop(self.name, None)
        else:
            holder[self.name] = self.old
        return False


def bumped(table, keys):
    """能力値の辞書の写しで、`keys` だけ BUMP を足したもの。読めなければ None。"""
    if not isinstance(table, dict):
        return None
    out = dict(table)
    for key in keys:
        value = _number(out.get(key))
        if value is None:
            return None
        out[key] = value + BUMP
    return out


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    store = _store()
    local = threading.local()
    cached = {"app": None}
    tried = getattr(sys, TRIED_MARK, None)
    if not isinstance(tried, set):
        tried = set()
        setattr(sys, TRIED_MARK, tried)
    try_state = {"stopped": False}

    def now():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def app_of():
        app = cached["app"]
        if app is None:
            app = ui.find_app()
            cached["app"] = app
        return app

    def saturated(kind):
        """その種類の呼び出しが SAMPLES × 50 回を超えたら True。呼び出し元をたどる前に見る（たどるのが重い）。"""
        counts = store["counts"]
        key = kind + "#calls"
        counts[key] = counts.get(key, 0) + 1
        return counts[key] > SAMPLES * 50

    def allow(*key):
        """その種類（と呼び出し元）でまだ SAMPLES 件に届いていなければ数えて True。判定は重い処理より先。"""
        counts = store["counts"]
        key = "|".join(str(k) for k in key)
        if counts.get(key, 0) >= SAMPLES:
            return False
        counts[key] = counts.get(key, 0) + 1
        return True

    def emit(kind, line, **fields):
        row = {"at": now(), "kind": kind, "thread": threading.current_thread().name,
               "dry": getattr(sys, DRY_THREAD_MARK, None) == threading.get_ident()}
        row.update(fields)
        record(row)
        write("{} {}".format(kind, line))

    def who_of(app, obj):
        """`player` / `party:<id>` / `enemy:<鍵>` / `other`。"""
        if obj is frames.attr(app, "player", None):
            return ui.PLAYER_ID
        holder = getattr(obj, "__dict__", None)
        ident = holder.get("id") if isinstance(holder, dict) else None
        if ident is not None and str(ident) in {str(m) for m in ui.party_member_ids(app)}:
            return "party:" + str(ident)
        enemies = frames.attr(app, "current_enemy_dict", None)
        if isinstance(enemies, dict):
            for key, value in enemies.items():
                if value is obj:
                    return "enemy:" + str(key)
        return "other"

    def inside():
        return list(getattr(local, "stack", ()) or ())

    def called_from_a_mod():
        """直に呼んだのが MOD のファイルなら True。

        この probe とローダのフレームを飛ばし、最初に出たフレームで決める。
        途中のどこかに MOD があるかで見ると、ほかの MOD の包みを通ったゲームの呼び出しまで真になる。
        この probe の包みは適用順の最後で一番外にあるので、最初のフレームが呼び出し元になる。
        """
        for index in range(2, 40):
            try:
                frame = sys._getframe(index)
            except ValueError:
                return False
            name = frame.f_code.co_filename
            if name == __file__ or "instantale_modloader" in name.replace("/", "\\"):
                continue
            return frames.is_ours(name)
        return False

    # ------------------------------------------------------------ 書き込みの見張り
    def sink(obj, name, old, new):
        if ctx.superseded():
            return      # 外した・切って注入し直した後の古い書き手（見張りはクラスに残る）
        try:
            same = old == new
        except Exception:
            same = old is new
        if same:
            return
        app = app_of()
        if app is None or saturated("set"):
            return
        caller = frames.caller(depth=CALLER_DEPTH)
        if not allow("set", name, caller):
            return
        who = who_of(app, obj)
        diff = changes(old, new)
        within, by_mod = inside(), called_from_a_mod()
        emit("set", "{} {}({}) {} inside={} mod={}  {}".format(
            name, who, frames.short(obj.__dict__.get("name"), 30), diff, within, by_mod, caller),
            attr=name, who=who, name=plain(obj.__dict__.get("name")), change=diff,
            level=plain(obj.__dict__.get("experience_level")), inside=within,
            by_mod=by_mod, caller=caller)

    def install():
        """見張りを置く（置いてあれば書き手だけ差し替える）。置けた属性の数を返す。"""
        module = sys.modules.get(CHARACTER_MODULE)
        cls = getattr(module, CHARACTER_CLASS, None) if module is not None else None
        if not isinstance(cls, type):
            write("WARN {}.{} is not loaded yet; writes are not watched".format(
                CHARACTER_MODULE, CHARACTER_CLASS))
            return 0
        if store.get("version") != WATCH_VERSION:
            watch = _make_watch(store)
            installed = []
            for name in WATCHED:
                previous = vars(cls).get(name, _MISSING)
                if getattr(previous, ABILITY_FIELD_MARK, False):
                    # ローダの能力値の読み口（abilities.py）。見張りはその内側（素の値の置き場）に置く
                    inner = previous.inner
                    if type(inner).__name__ == "AbilityWatch":
                        inner = inner.fallback
                    if inner is (getattr(sys, ABILITY_HOOKS, None) or {}).get("missing"):
                        inner = _MISSING
                    previous.inner = watch(name, inner)
                    installed.append(name)
                    continue
                ours = type(previous).__name__ == "AbilityWatch"
                if (not ours and previous is not _MISSING
                        and getattr(type(previous), "__get__", None) is not None):
                    # クラスが自分で持つ property などは上書きしない（値の出どころが変わる）
                    write("WARN {} is a {} on the class; not watched".format(
                        name, type(previous).__name__))
                    continue
                fallback = getattr(previous, "fallback", previous) if ours else previous
                setattr(cls, name, watch(name, fallback))
                installed.append(name)
            store["version"] = WATCH_VERSION
            store["installed"] = installed
            write("watching {} on {}.{} (version {})".format(
                ", ".join(installed), CHARACTER_MODULE, CHARACTER_CLASS, WATCH_VERSION))
        store["sink"] = sink
        return len(store["installed"])

    install()

    # ------------------------------------------------------------ どの計算の内側か
    def inside_of(label):
        def wrapper(orig, *args, **kwargs):
            stack = getattr(local, "stack", None)
            if stack is None:
                stack = local.stack = []
            stack.append(label)
            try:
                return orig(*args, **kwargs)
            finally:
                stack.pop()
        return wrapper

    for _target, _label in INSIDE:
        ctx.wrap(_target, required=False, safe=True)(inside_of(_label))

    # ------------------------------------------------------------ 組み直し
    def recompute_of(label):
        def wrapper(orig, self, *args, **kwargs):
            app = app_of()
            if app is None or saturated("recompute"):
                return orig(self, *args, **kwargs)
            caller = frames.caller(depth=CALLER_DEPTH)
            if not allow("recompute", label, caller):
                return orig(self, *args, **kwargs)
            before = snapshot(self)
            result = orig(self, *args, **kwargs)
            try:
                after = snapshot(self)
                diff = {k: changes(before.get(k), after.get(k)) for k in after
                        if before.get(k) != after.get(k)}
                who = who_of(app, self)
                within = inside()
                emit("recompute", "{} {}({}) changed={} inside={}  {}".format(
                    label, who, frames.short(self.__dict__.get("name"), 30), diff or "-",
                    within, caller),
                    function=label, who=who, name=plain(self.__dict__.get("name")),
                    changed=diff, before={k: plain(v) for k, v in before.items()},
                    body=body_of(self), inside=within, by_mod=called_from_a_mod(),
                    caller=caller)
            except Exception:
                ctx.log_exc("ability reads probe: recording {} failed".format(label))
            return result
        return wrapper

    for _name in RECOMPUTE:
        ctx.wrap("scripts.characters:Character." + _name, required=False,
                 safe=True)(recompute_of(_name))

    # ------------------------------------------------------------ 目減りの換算
    @ctx.wrap("scripts.characters:Character.calculate_attribute", required=False, safe=True)
    def calculate_attribute(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = app_of()
            if app is None or saturated("convert"):
                return result
            raw = frames.arg(args, kwargs, "attribute_score", 0)
            caller = frames.caller(depth=CALLER_DEPTH)
            if not allow("convert", raw == result, caller):
                return result
            who = who_of(app, self)
            body = body_of(self)
            emit("convert", "{}({}) {} -> {} hp={}/{} pi={}/{} body={}  {}".format(
                who, frames.short(self.__dict__.get("name"), 30), plain(raw), plain(result),
                plain(self.__dict__.get("current_hp")), plain(self.__dict__.get("max_hp")),
                plain(self.__dict__.get("physical_integrity")),
                plain(self.__dict__.get("max_physical_integrity")), body, caller),
                who=who, name=plain(self.__dict__.get("name")), arg=plain(raw),
                result=plain(result), hp=plain(self.__dict__.get("current_hp")),
                max_hp=plain(self.__dict__.get("max_hp")),
                pi=plain(self.__dict__.get("physical_integrity")),
                max_pi=plain(self.__dict__.get("max_physical_integrity")),
                body=body, inside=inside(), by_mod=called_from_a_mod(), caller=caller)
        except Exception:
            ctx.log_exc("ability reads probe: recording calculate_attribute failed")
        return result

    # ------------------------------------------------------------ 印象の魅力
    @ctx.wrap(CHARISMA_TARGET, required=False, safe=True)
    def document_emotion_scores(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        try:
            app = app_of()
            if app is None or saturated("charisma"):
                return result
            player = frames.attr(app, "player", None)
            given = frames.arg(args, kwargs, "player_charisma", 1)
            scores = frames.attr(player, "ability_scores", None)
            original = frames.attr(player, "original_ability_scores", None)
            current = scores.get("charisma") if isinstance(scores, dict) else None
            base = original.get("charisma") if isinstance(original, dict) else None
            distinct = current is not None and base is not None and current != base
            # ローダの窓口が一番外側で足した魅力の補正（足すのは渡った値が素の魅力と同じとき。下限 1）
            extra = None
            if player is not None:
                try:
                    extra = _number(abilities.bonus(app, player).get("charisma"))
                except Exception:
                    extra = None
            boosted = (max(1, current + extra)
                       if extra and _number(current) is not None else None)
            caller = frames.caller(depth=CALLER_DEPTH)
            if allow("charisma", distinct, given == current, given == base,
                     boosted is not None and given == boosted, caller):
                source = [name for name, value in (("ability_scores", current),
                                                   ("original_ability_scores", base),
                                                   ("ability_scores+bonus", boosted))
                          if value is not None and given == value]
                emit("charisma", "given={} ability_scores={} original={} bonus={} boosted={} "
                     "matches={} distinct={}  {}".format(
                         plain(given), plain(current), plain(base), plain(extra), plain(boosted),
                         source, distinct, caller),
                     given=plain(given), ability_scores=plain(current), original=plain(base),
                     bonus=plain(extra), boosted=plain(boosted),
                     matches=source, distinct=distinct, by_mod=called_from_a_mod(), caller=caller)
        except Exception:
            ctx.log_exc("ability reads probe: recording the charisma failed")
        return result

    # ------------------------------------------------------------ 戦闘の開始の試し
    def combatants_state(app, people):
        state = {}
        for _side, name, person, _target in people:
            status = frames.attr(person, "status", None)
            state[name] = (plain(frames.attr(person, "current_hp", None)),
                           plain(sorted(status) if isinstance(status, dict) else status))
        return state

    def try_defense(side, name, person):
        method = getattr(type(person), "get_npc_defense", None)
        raw = patch.original_of(method) if callable(method) else None
        if not callable(raw):
            return
        scores = person.__dict__.get("ability_scores")
        original = person.__dict__.get("original_ability_scores")
        results = {"base": plain(raw(person))}
        for label, attr, table in (("scores", "ability_scores", scores),
                                   ("original", "original_ability_scores", original)):
            replaced = bumped(table, ABILITIES)
            if replaced is None:
                results[label] = None
                continue
            with Swapped(person, attr, replaced):
                results[label] = plain(raw(person))
        reads = [label for label in ("scores", "original")
                 if results.get(label) is not None and results[label] != results["base"]]
        emit("try_defense", "{} {} lv={} base={} scores+{}={} original+{}={} reads={} "
             "expect(scores)={} expect(original)={}".format(
                 side, name, plain(person.__dict__.get("experience_level")), results["base"],
                 BUMP, results["scores"], BUMP, results["original"], reads,
                 average_times_rate(scores, person), average_times_rate(original, person)),
             side=side, actor=name, level=plain(person.__dict__.get("experience_level")),
             results=results, reads=reads, bump=BUMP,
             expect_scores=average_times_rate(scores, person),
             expect_original=average_times_rate(original, person),
             scores=plain(scores), original=plain(original))

    def try_power(manager, calculate, side, name, person, target, rng):
        for category, power_attr, ability in PLANS:
            action = {"actor": person, "narration": "", "vfx": "slash",
                      "instant_damage": [{"target": target, "category": category,
                                          "power": "normal", "multiplier": 1}],
                      "instant_heal": [], "text_status": [], "escape_from_battle": [],
                      "other_action": []}

            def raw_of():
                rng_restore(rng)
                got = calculate(manager, action)
                first = got[0] if isinstance(got, (list, tuple)) and got else None
                return plain(first.get(target)) if isinstance(first, dict) else plain(got)

            power = _number(person.__dict__.get(power_attr))
            results = {"base": raw_of(), "base_again": raw_of()}
            if power is not None:
                with Swapped(person, power_attr, power * POWER_RATE):
                    results["power"] = raw_of()
            for label, attr in (("scores", "ability_scores"),
                                ("original", "original_ability_scores")):
                replaced = bumped(person.__dict__.get(attr), (ability,))
                if replaced is None:
                    results[label] = None
                    continue
                with Swapped(person, attr, replaced):
                    results[label] = raw_of()
            noisy = results["base"] != results["base_again"]
            reads = [] if noisy else [
                label for label in ("power", "scores", "original")
                if results.get(label) is not None and results[label] != results["base"]]
            emit("try_power", "{} {} lv={} {} {}={} base={} {}x{}={} {}+{}={} original+{}={} "
                 "reads={}{}".format(
                     side, name, plain(person.__dict__.get("experience_level")), category,
                     power_attr, plain(power), results["base"], power_attr, POWER_RATE,
                     results.get("power"), ability, BUMP, results["scores"], BUMP,
                     results["original"], reads, " NOISY" if noisy else ""),
                 side=side, actor=name, level=plain(person.__dict__.get("experience_level")),
                 category=category, power_attr=power_attr, power=plain(power), ability=ability,
                 results=results, reads=reads, noisy=noisy, rate=POWER_RATE, bump=BUMP)

    def try_all(app):
        manager_cls = ui.cls_of("BattlePhaseManager")
        enemies = frames.attr(app, "current_enemy_dict", None)
        if manager_cls is None or not isinstance(enemies, dict) or not enemies:
            return
        player = frames.attr(app, "player", None)
        allies = [player] + [ui.character_of(app, m) for m in ui.party_member_ids(app)]
        allies = [c for c in allies if c is not None]
        first_enemy = str(next(iter(enemies)))
        player_name = str(frames.attr(player, "name", ""))
        people = [("ally", str(frames.attr(c, "name", "?")), c, first_enemy) for c in allies]
        enemy_key, enemy = next(iter(enemies.items()))
        people.append(("enemy", str(enemy_key), enemy, player_name))
        fresh = []
        for side, name, person, target in people:
            key = (side, name, plain(frames.attr(person, "experience_level", None)))
            if key not in tried and isinstance(getattr(person, "__dict__", None), dict):
                fresh.append((key, side, name, person, target))
        if not fresh:
            return
        manager = manager_cls.__new__(manager_cls)
        manager.app = app
        calculate = patch.original_of(getattr(manager_cls, "calculate_battle_effect"))
        before = combatants_state(app, people)
        rng = rng_saved()
        marked_before = getattr(sys, DRY_THREAD_MARK, None)
        setattr(sys, DRY_THREAD_MARK, threading.get_ident())
        try:
            for key, side, name, person, target in fresh:
                tried.add(key)
                try_defense(side, name, person)
                try_power(manager, calculate, side, name, person, target, rng)
        finally:
            try:
                if marked_before is None:
                    delattr(sys, DRY_THREAD_MARK)
                else:
                    setattr(sys, DRY_THREAD_MARK, marked_before)
            except AttributeError:
                pass
            rng_restore(rng)
        after = combatants_state(app, people)
        if after != before:
            try_state["stopped"] = True
            emit("try_abort", "the battle changed across the tries; no more tries",
                 changed={k: [before.get(k), v] for k, v in after.items() if before.get(k) != v})
            ctx.log("ability reads probe: the tries changed the battle; stopped", level="WARN")

    @ctx.wrap(BATTLE_START, required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if TRY_AT_BATTLE and not try_state["stopped"]:
            try:
                try_all(getattr(self, "app", None) or app_of())
            except Exception:
                try_state["stopped"] = True
                ctx.log_exc("ability reads probe: the tries failed; no more tries")
        return result

    ctx.log("ability reads probe: installed (samples={}, try={}, watched={})".format(
        SAMPLES, TRY_AT_BATTLE, store.get("installed")))
