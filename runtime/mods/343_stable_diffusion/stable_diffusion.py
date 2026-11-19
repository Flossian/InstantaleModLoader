# -*- coding: utf-8 -*-
r"""Stable Diffusion の解像度・サンプラー・モデル・プロンプトを差し替える。

ゲームは生成の解像度もサンプラーも設定画面から変えられず、
立ち絵を 256x512 で描いてから 512x1024 で描き直し、背景を 1024x512 で描く。
この MOD は**どのバックエンドでも通る1つの出口**でその値を書き換える。

ゲームの側の読み方は GAME.md §2.33、残っている確認は VERIFICATION.md §3.65。

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

同じ出口で、今の絵を下絵にした描き直し（img2img）と、2つのモデルで続けて描く
（txt2img→img2img）も行う。今の絵は入口で読んで印と一緒に控え、出口で寸法を合わせて渡す。
決め方と下絵の形は `img2img.py`。

同じ出口で、会話の立ち絵の表情差分も描く（`variants.py`）。表情を見せる MOD がローダの窓口 `expressions` に
宣言しているときだけ、描き終えた立ち絵の顔の周りを表情のタグを足して描き直し、立ち絵のフォルダに置く。
一括生成では立ち絵に続けて描き、ほかの場面では立ち絵を先に返して裏で描く。

版28: `img2img.py` の `hasattr` を既定値付きの `getattr` に替えた（TECH.md §6）。設定の窓のフックの第1引数を
`self` と書くようにした（受けていたのは HUD そのもので、働きは変わらない）。使っていない import を外した（`iniimport.py`）。
"""
import inspect
import os
import sys
import threading
import time

from instantale_modloader import expressions
from instantale_modloader import frames
from instantale_modloader import imagegen
from instantale_modloader import patch as loader_patch
from instantale_modloader import saves
from instantale_modloader import ui

from . import assets
from . import faces
from . import humans
from . import img2img
from . import live as hot
from . import presets
from . import rules as rulebook
from . import sizes
from . import variants
from . import tagbook

LOG_BASENAME = "stable_diffusion.log"

#: 種類と段の印（スレッドごと）。`apply()` の中に置くと、当て直しを跨いだ回に届かない
#: （VERIFICATION_LOG.md §2.86）。
MARKS = threading.local()

#: 建てるときに見分けたモデルの系統。印と同じ理由でモジュール側に置く。
FAMILY = {"name": None, "path": None}

#: `sys.meta_path` に置いた観測者の目印（入れ直しで積み上げないため）。
OBSERVER_MARK = "_mod_343_stable_diffusion"

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
#: SDXL のモデルへ移るときはここを指す（系統は揃えること。DOC.md）。
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
#: モデルを差し替えるときはこちらも入にする（VERIFICATION.md §3.65）。
#: 規則（付け替え・追加）で足したタグは外さない。
STRIP_LORA = False

#: diffusers 系（`diffusers_openvino` など）でも寸法を変える。
#: **既定は切**。この一族は形を固定して変換したモデルを回すので、
#: 寸法を変えると作り直しが走り、実機では RAM を使い切って
#: SSD へページングを始めた（VERIFICATION.md §3.65）。sdcpp 系には関係しない。
DIFFUSERS_RESIZE = False

# ------------------------------------------------------------------------

#: ゲームの用意（`set_ai_models`）をメインスレッドで走らせるとき、取られるのを待つ間に様子を見る間隔（秒）。
#: 待つのは最長でこの 30 倍（`ctx.run_on_main_thread`）。
PREPARE_WAIT = 5.0

#: ゲームが一括生成の画面へ件数を1回も渡さずに終えた回に、差分だけ描いた後で渡す終わりの形
#: （ゲームが終わりに渡す形と同じ鍵。GAME.md の一括生成）。
BULK_FINAL = {"status": "complete", "completed": 0, "remaining": 0, "failed": 0}

#: 出口の候補。生きたパイプラインの型が自分の MRO の中に持つものだけ包む。
EXIT_METHODS = ("generate_image", "__call__")

#: 建つ前でも包める出口。sdcpp の一族は manager がクラス（`StableDiffusion`）を
#: モジュール変数に持つので、パイプラインが無くてもクラスから包める（版26）。
#: `__call__` は名前がありふれているので、生きたパイプラインの型からしか包まない。
EARLY_EXIT_METHOD = "generate_image"

#: 種類ごとに割り当てるカスタムプリセットの名前（版26）。空ならプリセットを使わずに描く。
#: 割り当てた種類は、そのプリセットのチェックポイントで建てた別の部品と、そのプリセットの
#: 寸法・サンプラー・LoRA タグの除去・安全弁で描く（VERIFICATION.md §3.65）。
#: 使うかどうか。上級者向けなので既定は切（もう1つモデルを載せるぶん VRAM を使う）。
KIND_PRESETS = False
PORTRAIT_PRESET = ""
ENEMY_PRESET = ""
BACKGROUND_PRESET = ""

#: 今の絵を下絵にした描き直し（img2img）。使うかどうかと、種類ごとのプリセット
#: （空でゲーム既定のモデル）・強さ。入なら再生成のボタンに出る（再生成でだけ使う）。
#: 既定は切（ゲームのまま）。sdcpp 系の出口だけで効く（`img2img.py`）。
IMG2IMG = False
IMG2IMG_PORTRAIT_PRESET = ""
IMG2IMG_PORTRAIT_STRENGTH = img2img.REDRAW_STRENGTH
IMG2IMG_ENEMY_PRESET = ""
IMG2IMG_ENEMY_STRENGTH = img2img.REDRAW_STRENGTH
IMG2IMG_BACKGROUND_PRESET = ""
IMG2IMG_BACKGROUND_STRENGTH = img2img.REDRAW_STRENGTH

#: 2つのモデルで続けて描く（txt2img→img2img）。使うかどうかと、種類ごとの
#: 1つ目と2つ目のプリセット（空でゲーム既定のモデル）・2つ目の強さ・
#: 通常の生成（新しい NPC・場所・敵）でも使うか（`_AUTO`）。入なら再生成のボタンに出る。上級者向けで既定は切。
CHAIN = False
CHAIN_PORTRAIT_FIRST = ""
CHAIN_PORTRAIT_SECOND = ""
CHAIN_PORTRAIT_STRENGTH = img2img.CHAIN_STRENGTH
CHAIN_PORTRAIT_AUTO = False
CHAIN_ENEMY_FIRST = ""
CHAIN_ENEMY_SECOND = ""
CHAIN_ENEMY_STRENGTH = img2img.CHAIN_STRENGTH
CHAIN_ENEMY_AUTO = False
CHAIN_BACKGROUND_FIRST = ""
CHAIN_BACKGROUND_SECOND = ""
CHAIN_BACKGROUND_STRENGTH = img2img.CHAIN_STRENGTH
CHAIN_BACKGROUND_AUTO = False

#: 人間の敵（闘技場の相手など）を立ち絵の描き方（プリセット・img2img・続けて描く）で、
#: 同じ高さの縦長に描いて正方形へ埋め戻す。既定は切（ゲームのまま）。sdcpp 系だけ（humans.py）。
HUMAN_ENEMY_AS_PORTRAIT = False

#: 描き終えた立ち絵の顔だけを切り出して描き直し、貼り戻す。既定は切（ゲームのまま）。
#: プリセットが空なら、その立ち絵を描いたプリセットで描く。sdcpp 系だけ（faces.py）。
FACE_REDRAW = False
FACE_REDRAW_PRESET = ""
FACE_REDRAW_STRENGTH = faces.DEFAULT_STRENGTH
FACE_REDRAW_EXISTING = False

#: 会話の立ち絵の表情差分の強さとタグ（variants.py）。描くかは設定でなく、
#: 表情を見せる MOD がローダの窓口 `expressions` に宣言しているかで決まる。sdcpp 系だけ。
#: 強さは「モデル名で分ける」の行（`条件=強さ`）の最初に当たったもの、無ければ系統で SD1.5 か SDXL・その他。
EXPRESSION_STRENGTH_SD15 = 0.5
EXPRESSION_STRENGTH = 0.35
EXPRESSION_STRENGTH_BY_MODEL = ""
EXPRESSION_SMILE = "light smile, closed mouth"
EXPRESSION_ANGRY = "annoyed, slight frown"
EXPRESSION_SAD = "sad, worried"
EXPRESSION_SURPRISED = "surprised, :o"

#: 一括生成の本体（`InstantaleApp`）。走っているあいだは、表情差分を立ち絵に続けて描く（裏へ回さない）。
BULK_TARGET = "__main__:InstantaleApp.run_world_bulk_generation"

#: 一括生成が走っている数（`depth`）と、一括生成の後に差分だけ描く関数（`backfill`）。
#: `sys` に置いて当て直しをまたぐ（一括生成を始めた後にローダが MOD を当て直す。variants.shared）。
BULK = variants.shared()

#: ローダの窓口 `expressions` に表情の一覧を登録するときの名前。
EXPRESSION_OWNER = "343_stable_diffusion"

#: 表情差分を通すゲームの工程（`image_generation_creature` の写し。GAME.md §2.30）。
VARIANT_STEPS = ("pixel_art_process", "reduce_image_colors")
#: `InstantaleApp` が `AIManager` を持つ属性（綴りの違う2つが在った。`210_probe_character_state` の属性の記録）。先頭が、組んだときに置く先。
AI_MANAGER_ATTRS = ("ai_manager", "ai_mamanger")
#: ゲームの用意を呼んだ後、ローダの当て直しを待つ秒数の上限（当て直しは用意の数秒後に来る）。
REAPPLY_WAIT = 60.0

#: ゲームが立ち絵の工程に渡した引数（モジュール名 → {関数名: (位置引数, 名前つき引数)}。絵を除く）。
#: 表情差分を同じ引数で通すために、立ち絵の入口の中の最初の1回を控える（当て直しをまたぐ）。
STEP_CALLS = BULK["steps"]

#: ゲームが部品を建てたときの引数。種類ごとの部品を同じ作りで建てるために控える。
#: 部品のクラスに持たせる（注入のたびにモジュールは作り直されるが、クラスは同じ）。
INIT_ATTR = "_mod343_init_kwargs"

#: 種類ごとに建てた部品の置き場（部品のクラスに持たせる。注入し直しても二重に建てない）。
PIPES_ATTR = "_mod343_kind_pipes"

#: 建てられなかった部品の鍵（部品のクラスに持たせる）。ネイティブの中で落ちた後は
#: 建て直すたびに壊れ方が広がり、ゲームの部品も描けなくなったので、同じプロセスでは建て直さない。
FAILED_ATTR = "_mod343_failed_pipes"

#: 343 が書き換える前のゲームの材料（manager のモジュールに持たせる）。
#: プリセットのチェックポイントが空のとき（ゲームのまま）に使う。
GAME_ATTR = "_mod343_game_materials"

#: 設定の画面（ゲームを終了する・タイトルに戻る・画像の再生成）を開け閉めする関数。
#: 開いた直後に「画像の再生成」を描き方ごとのボタンへ分ける。
SETTING_WINDOW_TARGET = "scripts.hud.new_hud:InstanTaleHUD.toggle_setting_window_visibility"

#: 「画像の再生成」が描く作業のスレッドの本体（会話の相手・背景・戦闘の敵の絵を描き直す）。
#: ここでボタンで選んだ描き方をスレッドの印にする。
REGEN_THREAD_TARGET = "__main__:InstantaleApp._regenerate_current_image_thread"

#: ボタンで選んだ描き方を、押したときから再生成のスレッドの頭まで控える。
#: ゲームの再生成ボタンを直接押した回は空で、txt2img（ふだんどおり）になる。
REGEN = {"mode": None}

#: いちばん新しい apply() の `attach`。用済みになった apply() の包みが、
#: 建て終わった後の当て直しをこちらへ渡す（版26。VERIFICATION.md §3.65）。
#: 注入のたびにモジュールは作り直されるので、渡す相手は `sys.modules` の今の同名モジュールから引く。
HANDOFF = {"attach": None}

#: パイプラインを建てる関数（manager のモジュール関数）。
#: 建てる直前に材料を書き直す口。import の観測（`_ImportObserver`）は
#: **最初の1回**にしか効かない（同じプロセスで2回目のワールド選択をしても
#: import は走らない）ので、建てるたびに効くこちらを本線にする。
PIPELINE_FUNC = "load_sd_pipeline"

