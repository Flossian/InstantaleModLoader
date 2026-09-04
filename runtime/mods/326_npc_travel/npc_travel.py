# -*- coding: utf-8 -*-
"""街の冒険者が旅に出る。別の街のギルドか宿に現れ、期限が来たら帰る。

素のゲームの NPC は生成された施設から一歩も動かない（GAME.md §2.8。
「動かした NPC は自主的には持ち場へ戻らない」）。
このMODは、日数が進むたびに各街のギルドの冒険者から出発する者を引き、
ゲーム自身の `move_npc_to_facility` で置き直す。
**動かした者はこのMODが帰す**（同 §2.8 の「戻す責任」）。
誰がどこへ何日まで、は `state\\npc_travel\\<世界>.json` の台帳に持つ。

この版の対象は冒険者（`Area.adventurer_npcs` に載っている者）だけ。
店主や住人へ広げる余地を名前に残してある。

##### 決まり

- 出るのは友好度（`relationship.player.affinity`）が閾値以上の者。
  出発の瞬間だけ見る
- 各街に必ず `STAY_MIN` 人（既定2）を残す。
  数えるのは「いまギルドの一覧に出る人」（生存・非同行・旅に出ていない）
- 別の街へは 30〜90 日（既定）。行き先は施設が生成済みの街のギルドか宿
- 同じ街の中なら 7 日（既定）。行き先はギルド以外の施設
  （通路は除く。`travel.PASSAGE_TYPES`）
- 旅の間は元の街のギルドの一覧（`adventurer_npcs`）から外す。
  別の街のギルドに着いた者はそこの一覧に載る＝雇える
- 帰ってきた人は `REST_DAYS`（既定 90）の間は次の旅に出ない
  （実機で同じ人が続けて引かれたので足した。旅先で雇われて解散した人も同じ）
- 出発も帰還も、プレイヤーがその施設に居る間は次の機会まで延ばす
  （目の前で消えたり湧いたりしない）
- 旅先で雇われた者は旅を終える。帰さない。解散のときにゲームが
  `initial_location`＝元の街へ戻すか、`303_` が居る街のギルドへ置くかは
  こちらでは決めない。**解散した後に実際に居る街の名簿へ入れる**
  （台帳の `hired` に控えて、パーティから外れた時点で見る）。死んだ者も帰さない
- 名簿の街と実体の居る街が食い違う人（`303_` が別の街に置いた人）は
  旅に出さない。帰り先が決められないため
- 旅先で話しかけると、会話の文脈に「〜から来ている」を1文足す。
  元の街では何も出さない（決めた仕様）

##### ロードのとき

台帳は state\\、居場所はセーブに在る。
両方が揃って初めて成り立つので、ロードの直後（最初に選択肢が組まれた時）に
突き合わせる:

- セーブの日付が出発日より前（古いセーブを読んだ）→ その旅は無かったことにする
- 台帳の行き先に居ない → 置き直す
- `RETURN_ALL_ON_LOAD` が入 → 全員をその場で帰す（MODを外す前の片付け）

ロードの直後は `world.characters` がほぼ空（GAME.md §2.7 の表）なので、
ロードのフックの中では動かさず、旗だけ立てる。
"""

import copy
import random
import sys

from instantale_modloader import state as loader_state, ui
from instantale_modloader.npcs import npc_stores, save_npcs

from . import travel

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
DEPART_CHANCE_PERCENT = 30   # 冒険者1人あたり、月あたりの出発確率（%）
LOCAL_CHANCE_PERCENT = 50    # 出発のうち同じ街の中へ向かう割合（%）
STAY_MIN = 2                 # 各街に必ず残す人数
AFFINITY_MIN = 20            # 出発できる友好度の下限
AWAY_DAYS_MIN = 30           # 別の街への滞在日数の下限
AWAY_DAYS_MAX = 90           # 同 上限
LOCAL_DAYS = 7               # 同じ街の中の滞在日数
CAPACITY_PER_FACILITY = 2    # 1施設に旅で来られる人数。0 で上限なし
REST_DAYS = 90               # 帰ってから次に出るまでの日数
RETURN_ALL_ON_LOAD = False   # ロードのとき台帳の全員を帰す（片付け）
AWAY_CONTEXT = "{name}は{origin}から旅をして来て、いまは{where}に滞在している（あと{days}日ほど）。"
LOCAL_CONTEXT = "{name}はいつものギルドを離れ、いまは{where}に来ている（あと{days}日ほど）。"

