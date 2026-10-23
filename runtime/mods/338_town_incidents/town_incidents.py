# -*- coding: utf-8 -*-
"""街に盗賊や疫病などの困り事や、祝祭や豊作などの良い出来事が起き、日を追って移り変わる。

日数が進むと、訪れたことのあるギルドのある街に、表（`incidents.default.json` と `kinds\\`）の出来事が起きることがある。
困り事は放っておくと 兆し → 悪化 → 深刻 と進み、最後は自然に収まる。その街の掲示板の依頼で片付けられ、
ほかの冒険者（ライバルなど）が先に片付けることもある（ローダの窓口 `quest_claims`）。良い出来事は依頼を作らず、出来事ごとの段を進んで収まる。
遊び方は DOC.md、決めた仕様と控えの形（他の MOD との取り決め）は VERIFICATION.md §3.97。

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
import re
import sys
import threading
import time

from instantale_modloader import (arrivals, choices, durations, hudicons, jobs, llm, loads, modnpc, prices,
                                  quest_claims, state as loader_state, ui)

from . import incidents, window

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
ROLL_DAYS = 30                 # 抽選の間隔（日）
ROLL_SPREAD_DAYS = 15          # 抽選の間隔を前後にずらすランダムな日数の幅（抽選のたびに引き直す）
ROLL_PERCENT = 25              # 抽選で起きる確率（%）
MAX_ACTIVE = 3                 # 世界で同時に起きる数の上限
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

# ---- 文（ゲーム画面の文は常体）--------------------------------------------
BOARD_TEXT = "掲示板に、{name}にまつわる依頼が新しく貼り出されている。"
#: 掲示板で出来事の依頼のボタンの題の後ろに付ける印（写しの文言だけ。セーブには素の題が残る）。
#: 題は同じになることがあるので、どれを片付ければ出来事が終わるかはこの印で見分ける（VERIFICATION.md §3.97）。
QUEST_MARK = "（街の出来事）"
#: 掲示板の依頼のボタンが呼ぶクラス（`QuestChoiceManager(app, quest_type, quest_id)`。GAME.md §2.9）。
QUEST_CHOICE_CLS = "QuestChoiceManager"
SOLVED_TEXT = "{town}の{name}は、これで収まるだろう。"
RIVAL_SOLVED_TEXT = "{town}の{name}は、冒険者の{rival}が片付けたらしい。"
OUTBREAK_TEXT = "{line}"
GUIDE_TEXT = "{guild}に行けば、この件で人手を求める依頼が出ているかもしれない。"
GUILD_FALLBACK = "ギルド"
LOCAL_NOTE = "いまこの土地では「{name}」が起きている（{stage}）。{line}"
RUMOR_NOTE = "噂: {line}"
PROFILE_NOTE = "【この土地の情勢】{name}（{stage}）。{line}"
BRIEF = ("\n\n【この街でいま起きている出来事】{name}（{stage}）: {what}。\n"
         "今回の依頼は、次の内容にすること: {quest}。")

# ---- コード側の決まり -------------------------------------------------------
OWNER = "338_town_incidents"
LOG_BASENAME = "town_incidents.log"
STATE_DIRNAME = "town_incidents"
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
#: ロードの入口（ローダの窓口 `loads`。TECH.md §3.3.21）。
LOAD_KINDS = (loads.LOAD,)
BUSY_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle", "in_conversation")
#: 窓のアイコン（注意の標識）。普段・乗せた時・押した時。描き方は `tools\make_town_incidents_icon.py`。
ICON_FILES = ("icon_caution.png", "icon_caution_hover.png", "icon_caution_click.png")
#: 左下の一覧での並び（`339_` の勢力と情勢より前）。
ICON_ORDER = 10
#: 窓を開くときの音。キャラ情報と同じ（ゲームは開くときだけ鳴らし、閉じるときは鳴らさない）。
VIEW_TITLE = "各地の出来事"

#: 起こさない出来事の英名（カンマ区切り）。設定画面の「出来事の種類」タブが書く。
DISABLED_KINDS = ""
#: 版1 の出来事ごとの設定（`KIND_OUTLAWS` など）で切ってあった英名。起動のときに設定の控えから読んで足す。
_LEGACY_DISABLED = set()

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
{summary_rule}
{arrive_rule}
- shop: 店に入ったときの1文。{shop_hint}
{shop_turn_rule}
- settled: 出来事が自然に収まったと耳にする1文（「〜らしい」で終える）
{quest_rule}
- rumor: よその街で聞く噂の1文（「〜らしい」で終える）。街の名前「{town}」を入れる

# 決まり
- どの文も1文、常体、60字まで
- 世界の設定のうち、住人が知らないはずのこと（秘密や真相）は書かない
- 主人公の台詞や行動は書かない
"""
TEXT_FIELDS = ("fits", "name", "summary_1", "summary_2", "summary_3", "summary_4",
               "arrive_1", "arrive_2", "arrive_3", "arrive_4", "shop", "shop_turn", "settled", "quest", "rumor")
