# -*- coding: utf-8 -*-
"""画面左下のゲームのアイコンの列に、MOD の機能の入口を足す窓口。TECH.md §3.3.15。

    hudicons.add(ctx, key, on_press, strokes=None, images=None, order=0, write=None, label=None,
                 visible=None)
    hudicons.notify(key, on=True, playthrough=None)
    hudicons.withdraw(key)
    hudicons.settle()          # ローダが注入の終わりに呼ぶ
    hudicons.remove_all()      # ローダが外すときに呼ぶ

ゲームは能力値の欄の下端に、持ち物・設定・キャラ情報・クラフトの4つのアイコンを並べている
（`InstanTaleHUD.icon_button_layout`。FloatLayout で、位置は比率、大きさは固定の px。GAME.md §2.34）。
MOD が足した機能は、その4つの右に置く **MOD 用のボタン1つ** にまとめる。
押すとボタンの上に一覧が開き、行（MOD の絵と名前）を選ぶと、その MOD の `on_press(app)` を呼ぶ。
登録が1つでも一覧を出す（2026-10-13 に決めた）。

MOD ごとにアイコンを並べていた形（v1.15.1）は、MOD が増えるほど列が詰まり、1280×720 では
6つ目で箱が重なる計算だった。ボタン1つなら、MOD の数によらず列は5つで変わらない。

並べ方:

- 列の左の余白（ゲームの一番左のアイコンの x）を、右にも同じだけ取る
- その間に、ゲームの4つ・MOD 用のボタンの順で、絵の見えている部分どうしの間が等しくなるよう置く
- 大きさはゲームのアイコンに揃える
- 比率は列の幅から毎回出し直す（大きさが px の固定なので、窓の大きさが変わると比率も変わる）
- 登録が1つも無くなったら、MOD 用のボタンを外し、ゲームの4つを元の位置へ戻す

一覧の行の並びは `order`、同じなら `key` の順。行の名前は `label`（無ければ `key`）。
`images` は画像のパスで、1枚目（普段の絵）を行の頭に小さく出す。`strokes` は v1.15.1 の形の名残で、
行には出さない（絵の無い行は名前だけ）。

`visible(app)` を渡すと、ボタンを並べるときと一覧を開くときにその場で聞き、偽なら行を出さない
（世界ごとに有効にする MOD 向け）。読み込みや注入の瞬間に出し入れを決める形は、世界をまだ読み終えていない
瞬間や注入の世代の入れ替わりに左右され、行が次の操作まで出なかった（2026-10-13 の実機）。

`notify(key)` は「見てほしいことがある」印。MOD 用のボタンの右上と、その行に赤い点を出す。
行を押すと消える（MOD が `notify(key, False)` で消してもよい）。
`playthrough`（周回の鍵。`state.playthrough_key`）を渡すと、その周回を遊んでいる間だけ点を出す
（世界 A の知らせを世界 B で出さない。背景の仕事が LLM を待つ間に別のセーブを読んでも、積んだ周回へ付く）。
省くとどの周回でも出す。行を押すと、いまの周回の印と周回を問わない印が消える。

登録した MOD を全部切って注入し直すと、その世代では誰も `add` を呼ばない。ローダが注入の終わりに
`settle()` を呼び、用済みの登録を外して MOD 用のボタンを片付ける。ローダを外すときは `remove_all()`。

一覧は、枠の外を押すか、MOD 用のボタンをもう一度押すか、ESC で閉じる（ESC は下のゲームへ渡さない）。

置き場は `sys` の属性（`_instantale_hud_icons`）。注入し直しをまたいで残り、同じ `key` は差し替える
（差し替えても知らせの印は残る）。
"""
import os
import sys

from . import frames, log_exc, panels, ui
from . import state as _state_keys

STORE_ATTR = "_instantale_hud_icons"
#: HUD に足したボタンの控え（`{key: Button}`）。
HUD_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icons"
#: ゲームのボタンの元の `pos_hint`。
HOME_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_home"
#: 列の大きさの変化に結んだ手。
BIND_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_bound"
CALLBACK_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_press"
#: 窓（`kivy.core.window.Window`）に結んだマウスの位置の見張りと、ボタンごとの「乗っているか」。
HOVER_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_hover"
HOVERING_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_hovering"
LAYOUT_NAME = "icon_button_layout"
BUTTONS_NAME = "icon_buttons"
TARGETS = ("scripts.hud.new_hud:InstanTaleHUD.update_button_texts",
           "scripts.hud.new_hud:InstanTaleHUD.update_status_texts")
#: 隣どうしの間がこれより狭くなったら1回だけ記録する（px）。
TIGHT_GAP = 4.0

