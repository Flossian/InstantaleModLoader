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

#: 包みが立っていないときに、同じ到着とみなす秒数（最初の申し出から）。
WINDOW_SECONDS = 20.0

MOVE_TARGET = "__main__:MovePhaseManager.move_phase"
STORE_ATTR = "_instantale_arrivals"
_GATE_ATTR = "_instantale_arrivals_gate"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"serial": 0, "key": None, "at": 0.0, "offers": []}
        setattr(sys, STORE_ATTR, store)
    store.setdefault("serial", 0)
    return store


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
        """番号を進めてから `orig` を呼ぶ。引数は受け取ったまま渡す。"""
        _store()["serial"] += 1
        return orig(self, *args, **kwargs)

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


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"serial": 0, "key": None, "at": 0.0, "offers": []})
    if hasattr(sys, _GATE_ATTR):
        delattr(sys, _GATE_ATTR)
