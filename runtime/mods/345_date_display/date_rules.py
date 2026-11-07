# -*- coding: utf-8 -*-
"""暦の計算と、能力値の欄に収める字の大きさの決め方。

ローダにもゲームにも Kivy にも依存しない素の Python（`345_date_display` の本体と設定画面が共有する）。
"""
import math

#: 素の暦。ゲームの訓練の1年（`elapse_days(365)`。GAME.md §2.17）に合わせた 365 日。
DEFAULT_MONTHS = "31,28,31,30,31,30,31,31,30,31,30,31"
DEFAULT_FORMAT = "Date:{year}年{month}{day}日"

MAX_MONTHS = 100
MAX_DAYS_IN_MONTH = 99999

#: 名前に使えない字。`MONTHS` の区切りと、改行（欄の行が増える）。
_SEPARATORS = (",", "，", ":", "：", "\n", "\r")

#: 行間の下限。字の高さ 28.3px（`font_size=20`）に対して 23px。字の大きさ 20px は重ならない。
MIN_LINE_HEIGHT = 0.8
#: 行間を下限まで詰めても入らないときに試す字の大きさの倍率。
FONT_SCALES = (1.0, 0.9, 0.8, 0.7)


class _SafeDict(dict):
    """書式に無い名前が来ても落とさない（打ち間違いは `{typo}` のまま画面に見える）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def clean_name(name):
    """月の名前から区切りの字を除く。"""
    text = str(name or "")
    for mark in _SEPARATORS:
        text = text.replace(mark, "")
    return text.strip()


def parse_months(text):
    """`31,28,霜月:31` を `[(名前, 日数), …]` に。読めない欄が1つでもあれば None。"""
    if not isinstance(text, str) or not text.strip():
        return None
    months = []
    for part in text.replace("，", ",").replace("：", ":").split(","):
        name, _mark, days = part.rpartition(":")
        try:
            days = int(days.strip())
        except ValueError:
            return None
        if not 1 <= days <= MAX_DAYS_IN_MONTH:
            return None
        months.append((clean_name(name), days))
    return months if len(months) <= MAX_MONTHS else None


def format_months(months):
    """`parse_months` の逆。名前の無い月は日数だけ。"""
    parts = []
    for name, days in months:
        name = clean_name(name)
        parts.append("{}:{}".format(name, int(days)) if name else str(int(days)))
    return ",".join(parts)


def distribute(total, count):
    """`total` 日を `count` か月に均等に分ける。余りは前の月から1日ずつ。"""
    base, extra = divmod(int(total), int(count))
    return [base + (1 if index < extra else 0) for index in range(int(count))]


def month_label(index, name):
    """月の呼び名。名前が無ければ番号（`3月`）。`index` は 0 から。"""
    return name or "{}月".format(int(index) + 1)


def date_of(elapsed, months, start_year):
    """経過日数を暦に直す。`{year, month_index, month_name, day, day_of_year}`。経過 0 日が開始年の1月1日。"""
    year_days = sum(days for _name, days in months)
    years, rest = divmod(max(0, int(elapsed)), year_days)
    day_of_year = rest + 1
    for index, (name, days) in enumerate(months):
        if rest < days:
            return {"year": int(start_year) + years, "month_index": index,
                    "month_name": name, "day": rest + 1, "day_of_year": day_of_year}
        rest -= days
    raise AssertionError("the remainder is smaller than the year")


def format_date(template, elapsed, months, start_year):
    """画面の1行を組む。`template` が壊れていれば既定の書式で組む。"""
    found = date_of(elapsed, months, start_year)
    values = _SafeDict(
        year=found["year"], month=month_label(found["month_index"], found["month_name"]),
        month_number=found["month_index"] + 1, day=found["day"],
        day_of_year=found["day_of_year"], elapsed=max(0, int(elapsed)))
    try:
        return str(template).format_map(values)
    except (ValueError, IndexError, KeyError, AttributeError):
        return DEFAULT_FORMAT.format_map(values)


def with_date(status_text, date_line, mark="Location:"):
    """能力値の欄の文字列の `mark` の行の下に1行足す。`mark` の行が無い・足す行が空なら None。"""
    if not isinstance(status_text, str) or not date_line:
        return None
    lines = status_text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith(mark):
            return "\n".join(lines[:index + 1] + [date_line] + lines[index + 1:])
    return None


def choose_fit(measure, available, font_size, line_height):
    """`available` の高さに収まる `(字の大きさ, 行間)` を選ぶ。

    `measure(字の大きさ, 行間)` は `(行数, 1行の高さ)` を返す。
    行間は折り返しを変えないので先に詰め、下限でも入らないときだけ字を小さくする（字を小さくすると折り返しが変わる）。
    どれでも入らなければ、いちばん小さい組を返す。
    """
    floor = min(float(line_height), MIN_LINE_HEIGHT)
    for scale in FONT_SCALES:
        font = font_size if scale == 1.0 else font_size * scale
        rows, row_height = measure(font, line_height)
        if rows * row_height <= available:
            return font, line_height
        wanted = math.floor(line_height * available / (rows * row_height) * 100) / 100.0
        # `line_height` と1行の高さの関係は丸めが入るので、実際に測って確かめる。
        while wanted >= floor:
            _rows, row_height = measure(font, wanted)
            if rows * row_height <= available:
                return font, wanted
            wanted = round(wanted - 0.01, 2)
    return font_size * FONT_SCALES[-1], floor
