# -*- coding: utf-8 -*-
r"""別の世界から書き出した NPC を、この世界のロード時に1体ずつ入れる。

書き出しと予約はゲームの外の画面（`tool.py`）でやる。
ここがするのは**予約された世界をロードしたときに、その予約を果たすこと**だけ。

##### いつ入れるか

`InstantaleApp.load_game_new` / `start_game` を包み、**戻った後**に1回。
`120_` と `110_` が名簿を控え直しているのと同じ地点で、
そこでは素データも施設も揃っている（`224_probe_npc_carryover` が地点ごとに数える）。

予約は `state/npc_carryover/pending.json` の `status` で1回きりになる。
セーブには独自キーを足さない（MOD を外しても壊れないため）。

##### 置いただけでは残らない

この MOD が触るのは**動いているゲームの中の辞書**で、
ファイルとしてのセーブが書かれるのはプレイヤーが保存したとき。
ロードしてそのままゲームを終えると、置いた NPC は消える。

> 実機（2026-08-30 の3回目）: 素データは
> `save_data_dict['npcs']` に入ったが、保存せずに終えたので
> `savedata.json` は 43人のままだった。

予約を「果たした」ことにするのは置いた瞬間だが、
**保存されたかどうかは別に控える**（`saved`）。
次のロードで「`placed` なのに `saved` が無く、その NPC も世界に居ない」なら、
その予約は果たされていない ― `pending` に戻して置き直す。

保存まで済んだ予約（`saved` 有り）は二度と触らない。
後からセーブエディタでその NPC を消しても、勝手には戻さない。

##### 誰をどこへ

置き先はダンジョン以外のエリアから引く（実セーブの `size` は
`town` / `village` / `city` / `dungeon` で、ヴェスティアでは 54個中 45個がダンジョン）。
その中にギルドがあればギルド、無ければ宿。
旅の者が流れて来た、という位置づけなので `adventurer_npcs` に載せる。

##### 名前がぶつかったら見送る

検査の主役は予約する画面のほう（ゲームを起動する前に分かる）。
ここに残してあるのは、**予約してからロードするまでの間に生まれた衝突**
― その世界を遊んでいる間にゲームや `320_` が同じ名前の NPC を作る場合 ―
を黙って通さないため。
持ち込む NPC は固有の人物なので勝手に改名しない。
見送ったことは `pending.json` に理由ごと残し、ゲーム内にも1度出す
（黙って出てこないとバグに見える）。

##### 世界に残る

作った NPC はゲーム自身の `npcs` 項目なので、**MOD を外しても消えない**。
`320_` と同じ性質で、DOC.md に明記してある。
"""

import datetime
import io
import json
import os
import random
import sys

from instantale_modloader import frames, ids, state as loader_state, ui
from instantale_modloader.npcs import make_npc

from . import carryover

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
INHERIT_MEMORY = True         # 311_ / 403_ の記録を新しい世界へ写す
INHERIT_RELATIONSHIP = True   # セーブの relationship
INHERIT_LIFE_LOG = True       # セーブの life_log
ANNOUNCE = True               # 到着と見送りをゲーム内の文で知らせる

# ---- 設定にしない定数 ----------------------------------------------------
LOG_BASENAME = "npc_carryover.log"

#: ロードの入口。新規と続きの両方（名前では決められない。GAME.md §1.3）。
LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",
                "__main__:InstantaleApp.start_game")

#: 置けないエリアの `size`。
DUNGEON_SIZE = "dungeon"

#: 初対面に戻すときの関係（実セーブの語彙。`320_` と同じ形）。
FRESH_RELATIONSHIP = {"player": {"affinity": 0,
                                 "affinity_text": ["警戒心がある"],
                                 "relationship": ["初対面"],
                                 "conversation_count": 0}}

#: 記憶の置き場（`311_` と `403_` の `state/` のフォルダ名）と zip の中の鍵。
MEMORY_STORES = (("npc_profiles", "profile"),
                 ("npc_social_memory", "social"))

#: ゲームの保存。ここを通ったら、置いた NPC はファイルにも残る。
SAVE_TARGET = "__main__:InstantaleApp.save_game"

ARRIVED = "{name}が旅の末に{where}へ流れて来たらしい。"
SKIPPED = "{name}はこの世界に同名の人物が居るため現れなかった。"

