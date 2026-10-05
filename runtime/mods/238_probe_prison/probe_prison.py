# -*- coding: utf-8 -*-
r"""計測: 逮捕・裁判・服役・釈放。ゲームは変えない。

`336_crime_overhaul` の脱獄（服役中の毎年の画面に備えと決行を足す）の下調べ。
分かっていないのは次のとおりで、GAME.md にも検証記録にも数字が無い。

    1. 捕まる経路（衛兵の「大人しく捕まる」と自由行動の `arrest_player`）が
       どこから `TrialStartManager` を作るか、渡す罪状と出来事
    2. 裁判の求刑・判決の値（`TrialPhaseManager(app, sentence_sought_dict, ...)`）と、
       懲役の年数の出どころ
    3. 服役の1年で暦・年齢がどう進むか、どの段で進むか
       （`ImprisonmentPhaseManager(app, remaining_years, charges, incident_details,
       imprisonment_years)`）
    4. 捕まった後と釈放の後の、居場所・仲間・所持金・持ち物・装備・手配度
       （釈放の文「かつての所持品を投げてよこした」が、本当に取り上げていたのかを含む）
    5. 服役中にゲームがセーブするか（`save_game` が段の中で呼ばれるか）

録り方は `231_probe_training` と同じ。マネージャの `execute` を窓にして、
窓の前後の様子と、窓の間の `elapse_days`・`save_game`・文言・次に並ぶボタンを1行にまとめる。

    out\prison.log      読む用
    out\prison.jsonl    1窓＝1行。後から数える用
"""
import datetime
import time
import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "prison.log"
RECORD_BASENAME = "prison.jsonl"

#: 窓の間に写す文言の上限。超えた数も残す。
TEXT_LIMIT = 30

#: 窓を開けるマネージャ（`__init__` の並びはゲームの中で `inspect.signature` で見た）。
#:   TrialStartManager(app, charges, incident_details)
#:   TrialPhaseManager(app, sentence_sought_dict, charges, incident_details)
#:   ImprisonmentStartManager(app, imprisonment_years, charges, incident_details)
#:   ImprisonmentPhaseManager(app, remaining_years, charges, incident_details, imprisonment_years)
#:   ImprisonmentEndManager(app, imprisonment_years)
#:   DieFromOldAgePrison(app) / ExecutionPhaseManager(app)
#:   IllegalLaborStartManager / IllegalLaborPhaseManager（exe の文で裁判の近くに並ぶ）
MANAGERS = ("TrialStartManager", "TrialPhaseManager",
            "ImprisonmentStartManager", "ImprisonmentPhaseManager",
            "ImprisonmentEndManager", "DieFromOldAgePrison", "ExecutionPhaseManager",
            "IllegalLaborStartManager", "IllegalLaborPhaseManager")

#: 段の中の道。入ったか・何を受け取ったか・何を返したかだけを録る。
INNER = (("TrialStartManager", "trial_start"),
         ("TrialPhaseManager", "get_sentence"),
         ("ImprisonmentStartManager", "start_imprisonment"),
         ("ImprisonmentPhaseManager", "serve_sentence"),
         ("ImprisonmentEndManager", "release"),
         ("DieFromOldAgePrison", "method"),
         ("ExecutionPhaseManager", "execution"),
         ("IllegalLaborStartManager", "labor_start"),
         ("IllegalLaborPhaseManager", "labor"))

#: 持ち物・装備の候補の属性名（どれがあるかは決め打ちしない）。
CARRY_ATTRS = ("inventory", "items", "equipments", "equipment")


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    state = {"windows": []}
    inits = weakref.WeakKeyDictionary()
    inits_by_id = {}

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

    def size_of(value):
        try:
            return len(value)
        except TypeError:
            return None

    def snapshot(app):
        """居場所・仲間・所持金・持ち物・手配度。読めないものは None。"""
        player = getattr(app, "player", None) if app is not None else None
        location = getattr(player, "location", None)
        snap = {
            "day": ui.game_day(app),
            "age": frames.repr_value(getattr(player, "age", None)),
            "gold": ui.gold_of(app),
            "area": frames.repr_value(getattr(ui.current_area(app), "name", None)),
            "location": frames.repr_value(getattr(location, "name", location)),
            "party": [str(member) for member in ui.party_member_ids(app)],
            "lawfulness": {},
            "carry": {},
            "flags": {},
        }
        try:
            snap["lawfulness"] = {str(k): v for k, v in ui.lawfulness_by_area(player).items()
                                  if v != 10}
        except Exception:
            pass
        for name in CARRY_ATTRS:
            value = getattr(player, name, None)
            if value is not None:
                snap["carry"][name] = size_of(value)
        for name in ("in_battle", "in_conversation", "in_free_input", "in_shopping",
                     "is_popup_window_opened"):
            snap["flags"][name] = getattr(app, name, None)
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
                write("=" * 72 if cls_name == "TrialStartManager" else "-" * 72)
                write("{}.__init__(args={} kwargs={}) from {}".format(
                    cls_name, init_args, frames.repr_value(kwargs), frames.caller()))
            except Exception:
                pass
            return result

        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False, safe=True)
        def manager_execute(orig, self, *args, **kwargs):
            choice_text = args[0] if args else kwargs.get("choice_text")
            app = getattr(self, "app", None) or ui.find_app()
            window = {"cls": cls_name, "texts": [], "days": [], "saves": 0,
                      "dots": 0, "overflow": 0, "inner": []}
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
                    ctx.log_exc("prison probe: cannot record before the window")
                return orig(self, *args, **kwargs)
            finally:
                try:
                    state["windows"].remove(window)
                except ValueError:
                    pass
                try:
                    after = snapshot(app)
                    row = {
                        "at": now(), "phase": "execute", "cls": cls_name,
                        "choice_text": choice_text, "init_args": init_args_of(self),
                        "before": before, "after": after,
                        "elapse_days_calls": window["days"],
                        "save_game_calls": window["saves"],
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
                    ctx.log_exc("prison probe: cannot record the window")

    for name in MANAGERS:
        install_windows(name)

    # ------------------------------------------------------------ 段の中の道
    def install_inner(cls_name, method):
        @ctx.wrap("__main__:{}.{}".format(cls_name, method), required=False, safe=True)
        def inner(orig, self, *args, **kwargs):
            head = "{}.{}".format(cls_name, method)
            try:
                write("    -> {}({})".format(head, frames.repr_value(args)))
            except Exception:
                pass
            result = orig(self, *args, **kwargs)
            try:
                write("    <- {} returned {}".format(head, frames.repr_value(result)))
                if state["windows"]:
                    state["windows"][-1]["inner"].append(head)
            except Exception:
                pass
            return result

    for cls_name, method in INNER:
        install_inner(cls_name, method)

    # ------------------------------------------- 日数送り・セーブ・文言（窓の内外）
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        try:
            if state["windows"]:
                days = args[0] if args else kwargs.get("days")
                state["windows"][-1]["days"].append(frames.repr_value(days))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.save_game", required=False, safe=True)
    def save_game(orig, self, *args, **kwargs):
        # 別スレッドから呼ばれることがある（229 で踏んだ）。数えるだけで何も読まない。
        try:
            if state["windows"]:
                state["windows"][-1]["saves"] += 1
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

    ctx.log("prison probe: ready ({}, {})".format(LOG_BASENAME, RECORD_BASENAME))
