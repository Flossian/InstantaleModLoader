# -*- coding: utf-8 -*-
"""開いている売買の窓で、店の棚の品を1つ手持ちの側へ落として「買う」を押す。ARGS: {"item": 品名}

確認の窓の「品名:額G」と、買う前後の手持ち・棚の同名の品の数と所持金の増減を返す。
"""
KEEP_OPEN = True


def main(say):
    st = {}
    name = ARGS["item"]

    def same_name(mine):
        """同名の品の部品。`mine` が真なら手持ち、偽なら棚。"""
        found = []
        for node in ui.walk_widgets(Window):
            item = getattr(node, "item_instance", None)
            if type(node).__name__ == "InventoryItem" and getattr(item, "name", None) == name \
                    and (getattr(item, "obtainer", None) is app().player) is mine:
                found.append(node)
        return found

    def shelf_widget():
        found = same_name(False)
        return found[0] if found else None

    def drop():
        # 数で比べる。同名の品を前から持っている・棚に2つある、では「在るか」で決まらない
        st["mine"], st["shelf"] = len(same_name(True)), len(same_name(False))
        widget = shelf_widget()
        say("shop has item: {}".format(widget is not None))
        if widget is None:
            return 0.2
        grid = [n for n in widget.parent.children
                if getattr(n, "obtainer", None) is app().player and hasattr(n, "place_new_item")][0]
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

    def buy():
        labels = [n.text for n in find("Label") if ":" in (n.text or "") and (n.text or "").endswith("G")]
        say("confirm {}".format(labels))
        buttons = find("Button", "買う")
        say("buy buttons {}".format(len(buttons)))
        if buttons:
            click(buttons[0])
        return 1.5

    def report():
        mine, shelf = len(same_name(True)), len(same_name(False))
        bought = st.get("mine") is not None and mine == st["mine"] + 1 \
            and shelf == st["shelf"] - 1
        say("after: bought {} / player has {} -> {} / shop has {} -> {} / gold {} -> {}".format(
            bought, st.get("mine"), mine, st.get("shelf"), shelf, st.get("gold"),
            app().player.gold))
        return 0.2

    Steps(say, [drop, buy, report], timeout=60).start()
