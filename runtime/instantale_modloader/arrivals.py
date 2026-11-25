# -*- coding: utf-8 -*-
"""施設に着いた場面で「誰が話しかけるか」を MOD どうしで分け合う窓口。

`300_event_facility_arrival`（その場の NPC が話しかける）のように、施設に着いた直後に
ゲーム本来の会話を始める（`process_choice(ConversationStartManager, ...)`）MOD は1本とは限らない。
同じ到着で2本が会話を始めると噛み合わない。
MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。

    apply() の中で          arrivals.install(ctx, write)
    到着を決めたとき        arrivals.offer(app, owner, priority)
    会話を始める直前        if arrivals.winner(app) not in (None, owner): 譲る
    始める前に取りやめた    arrivals.withdraw(app, owner)

着いた**前後の処理**（施設に着いた後の見回り・街を移った後の知らせなど）もここで預かる。
十数本の MOD が `move_phase` / `AreaMoveManager.execute` を各自で包んでいた（TECH.md §3.3.21）。
施設は上の番号を数える包みと同じ1枚、街は `AreaMoveManager.execute` を1枚だけ包む。

    arrivals.on(owner, ctx, (arrivals.FACILITY,), after=fn)          # 施設に着いた後
    arrivals.on(owner, ctx, (arrivals.AREA,), before=fn, after=fn, always=True)   # 街を移る前後
    arrivals.on(owner, ctx, (arrivals.FACILITY,), enter=fn, leave=fn)  # 移動の処理の間だけ（ほかの前後の処理の内側）
    fn(call)    # call.app / call.obj（マネージャ）/ call.args …（gates.Call）

`call.app` はマネージャの `app`（無ければ走っている app）。前後の処理は引数も戻り値も変えない。
`FACILITY` の `after` の時点では、この到着の番号は進んでいる（`offer` は同じ到着に入る）。

街の移動の組み立て（`AreaMoveManager.__init__`）の前後も預かる（`AREA_SETUP`）。
`execute` には引数が来ないので、行き先と手段は組み立ての引数でしか読めない。
`307_` / `314_` / `325_` が組み立てを各自で包んで読んでいた（TECH.md §3.3.21）。

    arrivals.on(owner, ctx, (arrivals.AREA_SETUP,), before=fn)   # 街の移動を組み立てる前後
    fn(call)    # call.target_area_id / call.mode / call.app（引数の app）…

| 属性 | |
|---|---|
| `call.target_area_id` | 行き先の土地の id（文字列）。引数に無ければ None |
| `call.mode` | 手段（文字列。`walk` など）。引数に無ければ None |

窓口はマネージャに行き先と手段を控える（`move_of(manager)`）。
`AREA` の `call` にも同じ `call.target_area_id` / `call.mode` が載る（`AREA` を登録すると組み立ての包みも立つ）。

MOD が起こした会話の第一声の予約（`opening`）もここに置く。到着で話しかける MOD
（`300_` / `339_` / `911_`）が、会話を始める直前に印を置いて `conversation_starter` を各自で包み、
`messages` の最後の1件を「NPC の方から声をかけた」に差し替えていた。
相手の確かめ方が名前・id・確かめない、の3通りに割れていて、確かめない写しは別の相手の第一声を書き替えた。

    apply() の中で                   arrivals.install_opening(ctx, write)
    process_choice の直前            arrivals.opening(app, owner, npc_id, text_or_fn, ttl=60.0, ...)
    会話を始められなかった            arrivals.cancel_opening(owner)

- 予約は、次に来た `conversation_starter` で全部片付く。相手（`character_instance.id`）が予約の id と同じものだけ差し替え、
  ほかは捨てる（期限切れ・相手違い・相手の id が読めない）。同じ相手の予約が2つあれば新しい方
- 差し替えは `messages` の写しの最後の1件の `content`。ゲームが持つ会話履歴には触らない
- 包みはローダの一番外側に1枚（`gates.defer_outermost`）。`modnpc` の prompt の口や、
  ほかの MOD が同じ `messages[-1]` に足す文は、差し替えた後に足される（適用順に依らない）

申し出は移動の直後（`MovePhaseManager.move_phase` の復帰後）、会話を始めるのは手が空いてから
（`ui.Screen.when_idle`）なので、始める直前に見るときには同じ到着の申し出がそろっている。
`winner` はいちばん優先度の高い申し出の持ち主。同じ優先度なら先に申し出た方。

**会話を始めた後も申し出は下げない。**
下げると、後から確かめた側に申し出が無いように見えて、同じ到着で2本目の会話が始まる。
申し出は次の到着で数え直すときに消える。

**到着の見分けは移動の番号。**
`install` が `move_phase` を1枚だけ包み、`orig` の前で番号を1つ進める
（1つの世代につき1枚。`durations.install` と同じ形）。
申し出る MOD の判断はどれも `orig` の後なので、同じ移動の申し出は同じ番号に入る。
施設と時刻で見分けると、同じ施設に入り直したとき前の到着の申し出が残って譲り続ける。
包みが立っていない（`install` を呼ぶ MOD が無い）ときだけ、施設と時刻（`WINDOW_SECONDS`）で見分ける。
**申し出る MOD は必ず `install` も呼ぶこと。**
包みの印は前の世代のものも残るので、`install` を呼ぶ MOD が外れた世代で `offer` だけする MOD が居ると、
番号が進まないまま申し出が次の到着へ持ち越される。

置き場は `sys` の属性（`_instantale_arrivals`）。注入し直しをまたいで残る。
"""
import sys
import time

