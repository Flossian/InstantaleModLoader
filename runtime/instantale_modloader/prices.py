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


def inn_room(app, quality, write=None):
    """宿屋の部屋1回の値段。**分からなければ None**（呼ぶ側が別の道へ落とす）。

    知らない `quality` で None を返すのは、ゲームの更新で語彙が変わったときに
    **当て推量の額を前払いしない**ため。0 と「分からない」は別の答えにする。
    """
    answer = durations.ask(INN_ROOM, app, write=write, quality=quality)
    if answer is None:
        return game_inn_room(quality)
    price = answer.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        if write:
            write("WARN prices: inn_room from {!r} returned {!r}, not a number; "
                  "using the game's own value".format(source_of(INN_ROOM), price))
        return game_inn_room(quality)
    return max(0, int(price))


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
    if not isinstance(found.get("ledger"), _Ledger):
        found["ledger"] = _Ledger()
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
    if write:
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
                _item_registry()["saving"] = depth
            if depth == 0:
                try:
                    _rewrite_touched(temporary=True, why="save/restore")
                except Exception:
                    log_exc("prices: could not restore temporary prices "
                            "after saving")
                if write and stripped:
                    write("prices: {} temporary price(s) were kept out of "
                          "the save".format(stripped))

    for target in SAVE_TARGETS:
        ctx.wrap(target, required=False, safe=True)(saving)
        targets.append(target)

    setattr(sys, _ITEM_GATE_ATTR, {"generation": generation,
                                   "targets": list(targets)})
    if write:
        write("prices: the item price gate was declared on {} target(s)".format(
            len(targets)))
    return targets


def _wants_sight():
    """画面に出た品を付け直すよう頼まれているか（`declare_base(on_sight=...)`）。"""
    with _LOCK:
        base = _item_registry().get("base")
    return bool(base and base[2])
