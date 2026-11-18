# -*- coding: utf-8 -*-
"""売買の窓。ARGS: {"do": "open" | "close" | "status" | "prices" | "shop"}

    open    選択肢の「売買する」を押す（店主の台詞の生成を待つことがあるので、開いたかは status で見る）
    close   開いている窓を閉じる
    status  窓が開いているか・手が空いたか・選択肢
    prices  手持ちの品の売価を、ローダの値段の表（`prices`）の段ごとに並べる（どの MOD が値を動かしたか）
    shop    店の棚の品の買価（店で買うときの額）を、同じく段ごとに並べる
"""
KEEP_OPEN = True
from instantale_modloader import prices as _prices


def axis_price(say, reg, item, key):
    """段を乗せる前の額と軸の名前。本体（`prices._axis_prices`）と同じ順で引くが、台帳は書かない。

    式が組めなければ、台帳に控えたゲームの額、それも無ければ品に今ある額（まだ書き換えていない）。
    """
    base = reg.get("base")
    if base is not None:
        try:
            built = base[1](item)
        except Exception as exc:
            built = None
            say("P base from {!r} failed: {!r}".format(base[0], exc))
        if isinstance(built, dict):
            return _prices._price_number(built.get(key)), "base"
    known = reg["ledger"].get(item)
    if isinstance(known, dict) and key in (known.get("game") or {}):
        return _prices._price_number(known["game"][key]), "game"
    _field, attributes = _prices.read_item(item)
    return _prices._price_number(attributes.get(key)), "game"


def sell_prices(say, shop=False):
    # 段の回し方は本体（`prices._apply_one`）に揃える。
    # 額が引けない品に段を呼ばない・段の例外は握って次へ・額は数として読む。
    reg = _prices._item_registry()
    seen = set()
    for node in ui.walk_widgets(Window):
        item = getattr(node, "item_instance", None)
        if type(node).__name__ != "InventoryItem" or item is None \
                or (getattr(item, "obtainer", None) is app().player) is shop or id(item) in seen:
            continue
        seen.add(id(item))
        key = "買価" if shop else "売価"
        price, axis = axis_price(say, reg, item, key)
        if price is None:
            say("P {} no {} to start from".format(item.name, key))
            continue
        start, steps = price, []
        for owner, fn, _t, _w in _prices._layers(reg, True):
            try:
                got = _prices._price_number(fn(item, key, price))
            except Exception as exc:
                steps.append("{}:failed {!r}".format(owner, exc))
                continue
            if got is not None and abs(got - price) >= 0.0001:
                steps.append("{}:{}->{}".format(owner, price, got))
                price = got
        say("P {} {} {} now {} {}".format(item.name, axis, start, int(round(price)), steps))


def main(say):
    do = ARGS.get("do", "status")
    if do == "open":
        say("open {}".format(press_choice("売買する")))
        say("<done>")
    elif do == "close":
        close_trade_window()

        def later(_dt):
            say("closed; choices {}".format(choices()))
            say("<done>")
        _ALIVE.append(later)
        Clock.schedule_once(later, 1.5)
    elif do in ("prices", "shop"):
        sell_prices(say, shop=do == "shop")
        say("<done>")
    else:
        say("Q trade {} settled {} choices {}".format(trade_open(), settled(), choices()))
        say("<done>")
