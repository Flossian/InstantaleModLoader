# -*- coding: utf-8 -*-
"""334_colosseum_custom をゲーム抜きで通す。

    python tools/tests/test_colosseum_custom.py

確認するもの:

  素のまま … 設定を触らなければ難易度も所持金も文も1つも動かない
  強さ     … 初戦・伸び・上限・基準（土地／レベル／高いほう）が効く
  懸賞金   … 文の額と所持金の増えが必ず揃う。書き換えられなければ金も触らない
  負け     … 闘技場でだけ、1試合に1度だけ、倒れているときだけ逃走扱いで終える
  相手     … 既出の闘士を頼み文に足す。同じ一文が既にあれば二重にしない
"""
import importlib.util
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "334_colosseum_custom")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "colosseum_custom"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Facility(object):
    def __init__(self, name="漆黒斗技場", facility_type="colosseum", config=None,
                 description="石造りの闘技場。"):
        self.id = "161"
        self.name = name
        self.facility_type = facility_type
        self.config = config if config is not None else {}
        self.description = description


class Player(object):
    def __init__(self, location, gold=1000, level=20, hp=100):
        self.location = location
        self.gold = gold
        self.experience_level = level
        self.current_hp = hp
        self.is_player = True


class App(object):
    def __init__(self, player):
        self.player = player
        self.buttons = []
        self.texts = []
        self.in_battle = 0
        self.in_colosseum_battle = 0

    def add_text(self, context):
        self.texts.append(context)


class Manager(object):
    def __init__(self, app):
        self.app = app


class FakeUI(object):
    """本物の `ui` へ委譲し、`find_app` だけ偽の app を返す。"""

    def __init__(self, app, real):
        self._app = app
        self._real = real

    def find_app(self):
        return self._app

    def __getattr__(self, name):
        return getattr(self._real, name)


class FakeCtx(object):
    _mod = "334_colosseum_custom"

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
    name = "colosseum_custom_mod"
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
    hook = ctx.hooks["__main__:InstantaleApp.add_text"]
    app.add_text = lambda context: hook(App.add_text, app, context)
    return module, ctx, manifest


def generate(ctx, arena, difficulty, area=None, world=None):
    """敵を作る包みを1回通し、`orig` が実際に受け取った引数を返す。"""
    seen = {}

    def orig(location=None, area=None, world=None, npc_difficulty_level=None):
        seen.update(location=location, area=area, world=world,
                    difficulty=npc_difficulty_level)
        return {"data": {"name": "闘士", "rank": npc_difficulty_level}}

    ctx.hooks["scripts.llm.llm_manager:colosseum_enemy_generator"](
        orig, arena, area or {"name": "街"}, world or {"name": "世界"}, difficulty)
    return seen


# ---------------------------------------------------------------- 素のまま
print("設定が素のままなら何も動かない")
arena = Facility(config={"current_phase": 4, "enemy_data": {}})
app = App(Player(arena, gold=1000, level=20))
module, ctx, manifest = fresh(app)

for target in ("scripts.llm.llm_manager:colosseum_enemy_generator",
               "scripts.functions:get_enemy_exp_lvl",
               "scripts.functions:get_enemy_attributes_base_point",
               "__main__:ColosseumMatchStart.execute",
               "__main__:ColosseumMatchStart.generate_enemy_data",
               "__main__:BattleStartManager.start_battle",
               "__main__:EntryColosseumMatchManager.method",
               "__main__:BattlePhaseManager.check_battle_end",
               "__main__:BattleEndInColosseum.end_phase",
               "__main__:InstantaleApp.add_text"):
    check("包む: " + target, target in ctx.hooks, sorted(ctx.hooks))

declared = sorted(manifest["settings"])
missing = [name for name in declared if not hasattr(module, name)]
check("宣言した設定の定数が全部ある", not missing, missing)
check("既定値が mod.json と揃っている",
      all(getattr(module, name) == manifest["settings"][name]["default"]
          for name in declared),
      {name: (getattr(module, name), manifest["settings"][name]["default"])
       for name in declared
       if getattr(module, name) != manifest["settings"][name]["default"]})

for phase, raw in ((0, 30), (2, 44), (4, 58), (10, 97), (6, 71)):
    arena.config["current_phase"] = phase
    seen = generate(ctx, arena, raw)
    check("素のまま phase={} の難易度 {} を触らない".format(phase, raw),
          seen["difficulty"] == raw, seen["difficulty"])
arena.config["current_phase"] = 4
check("素のままなら頼み文も触らない（既出の闘士が居ない）",
      generate(ctx, arena, 58)["location"] is arena)
