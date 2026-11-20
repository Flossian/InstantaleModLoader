# -*- coding: utf-8 -*-
"""ゲームの保存（`InstantaleApp.save_game`）の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

保存の前後に何かする MOD が6本あり（134/137/327/329/330/407）、どれも同じ関数を自分で包んでいた。
形は2つに分かれる。

| 形 | 前（`before`） | 後（`after`、`always=True`） | MOD |
|---|---|---|---|
| 保存の間だけ素の値へ戻す | MOD が変えている値を元へ戻し、戻したものを返す | 掛け直す（投げた回も） | 134 / 137 / 329 / 330 |
| 保存が通ったら控えを書く | 控えの錠を取る（保存の間ずっと持つ） | `call.error` が無ければ控えを書き、錠を放す | 327 / 407 |

包むのはローダの1枚にし、MOD は前後の処理だけを渡す（`gates`）。

    saving.on(owner, ctx, before=fn, after=fn, always=True, write=write)    # apply() の中で
    fn(call)    # call.app（= self）/ call.result / call.error / call.memo …（gates.Call）

- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る。錠を取ったかどうかもここで渡す
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）
- ラッパーは、全 MOD の適用の後にローダが一番外側に1枚立てる（`gates.defer_outermost`・TECH.md §3.3.21）。MOD の適用の間に呼んだ `on` / `install` は包まない
- セーブを読む共有部品は `saves`（別物）。ローダ自身の保存の関所（`choices` / `modfacility` / `modnpc`）は、それぞれの決まりがあるので自分で包む

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import gates

TARGET = "__main__:InstantaleApp.save_game"
#: MOD の層とローダの保存の関所（`modnpc` / `modfacility` / `choices`）より外側に立てる
#: （`boot()` が全 MOD の適用の後に立てる。`gates.defer_outermost`）。保存の間だけ素の値へ戻す処理は、
#: `modnpc` が MOD の人物を写すより先に走らないと、写しに伏せた値が残る。
GATE = gates.Gate("saving", TARGET, "_instantale_saving", outermost=True)


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
    def save_game(orig, self, *args, **kwargs):
        call = gates.Call("save", self, args, kwargs, app=self, label="save_game")
        return GATE.run(call, lambda: orig(self, *args, **kwargs))
    return GATE.install(ctx, save_game, write)
