# -*- coding: utf-8 -*-
r"""計測: 店の値切りで決まった額が、品のどこに書かれ、いつまで残るかを録る。ゲームは変えない。

##### 何を決めるための計測か

売買の窓の入力欄に書いた文は、ゲームの `ShoppingNegotiateManager` が受け取り、
LLM の `shopping_negotiator` が `player_attempt` を返す。値切りなら
`bargaining`（`trade_direction` / `target_item` / `decision` / `new_offer`）で、
exe の定数はその後に `reload_twin_inventory`（売買の一覧の描き直し）が続く。
値段の MOD（`129_` / `336_` / `338_` / `341_` / `405_`）を入れると、
値切った額が効かないという指摘がある。

ローダの値段の関所（`instantale_modloader/prices.py`）は、品が画面に出たときと
保存の前後に `買価` / `売価` を組み直す。ゲームが関所を通らずに書いた額は、
そこで消えるはず（ゲーム抜きの模擬では消えた）。分かっていないのは次の3つ:

    録れていない   ゲームは new_offer をどこへ書くか（品の attributes の鍵か、別の所か）
    録れていない   素のゲームで、その額はいつまで残るか（店を出る・保存・ロード・付け直し）
    録れていない   関所があるとき、どの地点で元へ戻るか（実機で）

##### 録るもの

| 何を録るか | 見どころ |
| --- | --- |
| `shopping_negotiator` | 送った文と、返った `player_attempt` |
| `ShoppingNegotiateManager.execute` / `shopping_negotiate_method` | 前後で動いた品の `買価` / `売価`（店と手持ちの両方）、所持金、app とマネージャの浅い属性の増減 |
| 値切った品の額 | 一覧の描き直し・売買の窓・品の詳細・売買・付け直し・保存・窓を閉じる、のそれぞれの後で、決まった額から動いたか |
| 値段の関所 | いま式と段を置いている MOD（`prices.item_price_sources()`） |

値切った品は id で追う（`sys` に置くので、注入し直しや読み直しをまたいでも追える）。
ロードの後は現物が別になるので、手持ちと店の持ち物から id で引き直す（両方を1回なめて id の表を作る）。
記録の件数（`SAMPLES`）も `sys` に置き、1プロセスで数える。上限に届いたら追跡もやめる。
値切った品の売買の行だけは別の上限（`WATCHED_SAMPLES`）で、`SAMPLES` の後も書く。

200番台の約束どおり読み取りだけ。引数は受け取った形のまま `orig` へ渡す。
`safe=True` と握り潰しで、記録に失敗しても本体は必ず1回呼ぶ。

出力は `out/shop_negotiation.log`（読む用）と `out/shop_negotiation.jsonl`（1件1行）。
"""

import datetime
import sys

from instantale_modloader import frames, ui

LOG_BASENAME = "shop_negotiation.log"
RECORD_BASENAME = "shop_negotiation.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
SAMPLES = 400

#: 値切った品の追跡。`{品の id: {...}}`。注入し直しても続けて追うので `sys` に置く。
WATCH_ATTR = "_probe_shop_negotiation_watch"
WATCH_LIMIT = 40

#: 書いた行の数。`{"lines": n, "watched": n}`。1プロセスで数えるので `sys` に置く。
SEEN_ATTR = "_instantale_probe_shop_negotiation_seen"
#: 値切った品の売買の行の上限（`SAMPLES` とは別に数える）。
WATCHED_SAMPLES = 100

#: 値段の鍵（`prices.ITEM_PRICE_KEYS` と同じ。ゲームが持ち主に応じて付け替える）。
PRICE_KEYS = ("買価", "売価")

TRADE_SITUATION = "shop"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def watch_store():
    found = getattr(sys, WATCH_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, WATCH_ATTR, found)
    return found


