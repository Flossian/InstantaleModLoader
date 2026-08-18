# -*- coding: utf-8 -*-
"""115_ui_item_list_fit.py をゲーム抜きで通す。

    python tools/tests/test_ui_item_list_fit.py

偽の `scripts.hud.new_hud` / `InstanTaleHUD` / Kivy（Clock・Window）と、
実測に合わせた偽の一覧（`GridLayout` 相当）を差し込んで、次を確認する。

  無干渉   … 収まっている一覧には触らない（`cols` も寸法も）
  2列      … はみ出す件数では列が増え、窓の内側に収まる
  不動     … 位置（`pos`）に触らない。行の高さも文字も変えない
  全件     … 列にしても行は1つも捨てない
  上限     … `MAX_COLUMNS` を超えて増やさない
  固定     … `FIXED_COLUMNS` を入れると、収まっていてもその列数にする
  幅       … 列が入れ物からはみ出すときだけ広げる（左端は動かさない）
  高さ     … 入れ物自身の `minimum_height` に合わせる
  安定     … 高さを変えると下端が動くビルドでも、列数が行ったり来たりしない
  復元     … もう一度押すと `cols` も寸法も元に戻る
  吹き出し … アイテム説明の箱には触らない（列を持てない／ボタンの中／中身が空）
  別物     … 選択肢のボタン（押下で作り直されない）には触らない
  途中     … レイアウトが走る前の寸法を控えない
  無傷     … 一覧が見つからないビルドでは何もしない

寸法は実測（GAME.md §2.14.1）に合わせてある: 窓 1000 高、
一覧は `ToolListPopup`（`GridLayout` 派生）幅 926.64・18行、行の高さ 57、
アイテム説明の箱は `ItemDetailBox`（`FloatLayout` 派生・`parent=Button`・中身は
`['', '', 'item_detail:']`）。
"""
import importlib.util
import io
import json
import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD = find_mod("_ui_item_list_fit")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 実測値
WIN_WIDTH, WIN_HEIGHT = 1876.0, 1000.0
ROW_HEIGHT = 57.0                     # 実機の行の高さ
ROW_WIDTH = 175.0
LIST_X, LIST_Y = 486.2, 80.9          # 実機の一覧の位置（入力欄のすぐ上）
LIST_WIDTH = 926.64                   # 実機の一覧の幅（行より広い）
SPACING = 2.0
FONT_SIZE = 24.0

