# -*- coding: utf-8 -*-
"""人物に話しかけて会話を始める（ゲーム自身の `ConversationStartManager` を `process_choice` に渡す）。ARGS: {"npc": 人物の id}

選択肢の一覧に出ない相手（仲間が1人だけのときの仲間など）と話すのに使う。
`300_event_facility_arrival` が施設で話しかけるのと同じ口なので、会話の始まりを包む MOD はそのまま動く。
手が空いているとき（施設の画面）に流す。返事を待つなら続けて `w 会話を終了する`。
"""


def main(say):
    import __main__
    a = app()
    npc_id = str(ARGS["npc"])
    who = a.world.characters.get(npc_id)
    if who is None:
        say("C no such npc {}".format(npc_id))
        return
    manager = getattr(__main__, "ConversationStartManager")(a, npc_id)
    a.process_choice(manager, who.name)
    say("C talking to {} {}".format(npc_id, who.name))
