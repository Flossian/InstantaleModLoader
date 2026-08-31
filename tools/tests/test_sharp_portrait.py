# -*- coding: utf-8 -*-
"""131_sharp_portrait をゲーム抜きで通す。

    python tools/tests/test_sharp_portrait.py

偽の画像と偽の `ctx` を差し込み、次を確認する。

  素通し   … 縮小は元の工程を呼んで形だけ借り、`(絵, 写し)` を返す。減色は元の工程を
             呼ばず、縮小に入って来た絵（2倍にする前）を返す。控えは1度きり
  顔の代わり … 減色の後の縮小だけは元の工程を呼ぶ。旗は1度きり。スレッドを跨がない
  顔       … ゲームが見つけた回は触らない。見つけられなかった回は前処理した絵で
             呼び直し、ゲームの関数が返した値をそのまま返す。**本物の cv2 と
             ゲームのカスケードと、実際に外れた絵で通す**（無ければ飛ばす）
  設置     … `safe=True` / `alias_scan=False`。対象名はリコンのダンプに在る
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

    def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label, cap=cap)

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
    spec = importlib.util.spec_from_file_location(
        "mod_sharp_portrait", MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules["mod_sharp_portrait"] = module
    spec.loader.exec_module(module)
    for name, value in overrides.items():
        setattr(module, name, value)
    ctx = FakeCtx(OUT_DIR)
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

        for want in module.faces.CASCADES:
            label = want.split("cascade")[0]
            path = samples[want]
            print("  --   {}: {}".format(label, os.path.relpath(path, WORLDS)))
            picture = PIL.Image.open(path).convert("RGBA")
            del calls[:]
            check(label + ": 再現した検出は素の絵では外す（前提）", game_like(picture) is None)
            del calls[:]
            got = face(game_like, picture, "lbpcascade_animeface.xml", 0.25, 256)
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
        full = os.path.join(cascade_dir, "lbpcascade_animeface.xml")
        got = face(game_like, picture, full, 0.25, 256)
        check("フォルダ付きで来たら同じフォルダの haar で呼び直す",
              got is not None and calls[-1][1] == os.path.join(cascade_dir, "haarcascade_frontalface_alt.xml"),
              calls[-1][1] if calls else None)

        # こちらは見えたのにゲームの関数が None のときは、その旨を残す。
        def game_blind(image, cascade_path="lbpcascade_animeface.xml", padding=0.25, crop_size=256):
            return None

        module, ctx, pixel, reduce_ = fresh_mod()
        face = ctx.hooks[module.CREATURE + ":detect_face_coordinates"]
        check("ゲームの関数が拒み続ければ None", face(game_blind, picture, "lbpcascade_animeface.xml") is None)
        check("そのとき、こちらが見た箱を記録に残す",
              any("こちらは見えたがゲームの関数は None" in line for line in log_lines(module, ctx)),
              log_lines(module, ctx))
        blank = PIL.Image.new("RGBA", (512, 1024), (40, 40, 40, 255))
        check("何も無い絵は None のまま", face(game_like, blank, "lbpcascade_animeface.xml") is None)

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

    print("設置")
    module, ctx, pixel, reduce_ = fresh_mod()
    check("全て safe=True", all(kw.get("safe") for kw in ctx.kwargs.values()), ctx.kwargs)
    check("全て alias_scan=False",
          all(kw.get("alias_scan") is False for kw in ctx.kwargs.values()), ctx.kwargs)
    check("背景には当たらない", not any("background" in t for t in ctx.hooks), sorted(ctx.hooks))
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
            check("リコンのダンプに在る: " + name, bool(body) and "def {}(".format(func) in body)
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
