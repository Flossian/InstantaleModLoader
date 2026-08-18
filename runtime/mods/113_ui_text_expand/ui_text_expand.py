# -*- coding: utf-8 -*-
"""追加: 本文の表示域を、画面の隅のボタン1つで広げたり元に戻したりする。

## 何が困っているか

本文（情景描写・LLM の応答・システムメッセージ）が出るのは画面の一部分で、
そこは選択肢・所持品・立ち絵と場所を分け合っている。
長い応答が来ると、読むために毎回そこをスクロールすることになる。
文字を小さくしても、
行間を詰めても（`112_ui_text_spacing`）、**枠そのものが広がるわけではない**。

そこで「今だけ広く読みたい」を押せる形にする。
押すと本文の枠が広がり、もう一度押すと元の寸法に戻る。
ゲームの本文・会話履歴・セーブには一切触らない。

## 何を広げるか（枠であって文字ではない）

広げるのは**本文のラベルを載せている入れ物**で、ラベルそのものではない。
本文の高さはゲームが `InstanTaleHUD.update_label_height()` で決めているので（GAME.md §2.3）、
ラベルの高さをこちらが決めても次の1文字で塗り直される。
入れ物（スクロールする枠）を広げれば、同じ仕組みのまま見える行数が増える。

    hud.text_display          本文のラベル（font_size=27 / line_height=1.8）
      ← 親をたどる            ScrollView（`scroll_enabled` / `_on_scroll_resize` の相手）
        ← ここを広げる

入れ物は**型で決めつけず、実行時に探す**（GAME.md §1.3）。
ラベルから親を上へたどり、`scroll_y` と
`do_scroll_y` を持つ最初の相手を「本文の枠」とみなす。
見つからないビルドでは HUD の1つ下の親を使い、
それも無ければ何もしない（警告を1回だけ出す）。

### 枠線は別のウィジェットが描いている

入れ物だけを広げると、**本文は増えるのに画面に見えている枠線は元の大きさのまま**になる。
枠線は `add_border(widget)` / `update_border` がウィジェットの `pos` /
`size` に束ねて描くので（`scripts.hud.new_hud`）、
束ねられている相手が別のウィジェットだとこちらが広げた入れ物には付いてこない。

その相手もクラス名では決めつけない。
**今その枠と同じ場所に同じ大きさで置かれているもの**を「枠の仲間」とみなして一緒に広げる。
枠線・背景・影は定義上そこに重なっている。
兄弟を見て、親も同じ矩形ならもう一段上へ（`members_of`）。
寸法ではなく倍率を配るので、数 px 大きい枠線もその比のまま広がる。

仲間の見つけ方を直すときのために、枠のまわりの構成（親・兄弟の型と矩形）を
`out/text_expand.log` に1回だけ書く。
ここが唯一の資料になる。

## 元に戻せること

戻すために覚えるのは、こちらが触る前の寸法一式（`size_hint` / `size` / `pos` / ラベルの
`text_size`）。
これを**入れ物のウィジェット自身に持たせる**（`DESIGN_ATTR`）。
MOD 側の変数に持つと、注入し直したときに「広げた後の寸法」を設計値として控えてしまい、押すたびに枠が育っていく（`112_` が
`line_height` で踏んだのと同じ罠）。

## 触る軸は伸ばす軸だけ

横の倍率が 1.0（既定）なら、幅には一切触らない。
`size_hint` を丸ごと外して測った幅を入れ直すと、
`size_hint_x=1` でゲームが決めていた幅が「その瞬間の実測値」に固定され、
数 px 動いて見える。
外すのは `size_hint_y` だけで、横はゲームの決め方へ預けたままにする。
折り返し幅（`text_display.text_size`）も同じ理由で、横に広げるときだけ触る。

## 伸びる向き（上だけ）

位置は一切入れない。
Kivy の `y` は下端なので、`y` を据え置いて
`height` だけ増やすと下端が動かず上へ伸びる。
枠の下には入力欄と状態表示が並んでいるので、下端が動かないことは、
そのまま「他をずらさない」になる。
上に在るのは背景画像だけで、広げるときに隠れてよいのはそちら。

`pos_hint` を持つ相手（実機の枠は `center_y: 0.5` を持つ）も同じ理由で触らない。
親の中で寄せ直されるだけで、画面のどこに出るかは
`pos_hint` を持たない側（親）の伸び方が決める。
伸ばした後、窓からはみ出していれば次のフレームで内側へ寄せる（仲間は同じ量だけ動かす。
別々に収めると枠線と中身がずれる）。

## ゲーム側の採寸とぶつからないようにする

枠の寸法はゲームも触る（`InstanTaleHUD.update_text_display_size`
/ 窓の大きさが変わったとき）。
そこで:

* 広げた後にゲームの採寸 `update_text_display_size()` を1回呼んで、
  折り返し幅（`text_display.text_size`）をゲーム自身の計算で追従させる。
  追従しないビルドではこちらが「入れ物の幅
  − ゲームが空けていた左右の余白」を入れる（余白は設計値から逆算する。
  `109_` と同じ立場で、寸法を発明しない）
* その呼び出しが**枠の寸法を元に戻してしまう**ビルドなら、
  以後は呼ばない（`state["ask_game"]`）。
  1回だけ警告を出して、こちらの採寸だけで動く
* 本文が塗り直されるたびに、広げたままかどうかを見て必要なら当て直す。
  窓の大きさが変わってゲームが枠を組み直しても、広げた状態が戻ってしまわない

## ボタン

選択肢の枠（`hud.buttons` ＝ 4枠）は使わない。
あそこはゲームの行動に使う場所で、1枠を常時潰すと遊ぶ手数が減る。
代わりに `Button` を1枚足す。
`add_widget` の既定は先頭挿入で、
Kivy は子を逆順に描くので後から足したものが一番上に出る。

**足す先は HUD ではなく、HUD が持っている `FloatLayout` の中**（`host_of`）。
素の HUD の子はその 1枚だけで、
そこへ直接足すと子が2つになり、**「画面の最初の子」を取る側から見える相手が変わる**（`scripts.hud.new_hud:get_current_screen_root`）。
実際、この形にする前は **アイテムを持ち物へ移す・装備する操作が効かなくなる**（VERIFICATION_LOG.md
§2.33）。
ドラッグ中のアイテムの置き場所や
`InventoryItem.get_all_inventories()` の起点がこちらのボタンにすり替わったためと考えられる。

> 他人の画面に物を足すときは、**その画面の子の並びを変えない**。
> 並びを手がかりにしている処理は、こちらからは見えない
> （ソースが読めない以上、確かめようもない）。

見た目は背景なしの白いアイコン（既定）。
画像ファイルは持たない。
Kivy の canvas に線で描く。
0〜1 の座標で形を持つので、どの解像度でも滲まず、
色も透過もこちらで決められる（配布物にバイナリが増えないのも利点）。
押すと上下が入れ替わり、
「次に何が起きるか」がそのまま形になる（上向き＝広がる／下向き＝戻る）。

線を引き直すのは**位置・大きさ・向き・絵柄のどれかが変わったときだけ**。
本文は 1文字ずつ増えるので塗り直しは何十回も来る。
毎回引き直すと無駄が積み上がる。

絵柄に「文字」を選ぶと今までどおりの文字ボタンになる。
そのときフォントは本文のラベルから写す。
Kivy の既定（Roboto）には日本語が無く、写さないとボタンの文字が豆腐になる。

置き場所の既定はキャラの欄の上（画面の隅はどれも既存の表示と重なりやすい）。
その欄は位置から探す（`side_panel_of`）。
画面下の帯は「左（状態）／入力欄／本文の枠／右（立ち絵と
HP）」の4つが並んでいるので、キャラの欄は**枠の右端より右に始まる、
いちばん大きい入れ物**として決まる。

画像（`source`）で立ち絵を探すのは予備に降格してある。
`hud.image_portrait` と同じ絵を持つウィジェットを探す方法は一見すじが良いが、
会話へ入った瞬間に相手の絵を持つ別のウィジェットへ乗り換え、
ボタンが画面の左へ飛ぶ（VERIFICATION_LOG.md §2.26）。
場所で決めるほうが、画面が変わっても動かない。
どちらも無い画面では右上へ落とす。

設定で「枠の右上」（本文の枠の内側）と、画面の四隅も選べる。
枠の内側に置いた場合は枠が伸びれば一緒に上がる（毎回の塗り直しで枠の矩形から座標を出し直す）。

## 窓の大きさが変わったとき

塗り直し（`update_display_text` /
`update_button_texts`）は本文や選択肢が変わったときにしか来ない。
**窓だけ変えられるとこちらは何も気付けず**、ボタンは古い座標に取り残され、
控えている「触る前の寸法」も古い窓の値のまま残る。
そこで `Window.on_resize` を直に拾う（`watch_window`。
注入し直したら前の手を外してから結ぶ）。

拾った後の順番が要点:

1. `state["stale"]` を立てる。
   寸法を測らない（途中の、まだ古い寸法を新しい設計値として控えてしまうのを止める）
2. 次のフレームで畳んで（`size_hint` を戻して）控えを捨てる
3. さらに次のフレームで控え直して当て直す。
   間に挟まる1フレームで、ゲームが自分の寸法を入れ直す
4. 念のため `RESETTLE_DELAY` 秒後にもう一度当て直す（レイアウトが1フレームで終わらないビルド用。
   `upkeep` は何度呼んでも同じ結果になる）

## 触らないもの

`hud.display_text`（ゲームが持っている本文そのもの）、`app.buttons`、セーブ。
このMODが変えるのは枠の寸法と、自分で足したボタン1枚だけ。
剥がす（`--unload`）とフックは外れるが、
足したボタンはゲームのプロセスに残る（§3.10 の「戻らないもの」）。
そのときボタンは最後に注入された版の切り替えを呼び続けるので、押しても壊れはしない。
"""

