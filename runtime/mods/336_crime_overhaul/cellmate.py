# -*- coding: utf-8 -*-
"""同房の囚人。服役の始まりに新しく作った囚人が同じ房に入り、年ごとに親しくなる。仕様は DOC.md「同房の囚人」。

本人の決定（2026-10-05）:
- 既存の冒険者ではなく、新しく作る。服役の始まりに確率で現れる
- 牢の中では会話できる（ゲームの会話。年は進まない）。服役で1年過ごすたびに好感度が上がる
- 好感度が高ければ、脱獄の決行の戦闘に仲間として加勢する。勝つか逃げれば仲間のまま外へ。
  主人公が倒れればゲームオーバー（決行の決まりのまま）。囚人だけが倒れたら、ゲームが仲間の死として退場させる
- 主人公が牢を出たら（釈放・脱獄・処刑場からの脱出）、街のギルドの冒険者に加わり、雇える

作り方は `320_` と同じ（LLM に人物を書かせ、ローダの `npcs.make_npc` で世界に入れる。GAME.md §2.23）。
牢の場所は無いので、服役の間は街のギルドに置き、冒険者の一覧（`adventurer_npcs`）には載せない。
出所のときに `npcs.enroll` で一覧へ載せる（雇えるのは一覧に載った冒険者。仲間に入れるにも要る）。
作った直後にゲーム自身の `generate_npc_detail` で HP・スキル・立ち絵を埋める
（埋めないまま戦闘に出すと落ちる。GAME.md §2.23）。
控えは `state\\crime_overhaul\\<世界×主人公>.json` の `cellmates`（{id: {name, crime, area, guild, status, years}}）。
`status` は "jailed"（牢の中）/ "breaking"（決行の戦闘に加勢中）/ "party"（一緒に外へ出て仲間）/ "free"（ギルドの冒険者）/
"dead"（決行の戦闘で倒れた。ゲームは倒れた仲間を死なせる（`config['is_dead']`。GAME.md §2.22）。実機）。
"""
import sys

from instantale_modloader import confinement, frames, llm, modnpc, npcs, ui

from . import jailbreak

TALK_LABEL = "{name}と話す"
MARK = "mate:talk"
MANAGER_NAME = "mod_crime_cellmate"
COMPOSE_TIMEOUT = 90.0
COMPOSE_MAX_TOKENS = 900
NAME_MAX = 24
TAKEN_NAMES_MAX = 15
DIFFICULTY_MAX = 76                 # get_npc_employ_price の定義域（`320_` と同じ）
CONVERSATION_SPEC = "ConversationStartManager"
#: 作るときの関係の印（`relationship.player.relationship` の並び。ゲームは「家族」「同行中」などをここに足す）。
RELATION_TAG = "同房の囚人"
CATEGORIES = ("young man", "young woman", "teenage boy", "teenage girl",
              "middle-aged man", "middle-aged woman", "old man", "old woman")

ARRIVE_TEXT = "同じ房に、もう一人の囚人がいた。{name}。{crime}で捕まったという。"
YEAR_TEXT = "{name}と同じ房で一年を過ごした。（好感度 {before} → {after}）"
NO_ACTION_TEXT = "（牢の中では、話すことしかできない）"
#: 牢の中の会話から外すゲームの選択肢（雇う）。spec は `ConversationPhaseManager(app, '雇いたい')`。
HIRE_TEXT = "雇いたい"
CONFINED_TEXT = "牢の中で同房の囚人と話している"
ASSIST_TEXT = "{name}が隣で身を起こした。置いていくなと言わんばかりに、拳を固めている。"
ASSIST_FREE_TEXT = "{name}も共に牢を抜け出した。"
ASSIST_DEAD_TEXT = "{name}は看守たちの刃に倒れ、もう起き上がらなかった。"
RELEASE_TEXT = "{name}も、ほどなく牢を出たらしい。{town}のギルドで顔を見かけるかもしれない。"
NOTE_JAILED = ("【同房】あなたは今、{town}の牢に入っている囚人で、{player}と同じ房にいる。"
               "罪状は{crime}。{player}とはこれまでに{years}年を同じ房で過ごした。"
               "看守の目を盗んで小声で話している。牢の外へは出られない。")
