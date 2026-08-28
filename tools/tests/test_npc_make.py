# -*- coding: utf-8 -*-
"""ローダの `npcs.make_npc` をゲーム抜きで通す。

    python tools/tests/test_npc_make.py

偽の `app` を差し込み、次を確認する。

  採番   … 実在する id の最大値+1 と `index['npc']` の大きいほうから採る
  台帳   … 作ったあと `world_dict` / `save_data_dict` 両方の `index['npc']` が id+1 に進む
  戻さない … 台帳のほうが先に進んでいれば触らない
  並び   … 33項目の並びが崩れない

背景: 実在 id だけ見て採ると台帳が追いつかず、次の町の生成でゲームが
同じ番号を踏む（テストワールドの灰の交易都市、2026-08-29。GAME.md §2.23）。
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


def existing(npc_id, area="0", facility="2"):
    data = copy.deepcopy(npcs.NEW_NPC_TEMPLATE)
    data.update({"name": "npc{}".format(npc_id), "id": str(npc_id),
                 "initial_location": {"area": area, "node": None,
                                      "facility": facility}})
    return data


class FakeWorld:
    def __init__(self, save):
        self.save_data_dict = save
        self.characters = {k: object() for k in save["npcs"]}
        self.built = []

    def generate_character(self, npc_id, value):
        assert npc_id in self.save_data_dict["npcs"], npc_id
        self.built.append(npc_id)
        self.characters[npc_id] = object()
        return self.characters[npc_id]


def make_app(ids, index_npc):
    save = {"npcs": {str(i): existing(i) for i in ids},
            "index": {"area": 9, "npc": index_npc}}
    world_dict = {"npcs": copy.deepcopy(save["npcs"]),
                  "index": {"area": 9, "npc": index_npc}}
    app = types.SimpleNamespace(save_data_dict=save, world_dict=world_dict)
    app.world = FakeWorld(save)
    return app


def check(label, cond):
    print("  {} {}".format("ok  " if cond else "FAIL", label))
    return bool(cond)


def main():
    ok = True
    log = []

    print("採番: 台帳が先行しているとき")
    app = make_app(range(0, 47), index_npc=50)
    ok &= check("free_id -> 50 (index 50 > max 46 + 1)", npcs.free_id(app) == "50")
    made = npcs.make_npc(app, {"name": "外套の女", "job": "adventure"}, "0", "2",
                         write=log.append)
    ok &= check("made 50", made == "50")
    ok &= check("index advanced in save", app.save_data_dict["index"]["npc"] == 51)
    ok &= check("index advanced in world_dict", app.world_dict["index"]["npc"] == 51)
    ok &= check("index logged", any("index['npc'] -> 51" in line for line in log))

    print("採番: 実在 id が先行しているとき（台帳が追いついていない古いセーブ）")
    app = make_app(range(0, 64), index_npc=50)
    ok &= check("free_id -> 64 (max 63 + 1 > index 50)", npcs.free_id(app) == "64")
    made = npcs.make_npc(app, {"name": "泥濘のニナ", "job": "adventure"}, "0", "6")
    ok &= check("made 64", made == "64")
    ok &= check("index caught up to 65", app.save_data_dict["index"]["npc"] == 65
                and app.world_dict["index"]["npc"] == 65)

    print("戻さない: 台帳が id より先なら触らない")
    app = make_app(range(0, 3), index_npc=100)
    made = npcs.make_npc(app, {"name": "x"}, "0", "2")
    ok &= check("made 100", made == "100")
    ok &= check("index -> 101", app.save_data_dict["index"]["npc"] == 101)
    ok &= check("advance_index(5) leaves 101", npcs.advance_index(app, "5") == []
                and app.save_data_dict["index"]["npc"] == 101)

    print("連続で作ると id が進む")
    app = make_app(range(0, 3), index_npc=3)
    a = npcs.make_npc(app, {"name": "a"}, "0", "2")
    b = npcs.make_npc(app, {"name": "b"}, "0", "2")
    ok &= check("3 then 4", (a, b) == ("3", "4"))
    ok &= check("index 5", app.world_dict["index"]["npc"] == 5)

    print("台帳が無くても落ちない")
    app = make_app(range(0, 3), index_npc=0)
    del app.save_data_dict["index"]; del app.world_dict["index"]
    ok &= check("free_id -> 3", npcs.free_id(app) == "3")
    ok &= check("make_npc works", npcs.make_npc(app, {"name": "c"}, "0", "2") == "3")

    print("並び順")
    app = make_app(range(0, 3), index_npc=3)
    made = npcs.make_npc(app, {"name": "d", "job": "inn"}, "0", "2")
    ok &= check("33 fields in order",
                tuple(app.save_data_dict["npcs"][made]) == npcs.NPC_FIELD_ORDER)

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
