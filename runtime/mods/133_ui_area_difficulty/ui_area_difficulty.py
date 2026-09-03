# -*- coding: utf-8 -*-
"""表示: 移動先の一覧に、その土地の適正レベル帯を添える。

素のゲームの「他の土地へ行く」の一覧は土地の名前だけが並ぶ。
次に向かう土地のレベル適正は、行ってから掲示板を見るまで分からない。
この MOD は一覧のボタンにその土地の依頼の難易度帯を足す:

    陽光の砦                →  陽光の砦（適正Lv 21〜30）

帯の材料はゲーム自身の `get_quest_difficulties(area, world)`
（`scripts.functions`。店の品揃え・敵の強さの源と同じ関数。GAME.md §2.9.1）。
最小〜最大に `LEVEL_OFFSET`（既定 1。敵のレベルは難易度+1。GAME.md §2.17）を足して出す。
生きた一覧（`world.quests`）を読むので、`318_area_difficulty_growth` で難易度が育っていれば
育った後の値が出る。

変更点は3つ。

| 何を変えるか | どこで変えるか |
|---|---|
| 一覧のボタンの表示 | `DisplayAreaMoveChoice.update_button_display` の後で `text` だけ書き換える |
| 確認画面の1行 | `AreaMoveCofirmation.execute` の間に通る文言に土地の名前があれば帯を添える。無ければ1行足す |
| 押された文字列 | 自分が書いたラベルは、`execute` へ渡す前に素の名前へ戻す |

留意点:

- **ボタンは `text` だけ触る**（spec と `args` はゲーム自身のものを残す。`314_` と同じ）。
  行き先の見分けは文字列ではなく spec の `args`（`[target_area_id]`）で行う。
  `args` から土地が引けなければ名前で引き、それも無ければ触らない
- 押されたボタンの文字列（`choice_text`）はゲームの `execute` に渡る。
  ゲームがそれを土地の名前として使う可能性を消すため、
  自分が書いたラベルなら素の名前に戻してから渡す
- 依頼が1件も無い土地は、まだ作られていない（未訪問の）街。街の中身と初期依頼は初訪問で
  作られ、難易度は `random.sample(range(lo, hi), 3)` で `lo` / `hi` は街の枠（id）で決まる
  （`225_` の実測。VERIFICATION_LOG.md §2.84）。枠の表（`SLOT_RANGES`）から見積もった帯を
  `BAND_ESTIMATE` の表記で出す。枠の無い街（エディタで足した id 9 以降）は `BAND_UNKNOWN`。
  空なら名前のまま
- 「既定=素の挙動」の原則の例外。素の値を変えず情報を足すだけの UI なので、
  既定で表示する（`314_` の馬車の日数と同じ判断）

遊び方の説明は DOC.md、検証の経過は VERIFICATION.md §3.49。
"""

import sys

from instantale_modloader import ui

LOG_BASENAME = "ui_area_difficulty.log"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_ui_area_difficulty_store__"

# ボタンは足さないが `ui.Screen` の道具（say / apply_buttons）を使うので、
# 印のキーは他の MOD と別にして持つ（TECH.md §5.1.1）。
MARK = "mod_ui_area_difficulty"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む。TECH.md
# §3.8）。
# 一覧のボタンに帯を出すか。False なら何もしない（確認画面の1行も出ない）。
SHOW = True

# 難易度に足してレベルにする数。
# 敵のレベルは難易度+1（GAME.md §2.17 の実測）なので既定 1。
# 0 にすると難易度そのものを出す。
LEVEL_OFFSET = 1

# ボタンの表示テンプレート。
# {name} は土地の名前、{band} は下の帯。
LABEL = "{name}（適正Lv {band}）"

# 帯の表記。
# 最小と最大が違うとき / 同じとき / 依頼が無くて引けないとき。
# 使える変数: {lv_min} {lv_max}（レベル）、{min} {max}（難易度）、{count}（依頼の数）。
# BAND_UNKNOWN を空にすると、引けない土地は名前のまま。
BAND = "{lv_min}〜{lv_max}"
BAND_SINGLE = "{lv_min}"
BAND_UNKNOWN = "不明"

# まだ作られていない街（未訪問。依頼が無い）に、街の枠から見積もった帯を出すか。
# 街の中身は初訪問で作られ、初期依頼3件の難易度は `random.sample(range(lo, hi), 3)` で、
# `lo` / `hi` は街の枠（id）で決まる（`225_` の実測。VERIFICATION_LOG.md §2.84）。
# 枠は下の `SLOT_RANGES`。False なら未訪問の街は `BAND_UNKNOWN`。
ESTIMATE = True

