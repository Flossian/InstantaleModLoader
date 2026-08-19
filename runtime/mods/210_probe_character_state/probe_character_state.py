# -*- coding: utf-8 -*-
"""計測: NPC の「死んでいる」印と、世界の誰がその NPC を参照しているか。

##### 何を決めるための計測か

事件ものの MOD では、犯人役の NPC を最後に世界から退場させたい。
既存の NPC を使うと**店の主や住人が消えて世界が壊れる**ので、案は2つある。

1. 自前で NPC を生成して使い、終わったら退場させる
2. 死亡の印（`is_dead` に相当するもの）を立てれば参照されなくなる、という性質に乗る

2が本当なら1より遥かに安い。
だが `is_dead` という属性はリコンに出ていない。
リコンが拾うのは関数・クラス・モジュール定数だけで、
インスタンス属性は写らない（`Character.__init__` の引数には `state` / `status` /
`config` があり、死亡の印はこの中のどれかに入っている可能性が高い）。
名前を推測して探すと空振りするので（GAME.md §1.3 の実例）、実物を開いて確かめる。

##### 3つの問いに答える

| 問い | 見るところ |
|---|---|
| 死亡の印はどの項目か | `check_character_death` の前後で何が変わったかの差分 |
| セーブに残る形は | `World.generate_character` の `character_value`（保存された辞書そのもの） |
| 印を立てると参照されなくなるのか | 世界の全 NPC を舐めて、誰がどこから参照されているかの表 |

3つ目が要点。
印は「参照されなくなる」ことの原因ではなく、結果かもしれない。
つまりゲームが死亡時に施設の名簿からも外している可能性がある。
その場合、印だけ立てても名簿には残り続ける。
だから印の有無ではなく実際の参照の有無を数える。

既に死んだ NPC が世界に居れば、生きている NPC との差分がそのまま答えになる。
居なければ「生きている NPC はこう参照されている」という基準表が残るので、
1体退場させた後にもう一度取れば差分が読める。

##### 消すと壊れる NPC の一覧も出す

`Facility.owner` に載っている NPC は、消えると店に主が居なくなる。
この表は「どの NPC なら退場させても安全か」を選ぶのにそのまま使える。
依頼の `client_name` は実在 NPC と紐付いていない（GAME.md §2.9）ので、
依頼の側は気にしなくてよい。

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。
値は書かず、記録に失敗しても本体は必ず呼ぶ。
`check_character_death` の差分を取るために対象の属性を読むが、書かない。
"""

import datetime
import json
import sys

from instantale_modloader import frames, ui

LOG_BASENAME = "character_state.log"

# 1プロセスに1回だけにするための印（`sys` に置く。TECH.md §3.6）。
CENSUS_MARK = "_instantale_probe_charstate_census"

# 死亡の印が入っていそうな項目。
# ここに無くても構わない。
# 下の `interesting_keys` が実物の属性名から候補を拾い直す。
# 1回目の実機で、セーブ側の項目は 33 個と分かった（`generate_character` のダンプ）。
# **`is_dead` は無く**、状態らしきものは `state`（生きている NPC では空文字）と HP
# 3種だけ。
# だから死亡の印は `state` の中身である可能性が高い。
#: `category` と `job` は状態ではないが、
#: 個人か群衆かの見分けに要る（実機に
#: `混乱する村人たち` のような群衆の登場人物が居る）。
#: 事件の犯人役を選ぶ MOD が、何を手掛かりに弾けばよいかをここで見る。
STATE_ATTRS = ("state", "status", "config", "current_hp", "max_hp",
               "original_max_hp", "physical_integrity", "age", "days",
               "story_achievements", "category", "job")

# 属性名に含まれていたら「状態っぽい」と見なす語。
# 名前を決め打ちしないための網。
STATE_HINTS = ("dead", "death", "alive", "live", "state", "status", "hp",
               "integrity", "retire", "gone", "remove", "leave")

# 全 NPC を舐めるときの上限（大きい世界でも注入を待たせない）。
MAX_CHARACTERS = 400
MAX_AREAS = 40

# 死亡判定の記録の上限。
# 1回の戦闘ぶん追えれば十分。
MAX_DEATH_EVENTS = 60

# この件数までの辞書は値ごと出す（`config` を読むため。上の `snapshot`）。
MAX_INLINE_DICT = 8

