# -*- coding: utf-8 -*-
"""343_stable_diffusion の顔の描き直しの部品（faces.py）をゲーム抜きで通す。

    python tools/tests/test_sd_faces.py

  設定   … 既定は切。入なら同梱の検出器で動き、壊れた値は既定へ
  日本語 … パスに日本語があっても検出器を作れる（cv2 が無ければ飛ばす）
  枠     … 顔の枠を広げた正方形は絵の中に収まる
  順     … YuNet を先に、見つからなければ lbpcascade。倍率は見つけた方のもの
  文     … 全身を描かせるタグを外し、その位置に顔の寄りのタグを入れる
  貼る   … 楕円の内側だけ差し替わり、外側と元の絵は変わらない
顔を探す処理と描き直し（出口）はゲームの中でしか通らないので、ここでは見ない。
"""
import importlib.util
import io
import json
import os
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FACES_PATH = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime", "mods",
                                           "343_stable_diffusion", "faces.py"))
_spec = importlib.util.spec_from_file_location("faces_under_test", FACES_PATH)
faces = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(faces)

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  {}".format(detail)))
    if not cond:
        failures.append(name)


# ------------------------------------------------------------------ 設定
MOD_DIR = os.path.dirname(FACES_PATH)
MODELS = faces.models_dir(MOD_DIR)
check("検出器は2つとも同梱している",
      os.path.isfile(faces.yunet_path(MODELS)) and os.path.isfile(faces.cascade_path(MODELS))
      and os.path.isfile(os.path.join(MODELS, "LICENSE.yunet.txt")))
