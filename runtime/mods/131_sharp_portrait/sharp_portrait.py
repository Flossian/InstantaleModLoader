# -*- coding: utf-8 -*-
"""立ち絵・敵の絵・背景を荒くする工程を通さず、顔は YuNet で先に探し、外れた回はやり直す。

ゲームは Stable Diffusion で描いた絵を、保存する前に**わざと荒くしている**
（経路と寸法は GAME.md §2.30。下は立ち絵。敵と背景は工程の順が違うので別の節に書く）:

    no_bg_image.png                512x1024   背景を抜いた絵
      pixel_art_process(512x1024) -> (512x1024, 165x330)
    pixelated_image_original.png   165x330    ← 2つ目がそのまま保存される
      ゲームがそれを2倍に伸ばす                330x660
      reduce_image_colors(330x660) -> 330x660（16色）
    reduced_color_image.png        330x660    ← 立ち絵。会話中に見えるのはこれ
    face_image.png                            立ち絵から切り出す

## 立ち絵（`SHARP_PORTRAIT`）

2つの工程を包む。

  * `pixel_art_process`: 元の工程を呼んで**戻り値の形だけ借り**、
    `(絵, 絵の写し)` を返す。縮めない。入って来た絵をスレッドに控える。
    元の工程を呼ぶのは形の保険。ゲームの更新で戻り値の形が変われば
    2つ組の受け取りで投げ、`safe=True` が元の工程の結果（素の荒い絵）に落とす。
    呼ばずに形を決め打ちしていた版は、形が違ったときに生成スレッドごと死んで
    「NPC の絵が出ない」になった（VERIFICATION_LOG.md §2.80 の版1）。
    代償は 256x256 の KMeans 1回（0.3〜0.5秒）
  * `reduce_image_colors`: **控えた絵を返す**。減色しない。
    入って来るのはゲームが2倍に伸ばした 1024x2048 で、最近傍の2倍は情報を足さない。
    2倍の段は包めない位置にあるので、通した後で元の絵に戻す

立ち絵は `no_bg_image.png` と同じ 512x1024 になる。

顔の検出に失敗すると、ゲームは立ち絵をもう一度 `pixel_art_process` に通して
小さな全身（16x32 → 2倍で 32x64）を顔の代わりにする。
そこも素通しにすると立ち絵と同じ大きさの「顔」ができるので、
**減色を通った後の `pixel_art_process` はゲームのものを呼ぶ**。
順番はスレッドごとに `縮小 → 減色 → （失敗時）縮小` で、
生成は1体1スレッド（`Thread-N (generate_images)`）なので控えと旗はスレッドに置く。

縮小と減色は別々には切れない。縮めた絵の減色だけ飛ばしても細かさは戻らず、
縮小だけ飛ばすと 1024x2048 の 16色になる。

## 顔を切り直す

`detect_face_coordinates` が返すのは `(左, 上, 右, 下)` で、一辺は `crop_size`（256）。
ゲームはこの箱を **330/512 の定数で縮めてから**立ち絵から切る
（立ち絵が 330x660 だった頃の比。立ち絵の実寸からは求めていない）。
立ち絵を 512x1024 にすると顔が左上へずれるので、
`extract_and_save_face(pixelated_image, coordinates, output_path)` を包み、
ゲームに書かせた後で**同じ場所に切り直して上書きする**。
`coordinates` の一辺を 256 に戻せば検出した絵の座標系で、立ち絵はその絵と同じ寸法。
ゲームの側は触らず（戻り値もそのまま）、ファイルだけ差し替える。
`SHARP_PORTRAIT` が切のときは立ち絵が 330x660 で、ゲームの縮め方がそのまま合うので触らない。

## 先に YuNet で探す（`FACE_YUNET`。版19）

ゲームの1回目の検出（anime）の前に、YuNet（OpenCV の深層学習の顔検出。`faces.py`）で探す。
見つかれば、その箱から `faces.crop_box` でゲームの戻り値と同じ形を作って返し、ゲームの検出は呼ばない。
外したらゲームの検出（anime → haar）、それも外したら下のやり直しへ落とす。

手元の 695 体で（VERIFICATION.md §3.43「YuNet」）、カスケードの再現と YuNet の箱が食い違った 67 体は
保存された顔の約 19 体が服・鎧・飾りで、YuNet の箱はほぼ顔だった。
YuNet は 651 体を拾い、外した 44 体は目の大きいアニメ寄りの顔が中心で、そちらはカスケードが拾う。
1枚 30〜55ms。見つかればカスケードの2回は走らない。

モデルは同梱の `models/face_detection_yunet_2023mar.onnx`（MIT、NOTICE）。
パスに日本語があると OpenCV がファイルを開けないことがあるので、numpy で読んでバイト列で渡す。
読めない・作れない・投げたときは、ゲームの検出だけで探す（版18 と同じ動き）。

## 顔の検出をやり直す（`FACE_RETRY`）

ゲームの検出は手元の 364 体で 58 体（16%）を外していた（VERIFICATION.md §3.43）。
`detect_face_coordinates` は OpenCV のカスケード
（`lbpcascade_animeface.xml` → `haarcascade_frontalface_alt.xml` の順に2回呼ばれる。
手元の再現で 93% 一致）を既定の感度で掛けているだけで、
暗い絵・コントラストの低い絵で外れる。

包んで、**ゲームが `None` を返した回だけ**、前処理を変えた絵で同じ関数を呼び直す。
anime の回の `None` では呼び直さない。ここで拾って返すとゲームの haar の回が来なくなり、
素の haar で取れた顔を前処理の拾いで置き換える。やり直すのは haar の回（ゲームの最後の試行）が外れた後。
呼び直すのは包みを全部剥がした素の関数（`patch.unwrap`）で、フックが受け取った `orig` ではない。
`safe=True` の包みは最後に呼んだ `orig` の結果を本番の答えとして覚えるので、
探りの呼び出しで投げるとその例外がゲームへ素通しになる（TECH.md §6.1）。
呼び直しが投げた回は「拾えなかった」として次へ進む。

  * 前処理は座標系を変えないものだけ（均一化 / CLAHE / ガンマ / ぼかし）。
    ゲームの関数が返した値をそのまま渡せる
  * 呼び直す前に、同じカスケードでこちらでも検出して**位置で絞る**
    （全身の立ち絵なので顔は上寄り・程よい幅。一番上の箱を採る）。
    絞らずに緩めると鎧や靴を顔として拾う。
    絞りを通った前処理のときだけゲームの関数を呼び直す
  * カスケードは1回の呼び出しの中で両方試す（anime だけだと拾いが 42 → 24 に落ちる）。
    渡す形は**ゲームが渡して来た形に合わせる**。ゲームはフォルダ付き
    （`runtime/models/face_recognition/...`）で渡していて、素のファイル名では
    ゲームの関数がカスケードを読めず `None` を返す（版7〜9 がこれで拾えなかった）
  * 検出は縮小の前の絵に対して走るので、`SHARP_PORTRAIT` と独立に効く
  * 58 体のうち 42 体を拾い、目で見て 4 体が顔ではなかった

`cv2` / `numpy` / `PIL` はゲームが同梱しているものを、フックの中で遅延 import する。
ローダの起動時に読み込まないため。
検出の中身（前処理・位置の絞り・カスケードの順）は `faces.py` にあり、
既存の NPC を一括で直す道具（`tool.py`。DOC.md）と共有する。

## 効く範囲

**これから作られる画像だけ**。既にある絵は荒いまま残る。
荒くする工程は立ち絵と敵が同じ関数を通るので、工程の側では見分けられない。
入口の `generate_enemy_image`（モンスター・衛兵）と
`generate_enemy_image_from_character`（NPC の戦闘用の絵）を包み、
その中に居る間はスレッドに旗を立てて、立ち絵の処理と顔の処理を通さない。
入口の包みは `safe=True` にしない（投げたときにゲームが2度描く）。
後処理は自分で例外を受ける。

## 敵の絵（`SHARP_ENEMY`。工程と寸法は GAME.md §2.30）

    描いた絵 → 背景抜き → pixel_art_process → 縁 → 色調 → reduce_image_colors → ゲームが2倍にする

  * `pixel_art_process`: 立ち絵と同じく元を呼んで形だけ借り、`(絵, 絵の写し)` を返す
  * 縁（`add_border_to_image`）: `ENEMY_BORDER` が切なら通さない。太さは固定のピクセル数で、
    大きな絵では途切れた細い線になり、背景抜きの取り残しまで囲む
  * 色調（`apply_dark_fantasy_tone`）: 色調は残し、雑音だけ下限まで落とす
  * `reduce_image_colors`: 減色せず、入って来た絵を控えて返す
  * 2倍: 実寸を見て倍にするので 2048x2048 になる。包めない位置にあるので、
    入口が返した後で、戦闘画面が読むファイルを控えの絵で上書きする
  * 戦闘画面（`NearestNeighborImage`）: 縮小も最近傍なので細部がギザつく。
    敵の絵の縮小だけ線形にする。拡大は触らないので、既にある敵のドット絵は変わらない

表示の大きさは部品が決める（実寸を変えても同じ）。
`generate_enemy_image_from_character` は縮小して2倍に伸ばし、保存するだけで、縁・色調・減色を通らない。
2倍は同じように戻す。

## 背景（`SHARP_BACKGROUND`）

    描いた絵 → pixel_art_process → 色調 → reduce_image_colors

背景の工程は立ち絵の工程とは別の入口を持つ。
`pixel_art_process` は `image_generation_background` の写しを、色調と減色はモジュールの属性を
`alias_scan=False` で包むと、背景にだけ当たる（立ち絵と敵は `from` の写しを持つ。
元の1本に当てると張り替えで両方変わる）。
雑音は敵と同じく下限まで落とし、色調は残す。

## 背景抜き（`CUTOUT_MODEL`・`ENEMY_CUTOUT_MERGE`。`cutout.py`）

`remove_background` を包み、ゲームのフォルダのモデルで抜く。
`ENEMY_CUTOUT_MERGE` が入なら、敵の入口の中だけ2つのモデルを合わせて濃くする。
立ち絵は `CUTOUT_MODEL` に従う。
抜けなければ（ファイルが無い・推論で落ちた）ゲームの切り抜きへ戻す。

`CUTOUT_MODEL` は `343_` から移した設定で、`343_` の欄に残っていれば最初の起動で1度だけ移す。
"""

