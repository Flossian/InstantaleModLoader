# -*- coding: utf-8 -*-
r"""計測: 待機表示（「…」）。ゲームは変えない。

ゲーム標準の「…」と、ローダの `ui.Screen.busy_on` が出す「…」を同じ物差しで録り、
見た目の違いの元を探す。
ローダの側は `301_` がゲームの「クエストを探す」を**画面で見て真似た**もので
（VERIFICATION_LOG.md §2.23）、ゲームがどの仕組みで出しているかは測っていない。
ゲームは Nuitka ビルドで中のコードは読めない（`232_` の記録）ので、外から録る。

録るもの

    見張り   `POLL_SECONDS` ごと（メインスレッド）に画面の状態を読み、**変わったときだけ**1行。
             左右の選択肢の枠（`hud.buttons` / `hud.right_buttons`）の文字・disabled・opacity、
             `is_button_enabled`、`is_adding_text`、`to_display_buttons`、送信ボタン、
             背景の絵（`location_image` のフォルダ名。版2）
    呼び出し 選択肢を塗る手（`display_button_load` / `update_button_texts`）、
             組み直し（`refresh_choice_buttons` / `set_buttons_to_normal`）、
             場面の区切り（`process_choice`）、送信ボタンの有効・無効、点だけの `add_text`、
             背景を替える手（`change_background_image_*` と HUD の `update_image_source`。版2）。
             スレッド名と呼び出し元つき
    点送り   待機中の点のコマ（版3）。1コマずつは書かず、ひと続きの点送りを1行にまとめる
             （コマ数・手ごとの回数・始まりと終わりの時刻）

MOD が出している「…」の区間は、その MOD のログの `busy on` / `busy off` の行
（`ui.Screen` が書く。例 `out\real_estate.log` の `real estate: busy on`）と時刻で突き合わせる。
ここで時刻をミリ秒まで書くのはそのため。

版3: 点のコマごとの記録をやめた。
問いは版1〜2で決着した（GAME.md §2.4）が、ゲームの点送り（約0.3秒ごと）の1コマにつき
`display_button_load` / `update_button_texts` / 見張りの3行を2ファイルへ書き、
うち2行は呼び出し元を組んでいた（メインスレッドで、`frames.caller` は `__main__` の
全クラスを総当たりする）。4日で約 60MB、その 96% が点のコマだった。
点のコマは数だけ数えて、点以外の記録が来たとき・点が 1 秒途切れたときに1行にまとめる。
見張りは点の文字を `…` に揃えてから比べるので、点が進んでも行は出ない。
見張りの間隔も 0.05 秒から 0.2 秒へ広げた（点のコマを捕まえる必要が無くなったため）。
包みは受け取った引数をそのまま `orig` へ渡す形にした（キーワードを位置に直さない）。

版4: 点送りの始め手を録る。
待機中に点送りが2本・4本と並んで回り、点が倍の速さで進む区間が戦闘・会話の始まりなどで出ていた
（版3の `dots` の行で、`display_button_load` が1秒に約7回・約14回。1本なら約3.4回）。
`Clock.schedule_once` に `display_button_load` が渡されたとき、点送りの続き（Clock から呼ばれた
`display_button_load` が自分を予約し直す）でなければ、その時点で載っていた本数と呼び出しの段
（ローダと MOD のフレームも飛ばさない）を `dots_start` として1行書く。

版5（見直し）:
  * 見張りの `screen` の行は、前の写しから変わった項目だけを書く（`full=False`。書かない項目は前のまま）。
    注入ごとの最初の1行だけ全項目（`full=True`）。版4では1項目でも変わると全項目（平均約 460B）を
    2ファイルへ書いていて、`screen` の行の半分近くは `adding`（`is_adding_text`）だけが変わった行だった
  * 「polling the screen」「polling stopped」「watching who starts the dots」は1プロセス1回
    （`sys` の `SAID_MARK`）。注入のたびに出ていた
  * `Clock.schedule_once` の包みをローダの `ctx.wrap`（`kivy.clock:Clock.schedule_once`）へ移した。
    版4はインスタンスの属性を直に差し替えていて、ローダの包みの一覧と drift から見えなかった。
    `Clock` は `kivy.context` の代理（`ProxyContext`）で、属性の読み書きは中の実体へ素通しになるので、
    ローダの `resolve` / `set_attr` で同じ所（実体の属性）へ載る。
    包みの世代はローダが入れ替えるので、版4の「1プロセスに1枚で中身だけ差し替える」入れ物は要らなくなった。
    版4の入れ物が残っているプロセスでは、その内側に載る（古い入れ物は新しい世代では何も書かない）

    out\busy_display.log     読む用
    out\busy_display.jsonl   1件＝1行。後から数える用
"""
import datetime
import os
import sys
import threading
import time

