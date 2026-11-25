# -*- coding: utf-8 -*-
"""ゲームの店で売買を始める場面の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

売買を押したとき（`ShoppingStartManagerRemake.execute`）の前後に処理を挟む MOD は9本あり
（312/331/336/338/340/341/346/405/411）、どれも同じ関数を自分で包んでいた（336 は中で2回）。
棚に雛形から品を並べる処理（`set_item_from_world_data`）も 312/340/405 が包み、
340 の追加を 405 の検品が見るかどうかが包みの重なり順で前後していた。
包むのはローダの1枚にし、MOD は前後の処理だけを渡す（`gates`）。

    shopping.on(owner, ctx, (shopping.ENTER,), before=fn, after=fn, always=True, write=write)
    shopping.on(owner, ctx, (shopping.LAY,), before=fn, after=fn, write=write)
    fn(call)    # call.app / call.obj / call.facility / call.result / call.memo …（gates.Call）

| 種類 | 包む関数 | 足す属性（どれも `orig` の前に読む。読めなければ None） |
|---|---|---|
| `ENTER` | `ShoppingStartManagerRemake.execute` | `facility`（いま居る施設。`ui.current_facility`） |
| `LAY` | `ShoppingStartManagerRemake.set_item_from_world_data` | `facility`・`owner`（引数の `shop_owner_instance`）・`tier`（引数の `next_tier`） |

`call.obj` はマネージャ、`call.app` はマネージャの `app`（無ければ `ui.find_app()`）。
`call.label` は包んだ関数の名前（`execute` / `set_item_from_world_data`）。ログ用。

- どちらもワーカースレッドで走る（GAME.md §2.13.1）。画面に1行出すなら `screen.schedule` で積む
- `ENTER` の `orig` の中で売買画面が Clock へ積まれ、`execute` が戻る前に開きうる。
  画面を開く前に済ませたいこと（検品・品揃えの生成）は `before` に置く。
  `ENTER` の `after` で主の持ち物の辞書に触ると、メインスレッドの値付けが辞書を回している最中と重なって落ちる
  （触るなら `before`・`LAY`、または売買画面を開く手前の `toggle_twin_inventory_window`）
- `ENTER` の `before` は止めてもよい（判定が届くまで待つなど）。`orig` も後の MOD もその間待つ
- `LAY` は `ENTER` の `orig` の中から来る（初めて開いた店・312 の入れ替えの後）。来ない回もある。
  `before` で足した品は、全部の `after`（検品など）が見る
- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る（種類ごとに別）
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）
- ラッパーは、全 MOD の適用の後にローダが一番外側に1枚立てる（`gates.defer_outermost`・TECH.md §3.3.21）。
  MOD の適用の間に呼んだ `on` / `install` は包まない。`ENTER` を自分で包む `331_`（自分の建物で `orig` が
  投げたら握って None を返す）より外側になるので、その回の前後の処理は `call.error` 無しで `call.result` が None を見る

寄せない形（自分で包むまま）:

- `orig` の例外を握って戻り値を変える（`331_` の `ENTER`）
- 品を1つ作る処理（`generate_item_in_shopping`）を止める・もう1回呼ぶ（`340_` / `405_` / `410_`）。
  書き換えの勝敗が `orig` の前後と重なり順で決まり、前後の処理に分けられない

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import frames, gates

ENTER = "enter"
LAY = "lay"
KINDS = (ENTER, LAY)

TARGETS = {
    ENTER: "__main__:ShoppingStartManagerRemake.execute",
    LAY: "__main__:ShoppingStartManagerRemake.set_item_from_world_data",
}
LABELS = {ENTER: "execute", LAY: "set_item_from_world_data"}

#: `set_item_from_world_data(shop_owner_instance, next_tier)` の引数名の並び（`frames.arg` の添字）。
LAY_ARGS = ("shop_owner_instance", "next_tier")

#: MOD の層より外側に立てる（`boot()` が全 MOD の適用の後に立てる。`gates.defer_outermost`）。
#: `ENTER` は 331 が自分で包むまま残る。最初に登録した MOD（312）の位置に立てると、336〜411 の前後の処理が
#: 331 の内側へ移り、331 が握った回に `call.error` を見るようになる（今は外側で None を見ている）。
#: 外側に立てれば、注入の何回目でも並びが同じになる。`LAY` も同じ立て方にそろえる。
GATES = {kind: gates.Gate("shopping." + kind, TARGETS[kind], "_instantale_shopping_" + kind, outermost=True)
         for kind in KINDS}


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    unknown = [kind for kind in kinds if kind not in GATES]
    if not kinds or unknown:
        raise ValueError("shopping: kinds must be among {}, got {!r}".format(KINDS, kinds))
    return kinds


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None):
    """前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    種類ごとに別の登録になる。種類で処理を変えるなら、種類ごとに呼ぶ。
    """
    for kind in _kinds_of(kinds):
        GATES[kind].on(owner, ctx, before=before, after=after, always=always, write=write)
        install(ctx, write, (kind,))


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


def _app_of(manager):
    app = getattr(manager, "app", None)
    if app is not None:
        return app
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def _facility_of(app):
    if app is None:
        return None
    try:
        from . import ui
        return ui.current_facility(app)
    except Exception:
        return None


def _wrapper(kind):
    gate = GATES[kind]
    label = LABELS[kind]

    def shopping(orig, self, *args, **kwargs):
        # 登録が無ければ何も読まずに通す（`LAY` は1回の来店で段ごとに来る）。
        if not gate.entries():
            return orig(self, *args, **kwargs)
        app = _app_of(self)
        extra = {"facility": _facility_of(app)}
        if kind == LAY:
            extra["owner"] = frames.arg(args, kwargs, "shop_owner_instance", LAY_ARGS)
            extra["tier"] = frames.arg(args, kwargs, "next_tier", LAY_ARGS)
        call = gates.Call(kind, self, args, kwargs, app=app, label=label, **extra)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return shopping


def install(ctx, write=None, kinds=KINDS):
    """関所を立てる。種類ごとに1つの世代につき1枚。包んだ対象の名前を返す。

    `boot()` の `_install_outer_windows` は `install(ctx, log)` で全部の種類を立てる。
    """
    done = []
    for kind in _kinds_of(kinds):
        done.extend(GATES[kind].install(ctx, _wrapper(kind), write))
    return done
