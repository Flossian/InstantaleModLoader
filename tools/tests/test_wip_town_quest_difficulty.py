# -*- coding: utf-8 -*-
"""922_town_quest_difficulty（開発中）をゲーム抜きで通す。

    python tools/tests/test_wip_town_quest_difficulty.py

  範囲       … 収め方（上限・逆さま・幅3以上）、中心と幅からの範囲、ゲームの範囲の表
  街と道     … 世界の areas から街だけを拾い、片側だけの道を両側へ足す
  なだらか   … 開始の街・最も遠い街・固定した街が錨。行き止まりも最も遠ければ錨。たどり着けない街はそのまま
  窓口       … quest_bands の登録・差し替え・用済み・例外・形の違う戻り
  初訪問     … 街の中身を作る間の `sample(range, 3)` の最初の1回だけ、決めた範囲から引く。範囲の無い街は素通し
  探す       … 依頼を探す間の最初の `randint` だけ。窓の外と別のスレッドは素通し
  上限・読み直し … 設定の上限で頭打ち。控えを書き換えれば次の引きから効く
  画面の材料 … 骨格とセーブの読み込み、控えの組み立て（素と同じで固定なしは書かない）、今の依頼に合わせる
"""
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "town_quest_difficulty"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import quest_bands, state  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find *{} in {}: {}".format(suffix, MODS_DIR, matches))
    return os.path.join(MODS_DIR, matches[0])


FOLDER = find_mod("_town_quest_difficulty")
sys.path.insert(0, FOLDER)
import bands  # noqa: E402

WORLD = "試しの世界"
BUILD = "save_area_json:write_area_data_to_world_dict"
SEARCH = "__main__:DisplayQuestChoice.generate_random_quest"


class FakeCtx:
    _mod = "922_town_quest_difficulty"

    def __init__(self, out_dir, state_dir):
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.gone = False

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def read_json(self, path, default=None):
        return ml.read_json(path, default)

    def superseded(self):
        return self.gone

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    path = os.path.join(FOLDER, "town_quest_difficulty.py")
    name = "town_quest_difficulty_mod"
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[FOLDER])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_towns(state_dir, towns):
    path = os.path.join(state_dir, "town_quest_difficulty", state.world_filename(WORLD))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump({"towns": towns}, fh, ensure_ascii=False)
    return path


def log_text():
    path = os.path.join(OUT_DIR, "out", "town_quest_difficulty.log")
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def make_app(area_id):
    area = types.SimpleNamespace(id=area_id, name="街" + area_id)
    return types.SimpleNamespace(player=types.SimpleNamespace(current_area=area),
                                 world_dict={"world_data": {"world_name": WORLD}},
                                 world=types.SimpleNamespace(areas={area_id: area}))


# ----------------------------------------------------------------- 範囲
def test_bands():
    check("収める: 幅3以上へ広げる", bands.normalize(5, 5) == (5, 7))
    check("収める: 逆さまを並べ直す", bands.normalize(12, 8) == (8, 12))
    check("収める: 上限で切って幅を保つ", bands.normalize(76, 90) == (74, 76), bands.normalize(76, 90))
    check("収める: 上限 100", bands.normalize(90, 100, 100) == (90, 100))
    check("収める: 読めない値は None", bands.normalize("x", 3) is None)
    check("中心と幅: 上の端で内側へずらす", bands.band_around(75, 9) == (68, 76))
    check("中心と幅: 下の端", bands.band_around(1, 9) == (1, 9))
    check("ゲームの範囲: id 2", bands.game_band("2") == (19, 27))
    check("ゲームの範囲: id 9 以降は全域", bands.game_band("13") == (1, 76))
    check("上限: 範囲外は収める", bands.clamp_limit(500) == 100 and bands.clamp_limit("x") == 76)


