# -*- coding: utf-8 -*-
"""勢力と情勢の窓を描く（Kivy）。中身は `view.build` が組んだものをそのまま描く。

見た目と言葉は VERIFICATION.md §3.98「勢力と情勢の窓」。部品はローダの `panels`（TECH.md §3.3.17）で、
`338_` の各地の出来事の窓と同じ作り。左に勢力の札、右に「最近の噂」と「主人公への態度の推移」。
どれもスクロールできる。
"""
from instantale_modloader import panels

from . import rules

TITLE = "勢力と情勢"
#: 段階（-2〜+2）の色。見られ方と勢力どうしの関係で同じ並び。
STAGE_COLORS = {-2: (0.886, 0.329, 0.282, 1), -1: (0.91, 0.659, 0.282, 1), 0: (0.647, 0.647, 0.647, 1),
                1: (0.588, 0.804, 0.471, 1), 2: (0.376, 0.769, 0.588, 1)}
CAUTION = STAGE_COLORS[-1]
EMPTY_RUMORS = "まだ噂は聞こえてこない。"
EMPTY_HISTORY = "まだ態度は動いていない。"
STAGES = (-2, -1, 0, 1, 2)


def open_window(app, model, *, on_dismiss=None, write=None):
    """窓を開いて返す。"""

    def card(kit, data):
        stage = data["stage"]
        color = STAGE_COLORS[stage]
        box = kit.card()
        box.add_widget(kit.head(data["name"], sub=data["kind"], sub_scale=panels.META,
                                badge=(rules.STANDING_NAMES[stage], color), caption="主人公への態度"))
        current = STAGES.index(stage)
        center = STAGES.index(0)
        box.add_widget(kit.gauge([rules.STANDING_NAMES[s] for s in STAGES], current, color,
                                 lit=lambda i: min(current, center) <= i <= max(current, center)))
        if data["title"]:
            box.add_widget(kit.label("勢力内での呼び名　「{}」".format(data["title"]), panels.META, color))
        if data["note"]:
            box.add_widget(kit.label(data["note"]))
        if data["towns"]:
            # 「、」で区切る。街の名前に「・」が入ると（ルナ・ミスト）区切りと見分けられない（VERIFICATION.md §3.98）
            box.add_widget(kit.label("影響下の街　" + "、".join(data["towns"]), panels.META, panels.DIM))
        if data["relations"]:
            pieces = [("他勢力との関係", panels.DIM)]
            for other, rel in data["relations"]:
                pieces += [("　{}と".format(other), None),
                           ("{}関係".format(rules.RELATION_NAMES.get(rel, "")), STAGE_COLORS[rel])]
            box.add_widget(kit.label(panels.markup(pieces), use_markup=True))
        for text in data["cautions"]:
            box.add_widget(kit.barred(kit.label(text, panels.META, CAUTION), CAUTION, width=4))
        return box

    def rumor(kit, item):
        box = kit.stack(spacing=4)
        box.add_widget(kit.label(item["when"], panels.META, panels.DIM))
        box.add_widget(kit.barred(kit.label(item["text"]), panels.QUOTE))
        box.add_widget(kit.label(panels.markup(item["follow"], STAGE_COLORS), panels.META, use_markup=True))
        return box

    def history(kit, item):
        words = kit.stack(spacing=2)
        words.add_widget(kit.label(panels.markup(item["pieces"], STAGE_COLORS), panels.META, use_markup=True))
        if item["reason"]:
            words.add_widget(kit.label("理由　" + item["reason"], panels.TICK, panels.DIM))
        return kit.dated(item["when"], words)

    def build(kit):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.widget import Widget
        if model.get("status") != rules.READY:
            box = BoxLayout(orientation="vertical")
            box.add_widget(kit.label(model.get("message") or ""))
            box.add_widget(Widget())
            return box
        body = BoxLayout(orientation="horizontal", spacing=kit.px(16))
        cards = kit.column()
        for data in model["cards"]:
            cards.add_widget(card(kit, data))
        left = kit.scroller(cards)
        left.size_hint_x = 0.6
        right = BoxLayout(orientation="vertical", spacing=kit.px(14), size_hint_x=0.4)
        right.add_widget(kit.section("最近の噂", [rumor(kit, item) for item in model["rumors"]], EMPTY_RUMORS))
        right.add_widget(kit.section("主人公への態度の推移", [history(kit, item) for item in model["history"]],
                                     EMPTY_HISTORY))
        body.add_widget(left)
        body.add_widget(right)
        return body

    return panels.open_window(app, TITLE, build,
                              subtitle="現在地　" + model["here"] if model.get("here") else "",
                              on_dismiss=on_dismiss,
                              log_exc=(lambda msg: write("WARN " + msg)) if write is not None else None)
