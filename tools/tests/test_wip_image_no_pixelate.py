# -*- coding: utf-8 -*-
"""909_image_no_pixelate（開発中。正式化しない）をゲーム抜きで通す。

    python tools/tests/test_wip_image_no_pixelate.py

`test_wip_*.py` は CI の対象外（TECH.md §2.1）。

偽の画像（PIL の持ち物だけ真似たもの）と偽の `ctx` を差し込み、次を確認する。

  見分け   … PIL の画像だけを画像と見る。numpy の配列・文字列・None は通さない
  形       … **戻り値の形を決め打ちしない**。tuple・list・画像1枚のどれでも、
             同じ形・同じ寸法のまま返す（実機で `a, b = ...` に画像1枚を返して落ちた）
  作り直し … 戻って来た画像を**全部**、元の絵から作り直す。ゲームが縮めた側
             （2つ目）も対象。ここを落とすと立ち絵に効かない（版2の失敗）
  縮め方   … 縮めるときは最近傍ではなくフィルタを使う。PIL は import しない
  縦横比   … 比が元の絵と違う画像（添え物）は触らない
  控え     … 減色の段は1つ手前の元の絵を使う。使ったら捨てる。スレッドを跨がない
  必ず呼ぶ … 元の工程は毎回呼ぶ（戻り値の形はゲームのものを使う）
  落とし方 … 画像でない値・差し替えられない戻り値は、元の結果をそのまま返し、
             警告は1度だけ出す
  顔       … `detect_face_coordinates` は何も変えず、記録だけ残す
  設置     … 設定どおりの対象にだけ当たる。背景は既定で当たらない
  巻き添え … 当てるのは各モジュールの写しで、`alias_scan` は切ってある
  数       … ログは工程ごとに `LOG_LIMIT` 行で止まる
  対象名   … リコンのダンプに在る名前と一致する（在れば照合する）
"""
import importlib.util
import io
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.join(ROOT, "out", "test")
RECON = os.path.join(ROOT, "out", "recon", "game_modules.txt")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

#: `resample_filter` はこのモジュールから引く（PIL の代わり）。
#: 偽画像の `__module__` がここなので、本物と同じ探し方が通る。
LANCZOS = "lanczos"


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


MOD_DIR, MOD = find_mod("_image_no_pixelate")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class FakeImage(object):
    """PIL の Image の代わり。作り直しの判定に要る持ち物だけ持つ。

    `who` は「どの絵から来たか」の印。差し替わったかを中身で見分ける。
    """

    def __init__(self, width=512, height=1024, mode="RGBA", who="素",
                 resample=None):
        self.size = (width, height)
        self.mode = mode
        self.who = who
        self.resample = resample      # 縮めるときに渡されたフィルタ

    def copy(self):
        return FakeImage(self.size[0], self.size[1], self.mode, self.who)

    def convert(self, mode):
        return FakeImage(self.size[0], self.size[1], mode, self.who)

    def resize(self, size, resample=None):
        return FakeImage(size[0], size[1], self.mode, self.who, resample)


class FakeArray(object):
    """numpy の配列の代わり。`size` と `copy` は在るが PIL ではない。"""

    def __init__(self):
        self.size = 512 * 1024

    def copy(self):
        return FakeArray()


class Original(object):
    """包まれた側。呼ばれた回数を数え、実機で観測した形を返す。

    `pair` は `pixel_art_process` の実測（`(入力そのまま, 縮めた絵)`）。
    """

    def __init__(self, shape="pair", small=(165, 330)):
        self.shape = shape
        self.small = small
        self.calls = []

    def __call__(self, image, *args, **kwargs):
        self.calls.append((image, args, kwargs))
        w, h = image.size if hasattr(image, "size") and isinstance(
            image.size, tuple) else (512, 1024)
        big = FakeImage(w, h, "RGBA", who="ゲーム")
        small = FakeImage(self.small[0], self.small[1], "RGBA", who="ゲーム")
        if self.shape == "pair":
            return (big, small)
        if self.shape == "single":
            return big
        if self.shape == "list":
            return [big, small]
        if self.shape == "palette":            # 縦横比の違う添え物つき
            return (big, FakeImage(16, 1, "P", who="ゲーム"))
        if self.shape == "nothing":            # 画像を返さない形
            return ("path/to.png", 3)
        raise AssertionError(self.shape)


