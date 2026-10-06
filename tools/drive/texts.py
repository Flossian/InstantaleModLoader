# -*- coding: utf-8 -*-
"""画面の本文から、語を含む行を拾う。ARGS: {"contains": [語, …], "last": 行数}

本文の欄は上へ流れて写真に写らない行があるので、知らせの文が出たか・出ていないかはこちらで見る。
`contains` を省くと全部の行、`last`（既定 20）は後ろから何行返すか。色の印（[color=…]）は外して返す。
"""
import re


def main(say):
    words = list(ARGS.get("contains") or [])
    lines = []
    for node in ui.walk_widgets(Window):
        text = getattr(node, "text", None)
        if not isinstance(text, str) or "\n" not in text and len(text) < 20:
            continue
        for line in re.sub(r"\[/?color[^\]]*\]", "", text).splitlines():
            line = line.strip()
            if line and (not words or any(w in line for w in words)):
                lines.append(line)
    for line in lines[-int(ARGS.get("last", 20)):]:
        say("X {}".format(line[:300]))
    say("X {} line(s)".format(len(lines)))
