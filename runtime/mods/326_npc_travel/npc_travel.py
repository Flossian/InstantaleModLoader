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
- **同じ街の中に居る間はギルドの一覧に残す**（`adventurer_npcs` から外さない）。
  施設を探し回らずにギルドから話せる。ゲームが居場所で一覧を絞る作りだった場合に備えて、
  一覧が開かれたときに漏れている者のボタンを足す（`320_` と同じ差し込み方）
- 別の街へ出た者は元の街の一覧から外す。着いた先がギルドならそこの一覧に載る＝雇える
- **同じ街の中の移動は、プレイヤーが居る街でだけ、その街に着いた時に引く**
  （日が進んだときにも引く）。前に引いてからの日数ぶんをまとめて引くので、
  宿に泊まっても、旅から戻っても、着いた街が動いている。
  街の間は徒歩でも馬車でも 14 日かかる（`314_` の既定）ので、
  他の街の 7 日の移動は着く前に必ず終わっていて一度も見えない
  （実プレイ 2026-09-04 の 30 件中 16 件がこれだった）。
  別の街への旅は全部の街で、日が進んだときに引く ― 次にその街へ着いたときの
  「1人欠けている」「知らない冒険者が来ている」を作っているのがそれなので。
  月あたりの出発確率は「同じ街へ向かう割合」で2つに割り当て、
  それぞれの契機で引く
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
  元の街では何も出さない（決めた仕様）。
  まだ細部の生成されていない個体は `profile` が `None` のことがあるので空欄として扱う
  （生成そのものはゲームが会話の直前に行う。`ensure_npc_detail_generated`。
  こちらは触らない）

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
LOCAL_LIST_SUFFIX = "（{where}）"   # 同じ街に出ている者の、一覧での居場所の添え字
LOCAL_ONLY_HERE = True       # 同じ街の中の移動はプレイヤーが居る街でだけ起こす
AWAY_CONTEXT = "{name}は{origin}から旅をして来て、いまは{where}に滞在している（あと{days}日ほど）。"
LOCAL_CONTEXT = "{name}はいつものギルドを離れ、いまは{where}に来ている（あと{days}日ほど）。"

# ---- 設定にしない定数 ----------------------------------------------------
LOG_BASENAME = "npc_travel.log"
STATE_DIRNAME = "npc_travel"          # state\ 直下のフォルダ。MOD専用の名前
STORE_ATTR = "_instantale_npc_travel"  # 世代をまたぐ入れ物（sys の属性）
GUILD_TYPE = "guild"
MARK = "mod_travel_list"              # 自前ボタンの印（MODごとに別の文字列）
LOCAL_CATCHUP_MAX = 30                # 街中の移動をまとめて引く日数の上限
CONVERSATION_SPEC = "ConversationStartManager"

