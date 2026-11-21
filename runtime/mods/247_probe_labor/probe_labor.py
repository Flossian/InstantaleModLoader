# -*- coding: utf-8 -*-
r"""計測: 役場の労働と裏の事務所の闇労働。ゲームは変えない。

労働に職種・出来事・勤続を足す MOD の下調べ。分かっていないのは次のとおり。

    1. `get_labor_reward` に渡る難易度の出どころと、返った額が選択肢の額になるまでの掛け算
    2. 満了の割増（16ヵ月 1.125 倍・32ヵ月 1.25 倍と逆算した）が入る時点と、途中でやめたときの扱い
    3. `LaborPhaseManager` の引数（`action_months` / `remaining_count` / `reward_per_action` /
       `day_labor_start` / `total_reward`）を変えたときに、暦と所持金がその通りに動くか
    4. 闇労働の1期の額（`reward_per_term`）の出どころ
    5. `labor_choice_generator` / `labor_narration_generator` / `illegal_labor_choice_generator` が
       呼ばれるか、呼ばれるなら何を返すか
    6. 労働の間にゲームがセーブするか

録り方は `238_probe_prison` と同じ。マネージャの `execute` を窓にして、
窓の前後の様子と、窓の間の `elapse_days`・`save_game`・文言・次に並ぶボタンを1行にまとめる。

    out\labor.log      読む用
    out\labor.jsonl    1窓＝1行。後から数える用
"""
import datetime
import sys
import threading
import time
import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "labor.log"
RECORD_BASENAME = "labor.jsonl"

#: 窓の間に写す文言の上限。超えた数も残す。
TEXT_LIMIT = 30

#: 窓を開けるマネージャ（`__init__` の並びはリコンの一覧）。
#:   DisplayLongTermLaborChoice(app) / DisplayIllegalLaborChoice(app)
#:   LaborChoiceManager(app, action_months, action_count, reward_per_action)
#:   LaborStartManager(app, action_months, action_count, reward_per_action)
#:   LaborPhaseManager(app, action_months, remaining_count, reward_per_action, day_labor_start, total_reward)
#:   LaborCancelManager(app, day_labor_start)
#:   IllegalLaborStartManager(app, term_count, term_months, reward_per_term)
#:   IllegalLaborPhaseManager(app, term_months, term_count, reward_per_term, remaining_term, total_reward, day_labor_start)
MANAGERS = ("DisplayLongTermLaborChoice", "LaborChoiceManager", "LaborStartManager",
            "LaborPhaseManager", "LaborCancelManager",
            "DisplayIllegalLaborChoice", "IllegalLaborStartManager", "IllegalLaborPhaseManager")

#: 段の中の道。入ったか・何を受け取ったか・何を返したかだけを録る。
INNER = (("__main__:LaborChoiceManager.labor_acceptance_choice", "labor_acceptance_choice"),
         ("__main__:LaborStartManager.labor_start", "labor_start"),
         ("__main__:LaborPhaseManager.labor", "labor"),
         ("__main__:LaborCancelManager.labor_cancel", "labor_cancel"),
         ("__main__:IllegalLaborStartManager.labor_start", "illegal_labor_start"),
         ("__main__:IllegalLaborPhaseManager.labor", "illegal_labor"),
         ("scripts.functions:get_labor_reward", "get_labor_reward"),
         ("scripts.llm.llm_manager:labor_choice_generator", "labor_choice_generator"),
         ("scripts.llm.llm_manager:labor_narration_generator", "labor_narration_generator"),
         ("scripts.llm.llm_manager:illegal_labor_choice_generator", "illegal_labor_choice_generator"))

#: 開いている窓の並びと `__init__` の引数の控え。注入し直しで消えないよう `sys` に置く。
STORE_ATTR = "_instantale_probe_labor_store"


def process_store():
    """`{"windows": [...], "inits": WeakKeyDictionary, "inits_by_id": {...}}`。プロセスに1つ。"""
    store = getattr(sys, STORE_ATTR, None)
    if not (isinstance(store, dict) and isinstance(store.get("windows"), list)
            and isinstance(store.get("inits"), weakref.WeakKeyDictionary)
            and isinstance(store.get("inits_by_id"), dict)):
        store = {"windows": [], "inits": weakref.WeakKeyDictionary(), "inits_by_id": {}}
        setattr(sys, STORE_ATTR, store)
    return store


