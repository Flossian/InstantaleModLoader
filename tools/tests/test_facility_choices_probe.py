# -*- coding: utf-8 -*-
"""232_probe_facility_choices をゲーム抜きで通す。

    python tools/tests/test_facility_choices_probe.py

確認するもの:

  記録   … 施設へ入った直後に、`choices` の型と順・ボタンの並び・ハッシュの枠が1行で残る
  素通り … `orig` の戻り値をそのまま返す。施設が無くても落ちない
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
                                        "facility_choices_probe"))

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
    _mod = "232_probe_facility_choices"

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


class Facility:
    def __init__(self, name, facility_type, choices):
        self.name = name
        self.facility_type = facility_type
        self.choices = choices
        self.id = "7"


class MovePhaseManager:
    def __init__(self, app):
        self.app = app

    def move_phase(self):
        return "moved"


def setup():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    folder = os.path.join(MODS_DIR, "232_probe_facility_choices")
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location("probe_facility_choices_mod",
                                                  os.path.join(folder, entry))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


module, ctx = setup()
if True:
    hook = ctx.hooks["__main__:MovePhaseManager.move_phase"]

    def log_lines():
        path = os.path.join(OUT_DIR, "facility_choices.log")
        if not os.path.exists(path):
            return []
        with io.open(path, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if line.strip()]

    print("宿屋へ入る")
    inn = Facility("霧隠れの宿", "inn", {"出る", "宿泊する"})
    app = types.SimpleNamespace(
        player=types.SimpleNamespace(location=inn),
        buttons=[button("出る", "MovePhaseManager", ["1", "2", "3"]),
                 button("宿泊する(3ヵ月)", "DisplayVacationChoice", [3]),
                 button("会話する", "DisplayTalkChoice", [])])
    result = hook(MovePhaseManager.move_phase, MovePhaseManager(app))
    check("orig の戻り値をそのまま返す", result == "moved", result)
    lines = log_lines()
    check("env の行（hash_randomization）", any("hash_randomization=" in l for l in lines), lines)
    check("hash の枠の行", any("hash slots" in l for l in lines), lines)
    moved = [l for l in lines if "move_phase:" in l]
    check("move_phase の行が1つ", len(moved) == 1, moved)
    check("choices の型が set と分かる", moved and "choices<set>=" in moved[0], moved)
    check("ボタンの並びと cls が入る",
          moved and "('出る', 'MovePhaseManager')" in moved[0]
          and "('宿泊する(3ヵ月)', 'DisplayVacationChoice')" in moved[0], moved)
    check("施設の属性名の行", any(l.startswith("[") and "attrs of a inn" in l for l in lines), lines)
    check("choices の属性値の行", any("  choices=" in l for l in lines), lines)

    print("2度目")
    hook(MovePhaseManager.move_phase, MovePhaseManager(app))
    lines = log_lines()
    check("env は1度だけ", sum(1 for l in lines if "hash_randomization=" in l) == 1, lines)
    check("move_phase の行は増える", sum(1 for l in lines if "move_phase:" in l) == 2)
    check("属性名の行は種類ごとに1度", sum(1 for l in lines if "attrs of a inn" in l) == 1)

    print("店（list の choices）")
    shop = Facility("雑貨店", "general_store", ["売買する", "出る"])
    app.player.location = shop
    app.buttons = [button("売買する", "ShoppingStartManagerRemake", []),
                   button("出る", "MovePhaseManager", ["1", "2", "3"])]
    hook(MovePhaseManager.move_phase, MovePhaseManager(app))
    last = [l for l in log_lines() if "move_phase:" in l][-1]
    check("list はそのままの順", "choices<list>=['売買する', '出る']" in last, last)

    print("施設が無い")
    app.player.location = None
    app.buttons = []
    result = hook(MovePhaseManager.move_phase, MovePhaseManager(app))
    check("落ちずに素通り", result == "moved" and not ctx.errors, ctx.errors)
    check("no facility の行", any("no facility under the player" in l for l in log_lines()))

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