MAX_FILL = 0.9
MAX_COLUMNS = 4
MARGIN = 4.0
ALLOWED = min(WIN_HEIGHT * MAX_FILL, WIN_HEIGHT - MARGIN - LIST_Y)
PER_COLUMN = int((ALLOWED + SPACING) // (ROW_HEIGHT + SPACING))


# ---------------------------------------------------------------- 偽 Kivy
class FakeWindow(object):
    width = WIN_WIDTH
    height = WIN_HEIGHT
    children = []


class FakeWidget(object):
    def __init__(self):
        self.size_hint = (1, 1)
        self.size_hint_x = 1
        self.size_hint_y = 1
        self.width = 0.0
        self.height = 0.0
        self.x = 0.0
        self.y = 0.0
        self.parent = None
        self.children = []

    @property
    def size(self):
        return (self.width, self.height)

    @property
    def pos(self):
        return (self.x, self.y)

    @property
    def top(self):
        return self.y + self.height

    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def remove_widget(self, widget):
        if widget in self.children:
            self.children.remove(widget)
            widget.parent = None


class FakeRow(FakeWidget):
    """一覧の1行（アイテム名のボタン）。"""

    def __init__(self, text):
        FakeWidget.__init__(self)
        self.text = text
        self.size_hint = (None, None)
        self.size_hint_x = None
        self.size_hint_y = None
        self.width, self.height = ROW_WIDTH, ROW_HEIGHT
        self.font_size = FONT_SIZE


class FakeButton(FakeWidget):
    """アイテムのアイコン。`on_press` を持つ ＝ mod から見て「ボタン」。"""

    def __init__(self):
        FakeWidget.__init__(self)
        self.on_press = lambda *a: None
        self.width, self.height = 64.0, 64.0


class FakeGrid(FakeWidget):
    """一覧（`ToolListPopup` ＝ `GridLayout` 派生）のうち mod から見える部分。

    `anchor="bottom"` … 高さを変えても下端が動かない `anchor="top"` … 上端が固定で、
    高さを変えると下端が動く（列数が行ったり
                        来たりしないかを見るため）
    """

    def __init__(self, x=LIST_X, y=LIST_Y, anchor="bottom", lazy=False,
                 stale_height=False, obeys_cols=True):
        FakeWidget.__init__(self)
        self.size_hint = (None, None)
        self.size_hint_x = None
        self.size_hint_y = None
        self.x, self.y = x, y
        self.width, self.height = LIST_WIDTH, 0.0
        self.cols = 1
        self.spacing = [SPACING, SPACING]
        self.padding = [0, 0, 0, 0]
        self.minimum_height = 0.0
        self.anchor = anchor
        self.lazy = lazy
        # 実機で観測した状態: 行は並び終わっているのに、
        # 入れ物の矩形だけが `(0, 0, 926, 78.75)` のまま（GAME.md §2.14.1）。
        # ここを条件にすると一覧を1つも掴めない。
        self.stale_height = stale_height
        # obeys_cols=False … `cols` を変えても行の位置が変わらないビルド。
        # 実機の `ToolListPopup` はこう見えている（箱の高さ 78.75 に対して行は
        # 0〜1026 に並ぶ）。
        # この場合 mod が自分で行を置く。
        self.obeys_cols = obeys_cols
        # `rows` は `GridLayout` の**個数のプロパティ**（mod はここを
        # None にする）。
        # 行のウィジェットは `row_widgets()` で返す。
        self.rows = None

    def row_widgets(self):
        return [child for child in reversed(self.children)
                if isinstance(child, FakeRow)]          # 足した順

    def layout(self):
        """Kivy の `GridLayout` が次のフレームでやること。"""
        if self.lazy:
            return
        rows = self.row_widgets()
        if not rows:
            return
        if not self.obeys_cols:
            # 行の位置を格子が決めていないビルド。
            # 1列のまま置き直すだけ。
            self.minimum_height = len(rows) * (ROW_HEIGHT + SPACING) - SPACING
            return
        cols = max(1, int(self.cols or 1))
        per_column = int(math.ceil(float(len(rows)) / cols))
        self.minimum_height = (per_column * (ROW_HEIGHT + SPACING) - SPACING)
        top = self.top
        if self.anchor == "top":
            self.y = top - self.height          # 上端を固定（下端が動く）
        top = self.top
        for index, row in enumerate(rows):
            line, column = divmod(index, cols)
            row.x = self.x + column * (ROW_WIDTH + SPACING)
            row.y = top - (line + 1) * ROW_HEIGHT - line * SPACING
        if self.stale_height:
            self.height = 78.75          # 行はそのまま、矩形だけ組み上がる前の値

    def place_one_column(self):
        """格子が並べてくれないビルドで、行が実際に置かれている形。

        実機の見え方（下端から上へ1列）に合わせる。
        入れ物の高さ（入力欄の帯）とはまるで噛み合っていない。
        """
        rows = self.row_widgets()
        for index, row in enumerate(rows):
            row.x = self.x
            row.y = self.y + index * (ROW_HEIGHT + SPACING)
        self.minimum_height = len(rows) * (ROW_HEIGHT + SPACING) - SPACING


class FakeDetailBox(FakeWidget):
    """アイテム説明の吹き出し（`ItemDetailBox`）。**掴んではいけない相手**。

    列を持てず（`FloatLayout` 派生）、ボタンにぶら下がり、中身は空の行ばかり。
    """

    def __init__(self, parent):
        FakeWidget.__init__(self)
        self.size_hint = (None, None)
        self.x, self.y = 924.5, 392.5
        self.width, self.height = 231.0, 347.0
        parent.add_widget(self)
        for index, text in enumerate(("", "", "item_detail:", "")):
            row = FakeRow(text)
            row.y = 400.0 + index * 60.0
            row.width = 231.0
            self.add_widget(row)


class FakeClock(object):
    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, timeout=0):
        self.pending.append(callback)


CLOCK = FakeClock()


