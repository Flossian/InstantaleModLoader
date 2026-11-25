# -*- coding: utf-8 -*-
"""戦闘の節目（組み立て・始まり・1手・終わり）の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

戦闘の節目に処理を挟む MOD は十本あまりあり、どれも同じ関数を自分で包んでいた
（終わり方3つを 106/107/147/410 が同じ並びで、始まりを 106/137/308/319/334/336/410 が、
1手を 147/308/319/333 が）。ゲームの更新で終わり方の名前が変わると直す所が MOD の数だけあるので、
包むのはローダの1枚にし、MOD は前後の処理だけを渡す（`gates`）。
`combat` は戦闘の数（ダメージ・命中）の窓口で、こちらは節目の前後なので別の部品にした。

    battles.on(owner, ctx, (battles.END,), after=fn)                   # 終わり方3つの後（call.kind で終わり方）
    battles.on(owner, ctx, (battles.END_NORMAL,), before=fn, after=fn, always=True)   # BattleEndManager だけ
    battles.on(owner, ctx, (battles.START,), before=fn, after=fn)      # 敵が揃う前後
    battles.on(owner, ctx, (battles.BUILD,), after=fn)                 # 組み立ての後（call.enemy_type）
    battles.on(owner, ctx, (battles.ACTION,), before=fn, after=fn)     # 1手の前後
    fn(call)    # call.app / call.obj（マネージャ）/ call.kind / call.result / call.memo …（gates.Call）

| 種類 | 包む関数 | 足す属性 |
|---|---|---|
| `BUILD` | `BattleStartManager.__init__` | `enemy_type` / `enemy_content`（引数。名前でも位置でも読む） |
| `START` | `BattleStartManager.start_battle` | |
| `ACTION` | `BattlePhaseManager.handle_battle_situation` | `character_key` / `character_side` / `battle_action` |
| `EFFECT` | `BattlePhaseManager.resolve_battle_effect` | 上の3つと `effect_to_enemies` |
| `END_NORMAL` | `BattleEndManager.end_phase` | `end_type`（`'won'` / `'escaped'`） |
| `END_FREE` | `BattleEndInFreeAction.end_phase` | `end_type`（ゲームが持たないので普通は None） |
| `END_COLOSSEUM` | `BattleEndInColosseum.end_phase` | `end_type` |

`END` は終わり方3つ（`ENDS`）をまとめて指す名前で、登録すると3つの関所に同じ前後の処理が載る。
来た回の終わり方は `call.kind`（`END_NORMAL` / `END_FREE` / `END_COLOSSEUM`）で見分ける。
`call.label` は包んだ関数の名前（`BattleEndManager.end_phase` など）。ログ用。

- `call.app` はマネージャの `app`（無ければ `ui.find_app()`）。`BUILD` は組み立ての前で `self.app` がまだ無いので、引数の `app`（無ければ `ui.find_app()`）
- `call.end_type` はマネージャの `end_type`。組み立てのときに渡る値なので `orig` の前に読み、`orig` が戻ったら（投げても）読み直す
- `BUILD` の `before` の時点ではマネージャはまだ組み上がっていない。引数は `call.enemy_type` / `call.enemy_content` から読む
- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る（闘技場かどうかを `orig` の前に読んで後へ渡す形など）
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）
- 1本の MOD が同じ節目に別の機能で2つ載せるときは、持ち主の名前を分ける（`"<フォルダ名>:hunter"` など。登録は持ち主ごとに1つ）

関所は最初に登録した MOD の適用の位置に立てる（一番外側には立てない）。
1手の引数の `battle_action` をその場で書き換える MOD（`342_`）は自分で包んだまま関所の外側に居て、
書き換えた後の値を関所の前の処理に見せる。外側に立てると、書き換えより前に前の処理が走る。
ローダ自身の戦闘の包み（`guards` の強さ・`battle_effects` の効果・`abilities` の期限）は用途が別なので、それぞれが自分で包む。

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import frames, gates

BUILD = "build"
START = "start"
ACTION = "action"
EFFECT = "effect"
END_NORMAL = "end_normal"
END_FREE = "end_free"
END_COLOSSEUM = "end_colosseum"
#: 終わり方3つ。`on` / `off` の種類に `END` と書くと、この3つを指す。
ENDS = (END_NORMAL, END_FREE, END_COLOSSEUM)
END = "end"
KINDS = (BUILD, START, ACTION, EFFECT) + ENDS

TARGETS = {
    BUILD: "__main__:BattleStartManager.__init__",
    START: "__main__:BattleStartManager.start_battle",
    ACTION: "__main__:BattlePhaseManager.handle_battle_situation",
    EFFECT: "__main__:BattlePhaseManager.resolve_battle_effect",
    END_NORMAL: "__main__:BattleEndManager.end_phase",
    END_FREE: "__main__:BattleEndInFreeAction.end_phase",
    END_COLOSSEUM: "__main__:BattleEndInColosseum.end_phase",
}
LABELS = {kind: target.split(":", 1)[1] for kind, target in TARGETS.items()}

#: 引数の並び（`frames.arg` の位置）。`self` を除く。GAME.md §2.10 の「1手ぶんの内訳」。
BUILD_ARGS = ("app", "enemy_type", "enemy_content")
ACTION_ARGS = ("character_key", "character_side", "battle_action")
EFFECT_ARGS = ACTION_ARGS + ("effect_to_enemies",)

GATES = {kind: gates.Gate("battles." + kind, TARGETS[kind], "_instantale_battles_" + kind)
         for kind in KINDS}


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    out = []
    for kind in tuple(kinds or ()):
        for one in (ENDS if kind == END else (kind,)):
            if one not in out:
                out.append(one)
    unknown = [kind for kind in out if kind not in GATES]
    if not out or unknown:
        raise ValueError("battles: kinds must be among {} or {!r}, got {!r}".format(KINDS, END, kinds))
    return tuple(out)


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None, enter=None, leave=None):
    """前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は種類ごとに差し替える。

    `kinds` は種類の並び（`END` は終わり方3つ）。`enter` / `leave` は `orig` のすぐ前と後
    （ほかの MOD の `before` / `after` の内側。`gates` の説明の表）。
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
    """生きている登録の持ち主（登録の古い順）。`END` なら終わり方3つの和（最初に出た順）。"""
    seen = []
    for one in _kinds_of(kind):
        for owner in GATES[one].owners():
            if owner not in seen:
                seen.append(owner)
    return seen