import os
import threading
import time

from instantale_modloader import config as loader_config
from instantale_modloader import imagegen, patch

from . import cutout, faces

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる。TECH.md §3.8.3）------------

# 縮小と減色を通さず、背景を抜いた絵をそのまま立ち絵にするか。
# 既定は切（ゲームのまま。330x660 のドット絵寄り）。画風が変わるので、入れるのは選んだ人だけ。
SHARP_PORTRAIT = False

# 敵の絵の縮小・減色・雑音を通さないか。既定は切（ゲームのまま）。
SHARP_ENEMY = False

# `SHARP_ENEMY` が入のとき、ゲームの白い縁を付けるか。既定は切。
ENEMY_BORDER = False

# 背景の縮小・減色・雑音を通さないか。既定は切（ゲームのまま）。
SHARP_BACKGROUND = False

# 背景抜きのモデル。game はゲームのまま（サイドカーの u2net）、
# isnet-anime はゲームのフォルダのアニメ絵向けのモデルで抜く（`cutout.py`）。
CUTOUT_MODEL = "game"

# 敵の背景抜きを、2つのモデルで合わせて濃くするか。既定は切（`CUTOUT_MODEL` のまま）。
ENEMY_CUTOUT_MERGE = False

# 顔の検出が外れた回に、前処理した絵でやり直すか。
FACE_RETRY = True

