# -*- coding: utf-8 -*-
"""322_battle_bgm をゲーム抜きで通す。

    python tools/tests/test_battle_bgm.py

9xx なので CI は走らせない（`test_wip_*`）。正式な番号へ振り直すときに名前も直す。

  走査    … 2つのフォルダを1つのプールにする。同名は state 側
  同期    … playlist.json に無い曲だけ足す。書いた数字は消えない
  重み    … 文字列の数字も読む。負・読めない・無い項目は 0
  選曲    … 重みどおりの比率で出る。全部 0 なら None。前回と同じ曲は避ける
  種類    … フラグ → 印 → enemy_type の語 の順
  パス    … Assets 側は相対の形を保つ。state 側は絶対パス
  フック  … 戦闘曲だけ差し替える。鳴らせなければ素の曲に落ちる。エリア曲は素通し
"""
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


def load_module(path):
    spec = importlib.util.spec_from_file_location("battle_bgm_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_module(find_mod("322_battle_bgm"))

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


tmp = tempfile.mkdtemp(prefix="battle_bgm_test_")
try:
    game = os.path.join(tmp, "game")
    assets = os.path.join(game, *mod.ASSET_SUBDIR)
    state_root = os.path.join(tmp, "state")
    state_dir = os.path.join(state_root, *mod.STATE_SUBDIR)
    touch(assets, "1. Echoes of Valhalla.mp3", "readme.txt")
    touch(state_dir, "決戦.mp3", "喝采.ogg", "1. Echoes of Valhalla.mp3", "memo.md")
    os.makedirs(os.path.join(state_dir, "sub"))
    touch(os.path.join(state_dir, "sub"), "deep.mp3")

    # ------------------------------------------------------------ 走査
    found = mod.scan_tracks(assets, state_dir)
    check("scan: three tracks, non-audio ignored, subfolder ignored",
          sorted(found) == sorted(["1. Echoes of Valhalla.mp3", "決戦.mp3", "喝采.ogg"]), sorted(found))
    check("scan: same name -> state wins",
          found["1. Echoes of Valhalla.mp3"][1] == "state")
    check("scan: missing folder is empty", mod.scan_tracks(None, os.path.join(tmp, "nope")) == {})

    # ------------------------------------------------------------ 同期
    playlist, added = mod.sync_playlist(None, found.keys(), mod.NEW_TRACK_WEIGHT)
    check("sync: new file lists every track", sorted(added) == sorted(found))
    check("sync: found tracks are listed at 0 (not in use until chosen)",
          all(playlist["tracks"][n] == {"normal": 0, "boss": 0, "colosseum": 0}
              for n in found))
    check("sync: NEW_TRACK_WEIGHT is 0", mod.NEW_TRACK_WEIGHT == 0)
    playlist["tracks"]["決戦.mp3"] = {"normal": 0, "boss": 100, "colosseum": 0}
    again, added = mod.sync_playlist(playlist, list(found) + ["新曲.mp3"], 50)
    check("sync: only the new track is added", added == ["新曲.mp3"])
    check("sync: edited numbers survive",
          again["tracks"]["決戦.mp3"] == {"normal": 0, "boss": 100, "colosseum": 0})
    check("sync: new track uses the given default",
          again["tracks"]["新曲.mp3"]["boss"] == 50)
    broken, added = mod.sync_playlist({"tracks": "oops"}, ["a.mp3"], 1)
    check("sync: broken playlist is rebuilt", added == ["a.mp3"] and "a.mp3" in broken["tracks"])

    # ------------------------------------------------------------ 重み
    check("weight: string number", mod.weight_of({"boss": "40"}, "boss") == 40.0)
    check("weight: negative -> 0", mod.weight_of({"boss": -3}, "boss") == 0.0)
    check("weight: garbage -> 0", mod.weight_of({"boss": "many"}, "boss") == 0.0)
    check("weight: missing category -> 0", mod.weight_of({"boss": 5}, "normal") == 0.0)
    check("weight: non-dict entry -> 0", mod.weight_of(7, "normal") == 0.0)

    # ------------------------------------------------------------ 選曲
    pl = {"tracks": {"a.mp3": {"normal": 70, "boss": 0, "colosseum": 0},
                     "b.mp3": {"normal": 30, "boss": 0, "colosseum": 0},
                     "ghost.mp3": {"normal": 100, "boss": 100, "colosseum": 100}}}
    pool = {"a.mp3": ("/x/a.mp3", "assets"), "b.mp3": ("/x/b.mp3", "state")}
    rng = random.Random(1234)
    counts = {}
    for _ in range(4000):
        name = mod.pick(pl, pool, "normal", avoid_repeat=False, rng=rng)
        counts[name] = counts.get(name, 0) + 1
    share = counts.get("a.mp3", 0) / 4000.0
    check("pick: 70/30 comes out near 0.7", 0.65 < share < 0.75, share)
    check("pick: track not on disk is never chosen", "ghost.mp3" not in counts)
    check("pick: all zero -> None", mod.pick(pl, pool, "boss", rng=rng) is None)
    check("pick: avoid repeat", all(mod.pick(pl, pool, "normal", last="a.mp3", rng=rng) == "b.mp3"
                                    for _ in range(20)))
    only = {"tracks": {"a.mp3": {"normal": 1}}}
    check("pick: single candidate repeats regardless",
          mod.pick(only, pool, "normal", last="a.mp3", rng=rng) == "a.mp3")

    # ------------------------------------------------------------ 種類
    check("classify: colosseum flag first",
          mod.classify({"in_colosseum_battle": 1, "in_boss_battle": 1})[0] == "colosseum")
    check("classify: boss flag", mod.classify({"in_boss_battle": 1})[0] == "boss")
    check("classify: pending boss without flag",
          mod.classify({"in_battle": 1}, pending="boss")[0] == "boss")
    check("classify: enemy_type word",
          mod.classify({}, enemy_type="colosseum_champion")[0] == "colosseum")
    check("classify: guard is normal", mod.classify({"in_battle": 1}, enemy_type="guard") == ("normal", "default"))

    # ------------------------------------------------------------ パス
    src = "Assets/sounds/musics/battle/1. Echoes of Valhalla.mp3"
    check("rewrite: assets keeps the relative form",
          mod.rewrite_src(src, "b.mp3", os.path.join(assets, "b.mp3"), "assets")
          == "Assets/sounds/musics/battle/b.mp3")
    got = mod.rewrite_src(src, "決戦.mp3", os.path.join(state_dir, "決戦.mp3"), "state")
    check("rewrite: state is absolute with slashes",
          os.path.isabs(got) and "\\" not in got and got.endswith("/musics/battle/決戦.mp3"), got)
    check("rewrite: state path still counts as a battle track", mod.is_battle_track(got))

    # ------------------------------------------------------------ フック
    class FakeCtx:
        def __init__(self):
            self.out_dir = os.path.join(tmp, "out")
            self.state_dir = state_root
            self.hooks = {}
            self.errors = []
            self.logs = []
            self.ready = []
            os.makedirs(self.out_dir, exist_ok=True)

        def out_path(self, *parts):
            path = os.path.join(self.out_dir, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path

        _mod = None      # 本物の logger が MOD 名に使う

        def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
            return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

        def warner(self, tag):
            seen = set()

            def warn_once(key, message):
                if key not in seen:
                    seen.add(key)
                    self.logs.append(("WARN", message))
            return warn_once

        def state_path(self, *parts):
            path = os.path.join(self.state_dir, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path

        def log(self, msg, level="INFO"):
            self.logs.append((level, msg))

        def log_exc(self, msg):
            self.errors.append(msg)

        def write_json(self, path, data, *, indent=1):
            return ml.write_json(path, data, indent=indent, report=self.log_exc)

        def read_json(self, path, default=None):
            return ml.read_json(path, default, report=self.log_exc)

        def on_ready(self, fn, **kw):
            self.ready.append(fn)
            fn()
            return True

        def wrap(self, target, **kw):
            def decorator(func):
                self.hooks[target] = func
                return func
            return decorator

    class FakeApp:
        in_battle = 1
        in_boss_battle = 0
        in_colosseum_battle = 0
        music = None

    old_cwd = os.getcwd()
    os.chdir(game)                       # game_root() はカレントから探す
    try:
        ctx = FakeCtx()
        mod.AVOID_REPEAT = False
        mod.apply(ctx)
    finally:
        os.chdir(old_cwd)

    playlist_path = os.path.join(state_dir, mod.PLAYLIST_NAME)
    check("hook: playlist.json is written at boot", os.path.isfile(playlist_path))
    with io.open(playlist_path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    check("hook: boot playlist lists the three tracks", sorted(on_disk["tracks"]) == sorted(found))

    # 決戦 だけをボス戦に、通常は素の曲だけに
    on_disk["tracks"]["決戦.mp3"] = {"normal": 0, "boss": 100, "colosseum": 0}
    on_disk["tracks"]["喝采.ogg"] = {"normal": 0, "boss": 0, "colosseum": 100}
    on_disk["tracks"]["1. Echoes of Valhalla.mp3"] = {"normal": 100, "boss": 0, "colosseum": 0}
    with io.open(playlist_path, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh, ensure_ascii=False)

    played = []

    def orig(self, app, music_src):
        if "喝采" in music_src:
            raise RuntimeError("unsupported")
        played.append(music_src)
        return "played"

    hook = ctx.hooks["scripts.sounds:SoundManager.play_music_from_src"]
    app = FakeApp()

    # 通常戦闘: 同名は state 側なので絶対パスで鳴る
    result = hook(orig, None, app, src)
    check("hook: normal battle picks the normal-only track",
          result == "played" and played[-1].endswith("/state/musics/battle/1. Echoes of Valhalla.mp3"),
          played[-1:])

    # エリア曲は素通し
    hook(orig, None, app, "Assets/sounds/musics/town/calm/x.mp3")
    check("hook: area track untouched", played[-1] == "Assets/sounds/musics/town/calm/x.mp3")

    # ボス戦
    app.in_boss_battle = 1
    hook(orig, None, app, src)
    check("hook: boss flag picks the boss-only track", played[-1].endswith("/決戦.mp3"), played[-1:])
    app.in_boss_battle = 0

    # 印だけでボス
    ctx.hooks["__main__:QuestEncounterFinalBoss.execute"](lambda self, *a: None, None, "text")
    hook(orig, None, app, src)
    check("hook: pending boss mark works without the flag", played[-1].endswith("/決戦.mp3"))
    hook(orig, None, app, src)
    check("hook: mark is consumed by one battle", played[-1].endswith("/1. Echoes of Valhalla.mp3"))

    # 闘技場: 鳴らせなければ素の曲に落ちる
    app.in_colosseum_battle = 1
    n = len(played)
    result = hook(orig, None, app, src)
    check("hook: unplayable track falls back to the game's track",
          result == "played" and played[-1] == src and len(played) == n + 1
          and any("could not be played" in e for e in ctx.errors))
    app.in_colosseum_battle = 0

    # 全部 0 の種類は素の曲
    ctx.hooks["__main__:BattleStartManager.__init__"](lambda *a, **k: None, None, app, "guard", None)
    on_disk["tracks"]["1. Echoes of Valhalla.mp3"]["normal"] = 0
    with io.open(playlist_path, "w", encoding="utf-8") as fh:
        json.dump(on_disk, fh, ensure_ascii=False)
    hook(orig, None, app, src)
    check("hook: no candidate -> the game's track", played[-1] == src)

    with io.open(os.path.join(ctx.out_dir, mod.LOG_BASENAME), encoding="utf-8") as fh:
        log_text = fh.read()
    check("hook: every battle leaves a [BGMPICK] line", log_text.count("[BGMPICK]") >= 7, log_text.count("[BGMPICK]"))
    check("hook: no unexpected errors",
          all("could not be played" in e for e in ctx.errors), ctx.errors)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("all ok")