from . import gates, log, log_exc, state

#: 包みが立っていないときに、同じ到着とみなす秒数（最初の申し出から）。
WINDOW_SECONDS = 20.0

MOVE_TARGET = "__main__:MovePhaseManager.move_phase"
AREA_TARGET = "__main__:AreaMoveManager.execute"
AREA_SETUP_TARGET = "__main__:AreaMoveManager.__init__"
OPENING_TARGET = "scripts.llm.llm_manager:conversation_starter"
STORE_ATTR = "_instantale_arrivals"
_GATE_ATTR = "_instantale_arrivals_gate"
_OPENING_ATTR = "_instantale_arrivals_openings"
_OPENING_GATE_ATTR = "_instantale_arrivals_opening_gate"
#: 窓口がマネージャに控える行き先と手段の属性名（ゲームのデータではない。マネージャはセーブに入らない）。
_MOVE_ATTR = "_instantale_arrivals_move"

#: 前後の処理の種類。施設に着いた（`move_phase`）と、街を移った（`AreaMoveManager.execute`）と、
#: 街の移動を組み立てた（`AreaMoveManager.__init__`）。
FACILITY = "facility"
AREA = "area"
AREA_SETUP = "area_setup"
#: 前からの2つ（公開 I/F。中身を変えない）。`AREA_SETUP` も含めた全部は `ALL_KINDS`。
KINDS = (FACILITY, AREA)
ALL_KINDS = KINDS + (AREA_SETUP,)
HOOKS = {
    FACILITY: gates.Gate("arrivals.facility", MOVE_TARGET, "_instantale_arrivals_facility_hooks"),
    AREA: gates.Gate("arrivals.area", AREA_TARGET, "_instantale_arrivals_area_hooks"),
    AREA_SETUP: gates.Gate("arrivals.area_setup", AREA_SETUP_TARGET,
                           "_instantale_arrivals_area_setup_hooks"),
}
gates.track_install_attr(_GATE_ATTR)
gates.track_install_attr(_OPENING_GATE_ATTR)

#: 第一声の予約を捨てた理由（`dropped` の `call.reason`）。
DROP_STALE = "stale"          # 期限切れ
DROP_PARTNER = "partner"      # 会話の相手が予約の相手と違う・相手の id が読めない
DROP_REPLACED = "replaced"    # 同じ相手への新しい予約があった
DROP_MESSAGES = "messages"    # messages の最後の1件が読めない（差し替えられなかった）


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    if not kinds or any(kind not in HOOKS for kind in kinds):
        raise ValueError("arrivals: kinds must be among {}, got {!r}".format(ALL_KINDS, kinds))
    return kinds


