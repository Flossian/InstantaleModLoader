# -*- coding: utf-8 -*-
"""343_stable_diffusion をゲーム抜きで通す。

    python tools/tests/test_stable_diffusion.py

見ているのは、この MOD が自分で決めている所だけ。

  倍率   … 短辺の目標と長辺の上限で決まる。縮めない・比を変えない・64 の倍数
  既定   … すべて実測したゲームの値。既定のままの項目は書き換えない
  当て先 … バックエンドを名指しせず、生きたパイプラインの型から出口を引く
  印     … 種類は入口（敵か立ち絵か）、段は上の層の関数から
  書換   … 種類ごとの寸法と段ごとのサンプラー。位置引数でも通る
  一族   … diffusers 系は引数名を読み替え、当てられない項目は落とす
  建つ前 … import の直後に材料（チェックポイント・TAESD・VAE・LoRA）を差し替える
"""
import importlib.util
import inspect
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402

MOD_DIR = os.path.join(MODS_DIR, "343_stable_diffusion")
MANIFEST_PATH = os.path.join(MOD_DIR, "mod.json")
with io.open(MANIFEST_PATH, encoding="utf-8") as fh:
    MANIFEST = json.load(fh)
MOD_PATH = os.path.join(MOD_DIR, MANIFEST["entry"])

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# `from . import sizes` が通るよう、パッケージとして読み込む（`131_` の検査と同じ）。
spec = importlib.util.spec_from_file_location(
    "stable_diffusion_under_test", MOD_PATH, submodule_search_locations=[MOD_DIR])
MOD = importlib.util.module_from_spec(spec)
sys.modules["stable_diffusion_under_test"] = MOD
spec.loader.exec_module(MOD)
SZ = MOD.sizes          # 寸法と系統の計算は本体と道具で共有している


# ---------------------------------------------------------------- 偽ゲーム
class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    _mod = None

    def __init__(self, out_dir, settings):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.game_dir = out_dir          # 本物の ctx はゲームの exe の在る場所
        self.runtime_dir = os.path.join(out_dir, "runtime")   # settings\ はこの隣
        self.mod_dir = MOD_DIR           # 宣言（mod.json）を読む場所
        self.settings = settings
        self.hooks = {}
        self.errors = []
        self.notes = []
        self.warnings = []
        self.generation = "test"

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def setting(self, name):
        return self.settings[name]

    def logger(self, name, **kw):
        real = ml.ModContext.logger(self, name, **kw)

        def write(message):
            self.notes.append(str(message))
            return real(message)
        return write

    def warner(self, tag):
        def warn(key, message):
            self.warnings.append((key, message))
        return warn

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def refresh_status(self):
        pass

    def superseded(self):
        return False

    def on_ready(self, fn, key=None, delay=0):
        return True            # Clock は無いので積むだけ

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


class Pipe(object):
    """sdcpp 系の出口（引数の並びは実機の署名と同じ）。"""

    def generate_image(self, prompt, negative_prompt="", width=512, height=512,
                       cfg_scale=7.0, scheduler="default", sample_method="euler_a",
                       sample_steps=20, seed=-1):
        return {"width": width, "height": height, "cfg_scale": cfg_scale,
                "scheduler": scheduler, "sample_method": sample_method,
                "sample_steps": sample_steps, "prompt": prompt,
                "negative_prompt": negative_prompt, "seed": seed}


class OVPipe(object):
    """diffusers 系の出口（引数名が違う）。"""

    def __call__(self, prompt, negative_prompt="", width=512, height=512,
                 num_inference_steps=5, guidance_scale=1.0):
        return {"width": width, "height": height,
                "num_inference_steps": num_inference_steps,
                "guidance_scale": guidance_scale,
                "prompt": prompt, "negative_prompt": negative_prompt}


MANAGER = "image_generation.fakebackend.stable_diffusion_manager"
CREATURE = "image_generation.fakebackend.image_generation_creature"


def fake_backend(manager=MANAGER, creature=CREATURE, pipe=None):
    """`managers()` と `creature_modules()` が拾う形の偽モジュールを置く。"""
    mod = types.ModuleType(manager)
    mod.txt2img_pipe = Pipe() if pipe is None else pipe
    mod.load_sd_pipeline = lambda: None
    mod.model_path_anime = "runtime/models/sd15/checkpoints/sotemix_v30.safetensors"
    mod.taesd_path = "runtime/models/sd15/taesd/diffusion_pytorch_model.safetensors"
    mod.vae_path = "runtime/models/sd15/vae/vae.safetensors"
    mod.lora_dir = "runtime/models/sd15/lora"
    for fn in MOD.STAGE_FUNCS:
        setattr(mod, fn, lambda *a, **kw: None)
    sys.modules[manager] = mod

    entry = types.ModuleType(creature)
    for fn in MOD.KIND_FUNCS:
        setattr(entry, fn, lambda *a, **kw: None)
    sys.modules[creature] = entry
    return mod, entry


def cleanup(*names):
    for name in names:
        sys.modules.pop(name, None)
    sys.meta_path[:] = [f for f in sys.meta_path
                        if getattr(f, MOD.OBSERVER_MARK, None) is None]
    MOD.MARKS.kind = None
    MOD.MARKS.func = None


def fresh(_state_dir=None, **settings):
    values = {key: MANIFEST["settings"][key]["default"]
              for key in MANIFEST["settings"]}
    values.update(settings)
    ctx = Ctx(tempfile.mkdtemp(prefix="stable_diffusion_test_"), values)
    if _state_dir:
        ctx.state_dir = _state_dir      # 規則の置き場（`state\`）を差し替える
    MOD.apply(ctx)
    return ctx


def observer():
    found = [f for f in sys.meta_path if getattr(f, MOD.OBSERVER_MARK, None)]
    return found[0] if found else None


# ---------------------------------------------------------------- 倍率
print("倍率（短辺の目標と長辺の上限）")
check("ゲームの値のままなら 1.0", SZ.size_scale("portrait", 512, 1024) == 1.0)
check("短辺だけ上げても長辺の上限で止まる",
      SZ.size_scale("portrait", 832, 1024) == 1.0,
      SZ.size_scale("portrait", 832, 1024))
check("長辺も上げれば短辺の目標どおり",
      SZ.size_scale("portrait", 832, 1664) == 1.625,
      SZ.size_scale("portrait", 832, 1664))
check("先に当たるのは小さいほう",
      SZ.size_scale("portrait", 1024, 1280) == 1.25,
      SZ.size_scale("portrait", 1024, 1280))
check("下げても縮めない", SZ.size_scale("portrait", 256, 512) == 1.0)
check("知らない種類は 1.0", SZ.size_scale("item", 2048, 2048) == 1.0)

print("丸め")
check("64 の倍数へ丸める", SZ.round_side(300) == 320, SZ.round_side(300))
check("片辺は 2048 で止める", SZ.round_side(9999) == SZ.MAX_SIDE)
check("1.0 は触らない", SZ.scaled(512, 1024, 1.0) is None)
check("1.625 倍（比を保つ）", SZ.scaled(512, 1024, 1.625) == (832, 1664),
      SZ.scaled(512, 1024, 1.625))
check("1段目も同じ比で上がる", SZ.scaled(256, 512, 1.625) == (448, 832),
      SZ.scaled(256, 512, 1.625))
check("上限に当たったら倍率を下げる（歪ませない）",
      SZ.scaled(512, 1024, 4.0) == (1024, 2048), SZ.scaled(512, 1024, 4.0))
check("整数でない寸法は触らない", SZ.scaled(None, 512, 2.0) is None)

print("既定値")
for key in MANIFEST["settings"]:
    check("{} の既定値がコードと mod.json で同じ".format(key),
          getattr(MOD, key) == MANIFEST["settings"][key]["default"])
check("サンプラーの既定は実測したゲームの値",
      SZ.GAME_SAMPLER["stage1"] == (MOD.STAGE1_METHOD, MOD.STAGE1_STEPS,
                                     MOD.STAGE1_CFG, MOD.STAGE1_SCHEDULER)
      and SZ.GAME_SAMPLER["stage2"] == (MOD.STAGE2_METHOD, MOD.STAGE2_STEPS,
                                         MOD.STAGE2_CFG, MOD.STAGE2_SCHEDULER)
      and SZ.GAME_SAMPLER["lcm"] == (MOD.LCM_METHOD, MOD.LCM_STEPS,
                                      MOD.LCM_CFG, MOD.LCM_SCHEDULER))

# ---------------------------------------------------------------- 当て先
print("当て先（バックエンドを名指ししない）")
mod, entry = fake_backend()
ctx = fresh(PORTRAIT_SHORT=832, PORTRAIT_MAX_LONG=1664, STAGE1_STEPS=28)
exit_target = "{}:Pipe.generate_image".format(Pipe.__module__)
check("出口を生きたパイプラインの型から引いた", exit_target in ctx.hooks, sorted(ctx.hooks))
for fn in MOD.STAGE_FUNCS:
    check("段の印 {} を包んだ".format(fn), "{}:{}".format(MANAGER, fn) in ctx.hooks)
for fn in MOD.KIND_FUNCS:
    check("種類の印 {} を包んだ".format(fn), "{}:{}".format(CREATURE, fn) in ctx.hooks)
check("モジュール名を決め打ちしていない",
      not any("sdcpp" in t for t in ctx.hooks), sorted(ctx.hooks))

# ---------------------------------------------------------------- 印と書換
print("印（種類と段）")
pipe = mod.txt2img_pipe
hook = ctx.hooks[exit_target]


def run(kind=None, func=None, **kwargs):
    MOD.MARKS.kind = kind
    MOD.MARKS.func = func
    return hook(Pipe.generate_image, pipe, **kwargs)


seen_kind = {}
ctx.hooks["{}:generate_enemy_image".format(CREATURE)](
    lambda *a, **kw: seen_kind.update(kind=MOD.MARKS.kind))
