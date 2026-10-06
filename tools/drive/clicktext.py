# -*- coding: utf-8 -*-
"""文字の見えている部品の位置を、本物の入力で押す。ARGS: {"texts": [文字, …], "contains": false, "pause": 秒}

選択肢の欄の外（題の画面の「開始する」・世界の札など）を押すのに使う。
押すのは部品の真ん中の画面上の位置なので、その下にある札そのものが押される。
`contains` が真なら、文字の一部が合えば押す（世界の札は名前のほかの字も並ぶ）。
"""
KEEP_OPEN = True


def main(say):
    texts = list(ARGS["texts"])
    contains = bool(ARGS.get("contains"))

    def hits_of(t):
        out = []
        for n in ui.walk_widgets(Window):
            text = getattr(n, "text", None)
            if not isinstance(text, str) or getattr(n, "disabled", False):
                continue
            if text == t or (contains and t in text):
                out.append(n)
        return out

    def step(_dt):
        if not texts:
            say("<done>")
            return
        t = texts.pop(0)
        hits = hits_of(t)
        say("click {!r}: {} hit(s) {}".format(t, len(hits), [type(n).__name__ for n in hits]))
        if hits:
            click(hits[-1])
        Clock.schedule_once(step, ARGS.get("pause", 2.0))
    _ALIVE.append(step)
    Clock.schedule_once(step, 0.2)
