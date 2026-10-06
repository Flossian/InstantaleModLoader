# -*- coding: utf-8 -*-
"""選択肢のボタンの窓口。TECH.md §3.3.14。

MOD が選択肢にボタンを足すとき、守ることが多い（どれか1つ抜けると壊れる。実機で何度も起きた）:

- 組み直し（`refresh_choice_buttons`）は `to_display_buttons` までで、画面には塗らない
- セーブはボタンの `text` と `spec` だけを書き、MOD の印（`mod_…`）は落ちる。ロードすると印の無い残骸が戻る
- ロードの途中の組み直しでは、人物（`world.characters`）がまだ 0 人、名簿もまだ空（`336_` の実機）
- MOD のボタンを押しても、ゲームは保存しない（払った額や控えだけが進んだ形が残る）

これらを MOD ごとに書くのをやめ、ここで1か所に引き受ける。MOD が登録するのは処理だけ:

    choices.provide(ctx, screen, refresh=fn, after=fn2, presses={"印の頭": handler, ...}, intercept=fn3)
        refresh(app, buttons)     組み直しの前。ゲームの一覧（`app.buttons`）を直に書き換えてよい。
                                  一覧が空でも `None` でも呼ぶ（包んでいたときと同じ。見るかどうかは MOD が決める）
        after(app)                組み直しの後（`to_display_buttons` が組み上がった後）。
                                  ゲームの組み直しに渡された引数は `choices.refreshing()`
        handler(app, action)      `screen.mark_of(entry)` が「印の頭」で始まるボタンが押された。
                                  ゲームの押下へは流さない。True を返すと、窓口がゲーム自身の保存を呼ぶ。
                                  押されたボタンそのものは `choices.pressed()`
        intercept(app, entry, button_index)
                                  どのボタンが押されても、印の振り分けの前に呼ぶ（ゲーム自身のボタンを
                                  spec で見て断る・手前で何かする MOD のため）。True を返すと押下を握る
                                  （ゲームへ流さない）

呼ぶ順は、MOD が自分で包みを重ねていたときと同じにする（乗せ替えても並びが変わらないように）。
包みは後から読み込んだ MOD ほど外側に重なるので、

    refresh / intercept   後から登録した MOD が先
    after                 先に登録した MOD が先

窓口が引き受けること:

1. 包みは窓口の1枚だけ。`refresh_choice_buttons` / `on_button_press` / `save_game` を包み、登録した MOD へ配る。
   1つの世代（`ctx.generation`）につき1度だけ包む（何本の MOD が登録しても）
2. 印の持ち越し。ゲームの保存の直前に、印の付いたボタンの `(text, spec, 印)` を
   世界×主人公の控え（`state\\choices\\`）に書く。ロードの後の最初の組み直しで、同じ `(text, spec)` の
   ボタンに印を付け直す（`ui` の `MARK_PREFIX` で始まる印なら、登録していない MOD の印も戻る）
3. ロードの後。組み直しが人物 0 人のまま走った（＝ロードの途中）ら見張りを仕掛け、
   人物が揃い切り（数が2回続けて同じ）、名簿も少し待ち、手が空いていたら1度だけ組み直して塗る。
   ロードの包み（`load_game_new`）には頼らない（包みが付く前に始まったロードでは、包みの後ろの処理が走らなかった。実機）。
   印の付け直しは「`app.world` が入れ替わった後の最初の組み直し」で行う
4. 押した後の保存。`handler` が True を返したら `ui.saver` でゲーム自身の保存を呼ぶ

登録は持ち主ごとに1つ。注入し直すと同じ持ち主の登録を差し替える。
登録した `ctx` が用済み（`ctx.superseded()`）で、差し替えられていない登録は呼ばない。
置き場は `sys` の属性（`_instantale_choices`）。注入し直しをまたいで残る。
"""
import sys

from . import ui

STORE_ATTR = "_instantale_choices"
REFRESH_TARGET = "__main__:InstantaleApp.refresh_choice_buttons"
PRESS_TARGET = "__main__:InstantaleApp.on_button_press"
SAVE_TARGET = "__main__:InstantaleApp.save_game"
STATE_DIRNAME = "choices"
#: ロードの後、人物が揃うのを待つ見張り（0.25 秒おき、最長 60 秒）。
POLL = 0.25
WAIT_TICKS = 240
#: 人物が揃った後、名簿（同行者）が戻るのを待つ回数（同行者の居ないセーブでは待ち切ってから組み直す）。
PARTY_TICKS = 12


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"providers": {}, "order": [], "installed": None, "world": None,
                 "restore": None, "after": {"token": None}, "worlds": None, "log": []}
        setattr(sys, STORE_ATTR, store)
    return store


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def _log(ctx, line):
    store = _store()
    store["log"] = (store["log"] + [line])[-50:]
    log = getattr(ctx, "log", None)
    if callable(log):
        try:
            log("choices: " + line)
        except Exception:
            pass


