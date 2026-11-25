# -*- coding: utf-8 -*-
"""本文の1行（`InstantaleApp.add_text`）を書き換える・出さないを MOD どうしで分け合う窓口。TECH.md §3.3.31。

ゲームが本文へ1行足すたびに、その場面の間だけ文言を直す MOD が9本あり
（133/134/304/307/314/315/325/332/334/336）、どれも同じ関数を自分で包んでいた。
包みが重なるので、どの書き換えが先に見るかは適用順で決まり、`context` の読み方（位置かキーワード）も
MOD ごとに写されていた。包むのはローダの1枚にし、MOD は1行を受けて返す関数だけを渡す。

    text_filters.on(owner, ctx, fn, write=write)    # apply() の中で
    fn(text, app) -> None   … そのまま（次の MOD へ）
             -> str         … この文に差し替える（次の MOD はこの文を見る）
             -> text_filters.DROP … この行を出さない（残りの MOD と `orig` を呼ばない）

- 呼ぶ順は登録の新しい順（＝前に各 MOD が自分で包んでいたときの重なりで、外側だった順）
- `app` は `add_text` の `self`（走っている app）。`text` は `add_text` の `context`。位置でもキーワードでも読み、差し替えも来た側に合わせる（`frames.replace_arg`）。
  文字列でないこともある（`fn` の側で見る）
- `fn` が投げた例外は握って記録し、その MOD は何もしなかったものとして続ける
- 登録した ctx が用済み（`ctx.superseded()`）なら呼ばない
- ラッパーは、全 MOD の適用の後にローダが一番外側に1枚立てる（`gates.defer_outermost`・TECH.md §3.3.21）。MOD の適用の間に呼んだ `on` / `install` は包まない
- 1行の後ろに自分の行を足す処理（`306_` の仲間の話）はここではなく、自分で `add_text` を包む

置き場は `sys` の属性（`_instantale_text_filters`）。注入し直しをまたいで残る。
"""
import sys
import threading

from . import alive, frames, gates, log, log_exc, state, window_put

TARGET = "__main__:InstantaleApp.add_text"
#: `fn` がこれを返したら、その行を出さない。
DROP = object()

STORE_ATTR = "_instantale_text_filters"
_LOCK = threading.Lock()
gates.track_install_attr(STORE_ATTR)


def _store():
    return state.process_store(STORE_ATTR, {"filters": {}, "seq": 0, "installed": None})


def on(owner, ctx, fn, write=None):
    """1行を受けて返す関数を登録し、包みを立てる。`apply()` の中で呼ぶ。同じ持ち主は差し替える。"""
    if not callable(fn):
        raise TypeError("text_filters.on needs a callable, got {!r}".format(type(fn)))
    with _LOCK:
        store = _store()
        store["seq"] += 1
        window_put(store["filters"], owner, {"ctx": ctx, "fn": fn, "write": write, "seq": store["seq"]})
    install(ctx, write)


def off(owner):
    """登録を下げる。下げたら True。"""
    with _LOCK:
        return _store()["filters"].pop(str(owner), None) is not None


def _entries():
    with _LOCK:
        rows = sorted(_store()["filters"].items(), key=lambda row: row[1]["seq"])
    return [(owner, entry) for owner, entry in rows if alive(entry["ctx"])]


def owners():
    """生きている登録の持ち主（登録の古い順）。ログと検査用。"""
    return [owner for owner, _entry in _entries()]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"filters": {}, "seq": 0, "installed": None})


def filter_text(text, app=None):
    """登録の新しい順に通した文か `DROP`。包みの中と検査から呼ぶ。"""
    for owner, entry in reversed(_entries()):
        try:
            result = entry["fn"](text, app)
        except Exception as exc:
            log_exc("text_filters: the filter of {} failed".format(owner))
            write = entry.get("write")
            try:
                (write or (lambda line: log("text_filters: " + line)))(
                    "WARN text_filters: the filter raised {}: {}".format(type(exc).__name__, exc))
            except Exception:
                pass
            continue
        if result is DROP:
            return DROP
        if result is not None:
            text = result
    return text


def install(ctx, write=None):
    """`add_text` を1枚だけ包む。1つの世代につき1回。包んだら True。"""
    generation = getattr(ctx, "generation", None)
    # MOD を適用している間は包まない。`boot()` が全 MOD の後にローダの ctx で外側へ立てる（`gates.defer_outermost`）。
    if gates.deferring():
        return False
    with _LOCK:
        store = _store()
        done = store.get("installed")
        if isinstance(done, dict) and done.get("generation") == generation:
            return False
        store["installed"] = {"generation": generation}

    def add_text(orig, self, *args, **kwargs):
        if not _entries():
            return orig(self, *args, **kwargs)
        original = frames.arg(args, kwargs, "context", 0)
        text = filter_text(original, self)
        if text is DROP:
            return None
        if text is not original:
            args, kwargs, _ = frames.replace_arg(args, kwargs, "context", 0, text)
        return orig(self, *args, **kwargs)

    ctx.wrap(TARGET, required=False, safe=True)(add_text)
    if write:
        write("text_filters: the window was declared on {}".format(TARGET))
    return True
