# -*- coding: utf-8 -*-
r"""機能追加: 役場に出資して街に施設を建て、売上を受け取る。

素のゲームの街は生成された時点で固まっていて、遊んでいる側が何かを足す手段が無い。
この MOD は役場（`administrative_office`）の選択肢に出資の窓口を1つ足す。

    [役場]  労働の募集をみる / 市民権の発行 / 出資する / 出る
                                    ↓
            宿屋を建てる / 闘技場を建てる / 持っている施設 / やめる
                                    ↓
            並(12,000G) / 上等(21,600G) / 最上(38,400G) / やめる
                                    ↓
            街の入口に建物が1軒増え、主人が1人立つ

    [建物]  泊まる（宿屋）／試合に出る（闘技場） / 売上を受け取る(N G) / 出る

## 建てられるものは街の規模で決まる

種類ごとに「最低の規模」と「軒数の上限」がある（`catalog.py`）。
闘技場は村には建たず、宿屋は既に2軒ある町にはもう建たない。
上限は**ゲームが生成した施設も数に入れる**。

費用と売上は 種類の基礎額 × 等級 × 規模 で、都市は高いが儲かり、村は安いが細い。

## 建物も主人もローダが持つ

建物は `modfacility`（TECH.md §5.8）、主人は `modnpc`（§5.7）。
どちらもセーブには残らず、控え（`state\`）から建て直す。
この MOD が持つのは**出資の帳簿**だけ（`state\facility_investment\<世界名>.json`）。

    持ち株  = {area, facility, keeper, kind, tier, size, name, built, collected}

主人は施設を建てたときに一緒に生まれ、その施設の主として立つ。
雇っているわけではない（表現の問題で、実際にどこかで雇うわけではない）。
宿屋の主が居るので、ゲームの宿泊（`VacationStartManager`）は主を引けて落ちない。

## 売上は訪ねて受け取る

日数が進んでも勝手には入らない。
建物へ行って「売上を受け取る」を押すと、前回受け取った日からの日数 × 1日の売上 が入る。
溜まるのは `HOLD_DAYS` 日ぶんまでで、それ以上は放置しても増えない
（主人が預かっている、という理屈）。

## 中身の経路は「落ちない経路」だけ

実行時に足した施設で本体の売買（`shopping_start_method_1`）を起こすと
`world_dict` を引いて `KeyError` で落ちる（GAME.md §2.28）。
だから店は建てない。宿屋は `VacationStartManager`（`914_` で実機を通した経路）、
闘技場は `EntryColosseumMatchManager(app)`（施設 id を引数に取らない。未確認）。
道場は `DisplayTrainingChoice(app, training_type)` の語彙が測れていないので、
表には置いたが窓口には出さない（`catalog.KINDS[...]["enabled"]`）。
"""

import datetime
import sys

from instantale_modloader import frames, modfacility, modnpc, ui
from instantale_modloader.state import UNKNOWN_WORLD, WorldStore, world_key

from . import catalog


LOG_BASENAME = "facility_investment.log"

#: `modfacility` / `modnpc` に名乗る持ち主。控えの中でこの MOD の建物と主人を束ねる鍵。
OWNER = "915_facility_investment"

#: 世界ごとの控え `state\facility_investment\<世界名>.json`。
STATE_DIRNAME = "facility_investment"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_facility_investment_store__"

#: 押下を横取りするための印。他の MOD と別のキーにすること。
MARK = "mod_facility_investment"

#: ボタンに載せる種類・等級（`mod_` で始めるのは、他の MOD の掃除に
#: 「印の無いボタン」と見なされないため。TECH.md §5.1.1）。
KIND_KEY = "mod_facility_investment_kind"
TIER_KEY = "mod_facility_investment_tier"

#: 役場の `facility_type`（実セーブで確認。GAME.md §2.7）。
OFFICE_FACILITY_TYPE = "administrative_office"

#: 施設の選択肢であることの目印。移動のボタンがある画面だけに足す（`309_` と同じ）。
MOVE_CLS = "MovePhaseManager"

#: 宿泊の入口。実測の署名は `(app, months, quality)`（GAME.md §2.17）。
STAY_CLS = "VacationStartManager"