# 見積もりの帯の表記。変数は BAND と同じ。
BAND_ESTIMATE = "推定 {lv_min}〜{lv_max}"

# 確認画面（徒歩・馬車が並ぶ画面）に足す1行。
# その画面でゲームが出す文言に土地の名前が入っていれば、そこへ帯を添えるだけで
# この1行は出さない。
# 空でその1行を出さない。
# 使える変数: {name} {band} {label}。
CONFIRM_TEXT = "（{name}の適正Lv: {band}）"

# ---------------------------------------------------------------- コード側の設定
# 行き先一覧のボタンの spec のクラス名。
# `process_choice(AreaMoveCofirmation, '陽光の砦')` の実測（GAME.md §2.18）から、
# 一覧の1行はこのクラスの `PhaseSpec` で、`args` は `[target_area_id]`。
TARGET_CLS = "AreaMoveCofirmation"

# 依頼の難易度をその土地に結ぶ鍵（ゲーム自身の関数が無いときの落とし所。GAME.md §2.9）。
QUEST_AREA_KEY = "neighboring_settlement_id"
QUEST_DIFFICULTY_KEY = "difficulty"

# 街の枠ごとの初期依頼の難易度の範囲 `range(lo, hi)`（`hi` は含まない）。
# 世界は必ず 9 街（0 が開始地点。0→1→2→3 / 0→4→5→6 / 0→7→8 の木）で、
# 枠は id そのもの。序盤 0・1・4 → 中盤 2・5・7 → 終盤 3・6・8 の順に窓が隣り合う
# （前の枠の `hi` が次の枠の `lo`）。id 0 だけ幅が狭い。
# 実測: id 2 / 3 / 8 は `sample` の実引数そのもの（`225_`）。
# 残りは5世界の初期依頼 15 件ずつから読んだ境界（VERIFICATION_LOG.md §2.84）。
# エディタで足した街（id 9 以降）には枠が無く、ゲームは全域 `range(1, 77)` から引く
# （実測）。推定しても 2〜77 にしかならないので表に入れず、`BAND_UNKNOWN` にする。
SLOT_RANGES = {
    "0": (1, 6),
    "1": (3, 11),
    "4": (11, 19),
    "2": (19, 28),
    "5": (28, 37),
    "7": (37, 47),
    "3": (47, 57),
    "6": (57, 67),
    "8": (67, 77),
}


