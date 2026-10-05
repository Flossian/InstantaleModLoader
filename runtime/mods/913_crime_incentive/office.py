# -*- coding: utf-8 -*-
"""裏の仕事。裏の事務所で違法な依頼を受ける。仕様は DOC.md「裏の仕事」、種類の表は `underworld`。

- 裏の事務所に「裏の仕事を探す」。種類（密輸・盗掘・破壊工作・脱獄の手引き・強盗・暗殺）は
  手配の重さの合計で解禁され、ゲーム自身の依頼の生成に種類ごとの指示を差し込んで作る
- 報酬は種類ごとの倍率（5〜10倍）、片付けると依頼の街の手配度が下がる。ギルドの掲示板からは隠す
- 事務所の公式の「裏の依頼掲示板」（`NotImplementedManager`）と同じ役割なので、公式が未実装のあいだだけ
  別のボタンとして出す（本人の判断で方針の例外）。公式が実装したら出さない。公式のボタンには触らない
"""
import time

from instantale_modloader import frames, ui, wanted

from . import common, theft, underworld

#: 裏の事務所（GAME.md §2.7 の `facility_type`）。
OFFICE_FACILITY_TYPE = "underworld_office"
#: 公式が「※未実装」として並べる枠のマネージャ。
NOT_IMPLEMENTED_SPEC = "NotImplementedManager"
#: 裏の事務所の公式の枠（実機。2026-10-04 の時点で `NotImplementedManager`）。
#: 裏の仕事はこれと同じ役割なので、**公式が未実装のあいだだけ**別のボタンとして出す（本人の判断）。
#: 公式のボタンには触らない。公式が実装したら（spec が未実装でなくなったら）出さない ＝ 二重にならない。
OFFICIAL_BOARD_LABEL = "裏の依頼掲示板"
SEARCH_LABEL = "裏の仕事を探す"
JOB_LABEL = "裏の仕事：{title}"
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
#: セーブから復元された印の無い残骸を見分けるラベル（`ui.Screen.prune_stale`。前方一致）。
#: 店で盗むの外した「盗みを働く」も施設の選択肢に焼かれていたので、ここで一緒に消す。
OUR_LABELS = (theft.THEFT_LABEL, SEARCH_LABEL, JOB_LABEL_HEAD)


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    current_facility, back_to_shop = env.current_facility, env.back_to_shop

    # -------------------------------------------------- 裏の仕事
    # 裏の事務所に「裏の仕事を探す」を足す。押すとゲーム自身の依頼の生成（`generate_random_quest`）を
    # 呼び、その内側の `random_quest_generator` の頼み文へ種類ごとの指示を差し込む（`307_` と同じ形）。
    # 依頼の id は控え（`state\` の `underworld`）に持ち、依頼の辞書には鍵を足さない（GAME.md §2.9）。
    # ギルドの掲示板からは隠し、帰還のときに報酬へ倍率を掛け、依頼の街の手配度を下げる。
    inject = {"brief": None, "at": 0.0}
    #: 公式の枠が実装されたことを1度だけ記録するための印。
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

    def insert_office_buttons(app, buttons):
        """裏の事務所の選択肢に、受けられる裏の仕事か「裏の仕事を探す」を1つ足す。"""
        if not cfg.UNDERWORLD_ENABLED or office_of(app) is None:
            return
        official = [ui.spec_cls_name(entry) for entry in buttons
                    if isinstance(entry, dict) and entry.get("text") == OFFICIAL_BOARD_LABEL]
        if official and official[0] != NOT_IMPLEMENTED_SPEC:
            # 公式の裏の依頼掲示板が実装された。こちらは手を引く。
            if not office_notes.get("official"):
                office_notes["official"] = True
                write("underworld: the official {!r} is implemented ({}); not adding ours".format(
                    OFFICIAL_BOARD_LABEL, official[0]))
            return
        area_id = ui.area_id_of(ui.current_area(app))
        pending = open_jobs_here(app, area_id)
        if pending:
            quest_id = pending[0]
            title = ui.quest_value(ui.quest_of(app, quest_id), "quest_title", "") or "名も無い仕事"
            entry = screen.button(JOB_LABEL.format(title=frames.short(title, 30)),
                                  mark="job:" + quest_id)
        else:
            weight = wanted.total_weight(getattr(app, "player", None))
            if not underworld.unlocked(weight, cfg.UNDERWORLD_UNLOCK_PCT):
                return
            entry = screen.button(SEARCH_LABEL, mark="search")
        if entry is None:
            return
        at = next((index for index, item in enumerate(buttons)
                   if ui.spec_cls_name(item) == common.FACILITY_MARK), len(buttons))
        buttons.insert(at, entry)

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

    def open_acceptance(app, quest_id):
        """ゲーム本来の受注画面へ渡す（`307_` と同じ。自前の `PhaseSpec` は組まない）。"""
        choice_cls = ui.cls_of(QUEST_CHOICE_SPEC)
        if choice_cls is None:
            write("WARN underworld: QuestChoiceManager is not available")
            return False
        try:
            manager = choice_cls(app, QUEST_TYPE, str(quest_id))
        except Exception:
            ctx.log_exc("crime incentive: QuestChoiceManager({!r}) failed".format(quest_id))
            return False
        title = ui.quest_value(ui.quest_of(app, str(quest_id)), "quest_title", "") or SEARCH_LABEL
        return screen.start_phase(app, manager, title)

    def search_job(app):
        """裏の仕事を1つ作って受注画面を出す。**別スレッドに投げずここで最後までやる**（`307_`）。"""
        found = office_of(app)
        if found is None:
            write("underworld: not at the office any more")
            back_to_shop(app, "not at the office")
            return
        _facility, broker = found
        player = getattr(app, "player", None)
        kinds = underworld.unlocked(wanted.total_weight(player), cfg.UNDERWORLD_UNLOCK_PCT)
        if not kinds:
            back_to_shop(app, "nothing unlocked")
            return
        kind = cfg._RNG.choice(kinds)
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        town = getattr(area, "name", None) or "この街"
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("WARN underworld: DisplayQuestChoice is not available")
            screen.say(app, NO_JOB_TEXT)
            back_to_shop(app, "no generator")
            return
        inject.update(brief=underworld.brief(kind, town, broker), at=time.monotonic())
        screen.busy_on(app)
        screen.say(app, LOOKING_TEXT.format(broker=broker or "事務所の主"))
        before = set(ui.quest_ids(app))
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("crime incentive: generate_random_quest failed")
        finally:
            inject.update(brief=None)
        added = sorted(set(ui.quest_ids(app)) - before, key=ui.id_sort_key)
        if not added:
            write("underworld: no quest was generated ({})".format(kind["name"]))
            screen.busy_off(app)
            screen.say(app, NO_JOB_TEXT)
            back_to_shop(app, "no quest")
            return
        quest_id = added[-1]
        mult = underworld.multiplier(kind, cfg.UNDERWORLD_REWARD_PCT)
        loss = underworld.scaled(kind["loss"], cfg.UNDERWORLD_LOSS_PCT)
        with worlds.lock:
            playthrough, jobs = jobs_of(app)
            jobs[quest_id] = {"kind": kind["key"], "area": area_id, "mult": mult, "loss": loss}
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
        screen.when_idle(app, lambda: open_acceptance(app, quest_id) or back_to_shop(
            app, "acceptance failed"), proceed_on_timeout=True, tag="underworld")

    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator", required=False)
    def random_quest_generator(orig, world_overview, settlement_name, settlement_overview,
                               settlement_structure_description, area_description,
                               quest_difficulty, *args, **kwargs):
        """裏の仕事を作る回だけ、`area_description` に種類の指示を足す。印は1回で使い切る。

        `328_`（街の描写を伏せる）より内側に居るので、伏せられた後の描写に足される。
        """
        brief, at = inject.get("brief"), inject.get("at") or 0.0
        inject["brief"] = None
        if brief and time.monotonic() - at <= INJECT_TTL:
            area_description = (area_description or "") + brief
            write("underworld: injected the job brief ({} chars)".format(len(brief)))
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
        """事務所なら仕事のボタンを足す。施設の選択肢でなければ掲示板から裏の仕事を隠す。"""
        if common.is_facility_screen(buttons):
            screen.prune_stale(buttons, OUR_LABELS)
            if not any(screen.mark_of(entry) for entry in buttons):
                insert_office_buttons(app, buttons)
        else:
            hide_from_board(app, buttons)

    def press_search(app, action):
        write("pressed {!r}".format(SEARCH_LABEL))
        screen.start_phase(app, SearchPhase(app), SEARCH_LABEL,
                           fallback=lambda: search_job(app))

    def press_job(app, action):
        quest_id = action[len("job:"):]
        write("pressed the job {}".format(quest_id))
        if not open_acceptance(app, quest_id):
            back_to_shop(app, "acceptance failed")

    env.on_refresh(on_refresh)
    env.on_press("search", press_search)
    env.on_press("job:", press_job)
