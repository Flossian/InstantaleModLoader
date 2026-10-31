# -*- coding: utf-8 -*-
"""宿屋と鍛冶屋でも売買でき、店の種類ごとに品揃えの傾向が付く。

##### 何が足りないのか

ゲームの宿屋の `Facility.choices` は `出る` だけ、鍛冶屋は `装備の強化 / 出る` で、店の `売買する` が無い
（`232_probe_facility_choices` の実測。GAME.md §2.2）。
品揃えはどの店でも同じプロンプトで作られ、「武器と防具を1つずつ必ず作る」と頼んでいる
（`shop_item_generator_ordinary` / `shop_additional_item_generator_ordinary`。exe の定数で読んだ。GAME.md §2.13.1.2）。
診療所や香水店にも武器と防具が並ぶ。

##### 直し方。「入口を足して、ゲームの売買に任せる」

1. **宿屋と鍛冶屋の `choices` に、店と同じ `売買する` を足す。**
   値は店の施設が持っている `choices["売買する"]` の写し（ゲームが組んだもの）。
   ボタンを組むのも、押した後の品揃えの生成・値付け・確認の窓・交渉もゲーム自身が行う。
   宿屋のボタンは `売買する / 出る / 宿泊する / 会話する` で組まれ、`135_fix_inn_button_order` が
   `宿泊する` を `出る` の前へ動かす。鍛冶屋は `売買する / 装備の強化 / 出る / 会話する`。
   `choices` はセーブに焼かれない（施設の素データに鍵が無い）ので、外せば次の読み込みで消える。
   足すのは `Facility.__init__` の後（新しく生まれた施設）と、選択肢の組み直しの前
   （注入より前に生まれていた施設。世界ごとに1回見回る）。
   MOD が建てた施設（`mod:` の施設か、主が `mod:` の NPC）には足さない。
   素データの `world_dict` に施設か主が無い施設（セーブエディタで足したもの）では、組み直しの前に入口を外す。
   ただし、ローダの窓口 `save_lookup` に売買を直している MOD（`146_fix_save_only_entities`）が名乗っていれば外さない
   （その MOD が引き先をセーブ側にして売買を開けるようにする）。
   ゲームの売買は `world_dict` から引くので、押すと `KeyError` でスレッドが落ちる（GAME.md §2.28。
   2026-10-19 に実プレイの世界で踏んだ。直し方の記録は `discontinued\\906_fix_new_shop_data`）。
   主との会話のボタンにも、店の主との会話と同じく `売買する` を先頭に入れる
   （ゲームは会話に足す入口を施設の種類で決め打ちしていて、宿屋と鍛冶屋の売買は入っていない。GAME.md §2.5）。
   主との会話の LLM（`conversation_facilitator` / `_after_retrieval`）にも、店の主と同じ返答の種類
   `selling_response` と、その説明の1行を足す（`SELLS` の一文を添える）。
   ゲームは返答の種類（`response_list`）と説明の文（`response_prompt_instruction`）も施設の種類で決め打ちしていて、
   宿屋の主は宿泊、鍛冶屋の主は装備の強化しか知らず、「ここは買い物をする店ではない」と答えていた（GAME.md §2.24）。
2. **品揃えを作る間だけ、ゲームのプロンプトの1文を店の種類ごとの文へ差し替える。**
   ゲームの店（雑貨屋・専門店・診療所・鍛冶屋・宿屋）で `ShoppingStartManagerRemake.execute` が走っている間だけ、
   ローダの送り口（`llm.wrap_outgoing`）で「武器と防具を必ず作る」の1文を `LINEUPS` の文へ替える。
   ほかの頼みには当たらない（1文の一致を見る）。
   傾向は「中心」を頼むだけで、ほかの品が混じるのは止めない（鍛冶屋に回復薬が並んでもよい）。
   ただし設定 `MIXED_GEAR` を切ると、宿屋と診療所（`GEARLESS_TYPES`）では「武器と防具は作らない」と頼み、
   それでも返ってきた武器・防具（`GEAR_TYPES`）は `generate_item_in_shopping` で作らない（棚に入れない）。
   雛形のある店は LLM を呼ばず雛形から段の品を並べる（`set_item_from_world_data`）ので、そこで入った武器・防具も外す。
   初めて並べた棚から武器・防具を外したときは、同じ間に傾向の品を `FILL_MIN`〜`FILL_MAX` 品足す
   （自分で LLM に頼み、ゲームの生成入口 `generate_item_from_item_data` で作る。312 の入れ替えと同じ形）。
   `405_regional_economy` の特産品は 405 が自分で作るので、傾向は掛からない。
   世界の一括生成の店の仕事（`GameGenerationBackend.generate`。ゲームの売買の外で同じプロンプトを使う）でも、
   仕事の `ids` が指す施設の種類で同じ差し替えを掛ける。品揃えの雛形がある店は売買を開いても LLM を呼ばず、
   雛形から作り直すだけなので（GAME.md §2.13.1.3）、一括生成で作った雛形に傾向が無いと後から付かない。
3. **入れ替え（`312_shop_restock`）にも同じ傾向を使わせる。**
   傾向の一文（`LINEUPS[...]["tendency"]`）と、並べない品の種類（`MIXED_GEAR` を切ったときの武器・防具）を
   ローダの窓口 `shop_lineup`（TECH.md §3.3.22）に出す。312 は自分の頼みを組むときにそれを聞く。

売る側（プレイヤーが主に売る）は、ゲームのまま何でも売れる。

##### 他の MOD との噛み合い

- `336_crime_overhaul` の店で盗むは、施設の `choices` に `売買する` があるかで店を見分ける。
  宿屋と鍛冶屋の `choices` に足すので、その棚からも盗める
- `331_facility_investment` の持ち株の施設は MOD の施設なので、入口も傾向も掛けない（331 が自分で売買を開く）
"""

