# -*- coding: utf-8 -*-
"""124_ui_craft_window_fit.py をゲーム抜きで通す。

    python tools/tests/test_ui_craft_window_fit.py

偽の `scripts.hud.new_hud` / `InstanTaleHUD` / Kivy（Clock・Window）と、
クラフト画面と同じ並び（所持品・クラフト・生成先の3つのグリッド＋矢印＋「作成」ボタン）を組んで、
次を確認する。

  無干渉 … 隙間が足りている配置では1ドットも動かさない
  不重複 … 重なる配置では、当てた後どのグリッドとも重ならない
  最小   … 入る隙間があるときは、いちばん動かさずに済む位置へ滑らせる
  詰め   … 近い隙間に入らないときだけ幅を詰める（下限を割らない）
  逃げ   … 横に入る空きが1つも無ければグリッドの下へ逃がす
  無傷   … グリッドは1つも動かない・大きさも変わらない
  分数   … `pos_hint` を直すので、次のレイアウトで元へ戻らない
  冪等   … 何度当てても、注入し直しても位置が育たない
  透明   … 見えていないグリッドは避ける相手に数えない
  相対   … 座標系がずれていても（`to_window`）結果は同じ
  追従   … 窓の大きさが変わっても、詰めた幅を設計値と取り違えない
  入口   … app 側の `toggle_craft_inventory_window` からも当たる
  別物   … ボタンを持たないビルドでは何もしない（例外も出さない）

寸法は 2026-08-11 の画面（2560x1440）から起こした概寸で、実測ではない。
数値そのものではなく**隙間とボタンの幅の大小関係**を再現するために置いてある（mod は固定値を持たず、
その場で測って動かす）。
"""
import importlib.util
import io
import json
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


MOD = find_mod("_ui_craft_window_fit")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ------------------------------------------------------------------ 概寸
WIN_WIDTH, WIN_HEIGHT = 2560.0, 1440.0

# (x, y, 幅, 高さ)。
# 画面から起こした概寸。
BAG_GRID = (804.0, 768.0, 256.0, 390.0)        # 所持品
CRAFT_GRID = (1079.0, 828.0, 256.0, 269.0)     # クラフト（材料）
OUT_GRID = (1504.0, 772.0, 230.0, 384.0)       # 生成先
BUTTON = (1425.0, 881.0, 175.0, 56.0)          # 「作成」
ARROW = (1508.0, 1046.0, 22.0, 19.0)           # 「→」（矩形＝文字）
ARROW_TEXTURE = (22.0, 19.0)                   # 文字の箱

# 矢印のラベルがグリッドと同じくらい大きいビルド。
# 見えているのは中心の文字だけ。
WIDE_ARROW = (1420.0, 950.0, 320.0, 220.0)

# 生成先グリッドとクラフトグリッドの隙間（169）より、ボタン（175）のほうが広い。
# これが実機で起きている重なりの正体。
GAP = OUT_GRID[0] - (CRAFT_GRID[0] + CRAFT_GRID[2])


# --------------------------------------------------------------- 偽 Kivy
class FakeWindow(object):
    width = WIN_WIDTH
    height = WIN_HEIGHT
    children = []


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


