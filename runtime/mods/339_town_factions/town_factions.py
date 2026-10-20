# -*- coding: utf-8 -*-
"""街の勢力。

世界の設定に書かれた勢力を台帳に起こし、勢力ごとに主人公の見られ方を持つ。
見られ方は NPC の会話の構えに出る（どこでも誰にでも同じに好かれるのを避ける）。
遊び方と決まりは DOC.md、決めた仕様と根拠は VERIFICATION.md §3.98。

| 場面 | すること |
|---|---|
| 世界を読み込んだ・会話・日数が進んだ | 台帳が無ければ作る（背景で LLM を2回。抽出と初回の判定） |
| 会話の頼み（5関数） | 相手の街の勢力の構えと、最近の情勢の噂を足す（ローダの `modnpc` の `notes`） |
| 会話を閉じたときの要約 | 見られ方がどう動いたかを相乗りで判定させる（ローダの `talk_summary`） |
| 台帳ができた・住人が増えた | 勢力の街の住人の立場（一員・従う住人・面従腹背・無関心・他の勢力寄り）を街ごとに決める（背景で LLM を街ごとに1回） |
| 日数が進んだ | 338 の出来事を主人公が片付けていれば、その街の勢力の見られ方を上げる。間隔が来たら情勢を1回進める（背景で LLM を1回） |

勢力が2つ以上書かれていない世界（統一された世界・勢力の話が無い世界）では台帳を作らない（VERIFICATION.md §3.98「勢力は設定に書かれたものだけ」）。
"""

import json
import os
import random
import sys

from instantale_modloader import choices, frames, hudicons, jobs, llm, modnpc, talk_summary, ui
from instantale_modloader import state as loader_state
from instantale_modloader.state import UNKNOWN_WORLD, WorldStore, world_key

from . import material, rules, view, window

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
ENABLED = False               # この世界で勢力を使う（ワールド個別で入れる）
GAIN_PER_STEP = 5             # 会話の判定 +1 あたりに上がる点
LOSS_PER_STEP = 8             # 会話の判定 -1 あたりに下がる点
INCIDENT_GAIN = 10            # 338 の出来事を主人公が片付けたとき、その街の勢力に上がる点
TREND_DAYS = 30               # 情勢を進める間隔（日）。0 で進めない
TREND_SPREAD_DAYS = 15        # 間隔に足すランダムな日数の上限（情勢が動くたびに 0〜これを引き直す）
RUMOR_DAYS = 90              # 情勢の噂を会話に足しておく日数
ORGANIZE_NEW_WORLDS = False   # 新しく作る世界に組織を設ける（全世界共通。世界ごとの設定では効かない）
ADD_ORGANIZATIONS = False     # 設定に勢力が無い既存の世界に組織を設ける（ワールド個別で入れる）

SETTING_NAMES = ("ENABLED", "ADD_ORGANIZATIONS", "GAIN_PER_STEP", "LOSS_PER_STEP", "INCIDENT_GAIN",
                 "TREND_DAYS", "TREND_SPREAD_DAYS", "RUMOR_DAYS")

