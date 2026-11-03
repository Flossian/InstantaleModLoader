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
        # 大きな品（2x2 など）は品の大きさで空きを探す。掴むのは品の真ん中で、落とす先も同じずれで
        # （2x2 の品は左下のマスの真ん中を掴むと動かなかった。2026-11-03 の実機）
        w = int(getattr(widget.item_instance, "width_slots", 1) or 1)
        h = int(getattr(widget.item_instance, "height_slots", 1) or 1)
        cell = None
        for gy in range(grid.rows - h, -1, -1):
            for gx in range(grid.cols - w + 1):
                if grid.is_valid_placement(gx, gy, w, h):
                    cell = (gx, gy)
                    break
            if cell:
                break
        say("free cell {} for {}x{}".format(cell, w, h))
        if cell is None:
            return 0.2
        dx, dy = widget.width / 2.0, widget.height / 2.0
        start = widget.parent.to_window(widget.x + dx, widget.y + dy)
        end = grid.parent.to_window(grid.x + cell[0] * 65 + dx, grid.y + cell[1] * 65 + dy)
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