check("ログに rank: の行が無い",
      not any("rank:" in line for line in io.open(
          os.path.join(OUT_DIR, module.LOG_BASENAME), encoding="utf-8")
          if True) if os.path.exists(os.path.join(OUT_DIR, module.LOG_BASENAME)) else True)

# ------------------------------------------------------------------ 強さ
print("相手の強さ")
module.RANK_CAP = 40
seen = generate(ctx, arena, 58)
check("上限が効く", seen["difficulty"] == 40, seen["difficulty"])
seen = generate(ctx, arena, 30)
check("上限より弱い相手はそのまま", seen["difficulty"] == 30, seen["difficulty"])
module.RANK_CAP = 0

module.RANK_STEP_SCALE = 0.0
arena.config["current_phase"] = 4          # 3試合目（素なら 58、D は 61.9…）
seen = generate(ctx, arena, 58)
check("伸びを 0 にすると初戦の格のまま",
      seen["difficulty"] == module.plain_rank(module.difficulty_from(58, 2), 0),
      seen["difficulty"])
module.RANK_STEP_SCALE = 1.0

module.RANK_START_SCALE = 0.5
arena.config["current_phase"] = 0
seen = generate(ctx, arena, 30)
check("初戦を半分にする", seen["difficulty"] == 15, seen["difficulty"])
module.RANK_START_SCALE = 1.0

module.RANK_BASIS = module.BASIS_PLAYER
arena.config["current_phase"] = 0
seen = generate(ctx, arena, 30)
check("レベル基準（レベル20 なら格20）", seen["difficulty"] == 20, seen["difficulty"])
module.RANK_PLAYER_RATIO = 2.0
seen = generate(ctx, arena, 30)
check("レベルの倍率が効く", seen["difficulty"] == 40, seen["difficulty"])
module.RANK_PLAYER_RATIO = 1.0
module.RANK_BASIS = module.BASIS_HIGHER
seen = generate(ctx, arena, 30)
check("高いほう（土地30 とレベル20 なら 30）", seen["difficulty"] == 30, seen["difficulty"])
module.RANK_BASIS = module.BASIS_AREA

check("読めない難易度は触らない", generate(ctx, arena, None)["difficulty"] is None)
check("例外を漏らさない", not ctx.errors, ctx.errors)

# -------------------------------------------------- 敵の実際の強さ（窓の中だけ）
print("敵の実際の強さ")
seen = {}


def enemy_level(tier, difficulty):
    seen["difficulty"] = difficulty
    return difficulty + 1


lvl = ctx.hooks["scripts.functions:get_enemy_exp_lvl"]
module.RANK_CAP = 40
arena.config["current_phase"] = 4
check("窓の外（依頼の敵）には触らない",
      lvl(enemy_level, "normal", 58) == 59 and seen["difficulty"] == 58,
      seen)

ctx.hooks["__main__:ColosseumMatchStart.execute"](
    lambda self, choice: None, Manager(app), "申し込む")
check("窓の中では上限が効く", lvl(enemy_level, "normal", 58) == 41, seen)
check("能力値のほうにも同じ値が渡る",
      ctx.hooks["scripts.functions:get_enemy_attributes_base_point"](
          enemy_level, "normal", 58) == 41, seen)


def made(self, enemy_id):
    return {"type": "normal", "data": {"name": "闘士", "rank": 58}}


result = ctx.hooks["__main__:ColosseumMatchStart.generate_enemy_data"](
    made, Manager(app), "4")
check("施設に焼かれる格も揃う", result["data"]["rank"] == 40, result)

ctx.hooks["__main__:BattleStartManager.start_battle"](lambda self: None, Manager(app))
check("戦闘が始まれば窓は閉じる",
      lvl(enemy_level, "normal", 58) == 59 and seen["difficulty"] == 58, seen)

saved = module.WINDOW_SECONDS
module.WINDOW_SECONDS = 0
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    lambda self, choice: None, Manager(app), "申し込む")
check("窓は時間切れで失効する",
      lvl(enemy_level, "normal", 58) == 59 and seen["difficulty"] == 58, seen)
module.WINDOW_SECONDS = saved

module.RANK_CAP = 0
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    lambda self, choice: None, Manager(app), "申し込む")
check("素のままなら窓の中でも触らない",
      lvl(enemy_level, "normal", 58) == 59 and seen["difficulty"] == 58, seen)
ctx.hooks["__main__:BattleStartManager.start_battle"](lambda self: None, Manager(app))