check("入口を通ると種類の印が立つ", seen_kind.get("kind") == "enemy", seen_kind)
check("入口を出ると印が戻る", MOD.MARKS.kind is None, MOD.MARKS.kind)

seen_func = {}
ctx.hooks["{}:image_to_image_anime".format(MANAGER)](
    lambda *a, **kw: seen_func.update(func=MOD.MARKS.func))
check("上の層を通ると関数の印が立つ", seen_func.get("func") == "image_to_image_anime", seen_func)

print("段の決め方（関数で決め打ちしない）")
check("lcm の関数は LCM の段（種類は見ない）",
      MOD.stage_of("generate_image_real_lcm") == "lcm")
check("img2img は2段目", MOD.stage_of("image_to_image_anime") == "stage2")
check("それ以外は1段目", MOD.stage_of("generate_image_anime") == "stage1")
check("関数が分からなければ段も決めない", MOD.stage_of(None) is None)


def _wrapped_twice(fn):
    """ローダの包みの形（`__original__` の連鎖）を2層まねる。"""
    def outer(*a, **kw):
        return fn(*a, **kw)
    outer.__original__ = fn

    def outermost(*a, **kw):
        return outer(*a, **kw)
    outermost.__original__ = outer
    return outermost


check("署名は前の世代の包みを剥がして読む",
      "width" in inspect.signature(MOD.unwrapped(_wrapped_twice(Pipe.generate_image))).parameters)

print("寸法とサンプラーの書き換え")
got = run("portrait", "generate_image_anime", prompt="p", width=256, height=512)
check("立ち絵の1段目が目標の比で上がる", (got["width"], got["height"]) == (448, 832), got)
check("既定と違う項目だけ書き換わる（steps）", got["sample_steps"] == 28, got)
check("既定のままの項目は触らない（method / cfg / scheduler）",
      got["sample_method"] == "euler_a" and got["cfg_scale"] == 7.0
      and got["scheduler"] == "default", got)
check("プロンプトは触らない", got["prompt"] == "p")

got = run("portrait", "image_to_image_anime", prompt="p", width=512, height=1024)
check("立ち絵の2段目も同じ比で上がる", (got["width"], got["height"]) == (832, 1664), got)

got = run("enemy", "generate_image_anime", prompt="p", width=256, height=512)
check("敵は別の設定（既定なので触らない）",
      (got["width"], got["height"]) == (256, 512), got)

got = run("background", "generate_image_real_lcm", prompt="p", width=1024, height=512)
check("背景も既定なので触らない", (got["width"], got["height"]) == (1024, 512), got)

got = run("enemy", "generate_image_real_lcm", prompt="p", width=512, height=512)
check("敵は正方形 512x512 が素（既定なので触らない）",
      (got["width"], got["height"]) == (512, 512), got)
check("敵の素は正方形として持っている", SZ.GAME_SIZE["enemy"] == (512, 512),
      SZ.GAME_SIZE["enemy"])
check("敵の短辺を上げれば正方形のまま上がる",
      SZ.scaled(512, 512, SZ.size_scale("enemy", 768, 768)) == (768, 768),
      SZ.size_scale("enemy", 768, 768))

MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_anime"
got = hook(Pipe.generate_image, pipe, "p", "n", 256, 512)
check("位置引数で来ても書き換わる", (got["width"], got["height"]) == (448, 832), got)

print("系統の見分け（中身で決める）")
import struct as _struct  # noqa: E402


def fake_checkpoint(names, suffix):
    """safetensors のヘッダだけを持つ偽のチェックポイント。"""
    head = json.dumps({n: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}
                       for n in names}).encode("utf-8")
    path = os.path.join(tempfile.mkdtemp(prefix="ckpt_"), "model" + suffix)
    with io.open(path, "wb") as fh:
        fh.write(_struct.pack("<Q", len(head)))
        fh.write(head)
        fh.write(bytes(2))
    return path


sd15_path = fake_checkpoint(["cond_stage_model.transformer.x",
                             "model.diffusion_model.input_blocks.0.0.weight"], ".safetensors")
sdxl_path = fake_checkpoint(["conditioner.embedders.1.model.text_projection",
                             "model.diffusion_model.label_emb.0.0.weight"], ".safetensors")
check("SD1.5 を中身で見分ける", SZ.family_of(sd15_path) == "sd15", SZ.family_of(sd15_path))
check("SDXL を中身で見分ける", SZ.family_of(sdxl_path) == "sdxl", SZ.family_of(sdxl_path))
check("読めないファイルは None", SZ.family_of(__file__) is None)
check("無いファイルは None", SZ.family_of(sd15_path + ".missing") is None)

print("無難な寸法（助言の計算。値は動かさない）")
check("SDXL に 512x1024 は小さいので上げる",
      SZ.safe_size(512, 1024, "sdxl") == (704, 1344), SZ.safe_size(512, 1024, "sdxl"))
check("上げた先は下限（0.9 メガピクセル）を割らない",
      704 * 1344 >= SZ.SDXL_MIN_PIXELS, 704 * 1344)
check("下げた先は上限（1.1 メガピクセル）を超えない",
      SZ.safe_size(1024, 2048, "sd15")[0] * SZ.safe_size(1024, 2048, "sd15")[1]
      <= SZ.SD15_MAX_PIXELS, SZ.safe_size(1024, 2048, "sd15"))
check("SDXL の 1024x2048 はそのまま", SZ.safe_size(1024, 2048, "sdxl") is None)
check("SD1.5 の 1024x2048 は下げる",
      SZ.safe_size(1024, 2048, "sd15") is not None, SZ.safe_size(1024, 2048, "sd15"))
check("SD1.5 の素（512x1024）はそのまま", SZ.safe_size(512, 1024, "sd15") is None)
check("系統が分からなければ触らない", SZ.safe_size(512, 1024, None) is None)

print("プロンプトの規則（元 MOD の ini の節に当たる）")
RB = MOD.rulebook
RULES = {
    "lora_map": [{"enabled": True, "from": "LCM_LoRA_Weights_SD15", "to": "off"},
                 {"enabled": True, "from": "oldStyle", "to": "newStyle:0.7"}],
    "remove": [{"enabled": True, "kind": "portrait", "target": "prompt",
                "text": "medieval, watercolor"}],
    "replace": [{"enabled": True, "kind": "background", "target": "prompt",
                 "text": "cyberpunk, {prompt}, neon"}],
    "add": [{"enabled": True, "kind": "portrait", "target": "prompt",
             "when": "1boy/male/man", "text": "<lora:maleStyle:0.8>"},
            {"enabled": True, "kind": "portrait", "target": "prompt",
             "when": "!1boy/!male/!man", "text": "<lora:femaleStyle:0.8>"},
            {"enabled": False, "kind": "portrait", "target": "prompt",
             "text": "切った行"},
            {"enabled": True, "kind": "any", "target": "negative",
             "text": "bad hands"}],
    "skip_upscale": [{"enabled": True, "kind": "any", "when": "pixel art/sprite"}],
}
check("条件は単語境界つき（male は female に当たらない）",
      RB.matches("male", "a female knight") is False)
check("条件は / でどれか1つ", RB.matches("1boy/male/man", "a young man") is True)
check("! は含まれないことが条件", RB.matches("!male", "a female knight") is True)
check("! は全部満たす必要がある",
      RB.matches("!male/!man", "a young man") is False)
check("空の条件はいつも成立", RB.matches("", "なんでも") is True)

check("除去は区画単位の完全一致",
      RB.remove_tags("solo, medieval, watercolor painting, 1boy",
                     "medieval, watercolor") == "solo, watercolor painting, 1boy",
      RB.remove_tags("solo, medieval, watercolor painting, 1boy", "medieval, watercolor"))
GAME_NEG = "photoreal, (nsfw, worst quality, low quality:1.4), thighs, nude"
check("重みの括弧の中の区画も当たる（先頭の括弧は次の区画へ）",
      RB.remove_tags(GAME_NEG, "nsfw")
      == "photoreal, (worst quality, low quality:1.4), thighs, nude",
      RB.remove_tags(GAME_NEG, "nsfw"))
check("括弧の末尾の区画を消すと :重み) は前の区画へ",
      RB.remove_tags(GAME_NEG, "low quality")
      == "photoreal, (nsfw, worst quality:1.4), thighs, nude",
      RB.remove_tags(GAME_NEG, "low quality"))
check("真ん中の区画はそのまま落ちる",
      RB.remove_tags(GAME_NEG, "worst quality")
      == "photoreal, (nsfw, low quality:1.4), thighs, nude",
      RB.remove_tags(GAME_NEG, "worst quality"))
check("括弧が閉じた1区画は丸ごと消える",
      RB.remove_tags("a, (nsfw:1.4), b", "nsfw") == "a, b",
      RB.remove_tags("a, (nsfw:1.4), b", "nsfw"))
check("指定側に飾りが付いていても本体で比べる",
      RB.remove_tags(GAME_NEG, "(nsfw:1.2)")
      == "photoreal, (worst quality, low quality:1.4), thighs, nude")
check("括弧の中身を全部消すと括弧ごと消える",
      RB.remove_tags(GAME_NEG, "nsfw, worst quality, low quality")
      == "photoreal, thighs, nude",
      RB.remove_tags(GAME_NEG, "nsfw, worst quality, low quality"))
check("付け替え（off で外す）",
      RB.remap_lora("a, <lora:LCM_LoRA_Weights_SD15:1>, b",
                    [("LCM_LoRA_Weights_SD15", "off")]) == "a, , b",
      RB.remap_lora("a, <lora:LCM_LoRA_Weights_SD15:1>, b", [("LCM_LoRA_Weights_SD15", "off")]))
check("付け替え（名前と重みを差し替え）",
      RB.remap_lora("<lora:oldStyle:1>", [("oldStyle", "newStyle:0.7")])
      == "<lora:newStyle:0.7>")
check("知らない名前は触らない",
      RB.remap_lora("<lora:keepMe:1>", [("other", "off")]) == "<lora:keepMe:1>")

