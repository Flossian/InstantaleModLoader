# -*- coding: utf-8 -*-
"""修正: 入力欄の上に開くアイテム一覧が、件数が多いと画面の上へはみ出すのを直す。

位置も行の大きさも変えず、列を増やして収める。

## 何が起きているか（寸法と根拠は GAME.md §2.14.1）

自由入力の欄の左下のアイコンを押すと、持ち物の一覧が入力欄の上へ縦に伸びる。
一覧は入力欄を下端として上へ積まれるので、アイテムが増えるほど上へ伸び、
画面の上端を突き抜けて**先頭の数件が画面の外に出る**。
外に出た行は押せないので、そのアイテムは自由入力に差し込めない。

何件で溢れるかは画面の解像度で変わる（行の高さは変わらず、窓の高さだけが変わる）。
固定の上限件数を決めても解像度が変われば意味が無い。

## 一覧の正体

一覧は `scripts.hud.new_hud:ToolListPopup` で、
リコンの `modules.json` によれば **`GridLayout` の派生**。

* だから列を持てる。
  `cols` を 2 にすれば、位置も行の大きさもそのままで高さが半分になる。
  入れ物を作り替えたり、ウィジェットを移し替えたりする必要はどこにも無い
* 幅にも余裕がある（一覧の幅 926 に対し、行の幅は 175 前後）

そこでこの MOD がするのは1つ。
`cols` を、窓に収まる列数へ変える。
位置（`pos`）には触らない。
行の高さも文字の大きさも変えない。
行は1件も隠さない。

### ただし、格子が並べてくれないことがある

`cols=2` を入れても見た目の変わらないビルドがある。
`size_hint=[1, 1]` なので一覧の寸法は親（入力欄の帯）と同じ 926x78.75 で、
中身が要求する高さ（`minimum_height=1026`）と噛み合っていない。
それでも行は 0〜1026 に並んでいる＝ **行の位置をこの格子が決めていない**。

そこで手順を3つにする:

1. `cols` を入れ、`rows`（個数のプロパティ）が入っていたら外す（`cols x rows` の枠が足りないと
   Kivy はレイアウト自体を行わない）
2. **入れ物の高さを中身ぶんにする**。
   格子が並べ直すなら、
   78.75 の箱に押し込まれるのを防ぐため先に入れておく（下端は動かさないので伸びるのは上へ）
3. `VERIFY_DELAY` 秒後に本当に折り返ったかを見る。
   行の左端が1種類のままなら格子は並べていないので、
   行の位置をこちらで入れる（下端から上へ積み、1列埋まったら右の列の下端から続ける）

3 の判定に使うのは「行が実際に何列に並んでいるか」だけで、
ビルドの作りを決めつけない。
結果は `out/item_list.log` の `after:` の行に残る。

## 何もしてはいけない相手

「縦に積まれた文字のあるウィジェット」を一覧とみなすと、**アイテムの説明の吹き出し（`ItemDetailBox`）まで掴んで**画面が崩れる（VERIFICATION_LOG.md
§2.28）。
見分け方は 3つで、どれも型名ではなく持ち物と置かれ方で見る（GAME.md §1.3）:

| 条件 | 落ちる相手 |
|---|---|
| 列を持てること（`cols` と `minimum_height` がある ＝ `GridLayout` の能力） | `ItemDetailBox` は `FloatLayout` 派生なので落ちる |
| ボタンの中に居ないこと | 吹き出しはアイテムのボタンにぶら下がっている（`parent=Button`） |
| 行の大半が空でない文字を持つこと | 吹き出しの中身は `['', '', 'item_detail:']` |

「列を持てること」を条件にしているのは、
名前で弾くためではなく、**この直し方が成り立つ相手かどうか**そのものだから。
列を持てない入れ物は、そもそも列にできない。

## 列数の決め方

    1列に入る行数 = (使ってよい高さ - 余白) / (行の高さ + 行間)
    列数         = ceil(行数 / 1列に入る行数)

「使ってよい高さ」は窓の高さの
`MAX_FILL` と、**一覧の下端から窓の上端まで**の小さい方。
下端は、こちらが触る前の値を1回だけ控えて使い続ける。
高さを変えると下端の動くビルドもある。
そのたびに測り直すと、列数が行ったり来たりする。

列数には上限を置く（`MAX_COLUMNS`）。
窓の幅から入る列数でも頭打ちにする。
`FIXED_COLUMNS` に 1 以上を入れると、その列数に固定する（「常に2列」）。

## 高さと幅

列を増やすと必要な高さは減る。
減った分は入れ物の側が持っている数を優先して入れる。
`GridLayout` は中身から `minimum_height` を出すので、
それが使えるならこちらの計算より正しい。
まだ組み直されていない（前の列数のままの）値を掴まないよう、
こちらの計算とかけ離れた値は使わない。

幅は、列が入れ物からはみ出すときだけ広げる（左端は動かさないので右へ伸びる）。

## 元に戻せること

控えるのは `cols` と `size_hint` / `size`。
**入れ物のウィジェット自身に持たせる**（MOD 側の変数に持つと、
注入し直したときに変えた後の値を設計値として控えてしまう。
`112_` / `113_` と同じ罠）。
アイコンをもう一度押す＝一覧が作り直されるので、そのタイミングで控えを捨てて、
ゲームが組んだ寸法から測り直す。

## 触らないもの

一覧の位置、行の高さ・文字の大きさ、中身（どのアイテムが並ぶか）、押したときの動き、
入力欄の文字、セーブ。
剥がす（`--unload`）とフックは外れ、
そのとき開いていた一覧は次の開閉でゲームの組み方に戻る。
"""