# ---------------------------------------------------------------- 相手の顔ぶれ
print("同じ闘士が出てこないようにする")
arena.config["enemy_data"] = {"0": {"data": {"name": "灰燼の断罪者"}},
                              "2": {"data": {"name": "葬送の執行人"}}}
arena.config["current_phase"] = 4
seen = generate(ctx, arena, 58)
passed = seen["location"]
check("身代わりを渡す（本物には書かない）", passed is not arena, passed)
check("本物の概要は変わらない", "既に" not in arena.description, arena.description)
check("既出の闘士が頼み文に入る",
      "灰燼の断罪者" in passed.description and "葬送の執行人" in passed.description,
      passed.description)
check("施設の名前は素通し", passed.name == arena.name)

already = Facility(description="石造りの闘技場。\n" + module.VARIETY_NOTE.format(
    names="「先客」"), config=arena.config)
seen = generate(ctx, already, 58)
check("331 が先に足していたら二重にしない", seen["location"] is already)

module.VARY_OPPONENT = False
check("切れば足さない", generate(ctx, arena, 58)["location"] is arena)
module.VARY_OPPONENT = True
arena.config["enemy_data"] = {}

# ------------------------------------------------------------------ 懸賞金
print("懸賞金")


def win(app, ctx, paid=359, text="報酬として{}Gを貰った。"):
    """ゲームが `end_phase` の中で文を出して所持金を足す、その代わり。"""
    def body(self):
        app.add_text("受付: おめでとう！")
        app.add_text(text.format(paid))
        app.player.gold += paid
        return "ended"
    before = app.player.gold
    result = ctx.hooks["__main__:BattleEndInColosseum.end_phase"](body, Manager(app))
    return result, app.player.gold - before


app.texts = []
result, gained = win(app, ctx)
check("素のままなら素の額のまま", gained == 359 and result == "ended", (gained, result))
check("文も素のまま", app.texts[-1] == "報酬として359Gを貰った。", app.texts[-1])

module.REWARD_SCALE = 2.0
app.texts = []
result, gained = win(app, ctx)
check("倍率が所持金に効く", gained == 718, gained)
check("文の額も同じ", app.texts[-1] == "報酬として718Gを貰った。", app.texts[-1])
check("orig の戻りをそのまま返す", result == "ended", result)

app.texts = []
_result, gained = win(app, ctx, paid=1200, text="報酬として{:,}Gを貰った。")
check("桁区切りのある文も書き換える", app.texts[-1] == "報酬として2,400Gを貰った。",
      app.texts[-1])
check("桁区切りでも所持金が揃う", gained == 2400, gained)

app.texts = []
_result, gained = win(app, ctx, text="ほうびとして{}Gを渡された。")
check("目印の無い文は書き換えない（所持金も触らない）",
      gained == 359 and app.texts[-1] == "ほうびとして359Gを渡された。",
      (gained, app.texts[-1]))
module.REWARD_SCALE = 1.0

module.REWARD_PER_LEVEL = 0.02       # レベル20 → 1 + 0.02*19 = 1.38
app.texts = []
_result, gained = win(app, ctx)
check("レベル連動", gained == int(round(359 * 1.38)), gained)
module.REWARD_PER_LEVEL = 0.0

module.REWARD_PER_RANK = 0.01        # 相手の格 58 → 1 + 0.58
arena.config["current_phase"] = 4
arena.config["enemy_data"] = {"4": {"data": {"name": "闘士", "rank": 58}}}
app.texts = []
_result, gained = win(app, ctx)
check("相手の格に連動", gained == int(round(359 * 1.58)), gained)
module.REWARD_PER_RANK = 0.0
arena.config["enemy_data"] = {}

# ------------------------------------------------------------ 踏みとどまり
print("倒れたら負けとして試合を切り上げる")
hook = ctx.hooks["__main__:BattlePhaseManager.check_battle_end"]


class FakeEnd(object):
    """ゲームの `BattleEndManager` の代わり。作られた引数と `execute` を控える。"""

    made = []

    def __init__(self, app, end_type):
        self.app = app
        self.end_type = end_type
        FakeEnd.made.append(("init", end_type))

    def execute(self, choice_text):
        FakeEnd.made.append(("execute", choice_text))
        self.app.in_battle = 0
        self.app.in_colosseum_battle = 0
        return "ended"


module.ui.classes = {"BattleEndManager": FakeEnd}
module.ui.cls_of = lambda name: module.ui.classes.get(name)