# ---- 設定にしない定数 ----------------------------------------------------
LOG_BASENAME = "npc_travel.log"
STATE_DIRNAME = "npc_travel"          # state\ 直下のフォルダ。MOD専用の名前
STORE_ATTR = "_instantale_npc_travel"  # 世代をまたぐ入れ物（sys の属性）
GUILD_TYPE = "guild"

#: ロードの入口。新規と続きの両方（名前では決められない。GAME.md §1.3）。
LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",
                "__main__:InstantaleApp.start_game")

#: 会話の返答を作る LLM 呼び出し（`403_` と同じ5本）。
#: 第4引数（index 3）か `character_instance` が会話相手の Character。
CONVERSATION_TARGETS = (
    ("scripts.llm.llm_manager:conversation_facilitator", "facilitator"),
    ("scripts.llm.llm_manager:conversation_facilitator_after_retrieval",
     "facilitator[retrieval]"),
    ("scripts.llm.llm_manager:conversation_facilitator_in_quest",
     "facilitator[quest]"),
    ("scripts.llm.llm_manager:conversation_starter", "starter"),
    ("scripts.llm.llm_manager:conversation_starter_in_quest", "starter[quest]"),
)


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None, "reconcile": False, "rng": random.Random()}
        setattr(sys, STORE_ATTR, store)
    return store


# ============================================================ 世界を読む部品
# （方針を持たない。ゲームのどこに何があるかだけ）

def id_of(obj):
    """施設・エリア・Character の id。文字列で。読めなければ ""。"""
    if obj is None:
        return ""
    if isinstance(obj, (str, int)) and not isinstance(obj, bool):
        return str(obj)
    value = getattr(obj, "id", None)
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def area_size(app, area_id):
    """土地の種類。実行時の `Area.size` は読めないのでセーブ側から（GAME.md §2.7）。"""
    for attr in ("save_data_dict", "world_dict"):
        container = getattr(app, attr, None)
        if not isinstance(container, dict):
            continue
        holders = [container]
        inner = container.get("world_data")
        if isinstance(inner, dict):
            holders.append(inner)
        for holder in holders:
            areas = holder.get("areas")
            entry = areas.get(str(area_id)) if isinstance(areas, dict) else None
            size = entry.get("size") if isinstance(entry, dict) else None
            if isinstance(size, str) and size:
                return size
    return ""


def roster_lists(app, area, area_id):
    """`adventurer_npcs` の心当たり全部（実行時 + セーブ側）。`320_` の enroll と同じ。"""
    found = []
    roster = getattr(area, "adventurer_npcs", None) if area is not None else None
    if isinstance(roster, list):
        found.append(roster)
    for root in (getattr(app, "world_dict", None), getattr(app, "save_data_dict", None)):
        if not isinstance(root, dict):
            continue
        holders = [root]
        inner = root.get("world_data")
        if isinstance(inner, dict):
            holders.append(inner)
        for holder in holders:
            areas = holder.get("areas")
            entry = areas.get(str(area_id)) if isinstance(areas, dict) else None
            raw = entry.get("adventurer_npcs") if isinstance(entry, dict) else None
            if isinstance(raw, list) and all(raw is not seen for seen in found):
                found.append(raw)
    return found


def roster_add(app, area, area_id, npc_id):
    npc_id = str(npc_id)
    for roster in roster_lists(app, area, area_id):
        if npc_id not in roster:
            roster.append(npc_id)


def roster_drop(app, area, area_id, npc_id):
    npc_id = str(npc_id)
    for roster in roster_lists(app, area, area_id):
        while npc_id in roster:
            roster.remove(npc_id)


def facility_drop(facility, npc_id):
    """施設の名簿（`Facility.characters`）から外す。重複も全部。"""
    present = getattr(facility, "characters", None)
    if isinstance(present, list):
        npc_id = str(npc_id)
        while npc_id in present:
            present.remove(npc_id)