import math

from instantale_modloader import frames

LOG_BASENAME = "item_list.log"

# 一覧が窓の高さに占めてよい割合。
# 窓の縁まで詰めない。
MAX_FILL = 0.9

# 列数の上限。
# これ以上には増やさない（横に長い一覧は読みにくい）。
MAX_COLUMNS = 4

# 列数を固定する。
# 0 なら件数と窓の高さから自動で決める。
FIXED_COLUMNS = 0

# 窓の縁に残す余白（px）。
MARGIN = 4.0

# 一覧とみなす最小の行数。
MIN_ROWS = 3

# 子のうち行とみなせるものがこの割合を下回るなら、
# それは一覧ではない（背景や枠線が1枚混じる程度は許す）。
ROW_RATIO = 0.6

# 行の大半が文字を持っていること。
# 空の行ばかりの入れ物は一覧ではない（アイテム説明の吹き出しがこれで落ちる）。
TEXT_RATIO = 0.6

# 行の幅が「揃っている」とみなす許容（いちばん広い行に対する割合）。
WIDTH_SLACK = 0.35

# ウィジェット木を何段まで降りて一覧を探すか。
MAX_DEPTH = 12

# 「ボタンの中に居るか」を何段上まで見るか。
MAX_UP = 6

# 当て直す間隔（秒）。
# 0 は次のフレーム。
# レイアウトが1フレームで終わらないビルドのために2回目・3回目を置く。
# `fit` は何度呼んでも同じ結果になる。
PASS_DELAYS = (0.0, 0.05, 0.25)

# 列にしたあと、本当に折り返ったかを見に行くまでの秒数。
# 最後の当て直しより後。
VERIFY_DELAY = 0.4

# 記録の上限。
# 一覧は開くたびに来るので、書くのは形が変わったときだけ。
# ここに当たると**見届けの行（`after:`）が落ちて**何が起きたか読めなくなるので、
# 同じ形の一覧は1回しか書かないようにしたうえで、上限にも余裕を持たせている。
MAX_LOG = 200

# 控え（触る前の寸法）。
# ウィジェット自身に持たせる。
BOX_ATTR = "_instantale_itemfit_box"


