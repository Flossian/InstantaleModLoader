# -*- coding: utf-8 -*-
"""修正: アイテム説明欄が固定サイズで、長い説明・長い名前が切れるのを直す。

## 何が起きているか（寸法と根拠は GAME.md §2.14）

`ItemDetailBox` は箱ごと固定（`size_hint=(None, None)`）で、
中のラベルも `text_size` の高さが固定されている。
`max_lines` は 0（無制限）なので**行数で切られているのではない**。
入らない行が描かれていないだけで、説明は半角24文字 × 3行 = 72文字までしか出ない。

同じ作りなので **`name_label`（50px＝1行分）と
`attributes_label`（225px）も超えれば切れる**。
名前が長いアイテムや属性が増えたアイテムで同じことが起きる。

直し方を決めているのは、GAME.md §2.14 に挙げた次の4点:

* 子の位置は `pos_hint` の `top` 分数で、箱の高さに追従する。
  箱を伸ばせば中身は自分で並び直すので、座標を1つずつ計算し直す必要はない
* 余白と隙間は `上余白25 + 50 + 225 + 150 + 下余白50 = 500`。
  ラベル間の隙間は 0 で、見た目の余白は**ラベルが自分の高さの中に持っている**
* 箱はホバーのたびに作り直され、
  `update_content` の時点で子はまだ一度もレイアウトされていない。
  したがって**絶対座標から設計を読んではいけない**。
  余白は `pos_hint` の分数と箱の高さから求める（レイアウト前でも正しい）
* **箱は上端を固定して置かれている**（所持品グリッドの上端と一致）。
  高さを変えるときは上端を保って下へ伸ばす（`y` を据え置くと上へ伸びて位置が浮く）

## 直し方

寸法を発明しない（`108_` と同じ立場）。
ゲームが決めた配置を「最低の高さ」としてそのまま残し、**文字がそれ以上を要求したときだけ、
その分だけ伸ばす**。

1. 各ラベルの書体・行間・余白を写した**画面に出ない複製**（`kivy.core.text.Label`）を
   `(元の幅, None)` で焼き、その高さを読む（`_probe_height`）。
   これが文字の要求する高さ。
   幅はこちらで決めない（ゲームの 316/300 のまま）。
   表示中のラベルは測定に使わない（測るだけでテクスチャが焼き直され、
   `text_size` も測定用の値のまま残るため。同関数の説明）。
2. 高さは `max(要求, ゲームの設計値)`。
   普通の長さのアイテムでは設計値が勝つので **見た目は今までと1px も変わらない**。
3. 箱の高さ ＝ 余白と隙間（設計のまま）＋ 各ラベルの高さ。
   `pos_hint` の
   `top` を新しい箱の高さに合わせた分数に直す（隙間は設計値をそのまま使う）。

## 横幅

縦だけ伸ばすと、長い説明で細長い柱になる。
横も可変にする。
ただし**広げる量をこちらの好みで決めない**。
基準はゲーム自身が決めた**箱の縦横比 500:333 ＝ 1.50**:

    その幅で折り返した結果が設計の比より縦長になる間は、1段ずつ広げる

これで「文字が多いほど横にも広がる」が、
比というゲームの設計から取った値だけで決まる。
上限は窓の右端まで（左端はゲームがアイテムの右端に合わせているので、
そこから右に取れる分がそのまま実測の上限になる）。
窓が読めないときの予備として設計幅の `WIDEST` 倍で頭打ちにする。

左右の余白は設計から逆算して保つ（箱の幅 − 折り返し幅 ＝ 名前 17 / 説明 33）。
幅も高さと同じく設計値より狭くはしないので、短いアイテムの見た目は変わらない。

設計値は**箱ごとに、こちらが触る前の初回に**写し取る。
以後はそれを基準にするので、マウスが動くたびに
`update_content` が走っても値が育っていかない。
ゲームが箱を組み直したとき（幅が変わったとき）だけ写し直し、
そのとき **こちらが当てた高さを設計値と取り違えない**よう、
当てた値と一致する項目は前回の設計値を引き継ぐ。

## 画面外への対策

箱は `pos_hint` を持たず、位置は `update_content` の後で誰かが直接入れている（写し取った時点で
`opacity=0`）。
伸ばした箱が画面からはみ出す可能性があるので、次のフレームに窓の内側へ収める。
誰が位置を決めているかを知らなくても効く。

それでも入り切らないほど説明が長い場合（LLM 生成なのでありうる）は、
窓の高さで頭打ちにして、あふれる分は説明ラベルから削る。
設計値までは削らない。
この場合だけは今までどおり末尾が切れるが、窓に入る限りは全部出る。
"""

