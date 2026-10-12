# -*- coding: utf-8 -*-
"""ゲームが決めている値段をローダが1箇所で持つ（期間は `durations`）。

宿屋の部屋の値段のように、**ゲームが決めている額**を変える MOD
（`315_vacation_custom`）と、その額を**先に知りたい** MOD
（`330_real_estate` / `331_facility_investment` の自分の建物での滞在）がある。
MOD どうしは import しない（TECH.md §3.2.3）ので、両者はここで繋がる。

    値段を変える側      prices.declare(prices.INN_ROOM, fn, owner="315_…")
    値段を知りたい側    price = prices.inn_room(app, quality)   # int か None

**既定はゲーム自身の値**で、変える MOD が入っていなければそれが答えになる。
置く側は「答えを返す関数」を置く（値ではない。設定で変わるうえ、
置き直す責任を読む側に持ち込まないため。`durations` と同じ約束）。

読む側が額を先に知りたいのは、**ゲームに引かせてから返すのをやめる**ため。
ゲームは所持金を `player.gold` に直接書くので、引き落としの瞬間だけを掴む口が無い。
そこで引かれるぶんを先に足しておき、ゲームが引いて元に戻す（前払い調整。
`314_area_move_custom` の運賃・`315_vacation_custom` の宿代と同じ形）。
差額で当てると、同じ区間で動いた他の MOD の金まで巻き込む。

登録簿は `durations` と同じ1つ（`sys` の `_instantale_durations`）。
`durations.forget(owner)` で期間と値段がまとめて外れるので、片付けの口は増えない。
"""
import sys
import threading
import weakref

from . import durations
from . import log_exc


#: 関所を立てた行を最後に書いた中身。同じなら繰り返さない。
#: 関所は boot ごとに立て直すので、遅れて当て直す boot のたびに同じ行が並んでいた。
#: 注入し直すとこのモジュールごと読み直されるので、新しいログの頭には必ず出る。
_gate_written = None

#: `adjust` の行を書いた持ち主 -> temporary か。同じなら繰り返さない（`_gate_written` と同じ）。
_adjust_written = {}

#: 宿屋の部屋1回。置く関数は `fn(app, quality=...)`、答えは `{"price": int}`。
#: 「ゲームのままでよい」なら `None` を返す（既定へ落ちる）。
INN_ROOM = "inn_room"

#: 素のゲームの部屋の値段（`quality` の実値 → 額）。
#: 実測（部屋選びのボタン `個室(100G)` と `VacationStartManager` の args。
#: GAME.md §2.17）。**観測できたものだけ置く**。
GAME_ROOM_PRICES = {"kennel": 0, "bunk": 10, "private_room": 100,
                    "luxury_suite": 1000}


def declare(kind, fn, owner="", write=None):
    """その種類の値段を決める関数を置く。置き換えたら前の持ち主を返す。"""
    return durations.declare(kind, fn, owner=owner, write=write)


def source_of(kind):
    """その種類を決めている MOD の名前。誰も置いていなければ `""`（ゲームの値）。"""
    return durations.source_of(kind)


def game_inn_room(quality):
    """素のゲームの部屋の値段。知らない `quality` なら None。"""
    return GAME_ROOM_PRICES.get(str(quality))


def inn_room_base(app, quality, write=None):
    """宿屋の部屋1回の値段のうち、段（`adjust_inn`）を掛ける前の額。分からなければ None。

    `declare(INN_ROOM, …)` の答え（`315_vacation_custom` の設定額）か、無ければゲームの値。
    v1.15.0 までの `inn_room` の答えがこれ。
    """
    answer = durations.ask(INN_ROOM, app, write=write, quality=quality)
    if answer is None:
        return _game_price_of(quality)
    price = answer.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        if write:
            write("WARN prices: inn_room from {!r} returned {!r}, not a number; "
                  "using the game's own value".format(source_of(INN_ROOM), price))
        return _game_price_of(quality)
    return max(0, int(price))


def inn_room(app, quality, write=None):
    """宿屋の部屋1回の値段。**分からなければ None**（呼ぶ側が別の道へ落とす）。

    知らない `quality` で None を返すのは、ゲームの更新で語彙が変わったときに
    **当て推量の額を前払いしない**ため。0 と「分からない」は別の答えにする。

    v1.15.1 から段（`adjust_inn`）を掛けた後の額を返す。ゲームが実際に引く額と同じ
    （宿代の関所がその額になるよう前払いで合わせる）。
    """
    base = inn_room_base(app, quality, write=write)
    if base is None:
        return None
    return _inn_layered(app, quality, base, write)


