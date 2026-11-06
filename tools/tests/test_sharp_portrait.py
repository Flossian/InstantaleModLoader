# -*- coding: utf-8 -*-
"""131_sharp_portrait をゲーム抜きで通す。

    python tools/tests/test_sharp_portrait.py

偽の画像と偽の `ctx` を差し込み、次を確認する。

  素通し   … 縮小は元の工程を呼んで形だけ借り、`(絵, 写し)` を返す。減色は元の工程を
             呼ばず、縮小に入って来た絵（2倍にする前）を返す。控えは1度きり
  顔の代わり … 減色の後の縮小だけは元の工程を呼ぶ。旗は1度きり。スレッドを跨がない
  顔       … ゲームが見つけた回は触らない。見つけられなかった回は前処理した絵で
             呼び直し、ゲームの関数が返した値をそのまま返す。**本物の cv2 と
             ゲームのカスケードと、実際に外れた絵で通す**（無ければ飛ばす）。
             呼び直すのは包みを剥がした素の関数で、`orig` は本番の1回だけ。
             素の関数が投げても None で返す。anime の回の None では呼び直さない
  敵       … 既定はゲームのまま。入れると縮小と減色を通さず、縁は設定で選び、雑音は下限へ。
             2倍の絵は返った後に戻す。戦闘画面の絵の縮小だけ線形にする
  背景     … 既定はゲームのまま。入れると縮小と減色を通さず、雑音は下限へ
  背景抜き … 既定はゲームのまま。設定に従ってモデルを選び、敵は2つのモデルを合わせる。
             抜けなければゲームへ戻す。343 の欄に残った設定は1度だけ移す
  設置     … `safe=True` / `alias_scan=False`。対象名はリコンのダンプに在る
"""
import importlib.util
import io
import json
import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.join(ROOT, "out", "test")
RECON = os.path.join(ROOT, "out", "recon", "game_modules.txt")
GAME_DIR = r"C:\Program Files\Epic Games\Instantaleq6Ve7"
#: 手元の世界の立ち絵。ゲームの検出が外した絵を、検査のたびにここから探す
#: （名指しすると、その NPC が作り直されて見つかる側に変わった時点で前提が崩れる）。
WORLDS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Darmabeko", "Instantale", "worlds")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


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


MOD_DIR, MOD = find_mod("_sharp_portrait")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class FakeImage(object):
    def __init__(self, width, height, who="素"):
        self.size = (width, height)
        self.who = who

    def copy(self):
        return FakeImage(self.size[0], self.size[1], self.who)


