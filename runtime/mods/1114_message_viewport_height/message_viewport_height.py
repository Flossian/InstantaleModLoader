# -*- coding: utf-8 -*-
"""メッセージ表示領域を下端固定で上方向へ広げる。

実機計測（out/text_viewport.log, 2026-08-01）:

    hud.scroll_view                 size_hint=(1, 1)
    hud.scroll_view.parent          FloatLayout
      size_hint=(0.53, 0.62)  pos_hint={'center_x': .5, 'y': .36}

親 FloatLayout の `y` が下端を決め、`size_hint_y` が高さを決めている。通常時は
倍率で広げる。全画面時は Window 座標へ変換した下端を基準にし、Window の上端から
10px を残す固定ピクセル高にする。

右上のベクターアイコンは通常倍率と 3.0 倍を切り替える。背景は本文を薄くせず、
ScrollView の canvas.before だけに不透明度 0.5 の黒を描く。
"""

import sys

from instantale_modloader import frames

HEIGHT_SCALE = 1.5
FULL_HEIGHT_SCALE = 3.0
FULL_TOP_MARGIN = 10
BACKGROUND_OPACITY = 0.5
DESIGN_ATTR = "__instantale_message_viewport_design__"
FULL_ATTR = "__instantale_message_viewport_full__"
BACKGROUND_ATTR = "__instantale_message_viewport_background__"
BUTTON_ATTR = "__instantale_message_viewport_toggle__"
ICON_ATTR = "__instantale_message_viewport_icon__"


