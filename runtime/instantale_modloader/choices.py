# -*- coding: utf-8 -*-
"""選択肢のボタンの窓口。TECH.md §3.3.14。

MOD が選択肢にボタンを足すとき、守ることが多い（どれか1つ抜けると壊れる。実機で何度も起きた）:

- 組み直し（`refresh_choice_buttons`）は `to_display_buttons` までで、画面には塗らない
- セーブはボタンの `text` と `spec` だけを書き、MOD の印（`mod_…`）は落ちる。ロードすると印の無い残骸が戻る
- ロードの途中の組み直しでは、人物（`world.characters`）がまだ 0 人かほぼ空、名簿もまだ空（`336_` / `301_` の実機）
- MOD のボタンを押しても、ゲームは保存しない（払った額や控えだけが進んだ形が残る）

これらを MOD ごとに書くのをやめ、ここで1か所に引き受ける。MOD が登録するのは処理だけ:

    choices.provide(ctx, screen, refresh=fn, after=fn2, presses={"印の頭": handler, ...}, intercept=fn3, trim=fn4)
        refresh(app, buttons)     組み直しの前。ゲームの一覧（`app.buttons`）を直に書き換えてよい。
                                  一覧が空でも `None` でも呼ぶ（包んでいたときと同じ。見るかどうかは MOD が決める）
        trim(app, buttons)        全部の MOD の `refresh` の後（組み直しの前）。ほかの MOD が足したボタンまで含めて
                                  一覧を絞るとき。`refresh` と同じ順（画面を自分のボタンだけにするなら、宣言型の口の `claim`）
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

    refresh / trim / intercept   後から登録した MOD が先
    after                 先に登録した MOD が先

窓口が引き受けること:

1. 包みは窓口の1枚だけ。`refresh_choice_buttons` / `on_button_press` / `save_game` を包み、登録した MOD へ配る。
   1つの世代（`ctx.generation`）につき1度だけ包む（何本の MOD が登録しても）
2. 印の持ち越し。ゲームの保存の直前に、印の付いたボタンの `(text, spec, 印)` を
   世界×主人公の控え（`state\\choices\\`）に書く。ロードの後の最初の組み直しで、同じ `(text, spec)` の
   ボタンに印を付け直す（`ui` の `MARK_PREFIX` で始まる印なら、登録していない MOD の印も戻る）
3. ロードの後。組み直しが人物の揃わないまま走った（＝ロードの途中。`ready`）ら見張りを仕掛け、
   人物が揃い切り（数が2回続けて同じ）、名簿も少し待ち、手が空いていたら1度だけ組み直して塗る。
   ロードの包み（`load_game_new`）には頼らない（包みが付く前に始まったロードでは、包みの後ろの処理が走らなかった。実機）。
   印の付け直しは「`app.world` が入れ替わった後の最初の組み直し」で行う
4. 押した後の保存。`handler` が True を返したら `ui.saver` でゲーム自身の保存を呼ぶ

登録は持ち主ごとに1つ。注入し直すと同じ持ち主の登録を差し替える。
登録した `ctx` が用済み（`ctx.superseded()`）で、差し替えられていない登録は呼ばない。
置き場は `sys` の属性（`_instantale_choices`）。注入し直しをまたいで残る。

宣言型の口（2026-10-21〜。TECH.md §3.3.14「宣言型の口」）。ボタンを一覧に物として置いて印で覚えるのをやめ、
組み直しのたびに MOD の状態から作り直す:

    choices.provide(ctx, screen, offer=fn, claim=fn2, screens={"名前": fn3}, labels=(...), priority=0)
        offer(app, view)          いまの画面に足す `Choice` の並び。組み直しのたびに呼ぶ
        claim(app, view)          この画面を自分のボタンだけにするなら `Choice` の並び（しないなら None）。
                                  ほかの MOD のボタンもゲームのボタンも出さない。`priority` の大きい登録が勝つ
        screens[名前](app, view, data)
                                  `open_screen` で開いた MOD だけの画面の `Choice` の並び。None を返すと閉じる
        labels                    前の版がセーブに焼いた自前のボタンの文言（無害な spec で印の無いものを外す）

    choices.Choice(text, on_press=fn, place=choices.before("ConversationEndManager"), base=None)
        on_press(app)             押された。True を返すと窓口がゲーム自身の保存を呼ぶ
        base                      ゲームのボタン。その spec のまま文言だけ変えて出す（MOD が無い起動では元のボタンとして働く）
    choices.open_screen(app, owner, name, data) / close_screen(app, owner) / opened(app)

窓口が引き受けること:

5. 組み直しのたびに、前に組んだ宣言型のボタンを全部外してから作る。印には頼らない（押下はボタンに持たせた
   `Choice` をそのまま呼ぶ）。人物が揃うまで（`ready`）は MOD の関数を呼ばない
6. ゲームの保存の間だけ、宣言型のボタンを一覧（`buttons` / `buttons_backup` / `buttons_backup_for_shopping`）
   から外す。セーブにはゲームのボタンだけが残る。`claim` と MOD だけの画面の下のゲームの一覧もそのまま残る
7. MOD だけの画面は「いまどの MOD のどの画面か」を世界×主人公の控えに持ち、ロードの後もその画面を組む
"""
import sys
import threading
import weakref

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
#: 組み直しの後、塗り終わって手が空くのを待って右の欄を見る見張り（0.1 秒おき、最長 30 秒）。
#: 戦闘の1手は審判を待つ間ずっと待機中なので、長めに待つ。次の組み直しが来れば差し替わる。
FOLD_POLL = 0.1
FOLD_TICKS = 300
#: 宣言型の口で窓口が組んだボタンの印のキー。値は窓口だけが読む辞書（`Choice` と下の一覧）で、
#: 印の持ち越し（`record_marks`）は値が辞書の印を控えない。セーブの前には外す（`strip`）。
CHOICE_KEY = "mod_choices"
#: ローダの部品が窓口に預けるときの持ち主の頭。並びは MOD より先（`provide`）。
LOADER_OWNER_PREFIX = "loader:"
#: セーブに焼かれる、選択肢と同じ形の器（`modfacility` / `modnpc` と同じ3つ）。
SAVED_CHOICE_ATTRS = ("buttons", "buttons_backup", "buttons_backup_for_shopping")
#: 組み直しと保存が一覧を取り合わないための鍵の待ち（秒）。待ち切ったら鍵なしで進む（ゲームを止めない）。
LOCK_TIMEOUT = 2.0
OPENED_KEY = "opened"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"providers": {}, "order": [], "installed": None, "world": None,
                 "restore": None, "after": {"token": None}, "worlds": None, "log": []}
        setattr(sys, STORE_ATTR, store)
    store.setdefault("fold", {"token": None})     # 前の版のローダが作った入れ物にも足す
    store.setdefault("lock", threading.RLock())
    store.setdefault("opened", {})
    return store


