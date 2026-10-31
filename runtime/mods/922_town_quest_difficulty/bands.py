# -*- coding: utf-8 -*-
"""街ごとの依頼の難易度の範囲と、なだらかにする計算。素の Python（ゲームにもローダにも依存しない）。

本体（`town_quest_difficulty.py`）と設定画面（`tool.py`）が同じ表と式を使うため、ここに1つだけ置く。
範囲はどれも両端を含む `(下限, 上限)`。

ゲームの範囲（GAME.md §2.9）:

    初訪問の3件   random.sample(range(lo, hi), 3)      save_area_json.py:329
    探した1件     random.randint(lo, hi - 1)          instantale.py:5957（DisplayQuestChoice.generate_random_quest）

`lo` / `hi` は街の id で決まる。id 9 以降は `range(1, 77)`（全域）。
"""

#: ゲームが依頼の難易度に使う下限と上限（`QUEST_DIFFICULTY_VALUE_MIN` / `_MAX`）。
GAME_MIN = 1
GAME_MAX = 76

#: 設定で上げられる上限の天井（`318_area_difficulty_growth` の `DIFFICULTY_LIMIT` と同じ）。
LIMIT_CEILING = 100

#: 初訪問の3件は重なりなしで引くので、範囲には少なくとも3つの値が要る。
MIN_WIDTH = 3

#: 街の id ごとのゲームの範囲（両端を含む）。VERIFICATION_LOG.md §2.84 の `range(lo, hi)` の `hi` を1つ下げた形。
SLOT_BANDS = {
    "0": (1, 5),
    "1": (3, 10),
    "4": (11, 18),
    "2": (19, 27),
    "5": (28, 36),
    "7": (37, 46),
    "3": (47, 56),
    "6": (57, 66),
    "8": (67, 76),
}

#: なだらかにするときの既定。開始の街は素の id 0、最も遠い街は素の id 8、幅は素の範囲の幅に近い 9。
DEFAULT_START = (1, 5)
DEFAULT_END = (67, 76)
DEFAULT_WIDTH = 9

#: 街ではない土地の `size`（依頼の土地・ダンジョン）。
NOT_SETTLEMENT = (None, "", "dungeon")


def game_band(area_id):
    """その街のゲームの範囲。id 9 以降は全域。"""
    return SLOT_BANDS.get(str(area_id), (GAME_MIN, GAME_MAX))


def clamp_limit(limit):
    """設定の上限を 1〜`LIMIT_CEILING` に収める。読めなければ `GAME_MAX`。"""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return GAME_MAX
    return max(MIN_WIDTH, min(LIMIT_CEILING, value))


def normalize(low, high, limit=GAME_MAX):
    """範囲を 1〜上限に収め、逆さまを並べ直し、幅を `MIN_WIDTH` 以上にする。読めなければ None。"""
    try:
        low, high = int(low), int(high)
    except (TypeError, ValueError):
        return None
    top = clamp_limit(limit)
    if low > high:
        low, high = high, low
    low = max(GAME_MIN, min(top, low))
    high = max(GAME_MIN, min(top, high))
    while high - low + 1 < MIN_WIDTH:
        if high < top:
            high += 1
        elif low > GAME_MIN:
            low -= 1
        else:
            break
    return low, high


def band_around(center, width, limit=GAME_MAX):
    """中心と幅から範囲。上下の端に当たったら、幅を保ったまま内側へずらす。"""
    top = clamp_limit(limit)
    width = max(MIN_WIDTH, min(top - GAME_MIN + 1, int(width)))
    low = int(round(center - (width - 1) / 2.0))
    low = max(GAME_MIN, min(top - width + 1, low))
    return low, low + width - 1


def center_of(band):
    return (band[0] + band[1]) / 2.0


def is_settlement(area):
    return isinstance(area, dict) and area.get("size") not in NOT_SETTLEMENT


