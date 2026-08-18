# -*- coding: utf-8 -*-
"""109_fix_item_detail_autosize.py をゲーム抜きで通す。

    python tools/tests/test_item_detail_autosize.py

偽の `scripts.hud.new_hud` / `ItemDetailBox` / Kivy の
Label・Clock・Window を差し込んで、次を確認する。

  未配置   … 箱はホバーのたびに作り直され、子はまだレイアウトされていない
             （`pos` が箱の左下原点のまま）。この状態でも設計を読み違えない
  据え置き … 収まる長さでは設計どおり（箱 500、ラベル 50/225/150）で1px も動かない
  上端固定 … 高さが変わっても箱の上端は動かない（ゲームはグリッドの上端に合わせる）
  横幅     … 設計の縦横比（500:333）より縦長になるなら横にも広がる。左端は動かない
  余白     … 幅を変えても左右の余白は設計どおり（名前 17 / 説明 33）
  伸長     … 説明が長いと必要な高さまで伸び、切れずに収まる（実測の 108 文字）
  名前     … 名前が折り返す場合も同じく伸びる
  冪等     … `update_content` はマウス移動で何度も走る。3回呼んでも育たない
  整合     … 余白・隙間は設計どおりで、pos_hint の分数が重ならず箱に収まる
  頭打ち   … 窓に入らないほど長ければ窓の高さで止め、設計値より下には削らない
  収納     … 伸びた箱が画面外に出たら次のフレームで窓の内側へ戻る
  無傷     … ラベルが1つでも欠けていたら何もしない（ゲームの箱をそのまま返す）

寸法はすべて `out/item_detail.log`（`208_probe_item_detail` の実測、
window=2560x1387）から取っている。
折り返しの計算だけは Kivy の代わりに「幅 ÷ 半角1文字」で行う ― 実測が
300px 幅で半角24文字/行だったので、半角1文字 = 12.5px、
1行 = 50px（150px で3行）とすると実機と一致する。
"""
import importlib.util
import math
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


MOD = find_mod("_fix_item_detail_autosize")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 実測値
WINDOW = (2560, 1387)
BOX_SIZE = (333, 500)
BOX_POS = (1214.5, 630.725)
# (属性名, 高さ, text_size 幅, pos_hint の top)
DESIGN = (("name_label", 50, 316, 0.95),
          ("attributes_label", 225, 316, 0.85),
          ("desc_label", 150, 300, 0.40))

CHAR_W = 12.5      # 半角1文字（300px で24文字だった）
LINE_H = 50.0      # 1行（150px で3行だった）


# ---------------------------------------------------------------- 偽 Kivy
class FakeLabel(object):
    """Kivy の Label のうち、この mod が触るところだけ。"""

    def __init__(self, text, height, text_width, top):
        self.text = text
        self.height = height
        self.width = BOX_SIZE[0]
        self.size_hint = (None, None)
        self.text_size = [text_width, height]
        self.texture_size = [text_width, height]
        self.pos_hint = {"center_x": 0.5, "top": top}
        self.font_size = 27
        # **作られた直後はレイアウトされていない**（実測）。
        # 座標は箱の左下原点のまま ＝ 絶対座標から設計を読むと嘘の余白が出る。
        # ここを再現しておく。
        self.x = 0
        self.y = BOX_SIZE[1] * top - height

    def texture_update(self):
        width, height = self.text_size[0], self.text_size[1]
        per_line = max(1, int(width // CHAR_W))
        lines = sum(max(1, math.ceil(len(part) / per_line))
                    for part in self.text.split("\n"))
        # 高さを指定されていれば Kivy はそこに切り揃える（実測でも texture_size ==
        # text_size だった）。
        # None のときだけ本当の高さが出る。
        self.texture_size = [width, height if height is not None else lines * LINE_H]

    def wants(self):
        """このラベルが本当に必要としている高さ（判定用。mod は使わない）。"""
        keep = list(self.text_size)
        self.text_size = (keep[0], None)
        self.texture_update()
        needed = self.texture_size[1]
        self.text_size = keep
        self.texture_update()
        return needed


class FakeBox(object):
    def __init__(self, name="新しいアイテム", attributes="攻撃力: 500\n売価: 1\n",
                 description="", pos=None):
        self.width, self.height = BOX_SIZE
        self.size_hint = (None, None)
        self.x, self.y = pos if pos else BOX_POS
        texts = (name, attributes, description)
        for (attr, height, text_width, top), text in zip(DESIGN, texts):
            setattr(self, attr, FakeLabel(text, height, text_width, top))

    @property
    def size(self):
        return [self.width, self.height]

    @size.setter
    def size(self, value):
        self.width, self.height = value

    def labels(self):
        return [getattr(self, attr, None) for attr, _, _, _ in DESIGN
                if getattr(self, attr, None) is not None]

    def lay_out(self):
        """FloatLayout.do_layout と同じ計算で子を置く（`c.top = y + 分数 * h`）。

        ゲームは箱を作った直後に
        `update_content` を呼ぶので本番ではまだ走っていないが、**走った後の箱**でも同じ結果になることを確かめるために使う。
        """
        for label in self.labels():
            label.y = self.y + self.height * label.pos_hint["top"] - label.height
            label.x = self.x + self.width * label.pos_hint["center_x"] - label.width / 2

    def update_content(self, item):
        """本物と同じく、ラベルの text を入れ直すだけ（寸法は触らない）。

        ラベルを1つ落とした状態も試すので、**無い相手には何もしない**。
        本物がその形でも落ちないかどうかはここでは問題にしていない（mod が触らないことだけを見る）。
        """
        for attr, text in zip((name for name, _, _, _ in DESIGN),
                              (item["name"], item["attributes"], item["description"])):
            label = getattr(self, attr, None)
            if label is not None:
                label.text = text


class FakeClock(object):
    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, timeout=0):
        self.pending.append(callback)

    def tick(self):
        pending, self.pending = self.pending, []
        for callback in pending:
            callback(0)


class FakeWindow(object):
    width, height = WINDOW


CLOCK = FakeClock()


def install_fake_kivy():
    """mod は kivy を関数の中で遅延 import する。sys.modules に偽物を置く。"""
    kivy = types.ModuleType("kivy")
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = CLOCK
    core = types.ModuleType("kivy.core")
    window_mod = types.ModuleType("kivy.core.window")
    window_mod.Window = FakeWindow
    for name, module in (("kivy", kivy), ("kivy.clock", clock_mod),
                         ("kivy.core", core), ("kivy.core.window", window_mod)):
        sys.modules[name] = module


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

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        # 握り潰しの中で例外が出ていたらテストとしては失敗にしたい。
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())


