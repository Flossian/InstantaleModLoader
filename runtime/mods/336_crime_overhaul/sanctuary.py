# -*- coding: utf-8 -*-
"""裏の事務所には衛兵と追手が来ない。遊び方は DOC.md の同じ名前の節。

##### ゲームの衛兵（移動の処理の間だけ、その土地の手配度を平常に見せる）

ゲームは施設へ移る処理（`MovePhaseManager.move_phase`）の中で、その土地の手配度を見て衛兵を抽選する。
施設の種類は見ない（裏の事務所に入った回に衛兵が出た記録がある。GAME.md §2.20「衛兵の抽選」）。
抽選そのものに手を入れる口は無いので、移動先が対象の施設のときは、移動の処理の間だけ
その土地の手配度を平常（`CALM`）に置き換え、終わったら戻す。

- 置き換えるのは窓口 `arrivals` の `enter` / `leave`（移動の処理のすぐ前と後）。ほかの MOD の到着の前後の処理
  （`317_` の評判の照合など、手配度を読むもの）より内側なので、そちらには本当の値が見える
- 情景描写のリクエストには手配度が載らないので、描写は変わらない（GAME.md §2.20）
- 移動の処理の中でゲームが保存しても、保存の間は本当の値へ戻す（窓口 `saving`。錠は保存の間ずっと持つ）
- 戻すときは、移動の間に動いた分（平常からの差）を本当の値に足す（移動の中で手配度が動いても失わない）
- 手配度が平常以上の土地、読めない土地、移動先が読めない回は何もしない

##### 追手（`hunter`）

追手は、決まっても画面が整ったところで起こす（`hunter.launch`）。そこで今いる施設が対象なら、控えたまま待つ。
裏の事務所を出て画面が整ったところで出る（決まった追手は、画面が20回変わっても出られなければ捨てる。追手の決まり）。
口は `env.sanctuary(app)`（対象の施設に居れば施設の名前、居なければ None）。
"""
import threading

from instantale_modloader import arrivals, saving, ui

#: 移動の間に見せる手配度（素の平常値。GAME.md §2.20）。
CALM = 10

#: 衛兵と追手が来ない施設の種類（`facility_type`。GAME.md §2.13.1）。
#: 今は裏の事務所だけ。広げるときは種類ごとの入切を設定に足す（辞書・タプルは設定に宣言しない。TECH.md §3.8）。
SANCTUARY_TYPES = ("underworld_office",)


def install(env):
    ctx, cfg, write = env.ctx, env.cfg, env.write
    owner = env.owner + ":sanctuary"
    #: 移動の間の置き換えと保存の間の戻しを、同じ錠で順に通す（保存は別のスレッドから来ることがある）。
    lock = threading.RLock()
    #: 置き換えている最中の控え。`{"entry": 土地の記録, "real": 本当の手配度, "area": 土地の id, "name": 施設名}`。
    held = {"swap": None}

    def covers(facility):
        return facility is not None and ui.facility_type_of(facility) in SANCTUARY_TYPES

    def facility_label(app, facility):
        name = ui.facility_name(app, facility) if facility is not None else ""
        return name or ui.facility_type_of(facility) or "?"

    def sanctuary(app):
        """いま対象の施設に居れば、その施設の名前。居なければ None（追手が見る）。"""
        if not cfg.SANCTUARY_ENABLED or app is None:
            return None
        facility = ui.current_facility(app)
        return facility_label(app, facility) if covers(facility) else None

    env.sanctuary = sanctuary

    def destination(call):
        """この移動の行き先。`(土地の id, 施設)`。読めなければ `(None, None)`。

        マネージャは `MovePhaseManager(app, connected_node_id, facility_move_to_id, area_id)` で組まれ、
        同じ名前の属性を持つ（exe の定数と `205_` の記録。GAME.md §2.20）。
        """
        manager = call.obj
        facility_id = getattr(manager, "facility_move_to_id", None)
        area_id = getattr(manager, "area_id", None)
        app = call.app
        if area_id in (None, ""):
            area_id = ui.area_id_of(ui.current_area(app))
        if facility_id in (None, "") or area_id in (None, ""):
            return None, None
        area = (ui.world_areas(app) or {}).get(str(area_id))
        facility, _node = ui.find_facility(area, facility_id)
        return str(area_id), facility

    def enter(call):
        """移動の処理の直前。行き先が対象の施設で、その土地で手配されていれば手配度を平常に見せる。"""
        if not cfg.SANCTUARY_ENABLED:
            return None
        app = call.app
        area_id, facility = destination(call)
        if not covers(facility):
            return None
        entry = ui.area_record(getattr(app, "player", None), area_id)
        real = ui.lawfulness_of(entry)
        if real is None or real >= CALM:
            return None
        with lock:
            if held["swap"] is not None:
                return None             # 置き換えの最中に次の移動は来ない（来たら重ねない）
            if not ui.set_lawfulness(entry, CALM):
                write("sanctuary: 手配度を書き換えられなかった 土地={}".format(area_id))
                return None
            held["swap"] = {"entry": entry, "real": real, "area": area_id,
                            "name": facility_label(app, facility)}
        write("sanctuary: {} へ入る移動の間だけ、土地 {} の手配度 {} を {} に見せる".format(
            held["swap"]["name"], area_id, real, CALM))
        return True

    def leave(call):
        """移動の処理の直後（投げたときも）。本当の値へ戻す。移動の間に動いた分は足す。"""
        with lock:
            swap, held["swap"] = held["swap"], None
            if swap is None:
                return
            now = ui.lawfulness_of(swap["entry"])
            moved = now - CALM if isinstance(now, int) and not isinstance(now, bool) else 0
            final = swap["real"] + moved
            ok = ui.set_lawfulness(swap["entry"], final)
        write("sanctuary: {} に入った。土地 {} の手配度を {} に戻した{}{}".format(
            swap["name"], swap["area"], final,
            "（移動の間に {:+d}）".format(moved) if moved else "",
            "" if ok else "（書き戻せなかった）"))

    arrivals.on(owner, ctx, (arrivals.FACILITY,), enter=enter, leave=leave, write=write)

    def save_before(call):
        """保存の間は本当の値へ戻す。錠は保存の間ずっと持つ（`save_after` で放す）。"""
        lock.acquire()
        try:
            swap = held["swap"]
            if swap is None:
                return {"locked": True, "swapped": False}
            now = ui.lawfulness_of(swap["entry"])
            moved = now - CALM if isinstance(now, int) and not isinstance(now, bool) else 0
            swap["real"] += moved
            ui.set_lawfulness(swap["entry"], swap["real"])
            return {"locked": True, "swapped": True}
        except Exception:
            lock.release()              # 投げたら `after` に錠の印が渡らないので、ここで放す
            raise

    def save_after(call):
        memo = call.memo or {}
        try:
            swap = held["swap"]
            if memo.get("swapped") and swap is not None:
                ui.set_lawfulness(swap["entry"], CALM)
                write("sanctuary: 移動の途中の保存は本当の手配度 {} で書いた（土地 {}）".format(
                    swap["real"], swap["area"]))
        finally:
            if memo.get("locked"):
                lock.release()

    saving.on(owner, ctx, before=save_before, after=save_after, always=True, write=write)