#: MOD 用のボタンの控えの名前（`HUD_ATTR` の中の鍵。MOD の `key` とは被らない形）。
ROOT_KEY = "__mods__"
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
#: MOD 用のボタンの絵（普段・乗せた時・押した時）。`tools\draw_hud_icons.py` が描き出す。
ROOT_IMAGES = tuple(os.path.join(ASSETS_DIR, "hud_mods{}.png".format(suffix))
                    for suffix in ("", "_hover", "_click"))
#: 一覧の見た目（`338_` の窓に揃える。px は `ui.upx` で縮尺を掛ける）。
LIST_BG = (0.06, 0.06, 0.08, 1)        # 下の字が 4% 透けて読めたので不透明に（2026-10-13 の実機）
LIST_PAD = 8
LIST_ROW_HEIGHT = 40
LIST_ICON = 28
LIST_GAP = 6                 # ボタンの上端と一覧の下端の間
LIST_MIN_WIDTH = 200
ROW_HOVER = (1, 1, 1, 0.12)
DOT_COLOR = (0.87, 0.25, 0.2, 1)
DOT_SIZE = 9
#: 周回を問わない知らせの印（`notify` で `playthrough` を省いたとき）。
ANY_PLAYTHROUGH = "*"
#: 一覧を閉じるキー（ESC）。
ESCAPE_KEY = 27
#: 一覧を開いたときの音（ゲームのアイコンで窓を開くときと同じ）。
OPEN_SOUND = "Assets/sounds/sounds/ui/常時/アイコン_キャラシート.wav"
LIST_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_list"
DOT_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_dot"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict) or not isinstance(store.get("icons"), dict):
        store = {"icons": {}, "generation": None, "write": None, "said": set(), "attention": {},
                 "list": None}
        setattr(sys, STORE_ATTR, store)
    attention = store.get("attention")
    if not isinstance(attention, dict):
        # 前の版の形（印の付いた key の集合。周回を問わない印）を移す
        store["attention"] = {str(key): {ANY_PLAYTHROUGH} for key in (attention or ())}
    store.setdefault("list", None)
    return store


def _log(line):
    write = _store().get("write")
    if write:
        write(line)


def _once(key, line):
    said = _store()["said"]
    if key not in said:
        said.add(key)
        _log(line)


def add(ctx, key, on_press, strokes=None, images=None, order=0, write=None, label=None,
        visible=None):
    """一覧に機能を1つ足す（同じ `key` は差し替え）。HUD がもう在れば次のフレームで並べ直す。"""
    if not callable(on_press):
        raise TypeError("hudicons.add() needs a callable on_press, got {!r}".format(type(on_press)))
    if visible is not None and not callable(visible):
        raise TypeError("hudicons.add() needs a callable visible, got {!r}".format(type(visible)))
    store = _store()
    store["icons"][str(key)] = {"key": str(key), "on_press": on_press, "ctx": ctx,
                                "strokes": list(strokes or []), "images": images,
                                "order": order, "label": str(label) if label else str(key),
                                "visible": visible}
    if write is not None:
        store["write"] = write
    install(ctx)
    schedule = ui.scheduler(ctx, "hud icons")

    def later():
        hud = ui.find_hud(ui.find_app())
        if hud is not None:
            arrange(hud)

    schedule(later)


def withdraw(key):
    """一覧から外す。登録が無くなれば MOD 用のボタンも外れる。"""
    store = _store()
    store["icons"].pop(str(key), None)
    store["attention"].pop(str(key), None)
    close_list()
    hud = ui.find_hud(ui.find_app())
    if hud is not None:
        try:
            arrange(hud)
        except Exception:
            log_exc("hud icons: cannot rearrange after withdraw")


def keys():
    return sorted(_store()["icons"])


def notify(key, on=True, playthrough=None):
    """`key` の行に「見てほしいことがある」印を付ける（`on=False` で外す）。登録の前でもよい。

    `playthrough` を渡すとその周回だけの印（外すときはその周回の印と、周回を問わない印を外す）。
    省くと、付けるときはどの周回でも出る印、外すときはその `key` の印を全部外す。
    背景のスレッド（LLM の答えを待つ仕事）から呼んでもよい。点を描くのは表のスレッドへ回す。
    """
    store = _store()
    key = str(key)
    marks = store["attention"].setdefault(key, set())
    if on:
        marks.add(str(playthrough) if playthrough else ANY_PLAYTHROUGH)
    elif playthrough:
        marks.discard(str(playthrough))
        marks.discard(ANY_PLAYTHROUGH)
    else:
        marks.clear()
    if not marks:
        store["attention"].pop(key, None)

    def repaint(*_args):
        hud = ui.find_hud(ui.find_app())
        root = (frames.attr(hud, HUD_ATTR, None) or {}).get(ROOT_KEY) if hud is not None else None
        if root is not None:
            _paint_dot(root)

    try:
        from kivy.clock import Clock
    except Exception:
        return                                  # ゲームの外（検査）
    try:
        Clock.schedule_once(repaint, 0)
    except Exception:
        log_exc("hud icons: cannot schedule the notice dot")


