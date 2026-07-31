# -*- coding: utf-8 -*-
"""112_ui_text_spacing.py をゲーム抜きで通す。

    python tools/test_ui_text_spacing.py

偽の `scripts.hud.new_hud` / `InstanTaleHUD` / Kivy の Clock を差し込んで、次を確認する。

  探索     … 本文のラベルを `vars(hud)` から見つける（属性名を知らなくても届く）
  木       … 属性として持たれていないビルドでも、ウィジェット木を降りて見つける
  巻き添え … 状態表示など、本文と関係ないラベルは掴まない
  行間     … `line_height` がゲームの値の LINE_SCALE 倍になる
  空行     … 段落の間の空行が BLANK_LINES 行ぶんまで詰まる
  非破壊   … ゲームが持っている本文（`display_text`）は書き換えない
  冪等     … 1文字ずつ何度呼ばれても行間が縮み続けない
  再注入   … MOD 側の控えが作り直されても、設計値をラベルから読み直して二重に掛けない
  高さ     … 高さの決め直しは次のフレームに1回だけ（1文字ごとに同期描画しない）
  無効     … 設定を空欄（null）にすると、その項目には触らない
  無傷     … ラベルが見つからないビルドでは何もしない

値は**実機の実測**（`out\\text_spacing.log`、2026-07-31）から取ってある:
本文のラベルは `hud.text_display`、`font_size=27`、**`line_height=1.8`**
（Kivy の既定は 1.0）。画面の採寸（行の間隔 70px・段落の間隔 138px ＝ちょうど
2行分）と合わせると1行の送りは `27 × 1.44 × 1.8 ≒ 70px` で、偽ラベルはこの3つで作る。

**ラベルの `text` は `display_text` と完全一致しない**（実測。塗るときにゲームが
末尾を足すか削るかしている ― ログの `match=2` がそれ）。偽 HUD も同じように塗る:
完全一致を条件にしていると実機ではラベルが1回も見つからないので、ここが一番効く。
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
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


MOD = find_mod("_ui_text_spacing")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 実測値
FONT_SIZE = 27            # out/text_spacing.log（hud.text_display）
LINE_HEIGHT = 1.8         # 同上。Kivy の既定は 1.0
GLYPH_TO_LINE = 1.44      # 画面採寸の行の間隔 70px から逆算（70 / (27 × 1.8)）

# ゲームがラベルに塗るときに**前へ**足しているもの。中身は分かっていないが、
# 実測の `match=2` は「ラベルの text が display_text で終わる」なので、
# 足しているのは前側だと決まる（後ろに足すなら末尾は一致しない）。
PAINT_PREFIX = "（前の本文）\n\n"

NARRATION = "薄暗い部屋に、腐敗した薬草の臭いが漂う。\n\n不衛生な医者: ……ちっ、また新しい客か。\n金は持ってんだろうな？"


# ---------------------------------------------------------------- 偽 Kivy
class FakeLabel(object):
    """Kivy の Label のうち、この mod が触るところだけ。"""

    def __init__(self, text="", parent=None):
        self.text = text
        self.line_height = LINE_HEIGHT
        self.font_size = FONT_SIZE
        self.text_size = [1400, None]
        self.texture_size = [1400, 0]
        self.height = 0
        self.parent = parent
        self.children = []
        self.updates = 0

    def texture_update(self):
        self.updates += 1
        lines = self.text.count("\n") + 1 if self.text else 0
        self.texture_size = [self.text_size[0],
                             lines * self.font_size * GLYPH_TO_LINE * self.line_height]

    def pitch(self):
        """1行ぶんの送り（判定用。mod は使わない）。"""
        return self.font_size * GLYPH_TO_LINE * self.line_height


class FakeLayout(object):
    """ラベルを抱えているだけの入れ物（ウィジェット木の途中）。"""

    def __init__(self, children):
        self.children = list(children)
        self.parent = None


class FakeHUD(object):
    """本物の `InstanTaleHUD` のうち、この mod から見える部分だけ。

    `nested=True` にすると本文のラベルを属性として持たず、木の奥にだけ置く
    （ビルドによって持ち方が違っても届くことを確かめるため）。
    """

    def __init__(self, nested=False):
        self.display_text = ""
        self.height_updates = 0
        label = FakeLabel(parent=self)
        # 本文と紛らわしいラベルを2枚混ぜておく。状態表示（画面左下）と、
        # **本文に出てくる語と同じ文字列を持つ選択肢のボタン**（Kivy の Button も
        # Label なので、短い「含まれる」を許すとこれを掴みうる）。
        self.status_label = FakeLabel("Atk:330(+500)\nDef:0(+500)", parent=self)
        self.choice_button = FakeLabel("金は持って", parent=self)
        if nested:
            inner = FakeLayout([label])
            label.parent = inner
            self.children = [FakeLayout([inner]), self.status_label,
                             self.choice_button]
        else:
            self.text_label = label
            self.children = [label, self.status_label, self.choice_button]
        self._label = label

    def update_display_text(self, instance, value):
        """本物と同じく、ラベルに本文を入れるところまで。

        **完全一致では塗らない**（実測。GAME.md §2.3）。前に何かを足した形にする。
        """
        self._label.text = PAINT_PREFIX + value

    def update_label_height(self, *args):
        self.height_updates += 1
        self._label.height = self._label.texture_size[1]

    def show(self, text):
        """ゲームが本文を差し替えたときの流れ（プロパティ → 監視）。"""
        self.display_text = text
        self.update_display_text(self, text)

    def type_out(self, text):
        """1文字ずつ増える本物の出方（`InstantaleApp.add_text_display`）。"""
        for index in range(1, len(text) + 1):
            self.show(text[:index])


PRISTINE = FakeHUD.update_display_text


class FakeClock(object):
    def __init__(self):
        self.pending = []
        self.scheduled = 0

    def schedule_once(self, callback, timeout=0):
        self.scheduled += 1
        self.pending.append(callback)

    def tick(self):
        pending, self.pending = self.pending, []
        for callback in pending:
            callback(0)


CLOCK = FakeClock()


def install_fake_kivy():
    """mod は kivy を関数の中で遅延 import する。sys.modules に偽物を置く。"""
    kivy = types.ModuleType("kivy")
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = CLOCK
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = clock_mod


# ---------------------------------------------------------------- 偽ローダ
class FakeCtx(object):
    """`ctx.wrap` だけ本物と同じ形にする（第1引数 orig、第2引数 self）。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.wrapped = {}
        self.errors = []

    def wrap(self, target, **kw):
        def decorate(func):
            module_name, qualname = target.split(":")
            module = sys.modules[module_name]
            cls_name, method = qualname.split(".")
            cls = getattr(module, cls_name)
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

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        # 握り潰しの中で例外が出ていたらテストとしては失敗にしたい。
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_ui_text_spacing", MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install(mod, ctx):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。"""
    FakeHUD.update_display_text = PRISTINE
    mod.apply(ctx)


