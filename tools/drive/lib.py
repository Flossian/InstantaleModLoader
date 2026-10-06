# -*- coding: utf-8 -*-
# ゲームの中で使う操作の部品。drive.py が手順より先に同じ名前空間へ読み込む（手順から import しない）。
import sys
import time

from kivy.base import EventLoop
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.input.providers.mouse import MouseMotionEvent

from instantale_modloader import ui



def mod(part):
    """読み込まれている MOD の本体のモジュール（`instantale_mod_<フォルダ名>`）。名前の一部で引く。無ければ None。"""
    for name, module in list(sys.modules.items()):
        if name.startswith("instantale_mod_") and "." not in name and part in name:
            return module
    return None


_ALIVE = sys.modules.setdefault("_drive_alive", type(sys)("_drive_alive")).__dict__.setdefault("items", [])


def app():
    return ui.find_app()


def hud():
    return ui.find_hud(app())


def _touch(points):
    w, h = Window._get_effective_size()
    first = points[0]
    touch = MouseMotionEvent("mouse", "drive_%f" % time.time(),
                             [first[0] / float(w), first[1] / float(h), "left"],
                             is_touch=True, type_id="touch")
    EventLoop.post_dispatch_input("begin", touch)
    for x, y in points[1:]:
        touch.move([x / float(w), y / float(h)])
        EventLoop.post_dispatch_input("update", touch)
    touch.update_time_end()
    EventLoop.post_dispatch_input("end", touch)


def click_at(x, y):
    _touch([(x, y)])


def click(widget):
    click_at(*widget.to_window(*widget.center))


def find(kind=None, text=None, root=None):
    out = []
    for node in ui.walk_widgets(root or Window):
        if kind and type(node).__name__ != kind:
            continue
        if text is not None and getattr(node, "text", None) != text:
            continue
        out.append(node)
    return out


def item_widget(name, owner=None):
    for node in ui.walk_widgets(Window):
        item = getattr(node, "item_instance", None)
        if type(node).__name__ == "InventoryItem" and getattr(item, "name", None) == name:
            if owner is None or getattr(item, "obtainer", None) is owner:
                return node
    return None


def press_choice(text):
    """画面に塗られている選択肢のボタンを、本物の入力で1つ押す。画面に無ければ False。

    以前は `to_display_buttons` から番号を引いて `on_button_press` を直に呼んでいた。
    それだと画面に塗られていない一覧でも押せてしまい、塗り損ねを見落とした（2026-10-05）。
    """
    if text not in choices():
        return False
    h = hud()
    hits = []
    for layout in ("button_layout", "right_button_layout"):   # 選択肢の左右の欄だけ（隠れた別の欄にも同じ文字がある）
        root = getattr(h, layout, None)
        if root is not None:
            hits += [n for n in find("Button", text, root=root) if not getattr(n, "disabled", False)]
    if not hits:
        return False
    click(hits[0])
    return True


def choices():
    """画面の選択肢の欄（左 button_layout・右 right_button_layout）のボタンの文字。データ（to_display_buttons）ではない。

    HUD の button_texts は古い値が残ることがあり、画面と食い違った（2026-10-05。画面は一覧なのに事務所の選択肢を返した）。
    """
    h = hud()
    out = []
    for layout in ("button_layout", "right_button_layout"):
        root = getattr(h, layout, None)
        if root is None:
            continue
        out += [n.text for n in reversed(find("Button", root=root)) if n.text]
    return out


def data_choices():
    return list(app().to_display_buttons or [])


def close_trade_window():
    h = hud()
    h.turnoff_window_visibility(h, h.visible_twin_inventory_data)
    h.on_backdrop_callback()


def idle():
    a = app()
    return (getattr(a, "is_button_enabled", False) and not getattr(a, "is_adding_text", False)
            and not getattr(a, "is_popup_window_opened", False))


def lawfulness():
    return {k: v.get("lawfulness") for k, v in (app().player.area_history or {}).items()
            if isinstance(v, dict) and v.get("lawfulness") != 10}


class Steps(object):
    """手順を順に走らせる。各手順は関数で、戻り値が数なら次までの秒、"wait" なら手が空くまで待つ。"""

    def __init__(self, say, steps, timeout=900):
        self.say, self.steps, self.started = say, list(steps), time.time()
        self.timeout = timeout

    def start(self):
        # Clock はメソッドを弱い参照で持つ。ここで握っておかないと予約ごと消える。
        _ALIVE.append(self)
        Clock.schedule_once(self._next, 0.1)

    def _next(self, _dt):
        if gameover():
            self.say("GAMEOVER")
            self.say("<done>")
            return
        if time.time() - self.started > self.timeout:
            self.say("TIMEOUT; choices {}".format(choices()))
            self.say("<done>")
            return
        if not self.steps:
            self.say("<done>")
            return
        step = self.steps.pop(0)
        try:
            result = step()
        except Exception:
            import traceback
            self.say(traceback.format_exc())
            self.say("<done>")
            return
        if result == "wait":
            Clock.schedule_once(self._wait, 1.0)
        else:
            Clock.schedule_once(self._next, float(result or 0.5))

    def _wait(self, _dt):
        if gameover():
            return self._next(0)
        if time.time() - self.started > self.timeout:
            return self._next(0)
        if idle():
            Clock.schedule_once(self._next, 1.0)
        else:
            Clock.schedule_once(self._wait, 1.0)


def trade_open():
    """売買の窓が開いているか。開いている間は is_popup_window_opened が立ったままなので、idle() は真にならない。"""
    data = getattr(hud(), "visible_twin_inventory_data", None) or {}
    return isinstance(data, dict) and bool(data.get("TorF"))


def settled():
    """手が空いた（売買の窓が開いていて本文が止まっているときも含める）。"""
    return idle() or (trade_open() and not getattr(app(), "is_adding_text", False))


GAMEOVER_TEXT = "あなたは死んだ..."


def gameover():
    """ゲームオーバーの画面か。この画面では is_button_enabled が偽のまま・in_battle も残るので、idle() は真にならない。"""
    return any(getattr(n, "text", None) == GAMEOVER_TEXT for n in ui.walk_widgets(Window))
