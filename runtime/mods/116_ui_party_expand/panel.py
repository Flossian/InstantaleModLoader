# -*- coding: utf-8 -*-
"""ゲームのパーティ欄がどこにあり、1枠がどういう形をしているかだけを知る部品。

この MOD の方針（何行まで増やすか・ボタンをどこに置くか・いつ広げるか）は
入口 `ui_party_expand.py` にある。ここにあるのは「Instantale の HUD の
どこに何があるか」だけで、設定は読まない（必要な値は引数で受け取る）。

実測（`instantale.exe` の定数表、main_023）:

    bottom_info_layout          size_hint=(0.2, 0.88) pos_hint={center_x:0.88, y:0.1}
      party_cells               range(0, 3) ← **3枠しか作られない**
        ClickableFloatLayout    size_hint=(1, 0.33) / member_id を持つ
          StencilFloatLayout
            Image               立ち絵（source）
    update_party_display        party_members（DictProperty）の変化で呼ばれる

4人目以降が出ないのは「枠が3つしか無い」からで、`party_members` の中身が
欠けているわけではない。だから枠を足せば出る。
"""

import sys

from instantale_modloader import frames

# ウィジェット木を何段まで降りるか。パーティ欄は HUD より数段下に居る。
MAX_DEPTH = 14

# 「同じ矩形」と見なす許容。枠線・背景はぴったり重ならず数 px ずれて置かれている
# （`113_ui_text_expand` の `members_of` と同じ理由・同じ値）。
RECT_SLACK = 12.0
RECT_RATIO = 0.03

# 一緒に広げる相手（枠線・背景）の上限。これ以上あるなら別の作り。
MAX_FAMILY = 12

# 枠から上へ何段までたどるか。
MAX_UP = 6

# 位置の縦を決めている `pos_hint` のキー。並べ直すときはこれだけ入れ替える。
VERTICAL_HINTS = ("y", "top", "center_y")

# 枠を複製するときに写す属性。**型では見ない**ので、持っているものだけ写す。
COPY_ATTRS = ("size_hint", "size_hint_x", "size_hint_y", "pos_hint",
              "width", "height", "source", "allow_stretch", "keep_ratio",
              "opacity", "color", "text", "font_name", "font_size",
              "halign", "valign", "text_size", "bold", "padding",
              "background_normal", "background_down", "background_color",
              "line_height", "mipmap", "nocache")

# 押下先とみなす属性名の手がかり（`set_party_member_callback` が入れる先）。
CALLBACK_HINTS = ("callback", "press", "release", "on_touch", "func")

# 黒い板が持っている矩形の描画命令（寸法を合わせ直すのに引く）。
BLOCK_RECT_ATTR = "_instantale_party_block_rect"


# ---------------------------------------------------------------- 寸法
def numbers(value, count):
    """`size_hint` などを素の tuple にする（Kivy の可変列を持ち歩かない）。"""
    try:
        return tuple(value)[:count]
    except Exception:
        return None


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


def overlaps(rect, target):
    if rect is None or target is None:
        return False
    return (rect[0] < target[0] + target[2] and target[0] < rect[0] + rect[2]
            and rect[1] < target[1] + target[3] and target[1] < rect[1] + rect[3])


def is_widget(obj):
    """ウィジェットとして扱えるか。**型では見ない**（GAME.md §1.3）。"""
    for name in ("children", "size", "pos"):
        if frames.attr(obj, name) is frames.MISSING:
            return False
    return True


def children_of(widget):
    children = frames.attr(widget, "children")
    return list(children) if isinstance(children, (list, tuple)) else []


# ---------------------------------------------------------------- 枠を探す
def has_member_id(widget):
    """パーティの枠か。空の枠でも `member_id` は在る（値が None になるだけ）ので、
    **属性の有無**で見る（`frames.MISSING` との比較。値の真偽で見ない）。"""
    return frames.attr(widget, "member_id") is not frames.MISSING


def scan_cells(hud):
    """`party_cells` を名前で引けないビルド用。木を降りて `member_id` を持つ枠を拾う。"""
    found = []

    def descend(widget, depth):
        if depth and has_member_id(widget):
            found.append(widget)
            return                    # 枠の中にもう1つ枠は無い
        if depth >= MAX_DEPTH:
            return
        for child in children_of(widget):
            descend(child, depth + 1)

    descend(hud, 0)
    return found


