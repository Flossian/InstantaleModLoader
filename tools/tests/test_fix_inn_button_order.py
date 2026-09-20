# -*- coding: utf-8 -*-
"""135_fix_inn_button_order をゲーム抜きで通す。

    python tools/tests/test_fix_inn_button_order.py

確認するもの:

  宿屋   … `出る / 宿泊する / 会話する` が `宿泊する / 出る / 会話する` になる。
           描く前に動かす（`orig` が受け取る並びが動かした後）
  素通り … 店（`売買する / 出る / 会話する`）・部屋選び・活動の選択肢・空・並びが既に正しい宿屋は触らない
  記録   … 動かしたときだけ、同じ並びは2度書かない
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "fix_inn_button_order"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)


def button(text, cls, args=()):
    return {"text": text, "spec": PhaseSpec(cls, args)}


class FakeCtx:
    _mod = "135_fix_inn_button_order"

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


def setup():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    folder = os.path.join(MODS_DIR, "135_fix_inn_button_order")
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location("fix_inn_button_order_mod",
                                                  os.path.join(folder, entry))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


module, ctx = setup()
hook = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"]
seen = []


def names(buttons):
    return [e.get("text") if isinstance(e, dict) else e
            for e in (buttons if isinstance(buttons, list) else [])]


def orig(self, *args, **kwargs):
    seen.append(names(self.buttons))
    return "drawn"


def run(buttons):
    app = types.SimpleNamespace(buttons=buttons)
    result = hook(orig, app, True)
    return names(app.buttons), result


def log_lines():
    path = os.path.join(OUT_DIR, "inn_button_order.log")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


print("宿屋: 出る / 宿泊する / 会話する -> 宿泊する / 出る / 会話する")
inn = [button("出る", "MovePhaseManager", ["33", "255", "8"]),
       button("宿泊する(3ヵ月)", "DisplayVacationChoice", [3]),
       button("会話する", "DisplayTalkChoice", [])]
texts, result = run(inn)
check("並びが直る", texts == ["宿泊する(3ヵ月)", "出る", "会話する"], texts)
check("orig は動かした後の並びを受け取る", seen[-1] == texts, seen[-1])
check("orig の戻り値をそのまま返す", result == "drawn")
check("記録が1行", len(log_lines()) == 1, log_lines())
texts, _ = run([button("出る", "MovePhaseManager", ["33", "255", "8"]),
                button("宿泊する(3ヵ月)", "DisplayVacationChoice", [3]),
                button("会話する", "DisplayTalkChoice", [])])
check("同じ並びは2度書かない", len(log_lines()) == 1, log_lines())
texts, _ = run([button("出る", "MovePhaseManager", ["33", "255", "8"]),
                button("宿泊する(1週間)", "DisplayVacationChoice", [3]),
                button("会話する", "DisplayTalkChoice", [])])
check("文言が違えば（315 の書き換え）もう1行", len(log_lines()) == 2, log_lines())
check("315 の文言でも spec で見るので直る", texts[0] == "宿泊する(1週間)", texts)

print("素通り")
for name, buttons in (
        ("店", [button("売買する", "ShoppingStartManagerRemake", []),
               button("出る", "MovePhaseManager", ["1", "2", "3"]),
               button("会話する", "DisplayTalkChoice", [])]),
        ("並びが正しい宿屋", [button("宿泊する(3ヵ月)", "DisplayVacationChoice", [3]),
                            button("出る", "MovePhaseManager", ["1", "2", "3"]),
                            button("会話する", "DisplayTalkChoice", [])]),
        ("部屋選び", [button("相部屋(10G)", "VacationStartManager", [3, "bunk"]),
                     button("個室(100G)", "VacationStartManager", [3, "private_room"]),
                     button("やめる", "JustSetButtonToNormalPhase", [])]),
        ("活動", [button("休養をとる", "VacationRestManager", [1, "bunk"]),
                 button("宿泊を終える", "VacationEndManager", [])]),
        ("街", [button("カレーム - 入口", "MovePhaseManager", ["1", "2", "3"]),
               button("霧隠れの宿", "MovePhaseManager", ["1", "2", "4"])]),
        ("空", [])):
    before = names(buttons)
    texts, result = run(buttons)
    check(name + " は触らない", texts == before, texts)
check("素通りでは記録が増えない", len(log_lines()) == 2, log_lines())

print("壊れた値")
texts, result = run([{"text": "x"}, "not a dict", button("出る", "MovePhaseManager", [])])
check("spec の無い項目が混じっても落ちない", result == "drawn" and not ctx.errors, ctx.errors)
app = types.SimpleNamespace(buttons=None)
check("buttons が無くても落ちない", hook(orig, app) == "drawn" and not ctx.errors, ctx.errors)

print("宿泊する が2つ（在れば元の順のまま前へ）")
texts, _ = run([button("出る", "MovePhaseManager", []),
                button("宿泊する(1)", "DisplayVacationChoice", [1]),
                button("会話する", "DisplayTalkChoice", []),
                button("宿泊する(2)", "DisplayVacationChoice", [2])])
check("2つとも出るの前", texts == ["宿泊する(1)", "宿泊する(2)", "出る", "会話する"], texts)

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
