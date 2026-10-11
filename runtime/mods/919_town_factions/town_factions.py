# -*- coding: utf-8 -*-
"""街の勢力（開発中）。

世界の設定に書かれた勢力を台帳に起こし、勢力ごとに主人公の見られ方を持つ。
見られ方は NPC の会話の構えに出る（どこでも誰にでも同じに好かれるのを避ける）。
遊び方と決まりは DOC.md §1、決めた仕様と根拠は DOC.md §2。

| 場面 | すること |
|---|---|
| 世界を読み込んだ・会話・日数が進んだ | 台帳が無ければ作る（背景で LLM を2回。抽出と初回の判定） |
| 会話の頼み（5関数） | 相手の街の勢力の構えと、最近の情勢の噂を足す（ローダの `modnpc` の `notes`） |
| 会話を閉じたときの要約 | 見られ方がどう動いたかを相乗りで判定させる（ローダの `talk_summary`） |
| 日数が進んだ | 918 の出来事を主人公が片付けていれば、その街の勢力の見られ方を上げる。間隔が来たら情勢を1回進める（背景で LLM を1回） |

勢力が2つ以上書かれていない世界（統一された世界・勢力の話が無い世界）では台帳を作らない（DOC.md §2.1）。
"""

import os
import sys

from instantale_modloader import choices, frames, jobs, llm, modnpc, talk_summary, ui
from instantale_modloader import state as loader_state
from instantale_modloader.state import UNKNOWN_WORLD, WorldStore, world_key

from . import material, rules

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
ENABLED = False               # この世界で勢力を使う（ワールド個別で入れる）
GAIN_PER_STEP = 5             # 会話の判定 +1 あたりに上がる点
LOSS_PER_STEP = 8             # 会話の判定 -1 あたりに下がる点
INCIDENT_GAIN = 10            # 918 の出来事を主人公が片付けたとき、その街の勢力に上がる点
TREND_DAYS = 30               # 情勢を進める間隔（日）。0 で進めない
RUMOR_DAYS = 90               # 情勢の噂を会話に足しておく日数
ORGANIZE_NEW_WORLDS = False   # 新しく作る世界に組織を設ける（全世界共通。世界ごとの設定では効かない）
ADD_ORGANIZATIONS = False     # 設定に勢力が無い既存の世界に組織を設ける（ワールド個別で入れる）

SETTING_NAMES = ("ENABLED", "ADD_ORGANIZATIONS", "GAIN_PER_STEP", "LOSS_PER_STEP", "INCIDENT_GAIN",
                 "TREND_DAYS", "RUMOR_DAYS")

# ---- 設定にしない定数 ----------------------------------------------------
OWNER = "919_town_factions"
LOG_BASENAME = "town_factions.log"
#: 周回の控え（世界×主人公）。ワールド個別の設定とは別のフォルダに置く
#: （同じフォルダだと、周回の鍵へ移す処理が `<世界>.json` の設定を控えとして移してしまう）。
STATE_DIRNAME = "town_factions_ledger"
#: ワールド個別の設定（`tools\modtool.py` がフォルダ名から決める名前）。
SETTINGS_DIRNAME = "town_factions"
#: 918 の控え（読むだけ。918 の DOC.md「控えの形」）。
INCIDENTS_DIRNAME = "town_incidents"
STORE_ATTR = "_instantale_town_factions"
SETTINGS_STORE_ATTR = "_instantale_town_factions_settings"
MANAGER_EXTRACT = "mod_town_factions_extract"
MANAGER_STANDING = "mod_town_factions_standing"
MANAGER_TREND = "mod_town_factions_trend"
MANAGER_DESIGN = "mod_town_factions_design"
DAYS_TARGET = "__main__:InstantaleApp.elapse_days"
SEND_TARGET = "scripts.llm.llm_manager:send_request"
WORLD_MANAGER = "create_world_overview"       # 世界の生成の頼み（概要の有無によらずこの名前。GAME.md §2.27）
WORLD_SAVE_TARGET = "save_world_json:write_obfuscated_json_file"
WORLD_FILE = "world_data.json"
LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",)
TIMEOUT = 300
CHANGE_FIELD = "faction_change"
REASON_FIELD = "faction_reason"
RECONCILE_FIELD = "faction_reconcile"
REASON_CHARS = 80
#: 抽出の並びの件数の上限（ローカル LLM が書き足し続けないように。139 と同じ穴）。ローカルのときだけ付ける。
LIST_LIMITS = {"factions": 8, "towns": 40, "relations": 28, "standings": 8}

