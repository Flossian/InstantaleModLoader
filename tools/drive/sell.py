# -*- coding: utf-8 -*-
"""開いている売買の窓で、手持ちの品を1つ店の側へ落として「売る」を押す。ARGS: {"item": 品名}

確認の窓の「品名:額G」と、売った後に品が手持ちに残ったか・所持金の増減を返す。
"""
KEEP_OPEN = True


def main(say):
    st = {}
    name = ARGS["item"]

    def drop():
        widget = item_widget(name, app().player)
        say("player has item: {}".format(widget is not None))
        grid = [n for n in widget.parent.children
                if getattr(n, "obtainer", None) is not app().player and hasattr(n, "place_new_item")][0]
        cell = None
        for gy in range(grid.rows - 1, -1, -1):
            for gx in range(grid.cols):
                if grid.is_valid_placement(gx, gy, 1, 1):
                    cell = (gx, gy)
                    break
            if cell:
                break
        start = widget.parent.to_window(widget.x + 32, widget.y + 32)
        end = grid.parent.to_window(grid.x + cell[0] * 65 + 32, grid.y + cell[1] * 65 + 32)
        st["gold"] = app().player.gold
        _touch([start, end])
        return 0.8

    def sell():
        labels = [n.text for n in find("Label") if ":" in (n.text or "") and (n.text or "").endswith("G")]
        say("confirm {}".format(labels))
        buttons = find("Button", "売る")
        say("sell buttons {}".format(len(buttons)))
        if buttons:
            click(buttons[0])
        return 1.5

    def report():
        w = item_widget(name, app().player)
        say("after: player still has item {} gold {} -> {}".format(w is not None, st["gold"], app().player.gold))
        return 0.2

    Steps(say, [drop, sell, report], timeout=60).start()
