# -*- coding: utf-8 -*-
"""233_probe_colosseum をゲーム抜きで通す。

    python tools/tests/test_colosseum_probe.py

確認するもの:

  窓     … 申し込みから `end_phase` までが `match` 行1件になり、
            所持金の差・報酬の額・相手のランク・phase の前後が入る
  地点   … 所持金が動いた地点が呼び出し元つきで `gold_move` 行に出る
  難易度 … `colosseum_enemy_generator` の難易度を位置でも kwargs でも拾う
  敗北   … `GameOverManager` で窓が閉じ、`game_over` が真になる
  素通り … どの包みも `orig` の戻り値をそのまま返し、例外を漏らさない
"""
import importlib.util
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "233_probe_colosseum")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "colosseum_probe"))
LOG_NAME = "colosseum.log"
RECORD_NAME = "colosseum.jsonl"

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class PhaseSpec(object):
    def __init__(self, cls_name, args=()):
        self.cls_name = cls_name
        self.args = list(args)


def button(text, cls, args=()):
    return {"text": text, "spec": PhaseSpec(cls, args)}


class Facility(object):
    def __init__(self, name, facility_type, config=None):
        self.id = "8-1"
        self.name = name
        self.facility_type = facility_type
        self.config = config


class Player(object):
    def __init__(self, location, gold=500, level=7):
        self.location = location
        self.gold = gold
        self.experience_level = level
        self.current_hp = 120
        self.is_player = True


class App(object):
    def __init__(self, player):
        self.player = player
        self.buttons = []
        self.texts = []
        self.in_battle = 0
        self.in_boss_battle = 0
        self.in_colosseum_battle = 0
        self.current_enemy_dict = {}

    def add_text(self, context):
        self.texts.append(context)

    def elapse_days(self, days):
        return days


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
    _mod = "233_probe_colosseum"

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

    def jsonl(self, name, **kw):
        return ml.ModContext.jsonl(self, name, **kw)

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
    name = "colosseum_probe_mod"
    sys.modules.pop(name, None)
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(name, os.path.join(MOD_DIR, entry))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh(app):
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    module = load_mod()
    module.ui = FakeUI(app, ml.ui)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    # 本物では `add_text` の包みがクラスに当たる。偽の app では本体を包み越しに通す。
    hook = ctx.hooks["__main__:InstantaleApp.add_text"]
    app.add_text = lambda context: hook(App.add_text, app, context)
    return module, ctx