def _world_mark(world):
    """世界の見分けの印。番地（`id`）は前の世界が解放されると次の世界に使い回されうるので、弱参照で持つ。"""
    if world is None:
        return None
    try:
        return weakref.ref(world)
    except TypeError:
        return id(world)


def _same_world(mark, world):
    if mark is None or world is None:
        return mark is None and world is None
    if isinstance(mark, int):           # 弱参照を作れない世界と、前の版が控えた番地
        return mark == id(world)
    return mark() is world


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


def provide(ctx, screen=None, refresh=None, after=None, presses=None, intercept=None, owner=None, trim=None,
            offer=None, claim=None, screens=None, labels=(), priority=0):
    """`screen`（`ui.Screen`。印のキーを持つ。印を使わないなら None）で作るボタンの扱いを窓口に預ける。

    `offer` / `claim` / `screens` / `labels` / `priority` は宣言型の口（モジュールの説明）。
    """
    store = _store()
    _warn_older_ways(ctx, owner, presses=presses, trim=trim)
    handlers = list((presses or {}).items())
    screens = dict(screens or {})
    key = _owner_of(ctx, owner, [fn for fn in (refresh, after, intercept, trim, offer, claim) if fn is not None]
                    + [handler for _prefix, handler in handlers] + list(screens.values()))
    # その世代で初めての登録なら末尾へ付け直す。前の世代の並びを持ち越すと、注入し直しで足した・
    # 並べ替えた MOD の呼ぶ順が適用順とずれる。全部が登録し直せば、並びはこの世代の apply の順になる。
    generation = getattr(ctx, "generation", None)
    seen = store.setdefault("order_generation", {})
    if key not in store["order"] or (generation is not None and seen.get(key) != generation):
        if key in store["order"]:
            store["order"].remove(key)
        if key.startswith(LOADER_OWNER_PREFIX):
            # ローダの部品（`loader:modfacility`）は MOD より先。1回目の起動は MOD の apply の中から、
            # 2回目からは MOD より前の関所の立て直しから預けられるので、末尾へ付けると起動ごとに並びが変わる
            at = sum(1 for other in store["order"] if other.startswith(LOADER_OWNER_PREFIX))
            store["order"].insert(at, key)
        else:
            store["order"].append(key)
    seen[key] = generation
    store["providers"][key] = {
        "ctx": ctx, "screen": screen, "refresh": refresh, "after": after, "trim": trim,
        "presses": handlers, "intercept": intercept,
        "offer": offer, "claim": claim, "screens": screens,
        "labels": labels if callable(labels)
        else tuple(label for label in (labels or ()) if isinstance(label, str) and label),
        "priority": priority if isinstance(priority, (int, float)) and not isinstance(priority, bool) else 0,
        "save_soon": ui.saver(ctx, getattr(screen, "write", None), getattr(screen, "tag", key)),
    }
    install(ctx)
    return key


