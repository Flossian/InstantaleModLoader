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
| `TITLE` | `InstantaleApp.return_to_title` | app | app | |

`call.label` は包んだ関数の名前（`load_game_new` / `start_game` / `World.__init__` / `return_to_title`）。ログ用。

`TITLE` は周回の境目のもう一方（タイトルへ戻る）。周回をまたぐ控えを捨てる・隠したボタンを戻す MOD
（333/401/402/404/910）が各自で包んでいた。`KINDS` は前からの3つのままで、`TITLE` も含めた全部は `ALL_KINDS`。

ロードの後、人物が揃ってから1回（`on_settled`）:

    loads.on_settled(owner, ctx, fn, kinds=(loads.LOAD,), write=write)
    fn(call)    # call.app / call.kind（印を立てた種類）/ call.label == "settled"

ロードの後の突き合わせは、ロードの直後には人物（`world.characters`）がまだ揃っていないことがあるので、
MOD はロードの後に印を立て、選択肢の組み直しで人物が揃っていたら1回だけ突き合わせていた（326/338/911）。
ローダが印と見張りを持つ。`kinds` の `orig` が返ったら印を立て、次の組み直し（`choices` の `refresh`）で
人物が揃っていたら（`choices.ready` かつ人物が2人以上）印を下ろして `fn` を呼ぶ。

- 呼ぶのは組み直しの前。登録ごとに `choices` の `refresh`（持ち主 `<owner>:settled`）として預けるので、
  呼ぶ順は `choices` の決まり（後から預けたものが先）。`choices.provide` の後で `on_settled` を呼べば、
  その MOD の `refresh` より先に来る（自分の `refresh` の頭で突き合わせていた形）。前で呼べば後に来る
- 組み直しはワーカースレッドからも来る。`fn` は呼ばれたスレッドで動く（錠は MOD が取る）
- 印は注入し直しをまたいで残る（ロードの途中で当て直しても、揃った後に1回来る）
- `catch_up=True` なら、登録の時点で世界が読み込まれていれば印を立てる（下の `catch_up` と同じ理由）

読み込みの拾い直し（`on(..., catch_up=True)`）: 起動直後は注入のやり直しと読み込みが重なり、
読み込みの包みが立つ前に世界が読み込まれることがある（317/346 が各自で拾い直していた）。
`catch_up=True` で登録すると、登録の `CATCH_UP_DELAY` 秒後（メインスレッド）に、世界が読み込まれていて、
登録の後に包みを通った読み込みが無ければ、`after` を1回呼ぶ（`call.label == "inject"`。`before` は呼ばない）。
Kivy が無い（ゲームの外）なら何もしない。

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

from . import gates, state

LOAD = "load"
NEW_GAME = "new_game"
WORLD = "world"
TITLE = "title"
#: 前からの3つ（公開 I/F。中身を変えない）。`TITLE` も含めた全部は `ALL_KINDS`。
KINDS = (LOAD, NEW_GAME, WORLD)
ALL_KINDS = KINDS + (TITLE,)
#: 読み込みの後に当たる種類（`on_settled` の印・`catch_up` の拾い直し）。
LOADED_KINDS = (LOAD, NEW_GAME)

TARGETS = {
    LOAD: "__main__:InstantaleApp.load_game_new",
    NEW_GAME: "__main__:InstantaleApp.start_game",
    WORLD: "__main__:World.__init__",
    TITLE: "__main__:InstantaleApp.return_to_title",
}
LABELS = {LOAD: "load_game_new", NEW_GAME: "start_game", WORLD: "World.__init__", TITLE: "return_to_title"}

GATES = {kind: gates.Gate("loads." + kind, TARGETS[kind], "_instantale_loads_" + kind)
         for kind in ALL_KINDS}

#: `catch_up=True` の拾い直しを、登録から何秒後に見るか（317/346 が使っていた値）。
CATCH_UP_DELAY = 1.0
#: `on_settled` の見張りを `choices` に預けるときの持ち主の後ろに付ける印（`<owner>:settled`）。
#: ローダの頭（`loader:`）にしないのは、ローダの預け先は `refresh` が MOD より後に呼ばれるため。
#: MOD の持ち主の並びに置けば、自分の `refresh` の中で突き合わせていたときと同じ位置で呼べる。
SETTLED_SUFFIX = ":settled"
_SETTLED_ATTR = "_instantale_loads_settled"
#: 包みを通って返った読み込みの数（`catch_up` が、登録の後に `after` が来たかを見分ける）。
_SEEN_ATTR = "_instantale_loads_seen"
gates.track_install_attr(_SETTLED_ATTR)
gates.track_install_attr(_SEEN_ATTR)


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    unknown = [kind for kind in kinds if kind not in GATES]
    if not kinds or unknown:
        raise ValueError("loads: kinds must be among {}, got {!r}".format(ALL_KINDS, kinds))
    return kinds