def install_fake_kivy():
    """mod は kivy を関数の中で遅延 import する。sys.modules に偽物を置く。"""
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = CLOCK
    window_mod = types.ModuleType("kivy.core.window")
    window_mod.Window = FakeWindow
    for name, module in (("kivy", types.ModuleType("kivy")),
                         ("kivy.clock", clock_mod),
                         ("kivy.core", types.ModuleType("kivy.core")),
                         ("kivy.core.window", window_mod),
                         ("kivy.uix", types.ModuleType("kivy.uix"))):
        sys.modules[name] = module


# ---------------------------------------------------------------- 偽 HUD
class FakeHUD(FakeWidget):
    """本物の `InstanTaleHUD` のうち、この mod から見える部分だけ。

    アイコンを押すと一覧を組んで載せ、もう一度押すと外す（本物と同じ toggle）。
    """

    def __init__(self, count=10, anchor="bottom", lazy=False, with_detail=True,
                 stale_height=False, obeys_cols=True):
        FakeWidget.__init__(self)
        self.width, self.height = WIN_WIDTH, WIN_HEIGHT
        self.count = count
        self.anchor = anchor
        self.lazy = lazy
        self.stale_height = stale_height
        self.obeys_cols = obeys_cols
        self.item_list = None
        self.selected = None
        # 選択肢のボタン（縦に積まれているが、押下で作り直されずはみ出しもしない）。
        self.choices = FakeGrid(x=40.0, y=700.0)
        for index in range(4):
            self.choices.add_widget(FakeRow("選択肢{}".format(index)))
        self.choices.height = 4 * (ROW_HEIGHT + SPACING) - SPACING
        self.choices.layout()
        self.add_widget(self.choices)
        # アイテムのアイコンと、それにぶら下がる説明の吹き出し。
        self.icon = FakeButton()
        self.add_widget(self.icon)
        self.detail = FakeDetailBox(self.icon) if with_detail else None

    # -- ゲーム側の仕組み ---------------------------------------------------
    def build_list(self):
        stack = FakeGrid(anchor=self.anchor, lazy=self.lazy,
                         stale_height=self.stale_height,
                         obeys_cols=self.obeys_cols)
        for index in range(self.count):
            stack.add_widget(FakeRow("新しいアイテム{}".format(index + 1)))
        if self.obeys_cols:
            stack.height = self.count * (ROW_HEIGHT + SPACING) - SPACING
            stack.layout()
        else:
            # 実機の `ToolListPopup`: 高さは入力欄の帯のまま（`size_hint=[1,1]`）、
            # 行は下端から上へ1列に並んでいる。
            stack.height = 78.75
            stack.place_one_column()
        return stack

    def press_item_icon(self, *args):
        if self.item_list is not None and self.item_list.parent is not None:
            self.item_list.parent.remove_widget(self.item_list)   # 畳む
            self.item_list = None
            return "closed"
        self.item_list = self.build_list()
        self.add_widget(self.item_list)
        return "opened"

    def press_skill_icon(self, *args):
        return self.press_item_icon(*args)

    # -- テストから使う道具 -------------------------------------------------
    def grids(self):
        found = []

        def walk(widget):
            if isinstance(widget, FakeGrid):
                found.append(widget)
            for child in list(widget.children):
                walk(child)

        walk(self)
        return found

    def layout(self):
        for grid in self.grids():
            grid.layout()

    def settle(self, rounds=8):
        """押下のあとの「次のフレーム」を1回ずつ回す。

        **1フレームに1つずつ**走らせ、間にレイアウトを挟む。
        本物もそうなっているので、まだ組み上がっていない寸法を mod が控えてしまわないかが、
        ここで初めて見える。
        """
        for _round in range(rounds):
            if not CLOCK.pending:
                break
            CLOCK.pending.pop(0)(0)
            self.layout()
        self.layout()


PRISTINE = {name: getattr(FakeHUD, name)
            for name in ("press_item_icon", "press_skill_icon")}