def seen_store():
    found = getattr(sys, SEEN_ATTR, None)
    if not (isinstance(found, dict) and isinstance(found.get("lines"), int)
            and isinstance(found.get("watched"), int)):
        found = {"lines": 0, "watched": 0}
        setattr(sys, SEEN_ATTR, found)
    return found


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    seen = seen_store()
    state = {"left": None, "depth": 0}

    def take(budget):
        limit = WATCHED_SAMPLES if budget == "watched" else SAMPLES
        if seen[budget] >= limit:
            return False
        seen[budget] += 1
        return True

    def emit(phase, line, budget="lines", **fields):
        """1行書く。`budget` は数える枠（`lines` は `SAMPLES`、`watched` は `WATCHED_SAMPLES`）。"""
        if not take(budget):
            return
        write("{}: {}".format(phase, line))
        row = {"at": now(), "phase": phase, "line": line}
        row.update(fields)
        record(row)

    # ------------------------------------------------------------ ゲームを読む
    def app_of(holder):
        app = frames.attr(holder, "app", None)
        return app if app is not None else ui.find_app()

    def inventory_of(obtainer):
        if obtainer is None:
            return None
        inventory = frames.attr(obtainer, "inventory", None)
        if isinstance(inventory, dict):
            return inventory
        inner = frames.attr(inventory, "inventory", None)
        return inner if isinstance(inner, dict) else None

    def name_of(value):
        name = frames.attr(value, "name", None)
        return name if isinstance(name, str) and name else "?"

    def who(obtainer):
        if obtainer is None:
            return "-"
        return "{}({})".format(name_of(obtainer), frames.attr(obtainer, "id", "?"))

    def shop_owner(app):
        """売買の窓の左（開いたときに控えた相手）。無ければ今いる施設の主。"""
        if state["left"] is not None:
            return state["left"]
        location = frames.attr(frames.attr(app, "player", None), "location", None)
        try:
            facility = (ui.find_facility(ui.current_area(app), location)[0]
                        if isinstance(location, str) else location)
        except Exception:
            facility = None
        owner = frames.attr(facility, "owner", None) if facility is not None else None
        return ui.character_of(app, str(owner)) if owner is not None else None

    def sides(app):
        player = frames.attr(app, "player", None)
        return (("店", shop_owner(app)), ("手持ち", player))

    def price_table(app):
        """`{(側, 鍵, 品の id): (品, {鍵: 額})}`。額の鍵が無い品は載せない。"""
        out = {}
        for side, obtainer in sides(app):
            inventory = inventory_of(obtainer)
            if not isinstance(inventory, dict):
                continue
            for key, item in inventory.items():
                attributes = frames.attr(item, "attributes", None)
                if not isinstance(attributes, dict):
                    continue
                values = {k: attributes.get(k) for k in PRICE_KEYS if k in attributes}
                if values:
                    out[(side, str(key), str(frames.attr(item, "id", key)))] = (item, values)
        return out

    def shallow(obj):
        """浅い属性の要約。入れ物は長さだけ（世界の辞書を丸写ししない）。"""
        out = {}
        try:
            members = vars(obj)
        except TypeError:
            return out
        for name, value in list(members.items()):
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[name] = frames.short(value, 80)
            elif isinstance(value, (dict, list, tuple, set)):
                out[name] = "<{} {}>".format(type(value).__name__, len(value))
            else:
                out[name] = "<{}>".format(type(value).__name__)
        return out

    def shallow_diff(before, after):
        parts = []
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                parts.append("{}: {} -> {}".format(name, before.get(name), after.get(name)))
        return parts

    def gate_sources():
        try:
            from instantale_modloader import prices
            return prices.item_price_sources()
        except Exception:
            return None

    # ------------------------------------------------------------ 値切った品の追跡
    def watched_items(app, wanted):
        """`{品の id: (側, 品)}`。店と手持ちを1回ずつなめ、追っている id だけ載せる（先に見た側を取る）。"""
        found = {}
        for side, obtainer in sides(app):
            inventory = inventory_of(obtainer)
            if not isinstance(inventory, dict):
                continue
            for item in list(inventory.values()):
                item_id = str(frames.attr(item, "id", ""))
                if item_id in wanted and item_id not in found:
                    found[item_id] = (side, item)
        return found

    def check(where, app=None):
        """値切った品の額が、前に見た額から動いたら書く。"""
        if seen["lines"] >= SAMPLES:
            return      # 書けないので持ち物もなめない
        watch = watch_store()
        if not watch:
            return
        try:
            app = app or ui.find_app()
            table = watched_items(app, watch)
            for item_id, entry in list(watch.items()):
                side, item = table.get(item_id, (None, None))
                if item is None:
                    if entry.get("last") != "missing":
                        entry["last"] = "missing"
                        emit("追跡", "{} {!r}({}): 店にも手持ちにも見えない".format(
                            where, entry["name"], item_id), id=item_id, where=where)
                    continue
                attributes = frames.attr(item, "attributes", None) or {}
                values = {k: attributes.get(k) for k in PRICE_KEYS if k in attributes}
                current = (side, values)
                if entry.get("last") != current:
                    entry["last"] = current
                    emit("追跡", "{} {!r}({}) {} {}  値切りで {} {}".format(
                        where, entry["name"], item_id, side, values,
                        entry["key"], entry["negotiated"]),
                        id=item_id, where=where, side=side, values=values,
                        negotiated={entry["key"]: entry["negotiated"]})
        except Exception:
            ctx.log_exc("shop negotiation probe: cannot check the watched items")

    # ------------------------------------------------------------ 値切りの境目
    def around(label, holder, call):
        """値切りの処理の前後で動いた額を録る。入れ子の内側は数えない。"""
        state["depth"] += 1
        before = None
        if state["depth"] == 1 and seen["lines"] < SAMPLES:
            try:
                app = app_of(holder)
                before = {"app": app, "prices": price_table(app),
                          "gold": frames.attr(frames.attr(app, "player", None), "gold", None),
                          "app_attrs": shallow(app), "self_attrs": shallow(holder)}
            except Exception:
                before = None
                ctx.log_exc("shop negotiation probe: cannot read the state before")
        try:
            return call()
        finally:
            state["depth"] -= 1
            if before is not None:
                try:
                    after_prices = price_table(before["app"])
                    changes = []
                    watch = watch_store()
                    for slot, (item, values) in after_prices.items():
                        old = before["prices"].get(slot, (None, {}))[1]
                        for key in PRICE_KEYS:
                            if key in values and values.get(key) != old.get(key):
                                changes.append("{} {} {!r}({}) {}: {} -> {}".format(
                                    slot[0], slot[1], name_of(item), slot[2], key,
                                    old.get(key), values.get(key)))
                                if slot[2] in watch or len(watch) < WATCH_LIMIT:
                                    watch[slot[2]] = {"name": name_of(item), "key": key,
                                                      "negotiated": values.get(key),
                                                      "last": (slot[0], dict(values))}
                    gone = [slot for slot in before["prices"] if slot not in after_prices]
                    gold = frames.attr(frames.attr(before["app"], "player", None), "gold", None)
                    app_diff = shallow_diff(before["app_attrs"], shallow(before["app"]))
                    self_diff = shallow_diff(before["self_attrs"], shallow(holder))
                    gate = gate_sources()
                    emit("値切り", "{}: 額 {} / 消えた品 {} / 所持金 {} -> {} / app {} / "
                         "マネージャ {} / 関所 {}".format(
                             label, changes or "動かず", gone or "-", before["gold"], gold,
                             app_diff or "-", self_diff or "-", gate),
                         where=label, changes=changes, app_diff=app_diff,
                         self_diff=self_diff, gate=gate)
                except Exception:
                    ctx.log_exc("shop negotiation probe: cannot record a negotiation")

    @ctx.wrap("__main__:ShoppingNegotiateManager.execute", required=False, safe=True)
    def negotiate_execute(orig, self, *args, **kwargs):
        try:
            emit("入力", "execute choice_text={!r}".format(
                frames.short(frames.arg(args, kwargs, "choice_text", 0), 120)))
        except Exception:
            ctx.log_exc("shop negotiation probe: cannot record the input")
        return around("execute", self, lambda: orig(self, *args, **kwargs))

    @ctx.wrap("__main__:ShoppingNegotiateManager.shopping_negotiate_method",
              required=False, safe=True)
    def negotiate_method(orig, self, *args, **kwargs):
        return around("shopping_negotiate_method", self, lambda: orig(self, *args, **kwargs))

    @ctx.wrap("scripts.llm.llm_manager:shopping_negotiator", required=False, safe=True)
    def negotiator(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        try:
            attempt = (result.get("player_attempt") if isinstance(result, dict)
                       else frames.attr(result, "player_attempt", result))
            shown = frames.repr_value(attempt)
            emit("LLM", "player_input={!r} -> player_attempt {}".format(
                frames.short(frames.arg(args, kwargs, "player_input", 7), 120),
                frames.short(shown, 600)),
                attempt=shown[:1200])
        except Exception:
            ctx.log_exc("shop negotiation probe: cannot record the negotiator result")
        return result

    # ------------------------------------------------------------ 追跡する地点（どれも orig の後）
    def after(label):
        def hook(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            check(label, self)
            return result
        return hook

    @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window", required=False, safe=True)
    def twin_window(orig, self, *args, **kwargs):
        try:
            if frames.arg(args, kwargs, "situation", 3) == TRADE_SITUATION:
                state["left"] = frames.arg(args, kwargs, "left_inventory_obtainer", 0)
                emit("売買の窓", "左={} 右={} 関所 {}".format(
                    who(state["left"]),
                    who(frames.arg(args, kwargs, "right_inventory_obtainer", 1)),
                    gate_sources()))
        except Exception:
            ctx.log_exc("shop negotiation probe: cannot record the trade window")
        result = orig(self, *args, **kwargs)
        check("toggle_twin_inventory_window", self)
        return result

    for target, label in (
            ("__main__:InstantaleApp.normalize_shop_inventory_prices", "normalize"),
            ("__main__:InstantaleApp.set_shop_price_for_owner", "set_shop_price_for_owner"),
            ("__main__:InstantaleApp.set_shop_price_for_player", "set_shop_price_for_player"),
            ("__main__:InstantaleApp.save_game", "save_game"),
            ("__main__:InstantaleApp.close_shopping_window_process", "close_shopping")):
        ctx.wrap(target, required=False, safe=True)(after(label))

    # HUD の側は app を持たないので、check が引き直す。
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.reload_twin_inventory",
              required=False, safe=True)
    def reload_twin(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        check("reload_twin_inventory")
        return result

    @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content", required=False, safe=True)
    def detail_box(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        check("detail")
        return result

    def trade(label):
        """売買の1回。品の額と所持金の前後（実際に引かれた額）を出す。"""
        def hook(orig, self, *args, **kwargs):
            item, item_id, watched, player, gold, attributes = None, "", False, None, None, None
            try:
                item = frames.arg(args, kwargs, "item_instance", 0)
                item_id = str(frames.attr(item, "id", ""))
                watched = item_id in watch_store()
                player = frames.attr(self, "player", None)
                gold = frames.attr(player, "gold", None)
                found = frames.attr(item, "attributes", None)
                attributes = dict(found) if isinstance(found, dict) else {}
            except Exception:
                ctx.log_exc("shop negotiation probe: cannot read before a trade")
            result = orig(self, *args, **kwargs)
            try:
                # 値切った品の行は別の枠で数え、`SAMPLES` の後も書く
                if attributes is not None and (watched or seen["lines"] < SAMPLES):
                    emit("売買", "{} {!r}({}) 額 {} 所持金 {} -> {}{}".format(
                        label, name_of(item), item_id,
                        {k: attributes.get(k) for k in PRICE_KEYS if k in attributes},
                        gold, frames.attr(player, "gold", None),
                        "  （値切った品）" if watched else ""),
                        budget="watched" if watched else "lines",
                        id=item_id, watched=watched)
                check(label, self)
            except Exception:
                ctx.log_exc("shop negotiation probe: cannot record a trade")
            return result
        return hook

    ctx.wrap("__main__:InstantaleApp.buy_item", required=False, safe=True)(trade("buy_item"))
    ctx.wrap("__main__:InstantaleApp.sell_item", required=False, safe=True)(trade("sell_item"))

    ctx.log("shop negotiation probe: watching ShoppingNegotiateManager; "
            "records go to out/{} and out/{}".format(LOG_BASENAME, RECORD_BASENAME))
