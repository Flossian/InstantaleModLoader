# -*- coding: utf-8 -*-
"""依頼から帰還すると日付が進む（`344_quest_return_days`）。

切っていれば何も包まないこと、進む日数が宿の宿泊日数と上限で決まること、
元の処理の後に進めること、片付けると移動する依頼には足さないこと、放棄では足すことを見る。
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import durations, ui  # noqa: E402

MOD_FILE = os.path.join(RUNTIME_DIR, "mods", "344_quest_return_days", "quest_return_days.py")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  {!r}".format(detail)))
    if not cond:
        failures.append(name)


class FakeCtx(object):
    def __init__(self):
        self.hooks = {}
        self.errors = []
        self.logs = []

    def log(self, text):
        self.logs.append(text)

    def logger(self, _name, tag=""):
        return lambda text: self.logs.append(text)

    def log_exc(self, text):
        self.errors.append(text)

    def out_path(self, name):
        return name

    def wrap(self, target, **_options):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


class FakeApp(object):
    def __init__(self):
        self.calls = []
        self.player = types.SimpleNamespace(age=20)
        self.world = types.SimpleNamespace(days_elapsed=100)

    def add_text(self, text):
        self.calls.append(("text", text))

    def elapse_days(self, days):
        self.world.days_elapsed += days
        self.calls.append(("elapse", days))

    def save_game(self):
        self.calls.append(("save", self.world.days_elapsed))


def load():
    spec = importlib.util.spec_from_file_location("quest_return_days", MOD_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def started(enabled=True, limit=30):
    mod = load()
    mod.ENABLED = enabled
    mod.MAX_DAYS = limit
    ctx = FakeCtx()
    mod.apply(ctx)
    return mod, ctx


print("切っているとき")
mod, ctx = started(enabled=False)
check("何も包まない", not ctx.hooks, ctx.hooks)
check("切れていることをログに出す", "quest return days: off" in ctx.logs, ctx.logs)

print("進む日数")
check("宿の日数のまま", mod.days_after_return(7, 30) == 7)
check("上限で止まる", mod.days_after_return(90, 30) == 30)
check("上限と同じ", mod.days_after_return(30, 30) == 30)

ui.current_quest_id = lambda _app: "28"
ui.game_day = lambda app: app.world.days_elapsed
durations.forget("road")

print("片付けて帰還")
mod, ctx = started()
app = FakeApp()
order = []


def original(self, *args, **kwargs):
    order.append(("orig", list(app.calls)))
    return "done"


manager = types.SimpleNamespace(app=app)
result = ctx.hooks[mod.QUEST_END_TARGET](original, manager)
check("元の戻り値を返す", result == "done", result)
check("元の処理より後に進める", order == [("orig", [])], order)
check("素の宿泊は上限の30日になる", ("elapse", 30) in app.calls, app.calls)
check("文 → 日数 → 保存の順",
      [row[0] for row in app.calls] == ["text", "elapse", "save"], app.calls)
check("進めた後の日付を保存する", ("save", 130) in app.calls, app.calls)
check("日数を文に出す", ("text", "帰還までに30日経過した。") in app.calls, app.calls)

print("宿泊期間を短くしているとき")
durations.declare(durations.INN_STAY, lambda _app: {"months": 1, "days": 7, "length": "1週間"},
                  owner="stay")
app = FakeApp()
ctx.hooks[mod.QUEST_END_TARGET](lambda self: None, types.SimpleNamespace(app=app))
check("宿の日数だけ進む", ("elapse", 7) in app.calls, app.calls)
durations.forget("stay")

print("上限を下げたとき")
mod, ctx = started(limit=10)
app = FakeApp()
ctx.hooks[mod.QUEST_END_TARGET](lambda self: None, types.SimpleNamespace(app=app))
check("設定の上限で止まる", ("elapse", 10) in app.calls, app.calls)

print("片付けると移動する依頼")
mod, ctx = started()
seen = []


def road(_app, quest_id):
    seen.append(quest_id)
    return quest_id == "28"


durations.declare_quest_travel("road", road)
app = FakeApp()
ctx.hooks[mod.QUEST_END_TARGET](lambda self: seen.append("orig"), types.SimpleNamespace(app=app))
check("元の処理より前に聞く", seen == ["28", "orig"], seen)
check("日付を進めない", not app.calls, app.calls)

print("放棄")
app = FakeApp()
result = ctx.hooks[mod.QUEST_RETIRE_TARGET](lambda self: "gone", types.SimpleNamespace(app=app))
check("元の戻り値を返す", result == "gone", result)
check("移動する依頼でも放棄なら進める", ("elapse", 30) in app.calls, app.calls)

print("別の依頼")
ui.current_quest_id = lambda _app: "29"
app = FakeApp()
ctx.hooks[mod.QUEST_END_TARGET](lambda self: None, types.SimpleNamespace(app=app))
check("移動しない依頼なら進める", ("elapse", 30) in app.calls, app.calls)
durations.forget("road")

print("日数送りが壊れていても依頼の終わりは止めない")


class BrokenApp(FakeApp):
    def elapse_days(self, days):
        raise RuntimeError("broken")


mod, ctx = started()
result = ctx.hooks[mod.QUEST_END_TARGET](lambda self: "done",
                                         types.SimpleNamespace(app=BrokenApp()))
check("戻り値はそのまま", result == "done", result)
check("例外はログへ", len(ctx.errors) == 1, ctx.errors)

print("OK" if not failures else "FAILED: {}".format(failures))
sys.exit(1 if failures else 0)