#: 段の印を立てる上の層（`stable_diffusion_manager` の関数）。
#: **関数から段を決め打ちしない。**
#: `generate_image_real_lcm` は背景だけのものではなく、
#: `diffusers_openvino` ではキャラの絵もここを通る（実機）。
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

#: 背景の入口（`image_generation_background` の関数）。今の絵を控えるために包む。
#: 種類の印はこれが無くても呼び出し元のファイル名で分かる（`KIND_HINTS`）。
BACKGROUND_FUNCS = ("generate_background_image", "generate_and_save_background")

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
    （実機: 敵は 512x512 を lcm / 5 / cfg 1 で1枚）。
    `diffusers_openvino` ではキャラもここを通る。
    種類では決めない（種類で決めると、同じ呼び出しに違う設定が当たる）。
    """
    if func is None:
        return None
    if func == LCM_FUNC:
        return "lcm"
    return "stage2" if func == SECOND_STAGE_FUNC else "stage1"


def worlds_dir():
    r"""今の絵を探す親（`%LOCALAPPDATA%\Darmabeko\Instantale\worlds`）。検査は仮の置き場へ差し替える。"""
    return saves.worlds_dir()


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


def below_ours(func):
    """出口の包みの連鎖のうち、この MOD の層のすぐ下（ふだんの描き方で出口の包みに `orig` として渡る物）。

    差分だけ描くときは出口を通さずに `draw` を呼ぶ。素の関数（`unwrapped`）を渡すと、この MOD より下に
    ほかの MOD やローダが掛けた層を素通りするので、ふだんの描き方と同じ層を渡す。
    この MOD の層が無い・その下にもこの MOD の層が残っている（前の世代の層）なら素の関数。
    """
    current, mine = func, None
    for _ in range(32):
        if current is None or not loader_patch.is_ours(current):
            break
        if getattr(current, loader_patch.HOOK_MODULE_MARK, None) == __name__:
            if mine is not None:
                return unwrapped(func)
            mine = current
        current = getattr(current, "__original__", None)
    below = getattr(mine, "__original__", None) if mine is not None else None
    return below if below is not None else unwrapped(func)


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

    外すのは次の apply() だけなので、343 を切って注入し直しても残る。
    `stale()` が真になった後は何も包まずに素通しする（名前が合うモジュールは数百あり、
    用済みの観測者がそのローダを包み続けないため）。
    `find_spec` の中で `sys.meta_path` から自分を抜くと、走査中の一覧がずれて次の finder が飛ばされる。
    """

    def __init__(self, after, stale=None):
        self._after = after
        self._stale = stale
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
            if self._stale is not None and self._stale():
                return None
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
    #: 顔だけ描き直すときの検出器の置き場（同梱。ctx.mod_dir はフックの中では読めない）。
    face_models = faces.models_dir(frames.attr(ctx, "mod_dir", None))
    for line in live.describe():
        write(line)

    def expression_rows():
        """会話の表情の一覧（基本表情と追加の表情のうちタグのあるもの。`variants.entry_rows`）。

        ローダの窓口が一覧を使うたびに呼ぶ。設定と追加の表情が変わっていれば読み直すので、次の会話から効く。
        """
        live.refresh()
        return variants.entry_rows(live.variants)

    expressions.define(EXPRESSION_OWNER, ctx, expression_rows)
    if not live.hot():
        write("hot reload: off (場所が控えられない。設定は注入し直しで効く)")

    # v1.18.0（916）の置き場 state\models\ に残ったモデルを state\stable_diffusion\models\ へ移す。
    for path in assets.migrate(ctx.state_dir):
        write("置き場を移した: {}".format(path))

    #: 建つ前に書く材料。**熱くしない**（建つ瞬間にしか効かない）。
    #: 古い置き場を指したままの設定は新しい置き場へ読み替える。
    materials = [(attr, assets.relocate(ctx.setting(name), ctx.state_dir))
                 for name, attr in MATERIALS if str(ctx.setting(name) or "").strip()]

    #: diffusers 系で実際に当てた寸法。2つ以上になったら知らせる
    #: （寸法ごとにモデルが作り直され、ゲームを閉じるまで残る。VERIFICATION.md §3.65）。
    diffusers_sizes = set()
    no_taesd = bool(ctx.setting("DISABLE_TAESD"))
    no_vae = bool(ctx.setting("DISABLE_VAE"))

    # **「これから書く値」であって、書いた記録ではない。**
    # 実際に書いたときは `<モジュール名>: model_path_anime '旧' -> '新'` の形で出る。
    # 材料はパイプラインが建つ瞬間にしか効かないので、既に建っていれば次の起動まで届かない
    # （試作の提供先で、この2つを取り違えて「差し替えたのに sd15 と出る」になった）。
    for attr, value in materials:
        write("material (次に建てるとき) {} -> {!r}".format(attr, value))
    if no_taesd:
        write("material (次に建てるとき) taesd_path -> '' (VAE で復号させる)")
    if no_vae:
        write("material (次に建てるとき) vae_path -> '' (VAE を渡さない)")

    #: ゲームが立ち絵を描いたときの呼び方の控え（一括生成で差分だけ描くときに使う。variants.TEMPLATE_NAME）。
    #: 置き場は apply の間に決める（ctx の値はフックの中では読めない）。
    template_path = os.path.join(ctx.state_dir, presets.CUSTOM_DIRNAME, variants.TEMPLATE_NAME)
    template = {"value": variants.load_template(template_path)}
    #: この登録がゲームの用意（set_ai_models）を呼んだか（呼んだ回は、描く前にローダの当て直しを待つ）。
    prepared = {"now": False}
    #: 顔の描き直しを済ませた立ち絵の控え（一括生成で既存の立ち絵にかけ直さないため。faces.DONE_NAME）。
    done_path = os.path.join(ctx.state_dir, presets.CUSTOM_DIRNAME, faces.DONE_NAME)
    #: sdcpp の出口ごとの描く関数（`make_exit_hook` の `draw`）。差分だけ描くときに出口を通さず呼ぶ。
    exit_draws = {}
    #: 呼び方の控えの読み合わせと書き戻しの錠（立ち絵の出口と工程のフックが別のスレッドから呼ぶ）。
    template_lock = threading.Lock()

    def remember_template(stage=None, arguments=None, steps=None):
        """呼び方の控えを更新する。変わったときだけ書く。"""
        try:
            with template_lock:
                now = dict(template["value"] or {})
                if stage is not None:
                    now["stage"] = stage
                    now["params"] = variants.template_params(arguments)
                if steps:
                    merged = dict(now.get("steps") or {})
                    merged.update((name, row) for name, row in steps.items() if row is not None)
                    now["steps"] = merged
                if now != template["value"] and variants.save_template(template_path, now):
                    template["value"] = now
        except Exception:
            ctx.log_exc("image quality: remembering the portrait call failed")

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

    def background_modules():
        """背景の入口（`image_generation_background`）を持つモジュール。"""
        return [name for name in sys.modules
                if name.endswith(".image_generation_background")]

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

    def make_kind_hook(kind, func_name=None, module_name=None):
        """入口で種類の印を立てる。描き直しが入の種類なら、今の絵もここで読んで控える。

        入口で読むのは、世界と名前（場所）が入口の引数にしか無く、出口では分からないため。
        ゲームは描き終えてから同じ名前で絵を書き直すので、入口の時点では今の絵が残っている（GAME.md §2.30）。
        今の絵を探さない入口（`img2img.ENTRANCES` に無い）は、外側の控えを持ち越さない。

        立ち絵の入口は、ゲームが絵を保存し終えた後（入口を抜けるとき）に表情差分の仕事を始める
        （出口の `finish` が描く材料を `MARKS.variant` に控えた回だけ）。
        顔を描き直した回（`MARKS.refined`）は、保存された絵の印を控え（`faces.DONE_NAME`）に書く
        （一括生成で既存の立ち絵へ二重にかけない）。
        """
        entrance = img2img.ENTRANCES.get(func_name)

        def hook(orig, *args, **kwargs):
            before = frames.attr(MARKS, "kind", None)
            before_source = frames.attr(MARKS, "source", None)
            before_variant = frames.attr(MARKS, "variant", None)
            before_steps = frames.attr(MARKS, "steps", None)
            before_refined = frames.attr(MARKS, "refined", None)
            MARKS.kind = kind
            MARKS.source = None
            MARKS.variant = None
            MARKS.steps = set()
            MARKS.refined = None
            if entrance is not None:
                MARKS.source = current_image(kind, args, kwargs, entrance)
            # 作成画面かは描き始めに見る（描いている間に「人生を始める」「戻る」で画面が替わりうる）
            creating = kind == "portrait" and ui.on_character_create()
            captured = None
            refined = None
            try:
                result = orig(*args, **kwargs)
                captured = frames.attr(MARKS, "variant", None)
                refined = frames.attr(MARKS, "refined", None)
                return result
            finally:
                MARKS.kind = before
                MARKS.source = before_source
                MARKS.variant = before_variant
                MARKS.steps = before_steps
                MARKS.refined = before_refined
                if refined and kind == "portrait":
                    try:
                        world = frames.arg(args, kwargs, "world_name", 0)
                        name = frames.arg(args, kwargs, "name", 1)
                        folder = expressions.character_dir(world, name)
                        remember_done(world, name, expressions.stamp(folder), refined)
                    except Exception:
                        ctx.log_exc("image quality: recording the face redraw failed")
                if captured is not None:
                    try:
                        start_variants(module_name, frames.arg(args, kwargs, "world_name", 0),
                                       frames.arg(args, kwargs, "name", 1), captured, creating)
                    except Exception:
                        ctx.log_exc("image quality: starting the expression variants failed")
        return hook

    def make_step_hook(module_name, step):
        """立ち絵の工程（縮小・減色）の引数を、立ち絵の入口の中の最初の1回だけ控える。働きは変えない。

        `131_` より外側に置く（mod.json の `after`）。`131_` の高画質化は減色の元の関数を呼ばずに返すので、
        内側に居ると減色の呼び方が控えられない（VERIFICATION.md §3.132）。
        """
        def hook(orig, image, *args, **kwargs):
            steps = frames.attr(MARKS, "steps", None)
            if frames.attr(MARKS, "kind", None) == "portrait" and isinstance(steps, set) \
                    and step not in steps:
                steps.add(step)
                STEP_CALLS.setdefault(module_name, {})[step] = (tuple(args), dict(kwargs))
                remember_template(steps={step: variants.step_call(args, kwargs)})
            return orig(image, *args, **kwargs)
        return hook

    def current_image(kind, args, kwargs, entrance):
        """設定の画面のボタンで img2img を選んだ回なら、今の絵を読んで返す。

        選んでいない・絵が無い・読めないなら None。
        """
        if imagegen.method_now() != "img2img":
            return None
        _kind, name_arg, name_index = entrance
        world = frames.arg(args, kwargs, "world_name", 0)
        name = frames.arg(args, kwargs, name_arg, name_index)
        path = img2img.source_path(worlds_dir(), kind, world, name)
        if not path or not os.path.isfile(path):
            return None
        try:
            return img2img.load_image(path)
        except Exception as exc:
            warn("img2img-read", "今の絵を読めなかったので、ふだんどおり描く（{}: {}: {}）".format(
                path, type(exc).__name__, exc))
            return None

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

    def wanted(kind, stage, params, route=None):
        """この1回で書き換える引数。触らないなら空の辞書。

        `params` は名前で引ける今の引数。
        サンプラーは**設定が既定と同じ項目には触らない**。
        `route` はその種類に割り当てたプリセット（`kind_route`）。在ればその値とモデルで決める。
        """
        changes = {}
        conf = route["cfg"] if route else main_cfg()
        scales, samplers, rules = conf["scales"], conf["samplers"], live.rules
        # 規則の `model` で絞る相手（描く部品の系統とチェックポイント）。
        model = route["model"] if route else (FAMILY.get("name"), FAMILY.get("path"))
        scale = scales.get(kind, 1.0)
        if rulebook.skip_upscale(rules, kind, params.get("prompt"), model):
            scale = 1.0
        new = sizes.scaled(params.get("width"), params.get("height"), scale,
                           conf.get("long_caps", {}).get(kind))
        if new is not None:
            changes["width"], changes["height"] = new
        # 人間の敵は、ゲームの否定から種族とぶつかる語を外してから規則を当てる（humans.py）
        race = humans.race_of(params.get("prompt")) if kind == "enemy" else None
        for name, target in (("prompt", "prompt"), ("negative_prompt", "negative")):
            body = params.get(name)
            if not isinstance(body, str):
                continue
            base = humans.strip_negative(body, race) if target == "negative" else body
            book = tag_book(rules, kind, target, model)
            got = rulebook.rewrite(rules, kind, target, base, body, model, book)
            if got is None and base != body:
                got = base
            if got is not None:
                changes[name] = got
            if target == "prompt" and book is not None:
                note_tags(rulebook.game_text(rules, kind, target, body, model, book), book)
        if conf["strip_lora"]:
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
        add_safety(stage, params, changes, conf, model[0])
        # 検証の道具だけが使う口。そのスレッドに種の印があれば種を固定する
        # （ゲームは種 -1 で毎回変わるので、プロンプトの差だけを見比べるため。VERIFICATION.md §3.65）。
        seed = frames.attr(MARKS, "seed", None)
        if seed is not None and "seed" in params:
            changes["seed"] = seed
        return changes

    def is_human_enemy(kind, params):
        """ゲームの敵の本文の種族が人間か（humans.py）。"""
        return kind == "enemy" and humans.is_human(humans.race_of(params.get("prompt")))

    def as_portrait(kind, params):
        """立ち絵の描き方で描く人間の敵か（`HUMAN_ENEMY_AS_PORTRAIT`）。"""
        return bool(live.values.get("HUMAN_ENEMY_AS_PORTRAIT")) and is_human_enemy(kind, params)

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

    def main_cfg():
        """道具画面の各タブの値（`live`）を、プリセットの値と同じ形で。"""
        return {"scales": live.scales, "long_caps": live.long_caps, "samplers": live.samplers,
                "strip_lora": live.strip_lora, "safety": live.safety}

    def add_safety(stage, params, changes, conf, family):
        """モデルの系統と食い違う指定を、壊れない側へ寄せる。

        今日の実機で踏んだ2つの壊れ方をそのまま塞ぐ（VERIFICATION.md §3.65）。
        SDXL に SD1.5 用の LoRA が当たるとプロセスごと落ち、
        SDXL を LCM の設定（5 steps / cfg 1.0）で回すとマゼンタに潰れる。
        設定で明示された値は動かさない（**寄せるのは素のままの項目だけ**）。
        """
        samplers = conf["samplers"]
        if conf["safety"] != "auto" or family is None:
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
        """ログの1項目。プロンプトは本文を出さず、字数と LoRA の出入りだけ出す。下絵は寸法だけ。"""
        if name == "init_image":
            size = frames.attr(value, "size", None)
            return "下絵 {}x{}".format(*size) if size else "下絵"
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

    #: タグの一覧の置き場（`ctx` の値は apply() の間しか確かでないので控える）。
    state_dir = ctx.state_dir

    #: 知らせ済みの、タグでない言い方。1プロセスに1回ずつ。
    noted = set()

    def tag_book(rules, kind, target, model):
        """この回にタグの照合か語の置き換えの行が効くなら、タグの一覧。無ければ None。

        語の置き換えだけの回も引くのは、タグに無い言い方の知らせ（`note_tags`）が
        次の置き換えを足す手掛かりだから。一覧が無いと知らせるのは照合の行がある回だけ。
        """
        tags_on = any(True for _row in rulebook.active(rules, "tags", kind, target, model))
        words_on = any(True for _row in rulebook.active(rules, "words", kind, target, model))
        if not (tags_on or words_on):
            return None
        book = tagbook.load(state_dir)
        if book is None and tags_on:
            warn("tagbook-missing",
                 "タグの照合の行があるのに Danbooru のタグ一覧が無い（{}）。"
                 "道具画面の「モデル別」から取る".format(tagbook.path_of(state_dir)))
        return book

    def note_tags(body, book):
        """タグでない言い方に、よく使われるタグの語が紛れていたら知らせる（語の置き換えの手掛かり）。"""
        for core, words in rulebook.tag_notes(body, book):
            if core.lower() in noted:
                continue
            noted.add(core.lower())
            write("danbooru: タグに無い言い方 '{}'（中のタグの語: {}）".format(core, ", ".join(words)))

    #: 全文を出した (種類, 段, チェックポイント, 規則の世代)。
    #: 規則を読み直すと `live.rules` は別の物になるので、書き換えた規則でもう1回出る。
    shown = set()

    def show_prompts(kind, stage, params, changes, which=None):
        """書き換えたプロンプトの前後を全文で出す。種類・段・モデル・規則の組ごとに最初の1回だけ。

        ふだんの行は字数だけなので、規則が思った形に当たっているかはここで見る。
        敵の絵はゲームが prompts.json を残さないので、送った文はここでしか見えない。
        """
        if "prompt" not in changes and "negative_prompt" not in changes:
            return
        key = (kind, stage, which or FAMILY.get("path"), id(live.rules))
        if key in shown:
            return
        shown.add(key)
        for name in ("prompt", "negative_prompt"):
            if name in changes:
                write("  {} [{}/{}] 前: {}".format(name, kind, stage, params.get(name)))
                write("  {} [{}/{}] 後: {}".format(name, kind, stage, changes[name]))

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
                # 実機では RAM を使い切ってページングに入った（VERIFICATION.md §3.65）。
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

    # ------------------------------------------- 種類ごとのプリセット（版26）

    #: 置き場（`ctx` の値は apply() の間しか当てにならないので控える）。
    state_dir = ctx.state_dir
    game_dir = ctx.game_dir

    #: チェックポイントの系統（ヘッダを毎回読まないため）。
    families = {}

    def full_path(path):
        path = str(path or "")
        return path if not path or os.path.isabs(path) else os.path.join(game_dir, path)

    def family_at(path):
        full = full_path(path)
        if full not in families:
            families[full] = sizes.family_of(full) if full else None
        return families[full]

    def game_materials():
        """343 が書き換える前のゲームの材料（manager のモジュールに控えたもの）。"""
        for name in managers():
            got = frames.attr(sys.modules.get(name), GAME_ATTR, None)
            if got:
                return got
        return {}

    def kind_route(kind):
        """その種類に割り当てたプリセット。割り当てが無い・プリセットが無いなら None。"""
        return preset_route(live.kind_presets.get(kind) or "", kind)

    def preset_route(name, where):
        """名前のカスタムプリセットで描くための組。名前が空・プリセットが無いなら None。

        値は道具画面の各タブの値にプリセットの値を重ねたもの（版の違いで欠けた項目はいまの値。`presets.overlay`）。
        `where` は知らせに出す使い道（種類の名前や「描き直し」）。
        """
        if not name:
            return None
        entry = presets.find_custom(state_dir, name)
        if entry is None:
            warn("kind-preset-missing-" + name,
                 "{} に割り当てたプリセット「{}」が見つからない。プリセットを使わずに描く".format(where, name))
            return None
        got = entry["values"]
        merged = presets.overlay(live.values, got)
        game = game_materials()
        checkpoint = str(got.get("CHECKPOINT_PATH") or "") or game.get("model_path_anime", "")
        taesd = "" if got.get("DISABLE_TAESD") else (
            str(got.get("TAESD_PATH") or "") or game.get("taesd_path", ""))
        vae = "" if got.get("DISABLE_VAE") else (
            str(got.get("VAE_PATH") or "") or game.get("vae_path", ""))
        lora = str(got.get("LORA_DIR") or "") or game.get("lora_dir", "")
        return {"name": name, "label": "プリセット「{}」".format(name), "cfg": hot.derive(merged),
                "model": (family_at(checkpoint), checkpoint),
                "materials": {"model_path": checkpoint, "taesd_path": taesd,
                              "vae_path": vae, "lora_model_dir": lora}}

    def game_route():
        """ゲーム既定のモデル（モデルの差し替えの前の、ゲームが選んだ材料）で描く組。

        高度設定でプリセットを空にした回に使う。寸法・サンプラーは道具画面の各タブの値。
        モデルの差し替えが空なら、材料がゲームの部品と同じなのでゲームの部品で描く（`route_pipe`）。
        ゲームの材料がまだ分からないなら None（ゲームの部品で描く）。
        """
        game = game_materials()
        checkpoint = game.get("model_path_anime", "")
        if not checkpoint:
            return None
        return {"name": "ゲーム既定のモデル", "label": "ゲーム既定のモデル", "cfg": main_cfg(),
                "model": (family_at(checkpoint), checkpoint),
                "materials": {"model_path": checkpoint, "taesd_path": game.get("taesd_path", ""),
                              "vae_path": game.get("vae_path", ""),
                              "lora_model_dir": game.get("lora_dir", "")}}

    def method_route(name, where):
        """描き直しと続けて描くのプリセットの組。空か見つからないならゲーム既定のモデル（`game_route`）。

        見つからないときも道具画面はゲーム既定のモデルで描くと知らせるので、モデルの差し替えへは落とさない。
        """
        return (preset_route(name, where) if name else None) or game_route()

    def routes_in_use():
        """いま割り当てられているプリセットの組（種類ごと・描き直し・続けて描く）。

        描き直しと続けて描くは、使う設定が入のとき全種類を数える（どの種類を描き直すかはゲームが決める）。
        """
        got = [kind_route(kind) for kind in img2img.KINDS]
        for kind, conf in live.img2img["redraw"].items() if live.img2img["img2img_on"] else ():
            got.append(method_route(conf["preset"], "描き直し（{}）".format(kind)))
        for kind, conf in live.img2img["chain"].items() if live.img2img["chain_on"] else ():
            got.append(method_route(conf["first"], "続けて描く1つ目（{}）".format(kind)))
            got.append(method_route(conf["second"], "続けて描く2つ目（{}）".format(kind)))
        if live.faces["on"] and live.faces["preset"]:
            got.append(method_route(live.faces["preset"], "顔だけ描き直す"))
        return [route for route in got if route is not None]

    def pipe_key(mats):
        """部品を見分ける鍵（チェックポイント・TAESD・VAE・LoRA の置き場）。"""
        norm = lambda path: os.path.normcase(os.path.normpath(full_path(path))) if path else ""
        return tuple(norm(mats.get(name, "")) for name in ("model_path", "taesd_path", "vae_path",
                                                           "lora_model_dir"))

    def clear_progress_callback(cls):
        """ゲームが描いたときに登録した進み具合のコールバックを外す（部品を建てる前に呼ぶ）。

        ゲームの包みは描くたびに関数の中で作ったコールバックを DLL に登録し、描き終えると
        Python の側だけが消える。DLL はそれを指したまま、次の部品のモデルを読み込む途中で呼んで
        落ちる（access violation。VERIFICATION.md §3.65）。外すと DLL は自分で標準出力へ書く。
        """
        binding = getattr(sys.modules.get(cls.__module__), "sd_cpp", None)
        clear = getattr(binding, "sd_set_progress_callback", None)
        if not callable(clear):
            return
        try:
            clear(None, None)
        except Exception:
            ctx.log_exc("image quality: clearing the progress callback failed")

    def route_pipe(pipe, route):
        """その種類を描く部品。ゲームの部品と同じ材料ならゲームの部品。建てられなければ None。

        ゲームが建てた引数（`INIT_ATTR`）の材料だけを差し替えて、同じ作りで建てる。
        建てた部品はクラスに置いて使い回す（描くたびに読み込まない）。
        """
        cls = type(pipe)
        init = frames.attr(cls, INIT_ATTR, None)
        if not init:
            warn("kind-preset-no-init",
                 "ゲームが部品を建てたときの引数が控えられていない（注入より先に建っていた）。"
                 "種類ごとのプリセットはゲームを起動し直すと効く。プリセットを使わずに描く")
            return None
        key = pipe_key(route["materials"])
        if key == pipe_key(init):
            return pipe
        checkpoint = full_path(route["materials"]["model_path"])
        if not checkpoint or not os.path.isfile(checkpoint):
            warn("kind-preset-no-model-" + route["name"],
                 "{}のチェックポイントが見つからない（{}）。プリセットを使わずに描く".format(
                     route["label"], checkpoint or "空。ゲームのモデルがまだ分からない"))
            return None
        cache = cls.__dict__.get(PIPES_ATTR)
        if cache is None:
            cache = {}
            setattr(cls, PIPES_ATTR, cache)
        failed = cls.__dict__.get(FAILED_ATTR)
        if failed is None:
            failed = set()
            setattr(cls, FAILED_ATTR, failed)
        if key in failed:
            return None                      # 知らせは建てられなかった回に出した
        if key not in cache:
            # どこにも割り当てられていない部品は外す（割り当てを変えるたびに VRAM に溜めない）。
            wanted_keys = set(pipe_key(other["materials"]) for other in routes_in_use())
            for old in [old for old in cache if old not in wanted_keys]:
                cache.pop(old, None)
                write("kind pipe: 割り当てから外れた部品を外した（{}）".format(
                    os.path.basename(old[0]) or "ゲームのまま"))
            kwargs = dict(init)
            kwargs.update(route["materials"])
            started = time.time()
            MARKS.building = True            # 控えの引数を自分の部品で上書きしない
            try:
                clear_progress_callback(cls)
                built = cls(**kwargs)
                # モデルが入っていない部品は描くところで落ちる（`model` が AssertionError）。
                # 建てた時点で読んで確かめ、入っていなければ使わない。
                check = getattr(type(built), "model", None)
                if isinstance(check, property):
                    check.fget(built)
                cache[key] = built
            except Exception:
                ctx.log_exc("image quality: building the pipe for preset failed")
                failed.add(key)
                warn("kind-preset-build-" + route["name"],
                     "{}の部品を建てられなかった。ゲームを起動し直すまで、プリセットを使わずに描く".format(
                         route["label"]))
                return None
            finally:
                MARKS.building = False
            write("kind pipe: {}の部品を建てた（{}・{}、{:.1f} 秒。ゲームの部品のほかに {} 個）".format(
                route["label"], route["model"][0] or "系統不明",
                os.path.basename(kwargs["model_path"]), time.time() - started, len(cache)))
        return cache[key]

    def make_init_hook(sig):
        """ゲームが部品を建てたときの引数を、部品のクラスに控える。"""
        def hook(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            if not frames.attr(MARKS, "building", False):
                try:
                    bound = sig.bind_partial(self, *args, **kwargs)
                    got = dict(bound.arguments)
                    got.pop(next(iter(sig.parameters)), None)
                    setattr(type(self), INIT_ATTR, got)
                except Exception:
                    ctx.log_exc("image quality: recording the pipe init failed")
            return result
        return hook

    def make_exit_hook(label, sig, diffusers):
        """出口の包み。sdcpp の出口なら `hook.draw` に描く関数を付ける（差し込んだときだけ `exit_draws` に置く）。"""
        self_name = next(iter(sig.parameters)) if sig is not None else "self"

        def draw(orig, self, args, kwargs, kind, stage, route, extra=None, mode=""):
            """1回描く。`route` はプリセットの組（None で道具画面の各タブの値とゲームの部品）。

            `extra` は寸法が決まった後に足す引数を返す関数（下絵と強さ）。
            `mode` はログの印（描き直し・続けて描く）。
            """
            if sig is None:
                params = dict(kwargs)
                bound = None
            else:
                bound = sig.bind_partial(self, *args, **kwargs)
                bound.apply_defaults()
                params = dict(bound.arguments)
            target = self
            if route is not None:
                target = route_pipe(self, route)
                if target is None:          # 建てられなかった。プリセットを使わずに描く
                    target, route = self, None
            changes = wanted(kind, stage, params, route)
            # 立ち絵の描き方で描く人間の敵は、正方形を同じ高さの縦長にする。
            # 正方形への埋め戻しは `hook` の出口（sdcpp だけ）
            if not diffusers and as_portrait(kind, params):
                tall = humans.tall_size(changes.get("width", params.get("width")),
                                        changes.get("height", params.get("height")), sizes.round_side)
                if tall is not None:
                    changes["width"], changes["height"] = tall
            if diffusers:
                changes = for_diffusers(changes, params)
            # その一族の出口に無い引数は落とす（名前を新設しない）。
            changes = {name: value for name, value in changes.items()
                       if name in params or name in ("width", "height")}
            if extra is not None:
                changes.update(extra(params, changes))
            if target is not self:
                bound.arguments[self_name] = target
            if not changes:
                if target is not self:
                    return orig(*bound.args, **bound.kwargs)
                return orig(self, *args, **kwargs)
            write("{} [{}/{}]{}{} {}{}".format(
                label, kind, stage,
                " {}".format(mode) if mode else "",
                " <{}>".format(route["name"]) if route else "",
                " ".join("{}: {}".format(name, brief_change(name, params, value))
                         for name, value in sorted(changes.items())),
                pixels_note(params, changes)))
            show_prompts(kind, stage, params, changes, route["model"][1] if route else None)
            if bound is None:
                if not set(changes) <= set(kwargs):
                    warn(label, "{}: 署名が読めず、位置引数は触れない".format(label))
                    return orig(self, *args, **kwargs)
                kwargs = dict(kwargs)
                kwargs.update(changes)
                return orig(self, *args, **kwargs)
            bound.arguments.update(changes)
            return orig(*bound.args, **bound.kwargs)

        def init_from(image, strength):
            """下絵と強さを足す関数。下絵は出口が描く寸法に合わせる（違うとネイティブで落ちる）。"""
            def extra(params, changes):
                width = int(changes.get("width", params.get("width")))
                height = int(changes.get("height", params.get("height")))
                return {"init_image": img2img.as_init(image, width, height),
                        "strength": float(strength)}
            return extra

        def refine(orig, self, args, kwargs, kind, stage, result, route):
            """描き終えた立ち絵の顔だけを描き直して貼り戻す（`faces.py`。高度設定で入のときだけ）。

            `route` はその立ち絵を最後に描いたプリセットの組（None はゲームの部品）。
            顔が見つからない・描けないときは元の戻り値のまま。
            """
            conf = faces.config(live.faces, face_models)
            if conf is None:
                return result
            image = img2img.first_image(result)
            if image is None:
                return result
            started = time.time()
            try:
                found = faces.find(image, conf)
            except Exception as exc:
                ctx.log_exc("image quality: finding the face failed")
                warn("face-find-failed", "顔の位置を探せなかったので、顔を描き直さない（{}: {}）".format(
                    type(exc).__name__, exc))
                return result
            if found is None:
                write("顔の描き直し [{}/{}]: 顔が見つからないので描き直さない".format(kind, stage))
                MARKS.refined = faces.NO_FACE
                return result
            if conf["preset"]:
                route = method_route(conf["preset"], "顔の描き直し")
            captured = {"draw": draw, "orig": orig, "self": self, "args": args, "kwargs": kwargs,
                        "stage": stage, "kind": kind}
            try:
                full, (_left, _top, size, side) = redraw_face(captured, image, found, route, conf["strength"],
                                                              "顔の描き直し")
            except Exception as exc:
                ctx.log_exc("image quality: redrawing the face failed")
                warn("face-redraw-failed", "顔を描き直せなかったので、元の絵のままにする（{}: {}）".format(
                    type(exc).__name__, exc))
                return result
            if full is None:
                return result
            write("顔の描き直し [{}/{}]: 顔 {} を {} 四方で切り出し {} 四方で描き直した（強さ {}、{:.1f} 秒）".format(
                kind, stage, found[0], size, side, conf["strength"], time.time() - started))
            # 入口を抜けるとき（ゲームが絵を保存した後）に控えへ書く（`make_kind_hook`）
            MARKS.refined = faces.REDRAWN
            return faces.replace_first(result, full)

        def hook(orig, self, *args, **kwargs):
            # 1枚描くあいだは描く順番を持つ（裏で描く表情差分と重ねない。variants.Turn）
            with variants.turn().game():
                result = run(orig, self, *args, **kwargs)
            if diffusers or sig is None:
                return result
            try:
                bound = sig.bind_partial(self, *args, **kwargs)
                if as_portrait(kind_now(), bound.arguments):
                    return humans.pad_result(result)
            except Exception:
                ctx.log_exc("image quality: padding a human enemy failed")
            return result

        def run(orig, self, *args, **kwargs):
            live.refresh()                 # 設定と規則が変わっていれば読み直す
            kind = kind_now()
            stage = stage_of(frames.attr(MARKS, "func", None))
            # プリセット・描き直し・続けて描くは sdcpp だけ（diffusers 系に別の部品と下絵の口は無い）。
            sdcpp = kind is not None and not diffusers and sig is not None
            source = frames.attr(MARKS, "source", None)
            forced = imagegen.method_now()
            mode = None
            # 描き方（プリセット・描き直し・続けて描く）を選ぶ種類。人間の敵は設定で立ち絵の描き方にする
            pick = kind
            if sdcpp:
                bound = sig.bind_partial(self, *args, **kwargs)
                bound.apply_defaults()
                if as_portrait(kind, bound.arguments):
                    pick = "portrait"
                mode = img2img.plan(pick, stage, dict(bound.arguments), live.img2img,
                                    source is not None, forced)

            def finish(result, route):
                # 立ち絵の最後の段（ゲームの2段の1段目は除く）だけ、顔を描き直す
                if not sdcpp or kind != "portrait" or stage == "stage1":
                    return result
                # 表情差分を描く材料を控える（入口を抜けるときに仕事を始める。`make_kind_hook`）。
                # 入口の中（入口の印が立っている）だけ。入口を包めていない回に控えると、受け取る者が無くスレッドに残る
                if expressions.wanted() and frames.attr(MARKS, "kind", None) == "portrait":
                    MARKS.variant = {"draw": draw, "orig": orig, "self": self, "args": args,
                                     "kwargs": kwargs, "stage": stage, "route": route}
                # 一括生成で差分だけ描く・既存の立ち絵の顔を描き直すときのために、ゲームの呼び方を控える
                # （文と絵は除く）。表情を見せる MOD が無くても、既存の立ち絵の顔の描き直しが使う
                try:
                    remember_template(stage=stage, arguments=dict(
                        sig.bind_partial(self, *args, **kwargs).arguments))
                except Exception:
                    ctx.log_exc("image quality: reading the portrait call failed")
                try:
                    return refine(orig, self, args, kwargs, kind, stage, result, route)
                except Exception:
                    # 描き終えた絵は失わない（描き直しの段の外へ例外を出すと、ふだんの描き方で描き直す）
                    ctx.log_exc("image quality: face pass failed")
                    return result

            if mode == "redraw":
                conf = img2img.conf_for(live.img2img, mode, pick)
                # プリセットが空なら、ゲーム既定のモデルで描く（続けて描くと同じ）。
                route = method_route(conf["preset"], "描き直し（{}）".format(kind))
                try:
                    return finish(draw(orig, self, args, kwargs, kind, stage, route,
                                       init_from(source, conf["strength"]), "描き直し"), route)
                except Exception as exc:
                    ctx.log_exc("image quality: img2img redraw failed")
                    warn("img2img-redraw-failed",
                         "今の絵を下絵にして描けなかったので、ふだんどおり描く（{}: {}）".format(
                             type(exc).__name__, exc))
            elif mode == "chain":
                conf = img2img.conf_for(live.img2img, mode, pick)
                first_route = method_route(conf["first"], "続けて描く1つ目（{}）".format(kind))
                try:
                    first = draw(orig, self, args, kwargs, kind, stage, first_route,
                                 mode="続けて描く 1/2")
                except Exception as exc:
                    ctx.log_exc("image quality: chain first pass failed")
                    warn("img2img-chain-first-failed",
                         "続けて描くの1つ目で描けなかったので、ふだんどおり描く（{}: {}）".format(
                             type(exc).__name__, exc))
                else:
                    image = img2img.first_image(first)
                    if image is None:
                        warn("img2img-chain-no-image",
                             "続けて描くの1つ目が絵を返さなかったので、1つ目の絵のままにする")
                        return first
                    second_route = method_route(conf["second"], "続けて描く2つ目（{}）".format(kind))
                    try:
                        second = draw(orig, self, args, kwargs, kind, stage, second_route,
                                      init_from(image, conf["strength"]), "続けて描く 2/2")
                    except Exception as exc:
                        ctx.log_exc("image quality: chain second pass failed")
                        warn("img2img-chain-second-failed",
                             "続けて描くの2つ目で描けなかったので、1つ目の絵のままにする（{}: {}）".format(
                                 type(exc).__name__, exc))
                        return finish(first, first_route)
                    return finish(second, second_route)
            # 種類にプリセットを割り当てていれば、その部品と値で描く（sdcpp だけ）。
            route = kind_route(pick) if sdcpp else None
            return finish(draw(orig, self, args, kwargs, kind, stage, route), route)
        if sig is not None and not diffusers:
            hook.draw = draw
        return hook

    # ------------------------------------------------------- 当てる・当て直す

    def install(target, hook, **options):
        if target in seen:
            return 0
        seen.add(target)
        ctx.wrap(target, required=False, safe=True, **options)(hook)
        write("wrapped {}".format(target))
        return 1

    def attach():
        # **用済みの apply() は当て直さない。**
        # `load_sd_pipeline` の包みも見張りも、注入し直した後まで生きている。
        # そこから当て直すと、ローダは「いまの世代」として入れてしまい、
        # 新しい世代の包みの**外側に**古い包みが重なる。
        # 重なると、古い包みが新しい包みの書いた寸法をもう一度拡大し、
        # 印は別インスタンスなので段が None になる
        # （実機: 1段目が 256x512 -> 512x1024 -> 1024x2048 と二重に効き、
        #  22 秒かかった。`[portrait/None]` の行がその印）。
        if ctx.superseded():
            return 0
        added = 0
        for name in managers():
            # 系統は import のときにも見るが、**注入し直しでは import が起きない**。
            # 当て直しのたびに、いま効いているチェックポイントで見直す。
            detect_family(sys.modules.get(name))
            warn_material_gap(sys.modules.get(name))
        for name in creature_modules():
            mod = sys.modules.get(name)
            for fn, kind in KIND_FUNCS.items():
                if frames.attr(mod, fn, None) is None:
                    continue
                added += install("{}:{}".format(name, fn), make_kind_hook(kind, fn, name))
            # 表情差分を同じ引数で通すため、立ち絵の工程の引数を控える（このモジュールの写しだけ）
            for step in VARIANT_STEPS:
                if frames.attr(mod, step, None) is None:
                    continue
                added += install("{}:{}".format(name, step), make_step_hook(name, step),
                                 alias_scan=False)
        for name in background_modules():
            mod = sys.modules.get(name)
            for fn in BACKGROUND_FUNCS:
                if frames.attr(mod, fn, None) is None:
                    continue
                added += install("{}:{}".format(name, fn), make_kind_hook("background", fn))
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
            # 建つ前でも、manager が持っているクラスから出口を包む。
            # 起動直後の注入し直し（boot #2）が建てている途中に入ると、
            # 建て終わってから包むまでの間にゲームが描き始め、
            # SD1.5 用の LoRA が SDXL に当たって落ちた（実機 2026-10-26。VERIFICATION.md §3.65）。
            early = [value for value in list(frames.attr(mod, "__dict__", {}).values())
                     if isinstance(value, type)
                     and owner_of(value, EARLY_EXIT_METHOD) is not None]
            for cls, methods in ([(type(pipe), EXIT_METHODS)] if pipe is not None else []) + \
                    [(cls, (EARLY_EXIT_METHOD,)) for cls in early]:
                for method in methods:
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
                    hook = make_exit_hook(target, sig, diffusers)
                    got = install(target, hook)
                    if got and getattr(hook, "draw", None) is not None:
                        exit_draws[target] = hook.draw
                    added += got
                # 部品を建てたときの引数を控える（種類ごとのプリセットで同じ作りの部品を建てる）。
                base = owner_of(cls, "__init__")
                if base is not None and EARLY_EXIT_METHOD in methods:
                    try:
                        init_sig = inspect.signature(unwrapped(base.__dict__.get("__init__")))
                    except Exception:
                        init_sig = None
                    if init_sig is not None:
                        added += install("{}:{}.__init__".format(
                            frames.attr(base, "__module__", "?"),
                            frames.attr(base, "__qualname__", base.__name__)),
                            make_init_hook(init_sig))
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
        # ゲームの値を控える。読み込まれた直後の manager は空で、ゲームは建てる直前に
        # 設定のモデル名から値を入れる（実機 2026-10-26。空を控えて空のモデルの部品を建て、
        # 描くところで AssertionError になった）。空でも 343 の値でもない値が見えるたびに更新する。
        kept = frames.attr(module, GAME_ATTR, None)
        if kept is None:
            kept = {}
            setattr(module, GAME_ATTR, kept)
        ours = dict(materials)
        for _name, attr in MATERIALS:
            now = frames.attr(module, attr, None)
            if isinstance(now, str) and now and now != ours.get(attr):
                kept[attr] = now
        for attr, value in materials:
            now = frames.attr(module, attr, None)
            if now is None or now == value:
                continue
            write("{}: (適用) {} {!r} -> {!r}".format(name, attr, now, value))
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
                # 建てている間に注入し直されたら、この apply() は用済みで何もしないので、
                # いちばん新しい apply() に包ませる（そちらは建つ前に見たのでまだ包めていない）。
                if ctx.superseded():
                    newest = getattr(sys.modules.get(__name__), "HANDOFF", {})
                    if callable(newest.get("attach")):
                        newest["attach"]()
                else:
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

    def warn_material_gap(module):
        """設定のチェックポイントが**まだ建っていない**なら知らせる。

        材料はパイプラインが建つ瞬間にしか効かない。
        注入したときに既に建っていれば、設定を入れても届かないまま生成が続く。
        **ワールド選択をやり直しても建て直らない**（実測: `set_ai_models` に入った時点で
        `txt2img_pipe` が在る回は `load_sd_pipeline` を呼ばない。7回ともそうだった）。
        ゲームは1プロセスに1回しか建てないので、要るのは起動し直し。

        試作の提供先でこれを踏んだ。SDXL を指したまま SD1.5 のモデルで描き続け、
        規則が付け替えた SDXL 用の LoRA が SD1.5 に当たる一歩手前だった。
        """
        want = dict(materials).get("model_path_anime")
        if not want or module is None:
            return
        now = frames.attr(module, "model_path_anime", None)
        if not now or now == want:
            return
        if frames.attr(module, "txt2img_pipe", None) is None:
            return                          # まだ建っていない。次に建つときに効く
        live = FAMILY.get("name")
        want_family = sizes.family_of(
            want if os.path.isabs(want) else os.path.join(ctx.game_dir, want))
        warn("material-gap",
             "チェックポイントの差し替えが**まだ効いていない**。"
             "いま建っているのは {}（{}）で、設定は {}（{}）を指している。"
             "材料はパイプラインが建つ瞬間にしか効かず、"
             "**タイトルへ戻ってワールドを選び直しても建て直らない**（実測）。"
             "ゲームを起動し直してから注入すること".format(
                 live or "系統不明", os.path.basename(str(now)),
                 want_family or "系統不明", os.path.basename(str(want))))

    def install_observer():
        kept = [f for f in sys.meta_path
                if frames.attr(f, OBSERVER_MARK, None) is None]
        observer = _ImportObserver(on_import_done, stale=lambda: ctx.superseded())
        sys.meta_path[:] = [observer] + kept

    install_observer()
    HANDOFF["attach"] = attach
    attach()

    # ------------------------------------------------- 会話の立ち絵の表情差分

    @ctx.wrap(BULK_TARGET, required=False, safe=True)
    def bulk_generation(orig, *args, **kwargs):
        """一括生成のあいだは、表情差分を立ち絵に続けて描く（`start_variants`）。

        ゲームの仕事が済んだ後に、立ち絵が既にある人物へ差分だけ描く（`backfill_variants`）。
        引数は `(self, world_path, stop_event, progress)`。一括生成を始めた後の当て直しで
        この包みは古い世代のまま走り続けるので、差分だけ描く関数は置き場（`BULK`）から最新のものを引く。
        """
        BULK["depth"] += 1
        try:
            # ゲームが画面へ最後に渡した件数を控える（差分だけ描いた後に、画面をゲームの終わりの表示へ戻す）
            progress = frames.arg(args, kwargs, "progress", 3)
            last = {}
            if callable(progress):
                def recording(state, *more, **named):
                    if isinstance(state, dict):
                        last.clear()
                        last.update(state)
                    return progress(state, *more, **named)
                if len(args) > 3:
                    args = args[:3] + (recording,) + tuple(args[4:])
                elif "progress" in kwargs:
                    kwargs = dict(kwargs, progress=recording)
            result = orig(*args, **kwargs)
            # 一括生成の開始で読み込まれたモジュールに、ローダが当て直している最中のことがある（ゲームの仕事が
            # 無いと、その間に終わる）。最新の描き足しと表情を見せる MOD の宣言が揃うまで待つ（VERIFICATION.md §3.132）
            waiting = time.time()
            if not booted():
                write("一括生成: {:.0f} 秒待っても当て直しが終わらなかったので、このまま描き足す".format(REAPPLY_WAIT))
            elif time.time() - waiting >= 0.1:
                write("一括生成: ローダの当て直しが終わるのを {:.1f} 秒待ってから描き足す".format(time.time() - waiting))
            backfill = BULK.get("backfill")
            if callable(backfill):
                stop_event = frames.arg(args, kwargs, "stop_event", 2)
                reported = False
                try:
                    reported = backfill(frames.arg(args, kwargs, "world_path", 1), stop_event, progress)
                except Exception:
                    ctx.log_exc("image quality: drawing the missing expression variants failed")
                if reported and callable(progress):
                    # 差分だけ描く間に渡した「実行中」のまま残さない（画面が止まって見える。VERIFICATION.md §3.132）。
                    # ゲームが件数を1回も渡さずに終えた回は、ゲームの終わりの形（件数 0）を渡す。
                    # 差分だけ描いている途中で「中断する」が押されたら、中断の表示にする
                    final = dict(last) if last else dict(BULK_FINAL)
                    if stopped(stop_event) and final.get("status") == "complete":
                        final["status"] = "stopped"
                    try:
                        progress(final)
                    except Exception:
                        ctx.log_exc("image quality: restoring the bulk progress failed")
            return result
        finally:
            BULK["depth"] = max(0, BULK["depth"] - 1)

    def stopped(stop_event):
        is_set = frames.attr(stop_event, "is_set", None)
        return callable(is_set) and bool(is_set())

    def booted():
        """ローダの当て直しが走っていれば終わるまで待つ（`ctx.wait_booted`）。待てた・走っていなければ真。"""
        wait_booted = getattr(ctx, "wait_booted", None)
        return not callable(wait_booted) or bool(wait_booted(REAPPLY_WAIT))

    def show_wanted():
        """表情を見せる MOD が居るか。当て直しの最中は宣言が一時的に消えるので、終わるのを待ってから数え直す。"""
        return expressions.wanted() or (booted() and expressions.wanted())

    def portrait_route():
        """差分だけ描くときの、立ち絵を描いたのと同じ組（ゲームの通常の生成と同じ選び方）。

        続けて描くを通常の生成でも使う立ち絵なら2つ目、種類ごとのプリセットがあればそれ、無ければゲームの部品。
        顔だけ描き直すのプリセットがあれば、`variant_job` がそちらへ替える。
        """
        if live.img2img["chain_on"] and "portrait" in live.img2img["chain_auto"]:
            conf = img2img.conf_for(live.img2img, "chain", "portrait")
            return method_route(conf["second"], "表情差分（続けて描く2つ目）")
        return kind_route("portrait")

    def prepare_image_generation():
        """ゲームの画像生成がまだ用意されていなければ、ゲーム自身の用意（`AIManager.set_ai_models`）を呼ぶ。

        ゲームは画像生成のモジュールを `set_ai_models` の中で読み込み、部品を建てる。
        一括生成はゲームの仕事が1件も無いとこれを呼ばないので、立ち絵が揃った世界を
        起動直後に一括生成へ掛けると、差分だけ描く時点でモジュールも部品も無い（VERIFICATION.md §3.132）。
        用意できなかった理由（できたら None）を返す。
        """
        if imagegen.creature_module() is not None:
            return None
        app = ui.find_app()
        if app is None:
            return "ゲームの画像生成がまだ用意されておらず、ゲームの本体が見つからない"
        prepare = None
        for name in AI_MANAGER_ATTRS:
            candidate = frames.attr(frames.attr(app, name, None), "set_ai_models", None)
            if callable(candidate):
                prepare = candidate
                break

        def build():
            """ゲームの用意を呼ぶ（メインスレッドで）。用意できなかった理由（呼べたら None）。"""
            try:
                if prepare is not None:
                    prepare()
                    return None
                # 題の画面から始めた回は `AIManager` がまだ組まれていない。ゲームと同じ引数で組む
                # （`__init__` が `set_ai_models` を呼ぶ）
                cls = ui.cls_of("AIManager")
                config = frames.attr(frames.attr(app, "auto_configuration_screen", None), "config", None)
                if config is None:
                    # 予備の `app.config` は辞書のときだけ（Kivy の App が持つ設定の ConfigParser は渡さない）
                    config = frames.attr(app, "config", None)
                    config = config if isinstance(config, dict) else None
                if cls is None or config is None:
                    return "ゲームの画像生成がまだ用意されておらず、用意の処理（AIManager）も呼べない"
                setattr(app, AI_MANAGER_ATTRS[0], cls(app, config))
                return None
            except Exception:
                ctx.log_exc("image quality: preparing the game's image generation failed")
                return "ゲームの画像生成の用意（set_ai_models）が失敗した"
        # ゲームは `set_ai_models` をメインスレッドで呼ぶ（部品を組み、送信関数を作り直す。GAME.md §2.12）ので、
        # 一括生成のスレッドからは呼ばず、メインスレッドで走らせて終わるまで待つ
        ran, why = ctx.run_on_main_thread(build, timeout=PREPARE_WAIT)
        if not ran:
            return "ゲームの画像生成の用意（set_ai_models）をメインスレッドで走らせられなかった"
        if why:
            return why
        if imagegen.creature_module() is None:
            return "ゲームの画像生成の用意（set_ai_models）を呼んだが、画像生成のモジュールが読み込まれなかった"
        write("表情差分（一括生成）: ゲームの画像生成が用意されていなかったので、ゲームの用意（set_ai_models）を呼んだ")
        prepared["now"] = True
        attach()
        return None

    def backfill_materials():
        """差分だけ描く材料。`(材料, None)` か `(None, 揃わない理由)`。

        部品はゲームの立ち絵の部品、描く関数は sdcpp の出口の `draw`、引数は控え（`remember_template`）。
        """
        if str(imagegen.backend() or "").startswith("diffusers"):
            return None, "画像生成の方式が diffusers 系（下絵の口が無い）"
        saved = template["value"] or variants.load_template(template_path)
        if not saved or not isinstance(saved.get("params"), dict) or not saved.get("stage"):
            return None, "ゲームが立ち絵を描いたときの呼び方の控えが無い（立ち絵を1枚描くと控えができる）"
        why = prepare_image_generation()
        if why:
            return None, why
        module = imagegen.creature_module()
        module_name = frames.attr(module, "__name__", None)
        # 工程は1つずつ、この起動の控え、無ければ保存した控えから（片方だけ控えた起動でも、保存した方で揃える）
        here = STEP_CALLS.get(module_name or "") or {}
        steps = {}
        for step in VARIANT_STEPS:
            got = here.get(step) or (variants.steps_from(saved, (step,)) or {}).get(step)
            if got is not None:
                steps[step] = got
        if module is None or len(steps) < len(VARIANT_STEPS) \
                or not all(callable(frames.attr(module, s, None)) for s in VARIANT_STEPS):
            return None, "ゲームの立ち絵の工程（縮小・減色）の呼び方の控えが無い"
        pipe = None
        for name in managers():
            manager = sys.modules.get(name)
            pipe = frames.attr(manager, "txt2img_pipe", None)
            build = frames.attr(manager, PIPELINE_FUNC, None)
            if pipe is None and callable(build):
                build()
                pipe = frames.attr(manager, "txt2img_pipe", None)
            if pipe is not None:
                break
        base = owner_of(type(pipe), EARLY_EXIT_METHOD) if pipe is not None else None
        if base is None:
            return None, "ゲームの画像生成の部品が建っていない"
        target = "{}:{}.{}".format(frames.attr(base, "__module__", "?"),
                                   frames.attr(base, "__qualname__", base.__name__), EARLY_EXIT_METHOD)
        draw = exit_draws.get(target)
        if draw is None:
            return None, "画像生成の出口（{}）を包んでいない".format(target)
        return {"draw": draw, "orig": below_ours(base.__dict__.get(EARLY_EXIT_METHOD)), "self": pipe,
                "stage": saved["stage"], "params": dict(saved["params"]),
                "module": module_name, "steps": steps}, None

    def reapply_face(materials, folder, name, world, captured, stop_event=None):
        """既に在る立ち絵の顔を描き直し、ゲームの立ち絵の5枚（顔アイコンまで）を書き直す。

        描き方は立ち絵を描くときの顔の描き直し（`refine`）と同じ（検出・切り出し・強さ・プリセット）。
        背景を抜いた形は今の `no_bg_image.png` の透明度を使い、縮小と減色はゲームの工程を通す（差分と同じ）。
        顔アイコンは、今のアイコンを切り出した位置（`faces.locate`）で、描き直した立ち絵から切り直す。
        元の絵（`generated_image.png`）は最後に書く（途中で止まっても、次の回に元の絵から描き直せる）。
        `faces.REDRAWN`・`faces.NO_FACE`（顔が見つからない。控えに書いて次から外す）・None（描けなかった）を返す。
        """
        started = time.time()
        try:
            conf = faces.config(live.faces, face_models)
            if conf is None:
                return None
            module = sys.modules.get(materials["module"] or "")
            funcs = [frames.attr(module, step, None) for step in VARIANT_STEPS]
            calls = materials["steps"]
            if not all(callable(func) for func in funcs) or any(step not in calls for step in VARIANT_STEPS):
                warn("face-existing-no-steps", "ゲームの立ち絵の工程（縮小・減色）が見つからないので、既存の立ち絵の顔を描き直さない")
                return None
            source_path = os.path.join(folder, expressions.SOURCE)
            stamp = expressions.stamp(folder)
            if stamp is None:
                return None
            source = img2img.load_image(source_path)
            image = source.convert("RGB")
            found = faces.find(image, conf)
            if found is None:
                write("顔の描き直し [{}]: 顔が見つからないので描き直さない".format(name))
                remember_done(world, name, stamp, faces.NO_FACE)
                return faces.NO_FACE
            route = captured["route"]
            if conf["preset"]:
                route = method_route(conf["preset"], "顔の描き直し")
            if stopped(stop_event):
                return None
            with variants.turn().job():
                full, (_left, _top, size, side) = redraw_face(captured, image, found, route, conf["strength"],
                                                              "顔の描き直し（既存の立ち絵）")
            if full is None:
                write("顔の描き直し [{}]: 絵が返らなかった".format(name))
                return None
            if expressions.stamp(folder) != stamp:
                write("顔の描き直し [{}]: 描いている間に立ち絵が描き直されたので書かない".format(name))
                return None
            cutout_path = os.path.join(folder, img2img.SOURCES["portrait"][1])
            cutout = img2img.load_image(cutout_path) if os.path.isfile(cutout_path) else None
            rgba = variants.with_alpha(full, cutout) if cutout is not None else full.convert("RGBA")
            small, shown = variants.in_fresh_thread(
                lambda: variants.game_step_images(rgba, funcs[0], funcs[1], calls[VARIANT_STEPS[0]],
                                                  calls[VARIANT_STEPS[1]]))
            shown_path = os.path.join(folder, faces.SHOWN_NAME)
            icon_path = os.path.join(folder, faces.ICON_NAME)
            icon = None
            if os.path.isfile(icon_path) and os.path.isfile(shown_path):
                old_icon = img2img.load_image(icon_path)
                old_shown = img2img.load_image(shown_path)
                # 寸法が違えば（立ち絵を描いた後に 131_ の高画質化を切り替えた）、探した位置は新しい絵に当たらない
                at = faces.locate(old_shown, old_icon) if old_shown.size == shown.size else None
                if old_shown.size != shown.size:
                    write("顔の描き直し [{}]: 会話の立ち絵の寸法が前と違う（{} → {}）ので、顔アイコンは元のまま".format(
                        name, old_shown.size, shown.size))
                elif at is None:
                    write("顔の描き直し [{}]: 顔アイコンを切り出した位置が分からないので、顔アイコンは元のまま".format(name))
                else:
                    icon = shown.convert("RGBA").crop((at[0], at[1], at[0] + old_icon.size[0],
                                                       at[1] + old_icon.size[1]))
            if cutout is not None:
                variants.save_replacing(rgba, cutout_path)
            variants.save_replacing(small, os.path.join(folder, faces.SMALL_NAME))
            variants.save_replacing(shown, shown_path)
            if icon is not None:
                variants.save_replacing(icon, icon_path)
            variants.save_replacing(full.convert(source.mode), source_path)
            remember_done(world, name, expressions.stamp(folder), faces.REDRAWN)
            write("顔の描き直し [{}]: 既存の立ち絵の顔 {} を {} 四方で切り出し {} 四方で描き直した（強さ {}、{:.1f} 秒）".format(
                name, found[0], size, side, conf["strength"], time.time() - started))
            return faces.REDRAWN
        except Exception as exc:
            ctx.log_exc("image quality: redrawing the face of an existing portrait failed")
            warn("face-existing-failed", "既存の立ち絵の顔を描き直せなかった（{}: {}: {}）".format(
                name, type(exc).__name__, exc))
            return None

    def captured_for(materials, route, folder):
        """控えた呼び方と人物のフォルダの `prompts.json` から、描く材料を組む。文が無ければ None。"""
        prompts = variants.read_prompts(folder)
        if prompts is None:
            return None
        kwargs = dict(materials["params"], prompt=prompts[0], negative_prompt=prompts[1])
        return {"draw": materials["draw"], "orig": materials["orig"], "self": materials["self"],
                "args": (), "kwargs": kwargs, "stage": materials["stage"], "route": route}

    def in_worker(materials, job):
        """1人ぶんの仕事を新しいスレッドで走らせて待つ（`variants.in_fresh_thread`）。

        ゲームが最後に描いたときに DLL へ登録した進み具合のコールバックを先に外す。
        ゲームの呼び方なら毎回生きたコールバックが渡るが、控えた呼び方には無く（数と文字列だけ）、
        DLL が消えたコールバックを呼ぶと access violation になる（VERIFICATION.md §3.132）。
        新しいスレッドで描くのは、`131_` がスレッドに残す工程の控えを持ち込まないため（start_variants と同じ）。
        """
        clear_progress_callback(type(materials["self"]))
        return variants.in_fresh_thread(job, name="mod343-expressions")

    def redraw_face(captured, image, found, route, strength, mode, prompt_of=None, cut=None):
        """顔の周りを切り出して描き直し、元の絵へ貼った写しを返す（`refine`・`reapply_face`・`variant_job`）。

        `captured` は描く材料（`draw`・`orig`・`self`・`args`・`kwargs`・`stage`、あれば `kind`）。
        `route` は描く組（None はゲームの部品）。切り出しを拡大する一辺は、描くモデルの系統で決める。
        `prompt_of` はプロンプトの直し方（既定は顔の寄り。`faces.redraw_args`）。
        `cut` は切り出し（`face_cut` の戻り値）。同じ顔を何度も描くとき（表情差分）は1回作って渡す。
        `(貼った絵, (左, 上, 一辺, 拡大した一辺))`。絵が返らなければ貼った絵は None。
        """
        left, top, size, side, crop = cut or face_cut(image, found, route)
        face = img2img.first_image(captured["draw"](
            captured["orig"], captured["self"], captured["args"], captured["kwargs"],
            captured.get("kind", "portrait"), captured["stage"], route,
            faces.redraw_args(side, crop, strength, prompt_of), mode))
        full = faces.paste(image, face, left, top, size) if face is not None else None
        return full, (left, top, size, side)

    def face_cut(image, found, route):
        """顔の切り出し（`faces.cut`）。拡大する一辺は描く組（None はゲームの部品）のモデルの系統で決める。"""
        return faces.cut(image, found, route["model"][0] if route else FAMILY.get("name"))

    def remember_done(world, name, stamp, result):
        """顔の描き直しの控えに1人ぶん書く。書けなければ警告する（次の一括生成でまた描き直すことになる）。"""
        if not faces.record_done(done_path, world, name, stamp, result):
            warn("face-done-unwritten", "顔の描き直しの控え（{}）に書けなかった（{}）".format(faces.DONE_NAME, name))

    def backfill_variants(world_path, stop_event, progress, handed=False):
        """一括生成の後に、立ち絵が既に在る人物へ、顔の描き直しと表情差分だけ描く（主人公は除く）。

        顔の描き直しは、高度設定の顔の描き直しと「既存の立ち絵にもかける」が入のときだけ、
        控え（`faces.DONE_NAME`）が今の絵に合わない人物へかける。描き直すと元の絵が変わるので、差分はその後に数える。
        差分は、表情を見せる MOD が居るときだけ、差分の無い人物と、描き足す・タグの変わった・名前の変わった・
        一覧から外した表情のある人物へ手を入れる（`variant_job`）。
        人物は世界の骨格とセーブの名簿から。文は人物のフォルダの `prompts.json`。
        「中断する」で止まり、次の一括生成で残りから描く（済んだ人物は控えと印で外れる）。
        描いている間は一括生成の画面に件数を渡す（`variants.progress_state`）。
        画面へ件数を渡したか（呼んだ側が、終わった後にゲームの終わりの表示へ戻す）を返す。
        `handed` は、当て直しの前の世代から残りを受け取った回（描かずに戻るなら理由を残す）。
        """
        reported = False
        live.refresh()
        face_conf = faces.config(live.faces, face_models) if live.faces["existing"] else None
        if (face_conf is None and not show_wanted()) or stopped(stop_event):
            if not stopped(stop_event):
                write("一括生成: {}表情を見せる MOD の宣言が無いので、表情差分を描かない".format(
                    "前の世代から受け取ったが、" if handed else ""))
            return reported
        # 表情を見せる MOD が居た回か（当て直しの間は宣言が数えられないので、受け渡しの前に戻るのを待つ）
        wanted_at_start = expressions.wanted()
        world_dir = os.path.dirname(str(world_path or ""))
        world = os.path.basename(world_dir)
        if not world:
            return reported
        try:
            with open(os.path.join(world_dir, "world_data.json"), "rb") as fh:
                world_data = saves.decode(fh.read())
        except OSError:
            world_data = None
        try:
            save_data = saves.read_save(world)
        except Exception:
            save_data = None
        player_data = save_data.get("player_data") if isinstance(save_data, dict) else None
        player = player_data.get("name") if isinstance(player_data, dict) else None
        # 文（`prompts.json`）の無い人物は描けないので先に外す（数えると、一括生成のたびにゲームの用意へ進んで空振りする）
        people, no_text = [], 0
        for name in variants.world_npc_names(world_data, save_data):
            folder = expressions.character_dir(world, name)
            if name == player or folder is None:
                continue
            if variants.read_prompts(folder) is None:
                no_text += expressions.stamp(folder) is not None
                continue
            people.append((name, folder))
        if no_text:
            write("一括生成 [{}]: {} の無い立ち絵 {} 人は、顔の描き直しと表情差分から外す".format(
                world, variants.PROMPTS_NAME, no_text))
        redo = []
        if face_conf is not None:
            rows = faces.load_done(done_path)
            if rows is None:
                # 読めない控えを空とみなすと、全員の顔をもう一度描き直す
                warn("face-done-unreadable", "顔の描き直しの控え（{}）が読めないので、既存の立ち絵の顔を描き直さない"
                     "（壊れていれば消すと作り直す）".format(faces.DONE_NAME))
                face_conf = None
            for name, folder in people if face_conf is not None else ():
                stamp = expressions.stamp(folder)
                if stamp is not None and not faces.is_done(rows, world, name, stamp):
                    redo.append((name, folder))

        def variant_blocked():
            """差分を1人も描けない理由（描けるなら None）。`variant_job` が描かずに返す条件と同じ。"""
            if not live.variants["tags"]:
                return "表情のタグが全部空なので描かない"
            if faces.config(dict(live.faces, on=True), face_models) is None:
                return "顔の検出器が見つからないので描かない"
            return None

        def variant_todo():
            # 描けない設定なら数えない（ゲームの用意へ進んで、全員を「描けなかった」と数えないため）
            if not show_wanted() or variant_blocked() is not None:
                return []
            wanted = variants.wanted_rows(live.variants)
            return [(name, folder) for name, folder in people if variants.needs_variants(folder, expressions, wanted)]

        def tidy_only(rows):
            """描く表情が無く、名前を変える・消すだけの人物を先に済ませ、残り（描く人物）を返す。

            名前の変更と削除は絵を描かないので、ゲームの用意（`backfill_materials`）を待たない。
            """
            wanted, left, tidied = variants.wanted_rows(live.variants), [], 0
            for name, folder in rows:
                planned = expressions.plan(folder, wanted)
                if planned is not None and not planned["draw"]:
                    tidied += variant_job(None, folder, name, None) is True
                else:
                    left.append((name, folder))
            if tidied:
                write("表情差分（一括生成）[{}]: {} 人の差分の名前を変えた・消した（描き直しは無し）".format(world, tidied))
            return left
        todo = tidy_only(variant_todo())
        if not redo and not todo:
            if face_conf is not None:
                write("顔の描き直し（一括生成）[{}]: 描き直していない立ち絵は無い".format(world))
            if expressions.wanted():
                blocked = variant_blocked()
                write("表情差分（一括生成）[{}]: {}".format(
                    world, "差分の無い人物・描き足す表情・タグの変わった表情は無い" if blocked is None else blocked))
            return reported
        materials, why = backfill_materials()
        if materials is None:
            if redo:
                warn("face-existing-unready",
                     "一括生成で、既存の立ち絵 {} 人の顔を描き直せなかった（{}）".format(len(redo), why))
            if todo:
                warn("expression-backfill-unready",
                     "一括生成で、立ち絵のある {} 人に表情差分だけ描けなかった（{}）".format(len(todo), why))
            return reported
        route = portrait_route()

        def handed_off():
            # 描いている間に当て直されたら、残りは新しい登録に任せる（済んだ人物は控えと印で外れる）。
            # ゲームの用意を呼んだ回は、読み込まれたモジュールを見てローダが当て直す
            newest = BULK.get("backfill")
            if not callable(newest) or newest is backfill_variants:
                return None
            # 当て直しが始まると前の世代の宣言は数えられなくなり、表情を見せる MOD（`347_`）はこの MOD より後に
            # 宣言し直す。新しい登録に渡すのは宣言が戻ってから（上限の後はそのまま渡す。VERIFICATION.md §3.132）
            limit = time.time() + REAPPLY_WAIT
            while wanted_at_start and not expressions.wanted() and time.time() < limit and not stopped(stop_event):
                time.sleep(0.2)
            return newest

        def hand_over(newest):
            return bool(newest(world_path, stop_event, progress, handed=True)) or reported

        if prepared["now"]:
            # 用意で読み込まれたモジュールを見てローダが MOD を当て直す。当て直しの前に描くと、
            # ほかの MOD（`131_` の縮小の素通しなど）がまだ新しいモジュールに掛かっていない（VERIFICATION.md §3.132）
            prepared["now"] = False
            write("一括生成 [{}]: ゲームの用意で読み込まれたモジュールにローダが当て直すのを待ってから描く".format(world))
            limit = time.time() + REAPPLY_WAIT
            while time.time() < limit and not stopped(stop_event):
                newest = handed_off()
                if newest is not None:
                    write("一括生成 [{}]: 当て直されたので、新しい登録で描く".format(world))
                    return hand_over(newest)
                time.sleep(0.2)
            if stopped(stop_event):
                return reported
            write("一括生成 [{}]: {:.0f} 秒待っても当て直されなかったので、このまま描く".format(world, REAPPLY_WAIT))

        def report(done, remaining, failed, name, what):
            if callable(progress):
                try:
                    progress(variants.progress_state(done, remaining, failed, name, what))
                    return True
                except Exception:
                    ctx.log_exc("image quality: reporting the expression progress failed")
            return False

        if redo:
            write("顔の描き直し（一括生成）[{}]: 既存の立ち絵 {} 人の顔を描き直す".format(world, len(redo)))
            started, done, missed, failed = time.time(), 0, 0, 0
            for index, (name, folder) in enumerate(redo):
                newest = handed_off()
                if newest is not None:
                    write("顔の描き直し（一括生成）[{}]: {} 人描き直したところで当て直されたので、残りは新しい登録で描く".format(
                        world, done))
                    return hand_over(newest)
                if stopped(stop_event):
                    write("顔の描き直し（一括生成）[{}]: 中断した（{} 人描き直して、残り {} 人）".format(
                        world, done, len(redo) - index))
                    return reported
                reported = report(done, len(redo) - index, failed, name, variants.PROGRESS_FACE) or reported
                captured = captured_for(materials, route, folder)
                if captured is None:
                    write("顔の描き直し（一括生成）[{}]: {} が無いので描き直さない".format(name, variants.PROMPTS_NAME))
                    failed += 1
                    continue
                got = in_worker(materials, lambda folder=folder, name=name, captured=captured: reapply_face(
                    materials, folder, name, world, captured, stop_event=stop_event))
                if got == faces.REDRAWN:
                    done += 1
                elif got == faces.NO_FACE:
                    missed += 1
                else:
                    failed += 1
            write("顔の描き直し（一括生成）[{}]: {} 人の顔を描き直した（顔が見つからない {} 人、描けなかった {} 人、計 {:.0f} 秒）".format(
                world, done, missed, failed, time.time() - started))
            todo = tidy_only(variant_todo())
        if not todo:
            return reported
        write("表情差分（一括生成）[{}]: 立ち絵のある {} 人に差分だけ描く".format(world, len(todo)))
        started, done, missed, failed = time.time(), 0, 0, 0
        for index, (name, folder) in enumerate(todo):
            newest = handed_off()
            if newest is not None:
                write("表情差分（一括生成）[{}]: {} 人描いたところで当て直されたので、残りは新しい登録で描く".format(
                    world, done))
                return hand_over(newest)
            if stopped(stop_event):
                write("表情差分（一括生成）[{}]: 中断した（{} 人描いて、残り {} 人）".format(
                    world, done, len(todo) - index))
                return reported
            reported = report(done, len(todo) - index, failed, name, variants.PROGRESS_VARIANTS) or reported
            captured = captured_for(materials, route, folder)
            if captured is None:
                write("表情差分（一括生成）[{}]: {} が無いので描かない".format(name, variants.PROMPTS_NAME))
                failed += 1
                continue
            got = in_worker(materials, lambda folder=folder, name=name, captured=captured: variant_job(
                materials["module"], folder, name, captured, steps=materials["steps"],
                how="一括生成で差分だけ描いた", stop_event=stop_event))
            if got is True:
                done += 1
            elif got == faces.NO_FACE:
                missed += 1
            else:
                failed += 1
        write("表情差分（一括生成）[{}]: {} 人に描いた（顔が見つからない {} 人、描けなかった {} 人、計 {:.0f} 秒）".format(
            world, done, missed, failed, time.time() - started))
        return reported

    # 一括生成の包み（古い世代のまま走り続けることがある）が最新の関数を呼ぶように置く
    if not ctx.superseded():
        BULK["backfill"] = backfill_variants

    def is_player(name, creating=False):
        """主人公の立ち絵か（主人公の立ち絵は会話の相手に出ないので、差分を描かない）。

        `creating` は、立ち絵の入口でキャラを作る画面が出ていたか（`ui.on_character_create`）。
        作成画面で描く立ち絵は主人公のもの（`app.player` はまだ無いか前の主人公なので、名前では分からない）。
        """
        if creating:
            return True
        player = frames.attr(ui.find_app(), "player", None)
        return bool(name) and frames.attr(player, "name", None) == name

    def start_variants(module_name, world, name, captured, creating=False):
        """立ち絵を保存し終えた後に、表情差分の仕事を始める。

        一括生成のあいだは終わるまで待つ（立ち絵に続けて描く）。ほかは待たずに返し、裏で描く。
        どちらも新しいスレッドで描く（`131_` がスレッドに残す工程の控えを持ち込まないため。variants.in_fresh_thread）。
        """
        folder = expressions.character_dir(world, name)
        if folder is None or not expressions.wanted() or is_player(name, creating):
            return
        worker = threading.Thread(target=variant_job, args=(module_name, folder, name, captured),
                                  name="mod343-expressions", daemon=True)
        worker.start()
        if BULK["depth"] > 0:
            worker.join()

    def variant_job(module_name, folder, name, captured, steps=None, how=None, stop_event=None):
        """1人ぶんの表情差分を描いて置く。描き終えて印を書けたら True、顔が見つからなければ `faces.NO_FACE`、
        ほかは False。

        顔が見つからないときは差分の無い印を書く（一括生成のたびに数え直して、ゲームの用意と顔の検出を
        繰り返さないため。立ち絵が描き直されれば印が外れて、また描く）。
        描けない・途中で立ち絵が描き直された・`stop_event` が立ったときは印を書かない。
        印が今の立ち絵のものなら、足りない表情とタグの変わった表情だけ描き、名前の変わった表情はファイル名を変え、
        一覧から外した表情は消す（ローダの窓口の `expressions.plan`）。描き直しに失敗した表情は前の絵のまま見せる。
        `steps` は工程の引数（無ければこの起動で控えたもの）、`how` はログの描いた場面。
        """
        started = time.time()
        try:
            conf = live.variants
            if not conf["tags"]:
                return False
            stamp = expressions.stamp(folder)
            if stamp is None:
                write("表情差分 [{}]: 元の絵（{}）が無いので描かない".format(name, expressions.SOURCE))
                return False
            planned = expressions.plan(folder, variants.wanted_rows(conf), stamp)
            kept, stale = [], {}
            if planned is None:
                todo = list(conf["tags"])
            else:
                moved = expressions.tidy(folder, planned)
                kept, stale = planned["keep"] + moved, dict(planned["stale"])
                todo = [e for e in conf["tags"] if e in planned["draw"]
                        or any(new == e and e not in moved for _old, new in planned["rename"])]
                if planned["rename"] or planned["remove"]:
                    # 描く前に印を書き直す（描いている途中で止まっても、名前を変えた表情を見せられる）
                    tags = dict(conf["tags"], **stale)
                    expressions.seal(folder, stamp, names=kept + list(stale), complete=not todo, tags=tags,
                                     ids=conf["ids"])
                    write("表情差分 [{}]: 名前を変えた {}、消した {}".format(
                        name, ", ".join("{}→{}".format(old, new) for old, new in planned["rename"]) or "無し",
                        ", ".join(planned["remove"]) or "無し"))
                if not todo:
                    return True
            detector = faces.config(dict(live.faces, on=True), face_models)
            if detector is None:
                warn("expression-no-detector", "顔の検出器が見つからないので、表情差分を描かない")
                return False
            module = sys.modules.get(module_name or "")
            funcs = [frames.attr(module, step, None) for step in VARIANT_STEPS]
            calls = steps or STEP_CALLS.get(module_name or "", {})
            if not all(callable(func) for func in funcs) or any(step not in calls for step in VARIANT_STEPS):
                warn("expression-no-steps", "ゲームの立ち絵の工程（縮小・減色）の呼び方が控えられていないので、"
                     "表情差分を描かない（注入より先に描いていた立ち絵）")
                return False
            base = img2img.load_image(os.path.join(folder, expressions.SOURCE)).convert("RGB")
            cutout_path = os.path.join(folder, img2img.SOURCES["portrait"][1])
            cutout = img2img.load_image(cutout_path) if os.path.isfile(cutout_path) else None
            found = faces.find(base, detector)
            if found is None:
                if planned is None:
                    expressions.clear(folder)
                    expressions.seal(folder, stamp, names=[])
                    write("表情差分 [{}]: 顔が見つからないので描かない（この立ち絵には次から描かない）".format(name))
                else:
                    write("表情差分 [{}]: 顔が見つからないので描き足さない".format(name))
                return faces.NO_FACE
            route = captured["route"]
            if live.faces["on"] and live.faces["preset"]:
                route = method_route(live.faces["preset"], "表情差分")
            model = route["model"] if route else (FAMILY.get("name"), FAMILY.get("path"))
            # 強さは描くモデルで決める（モデル名で分ける行、無ければ系統の欄。variants.strength_for）
            strength, matched = variants.strength_for(conf, model)
            cut = face_cut(base, found, route)          # どの表情も同じ切り出し
            if planned is None:
                expressions.clear(folder)
            done, drawn = [], []
            for expression in todo:
                tags = conf["tags"][expression]
                if expressions.stamp(folder) != stamp:
                    write("表情差分 [{}]: 描いている間に立ち絵が描き直されたので止める".format(name))
                    return False
                if stopped(stop_event):
                    write("表情差分 [{}]: 一括生成が中断されたので止める（印を書かない）".format(name))
                    return False
                one = time.time()
                with variants.turn().job():
                    full, _cut = redraw_face(captured, base, found, route, strength, "表情差分 " + expression,
                                             lambda prompt, tags=tags: variants.expression_prompt(prompt, tags),
                                             cut=cut)
                if full is None:
                    write("表情差分 [{}]: {} は絵が返らなかった".format(name, expression))
                    continue
                # 描いている間（約7秒）に立ち絵が描き直されたら、新しい立ち絵の仕事の置き場へ古い差分を書かない
                if expressions.stamp(folder) != stamp:
                    write("表情差分 [{}]: 描いている間に立ち絵が描き直されたので止める".format(name))
                    return False
                rgba = variants.with_alpha(full, cutout) if cutout is not None else full.convert("RGBA")
                shown = variants.in_fresh_thread(
                    lambda: variants.game_steps(rgba, funcs[0], funcs[1], calls[VARIANT_STEPS[0]],
                                                calls[VARIANT_STEPS[1]]))
                variants.save_replacing(shown, expressions.variant_path(folder, expression))
                drawn.append(expression)
                done.append("{} {:.1f}秒".format(expression, time.time() - one))
            # 欠けた表情があれば、描けた分を見せつつ描き揃っていない印にする（次の一括生成で描き直す）。
            # タグを変えて描き直せなかった表情は、前のタグの絵と前のタグを印に残す（次の一括生成で描き直す）
            whole = len(drawn) == len(todo)
            tags = dict(conf["tags"])
            failed_stale = [e for e in stale if e not in drawn]
            tags.update((e, stale[e]) for e in failed_stale)
            shown_names = kept + drawn + failed_stale
            # 1枚も描けなかった回も、前の差分が残っていれば描き揃っていない印に書き直す
            if not (drawn or (planned is not None and shown_names)) or expressions.stamp(folder) != stamp \
                    or not expressions.seal(folder, stamp, names=shown_names, complete=whole, tags=tags,
                                            ids=conf["ids"]):
                return False
            write("表情差分 [{}]: {}（計 {:.1f} 秒、{}、強さ {}{}）{}".format(
                name, "、".join(done) or "描けた表情は無し", time.time() - started,
                how or ("一括生成なので続けて描いた" if BULK["depth"] > 0 else "裏で描いた"), strength,
                "（{}）".format(matched),
                "" if whole else "。描けなかった表情があるので、次の一括生成で描き直す"))
            return whole
        except Exception as exc:
            ctx.log_exc("image quality: drawing the expression variants failed")
            warn("expression-failed", "表情差分を描けなかった（{}: {}: {}）".format(name, type(exc).__name__, exc))
            return False

    # ------------------------------------- 再生成のボタンを描き方ごとに分ける

    def offered(_kind=None):
        """再生成のボタンに出す描き方（txt2img のほかに）。ローダの描き方の窓口へ登録する中身。

        使う設定が入の描き方だけで、種類は問わない（どの種類を描き直すかはゲームが決める）。
        画像生成の方式が diffusers 系なら下絵の口が無いので出さない。
        """
        if str(imagegen.backend() or "").startswith("diffusers"):
            return []
        live.refresh()
        return img2img.enabled(live.img2img)

    # 主人公の描き直し（`335_`）も、この中身から選ぶ口を出す（TECH.md §5.11）。
    imagegen.declare_methods("343_stable_diffusion", ctx, offered)

    @ctx.wrap(REGEN_THREAD_TARGET, required=False, safe=True)
    def regenerate_thread(orig, *args, **kwargs):
        """ボタンで選んだ描き方を、このスレッドの描き方にする（出口の `img2img.plan` が見る）。"""
        mode, REGEN["mode"] = REGEN["mode"], None
        with imagegen.drawing_method(mode or "txt2img") as method:
            write("regenerate: {}".format(method))
            return orig(*args, **kwargs)

    #: 足したボタン、ゲームのボタンの元の文字と形、ゲームの再生成ボタンに結んだ手（閉じたら戻す）。
    regen_ui = {"buttons": [], "kept": [], "bound": None}

    @ctx.wrap(SETTING_WINDOW_TARGET, required=False, safe=True)
    def setting_window(orig, self, *args, **kwargs):
        # `self` は HUD（`InstanTaleHUD.toggle_setting_window_visibility(self, *args)`）。
        result = orig(self, *args, **kwargs)
        # ここで投げると safe の包みがゲームの関数をもう一度呼ぶので、自分で受け止める。
        try:
            arrange_regen_buttons(self)
        except Exception:
            ctx.log_exc("image quality: arranging the regenerate buttons failed")
        return result

    def regen_widgets(hud):
        """設定の画面の枠と、上から順のゲームのボタン（終了・タイトル・再生成）。"""
        names = ("setting_window_shutdown_button", "setting_window_return_to_title_button",
                 "setting_window_regenerate_image_button")
        buttons = [frames.attr(hud, name, None) for name in names]
        return frames.attr(hud, "setting_window_layout", None), buttons

    def drop_regen_buttons():
        """足したボタンを外し、ゲームのボタンの文字と形を戻す。"""
        for button in regen_ui["buttons"]:
            if button.parent is not None:
                button.parent.remove_widget(button)
        regen_ui["buttons"] = []
        if regen_ui["bound"] is not None:
            regen, clear = regen_ui["bound"]
            regen.unbind(on_press=clear)
            regen_ui["bound"] = None
        for widget, text, pos_hint, pos, size in regen_ui["kept"]:
            widget.text, widget.pos_hint, widget.pos, widget.size = text, pos_hint, pos, size
        regen_ui["kept"] = []

    def arrange_regen_buttons(hud):
        """開いた直後なら、再生成を txt2img / img2img（/ txt2img→img2img）のボタンに分けて並べる。

        閉じたときは足したボタンを外して戻すだけ。
        画像生成の方式が diffusers 系なら下絵の口が無いので、ゲームのままにする。
        """
        layout, game = regen_widgets(hud)
        if layout is None or None in game:
            return
        drop_regen_buttons()
        regen = game[-1]
        if regen.parent is not layout:
            return
        modes = offered()
        if not modes:
            return                           # 足すボタンが無い。ゲームのまま
        regen_ui["kept"] = [(widget, widget.text, dict(widget.pos_hint or {}), tuple(widget.pos),
                             tuple(widget.size)) for widget in game]
        regen.text = img2img.BUTTON_LABELS["txt2img"]
        for mode in modes:
            button = ui.popup_button(img2img.BUTTON_LABELS[mode], regen)
            button.bind(on_release=lambda _button, chosen=mode: press_regen(regen, chosen))
            layout.add_widget(button)
            regen_ui["buttons"].append(button)
        rows = game + regen_ui["buttons"]
        width = float(regen.width)
        for button in rows:
            button.texture_update()
            width = max(width, float(button.texture_size[0]) + 60)
        height, bottoms = img2img.stack(len(rows))
        side = (float(layout.width) - float(regen.width)) / 2
        # 位置は枠の中の割合で持たせ、枠（FloatLayout）に並べさせる。
        # 枠の位置は開いた後にもう一度動くので、自分で座標を書くと置き去りになった（実機）。
        for button, bottom in zip(rows, bottoms):
            button.size = (width, img2img.STACK_HEIGHT)
            button.pos_hint = {"center_x": 0.5, "y": float(height - bottom) / height}

        def clear(*_args):
            REGEN["mode"] = None             # ゲームのボタン（txt2img）を直接押した

        regen.bind(on_press=clear)
        regen_ui["bound"] = (regen, clear)
        layout.size = (width + 2 * side, height)
        write("regenerate buttons: {}".format(", ".join(button.text for button in rows[2:])))

    def press_regen(regen, mode):
        """選んだ描き方を控えてから、ゲームの再生成ボタンを押したのと同じ処理を呼ぶ。"""
        REGEN["mode"] = mode
        regen.dispatch("on_release")

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
                 key="343_stable_diffusion:poll:{}".format(ctx.generation))
