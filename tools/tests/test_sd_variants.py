# -*- coding: utf-8 -*-
"""343_stable_diffusion の表情差分（variants.py と出口・入口の流れ）をゲーム抜きで通す。

    python tools/tests/test_sd_variants.py

  設定   … 既定は強さ 0.35 と4つのタグ。空のタグの表情は描かない。壊れた強さは既定へ
           強さはモデルごとに変える行、無ければ系統で SD1.5（既定 0.5）か SDXL・その他（0.35）
           顔を描くモデルの決まり方（道具画面の表示）は本体と同じ順
  文     … 顔の寄りのタグの直後に表情のタグを入れる
  順番   … ゲームの絵は差分より先。差分はゲームが待っていれば譲る。同じスレッドは入り直せる
  工程   … 1枚ずつ新しいスレッドで通す（`131_` の控えを持ち込まない）
  流れ   … 表情を見せる MOD が宣言していれば、立ち絵の入口を抜けた後に差分を描いて置く（PIL が要る）
           一括生成のあいだは入口の中で描き終える。宣言が無い・主人公なら描かない。途中で描き直されたら印を書かない
           一括生成の後に、立ち絵が既にあって印の無い人物へ差分だけ描く（控えた呼び方と prompts.json で）
           一括生成の印と工程の控えは当て直しをまたぐ
顔を探す処理はゲームの中でしか通らないので、ここでは決まった枠を返す偽物にする。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402
from instantale_modloader import expressions  # noqa: E402

MOD_DIR = os.path.join(MODS_DIR, "343_stable_diffusion")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    MANIFEST = json.load(fh)
spec = importlib.util.spec_from_file_location(
    "stable_diffusion_variants_under_test", os.path.join(MOD_DIR, MANIFEST["entry"]),
    submodule_search_locations=[MOD_DIR])
MOD = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = MOD
spec.loader.exec_module(MOD)
V = MOD.variants

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 設定
print("設定")
decls = MANIFEST["settings"]
check("mod.json に強さと4つのタグがあり、既定はコードと同じ",
      all(name in decls for name in V.NAMES)
      and decls[V.STRENGTH]["default"] == V.DEFAULT_STRENGTH == MOD.EXPRESSION_STRENGTH == 0.35
      and all(decls[name]["default"] == V.DEFAULT_TAGS[e] for e, name in V.TAGS))
check("表情は窓口の差分の一覧と同じ並び", tuple(e for e, _n in V.TAGS) == expressions.VARIANTS)
again = importlib.util.module_from_spec(spec)
spec.loader.exec_module(again)
check("一括生成の印と工程の控えは、モジュールを作り直しても（当て直し）同じ物を使う",
      again.BULK is MOD.BULK and again.STEP_CALLS is MOD.STEP_CALLS and MOD.BULK is V.shared())
from instantale_modloader import patch as loader_patch  # noqa: E402


def layer(below, module):
    """ローダの包みの代わり（`__original__` と、フックを書いたモジュールの印）。"""
    def wrapper(*a, **kw):
        return below(*a, **kw)
    wrapper.__original__ = below
    setattr(wrapper, loader_patch.PATCH_MARK, "m:StableDiffusion.generate_image")
    setattr(wrapper, loader_patch.SELF_MARK, wrapper)
    setattr(wrapper, loader_patch.HOOK_MODULE_MARK, module)
    return wrapper


def plain_generate(self, prompt=""):
    return prompt


lower = layer(plain_generate, "mods.other.lower")
ours = layer(lower, MOD.__name__)
upper = layer(ours, "mods.other.upper")
check("差分だけ描くときの orig は、この MOD の層のすぐ下（ふだんの描き方と同じく、下のほかの層を通す）",
      MOD.below_ours(upper) is lower and MOD.below_ours(ours) is lower)
check("この MOD の層が無い・下に前の世代のこの MOD の層が残っているなら素の関数",
      MOD.below_ours(lower) is plain_generate
      and MOD.below_ours(layer(layer(plain_generate, MOD.__name__), MOD.__name__)) is plain_generate
      and MOD.below_ours(plain_generate) is plain_generate)
check("控えに残す引数は数・文字列・真偽だけ（文・絵・部品を除く）",
      V.template_params({"self": 1, "prompt": "p", "width": 512, "init_image": object(), "seed": -1,
                         "cb": print}) == {"width": 512, "seed": -1})
check("工程の引数は数・文字列だけ控える", V.step_call((165, 330), {"num_colors": "16"}) == [[165, 330], {"num_colors": "16"}]
      and V.step_call((object(),), {}) is None)
check("名簿は骨格の順、セーブにだけ居る人物を後ろに（重複は除く）",
      V.world_npc_names({"npcs": {"0": {"name": "A"}, "1": {"name": "B"}}},
                        {"npcs": {"5": {"name": "B"}, "6": {"name": "C"}}}) == ["A", "B", "C"])
check("強さの欄の既定は mod.json と同じ（SD1.5 0.5・SDXL・その他 0.35・モデルごとに変えるは空）",
      decls[V.STRENGTH_SD15]["default"] == V.DEFAULT_SD15 == MOD.EXPRESSION_STRENGTH_SD15 == 0.5
      and decls[V.BY_MODEL]["default"] == V.DEFAULT_BY_MODEL == MOD.EXPRESSION_STRENGTH_BY_MODEL == "")
got = V.derive({})
check("値が無ければ既定", got == {"strength": 0.35, "sd15": 0.5, "by_model": [], "bad": [],
                           "tags": V.DEFAULT_TAGS, "ids": {}, "meanings": {}, "skipped": []}, got)
rows, bad = V.model_rows(" sd15=0.5 ;\npony / !score=0.4;; x ; sdxl=abc; sdxl=0; sdxl=1.5; =0.3; sdxl=1 ")
check("モデルごとに変える: ; と改行で区切り、読めない区切り（= が無い・数でない・0 以下・1 超え・条件が空）は飛ばす",
      rows == [("sd15", 0.5), ("pony / !score", 0.4), ("sdxl", 1.0)]
      and bad == ["x", "sdxl=abc", "sdxl=0", "sdxl=1.5", "=0.3"], (rows, bad))
conf = V.derive({V.BY_MODEL: "pony=0.4; sdxl/!wai=0.3", V.STRENGTH_SD15: 0.6})
cases = [
    (("sd15", r"C:\m\sotemix_v30.safetensors"), (0.6, V.LABEL_SD15)),
    (("sdxl", r"C:\m\ponyDiffusionV6XL.safetensors"), (0.4, "モデルごとに変える: pony=0.4")),
    (("sdxl", r"C:\m\animagine.safetensors"), (0.3, "モデルごとに変える: sdxl/!wai=0.3")),
    (("sdxl", r"C:\m\WAI-Nsfw-Illustrious-17.safetensors"), (0.35, V.LABEL_OTHER)),
    ((None, None), (0.35, V.LABEL_OTHER)),
]
got = [V.strength_for(conf, model) for model, _want in cases]
check("強さ: モデルごとに変える行を前から見て最初に当たったもの、無ければ SD1.5 の系統は SD1.5 の欄、"
      "ほか（SDXL・読めない系統）は SDXL・その他の欄", got == [want for _model, want in cases], got)
# 呼び方の控えが無いときの仮の控えと、仕上げの段の見分け（VERIFICATION.md §3.132）
check("仕上げの段は画質で決まる（highres は1段目、highres_upscale は2段目、highres_faster は LCM）",
      [V.finish_stage(q) for q in ("highres", "highres_upscale", "highres_faster", "other", None)]
      == ["stage1", "stage2", "lcm", None, None])
check("下描きの段: 画質が分かればその仕上げの段でない段、分からなければ1段目",
      [V.is_draft_stage("stage1", q) for q in ("highres", "highres_upscale", None, "other")]
      == [False, True, True, True]
      and not V.is_draft_stage("stage2", "highres_upscale") and V.is_draft_stage("stage2", "highres")
      and not V.is_draft_stage("lcm", "highres_faster") and not V.is_draft_stage("lcm", None))
guess = V.fallback_template("highres_upscale")
check("仮の控えは画質のゲームの値と工程の引数（知らない画質は None）",
      guess["stage"] == "stage2" and guess["params"]["sample_method"] == "dpmpp2m"
      and guess["params"]["cfg_scale"] == 7 and guess["steps"] == V.PORTRAIT_STEPS
      and V.fallback_template("other") is None and V.fallback_template(None) is None, guess)
guess["steps"]["reduce_image_colors"][0].append(1)
check("仮の控えは表の写し（書き換えても表は変わらない）", V.PORTRAIT_STEPS["reduce_image_colors"] == [[64], {}])
half = {"steps": {"reduce_image_colors": [[16], {}]}}
check("段と引数の欠けた控えは揃っていないとみなし、仮の控えには控えの工程の引数を使う",
      not V.template_ready(half) and not V.template_ready(None) and not V.template_ready({"stage": "lcm", "params": {}})
      and V.template_ready({"stage": "lcm", "params": {"width": 512}})
      and V.fallback_template("highres", half)["steps"]["reduce_image_colors"] == [[16], {}]
      and V.fallback_template("highres", half)["steps"]["pixel_art_process"] == V.PORTRAIT_STEPS["pixel_art_process"])
real = V.fallback_template("highres_faster")
check("本物の控えが仮の値と同じなら食い違いは無い", V.template_differences(real, "highres_faster") == [])
real["params"]["sample_steps"] = 6
real["steps"]["reduce_image_colors"] = [[16], {}]
check("本物の控えと仮の値の食い違いを並べる（段が違う・揃っていない控えは比べない）",
      V.template_differences(real, "highres_faster")
      == ["params.sample_steps: 6 / 仮 5", "steps.reduce_image_colors: [[16], {}] / 仮 [[64], {}]"]
      and V.template_differences(real, "highres_upscale") == [] and V.template_differences(half, "highres") == [],
      V.template_differences(real, "highres_faster"))
check("SD1.5 の欄の読めない値は SD1.5 の既定",
      [V.derive({V.STRENGTH_SD15: v})["sd15"] for v in (0, "x", None, 2)] == [0.5, 0.5, 0.5, 1.0])

# 顔を描くモデルの決まり方（道具画面の表示。outlook.expression_models）
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)
import outlook  # noqa: E402
check("段の表は仮の控えの仕上げの段と食い違わない（道具画面の見通しと同じ表）",
      all(outlook.STAGES_BY_QUALITY["portrait"][q][-1] == V.finish_stage(q) for q in V.PORTRAIT_FINISH))
_spec_v = importlib.util.spec_from_file_location("variants_reloaded", os.path.join(MOD_DIR, "variants.py"))
variants_reloaded = importlib.util.module_from_spec(_spec_v)
_spec_v.loader.exec_module(variants_reloaded)
check("描く順番の錠は variants.py を読み直しても同じ物（注入し直した世代と、走り続ける古い世代の仕事で重ねない）",
      variants_reloaded.Turn is not V.Turn and variants_reloaded.turn() is V.turn())
custom = [{"name": "FacePre", "values": {"CHECKPOINT_PATH": ""}},
          {"name": "Second", "values": {"CHECKPOINT_PATH": ""}},
          {"name": "Kind", "values": {"CHECKPOINT_PATH": ""}}]
game_row = {"model": "gamemodel", "family": "sd15"}


def used_of(values):
    rows = outlook.expression_models(values, custom, game_row)
    return [row["key"] for row in rows if row["used"]], rows


base_values = {"FACE_REDRAW": True, "FACE_REDRAW_PRESET": "FacePre", "CHAIN": True, "CHAIN_PORTRAIT_AUTO": True,
               "CHAIN_PORTRAIT_SECOND": "Second", "KIND_PRESETS": True, "PORTRAIT_PRESET": "Kind"}
checks = [
    (base_values, "face"),
    (dict(base_values, FACE_REDRAW=False), "chain"),
    (dict(base_values, FACE_REDRAW_PRESET=""), "chain"),
    (dict(base_values, FACE_REDRAW=False, CHAIN_PORTRAIT_AUTO=False), "kind"),
    (dict(base_values, FACE_REDRAW=False, CHAIN=False, KIND_PRESETS=False), "game"),
]
got = [used_of(values)[0] for values, _want in checks]
check("顔を描くモデルは、顔だけ描き直すのプリセット → 通常の生成の txt2img→img2img の2つ目 → 種類ごとのプリセット → "
      "ゲーム既定の順で最初に当てはまるもの（本体の variant_job・portrait_route と同じ）",
      got == [[want] for _values, want in checks], got)
_used, rows = used_of(dict(base_values, FACE_REDRAW=False, CHAIN_PORTRAIT_SECOND=""))
check("txt2img→img2img の2つ目が空ならゲーム既定のモデルで描く（使っていない扱いにしない）",
      rows[1]["used"] and "gamemodel" in rows[1]["model"] and rows[1]["family"] == "sd15", rows[1])
# モデルを差し替えていても、本体はチェックポイントの無いプリセット・空や見つからない描き方のプリセットを
# 差し替えの前のゲームのモデルで描く（preset_route / method_route）。種類ごとのプリセットが見つからなければ次の行
replaced = {"CHECKPOINT_PATH": "C:/nowhere/otherxl.safetensors"}
_used, rows = used_of(dict(base_values, FACE_REDRAW=False, CHAIN_PORTRAIT_SECOND="", **replaced))
check("差し替えていても、空の2つ目は差し替えの前のゲームのモデルで描くと出す（強さの照合もそのモデル名）",
      rows[1]["used"] and rows[1]["family"] == "sd15" and rows[1]["path"] == "gamemodel", rows[1])
_used, rows = used_of(dict(base_values, FACE_REDRAW_PRESET="Gone", **replaced))
check("差し替えていても、見つからない顔のプリセットは差し替えの前のゲームのモデル",
      rows[0]["used"] and rows[0]["family"] == "sd15" and "見つからないのでゲーム既定" in rows[0]["model"], rows[0])
_used, rows = used_of(dict(base_values, **replaced))
check("差し替えていても、チェックポイントの無いプリセットはゲームのモデルで描くと出す",
      rows[0]["used"] and rows[0]["family"] == "sd15" and rows[0]["path"] == "gamemodel", rows[0])
used, rows = used_of(dict(base_values, FACE_REDRAW=False, CHAIN_PORTRAIT_AUTO=False, PORTRAIT_PRESET="Gone",
                          **replaced))
check("種類ごとのプリセットが見つからなければ、その行では決まらず差し替えたモデルの行で決まる（本体の kind_route）",
      used == ["game"] and rows[3]["path"] == replaced["CHECKPOINT_PATH"] and "見つからない" in rows[2]["model"],
      (used, rows))
_used, rows = used_of(dict(base_values, FACE_REDRAW_PRESET="Gone"))
check("見つからないプリセットはゲーム既定のモデルで描くと出す",
      rows[0]["used"] and "見つからないのでゲーム既定" in rows[0]["model"], rows[0])
got = V.derive({V.STRENGTH: 0.5, "EXPRESSION_SAD": "  ", "EXPRESSION_SMILE": " grin "})
check("空のタグの表情は描かない（前後の空白は外す）",
      got["strength"] == 0.5 and "sad" not in got["tags"] and got["tags"]["smile"] == "grin", got)
check("強さの範囲の外と読めない値は既定、1 を超える値は 1",
      [V.derive({V.STRENGTH: v})["strength"] for v in (0, -1, "x", None, float("nan"), 3)]
      == [0.35, 0.35, 0.35, 0.35, 0.35, 1.0])

print("追加の表情")
check("基本表情と名前の形はローダの窓口と同じ",
      V.BUILTIN == expressions.NAMES and V.ADDED_PATTERN.pattern == expressions.NAME_PATTERN.pattern)
added = [{"id": "x1", "name": "shy", "meaning": "照れる", "tags": " blush "},
         {"name": "smile", "tags": "grin"}, {"name": "Bad", "tags": "x"}, {"name": "shy", "tags": "y"},
         {"id": "x3", "name": "quiet", "meaning": "黙る", "tags": ""}]
got = V.derive({"EXPRESSION_SAD": ""}, added)
check("追加の表情はタグのある行だけ基本表情の後に並ぶ（基本表情と重なる・使えない・重なる名前は飛ばす）",
      list(got["tags"]) == ["smile", "angry", "surprised", "shy"] and got["tags"]["shy"] == "blush"
      and got["ids"] == {"shy": "x1"} and got["meanings"] == {"shy": "照れる"}
      and got["skipped"] == ["smile", "Bad", "shy"], got)
check("描くはずの表情と窓口に登録する一覧",
      V.wanted_rows(got)[-1] == {"name": "shy", "tags": "blush", "id": "x1"}
      and V.wanted_rows(got)[0] == {"name": "smile", "tags": V.DEFAULT_TAGS["smile"]}
      and V.entry_rows(got) == [{"name": "smile"}, {"name": "angry"}, {"name": "surprised"},
                                {"name": "shy", "meaning": "照れる"}], V.entry_rows(got))
errors = V.check_added([{"name": "ok_1", "meaning": "m", "tags": "t"}, {"name": "2x", "tags": ""},
                        {"name": "sad"}, {"name": "ok_1", "meaning": "m"}, {"name": "nomean", "tags": "t"}])
check("保存の前の検査（名前の形・基本表情と重なる・行どうしで重なる・タグのある行の意味が空）",
      [e.split(":")[0] for e in errors] == ["2 行目", "3 行目", "4 行目", "5 行目"], errors)
got = V.normalize_added([{"name": " a ", "meaning": "x\ny"}, {"id": "x1", "name": "b"}, {"id": "x1", "name": "c"}])
check("id の無い行と重なった id の行には、空いている id を付ける（改行は空白に）",
      [r["id"] for r in got] == ["x2", "x1", "x3"] and got[0]["name"] == "a" and got[0]["meaning"] == "x y", got)
temp_dir = tempfile.mkdtemp(prefix="sd_added_")
try:
    path = V.added_path(temp_dir)
    check("無い置き場は見本の shy", V.load_added(path) == [dict(row) for row in V.SAMPLE_ADDED]
          and V.load_added(path)[0]["name"] == "shy" and not V.check_added(V.load_added(path)))
    check("空で保存したら見本は戻らない", V.save_added(path, []) and V.load_added(path) == [])
    check("書いて読み直せる", V.save_added(path, [{"id": "x1", "name": "shy", "meaning": "照れる", "tags": "blush"}])
          and V.load_added(path) == [{"id": "x1", "name": "shy", "meaning": "照れる", "tags": "blush"}])
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    check("壊れた置き場は空", V.load_added(path) == [])
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)

print("文")
got = V.expression_prompt("masterpiece, full body, standing, young man, cloak", "light smile, closed mouth")
check("全身のタグを外した位置に顔の寄りのタグ、その直後に表情のタグ",
      got == "masterpiece, portrait, face focus, close-up, light smile, closed mouth, young man, cloak", got)
got = V.expression_prompt("young man", "sad, worried")
check("全身のタグが無ければ先頭に顔の寄りのタグと表情のタグ",
      got == "portrait, face focus, close-up, sad, worried, young man", got)
check("タグが空なら顔の寄りだけ", V.expression_prompt("young man", "") == "portrait, face focus, close-up, young man")

# ---------------------------------------------------------------- 順番
print("順番")
turn = V.Turn()
order = []


def job(tag, ready=None):
    if ready is not None:
        ready.set()
    with turn.job():
        order.append(tag)


with turn.game():
    with turn.game():
        order.append("game nested")
    worker = threading.Thread(target=job, args=("job",))
    worker.start()
    time.sleep(0.05)
    check("ゲームが描いている間、差分は待つ（同じスレッドは入り直せる）", order == ["game nested"], order)
worker.join(2)
check("ゲームが描き終えると差分が描く", order == ["game nested", "job"], order)

order[:] = []
held, release = threading.Event(), threading.Event()


def long_job():
    with turn.job():
        held.set()
        release.wait(2)
        order.append("job 1")


def game():
    with turn.game():
        order.append("game")


first = threading.Thread(target=long_job)
first.start()
held.wait(2)
waiting_game = threading.Thread(target=game)
waiting_game.start()
time.sleep(0.05)
second_ready = threading.Event()
second = threading.Thread(target=job, args=("job 2", second_ready))
second.start()
second_ready.wait(2)
time.sleep(0.05)
release.set()
for t in (first, waiting_game, second):
    t.join(2)
check("差分を描き終えたとき、ゲームが待っていればゲームが先", order == ["job 1", "game", "job 2"], order)
check("描き終えたら順番は空く", turn.busy() is False)
check("プロセスに1つ（注入し直しても同じ順番）", V.turn() is V.turn())

print("工程")
local = threading.local()


def mark():
    seen = getattr(local, "seen", False)
    local.seen = True
    return seen, threading.get_ident()


got = [V.in_fresh_thread(mark) for _ in range(3)]
check("1回ずつ新しいスレッドで通す（スレッドの控えを持ち込まない）",
      all(seen is False for seen, _i in got) and threading.get_ident() not in [i for _s, i in got], got)
try:
    V.in_fresh_thread(lambda: 1 / 0)
    raised = False
except ZeroDivisionError:
    raised = True
check("例外は呼んだ側で投げ直す", raised)

# ---------------------------------------------------------------- 流れ（PIL が要る）
try:
    from PIL import Image
except ImportError:
    Image = None

if Image is None:
    print("  skip 流れ（PIL が無い）")
else:
    print("流れ")

    ON_MAIN = {"now": False}

    class Ctx(object):
        _mod = None

        def __init__(self, out_dir, settings):
            self.out_dir = out_dir
            self.state_dir = os.path.join(out_dir, "state")
            self.game_dir = out_dir
            self.runtime_dir = os.path.join(out_dir, "runtime")
            self.mod_dir = MOD_DIR
            self.settings = settings
            self.hooks = {}
            self.errors = []
            self.notes = []
            self.warnings = []
            self.generation = "test"

        @property
        def mod_name(self):
            # 本物の ctx はフォルダ名を返す
            return os.path.basename(self.mod_dir) if self.mod_dir else None

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
            import traceback
            self.errors.append(message + "\n" + traceback.format_exc())

        def refresh_status(self):
            pass

        def superseded(self):
            return False

        def wait_booted(self, timeout=60.0):
            return ml.ModContext.wait_booted(self, timeout)

        def on_ready(self, fn, key=None, delay=0):
            return True

        def run_on_main_thread(self, fn, timeout=5.0, give_up=None):
            ON_MAIN["now"] = True
            try:
                return True, fn()
            finally:
                ON_MAIN["now"] = False

        def wrap(self, target, required=True, safe=False, alias_scan=True):
            def decorate(fn):
                self.hooks[target] = fn
                return fn
            return decorate

    class Owner(object):
        def superseded(self):
            return False

    class VarSD(object):
        """sdcpp の部品の代わり。描いた引数を控え、引数の寸法の絵を返す。"""
        calls = []
        on_call = None
        empty_at = None             # この回目の描画は絵を返さない

        def __init__(self, model_path="", taesd_path="", vae_path="", lora_model_dir=""):
            self.model_path = model_path

        def generate_image(self, prompt, negative_prompt="", init_image=None, width=512, height=512,
                           cfg_scale=7.0, scheduler="default", sample_method="euler_a",
                           sample_steps=20, strength=0.75, seed=-1):
            VarSD.calls.append({"prompt": prompt, "init": getattr(init_image, "size", None),
                                "strength": strength, "size": (width, height)})
            if VarSD.on_call is not None:
                VarSD.on_call(len(VarSD.calls))
            if VarSD.empty_at == len(VarSD.calls):
                return None
            color = (40, 80, 120) if init_image is None else (200, 30, 30)
            return [Image.new("RGB", (width, height), color)]

    VarSD.__module__ = "fake_var.stable_diffusion"
    manager = types.ModuleType("image_generation.fakevar.stable_diffusion_manager")
    manager.StableDiffusion = VarSD
    manager.txt2img_pipe = VarSD()
    manager.load_sd_pipeline = lambda: None
    manager.model_path_anime = "runtime/models/sd15/checkpoints/none.safetensors"
    for fn in MOD.STAGE_FUNCS:
        setattr(manager, fn, lambda *a, **kw: None)
    sys.modules[manager.__name__] = manager
    creature = types.ModuleType("image_generation.fakevar.image_generation_creature")
    step_threads = []

    def pixel_art_process(image, width=256, height=256, **_kw):
        step_threads.append(threading.get_ident())
        return image, image.resize((width, height), Image.NEAREST)

    def reduce_image_colors(image, n_colors=16):
        return image.copy()

    creature.pixel_art_process = pixel_art_process
    creature.reduce_image_colors = reduce_image_colors
    for fn in MOD.KIND_FUNCS:
        setattr(creature, fn, lambda *a, **kw: None)
    sys.modules[creature.__name__] = creature

    base = tempfile.mkdtemp(prefix="sd_variants_")
    saved_env = os.environ.get("IML_INSTANTALE_DATA")
    os.environ["IML_INSTANTALE_DATA"] = base
    real_find = MOD.faces.find
    MOD.faces.find = lambda image, conf: ((200, 150, 100, 100), 2.0)
    main = sys.modules["__main__"]
    saved_app = getattr(main, "APP", None)
    try:
        values = {key: decls[key]["default"] for key in decls}
        ctx = Ctx(tempfile.mkdtemp(prefix="sd_variants_ctx_"), values)
        # 追加の表情は空で始める（置き場が無いと見本の shy を描く。見本は「追加の表情」の節の終わりで確かめる）
        V.save_added(V.added_path(ctx.state_dir), [])
        MOD.apply(ctx)
        pipe = manager.txt2img_pipe
        exit_target = "fake_var.stable_diffusion:VarSD.generate_image"
        entrance = creature.__name__ + ":generate_character_image"
        pixel_hook = ctx.hooks.get(creature.__name__ + ":pixel_art_process")
        reduce_hook = ctx.hooks.get(creature.__name__ + ":reduce_image_colors")
        check("立ち絵の入口と、工程の引数を控える口を包む",
              exit_target in ctx.hooks and entrance in ctx.hooks and pixel_hook and reduce_hook,
              sorted(ctx.hooks))
        check("一括生成の本体を包む", MOD.BULK_TARGET in ctx.hooks)

        draws = [0]

        def game_draw(world_name, name, category, prompt):
            """ゲームの立ち絵の入口の代わり。描いて、背景を抜いて、縮小・2倍・減色して保存する。"""
            MOD.MARKS.func = "generate_image_real_lcm"
            try:
                result = ctx.hooks[exit_target](VarSD.generate_image, pipe, prompt=prompt,
                                                negative_prompt="neg", width=512, height=1024)
            finally:
                MOD.MARKS.func = None
            # 描くたびに違う絵にする（同じ絵なら印が合い、差分は描き直さない。expressions.plan）
            draws[0] += 1
            image = result[0].copy()
            image.putpixel((0, 0), tuple([draws[0] % 256] * len(image.getbands())))
            folder = expressions.character_dir(world_name, name)
            os.makedirs(folder, exist_ok=True)
            image.save(os.path.join(folder, "generated_image.png"))
            cut = image.convert("RGBA")
            alpha = Image.new("L", cut.size, 0)
            alpha.paste(255, (0, 0, cut.size[0] // 2, cut.size[1]))
            cut.putalpha(alpha)
            cut.save(os.path.join(folder, "no_bg_image.png"))
            _big, small = pixel_hook(creature.pixel_art_process, cut, 165, 330)
            doubled = small.resize((330, 660), Image.NEAREST)
            reduce_hook(creature.reduce_image_colors, doubled, 16).save(
                os.path.join(folder, "reduced_color_image.png"))
            return result

        def join_jobs():
            for thread in threading.enumerate():
                if thread.name == "mod343-expressions":
                    thread.join(10)

        folder = expressions.character_dir("w", "Zeno")
        VarSD.calls[:] = []
        ctx.hooks[entrance](game_draw, "w", "Zeno", "cat", "masterpiece, full body, standing, young man")
        join_jobs()
        check("表情を見せる MOD が宣言していなければ差分を描かない",
              len(VarSD.calls) == 1 and not os.path.isdir(expressions.folder_of(folder)), VarSD.calls)
        kept_template = V.load_template(os.path.join(ctx.state_dir, "stable_diffusion", V.TEMPLATE_NAME)) or {}
        check("宣言が無くても呼び方の控え（段と引数）は書く（既存の立ち絵の顔の描き直しが使う）",
              kept_template.get("stage") and kept_template.get("params", {}).get("width") == 512, kept_template)

        expressions.want("347_conversation_expressions", Owner())
        VarSD.calls[:] = []
        step_threads[:] = []
        ctx.hooks[entrance](game_draw, "w", "Zeno", "cat", "masterpiece, full body, standing, young man")
        join_jobs()
        faces_drawn = VarSD.calls[1:]
        check("宣言があれば、立ち絵の後に表情ごとに顔の周りを1回ずつ描く",
              len(VarSD.calls) == 5 and all(c["init"] == (768, 768) and c["size"] == (768, 768)
                                            and c["strength"] == 0.35 for c in faces_drawn), VarSD.calls)
        check("表情のタグは顔の寄りのタグの直後",
              faces_drawn[0]["prompt"] == "masterpiece, portrait, face focus, close-up, light smile, closed mouth, young man"
              and faces_drawn[3]["prompt"].split(", ")[4:6] == ["surprised", ":o"],
              [c["prompt"] for c in faces_drawn])
        check("ゲームの工程の引数を立ち絵の入口の中の最初の1回で控える",
              MOD.STEP_CALLS.get(creature.__name__) == {"pixel_art_process": ((165, 330), {}),
                                                       "reduce_image_colors": ((16,), {})},
              MOD.STEP_CALLS.get(creature.__name__))
        shown = {e: expressions.image(folder, e) for e in expressions.VARIANTS}
        check("4つの差分を置き、印が合うので窓口が返す", all(shown.values()), shown)
        smile = Image.open(shown["smile"])
        check("差分はゲームの立ち絵と同じ寸法・同じ工程（縮小の引数・2倍）",
              smile.size == (330, 660) and smile.mode == "RGBA", (smile.size, smile.mode))
        check("背景の透明度は元の絵の背景抜きのもの（右半分が透明）",
              smile.getpixel((300, 330))[3] == 0 and smile.getpixel((10, 330))[3] == 255,
              (smile.getpixel((300, 330)), smile.getpixel((10, 330))))
        check("工程は1枚ずつ別のスレッドで通す（ゲームの工程の1回 + 差分の4回）",
              len(step_threads) == 5 and len(set(step_threads[1:])) == 4, step_threads)
        check("裏で描いたとログに残る", any("表情差分 [Zeno]" in n and "裏で描いた" in n for n in ctx.notes),
              [n for n in ctx.notes if "表情差分" in n])
        check("描き終えたら順番は空く", V.turn().busy() is False)

        class CreatingApp(object):            # ui.find_app() は __main__ の InstantaleApp の値を拾う
            player = None
            root = types.SimpleNamespace(current=MOD.ui.CHARACTER_CREATE_SCREEN)
        main.InstantaleApp = CreatingApp
        main.APP = CreatingApp()

        def leave_midway(*a, **kw):
            main.APP.root.current = "world_choice"          # 描いている間に作成画面の「戻る」で世界の一覧へ
            return game_draw(*a, **kw)
        VarSD.calls[:] = []
        ctx.hooks[entrance](leave_midway, "w", "Zeno", "cat", "masterpiece, full body, standing, young man")
        join_jobs()
        check("キャラを作る画面で描き始めた立ち絵（主人公。app.player はまだ無い）には、途中で画面を出ても差分を描かない",
              len(VarSD.calls) == 1, VarSD.calls)
        VarSD.empty_at = 3                                  # 立ち絵・smile の次（angry）が絵を返さない
        VarSD.calls[:] = []
        try:
            ctx.hooks[entrance](game_draw, "w", "Zeno", "cat", "masterpiece, full body, standing, young man")
            join_jobs()
        finally:
            VarSD.empty_at = None
        check("キャラを作る画面の外で描いた立ち絵には、また差分を描く", len(VarSD.calls) == 5, VarSD.calls)
        check("絵が返らなかった表情があれば、描けた分を見せ、描き揃っていない印にして次の一括生成で描き直す",
              expressions.image(folder, "angry") is None and expressions.image(folder, "smile") is not None
              and not expressions.complete(folder) and V.needs_variants(folder, expressions)
              and any("次の一括生成で描き直す" in n for n in ctx.notes), ctx.notes[-2:])

        expressions.clear(folder)
        VarSD.calls[:] = []
        ctx.hooks[MOD.BULK_TARGET](lambda _app: ctx.hooks[entrance](
            game_draw, "w", "Zeno", "cat", "masterpiece, full body, young man"), object())
        check("一括生成のあいだは、入口を抜ける前に描き終える（待たずに差分が在る）",
              expressions.image(folder, "angry") is not None and len(VarSD.calls) == 5, VarSD.calls)
        check("一括生成なので続けて描いたとログに残る", any("続けて描いた" in n for n in ctx.notes))
        check("一括生成を抜けたら数は戻る", MOD.BULK["depth"] == 0)

        def redraw_midway(count):
            if count == 3:          # 2つ目の表情を描いているときに立ち絵が描き直された
                io.open(os.path.join(folder, "generated_image.png"), "ab").write(b"x")
        VarSD.on_call = redraw_midway
        VarSD.calls[:] = []
        ctx.hooks[entrance](game_draw, "w", "Zeno", "cat", "masterpiece, young man")
        join_jobs()
        VarSD.on_call = None
        check("途中で立ち絵が描き直されたら止めて、印を書かない（古い差分を見せない）",
              len(VarSD.calls) == 3 and expressions.image(folder, "smile") is None
              and any("描き直されたので止める" in n for n in ctx.notes), VarSD.calls)

        class InstantaleApp(object):          # ui.find_app() は __main__ のこの型の値を拾う
            player = types.SimpleNamespace(name="Hero")
        main.InstantaleApp = InstantaleApp
        main.APP = InstantaleApp()
        VarSD.calls[:] = []
        ctx.hooks[entrance](game_draw, "w", "Hero", "cat", "young man")
        join_jobs()
        check("主人公の立ち絵には差分を描かない", len(VarSD.calls) == 1, VarSD.calls)

        # ---- 一括生成で、立ち絵が既にある人物に差分だけ描く
        saved_template = V.load_template(os.path.join(ctx.state_dir, "stable_diffusion", V.TEMPLATE_NAME))
        check("立ち絵を描いたときの呼び方を控える（文と絵は除く。工程の引数も）",
              saved_template is not None and saved_template.get("stage") == "lcm"
              and saved_template["params"].get("width") == 512 and "prompt" not in saved_template["params"]
              and saved_template.get("steps") == {"pixel_art_process": [[165, 330], {}],
                                                  "reduce_image_colors": [[16], {}]}, saved_template)
        wdir = os.path.join(base, "worlds", "w2")
        os.makedirs(wdir)
        with io.open(os.path.join(wdir, "world_data.json"), "w", encoding="utf-8") as fh:
            json.dump({"npcs": {"0": {"name": "Ana"}, "1": {"name": "Bob"}, "2": {"name": "Hero2"},
                                "3": {"name": "NoPic"}}}, fh)
        os.makedirs(os.path.join(base, "saves", "w2"))
        with io.open(os.path.join(base, "saves", "w2", "savedata.json"), "w", encoding="utf-8") as fh:
            json.dump({"player_data": {"name": "Hero2"}, "npcs": {"9": {"name": "Cid"}}}, fh)

        def portrait(name, prompts=True):
            path = expressions.character_dir("w2", name)
            os.makedirs(path)
            Image.new("RGB", (512, 1024), (90, 90, 90)).save(os.path.join(path, "generated_image.png"))
            Image.new("RGBA", (512, 1024), (90, 90, 90, 255)).save(os.path.join(path, "no_bg_image.png"))
            if prompts:
                with io.open(os.path.join(path, V.PROMPTS_NAME), "w", encoding="utf-8") as fh:
                    json.dump({"positive_prompt": "full-body, masterpiece, standing, {} face".format(name),
                               "negative_prompt": "nsfw"}, fh)
            return path
        ana, bob, hero, cid = portrait("Ana"), portrait("Bob"), portrait("Hero2"), portrait("Cid", prompts=False)
        expressions.seal(bob, expressions.stamp(bob))       # 描き済み（印が合う）
        real_creature = MOD.imagegen.creature_module
        MOD.imagegen.creature_module = lambda *_a: creature
        MOD.STEP_CALLS.clear()                              # 別の起動（工程の控えは state\ から）
        try:
            VarSD.calls[:] = []
            progress_seen = []
            game_done = {"status": "complete", "completed": 2, "remaining": 0, "failed": 0}

            def game_bulk(_self, _path, _stop, progress):
                """ゲームの一括生成の代わり。自分の仕事を終えたら画面へ「完了」を渡して返る。"""
                progress(dict(game_done))
                return "done"
            ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                       threading.Event(), progress_seen.append)
            check("一括生成の後に、印の無い人物（骨格とセーブの名簿から。主人公・描き済み・立ち絵の無い人物は除く）"
                  "へ差分だけ描く", len(VarSD.calls) == 4 and expressions.image(ana, "smile") is not None
                  and expressions.image(hero, "smile") is None, VarSD.calls)
            check("文は人物のフォルダの prompts.json から（顔の寄りと表情のタグを足す）",
                  VarSD.calls[0]["prompt"].startswith("portrait, face focus, close-up, light smile, closed mouth, masterpiece")
                  and "Ana face" in VarSD.calls[0]["prompt"], VarSD.calls[:1])
            check("prompts.json の無い人物は数えずに外す（一括生成のたびにゲームの用意へ進んで空振りしない）",
                  any("{} の無い立ち絵 1 人".format(V.PROMPTS_NAME) in n for n in ctx.notes)
                  and any("1 人に描いた（顔が見つからない 0 人、描けなかった 0 人" in n for n in ctx.notes),
                  [n for n in ctx.notes if "一括生成" in n])
            check("描く間は一括生成の画面へ件数を渡す（人物ごと）",
                  [(p["completed"], p["remaining"], p.get("label")) for p in progress_seen[1:-1]]
                  == [(0, 1, "Ana（表情差分）")], progress_seen)
            check("描き終えたら、画面をゲームが最後に渡した表示（完了）へ戻す（「実行中」のまま残さない）",
                  progress_seen[0] == game_done and progress_seen[-1] == game_done, progress_seen)
            check("差分だけ描いた回はログにそう残る",
                  any("表情差分 [Ana]" in n and "一括生成で差分だけ描いた" in n for n in ctx.notes))
            # ゲームが画面へ件数を1回も渡さずに終えた回も、「実行中」のまま残さない
            expressions.clear(ana)
            progress_seen = []
            ctx.hooks[MOD.BULK_TARGET](lambda *_a, **_kw: "done", object(), os.path.join(wdir, "world_data.json"),
                                       threading.Event(), progress_seen.append)
            expressions.clear(ana)
            MOD.STEP_CALLS[creature.__name__] = {"pixel_art_process": ((165, 330), {})}
            VarSD.calls[:] = []
            ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                       threading.Event(), [].append)
            MOD.STEP_CALLS.clear()
            check("この起動で工程を片方だけ控えていても、保存した控えで揃えて差分だけ描く",
                  len(VarSD.calls) == 4 and expressions.image(ana, "smile") is not None, VarSD.calls)
            check("ゲームが件数を渡さずに終えた回は、差分だけ描いた後にゲームの終わりの形（件数 0）を渡す",
                  len(progress_seen) == 2 and progress_seen[0]["status"] == "running"
                  and progress_seen[-1] == MOD.BULK_FINAL, progress_seen)
            expressions.clear(ana)
            stop = threading.Event()
            stop.set()
            VarSD.calls[:] = []
            ctx.hooks[MOD.BULK_TARGET](lambda *a, **kw: None, object(), os.path.join(wdir, "world_data.json"),
                                       stop, None)
            check("中断された一括生成では差分だけ描かない", VarSD.calls == [], VarSD.calls)
            expressions.clear(ana)
            stop = threading.Event()
            VarSD.on_call = lambda count: stop.set() if count == 1 else None
            VarSD.calls[:] = []
            progress_seen = []
            ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                       stop, progress_seen.append)
            VarSD.on_call = None
            check("差分だけ描いている途中で中断したら、印を書かずに止め、画面は中断の表示にする",
                  len(VarSD.calls) == 1 and expressions.image(ana, "smile") is None
                  and progress_seen[-1] == dict(game_done, status="stopped"), (VarSD.calls, progress_seen))
            # 立ち絵が揃った世界を起動直後に一括生成へ掛けた形: ゲームの仕事が無く、画像生成が用意されていない
            expressions.clear(ana)
            loaded = {"on": False, "calls": 0}
            MOD.imagegen.creature_module = lambda *_a: creature if loaded["on"] else None
            ctx.warnings[:] = []
            VarSD.calls[:] = []
            ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                       threading.Event(), [].append)
            check("画像生成が用意されておらず、ゲームの用意も呼べないなら、描かずに理由を知らせる",
                  VarSD.calls == [] and any("AIManager" in m for _k, m in ctx.warnings), ctx.warnings)

            def set_ai_models():
                loaded["calls"] += 1
                loaded["on"] = True
                loaded.setdefault("main", []).append(ON_MAIN["now"])
            main.APP.ai_manager = types.SimpleNamespace(set_ai_models=set_ai_models)
            real_wait = MOD.REAPPLY_WAIT
            MOD.REAPPLY_WAIT = 0.3                          # 当て直しが来ない形（このまま描く）
            VarSD.calls[:] = []
            progress_seen = []
            try:
                ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                           threading.Event(), progress_seen.append)
            finally:
                MOD.REAPPLY_WAIT = real_wait
            check("用意の前に、ゲームが出した「完了」の後へ「画像生成を用意しています」を渡し、ログにも人数を残す"
                  "（モデルの読み込みの間、画面が完了のまま止まって見えない）",
                  [(p["completed"], p["remaining"], p.get("label")) for p in progress_seen[1:3]]
                  == [(0, 1, "{}（表情差分）".format(V.PROGRESS_PREPARING)), (0, 1, "Ana（表情差分）")]
                  and progress_seen[-1] == game_done
                  and any("顔の描き直し 0 人・表情差分 1 人を描く前に、ゲームの画像生成を用意する" in n
                          for n in ctx.notes), progress_seen)
            check("画像生成が用意されていなければ、ゲームの用意（set_ai_models）を1回呼んでから差分だけ描く",
                  loaded["calls"] == 1 and len(VarSD.calls) == 4 and expressions.image(ana, "smile") is not None,
                  (loaded, VarSD.calls))
            check("ゲームの用意は一括生成のスレッドから呼ばず、メインスレッドで走らせる（ctx.run_on_main_thread）",
                  loaded.get("main") == [True], loaded)
            check("用意を呼んだ回は、描く前にローダの当て直しを待つ（来なければ上限の後にこのまま描く）",
                  any("当て直すのを待ってから描く" in n for n in ctx.notes)
                  and any("当て直されなかったので、このまま描く" in n for n in ctx.notes))
            # 当て直しが来る形: 待っている間に登録が新しくなったら、1人も描かずに新しい登録へ任せる
            loaded["on"] = False
            expressions.clear(ana)
            mine = MOD.BULK["backfill"]
            took = []

            def reapplied(world_path, stop_event, progress, handed=False):
                took.append((world_path, handed, expressions.wanted()))
                return True

            def reapply():
                # 当て直しの形: 新しい 343 が先に登録し、その時点では 347 の宣言が数えられない。347 は後から宣言し直す
                expressions.withdraw("347_conversation_expressions")
                MOD.BULK["backfill"] = reapplied
                threading.Timer(0.4, lambda: expressions.want("347_conversation_expressions", Owner())).start()

            def set_ai_models_then_reapply():
                set_ai_models()
                threading.Timer(0.1, reapply).start()
            main.APP.ai_manager = types.SimpleNamespace(set_ai_models=set_ai_models_then_reapply)
            VarSD.calls[:] = []
            try:
                got = mine(os.path.join(wdir, "world_data.json"), threading.Event(), None)
            finally:
                MOD.BULK["backfill"] = mine
                main.APP.ai_manager = types.SimpleNamespace(set_ai_models=set_ai_models)
                expressions.want("347_conversation_expressions", Owner())
            check("用意の後に当て直されたら、1人も描かずに新しい登録で描く（当て直し前の不完全なフックで描かない）",
                  VarSD.calls == [] and len(took) == 1 and took[0][1] is True and got is True, (VarSD.calls, took))
            check("新しい登録へ渡すのは、表情を見せる MOD が宣言し直してから（当て直しの途中で渡すと描かずに戻る）",
                  took[0][2] is True, took)
            expressions.withdraw("347_conversation_expressions")
            seen_notes = len(ctx.notes)
            mine(os.path.join(wdir, "world_data.json"), threading.Event(), None, handed=True)
            expressions.want("347_conversation_expressions", Owner())
            check("受け取った回に宣言が無ければ、描かない理由をログに残す",
                  any("宣言が無いので、表情差分を描かない" in n for n in ctx.notes[seen_notes:]), ctx.notes[seen_notes:])
            loaded["calls"] = 1
            VarSD.calls[:] = []
            expressions.clear(ana)
            ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                       threading.Event(), [].append)
            check("用意されていれば、ゲームの用意は呼ばない", loaded["calls"] == 1 and len(VarSD.calls) == 4, loaded)

            # ---- 呼び方の控えが無い起動（更新の後に立ち絵を1枚も描かずに一括生成をかけた形。VERIFICATION.md §3.132）
            tpl_path = os.path.join(ctx.state_dir, "stable_diffusion", V.TEMPLATE_NAME)
            os.replace(tpl_path, tpl_path + ".keep")
            cfg_path = os.path.join(base, "config.json")

            def set_quality(quality):
                with io.open(cfg_path, "w", encoding="utf-8") as fh:
                    json.dump({"ai_setting": {"local_model_setting": {"sd_backend": {
                        "name": "sdcpp_cuda", "character_generation_quality": quality}}}}, fh)
            try:
                set_quality("highres_faster")
                ctx_nt = Ctx(ctx.out_dir, values)              # 控えを持たない起動
                MOD.apply(ctx_nt)
                VarSD.calls[:] = []
                expressions.clear(ana)
                ctx_nt.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                              threading.Event(), [].append)
                check("控えが無ければ、画質の設定のゲームの値で仮の控えを組んで差分だけ描く（ファイルには書かない）",
                      len(VarSD.calls) == 4 and expressions.image(ana, "smile") is not None
                      and not os.path.isfile(tpl_path)
                      and any("画質の設定（highres_faster）のゲームの値で描く" in n for n in ctx_nt.notes),
                      (VarSD.calls, [n for n in ctx_nt.notes if "一括生成" in n]))
                set_quality("someday_quality")
                ctx_nt = Ctx(ctx.out_dir, values)
                MOD.apply(ctx_nt)
                VarSD.calls[:] = []
                expressions.clear(ana)
                progress_seen = []
                ctx_nt.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                              threading.Event(), progress_seen.append)
                check("画質からも組めなければ描かずに理由を知らせ、「画像生成を用意しています」は出さない",
                      VarSD.calls == [] and progress_seen == [game_done]
                      and any("画質の設定（someday_quality）からも組めない" in m for _k, m in ctx_nt.warnings),
                      (progress_seen, ctx_nt.warnings))
            finally:
                os.replace(tpl_path + ".keep", tpl_path)
                os.remove(cfg_path)
                MOD.apply(ctx)                                 # 続きの検査はもとの起動の登録で

            # 一括生成がローダの当て直しの最中に終わった回（ゲームの仕事が無い。VERIFICATION.md §3.132）。
            # 当て直しは boot の錠を持って走り、347 の宣言はその終わり近くで戻る。一括生成はゲームの裏のスレッドで走る
            boot_lock = ml._boot_lock()
            booting = threading.Event()

            def boot_then_declare():
                with boot_lock:
                    booting.set()
                    time.sleep(0.5)
                    expressions.want("347_conversation_expressions", Owner())

            def bulk_in_thread():
                worker = threading.Thread(target=lambda: ctx.hooks[MOD.BULK_TARGET](
                    game_bulk, object(), os.path.join(wdir, "world_data.json"), threading.Event(), [].append))
                worker.start()
                worker.join(30)
            expressions.withdraw("347_conversation_expressions")
            expressions.clear(ana)
            VarSD.calls[:] = []
            seen_notes = len(ctx.notes)
            booter = threading.Thread(target=boot_then_declare)
            booter.start()
            booting.wait(5)
            check("当て直しの最中は、メインスレッドからは待たずに偽を返す（画面を止めない）",
                  ctx.wait_booted(5) is False)
            bulk_in_thread()
            booter.join(5)
            check("一括生成が当て直しの最中に終わっても、当て直しが終わるのを待って差分を描く",
                  len(VarSD.calls) == 4 and expressions.image(ana, "smile") is not None
                  and not any("宣言が無い" in n for n in ctx.notes[seen_notes:])
                  and any("当て直しが終わるのを" in n for n in ctx.notes[seen_notes:]), (VarSD.calls, ctx.notes[seen_notes:]))
            check("当て直しが終われば、メインスレッドからも真", ctx.wait_booted(5) is True)
            expressions.withdraw("347_conversation_expressions")
            expressions.clear(ana)
            VarSD.calls[:] = []
            seen_notes = len(ctx.notes)
            began = time.time()
            bulk_in_thread()
            expressions.want("347_conversation_expressions", Owner())
            check("表情を見せる MOD が居なければ、待たずに描かない理由をログに残す",
                  VarSD.calls == [] and time.time() - began < 5
                  and any("宣言が無いので、表情差分を描かない" in n for n in ctx.notes[seen_notes:]),
                  ctx.notes[seen_notes:])
            loaded["calls"] = 1
            VarSD.calls[:] = []
            expressions.clear(ana)
            ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                       threading.Event(), [].append)

            class AIManager(object):              # 題の画面から始めた回: まだ組まれていないので、ゲームと同じ引数で組む
                built = []

                def __init__(self, app, config):
                    AIManager.built.append((app, config))
                    loaded["on"] = True
            del main.APP.ai_manager
            main.APP.config = {"k": 1}
            main.AIManager = AIManager
            loaded["on"] = False
            expressions.clear(ana)
            VarSD.calls[:] = []
            MOD.REAPPLY_WAIT = 0.3
            try:
                ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                           threading.Event(), [].append)
            finally:
                MOD.REAPPLY_WAIT = real_wait
            check("AIManager が組まれていなければ、ゲームと同じ引数で組んで app に置く",
                  AIManager.built == [(main.APP, {"k": 1})] and isinstance(main.APP.ai_manager, AIManager)
                  and len(VarSD.calls) == 4, (AIManager.built, VarSD.calls))

            # 描いている間に当て直された形: 残りは新しい登録に任せる
            MOD.imagegen.creature_module = lambda *_a: creature
            expressions.clear(ana)
            expressions.clear(bob)                          # Ana の後に Bob が残る形
            mine = MOD.BULK["backfill"]
            handed = []

            def newer(world_path, stop_event, progress, handed_now=False, **kwargs):
                handed.append((world_path, kwargs.get("handed", handed_now)))
                return True
            VarSD.on_call = lambda count: MOD.BULK.__setitem__("backfill", newer) if count == 1 else None
            VarSD.calls[:] = []
            try:
                got = mine(os.path.join(wdir, "world_data.json"), threading.Event(), None)
            finally:
                VarSD.on_call = None
                MOD.BULK["backfill"] = mine
            check("描いている間に当て直されたら、描きかけの人物を終えて、残りを新しい登録に任せる",
                  len(VarSD.calls) == 4 and len(handed) == 1 and got is True, (VarSD.calls, handed, got))
            expressions.seal(bob, expressions.stamp(bob))

            # ---- 追加の表情（state\stable_diffusion\expressions.json）
            added_file = V.added_path(ctx.state_dir)

            def set_added(rows):
                check("追加の表情を書ける", V.save_added(added_file, rows))

            def bulk_once():
                VarSD.calls[:] = []
                ctx.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                           threading.Event(), [].append)
                return list(VarSD.calls)

            def record_of(folder):
                with io.open(os.path.join(expressions.folder_of(folder), expressions.SEAL), encoding="utf-8") as fh:
                    return json.load(fh)
            check("ここまでで Ana は4表情を描き揃え、印に表情ごとのタグが載る",
                  record_of(ana).get("tags") == V.DEFAULT_TAGS and expressions.complete(ana), record_of(ana))
            set_added([{"id": "x1", "name": "shy", "meaning": "照れる", "tags": "blush"}])
            check("追加の表情は登録した一覧に入り、LLM への説明にも載る",
                  expressions.names()[-1] == "shy" and "shy=照れる" in expressions.guide(), expressions.names())
            calls = bulk_once()
            check("表情を足したら、描き揃った人物にも次の一括生成で足した表情だけ描く",
                  len(calls) == 1 and "blush" in calls[0]["prompt"] and expressions.image(ana, "shy") is not None
                  and expressions.image(ana, "smile") is not None and expressions.complete(ana)
                  and record_of(ana)["ids"] == {"shy": "x1"}, (calls, record_of(ana)))
            set_added([{"id": "x1", "name": "blushing", "meaning": "照れる", "tags": "blush"}])
            calls = bulk_once()
            check("名前だけ変えたら描き直さず、ファイル名を変える",
                  calls == [] and expressions.image(ana, "blushing") is not None
                  and not os.path.isfile(expressions.variant_path(ana, "shy")), (calls, record_of(ana)))
            set_added([{"id": "x1", "name": "blushing", "meaning": "顔を赤らめる", "tags": "blush, embarrassed"}])
            calls = bulk_once()
            check("タグを変えた表情だけ描き直す（意味の変更では描かない）",
                  len(calls) == 1 and "embarrassed" in calls[0]["prompt"]
                  and record_of(ana)["tags"]["blushing"] == "blush, embarrassed", (calls, record_of(ana)))
            set_added([{"id": "x1", "name": "blushing", "meaning": "顔を赤らめる", "tags": "blush, nervous smile"}])
            VarSD.empty_at = 1
            try:
                calls = bulk_once()
            finally:
                VarSD.empty_at = None
            check("タグを変えて描き直せなかった表情は、前のタグの絵のまま見せ、描き揃っていない印にする",
                  len(calls) == 1 and expressions.image(ana, "blushing") is not None and not expressions.complete(ana)
                  and record_of(ana)["tags"]["blushing"] == "blush, embarrassed", record_of(ana))
            calls = bulk_once()
            check("次の一括生成で描き直す", len(calls) == 1 and expressions.complete(ana), calls)
            set_added([])
            calls = bulk_once()
            check("一覧から外した表情は描かずに消す",
                  calls == [] and not os.path.isfile(expressions.variant_path(ana, "blushing"))
                  and "blushing" not in expressions.names() and record_of(ana)["expressions"] == list(expressions.VARIANTS),
                  (calls, record_of(ana)))
            os.remove(added_file)
            calls = bulk_once()
            check("置き場が無ければ見本の shy を描く",
                  len(calls) == 1 and "blush, embarrassed" in calls[0]["prompt"]
                  and expressions.image(ana, "shy") is not None and "shy" in expressions.names(), calls)
            set_added([])
            calls = bulk_once()
            check("見本を消して保存したら、描いた見本も消す",
                  calls == [] and not os.path.isfile(expressions.variant_path(ana, "shy")), calls)

            # ---- 既存の立ち絵に顔の描き直しをかける（高度設定の顔の描き直しと「既存の立ち絵にも」が入）
            check("顔の描き直しが切なら、既存の立ち絵の顔は描き直さない（ここまでの一括生成で控えが無い）",
                  not os.path.isfile(os.path.join(ctx.state_dir, "stable_diffusion", MOD.faces.DONE_NAME)))
            on_values = dict(values, FACE_REDRAW=True, FACE_REDRAW_EXISTING=True)
            ctx_on = Ctx(ctx.out_dir, on_values)                 # 同じ state\（呼び方の控え）で設定だけ入れた起動
            MOD.apply(ctx_on)
            ctx.hooks = ctx_on.hooks                             # game_draw も新しい登録を通す
            done_file = os.path.join(ctx.state_dir, "stable_diffusion", MOD.faces.DONE_NAME)
            for path in (ana, bob):
                expressions.seal(path, expressions.stamp(path))  # 差分は描き済み
                Image.new("RGBA", (512, 1024), (90, 90, 90, 255)).save(os.path.join(path, "pixelated_image_original.png"))
            # 会話の立ち絵は工程の代わりと同じ 330x660。顔（元の絵の (250, 200) の辺り）は (161, 129) の辺り
            shown_old = Image.new("RGBA", (330, 660), (90, 90, 90, 255))
            shown_old.paste((10, 10, 200, 255), (135, 105, 145, 115))  # 顔アイコンの位置を見分ける印
            shown_old.save(os.path.join(ana, "reduced_color_image.png"))
            shown_old.crop((130, 100, 190, 160)).save(os.path.join(ana, "face_image.png"))
            old_stamp = expressions.stamp(ana)
            VarSD.calls[:] = []
            progress_seen = []
            ctx_on.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                          threading.Event(), progress_seen.append)
            rows = MOD.faces.load_done(done_file)
            face_calls = [c for c in VarSD.calls if c["strength"] == MOD.faces.DEFAULT_STRENGTH]
            check("一括生成の後に、控えの無い人物の顔を立ち絵と同じ描き方で描き直す（主人公・文の無い人物は除く）",
                  len(face_calls) == 2 and all(c["size"] == (768, 768) for c in face_calls)
                  and face_calls[0]["prompt"].startswith("portrait, face focus, close-up, masterpiece"), face_calls)
            regen = Image.open(os.path.join(ana, "generated_image.png"))
            check("元の絵の顔だけが描き直した顔になる（顔の外は元のまま）",
                  regen.convert("RGB").getpixel((250, 200)) == (200, 30, 30)
                  and regen.convert("RGB").getpixel((10, 900)) == (90, 90, 90) and expressions.stamp(ana) != old_stamp)
            check("背景を抜いた絵・縮めた絵・会話の立ち絵も書き直す",
                  Image.open(os.path.join(ana, "no_bg_image.png")).convert("RGB").getpixel((250, 200)) == (200, 30, 30)
                  and Image.open(os.path.join(ana, "reduced_color_image.png")).convert("RGB").getpixel((161, 129))
                  == (200, 30, 30)
                  and Image.open(os.path.join(ana, "pixelated_image_original.png")).size == (165, 330), None)
            icon = Image.open(os.path.join(ana, "face_image.png"))
            check("顔アイコンは今のアイコンと同じ位置で、描き直した会話の立ち絵から切り直す",
                  icon.size == (60, 60) and icon.convert("RGB").getpixel((31, 29)) == (200, 30, 30), icon.size)
            check("済んだ人物は描き直した後の絵の印で控える",
                  rows.get("w2", {}).get("Ana", {}).get("sha1") == expressions.stamp(ana)
                  and rows["w2"]["Ana"]["result"] == MOD.faces.REDRAWN and "Hero2" not in rows.get("w2", {}), rows)
            check("元の絵が変わったので、表情差分を新しい顔から描き直す",
                  len(VarSD.calls) == 2 + 8 and expressions.image(ana, "smile") is not None
                  and expressions.image(bob, "smile") is not None, len(VarSD.calls))
            check("画面には顔の描き直しと差分の件数を順に渡す",
                  [p.get("label") for p in progress_seen[1:-1]]
                  == ["Ana（顔の描き直し）", "Bob（顔の描き直し）",
                      "Ana（表情差分）", "Bob（表情差分）"], progress_seen)
            VarSD.calls[:] = []
            ctx_on.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                          threading.Event(), [].append)
            check("2回目の一括生成では、済んだ人物に二度かけない", VarSD.calls == [], VarSD.calls)
            MOD.faces.find = lambda image, conf: None
            io.open(os.path.join(ana, "generated_image.png"), "ab").write(b"x")   # 立ち絵が描き直された
            VarSD.calls[:] = []
            ctx_on.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                          threading.Event(), [].append)
            check("立ち絵が描き直された人物はまた対象になり、顔が見つからなければその印で控えて次から外す",
                  MOD.faces.load_done(done_file)["w2"]["Ana"] == {"sha1": expressions.stamp(ana),
                                                                  "result": MOD.faces.NO_FACE}
                  and any("Ana" in n and "顔が見つからない" in n for n in ctx_on.notes),
                  (MOD.faces.load_done(done_file)["w2"]["Ana"], [n for n in ctx_on.notes if "Ana" in n][-3:]))
            check("表情差分も、顔が見つからなければ差分の無い印を書いて次から外す",
                  expressions.sealed(ana) == expressions.stamp(ana) and expressions.image(ana, "smile") is None
                  and any("1 人に描いた" not in n and "顔が見つからない 1 人" in n for n in ctx_on.notes[-1:]),
                  ctx_on.notes[-3:])
            VarSD.calls[:] = []
            seen_notes = len(ctx_on.notes)
            ctx_on.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                          threading.Event(), [].append)
            check("顔が見つからなかった人物は、次の一括生成で数えない（ゲームの用意と顔の検出を繰り返さない）",
                  VarSD.calls == [] and not any("差分だけ描く" in n or "顔を描き直す" in n
                                                for n in ctx_on.notes[seen_notes:]), ctx_on.notes[seen_notes:])
            # 控えが読めない（ロック・破損）なら、全員を未処理と数えて描き直さず、控えも上書きしない
            kept = io.open(done_file, encoding="utf-8").read()
            io.open(done_file, "w", encoding="utf-8").write("{broken")
            io.open(os.path.join(ana, "generated_image.png"), "ab").write(b"y")
            real_retry = MOD.faces.READ_RETRY_WAIT
            MOD.faces.READ_RETRY_WAIT = 0
            ctx_on.warnings[:] = []
            VarSD.calls[:] = []
            try:
                ctx_on.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                              threading.Event(), [].append)
            finally:
                MOD.faces.READ_RETRY_WAIT = real_retry
            check("顔の描き直しの控えが読めなければ、既存の立ち絵の顔を描き直さず、控えも上書きしない",
                  not [c for c in VarSD.calls if c["strength"] == MOD.faces.DEFAULT_STRENGTH]
                  and any(k == "face-done-unreadable" for k, _m in ctx_on.warnings)
                  and io.open(done_file, encoding="utf-8").read() == "{broken", ctx_on.warnings)
            io.open(done_file, "w", encoding="utf-8").write(kept)
            MOD.faces.find = lambda image, conf: ((200, 150, 100, 100), 2.0)
            zeno = expressions.character_dir("w", "Zeno")
            ctx_on.hooks[entrance](game_draw, "w", "Zeno", "cat", "masterpiece, full body, young man")
            join_jobs()
            check("顔の描き直しが入なら、新しく描いた立ち絵も保存後の絵の印で控える（一括生成で二度かけない）",
                  MOD.faces.load_done(done_file).get("w", {}).get("Zeno", {}).get("sha1") == expressions.stamp(zeno),
                  MOD.faces.load_done(done_file).get("w"))
            check("顔の描き直しで例外を出していない", ctx_on.errors == [], ctx_on.errors)

            # ---- 強さの「モデルごとに変える」は、ゲームの部品で描くときも建っているチェックポイントの名前で当てる
            by_values = dict(values, EXPRESSION_STRENGTH_BY_MODEL="ponyish=0.42")
            ctx_by = Ctx(ctx.out_dir, by_values)
            MOD.apply(ctx_by)
            ctx.hooks = ctx_by.hooks
            saved_family = dict(MOD.FAMILY)
            MOD.FAMILY.update({"name": "sdxl", "path": "C:/models/ponyish_v1.safetensors"})
            try:
                VarSD.calls[:] = []
                ctx_by.hooks[entrance](game_draw, "w", "Zeno", "cat", "masterpiece, full body, young man")
                join_jobs()
            finally:
                MOD.FAMILY.clear()
                MOD.FAMILY.update(saved_family)
            check("ゲームの部品で描く差分も、モデルごとに変える行がチェックポイントの名前に当たる",
                  len(VarSD.calls) == 5 and all(c["strength"] == 0.42 for c in VarSD.calls[1:]),
                  [c["strength"] for c in VarSD.calls])

            # ---- 差分を描けない設定（表情のタグが全部空）なら、一括生成で差分の人物を数えない
            empty_values = dict(values, **{key: "" for _expression, key in V.TAGS})
            ctx_empty = Ctx(ctx.out_dir, empty_values)
            MOD.apply(ctx_empty)
            ctx.hooks = ctx_empty.hooks
            expressions.clear(ana)
            loaded["calls"] = 0
            loaded["on"] = False
            VarSD.calls[:] = []
            ctx_empty.hooks[MOD.BULK_TARGET](game_bulk, object(), os.path.join(wdir, "world_data.json"),
                                             threading.Event(), [].append)
            check("表情のタグが全部空なら、差分の無い人物が居てもゲームの用意へ進まず、描けなかったと数えない",
                  VarSD.calls == [] and loaded["calls"] == 0
                  and not any("描けなかった" in n for n in ctx_empty.notes)
                  and any("表情のタグが全部空なので描かない" in n for n in ctx_empty.notes),
                  (VarSD.calls, loaded, [n for n in ctx_empty.notes if "一括生成" in n][-3:]))
            check("強さと空のタグの回で例外を出していない", ctx_by.errors == [] and ctx_empty.errors == [],
                  (ctx_by.errors, ctx_empty.errors))
        finally:
            MOD.imagegen.creature_module = real_creature
            main.__dict__.pop("AIManager", None)
        check("例外を出していない", ctx.errors == [], ctx.errors)
    finally:
        MOD.faces.find = real_find
        if saved_env is None:
            os.environ.pop("IML_INSTANTALE_DATA", None)
        else:
            os.environ["IML_INSTANTALE_DATA"] = saved_env
        main.__dict__.pop("InstantaleApp", None)
        if saved_app is None:
            main.__dict__.pop("APP", None)
        else:
            main.APP = saved_app
        expressions.reset()
        sys.modules.pop(manager.__name__, None)
        sys.modules.pop(creature.__name__, None)
        shutil.rmtree(base, ignore_errors=True)

print()
if failures:
    print("失敗 {} 件: {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("失敗 0 件")
print("すべて通った")