def _app_of(manager):
    app = getattr(manager, "app", None)
    if app is not None:
        return app
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None, enter=None, leave=None):
    """着いた前後の処理を登録し、包みを立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    `enter` / `leave` は `orig`（移動の処理そのもの）のすぐ前と後で、ほかの MOD の `before` / `after` の内側
    （`gates` の説明の表）。`leave` は `orig` が投げても呼ぶ。
    """
    for kind in _kinds_of(kinds):
        HOOKS[kind].on(owner, ctx, before=before, after=after, always=always, write=write,
                       enter=enter, leave=leave)
        if kind == FACILITY:
            install(ctx, write)
        elif kind == AREA:
            _install_area(ctx, write)
        else:
            _install_area_setup(ctx, write)


def off(owner, kinds=ALL_KINDS):
    """前後の処理の登録を下げる。"""
    for kind in _kinds_of(kinds):
        HOOKS[kind].withdraw(owner)


def owners(kind):
    """前後の処理を登録している持ち主（登録の古い順）。"""
    return HOOKS[_kinds_of(kind)[0]].owners()


def move_of(manager):
    """組み立ての包みがマネージャに控えた `{"target_area_id", "mode"}`。控えが無ければ None。

    `AreaMoveManager.execute` には引数が来ないので、`execute` の側（`AREA` の前後の処理の外）で行き先を読むときに使う。
    """
    record = getattr(manager, _MOVE_ATTR, None)
    return dict(record) if isinstance(record, dict) else None


def _install_area(ctx, write=None):
    # 行き先と手段は組み立ての引数でしか読めないので、組み立ての包みも立てて控えさせる。
    _install_area_setup(ctx, write)

    def area_move(orig, self, *args, **kwargs):
        record = move_of(self) or {}
        call = gates.Call(AREA, self, args, kwargs, app=_app_of(self),
                          target_area_id=record.get("target_area_id"), mode=record.get("mode"))
        return HOOKS[AREA].run(call, lambda: orig(self, *args, **kwargs))
    return HOOKS[AREA].install(ctx, area_move, write)


def _text_or_none(value):
    return None if value is None else str(value)


def _install_area_setup(ctx, write=None):
    def area_setup(orig, self, *args, **kwargs):
        """行き先と手段を `orig` の前に読み、マネージャに控えてから前後の処理を呼ぶ。引数は受け取ったまま渡す。"""
        from . import frames
        app = target_area_id = mode = None
        try:
            # (app, target_area_id, mode)。名前で来ても位置で来ても読む（GAME.md §2.24）。
            app = frames.arg(args, kwargs, "app", 0)
            target_area_id = _text_or_none(frames.arg(args, kwargs, "target_area_id", 1))
            mode = _text_or_none(frames.arg(args, kwargs, "mode", 2))
            setattr(self, _MOVE_ATTR, {"target_area_id": target_area_id, "mode": mode})
        except Exception:
            log_exc("arrivals: cannot read the area move's target")
        hooks = HOOKS[AREA_SETUP]
        if not hooks.entries():
            return orig(self, *args, **kwargs)
        if app is None:
            app = _app_of(None)
        call = gates.Call(AREA_SETUP, self, args, kwargs, app=app,
                          target_area_id=target_area_id, mode=mode)
        return hooks.run(call, lambda: orig(self, *args, **kwargs))
    return HOOKS[AREA_SETUP].install(ctx, area_setup, write)


def _store():
    return state.process_store(STORE_ATTR, {"serial": 0, "key": None, "at": 0.0, "offers": []})


def installed():
    """包みの状態。`{"generation", "targets"}` か、立っていなければ None。"""
    done = getattr(sys, _GATE_ATTR, None)
    return done if isinstance(done, dict) else None