got = RB.rewrite(RULES, "portrait", "prompt",
                 "solo, medieval, 1boy, watercolor, silver hair",
                 "solo, medieval, 1boy, watercolor, silver hair")
check("立ち絵: 除去と条件つき追加が当たる",
      got == "solo, 1boy, silver hair, <lora:maleStyle:0.8>", got)
check("切った行は当たらない", "切った行" not in (got or ""))
got = RB.rewrite(RULES, "portrait", "prompt",
                 "solo, young woman, watercolor", "solo, young woman, watercolor")
check("条件が反対なら別の行が当たる", "femaleStyle" in (got or ""), got)
got = RB.rewrite(RULES, "background", "prompt",
                 "outdoor, <lora:LCM_LoRA_Weights_SD15:1>, masterpiece",
                 "outdoor, <lora:LCM_LoRA_Weights_SD15:1>, masterpiece")
check("背景: 置換に {prompt} が埋まり、付け替えの穴も塞がる",
      got == "cyberpunk, outdoor, masterpiece, neon", got)
got = RB.rewrite(RULES, "enemy", "negative", "worst quality", "worst quality")
check("ネガティブ側にも当たる（any の行）", got == "worst quality, bad hands", got)
check("当たる行が無ければ触らない",
      RB.rewrite(RB.empty(), "portrait", "prompt", "a, b", "a, b") is None)
check("skip の条件", RB.skip_upscale(RULES, "portrait", "pixel art of a cat") is True
      and RB.skip_upscale(RULES, "portrait", "a cat") is False)

print("規則がフック越しに効く")
rules_dir = tempfile.mkdtemp(prefix="stable_diffusion_rules_")
os.makedirs(os.path.join(rules_dir, RB.STATE_DIRNAME))
io.open(RB.rules_path(rules_dir), "w", encoding="utf-8").write(
    json.dumps(RULES, ensure_ascii=False))
ctx_rules = fresh(PORTRAIT_SHORT=832, PORTRAIT_MAX_LONG=1664, _state_dir=rules_dir)
MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_anime"
got = ctx_rules.hooks[exit_target](
    Pipe.generate_image, pipe, prompt="solo, medieval, 1boy, watercolor",
    width=256, height=512)
check("出口でプロンプトが書き換わる",
      got["prompt"] == "solo, 1boy, <lora:maleStyle:0.8>", got["prompt"])
check("同じ回に寸法も当たる", (got["width"], got["height"]) == (448, 832), got)
got = ctx_rules.hooks[exit_target](
    Pipe.generate_image, pipe, prompt="pixel art of a hero",
    width=256, height=512)
check("skip の条件に当たると寸法を触らない",
      (got["width"], got["height"]) == (256, 512), got)
MOD.MARKS.kind, MOD.MARKS.func = None, "generate_image_anime"
got = ctx_rules.hooks[exit_target](
    Pipe.generate_image, pipe, prompt="solo, medieval, 1boy, watercolor",
    negative_prompt="worst quality", width=256, height=512)
check("種類が分からない回は種類つきの行を当てない（立ち絵の除去が掛からない）",
      got["prompt"] == "solo, medieval, 1boy, watercolor", got["prompt"])
check("種類が分からなくても any の行は当たる",
      got["negative_prompt"] == "worst quality, bad hands", got["negative_prompt"])
check("種類が分からない回は寸法も触らない", (got["width"], got["height"]) == (256, 512))

print("LoRA タグ")
ctx_lora = fresh(STRIP_LORA=True)
MOD.MARKS.kind, MOD.MARKS.func = "background", "generate_image_real_lcm"
got = ctx_lora.hooks[exit_target](
    Pipe.generate_image, pipe,
    prompt="masterpiece, outdoor, , <lora:LCM_LoRA_Weights_SD15:1>, best quality",
    width=1024, height=512)
check("タグを外す", "<lora:" not in got["prompt"], got["prompt"])
check("本文は残し、穴も塞ぐ", got["prompt"] == "masterpiece, outdoor, best quality",
      got["prompt"])
check("既定では外さない",
      "<lora:" in ctx.hooks[exit_target](
          Pipe.generate_image, pipe,
          prompt="a, <lora:X:1>, b", width=1024, height=512)["prompt"])

SDXL_RULES = {
    "lora_map": [{"enabled": True, "from": "LCM_LoRA_Weights_SD15",
                  "to": "LCM_LoRA_SDXL:1.0"}],
    "add": [{"enabled": True, "kind": "portrait", "target": "prompt",
             "text": "<lora:styleXL:0.6>"}],
}
sdxl_rules_dir = tempfile.mkdtemp(prefix="stable_diffusion_sdxl_rules_")
os.makedirs(os.path.join(sdxl_rules_dir, RB.STATE_DIRNAME))
io.open(RB.rules_path(sdxl_rules_dir), "w", encoding="utf-8").write(
    json.dumps(SDXL_RULES, ensure_ascii=False))
ctx_keep = fresh(STRIP_LORA=True, _state_dir=sdxl_rules_dir)
MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_real_lcm"
got = ctx_keep.hooks[exit_target](
    Pipe.generate_image, pipe,
    prompt="1boy, <lora:LCM_LoRA_Weights_SD15:1>, <lora:GameOnly:1>, solo",
    width=512, height=1024)
check("外すのはゲームが入れたタグだけ（付け替えた先と規則で足したものは残る）",
      got["prompt"] == "1boy, <lora:LCM_LoRA_SDXL:1.0>, solo, <lora:styleXL:0.6>",
      got["prompt"])

print("安全弁（SDXL）")
ctx_safe = fresh(_state_dir=sdxl_rules_dir)
MOD.FAMILY.update({"name": "sdxl", "path": "fake"})
try:
    MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_real_lcm"
    got = ctx_safe.hooks[exit_target](
        Pipe.generate_image, pipe,
        prompt="1boy, <lora:LCM_LoRA_Weights_SD15:1>, solo", width=512, height=1024)
    check("SDXL: 素のままの LCM の段は euler_a / 24 / 5.0 になる",
          (got["sample_method"], got["sample_steps"], got["cfg_scale"])
          == ("euler_a", 24, 5.0), got)
    check("SDXL: ゲームのタグは外し、規則で足したタグは残す",
          got["prompt"] == "1boy, <lora:LCM_LoRA_SDXL:1.0>, solo, <lora:styleXL:0.6>",
          got["prompt"])
    MOD.MARKS.kind, MOD.MARKS.func = "portrait", "image_to_image_anime"
    got = ctx_safe.hooks[exit_target](
        Pipe.generate_image, pipe, prompt="1boy, solo", width=512, height=1024)
    check("SDXL: 2段目（dpmpp2m / 15 / 7.0）は壊れないので触らない",
          got["sample_steps"] == 20 and got["cfg_scale"] == 7.0, got)
    ctx_safe_off = fresh(MODEL_SAFETY="off", _state_dir=sdxl_rules_dir)
    MOD.MARKS.kind, MOD.MARKS.func = "background", "generate_image_real_lcm"
    got = ctx_safe_off.hooks[exit_target](
        Pipe.generate_image, pipe, prompt="a, <lora:LCM_LoRA_Weights_SD15:1>, b",
        width=1024, height=512)
    check("off なら何もしない", "<lora:LCM_LoRA_SDXL" in got["prompt"]
          and got["sample_steps"] == 20, got)
finally:
    MOD.FAMILY.update({"name": None, "path": None})

ctx_off = fresh()
check("設定が全部既定なら1つも書き換えない",
      ctx_off.hooks[exit_target](Pipe.generate_image, pipe, prompt="p",
                                 width=256, height=512)["width"] == 256)
GAME_PROMPT = "full-body, masterpiece,high quality, detailed face, medieval"
check("規則が空ならプロンプトのカンマも均さない（ゲームの本文は空白無しで来る）",
      ctx_off.hooks[exit_target](
          Pipe.generate_image, pipe, prompt=GAME_PROMPT,
          width=256, height=512)["prompt"] == GAME_PROMPT)
check("規則が空なら rewrite は None",
      RB.rewrite(RB.empty(), "portrait", "prompt", GAME_PROMPT, GAME_PROMPT) is None)
check("当たる規則があるときは付け替えの穴を塞ぐ（tidy は残す）",
      RB.rewrite({"lora_map": [{"enabled": True, "from": "X", "to": "off"}]},
                 "portrait", "prompt", "a, <lora:X:1>, b", "a, <lora:X:1>, b")
      == "a, b")

# ---------------------------------------------------------------- 一族の差
print("diffusers 系")
cleanup(MANAGER, CREATURE)
ov = OVPipe()
fake_backend(pipe=ov)
ctx_ov = fresh(BACKGROUND_SHORT=768, BACKGROUND_MAX_LONG=1536,
               LCM_STEPS=8, LCM_CFG=2.0, LCM_METHOD="euler")
ov_target = "{}:OVPipe.__call__".format(OVPipe.__module__)
check("diffusers 系の出口も引けた", ov_target in ctx_ov.hooks, sorted(ctx_ov.hooks))
MOD.MARKS.kind, MOD.MARKS.func = "background", "generate_image_real_lcm"
got = ctx_ov.hooks[ov_target](OVPipe.__call__, ov, prompt="p", width=1024, height=512)
check("既定では寸法を触らない（作り直しで RAM を使い切るため）",
      (got["width"], got["height"]) == (1024, 512), got)
check("触らなかったと警告が出る",
      any(key == "diffusers-resize" for key, _ in ctx_ov.warnings), ctx_ov.warnings)

ctx_ov2 = fresh(BACKGROUND_SHORT=768, BACKGROUND_MAX_LONG=1536, DIFFUSERS_RESIZE=True)
MOD.MARKS.kind, MOD.MARKS.func = "background", "generate_image_real_lcm"
got2 = ctx_ov2.hooks[ov_target](OVPipe.__call__, ov, prompt="p", width=1024, height=512)
check("設定を入にすれば当たる", (got2["width"], got2["height"]) == (1536, 768), got2)
check("steps と cfg は名前を読み替えて当たる",
      got["num_inference_steps"] == 8 and got["guidance_scale"] == 2.0, got)
