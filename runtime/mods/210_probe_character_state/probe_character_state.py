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

版3: 問いは決着済み（VERIFICATION_LOG.md §2.29・GAME.md §2.22）なので、見張りは残したままログと手間を絞った。

- 数えた人数を印と同じく `sys` に置いた。
  版2は人数を apply ごとの器に持っていて、注入し直すたびに「人数が変わった」と読んで
  census を丸ごと取り直していた（取り直し 326 回の大半がこれ）
- 人別の表は世界ごとに前回の census との差分だけを書く。
  列も参照・`is_dead`・`state`・`category`・`job` に絞った。
  版2は1回 40KB 前後の表を毎回全部書いていて、1か月で約19MB、このログの7割だった。
  サンプルの全属性はプロセスに1回
- `check_character_death` は上限を先に見て、写し取りを try に入れた。
  版2は上限の後も本体を呼ぶ前に毎回全属性を舐めていて、その部分は例外を握っていなかった。
  何も変わらなかった回は1行にした
- セーブ辞書の全文と `generate_npc` の記録に、プロセス単位の上限を付けた
"""

import datetime
import json
import sys

from instantale_modloader import frames, ui
from instantale_modloader.state import world_key

LOG_BASENAME = "character_state.log"

# 1プロセスに1回だけにするための印（`sys` に置く。TECH.md §3.6）。
CENSUS_MARK = "_instantale_probe_charstate_census"
# 最後に数えた人数（版3。apply ごとの器に持つと注入し直しで None に戻り、census を取り直していた）。
COUNT_MARK = "_instantale_probe_charstate_counted"
# 世界ごとの前回の人別の行 `{世界: {id: 行}}`（版3。差分だけを書くため）。
ROWS_MARK = "_instantale_probe_charstate_rows"
# サンプルの全属性を書いたか（版3。プロセスに1回）。
SAMPLE_MARK = "_instantale_probe_charstate_sample"
# セーブ辞書と `generate_npc` の記録数（版3。プロセス単位で数える）。
SAVES_MARK = "_instantale_probe_charstate_saves"
NPCS_MARK = "_instantale_probe_charstate_npcs"

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
# 版3でプロセス単位にした（版2は注入のたびに4体ぶん書いていた）。
MAX_SAVE_DUMPS = 4

# `generate_npc` を写す件数（プロセス単位）。
# 版2は上限が無く、毎回呼び出し元と npc_data の全文を書いていた。
MAX_NPC_DUMPS = 10

# 人別の表の行に載せる項目（版3。版2は `STATE_ATTRS` を全部載せていた）。
ROW_ATTRS = ("state", "category", "job")


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, stamp=False)
    # 上限つきの器。
    # 数えた人数・セーブ辞書の件数は `sys` に置く（版3。上の COUNT_MARK / SAVES_MARK）。
    state = {"deaths": 0, "quiet_keys_written": False}

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
        # 上限を先に見る（版3。版2は上限の後も本体の前で毎回全属性を舐めていた）。
        # 写し取りは try の中に置く。失敗しても本体はそのまま1回呼ぶ。
        character, keys, before = None, [], {}
        if state["deaths"] < MAX_DEATH_EVENTS:
            try:
                # 署名は決め打ちしない（リコンでは (self, index, character)）。
                for candidate in list(args) + list(kwargs.values()):
                    if frames.attr(candidate, "name") is not frames.MISSING:
                        character = candidate
                        break
                if character is not None:
                    keys = interesting_keys(character)
                    before = snapshot(character, keys)
            except Exception:
                character = None
                ctx.log_exc("character state probe: death snapshot failed")
        result = orig(self, *args, **kwargs)
        if character is None:
            return result
        try:
            state["deaths"] += 1
            changes = diff(before, snapshot(character, keys))
            if changes:
                write("\n[{}] check_character_death -> {}".format(
                    stamp(), frames.repr_value(result)))
                write("    character: {}".format(
                    frames.describe_instance(character)))
                write("    changed:")
                for line in changes:
                    write(line)
            else:
                # 何も変わらなかった回は1行（版3。版2は見張った項目の一覧まで毎回4行書いていて、
                # 記録の9割近くがこれだった）。項目の一覧は注入ごとに1回だけ出す。
                write("[{}] check_character_death -> {}  {}  changed: <nothing>".format(
                    stamp(), frames.repr_value(result),
                    frames.repr_value(frames.attr(character, "name"))))
                if not state["quiet_keys_written"]:
                    state["quiet_keys_written"] = True
                    write("    watched: {}".format(keys))
        except Exception:
            ctx.log_exc("character state probe: death record failed")
        return result

    # ------------------------------------------------------------------
    # セーブに残る形（`character_value` は保存された辞書そのもの）
    # ------------------------------------------------------------------
    @ctx.wrap("__main__:World.generate_character", required=False)
    def generate_character(orig, self, *args, **kwargs):
        # 件数はプロセス単位（版3。版2は注入のたびに鍵の一覧と4体ぶんの全文を書いていた）。
        # 上限の後は何も見ずに素通しにする。
        saves = getattr(sys, SAVES_MARK, 0)
        if saves >= MAX_SAVE_DUMPS:
            return orig(self, *args, **kwargs)
        try:
            value = None
            for candidate in list(args)[1:] + list(kwargs.values()):
                if isinstance(candidate, dict):
                    value = candidate
                    break
            if isinstance(value, dict):
                setattr(sys, SAVES_MARK, saves + 1)
                if saves == 0:
                    write("\n" + "=" * 72)
                    write("[{}] character save keys ({}):".format(
                        stamp(), len(value)))
                    write("    " + ", ".join(sorted(str(key) for key in value)))
                    # 名前に死亡の気配があるものを名指しする。
                    hits = [key for key in value
                            if any(hint in str(key).lower()
                                   for hint in STATE_HINTS)]
                    write("    state-ish keys: {}".format(hits or "<none>"))
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
        実際には遊んでいる間にも発火していた（版2までに約300回）ので、版3でプロセス単位の上限を付けた。
        """
        count = getattr(sys, NPCS_MARK, 0)
        if count >= MAX_NPC_DUMPS:
            return orig(*args, **kwargs)
        try:
            setattr(sys, NPCS_MARK, count + 1)
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
        setattr(sys, COUNT_MARK, len(characters))

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

        # 行はまとめて1回で書く（版3。版2は1行ごとにファイルを開いていて、1回の census で 170 回前後開いていた）。
        lines = ["", "#" * 72, "# {}  character census".format(stamp()), "#" * 72,
                 "characters={} facilities={} party={}".format(
                     len(characters), facilities_seen, sorted(party))]

        # サンプルの全属性はプロセスに1回（版3。版2は census のたびに 4KB 前後書いていた）。
        if not getattr(sys, SAMPLE_MARK, False):
            setattr(sys, SAMPLE_MARK, True)
            sample = next(iter(characters.values()))
            lines.append("watched keys: {}".format(interesting_keys(sample)))
            lines.append("\nvars(sample character) [{}]:".format(
                frames.describe_instance(sample)))
            for key, value in sorted(own_dict(sample).items()):
                lines.append("    {:<28} = {}".format(key, frames.repr_value(value)))

        # 人別の行は、この世界の前回の census と違う行だけを書く（版3）。
        # 前回が無ければ（この世界をこのプロセスで初めて数えたとき）全部書く。
        rows = {}
        unreferenced = []
        for index, (character_id, character) in enumerate(
                sorted(characters.items(), key=lambda kv: str(kv[0]))):
            if index >= MAX_CHARACTERS:
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
            values = snapshot(character, ROW_ATTRS)
            values[DEAD_FLAG] = frames.repr_value(flag_of(character, DEAD_FLAG))
            rows[cid] = "  {:<6} {:<24} {:<40} {}".format(
                cid,
                frames.repr_value(frames.attr(character, "name"))[:24],
                ", ".join(refs)[:40] or "<no reference>",
                "  ".join("{}={}".format(k, v) for k, v in sorted(values.items())))

        try:
            wkey = world_key(app)
        except Exception:
            wkey = "_"
        by_world = getattr(sys, ROWS_MARK, None)
        if not isinstance(by_world, dict):
            by_world = {}
            setattr(sys, ROWS_MARK, by_world)
        previous = by_world.get(wkey)
        by_world[wkey] = rows

        lines.append("\n--- per character ---")
        lines.append("  id / name / refs(owner,roster,resident,party) / "
                     "category is_dead job state")
        if previous is None:
            lines.append("  (first census of this world in this process: all rows)")
            lines.extend(rows[cid] for cid in rows)
        else:
            changed = [cid for cid in rows if previous.get(cid) != rows[cid]]
            gone = [cid for cid in previous if cid not in rows]
            lines.append("  (diff against the previous census of this world: "
                         "{} new/changed, {} gone, {} unchanged)".format(
                             len(changed), len(gone), len(rows) - len(changed)))
            lines.extend(rows[cid] for cid in changed)
            if gone:
                lines.append("  gone: {}".format(gone[:40]))
        if len(characters) > MAX_CHARACTERS:
            lines.append("    ... {} more (limit {})".format(
                len(characters) - MAX_CHARACTERS, MAX_CHARACTERS))
        write("\n".join(lines))

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

        lines = ["\n--- summary ---",
                 "  facility owners (removing these breaks the world): {}".format(
                     len(owners)),
                 "  listed in a facility roster: {}".format(len(rosters)),
                 "  area residents: {}".format(len(residents)),
                 "  referenced by nothing: {} {}".format(
                     len(unreferenced), unreferenced[:40]),
                 "  {}=True: {} {}".format(DEAD_FLAG, len(dead), dead[:40]),
                 "  {}=False/absent: {}".format(DEAD_FLAG, len(alive)),
                 "  dead but still referenced: {} {}".format(
                     len(dead_referenced), dead_referenced[:40])]
        if dead and not dead_referenced:
            lines.append("  -> the game drops the references itself when the flag goes up.")
        elif dead_referenced:
            lines.append("  -> the flag alone does NOT unlink them; the rosters keep")
            lines.append("     the id. Check in game whether they still show up as a")
            lines.append("     person you can talk to at that facility.")
        else:
            lines.append("  -> nobody is flagged dead yet; re-run this census after")
            lines.append("     one dies to get the comparison.")
        write("\n".join(lines))

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
                counted = getattr(sys, COUNT_MARK, None)
                if not isinstance(characters, dict):
                    pass
                elif counted is None:
                    # 印は立っているが人数が無い ＝ 版2以前がこのプロセスで数えた。
                    # 注入し直しただけなので取り直さず、今の人数を控えるだけにする（版3）。
                    setattr(sys, COUNT_MARK, len(characters))
                elif len(characters) != counted:
                    write("\n[{}] character count {} -> {}; re-running census"
                          .format(stamp(), counted, len(characters)))
                    census(force=True)
        except Exception:
            ctx.log_exc("character state probe: census retry failed")
        return result

    ctx.on_ready(census)

    ctx.log("character state probe: census + death diff go to out/{}".format(
        LOG_BASENAME))
