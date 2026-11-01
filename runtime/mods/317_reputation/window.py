# -*- coding: utf-8 -*-
"""評判の画面を描く（Kivy）。中身は `reputation.view_model` が組んだものをそのまま描く。

入口は画面左下の MOD 用のボタンの一覧（`hudicons`。TECH.md §3.3.15）。
部品はローダの `panels`（TECH.md §3.3.17）で、`338_` / `339_` の画面と同じ作り。
上の枠に二つ名・名声と悪名の目盛り・評判の立つ土地の数、下の「各地の評判」の枠に土地ごとの札（スクロールする）。
二つ名と各地の呼び名の右に再生成のボタンを置く。絵は `121_` の人物欄の引き直しと同じさいころ（`ui.icon_strokes`）。
"""
from instantale_modloader import panels, ui

TITLE = "評判"
GOLD = (0.84, 0.70, 0.38, 1)
RED = (0.886, 0.329, 0.282, 1)
LANDS_TITLE = "各地の評判"
EMPTY = "まだどの土地にも評判は立っていない。"
UNCOMPILED = "噂をまとめているところ"
REROLL_ICON = "さいころ"
#: 再生成のボタンの大きさ（upx。`121_` の人物欄の引き直しのボタンと同じ）。
REROLL_SIZE = 24.0
#: 再生成の最中の絵の濃さ（押せない合図）。ふだんは `ui.paint_icon` の既定。
BUSY_ALPHA = 0.3
ICON_ATTR = ui.MOD_WIDGET_PREFIX + "reputation_reroll_icon"


def reroll_button(busy, on_press):
    """名前の再生成のボタン（さいころの絵）。押すと絵を薄くして押せなくする。

    編み終わると画面ごと開き直すので、元に戻す処理は持たない。作れない環境では None。
    """
    button = ui.make_icon_button(size=REROLL_SIZE)
    if button is None:
        return None
    button.pos_hint = {"center_y": 0.5}
    flags = {"busy": bool(busy)}
    button.disabled = flags["busy"]

    def repaint(*_args):
        ui.paint_icon(button, ui.icon_strokes(REROLL_ICON), attr=ICON_ATTR,
                      key=(REROLL_ICON, flags["busy"]),
                      **({"alpha": BUSY_ALPHA} if flags["busy"] else {}))

    def pressed(_instance):
        flags["busy"] = True
        button.disabled = True
        repaint()
        on_press()

    button.bind(pos=repaint, size=repaint, on_release=pressed)
    repaint()
    return button