check("方式は当てられないと警告して落とす",
      any(key == "diffusers-sample_method" for key, _ in ctx_ov.warnings),
      ctx_ov.warnings)
ctx_ov3 = fresh(_state_dir=rules_dir)
MOD.MARKS.kind, MOD.MARKS.func = "enemy", "generate_image_real_lcm"
got3 = ctx_ov3.hooks[ov_target](OVPipe.__call__, ov, prompt="p",
                                negative_prompt="worst quality", width=512, height=512)
check("ネガティブの書き換えは diffusers 系にも同じ名前で届く",
      got3["negative_prompt"] == "worst quality, bad hands", got3)
check("届く項目に「当てられない」の警告を出さない",
      not any(key == "diffusers-negative_prompt" for key, _ in ctx_ov3.warnings),
      ctx_ov3.warnings)

# ---------------------------------------------------------------- 建つ前
print("建つ前の差し替え")
cleanup(MANAGER, CREATURE)
fresh(CHECKPOINT_PATH="runtime/models/sdxl/checkpoints/illustrious.safetensors",
      TAESD_PATH="runtime/models/sdxl/taesd/taesdxl.safetensors",
      LORA_DIR="runtime/models/sdxl/lora")
target, _ = fake_backend("image_generation.fake2.stable_diffusion_manager",
                         "image_generation.fake2.image_generation_creature")
observer()._after(target.__name__, target)
check("チェックポイントを差し替えた",
      target.model_path_anime.endswith("illustrious.safetensors"),
      target.model_path_anime)
check("TAESD を差し替えた", target.taesd_path.endswith("taesdxl.safetensors"),
      target.taesd_path)
check("LoRA の置き場を差し替えた", target.lora_dir == "runtime/models/sdxl/lora",
      target.lora_dir)
check("空のままの材料は触らない", target.vae_path.endswith("vae.safetensors"),
      target.vae_path)

print("建てるたびに書く（同じプロセスで2回目のワールド選択）")
cleanup("image_generation.fake2.stable_diffusion_manager",
        "image_generation.fake2.image_generation_creature")
again, _ = fake_backend("image_generation.fake2.stable_diffusion_manager",
                        "image_generation.fake2.image_generation_creature")
ctx_again = fresh(CHECKPOINT_PATH="runtime/models/sdxl/checkpoints/second.safetensors")
build_target = "image_generation.fake2.stable_diffusion_manager:load_sd_pipeline"
check("load_sd_pipeline を包んだ", build_target in ctx_again.hooks, sorted(ctx_again.hooks))
built = []
ctx_again.hooks[build_target](lambda: built.append(again.model_path_anime))
check("建てる直前に材料が書き換わっている（import は走っていない）",
      built == ["runtime/models/sdxl/checkpoints/second.safetensors"], built)

print("建つ前に出口を包む・建てている間の注入し直し（版26）")


class EarlySD(object):
    """sdcpp の manager がモジュール変数に持つクラスの代わり。"""

    def generate_image(self, prompt, negative_prompt="", width=512, height=512,
                       cfg_scale=7.0, scheduler="default", sample_method="euler_a",
                       sample_steps=20, seed=-1):
        return {"width": width, "height": height, "prompt": prompt}


class LatePipe(object):
    """建ってから初めて型が分かる出口（diffusers 系の `__call__`）。"""

    def __call__(self, prompt, negative_prompt="", width=512, height=512,
                 num_inference_steps=5, guidance_scale=1.0):
        return {"width": width, "height": height, "prompt": prompt}


EarlySD.__module__ = LatePipe.__module__ = "fake_sdcpp.stable_diffusion"
cleanup("image_generation.fake2.stable_diffusion_manager",
        "image_generation.fake2.image_generation_creature")
early_mod, _ = fake_backend("image_generation.fake5.stable_diffusion_manager",
                            "image_generation.fake5.image_generation_creature", pipe=None)
early_mod.txt2img_pipe = None
early_mod.StableDiffusion = EarlySD
ctx_early = fresh(PORTRAIT_SHORT=832, PORTRAIT_MAX_LONG=1664)
early_target = "fake_sdcpp.stable_diffusion:EarlySD.generate_image"
check("パイプラインが無くても manager のクラスから出口を包む", early_target in ctx_early.hooks,
      sorted(t for t in ctx_early.hooks if "fake_sdcpp" in t))
check("ありふれた __call__ はクラスからは包まない",
      not any(t.endswith(".__call__") for t in ctx_early.hooks if "fake_sdcpp" in t))
MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_anime"
got = ctx_early.hooks[early_target](EarlySD.generate_image, EarlySD(), prompt="p", width=256, height=512)
check("建つ前に包んだ出口でも寸法が当たる", (got["width"], got["height"]) == (448, 832), got)

ctx_new = fresh()                          # 建てている間に注入し直した
ctx_early.superseded = lambda: True
late_target = "fake_sdcpp.stable_diffusion:LatePipe.__call__"
early_build = "image_generation.fake5.stable_diffusion_manager:load_sd_pipeline"
ctx_early.hooks[early_build](lambda: setattr(early_mod, "txt2img_pipe", LatePipe()))
check("用済みの包みが建て終えたら、いちばん新しい apply() が出口を包む",
      late_target in ctx_new.hooks and late_target not in ctx_early.hooks,
      (late_target in ctx_new.hooks, late_target in ctx_early.hooks))
cleanup("image_generation.fake5.stable_diffusion_manager",
        "image_generation.fake5.image_generation_creature")

print("差し替えが届いていないと知らせる（既に建っている回）")


def only_fake(name):
    """偽のバックエンドを1つだけ残す。

    `attach()` は載っている manager を全部見るので、前の節の偽が残っていると
    警告が複数回数えられる（本物の `ctx.warner` は同じ鍵を1度しか出さないが、
    ここの偽 `Ctx` は素直に全部溜める）。
    """
    for key in [k for k in sys.modules
                if "image_generation.fake" in k or k in (MANAGER, CREATURE)]:
        sys.modules.pop(key, None)
    return fake_backend(name + ".stable_diffusion_manager",
                        name + ".image_generation_creature")


late, _ = only_fake("image_generation.fake6")
late.model_path_anime = sd15_path             # ゲームが建てた SD1.5
MOD.FAMILY.update({"name": None, "path": None})
ctx_late = fresh(CHECKPOINT_PATH=sdxl_path)   # 建った後に注入した形
gap = [message for key, message in ctx_late.warnings if key == "material-gap"]
check("既に建っているのに設定が別のモデルを指していたら警告する", len(gap) == 1, ctx_late.warnings)
check("警告に起動し直しと、選び直しでは駄目なことが出る",
      gap and "起動し直" in gap[0] and "選び直しても建て直らない" in gap[0], gap)
check("警告に両方の系統が出る（いま sd15 / 設定 sdxl）",
      gap and "sd15" in gap[0] and "sdxl" in gap[0], gap)

quiet, _ = only_fake("image_generation.fake7")
quiet.txt2img_pipe = None                     # まだ建っていない
ctx_quiet = fresh(CHECKPOINT_PATH=sdxl_path)
check("まだ建っていなければ警告しない（次に建つときに効く）",
      not any(key == "material-gap" for key, _ in ctx_quiet.warnings), ctx_quiet.warnings)
only_fake("image_generation.fake8")
ctx_same = fresh()
check("差し替えていなければ警告しない",
      not any(key == "material-gap" for key, _ in ctx_same.warnings), ctx_same.warnings)
for _name in ("image_generation.fake6", "image_generation.fake7", "image_generation.fake8"):
    cleanup(_name + ".stable_diffusion_manager", _name + ".image_generation_creature")
fake_backend()                                # 後ろの節が使う偽を戻す

print("用済みの apply() は当て直さない（包みが重ならない）")
cleanup("image_generation.fake5.stable_diffusion_manager",
        "image_generation.fake5.image_generation_creature")
stale_mod, _ = fake_backend("image_generation.fake5.stable_diffusion_manager",
                            "image_generation.fake5.image_generation_creature")
ctx_old = fresh(PORTRAIT_SHORT=832, PORTRAIT_MAX_LONG=1664)
old_build = "image_generation.fake5.stable_diffusion_manager:load_sd_pipeline"
check("生きている間は当て直す", old_build in ctx_old.hooks, sorted(ctx_old.hooks)[:3])
wrapped_before = len(ctx_old.hooks)
ctx_old.superseded = lambda: True
cleanup()                     # 印だけ戻す（モジュールは残す）
ctx_old.hooks[old_build](lambda: None)
check("用済みになったら load_sd_pipeline の包みからは当て直さない",
      len(ctx_old.hooks) == wrapped_before, (wrapped_before, len(ctx_old.hooks)))
check("用済みの apply() は建てる直前にも書かない",
      stale_mod.model_path_anime.endswith("sotemix_v30.safetensors"),
      stale_mod.model_path_anime)
cleanup("image_generation.fake5.stable_diffusion_manager",
        "image_generation.fake5.image_generation_creature")

print("用済みの観測者は書かない")
cleanup("image_generation.fake2.stable_diffusion_manager",
        "image_generation.fake2.image_generation_creature")
stale, _ = fake_backend("image_generation.fake2.stable_diffusion_manager",
                        "image_generation.fake2.image_generation_creature")
ctx_stale = fresh(CHECKPOINT_PATH="runtime/models/sdxl/checkpoints/stale.safetensors")
ctx_stale.superseded = lambda: True
observer()._after(stale.__name__, stale)
check("自分の apply() が用済みなら材料を触らない",
      stale.model_path_anime.endswith("sotemix_v30.safetensors"), stale.model_path_anime)
lib = types.ModuleType("diffusers.models.something")
observer()._after(lib.__name__, lib)
check("manager でないモジュールでは何もしない（例外も出ない）", not ctx_stale.errors)