# ---------------------------------------------------------------------------
# 宿代の段と関所（TECH.md §3.3.4）
# ---------------------------------------------------------------------------
#
# 宿代は2つの層で決まる。
#
#     declare(INN_ROOM, fn)   1枚だけ。部屋1回の額と呼び名（`315_vacation_custom` の設定）
#     adjust_inn(owner, fn)   何枚でも乗る。その額へ倍率を掛ける（`338_town_incidents` の街の出来事）
#
# ゲームに効かせるのは関所（`install_inn`）の1か所で、v1.15.0 までは `315_` が自分で持っていた。
# 段を置く MOD が増えると、`315_` が入っていないときに誰も効かせなくなるので、ローダへ移した。
# 関所がすること（どれも `315_` の版から形を変えていない）:
#
# - 部屋のボタン（`VacationStartManager` の spec）の `text` を、呼び名と額で書き直す
#   （素の呼び名で素の額なら、ゲームの文言のまま）。spec と `args` には触らない
# - 手持ちが額に満たなければ、押した時点で断る（部屋選びは残る）
# - ゲームが引く前に、差額ぶん所持金をずらす（前払い調整）。引かなかったビルドでは返す
#
# 部屋の見分けは `quality` の実値（`GAME_ROOM_PRICES`）。語彙が変わったビルドのために、
# 画面のラベルの素の額と `quality` の対も控えて保険にする。どちらにも当たらなければ
# 表示も額も全部ゲームのまま（表示だけ変わる食い違いを作らない）。

#: 素のゲームの部屋の呼び名（`quality` の実値 → 名前。実測。GAME.md §2.17）。
GAME_ROOM_NAMES = {"kennel": "犬小屋", "bunk": "簡易寝台", "private_room": "個室",
                   "luxury_suite": "高級個室"}

#: 部屋のボタンの既定の形（ゲーム自身の書式。通貨の表記は `ui.fill_template` が今のものへ直す）。
ROOM_LABEL = "{name}({price}G)"

#: 手持ちが宿代に足りないときの一言（ゲーム画面の文。`315_` の版と同じ）。
INN_REFUSE_TEXT = "（{name}の宿代{price}Gに足りない ― 手持ち{gold}G）"

INN_START = "VacationStartManager"
_INN_ATTR = "_instantale_inn_prices"
_INN_MARK = "_instantale_inn_room"
_INN_OWNER = "prices:inn"


def _inn_store():
    found = getattr(sys, _INN_ATTR, None)
    if not isinstance(found, dict) or not isinstance(found.get("adjust"), list):
        found = {"adjust": [], "observed": {}, "ours": set(), "unknown": set(),
                 "write": None, "generation": None, "installed": False}
        setattr(sys, _INN_ATTR, found)
    return found


def adjust_inn(owner, fn, write=None):
    """宿代へ乗せる段を足す。**同じ持ち主は1枚まで**（再注入で重ねない）。

    `fn(app, quality, price)` は新しい額か、**触らないなら None** を返す。
    `quality` はゲームの語彙（`kennel` / `bunk` / `private_room` / `luxury_suite`）、
    `price` はそこまでの段を通した額。段は登録順に重なる。
    段を置いた MOD は `install_inn(ctx, write)` も呼ぶ（呼ばないとゲームに効かない）。
    """
    if not callable(fn):
        raise TypeError("adjust_inn() needs a callable, got {!r}".format(type(fn)))
    name = str(owner or "")
    with _LOCK:
        layers = _inn_store()["adjust"]
        fresh = name not in [layer[0] for layer in layers]
        layers[:] = [layer for layer in layers if layer[0] != name]
        layers.append((name, fn))
    if write and fresh:
        write("prices: {!r} adjusts the inn room price".format(name))


def inn_price_sources():
    """いま宿代を決めている持ち主。`("答えの持ち主", ["段の持ち主", ...])`。"""
    with _LOCK:
        return (source_of(INN_ROOM), [layer[0] for layer in _inn_store()["adjust"]])


def forget_inn_prices(owner, write=None):
    """その持ち主が置いた宿代の段を外す。外したら `["inn_room_adjust"]`。"""
    name = str(owner or "")
    with _LOCK:
        layers = _inn_store()["adjust"]
        if not any(layer[0] == name for layer in layers):
            return []
        layers[:] = [layer for layer in layers if layer[0] != name]
    if write:
        write("prices: {!r} no longer adjusts the inn room price".format(name))
    return ["inn_room_adjust"]


durations.on_forget(forget_inn_prices)


def _quality_of_price(price):
    for quality, value in GAME_ROOM_PRICES.items():
        if value == price:
            return quality
    return None


def canonical_quality(quality):
    """ゲームの語彙の `quality`。語彙に無ければ、画面で控えた額から引く。当たらなければ None。"""
    quality = str(quality)
    if quality in GAME_ROOM_PRICES:
        return quality
    observed = _inn_store()["observed"].get(quality)
    return _quality_of_price(observed) if observed is not None else None


def _game_price_of(quality):
    """ゲームがその部屋で引く額。画面のラベルで控えた額を先に、無ければ実測の表。"""
    observed = _inn_store()["observed"].get(str(quality))
    if observed is not None:
        return observed
    canon = canonical_quality(quality)
    return GAME_ROOM_PRICES.get(canon) if canon is not None else None


def _inn_layered(app, quality, price, write=None):
    canon = canonical_quality(quality) or str(quality)
    with _LOCK:
        layers = list(_inn_store()["adjust"])
    for owner, fn in layers:
        try:
            got = _price_number(fn(app, canon, price))
        except Exception:
            log_exc("prices: the inn adjust from {!r} failed".format(owner))
            continue
        if got is not None:
            price = got
    return max(0, int(round(price)))


