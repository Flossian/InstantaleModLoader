# -*- coding: utf-8 -*-
"""家（`330_real_estate`）と施設（`331_facility_investment`）を1つの窓に並べる窓口。TECH.md §3.3.19。

    holdings.provide(ctx, holdings.HOUSES, build, write=write)       # 330
    holdings.provide(ctx, holdings.FACILITIES, build, write=write)   # 331
    #   build(app) が札の中身（下の「側の形」）を返す。読めなければ None
    holdings.notify(playthrough=key)     # 左下の MOD 用のボタンと「家と施設」の行に赤い点

窓はローダが持つ。どちらの MOD が持つかを決めずに済み、片方だけ入れてもそのまま動く。
一覧の行（「家と施設」）は `hudicons`、窓の部品は `panels`（338/339 の窓と同じ作り）。

並べ方:

- 両方あるとき: 左に家、右に施設。どちらの列もスクロールする
- 片方だけのとき: 窓の幅を2列に使う。左の列を上から埋め、入らない札から右の列へ、
  右の列にも入らなければ右の列だけスクロールする（`panels.Kit.flowing`）

側の形（MOD が返す）:

    {"cards": [札],                 並びは MOD が決める（家は期限の近い順、施設は保管上限に近い順）
     "count": int,                  軒数（上の欄の「家 3軒」）
     "income": int | None,          受け取れる売上の合計（施設だけ）
     "here": [str]}                 いまいる街にある自分の建物の名前

札の形:

    {"name", "where",               上段の名前と、その右の小さな添え
     "badge": (文, 色の段) | None, "caption": 文,
     "meter": {"ratio", "tone", "left", "right"} | None,
     "meter_after": int,            目盛りの前に置く行の数（既定 0 で上段のすぐ下）
     "rows": [(見出し, 中身)], "notes": [文], "cautions": [(文, 色の段)],
     "frame": 色の段 | None, "title_tone": 色の段 | None}     枠と名前の色（役場の預かりの札）

色の段は `good`（緑）・`fair`（若草）・`warn`（橙）・`bad`（赤）・`plain`（灰）。
金額は素のゲームの言い方（`G`）で書いてよい。描く前に `ui.rewrite_coins` を通す（`130_` の表記になる）。

置き場は `sys` の属性（`_instantale_holdings`）。注入し直しをまたいで残る。
その世代で適用されなかった MOD の側は、`ctx.superseded()` で見分けて出さない。
"""
import os
import sys

from . import hudicons, log_exc, panels, ui

STORE_ATTR = "_instantale_holdings"
HOUSES = "houses"
FACILITIES = "facilities"
SIDES = (HOUSES, FACILITIES)
TITLE = "家と施設"
ICON_KEY = "holdings"
#: 一覧の並び（338 の「各地の出来事」10・339 の「勢力と情勢」20 の後）。
ICON_ORDER = 30
ICON_IMAGES = tuple(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets",
                                 "holdings{}.png".format(suffix)) for suffix in ("", "_hover", "_click"))
#: 側ごとの見出し・並びの添え・軒数の言い方・何も無いときの1行。
SIDE_TEXT = {
    HOUSES: {"title": "家", "order": "期限の近い順", "count": "家 {}軒", "empty": "持っている家は無い。"},
    FACILITIES: {"title": "施設", "order": "保管上限に近い順", "count": "施設 {}軒",
                 "empty": "持っている施設は無い。"},
}
TONES = {"good": (0.376, 0.769, 0.588, 1), "fair": (0.588, 0.804, 0.471, 1),
         "warn": (0.91, 0.659, 0.282, 1), "bad": (0.886, 0.329, 0.282, 1),
         "plain": (0.647, 0.647, 0.647, 1)}
UNREADABLE = "記録を読めなかった。"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict) or not isinstance(store.get("sides"), dict):
        store = {"sides": {}, "view": None}
        setattr(sys, STORE_ATTR, store)
    return store