#: 闘技場の入口。署名は `(app)`（`out\recon\targets.txt`）。
ARENA_CLS = "EntryColosseumMatchManager"

DAYS_PER_MONTH = 30
BASE_STAY_MONTHS = 3

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む）。
# `catalog.py` へ移さないこと（TECH.md §3.8）。
COST_SCALE = 100
INCOME_SCALE = 100
HOLD_DAYS = 360
STAY_QUALITY = "private_room"

# ---------------------------------------------------------------- 文言
DESK_LABEL = "出資する"
BUILD_LABEL = "{}を建てる"
TIER_LABEL = "{}({}G)"
STATUS_LABEL = "持っている施設"
CANCEL_LABEL = "やめる"
COLLECT_LABEL = "売上を受け取る({}G)"
COLLECT_EMPTY_LABEL = "売上を受け取る(まだ無い)"
STAY_LABEL = "泊まる"
ARENA_LABEL = "試合に出る"
LEAVE_LABEL = "出る"

BUILT_TEXT = "{area}の入口に{name}が建った。{keeper}が{role}として店を開けている。"
COLLECTED_TEXT = "{keeper}から{days}日ぶんの売上、{gold}Gを受け取った。"
NOTHING_TEXT = "{keeper}は帳簿を開いたが、まだ渡せるものは無いという。"
NO_GOLD_TEXT = "手持ちが足りない（{gold}G 必要だ）。"
NO_ROOM_TEXT = "{area}に{kind}をこれ以上建てる余地は無いようだ。"
NO_SIZE_TEXT = "{area}の規模では{kind}は成り立たないと言われた。"
NO_HUB_TEXT = "この土地には建てられる場所が無いようだ。"

#: 印を失った残骸を文言で見分けて掃除するための前方一致（TECH.md §6.2）。
OUR_LABEL_PREFIXES = (DESK_LABEL, STATUS_LABEL, "宿屋を建てる", "道場を建てる",
                      "闘技場を建てる", "並(", "上等(", "最上(")


def main_module():
    """ゲーム本体のモジュール（`instantale.py`）。"""
    return sys.modules.get("__main__")


def age_of(app):
    value = getattr(getattr(app, "player", None), "age", None)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def stay_months(app):
    """宿泊の月数（素のゲームの式。3ヵ月を土台に 30代 +1・40代 +2・50代以上 +3、上限6）。

    `914_` は宿屋で実際に使われた月数を覚えて追従するが、
    自分の宿屋の宿泊はゲームの宿屋と同じ経路なので、ここでは式で足りる
    （`315_vacation_custom` を入れていればそちらが `execute` の中で効く）。
    """
    months = BASE_STAY_MONTHS
    age = age_of(app)
    if age is not None:
        months += 3 if age >= 50 else 2 if age >= 40 else 1 if age >= 30 else 0
    return max(1, min(months, 6))


def _fmt(template, **values):
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def ordered_bucket(bucket):
    """控えの項目の並び。"""
    if not isinstance(bucket, dict):
        return bucket
    order = ("holdings",)
    ordered = {key: bucket[key] for key in order if key in bucket}
    ordered.update({k: v for k, v in bucket.items() if k not in ordered})
    return ordered


