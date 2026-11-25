# -*- coding: utf-8 -*-
"""ローダの窓口 `items.on_made` / `items.on_sight` / `items.on`（品が生まれた・画面に出た）をゲーム抜きで通す。

    python tools/tests/test_window_c7.py

  生まれた   … 入口の間に組まれた品と戻り値の品を1回ずつ。入れ子は外の入口で1回。出どころは組まれた入口
  鍵で引く   … 戻り値が None の入口は、引数の持ち主の持ち物から鍵で引く
  絞る       … sources に無い出どころは来ない
  名付けた   … マスターAIの名前と同じ品が入口の外で組まれたら named。入口の中では控えない。控えは20件
  クラフト   … orig の前に、新しい登録から。部品なら中の品
  画面       … 2列の画面は左を全部、次に右（twin_windows.OPEN に載る）。所持品の画面・説明欄
  例外       … 1品が投げても残りの品・ほかの登録・orig は止まらない
  直の前後   … DETAIL の enter/leave は after より先。BUILD の after と named の順は登録の順
  包み       … 登録が無ければ包まない。あれば8つ。1つの世代に1枚。適用の間は見送り、外側で立てる
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import gates, items, twin_windows, ui  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("ok  " if ok else "FAIL", name, "  " + str(detail) if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Ctx(object):
    seq = 0

    def __init__(self, mod_name=None):
        Ctx.seq += 1
        self.generation = Ctx.seq
        self.hooks = {}
        self.gone = False
        self.mod_name = mod_name

    def superseded(self):
        return self.gone

    def wrap(self, target, **kw):
        def decorator(fn):
            self.hooks[target] = fn
            return fn
        return decorator


class App(object):
    def __init__(self):
        self.player = None


class Item(object):
    def __init__(self, name, item_type="weapon", attributes=None, obtainer=None):
        self.name = name
        self.item_type = item_type
        self.attributes = dict(attributes or {})
        self.obtainer = obtainer


class Container(object):
    def __init__(self, inventory=None):
        self.inventory = dict(inventory or {})


class Character(object):
    def __init__(self, name, inventory=None):
        self.name = name
        self.inventory = Container(inventory)


class Widget(object):
    def __init__(self, item):
        self.item_instance = item


app = App()
real_find_app = ui.find_app
ui.find_app = lambda: app


def hook_of(ctx, kind):
    return ctx.hooks[items.TARGETS[kind]]


def build(ctx, name, **kwargs):
    """`Item.__init__` を包みごと通して品を作る。"""
    obj = Item.__new__(Item)
    hook_of(ctx, items.BUILD)(Item.__init__, obj, name, **kwargs)
    return obj


try:
    gates.reset_all()
    items.reset()
    twin_windows.reset()

    # ------------------------------------------------------------------ 包み
    print("[包み]")
    ctx = Ctx()
    check("登録が無ければ包まない", items.install(ctx) == [] and not ctx.hooks)
    got = []
    items.on_made(ctx, lambda call: got.append(("a", call.item.name, call.source)), owner="a")
    check("登録があれば8つを全部包む", sorted(ctx.hooks) == sorted(items.TARGETS.values()), sorted(ctx.hooks))
    hooks_before = dict(ctx.hooks)
    check("1つの世代に1枚", items.install(ctx) == list(items.TARGETS.values())
          and ctx.hooks == hooks_before)
    check("owners は :made の名で載る", items.owners(items.FROM_ITEM_DATA) == ["a:made"]
          and items.made_owners() == ["a"], items.owners(items.FROM_ITEM_DATA))
    try:
        items.on_made(Ctx(), lambda call: None)
        check("持ち主が無ければ ValueError", False)
    except ValueError:
        check("持ち主が無ければ ValueError", True)
    named_ctx = Ctx(mod_name="999_named")
    items.on_made(named_ctx, lambda call: None, sources=(items.CRAFTED,))
    check("owner を省くと ctx.mod_name", "999_named" in items.made_owners(), items.made_owners())
    items.off_made("999_named")
    check("off_made で下がる", "999_named" not in items.made_owners(), items.made_owners())
    try:
        items.on_made(ctx, lambda call: None, owner="x", sources=("nope",))
        check("知らない出どころは ValueError", False)
    except ValueError:
        check("知らない出どころは ValueError", True)

    # ------------------------------------------------------------------ 生まれた
    print("[生まれた]")
    shop = Character("shopkeeper")
    items.on_made(ctx, lambda call: got.append(("b", call.item.name, call.source, call.obtainer is shop,
                                                call.app is app, call.entry.kind)), owner="b")

    def from_item_data(self, item_name, description, item_type, item_sub_type, value, appearance,
                       rarity, obtainer):
        side = build(ctx, "scrap")                       # 入口の中で組まれた別の品
        made = build(ctx, item_name, obtainer=obtainer)
        obtainer.inventory.inventory["item_1"] = made
        return (made, "item_1"), side

    result = hook_of(ctx, items.FROM_ITEM_DATA)(from_item_data, app, "剣", "d", "weapon", "small", 3,
                                                 "a sword", "common", shop)
    check("orig の戻り値を返す", isinstance(result, tuple) and result[0][1] == "item_1")
    check("組まれた順に1回ずつ、登録の古い順（a の全部、次に b）",
          got == [("a", "scrap", "generated"), ("a", "剣", "generated"),
                  ("b", "scrap", "generated", True, True, items.FROM_ITEM_DATA),
                  ("b", "剣", "generated", True, True, items.FROM_ITEM_DATA)], got)
    check("入口を抜けたら積みは空", items._stack() == [])

    print("[入れ子・鍵で引く]")
    got.clear()
    keeper = Character("keeper")

    def from_dict(self, item_dict, item_id, obtainer):
        # 組んだ品を返さず、持ち物へ入れる（GAME.md §2.13.1.5）。Item.__init__ を通らない版も鍵で引ける
        obtainer.inventory.inventory[item_id] = Item(item_dict["name"])
        return None

    def outer(self, *args):
        hook_of(ctx, items.FROM_DICT)(from_dict, app, {"name": "盾"}, "item_9", keeper)
        made = build(ctx, "outer")
        return (made, "item_2")

    hook_of(ctx, items.FROM_ITEM_DATA)(outer, app, "outer", "d", "weapon", "small", 3, "x", "common", shop)
    check("入れ子は外の入口で1回。出どころは品を作った入口",
          [row[:3] for row in got] == [("a", "盾", "generated/dict"), ("a", "outer", "generated"),
                                       ("b", "盾", "generated/dict"), ("b", "outer", "generated")], got)
    got.clear()
    hook_of(ctx, items.FROM_DICT)(from_dict, app, {"name": "兜"}, "item_10", keeper)
    check("戻り値が None でも持ち物から鍵で引く",
          [row[:3] for row in got] == [("a", "兜", "generated/dict"), ("b", "兜", "generated/dict")], got)

    print("[絞る]")
    got.clear()
    picky = []
    items.on_made(ctx, lambda call: picky.append((call.item.name, call.source)), owner="c",
                  sources=(items.GENERATED, items.NAMED, items.CRAFTED))
    hook_of(ctx, items.FROM_DICT)(from_dict, app, {"name": "鎧"}, "item_11", keeper)
    check("sources に無い出どころは来ない", picky == [] and len(got) == 2, (picky, got))
    hook_of(ctx, items.FROM_ITEM_DATA)(outer, app, "outer", "d", "weapon", "small", 3, "x", "common", shop)
    check("入れ子の内側の出どころでも絞る", picky == [("outer", "generated")], picky)
    items.off_made("c")

    print("[投げた入口]")
    got.clear()

    def broken(self, *args):
        build(ctx, "half")
        raise RuntimeError("boom")

    try:
        hook_of(ctx, items.FROM_ITEM_DATA)(broken, app, "x", "d", "weapon", "small", 3, "x", "common", shop)
        check("orig の例外はそのまま流す", False)
    except RuntimeError:
        check("orig の例外はそのまま流す", True)
    check("投げた入口の品は渡さず、積みも残さない", got == [] and items._stack() == [], (got, items._stack()))

    # ------------------------------------------------------------------ 名付けた
    print("[名付けた]")
    got.clear()
    master = hook_of(ctx, items.MASTER_ITEM)
    reply = {"item_name": "炎の剣", "item_category": {"type": "weapon"}}
    check("マスターAIの戻り値はそのまま", master(lambda *a, **k: reply, "prompt") is reply)
    check("入口の外で名付けた名前を控える", items.pending_names() == ["炎の剣"], items.pending_names())
    build(ctx, "無関係")
    check("名前の違う品は来ない", got == [], got)
    build(ctx, "炎の剣", obtainer=shop)
    check("同じ名前の品が組まれたら named（obtainer は Item.__init__ の引数）",
          [row[:3] for row in got] == [("a", "炎の剣", "named"), ("b", "炎の剣", "named")]
          and got[1][3] is True, got)
    check("控えは1回で消える", items.pending_names() == [], items.pending_names())

    def names_inside(self, *args):
        master(lambda *a, **k: {"item_name": "中の剣"}, "p")
        return (build(ctx, "中の剣"), "item_3")

    got.clear()
    hook_of(ctx, items.FROM_ITEM_DATA)(names_inside, app, "x", "d", "weapon", "small", 3, "x", "common", shop)
    check("入口の中で名付けた名前は控えず、入口が generated で渡す",
          items.pending_names() == [] and [row[:3] for row in got]
          == [("a", "中の剣", "generated"), ("b", "中の剣", "generated")], (items.pending_names(), got))
    master(lambda *a, **k: {"item_name": "先の剣"}, "p")

    def picks_named(self, *args):
        return (build(ctx, "先の剣"), "item_4")

    hook_of(ctx, items.FROM_ITEM_DATA)(picks_named, app, "x", "d", "weapon", "small", 3, "x", "common", shop)
    check("入口で渡した品の名前は控えから消す（後で named として拾い直さない）",
          items.pending_names() == [], items.pending_names())
    for index in range(items.PENDING_LIMIT + 5):
        master(lambda *a, **k: {"item_name": "n{}".format(index)}, "p")
    names = items.pending_names()
    check("控えは新しい20件", len(names) == items.PENDING_LIMIT and names[0] == "n5", names[:2])
    items.reset()
    gates.reset_all()

    # ------------------------------------------------------------------ クラフト
    print("[クラフト]")
    ctx = Ctx()
    order = []
    items.on_made(ctx, lambda call: order.append(("a", call.item.name, call.source)), owner="a")
    items.on_made(ctx, lambda call: order.append(("b", call.item.name, call.source)), owner="b")

    def place(self, generated_item):
        order.append(("orig", generated_item.item_instance.name))

    crafted = Item("作った剣")
    hook_of(ctx, items.PLACE_CRAFTED)(place, object(), Widget(crafted))
    check("orig の前に、新しい登録から。部品は中の品",
          order == [("b", "作った剣", "crafted"), ("a", "作った剣", "crafted"), ("orig", "作った剣")], order)
    order.clear()
    hook_of(ctx, items.PLACE_CRAFTED)(lambda self, generated_item: None, object(), generated_item=crafted)
    check("キーワードで来ても読む", [row[0] for row in order] == ["b", "a"], order)

    # ------------------------------------------------------------------ 例外
    print("[例外]")
    order.clear()
    warns = []

    def picky_fn(call):
        if call.item.name == "壊れた品":
            raise ValueError("bad item")
        order.append(("c", call.item.name))

    items.on_made(ctx, picky_fn, owner="c", write=warns.append)

    def two(self, *args):
        build(ctx, "壊れた品")
        return (build(ctx, "よい品"), "item_5")

    ran = hook_of(ctx, items.FROM_ITEM_DATA)(two, app, "x", "d", "weapon", "small", 3, "x", "common", None)
    check("投げても orig の戻り値は返る", ran[1] == "item_5")
    check("1品が投げても残りの品とほかの登録は止まらない",
          ("c", "よい品") in order and ("a", "よい品", "generated") in order
          and ("b", "壊れた品", "generated") in order, order)
    check("投げた登録の write に WARN", len(warns) == 1 and warns[0].startswith("WARN items.made"), warns)
    items.off_made("c")

    print("[用済みの ctx]")
    order.clear()
    ctx2 = Ctx()
    items.on_made(ctx2, lambda call: order.append(("gone", call.item.name)), owner="gone")
    ctx2.gone = True
    hook_of(ctx, items.FROM_ITEM_DATA)(two, app, "x", "d", "weapon", "small", 3, "x", "common", None)
    check("用済みの ctx の登録は呼ばない", not any(row[0] == "gone" for row in order), order)

    # ------------------------------------------------------------------ 画面
    print("[画面]")
    gates.reset_all()
    items.reset()
    twin_windows.reset()
    ctx = Ctx()
    sights = []

    def sight(name):
        def fn(call):
            sights.append((name, call.where, call.item.name, getattr(call.holder, "name", None)))
        return fn

    items.on_sight(ctx, sight("a"), owner="a")
    items.on_sight(ctx, sight("b"), owner="b", wheres=(items.WINDOW_LEFT, items.SIGHT_DETAIL))
    check("2列の画面は twin_windows.OPEN に :sight の名で載る",
          twin_windows.owners(twin_windows.OPEN) == ["a:sight", "b:sight"]
          and items.sight_owners() == ["a", "b"], twin_windows.owners(twin_windows.OPEN))
    left = Character("店主", {"k1": Item("薬"), "k2": Item("盾")})
    right = Character("主人公", {"k3": Item("剣")})
    opened = []

    def open_orig(self, *args, **kwargs):
        opened.append(list(sights))
        return "opened"

    twin = ctx.hooks[twin_windows.TARGETS[twin_windows.OPEN]]
    check("開いた画面の戻り値", twin(open_orig, app, left, right, "店", "shop") == "opened")
    check("orig の前に、新しい登録から。左を全部、次に右。絞った画面は来ない",
          opened == [[("b", "window/left", "薬", "店主"), ("b", "window/left", "盾", "店主"),
                      ("a", "window/left", "薬", "店主"), ("a", "window/left", "盾", "店主"),
                      ("a", "window/right", "剣", "主人公")]], opened)
    sights.clear()
    app.player = Character("主人公", {"k4": Item("杖")})
    center_seen = []
    ctx.hooks[items.TARGETS[items.CENTER]](lambda self: center_seen.append(list(sights)), app)
    check("所持品の画面は主人公の持ち物（絞った登録には来ない）",
          center_seen == [[("a", "window/center", "杖", "主人公")]], center_seen)
    sights.clear()
    shown = Item("指輪")
    raw_seen = []
    items.on("raw", ctx, (items.DETAIL,), before=lambda call: raw_seen.append(call.raw_item))
    ctx.hooks[items.TARGETS[items.DETAIL]](lambda self, item: None, object(), Widget(shown))
    check("説明欄は部品なら中の品で、新しい登録から",
          sights == [("b", "detail", "指輪", None), ("a", "detail", "指輪", None)], sights)
    check("直の登録には引数のままの品も渡る", isinstance(raw_seen[0], Widget), raw_seen)
    items.off("raw", (items.DETAIL,))
    items.off_sight("b")
    check("off_sight で2列の画面からも下がる", twin_windows.owners(twin_windows.OPEN) == ["a:sight"]
          and "b" not in items.sight_owners(), twin_windows.owners(twin_windows.OPEN))

    # ------------------------------------------------------------------ 直の前後
    print("[直の前後]")
    gates.reset_all()
    items.reset()
    twin_windows.reset()
    ctx = Ctx()
    steps = []
    # 410 の形（orig の間だけ説明欄に行を足す）と 109 の形（描いた後で箱を測る）
    items.on("410", ctx, (items.DETAIL,), enter=lambda call: steps.append("410 enter"),
             leave=lambda call: steps.append("410 leave"))
    items.on("109", ctx, (items.DETAIL,), after=lambda call: steps.append("109 after"))
    items.on("405", ctx, (items.DETAIL,), before=lambda call: steps.append("405 before") or "memo",
             after=lambda call: steps.append("405 after " + str(call.memo)))
    ctx.hooks[items.TARGETS[items.DETAIL]](lambda self, item: steps.append("orig"), object(), Item("x"))
    check("before → enter → orig → leave → after（古い順）",
          steps == ["405 before", "410 enter", "orig", "410 leave", "109 after", "405 after memo"], steps)
    steps.clear()
    items.on_made(ctx, lambda call: steps.append("410 named " + call.item.image_src), owner="410",
                  sources=(items.NAMED,))

    def remap(call):                         # 128 の形（組まれた後で絵のパスを付け替える）
        call.obj.image_src = "new.png"
        steps.append("128 after")

    items.on("128", ctx, (items.BUILD,), after=remap)
    master = hook_of(ctx, items.MASTER_ITEM)
    master(lambda *a: {"item_name": "名剣"}, "p")
    obj = Item.__new__(Item)

    def init(self, name):
        Item.__init__(self, name)
        self.image_src = "old.png"

    hook_of(ctx, items.BUILD)(init, obj, "名剣")
    check("BUILD の after と named は登録の古い順（410 が先に見る）",
          steps == ["410 named old.png", "128 after"], steps)
    items.on("probe", ctx, (items.MASTER_ITEM,),
             after=lambda call: steps.append(("master", call.name, call.item_type)))
    steps.clear()
    master(lambda *a: {"item_name": "槍", "item_category": {"type": "weapon"}}, "p")
    check("MASTER_ITEM の after は返った名前と種別を読める", steps == [("master", "槍", "weapon")], steps)
    try:
        items.on("x", ctx, ("nope",), after=lambda call: None)
        check("知らない種類は ValueError", False)
    except ValueError:
        check("知らない種類は ValueError", True)

    print("[組み直しの素通し]")
    gates.reset_all()
    items.reset()
    ctx = Ctx()
    items.on("only-detail", ctx, (items.DETAIL,), after=lambda call: None)
    runs = []
    real_run = items.GATES[items.BUILD].run
    items.GATES[items.BUILD].run = lambda call, invoke: runs.append(call) or real_run(call, invoke)
    try:
        plain = build(ctx, "読み込んだ品")
    finally:
        items.GATES[items.BUILD].run = real_run
    check("控えも BUILD の登録も無ければ Item.__init__ は素通し", runs == [] and plain.name == "読み込んだ品", runs)

    print("[スレッド]")
    gates.reset_all()
    items.reset()
    ctx = Ctx()
    seen = []
    items.on_made(ctx, lambda call: seen.append(call.item.name), owner="a")
    other = []
    started, release = threading.Event(), threading.Event()

    def slow(self, *args):
        started.set()
        release.wait(5)
        return (build(ctx, "スレッドの品"), "item_7")

    worker = threading.Thread(target=lambda: other.append(hook_of(ctx, items.FROM_ITEM_DATA)(
        slow, app, "x", "d", "weapon", "small", 3, "x", "common", None)))
    worker.start()
    started.wait(5)
    build(ctx, "別のスレッドで組んだ品")      # 入口の中ではない（積みはスレッドごと）
    release.set()
    worker.join(5)
    check("入口の積みはスレッドごと", seen == ["スレッドの品"], seen)

    print("[適用の間は見送る]")
    gates.reset_all()
    items.reset()
    ctx = Ctx()
    gates.defer_outermost(True)
    try:
        items.on_made(ctx, lambda call: None, owner="a")
        check("適用の間は包まない", ctx.hooks == {}, sorted(ctx.hooks))
    finally:
        gates.defer_outermost(False)
    check("外側の立て方で8つ包む", len(items.install(ctx)) == len(items.KINDS) and len(ctx.hooks) == len(items.KINDS))
finally:
    ui.find_app = real_find_app
    gates.defer_outermost(False)
    gates.reset_all()
    items.reset()
    twin_windows.reset()

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all passed")