def cells_of(hud):
    """パーティの枠を、**ゲームが持っている並び順のまま**返す。

    並びをこちらで整え直さないのは、`on_member_label_press(label_index)` が
    この並びの添字で相手を決めているため（GAME.md §2.8）。見た目の上下は
    `capture()` が控える座標から別に決める。
    """
    named = frames.attr(hud, "party_cells")
    if isinstance(named, (list, tuple)):
        cells = [cell for cell in named if is_widget(cell)]
        if cells:
            return cells
    return scan_cells(hud)


def roster_of(hud):
    """`hud.party_cells` そのもの（枠を足すときに追記する相手）。無ければ None。"""
    named = frames.attr(hud, "party_cells")
    return named if isinstance(named, list) else None


def panel_of(cells):
    """枠を載せている入れ物（`bottom_info_layout`）。"""
    if not cells:
        return None
    parent = frames.attr(cells[0], "parent")
    return parent if parent not in (None, frames.MISSING) else None


def family_of(hud, panel):
    """一緒に伸ばす相手（＝**同じ矩形を占めている仲間**）。

    入れ物だけ伸ばすと、枠線や背景は元の大きさのまま取り残される。枠線は
    `add_border(widget)` がウィジェットの `pos` / `size` に束ねて描くので、
    束ねられている相手が別のウィジェットだと付いてこない（`113_` が実機で
    踏んだのと同じ）。クラス名では決めつけず、**今そこに同じ大きさで置かれて
    いるもの**を仲間とみなす。
    """
    target = rect_of(panel)
    found, seen = [panel], {id(panel)}
    if target is None:
        return found
    node = panel
    for _step in range(MAX_UP):
        parent = frames.attr(node, "parent")
        if parent in (None, frames.MISSING):
            break
        for child in children_of(parent):
            if id(child) in seen or len(found) >= MAX_FAMILY:
                continue
            if same_rect(rect_of(child), target):
                seen.add(id(child))
                found.append(child)
        if parent is hud:
            break
        if not same_rect(rect_of(parent), target) or id(parent) in seen:
            break
        seen.add(id(parent))
        found.append(parent)
        node = parent
    return found


# ---------------------------------------------------------------- 名簿
def member_ids(hud):
    """画面に出したい相手の id を、ゲームが並べている順のまま。"""
    members = frames.attr(hud, "party_members")
    if isinstance(members, dict):
        return list(members)
    if isinstance(members, (list, tuple)):
        return list(members)
    return []


def member_data(hud, member_id):
    members = frames.attr(hud, "party_members")
    if isinstance(members, dict):
        return members.get(member_id)
    return None


def data_items(data):
    """`party_members` の値から `(キー, 値)` を取り出す。dict でも実体でも読む。"""
    if isinstance(data, dict):
        try:
            return list(data.items())
        except Exception:
            return []
    try:
        return list(vars(data).items())
    except Exception:
        return []


def shown_ids(cells):
    ids = []
    for cell in cells:
        value = frames.attr(cell, "member_id", None)
        if value not in (None, frames.MISSING):
            ids.append(value)
    return ids


# ---------------------------------------------------------------- 控え
def snapshot(widget):
    hint = frames.attr(widget, "pos_hint")
    return {
        "widget": widget,
        "size_hint": numbers(frames.attr(widget, "size_hint"), 2),
        "size": numbers(frames.attr(widget, "size"), 2),
        "pos": numbers(frames.attr(widget, "pos"), 2),
        "pos_hint": dict(hint) if isinstance(hint, dict) else {},
    }


def capture(hud, panel, cells):
    """こちらが触る前の寸法一式。**最初に見たとき**の値だけを控える。

    控え先はウィジェット自身（入口の `DESIGN_ATTR`）。MOD 側の変数に持つと、
    注入し直したときに「広げた後の寸法」を設計値として控えてしまい、押すたびに
    帯が育つ（`112_` が `line_height` で踏んだ罠）。
    """
    family = []
    for widget in family_of(hud, panel):
        shot = snapshot(widget)
        if shot["size"]:
            family.append(shot)
    if not family:
        return None
    rows = [snapshot(cell) for cell in cells]
    if not rows:
        return None
    design = {"family": family, "cells": rows, "rows": len(rows),
              "size": family[0]["size"], "size_hint": family[0]["size_hint"],
              "pos": family[0]["pos"], "pos_hint": family[0]["pos_hint"]}
    # 見た目の上下（上が先）。`party_cells` の並びとは別に持つ。
    design["order"] = sorted(
        range(len(rows)),
        key=lambda index: -(rows[index]["pos"][1] if rows[index]["pos"] else 0.0))
    return design