def _shown(entry, app):
    visible = entry.get("visible")
    if visible is None:
        return True
    try:
        return bool(visible(app))
    except Exception:
        log_exc("hud icons: the visible check of {!r} failed".format(entry["key"]))
        return False


def menu_entries(app=None):
    """一覧に並べる登録（生きていて、`visible(app)` が真のものだけ。`order`・`key` の順）。"""
    return sorted((entry for entry in _store()["icons"].values() if _alive(entry) and _shown(entry, app)),
                  key=lambda entry: (entry.get("order", 0), entry["key"]))


def current_playthrough(app):
    """いまの周回の鍵。読めなければ None。"""
    if app is None:
        return None
    try:
        key = _state_keys.playthrough_key(app)
    except Exception:
        return None
    return None if key == _state_keys.UNKNOWN_WORLD else key


def marked(key, app=None):
    """`key` の行に、いまの周回で見せる印があるか。"""
    marks = _store()["attention"].get(str(key)) or ()
    if ANY_PLAYTHROUGH in marks:
        return True
    current = current_playthrough(app)
    return current is not None and current in marks


def has_attention(app=None):
    """一覧に並ぶ登録のどれかに、いまの周回で見せる知らせの印が付いているか。"""
    return any(marked(entry["key"], app) for entry in menu_entries(app))


def _alive(entry):
    ctx = entry.get("ctx")
    superseded = getattr(ctx, "superseded", None)
    try:
        return not (callable(superseded) and superseded())
    except Exception:
        return True


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _game_style(entry, size, template):
    """ゲーム自身のアイコンと同じ型（HoverableButton）で作る。作れなければ None。

    ゲームの型は普段・乗せた時・押した時の3枚の絵を `normal_image` / `hover_image` / `click_image` で持つ
    （GAME.md §2.34）。`images` が3枚なら、乗せた時の絵もゲームと同じく効く。
    """
    images = tuple(entry.get("images") or ())
    if len(images) < 3 or template is None:
        return None
    try:
        # ゲームの型は `HoverableButton(normal_image, hover_image, click_image, **kwargs)`（リコンの一覧）。
        return type(template)(images[0], images[1], images[2], size_hint=(None, None),
                              size=tuple(size))
    except Exception:
        _once("game-style", "WARN hud icons: cannot build the game's icon button; using a plain one")
        return None


def _make_button(entry, size, template=None):
    images = entry.get("images")
    if images:
        game_like = _game_style(entry, size, template)
        if game_like is not None:
            return game_like
        try:
            from kivy.uix.button import Button
        except Exception:
            return None
        normal, down = (tuple(images) + (None, None))[:3:2] if len(images) >= 3 \
            else (tuple(images) + (None,))[:2]
        button = Button(text="", size_hint=(None, None), size=tuple(size))
        try:
            button.background_normal = normal
            button.background_down = down or normal
            button.border = (0, 0, 0, 0)
        except Exception:
            pass
        return button
    text = "" if entry.get("strokes") else entry["key"][:1]
    return ui.make_icon_button(text=text, size=size[1], square=True)


def _bind_press(button, entry):
    previous = frames.attr(button, CALLBACK_ATTR, None)
    if previous not in (None, frames.MISSING):
        try:
            button.unbind(on_release=previous)
        except Exception:
            pass
    on_press = entry["on_press"]

    def pressed(_instance=None):
        try:
            on_press(ui.find_app())
        except Exception:
            log_exc("hud icons: the press handler of {!r} failed".format(entry["key"]))

    try:
        button.bind(on_release=pressed)
        setattr(button, CALLBACK_ATTR, pressed)
    except Exception:
        log_exc("hud icons: cannot bind {!r}".format(entry["key"]))


def positions(left, count, layout_width, icon_width):
    """`count` 個を等間隔に置く x の比率。左の余白を右にも取る。並べられなければ空。"""
    if count <= 0 or layout_width <= 0:
        return []
    if count == 1:
        return [left]
    right = 1.0 - left - icon_width / layout_width
    if right <= left:
        return []
    pitch = (right - left) / (count - 1)
    return [left + pitch * index for index in range(count)]


#: 見えている部分に数えるドット（不透明で、暗い背景に溶けない明るさ。r+g+b の和）。
INK_ALPHA = 100
INK_LIGHT = 120


