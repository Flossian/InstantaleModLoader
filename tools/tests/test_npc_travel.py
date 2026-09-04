# -*- coding: utf-8 -*-
"""326_npc_travel をゲーム抜きで通す。

    python tools/tests/test_npc_travel.py

偽の app / World / Area / Facility / Character を差し込み、次を確認する。

  出発     … 友好度が閾値以上で、残す人数を割らない範囲で出る。
             ゲームの move_npc_to_facility を通り、元のギルドの一覧から外れる
  残す     … 一覧が2人なら誰も出ない。同行中・死亡は数えない
  別の街   … 施設が生成済みの街のギルドか宿だけ。ダンジョンと未生成の街は選ばれない。
             ギルドなら着いた先の一覧に載る（雇える）。日数は 30..90
  同じ街   … ギルドと通路を除いた施設。日数は 7。名簿には残す
  一覧     … ギルドの冒険者一覧に、同じ街に出ている人を居場所付きで並べる（二重にしない）
  現在地   … 同じ街の中の移動はプレイヤーが居る街だけ。別の街への旅はどの街でも起きる
  到着     … 街中の移動は着いた街で引く（前に引いてからの日数ぶん。同じ日には引き直さない）
  帰還     … 期限が来たら元の施設へ戻り、一覧も戻り、台帳から消える
  延期     … プレイヤーがその施設に居る間は出発も帰還も待つ。施設を出たら果たす
  雇用     … 旅先で雇われたら動かさずに旅を終える
  上限     … 1施設の受け入れ人数を超えては行かない
  文脈     … 旅の途中の相手との会話に1文足す（複製に足し、本体は変えない）
  ロード   … 古いセーブなら旅を忘れる。行き先に居なければ置き直す。
             RETURN_ALL_ON_LOAD で全員帰る
  控え     … state/npc_travel/<世界名>.json
"""
import importlib.util
import io
import json
import os
import random
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state_npc_travel")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_npc_travel")
MOD_NAME = "npc_travel_mod"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class PhaseSpec:
    """ゲームのボタン spec。`ui.spec_data` が読む形。"""

    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    """無害 spec（`ui.SAFE_CLS`）。一覧の「やめる」。"""

    def __init__(self, app, *args):
        self.app = app


class ConversationStartManager:
    def __init__(self, app, character_id):
        self.app = app
        self.character_id = character_id


class Facility:
    def __init__(self, facility_id, facility_type, name=None):
        self.id = facility_id
        self.name = name or ("テスト" + facility_type)
        self.facility_type = facility_type
        self.owner = None
        self.characters = []


class Node:
    def __init__(self, facilities):
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, name, facilities, adventurer_npcs=()):
        self.id = area_id
        self.name = name
        self.nodes = {"0": Node(facilities)} if facilities else {}
        self.adventurer_npcs = list(adventurer_npcs)
        self.resident_npcs = []


class Character:
    def __init__(self, character_id, name, affinity, location=None):
        self.id = character_id
        self.name = name
        self.profile = "{}の経歴。".format(name)
        self.config = {"is_dead": False}
        self.relationship = {"player": {"affinity": affinity}}
        self.location = location


class World:
    def __init__(self, areas, characters, days):
        self.areas = areas
        self.characters = characters
        self.days_elapsed = days


class Player:
    def __init__(self, area, location):
        self.current_area = area
        self.location = location


class InstantaleApp:
    """`__main__` のクラスとして `ui.find_app()` に見つけてもらう。"""

    def __init__(self, world, player, save_data_dict):
        self.world = world
        self.player = player
        self.save_data_dict = save_data_dict
        self.world_dict = save_data_dict
        self.game_variables = {"party": ["player"]}
        self.moved = []
        self.in_conversation = None

    def move_npc_to_facility(self, character_id, character_instance,
                             target_facility, target_node=None,
                             register_facility=True):
        self.moved.append((str(character_id), target_facility.id))
        old = character_instance.location
        if isinstance(old, Facility) and str(character_id) in old.characters:
            old.characters.remove(str(character_id))
        character_instance.location = target_facility
        target_facility.characters.append(str(character_id))

    def elapse_days(self, days):
        self.world.days_elapsed += days
        self.save_data_dict["world_data"]["days_elapsed"] = self.world.days_elapsed


APP = None


class FakeCtx:
    def __init__(self, out_dir, state_dir):
        self.out_dir = out_dir
        self.state_dir = state_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1]
                    else path, exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    sys.modules.pop(MOD_NAME, None)
    sys.modules.pop(MOD_NAME + ".travel", None)
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 舞台作り
WORLD_NAME = "旅の検査世界"
G0, I0, S0, E0 = "100", "101", "102", "103"
G1, I1, S1, E1 = "200", "201", "202", "203"
G2 = "300"


