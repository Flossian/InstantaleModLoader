# -*- coding: utf-8 -*-
r"""計測: 待機表示（「…」）。ゲームは変えない。

ゲーム標準の「…」と、ローダの `ui.Screen.busy_on` が出す「…」を同じ物差しで録り、
見た目の違いの元を探す。
ローダの側は `301_` がゲームの「クエストを探す」を**画面で見て真似た**もので
（VERIFICATION_LOG.md §2.23）、ゲームがどの仕組みで出しているかは測っていない。
ゲームは Nuitka ビルドで中のコードは読めない（`232_` の記録）ので、外から録る。

録るもの

    見張り   0.05 秒ごと（メインスレッド）に画面の状態を読み、**変わったときだけ**1行。
             左右の選択肢の枠（`hud.buttons` / `hud.right_buttons`）の文字・disabled・opacity、
             `is_button_enabled`、`is_adding_text`、`to_display_buttons`、送信ボタン、
             背景の絵（`location_image` のフォルダ名。版2）
    呼び出し 選択肢を塗る手（`display_button_load` / `update_button_texts`）、
             組み直し（`refresh_choice_buttons` / `set_buttons_to_normal`）、
             場面の区切り（`process_choice`）、送信ボタンの有効・無効、点だけの `add_text`、
             背景を替える手（`change_background_image_*` と HUD の `update_image_source`。版2）。
             スレッド名と呼び出し元つき

MOD が出している「…」の区間は、その MOD のログの `busy on` / `busy off` の行
（`ui.Screen` が書く。例 `outeal_estate.log` の `real estate: busy on`）と時刻で突き合わせる。
ここで時刻をミリ秒まで書くのはそのため。

    out\busy_display.log     読む用
    out\busy_display.jsonl   1件＝1行。後から数える用
"""
import datetime
import os
import threading
import time

from instantale_modloader import frames

LOG_BASENAME = "busy_display.log"
RECORD_BASENAME = "busy_display.jsonl"

#: 見張りの周期。「…」は約 0.3 秒でコマが進む（GAME.md §2.4）ので、その6分の1。
POLL_SECONDS = 0.05

#: 1つの呼び出しの記録に写す選択肢の数の上限。
TEXT_LIMIT = 8


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    started = time.monotonic()
    last = {"snapshot": None, "at": None}

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
            out.append([getattr(widget, "text", None),
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
            "display": texts_of(getattr(app, "to_display_buttons", None)),
            "choices": len(getattr(app, "buttons", None) or []),
            "send_disabled": getattr(send, "disabled", None) if send is not None else None,
            "picture": picture(getattr(app, "location_image", None)),
        }

    def event(kind, **fields):
        row = {"at": now(), "t": elapsed(), "kind": kind, "thread": thread()}
        row.update(fields)
        record(row)
        write("{:>9.3f} [{}] {} {}".format(
            row["t"], row["thread"], kind,
            " ".join("{}={}".format(k, v) for k, v in fields.items())))

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
                write("polling stopped (a newer injection took over)")
                return False
            try:
                app = App.get_running_app()
                if app is None:
                    return True
                shot = snapshot(app)
                if shot != last["snapshot"]:
                    since = (None if last["at"] is None
                             else round(time.monotonic() - last["at"], 3))
                    last["snapshot"] = shot
                    last["at"] = time.monotonic()
                    event("screen", since=since, **shot)
            except Exception:
                ctx.log_exc("busy display probe: polling failed")
            return True

        Clock.schedule_interval(poll, POLL_SECONDS)
        write("polling the screen every {}s (gen {})".format(POLL_SECONDS, ctx.generation))

    ctx.on_ready(start_poll, key="234_probe_busy_display:poll:{}".format(ctx.generation))

    # ------------------------------------------------------------ 呼び出し
    @ctx.wrap("__main__:InstantaleApp.display_button_load", required=False, safe=True)
    def display_button_load(orig, self, dt=0, *args, **kwargs):
        event("display_button_load", enabled=getattr(self, "is_button_enabled", None),
              display=texts_of(getattr(self, "to_display_buttons", None)),
              caller=frames.caller())
        return orig(self, dt, *args, **kwargs)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_button_texts",
              required=False, safe=True)
    def update_button_texts(orig, self, instance, value, *args, **kwargs):
        event("update_button_texts", value=texts_of(value), caller=frames.caller())
        return orig(self, instance, value, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        event("refresh_choice_buttons", enabled=getattr(self, "is_button_enabled", None),
              display=texts_of(getattr(self, "to_display_buttons", None)),
              caller=frames.caller())
        return result

    @ctx.wrap("__main__:InstantaleApp.set_buttons_to_normal", required=False, safe=True)
    def set_buttons_to_normal(orig, self, *args, **kwargs):
        event("set_buttons_to_normal", caller=frames.caller())
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.process_choice", required=False, safe=True)
    def process_choice(orig, self, function, choice_text="", *args, **kwargs):
        event("process_choice", phase=type(function).__name__, choice=choice_text,
              enabled=getattr(self, "is_button_enabled", None))
        return orig(self, function, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        if isinstance(context, str) and context.strip() \
                and not context.strip(".。…・ 　"):
            event("add_text_dots", text=context, caller=frames.caller())
        return orig(self, context, *args, **kwargs)

    # ------------------------------------------------------------ 背景（版2）
    for name in ("change_background_image_to_current_location",
                 "change_background_image_from_location_id",
                 "change_background_image_to_inn_room"):
        def install_bg(name=name):
            @ctx.wrap("__main__:InstantaleApp.{}".format(name), required=False, safe=True)
            def change(orig, self, *args, **kwargs):
                event(name, args=frames.repr_value(args),
                      picture=picture(getattr(self, "location_image", None)),
                      caller=frames.caller())
                return orig(self, *args, **kwargs)
        install_bg()

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_image_source",
              required=False, safe=True)
    def update_image_source(orig, self, instance, value, *args, **kwargs):
        event("update_image_source", picture=picture(value), caller=frames.caller())
        return orig(self, instance, value, *args, **kwargs)

    for name in ("disable_text_send_button", "enable_text_send_button"):
        def install(name=name):
            @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.{}".format(name),
                      required=False, safe=True)
            def toggle(orig, self, *args, **kwargs):
                event(name, caller=frames.caller())
                return orig(self, *args, **kwargs)
        install()

    ctx.log("busy display probe: ready ({}, {})".format(LOG_BASENAME, RECORD_BASENAME))
