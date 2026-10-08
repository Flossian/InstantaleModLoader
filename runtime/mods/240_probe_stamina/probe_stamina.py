# -*- coding: utf-8 -*-
r"""計測: 体力（`physical_integrity`）がいつ・誰に・どれだけ減らされるか。ゲームは変えない。

##### 何を決めるための計測か

依頼のクリアで体力が減る量を、依頼の難易度と道中の被弾で変える MOD の下調べ。
セーブの控えを前後で比べると、1件のクリアで上限の半分（切り捨て）が引かれ、
依頼の難易度は効いていなかった（GAME.md §2.19）。
控えの比較では半分より多く減った回もあり、道中の何が体力を使ったのかは分からない。

| 問い | 見るところ |
|---|---|
| クリアで引く関数と、引く量の式（上限の半分か、今の値の半分か、0 で止まるか） | `set` 行の呼び出し元と前後の値 |
| 依頼の道中で体力を使うのは何か（休む・出来事・戦闘） | 依頼の最中の `set` 行の呼び出し元 |
| 差し替える口（引く前に何が読めるか） | `quest_end` 行の前の値（難易度・レベル・HP・部位の怪我・道中の被弾） |
| 被弾をどこから読むか | `quest_end` 行の `damage`（依頼の間に HP が減った合計と最低値）と部位の前後 |
| 同行者の体力も減るか | 同行者の `set` 行 |

ゲームは Nuitka ビルドでコードを読めない（TECH.md §1.1）。
体力と HP は `Character` のインスタンス属性なので、`239_probe_game_flags` と同じく
クラスに属性ごとのデータデスクリプタを置き、書き込みを途中で拾う。
値は今までどおりインスタンスの `__dict__` に入り、ゲームから見た動きは変わらない。

録るもの（1件＝1行）

    set         主人公か同行者の体力（`physical_integrity` / `max_physical_integrity`）か、
                疲労の旗（`exhausted`。立っていると依頼を受けられない）が変わった。
                誰か、旧値 → 新値と差、上限、レベル、依頼の最中か（依頼の id）、戦闘の最中か、
                呼び出し元の連鎖。生まれたときの初回の書き込み（旧値が無い）は書かない
    quest_start 依頼に出た直後。依頼の id・難易度・種類、主人公と同行者の体力・HP・部位の怪我
    quest_end   `QuestEndManager.execute` の前後。前の値には依頼の難易度・種類、レベル、
                依頼の間の被弾（HP が減った合計・回復した合計・最低値。主人公と同行者ごと）を添える
    quest_retire `QuestRetireManager.execute` の前後。中身は quest_end と同じ

    out\stamina.log     読む用
    out\stamina.jsonl   後から数える用

HP の書き込みは1行ずつは書かない。依頼の最中だけ、人ごとに減った合計と最低値を数える。
見張りは1プロセスに1組（`sys` の `STORE`）。注入し直しても積み上がらず、書き手（`sink`）だけ差し替わる。
読み取りだけ。値も乱数も state\ もセーブも動かさない。引数は受け取ったまま `orig` へ渡す。
"""
import datetime
import sys
import threading

from instantale_modloader import frames, ui

LOG_BASENAME = "stamina.log"
RECORD_BASENAME = "stamina.jsonl"

#: 見張る属性。体力の2つと疲労の旗は変わるたびに書く。HP は依頼の最中に数えるだけ。
STAMINA_ATTRS = ("physical_integrity", "max_physical_integrity", "exhausted")
HP_ATTR = "current_hp"
WATCHED = STAMINA_ATTRS + (HP_ATTR,)

#: 見張りを置くクラス。
CHARACTER_MODULE = "scripts.characters"
CHARACTER_CLASS = "Character"

#: 見張りの置き場（`sys` の属性名。注入し直しても同じ物を使う）。
STORE = "__instantale_probe_stamina__"

#: 見張りの作り。中身を変えたら上げる（古い見張りを置き直す）。
WATCH_VERSION = 2

#: 呼び出し元の連鎖を何段まで書くか。
CALLER_DEPTH = 8

#: 前後を比べる窓。`(対象, 名前)`。
WINDOWS = (
    ("__main__:QuestEndManager.execute", "quest_end"),
    ("__main__:QuestRetireManager.execute", "quest_retire"),
)

