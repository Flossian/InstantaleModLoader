# -*- coding: utf-8 -*-
"""137_fix_npc_skill_uses をゲーム抜きで通す。

    python tools/tests/test_fix_npc_skill_uses.py

  絞り方 … 残り 0 以下のスキルだけ外す。通常攻撃・上限の無いもの・読めないものは残す
  審判   … 審判の間だけ `skills` が絞られ、戻ったら元の辞書（同じ物）に戻る。
           審判が投げても戻る。間に減った残りは元の辞書に残る
  セーブ … 審判の途中の保存には元の辞書が入り、保存の後はまた絞られる
  重ねがけ … 使った強化・弱体だけのスキルは、次の手から duration の手番だけ外れる。戦闘が変わると数え直す
  休養   … 仲間の残りだけ上限へ戻す。プレイヤーには触らない
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


spec = importlib.util.spec_from_file_location("fix_npc_skill_uses_under_test",
                                              find_mod("_fix_npc_skill_uses"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx(object):
    def __init__(self):
        self.hooks, self.lines, self.errors = {}, [], []

    def logger(self, name, **kw):
        return self.lines.append

    def out_path(self, *parts):
        return os.path.join("out", *parts)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def skill(current, limit):
    return {"description": "", "effects": [], "current_uses": current, "max_uses": limit}


def companion_skills():
    return {"商人の勘": skill(-23, 5), "放浪の連撃": skill(2, 7),
            "通常攻撃": skill(0, 16), "逃げる": skill(-1, -1), "謎": {"effects": []}}


print("絞り方")
kept = mod.usable_skills(companion_skills())
check("使い切ったスキルだけ外す", kept is not None and list(kept) == ["放浪の連撃", "通常攻撃", "逃げる", "謎"],
      kept and list(kept))
check("残り 0 ちょうども外す", "x" not in (mod.usable_skills({"x": skill(0, 3), "通常攻撃": skill(16, 16)}) or {}))
check("外すものが無ければ None", mod.usable_skills({"a": skill(1, 3), "通常攻撃": skill(16, 16)}) is None)
check("辞書でなければ None", mod.usable_skills(None) is None and mod.usable_skills([]) is None)

print("審判")
ctx = Ctx()
mod.ui = types.SimpleNamespace(find_app=lambda: None, party_member_ids=lambda app: [],
                               character_of=lambda app, i: None)
mod.apply(ctx)
referee = ctx.hooks["scripts.llm.llm_manager_battle:referee_npc"]
save = ctx.hooks["__main__:InstantaleApp.save_game"]
rest = ctx.hooks[mod.REST_TARGET]

actor = types.SimpleNamespace(name="リュウ", skills=companion_skills())
original = actor.skills
seen = {}


def fake_referee(player, combat_log, actor_name, who, side, party, enemies):
    seen["names"] = list(who.skills)
    who.skills["放浪の連撃"]["current_uses"] -= 1          # ゲームが残りを減らしても
    seen["saved"] = save(lambda self: list(actor.skills), types.SimpleNamespace())
    seen["after_save"] = list(who.skills)
    return {"skill": "放浪の連撃"}


got = referee(fake_referee, "player", "log", "リュウ", actor, "味方陣営", {}, {})
check("審判の戻りはそのまま", got == {"skill": "放浪の連撃"})
check("審判には使い切ったスキルが見えない", "商人の勘" not in seen["names"] and "通常攻撃" in seen["names"],
      seen["names"])
check("審判の途中の保存には元の辞書が入る", "商人の勘" in seen["saved"], seen["saved"])
check("保存の後はまた絞られる", "商人の勘" not in seen["after_save"], seen["after_save"])
check("戻ったら元の辞書（同じ物）に戻る", actor.skills is original and "商人の勘" in actor.skills)
check("審判の間に減った残りは元の辞書に残る", actor.skills["放浪の連撃"]["current_uses"] == 1)
check("隠したことを記録する", any("hid from the referee: spent=['商人の勘'] resting=[]" in line for line in ctx.lines), ctx.lines)


def raising_referee(*args, **kwargs):
    raise RuntimeError("llm failed")


try:
    referee(raising_referee, "player", "log", "リュウ", actor, "味方陣営", {}, {})
    raised = False
except RuntimeError:
    raised = True
check("審判が投げても例外は呼び元へ届き、辞書は戻る", raised and actor.skills is original)
plain = types.SimpleNamespace(name="メイ", skills={"通常攻撃": skill(16, 16)})
before = plain.skills
referee(lambda *a: "ok", "player", "log", "メイ", plain, "味方陣営", {}, {})
check("外すものが無い者には触らない", plain.skills is before)
check("保存は審判の外では素通り", save(lambda self: "saved", types.SimpleNamespace()) == "saved")

print("強化・弱体の重ねがけ")
check("強化だけのスキルは duration の手番数", mod.lingering_turns(
    {"effects": [{"type": "buff", "duration": 2}]}) == 2)
check("duration が無ければ既定", mod.lingering_turns(
    {"effects": [{"type": "debuff"}]}) == mod.DEFAULT_DURATION)
check("ダメージを含むスキルは外さない", mod.lingering_turns(
    {"effects": [{"type": "instant_damage"}, {"type": "debuff", "duration": 2}]}) == 0
      and mod.lingering_turns({"effects": []}) == 0 and mod.lingering_turns(None) == 0)

mei = types.SimpleNamespace(name="メイ", skills={
    "強者への追従": dict(skill(3, 3), effects=[{"type": "buff", "duration": 2}]),
    "通常攻撃": skill(16, 16)})
offered = []


def picks(name):
    def fake(player, combat_log, actor_name, who, *rest):
        offered.append(list(who.skills))
        return {"skill": name}
    return fake


ctx.lines[:] = []
referee(picks("強者への追従"), "player", "log", "メイ", mei, "味方陣営", {}, {})
referee(picks("通常攻撃"), "player", "log", "メイ", mei, "味方陣営", {}, {})
referee(picks("通常攻撃"), "player", "log", "メイ", mei, "味方陣営", {}, {})
referee(picks("強者への追従"), "player", "log", "メイ", mei, "味方陣営", {}, {})
check("使った強化は次の手から duration の手番だけ外れ、その後また見える",
      ["強者への追従" in names for names in offered] == [True, False, False, True], offered)
check("外したことと休ませたことを記録する",
      any("used 強者への追従 (buff/debuff); resting it for 2 turn(s)" in line for line in ctx.lines)
      and any("spent=[] resting=['強者への追従']" in line for line in ctx.lines), ctx.lines)
check("戦闘が変わると数え直す",
      ctx.hooks["__main__:BattleStartManager.start_battle"](lambda self: "go", object()) == "go")
offered[:] = []
referee(picks("通常攻撃"), "player", "log", "メイ", mei, "味方陣営", {}, {})
check("新しい戦闘の最初の手では強化が見える", offered and "強者への追従" in offered[0], offered)

print("休養")
hero = types.SimpleNamespace(name="主人公", skills={"断罪の連撃": skill(1, 4)})
ryu = types.SimpleNamespace(name="リュウ", skills=companion_skills())
app = types.SimpleNamespace(player=hero)
mod.ui = types.SimpleNamespace(find_app=lambda: app, party_member_ids=lambda a: ["95"],
                               character_of=lambda a, i: ryu if i == "95" else None)
check("休養の戻りはそのまま", rest(lambda self: "rested", types.SimpleNamespace(app=app)) == "rested")
check("仲間の残りは上限へ戻る", ryu.skills["商人の勘"]["current_uses"] == 5
      and ryu.skills["放浪の連撃"]["current_uses"] == 7 and ryu.skills["通常攻撃"]["current_uses"] == 16)
check("上限の無いスキルと読めないスキルは触らない",
      ryu.skills["逃げる"]["current_uses"] == -1 and "current_uses" not in ryu.skills["謎"])
check("プレイヤーには触らない", hero.skills["断罪の連撃"]["current_uses"] == 1)
check("戻したことを記録する", any(line.startswith("rest: リュウ 商人の勘 -23 -> 5") for line in ctx.lines),
      ctx.lines[-1:])
check("何も飲み込んでいない", not ctx.errors, ctx.errors)

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all good")
