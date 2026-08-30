# -*- coding: utf-8 -*-
"""修正: クラフト画面で「作成」ボタンの枠と矢印が、グリッドと重なるのを直す。

動かすのはボタンと矢印だけ。
グリッドには触らない。

## 何が起きているか

クラフト画面は左から「所持品」「クラフト」、その右に生成先のグリッドが並び、
その間に矢印と「作成」ボタンが置かれている。
ボタンは枠線を持つので、
置き場所がグリッドと重なると枠が2本交差して見える（実機の画面で確認）。
矢印は枠を持たないが、生成先グリッドの裏に埋もれて読めなくなる（同じ画面。
VERIFICATION.md §3.22）。

ボタンの幅も、グリッドとグリッドの隙間も、その時の窓の大きさで決まる。
隙間よりボタンが広ければ重なるので、特定の解像度でだけ起きる。
だから「何px 動かす」という固定値は書けない。

## 直し方

寸法を発明しない（`108_` / `109_` と同じ立場）。
その場で測って、重なっているぶんだけ動かす。

1. ゲームが組んだ位置（設計値）を1回だけ控え、毎回そこから測り直す
2. 画面に出ているグリッドの矩形を全部集める
3. ボタンの高さの帯（上下に隙間ぶんの余裕を足したもの）に掛かるグリッドを並べ、
   横方向の空きを求める
4. **ゲームが置いた位置にいちばん近い空きから順に**見て、
   * 入るなら、その空きの中で**いちばん動かさずに済む位置**へ滑らせる
     （元の位置が既に空きの中なら、そこに留まる ＝ 何もしない）
   * 入らないなら、その空きの幅までボタンを詰めて入れる（`MIN_WIDTH_RATIO`）
5. どの空きにも入らなければ、グリッドの**下（入らなければ上）へ逃がす**

遠い空きより、近い空きに詰めて入るほうを選ぶ。
クラフト画面のボタンは「クラフト」と生成先のあいだに置かれていて、
その位置に意味がある（矢印が同じ隙間に居る）。
生成先の右にも窓の端まで空きはあるが、そこへ飛ばすと並びの意味が壊れる。
数十px 詰めるほうが、200px 動かすより見た目の変化が小さい。

この手順は「作成」ボタンと矢印の両方に同じものを使う。
ボタンを先に片付け、矢印はその結果も避ける相手に足して置く。

グリッドは1つも動かさない。
中のアイテムの当たり判定は矩形で決まるので、
グリッドを動かす直し方はアイテムの移動・装備を巻き込む（VERIFICATION_LOG.md
§2.33 と同じ性質の危険がある）。
動かしてよいのは、置き場所以外の役目を持たないボタンと矢印だけ。

## 「見えている枠」はウィジェットの矩形とは限らない

ボタンは枠線を自分で描くので、矩形がそのまま見た目になる。
ラベルは違う。
Kivy の `Label` は `text_size` を持たなければ、
文字のテクスチャをウィジェットの中心に描く。
矩形いっぱいには描かない。
矢印のラベルがグリッドと同じくらい大きい場合、
矩形で重なりを測ると「どこにも置けない」に化ける。

そこで、重なりを測る相手は次で分ける（型名ではなく押せるかどうかで見る）:

| 相手 | 見えている枠 |
|---|---|
| 押せるもの（`on_press` / `trigger_action` を持つ） | ウィジェットの矩形そのもの |
| それ以外で `texture_size` が矩形より小さいもの | 矩形の中心に置いた文字の箱 |

動かすのはどちらもウィジェットなので、文字も同じだけ動く。
幅を詰めてよいのは前者だけ（文字の箱で測っている相手の幅を詰めても、
文字は細くならない）。

## グリッドの見分け方は型名ではなく持ち物で

`InventoryGrid` は `scripts.hud.new_hud` に居るが、名前で引くと、
ゲームが更新でクラスを増やした・別名にした瞬間、外れる。
**アイテムを置く能力を持っているか**で見る（GAME.md §1.3）:

    place_new_item / try_place_item / occupy_slots / find_placement_position
    is_valid_placement / place_existing_item / get_unique_items

このうち `MIN_GRID_MARKS` 個以上を持つものだけをグリッドとみなす。

見えているものだけを数える。
クラフト画面が開いている間も、売買や強化のグリッドは同じ
HUD にぶら下がったまま（`opacity=0`）居る。
透明なものまで数えると、重なってもいない相手を避けてボタンが飛ぶ。
親をたどって1枚でも透明なら外す。

## 位置は `pos_hint` で入れる

ボタンは `FloatLayout` の子なので、
`pos_hint` を持っていれば**レイアウトが走るたびに位置を入れ直される**。
`x` にだけ書くと、アイテムを1つ置いた次の瞬間に元へ戻る。
`pos_hint` を持つ相手には分数のほうを直し、
`x` にも同じ差分を入れる（次のレイアウトまでの間もその場に居させるため）。

## 何度呼ばれても同じ結果になること

`apply()` も、この当て直しも、1回のプレイで何度も走る（TECH.md §3.4）。
控えは**ウィジェット自身に持たせて**、
毎回そこへ戻してから測り直す（`115_` / `109_` と同じ。MOD 側の変数に持つと、
注入し直したときに「こちらが当てた後の値」を設計値として控えてしまう）。

いま画面に在る値がこちらの当てた値と違っていたら、
ゲームが組み直したと判断して控えを取り直す。
こうしないと、窓の大きさを変えた後に古い位置へ戻してしまう。

## 触らないもの

グリッドの位置と大きさ、中のアイテム、クラフトの結果、セーブ。
剥がす（`--unload`）とフックは外れ、次に画面を開くとゲームの組み方に戻る。

矢印は既定で動かす（`FIX_ARROW`）。
ボタンを先に片付けてから、**ボタンの矩形も避ける相手に足して**矢印を置く。
`ItemCraftManager.arrow_label_animation` が矢印の位置を触っていれば戻されうる（未確認）。
戻るなら `FIX_ARROW` を切って、枠の交差だけを直せばよい。

測った矩形と、何をしたかは `out/craft_window.log` に出る。
重なっていなければ `clear` と出て何も動かない。
"""

