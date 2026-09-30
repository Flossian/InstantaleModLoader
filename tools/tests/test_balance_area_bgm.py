# -*- coding: utf-8 -*-
"""104_balance_area_bgm の記録をゲーム抜きで通す。

    python tools/tests/test_balance_area_bgm.py

選び方そのものは apply() の中の自己検証（`_self_test`）と
`tools/tests/test_rebalance_saved_bgm.py` が見る。ここで見るのは `out/bgm.log` の書き方。

  内訳   … 曲の内訳（`[BALANCE] pool under ...` と size ごとの行）は、
           前に書いた中身と同じなら書かない。遅れて当て直す boot と注入し直しでモジュールが
           読み直されても繰り返さない（版3。版2は apply のたびに書いていた）
  世代送り … 注入のときにログが世代送りされた（ファイルが入れ替わった）後は書く
  中身   … 曲が増えるなど中身が変われば書く
  出来事 … 選び直した行は今までどおり書く
  覚え   … 「最初に見たときに在ったエリア」は boot をまたいで覚え、覚え直さない（版3）
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

# 内訳の控えの置き場（mod の `POOL_LOG_ATTR`）。mod を読む前に捨てるので名前で持つ。
POOL_LOG_ATTR = "_instantale_balance_area_bgm_pool_logged"
# ワールドごとの「最初に見たときに在ったエリア」の控え（版3で `sys` へ移した）。
SEEN_ATTR = "_instantale_balance_area_bgm_seen"


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.mod_dir = MOD_DIR
        self.logs = []
        self.errors = []
        self.hooks = {}

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    _mod = None

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
    # 自己検証（`_self_test`）が city / village / dungeons / town を引くので4つとも置く。
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


def main():
    print("== 104_balance_area_bgm ==")
    tmp = tempfile.mkdtemp(prefix="instantale_bgm_")
    try:
        root = os.path.join(tmp, "musics")
        out_dir = os.path.join(tmp, "out")
        make_musics(root)
        for attr in (POOL_LOG_ATTR, SEEN_ATTR):
            if hasattr(sys, attr):
                delattr(sys, attr)               # 新しいプロセスから始める

        module, ctx = apply_mod(out_dir, root)
        lines = read_log(out_dir)
        check("内訳: 最初の apply で書く",
              len(pool_heads(out_dir)) == 1
              and any("town" in line and "3 track(s) over 2 mood(s)" in line for line in lines),
              lines)
        check("自己検証が通る", not any(level == "ERROR" for level, _msg in ctx.logs), ctx.logs)

        for _ in range(3):
            apply_mod(out_dir, root)             # 遅れて当て直す boot
        check("内訳: 同じ注入・同じ中身なら繰り返さない",
              len(pool_heads(out_dir)) == 1 and len(read_log(out_dir)) == len(lines),
              read_log(out_dir))

        # 曲が増えた（キャッシュはモジュールごとなので、読み直せば数え直す）。
        make_musics(root, extra=True)
        module, ctx = apply_mod(out_dir, root)
        lines = read_log(out_dir)
        check("中身: 変われば書く",
              len(pool_heads(out_dir)) == 2
              and any("4 track(s) over 3 mood(s)" in line for line in lines),
              lines)

        # 注入のときにログが世代送りされた形。新しいログに書き直す。
        log_path = os.path.join(out_dir, "bgm.log")
        os.replace(log_path, log_path + ".1")
        module, ctx = apply_mod(out_dir, root)
        check("世代送り: 新しいログに書く", len(pool_heads(out_dir)) == 1, read_log(out_dir))
        apply_mod(out_dir, root)
        check("世代送り: その後の当て直しでは繰り返さない",
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
    finally:
        for attr in (POOL_LOG_ATTR, SEEN_ATTR):
            if hasattr(sys, attr):
                delattr(sys, attr)
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
