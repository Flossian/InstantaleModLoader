# -*- coding: utf-8 -*-
"""ローダの窓口 `shopping`（店で売買を始める場面と、棚に雛形から品を並べる場面の前後）をゲーム抜きで通す。

    python tools/tests/test_window_c2.py

  読む     … ENTER は居る施設、LAY は主と段を orig の前に読んで渡す（位置でもキーワードでも）
  順       … before は登録の新しい順、after は古い順。before の戻り値は同じ持ち主の after へ（種類ごと）
  並べる   … LAY の before で足した品は、登録の順に関わらず全部の after が見る
  投げた回 … orig が投げたら always=True の after だけ来る（call.error）。例外はそのまま流す
  握る     … 前後の処理の例外は記録して、残りと orig を止めない
  外側     … 適用の間は包まず、install で立てる。自分で包む MOD（331 の形）より外側
  素通し   … 登録が無ければ施設も引数も読まずに orig を呼ぶ
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import gates, shopping, ui  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("ok  " if ok else "FAIL", name, "  " + str(detail) if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Ctx(object):
    """後から当てた包みが外側になる（本物の wrap と同じ）。"""
    seq = 0

    def __init__(self):
        Ctx.seq += 1
        self.generation = Ctx.seq
        self.hooks = {}
        self.dead = False

    def superseded(self):
        return self.dead

    def wrap(self, target, **kw):
        def decorator(fn):
            previous = self.hooks.get(target)
            if previous is None:
                self.hooks[target] = fn
            else:
                self.hooks[target] = lambda orig, this, *a, _p=previous, _f=fn, **k: _f(
                    lambda obj, *a2, **k2: _p(orig, obj, *a2, **k2), this, *a, **k)
            return fn
        return decorator


class Player(object):
    def __init__(self, location):
        self.location = location


class App(object):
    def __init__(self, location="店"):
        self.player = Player(location)


class Manager(object):
    def __init__(self, app):
        self.app = app


class Owner(object):
    def __init__(self):
        self.id = "keeper"
        self.inventory = {}


ENTER_TARGET = shopping.TARGETS[shopping.ENTER]
LAY_TARGET = shopping.TARGETS[shopping.LAY]


def fresh():
    gates.reset_all()
    shopping.reset()
    return Ctx()


print("[外側に立てる]")
ctx = fresh()
gates.defer_outermost(True)
try:
    shopping.on("312", ctx, (shopping.ENTER, shopping.LAY), before=lambda call: None)
    check("適用の間は包まない", ENTER_TARGET not in ctx.hooks and LAY_TARGET not in ctx.hooks, sorted(ctx.hooks))
finally:
    gates.defer_outermost(False)
built = shopping.install(ctx, None)
check("install(ctx, write) で2つとも立てる", built == [ENTER_TARGET, LAY_TARGET], built)
check("→ 2つの対象を包む", ENTER_TARGET in ctx.hooks and LAY_TARGET in ctx.hooks)
check("同じ世代では2枚目を立てない", shopping.install(ctx, None) == [ENTER_TARGET, LAY_TARGET])
check("登録の持ち主", shopping.owners(shopping.ENTER) == ["312"] and shopping.owners(shopping.LAY) == ["312"])
shopping.reset()

print("[ENTER: 順と施設]")
ctx = fresh()
seen = []
real_current = ui.current_facility
ui.current_facility = lambda app: getattr(getattr(app, "player", None), "location", None)


def make(owner):
    def before(call):
        seen.append(("before", owner, call.facility, call.kind, call.label))
        return "memo-" + owner

    def after(call):
        seen.append(("after", owner, call.memo, call.result,
                     type(call.error).__name__ if call.error else None))
    return before, after


try:
    for name, always in (("312", True), ("336", False), ("341", False)):
        b, a = make(name)
        shopping.on(name, ctx, (shopping.ENTER,), before=b, after=a, always=always)
    check("立てたのは ENTER だけ", ENTER_TARGET in ctx.hooks and LAY_TARGET not in ctx.hooks)
    hook = ctx.hooks[ENTER_TARGET]

    def execute(self):
        # 売買を始めると居る場所が変わったとしても、前の処理は orig の前の施設を見る。
        self.app.player.location = "別の場所"
        return "opened"

    app = App("店")
    check("orig の戻り値を返す", hook(execute, Manager(app)) == "opened")
    check("before は新しい順で、施設を orig の前に読む",
          [row[:3] for row in seen[:3]] == [("before", "341", "店"), ("before", "336", "店"), ("before", "312", "店")],
          seen[:3])
    check("call.kind と call.label", seen[0][3:] == (shopping.ENTER, "execute"), seen[0])
    check("after は古い順で、memo と戻り値が渡る",
          seen[3:] == [("after", "312", "memo-312", "opened", None), ("after", "336", "memo-336", "opened", None),
                       ("after", "341", "memo-341", "opened", None)], seen[3:])

    print("[ENTER: 投げた回]")
    seen.clear()

    def broken(self):
        raise RuntimeError("boom")

    try:
        hook(broken, Manager(App()))
    except RuntimeError:
        check("orig の例外はそのまま流す", True)
    else:
        check("orig の例外はそのまま流す", False)
    check("always=True の after だけ来る（call.error）",
          [row for row in seen if row[0] == "after"] == [("after", "312", "memo-312", None, "RuntimeError")], seen)

    print("[ENTER: app の引き方]")
    seen.clear()
    found = App("見つけた店")
    real_find = ui.find_app
    ui.find_app = lambda: found
    try:
        hook(lambda self: "done", type("SSM", (), {})())
    finally:
        ui.find_app = real_find
    check("self.app が無ければ ui.find_app()", ("before", "312", "見つけた店", shopping.ENTER, "execute") in seen, seen)
finally:
    ui.current_facility = real_current

print("[ENTER: 握る]")
ctx = fresh()
seen = []
warned = []


def bad_before(call):
    raise ValueError("bad")


shopping.on("312", ctx, (shopping.ENTER,), after=lambda call: seen.append("312 after"))
shopping.on("405", ctx, (shopping.ENTER,), before=bad_before, write=warned.append)
result = ctx.hooks[ENTER_TARGET](lambda self: seen.append("orig") or "ok", Manager(App()))
check("前の処理が投げても orig と残りは走る", result == "ok" and seen == ["orig", "312 after"], seen)
check("持ち主の write に WARN を残す", len(warned) == 1 and warned[0].startswith("WARN shopping.enter"), warned)

print("[ENTER: 用済みの登録と下げた登録]")
seen.clear()
dead = Ctx()
dead.generation = ctx.generation
dead.dead = True
shopping.on("old", dead, (shopping.ENTER,), before=lambda call: seen.append("old"))
shopping.off("405")
ctx.hooks[ENTER_TARGET](lambda self: "ok", Manager(App()))
check("用済みの ctx の登録と下げた登録は呼ばない", seen == ["312 after"], seen)
check("持ち主は生きている登録だけ", shopping.owners(shopping.ENTER) == ["312"], shopping.owners(shopping.ENTER))

print("[素通し]")
ctx = fresh()
shopping.install(ctx)
reads = []
real_current = ui.current_facility
ui.current_facility = lambda app: reads.append(app)
try:
    out = ctx.hooks[LAY_TARGET](lambda self, owner, tier: (owner, tier), Manager(App()), "o", 2)
finally:
    ui.current_facility = real_current
check("登録が無ければ orig をそのまま呼ぶ", out == ("o", 2))
check("→ 施設を読まない", reads == [], reads)

print("[LAY: 主と段・並べた品の検品]")
ctx = fresh()
seen = []
real_current = ui.current_facility
ui.current_facility = lambda app: "鍛冶屋"


def tier_before(call):
    seen.append(("312 tier", call.owner.id, call.tier, call.facility))


def fill_before(call):
    # 340 の形: 空の棚なら並べる前に品を足し、並べる前の鍵を後へ渡す。
    stock = call.owner.inventory
    if not stock:
        stock["added"] = "傾向の品"
    return set(stock)


def drop_after(call):
    stock = call.owner.inventory
    for key in [key for key in list(stock) if key not in call.memo and key.startswith("gear")]:
        del stock[key]
    seen.append(("340 dropped", sorted(stock)))


def check_after(call):
    seen.append(("405 checked", sorted(call.owner.inventory), call.result))


def lay(self, shop_owner_instance, next_tier):
    shop_owner_instance.inventory["gear-1"] = "剣"
    shop_owner_instance.inventory["potion"] = "薬"
    return "laid"


try:
    # 適用の順（312 → 340 → 405）。340 が足して外した後の棚を 405 が検品する。
    shopping.on("312", ctx, (shopping.LAY,), before=tier_before)
    shopping.on("340", ctx, (shopping.LAY,), before=fill_before, after=drop_after)
    shopping.on("405", ctx, (shopping.LAY,), after=check_after)
    hook = ctx.hooks[LAY_TARGET]
    owner = Owner()
    check("orig の戻り値を返す", hook(lay, Manager(App()), owner, 3) == "laid")
    check("位置の引数から主と段を読む", seen[0] == ("312 tier", "keeper", 3, "鍛冶屋"), seen)
    check("after は古い順（340 が外してから 405 が検品）",
          seen[1:] == [("340 dropped", ["added", "potion"]), ("405 checked", ["added", "potion"], "laid")], seen)
    seen.clear()
    owner2 = Owner()
    hook(lay, Manager(App()), shop_owner_instance=owner2, next_tier=1)
    check("キーワードの引数からも読む", seen[0] == ("312 tier", "keeper", 1, "鍛冶屋"), seen)

    # 405 が 340 より先に登録されても、340 の before（足す）は 405 の after（検品）より先に走る。
    ctx = fresh()
    seen.clear()
    shopping.on("405", ctx, (shopping.LAY,), after=check_after)
    shopping.on("340", ctx, (shopping.LAY,), before=fill_before, after=drop_after)
    ctx.hooks[LAY_TARGET](lay, Manager(App()), Owner(), 1)
    check("before で足した品は、先に登録した after も見る",
          seen and seen[0][0] == "405 checked" and "added" in seen[0][1], seen)
finally:
    ui.current_facility = real_current

print("[LAY: 種類ごとの memo]")
ctx = fresh()
memos = []
shopping.on("340", ctx, (shopping.ENTER,), before=lambda call: "enter-memo",
            after=lambda call: memos.append(("enter", call.memo)), always=True)
shopping.on("340", ctx, (shopping.LAY,), before=lambda call: "lay-memo",
            after=lambda call: memos.append(("lay", call.memo)))


def execute_with_lay(self):
    return ctx.hooks[LAY_TARGET](lambda this, o, t: None, self, Owner(), 1)


ctx.hooks[ENTER_TARGET](execute_with_lay, Manager(App()))
check("入れ子の LAY と ENTER で memo が混ざらない", memos == [("lay", "lay-memo"), ("enter", "enter-memo")], memos)

print("[自分で包む MOD より外側]")
ctx = fresh()
seen = []
gates.defer_outermost(True)
try:
    shopping.on("312", ctx, (shopping.ENTER,), before=lambda call: seen.append("312 before"),
                after=lambda call: seen.append(("312 after", call.result, call.error)), always=True)

    # 331 の形: 自分の建物で orig が投げたら握って None を返す（適用の間に直に包む）。
    @ctx.wrap(ENTER_TARGET)
    def guarded(orig, self, *args, **kwargs):
        try:
            return orig(self, *args, **kwargs)
        except Exception:
            seen.append("331 swallowed")
            return None

    shopping.on("405", ctx, (shopping.ENTER,), after=lambda call: seen.append(("405 after", call.result, call.error)))
finally:
    gates.defer_outermost(False)
shopping.install(ctx, None)


def fails(self):
    raise KeyError("facility")


check("投げた回も外側では None が返る", ctx.hooks[ENTER_TARGET](fails, Manager(App())) is None)
check("前後の処理は 331 の外側で、握った後の None を見る",
      seen == ["312 before", "331 swallowed", ("312 after", None, None), ("405 after", None, None)], seen)

print("[種類の誤り]")
try:
    shopping.on("x", Ctx(), ("open",), before=lambda call: None)
except ValueError:
    check("知らない種類は ValueError", True)
else:
    check("知らない種類は ValueError", False)
try:
    shopping.on("x", Ctx(), (), before=lambda call: None)
except ValueError:
    check("種類が空なら ValueError", True)
else:
    check("種類が空なら ValueError", False)
check("owners は文字列の種類で引ける", shopping.owners("lay") == [])

gates.reset_all()
print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
