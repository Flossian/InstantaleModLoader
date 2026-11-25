# -*- coding: utf-8 -*-
"""画面左下の能力値の欄の、Location の下に日付を出す。

能力値の欄は改行で区切った1本の文字列で、ゲームが見張り（`update_status_texts`）に渡して塗る（GAME.md §2.29）。
その文字列の Location の行の下に1行足す。Location が折り返しても、日付は必ずその下に来る。

欄は8行ほどでアイコンの上端に届き、箱の高さを超えた行はラベルが切り捨てる（GAME.md §2.29）。
入らないときはラベルの行間を詰め、下限でも入らなければ字を小さくする。

`status_texts` は中身が変わったときしか見張りを呼ばないので、日数の後とロードの後は見張りを直に呼ぶ。

見張りに渡る文字列の書き換えはローダの窓口 `status_text`（TECH.md §3.3.21）に預け、
ワールドごとの設定の読みはローダの `state.WorldSettings`（TECH.md §3.12.1）に任せる。
"""

from instantale_modloader import durations, frames, loads, state as state_api, status_text, ui

from . import date_rules as rules

LOG_BASENAME = "date_display.log"

# 設定（既定値。`mod.json` の "settings" が同じ値を宣言している）
START_YEAR = 1
MONTHS = "31,28,31,30,31,30,31,31,30,31,30,31"
FORMAT = "Date:{year}年{month}{day}日"

# ワールドごとの設定を置く場所。設定画面（tool.py）だけが書き、ゲーム中は読む。
STATE_DIRNAME = "date_display"
#: 世界ごとに変えられる設定。
WORLD_SETTINGS = ("START_YEAR", "MONTHS", "FORMAT")

#: 窓口 `status_text` で書き換える順（小さいほど先）。前は `333_` / `130_` より外側で、最初に書き換えていた。
STATUS_ORDER = 10

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
        if frames.is_number(top):
            tops.append(top)
    return max(tops) if tops else label.y


def measure_on(label, text):
    """ゲームのラベルと同じ条件で組み、`(行数, 1行の高さ)` を返す関数を作る。

    ラベル自身は箱の高さを超えた行を捨てるので、行数を読めない（GAME.md §2.29）。
    高さの制限を外した別の組版で数える（ローダの `ui.text_lines`。ラベルの幅で折り返す）。
    測れなければ投げる（`fit` は予約の中で走るので、`ui.scheduler` が記録する）。
    """
    def measure(font_size, line_height):
        found = ui.text_lines(label, text, label.width, font_size=font_size, line_height=line_height)
        if found is None:
            raise RuntimeError("date display: cannot measure the status label")
        return found

    return measure


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="date display:")
    owner = ctx.mod_name or "345_date_display"
    schedule = ui.scheduler(ctx, "date display")

    def months_from(text):
        months = rules.parse_months(text)
        if months is None:
            write("WARN the months {!r} cannot be read; using {!r}".format(text, rules.DEFAULT_MONTHS))
            return rules.parse_months(rules.DEFAULT_MONTHS)
        return months

    def acceptable(name, value, _base):
        """項目ごとの型。開始年は真偽値でない int、月と書式は文字列（前の読み方と同じ）。"""
        if name == "START_YEAR":
            return isinstance(value, int) and not isinstance(value, bool)
        return isinstance(value, str)

    # 世界が変わったときだけ読む（`follow_file=False`）。同じ世界を開き直したときは `after_load` が忘れさせる。
    settings = state_api.WorldSettings(ctx, STATE_DIRNAME, WORLD_SETTINGS, globals(), write=write,
                                       check=acceptable, follow_file=False)
    calendar = {"start_year": START_YEAR, "months": months_from(MONTHS), "format": FORMAT}
    seen = {"misses": 0, "hit": False, "line": None, "fit_due": False}

    def refresh_world(app):
        """世界が変わっていれば個別設定を読み直す。無い項目は一括設定のまま。"""
        world = settings.world(app)

        def take(values):
            calendar.update(start_year=values["START_YEAR"], months=months_from(values["MONTHS"]),
                            format=values["FORMAT"])
            write("world {!r}: start year {}, {} month(s), {} day(s) a year, format {!r}".format(
                world, calendar["start_year"], len(calendar["months"]),
                sum(days for _name, days in calendar["months"]), calendar["format"]))

        settings.refresh(app, take)

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

    def with_date(text, app):
        """窓口 `status_text` から、見張りに渡る文字列を受け取る。Location の行の下に日付を足した文字列を返す。"""
        # 印は呼ばれるたびに下ろす。orig が投げて `painted` が来なかった回の印を、次の日付の無い回に持ち越さない。
        seen["fit_due"] = False
        line = date_line(app) if app is not None else None
        fixed = rules.with_date(text, line)
        if fixed is None:
            seen["misses"] += 1
            if not seen["hit"] and seen["misses"] == MISS_LIMIT:
                write("WARN no Location line in {} status update(s); no date is shown".format(MISS_LIMIT))
            return None
        seen["hit"] = True
        seen["fit_due"] = True
        if line != seen["line"]:
            seen["line"] = line
            write("date: {!r} (day {})".format(line, ui.game_day(app)))
        return fixed

    def painted(call):
        # 日付を足した回だけ、塗った後に収まりを見る。
        # 見張りが `value` を塗らないビルドへの保険（GAME.md §2.29）は窓口が受け持つ。
        if seen["fit_due"]:
            seen["fit_due"] = False
            hud = call.obj
            schedule(lambda: fit(hud))

    status_text.on(owner, ctx, with_date, order=STATUS_ORDER, after=painted, write=write)

    def repaint(app):
        """見張りを直に呼ぶ（書き換えを全部通る）。例外は窓口が記録する。"""
        status_text.repaint(app)

    def after_days(call):
        if call.moved:
            schedule(lambda: repaint(call.app))

    def after_load(call):
        # 同じ世界を開き直したときも、設定画面で書いた個別設定を読み直す。
        settings.forget()
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