def tone(name, default=panels.TEXT):
    return TONES.get(name, default)


def _alive(entry):
    ctx = entry.get("ctx")
    superseded = getattr(ctx, "superseded", None)
    try:
        return not (callable(superseded) and superseded())
    except Exception:
        return True


def provide(ctx, side, build, write=None):
    """`side`（`HOUSES` / `FACILITIES`）の札の中身を返す `build(app)` を置く。同じ側は差し替え。"""
    if side not in SIDES:
        raise ValueError("holdings.provide() needs one of {}, got {!r}".format(SIDES, side))
    if not callable(build):
        raise TypeError("holdings.provide() needs a callable build, got {!r}".format(type(build)))
    _store()["sides"][side] = {"ctx": ctx, "build": build, "write": write}
    hudicons.add(ctx, ICON_KEY, toggle, images=ICON_IMAGES, order=ICON_ORDER, label=TITLE,
                 write=write, visible=listed)


def sides():
    """いまの世代で置かれている側（`SIDES` の順）。"""
    found = _store()["sides"]
    return [side for side in SIDES if side in found and _alive(found[side])]


def notify(playthrough=None):
    """「家と施設」の行に、見てほしいことがある印を付ける（家の期限が近い・施設の売上が保管上限に届いた）。"""
    hudicons.notify(ICON_KEY, playthrough=playthrough)


def listed(app):
    """一覧に行を出すか。どちらかの側が置かれていて、世界を遊んでいる最中なら出す。"""
    return bool(sides()) and hudicons.current_playthrough(app) is not None


def _write(side, line):
    write = (_store()["sides"].get(side) or {}).get("write")
    if write:
        write(line)


def _coins(value):
    """札の中の文を、通貨の表記へ直す（入れ子の並びと辞書もたどる）。"""
    if isinstance(value, str):
        return ui.rewrite_coins(value)
    if isinstance(value, tuple):
        return tuple(_coins(v) for v in value)
    if isinstance(value, list):
        return [_coins(v) for v in value]
    if isinstance(value, dict):
        return {k: _coins(v) for k, v in value.items()}
    return value


def collect(app):
    """側ごとの中身 `{側: 側の形 or None}`（置かれている側だけ）。読めない側は None。"""
    found = _store()["sides"]
    out = {}
    for side in sides():
        try:
            data = found[side]["build"](app)
        except Exception:
            log_exc("holdings: the {} side could not be built".format(side))
            _write(side, "WARN holdings: the window could not read this mod's records")
            data = None
        out[side] = _coins(data) if isinstance(data, dict) else None
    return out


def subtitle(model, here_name):
    """上の欄の片の並び `[(文, 色 or None)]`。色の無い片は薄い字。"""
    pieces = []
    if here_name:
        pieces.append(("現在地　", None))
        pieces.append((here_name, panels.TEXT))
        here = [name for data in model.values() if data for name in data.get("here") or []]
        if here:
            pieces.append(("（{}）".format("・".join(here)), None))
        pieces.append(("　　", None))
    counts = [SIDE_TEXT[side]["count"].format(int(data.get("count") or 0))
              for side, data in model.items() if data]
    if counts:
        pieces.append(("・".join(counts), None))
    income = [data.get("income") for data in model.values()
              if data and isinstance(data.get("income"), int) and not isinstance(data.get("income"), bool)]
    if income:
        pieces.append(("　受け取れる売上 合計 ", None))
        pieces.append((ui.rewrite_coins("{}G".format(ui.money(sum(income)))), panels.TEXT))
    return pieces