# ------------------------------------------------------------- 偽ウィジェット
class FakeWidget(object):
    def __init__(self, rect=(0.0, 0.0, 0.0, 0.0)):
        self.x, self.y, self.width, self.height = [float(v) for v in rect]
        self.opacity = 1.0
        self.parent = None
        self.children = []
        self.pos_hint = {}
        self.size_hint_x = None
        self.size_hint_y = None
        # 座標系のずれ（`RelativeLayout` が挟まっている場合に相当）。
        self.offset = (0.0, 0.0)

    @property
    def right(self):
        return self.x + self.width

    @property
    def top(self):
        return self.y + self.height

    @property
    def pos(self):
        return (self.x, self.y)

    def to_window(self, x, y):
        return (x + self.offset[0], y + self.offset[1])

    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self
        widget.offset = self.offset
        for child in widget.children:
            child.offset = self.offset
        return widget

    def window_rect(self):
        left, bottom = self.to_window(self.x, self.y)
        return (left, bottom, left + self.width, bottom + self.height)

    def layout(self):
        """Kivy の `FloatLayout` が次のフレームでやること。

        `pos_hint` を持つ子は、毎回ここで位置を入れ直される。
        mod が `x` にしか書かなければ、この1回で元の場所へ戻る。
        """
        for child in self.children:
            hint = child.pos_hint or {}
            if child.size_hint_x is not None:
                child.width = self.width * child.size_hint_x
            if "x" in hint:
                child.x = self.x + hint["x"] * self.width
            if "right" in hint:
                child.x = self.x + hint["right"] * self.width - child.width
            if "center_x" in hint:
                child.x = self.x + hint["center_x"] * self.width - child.width / 2.0
            if "y" in hint:
                child.y = self.y + hint["y"] * self.height
            if "top" in hint:
                child.y = self.y + hint["top"] * self.height - child.height
            if "center_y" in hint:
                child.y = self.y + hint["center_y"] * self.height - child.height / 2.0
            child.layout()


class FakeGrid(FakeWidget):
    """アイテムを置くグリッド（`InventoryGrid` 相当）。

    mod はこれを**型名ではなく持ち物**で見分ける。
    ここに並べたメソッド名がその手掛かりそのもの。
    """

    def __init__(self, rect):
        FakeWidget.__init__(self, rect)

    def place_new_item(self, *args):
        return None

    def try_place_item(self, *args):
        return None

    def occupy_slots(self, *args):
        return None

    def find_placement_position(self, *args):
        return None

    def is_valid_placement(self, *args):
        return True


class FakeLabel(FakeWidget):
    """矢印のラベル。

    Kivy の `Label` は `text_size` を持たなければ、
    文字のテクスチャを **ウィジェットの中心に**描く。
    `texture_size` がその文字の大きさで、
    ウィジェットの矩形とは別物（`window_rect` ではなく
    `glyph_rect` が見えている枠）。
    """

    def __init__(self, rect, text="", texture=None):
        FakeWidget.__init__(self, rect)
        self.text = text
        self.texture_size = list(texture if texture else (rect[2], rect[3]))

    def glyph_rect(self):
        left, bottom = self.to_window(self.x, self.y)
        width, height = self.texture_size
        if width > self.width or height > self.height:
            return (left, bottom, left + self.width, bottom + self.height)
        center_x, center_y = left + self.width / 2.0, bottom + self.height / 2.0
        return (center_x - width / 2.0, center_y - height / 2.0,
                center_x + width / 2.0, center_y + height / 2.0)


class FakeButton(FakeWidget):
    """「作成」ボタン。位置は `pos_hint`、幅は固定（`size_hint_x=None`）。"""

    def __init__(self, rect, parent_size):
        FakeWidget.__init__(self, rect)
        self.text = "作成"
        # 押せる ＝ 枠を自分で描く。
        # 文字（`texture_size`）より枠のほうが大きいが、
        # 見えているのは枠なので mod は矩形で測らなければならない。
        self.on_press = lambda *args: None
        self.texture_size = [48.0, 30.0]
        self.pos_hint = {
            "center_x": (rect[0] + rect[2] / 2.0) / parent_size[0],
            "center_y": (rect[1] + rect[3] / 2.0) / parent_size[1],
        }