def make_world(days=10, affinities=(10, 25, 30), party_member=None,
               player_area="0", player_facility=E0, extra_towns=1):
    """街0（ギルド・宿・店・入口）、街1（ギルド・宿）、ダンジョン2、未生成の街3。"""
    guild0 = Facility(G0, "guild", "泥濘の会館")
    inn0 = Facility(I0, "inn", "灯り亭")
    shop0 = Facility(S0, "general_store", "雑貨屋")
    gate0 = Facility(E0, "entrance", "門")
    area0 = Area("0", "始まりの泥濘", [guild0, inn0, shop0, gate0])
    facilities = {"0": {G0: guild0, I0: inn0, S0: shop0, E0: gate0}}
    areas = {"0": area0}
    sizes = {"0": "village", "2": "dungeon", "3": "town"}
    if extra_towns:
        guild1 = Facility(G1, "guild", "灰の会館")
        inn1 = Facility(I1, "inn", "灰の宿")
        shop1 = Facility(S1, "general_store", "灰の雑貨屋")
        gate1 = Facility(E1, "entrance", "灰の門")
        areas["1"] = Area("1", "灰の交易都市", [guild1, inn1, shop1, gate1])
        facilities["1"] = {G1: guild1, I1: inn1, S1: shop1, E1: gate1}
        sizes["1"] = "city"
    areas["2"] = Area("2", "骸の洞窟", [Facility(G2, "dungeon_location")])
    areas["3"] = Area("3", "未踏の街", [])

    characters = {}
    npcs = {}
    roster = []
    for index, affinity in enumerate(affinities):
        npc_id = str(10 + index)
        character = Character(npc_id, "冒険者{}".format(npc_id), affinity, guild0)
        guild0.characters.append(npc_id)
        characters[npc_id] = character
        roster.append(npc_id)
        npcs[npc_id] = {"name": character.name, "ability_scores": {},
                        "relationship": {"player": {"affinity": affinity}},
                        "current_area": "0", "current_location": G0}
    area0.adventurer_npcs = list(roster)
    save = {"world_data": {"world_name": WORLD_NAME, "days_elapsed": days},
            "npcs": npcs,
            "areas": {area_id: {"size": size, "adventurer_npcs": list(roster) if area_id == "0" else []}
                      for area_id, size in sizes.items()}}
    if "1" in areas:
        save["areas"]["1"] = {"size": "city", "adventurer_npcs": []}
    world = World(areas, characters, days)
    location = facilities.get(player_area, {}).get(player_facility)
    player = Player(areas[player_area], location if location is not None else player_facility)
    app = InstantaleApp(world, player, save)
    if party_member is not None:
        app.game_variables["party"].append(str(party_member))
    return app, facilities


def fresh_mod(seed=1, keep_state=False, **settings):
    module = load_mod()
    if not keep_state and hasattr(sys, module.STORE_ATTR):
        delattr(sys, module.STORE_ATTR)
    if not keep_state and os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    for name, value in settings.items():
        setattr(module, name, value)
    log_path = os.path.join(OUT_DIR, module.LOG_BASENAME)
    if os.path.exists(log_path):
        os.remove(log_path)
    ctx = FakeCtx(OUT_DIR, STATE_DIR)
    module.apply(ctx)
    module._store()["rng"].seed(seed)
    if hasattr(sys, module.STORE_ATTR):
        getattr(sys, module.STORE_ATTR)["reconcile"] = False
    return module, ctx


def use(app):
    global APP
    APP = app


def elapse(ctx, app, days):
    ctx.hooks["__main__:InstantaleApp.elapse_days"](
        InstantaleApp.elapse_days, app, days)


def moved_player(ctx, app):
    class Phase:
        pass
    phase = Phase()
    phase.app = app
    ctx.hooks["__main__:MovePhaseManager.move_phase"](lambda self: None, phase)


def ledger_path():
    from instantale_modloader import state
    return os.path.join(STATE_DIR, "npc_travel", state.world_filename(WORLD_NAME))


def read_ledger():
    path = ledger_path()
    if not os.path.exists(path):
        return {}
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh).get("trips", {})


def read_log(module):
    path = os.path.join(OUT_DIR, module.LOG_BASENAME)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- 場面
