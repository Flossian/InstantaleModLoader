# -*- coding: utf-8 -*-
"""セーブを読む・新しく始める・世界を組み立てる場面の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

ロードの後に前の周回の控えを捨てる・組み直す MOD は十数本あり、
どれも `load_game_new` / `start_game` / `World.__init__` を自分で包んでいた
（107/110/120 は同じ形の `on_load` を、323/326/338/911 は同じ `LOAD_TARGETS` の組を各自で持っていた）。
包むのはローダの1枚にし、MOD は前後の処理だけを渡す（`gates`）。

    loads.on(owner, ctx, (loads.LOAD, loads.NEW_GAME), after=fn)     # ロードと新規開始の後
    loads.on(owner, ctx, (loads.WORLD,), before=fn, after=fn)        # 世界を組み立てる前後
    fn(call)    # call.app / call.obj / call.result / call.memo …（gates.Call）

| 種類 | 包む関数 | `call.obj` | `call.app` | 足す属性 |
|---|---|---|---|---|
| `LOAD` | `InstantaleApp.load_game_new` | app | app | |
| `NEW_GAME` | `InstantaleApp.start_game` | app | app | |
| `WORLD` | `World.__init__` | 組み立て中の `World` | 引数の app | `save_data`（引数の `save_data_dict`） |

`call.label` は包んだ関数の名前（`load_game_new` / `start_game` / `World.__init__`）。ログ用。

- 新規開始は `start_game` の中で `load_game_new` も通る（入れ子。`120_` の深さの数え方）。両方に登録すれば2回来る
- `WORLD` の時点では `app.world` がまだ前の周回を指している（組み立ての最中）。周回の鍵は `call.save_data` から引く（`state.playthrough_key_of_dict`）
- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import gates

LOAD = "load"
NEW_GAME = "new_game"
WORLD = "world"
KINDS = (LOAD, NEW_GAME, WORLD)

TARGETS = {
    LOAD: "__main__:InstantaleApp.load_game_new",
    NEW_GAME: "__main__:InstantaleApp.start_game",
    WORLD: "__main__:World.__init__",
}
LABELS = {LOAD: "load_game_new", NEW_GAME: "start_game", WORLD: "World.__init__"}

GATES = {kind: gates.Gate("loads." + kind, TARGETS[kind], "_instantale_loads_" + kind)
         for kind in KINDS}


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    unknown = [kind for kind in kinds if kind not in GATES]
    if not kinds or unknown:
        raise ValueError("loads: kinds must be among {}, got {!r}".format(KINDS, kinds))
    return kinds


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None):
    """前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。"""
    for kind in _kinds_of(kinds):
        GATES[kind].on(owner, ctx, before=before, after=after, always=always, write=write)
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


def install(ctx, kind, write=None):
    """その種類の関所を立てる。1つの世代につき1枚。包んだ対象の名前を返す。"""
    gate = GATES[_kinds_of(kind)[0]]
    label = LABELS[kind]
    if kind == WORLD:
        def world_init(orig, self, *args, **kwargs):
            save_data = kwargs.get("save_data_dict", args[0] if args else None)
            app = kwargs.get("app", args[1] if len(args) > 1 else None)
            call = gates.Call(kind, self, args, kwargs, app=app, label=label, save_data=save_data)
            return gate.run(call, lambda: orig(self, *args, **kwargs))
        return gate.install(ctx, world_init, write)

    def app_method(orig, self, *args, **kwargs):
        call = gates.Call(kind, self, args, kwargs, app=self, label=label)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return gate.install(ctx, app_method, write)
