# -*- coding: utf-8 -*-
r"""計測: 医療施設の治療。ゲームは変えない。

治療の期間を変え、人体改造を足す MOD の下調べ。分かっていないのは次のとおり。

    1. 治療で `body_parts` の `injury` がいくつ戻るか（部位ごと・段階ごと）
    2. 体力（`physical_integrity`）・その上限・`exhausted`・戦闘の HP が戻るか
    3. 進む日数（文言は「3ヵ月入院」）と、`elapse_days` が何回・いくつで呼ばれるか
    4. 料金（`treatment_price`）がいつ決まり、何で変わるか
    5. 治療の間にゲームがセーブするか、人生の記録に何が足されるか

録り方は `247_probe_labor` と同じ。マネージャの `execute` を窓にして、
窓の前後の様子と、窓の間の `elapse_days`・`save_game`・文言・次に並ぶボタンを1行にまとめる。

    out\medical.log      読む用
    out\medical.jsonl    1窓＝1行。後から数える用
"""
import datetime
import sys
import threading
import time
import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "medical.log"
RECORD_BASENAME = "medical.jsonl"

#: 窓の間に写す文言の上限。超えた数も残す。
TEXT_LIMIT = 30

#: 窓を開けるマネージャ（`__init__` の並びはリコンの一覧）。
#:   DisplayMedicalTreatmentChoice(app)
#:   MedicalTreatmentManager(app, treatment_price)
MANAGERS = ("DisplayMedicalTreatmentChoice", "MedicalTreatmentManager")

#: 段の中の道。入ったか・何を受け取ったか・何を返したかだけを録る。
INNER = (("__main__:MedicalTreatmentManager.medical_treatment", "medical_treatment"),
         ("__main__:DisplayMedicalTreatmentChoice.update_button_display", "update_button_display"),
         ("__main__:InstantaleApp.check_body_damage", "check_body_damage"),
         ("scripts.characters:Character.update_max_physical_integrity", "update_max_physical_integrity"),
         ("scripts.functions:get_max_physical_integrity", "get_max_physical_integrity"))

#: 主人公から写す数。
PLAYER_NUMBERS = ("physical_integrity", "max_physical_integrity", "original_max_physical_integrity",
                  "current_hp", "max_hp", "original_max_hp", "experience_level", "age")

#: 開いている窓の並びと `__init__` の引数の控え。注入し直しで消えないよう `sys` に置く。
STORE_ATTR = "_instantale_probe_medical_store"


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


def body_brief(body_parts):
    """`{部位: [injury, stage]}`。辞書でなければ空。"""
    brief = {}
    if isinstance(body_parts, dict):
        for part, entry in body_parts.items():
            if isinstance(entry, dict):
                brief[str(part)] = [entry.get("injury"), entry.get("stage")]
    return brief


def numbers_of(holder, names=PLAYER_NUMBERS):
    """`holder` の属性のうち、数のものだけ。"""
    found = {}
    for name in names:
        value = getattr(holder, name, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found[name] = value
    return found


def log_tail(holder, limit=2):
    """人生の記録の件数と、末尾 `limit` 件の短い写し。読めなければ `(None, [])`。"""
    life_log = getattr(holder, "life_log", None)
    if not isinstance(life_log, (list, tuple)):
        return None, []
    return len(life_log), [frames.repr_value(entry) for entry in life_log[-limit:]]


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
        """暦・所持金・主人公の怪我と体力・仲間の体力。読めないものは None。"""
        player = getattr(app, "player", None) if app is not None else None
        count, tail = log_tail(player)
        snap = {
            "day": ui.game_day(app),
            "gold": ui.gold_of(app),
            "area": frames.repr_value(getattr(ui.current_area(app), "name", None)),
            "player": numbers_of(player),
            "exhausted": getattr(player, "exhausted", None),
            "body": body_brief(getattr(player, "body_parts", None)),
            "abilities": dict(getattr(player, "ability_scores", None) or {})
            if isinstance(getattr(player, "ability_scores", None), dict) else None,
            "life_log": count,
            "life_log_tail": tail,
            "party": [str(member) for member in ui.party_member_ids(app)],
        }
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
                    ctx.log_exc("medical probe: cannot record before the window")
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
                    ctx.log_exc("medical probe: cannot record the window")

    for name in MANAGERS:
        install_windows(name)

    # ------------------------------------------------------------ 段の中の道
    # 窓の外（依頼の出来事の怪我など）では録らない。行数を増やさないため。
    def install_inner(target, head):
        @ctx.wrap(target, required=False, safe=True)
        def inner(orig, *args, **kwargs):
            watching = bool(state["windows"])
            if watching:
                try:
                    shown = args[1:] if not target.startswith("scripts.functions:") else args
                    write("    -> {}(args={} kwargs={}) from {}".format(
                        head, frames.repr_value(shown), frames.repr_value(kwargs), frames.caller()))
                except Exception:
                    pass
            result = orig(*args, **kwargs)
            if watching:
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

    # ------------------------------------------- 日数送り・セーブ・文言（窓の中だけ）
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

    ctx.log("medical probe: ready ({}, {})".format(LOG_BASENAME, RECORD_BASENAME))
