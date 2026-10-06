# -*- coding: utf-8 -*-
r"""計測: ゲームの「〜の最中」の旗 7 つ。ゲームは変えない。

`ui.BUSY_FLAGS` と `in_shopping` の旗が、名前のとおりに立って下りるかを録る。
分かっているのは戦闘の 3 つだけで（GAME.md §2.10・`107_fix_battle_flag_stuck`）、
`in_shopping` は「店を出ても下りない」という結果しか無く、誰が立てて誰が下ろすのかは測っていない。
会話・自由入力・会話中の行動の 3 つは、下ろし忘れがあるかも測っていない。

ゲームは Nuitka ビルドでコードを読めない（TECH.md §1.1）。
旗はゲームのインスタンス属性（`vars(app)` に並ぶ。Kivy のプロパティではない）なので、
`InstantaleApp` のクラスに旗ごとのデータデスクリプタを置き、読み書きを途中で拾う。
値は今までどおりインスタンスの `__dict__` に入り、ゲームから見た動きは変わらない。

録るもの

    書き込み  値が変わったときは毎回1行。旧値 → 新値・スレッド・ゲーム側の呼び出し元の連鎖・
              居場所・売買の窓が開いているか・他の旗。同じ値の書き直しは呼び出し元ごとに初回だけ
    読み取り  旗ごと・読んだ関数ごとに初回だけ1行（ゲームの分岐が旗を読んでいるかを見る。
              ローダと MOD の読みも出す）
    ロード    `load_game_new` / `start_game` の後の旗（セーブから戻った値）
    保存      `save_game` の前の旗（セーブに焼かれる値）と並んでいる選択肢のクラス、
              見張りを通らずに変わった旗（ゲームが `__dict__` を直に書いていれば、ここで分かる）

    out\game_flags.log      読む用
    out\game_flags.jsonl    1件＝1行。後から数える用

見張りは1プロセスに1組（`sys` の `STORE`）。注入し直しても積み上がらず、書き手（`sink`）だけ差し替わる。
"""
import datetime
import sys
import threading

from instantale_modloader import frames, ui

LOG_BASENAME = "game_flags.log"
RECORD_BASENAME = "game_flags.jsonl"

#: 見る旗。`ui.BUSY_FLAGS` の 6 つと `in_shopping`。
FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
         "in_conversation", "in_free_input", "in_action_in_conversation",
         "in_shopping")

#: 見張りを置くクラス。
APP_CLASS = "InstantaleApp"

#: 見張りの置き場（`sys` の属性名。注入し直しても同じ物を使う）。
STORE = "__instantale_probe_game_flags__"

#: 見張りの作り。中身を変えたら上げる（古い見張りを置き直す）。
WATCH_VERSION = 1

#: 呼び出し元の連鎖を何段まで書くか。
CALLER_DEPTH = 6

#: 保存とロードの行に写す選択肢の数の上限。
SCREEN_LIMIT = 12

#: ロードと保存の窓。
LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",
                "__main__:InstantaleApp.start_game")
SAVE_TARGET = "__main__:InstantaleApp.save_game"

_MISSING = object()


def _store():
    store = getattr(sys, STORE, None)
    if not isinstance(store, dict):
        store = {"sink": None, "reader": None, "version": None, "installed": []}
        setattr(sys, STORE, store)
    return store


