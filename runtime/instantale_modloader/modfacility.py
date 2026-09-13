# -*- coding: utf-8 -*-
r"""MOD が持つ施設。街に建てて、MOD の側が丸ごと管理する。

`modnpc` の施設版で、考え方は同じ。
**ゲームが id で引く場所に現れない**施設を MOD が持ち、
控えはローダが `state\modfacility\<世界>.json` に持って、世界を読み直すたびに建て直す。
MOD を外せば街は素のまま（建物も、そこへ繋がる道も残らない）。

##### NPC と施設では、隠す側と肩代わりする側が逆になる

| | `modnpc` | ここ |
|---|---|---|
| 保存が舐める器 | `world.characters` と `npcs` の素データ | **舐めない**。保存は `save_data_dict['areas']` から書き、実行時の `node.facilities` を見ない |
| だから保存で | 名簿を `_RosterView` に差し替える | 実体を隠す必要が無い |
| ゲームが実行時の追加を見るか | 見る（会話の一覧に並ぶ） | **見ない**（移動の一覧にも、建物の中にも出ない） |
| だから MOD が出すもの | 入口だけ | 道・中の選択肢・出口・背景の全部 |

ゲームは移動の一覧を素データから組み直すので、実行時に足した `connections` を読まない
（`914_real_estate` が実機で確認。2026-09-11）。
繋ぎ先の画面にも、建物の中にも、その施設は並ばない。
**だから `hide` は軽く、肩代わりが重い。**

##### 実体が隠れていても、id は別の器に載る

ここは `modnpc` と同じ仕組みが要る。

| 器 | 起きること |
|---|---|
| `player_data["location"]` | 建て直されない施設の id が立ち位置に残ると、**その世界は二度と開けない**（ロードが引けなかった施設に `'facilityが見つからない'` という文字列を入れ、その `.name` を読む。`instantale.py:1493`） |
| `buttons` / `buttons_backup` | `MovePhaseManager(args=[ノード, 施設, エリア])` が焼かれ、MOD を外した環境で押せてしまう |
| `modnpc` の `.location` / `current_location` | ここに ModNPC を置くと両方が絡む（先に降ろす） |

自前のボタンの spec に `MovePhaseManager` を書かないのはそのため。
無害な既存クラス（`ui.SAFE_CLS`）を持たせ、押されたら印で横取りして本物を組む。

##### なぜ id を文字列にするのか

`ids.claim` で採ると採番台帳（`index['facility']`）が進み、セーブに足跡が残る。
かといって適当な整数を振ると、ゲームが次に施設を作ったときに同じ番号を踏む
（NPC で実際に起きた事故。GAME.md §2.23）。
ゲームの採番は整数の連番なので、文字列にすれば名前空間が分かれて両方を避けられる。
`node.facilities` は辞書なので、文字列の鍵でも載る。

> 施設 id は**土地の中でしか一意でない**（GAME.md §2.7）が、
> `mod:<持ち主>:<鍵>` は世界で一意にする。控えも関所もこの id 1本で引く。

##### 何を宣言できるか

| 登録するもの | 効く場所 |
|---|---|
| `fields` | 施設の素データ（`FACILITY_FIELDS` の8項目）の初期値。控えの写しが在ればそちらが勝つ |
| `choices` | 建物の中の選択肢。配列か、その時点で組む関数 |
| `on["enter"]` / `["leave"]` | 出入り |
| `on["background"]` | 背景を描く場面（描いたら True を返す。二重描きは関所が抑える） |
| `on["world"]` | セーブを読んだ直後。建て直すならここ |
| `on["save"]` | 保存の直前と直後 |
| `on["choices"]` | 組んだ選択肢に手を入れる最後の口 |

**出口は宣言しなくても出る。** ゲームが出さないので、無いと建物から出られない。

##### 未確認

実機では何も確かめていない（測るのは `230_probe_mod_facility`）。
文字列の施設 id が `MovePhaseManager` と背景の引きで通るか、
保存後のセーブにその土地の施設が増えていないかは、この時点ではどれも見込みでしかない。
整数で建てる形は `914_real_estate` が実機で通しているので、
落ちたときの逃げ場はそちら（`ids.claim`）。
"""

import copy
import sys

from . import log_exc, patch, state, ui

#: MOD の施設の id の接頭辞。ゲームの採番は整数の連番なので、ここが被ることはない。
PREFIX = "mod:"

#: 登録簿の置き場所。
#: `apply()` は1プロセスで何度も呼ばれる（TECH.md §3.5）ので、
#: モジュールのグローバルではなく `sys` に置いて注入をまたがせる。
REGISTRY_ATTR = "_instantale_modfacility_registry"

#: 関所を立てた世代の控え。同じ世代で二度立てない。
INSTALLED_ATTR = "_instantale_modfacility_installed"

#: 施設の素データの項目と並び。実セーブの `location` と同じ順
#: （順番が変わると、項目が揃っていてもセーブエディタの表示が崩れる。GAME.md §2.23）。
FACILITY_FIELDS = ("name", "id", "description", "facility_type", "tier", "owner",
                   "connections", "config")

#: 既定の種類。主のいない場所（`ward` / `location` / `entrance` / `exit` の仲間）で、
#: ゲーム側の出し分け（売買・訓練・宿泊）に一切引っ掛からない。
#: 別の種類（`inn` / `colosseum` …）を名乗らせても、ゲームは実行時の施設を見ないので
#: 中の選択肢は結局こちらが出す。種類は描写と、種類を読む MOD のためにある。
DEFAULT_FACILITY_TYPE = "location"

#: 建物を繋ぐ先に選ぶ施設の種類。この順に探す。
#: **エリアの入口を最優先**にする。街に着いて最初に立つのが入口で、
#: そこから各区画（`ward`）へ枝分かれしている。
HUB_TYPES = ("entrance", "ward")

#: 素データの `config` の既定。実セーブの `location` は `level_of_detail` だけを持つ。
DEFAULT_CONFIG = {"level_of_detail": 0}

#: 移動のボタンの spec のクラス名（GAME.md §2.2）。
MOVE_CLS = "MovePhaseManager"


# --------------------------------------------------------------------------
# id
# --------------------------------------------------------------------------
def make_id(owner, key):
    """`mod:<持ち主>:<鍵>`。"""
    return "{}{}:{}".format(PREFIX, owner, key)


def is_mod_facility(facility_id):
    """MOD の施設の id か。"""
    return str(facility_id).startswith(PREFIX)


def owner_of_id(facility_id):
    """id から持ち主を取る。MOD の施設でなければ空文字。"""
    text = str(facility_id)
    if not text.startswith(PREFIX):
        return ""
    rest = text[len(PREFIX):]
    return rest.split(":", 1)[0] if ":" in rest else rest


def facility_id_of(facility):
    """実体から id を取る。読めなければ空文字。"""
    return str(getattr(facility, "id", "")) if facility is not None else ""


def node_id_of(node):
    return str(getattr(node, "id", "")) if node is not None else ""


# --------------------------------------------------------------------------
# 登録簿
# --------------------------------------------------------------------------
def registry():
    """`{facility_id: 記録}`。書き換えてよいのはこのモジュールだけ。"""
    reg = getattr(sys, REGISTRY_ATTR, None)
    if not isinstance(reg, dict):
        reg = {}
        setattr(sys, REGISTRY_ATTR, reg)
    return reg


def _record(facility_id):
    """その id の記録。無ければ作る。"""
    reg = registry()
    record = reg.get(facility_id)
    if not isinstance(record, dict):
        record = reg[facility_id] = {
            "id": facility_id,
            "layers": [],       # 積んだ順。同じ持ち主は1つだけ
            "facility": None,   # 実体（`Facility`）
            "built_in": None,   # その実体を組んだ世代（注入し直しで組み直すため）
            "snapshot": None,   # 控えから読んだ素データの写し（次の spawn の材料）
            "placed": None,     # (area_id, node_id, hub_id)
        }
    return record


def layers(facility_id):
    """その id に積まれている層。積んだ順。"""
    record = registry().get(str(facility_id))
    return list(record["layers"]) if isinstance(record, dict) else []


def layers_for(facility_id):
    """その id に効く層。`priority` 順、同じなら積んだ順。"""
    ordered = [(layer.get("priority", 0), i, layer)
               for i, layer in enumerate(layers(str(facility_id)))]
    return [layer for _p, _i, layer in sorted(ordered, key=lambda t: t[:2])]


def has_layers(facility_id):
    """その id に効く層が1つでもあるか。"""
    return bool(layers(str(facility_id)))


def entries(owner=None):
    """登録されている id。`owner` を渡すとその MOD の分だけ。"""
    out = []
    for facility_id, record in registry().items():
        if owner is None or any(layer["owner"] == owner
                                for layer in record["layers"]):
            out.append(facility_id)
    return sorted(out)


