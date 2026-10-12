# -*- coding: utf-8 -*-
"""街に盗賊や疫病などの出来事が起き、放っておくと悪化する。

日数が進むと、訪れたことのあるギルドのある街に、表（`incidents.KINDS`）の出来事が起きることがある。
放っておくと 兆し → 悪化 → 深刻 と進み、最後は自然に収まる。その街の掲示板の依頼で片付けられ、
`911_rival_adventurer` のライバルが先に片付けることもある。
仕様は DOC.md、控えの形は他の MOD との取り決め（DOC.md「控えの形」）。

##### ゲームのどこに触るか

- 値段はローダの関所に段を置く（品は `prices.adjust`、宿代は `prices.adjust_inn`）。どちらも保存の前に外れる
- 会話には `modnpc` の窓口から文を足す。情景描写は narrator の `player_profile` に素材として足す（`317_` の形）
- 街の案内文（`Area.descriptions`）と依頼の難易度には触らない（`321_` と `318_` が持つ）
- 依頼は、その街の掲示板を開いたときにゲーム自身の生成で作らせる（`336_` の裏の仕事と同じく、
  `random_quest_generator` の `area_description` に足す）。自然に収まったら、残った依頼を世界から片付ける
  （`ui.remove_quest`。セーブの辞書とダンジョンの土地まで）
- 呼び名や情景の文は、発生のときに LLM に1回だけ書かせる（背景の待ち行列。`911_` の登場の語りと同じ形）
- 状況を見る窓は、画面左下のゲームのアイコンの列に足したアイコンから開く（ローダの `hudicons`）
"""

import os
import random
import sys
import time

from instantale_modloader import (choices, frames, hudicons, jobs, llm, modnpc, prices,
                                  state as loader_state, ui)

from . import incidents

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
ROLL_DAYS = 30                 # 抽選の間隔（日）
ROLL_PERCENT = 25              # 抽選で起きる確率（%）
MAX_ACTIVE = 2                 # 世界で同時に起きる数の上限
REST_DAYS = 180                # 収まった街が次に起きるまでの休み（日）
STAGE_DAYS = 60                # 段が上がる間隔（日）
SETTLE_DAYS = 90               # 3段目から自然に収まるまで（日）
STEPS_PER_ELAPSE = 1           # 1回の日数送りで上がる段の上限
PRICE_STRENGTH_PERCENT = 100   # 値段の効き目の強さ（表の率に掛ける %）
SIZE_WEIGHTING = True          # 街の規模で起こりやすさを変える
USE_LLM = True                 # 呼び名と情景の文を LLM に書かせる
FIT_CHECK = True               # 世界に合うかを LLM に確かめさせる
ARRIVAL_NOTICE = True          # 街に着いたときに情勢の1行を出す
GUIDE_NOTICE = True            # 様子の1行に、ギルドへの案内を添える（依頼がまだ出ていない間だけ）
SHOP_NOTICE = True             # 店に入ったときに値段の理由の1行を出す
WINDOW_DAYS = "日数"           # 窓の日付の出し方（日数／あいまい）
WINDOW_EFFECTS = "数字"        # 窓に出す値段の影響（数字／言葉／出さない）
KIND_OUTLAWS = True
KIND_EPIDEMIC = True
KIND_INVADERS = True
KIND_UNREST = True
KIND_SHORTAGE = True
KIND_AFTERMATH = True
KIND_COUNTERFEIT = True
KIND_HOARDING = True
KIND_ANOMALY = True
KIND_ABDUCTIONS = True
KIND_CONTRABAND = True
KIND_CRACKDOWN = True
KIND_POWER_VACUUM = True
KIND_INFLUX = True
KIND_BOOM = True

# ---- 文（ゲーム画面の文は常体）--------------------------------------------
BOARD_TEXT = "掲示板に、{name}にまつわる依頼が新しく貼り出されている。"
SOLVED_TEXT = "{town}の{name}は、これで収まるだろう。"
RIVAL_SOLVED_TEXT = "{town}の{name}は、冒険者{rival}が片付けたらしい。"
OUTBREAK_TEXT = "{line}"
GUIDE_TEXT = "{guild}に行けば、この件で人手を求める依頼が出ているかもしれない。"
GUILD_FALLBACK = "ギルド"
LOCAL_NOTE = "いまこの土地では「{name}」が起きている（{stage}）。{line}"
RUMOR_NOTE = "噂: {line}"
PROFILE_NOTE = "【この土地の情勢】{name}（{stage}）。{line}"
BRIEF = ("\n\n【この街でいま起きている出来事】{name}（{stage}）: {what}。\n"
         "今回の依頼は、次の内容にすること: {quest}。")