import collections
import copy
import random
import sys
import time
import typing
import weakref

from instantale_modloader import choices, frames, llm, modfacility, save_lookup, shop_lineup, ui, value_limits

# ---------------------------------------------------------------- 設定（mod.json）
#: 店の種類に合わない武器・防具も並べる（ゲームの「武器と防具を1つずつ」の名残）。
#: 切ると宿屋と診療所では武器・防具を作らない（頼みで止め、返ってきても棚に入れない）。
MIXED_GEAR = True

OWNER = "340_shop_lineup"
LOG_BASENAME = "shop_lineup.log"

#: 再注入しても1組だけ持つ（写した入口と、見回った世界）。
STORE_ATTR = "_instantale_shop_lineup_store"

#: 店の入口の文言。`Facility.choices` の鍵（GAME.md §2.2）。
SHOP_CHOICE = "売買する"
SHOP_CLS = "ShoppingStartManagerRemake"
#: ローダの窓口 `save_lookup` の場面の名前。直している MOD（`146_`）が居れば、素データに無い施設でも入口を出す。
SHOP_SITE = "shopping"
#: 施設の `出る`（ゲームの移動）。施設の画面かの目印。
MOVE_CLS = "MovePhaseManager"
MOD_PREFIX = "mod:"

#: `売買する` を足す施設の種類と、その施設の画面に並ぶゲーム自身の入口（施設の画面かの目印）。
ENTRY_TYPES = {
    "inn": "DisplayVacationChoice",              # 宿泊する
    "blacksmith": "EquipmentReinforcementStart",  # 装備の強化
}

#: 主との会話の LLM と、返答の種類・説明の文の位置（recon の署名。0 起点）。
TALK_TARGETS = {
    "scripts.llm.llm_manager:conversation_facilitator": (8, 10),
    "scripts.llm.llm_manager:conversation_facilitator_after_retrieval": (8, 9),
}
SELLING_TYPE = "selling_response"
#: ゲームが店の主に添える説明の1行（exe の定数の写し）と、その後に続くゲームの行の頭。
SELLING_LINE = "  - 売買の提案の場合: selling_responseとして返答を記述。"
OTHER_LINE = "  - それ以外のあらゆる要求や取引"
#: 宿屋と鍛冶屋の主に添える、売り買いしている品の一文。
SELLS = {
    "inn": "この宿では料理・飲み物・酒と少しの回復薬を売っていて、客の品の買い取りもする。",
    "blacksmith": "この鍛冶屋では武器と防具を売っていて、客の品の買い取りもする。",
}

FACILITY_INIT = "__main__:Facility.__init__"
SHOP_TARGET = "__main__:ShoppingStartManagerRemake.execute"
ITEM_TARGET = "__main__:ShoppingStartManagerRemake.generate_item_in_shopping"
#: 品揃えの雛形（`config['goods']`）からその段の品を並べる入口（初めて開いた店。GAME.md §2.13.1）。
TEMPLATE_TARGET = "__main__:ShoppingStartManagerRemake.set_item_from_world_data"
#: 世界の一括生成の仕事1件（`scripts.world_bulk_*` は「開始／再開」の後に読み込まれ、ローダが当て直す。GAME.md §2.27）。
BULK_TARGET = "scripts.world_bulk_backend:GameGenerationBackend.generate"
BULK_SHOP_KIND = "shop"

#: ゲームのプロンプトの、差し替える2文（exe の定数の写し）。
GAME_FIRST = ("必ず最低バリュー段階の武器と防具をそれぞれ一つずつ生成し、"
              "また許可されたリストの各段階ごとの回復アイテムを一つずつ生成する。"
              "それに加えて自由にいくつか作成する。")     # `shop_item_generator_ordinary`（初めて品揃えを作るとき）
GAME_MORE = "必ず武器と防具をそれぞれ一つずつ生成する。"  # `shop_additional_item_generator_ordinary`（売買のたびに足す品）

