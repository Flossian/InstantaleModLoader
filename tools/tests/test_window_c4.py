# -*- coding: utf-8 -*-
"""宿泊の窓口 `stays` と会話の窓口 `talks` をゲーム抜きで通す。

    python tools/tests/test_window_c4.py

  stays 包み   … 3つの種類が別々の execute を包む。START / REST は __init__ を読む包みも立つ（END は立たない）
  stays 引数   … call.months / call.quality は __init__ の引数から（文字列の月数も int に）。
                 控えの無いマネージャは months / quality の属性を読む。END はどちらも None
  stays 順番   … before は新しい順・after は古い順。always=True の after は orig が投げても来る
  stays 1枚    … 同じ世代では何本登録しても包みは1枚。世代が変われば立て直す
  talks 包み   … 4つの種類が別々の関数を包む
  talks 相手   … call.partner は orig の前の in_conversation（orig の中で下りても）。真偽値は None
  talks 引数   … call.choice_text は TURN / END で引数から、FINISH は None。app はマネージャから、無ければ find_app
  寿命         … off で下げた登録・用済みの ctx の登録は呼ばない。知らない種類は断る
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import gates, stays, talks, ui  # noqa: E402

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
        self.options = {}
        self.dead = False

    def superseded(self):
        return self.dead

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            self.options.setdefault(target, []).append(kw)
            return func
        return decorator


class App:
    def __init__(self, in_conversation=None):
        self.in_conversation = in_conversation


class Manager:
    """ゲームのマネージャの代わり。`__init__` は包みから呼ぶので、ここでは何もしない。"""

    def __init__(self, app=None):
        self.app = app


def build(ctx, target, cls, *args, **kwargs):
    """`__init__` の包みを通してマネージャを組む。"""
    obj = cls.__new__(cls)

    def orig(self, app, *rest, **kw):
        cls.__init__(self, app)

    wrappers = ctx.hooks.get(target) or []
    wrappers[-1](orig, obj, *args, **kwargs)
    return obj


def stays_wrapping():
    print("stays の包み")
    gates.reset_all()
    stays.reset()
    ctx = Ctx()
    seen = []
    stays.on("A", ctx, (stays.START, stays.REST, stays.END), after=lambda call: seen.append(call.kind))
    for kind in stays.KINDS:
        check("{} は {} を1枚包む".format(kind, stays.TARGETS[kind]),
              len(ctx.hooks.get(stays.TARGETS[kind]) or []) == 1, ctx.hooks.keys())
    check("START / REST の __init__ を読む包みが立つ（safe）",
          all(len(ctx.hooks.get(stays.INIT_TARGETS[kind]) or []) == 1
              and ctx.options[stays.INIT_TARGETS[kind]][0].get("safe") for kind in (stays.START, stays.REST)),
          ctx.options)
    check("END の __init__ は包まない", "__main__:VacationEndManager.__init__" not in ctx.hooks)

    stays.on("B", ctx, (stays.START,), before=lambda call: None)
    check("同じ世代では2本目でも1枚", len(ctx.hooks[stays.TARGETS[stays.START]]) == 1
          and len(ctx.hooks[stays.INIT_TARGETS[stays.START]]) == 1)
    check("持ち主の一覧", stays.owners(stays.START) == ["A", "B"], stays.owners(stays.START))

    ctx2 = Ctx(generation=2)
    stays.on("A", ctx2, (stays.START,), after=lambda call: None)
    check("世代が変われば立て直す", len(ctx2.hooks.get(stays.TARGETS[stays.START]) or []) == 1
          and len(ctx2.hooks.get(stays.INIT_TARGETS[stays.START]) or []) == 1, ctx2.hooks.keys())

    try:
        stays.on("C", ctx, ("vacation",), after=lambda call: None)
        check("知らない種類は断る", False)
    except ValueError:
        check("知らない種類は断る", True)


def stays_terms():
    print("stays の引数")
    gates.reset_all()
    stays.reset()
    ctx = Ctx()
    seen = []

    def before(call):
        seen.append(("before", call.kind, call.months, call.quality, call.choice_text, call.app))
        return "memo-" + call.kind

    def after(call):
        seen.append(("after", call.kind, call.memo, call.result))

    stays.on("A", ctx, stays.KINDS, before=before, after=after)
    app = App()
    start = build(ctx, stays.INIT_TARGETS[stays.START], Manager, app, 1, "private_room")
    check("控えはマネージャの属性", getattr(start, stays.TERMS_ATTR, None) == {"months": 1, "quality": "private_room"},
          getattr(start, stays.TERMS_ATTR, None))
    execute = ctx.hooks[stays.TARGETS[stays.START]][0]
    result = execute(lambda self, text: "started", start, "個室(100G)")
    check("戻り値は orig のまま", result == "started", result)
    check("START は月数・部屋・choice_text・app を渡す",
          seen[0] == ("before", stays.START, 1, "private_room", "個室(100G)", app), seen)
    check("before の戻り値が after の memo に", seen[1] == ("after", stays.START, "memo-start", "started"), seen)

    seen[:] = []
    rest = build(ctx, stays.INIT_TARGETS[stays.REST], Manager, app, months="3", quality="bunk")
    ctx.hooks[stays.TARGETS[stays.REST]][0](lambda self, choice_text: None, rest, choice_text="休養をとる")
    check("REST はキーワードの引数も読み、文字列の月数を int に",
          seen[0] == ("before", stays.REST, 3, "bunk", "休養をとる", app), seen)

    seen[:] = []
    bare = Manager(app)
    bare.months = 2
    bare.quality = "kennel"
    ctx.hooks[stays.TARGETS[stays.START]][0](lambda self, text: None, bare, "まだ宿泊する")
    check("控えの無いマネージャは months / quality の属性を読む",
          seen[0][2:4] == (2, "kennel"), seen)

    seen[:] = []
    end = Manager(app)
    end.months = 5
    ctx.hooks[stays.TARGETS[stays.END]][0](lambda self, text: None, end, "宿泊を終える")
    check("END は月数・部屋を渡さない", seen[0][2:4] == (None, None), seen)

    check("months_of の均し",
          [stays.months_of(v) for v in (1, "2", " 4 ", 3.0, 1.5, "1.0", True, None, "x")]
          == [1, 2, 4, 3, None, None, None, None, None])
    check("quality_of は空でない文字列だけ", [stays.quality_of(v) for v in ("bunk", "", None, 1)]
          == ["bunk", None, None, None])

    seen[:] = []
    nobody = Manager(None)
    original = ui.find_app
    found = App()
    ui.find_app = lambda: found
    try:
        ctx.hooks[stays.TARGETS[stays.END]][0](lambda self, text: None, nobody, "宿泊を終える")
    finally:
        ui.find_app = original
    check("app はマネージャに無ければ find_app", seen[0][5] is found, seen)


def stays_order():
    print("stays の順番と例外")
    gates.reset_all()
    stays.reset()
    ctx = Ctx()
    seen = []
    for name in ("315", "327"):
        stays.on(name, ctx, (stays.START,),
                 before=lambda call, n=name: seen.append("before " + n),
                 after=lambda call, n=name: seen.append("after {} {}".format(
                     n, type(call.error).__name__ if call.error else "-")),
                 always=True)
    stays.on("331", ctx, (stays.START,), after=lambda call: seen.append("after 331"))
    execute = ctx.hooks[stays.TARGETS[stays.START]][0]

    def orig(self, text):
        seen.append("orig")
        return "ok"

    execute(orig, Manager(App()), "x")
    check("before は新しい順・after は古い順",
          seen == ["before 327", "before 315", "orig", "after 315 -", "after 327 -", "after 331"], seen)

    seen[:] = []

    def failing(self, text):
        seen.append("orig")
        raise RuntimeError("game")

    raised = None
    try:
        execute(failing, Manager(App()), "x")
    except RuntimeError as exc:
        raised = exc
    check("orig の例外はそのまま流す", isinstance(raised, RuntimeError), raised)
    check("投げた回は always の after だけ（call.error）",
          seen == ["before 327", "before 315", "orig", "after 315 RuntimeError", "after 327 RuntimeError"], seen)

    seen[:] = []
    stays.off("327", (stays.START,))
    execute(orig, Manager(App()), "x")
    check("off で下げた登録は呼ばない", "before 327" not in seen and "after 327 -" not in seen, seen)


def talks_wrapping():
    print("talks の包み")
    gates.reset_all()
    talks.reset()
    ctx = Ctx()
    talks.on("A", ctx, talks.KINDS, after=lambda call: None)
    for kind in talks.KINDS:
        check("{} は {} を1枚包む".format(kind, talks.TARGETS[kind]),
              len(ctx.hooks.get(talks.TARGETS[kind]) or []) == 1, ctx.hooks.keys())
    check("包むのは4つだけ", len(ctx.hooks) == 4, ctx.hooks.keys())
    talks.on("B", ctx, (talks.TURN,), before=lambda call: None)
    check("同じ世代では2本目でも1枚", len(ctx.hooks[talks.TARGETS[talks.TURN]]) == 1)
    try:
        talks.on("C", ctx, ("start",), after=lambda call: None)
        check("知らない種類は断る", False)
    except ValueError:
        check("知らない種類は断る", True)


def talks_partner():
    print("talks の相手と引数")
    gates.reset_all()
    talks.reset()
    ctx = Ctx()
    seen = []

    def before(call):
        seen.append(("before", call.kind, call.partner, call.choice_text))
        return call.app.in_conversation

    def after(call):
        seen.append(("after", call.kind, call.partner, call.memo, call.app.in_conversation))

    talks.on("300", ctx, talks.KINDS, before=before, after=after)
    app = App("npc_7")
    manager = Manager(app)

    def finish(self):
        self.app.in_conversation = None     # 終了処理の中で下りる
        return "done"

    result = ctx.hooks[talks.TARGETS[talks.FINISH]][0](finish, manager)
    check("戻り値は orig のまま", result == "done", result)
    check("FINISH の相手は orig の前に読む（後で下りても after に残る）",
          seen == [("before", talks.FINISH, "npc_7", None),
                   ("after", talks.FINISH, "npc_7", "npc_7", None)], seen)

    seen[:] = []
    app.in_conversation = "npc_8"
    ctx.hooks[talks.TARGETS[talks.TURN]][0](lambda self, text: None, manager, "こんにちは")
    check("TURN は choice_text を渡す", seen[0] == ("before", talks.TURN, "npc_8", "こんにちは"), seen)

    seen[:] = []
    ctx.hooks[talks.TARGETS[talks.END]][0](lambda self, choice_text: None, manager, choice_text="会話を終了する")
    check("END はキーワードの choice_text も読む", seen[0] == ("before", talks.END, "npc_8", "会話を終了する"), seen)

    seen[:] = []
    app.in_conversation = True
    ctx.hooks[talks.TARGETS[talks.TURN_IN_QUEST]][0](lambda self, text: None, manager, "x")
    check("in_conversation が真偽値なら partner は None", seen[0][2] is None, seen)

    check("partner_of", [talks.partner_of(App(v)) for v in ("a", "", None, False, 3)] + [talks.partner_of(None)]
          == ["a", None, None, None, None, None])

    print("寿命")
    seen[:] = []
    old = Ctx()
    talks.on("old", old, (talks.FINISH,), after=lambda call: seen.append("old"))
    old.dead = True
    app.in_conversation = "npc_9"
    ctx.hooks[talks.TARGETS[talks.FINISH]][0](lambda self: None, manager)
    check("用済みの ctx の登録は呼ばない", "old" not in seen, seen)
    check("持ち主の一覧にも出ない", talks.owners(talks.FINISH) == ["300"], talks.owners(talks.FINISH))


def talks_always():
    print("talks の always")
    gates.reset_all()
    talks.reset()
    ctx = Ctx()
    seen = []
    talks.on("402", ctx, (talks.FINISH,), before=lambda call: seen.append("before 402"))
    talks.on("404", ctx, (talks.FINISH,), before=lambda call: "active",
             after=lambda call: seen.append("clear {}".format(call.memo)), always=True)

    def failing(self):
        raise TimeoutError("summary")

    raised = None
    try:
        ctx.hooks[talks.TARGETS[talks.FINISH]][0](failing, Manager(App("npc")))
    except TimeoutError as exc:
        raised = exc
    check("要約が投げても always の after は畳む", isinstance(raised, TimeoutError)
          and seen == ["before 402", "clear active"], seen)


def main():
    stays_wrapping()
    stays_terms()
    stays_order()
    talks_wrapping()
    talks_partner()
    talks_always()
    gates.reset_all()
    stays.reset()
    talks.reset()
    print()
    if failures:
        print("FAILED: {}".format(len(failures)))
        for name in failures:
            print("  - " + name)
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
