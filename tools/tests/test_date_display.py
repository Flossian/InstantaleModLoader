# -*- coding: utf-8 -*-
"""現在地の下に日付を表示する（`345_date_display`）。

暦の計算（年・月の繰り上がり、壊れた設定）、Location の行の下に足すこと、
欄に収める字の大きさの決め方（行間を先に詰め、足りなければ字を小さくする）、
見張りの包みが値を差し替えて、日数の後は直に呼び直すことを見る。
"""
import importlib
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "345_date_display")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import durations, loads, state as state_api, ui  # noqa: E402

# フォルダ名が数字で始まるので、パッケージの名前を付け直して読む（本体は相対 import で同居のファイルを読む）。
package = types.ModuleType("date_display_pkg")
package.__path__ = [MOD_DIR]
sys.modules["date_display_pkg"] = package
rules = importlib.import_module("date_display_pkg.date_rules")
mod = importlib.import_module("date_display_pkg.date_display")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  {!r}".format(detail)))
    if not cond:
        failures.append(name)


# ------------------------------------------------------------------ 暦
PLAIN = rules.parse_months(rules.DEFAULT_MONTHS)
check("the default year has 365 days", sum(d for _n, d in PLAIN) == 365)
check("day 0 is the first day of the start year",
      rules.format_date(rules.DEFAULT_FORMAT, 0, PLAIN, 1) == "Date:1年1月1日")
check("the last day of January", rules.format_date("{month}{day}", 30, PLAIN, 1) == "1月31")
check("the first day of February", rules.format_date("{month}{day}", 31, PLAIN, 1) == "2月1")
check("the last day of the year", rules.format_date("{year}/{month_number}/{day}", 364, PLAIN, 1) == "1/12/31")
check("the year rolls over", rules.format_date("{year}/{month_number}/{day}", 365, PLAIN, 1) == "2/1/1")
check("the start year is added", rules.format_date("{year}", 730, PLAIN, 1000) == "1002")
check("a negative day stays on the first day", rules.format_date("{month}{day}", -5, PLAIN, 1) == "1月1")

NAMED = rules.parse_months("霜月:10,5,雪月:15")
check("names and bare numbers mix", NAMED == [("霜月", 10), ("", 5), ("雪月", 15)], NAMED)
check("a named month shows its name", rules.format_date("{month}{day}", 0, NAMED, 1) == "霜月1")
check("an unnamed month shows its number", rules.format_date("{month}{day}", 10, NAMED, 1) == "2月1")
check("the elapsed and day-of-year names",
      rules.format_date("{elapsed}/{day_of_year}", 32, NAMED, 1) == "32/3")
check("a typo stays visible", rules.format_date("{typo}{year}", 0, PLAIN, 1) == "{typo}1")
check("a broken format falls back", rules.format_date("{", 0, PLAIN, 1) == "Date:1年1月1日")
check("full-width separators are read", rules.parse_months("霜月：10，5") == [("霜月", 10), ("", 5)])
for broken in ("", "a,b", "0", "31,-1", "31,,31", None, ",".join(["1"] * (rules.MAX_MONTHS + 1))):
    check("broken months are refused: {!r}".format(broken and broken[:12]), rules.parse_months(broken) is None)
check("the format and the parser agree",
      rules.parse_months(rules.format_months(NAMED)) == NAMED)
check("separators leave the names",
      rules.format_months([("霜,月:", 3)]) == "霜月:3")
check("days are spread with the rest in front", rules.distribute(365, 12)[:6] == [31] * 5 + [30])
check("the spread keeps the total", sum(rules.distribute(1000, 13)) == 1000)

# ------------------------------------------------------------------ Location の下
STATUS = "Atk:1\nGold:2\nLocation:土地F61"
check("the date goes under the location",
      rules.with_date(STATUS, "Date:x") == STATUS + "\nDate:x")
check("a later line stays under the date",
      rules.with_date("Location:a\nOther:b", "Date:x") == "Location:a\nDate:x\nOther:b")
check("no location line, no date", rules.with_date("", "Date:x") is None)
check("an empty date line adds nothing", rules.with_date(STATUS, "") is None)
check("no text, no date", rules.with_date(None, "Date:x") is None)


# ------------------------------------------------------------------ 欄に収める（実機の値: 行間 1.2 で 34px、0.8 で 23px）
def measurer(rows_at):
    def measure(font, line):
        return rows_at(font), int(font / 20.0 * 28.3 * line + 0.5)
    return measure


FULL = 272.7
check("8 rows stay as the game drew them",
      rules.choose_fit(measurer(lambda _f: 8), FULL, 20, 1.2) == (20, 1.2))
font, line = rules.choose_fit(measurer(lambda _f: 9), FULL, 20, 1.2)
check("9 rows tighten the lines but keep the font", font == 20 and 0.8 <= line < 1.2, (font, line))
check("9 rows fit after tightening", 9 * int(20 / 20.0 * 28.3 * line + 0.5) <= FULL, line)
font, line = rules.choose_fit(measurer(lambda _f: 11), FULL, 20, 1.2)
check("11 rows still keep the font", font == 20, (font, line))
font, line = rules.choose_fit(measurer(lambda f: 16 if f >= 20 else 13), FULL, 20, 1.2)
check("too many rows shrink the font", font < 20, (font, line))
check("wrapping less at a smaller font is measured again", 13 * int(font / 20.0 * 28.3 * line + 0.5) <= FULL)
check("the smallest pair comes back when nothing fits",
      rules.choose_fit(measurer(lambda _f: 500), FULL, 20, 1.2) == (20 * rules.FONT_SCALES[-1], rules.MIN_LINE_HEIGHT))
