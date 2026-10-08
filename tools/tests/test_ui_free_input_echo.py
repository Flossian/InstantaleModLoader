# -*- coding: utf-8 -*-
"""141_ui_free_input_echo をゲーム抜きで通す。

    python tools/tests/test_ui_free_input_echo.py

確認するもの:

  足す     … 入力欄の送信で `process_choice` が来たら、ゲームの処理より先に、名前を付けずに文だけを足す
  会話     … 送り先が会話なら足さない（ゲームが出す）
  ボタン   … 入力欄の送信の外で来た `process_choice` には足さない
  受け付けない … 送信の中で `process_choice` が来なければ何も足さず、次のボタンにも持ち越さない
  1回だけ  … 1回の送信の中で `process_choice` が2回来ても足すのは最初の1回
  空の文   … 空白だけの文は足さない
  壊れた値 … 足せなくてもゲームの処理は必ず呼ぶ
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "ui_free_input_echo"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Player:
    def __init__(self, name):
        self.name = name


class FreeInputStart:
    pass


class BattlePhaseManager:
    pass


class ConversationPhaseManager:
    pass


class ConversationInQuestPhase:
    pass


class App:
    def __init__(self, player=Player("ヴァルカ")):
        self.player = player
        self.events = []

    def add_text(self, context):
        self.events.append(("text", context))


class FakeCtx:
    _mod = "141_ui_free_input_echo"

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


path = os.path.join(MODS_DIR, "141_ui_free_input_echo", "ui_free_input_echo.py")
spec = importlib.util.spec_from_file_location("ui_free_input_echo", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ctx = FakeCtx(OUT_DIR)
mod.apply(ctx)
send_hook = ctx.hooks.get("__main__:InstantaleApp.on_text_input")
choice_hook = ctx.hooks.get("__main__:InstantaleApp.process_choice")
check("on_text_input と process_choice を包む",
      send_hook is not None and choice_hook is not None, list(ctx.hooks))


def orig_choice(app, function, choice_text=""):
    app.events.append(("choice", type(function).__name__, choice_text))
    return "chosen"


def press(app, manager, text):
    """ボタンの押下（入力欄の外）。"""
    return choice_hook(orig_choice, app, manager, text)


def send(app, manager, text, dispatch=1, keyword=False):
    """入力欄の送信。ゲームは中で `process_choice` を `dispatch` 回呼ぶ。"""
    def orig_send(self, instance):
        for _ in range(dispatch):
            if keyword:
                choice_hook(orig_choice, self, manager, choice_text=text)
            else:
                choice_hook(orig_choice, self, manager, text)
        return "sent"
    return send_hook(orig_send, app, object())


print("足す")
app = App()
result = send(app, FreeInputStart(), "  店の金庫をこじ開ける ")
check("名前を付けずに文だけをゲームの処理より先に足す",
      app.events == [("text", "店の金庫をこじ開ける"),
                     ("choice", "FreeInputStart", "  店の金庫をこじ開ける ")], app.events)
check("ゲームの返り値をそのまま返す", result == "sent", result)
app = App()
send(app, BattlePhaseManager(), "防御に徹して身を守る", keyword=True)
check("戦闘でもキーワード渡しでも足す",
      app.events[0] == ("text", "防御に徹して身を守る"), app.events)

print("会話")
for manager in (ConversationPhaseManager(), ConversationInQuestPhase()):
    app = App()
    send(app, manager, "最近の試合で手強かった相手はいるか？")
    check("{} には足さない".format(type(manager).__name__),
          [e for e in app.events if e[0] == "text"] == [], app.events)

print("ボタン")
app = App()
result = press(app, FreeInputStart(), "自由入力")
check("入力欄の外の process_choice には足さない",
      app.events == [("choice", "FreeInputStart", "自由入力")] and result == "chosen",
      (app.events, result))

print("受け付けない")
app = App()
send(app, FreeInputStart(), "まだ待機中", dispatch=0)
press(app, FreeInputStart(), "出る")
check("送信が流れなければ足さず、次のボタンに持ち越さない",
      app.events == [("choice", "FreeInputStart", "出る")], app.events)

print("1回だけ")
app = App()
send(app, FreeInputStart(), "野宿する", dispatch=2)
check("足すのは最初の1回",
      [e for e in app.events if e[0] == "text"] == [("text", "野宿する")], app.events)

print("空の文")
app = App()
send(app, FreeInputStart(), "  \n")
check("空白だけなら足さない", [e for e in app.events if e[0] == "text"] == [], app.events)

print("壊れた値")
class Broken(App):
    def add_text(self, context):
        raise RuntimeError("cannot add")


app = Broken()
result = send(app, FreeInputStart(), "散策する")
check("足せなくてもゲームの処理は呼ぶ",
      app.events == [("choice", "FreeInputStart", "散策する")] and result == "sent",
      (app.events, result))

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