# ---------------------------------------------------------------- 窓（Kivy）
def _card(kit, data):
    frame = data.get("frame")
    if frame:
        box = kit.framed(kit.stack(spacing=6, padding=14), color=tone(frame))
    else:
        box = kit.card()
    badge = data.get("badge")
    if data.get("title_tone"):
        box.add_widget(kit.label(data.get("name") or "", panels.BODY, tone(data.get("title_tone"))))
    else:
        box.add_widget(kit.head(data.get("name") or "", sub=data.get("where") or "", sub_scale=panels.META,
                                badge=(badge[0], tone(badge[1])) if badge else None,
                                caption=data.get("caption") or ""))
    meter = data.get("meter")
    rows = list(data.get("rows") or [])
    before = int(data.get("meter_after") or 0) if meter else 0
    for head, text in rows[:before]:
        box.add_widget(kit.labeled(head, text))
    if meter:
        box.add_widget(kit.meter(meter.get("ratio"), tone(meter.get("tone")),
                                 meter.get("left") or "", meter.get("right") or ""))
    for head, text in rows[before:]:
        box.add_widget(kit.labeled(head, text))
    for text in data.get("notes") or []:
        box.add_widget(kit.label(text, panels.META, panels.DIM))
    for text, name in data.get("cautions") or []:
        color = tone(name)
        box.add_widget(kit.barred(kit.label(text, panels.META, color), color, width=4))
    return box


def _column_title(kit, side):
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.widget import Widget
    text = SIDE_TEXT[side]
    row = BoxLayout(orientation="horizontal", size_hint_y=None, height=kit.base * panels.NAME * 1.5,
                    spacing=kit.px(14))
    for widget in (kit.label(text["title"], panels.NAME, wrap=False),
                   kit.label(text["order"], panels.META, panels.DIM, wrap=False)):
        widget.pos_hint = {"center_y": 0.5}
        row.add_widget(widget)
    row.add_widget(Widget())
    return row


def _cards_of(kit, side, data):
    if data is None:
        return [kit.label(UNREADABLE, panels.BODY, panels.DIM)]
    cards = [_card(kit, card) for card in data.get("cards") or []]
    return cards or [kit.label(SIDE_TEXT[side]["empty"], panels.BODY, panels.DIM)]


def build_body(kit, model):
    from kivy.uix.boxlayout import BoxLayout
    present = [side for side in SIDES if side in model]
    if len(present) == 1:
        side = present[0]
        body = BoxLayout(orientation="vertical", spacing=kit.px(8))
        body.add_widget(_column_title(kit, side))
        body.add_widget(kit.flowing(_cards_of(kit, side, model[side])))
        return body
    body = BoxLayout(orientation="horizontal", spacing=kit.px(16))
    for side in present:
        column = BoxLayout(orientation="vertical", spacing=kit.px(8))
        column.add_widget(_column_title(kit, side))
        cards = kit.column()
        for widget in _cards_of(kit, side, model[side]):
            cards.add_widget(widget)
        column.add_widget(kit.scroller(cards))
        body.add_widget(column)
    return body


def close():
    store = _store()
    view, store["view"] = store.get("view"), None
    if view is not None:
        try:
            view.dismiss()
        except Exception:
            log_exc("holdings: could not close the window")


def toggle(app):
    """一覧の「家と施設」の行が押された。開いていれば閉じる。"""
    store = _store()
    if store.get("view") is not None:
        close()
        return
    if app is None:
        return
    try:
        model = collect(app)
        area = ui.current_area(app)
        here_name = str(getattr(area, "name", "") or "") if area is not None else ""
        store["view"] = panels.open_window(app, TITLE, lambda kit: build_body(kit, model),
                                           subtitle=subtitle(model, here_name),
                                           on_dismiss=lambda: store.update({"view": None}),
                                           log_exc=log_exc)
        for side in model:
            data = model[side]
            _write(side, "holdings: window opened ({} card(s) on the {} side)".format(
                len((data or {}).get("cards") or []), side))
    except Exception:
        store["view"] = None
        log_exc("holdings: could not open the window")


def remove_all():
    """ローダを外すときに呼ぶ。"""
    close()
    _store()["sides"].clear()