def _owner_of(ctx, owner, fns):
    """持ち主の名前。MOD 名（`ctx._mod`）、無ければ処理を書いたモジュールの名前（検査の偽の ctx 向け）。"""
    if owner:
        return str(owner)
    mod = getattr(ctx, "_mod", None)
    if mod:
        return str(mod)
    for fn in fns:
        module = getattr(fn, "__module__", None)
        if module:
            return module
    return str(id(ctx))


def provide(ctx, screen=None, refresh=None, after=None, presses=None, intercept=None, owner=None):
    """`screen`（`ui.Screen`。印のキーを持つ。印を使わないなら None）で作るボタンの扱いを窓口に預ける。"""
    store = _store()
    handlers = list((presses or {}).items())
    key = _owner_of(ctx, owner, [fn for fn in (refresh, after, intercept) if fn is not None]
                    + [handler for _prefix, handler in handlers])
    if key not in store["order"]:
        store["order"].append(key)
    store["providers"][key] = {
        "ctx": ctx, "screen": screen, "refresh": refresh, "after": after,
        "presses": handlers, "intercept": intercept,
        "save_soon": ui.saver(ctx, getattr(screen, "write", None), getattr(screen, "tag", key)),
    }
    install(ctx)
    return key


def withdraw(owner):
    store = _store()
    store["providers"].pop(str(owner), None)


def providers():
    """いま生きている登録の持ち主（登録した順）。"""
    store = _store()
    return [key for key in store["order"]
            if key in store["providers"] and _alive(store["providers"][key]["ctx"])]


def refreshing():
    """いま配っている組み直しの呼び出しの `(args, kwargs)`（`refresh` / `after` の中で読む）。外では None。"""
    return _store().get("refreshing")


def pressed():
    """いま押下を配っているボタン（`handler` / `intercept` の中で読む）。外では None。"""
    return _store().get("pressed")


def ready(app):
    """人物が揃っているか（ロードの途中の組み直しでは 0 人）。"""
    characters = getattr(getattr(app, "world", None), "characters", None)
    return isinstance(characters, dict) and bool(characters)


# ---------------------------------------------------------------- 印の持ち越し
def _entry_key(entry):
    return (str(entry.get("text") or ""), ui.spec_cls_name(entry) or "", repr(ui.spec_args(entry)))


def _marks_of(entry):
    if not isinstance(entry, dict):
        return {}
    return {k: v for k, v in entry.items()
            if isinstance(k, str) and k.startswith(ui.MARK_PREFIX)
            and isinstance(v, (str, int, float, bool))}


def _screen_of(buttons):
    return [str(entry.get("text") or "") if isinstance(entry, dict) else "" for entry in (buttons or [])]


def _worlds(ctx):
    """控えの置き場。state の置き場と JSON の読み書きを持たない ctx（ゲーム抜きの検査の偽物）なら None（印の持ち越しは見送る）。"""
    store = _store()
    if store["worlds"] is None:
        if not getattr(ctx, "state_dir", None) or not all(
                callable(getattr(ctx, name, None)) for name in ("read_json", "write_json")):
            return None
        from . import state as loader_state
        store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME)
    return store["worlds"]


def record_marks(ctx, app):
    """保存の直前に呼ぶ。印の付いたボタンを控える（無ければ控えを消す）。"""
    rows = []
    for entry in list(getattr(app, "buttons", None) or []):
        marks = _marks_of(entry)
        if marks:
            text, cls_name, args = _entry_key(entry)
            rows.append({"text": text, "cls": cls_name, "args": args, "marks": marks})
    worlds = _worlds(ctx)
    if worlds is None:
        return rows
    playthrough = worlds.playthrough(app)
    with worlds.lock:
        bucket = worlds.load(playthrough)
        if rows:
            bucket["marks"] = rows
            bucket["screen"] = _screen_of(getattr(app, "buttons", None))
        elif "marks" not in bucket:
            return rows
        else:
            bucket.pop("marks", None)
            bucket.pop("screen", None)
        worlds.save(playthrough)
    return rows


