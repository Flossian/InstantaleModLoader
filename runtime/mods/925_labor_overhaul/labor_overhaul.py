# -*- coding: utf-8 -*-
"""役場の労働と裏の事務所の闇労働に、職種・期ごとの出来事・勤続・危険を足す。

MOD なしの労働は、役場が「16ヵ月」「32ヵ月」の2択、裏の事務所が条件1つだけで、
どちらも「働く」を決まった回数だけ押すと決まった額が入る。能力値も街の様子も働いた先も関わらない。
この MOD は「労働の募集をみる」「闇労働の募集をみる」の先を、次の形に置き換える。

    [役場]        その街の施設ごとの仕事・日雇いの人足・街の困り事の人手 から選ぶ
                  → 契約の長さを選ぶ → 「働く」のたびに賃金が入り、ときどき出来事が起きて選択肢で応じる
    [裏の事務所]  裏の仕事（危険度 1〜3）から選ぶ → 「働く」のたびに賃金が入り、ときどき危険が起きる

遊び方と数は DOC.md、規則（表と式）は `rules.py`。

##### ゲームのどこに触るか

- ゲームの「労働の募集をみる」「闇労働の募集をみる」のボタンは、同じ場所で差し替える（窓口 `choices` の `replace`。
  ゲームの一覧からは外さないので、セーブには元のボタンが残る）
- 募集の一覧から先は MOD だけの画面（`open_screen`）。ゲームの労働のクラス
  （`LaborChoiceManager` / `LaborPhaseManager` / `IllegalLaborPhaseManager` ほか）は呼ばない。
  期ごとに出来事を挟む・契約の途中で打ち切る、をそのクラスの上では組めないため
- 賃金の基準はゲームの式（`scripts.functions:get_labor_reward`）を呼ぶ。渡す難易度は `app.highest_cleared_quest_difficulty`
- 暦はゲームの `elapse_days` で進める（家賃・街の出来事などの日数の処理がそのまま走る）
- 所持金・手配度・好感度はローダの部品で書き、保存はゲーム自身の `save_game` に任せる
- 進行中の契約と雇い主ごとの勤続は `state\\labor_overhaul\\<世界>×<主人公>.json`
- 街の出来事はローダの窓口 `town_events` で読み、人手の仕事を終えたら収まる側へ、裏の仕事を終えたら悪くなる側へ1段動かす
- 勤続の段による店の値引きは、ローダの値段の共通フックに一時の段として置く（保存の直前に外れる）
"""

import random
import sys

from instantale_modloader import (abilities, choices, durations, frames, modfacility, modnpc, npcs, prices,
                                  state as loader_state, town_events, ui)

from . import rules

# ---- 設定（既定値は mod.json の "settings" と一致させること。`tools/check_mods.py` が AST で突き合わせる）
TERM_DAYS = 90                   # 役場の「働く」1回の長さ（日。29歳まで）。ゲームは 3ヵ月
SHORT_TERMS = 4                  # 短い契約の回数。ゲームは 4回（16ヵ月）
LONG_TERMS = 8                   # 長い契約の回数。ゲームは 8回（32ヵ月）
ILLEGAL_TERM_DAYS = 90           # 闇労働の1期の長さ（日。29歳まで）。ゲームは 3ヵ月
AGE_RULES = True                 # ゲームと同じく、年齢で1回が長くなり、老いると断られる
OFFICE_AGE_LIMIT = 55            # 役場で断られる年齢。ゲームは 55
ILLEGAL_AGE_LIMIT = 50           # 裏の事務所で断られる年齢。ゲームは 50
ILLEGAL_TERMS = 3                # 闇労働の期の数。ゲームは 3期
EVENT_PERCENT = 50               # 出来事が起きる確率（%。120日あたり）
ABILITY_MAX_PERCENT = 200        # 能力値による賃金の倍率の上限（%）
SENIORITY = True                 # 雇い主ごとの勤続
SHOP_DISCOUNT = True             # 勤続の段による、働いた店での値引き
INCIDENT_LINK = True             # 街の出来事との連動
ILLEGAL_PAY_MIN_PERCENT = 200    # 闇労働の賃金（危険度 1。役場の同じ長さの賃金に対する %）
ILLEGAL_PAY_MAX_PERCENT = 400    # 闇労働の賃金（危険度 3）
RISK_STRENGTH_PERCENT = 100      # 闇労働の危険の起きやすさ（%）
TOWN_GROWTH = True               # 街ごとの労働の実績（務め終えるたびに、その街の賃金の基準が上がる）
GROWTH_SHORT = 2                 # 短い契約を務め終えたときに上がる実績
GROWTH_LONG = 5                  # 長い契約を務め終えたときに上がる実績
GROWTH_MAX = 60                  # 1つの街で上がる実績の上限
# ここから下は数の調整（意味は mod.json の label / note。規則の側へは apply() のたびに `rules.configure` で渡す）
LIST_JOBS = 3
ROTATE_DAYS = 30
SHORT_BONUS_PERCENT = 12.5
LONG_BONUS_PERCENT = 25.0
SHORT_AFFINITY = 3
LONG_AFFINITY = 6
QUIT_TENURE_DAYS = 240
QUIT_BARRED_DAYS = 180
RANK2_DAYS = 480
RANK3_DAYS = 1440
RANK4_DAYS = 2880
RANK2_PAY_PERCENT = 125
RANK3_PAY_PERCENT = 150
RANK4_PAY_PERCENT = 200
RANK2_DISCOUNT_PERCENT = 5
RANK3_DISCOUNT_PERCENT = 10
RANK4_DISCOUNT_PERCENT = 15
ABILITY_LOW_PERCENT = 80
CHECK_PER_POINT = 3
GOOD_EVENT_STEP_PERCENT = 10
GOOD_EVENT_MAX_PERCENT = 130
BAD_STAGE_PERCENT = 70
RELIEF_PAY_PERCENT = 150
RELIEF_TERM_DAYS = 30
RELIEF_TERMS = 3
RELIEF_STEPS = 1
ILLEGAL_TROUBLE_PERCENT = 130
ILLEGAL_STEPS = 1
RISK_PERCENT_1 = 10
RISK_PERCENT_2 = 18
RISK_PERCENT_3 = 28
CRACKDOWN_LAW = 2
BETRAYAL_LAW = 1
BETRAYAL_TENURE_DAYS = 90
RETALIATION_TERMS = 2