def map_areas():
    roads = {0: [1, 4, 9], 1: [2], 2: [10, 5], 3: [10, 6, 11], 4: [7], 5: [9, 11, 7],
             6: [7, 8, 10], 8: [], 9: [], 10: [12], 11: [13], 12: [], 13: [], 7: []}
    areas = {str(k): {"name": "街{}".format(k), "size": "town", "connections": [str(x) for x in v]}
             for k, v in roads.items()}
    areas["20"] = {"name": "洞窟", "size": "dungeon", "connections": ["0"]}
    areas["21"] = {"name": "依頼の土地", "size": None, "connections": []}
    areas["30"] = {"name": "離れ小島", "size": "village", "connections": []}
    return areas


def test_towns_and_smooth():
    towns = bands.towns_of(map_areas())
    check("街と道: 街だけを拾う", sorted(towns, key=bands.id_sort_key)
          == [str(i) for i in range(14)] + ["30"], sorted(towns))
    check("街と道: 片側だけの道を両側へ", "0" in towns["9"]["connections"] and "5" in towns["9"]["connections"])
    check("街と道: ダンジョンへの道は落とす", "20" not in towns["0"]["connections"])
    depth = bands.depths(towns, "0")
    check("道の数", depth["3"] == 4 and depth["12"] == 4 and "30" not in depth, depth)

    plain = bands.smooth(towns, {}, [])
    check("なだらか: 開始の街", plain["0"] == (1, 5))
    check("なだらか: 最も遠い街（行き止まり含む）", all(plain[a] == (67, 76) for a in ("3", "8", "12", "13")))
    check("なだらか: 道の段で上がる",
          center(plain["1"]) < center(plain["2"]) < center(plain["6"]) < center(plain["3"]), plain)
    check("なだらか: たどり着けない街はそのまま", plain["30"] == (1, 76))
    check("なだらか: 錨でない街の幅は 9", all(b[1] - b[0] + 1 == 9 for a, b in plain.items()
                                          if a not in ("0", "30", "3", "8", "12", "13")), plain)

    pinned = bands.smooth(towns, {"2": (19, 27), "13": (40, 48)}, ["2", "13"])
    check("なだらか: 固定した街はそのまま", pinned["2"] == (19, 27) and pinned["13"] == (40, 48))
    check("なだらか: 固定の手前が寄る", center(pinned["1"]) < center(plain["1"]), (pinned["1"], plain["1"]))
    check("なだらか: 固定した遠い街へ下がる", center(pinned["11"]) < center(plain["11"]), (pinned["11"], plain["11"]))

    high = bands.smooth(towns, {}, [], end_band=(91, 100), limit=100)
    check("なだらか: 上限 100 の遠い街", high["3"] == (91, 100))
    capped = bands.smooth(towns, {}, [], end_band=(91, 100), limit=76)
    check("なだらか: 上限で頭打ち", all(b[1] <= 76 for b in capped.values()))


def center(band):
    return (band[0] + band[1]) / 2.0


# ----------------------------------------------------------------- 窓口
def test_window():
    quest_bands.reset()
    alive = types.SimpleNamespace(superseded=lambda: False)
    gone = types.SimpleNamespace(superseded=lambda: True)
    check("窓口: 誰もいない", quest_bands.band_of(None, "9") == (None, None))
    quest_bands.provide("a", alive, lambda app, area: (10, 5) if area == "9" else None)
    check("窓口: 逆さまを並べ直して返す", quest_bands.band_of(None, 9) == ((5, 10), "a"))
    check("窓口: 口を出さない街", quest_bands.band_of(None, "8") == (None, None))
    quest_bands.provide("b", gone, lambda app, area: (1, 2))
    check("窓口: 用済みは聞かない", quest_bands.band_of(None, "9") == ((5, 10), "a"))
    quest_bands.provide("c", alive, lambda app, area: 1 / 0)
    check("窓口: 投げた登録は飛ばす", quest_bands.band_of(None, "9") == ((5, 10), "a")
          and quest_bands._store()["errors"])
    quest_bands.provide("d", alive, lambda app, area: ("1", "2"))
    check("窓口: 形の違う戻りは飛ばす", quest_bands.band_of(None, "9") == ((5, 10), "a"))
    quest_bands.withdraw("a")
    check("窓口: 下げた登録", quest_bands.band_of(None, "9") == (None, None))
    quest_bands.reset()