from instantale_modloader import frames

LOG_BASENAME = "busy_display.log"
RECORD_BASENAME = "busy_display.jsonl"

#: 見張りの周期。版2までは点のコマ（約 0.3 秒。GAME.md §2.4）を捕まえるため 0.05 秒だった。
#: 版3で点のコマは見張りで比べなくなったので広げた。
POLL_SECONDS = 0.2

#: 点送りがこの秒数途切れたら、ひと続きの点送りを閉じて1行書く。
DOTS_IDLE_SECONDS = 1.0

#: 1つの呼び出しの記録に写す選択肢の数の上限。
TEXT_LIMIT = 8

#: 点だけの文字とみなす字（`add_text_dots` と同じ）。
DOT_CHARS = ".。…・ 　"

#: 点送りの始め手の記録に並べる段数（版4）。
START_DEPTH = 10

#: 包む `Clock.schedule_once`（版5でローダの `ctx.wrap` へ移した）。
#: 版4の入れ物の印 `_iml_234_schedule_holder` は使わない。
SCHEDULE_TARGET = "kivy.clock:Clock.schedule_once"

#: 1プロセス1回だけ書く行の控え（版5）。中身は書いた行の鍵の集合。
SAID_MARK = "_iml_234_said_once"

#: この probe のファイル。点送りの始め手を探すとき、先頭のこのファイルの段を飛ばす（版5）。
THIS_FILE = os.path.normcase(os.path.abspath(__file__))


def is_dots(text):
    """点だけの文字か（空は点ではない）。"""
    return isinstance(text, str) and bool(text.strip()) and not text.strip(DOT_CHARS)


def is_wrapper_frame(filename):
    """この probe 自身か、ローダの包みの層（`patch.py`）の段か（版5）。"""
    try:
        path = os.path.normcase(os.path.abspath(filename))
    except Exception:
        return False
    return path == THIS_FILE or (frames.is_ours(filename)
                                 and os.path.basename(path) == "patch.py")


def call_stack(depth=START_DEPTH):
    """`Clock.schedule_once` を呼んだ手から上の段を (ファイル名, 関数名, 行) で並べる。

    先頭に続くこの probe と包みの層（`patch.py`）の段だけを飛ばし、そこから先はどのフレームも飛ばさない
    （ローダや MOD が点送りを始めた手も、その段のまま見える）。
    版5: 包みをローダの `ctx.wrap` に移したので、段の数を決め打ちせずにファイル名で飛ばす。
    """
    chain, index, leading = [], 1, True
    while len(chain) < depth:
        try:
            frame = sys._getframe(index)
        except ValueError:
            break
        index += 1
        code = frame.f_code
        if leading and is_wrapper_frame(code.co_filename):
            continue
        leading = False
        chain.append((code.co_filename, code.co_name, frame.f_lineno))
    return chain


def say_once(key):
    """`key` の行をこのプロセスでまだ書いていなければ True（版5）。注入し直しでも数え直さない。"""
    said = getattr(sys, SAID_MARK, None)
    if not isinstance(said, set):
        said = set()
        setattr(sys, SAID_MARK, said)
    if key in said:
        return False
    said.add(key)
    return True