SUMMARY_TROUBLE = "その段階のときに、この街で何が起き、誰がどう困っているかの概要を1文ずつ（遊ぶ人が一覧で読む）"
SUMMARY_GOOD = "その段階のときに、この街で何が起き、街がどうなっているかの概要を1文ずつ（遊ぶ人が一覧で読む）"
ARRIVE_RULE = "街に着いたときの情景を1文ずつ"
QUEST_TROUBLE = "- quest: この街のギルド（依頼を仲介する所）に出る、この出来事を解決する依頼の中身を1文で"
QUEST_GOOD = "- quest: 空にする（この出来事には依頼を出さない）"
SHOP_TURN_RULE = "- shop_turn: 値段の向きが途中で変わった後に店に入ったときの1文。{hint}"
SHOP_TURN_NONE = "- shop_turn: 空にする"


def stage_rule(prefix, what, kind):
    """頼み文の段ごとの欄（`summary_*` / `arrive_*`）の決まり。段の数だけ書かせ、残りは空にさせる。"""
    names = kind["stages"]
    fields = [n for n in TEXT_FIELDS if n.startswith(prefix + "_")]
    rule = "- {}: {}。{}".format(
        " / ".join(fields[:len(names)]), what, "、".join("{}は{}".format(i + 1, n) for i, n in enumerate(names)))
    if len(names) < len(fields):
        rule += "\n- {}: 空にする".format(" / ".join(fields[len(names):]))
    return rule


def arrive_rule(kind):
    return stage_rule("arrive", ARRIVE_RULE, kind)


def summary_rule(kind):
    return stage_rule("summary", SUMMARY_GOOD if incidents.is_good(kind) else SUMMARY_TROUBLE, kind)


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None, "worker": None, "reconcile": False,
                 "rng": random.Random(), "inject": {}, "view": None, "here": None}
        setattr(sys, STORE_ATTR, store)
    store.setdefault("urgent", {})
    # 掲示板で出来事の依頼を作らせている間の錠。押下ごとに別スレッドで `execute` が走るので、2つ目は作らせない
    store.setdefault("making_quest", threading.Lock())
    return store


def disabled_keys():
    words = re.split(r"[\s,、]+", DISABLED_KINDS) if isinstance(DISABLED_KINDS, str) else []
    return {word.strip() for word in words if word.strip()} | set(_LEGACY_DISABLED)


def enabled_kinds():
    disabled = disabled_keys()
    return [kind for kind in incidents.KINDS if kind["key"] not in disabled]


def legacy_disabled(raw):
    """設定の控え（この MOD の分の辞書）から、版1 の `KIND_*` で切ってあった英名を拾う。"""
    if not isinstance(raw, dict):
        return set()
    return {name[len("KIND_"):].lower() for name, value in raw.items()
            if isinstance(name, str) and name.startswith("KIND_") and value is False}