import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "text_expand.log"

# 広げたときの寸法。
# 元の枠に対する倍率で決める（絶対値を発明しない）。
# 1.0 でその向きには広げない。
# 窓からはみ出す分は MAX_FILL で頭打ちになる。
WIDTH_SCALE = 1.0
HEIGHT_SCALE = 4.0

# ボタンの置き場所。
# 既定はキャラの欄の上（画面の隅は他の表示と重なりやすい）。
# 手がかりが見つからない画面では右上へ落ちる。
BUTTON_CORNER = "キャラの上"

# ボタンの絵柄。
# 線で描くので画像ファイルは要らない（背景なし・白）。
# 「文字」を選ぶと LABEL_EXPAND / LABEL_RESTORE の文字ボタンになる。
ICON = "二重山形"

# 線の太さ（px）と濃さ（0〜1）。
# 背景に溶けるなら濃さを上げる。
ICON_WIDTH = 2.0
ICON_ALPHA = 0.85

# ボタンの高さ（px）。
# アイコンなら正方形、文字ならこの 2 倍の幅。
# ゲームの `upx()` が読めればそれで拡縮する。
BUTTON_SIZE = 32

# ボタンの文字（絵柄に「文字」を選んだときだけ使う）。
# 押すと入れ替わる。
LABEL_EXPAND = "拡張"
LABEL_RESTORE = "戻す"

# 起動した直後から広げておくか。
START_EXPANDED = False

# 広げた枠が窓に占めてよい割合。
# 窓の縁まで詰めない（縁の UI を完全には隠さない）。
MAX_FILL = 0.98

# 隅と `pos_hint` の対応。
# ボタンの作り方ごとローダに集約してある（`116_` / `122_` と共有。TECH.md §3.2.3）。
CORNERS = ui.CORNERS

# 隅ではない置き場所（こちらが座標を入れるもの）と、その余白（px）。
ON_PORTRAIT = "キャラの上"
IN_FRAME = "枠の右上"
PLACEMENTS = (ON_PORTRAIT, IN_FRAME)
PORTRAIT_GAP = 4.0
FRAME_INSET = 8.0

# 絵柄に「文字」を選んだときの呼び名。
AS_TEXT = ui.AS_TEXT

# 立ち絵を探すのに木を何段まで降りるか。
# 本文のラベルより深いところに居る（画面下の帯 → 右の入れ物 → その中）。
PORTRAIT_DEPTH = 14

# 窓の大きさが変わってから、もう一度当て直すまでの秒数。
# ゲームのレイアウトが
# 1フレームで終わらないビルドのための2回目（1回目は次のフレーム）。
RESETTLE_DELAY = 0.3

# 「同じ矩形」と見なす許容。
# 枠線・背景はぴったり重ならず数 px ずれて置かれている。
RECT_SLACK = 12.0
RECT_RATIO = 0.03