def describe(design):
    """控えたものを1行で。**枠の足し方を直すときの唯一の資料**になる。"""
    return "panel {} pos={} hint={} / {} cell(s) {}".format(
        design["size"], design["pos"], design["size_hint"], design["rows"],
        " ".join("{}@{}".format(cell["size"], cell["pos"]) for cell in design["cells"]))


# ---------------------------------------------------------------- 伸ばす
def hint_x_of(shot):
    hint = shot["size_hint"]
    try:
        return hint[0]
    except (TypeError, IndexError):
        return None


def grow(shot, delta):
    """仲間を1つ、`delta` px だけ縦に伸ばす。**位置は入れない**。

    Kivy の `y` は下端なので、`y` を据え置いて `height` だけ増やすと
    **下端が動かず上へ伸びる**。帯の下には画面の縁しか無く、上に在るのは
    背景画像なので、下端が動かないことがそのまま「他をずらさない」になる。
    横には広げないので `size_hint_x` はゲームの決め方に預けたまま。

    倍率ではなく**足し算**にしてある。枠線は帯より数 px 大きいことがあり、
    倍率で配ると差まで倍になって帯と枠線がずれる。増える高さは行の送り幅の
    整数倍なので、全員に同じ px を足すのが正しい。
    """
    widget = shot["widget"]
    widget.size_hint = (hint_x_of(shot), None)
    widget.height = float(shot["size"][1]) + delta


def restore(shot, move):
    widget = shot["widget"]
    widget.size_hint = shot["size_hint"] or (None, None)
    if shot["size"]:
        widget.height = shot["size"][1]
        if shot["size_hint"] is None or shot["size_hint"][0] is None:
            widget.width = shot["size"][0]
    if isinstance(shot["pos_hint"], dict) and shot["pos_hint"]:
        widget.pos_hint = dict(shot["pos_hint"])
    elif move and shot["pos"]:
        widget.x, widget.y = shot["pos"]


def row_pitch(design):
    """1行ぶんの送り幅（隣り合う枠の y の差）。枠が1つなら枠の高さ。

    枠の高さそのものではなく**送り幅**を使う。枠と枠の間に余白があるビルドでも
    足した枠が同じ間隔で並ぶ。
    """
    order, cells = design["order"], design["cells"]
    if len(order) >= 2:
        top = cells[order[0]]["pos"]
        second = cells[order[1]]["pos"]
        if top and second:
            pitch = abs(float(top[1]) - float(second[1]))
            if pitch > 0.5:
                return pitch
    return float(cells[order[0]]["size"][1])


def pin(shot, widget, x, y):
    """`widget` を、実測どおりの大きさで座標に**釘付け**する。

    比率（`size_hint` / `pos_hint`）で置き直さない。0.33 のような丸めた比率を
    伸びた帯に当て直すと、**元から在る枠が数 px 動く**（実機で確認、2026-08-03）。
    親が伸びても比率で寄せ直されないよう、`size_hint` も `pos_hint` も外す。
    """
    widget.size_hint = (None, None)
    widget.pos_hint = {}
    if shot["size"]:
        widget.width, widget.height = shot["size"]
    widget.x, widget.y = x, y


