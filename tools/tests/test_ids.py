# -*- coding: utf-8 -*-
"""ローダの `ids`（採番台帳を通した id の採り方）をゲーム抜きで通す。

    python tools/tests/test_ids.py

偽の `app` を差し込み、次を確認する。

  台帳   … `index` を持つ辞書（world_dict / save_data_dict）を両方見つける
  実在   … 6種類それぞれの実在 id を素データの正しい場所から拾う
           （node / facility は areas の入れ子、item は `item_<n>` の鍵だけ、
           quest は story_quests も、npc は実行時の名簿も）
  採番   … 台帳と実在+1 の大きいほう。`item` は `item_<n>` の書式
  進める … `claim` で両方の台帳が id+1 に進み、連続で呼ぶと連番。戻さない
  検算   … `audit` は台帳 ≤ 実在の最大 の種類だけを並べる
  規約   … 知らない種類は ValueError
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import ids  # noqa: E402


def make_save(index):
    return {
        "areas": {
            "0": {"nodes": {"0": {"facilities": {"0": {}, "1": {}}},
                            "1": {"facilities": {"2": {}}}}},
            "5": {"nodes": {"7": {"facilities": {"30": {}}}}},
        },
        "npcs": {"0": {"inventory": {"item_3": {}, "79": {}}},
                 "1": {"inventory": {}}, "9": {}},
        "quests": {"0": {}, "4": {}},
        "story_quests": {"6": {}},
        "player_data": {"inventory": {"item_0": {}, "item_12": {}}},
        "index": dict(index),
    }


def make_app(index, runtime_npcs=(), runtime_items=()):
    save = make_save(index)
    world_dict = make_save(index)
    world_dict.pop("player_data")
    app = types.SimpleNamespace(save_data_dict=save, world_dict=world_dict)
    characters = {str(k): types.SimpleNamespace(inventory={}) for k in runtime_npcs}
    app.world = types.SimpleNamespace(save_data_dict=save, characters=characters)
    app.player = types.SimpleNamespace(inventory={k: {} for k in runtime_items})
    return app


def check(label, cond):
    print("  {} {}".format("ok  " if cond else "FAIL", label))
    return bool(cond)


def main():
    ok = True
    healthy = {"area": 6, "node": 8, "facility": 31, "npc": 10, "item": 13, "quest": 7}

    print("台帳")
    app = make_app(healthy)
    where = [w for w, _ in ids.stores(app)]
    ok &= check("both index stores found: {}".format(where),
                sorted(where) == ["app.save_data_dict['index']", "app.world_dict['index']"])
    ok &= check("counter npc 10", ids.counter(app, "npc") == 10)

    print("実在")
    ok &= check("area {0,5}", ids.existing(app, "area") == {0, 5})
    ok &= check("node {0,1,7}", ids.existing(app, "node") == {0, 1, 7})
    ok &= check("facility {0,1,2,30}", ids.existing(app, "facility") == {0, 1, 2, 30})
    ok &= check("npc {0,1,9}", ids.existing(app, "npc") == {0, 1, 9})
    ok &= check("quest {0,4,6} (story_quests too)", ids.existing(app, "quest") == {0, 4, 6})
    ok &= check("item {0,3,12} (店在庫の '79' は数えない)", ids.existing(app, "item") == {0, 3, 12})
    app = make_app(healthy, runtime_npcs=(40,), runtime_items=("item_50",))
    ok &= check("npc sees world.characters", 40 in ids.existing(app, "npc"))
    ok &= check("item sees app.player.inventory", 50 in ids.existing(app, "item"))

    print("採番: 健全なら台帳の値")
    app = make_app(healthy)
    ok &= check("npc -> '10'", ids.next_id(app, "npc") == "10")
    ok &= check("item -> 'item_13'", ids.next_id(app, "item") == "item_13")
    ok &= check("facility -> '31'", ids.next_id(app, "facility") == "31")
    ok &= check("audit empty", ids.audit(app) == [])

    print("採番: 台帳が遅れていれば実在+1（灰の交易都市の形）")
    behind = dict(healthy, npc=5, item=2)
    app = make_app(behind)
    ok &= check("npc -> '10'", ids.next_id(app, "npc") == "10")
    ok &= check("item -> 'item_13'", ids.next_id(app, "item") == "item_13")
    log = []
    ok &= check("audit lists npc and item only",
                ids.audit(app, log.append) == [("npc", 5, 9), ("item", 2, 12)])
    ok &= check("audit logged", log and "index behind" in log[0])
    ok &= check("used= is merged", ids.next_id(app, "npc", used=["20"]) == "21")

    print("進める")
    app = make_app(healthy)
    log = []
    a = ids.claim(app, "npc", write=log.append)
    b = ids.claim(app, "npc", write=log.append)
    ok &= check("claim twice -> 10, 11 (素データに書かなくても連番)", (a, b) == ("10", "11"))
    ok &= check("both indexes 12", app.save_data_dict["index"]["npc"] == 12
                and app.world_dict["index"]["npc"] == 12)
    ok &= check("logged", any("index['npc'] -> 11" in line for line in log))
    ok &= check("advance never rewinds", ids.advance(app, "npc", "3") == []
                and app.world_dict["index"]["npc"] == 12)
    ok &= check("advance ignores foreign key form", ids.advance(app, "item", "79") == [])
    i = ids.claim(app, "item")
    ok &= check("item claim -> 'item_13', index 14", i == "item_13"
                and app.save_data_dict["index"]["item"] == 14)

    print("台帳の無いセーブ")
    app = make_app(healthy)
    del app.save_data_dict["index"]; del app.world_dict["index"]
    ok &= check("stores empty", ids.stores(app) == [])
    ok &= check("next_id falls back to existing+1", ids.next_id(app, "npc") == "10")
    ok &= check("claim still returns", ids.claim(app, "npc") == "10")

    print("規約")
    try:
        ids.next_id(make_app(healthy), "monster")
        ok &= check("unknown kind raises", False)
    except ValueError:
        ok &= check("unknown kind raises", True)

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