def reset():
    """全部忘れる（検査用）。"""
    for gate in GATES.values():
        gate.reset()


def _app_of(manager):
    """マネージャの `app`。無ければ走っている app（MOD が自分で包んでいたときと同じ引き方）。"""
    app = frames.field(manager, "app", None)
    if app is not None:
        return app
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def _end_type_of(manager, default=None):
    return frames.field(manager, "end_type", default)


def _call_of(kind, self, args, kwargs):
    """1回の呼び出しを組む。引数の読み違いで `orig` を止めないよう、読めない値は None にする。"""
    extra = {"label": LABELS[kind]}
    try:
        if kind == BUILD:
            app = frames.arg(args, kwargs, "app", BUILD_ARGS)
            extra["enemy_type"] = frames.arg(args, kwargs, "enemy_type", BUILD_ARGS)
            extra["enemy_content"] = frames.arg(args, kwargs, "enemy_content", BUILD_ARGS)
            if app is None:
                app = _app_of(None)
        else:
            app = _app_of(self)
        if kind in (ACTION, EFFECT):
            for name in (EFFECT_ARGS if kind == EFFECT else ACTION_ARGS):
                extra[name] = frames.arg(args, kwargs, name, EFFECT_ARGS)
        if kind in ENDS:
            extra["end_type"] = _end_type_of(self)
    except Exception:
        app = None
    return gates.Call(kind, self, args, kwargs, app=app, **extra)


def install(ctx, kind, write=None):
    """その種類の関所を立てる。1つの世代につき1枚。包んだ対象の名前を返す。"""
    kind = _kinds_of(kind)
    if len(kind) != 1:
        raise ValueError("battles: install takes one kind, got {!r}".format(kind))
    kind = kind[0]
    gate = GATES[kind]

    if kind in ENDS:
        def end_phase(orig, self, *args, **kwargs):
            call = _call_of(kind, self, args, kwargs)

            def invoke():
                try:
                    return orig(self, *args, **kwargs)
                finally:
                    call.end_type = _end_type_of(self, getattr(call, "end_type", None))
            return gate.run(call, invoke)
        return gate.install(ctx, end_phase, write)

    def battle_step(orig, self, *args, **kwargs):
        call = _call_of(kind, self, args, kwargs)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return gate.install(ctx, battle_step, write)
