# -*- coding: utf-8 -*-
"""138_fix_character_image_refresh をゲーム抜きで通す。

    python tools/tests/test_fix_character_image_refresh.py

偽の HUD（パーティー欄の顔を木の数段下に置く）と画像生成関数で、次を確認する。

  対象       … config.json で選ばれた方式の2関数だけを包む。分からなければ4つとも
  読み直し   … 描き終えたら、その人物のフォルダを指す立ち絵と顔だけを読み直す
  他人       … 他の人物の画像・名前の前方一致には触らない
  失敗       … 描くのが落ちたら読み直さない。読み直しの失敗は次の画像へ進む
  版差       … reload の無い画像は source を付け直す
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


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


spec = importlib.util.spec_from_file_location(
    "character_image_refresh_under_test", find_mod("_fix_character_image_refresh"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        print("  FAIL  {}  {!r}".format(label, detail))
        failures.append(label)


reloaded = []


class Picture(object):
    def __init__(self, source):
        self.source = source
        self.children = []

    def reload(self):
        reloaded.append(self.source)


class OldPicture(object):
    """reload を持たない版の画像。source の付け直しを記録する。"""

    def __init__(self, source):
        self._source = source
        self.children = []
        self.history = []

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value):
        self.history.append(value)
        self._source = value


class Broken(Picture):
    def reload(self):
        raise RuntimeError("cannot reload")


class Box(object):
    def __init__(self, *children):
        self.children = list(children)


ROOT = "C:\\worlds\\W\\characters\\"
body = ROOT + "甲\\reduced_color_image.png"
face = ROOT + "甲\\face_image.png"
other = ROOT + "乙\\face_image.png"
prefix = ROOT + "甲乙\\face_image.png"

print("=== 見分け ===")
check("その人物のフォルダ", mod.shows_character(face, "甲"))
check("区切りは / でも", mod.shows_character("C:/w/characters/甲/face_image.png", "甲"))
check("前方一致は別人", not mod.shows_character(prefix, "甲"))
check("characters の下でなければ別物",
      not mod.shows_character("C:\\w\\items\\甲\\x.png", "甲"))
check("source が無ければ別物", not mod.shows_character(None, "甲"))

print("=== 読み直し ===")
old = OldPicture(ROOT + "甲\\generated_image.png")
broken = Broken(face)
party = Box(Box(Box(Picture(face))), Box(Box(Picture(other))), Box(Box(Picture(prefix))))
hud = types.SimpleNamespace(character_image_right=Picture(body),
                            character_image=Picture(other),
                            children=[Box(Box(party)), Box(old), Box(broken)])
app = types.SimpleNamespace()
logs = []
mod.ui.find_hud = lambda app: hud
count = mod.refresh(app, "甲", logs.append)
check("立ち絵と顔を読み直す", sorted(reloaded) == sorted([body, face]), reloaded)
check("reload の無い画像は source を付け直す",
      old.history == ["", ROOT + "甲\\generated_image.png"], old.history)
check("読み直しの失敗は数えず次へ進む", count == 3, count)
check("数をログに残す", any("reloaded 3 image(s) of 甲" in line for line in logs), logs)
check("失敗もログに残す", any("could not reload Broken" in line for line in logs), logs)
del reloaded[:]
mod.ui.find_hud = lambda app: None
check("HUD が無ければ何もしない", mod.refresh(app, "甲", logs.append) == 0)


print("=== フック ===")


class Ctx(object):
    def __init__(self):
        self.wraps = {}

    def logger(self, *args, **kwargs):
        return logs.append

    def log(self, message):
        logs.append(message)

    def log_exc(self, message):
        logs.append("EXC " + message)

    def wrap(self, target, **kwargs):
        def decorator(fn):
            self.wraps[target] = fn
            return fn
        return decorator


tmp = tempfile.mkdtemp()
config = os.path.join(tmp, "config.json")
with io.open(config, "w", encoding="utf-8") as fh:
    json.dump({"ai_setting": {"local_model_setting": {
        "sd_backend": {"name": "sdcpp_vulkan"}}}}, fh)
check("config.json の方式を読む", mod.config_backend(config) == "sdcpp_vulkan")
check("config.json が無ければ None",
      mod.config_backend(os.path.join(tmp, "missing.json")) is None)
shutil.rmtree(tmp)
check("sdcpp_* は下線を落とした1つだけ", mod.targets_for("sdcpp_cpu") == (
    "image_generation.sdcppcpu.image_generation_creature:generate_character_image",
    "image_generation.sdcppcpu.image_generation_creature:generate_character_image_from_enemy"),
    mod.targets_for("sdcpp_cpu"))
check("diffusers_openvino は下線が残る",
      all(".diffusers_openvino." in t for t in mod.targets_for("diffusers_openvino"))
      and len(mod.targets_for("diffusers_openvino")) == 2)
check("方式が分からなければ4つとも", len(mod.targets_for(None)) == 8
      and len(mod.targets_for("new_backend")) == 8)

ctx = Ctx()
mod.ui.find_app = lambda: app
mod.ui.find_hud = lambda app: hud
mod.config_backend = lambda path=None: "sdcpp_cuda"
mod.apply(ctx)
check("選ばれた方式の2関数だけを包む", sorted(ctx.wraps) == sorted(
    "image_generation.sdcppcuda.image_generation_creature:" + func for func in mod.FUNCS),
    sorted(ctx.wraps))
hook = ctx.wraps["image_generation.sdcppcuda.image_generation_creature:generate_character_image"]
result = hook(lambda *a, **k: ("big", "small"), "W", "甲", "young man", ["x"])
check("戻り値はそのまま", result == ("big", "small"))
check("描き終えたら読み直す", sorted(reloaded) == sorted([body, face]), reloaded)
del reloaded[:]
enemy = ctx.wraps[
    "image_generation.sdcppcuda.image_generation_creature:"
    "generate_character_image_from_enemy"]
enemy(lambda *a, **k: None, "W", name="乙")
check("キーワードの名前でも読み直す", reloaded == [other, other], reloaded)
del reloaded[:]


def fails(*args, **kwargs):
    raise RuntimeError("generation failed")


try:
    hook(fails, "W", "甲", "young man", ["x"])
    raised = False
except RuntimeError:
    raised = True
check("描くのが落ちたら例外はそのまま、読み直さない", raised and reloaded == [], reloaded)

print("\n{} failure(s)".format(len(failures)))
sys.exit(1 if failures else 0)
