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
"""
import inspect
import os
import sys
import threading
import time

from instantale_modloader import frames
from instantale_modloader import imagegen
from instantale_modloader import saves
from instantale_modloader import ui

from . import assets
from . import cutout
from . import img2img
from . import live as hot
from . import presets
from . import rules as rulebook
from . import sizes
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
#: 寸法を変えると作り直しが走り、実機では 64GB の RAM を使い切って
#: SSD へページングを始めた（VERIFICATION.md §3.65）。sdcpp 系には関係しない。
DIFFUSERS_RESIZE = False

# ------------------------------------------------------------------------

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

#: 背景の切り抜きのモデル（版27）。game はゲームのまま（サイドカーの u2net）、
#: isnet-anime はゲームのフォルダのアニメ絵向けのモデルで抜く（`cutout.py`）。
CUTOUT_MODEL = "game"

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

#: ゲームの背景の切り抜き（rembg のサイドカーへ頼む関数。版27 で isnet-anime に差し替えられる）。
CUTOUT_TARGET = "scripts.image_processing.request_remove_background:remove_background"

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
    for line in live.describe():
        write(line)
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

    def make_kind_hook(kind, func_name=None):
        """入口で種類の印を立てる。描き直しが入の種類なら、今の絵もここで読んで控える。

        入口で読むのは、世界と名前（場所）が入口の引数にしか無く、出口では分からないため。
        ゲームは描き終えてから同じ名前で絵を書き直すので、入口の時点では今の絵が残っている（GAME.md §2.30）。
        今の絵を探さない入口（`img2img.ENTRANCES` に無い）は、外側の控えを持ち越さない。
        """
        entrance = img2img.ENTRANCES.get(func_name)

        def hook(orig, *args, **kwargs):
            before = frames.attr(MARKS, "kind", None)
            before_source = frames.attr(MARKS, "source", None)
            MARKS.kind = kind
            MARKS.source = None
            if entrance is not None:
                MARKS.source = current_image(kind, args, kwargs, entrance)
            try:
                return orig(*args, **kwargs)
            finally:
                MARKS.kind = before
                MARKS.source = before_source
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
        for name, target in (("prompt", "prompt"), ("negative_prompt", "negative")):
            body = params.get(name)
            if not isinstance(body, str):
                continue
            book = tag_book(rules, kind, target, model)
            got = rulebook.rewrite(rules, kind, target, body, body, model, book)
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

        def hook(orig, self, *args, **kwargs):
            live.refresh()                 # 設定と規則が変わっていれば読み直す
            kind = kind_now()
            stage = stage_of(frames.attr(MARKS, "func", None))
            # プリセット・描き直し・続けて描くは sdcpp だけ（diffusers 系に別の部品と下絵の口は無い）。
            sdcpp = kind is not None and not diffusers and sig is not None
            source = frames.attr(MARKS, "source", None)
            forced = imagegen.method_now()
            mode = None
            if sdcpp:
                bound = sig.bind_partial(self, *args, **kwargs)
                bound.apply_defaults()
                mode = img2img.plan(kind, stage, dict(bound.arguments), live.img2img,
                                    source is not None, forced)
            if mode == "redraw":
                conf = img2img.conf_for(live.img2img, mode, kind)
                # プリセットが空なら、ゲーム既定のモデルで描く（続けて描くと同じ）。
                route = method_route(conf["preset"], "描き直し（{}）".format(kind))
                try:
                    return draw(orig, self, args, kwargs, kind, stage, route,
                                init_from(source, conf["strength"]), "描き直し")
                except Exception as exc:
                    ctx.log_exc("image quality: img2img redraw failed")
                    warn("img2img-redraw-failed",
                         "今の絵を下絵にして描けなかったので、ふだんどおり描く（{}: {}）".format(
                             type(exc).__name__, exc))
            elif mode == "chain":
                conf = img2img.conf_for(live.img2img, mode, kind)
                try:
                    first = draw(orig, self, args, kwargs, kind, stage,
                                 method_route(conf["first"], "続けて描く1つ目（{}）".format(kind)),
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
                    try:
                        return draw(orig, self, args, kwargs, kind, stage,
                                    method_route(conf["second"], "続けて描く2つ目（{}）".format(kind)),
                                    init_from(image, conf["strength"]), "続けて描く 2/2")
                    except Exception as exc:
                        ctx.log_exc("image quality: chain second pass failed")
                        warn("img2img-chain-second-failed",
                             "続けて描くの2つ目で描けなかったので、1つ目の絵のままにする（{}: {}）".format(
                                 type(exc).__name__, exc))
                        return first
            # 種類にプリセットを割り当てていれば、その部品と値で描く（sdcpp だけ）。
            return draw(orig, self, args, kwargs, kind, stage,
                        kind_route(kind) if sdcpp else None)
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
                added += install("{}:{}".format(name, fn), make_kind_hook(kind, fn))
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
                    added += install(target, make_exit_hook(target, sig, diffusers))
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

    # ------------------------------------------------- 背景の切り抜き（版27）

    cutout_file = cutout.model_path(game_dir)
    cutout_seen = {}

    @ctx.wrap(CUTOUT_TARGET, required=False, safe=True)
    def remove_background(orig, *args, **kwargs):
        """設定が isnet-anime なら、サイドカー（u2net 固定）の代わりに isnet-anime で抜く。

        抜けなければ（ファイルが無い・推論で落ちた）ゲームの切り抜きへ戻す。
        """
        if str(live.values.get("CUTOUT_MODEL") or "game") != "isnet-anime":
            return orig(*args, **kwargs)
        image = frames.arg(args, kwargs, "image", 0)
        if image is None or not os.path.isfile(cutout_file):
            warn("cutout-missing", "isnet-anime が使えないのでゲームの切り抜きで抜く"
                 "（{} が無い）".format(cutout_file))
            return orig(*args, **kwargs)
        start = time.time()
        try:
            got = cutout.cut(image, cutout_file)
        except Exception as exc:
            warn("cutout-failed", "isnet-anime で抜けなかったのでゲームの切り抜きで抜く"
                 "（{}: {}）".format(type(exc).__name__, exc))
            return orig(*args, **kwargs)
        count = cutout_seen.get("count", 0) + 1
        cutout_seen["count"] = count
        if count <= 3 or count % 50 == 0:
            write("cutout: isnet-anime {}x{} {:.2f} 秒（{} 枚目）".format(
                got.size[0], got.size[1], time.time() - start, count))
        return got

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
    def setting_window(orig, hud, *args, **kwargs):
        result = orig(hud, *args, **kwargs)
        # ここで投げると safe の包みがゲームの関数をもう一度呼ぶので、自分で受け止める。
        try:
            arrange_regen_buttons(hud)
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