#: 古い口（印で振り分ける押下・ほかの MOD のボタンまで絞る `trim`）を使う登録に出す WARN。v2.0.0 で外す（TECH.md §3.9）。
OLDER_WAYS_DEPRECATED = (
    "{mod} gives choices.provide the older way ({ways}); it keeps working until v2.0.0. "
    "Declare the buttons with offer / screens / claim instead (choices.Choice; TECH.md §3.3.14): "
    "the window rebuilds them on every refresh and keeps them out of the save, so no mark has to survive a load.")


def _warn_older_ways(ctx, owner, presses=None, trim=None):
    """古い口で登録した MOD に、宣言型の口への移行を促す（登録のたびに1行。登録は1回の当て直しに1度）。"""
    ways = [name for name, value in (("presses=", presses), ("trim=", trim)) if value]
    log = getattr(ctx, "log", None)
    if not ways or not callable(log):
        return
    message = OLDER_WAYS_DEPRECATED.format(mod=owner or getattr(ctx, "_mod", None) or "a mod",
                                           ways=", ".join(ways))
    try:
        log(message, level="WARN")
    except TypeError:
        log("WARN " + message)        # level を受けない ctx（検査の偽物など）


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


def from_save():
    """いま配っている組み直しが、ロードで戻った選択肢のものか（`refresh` の中で読む）。外では False。

    世界が入れ替わってから、人物が揃った後の最初の組み直し（それも含む）までが真。
    そのあいだの選択肢の文言はセーブに焼かれていたもので、前の起動で MOD が書き換えた文言のことがある。
    人物が揃う前の組み直しでは MOD が文言を書き直せないことがあり、書き直すのは揃った後の組み直し
    （ロードの後の組み直し）になるので、そこまで含める。
    ゲームが描いた文言として読む（素の値を控える）MOD は、真のあいだは控えない。
    """
    return bool(_store().get("from_save"))


def pressed():
    """いま押下を配っているボタン（`handler` / `intercept` の中で読む）。外では None。"""
    return _store().get("pressed")


def ready(app):
    """人物が揃っているか（ロードの途中の組み直しでは 0 人か、ほぼ空）。

    会話中なら、その相手が人物に居ることまで見る。ロードの途中は人物が1件だけ居ることがあり、
    数だけでは揃ったと見誤って見張りを仕掛けない（会話中のセーブで、仲間に `301_` の依頼のボタンが残った。
    VERIFICATION.md §3.102）。
    """
    characters = getattr(getattr(app, "world", None), "characters", None)
    if not isinstance(characters, dict) or not characters:
        return False
    partner = getattr(app, "in_conversation", None)
    return not (isinstance(partner, str) and partner and partner not in characters)


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
    # 宣言型のボタンはセーブに残さず、ロードの後に組み直す。控えるのはセーブに残る一覧
    # （MOD だけの画面と `claim` の下のゲームの一覧）の印と並び
    game, _changed = strip(getattr(app, "buttons", None))
    for entry in list(game or []):
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
            bucket["screen"] = _screen_of(game)
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
    left = {"wait": WAIT_TICKS, "party": PARTY_TICKS, "world": _world_mark(getattr(app, "world", None)),
            "count": None}

    def check(_dt):
        # 用済みの世代でも降りない（降りると、新しい世代はロードの途中の組み直しを見ていないので誰も組み直さない。実機）。
        # 置き場が作り直された（`reset`）後の見張りも降りる（前の置き場の app を組み直さない）
        if after["token"] is not token or _store()["after"] is not after:
            return False
        left["wait"] -= 1
        if left["wait"] <= 0:
            after["token"] = None
            _log(ctx, "after load: the characters never settled; no refresh")
            return False
        # 仕掛けた後に世界がまた入れ替わった（続けて読み込んだ）なら、その世界で待ち直す。
        world = getattr(app, "world", None)
        if not _same_world(left["world"], world):
            left.update(world=_world_mark(world), count=None, party=PARTY_TICKS)
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
            _store()["watcher_refreshing"] = True
            try:
                app.refresh_choice_buttons()
            finally:
                _store()["watcher_refreshing"] = False
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