def apply(ctx):
    state = {"logged": 0, "shape": None, "boxes": []}
    warned = set()

    write = ctx.logger(LOG_BASENAME)

    def note(text):
        if state["logged"] < MAX_LOG:
            state["logged"] += 1
            write(text)

    # -- 寸法の読み書き ------------------------------------------------------
    def number(value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def size_of(widget, name, default=None):
        return number(frames.attr(widget, name), default)

    def rect_of(widget):
        x, y = size_of(widget, "x"), size_of(widget, "y")
        width, height = size_of(widget, "width"), size_of(widget, "height")
        if None in (x, y, width, height):
            return None
        return (x, y, width, height)

    def window_size():
        try:
            from kivy.core.window import Window
            return number(Window.width, 0.0), number(Window.height, 0.0)
        except Exception:
            return 0.0, 0.0

    def schedule(fn, delay=0.0):
        try:
            from kivy.clock import Clock
        except Exception:
            fn()              # ゲームの外（オフライン検証）ではその場で
            return
        try:
            Clock.schedule_once(lambda _dt: fn(), delay)
        except Exception:
            ctx.log_exc("item list: could not schedule the fit")

    def children_of(widget):
        children = frames.attr(widget, "children")
        return list(children) if isinstance(children, (list, tuple)) else []

    def parent_of(widget):
        parent = frames.attr(widget, "parent")
        return None if parent in (None, frames.MISSING) else parent

    def alive(widget):
        return widget is not None and parent_of(widget) is not None

    def pair(value, default=0.0):
        """`spacing` / `padding` を (横, 縦) にする。数1つでも列でも受ける。"""
        if isinstance(value, (list, tuple)):
            values = [number(item, default) for item in value]
            if len(values) >= 4:
                return (values[0] + values[2], values[1] + values[3])
            if len(values) == 2:
                return (values[0], values[1])
            if len(values) == 1:
                return (values[0], values[0])
            return (default, default)
        single = number(value, None)
        if single is None:
            return (default, default)
        return (single, single)

    # -- 一覧を見分ける ------------------------------------------------------
    def has_columns(widget):
        """列を持てる入れ物か（`GridLayout` の能力）。

        **この直し方が成り立つ相手かどうか**そのもの。
        列を持てない入れ物は列にできないので、
        掴んでも何もできない（この条件が無いと
        `ItemDetailBox` を掴んで画面が崩れる。VERIFICATION_LOG.md §2.28）。
        """
        for name in ("cols", "minimum_height"):
            if frames.attr(widget, name) is frames.MISSING:
                return False
        return True

    def inside_button(widget):
        """ボタンにぶら下がっているか（＝一覧ではなく、その説明の吹き出し）。"""
        node = parent_of(widget)
        for _step in range(MAX_UP):
            if node is None:
                return False
            for name in ("on_press", "trigger_action"):
                if frames.attr(node, name) is not frames.MISSING:
                    return True
            node = parent_of(node)
        return False

    def is_row(widget):
        """一覧の1行か。型では見ない（GAME.md §1.3）。

        文字列は `frames.text_of` で受ける。
        `frames.attr` の既定は文字列の番人（`"<missing>"`）なので、
        `isinstance(str)` で受けると **`text` を持たない飾りのウィジェット（背景・枠線・画像）まで「行」に数えてしまう**。
        列数の計算が狂うだけでなく、飾りが本物の行と同じ高さに居ると `rows_of` の「横並びだから一覧ではない」判定に掛かって、
        一覧が丸ごと棄却される（TECH.md §5.2 の罠。`118_` が先に踏んでいる）。
        """
        text = frames.text_of(widget)
        if text is None:
            return False
        height = size_of(widget, "height")
        return height is not None and height > 0

    def rows_of(widget):
        """縦に積まれた行。一覧でなければ空を返す。"""
        children = children_of(widget)
        if len(children) < MIN_ROWS:
            return []
        rows = [child for child in children if is_row(child)]
        if len(rows) < MIN_ROWS or len(rows) < len(children) * ROW_RATIO:
            return []
        filled = [row for row in rows if frames.attr(row, "text", "").strip()]
        if len(filled) < len(rows) * TEXT_RATIO:
            return []             # 中身が空ばかり ＝ 一覧ではない
        levels = set()
        widths = []
        for row in rows:
            y = size_of(row, "y")
            width = size_of(row, "width")
            if y is None or width is None:
                return []
            levels.add(round(y, 1))
            widths.append(width)
        if len(levels) < len(rows):
            return []             # 同じ高さに並んでいる ＝ 横並び（一覧ではない）
        widest = max(widths)
        if widest <= 0:
            return []
        for width in widths:
            if abs(width - widest) > widest * WIDTH_SLACK:
                return []         # 幅がばらばら ＝ 積まれた一覧ではない
        rows.sort(key=lambda r: size_of(r, "y", 0.0))
        return rows

    def bounds(rows):
        """行が実際に占めている上下（下端, 上端）。"""
        bottom = min(size_of(row, "y", 0.0) for row in rows)
        top = max(size_of(row, "y", 0.0) + size_of(row, "height", 0.0) for row in rows)
        return bottom, top

    def walk(widget, depth, seen, out):
        if id(widget) in seen:
            return
        seen.add(id(widget))
        out.append(widget)
        if depth >= MAX_DEPTH:
            return
        for child in children_of(widget):
            walk(child, depth + 1, seen, out)

    def tree(hud):
        """一覧が出てきうる場所すべて（HUD の下と、窓に直付けされたもの）。"""
        found, seen = [], set()
        try:
            walk(hud, 0, seen, found)
        except Exception:
            ctx.log_exc("item list: walking the HUD failed")
        try:
            from kivy.core.window import Window
            for child in children_of(Window):
                walk(child, 1, seen, found)
        except Exception:
            pass              # 窓を持たない環境（オフライン検証）ではここは無い
        return found

    def ids_of(hud):
        return set(id(widget) for widget in tree(hud))

    def fresh(widget, rows, before):
        """このアイコン押下で作り直されたか。

        入れ物を使い回して中身だけ入れ替えるビルドもあるので、
        行のどれかが新しければ作り直されたとみなす。
        """
        if id(widget) not in before:
            return True
        return any(id(row) not in before for row in rows)

    def tracked():
        """いま我々が面倒を見ている一覧（消えたものは落とす）。"""
        live = [box for box in state["boxes"] if alive(box)]
        state["boxes"] = live
        return live

    def track(box):
        if not any(box is seen for seen in state["boxes"]):
            state["boxes"].append(box)

    def reject(widget, reason):
        """掴まなかった相手と、その理由を1度だけ残す。

        「掴めた／掴めなかった」の理由がログに無いと、
        実機で1往復ずつ潰す羽目になる。
        落とした理由まで書くのがここの仕事。
        """
        key = (type(widget).__name__, reason)
        if key in warned:
            return
        warned.add(key)
        note("skipped {} ({}): {} children, rect={}".format(
            type(widget).__name__, reason, len(children_of(widget)), rect_of(widget)))

    def candidates(hud, before):
        """収める相手。見つからなければ空。"""
        found = list(tracked())
        for widget in tree(hud):
            if widget is hud or any(widget is seen for seen in found):
                continue
            if not has_columns(widget):
                continue          # 列を持てない ＝ この直し方の対象外（黙って通す）
            if len(children_of(widget)) < MIN_ROWS:
                continue
            if inside_button(widget):
                reject(widget, "inside a button")
                continue
            rows = rows_of(widget)
            if not rows:
                reject(widget, "children do not look like a stacked list")
                continue
            if before is not None and not fresh(widget, rows, before):
                reject(widget, "not rebuilt by this press")
                continue
            found.append(widget)
        return found

    # -- 控え（触る前の寸法） ------------------------------------------------
    def settled(rows, gap_y):
        """行の位置と高さが噛み合っているか（＝レイアウトが走った後か）。

        見るのは行だけ。
        入れ物自身の矩形は当てにしない。
        実機では行が並び終わっているのに `ToolListPopup` の矩形が
        `(0, 0, 926, 78.75)` のまま、という瞬間がある（GAME.md §2.14.1。
        ここを条件に入れると一覧を1つも掴めない）。
        """
        bottom, top = bounds(rows)
        total = gap_y * max(0, len(rows) - 1) + sum(
            size_of(row, "height", 0.0) for row in rows)
        return abs((top - bottom) - total) <= max(2.0, total * 0.02)

    def box_design(box, rows, gap_y):
        """触る前の寸法。**レイアウトが落ち着く前には控えない**（None を返す）。

        位置の基準も行から採る（入れ物の矩形は上のとおり当てにならない）。
        戻すための値は、実際に書き換える直前に1つずつ控える（`remember`）。
        """
        design = frames.attr(box, BOX_ATTR, None)
        if isinstance(design, dict):
            return design
        if not settled(rows, gap_y):
            return None
        bottom, _top = bounds(rows)
        design = {"cols": frames.attr(box, "cols", None),
                  "bottom": bottom,
                  "left": min(size_of(row, "x", 0.0) for row in rows),
                  "was": {}}
        try:
            setattr(box, BOX_ATTR, design)
        except Exception:
            pass              # 控えを持てない相手でも1回は正しく列にできる
        sample = rows[0]
        # 同じ形の一覧を開くたびに書かない（開閉のたびに新しい入れ物が来るので、
        # そのまま書くと数十行で記録の上限に当たり、後の行が落ちる）。
        signature = ("popup", type(box).__name__, len(rows),
                     round(size_of(box, "width", 0.0) or 0.0),
                     round(size_of(box, "height", 0.0) or 0.0),
                     round(size_of(box, "minimum_height", 0.0) or 0.0))
        if signature in warned:
            return design
        warned.add(signature)
        note("popup {} cols={} rows_prop={} spacing={} padding={} size_hint={} "
             "pos_hint={} pos={} size={} minimum_height={} rows={} row={}x{} "
             "row.size_hint={} row.pos_hint={} bottom={:.0f} left={:.0f}".format(
                 type(box).__name__, frames.attr(box, "cols", None),
                 frames.attr(box, "rows", None),
                 frames.repr_value(frames.attr(box, "spacing", None)),
                 frames.repr_value(frames.attr(box, "padding", None)),
                 frames.repr_value(frames.attr(box, "size_hint", None)),
                 frames.repr_value(frames.attr(box, "pos_hint", None)),
                 frames.repr_value(frames.attr(box, "pos", None)),
                 frames.repr_value(frames.attr(box, "size", None)),
                 frames.repr_value(frames.attr(box, "minimum_height", None)),
                 len(rows),
                 round(max(size_of(row, "width", 0.0) for row in rows)),
                 round(max(size_of(row, "height", 0.0) for row in rows)),
                 frames.repr_value(frames.attr(sample, "size_hint", None)),
                 frames.repr_value(frames.attr(sample, "pos_hint", None)),
                 bottom, design["left"]))
        return design

    def remember(box, name):
        """書き換える直前の値を1度だけ控える。

        入れ物の矩形をまとめて控えないのは、
        控えた瞬間の矩形が組み上がる前の値だったときに、**戻すとそのおかしな値が入ってしまう**ため。
        """
        design = frames.attr(box, BOX_ATTR, None)
        if not isinstance(design, dict) or name in design["was"]:
            return
        design["was"][name] = frames.attr(box, name, None)

    def restore(box):
        """この一覧を、我々が触る前の姿へ戻す（触った項目だけ戻す）。"""
        design = frames.attr(box, BOX_ATTR, None)
        if not isinstance(design, dict):
            return
        for name, value in design.get("was", {}).items():
            if value is None:
                continue
            try:
                setattr(box, name, value)
            except Exception:
                ctx.log_exc("item list: could not restore " + name)
        try:
            delattr(box, BOX_ATTR)
        except Exception:
            pass

    def restore_all():
        """一覧が作り直される前に、控えを捨てて元の姿へ戻す。"""
        for box in tracked():
            restore(box)
        state["boxes"] = []

    # -- 収める --------------------------------------------------------------
    def describe(box, rows):
        parent = parent_of(box)
        return "list {}{} rows={} parent={} sample={!r}".format(
            type(box).__name__, rect_of(box), len(rows),
            type(parent).__name__ if parent is not None else None,
            [frames.attr(row, "text", "")[:12] for row in rows[:3]])

    def wanted_columns(count, row_h, row_w, gap, pad, allowed, left, win_width):
        """窓に収まる列数。行の高さも位置も変えずに決まるのはここだけ。"""
        gap_x, gap_y = gap
        pad_x, pad_y = pad
        per_column = int((allowed - pad_y + gap_y) // max(1.0, row_h + gap_y))
        per_column = max(1, per_column)
        columns = int(math.ceil(float(count) / per_column))
        # 横に置ける数でも頭打ちにする（左端は動かさないので右へ伸びる）。
        room = win_width - MARGIN - max(left, 0.0) - pad_x
        by_width = int((room + gap_x) // max(1.0, row_w + gap_x))
        columns = min(columns, max(1, by_width), max(1, int(MAX_COLUMNS)))
        if int(FIXED_COLUMNS) > 0:
            columns = min(int(FIXED_COLUMNS), max(1, by_width))
        return max(1, columns)

    def fit_one(box):
        """一覧を1つ、列を増やして窓の内側へ収める。何度呼んでも同じ結果になる。"""
        rows = rows_of(box)
        if not rows:
            return False
        win_width, win_height = window_size()
        if not win_height:
            return False

        gap = pair(frames.attr(box, "spacing", None), 0.0)
        pad = pair(frames.attr(box, "padding", None), 0.0)
        design = box_design(box, rows, gap[1])
        if design is None:
            reject(box, "the rows have not been laid out yet")
            return False          # まだレイアウトが走っていない。次の回で測る

        row_h = max(size_of(row, "height", 0.0) for row in rows)
        row_w = max(size_of(row, "width", 0.0) for row in rows)
        count = len(rows)
        needed = count * (row_h + gap[1]) - gap[1] + pad[1]

        # 使ってよい高さ。
        # 窓に対する上限と、触る前の下端から上の空きの小さい方。
        # 下端を控えから採るのは、高さを変えると下端が動くビルドで、
        # 測り直すたびに列数が行ったり来たりするのを止めるため。
        allowed = win_height * float(MAX_FILL)
        bottom = design["bottom"]
        room = win_height - MARGIN - max(bottom, 0.0)
        if room >= win_height * 0.25:
            allowed = min(allowed, room)

        columns = wanted_columns(count, row_h, row_w, gap, pad, allowed,
                                 design["left"], win_width)
        current = frames.attr(box, "cols", None)
        if columns <= 1 and (current in (1, None) or needed <= allowed):
            reject(box, "one column already fits")
            return False          # 1列のままで収まっている ＝ 触らない

        shape = (count, columns, round(needed), round(allowed), round(win_height))
        if state["shape"] != shape:
            state["shape"] = shape
            note("{} needed={:.0f} allowed={:.0f} window={:.0f} -> cols={}".format(
                describe(box, rows), needed, allowed, win_height, columns))

        try:
            if current != columns:
                remember(box, "cols")
                box.cols = columns
            # `rows` も入っていると格子は `cols x rows` で固定され、
            # 行数がこちらの列数と噛み合わない（Kivy は枠が足りなければレイアウトを行わない）。
            # 列数から行数を出させる。
            if frames.attr(box, "rows", None) is not None:
                remember(box, "rows")
                box.rows = None
        except Exception:
            ctx.log_exc("item list: could not set the number of columns")
            return False
        relayout(box)

        per_column = int(math.ceil(float(count) / columns))
        content_h = per_column * (row_h + gap[1]) - gap[1] + pad[1]
        if content_h > allowed + 1.0 and "capped" not in warned:
            # 列の上限（または窓の幅）に当たって、まだはみ出している。
            warned.add("capped")
            note("still taller than the window at {} column(s) "
                 "({:.0f} > {:.0f}); raise MAX_COLUMNS to fit {} row(s)".format(
                     columns, content_h, allowed, count))
        content_w = columns * (row_w + gap[0]) - gap[0] + pad[0]
        resize(box, content_h, content_w)
        track(box)
        # 本当に折り返ったかを見届ける。
        # 折り返っていなければ自分で並べる。
        schedule(lambda: guard(lambda: verify(box, columns, gap)), VERIFY_DELAY)
        return True

    def relayout(box):
        """入れ物に組み直しを促す。

        `cols` を変えれば Kivy は次のフレームで組み直す。
        はずだが、`cols=2` を入れても見た目が変わらないビルドがある（GAME.md
        §2.14.1）。
        促すのはただで、余計に走っても結果は同じなので、こちらからも1回叩く。
        """
        for name in ("_trigger_layout", "do_layout"):
            call = frames.attr(box, name, None)
            if callable(call):
                try:
                    call()
                except Exception:
                    ctx.log_exc("item list: could not ask for a relayout")
                return

    def columns_seen(rows):
        """行が実際に何列に並んでいるか（左端の種類の数）。"""
        return len(set(round(size_of(row, "x", 0.0), 1) for row in rows))

    def verify(box, columns, gap):
        """列にしたあと、本当に折り返ったかを見る。

        入れ物が `GridLayout` でも、行の位置をその格子が決めていない作りはありうる（実機の `ToolListPopup` は箱の高さ
        78.75 に対して行が 0〜1026 に並んでいた）。
        折り返っていなければ、行の位置をこちらで入れる。
        """
        if not alive(box):
            return
        rows = rows_of(box)
        design = frames.attr(box, BOX_ATTR, None)
        if not rows or not isinstance(design, dict):
            return
        seen = columns_seen(rows)
        bottom, top = bounds(rows)
        if "after" not in warned:
            warned.add("after")
            note("after: cols={} rows={} size={} minimum_height={} "
                 "columns_seen={} bounds=({:.0f}, {:.0f})".format(
                     frames.attr(box, "cols", None), frames.attr(box, "rows", None),
                     frames.repr_value(frames.attr(box, "size", None)),
                     frames.repr_value(frames.attr(box, "minimum_height", None)),
                     seen, bottom, top))
        if seen >= columns:
            return                # 格子が折り返してくれた。何もしない
        place(box, rows, columns, gap, design)

    def place(box, rows, columns, gap, design):
        """行の位置をこちらで入れる（格子が並べてくれないビルドのため）。

        並べ方は今の見え方をそのまま保つ: 下端から上へ積み、
        1列が埋まったら右の列の下端から続ける。
        位置の基準（下端・左端）は控えた値なので、何度呼んでも同じ結果になる。
        """
        row_h = max(size_of(row, "height", 0.0) for row in rows)
        row_w = max(size_of(row, "width", 0.0) for row in rows)
        per_column = int(math.ceil(float(len(rows)) / columns))
        moved = 0
        for index, row in enumerate(rows):          # 下から上（今の並び順）
            column, line = divmod(index, per_column)
            x = design["left"] + column * (row_w + gap[0])
            y = design["bottom"] + line * (row_h + gap[1])
            try:
                if abs(size_of(row, "x", 0.0) - x) > 0.5 \
                        or abs(size_of(row, "y", 0.0) - y) > 0.5:
                    row.x, row.y = x, y
                    moved += 1
            except Exception:
                ctx.log_exc("item list: could not place a row")
                return
        if moved and "placed" not in warned:
            warned.add("placed")
            note("the grid did not wrap; placed {} row(s) into {} column(s) "
                 "by hand ({}x{} each, from bottom={:.0f} left={:.0f})".format(
                     moved, columns, round(row_w), round(row_h),
                     design["bottom"], design["left"]))

    def guard(fn):
        """遅れて走る処理。ここで投げるとゲームを巻き込む。"""
        try:
            return fn()
        except Exception:
            ctx.log_exc("item list: the follow-up pass failed")
            return None

    def resize(box, content_h, content_w):
        """列を増やしたぶん、入れ物の高さ（と足りなければ幅）を合わせる。

        高さは**中身に合わせる。低い側にも合わせる**のが要点。
        実機の `ToolListPopup` は `size_hint=[1, 1]` で入力欄の帯（高さ
        78.75）と同じ寸法になっており、中身（1026）とまるで合っていない。
        格子が並べ直すならその
        78.75 の中に押し込むことになるので、**先に中身ぶんの高さを入れておく**。
        下端は動かさないので、伸びるのは上へ。

        高さは入れ物自身が出している `minimum_height` を優先するが、
        まだ組み直されていない（前の列数のままの）値を掴まないよう、
        こちらの計算とかけ離れていたら使わない。
        """
        target = content_h
        own = size_of(box, "minimum_height", None)
        if own and abs(own - content_h) <= content_h * 0.5:
            target = own
        height = size_of(box, "height", None)
        try:
            if height is not None and abs(height - target) > 1.0:
                if frames.attr(box, "size_hint_y", None) is not None:
                    # 親（入力欄の帯）に高さを決められたままだと中身が入らない。
                    remember(box, "size_hint_y")
                    box.size_hint_y = None
                remember(box, "height")
                box.height = target
        except Exception:
            ctx.log_exc("item list: could not set the height")
        width = size_of(box, "width", None)
        try:
            if width is not None and width < content_w - 1.0:
                if frames.attr(box, "size_hint_x", None) is not None:
                    remember(box, "size_hint_x")
                    box.size_hint_x = None
                remember(box, "width")
                box.width = content_w
        except Exception:
            ctx.log_exc("item list: could not set the width")

    def fit(hud, before):
        boxes = candidates(hud, before)
        done = False
        for box in boxes:
            try:
                done = fit_one(box) or done
            except Exception:
                ctx.log_exc("item list: fitting failed; leaving the list alone")
        return done

    def fit_later(hud, before):
        """今と、レイアウトが落ち着いた後に当てる。`fit` は何度でも同じ結果。"""
        for delay in PASS_DELAYS:
            schedule(lambda: fit(hud, before), delay)

    # -- フック --------------------------------------------------------------
    # アイコンの押下 ＝ 一覧が開く（もう一度押すと閉じる）。
    # どちらでも一覧は作り直されるので、控えを捨ててから測り直す。
    def press(orig, self, args, kwargs):
        before = ids_of(self)
        restore_all()
        result = orig(self, *args, **kwargs)
        if not fit(self, before) and "none" not in warned:
            # 見つからないのは異常ではない。
            # 畳んだときもここへ来る。
            warned.add("none")
            note("nothing to fit after the icon press "
                 "(the list was closed, or it already fits)")
        fit_later(self, before)
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.press_item_icon", safe=True)
    def press_item_icon(orig, self, *args, **kwargs):
        return press(orig, self, args, kwargs)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.press_skill_icon",
              safe=True, required=False)
    def press_skill_icon(orig, self, *args, **kwargs):
        return press(orig, self, args, kwargs)

    ctx.log("item list: fitting the item/skill list by adding columns "
            "(fill={}, max {} column(s), fixed={}); "
            "what it found goes to out/{}".format(
                MAX_FILL, MAX_COLUMNS, FIXED_COLUMNS or "auto", LOG_BASENAME))
