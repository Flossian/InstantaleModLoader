# -*- coding: utf-8 -*-
"""画面左下の能力値の欄の、Location の下に日付を出す。

能力値の欄は改行で区切った1本の文字列で、ゲームが見張り（`update_status_texts`）に渡して塗る（GAME.md §2.29）。
その文字列の Location の行の下に1行足す。Location が折り返しても、日付は必ずその下に来る。

欄は8行ほどでアイコンの上端に届き、箱の高さを超えた行はラベルが切り捨てる（GAME.md §2.29）。
入らないときはラベルの行間を詰め、下限でも入らなければ字を小さくする。

`status_texts` は中身が変わったときしか見張りを呼ばないので、日数の後とロードの後は見張りを直に呼ぶ。
"""

import os
import sys

from instantale_modloader import durations, frames, loads, state as state_api, ui

from . import date_rules as rules

LOG_BASENAME = "date_display.log"

# 設定（既定値。`mod.json` の "settings" が同じ値を宣言している）
START_YEAR = 1
MONTHS = "31,28,31,30,31,30,31,31,30,31,30,31"
FORMAT = "Date:{year}年{month}{day}日"

# ワールドごとの設定を置く場所。設定画面（tool.py）だけが書き、ゲーム中は読む。
STATE_DIRNAME = "date_display"
STATE_STORE_ATTR = "_instantale_date_display_world_store"

HUD_TARGET = "scripts.hud.new_hud:InstanTaleHUD.update_status_texts"
HUD_STATUS_LABEL = "status_label"
HUD_ICONS = "icon_buttons"

#: ラベルに控える `{設計の字, 設計の行間, 当てた字, 当てた行間, 鍵, 測り直す関数}`。
FIT_ATTR = ui.MOD_WIDGET_PREFIX + "date_fit"

#: Location の行に1度も当たらないまま何回来たら「形が違う」と言うか。組み上がる前は空文字列でも呼ばれる。
MISS_LIMIT = 50


def icons_top(hud, label):
    """欄の中で字に使える下端。アイコンの上端。アイコンが読めなければラベルの下端。"""
    tops = []
    for button in frames.attr(hud, HUD_ICONS, None) or ():
        top = frames.attr(button, "top", None)
        if isinstance(top, (int, float)):
            tops.append(top)
    return max(tops) if tops else label.y


