# -*- coding: utf-8 -*-
"""修正: 会話の外で入力欄から送った文を、そのまま1行で本文に残す。

## 何が困っているか

会話の中で送った文は、ゲームが「主人公名: 文」の1行で本文に出す。
会話の外（自由行動・戦闘・裁判の釈明など）で送った文は、本文のどこにも出ない。
本文には GM の語りがいきなり始まるので、読み返すと自分が何をしたのか分からない
（`122_ui_conversation_log` の記録でも、自由行動の前に入力文の行が無い）。

文には何も付けない。
会話の外で送るのはほとんどが行動で、会話の行と同じ「主人公名: 文」だと発言に見える。
頭に印（「＞ 」）を付ける形も試したが、要らないと判断した。

## どこで拾うか

入力欄の送信は `InstantaleApp.on_text_input(self, instance)` が受け、
いまの送り先（`function_correspond_to_input` の `PhaseSpec`）を組み立てて
`InstantaleApp.process_choice(manager, 文)` に渡す（GAME.md §2.2）。

`on_text_input` の間だけ印を立て、その間に `process_choice` が呼ばれたときに限り、
ゲームの処理より先に1行足す。
送信をゲームが受け付けなかったとき（空の文・待機中など）は `process_choice` が来ないので、出さない。
文は `process_choice` に渡った文字列をそのまま使う（ゲームが実際に送った文）。

送り先が会話（`ConversationPhaseManager` / `ConversationInQuestPhase`）のときは足さない。
ゲームが自分で出すので、足すと2行になる。

足す先は本文の待ち行列（`ui.Screen.say` → `app.add_text`）。
`122_ui_conversation_log` は本文の打ち出しを拾っているので、読み返しにも同じ行が残る。
"""
from instantale_modloader import ui

LOG_BASENAME = "free_input_echo.log"

#: ログの上限（送信ごとに1行）。
MAX_LINES = 300

#: ゲームが自分で「主人公名: 文」を出す送り先。ここには足さない。
SELF_ECHO_CLASSES = frozenset((
    "ConversationPhaseManager",
    "ConversationInQuestPhase",
))

def echo_line(text):
    """本文に足す1行。空白だけなら None。"""
    text = text.strip()
    return text or None


def apply(ctx):
    append = ctx.logger(LOG_BASENAME)
    log_state = {"lines": 0}

    def write(text):
        if log_state["lines"] >= MAX_LINES:
            return
        log_state["lines"] += 1
        append(text)

    screen = ui.Screen(ctx, write, tag="free input echo")
    # 入力欄の送信の最中か。送信は Kivy のメインスレッドで1本ずつ来る。
    sending = {"on": False, "dispatched": False}

    @ctx.wrap("__main__:InstantaleApp.on_text_input", required=False)
    def on_text_input(orig, self, *args, **kwargs):
        sending["on"] = True
        sending["dispatched"] = False
        try:
            return orig(self, *args, **kwargs)
        finally:
            if not sending["dispatched"]:
                write("send was not dispatched")
            sending["on"] = False
            sending["dispatched"] = False

    @ctx.wrap("__main__:InstantaleApp.process_choice", required=False)
    def process_choice(orig, self, function, *args, **kwargs):
        if sending["on"] and not sending["dispatched"]:
            sending["dispatched"] = True
            try:
                text = args[0] if args else kwargs.get("choice_text", "")
                cls_name = type(function).__name__
                line = echo_line(text) if isinstance(text, str) else None
                if cls_name in SELF_ECHO_CLASSES:
                    write("{}: the game shows it".format(cls_name))
                elif line is None:
                    write("{}: nothing to show".format(cls_name))
                else:
                    screen.say(self, line)
                    write("{}: echoed".format(cls_name))
            except Exception:
                ctx.log_exc("free input echo: cannot echo the sent text")
        return orig(self, function, *args, **kwargs)

    ctx.log("free input echo: shows sent text outside conversations")