def set_save_location(app, npc_id, area_id, facility_id):
    """セーブ側の `current_area` / `current_location` を置き先に合わせる。

    ゲームがロードで名簿を組み直すときに読むのはこちら
    （施設は名簿を持たず、NPC 側の居場所から組む）。
    心当たりの辞書すべてに書く（`npcs.make_npc` と同じ）。
    """
    npc_id = str(npc_id)
    for where, store in npc_stores(app):
        if "characters" in where.rsplit(".", 1)[-1]:
            continue                    # 実行時の名簿。素データではない
        data = store.get(npc_id)
        if isinstance(data, dict):
            data["current_area"] = str(area_id)
            data["current_location"] = str(facility_id)


def exists(app, npc_id):
    """その NPC がまだ世界に居るか（実行時の名簿か素データのどちらか）。

    別の MOD やセーブエディタが消した人の行を台帳に残さないため。
    ロード直後は実行時の名簿が空なので、素データも見る。
    """
    if ui.character_of(app, npc_id) is not None:
        return True
    return str(npc_id) in save_npcs(app)


def is_dead(character):
    config = getattr(character, "config", None)
    return bool(config.get("is_dead")) if isinstance(config, dict) else False


def facility_of_character(character):
    """Character の今の施設 id。`location` は Facility か id の文字列（GAME.md §2.7）。"""
    return id_of(getattr(character, "location", None))


def player_at(app, area_id, facility_id) -> bool:
    """プレイヤーがその施設に居るか。読めなければ False（居ないとみなす）。"""
    player = getattr(app, "player", None)
    if player is None:
        return False
    here_area = id_of(getattr(player, "current_area", None))
    here_facility = id_of(getattr(player, "location", None))
    return here_area == str(area_id) and here_facility == str(facility_id)


def area_of_facility(app, facility_id, preferred=""):
    """その施設 id を持つ土地の id。まず `preferred`、次に全土地。無ければ ""。

    施設 id は土地の中でしか一意でない（GAME.md §2.7）ので、
    心当たりの土地を先に見る。
    """
    if not facility_id:
        return ""
    areas = ui.world_areas(app) or {}
    order = ([str(preferred)] if str(preferred) in areas else []) + \
        [str(key) for key in areas if str(key) != str(preferred)]
    for area_id in order:
        facility, _node = ui.find_facility(areas.get(area_id), facility_id)
        if facility is not None:
            return area_id
    return ""