#: 店の種類ごとの品揃えの傾向。
#: tendency … 傾向の一文（窓口 `shop_lineup` で 312 の入れ替えにも渡す）
#: first    … `GAME_FIRST` の代わり（tendency の後ろに付ける、数と段の指示）
#: more     … `GAME_MORE` の代わり。None ならゲームの文のまま
LINEUPS = {
    "general_store": {
        "tendency": "ここは何でも扱う店なので、回復アイテム・道具・武器・防具・素材を偏らないように幅広く並べる。",
        "first": "許可されたリストの各段階ごとの回復アイテムを一つずつ生成し、"
                 "最低バリュー段階の武器と防具もそれぞれ一つずつ生成する。"
                 "それに加えて、道具や素材など、ほかの種類の品も自由にいくつか作成する。",
        "more": None,
    },
    "specialty_shop": {
        "tendency": "ここは専門店なので、並べる品は店の名前と説明に合うものが中心になる。"
                    "武器や防具は看板に合うときだけ作る。ほかの品が少し混じってもよい。",
        "first": "許可されたリストの各段階ごとに、店の看板に合う品を一つずつ生成する。"
                 "それに加えて、この店で扱っていそうな品を自由にいくつか作成する。",
        "more": "この店の看板に合う品を二つ生成する。",
    },
    "medical_facility": {
        "tendency": "ここは薬や手当ての品を扱う店なので、並べる品は回復薬・ポーション・薬・薬草が中心になる。"
                    "ほかの品が少し混じってもよい。",
        "first": "許可されたリストの各段階ごとの回復アイテムを一つずつ生成する。"
                 "それに加えて、ここで扱っていそうな回復薬・薬・手当ての品を自由にいくつか作成する。",
        "more": "ここで扱っていそうな回復薬と薬をそれぞれ一つずつ生成する。",
    },
    "blacksmith": {
        "tendency": "ここは鍛冶屋なので、並べる品は武器と防具が中心になる。"
                    "鍛冶に使う金属や鉱石、手入れの道具、回復アイテムなどが少し混じってもよい。",
        "first": "許可されたリストの各段階ごとに、武器か防具を一つずつ生成する。"
                 "最低バリュー段階の武器と防具もそれぞれ一つずつ生成する。"
                 "それに加えて、この鍛冶屋で打っていそうな武器・防具を自由にいくつか作成する。",
        "more": "この鍛冶屋で打っていそうな武器と防具をそれぞれ一つずつ生成する。",
    },
    "inn": {
        "tendency": "ここは宿なので、並べる品は料理・飲み物・酒が中心になる。"
                    "回復薬も少しだけ置き、ほかの品が少し混じってもよい。",
        "first": "許可されたリストの各段階ごとに、食料か飲み物（酒を含む）の回復アイテムを一つずつ生成する。"
                 "それに加えて、この宿で出てきそうな料理・飲み物・酒を自由にいくつか作成し、回復薬も一つか二つ作成する。",
        "more": "この宿で出てきそうな料理と、飲み物か酒をそれぞれ一つずつ生成する。",
    },
}

#: `MIXED_GEAR` を切ったときに武器・防具を並べない店の種類と、その品の種類（`item_category.type`）。
#: 専門店は看板が武器・防具のこともある（鍛冶屋の名前の専門店がある）ので、種類では止めない。
GEARLESS_TYPES = ("inn", "medical_facility")
GEAR_TYPES = ("weapon", "wearable")
NO_GEAR = "武器と防具は作らない。"

#: 武器・防具を外して棚が寂しくなった店に、傾向の品を足す数（その都度この幅でランダムに決める）。
FILL_MIN = 2
FILL_MAX = 6
#: 足す品の頼み。記録の名前・待つ秒数。
FILL_MANAGER = "mod_shop_lineup"
FILL_TIMEOUT = 90
#: 足す品の種類（`312_shop_restock` と同じ語彙。ゲームの生成入口 `generate_item_from_item_data` の細分）。
#: 武器・防具は頼まない。
FILL_TYPES = {
    "healing_item": ("food", "drink", "herb", "medicine", "potion"),
    "consumable": ("food", "drink", "herb", "medicine", "potion", "scroll"),
    "utility": ("tool", "document"),
    "material": ("creature_part", "ore", "gem", "treasure", "metal", "creature",
                 "plant", "mushroom", "relic", "scrap", "magical_material",
                 "other_material"),
}
RARITIES = ("common", "rare", "magical", "epic", "legendary", "mythic")
#: 頼みに書く価値の目盛りの上限。ゲームの難易度の上限（`QUEST_DIFFICULTY_VALUE_MAX`）と同じ 76。
#: ゲームの頼みは「最高が70」と書くが、雛形の価値は終盤の街で 76 まで出る。
#: 返ってきた価値を切る上限は窓口 `value_limits` から引く（素は 76。76 を超えた価値はゲームが値段と強さを
#: 76 の値で止める。VERIFICATION.md §3.112。`923_beyond_76` が入っていれば 100。TECH.md §3.3.26）。
VALUE_MAX = 76

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


def gear_dropped(facility_type, mixed=None):
    """その店の種類で武器・防具を並べないか（`MIXED_GEAR` を切っていて、宿屋か診療所）。"""
    mixed = MIXED_GEAR if mixed is None else mixed
    return not mixed and facility_type in GEARLESS_TYPES


