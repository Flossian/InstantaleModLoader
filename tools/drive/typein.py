# -*- coding: utf-8 -*-
"""入力欄に文字を書く（押しはしない）。ARGS: {"fields": {入力欄の hint_text の一部: 文字, …}}

世界を作る画面（名前・概要）やキャラを作る画面の欄に使う。
書いた後に押すボタンは `clicktext.py` で押す。
"""


def main(say):
    boxes = find("TextInput")
    for part, text in ARGS["fields"].items():
        hits = [n for n in boxes if part in (getattr(n, "hint_text", "") or "")]
        say("field {!r}: {} hit(s)".format(part, len(hits)))
        if hits:
            hits[0].text = text
            say("  wrote {} char(s)".format(len(hits[0].text)))