app.in_colosseum_battle = 1
app.player.current_hp = -107
app.texts = []
FakeEnd.made = []
check("切ってあれば触らない",
      hook(lambda self: "checked", Manager(app)) == "checked"
      and app.player.current_hp == -107 and not FakeEnd.made,
      (app.player.current_hp, FakeEnd.made))

module.SURVIVE_DEFEAT = True
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    lambda self, choice: None, Manager(app), "申し込む")
app.in_colosseum_battle = 1
app.player.current_hp = -107
app.texts = []
FakeEnd.made = []
called = []
result = hook(lambda self: called.append(1) or "checked", Manager(app))
check("体力1で踏みとどまる", app.player.current_hp == 1, app.player.current_hp)
check("逃走と同じ終わり方を起こす",
      FakeEnd.made == [("init", "escaped"), ("execute", "")], FakeEnd.made)
check("ゲームの判定は通さない（試合はもう終わっている）",
      result is None and not called, (result, called))
check("退いた一文が出る", app.texts and module.DEFEAT_TEXT in app.texts[-1], app.texts)

app.player.current_hp = -50
FakeEnd.made = []
result = hook(lambda self: "checked", Manager(app))
check("同じ試合で2度は起こさない",
      result == "checked" and not FakeEnd.made and app.player.current_hp == -50,
      (result, FakeEnd.made))

print("終わり方を起こせないとき")
module.ui.classes = {}
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    lambda self, choice: None, Manager(app), "申し込む")
app.in_colosseum_battle = 1
app.player.current_hp = -20
result = hook(lambda self: "checked", Manager(app))
check("素の判定へ落とす", result == "checked", result)
check("それでもその一撃では死なない", app.player.current_hp == 1, app.player.current_hp)
check("WARN を残す",
      any("the match goes on" in line for line in io.open(
          os.path.join(OUT_DIR, module.LOG_BASENAME), encoding="utf-8")))
module.ui.classes = {"BattleEndManager": FakeEnd}

print("触らない場面")
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    lambda self, choice: None, Manager(app), "申し込む")
app.in_colosseum_battle = 0
app.player.current_hp = -50
FakeEnd.made = []
check("闘技場の外の戦闘では助けない",
      hook(lambda self: "checked", Manager(app)) == "checked"
      and app.player.current_hp == -50 and not FakeEnd.made,
      (app.player.current_hp, FakeEnd.made))
app.in_colosseum_battle = 1
app.player.current_hp = 100
check("生きているうちは触らない",
      hook(lambda self: "checked", Manager(app)) == "checked"
      and app.player.current_hp == 100 and not FakeEnd.made,
      (app.player.current_hp, FakeEnd.made))
module.SURVIVE_DEFEAT = False

# -------------------------------------------------------------- 格を告げる
print("申し込む前に相手の格を告げる")
arena.config["current_phase"] = 4
arena.config["enemy_data"] = {"4": {"data": {"name": "闘士", "rank": 58}}}
app.texts = []
result = ctx.hooks["__main__:EntryColosseumMatchManager.method"](
    lambda self: "spoken", Manager(app))
check("orig の戻りをそのまま返す", result == "spoken", result)
check("格の行が出る", app.texts and "次の相手は" in app.texts[-1], app.texts)
check("レベル20 に格58 なら危ないと言う",
      app.texts and "命がいくつあっても" in app.texts[-1], app.texts)

app.player.experience_level = 80
app.texts = []
ctx.hooks["__main__:EntryColosseumMatchManager.method"](
    lambda self: None, Manager(app))
check("釣り合っていれば普通の口上",
      app.texts and "命がいくつあっても" not in app.texts[-1], app.texts)
check("格の言い換えが入る（58 は歴戦の猛者）",
      app.texts and "歴戦の猛者" in app.texts[-1], app.texts)

module.ANNOUNCE_RANK = False
app.texts = []
ctx.hooks["__main__:EntryColosseumMatchManager.method"](
    lambda self: None, Manager(app))
check("切れば何も言わない", not app.texts, app.texts)
module.ANNOUNCE_RANK = True

print("闘技場でない施設・値が読めないとき")
app.player.location = Facility(name="霧隠れの宿", facility_type="inn", config={})
app.texts = []
ctx.hooks["__main__:EntryColosseumMatchManager.method"](
    lambda self: None, Manager(app))
check("宿屋では何も言わない", not app.texts, app.texts)
app.player.location = None
check("施設が無くても落ちない",
      generate(ctx, arena, 58)["difficulty"] == 58
      and hook(lambda self: "checked", Manager(app)) == "checked")
check("例外を漏らさない（通し）", not ctx.errors, ctx.errors)

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