def ink_columns(pixels, width, height):
    """RGBA のバイト列から、見えているドットのある列の左端と右端（テクスチャのドット）。無ければ None。"""
    left, right = None, None
    for x in range(width):
        for y in range(height):
            at = (y * width + x) * 4
            r, g, b, a = pixels[at], pixels[at + 1], pixels[at + 2], pixels[at + 3]
            if a > INK_ALPHA and r + g + b > INK_LIGHT:
                left = x if left is None else left
                right = x
                break
    return None if left is None else (left, right + 1)


def _normal_columns(button):
    """普段の絵（`normal_image`）の見えている列 `(左, 右, 絵の幅)`。絵のパスごとに1回だけ数えて覚える。

    いまのテクスチャで数えないのは、乗せた時の絵（白い縁）を数えると、並べ直しがマウスの位置で揺れるため。
    """
    path = frames.attr(button, "normal_image", None)
    if not isinstance(path, str) or not path:
        return None
    cache = _store().setdefault("ink", {})
    if path in cache:
        return cache[path]
    found = None
    try:
        from kivy.core.image import Image as CoreImage
        texture = CoreImage(path).texture
        width, height = int(texture.width), int(texture.height)
        pixels = texture.pixels
        if width > 0 and height > 0 and len(pixels) >= width * height * 4:
            columns = ink_columns(pixels, width, height)
            found = None if columns is None else (columns[0], columns[1], width)
    except Exception:
        found = None
    cache[path] = found
    return found


def optical_span(button):
    """ボタンの中で絵の見えている部分の左右（ボタンの左端からの px）。読めなければ None。

    絵ごとに端のドットの明るさが違う（袋は端が暗く、背景に溶けて細く見える）。
    ボタンの箱どうしを等間隔にしても見た目の間がばらつくので、見えている部分で並べる。
    """
    columns = _normal_columns(button)
    try:
        norm_width = float(button.norm_image_size[0])
        box = float(button.width)
    except Exception:
        return None
    if columns is None or norm_width <= 0:
        return None
    scale = norm_width / columns[2]
    pad = (box - norm_width) / 2.0
    return (pad + columns[0] * scale, pad + columns[1] * scale)


def optical_positions(spans, layout_width, left, box_width):
    """見えている部分どうしの間と、左右の余白が等しくなる x の比率。並べられなければ空。

    `spans` はボタンごとの見えている部分 `(左, 右)`（ボタンの左端からの px）、
    `left` は列の左端から最初の絵の見えている部分までの px（右にも同じだけ取る）。
    """
    count = len(spans)
    if count <= 0 or layout_width <= 0:
        return []
    widths = [right - start for start, right in spans]
    room = layout_width - 2 * left - sum(widths)
    gap = room / (count - 1) if count > 1 else 0.0
    if count > 1 and gap <= 0:
        return []
    xs, cursor = [], left
    for (start, _right), width in zip(spans, widths):
        x = cursor - start
        if x < 0 or x + box_width > layout_width + 0.5:
            return []
        xs.append(x / layout_width)
        cursor += width + gap
    return xs


def _root_entry():
    """MOD 用のボタンの登録（絵が無ければ「M」の字のボタン）。"""
    images = ROOT_IMAGES if all(os.path.isfile(path) for path in ROOT_IMAGES) else None
    if images is None:
        _once("no-root-images", "WARN hud icons: the button images are missing in {}".format(ASSETS_DIR))
    return {"key": "MOD", "on_press": toggle_list, "images": images, "strokes": []}