cleanup("image_generation.fake2.stable_diffusion_manager",
        "image_generation.fake2.image_generation_creature")
fresh(DISABLE_TAESD=True)
target, _ = fake_backend("image_generation.fake3.stable_diffusion_manager",
                         "image_generation.fake3.image_generation_creature")
observer()._after(target.__name__, target)
check("TAESD を切ると空になる", target.taesd_path == "", target.taesd_path)

cleanup("image_generation.fake3.stable_diffusion_manager",
        "image_generation.fake3.image_generation_creature")
keep, _ = fake_backend("image_generation.fake4.stable_diffusion_manager",
                       "image_generation.fake4.image_generation_creature")
fresh()
observer()._after(keep.__name__, keep)
check("既定なら材料を触らない",
      keep.taesd_path.endswith("diffusion_pytorch_model.safetensors")
      and keep.model_path_anime.endswith("sotemix_v30.safetensors"), keep.taesd_path)

print("モデルの置き場とダウンロード")
import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("assets_under_test",
                                     os.path.join(MOD_DIR, "assets.py"))
AS = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(AS)

store = tempfile.mkdtemp(prefix="stable_diffusion_models_")
made = AS.ensure_dirs(store)
check("系統ごとに4つのフォルダを作る", len(made) == 8, made)
check(r"置き場は state\models\<系統>\<種類>",
      AS.dir_of(store, "sdxl", "taesd").endswith(os.path.join("models", "sdxl", "taesd")),
      AS.dir_of(store, "sdxl", "taesd"))
check("同じ並びを2回作っても増えない", AS.ensure_dirs(store) == [])

io.open(os.path.join(AS.dir_of(store, "sdxl", "checkpoints"), "x.safetensors"),
        "w").write("x")
io.open(os.path.join(AS.dir_of(store, "sdxl", "checkpoints"), "読まない.txt"),
        "w", encoding="utf-8").write("x")
check("モデルの拡張子だけ数える",
      AS.files_in(store, "sdxl", "checkpoints") == ["x.safetensors"],
      AS.files_in(store, "sdxl", "checkpoints"))
check("一番新しいものを絶対パスで引ける",
      AS.newest(store, "sdxl", "checkpoints").endswith("x.safetensors"))
check("無ければ空", AS.newest(store, "sd15", "taesd") == "")
check("置き場の説明が系統ごとに出る",
      len(AS.describe(store)) == 2 and "sdxl" in AS.describe(store)[1],
      AS.describe(store))


class _Response(object):
    """ダウンロードの応答の代わり（通信しない）。"""

    def __init__(self, body):
        self._body = body
        self._at = 0

    def read(self, size):
        chunk = self._body[self._at:self._at + size]
        self._at += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


dest = os.path.join(AS.dir_of(store, "sdxl", "taesd"), "taesdxl.safetensors")
ok, message = AS.download("https://例", dest,
                          opener=lambda _url, _timeout: _Response(b"x" * (2 * 1024 * 1024)))
check("ダウンロードすると置き場に入る", ok and os.path.isfile(dest), message)
check("書き途中のファイルを残さない", not os.path.isfile(dest + ".part"))

ok, message = AS.download("https://例", dest + "2",
                          opener=lambda _url, _timeout: _Response(b"<html>error</html>"))
check("小さすぎる応答は失敗にする", not ok and "小さすぎ" in message, message)
check("失敗したら置き場を汚さない", not os.path.isfile(dest + "2"))

ok, message = AS.download("https://例", dest + "3",
                          opener=lambda _url, _timeout: (_ for _ in ()).throw(IOError("切断")))
check("繋がらないときも例外を投げない", not ok and "ダウンロードできませんでした" in message, message)
check("手で入れる案内に URL と置き場と名前が出る",
      all(part in AS.manual_steps(AS.DOWNLOADS[("sdxl", "taesd")],
                                  AS.dir_of(store, "sdxl", "taesd"))
          for part in ("https://", "taesd", "diffusion_pytorch_model.safetensors")))

print("プリセット（SD1.5 / SDXL）")
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)          # presets は道具と同じく隣を素の名前で import する
import presets as PR  # noqa: E402

sd15_values, sd15_notes = PR.resolve("sd15", store)
check("SD1.5 プリセットは元の sd15.ini の値（832 / 1216、euler_a / 24 / 5.0）",
      sd15_values["PORTRAIT_SHORT"] == 832 and sd15_values["PORTRAIT_MAX_LONG"] == 1216
      and sd15_values["STAGE2_METHOD"] == "euler_a" and sd15_values["STAGE2_STEPS"] == 24
      and sd15_values["STAGE2_CFG"] == 5.0, sd15_values)
check("SD1.5 プリセットは材料と LoRA 除去を空に戻す",
      sd15_values["CHECKPOINT_PATH"] == "" and sd15_values["TAESD_PATH"] == ""
      and sd15_values["STRIP_LORA"] is False and sd15_values["LCM_METHOD"] == "lcm"
      and not sd15_notes, (sd15_values, sd15_notes))
sdxl_values, sdxl_notes = PR.resolve("sdxl", store)
check("SDXL プリセットは元の sdxl.ini の値（1024 / 2048、背景 704 / 1408）",
      sdxl_values["PORTRAIT_SHORT"] == 1024 and sdxl_values["PORTRAIT_MAX_LONG"] == 2048
      and sdxl_values["BACKGROUND_SHORT"] == 704 and sdxl_values["BACKGROUND_MAX_LONG"] == 1408,
      sdxl_values)
check("SDXL プリセットは実測で要ると分かった2つを足す（LoRA 除去と LCM の段）",
      sdxl_values["STRIP_LORA"] is True
      and (sdxl_values["LCM_METHOD"], sdxl_values["LCM_STEPS"], sdxl_values["LCM_CFG"])
      == ("euler_a", 24, 5.0), sdxl_values)
check("SDXL プリセットは置き場の一番新しいチェックポイントを指す（x.safetensors）",
      sdxl_values["CHECKPOINT_PATH"].endswith("x.safetensors"), sdxl_values["CHECKPOINT_PATH"])
check("SDXL プリセットは置き場の TAESD を指す（ダウンロードで置いたもの）",
      sdxl_values["TAESD_PATH"].endswith("taesdxl.safetensors"), sdxl_values["TAESD_PATH"])
empty_store = tempfile.mkdtemp(prefix="stable_diffusion_empty_")
sdxl_values, sdxl_notes = PR.resolve("sdxl", empty_store)
check("置き場に無ければ空のまま注記が出る",
      sdxl_values["CHECKPOINT_PATH"] == "" and len(sdxl_notes) == 2
      and all("無い" in note for note in sdxl_notes), sdxl_notes)
check("プリセットの値は全部 mod.json に宣言がある",
      all(name in MANIFEST["settings"] for name in sdxl_values),
      [name for name in sdxl_values if name not in MANIFEST["settings"]])

print("カスタムプリセット（版26）")
custom_dir = tempfile.mkdtemp(prefix="stable_diffusion_custom_")
check("無ければ空", PR.load_custom(custom_dir) == [])
ckpt = fake_checkpoint(["conditioner.embedders.1.model.x"], ".safetensors")
ok1 = PR.save_custom(custom_dir, "WAI 1段", {"CHECKPOINT_PATH": ckpt, "PORTRAIT_SHORT": 704,
                                            "STRIP_LORA": True}, now="t1")
ok2 = PR.save_custom(custom_dir, "SD1.5", {"CHECKPOINT_PATH": "", "PORTRAIT_SHORT": 832}, now="t2")
ok3 = PR.save_custom(custom_dir, "WAI 1段", {"CHECKPOINT_PATH": ckpt, "PORTRAIT_SHORT": 768,
                                            "STRIP_LORA": True}, now="t3")
rows = PR.load_custom(custom_dir)
check("名前を付けて残し、同じ名前は並びの位置を保って置き換える",
      ok1 and ok2 and ok3 and [r["name"] for r in rows] == ["WAI 1段", "SD1.5"]
      and rows[0]["values"]["PORTRAIT_SHORT"] == 768 and rows[0]["saved"] == "t3", rows)
check("型は保つ（数は数、真偽は真偽）",
      isinstance(rows[0]["values"]["PORTRAIT_SHORT"], int) and rows[0]["values"]["STRIP_LORA"] is True)
check("名前が空なら残さない", PR.save_custom(custom_dir, "  ", {}) is False)
got, notes = PR.custom_values(PR.find_custom(custom_dir, "WAI 1段"),
                              ["CHECKPOINT_PATH", "PORTRAIT_SHORT", "STRIP_LORA", "LCM_STEPS"])
check("いまの項目だけ入れ、無い項目はいまの値のまま（注記する）",
      set(got) == {"CHECKPOINT_PATH", "PORTRAIT_SHORT", "STRIP_LORA"}
      and any("LCM_STEPS" in note for note in notes), (got, notes))
got, notes = PR.custom_values({"name": "x", "values": {"OLD_KEY": 1, "CHECKPOINT_PATH": ckpt}},
                              ["CHECKPOINT_PATH"])
check("いまの版に無い項目は入れない（注記する）",
      got == {"CHECKPOINT_PATH": ckpt} and any("OLD_KEY" in note for note in notes), notes)
gone = os.path.join(custom_dir, "nowhere", "missing.safetensors")
got, notes = PR.custom_values({"name": "x", "values": {"CHECKPOINT_PATH": gone, "TAESD_PATH": ""}},
                              ["CHECKPOINT_PATH", "TAESD_PATH"])
check("見当たらないチェックポイントは注記する（入れることは止めない）",
      got["CHECKPOINT_PATH"] == gone and any("見つかりません" in note for note in notes), notes)
check("消す", PR.delete_custom(custom_dir, "SD1.5")
      and [r["name"] for r in PR.load_custom(custom_dir)] == ["WAI 1段"])
