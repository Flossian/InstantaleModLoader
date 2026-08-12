# -*- coding: utf-8 -*-
"""114_ui_input_focus をゲーム抜きで通す。

    python tools/test_ui_input_focus.py

偽の `scripts.hud.new_hud` / `InstanTaleHUD` / Kivy（Clock）と、焦点を本物と
同じように配る `FakeTextInput` を差し込んで、次を確認する。

  発見     … 入力欄を**型名でも属性名でもなく**持ち物（focus/insert_text）で見つける
  選別     … 欄が複数あるビルドでは、送信ボタンと同じ親に居るものを選ぶ
  戻す     … 焦点が外れたら次のフレームで入力欄に戻る
  Enter    … `on_text_validate`（Enter で送る経路）でも戻る
  応答待ち … 送信が塞がれている間は戻さない。解けた瞬間に戻る
  設定     … 「外れたら戻す」を切ると、送信の後だけ戻る
  遠慮     … 他の入力欄・ポップアップ・入力の封鎖中は焦点を奪わない
  歯止め   … ゲーム側と取り合いになったら手を引く（画面が固まらない）
  再注入   … 注入し直しても監視は1本のまま（古い版の手が残らない）
  無傷     … 入力欄が無いビルドでは何もしない
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


MOD = find_mod("_ui_input_focus")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ウィジェット
class FakeWidget(object):
    def __init__(self):
        self.parent = None
        self.children = []
        self.width = 0.0
        self.height = 0.0
        self.disabled = False


class Bindable(FakeWidget):
    """Kivy のプロパティ監視のうち、bind/unbind と配り方だけ本物に似せる。"""

    def __init__(self):
        FakeWidget.__init__(self)
        self.bound = {}

    def bind(self, **kwargs):
        for event, callback in kwargs.items():
            self.bound.setdefault(event, []).append(callback)

    def unbind(self, **kwargs):
        for event, callback in kwargs.items():
            handlers = self.bound.get(event, [])
            if callback in handlers:
                handlers.remove(callback)

    def dispatch(self, event, value=None):
        for callback in list(self.bound.get(event, [])):
            callback(self, value)

    def watchers(self, event):
        return len(self.bound.get(event, []))


class FakeTextInput(Bindable):
    """`TextInput`。`focus` を代入すると本物と同じく監視へ配る。"""

    def __init__(self, width=1000.0):
        Bindable.__init__(self)
        self.text = ""
        self._focus = False
        self.width = width

    @property
    def focus(self):
        return self._focus

    @focus.setter
    def focus(self, value):
        value = bool(value)
        if value == self._focus:
            return
        self._focus = value
        self.dispatch("focus", value)

    def insert_text(self, substring, from_undo=False):
        self.text += substring

    # -- 画面で起きること ----------------------------------------------------
    def blur(self):
        """送信ボタンを押した・別の場所を触った（Kivy が焦点を外す）。"""
        self.focus = False

    def validate(self):
        """Enter で送った（`on_text_validate`）。"""
        self.dispatch("on_text_validate")


class FakeSendButton(Bindable):
    """自由入力の送信ボタン。`disabled` の出入りが「応答待ち」の合図。"""

    def __init__(self):
        self._disabled = False          # 代入監視より先に持たせる（下の setter が読む）
        Bindable.__init__(self)
        self.text = "送信"

    @property
    def disabled(self):
        return self._disabled

    @disabled.setter
    def disabled(self, value):
        value = bool(value)
        if value == self._disabled:
            return
        self._disabled = value
        self.dispatch("disabled", value)


class FakeApp(object):
    """`InstantaleApp` のうち、この mod から見える部分だけ。"""

    def __init__(self):
        self.text_input_disabled = False
        self.is_popup_window_opened = False
        self.is_button_enabled = True


APP = FakeApp()


class FakeHUD(FakeWidget):
    def __init__(self, with_input=True, extra_input=False):
        FakeWidget.__init__(self)
        self.display_text = ""
        self.button_texts = []
        # 画面下の帯（入力欄と送信ボタンが同じ親に並ぶ）。
        self.bar = FakeWidget()
        self.bar.parent = self
        self.text_send_button = FakeSendButton()
        self.text_send_button.parent = self.bar
        self.bar.children = [self.text_send_button]
        self.children = [self.bar]
        if with_input:
            self.text_input = FakeTextInput()
            self.text_input.parent = self.bar
            self.bar.children.append(self.text_input)
        else:
            self.text_input = None
        # 名前入力の窓のように、別の入力欄が居るビルド（幅は広いが親が違う）。
        if extra_input:
            self.other_input = FakeTextInput(width=2000.0)
            self.other_input.parent = self
            self.children.append(self.other_input)
        else:
            self.other_input = None

    # -- ゲーム側の仕組み ---------------------------------------------------
    def update_display_text(self, instance, value):
        self.display_text = value

    def update_button_texts(self, instance, value):
        self.button_texts = list(value or [])

    # -- 画面が塗られる ------------------------------------------------------
    def show(self, text="宿の主人: いらっしゃい。"):
        self.update_display_text(self, text)

    def repaint(self):
        self.update_button_texts(self, ["会話する", "出る"])

    def send(self):
        """自由入力を送る（押下 → 焦点が外れ、応答待ちで入力が塞がる）。"""
        self.text_input.blur()
        self.text_send_button.disabled = True

    def replied(self):
        """応答が返ってきた（入力が解ける）。"""
        self.text_send_button.disabled = False
        self.show()


PRISTINE = (FakeHUD.update_display_text, FakeHUD.update_button_texts)


class FakeClock(object):
    def __init__(self):
        self.pending = []

    def schedule_once(self, callback, timeout=0):
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
    app_mod = types.ModuleType("kivy.app")

    class App(object):
        @staticmethod
        def get_running_app():
            return APP

    app_mod.App = App
    for name, module in (("kivy", kivy), ("kivy.clock", clock_mod),
                         ("kivy.app", app_mod)):
        sys.modules[name] = module


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

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        if level == "WARN":
            self.warnings.append(msg)

    def log_exc(self, msg):
        # 握り潰しの中で例外が出ていたらテストとしては失敗にしたい。
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_ui_input_focus", MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def install(mod, ctx):
    """注入をやり直す。**ラッパを重ねない**（本物は世代管理で置き換える）。"""
    FakeHUD.update_display_text, FakeHUD.update_button_texts = PRISTINE
    mod.apply(ctx)


def reset(mod):
    """設定を既定へ戻す（ローダはモジュールのグローバルへ書き込む。TECH.md §3.8）。"""
    mod.REFOCUS_ON_BLUR = True
    mod.REFOCUS_AFTER_SEND = True
    mod.REFOCUS_DELAY = 0.05
    APP.text_input_disabled = False
    APP.is_popup_window_opened = False


def run():
    install_fake_kivy()
    module = types.ModuleType("scripts.hud.new_hud")
    module.InstanTaleHUD = FakeHUD
    sys.modules["scripts"] = types.ModuleType("scripts")
    sys.modules["scripts.hud"] = types.ModuleType("scripts.hud")
    sys.modules["scripts.hud.new_hud"] = module
    # `__main__` に `InstantaleApp` が居ないと `ui.find_app()` は Kivy 側を見る。
    sys.modules["__main__"].InstantaleApp = FakeApp

    out_dir = os.path.join(HERE, os.pardir, "out", "_test_input_focus")
    ctx = FakeCtx(os.path.normpath(out_dir))
    mod = load_mod()
    reset(mod)
    install(mod, ctx)

    # -- 発見と監視 ---------------------------------------------------------
    print("\n[発見]")
    hud = FakeHUD()
    hud.show()
    check("入力欄を持ち物で見つけて監視を結ぶ", hud.text_input.watchers("focus") == 1,
          hud.text_input.bound)
    check("Enter の経路も監視する", hud.text_input.watchers("on_text_validate") == 1)
    check("送信ボタンの塞がりを監視する", hud.text_send_button.watchers("disabled") == 1)

    print("\n[選別]")
    two = FakeHUD(extra_input=True)
    two.show()
    check("送信ボタンと同じ親に居る欄を選ぶ（幅の広い別の欄に釣られない）",
          two.text_input.watchers("focus") == 1 and two.other_input.watchers("focus") == 0)

    # -- 戻す ---------------------------------------------------------------
    print("\n[戻す]")
    hud.text_input.focus = True
    hud.text_input.blur()
    check("外れた直後はまだ戻していない（次のフレームまで待つ）",
          hud.text_input.focus is False)
    CLOCK.tick()
    check("次のフレームで入力欄に戻る", hud.text_input.focus is True)

    hud.text_input.focus = False
    CLOCK.tick()
    hud.text_input.focus = False
    CLOCK.tick()
    check("何度外されても戻る", hud.text_input.focus is True)

    print("\n[Enter]")
    hud.text_input.focus = False
    CLOCK.pending = []
    hud.text_input.validate()
    CLOCK.tick()
    check("Enter で送った後も戻る", hud.text_input.focus is True)

    # -- 応答待ち -----------------------------------------------------------
    print("\n[応答待ち]")
    hud.send()
    CLOCK.tick()
    check("応答を待つ間は戻さない（ゲームが入力を塞いでいる）",
          hud.text_input.focus is False)
    hud.replied()
    CLOCK.tick()
    check("応答が返ったら戻る", hud.text_input.focus is True)

    # -- 設定 ---------------------------------------------------------------
    print("\n[設定]")
    mod.REFOCUS_ON_BLUR = False
    install(mod, ctx)
    quiet = FakeHUD()
    quiet.show()
    quiet.text_input.focus = True
    quiet.text_input.blur()
    CLOCK.tick()
    check("「外れたら戻す」を切ると、ただ外れただけでは戻らない",
          quiet.text_input.focus is False)
    quiet.text_send_button.disabled = True
    quiet.text_send_button.disabled = False
    CLOCK.tick()
    check("切っていても送信の後は戻る", quiet.text_input.focus is True)

    mod.REFOCUS_AFTER_SEND = False
    install(mod, ctx)
    silent = FakeHUD()
    silent.show()
    silent.text_send_button.disabled = True
    silent.text_send_button.disabled = False
    CLOCK.tick()
    check("両方切ると一切戻さない", silent.text_input.focus is False)
    reset(mod)
    install(mod, ctx)

    # -- 遠慮 ---------------------------------------------------------------
    print("\n[遠慮]")
    polite = FakeHUD(extra_input=True)
    polite.show()
    polite.other_input.focus = True
    polite.text_input.focus = False
    CLOCK.tick()
    check("他の入力欄に打っている最中は焦点を奪わない",
          polite.text_input.focus is False and polite.other_input.focus is True)
    polite.other_input.focus = False

    APP.is_popup_window_opened = True
    polite.text_input.blur()
    CLOCK.tick()
    check("別の窓が開いている間は戻さない", polite.text_input.focus is False)
    APP.is_popup_window_opened = False

    APP.text_input_disabled = True
    polite.text_input.blur()
    CLOCK.tick()
    check("ゲームが入力を塞いでいる間は戻さない", polite.text_input.focus is False)
    APP.text_input_disabled = False

    polite.text_input.disabled = True
    polite.text_input.blur()
    CLOCK.tick()
    check("欄そのものが無効なら戻さない", polite.text_input.focus is False)
    polite.text_input.disabled = False

    # -- 歯止め -------------------------------------------------------------
    print("\n[歯止め]")
    ring = FakeHUD()
    ring.show()
    # ゲーム側が「外す」を繰り返すビルド（取り合い）。戻すたびに外し返す。
    ring.text_input.focus = True
    for _round in range(mod.GUARD_LIMIT + 6):
        ring.text_input.blur()
        CLOCK.tick()
    check("取り合いになったら手を引く（画面が固まらない）",
          ring.text_input.focus is False, ring.text_input.focus)
    check("手を引いたことは警告に出る",
          any("standing down" in msg for msg in ctx.warnings), ctx.warnings)

    # -- 再注入 -------------------------------------------------------------
    print("\n[再注入]")
    again = FakeHUD()
    again.show()
    install(mod, ctx)
    again.show()
    check("注入し直しても焦点の監視は1本のまま",
          again.text_input.watchers("focus") == 1, again.text_input.bound)
    check("送信ボタンの監視も1本のまま",
          again.text_send_button.watchers("disabled") == 1)

    # -- 無傷 ---------------------------------------------------------------
    print("\n[無傷]")
    bare = FakeHUD(with_input=False)
    bare.show()
    bare.repaint()
    check("入力欄が無いビルドでは何もしない（落ちない）", True)
    check("入力欄が無いことは警告に出る",
          any("no text input" in msg for msg in ctx.warnings), ctx.warnings)

    check("握り潰した例外が無い", not ctx.errors, "\n".join(ctx.errors))

    print("\n" + ("FAILED: " + ", ".join(failures) if failures else "all ok"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