def arm_fold(ctx, app):
    """組み直しの後、ゲームが選択肢を塗り終えたら、取り残された右の欄を1度だけ畳み直す見張り。

    戦闘に入ると、前の画面の右の欄が閉じられずに残ることがある（`ui.right_panel_left_open`）。
    MOD のボタンでもゲーム自身のボタンでも組み直しはここを通るので、ここで1か所に見る。組み直しの合図は
    ワーカーのスレッドから来て、塗りはその後のフレームなので、塗り終わり（`ui.choices_painted`）と
    手が空く（待機中でない）のを待ってから見る。前の見張りは新しい組み直しで差し替える。
    """
    if getattr(ui.find_hud(app), "right_button_layout", None) is None:
        return False                     # 右の欄が読めない（HUD の無い場面・ゲーム抜きの検査）
    fold = _store()["fold"]
    token = fold["token"] = object()
    left = {"ticks": FOLD_TICKS}

    def check(_dt):
        if fold["token"] is not token or _store()["fold"] is not fold:
            return False
        left["ticks"] -= 1
        if left["ticks"] <= 0:
            fold["token"] = None
            return False
        if not ui.choices_painted(app) or getattr(app, "is_button_enabled", None) is False:
            return True
        fold["token"] = None
        try:
            if ui.fold_right_panel(app):
                _log(ctx, "folded the right panel left open over {} choice(s): {}".format(
                    len(getattr(app, "to_display_buttons", None) or []),
                    "still open" if ui.right_panel_left_open(app) else "closed"))
        except Exception:
            log_exc = getattr(ctx, "log_exc", None)
            if callable(log_exc):
                log_exc("choices: cannot fold the right panel")
        return False

    if not _schedule_interval(check, FOLD_POLL):
        fold["token"] = None
        return False
    return True


# ---------------------------------------------------------------- 宣言型の口
FIRST = ("first", None)
LAST = ("last", None)


def before(cls_name):
    """その spec のゲームのボタンの手前に置く（無ければ末尾）。"""
    return ("before", cls_name)


def after(cls_name):
    """その spec のゲームのボタンの後ろに置く（無ければ末尾）。"""
    return ("after", cls_name)


def at(index):
    """ゲームのボタンの `index` 番目（0 から。宣言型のボタンは数えない）の手前に置く。越えれば末尾。"""
    return ("at", index)


class Choice(object):
    """宣言型の口で出すボタン1つ。窓口が組み直しのたびに一覧へ置き、押されたら `on_press(app)` を呼ぶ。

    `on_press` が None なら押下を握るだけ（`base` があればゲームの押下へ流す）。
    `base` はゲームのボタン。その spec をそのまま使い、文言だけを `text` にする。
    `replace` を真にすると、`base` のゲームのボタンの場所に出し、元のボタンは出さない（ゲームの一覧からは外さない。
    保存の間は元のボタンに戻る）。`place` は使わない。`base` が一覧に無ければ出さない。
    `marks` はほかの MOD が印のキーでこのボタンを見分けるときの印（`{"mod_…": 値}`）。
    押下の振り分けには使わず、印の持ち越しもしない（`404_` がパーティー会話の間 `302_` のボタンを落とす印）。
    """

    __slots__ = ("text", "on_press", "place", "base", "marks", "replace")

    def __init__(self, text, on_press=None, place=LAST, base=None, marks=None, replace=False):
        self.text = str(text)
        self.on_press = on_press
        self.place = place
        self.base = base if isinstance(base, dict) else None
        self.replace = bool(replace) and self.base is not None
        self.marks = {key: value for key, value in (marks or {}).items()
                      if isinstance(key, str) and key.startswith(ui.MARK_PREFIX) and key != CHOICE_KEY}

    def __repr__(self):
        return "Choice({!r})".format(self.text)


class View(object):
    """MOD の関数に渡す、いまの画面。`buttons` はゲームの一覧（宣言型のボタンを外した後。書き換えない）。"""

    def __init__(self, app, buttons):
        self.app = app
        self.buttons = list(buttons or [])
        self.partner, self.end_entry = ui.conversation_partner(self.buttons)
        self.from_save = from_save()
        self.refreshing = refreshing()

    def find(self, cls_name):
        """その spec のゲームのボタン（最初の1つ）。無ければ None。"""
        return ui.find_spec_button(self.buttons, cls_name)


def ours(entry):
    """窓口が宣言型の口で組んだボタンか。"""
    return isinstance(entry, dict) and isinstance(entry.get(CHOICE_KEY), dict)


