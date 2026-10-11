# -*- coding: utf-8 -*-
"""画面左下のゲームのアイコンの列に、MOD のアイコンを足す窓口。TECH.md §3.3.15。

    hudicons.add(ctx, key, on_press, strokes=None, images=None, order=0, write=None)
    hudicons.withdraw(key)

ゲームは能力値の欄の下端に、持ち物・設定・キャラ情報・クラフトの4つのアイコンを並べている
（`InstanTaleHUD.icon_button_layout`。FloatLayout で、位置は比率、大きさは固定の px。GAME.md §2.34）。
足したアイコンはその4つの右に並べ、全部が欄に収まるよう、ゲームの4つも含めて左へ詰める。

並べ方:

- 列の左の余白（ゲームの一番左のアイコンの x）を、右にも同じだけ取る
- その間に、ゲームの4つ・足したアイコンの順で等間隔に置く。足したアイコンどうしは `order`、同じなら `key` の順
- 大きさはゲームのアイコンに揃える
- 比率は列の幅から毎回出し直す（大きさが px の固定なので、窓の大きさが変わると比率も変わる）
- 足したアイコンが1つも無くなったら、ゲームの4つを元の位置へ戻す

並べ方を MOD に書かせないのは、2本目の MOD が足したときに互いの位置を知らずに重なるため
（画面の判断はローダの1か所）。

`on_press(app)` は押されたときに呼ぶ。`strokes` は `ui.paint_icon` と同じ 0〜1 の座標の線、
`images` は画像のパスで、`(普段, 乗せた時, 押した時)` の3枚ならゲーム自身のアイコンと同じ型のボタンで作る
（乗せた時の絵も効く）。`(普段, 押した時)` の2枚なら普通のボタン。どちらも無ければ `key` の頭1字を出す。
置き場は `sys` の属性（`_instantale_hud_icons`）。注入し直しをまたいで残り、同じ `key` は差し替える。
"""
import sys

from . import frames, log_exc, ui

STORE_ATTR = "_instantale_hud_icons"
#: HUD に足したボタンの控え（`{key: Button}`）。
HUD_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icons"
#: ゲームのボタンの元の `pos_hint`。
HOME_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_home"
#: 列の大きさの変化に結んだ手。
BIND_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_bound"
PAINT_ATTR = ui.MOD_WIDGET_PREFIX + "hud_icon_paint"
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


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict) or not isinstance(store.get("icons"), dict):
        store = {"icons": {}, "generation": None, "write": None, "said": set()}
        setattr(sys, STORE_ATTR, store)
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


def add(ctx, key, on_press, strokes=None, images=None, order=0, write=None):
    """アイコンを1つ足す（同じ `key` は差し替え）。HUD がもう在れば次のフレームで並べ直す。"""
    if not callable(on_press):
        raise TypeError("hudicons.add() needs a callable on_press, got {!r}".format(type(on_press)))
    store = _store()
    store["icons"][str(key)] = {"key": str(key), "on_press": on_press, "ctx": ctx,
                                "strokes": list(strokes or []), "images": images,
                                "order": order}
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
    """アイコンを外す。次に並べ直すときに HUD からも外れる。"""
    _store()["icons"].pop(str(key), None)
    hud = ui.find_hud(ui.find_app())
    if hud is not None:
        try:
            arrange(hud)
        except Exception:
            log_exc("hud icons: cannot rearrange after withdraw")


def keys():
    return sorted(_store()["icons"])


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


def _paint(button, entry):
    strokes = entry.get("strokes")
    if strokes:
        ui.paint_icon(button, strokes, attr=PAINT_ATTR, key=(entry["key"],),
                      log_exc=lambda msg: log_exc("hud icons: " + msg))


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


def arrange(hud):
    """足したアイコンを置き、ゲームの4つと一緒に並べ直す。何度呼んでも同じ形になる。"""
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
    entries = sorted((entry for entry in _store()["icons"].values() if _alive(entry)),
                     key=lambda entry: (entry.get("order", 0), entry["key"]))
    wanted = {entry["key"] for entry in entries}
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
    for entry in entries:
        button = placed.get(entry["key"])
        if button is None:
            button = _make_button(entry, size, game[0])
            if button is None:
                _once("no-kivy", "WARN hud icons: kivy Button unavailable")
                return
            placed[entry["key"]] = button
            _log("hud icons: added {!r}".format(entry["key"]))
        if button.parent is not layout:
            try:
                if button.parent is not None:
                    button.parent.remove_widget(button)
                layout.add_widget(button)
            except Exception:
                log_exc("hud icons: cannot add {!r}".format(entry["key"]))
                continue
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
    widgets = [button for button, _home in order] + [placed[entry["key"]] for entry in entries
                                                      if entry["key"] in placed]
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
    for entry in entries:
        button = placed.get(entry["key"])
        if button is not None:
            _paint(button, entry)
            _follow(button, entry)


def _follow(button, entry):
    """動いたら線を引き直す（列が並べ直されると、比率の変化で位置が動く）。"""
    if frames.attr(button, BIND_ATTR, None) not in (None, frames.MISSING):
        return

    def moved(*_args):
        _paint(button, entry)

    try:
        button.bind(pos=moved, size=moved)
        setattr(button, BIND_ATTR, moved)
    except Exception:
        pass


def _watch(layout, hud):
    """列の幅が変わったら並べ直す（窓の大きさが変わったとき）。結ぶのは1本だけ。"""
    previous = frames.attr(layout, BIND_ATTR, None)
    if previous not in (None, frames.MISSING):
        return

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