NOTE_FREE = ("【かつての同房】あなたは{town}の牢で{player}と同じ房にいた元囚人（罪状は{crime}）。"
             "{player}とは{years}年を共に過ごし、今は牢を出ている。")

PROMPT = (
    "あなたはRPGの世界で、牢に入っている囚人NPCを1人考える係です。\n"
    "この囚人は、捕まった主人公と同じ房に入れられています。\n"
    "舞台となる土地: {area}\n"
    "土地の様子: {notes}\n"
    "この土地の強さの目安: 難易度{difficulty}（0〜76。大きいほど強い）\n"
    "既にいる人物（名前をかぶらせない）: {taken}\n"
    "\n"
    "次の項目を考えてください。\n"
    "- name: 日本語。通り名と名前を合わせた短い呼び名（例の形式:「鉄拳のグレン」）\n"
    "- crime: 日本語。捕まった罪状。短い句（例:「関所破り」「貴族の屋敷への押し込み」）\n"
    "- profile: 日本語。経歴と捕まった経緯。2〜3文。腕に覚えがあり、牢を出たら冒険者として身を立てるつもりでいる\n"
    "- personality: 日本語。性格。1〜2文\n"
    "- speech_style: 日本語。口調の特徴。1文\n"
    "- look_description: 日本語。見た目。1〜2文\n"
    "- category: 英語。次のどれか1つ: {categories}\n"
    "- look: 英語。外見を表す短い句を5つ、カンマ区切り。"
    "1つ目は category と同じ語にする（例: middle-aged man, shaved head, prison rags, scarred knuckles, wary eyes）\n"
)


def clean_text(value, limit=400):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def clean_name(value):
    """名前はそのままファイルパスになる（GAME.md §2.15）。危ない字は落とす（`320_` と同じ）。"""
    name = clean_text(value, NAME_MAX * 2)
    for bad in "\\/:*?\"<>|":
        name = name.replace(bad, "")
    return name.strip()[:NAME_MAX]


def clean_category(value):
    text = clean_text(value, 40).lower().strip()
    for known in CATEGORIES:
        if known in text:
            return known
    return CATEGORIES[0]


def clean_look(value, category):
    tokens = []
    for token in clean_text(value, 240).split(","):
        token = token.strip()
        if token and token.lower() != category:
            tokens.append(token)
    return [category] + tokens[:7]


