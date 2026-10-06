# -*- coding: utf-8 -*-
"""`336_crime_overhaul` の「店で盗む」を売買の窓で動かす。ARGS: {"mode": "steal" | "caught" | "decline", "enter": "open" | 店の名前 | ""}

    mode    steal: 棚の先頭の品を必ず盗める乱数で盗み、次の品の確認で「いいえ」を押して窓を閉じる
            caught: 必ずしくじる乱数で盗む（衛兵の戦闘になる）
            decline: 確認で「いいえ」を押すだけ
    enter   "open": 窓はもう開いている / 店の名前: 「出る」→ 店 →「売買する」/ 省略: 「売買する」から

乱数は 336 の `_RNG` を手順の間だけ差し替え、終わったら戻す。
"""
KEEP_OPEN = True


def main(say):
    MOD = mod("crime_overhaul")
    real = MOD._RNG

    class Fixed(object):
        def __init__(self, value):
            self.value = value

        def random(self):
            return self.value

        def choice(self, seq):
            return real.choice(seq)

    def shop_items():
        keeper_items = []
        for node in ui.walk_widgets(Window):
            item = getattr(node, "item_instance", None)
            if type(node).__name__ == "InventoryItem" and item is not None \
                    and getattr(item, "obtainer", None) is not app().player:
                keeper_items.append(node)
        return sorted(keeper_items, key=lambda n: (-n.y, n.x))

    def press(text):
        def step():
            say("press {!r}: {}".format(text, press_choice(text)))
            return 4.0 if text == "売買する" else "wait"
        return step

    def ask(index):
        def step():
            widget = shop_items()[index]
            say("ask about {!r}".format(widget.item_instance.name))
            widget.show_popup_menu(widget.to_window(*widget.center))
            Clock.schedule_once(lambda _dt: click([n for n in Window.children
                                                   if getattr(n, "text", None) == "盗む"][0]), 0.4)
            return 1.6
        return step

    def answer(text, sure=None):
        def step():
            labels = [n.text for n in find("Label") if "成功率" in (n.text or "")]
            say("confirm: {}".format(labels))
            if sure == "fail":
                MOD._RNG = Fixed(0.99)
            elif sure:
                MOD._RNG = Fixed(0.0)
            click(find("Button", text)[0])
            return 1.5
        return step

    def restore():
        MOD._RNG = real
        return 0.2

    def close():
        close_trade_window()
        return "wait"

    enter = ARGS.get("enter", "")
    if enter == "open":
        steps = []
    elif enter:
        steps = [press("出る"), press(enter), press("売買する")]
    else:
        steps = [press("売買する")]
    mode = ARGS.get("mode", "decline")
    if mode == "caught":
        steps += [ask(0), answer("はい", sure="fail"), restore]
    elif mode == "steal":
        steps += [ask(0), answer("はい", sure=True), restore, ask(0), answer("いいえ"), close]
    else:
        steps += [ask(0), answer("いいえ"), close]
    Steps(say, steps, timeout=300).start()
