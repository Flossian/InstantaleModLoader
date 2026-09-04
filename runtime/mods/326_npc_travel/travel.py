# -*- coding: utf-8 -*-
"""`326_npc_travel` の、ゲームに触らない部分。

誰を出すか・どこへ・何日か、と台帳の形。
`npc_travel.py`（フック）から呼ぶ。
ゲームのオブジェクトを受けないので `tools/tests/test_npc_travel.py` が
そのまま通せる。
"""

#: 旅の種類。`away` は別の街、`local` は同じ街の別の施設。
AWAY = "away"
LOCAL = "local"

#: 別の街で置ける施設の種別（`323_` と同じ。ギルドか宿）。
AWAY_TYPES = ("guild", "inn")

#: 主の居ない通路。同じ街の行き先にしない（GAME.md §2.7）。
#: `free` はシーン記述エンジンが画面を持つ施設（GAME.md §2.21）で、
#: 置いても会えるか分からないので同じく外す（実機 2026-09-04 で 4 件が引かれて気付いた）。
PASSAGE_TYPES = ("entrance", "exit", "ward", "location", "dungeon_location", "free")

#: 街とみなす `size`（`325_` の TOWN_SIZES と同じ語彙）。
TOWN_SIZES = ("village", "town", "city")

#: 台帳の版。形を変えたら上げる。
LEDGER_VERSION = 1


# ---- 台帳 ------------------------------------------------------------------

def new_bucket():
    """1世界ぶんの台帳の初期形。`WorldStore(default=new_bucket)`。

    `trips` は旅の途中の人、`hired` は旅先で雇われて解散待ちの人、
    `rest` は帰ってきて休んでいる人（次に出られる日）。
    """
    return {"version": LEDGER_VERSION, "trips": {}, "hired": {}, "rest": {}}


def table_of(bucket, name) -> dict:
    """台帳の表 `{npc_id: 行}`。無ければ作る。"""
    if not isinstance(bucket, dict):
        return {}
    table = bucket.get(name)
    if not isinstance(table, dict):
        table = {}
        bucket[name] = table
    return table


def trips_of(bucket) -> dict:
    return table_of(bucket, "trips")


def hired_of(bucket) -> dict:
    return table_of(bucket, "hired")


def rest_of(bucket) -> dict:
    return table_of(bucket, "rest")


def _ordered(table, fields):
    ordered = {}
    for npc_id in sorted(table, key=lambda key: (len(str(key)), str(key))):
        row = table[npc_id]
        if isinstance(row, dict):
            ordered[npc_id] = {key: row[key] for key in fields if key in row}
            for key in row:
                if key not in fields:
                    ordered[npc_id][key] = row[key]
        else:
            ordered[npc_id] = row
    return ordered


def order_bucket(bucket):
    """書く直前に並びを固定する（`state/` の差分を読めるように）。"""
    return {"version": LEDGER_VERSION,
            "trips": _ordered(trips_of(bucket), TRIP_FIELDS),
            "hired": _ordered(hired_of(bucket), HIRED_FIELDS),
            "rest": _ordered(rest_of(bucket), REST_FIELDS)}


#: 旅の1行。並びは書くときの並び。
TRIP_FIELDS = ("name", "kind", "origin_area", "origin_facility",
               "dest_area", "dest_facility", "depart_day", "return_day",
               "enrolled")


#: 旅先で雇われた人の1行。解散したら、実際に置かれた街の名簿へ入れる。
HIRED_FIELDS = ("name", "origin_area", "dest_area", "hired_day")


#: 休んでいる人の1行。`until_day` 未満の日は出発の候補にしない。
REST_FIELDS = ("name", "home_day", "until_day")


def make_rest(name, day, rest_days):
    return {"name": name, "home_day": int(day), "until_day": int(day) + max(0, int(rest_days))}


def is_resting(rest, npc_id, day) -> bool:
    """休み中か。日が読めなければ休み中とみなさない。"""
    row = rest.get(str(npc_id))
    if not isinstance(row, dict) or day is None:
        return False
    try:
        return int(day) < int(row.get("until_day", 0))
    except (TypeError, ValueError):
        return False


def prune_rest(rest, day) -> bool:
    """休みの明けた行を落とす。落としたら True。"""
    if day is None:
        return False
    gone = [npc_id for npc_id in rest if not is_resting(rest, npc_id, day)]
    for npc_id in gone:
        rest.pop(npc_id, None)
    return bool(gone)


def make_hired(trip, day):
    return {"name": trip.get("name"), "origin_area": trip.get("origin_area"),
            "dest_area": trip.get("dest_area"), "hired_day": int(day or 0)}


