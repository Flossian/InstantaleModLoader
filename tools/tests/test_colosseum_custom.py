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


class FakeLLM(object):
    """`llm.watch_aliases` の代わり。見張らずに、その場で全部の送り口へ当てる。"""

    def watch_aliases(self, ctx, targets, install, **kw):
        for target in targets:
            install(target)
        return []


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
    module.llm = FakeLLM()
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
module.RANK_STEP_SCALE = 0.0
arena.config["current_phase"] = 8
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    lambda self, choice: None, Manager(app), "申し込む")
check("伸び0なら5試合目の頼み文も初戦の格", generate(ctx, arena, 44)["difficulty"] == 16)
check("焼いた格（揃えた後）が渡ってきたら組み直さない（二重に掛けない）",
      lvl(enemy_level, "normal", 16) == 17 and seen["difficulty"] == 16, seen)
check("揃える前の素の値が渡ってきたら揃える",
      lvl(enemy_level, "normal", 44) == 17 and seen["difficulty"] == 16, seen)
ctx.hooks["__main__:BattleStartManager.start_battle"](lambda self: None, Manager(app))
module.RANK_STEP_SCALE = 1.0
arena.config["current_phase"] = 4

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
battle = Manager(app)
result = hook(lambda self: called.append(1) or "checked", battle)
check("体力1で踏みとどまる", app.player.current_hp == 1, app.player.current_hp)
check("逃走と同じ終わり方を起こす",
      FakeEnd.made == [("init", "escaped"), ("execute", "")], FakeEnd.made)
check("ゲームの判定は通さず、決着したと返す（試合はもう終わっている）",
      result is True and not called, (result, called))
check("退いた一文が出る", app.texts and module.DEFEAT_TEXT in app.texts[-1], app.texts)

app.player.current_hp = -50
FakeEnd.made = []
result = hook(lambda self: "checked", battle)
check("同じ戦闘ではもう判定しない（決着したと返し続ける）",
      result is True and not FakeEnd.made and app.player.current_hp == -50,
      (result, FakeEnd.made))
result = hook(lambda self: "checked", Manager(app))
check("別の戦闘では同じ試合で2度は起こさない（素の判定へ）",
      result == "checked" and not FakeEnd.made, (result, FakeEnd.made))

# 切り上げた後もゲームの戦闘の繰り返しが敵の次の手を処理しようとして、消えた敵で落ちた（実機）。
fighting = Manager(app)
for step in module.AFTER_SURRENDER_STEPS:
    target = "__main__:BattlePhaseManager." + step
    check("切り上げた戦闘の " + step + " を飛ばす",
          ctx.hooks[target](lambda self, *a: "ran", battle) is None)
    check("よその戦闘の " + step + " は素通し",
          ctx.hooks[target](lambda self, *a: "ran", fighting) == "ran")

# 切り上げた後、敵の一撃の演出が予約した敵の欄の更新が畳まれた欄で 0 除算になる（実機）。
display = ctx.hooks[module.ENEMY_DISPLAY_TARGET]


def collapsed_panel(self, *args):
    raise ZeroDivisionError("float division by zero")


check("切り上げた直後の敵の欄の 0 除算は握る",
      display(collapsed_panel, object()) is None)
check("握ったことが残る", any("skipped an enemy panel update" in line for line in io.open(
    os.path.join(OUT_DIR, module.LOG_BASENAME), encoding="utf-8")))
check("普段の敵の欄の更新は素通し", display(lambda self: "drawn", object()) == "drawn")
module.SURRENDER_GUARD_SECONDS = -1
try:
    display(collapsed_panel, object())
    raised = False
except ZeroDivisionError:
    raised = True
check("切り上げから時間が経っていれば握らない（よその不具合を隠さない）", raised)
module.SURRENDER_GUARD_SECONDS = 10

print("ゲーム自身の逃走と同じ状態にしてから切り上げる")


class GameEnd(FakeEnd):
    """ゲームの逃走の終わり方: 預かった者を一覧へ戻す（実機の差 party 0 → 1、預かり 1 → 0）。"""

    def execute(self, choice_text):
        self.app.party.update(self.app.escaped_member_in_battle)
        self.app.escaped_member_in_battle.clear()
        return FakeEnd.execute(self, choice_text)


class ForgetfulEnd(FakeEnd):
    """預かりを戻さない終わり方（値の形が合わなかったときの受けを確かめる）。"""


def fall_in_arena(end_cls):
    """闘技場で倒れて、ゲームが主人公を一覧から外すところまで。"""
    module.ui.classes = {"BattleEndManager": end_cls}
    ctx.hooks["__main__:ColosseumMatchStart.execute"](
        lambda self, choice: None, Manager(app), "申し込む")
    app.in_battle = "normal"
    app.in_colosseum_battle = 1
    app.player.current_hp = -30
    app.current_enemy_dict = {"黄金の蒐集家": object()}
    app.escaped_member_in_battle = {}
    app.party = {"player": app.player}

    def game_removes(self, member_id):
        self.party.pop(member_id, None)

    ctx.hooks["__main__:InstantaleApp.remove_party_member"](game_removes, app, "player")
    return Manager(app)


