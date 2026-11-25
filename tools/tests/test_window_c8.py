# -*- coding: utf-8 -*-
"""ローダの窓口 `twin_windows`（持ち物を2列で並べる画面の前後）をゲーム抜きで通す。

    python tools/tests/test_window_c8.py

  開く     … 左右の持ち主・見出し・場面名を位置でもキーワードでも読み、渡し方はそのまま orig へ
  動かす   … 動かす前の self.inventory を orig の前に読んで渡す（orig が書き換えても読める）
  閉じる   … ボタンの経路は app が self。順は before が新しい順、after が古い順
  外側     … 外側を押した後、次のフレームで2列の画面が消えていれば HIDDEN が1回来る
  包み     … 1つの世代につき1枚。HIDDEN だけの登録でも外側の関所が立つ。登録が無ければ何も読まない
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import gates, twin_windows, ui  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("ok  " if ok else "FAIL", name, "  " + str(detail) if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Ctx(object):
    seq = 0

    def __init__(self):
        Ctx.seq += 1
        self.generation = Ctx.seq
        self.hooks = {}
        self.gone = False

    def superseded(self):
        return self.gone

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator


class App(object):
    def __init__(self):
        self.is_popup_window_opened = False


class Hud(object):
    def __init__(self):
        self.visible_twin_inventory_data = {"TorF": True, "situation": "shop"}


class Grid(object):
    def __init__(self, name):
        self.name = name


class Widget(object):
    def __init__(self, inventory, item):
        self.inventory = inventory
        self.item_instance = item


app = App()
hud = Hud()
real_find_app, real_find_hud = ui.find_app, ui.find_hud
ui.find_app = lambda: app
ui.find_hud = lambda _app: hud

try:
    gates.reset_all()
    twin_windows.reset()
    ctx = Ctx()
    seen = []

    # ------------------------------------------------------------------ 開く
    print("[開く]")

    def open_before(name):
        def fn(call):
            seen.append(("before", name, call.left, call.right, call.label_text, call.situation))
            return "memo-" + name
        return fn

    def open_after(name):
        def fn(call):
            seen.append(("after", name, call.memo, call.result))
        return fn

    twin_windows.on("a", ctx, (twin_windows.OPEN,), before=open_before("a"), after=open_after("a"))
    twin_windows.on("b", ctx, twin_windows.OPEN, before=open_before("b"), after=open_after("b"))
    hook = ctx.hooks[twin_windows.TARGETS[twin_windows.OPEN]]
    check("1つの世代に1枚", twin_windows.install(ctx, kinds=(twin_windows.OPEN,))
          == [twin_windows.TARGETS[twin_windows.OPEN]] and len(ctx.hooks) == 1)
    got = []

    def open_orig(self, *args, **kwargs):
        got.append((args, kwargs))
        return "opened"

    result = hook(open_orig, app, "shopkeeper", "player", "店", "shop")
    check("orig の戻り値を返す", result == "opened")
    check("before は新しい順で、位置の引数を読む",
          seen[:2] == [("before", "b", "shopkeeper", "player", "店", "shop"),
                       ("before", "a", "shopkeeper", "player", "店", "shop")], seen[:2])
    check("after は古い順で、memo と戻り値が渡る",
          seen[2:] == [("after", "a", "memo-a", "opened"), ("after", "b", "memo-b", "opened")], seen[2:])
    seen.clear()
    got.clear()
    hook(open_orig, app, left_inventory_obtainer="L", right_inventory_obtainer="R",
         left_label_text="見出し", situation="twin")
    check("キーワードの引数も読む", seen[0] == ("before", "b", "L", "R", "見出し", "twin"), seen[:1])
    check("渡し方はそのまま orig へ（キーワードはキーワードのまま）",
          got == [((), {"left_inventory_obtainer": "L", "right_inventory_obtainer": "R",
                        "left_label_text": "見出し", "situation": "twin"})], got)

    # ------------------------------------------------------------------ 動かす
    print("[動かす]")
    seen.clear()
    old_grid, new_grid = Grid("old"), Grid("new")

    def move_before(call):
        seen.append(("before", call.old_inventory.name, call.new_inventory.name, call.item, call.app is app))
        return call.old_inventory

    def move_after(call):
        seen.append(("after", call.memo.name, call.widget.inventory.name))

    twin_windows.on("a", ctx, (twin_windows.MOVE,), before=move_before, after=move_after)
    move_hook = ctx.hooks[twin_windows.TARGETS[twin_windows.MOVE]]

    def move_orig(self, new_inventory):
        self.inventory = new_inventory     # ゲームは移動先へ書き換える
        return None

    sword = Grid("剣")      # 品の代わり（中身は見ない）
    widget = Widget(old_grid, sword)
    move_hook(move_orig, widget, new_grid)
    check("動かす前の inventory を orig の前に読み、品と app を渡す",
          seen[0] == ("before", "old", "new", sword, True), seen[:1])
    check("after は orig の後（部品は移動先を指す）", seen[1:] == [("after", "old", "new")], seen[1:])
    seen.clear()
    move_hook(move_orig, Widget(old_grid, None), new_inventory=new_grid)
    check("キーワードの new_inventory も読む。部品に品が無ければ item は None",
          seen[0] == ("before", "old", "new", None, True), seen[:1])

    # ------------------------------------------------------------------ 閉じる
    print("[閉じる]")
    seen.clear()
    twin_windows.on("a", ctx, (twin_windows.CLOSE,), before=lambda call: seen.append(("before", "a", call.app is app)))
    twin_windows.on("b", ctx, (twin_windows.CLOSE,), after=lambda call: seen.append(("after", "b", call.app is app)))
    twin_windows.on("c", ctx, (twin_windows.CLOSE,), after=lambda call: seen.append(("after", "c")))
    close_hook = ctx.hooks[twin_windows.TARGETS[twin_windows.CLOSE]]
    close_hook(lambda self: seen.append(("orig",)), app)
    check("before → orig → after（古い順）。app は self",
          seen == [("before", "a", True), ("orig",), ("after", "b", True), ("after", "c")], seen)

    print("[前後の処理が投げた・orig が投げた]")
    seen.clear()

    def broken_hook(call):
        raise ValueError("hook boom")

    twin_windows.on("b", ctx, (twin_windows.CLOSE,), after=broken_hook)
    close_hook(lambda self: seen.append(("orig",)), app)
    check("1本が投げても残りと orig は止まらない", seen == [("before", "a", True), ("orig",), ("after", "c")], seen)
    seen.clear()
    twin_windows.on("c", ctx, (twin_windows.CLOSE,), after=lambda call: seen.append(
        ("after", "c", type(call.error).__name__ if call.error else None)), always=True)

    def broken_orig(self):
        raise RuntimeError("boom")

    try:
        close_hook(broken_orig, app)
    except RuntimeError:
        check("orig の例外はそのまま流す", True)
    else:
        check("orig の例外はそのまま流す", False)
    check("always=True の after だけ来る（call.error）",
          [row for row in seen if row[0] == "after"] == [("after", "c", "RuntimeError")], seen)
    twin_windows.off("b", (twin_windows.CLOSE,))

    # ------------------------------------------------------------------ 外側を押す
    print("[外側を押す]")
    seen.clear()
    twin_windows.on("hide-a", ctx, (twin_windows.HIDDEN,),
                    after=lambda call: seen.append(("hidden", "a", call.twin, call.app is app)))
    back_target = twin_windows.TARGETS[twin_windows.BACKDROP]
    check("HIDDEN だけの登録でも外側の関所が立つ", back_target in ctx.hooks)
    back_hook = ctx.hooks[back_target]
    twin_windows.on("b", ctx, (twin_windows.BACKDROP,),
                    after=lambda call: seen.append(("backdrop", "b", call.twin)))
    twin_windows.on("hide-c", ctx, (twin_windows.HIDDEN,), after=lambda call: seen.append(("hidden", "c")))

    def turnoff(self, instance, target):
        target["TorF"] = False
        return None

    back_hook(turnoff, hud, "instance", hud.visible_twin_inventory_data)
    check("BACKDROP の after → 次のフレームで HIDDEN（古い順）。消したのが2列の画面なら twin",
          seen == [("backdrop", "b", True), ("hidden", "a", True, True), ("hidden", "c")], seen)
    seen.clear()
    hud.visible_twin_inventory_data["TorF"] = True
    back_hook(lambda self, instance, target: None, hud, "instance", {"TorF": True})
    check("2列の画面が見えたままなら HIDDEN は来ない", seen == [("backdrop", "b", False)], seen)

    print("[次のフレーム]")
    seen.clear()
    queued = []
    fake_kivy = types.ModuleType("kivy")
    fake_clock_module = types.ModuleType("kivy.clock")
    fake_clock_module.Clock = types.SimpleNamespace(schedule_once=lambda fn, delay: queued.append((fn, delay)))
    saved = {name: sys.modules.get(name) for name in ("kivy", "kivy.clock")}
    sys.modules["kivy"], sys.modules["kivy.clock"] = fake_kivy, fake_clock_module
    try:
        back_hook(lambda self, instance, target: None, hud, "instance", hud.visible_twin_inventory_data)
        check("orig の直後には呼ばない（積むだけ）",
              seen == [("backdrop", "b", True)] and len(queued) == 1 and queued[0][1] == 0, (seen, queued))
        hud.visible_twin_inventory_data["TorF"] = False      # 次のフレームまでにゲームが書き換える
        queued.pop()[0](0)
        check("次のフレームで消えていたら来る", seen[1:] == [("hidden", "a", True, True), ("hidden", "c")], seen)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    print("[app が引けない・用済みの ctx]")
    seen.clear()
    ui.find_app = lambda: None
    back_hook(lambda self, instance, target: None, hud, "instance", hud.visible_twin_inventory_data)
    ui.find_app = lambda: app
    check("app が引けなければ HIDDEN は来ない", seen == [("backdrop", "b", True)], seen)
    seen.clear()
    ctx2 = Ctx()
    twin_windows.on("hide-a", ctx2, (twin_windows.HIDDEN,), after=lambda call: seen.append(("hidden", "a2")))
    ctx2.gone = True
    back_hook(lambda self, instance, target: None, hud, "instance", hud.visible_twin_inventory_data)
    check("用済みの ctx の登録は呼ばない", ("hidden", "a2") not in seen and ("hidden", "c") in seen, seen)

    print("[登録が無いとき]")
    twin_windows.reset()
    calls = []
    ui.find_app = lambda: calls.append("find_app") or app
    check("素通しで戻り値を返す", back_hook(lambda self, instance, target: "x", hud, "i", {}) == "x")
    move_hook(move_orig, Widget(old_grid, "剣"), new_grid)
    check("登録が無ければ app も引かない", calls == [], calls)
    ui.find_app = lambda: app

    print("[2列の画面が見えているか]")
    hud.visible_twin_inventory_data["TorF"] = False
    app.is_popup_window_opened = True
    check("TorF が下りていれば閉じている", twin_windows.is_open(app) is False)
    check("popup=True なら開いている印も見る", twin_windows.is_open(app, popup=True) is True)
    hud.visible_twin_inventory_data["TorF"] = True
    check("TorF が立っていれば開いている", twin_windows.is_open(app) is True)
    check("app が無ければ閉じている", twin_windows.is_open(None) is False)

    print("[包み]")
    gates.reset_all()
    ctx3 = Ctx()
    targets = twin_windows.install(ctx3)
    check("install は4つの対象を1枚ずつ包む",
          sorted(targets) == sorted(set(twin_windows.TARGETS.values())) and len(ctx3.hooks) == 4, targets)
    check("同じ世代の2回目は包まない", twin_windows.install(ctx3) == targets and len(ctx3.hooks) == 4)
    try:
        twin_windows.on("x", ctx3, ("nope",), after=lambda call: None)
    except ValueError:
        check("知らない種類は ValueError", True)
    else:
        check("知らない種類は ValueError", False)
    twin_windows.reset()
finally:
    ui.find_app, ui.find_hud = real_find_app, real_find_hud

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