# ---- 設定にしない定数 ----------------------------------------------------
OWNER = "339_town_factions"
LOG_BASENAME = "town_factions.log"
#: 周回の控え（世界×主人公）。ワールド個別の設定とは別のフォルダに置く
#: （同じフォルダだと、周回の鍵へ移す処理が `<世界>.json` の設定を控えとして移してしまう）。
STATE_DIRNAME = "town_factions_ledger"
#: ワールド個別の設定（`tools\modtool.py` がフォルダ名から決める名前）。
SETTINGS_DIRNAME = "town_factions"
#: 338 の控え（読むだけ。VERIFICATION.md §3.97「控えの形」）。
INCIDENTS_DIRNAME = "town_incidents"
STORE_ATTR = "_instantale_town_factions"
SETTINGS_STORE_ATTR = "_instantale_town_factions_settings"
MANAGER_EXTRACT = "mod_town_factions_extract"
MANAGER_STANDING = "mod_town_factions_standing"
MANAGER_TREND = "mod_town_factions_trend"
MANAGER_DESIGN = "mod_town_factions_design"
MANAGER_PEOPLE = "mod_town_factions_people"
DAYS_TARGET = "__main__:InstantaleApp.elapse_days"
SEND_TARGET = "scripts.llm.llm_manager:send_request"
# 世界の生成の頼みは `llm_manager_world_generate` から送られる。main_026 から部品ごとに別の関数になり、
# `llm_manager` だけを包む形では届かなくなった（VERIFICATION.md §3.100）。同じ関数なら二重には包まない（llm.watch_aliases）。
GENERATE_TARGETS = (SEND_TARGET, "scripts.llm.llm_manager_world_generate:send_request")
WORLD_MANAGER = "create_world_overview"       # 世界の生成の頼み（概要の有無によらずこの名前。GAME.md §2.27）
WORLD_SAVE_TARGET = "save_world_json:write_obfuscated_json_file"
WORLD_FILE = "world_data.json"
LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",)
TIMEOUT = 300
CHANGE_FIELD = "faction_change"
REASON_FIELD = "faction_reason"
RECONCILE_FIELD = "faction_reconcile"
REASON_CHARS = 80
#: 画面左下の MOD 用のボタンの一覧に出す行（ローダの `hudicons`）。並びは 338 の「各地の出来事」の後。
ICON_KEY = "town_factions"
ICON_FILES = ("icon_flag.png", "icon_flag_hover.png", "icon_flag_click.png")
ICON_ORDER = 20
#: 情勢の間隔に足す日数を引く乱数。ゲームが使う `random` の並びを動かさないように別に持つ。
RNG = random.Random()
#: 住人の立場を1回に聞く人数（街の住人がこれより多ければ分けて聞く）。
PEOPLE_BATCH = 20
#: 抽出の並びの件数の上限（ローカル LLM が書き足し続けないように。139 と同じ穴）。ローカルのときだけ付ける。
LIST_LIMITS = {"factions": 8, "towns": 40, "relations": 28, "standings": 8, "people": PEOPLE_BATCH}

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
#: 敵対が外れなくなった勢力の相手と話すときだけ足す（VERIFICATION.md §3.98「手配と敵対の上限」）。
RECONCILE_INSTRUCTION = """
{faction}の者たちは、{player}を許しがたい敵とみなしている。
この会話で{player}が、{faction}がその敵意を改めるに足る理由（誤解を解く確かな証し・償い・{faction}のためになした行い・筋の通った説得など）を示し、
{name}が{faction}の者として本当に納得したと言えるときだけ、faction_reconcile を "true" にすること。
謝罪や言い訳だけ、口先の約束、媚び、脅しでは "false" にすること。迷ったら "false"。"""


