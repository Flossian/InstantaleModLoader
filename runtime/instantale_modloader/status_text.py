# -*- coding: utf-8 -*-
"""画面左の能力値の欄（`InstanTaleHUD.update_status_texts` の `value`）を書き換える処理を MOD どうしで分け合う窓口。TECH.md §3.3.21。

能力値の欄は改行で区切った1本の文字列で、ゲームが見張り（`update_status_texts(self, instance, value)`）に渡して塗る
（GAME.md §2.29）。通貨の単位（130）・装備の合算の値（333）・日付の行（345）の3本が、同じ関数を自分で包んで
`value` を差し替え合い、重なりの順を `mod.json` の before/after で決めていた。
包むのはローダの1枚にし、MOD は文字列を受けて返す関数だけを渡す。順は `order` で決める。

    status_text.on(owner, ctx, fn, order=0, after=None, write=write)    # apply() の中で
    fn(text, app) -> None   … そのまま（次の MOD へ）
             -> str         … この文字列に差し替える（次の MOD はこの文字列を見る）
    after(call)             … 塗った後（`orig` が返った後）。call.obj（HUD）/ call.app / call.value / call.text / call.status_label
    status_text.repaint(app)    # 見張りを今の `status_texts` で直に呼び、書き換えを通して塗り直す

- 呼ぶ順は `order` の小さい順。同じ `order` なら登録の新しい順（前に各 MOD が自分で包んでいたときの外側から）。
  `after` はその逆の順（包みの内側だった方から）
- `call.value` はゲームが渡した元の文字列、`call.text` は全部の `fn` を通して見張りへ渡した文字列、
  `call.status_label` は欄のラベル（HUD の `status_label`。読めなければ None）、`call.label` は包んだ関数の名前（ログ用。`gates` の窓口と同じ）
- 見張りが渡された `value` を使わず、ラベルにゲームの元の文字列が残ったときは、窓口が書き換えた文字列をラベルへ直に入れる
  （130 と 345 が各自に持っていた保険。ラベルが元の文字列と同じときだけ入れる）
- `fn` と `after` の例外は握って記録し、その MOD は何もしなかったものとして続ける
- 文字列でない `value`（組み上がる前の呼び出しなど）も `fn` に渡る（`fn` の側で見る）
- 包みは、最初に登録した MOD の適用の時に立つ（一番外側には立てない。HUD の窓口 `hud` と同じ）
- `status_texts` は中身が変わったときしか見張りを呼ばない（GAME.md §2.13）。
  装備を替えた・日数が進んだのように、MOD の書き換えの答えだけが変わったときは `repaint(app)` を呼ぶ

登録した ctx が用済み（`ctx.superseded()`）なら呼ばない。
置き場は `sys` の属性（`_instantale_status_text`）。注入し直しをまたいで残る。
"""
import sys
import threading

from . import alive, frames, gates, log, log_exc, state, window_put

TARGET = "scripts.hud.new_hud:InstanTaleHUD.update_status_texts"
#: 欄のラベル（`InstanTaleHUD.status_label`）。
LABEL = "status_label"

STORE_ATTR = "_instantale_status_text"
_LOCK = threading.Lock()
gates.track_install_attr(STORE_ATTR)


def _store():
    return state.process_store(STORE_ATTR, {"entries": {}, "seq": 0, "installed": None, "said": set()})


def on(owner, ctx, fn=None, order=0, after=None, write=None):
    """書き換える関数と塗った後の処理を登録し、包みを立てる。`apply()` の中で呼ぶ。同じ持ち主は差し替える。"""
    if fn is None and after is None:
        raise ValueError("status_text.on needs fn= or after=")
    for value in (fn, after):
        if value is not None and not callable(value):
            raise TypeError("status_text.on needs callables, got {!r}".format(type(value)))
    if not frames.is_number(order):
        raise TypeError("status_text.on needs a number for order, got {!r}".format(type(order)))
    with _LOCK:
        store = _store()
        store["seq"] += 1
        window_put(store["entries"], owner, {"ctx": ctx, "fn": fn, "after": after, "order": order,
                                             "write": write, "seq": store["seq"]})
    install(ctx, write)


def off(owner):
    """登録を下げる。下げたら True。"""
    with _LOCK:
        return _store()["entries"].pop(str(owner), None) is not None