class FakeCtx(object):
    """`apply(ctx)` が使うぶんだけの ctx。`wrap` は当てずに控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}          # 対象 -> 包んだ関数
        self.kwargs = {}         # 対象 -> wrap に渡された引数
        self.logs = []
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログと警告は本物の実装をそのまま借りる（`test_wip_party_opponent` と同じ）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label, cap=cap)

    def warner(self, tag):
        import instantale_modloader as _ml
        return _ml.ModContext.warner(self, tag)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            self.kwargs[target] = kw
            return func
        return decorator


def fresh_mod(**overrides):
    """MOD を読み直し、設定を上書きしてから `apply()` を通す。"""
    spec = importlib.util.spec_from_file_location(
        "mod_image_no_pixelate", MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules["mod_image_no_pixelate"] = module
    spec.loader.exec_module(module)
    for name, value in overrides.items():
        setattr(module, name, value)
    ctx = FakeCtx(OUT_DIR)
    log_path = ctx.out_path(module.LOG_BASENAME)
    if os.path.exists(log_path):
        os.remove(log_path)
    module.apply(ctx)
    return module, ctx


def log_lines(module, ctx):
    path = os.path.join(ctx.out_dir, module.LOG_BASENAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [line.rstrip("\n") for line in fh if line.strip()]


def warns(ctx):
    return [msg for level, msg in ctx.logs if level == "WARN"]


def pixel_of(module, ctx):
    return ctx.hooks[module.CREATURE + ":pixel_art_process"]


def reduce_of(module, ctx):
    return ctx.hooks[module.CREATURE + ":reduce_image_colors"]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("見分け")
    module, ctx = fresh_mod()
    check("PIL の画像は画像と見る", module.is_image(FakeImage()))
    check("numpy の配列は画像と見ない", not module.is_image(FakeArray()))
    check("文字列は画像と見ない", not module.is_image("path/to.png"))
    check("None は画像と見ない", not module.is_image(None))

    print("形")
    module, ctx = fresh_mod()
    check("キャラクタの工程に当たっている",
          module.CREATURE + ":pixel_art_process" in ctx.hooks, sorted(ctx.hooks))
    hook = pixel_of(module, ctx)
    orig = Original("pair")
    result = hook(orig, FakeImage(512, 1024, who="元絵"), 256, 256, num_colors=256)
    check("2つ組はそのまま2つ組で返る",
          isinstance(result, tuple) and len(result) == 2, result)
    check("元の工程を必ず呼ぶ", len(orig.calls) == 1, orig.calls)

    module, ctx = fresh_mod()
    single = pixel_of(module, ctx)(Original("single"), FakeImage(), 256, 256)
    check("画像1枚を返す形なら画像1枚で返る", module.is_image(single), single)

    module, ctx = fresh_mod()
    listed = pixel_of(module, ctx)(Original("list"), FakeImage(), 256, 256)
    check("list を返す形なら list で返る",
          isinstance(listed, list) and len(listed) == 2, listed)

    print("作り直し")
    module, ctx = fresh_mod()
    source = FakeImage(512, 1024, who="元絵")
    big, small = pixel_of(module, ctx)(Original("pair"), source, 256, 256)
    check("1つ目は元の絵から作り直す",
          big.who == "元絵" and big.size == (512, 1024), (big.who, big.size))
    check("**縮めた2つ目も**元の絵から作り直す（版2はここを落としていた）",
          small.who == "元絵", small.who)
    check("縮めた側の寸法はゲームのまま", small.size == (165, 330), small.size)

    print("縮め方")
    check("縮めるときはフィルタを渡す", small.resample == LANCZOS, small.resample)
    check("同じ寸法のときは縮めない（写しだけ）", big.resample is None, big.resample)

    print("縦横比")
    module, ctx = fresh_mod()
    source = FakeImage(512, 1024, who="元絵")
    kept_big, palette = pixel_of(module, ctx)(Original("palette"), source, 256, 256)
    check("比の合う画像は作り直す", kept_big.who == "元絵", kept_big.who)
    check("比の違う添え物は触らない",
          palette.who == "ゲーム" and palette.size == (16, 1),
          (palette.who, palette.size))

    print("控え")
    module, ctx = fresh_mod()
    pixel, reduce_ = pixel_of(module, ctx), reduce_of(module, ctx)
    source = FakeImage(512, 1024, who="元絵")
    pixel(Original("pair"), source, 256, 256)              # 控えを置く
    enlarged = FakeImage(330, 660, who="ゲームが2倍にした絵")
    reduced = reduce_(Original("single"), enlarged, 16)
    check("減色は1つ手前の元の絵から作り直す", reduced.who == "元絵", reduced.who)
    check("減色でも寸法はゲームのまま", reduced.size == (330, 660), reduced.size)
    again = reduce_(Original("single"), enlarged, 16)
    check("控えは1度きり（2度目はその回の入力を使う）",
          again.who == "ゲームが2倍にした絵", again.who)

    module, ctx = fresh_mod()
    pixel, reduce_ = pixel_of(module, ctx), reduce_of(module, ctx)
    pixel(Original("pair"), FakeImage(512, 1024, who="別スレッドの元絵"), 256, 256)
    seen = {}

    def other_thread():
        seen["out"] = reduce_(Original("single"),
                              FakeImage(330, 660, who="このスレッドの絵"), 16)

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join()
    check("控えはスレッドを跨がない（別人の絵が回り込まない）",
          seen["out"].who == "このスレッドの絵", seen["out"].who)

    print("落とし方")
    module, ctx = fresh_mod()
    hook = pixel_of(module, ctx)
    orig = Original("pair")
    fallback = hook(orig, FakeArray(), 256, 256)
    check("画像でない値の回も元の工程は呼ぶ", len(orig.calls) == 1, orig.calls)
    check("元の戻り値をそのまま返す",
          isinstance(fallback, tuple) and fallback[0].who == "ゲーム", fallback)
    hook(orig, FakeArray(), 256, 256)
    check("元の工程は毎回呼ばれる", len(orig.calls) == 2, orig.calls)
    check("警告は1度だけ", len(warns(ctx)) == 1, warns(ctx))

    module, ctx = fresh_mod()
    kept = pixel_of(module, ctx)(Original("nothing"), FakeImage(), 256, 256)
    check("画像を返さない形なら元の結果をそのまま返す",
          kept == ("path/to.png", 3), kept)
    check("そのことを警告に出す",
          len(warns(ctx)) == 1 and "差し替える画像が見つからない" in warns(ctx)[0],
          warns(ctx))

    print("顔")
    module, ctx = fresh_mod()
    face = ctx.hooks.get(module.CREATURE + ":detect_face_coordinates")
    check("顔の検出にも当たる", face is not None, sorted(ctx.hooks))

    def detect(image, *args, **kwargs):
        return (10, 20, 30, 40)

    got = face(detect, FakeImage(330, 660), padding=0.25)
    check("顔の検出は何も変えない", got == (10, 20, 30, 40), got)
    check("座標を記録する",
          any("顔の検出: 入力 330x660 RGBA / 座標 (10, 20, 30, 40)" in line
              for line in log_lines(module, ctx)), log_lines(module, ctx))

    print("設置")
    module, ctx = fresh_mod()
    check("既定では背景に当たらない",
          module.BACKGROUND + ":pixel_art_process" not in ctx.hooks,
          sorted(ctx.hooks))
    check("既定では減色に当たる",
          module.CREATURE + ":reduce_image_colors" in ctx.hooks,
          sorted(ctx.hooks))

    module, ctx = fresh_mod(BYPASS_BACKGROUND=True)
    check("背景を入にすると背景にも当たる",
          module.BACKGROUND + ":pixel_art_process" in ctx.hooks,
          sorted(ctx.hooks))
    background = ctx.hooks[module.BACKGROUND + ":pixel_art_process"]
    background(Original("pair"), FakeImage(1024, 512, who="背景"), 256, 256)
    out = reduce_of(module, ctx)(Original("single"),
                                 FakeImage(330, 660, who="キャラの絵"), 16)
    check("背景は控えを置かない（キャラクタの減色へ回り込まない）",
          out.who == "キャラの絵", out.who)

    module, ctx = fresh_mod(BYPASS_CHARACTER=False, BYPASS_COLOR_REDUCE=False)
    check("全て切なら作り直しの包みを残さない",
          module.CREATURE + ":pixel_art_process" not in ctx.hooks
          and module.CREATURE + ":reduce_image_colors" not in ctx.hooks,
          sorted(ctx.hooks))
    check("切のときもその旨を記録する",
          any("何も仕掛けない" in line or "顔の検出の記録だけ" in line
              for line in log_lines(module, ctx)), log_lines(module, ctx))

    print("巻き添え")
    module, ctx = fresh_mod(BYPASS_BACKGROUND=True)
    scans = {t: kw.get("alias_scan") for t, kw in ctx.kwargs.items()}
    check("どの対象も alias_scan を切ってある",
          set(scans.values()) == {False}, scans)
    check("どの対象も safe=True",
          all(kw.get("safe") for kw in ctx.kwargs.values()), ctx.kwargs)

    print("数")
    module, ctx = fresh_mod(LOG_LIMIT=2)
    hook = pixel_of(module, ctx)
    orig = Original("pair")
    for _ in range(5):
        hook(orig, FakeImage(512, 1024), 256, 256)
    passed = [line for line in log_lines(module, ctx) if "回目）" in line]
    check("ログは LOG_LIMIT 行で止まる", len(passed) == 2, passed)
    check("止まっても元の工程は呼ばれ続ける", len(orig.calls) == 5, orig.calls)

    module, ctx = fresh_mod(LOG_LIMIT=3)
    pixel, reduce_ = pixel_of(module, ctx), reduce_of(module, ctx)
    for _ in range(4):
        pixel(Original("pair"), FakeImage(512, 1024), 256, 256)
        reduce_(Original("single"), FakeImage(330, 660), 16)
    lines = log_lines(module, ctx)
    check("数は工程ごとに分けて数える",
          len([x for x in lines if x.count("キャラクタ: ")]) == 3
          and len([x for x in lines if x.count("減色: ")]) == 3, lines)

    print("対象名")
    module, ctx = fresh_mod(BYPASS_BACKGROUND=True)
    if os.path.exists(RECON):
        with io.open(RECON, encoding="utf-8") as fh:
            dump = fh.read()
        for name in sorted(ctx.hooks):
            mod_name, _, func = name.partition(":")
            head = "\n{}   (file=".format(mod_name)
            body = ""
            if head in dump:
                # 見出しの行と、その下の罫線を落としてから中身だけ見る。
                tail = dump.split(head, 1)[-1].split("\n", 2)[-1]
                body = tail.split("\n====", 1)[0]
            check("リコンのダンプに在る: " + name,
                  bool(body) and "def {}(".format(func) in body)
    else:
        print("  --   リコンのダンプが無いので照合は飛ばす")

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