check("the floor never raises the game's own line height",
      rules.choose_fit(measurer(lambda _f: 500), FULL, 20, 0.7)[1] == 0.7)

# ------------------------------------------------------------------ 既定値が3か所で揃っている
with open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as handle:
    manifest = json.load(handle)
declared = manifest["settings"]
check("mod.json START_YEAR matches", declared["START_YEAR"]["default"] == mod.START_YEAR)
check("mod.json MONTHS matches the rules",
      declared["MONTHS"]["default"] == mod.MONTHS == rules.DEFAULT_MONTHS)
check("mod.json FORMAT matches the rules",
      declared["FORMAT"]["default"] == mod.FORMAT == rules.DEFAULT_FORMAT)
check("the state folder is declared", mod.STATE_DIRNAME in manifest["state"])


# ------------------------------------------------------------------ 見張りの包み
class FakeStore(object):
    records = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def rebind(self, *_args):
        return self

    def load(self, world, fresh=False):
        return dict(self.records.get(world, {}))


class FakeCtx(object):
    mod_dir = MOD_DIR

    def __init__(self):
        self.hooks = {}
        self.logs = []

    def log(self, text):
        self.logs.append(text)

    def log_exc(self, text):
        self.logs.append("EXC " + text)

    def logger(self, _name, tag=""):
        return lambda text: self.logs.append(text)

    def out_path(self, name):
        return name

    def wrap(self, target, **_options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


app = types.SimpleNamespace(world=types.SimpleNamespace(days_elapsed=31))
label = types.SimpleNamespace(text="")
painted = []


class FakeHud(object):
    status_label = label
    icon_buttons = ()
    status_texts = STATUS


hud = FakeHud()


def game_paint(self, instance=None, value=None):
    """ゲームの見張り。渡された `value` を塗る。"""
    painted.append(value)
    label.text = value


hud.update_status_texts = lambda instance, value: ctx.hooks[mod.HUD_TARGET](game_paint, hud, instance, value)

ctx = FakeCtx()
days_hooks, load_hooks, world_name = [], [], ["甲"]
saved = (state_api.WorldStore, durations.on_days, loads.on, ui.find_app, ui.find_hud, state_api.world_key,
         ui.scheduler)
state_api.WorldStore = FakeStore
durations.on_days = lambda owner, _ctx, after=None, **_kw: days_hooks.append((owner, after))
loads.on = lambda owner, _ctx, kinds, after=None, **_kw: load_hooks.append((owner, kinds, after))
ui.find_app = lambda: app
ui.find_hud = lambda _app: hud
state_api.world_key = lambda _app: world_name[0]
ui.scheduler = lambda _ctx, _tag="": lambda fn, delay=0.0: fn()
real_measure = mod.measure_on
try:
    label.top, label.y, label.width, label.font_size, label.line_height = 300.0, 60.0, 360.0, 20, 1.2
    label.bind = lambda **_kw: None
    # 組版は Kivy が要るので差し替える（収める判断は上で見た）。
    mod.measure_on = lambda _l, _t: (lambda _f, _h: (8, 34))

    FakeStore.records = {"乙": {"START_YEAR": 500, "MONTHS": "霜月:10,10", "FORMAT": "{year}-{month}-{day}"}}
    mod.apply(ctx)
    wrap = ctx.hooks[mod.HUD_TARGET]
    check("the status watcher is wrapped", callable(wrap))
    check("applying again repaints what was painted without the wrap",
          painted[-1:] == [STATUS + "\nDate:1年2月1日"], painted[-1:])

    wrap(game_paint, hud, hud, STATUS)
    check("the painted value has the date", painted[-1] == STATUS + "\nDate:1年2月1日", painted[-1:])
    check("the game's own property keeps the plain text", hud.status_texts == STATUS)

    wrap(game_paint, hud, hud, "")
    check("an empty status stays empty", painted[-1] == "")

    app.world.days_elapsed = 32
    owner, after = days_hooks[-1]
    check("the owner is the folder name", owner == "345_date_display", owner)
    after(types.SimpleNamespace(moved=1, app=app))
    check("the days hook repaints", painted[-1] == STATUS + "\nDate:1年2月2日", painted[-1:])
    before = len(painted)
    after(types.SimpleNamespace(moved=0, app=app))
    check("no movement, no repaint", len(painted) == before)

    world_name[0] = "乙"
    check("load and new game are watched", load_hooks[-1][1] == (loads.LOAD, loads.NEW_GAME))
    load_hooks[-1][2](types.SimpleNamespace(app=app))
    check("the other world's calendar applies after a load",
          painted[-1] == STATUS + "\n501-2月-3", painted[-1:])

    FakeStore.records["乙"]["START_YEAR"] = 700
    load_hooks[-1][2](types.SimpleNamespace(app=app))
    check("opening the same world again reads its settings again",
          painted[-1] == STATUS + "\n701-2月-3", painted[-1:])

    # 見張りが `value` を塗らないビルドの保険
    label.text = STATUS
    wrap(lambda self, instance=None, value=None: None, hud, hud, STATUS)
    check("the label is written when the watcher ignores the value",
          label.text.endswith("701-2月-3"), label.text)
    check("that is warned about", any("did not take the value" in line for line in ctx.logs))
finally:
    mod.measure_on = real_measure
    (state_api.WorldStore, durations.on_days, loads.on, ui.find_app, ui.find_hud, state_api.world_key,
     ui.scheduler) = saved

if failures:
    print("{} check(s) failed".format(len(failures)))
    sys.exit(1)
print("date display: ok")
