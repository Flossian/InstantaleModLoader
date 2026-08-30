# -*- coding: utf-8 -*-
"""荒くする工程が返す絵を、元の絵から作り直した同じ寸法の絵に差し替える。

ゲームは Stable Diffusion で描いた絵を、保存する前に**わざと荒くしている**。
実機で採った経路（2026-08-30。DOC.md §3.2）:

    generated_image.png            512x1024   SD の出力
    no_bg_image.png                512x1024   背景を抜いた絵
      pixel_art_process(512x1024) -> (512x1024, 165x330)
    pixelated_image_original.png   165x330    ← 2つ目がそのまま保存される
      それを2倍にして 330x660
      reduce_image_colors(330x660) -> 330x660
    reduced_color_image.png        330x660    ← 立ち絵。会話中に見えるのはこれ
    face_image.png                            立ち絵から切り出す

**捨てられているのは寸法**。165x330 まで落として2倍に伸ばしているので、
立ち絵 330x660 の中身は 165x330 ぶんの細かさしか無い。

## 何を差し替えるか

`pixel_art_process` の戻り値は2つ組で、**1つ目は入って来た絵そのもの、
2つ目が縮めた絵**。ゲームは2つ目しか使わない。
版2は「入力と同じ寸法の画像」だけを差し替えていたので、
1つ目（＝捨てられる側）だけを直していて、絵は荒いままだった。

版3は**戻って来た画像を全部、元の絵から作り直す**。
`165x330` の枠には元の絵を 165x330 へ縮めたものを入れる（LANCZOS。
ゲームは最近傍で潰していた）。寸法は1つも変えない。

減色（`reduce_image_colors`）の段では、入って来るのはもう潰れた 330x660 なので、
そこから細かさは戻せない。
**1つ手前の `pixel_art_process` に入って来た元の絵をスレッドごとに控えておき**、
それを 330x660 へ縮めたものを返す。これで立ち絵 330x660 の中身が
「元の絵を 330x660 へ縮めたもの」になる。

    控えは1度きり（使ったら捨てる）。
    生成は1体につき1スレッド（`Thread-N (generate_images)`）で、
    控えるのは毎回その回の入力なので、別人の絵が回り込むことはない。
    控えが無い状態で減色だけが来たら、その回の入力を使う（素より悪くはしない）。

## 寸法は変えない

立ち絵は 330x660 のまま。
`165x330 -> 2倍` を決めているのはゲーム側で、そこを動かすと
顔の切り出しや表示の寸法まで連鎖する。
細かさの上限が 330x660 になるのはそのため
（SD が描いた 512x1024 には届かない。DOC.md §3.3）。

## どこに仕掛けるか

    image_generation.sdcppcuda.image_generation_creature:pixel_art_process
    image_generation.sdcppcuda.image_generation_background:pixel_art_process
    image_generation.sdcppcuda.image_generation_creature:reduce_image_colors
    image_generation.sdcppcuda.image_generation_creature:detect_face_coordinates（記録だけ）

実体は `scripts.image_processing.image_to_pixel:pixel_art_process` 1本で、
上の2つのモジュールが `from ... import` で写しを持っている。
**元の1本ではなく写しの側に当てる**（`alias_scan=False`）。
元に当てるとエイリアス張り替えでキャラクタと背景の両方が一度に変わり、
「キャラクタだけ高画質、背景はゲームのまま」を選べなくなる。

`detect_face_coordinates` は**何も変えない**。
顔がどの絵のどこから切られたかを1行ずつ記録するだけ
（版2の回で顔が 32x64 になった理由がまだ分かっていない。DOC.md §3.3）。

## 戻り値の形は決めない

版1は `orig` を呼ばずに画像1枚を返し、実機で生成スレッドを殺した
（`TypeError: cannot unpack non-iterable Image object`。DOC.md §3.1）。
以来、**元の工程は必ず呼び、返って来た構造をなぞって中身だけ入れ替える**。
tuple でも list でも画像1枚でも同じように通る。

縦横の比が元の絵と違う画像は触らない（パレットのような添え物のため）。
実測では戻って来る画像はどれも縦横比 0.5 で揃っている。

## 効く範囲

**これから作られる画像だけ**。
既にある NPC の絵は荒いまま残る。

## 割り切り

  * 絵の見た目が変わる。ドット絵寄りの画風は、荒くする工程が作っていた
    ものなので、差し替えると滑らかな絵になる
  * 立ち絵の細かさの上限は 330x660。SD の 512x1024 には届かない（上記）
  * 背景は既定で触らない。背景の減色は別経路（`reduce_color` モジュール越し）
    なので、背景を入れても減色までは追っていない
"""

