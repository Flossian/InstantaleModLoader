# -*- coding: utf-8 -*-
"""913_area_move_with_party をゲーム抜きで通す。

    python tools/tests/test_wip_area_move_with_party.py

偽物の `execute` は本体の順（同行者の `relationship.player.relationship` を見る → 通れば `elapse_days`、駄目なら `area_move_rejector`）を写す。
見るのは、判定の間だけ関係の配列が `['家族']` になること・`elapse_days` と `save_game` の前に戻ること・拒否と不足でも戻ること・セーブ側の写しにも残らないこと。
"""
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import ui  # noqa: E402

folder = os.path.join(MODS_DIR, [n for n in os.listdir(MODS_DIR)
                                 if n.endswith("_area_move_with_party")][0])
manifest = json.load(io.open(os.path.join(folder, "mod.json"), encoding="utf-8"))
spec = importlib.util.spec_from_file_location("area_move_with_party_under_test",
                                              os.path.join(folder, manifest["entry"]))
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)


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
    def __init__(self, name, tags=None):
        self.name = name
        self.state = ""
        self.relationship = {"player": {"affinity": 0, "relationship": tags}}
        if tags is None:
            del self.relationship["player"]["relationship"]


def tags_of(character):
    return character.relationship["player"].get("relationship", "MISSING")


def make_app(*members):
    app = types.SimpleNamespace()
    app.party = ["player"] + [m for m, _ in members]
    app.world = types.SimpleNamespace(characters={m: c for m, c in members})
    app.save_data_dict = {"npcs": {m: {"relationship": copy.deepcopy(c.relationship)} for m, c in members}}
    app.player = types.SimpleNamespace(name="PC")
    return app


def run(members, gold_ok=True, family_passes=True, save_in_branch=False):
    """偽の本体。戻り値は (辿り着いたか, 判定時に見えた配列の一覧, 拒否回数, セーブ側)。"""
    ctx = FakeCtx(tempfile.mkdtemp())
    MOD.apply(ctx)
    app = make_app(*members)
    seen = {"tags": [], "rejected": 0, "days": []}

    def orig_save(self):
        # 本体の保存は Character の値をセーブ側へ写す
        for member_id in ui.party_member_ids(app):
            app.save_data_dict["npcs"][member_id]["relationship"] = copy.deepcopy(
                ui.character_of(app, member_id).relationship)

    def orig_rejector(life_log, player, character, worldview):
        seen["rejected"] += 1
        if save_in_branch:
            # 版1で踏んだ形: 拒否の途中で本体が写して保存する
            orig_save(app)
            ctx.hooks["__main__:InstantaleApp.save_game"](orig_save, app)
        return "no"

    def orig_elapse(self, days):
        seen["days"].append(days)

    def orig_execute(self, choice_text):
        for member_id in ui.party_member_ids(app):
            character = ui.character_of(app, member_id)
            tags = character.relationship["player"]["relationship"]
            seen["tags"].append(list(tags))
            if not (family_passes and "家族" in tags):
                ctx.hooks["scripts.llm.llm_manager:area_move_rejector"](
                    orig_rejector, [], app.player, character, "")
                return
        if not gold_ok:
            return
        ctx.hooks["__main__:InstantaleApp.elapse_days"](orig_elapse, app, 14)
        for member_id in ui.party_member_ids(app):
            seen["tags"].append(tags_of(ui.character_of(app, member_id)))
        ctx.hooks["__main__:InstantaleApp.save_game"](orig_save, app)

    manager = types.SimpleNamespace(app=app)
    ctx.hooks["__main__:AreaMoveManager.execute"](orig_execute, manager, "馬車")
    assert not ctx.errors, ctx.errors
    saved = {m: e["relationship"]["player"].get("relationship", "MISSING")
             for m, e in app.save_data_dict["npcs"].items()}
    return bool(seen["days"]), seen["tags"], seen["rejected"], saved


# 雇用（同行中）と欄の無い相手を連れて移動: 判定では 家族、到着後は元の値。セーブ側も元のまま
hired, blank = Character("A", ["同行中"]), Character("B", None)
arrived, tags, rejected, saved = run([("1", hired), ("2", blank)])
assert arrived and rejected == 0, (arrived, rejected)
assert tags == [["家族"], ["家族"], ["同行中"], "MISSING"], tags
assert tags_of(hired) == ["同行中"] and tags_of(blank) == "MISSING"
assert saved == {"1": ["同行中"], "2": "MISSING"}, saved

# もう家族の相手は触らない
family = Character("C", ["家族"])
arrived, tags, _, _ = run([("3", family)])
assert arrived and tags == [["家族"], ["家族"]] and tags_of(family) == ["家族"]

# 所持金不足: elapse_days を通らないが戻る
poor = Character("D", ["同行中"])
arrived, _, _, _ = run([("4", poor)], gold_ok=False)
assert not arrived and tags_of(poor) == ["同行中"]

# 分岐が配列を見ていない場合でも戻り、拒否の途中の保存にも 家族 が残らない（版1の漏れ）
other = Character("E", ["同行中"])
arrived, _, rejected, saved = run([("5", other)], family_passes=False, save_in_branch=True)
assert not arrived and rejected == 1 and tags_of(other) == ["同行中"]
assert saved == {"5": ["同行中"]}, saved

# 同行者なし: 何もしない
arrived, tags, _, _ = run([])
assert arrived and tags == []

print("ok")