def tendency_of(facility_type, mixed=None):
    """傾向の一文。武器・防具を並べない店なら、その一文を足す。傾向の無い種類は None。"""
    lineup = LINEUPS.get(facility_type)
    if lineup is None:
        return None
    return lineup["tendency"] + (NO_GEAR if gear_dropped(facility_type, mixed) else "")


def replacements(facility_type, mixed=None):
    """その店の種類で差し替える `(ゲームの文, 新しい文)` の組。傾向の無い種類は空。"""
    lineup = LINEUPS.get(facility_type)
    if lineup is None:
        return ()
    tendency = tendency_of(facility_type, mixed)
    pairs = [(GAME_FIRST, tendency + lineup["first"])]
    if lineup.get("more"):
        pairs.append((GAME_MORE, tendency + lineup["more"]))
    return tuple(pairs)


def rewrite_text(text, facility_type, mixed=None):
    """差し替えの1文を全部当てる。`(新しい文, 当てた数)`。"""
    hits = 0
    for old, new in replacements(facility_type, mixed):
        if old in text:
            text = text.replace(old, new)
            hits += 1
    return text, hits


def add_selling(response_list, instruction, facility_type):
    """返答の種類に `selling_response` を足し、説明の文に店と同じ1行を入れる。`(種類, 説明, 足したか)`。

    1行は「それ以外のあらゆる要求や取引」の行の前に入れる（無ければ末尾）。どちらも既にあれば触らない。
    """
    if not isinstance(response_list, (list, tuple)) or not isinstance(instruction, str):
        return response_list, instruction, False
    changed = False
    if SELLING_TYPE not in response_list:
        response_list = list(response_list) + [SELLING_TYPE]
        changed = True
    if SELLING_TYPE not in instruction:
        line = SELLING_LINE + SELLS.get(facility_type, "") + "\n"
        at = instruction.find(OTHER_LINE)
        if at < 0:
            instruction = instruction + ("" if not instruction or instruction.endswith("\n") else "\n") + line
        else:
            instruction = instruction[:at] + line + instruction[at:]
        changed = True
    return response_list, instruction, changed


