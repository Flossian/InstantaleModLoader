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
- 賃金の基準はゲームの式（`scripts.functions:get_labor_reward`）と、同じ難易度の依頼1件の報酬（`get_quest_reward`）の
  割合の高い方。渡す難易度は `app.highest_cleared_quest_difficulty` にその街の労働の実績を足した数
- 暦はゲームの `elapse_days` で進める（家賃・街の出来事などの日数の処理がそのまま走る）
- 所持金・手配度・好感度はローダの部品で書き、保存はゲーム自身の `save_game` に任せる
- 進行中の契約と雇い主ごとの勤続は `state\\labor_overhaul\\<世界>×<主人公>.json`
- 街の出来事はローダの窓口 `town_events` で読み、人手の仕事を終えたら収まる側へ、裏の仕事を終えたら悪くなる側へ1段動かす
- 勤続の段による店の値引きは、ローダの値段の共通フックに一時の段として置く（保存の直前に外れる）
- 募集の一覧は MOD のウィンドウ（`window.py`。雇い主の顔・勤続・契約のボタンの札）。開けなければ選択肢の一覧に戻る
- 契約の最中は、背景の上に期のマスの帯を出す（`window.Strip`。押下は素通し）。出し入れは組み直しの後（窓口 `choices` の `after`）
- 務め終えたら、雇い主の立ち絵をゲームの `update_character_image` で出し、一言を LLM に書かせる（ローダの `llm.ask`）
"""

import copy
import importlib
import os
import random
import sys

from instantale_modloader import (abilities, choices, durations, frames, ids, items, llm, modfacility, modnpc,
                                  npcs, prices, state as loader_state, town_events, ui, value_limits)

from . import rules, view, window

# ---- 設定（既定値は mod.json の "settings" と一致させること。`tools/check_mods.py` が AST で突き合わせる）
TERM_DAYS = 90                   # 役場の「働く」1回の長さ（日。29歳まで）。ゲームは 3ヵ月
SHORT_TERMS = 4                  # 短い契約の回数。ゲームは 4回（16ヵ月）
LONG_TERMS = 8                   # 長い契約の回数。ゲームは 8回（32ヵ月）
ILLEGAL_TERM_DAYS = 90           # 闇労働の1期の長さ（日。29歳まで）。ゲームは 3ヵ月
AGE_RULES = True                 # ゲームと同じく、年齢で1回が長くなり、老いると断られる
OFFICE_AGE_LIMIT = 55            # 役場で断られる年齢。ゲームは 55
ILLEGAL_AGE_LIMIT = 50           # 裏の事務所で断られる年齢。ゲームは 50
ILLEGAL_TERMS = 3                # 闇労働の期の数。ゲームは 3期
EVENT_PERCENT = 30               # 出来事が起きる確率（%。1期ごと。闇労働は危険が起きなかった期だけ）
QUEST_PAY_PERCENT = 20           # 賃金の基準: 同じ難易度の依頼1件の報酬に対する割合（%。ゲームの式の値が下限）
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
LIST_WINDOW = True               # 募集の一覧をウィンドウ（雇い主の顔と勤続の札）で出す
PROGRESS_STRIP = True            # 契約の最中に、期ごとの結果の帯を出す
FINISH_SCENE = True              # 務め終えたとき、雇い主の立ち絵を出す
FINISH_LINE = True               # 務め終えたときの雇い主の一言を LLM に書かせる（切ると決まった一言）
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
AVOID_PERCENT_1 = 60
AVOID_PERCENT_2 = 50
AVOID_PERCENT_3 = 40
BRIBE_PAY_PERCENT = 100
ITEM_VALUE_TERMS = 2
ITEM_FALLBACK_PERCENT = 20

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
OTHER_CONTRACT_TEXT = "{name}の契約がまだ残っている。終えるまで、ほかの仕事は引き受けられない。"
GROWTH_TEXT = "この街での労働の実績は +{growth}（務め終えた契約 {done} 回）"
GROWTH_UP_TEXT = "{area}での働きぶりが知られ、この街の賃金の水準が上がった（実績 +{growth}）。"
JOB_LABEL = "{name}({pay}G)"
EMPLOYER_NAME = "{place}の{role}"
PORTER_NAME = "日雇いの人足"
PORTER_WORK = "雇い主を決めずに、その日の力仕事をする。"
RELIEF_NAME = "臨時の募集・{event}の対応"
LIST_LAST = "前回の仕事"
RELIEF_WORK = "街の困り事を片付ける。務め終えると事態が1段収まる。"
INCIDENT_TEXT = "{event}の影響で、賃金は普段の{percent}%になっている。"
CANCEL_LABEL = "やめる"
CANCEL_TEXT = "募集の一覧を閉じた。"
TERM_TEXT = "{name}。{period}ごとに{pay}G。どれだけ働く？"
TERM_LABEL = "{period}({total}G)"
START_TEXT = "{name}で働き始めた。"
#: 施設の仕事（名前が「〈施設名〉の〈役名〉」）を始めたとき。
START_ROLE_TEXT = "{name}として働き始めた。"
WORK_LABEL = "働く(あと{left}回)"
QUIT_LABEL = "途中でやめる"
WORKED_TEXT = "{period}を働いて過ごし、{pay}Gを得た。"
WORKED_NOTHING = "{period}を働いて過ごしたが、今回の賃金は残らなかった。"
OPTION_LABEL = "{label}({ability} {percent}%)"
#: 選択肢の文言と確率の括弧を合わせた字数がこれを超えたら、括弧の前で改行する（窓の横縦の比で分ける）。
#: 選択肢の列は 4:3 の窓では細く、ゲームが語の途中で折り返した（「賢／さ」「1,542／円」）。
WRAP_CHARS_NARROW = 12
WRAP_CHARS_WIDE = 18
WRAP_NARROW_RATIO = 1.6
RESULT_PAY = "今回の賃金は{pay}Gになった。"
FIRED_TEXT = "{name}の仕事は、ここで打ち切りになった。"
FINISH_TEXT = "{name}の契約を務め終えた。稼ぎは合わせて{total}G。"
FINISH_BONUS = "満了の割増として{bonus}Gが上乗せされた。"
RANK_UP_TEXT = "{where}で「{rank}」と認められた。"
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
ILLEGAL_START_TEXT = "{name}を引き受けた。"
ILLEGAL_WORK_LABEL = "仕事に出る(あと{left}回)"
ILLEGAL_WORKED_TEXT = "{period}を裏の仕事に費やし、{pay}Gを得た。"
CRACKDOWN_TEXT = "取り締まりに踏み込まれた。逃げ切ったが顔を見られ、今回の稼ぎは置いてきた。"
RETALIATION_TEXT = "仕事の相手方から仕返しを受けた。今回の稼ぎに加えて{loss}Gを奪われた。"
BETRAYAL_TEXT = "組んでいた者に売られた。ここまでの稼ぎ{loss}Gは消え、役人に名前が伝わった。仕事は打ち切りになった。"
#: 失う稼ぎが無かったとき（この契約で、まだ稼ぎが残っていない）。
BETRAYAL_TEXT_EMPTY = "組んでいた者に売られた。役人に名前が伝わり、仕事は打ち切りになった。"
ILLEGAL_FINISH_TEXT = "{name}を務め終えた。稼ぎは合わせて{total}G。"
BRIBE_LABEL = "{label}({cost}G・{ability} {percent}%)"
BRIBE_PAID = "見張りに{cost}Gを握らせた。"
ITEM_TEXT = "{owner}から「{item}」を受け取った。"
ITEM_FALLBACK_TEXT = "{owner}から心付けとして{pay}Gを受け取った。"
#: 品揃えの雛形を持たない施設で、雇い主が渡す品を LLM に書かせる頼み（`312_` の新しい品揃えと同じ項目）。
ITEM_PROMPT = (
    "あなたはRPGのアイテムの作り手だ。\n【指示】\n"
    "{owner}（{job}）が、{place}で働いた{player}に礼として品を1つ渡す。その品を1つ作れ。"
    "{place}の仕事や{owner}の職業にちなむ物にする。\n\n"
    "【場所】\n土地: {area}\n施設: {place}（{kind}）\n{description}\n\n"
    "【アイテムの生成要素】\n"
    "- item_name: 日本語で。\n"
    "- description: 日本語で1〜2文。\n"
    "- item_type と item_sub_type: 次の組から選ぶ。{types}\n"
    "- item_appearance: 英語で短い一文、画像の選択に使う。"
    "(例:['A worn leather pouch tied with a frayed cord.'])\n"
    "- rarity: {rarities} のいずれか。礼の品なので common か rare。\n")
ITEM_REQUEST = "＜礼の品＞"
ITEM_MANAGER = "mod_labor_gift_item"
ITEM_TIMEOUT = 60

# 募集のウィンドウ（見出しと添え書き）
WINDOW_TITLE = "労働の募集"
WINDOW_TITLE_ILLEGAL = "闇労働の募集"
WINDOW_GROWTH = ("この街での実績", "+{growth}（満了 {done}回）")
WINDOW_ROTATE = ("募集の入れ替えまで", "あと {days}日")
WINDOW_TERM = ("1回の長さ", "{period}")
WINDOW_TERM_AGE = ("1回の長さ", "{period}（{age}歳）")
WINDOW_OWNER = ("雇い主", "{owner}")
WINDOW_RANK = ("勤続", "{rank}")
WINDOW_ILLEGAL_TERM = ("1回の長さ", "{period}")
# 務め終えた場面
FINISH_OK_LABEL = "やった"
FINISH_SAY = "{owner}:「{line}」"
FINISH_FALLBACK = "よく務めてくれた。また手が要るときは頼む"
FINISH_FALLBACK_ILLEGAL = "上出来だ。次の口が来たら、また回してやる"
#: 雇い主の一言の頼み（LLM へ）。人物の欄は世界の人物の控えから写す。
FINISH_PROMPT = (
    "あなたはゲームの登場人物を演じ、その人物の台詞を1つだけ書く係です。\n"
    "人物: {owner}（{job}）\n性格: {personality}\n話し方: {speech}\n経歴: {profile}\n\n"
    "{situation}\n"
    "働いた間の出来事:\n{events}\n"
    "勤続: {tenure}\n\n"
    "{owner}が、去り際の{player}にかける一言を書いてください。\n"
    "- 日本語で、かぎ括弧を付けず、台詞だけを1〜2文・60字まで\n"
    "- 働きぶりか出来事に触れる。新しい物や場所の名前は作らない\n"
    "- 人物の話し方に合わせる")
FINISH_SITUATION = "{player}が、{owner}の営む{place}で{period}働く契約を務め終えた。"
FINISH_SITUATION_ILLEGAL = "{player}が、{owner}から回された表に出せない仕事「{name}」を{period}務め終えた。"
FINISH_NO_EVENTS = "（目立った出来事は無かった）"
FINISH_LINE_MAX = 80
FINISH_TIMEOUT = 60
FINISH_MANAGER = "mod_labor_finish_line"

#: 人生の記録（`life_log`）に書く1行。頭の印と言い方は、ゲームが労働の後に書く記録に合わせる（GAME.md の労働の節）。
RECORD_MARK = "〈労働の記録〉"
RECORD_FINISHED = "{player}は{area}の{name}を{period}務め上げ、給与を得た。"
RECORD_QUIT = "{player}は{area}の{name}に従事したが、{period}で途中で辞めた。"
RECORD_FIRED = "{player}は{area}の{name}に従事したが、{period}で暇を出された。"
RECORD_ILLEGAL = "{player}は{area}で{period}のあいだ表に出せない仕事を引き受け、報酬を得た。"
RECORD_ILLEGAL_FIRED = "{player}は{area}で表に出せない仕事を引き受けたが、{period}で仲間に売られた。"
RECORD_ILLEGAL_QUIT = "{player}は{area}で表に出せない仕事を引き受けたが、{period}で手を引いた。"

# ---- コード側の決まり
OWNER = "349_labor_overhaul"
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
QUEST_REWARD_FUNCTION = "get_quest_reward"
DIFFICULTY_ATTR = "highest_cleared_quest_difficulty"
#: 賃金の式に渡す難易度の上限（ゲームが難易度に持てる上限。GAME.md の難易度の節）。
DIFFICULTY_TOP = 100

JOBS_SCREEN = "jobs"
TERMS_SCREEN = "terms"
WORK_SCREEN = "work"
FINISH_SCREEN = "finish"

#: 闇労働の仕事の様子のアイコンの置き場（`<種類>_<番号>.png`。`tools/make_labor_icons.py` が書く）。
#: `ctx.mod_dir` は apply() の間しか使えないので、モジュールの場所から引く。
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

#: 店の品が持つ値札の欄（GAME.md §2.13.1。持ち主が替わればゲームが付け直す）。渡す品からは外す。
SHOP_PRICE_KEYS = ("買価", "売価")

AFFINITY_MIN = -100
AFFINITY_MAX = 100


def affinity_after(current, delta):
    """労働で動かした後の好感度。上げるのは `AFFINITY_MAX` まで、下げるのは `AFFINITY_MIN` まで。

    範囲の外に居る相手（同行の依頼で 100 を超えるのは普通）は範囲へ引き戻さない
    （上がる場面で下がらない。337 と同じ切り方）。
    """
    current, delta = int(current), int(delta)
    if delta > 0:
        return max(current, min(AFFINITY_MAX, current + delta))
    return min(current, max(AFFINITY_MIN, current + delta))

money = ui.money
coins = ui.rewrite_coins


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5。ローダの state.process_store が足りない鍵を埋める）。"""
    # window / strip: 開いている募集のウィンドウと契約の帯（1枚ずつ）。portrait: 立ち絵を出した雇い主の id
    return loader_state.process_store(STORE_ATTR, {"worlds": None, "rng": random.Random(), "window": None,
                                                   "strip": None, "portrait": None})


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
    # 契約の帯（押下は素通し）。帯の中身は `store["strip_model"]` に控え、同じ中身なら描き直さない
    strip = window.Strip(store, write=write)

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
        """賃金の基準（90日ぶん）。読めなければ None（そのときはゲームの労働のままにする）。

        渡す難易度は、主人公が片付けた依頼の最高難易度に、その街の労働の実績を足した数。
        ゲームの労働の式の値と、同じ難易度の依頼1件の報酬の割合の高い方（`rules.base_pay`）。
        依頼の報酬が読めなければ式の値のまま。
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
        quest = None
        quest_reward = getattr(functions, QUEST_REWARD_FUNCTION, None)
        if callable(quest_reward):
            try:
                quest = quest_reward(difficulty)
            except Exception:
                ctx.log_exc("labor: {} failed".format(QUEST_REWARD_FUNCTION))
        return rules.base_pay(value, quest if frames.is_number(quest) else None)

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

    def image_of(app, owner_id, key):
        """人物の絵のパス（`image_src` の `face` / `fullbody`）。描かれていなければ None。"""
        if owner_id in (None, "") or modnpc.is_mod_npc(owner_id):
            return None
        src = getattr(ui.character_of(app, owner_id), "image_src", None)
        path = src.get(key) if isinstance(src, dict) else None
        return path if isinstance(path, str) and path.strip() and os.path.isfile(path) else None

    def job_icon(job_key):
        """裏の仕事の様子のアイコン（種類ごとに数枚から1枚）。無ければ None。一覧を開くたびに選び直す。"""
        found = sorted(name for name in (os.listdir(ICON_DIR) if os.path.isdir(ICON_DIR) else ())
                       if name.startswith(job_key + "_") and name.endswith(".png"))
        if not found:
            return None
        # 出来事や危険の乱数（`rng()`）とは分ける。見た目だけの選び方で、判定の乱数の並びを動かさない
        return os.path.join(ICON_DIR, random.choice(found))

    def strip_face(app, contract):
        """帯の見出しの絵。闇労働は契約の仕事のアイコン、役場は雇い主の顔。"""
        icon = contract.get("icon")
        if contract.get("kind") == rules.ILLEGAL and isinstance(icon, str) and os.path.isfile(icon):
            return icon
        return image_of(app, contract.get("owner"), "face")

    def affinity_or_none(app, owner_id):
        if owner_id in (None, ""):
            return None
        try:
            value = npcs.affinity_of(app, owner_id)
        except Exception:
            return None
        return int(value) if frames.is_number(value) else None

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
    def office_role(app, key, kind, tenure):
        """その雇い主の、いまの役 `(役名, 仕事の中身)`。勤続の段で絞り、入れ替えの回ごとに選び直す。"""
        seed = "{}|{}|{}".format(loader_state.playthrough_key(app), key, rules.rotation_of(ui.game_day(app)))
        return rules.office_role(kind, rules.rank_index(tenure) if SENIORITY else None, seed)

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
            _kind, job_abilities, _work = rules.OFFICE_JOB_BY_TYPE[kind]
            tenure = rules.tenure_days(bucket, key) if SENIORITY else 0
            role, work = office_role(app, key, kind, tenure)
            ability = rules.ability_percent(rules.ability_average(mine, job_abilities), ABILITY_MAX_PERCENT)
            percents = (ability, rules.rank_pay_percent(tenure), incident)
            offers.append({
                "kind": rules.OFFICE, "job": kind, "employer": key,
                "name": EMPLOYER_NAME.format(place=place, role=role), "place": place, "work": work,
                "owner": owner_id, "owner_name": owner_name_of(app, owner_id),
                "abilities": list(job_abilities), "term_days": term_for_age(app, TERM_DAYS),
                "pay": rules.term_pay(base, TERM_DAYS, percents),
                "rank": rules.rank_name(tenure) if SENIORITY else "",
                "again": key == again,
                "terms": [[int(SHORT_TERMS), False], [int(LONG_TERMS), True]],
                # ここから下は募集のウィンドウの札に出すだけ（賃金には使わない）
                "face": image_of(app, owner_id, "face"), "affinity": affinity_or_none(app, owner_id),
                "scores": {name: mine.get(name) for name in job_abilities}, "ability_percent": ability,
                "tenure": tenure if SENIORITY else None})
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
            ability = rules.ability_percent(rules.ability_average(mine, rules.RELIEF_ABILITIES), ABILITY_MAX_PERCENT)
            percents = (ability, rules.RELIEF_PAY_PERCENT)
            offers.append({
                "kind": rules.OFFICE, "job": rules.RELIEF, "employer": None,
                "name": RELIEF_NAME.format(event=event.get("name") or "困り事"),
                "place": PLACE_FALLBACK, "work": RELIEF_WORK,
                "owner": None, "owner_name": OWNER_FALLBACK,
                "abilities": list(rules.RELIEF_ABILITIES), "term_days": term_for_age(app, days),
                "pay": rules.term_pay(base, days, percents), "rank": "", "terms": [[terms, False]],
                "event_name": event.get("name") or "困り事", "event_stage": event.get("stage") or 1,
                "scores": {name: mine.get(name) for name in rules.RELIEF_ABILITIES}, "ability_percent": ability})
        return offers

    def barred_here(app, bucket):
        """途中でやめて、まだ雇ってもらえない雇い主（募集のウィンドウに押せない札で出す）。"""
        if not SENIORITY:
            return []
        area_id = area_id_here(app)
        day = ui.game_day(app)
        rows = []
        for kind, facility_id, place, owner_id in employers_here(app):
            key = rules.employer_key(area_id, facility_id)
            if not rules.barred(bucket, key, day):
                continue
            row = rules.employer_row(bucket, key, owner_id)
            if not rules.barred(bucket, key, day):      # 主が替わって控えが消えた
                continue
            _kind, job_abilities, _work = rules.OFFICE_JOB_BY_TYPE[kind]
            role, _work = office_role(app, key, kind, rules.tenure_days(bucket, key))
            rows.append({"name": EMPLOYER_NAME.format(place=place, role=role), "place": place, "job": kind, "owner_name": owner_name_of(
                app, owner_id), "face": image_of(app, owner_id, "face"), "abilities": list(job_abilities),
                "tenure": rules.tenure_days(bucket, key), "days_left": int(row["barred_until"]) - int(day)})
        return rows

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
                "terms": [[int(ILLEGAL_TERMS), False]],
                "face": image_of(app, owner_id, "face"), "icon": job_icon(job["key"]),
                "scores": {name: mine.get(name) for name in job["abilities"]},
                "tenure": tenure if SENIORITY else None})
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
                    barred = barred_here(app, bucket)
                else:
                    (offers, locked), barred = illegal_offers(app, bucket), []
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
        use_window = bool(LIST_WINDOW) and offers and window_ready()
        notes = list_notes(kind, event, locked)
        if kind == rules.OFFICE:
            screen.say(app, LIST_TEXT.format(area=area_name_here(app)))
            if TOWN_GROWTH and done and not use_window:
                screen.say(app, GROWTH_TEXT.format(growth=growth, done=done))
        else:
            screen.say(app, ILLEGAL_LIST_TEXT)
        if not use_window:
            for text, _key in notes:
                if text != ILLEGAL_LOCKED_TEXT:
                    screen.say(app, text)
        if not offers:
            screen.say(app, LIST_EMPTY)
            choices.repaint(app)
            return
        write("list [{}]: {}".format(kind, ", ".join(
            "{}={}".format(row["name"], row["pay"]) for row in offers)))
        if use_window:
            model = list_model(app, kind, offers, barred, growth, done, notes)
            later(lambda: open_window(app, kind, offers, model))
            choices.repaint(app)
            return
        text_list(app, kind, offers, locked)

    def text_list(app, kind, offers, locked):
        """募集を本文に並べ、選択肢の一覧で選ばせる（ウィンドウを使わないとき・開けなかったとき）。"""
        for row in offers:
            screen.say(app, coins(list_row(row)))
        if locked:
            screen.say(app, ILLEGAL_LOCKED_TEXT)
        choices.open_screen(app, state["owner"], JOBS_SCREEN, {"kind": kind, "offers": offers})

    def list_notes(kind, event, locked):
        """一覧の添え書き `[(文, 色の鍵)]`（街の出来事の効き目・隠れた仕事）。"""
        notes = []
        if kind == rules.OFFICE:
            percent = rules.incident_percent(event)
            if percent != 100:
                notes.append((INCIDENT_TEXT.format(event=event.get("name") or "街の出来事", percent=percent),
                              view.ACCENT))
        else:
            if rules.is_trouble(event):
                notes.append((ILLEGAL_TROUBLE_TEXT.format(event=event.get("name") or "困り事"), view.ACCENT))
            if locked:
                notes.append((ILLEGAL_LOCKED_TEXT, view.PLAIN))
        return notes

    # ------------------------------------------------------------ 募集のウィンドウ
    def window_ready():
        """Kivy の画面部品が使えるか（ゲームの外の検査では使えない）。"""
        try:
            importlib.import_module("kivy.uix.modalview")
        except Exception:
            return False
        return True

    def later(fn):
        """メインスレッドの次のフレームで `fn` を呼ぶ（画面の部品はメインスレッドで触る）。"""
        try:
            from kivy.clock import Clock
        except Exception:
            fn()
            return
        Clock.schedule_once(lambda _dt: screen.guarded(fn), 0)

    def coin_text(value):
        return coins(money(value) + "G")

    def list_model(app, kind, offers, barred, growth, done, notes):
        """募集のウィンドウの中身（見出し・添え書き・札）。"""
        place = ui.facility_name(app, ui.current_facility(app)) or ""
        age = age_of(app)
        if kind == rules.OFFICE:
            cards = [view.office_card(row, total_of, coin_text, SHOP_DISCOUNT) for row in offers]
            cards += [view.barred_card(row, SHOP_DISCOUNT) for row in barred]
            subtitle = []
            if TOWN_GROWTH:
                subtitle.append((WINDOW_GROWTH[0], WINDOW_GROWTH[1].format(growth=growth, done=done)))
            day = ui.game_day(app)
            if day is not None:
                every = max(1, int(ROTATE_DAYS))
                subtitle.append((WINDOW_ROTATE[0], WINDOW_ROTATE[1].format(days=every - int(day) % every)))
            period = rules.days_in_words(term_for_age(app, TERM_DAYS))
            term = WINDOW_TERM_AGE if AGE_RULES and age is not None else WINDOW_TERM
            subtitle.append((term[0], term[1].format(period=period, age=age)))
            title = WINDOW_TITLE
        else:
            cards = [view.illegal_card(row, total_of, coin_text) for row in offers]
            first = offers[0] if offers else {}
            subtitle = [(WINDOW_OWNER[0], WINDOW_OWNER[1].format(owner=first.get("owner_name") or OWNER_FALLBACK))]
            if SENIORITY and first.get("rank"):
                subtitle.append((WINDOW_RANK[0], WINDOW_RANK[1].format(rank=first["rank"])))
            subtitle.append((WINDOW_ILLEGAL_TERM[0], WINDOW_ILLEGAL_TERM[1].format(
                period=rules.days_in_words(term_for_age(app, ILLEGAL_TERM_DAYS)))))
            title = WINDOW_TITLE_ILLEGAL
        return {"title": title, "place": place, "subtitle": subtitle, "notes": notes, "cards": cards}

    def close_window():
        shown = store.get("window")
        store["window"] = None
        if shown is not None:
            try:
                shown.dismiss()
            except Exception:
                ctx.log_exc("labor: cannot close the list window")

    def open_window(app, kind, offers, model):
        """募集のウィンドウを開く。開けなければ選択肢の一覧に戻す。"""
        close_window()

        def on_pick(index, payload):
            try:
                row = offers[index]
                terms, long_contract = payload
            except Exception:
                return
            label = row.get("name") or ""
            write("window: picked {!r} x {}".format(label, terms))
            screen.run_phase(app, label, begin, row, int(terms), bool(long_contract), label="start",
                             on_error=choices.repaint)

        def dismissed():
            if store.get("window") is box["shown"]:
                store["window"] = None

        box = {"shown": None}
        try:
            box["shown"] = window.LaborWindow(app, model, on_pick=on_pick, on_dismiss=dismissed, write=write)
        except Exception:
            ctx.log_exc("labor: cannot open the list window")
            text_list(app, kind, offers, False)
            return
        store["window"] = box["shown"]
        write("window: opened [{}] {} card(s)".format(kind, len(model.get("cards") or [])))

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
            label = wrapped(coins(JOB_LABEL.format(name=row["name"], pay=money(row["pay"]))), drop=True)
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
        begin(app, row, terms, long_contract)

    def begin(app, row, terms, long_contract):
        """募集 `row` で契約を始める（選択肢の一覧からも、募集のウィンドウからも）。"""
        if not at_desk(app, row.get("kind")):
            write("start: left the desk before the contract began")
            choices.repaint(app)
            return
        contract = {
            "kind": row["kind"], "job": row["job"], "employer": row.get("employer"),
            "name": row["name"], "owner": row.get("owner"), "owner_name": row.get("owner_name"),
            "place": row.get("place"), "abilities": list(row.get("abilities") or []),
            "term_days": int(row["term_days"]), "terms": int(terms), "done": 0,
            "pay": int(row["pay"]), "earned": 0, "long": bool(long_contract),
            "started": ui.game_day(app), "area": area_id_here(app),
            "danger": row.get("danger"), "pending": None, "log": [], "rank_start": None,
            "icon": row.get("icon")}
        with worlds.lock:
            key, bucket = ledger(app)
            other = other_contract(app, bucket, contract["kind"])
            if other is None:
                employer = tenure_key(contract)
                if employer:
                    contract["rank_start"] = rules.rank_index(rules.tenure_days(bucket, employer))
                bucket["contract"] = contract
                worlds.save(key)
        if other is not None:
            screen.say(app, OTHER_CONTRACT_TEXT.format(name=other.get("name") or ""))
            close(app, "a {} contract is going".format(other.get("kind")))
            return
        write("start: {} [{}] {} x {} day(s), {} per term".format(
            contract["name"], contract["kind"], contract["terms"], contract["term_days"], contract["pay"]))
        if contract["kind"] == rules.ILLEGAL:
            started = ILLEGAL_START_TEXT
        else:
            started = START_ROLE_TEXT if view.title_keep(contract["name"], contract.get("place")) else START_TEXT
        screen.say(app, started.format(name=contract["name"]))
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
            chance = rules.event_percent(EVENT_PERCENT)
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

    def pending_event(contract):
        """控えの出来事（役場か闇労働の出来事）。無ければ None。"""
        pending = contract.get("pending") or {}
        return rules.EVENT_BY_KEY.get(pending.get("event")) or rules.ILLEGAL_EVENT_BY_KEY.get(pending.get("illegal"))

    def event_choices(contract):
        risk = (contract.get("pending") or {}).get("risk")
        if risk in rules.RISK_EVENTS:
            return risk_choices(contract, risk)
        event = pending_event(contract)
        if event is None:
            label = WORK_LABEL.format(left=max(0, int(contract["terms"]) - int(contract["done"])))
            return [choices.Choice(label, on_press=_press("skip_event", label, None))]
        app = ui.find_app()
        mine = scores(app) if app is not None else {}
        out = []
        for index, option in enumerate(event["options"]):
            label = fill(option["label"], contract)
            if option["ability"]:
                label = wrapped(OPTION_LABEL.format(
                    label=label, ability=rules.ABILITY_NAMES[option["ability"]],
                    percent=rules.check_percent(option["base"], mine.get(option["ability"]))))
            out.append(choices.Choice(label, on_press=_press("option", label, index)))
        return out

    def wrapped(label, drop=False):
        """長い選択肢は、確率の括弧の前で改行する（語の途中で折り返させない）。

        `drop` なら、括弧の前だけで1行を超えるとき括弧を外す（一覧の額。次の画面に出る。3行になってボタンから
        はみ出した）。
        """
        cut = label.rfind("(")
        if cut <= 0:
            return label
        width, height = ui.window_size()
        narrow = height > 0 and width / height < WRAP_NARROW_RATIO
        limit = WRAP_CHARS_NARROW if narrow else WRAP_CHARS_WIDE
        if len(label) <= limit:
            return label
        if drop and cut > limit:
            return label[:cut]
        return label[:cut] + "\n" + label[cut:]

    def bribe_cost(contract):
        return max(1, int(round(int(contract["pay"]) * max(0, int(BRIBE_PAY_PERCENT)) / 100.0)))

    def risk_choices(contract, risk):
        """危険を避ける選択肢。金を握らせる選択肢は、払えるときだけ出す。"""
        app = ui.find_app()
        mine = scores(app) if app is not None else {}
        gold = ui.gold_of(app) if app is not None else None
        out = []
        for index, option in enumerate(rules.RISK_EVENTS[risk]["options"]):
            label = option["label"]
            percent = rules.avoid_percent(option, contract.get("danger"), mine.get(option["ability"]))
            if option["how"] == "bribe":
                cost = bribe_cost(contract)
                if not isinstance(gold, int) or gold < cost:
                    continue
                label = wrapped(coins(BRIBE_LABEL.format(label=label, cost=money(cost),
                                                         ability=rules.ABILITY_NAMES[option["ability"]],
                                                         percent=percent)))
            elif percent is not None:
                label = wrapped(OPTION_LABEL.format(label=label, ability=rules.ABILITY_NAMES[option["ability"]],
                                                    percent=percent))
            out.append(choices.Choice(label, on_press=_press("option", label, index)))
        return out

    def skip_event(app, _payload):
        """表から消えた出来事が控えに残っていたとき。出来事なしでその期を締める。"""
        contract = contract_of(app)
        if contract is None:
            choices.repaint(app)
            return
        if contract["kind"] == rules.ILLEGAL:
            settle_illegal(app, contract, int(contract["pay"]), [], mark={"kind": "safe"})
            return
        settle_term(app, contract, int(contract["pay"]), [])

    def option(app, index):
        """出来事の選択肢を選んだ。判定して結果を当て、その期を締める。"""
        contract = contract_of(app)
        pending = contract.get("pending") if contract is not None else None
        if contract is not None and (pending or {}).get("risk") in rules.RISK_EVENTS:
            avoid(app, contract, pending["risk"], index)
            return
        event = pending_event(contract) if contract is not None else None
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
        mark = {"kind": "event", "event": event["key"], "delta": pay - int(contract["pay"]),
                "tone": event_tone(result), "fired": bool(result.get("fired")),
                "summary": "{}（{}）{}".format(fill(event["text"], contract), fill(picked["label"], contract),
                                              fill(result.get("text") or "", contract))}
        if result.get("item"):
            owner = contract.get("owner_name") or OWNER_FALLBACK
            got = give_item(app, contract)
            if got is not None:
                lines.append(ITEM_TEXT.format(owner=owner, item=got))
                mark["item"] = got
                mark["summary"] += "「{}」を受け取った。".format(got)
            else:
                extra = max(1, int(int(contract["pay"]) * max(0, int(ITEM_FALLBACK_PERCENT)) / 100.0))
                pay += extra
                mark["delta"] = pay - int(contract["pay"])
                lines.append(coins(ITEM_FALLBACK_TEXT.format(owner=owner, pay=money(extra))))
            mark["tone"] = view.GOOD
        if contract["kind"] == rules.ILLEGAL:
            change_affinity(app, contract, result.get("affinity") or 0)
            change_law(app, contract, result.get("law") or 0)
            settle_illegal(app, contract, pay, lines, tenure=result.get("tenure") or 0,
                           fired=bool(result.get("fired")), mark=mark)
            return
        settle_term(app, contract, pay, lines, tenure=result.get("tenure") or 0,
                    affinity=result.get("affinity") or 0, law=result.get("law") or 0,
                    fired=bool(result.get("fired")), mark=mark)

    def give_item(app, contract):
        """雇い主から主人公へ品を1つ作って渡す。渡した品の名前。作れなければ None。

        雇い主の施設が品揃えの雛形（`config['goods']`）を持っていれば、雛形から1件を写して作る（LLM なし）。
        持っていなければ、LLM に施設に合う品を1つ書かせ、ゲームの `generate_item_from_item_data` で作る。
        どちらもゲームの生成の入口を通るので、値付けと効果はいつもどおり付く。店の棚には触らない。
        """
        if items.inventory_of(getattr(app, "player", None)) is None:
            return None
        facility = employer_facility(app, contract)
        goods = goods_of(facility)
        if goods:
            return item_from_goods(app, contract, goods)
        return item_from_llm(app, contract, facility)

    def employer_facility(app, contract):
        """契約の雇い主の施設（いま居る区画から引く）。引けなければ None。"""
        employer = str(contract.get("employer") or "")
        facility_id = employer.split("/", 1)[1] if "/" in employer else None
        if facility_id is None:
            return None
        for node in nodes_here(app):
            for key, facility in ui.facilities_of(node).items():
                if facility_id_of(facility, key) == facility_id:
                    return facility
        return None

    def goods_of(facility):
        """施設の品揃えの雛形（セーブの品と同じ形の辞書の並び）。無ければ空。"""
        config = getattr(facility, "config", None) if facility is not None else None
        goods = config.get("goods") if isinstance(config, dict) else None
        if isinstance(goods, dict):
            goods = list(goods.values())
        return [entry for entry in goods or [] if isinstance(entry, dict) and entry.get("name")]

    def shop_price(entry):
        attributes = entry.get("attributes") if isinstance(entry.get("attributes"), dict) else {}
        for name in SHOP_PRICE_KEYS:
            if frames.is_number(attributes.get(name)):
                return attributes[name]
        return entry.get("value") if frames.is_number(entry.get("value")) else 0

    def item_from_goods(app, contract, goods):
        """雛形から1件を選んで品を作る。選ぶのは店の買値が1回の賃金の `ITEM_VALUE_TERMS` 倍までの品（無ければ最安）。

        雛形は写して渡す（世界の骨格を書き換えない）。置き場所は空けてゲームに任せ、店の値札は外す。
        鍵はゲームの採番台帳から採る（ローダの `ids.claim`）。
        """
        cap = max(1, int(contract["pay"]) * max(1, int(ITEM_VALUE_TERMS)))
        affordable = [entry for entry in goods if shop_price(entry) <= cap]
        pool = affordable or [min(goods, key=shop_price)]
        entry = pool[int(rng().random() * len(pool)) % len(pool)]
        data = copy.deepcopy(entry)
        data["grid_pos"] = None
        if isinstance(data.get("attributes"), dict):
            data["attributes"] = {name: v for name, v in data["attributes"].items() if name not in SHOP_PRICE_KEYS}
        key = ids.claim(app, "item", write=write)
        if not items.rebuild(app, app.player, {key: data}, write=write):
            write("WARN item: could not make {!r} from the goods".format(data.get("name")))
            return None
        write("item: {!r} from the goods of {} (price {}, cap {})".format(
            data.get("name"), contract.get("place"), shop_price(entry), cap))
        return data.get("name")

    def item_structure():
        """LLM の返却型（品1つ）。`Literal` は使わない（llm.py）。"""
        return llm.create_structure(ctx, "LaborGiftItem", {
            "item_name": (str, ...), "description": (str, ...), "item_type": (str, ...),
            "item_sub_type": (str, ...), "item_appearance": (str, ...), "rarity": (str, ...)}, label="labor")

    def item_value(app):
        """作る品の価値（その土地の平均難易度。店の品と同じ物差し）。引けなければ依頼の最高難易度。"""
        functions = sys.modules.get(REWARD_MODULE)
        average = getattr(functions, "get_area_average_difficulty", None)
        value = None
        if callable(average):
            try:
                value = average(ui.current_area(app), getattr(app, "world", None))
            except Exception:
                ctx.log_exc("labor: get_area_average_difficulty failed")
        if not frames.is_number(value):
            value = getattr(app, DIFFICULTY_ATTR, None)
        value = int(round(value)) if frames.is_number(value) else 1
        return max(1, min(value_limits.value_max(), value))

    def item_from_llm(app, contract, facility):
        """LLM に施設に合う品を1つ書かせ、ゲームの `generate_item_from_item_data` で主人公の持ち物に作る。"""
        maker = getattr(app, "generate_item_from_item_data", None)
        structure = item_structure()
        if not callable(maker) or structure is None:
            return None
        owner = ui.character_of(app, contract.get("owner"))
        job = getattr(owner, "job", None)
        area = ui.current_area(app)
        prompt = ITEM_PROMPT.format(
            owner=contract.get("owner_name") or OWNER_FALLBACK,
            job=job if isinstance(job, str) and job.strip() else "（不明）",
            player=ui.player_name(app, fallback="主人公"),
            place=contract.get("place") or PLACE_FALLBACK,
            kind=view.FACILITY_KIND_NAMES.get(contract.get("job"), ""),
            area=frames.short(str(getattr(area, "name", "") or ""), 60),
            description=frames.short(str(getattr(facility, "description", "") or ""), 400),
            types="、".join("{}: {}".format(name, "/".join(subs)) for name, subs in items.ITEM_TYPES.items()),
            rarities="/".join(items.RARITIES))
        screen.busy_on(app)
        try:
            raw = llm.ask(ctx, ITEM_MANAGER, [{"role": "system", "content": prompt},
                                              {"role": "user", "content": ITEM_REQUEST}],
                          timeout=ITEM_TIMEOUT, structure=structure, label="labor", write=write)
        finally:
            screen.busy_off(app, restore=False)
        fields = rules.tidy_item(raw, item_value(app), items.ITEM_TYPES, items.RARITIES)
        if fields is None:
            write("WARN item: the LLM returned no usable item ({})".format(type(raw).__name__))
            return None
        player = app.player
        before = set(items.inventory_of(player) or {})
        call = fields + (player,)
        try:
            made = items.made_item("generate_item_from_item_data", maker(*call), call, {})
        except Exception:
            ctx.log_exc("labor: generate_item_from_item_data failed")
            return None
        inventory = items.inventory_of(player)
        if not isinstance(inventory, dict):
            return None
        if not set(inventory) - before:
            if made is None:
                write("WARN item: generate_item_from_item_data made nothing for {!r}".format(fields[0]))
                return None
            inventory[ids.claim(app, "item", write=write)] = made     # ゲームが入れない作りなら台帳の鍵で入れる
        write("item: {!r} written by the LLM for {} (value {})".format(fields[0], contract.get("place"), fields[4]))
        return fields[0]

    def avoid(app, contract, risk, index):
        """危険を避ける選択肢を選んだ。成功すれば避け、失敗すれば危険のとおりの損。"""
        options = rules.RISK_EVENTS[risk]["options"]
        job = rules.ILLEGAL_JOB_BY_KEY.get(contract.get("job"))
        if not isinstance(index, int) or not 0 <= index < len(options) or job is None:
            choices.repaint(app)
            return
        option = options[index]
        lead = []
        bribe = 0
        if option["how"] == "bribe":
            cost = bribe_cost(contract)
            bribe = take(app, cost)
            if bribe < cost:
                if bribe:
                    pay_out(app, bribe)         # 足りなかった（押す間に所持金が減った）。払った分を戻して選び直させる
                write("WARN avoid: cannot pay the bribe {} (took {})".format(cost, bribe))
                choices.repaint(app)
                return
            lead.append(coins(BRIBE_PAID.format(cost=money(cost))))
        percent = rules.avoid_percent(option, job["danger"], scores(app).get(option["ability"]))
        won = percent is None or rng().random() * 100.0 < percent
        write("avoid: {} #{} {} {} -> {}".format(risk, index, option["how"],
                                                  "{}%".format(percent) if percent is not None else "sure",
                                                  "avoided" if won else "failed"))
        if not won:
            suffer(app, contract, risk, job, lead + [option["lose"]], bribe=bribe)
            return
        result = option["win"]
        pay = int(contract["pay"])
        if result.get("pay"):
            pay = max(0, int(round(pay * (100 + result["pay"]) / 100.0)))
        text = result.get("text") or ""
        mark = {"kind": risk, "avoided": True, "how": option["how"], "bribe": bribe,
                "summary": "{}（{}）{}".format(rules.RISK_EVENTS[risk]["text"], option["label"], text)}
        settle_illegal(app, contract, pay, lead + [text], tenure=result.get("tenure") or 0, mark=mark,
                       quit=bool(result.get("quit")))

    def suffer(app, contract, risk, job, lead=(), bribe=0):
        """危険のとおりの損を当てて、その期を締める（避けられなかったとき）。"""
        lead = [line for line in lead if line]
        days = int(contract["term_days"])
        pay = int(contract["pay"])
        if risk == rules.CRACKDOWN:
            law = rules.CRACKDOWN_LAW_PER_DANGER * int(job["danger"])
            change_law(app, contract, -law)
            settle_illegal(app, contract, 0, lead + [CRACKDOWN_TEXT],
                           mark={"kind": risk, "law": law, "bribe": bribe, "summary": CRACKDOWN_TEXT})
        elif risk == rules.RETALIATION:
            loss = take(app, pay * rules.RETALIATION_LOSS_TERMS)
            text = coins(RETALIATION_TEXT.format(loss=money(loss)))
            settle_illegal(app, contract, 0, lead + [text], mark={"kind": risk, "loss": loss, "summary": text})
        else:
            loss = take(app, int(contract["earned"]))
            contract["earned"] = max(0, int(contract["earned"]) - loss)
            change_law(app, contract, -rules.BETRAYAL_LAW)
            text = coins(BETRAYAL_TEXT.format(loss=money(loss))) if loss > 0 else BETRAYAL_TEXT_EMPTY
            settle_illegal(app, contract, 0, lead + [text],
                           tenure=-int(BETRAYAL_TENURE_DAYS) - days, fired=True,
                           mark={"kind": risk, "loss": loss, "summary": text})

    def event_tone(result):
        """出来事の結果が良かったか（帯の印の色）。賃金・勤続・好感度・手配度・打ち切りから決める。"""
        bad = any((result.get(name) or 0) < 0 for name in ("pay", "tenure", "affinity", "law")) \
            or bool(result.get("fired"))
        if bad:
            return view.BAD
        good = any((result.get(name) or 0) > 0 for name in ("pay", "tenure", "affinity"))
        return view.GOOD if good else view.PLAIN

    def change_affinity(app, contract, delta):
        owner_id = contract.get("owner")
        if not delta or owner_id in (None, ""):
            return
        try:
            current = npcs.affinity_of(app, owner_id)
            if not frames.is_number(current):
                return
            target = affinity_after(current, delta)
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

    def log_term(contract, paid, mark):
        """その期の結果を契約の控えに足す（帯のマスと雇い主の一言の頼みが読む）。"""
        entry = {"pay": int(paid)}
        entry.update(mark or {})
        log = contract.get("log")
        if not isinstance(log, list):
            log = contract["log"] = []
        log.append(entry)

    def settle_term(app, contract, pay, lines, tenure=0, affinity=0, law=0, fired=False, mark=None):
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
            log_term(contract, paid, mark)
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
            where = contract.get("place") if contract.get("kind") == rules.OFFICE else None
            screen.say(app, RANK_UP_TEXT.format(where=where or contract["name"], rank=rules.RANKS[rank_after][0]))
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
        if rng().random() * 100.0 < percent:
            risk = rules.pick_risk(rng(), job)
            write("risk: {} ({:.1f}%) during {}".format(risk, percent, contract["name"]))
            pend(app, contract, {"risk": risk}, rules.RISK_EVENTS[risk]["text"])
            return
        write("risk: none ({:.1f}%) during {}".format(percent, contract["name"]))
        if rng().random() * 100.0 < rules.event_percent(EVENT_PERCENT):
            with worlds.lock:
                _key, bucket = ledger(app)
                last = bucket.get("last_event")
            event = rules.pick_illegal_event(rng(), last)
            pend(app, contract, {"illegal": event["key"]}, fill(event["text"], contract), event_key=event["key"])
            return
        settle_illegal(app, contract, pay, [], mark={"kind": "safe"})

    def pend(app, contract, pending, text, event_key=None):
        """その期を締めずに、選択肢を出して待つ（危険・出来事）。"""
        with worlds.lock:
            key, bucket = ledger(app)
            contract["pending"] = pending
            if event_key is not None:
                bucket["last_event"] = event_key
            worlds.save(key)
        write("pending: {} during {}".format(pending, contract["name"]))
        screen.say(app, text)
        choices.repaint(app)
        save_soon(app, "labor event")

    def settle_illegal(app, contract, pay, lines, tenure=0, fired=False, mark=None, quit=False):
        days = int(contract["term_days"])
        paid = pay_out(app, pay)
        if paid > 0:
            lines = list(lines) + [coins(ILLEGAL_WORKED_TEXT.format(
                period=rules.days_in_words(days), pay=money(paid)))]
        with worlds.lock:
            key, bucket = ledger(app)
            contract["pending"] = None
            contract["done"] = int(contract["done"]) + 1
            contract["earned"] = int(contract["earned"]) + paid
            log_term(contract, paid, mark)
            employer = tenure_key(contract)
            if employer:
                rules.add_tenure(bucket, employer, days + int(tenure))
            worlds.save(key)
        for line in lines:
            screen.say(app, line)
        refresh_status(app)
        if quit:
            end_contract(app, contract, "quit")
        elif fired:
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
        bonus = 0
        tenure_now = None
        if why == "finished":
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
            if employer:
                tenure_now = rules.tenure_days(bucket, employer)
            bucket["contract"] = None
            worlds.save(key)
        record_life(app, contract, why)
        write("end: {} [{}] {} after {}/{} term(s), earned {}".format(
            contract["name"], contract["kind"], why, contract["done"], contract["terms"], earned))
        for line in lines:
            screen.say(app, line)
        refresh_status(app)
        prices.refresh("labor end")
        if why == "finished" and FINISH_SCENE and contract.get("owner") not in (None, ""):
            finish_scene(app, contract, bonus, tenure_now)
            return
        close(app, why)
        save_soon(app, "labor end")

    # ------------------------------------------------------------ 務め終えた場面
    def finish_scene(app, contract, bonus, tenure_now):
        """雇い主の立ち絵を出し、一言を本文に出して、「やった」だけの画面にする（押すと立ち絵を下げて閉じる）。"""
        owner_id = str(contract.get("owner"))
        rank_up = None
        if contract.get("rank_start") is not None and tenure_now is not None:
            rank_up = (int(contract["rank_start"]), rules.rank_index(tenure_now))
        model = view.strip_model(contract, coin_text, face=strip_face(app, contract), tenure=tenure_now,
                                 discount=SHOP_DISCOUNT, finished=True, bonus=bonus, rank_up=rank_up)
        # 立ち絵を出すときは帯を見出しだけにする（マスの段が顔に重なる）
        model["compact"] = show_portrait(app, owner_id)
        line = finish_line(app, contract, tenure_now)
        screen.say(app, FINISH_SAY.format(owner=contract.get("owner_name") or OWNER_FALLBACK, line=line))
        write("finish: {} by {} ({})".format(contract["name"], owner_id, line))
        choices.open_screen(app, state["owner"], FINISH_SCREEN,
                            {"kind": contract["kind"], "strip": model, "owner": owner_id})
        save_soon(app, "labor end")

    def finish_screen(app, _view, data):
        """務め終えた場面のボタン（「やった」だけ）。その場を離れていたら閉じる。"""
        if not isinstance(data, dict) or not at_desk(app, data.get("kind")):
            return None
        return [choices.Choice(FINISH_OK_LABEL, on_press=_press("finish_ok", FINISH_OK_LABEL, None))]

    def finish_ok(app, _payload):
        hide_portrait(app)
        close(app, "finish scene")

    def show_portrait(app, owner_id):
        """雇い主の立ち絵を、ゲームの会話と同じ部品に出す（`update_character_image`）。絵が無ければ出さない。"""
        if image_of(app, owner_id, "fullbody") is None:
            write("finish: {} has no portrait yet".format(owner_id))
            return False
        show = getattr(app, "update_character_image", None)
        if not callable(show):
            return False
        store["portrait"] = owner_id
        later(lambda: show(owner_id))
        return True

    def hide_portrait(app):
        """出した立ち絵を下げる（`remove_character_image`）。会話が始まっていたら会話の立ち絵なので触らない。"""
        if store.get("portrait") is None:
            return
        store["portrait"] = None
        remove = getattr(app, "remove_character_image", None)
        if callable(remove) and not getattr(app, "in_conversation", None):
            later(remove)

    def finish_line(app, contract, tenure):
        """雇い主の一言。LLM に書かせ、書けなければ決まった一言。"""
        illegal = contract["kind"] == rules.ILLEGAL
        fallback = FINISH_FALLBACK_ILLEGAL if illegal else FINISH_FALLBACK
        character = ui.character_of(app, contract.get("owner"))
        if not FINISH_LINE or character is None:
            return fallback

        def field(name, limit=200):
            value = getattr(character, name, None)
            return frames.short(value.strip(), limit) if isinstance(value, str) and value.strip() else "（不明）"

        owner = contract.get("owner_name") or OWNER_FALLBACK
        player = ui.player_name(app, fallback="主人公")
        period = rules.days_in_words(int(contract["term_days"]) * int(contract["done"]))
        situation = (FINISH_SITUATION_ILLEGAL if illegal else FINISH_SITUATION).format(
            player=player, owner=owner, place=contract.get("place") or PLACE_FALLBACK, period=period,
            name=contract.get("name") or "")
        events = "\n".join("・" + text for text in view.event_summary(contract.get("log"))) or FINISH_NO_EVENTS
        prompt = FINISH_PROMPT.format(
            owner=owner, job=field("job", 40), personality=field("personality"), speech=field("speech_style"),
            profile=field("profile", 300), situation=situation, events=events,
            tenure=rules.rank_name(tenure) if tenure is not None else "（なし）", player=player)
        screen.busy_on(app)
        try:
            raw = llm.ask(ctx, FINISH_MANAGER, [{"role": "user", "content": prompt}], timeout=FINISH_TIMEOUT,
                          label="labor", write=write)
        finally:
            screen.busy_off(app, restore=False)
        return clean_line(raw, owner) or fallback

    def clean_line(raw, owner):
        """LLM の返事から台詞の1行を取り出す（囲みの括弧・名前の頭を外し、長さを切る）。"""
        if not isinstance(raw, str):
            return None
        for text in llm.strip_fence(raw).splitlines():
            text = text.strip()
            if text.startswith(owner + ":") or text.startswith(owner + "："):
                text = text[len(owner) + 1:].strip()
            # 末尾の句点は外す（ゲームは本文を句点で改行するので、残すと閉じ括弧だけが1行に落ちる）
            text = text.strip("「」『』\"' ").rstrip("。")
            if text:
                return frames.short(text, FINISH_LINE_MAX)
        return None

    # ------------------------------------------------------------ 契約の帯
    def strip_for(app, contract):
        """いまの契約の帯の中身。"""
        employer = tenure_key(contract)
        tenure = None
        if employer:
            with worlds.lock:
                _key, bucket = ledger(app)
                tenure = rules.tenure_days(bucket, employer)
        risk = None
        if contract.get("kind") == rules.ILLEGAL:
            job = rules.ILLEGAL_JOB_BY_KEY.get(contract.get("job"))
            if job is not None:
                average = rules.ability_average(scores(app), job["abilities"])
                risk = risk_percent_for(job, average, int(contract["term_days"]), tenure or 0)
        return view.strip_model(contract, coin_text, face=strip_face(app, contract),
                                tenure=tenure, risk=risk, discount=SHOP_DISCOUNT)

    def refreshed(app):
        """組み直しの後（窓口 `choices` の `after`）。開いている画面に合わせて帯と立ち絵を出し入れする。"""
        try:
            opened = choices.opened(app)
            mine = isinstance(opened, dict) and opened.get("owner") == state["owner"]
            name = opened.get("name") if mine else None
            if name != FINISH_SCREEN and store.get("portrait") is not None:
                hide_portrait(app)
            model = None
            if PROGRESS_STRIP and name == WORK_SCREEN:
                contract = contract_of(app)
                if contract is not None and at_desk(app, contract.get("kind")):
                    model = strip_for(app, contract)
            elif PROGRESS_STRIP and name == FINISH_SCREEN:
                data = opened.get("data")
                model = data.get("strip") if isinstance(data, dict) else None
            later(lambda: place_strip(app, model))
        except Exception:
            ctx.log_exc("labor: cannot sync the contract strip")

    def place_strip(app, model):
        if model is None:
            if strip.hide():
                store["strip_model"] = None
            return
        widget = strip.current()
        # 同じ中身でも、画面に付いていなければ出し直す（ロードし直すとゲームが HUD を作り直し、
        # 前の HUD の層に付いたままの帯は見えなくなる）
        if store.get("strip_model") == model and widget is not None and widget.get_root_window() is not None:
            return
        if strip.show(app, model):
            store["strip_model"] = model

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
                template = {"finished": RECORD_ILLEGAL, "quit": RECORD_ILLEGAL_QUIT}.get(why, RECORD_ILLEGAL_FIRED)
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
        owner_id, _owner = ui.facility_owner(app, facility)     # 主が替わっていたら勤続は数えない
        with worlds.lock:
            bucket = worlds.load(loader_state.playthrough_key(app))
            percent = rules.rank_discount_percent(rules.tenure_days(bucket, employer, owner_id))
        if percent <= 0:
            return None
        return max(1, int(round(price * (100 - percent) / 100.0)))

    prices.install(ctx, write)
    prices.adjust(OWNER, discount_layer, temporary=True, write=write)

    # ------------------------------------------------------------ 押下
    ACTIONS = {"list": open_list, "back": back, "pick": pick, "start": start, "work": work,
               "option": option, "skip_event": skip_event, "quit": quit_work, "cancel": cancel,
               "finish_ok": finish_ok}

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
        ctx, screen, offer=offer, after=refreshed,
        screens={JOBS_SCREEN: jobs_screen, TERMS_SCREEN: terms_screen, WORK_SCREEN: work_screen,
                 FINISH_SCREEN: finish_screen})

    ctx.log("labor overhaul: {} day(s) x {}/{} at the office, {} day(s) x {} underground, events {}%, log={}"
            .format(TERM_DAYS, SHORT_TERMS, LONG_TERMS, ILLEGAL_TERM_DAYS, ILLEGAL_TERMS, EVENT_PERCENT,
                    ctx.out_path(LOG_BASENAME)))