def _loaded_kind(kinds, what):
    """`kinds` のうち読み込みの後に当たる種類（`LOAD` を先に）。無ければ ValueError。"""
    for kind in LOADED_KINDS:
        if kind in kinds:
            return kind
    raise ValueError("loads: {} needs {} or {} in kinds, got {!r}".format(what, LOAD, NEW_GAME, kinds))


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None, catch_up=False):
    """前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    `catch_up=True` は、包みが立つ前に済んだ読み込みの拾い直し（モジュールの説明）。
    `after` と、`kinds` に `LOAD` か `NEW_GAME` が要る。
    """
    kinds = _kinds_of(kinds)
    if catch_up:
        catch_kind = _loaded_kind(kinds, "catch_up")
        if after is None:
            raise ValueError("loads: catch_up needs after=")
    for kind in kinds:
        GATES[kind].on(owner, ctx, before=before, after=after, always=always, write=write)
        install(ctx, kind, write)
    if catch_up:
        _schedule_catch_up(str(owner), catch_kind, write)


def off(owner, kinds=ALL_KINDS):
    """登録を下げる。"""
    for kind in _kinds_of(kinds):
        GATES[kind].withdraw(owner)


def owners(kind):
    """生きている登録の持ち主（登録の古い順）。"""
    return GATES[_kinds_of(kind)[0]].owners()


def reset():
    """全部忘れる（検査用）。`on_settled` の登録と印、読み込みの数も。"""
    for gate in GATES.values():
        gate.reset()
    for attr in (_SETTLED_ATTR, _SEEN_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)


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
            _loaded(kind)
            return result
        return gate.install(ctx, new_game, write)

    def app_method(orig, self, *args, **kwargs):
        call = gates.Call(kind, self, args, kwargs, app=self, label=label)
        result = gate.run(call, lambda: orig(self, *args, **kwargs))
        if kind == LOAD:
            _loaded(kind)
        return result
    return gate.install(ctx, app_method, write)


# ---------------------------------------------------------------- 読み込みの後（on_settled / catch_up）
def _seen():
    """包みを通って返った読み込みの数。"""
    value = getattr(sys, _SEEN_ATTR, 0)
    return value if isinstance(value, int) else 0


def _loaded(kind):
    """読み込み（`LOAD` / `NEW_GAME`）の `orig` と後の処理が返った。数を進め、`on_settled` の印を立てる。

    投げない（ゲームの読み込みの戻りを巻き込まない）。
    """
    try:
        setattr(sys, _SEEN_ATTR, _seen() + 1)
        _arm(kind)
    except Exception:
        from . import log_exc
        log_exc("loads: cannot arm the settled hooks after {}".format(kind))


def _world_loaded(app):
    """世界が読み込まれているか（主人公が居て、エリア表が埋まっている）。"""
    if app is None:
        return False
    try:
        from . import ui
        return getattr(app, "player", None) is not None and bool(ui.world_areas(app))
    except Exception:
        return False


def _schedule_catch_up(owner, kind, write=None):
    """`CATCH_UP_DELAY` 秒後に、包みが拾えなかった読み込みの `after` を1回呼ぶ予約。予約したら True。"""
    try:
        from kivy.clock import Clock
    except Exception:
        return False          # ゲームの外（検査）には Kivy が無い
    gate = GATES[kind]
    entry = dict(gate.entries()).get(owner)
    if entry is None:
        return False
    seq, seen = entry.get("seq"), _seen()

    def catch_up(_dt=None):
        current = dict(gate.entries()).get(owner)
        # 差し替えた・下げた・用済みになった登録の予約は捨てる（新しい登録が自分の予約を持つ）。
        if current is None or current.get("seq") != seq or current.get("after") is None:
            return
        if _seen() != seen:
            return            # 登録の後に包みを通った読み込みがあり、そこで after が来ている
        from . import ui
        app = ui.find_app()
        if not _world_loaded(app):
            return
        if write:
            try:
                write("loads: the world was loaded before the gate; {} catches up".format(owner))
            except Exception:
                pass
        gate._call(owner, current, "after", gates.Call(kind, app, (), {}, app=app, label="inject"))

    try:
        Clock.schedule_once(catch_up, CATCH_UP_DELAY)
    except Exception:
        return False
    return True


def _settled_store():
    return state.process_store(_SETTLED_ATTR, {"hooks": {}, "seq": 0, "armed": {}})


