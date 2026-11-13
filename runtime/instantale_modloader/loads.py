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
| `NEW_GAME` | `InstantaleApp.start_game` | app | app | `world`（引数の世界名。読めなければ None） |
| `WORLD` | `World.__init__` | 組み立て中の `World` | 引数の app | `save_data`（引数の `save_data_dict`） |

`call.label` は包んだ関数の名前（`load_game_new` / `start_game` / `World.__init__`）。ログ用。

- 新規開始は `start_game` の中で `load_game_new` も通る（入れ子。`120_` の深さの数え方）。両方に登録すれば2回来る
- `WORLD` の時点では `app.world` がまだ前の周回を指している（組み立ての最中）。周回の鍵は `call.save_data` から引く（`state.playthrough_key_of_dict`）
- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `NEW_GAME` の関所は、登録された前の処理より先に、その世界の前の周回の控えを退避する（`state.retire_world`。TECH.md §5.4）。
  1つのファイルの中に世界ごとの控えを持つ MOD と、控えをメモリに別に持つ MOD は、`NEW_GAME` の `before` で `call.world` の分を捨てる
- `NEW_GAME` の関所はローダが注入のたびに立てる（登録する MOD が無くても退避は走る）
- `start_game` の間は、周回の鍵を作成画面の名前で作る（`state.new_game_player`）。名前はローダが
  `CharacterVM.to_dict` の戻り値から控える（`install_new_player`）。この間のセーブにはまだ主人公の名前が無い

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
import sys

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

    if kind == NEW_GAME:
        def new_game(orig, self, *args, **kwargs):
            world = kwargs.get("world_name", args[0] if args else None)
            world = world if isinstance(world, str) and world else None
            call = gates.Call(kind, self, args, kwargs, app=self, label=label, world=world)
            # 登録された前の処理より先に退避する（MOD が新しい周回の控えを読む前に）。
            retire_previous(ctx, world)
            # 戻るまでの間、周回の鍵は作成画面の名前で作る（`state.new_game_player`）。
            from . import state
            state.begin_new_game(world)
            try:
                result = gate.run(call, lambda: orig(self, *args, **kwargs))
            finally:
                state.end_new_game()
            check_world(self, world)
            return result
        return gate.install(ctx, new_game, write)

    def app_method(orig, self, *args, **kwargs):
        call = gates.Call(kind, self, args, kwargs, app=self, label=label)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return gate.install(ctx, app_method, write)


#: 作成画面の名前を引く対象（`start_game` が `World.__init__` より前に呼ぶ。GAME.md §2.32）。
NEW_PLAYER_TARGET = "scripts.create_world:CharacterVM.to_dict"
_NEW_PLAYER_ATTR = "_instantale_loads_new_player"


def install_new_player(ctx, write=None):
    """`CharacterVM.to_dict` を包み、新しい主人公を始めている最中なら戻り値の名前を控える。1つの世代につき1回。"""
    generation = getattr(ctx, "generation", None)
    done = getattr(sys, _NEW_PLAYER_ATTR, None)
    if isinstance(done, dict) and done.get("generation") == generation:
        return False

    def to_dict(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            from . import state
            name = result.get("name") if isinstance(result, dict) else None
            if state.note_new_player(name) and write:
                write("loads: 新しい主人公の名前 {!r} を控えた（start_game の間の周回の鍵）".format(name))
        except Exception:
            pass
        return result

    ctx.wrap(NEW_PLAYER_TARGET, required=False, safe=True)(to_dict)
    setattr(sys, _NEW_PLAYER_ATTR, {"generation": generation})
    return True


def retire_previous(ctx, world):
    """新しい主人公を始める前に、その世界の前の周回の控えを退避する（`state.retire_world`）。移した数を返す。"""
    from . import log, log_exc, state
    if world is None:
        log("loads: start_game の世界名が読めないので、前の周回の控えを退避しなかった", level="WARN")
        return 0
    try:
        return len(state.retire_world(getattr(ctx, "state_dir", None), world, write=log))
    except Exception:
        log_exc("loads: cannot retire the previous playthrough of {!r}".format(world))
        return 0


def check_world(app, world):
    """退避に使った世界名（`start_game` の引数）と、始まった世界の `world_key` が食い違えば残す。

    控えのファイル名は `world_key` から作る。食い違うと、前の周回の控えは退避されずに残る。
    """
    if world is None:
        return
    from . import log, state
    try:
        key = state.world_key(app)
    except Exception:
        return
    if key not in (world, state.UNKNOWN_WORLD):
        log("loads: start_game の世界名 {!r} と世界の名前 {!r} が違う。前の周回の控えは {!r} の名で退避した".format(
            world, key, world), level="WARN")