def arrange(hud):
    """MOD 用のボタンを置き、ゲームの4つと一緒に並べ直す。何度呼んでも同じ形になる。"""
    layout = frames.attr(hud, LAYOUT_NAME, None)
    game = frames.attr(hud, BUTTONS_NAME, None)
    if layout in (None, frames.MISSING) or not isinstance(game, (list, tuple)) or not game:
        _once("no-layout", "WARN hud icons: the game's icon row was not found; no icons added")
        return
    game = list(game)
    for button in game:
        if frames.attr(button, HOME_ATTR, None) in (None, frames.MISSING):
            try:
                setattr(button, HOME_ATTR, dict(button.pos_hint or {}))
            except Exception:
                return
    placed = frames.attr(hud, HUD_ATTR, None)
    if not isinstance(placed, dict):
        placed = {}
        setattr(hud, HUD_ATTR, placed)
    # 置くのは MOD 用のボタン1つだけ（登録が1つでもあれば）。v1.15.1 の形で MOD ごとに置いたボタンが
    # 前の世代から残っていれば、ここで外れる。
    entries = [_root_entry()] if menu_entries(ui.find_app()) else []
    wanted = {ROOT_KEY} if entries else set()
    for key in list(placed):
        if key not in wanted:
            button = placed.pop(key)
            try:
                if button.parent is not None:
                    button.parent.remove_widget(button)
            except Exception:
                log_exc("hud icons: cannot remove {!r}".format(key))
    homes = [frames.attr(button, HOME_ATTR, {}) or {} for button in game]
    if not entries:
        for button, home in zip(game, homes):
            if dict(button.pos_hint or {}) != home:
                button.pos_hint = dict(home)
        return
    size = tuple(frames.attr(game[0], "size", (40, 40)) or (40, 40))
    entry = entries[0]
    button = placed.get(ROOT_KEY)
    if button is None:
        button = _make_button(entry, size, game[0])
        if button is None:
            _once("no-kivy", "WARN hud icons: kivy Button unavailable")
            return
        placed[ROOT_KEY] = button
        _log("hud icons: added the MOD button")
    if button.parent is not layout:
        try:
            if button.parent is not None:
                button.parent.remove_widget(button)
            layout.add_widget(button)
        except Exception:
            log_exc("hud icons: cannot add the MOD button")
            return
    try:
        button.size = size
    except Exception:
        pass
    _bind_press(button, entry)
    order = sorted(zip(game, homes), key=lambda pair: _number(pair[1].get("x"), 0.0))
    left = min(_number(home.get("x"), 0.0) for home in homes)
    y = _number(order[0][1].get("y"), 0.03)
    width = _number(frames.attr(layout, "width", 0.0), 0.0)
    icon_width = _number(size[0], 40.0)
    xs = positions(left, len(game) + len(entries), width, icon_width)
    _watch(layout, hud)     # 幅が決まったら（窓の大きさが変わったら）並べ直す
    if not xs:
        return              # 列の幅がまだ決まっていない（組み立ての途中）。決まったときに `_watch` が呼ぶ
    widgets = [game_button for game_button, _home in order] + [button]
    # 絵の見えている部分が読めれば、見えている部分どうしの間で揃える（読めなければ箱の等間隔のまま）。
    spans = [optical_span(widget) for widget in widgets]
    if len(spans) == len(xs) and all(spans):
        optical = optical_positions(spans, width, left * width + spans[0][0], icon_width)
        if optical:
            xs = optical
    for widget, x in zip(widgets, xs):
        hint = {"x": round(x, 4), "y": y}
        if dict(widget.pos_hint or {}) != hint:
            widget.pos_hint = hint
    if len(xs) > 1 and (xs[1] - xs[0]) * width - icon_width < TIGHT_GAP:
        _once(("tight", int(width)), "WARN hud icons: the row is {:.0f}px wide; the icons "
                                     "are almost touching".format(width))
    _paint_dot(button)
    _follow(button)


def _current(previous):
    """前に結んだ手が、いま読み込まれているこのモジュールのものか（注入し直した後は前の世代の手が残る）。"""
    return previous not in (None, frames.MISSING) and getattr(previous, "__globals__", None) is globals()


def _follow(button):
    """動いたら知らせの点を描き直す（列が並べ直されると、比率の変化で位置が動く）。

    結ぶのは1本だけ。前の世代の手が残っていれば外して結び直す（残すと、注入し直した後も前の世代の描き方で描く）。
    """
    previous = frames.attr(button, BIND_ATTR, None)
    if _current(previous):
        return
    if previous not in (None, frames.MISSING):
        try:
            button.unbind(pos=previous, size=previous)
        except Exception:
            pass

    def moved(*_args):
        _paint_dot(button)

    try:
        button.bind(pos=moved, size=moved)
        setattr(button, BIND_ATTR, moved)
    except Exception:
        pass


def _paint_dot(widget, on=None):
    """右上の知らせの点を描く・消す。`on` を省くと、一覧のどれかに印があるかで決める。"""
    if on is None:
        on = has_attention(ui.find_app())
    group = frames.attr(widget, DOT_ATTR, None)
    try:
        from kivy.graphics import Color, InstructionGroup, Rectangle
    except Exception:
        return                                  # ゲームの外（検査）
    try:
        if group in (None, frames.MISSING):
            group = InstructionGroup()
            widget.canvas.after.add(group)
            setattr(widget, DOT_ATTR, group)
        group.clear()
        if on:
            # 角の無い四角の点（ゲームのドット絵の絵柄に合わせる）
            size = ui.upx(DOT_SIZE)
            group.add(Color(*DOT_COLOR))
            # `right` / `top` は使わない。`pos` の知らせ（`_follow`）の中では、まだ動く前の値が返る
            # （読み込みでボタンが y=0 から 48 へ動いたとき、点が y=0 のときの位置に残った。2026-10-13 の実機）
            right, top = widget.x + widget.width, widget.y + widget.height
            group.add(Rectangle(pos=(right - size - ui.upx(1), top - size - ui.upx(1)),
                                size=(size, size)))
    except Exception:
        log_exc("hud icons: cannot draw the notice dot")