# 死亡の印。
# 1回目の実機で `config` の中に在ることが分かった（`config` = level_of_detail /
# is_player / is_dead / difficulty_level）。
# 名前が変わっても census は動く（`interesting_keys` が拾い直す）。
DEAD_FLAG = "is_dead"

# セーブ辞書のダンプ件数。
# 数体ぶんあれば項目の一覧は分かる。
MAX_SAVE_DUMPS = 4


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, stamp=False)
    # 上限つきの器。`counted` だけは None で始める。
    # 人数を一度も数えられていないときは、次の機会に数え直したい。
    state = {"deaths": 0, "saves": 0, "save_keys": None, "counted": None}

    def stamp():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def own_dict(obj):
        try:
            return dict(vars(obj))
        except Exception:
            return {}

    def as_json(value):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2, default=repr)
        except Exception:
            return repr(value)

    def interesting_keys(sample):
        """実物の属性名から「状態っぽいもの」を拾う。名前を決め打ちしない。"""
        keys = set(STATE_ATTRS)
        for key in own_dict(sample):
            low = str(key).lower()
            if any(hint in low for hint in STATE_HINTS):
                keys.add(key)
        return sorted(keys)

    def snapshot(character, keys):
        """比較のために状態だけ写す。読むだけで書かない。"""
        out = {}
        for key in keys:
            value = frames.attr(character, key)
            if value is frames.MISSING:
                continue
            # `config` は中身が本体。
            # 1回目の実機では `frames.repr_value` が辞書をキーだけに畳んでしまい、
            # `is_dead` という項目が在ることは分かっても真偽が読めなかった。
            # 小さい辞書は値ごと出す。
            if isinstance(value, dict) and len(value) <= MAX_INLINE_DICT:
                out[key] = "{" + ", ".join(
                    "{}={}".format(k, frames.repr_value(v))
                    for k, v in sorted(value.items())) + "}"
                continue
            out[key] = frames.repr_value(value)
        return out

    def flag_of(character, name):
        """`config` の中の真偽値を1つ読む。無ければ None。"""
        config = frames.attr(character, "config")
        if isinstance(config, dict):
            return config.get(name)
        return None

    def diff(before, after):
        lines = []
        for key in sorted(set(before) | set(after)):
            old = before.get(key, "<absent>")
            new = after.get(key, "<absent>")
            if old != new:
                lines.append("        {:<24} {} -> {}".format(key, old, new))
        return lines

    # ------------------------------------------------------------------
    # 死亡の瞬間に何が変わるか
    # ------------------------------------------------------------------
    @ctx.wrap("__main__:BattlePhaseManager.check_character_death", required=False)
    def check_death(orig, self, *args, **kwargs):
        # 署名は決め打ちしない（リコンでは (self, index, character)）。
        character = None
        for candidate in list(args) + list(kwargs.values()):
            if frames.attr(candidate, "name") is not frames.MISSING:
                character = candidate
                break

        keys = interesting_keys(character) if character is not None else []
        before = snapshot(character, keys) if character is not None else {}
        result = orig(self, *args, **kwargs)
        try:
            if character is not None and state["deaths"] < MAX_DEATH_EVENTS:
                state["deaths"] += 1
                after = snapshot(character, keys)
                changes = diff(before, after)
                write("\n[{}] check_character_death -> {}".format(
                    stamp(), frames.repr_value(result)))
                write("    character: {}".format(
                    frames.describe_instance(character)))
                if changes:
                    write("    changed:")
                    for line in changes:
                        write(line)
                else:
                    write("    changed: <nothing in the watched keys>")
                    write("    watched: {}".format(keys))
        except Exception:
            ctx.log_exc("character state probe: death record failed")
        return result

    # ------------------------------------------------------------------
    # セーブに残る形（`character_value` は保存された辞書そのもの）
    # ------------------------------------------------------------------
    @ctx.wrap("__main__:World.generate_character", required=False)
    def generate_character(orig, self, *args, **kwargs):
        try:
            value = None
            for candidate in list(args)[1:] + list(kwargs.values()):
                if isinstance(candidate, dict):
                    value = candidate
                    break
            if isinstance(value, dict):
                if state["save_keys"] is None:
                    state["save_keys"] = sorted(value)
                    write("\n" + "=" * 72)
                    write("[{}] character save keys ({}):".format(
                        stamp(), len(value)))
                    write("    " + ", ".join(state["save_keys"]))
                    # 名前に死亡の気配があるものを名指しする。
                    hits = [key for key in value
                            if any(hint in str(key).lower()
                                   for hint in STATE_HINTS)]
                    write("    state-ish keys: {}".format(hits or "<none>"))
                if state["saves"] < MAX_SAVE_DUMPS:
                    state["saves"] += 1
                    write("\n[{}] generate_character({})".format(
                        stamp(), frames.repr_value(args[0] if args else None)))
                    write(as_json(value))
        except Exception:
            ctx.log_exc("character state probe: save dump failed")
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------------
    # NPC を作って施設へ置く側（上流）
    # ------------------------------------------------------------------
    @ctx.wrap("save_area_json:generate_npc", required=False)
    def generate_npc(orig, *args, **kwargs):
        """MOD が NPC を作るときに真似る相手。

        `World.generate_character` は「辞書を受け取って Character にする」側で、
        その辞書を誰がどう組んだかは見えない。
        こちらは `(npc_data, world_dict, area_id, facility_id, job)` を受け取るので、
        置き場所の決まり方まで分かる。

        遊んでいて発火しない可能性もある（世界生成でしか呼ばれないなら）。
        掛け捨ての保険として置いておく。
        """
        try:
            write("\n" + "=" * 72)
            write("[{}] generate_npc(args={} kwargs={})".format(
                stamp(),
                [frames.repr_value(a) for a in args[1:]],
                {k: frames.repr_value(v) for k, v in kwargs.items()}))
            write("    from {}".format(frames.caller()))
            data = args[0] if args else kwargs.get("npc_data")
            if isinstance(data, dict):
                write("    npc_data keys ({}): {}".format(
                    len(data), sorted(data)))
                write(as_json(data))
        except Exception:
            ctx.log_exc("character state probe: generate_npc record failed")
        return orig(*args, **kwargs)

    # ------------------------------------------------------------------
    # 世界の全 NPC と、その参照元（`app` が要るので on_ready 側）
    # ------------------------------------------------------------------
    def census(force=False):
        """`force=True` は印を無視して取り直す（新しい NPC が生えた後用）。"""
        if getattr(sys, CENSUS_MARK, False) and not force:
            return
        app = ui.find_app()
        if app is None:
            return
        world = getattr(app, "world", None)
        characters = getattr(world, "characters", None)
        # 世界が載るまで黙って諦める。
        # `on_ready` は全 MOD の適用直後＝まだタイトル画面のことがあり、
        # 1回目の実機はここで
        # `world.characters is NoneType` と1行書いて終わっていた。
        # プレイヤーが何か押したときに `retry_census` がもう一度呼ぶ。
        if not isinstance(characters, dict) or not characters:
            return
        setattr(sys, CENSUS_MARK, True)
        state["counted"] = len(characters)

        # 参照元を集める。
        # **誰がどこから指されているか**が本題。
        owners = {}        # character_id -> [施設の説明]
        rosters = {}       # character_id -> [施設の説明]
        residents = {}     # character_id -> [エリア名]
        facilities_seen = 0

        try:
            areas = list(ui.world_areas(app).items())[:MAX_AREAS]
        except Exception:
            areas = []
        for area_id, area in areas:
            for value in (frames.attr(area, "resident_npcs"),):
                if isinstance(value, (list, tuple)):
                    for entry in value:
                        residents.setdefault(ui.element_id(entry), []).append(
                            str(area_id))
            for node in ui.nodes_of(area):
                for facility_id, facility in ui.facilities_of(node).items():
                    facilities_seen += 1
                    where = "{}/{} {}".format(
                        area_id, facility_id, ui.facility_type_of(facility))
                    owner = frames.attr(facility, "owner")
                    if owner not in (None, frames.MISSING, ""):
                        owners.setdefault(ui.element_id(owner), []).append(where)
                    roster = frames.attr(facility, "characters")
                    if isinstance(roster, (list, tuple)):
                        for entry in roster:
                            rosters.setdefault(ui.element_id(entry), []).append(where)

        try:
            party = set(ui.party_ids(app))
        except Exception:
            party = set()

        sample = next(iter(characters.values()))
        keys = interesting_keys(sample)

        write("\n" + "#" * 72)
        write("# {}  character census".format(stamp()))
        write("#" * 72)
        write("characters={} facilities={} party={}".format(
            len(characters), facilities_seen, sorted(party)))
        write("watched keys: {}".format(keys))
        write("\nvars(sample character) [{}]:".format(
            frames.describe_instance(sample)))
        for key, value in sorted(own_dict(sample).items()):
            write("    {:<28} = {}".format(key, frames.repr_value(value)))

        write("\n--- per character ---")
        write("  id / name / refs(owner,roster,resident,party) / state")
        unreferenced = []
        for index, (character_id, character) in enumerate(
                sorted(characters.items(), key=lambda kv: str(kv[0]))):
            if index >= MAX_CHARACTERS:
                write("    ... {} more (limit {})".format(
                    len(characters) - MAX_CHARACTERS, MAX_CHARACTERS))
                break
            cid = str(character_id)
            refs = []
            if cid in owners:
                refs.append("owner={}".format(owners[cid]))
            if cid in rosters:
                refs.append("roster x{}".format(len(rosters[cid])))
            if cid in residents:
                refs.append("resident={}".format(residents[cid]))
            if cid in party:
                refs.append("party")
            if not refs:
                unreferenced.append(cid)
            values = snapshot(character, keys)
            write("  {:<6} {:<24} {:<40} {}".format(
                cid,
                frames.repr_value(frames.attr(character, "name"))[:24],
                ", ".join(refs)[:40] or "<no reference>",
                "  ".join("{}={}".format(k, v) for k, v in sorted(values.items()))))

        # ★ ここが答えの出る場所。
        #
        # 「印を立てれば参照されなくなる」は2通りに読める。
        #   (a) 印を立てるとゲームが名簿からも外す   → 参照が消える
        #   (b) 名簿には残るが、読む側が印を見て飛ばす → 参照は残る
        # census が数えるのは実際の参照なので、死んだ NPC が名簿に
        # 残っていれば (a) は否定される。そのとき (b) かどうかは、その施設で
        # 話しかけられる相手にその NPC が出るかを目で見て確かめること
        # （名簿に居るのに選択肢に出なければ (b)）。
        dead, alive, dead_referenced = [], [], []
        for character_id, character in characters.items():
            cid = str(character_id)
            if flag_of(character, DEAD_FLAG):
                dead.append(cid)
                if cid in owners or cid in rosters or cid in residents:
                    dead_referenced.append(cid)
            else:
                alive.append(cid)

        write("\n--- summary ---")
        write("  facility owners (removing these breaks the world): {}".format(
            len(owners)))
        write("  listed in a facility roster: {}".format(len(rosters)))
        write("  area residents: {}".format(len(residents)))
        write("  referenced by nothing: {} {}".format(
            len(unreferenced), unreferenced[:40]))
        write("  {}=True: {} {}".format(DEAD_FLAG, len(dead), dead[:40]))
        write("  {}=False/absent: {}".format(DEAD_FLAG, len(alive)))
        write("  dead but still referenced: {} {}".format(
            len(dead_referenced), dead_referenced[:40]))
        if dead and not dead_referenced:
            write("  -> the game drops the references itself when the flag goes up.")
        elif dead_referenced:
            write("  -> the flag alone does NOT unlink them; the rosters keep")
            write("     the id. Check in game whether they still show up as a")
            write("     person you can talk to at that facility.")
        else:
            write("  -> nobody is flagged dead yet; re-run this census after")
            write("     one dies to get the comparison.")

    # 世界が載るのはタイトルで「つづきから」を押した後。
    # `on_ready` の時点ではまだ無いことがあるので、**プレイヤーが何か押すたびに試す**。
    # 印が立った後は数を1つ比べるだけなので、押下の経路を重くしない。
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def retry_census(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if not getattr(sys, CENSUS_MARK, False):
                census()
            else:
                # NPC が増えたら取り直す。
                # 新しく生えた NPC が施設の名簿に載るのか（`initial_location` を書いただけなのか）は、生えた後の
                # census でしか分からない。
                characters = getattr(getattr(self, "world", None),
                                     "characters", None)
                if (isinstance(characters, dict)
                        and len(characters) != state["counted"]):
                    write("\n[{}] character count {} -> {}; re-running census"
                          .format(stamp(), state["counted"], len(characters)))
                    census(force=True)
        except Exception:
            ctx.log_exc("character state probe: census retry failed")
        return result

    ctx.on_ready(census)

    ctx.log("character state probe: census + death diff go to out/{}".format(
        LOG_BASENAME))