#: 施設の名前が読めなかったときに `{where}` の後ろへ入れる語。
#: 鍵は `carryover.PLACEABLE_TYPES`。
#: 名前が在るならそちらを出す（「灰の交易都市の灯り亭」であって
#: 「灰の交易都市の宿」ではない）。
KIND_WORDS = {"guild": "ギルド", "inn": "宿"}

#: 世代をまたいで持つものの置き場（TECH.md §3.5）。
#:
#: `apply()` は1プロセスで何度も呼ばれる。
#: 閉包に持たせると、注入し直した瞬間に前の世代の中身が消える。
#: 実機の1回目がまさにその形だった ―
#: ロード（14:03:37）で言付けを積んだ後、画面が出る前に再注入（14:03:39）が
#: 走り、新しい世代の空のリストが読まれた（`out/modloader.log`）。
#: 到着の知らせは**ロードの後・最初の選択肢の組み直し**まで持ち越すものなので、
#: その間に世代が変わっても残る場所に置く。
STORE_ATTR = "_instantale_npc_carryover"


def _store():
    """世代をまたぐ入れ物。プロセスに1つ。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"words": []}
        setattr(sys, STORE_ATTR, store)
    return store


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    # `ctx` の値は `apply()` の間だけ（フック実行時には読めない）。
    # 場所は今のうちに控える。
    state_dir = ctx.state_dir
    # ロードの後、最初に選択肢が組み直される瞬間まで持ち越す言付け。
    # 閉包ではなくプロセスに持たせる（注入し直しを跨ぐ。TECH.md §3.5）。
    pending_words = _store()["words"]

    # ================================================== 置き先を選ぶ
    def placeable_spots(app):
        """ダンジョン以外のエリアにあるギルドと宿。

        `[(エリア, エリアid, 施設id, 種別)]`。並びは毎回同じにする
        （引くのは乱数だが、候補そのものが実行のたびに入れ替わらないように）。
        """
        found = []
        for area_id, area in (ui.world_areas(app) or {}).items():
            if str(frames.attr(area, "size", "")) == DUNGEON_SIZE:
                continue
            for node in ui.nodes_of(area):
                for key, facility in ui.facilities_of(node).items():
                    kind = ui.facility_type_of(facility)
                    if kind in carryover.PLACEABLE_TYPES:
                        found.append((area, str(area_id), str(key), kind,
                                      facility))
        found.sort(key=lambda row: (int(row[1]) if row[1].isdigit() else 0,
                                    row[3], row[2]))
        return found

    def pick_spot(app):
        """置き先を1つ引く。土地を引いてから、その土地のギルドか宿を引く。

        施設から直に引かないのは、宿を2つ持つ土地があるため
        （実データ 33件中2件）。そこだけ宿が2倍出ることになる。
        土地 → 種別 → 施設 の順なら、**どの土地でもギルドと宿が半々**になる。

        旅の者としてやって来る設定なので、宿に居ても不自然ではない
        （ギルドを優先していた版は、素のデータでは宿が一度も選ばれなかった。§9.1）。
        """
        spots = placeable_spots(app)
        if not spots:
            return None
        areas = sorted({spot[1] for spot in spots})
        area_id = random.choice(areas)
        here = [spot for spot in spots if spot[1] == area_id]
        kinds = sorted({spot[3] for spot in here})
        kind = random.choice(kinds)
        return random.choice([spot for spot in here if spot[3] == kind])

    def where_text(area, facility, kind):
        """到着の知らせに書く場所。「〈エリア名〉の〈施設名〉」。

        施設の名前を出すのは、種別の語（「ギルド」）だけだと
        その町にギルドが1つでも「どこの」が伝わらないため。
        名前を持たない施設も居るので、読めなければ種別の語で埋める。
        """
        where = frames.attr(area, "name", "") or "どこかの町"
        spot = frames.attr(facility, "name", "")
        if not isinstance(spot, str) or not spot.strip():
            spot = "ギルド" if kind == carryover.PLACEABLE_TYPES[0] else "宿"
        return "{}の{}".format(where, spot)

    # ================================================== 項目を整える
    def reassign_items(app, fields):
        """持ち物の id を置き先の台帳で採り直し、装備の参照も付け替える。

        元の世界の id のままだと、ゲームが `index['item']` から配り直す id と
        衝突して上書きで消える（セーブエディタが `ReassignItemIds` で
        同じことをしている）。
        """
        inventory = fields.get("inventory")
        if not isinstance(inventory, dict) or not inventory:
            return 0
        moved = {}
        fresh = {}
        for old_key, item in inventory.items():
            new_key = ids.claim(app, "item")
            moved[str(old_key)] = new_key
            fresh[new_key] = item
        fields["inventory"] = fresh
        equipments = fields.get("equipments")
        if isinstance(equipments, dict):
            for slot, value in list(equipments.items()):
                if isinstance(value, str) and value in moved:
                    equipments[slot] = moved[value]
        return len(moved)

    def place_images(package, world, name):
        """zip の画像を置き先の世界へ展開する。`{鍵: 新しいパス}` を返す。

        置き場は `worlds/<世界>/characters/<名前>/`（`image_src` が指す先）。
        書けなければ空を返し、`image_src` は全部 None にする
        （元の世界の絶対パスを残すと、その世界を消した瞬間に切れる）。
        """
        if not package.images:
            return {}
        folder = carryover.characters_dir(world, name)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            write("images: cannot make {}: {}".format(folder, exc))
            return {}
        written = {}
        for entry, blob in package.images.items():
            # zip の中の名前は信頼しない（`read_package` が既にファイル名だけに
            # 落としているが、書く直前にもう一度確かめる）。
            safe = os.path.basename(entry)
            if not safe:
                continue
            path = os.path.join(folder, safe)
            try:
                with io.open(path, "wb") as fh:
                    fh.write(blob)
                written[safe] = path
            except OSError as exc:
                write("images: cannot write {}: {}".format(path, exc))
        return written

    def rewrite_image_src(fields, written):
        """`image_src` の各値を、展開した先の絶対パスへ貼り替える。

        元は絶対パス（実セーブで確認）なので、ファイル名だけを取って
        置き先のフォルダに繋ぎ直す（エディタの `RewriteImageSrc` と同じ）。
        """
        src = fields.get("image_src")
        if not isinstance(src, dict):
            return
        for key, value in list(src.items()):
            if not isinstance(value, str) or not value:
                src[key] = None
                continue
            src[key] = written.get(os.path.basename(value.replace("\\", "/")))

    def prepare(app, package, inherit, world):
        """`make_npc` に渡す項目を作る。`(項目, config)`。

        並び順は `make_npc` が守る（`NEW_NPC_TEMPLATE` の33項目を持っていて、
        既にある鍵への代入では位置が動かない）。
        ここでするのは値の入れ替えだけ。
        """
        fields = json.loads(json.dumps(package.npc))     # 深い複製
        config = fields.pop("config", None)
        fields.pop("id", None)
        # 場所は `make_npc` が置き先で埋める。
        # `location`（いま居る場所）はエディタと同じく空に戻す。
        fields["location"] = {"area": None, "node": None, "facility": None}
        fields.pop("current_area", None)
        fields.pop("current_location", None)
        fields.pop("initial_location", None)
        fields["display_position_in_battle"] = None
        if not inherit.get("relationship", INHERIT_RELATIONSHIP):
            fields["relationship"] = json.loads(json.dumps(FRESH_RELATIONSHIP))
        if not inherit.get("life_log", INHERIT_LIFE_LOG):
            fields["life_log"] = []
        moved = reassign_items(app, fields)
        written = place_images(package, world, fields.get("name") or "")
        rewrite_image_src(fields, written)
        write("    prepared: items={} images={}".format(moved, len(written)))
        return fields, config if isinstance(config, dict) else {}

    # ================================================== 名簿へ載せる
    def enroll(app, area, area_id, npc_id):
        """`Area.adventurer_npcs` へ足す。実行時とセーブ側の両方。

        セーブの形＝実行時の形ではない（GAME.md §2.7）ので、
        素データ側の同名リストにも心当たりを全部見て書く（`320_` の `enroll`）。
        """
        wrote = []
        roster = frames.attr(area, "adventurer_npcs", None)
        if isinstance(roster, list) and npc_id not in roster:
            roster.append(npc_id)
            wrote.append("area")
        for label, root in (("world_dict", getattr(app, "world_dict", None)),
                            ("save_data_dict", getattr(app, "save_data_dict", None))):
            if not isinstance(root, dict):
                continue
            holders = [root]
            inner = root.get("world_data")
            if isinstance(inner, dict):
                holders.append(inner)
            for holder in holders:
                areas = holder.get("areas")
                entry = areas.get(area_id) if isinstance(areas, dict) else None
                raw = entry.get("adventurer_npcs") if isinstance(entry, dict) else None
                if isinstance(raw, list) and raw is not roster and npc_id not in raw:
                    raw.append(npc_id)
                    wrote.append(label)
        write("    enroll: {} -> adventurer_npcs of area {} via {}".format(
            npc_id, area_id, wrote or "nothing (roster not found)"))
        return bool(wrote)

    # ================================================== 記憶を写す
    def carry_memories(package, world, npc_id):
        """`311_` / `403_` の記録を、置き先の世界のファイルへ新しい id で書く。

        書き方は相手と同じ「隣に作ってから差し替える」。
        相手が入っていなければ何もしない（空のフォルダを作らない。TECH.md §3.11）。
        """
        done = []
        for dirname, key in MEMORY_STORES:
            record = package.extra.get(key)
            if not isinstance(record, dict):
                continue
            folder = os.path.join(state_dir, dirname)
            if not os.path.isdir(folder):
                continue                # その MOD を入れていない
            path = os.path.join(folder, loader_state.world_filename(world))
            try:
                with io.open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data[str(npc_id)] = record
            try:
                tmp = path + ".writing"
                with io.open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
                done.append(dirname)
            except OSError as exc:
                write("    memory: cannot write {}: {}".format(path, exc))
        if done:
            write("    memory: carried {} for {}".format(", ".join(done), npc_id))
        return done

    # ================================================== 予約を果たす
    def landed(app, npc_id):
        """作った NPC がゲームの保存する辞書に入ったか。

        `save_data_dict['npcs']` が本体（GAME.md §2.23）。
        `world_dict` にしか入っていないと、遊んでいる間は居るのに
        セーブには残らない。
        """
        save = getattr(app, "save_data_dict", None)
        npcs = save.get("npcs") if isinstance(save, dict) else None
        if not isinstance(npcs, dict):
            return True          # そもそも見えない形。判定しない
        return str(npc_id) in npcs

    def names_in_world(app):
        """いま世界に居る名前。素データから採る（実行時の名簿より確か）。"""
        from instantale_modloader.npcs import save_npcs
        return {npc.get("name") for npc in save_npcs(app).values()
                if isinstance(npc, dict) and npc.get("name")}

    def bring_in(app, row, world):
        """予約1件を果たす。`(果たしたか, 言付け)`。"""
        path = carryover.package_path(state_dir, row)
        package = carryover.read_package(path)
        if package is None:
            row["status"] = carryover.SKIPPED
            row["reason"] = "zip を読めない"
            write("  {}: cannot read {}".format(row.get("name"), path))
            return False, ""
        name = package.name
        if name in names_in_world(app):
            row["status"] = carryover.SKIPPED
            row["reason"] = "同名の人物が居る"
            write("  {}: skipped; the world already has that name".format(name))
            return False, SKIPPED.format(name="「{}」".format(name))

        spot = pick_spot(app)
        if spot is None:
            write("  {}: no place to put them (no guild or inn outside "
                  "a dungeon)".format(name))
            return False, ""            # 予約は `pending` のまま残す
        area, area_id, facility_id, kind, facility = spot

        inherit = row.get("inherit") if isinstance(row.get("inherit"), dict) else {}
        fields, config = prepare(app, package, inherit, world)
        npc_id = make_npc(app, fields, area_id, facility_id,
                          config=config, write=write)
        if npc_id is None:
            write("  {}: make_npc failed".format(name))
            return False, ""
        if not landed(app, npc_id):
            # ここに落ちたら、世界には居るのにセーブされない
            # （`save_data_dict['npcs']` がゲームの保存する側。GAME.md §2.23）。
            # 黙って通すと、次にゲームを開いたとき居ないことで初めて気づく。
            write("  WARN {} ({}) is not in save_data_dict['npcs']; "
                  "the game will not save them".format(name, npc_id))
            ctx.log("npc carryover: {} was placed but is missing from the save "
                    "data; see out/{}".format(name, LOG_BASENAME), level="WARN")
        enroll(app, area, area_id, npc_id)
        if inherit.get("memory", INHERIT_MEMORY):
            carry_memories(package, world, npc_id)

        row["status"] = carryover.PLACED
        row["npc_id"] = npc_id
        row["placed_area"] = area_id
        row["placed_facility"] = facility_id
        write("  {}: placed as {} at area {} / facility {} ({})".format(
            name, npc_id, area_id, facility_id, kind))
        return True, ARRIVED.format(name="「{}」".format(name),
                                    where=where_text(area, facility, kind))

    def unsaved(app, rows, world):
        """置いたが保存されずに終わった予約。`pending` に戻した数を返す。

        見分け方は3つ揃ったとき ― `placed` で、`saved` の控えが無く、
        その NPC が世界に居ない。
        保存まで済んだ予約（`saved` 有り）は触らない
        （後から消したのはプレイヤーの意思）。
        """
        npcs = names_in_world(app)
        back, caught_up = 0, []
        for row in rows:
            if (row.get("status") != carryover.PLACED
                    or row.get("target_world") != world
                    or row.get("saved")):
                continue
            if row.get("name") in npcs:
                # 居る＝前の回の保存で残った。控えが遅れているだけなので追いつかせる
                # （`save_game` を包む前に置いた回がこの形になる）。
                row["saved"] = datetime.datetime.now().isoformat(timespec="seconds")
                caught_up.append(row.get("name"))
                continue
            write("  {}: placed last time but never saved; "
                  "putting the reservation back".format(row.get("name")))
            row["status"] = carryover.PENDING
            for key in ("npc_id", "placed_area", "placed_facility"):
                row.pop(key, None)
            back += 1
        if caught_up:
            write("  already in the world, marking as saved: {}".format(
                "、".join(str(name) for name in caught_up)))
        return back + len(caught_up)

    def run(app, world, label):
        """この世界に予約されているものを全部果たす。"""
        rows = carryover.load_pending(state_dir)
        returned = unsaved(app, rows, world)
        if returned:
            carryover.save_pending(state_dir, rows)
        mine = [row for row in rows
                if row.get("status") == carryover.PENDING
                and row.get("target_world") == world]
        if not mine:
            return
        write("== {} ({}): {} reservation(s)".format(world, label, len(mine)))
        words = []
        for row in mine:
            try:
                _done, said = bring_in(app, row, world)
            except Exception:
                ctx.log_exc("npc carryover: bringing one in failed")
                continue
            if said:
                words.append(said)
        carryover.save_pending(state_dir, rows)
        if ANNOUNCE and words:
            pending_words.extend(words)

    # ================================================== フック
    def make_load(target):
        label = target.rsplit(".", 1)[-1]

        @ctx.wrap(target, required=False, safe=True)
        def on_load(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                app = (self if getattr(self, "world_dict", None) is not None
                       else ui.find_app())
                if app is not None:
                    world = loader_state.world_key(app)
                    if world and world != loader_state.UNKNOWN_WORLD:
                        run(app, world, label)
                    else:
                        write("{}: the world has no name yet; "
                              "nothing brought in".format(label))
            except Exception:
                ctx.log_exc("npc carryover: the load hook failed")
            return result

        return on_load

    for target in LOAD_TARGETS:
        make_load(target)

    @ctx.wrap(SAVE_TARGET, required=False, safe=True)
    def save_game(orig, self, *args, **kwargs):
        """保存が済んだら、置いた予約に印を付ける。

        ここを通るまでは、置いた NPC はメモリの中にしか居ない。
        """
        result = orig(self, *args, **kwargs)
        try:
            app = (self if getattr(self, "save_data_dict", None) is not None
                   else ui.find_app())
            world = loader_state.world_key(app) if app is not None else ""
            rows = carryover.load_pending(state_dir)
            stamped = []
            for row in rows:
                if (row.get("status") == carryover.PLACED
                        and row.get("target_world") == world
                        and not row.get("saved")):
                    row["saved"] = datetime.datetime.now().isoformat(
                        timespec="seconds")
                    stamped.append(row.get("name"))
            if stamped:
                carryover.save_pending(state_dir, rows)
                write("saved: {} now live in {}".format(
                    "、".join(str(name) for name in stamped), world))
        except Exception:
            ctx.log_exc("npc carryover: cannot record the save")
        return result

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons",
              required=False, safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        # 言付けはロードの直後ではなく、画面が出来てから出す。
        if pending_words:
            words, pending_words[:] = list(pending_words), []
            for text in words:
                try:
                    self.add_text(text)
                except Exception:
                    ctx.log_exc("npc carryover: cannot say it in game")
        return orig(self, *args, **kwargs)

    ctx.log("npc carryover: installed (state: {})".format(
        carryover.carryover_dir(state_dir)))
