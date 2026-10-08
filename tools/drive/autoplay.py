# -*- coding: utf-8 -*-
"""依頼の中などを、ゲームの中で手が空くたびに選択肢を選んで進める。外からの見張りを挟まないので、押す間が空かない。

    ARGS: {"prefer": [語, …], "avoid": [語, …], "stop": [語, …], "steps": 上限, "timeout": 秒}

手が空くと（`settled()`）、画面の選択肢から `prefer` の語を先に含むものを選ぶ（無ければ先頭。`avoid` の語を含むものは選ばない）。
依頼の中から始めたときは、依頼を出たら止める（`ui.current_quest_id`。戻る先は街の入口などで、決まった選択肢が並ぶとは限らない）。
ほかに `stop` の語を含む選択肢が並んだら止める（既定は施設の「会話する」「クエスト掲示板」）。
1手ごとに `A 番号 [選択肢] -> 押したもの` を書く。ゲームオーバーでも止める。
"""
KEEP_OPEN = True

PREFER = ["攻撃", "先へ", "奥へ", "進む", "調べる", "帰還", "戻る", "報告", "受け取る", "はい"]
AVOID = ["逃げ", "漁る", "やめる"]
STOP = ["会話する", "クエスト掲示板"]


def main(say):
    prefer = list(ARGS.get("prefer") or []) + PREFER
    avoid = list(ARGS.get("avoid") or AVOID)
    stop = list(ARGS.get("stop") or STOP)
    state = {"n": 0, "last": None, "since": time.time()}
    limit = int(ARGS.get("steps", 30))
    deadline = time.time() + float(ARGS.get("timeout", 1500))

    def pick(items):
        # 本文を流している間は「.」「..」「...」の仮の札が並ぶ。押さない
        usable = [c for c in items if c and c.strip(".") and not any(a in c for a in avoid)]
        for word in prefer:
            for c in usable:
                if word in c:
                    return c
        return usable[0] if usable else None

    def tick(_dt):
        if gameover():
            say("A GAMEOVER")
            say("<done>")
            return
        if time.time() > deadline or state["n"] >= limit:
            say("A stop (limit) choices {}".format(choices()))
            say("<done>")
            return
        items = choices()
        if not settled() or not items or any(not c.strip(".") for c in items):
            Clock.schedule_once(tick, 0.5)
            return
        in_quest = ui.current_quest_id(app()) is not None
        if in_quest:
            state["quest"] = True
        elif state.get("quest"):
            # 依頼から戻った（街の入口など、`stop` の語が並ばない場所へ出ることがある）
            say("A done (left the quest) {}".format(items))
            say("<done>")
            return
        if any(any(s == c or s in c for s in stop) for c in items):
            say("A done {}".format(items))
            say("<done>")
            return
        if items == state["last"] and time.time() - state["since"] < 1.5:
            Clock.schedule_once(tick, 0.5)     # 押した直後でまだ画面が替わっていない
            return
        choice = pick(items)
        if choice is None:
            say("A nothing to press {}".format(items))
            say("<done>")
            return
        state["n"] += 1
        ok = press_choice(choice)
        say("A {} {} -> {} ({})".format(state["n"], items, choice, ok))
        state["last"], state["since"] = items, time.time()
        Clock.schedule_once(tick, 1.0)

    _ALIVE.append(tick)
    Clock.schedule_once(tick, 0.1)
