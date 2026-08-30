# -*- coding: utf-8 -*-
r"""修正: 遊んでいる最中に生まれた店で売買が開けない。

##### 何が起きているか

世界を作った後に生まれた施設で「売買する」を選ぶと、その場で処理が落ちる。

    File "instantale.py", line 3080, in shopping_start_method_1
    KeyError: '229'
      area_id = '8'   node_id = '32'   facility_id = '229'

プレイヤーはその施設に立ってボタンを押せているので、
組み上がった `Area` / `Node` / `Facility` の側には施設が在る
（同じ来店で `312_` が `first visit: ガルド(99) @ 229` を書いている）。
にもかかわらず id で引き直すと引けない。

##### 素データの辞書は2つあって、片方に追加が届いていない

`app.world_dict` と `app.save_data_dict` は別の辞書で、件数も違う（GAME.md §2.23）。
実セーブを復号して突き合わせた（GAME.md §2.28）:

| | world 側 | save 側 |
|---|---|---|
| 施設 | 228 | 230 |
| NPC | 92 | 100 |
| エリア | 40 | 53 |
| `index` | facility 230 / npc 100 / area 53 | 同じ |

save 側は world 側の厳密な上位集合で、world 側にだけ在るものは1件も無い。
そして `index`（採番）は両方とも同じところまで進んでいる。
採番だけが両方に届いていて、実体が片方にしか足されていない。

世界生成のときからある店はどちらの辞書にも在るので、今まで出なかった。

##### 引き方を推測しない。`KeyError` のキーを起点にする

版1と版2は `shopping_start_method_1` の引数から
`(area_id, node_id, facility_id)` を拾おうとして、実機で1つも拾えなかった。

    skip: could not read the ids from the arguments: args=() kwargs=[]

**この関数は `self` 以外の引数を取らない。**
クラッシュ記録に出ていた3つは引数ではなく、関数の中で作られたローカル変数だった
（同梱の `DOC.md` §3）。
どこから求めているのかは本体が凍結されていて読めない。

そこで求め方を当てにいくのをやめた。
やることは1つ:

    元の処理を呼ぶ → `KeyError` で落ちたら、そのキーを素データに埋めて、もう一度だけ呼ぶ

ゲームが何を引き損なったかは例外自身が持っている。
こちらは引き先も求め方も知らなくてよい
（TECH.md §6.2「値の語彙を推測するくらいなら、語彙を知らずに済む経路を探す」）。

##### 埋め方

キーを持っている辞書を探し、持っていない辞書へ写す。

    施設として見つかった → その施設が居るエリア・ノードごと経路を辿り、
                           欠けている一番上の段を丸ごと写す
    NPC として見つかった → `npcs` へ1人写す

施設を写したときは、その主（`owner`）も続けて揃える。
主が引けないと次の `KeyError` になるため。

写すのは `copy.deepcopy` でそのまま。項目を整えたり足したりはしない。
ゲーム自身が項目のばらつきを持っているので、こちらが揃えると
ゲームが作っていない形を作ることになる（実測: 施設は8項目が284件と
`tier` を欠く7項目が18件、遊んでいる最中に生まれた NPC 8人は `speech_style` を欠く）。

既に在れば1行も動かない。
本体が両方の辞書へ書くようになった版では、この MOD は何もしない。

##### やり直しを1回に閉じ込める

`orig` をもう一度呼ぶので、**1回目が途中まで進んでいると副作用が二度走る**。
落ちているのは施設を引いた行なので、その手前は id を求めるところまでのはずだが、
本体が読めない以上そこは推定になる。
そこで縛りを3つ置いた:

    やり直すのは「埋められた」ときだけ（埋めるものが無ければ素の動作へ渡す）
    同じキーで二度は埋めない（埋めたのに同じ所で落ちる ＝ 引き先が別）
    やり直しの上限は MAX_FILLS 回

正常に開ける店では `orig` は1回しか呼ばれない。

##### 書き先を1つに決めない

引き先が `app.world_dict` だとは断定できない。
そこで `areas` を持つ辞書を `app` と `app.world` の直下から全部集め、
キーを持っていないもの全部に写す（`902_` の `npc_stores` と同じ手）。
余分に書いても、同じ id に同じ値が入るだけで害が無い。

`npcs` は主を写すのに使うだけなので、持つことを条件にしない
（版1はここも条件にしていた）。

##### 直せなかったときに、見えていたものを残す

版1は記録の出口が無く、実機で `out\new_shop_data.log` が1行も作られなかった。
フックは当たっていたのに、黙って抜ける道が3本あったせいで何も分からなかった。

いまは `orig` が落ちて、しかも埋められなかったときにだけ、見えていたものを全部書く:

    引き損なったキーと、それを持っている辞書
    `app` と `app.world` が持っている辞書の一覧（名前から推測しない。TECH.md §6.3）
    組み上がった側の名簿に施設が居るか
    `player.location` の型と id（現在地が id の文字列のままかどうか。GAME.md §2.7）

正常に開ける店では1行も書かない。同じキーにつき1度だけ。

##### セーブに残る

写すのはゲーム自身が作った実体をゲーム自身の形のままで、
MOD 独自の項目は1つも足さない。
ただし次の保存で `world_data.json` に落ちるので、MOD を外しても残る。
`index` が既にそこまで進んでいる以上、整合する方向のずれではある。
`902_` の NPC 生成と同じ性質で、README の「MOD を消せば完全に元通り」からは外れる。
"""