import sys
import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "item_detail_autosize.log"

#: 設計値の置き場（`sys` の属性名）。注入し直しをまたいで残す。
DESIGN_STORE_ATTR = "__instantale_item_detail_designs__"

# ログに残す組み合わせの上限。
# 1アイテムにつき1件なので少しでいい。
MAX_LOG = 60

# 横幅を広げるときの1回ぶんの倍率と、設計幅の何倍までにするか。
# 窓の右端までという実測の制約が先に効くことが多く、これはその予備の頭打ち。
WIDEN_STEP = 1.25
WIDEST = 3.0

# 縦横比の判定にどれだけ余裕を持たせるか（px）。
# 設計そのものが比のちょうど上に乗っている（500 = 1.5015 × 333）ので、
# 丸め誤差だけで「縦長」と判定されて短いアイテムまで広がってしまう。
# 1px 未満の差は見た目に出ない。
RATIO_SLACK = 1.0

# 上から並ぶ順。
# 実測の top（1105 > 1055 > 830）がこの順だった。
LABEL_NAMES = ("name_label", "attributes_label", "desc_label")

# 測った高さの控えの上限。
# アイテムを見て回ると増えるので、際限なく持たない（溢れたら丸ごと捨てて測り直す。
# 控えは速さのためだけで、正しさには要らない）。
MEASURE_CACHE_MAX = 256