def scene_depart():
    print("[出発]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(10, 25, 30))
    use(app)
    elapse(ctx, app, 30)
    trips = read_ledger()
    check("1人だけ出る（3人中、残す2人）", len(trips) == 1, trips)
    check("出たのは友好度20以上の者", set(trips) <= {"11", "12"}, trips)
    npc_id = next(iter(trips))
    check("move_npc_to_facility を通った", app.moved and app.moved[0][0] == npc_id, app.moved)
    check("元のギルドの一覧から外れた（実行時）",
          npc_id not in app.world.areas["0"].adventurer_npcs)
    check("元のギルドの一覧から外れた（セーブ側）",
          npc_id not in app.save_data_dict["areas"]["0"]["adventurer_npcs"])
    check("元のギルドの名簿からも外れた", npc_id not in fac["0"][G0].characters)
    trip = trips[npc_id]
    check("行き先は街1", trip["dest_area"] == "1", trip)
    check("行き先はギルドか宿", trip["dest_facility"] in (G1, I1), trip)
    check("日数は 30..90", 30 <= trip["return_day"] - trip["depart_day"] <= 90, trip)
    check("出発日は今日", trip["depart_day"] == 40, trip)
    check("セーブ側の居場所が更新された",
          app.save_data_dict["npcs"][npc_id]["current_location"] == trip["dest_facility"])
    if trip["dest_facility"] == G1:
        check("着いた先のギルドの一覧に載る（雇える）",
              npc_id in app.world.areas["1"].adventurer_npcs
              and trip["enrolled"] is True)
    else:
        check("宿なら一覧には載らない", trip["enrolled"] is False)
    check("落ちていない", not ctx.errors, ctx.errors)


def scene_keep():
    print("[残す]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30))
    use(app)
    elapse(ctx, app, 30)
    check("2人しか居なければ誰も出ない", not read_ledger() and not app.moved)

    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30), party_member="12")
    use(app)
    elapse(ctx, app, 30)
    check("同行中の1人は数えない（残り2人）", not read_ledger() and not app.moved)

    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    app.world.characters["12"].config["is_dead"] = True
    use(app)
    elapse(ctx, app, 30)
    check("死んだ1人は数えない（残り2人）", not read_ledger() and not app.moved)

    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30, 30, 5))
    use(app)
    elapse(ctx, app, 30)
    trips = read_ledger()
    check("5人居れば友好度の足りる4人から最大3人出る", 1 <= len(trips) <= 3, trips)
    check("友好度5の者は出ない", "14" not in trips)
    left = [cid for cid in app.world.areas["0"].adventurer_npcs]
    check("残りは2人以上", len(left) >= 2, left)

    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 90)
    check("確率0なら出ない", not read_ledger())


def scene_local():
    print("[同じ街]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    trips = read_ledger()
    check("1人出た", len(trips) == 1, trips)
    trip = next(iter(trips.values()))
    check("同じ街", trip["dest_area"] == "0" and trip["kind"] == "local", trip)
    check("ギルドと通路は行き先にしない", trip["dest_facility"] in (I0, S0), trip)
    check("7日", trip["return_day"] - trip["depart_day"] == 7, trip)
    check("同じ街なら一覧に残す",
          next(iter(trips)) in app.world.areas["0"].adventurer_npcs)

    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30), extra_towns=0)
    use(app)
    elapse(ctx, app, 30)
    trip = next(iter(read_ledger().values()))
    check("別の街が無ければ同じ街へ（ダンジョン・未生成は選ばない）",
          trip["dest_area"] == "0", trip)


def talk_button(npc_id):
    return {"text": "冒険者{}".format(npc_id),
            "spec": PhaseSpec("ConversationStartManager", [npc_id])}


def quit_button():
    return {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}


def open_adventurer_list(ctx, app, listed):
    """ギルドの「冒険者達と話す」を開いて、ゲームが並べた一覧を渡す。"""
    ctx.hooks["__main__:DisplayAdventurerTalkChoice.execute"](
        lambda self, choice_text=None: None, object(), "冒険者達と話す")
    app.buttons = [talk_button(npc_id) for npc_id in listed] + [quit_button()]
    ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"](lambda self: None, app)
    return app.buttons