class Original(object):
    """包まれた側。呼ばれた回数を数える。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, image, *args, **kwargs):
        self.calls += 1
        return image, FakeImage(16, 32, "ゲーム")


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.game_dir = GAME_DIR
        self.mod_dir = MOD_DIR
        self.hooks = {}
        self.kwargs = {}
        self.logs = []
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`cap` も本物）。
    _mod = None

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

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


def game_module(module, detect):
    """ゲームの検出モジュールを置く。検出の関数はローダが包んだ後の形
    （外側は包み、`__original__` を辿ると素の `detect`）。"""
    def wrapper(*args, **kwargs):
        raise AssertionError("呼び直しが包みの側を呼んだ")
    wrapper.__original__ = detect
    creature = types.ModuleType(module.CREATURE)
    creature.detect_face_coordinates = wrapper
    sys.modules[module.CREATURE] = creature


def fresh_mod(**overrides):
    # 既定は「立ち絵を荒くしない」が切（版15）。検査は入れた側で通し、切は明示して確かめる。
    overrides.setdefault("SHARP_PORTRAIT", True)
    # YuNet（版19）は「YuNet」の節で入れて確かめる。ほかの節はゲームの検出とやり直しだけを見る。
    overrides.setdefault("FACE_YUNET", False)
    game_dir = overrides.pop("GAME_DIR_OVERRIDE", None)
    runtime_dir = overrides.pop("RUNTIME_DIR_OVERRIDE", None)
    spec = importlib.util.spec_from_file_location(
        "mod_sharp_portrait", MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules["mod_sharp_portrait"] = module
    spec.loader.exec_module(module)
    for name, value in overrides.items():
        setattr(module, name, value)
    ctx = FakeCtx(OUT_DIR)
    if game_dir:
        ctx.game_dir = game_dir
    if runtime_dir:
        ctx.runtime_dir = runtime_dir
    log_path = ctx.out_path(module.LOG_BASENAME)
    if os.path.exists(log_path):
        os.remove(log_path)
    module.apply(ctx)
    pixel = ctx.hooks[module.CREATURE + ":pixel_art_process"]
    reduce_ = ctx.hooks[module.CREATURE + ":reduce_image_colors"]
    return module, ctx, pixel, reduce_


def log_lines(module, ctx):
    path = os.path.join(ctx.out_dir, module.LOG_BASENAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [line.rstrip("\n") for line in fh if line.strip()]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("素通し")
    module, ctx, pixel, reduce_ = fresh_mod()
    orig = Original()
    source = FakeImage(512, 1024, "元絵")
    big, small = pixel(orig, source, 165, 330)
    check("縮小は (絵, 写し) を返す",
          big is source and small is not source and small.size == (512, 1024)
          and small.who == "元絵", (big, small))
    check("縮小は元の工程を呼んで形だけ借りる", orig.calls == 1, orig.calls)

    doubled = FakeImage(1024, 2048, "ゲームが2倍にした絵")
    reduced = reduce_(orig, doubled, 16)
    check("減色は縮小に入って来た絵（2倍にする前）を返す",
          reduced.who == "元絵" and reduced.size == (512, 1024) and reduced is not source,
          (reduced.who, reduced.size))
    check("減色の元の工程は呼ばない", orig.calls == 1, orig.calls)
    again = reduce_(orig, doubled, 16)
    check("控えは1度きり（無ければ入って来た絵をそのまま返す）", again is doubled)

    print("顔の代わり")
    fallback = pixel(orig, doubled, 16, 32)
    check("減色の後の縮小は元の工程の結果をそのまま返す",
          orig.calls == 2 and fallback[1].who == "ゲーム", (orig.calls, fallback))
    again = pixel(orig, FakeImage(512, 1024, "次の絵"), 165, 330)
    check("旗は1度きり（次の縮小は素通しに戻る）",
          orig.calls == 3 and again[1].who == "次の絵", (orig.calls, again))

    reduce_(orig, doubled, 16)                   # このスレッドに旗を立てる
    seen = {}

    def other_thread():
        seen["out"] = pixel(orig, FakeImage(512, 1024, "別スレッドの絵"), 165, 330)

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join()
    check("旗はスレッドを跨がない", orig.calls == 4 and seen["out"][1].who == "別スレッドの絵",
          (orig.calls, seen["out"]))

    print("顔")
    module, ctx, pixel, reduce_ = fresh_mod()
    face = ctx.hooks.get(module.CREATURE + ":detect_face_coordinates")
    check("顔の検出に当たる（無くても撥ねない）",
          face is not None and ctx.kwargs[module.CREATURE + ":detect_face_coordinates"].get("required") is False)
    calls = []

    def game_found(image, *args, **kwargs):
        calls.append(image)
        return (100, 40, 120, 120)

    got = face(game_found, FakeImage(512, 1024), "lbpcascade_animeface.xml")
    check("ゲームが見つけた回はそのまま返す（呼び直さない）",
          got == (100, 40, 120, 120) and len(calls) == 1, (got, len(calls)))
    sys.modules.pop(module.CREATURE, None)
    got = face(lambda image, *a, **k: None, FakeImage(512, 1024), "haarcascade_frontalface_alt.xml")
    check("素の関数が引けなければやり直さない",
          got is None and any("素の関数が引けない" in line for line in log_lines(module, ctx)),
          (got, log_lines(module, ctx)))
    # anime の回で拾って返すと、ゲームの haar の回が来ない。やり直しは haar の回まで待つ。
    probed = []
    game_module(module, lambda image, *a, **k: probed.append(image) or (1, 2, 3, 4))
    lbp_full = os.path.join("runtime", "models", "face_recognition", "lbpcascade_animeface.xml")
    got = face(lambda image, *a, **k: None, FakeImage(512, 1024), lbp_full, 0.25, 256)
    check("anime の回の None はそのまま返し、呼び直さない（ゲームの haar の回に任せる）",
          got is None and not probed
          and any("ゲームの次の試行に任せる" in line for line in log_lines(module, ctx)),
          (got, len(probed), log_lines(module, ctx)))
    got = face(lambda image, *a, **k: None, FakeImage(512, 1024), cascade_path=lbp_full)
    check("キーワードで来ても anime の回は呼び直さない", got is None and not probed, (got, len(probed)))
    sys.modules.pop(module.CREATURE, None)

    try:
        import cv2
        import numpy as np
        import PIL.Image
    except ImportError:
        cv2 = None
    cascade_dir = os.path.join(GAME_DIR, "runtime", "models", "face_recognition")
    samples = {}
    if cv2 is not None and os.path.isdir(cascade_dir) and os.path.isdir(WORLDS):
        loaded = {name: cv2.CascadeClassifier(os.path.join(cascade_dir, name)) for name in module.faces.CASCADES}

        def missed_by_game(gray):
            return all(module.faces.pick_face(gray, c) is None for c in loaded.values())

        def recovered_with(gray, want):
            """前処理を順に掛け、最初に拾えたカスケードが `want` なら真。"""
            for prep in module.faces.FACE_PREPS:
                done = module.faces.preprocess(gray, prep, cv2, np)
                for name in module.faces.CASCADES:
                    if module.faces.pick_face(done, loaded[name]) is not None:
                        return name == want
            return False

        # 素の絵では両方のカスケードが外し、前処理で拾える絵を、カスケードごとに1枚ずつ探す。
        for world in sorted(os.listdir(WORLDS)):
            chars = os.path.join(WORLDS, world, "characters")
            if not os.path.isdir(chars):
                continue
            for who in sorted(os.listdir(chars)):
                path = os.path.join(chars, who, "generated_image.png")
                if not os.path.isfile(path) or len(samples) == 2:
                    continue
                gray = cv2.cvtColor(np.asarray(PIL.Image.open(path).convert("RGB")), cv2.COLOR_RGB2GRAY)
                if not missed_by_game(gray):
                    continue
                for want in module.faces.CASCADES:
                    if want not in samples and recovered_with(gray, want):
                        samples[want] = path
    if len(samples) < 2:
        print("  --   cv2 かゲームのカスケードか、前処理で拾える外れた絵が無いので、呼び直しの検査は飛ばす")
    else:

        def game_like(image, cascade_path="lbpcascade_animeface.xml", padding=0.25, crop_size=256):
            """ゲームの検出の代わり。渡されたカスケードで、MOD と同じ絞りを掛ける。

            絞り無しの「一番大きい箱」だと、ゲームが外した絵でも靴や鎧の小さな箱を
            拾ってしまう（ゲームの関数は何かしら絞っている）。
            """
            calls.append((image, cascade_path, padding, crop_size))
            gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
            return module.faces.pick_face(gray, loaded[os.path.basename(cascade_path)])

        game_module(module, game_like)
        for want in module.faces.CASCADES:
            label = want.split("cascade")[0]
            path = samples[want]
            print("  --   {}: {}".format(label, os.path.relpath(path, WORLDS)))
            picture = PIL.Image.open(path).convert("RGBA")
            del calls[:]
            check(label + ": 再現した検出は素の絵では外す（前提）", game_like(picture) is None)
            del calls[:]
            got = face(game_like, picture, "haarcascade_frontalface_alt.xml", 0.25, 256)
            check(label + ": 外れた絵でも前処理で拾える", got is not None, got)
            check(label + ": 拾った箱は上寄り",
                  got is not None and (got[1] + got[3] / 2.0) / picture.size[1] < module.faces.FACE_TOP, got)
            check(label + ": 呼び直す絵は元と同じ形（RGBA）で、padding / crop_size はそのまま",
                  len(calls) >= 2 and calls[-1][0].mode == "RGBA" and calls[-1][0].size == picture.size
                  and calls[-1][2:] == (0.25, 256), calls[-1][1:])
            check(label + ": 拾ったカスケードで呼び直している", calls[-1][1] == want, calls[-1][1])
        check("記録に前処理とカスケードの名前が出る",
              any("顔: ゲームは見つけられず、" in line and "+ haar" in line for line in log_lines(module, ctx)),
              log_lines(module, ctx))
        check("ゲームの呼び方を1度だけ記録する",
              sum(1 for line in log_lines(module, ctx) if "顔: ゲームの呼び方" in line) == 1,
              log_lines(module, ctx))

        # ゲームがフォルダ付きで渡して来たら、呼び直しも同じフォルダの haar にする。
        del calls[:]
        picture = PIL.Image.open(samples["haarcascade_frontalface_alt.xml"]).convert("RGBA")
        full = os.path.join(cascade_dir, "haarcascade_frontalface_alt.xml")
        got = face(game_like, picture, full, 0.25, 256)
        check("フォルダ付きで来たら同じフォルダの haar で呼び直す",
              got is not None and calls[-1][1] == os.path.join(cascade_dir, "haarcascade_frontalface_alt.xml"),
              calls[-1][1] if calls else None)

        # こちらは見えたのにゲームの関数が None のときは、その旨を残す。
        def game_blind(image, cascade_path="lbpcascade_animeface.xml", padding=0.25, crop_size=256):
            return None

        module, ctx, pixel, reduce_ = fresh_mod()
        face = ctx.hooks[module.CREATURE + ":detect_face_coordinates"]
        game_module(module, game_blind)
        check("ゲームの関数が拒み続ければ None", face(game_blind, picture, "haarcascade_frontalface_alt.xml") is None)
        check("そのとき、こちらが見た箱を記録に残す",
              any("こちらは見えたがゲームの関数は None" in line for line in log_lines(module, ctx)),
              log_lines(module, ctx))
        game_module(module, game_like)
        blank = PIL.Image.new("RGBA", (512, 1024), (40, 40, 40, 255))
        check("何も無い絵は None のまま", face(game_like, blank, "haarcascade_frontalface_alt.xml") is None)

        # safe=True の包みは最後に呼んだ orig の結果を本番の答えとして覚える。探りで orig を呼ばない。
        production = []

        def game_orig(image, *args, **kwargs):
            production.append(image)
            return None

        got = face(game_orig, picture, "haarcascade_frontalface_alt.xml")
        check("呼び直しは剥がした素の関数で、orig は本番の1回だけ",
              got is not None and len(production) == 1, (got, len(production)))

        def game_throws(image, *args, **kwargs):
            raise ValueError("カスケードが合わない")

        game_module(module, game_throws)
        try:
            got = face(game_orig, picture, "haarcascade_frontalface_alt.xml")
            leaked = None
        except Exception as exc:
            got, leaked = None, exc
        check("素の関数が探りで投げても None を返す（ゲームへ投げない）",
              leaked is None and got is None, repr(leaked))
        check("投げたことを記録に残す",
              any("で投げた ValueError" in line for line in log_lines(module, ctx)),
              log_lines(module, ctx))
    sys.modules.pop(module.CREATURE, None)

    print("顔の切り直し")
    module, ctx, pixel, reduce_ = fresh_mod()
    extract = ctx.hooks.get(module.CREATURE + ":extract_and_save_face")
    check("切り出しに当たる（無くても撥ねない）",
          extract is not None and ctx.kwargs[module.CREATURE + ":extract_and_save_face"].get("required") is False)
    try:
        import PIL.Image
    except ImportError:
        PIL = None
    if PIL is None:
        print("  --   PIL が無いので切り直しの検査は飛ばす")
    else:
        # 顔の位置だけ色を変えた立ち絵。切り抜きがその色なら位置が合っている。
        standing = PIL.Image.new("RGBA", (512, 1024), (10, 10, 10, 255))
        standing.paste((200, 50, 50, 255), (140, 0, 396, 256))
        out_path = os.path.join(OUT_DIR, "face_test.png")

        def game_extract(image, coordinates, output_path, *args, **kwargs):
            PIL.Image.new("RGBA", (165, 165), (0, 0, 0, 255)).save(output_path)   # ゲームの（ずれた）顔
            return "game-result"

        pixel(Original(), standing, 165, 330)          # 検出した絵の幅を控える
        scaled = tuple(int(round(v * 330 / 512.0)) for v in (140, 0, 396, 256))
        got = extract(game_extract, standing, scaled, out_path)
        face_img = PIL.Image.open(out_path)
        check("ゲームが縮めた箱でも 256x256 で切り直す", face_img.size == (256, 256), face_img.size)
        check("切り直した顔は正しい位置", face_img.getpixel((5, 5))[:3] == (200, 50, 50)
              and face_img.getpixel((250, 250))[:3] == (200, 50, 50), face_img.getpixel((5, 5)))
        check("ゲームの戻り値はそのまま", got == "game-result", got)
        extract(game_extract, standing, (140, 0, 396, 256), out_path)
        check("縮めていない箱でも同じ結果", PIL.Image.open(out_path).size == (256, 256))
        extract(game_extract, standing, (400, 900, 656, 1156), out_path)
        check("はみ出す箱は切り直さない（ゲームのまま）", PIL.Image.open(out_path).size == (165, 165))
        check("記録に切り直しが出る",
              any("顔: " in line and "に戻して切り直した" in line for line in log_lines(module, ctx)),
              log_lines(module, ctx))

    print("設定で切る")
    module, ctx, pixel, reduce_ = fresh_mod(SHARP_PORTRAIT=False)
    orig = Original()
    out = pixel(orig, FakeImage(512, 1024, "元絵"), 165, 330)
    check("立ち絵を切ると縮小はゲームのもの", orig.calls == 1 and out[1].who == "ゲーム", (orig.calls, out))
    reduce_out = reduce_(lambda image, *a, **k: FakeImage(330, 660, "ゲームが減色した絵"),
                         FakeImage(330, 660, "2倍にした絵"), 16)
    check("減色もゲームのもの", reduce_out.who == "ゲームが減色した絵", reduce_out.who)
    extract = ctx.hooks[module.CREATURE + ":extract_and_save_face"]
    touched = []
    got = extract(lambda *a, **k: touched.append(a) or "game", FakeImage(330, 660), (94, 21, 259, 186), "x.png")
    check("顔の切り直しもしない（ゲームの縮め方がそのまま合う）", got == "game" and len(touched) == 1)
    keyword = []
    got = extract(lambda pixelated_image, coordinates, output_path: keyword.append(pixelated_image) or "game",
                  pixelated_image=FakeImage(330, 660), coordinates=(94, 21, 259, 186), output_path="x.png")
    check("本体の引数名のキーワードで呼ばれても二重に渡さない", got == "game" and len(keyword) == 1, keyword)
    check("顔の検出のやり直しは立ち絵の設定と独立に残る",
          module.CREATURE + ":detect_face_coordinates" in ctx.hooks and module.FACE_RETRY)

    module, ctx, pixel, reduce_ = fresh_mod(FACE_RETRY=False)
    face = ctx.hooks[module.CREATURE + ":detect_face_coordinates"]
    calls = []
    got = face(lambda image, *a, **k: calls.append(image) or None, FakeImage(512, 1024), "lbpcascade_animeface.xml")
    check("やり直しを切るとゲームの結果（None）をそのまま返し、呼び直さない",
          got is None and len(calls) == 1, (got, len(calls)))
    check("縮小・減色はやり直しの設定と独立に残る",
          pixel(Original(), FakeImage(512, 1024, "元絵"), 165, 330)[1].who == "元絵")

    print("YuNet")
    faces_mod = module.faces

    class FakeDetector(object):
        def __init__(self, found):
            self.found = found
            self.size = None

        def setInputSize(self, size):
            self.size = size

        def detect(self, image):
            return (0 if self.found is None else len(self.found)), self.found

    class FakeArray(object):
        def __init__(self, width, height):
            self.shape = (height, width, 3)

    def row(x, y, w, h, score):
        return [x, y, w, h] + [0] * 10 + [score]

    picture = FakeArray(512, 1024)
    detector = FakeDetector([row(200, 600, 100, 100, 0.99),     # 下寄り（腰や靴）
                             row(250, 100, 20, 20, 0.95),       # 小さすぎる
                             row(200, 120, 100, 100, 0.70),
                             row(210, 110, 96, 96, 0.90)])
    check("上寄りで程よい幅のうち一番確かな箱", faces_mod.yunet_face(picture, detector) == ((210, 110, 96, 96), 0.9),
          faces_mod.yunet_face(picture, detector))
    check("検出器に絵の大きさを渡す", detector.size == (512, 1024), detector.size)
    check("何も無ければ None", faces_mod.yunet_face(picture, FakeDetector(None)) is None)
    check("絞りに残らなければ None", faces_mod.yunet_face(picture, FakeDetector([row(200, 700, 100, 100, 0.9)])) is None)
    check("モデルが無ければ検出器は作らない", faces_mod.make_yunet(None, None, None) is None)
    check("同梱のモデルが MOD のフォルダに在る", os.path.isfile(os.path.join(MOD_DIR, faces_mod.YUNET_MODEL)))
    check("モデルの LICENSE を同梱している", os.path.isfile(os.path.join(MOD_DIR, "models", "LICENSE.yunet.txt")))

    # 包みの流れ。cv2 / numpy はゲームの中でしか import しないので、偽物を置いて確かめる。
    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.COLOR_RGB2BGR = 4
    fake_cv2.cvtColor = lambda array, code: array
    fake_np = types.ModuleType("numpy")
    fake_np.asarray = lambda image: FakeArray(*image.size)

    class RGBImage(FakeImage):
        def convert(self, mode):
            return self

    saved = {name: sys.modules.get(name) for name in ("cv2", "numpy")}
    # `faces` は読み直しても同じモジュールが使い回されるので、差し替えた関数は最後に戻す。
    shared_faces = module.faces
    real_funcs = {name: getattr(shared_faces, name) for name in ("read_yunet", "make_yunet", "yunet_face")}
    sys.modules["cv2"], sys.modules["numpy"] = fake_cv2, fake_np
    try:
        module, ctx, pixel, reduce_ = fresh_mod(FACE_YUNET=True)
        face = ctx.hooks[module.CREATURE + ":detect_face_coordinates"]
        faces_mod = module.faces
        faces_mod.read_yunet = lambda path, np: b"model"
        found = [((210, 110, 96, 96), 0.9)]
        faces_mod.make_yunet = lambda model, cv2, np: "detector"
        faces_mod.yunet_face = lambda bgr, det: found[0]
        calls = []

        def game(image, *args, **kwargs):
            calls.append(args)
            return None

        got = face(game, RGBImage(512, 1024), lbp_full, 0.25, 256)
        check("YuNet が見つけたら、ゲームの検出を呼ばずにゲームと同じ形の箱を返す",
              got == faces_mod.crop_box((210, 110, 96, 96), (512, 1024), 256) and not calls, (got, calls))
        check("記録に YuNet の箱と確かさが出る",
              any("顔: YuNet が見つけた" in line and "0.90" in line for line in log_lines(module, ctx)),
              log_lines(module, ctx))
        got = face(game, RGBImage(512, 1024), "haarcascade_frontalface_alt.xml")
        check("ゲームの2回目（haar）では YuNet を掛けない", got is None and len(calls) == 1, (got, calls))
        del calls[:]
        found[0] = None
        got = face(game, RGBImage(512, 1024), lbp_full, 0.25, 256)
        check("YuNet が外したらゲームの検出に任せる", got is None and len(calls) == 1
              and any("YuNet は見つけられず" in line for line in log_lines(module, ctx)), (got, calls))
        del calls[:]

        def boom(bgr, det):
            raise RuntimeError("bad shape")

        faces_mod.yunet_face = boom
        got = face(lambda image, *a, **k: calls.append(a) or (1, 2, 3, 4), RGBImage(512, 1024), lbp_full)
        check("YuNet で投げてもゲームの検出へ進み、その答えを返す", got == (1, 2, 3, 4) and len(calls) == 1
              and any("YuNet で投げた RuntimeError" in line for line in log_lines(module, ctx)), (got, calls))

        module, ctx, pixel, reduce_ = fresh_mod(FACE_YUNET=True)
        face = ctx.hooks[module.CREATURE + ":detect_face_coordinates"]
        module.faces.read_yunet = lambda path, np: None
        del calls[:]
        face(game, RGBImage(512, 1024), lbp_full)
        face(game, RGBImage(512, 1024), lbp_full)
        check("モデルが読めなければゲームの検出だけで探し、読めない旨は1度だけ書く",
              len(calls) == 2 and sum(1 for line in log_lines(module, ctx) if "モデルが読めない" in line) == 1,
              (calls, log_lines(module, ctx)))

        module, ctx, pixel, reduce_ = fresh_mod(FACE_YUNET=False)
        face = ctx.hooks[module.CREATURE + ":detect_face_coordinates"]
        module.faces.yunet_face = lambda bgr, det: ((210, 110, 96, 96), 0.9)
        del calls[:]
        face(game, RGBImage(512, 1024), lbp_full)
        check("切ればゲームの検出だけ", len(calls) == 1 and not any("YuNet" in line for line in log_lines(module, ctx)))
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        for name, func in real_funcs.items():
            setattr(shared_faces, name, func)
            setattr(module.faces, name, func)

    try:
        import cv2
        import numpy as np
    except ImportError:
        cv2 = None
    if cv2 is None or not hasattr(cv2, "FaceDetectorYN"):
        print("  --   cv2 が無いので、同梱のモデルで検出器を作る検査は飛ばす")
    else:
        model = module.faces.read_yunet(os.path.join(MOD_DIR, module.faces.YUNET_MODEL), np)
        real = module.faces.make_yunet(model, cv2, np)
        check("同梱のモデルをバイト列から読んで検出器を作れる", real is not None)
        blank = np.full((1024, 512, 3), 40, dtype=np.uint8)
        check("何も無い絵では見つけない", real is not None and module.faces.yunet_face(blank, real) is None)

    print("敵")
    module, ctx, pixel, reduce_ = fresh_mod()
    entries = {name: ctx.hooks.get(module.CREATURE + ":" + name) for name in module.ENEMY_ENTRIES}
    check("敵の入口2つに当たる（無くても撥ねない）",
          all(entries.values()) and all(ctx.kwargs[module.CREATURE + ":" + n].get("required") is False
                                         for n in module.ENEMY_ENTRIES), sorted(ctx.hooks))
    check("入口の包みは safe=True にしない（投げたときに2度描かない）",
          all(not ctx.kwargs[module.CREATURE + ":" + n].get("safe") for n in module.ENEMY_ENTRIES))
    inside = {}

    def game_enemy(world, race, name, appearance, size):
        orig = Original()
        out = pixel(orig, FakeImage(512, 512, "敵の絵"), 100, 100)
        inside["pixel"] = (orig.calls, out[1].who)
        red = reduce_(lambda image, *a, **k: FakeImage(200, 200, "ゲームが減色した絵"),
                      FakeImage(200, 200, "敵の2倍"), 16)
        inside["reduce"] = red.who
        return "enemy-image"

    got = entries["generate_enemy_image"](game_enemy, "世界", "orc", "オーク", "big", "large")
    check("敵の入口の中では縮小も減色もゲームのもの",
          got == "enemy-image" and inside["pixel"] == (1, "ゲーム") and inside["reduce"] == "ゲームが減色した絵", inside)
    after = pixel(Original(), FakeImage(512, 1024, "元絵"), 165, 330)
    check("入口を抜ければ NPC は素通しに戻る", after[1].who == "元絵", after[1].who)

    def game_enemy_boom(*a, **k):
        raise RuntimeError("boom")

    try:
        entries["generate_enemy_image_from_character"](game_enemy_boom, "世界", "名前", "path", "large")
    except RuntimeError:
        pass
    check("入口が投げても旗は降りる", pixel(Original(), FakeImage(512, 1024, "元絵"), 165, 330)[1].who == "元絵")

    print("敵の絵を荒くしない")
    module, ctx, pixel, reduce_ = fresh_mod(SHARP_ENEMY=True)
    entries = {name: ctx.hooks[module.CREATURE + ":" + name] for name in module.ENEMY_ENTRIES}
    border = ctx.hooks[module.CREATURE + ":add_border_to_image"]
    tone = ctx.hooks[module.CREATURE + ":apply_dark_fantasy_tone"]
    inside = {}

    def game_enemy_sharp(world, race, name, appearance, size):
        orig = Original()
        drawn = FakeImage(1024, 1024, "敵の絵")
        big, small = pixel(orig, drawn, 128, 128)
        inside["pixel"] = (orig.calls, big is drawn, small is not drawn, small.size)
        inside["border"] = border(lambda image, *a, **k: "ゲームの縁", small)
        toned = tone(lambda image, *a, **k: (image, a, k), small)
        inside["tone_kw"] = toned[2]
        inside["tone_pos"] = tone(lambda image, *a, **k: (a, k), small, 0.3, 1.5, 0.8, 35)
        calls = []
        inside["reduce"] = reduce_(lambda image, *a, **k: calls.append(1), small, 64)
        inside["reduce_calls"] = len(calls)
        inside["small"] = small
        return ("base_image.png", os.path.join(OUT_DIR, module.ENEMY_SHOWN[0]), "正", "負")

    saved = []
    original_save = getattr(FakeImage, "save", None)
    FakeImage.save = lambda self, path: saved.append((self, path))
    got = entries["generate_enemy_image"](game_enemy_sharp, "世界", "orc", "オーク", "big", "large")
    check("縮小は元の工程を形の保険に呼び、(絵, 写し) を返す", inside["pixel"] == (1, True, True, (1024, 1024)), inside["pixel"])
    check("縁は既定で通さない", inside["border"] is inside["small"], inside["border"])
    check("色調は雑音だけ下限へ（名前で）", inside["tone_kw"] == {"randomize": module.TONE_QUIET}, inside["tone_kw"])
    check("色調は雑音だけ下限へ（位置で）",
          inside["tone_pos"] == ((0.3, 1.5, 0.8, module.TONE_QUIET), {}), inside["tone_pos"])
    check("減色は元の工程を呼ばず、入って来た絵を返す",
          inside["reduce"] is inside["small"] and inside["reduce_calls"] == 0, inside["reduce_calls"])
    check("返った後に2倍の絵を2倍の前の絵で上書きする",
          len(saved) == 1 and saved[0][0] is inside["small"]
          and os.path.basename(saved[0][1]) == module.ENEMY_SHOWN[0], saved)
    check("入口の戻り値はそのまま", got[0] == "base_image.png")

    del saved[:]
    entries["generate_enemy_image"](lambda *a: game_enemy_sharp(*a)[:1], "世界", "orc", "オーク", "big", "large")
    check("戻り値に絵が無ければ上書きしない", not saved and any("2倍の絵のまま" in line for line in log_lines(module, ctx)))

    del saved[:]

    def game_from_character(world, name, fullbody, size):
        orig = Original()
        inside["npc"] = pixel(orig, FakeImage(512, 1024, "立ち絵"), pixelation_width=128, pixelation_height=256)
        inside["npc_source"] = inside["npc"][0]
        return os.path.join(OUT_DIR, "opponent_image.png")

    entries["generate_enemy_image_from_character"](game_from_character, "世界", "名前", "path", {"width": 128})
    check("NPC の戦闘用の絵は縮小を素通しし、2倍の絵も戻す（減色を通らない経路）",
          inside["npc"][1].who == "立ち絵" and inside["npc"][1].size == (512, 1024)
          and len(saved) == 1 and saved[0][0] is inside["npc_source"]
          and os.path.basename(saved[0][1]) == "opponent_image.png", saved)
    del saved[:]

    def game_boom_after_reduce(*a, **k):
        reduce_(Original(), FakeImage(256, 256, "途中の絵"), 64)
        raise RuntimeError("boom")

    try:
        entries["generate_enemy_image"](game_boom_after_reduce, "世界", "orc", "オーク", "big", "large")
    except RuntimeError:
        pass
    check("入口が投げたら上書きせず、控えも残さない", not saved and module._LOCAL.enemy_source is None, saved)
    entries["generate_enemy_image"](lambda *a: ("base_image.png", module.ENEMY_SHOWN[0]), "世界", "orc", "オーク", "big", "large")
    check("減色を通らなかった回は上書きしない", not saved, saved)

    def boom_save(self, path):
        raise OSError("disk")

    FakeImage.save = boom_save
    def game_save_fails(*a):
        reduce_(Original(), FakeImage(256, 256, "途中の絵"), 64)
        return ("base_image.png", module.ENEMY_SHOWN[0])

    result = entries["generate_enemy_image"](game_save_fails, "世界", "orc", "オーク", "big", "large")
    check("上書きに失敗しても戻り値を返す", result[0] == "base_image.png"
          and any("戻せず" in line for line in log_lines(module, ctx)), log_lines(module, ctx))
    if original_save is None:
        del FakeImage.save
    else:
        FakeImage.save = original_save

    module.ENEMY_BORDER = True
    entries["generate_enemy_image"](
        lambda *a: inside.update(border=border(lambda image, *x, **k: "ゲームの縁", FakeImage(8, 8))) or (),
        "世界", "orc", "オーク", "big", "large")
    check("ENEMY_BORDER が入なら縁を通す", inside["border"] == "ゲームの縁", inside["border"])
    module.ENEMY_BORDER = False
    outside = FakeImage(8, 8)
    check("入口の外の縁はゲームのまま", border(lambda image: "ゲームの縁", outside) == "ゲームの縁")
    check("入口の外の色調はゲームのまま", tone(lambda image, **k: k, outside) == {})
    module.SHARP_PORTRAIT = False
    after = pixel(Original(), FakeImage(512, 1024, "元絵"), 165, 330)
    check("立ち絵の設定は敵の設定と独立", after[1].who == "ゲーム", after[1].who)

    print("戦闘画面の絵の縮小")
    texture_hook = ctx.hooks[module.ENEMY_IMAGE_TEXTURE]
    check("部品のフックは safe=True", ctx.kwargs[module.ENEMY_IMAGE_TEXTURE].get("safe") is True)

    def widget(source):
        return types.SimpleNamespace(source=source, texture=types.SimpleNamespace(mag_filter="nearest", min_filter="nearest"))

    for name in module.ENEMY_SHOWN:
        part = widget(os.path.join("monsters", "orc", name))
        texture_hook(lambda w, *a: None, part, None, None)
        check("{} は縮小だけ線形".format(name),
              part.texture.min_filter == "linear" and part.texture.mag_filter == "nearest", part.texture)
    part = widget(os.path.join("characters", "x", "reduced_color_image.png"))
    texture_hook(lambda w, *a: None, part, None, None)
    check("敵の絵でなければ触らない", part.texture.min_filter == "nearest")
    part = widget(None)
    part.texture = None
    check("絵が無くても落ちない", texture_hook(lambda w, *a: "素", part, None, None) == "素")
    module.SHARP_ENEMY = False
    part = widget(os.path.join("monsters", "orc", module.ENEMY_SHOWN[0]))
    texture_hook(lambda w, *a: None, part, None, None)
    check("設定が切ならゲームのまま", part.texture.min_filter == "nearest")

    print("背景")
    module, ctx, pixel, reduce_ = fresh_mod()
    check("既定は切", module.SHARP_BACKGROUND is False and module.SHARP_ENEMY is False and module.ENEMY_BORDER is False)
    targets = [t for t in ctx.hooks if t.endswith("image_generation_background:pixel_art_process")]
    check("背景の縮小に当たる", targets and all(ctx.kwargs[t].get("required") is False for t in targets), sorted(ctx.hooks))
    bg_pixel = ctx.hooks[targets[0]]
    bg_tone = ctx.hooks[module.BACKGROUND_TONE]
    bg_reduce = ctx.hooks[module.BACKGROUND_REDUCE]
    picture = FakeImage(1024, 512, "背景")
    check("切なら縮小はゲームのまま", bg_pixel(Original(), picture, 512, 256)[1].who == "ゲーム")
    check("切なら色調はゲームのまま", bg_tone(lambda image, *a, **k: (a, k), picture) == ((), {}))
    check("切なら減色はゲームのまま", bg_reduce(lambda image, *a, **k: "ゲーム", picture, 16) == "ゲーム")
    module.SHARP_BACKGROUND = True
    orig = Original()
    big, small = bg_pixel(orig, picture, 512, 256)
    check("入なら縮小は形の保険に呼んで (絵, 写し) を返す",
          orig.calls == 1 and big is picture and small is not picture and small.who == "背景", (orig.calls, small))
    check("入なら雑音だけ下限へ", bg_tone(lambda image, *a, **k: k, picture) == {"randomize": module.TONE_QUIET})
    check("入なら減色は入って来た絵", bg_reduce(lambda image, *a, **k: "ゲーム", picture, 16) is picture)
    source = FakeImage(512, 1024, "元絵")
    check("背景の設定は立ち絵の工程に効かない",
          pixel(Original(), source, 165, 330)[1].who == "元絵" and module.SHARP_ENEMY is False)
    module.SHARP_PORTRAIT = False
    check("背景の設定は立ち絵の工程をゲームのままにする", pixel(Original(), source, 165, 330)[1].who == "ゲーム")

    print("背景抜き")
    module, ctx, pixel, reduce_ = fresh_mod()
    cut_hook = ctx.hooks[module.CUTOUT_TARGET]
    check("背景抜きのフックは safe=True", ctx.kwargs[module.CUTOUT_TARGET].get("safe") is True)

    def game_cut(image, use_rembg_alpha_matting=True):
        return ("ゲーム", image, use_rembg_alpha_matting)

    check("既定はゲームの切り抜きのまま（引数も素通し）",
          cut_hook(game_cut, "絵", use_rembg_alpha_matting=False) == ("ゲーム", "絵", False))
    entry = ctx.hooks[module.CREATURE + ":generate_enemy_image"]
    check("既定は敵の入口の中でもゲームの切り抜きのまま",
          entry(lambda *a: cut_hook(game_cut, "絵"), "世界", "orc", "オーク", "big", "large") == ("ゲーム", "絵", True))

    tmp_game = os.path.join(OUT_DIR, "fake_game")
    model_dir = os.path.join(tmp_game, module.cutout.MODEL_DIR)
    os.makedirs(model_dir, exist_ok=True)
    for model in (module.cutout.ISNET, module.cutout.U2NET):
        path = os.path.join(model_dir, model[0])
        if os.path.exists(path):
            os.remove(path)
    module, ctx, pixel, reduce_ = fresh_mod(CUTOUT_MODEL="isnet-anime", ENEMY_CUTOUT_MERGE=True, GAME_DIR_OVERRIDE=tmp_game)
    cut_hook = ctx.hooks[module.CUTOUT_TARGET]
    entry = ctx.hooks[module.CREATURE + ":generate_enemy_image"]
    check("モデルが無ければゲームの切り抜きへ戻す",
          cut_hook(game_cut, "絵") == ("ゲーム", "絵", True)
          and any("isnet-anime.onnx が無い" in line for line in log_lines(module, ctx)), log_lines(module, ctx))
    for model in (module.cutout.ISNET, module.cutout.U2NET):
        io.open(os.path.join(model_dir, model[0]), "wb").write(b"x")
    calls = []
    real_cut, real_merged = module.cutout.cut, module.cutout.cut_merged
    module.cutout.cut = lambda image, path: calls.append(("isnet", path)) or FakeImage(8, 8, "isnet")
    module.cutout.cut_merged = lambda image, game: calls.append(("merged", game)) or FakeImage(8, 8, "merged")
    try:
        check("立ち絵は CUTOUT_MODEL に従う", cut_hook(game_cut, "絵").who == "isnet", calls)
        check("キーワードで来た絵も読む", cut_hook(game_cut, image="絵").who == "isnet", calls)
        got = entry(lambda *a: cut_hook(game_cut, "絵"), "世界", "orc", "オーク", "big", "large")
        check("敵の入口の中は2つのモデルを合わせる", got.who == "merged" and calls[-1] == ("merged", tmp_game), calls)
        check("入口を抜ければ立ち絵の扱いに戻る", cut_hook(game_cut, "絵").who == "isnet", calls)
        module.CUTOUT_MODEL = "game"
        check("モデルが game でも、敵の合わせは効く",
              entry(lambda *a: cut_hook(game_cut, "絵"), "世界", "orc", "オーク", "big", "large").who == "merged")
        check("モデルが game なら、立ち絵はゲームの切り抜き", cut_hook(game_cut, "絵")[0] == "ゲーム")
        module.ENEMY_CUTOUT_MERGE = False
        check("合わせが切なら、敵も CUTOUT_MODEL に従う",
              entry(lambda *a: cut_hook(game_cut, "絵"), "世界", "orc", "オーク", "big", "large")[0] == "ゲーム")
        module.ENEMY_CUTOUT_MERGE = True

        def boom(image, game):
            raise RuntimeError("推論で落ちた")

        module.cutout.cut_merged = boom
        got = entry(lambda *a: cut_hook(game_cut, "絵"), "世界", "orc", "オーク", "big", "large")
        check("推論で落ちたらゲームの切り抜きへ戻す",
              got[0] == "ゲーム" and any("抜けなかった" in line for line in log_lines(module, ctx)), got)
    finally:
        module.cutout.cut, module.cutout.cut_merged = real_cut, real_merged
    check("モデルの場所はゲームのフォルダの runtime/models/u2net",
          module.cutout.model_path("G", module.cutout.U2NET).replace("\\", "/") == "G/runtime/models/u2net/u2net.onnx")
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        print("  --   numpy か PIL が無いので、マスクを合わせる検査は飛ばす")
    else:
        first = Image.fromarray(np.array([[0, 0, 200, 92, 24, 160]], dtype=np.uint8), "L")
        second = Image.fromarray(np.array([[0, 200, 0, 0, 10, 30]], dtype=np.uint8), "L")
        merged = np.asarray(module.cutout.merged_mask(first, second))[0].tolist()
        check("濃いほうを取って薄い所を濃くする", merged == [0, 255, 255, 127, 0, 255], merged)

    print("背景抜きの設定の移し替え")
    runtime = os.path.join(OUT_DIR, "fake_root", "runtime")
    os.makedirs(runtime, exist_ok=True)
    store_file = module.loader_config.store_path(runtime)
    os.makedirs(os.path.dirname(store_file), exist_ok=True)

    def run_take_over(store):
        with io.open(store_file, "w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False)
        probe, ctx_probe, _, _ = fresh_mod(RUNTIME_DIR_OVERRIDE=runtime)
        with io.open(store_file, encoding="utf-8") as fh:
            return probe, json.load(fh)

    own = os.path.basename(MOD_DIR)
    probe, saved_store = run_take_over({module.CUTOUT_OLD_OWNER: {"CUTOUT_MODEL": "isnet-anime", "IMG2IMG": True}, "他": {"X": 1}})
    check("343 の欄から移し、他の項目は残す",
          saved_store == {module.CUTOUT_OLD_OWNER: {"IMG2IMG": True}, "他": {"X": 1}, own: {"CUTOUT_MODEL": "isnet-anime"}}, saved_store)
    check("移した起動から効く", probe.CUTOUT_MODEL == "isnet-anime", probe.CUTOUT_MODEL)
    probe, saved_store = run_take_over({module.CUTOUT_OLD_OWNER: {"CUTOUT_MODEL": "game"}})
    check("既定の値なら移さず、空の欄も消す", saved_store == {} and probe.CUTOUT_MODEL == "game", saved_store)
    probe, saved_store = run_take_over({module.CUTOUT_OLD_OWNER: {"CUTOUT_MODEL": "isnet-anime"}, own: {"CUTOUT_MODEL": "game"}})
    check("この MOD に値があれば上書きしない",
          saved_store == {own: {"CUTOUT_MODEL": "game"}} and probe.CUTOUT_MODEL == "game", saved_store)
    probe, saved_store = run_take_over({"他": {"X": 1}})
    check("移すものが無ければ何も変えない", saved_store == {"他": {"X": 1}}, saved_store)
    with io.open(store_file, "w", encoding="utf-8") as fh:
        fh.write("{壊れた")
    probe, ctx_probe, _, _ = fresh_mod(RUNTIME_DIR_OVERRIDE=runtime)
    with io.open(store_file, encoding="utf-8") as fh:
        check("壊れた設定ファイルは書き換えない", fh.read() == "{壊れた")
    check("壊れていても MOD は動く", any("移せず" in line for line in log_lines(probe, ctx_probe)), log_lines(probe, ctx_probe))

    print("設置")
    module, ctx, pixel, reduce_ = fresh_mod()
    hooks_of_steps ={t: kw for t, kw in ctx.kwargs.items() if t.rsplit(":", 1)[1] not in module.ENEMY_ENTRIES}
    check("工程の包みは全て safe=True", all(kw.get("safe") for kw in hooks_of_steps.values()), hooks_of_steps)
    check("工程の包みは全て alias_scan=False（ゲームの写しごと差し替える背景抜きを除く）",
          all(kw.get("alias_scan") is False for t, kw in hooks_of_steps.items() if t != module.CUTOUT_TARGET), hooks_of_steps)
    if os.path.exists(RECON):
        with io.open(RECON, encoding="utf-8") as fh:
            dump = fh.read()
        for name in sorted(ctx.hooks):
            mod_name, _, func = name.partition(":")
            head = "\n{}   (file=".format(mod_name)
            body = ""
            if head in dump:
                tail = dump.split(head, 1)[-1].split("\n", 2)[-1]
                body = tail.split("\n====", 1)[0]
            check("リコンのダンプに在る: " + name, bool(body) and "def {}(".format(func.rsplit(".", 1)[-1]) in body)
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