def install(ctx, write=None):
    """移動の番号を数える包みを立てる。包んだ対象の名前を返す。

    到着で話しかける MOD が `apply()` の中で呼ぶ。何本の MOD が呼んでも1つの世代につき1枚。
    """
    generation = getattr(ctx, "generation", None)
    done = installed()
    if done is not None and done.get("generation") == generation:
        return list(done.get("targets") or [])

    def move_phase(orig, self, *args, **kwargs):
        """番号を進めてから `orig` を呼ぶ。引数は受け取ったまま渡す。前後の処理（`FACILITY`）はその前後。"""
        _store()["serial"] += 1
        hooks = HOOKS[FACILITY]
        if not hooks.entries():
            return orig(self, *args, **kwargs)
        call = gates.Call(FACILITY, self, args, kwargs, app=_app_of(self))
        return hooks.run(call, lambda: orig(self, *args, **kwargs))

    ctx.wrap(MOVE_TARGET, required=False)(move_phase)
    setattr(sys, _GATE_ATTR, {"generation": generation, "targets": [MOVE_TARGET]})
    if write:
        write("arrivals: counting moves on {}".format(MOVE_TARGET))
    return [MOVE_TARGET]


def _place_of(app):
    """いまの到着の場所（土地の id と施設の id）。読めなければ None。"""
    player = getattr(app, "player", None)
    facility = getattr(player, "location", None)
    if facility is None:
        return None
    facility_id = getattr(facility, "id", None)
    if facility_id is None and isinstance(facility, (str, int)):
        facility_id = facility
    area = getattr(player, "current_area", None)
    area_id = getattr(area, "id", area)
    return (str(area_id), str(facility_id))


def _current(app, now=None):
    """いまの到着の記録。別の到着になっていれば空にしてから返す。"""
    store = _store()
    now = time.monotonic() if now is None else now
    if installed() is not None:
        key = ("move", store["serial"], _place_of(app))
        stale = store["key"] != key
    else:
        key = ("place", _place_of(app))
        stale = store["key"] != key or now - store["at"] > WINDOW_SECONDS
    if stale:
        store["key"] = key
        store["at"] = now
        store["offers"] = []
    return store


def offer(app, owner, priority=0):
    """この到着で話しかけたいと申し出る。同じ持ち主の申し出は差し替える。"""
    store = _current(app)
    store["offers"] = [row for row in store["offers"] if row[0] != owner]
    store["offers"].append((owner, int(priority)))


def winner(app):
    """この到着で話しかけてよい持ち主。申し出が無ければ None。"""
    store = _current(app)
    best = None
    for owner, priority in store["offers"]:
        if best is None or priority > best[1]:
            best = (owner, priority)
    return best[0] if best is not None else None


def offers(app):
    """この到着の申し出 `[(持ち主, 優先度)]`。ログ用。"""
    return list(_current(app)["offers"])


def withdraw(app, owner):
    """会話を始める前に取りやめた（施設を出た・別の画面になった）。自分の申し出を下げる。

    下げると、譲っていた側がこの到着で話しかけられるようになる。
    会話を始めた後には呼ばない（上の説明）。
    """
    store = _current(app)
    store["offers"] = [row for row in store["offers"] if row[0] != owner]


# -- 第一声の予約 ---------------------------------------------------------------
#: `opening` の `done` / `dropped` に渡る `gates.Call` の種類。
OPENING = "opening"


def _openings():
    return state.process_store(_OPENING_ATTR, {})


def _say(write, line):
    if callable(write):
        try:
            write(line)
        except Exception:
            pass


def _running_app():
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def opening_installed():
    """第一声の包みの状態。`{"generation", "targets"}` か、立っていなければ None。"""
    done = getattr(sys, _OPENING_GATE_ATTR, None)
    return done if isinstance(done, dict) else None


def install_opening(ctx, write=None):
    """第一声の予約を当てる包み（`conversation_starter`）を立てる。包んだ対象の名前を返す。

    予約する MOD が `apply()` の中で呼ぶ。何本の MOD が呼んでも1つの世代につき1枚。
    本番は MOD の適用の間は包まず、全 MOD の後にローダが一番外側に立てる（`gates.defer_outermost`）。
    外側に置くのは、`modnpc` の prompt の口とほかの MOD の包みが `messages[-1]` に足す文を、
    差し替えで消さないため（内側に置くと、足された後の文をまるごと替える世代が出る）。
    """
    generation = getattr(ctx, "generation", None)
    done = opening_installed()
    if done is not None and done.get("generation") == generation:
        return list(done.get("targets") or [])
    if gates.deferring():
        return []

    def conversation_starter(orig, *args, **kwargs):
        """予約があれば、相手を確かめて `messages` の写しの最後の1件を差し替えてから `orig` を呼ぶ。"""
        if not getattr(sys, _OPENING_ATTR, None):
            return orig(*args, **kwargs)
        chosen = call = None
        try:
            args, kwargs, chosen, call = _use_openings(args, kwargs)
        except Exception:
            log_exc("arrivals: cannot apply the opening; passing through")
        result = orig(*args, **kwargs)
        if chosen is not None:
            call.result = result
            _callback(chosen, "done", call)
        return result

    ctx.wrap(OPENING_TARGET, required=False)(conversation_starter)
    setattr(sys, _OPENING_GATE_ATTR, {"generation": generation, "targets": [OPENING_TARGET]})
    if write:
        write("arrivals: the opening gate was declared on {}".format(OPENING_TARGET))
    return [OPENING_TARGET]