# ---------------------------------------------------------------- 枠を複製する
def clone(widget, log_exc):
    """枠を1つ複製する。**同じクラスを実行時に引いて**作る（型を名指ししない）。

    ゲームの枠は `ClickableFloatLayout > StencilFloatLayout > Image` の入れ子で、
    どれもこちらからは名前しか分からない。`type(widget)()` なら、そのビルドが
    持っているクラスがそのまま使われる。中身は持っている属性だけ写す。
    """
    try:
        fresh = type(widget)()
    except Exception:
        log_exc("party expand: could not construct a copy of {}".format(
            type(widget).__name__))
        return None
    for name in COPY_ATTRS:
        value = frames.attr(widget, name)
        if value is frames.MISSING:
            continue
        try:
            setattr(fresh, name, dict(value) if isinstance(value, dict) else value)
        except Exception:
            pass          # 読めるが書けない属性（property）はそのままでよい
    # 子は逆順に足す。Kivy の `children` は足した順の逆なので、そのまま
    # `add_widget` すると重なりが裏返る。
    for child in reversed(children_of(widget)):
        copy = clone(child, log_exc)
        if copy is None:
            continue
        try:
            fresh.add_widget(copy)
        except Exception:
            log_exc("party expand: could not add a copied child")
    return fresh


def add_border(widget, log_exc):
    """複製した枠に枠線を引く。**ゲーム自身の描き方を借りる**。

    枠線はウィジェットの canvas に描かれていて、`type(cell)()` で作った複製には
    付いてこない（実機で「足した枠だけ枠線が無い」。2026-08-03）。線を自分で
    引くより、`scripts.hud.new_hud` が持っている `add_border(widget)` を呼ぶ
    ほうが正しい ― 色も太さも、寸法が変わったときの追従（`update_border`）も
    ゲームのものになる。

    引けないビルドのためだけに、白い矩形を1本引く予備を持つ。
    """
    module = sys.modules.get("scripts.hud.new_hud")
    for name in ("add_border", "add_border_before"):
        painter = getattr(module, name, None) if module is not None else None
        if not callable(painter):
            continue
        try:
            painter(widget)
            return name
        except Exception:
            log_exc("party expand: the game's {} failed".format(name))
    return draw_border(widget, log_exc)


def draw_border(widget, log_exc):
    """予備の枠線。白い矩形を1本、寸法に追従させて引く。"""
    try:
        from kivy.graphics import Color, Line
    except Exception:
        return None       # 線が引けない環境（オフライン検証）
    try:
        group = widget.canvas.after
        group.add(Color(1, 1, 1, 0.6))
        line = Line(rectangle=(widget.x, widget.y, widget.width, widget.height),
                    width=1.0)
        group.add(line)

        def follow(*_args):
            line.rectangle = (widget.x, widget.y, widget.width, widget.height)

        widget.bind(pos=follow, size=follow)
        return "own"
    except Exception:
        log_exc("party expand: could not draw a border on a copied cell")
        return None


def sync_geometry(template, copy, fill, log_exc):
    """複製の中身を、見本の枠との**相対位置・大きさ**でそのまま合わせる。

    複製した時点では属性しか写っていないので、絵の大きさが見本と食い違うことが
    ある（実機では立ち絵が枠の左へはみ出した。2026-08-03。ゲームの枠は
    `StencilFloatLayout` がはみ出しを切っているが、複製ではそれが効いていない）。

    そこで**見た目そのもの**を合わせる。見本の各ノードの矩形を測り、複製の同じ
    位置のノードへ「枠の左下からのずれ」として入れ直す。比率で置き直さないのは
    `pin` と同じ理由。`fill` を立てると、絵は縦横比を無視して枠を埋める
    （ゲームは切り取って埋めているので、そちらに寄せたいとき）。
    """
    base, mine = rect_of(template), rect_of(copy)
    if base is None or mine is None:
        return

    def walk(src, dst, depth):
        for src_child, dst_child in zip(children_of(src), children_of(dst)):
            rect = rect_of(src_child)
            if rect is not None:
                try:
                    dst_child.size_hint = (None, None)
                    dst_child.pos_hint = {}
                    dst_child.width, dst_child.height = rect[2], rect[3]
                    dst_child.x = mine[0] + (rect[0] - base[0])
                    dst_child.y = mine[1] + (rect[1] - base[1])
                    if fill and frames.attr(dst_child, "keep_ratio") is not frames.MISSING:
                        dst_child.keep_ratio = False
                except Exception:
                    log_exc("party expand: could not align a copied cell's contents")
            if depth < MAX_DEPTH:
                walk(src_child, dst_child, depth + 1)

    walk(template, copy, 0)