import sys
import threading

# キャラクタ・敵・モンスターの絵を作り直すか。
# この MOD の本題。
BYPASS_CHARACTER = True

# 背景の絵も作り直すか。
# 既定は切（ゲームのまま）。
BYPASS_BACKGROUND = False

# 減色の段も作り直すか。
# **ここが立ち絵の中身を決める**（`pixel_art_process` の側だけでは効かない）。
BYPASS_COLOR_REDUCE = True

# 記録をログに出す回数（工程ごとに数える）。
# 効いているかの確認用なので数回で足りる。
# **`mod.json` の "default" と揃えること**（`tools/check_mods.py` が
# AST で突き合わせる。TECH.md §3.8.3）。
LOG_LIMIT = 8

#: この MOD の記録。
LOG_BASENAME = "image_no_pixelate.log"

#: 荒くする工程を写しで持っている2つのモジュール。
CREATURE = "image_generation.sdcppcuda.image_generation_creature"
BACKGROUND = "image_generation.sdcppcuda.image_generation_background"

#: 縦横比がこれより離れている画像は差し替えない（添え物とみなす）。
ASPECT_TOLERANCE = 0.05

#: 生成スレッドごとの控え。工程をまたいで元の絵を渡すために使う。
_LOCAL = threading.local()

#: PIL の縮小フィルタ。1度引いたら覚える（PIL を import しないため）。
_RESAMPLE = []


def is_image(value):
    """PIL の Image かどうかを、PIL を import せずに持ち物で見る。

    ゲームの中では `from PIL import Image` が通るが、
    それだけのために MOD がゲームの同梱ライブラリに依存するのを避ける。
    `size` と `copy` だけでは numpy の配列も通ってしまうので、
    PIL にしか無い `mode` と `convert` を一緒に見る。
    """
    return all(hasattr(value, name)
               for name in ("copy", "convert", "resize", "mode", "size"))


def resample_filter(image):
    """縮小に使うフィルタ。画像の出どころのモジュールから引く。

    既定（最近傍）で縮めると、ゲームがやっているのと同じ潰し方になる。
    取れなければ `None` を返し、呼ぶ側は PIL の既定に任せる。
    """
    if _RESAMPLE:
        return _RESAMPLE[0]
    module = sys.modules.get(type(image).__module__)
    found = None
    for name in ("LANCZOS", "ANTIALIAS", "BICUBIC"):
        found = getattr(module, name, None)
        if found is not None:
            break
    _RESAMPLE.append(found)
    return found


def aspect_matches(source, size):
    """縦横比が元の絵とほぼ同じか。添え物の小さな絵を避けるため。"""
    try:
        want = float(source.size[0]) / float(source.size[1])
        have = float(size[0]) / float(size[1])
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return False
    return abs(want - have) <= ASPECT_TOLERANCE * max(want, have)


def fit(source, size):
    """`source` を `size` の写しにする。同じ寸法ならただの写し。"""
    if tuple(source.size) == tuple(size):
        return source.copy()
    found = resample_filter(source)
    if found is None:
        return source.resize(tuple(size))
    return source.resize(tuple(size), found)


def rebuild(value, source, swapped):
    """`value` の中の画像を、`source` から作り直した同じ寸法の絵に差し替える。

    戻り値の形（画像1枚 / tuple / list）を決め打ちしないのが要点。
    `swapped` は差し替えた枚数を数えるための1要素のリスト。
    """
    if is_image(value):
        if not aspect_matches(source, value.size):
            return value
        swapped[0] += 1
        return fit(source, value.size)
    if isinstance(value, tuple):
        return tuple(rebuild(item, source, swapped) for item in value)
    if isinstance(value, list):
        return [rebuild(item, source, swapped) for item in value]
    return value