# ゲームの検出より先に YuNet で顔を探すか（版19）。外したらゲームの検出とやり直しへ落とす。
FACE_YUNET = True

#: この MOD の記録。
LOG_BASENAME = "sharp_portrait.log"

#: 荒くする工程の写しを持つモジュール（キャラクタ・敵・モンスター）。画像生成の方式ごとに別のモジュールに
#: 居るが、どの方式かはローダの `imagegen` が吸収する（GAME.md §2.33）。これは cuda のもの（後方互換の名前）。
CREATURE = imagegen.CREATURE_FORMAT.format(imagegen.FAMILIES["sdcpp_cuda"])

#: ゲームが選んでいる画像生成の方式。読めなければ None（ローダの `imagegen.backend`）。
config_backend = imagegen.backend

#: スレッドごとの控え。`source` は縮小に入って来た絵、`detect_width` はその幅、
#: `after_reduce` は「次の縮小は顔の代わりを作る回」の旗。
_LOCAL = threading.local()

#: 敵の絵の入口。この中に居る間は何も変えない（モンスター・衛兵と、NPC の戦闘用の絵）。
ENEMY_ENTRIES = ("generate_enemy_image", "generate_enemy_image_from_character")

#: ゲームが2倍にして保存し、戦闘画面が読む敵の絵（モンスター・衛兵と、NPC の戦闘用の絵）。
ENEMY_SHOWN = ("pixelated_image_resized.png", "opponent_image.png")