def records():
    path = os.path.join(OUT_DIR, RECORD_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def rows(phase):
    return [row for row in records() if row.get("phase") == phase]


def log_lines():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


ENEMY = {"type": "normal", "data": {"name": "黄金の断罪者", "rank": 51,
                                    "attribute_type": "battlemage"}}

print("包みが全部付く")
arena = Facility("全能闘技場", "colosseum",
                 {"current_phase": 2, "level_of_detail": 1,
                  "enemy_data": {"0": {"data": {"rank": 35, "name": "闘士A"}}}})
app = App(Player(arena))
module, ctx = fresh(app)
for target in ("__main__:EntryColosseumMatchManager.method",
               "__main__:ColosseumMatchStart.execute",
               "__main__:ColosseumMatchStart.method",
               "__main__:ColosseumMatchStart.generate_enemy_data",
               "scripts.llm.llm_manager:colosseum_enemy_generator",
               "scripts.llm.llm_manager:colosseum_battle_summarizer",
               "__main__:BattleStartManager.__init__",
               "__main__:BattleStartManager.start_battle",
               "__main__:CancelBattleActionManager.cancel_action",
               "__main__:BattlePhaseManager.check_team_annihilation",
               "__main__:BattlePhaseManager.check_character_death",
               "__main__:BattleEndInColosseum.execute",
               "__main__:BattleEndInColosseum.end_phase",
               "__main__:BattleEndManager.__init__",
               "__main__:BattleEndManager.execute",
               "__main__:BattleEndManager.end_phase",
               "__main__:BattleEndInFreeAction.end_phase",
               "__main__:GameOverManager.__init__",
               "__main__:NotImplementedManager.method",
               "__main__:MovePhaseManager.move_phase",
               "__main__:InstantaleApp.add_text",
               "__main__:InstantaleApp.elapse_days"):
    check("包む: " + target, target in ctx.hooks, sorted(ctx.hooks))

print("闘技場に立つ")
app.buttons = [button("試合に出る", "EntryColosseumMatchManager"),
               button("観戦する", "NotImplementedManager")]
moved = ctx.hooks["__main__:MovePhaseManager.move_phase"](
    lambda self: "moved", Manager(app))
check("orig の戻りをそのまま返す", moved == "moved", moved)
facility_rows = rows("facility")
check("闘技場に立った行が1件", len(facility_rows) == 1, facility_rows)
check("current_phase が入る",
      facility_rows and facility_rows[0]["arena"]["current_phase"] == 2, facility_rows)
check("phase ごとのランクが入る",
      facility_rows and facility_rows[0]["arena"]["ranks"]["0"]["rank"] == 35,
      facility_rows)

print("闘技場でない施設は録らない")
app.player.location = Facility("霧隠れの宿", "inn", {})
ctx.hooks["__main__:MovePhaseManager.move_phase"](lambda self: "moved", Manager(app))
check("宿屋では行が増えない", len(rows("facility")) == 1, rows("facility"))
app.player.location = arena

print("観戦する")
ctx.hooks["__main__:NotImplementedManager.method"](lambda self: None, Manager(app))
check("observe の行", len(rows("not_implemented")) == 1, rows("not_implemented"))

print("申し込む → 相手を作る → 勝つ")


def match_start_execute(self, choice_text):
    return "started"


ctx.hooks["__main__:ColosseumMatchStart.execute"](
    match_start_execute, Manager(app), "申し込む")


def generate(self, enemy_id):
    arena.config["current_phase"] = 4
    arena.config["enemy_data"]["4"] = ENEMY
    return ENEMY


ctx.hooks["__main__:ColosseumMatchStart.generate_enemy_data"](
    generate, Manager(app), "4")
gen_rows = rows("generate_enemy_data")
check("生成した相手のランクが入る",
      gen_rows and gen_rows[0]["returned_rank"] == 51, gen_rows)
check("phase の前後が入る",
      gen_rows and gen_rows[0]["arena_before"]["current_phase"] == 2
      and gen_rows[0]["arena_after"]["current_phase"] == 4, gen_rows)

# 難易度は位置引数（本体の並び）で来る。
ctx.hooks["scripts.llm.llm_manager:colosseum_enemy_generator"](
    lambda location, area, world, npc_difficulty_level: ENEMY,
    arena, {"name": "港町"}, {"name": "世界"}, 51)
gen = rows("enemy_generator")[-1]
check("難易度を位置引数から拾う",
      gen["difficulty"] == 51 and gen["difficulty_from"] == "args[3]", gen)
check("戻りのランクを拾う", gen["returned_rank"] == 51, gen)

# kwargs で来ても拾う（呼ばれ方はビルドで変わりうる）。
ctx.hooks["scripts.llm.llm_manager:colosseum_enemy_generator"](
    lambda location, npc_difficulty_level=None: ENEMY, arena,
    npc_difficulty_level=66)
gen = rows("enemy_generator")[-1]
check("難易度を kwargs から拾う",
      gen["difficulty"] == 66 and gen["difficulty_from"] == "kwargs", gen)

app.in_battle = 1
app.in_colosseum_battle = 1
ctx.hooks["__main__:BattleStartManager.__init__"](
    lambda self, app_, enemy_type: None, Manager(app), app, "colosseum")
app.buttons = [button("全力で攻撃", "UtteranceInBattleManager"),
               button("撤退しよう", "CancelBattleActionManager")]
ctx.hooks["__main__:BattleStartManager.start_battle"](lambda self: None, Manager(app))
start = rows("battle_start")
check("戦闘開始の行に enemy_type とフラグ",
      start and start[0]["enemy_type"] == "colosseum"
      and start[0]["flags"]["in_colosseum_battle"] is True, start)
check("戦闘に並ぶボタンが入る（撤退の有無を見るため）",
      start and any(entry["text"] == "撤退しよう" for entry in start[0]["buttons"]),
      start)


def end_phase(self):
    """報酬をここで渡す本体の代わり。文を出してから所持金を足す。"""
    app.add_text("受付: おめでとう！報酬として1,200Gを貰った。")
    app.player.gold += 1200
    app.in_battle = 0
    app.in_colosseum_battle = 0
    return "ended"


result = ctx.hooks["__main__:BattleEndInColosseum.end_phase"](end_phase, Manager(app))
check("end_phase の戻りをそのまま返す", result == "ended", result)

match_rows = rows("match")
check("試合の行が1件", len(match_rows) == 1, match_rows)
match = match_rows[0] if match_rows else {}
check("所持金の差が入る", match.get("gold_gained") == 1200, match)
check("報酬の文から額を読む",
      match.get("rewards") and match["rewards"][0]["amount"] == 1200, match)
check("相手のランクが試合の行に載る",
      (match.get("enemy") or {}).get("rank") == 51, match)
check("phase の前後が試合の行に載る",
      (match.get("arena_before") or {}).get("current_phase") == 2
      and (match.get("arena_after") or {}).get("current_phase") == 4, match)
check("プレイヤーのレベルが載る",
      (match.get("player_before") or {}).get("experience_level") == 7, match)
check("通った地点が並ぶ",
      "BattleEndInColosseum.end_phase" in (match.get("steps") or []), match)
check("ゲームオーバーではない", match.get("game_over") is False, match)

move_rows = rows("gold_move")
check("所持金が動いた地点が1件", len(move_rows) == 1, move_rows)
check("動いた額と呼び出し元",
      move_rows and move_rows[0]["moved"] == 1200 and move_rows[0]["caller"],
      move_rows)
check("動いた地点が残る（この偽の本体は文の後で足す）",
      move_rows
      and move_rows[0]["where"] == "BattleEndInColosseum.end_phase (returned)",
      move_rows)

print("逃げる（BattleEndInColosseum を通らない）")
app.player.gold = 700
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    match_start_execute, Manager(app), "申し込む")
app.in_battle = 1
app.in_colosseum_battle = 1