def describe(value):
    """ログ用に戻り値の形を書く。`(512x1024 RGBA、165x330 RGBA)` のような1行。"""
    if is_image(value):
        try:
            return "{}x{} {}".format(value.size[0], value.size[1], value.mode)
        except Exception:
            return "画像"
    if isinstance(value, (tuple, list)):
        return "({})".format("、".join(describe(item) for item in value))
    return type(value).__name__


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    warn_once = ctx.warner("image no pixelate")
    seen = {}

    def count(label):
        seen[label] = seen.get(label, 0) + 1
        return seen[label]

    def redraw(label, orig, image, args, kwargs, source=None):
        """元の工程を走らせ、返って来た画像を元の絵から作り直したものに替える。"""
        result = orig(image, *args, **kwargs)
        src = source if is_image(source) else image
        if not is_image(src):
            warn_once(label, "{}: 画像ではない値（{}）が来たので触らない"
                             .format(label, type(src).__name__))
            return result
        swapped = [0]
        rebuilt = rebuild(result, src, swapped)
        times = count(label)
        if swapped[0] == 0:
            # 差し替えられる画像が1枚も無い＝戻り値の読みが外れている。
            # ゲームのものをそのまま返す（絵は荒いが、生成は止まらない）。
            warn_once(label, "{}: 差し替える画像が見つからない（元絵 {} / 戻り {}）"
                             .format(label, describe(src), describe(result)))
            return result
        if times <= LOG_LIMIT:
            write("{}: {}枚 作り直し 元絵 {} / 入力 {} / 戻り {}（{}回目）".format(
                label, swapped[0], describe(src), describe(image),
                describe(result), times))
        return rebuilt

    on = []

    if BYPASS_CHARACTER:
        # safe=True: ここが壊れても荒い絵に落ちるだけで、絵は出る。
        # alias_scan=False: 背景側の写しを巻き添えにしない（docstring 参照）。
        @ctx.wrap(CREATURE + ":pixel_art_process", safe=True, alias_scan=False)
        def creature_pixel(orig, image, *args, **kwargs):
            # 減色の段へ渡す控え。毎回その回の入力で上書きする。
            if is_image(image):
                _LOCAL.source = image
            return redraw("キャラクタ", orig, image, args, kwargs)

        on.append("キャラクタ")

    if BYPASS_BACKGROUND:
        # 背景は控えを置かない（キャラクタの減色へ回り込ませない）。
        @ctx.wrap(BACKGROUND + ":pixel_art_process", safe=True, alias_scan=False)
        def background_pixel(orig, image, *args, **kwargs):
            return redraw("背景", orig, image, args, kwargs)

        on.append("背景")

    if BYPASS_COLOR_REDUCE:
        @ctx.wrap(CREATURE + ":reduce_image_colors", safe=True, alias_scan=False)
        def creature_colors(orig, image, *args, **kwargs):
            # 控えは1度きり。使い回すと別人の絵が回り込みうる。
            source = getattr(_LOCAL, "source", None)
            _LOCAL.source = None
            return redraw("減色", orig, image, args, kwargs, source=source)

        on.append("減色")

    # 何も変えない。顔がどの絵のどこから切られたかを控えるだけ。
    # 版2の回で顔が 32x64 になった理由がまだ分かっていない（DOC.md §3.3）。
    @ctx.wrap(CREATURE + ":detect_face_coordinates",
              required=False, safe=True, alias_scan=False)
    def face_coordinates(orig, image, *args, **kwargs):
        result = orig(image, *args, **kwargs)
        times = count("顔の検出")
        if times <= LOG_LIMIT:
            write("顔の検出: 入力 {} / 座標 {}（{}回目）".format(
                describe(image), result, times))
        return result

    if on:
        write("作り直しを仕掛けた: {}".format(" / ".join(on)))
    else:
        # 何も入っていない状態で包みだけ残さない（TECH.md §3.1 の 900_ と同じ）。
        write("全て切になっている。顔の検出の記録だけ残す")
    ctx.log("image no pixelate: installed ({})".format(
        " / ".join(on) if on else "off"))
