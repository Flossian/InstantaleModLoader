# -*- coding: utf-8 -*-
"""画面の選択肢を本物の入力で1つ押す。ARGS: {"text": ボタンの文字}。画面に無ければ False。"""


def main(say):
    say("press {} -> {}".format(ARGS["text"], press_choice(ARGS["text"])))