RUMOR_NOTE = "【各地の噂】{rumor}\nこれは各地に広まっている噂で、あなたも耳にしている。"

TALK_INSTRUCTION = """【追加の判定：勢力】
{name}は{faction}に属している。{faction}は{kind}{note}
要約とは別に、この会話での{player}の言動によって、{faction}の者たちから見た{player}の印象がどう動いたかを判定し、
faction_change に -2〜+2 の整数で答えること。
- 0: 変わらない。用件だけのやり取りや普通の雑談は 0
- +1 / +2: {faction}の考えや利益に沿う言動、{faction}への敬意や協力を示した
- -1 / -2: {faction}の考えや利益に反する言動、{faction}への敵意や侮辱を示した
{name}個人の好き嫌いではなく、{faction}の者として見た印象で判断すること。口先だけの媚びでは上げないこと。
faction_reason には判定の理由を1文で書くこと。
faction_reconcile は、下に指示が無ければ "false" にすること。
summary にはこの判定のことを書かないこと。"""
#: 敵視が外れなくなった勢力の相手と話すときだけ足す（DOC.md §2.12）。
RECONCILE_INSTRUCTION = """
{faction}の者たちは、{player}を許しがたい敵とみなしている。
この会話で{player}が、{faction}がその敵意を改めるに足る理由（誤解を解く確かな証し・償い・{faction}のためになした行い・筋の通った説得など）を示し、
{name}が{faction}の者として本当に納得したと言えるときだけ、faction_reconcile を "true" にすること。
謝罪や言い訳だけ、口先の約束、媚び、脅しでは "false" にすること。迷ったら "false"。"""


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None, "incidents": None, "worker": None, "busy": set(), "failed": set(),
                 "structures": {}}
        setattr(sys, STORE_ATTR, store)
    return store


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    quiet = ctx.logger(LOG_BASENAME, dedup=True)
    store = _store()
    store.setdefault("busy", set())
    store.setdefault("failed", set())
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, default=rules.empty_ledger,
                                                  write=write)
    worlds = store["worlds"].rebind(ctx, write)
    if store["incidents"] is None:
        store["incidents"] = loader_state.WorldStore(ctx, INCIDENTS_DIRNAME, own=False)
    incidents_store = store["incidents"].rebind(ctx)

    # ------------------------------------------------------------ ワールド個別の設定
    settings_store = getattr(sys, SETTINGS_STORE_ATTR, None)
    if not isinstance(settings_store, WorldStore):
        settings_store = WorldStore(ctx, SETTINGS_DIRNAME, default=dict, write=write)
        setattr(sys, SETTINGS_STORE_ATTR, settings_store)
    else:
        settings_store.rebind(ctx, write)
    # 注入のたびにモジュールは作り直され、一括設定を注入してからここへ来る（TECH.md §3.8）。
    base_settings = {name: globals()[name] for name in SETTING_NAMES}

    def refresh_world(app):
        """いまの世界の個別設定を読み直して反映する（332 と同じ形）。"""
        record = {}
        try:
            current = world_key(app) if app is not None else UNKNOWN_WORLD
            if isinstance(current, str) and current != UNKNOWN_WORLD:
                loaded = settings_store.load(current, fresh=True)
                record = loaded if isinstance(loaded, dict) else {}
        except Exception:
            ctx.log_exc("town factions: cannot read world settings")
        for name in SETTING_NAMES:
            default = base_settings[name]
            value = record.get(name, default)
            globals()[name] = value if type(value) is type(default) else default

    def enabled(app):
        refresh_world(app)
        return bool(ENABLED)

    # ------------------------------------------------------------ 型（構造化出力）
    def structure(name):
        """抽出・判定・情勢の返却型。作れなければ None。"""
        local = llm.is_local_runtime()
        known = store["structures"].get(name)
        module = llm.manager()
        if known is not None and known[0] is module and known[2] == local:
            return known[1]
        factory = getattr(module, "create_model", None) if module is not None else None
        if not callable(factory):
            return None
        pydantic = sys.modules.get("pydantic")
        field = getattr(pydantic, "Field", None)
        try:
            from typing import Annotated
        except ImportError:                 # pragma: no cover
            Annotated = None

        def listed(item, key):
            # 件数の上限は grammar で縛るローカルだけに付ける（139 と同じ穴。クラウドの型の対応は測っていない）。
            if local and callable(field) and Annotated is not None:
                return (Annotated[list[item], field(max_length=LIST_LIMITS[key])], ...)
            return (list[item], ...)

        try:
            if name == "extract":
                faction = factory("Faction", name=(str, ...), name_type=(str, ...), kind=(str, ...),
                                  note=(str, ...), evidence=(str, ...))
                town = factory("TownFaction", town_id=(str, ...), faction=(str, ...),
                               evidence=(str, ...))
                relation = factory("FactionRelation", a=(str, ...), b=(str, ...),
                                   stage=(int, ...), evidence=(str, ...))
                made = factory("FactionExtract", factions=listed(faction, "factions"),
                               towns=listed(town, "towns"), relations=listed(relation, "relations"),
                               peaceful=(str, ...))
            elif name == "standing":
                row = factory("FactionStanding", faction=(str, ...), stage=(int, ...),
                              title=(str, ...), reason=(str, ...))
                made = factory("FactionStandings", standings=listed(row, "standings"))
            elif name == "adjust":
                row = factory("FactionAdjust", faction=(str, ...), adjust=(int, ...),
                              title=(str, ...), reason=(str, ...))
                made = factory("FactionAdjusts", standings=listed(row, "standings"))
            elif name == "design":
                organization = factory("Organization", name=(str, ...), kind=(str, ...),
                                       note=(str, ...), towns=(list[str], ...))
                relation = factory("OrganizationRelation", a=(str, ...), b=(str, ...),
                                   stage=(int, ...))
                made = factory("OrganizationDesign", factions=listed(organization, "factions"),
                               relations=listed(relation, "relations"), peaceful=(str, ...))
            else:
                made = factory("FactionTrend", happened=(str, ...), a=(str, ...), b=(str, ...),
                               direction=(str, ...), rumor=(str, ...), reason=(str, ...))
        except Exception:
            ctx.log_exc("town factions: cannot build the structure {!r}".format(name))
            return None
        store["structures"][name] = (module, made, local)
        return made

    def ask(manager_name, messages, kind):
        shape = structure(kind)
        if shape is None:
            write("{}: no structure (the LLM is not ready)".format(kind))
            return None
        answer = llm.ask(ctx, manager_name, messages, timeout=TIMEOUT, structure=shape,
                         label="town factions", write=write)
        return answer if isinstance(answer, dict) else None

    # ------------------------------------------------------------ 台帳
    def load(key):
        return worlds.load(key)

    def save_data(app):
        data = getattr(app, "save_data_dict", None)
        return data if isinstance(data, dict) else {}

    def build(job):
        """台帳を作る（背景スレッド）。抽出 → 初回の判定。"""
        try:
            make(job)
        finally:
            store["busy"].discard(job["key"])

    def make(job):
        app, key = job["app"], job["key"]
        data = save_data(app)
        text = material.extract_material(data)
        towns = material.towns_of(data)
        extracted = ask(MANAGER_EXTRACT, material.extract_messages(text), "extract")
        day = ui.game_day(app)
        if extracted is None:
            with worlds.lock:
                bucket = load(key)
                bucket["status"], bucket["why"] = rules.FAILED, "the extraction did not answer"
                worlds.save(key)
            store["failed"].add(key)
            write("build: {} failed (no answer); will try again after the next load".format(key))
            return
        ledger, dropped = rules.build_ledger(extracted, text, [t[0] for t in towns], day=day)
        for line in dropped:
            write("build: dropped {}".format(line))
        if ledger["status"] == rules.NONE and ADD_ORGANIZATIONS:
            # 設定に勢力が無い世界に、組織を後から設ける（世界ごとの設定。DOC.md §2.11）
            designed = ask(MANAGER_DESIGN, material.design_messages(text), "design")
            if designed is None:
                write("build: {} the design did not answer; no factions this time".format(key))
            else:
                ledger = rules.build_designed_ledger(
                    designed, [t[0] for t in towns], day=day,
                    peaceful=llm.truthy(extracted.get("peaceful"), unknown=False))
                write("build: {} designed {} organization(s)".format(key, len(ledger["factions"])))
        if ledger["status"] == rules.READY:
            first_standing(app, key, data, ledger)
            ledger["trend"]["last_day"] = day
        with worlds.lock:
            bucket = load(key)
            bucket.clear()
            bucket.update(ledger)
            worlds.save(key)
        if ledger["status"] == rules.READY:
            write("build: {} ready: {}".format(key, describe(ledger)))
        else:
            write("build: {} no factions ({})".format(key, ledger["why"]))

    def first_standing(app, key, data, ledger):
        """最初の立ち位置。新しいキャラは出自だけから、途中導入は規則の土台を LLM が ±1 段階まで動かす。"""
        player = getattr(app, "player", None)
        records = rules.town_records(ui.area_history_of(player) or {})
        name = talk_summary.player_name(app)
        profile = getattr(player, "profile", "") or ""
        if rules.is_new_character(records):
            text = material.standing_material(data, ledger, player_name=name, profile=profile,
                                              lawfulness={}, achievements={}, life_log="")
            judged = ask(MANAGER_STANDING, material.standing_messages(text), "standing")
            rules.apply_initial_standing(ledger, judged)
            write("build: {} first standing from the background{}".format(
                key, "" if judged is not None else " (no answer; everyone starts neutral)"))
            return
        baseline = rules.baseline_scores(ledger, records)
        text = material.adjust_material(data, ledger, baseline, records, player_name=name,
                                        profile=profile, rules=rules)
        judged = ask(MANAGER_STANDING, material.adjust_messages(text), "adjust")
        rules.apply_adjusted_standing(ledger, baseline, judged)
        write("build: {} first standing from the deeds: {}{}".format(
            key, ", ".join("{} base {} {:+d}".format(fid, row.get("baseline"), row.get("adjust", 0))
                           for fid, row in ledger["standing"].items()),
            "" if judged is not None else " (no answer; the baseline stays)"))

    def describe(ledger):
        parts = []
        for faction in ledger["factions"]:
            row = ledger["standing"].get(faction["id"], {})
            towns = [t for t, r in ledger["towns"].items() if r.get("faction") == faction["id"]]
            parts.append("{} [{}] towns {} standing {} {}{}".format(
                faction["name"], faction["id"], towns, row.get("score"),
                rules.STANDING_NAMES[rules.standing_stage(row.get("score", 0))],
                " 「{}」".format(row["title"]) if row.get("title") else ""))
        return "; ".join(parts)

    def ensure(app):
        """台帳が無ければ作る仕事を積む。台帳（作ってあれば）を返す。"""
        if app is None or not enabled(app):
            return None
        key = worlds.playthrough(app)
        with worlds.lock:
            bucket = load(key)
            status = bucket.get("status")
        if status in (rules.READY, rules.NONE):
            return bucket
        # 積んである・作っている途中なら積まない。作れなかった周回は、読み込み直すまで試し直さない
        # （LLM が答えない間、会話のたびに聞き直さないように）。
        if key in store["busy"] or key in store["failed"]:
            return None
        store["busy"].add(key)
        write("build: {} queued".format(key))
        worker.enqueue({"kind": "build", "app": app, "key": key})
        return None

    # ------------------------------------------------------------ 情勢
    def trend(job):
        """情勢を1回進める（背景スレッド）。"""
        app, key, day = job["app"], job["key"], job["day"]
        with worlds.lock:
            ledger = load(key)
            if ledger.get("status") != rules.READY:
                return
            since = ledger["trend"].get("last_day")
            snapshot = {"factions": list(ledger["factions"]), "towns": dict(ledger["towns"]),
                        "relations": {k: dict(v) for k, v in ledger["relations"].items()},
                        "events": list(ledger["events"]), "standing": dict(ledger["standing"])}
        data = save_data(app)
        towns = material.towns_of(data)
        happened_here = material.incidents_since(
            incidents_store.load(key, fresh=True) or {}, since, towns)
        standings = ["- {}: {}".format(f["name"], rules.STANDING_NAMES[rules.standing_stage(
            snapshot["standing"].get(f["id"], {}).get("score", 0))]) for f in snapshot["factions"]]
        text = material.trend_material(data, snapshot, incidents=happened_here, standings=standings)
        days = day - since if isinstance(since, (int, float)) else TREND_DAYS
        answer = ask(MANAGER_TREND, material.trend_messages(text, days), "trend")
        with worlds.lock:
            ledger = load(key)
            ledger["trend"]["last_day"] = day
            if answer is None:
                worlds.save(key)
                write("trend: day {} no answer".format(day))
                return
            names = {f["name"]: f["id"] for f in ledger["factions"]}
            a, b = rules.faction_id_of(names, answer.get("a")), rules.faction_id_of(names, answer.get("b"))
            direction = rules.to_int(answer.get("direction"), -1, 1)
            # happened に出来事の文を書く答えがある（2026-10-11、Haiku 5.5）。"false" と書いたときだけ起きなかったとみなし、
            # 起きたかどうかは勢力の組と向きが読めるかで決める
            if not llm.truthy(answer.get("happened"), unknown=True) or a is None or b is None \
                    or a == b or not direction:
                worlds.save(key)
                write("trend: day {} nothing happened ({!r})".format(day, answer))
                return
            plan = rules.plan_trend(ledger, a, b, direction)
            rules.apply_trend(ledger, a, b, plan, day=day)
            rumor = str(answer.get("rumor") or "").strip()[:200]
            rules.add_event(ledger, {"day": day, "a": a, "b": b, "direction": direction,
                                     "before": plan["before"], "after": plan["after"],
                                     "rumor": rumor,
                                     "reason": str(answer.get("reason") or "").strip()[:200]})
            worlds.save(key)
        write("trend: day {} {}-{} {:+d} {} -> {} ({}) rumor: {}".format(
            day, a, b, direction, rules.RELATION_NAMES[plan["before"]],
            rules.RELATION_NAMES[plan["after"]], plan["why"], rumor))

    def run(job):
        (build if job["kind"] == "build" else trend)(job)

    worker = store["worker"] = jobs.rebind(
        store["worker"] or jobs.Worker(ctx, run, name="town_factions", label="town factions",
                                       write=write, key=lambda job: (job["kind"], job["key"])),
        ctx, run, write)

    # ------------------------------------------------------------ 相手の勢力
    def home_town(app, npc_id):
        """相手が住人・冒険者として名簿に載っている街。無ければ空。"""
        for area_id, area in (ui.world_areas(app) or {}).items():
            ids = list(getattr(area, "resident_npcs", None) or []) \
                + list(getattr(area, "adventurer_npcs", None) or [])
            if str(npc_id) in [str(x) for x in ids]:
                return str(area_id)
        return ""

    def faction_for(app, ledger, npc_id):
        """相手の勢力の id。名簿の街が無ければ、主人公がいま居る街の勢力。"""
        if not npc_id or modnpc.is_mod_npc(npc_id) or npc_id == "player":
            return None
        if npc_id in [str(x) for x in ui.party_member_ids(app)]:
            return None                     # 仲間は主人公を個人として知っている
        town = home_town(app, npc_id) or ui.area_id_of(ui.current_area(app))
        return rules.faction_of_town(ledger, town)

    # ------------------------------------------------------------ 会話の構えと噂
    def notes(info):
        app = info.get("app") or ui.find_app()
        ledger = ensure(app)
        if not ledger or ledger.get("status") != rules.READY:
            return None
        npc_id = str(info.get("npc_id") or "")
        player = talk_summary.player_name(app)
        lines = []
        faction_id = faction_for(app, ledger, npc_id)
        if faction_id:
            stance = rules.stance_text(ledger, faction_id, player)
            if stance:
                lines.append(stance)
                quiet("stance: {} ({}) {} -> {}".format(
                    info.get("site"), npc_id, faction_id, stance[:40]))
        day = ui.game_day(app)
        latest = (ledger.get("events") or [])[-1:]
        if latest and latest[0].get("rumor") and day is not None \
                and day - (latest[0].get("day") or 0) <= RUMOR_DAYS:
            lines.append(RUMOR_NOTE.format(rumor=latest[0]["rumor"]))
        return "\n\n".join(lines) or None

    modnpc.install(ctx, write=write)
    modnpc.register(OWNER, modnpc.ANY, notes=notes, write=write)

    # ------------------------------------------------------------ 会話の要約への相乗り
    def begin(talk):
        app = talk["app"]
        ledger = ensure(app)
        if not ledger or ledger.get("status") != rules.READY:
            return None
        faction_id = faction_for(app, ledger, talk["npc_id"])
        if not faction_id or not talk_summary.player_engaged(talk["messages"]):
            return None
        faction = rules.faction_by_id(ledger, faction_id)
        talk["faction"] = faction_id
        text = TALK_INSTRUCTION.format(
            name=talk["name"], player=talk["player"], faction=faction["name"],
            kind=faction.get("kind") or "勢力", note="。" + faction["note"] if faction.get("note") else "。")
        talk["locked"] = rules.is_locked(ledger, faction_id)
        if talk["locked"]:
            text += RECONCILE_INSTRUCTION.format(name=talk["name"], player=talk["player"],
                                                 faction=faction["name"])
        return text

    def settle(talk, answer):
        faction_id = talk.get("faction")
        if not faction_id:
            return
        if answer is None:
            write("judge: {} ({}) {} skipped ({})".format(talk["name"], talk["npc_id"], faction_id,
                                                          talk["status"]))
            return
        step = rules.to_int(answer.get(CHANGE_FIELD), -2, 2)
        reason = str(answer.get(REASON_FIELD) or "").strip()[:REASON_CHARS]
        if step is None:
            write("judge: {} ({}) unreadable change {!r}".format(
                talk["name"], talk["npc_id"], answer.get(CHANGE_FIELD)))
            return
        app = talk["app"]
        day = ui.game_day(app)
        key = worlds.playthrough(app)
        laws = ui.lawfulness_by_area(getattr(app, "player", None))
        # 敵視が外れない印は、印のある相手に聞いたときの "true" でだけ外す（DOC.md §2.12）
        reconciled = bool(talk.get("locked")) and llm.truthy(answer.get(RECONCILE_FIELD), unknown=False)
        with worlds.lock:
            ledger = load(key)
            if ledger.get("status") != rules.READY:
                return
            if reconciled:
                reconciled = rules.lift_lock(ledger, faction_id)
            gained_today = day is not None and ledger["gained"].get(faction_id) == day
            moves = rules.plan_talk(ledger, faction_id, step, gain_per_step=GAIN_PER_STEP,
                                    loss_per_step=LOSS_PER_STEP, gained_today=gained_today,
                                    ceiling=rules.gain_ceiling(ledger, faction_id, laws))
            rules.apply_moves(ledger, moves)
            if any(why == "gain" for _f, _b, _a, why in moves) and day is not None:
                ledger["gained"][faction_id] = day
            worlds.save(key)
        write("judge: {} ({}) {} step {:+d}{} {} ({})".format(
            talk["name"], talk["npc_id"], faction_id, step, " lock lifted" if reconciled else "",
            ", ".join("{} {} {}->{}".format(f, why, b, a) for f, b, a, why in moves) or "no move",
            reason))

    talk_summary.install(ctx, write)
    talk_summary.ride(OWNER, ctx, begin, settle,
                      {CHANGE_FIELD: int, REASON_FIELD: str, RECONCILE_FIELD: str})

    # ------------------------------------------------------------ 日数が進んだ後
    def credit_incidents(app, key, ledger):
        """918 の出来事を主人公が片付けていれば、その街の勢力の見られ方を上げる。"""
        bucket = incidents_store.load(key, fresh=True) or {}
        seen = ledger["trend"].setdefault("incidents", {})
        laws = ui.lawfulness_by_area(getattr(app, "player", None))
        for event in bucket.get("events") or []:
            if not isinstance(event, dict) or event.get("status") != "ended":
                continue
            event_id = str(event.get("id"))
            if event_id in seen:
                continue
            seen[event_id] = event.get("end_by")
            faction_id = rules.faction_of_town(ledger, event.get("area"))
            if event.get("end_by") != "player" or not faction_id or INCIDENT_GAIN <= 0:
                continue
            moves = rules.plan_gain(ledger, faction_id, INCIDENT_GAIN, "incident",
                                    ceiling=rules.gain_ceiling(ledger, faction_id, laws))
            rules.apply_moves(ledger, moves)
            write("incident: {} in {} solved by the player: {}".format(
                event.get("kind"), event.get("area"),
                ", ".join("{} {} {}->{}".format(f, why, b, a) for f, b, a, why in moves)))

    def after_days(app):
        ledger = ensure(app)
        if not ledger or ledger.get("status") != rules.READY:
            return
        key = worlds.playthrough(app)
        day = ui.game_day(app)
        with worlds.lock:
            ledger = load(key)
            credit_incidents(app, key, ledger)
            worlds.save(key)
            due = rules.trend_due(ledger["trend"].get("last_day"), day, TREND_DAYS)
        if due:
            write("trend: day {} queued".format(day))
            worker.enqueue({"kind": "trend", "app": app, "key": key, "day": day})

    @ctx.wrap(DAYS_TARGET, required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            after_days(self)
        except Exception:
            ctx.log_exc("town factions: after elapse_days failed")
        return result

    # ------------------------------------------------------------ ロード
    def make_load(target):
        @ctx.wrap(target, required=False, safe=True)
        def on_load(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            worlds.forget()
            store["failed"].clear()         # 作れなかった台帳は、読み込み直したらもう一度試す
            store["checked"] = None
            try:
                ensure(self)                # 読み込んだらすぐ作り始める（会話や日数の経過を待たない）
            except Exception:
                ctx.log_exc("town factions: cannot check the ledger after loading")
            return result
        return on_load

    for target in LOAD_TARGETS:
        make_load(target)

    def refresh_choice_buttons(app, _buttons):
        """読み込みの包みが読み込みを拾えなかったときの保険。

        起動直後の注入が2回入り、2回目の掛け替えと世界の読み込みが重なると、
        読み込みの包みが呼ばれないまま遊べる画面になる（2026-10-11 の実機）。
        選択肢を組み直すたびに、まだ見ていない周回なら台帳を確かめる。
        """
        key = worlds.playthrough(app)
        if key == UNKNOWN_WORLD or key == store.get("checked"):
            return
        store["checked"] = key
        ensure(app)

    choices.provide(ctx, None, refresh=refresh_choice_buttons, owner=OWNER)

    # ------------------------------------------------------------ 世界を作るとき
    def install_generate(target):
        @ctx.wrap(target, required=False)
        def send_request(orig, *args, **kwargs):
            """世界の生成の頼みに【勢力】の段を足す（設定を入れているときだけ）。"""
            manager_name = args[0] if args else kwargs.get("manager_name")
            if not ORGANIZE_NEW_WORLDS or manager_name != WORLD_MANAGER:
                return orig(*args, **kwargs)
            try:
                message = args[1] if len(args) > 1 else kwargs.get("message")
                changed = False
                if isinstance(message, list):
                    copied = []
                    for turn in message:
                        text = turn.get("content") if isinstance(turn, dict) else None
                        added = material.with_world_organizations(text) \
                            if isinstance(turn, dict) and turn.get("role") == "user" else None
                        if added is not None and not changed:
                            turn, changed = dict(turn, content=added), True
                        copied.append(turn)
                    if changed:
                        if len(args) > 1:
                            args = (args[0], copied) + tuple(args[2:])
                        else:
                            kwargs = dict(kwargs, message=copied)
                        store["organized"] = True
                        write("generate: asked the world generation for 2-4 organizations")
                if not changed:
                    write("generate: WARN the world generation prompt had no place for the "
                          "organizations (left untouched)")
            except Exception:
                ctx.log_exc("town factions: cannot add the organizations to the world generation")
            return orig(*args, **kwargs)

    llm.watch_aliases(ctx, [SEND_TARGET], install_generate, label="town factions")

    @ctx.wrap(WORLD_SAVE_TARGET, required=False, safe=True)
    def write_world(orig, *args, **kwargs):
        """組織を設けて作った世界は、919 の世界ごとの設定を有効にしておく。"""
        result = orig(*args, **kwargs)
        if not store.get("organized"):
            return result
        try:
            path = frames.arg(args, kwargs, "file_path", 0)
            data = frames.arg(args, kwargs, "data", 1)
            if os.path.basename(str(path or "")) == WORLD_FILE and isinstance(data, dict):
                name = (data.get("world_data") or {}).get("name")
                if isinstance(name, str) and name:
                    store["organized"] = False
                    record = settings_store.load(name)
                    record["ENABLED"] = True
                    settings_store.save(name)
                    write("generate: turned factions on for the new world {!r}".format(name))
        except Exception:
            ctx.log_exc("town factions: cannot turn factions on for the new world")
        return result

    def initial():
        try:
            ensure(ui.find_app())
        except Exception:
            ctx.log_exc("town factions: cannot check the ledger after injection")

    ctx.on_ready(initial)

    ctx.log("town factions: installed (enabled {}, gain {}/step, loss {}/step, incident {}, "
            "trend every {} day(s), rumor {} day(s), organize new worlds {}, state {})".format(
                ENABLED, GAIN_PER_STEP, LOSS_PER_STEP, INCIDENT_GAIN, TREND_DAYS, RUMOR_DAYS,
                ORGANIZE_NEW_WORLDS,
                worlds.dir_path()))