decls = json.load(io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8"))["settings"]
check("mod.json に設定が3つあり、既定は切・空・0.4",
      all(name in decls for name in faces.NAMES)
      and (decls[faces.SWITCH]["default"], decls[faces.PRESET]["default"],
           decls[faces.STRENGTH]["default"]) == (False, "", faces.DEFAULT_STRENGTH))
check("既定の値では動かない", faces.config(faces.derive({}), MODELS) is None)
on = faces.derive({faces.SWITCH: True, faces.PRESET: " WAI ", faces.STRENGTH: 0.35})
check("設定の値を読む（プリセットの名前の前後の空白は外す）",
      on == {"on": True, "preset": "WAI", "strength": 0.35}, on)
check("強さの範囲の外と読めない値は既定、1 を超える値は 1",
      [faces.derive({faces.STRENGTH: v})["strength"] for v in (0, -1, "x", None, float("nan"), 3)]
      == [0.4, 0.4, 0.4, 0.4, 0.4, 1.0])
got = faces.config(on, MODELS)
check("入なら同梱の検出器で動く",
      got is not None and got["yunet"] == faces.yunet_path(MODELS)
      and got["cascade"] == faces.cascade_path(MODELS) and got["strength"] == 0.35, got)
check("置き場が分からなければ動かない", faces.config(on, "") is None)
state = tempfile.mkdtemp()
try:
    check("検出器が1つも無ければ動かない", faces.config(on, state) is None)
    io.open(faces.cascade_path(state), "w", encoding="utf-8").write(u"<opencv_storage/>")
    got = faces.config(on, state)
    check("lbpcascade だけでも動く", got is not None and got["yunet"] is None, got)
finally:
    shutil.rmtree(state, ignore_errors=True)

# ------------------------------------------------------------------ 日本語のパス
if all(importlib.util.find_spec(name) for name in ("cv2", "numpy")):
    state = tempfile.mkdtemp()
    try:
        folder = os.path.join(state, u"日本語の置き場")
        shutil.copytree(MODELS, folder)
        faces._CONTENTS.clear()
        try:
            cascade = faces.make_cascade(faces.cascade_path(folder))
            yunet = faces.make_yunet(faces.yunet_path(folder), (320, 320))
            ok, detail = (not cascade.empty() and yunet is not None), ""
        except Exception as exc:
            ok, detail = False, "{}: {}".format(type(exc).__name__, exc)
        check("パスに日本語があっても検出器を作れる", ok, detail)
    finally:
        faces._CONTENTS.clear()
        shutil.rmtree(state, ignore_errors=True)
else:
    print("skip 日本語のパス（cv2 が無い）")

# ------------------------------------------------------------------ 探す順
calls = []
real = (faces.find_yunet, faces.find_cascade)
faces.find_yunet = lambda image, model: calls.append("yunet") or answers["yunet"]
faces.find_cascade = lambda image, path: calls.append("cascade") or answers["cascade"]
both = {"yunet": "y.onnx", "cascade": "c.xml"}
try:
    answers = {"yunet": (1, 2, 3, 4), "cascade": (5, 6, 7, 8)}
    check("両方あれば YuNet が先で、YuNet の倍率を返す",
          faces.find(None, both) == ((1, 2, 3, 4), 2.7) and calls == ["yunet"], calls)
    calls[:] = []
    answers = {"yunet": None, "cascade": (5, 6, 7, 8)}
    check("YuNet で見つからなければ lbpcascade と、その倍率",
          faces.find(None, both) == ((5, 6, 7, 8), 2.2) and calls == ["yunet", "cascade"], calls)
    calls[:] = []
    check("置いていない検出器は呼ばない",
          faces.find(None, dict(both, yunet=None)) == ((5, 6, 7, 8), 2.2) and calls == ["cascade"], calls)
    answers = {"yunet": None, "cascade": None}
    check("どちらも見つからなければ None", faces.find(None, both) is None)
finally:
    faces.find_yunet, faces.find_cascade = real

# ------------------------------------------------------------------ 枠と寸法
check("顔の枠を grow 倍の正方形へ広げる（中心は顔の中心）",
      faces.square((300, 400, 100, 100), 2.0, 704, 1408) == (250, 350, 200))
check("上の端からはみ出す枠は絵の中へずらす", faces.square((276, 10, 170, 170), 2.2, 704, 1408)[1] == 0)
check("絵より大きい枠は絵の短い辺に収める", faces.square((0, 0, 600, 600), 2.2, 704, 1408) == (0, 0, 704))
check("拡大の一辺は SDXL 1024・ほか 768", (faces.side_for("sdxl"), faces.side_for("sd15")) == (1024, 768))

# ------------------------------------------------------------------ プロンプト
got = faces.face_prompt("masterpiece, full body, 1girl, standing, grey eyes, straight-on, black background")
check("全身を描かせるタグを外し、最初に外した位置へ顔の寄りのタグを入れる",
      got == "masterpiece, portrait, face focus, close-up, 1girl, grey eyes, black background", got)
got = faces.face_prompt("full-body, masterpiece, <lora:x:1>")
check("full-body も外し、LoRA の指定は残す",
      got == "portrait, face focus, close-up, masterpiece, <lora:x:1>", got)
got = faces.face_prompt("1boy, short hair")
check("外すタグが無ければ先頭に入れる", got == "portrait, face focus, close-up, 1boy, short hair", got)
check("文字列でなければそのまま", faces.face_prompt(None) is None)

# ------------------------------------------------------------------ 貼る
try:
    from PIL import Image
except ImportError:
    Image = None
if Image is not None:
    base = Image.new("RGB", (200, 400), (10, 20, 30))
    face = Image.new("RGB", (512, 512), (250, 0, 0))
    out = faces.paste(base, face, 50, 20, 100, 0.18)
    check("楕円の中心は描き直した顔に替わる", out.getpixel((100, 70)) == (250, 0, 0), out.getpixel((100, 70)))
    check("切り出しの角と外側は元の絵のまま",
          out.getpixel((51, 21)) == (10, 20, 30) and out.getpixel((10, 300)) == (10, 20, 30))
    check("元の絵は書き換えない", base.getpixel((100, 70)) == (10, 20, 30))
    check("戻り値の1枚目を差し替える（一覧・組・1枚）",
          faces.replace_first([base, 1], out)[0] is out and faces.replace_first((base,), out) == (out,)
          and faces.replace_first(base, out) is out)
else:
    print("skip 貼る（PIL が無い）")

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