class Escape(object):
    def __init__(self, app):
        self.app = app
        self.end_type = "escaped"

    def end_phase(self):
        app.in_battle = 0
        app.in_colosseum_battle = 0
        return "escaped"


before = len(rows("match"))
ctx.hooks["__main__:BattleEndManager.__init__"](
    lambda self, app_, end_type: None, Escape(app), app, "escaped")
init = rows("battle_end_init")
check("end_type の実値と呼び出し元が残る",
      init and init[-1]["end_type"] == "escaped" and init[-1]["caller"], init)
ctx.hooks["__main__:BattleEndManager.execute"](
    lambda self, choice_text: "ok", Escape(app), "")
result = ctx.hooks["__main__:BattleEndManager.end_phase"](
    Escape.end_phase, Escape(app))
check("orig の戻りをそのまま返す（逃走）", result == "escaped", result)
escaped = rows("match")
check("逃げた試合も1行で閉じる", len(escaped) == before + 1, escaped)
check("execute も通った地点に残る",
      "BattleEndManager.execute" in (rows("match")[-1].get("steps") or []),
      rows("match")[-1].get("steps"))
check("閉じた理由に end_type が入る",
      escaped and "escaped" in (escaped[-1].get("closed_by") or ""), escaped[-1])
check("逃げた試合の報酬は空", escaped[-1].get("rewards") == [], escaped[-1])
other = rows("battle_end_other")
check("battle_end_other の行", len(other) == 1 and other[0]["end_type"] == "escaped",
      other)

print("闘技場の外の戦闘では窓を作らない")
before = len(records())
ctx.hooks["__main__:BattleEndManager.end_phase"](Escape.end_phase, Escape(app))
check("窓が無ければ素通り", len(records()) == before, records()[-1:])

print("負けてゲームオーバー")
app.player.gold = 700
ctx.hooks["__main__:ColosseumMatchStart.execute"](
    match_start_execute, Manager(app), "申し込む")
app.in_battle = 1
app.in_colosseum_battle = 1
app.player.current_hp = 0
ctx.hooks["__main__:BattlePhaseManager.check_team_annihilation"](
    lambda self: True, Manager(app))
ctx.hooks["__main__:GameOverManager.__init__"](
    lambda self, app_: None, Manager(app), app)
over = rows("game_over")
check("game over の行", len(over) == 1, over)
check("game over の行にフラグと呼び出し元",
      over and over[0]["flags"]["in_colosseum_battle"] is True and over[0]["caller"],
      over)
match_rows = rows("match")
check("負けた試合も1行で閉じる", len(match_rows) == 3, match_rows)
check("game_over が真", match_rows[-1]["game_over"] is True, match_rows[-1])
check("負けた試合に通った地点が残る",
      "check_team_annihilation" in (match_rows[-1].get("steps") or []),
      match_rows[-1])

print("窓の外・値が読めないとき")
app.player.location = None
ctx.hooks["__main__:MovePhaseManager.move_phase"](lambda self: "moved", Manager(app))
result = ctx.hooks["__main__:BattleEndInColosseum.execute"](
    lambda self, choice_text: "ok", Manager(app), "")
check("施設が無くても素通り", result == "ok", result)
check("例外を漏らさない", not ctx.errors, ctx.errors)
check("読む用のログも出ている", any("match #1 closed" in line for line in log_lines()),
      log_lines()[-3:])

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