def opening(app, owner, npc_id, text_or_fn, ttl=60.0, *, keep_rest=False, done=None, dropped=None,
            write=None):
    """MOD が起こす会話の第一声を予約する。`process_choice(ConversationStartManager, ...)` の直前に呼ぶ。予約したら True。

    | 引数 | |
    |---|---|
    | `npc_id` | 会話の相手の id。次の `conversation_starter` の `character_instance.id` と同じときだけ差し替える |
    | `text_or_fn` | 差し替える文。関数なら差し替えの時に `fn(call)` を呼び、返した文を使う（None・空なら差し替えない）。`call.content` は元の文 |
    | `ttl` | 予約の寿命（秒）。過ぎた予約は捨てる |
    | `keep_rest` | True なら元の文の1行目だけを替え、2行目以降を残す（`300_` の形）。既定は文をまるごと替える |
    | `done` | 差し替えた会話の `orig` が返った後に `done(call)`（`call.result` に戻り値・`call.text` に差し替えた文） |
    | `dropped` | 予約を使わずに捨てたときに `dropped(call)`（`call.reason` は `DROP_*`、`call.partner` は会話の相手の id） |
    | `write` | 差し替えと捨てた理由を書く先（MOD のログ） |

    同じ持ち主の予約は差し替える。予約は次の `conversation_starter` で、使っても使わなくても全部片付く
    （始まらなかった会話の印が残って、後で別の相手や同じ相手との普通の会話を書き替えないため）。
    `app` は予約したときの app で、差し替えの時に走っている app が引けなければ `call.app` に使う。
    包み（`install_opening`）は、予約する MOD が `apply()` の中で立てておく。
    """
    npc = "" if npc_id is None else str(npc_id)
    if not npc or not (isinstance(text_or_fn, str) or callable(text_or_fn)):
        log("arrivals: the opening of {} was refused (npc_id={!r}, text={})".format(
            owner, npc_id, type(text_or_fn).__name__), level="WARN")
        _say(write, "WARN arrivals: the opening was refused (npc_id={!r}, text={})".format(
            npc_id, type(text_or_fn).__name__))
        return False
    try:
        ttl = float(ttl)
    except (TypeError, ValueError):
        ttl = 60.0
    if opening_installed() is None:
        _say(write, "WARN arrivals: the opening gate is not installed; call install_opening in apply()")
    now = time.monotonic()
    store = _openings()
    stale = [(key, row) for key, row in list(store.items()) if now - row["at"] > row["ttl"]]
    for key, row in stale:
        store.pop(key, None)
    # 予約の順は通し番号で決める（時刻は Windows では同じ値が続くことがある）。
    seq = max([row.get("seq", 0) for row in store.values()] + [0]) + 1
    store[str(owner)] = {"npc": npc, "text": text_or_fn, "at": now, "ttl": ttl, "app": app, "seq": seq,
                         "keep_rest": bool(keep_rest), "done": done, "dropped": dropped, "write": write}
    for key, row in stale:
        _drop(key, row, DROP_STALE, None, (), {}, None, None)
    return True


def cancel_opening(owner):
    """自分の予約を下げる（会話を始められなかった）。下げたら True。`dropped` は呼ばない。"""
    return _openings().pop(str(owner), None) is not None


