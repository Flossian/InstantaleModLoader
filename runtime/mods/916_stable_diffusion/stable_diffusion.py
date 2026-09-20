# -*- coding: utf-8 -*-
r"""Stable Diffusion の解像度・サンプラー・モデル・プロンプトを差し替える。

ゲームは生成の解像度もサンプラーも設定画面から変えられず、
立ち絵を 256x512 で描いてから 512x1024 で描き直し、背景を 1024x512 で描く。
この MOD は**どのバックエンドでも通る1つの出口**でその値を書き換える。

ゲームの側の読み方は GAME.md §2.33、残っている確認は DOC.md §3。

    種類（解像度）  立ち絵 / 敵・モンスター / 背景
    段（サンプラー） 1段目（txt2img） / 2段目（img2img） / 背景

種類は**入口**で分かる（`generate_enemy_image` を通れば敵）。
段は**上の層の関数**で分かる（`generate_image_anime` なら1段目）。
どちらもスレッドに印として立て、出口で読む。
縦横比から portrait / landscape / square を推測する必要は無い
（DLL のプロキシがそうしていたのは、C の層に呼び手が見えないため）。

**既定値はすべて実測したゲームの値**（GAME.md §2.33）。
設定が既定のままの項目は書き換えない。
ゲームが更新で値を変えたとき、こちらの既定が古い値を焼き付けないため。

寸法・サンプラー・安全弁・規則は**生成のたびに変わっていれば読み直す**（`live.py`。
元 MOD のホットリロードに当たる）。道具画面で保存すれば次の絵から効き、
注入し直しは要らない。材料（モデルの置き場）だけは建つ瞬間にしか効かない。

パイプラインの材料（チェックポイント・TAESD・VAE・LoRA の置き場）は
建つ前にしか書けない。
建てる関数 `load_sd_pipeline` を包み、その直前に書く（建てるたびに効く）。
最初の1回は import から構築の開始まで 0.27〜0.62 秒しかなく、
5秒ごとの見張りでは包みが間に合わないので、
`sys.meta_path` の先頭に観測者を置いて import の直後に包みと書き込みを済ませる。
**この観測者は本来ローダの語彙**（TECH.md §3.2.3）で、試作の間だけここに置いてある。
"""
import inspect
import os
import sys
import threading

from instantale_modloader import frames

from . import live as hot
from . import rules as rulebook
from . import sizes

LOG_BASENAME = "stable_diffusion.log"

#: 種類と段の印（スレッドごと）。`apply()` の中に置くと、当て直しを跨いだ回に届かない
#: （VERIFICATION_LOG.md §2.86）。
MARKS = threading.local()

#: 建てるときに見分けたモデルの系統。印と同じ理由でモジュール側に置く。
FAMILY = {"name": None, "path": None}

#: `sys.meta_path` に置いた観測者の目印（入れ直しで積み上げないため）。
OBSERVER_MARK = "_mod_916_stable_diffusion"

# ---- 設定（既定値は mod.json の "settings" と一致させること）------------
# 既定はすべてゲームの実測値。同じ値のままなら触らない。

#: 立ち絵（キャラクタ）の短辺の目標。ゲームの最終段は 512x1024。
PORTRAIT_SHORT = 512

#: 立ち絵の長辺の上限。短辺の目標より先にこちらへ当たればそこで止まる。
PORTRAIT_MAX_LONG = 1024

#: 敵・モンスターの短辺の目標と長辺の上限。ゲームは正方形 512x512 を1段で描く。
ENEMY_SHORT = 512
ENEMY_MAX_LONG = 512

#: 背景の短辺の目標と長辺の上限。ゲームは 1024x512。
BACKGROUND_SHORT = 512
BACKGROUND_MAX_LONG = 1024

#: 1段目（txt2img）のサンプラー。立ち絵と敵で共通（同じ関数を通る）。
STAGE1_METHOD = "euler_a"
STAGE1_STEPS = 20
STAGE1_CFG = 8.0
STAGE1_SCHEDULER = "default"

#: 2段目（img2img）のサンプラー。
STAGE2_METHOD = "dpmpp2m"
STAGE2_STEPS = 15
STAGE2_CFG = 7.0
STAGE2_SCHEDULER = "karras"

