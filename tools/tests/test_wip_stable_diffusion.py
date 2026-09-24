# -*- coding: utf-8 -*-
"""916_stable_diffusion をゲーム抜きで通す。

    python tools/tests/test_wip_stable_diffusion.py

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

MOD_DIR = os.path.join(MODS_DIR, "916_stable_diffusion")
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
                "negative_prompt": negative_prompt}


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
    {"916_stable_diffusion": {"PORTRAIT_SHORT": 832, "PORTRAIT_MAX_LONG": 1664,
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
    {"916_stable_diffusion": {"PORTRAIT_SHORT": 832, "PORTRAIT_MAX_LONG": 1664,
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
# 観測者を外すのは次の apply() だけ。916 を切って注入し直しても残るので、
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