def open_window(app, model, *, on_dismiss=None, write=None, on_reroll=None, on_land_reroll=None,
                sound=True):
    """画面を開いて返す。

    `on_reroll()` を渡すと二つ名の行に、`on_land_reroll(土地id)` を渡すと呼び名の付いた土地の札に、
    再生成のボタンを置く。
    """

    land_buttons = []

    def with_reroll(row, busy, on_press, scrolled=False):
        if on_press is None:
            return row
        button = reroll_button(busy, on_press)
        if button is not None:
            row.add_widget(button)
            if scrolled:
                land_buttons.append(button)
        return row

    def summary(kit):
        box = kit.card(spacing=8)
        total = model["fame"] + model["notoriety"]
        if model["use_epithet"]:
            if model["epithet"]:
                box.add_widget(with_reroll(
                    kit.head("「{}」".format(model["epithet"]),
                             sub="土地を問わずこの二つ名で知られている", sub_scale=panels.META),
                    model.get("rerolling"), on_reroll))
            elif model["eligible"]:
                # 名が付かなかった回も、ここから頼み直せる
                box.add_widget(with_reroll(
                    kit.head("二つ名はまだ定まっていない",
                             sub="噂がまとまるのを待っている", sub_scale=panels.META),
                    model.get("rerolling"), on_reroll))
            else:
                box.add_widget(kit.head(
                    "二つ名はまだ無い", sub="名声と悪名の合計が{}、評判の立つ土地が{}つで付く".format(
                        model["threshold"], model["min_lands"]), sub_scale=panels.META))
        box.add_widget(kit.label(panels.markup([
            ("名声 ", panels.DIM), (str(model["fame"]), GOLD),
            ("　悪名 ", panels.DIM), (str(model["notoriety"]), RED if model["notoriety"] else None),
            ("　評判の立つ土地 ", panels.DIM),
            ("{}／{}".format(model["lands"], model["min_lands"]), None)]), use_markup=True))
        if model["use_epithet"]:
            box.add_widget(kit.meter(total / float(max(1, model["threshold"])), GOLD,
                                     left="名声＋悪名 {}".format(total),
                                     right="二つ名の線 {}".format(model["threshold"])))
        notes = ["名声は片付けた依頼・果たした物語の依頼・闘技場の勝ちから数える。重い依頼ほど大きく、軽い依頼はほとんど増えない。",
                 "悪名は手配が重くなった分の累計で、手配が解けても減らない。"]
        if model["use_land_names"]:
            notes.append("1つの土地で名声と悪名の合計が{}を越えると、その土地での呼び名が付く。".format(
                model["land_threshold"]))
        box.add_widget(kit.label("\n".join(notes), panels.META, panels.DIM))
        return box

    def land(kit, row):
        box = kit.card()
        head = kit.head(row["name"], sub="「{}」".format(row["nickname"]) if row["nickname"] else "",
                        sub_scale=panels.BODY)
        box.add_widget(with_reroll(
            head, row.get("rerolling"),
            (lambda area_id=row["id"]: on_land_reroll(area_id))
            if on_land_reroll is not None and row.get("named") else None, scrolled=True))
        pieces = [("名声 ", panels.DIM), (str(row["fame"]), GOLD if row["fame"] else None)]
        if row["notoriety"]:
            pieces += [("　悪名 ", panels.DIM), (str(row["notoriety"]), RED)]
        if isinstance(row["lawfulness"], int):
            law = row["lawfulness"]
            pieces += [("　手配度 ", panels.DIM), (str(law), RED if law < 0 else None)]
        box.add_widget(kit.label(panels.markup(pieces), panels.META, use_markup=True))
        if row["reputation"]:
            box.add_widget(kit.barred(kit.label(row["reputation"]), panels.QUOTE))
        elif row["standing"]:
            # 評判の立つ土地だが、まだ編み終わっていない（読み込み・移動・日送りの照合で編む）
            box.add_widget(kit.label(UNCOMPILED, panels.META, panels.FAINT))
        else:
            box.add_widget(kit.label("評判はまだ立っていない（行いが{}件で立つ）".format(model["min_deeds"]),
                                     panels.META, panels.FAINT))
        return box

    def lands_section(kit):
        """見出しつきの枠に土地の札を積む（`kit.section` と同じ作りで、スクロールを手元に持つ）。

        さいころの絵は線（`ui.paint_icon`）で描いていて、スクロールの外へ送った札の分がスクロールの枠で切られずに
        枠の外へ途切れ途切れに漏れた（実機。文字は切られる）。見えている範囲から外れたボタンは透明にする。
        """
        from kivy.clock import Clock
        from kivy.uix.boxlayout import BoxLayout
        box = kit.framed(BoxLayout(orientation="vertical", padding=kit.px(14), spacing=kit.px(8)))
        box.add_widget(kit.label(LANDS_TITLE, panels.NAME))
        content = kit.column()
        for widget in [land(kit, row) for row in model["rows"]] or [kit.label(EMPTY, panels.META, panels.DIM)]:
            content.add_widget(widget)
        scroller = kit.scroller(content)
        box.add_widget(scroller)

        def clip(*_args):
            bottom = scroller.to_window(scroller.x, scroller.y)[1]
            top = bottom + scroller.height
            for button in land_buttons:
                y = button.to_window(button.x, button.y)[1]
                button.opacity = 1 if (y >= bottom - 1 and y + button.height <= top + 1) else 0

        trigger = Clock.create_trigger(clip, 0)
        scroller.bind(scroll_y=trigger, pos=trigger, size=trigger)
        content.bind(pos=trigger, height=trigger)
        trigger()
        return box

    def build(kit):
        from kivy.uix.boxlayout import BoxLayout
        body = BoxLayout(orientation="vertical", spacing=kit.px(14))
        body.add_widget(summary(kit))
        # 二つ名（世界に1つ）と土地ごとの札を、見出しつきの別の枠に分ける
        body.add_widget(lands_section(kit))
        return body

    return panels.open_window(app, TITLE, build,
                              subtitle="現在地　" + model["here"] if model.get("here") else "",
                              on_dismiss=on_dismiss, sound=sound,
                              log_exc=(lambda msg: write("WARN " + msg)) if write is not None else None)