def register(owner, facility_id=None, *, key=None, fields=None, choices=None,
             exit_label=None, keep_inside=None, priority=0, on=None, write=None):
    """層を1つ積む。id を返す。

    | 引数 | |
    |---|---|
    | `owner` | 登録する MOD の名乗り。フォルダ名を使う（ログに出る名前と揃う） |
    | `facility_id` | 省いて `key` を渡す。id は `mod:<owner>:<key>` になる |
    | `key` | MOD の中で建物を見分ける鍵 |
    | `fields` | 素データの初期値（`FACILITY_FIELDS` の項目名）。控えの写しが在ればそちらが勝つ |
    | `choices` | 建物の中の選択肢。`[{"key", "label", "on"}, ...]` か、`fn(info)` がそれを返す |
    | `exit_label` | 出口の文言。省くと `DEFAULT_EXIT_LABEL` |
    | `keep_inside` | 中に立ったまま保存してよいか。`True` か `fn(info) -> bool`。既定は入口へ移す |
    | `priority` | 選択肢を並べる順。小さいほど先 |
    | `on` | 場面ごとのフック（`fire` の site 名） |

    `keep_inside` を真にするのは、**その建物がロードで必ず建て直る**ときだけ。
    建て直らない建物の id が立ち位置に残ると、その世界は二度と開けない
    （`LIFT_LOCATION` の枠）。壊す予定が立っている間だけ偽を返す関数にすると、
    「中に居るあいだは壊さず、外に出てから壊す」という作りとも噛み合う。

    同じ `(owner, facility_id)` の層は差し替える。
    注入し直すたびに `apply()` が走る（TECH.md §3.5）ので、
    ここで重ねると世代のぶんだけ層が積み上がる。
    """
    if facility_id is None:
        if key is None:
            raise ValueError("register needs either facility_id or key")
        facility_id = make_id(owner, key)
    facility_id = str(facility_id)
    layer = {"owner": owner, "fields": dict(fields or {}),
             "choices": choices, "exit_label": exit_label,
             "keep_inside": keep_inside,
             "priority": int(priority), "on": dict(on or {})}
    record = _record(facility_id)
    record["layers"] = [old for old in record["layers"]
                        if old["owner"] != owner]
    record["layers"].append(layer)
    if record.get("facility") is not None \
            and record.get("built_in") != getattr(patch, "_generation", None):
        # 前の世代で組んだ実体が残っている＝注入し直された。使い回さず組み直す
        # （`modnpc` が実機で踏んだ。`build` を直しても古い実体が使われた）。
        # 見るのは**世代**で「登録し直したか」ではない ―
        # 建物の登録は塗り直しのたびに走ることがあり、そのたび捨てると
        # 街から引き直す手間が毎手ぶん増える。
        record["facility"] = None
    if write:
        write("modfacility: {} registered {} ({} layer(s))".format(
            owner, facility_id, len(record["layers"])))
    return facility_id


def unregister(owner, facility_id=None, app=None, write=None):
    """層を外す。外した id を返す。

    `facility_id` を省くとその MOD の層を全部外す。
    層が1つも残らなくなった id は、`app` を渡してあれば街から壊してから記録ごと捨てる。
    """
    dropped = []
    for target in ([str(facility_id)] if facility_id is not None else entries(owner)):
        record = registry().get(target)
        if not isinstance(record, dict):
            continue
        record["layers"] = [layer for layer in record["layers"]
                            if layer["owner"] != owner]
        dropped.append(target)
        if not record["layers"] and app is not None:
            try:
                despawn(app, target, write=write)
            except Exception:
                log_exc("modfacility: cannot take {} off the town".format(target))
        if app is not None:
            _drop_persisted(app, owner, target)   # `despawn` の後
        if record["layers"]:
            continue
        registry().pop(target, None)
    if write and dropped:
        write("modfacility: {} unregistered {}".format(owner, ", ".join(dropped)))
    return dropped


def purge(app=None, write=None):
    """登録簿を空にする。建っている建物も壊す。

    ローダの `unload` はパッチを剥がすだけで、世界に書いた値は戻さない
    （TECH.md §3.10）。片付けたい MOD はこれを自分で呼ぶ。
    """
    for facility_id in list(registry()):
        if app is not None:
            try:
                despawn(app, facility_id, write=write)
            except Exception:
                log_exc("modfacility: cannot take {} off the town".format(facility_id))
    registry().clear()
    if write:
        write("modfacility: the registry is empty")


def forget(write=None):
    """実体への参照だけ捨てる（世界が入れ替わったとき）。登録簿の層は残す。"""
    dropped = 0
    for record in registry().values():
        if record.get("facility") is not None or record.get("placed"):
            dropped += 1
        record["facility"] = None
        record["placed"] = None
    if write and dropped:
        write("modfacility: forgot {} building(s) of the previous world".format(dropped))
    return dropped


# --------------------------------------------------------------------------
# 素データ
# --------------------------------------------------------------------------
def _facility_class():
    """`__main__.Facility`。引けなければ None（呼ぶ側はそこで諦める）。"""
    return getattr(sys.modules.get("__main__"), "Facility", None)


def fields_of(facility_id):
    """層を積んだ順に重ねた素データの項目。"""
    merged = {}
    for layer in layers_for(facility_id):
        for name, value in (layer.get("fields") or {}).items():
            if name == "config" and isinstance(value, dict):
                config = dict(merged.get("config") or {})
                config.update(value)
                merged["config"] = config
            else:
                merged[name] = value
    return merged


def template(facility_id, fields=None, hub_id=None):
    """施設の素データ。実セーブの `location` と同じ8項目・同じ並び。

    並びは `dict` の挿入順がそのまま JSON の行順になるので、
    **揃えるだけでなくこの順でなければならない**（GAME.md §2.23）。
    ここで組んだ辞書はセーブへは書かないが、
    `Facility.__init__` が読む順序と、控えの読みやすさのために同じにしてある。
    """
    fields = dict(fields or {})
    config = dict(DEFAULT_CONFIG)
    config.update(fields.get("config") or {})
    connections = fields.get("connections")
    if not isinstance(connections, (list, tuple)):
        connections = [str(hub_id)] if hub_id else []
    data = {
        "name": fields.get("name") or "",
        "id": str(facility_id),
        "description": fields.get("description") or "",
        "facility_type": fields.get("facility_type") or DEFAULT_FACILITY_TYPE,
        "tier": fields.get("tier"),
        "owner": fields.get("owner"),
        "connections": [str(c) for c in connections],
        "config": config,
    }
    return data


def build(app, node, data, write=None):
    """素データから実体を1つ組む。組めなければ None。

    ノードの `facilities` にはまだ入れない（`spawn` が入れる）。
    """
    cls = _facility_class()
    if cls is None:
        if write:
            write("WARN modfacility: __main__.Facility is not available")
        return None
    try:
        return cls(app, node, data)
    except Exception:
        log_exc("modfacility: Facility(...) raised for {!r}".format(data.get("id")))
        if write:
            write("WARN modfacility: Facility(...) raised for {!r}".format(
                data.get("id")))
        return None


# --------------------------------------------------------------------------
# 繋ぐ
# --------------------------------------------------------------------------
def connections_of(facility):
    """施設の接続。読めなければ空の配列。"""
    value = getattr(facility, "connections", None)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def link(facility, other_id):
    """接続を1本足す。既に在れば何もしない。足したら True。

    実行時の `connections` が骨格側の list と同じものを指している可能性があるので、
    中身を書き換えず新しい list を作って属性ごと差し替える（`325_` / `321_` と同じ）。
    """
    if facility is None or not other_id:
        return False
    other_id = str(other_id)
    current = connections_of(facility)
    if other_id in current:
        return False
    try:
        facility.connections = current + [other_id]
    except Exception:
        return False
    return True


def unlink(facility, other_id):
    """接続を1本外す。外したら True。"""
    if facility is None or not other_id:
        return False
    other_id = str(other_id)
    current = connections_of(facility)
    if other_id not in current:
        return False
    try:
        facility.connections = [c for c in current if c != other_id]
    except Exception:
        return False
    return True


def hub_of(area):
    """建物を繋ぐ先。`(ノード, 施設)`。見つからなければ `(None, None)`。

    ノード自身が「ここが入口だ」と持っている（実データの `entrance_facility`）ので、
    まずそれを引く。種類で探すのはその後（入口を持たないノードのため）。
    """
    for node in ui.nodes_of(area):
        entrance_id = getattr(node, "entrance_facility", None)
        if entrance_id is None:
            continue
        facility = ui.facilities_of(node).get(str(entrance_id))
        if facility is not None and ui.facility_type_of(facility) in HUB_TYPES:
            return node, facility
    for kind in HUB_TYPES:
        for node in ui.nodes_of(area):
            for facility in ui.facilities_of(node).values():
                if ui.facility_type_of(facility) == kind:
                    return node, facility
    return None, None


def hub_in(area, node_id, facility_id):
    """控えに書いてある繋ぎ先を引き当てる。引けなければ `hub_of` に落ちる。"""
    if node_id and facility_id:
        for node in ui.nodes_of(area):
            if node_id_of(node) != str(node_id):
                continue
            facility = ui.facilities_of(node).get(str(facility_id))
            if facility is not None:
                return node, facility
    return hub_of(area)


def area_of(app, area_id, world=None):
    """id からエリアを引く。引けなければ None。"""
    if world is not None:
        return ui.areas_of_world(world).get(str(area_id))
    return ui.world_areas(app).get(str(area_id))