import copy

from instantale_modloader import frames, ui

LOG_BASENAME = "new_shop_data.log"

# 対象。`self` 以外の引数は取らない（実機で確認。同梱の `DOC.md` §3）。
TARGET = "__main__:ShoppingStartManagerRemake.shopping_start_method_1"

# 素データの鍵（GAME.md §2.7 の `areas[id].nodes[nid].facilities[fid]`）。
AREAS = "areas"
NODES = "nodes"
FACILITIES = "facilities"
NPCS = "npcs"
OWNER = "owner"

# 心当たりの辞書（`app` の属性名）。
# これは決め打ちではなく、先に見る順序。この後に直下を舐める。
HOLDER_NAMES = ("save_data_dict", "world_dict")

# 1回の来店でやり直す上限。
# 施設1つと主1人で足りるはずだが、連鎖したときのために少し余裕を持たせる。
MAX_FILLS = 5


def _ident(value):
    """id を辞書の鍵の形（文字列）に揃える。読めなければ `None`。"""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value)
    return text or None


def _tables(app):
    """素データの辞書を `[(どこにあるか, 辞書), ...]` で全部返す。

    素データと見なす条件は `areas` を持つ辞書であること1つだけ。
    `npcs` は主を写すのに使うが、在ることを条件にしない。

    見るのは `app` と `app.world` の直下。
    """
    found, seen = [], set()

    def add(label, value):
        if not isinstance(value, dict) or id(value) in seen:
            return
        if not isinstance(value.get(AREAS), dict):
            return
        seen.add(id(value))
        found.append((label, value))

    for label, owner in (("app", app),
                         ("app.world", frames.attr(app, "world", None))):
        if owner is None:
            continue
        for name in HOLDER_NAMES:
            add(label + "." + name, frames.attr(owner, name, None))
        try:
            members = list(vars(owner).items())
        except Exception:
            members = []
        for name, value in members:
            add(label + "." + name, value)
    return found


def _walk(table, area_id, node_id, facility_id):
    """素データを id で辿る。`(エリア, ノード, 施設)`。欠けた段から先は `None`。"""
    area = table[AREAS].get(area_id)
    if not isinstance(area, dict):
        return None, None, None
    nodes = area.get(NODES)
    node = nodes.get(node_id) if isinstance(nodes, dict) else None
    if not isinstance(node, dict):
        return area, None, None
    facilities = node.get(FACILITIES)
    facility = (facilities.get(facility_id)
                if isinstance(facilities, dict) else None)
    return area, node, facility if isinstance(facility, dict) else None


def _route_of(table, facility_id):
    """その辞書の中で施設 id を探す。`(エリア id, ノード id)`。無ければ `(None, None)`。"""
    areas = table.get(AREAS)
    if not isinstance(areas, dict):
        return None, None
    for area_id, area in areas.items():
        if not isinstance(area, dict):
            continue
        nodes = area.get(NODES)
        if not isinstance(nodes, dict):
            continue
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            facilities = node.get(FACILITIES)
            if isinstance(facilities, dict) and facility_id in facilities:
                return str(area_id), str(node_id)
    return None, None