# ------------------------------------------------------------------ 一覧
def close_list():
    """一覧が開いていれば閉じる。"""
    store = _store()
    view = store.get("list")
    store["list"] = None
    if view is None:
        return
    try:
        view.dismiss()
    except Exception:
        log_exc("hud icons: cannot close the list")


def toggle_list(app):
    """MOD 用のボタンを押した。一覧が開いていれば閉じ、閉じていればボタンの上に開く。"""
    if _store().get("list") is not None:
        close_list()
        return
    hud = ui.find_hud(app)
    button = (frames.attr(hud, HUD_ATTR, None) or {}).get(ROOT_KEY) if hud is not None else None
    if button is None:
        return
    try:
        open_list(app, hud, button)
    except Exception:
        close_list()
        log_exc("hud icons: cannot open the list")


def list_size(rows, row_height, gap, pad, widest, icon, min_width, slack):
    """一覧の枠の大きさ `(幅, 高さ)`（px）。`widest` は名前の文字の幅の最大、`icon` は行の絵の幅（無ければ 0）。"""
    width = max(min_width, widest + icon + slack + 2 * pad)
    height = row_height * rows + gap * max(0, rows - 1) + 2 * pad
    return width, height


def list_position(anchor, size, window, margin, lift):
    """一覧の枠の左下 `(x, y)`。`anchor` はボタンの左上（窓の座標）。枠はボタンの真上に出し、窓からはみ出さない。"""
    (bx, by), (width, height), (win_w, win_h) = anchor, size, window
    x = max(margin, min(bx, win_w - width - margin))
    y = min(by + lift, win_h - height - margin)
    return x, max(margin, y)