#: LCM の段のサンプラー。背景と敵・モンスターが通る。
LCM_METHOD = "lcm"
LCM_STEPS = 5
LCM_CFG = 1.0
LCM_SCHEDULER = "default"

#: チェックポイント・TAESD・VAE・LoRA の置き場。**空でゲームのまま**。
#: SDXL のモデルへ移るときはここを指す（系統は揃えること。DOC.md §1）。
CHECKPOINT_PATH = ""
TAESD_PATH = ""
VAE_PATH = ""
LORA_DIR = ""

#: モデルの系統と食い違う指定を、壊れない側へ寄せるか。`auto` か `off`。
MODEL_SAFETY = "auto"

#: TAESD を使わずに建てる（VAE で復号する）。既定はゲームのまま（使う）。
DISABLE_TAESD = False

#: VAE を渡さずに建てる。既定はゲームのまま（渡す）。
#: 系統の違うモデル（SDXL）に SD1.5 の VAE が残ると、復号が合わずに
#: 絵がマゼンタへ潰れる。TAESD を差し替えたのに色が合わないときはこちらも切る。
DISABLE_VAE = False

#: プロンプトから**ゲームが入れた** `<lora:...>` を外す。**既定は切**（ゲームのまま）。
#: ゲームは背景・敵・（画質が低い設定のとき）立ち絵に SD1.5 用の LCM LoRA を入れる。
#: 系統の違うモデル（SDXL）に当たると**プロセスごと落ちる**ので、
#: モデルを差し替えるときはこちらも入にする（DOC.md §3）。
#: 規則（付け替え・追加）で足したタグは外さない。
STRIP_LORA = False

#: diffusers 系（`diffusers_openvino` など）でも寸法を変える。
#: **既定は切**。この一族は形を固定して変換したモデルを回すので、
#: 寸法を変えると作り直しが走り、実機では 64GB の RAM を使い切って
#: SSD へページングを始めた（DOC.md §3）。sdcpp 系には関係しない。
DIFFUSERS_RESIZE = False

# ------------------------------------------------------------------------

#: 出口の候補。生きたパイプラインの型が自分の MRO の中に持つものだけ包む。
EXIT_METHODS = ("generate_image", "__call__")

#: パイプラインを建てる関数（manager のモジュール関数）。
#: 建てる直前に材料を書き直す口。import の観測（`_ImportObserver`）は
#: **最初の1回**にしか効かない（同じプロセスで2回目のワールド選択をしても
#: import は走らない）ので、建てるたびに効くこちらを本線にする。
PIPELINE_FUNC = "load_sd_pipeline"

#: 段の印を立てる上の層（`stable_diffusion_manager` の関数）。
#: **関数から段を決め打ちしない。**
#: `generate_image_real_lcm` は背景だけのものではなく、
#: `diffusers_openvino` ではキャラの絵もここを通る（実機 2026-09-18）。
#: 段は種類と関数の組で決める（`stage_of`）。
STAGE_FUNCS = ("generate_image_anime", "image_to_image_anime",
               "generate_image_real_lcm")

#: 2段目だと分かる関数。これ以外は1段目。
SECOND_STAGE_FUNC = "image_to_image_anime"

#: LCM の段。背景と敵・モンスターが通る（一族によってはキャラも）。
LCM_FUNC = "generate_image_real_lcm"

#: 種類の印を立てる入口（`image_generation_creature` の関数。GAME.md §2.30）。
KIND_FUNCS = {"generate_enemy_image": "enemy",
              "generate_enemy_image_from_character": "enemy",
              "generate_character_image": "portrait",
              "generate_character_image_from_enemy": "portrait"}

#: 「出口を持っている層」の目印。
MANAGER_MARKS = ("txt2img_pipe", "load_sd_pipeline")

#: 棚卸しで拾うモジュール名（一族ごとに名前が変わるので広めに採る）。
MODULE_HINTS = ("image_generation", "sdcpp", "stable_diffusion", "diffusers",
                "optimum", "openvino")

#: 自分とローダを数えないための除外。
MODULE_SKIP = ("instantale_mod", "instantale_modloader")

#: 呼び出し元のファイル名から種類を決める（入口の印が無いときの予備）。
KIND_HINTS = (("image_generation_background", "background"),
              ("image_generation_creature", "portrait"))