from instantale_modloader import frames, ui

LOG_BASENAME = "craft_window.log"

# 枠と枠の間に残す隙間（px）。
# 0 にすると枠線どうしが接する。
MARGIN = 6.0

# ボタンを詰めてよい下限（設計値の幅に対する割合）。
# 1.0 で幅を変えない。
MIN_WIDTH_RATIO = 0.6

# 詰めるときでも下回らない幅（px）。
# 文字が読めなくなるのを防ぐ。
MIN_WIDTH = 48.0

# 矢印も動かすか。
# 既定で動かす（実機では生成先グリッドの裏に埋もれていた。VERIFICATION.md §3.22）。
# OFF にすると矢印は動かさず、「ボタンが避ける相手」としてだけ数える。
# `ItemCraftManager.arrow_label_animation` が位置を触っていれば戻されうる（未確認。
# 戻るなら OFF にして枠だけ直す）。
FIX_ARROW = True

# 当て直す間隔（秒）。
# 0 は次のフレーム。
# レイアウトが1フレームで終わらないビルドのために2回目以降を置く。
# `fit` は何度呼んでも同じ結果になる。
PASS_DELAYS = (0.0, 0.05, 0.25, 0.6)

# 画面は開くたびに来るので、書くのは形が変わったときだけ。

# ウィジェット木を何段まで降りてグリッドを探すか。
MAX_DEPTH = 14

# 「透明な親の中に居ないか」を何段上まで見るか。
MAX_UP = 14

# アイテムを置く能力。
# この数だけ揃っていればグリッドとみなす。
GRID_MARKS = ("place_new_item", "try_place_item", "occupy_slots",
              "find_placement_position", "is_valid_placement",
              "place_existing_item", "get_unique_items")
MIN_GRID_MARKS = 3

# 控え（ゲームが組んだ寸法）。
# ウィジェット自身に持たせる。
BOX_ATTR = "_instantale_craftfit_box"

# 座標の同一視。
# 1px 未満のずれはレイアウトの丸め。
EPS = 0.5