# ---------------------------------------------------------------- 偽ローダ
class FakeCtx(object):
    """`ctx.wrap` だけ本物と同じ形にする（第1引数 orig、第2引数 self）。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.wrapped = {}
        self.errors = []
        self.warnings = []

    def wrap(self, target, **kw):
        def decorate(func):
            module_name, qualname = target.split(":")
            module = sys.modules[module_name]
            cls_name, method = qualname.split(".")
            cls = getattr(module, cls_name)
            if not hasattr(cls, method):
                if kw.get("required") is False:
                    return func
                raise AssertionError("no such target: " + target)
            original = getattr(cls, method)

            def wrapper(self_, *args, **kwargs):
                return func(original, self_, *args, **kwargs)

            setattr(cls, method, wrapper)
            self.wrapped[target] = wrapper
            return func
        return decorate

    def patch(self, target, **kw):
        raise AssertionError("this mod should not use ctx.patch")

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

    def warner(self, tag):
        import instantale_modloader as _ml
        return _ml.ModContext.warner(self, tag)

    def log(self, msg, level="INFO"):
        if level == "WARN":
            self.warnings.append(msg)

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_ui_item_list_fit", MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install(mod, ctx, **settings):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。

    `settings` はローダが `apply()` の前にモジュールのグローバルへ書く値（TECH.md
    §3.8）と同じ形で当てる。
    """
    for name, func in PRISTINE.items():
        setattr(FakeHUD, name, func)
    CLOCK.pending = []
    mod.MAX_FILL, mod.MAX_COLUMNS, mod.FIXED_COLUMNS = MAX_FILL, MAX_COLUMNS, 0
    for name, value in settings.items():
        setattr(mod, name, value)
    mod.apply(ctx)


def close(value, expected, tolerance=0.51):
    return abs(value - expected) < tolerance


def rows_of(grid):
    return grid.row_widgets()


def extent(grid):
    rows = rows_of(grid)
    return min(row.y for row in rows), max(row.top for row in rows)


def open_list(hud):
    hud.press_item_icon()
    hud.settle()
    return hud.item_list