#: 建つ前に書き換えるモジュール変数。`(設定の名前, モジュール側の名前)`。
MATERIALS = (("CHECKPOINT_PATH", "model_path_anime"),
             ("TAESD_PATH", "taesd_path"),
             ("VAE_PATH", "vae_path"),
             ("LORA_DIR", "lora_dir"))

#: sdcpp 系の出口の引数名 → diffusers 系の引数名。
#: 方式とスケジューラは diffusers 側に同じ口が無いので当てない。
DIFFUSERS_NAMES = {"sample_steps": "num_inference_steps",
                   "cfg_scale": "guidance_scale"}

#: 名前を読み替えずにそのまま渡す引数（両方の一族に同じ名前で在る）。
SAME_NAMES = ("prompt", "negative_prompt", "width", "height")


def stage_of(func):
    """サンプラーの設定を引く段。**上の層の関数だけで決まる**（種類は見ない）。

    `generate_image_real_lcm` は LCM の段で、背景と敵・モンスターが通る
    （実機 2026-09-18: 敵は 512x512 を lcm / 5 / cfg 1 で1枚）。
    `diffusers_openvino` ではキャラもここを通る。
    種類では決めない（種類で決めると、同じ呼び出しに違う設定が当たる）。
    """
    if func is None:
        return None
    if func == LCM_FUNC:
        return "lcm"
    return "stage2" if func == SECOND_STAGE_FUNC else "stage1"


def unwrapped(func):
    """ローダの包み（`__original__` の連鎖）を剥がした素の関数。

    署名は素の関数から読む。注入し直した回は `base.__dict__` に前の世代の
    包みが載っていて、そのまま読むと `(*args, **kwargs)` になり、
    引数を名前で引けず**黙って何も書き換えなくなる**。
    """
    for _ in range(32):
        inner = frames.attr(func, "__original__", None)
        if inner is None or inner is func:
            break
        func = inner
    return func


class _LoaderProxy(object):
    """本物のローダに被せて、モジュールの本体が走り終えた直後に知らせる器。"""

    def __init__(self, loader, name, after):
        self._loader = loader
        self._name = name
        self._after = after

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        self._after(self._name, module)

    def __getattr__(self, item):
        return getattr(self._loader, item)