# 一緒に広げる相手の上限。
# 同じ矩形を持つものがこれ以上あるなら、
# それは「枠の仲間」ではなく別の作りなので触らない。
MAX_MEMBERS = 12

# ラベルから親を何段まで上へたどるか。
# ラベル → 入れ物 → ScrollView → HUD。
MAX_UP = 6

# ウィジェット木を何段まで降りて本文のラベルを探すか（属性で持たれていない場合）。
MAX_DEPTH = 6

# この長さ未満の本文はラベル探しの手がかりにしない。
# 短い文字列は選択肢のボタン（あれも Label）とも一致しうる（`112_` と同じ理由）。
MIN_MATCH = 8

# 記録の上限。
# 本文は1文字ずつ増えるので、書くのは「見つけたとき」と「押されたとき」だけ。
MAX_LOG = 60

# ウィジェット自身に持たせる控え。
# 注入し直しても残るので、
# 広げた後の寸法を設計値として控え直してしまう事故が起きない。
DESIGN_ATTR = "_instantale_expand_design"
EXPANDED_ATTR = "_instantale_expand_on"
BUTTON_ATTR = "_instantale_expand_button"
CALLBACK_ATTR = "_instantale_expand_callback"
WINDOW_ATTR = "_instantale_expand_on_resize"
ICON_ATTR = "_instantale_expand_icon"