def raised_affinity(before, step, ceiling=None):
    """1年ぶん上げた好感度。読めなければ 0 から。"""
    base = before if isinstance(before, (int, float)) and not isinstance(before, bool) else 0
    after = int(base) + max(0, int(step))
    return min(after, int(ceiling)) if ceiling is not None else after


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg

    # ---------------------------------------------------- 控え
    def mates(app):
        """`(周回の鍵, {id: 控え})`。書き換えたら `worlds.save` すること（錠は呼び側）。"""
        playthrough = worlds.playthrough(app)
        bucket = worlds.load(playthrough)
        found = bucket.get("cellmates")
        if not isinstance(found, dict):
            found = bucket["cellmates"] = {}
        return playthrough, found

    def jailed(app, statuses=("jailed",)):
        """牢の中（または決行に加勢中）の同房の囚人 `(id, 控え)`。いなければ None。"""
        with worlds.lock:
            _key, found = mates(app)
            for npc_id, mate in found.items():
                if isinstance(mate, dict) and mate.get("status") in statuses:
                    return str(npc_id), dict(mate)
        return None

    def is_dead(app, npc_id):
        config = getattr(ui.character_of(app, npc_id), "config", None)
        return isinstance(config, dict) and bool(config.get("is_dead"))

    def set_mate(app, npc_id, **changes):
        with worlds.lock:
            playthrough, found = mates(app)
            mate = found.get(str(npc_id))
            if isinstance(mate, dict):
                mate.update(changes)
                worlds.save(playthrough)

    # ---------------------------------------------------- 好感度
    def affinity_of(app, npc_id):
        character = ui.character_of(app, npc_id)
        relationship = getattr(character, "relationship", None)
        row = relationship.get("player") if isinstance(relationship, dict) else None
        value = row.get("affinity") if isinstance(row, dict) else None
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def write_affinity(app, npc_id, value):
        """実行時の人物と素データの両方へ書く（`912_` と同じ。保存のときゲームは実行時の値を写す）。"""
        targets = [getattr(ui.character_of(app, npc_id), "relationship", None)]
        for where, holder in npcs.npc_stores(app):
            if "characters" in where.rsplit(".", 1)[-1]:
                continue
            data = holder.get(str(npc_id)) if isinstance(holder, dict) else None
            if isinstance(data, dict):
                targets.append(data.get("relationship"))
        for relationship in targets:
            if isinstance(relationship, dict):
                row = relationship.setdefault("player", {})
                if isinstance(row, dict):
                    row["affinity"] = value

    # ---------------------------------------------------- 作る
    def area_notes(area):
        notes = frames.attr(area, "descriptions", None)
        if isinstance(notes, dict):
            for key in ("overview", "area_description", "facilities"):
                value = notes.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return notes if isinstance(notes, str) else ""

    def taken_names(app):
        characters = getattr(getattr(app, "world", None), "characters", None)
        names = [getattr(character, "name", None) for character in (characters or {}).values()]
        return [name for name in names if isinstance(name, str) and name][-TAKEN_NAMES_MAX:]

    def exp_level_for(difficulty):
        """経験値レベル。ゲーム自身の対応表で引く（呼べなければ近似。`320_` と同じ）。"""
        table = getattr(sys.modules.get("scripts.functions"), "get_npc_exp_level", None)
        if callable(table):
            try:
                level = table(difficulty)
                if isinstance(level, int) and not isinstance(level, bool):
                    return min(100, max(1, level))
            except Exception:
                ctx.log_exc("crime incentive: get_npc_exp_level failed")
        return min(100, max(1, difficulty + 5))

    def guild_of(area):
        for node in ui.nodes_of(area):
            for key, facility in ui.facilities_of(node).items():
                if ui.facility_type_of(facility) == ui.GUILD_FACILITY_TYPE:
                    return str(key), facility
        return None, None

    def compose(app, area, difficulty):
        """囚人1人ぶんの項目と罪状。書けなければ `(None, None)`。"""
        structure = llm.create_structure(
            ctx, "ModCellmate",
            {"name": (str, ...), "crime": (str, ...), "profile": (str, ...),
             "personality": (str, ...), "speech_style": (str, ...),
             "look_description": (str, ...), "category": (str, ...), "look": (str, ...)},
            label="cellmate")
        prompt = PROMPT.format(area=getattr(area, "name", "") or "", notes=clean_text(area_notes(area), 600),
                               difficulty=difficulty, taken="、".join(taken_names(app)) or "なし",
                               categories=", ".join(CATEGORIES))
        data = llm.ask(ctx, MANAGER_NAME, [{"role": "user", "content": prompt}],
                       timeout=COMPOSE_TIMEOUT, structure=structure, max_tokens=COMPOSE_MAX_TOKENS,
                       label="cellmate", write=write)
        if not isinstance(data, dict):
            return None, None
        name = clean_name(data.get("name"))
        crime = clean_text(data.get("crime"), 60)
        if not name or not crime:
            return None, None
        category = clean_category(data.get("category"))
        fields = {"name": name,
                  "profile": clean_text(data.get("profile")),
                  "personality": clean_text(data.get("personality")),
                  "speech_style": clean_text(data.get("speech_style"), 120),
                  "look_description": clean_text(data.get("look_description")),
                  "category": category,
                  "look": clean_look(data.get("look"), category),
                  "job": "adventure",
                  "age": 20,
                  "experience_level": exp_level_for(difficulty),
                  # 関係の印は「初対面」ではなく「同房の囚人」（`320_` の「初対面」のままにすると、出所後の会話で
                  # 文脈に「同じ房にいた」と渡しても、相手が初対面の名乗りをした。実機）。
                  "relationship": {"player": {
                      "affinity": 0, "affinity_text": "警戒心がある",
                      "relationship": [RELATION_TAG], "conversation_count": 0}}}
        return fields, crime

    def make_cellmate(app):
        """同房の囚人を作り、街のギルドに置く（一覧には載せない）。作れたら `(id, 控え)`。"""
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        guild_id, _guild = guild_of(area)
        if guild_id is None:
            write("cellmate: no guild in area {}; nobody joins the cell".format(area_id))
            return None
        difficulty = min(DIFFICULTY_MAX, max(0, int(env.area_difficulty(app) or 0)))
        fields, crime = compose(app, area, difficulty)
        if fields is None:
            write("cellmate: the LLM gave no usable cellmate")
            return None
        npc_id = npcs.make_npc(app, fields, area_id, guild_id,
                               config={"level_of_detail": 1, "difficulty_level": difficulty}, write=write)
        if npc_id is None:
            write("cellmate: make_npc failed")
            return None
        character = ui.character_of(app, npc_id)
        detail = getattr(app, "generate_npc_detail", None)
        if character is not None and callable(detail):
            try:
                detail(character)       # HP・スキル・立ち絵（埋めないまま戦闘に出すと落ちる）
            except Exception:
                ctx.log_exc("crime incentive: generate_npc_detail failed for the cellmate")
        name = ui.character_name(app, npc_id, fallback=fields["name"])
        mate = {"name": name, "crime": crime, "area": str(area_id), "guild": guild_id,
                "status": "jailed", "years": 0}
        with worlds.lock:
            playthrough, found = mates(app)
            found[str(npc_id)] = mate
            worlds.save(playthrough)
        write("cellmate: {} {!r} ({}) joined the cell in area {} (difficulty {}, Lv {})".format(
            npc_id, name, crime, area_id, difficulty, fields["experience_level"]))
        return str(npc_id), mate

    @ctx.wrap("__main__:ImprisonmentStartManager.execute", required=False, safe=True)
    def imprisonment_start(orig, self, *args, **kwargs):
        """新しい刑期。確率で同房の囚人を作る（LLM を待つのはこのスレッド。ゲームは待機表示のまま）。"""
        made = None
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if cfg.CELLMATE_ENABLED and app is not None and jailed(app, ("jailed", "breaking")) is None:
                roll = cfg._RNG.random() * 100
                write("cellmate: roll {:.0f} < {}%?".format(roll, cfg.CELLMATE_PCT))
                if roll < cfg.CELLMATE_PCT:
                    made = make_cellmate(app)
        except Exception:
            ctx.log_exc("crime incentive: cannot make the cellmate")
        result = orig(self, *args, **kwargs)
        if made is not None:
            screen.say(app, ARRIVE_TEXT.format(name=made[1]["name"], crime=made[1]["crime"]))
        return result

    # ---------------------------------------------------- 年ごとに親しくなる
    @ctx.wrap("__main__:ImprisonmentPhaseManager.execute", required=False, safe=True)
    def prison_year(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            found = jailed(app) if app is not None else None
            if found is not None:
                npc_id, mate = found
                before = affinity_of(app, npc_id)
                after = raised_affinity(before, cfg.CELLMATE_AFFINITY_PER_YEAR)
                write_affinity(app, npc_id, after)
                set_mate(app, npc_id, years=int(mate.get("years") or 0) + 1)
                write("cellmate: a year with {} ({}); affinity {} -> {}".format(
                    npc_id, mate.get("name"), before, after))
                screen.say(app, YEAR_TEXT.format(name=mate.get("name"), before=before if before is not None else 0,
                                                 after=after))
        except Exception:
            ctx.log_exc("crime incentive: the cellmate's year failed")
        return result

    # ---------------------------------------------------- 牢の中で話す
    def talking_in_cell(app):
        """牢の中の同房の囚人と会話している最中か。"""
        found = jailed(app)
        return found is not None and str(getattr(app, "in_conversation", "")) == found[0]

    def confined(app):
        """ローダの窓口 `confinement` に置く。牢の中の会話の間は、他の MOD も場を動かす選択肢を足さない。"""
        return CONFINED_TEXT if talking_in_cell(app) else None

    confinement.declare(env.owner, ctx, confined)

    def on_refresh(app, buttons):
        """服役の毎年の画面に「〈名前〉と話す」を足す。牢の中の会話からはゲームの「雇いたい」を外す。"""
        if talking_in_cell(app):
            kept = [entry for entry in buttons
                    if not (isinstance(entry, dict) and entry.get("text") == HIRE_TEXT)]
            if len(kept) != len(buttons):
                buttons[:] = kept
                write("cellmate: removed {!r} from the conversation in the cell".format(HIRE_TEXT))
            return
        buttons[:] = [entry for entry in buttons if screen.mark_of(entry) != MARK]
        if not cfg.CELLMATE_ENABLED or not any(ui.spec_cls_name(entry) == jailbreak.SERVE_SPEC
                                                for entry in buttons):
            return
        found = jailed(app)
        if found is None:
            return
        npc_id, mate = found
        screen.prune_stale(buttons, (TALK_LABEL.format(name=mate.get("name")),))
        entry = screen.button(TALK_LABEL.format(name=mate.get("name")), mark=MARK)
        if entry is not None:
            buttons.append(entry)

    def press(app, action):
        found = jailed(app)
        cls = ui.cls_of(CONVERSATION_SPEC)
        if found is None or cls is None:
            write("WARN cellmate: nobody to talk to ({})".format(found))
            screen.apply_buttons(app, None, "cellmate")
            return
        npc_id, mate = found
        write("cellmate: talk with {} ({})".format(npc_id, mate.get("name")))
        try:
            phase = cls(app, npc_id)
        except Exception:
            ctx.log_exc("crime incentive: cannot build the conversation with the cellmate")
            screen.apply_buttons(app, None, "cellmate")
            return
        screen.start_phase(app, phase, mate.get("name") or "")

    @ctx.wrap("__main__:InstantaleApp.toggle_to_action_in_conversation", required=False, safe=True)
    def action_menu(orig, self, *args, **kwargs):
        """牢の中の会話では「行動」（襲う・盗む・仲間に誘う など）を開かせない。"""
        if talking_in_cell(self):
            write("cellmate: blocked the action menu in the cell")
            screen.say(self, NO_ACTION_TEXT)
            return None
        return orig(self, *args, **kwargs)

    # ---------------------------------------------------- 会話の文脈
    def notes(info):
        app = info.get("app") or ui.find_app()
        npc_id = str(info.get("npc_id") or "")
        if app is None or not npc_id:
            return None
        with worlds.lock:
            _key, found = mates(app)
            mate = dict(found.get(npc_id) or {})
        if not mate:
            return None
        town = getattr(ui.world_areas(app).get(str(mate.get("area"))), "name", None) or "この街"
        player = getattr(getattr(app, "player", None), "name", None) or "主人公"
        text = NOTE_JAILED if mate.get("status") in ("jailed", "breaking") else NOTE_FREE
        return text.format(town=town, player=player, crime=mate.get("crime") or "不明",
                           years=int(mate.get("years") or 0))

    modnpc.install(ctx, write=write)
    modnpc.register(env.owner + ":cellmate", modnpc.ANY, notes=notes, write=write)

    # ---------------------------------------------------- 決行・出所
    def enroll(app, npc_id, mate):
        area = ui.world_areas(app).get(str(mate.get("area")))
        if area is None:
            write("WARN cellmate: area {} not found to enroll {}".format(mate.get("area"), npc_id))
            return
        rosters = npcs.enroll(app, area, str(mate.get("area")), npc_id)
        write("cellmate: enrolled {} at the guild of area {} ({})".format(npc_id, mate.get("area"), rosters))

    def place_at_guild(app, npc_id, mate):
        """ギルドへ戻す（ゲーム自身の `move_npc_to_facility`。セーブ側の居場所も合わせる。`326_` と同じ）。"""
        area_id = str(mate.get("area"))
        area = ui.world_areas(app).get(area_id)
        target, node = ui.find_facility(area, str(mate.get("guild")))
        mover = getattr(app, "move_npc_to_facility", None)
        character = ui.character_of(app, npc_id)
        if target is None or not callable(mover) or character is None:
            write("WARN cellmate: cannot place {} at the guild".format(npc_id))
            return
        mover(npc_id, character, target, node)
        for where, holder in npcs.npc_stores(app):
            if "characters" in where.rsplit(".", 1)[-1]:
                continue
            data = holder.get(str(npc_id)) if isinstance(holder, dict) else None
            if isinstance(data, dict):
                data["current_area"] = area_id
                data["current_location"] = str(mate.get("guild"))

    def on_break(app):
        """決行。好感度が足りていれば仲間に加えて加勢させる。"""
        found = jailed(app)
        if found is None:
            return
        npc_id, mate = found
        affinity = affinity_of(app, npc_id)
        if affinity is None or affinity < cfg.CELLMATE_ASSIST_AFFINITY:
            write("cellmate: {} stays in the cell (affinity {} < {})".format(
                npc_id, affinity, cfg.CELLMATE_ASSIST_AFFINITY))
            return
        enroll(app, npc_id, mate)       # 仲間は冒険者の一覧に載っていることが前提（GAME.md §2.23）
        app.add_party_member(npc_id)
        screen.paint_party(app)
        set_mate(app, npc_id, status="breaking")
        write("cellmate: {} joins the break out (affinity {})".format(npc_id, affinity))
        screen.say(app, ASSIST_TEXT.format(name=mate.get("name")))

    def on_exit(app, how=""):
        """主人公が牢を出た。加勢した囚人は仲間のまま、牢に残った囚人はギルドの冒険者になる。"""
        found = jailed(app, ("jailed", "breaking"))
        if found is None:
            return
        npc_id, mate = found
        town = getattr(ui.world_areas(app).get(str(mate.get("area"))), "name", None) or "この街"
        in_party = npc_id in [str(member) for member in ui.party_member_ids(app)]
        if is_dead(app, npc_id):
            # 決行の戦闘で倒れた（主人公は勝つか逃げて外へ出た）。ゲームが退場させている。
            set_mate(app, npc_id, status="dead")
            write("cellmate: player left the prison ({}); {} fell in the break out".format(how, npc_id))
            screen.when_idle(app, lambda: screen.say(app, ASSIST_DEAD_TEXT.format(name=mate.get("name"))),
                             proceed_on_timeout=True, tag="cellmate exit")
            return
        if mate.get("status") == "breaking" and in_party:
            set_mate(app, npc_id, status="party")
            line = ASSIST_FREE_TEXT.format(name=mate.get("name"))
        else:
            if mate.get("status") == "breaking":
                place_at_guild(app, npc_id, mate)     # 戦闘で倒れて仲間から外れた（どこにも居なくなるのを防ぐ）
            else:
                enroll(app, npc_id, mate)
            set_mate(app, npc_id, status="free")
            line = RELEASE_TEXT.format(name=mate.get("name"), town=town)
        write("cellmate: player left the prison ({}); {} is now {}".format(
            how, npc_id, "in the party" if mate.get("status") == "breaking" and in_party else "at the guild"))
        screen.when_idle(app, lambda: screen.say(app, line), proceed_on_timeout=True, tag="cellmate exit")

    env.on_refresh(on_refresh)
    env.on_press(MARK, press)
    env.on_prison("break", on_break)
    env.on_prison("exit", on_exit)