# `pos_hint` のうち横を決める鍵と縦を決める鍵。
X_KEYS = ("x", "right", "center_x")
Y_KEYS = ("y", "top", "center_y")


def apply(ctx):
    state = {"sig": None}

    note = ctx.logger(LOG_BASENAME)

    # ------------------------------------------------------------ 読み取り
    def num(value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def attr_num(widget, name, default=None):
        return num(frames.attr(widget, name), default)

    def parent_of(widget):
        parent = frames.attr(widget, "parent")
        return None if parent in (None, frames.MISSING) else parent

    def children_of(widget):
        children = frames.attr(widget, "children")
        return list(children) if isinstance(children, (list, tuple)) else []

    def geom(widget):
        """局所座標（`x`/`y`）と窓座標（`wx`/`wy`）の両方。読めなければ None。

        重なりの計算は窓座標で行い、書き戻すときは差分（dx/dy）を局所座標へ足す。
        こうしておくと `RelativeLayout` が挟まっていても座標系を取り違えない。
        """
        x, y = attr_num(widget, "x"), attr_num(widget, "y")
        width, height = attr_num(widget, "width"), attr_num(widget, "height")
        if None in (x, y, width, height):
            return None
        wx, wy = x, y
        to_window = frames.attr(widget, "to_window")
        if callable(to_window):
            try:
                px, py = to_window(x, y)
                wx, wy = num(px, x), num(py, y)
            except Exception:
                wx, wy = x, y
        return {"x": x, "y": y, "w": width, "h": height, "wx": wx, "wy": wy}

    def rect_of(box):
        """窓座標の矩形 (左, 下, 右, 上)。"""
        return (box["wx"], box["wy"], box["wx"] + box["w"], box["wy"] + box["h"])

    def looks_like_a_button(widget):
        """押せるか。押せるものは自分で枠を描くので矩形が見た目そのもの。"""
        for name in ("on_press", "trigger_action"):
            if frames.attr(widget, name) is not frames.MISSING:
                return True
        return False

    def visual_rect(widget, rect):
        """見えている枠。ボタンは矩形そのもの、ラベルは文字の箱。

        Kivy の `Label` は、
        `text_size` を持たなければ文字のテクスチャをウィジェットの中心に描く（矩形いっぱいには描かない）。
        矢印のラベルはグリッドと同じくらい大きいことがあり、
        矩形で重なりを測ると「どこにも置けない」に化ける。
        文字の箱で測れば、動かす量は文字のぶんだけで済む（動かすのはウィジェットなので、
        文字も同じだけ動く）。
        """
        if rect is None or looks_like_a_button(widget):
            return rect
        size = frames.attr(widget, "texture_size")
        if not isinstance(size, (list, tuple)) or len(size) < 2:
            return rect
        width, height = num(size[0]), num(size[1])
        if (not width or not height
                or width > rect[2] - rect[0] or height > rect[3] - rect[1]):
            return rect
        center_x = (rect[0] + rect[2]) / 2.0
        center_y = (rect[1] + rect[3]) / 2.0
        return (center_x - width / 2.0, center_y - height / 2.0,
                center_x + width / 2.0, center_y + height / 2.0)

    def design_rect(widget):
        """控えてある設計値の矩形（窓座標）。控えが無ければ None。

        記録には「いまの位置」ではなくこちらを出す。
        当て直しは数フレームにわたって走るので、
        いまの位置を出すと**当てた後の姿を「当てる前」として書いた行**が混ざる。
        """
        kept = frames.attr(widget, BOX_ATTR)
        box = geom(widget)
        if not isinstance(kept, dict) or box is None:
            return None
        design = kept.get("design")
        if not isinstance(design, dict):
            return None
        offset_x, offset_y = box["wx"] - box["x"], box["wy"] - box["y"]
        return (design["x"] + offset_x, design["y"] + offset_y,
                design["x"] + offset_x + design["w"],
                design["y"] + offset_y + design["h"])

    def inflate(rect, pad):
        return (rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)

    def hit(one, other):
        return (one[0] < other[2] and other[0] < one[2]
                and one[1] < other[3] and other[1] < one[3])

    def show(rect):
        return "({:.0f},{:.0f} {:.0f}x{:.0f})".format(
            rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])

    # -------------------------------------------------------- 相手を見つける
    def visible(widget):
        """親をたどって、1枚でも透明なら見えていない。"""
        node = widget
        for _step in range(MAX_UP):
            if node is None:
                return True
            opacity = attr_num(node, "opacity", 1.0)
            if opacity is not None and opacity <= 0.0:
                return False
            node = parent_of(node)
        return True

    def is_grid(widget):
        """アイテムを置く能力を持っているか（＝グリッド）。型名では見ない。"""
        marks = 0
        for name in GRID_MARKS:
            if frames.attr(widget, name) is not frames.MISSING:
                marks += 1
        return marks >= MIN_GRID_MARKS

    def is_ancestor(node, widget):
        """`node` が `widget` の先祖か。先祖の矩形は避ける相手にしない。"""
        walker = parent_of(widget)
        for _step in range(MAX_UP):
            if walker is None:
                return False
            if walker is node:
                return True
            walker = parent_of(walker)
        return False

    def walk(widget, depth, seen, out):
        if id(widget) in seen:
            return
        seen.add(id(widget))
        out.append(widget)
        if depth >= MAX_DEPTH:
            return
        for child in children_of(widget):
            walk(child, depth + 1, seen, out)

    def visible_grids(hud, avoid_for):
        found = []
        try:
            walk(hud, 0, set(), found)
        except Exception:
            ctx.log_exc("craft window: walking the HUD failed")
            return []
        grids = []
        for widget in found:
            if not is_grid(widget) or parent_of(widget) is None:
                continue
            if is_ancestor(widget, avoid_for):
                continue
            if not visible(widget):
                continue
            box = geom(widget)
            if box is None or box["w"] <= 0.0 or box["h"] <= 0.0:
                continue
            grids.append(rect_of(box))
        return grids

    # ------------------------------------------------------------ 控えと復帰
    def hint_of(widget):
        hint = frames.attr(widget, "pos_hint")
        return dict(hint) if isinstance(hint, dict) else None

    def same(now, before):
        if not isinstance(before, dict):
            return False
        for key in ("x", "y", "w", "h"):
            value = before.get(key)
            if value is None or abs(now[key] - value) > EPS:
                return False
        return True

    def redesign(widget, previous):
        """組み直された後の設計値を、控えてある分数から引き直す。

        いま見えている寸法を設計値として控え直してはいけない。
        それはこちらが当てた後の姿なので、詰めた幅が次の設計値になり、
        窓の大きさを変えるたびボタンが細くなっていく。
        ゲームが持っていた `pos_hint` / `size_hint_x` は最初に控えてあるので、
        いまの親の大きさに当て直せば正しい設計値が出る。
        """
        design = dict(previous["design"])
        parent = parent_of(widget)
        parent_w = attr_num(parent, "width")
        parent_h = attr_num(parent, "height")
        parent_x = attr_num(parent, "x", 0.0) or 0.0
        parent_y = attr_num(parent, "y", 0.0) or 0.0
        hint_x = num(previous.get("size_hint_x"))
        if hint_x is not None and parent_w:
            design["w"] = parent_w * hint_x
        hint = previous.get("pos_hint")
        if not hint or not parent_w or not parent_h:
            # 分数を持たない相手は、ゲームが直に置いている。
            # 位置だけはいまの値を採る（幅は上で控えたぶんを保つ ＝ 細くならない）。
            box = geom(widget)
            if box is not None:
                design["x"], design["y"] = box["x"], box["y"]
            return design
        for key, place in (("x", lambda v: parent_x + v * parent_w),
                           ("right", lambda v: parent_x + v * parent_w - design["w"]),
                           ("center_x",
                            lambda v: parent_x + v * parent_w - design["w"] / 2.0)):
            if key in hint:
                design["x"] = place(num(hint[key], 0.0))
        for key, place in (("y", lambda v: parent_y + v * parent_h),
                           ("top", lambda v: parent_y + v * parent_h - design["h"]),
                           ("center_y",
                            lambda v: parent_y + v * parent_h - design["h"] / 2.0)):
            if key in hint:
                design["y"] = place(num(hint[key], 0.0))
        return design

    def design_of(widget):
        """ゲームが組んだ寸法。ウィジェット自身に控える。

        いまの寸法がこちらの当てた値と違えば、
        ゲームが組み直した（窓の大きさが変わった・画面を開き直した）とみなす。
        そのときも分数の控えは捨てない（`redesign`）。
        """
        box = geom(widget)
        if box is None:
            return None, None
        kept = frames.attr(widget, BOX_ATTR)
        if isinstance(kept, dict) and same(box, kept.get("applied")):
            return box, kept
        if isinstance(kept, dict) and isinstance(kept.get("design"), dict):
            kept = dict(kept)
            kept["design"] = redesign(widget, kept)
            kept["applied"] = dict(kept["design"])
        else:
            plain = dict((key, box[key]) for key in ("x", "y", "w", "h"))
            kept = {"design": plain, "applied": dict(plain),
                    "pos_hint": hint_of(widget),
                    "size_hint_x": frames.attr(widget, "size_hint_x")}
        try:
            setattr(widget, BOX_ATTR, kept)
        except Exception:
            return box, None
        return box, kept

    def restore(widget, kept):
        """設計値へ戻す。測り直しは必ずここを通ってから行う。"""
        design = kept["design"]
        try:
            if kept["pos_hint"] is not None:
                widget.pos_hint = dict(kept["pos_hint"])
            if kept["size_hint_x"] is not frames.MISSING:
                widget.size_hint_x = kept["size_hint_x"]
            widget.width = design["w"]
            widget.height = design["h"]
            widget.x = design["x"]
            widget.y = design["y"]
        except Exception:
            ctx.log_exc("craft window: could not restore the design geometry")

    def remember(widget, kept):
        """当てた後の寸法を控える（次の回に設計値と取り違えないため）。"""
        box = geom(widget)
        if box is not None:
            kept["applied"] = dict((key, box[key]) for key in ("x", "y", "w", "h"))
        return box

    # ------------------------------------------------------------ 書き込み
    def shift(widget, dx, dy):
        """`pos_hint` があればそちらを直す（レイアウトで戻されないように）。"""
        hint = hint_of(widget)
        parent = parent_of(widget)
        parent_w = attr_num(parent, "width", 0.0) or 0.0
        parent_h = attr_num(parent, "height", 0.0) or 0.0
        touched = []
        if hint:
            if dx and parent_w and any(key in hint for key in X_KEYS):
                for key in X_KEYS:
                    if key in hint:
                        hint[key] = num(hint[key], 0.0) + dx / parent_w
                touched.append("pos_hint.x")
            if dy and parent_h and any(key in hint for key in Y_KEYS):
                for key in Y_KEYS:
                    if key in hint:
                        hint[key] = num(hint[key], 0.0) + dy / parent_h
                touched.append("pos_hint.y")
            if touched:
                widget.pos_hint = hint
        # 分数を直しても、次にレイアウトが走るまで位置は古いままなので、
        # 実座標にも同じ差分を入れる（分数を持たない相手はこちらだけが効く）。
        widget.x = num(widget.x, 0.0) + dx
        widget.y = num(widget.y, 0.0) + dy
        return "+".join(touched) if touched else "pos"

    def narrow(widget, new_width, parent_w):
        """幅を詰める。`size_hint_x` を持つ相手は分数のほうを直す。"""
        hint_x = frames.attr(widget, "size_hint_x")
        if hint_x is None:
            widget.width = new_width
            return "width"
        if num(hint_x) is not None and parent_w:
            widget.size_hint_x = new_width / parent_w
            widget.width = new_width
            return "size_hint_x"
        return None

    def anchored_left(widget, rect, new_width):
        """幅を詰めたとき、レイアウトが置き直す左端。

        `pos_hint` の `center_x` は中心を、`right` は右端を保つので、
        詰めたぶんだけ左端が動く。
        差分はここから求めないと1回ぶんずれる。
        """
        hint = hint_of(widget) or {}
        if "center_x" in hint:
            return (rect[0] + rect[2]) / 2.0 - new_width / 2.0
        if "right" in hint and "x" not in hint:
            return rect[2] - new_width
        return rect[0]

    # ------------------------------------------------------------ 置き場所
    def free_spans(field, blocks):
        """`field` から `blocks`（横方向の区間）を引いた空き。"""
        spans = sorted((max(field[0], left), min(field[1], right))
                       for left, right in blocks
                       if right > field[0] and left < field[1])
        free, cursor = [], field[0]
        for left, right in spans:
            if left > cursor:
                free.append((cursor, left))
            cursor = max(cursor, right)
        if cursor < field[1]:
            free.append((cursor, field[1]))
        return free

    def field_of(blocks, rect):
        """横に動かしてよい範囲。窓が読めればその内側、読めなければ枠の広がり。"""
        width, _height = ui.window_size()
        if width:
            return (0.0, width)
        edges = [rect[0], rect[2]]
        for block in blocks:
            edges.extend((block[0], block[2]))
        span = rect[2] - rect[0]
        return (min(edges) - span, max(edges) + span)

    def distance_to(span, center):
        """空きと、ゲームが置いた中心との隔たり。中に居れば 0。"""
        if span[0] <= center <= span[1]:
            return 0.0
        return min(abs(span[0] - center), abs(span[1] - center))

    def landing_in(span, center, width):
        """その空きの中で、いちばん動かさずに済む左端。"""
        return min(max(center, span[0] + width / 2.0),
                   span[1] - width / 2.0) - width / 2.0

    def escape(rect, blocks):
        """横に逃げ場が無いときの上下。返すのは新しい下端。無ければ None。"""
        blockers = [block for block in blocks
                    if block[0] < rect[2] and rect[0] < block[2]]
        if not blockers:
            return None
        height = rect[3] - rect[1]
        _width, window_h = ui.window_size()
        below = min(block[1] for block in blockers) - height
        above = max(block[3] for block in blockers)
        if below >= 0.0:
            return below
        if not window_h or above + height <= window_h:
            return above
        return None

    # ------------------------------------------------------------ 本体
    def resolve(widget, blocks, allow_shrink):
        """1つのウィジェットを、どの枠とも重ならない場所へ置く。

        返すのは何をしたか（記録用の文字列）。
        """
        box, kept = design_of(widget)
        if box is None or kept is None:
            return "unreadable"
        restore(widget, kept)
        box = geom(widget)
        if box is None:
            return "unreadable"

        rect = visual_rect(widget, rect_of(box))
        padded = [inflate(block, MARGIN) for block in blocks]
        if not [block for block in padded if hit(rect, block)]:
            remember(widget, kept)
            return "clear"

        band = [block for block in padded
                if block[1] < rect[3] and rect[1] < block[3]]
        field = field_of(padded, rect)
        free = free_spans(field, [(block[0], block[2]) for block in band])
        width = rect[2] - rect[0]
        center = (rect[0] + rect[2]) / 2.0
        limit = max(MIN_WIDTH, width * MIN_WIDTH_RATIO)
        # 詰めてよいのは、見えている枠がウィジェットの矩形そのものである相手だけ（文字の箱で測っている相手の幅を詰めても、
        # 文字は細くならない）。
        shrinkable = (allow_shrink and MIN_WIDTH_RATIO < 1.0
                      and abs(width - box["w"]) <= EPS)
        parent_w = attr_num(parent_of(widget), "width", 0.0) or 0.0

        # 近い空きから順に。
        # 遠くへ飛ばすより、近くで詰めるほうを選ぶ。
        for span in sorted(free, key=lambda item: distance_to(item, center)):
            if span[1] - span[0] + EPS >= width:
                target = landing_in(span, center, width)
                how = shift(widget, target - rect[0], 0.0)
                remember(widget, kept)
                return "slid dx={:+.0f} via {}".format(target - rect[0], how)
            if not shrinkable or span[1] - span[0] < limit:
                continue
            new_width = span[1] - span[0]
            how = narrow(widget, new_width, parent_w)
            if how is None:
                continue                # 幅を持てない相手。次の空きへ
            left = anchored_left(widget, rect, new_width)
            moved = shift(widget, span[0] - left, 0.0)
            remember(widget, kept)
            return "narrowed {:.0f}->{:.0f} via {} dx={:+.0f} via {}".format(
                width, new_width, how, span[0] - left, moved)

        bottom = escape(rect, padded)
        if bottom is not None:
            how = shift(widget, 0.0, bottom - rect[1])
            remember(widget, kept)
            return "moved dy={:+.0f} via {}".format(bottom - rect[1], how)

        restore(widget, kept)
        remember(widget, kept)
        return "no room"

    def fit(hud):
        if hud is None:
            return
        button = frames.attr(hud, "craft_inventory_generate_button")
        arrow = frames.attr(hud, "craft_inventory_generate_arrow_label")
        if button in (None, frames.MISSING):
            note("craft: this build has no craft_inventory_generate_button")
            return
        if parent_of(button) is None or not visible(button):
            return                    # 画面が閉じている
        grids = visible_grids(hud, button)
        if not grids:
            note("craft: no visible grid found; nothing to avoid")
            return

        has_arrow = arrow not in (None, frames.MISSING) and visible(arrow)
        blocks = list(grids)
        if not FIX_ARROW and has_arrow:
            arrow_box = geom(arrow)
            if arrow_box is not None and arrow_box["w"] > 0.0:
                blocks.append(visual_rect(arrow, rect_of(arrow_box)))

        outcome = resolve(button, blocks, True)
        before = design_rect(button)
        after = geom(button)

        arrow_outcome = ""
        if FIX_ARROW and has_arrow:
            taken = list(grids)
            if after is not None:
                # ボタンは枠を持つので、矢印はその矩形を避ける。
                taken.append(rect_of(after))
            result = resolve(arrow, taken, False)
            arrow_after = geom(arrow)
            arrow_design = visual_rect(arrow, design_rect(arrow))
            arrow_now = (visual_rect(arrow, rect_of(arrow_after))
                         if arrow_after else None)
            arrow_outcome = " | arrow {} -> {} {}".format(
                show(arrow_design) if arrow_design else "?",
                show(arrow_now) if arrow_now else "?", result)

        signature = (
            tuple(round(value, 1) for value in before) if before else None,
            tuple(tuple(round(value, 1) for value in block) for block in blocks),
            outcome, arrow_outcome)
        if signature == state["sig"]:
            return                    # 同じ画面。開くたびに書かない
        state["sig"] = signature
        note("craft: button {} -> {} {}{} | grids {}".format(
            show(before) if before else "?",
            show(rect_of(after)) if after else "?",
            outcome, arrow_outcome,
            " ".join(show(block) for block in blocks)))

    def schedule(hud):
        """次のフレームから数回当て直す。`fit` は何度呼んでも同じ結果になる。"""
        try:
            from kivy.clock import Clock
        except Exception:
            fit(hud)                  # ゲームの外（オフライン検証）ではその場で
            return
        for delay in PASS_DELAYS:
            try:
                Clock.schedule_once(lambda _dt, target=hud: fit(target), delay)
            except Exception:
                ctx.log_exc("craft window: could not schedule the fit")
                return

    # ------------------------------------------------------------ フック
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.toggle_craft_inventory_visibility",
              safe=True)
    def toggle_visibility(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        schedule(self)
        return result

    @ctx.wrap("__main__:InstantaleApp.toggle_craft_inventory_window",
              safe=True, required=False)
    def toggle_window(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        schedule(ui.find_hud(self))
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.place_crafted_item",
              safe=True, required=False)
    def place_crafted_item(orig, self, generated_item=None, generated_item_id=None,
                           *args, **kwargs):
        # 生成でグリッドの中身が変わる ＝ レイアウトが走り直す。
        # 当て直す。
        result = orig(self, generated_item, generated_item_id, *args, **kwargs)
        schedule(self)
        return result

    ctx.log("craft window: installed (MARGIN={} MIN_WIDTH_RATIO={} FIX_ARROW={})"
            .format(MARGIN, MIN_WIDTH_RATIO, FIX_ARROW))
