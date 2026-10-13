# -*- coding: utf-8 -*-
"""各地の出来事の窓を描く（Kivy）。中身は `incidents.view_model` が組んだものをそのまま描く。

部品はローダの `panels`（TECH.md §3.3.17）で、`339_` の勢力と情勢の窓と同じ作り。
左に進行中の出来事の札（段階の札と3段の目盛り・日数・概要・影響・依頼）、右に「この1年に終わったもの」。
どちらもスクロールできる。見た目は VERIFICATION.md §3.97「各地の出来事の窓」。
"""
from instantale_modloader import panels

from . import incidents

TITLE = "各地の出来事"
#: 段の調子（`incidents.stage_tones`）の色。困り事は 黄・橙・赤、良い出来事は 若草・緑、収束などの静かな段は灰。
TONE_COLORS = {"bad1": (0.886, 0.769, 0.353, 1), "bad2": (0.91, 0.588, 0.251, 1),
               "bad3": (0.886, 0.329, 0.282, 1), "good1": (0.588, 0.804, 0.471, 1),
               "good2": (0.376, 0.769, 0.588, 1), "calm": (0.647, 0.647, 0.647, 1)}
GREEN = (0.376, 0.769, 0.588, 1)
AMBER = (0.91, 0.659, 0.282, 1)
RED = (0.886, 0.329, 0.282, 1)
#: 影響の数字の色。主人公から見て得は緑、損は赤、売り買いが同じ向きに動く（半々）は橙。
EFFECT_COLORS = {incidents.GOOD: GREEN, incidents.BAD: RED, incidents.MIXED: AMBER}
QUEST_COLORS = {incidents.QUEST_PLAIN: panels.TEXT, incidents.QUEST_PENDING: panels.DIM,
                incidents.QUEST_RIVAL: AMBER, incidents.QUEST_GONE: panels.DIM}
END_COLORS = {incidents.END_PLAYER: GREEN, incidents.END_RIVAL: AMBER, incidents.END_NATURAL: panels.DIM}
EMPTY_ACTIVE = "いま、各地で目立った出来事は起きていない。"
EMPTY_ENDED = "この1年に終わった出来事はない。"


def open_window(app, model, *, on_dismiss=None, write=None):
    """窓を開いて返す。"""

    def labeled(kit, head, text, color=panels.TEXT):
        line = panels.markup([(head, panels.DIM), ("　" + text, color)])
        return kit.label(line, use_markup=True)

    def card(kit, data):
        names, tones = data["stages"], data["tones"]
        index = min(max(data["stage"], 1), len(names)) - 1
        color = TONE_COLORS.get(tones[index], panels.TEXT)
        box = kit.card()
        box.add_widget(kit.head(data["town"], sub=data["name"], badge=(data["stage_name"], color), caption="段階"))
        box.add_widget(kit.gauge(names, index, color, lit=lambda i: i < index,
                                 colors=[TONE_COLORS.get(t, panels.TEXT) for t in tones]))
        box.add_widget(kit.label(data["days"], panels.META, panels.DIM))
        if data["summary"]:
            box.add_widget(kit.label(data["summary"]))
        if data["effects"]:
            pieces = [("影響", panels.DIM), ("　", None)] + list(data["effects"])
            box.add_widget(kit.label(panels.markup(pieces, EFFECT_COLORS), use_markup=True))
        if data["quest"] is not None:                  # 良い出来事は依頼を作らないので行も出さない
            text, tone = data["quest"]
            box.add_widget(labeled(kit, "依頼", text, QUEST_COLORS.get(tone, panels.TEXT)))
        return box

    def ended(kit, row):
        words = kit.stack(spacing=2)
        words.add_widget(kit.label("{}　{}".format(row["town"], row["name"])))
        words.add_widget(kit.label(row["how"], panels.META, END_COLORS.get(row["by"], panels.DIM)))
        return kit.dated(row["when"], words)

    def build(kit):
        from kivy.uix.boxlayout import BoxLayout
        body = BoxLayout(orientation="horizontal", spacing=kit.px(16))
        cards = kit.column()
        for data in model["cards"]:
            cards.add_widget(card(kit, data))
        if not model["cards"]:
            cards.add_widget(kit.label(EMPTY_ACTIVE, panels.BODY, panels.DIM))
        left = kit.scroller(cards)
        left.size_hint_x = 0.6
        right = kit.section("この1年に終わったもの", [ended(kit, row) for row in model["ended"]], EMPTY_ENDED)
        right.size_hint_x = 0.4
        body.add_widget(left)
        body.add_widget(right)
        return body

    return panels.open_window(app, TITLE, build,
                              subtitle="現在地　" + model["here_name"] + model["here"]
                              if model.get("here_name") else "",
                              on_dismiss=on_dismiss,
                              log_exc=(lambda msg: write("WARN " + msg)) if write is not None else None)
