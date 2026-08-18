# -*- coding: utf-8 -*-
"""本文の出し方（逐次／一括）と、読み終わった本文の灰色化。

出し方は2つ（`BATCH_MODE`）。

    click    ゲーム本来の1文字ずつの打ち出しに任せる。打っている最中に画面を
             クリックしたら、残りを一括で出して打ち終わりにする（既定）
    always   本文を一括で反映し、新着部分だけを行単位で見せる

古い本文の見分け方も2つ（`FRESH_MODE`）。
追加からの経過秒数（`FRESH_SECONDS`）か、
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

# 窓に結んだクリックの見張りを控えておく印。
# 注入し直したときに古い版の手が残らないよう、
# 結び直す前にこれで外す（`113_` の `on_resize` と同じ作法）。
WINDOW_ATTR = "_instantale_batch_render_touch"

# 「この本文はもう画面に出ているか」を見るのに使う先頭の文字数（`settle_label`）。
PROBE_CHARS = 8

# 打ち切った直後の画面を1回だけ記録する（`watch_after_skip`）。
# 原因が決まったら落とす。
# 読み取りしかしないので、付けたままでも本文には触らない。
WATCH_AFTER_SKIP = True
WATCH_MOMENTS = (0.1, 0.5, 1.5)

# 打ち切って捨てる本文の控えの数（`remember_dropped`）。
# 予約の残りは打ち切りの直後に飛んでくるので、数件あれば足りる。
MAX_DROPPED = 8

# 覚えておく本文の数。
# 照合は1文字ごとに走るので、際限なく伸ばさない。
# 画面に載る本文は `117_` が上限を掛けているので、
# これより古い本文はそもそも照合対象に残っていない。
MAX_SEGMENTS = 60

# Kivy の Label は「新しく足された部分だけ」を個別にフェードできない。
# 動かせるのはラベル全体の opacity だけなので、
# 0 から始めると読んでいる途中の本文まで一度消える。
# 新着が来たことは分かる程度に留める。
FADE_FROM = 0.4


def segment_parts(segment):
    """控えた本文を (本文, 追加時刻, セッション番号) に開く。

    注入し直しても控えは `sys` に残る（世代をまたぐ）。
    古い版が置いた2つ組が混ざっていても読めるようにしておく。
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
    # app ごとの控えではなくここに置く。
    # クリックの見張りから引ける場所が要る。
    store.setdefault("stream", None)
    # 打ち切りの最中は塗り直しを止める（`mute`）。
    # 自分で回した鎖の残りを捨てるための印（`dropped`）。
    # どちらも注入し直しをまたいで同じ辞書を使う。
    store.setdefault("mute", False)
    store.setdefault("dropped", [])
    # 新しい本文が始まった回数。
    # 打ち切りの最中に終端が次の本文を始めたことを、
    # ゲームの旗（`is_adding_text`）ではなくこれで知る。
    # 旗は次の本文ですぐ True に戻るので、
    # 「終わった」と「次が始まった」を見分けられない。
    store.setdefault("starts", 0)
    states = store["states"]
    fallback_states = store["fallback_states"]
    warned = {}     # 一度きりの警告の控え（種類ごと）

    def new_state():
        return {
            "generation": 0,
            "overlay": None,
            "markup_label": None,
            "tagged": None,
            "plain": None,
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
    def compact(text):
        """空白を除いた文字列と、元の位置の対応表を返す。

        控えた本文をそのまま探してはいけない。
        ゲームは打ち出しの最中に本文には無い改行を混ぜるので、
        見つかる本文と見つからない本文が混ざり、
        色が白・灰・白・灰と交互になる（実機で出た）。
        空白を落として突き合わせ、位置だけ元に戻せば、
        どちらの形でも同じ1本の本文として扱える。
        """
        chars = []
        positions = []
        for index, char in enumerate(text):
            if char.isspace():
                continue
            chars.append(char)
            positions.append(index)
        return "".join(chars), positions

    def build_markup(state, shown):
        """`shown` を色付きの markup に組み直す。

        戻り値は次の4つ。
        `tail` は「控えのどれにも当たらない末尾」＝いま打っている最中の本文で、
        そこは触らずに白のまま残す。

            head        `shown[:tail]` の色付き表現
            tail        末尾の開始位置
            has_old     灰色にした部分があるか
            fresh_start 白いままの部分の先頭（一括表示のときの新着位置）
        """
        packed, positions = compact(shown)
        visible_segments = []
        claimed = []
        for segment in reversed(state["segments"]):
            text, added_at, session = segment_parts(segment)
            key = "".join(text.split())
            if not key:
                continue
            start = packed.rfind(key)
            end = start + len(key)
            while start >= 0 and any(
                    start < claimed_end and end > claimed_start
                    for claimed_start, claimed_end in claimed):
                start = packed.rfind(key, 0, start)
                end = start + len(key)
            if start < 0:
                continue
            claimed.append((start, end))
            # 位置は元の本文に戻す。
            # 中に挟まっている改行はその本文のものとして一緒に色を付ける（間に挟まる空白だけが「どの本文でもない」）。
            visible_segments.append(
                (positions[start], positions[end - 1] + 1, added_at, session))
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
                gap = shown[position:start]
                parts.append("[color={}]{}[/color]".format(
                    OLD_TEXT_COLOR, escape_markup(gap)))
                # 本文と本文の間の空白だけの隙間は、灰色にしても何も見えない。
                # それで markup を立てると、
                # 色を変える必要が無い場面でラベルを組み直すことになるので数に入れない。
                if gap.strip():
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
                remember_markup(state, label, text, shown)
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
        毎回すべてを照合すると1文字ぶんの手間がそこに乗るため、**前の部分の色は本文1つにつき1度だけ組み立てて控える**。
        伸びるのは末尾だけなので、以降は控えた色付きの前半と、
        逃がした末尾を繋ぐだけで済む。
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
                # 灰色にするものが無い＝生の本文をそのまま出してよい。
                # markup を落としておかないと、本文中の `[` を書式として食われる。
                drop_markup(state, label)
                state["stream_cache"] = None
                return
            cache = {
                "label": label,
                "prefix": shown[:built["tail"]],
                "head": built["head"],
            }
            state["stream_cache"] = cache
        label.markup = True
        tagged = cache["head"] + escape_markup(shown[len(cache["prefix"]):])
        label.text = tagged
        remember_markup(state, label, tagged, shown)

    def remember_markup(state, label, tagged, plain):
        """いま書いた色付きの文字列と、その素の形を控える。

        色を外すときに素の形へ戻すために要る（`drop_markup`）。
        """
        state["markup_label"] = label
        state["tagged"] = tagged
        state["plain"] = plain

    def drop_markup(state, label):
        """こちらが付けた色を外す。素の本文に戻してから markup を落とす。

        タグの付いた文字列を残したまま markup だけ落とすと、
        ゲームが本文を塗り直すまでの間、`[color=#808080]` がそのまま画面に出る（実機で見えた。
        場面転換のときに一瞬タグが出る）。
        塗り直しがいつ来るかはこちらの都合では決まらないので、その瞬間を作らない。
        """
        if state.get("markup_label") is not label:
            return
        try:
            if (frames.attr(label, "text") == state.get("tagged")
                    and isinstance(state.get("plain"), str)):
                label.text = state["plain"]
            label.markup = False
        except Exception:
            ctx.log_exc("batch message render: could not reset text markup")
        state["markup_label"] = None
        state["tagged"] = None
        state["plain"] = None

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
            # ここで例外を出すとゲームごと落ちる。
            # 実際に落とした（`out/live_crashes.log`: 存在しない
            # `ui.hud_of` を呼んでいた）。
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

        # 後始末はゲームにやらせる。
        # 待ち行列 `to_add_text_list` から取り除くのも
        # `is_adding_text` を下ろすのも、鎖の最後の呼び出しの仕事だ（`out/text_viewport.log`: 行列が 1 から 0 に減るのは
        # `index == len(context)` の呼び出しと同時）。
        # ここを自分で真似して行列を放置した結果、次の本文が永久に出ず、
        # 消えない先頭が何度も再表示された。
        # 実機で踏んだ。
        _owner, before = canonical_text(app, None)
        result = orig(app, dt, context, len(context))
        _owner, after = canonical_text(app, None)
        if before is not None and before != after and not warned.get("tail"):
            # 終端の
            # index が「何も足さずに完了だけ」である保証は実測3件からの推定でしかない。
            # 外れていれば末尾が重複するので、黙って壊れないよう一度だけ残す。
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
            "owner": None,      # 塗り直しの相手（塗り直し自身が教える）
        }
        try:
            return orig(app, dt, context, index)
        except Exception:
            store["stream"] = None
            raise

    def canonical_text(app, stream):
        """1文字ずつ伸びている本文の正本と、その持ち主を返す。

        どのオブジェクトが本文を持っているかを決めつけない。
        塗り直しの通知（`update_display_text`）に渡ってくる
        `instance` が本人なので、それを最優先で使い、
        無ければ HUD → app の順に当たる。

        `frames.attr` の既定値は文字列の `MISSING` なので、
        `isinstance(str)` だけでは「属性が無い」を弾けない。
        ここを取り違えて、実機で打ち切りが毎回見送られていた（`out/modloader.log` の
        `could not tell how much of the message is already shown`）。
        """
        owners = []
        if isinstance(stream, dict) and stream.get("owner") is not None:
            owners.append(stream["owner"])
        try:
            hud = ui.find_hud(app)
        except Exception:
            hud = None
        if hud is not None:
            owners.append(hud)
        owners.append(app)
        for owner in owners:
            value = frames.attr(owner, "display_text")
            if value is not frames.MISSING and isinstance(value, str):
                return owner, value
        return None, None

    def repaint(app, owner, text):
        """ゲーム本来の塗り直しを通す。

        本文の正本へ書けば塗り直しが走る、とは限らない。
        実機ではそれで画面が更新されず、打ちかけの数文字（NPC の名前）だけが残った（`out/modloader.log`:
        `canonical 57552 -> 57569` と書けているのに画面は 7文字のまま）。
        自分でラベルを塗らずにゲームの塗り直しを呼ぶのは、
        `117_` の切り詰めなど内側の層をそのまま通すため。
        """
        for host in (owner, ui.find_hud(app)):
            paint = frames.attr(host, "update_display_text")
            if callable(paint):
                paint(host, text)
                return True
        return False

    def settle_label(app):
        """打ち切った後にラベルの寸法を計算し直す。

        Kivy はテクスチャの作り直しを次のフレームへ回すので、
        ゲームが `update_label_height()` を呼ぶ時点の `texture_size` は1手前のもの。
        1文字ずつなら見えない遅れが、
        まとめて足すと「増えた行が枠から切り落とされる」形で出る（実機で踏んだ）。
        GAME.md §2.3 の作法どおり、`texture_update()` の後に高さを出し直す。
        """
        hud = ui.find_hud(app)
        label = frames.attr(hud, "text_display")
        if label in (None, frames.MISSING):
            return False
        update = frames.attr(label, "texture_update")
        if callable(update):
            update()
        update_height = frames.attr(hud, "update_label_height")
        if callable(update_height):
            update_height()
        return True

    def watch_after_skip(app, context):
        """打ち切った直後の数フレームを1回だけ記録する（読み取りのみ）。

        実機で「一瞬全文が出た後に消える」が出たときに足した。
        原因（正本だと思って書いていた先が写しで、
        0.1 秒後に作り直されていた）は決着済みだが、
        同じ種類の「出した後に誰かが変える」を次に疑うときの目になるので残す。
        """
        if not WATCH_AFTER_SKIP or warned.get("watch"):
            return
        warned["watch"] = True
        head = context[:PROBE_CHARS]

        def look(moment):
            def run(_dt=None):
                try:
                    hud = ui.find_hud(app)
                    label = frames.attr(hud, "text_display")
                    scroll = frames.attr(hud, "scroll_view")
                    _owner, canonical = canonical_text(app, None)
                    shown = frames.attr(label, "text", "")
                    ctx.log("batch message render: after the skip +{}s"
                            " canonical={} label={} height={} opacity={}"
                            " texture={} scroll_y={} markup={} head shown={}"
                            .format(
                                moment,
                                len(canonical) if isinstance(canonical, str) else "?",
                                len(shown) if isinstance(shown, str) else "?",
                                frames.attr(label, "height"),
                                frames.attr(label, "opacity"),
                                frames.attr(label, "texture_size"),
                                frames.attr(scroll, "scroll_y"),
                                frames.attr(label, "markup"),
                                isinstance(shown, str) and head in shown))
                except Exception:
                    ctx.log_exc("batch message render: could not look at the screen")
            return run

        for moment in WATCH_MOMENTS:
            schedule(look(moment), moment)

    # -- 打ち切って捨てる本文の控え ------------------------------------------
    # 打ち切りで自分で回した鎖には予約の残りが付いてくる。
    # それを捨てる印。
    # 1枠では足りない。
    # 終端はその場で次の本文を始めるので、
    # `finish_stream` を回している最中に新しい本文の `index == -1` が入る。
    # 1枠だと、そこでいま捨てたい印が消える。
    # 残りがゲームへ渡って終端をもう一度踏み、
    # `to_add_text_list.pop` が空の行列を叩いた（実機のクラッシュ。
    # docs/VERIFICATION.md）。
    # 本文ごとに1件持ち、落とすのはその本文が始め直されたときだけにする。
    def dropped_entries():
        # `is_dropped` は1文字ごとに通るので、
        # ここでは掃除をしない（死んだ参照を落とすのは `remember_dropped` の側）。
        entries = store.get("dropped")
        if not isinstance(entries, list):
            entries = []            # 前の版は1枠だった（注入し直しで混ざる）
            store["dropped"] = entries
        return entries

    def remember_dropped(app, context):
        entries = dropped_entries()
        forget_dropped(app, context)
        entries[:] = [entry for entry in entries if entry["app"]() is not None]
        entries.append({"app": weakref.ref(app), "context": context})
        del entries[:-MAX_DROPPED]

    def forget_dropped(app, context):
        entries = dropped_entries()
        entries[:] = [entry for entry in entries
                      if entry["context"] != context
                      or entry["app"]() is not app]

    def is_dropped(app, context):
        return any(entry["context"] == context and entry["app"]() is app
                   for entry in dropped_entries())

    def finish_stream(orig, app, dt, context, index, stream):
        """クリックされた。残りをゲーム本来の経路で一気に流す。

        本文はこちらで書かない。
        正本の名前を当てて書きにいく形は、実機で行き止まりだった。
        `hud.display_text` へ書けても 0.1 秒後には元へ戻されていた（`out/modloader.log` の `after the skip +0.1s` で
        `canonical` が書く前の長さに戻っている）。
        あれは正本ではなく写しで、ゲームは自分の控えから作り直す。

        そこで、1文字ずつの呼び出しをその場で最後まで回す。
        書くのはゲーム自身なので、どこに本文があるかを知る必要が無く、
        打ち出しの最中に足している改行もそのまま入る。
        代金は塗り直しだが、そこは止められる。
        こちらが `update_display_text` を包んでいるので、
        回している間は素通しにして、終わってから1回だけ塗る。

        鎖は1回ごとに次を予約しているので、回した後には予約が残る。
        それをゲームへ渡すと本文の終端を何度も踏む（＝次の本文が消える）ので、
        この本文の続きは捨てる印を置く（`remember_dropped`）。
        """
        remember_dropped(app, context)
        store["mute"] = True
        steps = 0
        position = index
        started = store.get("starts", 0)
        try:
            # 終わりの判定はゲームに任せる。
            # 終端の呼び出しがどの `index` なのかは実測できていないので、
            # 数え方を決め打ちにしない。
            # 合図は2つ。
            # `is_adding_text` が下りたか、
            # `starts` が動いた（＝終端がその場で次の本文を始めた）か。
            # **後者で止めないと、
            # 終わった本文の続きをもう一巡ぶん流し込んで終端を二度踏む。**
            while (position <= len(context)
                   and store.get("starts", 0) == started
                   and frames.attr(app, "is_adding_text", True) is not False):
                orig(app, dt, context, position)
                position += 1
                steps += 1
        except Exception:
            ctx.log_exc("batch message render: could not run the rest of the message")
        finally:
            store["mute"] = False

        if (store.get("starts", 0) == started
                and frames.attr(app, "is_adding_text", False) is True):
            # 鎖が終わっていない（回し方の見立てが外れた）。
            # 後始末だけ渡す。
            # 終端まで回せたならここへ来ないこと。
            # 終端はその場で次の本文を始めるので `is_adding_text` は再び
            # True になっており、旗だけを見ると打ち終わった本文の終端をもう一度踏む（＝空の行列を
            # pop する）。
            orig(app, dt, context, len(context))

        _owner, canonical = canonical_text(app, stream)
        try:
            if isinstance(canonical, str):
                repaint(app, stream.get("owner"), canonical)
            settle_label(app)
        except Exception:
            ctx.log_exc("batch message render: could not repaint after the skip")

        if not warned.get("skipped"):
            warned["skipped"] = True
            ctx.log("batch message render: skipped a message (index={},"
                    " len(context)={}, steps={}, canonical={} chars,"
                    " adding={})".format(
                        index, len(context), steps,
                        len(canonical) if isinstance(canonical, str) else "?",
                        frames.attr(app, "is_adding_text")))
        watch_after_skip(app, context)
        if store.get("stream") is stream:
            # 打ち切りの最中に次の本文が始まっていたら、そちらの帳簿が入っている。
            # 消すと次の本文が打ち切れず、色も付かなくなる。
            store["stream"] = None
        return None

    def continue_stream(orig, app, dt, context, index):
        if is_dropped(app, context):
            # 打ち切りで自分で回した鎖の残り。
            # ゲームへ渡すと終端を二度踏む。
            return None
        stream = store.get("stream")
        if (not isinstance(stream, dict) or stream["app"] is not app
                or stream["context"] != context):
            # 1文字ぶんの続きの呼び出し。
            # 注入し直した時にだけ飛んでくる（こちらが始めていない鎖）。
            # ゲームに返して終わらせる。
            return orig(app, dt, context, index)
        if type(index) is int and index >= len(context):
            store["stream"] = None      # 打ち終わり
            return orig(app, dt, context, index)
        if not stream["skip"]:
            return orig(app, dt, context, index)
        return finish_stream(orig, app, dt, context, index, stream)

    def on_screen_touch(_window, touch):
        """画面のどこかが押された。打ち出し中なら打ち切りの合図にする。

        偽を返して（何も返さないで）おくこと。
        真を返すと Kivy はここで配送を止めるので、ボタンが押せなくなる。

        ホイールは弾く。
        Kivy は回転も `on_touch_down` で配るので、
        本文を読み返そうとしてスクロールしただけで打ち切りが走っていた（押した記録がどこにも残らないので、
        原因を追うときに見えない）。
        """
        try:
            button = getattr(touch, "button", None)
            if isinstance(button, str) and button.startswith("scroll"):
                return
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
        store["starts"] = store.get("starts", 0) + 1
        store["stream"] = None      # 新しい本文が始まった＝前の鎖は終わっている
        # 落とすのはこの本文の印だけ。
        # 打ち切った本文の残りは、次の本文が始まった後に飛んでくる（終端がその場で次を始めるため）ので、
        # ここで全部落とすとその残りがゲームへ渡ってしまう。
        forget_dropped(self, context)
        if BATCH_MODE == "always" and callable(
                frames.attr(self, "add_text_immediately")):
            return show_at_once(orig, self, dt, context, state)
        return begin_stream(orig, self, dt, context, index, state)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text",
              required=False, safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        # 先にゲームに塗らせる。
        # こちらは塗り終わった後の色だけを直す（`112_` と同じ作法）。
        # 打ち出し中でなければ何もしない ＝ 一括表示のときは素通り。
        if store.get("mute"):
            # 打ち切りで1文字ずつ回している最中。
            # 塗り直しは終わってから1回だけ。
            return None
        result = orig(self, instance, value, *args, **kwargs)
        stream = store.get("stream")
        if isinstance(stream, dict):
            try:
                if instance is not None and isinstance(value, str):
                    # 本文の正本を持っているのはこの `instance`。
                    # 打ち切りの書き戻し先はここで教わる（名前で探し当てない）。
                    stream["owner"] = instance
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