def strip(entries):
    """宣言型のボタンを外した一覧と、外したかを返す（`entries` は書き換えない）。

    `claim` と MOD だけの画面では、ボタンが下のゲームの一覧を持っているので、それを返す。
    """
    if not isinstance(entries, list):
        return entries, False
    mine = [entry for entry in entries if ours(entry)]
    if not mine:
        return entries, False
    for entry in mine:
        under = entry[CHOICE_KEY].get("under")
        if isinstance(under, list):
            return list(under), True
    game = []
    for entry in entries:
        if not ours(entry):
            game.append(entry)
        elif isinstance(entry[CHOICE_KEY].get("replaced"), dict):
            game.append(entry[CHOICE_KEY]["replaced"])      # `replace` で差し替えた元のゲームのボタン
    return game, True


def game_buttons(app):
    """いまのゲームの一覧（宣言型のボタンを外し、`claim` と MOD だけの画面の下の一覧に戻したもの）。"""
    game, _changed = strip(getattr(app, "buttons", None))
    return list(game) if isinstance(game, list) else []


def hide_for_save(app):
    """保存の直前。宣言型のボタンを外した一覧に差し替え、戻すための `(属性, 元の一覧, 差し替えた一覧)` の並びを返す。"""
    undo = []
    for attr in SAVED_CHOICE_ATTRS:
        value = getattr(app, attr, None)
        game, changed = strip(value)
        if not changed:
            continue
        try:
            setattr(app, attr, game)
        except Exception:
            continue
        undo.append((attr, value, game))
    return undo


def _safe_spec():
    spec_cls = ui.cls_of("PhaseSpec")
    if spec_cls is None:
        return None
    try:
        return spec_cls(ui.SAFE_CLS, [])
    except Exception:
        return None


def _entry_of(owner, choice, under):
    if choice.base is not None:
        entry = {key: value for key, value in choice.base.items() if key in ("text", "spec")}
    else:
        spec = _safe_spec()
        if spec is None:
            return None
        entry = {"spec": spec}
    entry["text"] = choice.text
    entry.update(choice.marks)
    entry[CHOICE_KEY] = {"owner": owner, "choice": choice, "under": under}
    return entry


def _choices_of(result):
    if result is None:
        return None
    if isinstance(result, Choice):
        result = [result]
    try:
        return [choice for choice in result if isinstance(choice, Choice)]
    except TypeError:
        return None


def _game_slot(entry):
    """一覧の中でそのボタンが占めるゲームのボタン。宣言型のボタンは無し（None）、`replace` で差し替えたものは元のボタン。

    呼ぶ側は位置と種類をゲームの一覧で数えるので、差し替えた所もゲームのボタンとして数える
    （数えないと、後から `at` / `before` で置くボタンが1つ後ろか末尾へずれた。10-21 のコードレビュー）。
    """
    if not ours(entry):
        return entry
    replaced = entry[CHOICE_KEY].get("replaced")
    return replaced if isinstance(replaced, dict) else None


def _place(buttons, entry, place, cursors):
    """`place` に従って `entry` を挿す。同じ置き場所に続けて置くと、出した順に並ぶ。"""
    where, cls_name = place if isinstance(place, tuple) and len(place) == 2 else LAST
    if where == "first":
        index = cursors.get("first", 0)
        cursors["first"] = index + 1
        buttons.insert(index, entry)
        return
    if where == "at" and isinstance(cls_name, int) and not isinstance(cls_name, bool):
        seen = 0
        for index, existing in enumerate(buttons):
            if _game_slot(existing) is None:
                continue
            if seen == cls_name:
                buttons.insert(index, entry)
                return
            seen += 1
        buttons.append(entry)
        return
    if where in ("before", "after") and cls_name:
        for index, existing in enumerate(buttons):
            game = _game_slot(existing)
            if game is not None and ui.spec_cls_name(game) == cls_name:
                if where == "before":
                    buttons.insert(index, entry)
                else:
                    slot = index + 1 + cursors.get(("after", cls_name), 0)
                    cursors[("after", cls_name)] = cursors.get(("after", cls_name), 0) + 1
                    buttons.insert(slot, entry)
                return
    buttons.append(entry)


def _labels(app=None):
    store = _store()
    found = []
    for key in providers():
        provider = store["providers"][key]
        labels = provider.get("labels")
        if callable(labels):
            try:
                labels = labels(app)
            except Exception:
                _oops(provider, "{} could not list its labels".format(key))
                labels = ()
        found.extend(label for label in (labels or ()) if isinstance(label, str) and label)
    return found


