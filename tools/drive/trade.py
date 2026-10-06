# -*- coding: utf-8 -*-
"""売買の窓。ARGS: {"do": "open" | "close" | "status" | "prices"}

    open    選択肢の「売買する」を押す（店主の台詞の生成を待つことがあるので、開いたかは status で見る）
    close   開いている窓を閉じる
    status  窓が開いているか・手が空いたか・選択肢
    prices  手持ちの品の売価を、ローダの値段の表（`prices`）の段ごとに並べる（どの MOD が値を動かしたか）
"""
KEEP_OPEN = True
from instantale_modloader import prices as _prices


def sell_prices(say):
    reg = _prices._item_registry()
    base = reg.get("base")
    seen = set()
    for node in ui.walk_widgets(Window):
        item = getattr(node, "item_instance", None)
        if type(node).__name__ != "InventoryItem" or item is None \
                or getattr(item, "obtainer", None) is not app().player or id(item) in seen:
            continue
        seen.add(id(item))
        built = base[1](item) if base else None
        price = (built or {}).get("売価")
        start, steps = price, []
        for owner, fn, _t, _w in reg["adjust"]:
            got = fn(item, "売価", price)
            if got is not None and got != price:
                steps.append("{}:{}->{}".format(owner, price, got))
                price = got
        say("P {} base {} now {} {}".format(item.name, start, price, steps))


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
    elif do == "prices":
        sell_prices(say)
        say("<done>")
    else:
        say("Q trade {} settled {} choices {}".format(trade_open(), settled(), choices()))
        say("<done>")