def area_name(area, area_id):
    name = getattr(area, "name", None)
    return name if isinstance(name, str) and name.strip() else "土地{}".format(area_id)


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(
            ctx, STATE_DIRNAME, default=travel.new_bucket,
            order=travel.order_bucket, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    rng = store["rng"]

    def ledger(app):
        """この世界の `(鍵, 旅の表)`。"""
        key, bucket = worlds.of(app)
        return key, travel.trips_of(bucket)

    def hired_table(app):
        _key, bucket = worlds.of(app)
        return travel.hired_of(bucket)

    def rest_table(app):
        _key, bucket = worlds.of(app)
        return travel.rest_of(bucket)

    def start_rest(app, npc_id, name, day):
        """帰ってきた（解散した）日を控える。日が読めないときは休み無し。"""
        if day is None or REST_DAYS <= 0:
            return
        rest_table(app)[str(npc_id)] = travel.make_rest(name, day, REST_DAYS)

    # ------------------------------------------------------------ 街と施設
    def known_towns(app):
        """施設が生成済みの街 `{area_id: Area}`。ダンジョン・未生成は入らない。"""
        towns = {}
        for area_id, area in (ui.world_areas(app) or {}).items():
            area_id = str(area_id)
            size = area_size(app, area_id)
            if size and not travel.is_town(size):
                continue
            if not any(ui.facilities_of(node) for node in ui.nodes_of(area)):
                continue
            towns[area_id] = area
        return towns

    def spots_in(area, area_id, kinds=None, exclude_types=(), exclude_facility=""):
        """`[(area_id, facility_id, 種別)]`。並びは毎回同じ。"""
        found = []
        for node in ui.nodes_of(area):
            for key, facility in ui.facilities_of(node).items():
                kind = ui.facility_type_of(facility)
                facility_id = id_of(facility) or str(key)
                if kinds is not None and kind not in kinds:
                    continue
                if kind in exclude_types or not kind:
                    continue
                if facility_id == str(exclude_facility):
                    continue
                found.append((str(area_id), facility_id, kind))
        found.sort(key=lambda row: (ui.id_sort_key(row[1]), row[2]))
        return found

    def with_room(trips, spots):
        if CAPACITY_PER_FACILITY <= 0:
            return spots
        return [spot for spot in spots
                if travel.occupancy(trips, spot[0], spot[1]) < CAPACITY_PER_FACILITY]

    def away_spots(app, trips, towns, origin_id):
        found = []
        for area_id, area in towns.items():
            if area_id == str(origin_id):
                continue
            found.extend(spots_in(area, area_id, kinds=travel.AWAY_TYPES))
        return with_room(trips, found)

    def local_spots(app, trips, area, area_id, current_facility):
        found = spots_in(area, area_id,
                         exclude_types=(GUILD_TYPE,) + travel.PASSAGE_TYPES,
                         exclude_facility=current_facility)
        return with_room(trips, found)

    def where_text(app, area_id, facility_id):
        """「〈土地〉の〈施設〉」。施設名が読めなければ土地だけ。"""
        area = (ui.world_areas(app) or {}).get(str(area_id))
        town = area_name(area, area_id)
        facility, _node = ui.find_facility(area, facility_id)
        name = ui.facility_name(app, facility) if facility is not None else ""
        return "{}の{}".format(town, name) if name else town

    # ------------------------------------------------------------ 冒険者を数える
    def affinity_of(app, npc_id, character):
        value = travel.affinity_value(getattr(character, "relationship", None))
        if value is None:
            value = travel.affinity_value(
                (save_npcs(app).get(str(npc_id)) or {}).get("relationship"))
        return value

    def guild_members(app, area, area_id, trips, day):
        """`(一覧に出る人数, 出発できる id の並び)`。

        一覧に出る＝名簿に在り、生きていて、同行しておらず、旅に出ていない。
        `Area.adventurer_npcs` は刈り込まれない（死亡も同行中も残る。`320_`）ので
        自分で引き直す。休み中の人は一覧には出るが出発の候補にしない。
        """
        rosters = roster_lists(app, area, area_id)
        roster = rosters[0] if rosters else []
        party = {str(member) for member in ui.party_member_ids(app)}
        hired = hired_table(app)
        rest = rest_table(app)
        present, eligible = [], []
        for raw in roster:
            npc_id = str(raw)
            if npc_id in trips or npc_id in party or npc_id in hired:
                continue
            character = ui.character_of(app, npc_id)
            if character is None or is_dead(character):
                continue
            if npc_id in present:
                continue
            present.append(npc_id)
            affinity = affinity_of(app, npc_id, character)
            if affinity is None or affinity < AFFINITY_MIN:
                continue
            if travel.is_resting(rest, npc_id, day):
                continue
            here = facility_of_character(character)
            stands_in = area_of_facility(app, here, area_id) if here else str(area_id)
            if stands_in != str(area_id):
                # 名簿は area_id だが実体は別の街（`303_` が置いた人）。
                # 一覧には出るので残す人数には数え、旅には出さない（帰り先を決められない）。
                write("skip: {} is listed in area {} but stands in area {!r}"
                      .format(npc_id, area_id, stands_in))
                continue
            eligible.append(npc_id)
        return present, eligible

    # ------------------------------------------------------------ 置く・戻す
    def move(app, npc_id, character, area, area_id, facility_id):
        """ゲーム自身の `move_npc_to_facility` で置く。成否を返す。"""
        target, node = ui.find_facility(area, facility_id)
        mover = getattr(app, "move_npc_to_facility", None)
        if target is None or not callable(mover):
            write("move: facility {}/{} not found or move_npc_to_facility missing"
                  .format(area_id, facility_id))
            return False
        before = facility_of_character(character)
        before_area = id_of(getattr(character, "current_area", None))
        try:
            mover(npc_id, character, target, node)
        except Exception:
            ctx.log_exc("npc travel: move_npc_to_facility({!r}) failed".format(npc_id))
            return False
        # 前の施設の名簿に残っていたら外す（ゲームが外すかは版による。二重にならない）。
        if before and before != str(facility_id):
            old_area = (ui.world_areas(app) or {}).get(before_area or str(area_id))
            for candidate_area in (old_area, area):
                old, _n = ui.find_facility(candidate_area, before)
                if old is not None and old is not target:
                    facility_drop(old, npc_id)
                    break
        set_save_location(app, npc_id, area_id, facility_id)
        return True

    def depart(app, key, trips, day, npc_id, area, area_id, kind, spot):
        character = ui.character_of(app, npc_id)
        if character is None:
            return False
        origin_facility = facility_of_character(character)
        if not origin_facility:
            guild, _node = ui.find_guild(area)
            origin_facility = id_of(guild)
        if not origin_facility:
            write("depart: {} has no facility to come back to; skipped".format(npc_id))
            return False
        stands_in = area_of_facility(app, origin_facility, area_id)
        if stands_in != str(area_id):
            # 名簿は area_id だが実体は別の街（`303_` が置いた人）。帰り先を決められない。
            write("depart: {} is listed in area {} but stands in area {!r}; skipped"
                  .format(npc_id, area_id, stands_in))
            return False
        if player_at(app, area_id, origin_facility):
            write("depart: player is at {}/{}; {} waits".format(
                area_id, origin_facility, npc_id))
            return False
        dest_area_id, dest_facility, dest_kind = spot
        dest_area = (ui.world_areas(app) or {}).get(dest_area_id)
        if not move(app, npc_id, character, dest_area, dest_area_id, dest_facility):
            return False
        days = (travel.pick_duration(rng, AWAY_DAYS_MIN, AWAY_DAYS_MAX)
                if kind == travel.AWAY else max(1, int(LOCAL_DAYS)))
        enrolled = kind == travel.AWAY and dest_kind == GUILD_TYPE
        roster_drop(app, area, area_id, npc_id)
        if enrolled:
            roster_add(app, dest_area, dest_area_id, npc_id)
        name = ui.character_name(app, npc_id)
        trips[npc_id] = travel.make_trip(
            name, kind, area_id, origin_facility, dest_area_id, dest_facility,
            day, days, enrolled)
        write("depart: {} ({}) {} {}/{} -> {}/{} [{}] day {} .. {}".format(
            name, npc_id, kind, area_id, origin_facility, dest_area_id,
            dest_facility, dest_kind, day, day + days))
        return True

    def restore_rosters(app, npc_id, trip):
        origin_area = (ui.world_areas(app) or {}).get(trip["origin_area"])
        dest_area = (ui.world_areas(app) or {}).get(trip["dest_area"])
        if trip.get("enrolled"):
            roster_drop(app, dest_area, trip["dest_area"], npc_id)
        roster_add(app, origin_area, trip["origin_area"], npc_id)

    def come_back(app, trips, npc_id, trip, reason):
        """帰す。帰せたら台帳から落として True。"""
        character = ui.character_of(app, npc_id)
        if character is None:
            write("return: {} is not in world.characters; kept".format(npc_id))
            return False
        origin_area = (ui.world_areas(app) or {}).get(trip["origin_area"])
        if origin_area is None:
            write("return: origin area {} missing; kept".format(trip["origin_area"]))
            return False
        facility_id = trip["origin_facility"]
        target, _node = ui.find_facility(origin_area, facility_id)
        if target is None:
            guild, _node = ui.find_guild(origin_area)
            facility_id = id_of(guild)
        if not facility_id:
            write("return: nowhere to put {} in area {}; kept".format(
                npc_id, trip["origin_area"]))
            return False
        if not move(app, npc_id, character, origin_area, trip["origin_area"], facility_id):
            return False
        dest_area = (ui.world_areas(app) or {}).get(trip["dest_area"])
        dest, _node = ui.find_facility(dest_area, trip["dest_facility"])
        if dest is not None:
            facility_drop(dest, npc_id)
        restore_rosters(app, npc_id, trip)
        trips.pop(npc_id, None)
        start_rest(app, npc_id, trip.get("name"), ui.game_day(app))
        write("return: {} ({}) -> {}/{} ({})".format(
            trip.get("name"), npc_id, trip["origin_area"], facility_id, reason))
        return True

    def close_without_moving(app, trips, npc_id, trip, reason):
        """死んだ。動かさずに旅を終え、名簿は元に戻す（死者は名簿に残る。GAME.md §2.22）。"""
        restore_rosters(app, npc_id, trip)
        trips.pop(npc_id, None)
        write("closed: {} ({}) {}".format(trip.get("name"), npc_id, reason))

    def close_hired(app, trips, npc_id, trip, day):
        """旅先で雇われた。名簿はそのまま（同行中は一覧に出ない）にして、解散を待つ。"""
        hired_table(app)[npc_id] = travel.make_hired(trip, day)
        trips.pop(npc_id, None)
        write("closed: {} ({}) hired on the road; roster settled after the party splits"
              .format(trip.get("name"), npc_id))

    def settle_hired(app, party):
        """解散した人を、いま実際に居る街の名簿へ入れる。

        ゲームは `initial_location`（元の街）へ戻し、`303_` は居る街のギルドへ置く。
        どちらでも、置かれた施設の土地の名簿に載れば一覧と実体が一致する。
        """
        hired = hired_table(app)
        changed = False
        for npc_id in sorted(hired, key=ui.id_sort_key):
            row = hired[npc_id]
            if not isinstance(row, dict):
                hired.pop(npc_id, None)
                changed = True
                continue
            if npc_id in party:
                continue
            character = ui.character_of(app, npc_id)
            if character is None:
                if not exists(app, npc_id):
                    hired.pop(npc_id, None)
                    changed = True
                    write("hired: {} is no longer in the world; forgotten".format(npc_id))
                continue
            facility_id = facility_of_character(character)
            home = area_of_facility(app, facility_id, row.get("origin_area", ""))
            if not home:
                write("hired: {} left the party but stands nowhere known ({!r}); kept"
                      .format(npc_id, facility_id))
                continue
            areas = ui.world_areas(app) or {}
            for other in (row.get("origin_area"), row.get("dest_area")):
                if other and str(other) != home:
                    roster_drop(app, areas.get(str(other)), other, npc_id)
            roster_add(app, areas.get(home), home, npc_id)
            hired.pop(npc_id, None)
            start_rest(app, npc_id, row.get("name"), ui.game_day(app))
            changed = True
            write("hired: {} ({}) left the party; enrolled in area {} (from {})"
                  .format(row.get("name"), npc_id, home, row.get("origin_area")))
        return changed

    # ------------------------------------------------------------ 1回の見回り
    def settle(app, day, reason):
        """期限の来た者を帰す。変更があれば台帳を書く。"""
        key, trips = ledger(app)
        party = {str(member) for member in ui.party_member_ids(app)}
        changed = False
        for npc_id in sorted(trips, key=ui.id_sort_key):
            trip = trips.get(npc_id)
            if not isinstance(trip, dict):
                trips.pop(npc_id, None)
                changed = True
                continue
            character = ui.character_of(app, npc_id)
            if character is None and not exists(app, npc_id):
                # 世界から消された人（別の MOD・セーブエディタ）。名簿だけ元に戻して忘れる。
                close_without_moving(app, trips, npc_id, trip, "no longer in the world")
                changed = True
                continue
            if npc_id in party:
                close_hired(app, trips, npc_id, trip, day)
                changed = True
                continue
            if character is not None and is_dead(character):
                close_without_moving(app, trips, npc_id, trip, "died on the road")
                changed = True
                continue
            if day is None or not travel.is_due(trip, day):
                continue
            if player_at(app, trip["dest_area"], trip["dest_facility"]):
                write("return: player is at {}/{}; {} waits".format(
                    trip["dest_area"], trip["dest_facility"], npc_id))
                continue
            if come_back(app, trips, npc_id, trip, reason):
                changed = True
        if settle_hired(app, party):
            changed = True
        if travel.prune_rest(rest_table(app), day):
            changed = True
        if changed:
            worlds.save(key)
        return changed

    def send_off(app, day, days):
        """各街で出発を引く。変更があれば台帳を書く。"""
        key, trips = ledger(app)
        towns = known_towns(app)
        chance = travel.chance_over_days(DEPART_CHANCE_PERCENT, days)
        changed = False
        for area_id in sorted(towns, key=ui.id_sort_key):
            area = towns[area_id]
            present, eligible = guild_members(app, area, area_id, trips, day)
            chosen = travel.pick_departures(rng, eligible, len(present), STAY_MIN, chance)
            if not chosen:
                continue
            write("roll: area {} present={} eligible={} chosen={} (chance {:.3f} over {} day(s))"
                  .format(area_id, len(present), len(eligible), chosen, chance, days))
            for npc_id in chosen:
                character = ui.character_of(app, npc_id)
                here = facility_of_character(character) if character is not None else ""
                away = away_spots(app, trips, towns, area_id)
                local = local_spots(app, trips, area, area_id, here)
                kind = travel.pick_kind(rng, LOCAL_CHANCE_PERCENT, bool(away), bool(local))
                if kind is None:
                    write("roll: nowhere for {} to go from area {}".format(npc_id, area_id))
                    continue
                spot = travel.pick_spot(rng, away if kind == travel.AWAY else local)
                if depart(app, key, trips, day, npc_id, area, area_id, kind, spot):
                    changed = True
        if changed:
            worlds.save(key)
        return changed

    def patrol(app, days, reason):
        if app is None or getattr(app, "world", None) is None:
            return
        day = ui.game_day(app)
        with worlds.lock:
            settle(app, day, reason)
            if day is not None and days > 0:
                send_off(app, day, days)

    # ------------------------------------------------------------ ロードの突き合わせ
    def reconcile(app):
        key, trips = ledger(app)
        if not trips and not hired_table(app) and not rest_table(app):
            return
        day = ui.game_day(app)
        write("reconcile: world {!r} day {} trips {}".format(key, day, len(trips)))
        changed = False
        for npc_id in sorted(trips, key=ui.id_sort_key):
            trip = trips[npc_id]
            if not isinstance(trip, dict):
                trips.pop(npc_id, None)
                changed = True
                continue
            if day is not None and day < int(trip.get("depart_day", 0)):
                # 旅より古いセーブ。セーブの中では出発していない。
                trips.pop(npc_id, None)
                changed = True
                write("reconcile: {} left on day {} but the save is on day {}; forgotten"
                      .format(npc_id, trip.get("depart_day"), day))
                continue
            if RETURN_ALL_ON_LOAD:
                if come_back(app, trips, npc_id, trip, "RETURN_ALL_ON_LOAD"):
                    changed = True
                continue
            character = ui.character_of(app, npc_id)
            if character is None:
                continue
            if facility_of_character(character) != trip["dest_facility"]:
                dest_area = (ui.world_areas(app) or {}).get(trip["dest_area"])
                if move(app, npc_id, character, dest_area, trip["dest_area"],
                        trip["dest_facility"]):
                    write("reconcile: {} put back at {}/{}".format(
                        npc_id, trip["dest_area"], trip["dest_facility"]))
            origin_area = (ui.world_areas(app) or {}).get(trip["origin_area"])
            roster_drop(app, origin_area, trip["origin_area"], npc_id)
            if trip.get("enrolled"):
                dest_area = (ui.world_areas(app) or {}).get(trip["dest_area"])
                roster_add(app, dest_area, trip["dest_area"], npc_id)
        hired = hired_table(app)
        for npc_id in list(hired):
            row = hired[npc_id]
            if day is not None and isinstance(row, dict) \
                    and day < int(row.get("hired_day", 0)):
                hired.pop(npc_id, None)
                changed = True
                write("reconcile: {} was hired on day {} but the save is on day {}; forgotten"
                      .format(npc_id, row.get("hired_day"), day))
        rest = rest_table(app)
        for npc_id in list(rest):
            row = rest[npc_id]
            if day is not None and isinstance(row, dict) \
                    and day < int(row.get("home_day", 0)):
                # 帰る前のセーブ。休みも始まっていない。
                rest.pop(npc_id, None)
                changed = True
        if changed:
            worlds.save(key)
        settle(app, day, "reconcile")

    # ------------------------------------------------------------ 会話の文脈
    def travel_note(app, npc_id, trip):
        day = ui.game_day(app)
        origin_area = (ui.world_areas(app) or {}).get(trip["origin_area"])
        fields = {"name": trip.get("name") or ui.character_name(app, npc_id),
                  "origin": area_name(origin_area, trip["origin_area"]),
                  "where": where_text(app, trip["dest_area"], trip["dest_facility"]),
                  "days": travel.days_left(trip, day) if day is not None else ""}
        template = AWAY_CONTEXT if trip.get("kind") == travel.AWAY else LOCAL_CONTEXT
        return travel.format_context(template, **fields)

    def with_context(label, args, kwargs):
        """会話相手が旅の途中なら、`profile` に1文足した浅い複製を渡す（`403_` と同じ手）。"""
        npc = kwargs.get("character_instance")
        if npc is None and len(args) >= 4:
            npc = args[3]
        app = ui.find_app()
        if npc is None or app is None:
            return args, kwargs
        npc_id = id_of(npc)
        if not npc_id:
            current = getattr(app, "in_conversation", None)
            npc_id = str(current) if current is not None else ""
        _key, trips = ledger(app)
        trip = trips.get(npc_id)
        if not isinstance(trip, dict):
            return args, kwargs
        note = travel_note(app, npc_id, trip)
        base = getattr(npc, "profile", "")
        if not note or not isinstance(base, str):
            return args, kwargs
        clone = copy.copy(npc)
        clone.profile = (base.rstrip() + "\n\n" + note) if base.strip() else note
        write("context[{}]: {} +{} chars".format(label, trip.get("name"), len(note)))
        if "character_instance" in kwargs:
            new_kwargs = dict(kwargs)
            new_kwargs["character_instance"] = clone
            return args, new_kwargs
        new_args = list(args)
        new_args[3] = clone
        return tuple(new_args), kwargs

    def inject(orig, label, args, kwargs):
        try:
            args, kwargs = with_context(label, args, kwargs)
        except Exception:
            ctx.log_exc("npc travel: injection failed")
        return orig(*args, **kwargs)

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days, *args, **kwargs):
        """日が進んだ後に見回る。日数の進め方には触らない。"""
        result = orig(self, days, *args, **kwargs)
        try:
            count = int(days) if isinstance(days, (int, float)) and not isinstance(days, bool) else 0
            patrol(self, max(0, count), "days elapsed")
        except Exception:
            ctx.log_exc("npc travel: patrol after elapse_days failed")
        return result

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def move_phase(orig, self, *args, **kwargs):
        """プレイヤーが施設を移った後。待たせていた帰還があれば果たす。"""
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            patrol(app, 0, "player moved")
        except Exception:
            ctx.log_exc("npc travel: patrol after move failed")
        return result

    def make_load(target):
        label = target.rsplit(".", 1)[-1]

        @ctx.wrap(target, required=False, safe=True)
        def on_load(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            worlds.forget()
            store["reconcile"] = True
            write("{}: reconcile armed".format(label))
            return result

        return on_load

    for target in LOAD_TARGETS:
        make_load(target)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        """ロードの後、最初に画面が組まれたときに突き合わせる（名簿が埋まってから）。"""
        if store["reconcile"]:
            characters = getattr(getattr(self, "world", None), "characters", None)
            if isinstance(characters, dict) and len(characters) > 1:
                store["reconcile"] = False
                try:
                    with worlds.lock:
                        reconcile(self)
                except Exception:
                    ctx.log_exc("npc travel: reconcile failed")
        return orig(self, *args, **kwargs)

    def make_conversation(target, label):
        @ctx.wrap(target, required=False)
        def conversation(orig, *args, **kwargs):
            return inject(orig, label, args, kwargs)

        return conversation

    for target, label in CONVERSATION_TARGETS:
        make_conversation(target, label)

    ctx.log("npc travel: installed (chance {}%/month, stay {}, affinity >= {}, "
            "away {}..{} days, local {} days, rest {} days, state {})".format(
                DEPART_CHANCE_PERCENT, STAY_MIN, AFFINITY_MIN, AWAY_DAYS_MIN,
                AWAY_DAYS_MAX, LOCAL_DAYS, REST_DAYS, worlds.dir_path()))