#: 背景の工程を持つモジュール。方式ごとに別のパッケージに居る。
BACKGROUND_FORMAT = "image_generation.{}.image_generation_background"

#: 背景の減色と色調が呼ぶ、モジュールの属性。
BACKGROUND_REDUCE = "scripts.image_processing.reduce_color:reduce_image_colors"
BACKGROUND_TONE = "scripts.image_processing.dark_fantasy_tone:apply_dark_fantasy_tone"

#: ゲームの背景の切り抜き（rembg のサイドカーへ頼む関数）。
CUTOUT_TARGET = "scripts.image_processing.request_remove_background:remove_background"

#: `CUTOUT_MODEL` を前に持っていた MOD の欄（設定の移し替えに使う）。
CUTOUT_OLD_OWNER = "343_stable_diffusion"

#: 戦闘画面の敵の絵の部品。
ENEMY_IMAGE_TEXTURE ="scripts.hud.new_hud:NearestNeighborImage.on_texture"

#: `apply_dark_fantasy_tone(img, color, contrast, brightness, randomize)` の、img を除いた randomize の位置。
TONE_RANDOMIZE_POS = 3

#: 色調の雑音の下限。0 を渡すとゲームの関数が `np.random.randint(0, 0)` で投げ、生成スレッドが死ぬ。
#: 1 なら画素値が最大1落ちるだけで見えない。
TONE_QUIET = 1

#: `detect_face_coordinates(image, cascade_path, padding, crop_size)` の引数名。
#: 位置で来ても名前で来ても同じ形に揃えて、カスケードだけ差し替えて呼び直す。
ARG_NAMES = ("cascade_path", "padding", "crop_size")


def dims(image):
    return "{}x{}".format(*image.size)


def quiet_tone(orig, image, args, kwargs):
    """色調はゲームのまま、雑音だけ下限まで落として呼ぶ。"""
    if len(args) > TONE_RANDOMIZE_POS:
        args = args[:TONE_RANDOMIZE_POS] + (TONE_QUIET,) + args[TONE_RANDOMIZE_POS + 1:]
    else:
        kwargs = dict(kwargs, randomize=TONE_QUIET)
    return orig(image, *args, **kwargs)


def take_over_cutout_setting(ctx, note):
    """`343_` の欄に残った `CUTOUT_MODEL` を、この MOD の欄へ1度だけ移す。"""
    global CUTOUT_MODEL
    runtime_dir = getattr(ctx, "runtime_dir", None)
    if not runtime_dir:
        return
    try:
        store = loader_config.load_store_for_write(runtime_dir)
        old = store.get(CUTOUT_OLD_OWNER)
        if not isinstance(old, dict) or "CUTOUT_MODEL" not in old:
            return
        value = old.pop("CUTOUT_MODEL")
        if not old:
            store.pop(CUTOUT_OLD_OWNER)
        own_name = os.path.basename(ctx.mod_dir)
        own = store.get(own_name)
        # この MOD の欄に既に値があれば、そちらを選び直したものとして尊重する。
        if value == "isnet-anime" and not (isinstance(own, dict) and "CUTOUT_MODEL" in own):
            store.setdefault(own_name, {})["CUTOUT_MODEL"] = value
            CUTOUT_MODEL = value
        loader_config.save_store(runtime_dir, store)
        note("設定: 背景抜きのモデル {} を {} から移した".format(value, CUTOUT_OLD_OWNER))
    except Exception as exc:
        note("設定: 背景抜きのモデルを移せず {}: {}".format(type(exc).__name__, exc))