def make_trip(name, kind, origin_area, origin_facility, dest_area,
              dest_facility, depart_day, days, enrolled):
    return {"name": name, "kind": kind,
            "origin_area": str(origin_area),
            "origin_facility": str(origin_facility),
            "dest_area": str(dest_area),
            "dest_facility": str(dest_facility),
            "depart_day": int(depart_day),
            "return_day": int(depart_day) + int(days),
            "enrolled": bool(enrolled)}


def days_left(trip, day) -> int:
    try:
        return max(0, int(trip.get("return_day", 0)) - int(day))
    except (TypeError, ValueError):
        return 0


def is_due(trip, day) -> bool:
    try:
        return int(day) >= int(trip.get("return_day", 0))
    except (TypeError, ValueError):
        return False


def occupancy(trips, area_id, facility_id) -> int:
    """その施設に旅で来ている人数（受け入れ上限の判定用）。"""
    area_id, facility_id = str(area_id), str(facility_id)
    return sum(1 for row in trips.values()
               if isinstance(row, dict)
               and row.get("dest_area") == area_id
               and row.get("dest_facility") == facility_id)


# ---- 判定 --------------------------------------------------------------------

def is_town(size) -> bool:
    return isinstance(size, str) and size.strip().lower() in TOWN_SIZES


def chance_over_days(monthly_percent, days) -> float:
    """月あたりの確率（%）を、`days` 日のうちに1度でも起きる確率へ直す。

    1日あたり `p/30` として `1 - (1 - p/30)^days`。
    30日で設定値そのものより少し下（30% なら 26%）になるが、
    日数に対して単調で、90日を一度に飛ばしても 100% には張り付かない。
    """
    try:
        per_day = max(0.0, min(100.0, float(monthly_percent))) / 100.0 / 30.0
        days = max(0, int(days))
    except (TypeError, ValueError):
        return 0.0
    if per_day <= 0.0 or days <= 0:
        return 0.0
    return 1.0 - (1.0 - per_day) ** days


def pick_departures(rng, candidates, present, stay_min, chance):
    """出発する人を決める。

    `candidates` … 条件（友好度・生存・非同行・旅に出ていない）を満たした id の並び
    `present`    … いまその街のギルドに居る（＝会話の一覧に出る）人数
    `stay_min`   … 残す人数。`present - 出発 >= stay_min` を必ず守る
    `chance`     … 1人あたりの出発確率（0..1）

    候補を1人ずつ振り、当たった順に枠が尽きるまで出す。
    枠が無ければ誰も振らない（乱数の消費も無い）。
    """
    room = int(present) - int(stay_min)
    if room <= 0 or not candidates or chance <= 0.0:
        return []
    order = list(candidates)
    rng.shuffle(order)
    chosen = []
    for npc_id in order:
        if len(chosen) >= room:
            break
        if rng.random() < chance:
            chosen.append(npc_id)
    return chosen


def pick_duration(rng, lo, hi) -> int:
    """滞在日数。`lo..hi` の一様。逆なら入れ替え、1日未満にはしない。"""
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return 1
    if lo > hi:
        lo, hi = hi, lo
    lo = max(1, lo)
    hi = max(lo, hi)
    return rng.randint(lo, hi)


def pick_kind(rng, local_percent, has_away, has_local):
    """同じ街か別の街か。候補の無いほうへは行かない。どちらも無ければ None。"""
    if not has_away and not has_local:
        return None
    if not has_away:
        return LOCAL
    if not has_local:
        return AWAY
    try:
        local = max(0.0, min(100.0, float(local_percent))) / 100.0
    except (TypeError, ValueError):
        local = 0.5
    return LOCAL if rng.random() < local else AWAY


def pick_spot(rng, spots):
    """`[(エリアid, 施設id, 種別)]` から1つ。土地 → 種別 → 施設の順に引く。

    施設から直に引かないのは、宿を2つ持つ土地でそこだけ宿が2倍出るため
    （`323_` と同じ）。
    """
    if not spots:
        return None
    areas = sorted({spot[0] for spot in spots})
    area_id = rng.choice(areas)
    here = [spot for spot in spots if spot[0] == area_id]
    kinds = sorted({spot[2] for spot in here})
    kind = rng.choice(kinds)
    return rng.choice(sorted(spot for spot in here if spot[2] == kind))


def affinity_value(relationship):
    """`relationship["player"]["affinity"]` を int で。読めなければ None。

    `relationship` は実行時の `Character.relationship` でもセーブの辞書でも同じ形
    （GAME.md §2.25）。
    """
    if not isinstance(relationship, dict):
        return None
    player = relationship.get("player")
    if not isinstance(player, dict):
        return None
    value = player.get("affinity")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def format_context(template, **fields) -> str:
    """設定の文型に値を入れる。鍵が足りなくても落とさない（空にする）。"""
    if not isinstance(template, str) or not template.strip():
        return ""

    class _Missing(dict):
        def __missing__(self, key):
            return ""

    try:
        return template.format_map(_Missing(fields)).strip()
    except (ValueError, IndexError):
        return ""