def _facilities_of_node(node):
    """そのノードの下の施設。"""
    facilities = node.get(FACILITIES)
    if not isinstance(facilities, dict):
        return []
    return [item for item in facilities.values() if isinstance(item, dict)]


def _facilities_of_area(area):
    """そのエリアの下の施設を全部。"""
    nodes = area.get(NODES)
    if not isinstance(nodes, dict):
        return []
    found = []
    for node in nodes.values():
        if isinstance(node, dict):
            found.extend(_facilities_of_node(node))
    return found


def _copy_route(dst, src, area_id, node_id, facility_id):
    """欠けている一番上の段を `src` から `dst` へ写す。

    `(写した段の名前, 写した施設の一覧)`。写すものが無ければ `(None, [])`。

    判定は鍵が在るかどうかで行う（`_walk` の「dict として読めたか」ではない）。
    読めない値が入っているのは形が違うということで、
    そこへ書くと素データを上書きすることになる。
    埋めるのは空いている鍵だけ。
    """
    src_area, src_node, src_facility = _walk(src, area_id, node_id, facility_id)

    areas = dst[AREAS]
    if area_id not in areas:
        if src_area is None:
            return None, []
        copied = copy.deepcopy(src_area)
        areas[area_id] = copied
        return "area", _facilities_of_area(copied)

    area = areas[area_id]
    if not isinstance(area, dict) or src_node is None:
        return None, []
    nodes = area.get(NODES)
    if not isinstance(nodes, dict):
        if NODES in area:
            return None, []
        nodes = area[NODES] = {}
    if node_id not in nodes:
        copied = copy.deepcopy(src_node)
        nodes[node_id] = copied
        return "node", _facilities_of_node(copied)

    node = nodes[node_id]
    if not isinstance(node, dict) or src_facility is None:
        return None, []
    facilities = node.get(FACILITIES)
    if not isinstance(facilities, dict):
        if FACILITIES in node:
            return None, []
        facilities = node[FACILITIES] = {}
    if facility_id not in facilities:
        facilities[facility_id] = copy.deepcopy(src_facility)
        return "facility", [facilities[facility_id]]

    return None, []


def _copy_npc(dst, src, npc_id):
    """NPC を1人写す。写したら `True`。"""
    src_npcs, dst_npcs = src.get(NPCS), dst.get(NPCS)
    if not isinstance(src_npcs, dict) or not isinstance(dst_npcs, dict):
        return False
    if npc_id in dst_npcs:
        return False
    entry = src_npcs.get(npc_id)
    if not isinstance(entry, dict):
        return False
    dst_npcs[npc_id] = copy.deepcopy(entry)
    return True


def _copy_owners(dst, src, facilities):
    """写した施設の主を `npcs` へ揃える。写した主の id を返す。"""
    copied = []
    for facility in facilities:
        owner = _ident(facility.get(OWNER))
        if owner is not None and _copy_npc(dst, src, owner):
            copied.append(owner)
    return copied


def _spread(write, tables, src_label, src, area_id, node_id, facility_id, what):
    """供給元の経路を、持っていない辞書全部へ写す。1つでも写したら `True`。"""
    route = "area={} node={} facility={}".format(
        area_id or "-", node_id or "-", facility_id or "-")
    touched = False
    for label, table in tables:
        if table is src:
            continue
        stage, facilities = _copy_route(table, src, area_id, node_id,
                                        facility_id)
        if stage is None:
            continue
        owners = _copy_owners(table, src, facilities)
        touched = True
        write("copied the {} into {} from {} (key read as a {}; {}): "
              "{} facility/facilities, {} owner(s){}".format(
                  stage, label, src_label, what, route, len(facilities),
                  len(owners), " " + ",".join(owners) if owners else ""))
    return touched


def _fill_as_facility(write, tables, missing):
    """キーを施設 id と見て写す。"""
    for label, table in tables:
        area_id, node_id = _route_of(table, missing)
        if area_id is not None:
            return _spread(write, tables, label, table, area_id, node_id,
                           missing, "facility")
    return False


