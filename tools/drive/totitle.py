# -*- coding: utf-8 -*-
"""ゲームを閉じずに題の画面へ戻る（ゲーム自身の `return_to_title`）。

読み直しは続けて `clicktext.py` で「開始する」と世界の札を押す（nav.sh の `reload`）。
ゲームは行動のたびに保存しているので、戻る前の状態がそのまま読み直される。
保存とロードをまたぐ確認（ボタンの添え字・好感度が残るか）を、起動し直さずに回すのに使う。
"""


def main(say):
    app().return_to_title()
    say("T returned to the title")