def open_list(app, hud, button):
    from kivy.core.window import Window
    from kivy.graphics import Color, Rectangle
    from kivy.uix.behaviors import ButtonBehavior
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.floatlayout import FloatLayout
    from kivy.uix.image import Image
    from kivy.uix.label import Label
    from kivy.uix.modalview import ModalView
    from kivy.uix.widget import Widget

    class Row(ButtonBehavior, BoxLayout):
        pass

    store = _store()
    status = frames.attr(hud, "status_label", None)
    font_name = frames.text_of(status, "font_name") if status is not None else None
    font_size = frames.attr(status, "font_size", None) if status is not None else None
    pad, row_height, icon = ui.upx(LIST_PAD), ui.upx(LIST_ROW_HEIGHT), ui.upx(LIST_ICON)
    gap = ui.upx(2)
    entries = menu_entries(app)
    if not entries:
        return
    # 窓全体を覆う透明な面に一覧の枠を置く。枠の外を押したら閉じる（下のゲームの画面には押下を通さない）。
    view = ModalView(size_hint=(1, 1), auto_dismiss=False)
    for name in ("background_color", "overlay_color"):
        try:
            setattr(view, name, (0, 0, 0, 0))
        except Exception:
            pass
    cover = FloatLayout()
    panel = BoxLayout(orientation="vertical", padding=pad, spacing=gap, size_hint=(None, None))
    with panel.canvas.before:
        Color(*LIST_BG)
        panel_bg = Rectangle(pos=panel.pos, size=panel.size)
    panel.bind(pos=lambda _w, value: setattr(panel_bg, "pos", value),
               size=lambda _w, value: setattr(panel_bg, "size", value))
    panels.border(panel)                # ゲームの画面の枠と同じ 2px の灰白（TECH.md §3.3.17）
    rows, widest = [], 0.0
    has_images = any(entry.get("images") for entry in entries)
    for entry in entries:
        row = Row(orientation="horizontal", size_hint_y=None, height=row_height, spacing=ui.upx(10),
                  padding=(ui.upx(6), 0))
        with row.canvas.before:
            shade = Color(1, 1, 1, 0)
            row_bg = Rectangle(pos=row.pos, size=row.size)
        row.bind(pos=lambda _w, value, r=row_bg: setattr(r, "pos", value),
                 size=lambda _w, value, r=row_bg: setattr(r, "size", value))
        images = tuple(entry.get("images") or ())
        if images:
            picture = Image(source=images[0], size_hint=(None, None), size=(icon, icon),
                            pos_hint={"center_y": 0.5})
            try:
                picture.texture.mag_filter = "nearest"      # ドット絵をぼかさない
            except Exception:
                pass
            row.add_widget(picture)
        elif has_images:
            row.add_widget(Widget(size_hint=(None, None), size=(icon, icon)))
        label = Label(text=entry.get("label") or entry["key"], halign="left", valign="middle")
        if isinstance(font_name, str) and font_name:
            label.font_name = font_name
        if isinstance(font_size, (int, float)):
            label.font_size = font_size
        label.bind(size=lambda widget, value: setattr(widget, "text_size", value))
        label.texture_update()
        widest = max(widest, label.texture_size[0])
        row.add_widget(label)
        dot = Widget(size_hint=(None, None), size=(ui.upx(DOT_SIZE), ui.upx(DOT_SIZE)),
                     pos_hint={"center_y": 0.5})
        if marked(entry["key"], app):
            with dot.canvas:
                Color(*DOT_COLOR)
                mark = Rectangle(pos=dot.pos, size=dot.size)
            dot.bind(pos=lambda _w, value, m=mark: setattr(m, "pos", value))
        row.add_widget(dot)

        def chosen(_row, entry=entry):
            close_list()
            notify(entry["key"], False, playthrough=current_playthrough(app) or ANY_PLAYTHROUGH)
            try:
                entry["on_press"](app)
            except Exception:
                log_exc("hud icons: the press handler of {!r} failed".format(entry["key"]))

        row.bind(on_release=chosen)
        rows.append((row, shade))
        panel.add_widget(row)
    panel.size = list_size(len(entries), row_height, gap, pad, widest, icon if has_images else 0,
                           ui.upx(LIST_MIN_WIDTH), ui.upx(60))
    panel.pos = list_position(button.to_window(button.x, button.top), panel.size,
                              (Window.width, Window.height), ui.upx(4), ui.upx(LIST_GAP))
    cover.add_widget(panel)

    def touched(_cover, touch):
        if panel.collide_point(*touch.pos):
            return False                # 行へ渡す
        close_list()
        return True

    cover.bind(on_touch_down=touched)

    def hovered(_window, pos):
        for row, shade in rows:
            try:
                inside = row.collide_point(*row.to_widget(*pos))
            except Exception:
                inside = False
            shade.rgba = ROW_HOVER if inside else (1, 1, 1, 0)

    Window.bind(mouse_pos=hovered)
    view.add_widget(cover)

    def keyed(_window, key, *_args):
        # ESC で閉じ、下のゲームへは渡さない。ModalView 自身は `auto_dismiss=False` なので ESC を見ない
        # （Kivy 2.3.0 の `_handle_keyboard` は `auto_dismiss` のときだけ閉じる）
        if key == ESCAPE_KEY and store.get("list") is view:
            close_list()
            return True
        return False

    def dismissed(*_args):
        Window.unbind(mouse_pos=hovered)
        Window.unbind(on_keyboard=keyed)
        if store.get("list") is view:
            store["list"] = None

    view.bind(on_dismiss=dismissed)
    store["list"] = view
    view.open()
    # 開いた後に結ぶ（後から結んだ手が先に呼ばれる。ゲームのキーの手より先に ESC を受ける）
    Window.bind(on_keyboard=keyed)
    manager = getattr(app, "sound_manager", None)
    play = getattr(manager, "play_sound_from_src", None)
    if callable(play):
        try:
            play(app, OPEN_SOUND)
        except Exception:
            log_exc("hud icons: cannot play the open sound")


def _on_main(fn):
    """表のスレッドで1回呼ぶ（ゲームの外では何もしない）。"""
    try:
        from kivy.clock import Clock
    except Exception:
        return False
    try:
        Clock.schedule_once(lambda _dt: fn(), 0)
        return True
    except Exception:
        log_exc("hud icons: cannot schedule on the main thread")
        return False


def settle():
    """注入の終わりにローダが呼ぶ。用済みの世代の登録を外し、HUD を今の登録に合わせる。外した key を返す。

    登録した MOD を全部切って注入し直した世代では、誰も `add` を呼ばないので、並べ直す包みも立たない。
    ここで外さないと、押しても何も開かない MOD 用のボタンと、右へずれたゲームの4つが残る。
    """
    store = _store()
    gone = sorted(key for key, entry in store["icons"].items() if not _alive(entry))
    for key in gone:
        store["icons"].pop(key, None)
        store["attention"].pop(key, None)
    if not gone:
        return gone
    _log("hud icons: dropped {} (their mods were not applied this time)".format(", ".join(gone)))

    def rearrange():
        close_list()
        hud = ui.find_hud(ui.find_app())
        if hud is not None:
            try:
                arrange(hud)
            except Exception:
                log_exc("hud icons: cannot rearrange after settling")

    _on_main(rearrange)
    return gone


