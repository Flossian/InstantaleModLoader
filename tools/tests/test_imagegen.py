# -*- coding: utf-8 -*-
"""ローダの `imagegen`（画像生成の方式を MOD から隠す共有部品）のオフライン検証。

    方式の読み取り  config.json の `sd_backend.name`。無い・壊れたファイルは None
    包む先          知っている方式はその1つ、知らない・読めないなら4つとも。名前は下線の落ち方まで表どおり
    包み方          同じ関数を対象の数だけ登録する。required は方式が分かっているときだけ立つ
    実体            読み込まれている方のモジュールを返す
    描き方の窓口    出す MOD の登録を合わせ、用済みは数えない。選んだ描き方はスレッドごと
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

print("描き方の窓口")
store = getattr(sys, imagegen.METHODS_ATTR, None)
if isinstance(store, dict):
    store["providers"].clear()
check("出す MOD が居なければ txt2img だけ", imagegen.methods("portrait") == ["txt2img"])


class AliveCtx(object):
    gone = False

    def superseded(self):
        return self.gone


live = AliveCtx()
imagegen.declare_methods("a", live, lambda kind: ["chain", "img2img"] if kind == "portrait" else ["chain"])
check("並びは txt2img・img2img・chain の順", imagegen.methods("portrait") == ["txt2img", "img2img", "chain"])
check("種類ごとに出す", imagegen.methods("background") == ["txt2img", "chain"])
imagegen.declare_methods("b", live, lambda kind: ["img2img", "unknown"])
check("複数の MOD の分を合わせ、知らない描き方は出さない",
      imagegen.methods("background") == ["txt2img", "img2img", "chain"])
imagegen.declare_methods("b", live, lambda kind: 1 / 0)
check("投げた MOD の分は出さず、誤りを残す",
      imagegen.methods("background") == ["txt2img", "chain"]
      and any("ZeroDivisionError" in e for e in getattr(sys, imagegen.METHODS_ATTR)["errors"]))
live.gone = True
check("用済みになった登録は数えない", imagegen.methods("portrait") == ["txt2img"])
imagegen.withdraw_methods("a")
imagegen.withdraw_methods("b")
check("下げた登録は残らない", getattr(sys, imagegen.METHODS_ATTR)["providers"] == {})

check("選ばれていなければ None", imagegen.method_now() is None)
with imagegen.drawing_method("img2img") as chosen:
    inner = imagegen.method_now()
    with imagegen.drawing_method("chain"):
        nested = imagegen.method_now()
    after_nested = imagegen.method_now()
check("with の間だけ立ち、入れ子は出ると戻る",
      (chosen, inner, nested, after_nested, imagegen.method_now()) == ("img2img", "img2img", "chain", "img2img", None))
with imagegen.drawing_method("nonsense") as chosen:
    pass
check("知らない描き方は txt2img", chosen == "txt2img")
import threading as _threading  # noqa: E402
seen = {}
with imagegen.drawing_method("img2img"):
    worker = _threading.Thread(target=lambda: seen.update(other=imagegen.method_now()))
    worker.start()
    worker.join()
check("描き方はスレッドごと（別のスレッドには移らない）", seen.get("other") is None, seen)
check("選ぶ口に出す名前", imagegen.METHOD_LABELS["chain"] == "txt2img→img2img")

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