def _arm(kind):
    """`kind` を待つ登録に印を立てる。

    用済みの ctx の登録にも立てる（ロードの途中で当て直すと、ロードが返る時点では前の世代の登録しか無い。
    立てた印は同じ持ち主の新しい登録が引き継ぐ。呼ぶときに生きているかを見る）。
    """
    store = _settled_store()
    for owner, entry in list(store["hooks"].items()):
        if kind in entry.get("kinds", ()):
            store["armed"][owner] = kind


def _settled(app):
    """人物が揃ったか。`choices.ready`（会話の相手が人物に居るか）に、人物が2人以上を重ねる（326/338/911 の判断）。"""
    from . import choices
    if not choices.ready(app):
        return False
    characters = getattr(getattr(app, "world", None), "characters", None)
    return isinstance(characters, dict) and len(characters) > 1


def _settle(owner, app):
    """`owner` の印が立っていて人物が揃っていれば、印を下ろして1回呼ぶ。呼んだら True。"""
    from . import alive, log_exc
    store = _settled_store()
    if owner not in store["armed"] or not _settled(app):
        return False
    entry = store["hooks"].get(owner)
    if entry is None or not alive(entry.get("ctx")):
        return False          # 印は残す（同じ持ち主の新しい登録が引き継ぐ）
    kind = store["armed"].pop(owner, None)
    if kind is None:
        return False          # 別のスレッドの組み直しが先に呼んだ
    try:
        entry["fn"](gates.Call(kind, app, (), {}, app=app, label="settled"))
    except Exception as exc:
        log_exc("loads: the settled hook of {} failed".format(owner))
        write = entry.get("write")
        if callable(write):
            try:
                write("WARN loads.settled: the hook raised {}: {}".format(type(exc).__name__, exc))
            except Exception:
                pass
    return True


def _settle_refresh(owner):
    """`owner` の見張り（`choices` の `refresh` に預ける関数）。"""
    def refresh(app, _buttons):
        _settle(owner, app)
    return refresh


def on_settled(owner, ctx, fn, kinds=(LOAD,), write=None, catch_up=False):
    """ロードの後、人物が揃った最初の選択肢の組み直しで `fn(call)` を1回呼ぶ。`apply()` の中で呼ぶ。

    `kinds` は印を立てる読み込み（`LOAD` / `NEW_GAME` の中から）。同じ持ち主の登録は差し替え、立っている印は引き継ぐ。
    `catch_up=True` なら、登録の時点で世界が読み込まれていれば印を立てる（包みが立つ前に済んだ読み込みの拾い直し）。
    呼ぶ時点・順・スレッドはモジュールの説明。`on` の登録とは別なので、同じ持ち主で両方に登録してよい。
    """
    if not callable(fn):
        raise TypeError("loads: on_settled() needs a callable, got {!r}".format(type(fn)))
    kinds = _kinds_of(kinds)
    _loaded_kind(kinds, "on_settled")
    wrong = [kind for kind in kinds if kind not in LOADED_KINDS]
    if wrong:
        raise ValueError("loads: on_settled() takes kinds among {}, got {!r}".format(LOADED_KINDS, kinds))
    owner = str(owner)
    store = _settled_store()
    store["seq"] += 1
    store["hooks"][owner] = {"ctx": ctx, "fn": fn, "kinds": kinds, "write": write, "seq": store["seq"]}
    for kind in kinds:
        install(ctx, kind, write)
    from . import choices
    choices.provide(ctx, None, refresh=_settle_refresh(owner), owner=owner + SETTLED_SUFFIX)
    if catch_up:
        from . import ui
        try:
            app = ui.find_app()
        except Exception:
            app = None
        if _world_loaded(app):
            store["armed"][owner] = _loaded_kind(kinds, "on_settled")
            if write:
                write("loads: the world is already loaded; {} is armed for the settled hook".format(owner))


def off_settled(owner):
    """`on_settled` の登録と印と、`choices` に預けた見張りを下げる。下げたら True。"""
    store = _settled_store()
    store["armed"].pop(str(owner), None)
    from . import choices
    choices.withdraw(str(owner) + SETTLED_SUFFIX)
    return store["hooks"].pop(str(owner), None) is not None


def settled_owners():
    """`on_settled` の生きている登録の持ち主（登録の古い順）。"""
    from . import alive
    hooks = _settled_store()["hooks"]
    rows = sorted(((owner, entry) for owner, entry in hooks.items() if alive(entry.get("ctx"))),
                  key=lambda row: row[1].get("seq", 0))
    return [owner for owner, _entry in rows]


def armed_owners():
    """`on_settled` の印が立っている持ち主（ログと検査用）。"""
    return sorted(_settled_store()["armed"])


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