check("無い名前は消せない", PR.delete_custom(custom_dir, "SD1.5") is False)
io.open(PR.custom_path(custom_dir), "w", encoding="utf-8").write("{壊れた")
check("壊れたファイルは空として読む", PR.load_custom(custom_dir) == [])

print("元 MOD の ini の取り込み")
_spec = _ilu.spec_from_file_location("iniimport_under_test",
                                     os.path.join(MOD_DIR, "iniimport.py"))
INI = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(INI)

SAMPLE = """
; コメント行は読まない（元 MOD と同じ扱い）
[upscale]
enabled=1
goal_short=832
max_long=1216
round=64
goal_short_landscape=704
max_long_landscape=1408
;enabled_square=0
skip_if = pixel art/sprite
skip_if_portrait = chibi

[lora_map]
LCM_LoRA_Weights_SD15 = off
oldStyle = newStyle:0.7

[lora_add]
portrait  = <lora:myChar:0.7>, masterpiece
landscape = scenery

[negative_add]
portrait = bad hands

[lora_add_if]
rule1 = portrait | 1boy/male/man | <lora:maleStyle:0.8>
rule2 = square | !boss | <lora:mob:0.5>

[prompt_remove]
portrait = medieval, watercolor

[prompt_replace]
landscape = cyberpunk, {prompt}

[sampler]
portrait_method = dpm++2mv2
portrait_steps = 24
portrait_cfg = 5.0
landscape_method = euler_a
landscape_steps = 20
"""

settings, imported, notes = INI.convert(INI.parse(SAMPLE))
check("解像度が種類ごとに写る",
      settings["PORTRAIT_SHORT"] == 832 and settings["PORTRAIT_MAX_LONG"] == 1216
      and settings["BACKGROUND_SHORT"] == 704 and settings["BACKGROUND_MAX_LONG"] == 1408
      and settings["ENEMY_SHORT"] == 832, settings)
check("サンプラーの名前を読み替える（dpm++2mv2 -> dpmpp2mv2）",
      settings["STAGE1_METHOD"] == "dpmpp2mv2" and settings["STAGE2_METHOD"] == "dpmpp2mv2",
      settings.get("STAGE1_METHOD"))
check("portrait のサンプラーは1段目と2段目の両方へ",
      settings["STAGE1_STEPS"] == 24 and settings["STAGE2_STEPS"] == 24)
check("landscape のサンプラーは LCM の段へ",
      settings["LCM_METHOD"] == "euler_a" and settings["LCM_STEPS"] == 20)
check("コメントアウトされた行は読まない（enabled_square=0 は効かない）",
      "ENEMY_SHORT" in settings)

check("付け替えは元の綴りのまま",
      imported["lora_map"][0]["from"] == "LCM_LoRA_Weights_SD15",
      imported["lora_map"])
check("種類の対応（landscape は背景、square は敵）",
      any(r["kind"] == "background" and r["text"] == "scenery" for r in imported["add"])
      and any(r["kind"] == "enemy" for r in imported["add"] if r.get("when")),
      imported["add"])
check("ネガティブ側は target で分かれる",
      any(r["target"] == "negative" and r["text"] == "bad hands" for r in imported["add"]))
check("条件つきの行は when と名前を持つ",
      any(r.get("when") == "1boy/male/man" and r.get("name") == "rule1"
          for r in imported["add"]), imported["add"])
check("除去と置き換え",
      imported["remove"][0]["text"] == "medieval, watercolor"
      and imported["replace"][0]["text"] == "cyberpunk, {prompt}")
check("skip は全体と種類別の両方",
      sorted(r["kind"] for r in imported["skip_upscale"]) == ["any", "portrait"],
      imported["skip_upscale"])
check("読み替えの注記が出る", any("1段目と2段目" in note for note in notes), notes)
check("取り込んだ規則がそのまま使える",
      RB.rewrite(imported, "portrait", "prompt",
                 "solo, medieval, 1boy, watercolor",
                 "solo, medieval, 1boy, watercolor")
      == "solo, 1boy, <lora:myChar:0.7>, masterpiece, <lora:maleStyle:0.8>",
      RB.rewrite(imported, "portrait", "prompt", "solo, medieval, 1boy, watercolor",
                 "solo, medieval, 1boy, watercolor"))

print("道具画面の固定行（窓抜き）")
# 固定行に欄の無い種類の行は画面に出ず、保存の組み直しで落ちる。
# ini の `any = …` は kind=any の行になるので、any の欄が要る。
_spec = _ilu.spec_from_file_location("stable_diffusion_tool_under_test",
                                     os.path.join(MOD_DIR, "tool.py"))
TOOL = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(TOOL)
_rule_kinds = [kind for kind, _label in TOOL.RULE_LABELS]
check("追加・除去・置き換えの固定行は規則の全種類を持つ",
      sorted(_rule_kinds) == sorted(RB.KINDS), _rule_kinds)
_, any_rules, _ = INI.convert(INI.parse(
    "[negative_add]\nany = bad hands\n[prompt_remove]\nany = nsfw\n"
    "[prompt_replace]\nany = {prompt}, x\n"))
_shown = [(section, row["text"]) for section in ("add", "remove", "replace")
          for row in any_rules[section]
          if row["kind"] in _rule_kinds
          and TOOL.find_row(any_rules[section], section, row["kind"], row["target"]) is row]
check("取り込んだ any の行は固定行に出る（保存で消えない）",
      sorted(_shown) == [("add", "bad hands"), ("remove", "nsfw"),
                         ("replace", "{prompt}, x")], _shown)

print("モデルで絞る（版26）")
NOVA = ("sdxl", os.path.join("state", "models", "sdxl", "checkpoints", "novaAnimeXL_ilV190.safetensors"))
PONY = ("sdxl", os.path.join("x", "ponyDiffusionV6XL.safetensors"))
SOTE = ("sd15", os.path.join("runtime", "models", "sd15", "checkpoints", "sotemix_v30.safetensors"))
check("空のモデルの条件はいつも成立", RB.model_matches("", SOTE) is True)
check("sdxl は系統で比べる", RB.model_matches("sdxl", NOVA) and not RB.model_matches("sdxl", SOTE))
check("ほかの語はファイル名の部分一致（大文字小文字を問わない）",
      RB.model_matches("novaanime", NOVA) and not RB.model_matches("novaanime", PONY))
check("! で除く", RB.model_matches("sdxl/!pony", NOVA) and not RB.model_matches("sdxl/!pony", PONY))
check("系統が読めない回は sdxl に当たらない", RB.model_matches("sdxl", (None, None)) is False)
check("系統が読めない回も ! だけの条件は成立", RB.model_matches("!sdxl", (None, None)) is True)
check("拡張子は比べない", RB.model_matches("safetensors", NOVA) is False)
MODEL_RULES = {
    "add": [{"enabled": True, "kind": "any", "target": "prompt", "model": "sdxl", "text": "xl"},
            {"enabled": True, "kind": "any", "target": "prompt", "text": "all"}],
    "replace": [{"enabled": True, "kind": "portrait", "target": "prompt",
                 "when": "woman", "text": "1girl, {prompt}"}],
}
check("モデルの付いた行はそのモデルのときだけ",
      RB.rewrite(MODEL_RULES, "enemy", "prompt", "a", "a", NOVA) == "a, xl, all"
      and RB.rewrite(MODEL_RULES, "enemy", "prompt", "a", "a", SOTE) == "a, all")
check("モデルを渡さなければ絞らない（道具画面の一覧）",
      RB.rewrite(MODEL_RULES, "enemy", "prompt", "a", "a") == "a, xl, all")
check("置換にも when が効く",
      RB.rewrite(MODEL_RULES, "portrait", "prompt", "young woman", "young woman", SOTE)
      == "1girl, young woman, all"
      and RB.rewrite(MODEL_RULES, "portrait", "prompt", "young man", "young man", SOTE)
      == "young man, all")
check("skip もモデルで絞る",
      RB.skip_upscale({"skip_upscale": [{"enabled": True, "kind": "any", "model": "sd15",
                                         "when": "sprite"}]}, "portrait", "sprite", NOVA) is False)

print("プロンプトの雛形（版26）")
import profiles as PF  # noqa: E402
GAME_PORTRAIT = ("full-body, masterpiece,high quality, detailed face, watercolor, standing,"
                 " middle-aged man, graying hair, empty background,black background,wallpaper")
GAME_BG = "masterpiece, best quality, watercolor, highly detailed, indoor, dark stone prison cell"
check("雛形の行の項目は規則の書式に在る",
      all(set(row) <= set(RB.SECTIONS[section]) | {"enabled", "profile"}
          for profile in PF.PROFILES for section, row in PF.rows_of(profile["key"], "m")),
      [(section, row) for profile in PF.PROFILES for section, row in PF.rows_of(profile["key"], "m")
       if not set(row) <= set(RB.SECTIONS[section]) | {"enabled", "profile"}])
check("モデルの条件はチェックポイントのファイル名（無ければ sdxl）",
      PF.model_condition(NOVA[1]) == "novaAnimeXL_ilV190" and PF.model_condition("") == "sdxl")
ill = PF.put(RB.empty(), "illustrious", "novaAnimeXL_ilV190")
check("入れ直しても行は増えない",
      PF.put(ill, "illustrious", "novaAnimeXL_ilV190") == ill)
both = PF.put(ill, "illustrious", "otherXL")
check("別のモデルに入れた同じ雛形は残る",
      len(both["add"]) == 2 * len(ill["add"]), (len(both["add"]), len(ill["add"])))
check("元の規則は書き換えない", RB.empty()["add"] == [])
got = RB.rewrite(ill, "portrait", "prompt", GAME_PORTRAIT, GAME_PORTRAIT, NOVA)
check("Illustrious: 画質の印が頭に来る",
      got.startswith("masterpiece, best quality, amazing quality, very aesthetic, absurdres, full body, 1boy"),
      got)
check("Illustrious: wallpaper と empty background と full-body を外す",
      all(tag not in RB.split_tags(got) for tag in ("wallpaper", "empty background", "full-body",
                                                   "high quality")), got)