def path_of(root, widget):
    """`root` から `widget` までの添字の道。複製の同じ場所を指すのに使う。"""
    path = []
    node = widget
    for _step in range(MAX_DEPTH):
        if node is root:
            return tuple(reversed(path))
        parent = frames.attr(node, "parent")
        if parent in (None, frames.MISSING):
            return None
        siblings = children_of(parent)
        if node not in siblings:
            return None
        path.append(siblings.index(node))
        node = parent
    return None


def at_path(root, path):
    node = root
    for index in path:
        children = children_of(node)
        if index >= len(children):
            return None
        node = children[index]
    return node


def learn_fields(cell, data):
    """**埋まっている枠**と、その相手のデータを突き合わせて「どこに何を入れるか」を知る。

    枠の中の何が名前で何が絵なのかは、こちらからは分からない。分かるのは
    「今そこに出ている文字列」と「`party_members` に入っている文字列」なので、
    **一致したものを対応とみなす**。ゲームの埋め方をこちらで発明しない。

    返すのは `(枠の中の道, 属性名, データのキー)` の一覧。
    """
    pairs = [(key, value) for key, value in data_items(data)
             if isinstance(value, str) and value]
    if not pairs:
        return []
    fields, seen = [], set()

    def descend(widget, depth):
        for name in ("source", "text"):
            value = frames.attr(widget, name)
            if not isinstance(value, str) or not value:
                continue
            for key, wanted in pairs:
                if wanted != value:
                    continue
                path = path_of(cell, widget)
                if path is None or (path, name) in seen:
                    continue
                seen.add((path, name))
                fields.append((path, name, key))
        if depth >= MAX_DEPTH:
            return
        for child in children_of(widget):
            descend(child, depth + 1)

    descend(cell, 0)
    return fields


def fill(cell, data, fields, log_exc):
    """覚えた対応どおりに、複製した枠へ相手の中身を入れる。"""
    values = dict(data_items(data))
    for path, name, key in fields:
        if key not in values:
            continue
        target = at_path(cell, path)
        if target is None:
            continue
        try:
            setattr(target, name, values[key])
        except Exception:
            log_exc("party expand: could not set {} on a copied cell".format(name))


def callback_names(cell):
    """その枠が押下先を持っている属性の名前（`set_party_member_callback` の入れ先）。

    **まだ何も入っていない（None の）枠でも名前は挙げる。** `setup_callbacks` が
    走る前の枠を見本にすることがあり、「今 callable か」で絞ると、そのときだけ
    押せない複製ができる。名前で絞っているので、関係ない属性は掴まない。
    """
    try:
        items = list(vars(cell).items())
    except Exception:
        return []
    names = []
    for name, value in items:
        if not (callable(value) or value is None):
            continue
        if any(hint in name.lower() for hint in CALLBACK_HINTS):
            names.append(name)
    return names


# ---------------------------------------------------------------- 隠す相手
def coverable(host, gained, keep, window, max_area):
    """伸ばした先に**重なってしまう押せるもの**を挙げる。

    帯を上へ伸ばすと、その上に置かれていたボタン（`113_ui_text_expand` の
    切り替えボタンなど）が帯の裏に隠れる。見えないまま押せる状態で残ると、
    パーティの枠を押したつもりが別の機能に届く。だから隠して無効にする。

    見るのは **`host`（HUD の直下の入れ物）の直接の子だけ**。木を降りて
    「`disabled` を持つもの」を全部拾ってはいけない ― ゲームの選択肢ボタンまで
    掴んでしまい、**戻すときに古い `disabled` を書き戻して選択肢が押せなくなる**
    （実機で踏んだ。2026-08-03。ゲームは応答待ちの間だけ選択肢を無効にするので、
    その瞬間に控えると「無効」を控えることになる）。

    MOD が足すボタンはこの入れ物の直接の子になる（こちらも `113_` も
    `get_current_screen_root` を壊さないためそこへ足す）。ゲームの選択肢は
    もっと深い入れ物の中に居るので、この網には入らない。`keep` は触らない
    相手（帯・枠・こちらのボタン・ゲームのボタン列）の id。
    """
    found = []
    limit = (window[0] * window[1] * max_area) if window[0] and window[1] else 0.0
    for widget in children_of(host):
        if id(widget) in keep:
            continue                  # 帯・枠・自分のもの・ゲームのボタン列
        if frames.attr(widget, BLOCK_RECT_ATTR, None) is not None:
            continue                  # こちらが敷いた黒い板（隠す相手ではない）
        if frames.attr(widget, "disabled") is frames.MISSING:
            continue                  # 押せないもの（絵・入れ物）は隠さない
        rect = rect_of(widget)
        if rect is None or rect[2] <= 0 or rect[3] <= 0:
            continue
        if limit and rect[2] * rect[3] > limit:
            continue                  # ボタンではなく入れ物
        if overlaps(rect, gained):
            found.append(widget)
    return found


