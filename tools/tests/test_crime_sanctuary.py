# -*- coding: utf-8 -*-
"""336_crime_overhaul の「裏の事務所には衛兵と追手が来ない」（sanctuary.py）を、ゲーム抜きで通す。

    python tools/tests/test_crime_sanctuary.py

偽の世界（土地・施設）と、偽の移動の処理（`MovePhaseManager.move_phase` の本体の代わり）を差し込む。

  見せる   … 手配された土地で裏の事務所へ入る移動の間だけ、その土地の手配度が平常（10）に見える
  戻す     … 移動の処理が返ったら（投げても）本当の値へ戻す。移動の間に動いた分は足す
  内側     … ほかの MOD の到着の後の処理には本当の値が見える（窓口 `arrivals` の leave が先）
  触らない … 裏の事務所以外の施設・手配されていない土地・切ってあるとき・行き先が読めないときは何もしない
  保存     … 移動の途中の保存は本当の値で書き、保存の後はまた平常に見せる
  追手     … 裏の事務所に居れば `env.sanctuary` がその施設の名前を返す。居なければ None
"""
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import arrivals, gates, saving  # noqa: E402


def load_sanctuary():
    matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_overhaul"))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *_crime_overhaul: {}".format(matches))
    package = types.ModuleType("crime_overhaul_sanctuary_parts")
    package.__path__ = [os.path.join(MODS_DIR, matches[0])]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + ".sanctuary")


sanctuary = load_sanctuary()
failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({!r})".format(detail) if not cond else ""))
    if not cond:
        failures.append(name)


class Ctx(object):
    def __init__(self):
        self.generation = "g1"
        self.hooks = {}

    def superseded(self):
        return False

    def wrap(self, target, **kw):
        def deco(fn):
            self.hooks.setdefault(target, []).append(fn)
            return fn
        return deco

    def log_exc(self, msg):
        raise AssertionError(msg)


class Cfg(object):
    SANCTUARY_ENABLED = True


class Facility(object):
    def __init__(self, fid, name, kind):
        self.id = fid
        self.name = name
        self.facility_type = kind


class Node(object):
    def __init__(self, facilities):
        self.facilities = dict((f.id, f) for f in facilities)


class Area(object):
    def __init__(self, aid, facilities):
        self.id = aid
        self.nodes = {"n": Node(facilities)}


OFFICE = Facility("31", "闇の窓口", "underworld_office")
STORE = Facility("32", "よろず屋", "general_store")
WARD = Facility("30", "下町", "ward")


def make_app(lawfulness=-30):
    app = types.SimpleNamespace()
    area = Area("2", [WARD, OFFICE, STORE])
    app.world = types.SimpleNamespace(areas={"2": area})
    app.player = types.SimpleNamespace(
        current_area=area, location=WARD,
        area_history={"2": {"residency": {}, "achievements": [], "lawfulness": lawfulness}})
    return app


def lawfulness(app):
    return app.player.area_history["2"]["lawfulness"]


class Manager(object):
    """`MovePhaseManager(app, connected_node_id, facility_move_to_id, area_id)` の代わり。"""

    def __init__(self, app, facility_id, area_id="2"):
        self.app = app
        self.connected_node_id = "n"
        self.facility_move_to_id = facility_id
        self.area_id = area_id


def setup(enabled=True):
    gates.reset_all()
    saving.reset()
    ctx = Ctx()
    lines = []
    cfg = Cfg()
    cfg.SANCTUARY_ENABLED = enabled
    env = types.SimpleNamespace(ctx=ctx, cfg=cfg, write=lines.append, owner="336_crime_overhaul",
                                sanctuary=lambda app: None)
    sanctuary.install(env)
    return env, ctx, lines


def move(ctx, app, facility, body=None):
    """移動の処理を1回通す。`body(app)` は本体の中で起きること（既定は、手配度を読んで控えるだけ）。"""
    seen = {}

    def orig(manager):
        seen["inside"] = lawfulness(app)
        if body is not None:
            body(app)
        app.player.location = facility
        return "戻り値"

    wrapper = ctx.hooks[arrivals.MOVE_TARGET][0]
    returned = wrapper(orig, Manager(app, facility.id))
    return returned, seen.get("inside")


