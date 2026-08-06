# -*- coding: utf-8 -*-
"""本文の出し方（逐次／一括）と、読み終わった本文の灰色化。

出し方は2つ（`BATCH_MODE`）。

    click    ゲーム本来の1文字ずつの打ち出しに任せる。打っている最中に画面を
             クリックしたら、残りを一括で出して打ち終わりにする（既定）
    always   本文を一括で反映し、新着部分だけを行単位で見せる

古い本文の見分け方も2つ（`FRESH_MODE`）。追加からの経過秒数（`FRESH_SECONDS`）か、
その後に何回本文が追加されたか（`FRESH_SESSIONS`。1つの本文＝1セッション）。
どちらでも色が変わるのは本文が追加された瞬間だけで、読んでいる最中には変えない。
"""

import math
import sys
import time
import weakref

from instantale_modloader import frames, ui

BATCH_MODE = "click"
FRESH_MODE = "seconds"
FADE_SECONDS = 0.2
REVEAL_LINE_SECONDS = 0.04
OVERLAY_RGBA = (0, 0, 0, 1)
OLD_TEXT_COLOR = "#808080"
FRESH_SECONDS = 10.0
FRESH_SESSIONS = 1
CONTEXT_LINES = 3
monotonic_time = time.monotonic
STATE_STORE_ATTR = "__instantale_batch_message_render_states__"

# 窓に結んだクリックの見張りを控えておく印。注入し直したときに古い版の手が
# 残らないよう、結び直す前にこれで外す（`113_` の `on_resize` と同じ作法）。
WINDOW_ATTR = "_instantale_batch_render_touch"

# 覚えておく本文の数。照合は1文字ごとに走るので、際限なく伸ばさない。
# 画面に載る本文は `117_` が上限を掛けているので、これより古い本文は
# そもそも照合対象に残っていない。
MAX_SEGMENTS = 60

# Kivy の Label は「新しく足された部分だけ」を個別にフェードできない。動かせるのは
# ラベル全体の opacity だけなので、0 から始めると読んでいる途中の本文まで一度消える。
# 新着が来たことは分かる程度に留める。
FADE_FROM = 0.4


def segment_parts(segment):
    """控えた本文を (本文, 追加時刻, セッション番号) に開く。

    注入し直しても控えは `sys` に残る（世代をまたぐ）。古い版が置いた2つ組が
    混ざっていても読めるようにしておく。
    """
    session = segment[2] if len(segment) > 2 else 0
    return segment[0], segment[1], session


