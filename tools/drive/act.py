# -*- coding: utf-8 -*-
"""入力欄に文を書いて send を押し、手が空くまで待って、所持金と手配度の変化を返す。ARGS: {"text": 送る文}

自由行動（施設の画面）でも会話の中でも、入力欄が1つ見えていれば同じ手順で送れる。
返事を LLM が書くので、`--wait` は長めに（数分）取る。
"""
KEEP_OPEN = True


def main(say):
    state = {}

    def send():
        state["gold"] = app().player.gold
        state["law"] = lawfulness()
        boxes = [n for n in find("TextInput") if "入力" in (getattr(n, "hint_text", "") or "")]
        say("input boxes {}".format(len(boxes)))
        boxes[0].text = ARGS["text"]
        button = find("Button", "send")
        say("send buttons {}".format(len(button)))
        click(button[0])
        return 5.0

    def report():
        say("gold {} -> {} ({:+d})".format(state["gold"], app().player.gold,
                                          app().player.gold - state["gold"]))
        say("lawfulness {} -> {}".format(state["law"], lawfulness()))
        say("choices {}".format(choices()))
        return 0.2

    Steps(say, [send, lambda: "wait", report], timeout=ARGS.get("timeout", 400)).start()
