# -*- coding: utf-8 -*-
"""立ち絵を荒くする工程を通さず、顔の検出が外れた回はやり直す。

ゲームは Stable Diffusion で描いた絵を、保存する前に**わざと荒くしている**
（経路と寸法は GAME.md §2.30）:

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

## 顔の検出をやり直す（`FACE_RETRY`）

ゲームの検出は手元の 364 体で 58 体（16%）を外していた（VERIFICATION.md §3.43）。
`detect_face_coordinates` は OpenCV のカスケード
（`lbpcascade_animeface.xml` → `haarcascade_frontalface_alt.xml` の順に2回呼ばれる。
手元の再現で 93% 一致）を既定の感度で掛けているだけで、
暗い絵・コントラストの低い絵で外れる。

包んで、**ゲームが `None` を返した回だけ**、前処理を変えた絵で同じ関数を呼び直す。

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

**これから作られる画像だけ**。既にある NPC の絵は荒いまま残る。
背景は触らない（`image_generation_background` が持つ写しには当てない。
`alias_scan=False` はそのため ― 元の1本に当てると張り替えで両方変わる）。
"""

import os
import threading

from . import faces

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる。TECH.md §3.8.3）------------

# 縮小と減色を通さず、背景を抜いた絵をそのまま立ち絵にするか。
# 切るとゲームのまま（330x660 のドット絵寄り）。
SHARP_PORTRAIT = True

# 顔の検出が外れた回に、前処理した絵でやり直すか。
FACE_RETRY = True

#: この MOD の記録。
LOG_BASENAME = "sharp_portrait.log"

#: 荒くする工程の写しを持つモジュール（キャラクタ・敵・モンスター）。
CREATURE = "image_generation.sdcppcuda.image_generation_creature"

#: スレッドごとの控え。`source` は縮小に入って来た絵、`detect_width` はその幅、
#: `after_reduce` は「次の縮小は顔の代わりを作る回」の旗。
_LOCAL = threading.local()

#: `detect_face_coordinates(image, cascade_path, padding, crop_size)` の引数名。
#: 位置で来ても名前で来ても同じ形に揃えて、カスケードだけ差し替えて呼び直す。
ARG_NAMES = ("cascade_path", "padding", "crop_size")


def dims(image):
    return "{}x{}".format(*image.size)


def apply(ctx):
    # 打ち切らない。書くのは NPC の生成のたび（1体につき4〜5行）で、毎フレームではない。
    note = ctx.logger(LOG_BASENAME)
    # `ctx` の値は `apply()` の間だけ。カスケードの置き場は今のうちに控える。
    cascade_dir = os.path.join(ctx.game_dir, faces.CASCADE_DIR)
    cascades = {}
    seen_args = []          # ゲームの呼び方を1度だけ記録するための印

    @ctx.wrap(CREATURE + ":pixel_art_process", safe=True, alias_scan=False)
    def pixel_art_process(orig, image, *args, **kwargs):
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

    @ctx.wrap(CREATURE + ":reduce_image_colors", safe=True, alias_scan=False)
    def reduce_image_colors(orig, image, *args, **kwargs):
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

    @ctx.wrap(CREATURE + ":detect_face_coordinates",
              required=False, safe=True, alias_scan=False)
    def detect_face_coordinates(orig, image, *args, **kwargs):
        found = orig(image, *args, **kwargs)
        if found is not None:
            note("顔: ゲームが見つけた {}".format(found))
            return found
        if not FACE_RETRY:
            note("顔: ゲームは見つけられず、やり直しは切")
            return None
        import cv2
        import numpy as np
        import PIL.Image
        rest = dict(zip(ARG_NAMES, args))
        rest.update(kwargs)
        # ゲームがカスケードをどう渡しているか（素の名前か、フォルダ付きか）は
        # ここでしか分からない。1度だけ記録する。
        if not seen_args:
            seen_args.append(True)
            note("顔: ゲームの呼び方 {}".format(rest or "（引数なし。既定のまま）"))
        # 呼び直しに渡すカスケードは、ゲームが渡して来た形に合わせる。
        # フォルダ付きで来ていれば同じフォルダの haar、素の名前なら素の名前。
        given = str(rest.get("cascade_path") or faces.CASCADES[0])
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
                again = orig(prepped, **dict(rest, cascade_path=path))
                short = faces.short_name(name)
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

    @ctx.wrap(CREATURE + ":extract_and_save_face",
              required=False, safe=True, alias_scan=False)
    def extract_and_save_face(orig, image, coordinates, output_path, *args, **kwargs):
        result = orig(image, coordinates, output_path, *args, **kwargs)
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

    ctx.log("sharp portrait: installed")