def _entries():
    """生きている登録を、書き換えを呼ぶ順（`order` の小さい順、同じなら登録の新しい順）で。"""
    with _LOCK:
        rows = list(_store()["entries"].items())
    rows = [(owner, entry) for owner, entry in rows if alive(entry["ctx"])]
    rows.sort(key=lambda row: (row[1]["order"], -row[1]["seq"]))
    return rows


def owners():
    """生きている登録の持ち主（書き換えを呼ぶ順）。ログと検査用。"""
    return [owner for owner, _entry in _entries()]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"entries": {}, "seq": 0, "installed": None, "said": set()})


def _failed(owner, entry, side, exc):
    log_exc("status_text: the {} of {} failed".format(side, owner))
    write = entry.get("write")
    try:
        (write or (lambda line: log("status_text: " + line)))(
            "WARN status_text: the {} raised {}: {}".format(side, type(exc).__name__, exc))
    except Exception:
        pass


def restate(text, app=None, rows=None):
    """登録の順に `fn` を通した文字列。包みの中と検査から呼ぶ。"""
    for owner, entry in (_entries() if rows is None else rows):
        fn = entry.get("fn")
        if fn is None:
            continue
        try:
            result = fn(text, app)
        except Exception as exc:
            _failed(owner, entry, "rewrite", exc)
            continue
        if result is not None:
            text = result
    return text


def _say_once(key, line):
    said = _store()["said"]
    if key in said:
        return
    said.add(key)
    log(line, level="WARN")


def install(ctx, write=None):
    """`update_status_texts` を1枚だけ包む。1つの世代につき1回。包んだら True。"""
    generation = getattr(ctx, "generation", None)
    with _LOCK:
        store = _store()
        done = store.get("installed")
        if isinstance(done, dict) and done.get("generation") == generation:
            return False
        store["installed"] = {"generation": generation}

    def update_status_texts(orig, self, *args, **kwargs):
        # (instance, value)。受け取った形のまま渡し、`value` だけを来た側に合わせて差し替える。
        rows = _entries()
        if not rows:
            return orig(self, *args, **kwargs)
        from . import ui
        try:
            app = ui.find_app()
        except Exception:
            app = None
        original = frames.arg(args, kwargs, "value", 1)
        text = restate(original, app, rows)
        if text is not original:
            args, kwargs, _ = frames.replace_arg(args, kwargs, "value", 1, text)
        result = orig(self, *args, **kwargs)
        label = frames.attr(self, LABEL, None)
        if isinstance(label, str):
            label = None        # frames.attr の番人（読めなかった）
        # 見張りが `value` を使わないビルドへの保険（GAME.md §2.29）。ラベルが元の文字列のままのときだけ直に入れる。
        if isinstance(text, str) and text != original and label is not None:
            try:
                if frames.text_of(label) == original:
                    label.text = text
                    _say_once("unpainted", "status_text: the status label did not take the value; "
                                           "writing the label directly")
            except Exception:
                log_exc("status_text: cannot write the status label")
        call = gates.Call("status", self, args, kwargs, app=app, label="update_status_texts",
                          status_label=label, value=original, text=text)
        call.result = result
        for owner, entry in reversed(rows):
            after = entry.get("after")
            if after is None:
                continue
            try:
                after(call)
            except Exception as exc:
                _failed(owner, entry, "after", exc)
        return result

    ctx.wrap(TARGET, required=False, safe=True)(update_status_texts)
    if write:
        write("status_text: the window was declared on {}".format(TARGET))
    return True


def repaint(app):
    """見張りを今の `status_texts` で直に呼ぶ（書き換えを全部通して塗り直す）。呼んだら True。

    `status_texts` は中身が変わったときしか見張りを呼ばないので、MOD の書き換えの答えだけが変わったとき
    （装備の合算・日付）はこれで塗り直す。HUD・文字列・見張りが読めなければ何もしない。見張りの例外は握って記録する。
    """
    from . import ui
    hud = ui.find_hud(app) if app is not None else None
    if hud is None:
        return False
    text = frames.attr(hud, "status_texts", None)
    update = frames.attr(hud, "update_status_texts", None)
    if not isinstance(text, str) or not callable(update):
        return False
    try:
        update(hud, text)
    except Exception:
        log_exc("status_text: repainting the status texts failed")
        return False
    return True
