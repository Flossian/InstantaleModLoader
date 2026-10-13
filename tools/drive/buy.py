# -*- coding: utf-8 -*-
"""開いている売買の窓で、店の棚の品を1つ手持ちの側へ落として「買う」を押す。ARGS: {"item": 品名}

確認の窓の「品名:額G」と、買った後に品が手持ちに入ったか・棚に残ったか・所持金の増減を返す。
"""
KEEP_OPEN = True


def main(say):
    st = {}
    name = ARGS["item"]

    def shelf_widget():
        for node in ui.walk_widgets(Window):
            item = getattr(node, "item_instance", None)
            if type(node).__name__ == "InventoryItem" and getattr(item, "name", None) == name \
                    and getattr(item, "obtainer", None) is not app().player:
                return node
        return None

    def drop():
        widget = shelf_widget()
        say("shop has item: {}".format(widget is not None))
        if widget is None:
            return 0.2
        grid = [n for n in widget.parent.children
                if getattr(n, "obtainer", None) is app().player and hasattr(n, "place_new_item")][0]
        # 大きな品（2x2 など）は品の大きさで空きを探す。掴むのも落とすのも品の左下のマスの真ん中
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
        start = widget.parent.to_window(widget.x + 32, widget.y + 32)
        end = grid.parent.to_window(grid.x + cell[0] * 65 + 32, grid.y + cell[1] * 65 + 32)
        st["gold"] = app().player.gold
        _touch([start, end])
        return 0.8

    def buy():
        labels = [n.text for n in find("Label") if ":" in (n.text or "") and (n.text or "").endswith("G")]
        say("confirm {}".format(labels))
        buttons = find("Button", "買う")
        say("buy buttons {}".format(len(buttons)))
        if buttons:
            click(buttons[0])
        return 1.5

    def report():
        mine = item_widget(name, app().player)
        say("after: player has item {} / shop still has one {} / gold {} -> {}".format(
            mine is not None, shelf_widget() is not None, st.get("gold"), app().player.gold))
        return 0.2

    Steps(say, [drop, buy, report], timeout=60).start()
