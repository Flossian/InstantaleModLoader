# -*- coding: utf-8 -*-
"""228_probe_area_move_reject をゲーム抜きで通す。

    python tools/tests/test_area_move_reject_probe.py

偽物の `execute` は、同行者の `relationship.player.relationship` と名簿を読み、
窓の間に `world.characters` / `app.party` / `original_party` / `relationship` へ書き込んでから
`area_move_rejector` か `elapse_days` へ進み、その後にも書き込む。
見るのは、読みが `>> area_move_rejector` / `>> elapse_days` の前に録れること・
窓の間の書き込みが元のオブジェクトに残ること（写しと一緒に捨てられない）・
差し替えが `area_move_rejector` / `elapse_days` の時点で解けていること・
写した後に元へ直接入った書き込みも消えないこと。
"""
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402

folder = os.path.join(MODS_DIR, [n for n in os.listdir(MODS_DIR)
                                 if n.endswith("_probe_area_move_reject")][0])
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("area_move_reject_probe_under_test",
                                              os.path.join(folder, manifest["entry"]))
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)

failures = []

#: 読みの1行（`[時刻]   #12  npc1.rel ...`）
READ = re.compile(r"\]\s+#\d")


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx:
    _mod = None

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        return os.path.join(self.out_dir, *parts)

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


class Character:
    def __init__(self, name, tags):
        self.name = name
        self.relationship = {"player": {"affinity": 0, "relationship": tags}}


class Manager:
    def __init__(self, app):
        self.app = app


class App:
    pass


def run(passes):
    """偽の本体を1回通す。`(ctx, app, 元の入れ物, ログの行, 分岐の後に見えた型)`。"""
    ctx = FakeCtx(tempfile.mkdtemp())
    MOD.apply(ctx)
    member = Character("A", ["同行中"])
    app = App()
    app.party = {"player": {}, "1": {}}
    app.original_party = ["player", "1"]
    app.world = types.SimpleNamespace(characters={"1": member})
    app.player = types.SimpleNamespace(name="PC")
    before = {"party": app.party, "original_party": app.original_party,
              "characters": app.world.characters, "rel": member.relationship,
              "rel.player": member.relationship["player"]}
    seen = {}

    def orig_rejector(*args):
        return "no"

    def orig_elapse(self, days):
        # 日数送りの中の書き込み（窓はもう解けている）
        app.world.characters["13"] = "after"

    def orig_execute(self, choice_text):
        for member_id, _ in app.party.items():
            if member_id == "player":
                continue
            character = app.world.characters[member_id]
            tags = character.relationship["player"]["relationship"]
        # 窓の間の書き込み: 移動先の生成で NPC が増える・仲間が出入りする・関係が書き換わる
        app.world.characters["12"] = "spawned"
        app.party["2"] = {}
        del app.party["1"]
        app.original_party.append("2")
        member.relationship["player"]["affinity"] = 5
        member.relationship["2"] = {"affinity": 1}
        # 写す前から元を握っていた側（保存スレッドなど）の書き込み
        before["characters"]["14"] = "held"
        if passes and "同行中" in tags:
            ctx.hooks["__main__:InstantaleApp.elapse_days"](orig_elapse, app, 14)
        else:
            ctx.hooks["scripts.llm.llm_manager:area_move_rejector"](
                orig_rejector, [], app.player, member, "")
        seen["types"] = (type(app.party), type(app.world.characters),
                         type(member.relationship), type(member), type(app))
        # 分岐の後の書き込み
        app.world.characters["15"] = "later"

    manager = Manager(app)
    ctx.hooks["__main__:AreaMoveManager.execute"](orig_execute, manager, "馬車")
    with io.open(os.path.join(ctx.out_dir, MOD.LOG_BASENAME), encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return ctx, app, member, manager, before, lines, seen["types"]


for passes, marker in ((False, ">> area_move_rejector called"), (True, ">> elapse_days(14)")):
    print("{}: 窓の間の書き込みが元に残る".format("elapse_days" if passes else "area_move_rejector"))
    ctx, app, member, manager, before, lines, after_branch = run(passes)
    check("例外は出ていない", ctx.errors == [], ctx.errors)
    at = next((i for i, line in enumerate(lines) if marker in line), None)
    check("分岐の印が出る", at is not None, lines)
    check("分岐の前に同行者の関係の読みが録れる",
          at is not None and any("npc1.rel.player" in line and "'relationship'" in line
                                 for line in lines[:at]), lines)
    check("分岐の後の読みは録らない",
          at is not None and not any(READ.search(line) for line in lines[at + 1:]),
          lines)
    check("分岐の時点で差し替えが解けている",
          after_branch == (dict, dict, dict, Character, App), after_branch)
    check("入れ物は元のオブジェクトのまま",
          app.party is before["party"] and app.original_party is before["original_party"]
          and app.world.characters is before["characters"]
          and member.relationship is before["rel"]
          and member.relationship["player"] is before["rel.player"])
    check("窓の間に増えた NPC が残る", app.world.characters.get("12") == "spawned",
          sorted(app.world.characters))
    check("写す前から握っていた側の書き込みも残る", app.world.characters.get("14") == "held",
          sorted(app.world.characters))
    check("分岐の後の書き込みが残る", app.world.characters.get("15") == "later",
          sorted(app.world.characters))
    check("仲間の出入りが残る", sorted(app.party) == ["2", "player"], app.party)
    check("list の追記が残る", app.original_party == ["player", "1", "2"], app.original_party)
    check("関係の書き換えが残る",
          member.relationship["player"]["affinity"] == 5
          and member.relationship.get("2") == {"affinity": 1}, member.relationship)
    check("型は素に戻る",
          type(app.party) is dict and type(app.original_party) is list
          and type(member) is Character and type(app) is App and type(manager) is Manager)
    check("窓を閉じた行が出る", lines and "window closed" in lines[-1], lines[-1:])
    if passes:
        check("日数送りの中の書き込みが残る", app.world.characters.get("13") == "after",
              sorted(app.world.characters))

print("PASS" if not failures else "FAIL: " + ", ".join(failures))
sys.exit(0 if not failures else 1)
