# -*- coding: utf-8 -*-
"""ゲームの採番台帳（`index`）を通して id を振る（GAME.md §2.23 / §2.28）。

ゲームは `area` / `node` / `facility` / `npc` / `item` / `quest` の6種類の
id を、セーブの `index` から連番で振る。**実在する id の最大値ではない。**
そして振るとき、同じ id が既に居ても構わず上書きする。

MOD が自前で id を決めると、台帳が追いつかないまま MOD の id が並び、
ゲームが次に何かを作ったとき同じ番号を踏む。実際に起きた
（街の店主 50〜57 の素データが MOD の登場人物に差し替わった。
VERIFICATION_LOG.md §2.77）。

再発を防ぐ規約は1つ:
**MOD は id を自分で決めない。`claim(app, kind)` で採る。**
`claim` は「台帳と実在の大きいほう」を採り、台帳をその次へ進める。
これ以外の採り方（`max + 1`、`0` からの空き探し）はこの事故を再現する。

    from instantale_modloader.ids import claim
    npc_id = claim(app, "npc", write=write)          # "62"
    item_key = claim(app, "item", write=write)       # "item_227"

台帳は `app.world_dict` と `app.save_data_dict` の両方にあり、
ゲームは両方を同じ値に進める（実セーブで一致。GAME.md §2.28）。
こちらも決め打ちせず、`index` を持つ辞書を全部集めて全部に書く。
"""

KINDS = ("area", "node", "facility", "npc", "item", "quest")

#: 鍵の書式。`item` だけが `item_<n>`（持ち物の鍵）で、ほかは数字そのまま。
PREFIX = {"item": "item_"}


def _int(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _numeric(key, kind):
    """鍵から番号を取り出す。書式が違えば None（`item` の店在庫の `'79'` など）。"""
    prefix = PREFIX.get(kind, "")
    key = str(key)
    if prefix:
        if not key.startswith(prefix):
            return None
        key = key[len(prefix):]
    return _int(key)


def format_id(kind, number):
    """番号を鍵の書式にする。"""
    return "{}{}".format(PREFIX.get(kind, ""), int(number))


def _root_dicts(app):
    """素データの根（`world_dict` / `save_data_dict` …）を全部。`[(どこ, dict)]`。"""
    out, seen = [], set()
    for label, holder in (("app", app), ("world", getattr(app, "world", None))):
        if holder is None:
            continue
        try:
            items = list(holder.items() if isinstance(holder, dict)
                         else vars(holder).items())
        except Exception:
            continue
        for name, value in items:
            if not isinstance(value, dict) or id(value) in seen:
                continue
            if any(isinstance(value.get(key), dict)
                   for key in ("index", "npcs", "areas")):
                seen.add(id(value))
                out.append(("{}.{}".format(label, name), value))
    return out


def stores(app):
    """採番台帳を持つ辞書を全部集める。`[(どこにあるか, index), ...]`。"""
    return [(where + "['index']", root["index"]) for where, root in _root_dicts(app)
            if isinstance(root.get("index"), dict)]


def counter(app, kind):
    """ゲーム自身が次に振る番号（`index[kind]` の最大）。台帳が無ければ 0。"""
    largest = 0
    for _where, index in stores(app):
        value = _int(index.get(kind, 0))
        if value is not None:
            largest = max(largest, value)
    return largest


def existing(app, kind):
    """実在する番号の集合。素データの根すべてと、実行時の名簿を見る。"""
    found = set()

    def add(keys):
        for key in list(keys or ()):
            number = _numeric(key, kind)
            if number is not None:
                found.add(number)

    for _where, root in _root_dicts(app):
        areas = root.get("areas") if isinstance(root.get("areas"), dict) else {}
        if kind == "area":
            add(areas)
        elif kind in ("node", "facility"):
            for area in areas.values():
                nodes = area.get("nodes") if isinstance(area, dict) else None
                if not isinstance(nodes, dict):
                    continue
                if kind == "node":
                    add(nodes)
                else:
                    for node in nodes.values():
                        if isinstance(node, dict):
                            add(node.get("facilities"))
        elif kind == "npc":
            add(root.get("npcs"))
        elif kind == "quest":
            add(root.get("quests"))
            add(root.get("story_quests"))
        elif kind == "item":
            player = root.get("player_data")
            if isinstance(player, dict):
                add(player.get("inventory"))
            npcs = root.get("npcs")
            if isinstance(npcs, dict):
                for npc in npcs.values():
                    if isinstance(npc, dict):
                        add(npc.get("inventory"))
    world = getattr(app, "world", None)
    characters = getattr(world, "characters", None)
    if kind == "npc" and isinstance(characters, dict):
        add(characters)
    if kind == "item":
        player = getattr(app, "player", None)
        add(getattr(player, "inventory", None))
        if isinstance(characters, dict):
            for character in characters.values():
                inventory = getattr(character, "inventory", None)
                if isinstance(inventory, dict):
                    add(inventory)
    return found


def next_id(app, kind, used=()):
    """まだ使われていない番号を鍵の書式で返す。台帳は進めない。

    台帳（`counter`）と、実在する番号+1 の大きいほう。
    `used` は呼び側が既に見つけている鍵（重ねて見るだけ。無くてよい）。
    """
    if kind not in KINDS:
        raise ValueError("unknown id kind: {!r} (one of {})".format(kind, KINDS))
    numbers = existing(app, kind)
    for key in used or ():
        number = _numeric(key, kind)
        if number is not None:
            numbers.add(number)
    largest = max(numbers) + 1 if numbers else 0
    return format_id(kind, max(largest, counter(app, kind)))


def advance(app, kind, key, write=None):
    """台帳の `kind` を `key + 1` まで進める（戻しはしない）。進めた場所を返す。"""
    number = _numeric(key, kind)
    if number is None:
        return []
    floor = number + 1
    moved = []
    for where, index in stores(app):
        current = _int(index.get(kind, 0)) or 0
        if current < floor:
            index[kind] = floor
            moved.append(where)
    if write and moved:
        write("ids: index[{!r}] -> {} via {}".format(kind, floor, moved))
    return moved


def claim(app, kind, used=(), write=None):
    """id を1つ採って台帳を進める。MOD が id を要るときはこれを通す。

    連続で呼べば連番になる（前の呼び出しが台帳を進めているので、
    素データに書く前でも次は別の番号になる）。
    """
    key = next_id(app, kind, used)
    advance(app, kind, key, write)
    return key


def audit(app, write=None):
    """台帳が実在する id に追いついていない種類を並べる。

    `[(kind, 台帳, 実在の最大), ...]`。空なら健全。
    ゲームは台帳から振るので、台帳 ≤ 実在の最大 なら次の生成で衝突する。
    """
    behind = []
    for kind in KINDS:
        numbers = existing(app, kind)
        if not numbers:
            continue
        largest = max(numbers)
        value = counter(app, kind)
        if value <= largest:
            behind.append((kind, value, largest))
    if write and behind:
        write("ids: index behind existing ids: {}".format(
            ", ".join("{} index={} max={}".format(*row) for row in behind)))
    return behind
