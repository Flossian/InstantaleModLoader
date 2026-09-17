# -*- coding: utf-8 -*-
"""`instantale_modloader.prices`（ゲームが決めている値段の窓口）。

    python tools/tests/test_prices.py

置く側と読む側が互いの名前を知らずに繋がること、
誰も置いていなければゲームの値が答えになること、
知らない部屋では「分からない」（None）を返して 0 と混ぜないこと、
壊れた答えはゲームの値へ落ちること、
片付けが `durations.forget` の1本で済むことを見る。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))

from instantale_modloader import durations, prices  # noqa: E402


def check(label, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", label,
                              "" if cond else "  <- {!r}".format(detail)))
    return bool(cond)


def main():
    ok = True
    app = types.SimpleNamespace(player=types.SimpleNamespace(gold=500, age=25))
    logs = []

    print("[ゲームの値]")
    ok &= check("個室は 100", prices.inn_room(app, "private_room") == 100)
    ok &= check("犬小屋は 0", prices.inn_room(app, "kennel") == 0)
    ok &= check("高級個室は 1000", prices.inn_room(app, "luxury_suite") == 1000)
    ok &= check("知らない部屋は None（0 と混ぜない）",
                prices.inn_room(app, "tent") is None)
    ok &= check("誰も置いていなければ持ち主は空",
                prices.source_of(prices.INN_ROOM) == "")

    print("[置いた側が勝つ]")

    def room(app, quality=None):
        return {"price": 7} if quality == "private_room" else None

    prices.declare(prices.INN_ROOM, room, owner="315_test", write=logs.append)
    ok &= check("置いた額が返る", prices.inn_room(app, "private_room") == 7)
    ok &= check("None を返した部屋はゲームの値",
                prices.inn_room(app, "bunk") == 10)
    ok &= check("持ち主を名乗る", prices.source_of(prices.INN_ROOM) == "315_test")

    print("[壊れた答えはゲームの値へ落ちる]")
    prices.declare(prices.INN_ROOM, lambda app, quality=None: {"price": "たくさん"},
                   owner="broken")
    logs[:] = []
    ok &= check("数でなければゲームの値",
                prices.inn_room(app, "private_room", write=logs.append) == 100)
    ok &= check("そのとき WARN が出る",
                any("WARN prices" in line for line in logs), logs)

    prices.declare(prices.INN_ROOM, lambda app, quality=None: 1 / 0, owner="raises")
    logs[:] = []
    ok &= check("例外を投げてもゲームの値",
                prices.inn_room(app, "private_room", write=logs.append) == 100)

    prices.declare(prices.INN_ROOM, lambda app, quality=None: {"price": -5},
                   owner="minus")
    ok &= check("負の額は 0 に均す", prices.inn_room(app, "private_room") == 0)

    print("[片付け]")
    durations.forget("minus")
    ok &= check("外せばゲームの値に戻る",
                prices.inn_room(app, "private_room") == 100
                and prices.source_of(prices.INN_ROOM) == "")

    def stay(app):
        return {"months": 1, "days": 7, "length": "1週間"}

    durations.declare(durations.INN_STAY, stay, owner="315_test")
    prices.declare(prices.INN_ROOM, room, owner="315_test")
    durations.forget("315_test")
    ok &= check("期間と値段が同じ forget で外れる",
                prices.source_of(prices.INN_ROOM) == ""
                and durations.source_of(durations.INN_STAY) == "")

    ok &= gate_main()

    print("all ok" if ok else "FAILED")
    return 0 if ok else 1



# ---------------------------------------------------------------------------
# MOD が決める値段（アイテムの売買額）の関所
# ---------------------------------------------------------------------------


class Item:
    """`scripts.items.Item` の、値段に関わるところだけ。"""

    def __init__(self, name="品", attributes=None, item_type="material",
                 rarity="common", value=10):
        self.name = name
        self.attributes = dict(attributes if attributes is not None
                               else {prices.BUY: 100, prices.SELL: 40,
                                     "item_detail": "plant"})
        self.item_type = item_type
        self.rarity = rarity
        self.value = value


class Unhashable(Item):
    """弱参照もハッシュもできない品（`Item` が `__slots__` を持つ版の代役）。"""
    __hash__ = None


class Obtainer:
    def __init__(self, *items):
        self.inventory = {str(i): item for i, item in enumerate(items)}


class GateCtx:
    """関所が要るぶんだけの偽 ctx。"""

    def __init__(self, generation=1):
        self.generation = generation
        self.hooks = {}

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def fresh_gate(generation=1):
    """登録簿と関所を捨ててから立て直す。"""
    for attr in (prices._ITEM_ATTR, prices._ITEM_GATE_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)
    ctx = GateCtx(generation)
    prices.install(ctx)
    return ctx


def call(ctx, target, *args, **kwargs):
    """包んだ地点を1回通す。`orig` は何もしない。"""
    return ctx.hooks[target](lambda *a, **k: None, *args, **kwargs)


def open_window(ctx, left=None, right=None):
    return call(ctx, prices.SIGHT_TARGETS[0][0], None, left, right, None, None)


def price_owner(ctx, item):
    return call(ctx, "__main__:InstantaleApp.set_shop_price_for_owner",
                None, item)


def gate_main():
    ok = True

    print("[誰も置いていなければ触らない]")
    ctx = fresh_gate()
    item = Item()
    price_owner(ctx, item)
    ok &= check("素の額のまま", item.attributes[prices.BUY] == 100)

    print("[式が1枚。額を組む]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320,
                                           "axis": "value=10"})
    item = Item()
    price_owner(ctx, item)
    ok &= check("買価が置き換わる", item.attributes[prices.BUY] == 800)
    ok &= check("売価が置き換わる", item.attributes[prices.SELL] == 320)

    print("[段が乗る]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5)
    item = Item()
    price_owner(ctx, item)
    ok &= check("式 × 段", item.attributes[prices.BUY] == 1200)
    ok &= check("売価にも乗る", item.attributes[prices.SELL] == 480)

    print("[何度通しても積み上がらない]")
    for _ in range(5):
        price_owner(ctx, item)
        open_window(ctx, Obtainer(item))
    ok &= check("5往復しても同じ額", item.attributes[prices.BUY] == 1200,
                item.attributes[prices.BUY])

    print("[画面の地点でも段が残る（§3.19.1 の再発防止）]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5)
    shop = Item()
    open_window(ctx, Obtainer(shop))
    ok &= check("売買画面を開いた後も倍率が乗っている",
                shop.attributes[prices.BUY] == 1200, shop.attributes)
    call(ctx, prices.NORMALIZE_TARGET, None, Obtainer(shop), None)
    ok &= check("normalize を通しても戻されない",
                shop.attributes[prices.BUY] == 1200, shop.attributes)

    print("[段の順序は登録順]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 100, prices.SELL: 100})
    prices.adjust("first", lambda it, key, price: price + 10)
    prices.adjust("second", lambda it, key, price: price * 2)
    item = Item()
    price_owner(ctx, item)
    ok &= check("(100+10)*2 = 220", item.attributes[prices.BUY] == 220,
                item.attributes[prices.BUY])

    print("[同じ持ち主は1枚まで（再注入で重ねない）]")
    prices.adjust("second", lambda it, key, price: price * 2)
    item = Item()
    price_owner(ctx, item)
    ok &= check("当て直しても段は2枚のまま",
                item.attributes[prices.BUY] == 220, item.attributes[prices.BUY])
    ok &= check("名乗りも重ならない",
                prices.item_price_sources() == ("129", ["first", "second"]),
                prices.item_price_sources())

    print("[鍵は新設しない]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    only_buy = Item(attributes={prices.BUY: 100, "item_detail": "plant"})
    price_owner(ctx, only_buy)
    ok &= check("売価を足さない", prices.SELL not in only_buy.attributes)
    bare = Item(attributes={"item_detail": "plant"})
    price_owner(ctx, bare)
    ok &= check("値段の鍵が無い品には触らない", bare.attributes == {"item_detail": "plant"})

    print("[式が組めない品はゲームの額が軸]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: None)
    prices.adjust("405", lambda it, key, price: price * 1.5)
    odd = Item()
    price_owner(ctx, odd)
    ok &= check("ゲームの 100 に段が乗る", odd.attributes[prices.BUY] == 150,
                odd.attributes[prices.BUY])
    for _ in range(5):
        price_owner(ctx, odd)
        open_window(ctx, Obtainer(odd))
    ok &= check("何度通しても積み上がらない", odd.attributes[prices.BUY] == 150,
                odd.attributes[prices.BUY])

    print("[ゲームが付け直したら軸も取り直す]")
    odd.attributes[prices.BUY] = 200          # ゲームが書いた
    price_owner(ctx, odd)
    ok &= check("新しい素の額に段が乗る", odd.attributes[prices.BUY] == 300,
                odd.attributes[prices.BUY])

    print("[弱参照できない品でも軸を覚えている]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: None)
    prices.adjust("405", lambda it, key, price: price * 1.5)
    stubborn = Unhashable()
    price_owner(ctx, stubborn)
    first = stubborn.attributes[prices.BUY]
    for _ in range(3):
        price_owner(ctx, stubborn)
    ok &= check("id の台帳へ落ちても積み上がらない",
                stubborn.attributes[prices.BUY] == first == 150,
                stubborn.attributes[prices.BUY])

    print("[一時の段は保存の間だけ外れる]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5, temporary=True)
    item = Item()
    price_owner(ctx, item)
    ok &= check("画面では倍率が乗っている", item.attributes[prices.BUY] == 1200)

    seen = {}

    def saving(*args, **kwargs):
        seen["buy"] = item.attributes[prices.BUY]
        seen["sell"] = item.attributes[prices.SELL]

    ctx.hooks[prices.SAVE_TARGETS[0]](saving, None)
    ok &= check("保存の最中は式だけの額", seen.get("buy") == 800, seen)
    ok &= check("売価も式だけの額", seen.get("sell") == 320, seen)
    ok &= check("保存が済んだら戻る", item.attributes[prices.BUY] == 1200,
                item.attributes[prices.BUY])

    print("[保存を挟んでも倍率が積み上がらない]")
    for _ in range(3):
        ctx.hooks[prices.SAVE_TARGETS[0]](saving, None)
        open_window(ctx, Obtainer(item))
    ok &= check("3回保存しても同じ額", item.attributes[prices.BUY] == 1200,
                item.attributes[prices.BUY])

    print("[保存へ焼き付けてよい段は外れない]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("keep", lambda it, key, price: price * 2, temporary=False)
    item = Item()
    price_owner(ctx, item)
    kept = {}
    ctx.hooks[prices.SAVE_TARGETS[0]](
        lambda *a, **k: kept.setdefault("buy", item.attributes[prices.BUY]), None)
    ok &= check("保存の最中も乗ったまま", kept.get("buy") == 1600, kept)

    print("[壊れた段があっても残りは通る]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 100, prices.SELL: 100})
    prices.adjust("broken", lambda it, key, price: 1 / 0)
    prices.adjust("sane", lambda it, key, price: price * 3)
    item = Item()
    price_owner(ctx, item)
    ok &= check("壊れた段は飛ばして次が乗る", item.attributes[prices.BUY] == 300,
                item.attributes[prices.BUY])

    print("[壊れた式はゲームの額へ落ちる]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: 1 / 0)
    prices.adjust("405", lambda it, key, price: price * 1.5)
    item = Item()
    price_owner(ctx, item)
    ok &= check("式が投げてもゲームの額に段が乗る",
                item.attributes[prices.BUY] == 150, item.attributes[prices.BUY])

    print("[段が None を返した鍵は触らない]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5
                  if key == prices.BUY else None)
    item = Item()
    price_owner(ctx, item)
    ok &= check("買価だけ乗る", item.attributes[prices.BUY] == 1200)
    ok &= check("売価は式のまま", item.attributes[prices.SELL] == 320)

    print("[画面の地点は頼まれたときだけ包む]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320},
                        on_sight=False)
    old = Item()
    open_window(ctx, Obtainer(old))
    ok &= check("on_sight=False なら画面では付け直さない",
                old.attributes[prices.BUY] == 100, old.attributes[prices.BUY])
    price_owner(ctx, old)
    ok &= check("ゲームが書く地点では付け直す", old.attributes[prices.BUY] == 800)

    print("[関所は1世代に1枚]")
    ctx = fresh_gate(generation=7)
    before = len(ctx.hooks)
    again = GateCtx(generation=7)
    prices.install(again)
    ok &= check("同じ世代なら包み直さない", not again.hooks, again.hooks)
    ok &= check("立っている対象を返す",
                len(prices.item_gate()["targets"]) == before, before)
    newer = GateCtx(generation=8)
    prices.install(newer)
    ok &= check("世代が変われば立て直す", len(newer.hooks) == before)

    print("[記録の1行で段が効いたか分かる]")
    ctx = fresh_gate()
    logged = []
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320,
                                           "axis": "value=10"},
                        write=logged.append)
    prices.adjust("405", lambda it, key, price: price * 1.5)
    price_owner(ctx, Item())
    ok &= check("軸と段の名前が同じ行に出る",
                any("[value=10 +405]" in line and "-> 1200" in line
                    for line in logged), logged)
    logged[:] = []
    prices.adjust("405", lambda it, key, price: None)
    price_owner(ctx, Item())
    ok &= check("触らなかった段は名乗らない",
                any("[value=10]" in line for line in logged), logged)

    print("[保存が入れ子でも戻しすぎない]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5, temporary=True)
    item = Item()
    price_owner(ctx, item)
    nested = {}

    def outer(*args, **kwargs):
        # 保存の実体は `save_game` の内側から呼ばれる。
        ctx.hooks[prices.SAVE_TARGETS[1]](
            lambda *a, **k: nested.setdefault("inner", item.attributes[prices.BUY]))
        nested["after_inner"] = item.attributes[prices.BUY]

    ctx.hooks[prices.SAVE_TARGETS[0]](outer, None)
    ok &= check("内側の保存でも式だけの額", nested.get("inner") == 800, nested)
    ok &= check("内側は戻し切らない", nested.get("after_inner") == 800, nested)
    ok &= check("外側が済んでから戻る", item.attributes[prices.BUY] == 1200,
                item.attributes[prices.BUY])

    print("[段の答えが変わったら押して組み直す]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 100, prices.SELL: 100})
    rate = {"x": 1.0}
    prices.adjust("405", lambda it, key, price: price * rate["x"])
    item = Item()
    price_owner(ctx, item)
    ok &= check("はじめは等倍", item.attributes[prices.BUY] == 100)
    rate["x"] = 2.0
    ok &= check("押すまでは動かない", item.attributes[prices.BUY] == 100)
    prices.refresh("test")
    ok &= check("押せば組み直る", item.attributes[prices.BUY] == 200,
                item.attributes[prices.BUY])
    rate["x"] = 1.0
    prices.refresh("test")
    ok &= check("戻る向きにも組み直る", item.attributes[prices.BUY] == 100,
                item.attributes[prices.BUY])

    print("[片付け]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5)
    durations.forget("405")
    item = Item()
    price_owner(ctx, item)
    ok &= check("段を外せば式だけの額", item.attributes[prices.BUY] == 800)
    durations.forget("129")
    ok &= check("式も外れる", prices.item_price_sources() == ("", []),
                prices.item_price_sources())
    plain = Item()
    price_owner(ctx, plain)
    ok &= check("誰も居なければ素の額のまま", plain.attributes[prices.BUY] == 100)

    return ok


if __name__ == "__main__":
    sys.exit(main())