def _room_answer(app, quality, write=None):
    """部屋1つの `{"price", "name", "label", "game_price", "game_name"}`。見分けられなければ None。"""
    canon = canonical_quality(quality)
    if canon is None:
        return None
    game_price = _game_price_of(quality)
    if game_price is None:
        return None
    answer = durations.ask(INN_ROOM, app, write=write, quality=canon) or {}
    name = answer.get("name")
    label = answer.get("label")
    price = inn_room(app, canon, write=write)
    if price is None:
        return None
    return {"price": price, "game_price": game_price, "game_name": GAME_ROOM_NAMES[canon],
            "name": name if isinstance(name, str) and name else GAME_ROOM_NAMES[canon],
            "label": label if isinstance(label, str) and label else ROOM_LABEL}


def _game_label(ui, canon, price):
    return ui.rewrite_coins("{}({}G)".format(GAME_ROOM_NAMES[canon], price))


def _relabel_room(ui, app, entry, write):
    """部屋のボタン1つ。描かれる前に `text` だけ書き直し、素の額を対に控える。"""
    argv = ui.spec_args(entry)
    if not argv or len(argv) < 2:
        return
    quality = str(argv[1])
    old = entry.get("text") or ""
    store = _inn_store()
    if old not in store["ours"]:
        price = ui.parse_coin(old)
        if price is None:
            return                      # 額の無いラベル（まだ宿泊する 等）
        canon = quality if quality in GAME_ROOM_PRICES else _quality_of_price(price)
        if canon is None:
            if quality not in store["unknown"]:
                store["unknown"].add(quality)
                if write:
                    write("unknown room {!r} (quality {!r}); leaving it alone "
                          "(the game may have changed its rooms)".format(old, quality))
            return
        # 素の額は、ゲームの書式で、しかも今の答えで自分が書く文言ではないものからだけ控える
        # （ロードで戻ったボタンは保存したときに書いた文言のまま。自分の文言から読むと
        # 設定した額を素の額と取り違え、前払いが狂った。`315_` の版で踏んだ）。
        # ロードで戻った選択肢の組み直し（`choices.from_save`）では控えない。前の起動で段を掛けた文言は、保存の後に
        # 設定や段が変わると「今の答えの文言」とも食い違い、ゲームの書式のまま素の額に見える
        # （VERIFICATION.md §3.97「宿代」）。
        from . import choices as _choices
        answer = _room_answer(app, quality)
        ours_now = answer is not None and old == ui.fill_template(
            answer["label"], name=answer["name"], price=answer["price"])
        if old == _game_label(ui, canon, price) and not ours_now \
                and not _choices.from_save() \
                and store["observed"].get(quality) != price:
            store["observed"][quality] = price
            if write:
                write("room: quality {!r} shows {}G -> {}".format(quality, price, canon))
    answer = _room_answer(app, quality)
    if answer is None:
        return
    canon = canonical_quality(quality)
    if answer["price"] == answer["game_price"] and answer["name"] == answer["game_name"]:
        plain = _game_label(ui, canon, answer["game_price"])
        if old != plain and ui.parse_coin(old) is not None:
            entry["text"] = plain
            if write:
                write("label: {!r} -> {!r} ({}; back to the game's own)".format(old, plain, canon))
        return
    new = ui.fill_template(answer["label"], name=answer["name"], price=answer["price"])
    if new != old:
        entry["text"] = new
        store["ours"].add(new)
        if write:
            write("label: {!r} -> {!r} ({})".format(old, new, canon))