def pattern_words(kind, pattern, goods=None):
    """値段の向きの組（`incidents.shop_patterns` の1つ）を言葉にする。"""
    parts = []
    for (target, side, _rates), sign in zip(kind["effects"], pattern):
        if not sign:
            continue
        if target is incidents.ALL:
            what = "品"
        elif target == incidents.GOODS:
            what = incidents.GOODS_NAMES.get(goods, "ある品")
        else:
            what = "・".join(incidents.GOODS_NAMES.get(name, name) for name in target)
        side_text = {"buy": "店の売る値が", "sell": "店の買い取る値が", "both": "売り買いの値が"}[side]
        parts.append("{}の{}{}".format(what, side_text, "上がる" if sign > 0 else "下がる"))
    if pattern and pattern[-1]:
        parts.append("宿代が{}".format("上がる" if pattern[-1] > 0 else "下がる"))
    return "、".join(parts)


def effect_hint(kind, goods=None):
    """プロンプトの `shop` / `shop_turn` の手掛かり `(shop の手掛かり, shop_turn の決まり)`。

    値段の向きが途中で変わる出来事（にわか景気の崩れなど）は、変わった後の1文を `shop_turn` に書かせる。
    店では、いまの段の向きに合う方を出す（`incidents.shop_field`）。
    """
    patterns = incidents.shop_patterns(kind)
    if not patterns:
        return "店の値段は動かないので、空でよい", SHOP_TURN_NONE
    first = "値段の動き（{}）の理由になる、店の様子を書く".format(pattern_words(kind, patterns[0], goods))
    if len(patterns) < 2:
        return first, SHOP_TURN_NONE
    turn = "値段の動き（{}）の理由になる、店の様子を書く".format(pattern_words(kind, patterns[1], goods))
    return first, SHOP_TURN_RULE.format(hint=turn)


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    # 出来事の表は同梱の `incidents.default.json` と `kinds\`（足した出来事）から読む（VERIFICATION.md §3.97「出来事の表」）
    mod_dir = getattr(ctx, "mod_dir", None) or os.path.dirname(os.path.abspath(__file__))
    for problem in incidents.reload_kinds(mod_dir):
        write("WARN kinds: {}".format(problem))
    try:
        from instantale_modloader import config as loader_config
        raw = loader_config.load_store(os.path.dirname(os.path.dirname(mod_dir))).get(os.path.basename(mod_dir))
        _LEGACY_DISABLED.clear()
        _LEGACY_DISABLED.update(legacy_disabled(raw))
    except Exception:
        ctx.log_exc("town incidents: cannot read the old per-kind settings")
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(
            ctx, STATE_DIRNAME, default=incidents.new_bucket,
            order=incidents.order_bucket, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    rng = store["rng"]
    screen = ui.Screen(ctx, write, tag="town incidents", mark=MARK)
    inject = store["inject"]

    def ledger(app):
        key = worlds.playthrough(app)
        return key, worlds.load(key)

    def save(key, bucket):
        """控えを書き、急ぎの依頼の写しを作り直す（錠が取れないときに窓口 `quest_claims` へ答える写し）。"""
        worlds.save(key)
        store["urgent"][key] = urgent_rows(bucket)

    def same_world(app, key):
        """背景の仕事を積んだときの周回のままか（LLM を待つ間に別のセーブを読んでいないか）。"""
        return loader_state.playthrough_key(app) == key

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
        if not GUIDE_NOTICE or event is None or event.get("quest") or incidents.is_good(event):
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
        return incidents.stage_name(event)

    def name_of(event):
        return incidents.text_of(event, "name")

    def claims_by_quest(app):
        """ほかの冒険者が狙っている・片付けた依頼 `{依頼 id: {"state", "by"}}`（窓口 `quest_claims`）。"""
        found = {}
        for row in quest_claims.claims(app):
            if row["quest"] not in found or row.get("state") == quest_claims.DONE:
                found[row["quest"]] = {"state": row.get("state"), "by": row.get("by") or ""}
        return found

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
        shop_hint, shop_turn_rule = effect_hint(kind, event.get("goods"))
        prompt = TEXT_PROMPT.format(
            town=event.get("area_name") or "その街", world=ui.world_overview(app) or "（不明）",
            overview=area_overview(app, event.get("area")), what=kind["what"],
            examples=kind["examples"], shop_hint=shop_hint, shop_turn_rule=shop_turn_rule,
            summary_rule=summary_rule(kind),
            arrive_rule=arrive_rule(kind),
            quest_rule=QUEST_GOOD if incidents.is_good(kind) else QUEST_TROUBLE)
        return [{"role": "user", "content": prompt}]

    def clean_line(value, limit):
        text = str(value or "").strip().replace("\n", "")
        return text if 0 < len(text) <= limit else ""

    def write_text(job):
        """背景で文を書かせ、出来事を始める。合わないと言われたら別の種類で書かせ直す。"""
        app, key, event_id = job["app"], job["key"], job["event"]
        answer = None
        for _attempt in range(FIT_TRIES + 1):
            if not same_world(app, key):
                # 別の世界を読み込んだ。その世界の材料で書かせない（答え待ちのまま残し、読み込み直したときに頼み直す）
                write("text: #{} left pending (another save was loaded)".format(event_id))
                return
            with worlds.lock:
                bucket = worlds.load(key)
                event = incidents.event_by_id(bucket, event_id)
                if event is None or event.get("status") != incidents.PENDING:
                    return
                kind = incidents.KIND_BY_KEY.get(event.get("kind"))
                if kind is None:
                    retire(bucket, event, ui.game_day(app))
                    save(key, bucket)
                    return
                messages = text_messages(app, event, kind)
            answer = ask_text(messages)
            if not same_world(app, key):
                write("text: #{} left pending (another save was loaded)".format(event_id))
                return
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
                    save(key, bucket)
                    return
                incidents.change_kind(event, other, rng)
                save(key, bucket)
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
            save(key, bucket)
            write("start: {} #{} {} at {} ({}) stage 1 day {} text {}".format(
                event.get("kind"), event_id, name_of(event), event.get("area_name"),
                event.get("area"), event.get("started"), "llm" if text else "table"))
            hudicons.notify(ICON_KEY, playthrough=key)  # 左下の MOD 用のボタンと一覧の行に印（一覧の行を押すと消える）
            here = same_world(app, key) and here_id(app) == event.get("area")
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
        """ほかの冒険者が片付けた依頼（窓口 `quest_claims` の DONE）の出来事を終える。`[(出来事, 片付けた人)]`。"""
        done_rows = {row["quest"]: row for row in quest_claims.claims(app, quest_claims.DONE)}
        if not done_rows:
            return []
        done = []
        for event in incidents.events_of(bucket, incidents.ACTIVE):
            quest = event.get("quest")
            row = done_rows.get(str(quest)) if quest else None
            if row is None:
                continue
            by = row.get("by") or ""
            # 名前は終えたときのものを残す（後で片付ける人が替わっても、終わった欄はこの名前で書く）
            incidents.end(bucket, event, day, incidents.END_RIVAL, REST_DAYS, by_name=by)
            # 依頼は世界から片付ける（自然に収まったときと同じ。片付けた側の MOD を外しても掲示板に戻らない）
            if quest_status(app, quest) != COMPLETED:
                bucket.setdefault("cleanup", []).append(str(quest))
            write("end: #{} {} at {} was taken by {} ({}, quest {})".format(
                event.get("id"), name_of(event), event.get("area_name"), by or "someone",
                row.get("owner"), quest))
            done.append((event, by))
        return done

    def urgent_rows(bucket):
        rows = []
        for event in incidents.events_of(bucket, incidents.ACTIVE):
            quest = event.get("quest")
            if quest and not incidents.is_good(event):
                rows.append({"quest": str(quest), "area": str(event.get("area")),
                             "why": "{}（{}）".format(name_of(event), stage_name(event))})
        return rows

    def urgent_quests(app):
        """困り事の依頼（窓口 `quest_claims` の急ぎ）。放っておくと段階が進む依頼を、ほかの冒険者に先に狙わせる。

        錠を待たない（ほかの MOD が自分の錠を持ったまま窓口に聞きに来る。窓口の決まり）。待たずに取れたとき
        （ほかのスレッドが控えを書いていない・このスレッドが持っている）だけ控えを読む。取れなければ、
        控えを書くたびに作る写し（`save`）で答える（別のスレッドと錠を逆の順に取り合って固まらないように）。
        """
        key = loader_state.playthrough_key(app)
        if worlds.lock.acquire(blocking=False):
            try:
                store["urgent"][key] = urgent_rows(worlds.load(key))
            finally:
                worlds.lock.release()
        return [dict(row) for row in store["urgent"].get(key) or []]

    quest_claims.declare_urgent(OWNER, ctx, urgent_quests)

    def retire(bucket, event, day):
        """表から消した出来事（`kinds\\` のファイルを消した）。種類の中身が無いので続けられない。

        進行中なら自然に収まった扱いで終え、依頼は片付ける。文を書かせる前なら捨てる
        （VERIFICATION.md §3.97「出来事の表」）。
        """
        if event.get("status") == incidents.PENDING:
            incidents.drop(bucket, event)
            write("retire: dropped pending #{} ({} is no longer in the table)".format(
                event.get("id"), event.get("kind")))
            return
        incidents.end(bucket, event, day, incidents.END_NATURAL, REST_DAYS)
        event.setdefault("told", {})["settled"] = True      # 種類の文が無いので「収まった」は知らせない
        if event.get("quest"):
            bucket.setdefault("cleanup", []).append(str(event["quest"]))
        write("retire: ended #{} ({} is no longer in the table)".format(event.get("id"), event.get("kind")))

    def retire_unknown(bucket, day):
        """表に無い種類の出来事を片付ける。片付けたら真。"""
        gone = [event for event in incidents.events_of(bucket)
                if event.get("status") in (incidents.PENDING, incidents.ACTIVE) and not incidents.known(event)]
        for event in gone:
            retire(bucket, event, day)
        return bool(gone)

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
            refresh = retire_unknown(bucket, day)
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
                    hudicons.notify(ICON_KEY, playthrough=key)
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
                interval_days=ROLL_DAYS, max_active=MAX_ACTIVE, by_size=SIZE_WEIGHTING,
                spread_days=ROLL_SPREAD_DAYS)
            if new is not None:
                write("roll: {} at {} ({}) day {}; asking for the text".format(
                    new.get("kind"), new.get("area_name"), new.get("area"), day))
            save(key, bucket)
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
        if incidents.is_good(event):
            return False                    # 良い出来事は依頼を作らない
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
            kind = incidents.KIND_BY_KEY.get(event.get("kind"))
            if kind is None:
                return None                 # 表から消した出来事（次の日数送りで片付く）
            brief = BRIEF.format(name=name_of(event), stage=stage_name(event), what=kind["what"],
                                 quest=incidents.text_of(event, "quest").rstrip("。"))
        # 待機の表示と押下の止め方は呼ぶ側（`board`）。掲示板の `execute` ではゲームは旗を下ろさない（VERIFICATION.md §3.97）
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
                save(key, bucket)
        write("quest: made {} {!r} for #{}".format(
            quest_id, ui.quest_value(ui.quest_of(app, quest_id), "quest_title", ""), event_id))
        return quest_id

    @ctx.wrap("__main__:DisplayQuestChoice.execute", required=False, safe=True)
    def board(orig, self, *args, **kwargs):
        """掲示板を開いた。この街で出来事が起きていて依頼がまだ無ければ、先に作らせる。

        掲示板の `execute` ではゲームは旗を下ろさず点も送らない（押した直後も `is_button_enabled` は真）。
        依頼を作らせる数秒の間はこちらで待機表示を出して押せなくする。作っている最中に届いた押下
        （表示を出す前の一瞬に押された分）は作らせずに捨てる（VERIFICATION.md §3.97）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        making = store["making_quest"]
        if not making.acquire(False):
            write("quest: a quest is being made; ignored another press of the board")
            return None
        busy = False
        try:
            if app is not None and ui.game_day(app) is not None:
                with worlds.lock:
                    key, bucket = ledger(app)
                    for event, _by in check_rival(app, bucket, ui.game_day(app)):
                        pass
                    save(key, bucket)
                    event = incidents.live_in(bucket, here_id(app))
                    target = event.get("id") if event is not None and needs_quest(app, event) else None
                    name = name_of(event) if event is not None else ""
                if target is not None:
                    # 旗がもう下りていれば（ゲームが待機を出している）重ねない。重ねると `busy_off` が下りた旗を書き戻す
                    if getattr(app, "is_button_enabled", True) is not False:
                        screen.busy_on(app)
                        busy = True
                    made = make_quest(app, target)
                    if made is not None:
                        # 作らせた回は、この掲示板の続き（`orig`）を呼ばず、手が空いてから開き直す。
                        # 選択肢は塗らずに解く（開き直した掲示板が塗る。塗ると一瞬だけ前の画面が見える）
                        screen.busy_off(app, restore=False)
                        busy = False
                        screen.say(app, BOARD_TEXT.format(name=name))
                        reopen = getattr(self, "__class__", None)
                        choice = args[0] if args else kwargs.get("choice_text", "")
                        screen.when_idle(app, lambda: app.process_choice(reopen(app), choice),
                                         proceed_on_timeout=True, tag="town incidents board")
                        return None
        except Exception:
            ctx.log_exc("town incidents: cannot prepare the quest on the board")
        finally:
            if busy:
                screen.busy_off(app, restore=False)     # 作れなかった。この後の `orig` が掲示板を塗る
            making.release()
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
                save(key, bucket)
            if lines:
                prices.refresh("town incidents solved")
                say_when_idle(app, lines, "solved")
        except Exception:
            ctx.log_exc("town incidents: cannot settle the quest end")
        return result

    def after_days(call):
        """日が進んだ後に見回る。日数の進め方には触らない（ローダの日数の関所 `durations.on_days`）。"""
        try:
            tick(call.app)
        except Exception:
            ctx.log_exc("town incidents: tick after elapse_days failed")

    durations.on_days(OWNER, ctx, after=after_days, write=write)

    # ------------------------------------------------------------ 着いた・店に入った
    def area_arrival(call):
        """街に着いた後（ローダの窓口 `arrivals`。TECH.md §3.3.21）。"""
        try:
            app = call.app
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
                save(key, bucket)
            say_when_idle(app, lines, "arrival")
        except Exception:
            ctx.log_exc("town incidents: cannot tell the arrival")

    arrivals.on(OWNER, ctx, (arrivals.AREA,), after=area_arrival, write=write)

    @ctx.wrap(SHOP_TARGET, required=False, safe=True)
    def shop_start(orig, self, *args, **kwargs):
        try:
            app = getattr(self, "app", None) or ui.find_app()
            event = live_here(app)
            if SHOP_NOTICE and event is not None:
                # いまの段で値段が動いていなければ出さない（強さ 0% のときも）。向きが変わる出来事は段の向きで選ぶ
                line = incidents.shop_text(event, PRICE_STRENGTH_PERCENT)
                if line:
                    screen.schedule(lambda: screen.say(app, line))
        except Exception:
            ctx.log_exc("town incidents: cannot tell the shop")
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------ 状況を見る窓
    def view_model(app):
        day = ui.game_day(app)
        with worlds.lock:
            _key, bucket = ledger(app)
            events = [dict(event) for event in incidents.events_of(bucket)]
        claims = claims_by_quest(app)
        areas = ui.world_areas(app) or {}
        guilds = {}
        for event in events:
            area = areas.get(str(event.get("area")))
            name = ui.facility_name(app, guild_of(area)) if area is not None else ""
            if name:
                guilds[str(event.get("area"))] = name
        here = here_id(app)
        model = incidents.view_model(events, day, claims, stage_days=STAGE_DAYS, guild_names=guilds,
                                     settle_days=SETTLE_DAYS, ended_days=ENDED_SHOWN_DAYS,
                                     in_words=(WINDOW_DAYS == incidents.DAYS_VAGUE),
                                     quest_status=lambda qid: quest_status(app, qid),
                                     effects=WINDOW_EFFECTS, strength_percent=PRICE_STRENGTH_PERCENT,
                                     here=here,
                                     quest_title=lambda qid: ui.quest_value(ui.quest_of(app, str(qid)),
                                                                            "quest_title", None))
        area = areas.get(str(here)) if here is not None else None
        model["here_name"] = str(getattr(area, "name", "") or "") if area is not None else ""
        return model

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
            store["view"] = window.open_window(app, view_model(app), write=write,
                                               on_dismiss=lambda: store.update({"view": None}))
        except Exception:
            store["view"] = None
            ctx.log_exc("town incidents: could not open the window")

    # 絵のパスは apply() の間に組む（`ctx.mod_dir` はフックの中では読めない）。
    icon_dir = getattr(ctx, "mod_dir", None) or os.path.dirname(os.path.abspath(__file__))
    hudicons.add(ctx, ICON_KEY, toggle_view, order=ICON_ORDER, label=VIEW_TITLE,
                 images=tuple(os.path.join(icon_dir, name) for name in ICON_FILES), write=write)

    # ------------------------------------------------------------ ロードの突き合わせ
    def reconcile(app):
        day = ui.game_day(app)
        with worlds.lock:
            key, bucket = ledger(app)
            if incidents.rewind(bucket, day):
                write("reconcile: forgot what happened after day {} (older save)".format(day))
            if retire_unknown(bucket, day):
                clean_up(app, bucket)
            save(key, bucket)
            pending = [event["id"] for event in incidents.events_of(bucket, incidents.PENDING)]
        for event_id in pending:
            ask_later(app, key, event_id)       # 文を書き終える前に閉じた。もう一度頼む
        store["here"] = here_id(app)
        prices.refresh("town incidents load")

    def on_load(call):
        worlds.forget()
        store["reconcile"] = True

    loads.on(OWNER, ctx, LOAD_KINDS, after=on_load, write=write)

    def refresh_choice_buttons(self, _buttons):
        if store["reconcile"]:
            characters = getattr(getattr(self, "world", None), "characters", None)
            if isinstance(characters, dict) and len(characters) > 1:
                store["reconcile"] = False
                try:
                    reconcile(self)
                except Exception:
                    ctx.log_exc("town incidents: reconcile failed")

    def offer(app, view):
        """掲示板で、出来事の依頼のボタンに印を付ける（窓口 `choices` の `offer`。組み直しのたびに呼ばれる）。

        ゲームのボタンは書き換えず、同じ spec の写し（`replace`）を出す。押下はゲームへ流す（`on_press` 無し）。
        """
        rows = [(entry, ui.spec_args(entry)) for entry in view.buttons
                if ui.spec_cls_name(entry) == QUEST_CHOICE_CLS]
        if not rows:
            return None
        with worlds.lock:
            _key, bucket = ledger(app)
            ours = {str(event.get("quest")) for event in incidents.events_of(bucket)
                    if event.get("quest") and not incidents.is_good(event)}
        out = []
        for entry, args in rows:
            quest_id = str(args[1]) if isinstance(args, (list, tuple)) and len(args) >= 2 else None
            text = entry.get("text") or ""
            if quest_id in ours and not text.endswith(QUEST_MARK):
                out.append(choices.Choice(text + QUEST_MARK, base=entry, replace=True))
        return out or None

    choices.provide(ctx, screen, refresh=refresh_choice_buttons, offer=offer)

    ctx.log("town incidents: installed ({} kind(s), roll every {}+-{} day(s) at {}%, up to {}, stage "
            "every {} day(s), settles {} day(s) after stage 3, text {}, state {})".format(
                len(enabled_kinds()), ROLL_DAYS, ROLL_SPREAD_DAYS, ROLL_PERCENT, MAX_ACTIVE, STAGE_DAYS,
                SETTLE_DAYS, "llm" if USE_LLM else "table", worlds.dir_path()))