def apply(ctx):
    new_hud = sys.modules.get("scripts.hud.new_hud")
    if new_hud is None or getattr(new_hud, "InstanTaleHUD", None) is None:
        ctx.log("message viewport height: InstanTaleHUD not loaded; skipping",
                level="WARN")
        return

    def viewport(hud):
        scroll = frames.attr(hud, "scroll_view")
        if scroll in (None, frames.MISSING):
            return None
        parent = frames.attr(scroll, "parent")
        if parent in (None, frames.MISSING):
            return None
        hint = frames.attr(parent, "size_hint")
        position = frames.attr(parent, "pos_hint")
        if (not isinstance(hint, (list, tuple)) or len(hint) != 2
                or not isinstance(position, dict)
                or not isinstance(position.get("y"), (int, float))):
            return None
        if (hint[1] is not None
                and (not isinstance(hint[1], (int, float)) or hint[1] <= 0)):
            return None
        return parent

    def design_of(widget):
        design = frames.attr(widget, DESIGN_ATTR)
        hint = frames.attr(widget, "size_hint")
        position = frames.attr(widget, "pos_hint")
        if not isinstance(hint, (list, tuple)) or len(hint) != 2:
            return None
        if not isinstance(position, dict):
            return None

        current_y = hint[1]
        if (isinstance(design, dict)
                and (current_y == design.get("applied_hint_y")
                     or current_y is None)):
            return design

        design = {
            "hint_x": hint[0],
            "hint_y": current_y,
            "pos_hint": dict(position),
            "applied_hint_y": None,
        }
        try:
            setattr(widget, DESIGN_ATTR, design)
        except Exception:
            ctx.log_exc("message viewport height: cannot retain design size")
        return design

    def full_height(widget):
        return bool(frames.attr(widget, FULL_ATTR, False))

    def active_scale(widget):
        return FULL_HEIGHT_SCALE if full_height(widget) else float(HEIGHT_SCALE)

    def full_height_pixels(widget):
        try:
            from kivy.core.window import Window
            parent = frames.attr(widget, "parent")
            position = frames.attr(widget, "pos_hint")
            parent_height = frames.attr(parent, "height")
            to_window = frames.attr(parent, "to_window")
            if (isinstance(position, dict)
                    and isinstance(position.get("y"), (int, float))
                    and isinstance(parent_height, (int, float))
                    and callable(to_window)):
                _x, parent_bottom = to_window(0, 0)
                bottom = parent_bottom + parent_height * position["y"]
            else:
                bottom = frames.attr(widget, "y")
            if not isinstance(bottom, (int, float)):
                return None
            return max(0, Window.height - bottom - FULL_TOP_MARGIN)
        except Exception:
            ctx.log_exc("message viewport height: cannot calculate fullscreen height")
            return None

    def add_background(scroll):
        background = frames.attr(scroll, BACKGROUND_ATTR)
        if isinstance(background, dict):
            background["color"].rgba = (0, 0, 0, BACKGROUND_OPACITY)
            return
        try:
            from kivy.graphics import Color, Rectangle
            with scroll.canvas.before:
                color = Color(0, 0, 0, BACKGROUND_OPACITY)
                rectangle = Rectangle(pos=scroll.pos, size=scroll.size)

            def sync(instance, _value):
                rectangle.pos = scroll.pos
                rectangle.size = scroll.size

            scroll.bind(pos=sync, size=sync)
            setattr(scroll, BACKGROUND_ATTR, {
                "color": color,
                "rectangle": rectangle,
                "sync": sync,
            })
        except Exception:
            ctx.log_exc("message viewport height: cannot add background")

    def icon_points(button, collapsed):
        x, y = button.x, button.y
        outer, inner = 5, 11
        if collapsed:
            return (
                (x + outer, y + button.height - inner,
                 x + inner, y + button.height - inner,
                 x + inner, y + button.height - outer),
                (x + button.width - inner, y + button.height - outer,
                 x + button.width - inner, y + button.height - inner,
                 x + button.width - outer, y + button.height - inner),
                (x + outer, y + inner,
                 x + inner, y + inner,
                 x + inner, y + outer),
                (x + button.width - inner, y + outer,
                 x + button.width - inner, y + inner,
                 x + button.width - outer, y + inner),
            )
        return (
            (x + inner, y + button.height - outer,
             x + outer, y + button.height - outer,
             x + outer, y + button.height - inner),
            (x + button.width - outer, y + button.height - inner,
             x + button.width - outer, y + button.height - outer,
             x + button.width - inner, y + button.height - outer),
            (x + inner, y + outer,
             x + outer, y + outer,
             x + outer, y + inner),
            (x + button.width - outer, y + inner,
             x + button.width - outer, y + outer,
             x + button.width - inner, y + outer),
        )

    def add_icon(button):
        icon = frames.attr(button, ICON_ATTR)
        if isinstance(icon, dict):
            return icon
        try:
            from kivy.graphics import Color, Line
            with button.canvas.after:
                color = Color(1, 1, 1, 1)
                lines = [Line(points=points, width=1.5)
                         for points in icon_points(button, False)]

            def refresh(instance, _value=None):
                for line, points in zip(lines, icon_points(instance,
                                                            full_height(instance.parent))):
                    line.points = points

            button.bind(pos=refresh, size=refresh)
            icon = {"color": color, "lines": lines, "refresh": refresh}
            setattr(button, ICON_ATTR, icon)
            return icon
        except Exception:
            ctx.log_exc("message viewport height: cannot draw toggle icon")
            return None

    def refresh_icon(button):
        icon = frames.attr(button, ICON_ATTR)
        if isinstance(icon, dict):
            icon["refresh"](button)

    def add_toggle(hud, widget):
        button = frames.attr(widget, BUTTON_ATTR)
        if button not in (None, frames.MISSING):
            return button
        try:
            from kivy.uix.button import Button
            button = Button(
                size_hint=(None, None),
                size=(28, 28),
                pos_hint={"right": 0.985, "top": 0.985},
                background_normal="",
                background_down="",
                background_color=(0, 0, 0, 0.45),
            )

            def toggle(_button):
                try:
                    setattr(widget, FULL_ATTR, not full_height(widget))
                    resize(hud)
                    refresh_icon(button)
                except Exception:
                    ctx.log_exc("message viewport height: cannot toggle full height")

            button.bind(on_release=toggle)
            widget.add_widget(button)
            setattr(widget, BUTTON_ATTR, button)
            add_icon(button)
            return button
        except Exception:
            ctx.log_exc("message viewport height: cannot add toggle")
            return None

    def resize(hud):
        widget = viewport(hud)
        if widget is None:
            return
        design = design_of(widget)
        if design is None:
            return

        target_height = full_height_pixels(widget) if full_height(widget) else None
        if target_height is None:
            scaled_y = design["hint_y"] * active_scale(widget)
            widget.size_hint = (design["hint_x"], scaled_y)
            design["applied_hint_y"] = scaled_y
        else:
            widget.size_hint = (design["hint_x"], None)
            widget.height = target_height
            design["applied_hint_y"] = None
        widget.pos_hint = dict(design["pos_hint"])
        add_background(frames.attr(hud, "scroll_view"))
        button = add_toggle(hud, widget)
        if button is not None:
            refresh_icon(button)

    def watch(target):
        @ctx.wrap(target, safe=True)
        def wrapped(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                resize(self)
            except Exception:
                ctx.log_exc("message viewport height: cannot resize after {}".format(
                    target.rsplit(".", 1)[-1]))
            return result
        return wrapped

    watch("scripts.hud.new_hud:InstanTaleHUD.update_text_display_size")
    watch("scripts.hud.new_hud:InstanTaleHUD._on_scroll_resize")
    watch("scripts.hud.new_hud:InstanTaleHUD._on_text_input_layout_resize")
    watch("scripts.hud.new_hud:InstanTaleHUD.update_display_text")
    ctx.log("message viewport height installed (HEIGHT_SCALE={}, FULL_HEIGHT_SCALE={})"
            .format(HEIGHT_SCALE, FULL_HEIGHT_SCALE))