class _ImportObserver(object):
    """`sys.meta_path` の先頭で import の瞬間を捕まえる（`230_` と同じ形）。

    自分では読み込まない。
    他の finder に spec を作らせて、その `loader` だけを1件ぶん包む。
    """

    def __init__(self, after):
        self._after = after
        self._busy = threading.local()
        setattr(self, OBSERVER_MARK, True)

    def _matches(self, name):
        low = name.lower()
        if any(low.startswith(skip) for skip in MODULE_SKIP):
            return False
        return any(hint in low for hint in MODULE_HINTS)

    def find_spec(self, name, path=None, target=None):
        # 投げるとゲームの import ごと落ちる。何があっても None に落とす。
        try:
            return self._find(name, path, target)
        except Exception:
            return None

    def _find(self, name, path, target):
        if frames.attr(self._busy, "on", False) or not self._matches(name):
            return None
        self._busy.on = True
        try:
            spec = None
            for finder in list(sys.meta_path):
                if finder is self:
                    continue
                find = frames.attr(finder, "find_spec", None)
                if find is None:
                    continue
                try:
                    spec = find(name, path, target)
                except Exception:
                    spec = None
                if spec is not None:
                    break
        finally:
            self._busy.on = False
        if spec is None or frames.attr(spec, "loader", None) is None:
            return spec
        try:
            spec.loader = _LoaderProxy(spec.loader, name, self._after)
        except Exception:
            pass
        return spec


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    warn = ctx.warner("stable_diffusion")
    seen = set()

    #: 寸法・サンプラー・安全弁・規則。生成のたびに変わっていれば読み直す（`live.py`）。
    live = hot.Live(ctx, write)
    for line in live.describe():
        write(line)
    if not live.hot():
        write("hot reload: off (場所が控えられない。設定は注入し直しで効く)")

    #: 建つ前に書く材料。**熱くしない**（建つ瞬間にしか効かない）。
    materials = [(attr, ctx.setting(name)) for name, attr in MATERIALS
                 if str(ctx.setting(name) or "").strip()]

    #: diffusers 系で実際に当てた寸法。2つ以上になったら知らせる
    #: （寸法ごとにモデルが作り直され、ゲームを閉じるまで残る。DOC.md §3）。
    diffusers_sizes = set()
    no_taesd = bool(ctx.setting("DISABLE_TAESD"))
    no_vae = bool(ctx.setting("DISABLE_VAE"))

    for attr, value in materials:
        write("material {} -> {!r}".format(attr, value))
    if no_taesd:
        write("material taesd_path -> '' (VAE で復号させる)")
    if no_vae:
        write("material vae_path -> '' (VAE を渡さない)")

    def owner_of(cls, name):
        """`name` を自分の MRO の中に持っているクラス。無ければ None。"""
        for base in frames.attr(cls, "__mro__", (cls,)):
            if base is object:
                continue
            if name in frames.attr(base, "__dict__", {}):
                return base
        return None

    def managers():
        """`txt2img_pipe` か `load_sd_pipeline` を持つモジュール。"""
        found = []
        for name in list(sys.modules):
            low = name.lower()
            if any(low.startswith(skip) for skip in MODULE_SKIP):
                continue
            if not any(hint in low for hint in MODULE_HINTS):
                continue
            mod = sys.modules.get(name)
            if mod is None:
                continue
            for mark in MANAGER_MARKS:
                if frames.attr(mod, mark, None) is not None:
                    found.append(name)
                    break
        return sorted(found)

    def creature_modules():
        """入口（`image_generation_creature`）を持つモジュール。"""
        return [name for name in sys.modules
                if name.endswith(".image_generation_creature")]

    def kind_now():
        """入口の印。無ければ呼び出し元のファイル名から。"""
        kind = frames.attr(MARKS, "kind", None)
        if kind:
            return kind
        chain = frames.caller(depth=5)
        for hint, guess in KIND_HINTS:
            if hint in chain:
                return guess
        return None

    # ------------------------------------------------------------ 印を立てる

    def make_kind_hook(kind):
        def hook(orig, *args, **kwargs):
            before = frames.attr(MARKS, "kind", None)
            MARKS.kind = kind
            try:
                return orig(*args, **kwargs)
            finally:
                MARKS.kind = before
        return hook

    def make_stage_hook(func_name):
        """上の層の関数の名前を控える。段はここでは決めない（`stage_of`）。"""
        def hook(orig, *args, **kwargs):
            before = frames.attr(MARKS, "func", None)
            MARKS.func = func_name
            try:
                return orig(*args, **kwargs)
            finally:
                MARKS.func = before
        return hook

    # -------------------------------------------------------------- 出口で当てる

    def wanted(kind, stage, params):
        """この1回で書き換える引数。触らないなら空の辞書。

        `params` は名前で引ける今の引数。
        サンプラーは**設定が既定と同じ項目には触らない**。
        """
        changes = {}
        scales, samplers, rules = live.scales, live.samplers, live.rules
        scale = scales.get(kind, 1.0)
        if rulebook.skip_upscale(rules, kind, params.get("prompt")):
            scale = 1.0
        new = sizes.scaled(params.get("width"), params.get("height"), scale)
        if new is not None:
            changes["width"], changes["height"] = new
        for name, target in (("prompt", "prompt"), ("negative_prompt", "negative")):
            body = params.get(name)
            if not isinstance(body, str):
                continue
            got = rulebook.rewrite(rules, kind, target, body, body)
            if got is not None:
                changes[name] = got
        if live.strip_lora:
            drop_game_lora(params, changes)
        if stage in samplers:
            method, steps, cfg, scheduler = samplers[stage]
            game = sizes.GAME_SAMPLER[stage]
            for value, game_value, name in (
                    (method, game[0], "sample_method"),
                    (steps, game[1], "sample_steps"),
                    (cfg, game[2], "cfg_scale"),
                    (scheduler, game[3], "scheduler")):
                if value != game_value:
                    changes[name] = value
        add_safety(stage, params, changes)
        return changes

    def drop_game_lora(params, changes):
        """**ゲームが入れた** `<lora:...>` だけを外す。外したら True。

        外す名前は書き換える前の本文から採る。
        規則で足した別の名前のタグ（SDXL 用に付け替えたものなど）は残す。
        付け替えでゲームの名前を SDXL 用の名前へ写した行は、名前が変わっているので
        こちらでは外れない（付け替えが勝つ）。
        """
        original = params.get("prompt")
        if not isinstance(original, str):
            return False
        names = rulebook.lora_names(original)
        if not names:
            return False
        prompt = changes.get("prompt", original)
        got = rulebook.strip_lora(prompt, only=names)
        if got == prompt:
            return False
        changes["prompt"] = got
        return True

    def add_safety(stage, params, changes):
        """モデルの系統と食い違う指定を、壊れない側へ寄せる。

        今日の実機で踏んだ2つの壊れ方をそのまま塞ぐ（DOC.md §3）。
        SDXL に SD1.5 用の LoRA が当たるとプロセスごと落ち、
        SDXL を LCM の設定（5 steps / cfg 1.0）で回すとマゼンタに潰れる。
        設定で明示された値は動かさない（**寄せるのは素のままの項目だけ**）。
        """
        family = FAMILY.get("name")
        samplers = live.samplers
        if live.safety != "auto" or family is None:
            return
        # 寸法: その系統で無理の無い画素数へ寄せる。
        width = changes.get("width", params.get("width"))
        height = changes.get("height", params.get("height"))
        # 寸法は**言うだけで動かさない**。
        # 「SDXL は小さいと壊れる」は実測で否定された
        # （0.52 メガピクセルの背景が正しく出た。壊していたのはサンプラー）。
        # SD1.5 の上限超えも画質の話で破綻ではない（1.38 メガピクセルが通っている）。
        fixed = sizes.safe_size(width, height, family)
        if fixed is not None:
            warn("safety-size-{}".format(family),
                 "{} に {}x{}（{:.2f} メガピクセル）は学習の寸法から離れている。"
                 "崩れるようなら {}x{} 辺りが無難".format(
                     family, width, height, width * height / 1e6,
                     fixed[0], fixed[1]))
        if family != "sdxl":
            return
        # LoRA: ゲームが入れる SD1.5 用のタグは落とす（落ちるため）。
        # 規則で足したタグは残す（SDXL 用に付け替えたものを外しては本末転倒）。
        if drop_game_lora(params, changes):
            warn("safety-lora",
                 "SDXL に SD1.5 用の LoRA が当たると落ちるので、ゲームが入れたタグを外した")
        # サンプラー: **LCM の段が素のまま**のときだけ SDXL 向きへ。
        # 壊れると測れているのは lcm / 5 / cfg 1.0 だけで、
        # 1段目・2段目の euler_a / dpmpp2m は SDXL でも普通に回る設定なので触らない。
        if stage == "lcm" and samplers[stage] == sizes.GAME_SAMPLER[stage]:
            for value, name in zip(sizes.SDXL_SAMPLER,
                                   ("sample_method", "sample_steps",
                                    "cfg_scale", "scheduler")):
                changes[name] = value
            warn("safety-sampler",
                 "SDXL をゲームの LCM の設定で回すと絵にならないので "
                 "{} にした".format(sizes.SDXL_SAMPLER))

    def brief_change(name, params, value):
        """ログの1項目。プロンプトは本文を出さず、字数と LoRA の出入りだけ出す。"""
        if name not in ("prompt", "negative_prompt"):
            return "{}->{}".format(params.get(name), value)
        was = params.get(name) or ""
        note = "{}字 -> {}字".format(len(was), len(value))
        if name == "prompt":
            before, after = rulebook.lora_names(was), rulebook.lora_names(value)
            if before - after:
                note += " lora 外した {}".format(sorted(before - after))
            if after - before:
                note += " lora 足した {}".format(sorted(after - before))
        return note

    def pixels_note(params, changes):
        """画素数が何倍になるか。時間とメモリはここで効くので1行に添える。"""
        if "width" not in changes or "height" not in changes:
            return ""
        was = (params.get("width") or 0) * (params.get("height") or 0)
        now = changes["width"] * changes["height"]
        if not was:
            return ""
        return "  画素 x{:.2f}".format(float(now) / was)

    def for_diffusers(changes, params):
        """diffusers 系の引数名へ直す。当てられない項目は落とす。"""
        out = {}
        for name, value in changes.items():
            if name in SAME_NAMES and name not in ("width", "height"):
                out[name] = value           # prompt / negative_prompt は同じ名前
                continue
            if name in ("width", "height"):
                # 形を固定して変換したモデルなので、寸法を変えると作り直しが走る。
                # 実機では RAM を使い切ってページングに入った（DOC.md §3）。
                if live.diffusers_resize:
                    out[name] = value
                    if name == "width":
                        diffusers_sizes.add((value, changes.get("height")))
                        if len(diffusers_sizes) > 1:
                            warn("diffusers-sizes",
                                 "この一族で寸法を {} 通り使った。"
                                 "寸法ごとにモデルが作り直され、"
                                 "ゲームを閉じるまでメモリに残る: {}".format(
                                     len(diffusers_sizes), sorted(diffusers_sizes)))
                else:
                    warn("diffusers-resize",
                         "この一族では寸法を変えない（設定で入にできるが、"
                         "モデルの作り直しで RAM を使い切ることがある）")
                continue
            other = DIFFUSERS_NAMES.get(name)
            if other and other in params:
                out[other] = value
            elif name in ("sample_method", "scheduler") or other is None:
                warn("diffusers-{}".format(name),
                     "{} はこの一族には当てられない（同じ口が無い）".format(name))
        return out

    def make_exit_hook(label, sig, diffusers):
        def hook(orig, self, *args, **kwargs):
            live.refresh()                 # 設定と規則が変わっていれば読み直す
            kind = kind_now()
            stage = stage_of(frames.attr(MARKS, "func", None))
            if sig is None:
                params = dict(kwargs)
                bound = None
            else:
                bound = sig.bind_partial(self, *args, **kwargs)
                bound.apply_defaults()
                params = dict(bound.arguments)
            changes = wanted(kind, stage, params)
            if diffusers:
                changes = for_diffusers(changes, params)
            # その一族の出口に無い引数は落とす（名前を新設しない）。
            changes = {name: value for name, value in changes.items()
                       if name in params or name in ("width", "height")}
            if not changes:
                return orig(self, *args, **kwargs)
            write("{} [{}/{}] {}{}".format(
                label, kind, stage,
                " ".join("{}: {}".format(name, brief_change(name, params, value))
                         for name, value in sorted(changes.items())),
                pixels_note(params, changes)))
            if bound is None:
                if not set(changes) <= set(kwargs):
                    warn(label, "{}: 署名が読めず、位置引数は触れない".format(label))
                    return orig(self, *args, **kwargs)
                kwargs = dict(kwargs)
                kwargs.update(changes)
                return orig(self, *args, **kwargs)
            bound.arguments.update(changes)
            return orig(*bound.args, **bound.kwargs)
        return hook

    # ------------------------------------------------------- 当てる・当て直す

    def install(target, hook):
        if target in seen:
            return 0
        seen.add(target)
        ctx.wrap(target, required=False, safe=True)(hook)
        write("wrapped {}".format(target))
        return 1

    def attach():
        # **用済みの apply() は当て直さない。**
        # `load_sd_pipeline` の包みも見張りも、注入し直した後まで生きている。
        # そこから当て直すと、ローダは「いまの世代」として入れてしまい、
        # 新しい世代の包みの**外側に**古い包みが重なる。
        # 重なると、古い包みが新しい包みの書いた寸法をもう一度拡大し、
        # 印は別インスタンスなので段が None になる
        # （実機 2026-09-20: 1段目が 256x512 -> 512x1024 -> 1024x2048 と二重に効き、
        #  22 秒かかった。`[portrait/None]` の行がその印）。
        if ctx.superseded():
            return 0
        added = 0
        for name in managers():
            # 系統は import のときにも見るが、**注入し直しでは import が起きない**。
            # 当て直しのたびに、いま効いているチェックポイントで見直す。
            detect_family(sys.modules.get(name))
        for name in creature_modules():
            mod = sys.modules.get(name)
            for fn, kind in KIND_FUNCS.items():
                if frames.attr(mod, fn, None) is None:
                    continue
                added += install("{}:{}".format(name, fn), make_kind_hook(kind))
        for name in managers():
            mod = sys.modules.get(name)
            for fn in STAGE_FUNCS:
                if frames.attr(mod, fn, None) is None:
                    continue
                added += install("{}:{}".format(name, fn), make_stage_hook(fn))
            if frames.attr(mod, PIPELINE_FUNC, None) is not None:
                added += install("{}:{}".format(name, PIPELINE_FUNC),
                                 make_pipeline_hook(name))
            pipe = frames.attr(mod, "txt2img_pipe", None)
            if pipe is None:
                continue
            cls = type(pipe)
            for method in EXIT_METHODS:
                base = owner_of(cls, method)
                if base is None:
                    continue
                target = "{}:{}.{}".format(
                    frames.attr(base, "__module__", "?"),
                    frames.attr(base, "__qualname__", base.__name__), method)
                try:
                    sig = inspect.signature(unwrapped(base.__dict__.get(method)))
                except Exception:
                    sig = None
                diffusers = method == "__call__"
                added += install(target, make_exit_hook(target, sig, diffusers))
        if added:
            ctx.refresh_status()
        return added

    # ------------------------------------------- 建つ前に材料を差し替える

    def is_manager(module):
        return any(frames.attr(module, mark, None) is not None
                   for mark in MANAGER_MARKS)

    def write_materials(name, module):
        """manager のモジュール変数を設定の値へ。既定（空）の項目は触らない。

        同じ値なら書かない（建てるたびに呼ばれるので、ログが毎回出ないように）。
        """
        for attr, value in materials:
            now = frames.attr(module, attr, None)
            if now is None or now == value:
                continue
            write("{}: {} {!r} -> {!r}".format(name, attr, now, value))
            setattr(module, attr, value)
        if no_taesd and frames.attr(module, "taesd_path", None):
            write("{}: taesd_path {!r} -> ''".format(name, module.taesd_path))
            module.taesd_path = ""
        if no_vae and frames.attr(module, "vae_path", None):
            write("{}: vae_path {!r} -> ''".format(name, module.vae_path))
            module.vae_path = ""
        detect_family(module)

    def on_import_done(name, module):
        """モジュールの本体が走り終えた直後。パイプラインはまだ建っていない。

        観測者は `sys.meta_path` に残り続けるので、自分の apply() が用済みになった後は
        何もしない（古い設定を書いてしまうため）。
        manager 以外（`diffusers` の下の何百ものモジュールも名前で引っかかる）では
        棚卸しを走らせない。
        """
        try:
            if ctx.superseded():
                return
            if not is_manager(module):
                return
            write_materials(name, module)
            attach()
        except Exception:
            ctx.log_exc("image quality: import hook failed")

    def make_pipeline_hook(name):
        """`load_sd_pipeline` の直前に材料を書き、建った後に出口を包み直す。

        import の観測は最初の1回にしか効かない。
        同じプロセスで2回目のワールド選択をすると import は走らず
        `load_sd_pipeline()` だけが呼ばれるので、こちらが本線になる。
        """
        def hook(orig, *args, **kwargs):
            module = sys.modules.get(name)
            # 用済みの apply() は書かない（古い設定を建てる直前に戻してしまう）。
            if module is not None and not ctx.superseded():
                write_materials(name, module)
            try:
                return orig(*args, **kwargs)
            finally:
                # 組み直しで別のクラスになっていることがあるので包み直す。
                attach()
        return hook

    def detect_family(module):
        """いま効いているチェックポイントの中身から系統を見分ける。

        同じパスなら読み直さない（当て直しのたびにヘッダを読まないため）。
        """
        path = frames.attr(module, "model_path_anime", None)
        if not path or FAMILY.get("path") == path:
            return
        full = path if os.path.isabs(path) else os.path.join(ctx.game_dir, path)
        name = sizes.family_of(full)
        FAMILY.update({"name": name, "path": path})
        if name is None:
            write("safety: 系統が読めない（safetensors のヘッダを見た）: {}".format(full))
        else:
            write("safety: 系統は {}（{}）".format(name, os.path.basename(path)))

    def install_observer():
        kept = [f for f in sys.meta_path
                if frames.attr(f, OBSERVER_MARK, None) is None]
        sys.meta_path[:] = [_ImportObserver(on_import_done)] + kept

    install_observer()
    attach()

    def start_poll():
        """建った後に現れる出口（組み直し・バックエンド切替）へ当て直す。"""
        try:
            from kivy.clock import Clock
        except Exception:
            ctx.log("image quality: no kivy Clock; polling disabled", level="WARN")
            return

        def poll(_dt):
            if ctx.superseded():
                return False
            try:
                attach()
            except Exception:
                ctx.log_exc("image quality: poll failed")
            return True

        Clock.schedule_interval(poll, 5.0)

    ctx.on_ready(start_poll,
                 key="916_stable_diffusion:poll:{}".format(ctx.generation))