def remove_all():
    """ローダを外すとき。登録を空にし、MOD 用のボタンを外してゲームの4つを元の位置へ戻す（表のスレッドで呼ぶ）。"""
    store = _store()
    store["icons"].clear()
    store["attention"].clear()
    store["generation"] = None
    close_list()
    try:
        from kivy.core.window import Window
    except Exception:
        Window = None
    if Window is not None:
        previous = frames.attr(Window, HOVER_ATTR, None)
        if previous not in (None, frames.MISSING):
            try:
                Window.unbind(mouse_pos=previous)
            except Exception:
                pass
            try:
                setattr(Window, HOVER_ATTR, None)
            except Exception:
                pass
    hud = ui.find_hud(ui.find_app())
    if hud is None:
        return
    try:
        arrange(hud)                    # 登録が無いので、MOD 用のボタンを外して4つを元へ戻す
    except Exception:
        log_exc("hud icons: cannot restore the game's icons")
    layout = frames.attr(hud, LAYOUT_NAME, None)
    previous = frames.attr(layout, BIND_ATTR, None) if layout not in (None, frames.MISSING) else None
    if previous not in (None, frames.MISSING):
        try:
            layout.unbind(width=previous)
            setattr(layout, BIND_ATTR, None)
        except Exception:
            pass


def _watch(layout, hud):
    """列の幅が変わったら並べ直す（窓の大きさが変わったとき）。結ぶのは1本だけ（前の世代の手は外して結び直す）。"""
    previous = frames.attr(layout, BIND_ATTR, None)
    if _current(previous):
        return
    if previous not in (None, frames.MISSING):
        try:
            layout.unbind(width=previous)
        except Exception:
            pass

    def resized(*_args):
        try:
            arrange(hud)
        except Exception:
            log_exc("hud icons: cannot rearrange after a resize")

    try:
        layout.bind(width=resized)
        setattr(layout, BIND_ATTR, resized)
    except Exception:
        pass


def hover_at(pos):
    """マウスが `pos`（窓の座標）に来た。足したアイコンに、乗った・外れたを知らせる（変わったときだけ）。

    ゲームの4つのアイコンの「乗せた時」の絵は、ボタン自身ではなく HUD の画面（`on_mouse_pos`）が
    自分の4つにだけ知らせて切り替えている（GAME.md §2.34）。足したアイコンにはその知らせが届かず、
    乗せた絵のまま戻らなかったので、ここで同じ知らせを出す。ゲームの4つは窓が開いていても
    処理の最中でも切り替わるので、こちらも場面では止めない。
    """
    hud = ui.find_hud(ui.find_app())
    placed = frames.attr(hud, HUD_ATTR, None) if hud is not None else None
    if not isinstance(placed, dict):
        return
    for button in placed.values():
        on_hover = getattr(button, "on_hover", None)
        if not callable(on_hover) or frames.attr(button, "parent", None) is None:
            continue
        try:
            inside = bool(button.collide_point(*button.to_widget(*pos)))
        except Exception:
            continue
        if frames.attr(button, HOVERING_ATTR, None) is inside:
            continue
        try:
            setattr(button, HOVERING_ATTR, inside)
            on_hover(inside)
        except Exception:
            log_exc("hud icons: the hover of an icon failed")


def _watch_mouse():
    """窓のマウスの位置に1本だけ結ぶ（注入し直したら前の手を外して結び直す）。"""
    try:
        from kivy.core.window import Window
    except Exception:
        return                  # ゲームの外（検査）
    previous = frames.attr(Window, HOVER_ATTR, None)
    if previous not in (None, frames.MISSING):
        try:
            Window.unbind(mouse_pos=previous)
        except Exception:
            pass

    def moved(_window, pos):
        try:
            hover_at(pos)
        except Exception:
            log_exc("hud icons: cannot follow the mouse")

    try:
        Window.bind(mouse_pos=moved)
        setattr(Window, HOVER_ATTR, moved)
    except Exception:
        log_exc("hud icons: cannot watch the mouse")


def install(ctx):
    """HUD が塗り直されるたびに並べ直す包みと、マウスの位置の見張りを、1つの世代につき1組だけ立てる。"""
    store = _store()
    generation = getattr(ctx, "generation", None)
    if store.get("generation") == generation:
        return
    store["generation"] = generation
    _watch_mouse()

    def make(target):
        def upkeep(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                if store["icons"]:
                    arrange(self)
            except Exception:
                log_exc("hud icons: cannot arrange the icons")
            return result
        ctx.wrap(target, required=False, safe=True)(upkeep)

    for target in TARGETS:
        make(target)
