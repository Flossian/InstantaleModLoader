# -*- coding: utf-8 -*-
"""ローダの `imagegen`（画像生成の方式を MOD から隠す共有部品）のオフライン検証。

    方式の読み取り  config.json の `sd_backend.name`。無い・壊れたファイルは None
    包む先          知っている方式はその1つ、知らない・読めないなら4つとも。名前は下線の落ち方まで表どおり
    包み方          同じ関数を対象の数だけ登録する。required は方式が分かっているときだけ立つ
    実体            読み込まれている方のモジュールを返す
"""
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, os.pardir, os.pardir, "runtime")
sys.path.insert(0, os.path.abspath(RUNTIME))

from instantale_modloader import imagegen  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              "" if cond else " -- {!r}".format(detail)))
    if not cond:
        failures.append(name)


class FakeCtx(object):
    def __init__(self):
        self.wraps = {}

    def wrap(self, target, **options):
        def decorate(fn):
            self.wraps[target] = (fn, options)
            return fn
        return decorate


print("[方式の読み取り]")
tmp = tempfile.mkdtemp()
config = os.path.join(tmp, "config.json")
with open(config, "w", encoding="utf-8") as fh:
    json.dump({"ai_setting": {"local_model_setting": {"sd_backend": {"name": "sdcpp_vulkan"}}}}, fh)
check("config.json の方式を読む", imagegen.backend(config) == "sdcpp_vulkan")
check("無いファイルは None", imagegen.backend(os.path.join(tmp, "missing.json")) is None)
broken = os.path.join(tmp, "broken.json")
with open(broken, "w", encoding="utf-8") as fh:
    fh.write("{")
check("壊れたファイルは None", imagegen.backend(broken) is None)

print("[包む先]")
check("sdcpp_* は下線を落とした1つだけ",
      imagegen.creature_modules("sdcpp_cpu") == ["image_generation.sdcppcpu.image_generation_creature"])
check("diffusers_openvino は下線を残す",
      imagegen.creature_modules("diffusers_openvino")
      == ["image_generation.diffusers_openvino.image_generation_creature"])
check("方式が分からなければ4つとも",
      len(imagegen.creature_modules(None)) == 4 and len(imagegen.creature_modules("new")) == 4)
check("関数の対象はモジュール:関数",
      imagegen.creature_targets("pixel_art_process", "sdcpp_cuda")
      == ["image_generation.sdcppcuda.image_generation_creature:pixel_art_process"])
check("知っている方式か", imagegen.known("sdcpp_cuda") and not imagegen.known(None)
      and not imagegen.known("new"))

print("[包み方]")
ctx = FakeCtx()


def hook(orig, *args, **kwargs):
    return orig(*args, **kwargs)


imagegen.wrap_creature(ctx, "pixel_art_process", "sdcpp_vulkan", safe=True)(hook)
check("知っている方式はその1つだけを包み、required が立つ",
      list(ctx.wraps) == ["image_generation.sdcppvulkan.image_generation_creature:pixel_art_process"]
      and ctx.wraps[list(ctx.wraps)[0]][1] == {"safe": True, "required": True}, ctx.wraps)
ctx = FakeCtx()
imagegen.wrap_creature(ctx, "pixel_art_process", None)(hook)
check("分からなければ4つとも同じ関数で包み、required は立たない",
      len(ctx.wraps) == 4 and all(fn is hook and options == {"required": False}
                                  for fn, options in ctx.wraps.values()), ctx.wraps)
ctx = FakeCtx()
imagegen.wrap_creature(ctx, "detect_face_coordinates", "sdcpp_cpu", required=False)(hook)
check("required を渡せばそれを使う",
      ctx.wraps["image_generation.sdcppcpu.image_generation_creature:detect_face_coordinates"][1]
      == {"required": False})

print("[実体]")
name = "image_generation.sdcppcpu.image_generation_creature"
module = types.ModuleType(name)
sys.modules[name] = module
try:
    check("読み込まれている方を返す（方式が分からなくても）",
          imagegen.creature_module(None) is module and imagegen.creature_module("sdcpp_cpu") is module)
    check("別の方式を選んでいれば返さない", imagegen.creature_module("sdcpp_cuda") is None)
finally:
    sys.modules.pop(name, None)

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