# ---- コード側の決まり -------------------------------------------------------
OWNER = "918_town_incidents"
LOG_BASENAME = "town_incidents.log"
STATE_DIRNAME = "town_incidents"
RIVAL_DIRNAME = "rival_adventurer"
STORE_ATTR = "_instantale_town_incidents"
MARK = "mod_town_incidents"
ICON_KEY = "town_incidents"
MANAGER_TEXT = "mod_town_incidents_text"
TEXT_TIMEOUT = 90
NAME_CHARS = 20
LINE_CHARS = 90
FIT_TRIES = 2                  # 世界に合わないと言われたときに引き直す回数
RUMOR_LIMIT = 2
RUMOR_STAGE = 2                # よその街で噂になる段
ENDED_SHOWN_DAYS = 360         # 窓に出す「終わったもの」の日数
INJECT_TTL = 300.0
SIZES = ("village", "town", "city")
GUILD_TYPE = "guild"
COMPLETED = "completed"
SHOP_TARGET = "__main__:ShoppingStartManagerRemake.execute"
LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",)
BUSY_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle", "in_conversation")
#: 窓のアイコン（注意の標識）。普段・乗せた時・押した時。描き方は `tools\make_town_incidents_icon.py`。
ICON_FILES = ("icon_caution.png", "icon_caution_hover.png", "icon_caution_click.png")
#: 窓を開くときの音。キャラ情報と同じ（ゲームは開くときだけ鳴らし、閉じるときは鳴らさない）。
OPEN_SOUND = "Assets/sounds/sounds/ui/常時/アイコン_キャラシート.wav"
VIEW_TITLE = "各地の出来事"
VIEW_SIZE_HINT = (0.7, 0.8)
VIEW_PAD = 12
VIEW_BG = (0.06, 0.06, 0.08, 0.96)
VIEW_BORDER_WIDTH = 1.5
VIEW_BORDER_ALPHA = 0.8
VIEW_EMPTY = "いま、各地で目立った出来事は起きていない。"

TEXT_PROMPT = """あなたは RPG の語り手です。
次の世界の街「{town}」で起きた出来事を、この世界の言葉で書いてください。

# 世界
{world}

# 街「{town}」
{overview}

# 起きた出来事
{what}。
世界ごとの例: {examples}（例にすぎない。この世界と街に合う形に置き換える）

# 書くこと
- fits: この出来事がこの世界とこの街で起こりうるなら「はい」、世界観に合わないなら「いいえ」。迷ったら「はい」
- name: この世界での出来事の呼び名。12字まで
- summary: この街で何が起き、誰がどう困っているかの概要を1文で（遊ぶ人が一覧で読む）
- arrive_1 / arrive_2 / arrive_3: 街に着いたときの情景を1文ずつ。1は兆し、2は悪化、3は深刻
- shop: 店に入ったときの1文。{shop_hint}
- settled: 出来事が自然に収まったと耳にする1文（「〜らしい」で終える）
- quest: この街のギルド（依頼を仲介する所）に出る、この出来事を解決する依頼の中身を1文で
- rumor: よその街で聞く噂の1文（「〜らしい」で終える）。街の名前「{town}」を入れる

# 決まり
- どの文も1文、常体、60字まで
- 世界の設定のうち、住人が知らないはずのこと（秘密や真相）は書かない
- 主人公の台詞や行動は書かない
"""
TEXT_FIELDS = ("fits", "name", "summary", "arrive_1", "arrive_2", "arrive_3", "shop", "settled",
               "quest", "rumor")


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None, "rival": None, "worker": None, "reconcile": False,
                 "rng": random.Random(), "inject": {}, "view": None, "here": None}
        setattr(sys, STORE_ATTR, store)
    return store


def enabled_kinds():
    values = globals()
    return [kind for kind in incidents.KINDS
            if values.get("KIND_" + kind["key"].upper(), True)]