def install_inn(ctx, write=None):
    """宿代の関所を立てる。宿代の答えか段を置いた MOD が `apply()` の中で呼ぶ。

    何本の MOD が呼んでも、1つの世代につき包みは1組（`install` と同じ形）。
    記録は最後に呼んだ MOD のログへ書く。
    """
    from . import choices as _choices
    from . import ui as _ui
    store = _inn_store()
    store["write"] = write
    screen = _ui.Screen(ctx, write, tag="inn price", mark="mod_inn_price")

    def log(line):
        target = store.get("write")
        if target:
            target(line)

    def refresh(app, buttons):
        try:
            if isinstance(buttons, (list, tuple)):
                for entry in buttons:
                    if isinstance(entry, dict) and _ui.spec_cls_name(entry) == INN_START:
                        _relabel_room(_ui, app, entry, log)
        except Exception:
            log_exc("prices: cannot relabel the inn rooms")

    def intercept(app, entry, button_index):
        """手持ちが宿代に満たないときは、押した時点で断る（部屋選びはそのまま残る）。"""
        try:
            if not isinstance(entry, dict) or _ui.spec_cls_name(entry) != INN_START:
                return False
            argv = _ui.spec_args(entry)
            if not argv or len(argv) < 2:
                return False
            answer = _room_answer(app, argv[1])
            if answer is None or answer["price"] == answer["game_price"]:
                return False
            gold = _ui.gold_of(app)
            if gold is not None and gold < answer["price"]:
                log("refused: {} price {} > gold {}".format(argv[1], answer["price"], gold))
                screen.say(app, _ui.fill_template(INN_REFUSE_TEXT, name=answer["name"],
                                                  price=answer["price"], gold=gold))
                return True
        except Exception:
            log_exc("prices: inn price check failed")
        return False

    _choices.provide(ctx, screen, refresh=refresh, intercept=intercept, owner=_INN_OWNER)

    generation = getattr(ctx, "generation", None)
    if store.get("generation") == generation and store.get("installed"):
        return
    store["generation"] = generation
    store["installed"] = True

    def start_init(orig, self, app=None, months=None, quality=None, *args, **kwargs):
        """`execute` には引数が来ないので、部屋をここで自分に控える（ゲームのデータには書かない）。"""
        result = orig(self, app, months, quality, *args, **kwargs)
        try:
            setattr(self, _INN_MARK, str(quality))
        except Exception:
            pass
        return result

    def settle(app, window):
        """宿泊の開始が終わった時点の帳尻。引き落としが本当に起きたかだけを見る。"""
        before, prepaid, price = window["gold_before"], window["prepaid"], window["price"]
        if prepaid is None:
            return
        after = _ui.gold_of(app)
        if after is None:
            log("WARN price: cannot re-read the gold; leaving it as is")
        elif after == before - price:
            log("price: charged {} in one deduction; gold {} -> {} ({})".format(
                price, before, after, window["quality"]))
        elif after == prepaid:
            _ui.set_gold(app, before, on_error=lambda msg: log("WARN price: " + msg))
            log("price: the game did not charge inside execute; gold back to {} ({}) "
                "-- the charge happens elsewhere in this build; run 218_probe_vacation".format(
                    before, window["quality"]))
        else:
            log("WARN price: gold ended at {} (adjusted {}, expected {}); leaving it as is "
                "({})".format(after, prepaid, before - price, window["quality"]))

    def start_execute(orig, self, *args, **kwargs):
        """宿泊の開始の間だけ、宿代の前払い調整をする（連泊の `まだ宿泊する` も同じ窓を通る）。"""
        app = getattr(self, "app", None) or _ui.find_app()
        window = None
        try:
            quality = getattr(self, _INN_MARK, None)
            answer = _room_answer(app, quality) if quality is not None else None
            if answer is not None and answer["price"] != answer["game_price"]:
                before = _ui.gold_of(app)
                if before is not None:
                    window = {"quality": quality, "gold_before": before, "prepaid": None,
                              "price": answer["price"]}
                    pre = before + answer["game_price"] - answer["price"]
                    if _ui.set_gold(app, pre) is None:
                        log("WARN price: cannot write the gold; not pre-adjusted")
                    else:
                        window["prepaid"] = pre
                        log("price: gold {} -> {} before the game charges {} (ours is {}; "
                            "one deduction, no refund)".format(
                                before, pre, answer["game_price"], answer["price"]))
        except Exception:
            log_exc("prices: cannot pre-adjust the inn price")
        try:
            return orig(self, *args, **kwargs)
        finally:
            if window is not None:
                try:
                    settle(app, window)
                except Exception:
                    log_exc("prices: cannot settle the inn price")

    ctx.wrap("__main__:{}.__init__".format(INN_START), required=False, safe=True)(start_init)
    ctx.wrap("__main__:{}.execute".format(INN_START), required=False)(start_execute)
    log("prices: the inn price gate was declared")


# ---------------------------------------------------------------------------
# MOD が決める値段（アイテムの売買額）の関所
# ---------------------------------------------------------------------------
#
# 上の窓口（`declare` / `ask`）が**ゲームが決めている額**を1枚だけ差し替えるのに対し、
# こちらはアイテムの `attributes` に書かれる**売買額**を段で組み直す。
#
#     base    1枚だけ勝つ。品から額を組む（`129_balance_item_price` の式）
#     adjust  何枚でも乗る。組まれた額へ倍率を掛ける（`405_regional_economy` の地域倍率）
#
# **なぜ関所なのか。** 包む対象は8つ、値段を書く地点はその中に10ある。
# そして書く時点が地点ごとに違う（画面の2対象＝3地点は描く前なので `orig` の前、
# 残り6対象＝7地点は後）。
# 包みの勝敗は適用順ではなく `orig` の前に書くか後に書くかで決まるので、
# MOD どうしが別々に包むと、相手がどちらの層に居ても必ず半分の地点で負ける
# （TECH.md §3.3.1。実測は VERIFICATION.md §3.19.1 で、地域倍率が買値だけ9回とも消えていた）。
# 8箇所をここが1枚だけ包み、段の順序を宣言で決めれば、乗る側は前後を考えなくてよくなる。
#
# **段は覚えず、毎回組み直す。** 最終額はいつでも base から組み直せるので、
# 「前回自分が書いた額」を覚えなくてよい。保存の直前に一時の段を外すのも、
# 外して組み直すだけで済む。覚えるのは base が額を組めなかった品だけで、そこは台帳が持つ。

