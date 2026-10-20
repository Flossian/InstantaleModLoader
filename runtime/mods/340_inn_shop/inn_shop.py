# -*- coding: utf-8 -*-
"""宿屋で食べ物・飲み物・酒を売り買いできるようにする。

##### 何が足りないのか

ゲームの宿屋の `Facility.choices` は `出る` の1つだけで、店の `売買する` が無い
（`232_probe_facility_choices` の実測。GAME.md §2.2）。
施設の `config` も `level_of_detail` だけで、品揃えの雛形（`goods`）を持たない（GAME.md §2.13.1）。

##### 直し方。「入口を足して、ゲームの売買に任せる」

1. **宿屋の `choices` に、店と同じ `売買する` を足す。**
   値は店の施設が持っている `choices["売買する"]` の写し（ゲームが組んだもの）。
   ボタンを組むのも、押した後の品揃えの生成・値付け・確認の窓・交渉もゲーム自身が行う。
   ボタンは `売買する / 出る / 宿泊する / 会話する` で組まれ、`135_fix_inn_button_order` が
   `宿泊する` を `出る` の前へ動かす。
   `choices` はセーブに焼かれない（施設の素データに鍵が無い）ので、外せば次の読み込みで消える。
   足すのは `Facility.__init__` の後（新しく生まれた施設）と、選択肢の組み直しの前
   （注入より前に生まれていた施設。世界ごとに1回見回る）。
   MOD が建てた宿（`mod:` の施設か、主が `mod:` の NPC）には足さない。
   素データの `world_dict` に施設か主が無い宿屋（セーブエディタで足したもの）では、組み直しの前に入口を外す。
   ただし、ローダの窓口 `save_lookup` に売買を直している MOD（`146_fix_save_only_entities`）が名乗っていれば外さない
   （その MOD が引き先をセーブ側にして売買を開けるようにする）。
   ゲームの売買は `world_dict` から引くので、押すと `KeyError` でスレッドが落ちる（GAME.md §2.28。
   2026-10-19 に実プレイの世界で踏んだ。直し方の記録は `discontinued\\906_fix_new_shop_data`）。
   宿屋の主との会話のボタンにも、店の主との会話と同じく `売買する` を先頭に入れる
   （ゲームは会話に足す入口を施設の種類で決め打ちしていて、宿屋は入っていない）。
2. **宿屋で品揃えを作る間だけ、ゲームの指示文の1文を差し替える。**
   ゲームの指示文は武器と防具を必ず1つずつ作らせる
   （`shop_item_generator_ordinary` / `shop_additional_item_generator_ordinary`。exe の定数で読んだ。GAME.md §2.13.1.2）。
   宿屋で `ShoppingStartManagerRemake.execute` が走っている間だけ、ローダの送り口（`llm.wrap_outgoing`）で
   その1文を「食料・飲み物・酒を中心に」の文へ替える。ほかの頼みには当たらない（1文の一致を見る）。
3. **それでも返ってきた武器・防具・素材は棚に入れない。**
   同じ間に `generate_item_in_shopping` へ渡る品の `item_category.type` を見て、該当すれば作らない。
   `405_regional_economy` の特産品は 405 が自分の層の内側を呼ぶので、ここを通らない（品目は 405 のまま）。

売る側（プレイヤーが宿屋の主に売る）は、ゲームのまま何でも売れる。

##### 他の MOD との噛み合い

- `336_crime_overhaul` の店で盗むは、施設の `choices` に `売買する` があるかで店を見分ける。
  宿屋の `choices` に足すので、宿屋の棚からも盗める
- `312_shop_restock` の入れ替えは、宿屋（`facility_type == "inn"`）なら食べ物と飲み物で作り直す（312 側の分岐）
- `331_facility_investment` の売上は店の種類（`general_store` / `specialty_shop` / `blacksmith`）だけを数える。宿屋の売買は数えない
"""

import copy
import sys
import time
import weakref

from instantale_modloader import choices, frames, llm, modfacility, save_lookup, ui