# ================================================================ 見せる・戻す
print("[見せる・戻す] 手配された土地で裏の事務所へ入る移動")
env, ctx, lines = setup()
app = make_app(-30)
returned, inside = move(ctx, app, OFFICE)
check("移動の間は平常（10）に見える", inside == sanctuary.CALM, inside)
check("移動が終われば本当の値（-30）に戻る", lawfulness(app) == -30, lawfulness(app))
check("移動の処理の戻り値はそのまま", returned == "戻り値", returned)
check("記録に見せたことと戻したことが残る",
      any("に見せる" in line for line in lines) and any("に戻した" in line for line in lines), lines)
check("裏の事務所に居れば、追手の口が施設の名前を返す", env.sanctuary(app) == OFFICE.name, env.sanctuary(app))

print("[戻す] 移動の間に動いた分は足す・投げても戻す")
env, ctx, lines = setup()
app = make_app(-30)


def lowered(app_):
    app_.player.area_history["2"]["lawfulness"] -= 5


move(ctx, app, OFFICE, body=lowered)
check("移動の間に 5 下がったら、本当の値からも 5 下げる（-35）", lawfulness(app) == -35, lawfulness(app))

env, ctx, lines = setup()
app = make_app(-30)


def explode(_app):
    raise RuntimeError("game")


raised = None
try:
    move(ctx, app, OFFICE, body=explode)
except RuntimeError as exc:
    raised = exc
check("移動の処理が投げても本当の値へ戻す（例外はそのまま流す）",
      isinstance(raised, RuntimeError) and lawfulness(app) == -30, (raised, lawfulness(app)))

# ================================================================ 内側
print("[内側] ほかの MOD の到着の後の処理には本当の値が見える")
env, ctx, lines = setup()
seen_after = []
arrivals.on("317_reputation", ctx, (arrivals.FACILITY,),
            after=lambda call: seen_after.append(lawfulness(call.app)))
app = make_app(-30)
wrapper = ctx.hooks[arrivals.MOVE_TARGET][0]
manager = Manager(app, OFFICE.id)
wrapper(lambda m: None, manager)
check("後の処理が読むのは本当の値（-30）", seen_after == [-30], seen_after)
arrivals.off("317_reputation")

# ================================================================ 触らない
print("[触らない]")
for label, facility, start, enabled in (("裏の事務所以外の施設", STORE, -30, True),
                                         ("手配されていない土地", OFFICE, 10, True),
                                         ("切ってあるとき", OFFICE, -30, False)):
    env, ctx, lines = setup(enabled=enabled)
    app = make_app(start)
    _returned, inside = move(ctx, app, facility)
    check("{} は移動の間も本当の値のまま".format(label), inside == start and lawfulness(app) == start,
          (inside, lawfulness(app)))
env, ctx, lines = setup(enabled=False)
app = make_app(-30)
app.player.location = OFFICE
check("切ってあれば、裏の事務所に居ても追手の口は None", env.sanctuary(app) is None, env.sanctuary(app))
env, ctx, lines = setup()
app = make_app(-30)
wrapper = ctx.hooks[arrivals.MOVE_TARGET][0]
seen = {}
wrapper(lambda m: seen.setdefault("inside", lawfulness(app)), types.SimpleNamespace(app=app))
check("行き先の読めない移動は何もしない", seen.get("inside") == -30 and lawfulness(app) == -30, seen)
check("店に居れば追手の口は None", env.sanctuary(make_app(-30)) is None)

# ================================================================ 保存
print("[保存] 移動の途中の保存")
env, ctx, lines = setup()
app = make_app(-30)
written = []
save = ctx.hooks[saving.TARGET][0]


def save_inside(app_):
    save(lambda self: written.append(lawfulness(app_)), app_)
    written.append(("after save", lawfulness(app_)))


move(ctx, app, OFFICE, body=save_inside)
check("保存は本当の値（-30）で書き、保存の後はまた平常に見せる",
      written == [-30, ("after save", sanctuary.CALM)], written)
check("移動が終われば本当の値", lawfulness(app) == -30, lawfulness(app))
written[:] = []
save(lambda self: written.append(lawfulness(app)), app)
check("移動の外の保存は触らない", written == [-30], written)

print()
if failures:
    print("失敗 {} 件".format(len(failures)))
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
