# -*- coding: utf-8 -*-
"""宿泊の始まり・休養・終わり（`VacationStartManager` / `VacationRestManager` / `VacationEndManager` の
`execute`）の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

宿泊の前後に何かする MOD（137/315/327/330/331/410）は、どれも同じ `execute` を自分で包み、
`execute` には月数と部屋が来ない（`choice_text` だけ）ので `__init__` も包んで自分の属性に控えていた（315/327）。
包むのはローダの1枚にし、月数と部屋をローダが控えて前後の処理へ渡す（`gates`）。

    stays.on(owner, ctx, (stays.START,), before=fn, after=fn, always=True, write=write)   # apply() の中で
    stays.on(owner, ctx, (stays.REST,), before=fn)                                         # 休養の前
    fn(call)    # call.app / call.obj / call.months / call.quality / call.result / call.memo …（gates.Call）

| 種類 | 包む関数 | `call.months` / `call.quality` |
|---|---|---|
| `START` | `VacationStartManager.execute`（部屋を決めて宿泊を始める。連泊の「まだ宿泊する」も同じ） | `__init__(app, months, quality)` の値 |
| `REST` | `VacationRestManager.execute`（休養） | 同上 |
| `END` | `VacationEndManager.execute`（宿泊を終える。`__init__(app)`） | どちらも None |

| 属性 | |
|---|---|
| `call.obj` | マネージャ |
| `call.app` | マネージャの `app`。無ければ `ui.find_app()` |
| `call.months` | ゲームに渡った月数（int）。読めなければ None。部屋選びが渡すのは常に 1（GAME.md §2.17） |
| `call.quality` | 部屋の種類（`'kennel'` / `'bunk'` / `'private_room'` / `'luxury_suite'` などの文字列）。読めなければ None |
| `call.choice_text` | `execute` の引数（押したボタンの文言）。読めなければ None |

- 月数と部屋は、ローダが `VacationStartManager.__init__` / `VacationRestManager.__init__` を読むだけの包みで
  マネージャに控える（`TERMS_ATTR`）。控えが無いマネージャ（包みが立つ前に組まれたもの）は、マネージャの
  `months` / `quality` 属性を読む。控えの包みは `START` か `REST` に登録があるときだけ立てる
- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）
- 関所は最初に登録した MOD の適用の時に立てる（一番外側には立てない）。
  自分の建物での宿泊で `orig` の例外を握って None を返す MOD（330/331 の `START`）は窓口に載らず自分で包み、
  この関所の外側に載る（今までの重なりと同じ）
- `execute` の本体はワーカースレッドで走る（GAME.md §2.17）。前後の処理もそのスレッドで呼ぶ

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
import sys

from . import frames, gates

START = "start"
REST = "rest"
END = "end"
KINDS = (START, REST, END)

TARGETS = {
    START: "__main__:VacationStartManager.execute",
    REST: "__main__:VacationRestManager.execute",
    END: "__main__:VacationEndManager.execute",
}
LABELS = {
    START: "VacationStartManager.execute",
    REST: "VacationRestManager.execute",
    END: "VacationEndManager.execute",
}
#: 月数と部屋を控える先（`execute` には来ないので、組み立ての引数から読む）。
INIT_TARGETS = {
    START: "__main__:VacationStartManager.__init__",
    REST: "__main__:VacationRestManager.__init__",
}

GATES = {kind: gates.Gate("stays." + kind, TARGETS[kind], "_instantale_stays_" + kind)
         for kind in KINDS}

#: マネージャに控える属性の名前（`{"months", "quality"}`）。マネージャはセーブに入らない。
TERMS_ATTR = "_instantale_stays_terms"
#: 控えの包みの印（種類ごとの世代）。`gates.reset_all` が忘れる。
_INIT_ATTR = "_instantale_stays_init"
gates.track_install_attr(_INIT_ATTR)


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    unknown = [kind for kind in kinds if kind not in GATES]
    if not kinds or unknown:
        raise ValueError("stays: kinds must be among {}, got {!r}".format(KINDS, kinds))
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
    if hasattr(sys, _INIT_ATTR):
        delattr(sys, _INIT_ATTR)


def months_of(value):
    """月数を int にする。真偽値・読めない値は None。

    ゲームは int で渡すが、ボタンの spec を通ると文字列で来うる（`init_args=['1', ...]`。GAME.md §2.17）。
    `315_` の `int(str(...))` と同じく、小数の文字列は読まない。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def quality_of(value):
    """部屋の種類。空でない文字列のときだけ返し、ほかは None。"""
    return value if isinstance(value, str) and value else None


def terms_of(manager):
    """マネージャの `(月数, 部屋)`。ローダの控え → マネージャの属性の順に読む。読めなければ None。"""
    terms = frames.field(manager, TERMS_ATTR, None)
    months = quality = None
    if isinstance(terms, dict):
        months, quality = terms.get("months"), terms.get("quality")
    if months is None:
        months = frames.field(manager, "months", None)
    if quality is None:
        quality = frames.field(manager, "quality", None)
    return months_of(months), quality_of(quality)


def _app_of(manager):
    app = frames.field(manager, "app", None)
    if app is not None:
        return app
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def _install_terms(ctx, kind, write=None):
    """`__init__(app, months, quality)` を読むだけの包みを立てる。1つの世代につき種類ごとに1枚。"""
    target = INIT_TARGETS.get(kind)
    if target is None:
        return []
    generation = getattr(ctx, "generation", None)
    done = getattr(sys, _INIT_ATTR, None)
    if not isinstance(done, dict):
        done = {}
        setattr(sys, _INIT_ATTR, done)
    if kind in done and done[kind] == generation:
        return [target]

    def manager_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            # (app, months, quality)。受け取った形のまま渡し、ここでは読むだけ。
            months = frames.arg(args, kwargs, "months", 1)
            quality = frames.arg(args, kwargs, "quality", 2)
            setattr(self, TERMS_ATTR, {"months": months, "quality": quality})
        except Exception:
            pass
        return result

    ctx.wrap(target, required=False, safe=True)(manager_init)
    done[kind] = generation
    if write:
        write("stays: noting months and quality on {}".format(target))
    return [target]


def install(ctx, kind, write=None):
    """その種類の関所を立てる。1つの世代につき1枚。包んだ対象の名前を返す。"""
    kind = _kinds_of(kind)[0]
    gate = GATES[kind]
    label = LABELS[kind]
    _install_terms(ctx, kind, write)

    def execute(orig, self, *args, **kwargs):
        app = months = quality = choice_text = None
        try:
            app = _app_of(self)
            if kind != END:
                months, quality = terms_of(self)
            choice_text = frames.arg(args, kwargs, "choice_text", 0)
        except Exception:
            pass
        call = gates.Call(kind, self, args, kwargs, app=app, label=label,
                          months=months, quality=quality, choice_text=choice_text)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return gate.install(ctx, execute, write)