def json_copy(value):
    """台帳の写し（窓に渡す。鍵の外で読んでも、背景の仕事の書き込みと混ざらない）。"""
    return json.loads(json.dumps(value, ensure_ascii=False))


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
            elif name == "people":
                # 理由を先に書かせる（役の後に理由を書かせると、理由と役が食い違った。2026-10-14、Haiku 5.5）
                person = factory("FactionPerson", id=(str, ...), reason=(str, ...), role=(str, ...),
                                 faction=(str, ...), open=(str, ...))
                made = factory("FactionPeople", people=listed(person, "people"))
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

    def same_world(app, key):
        """背景の仕事を積んだときの周回のままか（LLM を待つ間に別のセーブを読んでいないか）。"""
        return loader_state.playthrough_key(app) == key

    def ended_incidents(key):
        """338 の控えで、もう終わっている出来事 `{id: 終えた人}`（台帳を作る前に終わった分は数えない）。"""
        bucket = incidents_store.load(key, fresh=True) or {}
        return {str(event.get("id")): event.get("end_by") for event in bucket.get("events") or []
                if isinstance(event, dict) and event.get("status") == "ended"}

    def build(job):
        """台帳を作る（背景スレッド）。抽出 → 初回の判定。"""
        try:
            make(job)
        finally:
            store["busy"].discard(job["key"])

    def make(job):
        app, key = job["app"], job["key"]
        if not same_world(app, key):
            write("build: {} skipped (another save was loaded)".format(key))
            return
        data = save_data(app)
        text = material.extract_material(data)
        towns = material.towns_of(data)
        day = ui.game_day(app)
        extracted = ask(MANAGER_EXTRACT, material.extract_messages(text), "extract")
        if not same_world(app, key):
            # 待つ間に別のセーブを読んだ。いまの app の主人公や日付で書かない（その世界を読み込み直したら作り直す）
            write("build: {} dropped (another save was loaded while asking)".format(key))
            return
        if extracted is None:
            with worlds.lock:
                bucket = load(key)
                bucket["status"], bucket["why"] = rules.FAILED, "the extraction did not answer"
                worlds.save(key)
            store["failed"].add(key)
            write("build: {} failed (no answer); will try again after the next load".format(key))
            return
        ledger, dropped = rules.build_ledger(extracted, text, [t[0] for t in towns], day=day,
                                             town_names={t[0]: t[1] for t in towns})
        for line in dropped:
            write("build: dropped {}".format(line))
        if ledger["status"] == rules.NONE and ADD_ORGANIZATIONS:
            # 設定に勢力が無い世界に、組織を後から設ける（世界ごとの設定。VERIFICATION.md §3.98「設定に勢力が無い既存の世界に組織を設ける」）
            designed = ask(MANAGER_DESIGN, material.design_messages(text), "design")
            if not same_world(app, key):
                write("build: {} dropped (another save was loaded while asking)".format(key))
                return
            if designed is None:
                # 答えが無いのは「勢力なし」ではない。読み込み直したら試し直す
                with worlds.lock:
                    bucket = load(key)
                    bucket["status"], bucket["why"] = rules.FAILED, "the design did not answer"
                    worlds.save(key)
                store["failed"].add(key)
                write("build: {} the design did not answer; will try again after the next load".format(key))
                return
            ledger = rules.build_designed_ledger(
                designed, [t[0] for t in towns], day=day,
                peaceful=llm.truthy(extracted.get("peaceful"), unknown=False))
            ledger["organize_asked"] = True
            write("build: {} designed {} organization(s)".format(key, len(ledger["factions"])))
        if ledger["status"] == rules.READY:
            if not first_standing(app, key, data, ledger):
                return
            ledger["trend"]["last_day"] = day
            ledger["trend"]["extra"] = roll_extra()
            # 台帳より前に終わった 338 の出来事は数えない（途中導入の土台が功績から出している）
            ledger["trend"]["incidents"] = ended_incidents(key)
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
        """最初の立ち位置。新しいキャラは出自だけから、途中導入は規則の土台を LLM が ±1 段階まで動かす。

        待つ間に別のセーブを読んだら偽（台帳を書かない）。
        """
        player = getattr(app, "player", None)
        records = rules.town_records(ui.area_history_of(player) or {})
        name = talk_summary.player_name(app)
        profile = getattr(player, "profile", "") or ""
        if rules.is_new_character(records):
            text = material.standing_material(data, ledger, player_name=name, profile=profile,
                                              lawfulness={}, achievements={}, life_log="")
            judged = ask(MANAGER_STANDING, material.standing_messages(text), "standing")
            if not same_world(app, key):
                write("build: {} dropped (another save was loaded while asking)".format(key))
                return False
            rules.apply_initial_standing(ledger, judged)
            write("build: {} first standing from the background{}".format(
                key, "" if judged is not None else " (no answer; everyone starts neutral)"))
            return True
        baseline = rules.baseline_scores(ledger, records)
        text = material.adjust_material(data, ledger, baseline, records, player_name=name,
                                        profile=profile, rules=rules)
        judged = ask(MANAGER_STANDING, material.adjust_messages(text), "adjust")
        if not same_world(app, key):
            write("build: {} dropped (another save was loaded while asking)".format(key))
            return False
        rules.apply_adjusted_standing(ledger, baseline, judged)
        write("build: {} first standing from the deeds: {}{}".format(
            key, ", ".join("{} base {} {:+d}".format(fid, row.get("baseline"), row.get("adjust", 0))
                           for fid, row in ledger["standing"].items()),
            "" if judged is not None else " (no answer; the baseline stays)"))
        return True

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
        if key == UNKNOWN_WORLD:
            return None                     # 世界をまだ読んでいない（タイトル画面の注入など）
        day = ui.game_day(app)
        with worlds.lock:
            bucket = load(key)
            status = bucket.get("status")
            if status == rules.READY:
                rewound = rules.rewind(bucket, day)
                if rewound == "rebuild":
                    bucket.clear()
                    bucket.update(rules.empty_ledger())
                    status = None
                    write("rewind: {} the ledger was made after day {}; building it again".format(key, day))
                elif rewound:
                    write("rewind: {} forgot what happened after day {} (older save)".format(key, day))
                if rewound:
                    worlds.save(key)
            if status == rules.READY:
                # 手で直した台帳の書き漏らし（組・見られ方）を埋める（rules.repair）
                fixed = rules.repair(bucket)
                if fixed:
                    worlds.save(key)
                    write("repair: {} {}".format(key, "; ".join(fixed)))
        if status == rules.READY:
            queue_people(app, key, bucket)
            return bucket
        # 勢力の無い世界。組織を設ける設定を後から入れたなら、設計を聞くために作り直す
        if status == rules.NONE and not (ADD_ORGANIZATIONS and not bucket.get("organize_asked")):
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
        if not same_world(app, key):
            write("trend: {} skipped (another save was loaded)".format(key))
            return
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
        if not same_world(app, key):
            # 待つ間に別のセーブを読んだ。前の周回の台帳は動かさない（間隔が来ていれば次の日数送りでまた進める）
            write("trend: {} dropped (another save was loaded while asking)".format(key))
            return
        with worlds.lock:
            ledger = load(key)
            ledger["trend"]["last_day"] = day
            ledger["trend"]["extra"] = roll_extra()
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
            if plan is None:
                worlds.save(key)
                write("trend: day {} no relation between {} and {}".format(day, a, b))
                return
            rules.apply_trend(ledger, a, b, plan, day=day)
            rumor = str(answer.get("rumor") or "").strip()[:200]
            rules.add_event(ledger, {"day": day, "a": a, "b": b, "direction": direction,
                                     "before": plan["before"], "after": plan["after"],
                                     "rumor": rumor,
                                     "reason": str(answer.get("reason") or "").strip()[:200]})
            worlds.save(key)
        if rumor:
            hudicons.notify(ICON_KEY, playthrough=key)
        write("trend: day {} {}-{} {:+d} {} -> {} ({}) rumor: {}".format(
            day, a, b, direction, rules.RELATION_NAMES[plan["before"]],
            rules.RELATION_NAMES[plan["after"]], plan["why"], rumor))

    # ------------------------------------------------------------ 住人の立場
    def queue_people(app, key, ledger):
        """立場を決めていない住人がいれば、決める仕事を積む（周回に1つ。住人の数が変わったときだけ数え直す）。"""
        npcs = save_data(app).get("npcs")
        count = len(npcs) if isinstance(npcs, dict) else 0
        mark = (key, count, len(ledger.get("people") or {}))
        if store.get("people_checked") == mark:
            return
        store["people_checked"] = mark
        failed = store.setdefault("people_failed", set())
        pending = {town: ids for town, ids in
                   rules.unjudged(ledger, material.residents_of(save_data(app))).items()
                   if (key, town) not in failed}
        if pending:
            write("people: {} queued {} town(s) {}".format(
                key, len(pending), {town: len(ids) for town, ids in pending.items()}))
            worker.enqueue({"kind": "people", "app": app, "key": key})

    def judge_people(job):
        """勢力の街ごとに、住人の立場を LLM に決めさせる（背景スレッド）。"""
        app, key = job["app"], job["key"]
        failed = store.setdefault("people_failed", set())
        while same_world(app, key):
            data = save_data(app)
            with worlds.lock:
                ledger = load(key)
                if ledger.get("status") != rules.READY:
                    return
                snapshot = json_copy(ledger)
            pending = [(town, ids) for town, ids in sorted(
                rules.unjudged(snapshot, material.residents_of(data)).items())
                if (key, town) not in failed]
            if not pending:
                return
            town, ids = pending[0]
            ids = ids[:PEOPLE_BATCH]
            towns = material.towns_of(data)
            answer = ask(MANAGER_PEOPLE, material.people_messages(
                material.people_material(data, snapshot, towns, town, ids)), "people")
            if not same_world(app, key):
                write("people: {} dropped (another save was loaded while asking)".format(key))
                return
            if answer is None:
                # 答えが無い街は、読み込み直すまで聞き直さない（会話のたびに聞き直さないように）
                failed.add((key, town))
                write("people: {} town {} no answer; will try again after the next load".format(key, town))
                continue
            rows, problems = rules.read_people(answer, snapshot, town, ids)
            with worlds.lock:
                ledger = load(key)
                if ledger.get("status") != rules.READY:
                    return
                ledger.setdefault("people", {}).update(rows)
                worlds.save(key)
            counts = {}
            for row in rows.values():
                counts[row["role"]] = counts.get(row["role"], 0) + 1
            write("people: {} town {} {}{}".format(key, town, counts,
                                                   " problems {}".format(problems) if problems else ""))

    def run(job):
        {"build": build, "trend": trend, "people": judge_people}[job["kind"]](job)

    worker = store["worker"] = jobs.rebind(
        store["worker"] or jobs.Worker(ctx, run, name="town_factions", label="town factions",
                                       write=write, key=lambda job: (job["kind"], job["key"])),
        ctx, run, write)

    # ------------------------------------------------------------ 相手の勢力
    def home_town(app, npc_id):
        """相手の街。住人・冒険者として名簿に載っている街、無ければ生まれた街（店や施設の主は名簿に載らない）。"""
        for area_id, area in (ui.world_areas(app) or {}).items():
            ids = list(getattr(area, "resident_npcs", None) or []) \
                + list(getattr(area, "adventurer_npcs", None) or [])
            if str(npc_id) in [str(x) for x in ids]:
                return str(area_id)
        return material.home_of(save_data(app), npc_id)

    def faction_for(app, ledger, npc_id):
        """相手の `(勢力の id, 立場)`。勢力と関わらない相手は `(None, None)`。

        街は名簿か生まれた街、どちらも無ければ主人公がいま居る街。立場は台帳の住人の立場
        （判定前・その街の住人でない相手は従う住人。VERIFICATION.md §3.98「住人の立場」）。
        """
        if not npc_id or modnpc.is_mod_npc(npc_id) or npc_id == "player":
            return None, None
        if npc_id in [str(x) for x in ui.party_member_ids(app)]:
            return None, None               # 仲間は主人公を個人として知っている
        home = home_town(app, npc_id)
        town_faction = rules.faction_of_town(ledger, home or ui.area_id_of(ui.current_area(app)))
        row = rules.person_of(ledger, npc_id)
        if row is not None and row.get("town") != home:
            row = None                      # 別の街で決めた立場（住まいが変わった）
        if row is None:
            return (town_faction, rules.FOLLOWER) if town_faction else (None, None)
        faction_id, role = rules.role_for(ledger, npc_id, town_faction)
        return (faction_id, role) if faction_id else (None, None)

    # ------------------------------------------------------------ 会話の構えと噂
    def notes(info):
        app = info.get("app") or ui.find_app()
        ledger = ensure(app)
        if not ledger or ledger.get("status") != rules.READY:
            return None
        npc_id = str(info.get("npc_id") or "")
        player = talk_summary.player_name(app)
        lines = []
        faction_id, role = faction_for(app, ledger, npc_id)
        if faction_id:
            stance = rules.stance_text(ledger, faction_id, player, role)
            if stance:
                lines.append(stance)
                quiet("stance: {} ({}) {} {} -> {}".format(
                    info.get("site"), npc_id, faction_id, role, stance[:40]))
        day = ui.game_day(app)
        latest = (ledger.get("events") or [])[-1:]
        if latest and latest[0].get("rumor") and day is not None \
                and 0 <= day - (latest[0].get("day") or 0) <= RUMOR_DAYS:
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
        faction_id, role = faction_for(app, ledger, talk["npc_id"])
        # 勢力の者として主人公を見ている相手（一員・他の勢力寄り）との会話だけが見られ方を動かす
        if not faction_id or role not in rules.JUDGING_ROLES                 or not talk_summary.player_engaged(talk["messages"]):
            return None
        faction = rules.faction_by_id(ledger, faction_id)
        if faction is None:
            return None
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
        # 敵対が外れない印は、印のある相手に聞いたときの "true" でだけ外す（VERIFICATION.md §3.98「手配と敵対の上限」）
        reconciled = bool(talk.get("locked")) and llm.truthy(answer.get(RECONCILE_FIELD), unknown=False)
        with worlds.lock:
            ledger = load(key)
            if ledger.get("status") != rules.READY:
                return
            if reconciled:
                reconciled = rules.lift_lock(ledger, faction_id)
            if reconciled:
                score = (ledger["standing"].get(faction_id) or {}).get("score", 0)
                rules.add_history(ledger, {"day": day, "faction": faction_id, "before": score,
                                           "after": score, "kind": rules.RECONCILE, "by": None,
                                           "reason": reason, "incident": ""})
            gained_today = day is not None and ledger["gained"].get(faction_id) == day
            moves = rules.plan_talk(ledger, faction_id, step, gain_per_step=GAIN_PER_STEP,
                                    loss_per_step=LOSS_PER_STEP, gained_today=gained_today,
                                    ceiling=rules.gain_ceiling(ledger, faction_id, laws))
            rules.apply_moves(ledger, moves)
            _written, changed = rules.record_moves(ledger, moves, day=day, kind=rules.TALK, reason=reason)
            if changed or reconciled:
                hudicons.notify(ICON_KEY, playthrough=key)
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
        """338 の出来事を主人公が片付けていれば、その街の勢力の見られ方を上げる。"""
        bucket = incidents_store.load(key, fresh=True) or {}
        seen = ledger["trend"].setdefault("incidents", {})
        # 古いセーブを読んで 338 が進行中へ戻した出来事は、数えた印を外す（もう一度片付けたら数える）
        live = {str(event.get("id")) for event in bucket.get("events") or []
                if isinstance(event, dict) and event.get("status") != "ended"}
        for event_id in [event_id for event_id in seen if event_id in live]:
            seen.pop(event_id, None)
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
            name = str((event.get("text") or {}).get("name") or "出来事")
            _written, changed = rules.record_moves(
                ledger, moves, day=ui.game_day(app), kind=rules.INCIDENT,
                incident="{}の{}".format(event.get("area_name") or "街", name))
            if changed:
                hudicons.notify(ICON_KEY, playthrough=key)
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
            trend = ledger["trend"]
            if type(trend.get("extra")) is not int:
                trend["extra"] = roll_extra()   # 版4 までの台帳（足す日数を持っていない）
            worlds.save(key)
            due = rules.trend_due(trend.get("last_day"), day, TREND_DAYS,
                                  extra=min(trend["extra"], TREND_SPREAD_DAYS))
        if due:
            write("trend: day {} queued".format(day))
            worker.enqueue({"kind": "trend", "app": app, "key": key, "day": day})

    def roll_extra():
        """次の情勢までの間隔に足す日数（0〜`TREND_SPREAD_DAYS`）。ゲームの乱数の並びは動かさない。"""
        return RNG.randint(0, max(0, int(TREND_SPREAD_DAYS)))

    @ctx.wrap(DAYS_TARGET, required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            after_days(self)
        except Exception:
            ctx.log_exc("town factions: after elapse_days failed")
        return result

    # ------------------------------------------------------------ 勢力と情勢の窓
    # 絵のパスは apply() の間に組む（`ctx.mod_dir` はフックの中では読めない）。
    icon_dir = getattr(ctx, "mod_dir", None) or os.path.dirname(os.path.abspath(__file__))
    icon_images = tuple(os.path.join(icon_dir, name) for name in ICON_FILES)

    def close_view():
        current = store.get("view")
        store["view"] = None
        if current is not None:
            try:
                current.dismiss()
            except Exception:
                ctx.log_exc("town factions: could not close the window")

    def view_model(app):
        key = worlds.playthrough(app)
        with worlds.lock:
            ledger = json_copy(load(key))
        areas = ui.world_areas(app) or {}
        names = {str(area_id): str(getattr(area, "name", "") or area_id) for area_id, area in areas.items()}
        return view.build(ledger, day=ui.game_day(app), here=ui.area_id_of(ui.current_area(app)),
                          area_names=names, laws=ui.lawfulness_by_area(getattr(app, "player", None)))

    def toggle_view(app):
        if store.get("view") is not None:
            close_view()
            return
        if app is None:
            return
        ensure(app)
        try:
            store["view"] = window.open_window(app, view_model(app), write=write,
                                               on_dismiss=lambda: store.update({"view": None}))
        except Exception:
            store["view"] = None
            ctx.log_exc("town factions: could not open the window")

    def listed(app):
        """一覧に「勢力と情勢」の行を出すか（いまの世界で有効か）。ローダが並べるたびに聞く。

        世界ごとの設定はファイルから読むので、注入の世代と周回ごとに1回だけ読んで覚える（読み込み直したら読み直す）。
        """
        if app is None:
            return False
        key = worlds.playthrough(app)
        if key == UNKNOWN_WORLD:
            return False
        # 覚えは注入の世代ごと（設定を変えるとローダが注入し直すので、そのときに読み直す）
        stamp = (getattr(ctx, "generation", None), key)
        known = store.get("listed")
        if not (isinstance(known, tuple) and known[0] == stamp):
            known = (stamp, enabled(app))
            store["listed"] = known
        return known[1]

    hudicons.add(ctx, ICON_KEY, toggle_view, images=icon_images, order=ICON_ORDER, label=window.TITLE,
                 write=write, visible=listed)

    # ------------------------------------------------------------ ロード
    def make_load(target):
        @ctx.wrap(target, required=False, safe=True)
        def on_load(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            worlds.forget()
            store["failed"].clear()         # 作れなかった台帳は、読み込み直したらもう一度試す
            store.setdefault("people_failed", set()).clear()    # 答えの無かった街の住人も聞き直す
            store["people_checked"] = None
            store["checked"] = None
            store["listed"] = None          # 一覧の行を出すかは、次に聞かれたときに読み直す
            try:
                close_view()
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
            if manager_name == WORLD_MANAGER:
                # 印は世界を作るたびに付け直す（前の生成が途中で止まった印を、次に書く世界へ持ち越さない）
                store["organized"] = False
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

    llm.watch_aliases(ctx, list(GENERATE_TARGETS), install_generate, label="town factions")

    @ctx.wrap(WORLD_SAVE_TARGET, required=False, safe=True)
    def write_world(orig, *args, **kwargs):
        """組織を設けて作った世界は、339 の世界ごとの設定を有効にしておく。

        有効にするのは、まだ無かった `world_data.json` を書いたとき（新しく作った世界）だけ。
        遊んでいる間の保存も同じ関数を通るので、生成が途中で止まった後に別の世界を遊んでも、その世界を有効にしない。
        """
        path = frames.arg(args, kwargs, "file_path", 0) if store.get("organized") else None
        try:
            fresh = bool(path) and not os.path.exists(str(path))
        except Exception:
            fresh = False
        result = orig(*args, **kwargs)
        if not store.get("organized") or not fresh:
            return result
        try:
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
            "trend every {}+0..{} day(s), rumor {} day(s), organize new worlds {}, state {})".format(
                ENABLED, GAIN_PER_STEP, LOSS_PER_STEP, INCIDENT_GAIN, TREND_DAYS, TREND_SPREAD_DAYS,
                RUMOR_DAYS, ORGANIZE_NEW_WORLDS,
                worlds.dir_path()))
