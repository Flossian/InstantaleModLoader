# -*- coding: utf-8 -*-
"""画像生成の方式（sdcpp の cuda / vulkan / cpu と diffusers_openvino）を MOD から隠す共有部品。

人物の絵を描くモジュール（`image_generation_creature`）は方式ごとに別のパッケージに居る（GAME.md §2.33）。
4つは排他で、ゲームは `config.json` の `ai_setting.local_model_setting.sd_backend.name` で選ばれた
1つだけを import する。名前は組み立てられない（`image_generation.` の下では `sdcpp_*` の下線が落ち、
`diffusers_openvino` は残る）。

この表と読み取りが `131_` / `138_` / `335_` / `408_` に1字違わず写されていた。
MOD は関数の名前だけを渡し、どの方式かは意識しない:

    @imagegen.wrap_creature(ctx, "generate_character_image", safe=True)
    def generated(orig, *args, **kwargs): ...

    imagegen.creature_targets("pixel_art_process")   # 包む対象の名前（`モジュール:関数`）
    imagegen.creature_module()                       # 読み込まれている方の実体（無ければ None）

方式が読めない・知らない方式なら4つとも包む（入っていない方式は保留のまま待つだけ）。
方式を切り替えるとゲームは再起動するので、注入のときに1回読めば足りる。
"""
import io
import json
import os
import sys

from . import saves

#: `sd_backend.name` → `image_generation.` の下のパッケージ名。
FAMILIES = {
    "sdcpp_cuda": "sdcppcuda",
    "sdcpp_vulkan": "sdcppvulkan",
    "sdcpp_cpu": "sdcppcpu",
    "diffusers_openvino": "diffusers_openvino",
}

#: 人物・敵・モンスターの絵を描くモジュール。
CREATURE_FORMAT = "image_generation.{}.image_generation_creature"

#: `backend` の既定の引数（「config.json から読む」）。None は「読めなかった」の意味で使う。
_READ = object()


def backend(path=None):
    """ゲームが選んでいる画像生成の方式（`sd_backend.name`）。読めなければ None。"""
    path = path or os.path.join(saves.data_dir(), "config.json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data["ai_setting"]["local_model_setting"]["sd_backend"]["name"]
    except Exception:
        return None


def _resolve(name):
    return backend() if name is _READ else name


def known(name=_READ):
    """その方式を知っているか（知っていれば包むのはその1つ）。"""
    return _resolve(name) in FAMILIES


def families(name=_READ):
    """包むパッケージ名。方式が分からなければ4つとも。"""
    family = FAMILIES.get(_resolve(name))
    return [family] if family else list(FAMILIES.values())


def creature_modules(name=_READ):
    """人物の絵を描くモジュールの名前。方式が分からなければ4つとも。"""
    return [CREATURE_FORMAT.format(family) for family in families(name)]


def creature_targets(func, name=_READ):
    """`func` を包む対象（`モジュール:関数`）。方式が分からなければ4つとも。"""
    return [module + ":" + func for module in creature_modules(name)]


def creature_module(name=_READ):
    """読み込まれている方の `image_generation_creature`。どれも読み込まれていなければ None。"""
    for module_name in creature_modules(name):
        module = sys.modules.get(module_name)
        if module is not None:
            return module
    return None


def wrap_creature(ctx, func, name=_READ, **options):
    """`func` を、選ばれた方式（分からなければ4つ）の同じ関数として包むデコレータ。

    `required` を渡さなければ、方式が分かっているときだけ要る（その一族に関数が無いことを知らせる）。
    分からないときは入っていない一族を待つだけなので要らない。
    同じ関数を対象の数だけ登録する（`ctx.wrap` は関数に印を1つ立てるだけなので重ねて登録できる）。
    """
    chosen = _resolve(name)
    options.setdefault("required", chosen in FAMILIES)
    targets = creature_targets(func, chosen)

    def decorate(fn):
        for target in targets:
            ctx.wrap(target, **options)(fn)
        return fn
    return decorate
