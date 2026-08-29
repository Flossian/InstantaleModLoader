# -*- coding: utf-8 -*-
"""322_battle_bgm の選曲画面（tool.py）を窓抜きで通す。

    python tools/tests/test_battle_bgm_tool.py

9xx なので CI は走らせない（`test_wip_*`）。

  場所    … 環境変数が無ければ settings/gui.json と MOD の位置から組む
  一覧    … 2つのフォルダを合算。playlist.json に無い曲は 0（使うに入れるまで鳴らない）
  絞込    … 曲名は部分一致、置き場は選択、重みは下限
  保存    … tmp→replace で書く。ファイルにあって曲が無い行は残す。_help はいまの文に置き換える。dirty が落ちる
  本体    … 保存した内容を battle_bgm.py がそのまま読める（候補と重みが一致する）
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "322_battle_bgm")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load("battle_bgm_tool_under_test", os.path.join(MOD_DIR, "tool.py"))
core = load("battle_bgm_under_test", os.path.join(MOD_DIR, "battle_bgm.py"))

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


tmp = tempfile.mkdtemp(prefix="battle_bgm_tool_test_")
try:
    game = os.path.join(tmp, "game")
    assets = os.path.join(game, *tool.ASSET_SUBDIR)
    state_root = os.path.join(tmp, "state")
    state_dir = os.path.join(state_root, *tool.STATE_SUBDIR)
    touch(assets, "1. Echoes of Valhalla.mp3")
    touch(state_dir, "決戦.mp3", "喝采.ogg", "note.txt")
    playlist_path = os.path.join(state_dir, tool.PLAYLIST_NAME)
    with io.open(playlist_path, "w", encoding="utf-8") as fh:
        json.dump({"_help": ["keep me"],
                   "tracks": {"決戦.mp3": {"normal": 0, "boss": 100, "colosseum": 0},
                              "消えた曲.mp3": {"normal": 5, "boss": 5, "colosseum": 5}}},
                  fh, ensure_ascii=False)

    # ---------------------------------------------------------------- 場所
    for key in ("IML_ROOT", "IML_STATE_DIR", "IML_GAME_DIR"):
        os.environ.pop(key, None)
    root, sdir, gdir = tool.locate()
    check("locate: root is the loader folder", os.path.isdir(os.path.join(root, "runtime")))
    check("locate: state under root", sdir == os.path.join(root, "state"))
    os.environ["IML_ROOT"] = tmp
    os.environ["IML_STATE_DIR"] = state_root
    os.environ["IML_GAME_DIR"] = game
    check("locate: env wins", tool.locate() == (tmp, state_root, game))

    # ---------------------------------------------------------------- 一覧
    model = tool.Model()
    check("model: three tracks", sorted(model.rows) == sorted(["1. Echoes of Valhalla.mp3", "決戦.mp3", "喝采.ogg"]))
    check("model: playlist values are read", model.rows["決戦.mp3"]["boss"] == 100 and model.rows["決戦.mp3"]["normal"] == 0)
    check("model: unknown track is listed at 0 (not in use)",
          model.rows["喝采.ogg"] == {"where": "state", "normal": 0, "boss": 0, "colosseum": 0})
    check("model: clean after load", not model.dirty())
    shares = model.shares("boss")
    check("model: shares add up (only the chosen track counts)",
          abs(sum(shares.values()) - 100.0) < 1e-9 and abs(shares["決戦.mp3"] - 100.0) < 1e-9)

    # ---------------------------------------------------------------- 絞込
    m = tool.matches
    check("filter: name partial, case-insensitive", m("Echoes.mp3", "assets", 10, "echo", tool.WHERE_ANY, 0))
    check("filter: name miss", not m("Echoes.mp3", "assets", 10, "決戦", tool.WHERE_ANY, 0))
    check("filter: where label", m("a", "state", 10, "", "state", 0) and not m("a", "assets", 10, "", "state", 0))
    check("filter: where any", m("a", "assets", 10, "", tool.WHERE_ANY, 0))
    check("filter: min weight", m("a", "state", 50, "", "", 50) and not m("a", "state", 49, "", "", 50))

    # ---------------------------------------------------------------- 保存
    model.rows["喝采.ogg"]["normal"] = 0
    model.rows["喝采.ogg"]["colosseum"] = 30
    check("save: dirty after edit", model.dirty())
    check("save: returns True", model.save())
    check("save: clean after save", not model.dirty())
    check("save: no tmp left behind", not os.path.exists(playlist_path + ".tmp"))
    with io.open(playlist_path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    check("save: _help is refreshed to the current text", on_disk["_help"] == tool.PLAYLIST_HELP)
    check("save: stale entry kept", on_disk["tracks"]["消えた曲.mp3"]["normal"] == 5)
    check("save: edited values written",
          on_disk["tracks"]["喝采.ogg"] == {"normal": 0, "boss": 0, "colosseum": 30})
    check("save: newly found track written at 0",
          on_disk["tracks"]["1. Echoes of Valhalla.mp3"] == {"normal": 0, "boss": 0, "colosseum": 0})
    check("save: tracks sorted", list(on_disk["tracks"]) == sorted(on_disk["tracks"]))

    # 再読込で同じ値に戻る
    again = tool.Model()
    check("reload: round trip", again.to_tracks() == model.to_tracks())

    # ---------------------------------------------------------------- 本体
    found = core.scan_tracks(assets, state_dir)
    pool = core.candidates(on_disk, found, "colosseum")
    check("core: colosseum candidates match the saved weights",
          pool == [("喝采.ogg", 30.0)], pool)
    check("core: stale entry ignored by the game side",
          "消えた曲.mp3" not in dict(core.candidates(on_disk, found, "normal")))
    synced, added = core.sync_playlist(on_disk, found.keys(), 100)
    check("core: nothing to add after a save", added == [])

    # ---------------------------------------------------------------- MOD の設定
    # mod_settings.json は IML_ROOT/runtime から settings/ を引くので、tmp に runtime/ を用意する。
    os.makedirs(os.path.join(tmp, "runtime"), exist_ok=True)
    store_path = os.path.join(tmp, "settings", "mod_settings.json")
    os.makedirs(os.path.dirname(store_path), exist_ok=True)
    with io.open(store_path, "w", encoding="utf-8") as fh:
        json.dump({"112_ui_text_spacing": {"BLANK_LINES": 1}}, fh)
    m2 = tool.Model()
    check("settings: defaults when nothing chosen", m2.settings == {"DEFAULT_WEIGHT": 100, "AVOID_REPEAT": True})
    m2.settings["DEFAULT_WEIGHT"] = 40
    m2.settings["AVOID_REPEAT"] = False
    check("settings: dirty after change", m2.dirty())
    check("settings: save ok", m2.save())
    with io.open(store_path, encoding="utf-8") as fh:
        store = json.load(fh)
    check("settings: written under the mod name", store.get("322_battle_bgm") == {"DEFAULT_WEIGHT": 40, "AVOID_REPEAT": False})
    check("settings: other mods untouched", store.get("112_ui_text_spacing") == {"BLANK_LINES": 1})
    m3 = tool.Model()
    check("settings: read back", m3.settings == {"DEFAULT_WEIGHT": 40, "AVOID_REPEAT": False})
    touch(state_dir, "新曲.mp3")
    m3.reload()
    check("settings: new track is still 0 (weight applies only when put in use)", m3.rows["新曲.mp3"]["boss"] == 0)
    m3.settings.update(tool.SETTING_DEFAULTS)
    check("settings: back to defaults -> entry removed", m3.save())
    with io.open(store_path, encoding="utf-8") as fh:
        store = json.load(fh)
    check("settings: default values are not written", "322_battle_bgm" not in store)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    for key in ("IML_ROOT", "IML_STATE_DIR", "IML_GAME_DIR"):
        os.environ.pop(key, None)

print()
if failures:
    print("{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("all ok")