def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "states": weakref.WeakKeyDictionary(),
            "fallback_states": {},
        }
        setattr(sys, STATE_STORE_ATTR, store)
    # 打ち出しの最中かどうかは1本しか無い（同時に2つの本文は流れない）ので、
    # app ごとの控えではなくここに置く。クリックの見張りから引ける場所が要る。
    store.setdefault("stream", None)
    states = store["states"]
    fallback_states = store["fallback_states"]
    warned = {}     # 一度きりの警告の控え（種類ごと）

    def new_state():
        return {
            "generation": 0,
            "overlay": None,
            "markup_label": None,
            "segments": [],
            "session": 0,
            "stream_cache": None,
        }

    def state_for(app):
        try:
            state = states.get(app)
            if state is None:
                state = new_state()
                states[app] = state
        except TypeError:
            state = fallback_states.setdefault(id(app), new_state())
        # 古い版が置いた控えには新しい鍵が無い。
        state.setdefault("session", 0)
        state.setdefault("stream_cache", None)
        return state

    def note_segment(state, context):
        state["session"] += 1
        state["segments"].append((context, monotonic_time(), state["session"]))
        if len(state["segments"]) > MAX_SEGMENTS:
            del state["segments"][:-MAX_SEGMENTS]

    def is_old(state, added_at, session):
        if FRESH_MODE == "sessions":
            return state["session"] - session >= FRESH_SESSIONS
        return monotonic_time() - added_at >= FRESH_SECONDS

    def hide_overlay(state):
        overlay = state.get("overlay")
        if not isinstance(overlay, dict):
            return
        try:
            overlay["color"].rgba = (0, 0, 0, 0)
            overlay["rectangle"].size = (0, 0)
        except Exception:
            state["overlay"] = None

    def set_cover(state, scroll, height):
        overlay = state.get("overlay")
        if not isinstance(overlay, dict) or overlay.get("scroll") is not scroll:
            hide_overlay(state)
            try:
                from kivy.graphics import Color, Rectangle
                with scroll.canvas.after:
                    color = Color(*OVERLAY_RGBA)
                    rectangle = Rectangle(pos=scroll.pos, size=scroll.size)
                overlay = {
                    "scroll": scroll,
                    "color": color,
                    "rectangle": rectangle,
                }
                state["overlay"] = overlay
            except Exception:
                return False
        try:
            height = max(0.0, min(float(height), float(scroll.height)))
            overlay["color"].rgba = OVERLAY_RGBA if height else (0, 0, 0, 0)
            overlay["rectangle"].pos = (scroll.x, scroll.y)
            overlay["rectangle"].size = (scroll.width, height)
            return True
        except Exception:
            hide_overlay(state)
            state["overlay"] = None
            return False

    def snapshot(app, state):
        try:
            hud = ui.find_hud(app)
            label = frames.attr(hud, "text_display")
            scroll = frames.attr(hud, "scroll_view")
            if label in (None, frames.MISSING) or scroll in (None, frames.MISSING):
                return None
            texture_size = frames.attr(label, "texture_size", (0, 0))
            before_height = float(texture_size[1])
            if not set_cover(state, scroll, frames.attr(scroll, "height", 0)):
                return None
            return {
                "hud": hud,
                "label": label,
                "scroll": scroll,
                "before_height": before_height,
            }
        except Exception:
            hide_overlay(state)
            return None

    def line_height_of(label):
        try:
            return max(
                1.0,
                float(frames.attr(label, "font_size", 1.0))
                * float(frames.attr(label, "line_height", 1.0)))
        except (TypeError, ValueError):
            return 1.0

    def measured_height(label, text):
        try:
            from kivy.core.text import Label as CoreLabel
            text_size = frames.attr(label, "text_size", (None, None))
            width = text_size[0] if isinstance(text_size, (list, tuple)) else None
            options = {
                "text": text,
                "font_size": frames.attr(label, "font_size", 12),
                "text_size": (width, None),
                "line_height": frames.attr(label, "line_height", 1.0),
            }
            font_name = frames.attr(label, "font_name")
            if isinstance(font_name, str) and font_name:
                options["font_name"] = font_name
            padding = frames.attr(label, "padding")
            if isinstance(padding, (list, tuple)) and len(padding) == 4:
                options["padding"] = tuple(padding)
            probe = CoreLabel(**options)
            probe.refresh()
            return float(probe.texture.size[1])
        except Exception:
            return None

    def estimate_height(label, text, line_height):
        try:
            text_size = frames.attr(label, "text_size", (None, None))
            width = float(text_size[0])
            font_size = max(1.0, float(frames.attr(label, "font_size", 1.0)))
            columns = max(1, int(width / font_size))
        except (TypeError, ValueError, IndexError):
            columns = 40
        lines = 0
        for part in text.split("\n"):
            lines += max(1, int(math.ceil(float(len(part)) / columns)))
        return max(line_height, lines * line_height)

    def region_height_from(label, shown, start):
        if type(start) is not int or start < 0 or start > len(shown):
            return None
        prefix = shown[:start]
        if prefix and not prefix.endswith("\n"):
            return None
        line_height = line_height_of(label)
        prefix_height = measured_height(label, prefix)
        try:
            full_height = float(frames.attr(
                label, "texture_size", (0, 0))[1])
        except (TypeError, ValueError, IndexError):
            full_height = None
        if full_height is not None and prefix_height is not None:
            return max(line_height, full_height - prefix_height)
        return estimate_height(label, shown[start:], line_height)

    # -- 色 ------------------------------------------------------------------
    def build_markup(state, shown):
        """`shown` を色付きの markup に組み直す。

        戻り値は次の4つ。`tail` は「控えのどれにも当たらない末尾」＝いま打って
        いる最中の本文で、そこは触らずに白のまま残す。

            head        `shown[:tail]` の色付き表現
            tail        末尾の開始位置
            has_old     灰色にした部分があるか
            fresh_start 白いままの部分の先頭（一括表示のときの新着位置）
        """
        visible_segments = []
        claimed = []
        for segment in reversed(state["segments"]):
            text, added_at, session = segment_parts(segment)
            if not text:
                continue
            start = shown.rfind(text)
            end = start + len(text)
            while start >= 0 and any(
                    start < claimed_end and end > claimed_start
                    for claimed_start, claimed_end in claimed):
                start = shown.rfind(text, 0, start)
                end = start + len(text)
            if start < 0:
                continue
            visible_segments.append((start, end, added_at, session))
            claimed.append((start, end))
        if not visible_segments:
            return None
        visible_segments.sort()

        from kivy.utils import escape_markup
        parts = []
        has_old = False
        fresh_start = None
        position = 0
        for start, end, added_at, session in visible_segments:
            if position < start:
                parts.append("[color={}]{}[/color]".format(
                    OLD_TEXT_COLOR, escape_markup(shown[position:start])))
                has_old = True
            escaped = escape_markup(shown[start:end])
            if is_old(state, added_at, session):
                parts.append("[color={}]{}[/color]".format(
                    OLD_TEXT_COLOR, escaped))
                has_old = True
            else:
                parts.append(escaped)
                if fresh_start is None:
                    fresh_start = start
            position = end
        return {
            "head": "".join(parts),
            "tail": position,
            "has_old": has_old,
            "fresh_start": fresh_start,
        }

    def apply_text_colors(state, hud, label, shown):
        """打ち終わっている本文を塗り直す（一括表示の経路）。"""
        try:
            from kivy.utils import escape_markup
            built = build_markup(state, shown)
            if built is None:
                return False
            text = built["head"]
            fresh_start = built["fresh_start"]
            if built["tail"] < len(shown):
                text += escape_markup(shown[built["tail"]:])
                if fresh_start is None:
                    fresh_start = built["tail"]
            if built["has_old"]:
                label.markup = True
                label.text = text
                state["markup_label"] = label
                update = frames.attr(label, "texture_update")
                if callable(update):
                    update()
                update_height = frames.attr(hud, "update_label_height")
                if callable(update_height):
                    update_height()
            return fresh_start
        except Exception:
            ctx.log_exc("batch message render: could not update text colors")
            return False

    def stream_colors(state, label):
        """1文字進むたびに、打ち出し中の本文より前を塗り直す（逐次表示の経路）。

        ゲームは1文字ごとにラベルへ生の本文を入れ直すので、色はそのたびに消える。
        毎回すべてを照合すると1文字ぶんの手間がそこに乗るため、**前の部分の色は
        本文1つにつき1度だけ組み立てて控える**。伸びるのは末尾だけなので、
        以降は控えた色付きの前半と、逃がした末尾を繋ぐだけで済む。
        """
        shown = frames.attr(label, "text")
        if not isinstance(shown, str):
            return
        from kivy.utils import escape_markup
        cache = state.get("stream_cache")
        if not (isinstance(cache, dict) and cache["label"] is label
                and shown.startswith(cache["prefix"])):
            built = build_markup(state, shown)
            if built is None or not built["has_old"]:
                # 灰色にするものが無い＝生の本文をそのまま出してよい。markup を
                # 落としておかないと、本文中の `[` を書式として食われる。
                if frames.attr(label, "markup", False):
                    label.markup = False
                state["stream_cache"] = None
                return
            cache = {
                "label": label,
                "prefix": shown[:built["tail"]],
                "head": built["head"],
            }
            state["stream_cache"] = cache
        label.markup = True
        label.text = cache["head"] + escape_markup(shown[len(cache["prefix"]):])

    def drop_markup(state, label):
        """ゲームが生の本文を入れる直前に markup を落とす。"""
        if state.get("markup_label") is not label:
            return
        try:
            label.markup = False
        except Exception:
            ctx.log_exc("batch message render: could not reset text markup")
        state["markup_label"] = None

    # -- 一括表示 ------------------------------------------------------------
    def schedule(callback, delay=0):
        try:
            from kivy.clock import Clock
            Clock.schedule_once(callback, delay)
        except Exception:
            callback()

    def fade(app, state):
        generation = state["generation"]

        def run(_dt=None):
            # Clock のコールバックは `ctx.wrap(safe=True)` の守備範囲の外にある。
            # ここで例外を出すとゲームごと落ちる ― 実際に落とした
            # （`out/live_crashes.log`: 存在しない `ui.hud_of` を呼んでいた）。
            try:
                if state["generation"] != generation:
                    return
                hide_overlay(state)
                hud = ui.find_hud(app)
                label = frames.attr(hud, "text_display")
                if label in (None, frames.MISSING):
                    return
                try:
                    from kivy.animation import Animation
                    Animation.cancel_all(label)
                    label.opacity = FADE_FROM
                    Animation(opacity=1, duration=FADE_SECONDS).start(label)
                except Exception:
                    label.opacity = 1
            except Exception:
                hide_overlay(state)
                ctx.log_exc("batch message render: could not show text")

        schedule(run)

    def reveal(app, state, context, captured):
        generation = state["generation"]

        def begin(_dt=None):
            try:
                if state["generation"] != generation:
                    return
                if not isinstance(captured, dict):
                    fade(app, state)
                    return
                label = captured["label"]
                scroll = captured["scroll"]
                shown = frames.attr(label, "text")
                if not isinstance(shown, str):
                    fade(app, state)
                    return
                viewport_height = float(scroll.height)
                fresh_start = apply_text_colors(
                    state, captured["hud"], label, shown)
                region_height = region_height_from(label, shown, fresh_start)
                if not region_height or viewport_height <= 0:
                    fade(app, state)
                    return
                content_height = max(
                    viewport_height,
                    float(frames.attr(label, "height",
                                      frames.attr(label, "texture_size", (0, 0))[1])))
                region_height = min(region_height, content_height)
                line_height = min(line_height_of(label), viewport_height)
                overflow = max(0.0, content_height - viewport_height)
                progress = {"revealed": 0.0}
                context_height = min(
                    CONTEXT_LINES * line_height,
                    max(0.0, content_height - region_height))

                def position_for(remaining):
                    if overflow <= 0:
                        return 0.0
                    return max(0.0, min(
                        1.0, (remaining - viewport_height) / overflow))

                scroll.scroll_y = position_for(region_height + context_height)
                page_height = min(viewport_height, region_height)
                page = {"revealed": 0.0}
                if not set_cover(state, scroll, page_height):
                    fade(app, state)
                    return

                def step(_step_dt=None):
                    try:
                        if state["generation"] != generation:
                            return
                        reveal_now = min(
                            line_height, page_height - page["revealed"])
                        page["revealed"] += reveal_now
                        progress["revealed"] += reveal_now
                        cover_height = max(
                            0.0, page_height - page["revealed"])
                        set_cover(state, scroll, cover_height)
                        if page["revealed"] >= page_height:
                            hide_overlay(state)
                        else:
                            schedule(step, REVEAL_LINE_SECONDS)
                    except Exception:
                        hide_overlay(state)
                        ctx.log_exc("batch message render: line reveal failed")

                schedule(step, REVEAL_LINE_SECONDS)
            except Exception:
                hide_overlay(state)
                ctx.log_exc("batch message render: could not start line reveal")

        schedule(begin)

    def show_at_once(orig, app, dt, context, state):
        immediate = frames.attr(app, "add_text_immediately")
        state["generation"] += 1
        captured = snapshot(app, state)
        if isinstance(captured, dict):
            drop_markup(state, captured["label"])
        immediate(context)
        note_segment(state, context)

        # 後始末はゲームにやらせる。待ち行列 `to_add_text_list` から取り除くのも
        # `is_adding_text` を下ろすのも、鎖の**最後の**呼び出しの仕事だ
        # （`out/text_viewport.log`: 行列が 1 から 0 に減るのは `index == len(context)`
        # の呼び出しと同時）。ここを自分で真似して行列を放置した結果、次の本文が
        # 永久に出ず、消えない先頭が何度も再表示された ― 実機で踏んだ。
        before = frames.attr(app, "display_text")
        result = orig(app, dt, context, len(context))
        after = frames.attr(app, "display_text")
        if before != after and not warned.get("tail"):
            # 終端の index が「何も足さずに完了だけ」である保証は実測3件からの
            # 推定でしかない。外れていれば末尾が重複するので、黙って壊れないよう
            # 一度だけ残す。
            warned["tail"] = True
            ctx.log("batch message render: the finishing call appended text"
                    " ({} -> {} chars); the tail may be duplicated".format(
                        len(before), len(after)), level="WARN")

        reveal(app, state, context, captured)
        return result

    # -- 逐次表示とクリックでの打ち切り --------------------------------------
    def begin_stream(orig, app, dt, context, index, state):
        """ゲーム本来の打ち出しに任せる。色付けだけこちらで面倒を見る。"""
        state["generation"] += 1        # 走っている一括表示の続きを止める
        hide_overlay(state)
        state["stream_cache"] = None    # 前の本文の色は使い回せない
        try:
            hud = ui.find_hud(app)
            label = frames.attr(hud, "text_display")
            if label not in (None, frames.MISSING):
                drop_markup(state, label)
        except Exception:
            ctx.log_exc("batch message render: could not prepare the label")
        note_segment(state, context)
        store["stream"] = {
            "app": app,
            "state": state,
            "context": context,
            "skip": False,
        }
        try:
            return orig(app, dt, context, index)
        except Exception:
            store["stream"] = None
            raise

    def typed_length(shown, context, hint):
        """いま何文字ぶん出ているかを、`index` を手掛かりに確かめる。

        `index` の意味（次に足す文字の1つ手前）は実測からの推定なので、
        鵜呑みにはしない。前後1文字まで見て、それでも本文の末尾と噛み合わ
        なければ諦める（打ち切らずに逐次のまま続ける）。
        """
        if not isinstance(shown, str) or type(hint) is not int:
            return None
        for length in (hint, hint - 1, hint + 1):
            if 1 <= length <= len(context) and shown.endswith(context[:length]):
                return length
        return None

    def finish_stream(orig, app, dt, context, index, stream):
        """クリックされた。残りを一度に出して、後始末はゲームに返す。"""
        state = stream["state"]
        shown = frames.attr(app, "display_text")
        typed = typed_length(shown, context, index + 1)
        if typed is None:
            store["stream"] = None
            if not warned.get("skip"):
                warned["skip"] = True
                ctx.log("batch message render: could not tell how much of the"
                        " message is already shown; the click was ignored",
                        level="WARN")
            return orig(app, dt, context, index)
        remaining = context[typed:]
        if remaining:
            try:
                # 1文字ずつ足しているのと同じ経路（`display_text` の書き換え）に
                # 残り全部を一度に流す。塗り直しは1回で済む。
                app.display_text = shown + remaining
            except Exception:
                store["stream"] = None
                ctx.log_exc("batch message render: could not skip to the end")
                return orig(app, dt, context, index)
        store["stream"] = None
        return orig(app, dt, context, len(context))

    def continue_stream(orig, app, dt, context, index):
        stream = store.get("stream")
        if (not isinstance(stream, dict) or stream["app"] is not app
                or stream["context"] != context):
            # 1文字ぶんの続きの呼び出し。注入し直した時にだけ飛んでくる
            # （こちらが始めていない鎖）。ゲームに返して終わらせる。
            return orig(app, dt, context, index)
        if type(index) is int and index >= len(context):
            store["stream"] = None      # 打ち終わり
            return orig(app, dt, context, index)
        if not stream["skip"]:
            return orig(app, dt, context, index)
        return finish_stream(orig, app, dt, context, index, stream)

    def on_screen_touch(_window, _touch):
        """画面のどこかが押された。打ち出し中なら打ち切りの合図にする。

        **偽を返して（何も返さないで）おくこと。** 真を返すと Kivy はここで
        配送を止めるので、ボタンが押せなくなる。
        """
        try:
            stream = store.get("stream")
            if isinstance(stream, dict):
                stream["skip"] = True
        except Exception:
            ctx.log_exc("batch message render: could not accept the click")

    def watch_touch():
        try:
            from kivy.core.window import Window
        except Exception:
            return False            # ゲームの外（オフライン検証）では窓が無い
        previous = frames.attr(Window, WINDOW_ATTR, None)
        if previous is not None:
            try:
                Window.unbind(on_touch_down=previous)
            except Exception:
                pass
            try:
                setattr(Window, WINDOW_ATTR, None)
            except Exception:
                pass
        if BATCH_MODE != "click":
            return False
        try:
            Window.bind(on_touch_down=on_screen_touch)
            setattr(Window, WINDOW_ATTR, on_screen_touch)
            return True
        except Exception:
            ctx.log_exc("batch message render: could not watch for clicks")
            return False

    # -- 差し込み ------------------------------------------------------------
    @ctx.wrap("__main__:InstantaleApp.add_text_display", required=False, safe=True)
    def add_text_display(orig, self, dt, context, index=-1):
        if index != -1 or not isinstance(context, str):
            return continue_stream(orig, self, dt, context, index)

        state = state_for(self)
        store["stream"] = None      # 新しい本文が始まった＝前の鎖は終わっている
        if BATCH_MODE == "always" and callable(
                frames.attr(self, "add_text_immediately")):
            return show_at_once(orig, self, dt, context, state)
        return begin_stream(orig, self, dt, context, index, state)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text",
              required=False, safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        # 先にゲームに塗らせる。こちらは塗り終わった後の色だけを直す（`112_` と
        # 同じ作法）。打ち出し中でなければ何もしない ＝ 一括表示のときは素通り。
        result = orig(self, instance, value, *args, **kwargs)
        stream = store.get("stream")
        if isinstance(stream, dict):
            try:
                label = frames.attr(self, "text_display")
                if label not in (None, frames.MISSING):
                    stream_colors(stream["state"], label)
            except Exception:
                ctx.log_exc("batch message render: could not color the text")
        return result

    watching = watch_touch()
    ctx.log("batch message render installed "
            "(BATCH_MODE={}, click watch={}, FRESH_MODE={}, "
            "FRESH_SECONDS={}, FRESH_SESSIONS={}, OLD_TEXT_COLOR={})".format(
                BATCH_MODE, "on" if watching else "off", FRESH_MODE,
                FRESH_SECONDS, FRESH_SESSIONS, OLD_TEXT_COLOR))
