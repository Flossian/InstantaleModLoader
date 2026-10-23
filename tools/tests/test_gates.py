# -*- coding: utf-8 -*-
"""関所の共通部品 `gates` と、それを使う窓口（`loads` / `durations.on_days` / `arrivals.on`）をゲーム抜きで通す。

    python tools/tests/test_gates.py

  順番   … before は登録の新しい順、after は古い順（MOD が自分で包んでいたときの重なりと同じ）
  控え   … before の戻り値が同じ持ち主の after に call.memo で渡る
  例外   … orig が投げたら after は always=True のものだけ。前後の処理の例外は握って残りを止めない
  寿命   … 用済みの ctx の登録は呼ばない。同じ持ち主は差し替え、順は登録し直した時点になる
  1枚    … 同じ世代では何本登録しても包みは1枚
  loads  … 3つの種類が別々の関数を包む。WORLD は save_data と引数の app を渡す
  日数   … before は日数を決める前、after は暦の差（moved）と決めた日数（granted）を読める
  到着   … 施設の包みは番号を進めてから前後の処理を呼ぶ。街の包みは別の1枚
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import arrivals, durations, gates, loads  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx:
    """`wrap` で受けた包みを控える偽物。`dead` を立てると用済みになる。"""

    def __init__(self, generation=1):
        self.generation = generation
        self.hooks = {}
        self.dead = False

    def superseded(self):
        return self.dead

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            return func
        return decorator


def gate_order():
    print("順番と控え")
    gate = gates.Gate("t", "m:f", "_instantale_test_gate")
    gate.reset()
    seen = []
    ctx = Ctx()
    for name in ("A", "B", "C"):
        gate.on(name, ctx,
                before=lambda call, n=name: seen.append("before " + n) or n.lower(),
                after=lambda call, n=name: seen.append("after {} memo={}".format(n, call.memo)))

    def invoke():
        seen.append("orig")
        return 42

    call = gates.Call("k", None, (), {})
    result = gate.run(call, invoke)
    check("戻り値は orig のまま", result == 42 and call.result == 42, result)
    check("before は新しい順・after は古い順",
          seen == ["before C", "before B", "before A", "orig",
                   "after A memo=a", "after B memo=b", "after C memo=c"], seen)

    print("同じ持ち主の差し替え")
    seen[:] = []
    gate.on("A", ctx, after=lambda call: seen.append("after A2"))
    gate.run(gates.Call("k", None, (), {}), invoke)
    check("差し替えた A は最後（登録し直した時点の順）・before は無い",
          seen == ["before C", "before B", "orig", "after B memo=b", "after C memo=c", "after A2"], seen)

    print("用済みの ctx")
    seen[:] = []
    old = Ctx()
    gate.on("D", old, after=lambda call: seen.append("after D"))
    old.dead = True
    gate.run(gates.Call("k", None, (), {}), invoke)
    check("用済みの登録は呼ばない", "after D" not in seen, seen)
    check("持ち主の一覧にも出ない", "D" not in gate.owners(), gate.owners())

    print("例外")
    gate.reset()
    seen[:] = []

    def broken(call):
        raise RuntimeError("boom")

    gate.on("A", ctx, before=broken, after=broken)
    gate.on("B", ctx, after=lambda call: seen.append("after B"))
    gate.on("C", ctx, after=lambda call: seen.append("always C {}".format(type(call.error).__name__)),
            always=True)
    result = gate.run(gates.Call("k", None, (), {}), invoke)
    check("前後の処理が投げても orig と残りは走る",
          result == 42 and "orig" in seen and "after B" in seen, seen)

    def failing():
        raise ValueError("game")

    seen[:] = []
    raised = None
    try:
        gate.run(gates.Call("k", None, (), {}), failing)
    except ValueError as exc:
        raised = exc
    check("orig の例外はそのまま流す", isinstance(raised, ValueError), raised)
    check("orig が投げたら after は always だけ", seen == ["always C ValueError"], seen)

    print("登録が無ければ orig だけ")
    gate.reset()
    check("素通し", gate.run(gates.Call("k", None, (), {}), invoke) == 42)
    try:
        gate.on("A", ctx)
        check("前も後も無い登録は断る", False)
    except ValueError:
        check("前も後も無い登録は断る", True)


def loads_gate():
    print("loads")
    loads.reset()
    seen = []
    ctx = Ctx(generation=5)
    loads.on("107_x", ctx, (loads.LOAD, loads.NEW_GAME),
             after=lambda call: seen.append((call.kind, call.label, call.app)))
    loads.on("120_x", ctx, (loads.LOAD, loads.NEW_GAME),
             after=lambda call: seen.append(("120", call.kind)))
    loads.on("330_x", ctx, (loads.WORLD,),
             before=lambda call: call.save_data,
             after=lambda call: seen.append(("world", call.memo, call.app, call.obj)))
    check("種類ごとに1枚ずつ包む",
          [len(ctx.hooks.get(loads.TARGETS[k], [])) for k in loads.KINDS] == [1, 1, 1],
          {k: len(v) for k, v in ctx.hooks.items()})
    loads.on("123_x", ctx, (loads.NEW_GAME,), after=lambda call: None)
    check("同じ世代では2枚目を包まない", len(ctx.hooks[loads.TARGETS[loads.NEW_GAME]]) == 1)

    app = object()
    load = ctx.hooks[loads.TARGETS[loads.LOAD]][0]
    result = load(lambda self, *a, **k: ("loaded", a), app, "slot1")
    check("引数は受けたまま orig へ・戻り値もそのまま", result == ("loaded", ("slot1",)), result)
    check("LOAD は app を渡す・登録の古い順", seen == [(loads.LOAD, "load_game_new", app), ("120", loads.LOAD)], seen)

    seen[:] = []
    world = ctx.hooks[loads.TARGETS[loads.WORLD]][0]
    built = object()
    world(lambda self, *a, **k: None, built, {"key": 1}, app)
    check("WORLD は save_data と引数の app を渡す", seen == [("world", {"key": 1}, app, built)], seen)
    seen[:] = []
    world(lambda self, *a, **k: None, built, save_data_dict={"key": 2}, app=app)
    check("名前で渡されても読む", seen == [("world", {"key": 2}, app, built)], seen)

    newer = Ctx(generation=6)
    loads.on("107_x", newer, (loads.LOAD,), after=lambda call: None)
    check("世代が変われば包み直す", len(newer.hooks.get(loads.TARGETS[loads.LOAD], [])) == 1)
    check("持ち主の一覧", loads.owners(loads.LOAD) == ["120_x", "107_x"], loads.owners(loads.LOAD))
    loads.off("120_x")
    check("off で下がる", loads.owners(loads.LOAD) == ["107_x"], loads.owners(loads.LOAD))
    try:
        loads.on("x", ctx, ("nope",), after=lambda call: None)
        check("知らない種類は断る", False)
    except ValueError:
        check("知らない種類は断る", True)


class Clock:
    def __init__(self, day):
        self.world = types.SimpleNamespace(days_elapsed=day)


def days_gate():
    print("日数の前後")
    durations.DAYS_HOOKS.reset()
    for attr in (durations._CLAIMS_ATTR, durations._GATE_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)
    seen = []
    ctx = Ctx(generation=9)
    durations.on_days("327_x", ctx, before=lambda call: seen.append(("before", call.granted, call.day_before)))
    durations.on_days("316_x", ctx, after=lambda call: seen.append(
        ("after", call.days, call.granted, call.day_before, call.day_after, call.moved)))
    check("日数の関所は1枚", len(ctx.hooks.get(durations.DAYS_TARGET, [])) == 1)
    durations.claim_days("314_x", lambda app, days: {"days": 14, "since": 1.0})
    gate = ctx.hooks[durations.DAYS_TARGET][0]
    app = Clock(100)

    def orig(self, days, *args, **kwargs):
        seen.append(("orig", days))
        self.world.days_elapsed += days
        return "done"

    result = gate(orig, app, 90)
    check("戻り値はそのまま", result == "done", result)
    check("before は日数を決める前（granted は素の値）・orig には決めた日数・after は暦の差",
          seen == [("before", 90, 100), ("orig", 14), ("after", 90, 14, 100, 114, 14)], seen)

    seen[:] = []
    durations.forget("314_x")
    app.world = None
    gate(lambda self, days: None, app, 30)
    check("暦が読めなければ moved は決めた日数", seen[-1] == ("after", 30, 30, None, None, 30), seen)
    durations.off_days("327_x")
    durations.off_days("316_x")
    seen[:] = []
    passed = []
    gate(lambda self, days: passed.append(days), Clock(1), 7)
    check("登録が無ければ渡された日数のまま", passed == [7] and seen == [], (passed, seen))


class Manager:
    def __init__(self, app):
        self.app = app


def arrival_gates():
    print("到着の前後")
    arrivals.reset()
    seen = []
    ctx = Ctx(generation=3)
    arrivals.on("405_x", ctx, (arrivals.FACILITY,),
                after=lambda call: seen.append(("facility", arrivals._store()["serial"], call.app)))
    arrivals.on("336_x", ctx, (arrivals.AREA,), before=lambda call: seen.append("area before"),
                after=lambda call: seen.append(("area after", call.error is not None)), always=True)
    check("施設の包みは番号の包みと同じ1枚", len(ctx.hooks.get(arrivals.MOVE_TARGET, [])) == 1
          and arrivals.installed() is not None)
    check("街は別の1枚", len(ctx.hooks.get(arrivals.AREA_TARGET, [])) == 1)
    app = object()
    move = ctx.hooks[arrivals.MOVE_TARGET][0]
    move(lambda self: None, Manager(app))
    check("番号を進めてから後の処理", seen == [("facility", 1, app)], seen)
    seen[:] = []
    area = ctx.hooks[arrivals.AREA_TARGET][0]
    try:
        area(lambda self, text: (_ for _ in ()).throw(RuntimeError("refused")), Manager(app), "馬車")
    except RuntimeError:
        pass
    check("always の後の処理は orig が投げても来る", seen == ["area before", ("area after", True)], seen)
    arrivals.off("405_x")
    check("off で下がる", arrivals.owners(arrivals.FACILITY) == [], arrivals.owners(arrivals.FACILITY))


def main():
    gate_order()
    loads_gate()
    days_gate()
    arrival_gates()
    print("FAILED: {}".format(failures) if failures else "OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