def openings():
    """いま残っている予約 `[(持ち主, 相手の id)]`（予約の古い順）。ログと検査用。"""
    rows = sorted(_openings().items(), key=_opening_order)
    return [(owner, row["npc"]) for owner, row in rows]


def _opening_order(item):
    return item[1].get("seq", 0)


def _callback(row, side, call):
    fn = row.get(side)
    if not callable(fn):
        return
    try:
        fn(call)
    except Exception as exc:
        log_exc("arrivals: the {} hook of the opening of {} failed".format(side, call.owner))
        _say(row.get("write"), "WARN arrivals: the {} hook of the opening raised {}: {}".format(
            side, type(exc).__name__, exc))


def _drop(owner, row, reason, partner, args, kwargs, app, character):
    _say(row.get("write"), "arrivals: the opening of {} for {} was dropped ({}; the talk is with {!r})".format(
        owner, row["npc"], reason, partner))
    call = gates.Call(OPENING, None, args, kwargs, app=app or row.get("app"), owner=owner,
                      npc_id=row["npc"], character=character, partner=partner, reason=reason,
                      content=None, text=None)
    _callback(row, "dropped", call)


def _use_openings(args, kwargs):
    """予約を全部片付け、相手が合う予約があれば差し替えた引数を返す。`(args, kwargs, 予約 | None, call | None)`。"""
    from . import frames
    store = _openings()
    taken = sorted(store.items(), key=_opening_order)
    store.clear()
    now = time.monotonic()
    character = frames.arg(args, kwargs, "character_instance", 3)
    partner = getattr(character, "id", None)
    partner = str(partner) if partner is not None and str(partner) else None
    app = _running_app()
    chosen = None
    losers = []
    for owner, row in taken:
        if now - row["at"] > row["ttl"]:
            losers.append((owner, row, DROP_STALE))
        elif partner is None or partner != row["npc"]:
            losers.append((owner, row, DROP_PARTNER))
        else:
            if chosen is not None:
                losers.append(chosen + (DROP_REPLACED,))
            chosen = (owner, row)
    for owner, row, reason in losers:
        _drop(owner, row, reason, partner, args, kwargs, app, character)
    if chosen is None:
        return args, kwargs, None, None
    owner, row = chosen
    messages = frames.arg(args, kwargs, "messages", 0)
    last = messages[-1] if isinstance(messages, list) and messages else None
    if not isinstance(last, dict):
        _drop(owner, row, DROP_MESSAGES, partner, args, kwargs, app, character)
        return args, kwargs, None, None
    content = last.get("content")
    call = gates.Call(OPENING, None, args, kwargs, app=app or row.get("app"), owner=owner,
                      npc_id=row["npc"], character=character, partner=partner, reason=None,
                      content=content, text=None)
    text = row["text"]
    if callable(text):
        try:
            text = text(call)
        except Exception as exc:
            log_exc("arrivals: the opening text of {} failed".format(owner))
            _say(row.get("write"), "WARN arrivals: the opening text raised {}: {}".format(
                type(exc).__name__, exc))
            text = None
    if not isinstance(text, str) or not text:
        _say(row.get("write"), "arrivals: the opening of {} for {} gave no text; left untouched".format(
            owner, row["npc"]))
        return args, kwargs, None, None
    if row.get("keep_rest"):
        # 1行目（ゲームの合図）だけを替え、2行目以降はゲームが付けたまま残す。
        rest = content.partition("\n")[2] if isinstance(content, str) else ""
        text = text + ("\n" + rest if rest else "")
    replacement = dict(last)
    replacement["content"] = text
    args, kwargs, _wrote = frames.replace_arg(args, kwargs, "messages", 0, messages[:-1] + [replacement])
    call.text = text
    _say(row.get("write"), "arrivals: the opening of {} for {}: {!r} -> {!r}".format(
        owner, row["npc"], frames.short(content, 60), frames.short(text, 80)))
    return args, kwargs, row, call


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"serial": 0, "key": None, "at": 0.0, "offers": []})
    setattr(sys, _OPENING_ATTR, {})
    for attr in (_GATE_ATTR, _OPENING_GATE_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)
    for hooks in HOOKS.values():
        hooks.reset()
