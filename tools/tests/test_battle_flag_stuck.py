# -*- coding: utf-8 -*-
"""107_fix_battle_flag_stuck をゲーム抜きで通す。

    python tools/tests/test_battle_flag_stuck.py

確認するもの:

  撤退     … `BattleEndManager` の後に `in_colosseum_battle` が下りる
  素通り   … ゲームが既に下ろしている経路では1行も書かない
  ボス     … `in_boss_battle` は下ろさず、立っていたら記録だけ
  ロード   … 焼き付いた印を読み込み直後に下ろす
  巻き込み … 敵が居る（本物の戦闘中）注入では触らない
"""
import importlib.util
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "107_fix_battle_flag_stuck")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "battle_flag_stuck"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class App(object):
    def __init__(self, battle=0, colosseum=0, boss=0, enemies=None):
        self.in_battle = battle
        self.in_colosseum_battle = colosseum
        self.in_boss_battle = boss
        self.current_enemy_dict = {} if enemies is None else enemies


class Manager(object):
    def __init__(self, app):
        self.app = app


class FakeUI(object):
    def __init__(self, app, real):
        self._app = app
        self._real = real

    def find_app(self):
        return self._app

    def __getattr__(self, name):
        return getattr(self._real, name)


class FakeCtx(object):
    _mod = "107_fix_battle_flag_stuck"

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


def load_mod():
    name = "battle_flag_stuck_mod"
    sys.modules.pop(name, None)
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(MOD_DIR, manifest["entry"]))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, manifest


def fresh(app):
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    module, manifest = load_mod()
    module.ui = FakeUI(app, ml.ui)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx, manifest


def lines():
    path = os.path.join(OUT_DIR, "battle_bgm.log")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


print("配られる MOD として有効か")
module, ctx, manifest = fresh(App())
check("superseded で伏せていない", "superseded" not in manifest, manifest.get("superseded"))
check("debug で伏せてもいない", not manifest.get("debug"), manifest.get("debug"))
check("下ろすのは in_battle と in_colosseum_battle",
      module.CLEAR_FLAGS == ("in_battle", "in_colosseum_battle"), module.CLEAR_FLAGS)
check("in_boss_battle は記録だけ", module.REPORT_FLAGS == ("in_boss_battle",),
      module.REPORT_FLAGS)
for target in ("__main__:BattleEndManager.end_phase",
               "__main__:BattleEndInFreeAction.end_phase",
               "__main__:BattleEndInColosseum.end_phase",
               "__main__:InstantaleApp.load_game_new",
               "__main__:InstantaleApp.start_game"):
    check("包む: " + target, target in ctx.hooks, sorted(ctx.hooks))

print("闘技場から撤退した後（BattleEndManager）")
app = App(battle=0, colosseum=1)       # in_battle はゲームが下ろし、闘技場の印だけ残る
module, ctx, _manifest = fresh(app)
result = ctx.hooks["__main__:BattleEndManager.end_phase"](
    lambda self: "ended", Manager(app))
check("orig の戻りをそのまま返す", result == "ended", result)
check("闘技場の印が下りる", app.in_colosseum_battle is False, app.in_colosseum_battle)
check("下ろしたことが1行残る",
      any("cleared in_colosseum_battle" in line for line in lines()), lines())

print("会話からの戦闘（BattleEndInFreeAction）")
app = App(battle=1, colosseum=0)
module, ctx, _manifest = fresh(app)
ctx.hooks["__main__:BattleEndInFreeAction.end_phase"](lambda self: None, Manager(app))
check("戦闘中の印が下りる", app.in_battle is False, app.in_battle)
check("立っていない印は書かない",
      not any("in_colosseum_battle" in line for line in lines()), lines())

print("勝った試合（ゲームが既に下ろしている）")
app = App(battle=0, colosseum=0)
module, ctx, _manifest = fresh(app)
ctx.hooks["__main__:BattleEndInColosseum.end_phase"](lambda self: None, Manager(app))
check("1行も書かない（正しい経路には触れない）", not lines(), lines())

print("ボス戦の印")
app = App(battle=1, boss=1)
module, ctx, _manifest = fresh(app)
ctx.hooks["__main__:BattleEndManager.end_phase"](lambda self: None, Manager(app))
check("ボスの印は下ろさない", app.in_boss_battle == 1, app.in_boss_battle)
check("立っていたことは記録する",
      any("in_boss_battle still set" in line for line in lines()), lines())

print("ロード直後")
app = App(battle=1, colosseum=1)
module, ctx, _manifest = fresh(app)
ctx.hooks["__main__:InstantaleApp.load_game_new"](lambda self: None, app)
check("焼き付いた印を両方下ろす",
      app.in_battle is False and app.in_colosseum_battle is False,
      (app.in_battle, app.in_colosseum_battle))

print("注入した時点")
app = App(battle=1, colosseum=1)       # 敵が居ない＝残骸
module, ctx, _manifest = fresh(app)
check("残骸なら注入時に下ろす",
      app.in_battle is False and app.in_colosseum_battle is False,
      (app.in_battle, app.in_colosseum_battle))

app = App(battle=1, colosseum=1, enemies={"0": object()})   # 本物の戦闘中
module, ctx, _manifest = fresh(app)
check("敵が居るなら触らない", app.in_battle == 1 and app.in_colosseum_battle == 1,
      (app.in_battle, app.in_colosseum_battle))
check("触らなかった理由が残る",
      any("looks like a real battle" in line for line in lines()), lines())

print("値が読めないとき")
module, ctx, _manifest = fresh(None)
check("app が無くても落ちない",
      ctx.hooks["__main__:BattleEndManager.end_phase"](
          lambda self: "ok", Manager(None)) == "ok" and not ctx.errors, ctx.errors)

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
