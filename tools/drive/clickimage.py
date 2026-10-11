# -*- coding: utf-8 -*-
"""文字の無い絵のボタンを、絵のファイル名の一部で引いて本物の入力で押す。ARGS: {"image": ファイル名の一部}

キャラを作る画面のさいころ（`dice_1.png`）のように、文字が無く `clicktext.py` で引けないボタンに使う。
"""


def main(say):
    part = ARGS["image"]
    hits = []
    for n in ui.walk_widgets(Window):
        source = getattr(n, "normal_image", None) or getattr(n, "source", None) or ""
        if isinstance(source, str) and part in source and not getattr(n, "disabled", False):
            hits.append(n)
    say("image {!r}: {} hit(s) {}".format(part, len(hits), [type(n).__name__ for n in hits]))
    if hits:
        click(hits[0])