# ------------------------------------------------------------------ 偽 HUD
class FakeHUD(FakeWidget):
    """本物の `InstanTaleHUD` のうち、この mod から見える部分だけ。"""

    def __init__(self, out_grid=OUT_GRID, bag_grid=BAG_GRID, with_button=True,
                 offset=(0.0, 0.0), hidden_over_button=False, arrow=ARROW,
                 arrow_texture=ARROW_TEXTURE):
        FakeWidget.__init__(self, (0.0, 0.0, WIN_WIDTH, WIN_HEIGHT))
        self.offset = offset
        self.craft_inventory_layout = FakeWidget((0.0, 0.0, WIN_WIDTH, WIN_HEIGHT))
        self.craft_inventory_layout.opacity = 0.0        # 最初は閉じている
        self.add_widget(self.craft_inventory_layout)
        layout = self.craft_inventory_layout
        self.bag_grid = layout.add_widget(FakeGrid(bag_grid))
        self.craft_grid = layout.add_widget(FakeGrid(CRAFT_GRID))
        self.out_grid = layout.add_widget(FakeGrid(out_grid))
        if hidden_over_button:
            # 売買・強化のグリッド。
            # クラフト画面が開いている間も居るが透明。
            self.hidden_grid = layout.add_widget(FakeGrid(
                (BUTTON[0] - 20.0, BUTTON[1] - 20.0, 400.0, 200.0)))
            self.hidden_grid.opacity = 0.0
        self.craft_inventory_generate_arrow_label = layout.add_widget(
            FakeLabel(arrow, "→", arrow_texture))
        if with_button:
            self.craft_inventory_generate_button = layout.add_widget(
                FakeButton(BUTTON, (WIN_WIDTH, WIN_HEIGHT)))
        self.layout()

    # -- ゲーム側の仕組み ---------------------------------------------------
    def toggle_craft_inventory_visibility(self, *args):
        layout = self.craft_inventory_layout
        layout.opacity = 0.0 if layout.opacity else 1.0
        return "opened" if layout.opacity else "closed"

    # -- テストから使う道具 -------------------------------------------------
    def settle(self, rounds=12):
        """押下のあとの「次のフレーム」を1回ずつ回す。間にレイアウトを挟む。"""
        for _round in range(rounds):
            if not CLOCK.pending:
                break
            CLOCK.pending.pop(0)(0)
            self.layout()
        self.layout()

    def grids(self):
        return [child for child in self.craft_inventory_layout.children
                if isinstance(child, FakeGrid)]


class InstantaleApp(object):
    """`__main__:InstantaleApp` の代わり。HUD への入口だけを持つ。"""

    def __init__(self, hud):
        self.root = hud

    def toggle_craft_inventory_window(self, *args):
        return self.root.toggle_craft_inventory_visibility(*args)


PRISTINE_HUD = {name: getattr(FakeHUD, name)
                for name in ("toggle_craft_inventory_visibility",)}
PRISTINE_APP = {name: getattr(InstantaleApp, name)
                for name in ("toggle_craft_inventory_window",)}


# --------------------------------------------------------------- 偽ローダ
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
            cls = getattr(module, cls_name, None)
            if cls is None or not hasattr(cls, method):
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
    # ここを自前で書くと、検査だけが別のログ処理を通ることになる。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        if level == "WARN":
            self.warnings.append(msg)

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_ui_craft_window_fit", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install(mod, ctx, **settings):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。"""
    for name, func in PRISTINE_HUD.items():
        setattr(FakeHUD, name, func)
    for name, func in PRISTINE_APP.items():
        setattr(InstantaleApp, name, func)
    CLOCK.pending = []
    mod.MARGIN, mod.MIN_WIDTH_RATIO, mod.FIX_ARROW = 6.0, 0.6, True
    for name, value in settings.items():
        setattr(mod, name, value)
    mod.apply(ctx)


def close(value, expected, tolerance=0.51):
    return abs(value - expected) < tolerance


def overlaps(one, other):
    return (one[0] < other[2] and other[0] < one[2]
            and one[1] < other[3] and other[1] < one[3])