def _make_watch(store):
    """旗1つの見張り（データデスクリプタ）のクラス。

    値はインスタンスの `__dict__` に置く（ゲームが今まで置いていた場所）。
    インスタンスに無ければ、置く前にクラスが持っていた値を返す（無ければ AttributeError）。
    書き手・読み手の例外はゲームへ流さない。書き込みは記録より先に済ませる。
    """
    class FlagWatch(object):
        def __init__(self, name, fallback):
            self.name = name
            self.fallback = fallback
            self.version = WATCH_VERSION

        def __get__(self, obj, owner=None):
            if obj is None:
                return self
            try:
                value = obj.__dict__[self.name]
            except KeyError:
                if self.fallback is _MISSING:
                    raise AttributeError(self.name)
                value = self.fallback
            reader = store.get("reader")
            if reader is not None:
                try:
                    reader(self.name, sys._getframe(1), value)
                except Exception:
                    pass
            return value

        def __set__(self, obj, value):
            old = obj.__dict__.get(self.name, _MISSING)
            obj.__dict__[self.name] = value
            sink = store.get("sink")
            if sink is not None:
                try:
                    sink(obj, self.name, old, value)
                except Exception:
                    pass

        def __delete__(self, obj):
            obj.__dict__.pop(self.name, None)

    return FlagWatch


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    store = _store()
    # 読み手と書き直しの初回の印。1プロセスで1組（遅れた当て直しで同じ読み手を二度書かない）。
    seen = store.setdefault("seen", {"reads": set(), "rewrites": set()})
    # 見張りを通った最後の値。保存の前に `__dict__` と比べる。
    known = {}

    def now():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def show(value):
        if value is _MISSING:
            return "<unset>"
        return frames.short(frames.repr_value(value), 60)

    def where(app):
        """居場所（土地・施設の種類と名前）。読めなければ空。"""
        out = {}
        try:
            player = getattr(app, "player", None)
            location = getattr(player, "location", None)
            out["area"] = frames.short(frames.repr_value(
                getattr(ui.current_area(app), "name", None)), 30)
            out["type"] = ui.facility_type_of(location)
            out["place"] = frames.short(frames.repr_value(
                getattr(location, "name", location)), 30)
        except Exception:
            pass
        return out

    def flags_of(app):
        state = {}
        for name in FLAGS:
            value = vars(app).get(name, _MISSING)
            if value is not _MISSING and value:
                state[name] = show(value)
        return state

    def site_of(frame):
        """読んだ関数。ゲームのものは持ち主のクラスまで、ローダと MOD のものは印を付ける。"""
        code = frame.f_code
        filename = code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
        if frames.is_ours(code.co_filename):
            return "ours {} ({}:{})".format(code.co_name, filename, frame.f_lineno)
        return "{} ({}:{})".format(frames.owner_of(code) or code.co_name,
                                   filename, frame.f_lineno)

    def sink(app, name, old, new):
        known[name] = new
        try:
            changed = old is _MISSING or old != new
        except Exception:
            changed = old is not new
        caller = frames.caller(depth=CALLER_DEPTH)
        if not changed:
            # 書き直しは直近の呼び出し元で数える（外側の連鎖まで比べると同じ場所が何度も出る）。
            key = (name, show(new), caller.split(" <- ", 1)[0])
            if key in seen["rewrites"]:
                return
            seen["rewrites"].add(key)
        entry = {
            "at": now(), "kind": "set" if changed else "rewrite", "flag": name,
            "old": show(old), "new": show(new),
            "thread": threading.current_thread().name,
            "caller": caller, "where": where(app),
            "popup": bool(vars(app).get("is_popup_window_opened")),
            "flags": flags_of(app),
        }
        record(entry)
        write("{} {} {} -> {}  [{}] {}  @{}{}".format(
            entry["kind"], name, entry["old"], entry["new"], entry["thread"], caller,
            "/".join(str(v) for v in entry["where"].values() if v),
            "  popup" if entry["popup"] else ""))

    def reader(name, frame, value):
        key = (name, frame.f_code)
        if key in seen["reads"]:
            return
        seen["reads"].add(key)
        site = site_of(frame)
        record({"at": now(), "kind": "read", "flag": name, "site": site,
                "value": show(value), "thread": threading.current_thread().name})
        write("read {} by {} (value {})".format(name, site, show(value)))

    def install():
        """見張りを置く（置いてあれば書き手だけ差し替える）。置けた旗の数を返す。"""
        main = sys.modules.get("__main__")
        cls = getattr(main, APP_CLASS, None) if main is not None else None
        if not isinstance(cls, type):
            write("WARN {} is not in __main__ yet; the flags are not watched".format(APP_CLASS))
            return 0
        if store.get("version") != WATCH_VERSION:
            watch = _make_watch(store)
            installed = []
            for name in FLAGS:
                previous = vars(cls).get(name, _MISSING)
                # 古い版の見張りなら、その前に在った値を引き継ぐ。
                fallback = getattr(previous, "fallback", previous) \
                    if type(previous).__name__ == "FlagWatch" else previous
                setattr(cls, name, watch(name, fallback))
                installed.append(name)
            store["version"] = WATCH_VERSION
            store["installed"] = installed
            write("watching {} on {} (version {})".format(
                ", ".join(installed), APP_CLASS, WATCH_VERSION))
        store["sink"] = sink
        store["reader"] = reader
        app = ui.find_app()
        if app is not None:
            for name in FLAGS:
                value = vars(app).get(name, _MISSING)
                if value is not _MISSING:
                    known[name] = value
            write("now {}".format(flags_of(app) or "{}"))
        return len(store["installed"])

    install()

    def screen_of(app):
        """並んでいる選択肢のクラス名（重複は1つ）。旗が場面と食い違っていないかを後から見るため。"""
        names = []
        for entry in (getattr(app, "buttons", None) or [])[:SCREEN_LIMIT]:
            name = ui.spec_cls_name(entry)
            if name and name not in names:
                names.append(name)
        return names

    def snapshot_line(kind, app, extra=None):
        entry = {"at": now(), "kind": kind, "flags": flags_of(app), "where": where(app),
                 "screen": screen_of(app)}
        if extra:
            entry.update(extra)
        record(entry)
        write("{} flags {}  @{}  screen {}{}".format(
            kind, entry["flags"] or "{}",
            "/".join(str(v) for v in entry["where"].values() if v),
            entry["screen"], "  {}".format(extra) if extra else ""))

    for target in LOAD_TARGETS:
        def make(target):
            @ctx.wrap(target, required=False, safe=True)
            def after_load(orig, self, *args, **kwargs):
                result = orig(self, *args, **kwargs)
                try:
                    snapshot_line("load", self, {"via": target.rsplit(".", 1)[-1]})
                except Exception:
                    ctx.log_exc("probe game flags: cannot record the load")
                return result
        make(target)

    @ctx.wrap(SAVE_TARGET, required=False, safe=True)
    def before_save(orig, self, *args, **kwargs):
        try:
            # 見張りを通らずに変わった旗（`__dict__` の直書き）。
            bypass = {}
            for name in FLAGS:
                if name not in known:
                    continue
                value = vars(self).get(name, _MISSING)
                if value is not known[name] and value != known[name]:
                    bypass[name] = "{} -> {}".format(show(known[name]), show(value))
            snapshot_line("save", self, {"bypass": bypass} if bypass else None)
        except Exception:
            ctx.log_exc("probe game flags: cannot record the save")
        return orig(self, *args, **kwargs)
