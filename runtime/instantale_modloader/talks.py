# -*- coding: utf-8 -*-
"""会話の節目（1手の後・「会話を終了する」・終了処理）の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

会話の節目に返事の記録・抽出の予約・仲間の台詞・控えの片付けを挟む MOD（300/301/311/402/403/404/902/910）は、
どれも同じ4つの関数を自分で包み、「終わると `in_conversation` が下りるので相手は `orig` の前に読む」を
それぞれに書いていた（300/301）。包むのはローダの1枚にし、相手を `orig` の前に読んで前後の処理へ渡す（`gates`）。

    talks.on(owner, ctx, (talks.TURN, talks.TURN_IN_QUEST), after=fn)      # 1手の後（依頼中も）
    talks.on(owner, ctx, (talks.FINISH,), before=fn, after=fn, always=True) # 終了処理の前後
    fn(call)    # call.app / call.obj / call.partner / call.choice_text / call.result / call.memo …（gates.Call）

| 種類 | 包む関数 | いつ |
|---|---|---|
| `TURN` | `ConversationPhaseManager.conversation_continued` | プレイヤーが1手入力した後の処理（ワーカースレッド）。`orig` の中で相手の返事が出る |
| `TURN_IN_QUEST` | `ConversationInQuestPhase.conversation_continued` | 依頼中の会話の同じ所 |
| `END` | `ConversationEndManager.execute` | 「会話を終了する」を押した直後。`orig` の中で `FINISH` を通る |
| `FINISH` | `ConversationEndManager.finish_conversation` | 会話の終了処理（要約・ライフログ。最大120秒の LLM を含む。GAME.md §2.5）。`orig` の中で `in_conversation` が下りる |

| 属性 | |
|---|---|
| `call.obj` | マネージャ（`ConversationPhaseManager` / `ConversationInQuestPhase` / `ConversationEndManager`） |
| `call.app` | マネージャの `app`。無ければ `ui.find_app()` |
| `call.partner` | 話している相手の id（`app.in_conversation`）。`orig` の前に読む。空でない文字列のときだけ入り、ほかは None |
| `call.choice_text` | 引数の `choice_text`（`TURN` / `TURN_IN_QUEST` / `END`）。`FINISH` と、読めないときは None |

- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る（`orig` の前の状態を `after` で比べる形）
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）
- 関所は最初に登録した MOD の適用の時に立てる（一番外側には立てない）。
  処理の間だけスレッドごとの印を立てる `125_`（相手が内側のメソッドで決まる）は自分で包み、先に適用されるのでこの関所の内側に載る
- `modnpc` の `on["conversation_start"]` / `["conversation_end"]` とは別物。あちらは登録した相手ごとの1回の知らせで、
  包む関数も違う（`ConversationStartManager.__init__` / `ConversationEndManager.resolve_conversation`。
  `resolve_conversation` は `FINISH` の中で呼ばれる）
- `ConversationEndManager` 自身が持つ相手（`__init__` の `in_conversation_id`）は読まない（属性の名前を測っていない）

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import frames, gates

TURN = "turn"
TURN_IN_QUEST = "turn_in_quest"
END = "end"
FINISH = "finish"
KINDS = (TURN, TURN_IN_QUEST, END, FINISH)

TARGETS = {
    TURN: "__main__:ConversationPhaseManager.conversation_continued",
    TURN_IN_QUEST: "__main__:ConversationInQuestPhase.conversation_continued",
    END: "__main__:ConversationEndManager.execute",
    FINISH: "__main__:ConversationEndManager.finish_conversation",
}
LABELS = {
    TURN: "ConversationPhaseManager.conversation_continued",
    TURN_IN_QUEST: "ConversationInQuestPhase.conversation_continued",
    END: "ConversationEndManager.execute",
    FINISH: "ConversationEndManager.finish_conversation",
}

GATES = {kind: gates.Gate("talks." + kind, TARGETS[kind], "_instantale_talks_" + kind)
         for kind in KINDS}


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    unknown = [kind for kind in kinds if kind not in GATES]
    if not kinds or unknown:
        raise ValueError("talks: kinds must be among {}, got {!r}".format(KINDS, kinds))
    return kinds


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None, enter=None, leave=None):
    """前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    `enter` / `leave` は `orig` のすぐ前と後で、ほかの MOD の `before` / `after` の内側（`gates` の説明の表）。
    """
    for kind in _kinds_of(kinds):
        GATES[kind].on(owner, ctx, before=before, after=after, always=always, write=write,
                       enter=enter, leave=leave)
        install(ctx, kind, write)


def off(owner, kinds=KINDS):
    """登録を下げる。"""
    for kind in _kinds_of(kinds):
        GATES[kind].withdraw(owner)


def owners(kind):
    """生きている登録の持ち主（登録の古い順）。"""
    return GATES[_kinds_of(kind)[0]].owners()


def reset():
    """全部忘れる（検査用）。"""
    for gate in GATES.values():
        gate.reset()


def partner_of(app):
    """話している相手の id（`app.in_conversation`）。空でない文字列のときだけ返し、ほかは None。

    `in_conversation` には相手の id が入るが（GAME.md）、真偽値で来る経路もありうるので、
    `str(True)` を id として扱わない（`300_` の `note_response` と同じ扱い）。
    """
    value = frames.field(app, "in_conversation", None) if app is not None else None
    return value if isinstance(value, str) and value else None


def _app_of(manager):
    app = frames.field(manager, "app", None)
    if app is not None:
        return app
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def install(ctx, kind, write=None):
    """その種類の関所を立てる。1つの世代につき1枚。包んだ対象の名前を返す。"""
    kind = _kinds_of(kind)[0]
    gate = GATES[kind]
    label = LABELS[kind]

    def conversation(orig, self, *args, **kwargs):
        app = partner = choice_text = None
        try:
            app = _app_of(self)
            partner = partner_of(app)       # 終わると下りるので orig の前に読む
            if kind != FINISH:
                choice_text = frames.arg(args, kwargs, "choice_text", 0)
        except Exception:
            pass
        call = gates.Call(kind, self, args, kwargs, app=app, label=label,
                          partner=partner, choice_text=choice_text)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return gate.install(ctx, conversation, write)