def apply(ctx):
    # 打ち切らない。書くのは NPC の生成のたび（1体につき4〜5行）で、毎フレームではない。
    note = ctx.logger(LOG_BASENAME)
    take_over_cutout_setting(ctx, note)
    game_dir = ctx.game_dir
    # `ctx` の値は `apply()` の間だけ。カスケードの置き場は今のうちに控える。
    cascade_dir = os.path.join(ctx.game_dir, faces.CASCADE_DIR)
    yunet_path = os.path.join(ctx.mod_dir, faces.YUNET_MODEL)
    # YuNet のモデルのバイト列（1度読んだら使い回す）。読めなかったら `False` で、以後は試さない。
    yunet_model = []
    cascades = {}
    seen_args = []          # ゲームの呼び方を1度だけ記録するための印
    backend = config_backend()
    creatures = imagegen.creature_modules(backend)

    def hook(name, **options):
        """選ばれた方式（分からなければ4つ）の同じ関数を、同じ手で包む（ローダの `imagegen`）。"""
        return imagegen.wrap_creature(ctx, name, backend, **options)

    def enemy():
        return getattr(_LOCAL, "enemy", False)

    def sharp_enemy():
        return enemy() and SHARP_ENEMY

    def undo_doubling(result):
        """ゲームが2倍にして保存した敵の絵を、2倍にする前の絵で上書きする。"""
        source = getattr(_LOCAL, "enemy_source", None)
        if source is None:
            return
        paths = result if isinstance(result, (tuple, list)) else (result,)
        doubled = [p for p in paths if isinstance(p, str) and os.path.basename(p) in ENEMY_SHOWN]
        if not doubled:
            note("敵: 戻り値に戦闘画面の絵が無いので、2倍の絵のまま残す")
            return
        try:
            source.save(doubled[0])
            note("敵: 2倍の絵を {} に戻した".format(dims(source)))
        except Exception as exc:
            note("敵: 2倍の絵を戻せず {}: {}".format(type(exc).__name__, exc))

    def make_enemy_entry(name):
        @hook(name, required=False)
        def enemy_entry(orig, *args, **kwargs):
            _LOCAL.enemy = True
            _LOCAL.enemy_source = None
            try:
                result = orig(*args, **kwargs)
                undo_doubling(result)
                return result
            finally:
                _LOCAL.enemy = False
                _LOCAL.enemy_source = None

        return enemy_entry

    for entry in ENEMY_ENTRIES:
        make_enemy_entry(entry)

    @ctx.wrap(CUTOUT_TARGET, required=False, safe=True)
    def remove_background(orig, *args, **kwargs):
        """設定に従ってゲームのフォルダのモデルで抜く。抜けなければゲームの切り抜きへ戻す。"""
        image = args[0] if args else kwargs.get("image")
        merge = enemy() and ENEMY_CUTOUT_MERGE
        if image is None or not (merge or str(CUTOUT_MODEL) == "isnet-anime"):
            return orig(*args, **kwargs)
        needed = (cutout.ISNET, cutout.U2NET) if merge else (cutout.ISNET,)
        missing = [m[0] for m in needed if not os.path.isfile(cutout.model_path(game_dir, m))]
        if missing:
            note("背景抜き: {} が無いのでゲームの切り抜きで抜く".format(", ".join(missing)))
            return orig(*args, **kwargs)
        start = time.time()
        try:
            got = cutout.cut_merged(image, game_dir) if merge else cutout.cut(image, cutout.model_path(game_dir))
        except Exception as exc:
            note("背景抜き: 抜けなかったのでゲームの切り抜きで抜く（{}: {}）".format(type(exc).__name__, exc))
            return orig(*args, **kwargs)
        note("背景抜き: {} {} {:.2f} 秒".format("2つのモデル" if merge else "isnet-anime", dims(got), time.time() - start))
        return got

    @hook("add_border_to_image", required=False, safe=True, alias_scan=False)
    def add_border_to_image(orig, image, *args, **kwargs):
        if sharp_enemy() and not ENEMY_BORDER:
            return image
        return orig(image, *args, **kwargs)

    @hook("apply_dark_fantasy_tone", required=False, safe=True, alias_scan=False)
    def creature_tone(orig, image, *args, **kwargs):
        if sharp_enemy():
            note("敵: 色調 {} 雑音を {} に".format(dims(image), TONE_QUIET))
            return quiet_tone(orig, image, args, kwargs)
        return orig(image, *args, **kwargs)

    @ctx.wrap(ENEMY_IMAGE_TEXTURE, required=False, safe=True, alias_scan=False)
    def on_texture(orig, widget, *args, **kwargs):
        result = orig(widget, *args, **kwargs)
        if SHARP_ENEMY and os.path.basename(str(getattr(widget, "source", None) or "")) in ENEMY_SHOWN:
            texture = getattr(widget, "texture", None)
            if texture is not None:
                texture.min_filter = "linear"
        return result

    def background_pixel(orig, image, *args, **kwargs):
        if not SHARP_BACKGROUND:
            return orig(image, *args, **kwargs)
        # 元の工程は形の保険として呼ぶ。形が違えばここで投げ、safe=True が素に落とす。
        _big, _small = orig(image, *args, **kwargs)
        note("背景: 縮小 {} をそのまま通す".format(dims(image)))
        return image, image.copy()

    for family in imagegen.families(backend):
        ctx.wrap(BACKGROUND_FORMAT.format(family) + ":pixel_art_process",
                 required=False, safe=True, alias_scan=False)(background_pixel)

    @ctx.wrap(BACKGROUND_TONE, required=False, safe=True, alias_scan=False)
    def background_tone(orig, image, *args, **kwargs):
        if not SHARP_BACKGROUND:
            return orig(image, *args, **kwargs)
        note("背景: 色調 {} 雑音を {} に".format(dims(image), TONE_QUIET))
        return quiet_tone(orig, image, args, kwargs)

    @ctx.wrap(BACKGROUND_REDUCE, required=False, safe=True, alias_scan=False)
    def background_reduce(orig, image, *args, **kwargs):
        if not SHARP_BACKGROUND:
            return orig(image, *args, **kwargs)
        note("背景: 減色 {} をそのまま通す".format(dims(image)))
        return image

    @hook("pixel_art_process", safe=True, alias_scan=False)
    def pixel_art_process(orig, image, *args, **kwargs):
        if enemy():
            if not SHARP_ENEMY:
                return orig(image, *args, **kwargs)
            # 元の工程は形の保険として呼ぶ。形が違えばここで投げ、safe=True が素に落とす。
            _big, _small = orig(image, *args, **kwargs)
            # NPC の戦闘用の絵はこの後に減色を通らないので、ここでも控える。
            _LOCAL.enemy_source = image
            note("敵: 縮小 {} をそのまま通す".format(dims(image)))
            return image, image.copy()
        if getattr(_LOCAL, "after_reduce", False):
            _LOCAL.after_reduce = False
            note("縮小: {} 顔の代わりを作る回なのでゲームに任せる".format(dims(image)))
            return orig(image, *args, **kwargs)
        # 顔の箱は検出した絵の座標系。検出もこの絵（同じ寸法）に対して走る。
        _LOCAL.detect_width = image.size[0]
        if not SHARP_PORTRAIT:
            note("縮小: {} ゲームのまま".format(dims(image)))
            return orig(image, *args, **kwargs)
        # 元の工程は形の保険として呼ぶ。形が違えばここで投げ、safe=True が素に落とす。
        _big, _small = orig(image, *args, **kwargs)
        _LOCAL.source = image
        note("縮小: {} をそのまま通す".format(dims(image)))
        return image, image.copy()

    @hook("reduce_image_colors", safe=True, alias_scan=False)
    def reduce_image_colors(orig, image, *args, **kwargs):
        if enemy():
            if not SHARP_ENEMY:
                return orig(image, *args, **kwargs)
            _LOCAL.enemy_source = image
            note("敵: 減色 {} をそのまま通す".format(dims(image)))
            return image
        _LOCAL.after_reduce = True
        source = getattr(_LOCAL, "source", None)
        _LOCAL.source = None
        if not SHARP_PORTRAIT:
            note("減色: {} ゲームのまま".format(dims(image)))
            return orig(image, *args, **kwargs)
        if source is None:
            note("減色: {} 控えが無いのでそのまま通す".format(dims(image)))
            return image
        note("減色: {} を控えの {} に戻す".format(dims(image), dims(source)))
        return source.copy()

    def cascade_for(name):
        """ゲームと同じカスケードをこちらでも持つ（1度読んだら使い回す）。"""
        import cv2
        path = name if os.path.isabs(name) else os.path.join(cascade_dir, os.path.basename(name))
        if path not in cascades:
            cascades[path] = cv2.CascadeClassifier(path)
        return cascades[path]

    def raw_detector():
        """呼び直しに使うゲームの素の検出関数（包みを全部剥がしたもの）。引けなければ None。"""
        module = imagegen.creature_module(backend)
        current = vars(module).get("detect_face_coordinates") if module is not None else None
        if not callable(current):
            return None
        return patch.unwrap(current)[0]

    def yunet_crop(image, rest):
        """YuNet で顔を探し、ゲームの戻り値と同じ形 `(左, 上, 右, 下)` で返す。無ければ None。"""
        import cv2
        import numpy as np
        if not yunet_model:
            model = faces.read_yunet(yunet_path, np)
            yunet_model.append(model if model is not None else False)
            if model is None:
                note("顔: YuNet のモデルが読めない（{}）。ゲームの検出だけで探す".format(yunet_path))
        model = yunet_model[0]
        if model is False:
            return None
        detector = faces.make_yunet(model, cv2, np)
        if detector is None:
            note("顔: YuNet の検出器が作れない。ゲームの検出に任せる")
            return None
        bgr = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        hit = faces.yunet_face(bgr, detector)
        if hit is None:
            note("顔: YuNet は見つけられず、ゲームの検出に任せる")
            return None
        box, score = hit
        found = faces.crop_box(box, image.size, int(rest.get("crop_size") or faces.FACE_CROP))
        note("顔: YuNet が見つけた {}（箱 {}、確かさ {:.2f}）".format(found, box, score))
        return found

    @hook("detect_face_coordinates", required=False, safe=True, alias_scan=False)
    def detect_face_coordinates(orig, image, *args, **kwargs):
        if enemy():
            return orig(image, *args, **kwargs)
        rest = dict(zip(ARG_NAMES, args))
        rest.update(kwargs)
        given = str(rest.get("cascade_path") or faces.CASCADES[0])
        first = os.path.basename(given) == faces.CASCADES[0]
        if FACE_YUNET and first:
            # ゲームの1回目（anime）の前に YuNet で探す。見つけて返せばゲームの2回目は来ない。
            # 探りで投げてもゲームの検出へ進む（`safe=True` に頼らない。まだ `orig` を呼んでいない）。
            try:
                found = yunet_crop(image, rest)
            except Exception as exc:
                note("顔: YuNet で投げた {}: {}。ゲームの検出に任せる".format(type(exc).__name__, exc))
                found = None
            if found is not None:
                return found
        found = orig(image, *args, **kwargs)
        if found is not None:
            note("顔: ゲームが見つけた {}".format(found))
            return found
        if not FACE_RETRY:
            note("顔: ゲームは見つけられず、やり直しは切")
            return None
        if first:
            # ゲームは1回目（anime）が None なら2回目（haar）を素の絵で呼ぶ（GAME.md §2.30）。
            # ここで拾って返すと2回目が来ず、素の haar で取れた顔を前処理の拾いで置き換える。
            # やり直しはゲームの試行が全部外れた後に限る。
            note("顔: ゲームは {} で見つけられず、ゲームの次の試行に任せる".format(faces.short_name(given)))
            return None
        raw = raw_detector()
        if raw is None:
            note("顔: ゲームは見つけられず、呼び直す素の関数が引けないのでやり直さない")
            return None
        import cv2
        import numpy as np
        import PIL.Image
        # ゲームがカスケードをどう渡しているか（素の名前か、フォルダ付きか）は
        # ここでしか分からない。1度だけ記録する。
        if not seen_args:
            seen_args.append(True)
            note("顔: ゲームの呼び方 {}".format(rest or "（引数なし。既定のまま）"))
        # 呼び直しに渡すカスケードは、ゲームが渡して来た形（`given`）に合わせる。
        # フォルダ付きで来ていれば同じフォルダ、素の名前なら素の名前。
        gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        tried = []
        for prep in faces.FACE_PREPS:
            done = faces.preprocess(gray, prep, cv2, np)
            prepped = None
            for name in faces.CASCADES:
                box = faces.pick_face(done, cascade_for(name))
                if box is None:
                    continue
                if prepped is None:
                    # 前処理した絵を、入って来たのと同じ形（RGBA なら α も）でゲームに渡す。
                    prepped = PIL.Image.fromarray(np.dstack([done, done, done]), "RGB")
                    if "A" in image.getbands():
                        prepped.putalpha(image.getchannel("A"))
                path = os.path.join(os.path.dirname(given), name) if os.path.dirname(given) else name
                short = faces.short_name(name)
                try:
                    again = raw(prepped, **dict(rest, cascade_path=path))
                except Exception as exc:
                    # 探りの失敗はゲームへ返さない（素の答えはもう None で確定している）。
                    tried.append("{}+{}={} で投げた {}".format(prep, short, box, type(exc).__name__))
                    continue
                if again is not None:
                    note("顔: ゲームは見つけられず、{} + {} で拾った {}（箱 {}）".format(
                        prep, short, again, box))
                    return again
                # こちらは見えたのにゲームの関数は None。次の前処理へ。
                tried.append("{}+{}={}".format(prep, short, box))
        note("顔: 見つからず（{} 通り試した{}）".format(
            len(faces.FACE_PREPS),
            "。こちらは見えたがゲームの関数は None: " + " / ".join(tried) if tried else ""))
        return None

    @hook("extract_and_save_face", required=False, safe=True, alias_scan=False)
    def extract_and_save_face(orig, pixelated_image, coordinates, output_path, *args, **kwargs):
        # 引数名は本体と同じにする（キーワードで渡されても二重にならない。版17）。
        result = orig(pixelated_image, coordinates, output_path, *args, **kwargs)
        image = pixelated_image
        if enemy():
            return result
        # 顔が切られた＝見つかった。顔の代わりを作る縮小は来ないので旗を降ろす
        # （スレッドが使い回されても次の NPC の縮小に旗が残らない）。
        _LOCAL.after_reduce = False
        if not SHARP_PORTRAIT:
            return result          # 立ち絵は 330x660 で、ゲームの縮め方がそのまま合う
        left, top, right, bottom = (float(v) for v in coordinates)
        side = max(right - left, 1.0)
        # ゲームが縮めた箱を検出した絵の座標系（一辺 256）に戻し、
        # 立ち絵が検出した絵と寸法が違えばその比で合わせる。
        # 縮めた箱は整数に丸まっている（165 と 166 が混ざる）ので、
        # 四隅を戻すのではなく中心を戻して一辺 256 の正方形に組み直す。
        ratio = image.size[0] / float(getattr(_LOCAL, "detect_width", None) or image.size[0])
        scale = (faces.FACE_CROP / side) * ratio          # 縮めた座標 → 立ち絵の座標
        half = int(round(faces.FACE_CROP * ratio / 2.0))
        cx = int(round((left + right) / 2.0 * scale))
        cy = int(round((top + bottom) / 2.0 * scale))
        box = (cx - half, cy - half, cx + half, cy + half)
        if box[2] > image.size[0] or box[3] > image.size[1] or box[0] < 0 or box[1] < 0:
            note("顔: 箱 {} が立ち絵 {} からはみ出すので切り直さない".format(box, dims(image)))
            return result
        image.crop(box).save(output_path)
        note("顔: {} を {} に戻して切り直した".format(
            tuple(int(v) for v in (left, top, right, bottom)), box))
        return result

    ctx.log("sharp portrait: installed (backend {!r}; wraps {})".format(backend, ", ".join(creatures)))