def _fill_as_node(write, tables, missing):
    """キーをノード id と見て写す。どのエリアの下かは持っている辞書から探す。"""
    for label, table in tables:
        for area_id, area in table[AREAS].items():
            if not isinstance(area, dict):
                continue
            nodes = area.get(NODES)
            if isinstance(nodes, dict) and missing in nodes:
                return _spread(write, tables, label, table, str(area_id),
                               missing, None, "node")
    return False


def _fill_as_area(write, tables, missing):
    """キーをエリア id と見て写す。"""
    for label, table in tables:
        if isinstance(table[AREAS].get(missing), dict):
            return _spread(write, tables, label, table, missing, None, None,
                           "area")
    return False


def _fill_as_npc(write, tables, missing):
    """キーを NPC id と見て写す。"""
    for label, table in tables:
        npcs = table.get(NPCS)
        if not isinstance(npcs, dict) or missing not in npcs:
            continue
        touched = False
        for other_label, other in tables:
            if other is table:
                continue
            if _copy_npc(other, table, missing):
                touched = True
                write("copied npc {} into {} from {}".format(
                    missing, other_label, label))
        return touched
    return False


def _fill(write, app, missing):
    """引き損なったキーを、持っている辞書から持っていない辞書へ写す。

    埋めたら `True`。

    **キーがエリア・ノード・施設・NPC のどれなのかは分からない。**
    採番は種類ごとに分かれていて（`index` が `area` / `node` / `facility` /
    `npc` / `item` / `quest` の6本）、同じ番号が別の種類に居る。
    実際、ノードごと欠けている店ではゲームは**ノード id** で落ちた。

    そこで見立てを1つに絞らず、4つとも試して当たったものを全部写す。
    余分に写しても、同じ id に同じ値が入るだけで害が無い
    （`902_` が置き場所を全部に書くのと同じ理由）。
    """
    tables = _tables(app)
    if len(tables) < 2:
        return False
    touched = False
    for fill in (_fill_as_area, _fill_as_node, _fill_as_facility,
                 _fill_as_npc):
        if fill(write, tables, missing):
            touched = True
    return touched


def _reach(table, missing):
    """その辞書がキーを持っているか。4つの見立てを全部見る。記録用の1行。"""
    if missing is None:
        return "no key to look up"
    found = []
    if isinstance(table[AREAS].get(missing), dict):
        found.append("area")
    for area_id, area in table[AREAS].items():
        if not isinstance(area, dict):
            continue
        nodes = area.get(NODES)
        if isinstance(nodes, dict) and missing in nodes:
            found.append("node under area {}".format(area_id))
            break
    area_id, node_id = _route_of(table, missing)
    if area_id is not None:
        found.append("facility at area={} node={}".format(area_id, node_id))
    npcs = table.get(NPCS)
    if isinstance(npcs, dict) and missing in npcs:
        found.append("npc")
    return ", ".join(found) if found else "not found"


def _census(write, app):
    """`app` と `app.world` が持っている辞書を全部書き出す。

    素データが集まらなかったときに、名前から推測せずに置き場所を探すため
    （TECH.md §6.3「属性は名前で推測せず `vars()` を全部出す」）。
    """
    for label, owner in (("app", app),
                         ("app.world", frames.attr(app, "world", None))):
        if owner is None:
            write("census: {} is None".format(label))
            continue
        write("census: {} is {}".format(label, type(owner).__name__))
        try:
            members = sorted(vars(owner).items(), key=lambda pair: pair[0])
        except Exception as exc:
            write("census: cannot read vars({}): {!r}".format(label, exc))
            continue
        for name, value in members:
            if not isinstance(value, dict) or not value:
                continue
            keys = sorted(str(key) for key in value)
            write("census: {}.{} = dict({}) {}{}".format(
                label, name, len(value), keys[:10],
                " ..." if len(keys) > 10 else ""))