def measure_on(label, text):
    """ゲームのラベルと同じ条件で組み、`(行数, 1行の高さ)` を返す関数を作る。

    ラベル自身は箱の高さを超えた行を捨てるので、行数を読めない（GAME.md §2.29）。
    高さの制限を外した別の組版で数える。
    """
    from kivy.core.text import Label as CoreLabel

    def measure(font_size, line_height):
        core = CoreLabel(text=text, font_name=label.font_name, font_size=font_size,
                         line_height=line_height, halign=label.halign, valign=label.valign,
                         strip=label.strip, text_size=(label.width, None))
        core.render(real=False)
        lines = core._cached_lines
        return len(lines), max((line.h for line in lines), default=0)

    return measure


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="date display:")
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "345_date_display"
    schedule = ui.scheduler(ctx, "date display")

    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, state_api.WorldStore):
        store = state_api.WorldStore(ctx, STATE_DIRNAME, default=dict, write=write)
        setattr(sys, STATE_STORE_ATTR, store)
    store.rebind(ctx, write)

    def months_from(text):
        months = rules.parse_months(text)
        if months is None:
            write("WARN the months {!r} cannot be read; using {!r}".format(text, rules.DEFAULT_MONTHS))
            return rules.parse_months(rules.DEFAULT_MONTHS)
        return months

    calendar = {"world": object(), "start_year": START_YEAR, "months": months_from(MONTHS),
                "format": FORMAT}
    seen = {"misses": 0, "hit": False, "line": None, "unpainted": False}

    def refresh_world(app):
        """世界が変わっていれば個別設定を読み直す。無い項目は一括設定のまま。"""
        world = state_api.world_key(app)
        if world == calendar["world"]:
            return
        record = (store.load(world, fresh=True)
                  if isinstance(world, str) and world != state_api.UNKNOWN_WORLD else {})
        year = record.get("START_YEAR")
        months = record.get("MONTHS")
        template = record.get("FORMAT")
        calendar.update(
            world=world,
            start_year=year if isinstance(year, int) and not isinstance(year, bool) else START_YEAR,
            months=months_from(months if isinstance(months, str) else MONTHS),
            format=template if isinstance(template, str) else FORMAT)
        write("world {!r}: start year {}, {} month(s), {} day(s) a year, format {!r}".format(
            world, calendar["start_year"], len(calendar["months"]),
            sum(days for _name, days in calendar["months"]), calendar["format"]))

    def date_line(app):
        """いまの日付の1行。日数が読めなければ None。"""
        elapsed = ui.game_day(app)
        if elapsed is None:
            return None
        refresh_world(app)
        return rules.format_date(calendar["format"], elapsed, calendar["months"], calendar["start_year"])

    def fit(hud):
        """字がアイコンに掛からない行間と字の大きさにする。"""
        label = frames.attr(hud, HUD_STATUS_LABEL, None)
        text = frames.text_of(label) if label is not None else None
        if not text:
            return
        memo = frames.attr(label, FIT_ATTR, None)
        if not isinstance(memo, dict):
            memo = {"design_font": label.font_size, "design_line": label.line_height,
                    "font": label.font_size, "line": label.line_height, "key": None, "refit": None}
            setattr(label, FIT_ATTR, memo)
            # 窓の大きさが変わると欄の高さが変わる。`refit` は注入し直すと新しい世代の関数に替わる。
            label.bind(size=lambda *_args: memo["refit"] and memo["refit"](),
                       pos=lambda *_args: memo["refit"] and memo["refit"]())
        memo["refit"] = lambda: schedule(lambda: fit(hud))
        # 当てた値から動いていれば、ゲームが決め直した値
        if label.font_size != memo["font"]:
            memo["design_font"] = label.font_size
        if label.line_height != memo["line"]:
            memo["design_line"] = label.line_height
        available = label.top - icons_top(hud, label)
        key = (text, label.width, available, memo["design_font"], memo["design_line"])
        if key == memo["key"] or available <= 0:
            return
        memo["key"] = key
        font, line = rules.choose_fit(measure_on(label, text), available,
                                      memo["design_font"], memo["design_line"])
        if (font, line) != (memo["font"], memo["line"]):
            write("fit: font {} -> {}, line height {} -> {} ({:.0f}px)".format(
                memo["font"], font, memo["line"], line, available))
        memo["font"], memo["line"] = font, line
        label.font_size = font
        label.line_height = line

    @ctx.wrap(HUD_TARGET, safe=True)
    def update_status_texts(orig, self, *args, **kwargs):
        # (instance, value)。受け取った形のまま渡し、`value` だけを来た側に合わせて差し替える。
        app = ui.find_app()
        line = date_line(app) if app is not None else None
        fixed = rules.with_date(frames.arg(args, kwargs, "value", 1), line)
        if fixed is None:
            seen["misses"] += 1
            if not seen["hit"] and seen["misses"] == MISS_LIMIT:
                write("WARN no Location line in {} status update(s); no date is shown".format(MISS_LIMIT))
            return orig(self, *args, **kwargs)
        seen["hit"] = True
        if line != seen["line"]:
            seen["line"] = line
            write("date: {!r} (day {})".format(line, ui.game_day(app)))
        args, kwargs, _written = frames.replace_arg(args, kwargs, "value", 1, fixed)
        result = orig(self, *args, **kwargs)
        # 見張りが `value` を塗らないビルドへの保険（GAME.md §2.29）
        label = frames.attr(self, HUD_STATUS_LABEL, None)
        shown = frames.text_of(label) if label is not None else None
        if shown is not None and line not in shown:
            if not seen["unpainted"]:
                seen["unpainted"] = True
                write("WARN the status label did not take the value; writing the label directly")
            label.text = fixed
        schedule(lambda: fit(self))
        return result

    def repaint(app):
        """見張りを直に呼ぶ（包みを全部通る）。"""
        hud = ui.find_hud(app)
        text = frames.attr(hud, "status_texts", None) if hud is not None else None
        update = frames.attr(hud, "update_status_texts", None) if hud is not None else None
        if isinstance(text, str) and callable(update):
            update(hud, text)

    def after_days(call):
        if call.moved:
            schedule(lambda: repaint(call.app))

    def after_load(call):
        # 同じ世界を開き直したときも、設定画面で書いた個別設定を読み直す。
        calendar["world"] = object()
        schedule(lambda: repaint(call.app))

    durations.on_days(owner, ctx, after=after_days, write=write)
    loads.on(owner, ctx, (loads.LOAD, loads.NEW_GAME), after=after_load, write=write)
    # 当て直しの間は包みが剥がれ、その間の塗りには日付が付かない（VERIFICATION.md §3.125）。
    # ロードの後にも当て直しは起きるので、包み終えたら塗り直す。
    app_now = ui.find_app()
    if app_now is not None:
        schedule(lambda: repaint(app_now))
    ctx.log("date display: start year {}, {} month(s), format {!r} (log={})".format(
        START_YEAR, len(calendar["months"]), FORMAT, ctx.out_path(LOG_BASENAME)))