# ---- 文（ゲーム画面の文は常体）
BACK_LABEL = "仕事に戻る"
LIST_TEXT = "{area}の募集の一覧に目を通した。"
# 一覧の1件は句点を挟まない1行にする（本文は句点ごとに行が分かれるので、1件が何行にもなる）
LIST_ROW = "・{name}（{details}）"
LIST_ABILITIES = "{abilities}"
LIST_PAY = "{period} {pay}G"
LIST_RANK = "勤続 {rank}"
LIST_SEP = "／"
LIST_EMPTY = "いま応じられる募集は無い。"
OTHER_CONTRACT_TEXT = "{name}の契約がまだ残っている。終えるまで、ほかの仕事は請けられない。"
GROWTH_TEXT = "この街での労働の実績は +{growth}（務め終えた契約 {done} 回）"
GROWTH_UP_TEXT = "{area}での働きぶりが知られ、この街の賃金の水準が上がった（実績 +{growth}）。"
JOB_LABEL = "{name}({pay}G)"
EMPLOYER_NAME = "{place}の手伝い"
PORTER_NAME = "日雇いの人足"
PORTER_WORK = "決まった雇い主の無い力仕事"
RELIEF_NAME = "臨時の募集・{event}の対応"
LIST_LAST = "前回の仕事"
RELIEF_WORK = "街の困り事の片付け。やり遂げると事態が収まる側へ動く"
INCIDENT_TEXT = "{event}の影響で、賃金は普段の{percent}%になっている。"
CANCEL_LABEL = "やめる"
CANCEL_TEXT = "募集の一覧を閉じた。"
TERM_TEXT = "{name}。{period}ごとに{pay}G。どれだけ働く？"
TERM_LABEL = "{period}({total}G)"
START_TEXT = "{name}で働き始めた。"
WORK_LABEL = "働く(あと{left}回)"
QUIT_LABEL = "途中でやめる"
WORKED_TEXT = "{period}を働いて過ごし、{pay}Gを得た。"
WORKED_NOTHING = "{period}を働いて過ごしたが、今期の賃金は残らなかった。"
OPTION_LABEL = "{label}({ability} {percent}%)"
RESULT_PAY = "今期の賃金は{pay}Gになった。"
FIRED_TEXT = "{name}の仕事は、ここで打ち切りになった。"
FINISH_TEXT = "{name}の契約を務め終えた。稼ぎは合わせて{total}G。"
FINISH_BONUS = "満了の割増として{bonus}Gが上乗せされた。"
RANK_UP_TEXT = "{name}での勤続が「{rank}」になった。"
QUIT_TEXT = "{name}の仕事を途中でやめた。ここまでの稼ぎは{total}G。"
QUIT_BARRED = "{owner}は当分、仕事を回してくれないだろう。"
OWNER_FALLBACK = "雇い主"
PLACE_FALLBACK = "持ち場"
AREA_FALLBACK = "この土地"

ILLEGAL_LIST_TEXT = "表に出せない仕事の口が、いくつか回ってきている。"
ILLEGAL_ROW = "・{name}（{period} {pay}G／危険 {risks} {percent}%）"
ILLEGAL_TROUBLE_TEXT = "街が{event}で乱れているぶん、裏の仕事の払いは良い。"
ILLEGAL_LOCKED_TEXT = "もっと割のいい口は、ここで仕事を重ねた者にしか回ってこない。"
ILLEGAL_TERM_TEXT = "{name}。{period}ごとに{pay}G。受けたら最後まで降りられない。"
ILLEGAL_START_TEXT = "{name}を請けた。"
ILLEGAL_WORK_LABEL = "仕事に出る(あと{left}期)"
ILLEGAL_WORKED_TEXT = "{period}を裏の仕事に費やし、{pay}Gを得た。"
CRACKDOWN_TEXT = "取り締まりに踏み込まれた。逃げ切ったが顔を見られ、今期の稼ぎは置いてきた。"
RETALIATION_TEXT = "仕事の相手方から仕返しを受けた。今期の稼ぎに加えて{loss}Gを奪われた。"
BETRAYAL_TEXT = "組んでいた者に売られた。ここまでの稼ぎ{loss}Gは消え、役人に名前が伝わった。仕事は打ち切りになった。"
#: 失う稼ぎが無かったとき（この契約で、まだ稼ぎが残っていない）。
BETRAYAL_TEXT_EMPTY = "組んでいた者に売られた。役人に名前が伝わり、仕事は打ち切りになった。"
ILLEGAL_FINISH_TEXT = "{name}を務め終えた。稼ぎは合わせて{total}G。"

#: 人生の記録（`life_log`）に書く1行。頭の印と言い方は、ゲームが労働の後に書く記録に合わせる（GAME.md の労働の節）。
RECORD_MARK = "〈労働の記録〉"
RECORD_FINISHED = "{player}は{area}の{name}を{period}務め上げ、給与を得た。"
RECORD_QUIT = "{player}は{area}の{name}に従事したが、{period}で途中で辞めた。"
RECORD_FIRED = "{player}は{area}の{name}に従事したが、{period}で暇を出された。"
RECORD_ILLEGAL = "{player}は{area}で{period}のあいだ表に出せない仕事を請け、報酬を得た。"
RECORD_ILLEGAL_FIRED = "{player}は{area}で表に出せない仕事を請けたが、{period}で仲間に売られた。"

# ---- コード側の決まり
OWNER = "925_labor_overhaul"
LOG_BASENAME = "labor_overhaul.log"
STATE_DIRNAME = "labor_overhaul"
STORE_ATTR = "_instantale_labor_overhaul"
MARK = "mod_labor_overhaul"

OFFICE_TYPE = "administrative_office"
UNDERWORLD_TYPE = "underworld_office"
#: ゲームの労働のボタンの spec のクラス名（GAME.md §2.20 の役場の選択肢）。
OFFICE_BUTTON_CLS = "DisplayLongTermLaborChoice"
ILLEGAL_BUTTON_CLS = "DisplayIllegalLaborChoice"
REWARD_MODULE = "scripts.functions"
REWARD_FUNCTION = "get_labor_reward"
DIFFICULTY_ATTR = "highest_cleared_quest_difficulty"
#: 賃金の式に渡す難易度の上限（ゲームが難易度に持てる上限。GAME.md の難易度の節）。
DIFFICULTY_TOP = 100

JOBS_SCREEN = "jobs"
TERMS_SCREEN = "terms"
WORK_SCREEN = "work"

AFFINITY_MIN = -100
AFFINITY_MAX = 100

