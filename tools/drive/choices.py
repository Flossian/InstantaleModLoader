# -*- coding: utf-8 -*-
"""画面の選択肢・データの選択肢・ボタンの中身・手待ちの旗を1行で返す（画面とデータの食い違いを見る）。"""


def main(say):
    a = app()
    say("D widgets {} data {} buttons {} popup {} enabled {} trade {} settled {}".format(
        choices(), data_choices(), [b.get("text") for b in (a.buttons or [])],
        getattr(a, "is_popup_window_opened", None), getattr(a, "is_button_enabled", None),
        trade_open(), settled()))