def effect_hint(kind, goods=None):
    """頼み文の `shop` の手掛かり。値段の動きを言葉にする。"""
    parts = []
    for target, side, rates in kind["effects"]:
        if target is incidents.ALL:
            what = "品"
        elif target == incidents.GOODS:
            what = incidents.GOODS_NAMES.get(goods, "ある品")
        else:
            what = "・".join(incidents.GOODS_NAMES.get(name, name) for name in target)
        way = "上がる" if rates[0] > 0 else "下がる"
        side_text = {"buy": "店の売る値が", "sell": "店の買い取る値が", "both": "売り買いの値が"}[side]
        parts.append("{}の{}{}".format(what, side_text, way))
    if not parts:
        return "店の値段は動かないので、空でよい"
    return "値段の動き（{}）の理由になる、店の様子を書く".format("、".join(parts))


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(
            ctx, STATE_DIRNAME, default=incidents.new_bucket,
            order=incidents.order_bucket, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    if store["rival"] is None:
        store["rival"] = loader_state.WorldStore(ctx, RIVAL_DIRNAME, own=False)
    rival_store = store["rival"].rebind(ctx)
    rng = store["rng"]
    screen = ui.Screen(ctx, write, tag="town incidents", mark=MARK)
    inject = store["inject"]

    def ledger(app):
        key = worlds.playthrough(app)
        return key, worlds.load(key)

    # ------------------------------------------------------------ 世界を読む
    def save_areas(app):
        data = getattr(app, "save_data_dict", None)
        areas = data.get("areas") if isinstance(data, dict) else None
        return areas if isinstance(areas, dict) else {}

    def size_of(app, area_id):
        row = save_areas(app).get(str(area_id))
        size = row.get("size") if isinstance(row, dict) else None
        return size if isinstance(size, str) else None

    def guild_of(area):
        """その街のギルド（施設）。無ければ None。"""
        nodes = getattr(area, "nodes", None)
        values = nodes.values() if isinstance(nodes, dict) else (nodes or [])
        for node in values:
            for facility in ui.facilities_of(node).values():
                if ui.facility_type_of(facility) == GUILD_TYPE:
                    return facility
        return None

    def has_guild(area):
        return guild_of(area) is not None

    def guide_line(app, event):
        """様子の1行に添えるギルドへの案内。依頼がもう掲示板に出ていれば空（くどくしない）。"""
        if not GUIDE_NOTICE or event is None or event.get("quest"):
            return ""
        area = (ui.world_areas(app) or {}).get(str(event.get("area")))
        name = ui.facility_name(app, guild_of(area)) if area is not None else ""
        return GUIDE_TEXT.format(guild=name or GUILD_FALLBACK)

    def towns(app):
        """起きる候補の街 `[(id, 名前, 規模)]`。訪れたことがあり（施設が作られている）、ギルドのある街。"""
        rows = []
        for area_id, area in sorted((ui.world_areas(app) or {}).items(),
                                    key=lambda item: ui.id_sort_key(item[0])):
            size = size_of(app, area_id)
            if size not in SIZES or not has_guild(area):
                continue
            rows.append((str(area_id), getattr(area, "name", "") or "", size))
        return rows

    def here_id(app):
        return ui.area_id_of(ui.current_area(app))

    def area_overview(app, area_id):
        area = (ui.world_areas(app) or {}).get(str(area_id))
        descriptions = getattr(area, "descriptions", None)
        text = descriptions.get("overview") if isinstance(descriptions, dict) else None
        return text.strip() if isinstance(text, str) and text.strip() else "（不明）"

    def stage_name(event):
        return incidents.STAGE_NAMES.get(event.get("stage"), "")

    def name_of(event):
        return incidents.text_of(event, "name")

    def rival_bucket(app):
        try:
            return rival_store.load(loader_state.playthrough_key(app), fresh=True) or {}
        except Exception:
            ctx.log_exc("town incidents: cannot read the rival record")
            return {}

    def quest_status(app, quest_id):
        quest = ui.quest_of(app, str(quest_id)) if quest_id else None
        if quest is None:
            return None
        config = ui.quest_value(quest, "config", None)
        return config.get("status") if isinstance(config, dict) else None

    def busy(app):
        for flag in BUSY_FLAGS:
            if getattr(app, flag, None):
                return flag
        return "on a quest" if ui.current_quest_id(app) is not None else None

    def say_when_idle(app, lines, tag):
        lines = [line for line in lines if line]
        if lines:
            screen.when_idle(app, lambda: [screen.say(app, line) for line in lines],
                             cancel_if=lambda: busy(app), proceed_on_timeout=True,
                             tag="town incidents " + tag)

    # ------------------------------------------------------------ 文を書かせる
    def text_messages(app, event, kind):
        prompt = TEXT_PROMPT.format(
            town=event.get("area_name") or "その街", world=ui.world_overview(app) or "（不明）",
            overview=area_overview(app, event.get("area")), what=kind["what"],
            examples=kind["examples"], shop_hint=effect_hint(kind, event.get("goods")))
        return [{"role": "user", "content": prompt}]

    def clean_line(value, limit):
        text = str(value or "").strip().replace("\n", "")
        return text if 0 < len(text) <= limit else ""

    def write_text(job):
        """背景で文を書かせ、出来事を始める。合わないと言われたら別の種類で書かせ直す。"""
        app, key, event_id = job["app"], job["key"], job["event"]
        answer = None
        for _attempt in range(FIT_TRIES + 1):
            with worlds.lock:
                bucket = worlds.load(key)
                event = incidents.event_by_id(bucket, event_id)
                if event is None or event.get("status") != incidents.PENDING:
                    return
                kind = incidents.KIND_BY_KEY.get(event.get("kind"))
                messages = text_messages(app, event, kind)
            answer = ask_text(messages)
            if answer is None:
                break                       # LLM が使えない。表の文で始める
            raw_fits = str(answer.get("fits") or "").strip().rstrip("。")
            fits = llm.truthy(raw_fits) if raw_fits else True      # 空は「合う」（迷ったら はい の頼み方）
            if fits or not FIT_CHECK:
                break
            with worlds.lock:
                bucket = worlds.load(key)
                event = incidents.event_by_id(bucket, event_id)
                if event is None:
                    return
                other = incidents.pick_kind(rng, enabled_kinds(), size_of(app, event.get("area")),
                                            SIZE_WEIGHTING, exclude=event.get("tries") or ())
                write("text: {} does not fit the world for {} ({})".format(
                    kind["key"], event.get("area_name"), "retry with " + other["key"]
                    if other and len(event.get("tries") or ()) <= FIT_TRIES else "dropped"))
                if other is None or len(event.get("tries") or ()) > FIT_TRIES:
                    incidents.drop(bucket, event)
                    worlds.save(key)
                    return
                incidents.change_kind(event, other, rng)
                worlds.save(key)
            answer = None
        activate(app, key, event_id, answer)

    def ask_text(messages):
        if not USE_LLM:
            return None
        structure = llm.create_structure(ctx, "TownIncidentText", {
            name: (str, ...) for name in TEXT_FIELDS}, label="town incidents")
        if structure is None:
            return None
        raw = llm.ask(ctx, MANAGER_TEXT, messages, timeout=TEXT_TIMEOUT, structure=structure,
                      label="town incidents", write=write)
        return raw if isinstance(raw, dict) else None

    def activate(app, key, event_id, answer):
        with worlds.lock:
            bucket = worlds.load(key)
            event = incidents.event_by_id(bucket, event_id)
            if event is None or event.get("status") != incidents.PENDING:
                return
            text = {}
            if isinstance(answer, dict):
                for name in incidents.TEXT_KEYS:
                    limit = NAME_CHARS if name == "name" else LINE_CHARS
                    line = clean_line(answer.get(name), limit)
                    if line:
                        text[name] = line
            event["text"] = text
            event["status"] = incidents.ACTIVE
            worlds.save(key)
            write("start: {} #{} {} at {} ({}) stage 1 day {} text {}".format(
                event.get("kind"), event_id, name_of(event), event.get("area_name"),
                event.get("area"), event.get("started"), "llm" if text else "table"))
            here = here_id(app) == event.get("area")
            line = incidents.arrive_text(event) if here else ""
            guide = guide_line(app, event) if here else ""
        prices.refresh("town incidents start")
        if line and ARRIVAL_NOTICE:
            say_when_idle(app, [OUTBREAK_TEXT.format(line=line), guide], "start")

    worker = store["worker"] = jobs.rebind(
        store["worker"]
        or jobs.Worker(ctx, write_text, name="town_incidents", label="town incidents",
                       key=lambda job: (job["key"], job["event"])),
        ctx, write_text, write)

    def ask_later(app, key, event_id):
        worker.enqueue({"app": app, "key": key, "event": event_id})

    # ------------------------------------------------------------ 日数が進んだ
    def check_rival(app, bucket, day):
        """ライバルが片付けた依頼（911 の控えの `taken`）の出来事を終える。終えた出来事の並び。"""
        taken = rival_bucket(app).get("taken")
        if not isinstance(taken, dict):
            return []
        done = []
        for event in incidents.events_of(bucket, incidents.ACTIVE):
            quest = event.get("quest")
            if quest and str(quest) in taken:
                incidents.end(bucket, event, day, incidents.END_RIVAL, REST_DAYS)
                write("end: #{} {} at {} was taken by the rival (quest {})".format(
                    event.get("id"), name_of(event), event.get("area_name"), quest))
                done.append((event, (taken.get(str(quest)) or {}).get("by") or ""))
        return done

    def clean_up(app, bucket):
        """自然に収まった出来事の依頼を世界から片付ける。受けている最中なら次の機会へ回す。"""
        left = []
        for quest_id in list(bucket.get("cleanup") or []):
            status = quest_status(app, quest_id)
            if status is None or status == COMPLETED:
                continue                    # もう無い／プレイヤーが片付けた（手柄として残す）
            gone = ui.remove_quest(app, quest_id)
            if gone:
                write("cleanup: removed quest {} from {}".format(quest_id, gone))
            else:
                left.append(str(quest_id))
        bucket["cleanup"] = left

    def tick(app):
        day = ui.game_day(app)
        if day is None:
            return
        lines = []
        refresh = False
        with worlds.lock:
            key, bucket = ledger(app)
            for event, by in check_rival(app, bucket, day):
                refresh = True
                if here_id(app) == event.get("area"):
                    lines.append(RIVAL_SOLVED_TEXT.format(
                        town=event.get("area_name"), name=name_of(event), rival=by or "誰か"))
            for event, what, stage in incidents.advance(
                    bucket, day, stage_days=STAGE_DAYS, settle_days=SETTLE_DAYS,
                    steps_per_elapse=STEPS_PER_ELAPSE, rest_days=REST_DAYS):
                refresh = True
                if what == "stage":
                    write("stage: #{} {} at {} -> {} day {}".format(
                        event.get("id"), name_of(event), event.get("area_name"), stage, day))
                else:
                    write("end: #{} {} at {} settled by itself day {}".format(
                        event.get("id"), name_of(event), event.get("area_name"), day))
                    if here_id(app) == event.get("area"):
                        # その街に居るならその場で知らせる（着いたときの知らせは土地を移るまで出ない）
                        event.setdefault("told", {})["settled"] = True
                        if ARRIVAL_NOTICE:
                            lines.append(incidents.text_of(event, "settled"))
                    if event.get("quest") and quest_status(app, event["quest"]) != COMPLETED:
                        bucket.setdefault("cleanup", []).append(str(event["quest"]))
            clean_up(app, bucket)
            new = incidents.roll(
                bucket, day, towns(app), rng, enabled_kinds(), chance_percent=ROLL_PERCENT,
                interval_days=ROLL_DAYS, max_active=MAX_ACTIVE, by_size=SIZE_WEIGHTING)
            if new is not None:
                write("roll: {} at {} ({}) day {}; asking for the text".format(
                    new.get("kind"), new.get("area_name"), new.get("area"), day))
            worlds.save(key)
        if new is not None:
            ask_later(app, key, new["id"])
        if refresh:
            prices.refresh("town incidents")
        say_when_idle(app, lines, "tick")

    # ------------------------------------------------------------ 値段
    def live_here(app):
        if app is None:
            return None
        try:
            with worlds.lock:
                _key, bucket = ledger(app)
                return incidents.live_in(bucket, here_id(app))
        except Exception:
            ctx.log_exc("town incidents: cannot read the ledger")
            return None

    def price_layer(item, key, price):
        event = live_here(ui.find_app())
        if event is None:
            return None
        field, attributes = prices.read_item(item)
        category = incidents.category_of(field("item_type"), attributes.get("item_detail"))
        rate = incidents.price_rate(event, category, key, PRICE_STRENGTH_PERCENT)
        return incidents.scaled(price, rate)

    def inn_layer(app, quality, price):
        event = live_here(app)
        if event is None:
            return None
        return incidents.scaled(price, incidents.inn_rate(event, PRICE_STRENGTH_PERCENT))

    prices.install(ctx, write)
    prices.adjust(OWNER, price_layer, temporary=True, write=write)
    prices.adjust_inn(OWNER, inn_layer, write=write)
    prices.install_inn(ctx, write)

    # ------------------------------------------------------------ 会話と情景
    def notes(info):
        app = info.get("app") or ui.find_app()
        if app is None:
            return None
        here = here_id(app)
        with worlds.lock:
            _key, bucket = ledger(app)
            local = incidents.live_in(bucket, here)
            rumors = [event for event in incidents.events_of(bucket, incidents.ACTIVE)
                      if str(event.get("area")) != here
                      and (event.get("stage") or 1) >= RUMOR_STAGE][:RUMOR_LIMIT]
        lines = []
        if local is not None:
            lines.append(LOCAL_NOTE.format(name=name_of(local), stage=stage_name(local),
                                           line=incidents.arrive_text(local)))
        for event in rumors:
            lines.append(RUMOR_NOTE.format(line=incidents.text_of(event, "rumor")))
        return "\n".join(lines) or None

    modnpc.install(ctx, write=write)
    modnpc.register(OWNER, modnpc.ANY, notes=notes, write=write)

    @ctx.wrap("scripts.llm.llm_manager:narrator", required=False)
    def narrator(orig, *args, **kwargs):
        """`player_profile`（第3引数）にこの土地の情勢を素材として足す（`317_` と同じ形）。"""
        try:
            event = live_here(ui.find_app())
            if event is not None:
                addition = PROFILE_NOTE.format(name=name_of(event), stage=stage_name(event),
                                               line=incidents.arrive_text(event))
                if isinstance(kwargs.get("player_profile"), str):
                    merged = dict(kwargs)
                    merged["player_profile"] = kwargs["player_profile"].rstrip() + "\n\n" + addition
                    return orig(*args, **merged)
                if len(args) >= 3 and isinstance(args[2], str):
                    merged = list(args)
                    merged[2] = args[2].rstrip() + "\n\n" + addition
                    return orig(*merged, **kwargs)
        except Exception:
            ctx.log_exc("town incidents: cannot add the situation to the narration")
        return orig(*args, **kwargs)

    # ------------------------------------------------------------ 依頼
    def needs_quest(app, event):
        quest = event.get("quest")
        if not quest:
            return True
        status = quest_status(app, quest)
        if status is None:
            return True                     # 消された（他の MOD・エディタ）。作り直す
        return False

    def make_quest(app, event_id):
        """この街の掲示板に、出来事を片付ける依頼を1つ作らせる。作れたら id。"""
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("WARN quest: DisplayQuestChoice is not available")
            return None
        with worlds.lock:
            key, bucket = ledger(app)
            event = incidents.event_by_id(bucket, event_id)
            if event is None:
                return None
            kind = incidents.KIND_BY_KEY[event["kind"]]
            brief = BRIEF.format(name=name_of(event), stage=stage_name(event), what=kind["what"],
                                 quest=incidents.text_of(event, "quest").rstrip("。"))
        # 掲示板の `execute` の中なので、待機の表示はゲームが出している（GAME.md §2.4）。
        # `screen.busy_on` / `busy_off` は重ねない（DOC.md §2.5）。
        inject.update(brief=brief, at=time.monotonic())
        before = set(ui.quest_ids(app))
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("town incidents: generate_random_quest failed")
        finally:
            inject.update(brief=None)
        added = sorted(set(ui.quest_ids(app)) - before, key=ui.id_sort_key)
        if not added:
            write("quest: no quest was generated for #{}".format(event_id))
            return None
        quest_id = added[-1]
        with worlds.lock:
            key, bucket = ledger(app)
            event = incidents.event_by_id(bucket, event_id)
            if event is not None:
                event["quest"] = quest_id
                worlds.save(key)
        write("quest: made {} {!r} for #{}".format(
            quest_id, ui.quest_value(ui.quest_of(app, quest_id), "quest_title", ""), event_id))
        return quest_id

    @ctx.wrap("__main__:DisplayQuestChoice.execute", required=False, safe=True)
    def board(orig, self, *args, **kwargs):
        """掲示板を開いた。この街で出来事が起きていて依頼がまだ無ければ、先に作らせる（ゲームの待機表示の中で）。"""
        app = getattr(self, "app", None) or ui.find_app()
        try:
            if app is not None and ui.game_day(app) is not None:
                with worlds.lock:
                    key, bucket = ledger(app)
                    for event, _by in check_rival(app, bucket, ui.game_day(app)):
                        pass
                    worlds.save(key)
                    event = incidents.live_in(bucket, here_id(app))
                    target = event.get("id") if event is not None and needs_quest(app, event) else None
                    name = name_of(event) if event is not None else ""
                if target is not None and make_quest(app, target) is not None:
                    # 作らせた回は、この掲示板の続き（`orig`）を呼ばず、手が空いてから開き直す（DOC.md §2.5）。
                    screen.say(app, BOARD_TEXT.format(name=name))
                    reopen = getattr(self, "__class__", None)
                    choice = args[0] if args else kwargs.get("choice_text", "")
                    screen.when_idle(app, lambda: app.process_choice(reopen(app), choice),
                                     proceed_on_timeout=True, tag="town incidents board")
                    return None
        except Exception:
            ctx.log_exc("town incidents: cannot prepare the quest on the board")
        return orig(self, *args, **kwargs)

    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator", required=False)
    def random_quest_generator(orig, world_overview, settlement_name, settlement_overview,
                               settlement_structure_description, area_description,
                               quest_difficulty, *args, **kwargs):
        """出来事の依頼を作る回だけ、`area_description` に出来事の説明を足す。印は1回で使い切る。

        `328_`（街の描写を伏せる）より内側に居るので、伏せられた後の描写に足される（`336_` と同じ）。
        """
        brief, at = inject.get("brief"), inject.get("at") or 0.0
        inject.update(brief=None)
        if brief and time.monotonic() - at <= INJECT_TTL:
            area_description = (area_description or "") + brief
            write("quest: injected the brief ({} chars)".format(len(brief)))
        return orig(world_overview, settlement_name, settlement_overview,
                    settlement_structure_description, area_description, quest_difficulty,
                    *args, **kwargs)

    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        """依頼から帰った。出来事の依頼を片付けていれば、その出来事は終わる。

        どの依頼が終わるのかは `orig` の前に読む（終わった後は片付いている。`318_` と同じ）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        quest_id = ui.current_quest_id(app) if app is not None else None
        result = orig(self, *args, **kwargs)
        try:
            if quest_id is None or quest_status(app, quest_id) != COMPLETED:
                return result
            lines = []
            with worlds.lock:
                key, bucket = ledger(app)
                day = ui.game_day(app)
                for event in incidents.events_of(bucket, incidents.ACTIVE):
                    if str(event.get("quest")) == str(quest_id):
                        incidents.end(bucket, event, day, incidents.END_PLAYER, REST_DAYS)
                        write("end: #{} {} at {} was solved by the player (quest {})".format(
                            event.get("id"), name_of(event), event.get("area_name"), quest_id))
                        lines.append(SOLVED_TEXT.format(town=event.get("area_name"),
                                                        name=name_of(event)))
                bucket["cleanup"] = [qid for qid in bucket.get("cleanup") or []
                                     if str(qid) != str(quest_id)]
                worlds.save(key)
            if lines:
                prices.refresh("town incidents solved")
                say_when_idle(app, lines, "solved")
        except Exception:
            ctx.log_exc("town incidents: cannot settle the quest end")
        return result

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        """日が進んだ後に見回る。日数の進め方には触らない（ローダの関所の外側の後処理）。"""
        result = orig(self, *args, **kwargs)
        try:
            tick(self)
        except Exception:
            ctx.log_exc("town incidents: tick after elapse_days failed")
        return result

    # ------------------------------------------------------------ 着いた・店に入った
    @ctx.wrap("__main__:AreaMoveManager.execute", required=False, safe=True)
    def area_arrival(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            here = here_id(app)
            if store.get("here") != here:
                store["here"] = here
                prices.refresh("town incidents arrival")
            lines = []
            with worlds.lock:
                key, bucket = ledger(app)
                event = incidents.live_in(bucket, here)
                if event is not None and ARRIVAL_NOTICE:
                    lines.append(incidents.arrive_text(event))
                    lines.append(guide_line(app, event))
                for ended in incidents.events_of(bucket, incidents.ENDED):
                    if str(ended.get("area")) == here and ended.get("end_by") == incidents.END_NATURAL \
                            and not (ended.get("told") or {}).get("settled"):
                        ended.setdefault("told", {})["settled"] = True
                        if ARRIVAL_NOTICE:
                            lines.append(incidents.text_of(ended, "settled"))
                worlds.save(key)
            say_when_idle(app, lines, "arrival")
        except Exception:
            ctx.log_exc("town incidents: cannot tell the arrival")
        return result

    @ctx.wrap(SHOP_TARGET, required=False, safe=True)
    def shop_start(orig, self, *args, **kwargs):
        try:
            app = getattr(self, "app", None) or ui.find_app()
            event = live_here(app)
            if SHOP_NOTICE and event is not None:
                line = incidents.text_of(event, "shop")
                if line:
                    screen.schedule(lambda: screen.say(app, line))
        except Exception:
            ctx.log_exc("town incidents: cannot tell the shop")
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------ 状況を見る窓
    def view_text(app):
        day = ui.game_day(app)
        with worlds.lock:
            _key, bucket = ledger(app)
            events = [dict(event) for event in incidents.events_of(bucket)]
        rival = rival_bucket(app)
        areas = ui.world_areas(app) or {}
        guilds = {}
        for event in events:
            area = areas.get(str(event.get("area")))
            name = ui.facility_name(app, guild_of(area)) if area is not None else ""
            if name:
                guilds[str(event.get("area"))] = name
        return incidents.view_lines(events, day, rival, stage_days=STAGE_DAYS, guild_names=guilds,
                                    settle_days=SETTLE_DAYS, ended_days=ENDED_SHOWN_DAYS,
                                    in_words=(WINDOW_DAYS == incidents.DAYS_VAGUE),
                                    quest_status=lambda qid: quest_status(app, qid),
                                    effects=WINDOW_EFFECTS,
                                    strength_percent=PRICE_STRENGTH_PERCENT)

    def close_view():
        view = store.get("view")
        store["view"] = None
        if view is not None:
            try:
                view.dismiss()
            except Exception:
                ctx.log_exc("town incidents: could not close the window")

    def toggle_view(app):
        if store.get("view") is not None:
            close_view()
            return
        if app is None:
            return
        try:
            from kivy.uix.boxlayout import BoxLayout
            from kivy.uix.button import Button
            from kivy.uix.label import Label
            from kivy.uix.modalview import ModalView
            from kivy.uix.scrollview import ScrollView
            from kivy.graphics import Color, Line, Rectangle
        except Exception:
            write("WARN view: kivy is not available")
            return
        hud = ui.find_hud(app)
        status = frames.attr(hud, "status_label", None) if hud is not None else None
        font_name = frames.text_of(status, "font_name") if status is not None else None
        font_size = frames.attr(status, "font_size", None) if status is not None else None
        view = ModalView(size_hint=VIEW_SIZE_HINT, auto_dismiss=True)
        for name in ("background_color", "overlay_color"):
            try:
                setattr(view, name, (0, 0, 0, 0.5))
            except Exception:
                pass
        root = BoxLayout(orientation="vertical", padding=ui.upx(VIEW_PAD), spacing=ui.upx(VIEW_PAD))
        with root.canvas.before:
            Color(*VIEW_BG)
            panel = Rectangle(pos=root.pos, size=root.size)
        # 枠線は中身の後（`canvas.after`）に引く（`122_` の窓と同じ白い線）。
        with root.canvas.after:
            Color(1, 1, 1, VIEW_BORDER_ALPHA)
            frame = Line(rectangle=(root.x, root.y, root.width, root.height),
                         width=ui.upx(VIEW_BORDER_WIDTH))

        def redraw(*_args):
            panel.pos, panel.size = root.pos, root.size
            frame.rectangle = (root.x, root.y, root.width, root.height)

        root.bind(pos=redraw, size=redraw)
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=ui.upx(36))
        title = Label(text=VIEW_TITLE, halign="left", valign="middle")
        close = Button(text="×", size_hint=(None, None), size=(ui.upx(36), ui.upx(36)))
        for widget in (title, close):
            if isinstance(font_name, str) and font_name:
                widget.font_name = font_name
        if isinstance(font_size, (int, float)):
            title.font_size = font_size
        title.bind(size=lambda instance, value: setattr(instance, "text_size", value))
        close.bind(on_release=lambda *_a: close_view())
        head.add_widget(title)
        head.add_widget(close)
        scroll = ScrollView(do_scroll_x=False)
        body = Label(text="\n".join(view_text(app)) or VIEW_EMPTY, halign="left", valign="top",
                     size_hint_y=None, markup=False)
        if isinstance(font_name, str) and font_name:
            body.font_name = font_name
        if isinstance(font_size, (int, float)):
            body.font_size = font_size
        body.line_height = 1.4
        body.bind(width=lambda instance, value: setattr(instance, "text_size", (value, None)),
                  texture_size=lambda instance, value: setattr(instance, "height", value[1]))
        scroll.add_widget(body)
        root.add_widget(head)
        root.add_widget(scroll)
        view.add_widget(root)
        view.bind(on_dismiss=lambda *_a: store.update({"view": None}))
        store["view"] = view
        try:
            view.open()
        except Exception:
            store["view"] = None
            ctx.log_exc("town incidents: could not open the window")
            return
        play_open_sound(app)

    def play_open_sound(app):
        """ゲーム自身の効果音の出口で鳴らす（音量の設定もゲームのものが効く）。"""
        manager = getattr(app, "sound_manager", None)
        play = getattr(manager, "play_sound_from_src", None)
        if callable(play):
            try:
                play(app, OPEN_SOUND)
            except Exception:
                ctx.log_exc("town incidents: cannot play the open sound")

    # 絵のパスは apply() の間に組む（`ctx.mod_dir` はフックの中では読めない）。
    icon_dir = getattr(ctx, "mod_dir", None) or os.path.dirname(os.path.abspath(__file__))
    hudicons.add(ctx, ICON_KEY, toggle_view,
                 images=tuple(os.path.join(icon_dir, name) for name in ICON_FILES), write=write)

    # ------------------------------------------------------------ ロードの突き合わせ
    def reconcile(app):
        day = ui.game_day(app)
        with worlds.lock:
            key, bucket = ledger(app)
            if incidents.rewind(bucket, day):
                write("reconcile: forgot what happened after day {} (older save)".format(day))
            worlds.save(key)
            pending = [event["id"] for event in incidents.events_of(bucket, incidents.PENDING)]
        for event_id in pending:
            ask_later(app, key, event_id)       # 文を書き終える前に閉じた。もう一度頼む
        store["here"] = here_id(app)
        prices.refresh("town incidents load")

    def make_load(target):
        @ctx.wrap(target, required=False, safe=True)
        def on_load(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            worlds.forget()
            store["reconcile"] = True
            return result
        return on_load

    for target in LOAD_TARGETS:
        make_load(target)

    def refresh_choice_buttons(self, _buttons):
        if store["reconcile"]:
            characters = getattr(getattr(self, "world", None), "characters", None)
            if isinstance(characters, dict) and len(characters) > 1:
                store["reconcile"] = False
                try:
                    reconcile(self)
                except Exception:
                    ctx.log_exc("town incidents: reconcile failed")

    choices.provide(ctx, screen, refresh=refresh_choice_buttons)

    ctx.log("town incidents: installed ({} kind(s), roll every {} day(s) at {}%, up to {}, stage "
            "every {} day(s), settles {} day(s) after stage 3, text {}, state {})".format(
                len(enabled_kinds()), ROLL_DAYS, ROLL_PERCENT, MAX_ACTIVE, STAGE_DAYS,
                SETTLE_DAYS, "llm" if USE_LLM else "table", worlds.dir_path()))