#: 値段の鍵。ゲームが持ち主に応じて付け替える（GAME.md §2.13.2）。
#: **鍵は新設しない。** 既にある鍵の値だけを書き換える。
BUY = "買価"
SELL = "売価"
ITEM_PRICE_KEYS = (BUY, SELL)

#: ゲームが1品の値段を書く地点。`orig` の**後**で通る（直後の額が素の額）。
GAME_WRITE_TARGETS = (
    ("__main__:InstantaleApp.set_shop_price_for_owner", "shop_owner"),
    ("__main__:InstantaleApp.set_shop_price_for_player", "shop_player"),
    ("__main__:InstantaleApp.generate_item_from_item_data", "generated"),
    ("__main__:InstantaleApp.generate_item_from_dict", "generated/dict"),
    ("__main__:InstantaleApp.generate_item_from_ready_made_data",
     "generated/ready_made"),
)

#: 持ち物ひとまとまりを均す地点（同じく `orig` の後）。
NORMALIZE_TARGET = "__main__:InstantaleApp.normalize_shop_inventory_prices"

#: 画面へ出す地点。`orig` の**前**に書く（描画は `attributes` をそのまま読む）。
#: 既にセーブに在る品はこの地点でしか通らないので、`on_sight` を頼んだ宣言が
#: 1つでもあれば包む。
SIGHT_TARGETS = (
    ("__main__:InstantaleApp.toggle_twin_inventory_window", "window"),
    ("scripts.hud.new_hud:ItemDetailBox.update_content", "detail"),
)

#: 保存の地点。ここでは一時の段を外した額を書き、戻ってきたら戻す。
SAVE_TARGETS = ("__main__:InstantaleApp.save_game",
                "save_world_json:write_obfuscated_json_file")

_ITEM_ATTR = "_instantale_item_prices"
_ITEM_GATE_ATTR = "_instantale_item_prices_gate"

# 台帳と段の読み書きは保存の経路からも通るので錠を持つ。
_LOCK = threading.RLock()