def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "worlds": WorldStore(ctx, STATE_DIRNAME, order=ordered_bucket),
            "state": {
                # 自前のフェーズが動いている間の旗。連打の2発目を捨てる。
                "acting": False,
                # 自前の画面を出す前の選択肢（`やめる` で戻すために控える）。
                "saved": None,
                # 窓口で選んだ種類（等級を選ぶ画面へ持ち回る）。
                "choosing": None,
                # 自分の宿屋での宿泊。`{"facility": id}`。宿代を返すために覚える。
                "own_stay": None,
                # 一度書いた WARN の覚え。
                "warned": set(),
                # 手が空くのを待っているボタンの足し直し。
                "retry": False,
            },
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]

    write = ctx.logger(LOG_BASENAME)
    worlds = store["worlds"].rebind(ctx, write)
    screen = ui.Screen(ctx, write, tag="investment", mark=MARK)

    # 建物と主人はローダが持つ。関所は何本の MOD が呼んでも1つしか立たない。
    modfacility.install(ctx, write=write)
    modnpc.install(ctx, write=write)

    # ------------------------------------------------------------ 補助
    def warn_once(token, text):
        if token in state["warned"]:
            return False
        state["warned"].add(token)
        write(text)
        return True

    # ------------------------------------------------------------ 控え
    def bucket_of(key):
        bucket = worlds.load(key)
        bucket.setdefault("holdings", [])
        return bucket

    def holdings_of(app):
        key = world_key(app)
        if not key or key == UNKNOWN_WORLD:
            return []
        return [h for h in bucket_of(key).get("holdings") or [] if isinstance(h, dict)]

    def holding_of(app, facility_id):
        for record in holdings_of(app):
            if str(record.get("facility") or "") == str(facility_id):
                return record
        return None

    def holdings_in(app, area_id):
        return [h for h in holdings_of(app) if str(h.get("area")) == str(area_id)]

    def save(app):
        key = world_key(app)
        if key and key != UNKNOWN_WORLD:
            worlds.save(key)

    # ------------------------------------------------------------ 街の規模と軒数
    def area_size(app, area_id):
        """街の規模。実行時の `Area.size` は読めない（`324_` の実機 38/38）ので
        素データから取る。街でなければ None。"""
        for attr in ("save_data_dict", "world_dict"):
            container = getattr(app, attr, None)
            areas = container.get("areas") if isinstance(container, dict) else None
            if not isinstance(areas, dict):
                continue
            entry = areas.get(str(area_id))
            if entry is None:
                for k, v in areas.items():
                    if str(k) == str(area_id):
                        entry = v
                        break
            if isinstance(entry, dict):
                size = catalog.normalize_size(entry.get("size"))
                if size:
                    return size
        return None

    def count_kind(area, kind):
        """その街にある同種の施設の数。ゲームのものも、MOD が建てたものも数える。"""
        found = 0
        for node in ui.nodes_of(area):
            for facility in ui.facilities_of(node).values():
                if ui.facility_type_of(facility) == kind:
                    found += 1
        return found

    def room_for(app, area, kind):
        """建ててよいか。`(可否, 理由)`。理由は `size` / `cap` / `hub` / None。"""
        area_id = ui.area_id_of(area)
        size = area_size(app, area_id)
        if size is None or not catalog.allowed(kind, size):
            return False, "size"
        if count_kind(area, kind) >= catalog.cap(kind, size):
            return False, "cap"
        node, hub = modfacility.hub_of(area)
        if node is None or hub is None:
            return False, "hub"
        return True, None

    # ------------------------------------------------------------ 建物と主人
    def facility_id_for(area_id, serial):
        return modfacility.make_id(OWNER, "{}-{}".format(area_id, serial))

    def keeper_id_for(area_id, serial):
        return modnpc.make_id(OWNER, "keeper-{}-{}".format(area_id, serial))

    def next_serial(app, area_id):
        used = set()
        for record in holdings_in(app, area_id):
            tail = str(record.get("facility") or "").rsplit("-", 1)[-1]
            if tail.isdigit():
                used.add(int(tail))
        serial = 1
        while serial in used:
            serial += 1
        return serial

    def per_day(record):
        return catalog.income_per_day(record.get("kind"), record.get("tier"),
                                      record.get("size"), INCOME_SCALE)

    def owed(app, record):
        """いま受け取れる売上と、その日数。`(金額, 日数)`。"""
        today = ui.game_day(app)
        since = record.get("collected")
        if today is None or not isinstance(since, int):
            return 0, 0
        days = max(0, min(int(today) - since, int(HOLD_DAYS)))
        return days * per_day(record), days

    def keeper_name(app, record):
        handle = modnpc.get(app, record.get("keeper"))
        name = getattr(handle, "name", None) if handle is not None else None
        return name or catalog.keeper_fields(record.get("kind"), "", "",
                                             record.get("keeper"))["name"]

    def building_choices(app, facility_id):
        """建物の中の選択肢。出口はローダが足す。"""
        record = holding_of(app, facility_id)
        if record is None:
            return []
        if state.get("own_stay") is not None:
            return None      # 宿泊の最中。活動の選択肢に混ぜない
        out = []
        kind = record.get("kind")
        if kind == "inn":
            out.append({"key": "stay", "label": STAY_LABEL,
                        "on": lambda info: act(info["app"], "stay", facility_id)})
        elif kind == "colosseum":
            out.append({"key": "arena", "label": ARENA_LABEL,
                        "on": lambda info: act(info["app"], "arena", facility_id)})
        gold, _days = owed(app, record)
        label = COLLECT_LABEL.format(ui.money(gold)) if gold > 0 else COLLECT_EMPTY_LABEL
        out.append({"key": "collect", "label": ui.rewrite_coins(label),
                    "on": lambda info: act(info["app"], "collect", facility_id)})
        return out

    def register_holding(record):
        """持ち株1つぶんの層を積む（建物と主人）。"""
        facility_id = str(record.get("facility") or "")
        keeper_id = str(record.get("keeper") or "")
        if not facility_id or not keeper_id:
            return ""

        def choices(info):
            found = building_choices(info["app"], info["facility_id"])
            return [] if found is None else found

        def no_exit(info):
            return [] if building_choices(info["app"], info["facility_id"]) is None \
                else info["args"]["choices"]

        def background(info):
            return paint_building(info["app"], info["facility_id"])

        modfacility.register(
            OWNER, facility_id=facility_id,
            fields={"name": record.get("name") or "",
                    "description": catalog.description_of(record.get("kind"),
                                                          record.get("tier")),
                    "facility_type": record.get("kind"),
                    "tier": record.get("tier"),
                    "owner": keeper_id},
            choices=choices, exit_label=LEAVE_LABEL,
            # 持ち株の建物はロードで必ず建ち直る（手放す経路がまだ無い）。
            keep_inside=True,
            on={"choices": no_exit, "background": background,
                "leave": lambda info: end_stay(info["app"], "left the building")},
            write=write)
        modnpc.register(
            OWNER, npc_id=keeper_id,
            fields=catalog.keeper_fields(record.get("kind"), record.get("area_name"),
                                         record.get("name"), keeper_id),
            write=write)
        return facility_id

    def keeper_placed(keeper_id):
        record = modnpc.registry().get(str(keeper_id))
        return bool(record and record.get("placed"))

    def apply_holdings(app, world, key, why):
        """控えの持ち株をこの世界へ当てる。建てた棟数を返す。

        ロードの直後と、選択肢が組まれるたびに呼ばれる。既に立っているものは何もしない。
        主人は建物が立った後に置く（フレームワークどうしは順序を約束しない。TECH.md §5.8）。
        """
        areas = ui.areas_of_world(world)
        if not areas:
            return 0
        built = 0
        for record in holdings_of(app) if key else []:
            area_id = str(record.get("area") or "")
            if area_id not in areas:
                warn_once(("area", key, area_id),
                          "WARN {}: area {!r} is not in this world".format(why, area_id))
                continue
            facility_id = register_holding(record)
            if not facility_id:
                continue
            standing = bool((modfacility.registry().get(facility_id) or {}).get("placed"))
            facility = modfacility.spawn(app, facility_id, area_id, world=world, write=write)
            if facility is None:
                warn_once(("hub", key, area_id),
                          "WARN {}: {!r} is not standing in area {!r}".format(
                              why, record.get("name"), area_id))
                continue
            keeper_id = str(record.get("keeper"))
            if modnpc.spawn(app, keeper_id, world=world, write=write) is not None \
                    and not keeper_placed(keeper_id):
                modnpc.place(app, keeper_id, area_id, facility_id, owner=True, write=write)
            state["warned"].discard(("hub", key, area_id))
            state["warned"].discard(("area", key, area_id))
            if not standing:
                built += 1
        if built:
            write("{}: {} building(s) standing in world {!r}".format(why, built, key))
        return built

    # ------------------------------------------------------------ 出資
    def build(app, kind, tier):
        """出資して建てる。成否を返す。"""
        area = ui.current_area(app)
        if area is None:
            write("build: no current area")
            return False
        area_id = ui.area_id_of(area)
        area_name = frames.short(getattr(area, "name", ""), 40) or area_id
        spec = catalog.kind_of(kind)
        if spec is None or tier not in catalog.TIERS:
            write("WARN build: unknown kind/tier {!r}/{!r}".format(kind, tier))
            return False
        ok, why = room_for(app, area, kind)
        if not ok:
            write("build: {} refused in area {!r} ({})".format(kind, area_id, why))
            text = {"size": NO_SIZE_TEXT, "cap": NO_ROOM_TEXT}.get(why, NO_HUB_TEXT)
            screen.say(app, _fmt(text, area=area_name, kind=spec["label"]))
            return False
        size = area_size(app, area_id)
        price = catalog.cost_of(kind, tier, size, COST_SCALE)
        gold = ui.gold_of(app)
        if gold is None or price is None:
            write("WARN build: cannot read the gold or the price")
            return False
        if gold < price:
            screen.say(app, ui.rewrite_coins(_fmt(NO_GOLD_TEXT, gold=ui.money(price))))
            return False
        serial = next_serial(app, area_id)
        facility_id = facility_id_for(area_id, serial)
        keeper_id = keeper_id_for(area_id, serial)
        name = catalog.facility_name(kind, tier, area_name, facility_id)
        day = ui.game_day(app)
        record = {
            "area": area_id,
            "area_name": area_name,
            "facility": facility_id,
            "keeper": keeper_id,
            "kind": kind,
            "tier": tier,
            "size": size,
            "name": name,
            "price": price,
            "built": day,
            "collected": day,
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        register_holding(record)
        facility = modfacility.spawn(app, facility_id, area_id, write=write)
        if facility is None:
            modfacility.unregister(OWNER, facility_id, app=app, write=write)
            modnpc.unregister(OWNER, keeper_id, app=app, write=write)
            screen.say(app, NO_HUB_TEXT)
            return False
        if modnpc.spawn(app, keeper_id, write=write) is not None:
            modnpc.place(app, keeper_id, area_id, facility_id, owner=True, write=write)
        else:
            write("WARN build: the keeper {} did not spawn; the building has no owner"
                  .format(keeper_id))
        bucket_of(world_key(app))["holdings"].append(record)
        save(app)
        ui.add_gold(app, -price, on_error=lambda: write("WARN build: cannot charge"))
        write("built: {} {} {!r} id={} keeper={} area={!r} size={} price={}".format(
            kind, tier, name, facility_id, keeper_id, area_id, size, price))
        screen.say(app, _fmt(BUILT_TEXT, area=area_name, name=name,
                             keeper=keeper_name(app, record),
                             role=spec.get("keeper_role", "主人")))
        return True

    def collect(app, facility_id):
        """溜まった売上を受け取る。受け取った額を返す。"""
        record = holding_of(app, facility_id)
        if record is None:
            return 0
        gold, days = owed(app, record)
        who = keeper_name(app, record)
        if gold <= 0:
            screen.say(app, _fmt(NOTHING_TEXT, keeper=who))
            return 0
        ui.add_gold(app, gold, on_error=lambda: write("WARN collect: cannot pay"))
        today = ui.game_day(app)
        record["collected"] = today if today is not None else record.get("collected")
        save(app)
        write("collected: {}G for {} day(s) at {!r}".format(gold, days, record.get("name")))
        screen.say(app, ui.rewrite_coins(_fmt(COLLECTED_TEXT, keeper=who, days=days,
                                              gold=ui.money(gold))))
        return gold

    # ------------------------------------------------------------ 中でできること
    def start_stay(app, facility_id):
        """自分の宿屋に泊まる。宿屋の宿泊と同じ経路で、宿代は返す。"""
        record = holding_of(app, facility_id)
        if record is None:
            return
        cls = getattr(main_module(), STAY_CLS, None)
        if cls is None:
            write("WARN stay: __main__.{} is not available".format(STAY_CLS))
            return
        try:
            phase = cls(app, int(stay_months(app)), str(STAY_QUALITY))
        except Exception:
            ctx.log_exc("investment: cannot build {}".format(STAY_CLS))
            return
        state["own_stay"] = {"facility": str(facility_id), "name": record.get("name")}
        write("stay: starting {} months={} quality={!r} at {!r}".format(
            STAY_CLS, stay_months(app), STAY_QUALITY, record.get("name")))
        screen.start_phase(app, phase, STAY_LABEL)

    def end_stay(app, why):
        if state.get("own_stay") is None:
            return
        write("stay: finished at {!r} ({})".format(state["own_stay"].get("name"), why))
        state["own_stay"] = None

    def staying_here(app):
        home = state.get("own_stay")
        if not isinstance(home, dict):
            return None
        if not modfacility.inside(app, home.get("facility")):
            return None
        return home

    def enter_arena(app, facility_id):
        """自分の闘技場で試合に出る。ゲームの闘技場の入口をそのまま起こす。"""
        record = holding_of(app, facility_id)
        if record is None:
            return
        cls = getattr(main_module(), ARENA_CLS, None)
        if cls is None:
            write("WARN arena: __main__.{} is not available".format(ARENA_CLS))
            return
        try:
            phase = cls(app)
        except Exception:
            ctx.log_exc("investment: cannot build {}".format(ARENA_CLS))
            return
        write("arena: starting {} at {!r}".format(ARENA_CLS, record.get("name")))
        screen.start_phase(app, phase, ARENA_LABEL)

    def paint_building(app, facility_id):
        """建物の背景。宿屋は部屋の絵を借りる。ほかは描くものが無い（街の景色のまま）。"""
        record = holding_of(app, facility_id)
        if record is None or record.get("kind") != "inn":
            return False
        paint = getattr(app, "change_background_image_to_inn_room", None)
        if not callable(paint):
            warn_once(("bg", "missing"),
                      "WARN background: change_background_image_to_inn_room is gone")
            return False
        try:
            paint(str(STAY_QUALITY))
        except Exception:
            ctx.log_exc("investment: cannot paint the inn background")
            return False
        write("background: {!r} -> the room ({})".format(record.get("name"), STAY_QUALITY))
        return True

    # ------------------------------------------------------------ 窓口
    def show_desk(app):
        """役場の出資の窓口。建てられる種類だけ並べる。"""
        area = ui.current_area(app)
        area_name = frames.short(getattr(area, "name", ""), 40) or "この土地"
        state["saved"] = [item for item in (getattr(app, "buttons", None) or [])
                          if not screen.mark_of(item)]
        state["choosing"] = None
        entries = []
        size = area_size(app, ui.area_id_of(area)) if area is not None else None
        for kind in catalog.enabled_kinds():
            spec = catalog.kind_of(kind)
            ok, _why = room_for(app, area, kind) if area is not None else (False, "size")
            if not ok:
                continue
            entry = screen.button(BUILD_LABEL.format(spec["label"]), mark="kind",
                                  extra={KIND_KEY: kind})
            if entry is not None:
                entries.append(entry)
        if holdings_of(app):
            entry = screen.button(STATUS_LABEL, mark="status")
            if entry is not None:
                entries.append(entry)
        cancel = screen.button(CANCEL_LABEL, mark="cancel")
        if cancel is not None:
            entries.append(cancel)
        if size is None:
            screen.say(app, "{}は出資を受け付けていない。".format(area_name))
        elif len(entries) <= (2 if holdings_of(app) else 1):
            screen.say(app, "{}（{}）にいま建てられるものは無い。".format(
                area_name, catalog.SIZE_LABEL.get(size, size)))
        else:
            screen.say(app, "{}（{}）で出資できる施設を並べた。".format(
                area_name, catalog.SIZE_LABEL.get(size, size)))
        screen.apply_buttons(app, entries, "desk")

    def show_tiers(app, kind):
        """等級を選ぶ。値札は今の街の規模で計算する。"""
        area = ui.current_area(app)
        spec = catalog.kind_of(kind)
        if area is None or spec is None:
            back(app, "no area")
            return
        size = area_size(app, ui.area_id_of(area))
        state["choosing"] = kind
        entries = []
        for tier in catalog.TIERS:
            price = catalog.cost_of(kind, tier, size, COST_SCALE)
            if price is None:
                continue
            label = TIER_LABEL.format(catalog.TIER_LABEL[tier], ui.money(price))
            entry = screen.button(ui.rewrite_coins(label), mark="tier",
                                  extra={KIND_KEY: kind, TIER_KEY: tier})
            if entry is not None:
                entries.append(entry)
        cancel = screen.button(CANCEL_LABEL, mark="cancel")
        if cancel is not None:
            entries.append(cancel)
        income = [catalog.income_per_day(kind, t, size, INCOME_SCALE) for t in catalog.TIERS]
        screen.say(app, ui.rewrite_coins(
            "{}の等級を選ぶ。1日の売上は 並 {}G / 上等 {}G / 最上 {}G の見込み。".format(
                spec["label"], *[ui.money(v) for v in income])))
        screen.apply_buttons(app, entries, "tiers")

    def show_status(app):
        lines = []
        for record in holdings_of(app):
            gold, days = owed(app, record)
            lines.append("{}（{}・{}・{}）: 売上 {}G（{}日ぶん）".format(
                record.get("name"), record.get("area_name"),
                catalog.kind_of(record.get("kind"))["label"]
                if catalog.kind_of(record.get("kind")) else record.get("kind"),
                catalog.TIER_LABEL.get(record.get("tier"), record.get("tier")),
                ui.money(gold), days))
        screen.say(app, ui.rewrite_coins("\n".join(lines) if lines else "持っている施設は無い。"))
        back(app, "status")

    def back(app, why="back"):
        """自前の画面から役場の選択肢へ戻す（窓口のボタンは塗り直しの中で足し直される）。"""
        saved, state["saved"] = state["saved"], None
        state["choosing"] = None
        write("back: {} ({} entries)".format(why, len(saved) if saved is not None else "keep"))
        screen.apply_buttons(app, saved, "back")

    # ------------------------------------------------------------ 自前のフェーズ
    class InvestPhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**（セーブに焼かれる）。"""

        def __init__(self, app, action, kind=None, tier=None, facility_id=None):
            self.app = app
            self.action = action
            self.kind = kind
            self.tier = tier
            self.facility_id = facility_id

        def execute(self, choice_text):
            state["acting"] = True
            try:
                run_action(self.app, self.action, self.kind, self.tier, self.facility_id)
            except Exception:
                ctx.log_exc("investment: phase {!r} failed".format(self.action))
            finally:
                state["acting"] = False

    def run_action(app, action, kind=None, tier=None, facility_id=None):
        if action == "desk":
            show_desk(app)
        elif action == "kind":
            show_tiers(app, kind)
        elif action == "tier":
            build(app, kind, tier)
            back(app, "built")
        elif action == "status":
            show_status(app)
        elif action == "cancel":
            back(app, "cancelled")
        elif action == "collect":
            collect(app, facility_id)
        elif action == "stay":
            start_stay(app, facility_id)
        elif action == "arena":
            enter_arena(app, facility_id)
        else:
            write("WARN unknown action {!r}".format(action))

    def act(app, action, facility_id):
        """建物の中の選択肢（ローダが押下を渡してくる）。フェーズに乗せて起こす。"""
        if state["acting"]:
            write("ignored {!r}: the previous press is still running".format(action))
            return
        text = {"collect": "売上", "stay": STAY_LABEL, "arena": ARENA_LABEL}.get(action, action)
        screen.start_phase(app, InvestPhase(app, action, facility_id=facility_id), text,
                           fallback=lambda: run_action(app, action, facility_id=facility_id))

    # ------------------------------------------------------------ 役場のボタン
    def is_facility_screen(buttons):
        if not isinstance(buttons, list):
            return False
        return any(ui.spec_cls_name(entry) == MOVE_CLS for entry in buttons)

    def at_office(app):
        location = getattr(getattr(app, "player", None), "location", None)
        if location is None or isinstance(location, (str, int)):
            return False
        return ui.facility_type_of(location) == OFFICE_FACILITY_TYPE

    def retry_when_idle(app):
        if state.get("retry"):
            return
        state["retry"] = True

        def again():
            state["retry"] = False
            try:
                maintain_buttons(app)
            except Exception:
                ctx.log_exc("investment: cannot maintain the choices (idle)")

        screen.when_idle(app, again, proceed_on_timeout=True, tag="retry")

    def maintain_buttons(app):
        """役場に「出資する」を足す。建物の中はローダが出す。何度呼んでも増えない。"""
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return
        if ui.busy_signals(app):
            retry_when_idle(app)
            return
        if state.get("own_stay") is not None or not is_facility_screen(buttons):
            return
        screen.prune_stale(buttons, list(OUR_LABEL_PREFIXES))
        if any(screen.mark_of(entry) for entry in buttons):
            return
        if not at_office(app):
            return
        entry = screen.button(DESK_LABEL, mark="desk")
        if entry is None:
            return
        buttons.insert(max(len(buttons) - 1, 0), entry)
        screen.apply_buttons(app, None, "office")

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """選択肢が組み直されるたびに、建物を当て直して自前のボタンを足す。

        最後にローダの塗り直しをもう一度呼ぶ（どちらの関所が内側かは適用順で変わる）。
        """
        result = orig(self, reset_page, *args, **kwargs)
        try:
            apply_holdings(self, getattr(self, "world", None), world_key(self), "screen")
            maintain_buttons(self)
            modfacility.maintain_buttons(self, write=write)
        except Exception:
            ctx.log_exc("investment: cannot maintain the choices")
        return result

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        entry = ui.pressed_entry(self, button_index)
        action = screen.mark_of(entry)
        if action is None:
            return orig(self, button_index, *args, **kwargs)
        if state["acting"]:
            write("ignored {!r}: the previous press is still running".format(
                entry.get("text") if isinstance(entry, dict) else None))
            return None
        text = (entry.get("text") if isinstance(entry, dict) else None) or DESK_LABEL
        kind = entry.get(KIND_KEY) if isinstance(entry, dict) else None
        tier = entry.get(TIER_KEY) if isinstance(entry, dict) else None
        write("pressed {!r} ({} kind={} tier={})".format(text, action, kind, tier))
        screen.start_phase(self, InvestPhase(self, action, kind, tier), text,
                           fallback=lambda: run_action(self, action, kind, tier))
        return None

    @ctx.wrap("__main__:World.__init__", required=False, safe=True)
    def world_loaded(orig, self, save_data_dict, app, *args, **kwargs):
        """セーブを読んだ直後。前の世界の覚えを捨て、持ち株を当て直す。"""
        result = orig(self, save_data_dict, app, *args, **kwargs)
        try:
            key = world_key(app)
            if app is not None and key and key != UNKNOWN_WORLD:
                worlds.forget(key)
                state["own_stay"] = None
                state["saved"] = None
                state["choosing"] = None
                state["warned"] = set()
                apply_holdings(app, self, key, "load")
        except Exception:
            ctx.log_exc("investment: cannot rebuild the holdings on load")
        return result

    @ctx.wrap("__main__:VacationStartManager.execute", required=False)
    def vacation_start(orig, self, choice_text="", *args, **kwargs):
        """自分の宿屋の宿泊は宿代を取らない。よその宿屋には触らない。

        宿代の引き落としは `execute` の中で1回だけ起きる（GAME.md §2.17）ので、
        前後の所持金の差を返す。落ちたときは操作を戻す（`914_` と同じ手当て）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        home = staying_here(app) if app is not None else None
        if home is None:
            return orig(self, choice_text, *args, **kwargs)
        before = ui.gold_of(app)
        try:
            result = orig(self, choice_text, *args, **kwargs)
        except Exception as exc:
            write("WARN stay: the game's stay failed: {}({!r}) at {!r}".format(
                type(exc).__name__, getattr(exc, "args", ()), home.get("name")))
            ctx.log_exc("investment: the game's stay failed at {!r}".format(home.get("name")))
            refund(app, before, "stay failed")
            end_stay(app, "the stay failed")
            screen.schedule(lambda: screen.busy_off(app), 0)
            return None
        refund(app, before, "stay")
        return result

    def refund(app, before, why):
        after = ui.gold_of(app)
        if not isinstance(before, int) or not isinstance(after, int):
            return 0
        room = before - after
        if room <= 0:
            return 0
        ui.add_gold(app, room, on_error=lambda: write("WARN {}: cannot refund".format(why)))
        write("{}: refunded {}".format(why, room))
        return room

    @ctx.wrap("__main__:VacationEndManager.execute", required=False)
    def vacation_end(orig, self, choice_text="", *args, **kwargs):
        result = orig(self, choice_text, *args, **kwargs)
        try:
            end_stay(getattr(self, "app", None) or ui.find_app(), "vacation ended")
        except Exception:
            ctx.log_exc("investment: cannot close the stay")
        return result

    write("applied: kinds={} scales cost={}% income={}% hold={}d".format(
        catalog.enabled_kinds(), COST_SCALE, INCOME_SCALE, HOLD_DAYS))