money = ui.money
coins = ui.rewrite_coins


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5。ローダの state.process_store が足りない鍵を埋める）。"""
    return loader_state.process_store(STORE_ATTR, {"worlds": None, "rng": random.Random()})


def rule_values():
    """規則の側（`rules.py`）へ渡す設定の値。規則は設定を読まないので、入口がまとめて渡す。"""
    return rules.values_from_settings(globals().get)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    rules.configure(**rule_values())
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(
            ctx, STATE_DIRNAME, default=rules.new_bucket, order=rules.order_bucket, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    screen = ui.Screen(ctx, write, tag="labor", mark=MARK)
    save_soon = ui.saver(ctx, write, "labor")
    state = {"owner": None}

    def rng():
        return store["rng"]

    def ledger(app):
        key = worlds.playthrough(app)
        return key, worlds.load(key)

    # ------------------------------------------------------------ 世界を読む
    def area_id_here(app):
        area_id = ui.area_id_of(ui.current_area(app))
        return str(area_id) if area_id not in (None, "") else None

    def area_name_here(app):
        # 名前は切らない（ローダの ui.area_name。空白だけの名前は無いとみなす）
        return ui.area_name(app, ui.current_area(app), fallback=AREA_FALLBACK, limit=None)

    def facility_type_here(app):
        return ui.facility_type_of(ui.current_facility(app))

    def facility_id_of(facility, fallback=None):
        value = getattr(facility, "id", None)
        return str(value if value not in (None, "") else fallback)

    def busy(app):
        return ui.busy_flags(app)       # 立っている忙しさの旗（ui.BUSY_FLAGS の順）

    def growth_here(app, bucket):
        """いま居る街の労働の実績（賃金の基準の難易度に足す数）。"""
        if not TOWN_GROWTH:
            return 0
        return rules.town_growth(bucket, area_id_here(app))

    def base_reward(app, growth=0):
        """ゲームの式の賃金（120日ぶん）。読めなければ None（そのときはゲームの労働のままにする）。

        渡す難易度は、主人公が片付けた依頼の最高難易度に、その街の労働の実績を足した数。
        """
        functions = sys.modules.get(REWARD_MODULE)
        reward = getattr(functions, REWARD_FUNCTION, None)
        if not callable(reward):
            return None
        difficulty = getattr(app, DIFFICULTY_ATTR, None)
        if not frames.is_number(difficulty):
            difficulty = 0
        difficulty = min(DIFFICULTY_TOP, difficulty + max(0, int(growth)))
        try:
            value = reward(difficulty)
        except Exception:
            ctx.log_exc("labor: {} failed".format(REWARD_FUNCTION))
            return None
        if not frames.is_number(value) or value <= 0:
            return None
        return value

    # 主人公の年齢。読めなければ None（そのときは年齢の決まりを当てない。ローダの durations.age_of）
    age_of = durations.age_of

    def term_for_age(app, days):
        """設定の長さ（29歳までの長さ）を、主人公の年齢の長さにする。"""
        age = age_of(app)
        if not AGE_RULES or age is None:
            return max(1, int(days))
        return rules.age_days(days, age)

    def too_old(app, kind):
        """その年齢では、ゲームが労働を断るか（役場と裏の事務所で境目が違う）。"""
        age = age_of(app)
        if not AGE_RULES or age is None:
            return False
        return age >= int(OFFICE_AGE_LIMIT if kind == rules.OFFICE else ILLEGAL_AGE_LIMIT)

    def scores(app):
        try:
            found = abilities.scores(app, getattr(app, "player", None))
        except Exception:
            ctx.log_exc("labor: cannot read the ability scores")
            found = None
        return found if isinstance(found, dict) else {}

    def town_event(app, area_id):
        if not INCIDENT_LINK:
            return None
        return town_events.current(app, area_id)

    def owner_name_of(app, owner_id):
        if owner_id in (None, ""):
            return OWNER_FALLBACK
        return ui.character_name(app, owner_id, fallback=OWNER_FALLBACK)

    def nodes_here(app):
        """雇い主を探す区画。主人公がいま居る区画（ローダの `modfacility.node_here`）。引けなければ土地の全部。

        1つの土地が区画を何組も持つ世界がある。区画どうしは繋がっていないので、
        いま居る区画の外の施設は、一覧に出しても歩いて行けない（ゲームの売買も、いま居る区画の施設を引く）。
        """
        area = ui.current_area(app)
        try:
            node = modfacility.node_here(app, area)
        except Exception:
            ctx.log_exc("labor: cannot tell the current node")
            node = None
        return [node] if node is not None else ui.nodes_of(area)

    def employers_here(app):
        """いま居る区画の、主の居る施設 `[(種類, 施設 id, 施設の名前, 主の人物 id)]`。表の並び順。

        MOD が建てた施設は雇い主にしない（主が世界の人物ではない）。
        """
        found = []
        seen = set()
        for node in nodes_here(app):
            for key, facility in ui.facilities_of(node).items():
                kind = ui.facility_type_of(facility)
                owner_id, _owner = ui.facility_owner(app, facility)     # 主が居なければ None
                facility_id = facility_id_of(facility, key)
                if kind not in rules.OFFICE_JOB_BY_TYPE or owner_id is None or facility_id in seen:
                    continue
                if modfacility.is_mod_facility(facility_id) or modnpc.is_mod_npc(owner_id):
                    continue
                place = ui.facility_name(app, facility) or PLACE_FALLBACK
                # 同じ種類・同じ名前の施設は1つだけ並べる（区画が何組もある街では、同じ施設が区画ごとに在る）
                if (kind, place) in seen:
                    continue
                seen.add(facility_id)
                seen.add((kind, place))
                found.append((kind, facility_id, place, str(owner_id)))
        order = [row[0] for row in rules.OFFICE_JOBS]
        found.sort(key=lambda row: (order.index(row[0]), ui.id_sort_key(row[1])))
        return found

    # ------------------------------------------------------------ 募集を組む
    def office_offers(app, bucket):
        """役場の募集 `[offer]`。offer は JSON にできる辞書（画面の控えに書く）。"""
        base = base_reward(app, growth_here(app, bucket))
        if base is None:
            return []
        area_id = area_id_here(app)
        day = ui.game_day(app)
        mine = scores(app)
        event = town_event(app, area_id)
        incident = rules.incident_percent(event)
        offers = []
        open_rows = []
        for kind, facility_id, place, owner_id in employers_here(app):
            key = rules.employer_key(area_id, facility_id)
            row = rules.employer_row(bucket, key, owner_id) if SENIORITY else None
            if row is not None and rules.barred(bucket, key, day):
                continue
            open_rows.append((key, kind, place, owner_id))
        # 並べるのは決まった数だけ。顔ぶれは入れ替えの間隔ごとに変わり、その間は開き直しても同じ。
        # 前回この土地で務め終えた仕事は、選ばれていなくても追加の枠で並べる。
        seed = "{}|{}|{}".format(loader_state.playthrough_key(app), area_id, rules.rotation_of(day))
        listed = set(rules.pick_listed([row[0] for row in open_rows], seed))
        again = rules.last_done(bucket, area_id)
        for key, kind, place, owner_id in open_rows:
            if key not in listed and key != again:
                continue
            _kind, job_abilities, work = rules.OFFICE_JOB_BY_TYPE[kind]
            tenure = rules.tenure_days(bucket, key) if SENIORITY else 0
            percents = (rules.ability_percent(rules.ability_average(mine, job_abilities), ABILITY_MAX_PERCENT),
                        rules.rank_pay_percent(tenure), incident)
            offers.append({
                "kind": rules.OFFICE, "job": kind, "employer": key,
                "name": EMPLOYER_NAME.format(place=place), "place": place, "work": work,
                "owner": owner_id, "owner_name": owner_name_of(app, owner_id),
                "abilities": list(job_abilities), "term_days": term_for_age(app, TERM_DAYS),
                "pay": rules.term_pay(base, TERM_DAYS, percents),
                "rank": rules.rank_name(tenure) if SENIORITY else "",
                "again": key == again,
                "terms": [[int(SHORT_TERMS), False], [int(LONG_TERMS), True]]})
        offers.append({
            "kind": rules.OFFICE, "job": rules.PORTER, "employer": None,
            "name": PORTER_NAME, "place": PLACE_FALLBACK, "work": PORTER_WORK,
            "owner": None, "owner_name": OWNER_FALLBACK, "abilities": [],
            "term_days": term_for_age(app, TERM_DAYS), "pay": rules.term_pay(base, TERM_DAYS),
            "rank": "", "terms": [[int(SHORT_TERMS), False], [int(LONG_TERMS), True]]})
        if rules.is_trouble(event):
            # 人手の仕事は、出来事の段の間隔より長い契約にしない
            if int(TERM_DAYS) <= rules.RELIEF_TERM_DAYS:
                days, terms = int(TERM_DAYS), int(SHORT_TERMS)
            else:
                days, terms = rules.RELIEF_TERM_DAYS, rules.RELIEF_TERMS
            percents = (rules.ability_percent(rules.ability_average(mine, rules.RELIEF_ABILITIES),
                                              ABILITY_MAX_PERCENT), rules.RELIEF_PAY_PERCENT)
            offers.append({
                "kind": rules.OFFICE, "job": rules.RELIEF, "employer": None,
                "name": RELIEF_NAME.format(event=event.get("name") or "困り事"),
                "place": PLACE_FALLBACK, "work": RELIEF_WORK,
                "owner": None, "owner_name": OWNER_FALLBACK,
                "abilities": list(rules.RELIEF_ABILITIES), "term_days": term_for_age(app, days),
                "pay": rules.term_pay(base, days, percents), "rank": "", "terms": [[terms, False]]})
        return offers

    def illegal_offers(app, bucket):
        """裏の事務所の募集。危険度の高い仕事は、この事務所での勤続が要る。`(offers, 隠れた仕事があるか)`。"""
        base = base_reward(app, growth_here(app, bucket))
        if base is None:
            return [], False
        area_id = area_id_here(app)
        facility = ui.current_facility(app)
        key = rules.employer_key(area_id, facility_id_of(facility))
        owner_id, _owner = ui.facility_owner(app, facility)     # 主が居なければ None
        if SENIORITY:
            rules.employer_row(bucket, key, owner_id)
        tenure = rules.tenure_days(bucket, key) if SENIORITY else 0
        rank = rules.rank_index(tenure)
        mine = scores(app)
        trouble = rules.is_trouble(town_event(app, area_id))
        offers, locked = [], False
        for job in rules.ILLEGAL_JOBS:
            if SENIORITY and rank < rules.DANGER_NEEDS_RANK.get(job["danger"], 0):
                locked = True
                continue
            percents = [rules.illegal_pay_percent(job["danger"], ILLEGAL_PAY_MIN_PERCENT, ILLEGAL_PAY_MAX_PERCENT)]
            if trouble:
                percents.append(rules.ILLEGAL_TROUBLE_PERCENT)
            average = rules.ability_average(mine, job["abilities"])
            offers.append({
                "kind": rules.ILLEGAL, "job": job["key"], "employer": key,
                "name": job["name"], "place": PLACE_FALLBACK, "work": job["detail"],
                "owner": str(owner_id) if owner_id not in (None, "") else None,
                "owner_name": owner_name_of(app, owner_id),
                "abilities": list(job["abilities"]), "term_days": term_for_age(app, ILLEGAL_TERM_DAYS),
                "pay": rules.term_pay(base, ILLEGAL_TERM_DAYS, percents),
                "rank": rules.rank_name(tenure) if SENIORITY else "", "danger": job["danger"],
                "risk": round(risk_percent_for(job, average, term_for_age(app, ILLEGAL_TERM_DAYS), tenure), 1),
                "terms": [[int(ILLEGAL_TERMS), False]]})
        return offers, locked

    def risk_percent_for(job, average, term_days, tenure):
        percent = rules.risk_percent(job["danger"], average, term_days, tenure if SENIORITY else 0)
        return max(0.0, min(float(rules.RISK_PERCENT_CAP), percent * max(0, int(RISK_STRENGTH_PERCENT)) / 100.0))

    # ------------------------------------------------------------ 入口のボタン
    def contract_here(app, bucket, kind):
        """この場所で続きを働ける契約。別の土地で残した契約は、罰なしで流す。"""
        contract = bucket.get("contract")
        if not isinstance(contract, dict):
            return None
        if contract.get("area") != area_id_here(app):
            return None
        return contract if contract.get("kind") == kind else None

    def other_contract(app, bucket, kind):
        """この場所に残っている、種類の違う契約（役場と裏の事務所）。上書きすると罰なしで消えるので、始めさせない。"""
        contract = bucket.get("contract")
        if not isinstance(contract, dict) or contract.get("kind") == kind:
            return None
        return contract if contract.get("area") == area_id_here(app) else None

    def offer(app, view):
        """ゲームの労働のボタンを、同じ場所で差し替える（窓口 `choices` の `offer`）。"""
        buttons = view.buttons
        if not buttons or not ui.is_facility_screen(buttons) or busy(app):
            return None
        here = facility_type_here(app)
        if here == OFFICE_TYPE:
            kind, cls_name = rules.OFFICE, OFFICE_BUTTON_CLS
        elif here == UNDERWORLD_TYPE:
            kind, cls_name = rules.ILLEGAL, ILLEGAL_BUTTON_CLS
        else:
            return None
        slot = view.find(cls_name)
        if slot is None or base_reward(app) is None:
            return None
        with worlds.lock:
            _key, bucket = ledger(app)
            going = contract_here(app, bucket, kind) is not None
        if not going and too_old(app, kind):
            # ゲームのボタンのまま出す（ゲームが「今の体力では…」と断り、乞食への道を出す）
            return None
        text = BACK_LABEL if going else (slot.get("text") or "")
        return [choices.Choice(text, base=slot, replace=True,
                               on_press=_press("back" if going else "list", text, kind))]

    # ------------------------------------------------------------ 一覧
    def open_list(app, kind):
        if too_old(app, kind):
            # 前の組み直しのボタンが残っていた（年齢が境目を越えた直後など）。ゲームのボタンに組み直す
            write("list: too old for {} (age {})".format(kind, age_of(app)))
            choices.repaint(app)
            return
        with worlds.lock:
            key, bucket = ledger(app)
            stale = bucket.get("contract")
            if isinstance(stale, dict) and contract_here(app, bucket, stale.get("kind")) is None:
                write("list: dropped a contract left in area {!r} ({})".format(
                    stale.get("area"), stale.get("name")))
                bucket["contract"] = None
            other = other_contract(app, bucket, kind)
            if other is None:
                if kind == rules.OFFICE:
                    offers, locked = office_offers(app, bucket), False
                else:
                    offers, locked = illegal_offers(app, bucket)
                growth = growth_here(app, bucket)
                done = rules.town_done(bucket, area_id_here(app))
                worlds.save(key)
        if other is not None:
            write("list: {} refused, a {} contract is going ({})".format(kind, other.get("kind"), other.get("name")))
            screen.say(app, OTHER_CONTRACT_TEXT.format(name=other.get("name") or ""))
            choices.repaint(app)
            return
        area_id = area_id_here(app)
        event = town_event(app, area_id)
        if kind == rules.OFFICE:
            screen.say(app, LIST_TEXT.format(area=area_name_here(app)))
            if TOWN_GROWTH and done:
                screen.say(app, GROWTH_TEXT.format(growth=growth, done=done))
            percent = rules.incident_percent(event)
            if percent != 100:
                screen.say(app, INCIDENT_TEXT.format(event=event.get("name") or "街の出来事", percent=percent))
        else:
            screen.say(app, ILLEGAL_LIST_TEXT)
            if rules.is_trouble(event):
                screen.say(app, ILLEGAL_TROUBLE_TEXT.format(event=event.get("name") or "困り事"))
        if not offers:
            screen.say(app, LIST_EMPTY)
            choices.repaint(app)
            return
        for row in offers:
            screen.say(app, coins(list_row(row)))
        if locked:
            screen.say(app, ILLEGAL_LOCKED_TEXT)
        write("list [{}]: {}".format(kind, ", ".join(
            "{}={}".format(row["name"], row["pay"]) for row in offers)))
        choices.open_screen(app, state["owner"], JOBS_SCREEN, {"kind": kind, "offers": offers})

    def list_row(row):
        period = rules.days_in_words(row["term_days"])
        if row["kind"] == rules.ILLEGAL:
            job = rules.ILLEGAL_JOB_BY_KEY[row["job"]]
            return ILLEGAL_ROW.format(name=row["name"], period=period,
                                      pay=money(row["pay"]), risks=rules.risk_words(job),
                                      percent=format_percent(row.get("risk")))
        words = rules.ability_words(row["abilities"])
        details = [LIST_ABILITIES.format(abilities=words)] if words else []
        details.append(LIST_PAY.format(period=period, pay=money(row["pay"])))
        if row.get("rank") and row.get("employer"):
            details.append(LIST_RANK.format(rank=row["rank"]))
        if row.get("again"):
            details.append(LIST_LAST)
        return LIST_ROW.format(name=row["name"], details=LIST_SEP.join(details))

    def format_percent(value):
        if not frames.is_number(value):
            return "?"
        return "{:g}".format(round(value, 1))

    def at_desk(app, kind):
        here = facility_type_here(app)
        return here == (OFFICE_TYPE if kind == rules.OFFICE else UNDERWORLD_TYPE)

    def jobs_screen(app, _view, data):
        """募集の一覧のボタン（窓口 `choices` の MOD だけの画面）。その場を離れていたら閉じる。"""
        if not isinstance(data, dict) or not at_desk(app, data.get("kind")):
            return None
        rows = [row for row in data.get("offers") or [] if isinstance(row, dict)]
        if not rows:
            return None
        out = []
        for index, row in enumerate(rows):
            label = coins(JOB_LABEL.format(name=row["name"], pay=money(row["pay"])))
            out.append(choices.Choice(label, on_press=_press("pick", label, [data.get("kind"), index])))
        out.append(choices.Choice(CANCEL_LABEL, on_press=_press("cancel", CANCEL_LABEL, None)))
        return out

    def pick(app, payload):
        """仕事を選んだ。契約の長さの画面へ。"""
        row = offer_at(app, payload)
        if row is None:
            close(app, "the offer is gone")
            return
        period = rules.days_in_words(row["term_days"])
        template = ILLEGAL_TERM_TEXT if row["kind"] == rules.ILLEGAL else TERM_TEXT
        screen.say(app, coins(template.format(name=row["name"], period=period, pay=money(row["pay"]))))
        choices.open_screen(app, state["owner"], TERMS_SCREEN, {"kind": row["kind"], "offer": row})

    def offer_at(app, payload):
        opened = choices.opened(app)
        data = opened.get("data") if isinstance(opened, dict) else None
        rows = data.get("offers") if isinstance(data, dict) else None
        try:
            _kind, index = payload
            row = rows[index]
        except Exception:
            return None
        return row if isinstance(row, dict) else None

    def total_of(row, terms, long_contract):
        earned = row["pay"] * terms
        if row["kind"] == rules.ILLEGAL:
            return earned
        return earned + rules.finish_bonus(earned, long_contract, relief=row["job"] == rules.RELIEF)

    def terms_screen(app, _view, data):
        """契約の長さのボタン。"""
        row = data.get("offer") if isinstance(data, dict) else None
        if not isinstance(row, dict) or not at_desk(app, row.get("kind")):
            return None
        out = []
        for terms, long_contract in row.get("terms") or []:
            label = coins(TERM_LABEL.format(period=rules.days_in_words(row["term_days"] * terms),
                                            total=money(total_of(row, terms, long_contract))))
            out.append(choices.Choice(label, on_press=_press("start", label, [terms, bool(long_contract)])))
        out.append(choices.Choice(CANCEL_LABEL, on_press=_press("cancel", CANCEL_LABEL, None)))
        return out

    def start(app, payload):
        """契約を始める。控えに書いて、働く画面へ。"""
        opened = choices.opened(app)
        data = opened.get("data") if isinstance(opened, dict) else None
        row = data.get("offer") if isinstance(data, dict) else None
        if not isinstance(row, dict):
            close(app, "the offer is gone")
            return
        terms, long_contract = payload
        contract = {
            "kind": row["kind"], "job": row["job"], "employer": row.get("employer"),
            "name": row["name"], "owner": row.get("owner"), "owner_name": row.get("owner_name"),
            "place": row.get("place"), "abilities": list(row.get("abilities") or []),
            "term_days": int(row["term_days"]), "terms": int(terms), "done": 0,
            "pay": int(row["pay"]), "earned": 0, "long": bool(long_contract),
            "started": ui.game_day(app), "area": area_id_here(app),
            "danger": row.get("danger"), "pending": None}
        with worlds.lock:
            key, bucket = ledger(app)
            other = other_contract(app, bucket, contract["kind"])
            if other is None:
                bucket["contract"] = contract
                worlds.save(key)
        if other is not None:
            screen.say(app, OTHER_CONTRACT_TEXT.format(name=other.get("name") or ""))
            close(app, "a {} contract is going".format(other.get("kind")))
            return
        write("start: {} [{}] {} x {} day(s), {} per term".format(
            contract["name"], contract["kind"], contract["terms"], contract["term_days"], contract["pay"]))
        screen.say(app, (ILLEGAL_START_TEXT if contract["kind"] == rules.ILLEGAL
                         else START_TEXT).format(name=contract["name"]))
        choices.open_screen(app, state["owner"], WORK_SCREEN)
        save_soon(app, "labor start")

    # ------------------------------------------------------------ 働く
    def contract_of(app):
        with worlds.lock:
            _key, bucket = ledger(app)
            contract = bucket.get("contract")
        return contract if isinstance(contract, dict) else None

    def work_screen(app, _view, _data):
        """働く画面のボタン。契約が無い・その場を離れていたら閉じる。"""
        contract = contract_of(app)
        if contract is None or not at_desk(app, contract.get("kind")):
            return None
        if contract.get("pending"):
            return event_choices(contract)
        left = max(0, int(contract["terms"]) - int(contract["done"]))
        if contract["kind"] == rules.ILLEGAL:
            label = ILLEGAL_WORK_LABEL.format(left=left)
            return [choices.Choice(label, on_press=_press("work", label, None))]
        label = WORK_LABEL.format(left=left)
        return [choices.Choice(label, on_press=_press("work", label, None)),
                choices.Choice(QUIT_LABEL, on_press=_press("quit", QUIT_LABEL, None))]

    def back(app, _payload):
        """進行中の契約の画面へ戻る。"""
        if contract_of(app) is None:
            choices.repaint(app)
            return
        choices.open_screen(app, state["owner"], WORK_SCREEN)

    def fill(text, contract):
        return text.format(owner=contract.get("owner_name") or OWNER_FALLBACK,
                           place=contract.get("place") or PLACE_FALLBACK)

    def work(app, _payload):
        """「働く」1回。暦を進め、賃金を払う。役場は出来事を、裏の事務所は危険を引く。"""
        contract = contract_of(app)
        if contract is None or contract.get("pending"):
            choices.repaint(app)
            return
        days = int(contract["term_days"])
        before = ui.game_day(app)
        app.elapse_days(days)
        write("work: {} term {}/{} day {} -> {}".format(
            contract["name"], int(contract["done"]) + 1, contract["terms"], before, ui.game_day(app)))
        if contract["kind"] == rules.ILLEGAL:
            illegal_term(app, contract)
            return
        event = None
        if contract["job"] != rules.PORTER:
            chance = rules.event_percent(EVENT_PERCENT, days)
            if rng().random() * 100.0 < chance:
                with worlds.lock:
                    _key, bucket = ledger(app)
                    last = bucket.get("last_event")
                event = rules.pick_event(rng(), contract["job"], last)
        if event is None:
            settle_term(app, contract, int(contract["pay"]), [])
            return
        with worlds.lock:
            key, bucket = ledger(app)
            contract["pending"] = {"event": event["key"]}
            bucket["last_event"] = event["key"]
            worlds.save(key)
        write("event: {} during {}".format(event["key"], contract["name"]))
        screen.say(app, fill(event["text"], contract))
        choices.repaint(app)
        save_soon(app, "labor event")

    def event_choices(contract):
        event = rules.EVENT_BY_KEY.get((contract.get("pending") or {}).get("event"))
        if event is None:
            label = WORK_LABEL.format(left=max(0, int(contract["terms"]) - int(contract["done"])))
            return [choices.Choice(label, on_press=_press("skip_event", label, None))]
        app = ui.find_app()
        mine = scores(app) if app is not None else {}
        out = []
        for index, option in enumerate(event["options"]):
            label = fill(option["label"], contract)
            if option["ability"]:
                label = OPTION_LABEL.format(
                    label=label, ability=rules.ABILITY_NAMES[option["ability"]],
                    percent=rules.check_percent(option["base"], mine.get(option["ability"])))
            out.append(choices.Choice(label, on_press=_press("option", label, index)))
        return out

    def skip_event(app, _payload):
        """表から消えた出来事が控えに残っていたとき。出来事なしでその期を締める。"""
        contract = contract_of(app)
        if contract is None:
            choices.repaint(app)
            return
        settle_term(app, contract, int(contract["pay"]), [])

    def option(app, index):
        """出来事の選択肢を選んだ。判定して結果を当て、その期を締める。"""
        contract = contract_of(app)
        pending = contract.get("pending") if contract is not None else None
        event = rules.EVENT_BY_KEY.get((pending or {}).get("event"))
        if contract is None or event is None or not isinstance(index, int) \
                or not 0 <= index < len(event["options"]):
            choices.repaint(app)
            return
        picked = event["options"][index]
        if picked["ability"]:
            percent = rules.check_percent(picked["base"], scores(app).get(picked["ability"]))
            won = rng().random() * 100.0 < percent
            result = picked["win"] if won else picked["lose"]
            write("option: {} #{} {} {}% -> {}".format(
                event["key"], index, picked["ability"], percent, "win" if won else "lose"))
        else:
            result = picked["result"]
            write("option: {} #{} (no check)".format(event["key"], index))
        lines = [fill(result.get("text") or "", contract)]
        pay = int(contract["pay"])
        if result.get("pay"):
            pay = max(0, int(round(pay * (100 + result["pay"]) / 100.0)))
            lines.append(coins(RESULT_PAY.format(pay=money(pay))))
        settle_term(app, contract, pay, lines, tenure=result.get("tenure") or 0,
                    affinity=result.get("affinity") or 0, law=result.get("law") or 0,
                    fired=bool(result.get("fired")))

    def change_affinity(app, contract, delta):
        owner_id = contract.get("owner")
        if not delta or owner_id in (None, ""):
            return
        try:
            current = npcs.affinity_of(app, owner_id)
            if not frames.is_number(current):
                return
            target = max(AFFINITY_MIN, min(AFFINITY_MAX, int(current) + int(delta)))
            if target != current:
                npcs.set_affinity(app, owner_id, target)
                write("affinity: {} ({}) {} -> {}".format(
                    contract.get("owner_name"), owner_id, current, target))
        except Exception:
            ctx.log_exc("labor: cannot change the affinity")

    def change_law(app, contract, delta):
        """その土地の手配度を動かす（下げる側だけ）。"""
        if not delta or delta > 0:
            return
        try:
            entry = ui.area_record(getattr(app, "player", None), contract.get("area"))
            current = ui.lawfulness_of(entry)
            if current is None:
                return
            if ui.set_lawfulness(entry, current + int(delta)):
                write("lawfulness: area {} {} -> {}".format(contract.get("area"), current, current + int(delta)))
        except Exception:
            ctx.log_exc("labor: cannot change the lawfulness")

    def refresh_status(app):
        """画面左の所持金などの表示を塗り直す（ローダの `ui.refresh_status`）。

        素の労働は「働く」のたびに塗り直している（GAME.md「役場の労働」）。
        日数送りの中でほかの MOD が所持金を動かす（330 の家賃など）ので、締めのたびにも呼ぶ。
        """
        ui.refresh_status(app)

    def pay_out(app, amount):
        """賃金を払う。払えた額を返す。"""
        amount = int(amount)
        if amount <= 0:
            return 0
        if ui.add_gold(app, amount, refresh=True) is None:
            write("WARN pay: cannot write player.gold; {} was not paid".format(amount))
            return 0
        return amount

    def take(app, amount):
        """所持金から取る。取れた額を返す（手持ちより多くは取らない）。"""
        gold = ui.gold_of(app)
        if gold is None:
            return 0
        loss = max(0, min(int(gold), int(amount)))
        if loss <= 0 or ui.add_gold(app, -loss, refresh=True) is None:
            return 0
        return loss

    def tenure_key(contract):
        return contract.get("employer") if SENIORITY else None

    def settle_term(app, contract, pay, lines, tenure=0, affinity=0, law=0, fired=False):
        """1回ぶんを締める。賃金・勤続・好感度・手配度を動かし、満了か打ち切りなら契約を終える。"""
        days = int(contract["term_days"])
        paid = pay_out(app, pay)
        period = rules.days_in_words(days)
        lines = [line for line in lines if line]
        lines.append(coins(WORKED_TEXT.format(period=period, pay=money(paid))) if paid > 0
                     else WORKED_NOTHING.format(period=period))
        rank_before = None
        rank_after = None
        with worlds.lock:
            key, bucket = ledger(app)
            contract["pending"] = None
            contract["done"] = int(contract["done"]) + 1
            contract["earned"] = int(contract["earned"]) + paid
            employer = tenure_key(contract)
            if employer:
                rank_before = rules.rank_index(rules.tenure_days(bucket, employer))
                rules.add_tenure(bucket, employer, days + int(tenure))
                rank_after = rules.rank_index(rules.tenure_days(bucket, employer))
            worlds.save(key)
        change_affinity(app, contract, affinity)
        change_law(app, contract, law)
        for line in lines:
            screen.say(app, line)
        refresh_status(app)
        if rank_after is not None and rank_after > rank_before:
            screen.say(app, RANK_UP_TEXT.format(name=contract["name"], rank=rules.RANKS[rank_after][0]))
            prices.refresh("labor rank")
        if fired:
            end_contract(app, contract, "fired")
        elif int(contract["done"]) >= int(contract["terms"]):
            end_contract(app, contract, "finished")
        else:
            choices.repaint(app)
            save_soon(app, "labor term")

    def illegal_term(app, contract):
        """裏の仕事の1期。危険が起きなければ賃金、起きれば種類ごとの損。"""
        job = rules.ILLEGAL_JOB_BY_KEY.get(contract.get("job"))
        days = int(contract["term_days"])
        pay = int(contract["pay"])
        if job is None:
            settle_illegal(app, contract, pay, [])
            return
        with worlds.lock:
            _key, bucket = ledger(app)
            tenure = rules.tenure_days(bucket, contract.get("employer")) if contract.get("employer") else 0
        average = rules.ability_average(scores(app), job["abilities"])
        percent = risk_percent_for(job, average, days, tenure)
        if rng().random() * 100.0 >= percent:
            write("risk: none ({:.1f}%) during {}".format(percent, contract["name"]))
            settle_illegal(app, contract, pay, [])
            return
        risk = rules.pick_risk(rng(), job)
        write("risk: {} ({:.1f}%) during {}".format(risk, percent, contract["name"]))
        if risk == rules.CRACKDOWN:
            change_law(app, contract, -rules.CRACKDOWN_LAW_PER_DANGER * int(job["danger"]))
            settle_illegal(app, contract, 0, [CRACKDOWN_TEXT])
        elif risk == rules.RETALIATION:
            loss = take(app, pay * rules.RETALIATION_LOSS_TERMS)
            settle_illegal(app, contract, 0, [coins(RETALIATION_TEXT.format(loss=money(loss)))])
        else:
            loss = take(app, int(contract["earned"]))
            contract["earned"] = max(0, int(contract["earned"]) - loss)
            change_law(app, contract, -rules.BETRAYAL_LAW)
            text = coins(BETRAYAL_TEXT.format(loss=money(loss))) if loss > 0 else BETRAYAL_TEXT_EMPTY
            settle_illegal(app, contract, 0, [text],
                           tenure=-int(BETRAYAL_TENURE_DAYS) - days, fired=True)

    def settle_illegal(app, contract, pay, lines, tenure=0, fired=False):
        days = int(contract["term_days"])
        paid = pay_out(app, pay)
        if paid > 0:
            lines = list(lines) + [coins(ILLEGAL_WORKED_TEXT.format(
                period=rules.days_in_words(days), pay=money(paid)))]
        with worlds.lock:
            key, bucket = ledger(app)
            contract["done"] = int(contract["done"]) + 1
            contract["earned"] = int(contract["earned"]) + paid
            employer = tenure_key(contract)
            if employer:
                rules.add_tenure(bucket, employer, days + int(tenure))
            worlds.save(key)
        for line in lines:
            screen.say(app, line)
        refresh_status(app)
        if fired:
            end_contract(app, contract, "fired")
        elif int(contract["done"]) >= int(contract["terms"]):
            end_contract(app, contract, "finished")
        else:
            choices.repaint(app)
            save_soon(app, "labor term")

    # ------------------------------------------------------------ 終える
    def end_contract(app, contract, why):
        """契約を終える（`finished` / `fired` / `quit`）。割増・好感度・街の出来事を動かし、画面を閉じる。"""
        illegal = contract["kind"] == rules.ILLEGAL
        earned = int(contract["earned"])
        lines = []
        if why == "finished":
            bonus = 0
            if not illegal:
                bonus = pay_out(app, rules.finish_bonus(earned, bool(contract.get("long")),
                                                        relief=contract["job"] == rules.RELIEF))
            total = earned + bonus
            lines.append(coins((ILLEGAL_FINISH_TEXT if illegal else FINISH_TEXT).format(
                name=contract["name"], total=money(total))))
            if bonus > 0:
                lines.append(coins(FINISH_BONUS.format(bonus=money(bonus))))
            if not illegal:
                change_affinity(app, contract, rules.FINISH_AFFINITY[1 if contract.get("long") else 0])
            nudge_town(app, contract)
        elif why == "fired":
            if not illegal:
                lines.append(FIRED_TEXT.format(name=contract["name"]))
        else:
            lines.append(coins(QUIT_TEXT.format(name=contract["name"], total=money(earned))))
        with worlds.lock:
            key, bucket = ledger(app)
            employer = tenure_key(contract)
            if why in ("quit", "fired") and employer and not illegal:
                day = ui.game_day(app)
                if why == "quit":
                    rules.add_tenure(bucket, employer, -rules.QUIT_TENURE_DAYS)
                row = rules.employer_row(bucket, employer)
                if day is not None:
                    row["barred_until"] = day + rules.QUIT_BARRED_DAYS
                    lines.append(QUIT_BARRED.format(owner=contract.get("owner_name") or OWNER_FALLBACK))
            if not illegal and why == "finished" and TOWN_GROWTH:
                # 務め終えた契約は、その街の労働の実績に数える（闇労働は数えない）
                before, after = rules.add_growth(
                    bucket, contract.get("area"),
                    GROWTH_LONG if contract.get("long") else GROWTH_SHORT, GROWTH_MAX)
                if after > before:
                    lines.append(GROWTH_UP_TEXT.format(area=area_name_here(app), growth=after))
            if not illegal:
                # 「前回の仕事」の枠は、務め終えた施設の仕事だけ（やめた・人足・臨時の募集の後は空く）
                rules.set_last_done(bucket, contract.get("area"),
                                    contract.get("employer") if why == "finished" else None)
            bucket["contract"] = None
            worlds.save(key)
        record_life(app, contract, why)
        write("end: {} [{}] {} after {}/{} term(s), earned {}".format(
            contract["name"], contract["kind"], why, contract["done"], contract["terms"], earned))
        for line in lines:
            screen.say(app, line)
        refresh_status(app)
        prices.refresh("labor end")
        close(app, why)
        save_soon(app, "labor end")

    def record_life(app, contract, why):
        """人生の記録に1行足す（ゲームの労働が書くのと同じ形）。書けなくても続ける。"""
        try:
            player = getattr(app, "player", None)
            logs = getattr(player, "life_log", None)
            day = ui.game_day(app)
            if not isinstance(logs, list) or day is None:
                return
            illegal = contract["kind"] == rules.ILLEGAL
            if illegal:
                template = RECORD_ILLEGAL if why == "finished" else RECORD_ILLEGAL_FIRED
            else:
                template = {"finished": RECORD_FINISHED, "quit": RECORD_QUIT}.get(why, RECORD_FIRED)
            started = contract.get("started")
            if isinstance(started, bool) or not isinstance(started, int):
                started = day
            text = template.format(
                player=ui.player_name(app, fallback="主人公"), area=area_name_here(app),
                name=contract["name"], period=rules.days_in_words(max(0, day - started)))
            logs.append({"day_start": started, "day_end": day, "summarized_count": 0,
                         "content": RECORD_MARK + text + "\n"})
        except Exception:
            ctx.log_exc("labor: cannot write the life log")

    def nudge_town(app, contract):
        """務め終えた仕事で街の出来事を動かす（人手は収まる側へ、裏の仕事は悪くなる側へ1段）。

        動いたことの知らせは出来事を持つ MOD が出す（窓口 `town_events` の決まり）。ここでは文を出さない。
        """
        if not INCIDENT_LINK:
            return
        area_id = contract.get("area")
        event = town_events.current(app, area_id)
        if not rules.is_trouble(event):
            return
        illegal = contract["kind"] == rules.ILLEGAL
        if not illegal and contract["job"] != rules.RELIEF:
            return
        steps = int(ILLEGAL_STEPS) if illegal else -int(RELIEF_STEPS)
        result = town_events.nudge(app, area_id, steps, OWNER)
        write("nudge: {} step(s) at {} -> {}".format(steps, area_id, result))

    def quit_work(app, _payload):
        contract = contract_of(app)
        if contract is None or contract["kind"] == rules.ILLEGAL:
            choices.repaint(app)
            return
        end_contract(app, contract, "quit")

    def cancel(app, _payload):
        screen.say(app, CANCEL_TEXT)
        close(app, "cancelled")

    def close(app, why):
        write("close: {}".format(why))
        if not choices.close_screen(app, state["owner"]):
            choices.repaint(app)

    # ------------------------------------------------------------ 値引き
    def discount_layer(item, key, price):
        """勤続の段のある店で、買値を下げる（ローダの値段の共通フックの一時の段）。触らないなら None。"""
        if not (SENIORITY and SHOP_DISCOUNT) or key != prices.BUY:
            return None
        if not frames.is_number(price) or price <= 0:
            return None
        app = ui.find_app()
        if app is None:
            return None
        facility = ui.current_facility(app)
        if ui.facility_type_of(facility) not in rules.OFFICE_JOB_BY_TYPE:
            return None
        employer = rules.employer_key(area_id_here(app), facility_id_of(facility))
        with worlds.lock:
            bucket = worlds.load(loader_state.playthrough_key(app))
            percent = rules.rank_discount_percent(rules.tenure_days(bucket, employer))
        if percent <= 0:
            return None
        return max(1, int(round(price * (100 - percent) / 100.0)))

    prices.install(ctx, write)
    prices.adjust(OWNER, discount_layer, temporary=True, write=write)

    # ------------------------------------------------------------ 押下
    ACTIONS = {"list": open_list, "back": back, "pick": pick, "start": start, "work": work,
               "option": option, "skip_event": skip_event, "quit": quit_work, "cancel": cancel}

    def _press(action, text, payload):
        """自前のボタンの押下。ゲームと同じ経路（`process_choice`）で動かす。

        使い捨てのフェーズはローダの `Screen.run_phase` が作る（`PhaseSpec` には載せない。セーブに残さない）。
        """
        def on_press(app):
            write("pressed {!r} ({})".format(text, action))
            screen.run_phase(app, text, ACTIONS[action], payload, label=action)
            return None
        return on_press

    state["owner"] = choices.provide(
        ctx, screen, offer=offer,
        screens={JOBS_SCREEN: jobs_screen, TERMS_SCREEN: terms_screen, WORK_SCREEN: work_screen})

    ctx.log("labor overhaul: {} day(s) x {}/{} at the office, {} day(s) x {} underground, events {}%, log={}"
            .format(TERM_DAYS, SHORT_TERMS, LONG_TERMS, ILLEGAL_TERM_DAYS, ILLEGAL_TERMS, EVENT_PERCENT,
                    ctx.out_path(LOG_BASENAME)))