def prune_remnants(buttons, labels=None, app=None):
    """前の版がセーブに焼いた自前のボタン（無害な spec・印なし・登録した文言）を外す。外した文言を返す。"""
    labels = _labels(app) if labels is None else labels
    if not isinstance(buttons, list) or not labels:
        return []
    removed, kept = [], []
    for entry in buttons:
        text = entry.get("text") if isinstance(entry, dict) else None
        if (isinstance(text, str) and ui.spec_cls_name(entry) == ui.SAFE_CLS
                and not ui.Screen.marked_by_a_mod(entry)
                and any(text == label or text.startswith(label) for label in labels)):
            removed.append(text)
            continue
        kept.append(entry)
    if removed:
        buttons[:] = kept
    return removed


def _call_builder(key, provider, what, fn, *args):
    try:
        return _choices_of(fn(*args))
    except Exception:
        _oops(provider, "{} could not build its {}".format(key, what))
        return None


def _exclusive(app, view):
    """MOD だけの画面か `claim`。`(持ち主, Choice の並び)`、無ければ None。"""
    store = _store()
    row = opened(app)
    if row is not None:
        provider = store["providers"].get(row.get("owner"))
        fn = (provider.get("screens") or {}).get(row.get("name")) if provider is not None else None
        changed = row.get("game") is not None and signature(view.buttons) != row["game"]
        if callable(fn) and _alive(provider["ctx"]) and changed:
            # 下のゲームの一覧が替わった（ゲームが別の画面へ進んだ）。MOD だけの画面はもう前提が無い
            close_screen(app, row["owner"], repaint_now=False)
            _log(provider["ctx"], "closed the screen {!r} of {} (the game moved on: {} -> {})".format(
                row["name"], row["owner"], row["game"], signature(view.buttons)))
        elif callable(fn) and _alive(provider["ctx"]):
            found = _call_builder(row["owner"], provider, "screen {!r}".format(row["name"]), fn,
                                  app, view, row.get("data"))
            if found:
                return row["owner"], found
            close_screen(app, row["owner"], repaint_now=False)
            _log(provider["ctx"], "closed the screen {!r} of {} (nothing to show)".format(row["name"], row["owner"]))
    claims = [(key, provider, fn) for key, provider, fn in _each("claim", reverse=False)]
    # `priority` の大きい登録が勝つ。同じなら先に登録した MOD
    claims.sort(key=lambda item: -item[1].get("priority", 0))
    for key, provider, fn in claims:
        found = _call_builder(key, provider, "claim", fn, app, view)
        if found:
            return key, found
    return None


def compose(app, buttons):
    """宣言型のボタンを組んで `buttons`（ゲームの一覧。外した後）へ置く。人物が揃うまでは何もしない。"""
    if not isinstance(buttons, list) or not ready(app):
        return 0
    view = View(app, buttons)
    found = _exclusive(app, view)
    if found is not None:
        key, picked = found
        under = list(buttons)
        entries = [entry for entry in (_entry_of(key, choice, under) for choice in picked) if entry is not None]
        if entries:
            buttons[:] = entries
            return len(entries)
    cursors, placed = {}, 0
    for key, provider, fn in _each("offer", reverse=False):
        for choice in _call_builder(key, provider, "choices", fn, app, view) or ():
            entry = _entry_of(key, choice, None)
            if entry is None:
                continue
            if choice.replace:
                index = next((i for i, existing in enumerate(buttons) if existing is choice.base), None)
                if index is None:
                    continue                 # 差し替える元のボタンが一覧に無い（ほかの MOD が先に差し替えた）
                entry[CHOICE_KEY]["replaced"] = choice.base
                buttons[index] = entry
            else:
                _place(buttons, entry, choice.place, cursors)
            placed += 1
    return placed


def _press_ours(app, entry):
    """宣言型のボタンの押下。握ったら True（`base` を持ち `on_press` の無いボタンはゲームへ流す）。"""
    store = _store()
    info = entry[CHOICE_KEY]
    choice = info.get("choice")
    provider = store["providers"].get(info.get("owner"))
    if provider is None or not _alive(provider["ctx"]) or not isinstance(choice, Choice):
        # 持ち主が居ない（切った・注入し直しの途中）。無害な spec をゲームに渡すと会話の途中でも
        # 素の画面へ戻るので、握って組み直す
        _log(_any_ctx(), "pressed {!r} of {} that is gone; refreshed".format(entry.get("text"), info.get("owner")))
        repaint(app)
        return True
    if choice.on_press is None:
        return choice.base is None
    changed = None
    try:
        changed = choice.on_press(app)
    except Exception:
        _oops(provider, "{} could not handle {!r}".format(info["owner"], choice.text))
    if changed is True:
        provider["save_soon"](app, "{} {}".format(info["owner"], choice.text))
    return True


