# -*- coding: utf-8 -*-
"""324_place_bgm の選曲画面（tool.py）を窓抜きで通す。

    python tools/tests/test_place_bgm_tool.py

（開発中は `910_place_bgm` / `test_wip_place_bgm_tool.py` だった。2026-09-02 に正式化。）

  場所    … 環境変数が無ければ settings/gui.json と MOD の位置から組む
  一覧    … 2つのフォルダを再帰で合算。playlist.json に無い曲は 0（使うに入れるまで鳴らない）
  絞込    … 曲名は部分一致、フォルダと置き場は選択、重みは下限
  保存    … tmp→replace で書く。ファイルにあって曲が無い行と知らない種類は残す。_help はいまの文。dirty が落ちる
  世界    … セーブを復号して世界→土地→施設の木にする。通路は出さない。worlds/<世界>.json の playlist を書き、
            本体が書いた chosen は保つ。「覚えた曲を消す」で chosen が消える
  本体    … 保存した内容を place_bgm.py がそのまま読める（候補と重みが一致する）
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
MOD_DIR = [os.path.join(MODS_DIR, n) for n in os.listdir(MODS_DIR) if n.endswith("_place_bgm")][0]

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load("place_bgm_tool_under_test", os.path.join(MOD_DIR, "tool.py"))
core = load("place_bgm_under_test", os.path.join(MOD_DIR, "place_bgm.py"))

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


def touch(folder, *names):
    os.makedirs(folder, exist_ok=True)
    for name in names:
        with io.open(os.path.join(folder, name), "wb") as fh:
            fh.write(b"\0")


def read_json(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_raw(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "wb") as fh:
        fh.write(data)


tmp = tempfile.mkdtemp(prefix="place_bgm_tool_test_")
try:
    game = os.path.join(tmp, "game")
    assets = os.path.join(game, *tool.ASSET_SUBDIR)
    state_root = os.path.join(tmp, "state")
    state_dir = os.path.join(state_root, *tool.STATE_SUBDIR)
    data_root = os.path.join(tmp, "data")
    touch(os.path.join(assets, "town", "calm"), "a.mp3", "b.mp3")
    touch(os.path.join(assets, "battle"), "1. Echoes of Valhalla.mp3")
    touch(assets, "それ以外.mp3")
    touch(state_dir, "宿.mp3")
    touch(os.path.join(state_dir, "town", "calm"), "a.mp3")

    # ------------------------------------------------------------ 定数は本体と同じ
    check("constants: same folders and extensions as the core",
          tool.STATE_SUBDIR == core.STATE_SUBDIR and tool.ASSET_SUBDIR == core.ASSET_SUBDIR
          and tool.EXTENSIONS == core.EXTENSIONS and tool.PLAYLIST_NAME == core.PLAYLIST_NAME
          and tool.PASSAGE_TYPES == core.PASSAGE_TYPES
          and core.WORLDS_DIRNAME == "/".join(core.STATE_SUBDIR + (tool.WORLDS_SUBDIR,)))
    check("constants: same categories as the core", sorted(tool.CATEGORY_KEYS) == sorted(core.CATEGORIES),
          set(tool.CATEGORY_KEYS) ^ set(core.CATEGORIES))
    check("constants: same help text", tool.PLAYLIST_HELP == core.PLAYLIST_HELP)
    check("constants: same setting defaults",
          tool.SETTING_DEFAULTS == {"DEFAULT_WEIGHT": core.DEFAULT_WEIGHT, "AVOID_REPEAT": core.AVOID_REPEAT,
                                    "AREA_STICKY": core.AREA_STICKY, "FACILITY_STICKY": core.FACILITY_STICKY})

    # ------------------------------------------------------------ 場所
    for key in ("IML_ROOT", "IML_STATE_DIR", "IML_GAME_DIR", "IML_INSTANTALE_DATA"):
        os.environ.pop(key, None)
    root, state_found, game_found = tool.locate()
    check("locate: root is three levels above the mod",
          os.path.normcase(root) == os.path.normcase(os.path.normpath(os.path.join(MOD_DIR, "..", "..", ".."))), root)
    check("locate: state is under the root", os.path.normcase(state_found) == os.path.normcase(os.path.join(root, "state")))
    os.environ["IML_ROOT"] = tmp
    os.environ["IML_STATE_DIR"] = state_root
    os.environ["IML_GAME_DIR"] = game
    check("locate: environment wins", tool.locate() == (tmp, state_root, game))
    check("data dir: default is under LOCALAPPDATA",
          tool.data_dir().endswith(os.path.join(*tool.DATA_VENDOR)) and tool.data_dir("X") == "X")
    os.environ["IML_INSTANTALE_DATA"] = data_root
    check("data dir: environment wins", tool.data_dir() == data_root)

    # ------------------------------------------------------------ 一覧
    model = tool.Model()
    check("model: pool from both folders, battle/ excluded",
          sorted(model.pool) == ["town/calm/a.mp3", "town/calm/b.mp3", "それ以外.mp3", "宿.mp3"], sorted(model.pool))
    check("model: same key -> state", model.pool["town/calm/a.mp3"] == "state" and model.pool["town/calm/b.mp3"] == "assets")
    check("model: folders", model.folders() == ["town/calm", tool.ROOT_FOLDER], model.folders())
    check("model: nothing in use before anything is chosen",
          all(not model.used(c) for c in tool.CATEGORY_KEYS) and not model.dirty())
    check("model: shares are 0 when nothing is used", set(model.shares(model.playlists["inn"]).values()) == {0.0})
    check("model: no worlds yet", model.worlds == [])

    # ------------------------------------------------------------ 絞込
    check("filter: name is a substring match on the key, case-insensitive",
          tool.matches("town/calm/A.mp3", "assets", 0, "calm/a", tool.ANY, tool.ANY, 0)
          and not tool.matches("town/calm/a.mp3", "assets", 0, "宿", tool.ANY, tool.ANY, 0))
    check("filter: folder", tool.matches("town/calm/a.mp3", "assets", 0, "", "town/calm", tool.ANY, 0)
          and tool.matches("宿.mp3", "state", 0, "", tool.ROOT_FOLDER, tool.ANY, 0)
          and not tool.matches("宿.mp3", "state", 0, "", "town/calm", tool.ANY, 0))
    check("filter: where", tool.matches("宿.mp3", "state", 0, "", tool.ANY, "state", 0)
          and not tool.matches("宿.mp3", "state", 0, "", tool.ANY, tool.WHERE_LABEL["assets"], 0))
    check("filter: min weight", tool.matches("x", "state", 50, "", tool.ANY, tool.ANY, 50)
          and not tool.matches("x", "state", 49, "", tool.ANY, tool.ANY, 50))
    check("names: folder and file", tool.folder_of("town/calm/a.mp3") == "town/calm"
          and tool.name_of("town/calm/a.mp3") == "a.mp3" and tool.folder_of("宿.mp3") == tool.ROOT_FOLDER)
    check("sort: ids as numbers", sorted(["10", "9", "x", "7/10", "7/9"], key=tool.place_sort_key)
          == ["7/9", "7/10", "9", "10", "x"])

    # ------------------------------------------------------------ 保存（一括設定）
    os.makedirs(state_dir, exist_ok=True)
    with io.open(model.playlist_path, "w", encoding="utf-8") as fh:
        json.dump({"_help": ["old"], "playlists": {"inn": {"消えた.mp3": 30, "宿.mp3": 100},
                                                     "tavern": {"酒.mp3": 1}}}, fh, ensure_ascii=False)
    model.reload()
    check("reload: weights from the file, missing track not in the pool",
          model.playlists["inn"]["宿.mp3"] == 100 and "消えた.mp3" not in model.playlists["inn"] and not model.dirty())
    model.playlists["area:town"]["town/calm/a.mp3"] = 60
    model.playlists["area:town"]["town/calm/b.mp3"] = 40
    model.playlists["inn"]["宿.mp3"] = 0
    model.settings["AREA_STICKY"] = False
    check("dirty after edits", model.dirty())
    shares = model.shares(model.playlists["area:town"])
    check("shares: 60/40", abs(shares["town/calm/a.mp3"] - 60.0) < 1e-9 and abs(shares["town/calm/b.mp3"] - 40.0) < 1e-9)
    data = model.to_json()
    check("to_json: missing track row kept, removed track dropped",
          data["playlists"]["inn"] == {"消えた.mp3": 30})
    check("to_json: unknown category kept", data["playlists"]["tavern"] == {"酒.mp3": 1})
    check("to_json: every known category present", all(c in data["playlists"] for c in tool.CATEGORY_KEYS))
    check("to_json: _help is the current text", data["_help"] == tool.PLAYLIST_HELP)
    ok = model.save()
    check("save: written", ok and os.path.isfile(model.playlist_path) and not os.path.exists(model.playlist_path + ".tmp"))
    check("save: dirty falls", not model.dirty())
    on_disk = read_json(model.playlist_path)
    check("save: area:town on disk", on_disk["playlists"]["area:town"] == {"town/calm/a.mp3": 60, "town/calm/b.mp3": 40})
    check("settings: default weight is the declared default", model.default_weight == core.DEFAULT_WEIGHT)

    # ------------------------------------------------------------ 本体が同じ内容を読める
    found = core.scan_tracks(model.asset_dir, model.state_dir)
    cands = core.candidates(on_disk["playlists"]["area:town"], found)
    check("core reads the saved playlist: same candidates and weights",
          cands == [("town/calm/a.mp3", 60.0), ("town/calm/b.mp3", 40.0)], cands)
    check("core: state-side key resolves to the state path", found["town/calm/a.mp3"][1] == "state")

    # ------------------------------------------------------------ セーブを読む
    save = {"world_data": {"name": "テスト世界"},
            "areas": {"7": {"name": "陽光の砦", "size": "town", "nodes": {"0": {"facilities": {
                          "0": {"name": "入口", "facility_type": "entrance"},
                          "107": {"name": "鷹の巣", "facility_type": "guild"},
                          "106": {"name": "灯火亭", "facility_type": "inn"}}}}},
                      "20": {"name": "暗い洞窟", "size": "dungeon", "nodes": {"0": {"facilities": {
                          "1": {"name": "洞窟", "facility_type": "dungeon_location"}}}}},
                      "3": {"name": "霧の村", "size": "village", "nodes": {}}}}
    raw = json.dumps(save, ensure_ascii=False).encode("utf-8")
    check("decode: plain json", tool.decode(raw) == save)
    check("decode: xor json", tool.decode(tool.xor(raw)) == save and tool.xor(tool.xor(raw)) == raw)
    check("decode: garbage -> None", tool.decode(b"\x00\x01\xff") is None and tool.decode(b"[1]") is None)
    write_raw(tool.save_path("テスト世界", data_root), tool.xor(raw))
    write_raw(tool.save_path("壊れ", data_root), b"\x00\x01\xff")
    os.makedirs(os.path.join(data_root, "saves", "空っぽ"))
    check("list worlds: only folders with savedata.json, sorted", tool.list_worlds(data_root) == ["テスト世界", "壊れ"])
    places = tool.places_of(save)
    check("places: world name from world_data", places["name"] == "テスト世界")
    check("places: areas in id order with size", [(a["id"], a["size"]) for a in places["areas"]]
          == [("3", "village"), ("7", "town"), ("20", "dungeon")], places["areas"])
    check("places: facilities in id order, passages dropped",
          [(f["id"], f["type"], f["name"]) for f in places["areas"][1]["facilities"]]
          == [("106", "inn", "灯火亭"), ("107", "guild", "鷹の巣")]
          and places["areas"][2]["facilities"] == [])
    check("places: empty save -> no areas", tool.places_of(None) == {"name": "", "areas": []})

    # ------------------------------------------------------------ ワールド個別設定
    model = tool.Model()
    check("model: worlds listed", model.worlds == ["テスト世界", "壊れ"])
    check("open: unreadable save -> None", model.open_world("壊れ") is None and model.open_world("壊れ") is None)
    world_path = model.world_path("テスト世界")
    check("world path: under worlds/, named by the loader's rule",
          os.path.normcase(os.path.dirname(world_path)) == os.path.normcase(os.path.join(state_dir, "worlds"))
          and os.path.basename(world_path) == "テスト世界.json", world_path)
    # 本体が先に書いた控え（覚えた曲）
    os.makedirs(os.path.dirname(world_path), exist_ok=True)
    with io.open(world_path, "w", encoding="utf-8") as fh:
        json.dump({"areas": {"7": {"name": "陽光の砦", "chosen": {"level": "area:town", "track": "town/calm/b.mp3"}}},
                   "facilities": {}, "note": "keep me"}, fh, ensure_ascii=False)
    places = model.open_world("テスト世界")
    check("open: places and key", places["name"] == "テスト世界" and model.world_keys["テスト世界"] == "テスト世界")
    check("open: entries from the file are loaded", ("テスト世界", "areas", "7") in model.world_playlists
          and not model.dirty())
    check("chosen: read from the file", model.chosen_of("テスト世界", "areas", "7") == "town/calm/b.mp3"
          and model.chosen_of("テスト世界", "facilities", "7/106") == "")
    inn = model.world_weights("テスト世界", "facilities", "7/106")
    check("world weights: all pool keys at 0, not dirty yet", set(inn) == set(model.pool) and not any(inn.values())
          and not model.dirty())
    inn["宿.mp3"] = 100
    inn["town/calm/a.mp3"] = 50
    check("world weights: dirty after edit", model.dirty() and model.world_used("テスト世界", "facilities", "7/106")
          == ["town/calm/a.mp3", "宿.mp3"])
    check("clear chosen: only when there is one",
          model.clear_chosen("テスト世界", "areas", "7") and not model.clear_chosen("テスト世界", "facilities", "7/106")
          and model.chosen_of("テスト世界", "areas", "7") == "")
    # 保存の前に本体が同じファイルへ書いた（施設の覚えた曲）。保存はそれを保つ
    on_disk = read_json(world_path)
    on_disk["facilities"]["7/106"] = {"chosen": {"level": "facility:inn", "track": "宿.mp3"}}
    with io.open(world_path, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh, ensure_ascii=False)
    check("save: worlds file written", model.save() and not model.dirty())
    on_disk = read_json(world_path)
    check("save: facility playlist with the name from the save",
          on_disk["facilities"]["7/106"]["playlist"] == {"town/calm/a.mp3": 50, "宿.mp3": 100}
          and on_disk["facilities"]["7/106"]["name"] == "灯火亭", on_disk)
    check("save: chosen written by the runtime meanwhile is kept",
          on_disk["facilities"]["7/106"]["chosen"] == {"level": "facility:inn", "track": "宿.mp3"})
    check("save: cleared chosen is gone and the empty area entry is dropped", "7" not in on_disk["areas"], on_disk["areas"])
    check("save: unknown keys kept, areas/facilities first",
          on_disk.get("note") == "keep me" and list(on_disk)[:2] == ["areas", "facilities"])
    check("chosen after save: the runtime's choice shows up",
          model.chosen_of("テスト世界", "facilities", "7/106") == "宿.mp3")
    # 曲の無い行を残す
    on_disk["facilities"]["7/106"]["playlist"]["消えた.mp3"] = 7
    with io.open(world_path, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh, ensure_ascii=False)
    model.reload()
    model.open_world("テスト世界")
    model.world_weights("テスト世界", "facilities", "7/106")["宿.mp3"] = 0
    model.save()
    on_disk = read_json(world_path)
    check("save: missing track row kept, removed track dropped",
          on_disk["facilities"]["7/106"]["playlist"] == {"town/calm/a.mp3": 50, "消えた.mp3": 7})
    # 本体がその指定を先に見る
    info = {"world": "テスト世界", "area_id": "7", "facility_id": "106", "facility_type": "inn", "size": "town"}
    level, pool, _memory = core.resolve(core.level_keys(info, on_disk, {"inn": {"宿.mp3": 100}}), found)
    check("core: the per-facility playlist wins over the inn category",
          level == "world:facility:7/106" and pool == [("town/calm/a.mp3", 50.0)], (level, pool))

    # ------------------------------------------------------------ dump は落ちない
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tool.dump(tool.Model())
    text = buf.getvalue()
    check("dump: lists the pool, the categories and the worlds",
          "4 track(s)" in text and "town/calm/a.mp3 60%" in text and "陽光の砦" in text
          and "7/106" in text and "壊れ: (セーブを読めない)" in text, text[-600:])
finally:
    for key in ("IML_ROOT", "IML_STATE_DIR", "IML_GAME_DIR", "IML_INSTANTALE_DATA"):
        os.environ.pop(key, None)
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("all ok")
