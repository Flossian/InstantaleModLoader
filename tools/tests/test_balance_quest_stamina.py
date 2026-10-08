# -*- coding: utf-8 -*-
"""142_balance_quest_stamina をゲーム抜きで通す。

    python tools/tests/test_balance_quest_stamina.py

確認するもの:

  割合     … 易しい・適正・上位と、その間の直線
  クリア   … ゲームの引き算の後に、クリア前のスタミナから決め直した量を引いた値へ置き直す
  被弾     … 依頼の最中の保存ごとに減った分を足す（回復で減らない）。数えていなくても最大 HP の不足を使う
  同行者   … 本人のレベルと被弾で決める。PARTY_TOO を切るとゲームのまま
  後始末   … exhausted を上限の半分以下で立て直し、update_max_hp を呼ぶ。ゲームが引かなかった人には触らない
  ゲームの値 … 設定がすべてゲームの値なら何も包まない
"""
import importlib.util
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "142_balance_quest_stamina")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "balance_quest_stamina"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Character(object):
    def __init__(self, level, pi, max_pi, hp):
        self.experience_level = level
        self.physical_integrity = pi
        self.max_physical_integrity = max_pi
        self.current_hp = hp
        self.max_hp = hp
        self.exhausted = False
        self.max_hp_updates = 0

    def update_max_hp(self):
        self.max_hp_updates += 1


class World(object):
    def __init__(self, characters):
        self.characters = characters


class App(object):
    def __init__(self, party=True):
        self.player = Character(72, 49, 49, 1000)
        self.member = Character(60, 100, 100, 800)
        self.world = World({"7": self.member})
        self.party = ["7"] if party else []
        self.current_quest_data = None

    def save_game(self):
        return "saved"


class Manager(object):
    def __init__(self, app):
        self.app = app


def game_clear(manager):
    """ゲームの引き算（`round(今の値 − 上限/2)`）。主人公と同行者。"""
    app = manager.app
    people = [app.player] + [app.world.characters[m] for m in app.party]
    for character in people:
        character.physical_integrity = round(character.physical_integrity
                                             - character.max_physical_integrity / 2)
        character.exhausted = True
    app.current_quest_data = None
    return "cleared"


class FakeUI(object):
    def __init__(self, app, real):
        self._app = app
        self._real = real

    def find_app(self):
        return self._app

    def party_member_ids(self, app):
        return list(app.party)

    def __getattr__(self, name):
        return getattr(self._real, name)


class FakeCtx(object):
    _mod = "142_balance_quest_stamina"

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(**settings):
    name = "balance_quest_stamina_mod"
    sys.modules.pop(name, None)
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    spec = importlib.util.spec_from_file_location(name, os.path.join(MOD_DIR, meta["entry"]))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for key, value in settings.items():
        setattr(module, key, value)
    return module, meta


def setup(party=True, **settings):
    app = App(party=party)
    module, _meta = load_mod(**settings)
    module.ui = FakeUI(app, ml.ui)
    ctx = FakeCtx(OUT_DIR)
    setattr(sys, module.INSTALLED_MARK, False)    # 1プロセス1回の印を外す（毎回の記録を見るため）
    module.apply(ctx)
    return app, module, ctx


def start(app, ctx, difficulty, quest_id="1"):
    app.current_quest_data = {"id": quest_id, "difficulty": difficulty}
    ctx.hooks[QUEST_START](lambda self: "started", Manager(app))


def save(app, ctx):
    return ctx.hooks[SAVE_GAME](App.save_game, app)


def clear(app, ctx):
    return ctx.hooks[QUEST_END](game_clear, Manager(app))


QUEST_START = "__main__:QuestStartManager.start_quest"
QUEST_END = "__main__:QuestEndManager.execute"
SAVE_GAME = "__main__:InstantaleApp.save_game"

shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)

print("mod.json と定数")
module, meta = load_mod()
for key, row in meta["settings"].items():
    check("{} の既定値がコードと同じ".format(key), getattr(module, key) == row["default"],
          (getattr(module, key), row["default"]))