def facility_of(app, facility_id, world=None):
    """建っている実体を引く。登録簿の控えを先に見て、無ければ街から探す。"""
    record = registry().get(str(facility_id))
    if isinstance(record, dict) and record.get("facility") is not None:
        return record["facility"]
    spot = (record or {}).get("placed") if isinstance(record, dict) else None
    if not spot:
        return None
    area = area_of(app, spot[0], world)
    if area is None:
        return None
    facility, _node = ui.find_facility(area, str(facility_id))
    return facility


# --------------------------------------------------------------------------
# 建てる・壊す
# --------------------------------------------------------------------------
def spawn(app, facility_id, area_id, *, node_id=None, hub_id=None, world=None,
          write=None):
    """建物を1軒建てる。建った実体を返す。建てられなければ None。

    控えの写し（`snapshot`）が在ればそれを素データに使い、無ければ層の `fields`。
    繋ぎ先はノードが名乗っている入口で、引けなければ種類（`entrance` → `ward`）で探す。
    """
    facility_id = str(facility_id)
    if not gate_is_live():
        # 関所の無い世代で建てると、立ち位置の直しもボタンの掃除も効かない。
        # 建物の中で保存された世界は**二度と開けなくなる**ので、建てない。
        if write:
            write("WARN modfacility: the gate is not live; {} was not built".format(
                facility_id))
        return None
    record = _record(facility_id)
    area = area_of(app, area_id, world)
    if area is None:
        if write:
            write("WARN modfacility: area {!r} is not in this world".format(area_id))
        return None
    # 既に街に在るなら建て直さない（塗り直しで二重に建てない）。
    found, found_node = ui.find_facility(area, facility_id)
    if found is not None:
        record["facility"] = found
        record["built_in"] = getattr(patch, "_generation", None)
        record["placed"] = (str(area_id), node_id_of(found_node),
                            (record.get("placed") or ("", "", ""))[2])
        return found
    node, hub = hub_in(area, node_id, hub_id)
    if node is None or hub is None:
        if write:
            write("WARN modfacility: no hub facility in area {!r}".format(area_id))
        return None
    facilities = ui.facilities_of(node)
    if not isinstance(facilities, dict):
        if write:
            write("WARN modfacility: node {!r} has no facilities dict".format(
                node_id_of(node)))
        return None
    snap = record.get("snapshot")
    fields = dict(snap) if isinstance(snap, dict) else fields_of(facility_id)
    this_hub = facility_id_of(hub)
    data = template(facility_id, fields, this_hub)
    facility = build(app, node, data, write=write)
    if facility is None:
        return None
    facilities[facility_id] = facility
    link(hub, facility_id)
    link(facility, this_hub)
    record["facility"] = facility
    record["built_in"] = getattr(patch, "_generation", None)
    record["placed"] = (str(area_id), node_id_of(node), this_hub)
    _persist(app, owner_of_id(facility_id), facility_id,
             spawned=True, place=record["placed"])
    if write:
        write("modfacility: built {!r} ({}) in area {!r} node {!r} off {!r}({})".format(
            data.get("name"), facility_id, area_id, node_id_of(node),
            getattr(hub, "name", ""), this_hub))
    return facility


def despawn(app, facility_id, *, world=None, write=None):
    """建物を壊す。壊したら True。

    ノードの `facilities` から外し、繋ぎ先の `connections` からも外す。
    中に居る `modnpc` は呼ぶ側が先に降ろすこと（関所はそうしている）。
    """
    facility_id = str(facility_id)
    record = registry().get(facility_id)
    spot = (record or {}).get("placed") if isinstance(record, dict) else None
    if not spot:
        if isinstance(record, dict):
            record["facility"] = None
        return False
    area_id, node_id, hub_id = (list(spot) + ["", "", ""])[:3]
    area = area_of(app, area_id, world)
    gone = False
    if area is not None:
        for node in ui.nodes_of(area):
            facilities = ui.facilities_of(node)
            if not isinstance(facilities, dict) or facility_id not in facilities:
                continue
            facility = facilities.pop(facility_id)
            for other_id in connections_of(facility):
                other = facilities.get(str(other_id))
                if other is not None:
                    unlink(other, facility_id)
            gone = True
        _node, hub = hub_in(area, node_id, hub_id)
        if hub is not None:
            unlink(hub, facility_id)
    record["facility"] = None
    record["placed"] = None
    _persist(app, owner_of_id(facility_id), facility_id, spawned=False, place=None)
    if write and gone:
        write("modfacility: demolished {} in area {!r}".format(facility_id, area_id))
    return gone


# --------------------------------------------------------------------------
# 控え
# --------------------------------------------------------------------------
STORE_ATTR = "_instantale_modfacility_store"
STATE_DIRNAME = "modfacility"
#: 建て直しの間だけ立つ「いまの世界の鍵」。`World.__init__` の中では `app.world_dict` が
#: まだ前の世界を指していることがあるので、鍵を引数の `save_data_dict` から決めて持ち回る。
_KEY_OVERRIDE_ATTR = "_instantale_modfacility_key_override"


def bind_store(ctx, write=None):
    """控えを今の世代の `ctx` に繋ぐ（`install` が毎回呼ぶ）。"""
    found = getattr(sys, STORE_ATTR, None)
    if isinstance(found, state.WorldStore):
        return found.rebind(ctx, write)
    found = state.WorldStore(ctx, STATE_DIRNAME, write=write)
    setattr(sys, STORE_ATTR, found)
    return found


def store():
    """控え。`install` がまだなら None（控えずに動く）。"""
    found = getattr(sys, STORE_ATTR, None)
    return found if isinstance(found, state.WorldStore) else None


def _current_key(app):
    override = getattr(sys, _KEY_OVERRIDE_ATTR, None)
    if isinstance(override, str) and override:
        return override
    return state.world_key(app) if app is not None else state.UNKNOWN_WORLD


def _bucket(app):
    """`(世界の鍵, 控え)`。控えが無いか世界が分からなければ `(None, None)`。"""
    found = store()
    if found is None or app is None:
        return None, None
    key = _current_key(app)
    if not key or key == state.UNKNOWN_WORLD:
        return None, None
    return key, found.load(key)