def inside_window(grid):
    bottom, top = extent(grid)
    right = max(row.x + row.width for row in rows_of(grid))
    return (top <= WIN_HEIGHT - MARGIN + 0.5 and bottom >= -0.5
            and right <= WIN_WIDTH + 0.5)


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    sys.modules["scripts"] = types.ModuleType("scripts")
    sys.modules["scripts.hud"] = types.ModuleType("scripts.hud")
    sys.modules["scripts.hud.new_hud"] = module

    mod = load_mod()
    ctx = FakeCtx(os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test")))

    # -- 収まっている一覧には触らない ---------------------------------------
    print("\n[収まる] 10件（1列で窓に入る）")
    install(mod, ctx)
    hud = FakeHUD(count=10)
    grid = open_list(hud)
    check("列を増やさない", grid.cols == 1, grid.cols)
    check("位置が動かない", grid.pos == (LIST_X, LIST_Y), grid.pos)
    check("幅が変わらない", close(grid.width, LIST_WIDTH), grid.width)
    check("行の高さが変わらない",
          all(close(row.height, ROW_HEIGHT) for row in rows_of(grid)))

    # -- はみ出す件数は列にする ---------------------------------------------
    print("\n[2列] 18件（実機と同じ。1列でははみ出す）")
    install(mod, ctx)
    hud = FakeHUD(count=18)
    grid = open_list(hud)
    rows = rows_of(grid)
    check("2列になる", grid.cols == 2, grid.cols)
    check("窓の内側に収まる", inside_window(grid), extent(grid))
    check("位置が動かない", grid.pos == (LIST_X, LIST_Y), grid.pos)
    check("行がすべて残る", len(rows) == 18, len(rows))
    check("行の高さが変わらない", all(close(row.height, ROW_HEIGHT) for row in rows))
    check("文字の大きさが変わらない", all(close(row.font_size, FONT_SIZE) for row in rows))
    check("2列に分かれて並ぶ", len(set(round(row.x, 1) for row in rows)) == 2,
          sorted(set(round(row.x, 1) for row in rows)))
    check("高さは入れ物自身の値に合わせる",
          close(grid.height, grid.minimum_height), (grid.height, grid.minimum_height))
    check("幅は足りているので広げない", close(grid.width, LIST_WIDTH), grid.width)

    print("\n[安定] 塗り直しが何度来ても列数が揺れない")
    for _round in range(5):
        hud.settle()
    check("2列のまま", grid.cols == 2, grid.cols)
    check("窓の内側のまま", inside_window(grid))

    print("\n[安定] 高さを変えると下端が動くビルドでも揺れない")
    install(mod, ctx)
    hud = FakeHUD(count=18, anchor="top")
    grid = open_list(hud)
    first = grid.cols
    for _round in range(5):
        hud.settle()
    check("列数が行ったり来たりしない", grid.cols == first, (first, grid.cols))
    check("窓の内側に収まる", inside_window(grid), extent(grid))

    # -- 上限と固定 ----------------------------------------------------------
    print("\n[上限] 200件でも `MAX_COLUMNS` を超えない")
    install(mod, ctx)
    hud = FakeHUD(count=200)
    grid = open_list(hud)
    check("上限どまり", grid.cols == MAX_COLUMNS, grid.cols)
    check("行を1つも捨てない", len(rows_of(grid)) == 200)
    check("左端は動かない", close(grid.x, LIST_X), grid.x)

    print("\n[固定] 列数を2に固定すると、収まっていても2列")
    install(mod, ctx, FIXED_COLUMNS=2)
    hud = FakeHUD(count=10)
    grid = open_list(hud)
    check("2列になる", grid.cols == 2, grid.cols)
    check("位置が動かない", grid.pos == (LIST_X, LIST_Y), grid.pos)

    print("\n[幅] 列が入れ物からはみ出すときだけ広げる")
    install(mod, ctx, FIXED_COLUMNS=4)
    hud = FakeHUD(count=10)
    grid = open_list(hud)
    grid_width_needed = 4 * (ROW_WIDTH + SPACING) - SPACING
    check("必要なら広げる（元が足りていれば広げない）",
          grid.width >= min(LIST_WIDTH, grid_width_needed) - 0.5, grid.width)
    check("左端は動かない", close(grid.x, LIST_X), grid.x)

    # -- 復元 ----------------------------------------------------------------
    print("\n[復元] もう一度押すと元に戻る")
    install(mod, ctx)
    hud = FakeHUD(count=18)
    grid = open_list(hud)
    check("いったん2列", grid.cols == 2, grid.cols)
    hud.press_item_icon()
    hud.settle()
    check("一覧が消える", hud.item_list is None)
    check("`cols` が戻る", grid.cols == 1, grid.cols)
    check("寸法が戻る", close(grid.width, LIST_WIDTH), (grid.width, grid.height))

    # -- 触ってはいけないもの -----------------------------------------------
    print("\n[吹き出し] アイテム説明の箱には触らない")
    install(mod, ctx)
    hud = FakeHUD(count=18)
    before = (hud.detail.size, hud.detail.pos, hud.detail.parent,
              [row.height for row in hud.detail.children])
    open_list(hud)
    after = (hud.detail.size, hud.detail.pos, hud.detail.parent,
             [row.height for row in hud.detail.children])
    check("説明の箱が変わらない", before == after, (before, after))
    check("説明の箱は親（ボタン）から動かない", hud.detail.parent is hud.icon)

    print("\n[別物] 選択肢のボタンには触らない")
    install(mod, ctx)
    hud = FakeHUD(count=18)
    before = (hud.choices.cols, hud.choices.size, hud.choices.pos)
    open_list(hud)
    after = (hud.choices.cols, hud.choices.size, hud.choices.pos)
    check("選択肢が変わらない", before == after, (before, after))

    # -- レイアウトが次のフレームで走るビルド --------------------------------
    print("\n[途中で測らない] 組んだ直後はまだ行の位置が入っていない")
    install(mod, ctx)
    hud = FakeHUD(count=18, lazy=True)
    hud.press_item_icon()
    grid = hud.item_list
    grid.lazy = False                 # 次のフレームでレイアウトが走る
    hud.settle()
    check("2列になる", grid.cols == 2, grid.cols)
    check("窓の内側に収まる", inside_window(grid), extent(grid))
    check("位置が動かない", grid.pos == (LIST_X, LIST_Y), grid.pos)

    print("\n[食い違い] 行は並んでいるのに入れ物の矩形が組み上がる前のまま（実機で観測）")
    install(mod, ctx)
    hud = FakeHUD(count=18, stale_height=True)
    grid = open_list(hud)
    rows = rows_of(grid)
    check("それでも2列にする", grid.cols == 2, (grid.cols, grid.size))
    check("2列に分かれて並ぶ", len(set(round(row.x, 1) for row in rows)) == 2,
          sorted(set(round(row.x, 1) for row in rows)))
    check("当てにならない矩形を書き戻さない",
          not close(grid.height, 78.75) or grid.height <= 78.75 + 0.5, grid.height)
    check("行がすべて残る", len(rows) == 18, len(rows))

    print("\n[格子が並べない] `cols` を変えても行が動かないビルド（実機の見え方）")
    install(mod, ctx)
    hud = FakeHUD(count=18, obeys_cols=False)
    grid = open_list(hud)
    rows = rows_of(grid)
    xs = sorted(set(round(row.x, 1) for row in rows))
    bottom, top = extent(grid)
    check("自分で2列に並べる", len(xs) == 2, xs)
    check("左端は元のまま", close(xs[0], LIST_X), xs)
    check("2列目は行の幅ぶん右", close(xs[1], LIST_X + ROW_WIDTH + SPACING), xs)
    check("下端が動かない", close(bottom, LIST_Y), bottom)
    check("窓の内側に収まる", top <= WIN_HEIGHT - MARGIN + 0.5, top)
    check("行がすべて残る", len(rows) == 18, len(rows))
    check("行の高さが変わらない", all(close(row.height, ROW_HEIGHT) for row in rows))
    check("入れ物の高さを中身に合わせる",
          close(grid.height, 9 * (ROW_HEIGHT + SPACING) - SPACING), grid.height)
    print("  （もう一度当てても同じ結果になるか）")
    for _round in range(3):
        hud.settle()
    check("並べ直しても同じ", sorted(set(round(row.x, 1) for row in rows)) == xs
          and close(extent(grid)[0], LIST_Y), (xs, extent(grid)))

    print("\n[飾り] `text` を持たないウィジェットを行に数えない")
    # `frames.attr` の既定は**文字列**の番人（`"<missing>"`）なので、
    # `isinstance(値, str)` で受けると背景・枠線・画像まで「行」になる（`118_` が同じ罠を踏んでいる。
    # TECH.md §5.2）。
    # 飾りが本物の行と同じ高さに居ると「横並び＝一覧ではない」に掛かり、**一覧が丸ごと棄却される**。
    # 手で並べるビルド（`ToolListPopup` の形）で見る。
    # 格子が自力で折り返す側だと mod は行に触らないので、
    # 飾りを行と取り違えても表に出ない。
    install(mod, ctx)
    hud = FakeHUD(count=18, obeys_cols=False)
    hud.press_item_icon()
    grid = hud.item_list
    decoration = FakeWidget()               # 背景板。`text` を持たない
    decoration.width, decoration.height = ROW_WIDTH, ROW_HEIGHT
    decoration.x, decoration.y = -999.0, -999.0    # 行の並びから外れた場所
    grid.add_widget(decoration)
    hud.settle()
    rows = [child for child in grid.children if isinstance(child, FakeRow)]
    xs = sorted(set(round(row.x, 1) for row in rows))
    check("飾りが混じっても一覧を掴む（棄却されない）", len(xs) > 1, xs)
    check("行はすべて残る", len(rows) == 18, len(rows))
    check("飾りは動かさない（行として並べ替えない）",
          close(decoration.x, -999.0) and close(decoration.y, -999.0),
          (decoration.x, decoration.y))
    check("握り潰した例外が無い（飾り混在）", not ctx.errors,
          "\n".join(ctx.errors[:2]))

    print("\n[無傷] 一覧が出てこないビルド")
    install(mod, ctx)
    hud = FakeHUD(count=0, with_detail=True)
    hud.press_item_icon()
    hud.settle()
    check("警告を出さない（畳んだときもここへ来る）", ctx.warnings == [], ctx.warnings)
    check("選択肢はそのまま", hud.choices.cols == 1)

    check("握り潰した例外が無い", not ctx.errors, "\n".join(ctx.errors[:2]))

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