def _price_number(value):
    """値段として読めれば float、読めなければ None（文字列で入ることがある）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if number >= 0 else None


def read_item(item):
    """`Item` インスタンスからもセーブの辞書からも、同じ形で読む。

    セーブを直に読む場面（検査・別 MOD）でも同じ関数を通せるようにしてある。
    """
    if isinstance(item, dict):
        attributes = item.get("attributes")

        def field(name):
            return item.get(name)
    else:
        attributes = getattr(item, "attributes", None)

        def field(name):
            return getattr(item, name, None)

    if not isinstance(attributes, dict):
        attributes = {}
    return field, attributes


class _Ledger:
    """触った品ごとの控え。中身は `{"game": {鍵: 素の額}, "written": {鍵: 書いた額}}`。

    **弱参照で持つ。** 品が消えたら控えも一緒に消えるので、
    街を渡り歩いても台帳が伸び続けない。
    `scripts.items:Item` を弱参照できるかは凍結されたゲームの中の話で手元からは
    確かめられないため、駄目だった場合は id の台帳へ落ちる
    （そちらは現物への強参照を持つ＝品が解放されなくなる）。
    **どちらを使ったかは一度だけログに出す。** 次の実機の1行で決着させるため。
    """

    def __init__(self):
        self.weak = weakref.WeakKeyDictionary()
        self.strong = {}
        self.mode = None

    def _announce(self, mode, write):
        if self.mode == mode:
            return
        self.mode = mode
        if not write:
            return
        if mode == "weakref":
            write("prices: the item ledger holds items by weak reference")
        else:
            write("WARN prices: Item cannot be held weakly; the ledger keeps "
                  "strong references (items will not be freed)")

    def put(self, item, entry, write=None):
        try:
            self.weak[item] = entry
        except TypeError:
            self.strong[id(item)] = (item, entry)
            self._announce("id", write)
            return
        self._announce("weakref", write)

    def get(self, item):
        try:
            found = self.weak.get(item)
        except TypeError:
            found = None
        if found is not None:
            return found
        known = self.strong.get(id(item))
        return known[1] if known else None

    def touched(self):
        """控えのある品を `(品, 控え)` で並べる。保存の前後で使う。"""
        out = []
        try:
            out.extend(self.weak.items())
        except Exception:
            log_exc("prices: cannot walk the weak ledger")
        out.extend(self.strong.values())
        return out


def _item_registry():
    """`{"base": (持ち主, 関数, on_sight, write), "adjust": [...], "ledger": _Ledger}`。"""
    found = getattr(sys, _ITEM_ATTR, None)
    if not isinstance(found, dict) or not isinstance(found.get("adjust"), list):
        found = {"base": None, "adjust": [], "ledger": _Ledger()}
        setattr(sys, _ITEM_ATTR, found)
    ledger = found.get("ledger")
    if not isinstance(ledger, _Ledger):
        # 注入し直すとこのモジュールごと読み直され、前の世代の台帳は別のクラスの実体になる。
        # 中身は引き継ぐ（捨てると、一時の段を掛けた額を素の額として控え直して段が二重に掛かり、
        # 保存の前の外しからも漏れてセーブに焼き付いた）。
        fresh = _Ledger()
        weak = getattr(ledger, "weak", None)
        if isinstance(weak, weakref.WeakKeyDictionary):
            fresh.weak = weak
            strong = getattr(ledger, "strong", None)
            fresh.strong = strong if isinstance(strong, dict) else {}
            fresh.mode = getattr(ledger, "mode", None)
        found["ledger"] = fresh
    return found


def declare_base(owner, fn, on_sight=True, write=None):
    """値段を組む式を置く。1枚だけ勝つ。置き換えたら前の持ち主を返す。

    `fn(item)` は `{BUY: 額, SELL: 額}`（`"axis"` を足してもよい。記録に出る）か、
    **組めなければ None** を返す。None の品はゲームが付けた額が軸になる。

    `on_sight` が真なら、既にセーブに在る品を画面に出た時点でも付け直す
    （包むのは `SIGHT_TARGETS`。頼んだ宣言が1つも無ければ包まない）。
    """
    if not callable(fn):
        raise TypeError("declare_base() needs a callable, got {!r}".format(type(fn)))
    with _LOCK:
        registry = _item_registry()
        previous = registry.get("base")
        registry["base"] = (str(owner or ""), fn, bool(on_sight), write)
    before = previous[0] if previous else None
    if write and before and before != str(owner or ""):
        write("prices: item prices are now built by {!r} (was {!r})".format(
            owner, before))
    return before


def adjust(owner, fn, temporary=False, write=None):
    """組まれた額へ乗せる段を足す。**同じ持ち主は1枚まで**（再注入で重ねない）。

    `fn(item, key, price)` は新しい額か、**触らないなら None** を返す。
    `key` は `BUY` か `SELL`、`price` はそこまでの段を通した額。

    `temporary` が真なら、保存の直前にこの段を外した額を書く
    （その土地でだけ効く倍率のように、セーブへ焼き付けてはいけない段）。
    """
    if not callable(fn):
        raise TypeError("adjust() needs a callable, got {!r}".format(type(fn)))
    name = str(owner or "")
    with _LOCK:
        registry = _item_registry()
        layers = registry["adjust"]
        layers[:] = [layer for layer in layers if layer[0] != name]
        layers.append((name, fn, bool(temporary), write))
    # apply のたびに段を置き直すので、同じ持ち主・同じ種類なら書かない（注入1回に1度）。
    if write and _adjust_written.get(name) != bool(temporary):
        _adjust_written[name] = bool(temporary)
        write("prices: {!r} adjusts item prices ({})".format(
            name, "temporary" if temporary else "saved"))


def item_price_sources():
    """いま値段を決めている持ち主。`("式の持ち主", ["段の持ち主", ...])`。"""
    with _LOCK:
        registry = _item_registry()
        base = registry.get("base")
        return (base[0] if base else "",
                [layer[0] for layer in registry["adjust"]])


def forget_item_prices(owner, write=None):
    """その持ち主が置いた式と段を外す。外したものの名前を返す。"""
    name = str(owner or "")
    gone = []
    with _LOCK:
        registry = _item_registry()
        base = registry.get("base")
        if base and base[0] == name:
            registry["base"] = None
            gone.append("item_price_base")
        layers = registry["adjust"]
        if any(layer[0] == name for layer in layers):
            layers[:] = [layer for layer in layers if layer[0] != name]
            gone.append("item_price_adjust")
    if write and gone:
        write("prices: {!r} no longer decides {}".format(name, gone))
    return gone


durations.on_forget(forget_item_prices)


def _name_of(item):
    """記録に出す品の見出し。`'名前'/細分/レア度`。"""
    field, attributes = read_item(item)
    return "{!r}/{}/{}".format(field("name"), attributes.get("item_detail"),
                               field("rarity"))


def _layers(registry, temporary):
    """通す段。`temporary` が偽なら一時の段を抜く。"""
    return [layer for layer in registry["adjust"]
            if temporary or not layer[2]]


def _axis_prices(item, attributes, registry, ledger, from_game, write):
    """段を乗せる前の軸 `{鍵: 額}` と、記録に出す軸の名前。

    式が組めた品はその額を軸にし、組めなかった品は**ゲームが付けた額**を軸にする。
    ゲームの額はその品を初めて見たときに控える（そのときはまだこちらが書いていない
    ので、必ず素の額が読める）。ゲームが後から付け直した回は、
    こちらが最後に書いた額と食い違うことで見分けて控え直す
    ― 食い違いを見ずに `from_game` だけで控え直すと、
    ゲームが書かなかった回にこちらの額を素の額として飲み込む。
    """
    entry = registry.get("base")
    if entry is not None:
        owner, fn, _on_sight, _write = entry
        try:
            built = fn(item)
        except Exception:
            log_exc("prices: the base from {!r} failed".format(owner))
            built = None
        if isinstance(built, dict):
            axis = built.get("axis")
            return ({key: _price_number(built.get(key))
                     for key in ITEM_PRICE_KEYS if key in attributes},
                    axis if isinstance(axis, str) else "base")

    known = ledger.get(item)
    if not isinstance(known, dict):
        known = {"game": {}, "written": {}}
    game, written = known["game"], known["written"]
    for key in ITEM_PRICE_KEYS:
        if key not in attributes:
            continue
        current = _price_number(attributes.get(key))
        if current is None:
            continue
        mine = written.get(key)
        fresh = mine is None or abs(current - mine) >= 0.5
        if key not in game or (from_game and fresh):
            game[key] = current
    ledger.put(item, known, write=write)
    return (dict(game), "game")


def _apply_one(item, why, from_game, temporary=True):
    """1品の値段を組み直して書く。書き換えた鍵の数を返す。

    **既にある鍵だけを、変わったときだけ書く。** 鍵を新設すると
    「店でもないのに売価が付いた品」ができ、セーブの形も変わる。
    """
    if item is None:
        return 0
    _field, attributes = read_item(item)
    if not attributes:
        return 0
    notes = []
    write = None
    with _LOCK:
        registry = _item_registry()
        base = registry.get("base")
        if base is None and not registry["adjust"]:
            return 0
        write = registry.get("write")
        if temporary and registry.get("saving", 0) > 0:
            # 保存は別スレッドで走る。その窓の間に画面や生成の地点が通っても
            # 一時の段を書き戻さない（セーブへ焼き付く）。窓が閉じた後の
            # 組み直しは `saving` の戻しが控えのある品を全部やり直す。
            temporary = False
        ledger = registry["ledger"]
        axis_prices, axis = _axis_prices(item, attributes, registry, ledger,
                                         from_game, write)
        layers = _layers(registry, temporary)
        known = ledger.get(item)
        if not isinstance(known, dict):
            known = {"game": {}, "written": {}}
        changed = 0
        for key in ITEM_PRICE_KEYS:
            if key not in attributes:
                continue
            price = axis_prices.get(key)
            if price is None:
                continue
            applied = []
            for owner, fn, _temp, _w in layers:
                try:
                    got = _price_number(fn(item, key, price))
                except Exception:
                    log_exc("prices: the adjust from {!r} failed".format(owner))
                    continue
                if got is not None and abs(got - price) >= 0.0001:
                    applied.append(owner)
                    price = got
            final = int(round(price))
            if final < 0:
                continue
            known["written"][key] = float(final)
            current = _price_number(attributes.get(key))
            if current is not None and abs(current - final) < 0.5:
                continue
            notes.append("{} {} {}: {} -> {}  [{}{}]".format(
                why, key, _name_of(item), attributes.get(key), final, axis,
                "".join(" +" + owner for owner in applied)))
            attributes[key] = final
            changed += 1
        ledger.put(item, known, write=write)
    # 記録は錠を放してから。ログの書き込みで保存側を待たせない。
    base_write = base[3] if base else None
    if base_write is not None:
        for line in notes:
            base_write(line)
    return changed


def _apply_inventory(obtainer, why, from_game):
    """持ち物ひとまとまりを組み直す。持ち物が引けなければ何もしない。"""
    inventory = getattr(obtainer, "inventory", None)
    if isinstance(inventory, dict):
        items = list(inventory.values())
    elif isinstance(inventory, (list, tuple)):
        items = list(inventory)
    else:
        return 0
    return sum(1 for item in items if _apply_one(item, why, from_game))


def _rewrite_touched(temporary, why):
    """控えのある品を全部組み直す。保存の前後で使う。書き換えた鍵の数を返す。"""
    with _LOCK:
        touched = _item_registry()["ledger"].touched()
    return sum(_apply_one(item, why, from_game=False, temporary=temporary)
               for item, _entry in touched)


def refresh(why="refresh"):
    """控えのある品を組み直す。**段の答えが変わった側が呼ぶ。**

    段は品ごとの事情で答えを変える（`405_regional_economy` は街が変わると
    倍率が変わり、検品が済むとスコアが付く）。関所はゲームが値段に触った
    地点でしか動かないので、次にそこを通るまで古い額が画面に残る。
    自分の答えが変わったと分かっている側から、ここで押す。

    書き換えた鍵の数を返す。
    """
    return _rewrite_touched(temporary=True, why=why)


def item_gate():
    """関所の状態。`{"generation", "targets"}` か、立っていなければ None。"""
    done = getattr(sys, _ITEM_GATE_ATTR, None)
    return done if isinstance(done, dict) else None


def install(ctx, write=None):
    """値段の関所を立てる。包んだ対象の名前を返す。

    値段に触る MOD が `apply()` の中で呼ぶ。何本の MOD が呼んでも、
    1つの世代につき関所は1つ（`durations.install` と同じ形。TECH.md §5.8）。

    ここが8つの対象を1枚だけ包む。**地点ごとの `orig` の前後はここが引き受ける**
    ので、式を置く側も段を置く側も層のことを考えなくてよい。
    """
    from . import patch as _patch
    _patch.note_gate_writer("prices", write)
    with _LOCK:
        _item_registry()["write"] = write
    generation = getattr(ctx, "generation", None)
    done = item_gate()
    if done is not None and done.get("generation") == generation:
        return list(done.get("targets") or [])

    targets = []

    # ---- ゲームが1品へ書く地点。`orig` の**後**で組み直す ------------------
    for target, why in GAME_WRITE_TARGETS:
        if target.endswith("set_shop_price_for_owner") or \
                target.endswith("set_shop_price_for_player"):
            def priced(orig, self, item_instance=None, *args,
                       _why=why, **kwargs):
                result = orig(self, item_instance, *args, **kwargs)
                _apply_one(item_instance, _why, from_game=True)
                return result

            ctx.wrap(target, required=False, safe=True)(priced)
        else:
            def generated(orig, self, *args, _why=why, **kwargs):
                result = orig(self, *args, **kwargs)
                _apply_one(result, _why, from_game=True)
                return result

            ctx.wrap(target, required=False, safe=True)(generated)
        targets.append(target)

    def normalize(orig, self, shop_obtainer=None, player_obtainer=None,
                  *args, **kwargs):
        result = orig(self, shop_obtainer, player_obtainer, *args, **kwargs)
        _apply_inventory(shop_obtainer, "normalize/shop", from_game=True)
        _apply_inventory(player_obtainer, "normalize/player", from_game=True)
        return result

    ctx.wrap(NORMALIZE_TARGET, required=False, safe=True)(normalize)
    targets.append(NORMALIZE_TARGET)

    # ---- 画面へ出す地点。`orig` の**前**に書く -----------------------------
    # 上の経路は「これから作られる品」と「ゲームが値付けし直す品」しか通らない。
    # 既にセーブに在る持ち物は元の額のまま残るので、画面に出た時点で付け直す。
    def twin_window(orig, self, left_inventory_obtainer=None,
                    right_inventory_obtainer=None, left_label_text=None,
                    situation=None, *args, **kwargs):
        if _wants_sight():
            _apply_inventory(left_inventory_obtainer, "window/left",
                             from_game=False)
            _apply_inventory(right_inventory_obtainer, "window/right",
                             from_game=False)
        return orig(self, left_inventory_obtainer, right_inventory_obtainer,
                    left_label_text, situation, *args, **kwargs)

    ctx.wrap(SIGHT_TARGETS[0][0], required=False, safe=True)(twin_window)
    targets.append(SIGHT_TARGETS[0][0])

    def detail_box(orig, self, item=None, *args, **kwargs):
        if _wants_sight():
            _apply_one(getattr(item, "item_instance", None) or item,
                       "detail", from_game=False)
        return orig(self, item, *args, **kwargs)

    ctx.wrap(SIGHT_TARGETS[1][0], required=False, safe=True)(detail_box)
    targets.append(SIGHT_TARGETS[1][0])

    # ---- 保存 --------------------------------------------------------------
    # 一時の段（その土地でだけ効く倍率など）を外した額を書いてから保存し、
    # 戻ってきたら戻す。**控えた軸から組み直す**ので、保存の最中に画面側が
    # 先に掛け直していても倍率が積み上がらない。
    def saving(orig, *args, **kwargs):
        # **入れ子を見張る。** `save_game` の中から保存の実体
        # （`write_obfuscated_json_file`）が呼ばれる経路があり、数えずに戻すと
        # 内側の「戻す」が外側の保存の最中に一時の段を書き戻す。
        # 別スレッドの保存とも重なりうるので、抜けるときは入った値へ書き戻さず
        # 1つ減らし、最後に抜けた側が戻す。先に入った側が先に抜けても、
        # まだ書き出し中の保存へ一時の段が入らず、数も 0 へ戻る。
        with _LOCK:
            registry = _item_registry()
            depth = registry.get("saving", 0)
            registry["saving"] = depth + 1
        stripped = 0
        if depth == 0:
            try:
                stripped = _rewrite_touched(temporary=False, why="save/strip")
            except Exception:
                log_exc("prices: could not strip temporary prices before saving")
        try:
            return orig(*args, **kwargs)
        finally:
            with _LOCK:
                registry = _item_registry()
                left = max(0, registry.get("saving", 0) - 1)
                registry["saving"] = left
            if left == 0:
                try:
                    _rewrite_touched(temporary=True, why="save/restore")
                except Exception:
                    log_exc("prices: could not restore temporary prices "
                            "after saving")
            # 剥がした側が記録する。最後に抜けるのが剥がした側とは限らない。
            if write and stripped:
                write("prices: {} temporary price(s) were kept out of "
                      "the save".format(stripped))

    for target in SAVE_TARGETS:
        ctx.wrap(target, required=False, safe=True)(saving)
        targets.append(target)

    setattr(sys, _ITEM_GATE_ATTR, {"generation": generation,
                                   "targets": list(targets)})
    global _gate_written
    if write and _gate_written != tuple(targets):
        _gate_written = tuple(targets)
        write("prices: the item price gate was declared on {} target(s)".format(
            len(targets)))
    return targets


def _wants_sight():
    """画面に出た品を付け直すよう頼まれているか（`declare_base(on_sight=...)`）。"""
    with _LOCK:
        base = _item_registry().get("base")
    return bool(base and base[2])
