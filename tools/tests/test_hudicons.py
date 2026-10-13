# -*- coding: utf-8 -*-
"""ローダの窓口 `hudicons`（画面左下の MOD 用のボタンと一覧）をゲーム抜きで通す。

    python tools/tests/test_hudicons.py

Kivy は読まない。偽の HUD（アイコンの列とゲームの4つのボタン）を差し込み、次を確認する。

  一覧     … 並びは order・key の順。名前は label、無ければ key。用済みの ctx の登録は並べない
  知らせ   … notify は登録の前でも効き、差し替えても残る。withdraw で消える
  置き方   … 登録があれば MOD 用のボタンを1つだけ、ゲームの4つの右に置く。v1.15.1 の形で MOD ごとに
              置いたボタンが残っていれば外す。登録が無くなれば外し、ゲームの4つを元の位置へ戻す
  枠       … 一覧の枠の大きさと、ボタンの真上に出して窓からはみ出さない位置

並べる比率（`positions` / `optical_positions` / `ink_columns`）の検査は `test_town_incidents.py` にある。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import hudicons, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  {}   {}{}".format("ok" if cond else "FAIL", name, "" if cond else " -- {}".format(detail)))
    if not cond:
        failures.append(name)


class FakeCtx:
    def __init__(self, generation=1, superseded=False):
        self.generation = generation
        self._superseded = superseded
        self.wrapped = []

    def superseded(self):
        return self._superseded

    def wrap(self, target, required=True, safe=False):
        def deco(fn):
            self.wrapped.append(target)
            return fn
        return deco


class FakeButton:
    """ゲームの `HoverableButton(普段, 乗せた時, 押した時, **kw)` の代わり。"""

    def __init__(self, normal="", hover="", click="", size_hint=(None, None), size=(40, 40),
                 pos_hint=None):
        self.normal_image, self.hover_image, self.click_image = normal, hover, click
        self.size = tuple(size)
        self.pos_hint = dict(pos_hint or {})
        self.parent = None
        self.bound = {}

    def bind(self, **kw):
        self.bound.update(kw)

    def unbind(self, **kw):
        for name in kw:
            self.bound.pop(name, None)


class FakeLayout:
    def __init__(self, width):
        self.width = width
        self.children = []

    def add_widget(self, widget):
        widget.parent = self
        self.children.append(widget)

    def remove_widget(self, widget):
        widget.parent = None
        self.children.remove(widget)

    def bind(self, **kw):
        pass


class FakeHud:
    def __init__(self, width=396.0):
        self.icon_button_layout = FakeLayout(width)
        self.icon_buttons = []
        for index in range(4):
            button = FakeButton("game{}.png".format(index),
                                pos_hint={"x": 0.07 + 0.22 * index, "y": 0.03})
            self.icon_button_layout.add_widget(button)
            self.icon_buttons.append(button)


def reset():
    if hasattr(sys, hudicons.STORE_ATTR):
        delattr(sys, hudicons.STORE_ATTR)
    hudicons._store()


# 並べ直しを呼ぶ口（add の「次のフレームで並べ直す」と HUD を探す口）は、偽の HUD へ向ける
HUD = [None]
ui.scheduler = lambda ctx, tag="mod": (lambda fn: None)
ui.find_app = lambda: None
ui.find_hud = lambda app: HUD[0]
ui.upx = lambda value: value

print("[一覧]")
reset()
ctx = FakeCtx()
pressed = []
hudicons.add(ctx, "339_town_factions", lambda app: pressed.append("339"), order=20, label="勢力と情勢",
             images=("flag.png", "flag_hover.png", "flag_click.png"))
hudicons.add(ctx, "338_town_incidents", lambda app: pressed.append("338"), order=10, label="各地の出来事")
hudicons.add(ctx, "zz_plain", lambda app: None)
entries = hudicons.menu_entries()
check("並びは order・key の順", [e["key"] for e in entries]
      == ["zz_plain", "338_town_incidents", "339_town_factions"], [e["key"] for e in entries])
check("名前は label、無ければ key", [e["label"] for e in entries] == ["zz_plain", "各地の出来事", "勢力と情勢"])
hudicons.add(FakeCtx(superseded=True), "old_generation", lambda app: None)
check("用済みの ctx の登録は並べない", "old_generation" not in [e["key"] for e in hudicons.menu_entries()])
try:
    hudicons.add(ctx, "bad", None)
    check("押したときの処理が無ければ断る", False)
except TypeError:
    check("押したときの処理が無ければ断る", True)

print("[知らせ]")
check("最初は印が無い", not hudicons.has_attention())
hudicons.notify("338_town_incidents")
check("印を付けると一覧に印がある", hudicons.has_attention())
hudicons.add(ctx, "338_town_incidents", lambda app: None, order=10, label="各地の出来事")
check("差し替えても印は残る", hudicons.has_attention())
hudicons.notify("338_town_incidents", False)
check("外すと消える", not hudicons.has_attention())
hudicons.notify("not_yet")
check("登録の前の印は一覧に数えない", not hudicons.has_attention())
hudicons.add(ctx, "not_yet", lambda app: None)
check("登録したら印が効く", hudicons.has_attention())
hudicons.withdraw("not_yet")
check("外した登録の印は消える", "not_yet" not in hudicons._store()["attention"])

print("[置き方]")
hud = FakeHud()
HUD[0] = hud
stale = FakeButton("old.png")
hud.icon_button_layout.add_widget(stale)
setattr(hud, hudicons.HUD_ATTR, {"338_town_incidents": stale})     # v1.15.1 の形で置いたボタン
hudicons.arrange(hud)
placed = getattr(hud, hudicons.HUD_ATTR)
check("MOD 用のボタンを1つだけ置く", list(placed) == [hudicons.ROOT_KEY]
      and len(hud.icon_button_layout.children) == 5, (list(placed), len(hud.icon_button_layout.children)))
check("前の形で MOD ごとに置いたボタンは外す", stale.parent is None)
root = placed[hudicons.ROOT_KEY]
check("ボタンの絵は同梱の3枚", (root.normal_image, root.hover_image, root.click_image)
      == hudicons.ROOT_IMAGES and all(os.path.isfile(p) for p in hudicons.ROOT_IMAGES))
xs = [b.pos_hint["x"] for b in hud.icon_buttons] + [root.pos_hint["x"]]
expected = hudicons.positions(0.07, 5, 396.0, 40.0)
check("ゲームの4つ・MOD 用のボタンの順に等間隔", all(abs(a - round(b, 4)) < 1e-9 for a, b in zip(xs, expected)),
      (xs, expected))
opened = []
real_open = hudicons.open_list
hudicons.open_list = lambda app, hud_, button: opened.append(button)
try:
    root.bound["on_release"](root)
finally:
    hudicons.open_list = real_open
check("押すと一覧を開く口につながる（ボタンの上に開く）", opened == [root], opened)
hudicons.arrange(hud)
check("何度並べても1つ", len(hud.icon_button_layout.children) == 5)
stale_follow = lambda *_a: None                         # 前の世代のモジュールが結んだ手の代わり（別の globals）
setattr(root, hudicons.BIND_ATTR, stale_follow)
root.bound["pos"] = stale_follow
hudicons.arrange(hud)
follow = getattr(root, hudicons.BIND_ATTR)
check("点を付いて来させる手は、前の世代のものなら結び直す", follow is not stale_follow
      and follow.__globals__ is vars(hudicons) and root.bound.get("pos") is follow, follow)
hudicons.arrange(hud)
check("今の世代の手なら結び直さない", getattr(root, hudicons.BIND_ATTR) is follow)
for key in ("zz_plain", "338_town_incidents", "339_town_factions"):
    hudicons.withdraw(key)
check("登録が無くなればボタンを外す", root.parent is None and len(hud.icon_button_layout.children) == 4)
check("ゲームの4つは元の位置", [b.pos_hint["x"] for b in hud.icon_buttons]
      == [0.07 + 0.22 * i for i in range(4)], [b.pos_hint for b in hud.icon_buttons])

print("[出すかをその場で聞く]")
reset()
shown = {"on": False}
hudicons.add(ctx, "339_town_factions", lambda app: None, label="勢力と情勢", visible=lambda app: shown["on"])
hudicons.add(ctx, "broken", lambda app: None, visible=lambda app: 1 / 0)
check("偽なら一覧に出さない（例外も出さない）", hudicons.menu_entries("app") == [])
hudicons.notify("339_town_factions")
check("出していない行の印は数えない", not hudicons.has_attention("app"))
hud = FakeHud()
HUD[0] = hud
hudicons.arrange(hud)
check("出す行が無ければ MOD 用のボタンも置かない", len(hud.icon_button_layout.children) == 4)
shown["on"] = True
hudicons.arrange(hud)
check("真になれば次に並べたときに出る", [e["key"] for e in hudicons.menu_entries("app")] == ["339_town_factions"]
      and len(hud.icon_button_layout.children) == 5 and hudicons.has_attention("app"))
try:
    hudicons.add(ctx, "bad", lambda app: None, visible=True)
    check("visible は関数だけ", False)
except TypeError:
    check("visible は関数だけ", True)

print("[知らせ] 周回ごとの印")
reset()
PLAYTHROUGH = ["世界A×主人公"]
real_key = hudicons._state_keys.playthrough_key
hudicons._state_keys.playthrough_key = lambda app: PLAYTHROUGH[0]
try:
    hudicons.add(ctx, "338_town_incidents", lambda app: None)
    hudicons.notify("338_town_incidents", playthrough="世界A×主人公")
    check("付けた周回では印がある", hudicons.has_attention("app"))
    PLAYTHROUGH[0] = "世界B×主人公"
    check("別の周回では印を出さない", not hudicons.has_attention("app"))
    hudicons.notify("338_town_incidents", False, playthrough="世界B×主人公")
    PLAYTHROUGH[0] = "世界A×主人公"
    check("別の周回で外しても、付けた周回の印は残る", hudicons.has_attention("app"))
    hudicons.notify("338_town_incidents", False, playthrough="世界A×主人公")
    check("付けた周回で外せば消える", not hudicons.has_attention("app")
          and "338_town_incidents" not in hudicons._store()["attention"])
    hudicons.notify("338_town_incidents")
    PLAYTHROUGH[0] = "世界B×主人公"
    check("周回を省いた印はどの周回でも出る", hudicons.has_attention("app"))
    hudicons.notify("338_town_incidents", False)
    check("周回を省いて外すと全部消える", not hudicons.has_attention("app"))
    setattr(sys, hudicons.STORE_ATTR, {"icons": {}, "generation": None, "write": None, "said": set(),
                                       "attention": {"old"}, "list": None})
    check("前の版の印（集合）は周回を問わない印へ移す",
          hudicons._store()["attention"] == {"old": {hudicons.ANY_PLAYTHROUGH}}, hudicons._store()["attention"])
finally:
    hudicons._state_keys.playthrough_key = real_key

print("[片付け] 登録した MOD を全部切って注入し直した")
reset()
old = FakeCtx(generation=1)
hudicons.add(old, "338_town_incidents", lambda app: None)
hud = FakeHud()
HUD[0] = hud
hudicons.arrange(hud)
check("前の世代では MOD 用のボタンがある", len(hud.icon_button_layout.children) == 5)
old._superseded = True                  # 次の世代の注入で、誰も add を呼ばなかった
ran = []
real_main = hudicons._on_main
hudicons._on_main = lambda fn: (ran.append(fn), fn())
try:
    gone = hudicons.settle()
finally:
    hudicons._on_main = real_main
check("用済みの登録を外す", gone == ["338_town_incidents"] and not hudicons._store()["icons"], gone)
check("MOD 用のボタンを外し、ゲームの4つを元の位置へ戻す", len(hud.icon_button_layout.children) == 4
      and [b.pos_hint["x"] for b in hud.icon_buttons] == [0.07 + 0.22 * i for i in range(4)],
      [b.pos_hint for b in hud.icon_buttons])
check("外すものが無ければ何もしない", hudicons.settle() == [])
hudicons.add(FakeCtx(generation=2), "339_town_factions", lambda app: None)
hudicons.arrange(hud)
hudicons.remove_all()
check("ローダを外すと登録を空にしてボタンも外す", not hudicons._store()["icons"]
      and len(hud.icon_button_layout.children) == 4)

print("[枠]")
width, height = hudicons.list_size(2, 40, 2, 8, 120.0, 28, 200, 60)
check("枠の大きさ（行の高さと間・余白）", (width, height) == (224.0, 98), (width, height))
check("名前が短くても最小の幅", hudicons.list_size(1, 40, 2, 8, 30.0, 0, 200, 60)[0] == 200)
check("ボタンの真上に出す", hudicons.list_position((300, 120), (224, 98), (1980, 1080), 4, 6) == (300, 126))
check("右にはみ出すなら左へ寄せる", hudicons.list_position((1900, 120), (224, 98), (1980, 1080), 4, 6)
      == (1752, 126))
check("上にはみ出すなら下へ寄せる", hudicons.list_position((300, 1050), (224, 98), (1980, 1080), 4, 6)
      == (300, 978))

print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
