# -*- coding: utf-8 -*-
"""ローダの好感度の読み書き（`npcs.affinity_value` / `affinity_of` / `set_affinity`）をゲーム抜きで通す。

    python tools/tests/test_npc_affinity.py

  読む   … 実行時の人物（属性）でもセーブの素データ（辞書）でも読む。真偽値と数でないものは None
  float  … None にせず、0 の方向に切り捨てた int で返す。セーブの値は書き換えない
  下がる … 実行時の名簿に居ない相手はセーブの素データから読む
  書く   … 実行時の人物と素データの全部に書く。`player` の欄が無い所には書かない（実行時の人物だけ欄が無ければ素データの欄を写す）
"""
import copy
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import npcs  # noqa: E402


def relationship(affinity):
    return {"player": {"affinity": affinity, "affinity_text": "警戒心がある"}}


def raw(npc_id, affinity=None, with_row=True):
    data = copy.deepcopy(npcs.NEW_NPC_TEMPLATE)
    data.update({"name": "npc{}".format(npc_id), "id": str(npc_id)})
    data["relationship"] = relationship(affinity) if with_row else {}
    return data


def make_app(save_npcs, runtime):
    """`save_npcs` は `{id: 素データ}`。`runtime` は `{id: 実行時の人物}`。"""
    save = {"npcs": save_npcs}
    world_dict = {"npcs": copy.deepcopy(save_npcs)}
    app = types.SimpleNamespace(save_data_dict=save, world_dict=world_dict)
    app.world = types.SimpleNamespace(characters=runtime)
    return app


def person(affinity):
    return types.SimpleNamespace(relationship=relationship(affinity))


def check(label, cond):
    print("  {}  {}".format("ok  " if cond else "FAIL", label))
    return bool(cond)


def main():
    ok = True

    print("読む")
    ok &= check("実行時の人物（属性）", npcs.affinity_value(person(25)) == 25)
    ok &= check("セーブの素データ（辞書）", npcs.affinity_value({"relationship": relationship(-40)}) == -40)
    ok &= check("真偽値は好感度に数えない", npcs.affinity_value(person(True)) is None)
    ok &= check("数でなければ None", npcs.affinity_value(person("20")) is None)
    ok &= check("欄が無ければ None", npcs.affinity_value(types.SimpleNamespace(relationship={})) is None
                and npcs.affinity_value(None) is None)

    print("float")
    holder = person(12.9)
    ok &= check("切り捨てた int", npcs.affinity_value(holder) == 12
                and isinstance(npcs.affinity_value(holder), int))
    ok &= check("負は 0 の方向", npcs.affinity_value(person(-3.7)) == -3)
    ok &= check("読むだけで書き換えない", holder.relationship["player"]["affinity"] == 12.9)
    ok &= check("nan と inf は None", npcs.affinity_value(person(float("nan"))) is None
                and npcs.affinity_value(person(float("inf"))) is None)

    print("名簿に居ない相手")
    app = make_app({"7": raw("7", 30), "8": raw("8", 5)}, {"8": person(60)})
    ok &= check("名簿に居ればそちら", npcs.affinity_of(app, "8") == 60)
    ok &= check("居なければ素データ", npcs.affinity_of(app, "7") == 30)
    ok &= check("どちらにも居なければ None", npcs.affinity_of(app, "9") is None)

    print("書く")
    runtime = person(60)
    app = make_app({"8": raw("8", 5)}, {"8": runtime})
    written = npcs.set_affinity(app, "8", 70)
    ok &= check("実行時の人物に書く", runtime.relationship["player"]["affinity"] == 70)
    ok &= check("セーブの素データに書く", app.save_data_dict["npcs"]["8"]["relationship"]["player"]["affinity"] == 70)
    ok &= check("世界の骨格（world_dict）には書かない（同じ世界の別の主人公に漏らさない）",
                app.world_dict["npcs"]["8"]["relationship"]["player"]["affinity"] == 5)
    ok &= check("書いた所の数（同じ欄は1回）", written == 2)

    talking = person(10)
    app = make_app({"7": raw("7", 10)}, {})
    npcs.set_affinity(app, "7", 14, character=talking)
    ok &= check("渡した人物にも書く", talking.relationship["player"]["affinity"] == 14)
    ok &= check("名簿に居なくても素データに書く",
                app.save_data_dict["npcs"]["7"]["relationship"]["player"]["affinity"] == 14)

    app = make_app({"6": raw("6", with_row=False)}, {})
    ok &= check("player の欄が無い所には書かない", npcs.set_affinity(app, "6", 20) == 0
                and app.save_data_dict["npcs"]["6"]["relationship"] == {})

    bare = types.SimpleNamespace(relationship={})
    app = make_app({"5": raw("5", 30)}, {"5": bare})
    npcs.set_affinity(app, "5", 35)
    row = bare.relationship.get("player") or {}
    ok &= check("実行時の人物に欄が無ければ素データの欄を丸ごと写して書く（保存で消えない）",
                row.get("affinity") == 35 and row.get("affinity_text") == "警戒心がある")
    ok &= check("  → 写した欄は素データと別の辞書",
                row is not app.save_data_dict["npcs"]["5"]["relationship"]["player"])

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
