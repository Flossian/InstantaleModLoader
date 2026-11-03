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
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))

import instantale_modloader as loader  # noqa: E402
from instantale_modloader import durations, loads, prices  # noqa: E402


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
    ok &= haggle_main()
    ok &= trade_main()
    ok &= made_main()

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

    def own_hooks(gate_ctx):
        # 値切りの固定はロードの窓口（`loads`）にも載る。あちらは別の関所なので数えない。
        return [target for target in gate_ctx.hooks if target not in loads.TARGETS.values()]

    ctx = fresh_gate(generation=7)
    before = len(own_hooks(ctx))
    again = GateCtx(generation=7)
    prices.install(again)
    ok &= check("同じ世代なら包み直さない", not own_hooks(again), again.hooks)
    ok &= check("立っている対象を返す",
                len(prices.item_gate()["targets"]) == before, before)
    newer = GateCtx(generation=8)
    prices.install(newer)
    ok &= check("世代が変われば立て直す", len(own_hooks(newer)) == before)

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

    print("[別スレッドの保存が重なり、先に入った側が先に抜けても戻しすぎない]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5, temporary=True)
    item = Item()
    price_owner(ctx, item)
    overlap = {}
    first_in = threading.Event()
    second_in = threading.Event()
    first_out = threading.Event()

    def first(*args, **kwargs):
        # ゲームの自動保存。書き出しの最中に別の保存が入ってくる。
        first_in.set()
        second_in.wait(5)

    def second(*args, **kwargs):
        # 後から入った保存。先の保存が抜けた後もまだ書き出している。
        second_in.set()
        first_out.wait(5)
        overlap["second"] = item.attributes[prices.BUY]

    worker = threading.Thread(
        target=lambda: (ctx.hooks[prices.SAVE_TARGETS[0]](first, None),
                        first_out.set()))
    worker.start()
    first_in.wait(5)
    ctx.hooks[prices.SAVE_TARGETS[0]](second, None)
    worker.join(5)
    ok &= check("後の保存にも式だけの額", overlap.get("second") == 800, overlap)
    ok &= check("両方抜けたら倍率が戻る", item.attributes[prices.BUY] == 1200,
                item.attributes[prices.BUY])
    ok &= check("入れ子の数が 0 へ戻る",
                prices._item_registry().get("saving") == 0,
                prices._item_registry().get("saving"))
    prices.refresh("test")
    ok &= check("以後も refresh で倍率が乗る",
                item.attributes[prices.BUY] == 1200, item.attributes[prices.BUY])

    print("[保存の窓の間に別の地点が通っても一時の段を書き戻さない]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5, temporary=True)
    item = Item()
    price_owner(ctx, item)
    during = {}

    def crossing(*args, **kwargs):
        # 保存のスレッドが書き出している間に、メインスレッドの画面・
        # `refresh()`・ゲームが値を付け直す地点が通る。
        open_window(ctx, Obtainer(item))
        during["window"] = item.attributes[prices.BUY]
        prices.refresh("test")
        during["refresh"] = item.attributes[prices.BUY]
        price_owner(ctx, item)
        during["game"] = item.attributes[prices.BUY]

    ctx.hooks[prices.SAVE_TARGETS[0]](crossing, None)
    ok &= check("画面の地点を通っても式だけの額", during.get("window") == 800, during)
    ok &= check("refresh を押されても式だけの額", during.get("refresh") == 800, during)
    ok &= check("ゲームが書く地点を通っても式だけの額", during.get("game") == 800, during)
    ok &= check("保存が済んだら倍率が戻る", item.attributes[prices.BUY] == 1200,
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


# ---------------------------------------------------------------------------
# 値切りで決まった額（VERIFICATION.md §3.121）
# ---------------------------------------------------------------------------


class Holder:
    """店主か主人公。持ち物は `{鍵: 品}`。"""

    def __init__(self, ident, *items):
        self.id = ident
        self.inventory = {}
        for item in items:
            self.take(item)

    def take(self, item):
        item.obtainer = self
        self.inventory[item.id] = item


def shop_item(ident, attributes=None):
    item = Item(name=ident, attributes=attributes)
    item.id = ident
    return item


DICT_MAKER = "__main__:InstantaleApp.generate_item_from_dict"
DATA_MAKER = "__main__:InstantaleApp.generate_item_from_item_data"


def make_from_dict(ctx, item, owner, by_keyword=False):
    """ゲームの `generate_item_from_dict` の形で作る。品は渡した鍵で持ち物へ入り、戻りは None（GAME.md §2.13.1.5）。"""
    def orig(self, item_dict, item_id, obtainer):
        obtainer.inventory[item_id] = item
        item.obtainer = obtainer
        return None

    if by_keyword:
        return ctx.hooks[DICT_MAKER](orig, None, item_dict={}, item_id=item.id, obtainer=owner)
    return ctx.hooks[DICT_MAKER](orig, None, {}, item.id, owner)


def make_from_data(ctx, item, owner):
    """ゲームの `generate_item_from_item_data` の形で作る。戻りは `(Item, 鍵)`。"""
    def orig(self, *args):
        owner.take(item)
        return item, item.id

    return ctx.hooks[DATA_MAKER](orig, None, item.name, "説明", item.item_type, "small",
                                 item.value, "a dagger", item.rarity, owner)


class StateCtx(GateCtx):
    """`state\\` の置き場と JSON の読み書きを持つ偽 ctx（周回の控えまで通す）。"""

    def __init__(self, state_dir, generation=1):
        super().__init__(generation)
        self.state_dir = state_dir

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        return loader.read_json(path, default)

    def write_json(self, path, data, *, indent=1):
        return loader.write_json(path, data, indent=indent)


def haggle(ctx, manager, write_price):
    """値切りを1回通す。ゲームは中で額を書き、続けて保存する（実機の順）。"""
    seen = {}

    def negotiate(self, *args, **kwargs):
        write_price()
        ctx.hooks[prices.SAVE_TARGETS[0]](
            lambda *a, **k: seen.setdefault("saved", dict(snapshot())), None)

    snapshot = manager.snapshot
    ctx.hooks[prices.HAGGLE_TARGET](negotiate, manager, "まけて")
    return seen.get("saved")


def haggle_main():
    ok = True

    def fresh(state_dir=None, generation=1):
        for attr in (prices._ITEM_ATTR, prices._ITEM_GATE_ATTR, prices._HAGGLE_STORE_ATTR):
            if hasattr(sys, attr):
                delattr(sys, attr)
        loads.reset()
        gate_ctx = StateCtx(state_dir, generation) if state_dir else GateCtx(generation)
        prices.install(gate_ctx)
        return gate_ctx

    print("[値切りの額は式と一時の段を越えて残る]")
    ctx = fresh()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("341", lambda it, key, price: price * 1.5, temporary=True)
    knife = shop_item("item_1", {prices.BUY: 100, "item_detail": "small_weapon"})
    owner = Holder("99", knife)
    player = Holder(None)
    app = types.SimpleNamespace(player=player)
    manager = types.SimpleNamespace(app=app,
                                    snapshot=lambda: {"buy": knife.attributes[prices.BUY]})
    call(ctx, prices.SIGHT_TARGETS[0][0], app, owner, player, None, "shop")
    ok &= check("窓を開くと式 × 段", knife.attributes[prices.BUY] == 1200,
                knife.attributes[prices.BUY])
    saved = haggle(ctx, manager, lambda: knife.attributes.__setitem__(prices.BUY, 1000))
    ok &= check("値切りの中の保存にも値切った額", saved == {"buy": 1000}, saved)
    ok &= check("値切りの後も値切った額", knife.attributes[prices.BUY] == 1000,
                knife.attributes[prices.BUY])
    ok &= check("固定として読める", prices.haggled(knife) == {prices.BUY: 1000},
                prices.haggled(knife))
    call(ctx, prices.SIGHT_TARGETS[0][0], app, owner, player, None, "shop")
    call(ctx, prices.SIGHT_TARGETS[1][0], None, knife)
    price_owner(ctx, knife)
    call(ctx, prices.NORMALIZE_TARGET, app, owner, player)
    prices.refresh("test")
    ok &= check("窓・詳細・付け直し・均し・refresh を通しても残る",
                knife.attributes[prices.BUY] == 1000, knife.attributes[prices.BUY])
    kept = {}
    ctx.hooks[prices.SAVE_TARGETS[0]](
        lambda *a, **k: kept.setdefault("buy", knife.attributes[prices.BUY]), None)
    ok &= check("あとの保存でも外さない", kept.get("buy") == 1000, kept)

    print("[ロードで作り直された同じ品にも当たる]")
    loaded = shop_item("item_1", {prices.BUY: 1000, "item_detail": "small_weapon"})
    make_from_dict(ctx, loaded, Holder("99"))
    ok &= check("作り直しても値切った額", loaded.attributes[prices.BUY] == 1000,
                loaded.attributes[prices.BUY])

    print("[売買で鍵が替わったら外れる]")
    owner.inventory.pop(knife.id)
    player.take(knife)
    knife.attributes.pop(prices.BUY)
    knife.attributes[prices.SELL] = 40          # ゲームが主人公の持ち物として付け直した
    call(ctx, "__main__:InstantaleApp.set_shop_price_for_player", app, knife)
    ok &= check("固定が外れる", prices.haggled(knife) == {}, prices.haggled(knife))
    ok &= check("売価は式 × 段", knife.attributes[prices.SELL] == 480,
                knife.attributes[prices.SELL])

    print("[ゲームが付け直したら外れる]")
    ctx = fresh()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    cup = shop_item("item_2", {prices.BUY: 100, "item_detail": "tool"})
    owner = Holder("99", cup)
    app = types.SimpleNamespace(player=Holder(None))
    manager = types.SimpleNamespace(app=app, snapshot=dict)
    call(ctx, prices.SIGHT_TARGETS[0][0], app, owner, app.player, None, "shop")
    haggle(ctx, manager, lambda: cup.attributes.__setitem__(prices.BUY, 600))
    ok &= check("値切った額が残る", cup.attributes[prices.BUY] == 600, cup.attributes)
    cup.attributes[prices.BUY] = 100            # ゲームの付け直し
    price_owner(ctx, cup)
    ok &= check("式の額へ戻る", cup.attributes[prices.BUY] == 800, cup.attributes)
    ok &= check("固定も外れる", prices.haggled(cup) == {}, prices.haggled(cup))

    print("[段だけでも残る（式の無い組み合わせ）]")
    ctx = fresh()
    prices.adjust("405", lambda it, key, price: price * 2, temporary=True)
    herb = shop_item("item_3", {prices.BUY: 100, "item_detail": "plant"})
    owner = Holder("99", herb)
    app = types.SimpleNamespace(player=Holder(None))
    manager = types.SimpleNamespace(app=app, snapshot=dict)
    price_owner(ctx, herb)
    ok &= check("段が乗る", herb.attributes[prices.BUY] == 200, herb.attributes)
    haggle(ctx, manager, lambda: herb.attributes.__setitem__(prices.BUY, 150))
    ctx.hooks[prices.SAVE_TARGETS[0]](lambda *a, **k: None, None)
    call(ctx, prices.SIGHT_TARGETS[1][0], None, herb)
    ok &= check("保存と詳細を通しても値切った額", herb.attributes[prices.BUY] == 150,
                herb.attributes)

    print("[値切りの最中に関所が組み直した額は固定しない]")
    ctx = fresh()
    prices.declare_base("129", lambda it: {prices.BUY: 100, prices.SELL: 40})
    rate = {"x": 1.0}
    prices.adjust("341", lambda it, key, price: price * rate["x"], temporary=True)
    bread = shop_item("item_4", {prices.BUY: 100, "item_detail": "food"})
    owner = Holder("99", bread)
    app = types.SimpleNamespace(player=Holder(None))
    manager = types.SimpleNamespace(app=app, snapshot=dict)
    call(ctx, prices.SIGHT_TARGETS[0][0], app, owner, app.player, None, "shop")

    def judged():
        rate["x"] = 0.9                         # 店主の態度が決まった（段の答えが変わった）
        prices.refresh("test")

    haggle(ctx, manager, judged)
    ok &= check("段の額になる", bread.attributes[prices.BUY] == 90, bread.attributes)
    ok &= check("固定はしない", prices.haggled(bread) == {}, prices.haggled(bread))

    print("[同じ額の逆提示は固定しない]")
    haggle(ctx, manager, lambda: bread.attributes.__setitem__(prices.BUY, 90))
    ok &= check("固定はしない", prices.haggled(bread) == {}, prices.haggled(bread))

    print("[店がもう持っていない品の固定は、窓を開いたとき片付く]")
    haggle(ctx, manager, lambda: bread.attributes.__setitem__(prices.BUY, 70))
    ok &= check("固定される", prices.haggled(bread) == {prices.BUY: 70}, prices.haggled(bread))
    owner.inventory.clear()                     # 店の入れ替えで消えた
    call(ctx, prices.SIGHT_TARGETS[0][0], app, owner, app.player, None, "shop")
    ok &= check("片付く", prices.haggled(bread) == {}, prices.haggled(bread))

    print("[周回の控えに書き、読み直しても当たる]")
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        ctx = fresh(state_dir=root)
        prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
        ring = shop_item("item_5", {prices.BUY: 100, "item_detail": "accessory"})
        owner = Holder("99", ring)
        app = types.SimpleNamespace(player=Holder(None),
                                    world_dict={"world_data": {"name": "試しの世界"}},
                                    save_data_dict={"player_data": {"name": "主"}})
        manager = types.SimpleNamespace(app=app, snapshot=dict)
        call(ctx, prices.SIGHT_TARGETS[0][0], app, owner, app.player, None, "shop")
        haggle(ctx, manager, lambda: ring.attributes.__setitem__(prices.BUY, 500))
        path = os.path.join(root, prices.HAGGLE_DIRNAME, "試しの世界×主.json")
        stored = loader.read_json(path, {})
        ok &= check("控えに書く",
                    (stored.get("haggled") or {}).get("item_5", {}).get("price") == 500, stored)
        ok &= check("持ち主も控える",
                    (stored.get("haggled") or {}).get("item_5", {}).get("owner") == "99", stored)

        # 起動し直して（控えは手元に無い）同じ周回を読む。
        ctx = fresh(state_dir=root, generation=2)
        prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
        world = loads.TARGETS[loads.WORLD]
        ctx.hooks[world](lambda *a, **k: None, object(),
                         {"world_data": {"name": "試しの世界"}, "player_data": {"name": "主"}},
                         app)
        again = shop_item("item_5", {prices.BUY: 500, "item_detail": "accessory"})
        make_from_dict(ctx, again, Holder("99"))
        ok &= check("ロードで作られた品へ当たる", again.attributes[prices.BUY] == 500,
                    again.attributes)

        other = shop_item("item_5", {prices.BUY: 500, "item_detail": "accessory"})
        ctx.hooks[world](lambda *a, **k: None, object(),
                         {"world_data": {"name": "別の世界"}, "player_data": {"name": "主"}},
                         app)
        make_from_dict(ctx, other, Holder("99"))
        ok &= check("別の周回の同じ id には当たらない", other.attributes[prices.BUY] == 800,
                    other.attributes)
    loads.reset()
    return ok


# ---------------------------------------------------------------------------
# 売買で持ち主が替わった品（VERIFICATION.md §3.121）
# ---------------------------------------------------------------------------


def trade(ctx, target, item, to, key, game_price, by_keyword=False):
    """売買を1回通す。ゲームは中で品を移し、新しい持ち主の鍵へ自分の額を付ける。"""
    paid = {}

    def move(self, *args, **kwargs):
        old = prices.SELL if key == prices.BUY else prices.BUY
        paid["price"] = item.attributes.pop(old)
        item.obtainer.inventory.pop(item.id)
        to.take(item)
        item.attributes[key] = game_price

    if by_keyword:
        ctx.hooks[target](move, None, item_instance=item)
    else:
        ctx.hooks[target](move, None, item)
    return paid.get("price")


def trade_main():
    ok = True
    sold_at = "__main__:InstantaleApp.sell_item"
    bought_at = "__main__:InstantaleApp.buy_item"

    print("[売った品・買った品はその場で組み直す]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    prices.adjust("405", lambda it, key, price: price * 1.5)
    sword = shop_item("item_9", {prices.SELL: 40, "item_detail": "small_weapon"})
    player = Holder(None, sword)
    owner = Holder("99")
    open_window(ctx, owner, player)
    ok &= check("売る前は式 × 段", sword.attributes[prices.SELL] == 480, sword.attributes)
    paid = trade(ctx, sold_at, sword, owner, prices.BUY, 300)
    ok &= check("売った額は関所の額", paid == 480, paid)
    ok &= check("店の買価はゲームの額でなく式 × 段", sword.attributes.get(prices.BUY) == 1200,
                sword.attributes)
    paid = trade(ctx, bought_at, sword, player, prices.SELL, 30, by_keyword=True)
    ok &= check("買い戻す額も関所の額", paid == 1200, paid)
    ok &= check("手持ちの売価も式 × 段（名前で渡されても）",
                sword.attributes.get(prices.SELL) == 480, sword.attributes)

    print("[式が無い品はゲームの新しい額に段が乗る]")
    ctx = fresh_gate()
    prices.adjust("405", lambda it, key, price: price * 1.5)
    cup = shop_item("item_10", {prices.SELL: 40, "item_detail": "tool"})
    player = Holder(None, cup)
    owner = Holder("99")
    call(ctx, "__main__:InstantaleApp.set_shop_price_for_player", None, cup)
    ok &= check("売る前はゲームの額 × 段", cup.attributes[prices.SELL] == 60, cup.attributes)
    trade(ctx, sold_at, cup, owner, prices.BUY, 100)
    ok &= check("ゲームの買価 × 段", cup.attributes.get(prices.BUY) == 150, cup.attributes)

    print("[値切った売価は売ったら外れる]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    ring = shop_item("item_11", {prices.SELL: 40, "item_detail": "accessory"})
    player = Holder(None, ring)
    owner = Holder("99")
    app = types.SimpleNamespace(player=player)
    manager = types.SimpleNamespace(app=app, snapshot=dict)
    call(ctx, prices.SIGHT_TARGETS[0][0], app, owner, player, None, "shop")
    haggle(ctx, manager, lambda: ring.attributes.__setitem__(prices.SELL, 500))
    ok &= check("値切った売価", prices.haggled(ring) == {prices.SELL: 500}, prices.haggled(ring))
    paid = trade(ctx, sold_at, ring, owner, prices.BUY, 300)
    ok &= check("値切った額で売れる", paid == 500, paid)
    ok &= check("固定が外れる", prices.haggled(ring) == {}, prices.haggled(ring))
    ok &= check("店の買価は式の額", ring.attributes.get(prices.BUY) == 800, ring.attributes)
    return ok


# ---------------------------------------------------------------------------
# 品を作る入口の戻り（GAME.md §2.13.1.5・VERIFICATION.md §3.121）
# ---------------------------------------------------------------------------


def made_main():
    from instantale_modloader import items
    ok = True

    print("[品を作る入口の戻り]")
    ctx = fresh_gate()
    prices.declare_base("129", lambda it: {prices.BUY: 800, prices.SELL: 320})
    dagger = shop_item("item_20", {prices.BUY: 100, "item_detail": "small_weapon"})
    result = make_from_data(ctx, dagger, Holder("99"))
    ok &= check("戻りはそのまま返す", result == (dagger, "item_20"), result)
    ok &= check("tuple の戻りでも作った品を組み直す", dagger.attributes[prices.BUY] == 800,
                dagger.attributes)
    loaded = shop_item("item_21", {prices.BUY: 100, "item_detail": "accessory"})
    owner = Holder("99")
    ok &= check("None の戻りはそのまま返す", make_from_dict(ctx, loaded, owner) is None)
    ok &= check("None の戻りは渡した鍵で持ち物から引いて組み直す",
                loaded.attributes[prices.BUY] == 800, loaded.attributes)
    named = shop_item("item_22", {prices.BUY: 100, "item_detail": "accessory"})
    make_from_dict(ctx, named, Holder("99"), by_keyword=True)
    ok &= check("キーワードで来た引数でも引く", named.attributes[prices.BUY] == 800,
                named.attributes)

    entry = "generate_item_from_item_data"
    ok &= check("品の無い tuple は None", items.made_item(entry, ("a", "b"), (), {}) is None)
    ok &= check("鍵の無い入口の None は None",
                items.made_item("generate_item_from_ready_made_data", None, (Holder("99"),), {})
                is None)
    ok &= check("辞書は品と読まない",
                items.made_item(entry, ({"attributes": {}}, "k"), (), {}) is None)
    ok &= check("持ち物に無い鍵は None",
                items.made_item("generate_item_from_dict", None, ({}, "nope", Holder("99")), {})
                is None)
    return ok


if __name__ == "__main__":
    sys.exit(main())