battle = fall_in_arena(GameEnd)
check("倒れて外された時点では一覧に居ない", "player" not in app.party)
result = hook(lambda self: "checked", battle)
check("主人公がゲームの手で一覧に戻る", app.party.get("player") is app.player, app.party)
check("預かりは空に戻る", app.escaped_member_in_battle == {}, app.escaped_member_in_battle)
check("敵の一覧を空にする（出口を足す側が忙しいと見ない）", app.current_enemy_dict == {},
      app.current_enemy_dict)
lines = io.open(os.path.join(OUT_DIR, module.LOG_BASENAME), encoding="utf-8").read()
check("預けたことと戻ったことが残る",
      "handed the player to escaped_member_in_battle" in lines
      and "the player is back in the party" in lines, lines[-600:])

battle = fall_in_arena(ForgetfulEnd)
hook(lambda self: "checked", battle)
check("ゲームが戻さなければ控えた値で戻す", app.party.get("player") is app.player, app.party)
check("手で戻したことは WARN で残る", "put back by hand" in io.open(
    os.path.join(OUT_DIR, module.LOG_BASENAME), encoding="utf-8").read())

module.ui.classes = {"BattleEndManager": FakeEnd}

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

print("試合で誰も死なない描写")
arena = Facility(config={"current_phase": 4, "enemy_data": {}})
app = App(Player(arena))
module, ctx, manifest = fresh(app)
battle_send = ctx.hooks["scripts.llm.llm_manager_battle:send_request"]
manager_send = ctx.hooks["scripts.llm.llm_manager:send_request_with_no_structure"]
summarizer = ctx.hooks["scripts.llm.llm_manager:colosseum_battle_summarizer"]
for target in module.NARRATION_SEND_TARGETS:
    check("包む: " + target, target in ctx.hooks)


def sent(hook, manager_name, message, by_keyword=False):
    """送り口の包みを1回通し、`orig` が受け取った message を返す。"""
    seen = {}

    def orig(*args, **kwargs):
        seen["message"] = args[1] if len(args) >= 2 else kwargs.get("message")
        return "reply"

    if by_keyword:
        hook(orig, manager_name=manager_name, message=message)
    else:
        hook(orig, manager_name, message)
    return seen["message"]


def ask():
    return [{"role": "system", "content": "審判"}, {"role": "user", "content": "手"}]


app.in_battle = app.in_colosseum_battle = 1
original = ask()
check("既定（OFF）では審判に足さない",
      sent(battle_send, "referee_player_attack_new_new", original) is original)

module.NONLETHAL_NARRATION = True
got = sent(battle_send, "referee_player_attack_new_new", original)
check("闘技場の審判の最後の user に足す",
      got[-1]["content"].endswith(module.NONLETHAL_NOTE) and got[0] == original[0], got)
check("呼び出し元の message は書き換えない", original == ask(), original)
check("敵の手の審判にも足す",
      module.NONLETHAL_MARK in sent(battle_send, "referee_enemy_new", ask())[-1]["content"])
check("keyword で渡されても足す",
      module.NONLETHAL_MARK in sent(battle_send, "referee_npc", ask(),
                                    by_keyword=True)[-1]["content"])
check("二重には足さない（別名の包みが重なったとき）",
      sent(battle_send, "referee_npc", got) is got)
check("審判以外には足さない",
      sent(battle_send, "conversation_starter", original) is original)

app.in_colosseum_battle = 0
check("依頼の戦闘の審判には足さない",
      sent(battle_send, "referee_player_attack_new_new", original) is original)
check("闘技場の外の要約（衛兵戦）には足さない",
      sent(manager_send, "guard_battle_summarizer", original) is original)
app.in_colosseum_battle = 1
escaped = sent(manager_send, "guard_battle_summarizer", ask())
check("闘技場で逃げた（負けて切り上げた）試合の締めには足す",
      escaped[-1]["content"].endswith(module.NONLETHAL_SUMMARY_NOTE), escaped)
app.in_colosseum_battle = 0

inside = {}


def summarize(*args, **kwargs):
    inside["message"] = sent(manager_send, "guard_battle_summarizer", ask())
    return "要約"


check("試合の要約は素通しで返る", summarizer(summarize, "闘技場", "街") == "要約")
check("試合の要約の頼み（衛兵戦の名前で送られる）には足す",
      module.NONLETHAL_MARK in inside["message"][-1]["content"], inside)
check("要約が終われば印は下りる", module.NONLETHAL_MARK not in
      sent(manager_send, "guard_battle_summarizer", ask())[-1]["content"])


def broken(*args, **kwargs):
    raise RuntimeError("boom")


try:
    summarizer(broken)
except RuntimeError:
    pass
check("要約が落ちても印は下りる",
      sent(manager_send, "guard_battle_summarizer", original) is original)
lines = io.open(os.path.join(OUT_DIR, module.LOG_BASENAME), encoding="utf-8").read()
check("足した回はログに残る",
      "nonlethal: referee_player_attack_new_new (referee)" in lines
      and "nonlethal: guard_battle_summarizer (summary)" in lines, lines)
check("例外を漏らさない（描写）", not ctx.errors, ctx.errors)

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
