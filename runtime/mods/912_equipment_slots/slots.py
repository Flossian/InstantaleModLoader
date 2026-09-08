# -*- coding: utf-8 -*-
"""装備欄の決まりごと。画面にもゲームにも触らない。

装備欄は本体の `InventoryGrid`（6列×8行）で、部位はその上の矩形（`REGIONS`）。
品の位置（マスの座標）から部位を引き、本体に渡す1つを選ぶところまでをここに置き、
ゲーム抜きで確かめられるようにしてある（`tools\\tests\\test_wip_equipment_slots.py`）。
"""

COLS, ROWS = 6, 10

#: 部位 -> (x, y, 幅, 高さ)。y は上から。手と胴は縦4マス（武器は最大 1×4）。
#: 並びは拾うとき・同点のときの優先（右手が先）。
#: 部位の外のマスは見せず、ドロップも受けない（`UNUSED_CELLS`）。
#:
#:             ［ 頭 ］
#:     ［左手］［ 胴 ］［右手］
#:     ［    ］［    ］［    ］
#:     ［ 腕 ］［ 脚 ］［装飾］
#:                     ［装飾］
REGIONS = {
    "head": (2, 0, 2, 2),
    "right_hand": (4, 2, 2, 4),
    "left_hand": (0, 2, 2, 4),
    "body": (2, 2, 2, 4),
    "arms": (0, 6, 2, 2),
    "legs": (2, 6, 2, 2),
    "accessory1": (4, 6, 2, 2),
    "accessory2": (4, 8, 2, 2),
}


def _unused_cells():
    used = set()
    for rx, ry, rw, rh in REGIONS.values():
        used.update((x, y) for x in range(rx, rx + rw) for y in range(ry, ry + rh))
    return tuple((x, y) for y in range(ROWS) for x in range(COLS) if (x, y) not in used)


#: 部位の外のマス。
UNUSED_CELLS = _unused_cells()

LABELS = {
    "head": "頭", "left_hand": "左手", "right_hand": "右手", "body": "胴",
    "arms": "腕", "legs": "脚", "accessory1": "装飾", "accessory2": "装飾",
}

#: 部位 -> 入る種類。
KIND_OF = {
    "head": "head", "body": "body", "arms": "arms", "legs": "legs",
    "left_hand": "hand", "right_hand": "hand",
    "accessory1": "accessory", "accessory2": "accessory",
}

#: `attributes['item_detail']` → 種類。武器は種類を問わず手。盾も手。
WEARABLE_KIND = {
    "shield": "hand",
    "headgear": "head",
    "clothing": "body",
    "body_armor": "body",
    "gauntlets": "arms",
    "legwear": "legs",
    "leg_armor": "legs",
    "accessory": "accessory",
}

#: 本体が読む鍵と、そのとき比べる能力値。
GAME_KEYS = (("weapon", "攻撃力"), ("wearable", "防御力"))


def detail_of(item):
    attrs = getattr(item, "attributes", None)
    if isinstance(attrs, dict):
        value = attrs.get("item_detail")
        if isinstance(value, str):
            return value
    return ""


def kind_of(item):
    """この品が入る種類。装備できない品は None。"""
    item_type = getattr(item, "item_type", None)
    if item_type == "weapon":
        return "hand"
    if item_type == "wearable":
        # ponytail: 知らない item_detail は装飾扱い。種類が増えたら表へ足す
        return WEARABLE_KIND.get(detail_of(item), "accessory")
    return None


def stat_of(item, name):
    attrs = getattr(item, "attributes", None)
    if not isinstance(attrs, dict):
        return 0.0
    try:
        return float(attrs.get(name, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def size_of(item):
    try:
        return (max(1, int(getattr(item, "width_slots", 1) or 1)),
                max(1, int(getattr(item, "height_slots", 1) or 1)))
    except (TypeError, ValueError):
        return (1, 1)


def cell_of_index(index):
    """`current_slots` の添字 → (x, y)。添字は y * COLS + x。"""
    return index % COLS, index // COLS


def top_left(current_slots):
    """占有マスの添字の並びから左上のマス。空なら None。"""
    try:
        indices = [int(i) for i in current_slots]
    except (TypeError, ValueError):
        return None
    if not indices:
        return None
    return cell_of_index(min(indices))


def region_at(x, y):
    """そのマスを含む部位。部位の外なら None。"""
    for name, (rx, ry, rw, rh) in REGIONS.items():
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return name
    return None


def fits(item, x, y):
    """左上を (x, y) に置いたとき、品がその部位に収まるか。収まる部位か None。"""
    name = region_at(x, y)
    if name is None:
        return None
    rx, ry, rw, rh = REGIONS[name]
    w, h = size_of(item)
    if x + w > rx + rw or y + h > ry + rh:
        return None
    return name


def accepts(item, x, y, occupied):
    """ドロップを受けるか。`occupied` は {部位: 別の品の id}。理由の文字列か、通れば None。"""
    kind = kind_of(item)
    if kind is None:
        return "not equipment"
    name = fits(item, x, y)
    if name is None:
        return "outside a region"
    if KIND_OF[name] != kind:
        return "wrong region {} for {}".format(name, kind)
    if occupied.get(name):
        return "region {} occupied".format(name)
    return None


def slots_from_positions(positions, items):
    """{id: (x, y)} → {部位: id}。部位の外の品は無視する。"""
    slots = {}
    for item_id, pos in positions.items():
        item = items.get(item_id)
        if item is None or not isinstance(pos, (list, tuple)) or len(pos) < 2:
            continue
        name = fits(item, int(pos[0]), int(pos[1]))
        if name is not None and name not in slots:
            slots[name] = item_id
    return slots


def fit_scale(item, name):
    """部位いっぱいに広げる倍率（縦横比は保つ）。1×1 を 2×2 の部位に置けば 2、1×4 の剣は 1。"""
    _rx, _ry, rw, rh = REGIONS[name]
    w, h = size_of(item)
    return max(1, min(rw // w, rh // h))


def best(slots, items, game_key):
    """本体の `equipments[game_key]` に渡す1つ。合算せず最高値のみ。

    `weapon` は手にある武器の中で攻撃力が最高のもの。
    `wearable` は全部位の防具（盾を含む）の中で防御力が最高のもの。
    候補が無ければ None。同点は REGIONS の並びで先のもの。
    """
    stat = dict(GAME_KEYS)[game_key]
    chosen, top = None, None
    for name in REGIONS:
        item = items.get(slots.get(name))
        if item is None or getattr(item, "item_type", None) != game_key:
            continue
        value = stat_of(item, stat)
        if top is None or value > top:
            chosen, top = item, value
    return chosen
