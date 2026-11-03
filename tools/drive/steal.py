# -*- coding: utf-8 -*-
"""`336_crime_overhaul` の「店で盗む」を売買の窓で動かす。ARGS: {"mode": "steal" | "caught" | "decline", "enter": "open" | 店の名前 | ""}

    mode    steal: 棚の品を必ず盗める乱数で盗み、次の品の確認で「いいえ」を押して窓を閉じる
            caught: 必ずしくじる乱数で盗む（衛兵の戦闘になる）
            decline: 確認で「いいえ」を押すだけ
    enter   "open": 窓はもう開いている / 店の名前: 「出る」→ 店 →「売買する」/ 省略: 「売買する」から

問い合わせる品は、棚の上から順に、手持ちの空きに入る大きさのもの。
336 は手持ちに入らない品の「盗む」では確認を出さない（2026-11-03。2x2 の品で確認が出ず、この道具が落ちた）。
待ちは画面の状態で決める（窓が開いて棚に品が並ぶ・「盗む」が出る・確認の窓が出る）。
出なければその旨を書いて残りを飛ばし、乱数を戻して窓を閉じる。

乱数は 336 の `_RNG` を手順の間だけ差し替え、終わったら戻す。
"""
import time

KEEP_OPEN = True


def main(say):
    MOD = mod("crime_overhaul")
    real = MOD._RNG
    state = {"abort": False}

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

    def fits(widget):
        """手持ちの格子に、その品の大きさの空きがあるか。"""
        grids = [n for n in widget.parent.children
                 if getattr(n, "obtainer", None) is app().player and hasattr(n, "place_new_item")]
        if not grids:
            return False
        grid = grids[0]
        w = int(getattr(widget.item_instance, "width_slots", 1) or 1)
        h = int(getattr(widget.item_instance, "height_slots", 1) or 1)
        return any(grid.is_valid_placement(gx, gy, w, h)
                   for gy in range(grid.rows - h + 1) for gx in range(grid.cols - w + 1))

    def steal_button():
        hits = [n for n in Window.children if getattr(n, "text", None) == "盗む"]
        return hits[0] if hits else None

    def confirm_labels():
        return [n.text for n in find("Label") if "成功率" in (n.text or "")]

    def until(label, check, limit):
        """`check()` が真になるまで待つ。`limit` 秒で諦め、残りの手順を飛ばす印を立てる。"""
        started = []

        def step():
            if state["abort"]:
                return 0.1
            if check():
                return 0.2
            if not started:
                started.append(time.time())
            if time.time() - started[0] > limit:
                say("gave up waiting for {}".format(label))
                state["abort"] = True
                return 0.1
            return "again"
        return step

    def press(text):
        def step():
            say("press {!r}: {}".format(text, press_choice(text)))
            return 0.5 if text == "売買する" else "wait"
        return step

    def ask():
        def step():
            if state["abort"]:
                return 0.1
            candidates = [w for w in shop_items() if fits(w)]
            if not candidates:
                say("no shop item fits the free cells")
                state["abort"] = True
                return 0.1
            widget = candidates[0]
            say("ask about {!r}".format(widget.item_instance.name))
            widget.show_popup_menu(widget.to_window(*widget.center))
            return 0.2

        def pick():
            if state["abort"]:
                return 0.1
            click(steal_button())
            return 0.2
        return [step, until("the steal menu", lambda: steal_button() is not None, 10), pick,
                until("the confirmation", lambda: bool(confirm_labels()), 10)]

    def answer(text, sure=None):
        def step():
            if state["abort"]:
                return 0.1
            say("confirm: {}".format(confirm_labels()))
            if sure == "fail":
                MOD._RNG = Fixed(0.99)
            elif sure:
                MOD._RNG = Fixed(0.0)
            buttons = find("Button", text)
            if not buttons:
                say("no {!r} button".format(text))
                state["abort"] = True
                return 0.1
            click(buttons[0])
            return 1.5
        return step

    def restore():
        MOD._RNG = real
        return 0.2

    def close():
        if trade_open():
            close_trade_window()
        return "wait"

    enter = ARGS.get("enter", "")
    if enter == "open":
        steps = []
    elif enter:
        steps = [press("出る"), press(enter), press("売買する")]
    else:
        steps = [press("売買する")]
    if enter != "open":
        # 品揃えの生成（LLM）を待つ店がある。窓が開いて棚に品が並ぶまで待つ
        steps.append(until("the trade window", lambda: trade_open() and bool(shop_items()), 180))
    mode = ARGS.get("mode", "decline")
    if mode == "caught":
        steps += ask() + [answer("はい", sure="fail"), restore]
    elif mode == "steal":
        steps += ask() + [answer("はい", sure=True), restore] + ask() + [answer("いいえ"), restore, close]
    else:
        steps += ask() + [answer("いいえ"), restore, close]
    Steps(say, steps, timeout=300).start()