def is_continuation(chain):
    """点送りの続きの予約か。

    続きは、Clock（`kivy\\clock.py` の `post_idle`）から呼ばれた `display_button_load` が
    自分を予約し直す形。間に挟まる包み（ローダと MOD のフレーム）は飛ばして見る。
    """
    if not chain or chain[0][1] != "display_button_load":
        return False
    for filename, name, _line in chain[1:]:
        if name == "display_button_load" or frames.is_ours(filename):
            continue
        return os.path.basename(filename) == "clock.py"
    return False


def format_stack(chain):
    return " <- ".join("{} ({}:{})".format(name, os.path.basename(filename), line)
                       for filename, name, line in chain) or "?"


def pending_loads(clock):
    """Clock に載っている `display_button_load` の予約の数。読めなければ None。"""
    try:
        events = clock.get_events()
    except Exception:
        return None
    count = 0
    for event in events or ():
        try:
            callback = event.get_callback()
        except Exception:
            callback = getattr(event, "callback", None)
        if getattr(callback, "__name__", "") == "display_button_load":
            count += 1
    return count


def all_dots(values):
    """選択肢の文字が、空か点だけで、点が1つ以上あるか。"""
    if not isinstance(values, (list, tuple)):
        return False
    seen = False
    for value in values:
        if is_dots(value):
            seen = True
        elif value not in ("", None):
            return False
    return seen


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    started = time.monotonic()
    last = {"snapshot": None, "at": None}
    #: ひと続きの点送り（版3）。None なら点送りの外。
    dots = {"run": None}
    lock = threading.Lock()

    def now():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def elapsed():
        return round(time.monotonic() - started, 3)

    def thread():
        return threading.current_thread().name

    def texts_of(value):
        if isinstance(value, (list, tuple)):
            return [str(v) for v in list(value)[:TEXT_LIMIT]]
        return frames.repr_value(value)

    def dotless(texts):
        if not isinstance(texts, list):
            return texts
        return ["…" if is_dots(text) else text for text in texts]

    def find_hud(app):
        try:
            from instantale_modloader import ui
            return ui.find_hud(app)
        except Exception:
            return None

    def widgets(hud, name):
        found = getattr(hud, name, None) if hud is not None else None
        if not isinstance(found, (list, tuple)):
            return None
        out = []
        for widget in found:
            text = getattr(widget, "text", None)
            # 版3: 点は1字に揃える（点が進んだだけでは変化にしない）。
            out.append(["…" if is_dots(text) else text,
                        bool(getattr(widget, "disabled", False)),
                        round(float(getattr(widget, "opacity", 1.0) or 0.0), 2)])
        return out

    def picture(value):
        """背景の絵のフルパスを、フォルダ名（＝場所の名前）にして返す。"""
        if not isinstance(value, str) or not value:
            return value
        return os.path.basename(os.path.dirname(value))

    def snapshot(app):
        hud = find_hud(app)
        send = getattr(hud, "text_send_button", None) if hud is not None else None
        return {
            "enabled": getattr(app, "is_button_enabled", None),
            "adding": getattr(app, "is_adding_text", None),
            "left": widgets(hud, "buttons"),
            "right": widgets(hud, "right_buttons"),
            # こちらも点は1字に揃える。版3の最初の形は枠だけ揃えていて、ここの '.' → '..' → '...' を
            # 変化と見て点送りを1コマごとに閉じていた（実機で全部 ticks=1 だった）
            "display": dotless(texts_of(getattr(app, "to_display_buttons", None))),
            "choices": len(getattr(app, "buttons", None) or []),
            "send_disabled": getattr(send, "disabled", None) if send is not None else None,
            "picture": picture(getattr(app, "location_image", None)),
        }

    def emit(kind, fields):
        row = {"at": now(), "t": elapsed(), "kind": kind, "thread": thread()}
        row.update(fields)
        record(row)
        write("{:>9.3f} [{}] {} {}".format(
            row["t"], row["thread"], kind,
            " ".join("{}={}".format(k, v) for k, v in fields.items())))

    def flush_dots(why):
        """ひと続きの点送りを1行にまとめて閉じる。"""
        with lock:
            run, dots["run"] = dots["run"], None
        if run is None:
            return
        emit("dots", {"ticks": run["ticks"], "calls": run["calls"],
                      "from": run["from"], "to": run["to"],
                      "seconds": round(run["to"] - run["from"], 3),
                      "closed_by": why})

    def dot_tick(kind):
        """点のコマを1つ数える（呼び出し元は組まない）。"""
        t = elapsed()
        with lock:
            run = dots["run"]
            if run is None:
                run = dots["run"] = {"ticks": 0, "calls": {}, "from": t, "to": t}
            run["calls"][kind] = run["calls"].get(kind, 0) + 1
            if kind == "update_button_texts":
                run["ticks"] += 1
            run["to"] = t

    def event(kind, **fields):
        # 点以外の記録の前に、それまでの点送りを閉じる（並びが時刻どおりになる）。
        flush_dots(kind)
        emit(kind, fields)

    # ------------------------------------------------------------ 見張り
    def start_poll():
        try:
            from kivy.app import App
            from kivy.clock import Clock
        except Exception:
            ctx.log("busy display probe: no kivy Clock; polling disabled", level="WARN")
            return

        def poll(_dt):
            if ctx.superseded():
                # 版5: 1プロセス1回（注入のたびに前の世代が1行ずつ書いていた）。
                if say_once("polling stopped"):
                    write("polling stopped (a newer injection took over; "
                          "said once per process)")
                return False
            try:
                run = dots["run"]
                if run is not None and elapsed() - run["to"] >= DOTS_IDLE_SECONDS:
                    flush_dots("idle")
                app = App.get_running_app()
                if app is None:
                    return True
                watch_screen(snapshot(app))
            except Exception:
                ctx.log_exc("busy display probe: polling failed")
            return True

        Clock.schedule_interval(poll, POLL_SECONDS)
        if say_once("polling"):
            write("polling the screen every {}s (gen {}; said once per process)".format(
                POLL_SECONDS, ctx.generation))

    def watch_screen(shot):
        """見張りの写しを前と比べ、変わった項目だけを `screen` の行にする（版5）。

        注入ごとの最初の1行だけ全項目（`full=True`）。2行目からは変わった項目だけ（`full=False`）で、
        書かない項目は前の行から変わっていない。
        """
        previous = last["snapshot"]
        if shot == previous:
            return
        since = (None if last["at"] is None
                 else round(time.monotonic() - last["at"], 3))
        last["snapshot"] = shot
        last["at"] = time.monotonic()
        if previous is None:
            event("screen", since=since, full=True, **shot)
        else:
            changed = dict((key, value) for key, value in shot.items()
                           if key not in previous or previous[key] != value)
            event("screen", since=since, full=False, **changed)

    # ------------------------------------------------------------ 点送りの始め手（版4）
    def note_schedule(callback, args, kwargs):
        """`display_button_load` の予約。点送りの続きでなければ、始め手として1行書く。

        `args` は `Clock.schedule_once` に渡ったまま（先頭が callback、次が timeout）。
        """
        chain = call_stack()
        if is_continuation(chain):
            return
        app = getattr(callback, "__self__", None)
        clock = getattr(sys.modules.get("kivy.clock"), "Clock", None)
        event("dots_start", pending=pending_loads(clock) if clock is not None else None,
              enabled=getattr(app, "is_button_enabled", None),
              timeout=args[1] if len(args) > 1 else kwargs.get("timeout"),
              stack=format_stack(chain))

    # 版5: インスタンスの属性の直の差し替えから、ローダの包みへ移した（docstring の版5）。
    # `alias_scan=False`: 包む相手はインスタンスの束縛メソッドで、別名がモジュールに写っていることは無い。
    @ctx.wrap(SCHEDULE_TARGET, required=False, safe=True, alias_scan=False)
    def schedule_once(orig, *args, **kwargs):
        callback = args[0] if args else kwargs.get("callback")
        if getattr(callback, "__name__", "") == "display_button_load":
            try:
                note_schedule(callback, args, kwargs)
            except Exception:
                pass
        return orig(*args, **kwargs)

    if say_once("watching"):
        write("watching who starts the dots ({}; said once per process)".format(SCHEDULE_TARGET))

    ctx.on_ready(start_poll, key="234_probe_busy_display:poll:{}".format(ctx.generation))

    # ------------------------------------------------------------ 呼び出し
    @ctx.wrap("__main__:InstantaleApp.display_button_load", required=False, safe=True)
    def display_button_load(orig, self, *args, **kwargs):
        try:
            enabled = getattr(self, "is_button_enabled", None)
            if enabled is False:
                # 待機中はゲーム自身がこれを約0.3秒ごとに予約し直して点を送る（GAME.md §2.4）。
                dot_tick("display_button_load")
            else:
                event("display_button_load", enabled=enabled,
                      display=texts_of(getattr(self, "to_display_buttons", None)),
                      caller=frames.caller())
        except Exception:
            ctx.log_exc("busy display probe: display_button_load")
        return orig(self, *args, **kwargs)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_button_texts",
              required=False, safe=True)
    def update_button_texts(orig, self, *args, **kwargs):
        try:
            value = args[1] if len(args) > 1 else kwargs.get("value")
            if all_dots(value):
                dot_tick("update_button_texts")
            else:
                event("update_button_texts", value=texts_of(value), caller=frames.caller())
        except Exception:
            ctx.log_exc("busy display probe: update_button_texts")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            event("refresh_choice_buttons", enabled=getattr(self, "is_button_enabled", None),
                  display=texts_of(getattr(self, "to_display_buttons", None)),
                  caller=frames.caller())
        except Exception:
            ctx.log_exc("busy display probe: refresh_choice_buttons")
        return result

    @ctx.wrap("__main__:InstantaleApp.set_buttons_to_normal", required=False, safe=True)
    def set_buttons_to_normal(orig, self, *args, **kwargs):
        try:
            event("set_buttons_to_normal", caller=frames.caller())
        except Exception:
            ctx.log_exc("busy display probe: set_buttons_to_normal")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.process_choice", required=False, safe=True)
    def process_choice(orig, self, *args, **kwargs):
        try:
            function = args[0] if args else kwargs.get("function")
            choice = args[1] if len(args) > 1 else kwargs.get("choice_text", "")
            event("process_choice", phase=type(function).__name__, choice=choice,
                  enabled=getattr(self, "is_button_enabled", None))
        except Exception:
            ctx.log_exc("busy display probe: process_choice")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, *args, **kwargs):
        try:
            context = args[0] if args else kwargs.get("context")
            if is_dots(context):
                event("add_text_dots", text=context, caller=frames.caller())
        except Exception:
            ctx.log_exc("busy display probe: add_text")
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------ 背景（版2）
    for name in ("change_background_image_to_current_location",
                 "change_background_image_from_location_id",
                 "change_background_image_to_inn_room"):
        def install_bg(name=name):
            @ctx.wrap("__main__:InstantaleApp.{}".format(name), required=False, safe=True)
            def change(orig, self, *args, **kwargs):
                try:
                    event(name, args=frames.repr_value(args),
                          picture=picture(getattr(self, "location_image", None)),
                          caller=frames.caller())
                except Exception:
                    ctx.log_exc("busy display probe: {}".format(name))
                return orig(self, *args, **kwargs)
        install_bg()

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_image_source",
              required=False, safe=True)
    def update_image_source(orig, self, *args, **kwargs):
        try:
            value = args[1] if len(args) > 1 else kwargs.get("value")
            event("update_image_source", picture=picture(value), caller=frames.caller())
        except Exception:
            ctx.log_exc("busy display probe: update_image_source")
        return orig(self, *args, **kwargs)

    for name in ("disable_text_send_button", "enable_text_send_button"):
        def install(name=name):
            @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.{}".format(name),
                      required=False, safe=True)
            def toggle(orig, self, *args, **kwargs):
                try:
                    event(name, caller=frames.caller())
                except Exception:
                    ctx.log_exc("busy display probe: {}".format(name))
                return orig(self, *args, **kwargs)
        install()

    ctx.log("busy display probe: ready ({}, {})".format(LOG_BASENAME, RECORD_BASENAME))