def restore_marks(ctx, app, buttons):
    """ロードの後の最初の組み直しで呼ぶ。印の落ちたボタンに、保存の直前の印を付け直す。戻した数を返す。"""
    worlds = _worlds(ctx)
    if worlds is None:
        return 0
    with worlds.lock:
        bucket = worlds.load(worlds.playthrough(app))
        rows = list(bucket.get("marks") or [])
        screen = bucket.get("screen")
    if not rows or not isinstance(buttons, list):
        return 0
    # 保存したときと同じ画面（ボタンの文言の並びが丸ごと同じ）のときだけ付け直す。
    # 同じ世界で同じ名前の主人公を作り直すと控えの鍵が同じになり、前の周回の印が別の画面に付く。
    if screen is not None and list(screen) != _screen_of(buttons):
        return 0
    restored = 0
    for entry in buttons:
        if not isinstance(entry, dict) or _marks_of(entry):
            continue
        key = _entry_key(entry)
        for index, row in enumerate(rows):
            if (row.get("text"), row.get("cls"), row.get("args")) == key:
                entry.update(row.get("marks") or {})
                rows.pop(index)
                restored += 1
                break
    return restored


# ---------------------------------------------------------------- ロードの後
def _schedule_interval(fn, poll):
    try:
        from kivy.clock import Clock
    except Exception:
        return False
    Clock.schedule_interval(fn, poll)
    return True


def arm_after_load(ctx, app):
    """人物が揃って手が空いたら1度だけ組み直して塗る見張りを仕掛ける（仕掛け済みなら何もしない）。"""
    after = _store()["after"]
    if after["token"] is not None:
        return False
    token = after["token"] = object()
    left = {"wait": WAIT_TICKS, "party": PARTY_TICKS, "world": id(getattr(app, "world", None)), "count": None}

    def check(_dt):
        # 用済みの世代でも降りない（降りると、新しい世代はロードの途中の組み直しを見ていないので誰も組み直さない。実機）。
        if after["token"] is not token:
            return False
        left["wait"] -= 1
        if left["wait"] <= 0:
            after["token"] = None
            _log(ctx, "after load: the characters never settled; no refresh")
            return False
        # 仕掛けた後に世界がまた入れ替わった（続けて読み込んだ）なら、その世界で待ち直す。
        world = id(getattr(app, "world", None))
        if world != left["world"]:
            left.update(world=world, count=None, party=PARTY_TICKS)
            return True
        if not ready(app):
            return True
        # 人物の数が2回続けて同じになるまで待つ（読み込みの途中で組み直さない）。
        count = len(getattr(app.world, "characters", None) or {})
        if count != left["count"]:
            left["count"] = count
            return True
        if not ui.party_member_ids(app) and left["party"] > 0:
            left["party"] -= 1
            return True
        after["token"] = None
        if getattr(app, "is_button_enabled", None) is False:
            _log(ctx, "after load: waiting; the game refreshes the choices when it ends")
            return False
        try:
            before = list(getattr(app, "to_display_buttons", []) or [])
            app.refresh_choice_buttons()
            after_texts = list(getattr(app, "to_display_buttons", []) or [])
            done = ui.paint_choices(app, after_texts, getattr(ctx, "log_exc", None))
            _log(ctx, "after load: refreshed {} -> {} via {}".format(
                before, after_texts, "+".join(done) if done else "(nothing)"))
        except Exception:
            log_exc = getattr(ctx, "log_exc", None)
            if callable(log_exc):
                log_exc("choices: the refresh after the load failed")
        return False

    if not _schedule_interval(check, POLL):
        after["token"] = None
        return False
    return True


# ---------------------------------------------------------------- 配る
def _each(field, reverse):
    store = _store()
    keys = providers()
    for key in (reversed(keys) if reverse else keys):
        provider = store["providers"][key]
        fn = provider.get(field)
        if callable(fn):
            yield key, provider, fn


def _oops(provider, what):
    log_exc = getattr(provider["ctx"], "log_exc", None)
    if callable(log_exc):
        log_exc("choices: {}".format(what))


def _call_refresh(app, buttons):
    for key, provider, fn in _each("refresh", reverse=True):
        try:
            fn(app, buttons)
        except Exception:
            _oops(provider, "{} could not add its choices".format(key))


def _call_after(app):
    for key, provider, fn in _each("after", reverse=False):
        try:
            fn(app)
        except Exception:
            _oops(provider, "{} failed after the refresh".format(key))


