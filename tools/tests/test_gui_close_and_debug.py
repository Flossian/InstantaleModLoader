# -*- coding: utf-8 -*-
"""ローダの設定画面（`tools/gui.py`）の、閉じるときの窓の記憶とデバッグモードの切り替えを窓を開かずに通す。

    python tools/tests/test_gui_close_and_debug.py

  閉じる     … 最大化のまま開いて閉じても、gui.json に残る寸法は普通の大きさ（描く前の 1x1 ではない）
  デバッグ   … 未保存の編集があるとき、キャンセルと保存の失敗では切り替えず、一覧も作り直さない
               （作り直すと編集が消える）。保存できれば切り替える

`App` の関数を偽の self に当てて呼ぶ。ダイアログ・設定ファイル・窓は差し替える。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import gui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 閉じる
print("[閉じる] 最大化のまま閉じても普通の大きさを覚える")


class FakeMaster(object):
    """最大化中は画面いっぱい、普通に戻すと元の寸法を返す窓。"""

    def __init__(self, zoomed):
        self._state = "zoomed" if zoomed else "normal"
        self.destroyed = False

    def state(self, value=None):
        if value is None:
            return self._state
        self._state = value
        return None

    def update_idletasks(self):
        return None

    def geometry(self):
        return "2576x1426+0+0" if self._state == "zoomed" else "1180x760+40+30"

    def destroy(self):
        self.destroyed = True


saved_update = gui.update_config
for zoomed in (True, False):
    written = {}
    gui.update_config = lambda **values: written.update(values)
    try:
        master = FakeMaster(zoomed)
        # 描く前に取った寸法のまま（最大化のまま開くと `_on_configure` が一度も拾わない）
        fake = types.SimpleNamespace(geom="1x1+0+0" if zoomed else "1180x760+40+30",
                                     _tick=None, winfo_toplevel=lambda m=master: m,
                                     _info_width=lambda: 300)
        gui.App._close(fake)
    finally:
        gui.update_config = saved_update
    label = "最大化" if zoomed else "普通"
    check("{}のまま閉じると普通の寸法を残す".format(label),
          written.get("window") == "1180x760+40+30", written)
    check("{}: 最大化かどうかを残す".format(label),
          written.get("window_maximized") is zoomed, written)
    check("{}: 窓を閉じる".format(label), master.destroyed)


# ---------------------------------------------------------------- デバッグ
print("[デバッグ] キャンセルと保存の失敗では切り替えない")


class FakeVar(object):
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def toggle(answer, save_works):
    """未保存の編集がある状態でデバッグモードを入れる。`answer` はダイアログの答え（None はキャンセル）。"""
    calls = {"reload": 0, "flags": []}
    fake = types.SimpleNamespace(debug_var=FakeVar(True), debug_mode=False, dirty=True)

    def save():
        if save_works:
            fake.dirty = False

    fake.save = save
    fake.reload = lambda: calls.__setitem__("reload", calls["reload"] + 1)
    fake._set_status = lambda msg: None
    fake._settle_unsaved = types.MethodType(gui.App._settle_unsaved, fake)
    saved = (gui.messagebox.askyesno, gui.messagebox.askyesnocancel, gui.C)
    gui.messagebox.askyesno = lambda *a, **k: bool(answer)
    gui.messagebox.askyesnocancel = lambda *a, **k: answer
    # 本物の settings/loader.json は書かない
    gui.C = types.SimpleNamespace(load_flags_for_write=lambda _d: {},
                                  save_flags=lambda _d, flags: calls["flags"].append(dict(flags)))
    try:
        gui.App._toggle_debug_mode(fake)
    finally:
        gui.messagebox.askyesno, gui.messagebox.askyesnocancel, gui.C = saved
    return calls, fake


calls, fake = toggle(None, True)
check("**キャンセルなら切り替えず、一覧も作り直さない**",
      calls["reload"] == 0 and not calls["flags"], calls)
check("キャンセルしたらチェックを元に戻す", fake.debug_var.get() is False)
calls, fake = toggle(True, False)
check("**保存に失敗したら切り替えず、一覧も作り直さない**",
      calls["reload"] == 0 and not calls["flags"], calls)
calls, fake = toggle(True, True)
check("保存できれば切り替えて作り直す",
      calls["reload"] == 1 and calls["flags"] == [{"debug": True}], calls)
calls, fake = toggle(False, True)
check("「いいえ」は編集を捨てて切り替える",
      calls["reload"] == 1 and calls["flags"] == [{"debug": True}], calls)


print("\n失敗 {} 件".format(len(failures)))
for name in failures:
    print("  - " + name)
if failures:
    sys.exit(1)
print("すべて通った")