def scene_guild_list():
    print("[ギルドの一覧]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id, trip = next(iter(read_ledger().items()))
    check("同じ街なら名簿に残る（実行時）", npc_id in app.world.areas["0"].adventurer_npcs)
    check("同じ街なら名簿に残る（セーブ側）",
          npc_id in app.save_data_dict["areas"]["0"]["adventurer_npcs"])

    # ゲームが居場所で絞った一覧（旅の人が漏れている）。
    others = [cid for cid in ("10", "11", "12") if cid != npc_id]
    buttons = open_adventurer_list(ctx, app, others)
    texts = [entry["text"] for entry in buttons]
    ids = [(entry["spec"].args or [None])[0] for entry in buttons]
    check("漏れていたら足す", npc_id in ids, texts)
    check("居場所が付く", any(t.startswith("冒険者{}（".format(npc_id)) for t in texts), texts)
    check("押すとゲームの会話が始まる形",
          buttons[ids.index(npc_id)]["spec"].cls_name == "ConversationStartManager")
    check("「やめる」の手前", ids.index(npc_id) < len(ids) - 1, texts)

    # ゲームが名簿だけで組む場合（旅の人も並んでいる）。
    buttons = open_adventurer_list(ctx, app, others + [npc_id])
    ids = [(entry["spec"].args or [None])[0] for entry in buttons]
    check("二重にしない", ids.count(npc_id) == 1, ids)
    check("並んでいる側にも居場所を添える",
          buttons[ids.index(npc_id)]["text"].startswith("冒険者{}（".format(npc_id)),
          buttons[ids.index(npc_id)]["text"])

    # 「会話する」の一覧では足さない。
    ctx.hooks["__main__:DisplayTalkChoice.execute"](
        lambda self, choice_text=None: None, object(), "会話する")
    app.buttons = [talk_button(cid) for cid in others] + [quit_button()]
    ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"](lambda self: None, app)
    ids = [(entry["spec"].args or [None])[0] for entry in app.buttons]
    check("「会話する」には足さない", npc_id not in ids, ids)

    # 別の街へ出た人は元の街の一覧に足さない。
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    away_id = next(iter(read_ledger()))
    others = [cid for cid in ("10", "11", "12") if cid != away_id]
    buttons = open_adventurer_list(ctx, app, others)
    ids = [(entry["spec"].args or [None])[0] for entry in buttons]
    check("別の街の人は足さない", away_id not in ids, ids)
    check("落ちていない", not ctx.errors, ctx.errors)

    # 添え字を空にすると名前だけ。
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100,
                            LOCAL_LIST_SUFFIX="")
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id = next(iter(read_ledger()))
    others = [cid for cid in ("10", "11", "12") if cid != npc_id]
    buttons = open_adventurer_list(ctx, app, others)
    texts = [entry["text"] for entry in buttons]
    check("空にすると名前だけ", "冒険者{}".format(npc_id) in texts, texts)


def add_adventurers(app, fac, area_id, facility_id, ids, affinity=30):
    """その街のギルドに冒険者を足す（実行時とセーブ側の両方）。"""
    area = app.world.areas[area_id]
    facility = fac[area_id][facility_id]
    for npc_id in ids:
        character = Character(npc_id, "冒険者{}".format(npc_id), affinity, facility)
        app.world.characters[npc_id] = character
        facility.characters.append(npc_id)
        area.adventurer_npcs.append(npc_id)
        app.save_data_dict["areas"][area_id]["adventurer_npcs"].append(npc_id)
        app.save_data_dict["npcs"][npc_id] = {
            "name": character.name, "ability_scores": {},
            "relationship": {"player": {"affinity": affinity}},
            "current_area": area_id, "current_location": facility_id}


def read_seen():
    path = ledger_path()
    if not os.path.exists(path):
        return {}
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh).get("seen", {})


def stand_in(app, fac, area_id, facility_id):
    """プレイヤーをその街のその施設に立たせる。"""
    app.player.current_area = app.world.areas[area_id]
    app.player.location = fac[area_id][facility_id]


def scene_only_here():
    print("[現在地だけ（同じ街の移動）]")
    # プレイヤーは街0。街1にも冒険者3人。
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=50,
                            LOCAL_ONLY_HERE=True)
    app, fac = make_world(affinities=(30, 30, 30))
    add_adventurers(app, fac, "1", G1, ["20", "21", "22"])
    use(app)
    for _ in range(4):
        elapse(ctx, app, 30)
    trips = read_ledger()
    there = {cid: t for cid, t in trips.items() if t["origin_area"] == "1"}
    check("居ない街では同じ街の移動が起きない",
          there and all(t["kind"] == "away" for t in there.values()), there)
    check("居ない街からも別の街へは出る", there, trips)
    check("落ちていない", not ctx.errors, ctx.errors)

    # 切れば他の街でも同じ街の中を動く。
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100,
                            LOCAL_ONLY_HERE=False)
    app, fac = make_world(affinities=(30, 30, 30))
    add_adventurers(app, fac, "1", G1, ["20", "21", "22"])
    use(app)
    elapse(ctx, app, 30)
    there = {cid: t for cid, t in read_ledger().items() if t["origin_area"] == "1"}
    check("設定を切ると居ない街でも同じ街の移動が起きる",
          any(t["kind"] == "local" for t in there.values()), there)