class _SafeDict(dict):
    """テンプレートに無い変数名が来ても落とさない（`{typo}` はそのまま残る）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def fmt(template, **values):
    """設定のテンプレートを埋める。壊れたテンプレートでも素の文字列で返す。"""
    try:
        return str(template).format_map(_SafeDict(values))
    except Exception:
        return str(template)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def difficulties_by_scan(world, area_id):
    """`world.quests` を自分で走査する落とし所。ゲームの関数が引けないときだけ。"""
    quests = getattr(world, "quests", None)
    if not isinstance(quests, dict):
        return []
    found = []
    for quest in quests.values():
        owner = ui.quest_value(quest, QUEST_AREA_KEY, None)
        if owner is None or str(owner) != str(area_id):
            continue
        value = ui.quest_value(quest, QUEST_DIFFICULTY_KEY, None)
        if _number(value):
            found.append(value)
    return found


def band_values(values, offset):
    """難易度の一覧からテンプレートに渡す変数一式。空なら None。"""
    numbers = [v for v in values if _number(v)]
    if not numbers:
        return None
    low = int(round(min(numbers)))
    high = int(round(max(numbers)))
    return {"min": low, "max": high, "count": len(numbers),
            "lv_min": low + int(offset), "lv_max": high + int(offset)}


def estimate_values(area_id, offset):
    """まだ作られていない街の見積もり。枠が無ければ None。

    `sample(range(lo, hi), 3)` の最小は lo、最大は hi-1。
    """
    slot = SLOT_RANGES.get(str(area_id))
    if slot is None:
        return None
    low, high = int(slot[0]), int(slot[1]) - 1
    return {"min": low, "max": high, "count": 0, "estimated": True,
            "lv_min": low + int(offset), "lv_max": high + int(offset)}


def band_text(values):
    """帯の文字列。引けないときは `BAND_UNKNOWN`（空なら None）。"""
    if values is None:
        return BAND_UNKNOWN or None
    if values.get("estimated"):
        return fmt(BAND_ESTIMATE, **values)
    template = BAND_SINGLE if values["min"] == values["max"] else BAND
    return fmt(template, **values)


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="area difficulty label", mark=MARK)

    # 置き場は `sys`。`apply()` は1プロセスで何度も走る（`314_` と同じ理由）。
    state = getattr(sys, STATE_STORE_ATTR, None)
    if state is None:
        state = {
            # 自分が最後に書いたラベル {area_id: text}。
            # 一覧を開き直したとき「自分の書いたラベルを素の名前と取り違えない」ため、
            # そして押された文字列を素の名前へ戻すため。
            "labels": {},
            # いま `AreaMoveCofirmation.execute` の中に居るかの窓。
            "confirm": None,
            # 落とし所（自前走査）へ下がったことを一度だけ書く印。
            "scan_warned": False,
        }
        setattr(sys, STATE_STORE_ATTR, state)

    # ============================================================ 帯を引く
    def difficulties_of(app, area):
        """その土地の依頼の難易度の一覧。ゲーム自身の関数に聞く。"""
        world = getattr(app, "world", None)
        functions = sys.modules.get("scripts.functions")
        fn = getattr(functions, "get_quest_difficulties", None) if functions else None
        if fn is not None:
            try:
                values = fn(area, world)
                return [v for v in (values or []) if _number(v)]
            except Exception as exc:
                write("WARN get_quest_difficulties failed on {!r}: {}: {}".format(
                    getattr(area, "name", None), type(exc).__name__, exc))
        elif not state["scan_warned"]:
            state["scan_warned"] = True
            write("WARN scripts.functions.get_quest_difficulties not found; "
                  "scanning world.quests instead")
        return difficulties_by_scan(world, ui.area_id_of(area))

    def values_of(app, area):
        """テンプレートの変数一式（name / band と難易度の数）。"""
        name = getattr(area, "name", None) or ""
        values = band_values(difficulties_of(app, area), LEVEL_OFFSET)
        if values is None and ESTIMATE:
            # 依頼が1件も無い＝まだ作られていない街。枠から見積もる。
            values = estimate_values(ui.area_id_of(area), LEVEL_OFFSET)
        band = band_text(values)
        merged = {"name": name, "band": band or ""}
        if values is not None:
            merged.update(values)
        return merged, band

    def label_of(app, area):
        """その土地のボタンの表示。帯が出せなければ None（名前のまま）。"""
        values, band = values_of(app, area)
        if band is None or not values["name"]:
            return None, values
        label = fmt(LABEL, **values)
        values["label"] = label
        return label, values

    def resolve_area(app, entry):
        """一覧の1行が指す土地。`args` で引き、無ければ名前で引く。"""
        areas = ui.world_areas(app)
        args = ui.spec_args(entry)
        if args:
            area = areas.get(str(args[0]))
            if area is not None:
                return area
        text = entry.get("text") or ""
        for area in areas.values():
            name = getattr(area, "name", None)
            if name and (text == name
                         or text == state["labels"].get(ui.area_id_of(area))):
                return area
        return None

    # ============================================================ 一覧のボタン
    @ctx.wrap("__main__:DisplayAreaMoveChoice.update_button_display", required=False,
              safe=True)
    def move_choice_buttons(orig, self, *args, **kwargs):
        """行き先が並び終えた後、ラベルの `text` だけを書き直す。

        描画はもう済んでいるので、変えたときだけ次のフレームで塗り直す（`314_` と同じ）。
        二度目は同じ文字列になるので塗り直しは繰り返さない。
        """
        result = orig(self, *args, **kwargs)
        if not SHOW:
            return result
        try:
            app = getattr(self, "app", None) or ui.find_app()
            buttons = getattr(app, "buttons", None) if app is not None else None
            if not isinstance(buttons, (list, tuple)):
                return result
            changed = 0
            seen = 0
            for entry in buttons:
                if ui.spec_cls_name(entry) != TARGET_CLS:
                    continue
                seen += 1
                old = entry.get("text") or ""
                area = resolve_area(app, entry)
                if area is None:
                    write("no area for {!r} args={!r}; leaving it alone".format(
                        old, ui.spec_args(entry)))
                    continue
                area_id = ui.area_id_of(area)
                name = getattr(area, "name", None) or ""
                if old != name and old != state["labels"].get(area_id):
                    write("label {!r} is neither the name {!r} nor ours; "
                          "leaving it alone".format(old, name))
                    continue
                new, values = label_of(app, area)
                if new is None:
                    if old != name:
                        entry["text"] = name
                        changed += 1
                    state["labels"].pop(area_id, None)
                    write("area {} {!r}: no band ({} quest(s))".format(
                        area_id, name, values.get("count", 0)))
                    continue
                state["labels"][area_id] = new
                if new != old:
                    entry["text"] = new
                    changed += 1
                    write("label: {!r} -> {!r} (difficulty {}..{}, {} quest(s){})".format(
                        old, new, values.get("min"), values.get("max"),
                        values.get("count"),
                        ", estimated from slot" if values.get("estimated") else ""))
            if changed:
                screen.apply_buttons(app, None, "relabel")
            elif seen:
                write("{} destination(s) already labelled".format(seen))
        except Exception:
            ctx.log_exc("area difficulty label: cannot relabel the destinations")
        return result

    # ============================================================ 確認画面
    @ctx.wrap("__main__:AreaMoveCofirmation.__init__", required=False, safe=True)
    def confirmation_init(orig, self, app, target_area_id, *args, **kwargs):
        """`execute` には行き先が来ないので、ここで自分に控える。

        自分専用の属性名で持つだけで、ゲームのデータには何も書かない
        （`314_` の `AreaMoveManager.__init__` と同じ）。
        """
        result = orig(self, app, target_area_id, *args, **kwargs)
        try:
            self._mod_ui_area_difficulty = str(target_area_id)
        except Exception:
            pass
        return result

    @ctx.wrap("__main__:AreaMoveCofirmation.execute", required=False)
    def confirmation_execute(orig, self, choice_text=None, *args, **kwargs):
        """確認画面の間だけ窓を開ける。

        押された文字列が自分の書いたラベルなら、素の名前に戻してから渡す。
        窓の間に土地の名前を含む文言が通れば、そこへ帯を添える。
        通らなければ、`orig` の後に1行足す（`CONFIRM_TEXT`）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        window = None
        try:
            target_id = getattr(self, "_mod_ui_area_difficulty", None)
            area = ui.world_areas(app).get(str(target_id)) \
                if target_id is not None else None
            if SHOW and area is not None:
                name = getattr(area, "name", None) or ""
                if isinstance(choice_text, str) and name \
                        and choice_text == state["labels"].get(str(target_id)) \
                        and choice_text != name:
                    write("choice text {!r} -> {!r} (our label, restored)".format(
                        choice_text, name))
                    choice_text = name
                label, values = label_of(app, area)
                if label is not None:
                    window = {"name": name, "label": label, "values": values,
                              "seen": 0}
                    state["confirm"] = window
        except Exception:
            ctx.log_exc("area difficulty label: cannot open the confirmation window")
            window = None
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            state["confirm"] = None
            if window is not None and CONFIRM_TEXT and not window["seen"]:
                try:
                    line = fmt(CONFIRM_TEXT, **window["values"])
                    write("confirm: no line carried {!r}; adding {!r}".format(
                        window["name"], line))
                    screen.say(app, line)
                except Exception:
                    ctx.log_exc("area difficulty label: "
                                "cannot add the confirmation line")

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context=None, *args, **kwargs):
        """確認画面の窓の間だけ、土地の名前を含む文言に帯を添える。"""
        try:
            window = state["confirm"]
            if window is not None and isinstance(context, str) and context.strip():
                name = window["name"]
                label = window["label"]
                if name and name in context and label not in context:
                    replaced = context.replace(name, label, 1)
                    window["seen"] += 1
                    write("confirm text: {!r} -> {!r}".format(context, replaced))
                    return orig(self, replaced, *args, **kwargs)
                if context.strip(".。 　"):
                    write("confirm text passing through: {!r}".format(context))
        except Exception:
            ctx.log_exc("area difficulty label: cannot reword the confirmation text")
        return orig(self, context, *args, **kwargs)

    # ------------------------------------------------------------ 自己検証
    # 実経路は一覧を開くまで通らない。
    # 帯の組み立てだけは作ったデータで先に確かめておく（`314_` と同じ方針）。
    ranged = band_text(band_values([20, 29, 25], 1))
    single = band_text(band_values([7], 1))
    empty = band_text(band_values([], 1))
    guess = band_text(estimate_values("8", 1))
    survives = fmt("{name}と{typo}", name="砦")
    if ranged == "21〜30" and single == "8" and empty == (BAND_UNKNOWN or None) \
            and guess == fmt(BAND_ESTIMATE, lv_min=68, lv_max=77, min=67, max=76,
                             count=0) \
            and survives == "砦と{typo}":
        ctx.log("verified: builds the band from difficulties and formats templates")
    else:
        ctx.log("VERIFY FAILED: ranged={!r} single={!r} empty={!r} survives={!r}".format(
            ranged, single, empty, survives), level="ERROR")

    ctx.log("area difficulty label: show={} offset={} label={!r} log={}".format(
        SHOW, LEVEL_OFFSET, LABEL, log_path))