print("割合")
check("適正は FAIR", module.base_percent(0) == 33)
check("20 以上易しいと EASY", module.base_percent(-20) == 10 and module.base_percent(-60) == 10)
check("20 以上難しいと HARD", module.base_percent(20) == 50 and module.base_percent(45) == 50)
check("間は直線", abs(module.base_percent(-10) - 21.5) < 1e-9
      and abs(module.base_percent(10) - 41.5) < 1e-9)
check("引く量は四捨五入で少なくとも 1", module.spend_of(49, 0, 0)[0] == 16
      and module.spend_of(49, 20, 0)[0] == 25 and module.spend_of(3, -20, 0)[0] == 1)
check("被弾は最大 HP の半分で満額", module.spend_of(49, 0, 0.5)[0] == module.spend_of(49, 0, 3.0)[0] == 26)
check("満額までは比例", module.spend_of(49, 0, 0.25)[0] == 21)
module.DAMAGE_FULL_AT = 100
check("満額の点は設定で動く", module.spend_of(49, 0, 0.5)[0] == 21)
module.DAMAGE_FULL_AT = 50

print("クリア（適正・被弾なし）")
app, module, ctx = setup()
start(app, ctx, 72)
result = clear(app, ctx)
check("クリアの戻り値はそのまま", result == "cleared")
check("適正は上限の 33%（49 -> 33）", app.player.physical_integrity == 33,
      app.player.physical_integrity)
check("半分より多いので exhausted は下りる", app.player.exhausted is False)
check("最大 HP を組み直した", app.player.max_hp_updates == 1)
check("同行者は本人のレベル（60）で決める: 難易度72 は +12 で 43.2% -> 57",
      app.member.physical_integrity == 57, app.member.physical_integrity)
check("記録で落ちていない", not ctx.errors, ctx.errors)

print("易しい依頼と難しい依頼")
app, module, ctx = setup(party=False)
start(app, ctx, "1")
clear(app, ctx)
check("易しい依頼（難易度は文字列）: 49 -> 44", app.player.physical_integrity == 44,
      app.player.physical_integrity)
app.player.physical_integrity = 49
start(app, ctx, 99, quest_id="2")
clear(app, ctx)
check("難しい依頼: 49 -> 24、exhausted が立つ", app.player.physical_integrity == 24
      and app.player.exhausted is True, (app.player.physical_integrity, app.player.exhausted))

print("被弾")
app, module, ctx = setup(party=False)
start(app, ctx, 72)
app.player.current_hp = 700
save(app, ctx)
app.player.current_hp = 900          # 回復しても減った分は残る
save(app, ctx)
app.player.current_hp = 500
check("保存の戻り値はそのまま", save(app, ctx) == "saved")
clear(app, ctx)
# 減った合計 300 + 400 = 700 / 1000 は満額の 50% を超える -> +20% -> 53% of 49 = 25.97 -> 26
check("減った合計で上乗せ（回復を挟んでも足す。49 -> 23）", app.player.physical_integrity == 23,
      app.player.physical_integrity)

app, module, ctx = setup(party=False)
app.current_quest_data = {"id": "5", "difficulty": 72}   # 依頼の途中でロードした（始まりを見ていない）
app.player.current_hp = 800
clear(app, ctx)
# 最大 HP の不足 200 / 1000 -> 満額の 40% -> +8% -> 41% of 49 = 20.09 -> 20
check("数えていなくても最大 HP の不足を使う（49 -> 29）", app.player.physical_integrity == 29,
      app.player.physical_integrity)

print("同行者を切る")
app, module, ctx = setup(PARTY_TOO=False)
start(app, ctx, 72)
clear(app, ctx)
check("主人公は決め直す", app.player.physical_integrity == 33)
check("同行者はゲームのまま（100 -> 50）", app.member.physical_integrity == 50
      and app.member.max_hp_updates == 0)

print("ゲームが引かなかった")
app, module, ctx = setup(party=False)
start(app, ctx, 72)
ctx.hooks[QUEST_END](lambda self: "nothing", Manager(app))
check("スタミナに触らない", app.player.physical_integrity == 49 and app.player.max_hp_updates == 0)

print("ゲームの値")
app, module, ctx = setup(FAIR_PERCENT=50, EASY_PERCENT=50, HARD_PERCENT=50, DAMAGE_PERCENT=0)
check("何も包まない", ctx.hooks == {}, list(ctx.hooks))

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