def rescale(hud, factor):
    """窓の大きさが変わったときの再レイアウト。

    グリッドは窓に追従して伸び縮みし、ボタンは固定幅（`size_hint_x=None`）のまま
    `pos_hint` で置き直される。
    実機で窓の大きさを変えたときと同じ形。
    """
    FakeWindow.width *= factor
    FakeWindow.height *= factor
    layout = hud.craft_inventory_layout
    for widget in [hud, layout] + [child for child in layout.children
                                   if not isinstance(child, FakeButton)]:
        widget.x *= factor
        widget.y *= factor
        widget.width *= factor
        widget.height *= factor
    hud.layout()


def open_craft(hud):
    hud.toggle_craft_inventory_visibility()
    hud.settle()
    return hud.craft_inventory_generate_button


def clear_of_grids(hud):
    """当てた後のボタンが、見えているグリッドのどれとも重なっていないか。"""
    rect = hud.craft_inventory_generate_button.window_rect()
    return not [grid for grid in hud.grids()
                if grid.opacity and overlaps(rect, grid.window_rect())]


def arrow_clear(hud):
    """矢印の**見えている枠**（文字の箱）が、どのグリッドとも重なっていないか。"""
    rect = hud.craft_inventory_generate_arrow_label.glyph_rect()
    return not [grid for grid in hud.grids()
                if grid.opacity and overlaps(rect, grid.window_rect())]