#: 冒険者の一覧ではありえない spec。1つでも見えたら別の画面（`320_` と同じ）。
OTHER_SCREEN_SPECS = ("ConversationEndManager", "MovePhaseManager",
                      "DisplayTalkChoice", "DisplayQuestChoice",
                      "QuestChoiceManager")

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

    def seen_table(app):
        _key, bucket = worlds.of(app)
        return travel.seen_of(bucket)

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

        一覧に出る＝名簿に在り、生きていて、同行していない。
        `Area.adventurer_npcs` は刈り込まれない（死亡も同行中も残る。`320_`）ので
        自分で引き直す。
        同じ街の中に出ている人はギルドから話せるので**居る人として数える**。
        休み中の人も一覧には出る。どちらも出発の候補にはしない。
        """
        rosters = roster_lists(app, area, area_id)
        roster = rosters[0] if rosters else []
        party = {str(member) for member in ui.party_member_ids(app)}
        hired = hired_table(app)
        rest = rest_table(app)
        present, eligible = [], []
        for raw in roster:
            npc_id = str(raw)
            if npc_id in party or npc_id in hired:
                continue
            trip = trips.get(npc_id)
            if isinstance(trip, dict) and trip.get("kind") != travel.LOCAL:
                continue
            character = ui.character_of(app, npc_id)
            if character is None or is_dead(character):
                continue
            if npc_id in present:
                continue
            present.append(npc_id)
            if npc_id in trips:
                continue                # 同じ街に出ている最中。数えるが、また出さない
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
        """出発させる。`"gone"` / `"waits"`（延期）/ `""`（出せない）を返す。"""
        character = ui.character_of(app, npc_id)
        if character is None:
            return ""
        origin_facility = facility_of_character(character)
        if not origin_facility:
            guild, _node = ui.find_guild(area)
            origin_facility = id_of(guild)
        if not origin_facility:
            write("depart: {} has no facility to come back to; skipped".format(npc_id))
            return ""
        stands_in = area_of_facility(app, origin_facility, area_id)
        if stands_in != str(area_id):
            # 名簿は area_id だが実体は別の街（`303_` が置いた人）。帰り先を決められない。
            write("depart: {} is listed in area {} but stands in area {!r}; skipped"
                  .format(npc_id, area_id, stands_in))
            return ""
        if player_at(app, area_id, origin_facility):
            write("depart: player is at {}/{}; {} waits".format(
                area_id, origin_facility, npc_id))
            return "waits"
        dest_area_id, dest_facility, dest_kind = spot
        if player_at(app, dest_area_id, dest_facility):
            # 目の前に湧かせない（出発を止めるのと同じ扱いで、次の機会に回す）。
            write("depart: player is at the destination {}/{}; {} waits".format(
                dest_area_id, dest_facility, npc_id))
            return "waits"
        dest_area = (ui.world_areas(app) or {}).get(dest_area_id)
        if not move(app, npc_id, character, dest_area, dest_area_id, dest_facility):
            return ""
        days = (travel.pick_duration(rng, AWAY_DAYS_MIN, AWAY_DAYS_MAX)
                if kind == travel.AWAY else max(1, int(LOCAL_DAYS)))
        enrolled = kind == travel.AWAY and dest_kind == GUILD_TYPE
        if kind == travel.AWAY:
            # 同じ街の中の移動では外さない（ギルドから話せるように残す）。
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
        return "gone"

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

    def local_areas(app, towns):
        """街中の移動を引く街。既定はプレイヤーが居る街だけ（`LOCAL_ONLY_HERE`）。"""
        if not LOCAL_ONLY_HERE:
            return sorted(towns, key=ui.id_sort_key)
        here = id_of(getattr(getattr(app, "player", None), "current_area", None))
        return [here] if here in towns else []

    def send_off(app, day, days, kind):
        """出発を引く。`kind` ごとに契機も窓も違う（変更があれば台帳を書く）。

        `away` … 全部の街。窓はいま進んだ日数
        `local` … `local_areas` の街。窓はその街で前に引いてからの日数
                  （`seen`。上限 `LOCAL_CATCHUP_MAX`）＝**着いたときに引く**
        """
        key, trips = ledger(app)
        towns = known_towns(app)
        seen = seen_table(app)
        area_ids = (sorted(towns, key=ui.id_sort_key) if kind == travel.AWAY
                    else local_areas(app, towns))
        changed = False
        deferred = False
        for area_id in area_ids:
            area = towns.get(area_id)
            if area is None:
                continue
            window = (days if kind == travel.AWAY
                      else travel.catchup_days(seen, area_id, day, LOCAL_CATCHUP_MAX))
            if window <= 0:
                continue
            chance = travel.kind_chance(DEPART_CHANCE_PERCENT, LOCAL_CHANCE_PERCENT,
                                        kind, window)
            if chance <= 0.0:
                if kind == travel.LOCAL:
                    seen[str(area_id)] = int(day)
                    changed = True
                continue
            present, eligible = guild_members(app, area, area_id, trips, day)
            chosen = travel.pick_departures(rng, eligible, len(present), STAY_MIN, chance)
            if not chosen:
                if kind == travel.LOCAL:
                    seen[str(area_id)] = int(day)
                    changed = True
                continue
            write("roll[{}]: area {} present={} eligible={} chosen={} "
                  "(chance {:.3f} over {} day(s))".format(
                      kind, area_id, len(present), len(eligible), chosen, chance, window))
            for npc_id in chosen:
                character = ui.character_of(app, npc_id)
                here = facility_of_character(character) if character is not None else ""
                if kind == travel.AWAY:
                    spots = away_spots(app, trips, towns, area_id)
                    if not spots and area_id in local_areas(app, towns):
                        # 行ける街が1つも無い世界。せめて街の中で動く。
                        spots = local_spots(app, trips, area, area_id, here)
                else:
                    spots = local_spots(app, trips, area, area_id, here)
                spot = travel.pick_spot(rng, spots)
                if spot is None:
                    write("roll: nowhere for {} to go from area {}".format(npc_id, area_id))
                    continue
                going = travel.AWAY if spot[0] != str(area_id) else travel.LOCAL
                result = depart(app, key, trips, day, npc_id, area, area_id,
                                going, spot)
                if result == "gone":
                    changed = True
                elif result == "waits":
                    deferred = True
        if kind == travel.LOCAL and not deferred:
            # 引いた窓は消費する（当たり外れに関わらず、同じ日数を二度引かない）。
            # 延期した人が居る間は消費しない ― プレイヤーがその場を離れたら
            # 同じ窓でもう一度引けるように。
            seen[str(area_id)] = int(day)
            changed = True
        if changed:
            worlds.save(key)
        return changed

    def patrol(app, days, reason):
        """帰還の後始末と出発の抽選。

        日が進んだとき（`days > 0`）は別の街への旅を全部の街で引く。
        街中の移動は**居る街に着くたび**に、前に引いてからの日数ぶんを引く
        （移動でも宿泊でも、着いた街で起こる）。
        """
        if app is None or getattr(app, "world", None) is None:
            return
        day = ui.game_day(app)
        with worlds.lock:
            settle(app, day, reason)
            if day is None:
                return
            if days > 0:
                send_off(app, day, days, travel.AWAY)
            send_off(app, day, 0, travel.LOCAL)

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
            if trip.get("kind") == travel.LOCAL:
                roster_add(app, origin_area, trip["origin_area"], npc_id)
            else:
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
        if not note:
            return args, kwargs
        base = getattr(npc, "profile", "")
        if base is None:
            # まだ細部の生成されていない個体（`level_of_detail` が低い）。
            # 空欄と同じに扱う（`None` で切ると、初対面の旅人にだけ文脈が付かない）。
            base = ""
        if not isinstance(base, str):
            write("context[{}]: profile is {}; left alone".format(
                label, type(base).__name__))
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

    # ------------------------------------------------------------ ギルドの一覧
    # 同じ街の中に出ている人が一覧から漏れていたら足す。
    # 名簿には残してあるので、ゲームが名簿だけで組むならここは何もしない
    # （足す前に spec の引数で突き合わせるので二重にならない）。
    screen = ui.Screen(ctx, write, tag="npc travel", mark=MARK)
    listing = {"armed": False}

    def local_trips_here(app, area_id):
        """この街の中に出ている人 `{npc_id: 行}`。"""
        _key, trips = ledger(app)
        return {npc_id: trip for npc_id, trip in trips.items()
                if isinstance(trip, dict) and trip.get("kind") == travel.LOCAL
                and trip.get("origin_area") == str(area_id)}

    def listed_ids(buttons):
        """一覧に並んでいる会話相手の id。**spec の引数をそのまま読む**（ui.spec_args）。"""
        found = set()
        for entry in buttons:
            if ui.spec_cls_name(entry) != CONVERSATION_SPEC:
                continue
            args = ui.spec_args(entry)
            if args:
                found.add(str(args[0]))
        return found

    def back_button_index(buttons):
        """ゲーム側の「やめる」の位置。無ければ None（＝一覧ではない）。`320_` と同じ。"""
        for index, entry in enumerate(buttons):
            if (ui.spec_cls_name(entry) == ui.SAFE_CLS
                    and not screen.marked_by_a_mod(entry)):
                return index
        return None

    def list_label(app, npc_id, trip):
        name = ui.character_name(app, npc_id, fallback=trip.get("name") or npc_id)
        where = where_text(app, trip["dest_area"], trip["dest_facility"])
        suffix = travel.format_context(LOCAL_LIST_SUFFIX, where=where, name=name)
        return "{}{}".format(name, suffix) if suffix else name

    def fix_adventurer_list(app, buttons):
        """冒険者の一覧なら、同じ街に出ている人を並べる（居場所を添える）。"""
        if not listing["armed"]:
            return
        names = [ui.spec_cls_name(entry) for entry in buttons]
        if any(name in OTHER_SCREEN_SPECS for name in names):
            listing["armed"] = False
            return
        at = back_button_index(buttons)
        if at is None:
            return                      # 「やめる」が無い＝一覧が組み上がっていない
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        trips = local_trips_here(app, area_id)
        if not trips:
            return
        party = {str(member) for member in ui.party_member_ids(app)}
        listed = listed_ids(buttons)
        added = []
        for npc_id in sorted(trips, key=ui.id_sort_key):
            trip = trips[npc_id]
            character = ui.character_of(app, npc_id)
            if npc_id in party or (character is not None and is_dead(character)):
                continue
            label = list_label(app, npc_id, trip)
            if npc_id in listed:
                # ゲームが並べている。居場所だけ添える。
                for entry in buttons:
                    if (ui.spec_cls_name(entry) == CONVERSATION_SPEC
                            and (ui.spec_args(entry) or [None])[0] is not None
                            and str((ui.spec_args(entry) or [""])[0]) == npc_id
                            and isinstance(entry, dict)
                            and entry.get("text") != label):
                        entry["text"] = label
                continue
            entry = screen.button(label, mark="talk:" + npc_id,
                                  cls_name=CONVERSATION_SPEC, args=[npc_id])
            if entry is None:
                continue
            buttons.insert(at, entry)
            at += 1
            added.append(label)
        if added:
            write("list: added {} to the adventurer list of area {}".format(
                added, area_id))

    # ================================================================ フック
    @ctx.wrap("__main__:DisplayAdventurerTalkChoice.execute",
              required=False, safe=True)
    def adventurer_list_execute(orig, self, choice_text=None, *args, **kwargs):
        listing["armed"] = True
        return orig(self, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:DisplayTalkChoice.execute", required=False, safe=True)
    def talk_list_execute(orig, self, choice_text=None, *args, **kwargs):
        # 「会話する」の一覧は冒険者の一覧と同じ形なので、明示的に旗を下ろす（`320_`）。
        listing["armed"] = False
        return orig(self, choice_text, *args, **kwargs)

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
        """ロードの後の突き合わせと、冒険者の一覧の手当て。"""
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list):
                fix_adventurer_list(self, buttons)
        except Exception:
            ctx.log_exc("npc travel: cannot fix the adventurer list")
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