# ----------------------------------------------------------------- 本体
def setup():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    state_dir = os.path.join(OUT_DIR, "state")
    os.makedirs(state_dir, exist_ok=True)
    quest_bands.reset()
    module = load_mod()
    ctx = FakeCtx(os.path.join(OUT_DIR, "out"), state_dir)
    module.apply(ctx)
    return module, ctx, state_dir


def test_game_hooks():
    module, ctx, state_dir = setup()
    path = write_towns(state_dir, {"9": {"lo": 8, "hi": 12, "pinned": False}})
    check("フック: 4つ", all(t in ctx.hooks for t in (BUILD, SEARCH, "random:sample", "random:randint")),
          sorted(ctx.hooks))
    sample, randint = ctx.hooks["random:sample"], ctx.hooks["random:randint"]

    seen = {}

    def build_orig(world_dict, area_id):
        seen["first"] = sample(random.sample, range(1, 77), k=3)
        seen["second"] = sample(random.sample, range(1, 77), 3)
        seen["list"] = sample(random.sample, ["a", "b", "c"], 3)
        return "built"

    world_dict = {"world_data": {"world_name": WORLD}}
    result = ctx.hooks[BUILD](build_orig, world_dict, "9")
    first = seen["first"]
    check("初訪問: 戻りはゲームのまま", result == "built")
    check("初訪問: 最初の1回は範囲から重ならず3つ", sorted(first) == sorted(set(first))
          and all(8 <= v <= 12 for v in first) and len(first) == 3, first)
    check("初訪問: 2回目は素通し（窓は1回）", len(seen["second"]) == 3)
    check("初訪問: 範囲でない母集団は素通し", sorted(seen["list"]) == ["a", "b", "c"])
    check("初訪問: ログ", "初訪問: 土地 9 range(1, 77) -> 8〜12 を引いた" in log_text(), log_text())

    calls = []

    def recorder(*args):
        calls.append(args)
        return 42

    ctx.hooks[BUILD](lambda wd, area: sample(recorder, range(1, 77), 3), world_dict, "10")
    check("初訪問: 範囲の無い街は素通し", calls == [(range(1, 77), 3)], calls)
    check("初訪問: 窓の外は素通し", sample(recorder, range(3, 9), 3) == 42 and calls[-1] == (range(3, 9), 3))

    app = make_app("9")
    owner = types.SimpleNamespace(app=app)
    got = {}

    def search_orig(self):
        got["first"] = randint(random.randint, 1, 76)
        got["second"] = randint(recorder, 1, 1000)
        return "searched"

    check("探す: 戻りはゲームのまま", ctx.hooks[SEARCH](search_orig, owner) == "searched")
    check("探す: 最初の1回は範囲から", 8 <= got["first"] <= 12, got)
    check("探す: 2回目は素通し", calls[-1] == (1, 1000))
    check("探す: 窓の外は素通し", randint(recorder, 1, 76) == 42 and calls[-1] == (1, 76))
    check("探す: ログ", "探す: 土地 9 randint(1, 76) -> 8〜12 を引いた" in log_text())

    # 別のスレッドの randint は、こちらの窓に吸われない。
    other = {}
    gate = threading.Event()
    done = threading.Event()

    def worker():
        gate.wait(5)
        other["value"] = randint(recorder, 1, 76)
        done.set()

    thread = threading.Thread(target=worker)
    thread.start()

    def slow_search(self):
        gate.set()
        done.wait(5)
        return randint(random.randint, 1, 76)

    mine = ctx.hooks[SEARCH](slow_search, owner)
    thread.join(5)
    check("探す: 別のスレッドは素通し", other.get("value") == 42 and calls[-1] == (1, 76))
    check("探す: 自分のスレッドは範囲から", 8 <= mine <= 12, mine)

    check("窓口: 本体が範囲を出す", quest_bands.band_of(app, "9")[0] == (8, 12))
    check("窓口: 決めていない街は口を出さない", quest_bands.band_of(app, "10") == (None, None))

    # 控えを書き換えると次の引きから効く（更新時刻と大きさで読み直す）。
    time.sleep(0.02)
    write_towns(state_dir, {"9": {"lo": 30, "hi": 40, "pinned": True}})
    later = ctx.hooks[SEARCH](lambda self: randint(random.randint, 1, 76), owner)
    check("読み直し: 書き換えた範囲から", 30 <= later <= 40, later)

    # 上限: 76 では 90〜100 を頭打ちに、100 ではそのまま。
    write_towns(state_dir, {"9": {"lo": 90, "hi": 100}})
    capped = [ctx.hooks[SEARCH](lambda self: randint(random.randint, 1, 76), owner) for _ in range(30)]
    check("上限 76: 74〜76 に収める", all(74 <= v <= 76 for v in capped), capped)
    module.DIFFICULTY_LIMIT = 100
    raised = [ctx.hooks[SEARCH](lambda self: randint(random.randint, 1, 76), owner) for _ in range(30)]
    check("上限 100: 90〜100", all(90 <= v <= 100 for v in raised) and max(raised) > 76, raised)
    check("例外を記録していない", not ctx.errors, ctx.errors)
    check("控えは書かない", os.path.getsize(path) > 0 and not os.path.exists(path + ".tmp"))
    quest_bands.reset()


