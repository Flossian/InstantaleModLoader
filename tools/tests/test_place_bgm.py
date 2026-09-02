# -*- coding: utf-8 -*-
"""324_place_bgm をゲーム抜きで通す。

    python tools/tests/test_place_bgm.py

（開発中は `910_place_bgm` / `test_wip_place_bgm.py` だった。2026-09-02 に正式化。）

  走査    … 2つのフォルダを再帰で1つのプールにする。鍵は相対パス。battle/ は除く。同じ鍵は state 側
  重み    … 文字列の数字も読む。負・読めない・無い項目は 0
  選曲    … 重みどおりの比率で出る。候補が無ければ None。前回と同じ曲は避ける
  場所    … size の均し、パスからの size、施設の種類、段の並びと覚える鍵
  パス    … Assets 側は musics/ から後ろを差し替える。state 側は絶対パス
  フック  … ゲームの play を差し替える。施設に入ると鳴らし直し、出ると土地の曲へ戻る。
            土地は覚える、ダンジョンは覚えない、世界ファイルの個別指定が先、鳴らせなければ素の曲
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
    spec = importlib.util.spec_from_file_location("place_bgm_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_module(find_mod("_place_bgm"))

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


# ------------------------------------------------------------------ 偽物
class Sound(object):
    def __init__(self):
        self.channels = 1

    def get_num_channels(self):
        return self.channels


class Facility(object):
    def __init__(self, fid, facility_type, name):
        self.id = fid
        self.facility_type = facility_type
        self.name = name


class Node(object):
    def __init__(self, facilities):
        self.facilities = facilities


class Area(object):
    def __init__(self, aid, name, size, bgm, facilities):
        self.id = aid
        self.name = name
        if size is not None:
            self.size = size
        self.bgm = bgm
        self.nodes = {"0": Node(dict((f.id, f) for f in facilities))}


class Player(object):
    def __init__(self, area, location):
        self.current_area = area
        self.location = location


class World(object):
    def __init__(self, areas):
        self.areas = areas


class App(object):
    in_battle = 0
    in_boss_battle = 0
    in_colosseum_battle = 0

    def __init__(self, areas, area, location, world_name="テスト世界", save_areas=None):
        self.world = World(areas)
        self.world_dict = {"world_data": {"name": world_name}}
        self.save_data_dict = {"areas": save_areas or {}}
        self.player = Player(area, location)
        self.music = None
        self.sound_manager = None


class Manager(object):
    """パッチ済みの SoundManager の代わり。呼ぶと包み（hook）を通る。"""

    def __init__(self, hook, orig):
        self.hook = hook
        self.orig = orig

    def play_music_from_src(self, app, src):
        return self.hook(self.orig, self, app, src)


tmp = tempfile.mkdtemp(prefix="place_bgm_test_")
try:
    game = os.path.join(tmp, "game")
    assets = os.path.join(game, *mod.ASSET_SUBDIR)
    state_root = os.path.join(tmp, "state")
    state_dir = os.path.join(state_root, *mod.STATE_SUBDIR)
    touch(os.path.join(assets, "town", "calm"), "a.mp3", "b.mp3", "readme.txt")
    touch(os.path.join(assets, "town", "lively"), "c.wav")
    touch(os.path.join(assets, "dungeons", "eerie"), "d.mp3")
    touch(os.path.join(assets, "battle"), "1. Echoes of Valhalla.mp3")
    touch(assets, "それ以外.mp3")
    touch(state_dir, "宿.mp3", "memo.md")
    touch(os.path.join(state_dir, "guild"), "酒場.ogg", "壊れ.mp3")
    touch(os.path.join(state_dir, "town", "calm"), "a.mp3")          # 同じ鍵。state 側が勝つ
    touch(os.path.join(state_dir, "battle"), "x.mp3")                # 直下の battle/ は除く

    # ------------------------------------------------------------ 走査
    found = mod.scan_tracks(assets, state_dir)
    check("scan: relative keys, recursive, battle/ and non-audio excluded",
          sorted(found) == ["dungeons/eerie/d.mp3", "guild/壊れ.mp3", "guild/酒場.ogg",
                            "town/calm/a.mp3", "town/calm/b.mp3", "town/lively/c.wav",
                            "それ以外.mp3", "宿.mp3"], sorted(found))
    check("scan: same key -> state wins", found["town/calm/a.mp3"][1] == "state"
          and found["town/calm/a.mp3"][0].startswith(state_dir))
    check("scan: assets keep their place", found["town/calm/b.mp3"][1] == "assets")
    check("scan: missing folders are empty", mod.scan_tracks(None, os.path.join(tmp, "nope")) == {})

    # ------------------------------------------------------------ 重み
    check("weight: string number", mod.coerce_weight("40") == 40.0)
    check("weight: negative -> 0", mod.coerce_weight(-3) == 0.0)
    check("weight: garbage -> 0", mod.coerce_weight("many") == 0.0)
    check("weight: bool", mod.coerce_weight(True) == 100.0 and mod.coerce_weight(False) == 0.0)
    cands = mod.candidates({"town/calm/a.mp3": 70, "town/calm/b.mp3": "30", "ghost.mp3": 100,
                            "town/lively/c.wav": 0}, found)
    check("candidates: on disk and positive only, sorted",
          cands == [("town/calm/a.mp3", 70.0), ("town/calm/b.mp3", 30.0)], cands)
    check("candidates: non-dict -> empty", mod.candidates("oops", found) == [])

    # ------------------------------------------------------------ 選曲
    rng = random.Random(1234)
    counts = {}
    for _ in range(4000):
        key = mod.pick(cands, avoid_repeat=False, rng=rng)
        counts[key] = counts.get(key, 0) + 1
    share = counts.get("town/calm/a.mp3", 0) / 4000.0
    check("pick: 70/30 comes out near 0.7", 0.65 < share < 0.75, share)
    check("pick: empty -> None", mod.pick([], rng=rng) is None)
    check("pick: avoid repeat", all(mod.pick(cands, last="town/calm/a.mp3", rng=rng) == "town/calm/b.mp3"
                                    for _ in range(20)))
    check("pick: single candidate repeats regardless",
          mod.pick(cands[:1], last="town/calm/a.mp3", rng=rng) == "town/calm/a.mp3")

    # ------------------------------------------------------------ 場所
    check("size: dungeons folder -> dungeon", mod.normalize_size("dungeons") == "dungeon")
    check("size: unknown -> None", mod.normalize_size("castle") is None and mod.normalize_size(7) is None)
    check("size from src: town", mod.size_from_src("Assets/sounds/musics/town/calm/x.mp3") == "town")
    check("size from src: single track has no size", mod.size_from_src("Assets/sounds/musics/それ以外.mp3") is None)
    check("size from src: backslashes", mod.size_from_src("Assets\\sounds\\musics\\dungeons\\eerie\\x.mp3") == "dungeon")
    check("facility category: inn", mod.facility_category("inn") == "inn")
    check("facility category: passage -> None",
          all(mod.facility_category(t) is None for t in mod.PASSAGE_TYPES))
    check("facility category: unknown -> None", mod.facility_category("bank") is None)

    info = {"world": "W", "area_id": "7", "facility_id": "106", "facility_type": "inn", "size": "town"}
    bucket = {"areas": {"7": {"playlist": {"x": 1}}},
              "facilities": {"7/106": {"playlist": {"y": 1}}}}
    playlists = {"inn": {"i": 1}, "area:town": {"t": 1}}
    levels = mod.level_keys(info, bucket, playlists, area_sticky=True, facility_sticky=False)
    check("levels: facility id > facility type > area id > area size",
          [l[0] for l in levels] == ["world:facility:7/106", "facility:inn", "world:area:7", "area:town"],
          [l[0] for l in levels])
    check("levels: playlists are threaded through",
          [l[1] for l in levels] == [{"y": 1}, {"i": 1}, {"x": 1}, {"t": 1}])
    check("levels: area levels remember, facility levels do not (default)",
          [l[2] for l in levels] == [None, None, "area:7", "area:7"])
    levels = mod.level_keys(info, None, playlists, area_sticky=True, facility_sticky=True)
    check("levels: facility sticky remembers per area/facility",
          [l[2] for l in levels] == ["facility:7/106", "facility:7/106", "area:7", "area:7"], [l[2] for l in levels])
    dungeon = dict(info, size="dungeon", facility_type="dungeon_location")
    levels = mod.level_keys(dungeon, None, playlists, area_sticky=True, facility_sticky=False)
    check("levels: dungeon is never remembered", all(l[2] is None for l in levels)
          and [l[0] for l in levels] == ["world:facility:7/106", "world:area:7", "area:dungeon"], levels)
    ward = dict(info, facility_type="ward", facility_id="3")
    levels = mod.level_keys(ward, {}, playlists, area_sticky=False, facility_sticky=False)
    check("levels: passage has no facility-type level, nothing remembered",
          [l[0] for l in levels] == ["world:facility:7/3", "world:area:7", "area:town"]
          and all(l[2] is None for l in levels))
    level, pool, memory = mod.resolve(
        [("a", {"nope": 1}, "m1"), ("b", {"town/calm/a.mp3": 5}, "m2"), ("c", {"town/calm/b.mp3": 1}, None)], found)
    check("resolve: first level with a candidate on disk", (level, memory) == ("b", "m2") and pool == [("town/calm/a.mp3", 5.0)])
    check("resolve: none", mod.resolve([("a", None, None)], found) == (None, [], None))
    check("place: area level is the area", mod.place_of("area:town", info) == "W|7"
          and mod.place_of("world:area:7", info) == "W|7")
    check("place: facility level is area/facility", mod.place_of("facility:inn", info) == "W|7/106")

    # ------------------------------------------------------------ パス
    src = "Assets/sounds/musics/town/calm/x.mp3"
    check("rewrite: assets replaces from musics/ on",
          mod.rewrite_src(src, "town/lively/c.wav", found["town/lively/c.wav"][0], "assets")
          == "Assets/sounds/musics/town/lively/c.wav")
    check("rewrite: assets keeps a different prefix",
          mod.rewrite_src("C:/g/Assets/sounds/musics/それ以外.mp3", "town/calm/b.mp3", "", "assets")
          == "C:/g/Assets/sounds/musics/town/calm/b.mp3")
    check("rewrite: assets without a base falls back to the default form",
          mod.rewrite_src(None, "town/calm/b.mp3", "", "assets") == "Assets/sounds/musics/town/calm/b.mp3")
    got = mod.rewrite_src(src, "宿.mp3", found["宿.mp3"][0], "state")
    check("rewrite: state is absolute with slashes",
          os.path.isabs(got) and "\\" not in got and got.endswith("/musics/place/宿.mp3"), got)
    check("rewrite: state path is not a battle track", not mod.is_battle_track(got))

    ordered = mod.order_world({"facilities": {"7/10": {}, "7/9": {}}, "areas": {"10": {}, "9": {}, "x": {}}, "extra": 1})
    check("order: numeric ids, unknown keys kept",
          list(ordered) == ["areas", "facilities", "extra"] and list(ordered["areas"]) == ["9", "10", "x"]
          and list(ordered["facilities"]) == ["7/9", "7/10"], ordered)
    b = {}
    entry = mod.memory_entry(b, "facility:7/106", create=True)
    entry["chosen"] = {"level": "facility:inn", "track": "宿.mp3"}
    check("memory entry: created under facilities", b == {"facilities": {"7/106": entry}})
    check("memory entry: unknown group -> None", mod.memory_entry(b, "planet:1", create=True) is None)

    # ------------------------------------------------------------ フック
    class FakeCtx:
        generation = "g1"
        _mod = None      # 本物の logger が MOD 名に使う

        def __init__(self):
            self.out_dir = os.path.join(tmp, "out")
            self.state_dir = state_root
            self.hooks = {}
            self.errors = []
            self.logs = []
            os.makedirs(self.out_dir, exist_ok=True)

        def out_path(self, *parts):
            path = os.path.join(self.out_dir, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path

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
            fn()
            return True

        def superseded(self):
            return False

        def wrap(self, target, **kw):
            def decorator(func):
                self.hooks[target] = func
                return func
            return decorator

    inn = Facility("106", "inn", "宿")
    guild = Facility("107", "guild", "ギルド")
    ward = Facility("3", "ward", "通り")
    ward2 = Facility("4", "ward", "広場")
    town = Area("7", "陽光の砦", "town", "Assets/sounds/musics/town/calm/x.mp3", [inn, guild, ward, ward2])
    cave = Facility("1", "dungeon_location", "洞窟")
    dungeon = Area("20", "暗い洞窟", "dungeon", "Assets/sounds/musics/dungeons/eerie/d.mp3", [cave])
    areas = {"7": town, "20": dungeon}

    if hasattr(sys, mod.STORE_ATTR):
        delattr(sys, mod.STORE_ATTR)
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
    check("hook: boot playlist has every category, all empty",
          sorted(on_disk["playlists"]) == sorted(mod.CATEGORIES)
          and all(v == {} for v in on_disk["playlists"].values()))
    check("hook: log has the pool line", any("pool: 8 track(s)" in line for line in
                                              io.open(os.path.join(ctx.out_dir, mod.LOG_BASENAME), encoding="utf-8")))

    def write_playlist(data):
        on_disk["playlists"].update(data)
        with io.open(playlist_path, "w", encoding="utf-8") as fh:
            json.dump(on_disk, fh, ensure_ascii=False)

    write_playlist({"inn": {"宿.mp3": 100},
                    "guild": {"guild/壊れ.mp3": 100},
                    "area:town": {"town/calm/a.mp3": 60, "town/calm/b.mp3": 40},
                    "area:dungeon": {"dungeons/eerie/d.mp3": 100}})

    played = []

    def orig(self, app, src):
        if "壊れ" in src:
            raise RuntimeError("unsupported")
        played.append(src)
        app.music = Sound()
        return "played"

    hook = ctx.hooks["scripts.sounds:SoundManager.play_music_from_src"]
    move_hook = ctx.hooks["__main__:MovePhaseManager.move_phase"]
    manager = Manager(hook, orig)
    app = App(areas, town, ward)
    app.sound_manager = manager

    class Mover(object):
        def __init__(self, app):
            self.app = app

    def move_to(location):
        app.player.location = location
        move_hook(lambda self: None, Mover(app))

    world_path = os.path.join(state_root, "musics", "place", "worlds", "テスト世界.json")

    # 1. ゲームが土地の曲を鳴らす（通りに居る）→ 土地の段で選ばれ、覚えられる
    result = hook(orig, manager, app, "Assets/sounds/musics/town/calm/x.mp3")
    check("game play in a ward: area track replaces the game's",
          result == "played" and played[-1] in ("Assets/sounds/musics/town/calm/b.mp3",
                                                 found["town/calm/a.mp3"][0].replace("\\", "/")), played[-1:])
    first_area_track = played[-1]
    with io.open(world_path, encoding="utf-8") as fh:
        world = json.load(fh)
    check("area sticky: choice is remembered in the world file with the area's name",
          world["areas"]["7"]["chosen"]["level"] == "area:town"
          and world["areas"]["7"]["name"] == "陽光の砦", world)
    check("battle track passes through",
          hook(orig, manager, app, "Assets/sounds/musics/battle/1. Echoes of Valhalla.mp3") == "played"
          and played[-1].endswith("battle/1. Echoes of Valhalla.mp3"))

    # 2. 同じ土地でゲームがもう一度鳴らす（戦闘の後）→ 同じ曲
    for _ in range(5):
        hook(orig, manager, app, "Assets/sounds/musics/town/calm/x.mp3")
    check("game replay in the same area keeps the remembered track",
          all(p == first_area_track for p in played[-5:]), played[-5:])

    # 3. 宿に入る → 鳴らし直す（state 側は絶対パス）
    n = len(played)
    move_to(inn)
    check("enter inn: the inn track is started",
          len(played) == n + 1 and played[-1].endswith("/musics/place/宿.mp3") and os.path.isabs(played[-1]), played[-1:])
    move_to(inn)
    check("stay in the inn: no restart", len(played) == n + 1)

    # 4. 通りへ出る → 土地の曲（覚えた曲）へ戻る
    move_to(ward2)
    check("leave to a ward: back to the remembered area track",
          len(played) == n + 2 and played[-1] == first_area_track, played[-2:])
    move_to(ward)
    check("ward to ward: nothing happens", len(played) == n + 2)

    # 5. 候補の無い施設（役場が無い）→ 土地のまま、鳴らし直さない
    office = Facility("108", "administrative_office", "役場")
    town.nodes["0"].facilities["108"] = office
    move_to(office)
    check("facility without a playlist: the area track continues", len(played) == n + 2)

    # 6. 鳴らせない曲 → FAILED を残して素通し（ゲームは黙らない）
    errors_before = len(ctx.errors)
    move_to(guild)
    check("unplayable facility track on move: logged, nothing else played",
          len(played) == n + 2 and len(ctx.errors) == errors_before + 1, ctx.errors[-1:])
    result = hook(orig, manager, app, "Assets/sounds/musics/town/calm/x.mp3")
    check("unplayable track on a game play: falls back to the game's track",
          result == "played" and played[-1] == "Assets/sounds/musics/town/calm/x.mp3")

    # 7. 世界ファイルの個別指定（施設 id）が施設の種類より先
    world["facilities"]["7/106"] = {"name": "宿", "playlist": {"town/lively/c.wav": 100}}
    with io.open(world_path, "w", encoding="utf-8") as fh:
        json.dump(world, fh, ensure_ascii=False)
    move_to(ward)
    n = len(played)
    move_to(inn)
    check("world file: facility override wins over the facility type",
          len(played) == n + 1 and played[-1] == "Assets/sounds/musics/town/lively/c.wav", played[-1:])

    # 8. ロード直後の形（id の文字列）でも施設と土地を引き当てる
    app.player.location = "106"
    app.player.current_area = "7"
    info = mod.context_of(app)
    check("context: string ids resolve to the facility and area",
          (info["area_id"], info["facility_id"], info["facility_type"], info["size"], info["size_by"])
          == ("7", "106", "inn", "town", "area"), info)
    app.player.location = ward
    app.player.current_area = town

    # 9. size がオブジェクトに無ければセーブの辞書、それも無ければパスから
    bare = Area("9", "無名", None, "", [])
    app2 = App({"9": bare}, bare, None, save_areas={"9": {"size": "village"}})
    check("size: falls back to save_data_dict", mod.size_of(app2, bare, "9", None) == ("village", "save"))
    app2.save_data_dict = {}
    check("size: falls back to the src folder",
          mod.size_of(app2, bare, "9", "Assets/sounds/musics/city/calm/z.mp3") == ("city", "src"))
    check("size: nothing -> None", mod.size_of(app2, bare, "9", None) == (None, ""))

    # 10. ダンジョンは覚えない
    app.player.current_area = dungeon
    app.player.location = cave
    hook(orig, manager, app, "Assets/sounds/musics/dungeons/eerie/d.mp3")
    with io.open(world_path, encoding="utf-8") as fh:
        world = json.load(fh)
    check("dungeon: played from area:dungeon but not remembered",
          played[-1] == "Assets/sounds/musics/dungeons/eerie/d.mp3" and "20" not in world["areas"], world["areas"].keys())
    app.player.current_area = town

    # 11. 戦闘中は場所が変わっても触らない
    app.in_battle = 1
    n = len(played)
    move_to(inn)
    check("in battle: move does nothing", len(played) == n)
    app.in_battle = 0
    move_to(ward)

    # 12. 何も鳴っていなければ触らない（ゲームの切り替えの最中）
    app.music.channels = 0
    n = len(played)
    move_to(inn)
    check("nothing audible: move does nothing", len(played) == n)
    app.music.channels = 1
    move_to(ward)

    # 13. 注入し直しても鳴っている曲を鳴らし直さない（世代をまたぐ控え）
    store = getattr(sys, mod.STORE_ATTR)
    playing_before = store["playing"]
    os.chdir(game)
    try:
        ctx2 = FakeCtx()
        ctx2.generation = "g2"
        mod.apply(ctx2)
    finally:
        os.chdir(old_cwd)
    check("re-apply keeps the store", getattr(sys, mod.STORE_ATTR) is store and store["playing"] == playing_before)

    with io.open(os.path.join(ctx.out_dir, mod.LOG_BASENAME), encoding="utf-8") as fh:
        log_text = fh.read()
    check("log: every decision leaves a [PLACEBGM] line with the trigger",
          log_text.count("[PLACEBGM]") >= 10 and "by move_phase" in log_text and "by game" in log_text,
          log_text.count("[PLACEBGM]"))
    check("log: FAILED is recorded", "FAILED" in log_text)
    check("hook: no unexpected errors",
          all("could not be played" in e for e in ctx.errors), ctx.errors)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("all ok")