check("Illustrious: 画材のタグは括弧を逃がして足す", got.endswith(r"watercolor \(medium\)"), got)
got = RB.rewrite(ill, "portrait", "prompt", "young woman, watercolor", "young woman, watercolor", NOVA)
check("Illustrious: 女性は 1girl（1boy は付かない）", "1girl" in got and "1boy" not in got, got)
got = RB.rewrite(ill, "background", "prompt", GAME_BG, GAME_BG, NOVA)
check("Illustrious: 背景に no humans, scenery、indoor は indoors に",
      "no humans, scenery" in got and "indoors" in RB.split_tags(got)
      and "indoor" not in RB.split_tags(got), got)
check("Illustrious: 別のモデルでは当たらない",
      RB.rewrite(ill, "portrait", "prompt", GAME_PORTRAIT, GAME_PORTRAIT, SOTE) is None)
pony = PF.put(RB.empty(), "pony", "ponyDiffusionV6XL")
got = RB.rewrite(pony, "portrait", "prompt", GAME_PORTRAIT, GAME_PORTRAIT, PONY)
check("Pony: 画質の印は score_9 の形", got.startswith("score_9, score_8_up, score_7_up, source_anime"), got)
got = RB.rewrite(pony, "enemy", "negative", "worst quality", "worst quality", PONY)
check("Pony: 否定に score_6 以下", "score_6, score_5, score_4" in got, got)

print("雛形がフック越しに効く（全文は1回だけ出す）")
prof_dir = tempfile.mkdtemp(prefix="stable_diffusion_profile_")
os.makedirs(os.path.join(prof_dir, RB.STATE_DIRNAME))
RB.save(prof_dir, ill)
ctx_prof = fresh(_state_dir=prof_dir)
MOD.FAMILY.update({"name": "sdxl", "path": NOVA[1]})
try:
    MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_anime"
    got = ctx_prof.hooks[exit_target](Pipe.generate_image, pipe, prompt=GAME_PORTRAIT,
                                      width=256, height=512)
    check("出口で雛形が当たる", got["prompt"].startswith("masterpiece, best quality"), got["prompt"])
    ctx_prof.hooks[exit_target](Pipe.generate_image, pipe, prompt=GAME_PORTRAIT,
                                width=256, height=512)
    shown = [line for line in ctx_prof.notes if line.startswith("  prompt [portrait/stage1] 前:")]
    check("書き換えの前後の全文は種類と段ごとに1回だけ", len(shown) == 1, shown)
    MOD.FAMILY.update({"name": "sd15", "path": SOTE[1]})
    got = ctx_prof.hooks[exit_target](Pipe.generate_image, pipe, prompt=GAME_PORTRAIT,
                                      width=256, height=512)
    check("SD1.5 に戻すと雛形は当たらない", got["prompt"] == GAME_PORTRAIT, got["prompt"])
finally:
    MOD.FAMILY.update({"name": None, "path": None})

print("道具画面のモデル別の行（窓抜き）")
mixed = PF.put({"add": [{"enabled": True, "kind": "portrait", "target": "prompt", "text": "plain"}],
                "remove": [], "replace": [], "skip_upscale": [], "lora_map": []},
               "illustrious", "novaAnimeXL_ilV190")
check("固定行はモデル別の行を拾わない",
      TOOL.find_row(mixed["add"], "add", "portrait", "prompt")["text"] == "plain")
check("固定行は when 付きの置換を拾わない",
      TOOL.find_row(ill["replace"], "replace", "portrait", "prompt") is None)
rows = TOOL.model_rows(mixed)
check("モデル別のタブは雛形の行だけを持つ",
      len(rows) == sum(len(ill[name]) for name in TOOL.MODEL_TAB_SECTIONS)
      and all(row["section"] in TOOL.MODEL_TAB_SECTIONS for row in rows), len(rows))
back = TOOL.split_model_rows(rows)
check("タブの行は節へ戻せる（印も残る）",
      all(back[name] == [row for row in mixed[name] if TOOL.by_model(row)]
          for name in TOOL.MODEL_TAB_SECTIONS))

print("語の置き換えと Danbooru のタグとの照合（版26）")
import tagbook as TB  # noqa: E402
TAG_CSV = "\n".join([
    'plate,0,44749,"dish,plates"',
    'plate_armor,0,2580,',
    'armor,0,210293,armour',
    'outdoors,0,537892,"outdoor,outside"',
    'watercolor_(medium),5,14950,"watercolor,watercolour"',
    'wolf,0,90000,wolves',
    'black_background,0,80815,/blackbg',
    'standing,0,900000,',
    'shengtian,4,1000,quiet',
    'some_artist,1,500,ink',
])
BOOK = TB.parse(io.StringIO(TAG_CSV))
check("一般とメタのタグだけを数える（絵師とキャラは外す）",
      "plate" in BOOK.tags and "watercolor_(medium)" in BOOK.tags
      and "shengtian" not in BOOK.tags and "some_artist" not in BOOK.tags)
check("キャラの別名は通さない（quiet はタグにならない）", BOOK.lookup("quiet") is None)
check("別名は正しいタグへ", BOOK.lookup("Outdoor") == ("alias", "outdoors"))
check("空白と下線と括弧の逃がしを同じに見る",
      BOOK.lookup(r"watercolor \(medium\)") == ("tag", "watercolor_(medium)")
      and BOOK.lookup("plate armor") == ("tag", "plate_armor"))
check("書く形は空白と逃がした括弧", TB.display("watercolor_(medium)") == r"watercolor \(medium\)")
check("紛れた語は複数語の言い方の中のよく使われるタグだけ",
      BOOK.hidden("battle-worn plate mail") == ["plate"] and BOOK.hidden("plate") == [])

WORDS = [("plate mail", "plate armor"), ("chain mail", "chainmail")]
check("語句を区画の中で置き換える（大文字小文字を問わない）",
      RB.replace_words("young man, battle-worn Plate Mail, grim", WORDS)
      == "young man, battle-worn plate armor, grim")
check("単語境界つき（breastplate mail には当たらない）",
      RB.replace_words("breastplate mail", WORDS) == "breastplate mail")
check("置換先が空なら語句を消し、空の区画は括弧の対応を保って落とす",
      RB.replace_words("a, (plate mail:1.2), b", [("plate mail", "")]) == "a, b"
      and RB.replace_words("(x, plate mail:1.2), b", [("plate mail", "")]) == "(x:1.2), b",
      (RB.replace_words("a, (plate mail:1.2), b", [("plate mail", "")]),
       RB.replace_words("(x, plate mail:1.2), b", [("plate mail", "")])))
check("当たらなければ本文をそのまま返す（カンマを均さない）",
      RB.replace_words("a,b", WORDS) == "a,b")
check("LoRA のタグは触らない",
      RB.replace_words("<lora:plate mail:1>", WORDS) == "<lora:plate mail:1>")
ENEMY = "full body,creature,(wolf:1.1), a huge grey dire wolf,(plain background,black background:1.1), <lora:X:1>"
check("alias は別名だけ直す",
      RB.check_tags("outdoor, watercolor, dark foggy path", BOOK, "alias")
      == r"outdoors, watercolor \(medium\), dark foggy path")
check("alias はタグでない区画を外さず、括弧と LoRA も保つ",
      RB.check_tags("outdoor," + ENEMY, BOOK, "alias") == "outdoors, " + ENEMY.replace(",", ", ").replace(",  ", ", "),
      RB.check_tags("outdoor," + ENEMY, BOOK, "alias"))
check("drop は外した（知らない方法の行は何もしない）",
      "drop" not in RB.TAG_MODES and RB.check_tags("outdoor," + ENEMY, BOOK, "drop") == "outdoor," + ENEMY)
check("一覧が無ければ何もしない", RB.check_tags("outdoor", None, "alias") == "outdoor")
check("紛れた語の知らせ",
      RB.tag_notes("standing, battle-worn plate mail", BOOK) == [("battle-worn plate mail", ["plate"])])
TAG_RULES = {"words": [{"enabled": True, "kind": "any", "target": "prompt",
                        "from": "plate mail", "to": "plate armor"}],
             "tags": [{"enabled": True, "kind": "any", "target": "prompt", "mode": "alias",
                       "model": "sdxl"}],
             "replace": [{"enabled": True, "kind": "any", "target": "prompt",
                          "text": "masterpiece, very aesthetic, outdoor, {prompt}"}]}
got = RB.rewrite(TAG_RULES, "portrait", "prompt", "standing, outdoor, battle-worn plate mail",
                 "standing, outdoor, battle-worn plate mail", NOVA, BOOK)
check("照合はゲームの本文だけ（規則が足す語は直さない）",
      got == "masterpiece, very aesthetic, outdoor, standing, outdoors, battle-worn plate armor", got)
got = RB.rewrite(TAG_RULES, "portrait", "prompt", "standing, outdoor", "standing, outdoor", SOTE, BOOK)
check("照合の行もモデルで絞る（SD1.5 では直さない）",
      got == "masterpiece, very aesthetic, outdoor, standing, outdoor", got)
got = RB.rewrite(TAG_RULES, "portrait", "prompt", "standing, outdoor", "standing, outdoor", NOVA, None)
check("一覧が無ければ照合は飛ばす", got.endswith("standing, outdoor"), got)
ill_tags = PF.put(RB.empty(), "illustrious", "novaAnimeXL_ilV190")
check("Illustrious の雛形は語の置き換えを持ち、照合（alias）は持たない",
      [r["from"] for r in ill_tags["words"]] == ["plate mail", "chain mail"]
      and ill_tags["tags"] == [])
got = RB.rewrite(ill_tags, "portrait", "prompt", GAME_PORTRAIT + ", battle-worn plate mail",
                 GAME_PORTRAIT + ", battle-worn plate mail", NOVA, BOOK)
check("雛形で plate mail が plate armor になる", "battle-worn plate armor" in got
      and "plate mail" not in got, got)