def make_blocker(log_exc):
    """帯が新しく覆った場所を塞ぐ**黒い板**。ゲームのウィジェットには触らない。

    足した枠は背景を持たない（絵と枠線だけ）ので、伸ばした帯の下にある
    ゲームの選択肢ボタンが**透けて見え、押せてしまう**（実機で確認、2026-08-03。
    「会話する」の文字が枠に重なって出た）。

    だからといってゲームのボタンを無効にしてはいけない ― ゲームは応答待ちの間だけ
    選択肢を無効にするので、その値を控えて書き戻すと選択肢が押せなくなる（同日、
    実際にそうなった）。代わりに**こちらが黒い板を1枚敷く**。

    * 見た目 ― 板が不透明なので、下の文字は見えない
    * 当たり判定 ― Kivy は「無効なウィジェットに触れたら `True` を返す」ので、
      `disabled=True` の板が触れられた時点でそこで止まり、下へ抜けない
    * 戻すとき ― 板を外すだけ。**ゲームの状態は1つも書き換えていない**

    板は帯の子にして、**兄弟の一番後ろ**へ置く（`children` の末尾＝描画は一番下・
    触りの判定は一番後）。こうすると枠より下・ゲームのボタンより上になり、
    枠の押下は今までどおり枠に届く。
    """
    try:
        from kivy.uix.widget import Widget
        from kivy.graphics import Color, Rectangle
    except Exception:
        return None       # 板が作れない環境（オフライン検証）
    try:
        blocker = Widget(size_hint=(None, None))
        blocker.disabled = True
        group = blocker.canvas.before
        group.add(Color(0, 0, 0, 1))
        rect = Rectangle(pos=(0, 0), size=(0, 0))
        group.add(rect)
        setattr(blocker, BLOCK_RECT_ATTR, rect)
        return blocker
    except Exception:
        log_exc("party expand: could not make the blocker")
        return None


def behind_index(host, box):
    """`host` の子のうち、**`box` を含む子のすぐ後ろ**の位置。見つからなければ None。

    Kivy は `children` を逆順に描く（後ろの子ほど先に描かれる＝下になる）ので、
    ここへ入れたものは帯より下に描かれ、触りの判定でも帯より後になる。
    「帯の下・ゲームのボタンの上」はこの1点しかない。
    """
    children = children_of(host)
    node = box
    for _step in range(MAX_UP):
        if node in children:
            return children.index(node) + 1
        parent = frames.attr(node, "parent")
        if parent in (None, frames.MISSING):
            return None
        node = parent
    return None


def place_blocker(blocker, rect, log_exc):
    """黒い板を、帯が新しく覆った矩形にぴったり合わせる。"""
    try:
        blocker.x, blocker.y = rect[0], rect[1]
        blocker.width, blocker.height = rect[2], rect[3]
    except Exception:
        log_exc("party expand: could not place the blocker")
        return
    instruction = frames.attr(blocker, BLOCK_RECT_ATTR, None)
    if instruction is None:
        return
    try:
        instruction.pos = (rect[0], rect[1])
        instruction.size = (rect[2], rect[3])
    except Exception:
        log_exc("party expand: could not resize the blocker")


def game_buttons(hud):
    """ゲーム自身が持っているボタンの id。**絶対に触らない相手**。

    名前で引くのは、ここが「見つけたら触る」ではなく「見つけたら**避ける**」側
    だから。名前が変わって引けなくなっても、危ない側へは倒れない
    （`coverable` が直接の子しか見ないので、そもそも網に入らない）。
    """
    kept = set()
    for name in ("buttons", "right_buttons", "icon_buttons", "party_cells"):
        value = frames.attr(hud, name)
        if isinstance(value, (list, tuple)):
            kept.update(id(item) for item in value)
    return kept