def apply(ctx):
    new_hud = sys.modules.get("scripts.hud.new_hud")
    if new_hud is None:
        ctx.log("scripts.hud.new_hud not loaded; skipping", level="WARN")
        return
    if getattr(new_hud, "ItemDetailBox", None) is None:
        ctx.log("ItemDetailBox not found; skipping", level="WARN")
        return

    # 箱ごとの設計値。
    # 箱が捨てられたら一緒に消えるよう弱参照で持つ。
    #
    # **置き場は `sys`。** `apply()` は1プロセスで何度も走るので、
    # ここで作り直すと2回目の注入が「1回目に広げた箱」を測り直して
    # それを設計として覚える。`design["heights"]` は下限に使うので、
    # 以後その箱は二度と縮まなくなる（`112_` / `113_` が設計を
    # ウィジェット側に貼っているのと同じ理由）。
    designs = getattr(sys, DESIGN_STORE_ATTR, None)
    if designs is None:
        designs = weakref.WeakKeyDictionary()
        setattr(sys, DESIGN_STORE_ATTR, designs)
    logged = set()

    write = ctx.logger(LOG_BASENAME)

    # -- 対象の取り出し ----------------------------------------------------
    def labels_of(box):
        """3つのラベルを上から順に。1つでも欠けたら何もしない（None を返す）。"""
        out = []
        for name in LABEL_NAMES:
            widget = frames.attr(box, name)
            if widget is frames.MISSING or widget is None:
                return None
            # 高さを測るのに使う。
            # 無ければこの修正は成り立たない。
            if frames.attr(widget, "texture_update") is frames.MISSING:
                return None
            out.append(widget)
        return out

    def text_width(label):
        """ラベルの折り返し幅。ここはゲームが決めた値をそのまま使う。

        既定を None にするのは、`frames.attr` の番人が文字列だから。
        `text_size` を持たない相手では `"<missing>"[0]` が `"<"` になり、
        幅として通ってしまう（呼び側の `float()` が ValueError で落ち、
        広い except に吸われて MOD が黙って何もしなくなる）。
        """
        size = frames.attr(label, "text_size", None)
        try:
            width = size[0]
        except Exception:
            width = None
        return width if width else frames.attr(label, "width", 0)

    # -- 設計値（＝ゲームが決めた配置）の写し取り --------------------------
    def top_hint(label):
        """`pos_hint['top']` の分数。無ければ None（そのときは何もしない）。"""
        hint = frames.attr(label, "pos_hint")
        if not isinstance(hint, dict):
            return None
        value = hint.get("top")
        return value if isinstance(value, (int, float)) else None

    def capture(box, labels):
        """箱の高さ・ラベルの高さ・余白・隙間を設計から取る。

        座標は使わない。
        箱は作り直された直後で、
        子はまだレイアウトされていない（`pos` が箱の左下原点のまま）ことがある。
        `pos_hint` の分数ならレイアウト前でもゲームが入れた値そのものなので、
        そこから逆算する。
        """
        height = float(box.height)
        hints = [top_hint(label) for label in labels]
        if not height or any(hint is None for hint in hints):
            return None
        heights = [float(label.height) for label in labels]
        # 箱の上端から各ラベルの上端までの距離。
        tops = [(1.0 - hint) * height for hint in hints]
        gaps = [tops[0]]
        for index in range(1, len(labels)):
            gaps.append(tops[index] - (tops[index - 1] + heights[index - 1]))
        width = float(box.width)
        return {
            "width": width,
            "height": height,
            # 箱の幅と折り返し幅の差 ＝ そのラベルの左右の余白。
            # 幅を変えてもゲームが決めた余白（名前 17 / 説明
            # 33）をそのまま保つために使う。
            "margins": [width - float(text_width(label)) for label in labels],
            "heights": heights,
            "gaps": gaps,
            "bottom": height - (tops[-1] + heights[-1]),
            # こちらが最後に当てた寸法。
            # 写し直しのときに設計値と取り違えないため。
            "applied": None,
            "applied_width": None,
        }

    def design_of(box, labels):
        design = designs.get(box)
        if design is not None and box.width in (design["width"],
                                                design["applied_width"]):
            # 幅がこちらの当てた値のままなら、ゲームは組み直していない。
            return design
        fresh = capture(box, labels)
        if fresh is None:
            return design          # 設計が読めない ＝ 何もしない
        if design is not None:
            # 見覚えのない幅 ＝ ゲームが組み直した。
            # ただし高さはこちらも動かしているので、
            # 当てた覚えのある値は「設計」ではない。
            # 前のを引き継ぐ。
            applied = design.get("applied")
            if applied:
                fresh["heights"] = [old if now == was else now
                                    for now, was, old
                                    in zip(fresh["heights"], applied, design["heights"])]
            # 余白も測り直せない。
            # `margins` は「箱の幅 − 折り返し幅」で採るが、折り返し幅は
            # こちらが `resize()` で書いた値なので、箱だけが別の幅に
            # 組み直されると**負の余白**になり、本文が箱より広く折り返される。
            # 余白は幅ではなく設計の側の値なので、前のを引き継ぐ。
            if any(value < 0 for value in fresh["margins"]):
                write("margins looked wrong ({}); keeping the design's {}".format(
                    [round(v, 1) for v in fresh["margins"]],
                    [round(v, 1) for v in design["margins"]]))
                fresh["margins"] = list(design["margins"])
        designs[box] = fresh
        return fresh

    # -- 文字が要求する高さ ------------------------------------------------
    #: 測った結果の控え。
    #: `(本文, 幅, 書体の大きさ, 行間, 書体) -> 高さ`。
    #: 書体と行間まで鍵に入れるのは、下の複製がその2つを明示的に受け取るため
    #: （表示中のラベル側が変わっても控えが古いままにならない）。
    measured = {}

    def _probe_height(label, text, width):
        """**画面に出ていない複製**で折り返しの高さを測る。読めなければ None。

        `kivy.core.text.Label` は Kivy がラベルの中身を焼くのに使っている当のもので、
        ウィジェットではないので画面には出ない。
        表示中のラベルから書体・大きさ・行間・余白を写して同じ条件で焼く。

        表示中のラベルを測定器にしない。
        前の版は `label.text_size = (幅, None)` を書いてから
        `texture_update()` を呼んでいたが、これには2つ困りごとがあった:

        * 測るだけでテクスチャが焼き直される。
          幅の候補を試す `resize` のループは1回のホバーで
          ラベル3枚 × 最大6周ぶん呼ぶので、**表示中の箱を測定のために
          18回焼き直す**ことになる（`112_` / `117_` が同じ形で打ち出しを
          1.6 倍遅くしていた。VERIFICATION_LOG.md §2.34）
        * 測り終えた時点で `text_size` が `(幅, None)` のまま残る。
          確定値は呼び出し側が入れ直すが、その前に `capture()` が走ると
          **測定用の値を設計値として控える**

        複製で測れば表示中のラベルには指一本触れない。
        `118_batch_message_render` の `measured_height` と同じ手（あちらが先に
        確立した。同じ計測なので寄せてある）。
        """
        try:
            from kivy.core.text import Label as CoreLabel
        except Exception:
            return None       # 画面の無い環境（オフライン検証）
        options = {
            "text": text or "",
            "font_size": frames.attr(label, "font_size", 12),
            "text_size": (width, None),
            "line_height": frames.attr(label, "line_height", 1.0),
        }
        font_name = frames.attr(label, "font_name")
        if isinstance(font_name, str) and font_name:
            options["font_name"] = font_name
        padding = frames.attr(label, "padding")
        if isinstance(padding, (list, tuple)) and len(padding) == 4:
            options["padding"] = tuple(padding)
        try:
            probe = CoreLabel(**options)
            probe.refresh()
            return float(probe.texture.size[1])
        except Exception:
            return None

    def needed_height(label, width):
        """その幅で折り返したときに文字が要求する高さ。

        同じ本文を同じ幅で測り直さない。
        `update_content` はマウスが動くたびに走るので、
        控えが無いと1ホバーごとに測り直すことになる。
        """
        key = (frames.text_of(label), round(float(width), 1),
               frames.attr(label, "font_size", None),
               frames.attr(label, "line_height", None),
               frames.attr(label, "font_name", None))
        if key in measured:
            return measured[key]
        height = _probe_height(label, key[0], width)
        if height is None:
            return 0.0        # 測れない ＝ 設計値のまま（`measure` の `max`）
        if len(measured) > MEASURE_CACHE_MAX:
            measured.clear()
        measured[key] = height
        return height

    window_size = ui.window_size

    # -- 当てる --------------------------------------------------------------
    def measure(design, labels, box_width):
        """その幅にしたときの、各ラベルの高さと箱の高さ。"""
        widths = [box_width - margin for margin in design["margins"]]
        heights = [max(needed_height(label, width), floor)
                   for label, width, floor in zip(labels, widths, design["heights"])]
        # 文字以外（余白と隙間）の取り分は設計のまま。
        return widths, heights, sum(design["gaps"]) + design["bottom"] + sum(heights)

    def resize(box, labels):
        design = design_of(box, labels)
        if design is None:
            return None, None
        win_width, win_height = window_size()

        # 幅を広げる余地。
        # 左端はゲームが決めた位置（アイテムの右端）なので、
        # そこから窓の右端までが上限。
        # 窓が読めなければ設計の WIDEST 倍まで。
        limit = design["width"] * WIDEST
        if win_width:
            limit = min(limit, win_width - box.x)
        limit = max(limit, design["width"])

        # 設計の縦横比より縦長になるなら、その分だけ横にも広げる。
        # 比はゲームが決めた 500:333 ＝ 1.50 で、こちらが選んだ数字ではない。
        ratio = design["height"] / design["width"]
        box_width = design["width"]
        widths, heights, box_height = measure(design, labels, box_width)
        while box_height > ratio * box_width + RATIO_SLACK and box_width < limit:
            box_width = min(box_width * WIDEN_STEP, limit)
            widths, heights, box_height = measure(design, labels, box_width)

        # 窓に入り切らないときだけ、説明ラベルから削る（設計値までで止める）。
        if win_height and box_height > win_height:
            spare = heights[-1] - design["heights"][-1]
            cut = min(spare, box_height - win_height)
            if cut > 0:
                heights[-1] -= cut
                box_height -= cut

        # 上端と左端を保つ。
        # ゲームは箱の上端を所持品グリッドの上端に、
        # 左端を掴んだアイテムの右端に合わせている（どちらも実測で一致）ので、
        # 伸ばすのは右と下。
        top = box.y + box.height
        box.size_hint = (None, None)
        box.width = box_width
        box.height = box_height
        box.y = top - box_height

        # 位置は pos_hint の分数で。
        # 箱の高さが変わっても中身が自分で並び直すという、
        # ゲーム本来の仕掛けにそのまま乗る。
        gaps = design["gaps"]
        offset = gaps[0]
        for index, label in enumerate(labels):
            if index:
                offset += gaps[index]
            label.size_hint = (None, None)
            label.width = box_width          # ラベルは箱いっぱい（設計どおり）
            label.height = heights[index]
            label.text_size = (widths[index], heights[index])
            hint = frames.attr(label, "pos_hint")
            hint = dict(hint) if isinstance(hint, dict) else {"center_x": 0.5}
            hint["top"] = (box_height - offset) / box_height if box_height else 1.0
            label.pos_hint = hint      # dict を作り直さないと dispatch されない
            offset += heights[index]

        design["applied"] = list(heights)
        design["applied_width"] = box_width
        return heights, (box_width, box_height)

    def clamp(box):
        """伸びた箱を窓の内側へ。位置を入れるのはゲーム側なので次のフレームに。"""
        try:
            from kivy.core.window import Window
            top = box.y + box.height
            if top > Window.height:
                box.y = Window.height - box.height
            if box.y < 0:
                box.y = 0
            right = box.x + box.width
            if right > Window.width:
                box.x = Window.width - box.width
            if box.x < 0:
                box.x = 0
        except Exception:
            ctx.log_exc("item detail autosize: clamp failed")

    @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content")
    def update_content(orig, self, item, *args, **kwargs):
        result = orig(self, item, *args, **kwargs)
        try:
            labels = labels_of(self)
            if labels is None:
                return result
            heights, box_size = resize(self, labels)
            if heights is None:
                # 設計が読めない形の箱。
                # ゲームが作ったまま返す。
                if "design" not in logged:
                    logged.add("design")
                    ctx.log("ItemDetailBox has no pos_hint['top'] to read the layout "
                            "from; leaving the box alone", level="WARN")
                return result

            try:
                from kivy.clock import Clock
                Clock.schedule_once(lambda dt: clamp(self), 0)
            except Exception:
                ctx.log_exc("item detail autosize: could not schedule the clamp")

            key = (frames.attr(item, "item_id"),
                   tuple(len(frames.attr(lb, "text", "") or "") for lb in labels))
            if key not in logged and len(logged) < MAX_LOG:
                logged.add(key)
                design = designs[self]
                write("item={!r} box={:.0f}x{:.0f} (design {:.0f}x{:.0f})  {}".format(
                    frames.attr(item, "item_id"), box_size[0], box_size[1],
                    design["width"], design["height"],
                    "  ".join("{}={:.0f}/{:.0f} len={}".format(
                        name, height, floor, len(frames.attr(label, "text", "") or ""))
                        for name, label, height, floor
                        in zip(LABEL_NAMES, labels, heights, design["heights"]))))
        except Exception:
            # 説明欄のためにゲームを落とさない。
            # 切れたままでも遊べる。
            ctx.log_exc("item detail autosize failed; leaving the box as the game made it")
        return result

    ctx.log("ItemDetailBox will grow to fit its text; sizes go to out/{}".format(
        LOG_BASENAME))