print("タグの照合がフック越しに効く（知らせは1回だけ）")
tag_dir = tempfile.mkdtemp(prefix="stable_diffusion_tags_")
os.makedirs(os.path.join(tag_dir, RB.STATE_DIRNAME))
hook_rules = dict(ill_tags, tags=[{"enabled": True, "kind": "any", "target": "prompt",
                                   "mode": "alias", "model": "sdxl"}])
RB.save(tag_dir, hook_rules)
io.open(TB.path_of(tag_dir), "w", encoding="utf-8").write(TAG_CSV)
ctx_tags = fresh(_state_dir=tag_dir)
MOD.FAMILY.update({"name": "sdxl", "path": NOVA[1]})
try:
    MOD.MARKS.kind, MOD.MARKS.func = "enemy", "generate_image_real_lcm"
    for _ in range(2):
        got = ctx_tags.hooks[exit_target](
            Pipe.generate_image, pipe, prompt="outdoor, battle-worn plate mail, a big wolf pack",
            width=512, height=512)
    check("出口で語の置き換えと alias が当たる",
          "outdoors" in got["prompt"] and "plate armor" in got["prompt"], got["prompt"])
    notes = [line for line in ctx_tags.notes if line.startswith("danbooru:")]
    check("タグに無い言い方の知らせは言い方ごとに1回", len(notes) == 2, notes)
finally:
    MOD.FAMILY.update({"name": None, "path": None})
words_dir = tempfile.mkdtemp(prefix="stable_diffusion_words_")
os.makedirs(os.path.join(words_dir, RB.STATE_DIRNAME))
RB.save(words_dir, ill_tags)                 # 雛形のまま（語の置き換えだけ、照合なし）
io.open(TB.path_of(words_dir), "w", encoding="utf-8").write(TAG_CSV)
ctx_words = fresh(_state_dir=words_dir)
MOD.FAMILY.update({"name": "sdxl", "path": NOVA[1]})
try:
    MOD.MARKS.kind, MOD.MARKS.func = "enemy", "generate_image_real_lcm"
    got = ctx_words.hooks[exit_target](
        Pipe.generate_image, pipe, prompt="outdoor, battle-worn plate mail, a big wolf pack",
        width=512, height=512)
    check("雛形のままなら置き換えだけ当たり、別名は直さない",
          "plate armor" in got["prompt"] and "outdoors" not in got["prompt"], got["prompt"])
    check("置き換えだけの回もタグに無い言い方を知らせる",
          len([line for line in ctx_words.notes if line.startswith("danbooru:")]) == 2, ctx_words.notes)
finally:
    MOD.FAMILY.update({"name": None, "path": None})
os.remove(TB.path_of(words_dir))
ctx_words2 = fresh(_state_dir=words_dir)
MOD.FAMILY.update({"name": "sdxl", "path": NOVA[1]})
try:
    MOD.MARKS.kind, MOD.MARKS.func = "enemy", "generate_image_real_lcm"
    got = ctx_words2.hooks[exit_target](Pipe.generate_image, pipe, prompt="battle-worn plate mail",
                                        width=512, height=512)
    check("一覧が無くても置き換えは当たり、無いという知らせは出さない",
          "plate armor" in got["prompt"]
          and not any(key == "tagbook-missing" for key, _m in ctx_words2.warnings), ctx_words2.warnings)
finally:
    MOD.FAMILY.update({"name": None, "path": None})
os.remove(TB.path_of(tag_dir))
ctx_none = fresh(_state_dir=tag_dir)
MOD.FAMILY.update({"name": "sdxl", "path": NOVA[1]})
try:
    MOD.MARKS.kind, MOD.MARKS.func = "enemy", "generate_image_real_lcm"
    got = ctx_none.hooks[exit_target](Pipe.generate_image, pipe, prompt="outdoor",
                                      width=512, height=512)
    check("一覧が無ければ alias は当たらず、知らせが出る",
          "outdoors" not in got["prompt"]
          and any(key == "tagbook-missing" for key, _m in ctx_none.warnings), ctx_none.warnings)
finally:
    MOD.FAMILY.update({"name": None, "path": None})

print("検証の種の印（版26）")
ctx_seed = fresh()
MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_anime"
MOD.MARKS.seed = 1234
try:
    got = ctx_seed.hooks[exit_target](Pipe.generate_image, pipe, prompt="p", width=256, height=512)
    check("種の印があればその種で描く", got.get("seed") == 1234, got)
finally:
    MOD.MARKS.seed = None
got = ctx_seed.hooks[exit_target](Pipe.generate_image, pipe, prompt="p", width=256, height=512, seed=-1)
check("印が無ければゲームの種のまま", got.get("seed", -1) == -1, got)

print("生成のたびに読み直す（ホットリロード）")
from instantale_modloader import config as _config  # noqa: E402

cleanup(MANAGER, CREATURE)
fake_backend()
ctx_hot = fresh()
hot_hook = ctx_hot.hooks[exit_target]
check("場所が控えられていれば読み直しが効く状態", ctx_hot.notes and not any(
    "hot reload: off" in note for note in ctx_hot.notes), ctx_hot.notes[-3:])
MOD.MARKS.kind, MOD.MARKS.func = "portrait", "generate_image_anime"
got = hot_hook(Pipe.generate_image, pipe, prompt="p", width=256, height=512)
check("最初は既定のまま", (got["width"], got["height"]) == (256, 512), got)

store = _config.store_path(ctx_hot.runtime_dir)
os.makedirs(os.path.dirname(store), exist_ok=True)
io.open(store, "w", encoding="utf-8").write(json.dumps(
    {"343_stable_diffusion": {"PORTRAIT_SHORT": 832, "PORTRAIT_MAX_LONG": 1664,
                           "STAGE1_STEPS": "28"}}))
got = hot_hook(Pipe.generate_image, pipe, prompt="p", width=256, height=512)
check("設定ファイルを書き換えると次の生成から効く（注入し直し不要）",
      (got["width"], got["height"]) == (448, 832), got)
check("値はローダの型変換を通る（文字列の '28' が int に）",
      got["sample_steps"] == 28, got["sample_steps"])
check("読み直したとログに出る", any(note.startswith("reloaded settings") for note in ctx_hot.notes),
      ctx_hot.notes[-2:])

notes_before = len(ctx_hot.notes)
hot_hook(Pipe.generate_image, pipe, prompt="p", width=256, height=512)
check("変わっていなければ読み直さない（ログも増えない）",
      not any(note.startswith("reloaded") for note in ctx_hot.notes[notes_before:]))

io.open(store, "w", encoding="utf-8").write(json.dumps(
    {"343_stable_diffusion": {"PORTRAIT_SHORT": 832, "PORTRAIT_MAX_LONG": 1664,
                           "STAGE1_STEPS": "not a number"}}))
got = hot_hook(Pipe.generate_image, pipe, prompt="p", width=256, height=512)
check("読めない値は既定に倒れ、他の項目は生きたまま",
      got["sample_steps"] == 20 and (got["width"], got["height"]) == (448, 832), got)

os.remove(store)
got = hot_hook(Pipe.generate_image, pipe, prompt="p", width=256, height=512)
check("設定ファイルが消えれば既定に戻る", (got["width"], got["height"]) == (256, 512), got)

rules_live = RB.rules_path(ctx_hot.state_dir)
os.makedirs(os.path.dirname(rules_live), exist_ok=True)
io.open(rules_live, "w", encoding="utf-8").write(json.dumps(
    {"add": [{"enabled": True, "kind": "any", "target": "prompt", "text": "hot"}]}))
got = hot_hook(Pipe.generate_image, pipe, prompt="p", width=256, height=512)
check("規則も次の生成から効く", got["prompt"] == "p, hot", got["prompt"])
os.remove(rules_live)
got = hot_hook(Pipe.generate_image, pipe, prompt="p", width=256, height=512)
check("規則のファイルが消えれば何もしない", got["prompt"] == "p", got["prompt"])
check("読み直しで例外が出ていない", not ctx_hot.errors, ctx_hot.errors)

print("積み上げ")
before = len([f for f in sys.meta_path if getattr(f, MOD.OBSERVER_MARK, None)])
fresh()
fresh()
after = len([f for f in sys.meta_path if getattr(f, MOD.OBSERVER_MARK, None)])
check("注入し直しても観測者は1つ", before == 1 and after == 1, (before, after))
check("関係の無い import は素通し（spec を作らない）",
      observer().find_spec("json") is None)
check("フックの中で例外が出ていない", not ctx.errors, ctx.errors)

print("用済みの観測者は包まない")
# 観測者を外すのは次の apply() だけ。343 を切って注入し直しても残るので、
# 用済みになった後は名前が合うモジュールでも他の finder に聞かず素通しする。
import importlib.machinery  # noqa: E402


class _Finder(object):
    calls = []

    def find_spec(self, name, path=None, target=None):
        if not name.startswith("diffusers.probe_"):
            return None
        _Finder.calls.append(name)
        loader = types.SimpleNamespace(create_module=lambda spec: None,
                                       exec_module=lambda module: None)
        return importlib.machinery.ModuleSpec(name, loader)


finder = _Finder()
sys.meta_path.append(finder)
try:
    ctx_live = fresh()
    spec = observer().find_spec("diffusers.probe_live")
    check("生きている間は名前が合うモジュールのローダを包む",
          spec is not None and isinstance(spec.loader, MOD._LoaderProxy), spec)
    ctx_live.superseded = lambda: True
    del _Finder.calls[:]
    check("用済みになったら包まない", observer().find_spec("diffusers.probe_stale") is None)
    check("用済みになったら他の finder にも聞かない", _Finder.calls == [], _Finder.calls)
finally:
    sys.meta_path.remove(finder)

cleanup("image_generation.fake4.stable_diffusion_manager",
        "image_generation.fake4.image_generation_creature")

print("\n失敗 {} 件".format(len(failures)))
if failures:
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
