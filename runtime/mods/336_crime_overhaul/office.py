# -*- coding: utf-8 -*-
"""裏の仕事。裏の事務所で違法な依頼を受ける。仕様は DOC.md「裏の仕事」、種類の表は `underworld`。

- 裏の事務所の公式の「裏の依頼掲示板」（未実装の枠）を入口にする。押すとギルドの掲示板と同じ形の一覧
  （片付けていない裏の仕事 / 少なければ「裏の仕事を探す」 / 「やめる」）が出る。種類（密輸・盗掘・破壊工作・脱獄の手引き・強盗・暗殺）は
  手配の重さの合計で解禁され、ゲーム自身の依頼の生成に種類ごとの指示を差し込んで作る
- 報酬は種類ごとの倍率（5〜10倍）、片付けると依頼の街の手配度が下がる。ギルドの掲示板からは隠し、
  掲示板に出さない依頼としてローダの窓口 `board` に置く（他の MOD が掲示板の依頼として拾わないように）
- 公式の「裏の依頼掲示板」（`NotImplementedManager`）と同じ役割なので、公式が未実装のあいだだけ枠を乗っ取る
  （方針の例外。はじめは別のボタンだったが、選択肢が増えたので枠を使う形にした）。
  ローダの窓口 `choices` の `replace` で同じ場所に差し替えるだけで、ゲームのボタン（spec は公式のまま）は書き換えない。
  公式が実装したら触らない
- 一覧は窓口の MOD だけの画面（`BOARD_SCREEN`）。下の事務所の選択肢は窓口が残すので、一覧で保存して読み直しても
  一覧に戻り、「やめる」で事務所の選択肢に戻る。依頼のボタンはゲームの `QuestChoiceManager` の写しで、押下はゲームへ流す
"""
import time

from instantale_modloader import board, choices, quest_clients, ui, wanted

from . import common, theft, underworld

