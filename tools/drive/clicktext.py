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

    def bring_into_view(n):
        """一覧（ScrollView）の中なら、画面の中ほどまで送る。送ったら真。

        世界の札が増えると、目当ての札が一覧の外に出る。外のまま押すと、位置に重なった
        別の部品（「戻る」など）を押してしまう（2026-10-11 に題の画面へ戻った）。
        """
        from kivy.uix.scrollview import ScrollView
        node = n.parent
        while node is not None and node is not Window and not isinstance(node, ScrollView):
            node = node.parent
        if not isinstance(node, ScrollView):
            return False
        node.scroll_to(n, padding=400, animate=False)
        return True

    def step(_dt):
        if not texts:
            say("<done>")
            return
        t = texts.pop(0)
        hits = hits_of(t)
        say("click {!r}: {} hit(s) {}".format(t, len(hits), [type(n).__name__ for n in hits]))
        if hits:
            target = hits[-1]
            if bring_into_view(target):
                say("scrolled {!r} into view".format(t))
                Clock.schedule_once(lambda _dt: click(target), 0.3)
            else:
                click(target)
        Clock.schedule_once(step, ARGS.get("pause", 2.0))
    _ALIVE.append(step)
    Clock.schedule_once(step, 0.2)
