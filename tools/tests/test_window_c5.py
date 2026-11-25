# -*- coding: utf-8 -*-
"""ローダの窓口 `arrivals` の街の移動の組み立て（`AREA_SETUP`）と第一声の予約（`opening`）をゲーム抜きで通す。

    python tools/tests/test_window_c5.py

  組み立て … 行き先と手段を orig の前に読んで call に載せ、マネージャに控える。引数は受け取ったまま渡す
  順番     … before は登録の新しい順、after は古い順（gates の決まり）
  街の移動 … AREA を登録すると組み立ての包みも立ち、AREA の call に行き先と手段が載る
  第一声   … 相手の id が合う予約だけ差し替え、相手違い・期限切れ・id が読めない予約は捨てる
  片付け   … 予約は次の conversation_starter で全部片付く（使っても使わなくても）
  1行目    … keep_rest なら1行目だけ替えて2行目以降を残す
  関数     … text_or_fn が関数なら差し替えの時に呼ぶ。None なら差し替えない
  後始末   … done は orig が返った後、dropped は捨てたときに理由つきで来る
  外側     … 適用の間（defer_outermost）は包まず、ローダが後で立てる。1つの世代につき1枚
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import arrivals, gates  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx:
    """`wrap` で受けた包みを控える偽物。"""

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


class Manager:
    """`AreaMoveManager` の代わり。素の `__init__` は (app, target_area_id, mode)。"""


def real_init(self, app, target_area_id, mode):
    self.app = app
    self.target = target_area_id
    self.mode = mode


class Character:
    def __init__(self, npc_id, name="誰か"):
        self.id = npc_id
        self.name = name


def area_setup():
    print("街の移動の組み立て")
    arrivals.reset()
    seen = []
    ctx = Ctx(generation=11)
    arrivals.on("307_x", ctx, (arrivals.AREA_SETUP,),
                before=lambda call: seen.append(("307 before", call.target_area_id, call.mode, call.app))
                or "memo307",
                after=lambda call: seen.append(("307 after", call.memo, call.obj.target)))
    arrivals.on("325_x", ctx, (arrivals.AREA_SETUP,),
                before=lambda call: seen.append(("325 before", call.target_area_id, call.mode)))
    hooks = ctx.hooks.get(arrivals.AREA_SETUP_TARGET, [])
    check("組み立ては1枚だけ包む", len(hooks) == 1, len(hooks))
    app = object()
    manager = Manager()
    hooks[0](real_init, manager, app, 5, "walk")
    check("前は新しい順・後は古い順。行き先と手段は文字列で orig の前に読む",
          seen == [("325 before", "5", "walk"), ("307 before", "5", "walk", app),
                   ("307 after", "memo307", 5)], seen)
    check("引数は受け取ったまま orig へ渡る", (manager.app, manager.target, manager.mode) == (app, 5, "walk"))
    check("マネージャに行き先と手段を控える",
          arrivals.move_of(manager) == {"target_area_id": "5", "mode": "walk"}, arrivals.move_of(manager))

    seen[:] = []
    manager = Manager()
    hooks[0](real_init, manager, app=app, target_area_id="9", mode="carriage")
    check("名前で来ても読む", seen[0] == ("325 before", "9", "carriage"), seen)

    seen[:] = []
    arrivals.on("307_x", ctx, (arrivals.AREA_SETUP,), before=lambda call: 1 / 0)
    manager = Manager()
    hooks[0](real_init, manager, app, 3, "walk")
    check("前後の処理が投げても orig と残りは止まらない",
          manager.target == 3 and seen == [("325 before", "3", "walk")], seen)

    arrivals.off("307_x")
    arrivals.off("325_x")
    check("off は既定で組み立ても下げる", arrivals.owners(arrivals.AREA_SETUP) == [],
          arrivals.owners(arrivals.AREA_SETUP))
    check("同じ世代で登録し直しても包みは増えない",
          len(ctx.hooks.get(arrivals.AREA_SETUP_TARGET, [])) == 1)
    manager = Manager()
    ctx.hooks[arrivals.AREA_SETUP_TARGET][0](real_init, manager, app, 4, "walk")
    check("登録が無くても控えは残す", arrivals.move_of(manager) == {"target_area_id": "4", "mode": "walk"})

    print("街の移動（AREA）に行き先と手段")
    arrivals.reset()
    seen[:] = []
    ctx = Ctx(generation=12)
    arrivals.on("314_x", ctx, (arrivals.AREA,),
                before=lambda call: seen.append(("314 before", call.target_area_id, call.mode)))
    check("AREA の登録で組み立ての包みも立つ",
          len(ctx.hooks.get(arrivals.AREA_SETUP_TARGET, [])) == 1
          and len(ctx.hooks.get(arrivals.AREA_TARGET, [])) == 1, sorted(ctx.hooks))
    manager = Manager()
    ctx.hooks[arrivals.AREA_SETUP_TARGET][0](real_init, manager, app, 7, "walk")
    ctx.hooks[arrivals.AREA_TARGET][0](lambda self: "moved", manager)
    check("AREA の call に組み立ての行き先と手段が載る", seen == [("314 before", "7", "walk")], seen)
    seen[:] = []
    ctx.hooks[arrivals.AREA_TARGET][0](lambda self: "moved", Manager())
    check("控えの無いマネージャなら None", seen == [("314 before", None, None)], seen)
    check("控えの無いマネージャの move_of は None", arrivals.move_of(Manager()) is None)


def starter_args(npc_id, content="<行動: 話しかける>", keyword=False):
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": content}]
    character = Character(npc_id) if npc_id is not None else object()
    if keyword:
        return (), {"messages": messages, "world": None, "player": None, "character_instance": character}
    return (messages, None, None, character), {}


def capture(store):
    def orig(*args, **kwargs):
        messages = kwargs.get("messages", args[0] if args else None)
        store.append(messages)
        return "reply"
    return orig


def opening_window():
    print("第一声の予約")
    arrivals.reset()
    ctx = Ctx(generation=21)
    gates.defer_outermost(True)
    try:
        check("適用の間は包まない", arrivals.install_opening(ctx) == [] and not ctx.hooks)
    finally:
        gates.defer_outermost(False)
    lines = []
    check("ローダが後で外側に立てる", arrivals.install_opening(ctx, lines.append) == [arrivals.OPENING_TARGET])
    check("同じ世代では1枚", arrivals.install_opening(ctx) == [arrivals.OPENING_TARGET]
          and len(ctx.hooks[arrivals.OPENING_TARGET]) == 1)
    wrapper = ctx.hooks[arrivals.OPENING_TARGET][0]

    passed = []
    args, kwargs = starter_args("n1")
    check("予約が無ければ素通し", wrapper(capture(passed), *args, **kwargs) == "reply"
          and passed[-1] is args[0])

    done, dropped, log = [], [], []
    app = object()
    check("予約できる", arrivals.opening(app, "339_x", "n1", "勝負だ", done=done.append,
                                    dropped=dropped.append, write=log.append))
    check("予約の一覧", arrivals.openings() == [("339_x", "n1")], arrivals.openings())
    args, kwargs = starter_args("n1", "<行動: 話しかける>\n状況: 夜")
    result = wrapper(capture(passed), *args, **kwargs)
    sent = passed[-1]
    check("相手が合えば最後の1件をまるごと替える",
          sent[-1] == {"role": "user", "content": "勝負だ"} and sent[0] == args[0][0], sent)
    check("渡した messages の元は書き換えない", args[0][-1]["content"] == "<行動: 話しかける>\n状況: 夜")
    check("done は orig の後に戻り値つきで来る", len(done) == 1 and done[0].result == "reply"
          and done[0].text == "勝負だ" and done[0].owner == "339_x" and done[0].app is app, done)
    check("戻り値は orig のまま", result == "reply")
    check("使った予約は片付く", arrivals.openings() == [] and not dropped, (arrivals.openings(), dropped))
    check("差し替えをログに書く", any("339_x" in line and "勝負だ" in line for line in log), log)

    print("相手違い（339 の不具合の型）")
    done[:] = []
    arrivals.opening(app, "339_x", "hunter", "勝負だ", done=done.append, dropped=dropped.append)
    args, kwargs = starter_args("shopkeeper")
    wrapper(capture(passed), *args, **kwargs)
    check("別の相手の第一声は替えない", passed[-1][-1]["content"] == "<行動: 話しかける>", passed[-1])
    check("相手違いの予約は捨てて理由を渡す", not done and len(dropped) == 1
          and dropped[0].reason == arrivals.DROP_PARTNER and dropped[0].partner == "shopkeeper", dropped)
    check("捨てた予約は残らない", arrivals.openings() == [])
    args, kwargs = starter_args("hunter")
    wrapper(capture(passed), *args, **kwargs)
    check("後から同じ相手と話しても替えない", passed[-1][-1]["content"] == "<行動: 話しかける>")

    print("相手の id が読めない")
    dropped[:] = []
    arrivals.opening(app, "300_x", "n2", "やあ", dropped=dropped.append)
    args, kwargs = starter_args(None)
    wrapper(capture(passed), *args, **kwargs)
    check("id が読めなければ替えずに捨てる", passed[-1][-1]["content"] == "<行動: 話しかける>"
          and dropped and dropped[0].reason == arrivals.DROP_PARTNER, dropped)

    print("期限切れ")
    dropped[:] = []
    arrivals.opening(app, "911_x", "n3", "よう", ttl=60.0, dropped=dropped.append)
    sys_store = getattr(sys, arrivals._OPENING_ATTR)
    sys_store["911_x"]["at"] -= 61.0
    args, kwargs = starter_args("n3")
    wrapper(capture(passed), *args, **kwargs)
    check("期限を過ぎた予約は替えずに捨てる", passed[-1][-1]["content"] == "<行動: 話しかける>"
          and dropped and dropped[0].reason == arrivals.DROP_STALE, dropped)
    dropped[:] = []
    arrivals.opening(app, "911_x", "n3", "よう", ttl=60.0, dropped=dropped.append)
    sys_store["911_x"]["at"] -= 61.0
    arrivals.opening(app, "300_x", "n4", "やあ")
    check("次の予約の時にも期限切れを捨てる", arrivals.openings() == [("300_x", "n4")]
          and dropped and dropped[0].reason == arrivals.DROP_STALE, (arrivals.openings(), dropped))
    arrivals.cancel_opening("300_x")

    print("1行目だけ（keep_rest）・名前で来た引数・関数")
    arrivals.opening(app, "300_x", "n5", lambda call: "<状況: {}が声をかけた>".format(call.npc_id),
                     keep_rest=True)
    args, kwargs = starter_args("n5", "<行動: 話しかける>\n仲間の様子", keyword=True)
    wrapper(capture(passed), *args, **kwargs)
    check("1行目だけ替えて2行目以降を残す", passed[-1][-1]["content"] == "<状況: n5が声をかけた>\n仲間の様子",
          passed[-1][-1])
    arrivals.opening(app, "300_x", "n5", lambda call: None)
    args, kwargs = starter_args("n5")
    wrapper(capture(passed), *args, **kwargs)
    check("関数が None なら替えない", passed[-1][-1]["content"] == "<行動: 話しかける>")
    check("それでも予約は片付く", arrivals.openings() == [])

    print("同じ相手に2つ・取り消し")
    dropped[:] = []
    arrivals.opening(app, "300_x", "n6", "古い", dropped=dropped.append)
    arrivals.opening(app, "339_x", "n6", "新しい", dropped=dropped.append)
    sys_store["339_x"]["at"] = sys_store["300_x"]["at"]      # 時刻が同じでも予約の順で決まる
    args, kwargs = starter_args("n6")
    wrapper(capture(passed), *args, **kwargs)
    check("同じ相手なら新しい予約を使い、古い方は捨てる",
          passed[-1][-1]["content"] == "新しい" and [c.owner for c in dropped] == ["300_x"]
          and dropped[0].reason == arrivals.DROP_REPLACED, (passed[-1][-1], dropped))
    arrivals.opening(app, "339_x", "n7", "x")
    check("取り消しは True", arrivals.cancel_opening("339_x") and arrivals.openings() == [])
    check("無い予約の取り消しは False", not arrivals.cancel_opening("339_x"))

    print("受け付けない予約・投げる後始末")
    check("相手の id が空なら受けない", not arrivals.opening(app, "x", "", "a") and arrivals.openings() == [])
    check("文でも関数でもなければ受けない", not arrivals.opening(app, "x", "n8", 3))
    arrivals.opening(app, "339_x", "n9", "勝負だ", done=lambda call: 1 / 0)
    args, kwargs = starter_args("n9")
    check("done が投げても戻り値は返る", wrapper(capture(passed), *args, **kwargs) == "reply")
    arrivals.opening(app, "339_x", "n9", lambda call: 1 / 0)
    args, kwargs = starter_args("n9")
    check("文の関数が投げたら替えずに通す", wrapper(capture(passed), *args, **kwargs) == "reply"
          and passed[-1][-1]["content"] == "<行動: 話しかける>")
    arrivals.opening(app, "339_x", "n9", "勝負だ")
    messages = [{"role": "user", "content": "a"}, "壊れた"]
    wrapper(capture(passed), messages, None, None, Character("n9"))
    check("最後の1件が辞書でなければ替えない", passed[-1] is messages and arrivals.openings() == [])

    arrivals.reset()
    check("reset で予約と包みの印を忘れる", arrivals.openings() == [] and arrivals.opening_installed() is None)


def main():
    area_setup()
    opening_window()
    arrivals.reset()
    print("FAILED: {}".format(failures) if failures else "OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