LOG_BASENAME = "inn_shop.log"

#: 再注入しても1組だけ持つ（写した入口と、見回った世界）。
STORE_ATTR = "_instantale_inn_shop_store"

INN_TYPE = "inn"
#: 店の入口の文言。`Facility.choices` の鍵（GAME.md §2.2）。
SHOP_CHOICE = "売買する"
SHOP_CLS = "ShoppingStartManagerRemake"
#: ローダの窓口 `save_lookup` の場面の名前。直している MOD（`146_`）が居れば、素データに無い宿屋でも入口を出す。
SHOP_SITE = "shopping"
#: 施設の `出る`（ゲームの移動）。宿屋の画面かの目印。
MOVE_CLS = "MovePhaseManager"
#: 宿屋の `宿泊する`。
STAY_CLS = "DisplayVacationChoice"
MOD_PREFIX = "mod:"

FACILITY_INIT = "__main__:Facility.__init__"
SHOP_TARGET = "__main__:ShoppingStartManagerRemake.execute"
ITEM_TARGET = "__main__:ShoppingStartManagerRemake.generate_item_in_shopping"

#: 宿屋の棚に入れない品の種類（`item_category.type`）。
DROP_TYPES = ("weapon", "wearable", "material")

#: 宿屋で品揃えを作る間だけ差し替える、ゲームの指示文の1文（exe の定数の写し）。
PROMPT_REPLACEMENTS = (
    # `shop_item_generator_ordinary`（初めて品揃えを作るとき）
    ("必ず最低バリュー段階の武器と防具をそれぞれ一つずつ生成し、"
     "また許可されたリストの各段階ごとの回復アイテムを一つずつ生成する。"
     "それに加えて自由にいくつか作成する。",
     "ここは宿なので、並べる品は食料・飲み物・酒が中心になる。武器・防具・素材は作らない。"
     "許可されたリストの各段階ごとに、食料か飲み物（酒を含む）の回復アイテムを一つずつ生成する。"
     "それに加えて、この宿で出てきそうな料理・飲み物・酒を自由にいくつか作成する。"),
    # `shop_additional_item_generator_ordinary`（売買のたびに足す品）
    ("必ず武器と防具をそれぞれ一つずつ生成する。",
     "ここは宿なので、武器・防具・素材は作らない。"
     "この宿で出てきそうな料理と、飲み物か酒をそれぞれ一つずつ生成する。"),
)

#: 品揃えの間の目印がこれより古ければ、差し替えない（`execute` が戻らなかった回の取り残し）。
BATCH_STALE_SECONDS = 600


