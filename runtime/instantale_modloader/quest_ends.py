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

依頼の放棄（`QuestRetireManager.execute`）も同じ形で預かる。放棄の後に道の取り消し・帰りの日数・
救出の決着を進める MOD（307/325/336/344/926）が、終わりと同じ「依頼の id を `orig` の前に読む」を各自で包んでいた。

    quest_ends.on_retire(owner, ctx, before=fn, after=fn, always=False, write=write)    # apply() の中で
    fn(call)    # call.obj は QuestRetireManager。call.app / call.quest_id / call.quest は終わりと同じ読み方

終わりと放棄は別の関所（登録も別）。両方を見る MOD は `on` と `on_retire` の両方に登録する。
放棄の関所も外側に立てる（`install_retire`。`boot()` の `_install_outer_windows` が呼ぶ）。

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import gates

TARGET = "__main__:QuestEndManager.execute"
#: MOD の層より外側に立てる（`boot()` が全 MOD の適用の後に立てる。`gates.defer_outermost`）。
GATE = gates.Gate("quest_ends", TARGET, "_instantale_quest_ends", outermost=True)

RETIRE_TARGET = "__main__:QuestRetireManager.execute"
#: 放棄の関所。終わりと同じく MOD の層より外側に立てる。
RETIRE_GATE = gates.Gate("quest_ends.retire", RETIRE_TARGET, "_instantale_quest_retire", outermost=True)


def _quest_call(kind, label, manager, args, kwargs):
    """終わる（放棄する）依頼を `orig` の前に読んだ `gates.Call`。ゲームは `orig` の中で今の依頼を片付ける。"""
    from . import ui
    app = getattr(manager, "app", None) or ui.find_app()
    quest_id = quest = None
    try:
        quest = getattr(app, "current_quest_data", None) if app is not None else None
        quest_id = ui.current_quest_id(app) if app is not None else None
    except Exception:
        pass
    return gates.Call(kind, manager, args, kwargs, app=app, label=label, quest_id=quest_id, quest=quest)


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
    """全部忘れる（検査用）。終わりと放棄の両方。"""
    GATE.reset()
    RETIRE_GATE.reset()


def install(ctx, write=None):
    """終わりの関所を立てる。1つの世代につき1枚。包んだ対象の名前を返す。"""
    def quest_end(orig, self, *args, **kwargs):
        call = _quest_call("quest_end", "QuestEndManager.execute", self, args, kwargs)
        return GATE.run(call, lambda: orig(self, *args, **kwargs))
    return GATE.install(ctx, quest_end, write)


def on_retire(owner, ctx, before=None, after=None, always=False, write=None):
    """依頼の放棄の前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    `call` の属性は `on` と同じ（`call.obj` が `QuestRetireManager`、`call.kind` が "quest_retire"）。
    放棄する依頼の id と中身は `orig` の前に読んである。読めなかった回は `call.quest_id` が None。
    """
    RETIRE_GATE.on(owner, ctx, before=before, after=after, always=always, write=write)
    install_retire(ctx, write)


def off_retire(owner):
    """放棄の登録を下げる。下げたら True。"""
    return RETIRE_GATE.withdraw(owner)


def retire_owners():
    """放棄に登録している生きている持ち主（登録の古い順）。"""
    return RETIRE_GATE.owners()


def install_retire(ctx, write=None):
    """放棄の関所を立てる。1つの世代につき1枚。包んだ対象の名前を返す。

    MOD の適用の間は包まない（`gates.defer_outermost`）。`boot()` が全 MOD の後に外側へ立てる。
    """
    def quest_retire(orig, self, *args, **kwargs):
        call = _quest_call("quest_retire", "QuestRetireManager.execute", self, args, kwargs)
        return RETIRE_GATE.run(call, lambda: orig(self, *args, **kwargs))
    return RETIRE_GATE.install(ctx, quest_retire, write)