def _runtime_note(write, app, missing):
    """組み上がった側の様子を書く（記録だけ。何も直さない）。

    素データを埋めても直らないときに、
    引かれているのが実行時の名簿なのかを見分ける手がかりになる。
    """
    try:
        areas = ui.world_areas(app)
        area = ui.current_area(app)
        facility, node = ui.find_facility(area, missing)
        write("runtime: world_areas={} current_area={} find_facility({})={}"
              .format(len(areas) if isinstance(areas, dict) else "?",
                      ui.area_id_of(area) or "None", missing,
                      "found at node {}".format(frames.attr(node, "id", "?"))
                      if facility is not None else "not found"))
        player = frames.attr(app, "player", None)
        location = frames.attr(player, "location", None)
        write("runtime: player.location = {} (id={}) current_node={} "
              "current_area={}".format(
                  type(location).__name__, frames.attr(location, "id", location),
                  frames.attr(frames.attr(player, "current_node", None), "id",
                              "?"),
                  frames.attr(frames.attr(player, "current_area", None), "id",
                              "?")))
    except Exception as exc:
        write("runtime: could not read the live world: {!r}".format(exc))


def _diagnose(write, app, missing, error=None):
    """埋められなかった、あるいは埋めても引けなかったときの記録。

    版1はここが無く、実機で1行も残らなかった。
    ここが唯一の記録の出口なので、名前から推測せず置き場所を並べる。

    `missing` は `None` のこともある（`KeyError` 以外で落ちた場合）。
    そのときも `census` と組み上がった側は同じだけ要る。
    """
    write("--- the shop did not open ({}) ---".format(
        "missing key {!r}".format(missing) if missing is not None
        else type(error).__name__ if error is not None else "no key"))
    tables = _tables(app)
    if not tables:
        write("no raw-data table found (nothing with a dict under {!r})".format(
            AREAS))
    for label, table in tables:
        write("table {} [{}] areas={} npcs={}".format(
            label, _reach(table, missing), len(table[AREAS]),
            len(table[NPCS]) if isinstance(table.get(NPCS), dict) else "none"))
    _census(write, app)
    _runtime_note(write, app, missing)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # 同じ内容は1度しか書かない（売買のたびに呼ばれるため）。
    # 注入し直すと作り直される。
    said = set()

    def once(key, message):
        if key in said:
            return
        said.add(key)
        write(message)

    @ctx.wrap(TARGET, safe=True)
    def shopping_start_method_1(orig, self, *args, **kwargs):
        app = frames.attr(self, "app", None)
        if app is None:
            app = ui.find_app()

        filled = []
        for attempt in range(MAX_FILLS + 1):
            error = None
            try:
                return orig(self, *args, **kwargs)
            except Exception as exc:
                # `except` の中で `orig` を呼び直すと例外が連鎖して
                # クラッシュ記録が読みにくくなるので、ここでは控えるだけ。
                #
                # `KeyError` に絞って捕まえない。
                # 素データの形が想定と違えば別の落ち方もするし、
                # 埋められないのは同じでも、記録は同じだけ要る。
                error = exc

            missing = (_ident(error.args[0])
                       if isinstance(error, KeyError) and error.args else None)
            done = False
            if app is None:
                once("no-app", "skip: no app on the manager and none running")
            elif missing is None:
                once("no-key", "skip: {} carried no key to fill in: {!r}".format(
                    type(error).__name__, error))
            elif missing in filled:
                # 埋めたのに同じ所で落ちる ＝ ゲームは別の場所を引いている。
                once("again:" + missing,
                     "filled {!r} but the game still cannot find it".format(
                         missing))
            elif attempt >= MAX_FILLS:
                once("too-many:" + missing,
                     "gave up after {} fill(s)".format(len(filled)))
            else:
                try:
                    done = _fill(write, app, missing)
                except Exception:
                    ctx.log_exc("new shop data: could not fill in {!r}".format(
                        missing))
                if not done:
                    once("cannot:" + missing,
                         "nothing to copy for {!r}".format(missing))

            if not done:
                if app is not None:
                    key = "diag:" + (missing or type(error).__name__)
                    if key not in said:
                        said.add(key)
                        try:
                            _diagnose(write, app, missing, error)
                        except Exception:
                            ctx.log_exc("new shop data: the diagnosis itself "
                                        "failed")
                raise error

            filled.append(missing)
            ctx.log("new shop data: filled in {!r} for the shop; see out/{}"
                    .format(missing, LOG_BASENAME))

    ctx.log("new shop data: shops built while playing will be filled in from "
            "the save side; log goes to out/{}".format(LOG_BASENAME))