def _call_press(app, entry, button_index):
    """押下を握ったら True（ゲームへ流さない）。"""
    store = _store()
    store["pressed"] = entry
    try:
        return _dispatch_press(app, entry, button_index)
    finally:
        store["pressed"] = None


def _dispatch_press(app, entry, button_index):
    for key, provider, fn in _each("intercept", reverse=True):
        try:
            if fn(app, entry, button_index) is True:
                return True
        except Exception:
            _oops(provider, "{} could not look at the press".format(key))
    store = _store()
    for key in providers():
        provider = store["providers"][key]
        mark_of = getattr(provider["screen"], "mark_of", None)
        if not provider["presses"] or not callable(mark_of):
            continue                     # 押下の処理を登録していない MOD の印は読まない
        action = mark_of(entry)
        if not isinstance(action, str):
            continue
        for prefix, handler in provider["presses"]:
            if not action.startswith(prefix):
                continue
            changed = None
            try:
                changed = handler(app, action)
            except Exception:
                _oops(provider, "{} could not handle {!r}".format(key, action))
            if changed is True:
                provider["save_soon"](app, "{} {}".format(key, action))
            return True
    return False


def on_refresh(ctx, app):
    """組み直しの包みの前半（`orig` の前）。検査からも直に呼ぶ。"""
    store = _store()
    buttons = getattr(app, "buttons", None)
    world = getattr(app, "world", None)
    if world is not None and id(world) != store["world"]:
        store["world"] = id(world)
        store["restore"] = True          # 世界が入れ替わった＝ロード（か新しく始めた）
    if store["restore"] and isinstance(buttons, list) and buttons:
        store["restore"] = None
        # 印の持ち越しは付け足しの働き。ここが失敗しても、MOD の処理は必ず呼ぶ（失敗は1度だけ残す）。
        try:
            restored = restore_marks(ctx, app, buttons)
        except Exception:
            restored = 0
            if not store.get("restore_failed"):
                store["restore_failed"] = True
                log_exc = getattr(ctx, "log_exc", None)
                if callable(log_exc):
                    log_exc("choices: cannot restore the marks after the load")
        if restored:
            _log(ctx, "restored the marks of {} button(s) after the load".format(restored))
    _call_refresh(app, buttons)
    if isinstance(buttons, list) and buttons and not ready(app):
        arm_after_load(ctx, app)


def install(ctx):
    """包みを立てる（1つの世代につき1度）。`provide` が呼ぶ。

    世代を持たない ctx（ゲーム抜きの検査の偽物）は、ctx ごとに1度。
    """
    store = _store()
    generation = getattr(ctx, "generation", None)
    mark = ("generation", generation) if generation is not None else ("ctx", id(ctx))
    if store["installed"] == mark:
        return False
    store["installed"] = mark
    if store["worlds"] is not None:
        store["worlds"] = store["worlds"].rebind(ctx)

    @ctx.wrap(REFRESH_TARGET, required=False)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        previous = store.get("refreshing")
        store["refreshing"] = (args, kwargs)
        try:
            try:
                on_refresh(ctx, self)
            except Exception:
                ctx.log_exc("choices: the refresh failed")
            result = orig(self, *args, **kwargs)
            try:
                _call_after(self)
            except Exception:
                ctx.log_exc("choices: the after-refresh failed")
            return result
        finally:
            store["refreshing"] = previous

    @ctx.wrap(PRESS_TARGET, required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        try:
            entry = ui.pressed_entry(self, button_index)
        except Exception:
            entry = None                 # 押されたボタンが引けない画面。印は読めないので素通し
        if entry is not None or providers():
            try:
                if _call_press(self, entry, button_index):
                    return None
            except Exception:
                ctx.log_exc("choices: the press failed")
        return orig(self, button_index, *args, **kwargs)

    @ctx.wrap(SAVE_TARGET, required=False, safe=True)
    def save_game(orig, self, *args, **kwargs):
        try:
            record_marks(ctx, self)
        except Exception:
            if not store.get("record_failed"):
                store["record_failed"] = True
                ctx.log_exc("choices: cannot record the marks before the save")
        return orig(self, *args, **kwargs)

    _log(ctx, "installed (generation {})".format(generation))
    return True


def reset():
    """検査用。置き場を空にする。"""
    if hasattr(sys, STORE_ATTR):
        delattr(sys, STORE_ATTR)