def apply(ctx):
    upx = ui.upx
    window_size = ui.window_size
    # 利用者の意思（広げたいか）。
    # 画面を作り直されても引き継ぐので、入れ物ではなくこちらに持つ。
    # 入れ物側にあるのは「今その枠が広いか」。
    state = {"expanded": bool(START_EXPANDED), "logged": 0, "ask_game": True,
             "synced": False, "anchor": None, "no_portrait": False,
             "watching": False, "hud": None, "stale": False}
    warned = set()
    labels = weakref.WeakKeyDictionary()

    write = ctx.logger(LOG_BASENAME)

    def note(text):
        if state["logged"] < MAX_LOG:
            state["logged"] += 1
            write(text)

    def warn_once(key, message):
        if key in warned:
            return
        warned.add(key)
        ctx.log("text expand: " + message, level="WARN")

    # -- 本文のラベルを探す --------------------------------------------------
    def is_label(widget):
        """本文を描けるウィジェットか。型では見ない（GAME.md §1.3）。"""
        for name in ("text", "texture_update", "text_size"):
            if frames.attr(widget, name) is frames.MISSING:
                return False
        return isinstance(frames.attr(widget, "text"), str)

    def matches(text, value):
        """`value`（ゲームが持っている本文）と同じものを描いているラベルか。

        完全一致は期待しない。
        実測では `text_display.text` と `display_text` は片方がもう片方の末尾を含む関係だった（GAME.md
        §2.3）。
        """
        if not text or len(text) < MIN_MATCH:
            return False
        return (text == value or value.endswith(text) or text.endswith(value)
                or text in value or value in text)

    def scan_tree(hud, value):
        """ウィジェット木を降りて本文のラベルを探す（属性で持たれていない場合）。"""
        found = []

        def descend(widget, depth):
            if depth and is_label(widget) and matches(
                    frames.attr(widget, "text", ""), value):
                found.append(widget)
            if depth >= MAX_DEPTH:
                return
            children = frames.attr(widget, "children")
            if not isinstance(children, (list, tuple)):
                return
            for child in reversed(list(children)):
                descend(child, depth + 1)

        try:
            descend(hud, 0)
        except Exception:
            ctx.log_exc("text expand: walking the widget tree failed")
            return None
        if not found:
            return None
        # 同じくらい確からしいなら長い方（本文のラベル）。
        found.sort(key=lambda w: len(frames.attr(w, "text", "")), reverse=True)
        return found[0]

    def label_of(hud):
        """本文のラベル。見つからなければ None。

        名指し（`hud.text_display`。GAME.md §2.3 の実測）を先に見て、
        無ければ本文の文字列を手がかりに探す。
        ビルドが変わって属性名が違っても届く。
        """
        cached = labels.get(hud)
        label = cached() if cached is not None else None
        if label is not None and frames.attr(label, "parent") not in (None, frames.MISSING):
            return label

        named = frames.attr(hud, "text_display")
        if named is not frames.MISSING and is_label(named):
            label = named
        else:
            value = frames.attr(hud, "display_text", "")
            if not isinstance(value, str) or len(value) < MIN_MATCH:
                return None
            label = None
            try:
                items = list(vars(hud).items())
            except Exception:
                items = []
            for _name, widget in items:
                if is_label(widget) and matches(frames.attr(widget, "text", ""), value):
                    label = widget
                    break
            if label is None:
                label = scan_tree(hud, value)
        if label is None:
            warn_once("label", "no label is showing the narration; "
                              "leaving the text area as the game built it")
            return None
        try:
            labels[hud] = weakref.ref(label)
        except TypeError:
            pass          # 弱参照を持てない相手なら毎回探し直す（動作は同じ）
        return label

    # -- 本文の枠（広げる相手）を探す ----------------------------------------
    def is_scroller(widget):
        for name in ("scroll_y", "do_scroll_y"):
            if frames.attr(widget, name) is frames.MISSING:
                return False
        return True

    def container_of(hud, label):
        """本文のラベルを載せている枠。HUD 自身は返さない（画面全部が動く）。"""
        widget = frames.attr(label, "parent")
        below_hud = None
        for _step in range(MAX_UP):
            if widget in (None, frames.MISSING) or widget is hud:
                break
            below_hud = widget
            if is_scroller(widget):
                return widget
            widget = frames.attr(widget, "parent")
        if below_hud is None:
            warn_once("container", "the narration label sits directly on the HUD; "
                                   "there is no frame to grow")
        return below_hud

    # -- 設計値（こちらが触る前の寸法） --------------------------------------
    def numbers(value, count):
        """`size_hint` / `size` などを素の tuple にする（Kivy の可変列を持ち歩かない）。"""
        try:
            return tuple(value)[:count]
        except Exception:
            return None

    def close_enough(value, wanted):
        """寸法が「もうその値になっている」か。浮動小数の丸めは差と見ない。"""
        try:
            return abs(float(value) - float(wanted)) < 0.5
        except (TypeError, ValueError):
            return False

    def rect_of(widget):
        size = numbers(frames.attr(widget, "size"), 2)
        pos = numbers(frames.attr(widget, "pos"), 2)
        if not size or not pos:
            return None
        try:
            return (float(pos[0]), float(pos[1]), float(size[0]), float(size[1]))
        except (TypeError, ValueError):
            return None

    def same_rect(rect, target):
        """見た目に同じ矩形か。枠線・背景は数 px ずれて置かれていることがある。"""
        if rect is None or target is None:
            return False
        slack_x = max(RECT_SLACK, target[2] * RECT_RATIO)
        slack_y = max(RECT_SLACK, target[3] * RECT_RATIO)
        return (abs(rect[0] - target[0]) <= slack_x
                and abs(rect[1] - target[1]) <= slack_y
                and abs(rect[2] - target[2]) <= slack_x * 2
                and abs(rect[3] - target[3]) <= slack_y * 2)

    def members_of(hud, box):
        """一緒に広げる相手（＝同じ矩形を占めている仲間）。

        スクロールする入れ物を広げただけでは、
        画面に見えている**枠線や背景は元の大きさのまま**になる。
        枠線は `add_border(widget)` / `update_border` がウィジェットの `pos` /
        `size` に束ねて描くので、
        束ねられている相手が別のウィジェットだと付いてこない。

        その相手をクラス名で決めつけない（GAME.md §1.3）。
        **今その枠と同じ場所に同じ大きさで置かれているもの**を仲間とみなす。
        枠線・背景・影は定義上そこに重なっている。
        兄弟を見てから、親も同じ矩形ならもう一段上へ。
        """
        target = rect_of(box)
        found, seen = [box], {id(box)}
        if target is None:
            return found
        node = box
        for _step in range(MAX_UP):
            parent = frames.attr(node, "parent")
            if parent in (None, frames.MISSING):
                break
            # 兄弟は HUG の直下でも見る（枠が HUD に直接乗っているビルドがある）。
            children = frames.attr(parent, "children")
            for child in list(children) if isinstance(children, (list, tuple)) else []:
                if id(child) in seen or len(found) >= MAX_MEMBERS:
                    continue
                if same_rect(rect_of(child), target):
                    seen.add(id(child))
                    found.append(child)
            # 上がるのは HUD の手前まで。
            # HUD 自身を広げると画面全部が動く。
            if parent is hud:
                break
            if not same_rect(rect_of(parent), target) or id(parent) in seen:
                break
            seen.add(id(parent))
            found.append(parent)
            node = parent
        return found

    def capture(hud, box, label):
        members = []
        for widget in members_of(hud, box):
            size = numbers(frames.attr(widget, "size"), 2)
            if not size:
                continue
            hint = frames.attr(widget, "pos_hint")
            members.append({
                "widget": widget,
                "size_hint": numbers(frames.attr(widget, "size_hint"), 2),
                "size": size,
                "pos": numbers(frames.attr(widget, "pos"), 2),
                "pos_hint": dict(hint) if isinstance(hint, dict) else {},
            })
        if not members:
            return None
        design = dict(members[0])
        design["members"] = members
        design["text_size"] = numbers(frames.attr(label, "text_size"), 2)
        del design["widget"]
        return design

    def design_of(hud, box, label):
        """この枠の元の寸法。最初に見たとき（＝まだ広げていないとき）の値。

        控え先はウィジェット自身。
        MOD 側の変数だけに持つと、
        注入し直したときに広げた後の寸法を設計値として控えてしまう。
        """
        saved = frames.attr(box, DESIGN_ATTR)
        if isinstance(saved, dict) and saved.get("size"):
            if not saved.get("members"):
                # 枠1つだけを覚えていた頃の控え（ゲームを起動したまま新しい版を注入した場合）。
                # 写し直さない。
                # 今は広がっているかもしれず、
                # その寸法を設計値として控えると元に戻せなくなる。
                member = dict(saved)
                member["widget"] = box
                member.pop("members", None)
                member.pop("text_size", None)
                saved["members"] = [member]
            return saved
        if frames.attr(box, EXPANDED_ATTR) is True:
            # 広がっている枠からは絶対に控えない。
            # 今の寸法はこちらが入れた値なので、
            # それを設計値にすると二度と元に戻せない（押すたびに育つ）。
            # 控えだけ失われた状態（畳むのに失敗したなど）はここで止める。
            warn_once("orphan", "the text frame is expanded but its original size "
                                "is gone; leaving it as it is")
            return None
        fresh = capture(hud, box, label)
        if fresh is None or not fresh["size"]:
            return None
        try:
            setattr(box, DESIGN_ATTR, fresh)
        except Exception:
            pass          # 属性を足せない相手でも、この場の1回は動く
        note("frame {} design size={} size_hint={} pos={} pos_hint={} text_size={}".format(
            type(box).__name__, fresh["size"], fresh["size_hint"], fresh["pos"],
            fresh["pos_hint"], fresh["text_size"]))
        note("frame family: {}".format(
            ", ".join("{} {}".format(type(m["widget"]).__name__, m["size"])
                      for m in fresh["members"])))
        note("frame neighbours: {}".format(describe_neighbours(hud, box)))
        return fresh

    def describe_neighbours(hud, box):
        """枠のまわりに何が置かれているか（**仲間の見つけ方を直すときの唯一の資料**）。

        枠線が付いてこないビルドでは、ここに出た相手の寸法を見て条件を決め直す。
        """
        lines, node = [], box
        for _step in range(MAX_UP):
            parent = frames.attr(node, "parent")
            if parent in (None, frames.MISSING):
                break
            children = frames.attr(parent, "children")
            children = list(children) if isinstance(children, (list, tuple)) else []
            lines.append("[{} {} <- {}]".format(
                type(parent).__name__, rect_of(parent),
                " ".join("{}{}".format(type(c).__name__, rect_of(c))
                         for c in children[:MAX_MEMBERS])))
            node = parent
        return " ".join(lines)

    # -- 広げる / 戻す --------------------------------------------------------
    def wanted_size(design):
        """広げたときの寸法。倍率を掛けて、窓に入る分で頭打ちにする。"""
        width, height = float(design["size"][0]), float(design["size"][1])
        win_width, win_height = window_size()
        new_width = width * max(float(WIDTH_SCALE), 1.0)
        new_height = height * max(float(HEIGHT_SCALE), 1.0)
        if win_width:
            new_width = min(new_width, win_width * MAX_FILL)
        if win_height:
            new_height = min(new_height, win_height * MAX_FILL)
        return max(new_width, width), max(new_height, height)

    def set_wrap_width(hud, label, design, box_width):
        """折り返し幅を新しい枠に合わせる。

        まずゲーム自身の採寸に任せる（ビルドごとの決まりを知らずに済む）。
        追従しなければ「枠の幅 − ゲームが空けていた左右の余白」を入れる。
        余白は設計値から逆算した値で、こちらが決めた数字ではない。
        """
        before = numbers(frames.attr(label, "text_size"), 2)
        if state["ask_game"]:
            sizer = frames.attr(hud, "update_text_display_size")
            if callable(sizer):
                try:
                    sizer()
                except Exception:
                    ctx.log_exc("text expand: the game's own sizer failed")
        after = numbers(frames.attr(label, "text_size"), 2)
        if after and before and after[0] != before[0]:
            return          # ゲームが追従した。こちらは触らない
        design_text = design.get("text_size")
        if not design_text or not design_text[0]:
            return          # 折り返し幅を持たないラベル。幅には触らない
        margin = float(design["size"][0]) - float(design_text[0])
        width = max(box_width - margin, 1.0)
        try:
            label.text_size = (width, design_text[1] if len(design_text) > 1 else None)
        except Exception:
            ctx.log_exc("text expand: could not set the wrap width")

    def settle(hud, label):
        """枠を変えた後、本文の高さをゲーム自身の計算で決め直させる（GAME.md §2.3）。"""
        try:
            label.texture_update()
            update_height = frames.attr(hud, "update_label_height")
            if callable(update_height):
                update_height()
        except Exception:
            ctx.log_exc("text expand: could not settle the label height")

    def clamp(box):
        """`pos_hint` を持たない枠を窓の内側へ。位置はゲームが入れるので次のフレームに。"""
        win_width, win_height = window_size()
        if not win_width or not win_height:
            return
        try:
            if box.y + box.height > win_height:
                box.y = win_height - box.height
            if box.y < 0:
                box.y = 0
            if box.x + box.width > win_width:
                box.x = win_width - box.width
            if box.x < 0:
                box.x = 0
        except Exception:
            ctx.log_exc("text expand: clamp failed")

    def clamp_all(design):
        """伸びた枠を窓の内側へ。仲間は同じ量だけ動かす（別々に収めると揃わない）。

        位置を決めているのは `pos_hint` を持たない相手なので、
        その最初の1つを基準にする。
        `pos_hint` を持つ相手は親の中で寄せ直されるので触らない。
        """
        movable = [m for m in design["members"] if not m["pos_hint"]]
        if not movable:
            return
        box = movable[0]["widget"]
        before = numbers(frames.attr(box, "pos"), 2)
        clamp(box)
        after = numbers(frames.attr(box, "pos"), 2)
        if not before or not after:
            return
        shift_x, shift_y = after[0] - before[0], after[1] - before[1]
        if not shift_x and not shift_y:
            return
        for member in movable[1:]:
            widget = member["widget"]
            try:
                widget.x, widget.y = widget.x + shift_x, widget.y + shift_y
            except Exception:
                ctx.log_exc("text expand: could not move a frame widget")

    def schedule(fn, delay=0.0):
        try:
            from kivy.clock import Clock
        except Exception:
            fn()              # ゲームの外（オフライン検証）ではその場で
            return
        try:
            Clock.schedule_once(lambda _dt: fn(), delay)
        except Exception:
            ctx.log_exc("text expand: could not schedule the clamp")

    def hint_x_of(member):
        """設計の `size_hint` の横だけ。縦は必ず外すので、残すのはこちらだけ。"""
        hint = member["size_hint"]
        try:
            return hint[0]
        except (TypeError, IndexError):
            return None

    def stretch(member, ratio_x, ratio_y, widen):
        """仲間を1つ、同じ倍率で広げる。位置は入れない（上へ伸ばすため）。

        Kivy の `y` は下端なので、`y` を据え置いて
        `height` だけ増やすと下端が動かず上へ伸びる。
        入力欄・状態表示は枠の下に並んでいるので、下端が動かないことは、
        そのまま「他をずらさない」になる。

        `pos_hint` を持つ相手（実機の枠は `center_y: 0.5` を持つ）は親の中で寄せ直されるだけなので、
        こちらも触らない。
        親も同じ倍率で伸びるため、結果は親の伸び方に従う。

        **横に広げないときは幅に一切触らない**（`widen=False`）。
        `size_hint` を丸ごと外して測った値を入れ直すと、
        `size_hint_x=1` でゲームが伸ばしていた幅が「その瞬間の実測値」に固定され、
        数 px 動いて見える。
        外すのは縦だけにして、横はゲームの決め方に預けたままにする。
        """
        widget = member["widget"]
        if widen:
            widget.size_hint = (None, None)
            widget.width = float(member["size"][0]) * ratio_x
        else:
            widget.size_hint = (hint_x_of(member), None)
        widget.height = float(member["size"][1]) * ratio_y

    def expand(hud, box, label):
        design = design_of(hud, box, label)
        if design is None:
            return False
        width, height = wanted_size(design)
        # 枠線や背景は数 px 大きいことがあるので、寸法ではなく倍率を配る。
        ratio_x = width / float(design["size"][0])
        ratio_y = height / float(design["size"][1])
        # 横に広げるかどうかで、触る軸を変える。
        # 広げないなら幅には手を出さない。
        widen = ratio_x > 1.0 + 1e-6
        if (frames.attr(box, EXPANDED_ATTR) is True
                and close_enough(frames.attr(box, "height"), height)
                and (not widen or close_enough(frames.attr(box, "width"), width))):
            return False          # もう広がっている（塗り直しのたびに触らない）

        for member in design["members"]:
            stretch(member, ratio_x, ratio_y, widen)
        try:
            setattr(box, EXPANDED_ATTR, True)
            design["widened"] = widen      # 戻すときに幅を触るかどうか
        except Exception:
            pass
        if widen:
            set_wrap_width(hud, label, design, width)

        # ゲームの採寸が枠ごと元に戻すビルドなら、以後はそれを呼ばない。
        if not close_enough(frames.attr(box, "height"), height):
            state["ask_game"] = False
            for member in design["members"]:
                stretch(member, ratio_x, ratio_y, widen)
            if widen:
                set_wrap_width(hud, label, design, width)
            warn_once("sizer", "the game's own sizer resets the text frame; "
                               "sizing it here instead")
        schedule(lambda: clamp_all(design))
        settle(hud, label)
        note("expanded to {}x{:.0f} (design {:.0f}x{:.0f}), {} widget(s)".format(
            "{:.0f}".format(width) if widen else "(unchanged)",
            height, design["size"][0], design["size"][1],
            len(design["members"])))
        return True

    def collapse(hud, box, label):
        design = frames.attr(box, DESIGN_ATTR)
        if not isinstance(design, dict) or frames.attr(box, EXPANDED_ATTR) is not True:
            return False          # 触っていない枠は戻す必要もない
        widened = bool(design.get("widened"))
        try:
            for member in design["members"]:
                widget = member["widget"]
                widget.size_hint = member["size_hint"] or (None, None)
                widget.height = member["size"][1]
                # 幅は触ったときだけ戻す。
                # 触っていない幅を入れ直すと、
                # `size_hint_x` でゲームが決めていた値をこちらで固定してしまう。
                if widened:
                    widget.width = member["size"][0]
                if not member["pos_hint"] and member["pos"]:
                    widget.x, widget.y = member["pos"]
            if widened and design["text_size"]:
                label.text_size = design["text_size"]
        except Exception:
            ctx.log_exc("text expand: could not restore the frame")
            return False
        try:
            setattr(box, EXPANDED_ATTR, False)
        except Exception:
            pass
        settle(hud, label)
        note("restored to {}".format(design["size"]))
        return True

    def adopt(box):
        """注入し直したときに、画面の今の姿を意思として引き継ぐ。

        `apply()` は何度でも呼ばれる（§3.6）。
        MOD 側の変数は作り直されるので、ここで読み直さないと「広げて遊んでいたのに、
        注入し直した拍子に戻る」。
        判断の元はウィジェット自身に付いた印なので、注入をまたいで残る。
        """
        if state["synced"]:
            return
        state["synced"] = True
        current = frames.attr(box, EXPANDED_ATTR)
        if isinstance(current, bool):
            state["expanded"] = current

    # -- 窓の大きさが変わったとき --------------------------------------------
    def rebuild(hud):
        """窓が変わった後に、控えを取り直して当て直す。

        窓の大きさが変わるとゲームは画面を組み直す。
        枠の寸法も、キャラの欄の位置も新しい値になる。
        こちらが控えている「触る前の寸法」は古い窓の値なので、
        そのままだと戻せなくなり、ボタンも古い座標に取り残される。

        順番が要点:

        1. 先に元の寸法へ戻す（広げていたなら畳む）。
           ここで戻さずに控えだけ捨てると、
           次に控えるのは「広げた後の寸法」になる（`112_` の罠と同じ）
        2. 控えを捨てる
        3. さらに次のフレームで当て直す。
           畳んだ直後はまだ古い寸法が入っているので、
           その場で控え直すと古い窓の値を新しい設計値にしてしまう。
           `size_hint` を戻してあるので、
           間に挟まる1フレームでゲームが自分の寸法を入れ直す
        """
        label = label_of(hud)
        box = container_of(hud, label) if label is not None else None
        if box is not None:
            collapse(hud, box, label)
            # 畳めたときだけ控えを捨てる。
            # 畳めていない（＝今の寸法がこちらの入れた値のままの）枠で控えを捨てると、
            # 次に控えるのが広げた後の寸法になる。
            # 畳めなかったときは古い窓の控えを残す。
            # 寸法は合わないが、「戻せる」ほうを優先する。
            if frames.attr(box, EXPANDED_ATTR) is not True:
                try:
                    setattr(box, DESIGN_ATTR, None)
                except Exception:
                    pass      # 捨てられなくても、次の当て直しで寸法は合う

        def resettled():
            # ここまで来て初めて控え直してよい（ゲームの入れ直しが済んでいる）。
            state["stale"] = False
            upkeep(hud)

        schedule(lambda: guarded(resettled))

    def on_window_resize(*_args):
        reference = state.get("hud")
        hud = reference() if reference is not None else None
        if hud is None:
            return
        # 組み直しが済むまで、寸法は測らない。
        # ここを止めておかないと、
        # 途中の（古い窓のままの）寸法を新しい設計値として控えてしまう。
        state["stale"] = True
        schedule(lambda: guarded(lambda: rebuild(hud)))
        # レイアウトが1フレームで終わらないビルドのために、少し置いてもう一度。
        # `upkeep` は何度呼んでも同じ結果になる（寸法が合っていれば何もしない）。
        schedule(lambda: guarded(lambda: upkeep(hud)), RESETTLE_DELAY)

    def watch_window():
        """窓の大きさの変化を拾う。塗り直しを待たないためにここだけ別経路。

        本文が変わらない限り `update_display_text` は来ないので、
        窓だけ変えられるとこちらは何も気付けない（ボタンが古い座標に取り残される）。
        """
        if state["watching"]:
            return
        try:
            from kivy.core.window import Window
        except Exception:
            return            # ゲームの外（オフライン検証）では窓が無い
        state["watching"] = True
        # 注入し直したときに古い版の手が残らないよう、前のものを外してから結ぶ。
        previous = frames.attr(Window, WINDOW_ATTR, None)
        if previous is not None:
            try:
                Window.unbind(on_resize=previous)
            except Exception:
                pass
        try:
            Window.bind(on_resize=on_window_resize)
            setattr(Window, WINDOW_ATTR, on_window_resize)
        except Exception:
            state["watching"] = False
            ctx.log_exc("text expand: could not watch the window size")

    def apply_state(hud):
        """今の意思（広げたいか）を、今の枠に当てる。"""
        if state["stale"]:
            return            # 窓の組み直しの最中。寸法が落ち着くまで測らない
        label = label_of(hud)
        if label is None:
            return
        box = container_of(hud, label)
        if box is None:
            return
        adopt(box)
        if state["expanded"]:
            expand(hud, box, label)
        else:
            collapse(hud, box, label)

    # -- ボタン --------------------------------------------------------------
    def button_text():
        if ICON != AS_TEXT:
            return ""         # アイコンで示すので文字は出さない
        return LABEL_RESTORE if state["expanded"] else LABEL_EXPAND

    def toggle(hud):
        state["expanded"] = not state["expanded"]
        note("pressed: {}".format("expand" if state["expanded"] else "restore"))
        apply_state(hud)
        button = frames.attr(hud, BUTTON_ATTR)
        if button not in (None, frames.MISSING):
            try:
                button.text = button_text()
            except Exception:
                ctx.log_exc("text expand: could not relabel the button")
            place_button(hud, button)     # 枠が動いたぶんを追い、向きも描き直す

    def basename(path):
        return path.replace("\\", "/").rsplit("/", 1)[-1]

    def portrait_of(hud):
        """立ち絵のウィジェット。属性名で探さない（GAME.md §1.3）。

        手がかりは `hud.image_portrait` /
        `image_portrait_right`（立ち絵の画像パス）。
        その値を `source` に持っているウィジェットが、今その絵を描いている相手。
        ビルドによってパスの書き方が違いうるので、ファイル名だけでも照合する。
        """
        wanted = set()
        for name in ("image_portrait", "image_portrait_right"):
            # 既定を None にする。
            # `frames.MISSING` は文字列（"<missing>"）なので、
            # そのまま照合の材料にすると**属性を持たない相手が全部一致**する。
            value = frames.attr(hud, name, None)
            if isinstance(value, str) and value:
                wanted.add(value)
                wanted.add(basename(value))
        if not wanted:
            return None
        found, sources = [], []

        def descend(widget, depth):
            source = frames.attr(widget, "source", None)
            if isinstance(source, str) and source:
                if len(sources) < MAX_MEMBERS:
                    sources.append(source)
                if source in wanted or basename(source) in wanted:
                    rect = rect_of(widget)
                    if rect and rect[2] and rect[3]:
                        found.append((rect[2] * rect[3], widget))
            if depth >= PORTRAIT_DEPTH:
                return
            children = frames.attr(widget, "children")
            if not isinstance(children, (list, tuple)):
                return
            for child in reversed(list(children)):
                descend(child, depth + 1)

        try:
            descend(hud, 0)
        except Exception:
            ctx.log_exc("text expand: could not look for the portrait")
            return None
        if not found:
            if not state["no_portrait"]:
                state["no_portrait"] = True
                note("portrait not found; sources on screen: {}".format(sources))
            return None
        # 同じ絵を複数の場所で使っているビルドなら、大きい方（立ち絵）を採る。
        found.sort(key=lambda hit: hit[0], reverse=True)
        return found[0][1]

    def side_panel_of(hud, box):
        """本文の枠の右隣に並んでいる入れ物（立ち絵と状態が入っている側）。

        立ち絵そのものが見つからないビルド用の受け皿。
        画面下の帯には「左（状態）／入力欄／本文の枠／右（立ち絵と
        HP）」が並んでいるので、枠の右端より右に始まる相手のうち、
        いちばん大きいものを採る。
        """
        rect = rect_of(box)
        if rect is None:
            return None
        # 幅は広げる前の値で見る。
        # 広げた枠を基準にすると、
        # 右隣に居るはずの相手が「枠の中」に入ってしまって見つからなくなる。
        design = frames.attr(box, DESIGN_ATTR)
        if isinstance(design, dict) and design.get("size"):
            rect = (rect[0], rect[1], float(design["size"][0]), rect[3])
        node, parent = box, frames.attr(box, "parent")
        for _step in range(MAX_UP):
            if parent in (None, frames.MISSING) or parent is hud:
                break
            if not same_rect(rect_of(parent), rect):
                break
            node, parent = parent, frames.attr(parent, "parent")
        children = frames.attr(parent, "children")
        if not isinstance(children, (list, tuple)):
            return None
        right = rect[0] + rect[2]
        best = None
        for child in list(children):
            if child is node:
                continue
            other = rect_of(child)
            if other is None or not other[2] or not other[3]:
                continue
            if other[0] < right - RECT_SLACK:
                continue
            area = other[2] * other[3]
            if best is None or area > best[0]:
                best = (area, child)
        return best[1] if best is not None else None

    def place_button(hud, button):
        """置き直す。塗り直しのたびに呼ぶ（枠も立ち絵も動くことがある）。

        隅に置く設定のときは `pos_hint` に任せるので、
        位置は入れない（それでもアイコンは描き直す。窓が変われば座標が変わる）。
        """
        if BUTTON_CORNER not in PLACEMENTS:
            paint_icon(button)
            return
        label = label_of(hud)
        box = container_of(hud, label) if label is not None else None

        if BUTTON_CORNER == IN_FRAME:
            # 本文の枠の内側・右上。
            # 枠が伸びれば一緒に上がる。
            rect = rect_of(box) if box is not None else None
            if rect is not None:
                inset = upx(FRAME_INSET)
                try:
                    button.pos_hint = {}
                    button.x = rect[0] + rect[2] - button.width - inset
                    button.y = rect[1] + rect[3] - button.height - inset
                except Exception:
                    ctx.log_exc("text expand: could not place the button in the frame")
                    return
                clamp(button)
                paint_icon(button)
                remember("frame", rect)
                return
        else:
            # 場所から探すほうを先に見る。
            # 立ち絵の画像（`source`）で探すと、
            # 会話に入った瞬間に相手の絵を持つ別のウィジェットへ乗り換えてボタンが飛ぶ。
            # 枠の右隣に並ぶ入れ物＝キャラの欄は、会話中も同じ場所に居座る。
            anchor, how = (side_panel_of(hud, box) if box is not None else None), "panel"
            if anchor is None:
                anchor, how = portrait_of(hud), "portrait"
            rect = rect_of(anchor) if anchor is not None else None
            if rect is not None:
                try:
                    button.pos_hint = {}          # 位置はこちらが入れる
                    button.x = rect[0]
                    button.y = rect[1] + rect[3] + upx(PORTRAIT_GAP)
                except Exception:
                    ctx.log_exc("text expand: could not place the button")
                    return
                clamp(button)
                paint_icon(button)
                remember(how, rect)
                return

        # 手がかりが無い画面（会話前・戦闘中など）では隅に落とす。
        if not button.pos_hint:
            button.pos_hint = dict(CORNERS["右上"])
        paint_icon(button)

    def remember(how, rect):
        """置き場所が変わったときだけ記録する（毎フレームは書かない）。

        動いたらここに残る。
        ボタンが飛ぶ不具合はこの行でしか追えない。
        """
        where = (how, tuple(round(value) for value in rect))
        if state.get("anchor") != where:
            state["anchor"] = where
            note("button placed by the {} at {}".format(how, where[1]))

    # -- 絵柄 ----------------------------------------------------------------
    def strokes(expanded):
        """アイコンの線。0〜1 の座標で返す（ボタンの大きさに依らない形）。

        画像ファイルは持たない。
        線で描けば、どの解像度でも滲まず、
        色も透過もこちらで決められる（白／背景なし）。
        絵柄は設定から選べる。

        `expanded` は「今広がっている」＝押すと戻る状態。
        上下を反転して「どちらへ動くか」をそのまま形にする。
        """
        flip = (lambda y: 1.0 - y) if expanded else (lambda y: y)

        def line(*points):
            return [(x, flip(y)) for x, y in points]

        if ICON == "伸縮":
            # 広げる前は外向き（↕）、広がっているときは内向き。
            # 上下反転では同じ形になるので、こちらは向きを明示して描く。
            if expanded:
                return [line((0.50, 0.20), (0.50, 0.80)),
                        line((0.30, 0.38), (0.50, 0.20), (0.70, 0.38)),
                        line((0.30, 0.62), (0.50, 0.80), (0.70, 0.62))]
            return [line((0.50, 0.20), (0.50, 0.80)),
                    line((0.30, 0.62), (0.50, 0.80), (0.70, 0.62)),
                    line((0.30, 0.38), (0.50, 0.20), (0.70, 0.38))]
        # 共有の絵柄（二重山形・山形・矢印・枠）はローダが持つ。
        return ui.icon_strokes(ICON, expanded)

    def paint_icon(button):
        """ボタンにアイコンを描き直す。変わったときだけ（毎フレーム描かない）。

        本文は1文字ずつ増えるので塗り直しは何十回も来る。
        同じ絵なら引き直さない判定はローダ側（`ui.paint_icon`）に集約してある（`116_`
        / `122_` と共有）。
        """
        if ICON == AS_TEXT:
            return
        ui.paint_icon(button, strokes(state["expanded"]), attr=ICON_ATTR,
                      key=(ICON, bool(state["expanded"])),
                      width=ICON_WIDTH, alpha=ICON_ALPHA,
                      log_exc=lambda msg: ctx.log_exc("text expand: " + msg))

    def make_button(hud, label):
        """ボタンを1枚作る（背景消し・フォント写し・大きさはローダの作法）。"""
        button = ui.make_icon_button(
            text=button_text(), size=BUTTON_SIZE, square=(ICON != AS_TEXT),
            font_name=frames.text_of(label, "font_name"),
            pos_hint=(dict(CORNERS.get(BUTTON_CORNER, CORNERS["右上"]))
                      if BUTTON_CORNER not in PLACEMENTS else {}))
        if button is None:
            warn_once("button", "kivy Button unavailable; no toggle will be shown")
        return button

    def host_of(hud):
        """ボタンを載せる相手。HUD 自身の子の並びは変えない。

        規則は `ui.overlay_host` に集約してある（`116_` も同じものが要るので、
        同じ発見を2箇所に書かない。TECH.md §6.1）。
        要点は2つ:

        * 足すのは HUD 直下ではなく、その中の `FloatLayout`。
          HUD の子を増やすと「画面の最初の子」を取る側から見える相手が変わり、
          アイテムの移動・装備が効かなくなる（VERIFICATION_LOG.md §2.33）
        * 選ぶのはいちばん古い子。
          先頭（＝いちばん新しい子）を採ると、
          ゲームの一時的な窓や**他の MOD が置いたウィジェット**を掴みうる
        """
        return ui.overlay_host(hud)

    def ensure_button(hud):
        """ボタンを1枚だけ足す。既にあれば押下先だけ今の注入へ付け替える。"""
        label = label_of(hud)
        if label is None:
            return
        host = host_of(hud)
        button = frames.attr(hud, BUTTON_ATTR)
        if button in (None, frames.MISSING):
            button = make_button(hud, label)
            if button is None:
                return
            setattr(hud, BUTTON_ATTR, button)
        parent = frames.attr(button, "parent")
        if parent is not host:
            # 置き場所が違う（初回、画面の作り直し、
            # 古い版が HUD 直下に足した後の注入し直し）。
            # 前の親から外してから足す。
            if parent not in (None, frames.MISSING):
                try:
                    parent.remove_widget(button)
                except Exception:
                    ctx.log_exc("text expand: could not detach the toggle button")
            try:
                host.add_widget(button)         # 既定は先頭挿入＝一番上に描かれる
            except Exception:
                ctx.log_exc("text expand: could not add the toggle button")
                return
            note("button added to {} at {}".format(type(host).__name__, BUTTON_CORNER))

        # 押下先の付け替え。
        # 注入し直したとき、古い注入の切り替えを呼び続けないため。
        previous = frames.attr(button, CALLBACK_ATTR)
        if previous is not frames.MISSING and previous is not None:
            try:
                button.unbind(on_release=previous)
            except Exception:
                pass
        def callback(_instance=None, _hud=hud):
            return guarded(lambda: toggle(_hud))

        try:
            button.bind(on_release=callback)
            setattr(button, CALLBACK_ATTR, callback)
        except Exception:
            ctx.log_exc("text expand: could not bind the toggle button")
            return
        try:
            button.text = button_text()
        except Exception:
            pass
        place_button(hud, button)

    def guarded(fn):
        """ボタンから呼ばれる処理。ここで投げるとゲームを巻き込む。"""
        try:
            return fn()
        except Exception:
            ctx.log_exc("text expand: toggle failed")
            return None

    def upkeep(hud):
        """塗り直しのたびに、ボタンが在ることと、広げたままであることを保つ。

        枠を先に見るのは、
        注入し直した直後の1回でボタンの文字（拡張／戻す）を今の姿に合わせるため（`adopt`）。
        """
        state["hud"] = weakref.ref(hud)
        watch_window()
        apply_state(hud)
        ensure_button(hud)

    # -- フック --------------------------------------------------------------
    # 本文が変わったとき（1文字ずつ来る）と、選択肢が塗り直されたとき。
    # どちらもゲームに先に仕事をさせてから、
    # こちらの後始末をする（`safe=True` の落とし先が `orig` の結果になる）。
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text", safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        result = orig(self, instance, value, *args, **kwargs)
        upkeep(self)
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_button_texts", safe=True)
    def update_button_texts(orig, self, instance=None, value=None, *args, **kwargs):
        result = orig(self, instance, value, *args, **kwargs)
        upkeep(self)
        return result

    ctx.log("text expand: {} widens the narration frame to x{} / x{} ({} corner); "
            "sizes go to out/{}".format(LABEL_EXPAND, WIDTH_SCALE, HEIGHT_SCALE,
                                        BUTTON_CORNER, LOG_BASENAME))