def load_mod():
    spec = importlib.util.spec_from_file_location("mod_item_detail_autosize", MOD,
                                            submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 判定の道具
def item(name="新しいアイテム", attributes="item_detail: small_weapon\n攻撃力: 500\n売価: 1\n",
         description=""):
    return {"item_id": "item_0", "name": name,
            "attributes": attributes, "description": description}


def close(value, expected, tolerance=0.01):
    """1px 未満の差は不問。

    設計値は「実際に置かれている座標を引き算して求める」ので、
    実機でも 25.000000000000057 のような値になる。
    丸めて誤魔化すより、見た目に出ない差として扱うほうが実態に合う。
    """
    return abs(value - expected) < tolerance


def layout(box):
    """pos_hint の分数から、箱の中での (top, bottom) をピクセルで返す。"""
    out = []
    for label in box.labels():
        top = box.height * label.pos_hint["top"]
        out.append((top, top - label.height))
    return out


def fits(box):
    """どのラベルも、要求した高さぶんの text_size を与えられているか。"""
    return all(label.text_size[1] >= label.wants() - 0.5 for label in box.labels())


def run():
    install_fake_kivy()
    hud = types.ModuleType("scripts.hud.new_hud")
    hud.ItemDetailBox = FakeBox
    sys.modules["scripts.hud.new_hud"] = hud

    ctx = FakeCtx(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "item_detail"))
    mod = load_mod()
    mod.apply(ctx)
    check("hooked update_content",
          "scripts.hud.new_hud:ItemDetailBox.update_content" in ctx.wrapped)

    # -- 収まる長さでは設計どおり ------------------------------------------
    box = FakeBox()
    box.update_content(item(description="よく切れる短剣。"))
    CLOCK.tick()
    check("short text keeps the game's design",
          close(box.height, 500) and all(
              close(lb.height, designed)
              for lb, designed in zip(box.labels(), (50, 225, 150))),
          "{} {}".format(box.height, [lb.height for lb in box.labels()]))
    check("short text is not clipped", fits(box))

    check("short text keeps the designed width", close(box.width, 333), box.width)

    # -- 実測の 108 文字 ----------------------------------------------------
    long_desc = "test" * 27          # 108 文字。報告と同じ長さ
    box = FakeBox()
    box.update_content(item(description=long_desc))
    CLOCK.tick()
    check("long description grows the box",
          box.height > 500 or box.width > 333,
          "{}x{}".format(box.width, box.height))
    check("long description is not clipped", fits(box))
    check("other labels are untouched",
          [box.name_label.height, box.attributes_label.height] == [50, 225])

    check("the box keeps its top edge (the game anchors it there)",
          close(box.y + box.height, BOX_POS[1] + 500), box.y + box.height)
    check("the box keeps its left edge (the game anchors it there)",
          close(box.x, BOX_POS[0]), box.x)

    # 縦横比: 設計（500:333 ＝ 1.50）より縦長のままなら、
    # まだ広げる余地があったということ。
    # 上限（窓の右端 / 設計の3倍）に当たった場合だけ許す。
    ratio = 500 / 333.0
    room = min(333 * mod.WIDEST, WINDOW[0] - box.x)
    check("the box is not left taller than the design's proportions",
          box.height <= ratio * box.width + mod.RATIO_SLACK + 0.01
          or close(box.width, room),
          "{}x{} ratio={:.2f}".format(box.width, box.height, box.height / box.width))
    check("the margins the game designed are kept",
          all(close(box.width - label.text_size[0], margin)
              for label, margin in zip(box.labels(), (17, 17, 33))),
          [box.width - lb.text_size[0] for lb in box.labels()])

    # -- 余白・隙間・並び ---------------------------------------------------
    spans = layout(box)
    check("every label stays inside the box",
          all(0 <= bottom and top <= box.height + 0.01 for top, bottom in spans),
          spans)
    check("top margin is the designed 25", abs(box.height - spans[0][0] - 25) < 0.01,
          box.height - spans[0][0])
    check("labels do not overlap",
          all(spans[i][1] >= spans[i + 1][0] - 0.01 for i in range(len(spans) - 1)),
          spans)
    check("bottom margin is the designed 50", abs(spans[-1][1] - 50) < 0.01, spans[-1][1])

    # -- 冪等（マウスが動くたびに走る）--------------------------------------
    heights = [box.height] + [lb.height for lb in box.labels()]
    for _ in range(3):
        box.update_content(item(description=long_desc))
        CLOCK.tick()
    check("repeated calls do not grow the box",
          [box.height] + [lb.height for lb in box.labels()] == heights,
          [box.height] + [lb.height for lb in box.labels()])

    # -- 短い説明に戻したら設計値に戻る -------------------------------------
    box.update_content(item(description="短い。"))
    CLOCK.tick()
    check("going back to a short description restores the design",
          close(box.height, 500) and close(box.desc_label.height, 150),
          "{} {}".format(box.height, box.desc_label.height))

    # -- レイアウト済みの箱でも同じ結果になる -------------------------------
    # 本番で来るのは未配置の箱（上のケース）。
    # こちらは「座標を読んでいたら結果が変わる」ことを示す対照で、
    # 2つが一致することが設計を pos_hint から読んでいる証拠になる。
    fresh = FakeBox()
    fresh.update_content(item(description=long_desc))
    CLOCK.tick()
    settled = FakeBox()
    settled.lay_out()                      # 一度レイアウトされた状態にしてから
    settled.update_content(item(description=long_desc))
    CLOCK.tick()
    check("a laid-out box gives the same layout as a fresh one",
          close(fresh.height, settled.height) and all(
              close(a.pos_hint["top"], b.pos_hint["top"])
              for a, b in zip(fresh.labels(), settled.labels())),
          "{} vs {}".format(fresh.height, settled.height))

    # -- 長い名前 -----------------------------------------------------------
    box = FakeBox()
    box.update_content(item(name="とても" * 20))
    CLOCK.tick()
    check("a long name grows too", box.name_label.height > 50, box.name_label.height)
    check("a long name is not clipped", fits(box))

    # -- 窓に入らないほど長い -----------------------------------------------
    box = FakeBox()
    box.update_content(item(description="test" * 400))
    CLOCK.tick()
    check("an absurd description stops at the window height",
          box.height <= WINDOW[1], box.height)
    check("the cap never cuts below the design",
          box.desc_label.height >= 150, box.desc_label.height)

    # -- 画面外へ出さない ---------------------------------------------------
    box = FakeBox(pos=(2400, 1300))       # 窓の右上いっぱい。伸ばせばはみ出す
    box.update_content(item(description=long_desc))
    CLOCK.tick()
    check("the grown box is pulled back inside the window",
          0 <= box.y and box.y + box.height <= WINDOW[1]
          and 0 <= box.x and box.x + box.width <= WINDOW[0],
          "pos=({}, {}) size={}".format(box.x, box.y, box.size))

    # -- ラベルが欠けているビルドでは何もしない ------------------------------
    box = FakeBox()
    del box.desc_label
    before = box.height
    box.update_content(item(description=long_desc))
    CLOCK.tick()
    check("a missing label leaves the box exactly as the game made it",
          close(box.height, before), box.height)

    check("no exception was swallowed", not ctx.errors, ctx.errors[:1])

    print()
    if failures:
        print("FAILED: {}".format(", ".join(failures)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