# ----------------------------------------------------------------- 画面の材料
def test_tool_data():
    data_dir = os.path.join(OUT_DIR, "data")
    world_dir = os.path.join(data_dir, "worlds", "w1")
    save_dir = os.path.join(data_dir, "saves", "w1")
    os.makedirs(world_dir, exist_ok=True)
    os.makedirs(save_dir, exist_ok=True)
    skeleton = {"world_data": {"world_name": WORLD}, "areas": map_areas(),
                "quests": {"0": {"neighboring_settlement_id": "9", "difficulty": 10}}}
    save = {"quests": {"0": {"neighboring_settlement_id": "9", "difficulty": 10},
                       "1": {"neighboring_settlement_id": "9", "difficulty": 14},
                       "2": {"neighboring_settlement_id": "2", "difficulty": True}}}
    for path, value in ((os.path.join(world_dir, "world_data.json"), skeleton),
                        (os.path.join(save_dir, "savedata.json"), save)):
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False)
    os.makedirs(os.path.join(data_dir, "worlds", "空のフォルダ"), exist_ok=True)

    import tool
    worlds = tool.world_folders(data_dir)
    check("画面: 骨格のある世界だけ", worlds == [(WORLD, "w1")], worlds)
    loaded = tool.load_world("w1", data_dir)
    check("画面: 街の数", len(loaded["towns"]) == 15)
    check("画面: 依頼はセーブから（真偽値は数えない）", loaded["quests"] == {"9": [10, 14]} and loaded["has_save"],
          loaded["quests"])
    check("画面: 依頼の表示", tool.quests_text([10, 14]) == "10〜14（2件）" and tool.quests_text([]) == "まだ無い")
    check("画面: 今の依頼に合わせる", tool.fit_quests([10, 14], 76) == (10, 14)
          and tool.fit_quests([70], 76) == (70, 72) and tool.fit_quests([], 76) is None)
    rows = {"0": ((1, 5), False), "2": ((19, 27), True), "9": ((8, 12), False), "99": ((1, 3), False)}
    record = tool.build_record(loaded["towns"], rows, {"width": 9})
    check("画面: 素と同じで固定なしは書かない・固定は書く・知らない街は書かない",
          record["towns"] == {"2": {"lo": 19, "hi": 27, "pinned": True},
                              "9": {"lo": 8, "hi": 12, "pinned": False}}, record)
    check("画面: 上限を上げた遠い街の既定", tool.default_end(100) == (91, 100) and tool.default_end(76) == (67, 76))


def main():
    test_bands()
    test_towns_and_smooth()
    test_window()
    test_game_hooks()
    test_tool_data()
    print()
    if failures:
        print("{} FAILED: {}".format(len(failures), ", ".join(failures)))
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
