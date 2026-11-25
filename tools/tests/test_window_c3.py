# -*- coding: utf-8 -*-
"""窓口 `quest_ends.on_retire` と `loads` の TITLE・`on_settled`・`catch_up` をゲーム抜きで通す。

    python tools/tests/test_window_c3.py

  放棄     … 放棄する依頼の id と中身は orig の前に読む。順は before が新しい順・after が古い順。
             終わりとは別の関所。MOD の適用の間は包まない（外側に立てる）
  TITLE    … return_to_title を1枚だけ包む。KINDS は前からの3つのまま、off の既定は TITLE も下げる
  settled  … LOAD の orig が返ったら印、人物が揃った組み直しで1回だけ（新しい順）。用済みの登録は呼ばない。
             差し替えても印は引き継ぐ。choices の組み直しの包みから、登録の位置（provide の前か後か）で届く。
             catch_up は読み込み済みなら印を立てる
  catch_up … 登録の後に Kivy の Clock で1回。読み込み済みで、包みを通った読み込みが無ければ after（label "inject"）
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import choices, gates, loads, quest_ends, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx(object):
    """`wrap` で受けた包みを控える偽物。`dead` を立てると用済みになる。"""
    seq = 100

    def __init__(self):
        Ctx.seq += 1
        self.generation = Ctx.seq
        self.hooks = {}
        self.dead = False
        self.lines = []

    def superseded(self):
        return self.dead

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks.setdefault(target, []).append(fn)
            return fn
        return decorator

    def log(self, line, level="INFO"):
        self.lines.append(line)


class World(object):
    def __init__(self, count):
        self.characters = {str(n): object() for n in range(count)}
        self.areas = {"1": object()}


class App(object):
    def __init__(self, count=3):
        self.world = World(count)
        self.player = object()
        self.in_conversation = None
        self.buttons = []
        self.current_quest_data = {"id": 9, "quest_title": "題"}


class Manager(object):
    def __init__(self, app):
        self.app = app


def fresh():
    gates.reset_all()
    gates.defer_outermost(False)
    loads.reset()
    quest_ends.reset()
    choices.reset()


# ---------------------------------------------------------------- 放棄
def retire():
    print("[放棄]")
    fresh()
    seen = []
    ctx = Ctx()
    for name in ("307_x", "325_x", "344_x"):
        quest_ends.on_retire(
            name, ctx,
            before=lambda call, n=name: seen.append(("before", n, call.quest_id)) or n,
            after=lambda call, n=name: seen.append(("after", n, call.memo, call.quest_id, call.result,
                                                    call.kind, call.quest and call.quest.get("quest_title"))))
    hooks = ctx.hooks.get(quest_ends.RETIRE_TARGET, [])
    check("放棄の関所は1枚", len(hooks) == 1, ctx.hooks)
    check("終わりの関所は立てない", quest_ends.TARGET not in ctx.hooks, ctx.hooks)
    check("1つの世代に1枚（install_retire の戻り値）",
          quest_ends.install_retire(ctx) == [quest_ends.RETIRE_TARGET] and len(hooks) == 1)
    check("持ち主の一覧（古い順）", quest_ends.retire_owners() == ["307_x", "325_x", "344_x"],
          quest_ends.retire_owners())
    check("終わりの持ち主とは別", quest_ends.owners() == [], quest_ends.owners())

    def orig(self):
        self.app.current_quest_data = None      # ゲームは放棄の中で今の依頼を片付ける
        return "retired"

    result = hooks[0](orig, Manager(App()))
    check("orig の戻り値を返す", result == "retired", result)
    check("before は新しい順で、放棄する依頼の id を orig の前に読む",
          seen[:3] == [("before", "344_x", "9"), ("before", "325_x", "9"), ("before", "307_x", "9")], seen[:3])
    check("after は古い順で、memo・戻り値・中身が渡る",
          seen[3:] == [("after", n, n, "9", "retired", "quest_retire", "題")
                       for n in ("307_x", "325_x", "344_x")], seen[3:])

    print("[放棄: 依頼が無い回・投げた回]")
    seen[:] = []
    empty = App()
    empty.current_quest_data = None
    hooks[0](lambda self: None, Manager(empty))
    check("依頼が無ければ quest_id は None", ("before", "307_x", None) in seen, seen)
    seen[:] = []
    quest_ends.on_retire("336_x", ctx, after=lambda call: seen.append(("after", "336", type(call.error).__name__)),
                         always=True)
    try:
        hooks[0](lambda self: (_ for _ in ()).throw(RuntimeError("boom")), Manager(App()))
    except RuntimeError:
        raised = True
    else:
        raised = False
    check("orig の例外はそのまま流す", raised)
    check("always=True の after だけ来る",
          [row for row in seen if row[0] == "after"] == [("after", "336", "RuntimeError")], seen)

    print("[放棄: app の引き方・下げる]")
    seen[:] = []
    found = App()
    real_find = ui.find_app
    ui.find_app = lambda: found
    try:
        hooks[0](lambda self: None, type("QR", (), {})())
    finally:
        ui.find_app = real_find
    check("self.app が無ければ ui.find_app()", ("before", "307_x", "9") in seen, seen)
    for name in ("307_x", "325_x", "344_x", "336_x"):
        check("off_retire で下がる: " + name, quest_ends.off_retire(name))
    seen[:] = []
    check("下げた登録は呼ばない", hooks[0](lambda self: "x", Manager(App())) == "x" and seen == [])

    print("[放棄: 外側に立てる]")
    fresh()
    later = Ctx()
    gates.defer_outermost(True)
    try:
        quest_ends.on_retire("307_x", later, after=lambda call: None)
        check("MOD の適用の間は包まない", quest_ends.RETIRE_TARGET not in later.hooks, later.hooks)
    finally:
        gates.defer_outermost(False)
    check("適用の後の install_retire で立つ",
          quest_ends.install_retire(later) == [quest_ends.RETIRE_TARGET]
          and len(later.hooks.get(quest_ends.RETIRE_TARGET, [])) == 1)
    check("reset で放棄の登録も消える", (quest_ends.reset() or quest_ends.retire_owners()) == [])
    check("gates.reset_all の対象", quest_ends.RETIRE_GATE in gates._GATES)


# ---------------------------------------------------------------- TITLE
def title():
    print("[TITLE]")
    fresh()
    seen = []
    ctx = Ctx()
    loads.on("401_x", ctx, (loads.TITLE,), before=lambda call: seen.append(("before 401", call.label)))
    loads.on("404_x", ctx, (loads.TITLE,), before=lambda call: seen.append(("before 404", call.app is call.obj)))
    loads.on("333_x:buttons", ctx, (loads.TITLE, loads.LOAD), after=lambda call: seen.append(("after 333", call.kind)))
    check("KINDS は前からの3つ", loads.KINDS == (loads.LOAD, loads.NEW_GAME, loads.WORLD), loads.KINDS)
    check("ALL_KINDS に TITLE", loads.ALL_KINDS == loads.KINDS + (loads.TITLE,), loads.ALL_KINDS)
    hooks = ctx.hooks.get(loads.TARGETS[loads.TITLE], [])
    check("return_to_title を1枚だけ包む", len(hooks) == 1 and loads.TARGETS[loads.TITLE]
          == "__main__:InstantaleApp.return_to_title", ctx.hooks)
    app = App()
    result = hooks[0](lambda self, *a, **k: ("title", a, k), app, 1, flag=True)
    check("引数は受けたまま orig へ・戻り値もそのまま", result == ("title", (1,), {"flag": True}), result)
    check("before は新しい順・after は古い順",
          seen == [("before 404", True), ("before 401", "return_to_title"), ("after 333", loads.TITLE)], seen)
    check("持ち主の一覧", loads.owners(loads.TITLE) == ["401_x", "404_x", "333_x:buttons"], loads.owners(loads.TITLE))
    check("TITLE は settled の印を立てない", loads.armed_owners() == [])
    loads.off("333_x:buttons")
    check("off の既定は TITLE も下げる",
          loads.owners(loads.TITLE) == ["401_x", "404_x"] and loads.owners(loads.LOAD) == [],
          (loads.owners(loads.TITLE), loads.owners(loads.LOAD)))
    try:
        loads.on("x", ctx, ("nope",), after=lambda call: None)
    except ValueError:
        check("知らない種類は ValueError", True)
    else:
        check("知らない種類は ValueError", False)


# ---------------------------------------------------------------- settled
def settled():
    print("[settled]")
    fresh()
    seen = []
    ctx = Ctx()
    loads.on("326_x", ctx, (loads.LOAD,), after=lambda call: seen.append("326 on_load"))
    loads.on_settled("326_x", ctx, lambda call: seen.append(("326", call.label, call.kind, call.app)),
                     kinds=(loads.LOAD, loads.NEW_GAME))
    loads.on_settled("338_x", ctx, lambda call: seen.append(("338", call.kind)))
    check("settled の持ち主（古い順）", loads.settled_owners() == ["326_x", "338_x"], loads.settled_owners())
    check("on の登録とは別（同じ持ち主でよい）", loads.owners(loads.LOAD) == ["326_x"], loads.owners(loads.LOAD))
    check("LOAD と NEW_GAME を包む",
          len(ctx.hooks.get(loads.TARGETS[loads.LOAD], [])) == 1
          and len(ctx.hooks.get(loads.TARGETS[loads.NEW_GAME], [])) == 1, list(ctx.hooks))
    check("登録ごとに <owner>:settled で choices に預ける",
          choices.providers() == ["326_x:settled", "338_x:settled"], choices.providers())

    app = App(count=3)
    load = ctx.hooks[loads.TARGETS[loads.LOAD]][0]
    choices._call_refresh(app, [])
    check("ロードの前は呼ばない", [row for row in seen if row != "326 on_load"] == [], seen)
    load(lambda self: "loaded", app)
    check("LOAD の after の後に印が立つ", seen == ["326 on_load"] and loads.armed_owners() == ["326_x", "338_x"],
          (seen, loads.armed_owners()))

    seen[:] = []
    one = App(count=1)
    choices._call_refresh(one, [])
    check("人物が1人なら待つ", seen == [] and loads.armed_owners() == ["326_x", "338_x"], seen)
    talking = App(count=3)
    talking.in_conversation = "99"
    choices._call_refresh(talking, [])
    check("会話の相手が人物に居なければ待つ（choices.ready）", seen == [], seen)

    choices._call_refresh(app, [])
    check("揃ったら新しい順に1回ずつ",
          seen == [("338", loads.LOAD), ("326", "settled", loads.LOAD, app)], seen)
    check("印は下りる", loads.armed_owners() == [])
    seen[:] = []
    choices._call_refresh(app, [])
    check("2回目の組み直しでは呼ばない", seen == [], seen)

    print("[settled: 投げた回・NEW_GAME・用済み]")
    try:
        load(lambda self: (_ for _ in ()).throw(RuntimeError("boom")), app)
    except RuntimeError:
        pass
    check("orig が投げたら印を立てない", loads.armed_owners() == [], loads.armed_owners())
    new_game = ctx.hooks[loads.TARGETS[loads.NEW_GAME]][0]
    real_retire = loads.retire_previous
    loads.retire_previous = lambda _ctx, _world: 0
    try:
        new_game(lambda self, *a, **k: None, app, "W")
    finally:
        loads.retire_previous = real_retire
    check("NEW_GAME を待つ登録にだけ印", loads.armed_owners() == ["326_x"], loads.armed_owners())

    broken = []
    loads.on_settled("911_x", ctx, lambda call: broken.append(1) or (_ for _ in ()).throw(ValueError("bad")),
                     write=lambda line: broken.append(line))
    load(lambda self: None, app)
    seen[:] = []
    choices._call_refresh(app, [])
    check("前後の処理の例外は握って残りを呼ぶ",
          broken[:1] == [1] and any("WARN loads.settled" in str(row) for row in broken)
          and ("326", "settled", loads.LOAD, app) in seen and ("338", loads.LOAD) in seen, (broken, seen))
    loads.off_settled("911_x")

    newer = Ctx()
    loads.on_settled("338_x", newer, lambda call: seen.append(("338 new", call.kind)))
    load(lambda self: None, app)
    ctx.dead = True
    seen[:] = []
    check("差し替えた持ち主は印を引き継ぐ", "338_x" in loads.armed_owners(), loads.armed_owners())
    choices._call_refresh(app, [])
    check("用済みの ctx の登録は呼ばず、差し替えた登録を呼ぶ", seen == [("338 new", loads.LOAD)], seen)
    check("用済みの登録は持ち主の一覧に出ない", loads.settled_owners() == ["338_x"], loads.settled_owners())
    check("off_settled で下がる", loads.off_settled("338_x") and loads.settled_owners() == [])

    print("[settled: 当て直しをまたぐ印]")
    fresh()
    seen[:] = []
    first = Ctx()
    loads.on_settled("326_x", first, lambda call: seen.append("first"))
    first.hooks[loads.TARGETS[loads.LOAD]][0](lambda self: None, app)
    first.dead = True                      # ロードが返った後に当て直した
    second = Ctx()
    loads.on_settled("326_x", second, lambda call: seen.append("second"))
    choices._call_refresh(app, [])
    check("新しい登録が揃った後に1回来る", seen == ["second"], seen)

    print("[settled: choices の組み直しの包みから]")
    for settled_first in (True, False):
        fresh()
        seen[:] = []
        ctx = Ctx()

        def provide():
            choices.provide(ctx, None, refresh=lambda a, b: seen.append("mod refresh"), owner="326_x")

        if settled_first:
            provide()
        loads.on_settled("326_x", ctx, lambda call: seen.append("settled"))
        if not settled_first:
            provide()
        ctx.hooks[loads.TARGETS[loads.LOAD]][0](lambda self: None, app)
        refresh = ctx.hooks[choices.REFRESH_TARGET][0]
        refresh(lambda self, *a, **k: None, app)
        if settled_first:
            check("choices.provide の後で on_settled なら、その MOD の refresh より先",
                  seen == ["settled", "mod refresh"], seen)
        else:
            check("choices.provide の前で on_settled なら、その MOD の refresh より後",
                  seen == ["mod refresh", "settled"], seen)
    check("off_settled は choices の見張りも下げる",
          loads.off_settled("326_x") and choices.providers() == ["326_x"], choices.providers())

    print("[settled: catch_up・引数の検査]")
    fresh()
    ctx = Ctx()
    real_find = ui.find_app
    ui.find_app = lambda: app
    try:
        loads.on_settled("339_x", ctx, lambda call: seen.append("caught"), catch_up=True)
        check("読み込み済みなら印を立てる", loads.armed_owners() == ["339_x"], loads.armed_owners())
        empty = App()
        empty.player = None
        ui.find_app = lambda: empty
        loads.on_settled("340_x", ctx, lambda call: None, catch_up=True)
        check("読み込み前なら印を立てない", loads.armed_owners() == ["339_x"], loads.armed_owners())
    finally:
        ui.find_app = real_find
    for kinds in ((loads.WORLD,), (loads.LOAD, loads.TITLE)):
        try:
            loads.on_settled("x", ctx, lambda call: None, kinds=kinds)
        except ValueError:
            check("LOAD / NEW_GAME 以外は ValueError: {}".format(kinds), True)
        else:
            check("LOAD / NEW_GAME 以外は ValueError: {}".format(kinds), False)
    try:
        loads.on_settled("x", ctx, "not callable")
    except TypeError:
        check("呼べないものは TypeError", True)
    else:
        check("呼べないものは TypeError", False)


# ---------------------------------------------------------------- catch_up
class FakeClock(object):
    def __init__(self):
        self.queue = []

    def schedule_once(self, fn, delay=0):
        self.queue.append((fn, delay))

    def run(self):
        queue, self.queue = self.queue, []
        for fn, _delay in queue:
            fn(0)


def catch_up():
    print("[catch_up]")
    fresh()
    seen = []
    ctx = Ctx()
    loads.on("317_x", ctx, (loads.LOAD, loads.NEW_GAME), after=lambda call: seen.append("no kivy"), catch_up=True)
    check("Kivy が無ければ予約しない（登録はする）", loads.owners(loads.LOAD) == ["317_x"], loads.owners(loads.LOAD))

    clock = FakeClock()
    saved = {name: sys.modules.get(name) for name in ("kivy", "kivy.clock")}
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    kivy.clock = kivy_clock
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    app = App()
    real_find = ui.find_app
    ui.find_app = lambda: app
    try:
        fresh()
        loads.on("317_x", ctx, (loads.LOAD, loads.NEW_GAME), before=lambda call: seen.append("before"),
                 after=lambda call: seen.append(("317", call.label, call.kind, call.app, call.memo)),
                 catch_up=True)
        check("CATCH_UP_DELAY 秒後に1回予約", [delay for _fn, delay in clock.queue] == [loads.CATCH_UP_DELAY],
              clock.queue)
        clock.run()
        check("読み込み済みなら after を1回（label inject・before は呼ばない）",
              seen == [("317", "inject", loads.LOAD, app, None)], seen)

        seen[:] = []
        loads.on("346_x", ctx, (loads.NEW_GAME,), after=lambda call: seen.append(("346", call.kind)), catch_up=True)
        clock.run()
        check("NEW_GAME だけなら call.kind は NEW_GAME", seen == [("346", loads.NEW_GAME)], seen)

        seen[:] = []
        loads.on("317_x", ctx, (loads.LOAD,), after=lambda call: seen.append(("317", call.label)), catch_up=True)
        ctx.hooks[loads.TARGETS[loads.LOAD]][0](lambda self: None, app)
        seen[:] = []
        clock.run()
        check("登録の後に包みを通った読み込みがあれば拾い直さない", seen == [], seen)

        loads.on("317_x", ctx, (loads.LOAD,), after=lambda call: seen.append("old"), catch_up=True)
        loads.on("317_x", ctx, (loads.LOAD,), after=lambda call: seen.append("new"))
        clock.run()
        check("差し替えた登録の予約は捨てる", seen == [], seen)

        dead = Ctx()
        loads.on("318_x", dead, (loads.LOAD,), after=lambda call: seen.append("dead"), catch_up=True)
        dead.dead = True
        clock.run()
        check("用済みの登録の予約は呼ばない", seen == [], seen)

        empty = App()
        empty.world.areas = {}
        ui.find_app = lambda: empty
        loads.on("319_x", ctx, (loads.LOAD,), after=lambda call: seen.append("empty"), catch_up=True)
        clock.run()
        check("世界が読み込まれていなければ呼ばない", seen == [], seen)
        ui.find_app = lambda: app

        def boom(call):
            raise RuntimeError("bad")
        loads.on("320_x", ctx, (loads.LOAD,), after=boom, catch_up=True,
                 write=lambda line: seen.append(line))
        clock.run()
        check("after の例外は握って記録する", any("WARN loads.load" in str(row) for row in seen), seen)
    finally:
        ui.find_app = real_find
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    for kinds, after in (((loads.WORLD,), lambda call: None), ((loads.TITLE,), lambda call: None),
                         ((loads.LOAD,), None)):
        try:
            loads.on("x", ctx, kinds, before=lambda call: None, after=after, catch_up=True)
        except ValueError:
            check("catch_up は LOAD / NEW_GAME と after が要る: {}".format(kinds), True)
        else:
            check("catch_up は LOAD / NEW_GAME と after が要る: {}".format(kinds), False)
    check("撥ねた登録は残らない", "x" not in loads.owners(loads.WORLD) + loads.owners(loads.LOAD))


retire()
title()
settled()
catch_up()
fresh()

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
