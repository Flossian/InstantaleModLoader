# -*- coding: utf-8 -*-
"""動いている MOD の定数（GUI の設定値）を書き換える。ARGS: {"mod": フォルダ名の一部, "name": 定数, "value": 値}

MOD の多くは設定を定数で持ち、呼ばれるたびに読むので、すぐ効く。
MOD は1回の起動で何度か当て直され、そのたびに設定ファイルから読み直されるので、効かせたい操作の直前に書く。
終わったら元の値へ戻す（返す行に元の値が出る）。
"""


def main(say):
    m = mod(ARGS["mod"])
    if m is None:
        say("V no mod matches {!r}".format(ARGS["mod"]))
        return
    old = getattr(m, ARGS["name"], None)
    setattr(m, ARGS["name"], ARGS["value"])
    say("V {} {} {!r} -> {!r}".format(m.__name__, ARGS["name"], old, getattr(m, ARGS["name"])))