def close(value, expected, tolerance=0.01):
    return abs(value - expected) < tolerance


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    sys.modules["scripts.hud.new_hud"] = module

    ctx = FakeCtx(os.path.join(HERE, os.pardir, "out", "test", "text_spacing"))
    mod = load_mod()
    install(mod, ctx)
    check("hooked update_display_text",
          "scripts.hud.new_hud:InstanTaleHUD.update_display_text" in ctx.wrapped)
    scale, blanks = mod.LINE_SCALE, mod.BLANK_LINES

    # -- 属性から見つける ---------------------------------------------------
    hud = FakeHUD()
    hud.show(NARRATION)
    label = hud._label
    check("the label found from vars(hud) gets the tightened line height",
          close(label.line_height, LINE_HEIGHT * scale), label.line_height)
    check("the label is found even though its text is not exactly display_text",
          label.text.startswith(PAINT_PREFIX.replace("\n\n", "\n")), repr(label.text[:20]))
    check("the blank line between paragraphs is gone",
          label.text.count("\n\n") == 0 and label.text.count("\n") == 3, repr(label.text))
    check("the tightened line is closer than the game's",
          label.pitch() < LINE_HEIGHT * FONT_SIZE * GLYPH_TO_LINE)

    # -- ゲーム側の本文は触らない -------------------------------------------
    check("the game's own display_text is left alone",
          hud.display_text == NARRATION, repr(hud.display_text))
    check("nothing was dropped from the text but the blank lines",
          label.text == (PAINT_PREFIX + NARRATION).replace("\n\n", "\n"), repr(label.text))

    # -- 巻き添えを出さない -------------------------------------------------
    check("an unrelated label is not restyled",
          close(hud.status_label.line_height, LINE_HEIGHT), hud.status_label.line_height)
    check("a choice button whose text appears in the narration is not restyled",
          close(hud.choice_button.line_height, LINE_HEIGHT), hud.choice_button.line_height)

    # -- 1文字ずつ来ても縮み続けない ----------------------------------------
    hud = FakeHUD()
    hud.type_out(NARRATION)
    check("typing the text out one character at a time keeps one line height",
          close(hud._label.line_height, LINE_HEIGHT * scale), hud._label.line_height)

    # -- 高さの決め直しは1フレームに1回 --------------------------------------
    CLOCK.tick()                             # ここまでの予約を流してから数える
    before = CLOCK.scheduled
    hud = FakeHUD()
    hud.type_out(NARRATION)
    check("the height update is scheduled once, not once per character",
          CLOCK.scheduled - before == 1, CLOCK.scheduled - before)
    CLOCK.tick()
    check("the height is recomputed after the frame", hud.height_updates == 1,
          hud.height_updates)
    lines = hud._label.text.count("\n") + 1
    check("the height matches the tightened text",
          close(hud._label.height, lines * hud._label.pitch()), hud._label.height)

    # -- 注入し直しても二重に掛からない --------------------------------------
    hud = FakeHUD()
    hud.show(NARRATION)
    once = hud._label.line_height
    install(mod, FakeCtx(ctx.out_dir))       # MOD 側の控えは作り直される
    hud.show(NARRATION + "。")
    check("re-injecting does not scale the line height twice",
          close(hud._label.line_height, once), hud._label.line_height)

    # -- 木を降りて見つける -------------------------------------------------
    install(mod, ctx)
    hud = FakeHUD(nested=True)
    hud.type_out(NARRATION)
    check("a label that is only in the widget tree is found too",
          close(hud._label.line_height, LINE_HEIGHT * scale)
          and hud._label.text.count("\n\n") == 0, hud._label.line_height)

    # -- 設定を空欄にすると触らない ------------------------------------------
    mod.LINE_SCALE, mod.BLANK_LINES = None, None
    install(mod, ctx)
    hud = FakeHUD()
    hud.show(NARRATION)
    check("null settings leave both the spacing and the text alone",
          close(hud._label.line_height, LINE_HEIGHT)
          and hud._label.text == PAINT_PREFIX + NARRATION,
          (hud._label.line_height, repr(hud._label.text)))

    # -- 空行だけを残す設定 --------------------------------------------------
    mod.LINE_SCALE, mod.BLANK_LINES = scale, 1
    install(mod, ctx)
    hud = FakeHUD()
    hud.show("段落1\n\n\n\n段落2")
    check("BLANK_LINES=1 keeps exactly one empty line",
          hud._label.text == PAINT_PREFIX + "段落1\n\n段落2", repr(hud._label.text))
    mod.BLANK_LINES = blanks

    # -- ラベルが見つからないビルドでは何もしない ----------------------------
    install(mod, ctx)
    hud = FakeHUD()
    hud._label.text = "(このビルドは本文をラベルに入れない)"
    hud.update_display_text = lambda instance, value: None    # 本文を塗らない
    hud.show(NARRATION)
    check("a build that shows the text elsewhere is left untouched",
          close(hud._label.line_height, LINE_HEIGHT) and hud.height_updates == 0,
          hud._label.line_height)

    check("no exception was swallowed", not ctx.errors, ctx.errors[:1])

    print()
    if failures:
        print("FAILED: {}".format(", ".join(failures)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
