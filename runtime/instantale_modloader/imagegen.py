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

後半は、描き直すときの描き方（txt2img / img2img / txt2img→img2img）を MOD どうしで分け合う窓口（TECH.md §3.3.24）。
"""
import io
import json
import os
import sys
import threading

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


# ---------------------------------------------------------------- 描き方の窓口
#
# 立ち絵・敵・背景を描き直すときに、描き方（ゲームのままの txt2img・今の絵を下絵にする img2img・
# 2つのモデルで続けて描く chain）を選べる。描き方を出すのは `343_` で、選ぶ口を持つのは
# `343_`（ゲームの設定の画面の「画像の再生成」）と `335_`（主人公の描き直し）。
# MOD どうしは import しない（TECH.md §3.2.3）ので、ここで仲介する。
#
#     出す側（apply() の中）   imagegen.declare_methods(owner, ctx, fn)
#                              fn(種類) -> 使える描き方の並び（"img2img" / "chain"）
#     選ぶ側                   imagegen.methods("portrait")      # 先頭は必ず "txt2img"
#                              with imagegen.drawing_method("img2img"):
#                                  ...描く関数を呼ぶ（同じスレッドの中で）
#     描く側                   imagegen.method_now()             # 選ばれていなければ None
#
# 登録は持ち主ごとに1つ。登録した ctx が用済みになった登録は数えない（`board` と同じ）。
# 置き場は `sys` の属性（`_instantale_imagegen_methods`）。注入し直しをまたいで残る。

#: 描き方の並び（この順で出す）と、選ぶ口に出す名前。
METHODS = ("txt2img", "img2img", "chain")
METHOD_LABELS = {"txt2img": "txt2img", "img2img": "img2img", "chain": "txt2img→img2img"}

#: 描き方を選べる絵の種類。
KINDS = ("portrait", "enemy", "background")

METHODS_ATTR = "_instantale_imagegen_methods"

#: いま描いているスレッドの描き方（`drawing_method` の中だけ）。
_DRAWING = threading.local()


def _method_store():
    store = getattr(sys, METHODS_ATTR, None)
    if not isinstance(store, dict):
        store = {"providers": {}, "errors": []}
        setattr(sys, METHODS_ATTR, store)
    return store


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def declare_methods(owner, ctx, fn):
    """`owner` が出せる描き方を返す関数を登録する。同じ持ち主の登録は差し替える。"""
    _method_store()["providers"][str(owner)] = (ctx, fn)


def withdraw_methods(owner):
    """登録を下げる。"""
    _method_store()["providers"].pop(str(owner), None)


def methods(kind):
    """その種類で選べる描き方。先頭は必ず "txt2img"。出す MOD が居なければ txt2img だけ。

    `fn` が投げた例外は握り、その MOD は何も出さなかったものとみなす（`errors` に残す）。
    """
    store = _method_store()
    found = set()
    for owner, (ctx, fn) in list(store["providers"].items()):
        if not _alive(ctx):
            continue
        try:
            found.update(str(method) for method in (fn(kind) or ()))
        except Exception as exc:
            store["errors"].append("{}: {}: {}".format(owner, type(exc).__name__, exc))
            del store["errors"][:-20]
    return ["txt2img"] + [method for method in METHODS[1:] if method in found]


class drawing_method(object):
    """このスレッドで描く絵の描き方を立てる（`with` の間だけ）。知らない描き方は txt2img。"""

    def __init__(self, method):
        self.method = method if method in METHODS else "txt2img"
        self.before = None

    def __enter__(self):
        self.before = getattr(_DRAWING, "method", None)
        _DRAWING.method = self.method
        return self.method

    def __exit__(self, *_exc):
        _DRAWING.method = self.before
        return False


def method_now():
    """このスレッドで選ばれている描き方。選ばれていなければ None。"""
    return getattr(_DRAWING, "method", None)