#: 依頼に出た地点。後に呼ばれた側の値で上書きされても困らない（最初の1回だけ取る）。
START_TARGETS = (
    "__main__:QuestStartManager.start_quest",
    "__main__:QuestStartManager.execute",
)


def _store():
    store = getattr(sys, STORE, None)
    if not isinstance(store, dict):
        store = {"sink": None, "version": None, "installed": []}
        setattr(sys, STORE, store)
    return store


#: 「値が無い」の番人。プロセスで1つ（239 と同じ理由。注入し直してもクラスの見張りは前の番人を握る）。
_MISSING = _store().setdefault("missing", object())


def _make_watch(store):
    """属性1つの見張り（データデスクリプタ）のクラス。

    値はインスタンスの `__dict__` に置く（ゲームが今まで置いていた場所）。
    インスタンスに無ければ、置く前にクラスが持っていた値を返す（無ければ AttributeError）。
    書き手の例外はゲームへ流さない。書き込みは記録より先に済ませる。
    """
    class StaminaWatch(object):
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

    return StaminaWatch


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    store = _store()
    #: 依頼の最中の被弾。`{依頼の id: {人: {"loss", "gain", "min", "writes"}}}`。
    damage = {}
    lock = threading.Lock()
    cached = {"app": None}

    def now():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def app_of():
        app = cached["app"]
        if app is None:
            app = ui.find_app()
            cached["app"] = app
        return app

    def who(app, obj):
        """主人公なら `player`、同行者ならその id、どちらでもなければ None。"""
        if obj is frames.attr(app, "player", None):
            return ui.PLAYER_ID
        member = obj.__dict__.get("id")
        if member is None:
            return None
        member = str(member)
        if member in [str(m) for m in ui.party_member_ids(app)]:
            return member
        return None

    def emit(kind, line, **fields):
        row = {"at": now(), "kind": kind, "thread": threading.current_thread().name}
        row.update(fields)
        record(row)
        write("{} {}".format(kind, line))

    def sink(obj, name, old, new):
        if ctx.superseded():
            return      # 外した・切って注入し直した後の古い書き手（見張りはクラスに残る）
        app = app_of()
        if app is None:
            return
        if name == HP_ATTR:
            quest_id = ui.current_quest_id(app)
            if quest_id is None:
                return
            person = who(app, obj)
            if person is None:
                return
            before, after = _number(old), _number(new)
            if before is None or after is None:
                return
            with lock:
                row = damage.setdefault(quest_id, {}).setdefault(
                    person, {"loss": 0, "gain": 0, "min": before, "writes": 0})
                if after < before:
                    row["loss"] += before - after
                elif after > before:
                    row["gain"] += after - before
                row["min"] = min(row["min"], after)
                row["writes"] += 1
            return
        person = who(app, obj)
        if person is None:
            return
        try:
            changed = old != new
        except Exception:
            changed = old is not new
        if not changed:
            return
        before, after = _number(old), _number(new)
        delta = (after - before) if before is not None and after is not None else None
        caller = frames.caller(depth=CALLER_DEPTH)
        fields = {
            "attr": name, "who": person, "name": (frames.attr(obj, "name", None) if person == ui.PLAYER_ID
                     else ui.character_name(app, person)),
            "old": frames.repr_value(old), "new": frames.repr_value(new), "delta": delta,
            "max": _number(obj.__dict__.get("max_physical_integrity")),
            "level": _number(obj.__dict__.get("experience_level")),
            "hp": _number(obj.__dict__.get(HP_ATTR)),
            "max_hp": _number(obj.__dict__.get("max_hp")),
            "quest": ui.current_quest_id(app),
            "in_battle": bool(vars(app).get("in_battle")),
            "caller": caller,
        }
        emit("set", "{} {}({}) {} -> {} ({}) max={} lv={} quest={} battle={}  {}".format(
            name, person, fields["name"], fields["old"], fields["new"],
            "{:+}".format(delta) if delta is not None else "?",
            fields["max"], fields["level"], fields["quest"], fields["in_battle"], caller),
            **fields)

    def install():
        """見張りを置く（置いてあれば書き手だけ差し替える）。置けた属性の数を返す。"""
        module = sys.modules.get(CHARACTER_MODULE)
        cls = getattr(module, CHARACTER_CLASS, None) if module is not None else None
        if not isinstance(cls, type):
            write("WARN {}.{} is not loaded yet; stamina is not watched".format(
                CHARACTER_MODULE, CHARACTER_CLASS))
            return 0
        if store.get("version") != WATCH_VERSION:
            watch = _make_watch(store)
            installed = []
            for name in WATCHED:
                previous = vars(cls).get(name, _MISSING)
                ours = type(previous).__name__ == "StaminaWatch"
                if not ours and previous is not _MISSING and hasattr(type(previous), "__get__"):
                    # クラスが自分で持つ property などは上書きしない（値の出どころが変わる）
                    write("WARN {} is a {} on the class; not watched".format(
                        name, type(previous).__name__))
                    continue
                # 古い版の見張りなら、その前に在った値を引き継ぐ。
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

    # ------------------------------------------------------------ 依頼の前後
    def body_of(character):
        """部位ごとの `(injury, stage)`。intact で injury が 0 の部位は省く。"""
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
            out[str(part)] = [injury, stage]
        return out

    def person_of(character):
        return {
            "pi": _number(frames.attr(character, "physical_integrity", None)),
            "max_pi": _number(frames.attr(character, "max_physical_integrity", None)),
            "hp": _number(frames.attr(character, HP_ATTR, None)),
            "max_hp": _number(frames.attr(character, "max_hp", None)),
            "level": _number(frames.attr(character, "experience_level", None)),
            "exhausted": frames.attr(character, "exhausted", None),
            "body": body_of(character),
        }

    def people(app):
        """主人公と同行者の今の値。"""
        out = {}
        player = frames.attr(app, "player", None)
        if player is not None:
            out[ui.PLAYER_ID] = person_of(player)
        characters = frames.attr(frames.attr(app, "world", None), "characters", None)
        for member in ui.party_member_ids(app):
            character = characters.get(member) if isinstance(characters, dict) else None
            if character is not None:
                out[str(member)] = person_of(character)
        return out

    def quest_of(app):
        quest = frames.attr(app, "current_quest_data", None)
        if quest is None:
            return {}
        title = ui.quest_value(quest, "quest_title")
        return {"id": ui.current_quest_id(app),
                "difficulty": ui.quest_value(quest, "difficulty"),
                "type": ui.quest_value(quest, "quest_type"),
                "title": frames.short(str(title), 40) if title is not None else None}

    def brief(state):
        return " ".join("{}:pi={}/{} hp={}/{} lv={}{}".format(
            key, v["pi"], v["max_pi"], v["hp"], v["max_hp"], v["level"],
            " body={}".format(v["body"]) if v["body"] else "")
            for key, v in state.items())

    started = set()

    for target in START_TARGETS:
        def make_start(target):
            @ctx.wrap(target, required=False, safe=True)
            def after_start(orig, self, *args, **kwargs):
                result = orig(self, *args, **kwargs)
                try:
                    app = frames.attr(self, "app", None) or app_of()
                    quest = quest_of(app)
                    if quest.get("id") is not None and quest["id"] not in started:
                        started.add(quest["id"])
                        with lock:
                            damage.pop(quest["id"], None)
                        state = people(app)
                        emit("quest_start", "quest={} via={}  {}".format(
                            quest, target.rsplit(".", 1)[-1], brief(state)),
                            quest=quest, via=target, people=state)
                except Exception:
                    ctx.log_exc("stamina probe: cannot record the quest start")
                return result
        make_start(target)

    for target, name in WINDOWS:
        def make_window(target, name):
            @ctx.wrap(target, required=False, safe=True)
            def window(orig, self, *args, **kwargs):
                app, before, quest, hits = None, None, {}, {}
                try:
                    app = frames.attr(self, "app", None) or app_of()
                    quest = quest_of(app)
                    before = people(app)
                    with lock:
                        hits = dict(damage.get(quest.get("id"), {}))
                except Exception:
                    ctx.log_exc("stamina probe: cannot read before {}".format(name))
                try:
                    return orig(self, *args, **kwargs)
                finally:
                    try:
                        if app is not None and before is not None:
                            after = people(app)
                            emit(name, "quest={} damage={}\n  before {}\n  after  {}".format(
                                quest, hits, brief(before), brief(after)),
                                quest=quest, damage=hits, before=before, after=after)
                            started.discard(quest.get("id"))
                    except Exception:
                        ctx.log_exc("stamina probe: cannot read after {}".format(name))
        make_window(target, name)