#: 裏の事務所（GAME.md §2.7 の `facility_type`）。
OFFICE_FACILITY_TYPE = "underworld_office"
#: 公式が「※未実装」として並べる枠のマネージャ。
NOT_IMPLEMENTED_SPEC = "NotImplementedManager"
#: 裏の事務所の公式の枠（実機。2026-10-04 の時点で `NotImplementedManager`）。
#: 裏の仕事はこれと同じ役割なので、**公式が未実装のあいだだけ**この枠を乗っ取って入口にする
#: （方針の例外。2026-10-05 に、選択肢が増えてきたので別のボタンをやめて枠を使う形にした）。
#: 乗っ取りは印を付けるだけで、spec は公式の `NotImplementedManager` のまま（印が消えれば素のゲームのボタンに戻る）。
#: 公式が実装したら（spec が未実装でなくなったら）触らない ＝ 二重にならない。
OFFICIAL_BOARD_LABEL = "裏の依頼掲示板"
#: 一覧の中の、裏の仕事を作るボタン（素の掲示板の「クエストを探す」に当たる）。
SEARCH_LABEL = "裏の仕事を探す"
#: 一覧を閉じるボタン。素の掲示板と同じ文言（2026-08-26 の `206_` の記録）。
BACK_LABEL = "やめる"
#: 一覧（窓口の MOD だけの画面）の名前。
BOARD_SCREEN = "underworld board"
#: 版1の途中まで足していたボタン（「裏の仕事：〈題名〉」と、枠に題名を付けた形）。セーブに焼かれた残骸を消すためだけに残す。
JOB_LABEL_HEAD = "裏の仕事："
#: `QuestChoiceManager(app, quest_type, quest_id)` の `quest_type`。`world.quests` に通るのはこれだけ（GAME.md §2.9）。
QUEST_TYPE = "settlement_quest"
QUEST_CHOICE_SPEC = "QuestChoiceManager"
#: 生成の頼み文へ差し込む印の寿命（秒）。`307_` と同じ。
INJECT_TTL = 300.0
LOOKING_TEXT = "{broker}が帳面をめくり、回せる仕事を探している……"
JOB_FOUND_TEXT = "「{name}の仕事だ。報酬は表の相場の{mult}倍。下手を打てば{town}には居られなくなる」{broker}が声を潜めた。"
NO_JOB_TEXT = "「今は回せる仕事が無い」"
JOB_DONE_TEXT = "{town}の官憲が、{name}の下手人を追い始めた。（手配度 {before} → {after}）"
#: セーブから復元された印の無い残骸を見分けるラベル（窓口の `labels`。前方一致）。
#: 店で盗むの外した「盗みを働く」も施設の選択肢に焼かれていたので、ここで一緒に消す。
OUR_LABELS = (theft.THEFT_LABEL, SEARCH_LABEL, JOB_LABEL_HEAD)


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    current_facility, back_to_shop = env.current_facility, env.back_to_shop

    # -------------------------------------------------- 裏の仕事
    # 裏の事務所の「裏の依頼掲示板」の一覧に「裏の仕事を探す」を出す。押すとゲーム自身の依頼の生成（`generate_random_quest`）を
    # 呼び、その内側の `random_quest_generator` の頼み文へ種類ごとの指示を差し込む（`307_` と同じ形）。
    # 依頼の id は控え（`state\` の `underworld`）に持ち、依頼の辞書には鍵を足さない（GAME.md §2.9）。
    # ギルドの掲示板からは隠し、帰還のときに報酬へ倍率を掛け、依頼の街の手配度を下げる。
    inject = env.quest_inject
    #: 公式の枠が実装されたことを1度だけ記録するための印。`board` は最後に記録した一覧の並び。
    office_notes = {}

    def jobs_of(app):
        """`(周回の鍵, {依頼の id: 控え})`。控えは書き換えたら `worlds.save` すること。"""
        playthrough = worlds.playthrough(app)
        bucket = worlds.load(playthrough)
        jobs = bucket.get("underworld")
        if not isinstance(jobs, dict):
            jobs = bucket["underworld"] = {}
        return playthrough, jobs

    def job_open(app, quest_id):
        """まだ片付いていない依頼か（生きた一覧に在り、`status` が `incomplete`）。"""
        quest = ui.quest_of(app, str(quest_id))
        config = ui.quest_value(quest, "config", None) if quest is not None else None
        status = config.get("status") if isinstance(config, dict) else None
        return quest is not None and status == "incomplete"

    def open_jobs_here(app, area_id):
        """この街の、まだ片付いていない裏の仕事の id（古い順）。片付いた控えはここで捨てる。"""
        with worlds.lock:
            playthrough, jobs = jobs_of(app)
            gone = [qid for qid in jobs if not job_open(app, qid)]
            for qid in gone:
                jobs.pop(qid, None)
            if gone:
                worlds.save(playthrough)
            return sorted((qid for qid, job in jobs.items() if job.get("area") == area_id),
                          key=ui.id_sort_key)

    def office_of(app):
        """いま居る裏の事務所の `(施設, 主の名前)`。事務所でなければ None。"""
        facility = current_facility(app)
        if ui.facility_type_of(facility) != OFFICE_FACILITY_TYPE:
            return None
        owner_id = getattr(facility, "owner", None)
        broker = getattr(ui.character_of(app, str(owner_id)), "name", None) if owner_id else None
        return facility, broker if isinstance(broker, str) and broker else None

    def banned(app):
        """裁判の司法取引で裏の事務所を売り、まだ締め出されている間か（`court`）。"""
        day = ui.game_day(app)
        with worlds.lock:
            until = worlds.load(worlds.playthrough(app)).get("underworld_ban")
        if day is None or not isinstance(until, int):
            return False
        if day < until:
            if office_notes.get("banned") != until:
                office_notes["banned"] = until
                write("underworld: shut out until day {} (sold the office out in court)".format(until))
            return True
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            worlds.load(playthrough).pop("underworld_ban", None)   # 明けたら控えを残さない
            worlds.save(playthrough)
        return False

    def board_slot(app, buttons):
        """裏の事務所の公式の「裏の依頼掲示板」（未実装の枠）を乗っ取るなら、そのゲームのボタン。

        受けられる仕事が無い・締め出し中・公式が実装済みなら None（素のゲームのボタンのまま）。
        """
        if not cfg.UNDERWORLD_ENABLED or office_of(app) is None:
            return None
        found = next((entry for entry in buttons if isinstance(entry, dict)
                      and str(entry.get("text") or "").startswith(OFFICIAL_BOARD_LABEL)), None)
        if found is None:
            if not office_notes.get("no_board"):
                office_notes["no_board"] = True
                write("underworld: no official {!r} among the choices; no way in".format(OFFICIAL_BOARD_LABEL))
            return None
        official = ui.spec_cls_name(found)
        if official != NOT_IMPLEMENTED_SPEC:
            # 公式の裏の依頼掲示板が実装された。こちらは手を引く。
            if not office_notes.get("official"):
                office_notes["official"] = True
                write("underworld: the official {!r} is implemented ({}); leaving it alone".format(
                    OFFICIAL_BOARD_LABEL, official))
            return None
        if banned(app):
            return None             # 裁判で裏の事務所を売った。しばらく仕事は回ってこない
        area_id = ui.area_id_of(ui.current_area(app))
        if not open_jobs_here(app, area_id) and not unlocked_kinds(app):
            return None             # 受けられる仕事も、作れる仕事も無い
        return found

    def unlocked_kinds(app):
        return underworld.unlocked(wanted.total_weight(getattr(app, "player", None)),
                                   cfg.UNDERWORLD_UNLOCK_PCT)

    def board_choices(app):
        """裏の依頼掲示板の一覧。素の掲示板（`DisplayQuestChoice`）と同じ並び:
        片付けていない裏の仕事（ゲームの `QuestChoiceManager` の写し。押下はゲームへ流す）/
        少なければ「裏の仕事を探す」/ 「やめる」。
        """
        area_id = ui.area_id_of(ui.current_area(app))
        out = []
        pending = open_jobs_here(app, area_id)
        for quest_id in pending:
            title = ui.quest_value(ui.quest_of(app, quest_id), "quest_title", "") or "名も無い仕事"
            spec = screen.make_spec(QUEST_CHOICE_SPEC, (QUEST_TYPE, str(quest_id)))
            if spec is not None:
                out.append(choices.Choice(title, base={"text": title, "spec": spec}))
        if len(pending) < cfg.UNDERWORLD_SEARCH_BELOW and unlocked_kinds(app):
            out.append(choices.Choice(SEARCH_LABEL, on_press=press_search))
        out.append(choices.Choice(BACK_LABEL, on_press=close_board))
        texts = [choice.text for choice in out]
        if office_notes.get("board") != texts:
            office_notes["board"] = texts
            write("underworld: board {}".format(texts))
        return out

    def board_screen(app, view, _data):
        """一覧（窓口の MOD だけの画面）。事務所を離れていたら閉じる（None）。"""
        if office_of(app) is None or not common.is_facility_screen(view.buttons):
            return None
        return board_choices(app)

    def show_board(app):
        """一覧を開く（窓口が次のフレームで組み直して塗る）。開いているなら組み直すだけ。"""
        env.open_screen(app, BOARD_SCREEN, {})

    def close_board(app):
        write("underworld: closed the board")
        env.close_screen(app)
        return None

    def hide_from_board(app, buttons):
        """ギルドの掲示板（`QuestChoiceManager` の並び）から裏の仕事を外す。"""
        rows = [(index, str((ui.spec_args(entry) or [None, None])[1]))
                for index, entry in enumerate(buttons)
                if ui.spec_cls_name(entry) == QUEST_CHOICE_SPEC
                and len(ui.spec_args(entry) or []) >= 2]
        if not rows:
            return
        with worlds.lock:
            _playthrough, jobs = jobs_of(app)
            ours = set(jobs)
        removed = [qid for index, qid in reversed(rows) if qid in ours]
        for index, qid in reversed(rows):
            if qid in ours:
                del buttons[index]
        if removed:
            write("underworld: hid job(s) {} from the quest board".format(removed))

    def search_job(app):
        """裏の仕事を1つ作って受注画面を出す。**別スレッドに投げずここで最後までやる**（`307_`）。"""
        found = office_of(app)
        if found is None:
            write("underworld: not at the office any more")
            back_to_shop(app, "not at the office")
            return
        _facility, broker = found
        player = getattr(app, "player", None)
        kinds = unlocked_kinds(app)
        if not kinds:
            screen.when_idle(app, lambda: show_board(app), proceed_on_timeout=True, tag="underworld board")
            return
        kind = cfg._RNG.choice(kinds)
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        town = getattr(area, "name", None) or "この街"
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("WARN underworld: DisplayQuestChoice is not available")
            screen.say(app, NO_JOB_TEXT)
            screen.when_idle(app, lambda: show_board(app), proceed_on_timeout=True, tag="underworld board")
            return
        inject.update(brief=underworld.brief(kind, town, broker), difficulty=None,
                      at=time.monotonic(), tag="underworld")
        screen.busy_on(app)
        screen.say(app, LOOKING_TEXT.format(broker=broker or "事務所の主"))
        before = set(ui.quest_ids(app))
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("crime incentive: generate_random_quest failed")
        finally:
            inject.update(brief=None, difficulty=None)
        added = sorted(set(ui.quest_ids(app)) - before, key=ui.id_sort_key)
        if not added:
            write("underworld: no quest was generated ({})".format(kind["name"]))
            screen.busy_off(app, restore=False)
            screen.say(app, NO_JOB_TEXT)
            screen.when_idle(app, lambda: show_board(app), proceed_on_timeout=True, tag="underworld board")
            return
        quest_id = added[-1]
        mult = underworld.multiplier(kind, cfg.UNDERWORLD_REWARD_PCT)
        loss = underworld.scaled(kind["loss"], cfg.UNDERWORLD_LOSS_PCT)
        with worlds.lock:
            playthrough, jobs = jobs_of(app)
            jobs[quest_id] = {"kind": kind["key"], "area": area_id, "mult": mult, "loss": loss,
                              "client": str(getattr(_facility, "owner", "") or "")}
            worlds.save(playthrough)
        quest = ui.quest_of(app, quest_id)
        summary = ui.quest_value(quest, "request_summary", "") or ""
        if isinstance(summary, str) and underworld.NOTE_MARK not in summary:
            ui.set_quest_value(app, quest_id, "request_summary",
                               summary + underworld.note(kind, town, mult, loss))
        write("underworld: made job {} {!r} kind {} x{} loss {} (weight {})".format(
            quest_id, ui.quest_value(quest, "quest_title", ""), kind["name"], mult, loss,
            wanted.total_weight(player)))
        screen.busy_off(app, restore=False)
        screen.say(app, JOB_FOUND_TEXT.format(name=kind["name"], mult=underworld.mult_text(mult),
                                              town=town, broker=broker or "事務所の主"))
        # 探すのはフェーズの中（生成が重い）。塗るのはフェーズが終わって手が空いてから（前の受注画面の出し方と同じ）。
        screen.when_idle(app, lambda: show_board(app), proceed_on_timeout=True, tag="underworld board")

    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator", required=False)
    def random_quest_generator(orig, world_overview, settlement_name, settlement_overview,
                               settlement_structure_description, area_description,
                               quest_difficulty, *args, **kwargs):
        """裏の仕事・処刑場からの脱出を作る回だけ、`area_description` に指示を足す。印は1回で使い切る。

        `328_`（街の描写を伏せる）より内側に居るので、伏せられた後の描写に足される。
        難易度が置かれていれば `quest_difficulty` も差し替える（`307_` と同じ形。依頼の敵の強さはこの値で決まる）。
        """
        brief, at = inject.get("brief"), inject.get("at") or 0.0
        difficulty, tag = inject.get("difficulty"), inject.get("tag") or "underworld"
        inject.update(brief=None, difficulty=None)
        if brief and time.monotonic() - at <= INJECT_TTL:
            area_description = (area_description or "") + brief
            write("{}: injected the brief ({} chars)".format(tag, len(brief)))
            if isinstance(difficulty, int) and not isinstance(difficulty, bool):
                write("{}: quest difficulty {!r} -> {}".format(tag, quest_difficulty, difficulty))
                quest_difficulty = difficulty
        return orig(world_overview, settlement_name, settlement_overview,
                    settlement_structure_description, area_description, quest_difficulty,
                    *args, **kwargs)

    @ctx.wrap("__main__:DisplayQuestChoice.get_active_quest_count", required=False, safe=True)
    def active_quest_count(orig, self, *args, **kwargs):
        """掲示板から隠した裏の仕事を未完了の数から引く（引かないと『クエストを探す』が出ない。`911_` と同じ）。"""
        result = orig(self, *args, **kwargs)
        if isinstance(result, bool) or not isinstance(result, int):
            return result
        app = getattr(self, "app", None) or ui.find_app()
        hidden = len(open_jobs_here(app, ui.area_id_of(ui.current_area(app)))) if app else 0
        return max(0, result - hidden) if hidden else result

    # 帰還の報酬に倍率を掛ける（`334_` の懸賞金と同じ形）。窓は `QuestEndManager.execute` の間だけ。
    reward = {"pending": None}

    def rewrite_reward(context):
        """帰還の窓の間だけ、報酬の文の額をこちらが払う額に書き換える。"""
        pending = reward["pending"]
        if pending is not None and pending.get("want") is None:
            base = underworld.reward_amount(context)
            if base:
                want = int(round(base * pending["mult"]))
                rewritten = underworld.replace_amount(context, base, want)
                if rewritten is not None:
                    pending.update(base=base, want=want)
                    context = rewritten
        return context, False

    env.on_text(rewrite_reward)

    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        """裏の仕事を片付けた。報酬に倍率を掛け、依頼の街の手配度を下げ、控えを捨てる。

        どの依頼が終わるのかは `orig` の前に読む（終わった後は片付いている。`318_` と同じ）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        quest_id = ui.current_quest_id(app) if app is not None else None
        job = None
        if cfg.UNDERWORLD_ENABLED and quest_id is not None:
            with worlds.lock:
                job = dict(jobs_of(app)[1].get(quest_id) or {}) or None
        if job is None:
            return orig(self, *args, **kwargs)
        before_gold = ui.gold_of(app)
        reward["pending"] = {"mult": float(job.get("mult") or 1.0), "base": None, "want": None}
        try:
            return orig(self, *args, **kwargs)
        finally:
            pending, reward["pending"] = reward["pending"], None
            try:
                settle_job(app, quest_id, job, pending, before_gold)
            except Exception:
                ctx.log_exc("crime incentive: cannot settle the underworld job")

    def settle_job(app, quest_id, job, pending, before_gold):
        kind = underworld.KIND_BY_KEY.get(job.get("kind")) or {"name": "裏の仕事"}
        after_gold = ui.gold_of(app)
        moved = (after_gold - before_gold) if isinstance(after_gold, int) and \
            isinstance(before_gold, int) else None
        want = pending.get("want") if pending else None
        if want is not None and moved is not None and moved > 0 and want != moved:
            ui.add_gold(app, want - moved)
        write("underworld: job {} ({}) done; reward {} -> {} (x{}), the game paid {}".format(
            quest_id, kind["name"], pending.get("base") if pending else None, want,
            job.get("mult"), moved))
        player = getattr(app, "player", None)
        area_id = str(job.get("area") or "")
        entry = ui.area_record(player, area_id)
        before = ui.lawfulness_of(entry)
        loss = max(0, int(job.get("loss") or 0))
        lines = []
        if before is not None and loss and ui.set_lawfulness(entry, before - loss):
            area = ui.world_areas(app).get(area_id)
            town = getattr(area, "name", None) or "この街"
            write("underworld: lawfulness of {} {} -> {}".format(area_id, before, before - loss))
            lines.append(JOB_DONE_TEXT.format(town=town, name=kind["name"], before=before,
                                              after=before - loss))
        with worlds.lock:
            playthrough, jobs = jobs_of(app)
            jobs.pop(quest_id, None)
            bucket = worlds.load(playthrough)
            # 片付いた後も、依頼人と表に出さない依頼の印を残す（`client_rows` / `kept_off`）。
            common.remember_past(bucket, quest_id, ui.quest_value(ui.quest_of(app, quest_id), "quest_title", ""),
                                 client=job.get("client"))
            # 片付けた数。裁判で司法取引（裏の事務所を売る）を持ちかけられるかに使う（`court`）。
            done = bucket.get("underworld_done")
            bucket["underworld_done"] = (done if isinstance(done, int) else 0) + 1
            worlds.save(playthrough)
        if lines:
            screen.when_idle(app, lambda: [screen.say(app, line) for line in lines],
                             proceed_on_timeout=True, tag="underworld done")

    class SearchPhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**。"""

        def __init__(self, app):
            self.app = app

        def execute(self, choice_text):
            try:
                search_job(self.app)
            except Exception:
                ctx.log_exc("crime incentive: searching a job failed")
                if screen.is_busy():
                    screen.busy_off(self.app)

    def on_refresh(app, buttons):
        """事務所の外の一覧（ギルドの掲示板）から裏の仕事を隠す（ゲームの一覧の間引き）。"""
        if office_of(app) is None:
            hide_from_board(app, buttons)

    def offer(app, view):
        """事務所の選択肢の、公式の「裏の依頼掲示板」の枠を同じ場所で差し替える。"""
        if not common.is_facility_screen(view.buttons):
            return None
        slot = board_slot(app, view.buttons)
        if slot is None:
            return None
        return [choices.Choice(OFFICIAL_BOARD_LABEL, base=slot, replace=True, on_press=press_board)]

    def press_board(app):
        write("pressed {!r}".format(OFFICIAL_BOARD_LABEL))
        show_board(app)
        return None

    def press_search(app):
        write("pressed {!r}".format(SEARCH_LABEL))
        screen.start_phase(app, SearchPhase(app), SEARCH_LABEL,
                           fallback=lambda: search_job(app))
        return None

    def kept_off(app):
        """ギルドの掲示板に出さない依頼（裏の仕事と処刑場からの脱出。片付いた分も）。ローダの窓口 `board` に置く。

        掲示板の依頼を拾う MOD（`911_` のライバル）が、裏の仕事を自分の獲物にしないように（実機で起きた）。
        """
        with worlds.lock:
            bucket = worlds.load(worlds.playthrough(app))
            ids = [str(quest_id) for quest_id in (bucket.get("underworld") or {})]
            ids.extend(common.past_of(app, bucket))
            rescue = bucket.get("rescue")
        if isinstance(rescue, dict) and rescue.get("quest") is not None:
            ids.append(str(rescue["quest"]))
        ids.extend(older_jobs(app, set(ids)))
        return ids

    def office_owners(app):
        """世界の裏の事務所の主の `{名前: 人物の id}`。"""
        owners = {}
        for area in ui.world_areas(app).values():
            for node in ui.nodes_of(area):
                for facility in ui.facilities_of(node).values():
                    if ui.facility_type_of(facility) != OFFICE_FACILITY_TYPE:
                        continue
                    owner_id = getattr(facility, "owner", None)
                    name = getattr(ui.character_of(app, str(owner_id)), "name", None) if owner_id else None
                    if isinstance(name, str) and name.strip():
                        owners[name.strip()] = str(owner_id)
        return owners

    def older_jobs(app, known):
        """控えに無い、片付いた裏の仕事 `{依頼の id: 事務所の主の id}`（版3までに片付けた分）。

        版3までは片付けると控えから消し、`kept_past` も無かった。
        裏の仕事の依頼人の欄は最初の版から事務所の主の名前にさせている（`underworld.BRIEF_TEXT`）ので、それで見分ける。
        片付いた分だけを数える（進行中の仕事は控えに在る）。VERIFICATION.md §3.115。
        """
        owners = office_owners(app)
        if not owners:
            return {}
        found = {}
        for quest_id in ui.quest_ids(app):
            if str(quest_id) in known:
                continue
            quest = ui.quest_of(app, quest_id)
            config = ui.quest_value(quest, "config", None) if quest is not None else None
            if not isinstance(config, dict) or config.get("status") != "completed":
                continue
            client = ui.quest_value(quest, "client_name", "")
            if isinstance(client, str) and client.strip() in owners:
                found[str(quest_id)] = owners[client.strip()]
        return found

    #: 依頼人の行の写し（周回の鍵ごと）。錠が取れなかったときはこれで答える。
    clients_copy = {}

    def client_rows(app):
        """裏の仕事と処刑場からの脱出の依頼人。ローダの窓口 `quest_clients` に置く（TECH.md §3.3.23）。

        依頼人は裏の事務所の主と手引き役で、依頼の行方を知っている。
        錠は待たない。読む側（`301_`）は会話の組み立ての最中に聞きに来る（`quest_claims` と同じ決まり）。
        """
        playthrough = worlds.playthrough(app)
        if not worlds.lock.acquire(blocking=False):
            return [dict(row) for row in clients_copy.get(playthrough) or []]
        try:
            bucket = worlds.load(playthrough)
            rows = [{"quest": quest_id, "npc_id": job.get("client")}
                    for quest_id, job in (bucket.get("underworld") or {}).items()
                    if isinstance(job, dict) and job.get("client")]
            rows.extend({"quest": quest_id, "npc_id": record.get("client"), "npc_name": record.get("name")}
                        for quest_id, record in common.past_of(app, bucket).items()
                        if record.get("client") or record.get("name"))
            rescue = bucket.get("rescue")
        finally:
            worlds.lock.release()
        if isinstance(rescue, dict) and rescue.get("quest") is not None and (rescue.get("client") or rescue.get("name")):
            rows.append({"quest": rescue["quest"], "npc_id": rescue.get("client"), "npc_name": rescue.get("name")})
        rows.extend({"quest": quest_id, "npc_id": owner_id}
                    for quest_id, owner_id in older_jobs(app, {str(row["quest"]) for row in rows}).items())
        clients_copy[playthrough] = [dict(row) for row in rows]
        return rows

    board.declare_kept_off(env.owner, ctx, kept_off)
    quest_clients.declare(env.owner, ctx, client_rows)
    env.underworld_banned = banned
    env.on_refresh(on_refresh)
    env.on_offer(offer)
    env.on_screen(BOARD_SCREEN, board_screen)
    env.on_labels(lambda app: OUR_LABELS)