def towns_of(areas):
    """世界の `areas`（id -> 辞書）から街だけを拾う。

    `{id: {"name", "size", "connections"}}`。つながりは街どうしだけを残し、片側にしか書かれていない道も両側へ足す。
    """
    towns = {}
    for area_id, area in (areas or {}).items():
        if is_settlement(area):
            towns[str(area_id)] = {"name": area.get("name") or "",
                                   "size": area.get("size") or "",
                                   "connections": []}
    for area_id, area in (areas or {}).items():
        if str(area_id) not in towns:
            continue
        for other in area.get("connections") or []:
            other = str(other)
            if other in towns and other != str(area_id):
                for a, b in ((str(area_id), other), (other, str(area_id))):
                    if b not in towns[a]["connections"]:
                        towns[a]["connections"].append(b)
    for town in towns.values():
        town["connections"].sort(key=id_sort_key)
    return towns


def id_sort_key(value):
    text = str(value)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def start_of(towns):
    """開始の街。id 0 があればそれ、無ければいちばん小さい id。街が無ければ None。"""
    if not towns:
        return None
    return "0" if "0" in towns else sorted(towns, key=id_sort_key)[0]


def depths(towns, start):
    """開始の街から道をいくつ通るか。たどり着けない街は入らない。"""
    if start not in towns:
        return {}
    seen = {start: 0}
    queue = [start]
    while queue:
        current = queue.pop(0)
        for other in towns[current]["connections"]:
            if other not in seen:
                seen[other] = seen[current] + 1
                queue.append(other)
    return seen


def smooth(towns, bands, pinned, start_band=DEFAULT_START, end_band=DEFAULT_END,
           width=DEFAULT_WIDTH, limit=GAME_MAX):
    """なだらかにした範囲 `{id: (下限, 上限)}` を返す。`bands` は今の範囲（無い街はゲームの範囲）。

    固定した街と、開始の街・最も遠い街を錨にし、残りの街の中心を「つながる街の中心の平均」に寄せる
    （調和補間。道の上で錨の間を均す）。
    開始の街と最も遠い街は、固定していなければ `start_band` / `end_band`。
    錨は固定した街だけにすると、行き止まりの街が隣と同じ高さに平らになる（1本道の先は平均の相手が1つしかない）ので、
    最も遠い街も錨に入れる。
    固定した街の範囲はそのまま返す。たどり着けない街は今の範囲のまま返す。
    """
    top = clamp_limit(limit)
    current = {}
    for area_id in towns:
        found = bands.get(area_id) if bands else None
        current[area_id] = normalize(*(found or game_band(area_id)), limit=top) \
            or game_band(area_id)
    start = start_of(towns)
    depth = depths(towns, start)
    if not depth:
        return dict(current)
    far = max(depth.values())
    pinned = set(str(p) for p in (pinned or ()) if str(p) in towns)
    start_band = normalize(*start_band, limit=top) or DEFAULT_START
    end_band = normalize(*end_band, limit=top) or DEFAULT_END

    anchors = {}
    for area_id in depth:
        if area_id in pinned:
            anchors[area_id] = center_of(current[area_id])
        elif area_id == start:
            anchors[area_id] = center_of(start_band)
        elif far > 0 and depth[area_id] == far:
            anchors[area_id] = center_of(end_band)

    # 初期値は道の段の数で線形に割った値（収束を早めるだけで、結果は変わらない）。
    low_c, high_c = center_of(start_band), center_of(end_band)
    centers = {}
    for area_id, d in depth.items():
        centers[area_id] = anchors.get(area_id, low_c + (high_c - low_c) * d / float(far or 1))
    free = [a for a in sorted(depth, key=id_sort_key) if a not in anchors]
    for _round in range(10000):
        moved = 0.0
        for area_id in free:
            neighbors = [n for n in towns[area_id]["connections"] if n in centers]
            if not neighbors:
                continue
            value = sum(centers[n] for n in neighbors) / float(len(neighbors))
            moved = max(moved, abs(value - centers[area_id]))
            centers[area_id] = value
        if moved < 1e-6:
            break

    out = dict(current)
    for area_id in depth:
        if area_id in pinned:
            continue
        if area_id == start:
            out[area_id] = start_band
        elif far > 0 and depth[area_id] == far:
            out[area_id] = end_band
        else:
            out[area_id] = band_around(centers[area_id], width, top)
    return out


def changed_from_game(area_id, band):
    """決めた範囲がゲームの範囲と違うか。"""
    return tuple(band) != tuple(game_band(area_id))