def _get(obj, key, default=None):
    """辞書でも属性でも読む。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    value = frames.attr(obj, key, default)
    return default if value is frames.MISSING else value


def category_of(item_data):
    """品の `item_category.type`（小文字）。読めなければ空文字。"""
    category = _get(item_data, "item_category")
    kind = _get(category, "type") if category is not None else None
    if kind is None:
        kind = _get(item_data, "item_type")
    return str(kind).strip().lower() if kind is not None else ""


def rewrite_text(text):
    """差し替えの1文を全部当てる。`(新しい文, 当てた数)`。"""
    hits = 0
    for old, new in PROMPT_REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
            hits += 1
    return text, hits


def apply(ctx):
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "entry": None,          # 店の `choices["売買する"]` の写し
            "pending": None,        # 入口がまだ写せていない間に生まれた宿屋
            "swept": None,          # 見回った世界（弱い参照。id は解放後の番地の使い回しで別の世界と一致する）
            "batch": None,          # 宿屋で品揃えを作っている間の目印
        }
        setattr(sys, STORE_ATTR, store)
    store.setdefault("withheld", set())   # 入口を外した (世界, 施設) の控え（ログを1度だけ出す）
    if store.get("pending") is None:
        try:
            store["pending"] = weakref.WeakSet()
        except Exception:
            store["pending"] = set()

    write = ctx.logger(LOG_BASENAME, cap=2000)

    # ------------------------------------------------------------ 宿屋を見分ける
    def facility_id(facility):
        return str(_get(facility, "id", "") or "")

    def is_game_inn(facility):
        """ゲームが作った宿屋か（MOD が建てた宿は除く）。"""
        if ui.facility_type_of(facility) != INN_TYPE:
            return False
        if modfacility.is_mod_facility(facility_id(facility)):
            return False
        owner = _get(facility, "owner")
        return owner is not None and not str(owner).startswith(MOD_PREFIX)

    def current_facility(app):
        """プレイヤーが今いる施設。ロード直後は id の文字列なので引き当て直す。"""
        location = _get(_get(app, "player"), "location")
        if location is None:
            return None
        if not isinstance(location, str):
            return location
        return ui.find_facility(ui.current_area(app), location)[0]

    # ------------------------------------------------------------ 入口を足す
    def learn_entry(facility):
        """店の施設から `売買する` の値を写す。写せたら真。"""
        if store["entry"] is not None:
            return True
        found = _get(facility, "choices")
        if not isinstance(found, dict) or SHOP_CHOICE not in found:
            return False
        if modfacility.is_mod_facility(facility_id(facility)):
            return False
        store["entry"] = found[SHOP_CHOICE]
        write("entry: copied {!r} from {} ({}) -> {}".format(
            SHOP_CHOICE, facility_id(facility), ui.facility_type_of(facility),
            type(store["entry"]).__name__))
        return True

    def add_entry(facility):
        """宿屋の `choices` の先頭に `売買する` を足す。足したら真。"""
        found = _get(facility, "choices")
        if not isinstance(found, dict) or SHOP_CHOICE in found:
            return False
        if store["entry"] is None:
            try:
                store["pending"].add(facility)
            except Exception:
                pass
            return False
        try:
            entry = copy.copy(store["entry"])
        except Exception:
            entry = store["entry"]
        # 施設ごとに新しい dict にする（同じ dict を他の施設と共有していても巻き込まない）。
        merged = {SHOP_CHOICE: entry}
        merged.update(found)
        try:
            facility.choices = merged
        except Exception:
            ctx.log_exc("inn shop: cannot add the shop entry")
            return False
        return True

    def flush_pending():
        """入口を写す前に生まれた宿屋へ足す。足した数。"""
        if store["entry"] is None:
            return 0
        waiting = list(store["pending"])
        store["pending"].clear()
        return sum(1 for facility in waiting
                   if is_game_inn(facility) and add_entry(facility))

    def all_facilities(app):
        for area in ui.world_areas(app).values():
            for node in ui.nodes_of(area):
                for facility in ui.facilities_of(node).values():
                    yield facility

    def swept(world):
        """`world` を見回り済みか。"""
        held = store["swept"]
        if isinstance(held, weakref.ref):
            return held() is world
        return held is not None and held == id(world)

    def sweep(app):
        """注入より前に生まれていた施設を、世界ごとに1回見回る。"""
        world = _get(app, "world")
        if world is None or swept(world):
            return
        facilities = list(all_facilities(app))
        if not facilities:
            return
        for facility in facilities:
            if learn_entry(facility):
                break
        if store["entry"] is None:
            # 店が1軒も無い世界。次の組み直しで見直す（新しい施設は `__init__` の側が拾う）。
            return
        try:
            store["swept"] = weakref.ref(world)
        except TypeError:
            store["swept"] = id(world)
        added = sum(1 for facility in facilities
                    if is_game_inn(facility) and add_entry(facility))
        added += flush_pending()
        write("sweep: {} inn(s) got {!r} ({} facilities)".format(
            added, SHOP_CHOICE, len(facilities)))

    @ctx.wrap(FACILITY_INIT, required=False, safe=True)
    def facility_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if store["entry"] is None and learn_entry(self):
                flush_pending()
            if is_game_inn(self):
                add_entry(self)
        except Exception:
            ctx.log_exc("inn shop: cannot look at a new facility")
        return result

    def in_world_dict(app, facility):
        """施設と主が世界の素データに在るか。読めなければ None（止めない）。

        ゲームの売買は施設と主を `world_dict` から引く。セーブエディタで足した施設は
        `save_data_dict` にしか無く、「売買する」を押すと `KeyError` でスレッドが落ちる（GAME.md §2.28）。
        たどるのはローダの窓口 `save_lookup.in_world`（`146_` と同じ判断。差し替えの間も本来の世界を見る）。
        """
        area = ui.current_area(app)
        found, node = ui.find_facility(area, facility_id(facility))
        node_id = modfacility.node_id_of(node)
        area_id = ui.area_id_of(area) if area is not None else None
        if found is None or not node_id or not area_id:
            return None
        here = save_lookup.in_world(app, "areas", area_id, "nodes", node_id, "facilities", facility_id(facility))
        if here is not True:
            return here
        owner = _get(facility, "owner")
        if owner is None:
            return True
        return save_lookup.in_world(app, "npcs", str(owner))

    def withdraw_entry(app, facility, buttons):
        """入口を外す（施設の `choices` と、組み直す前のボタン）。"""
        found = _get(facility, "choices")
        if isinstance(found, dict) and SHOP_CHOICE in found:
            rest = {key: value for key, value in found.items() if key != SHOP_CHOICE}
            try:
                facility.choices = rest
            except Exception:
                ctx.log_exc("inn shop: cannot withdraw the shop entry")
        for target in (buttons, _get(app, "buttons")):
            if isinstance(target, list):
                target[:] = [entry for entry in target
                             if ui.spec_cls_name(entry) != SHOP_CLS]

    def offer_now(app, facility, buttons):
        """宿屋の画面のボタンに入口が欠けていれば、先頭に入れる。

        ゲームは施設に入るときに `choices` からボタンを組むので、後から足した入口はその画面に出ない
        （注入したときに宿屋に居る・素データが後から揃った）。ロードは保存に焼かれたボタンを戻すだけなので、
        入口を足す前に保存した宿屋の画面にも出ない（GAME.md §2.3）。
        宿屋の画面（ゲームの `宿泊する` と `出る` が並んでいる）のときだけ入れる。
        """
        target = _get(app, "buttons")
        target = target if isinstance(target, list) else buttons
        if not isinstance(target, list):
            return
        names = [ui.spec_cls_name(entry) for entry in target]
        if SHOP_CLS in names or MOVE_CLS not in names or STAY_CLS not in names:
            return
        try:
            spec = copy.copy(facility.choices[SHOP_CHOICE])
        except Exception:
            return
        target.insert(0, {"text": SHOP_CHOICE, "spec": spec})
        write("offered {!r} on the current screen at inn {}".format(SHOP_CHOICE, facility_id(facility)))

    def offer_in_conversation(app, facility, buttons):
        """宿屋の主との会話のボタンに入口が欠けていれば、先頭に入れる。

        ゲームは会話を始めるとき（`ConversationStartManager.conversation_start_method_3`）に、
        店の主なら `売買する` を会話のボタンの先頭に足す。足す施設の種類は決め打ちで
        （exe の定数は `general_store` / `specialty_shop` / `medical_facility`）、宿屋は入っていない。
        店と同じく、話している相手（「会話を終了する」の `args[0]`）が今いる宿屋の主のときだけ入れる。
        """
        target = _get(app, "buttons")
        target = target if isinstance(target, list) else buttons
        if not isinstance(target, list):
            return
        partner, _end = ui.conversation_partner(target)
        if partner is None or partner != str(_get(facility, "owner")):
            return
        if any(ui.spec_cls_name(entry) == SHOP_CLS for entry in target):
            return
        try:
            spec = copy.copy(facility.choices[SHOP_CHOICE])
        except Exception:
            return
        target.insert(0, {"text": SHOP_CHOICE, "spec": spec})
        write("offered {!r} in the conversation with owner {} at inn {}".format(
            SHOP_CHOICE, partner, facility_id(facility)))

    def refresh_buttons(app, buttons):
        """組み直しの前。世界が替わっていれば見回り、今いる宿屋に入口が無ければ足す。

        素データに無い宿屋（セーブエディタで足したもの）では入口を外す。
        """
        try:
            sweep(app)
            facility = current_facility(app)
            if facility is None or not is_game_inn(facility):
                return
            if in_world_dict(app, facility) is False and not save_lookup.covered(SHOP_SITE):
                key = (id(_get(app, "world")), facility_id(facility))
                if key not in store["withheld"]:
                    store["withheld"].add(key)
                    write("withheld at inn {} {!r}: not in world_dict (GAME.md 2.28)".format(
                        facility_id(facility), ui.facility_name(app, facility)))
                withdraw_entry(app, facility, buttons)
                return
            add_entry(facility)
            offer_now(app, facility, buttons)
            offer_in_conversation(app, facility, buttons)
        except Exception:
            ctx.log_exc("inn shop: cannot sweep the inns")

    # 選択肢はローダの窓口 `choices`（TECH.md §3.3.14）に預ける。ボタンは足さない（ゲームが `choices` から組む）。
    choices.provide(ctx, None, refresh=refresh_buttons)

    # ------------------------------------------------------------ 品揃えを作る間
    def live_batch():
        batch = store.get("batch")
        if batch is None:
            return None
        if time.time() - batch["since"] > BATCH_STALE_SECONDS:
            return None
        return batch

    @ctx.wrap(SHOP_TARGET, required=False, safe=True)
    def shopping_start(orig, self, *args, **kwargs):
        app = _get(self, "app") or ui.find_app()
        facility = None
        try:
            facility = current_facility(app)
        except Exception:
            ctx.log_exc("inn shop: cannot read the current facility")
        if facility is None or not is_game_inn(facility):
            return orig(self, *args, **kwargs)
        batch = {"since": time.time(), "facility": facility_id(facility),
                 "owner": str(_get(facility, "owner")), "rewrites": 0, "dropped": []}
        store["batch"] = batch
        write("shopping at inn {} {!r} owner={}".format(
            batch["facility"], ui.facility_name(app, facility), batch["owner"]))
        try:
            return orig(self, *args, **kwargs)
        finally:
            if store.get("batch") is batch:
                store["batch"] = None
            goods = _get(_get(facility, "config"), "goods")
            kinds = [category_of(entry) for entry in goods] if isinstance(goods, list) else []
            stray = [kind for kind in kinds if kind in DROP_TYPES]
            write("done at inn {}: rewrites={} dropped={} goods={}{}".format(
                batch["facility"], batch["rewrites"], len(batch["dropped"]), len(kinds),
                " WARN goods hold {}".format(stray) if stray else ""))

    def rewrite(texts, site):
        batch = live_batch()
        if batch is None:
            return None
        out, hits = [], 0
        for text in texts:
            new, count = rewrite_text(text)
            out.append(new)
            hits += count
        if not hits:
            return None
        batch["rewrites"] += hits
        write("prompt: replaced {} sentence(s) at {}".format(hits, site))
        return out

    llm.wrap_outgoing(ctx, rewrite, label="inn shop")

    @ctx.wrap(ITEM_TARGET, required=False, safe=True)
    def keep_inn_goods(orig, self, *args, **kwargs):
        # 引数はそのまま渡す。名前付きで受けて渡し直すと、呼び手が省いた引数を None で上書きする。
        batch = live_batch()
        if batch is not None:
            item_data = frames.arg(args, kwargs, "item_data", 0)
            kind = category_of(item_data)
            if kind in DROP_TYPES:
                name = _get(item_data, "item_name") or _get(item_data, "name") or "?"
                batch["dropped"].append(name)
                write("dropped {!r} ({}) at inn {}".format(name, kind, batch["facility"]))
                return None
        return orig(self, *args, **kwargs)

    ctx.log("inn shop: inns get {!r}; goods lean to food and drink; log goes to out/{}".format(
        SHOP_CHOICE, LOG_BASENAME))
