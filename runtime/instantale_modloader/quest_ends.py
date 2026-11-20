# -*- coding: utf-8 -*-
"""依頼が終わる場面（`QuestEndManager.execute`）の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

依頼から帰った後に何かする MOD（301/307/318/321/325/336/338）は、どれも同じ関数を自分で包み、
「どの依頼が終わるのかは `orig` の前に読む（終わった後は `current_quest_data` が片付いている）」を
それぞれに書いていた。包むのはローダの1枚にし、終わる依頼を `orig` の前に読んで前後の処理へ渡す（`gates`）。

    quest_ends.on(owner, ctx, before=fn, after=fn, always=False, write=write)    # apply() の中で
    fn(call)    # call.app / call.obj / call.quest_id / call.quest / call.result / call.memo …（gates.Call）

| 属性 | |
|---|---|
| `call.obj` | `QuestEndManager` |
| `call.app` | `self.app`。無ければ `ui.find_app()` |
| `call.quest_id` | 終わる依頼の id（文字列。`ui.current_quest_id`）。`orig` の前に読む。読めなければ None |
| `call.quest` | 終わる依頼（`app.current_quest_data`）。`orig` の前に読む。読めなければ None |

- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）
- ラッパーは、全 MOD の適用の後にローダが一番外側に1枚立てる（`gates.defer_outermost`・TECH.md §3.3.21）。MOD の適用の間に呼んだ `on` / `install` は包まない

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import gates

TARGET = "__main__:QuestEndManager.execute"
#: MOD の層より外側に立てる（`boot()` が全 MOD の適用の後に立てる。`gates.defer_outermost`）。
GATE = gates.Gate("quest_ends", TARGET, "_instantale_quest_ends", outermost=True)


def on(owner, ctx, before=None, after=None, always=False, write=None):
    """前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。"""
    GATE.on(owner, ctx, before=before, after=after, always=always, write=write)
    install(ctx, write)


def off(owner):
    """登録を下げる。下げたら True。"""
    return GATE.withdraw(owner)


def owners():
    """生きている登録の持ち主（登録の古い順）。"""
    return GATE.owners()


def reset():
    """全部忘れる（検査用）。"""
    GATE.reset()


def install(ctx, write=None):
    """関所を立てる。1つの世代につき1枚。包んだ対象の名前を返す。"""
    def quest_end(orig, self, *args, **kwargs):
        from . import ui
        app = getattr(self, "app", None) or ui.find_app()
        quest_id = quest = None
        try:
            quest = getattr(app, "current_quest_data", None) if app is not None else None
            quest_id = ui.current_quest_id(app) if app is not None else None
        except Exception:
            pass
        call = gates.Call("quest_end", self, args, kwargs, app=app, label="QuestEndManager.execute",
                          quest_id=quest_id, quest=quest)
        return GATE.run(call, lambda: orig(self, *args, **kwargs))
    return GATE.install(ctx, quest_end, write)