def scene_arrival():
    print("[到着時の抽選]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100)
    app, fac = make_world(affinities=(30, 30, 30))
    add_adventurers(app, fac, "1", G1, ["20", "21", "22"])
    use(app)
    elapse(ctx, app, 30)                       # 街0で引く
    seen = read_seen()
    check("引いた街の日が控えられる", seen.get("0") == 40, seen)
    check("居ない街は引いていない", "1" not in seen, seen)
    before = dict(read_ledger())

    # 日を進めずに街1へ着く（移動そのものは move_phase）。門に着く。
    stand_in(app, fac, "1", E1)
    moved_player(ctx, app)
    trips = read_ledger()
    there = {cid: t for cid, t in trips.items() if t["origin_area"] == "1"}
    check("着いた街で引く（日は進んでいない）", there, trips)
    check("引いたのは街中の移動", all(t["kind"] == "local" for t in there.values()), there)
    check("着いた街の日が控えられる", read_seen().get("1") == 40, read_seen())
    check("別の街の旅は増えていない",
          {c for c, t in trips.items() if t["kind"] == "away"}
          == {c for c, t in before.items() if t["kind"] == "away"})

    # 続けて施設を移っても、同じ日には引き直さない。
    count = len(read_ledger())
    stand_in(app, fac, "1", I1)
    moved_player(ctx, app)
    moved_player(ctx, app)
    check("同じ日には引き直さない", len(read_ledger()) == count, read_ledger())
    check("落ちていない", not ctx.errors, ctx.errors)


def scene_in_the_way():
    print("[目の前では動かさない]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100)
    app, fac = make_world(affinities=(30, 30, 30))
    add_adventurers(app, fac, "1", G1, ["20", "21", "22"])
    use(app)
    stand_in(app, fac, "1", G1)          # 冒険者と同じギルドに立つ
    moved_player(ctx, app)
    check("居合わせた相手は出発しない",
          not [t for t in read_ledger().values() if t["origin_area"] == "1"],
          read_ledger())
    check("その街の日は控えない（次の機会に引き直す）", "1" not in read_seen(), read_seen())
    stand_in(app, fac, "1", E1)          # 門へ移る
    moved_player(ctx, app)
    there = [t for t in read_ledger().values() if t["origin_area"] == "1"]
    check("離れたら同じ窓で出発する", there, read_ledger())
    check("引き直した後は日を控える", read_seen().get("1") == 10, read_seen())
    check("落ちていない", not ctx.errors, ctx.errors)


def scene_return():
    print("[帰還]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    trips = read_ledger()
    npc_id, trip = next(iter(trips.items()))
    dest = fac["1"][trip["dest_facility"]]
    check("行き先の名簿に居る", npc_id in dest.characters)
    elapse(ctx, app, 10)
    check("期限前は帰らない", npc_id in read_ledger())
    elapse(ctx, app, 100)
    check("期限後に台帳から消える", npc_id not in read_ledger())
    check("元のギルドへ戻った", app.world.characters[npc_id].location is fac["0"][G0])
    check("行き先の名簿から外れた", npc_id not in dest.characters)
    check("元の一覧に戻った（実行時）", npc_id in app.world.areas["0"].adventurer_npcs)
    check("元の一覧に戻った（セーブ側）",
          npc_id in app.save_data_dict["areas"]["0"]["adventurer_npcs"])
    check("行き先の一覧からは外れた", npc_id not in app.world.areas["1"].adventurer_npcs)
    check("セーブ側の居場所も元に戻った",
          app.save_data_dict["npcs"][npc_id]["current_location"] == G0)
    check("落ちていない", not ctx.errors, ctx.errors)


def read_rest():
    path = ledger_path()
    if not os.path.exists(path):
        return {}
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh).get("rest", {})


def scene_rest():
    print("[休み]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100, REST_DAYS=90)
    app, fac = make_world(affinities=(30, 30, 30))       # day 10
    use(app)
    elapse(ctx, app, 30)                                  # day 40: A が出る（7日）
    a = next(iter(read_ledger()))
    elapse(ctx, app, 7)                                   # day 47: A が帰り、B が出る
    rest = read_rest()
    check("帰った日が控えられる", a in rest and rest[a]["home_day"] == 47
          and rest[a]["until_day"] == 137, rest)
    check("帰った直後の経過では出ない", a not in read_ledger(), read_ledger())
    module.DEPART_CHANCE_PERCENT = 0
    elapse(ctx, app, 7)                                   # day 54: B が帰る
    check("誰も旅に出ていない", not read_ledger())
    for cid in ("10", "11", "12"):
        if cid != a:
            app.world.characters[cid].relationship["player"]["affinity"] = 0
    module.DEPART_CHANCE_PERCENT = 100
    elapse(ctx, app, 30)                                  # day 84: A だけ候補だが休み中
    check("休みの間は出ない", not read_ledger() and a in read_rest())
    elapse(ctx, app, 30)                                  # day 114: まだ休み
    check("90日の途中も出ない", not read_ledger())
    elapse(ctx, app, 30)                                  # day 144: 休み明け（137）
    check("休みが明けると控えが消える", a not in read_rest(), read_rest())
    for _ in range(8):                                    # 確率 0.64/30日。8回で外れる確率は 1e-4
        if a in read_ledger():
            break
        elapse(ctx, app, 30)
    check("休みが明けた後は出られる", a in read_ledger(), read_ledger())

    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100, REST_DAYS=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    elapse(ctx, app, 7)
    check("0 なら控えない", not read_rest())


def scene_defer():
    print("[延期]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30), player_facility=G0)
    use(app)
    elapse(ctx, app, 30)
    check("プレイヤーがギルドに居る間は出発しない", not read_ledger() and not app.moved)
    app.player.location = fac["0"][E0]
    elapse(ctx, app, 30)
    check("出た後なら出発する", len(read_ledger()) == 1)

    npc_id, trip = next(iter(read_ledger().items()))
    app.player.current_area = app.world.areas["1"]
    app.player.location = fac["1"][trip["dest_facility"]]
    elapse(ctx, app, 200)
    check("プレイヤーが行き先に居る間は帰らない", npc_id in read_ledger())
    app.player.location = fac["1"][G1 if trip["dest_facility"] != G1 else I1]
    moved_player(ctx, app)
    check("施設を出たら帰る（日が進まなくても）", npc_id not in read_ledger())
    check("元のギルドに居る", app.world.characters[npc_id].location is fac["0"][G0])


def read_hired():
    path = ledger_path()
    if not os.path.exists(path):
        return {}
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh).get("hired", {})


def scene_hired():
    print("[雇用]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id, trip = next(iter(read_ledger().items()))
    moves_before = len(app.moved)
    app.game_variables["party"].append(npc_id)
    elapse(ctx, app, 1)
    check("雇われたら旅の表から消える", npc_id not in read_ledger())
    check("解散待ちの表に載る", npc_id in read_hired())
    check("動かさない", len(app.moved) == moves_before)
    check("同行中は名簿を触らない",
          (npc_id in app.world.areas["1"].adventurer_npcs) == trip["enrolled"]
          and npc_id not in app.world.areas["0"].adventurer_npcs)

    # 素のゲームの解散: initial_location（元の街のギルド）へ戻される。
    app.game_variables["party"].remove(npc_id)
    app.move_npc_to_facility(npc_id, app.world.characters[npc_id], fac["0"][G0])
    elapse(ctx, app, 1)
    check("解散後は居る街（元の街）の一覧に入る", npc_id in app.world.areas["0"].adventurer_npcs)
    check("行き先の一覧からは外す", npc_id not in app.world.areas["1"].adventurer_npcs)
    check("解散待ちの表から消える", npc_id not in read_hired())

    # 303 の解散: 居る街（街1）のギルドへ置かれる。
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id, trip = next(iter(read_ledger().items()))
    app.game_variables["party"].append(npc_id)
    elapse(ctx, app, 1)
    app.game_variables["party"].remove(npc_id)
    app.move_npc_to_facility(npc_id, app.world.characters[npc_id], fac["1"][G1])
    moved_player(ctx, app)
    check("303 で街1のギルドに置かれたら街1の一覧に入る",
          npc_id in app.world.areas["1"].adventurer_npcs
          and npc_id not in app.world.areas["0"].adventurer_npcs)
    check("解散待ちの表から消える（303）", npc_id not in read_hired())

    # 名簿と実体が食い違う人（303 が既に別の街へ置いた人）は出さない。
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    stray = app.world.characters["12"]
    fac["0"][G0].characters.remove("12")
    stray.location = fac["1"][G1]
    fac["1"][G1].characters.append("12")
    use(app)
    elapse(ctx, app, 30)
    check("実体が別の街に居る人は旅に出ない", "12" not in read_ledger())
    check("その人の枠で他の1人は出られる", len(read_ledger()) == 1, read_ledger())
    check("ログに理由", "stands in area '1'" in read_log(module), read_log(module)[-300:])
    check("落ちていない", not ctx.errors, ctx.errors)


def scene_removed():
    print("[消された人]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id, trip = next(iter(read_ledger().items()))
    del app.world.characters[npc_id]
    del app.save_data_dict["npcs"][npc_id]
    moves_before = len(app.moved)
    elapse(ctx, app, 1)
    check("世界から消えた人の旅は忘れる", npc_id not in read_ledger())
    check("動かそうとしない", len(app.moved) == moves_before)
    check("行き先の一覧からは外す", npc_id not in app.world.areas["1"].adventurer_npcs)
    check("落ちていない", not ctx.errors, ctx.errors)


def scene_capacity():
    print("[上限]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0,
                            CAPACITY_PER_FACILITY=1)
    app, fac = make_world(affinities=(30, 30, 30, 30, 30))
    use(app)
    elapse(ctx, app, 120)
    trips = read_ledger()
    check("3人出た", len(trips) == 3, trips)
    spots = [(t["dest_area"], t["dest_facility"]) for t in trips.values()]
    check("同じ施設に2人は行かない（街1に2施設、残りは同じ街）",
          len(set(spots)) == len(spots), spots)


def scene_context():
    print("[文脈]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id, trip = next(iter(read_ledger().items()))
    npc = app.world.characters[npc_id]
    other = app.world.characters[[cid for cid in ("10", "11", "12") if cid != npc_id][0]]
    seen = {}

    def orig(a, b, c, character_instance=None):
        seen["npc"] = character_instance
        return "ok"

    hook = ctx.hooks["scripts.llm.llm_manager:conversation_facilitator"]
    result = hook(orig, "m", "h", "x", npc)
    check("本体は呼ばれる", result == "ok")
    given = seen["npc"]
    check("複製に1文足す", given is not npc and "旅をして来て" in given.profile, given.profile)
    check("土地の名前が入る", "始まりの泥濘" in given.profile and "灰の交易都市" in given.profile,
          given.profile)
    check("本体の profile は変えない", "旅をして来て" not in npc.profile)
    hook(orig, "m", "h", "x", other)
    check("旅に出ていない相手はそのまま", seen["npc"] is other)
    hook(orig, "m", "h", "x", character_instance=npc)
    check("keyword でも足す", seen["npc"] is not npc and "旅をして来て" in seen["npc"].profile)

    # まだ細部の生成されていない個体（profile が None）。
    npc.profile = None
    hook(orig, "m", "h", "x", npc)
    check("profile が None でも足す",
          seen["npc"] is not npc and seen["npc"].profile.startswith(trip["name"]),
          seen["npc"].profile)
    check("本体の profile は None のまま", npc.profile is None)
    npc.profile = ["読めない形"]
    hook(orig, "m", "h", "x", npc)
    check("読めない形の profile は触らない", seen["npc"] is npc)

    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=100)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id, trip = next(iter(read_ledger().items()))
    hook = ctx.hooks["scripts.llm.llm_manager:conversation_starter"]
    hook(orig, "m", "h", "x", app.world.characters[npc_id])
    check("同じ街の文は LOCAL_CONTEXT", "いつものギルドを離れ" in seen["npc"].profile,
          seen["npc"].profile)


def refresh(ctx, app):
    ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"](lambda self: None, app)


def scene_load():
    print("[ロード]")
    # 旅に出た状態の台帳を作る。
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    npc_id, trip = next(iter(read_ledger().items()))

    # 古いセーブ（出発前の日付）を読む → 忘れる。
    module, ctx = fresh_mod(keep_state=True)
    app2, fac2 = make_world(days=5, affinities=(30, 30, 30))
    use(app2)
    ctx.hooks["__main__:InstantaleApp.load_game_new"](lambda self, name: None, app2, "w")
    refresh(ctx, app2)
    check("古いセーブなら旅を忘れる", npc_id not in read_ledger())
    check("動かさない", not app2.moved)

    # 台帳を戻して、新しいセーブ（出発後・行き先に居ない）を読む → 置き直す。
    module, ctx = fresh_mod(keep_state=True)
    key, bucket = module._store()["worlds"].of(app)
    bucket["trips"][npc_id] = dict(trip)
    module._store()["worlds"].save(key)
    app3, fac3 = make_world(days=50, affinities=(30, 30, 30))
    use(app3)
    ctx.hooks["__main__:InstantaleApp.start_game"](lambda self, name: None, app3, "w")
    refresh(ctx, app3)
    check("行き先に居なければ置き直す",
          app3.world.characters[npc_id].location is fac3["1"][trip["dest_facility"]],
          app3.moved)
    check("元の一覧から外す", npc_id not in app3.world.areas["0"].adventurer_npcs)
    check("台帳は残る", npc_id in read_ledger())

    # RETURN_ALL_ON_LOAD → 帰る。
    module, ctx = fresh_mod(keep_state=True, RETURN_ALL_ON_LOAD=True)
    app4, fac4 = make_world(days=50, affinities=(30, 30, 30))
    app4.world.characters[npc_id].location = fac4["1"][trip["dest_facility"]]
    app4.world.areas["0"].adventurer_npcs.remove(npc_id)
    use(app4)
    ctx.hooks["__main__:InstantaleApp.load_game_new"](lambda self, name: None, app4, "w")
    refresh(ctx, app4)
    check("RETURN_ALL_ON_LOAD で帰る", not read_ledger()
          and app4.world.characters[npc_id].location is fac4["0"][G0])
    check("一覧も戻る", npc_id in app4.world.areas["0"].adventurer_npcs)
    check("落ちていない", not ctx.errors, ctx.errors)


def scene_store():
    print("[控え]")
    module, ctx = fresh_mod(DEPART_CHANCE_PERCENT=100, LOCAL_CHANCE_PERCENT=0)
    app, fac = make_world(affinities=(30, 30, 30))
    use(app)
    elapse(ctx, app, 30)
    path = ledger_path()
    check("state/npc_travel/<世界名>.json に書く", os.path.exists(path), path)
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    row = next(iter(data["trips"].values()))
    check("行の並びは固定", list(row) == list(module.travel.TRIP_FIELDS), list(row))
    check("版が入る", data.get("version") == module.travel.LEDGER_VERSION)


def scene_pure():
    print("[判定の部品]")
    travel = load_mod().travel
    rng = random.Random(3)
    check("残す人数を割らない", travel.pick_departures(rng, ["a", "b", "c"], 3, 2, 1.0) == ["a"]
          or len(travel.pick_departures(random.Random(3), ["a", "b", "c"], 3, 2, 1.0)) == 1)
    check("枠が無ければ空", travel.pick_departures(rng, ["a"], 2, 2, 1.0) == [])
    check("確率0なら空", travel.pick_departures(rng, ["a"], 5, 2, 0.0) == [])
    check("30日で設定値に近い", abs(travel.chance_over_days(30, 30) - 0.26) < 0.02,
          travel.chance_over_days(30, 30))
    check("100%は1日で1.0に近い", travel.chance_over_days(100, 30) > 0.63)
    check("日数0なら0", travel.chance_over_days(50, 0) == 0.0)
    check("期間は範囲内", all(30 <= travel.pick_duration(rng, 30, 90) <= 90 for _ in range(50)))
    check("逆でも動く", 30 <= travel.pick_duration(rng, 90, 30) <= 90)
    check("月あたりの確率を種類で分ける",
          abs(travel.kind_chance(30, 50, "local", 30)
              - travel.chance_over_days(15, 30)) < 1e-9
          and abs(travel.kind_chance(30, 50, "away", 30)
                  - travel.chance_over_days(15, 30)) < 1e-9)
    check("割合0なら街中は起きない", travel.kind_chance(30, 0, "local", 30) == 0.0
          and travel.kind_chance(30, 100, "away", 30) == 0.0)
    check("引いてからの日数（記録が無ければ上限）",
          travel.catchup_days({}, "0", 100, 30) == 30
          and travel.catchup_days({"0": 90}, "0", 100, 30) == 10
          and travel.catchup_days({"0": 100}, "0", 100, 30) == 0
          and travel.catchup_days({"0": 50}, "0", 100, 30) == 30)
    check("日が読めなければ0", travel.catchup_days({}, "0", None, 30) == 0)
    check("文型の鍵が足りなくても落ちない",
          travel.format_context("{name}が{nothing}に", name="x") == "xがに")
    check("空の文型は空", travel.format_context("", name="x") == "")
    check("friendliness は int", travel.affinity_value({"player": {"affinity": 25}}) == 25
          and travel.affinity_value({"player": {"affinity": True}}) is None
          and travel.affinity_value(None) is None)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    scene_pure()
    scene_depart()
    scene_keep()
    scene_local()
    scene_guild_list()
    scene_only_here()
    scene_arrival()
    scene_in_the_way()
    scene_return()
    scene_rest()
    scene_defer()
    scene_hired()
    scene_removed()
    scene_capacity()
    scene_context()
    scene_load()
    scene_store()
    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
