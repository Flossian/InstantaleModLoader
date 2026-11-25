# -*- coding: utf-8 -*-
"""104_balance_area_bgm の計算と記録をゲーム抜きで通す。

    python tools/tests/test_balance_area_bgm.py

  計算   … パスの解析と選び方（以前は apply() の中の自己検証。固定の入力なのでここへ移した。TECH.md §6.1）
  内訳   … 曲の内訳（`[BALANCE] pool under ...` と size ごとの行）は、
           同じ注入の中で前に書いた中身と同じなら書かない。遅れて当て直す boot でモジュールが
           読み直されても繰り返さない（覚えはローダの `ctx.logger(span="injection")`）
  注入   … 注入し直した後（ローダが読み直され、ログが世代送りされる）は新しいログに書く
  中身   … 曲が増えるなど中身が変われば、見出しから全部の行を書く
  出来事 … 選び直した行は今までどおり書く
  覚え   … 「最初に見たときに在ったエリア」は boot をまたいで覚え、覚え直さない

選び直しの全体は `tools/tests/test_rebalance_saved_bgm.py` も見る。
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
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_balance_area_bgm")

# ワールドごとの「最初に見たときに在ったエリア」の控え（`sys` に置いて boot をまたぐ）。
SEEN_ATTR = "_instantale_balance_area_bgm_seen"


def reinject():
    """手で注入し直した形。ローダが読み直されて `ctx.logger(span="injection")` の覚えが空に戻る。"""
    ml._LOGGER_SPANS.clear()


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.mod_dir = MOD_DIR
        self.mod_name = os.path.basename(MOD_DIR)
        self.logs = []
        self.errors = []
        self.hooks = {}

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（span="injection" の鍵に `_mod` を使う）。
    _mod = os.path.basename(MOD_DIR)

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def wrap(self, target, **kw):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate

    def resolve(self, target):
        return None, target, None


def make_musics(root, extra=False):
    """`Assets/sounds/musics/<size>/<mood>/<曲>` の形の偽フォルダ。"""
    # 素のゲームの size（city / village / dungeons / town）を4つとも置く。
    tracks = {"town": {"calm": ["a.mp3", "b.mp3"], "eerie": ["c.mp3"]},
              "dungeons": {"eerie": ["d.mp3"], "tense": ["e.mp3"]},
              "city": {"calm": ["g.mp3"]},
              "village": {"calm": ["h.mp3"]}}
    if extra:
        tracks["town"]["solemn"] = ["f.mp3"]
    for size, moods in tracks.items():
        for mood, names in moods.items():
            folder = os.path.join(root, size, mood)
            os.makedirs(folder, exist_ok=True)
            for name in names:
                io.open(os.path.join(folder, name), "wb").close()


def apply_mod(out_dir, root):
    """mod を読み直して当てる（遅れて当て直す boot と同じく、モジュールは毎回読み直される）。"""
    spec = importlib.util.spec_from_file_location(
        "balance_area_bgm_mod", MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.music_root = lambda: root
    ctx = FakeCtx(out_dir)
    module.apply(ctx)
    return module, ctx


def read_log(out_dir):
    path = os.path.join(out_dir, "bgm.log")
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def pool_heads(out_dir):
    return [line for line in read_log(out_dir) if "[BALANCE] pool under" in line]


def check_rules(module, pool):
    """パスの解析と選び方（以前は apply() の中の `_self_test`）。pool は偽フォルダから作った曲の一覧。"""
    M = module
    real = "Assets/sounds/musics/town/solemn/Ambient 7 Loop.mp3"
    check("解析: / 区切り", M.parse_bgm(real) ==
          ("Assets/sounds/musics", "town", "solemn", "Ambient 7 Loop.mp3", "/"), M.parse_bgm(real))
    check("解析: \\ 区切り",
          M.parse_bgm(r"Assets\sounds\musics\dungeons\eerie\Hunted.mp3") ==
          (r"Assets\sounds\musics", "dungeons", "eerie", "Hunted.mp3", "\\"))
    check("解析: musics 直下の曲は対象外", M.parse_bgm("Assets/sounds/musics/other.mp3") is None)
    check("解析: 空は対象外", M.parse_bgm("") is None)
    check("解析: None は対象外", M.parse_bgm(None) is None)
    check("解析: 組み立て直すと元に戻る",
          M.build_bgm(*M.parse_bgm(real)[:4], sep="/") == real)

    # 3曲に対して6エリアなら、2/2/2 に分かれる。
    fake = {"town": [("a", "1.mp3"), ("a", "2.mp3"), ("b", "3.mp3")]}
    areas = dict((str(i), {"bgm": "Assets/sounds/musics/town/a/1.mp3"}) for i in range(6))
    M.balance_world(areas, fake)
    spread = {}
    for area in areas.values():
        spread[area["bgm"]] = spread.get(area["bgm"], 0) + 1
    check("選び方: 均等に散らばる", sorted(spread.values()) == [2, 2, 2], spread)
    check("選び方: 同じデータへの2回目は何も変えない", M.balance_world(areas, fake) == [])

    # フォルダを決めるのは size であってパスではない。size の dungeon はフォルダ dungeons。
    misfiled = {"0": {"size": "dungeon",
                      "bgm": "Assets/sounds/musics/town/calm/Ambient 1.mp3"}}
    M.balance_world(misfiled, pool)
    moved = M.parse_bgm(misfiled["0"]["bgm"])
    check("選び方: size=dungeon は dungeons のフォルダから引く",
          moved is not None and moved[1] == "dungeons", misfiled)
    for value, want in (("town", "town"), ("village", "village"),
                        ("city", "city"), ("dungeon", "dungeons")):
        check("選び方: size={} -> {}".format(value, want),
              M.size_folder({"size": value}, pool) == want)

    # bgm が空でも、size が分かっていれば曲が入る。
    silent = {"0": {"size": "city", "bgm": ""}, "1": {"size": "town"}}
    M.balance_world(silent, pool)
    check("選び方: 空の bgm を size から埋める",
          (M.parse_bgm(silent["0"].get("bgm")) or [None, None])[1] == "city", silent)
    check("選び方: bgm の無いエリアを size から埋める",
          (M.parse_bgm(silent["1"].get("bgm")) or [None, None])[1] == "town", silent)

    # size もパスも無い場合は判断材料が無いので、何もしない。
    nothing = {"0": {"bgm": ""}, "1": {}}
    M.balance_world(nothing, pool)
    check("選び方: size もパスも無ければ触らない",
          nothing["0"]["bgm"] == "" and "bgm" not in nothing["1"], nothing)

    # mood フォルダの外を指す曲には触らない。
    special = {"0": {"size": "town", "bgm": "Assets/sounds/musics/それ以外.mp3"}}
    M.balance_world(special, pool)
    check("選び方: musics 直下の単発の曲には触らない",
          special["0"]["bgm"] == "Assets/sounds/musics/それ以外.mp3", special)

    # only を指定したとき、それ以外のエリアは書き換わらない。
    mixed = dict((str(i), {"bgm": "Assets/sounds/musics/town/a/1.mp3"}) for i in range(4))
    before = mixed["0"]["bgm"]
    M.balance_world(mixed, fake, only={"3"})
    check("選び方: only= の外は書き換えない",
          mixed["0"]["bgm"] == before and mixed["1"]["bgm"] == before, mixed)

    check("並べ方: id は数の順で、数でない id は後ろ",
          M.area_order({"10": 0, "2": 0, "x": 0, "1": 0}) == ["1", "2", "10", "x"])


def main():
    print("== 104_balance_area_bgm ==")
    tmp = tempfile.mkdtemp(prefix="instantale_bgm_")
    try:
        root = os.path.join(tmp, "musics")
        out_dir = os.path.join(tmp, "out")
        make_musics(root)
        if hasattr(sys, SEEN_ATTR):
            delattr(sys, SEEN_ATTR)              # 新しいプロセスから始める
        reinject()

        module, ctx = apply_mod(out_dir, root)
        lines = read_log(out_dir)
        check("内訳: 最初の apply で書く",
              len(pool_heads(out_dir)) == 1
              and any("town" in line and "3 track(s) over 2 mood(s)" in line for line in lines),
              lines)
        check("apply で ERROR を書かない", not any(level == "ERROR" for level, _msg in ctx.logs),
              ctx.logs)
        check_rules(module, module.scan_pool(root))

        for _ in range(3):
            apply_mod(out_dir, root)             # 遅れて当て直す boot
        check("内訳: 同じ注入・同じ中身なら繰り返さない",
              len(pool_heads(out_dir)) == 1 and len(read_log(out_dir)) == len(lines),
              read_log(out_dir))

        # 曲が増えた（キャッシュはモジュールごとなので、読み直せば数え直す）。
        make_musics(root, extra=True)
        module, ctx = apply_mod(out_dir, root)
        lines = read_log(out_dir)
        check("中身: 変われば見出しから全部の行を書く",
              len(pool_heads(out_dir)) == 2
              and any("4 track(s) over 3 mood(s)" in line for line in lines)
              and sum("track(s) over" in line for line in lines) == 8,
              lines)

        # 手で注入し直した形。ローダが読み直され、ログは世代送りされる。新しいログに書き直す。
        log_path = os.path.join(out_dir, "bgm.log")
        os.replace(log_path, log_path + ".1")
        reinject()
        module, ctx = apply_mod(out_dir, root)
        check("注入: 新しいログに書く", len(pool_heads(out_dir)) == 1, read_log(out_dir))
        apply_mod(out_dir, root)
        check("注入: その後の当て直しでは繰り返さない",
              len(pool_heads(out_dir)) == 1, read_log(out_dir))

        # 選び直した行は書く（エリアが新しく書き出された形）。
        hook = ctx.hooks["save_area_json:write_area_data_to_world_dict"]
        world = {"name": "検査の世界",
                 "areas": {"1": {"size": "town",
                                 "bgm": "Assets/sounds/musics/town/calm/a.mp3"}}}

        def write_area(world_dict, area_id):
            world_dict["areas"][str(area_id)] = {
                "size": "town", "bgm": "Assets/sounds/musics/town/calm/a.mp3"}

        hook(write_area, world, 2)
        balanced = [line for line in read_log(out_dir) if "] [BALANCE] write_area" in line]
        check("出来事: 選び直した行と初めて見た行は書く",
              any("first sight" in line for line in balanced)
              and any("area 2:" in line for line in balanced), balanced)
        check("例外を握り潰していない", not ctx.errors, ctx.errors)

        # 版3: 「最初に見たときに在ったエリア」は boot をまたいで覚えている。
        # 版2はモジュール変数で、遅れて当て直す boot のたびに空に戻り、
        # 作られた瞬間の包みを通らずに増えたエリアを「前からある」として覚え直していた。
        _module, ctx2 = apply_mod(out_dir, root)          # 遅れて当て直す boot
        world["areas"]["3"] = {"size": "town",
                               "bgm": "Assets/sounds/musics/town/calm/a.mp3"}
        before = len(read_log(out_dir))
        save = ctx2.hooks["save_world_json:write_obfuscated_json_file"]
        save(lambda *a, **k: None, "save.json", world)
        after = read_log(out_dir)[before:]
        check("boot をまたいでも覚え直さない（first sight を書かない）",
              not any("first sight" in line for line in after), after)
        check("包みを通らずに増えたエリアを次の保存で選び直す",
              any("write_obfuscated_json_file area 3:" in line for line in after), after)
        check("例外を握り潰していない（boot の後）", not ctx2.errors, ctx2.errors)

        # 同じ名前の別のワールド。前のワールドの控えで新旧を決めない（エリアの名前で見分ける）。
        def town(name):
            return {"name": name, "size": "town",
                    "bgm": "Assets/sounds/musics/town/calm/a.mp3"}

        first = {"world_data": {"world_name": "同名の世界"}, "areas": {"1": town("港町")}}
        save(lambda *a, **k: None, "save.json", first)
        second = {"world_data": {"world_name": "同名の世界"},
                  "areas": {"1": town("山村"), "2": town("谷の村")}}
        before = len(read_log(out_dir))
        save(lambda *a, **k: None, "save.json", second)
        after = read_log(out_dir)[before:]
        check("同じ名前の別のワールドは初めて見たものとして扱う",
              any("different world with the same name" in line for line in after)
              and any("first sight" in line for line in after), after)
        check("  → 別のワールドの既存のエリアを選び直さない",
              not any("write_obfuscated_json_file area 2:" in line for line in after), after)

        # 保存は世界側（world_data.json）とセーブ側（savedata.json）を同じ関数で書き、
        # セーブ側にだけ依頼のダンジョンが在る（GAME.md §2.28）。先に世界側を見ても、
        # セーブ側にだけ在る既存のダンジョンを新しいエリアとして選び直さない。
        def dungeon(name):
            return {"name": name, "size": "dungeon",
                    "bgm": "Assets/sounds/musics/dungeons/eerie/a.mp3"}

        skeleton = {"world_data": {"world_name": "二つの辞書の世界"},
                    "areas": {"1": town("港町")}}
        progress = {"world_data": {"world_name": "二つの辞書の世界"},
                    "player_data": {"name": "主人公"},
                    "areas": {"1": town("港町"), "7": dungeon("古い洞窟")}}
        before = len(read_log(out_dir))
        save(lambda *a, **k: None, "world_data.json", skeleton)
        save(lambda *a, **k: None, "savedata.json", progress)
        after = read_log(out_dir)[before:]
        check("世界側とセーブ側はそれぞれ初めて見たものとして扱う",
              sum("first sight" in line for line in after) == 2, after)
        check("  → セーブ側にだけ在る既存のダンジョンを選び直さない",
              not any("write_obfuscated_json_file area 7:" in line for line in after), after)
        progress["areas"]["8"] = dungeon("新しい洞窟")
        before = len(read_log(out_dir))
        save(lambda *a, **k: None, "savedata.json", progress)
        after = read_log(out_dir)[before:]
        check("  → セーブ側で後から増えたダンジョンは選び直す",
              any("write_obfuscated_json_file area 8:" in line for line in after), after)
    finally:
        if hasattr(sys, SEEN_ATTR):
            delattr(sys, SEEN_ATTR)
        reinject()
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