# ---------------------------------------------------------------- MOD だけの画面
def _any_ctx():
    """いま生きている登録の ctx（ログと控えの置き場に使う）。無ければ None。"""
    store = _store()
    for key in providers():
        return store["providers"][key]["ctx"]
    return None


def _opened_store(app):
    """`(控えの置き場, 鍵)`。置き場が無い（ゲーム抜きの検査）なら `(None, None)`。"""
    store = _store()
    for key in providers():
        worlds = _worlds(store["providers"][key]["ctx"])
        if worlds is not None:
            return worlds, worlds.playthrough(app)
    return None, None


def opened(app):
    """いま開いている MOD だけの画面 `{"owner", "name", "data"}`。無ければ None。"""
    memory = _store()["opened"]
    worlds, key = _opened_store(app)
    if worlds is None:
        return memory.get(None)
    if key not in memory:
        with worlds.lock:
            row = worlds.load(key).get(OPENED_KEY)
        memory[key] = row if isinstance(row, dict) and row.get("owner") and row.get("name") else None
    return memory.get(key)


def _write_opened(app, row):
    memory = _store()["opened"]
    worlds, key = _opened_store(app)
    memory[key] = row
    if worlds is None:
        return
    with worlds.lock:
        bucket = worlds.load(key)
        if row is None and OPENED_KEY not in bucket:
            return
        if row is None:
            bucket.pop(OPENED_KEY, None)
        else:
            bucket[OPENED_KEY] = row
        worlds.save(key)


def signature(buttons):
    """ゲームの一覧の形（ゲーム自身のボタンの spec のクラス名の並び）。

    MOD の印の付いたボタンと無害な spec のボタンは数えない（印はロードで落ち、MOD のボタンも無害な spec を使う）。
    """
    return [ui.spec_cls_name(entry) or "" for entry in (buttons or [])
            if isinstance(entry, dict) and not ours(entry) and not ui.Screen.marked_by_a_mod(entry)
            and ui.spec_cls_name(entry) != ui.SAFE_CLS]


def open_screen(app, owner, name, data=None, repaint_now=True):
    """MOD だけの画面を開く（`provide(screens=)` の名前）。`data` は JSON にできる値（控えに書く）。

    閉じるまで、組み直しのたびにその画面の関数がボタンを組む。下のゲームの一覧は触らない。
    下のゲームの一覧の形（`signature`）を控え、ゲームがそれを替えたら（別の画面へ進んだら）閉じる。
    """
    _write_opened(app, {"owner": str(owner), "name": str(name), "data": data,
                        "game": signature(game_buttons(app))})
    if repaint_now:
        repaint(app)


def close_screen(app, owner=None, repaint_now=True):
    """MOD だけの画面を閉じる（`owner` を渡すと、その持ち主の画面のときだけ）。閉じたら True。"""
    row = opened(app)
    if row is None or (owner is not None and row.get("owner") != str(owner)):
        return False
    _write_opened(app, None)
    if repaint_now:
        repaint(app)
    return True