def close_window(windows, window):
    """`window` そのもの（`is`）を並びから外す。中身の等しい別の窓は外さない。"""
    for index in range(len(windows) - 1, -1, -1):
        if windows[index] is window:
            del windows[index]
            return True
    return False


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    state = process_store()
    inits = state["inits"]
    inits_by_id = state["inits_by_id"]

    def remember_init(manager, init_args):
        try:
            inits[manager] = init_args
        except TypeError:
            inits_by_id[id(manager)] = init_args
            while len(inits_by_id) > 8:
                inits_by_id.pop(next(iter(inits_by_id)))

    def init_args_of(manager):
        try:
            found = inits.get(manager)
        except TypeError:
            found = None
        return found if found is not None else inits_by_id.get(id(manager))

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def snapshot(app):
        """暦・年齢・所持金・居場所・仲間。読めないものは None。"""
        player = getattr(app, "player", None) if app is not None else None
        location = getattr(player, "location", None)
        snap = {
            "day": ui.game_day(app),
            "age": frames.repr_value(getattr(player, "age", None)),
            "gold": ui.gold_of(app),
            "area": frames.repr_value(getattr(ui.current_area(app), "name", None)),
            "location": frames.repr_value(getattr(location, "name", location)),
            "party": [str(member) for member in ui.party_member_ids(app)],
        }
        for name in ("exp", "experience_point", "level", "stamina"):
            value = getattr(player, name, None)
            if isinstance(value, (int, float)):
                snap[name] = value
        return snap

    def buttons_brief(app, limit=16):
        entries = []
        buttons = getattr(app, "buttons", None) if app is not None else None
        if isinstance(buttons, (list, tuple)):
            for entry in buttons[:limit]:
                entries.append({"text": (entry or {}).get("text")
                                if isinstance(entry, dict) else repr(entry),
                                "cls": ui.spec_cls_name(entry),
                                "args": ui.spec_args(entry)})
        return entries

    # ------------------------------------------------------------ 各段の窓
    def install_windows(cls_name):
        @ctx.wrap("__main__:{}.__init__".format(cls_name), required=False, safe=True)
        def manager_init(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                init_args = [frames.repr_value(a) for a in args[1:]]
                remember_init(self, init_args)
                write("-" * 72)
                write("{}.__init__(args={} kwargs={}) from {}".format(
                    cls_name, init_args, frames.repr_value(kwargs), frames.caller()))
            except Exception:
                pass
            return result

        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False, safe=True)
        def manager_execute(orig, self, *args, **kwargs):
            choice_text = args[0] if args else kwargs.get("choice_text")
            app = getattr(self, "app", None) or ui.find_app()
            # 窓はスレッドをまたいで数える。別のスレッドの保存なども混ざるので、どのスレッドから来たかを残す。
            window = {"cls": cls_name, "texts": [], "days": [], "saves": 0,
                      "dots": 0, "overflow": 0, "inner": [],
                      "thread": threading.current_thread().name, "day_threads": [], "save_threads": []}
            before = None
            started = time.monotonic()
            state["windows"].append(window)
            try:
                try:
                    before = snapshot(app)
                    write("{}.execute: choice={!r} init_args={}".format(
                        cls_name, choice_text, init_args_of(self)))
                    write("    before {}".format(before))
                except Exception:
                    ctx.log_exc("labor probe: cannot record before the window")
                return orig(self, *args, **kwargs)
            finally:
                try:
                    close_window(state["windows"], window)
                except Exception:
                    pass
                try:
                    after = snapshot(app)
                    row = {
                        "at": now(), "phase": "execute", "cls": cls_name,
                        "choice_text": choice_text, "init_args": init_args_of(self),
                        "before": before, "after": after,
                        "elapse_days_calls": window["days"],
                        "save_game_calls": window["saves"],
                        "window_thread": window["thread"],
                        "elapse_days_threads": window["day_threads"],
                        "save_game_threads": window["save_threads"],
                        "texts": window["texts"], "texts_dropped": window["overflow"],
                        "loading_dots": window["dots"], "inner": window["inner"],
                        "buttons_after": buttons_brief(app),
                        "seconds": round(time.monotonic() - started, 1),
                    }
                    write("{} done in {}s: elapse_days={} save_game={} texts={} dots={}".format(
                        cls_name, row["seconds"], window["days"], window["saves"],
                        len(window["texts"]), window["dots"]))
                    write("    after  {}".format(after))
                    for text in window["texts"]:
                        write("    text: {!r}".format(text))
                    for entry in row["buttons_after"]:
                        write("    next button: {!r} cls={} args={}".format(
                            entry["text"], entry["cls"], entry["args"]))
                    record(row)
                except Exception:
                    ctx.log_exc("labor probe: cannot record the window")

    for name in MANAGERS:
        install_windows(name)

    # ------------------------------------------------------------ 段の中の道
    def install_inner(target, head):
        @ctx.wrap(target, required=False, safe=True)
        def inner(orig, *args, **kwargs):
            try:
                shown = args[1:] if target.startswith("__main__:") else args
                write("    -> {}(args={} kwargs={}) from {}".format(
                    head, frames.repr_value(shown), frames.repr_value(kwargs), frames.caller()))
            except Exception:
                pass
            result = orig(*args, **kwargs)
            try:
                write("    <- {} returned {}".format(head, frames.repr_value(result)))
                if state["windows"]:
                    state["windows"][-1]["inner"].append(
                        "{} -> {}".format(head, frames.repr_value(result)))
            except Exception:
                pass
            return result

    for target, head in INNER:
        install_inner(target, head)

    # ------------------------------------------- 日数送り・セーブ・文言（窓の内外）
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        try:
            if state["windows"]:
                days = args[0] if args else kwargs.get("days")
                state["windows"][-1]["days"].append(frames.repr_value(days))
                state["windows"][-1]["day_threads"].append(threading.current_thread().name)
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.save_game", required=False, safe=True)
    def save_game(orig, self, *args, **kwargs):
        # 別スレッドから呼ばれることがある。数えるだけで何も読まない。
        try:
            if state["windows"]:
                state["windows"][-1]["saves"] += 1
                state["windows"][-1]["save_threads"].append(threading.current_thread().name)
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, *args, **kwargs):
        context = (args[0] if args else kwargs.get("context")) if state["windows"] else None
        if isinstance(context, str):
            try:
                window = state["windows"][-1]
                if context.strip() and not context.strip(".。 　"):
                    window["dots"] += 1
                elif len(window["texts"]) < TEXT_LIMIT:
                    window["texts"].append(context)
                else:
                    window["overflow"] += 1
            except Exception:
                pass
        return orig(self, *args, **kwargs)

    ctx.log("labor probe: ready ({}, {})".format(LOG_BASENAME, RECORD_BASENAME))