def _jsonable(value):
    """控えに入れてよい値か。JSON に落ちるものだけ（実行時のオブジェクトは控えない）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_jsonable(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _jsonable(v) for k, v in value.items())
    return False


def _persist(app, owner, facility_id, **changes):
    """1軒ぶんの控えを書く。`snapshot=`（素データの写し）／`spawned=`／`place=`。

    控えの形は
    `{持ち主: {id: {"snapshot": {...}, "spawned": bool, "place": [エリア, ノード, 繋ぎ先]}}}`。
    """
    if not owner:
        return False
    key, bucket = _bucket(app)
    if key is None:
        return False
    entry = bucket.setdefault(owner, {}).setdefault(
        str(facility_id), {"snapshot": None, "spawned": False, "place": None})
    if "snapshot" in changes:
        entry["snapshot"] = copy.deepcopy(changes["snapshot"])
    if "spawned" in changes:
        entry["spawned"] = bool(changes["spawned"])
    if "place" in changes:
        entry["place"] = list(changes["place"]) if changes["place"] else None
    return store().save(key, bucket)


def _drop_persisted(app, owner, facility_id):
    key, bucket = _bucket(app)
    if key is None:
        return False
    owned = bucket.get(owner)
    if not isinstance(owned, dict) or str(facility_id) not in owned:
        return False
    owned.pop(str(facility_id), None)
    if not owned:
        bucket.pop(owner, None)
    return store().save(key, bucket)


def _persisted_entry(app, owner, facility_id):
    key, bucket = _bucket(app)
    if key is None:
        return None
    owned = bucket.get(owner)
    entry = owned.get(str(facility_id)) if isinstance(owned, dict) else None
    return entry if isinstance(entry, dict) else None


def _layer_of(owner, facility_id):
    for layer in layers(str(facility_id)):
        if layer["owner"] == owner:
            return layer
    return None


#: 写しに入れない項目。置き場所は `place` の控えが持ち、接続は建て直しで張り直す。
SNAPSHOT_SKIP = frozenset(("id", "connections"))


def snapshot_of(facility):
    """実体の素データを写す（セーブの項目名で）。"""
    out = {}
    for field in FACILITY_FIELDS:
        if field in SNAPSHOT_SKIP or not hasattr(facility, field):
            continue
        value = getattr(facility, field)
        if _jsonable(value):
            out[field] = copy.deepcopy(value)
    return out


def snapshot_all(app, write=None):
    """建っている MOD の施設の素データを控えに写す。ゲームの保存と同じ時点で呼ぶ。

    正規の施設はゲームがセーブに書く。MOD の施設はそこに出さないので、
    同じ時点で同じ中身をこちらが控える（名前・説明・主・`config`）。
    """
    done = []
    for facility_id, record in list(registry().items()):
        if not is_mod_facility(facility_id) or record.get("facility") is None:
            continue
        snap = snapshot_of(record["facility"])
        if _persist(app, owner_of_id(facility_id), facility_id, snapshot=snap):
            done.append(facility_id)
    if write and done:
        write("modfacility: snapshot of {} taken".format(", ".join(done)))
    return done


def restore_world(app, world=None, save_data_dict=None, write=None):
    """世界を読んだ直後、控えから建て直す。

    控えは持ち主ごとなので、**その持ち主の層が今の世代に登録されているものだけ**戻す
    （MOD を外していれば何も建たない）。
    """
    found = store()
    if found is None:
        return []
    key = state.world_key_of_dict(save_data_dict, None) \
        if save_data_dict is not None else None
    if not key:
        key = state.world_key(app)
    if not key or key == state.UNKNOWN_WORLD:
        return []
    setattr(sys, _KEY_OVERRIDE_ATTR, key)
    done = []
    try:
        bucket = found.load(key, fresh=True)
        for owner, owned in list((bucket or {}).items()):
            if not isinstance(owned, dict):
                continue
            for facility_id, entry in list(owned.items()):
                layer = _layer_of(owner, facility_id)
                if layer is None or not isinstance(entry, dict) \
                        or not is_mod_facility(facility_id):
                    continue
                record = _record(facility_id)
                snap = entry.get("snapshot")
                record["snapshot"] = copy.deepcopy(snap) \
                    if isinstance(snap, dict) else None
                spot = entry.get("place")
                if entry.get("spawned") and isinstance(spot, (list, tuple)) and spot:
                    spot = (list(spot) + ["", "", ""])[:3]
                    spawn(app, facility_id, spot[0], node_id=spot[1],
                          hub_id=spot[2], world=world, write=write)
                done.append((owner, facility_id))
    finally:
        try:
            delattr(sys, _KEY_OVERRIDE_ATTR)
        except AttributeError:
            pass
    if write and done:
        write("modfacility: restored {} entr(y/ies) from the state of {!r}".format(
            len(done), key))
    return done


# --------------------------------------------------------------------------
# 取っ手
# --------------------------------------------------------------------------
class Fac(object):
    """1軒ぶんの読み書き。**真実は実体1つ**で、値の変換も戻しもしない。

    建っていれば実体を読み書きし、建っていなければ層の `fields` と控えの写しを読む。
    書いた値は次の保存で控えへ写る（`snapshot_all`）。
    """

    __slots__ = ("app", "id", "world")

    def __init__(self, app, facility_id, world=None):
        self.app = app
        self.id = str(facility_id)
        self.world = world

    @property
    def record(self):
        return registry().get(self.id)

    @property
    def facility(self):
        return facility_of(self.app, self.id, self.world)

    @property
    def placed(self):
        """`(エリア id, ノード id, 繋ぎ先 id)`。建っていなければ None。"""
        record = self.record
        return (record or {}).get("placed") if isinstance(record, dict) else None

    @property
    def area_id(self):
        spot = self.placed
        return str(spot[0]) if spot else ""

    def _get(self, field):
        facility = self.facility
        if facility is not None and hasattr(facility, field):
            return getattr(facility, field)
        record = self.record
        snap = (record or {}).get("snapshot") if isinstance(record, dict) else None
        if isinstance(snap, dict) and field in snap:
            return snap[field]
        return fields_of(self.id).get(field)

    def _set(self, field, value):
        facility = self.facility
        if facility is not None:
            try:
                setattr(facility, field, value)
                return True
            except Exception:
                log_exc("modfacility: cannot set {} of {}".format(field, self.id))
                return False
        # 建っていないなら控えの写しに書く（次に建てるときの材料になる）。
        record = _record(self.id)
        snap = record.get("snapshot")
        if not isinstance(snap, dict):
            snap = record["snapshot"] = dict(fields_of(self.id))
        snap[field] = value
        _persist(self.app, owner_of_id(self.id), self.id, snapshot=snap)
        return True

    def config_get(self, key, default=None):
        config = self._get("config")
        return config.get(key, default) if isinstance(config, dict) else default

    def config_set(self, key, value):
        config = self._get("config")
        config = dict(config) if isinstance(config, dict) else {}
        config[key] = value
        return self._set("config", config)


def _field_property(field):
    return property(lambda self: self._get(field),
                    lambda self, value: self._set(field, value))


for _field in FACILITY_FIELDS:
    if _field != "id":
        setattr(Fac, _field, _field_property(_field))
del _field


def get(app, facility_id, world=None):
    """id から取っ手を返す。登録簿にも街にも無ければ None。"""
    handle = Fac(app, str(facility_id), world)
    if handle.record is not None or handle.facility is not None:
        return handle
    return None


# --------------------------------------------------------------------------
# 呼ぶ
# --------------------------------------------------------------------------
def fire(site, app, facility_id, *, facility=None, args=None, world=None,
         write=None):
    """その id に積まれた層のフックを順に呼ぶ。`[(持ち主, 戻り値), ...]`。

    フックの例外は飲む（1本の MOD の失敗で関所を止めない）。
    飲んだことはローダのログに残す。
    """
    results = []
    for layer in layers_for(facility_id):
        fn = (layer.get("on") or {}).get(site)
        if not callable(fn):
            continue
        info = {"site": site, "app": app, "facility_id": str(facility_id),
                "facility": facility, "args": args, "world": world,
                "owner": layer["owner"]}
        try:
            results.append((layer["owner"], fn(info)))
        except Exception:
            log_exc("modfacility: {} hook of {} on {} failed".format(
                site, layer["owner"], facility_id))
            if write:
                write("WARN modfacility: {} hook of {} on {} failed".format(
                    site, layer["owner"], facility_id))
    return results


def fire_all(site, app, *, args=None, world=None, write=None):
    """登録されている全部の施設に同じ場面を通知する。`on["world"]` 用。"""
    results = []
    for facility_id in list(registry()):
        results.extend(fire(site, app, facility_id, args=args, world=world,
                            write=write))
    return results


# --------------------------------------------------------------------------
# 居場所
# --------------------------------------------------------------------------
def player_facility_id(app):
    """プレイヤーが立っている施設の id。読めなければ空文字。

    立ち位置はロード直後だけ文字列で、遊んでいる最中は `Facility`（GAME.md §2.7）。
    どちらでも読む。
    """
    location = getattr(getattr(app, "player", None), "location", None)
    if location is None:
        return ""
    if isinstance(location, (str, int)):
        return str(location)
    return facility_id_of(location)


def inside(app, facility_id=None):
    """MOD の施設の中に立っているか。`facility_id` を省くと登録されている全部を見る。

    立っていればその id、立っていなければ空文字。

    **街から引けることまで見る。** 壊した後の建物に立っているのは「中に居る」ではなく
    取り残された形（`stranded`）で、出口しか出さない。

    本筋は `player.location` だが、ゲームは自分で足した施設をよく知らないので
    （一覧にも出さない）、移動の後に居場所が書き換わらないことがある。
    そのときは**こちらが起こした移動の控え**を使う。
    居場所が読めて、しかもよその施設だったときは、その控えを落とす。
    """
    here = player_facility_id(app)
    entered = _state().get("entered")
    if isinstance(entered, dict):
        marked = str(entered.get("facility") or "")
        if not here:
            here = marked
        elif here != marked:
            _state()["entered"] = None
    if not here or here not in registry():
        return ""
    if facility_id is not None and here != str(facility_id):
        return ""
    area = ui.current_area(app)
    if area is None:
        return ""
    facility, _node = ui.find_facility(area, here)
    return here if facility is not None else ""


def keep_inside(app, facility_id, write=None):
    """その建物の中に立ったまま保存してよいか。

    層が1つでも真を返せば中のまま。既定（宣言が無い）は入口へ移す。
    """
    for layer in layers_for(facility_id):
        declared = layer.get("keep_inside")
        if declared is None or declared is False:
            continue
        if declared is True:
            return True
        if not callable(declared):
            continue
        info = {"site": "keep_inside", "app": app,
                "facility_id": str(facility_id), "owner": layer["owner"]}
        try:
            if declared(info):
                return True
        except Exception:
            log_exc("modfacility: the keep_inside of {} on {} failed".format(
                layer["owner"], facility_id))
            if write:
                write("WARN modfacility: the keep_inside of {} on {} failed".format(
                    layer["owner"], facility_id))
    return False


def stranded(app):
    """街に無い施設に立っているか（取り残された形）。

    建物を壊した後にそこへ立っていると、選択肢が1つも無い画面に閉じ込められる
    （`914_` が実機で踏んだ。2026-09-11）。
    壊す側で防ぐのが本筋だが、**すでにそうなった遊びからも戻れるように**出口を出す。

    誤って出さないよう、立っている施設が街から引けないことと、
    その土地の入口が引けることの両方を見る。
    エリアはプレイヤー自身が持っているものなので、よその土地の入口へ出すことはない。
    """
    area = ui.current_area(app)
    if area is None:
        return False
    here = player_facility_id(app)
    if not here:
        return False
    facility, _node = ui.find_facility(area, here)
    if facility is not None:
        return False
    _node, hub = hub_of(area)
    return hub is not None


# --------------------------------------------------------------------------
# 移動の選択肢
# --------------------------------------------------------------------------
def move_choices(area, facility_id, skip=()):
    """その施設に立ったときにゲームが並べる選択肢（繋ぎ先への移動）を組む。

    **ロードは選択肢を組み直さない**。`game_variables["buttons"]` に焼かれているものを
    そのまま戻すだけ（GAME.md §2.3）。だから立ち位置だけ直しても、
    選択肢が空なら動けないままになる。

    返すのは**セーブと同じ形**の辞書。
    `skip` には MOD の施設の id を渡す。
    **セーブに無い施設への道をボタンにしてはいけない**
    （そのボタンは焼かれ、MOD の無い環境で押せてしまう）。
    """
    facility, node = ui.find_facility(area, str(facility_id))
    if facility is None or node is None:
        return []
    node_id = node_id_of(node)
    area_id = ui.area_id_of(area)
    skip = {str(s) for s in skip}
    entries_out = []
    for target in connections_of(facility):
        if str(target) in skip or is_mod_facility(target):
            continue
        other, _node = ui.find_facility(area, target)
        if other is None:
            continue
        entries_out.append({"text": getattr(other, "name", "") or str(target),
                            "spec": {"cls_name": MOVE_CLS,
                                     "args": [node_id, str(target), area_id]}})
    return entries_out


def live_move_buttons(screen, area, facility_id, skip=()):
    """`move_choices` と同じ並びを、走っているゲームのボタンの形で組む。"""
    out = []
    for entry in move_choices(area, facility_id, skip):
        spec = screen.make_spec(entry["spec"]["cls_name"], entry["spec"]["args"])
        if spec is None:
            return []
        out.append({"text": entry["text"], "spec": spec})
    return out


def move_spec_args(area, facility_id):
    """その施設へ入る `MovePhaseManager` の引数。組めなければ None。"""
    facility, node = ui.find_facility(area, str(facility_id))
    if facility is None or node is None:
        return None
    return [node_id_of(node), str(facility_id), ui.area_id_of(area)]


def repair_player_location(world, save_data_dict, write=None):
    """セーブの立ち位置が街に無い施設を指していたら、その土地の入口へ直す。

    ゲームのロードは、引けなかった施設に `'facilityが見つからない'` という**文字列**を
    入れてから `.name` を読むので、そこで必ず落ちる（`instantale.py:1493`）。
    **直すまで、その世界はもう開けない。**

    建て直した後に呼ぶので、戻ってきた建物の中に居るぶんには何もしない。
    ここで直すのは**読み込みの途中の辞書**で、セーブの書き換えではない。
    """
    player = save_data_dict.get("player_data") \
        if isinstance(save_data_dict, dict) else None
    if not isinstance(player, dict):
        return False
    here = str(player.get("location") or "")
    if not here:
        return False
    area = ui.areas_of_world(world).get(str(player.get("current_area") or ""))
    if area is None:
        return False
    facility, _node = ui.find_facility(area, here)
    if facility is not None:
        return False
    _node, hub = hub_of(area)
    hub_id = facility_id_of(hub) if hub is not None else ""
    if not hub_id:
        if write:
            write("WARN modfacility: the player stands in {!r}, which is not in "
                  "the town, and this area has no entrance".format(here))
        return False
    player["location"] = hub_id
    variables = save_data_dict.get("game_variables")
    choices = move_choices(area, hub_id, list(registry()))
    if isinstance(variables, dict) and choices:
        variables["buttons"] = choices
    if write:
        write("modfacility: the player was standing in {!r}, which is not in the "
              "town; moved to the entrance {} with {} choice(s)".format(
                  here, hub_id, len(choices)))
    return True


# --------------------------------------------------------------------------
# 関所
# --------------------------------------------------------------------------
#: 保存のあいだ、MOD の施設に立ったままにしないか（宣言の無い層への既定）。
#:
#: ロードの建て直し（`World.__init__` の後段）がゲームの立ち位置の引き当てより
#: 先に走るなら、中に居たまま保存してよい（入ったところから続けられる）。
#: `914_` はその順序を読んで中のまま保存する形にしたが、**実機で当てていない**。
#: ここは事故の代償が「その世界が二度と開けない」なので、既定は安全側に倒し、
#: 中のまま保存したい層が `keep_inside` で名乗り出る形にしてある。
LIFT_LOCATION = True

#: セーブに焼かれる、施設の id を載せた器（`app` の属性）。
SAVED_CHOICE_ATTRS = ("buttons", "buttons_backup", "buttons_backup_for_shopping")
#: 選択肢と同じ形の `PhaseSpec` を1つだけ持つ器（自由入力が次に呼ぶもの）。
SAVED_SPEC_ATTRS = ("function_correspond_to_input", "input_backup",
                    "input_backup_for_shopping")


def _spec_mentions(value, mod_ids):
    """`PhaseSpec`（またはその辞書）の `args` が MOD の施設を指しているか。

    実行時は `PhaseSpec` のオブジェクト、セーブから復元された直後は辞書のことがある。
    """
    args = ui.spec_args(value)
    if not args:
        data = value.get("spec") if isinstance(value, dict) else None
        if isinstance(data, dict):
            args = data.get("args") or []
    return any(str(a) in mod_ids for a in (args or ()))


def scrub_saved_refs(app, mod_ids, write=None):
    """保存の直前。MOD の施設を指す選択肢を外す。戻すための控えを返す。

    自前のボタンの spec は無害な既存クラスなので焼かれても押せないが、
    **ゲームが組んだ移動のボタン**が MOD の施設を指していたら、
    MOD を外した環境で押せてしまう（そこに建物はもう無い）。
    ゲームは実行時に足した施設を一覧に出さないので普段は空振りするが、
    出す版が来たときに漏らさないための関所。
    """
    mod_ids = {str(i) for i in mod_ids}
    if not mod_ids:
        return None
    undo = []
    for attr in SAVED_CHOICE_ATTRS:
        found = getattr(app, attr, None)
        if not isinstance(found, list):
            continue
        kept = [entry for entry in found if not _spec_mentions(entry, mod_ids)]
        if len(kept) == len(found):
            continue
        undo.append((attr, found))
        try:
            setattr(app, attr, kept)
        except Exception:
            log_exc("modfacility: cannot scrub {}".format(attr))
            undo.pop()
    for attr in SAVED_SPEC_ATTRS:
        found = getattr(app, attr, None)
        if found is None or not _spec_mentions(found, mod_ids):
            continue
        undo.append((attr, found))
        try:
            setattr(app, attr, None)
        except Exception:
            log_exc("modfacility: cannot scrub {}".format(attr))
            undo.pop()
    if write and undo:
        write("modfacility: save: scrubbed {} container(s)".format(len(undo)))
    return undo


def unscrub_saved_refs(app, undo):
    """保存の直後。外した選択肢を戻す。"""
    for attr, value in reversed(undo or ()):
        try:
            setattr(app, attr, value)
        except Exception:
            log_exc("modfacility: cannot put {} back".format(attr))


def safe_save_location(app, screen=None, write=None):
    """保存のあいだだけ立ち位置を入口へ替える。替えたなら `(player, 元の値, 元の選択肢)`。

    セーブに残るのは施設の id だけなので、
    **建て直されない建物の id が残ると、次のロードでゲームが落ちる**。
    立ち位置だけ替えても、焼かれる選択肢が前の場所のままでは動けないので、
    入口の選択肢も一緒に置く（GAME.md §2.3）。
    """
    player = getattr(app, "player", None)
    here = inside(app)
    if player is None or (not here and not stranded(app)):
        return None
    if here and (not LIFT_LOCATION or keep_inside(app, here, write=write)):
        # ロードで必ず建て直ると層が名乗った建物。入ったところから続けられる。
        return None
    area = ui.current_area(app)
    _node, hub = hub_of(area) if area is not None else (None, None)
    if hub is None:
        return None
    hub_id = facility_id_of(hub)
    was = getattr(player, "location", None)
    try:
        player.location = hub
    except Exception:
        log_exc("modfacility: cannot move the player for the save")
        return None
    buttons = getattr(app, "buttons", None)
    choices = live_move_buttons(screen, area, hub_id, list(registry())) \
        if screen is not None else []
    if choices:
        try:
            app.buttons = choices
        except Exception:
            log_exc("modfacility: cannot swap the choices for the save")
            buttons = None
    else:
        buttons = None
    if write:
        write("modfacility: save: the player was in {!r}; saving at the entrance "
              "{} with {} choice(s)".format(here or "a building that is gone",
                                            hub_id, len(choices)))
    return (player, was, buttons)


def hide(app, *, screen=None, world=None, write=None):
    """保存の直前。MOD の施設の痕跡を引き上げる。戻すための控えを返す。

    **実体は隠さない。** 保存は `save_data_dict['areas']` から書き、
    実行時の `node.facilities` を舐めないので、建物はそのまま置いてよい
    （`modnpc` が名簿を `_RosterView` に差し替えるのと、そこが違う）。
    引き上げるのは id が載る器のほうだけ。
    """
    scrubbed = scrub_saved_refs(app, list(registry()), write=write)
    swapped = None
    try:
        swapped = safe_save_location(app, screen=screen, write=write)
    except Exception:
        log_exc("modfacility: cannot check the place before the save")
    return {"scrubbed": scrubbed, "swapped": swapped, "world": world}


def restore(app, hidden, *, write=None):
    """保存の直後。引き上げたものを戻す。"""
    if not isinstance(hidden, dict):
        return
    swapped = hidden.get("swapped")
    if swapped is not None:
        player, was, buttons = swapped
        try:
            player.location = was
            if buttons is not None:
                app.buttons = buttons
        except Exception:
            log_exc("modfacility: cannot put the player back after the save")
    unscrub_saved_refs(app, hidden.get("scrubbed"))


# --------------------------------------------------------------------------
# 画面（ゲームが出さないものを出す）
# --------------------------------------------------------------------------
#: 自前のボタンに付ける印の鍵。関所が1つなので、鍵も1つでよい。
#: どの建物のどの選択肢かは**印の値**が持つ（`<種別>\t<施設 id>\t<鍵>`）。
MARK = "mod_facility"

#: 出口の既定の文言。層が `exit_label` で変えられる。
DEFAULT_EXIT_LABEL = "出る"

SCREEN_ATTR = "_instantale_modfacility_screen"
_STATE_ATTR = "_instantale_modfacility_state"

#: 印の値の種別。
PRESS_ENTER = "enter"
PRESS_EXIT = "exit"
PRESS_DO = "do"


def _state():
    """塗り直しと押下の控え。注入をまたぐ（`sys` に置く）。"""
    found = getattr(sys, _STATE_ATTR, None)
    if not isinstance(found, dict):
        found = {"retry": False, "acting": False, "painted": None,
                 # こちらが起こした「建物へ入る」移動の控え。
                 # ゲームは自分で足した施設をよく知らないので、
                 # 移動の後に `player.location` が書き換わらないことがある。
                 "entered": None,
                 # 最後に書いた居場所（変わったときだけ1行書くため）。
                 "where": None,
                 # 背景の描き直しを予約した（まだ走っていない）。
                 "background_pending": False}
        setattr(sys, _STATE_ATTR, found)
    return found


def screen_of():
    """関所が持っている `ui.Screen`。`install` がまだなら None。"""
    found = getattr(sys, SCREEN_ATTR, None)
    return found if isinstance(found, ui.Screen) else None


def _screen(screen=None):
    return screen if screen is not None else screen_of()


def choices_of(app, facility_id, screen=None, write=None):
    """その建物の中の選択肢。`[{"key", "label", "on", "owner"}, ...]`。

    層が `choices` で宣言したものを `priority` 順に並べ、**最後に出口を足す**。
    宣言が関数なら、その時点の状態で組ませる（`fn(info)`）。
    """
    out = []
    exit_label = DEFAULT_EXIT_LABEL
    for layer in layers_for(facility_id):
        if layer.get("exit_label"):
            exit_label = layer["exit_label"]
        declared = layer.get("choices")
        if callable(declared):
            info = {"site": "choices", "app": app,
                    "facility_id": str(facility_id), "owner": layer["owner"],
                    "screen": _screen(screen)}
            try:
                declared = declared(info)
            except Exception:
                log_exc("modfacility: the choices of {} on {} failed".format(
                    layer["owner"], facility_id))
                if write:
                    write("WARN modfacility: the choices of {} on {} failed".format(
                        layer["owner"], facility_id))
                continue
        for item in (declared or ()):
            if not isinstance(item, dict) or not item.get("label"):
                continue
            entry = dict(item)
            entry.setdefault("key", entry["label"])
            entry["owner"] = layer["owner"]
            out.append(entry)
    out.append({"key": PRESS_EXIT, "label": exit_label, "on": None,
                "owner": None})
    # 組んだ結果に手を入れる最後の口。
    for _owner, changed in fire("choices", app, facility_id,
                                args={"choices": out}, write=write):
        if isinstance(changed, list):
            out = changed
    return out


def our_labels(app):
    """残骸の掃除に使う文言（`prune_stale`）。いま出しうるものを全部集める。

    印はセーブに焼かれないので、ロードや再注入のあと**印を失った自分のボタン**が
    画面に戻っている。文言でしか見分けられない（TECH.md §6.2）。
    """
    labels = set()
    for facility_id in registry():
        handle = Fac(app, facility_id)
        name = handle.name
        if isinstance(name, str) and name:
            labels.add(name)
        for layer in layers_for(facility_id):
            if layer.get("exit_label"):
                labels.add(layer["exit_label"])
            declared = layer.get("choices")
            if callable(declared):
                continue
            for item in (declared or ()):
                if isinstance(item, dict) and item.get("label"):
                    labels.add(item["label"])
    labels.add(DEFAULT_EXIT_LABEL)
    return sorted(labels)


def is_facility_screen(buttons):
    """施設の選択肢の画面か（ゲームの移動のボタンが1つでもある）。"""
    if not isinstance(buttons, list):
        return False
    return any(ui.spec_cls_name(entry) == MOVE_CLS for entry in buttons)


def _press_value(kind, facility_id, key=""):
    return "\t".join((kind, str(facility_id), str(key)))


def _parse_press(value):
    """印の値を `(種別, 施設 id, 鍵)` に分ける。読めなければ `(None, "", "")`。"""
    if not isinstance(value, str):
        return None, "", ""
    parts = value.split("\t")
    while len(parts) < 3:
        parts.append("")
    return parts[0] or None, parts[1], parts[2]


def _already(buttons, value, screen):
    """同じ印のボタンが既に画面に在るか。

    ふだんは塗り直しのたびにゲームが選択肢を組み直すので自前のボタンは消えているが、
    組み直しを伴わない呼ばれ方（手が空いてからのやり直し）でも増えないようにする。
    """
    if screen is None:
        return False
    return any(screen.mark_of(entry) == value for entry in buttons)


def add_inside_buttons(app, buttons, facility_id, screen=None, write=None):
    """建物の中の選択肢を足す。**出口もここで出す**。

    ゲームは自分で足した施設の `connections` を読まないので、
    中に立っても選択肢が1つも出ない。出口が無いと建物から出られなくなる。
    """
    screen = _screen(screen)
    if screen is None:
        return False
    at = len(buttons)
    for index, item in enumerate(buttons):
        if ui.spec_cls_name(item) == MOVE_CLS:
            at = index
            break
    added = False
    for choice in choices_of(app, facility_id, screen=screen, write=write):
        kind = PRESS_EXIT if choice["key"] == PRESS_EXIT else PRESS_DO
        if kind == PRESS_EXIT and is_facility_screen(buttons):
            # ゲームの移動が並んでいる画面。出る道はもうあるので足さない。
            continue
        value = _press_value(kind, facility_id, choice["key"])
        if _already(buttons, value, screen):
            continue
        entry = screen.button(choice["label"], mark=value)
        if entry is None:
            continue
        buttons.insert(at, entry)
        at += 1
        added = True
    return added


def add_exit_button(app, buttons, facility_id="", screen=None, force=False):
    """出口だけ足す。取り残されたときと、中の選択肢を出さない建物のため。

    ふだんはゲームの移動が1つでもあれば足さない（出る道はもうある）。
    取り残されたときだけ `force` で押し通す。
    `914_` が実機で踏んだ画面には**選択肢が4つ残っていた**が、そこから街へは戻れなかった。
    残っている選択肢が出口かどうかは当てにできない。
    """
    screen = _screen(screen)
    if screen is None or (not force and is_facility_screen(buttons)):
        return False
    value = _press_value(PRESS_EXIT, facility_id)
    if _already(buttons, value, screen):
        return False
    # 層が残っていればその文言で出す（壊した後の取り残しでは層ごと消えている）。
    label = DEFAULT_EXIT_LABEL
    for layer in layers_for(facility_id):
        if layer.get("exit_label"):
            label = layer["exit_label"]
    entry = screen.button(label, mark=value)
    if entry is None:
        return False
    buttons.insert(max(len(buttons) - 1, 0), entry)
    return True


def add_move_buttons(app, buttons, screen=None, write=None):
    """いま立っている場所から入れる MOD の施設への道を足す。

    ゲームはこの施設を一覧に出さないので、道はこちらが出す。
    ボタンの spec は無害な既存クラスにして、押下は印で横取りする
    （`MovePhaseManager` を spec に書くと、焼かれた後に MOD の無い環境で押せてしまう）。
    """
    screen = _screen(screen)
    area = ui.current_area(app)
    here = player_facility_id(app)
    if screen is None or area is None or not here:
        return False
    facility, _node = ui.find_facility(area, here)
    if facility is None:
        return False
    area_id = ui.area_id_of(area)
    added = False
    for target in connections_of(facility):
        if not is_mod_facility(target):
            continue
        record = registry().get(str(target))
        spot = (record or {}).get("placed") if isinstance(record, dict) else None
        if not spot or str(spot[0]) != str(area_id):
            continue
        if any(ui.spec_cls_name(e) == MOVE_CLS
               and len(ui.spec_args(e) or ()) > 1
               and str(ui.spec_args(e)[1]) == str(target) for e in buttons):
            continue
        value = _press_value(PRESS_ENTER, target)
        if _already(buttons, value, screen):
            continue
        name = Fac(app, target).name or str(target)
        entry = screen.button(name, mark=value)
        if entry is None:
            continue
        buttons.insert(max(len(buttons) - 1, 0), entry)
        added = True
    return added


def retry_when_idle(app, screen=None, write=None):
    """流し込みの最中に来た足し直しを、手が空いてからやり直す。

    `maintain_buttons` は本文が流れている間は何もしない（GAME.md §2.6）。
    ところが**自前の画面から戻す塗り直しは、いつも `add_text` の直後**に走る。
    そこで黙って戻ると、次にゲームが選択肢を組み直すまで出番が来ない
    （`914_` が実機で踏んだ。窓口が消えたままになった）。

    見張りは同時に1つだけ立てる。塗り直しは1手に何度も走るので、
    素直に立てると同じ見張りがその回数だけ並ぶ。
    """
    screen = _screen(screen)
    if screen is None or _state().get("retry"):
        return
    _state()["retry"] = True

    def again():
        _state()["retry"] = False
        try:
            maintain_buttons(app, screen=screen, write=write)
        except Exception:
            log_exc("modfacility: cannot maintain the choices (idle)")

    screen.when_idle(app, again, proceed_on_timeout=True, tag="retry")


def note_place(app, buttons, here, lost, write=None):
    """立っている場所が変わったら1行だけ書く（変わらないあいだは黙る）。

    「建物に入ったのに選択肢が出ない」が起きたとき、どちらが欠けていたのか
    （居場所の読み取りか、ゲームの選択肢か）を後から読めるようにするための行。
    """
    location = getattr(getattr(app, "player", None), "location", None)
    if isinstance(location, (str, int)) or location is None:
        facility_id, kind = str(location or ""), "(id only)"
    else:
        facility_id = facility_id_of(location)
        kind = ui.facility_type_of(location) or "?"
    token = (ui.area_id_of(ui.current_area(app)), facility_id, kind,
             bool(here), bool(lost), len(buttons))
    if _state().get("where") == token:
        return False
    _state()["where"] = token
    if write:
        write("modfacility: where: area={} facility={} type={} inside={} "
              "stranded={} game_choices={}".format(
                  token[0], facility_id, kind, here or "-", lost, len(buttons)))
    return True


def maintain_buttons(app, screen=None, write=None):
    """いまの画面に応じて自前のボタンを足す。何度呼んでも増えない。"""
    screen = _screen(screen)
    buttons = getattr(app, "buttons", None)
    if screen is None or not isinstance(buttons, list):
        return False
    if ui.busy_signals(app):
        # 本文が流れている最中は触らない。手が空いてからやり直す。
        retry_when_idle(app, screen=screen, write=write)
        return False
    screen.prune_stale(buttons, our_labels(app))
    here = inside(app)
    lost = (not here) and stranded(app)
    note_place(app, buttons, here, lost, write=write)
    if here:
        ensure_background(app, here, screen=screen, write=write)
        return add_inside_buttons(app, buttons, here, screen=screen, write=write)
    # 建物の外に出た。次に入ったときは背景を描き直す。
    _state()["painted"] = None
    _state()["entered"] = None
    _state()["background_pending"] = False
    if lost:
        return add_exit_button(app, buttons, player_facility_id(app),
                               screen=screen, force=True)
    return add_move_buttons(app, buttons, screen=screen, write=write)


# --------------------------------------------------------------------------
# 出入り
# --------------------------------------------------------------------------
def _move_phase(app, args):
    """`MovePhaseManager` を組む。組めなければ None。"""
    cls = getattr(sys.modules.get("__main__"), MOVE_CLS, None)
    if cls is None or not args:
        return None
    try:
        return cls(app, *args)
    except Exception:
        log_exc("modfacility: cannot build {}".format(MOVE_CLS))
        return None


def enter(app, facility_id, screen=None, write=None):
    """建物へ入る。ゲームの移動をその場で組んで起こす。

    引数の並びは実測した「出る」のボタンと同じ（GAME.md §2.2）。
    施設を引き当ててから組むので、建物が無ければ何も起こさない。
    """
    screen = _screen(screen)
    area = ui.current_area(app)
    args = move_spec_args(area, facility_id) if area is not None else None
    phase = _move_phase(app, args)
    if phase is None or screen is None:
        if write:
            write("WARN modfacility: cannot reach {}".format(facility_id))
        return False
    name = Fac(app, facility_id).name or str(facility_id)
    _state()["entered"] = {"facility": str(facility_id),
                           "area": ui.area_id_of(area)}
    fire("enter", app, facility_id, args={"name": name}, write=write)
    if write:
        write("modfacility: enter {!r} via {}".format(name, args))
    return screen.start_phase(app, phase, name)


def leave(app, facility_id=None, screen=None, write=None):
    """建物から出る。繋ぎ先へゲームの移動で戻す。

    ゲームは自分で足した施設の出口を作らないので、帰り道もこちらで起こす。
    控えの繋ぎ先が引けなければ、その土地の入口へ出す（取り残されたときの道）。
    """
    screen = _screen(screen)
    area = ui.current_area(app)
    if area is None or screen is None:
        if write:
            write("WARN modfacility: no area here")
        return False
    facility_id = str(facility_id or inside(app) or "")
    record = registry().get(facility_id)
    spot = (record or {}).get("placed") if isinstance(record, dict) else None
    hub_id = str(spot[2]) if spot and len(spot) > 2 else ""
    args = move_spec_args(area, hub_id) if hub_id else None
    if args is None:
        _node, hub = hub_of(area)
        if hub is not None:
            hub_id = facility_id_of(hub)
            args = move_spec_args(area, hub_id)
    phase = _move_phase(app, args)
    if phase is None:
        if write:
            write("WARN modfacility: cannot reach the hub {!r} of {!r}".format(
                hub_id, facility_id))
        return False
    fire("leave", app, facility_id, args={"hub": hub_id}, write=write)
    _state()["painted"] = None
    _state()["entered"] = None
    if write:
        write("modfacility: leave {} -> hub {} via {}".format(
            facility_id or "(nowhere)", hub_id, args))
    return screen.start_phase(app, phase, DEFAULT_EXIT_LABEL)


def press(app, button_index, screen=None, write=None):
    """自前のボタンを横取りする。横取りしたら True（呼ぶ側は素通ししない）。"""
    screen = _screen(screen)
    if screen is None:
        return False
    entry = ui.pressed_entry(app, button_index)
    kind, facility_id, key = _parse_press(screen.mark_of(entry))
    if kind is None:
        return False
    if _state().get("acting"):
        if write:
            write("modfacility: ignored a press; the previous one is still running")
        return True
    text = (entry.get("text") if isinstance(entry, dict) else None) or key
    if write:
        write("modfacility: pressed {!r} ({} {} {})".format(
            text, kind, facility_id, key))
    _state()["acting"] = True
    try:
        if kind == PRESS_ENTER:
            enter(app, facility_id, screen=screen, write=write)
            return True
        if kind == PRESS_EXIT:
            leave(app, facility_id, screen=screen, write=write)
            return True
        for choice in choices_of(app, facility_id, screen=screen, write=write):
            if str(choice.get("key")) != key or not callable(choice.get("on")):
                continue
            info = {"site": "press", "app": app, "facility_id": facility_id,
                    "facility": facility_of(app, facility_id), "choice": choice,
                    "text": text, "screen": screen, "owner": choice.get("owner")}
            try:
                choice["on"](info)
            except Exception:
                log_exc("modfacility: the press of {} on {} failed".format(
                    choice.get("owner"), facility_id))
                if write:
                    write("WARN modfacility: the press of {} on {} failed".format(
                        choice.get("owner"), facility_id))
            return True
        return True
    finally:
        _state()["acting"] = False


# --------------------------------------------------------------------------
# 背景
# --------------------------------------------------------------------------
def ensure_background(app, facility_id, screen=None, write=None):
    """建物に立っているのに背景をまだ描いていなければ、こちらから描く。

    ゲームが背景を決める経路は1つとは限らないので、包みだけに頼らない
    （ロードの後に街の外の景色が残っていた。`914_` が実機で踏んだ）。

    ただし**同じ絵を二度描かない**。
    ゲームの経路が先に描いていればそこで終わりで、
    予約から実行までの間に描かれた場合も、走った時点でもう一度確かめて降りる
    （画像の読み込みが2回走って見えた）。
    Kivy に触るのでメインスレッドへ回す。
    """
    screen = _screen(screen)
    facility_id = str(facility_id)
    if screen is None or _state().get("painted") == facility_id \
            or _state().get("background_pending"):
        return False
    _state()["background_pending"] = True

    def paint_now():
        _state()["background_pending"] = False
        if _state().get("painted") == facility_id:
            # 待っている間にゲームの経路が描いた。
            return
        paint_background(app, facility_id, "on arrival", write=write)

    screen.schedule(paint_now, 0)
    return True


def _repaint_after_failure(app, why, write=None):
    """ゲーム自身の背景の差し替えが落ちたときの後始末。

    本体の `change_background_image_from_location_id` は `self.app` を読むが、
    `InstantaleApp` にその属性は無い（`AttributeError`。`914_` が実機で確認）。
    **呼ばれた時点で必ず落ちる本体の不具合**で、自分の建物で「他者と交流」を選ぶと
    ゲーム自身がここを通り、握らないとスレッドごと落ちて活動が終わらない。

    絵が変わらないだけなので握って先へ通すが、MOD の施設に立っているなら
    描き直す（握った先で街の景色が残るのを避ける）。
    """
    if write:
        write("WARN modfacility: the game's own {} raised; "
              "the picture stays as it is".format(why))
    try:
        here = inside(app)
        if here:
            _state()["painted"] = None
            paint_background(app, here, "after the game failed", force=True, write=write)
    except Exception:
        log_exc("modfacility: cannot repaint after the game's failure")


def paint_background(app, facility_id, why, force=False, write=None):
    """MOD の施設の背景を描く。描いたら True。

    ゲームは背景を施設 id から引くので、自作の施設では前の絵（街の景色）が残る。
    何を描くかは層が決める（`on["background"]`）。

    **`force` は「ゲームが背景を決めようとしている場面」の印。**
    そこでは必ず描く（ゲームがその場面で絵を変えたがっているのに、
    こちらが降りると街の景色のままになる）。
    抑えるのは「立っているから念のため描く」ほうだけで、
    そちらを抑えないと読み込みが2回走って絵がちらつく（`914_` が実機で踏んだ）。
    """
    facility_id = str(facility_id)
    if not force and _state().get("painted") == facility_id:
        return False
    results = fire("background", app, facility_id,
                   facility=facility_of(app, facility_id),
                   args={"why": why}, write=write)
    if not any(bool(value) for _owner, value in results):
        return False
    _state()["painted"] = facility_id
    if write:
        write("modfacility: background of {} painted ({})".format(facility_id, why))
    return True


# --------------------------------------------------------------------------
# 関所を立てる
# --------------------------------------------------------------------------
SAVE_TARGET = "__main__:InstantaleApp.save_game"
WORLD_TARGET = "__main__:World.__init__"
REFRESH_TARGET = "__main__:InstantaleApp.refresh_choice_buttons"
PRESS_TARGET = "__main__:InstantaleApp.on_button_press"
BG_CURRENT_TARGET = "__main__:InstantaleApp.change_background_image_to_current_location"
BG_ID_TARGET = "__main__:InstantaleApp.change_background_image_from_location_id"


def installed():
    """関所の状態。`{"generation", "owner", "targets"}` か、立っていなければ None。"""
    done = getattr(sys, INSTALLED_ATTR, None)
    return done if isinstance(done, dict) else None


def gate_is_live():
    """保存の関所が**今の世代で**立っているか。

    登録簿は `sys` に在って関所より長生きするので、
    関所を立てた MOD の `apply()` が失敗した世代では、
    前の世代の建物が街に残ったまま関所だけが無いことがある。
    `spawn` はここが真でなければ建てない（`modnpc` が実機で踏んだのと同じ理由）。
    """
    try:
        _owner, _name, current = patch.resolve(SAVE_TARGET)
    except (LookupError, AttributeError):
        return False
    for _ in range(32):
        if current is None:
            return False
        if getattr(current, "__wrapper_of__", None) == SAVE_TARGET \
                and getattr(current, "__instantale_modfacility_gate__", False) \
                and getattr(current, patch.GENERATION_MARK, None) == patch._generation:
            return True
        current = getattr(current, "__original__", None)
    return False


def install(ctx, write=None):
    """関所を立てる。包んだ対象の名前を返す。

    フレームワークを使う MOD が `apply()` の中で呼ぶ。
    何本の MOD が呼んでも、1つの世代につき関所は1つしか立たない。

    > `modnpc` とは別の関所になる。施設の建て直しを NPC の置き直しより先に
    > 済ませたいときは、**MOD の側が `on["world"]` で置く**こと
    > （フレームワークどうしは順序を約束しない）。
    """
    bind_store(ctx, write)
    generation = getattr(ctx, "generation", None)
    done = installed()
    if done is not None and done.get("generation") == generation:
        return done.get("targets", [])
    setattr(sys, SCREEN_ATTR, ui.Screen(ctx, write or (lambda *a: None),
                                        tag="modfacility", mark=MARK))
    targets = _install(ctx, write)
    setattr(sys, INSTALLED_ATTR, {"generation": generation,
                                  "owner": getattr(ctx, "_mod", None),
                                  "targets": targets})
    if write:
        write("modfacility: the gate was declared on {}".format(", ".join(targets)))
    return targets


def _install(ctx, write):
    """関所の中身。宣言した対象の名前を集めて返す。

    実際に当たったかはここでは分からない（対象がまだ読み込まれていなければ保留になる。
    TECH.md §3.4）。
    """
    targets = []

    def save_game(orig, self, *args, **kwargs):
        """保存の間だけ、MOD の施設の痕跡を世界から外す。"""
        hidden = None
        try:
            fire_all("save", self, args={"phase": "hide"}, write=write)
            snapshot_all(self, write=write)   # ゲームの保存と同じ時点で素データを写す
            hidden = hide(self, screen=screen_of(), write=write)
        except Exception:
            log_exc("modfacility: cannot lift the buildings before the save")
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                restore(self, hidden, write=write)
                fire_all("save", self, args={"phase": "restore"}, write=write)
            except Exception:
                log_exc("modfacility: cannot put the buildings back after the save")
    # ローダの `wrap` は差し込むラッパではなく元の関数を返すので、
    # 印は差し込んだ後に対象を引き直して付ける（`gate_is_live` が見る）。
    ctx.wrap(SAVE_TARGET, required=False)(save_game)
    try:
        _owner, _name, current = patch.resolve(SAVE_TARGET)
        if getattr(current, "__wrapper_of__", None) == SAVE_TARGET:
            current.__instantale_modfacility_gate__ = True
    except Exception:
        pass
    targets.append(SAVE_TARGET)

    @ctx.wrap(WORLD_TARGET, required=False, safe=True)
    def world_init(orig, self, save_data_dict=None, app=None, *args, **kwargs):
        """セーブを読んだ直後。前の世界の建物を忘れ、控えから建て直す。

        **立ち位置を検めるのは建て直した後**。
        戻ってきた建物の中に居るぶんには何もせず、
        街に無い施設を指しているときだけ入口へ直す（そのままではロードで落ちる）。
        """
        result = orig(self, save_data_dict, app, *args, **kwargs)
        key = state.world_key_of_dict(save_data_dict, None)
        if key:
            setattr(sys, _KEY_OVERRIDE_ATTR, key)
        try:
            forget(write=write)
            _state()["painted"] = None
            fire_all("world", app, world=self,
                     args={"save_data_dict": save_data_dict}, write=write)
            restore_world(app, world=self, save_data_dict=save_data_dict,
                          write=write)
            repair_player_location(self, save_data_dict, write=write)
        except Exception:
            log_exc("modfacility: cannot rebuild the town after the load")
        finally:
            try:
                delattr(sys, _KEY_OVERRIDE_ATTR)
            except AttributeError:
                pass
        return result
    targets.append(WORLD_TARGET)

    @ctx.wrap(REFRESH_TARGET, required=False, safe=True)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """選択肢が組み直されるたびに、自前のボタンを足し直す。"""
        result = orig(self, reset_page, *args, **kwargs)
        try:
            maintain_buttons(self, write=write)
        except Exception:
            log_exc("modfacility: cannot maintain the choices")
        return result
    targets.append(REFRESH_TARGET)

    @ctx.wrap(PRESS_TARGET, required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        try:
            if press(self, button_index, write=write):
                return None
        except Exception:
            log_exc("modfacility: the press hook failed")
        return orig(self, button_index, *args, **kwargs)
    targets.append(PRESS_TARGET)

    @ctx.wrap(BG_CURRENT_TARGET, required=False, safe=True)
    def background_current(orig, self, *args, **kwargs):
        """いまの場所の背景。MOD の施設に立っていれば層に描かせる。"""
        try:
            here = inside(self)
            if here:
                # 描けても描けなくても本体へは渡さない。
                # ゲームは自作の施設を素データから引けないので、渡すと
                # 街の景色で上書きされる（描いた絵がその場で消える）。
                paint_background(self, here, "current", force=True, write=write)
                return None
        except Exception:
            log_exc("modfacility: cannot paint the background")
        try:
            return orig(self, *args, **kwargs)
        except AttributeError:
            _repaint_after_failure(self, "change_background_image_to_current_location",
                                   write)
            return None
    targets.append(BG_CURRENT_TARGET)

    @ctx.wrap(BG_ID_TARGET, required=False, safe=True)
    def background_from_id(orig, self, location_id=None, *args, **kwargs):
        """id 指定の背景。MOD の施設なら層に描かせる。

        本体のこれは `self.app` を読むので、呼ばれた時点で必ず `AttributeError` になる
        （`InstantaleApp` にその属性は無い。`914_` が実機で確認）。
        MOD の施設でないときにそれを握って先へ通すのは、
        ゲーム自身が通る経路（自分の建物での「他者と交流」）を止めないため
        （絵が変わらないだけで、描写も好感度も走る）。
        """
        try:
            if location_id is not None and is_mod_facility(location_id):
                paint_background(self, location_id, "id", force=True, write=write)
                return None      # 本体はこの id を引けない（上の枠と同じ理由）
        except Exception:
            log_exc("modfacility: cannot paint the background from an id")
        try:
            return orig(self, location_id, *args, **kwargs)
        except AttributeError:
            _repaint_after_failure(self, "change_background_image_from_location_id",
                                   write)
            return None
    targets.append(BG_ID_TARGET)

    return targets