def repaint(app):
    """次のフレームで組み直して塗る（押下の流れの中で塗ってもゲームが古い一覧で塗り直すため）。"""
    def run(_dt):
        try:
            app.refresh_choice_buttons(reset_page=True)
            ui.paint_choices(app, list(getattr(app, "to_display_buttons", None) or []))
        except Exception:
            log_exc = getattr(_any_ctx(), "log_exc", None)
            if callable(log_exc):
                log_exc("choices: cannot repaint the choices")
    try:
        from kivy.clock import Clock
    except Exception:
        run(0)
        return
    Clock.schedule_once(run, 0)


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
    for key, provider, fn in _each("trim", reverse=True):
        try:
            fn(app, buttons)
        except Exception:
            _oops(provider, "{} could not trim the choices".format(key))


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
    if ours(entry):
        return _press_ours(app, entry)
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
    # 前の組み直しで置いた宣言型のボタンを外してゲームの一覧に戻す。前の版がセーブに焼いた自前のボタンも、
    # 印の付け直しより前に外す（付け直すと、前の版の印で宣言型のボタンと二重に並ぶ）
    if isinstance(buttons, list):
        game, changed = strip(buttons)
        if changed:
            buttons[:] = game
        removed = prune_remnants(buttons, app=app)
        if removed:
            _log(ctx, "dropped {} button(s) the older versions left in the save: {}".format(len(removed), removed))
    world = getattr(app, "world", None)
    if world is not None and not _same_world(store["world"], world):
        store["world"] = _world_mark(world)
        store["restore"] = True          # 世界が入れ替わった＝ロード（か新しく始めた）
    loaded = bool(store["restore"] and isinstance(buttons, list) and buttons)
    if loaded:
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
    if loaded:
        store["save_pending"] = True
    store["from_save"] = bool(store.get("save_pending"))
    try:
        _call_refresh(app, buttons)
        try:
            compose(app, buttons)
        except Exception:
            log_exc = getattr(ctx, "log_exc", None)
            if callable(log_exc):
                log_exc("choices: cannot compose the declared choices")
    finally:
        store["from_save"] = False
    if store.get("save_pending") and ready(app):
        store["save_pending"] = False
    if isinstance(buttons, list) and buttons and not ready(app):
        arm_after_load(ctx, app)
    elif loaded and isinstance(buttons, list) and buttons and not ui.party_member_ids(app) and not store.get("watcher_refreshing"):
        # 人物は揃ったが名簿がまだ戻っていない、ロードの後の最初の組み直し。名簿を見て組む宣言型のボタン
        # （`404_` の自前の「会話する」）は、ここでは組まれず、次の組み直しまで出なかった（実機）。
        # 前の版の印の付け直しでは、セーブの残骸に印が戻って出ていた。名簿を少し待って1度組み直す
        # （見張り自身の組み直しは名簿を待った後なので、仕掛け直さない）
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
        # 組み直しはワーカーからも来る。一覧を外して組む間に保存が割り込まないよう、保存と同じ鍵を持つ
        locked = store["lock"].acquire(timeout=LOCK_TIMEOUT)
        # 引数は鍵を取ってから置く。先に置くと、組み直しが重なったときに前の組み直しの MOD 関数に後の引数が見え、
        # 前が抜けるときに戻した値で後の組み直しが None を読んだ（10-21 のコードレビュー）
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
            try:
                arm_fold(ctx, self)
            except Exception:
                ctx.log_exc("choices: cannot watch the right panel")
            return result
        finally:
            store["refreshing"] = previous
            if locked:
                store["lock"].release()

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
        locked = store["lock"].acquire(timeout=LOCK_TIMEOUT)
        try:
            # 印の控えも鍵の中で。外で読むと、組み直しの途中の一覧を控えうる
            try:
                record_marks(ctx, self)
            except Exception:
                if not store.get("record_failed"):
                    store["record_failed"] = True
                    ctx.log_exc("choices: cannot record the marks before the save")
            undo = hide_for_save(self)
            try:
                return orig(self, *args, **kwargs)
            finally:
                for attr, value, hidden in reversed(undo):
                    # 戻すのは、差し替えた一覧のままのときだけ。保存の間にゲームが新しい一覧を代入していたら
                    # （ゲームの代入はこの鍵を持たない）、それを潰さない（10-21 のコードレビュー）
                    if getattr(self, attr, None) is not hidden:
                        _log(ctx, "{} was replaced during the save; left as is".format(attr))
                        continue
                    try:
                        setattr(self, attr, value)
                    except Exception:
                        ctx.log_exc("choices: cannot put {} back after the save".format(attr))
        finally:
            if locked:
                store["lock"].release()

    _log(ctx, "installed (generation {})".format(generation))
    return True


def settle(ctx=None):
    """当て直しの終わりにローダが呼ぶ。宣言型の口の登録があり、遊ぶ画面なら、次のフレームで1度組み直して塗る。

    当て直し（起動直後の boot #2 など）の途中は、まだ登録し直していない MOD の登録が用済みの世代のもので呼ばれない。
    その間に来た組み直し（ロードの後の見張りの組み直しを含む）では、その MOD のボタンが組まれず、
    次の組み直しまで出なかった（実機。`404_` の自前の「会話する」）。組み直したら True。
    """
    store = _store()
    declared = any(store["providers"][key].get(field) for key in providers()
                   for field in ("offer", "claim", "screens"))
    app = ui.find_app()
    hud = ui.find_hud(app) if app is not None else None
    if not declared or hud is None or getattr(hud, "parent", None) is None:
        return False                     # 宣言型の登録が無い・題の画面（遊ぶ画面の HUD が付いていない）
    if not isinstance(getattr(app, "buttons", None), list) or getattr(app, "is_button_enabled", None) is False:
        return False                     # 待機中。待機の終わりにゲームが組み直す
    _log(ctx, "settled after the injection; refreshing the choices once")
    repaint(app)
    return True


def reset():
    """検査用。置き場を空にする。"""
    if hasattr(sys, STORE_ATTR):
        delattr(sys, STORE_ATTR)