def apply(ctx):
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "entry": None,          # 店の `choices["売買する"]` の写し
            "pending": None,        # 入口がまだ写せていない間に生まれた施設
            "swept": None,          # 見回った世界（弱い参照。id は解放後の番地の使い回しで別の世界と一致する）
            "batch": None,          # 店で品揃えを作っている間の目印
        }
        setattr(sys, STORE_ATTR, store)
    store.setdefault("withheld", set())   # 入口を外した (世界, 施設) の控え（ログを1度だけ出す）
    if store.get("pending") is None:
        try:
            store["pending"] = weakref.WeakSet()
        except Exception:
            store["pending"] = set()

    write = ctx.logger(LOG_BASENAME, cap=2000)

    # ------------------------------------------------------------ 施設を見分ける
    def facility_id(facility):
        return str(_get(facility, "id", "") or "")

    def is_game_facility(facility):
        """ゲームが作った施設か（MOD が建てた施設と、主が MOD の NPC の施設は除く）。"""
        if modfacility.is_mod_facility(facility_id(facility)):
            return False
        owner = _get(facility, "owner")
        return owner is not None and not str(owner).startswith(MOD_PREFIX)

    def takes_entry(facility):
        """`売買する` を足す施設か（ゲームの宿屋と鍛冶屋）。"""
        return ui.facility_type_of(facility) in ENTRY_TYPES and is_game_facility(facility)

    def has_lineup(facility):
        """品揃えに傾向を付ける施設か（ゲームの店）。"""
        return ui.facility_type_of(facility) in LINEUPS and is_game_facility(facility)

    def current_facility(app):
        """プレイヤーが今いる施設。ロード直後は id の文字列なので引き当て直す。"""
        location = _get(_get(app, "player"), "location")
        if location is None:
            return None
        if not isinstance(location, str):
            return location
        return ui.find_facility(ui.current_area(app), location)[0]

    def where(facility):
        return "{} {}".format(ui.facility_type_of(facility), facility_id(facility))

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
        """施設の `choices` の先頭に `売買する` を足す。足したら真。"""
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
            ctx.log_exc("shop lineup: cannot add the shop entry")
            return False
        return True

    def flush_pending():
        """入口を写す前に生まれた施設へ足す。足した数。"""
        if store["entry"] is None:
            return 0
        waiting = list(store["pending"])
        store["pending"].clear()
        return sum(1 for facility in waiting
                   if takes_entry(facility) and add_entry(facility))

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
        # 写しは世界ごとに取り直す（別のセーブを読んだ後に、前の世界の店から写した値を使わない）
        store["entry"] = None
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
        added = collections.Counter(ui.facility_type_of(facility) for facility in facilities
                                    if takes_entry(facility) and add_entry(facility))
        added.update({"pending": flush_pending()})
        write("sweep: {!r} added to {} ({} facilities)".format(
            SHOP_CHOICE, dict(+added) or "none", len(facilities)))

    @ctx.wrap(FACILITY_INIT, required=False, safe=True)
    def facility_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if store["entry"] is None and learn_entry(self):
                flush_pending()
            if takes_entry(self):
                add_entry(self)
        except Exception:
            ctx.log_exc("shop lineup: cannot look at a new facility")
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
                ctx.log_exc("shop lineup: cannot withdraw the shop entry")
        for target in (buttons, _get(app, "buttons")):
            if isinstance(target, list):
                target[:] = [entry for entry in target
                             if ui.spec_cls_name(entry) != SHOP_CLS]

    def offer_now(app, facility, buttons):
        """施設の画面のボタンに入口が欠けていれば、先頭に入れる。

        ゲームは施設に入るときに `choices` からボタンを組むので、後から足した入口はその画面に出ない
        （注入したときに施設に居る・素データが後から揃った）。ロードは保存に焼かれたボタンを戻すだけなので、
        入口を足す前に保存した施設の画面にも出ない（GAME.md §2.3）。
        施設の画面（ゲームの `出る` と、その施設の入口（`ENTRY_TYPES`）が並んでいる）のときだけ入れる。
        """
        target = _get(app, "buttons")
        target = target if isinstance(target, list) else buttons
        if not isinstance(target, list):
            return
        names = [ui.spec_cls_name(entry) for entry in target]
        mark = ENTRY_TYPES.get(ui.facility_type_of(facility))
        if SHOP_CLS in names or MOVE_CLS not in names or mark not in names:
            return
        try:
            spec = copy.copy(facility.choices[SHOP_CHOICE])
        except Exception:
            return
        target.insert(0, {"text": SHOP_CHOICE, "spec": spec})
        write("offered {!r} on the current screen at {}".format(SHOP_CHOICE, where(facility)))

    def offer_in_conversation(app, facility, buttons):
        """主との会話のボタンに入口が欠けていれば、先頭に入れる。

        ゲームは会話を始めるとき（`ConversationStartManager.conversation_start_method_3`）に、
        店の主なら `売買する` を会話のボタンの先頭に足す。足す施設の種類は決め打ちで
        （exe の定数は `general_store` / `specialty_shop` / `medical_facility`）、宿屋と鍛冶屋は入っていない
        （鍛冶屋は `装備の強化` だけ）。
        店と同じく、話している相手（「会話を終了する」の `args[0]`）が今いる施設の主のときだけ入れる。
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
        write("offered {!r} in the conversation with owner {} at {}".format(
            SHOP_CHOICE, partner, where(facility)))

    def refresh_buttons(app, buttons):
        """組み直しの前。世界が替わっていれば見回り、今いる宿屋・鍛冶屋に入口が無ければ足す。

        素データに無い施設（セーブエディタで足したもの）では入口を外す。
        """
        try:
            sweep(app)
            facility = current_facility(app)
            if facility is None or not takes_entry(facility):
                return
            if in_world_dict(app, facility) is False and not save_lookup.covered(SHOP_SITE):
                key = (id(_get(app, "world")), facility_id(facility))
                if key not in store["withheld"]:
                    store["withheld"].add(key)
                    write("withheld at {} {!r}: not in world_dict (GAME.md 2.28)".format(
                        where(facility), ui.facility_name(app, facility)))
                withdraw_entry(app, facility, buttons)
                return
            add_entry(facility)
            offer_now(app, facility, buttons)
            offer_in_conversation(app, facility, buttons)
        except Exception:
            ctx.log_exc("shop lineup: cannot sweep the facilities")

    # 選択肢はローダの窓口 `choices`（TECH.md §3.3.14）に預ける。ボタンは足さない（ゲームが `choices` から組む）。
    choices.provide(ctx, None, refresh=refresh_buttons)

    # ------------------------------------------------------------ 主との会話の LLM
    def selling_owner(character):
        """`character` が今いる宿屋・鍛冶屋の主で、入口を出している施設なら、その施設の種類。"""
        app = ui.find_app()
        facility = current_facility(app)
        if facility is None or not takes_entry(facility):
            return None
        found = _get(facility, "choices")
        if not isinstance(found, dict) or SHOP_CHOICE not in found:
            return None     # 入口を外した施設（素データに無い）
        owner = _get(facility, "owner")
        if ui.character_of(app, owner) is not character \
                and str(_get(character, "id", "")) != str(owner):
            return None
        return ui.facility_type_of(facility)

    def make_talk_hook(target, list_at, text_at):
        name = target.rsplit(":", 1)[1]

        @ctx.wrap(target, required=False, safe=True)
        def talk(orig, *args, **kwargs):
            try:
                kind = selling_owner(frames.arg(args, kwargs, "character_instance", 3))
                if kind is not None:
                    found = frames.arg(args, kwargs, "response_list", list_at)
                    text = frames.arg(args, kwargs, "response_prompt_instruction", text_at)
                    found, text, changed = add_selling(found, text, kind)
                    if changed:
                        args, kwargs, _ = frames.replace_arg(args, kwargs, "response_list", list_at, found)
                        args, kwargs, _ = frames.replace_arg(args, kwargs, "response_prompt_instruction", text_at, text)
                        write("talk: added {} to {} with the owner of {}".format(SELLING_TYPE, name, kind))
            except Exception:
                ctx.log_exc("shop lineup: cannot add the trade to the conversation")
            return orig(*args, **kwargs)
        return talk

    for target, (list_at, text_at) in TALK_TARGETS.items():
        make_talk_hook(target, list_at, text_at)

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
            ctx.log_exc("shop lineup: cannot read the current facility")
        if facility is None or not has_lineup(facility):
            return orig(self, *args, **kwargs)
        batch = {"since": time.time(), "facility": facility_id(facility),
                 "type": ui.facility_type_of(facility), "owner": str(_get(facility, "owner")),
                 "name": ui.facility_name(app, facility), "rewrites": 0, "dropped": []}
        store["batch"] = batch
        write("shopping at {} {!r} owner={}".format(where(facility), batch["name"], batch["owner"]))
        try:
            return orig(self, *args, **kwargs)
        finally:
            if store.get("batch") is batch:
                store["batch"] = None
            goods = _get(_get(facility, "config"), "goods")
            kinds = collections.Counter(category_of(entry) for entry in goods) \
                if isinstance(goods, list) else collections.Counter()
            write("done at {}: rewrites={} dropped={} goods={} {}".format(
                where(facility), batch["rewrites"], len(batch["dropped"]), sum(kinds.values()), dict(kinds)))

    def rewrite(texts, site):
        batch = live_batch()
        if batch is None:
            return None
        out, hits, named = [], 0, False
        for text in texts:
            new, count = rewrite_text(text, batch["type"])
            out.append(new)
            hits += count
            if count and batch["name"] and batch["name"] in text:
                named = True
        if not hits:
            return None
        batch["rewrites"] += hits
        # 専門店の傾向は「店の名前と説明」に頼る。プロンプトに店の名前が載っているかを残す。
        write("prompt: replaced {} sentence(s) for {} at {} (shop name in prompt: {})".format(
            hits, batch["type"], site, "yes" if named else "no"))
        return out

    llm.wrap_outgoing(ctx, rewrite, label="shop lineup")

    @ctx.wrap(ITEM_TARGET, required=False, safe=True)
    def keep_gear_out(orig, self, *args, **kwargs):
        """`MIXED_GEAR` を切っていれば、宿屋と診療所の品揃えの間は武器・防具を作らない。"""
        # 引数はそのまま渡す。名前付きで受けて渡し直すと、呼び手が省いた引数を None で上書きする。
        batch = live_batch()
        if batch is not None and gear_dropped(batch["type"]):
            item_data = frames.arg(args, kwargs, "item_data", 0)
            kind = category_of(item_data)
            if kind in GEAR_TYPES:
                name = _get(item_data, "item_name") or _get(item_data, "name") or "?"
                batch["dropped"].append(name)
                write("dropped {!r} ({}) at {} {}".format(name, kind, batch["type"], batch["facility"]))
                return None
        return orig(self, *args, **kwargs)

    def stock_of(owner):
        """主の持ち物の辞書（`inventory.inventory`）。読めなければ None。"""
        holder = _get(owner, "inventory")
        inner = _get(holder, "inventory")
        if isinstance(inner, dict):
            return inner
        return holder if isinstance(holder, dict) else None

    def fill_structure():
        """足す品の返却型。`Literal` は使わない（空だと pydantic が落ちる。llm.py）。"""
        item = llm.create_structure(ctx, "ShopLineupItem", {
            "item_name": (str, ...), "description": (str, ...),
            "item_type": (str, ...), "item_sub_type": (str, ...),
            "value": (int, ...), "item_appearance": (str, ...),
            "rarity": (str, ...)}, label="shop lineup")
        if item is None:
            return None
        return llm.create_structure(ctx, "ShopLineupFill", {
            "items": (typing.List[item], ...)}, label="shop lineup")

    def fill_messages(app, facility_type, facility, values, present):
        area = ui.current_area(app)
        types = "、".join("{}: {}".format(t, "/".join(subs)) for t, subs in FILL_TYPES.items())
        text = (
            "あなたはRPGの売買イベントの管理者だ。\n【指示】\n"
            "この店の棚に並ぶ商品を、あと{n}個作れ。棚にある品と同じ名前の商品は作らない。{tendency}\n\n"
            "【場所】\n世界: {world}\n土地: {area}\n{area_desc}\n"
            "店: {shop}（{kind}）\n{shop_desc}\n\n"
            "【アイテムの生成要素】\n"
            "- item_name: 日本語で。\n"
            "- description: 日本語で1〜2文。\n"
            "- item_type と item_sub_type: 次の組から選ぶ。{types}\n"
            "- value: アイテムの価値。次の値を順に1つずつ使う: {values}。"
            "この世界における最低が1で最高が{max}。高いほど強力であったり、特別であったりする。\n"
            "- item_appearance: 英語で短い一文、画像の選択に使う。"
            "(例:['A small clay jar of herbal salve sealed with wax.'])\n"
            "- rarity: {rarities} のいずれか。value が低いほど common。\n"
            "- 棚にある品（同じ名前を作らない）: {present}\n"
        ).format(
            n=len(values), tendency=tendency_of(facility_type) or "",
            world=frames.short(frames.attr(getattr(app, "world", None), "name", ""), 60),
            area=frames.short(frames.attr(area, "name", ""), 60),
            area_desc=frames.short(frames.attr(area, "description", "") or "", 600),
            shop=frames.short(frames.attr(facility, "name", ""), 60), kind=facility_type,
            shop_desc=frames.short(frames.attr(facility, "description", "") or "", 400),
            types=types, values=values, rarities="/".join(RARITIES),
            max=VALUE_MAX,
            present="、".join(sorted(present)) or "無し")
        return [{"role": "system", "content": text}, {"role": "user", "content": "＜品を足す＞"}]

    def tidy(row, value):
        """LLM の1行をゲームの生成入口の引数へ。語彙から外れた値は寄せ、武器・防具は捨てる。"""
        if not isinstance(row, dict):
            return None
        name = frames.short(str(row.get("item_name") or "").strip(), 40)
        item_type = str(row.get("item_type") or "")
        if not name or item_type in GEAR_TYPES:
            return None
        if item_type not in FILL_TYPES:
            item_type = "healing_item"
        sub = str(row.get("item_sub_type") or "")
        if sub not in FILL_TYPES[item_type]:
            sub = FILL_TYPES[item_type][0]
        got = row.get("value")
        if isinstance(got, int) and not isinstance(got, bool):
            value = max(1, min(value_limits.value_max(), got))
        rarity = str(row.get("rarity") or "")
        if rarity not in RARITIES:
            rarity = "common"
        return (name, str(row.get("description") or ""), item_type, sub,
                value, str(row.get("item_appearance") or name), rarity)

    def int_values(rows):
        return [v for v in rows if isinstance(v, int) and not isinstance(v, bool)]

    def tier_values(facility, tier):
        """その段の雛形の価値（なければ雛形全部）。足す品の価値に順に使う。"""
        goods = _get(_get(facility, "config"), "goods")
        goods = goods if isinstance(goods, list) else []
        values = int_values([_get(e, "value") for e in goods if _get(e, "stock_tier") == tier])
        return values or int_values([_get(e, "value") for e in goods])

    def template_gear(facility, tier):
        """その段の雛形に武器・防具があるか（ゲームがこれから並べる品）。"""
        goods = _get(_get(facility, "config"), "goods")
        return any(_get(e, "stock_tier") == tier and category_of(e) in GEAR_TYPES
                   for e in (goods if isinstance(goods, list) else []))

    def fill_stock(app, batch, facility, owner, tier):
        """傾向の品を `FILL_MIN`〜`FILL_MAX` 品足す。足した数。"""
        maker = frames.attr(app, "generate_item_from_item_data")
        base = tier_values(facility, tier)
        if maker is frames.MISSING or not base:
            write("WARN fill at {} {}: no maker or no values".format(batch["type"], batch["facility"]))
            return 0
        count = random.randint(FILL_MIN, max(FILL_MIN, FILL_MAX))
        values = [base[i % len(base)] for i in range(count)]
        stock = stock_of(owner) or {}
        present = {str(_get(item, "name") or "") for item in stock.values()}
        # これから雛形から並ぶ品の名前も同じ名前を作らない側に入れる（並べる前に足すため）。
        goods = _get(_get(facility, "config"), "goods")
        present |= {str(_get(e, "name") or "") for e in (goods if isinstance(goods, list) else [])
                    if _get(e, "stock_tier") == tier and category_of(e) not in GEAR_TYPES}
        present -= {""}
        structure = fill_structure()
        if structure is None:
            return 0
        raw = llm.ask(ctx, FILL_MANAGER, fill_messages(app, batch["type"], facility, values, present),
                      timeout=FILL_TIMEOUT, structure=structure, label="shop lineup", write=write)
        rows = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(rows, list) or not rows:
            write("WARN fill at {} {}: the LLM returned no items ({})".format(
                batch["type"], batch["facility"], type(raw).__name__))
            return 0
        made = []
        for index, row in enumerate(rows[:count]):
            fields = tidy(row, values[index])
            if fields is None or fields[0] in present:
                continue
            try:
                maker(*(fields + (owner,)))
            except Exception:
                ctx.log_exc("shop lineup: generate_item_from_item_data failed")
                continue
            made.append("{}({})".format(fields[0], fields[2]))
            present.add(fields[0])
        write("filled {} of {} item(s) at {} {}: {}".format(
            len(made), count, batch["type"], batch["facility"], "、".join(made)))
        return len(made)

    @ctx.wrap(TEMPLATE_TARGET, required=False, safe=True)
    def keep_gear_out_of_template(orig, self, *args, **kwargs):
        """雛形から段の品を並べる間も、`MIXED_GEAR` を切った宿屋と診療所では武器・防具を残さない。

        初めて開いた店では、ゲームが雛形（前の頼みで武器と防具を1つずつ作った品揃え）から
        その段の品を直に作る（`generate_item_in_shopping` も `generate_item_from_dict` も通らない。
        `227_` の品の誕生の記録）。作り終えた直後、売買の画面を開く前の同じスレッドで、新しく入った武器・防具を外す
        （画面を開いた後に持ち物の辞書を触ると、値付けの見回りと競合して落ちる。GAME.md §2.13.1）。
        棚が空から並べる回（初めて開いた店）で武器・防具を外すことになるときは、並べる**前**に傾向の品を足す
        （`fill_stock`）。後に足すと、品を並べ終えた直後に検品する `405_regional_economy` が足した品を見ないことがあり
        （包みの重なり順で前後する）、売買画面を開く手前でもう一度待つ（2026-10-24 の実機）。
        """
        batch = live_batch()
        owner = frames.arg(args, kwargs, "shop_owner_instance", 0)
        stock = stock_of(owner) if batch is not None and gear_dropped(batch["type"]) else None
        before = set(stock) if isinstance(stock, dict) else None
        if before is not None and not before and not batch.get("filled"):
            try:
                app = _get(self, "app") or ui.find_app()
                facility = current_facility(app)
                tier = frames.arg(args, kwargs, "next_tier", 1)
                if batch["dropped"] or template_gear(facility, tier):
                    batch["filled"] = True
                    fill_stock(app, batch, facility, owner, tier)
                    stock = stock_of(owner)
                    before = set(stock) if isinstance(stock, dict) else before
            except Exception:
                ctx.log_exc("shop lineup: cannot fill the stock")
        result = orig(self, *args, **kwargs)
        if before is None:
            return result
        try:
            stock = stock_of(owner)
            for key in [key for key in list(stock or {}) if key not in before]:
                item = stock[key]
                kind = category_of(item)
                if kind in GEAR_TYPES:
                    name = _get(item, "name") or "?"
                    del stock[key]
                    batch["dropped"].append(name)
                    write("dropped {!r} ({}) from the stock template at {} {}".format(
                        name, kind, batch["type"], batch["facility"]))
        except Exception:
            ctx.log_exc("shop lineup: cannot keep the gear out of the template stock")
        return result

    # ------------------------------------------------------------ 世界の一括生成の店の品
    def bulk_facility(task, world):
        """一括生成の店の仕事が指す施設の素データと id。店の仕事でなければ `(None, None)`。

        店の仕事の `ids` は `(エリア, ノード, 施設, 段)`（`241_` の記録。1軒を段ごとに3件に分ける）。
        """
        if str(_get(task, "kind", "")) != BULK_SHOP_KIND:
            return None, None
        ids = _get(task, "ids")
        if not isinstance(ids, (tuple, list)) or len(ids) < 3:
            return None, None
        area_id, node_id, fid = (str(part) for part in ids[:3])
        try:
            found = world["areas"][area_id]["nodes"][node_id]["facilities"][fid]
        except (KeyError, TypeError, IndexError):
            return None, None
        return (found, fid) if isinstance(found, dict) else (None, None)

    @ctx.wrap(BULK_TARGET, required=False, safe=True)
    def bulk_generate(orig, self, *args, **kwargs):
        task = frames.arg(args, kwargs, "task", 0)
        world = frames.arg(args, kwargs, "world", 1)
        found, fid = None, None
        try:
            found, fid = bulk_facility(task, world)
        except Exception:
            ctx.log_exc("shop lineup: cannot read the bulk task")
        kind = _get(found, "facility_type") if found is not None else None
        owner = _get(found, "owner") if found is not None else None
        if kind not in LINEUPS or owner is None or str(owner).startswith(MOD_PREFIX) \
                or modfacility.is_mod_facility(str(fid)):
            return orig(self, *args, **kwargs)
        batch = {"since": time.time(), "facility": str(fid), "type": kind, "owner": str(owner),
                 "name": str(_get(found, "name", "") or ""), "rewrites": 0, "dropped": []}
        store["batch"] = batch
        label = str(_get(task, "label", "") or "")
        try:
            return orig(self, *args, **kwargs)
        finally:
            if store.get("batch") is batch:
                store["batch"] = None
            goods = _get(_get(found, "config"), "goods")
            kinds = collections.Counter(category_of(entry) for entry in goods) \
                if isinstance(goods, list) else collections.Counter()
            write("bulk done at {} {} {!r}: rewrites={} dropped={} goods={} {}".format(
                kind, fid, label, batch["rewrites"], len(batch["dropped"]), sum(kinds.values()), dict(kinds)))

    # ------------------------------------------------------------ 入れ替え（312）へ傾向を渡す
    def tendency(facility):
        if not has_lineup(facility):
            return None
        return tendency_of(ui.facility_type_of(facility))

    def excluded(facility):
        if not has_lineup(facility) or not gear_dropped(ui.facility_type_of(facility)):
            return None
        return GEAR_TYPES

    shop_lineup.provide(OWNER, ctx, tendency, exclude=excluded)

    ctx.log("shop lineup: {!r} added at {}; goods lean by shop type ({}); off-type gear {}; log goes to out/{}".format(
        SHOP_CHOICE, "/".join(ENTRY_TYPES), "/".join(LINEUPS),
        "mixed in" if MIXED_GEAR else "kept out at " + "/".join(GEARLESS_TYPES), LOG_BASENAME))
