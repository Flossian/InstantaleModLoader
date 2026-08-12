# -*- coding: utf-8 -*-
"""121_ui_character_sheet をゲーム抜きで通す。

    python tools/tests/test_ui_character_sheet.py

偽の `scripts.hud.new_hud` / `InstanTaleHUD` / Kivy の Label と、実測に
合わせた偽のセーブ（エリア表・手配度・スキル・特性）を差し込んで、次を確認する。

  寸法     … 枠が窓に対する割合（PANEL_*）どおりに置かれる
  不変     … 何度開いても同じ寸法（開くたびに当て直しても膨らまない）
  追従     … 窓の大きさが変わると枠も付いてくる（開いている間だけ）
  他欄     … 既定値が画面下の情報欄・本文に食い込まない
  不動     … 立ち絵（character_image_right）には触らない
  閉        … 閉じた（寸法 0）ときは何もしない
  一度      … 足す箱は開き直しても増えない
  重なり    … 箱同士が重ならない（レイアウト表の検算）
  枠線      … 足した箱にはゲームの add_border で線が引かれる
  書体      … 足した箱の書体・文字の大きさを隣から写している
  手配度    … ダンジョンは出さない／記録の無い土地は出さない／id 順／上限
  規模      … 生のエリア表が優先され、無ければ BGM のパスから読む
  不明      … 規模が読めない土地は伏せない（ダンジョンと確定したものだけ落とす）
  詰め      … 見出しの次の行から始まり、行頭を下げない（2件目以降が入る）
  揃え      … こちらが書く箱は左上詰め・余白は手本と同じ
  スキル    … 辞書・配列・オブジェクトのどれでも名前が出る
  特性      … ゲームが空のまま置いている箱に入る（元は中央寄せ・広い余白）
  別人      … 人物欄に出ている名前が別人なら、その相手の値を出す

ゲーム側の作り（`212_probe_character_sheet` で採寸。窓 1920x1000）:
枠 810x497 `pos_hint {center_x:0.37, center_y:0.68}`、
`character_sheet_empty` は毎回 `text=''`、
`player.skills` は `{名前: {...}}`、`player.traits` は `[{'name':...}]`、
`player.area_history` は `{エリアid: {'lawfulness': 10, ...}}`。
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


MOD = find_mod("_ui_character_sheet")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def close(a, b, tol=0.51):
    return abs(a - b) <= tol


# ---------------------------------------------------------------- 実測値
SHEET_W, SHEET_H = 810.0, 497.0
FONT_SIZE = 23
FONT_NAME = "Assets/fonts/game.ttf"


WIN_WIDTH, WIN_HEIGHT = 1920.0, 1000.0

#: 画面下の情報欄の上端（実測。ここから下は既存の UI）。
BOTTOM_STRIP_TOP = 0.69

#: 立ち絵の見えている左端の目安（実測。ここから右は立ち絵）。
PORTRAIT_LEFT = 0.70


# ---------------------------------------------------------------- 偽 Kivy
class FakeWindow(object):
    width, height = WIN_WIDTH, WIN_HEIGHT
    bound = []

    @classmethod
    def bind(cls, **kwargs):
        cls.bound += list(kwargs.values())

    @classmethod
    def unbind(cls, **kwargs):
        for handler in kwargs.values():
            if handler in cls.bound:
                cls.bound.remove(handler)

    @classmethod
    def resize(cls, width, height):
        cls.width, cls.height = float(width), float(height)
        for handler in list(cls.bound):
            handler(cls, (cls.width, cls.height))


class FakeLabel(object):
    """Label のうち、この mod が触るところだけ。"""

    def __init__(self, text="", halign="left", valign="top", markup=False):
        self.text = text
        self.halign, self.valign, self.markup = halign, valign, markup
        self.size_hint = (1, 1)
        self.pos_hint = {}
        self.size = (0, 0)
        self.text_size = (None, None)
        self.font_name = None
        self.font_size = 15
        self.color = None
        self.padding = [0, 0, 0, 0]
        self.line_height = 1.0
        self.max_lines = 0
        self.shorten = False
        self.parent = None
        self.bound = []
        self.bordered = False

    def bind(self, **kwargs):
        self.bound += sorted(kwargs)


class FakeLayout(FakeLabel):
    """人物欄の入れ物。開閉は size と opacity の入れ替え（実測）。"""

    def __init__(self):
        FakeLabel.__init__(self)
        self.size = (0.0, 0.0)
        self.opacity = 0
        self.children = []

    def add_widget(self, widget, index=0):
        self.children.insert(index, widget)
        widget.parent = self

    def open(self):
        """ゲーム自身が開くときにやること（毎回この寸法を入れ直す）。"""
        self.size = (SHEET_W, SHEET_H)
        self.pos_hint = {"center_x": 0.37, "center_y": 0.68}
        self.opacity = 1

    def close(self):
        self.size = (0, 0)
        self.opacity = 0


def install_fake_kivy():
    kivy = types.ModuleType("kivy")
    uix = types.ModuleType("kivy.uix")
    label_mod = types.ModuleType("kivy.uix.label")
    label_mod.Label = FakeLabel
    core = types.ModuleType("kivy.core")
    window_mod = types.ModuleType("kivy.core.window")
    window_mod.Window = FakeWindow
    sys.modules.setdefault("kivy", kivy)
    sys.modules.setdefault("kivy.uix", uix)
    sys.modules.setdefault("kivy.core", core)
    sys.modules["kivy.uix.label"] = label_mod
    sys.modules["kivy.core.window"] = window_mod


# ---------------------------------------------------------------- 偽ゲーム
class FakeArea(object):
    def __init__(self, area_id, name, bgm=""):
        self.id = area_id
        self.name = name
        self.bgm = bgm
        self.nodes = {}


class FakeCharacter(object):
    def __init__(self, name, skills=None, traits=None, history=None):
        self.name = name
        self.skills = skills if skills is not None else {}
        self.traits = traits if traits is not None else []
        self.area_history = history if history is not None else {}


class FakeWorld(object):
    def __init__(self, areas, characters):
        self.areas = areas
        self.characters = characters


class FakeHUD(object):
    """人物欄を持つ HUD。名前は実測で確定したもの。"""

    def __init__(self):
        self.character_sheet_layout = FakeLayout()
        self.character_sheet_name = FakeLabel("エリス")
        self.character_sheet_basic_info = FakeLabel("31歳, 女, 60レベル")
        self.character_sheet_background = FakeLabel("出自:\n...")
        self.character_sheet_health_condition = FakeLabel("健康状態:\n特に問題はない。")
        self.character_sheet_attributes = FakeLabel("筋力: 30\n")
        self.character_sheet_empty = FakeLabel("")
        self.character_sheet_empty.halign = "left"
        self.character_sheet_empty.valign = "middle"      # 実測（中央寄せ）
        self.character_sheet_empty.padding = [46, 11, 46, 11]   # 実測（広い余白）
        self.character_image_right = FakeLabel("")
        self.character_image_right.pos_hint = {"center_x": 0.81, "center_y": 0.65}
        self.character_image_right.size = (1920.0, 763.0)
        self.visible_character_sheet_data = {"name": "エリス"}
        # 手本の見た目（足した箱が写す相手）
        self.character_sheet_health_condition.font_name = FONT_NAME
        self.character_sheet_health_condition.font_size = FONT_SIZE
        self.character_sheet_health_condition.color = [1, 1, 1, 1]
        self.character_sheet_health_condition.padding = [11, 11, 11, 11]

    def toggle_character_sheet_visibility(self, *args):
        layout = self.character_sheet_layout
        if layout.opacity:
            layout.close()
        else:
            layout.open()


class FakeApp(object):
    def __init__(self, world, player, world_dict=None):
        self.world = world
        self.player = player
        if world_dict is not None:
            self.world_dict = world_dict


class FakeCtx(object):
    def __init__(self):
        self.hooks = {}
        self.messages = []
        self.errors = []

    def wrap(self, target, **kwargs):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate

    def log(self, message, level="INFO"):
        self.messages.append(message)

    def log_exc(self, message):
        self.errors.append(message)


def load_mod(name="character_sheet_mod"):
    """本番と同じ形（**パッケージとして**）読み込む（`from . import sheet`）。"""
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def install(mod, ctx, hud, app):
    """`apply()` を呼び、ゲームの `toggle` を包んだものを返す。"""
    borders = []
    new_hud = types.ModuleType("scripts.hud.new_hud")
    new_hud.add_border = lambda widget: (borders.append(widget),
                                         setattr(widget, "bordered", True))
    sys.modules["scripts.hud.new_hud"] = new_hud

    import instantale_modloader.ui as ui_mod
    ui_mod.find_app = lambda: app

    mod.apply(ctx)
    hook = ctx.hooks[
        "scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility"]

    def toggle():
        hook(FakeHUD.toggle_character_sheet_visibility, hud)

    return toggle, borders


# ---------------------------------------------------------------- 題材
def sample_world():
    """実測の形に合わせたエリア表。roads / dungeons を混ぜてある。"""
    areas = {
        "0": FakeArea("0", "始まりの泥濘",
                      "Assets/sounds/musics/village/desolate/a.mp3"),
        "1": FakeArea("1", "鉄錆の砦",
                      "Assets/sounds/musics/village/eerie/b.mp3"),
        "2": FakeArea("2", "灰の交易都市", ""),          # bgm が空（実測）
        "9": FakeArea("9", "霧の湿地帯",
                      "Assets/sounds/musics/dungeons/eerie/c.mp3"),
        "12": FakeArea("12", "泥濘の通り", ""),           # 生の表でのみ dungeon
        "30": FakeArea("30", "訪れていない土地", ""),      # 記録が無い
    }
    history = {
        "0": {"residency": {}, "achievements": [], "lawfulness": 10},
        "1": {"residency": {}, "achievements": [], "lawfulness": 10},
        "2": {"residency": {}, "achievements": [], "lawfulness": -10},
        "9": {"lawfulness": 3},
        "12": {"lawfulness": 7},
    }
    raw = {"areas": {"0": {"size": "village", "name": "始まりの泥濘"},
                     "1": {"size": "village", "name": "鉄錆の砦"},
                     "2": {"size": "city", "name": "灰の交易都市"},
                     "9": {"size": "dungeon", "name": "霧の湿地帯"},
                     "12": {"size": "dungeon", "name": "泥濘の通り"},
                     "30": {"size": "town", "name": "訪れていない土地"}},
           "index": 3}
    player = FakeCharacter(
        "エリス",
        skills={"地響きの断罪": {"name": "地響きの断罪", "max_uses": 3}},
        traits=[{"name": "不屈の闘志", "severity": 5}],
        history=history)
    world = FakeWorld(areas, {"player": player})
    return FakeApp(world, player, raw), player


def run():
    install_fake_kivy()
    mod = load_mod()
    sheet = sys.modules["character_sheet_mod.sheet"] \
        if "character_sheet_mod.sheet" in sys.modules else None
    if sheet is None:
        from importlib import import_module
        sheet = import_module("character_sheet_mod.sheet")

    print("[枠] 広げても立ち絵は動かない")
    ctx, hud = FakeCtx(), FakeHUD()
    app, player = sample_world()
    toggle, borders = install(mod, ctx, hud, app)
    portrait = (tuple(hud.character_image_right.pos_hint.items()),
                hud.character_image_right.size)
    toggle()                                   # 開く
    layout = hud.character_sheet_layout
    def expected(window_width, window_height):
        return ((mod.PANEL_RIGHT - mod.PANEL_LEFT) * window_width,
                (mod.PANEL_BOTTOM - mod.PANEL_TOP) * window_height)

    want = expected(WIN_WIDTH, WIN_HEIGHT)
    check("幅が窓の割合どおり", close(layout.size[0], want[0]), (layout.size, want))
    check("高さが窓の割合どおり", close(layout.size[1], want[1]), (layout.size, want))
    check("中心を置き直している",
          close(layout.pos_hint["center_x"], (mod.PANEL_LEFT + mod.PANEL_RIGHT) / 2)
          and close(layout.pos_hint["center_y"],
                    1 - (mod.PANEL_TOP + mod.PANEL_BOTTOM) / 2, 0.001),
          layout.pos_hint)
    check("立ち絵に触っていない",
          (tuple(hud.character_image_right.pos_hint.items()),
           hud.character_image_right.size) == portrait)

    print("\n[他欄] 既定値が既存の UI に食い込まない")
    check("画面下の情報欄に被らない", mod.PANEL_BOTTOM <= BOTTOM_STRIP_TOP,
          mod.PANEL_BOTTOM)
    check("立ち絵に被らない", mod.PANEL_RIGHT <= PORTRAIT_LEFT, mod.PANEL_RIGHT)
    check("画面の中に収まる",
          0 <= mod.PANEL_LEFT < mod.PANEL_RIGHT <= 1
          and 0 <= mod.PANEL_TOP < mod.PANEL_BOTTOM <= 1)

    print("\n[追従] 窓の大きさが変わったら付いてくる")
    FakeWindow.resize(1280, 720)
    want = expected(1280, 720)
    check("リサイズで枠も変わる",
          close(layout.size[0], want[0]) and close(layout.size[1], want[1]),
          (layout.size, want))
    check("見張りは1つだけ", len(FakeWindow.bound) == 1, len(FakeWindow.bound))
    toggle()                                   # 閉じる
    FakeWindow.resize(1600, 900)
    check("閉じている間は触らない", layout.size == (0, 0), layout.size)
    toggle()                                   # 開き直す
    want = expected(1600, 900)
    check("開き直すと今の窓に合う",
          close(layout.size[0], want[0]) and close(layout.size[1], want[1]),
          (layout.size, want))
    FakeWindow.resize(WIN_WIDTH, WIN_HEIGHT)

    print("\n[繰り返し] 開き直しても膨らまない")
    for _round in range(5):
        toggle()                               # 閉じる
        toggle()                               # 開く
    want = expected(WIN_WIDTH, WIN_HEIGHT)
    check("寸法が変わらない",
          close(layout.size[0], want[0]) and close(layout.size[1], want[1]),
          layout.size)
    check("足した箱は増えない", len(layout.children) == len(sheet.ADDED),
          len(layout.children))
    check("枠線は一度だけ引く", len(borders) == len(sheet.ADDED), len(borders))

    print("\n[閉] 閉じたときは何もしない")
    toggle()                                   # 閉じる
    check("寸法は 0 のまま", layout.size == (0, 0), layout.size)
    check("握り潰した例外が無い", not ctx.errors, "\n".join(ctx.errors[:2]))
    toggle()                                   # 開き直す

    print("\n[中身] 手配度・スキル・特性")
    wanted = [box for box in layout.children if "手配度" in box.text]
    skills = [box for box in layout.children if box.text.startswith("スキル")]
    check("手配度の箱が在る", len(wanted) == 1, [b.text for b in layout.children])
    check("スキルの箱が在る", len(skills) == 1, [b.text for b in layout.children])
    text = wanted[0].text if wanted else ""
    check("ダンジョンを出さない", "霧の湿地帯" not in text and "泥濘の通り" not in text, text)
    check("非ダンジョンを出す",
          "始まりの泥濘：10" in text and "灰の交易都市：-10" in text, text)
    check("記録の無い土地を出さない", "訪れていない土地" not in text, text)
    check("id 順に並ぶ", text.index("始まりの泥濘") < text.index("鉄錆の砦")
          < text.index("灰の交易都市"), text)
    check("スキル名が出る", "地響きの断罪" in (skills[0].text if skills else ""),
          skills[0].text if skills else "")
    check("特性はゲームの空き箱に入る",
          "特性" in hud.character_sheet_empty.text
          and "不屈の闘志" in hud.character_sheet_empty.text,
          hud.character_sheet_empty.text)

    print("\n[詰め] 見出しの次の行から始まり、行頭を下げない")
    for box, label in ((wanted[0], "手配度"), (skills[0], "スキル"),
                       (hud.character_sheet_empty, "特性")):
        lines = box.text.splitlines()
        check("{}: 見出しの直後から始まる".format(label),
              len(lines) > 1 and lines[1].strip() != "", lines)
        check("{}: 行頭を下げない".format(label),
              all(line == line.lstrip() for line in lines), lines)
    check("スキルが2件でも入る",
          sheet.list_text(sheet.SKILLS_HEADING, ["A", "B"]).count("\n") == 2)

    print("\n[揃え] こちらが書く箱は左上詰め")
    for box, label in ((wanted[0], "手配度"), (skills[0], "スキル"),
                       (hud.character_sheet_empty, "特性")):
        check("{}: 左上詰め".format(label),
              box.halign == "left" and box.valign == "top",
              (box.halign, box.valign))
        check("{}: 余白は手本と同じ".format(label),
              box.padding == hud.character_sheet_health_condition.padding,
              box.padding)

    print("\n[書体] 足した箱は隣から写す")
    for box in layout.children:
        check("書体を写している ({})".format(box.text.splitlines()[0] if box.text else "?"),
              box.font_name == FONT_NAME and box.font_size == FONT_SIZE,
              (box.font_name, box.font_size))
        check("折り返しを寸法に束ねている", "size" in box.bound, box.bound)

    print("\n[規模] 生の表が無ければ BGM から読む")
    app2 = FakeApp(app.world, player)          # world_dict を持たないビルド
    ctx2, hud2 = FakeCtx(), FakeHUD()
    toggle2, _borders = install(mod, ctx2, hud2, app2)
    toggle2()
    text2 = "".join(box.text for box in hud2.character_sheet_layout.children
                    if "手配度" in box.text)
    check("BGM が dungeons の土地を落とす", "霧の湿地帯" not in text2, text2)
    check("BGM が village の土地を出す", "始まりの泥濘" in text2, text2)
    check("規模の読めない土地は伏せない", "灰の交易都市" in text2, text2)

    print("\n[別人] 人物欄に別の相手が出ているとき")
    other = FakeCharacter("カイル", skills={"影抜き": {"name": "影抜き"}},
                          traits=[{"name": "臆病"}],
                          history={"0": {"lawfulness": -3}})
    app.world.characters["71"] = other
    hud.visible_character_sheet_data = {"name": "カイル"}
    toggle()                                   # 閉じる
    toggle()                                   # 開き直す
    text3 = "".join(box.text for box in layout.children if "手配度" in box.text)
    skills3 = "".join(box.text for box in layout.children
                      if box.text.startswith("スキル"))
    check("その相手の手配度を出す", "始まりの泥濘：-3" in text3, text3)
    check("その相手のスキルを出す", "影抜き" in skills3, skills3)

    print("\n[表] 箱同士が重ならない")
    boxes = []
    for name, (size_hint, pos_hint) in sheet.BOXES.items():
        left = pos_hint.get("x", 0.0)
        if "y" in pos_hint:
            bottom = pos_hint["y"]
        else:
            bottom = pos_hint["center_y"] - size_hint[1] / 2.0
        boxes.append((name, left, bottom, left + size_hint[0], bottom + size_hint[1]))
    for i, one in enumerate(boxes):
        for other_box in boxes[i + 1:]:
            overlap = (one[1] < other_box[3] and other_box[1] < one[3]
                       and one[2] < other_box[4] and other_box[2] < one[4])
            check("{} と {} が重ならない".format(one[0], other_box[0]), not overlap,
                  (one, other_box))
    check("枠からはみ出さない",
          all(0 <= box[1] and box[3] <= 1.0 and 0 <= box[2] and box[4] <= 1.0
              for box in boxes), boxes)

    print("\n[上限] 行数の上限を超えない")
    many = {str(i): FakeArea(str(i), "土地{}".format(i), "") for i in range(60)}
    history = {str(i): {"lawfulness": 10} for i in range(60)}
    entries = sheet.wanted_entries(many, {}, history, max_lines=mod.MAX_WANTED_LINES)
    check("上限まで", len(entries) == mod.MAX_WANTED_LINES, len(entries))

    print("\n[入れ物] スキル・特性の形を決めつけない")
    check("辞書", sheet.names_of({"A": {"name": "A"}, "B": {}}) == ["A", "B"])
    check("配列（辞書の要素）",
          sheet.names_of([{"name": "不屈の闘志"}]) == ["不屈の闘志"])
    check("配列（文字列）", sheet.names_of(["素早さ"]) == ["素早さ"])

    class Named(object):
        name = "オブジェクト"

    check("オブジェクト", sheet.names_of([Named()]) == ["オブジェクト"])
    check("空", sheet.names_of(None) == [] and sheet.names_of({}) == [])

    check("握り潰した例外が無い（通し）", not ctx.errors and not ctx2.errors,
          "\n".join((ctx.errors + ctx2.errors)[:2]))

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
