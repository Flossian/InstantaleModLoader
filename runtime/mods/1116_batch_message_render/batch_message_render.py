# -*- coding: utf-8 -*-
"""本文を一括描画し、新着部分だけを行単位で見せる。"""

import math
import sys
import time
import weakref

from instantale_modloader import frames, ui

FADE_SECONDS = 0.2
REVEAL_LINE_SECONDS = 0.04
OVERLAY_RGBA = (0, 0, 0, 1)
OLD_TEXT_COLOR = "#808080"
FRESH_SECONDS = 10.0
CONTEXT_LINES = 3
monotonic_time = time.monotonic
STATE_STORE_ATTR = "__instantale_batch_message_render_states__"

# Kivy の Label は「新しく足された部分だけ」を個別にフェードできない。動かせるのは
# ラベル全体の opacity だけなので、0 から始めると読んでいる途中の本文まで一度消える。
# 新着が来たことは分かる程度に留める。
FADE_FROM = 0.4


def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "states": weakref.WeakKeyDictionary(),
            "fallback_states": {},
        }
        setattr(sys, STATE_STORE_ATTR, store)
    states = store["states"]
    fallback_states = store["fallback_states"]
    warned = []

    def new_state():
        return {
            "generation": 0,
            "overlay": None,
            "markup_label": None,
            "segments": [],
        }

    def state_for(app):
        try:
            state = states.get(app)
            if state is None:
                state = new_state()
                states[app] = state
            return state
        except TypeError:
            return fallback_states.setdefault(id(app), new_state())

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

    def apply_text_colors(state, hud, label, shown):
        visible_segments = []
        claimed = []
        for text, added_at in reversed(state["segments"]):
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
            visible_segments.append((start, end, added_at))
            claimed.append((start, end))
        if not visible_segments:
            return False
        visible_segments.sort()

        try:
            from kivy.utils import escape_markup
            now = monotonic_time()
            parts = []
            has_old_text = False
            fresh_start = None
            position = 0
            for start, end, added_at in visible_segments:
                if position < start:
                    parts.append("[color={}]{}[/color]".format(
                        OLD_TEXT_COLOR, escape_markup(shown[position:start])))
                    has_old_text = True
                text = shown[start:end]
                escaped = escape_markup(text)
                if now - added_at >= FRESH_SECONDS:
                    parts.append("[color={}]{}[/color]".format(
                        OLD_TEXT_COLOR, escaped))
                    has_old_text = True
                else:
                    parts.append(escaped)
                    if fresh_start is None:
                        fresh_start = start
                position = end
            if position < len(shown):
                parts.append(escape_markup(shown[position:]))
                if fresh_start is None:
                    fresh_start = position
            if has_old_text:
                label.markup = True
                label.text = "".join(parts)
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

    @ctx.wrap("__main__:InstantaleApp.add_text_display", required=False, safe=True)
    def add_text_display(orig, self, dt, context, index=-1):
        if index != -1 or not isinstance(context, str):
            # 1文字ぶんの続きの呼び出し。注入し直した時にだけ飛んでくる
            # （こちらが始めた本文は鎖を作らない）。ゲームに返して終わらせる。
            return orig(self, dt, context, index)

        immediate = frames.attr(self, "add_text_immediately")
        if not callable(immediate):
            return orig(self, dt, context, index)

        state = state_for(self)
        state["generation"] += 1
        captured = snapshot(self, state)
        if (isinstance(captured, dict)
                and state.get("markup_label") is captured["label"]):
            try:
                captured["label"].markup = False
            except Exception:
                ctx.log_exc("batch message render: could not reset text markup")
            state["markup_label"] = None
        immediate(context)
        state["segments"].append((context, monotonic_time()))

        # 後始末はゲームにやらせる。待ち行列 `to_add_text_list` から取り除くのも
        # `is_adding_text` を下ろすのも、鎖の**最後の**呼び出しの仕事だ
        # （`out/text_viewport.log`: 行列が 1 から 0 に減るのは `index == len(context)`
        # の呼び出しと同時）。ここを自分で真似して行列を放置した結果、次の本文が
        # 永久に出ず、消えない先頭が何度も再表示された ― 実機で踏んだ。
        before = frames.attr(self, "display_text")
        result = orig(self, dt, context, len(context))
        after = frames.attr(self, "display_text")
        if before != after and not warned:
            # 終端の index が「何も足さずに完了だけ」である保証は実測3件からの
            # 推定でしかない。外れていれば末尾が重複するので、黙って壊れないよう
            # 一度だけ残す。
            warned.append(True)
            ctx.log("batch message render: the finishing call appended text"
                    " ({} -> {} chars); the tail may be duplicated".format(
                        len(before), len(after)), level="WARN")

        reveal(self, state, context, captured)
        return result

    ctx.log("batch message render installed "
            "(REVEAL_LINE_SECONDS={}, OLD_TEXT_COLOR={}, "
            "FADE_FROM={}, FADE_SECONDS={})".format(
                REVEAL_LINE_SECONDS, OLD_TEXT_COLOR,
                FADE_FROM, FADE_SECONDS))