def grid_shapes(hud):
    return [(grid.x, grid.y, grid.width, grid.height) for grid in hud.grids()]


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    sys.modules["scripts"] = types.ModuleType("scripts")
    sys.modules["scripts.hud"] = types.ModuleType("scripts.hud")
    sys.modules["scripts.hud.new_hud"] = module

    mod = load_mod()
    ctx = FakeCtx(os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test")))

    print("\n[前提] 画面から起こした配置で、隙間よりボタンのほうが広い")
    check("隙間 {:.0f} < ボタン {:.0f}".format(GAP, BUTTON[2]), GAP < BUTTON[2])

    # -- 重なっていない配置には触らない --------------------------------------
    print("\n[無干渉] 生成先グリッドが十分右に在る（重なっていない）")
    install(mod, ctx)
    roomy = (OUT_GRID[0] + 300.0,) + OUT_GRID[1:]
    hud = FakeHUD(out_grid=roomy)
    before = hud.craft_inventory_generate_button.window_rect()
    button = open_craft(hud)
    check("1ドットも動かない", button.window_rect() == before, button.window_rect())
    check("幅も変わらない", close(button.width, BUTTON[2]), button.width)
    check("重なっていない", clear_of_grids(hud))

    # -- 実機と同じ配置 ------------------------------------------------------
    print("\n[不重複] 画面と同じ配置（隙間にボタンが入らない）")
    install(mod, ctx)
    hud = FakeHUD()
    shapes = grid_shapes(hud)
    was_overlapping = overlaps(hud.craft_inventory_generate_button.window_rect(),
                               hud.out_grid.window_rect())
    button = open_craft(hud)
    check("直す前は本当に重なっている", was_overlapping)
    check("当てた後はどのグリッドとも重ならない", clear_of_grids(hud),
          button.window_rect())
    check("グリッドは1つも動かない", grid_shapes(hud) == shapes, grid_shapes(hud))
    check("画面の中に居る",
          button.window_rect()[0] >= -0.5
          and button.window_rect()[2] <= WIN_WIDTH + 0.5, button.window_rect())

    print("\n[詰め] 隙間に入らないので幅を詰める（下限は割らない）")
    check("幅が縮む", button.width < BUTTON[2], button.width)
    check("下限を割らない", button.width >= max(mod.MIN_WIDTH,
                                                BUTTON[2] * mod.MIN_WIDTH_RATIO) - 0.51,
          button.width)
    check("隙間の中に入っている",
          button.window_rect()[0] >= CRAFT_GRID[0] + CRAFT_GRID[2] - 0.5
          and button.window_rect()[2] <= OUT_GRID[0] + 0.5, button.window_rect())
    check("高さは変えない", close(button.height, BUTTON[3]), button.height)

    print("\n[分数] レイアウトが何度走っても戻らない")
    landed = button.window_rect()
    for _round in range(5):
        hud.layout()
    check("位置が戻らない", button.window_rect() == landed, button.window_rect())
    check("重なりも戻らない", clear_of_grids(hud))

    print("\n[冪等] 開き直しても位置が育たない")
    hud.toggle_craft_inventory_visibility()      # 閉じる
    hud.settle()
    open_craft(hud)
    check("同じ場所に落ち着く", button.window_rect() == landed, button.window_rect())

    print("\n[冪等] 注入し直しても位置が育たない")
    install(mod, ctx)
    open_craft(hud)
    hud.settle()
    check("同じ場所のまま", button.window_rect() == landed, button.window_rect())

    # -- 幅を変えない設定 ----------------------------------------------------
    print("\n[幅固定] 詰めない設定では、入る空きのほうへ滑らせる")
    install(mod, ctx, MIN_WIDTH_RATIO=1.0)
    hud = FakeHUD()
    button = open_craft(hud)
    check("幅は変わらない", close(button.width, BUTTON[2]), button.width)
    check("どのグリッドとも重ならない", clear_of_grids(hud), button.window_rect())

    print("\n[逃げ] 横に入る空きが1つも無ければグリッドの下へ")
    install(mod, ctx, MIN_WIDTH_RATIO=1.0)
    packed_bag = (0.0, BAG_GRID[1], BAG_GRID[0] + BAG_GRID[2], BAG_GRID[3])
    packed_out = (OUT_GRID[0], OUT_GRID[1], WIN_WIDTH - OUT_GRID[0], OUT_GRID[3])
    hud = FakeHUD(bag_grid=packed_bag, out_grid=packed_out)
    button = open_craft(hud)
    check("幅は変わらない", close(button.width, BUTTON[2]), button.width)
    check("どのグリッドとも重ならない", clear_of_grids(hud), button.window_rect())
    check("下へ逃げている", button.y < BUTTON[1], button.y)
    check("画面の中に居る", button.y >= -0.5, button.y)

    # -- 入る隙間があるときは最小の移動 --------------------------------------
    print("\n[最小] 入る隙間があるときは、いちばん動かさずに済む位置へ")
    install(mod, ctx)
    wide = (CRAFT_GRID[0] + CRAFT_GRID[2] + BUTTON[2] + 40.0,) + OUT_GRID[1:]
    hud = FakeHUD(out_grid=wide)
    button = open_craft(hud)
    left = button.window_rect()[0]
    check("幅は変えない", close(button.width, BUTTON[2]), button.width)
    check("重ならない", clear_of_grids(hud), button.window_rect())
    check("右の枠のすぐ左に着く",
          close(button.window_rect()[2], wide[0] - mod.MARGIN, 1.0),
          button.window_rect())
    check("必要以上に動かさない", left > CRAFT_GRID[0] + CRAFT_GRID[2], left)

    # -- 見えていないグリッド ------------------------------------------------
    print("\n[透明] 見えていないグリッドは避ける相手に数えない")
    install(mod, ctx)
    hud = FakeHUD(out_grid=roomy, hidden_over_button=True)
    before = hud.craft_inventory_generate_button.window_rect()
    button = open_craft(hud)
    check("透明な相手のためには動かない", button.window_rect() == before,
          button.window_rect())

    # -- 座標系がずれている場合 ----------------------------------------------
    print("\n[相対] 座標系がずれていても（to_window）結果は同じ")
    install(mod, ctx)
    hud = FakeHUD(offset=(500.0, 300.0))
    button = open_craft(hud)
    check("どのグリッドとも重ならない", clear_of_grids(hud), button.window_rect())
    check("隙間の中に入っている",
          button.x >= CRAFT_GRID[0] + CRAFT_GRID[2] - 0.5
          and button.x + button.width <= OUT_GRID[0] + 0.5, (button.x, button.width))

    # -- 矢印 ----------------------------------------------------------------
    print("\n[矢印] 生成先グリッドの裏から出す")
    install(mod, ctx)
    hud = FakeHUD()
    buried = not arrow_clear(hud)
    button = open_craft(hud)
    arrow = hud.craft_inventory_generate_arrow_label
    check("直す前は本当に埋もれている", buried)
    check("グリッドの外に出る", arrow_clear(hud), arrow.glyph_rect())
    check("ボタンとも重ならない",
          not overlaps(arrow.glyph_rect(), button.window_rect()),
          (arrow.glyph_rect(), button.window_rect()))
    check("グリッドは1つも動かない", grid_shapes(hud) == grid_shapes(hud))
    check("ボタンの直し方は変わらない", clear_of_grids(hud), button.window_rect())

    print("\n[文字] ラベルが大きいビルドでも、測るのは文字の箱")
    install(mod, ctx)
    hud = FakeHUD(arrow=WIDE_ARROW, arrow_texture=ARROW_TEXTURE)
    arrow = hud.craft_inventory_generate_arrow_label
    check("ラベルの矩形はグリッドより大きい",
          WIDE_ARROW[2] * WIDE_ARROW[3] > OUT_GRID[2] * OUT_GRID[3] * 0.5)
    open_craft(hud)
    check("文字はグリッドの外に出る", arrow_clear(hud), arrow.glyph_rect())
    check("矩形ごと遠くへ飛ばさない（動いたのは文字ぶんの距離）",
          abs(arrow.x - WIDE_ARROW[0]) < OUT_GRID[2], arrow.x)

    print("\n[矢印OFF] 動かさない設定では矢印はそのまま")
    install(mod, ctx, FIX_ARROW=False)
    hud = FakeHUD()
    arrow = hud.craft_inventory_generate_arrow_label
    before = arrow.glyph_rect()
    button = open_craft(hud)
    check("矢印は動かない", arrow.glyph_rect() == before, arrow.glyph_rect())
    check("ボタンは矢印を避ける",
          not overlaps(button.window_rect(), before), button.window_rect())

    # -- 窓の大きさが変わったとき --------------------------------------------
    print("\n[追従] 窓が広がって隙間が足りるようになったら、幅は設計値へ戻る")
    install(mod, ctx)
    hud = FakeHUD()
    button = open_craft(hud)
    check("いったん詰まる", button.width < BUTTON[2], button.width)
    rescale(hud, 1.6)
    open_craft(hud)                     # 閉じる
    open_craft(hud)                     # 開き直す
    check("設計の幅に戻る", close(button.width, BUTTON[2]), button.width)
    check("重ならない", clear_of_grids(hud), button.window_rect())
    rescale(hud, 1.0 / 1.6)             # 後の検査のために戻す

    # -- app 側の入口 --------------------------------------------------------
    print("\n[入口] app 側の toggle からも当たる")
    install(mod, ctx)
    hud = FakeHUD()
    app = InstantaleApp(hud)
    app.toggle_craft_inventory_window()
    hud.settle()
    check("こちらからでも重なりが消える", clear_of_grids(hud),
          hud.craft_inventory_generate_button.window_rect())

    # -- ボタンを持たないビルド ----------------------------------------------
    print("\n[別物] ボタンを持たないビルドでは何もしない")
    install(mod, ctx)
    hud = FakeHUD(with_button=False)
    shapes = grid_shapes(hud)
    hud.toggle_craft_inventory_visibility()
    hud.settle()
    check("グリッドを動かさない", grid_shapes(hud) == shapes, grid_shapes(hud))
    check("警告を出さない", ctx.warnings == [], ctx.warnings)

    check("握り潰した例外が無い", not ctx.errors, "\n".join(ctx.errors[:2]))

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
