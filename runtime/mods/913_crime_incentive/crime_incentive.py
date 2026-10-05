# -*- coding: utf-8 -*-
"""犯罪に見返りを付ける。仕様と確認手順は DOC.md。

素のゲームの犯罪は、自由行動のその場で LLM が出す金品しか見返りが無く、
額も出すかどうかも LLM 任せだった。手配はリスクだけを積む（衛兵・懲役、`309_` の罰金、`316_` の追手）。
この版で足すのは2つ。

##### 盗みの稼ぎ（自由行動の盗みの額に床と天井を付ける）

- 行動の締めの要約（`master_ai_process_summarizer*`。`lawfulness_loss` を返す側）の頼みに、
  「プレイヤーが不法に得た金銭の規模」を 0〜4 で答える項目を相乗りさせる（`912_` と同じ形。LLM を別に呼ばない）
- 規模ごとの目安は、その土地の依頼1件の報酬（ゲームの `get_quest_reward(土地の平均難易度)`）× 割合
- 行動の最初の facilitator の前に所持金を控え、要約の後の所持金との差を「LLM が渡した額」とする。
  0 なら目安を渡し、幅（目安の下限%〜上限%）の外なら幅の端へ寄せる
- 発覚したか（`lawfulness_loss`）は問わない。隠しおおせた盗みがいちばん得になる

##### 怯える店（手配中の土地で値段が動く）

- その土地で手配されていると、店の買値が下がり、売値が上がる。率は手配の重さ × 1点あたりの率（上限あり）
- 値段はローダの関所（`prices.adjust`）へ一時の段として置く。保存の直前には外れる（セーブへ焼き付けない）

##### 時効（離れている土地の手配が戻る）

- 離れている土地だけ、離れていた日数 3ヵ月ごとに手配度が 10 戻る。戻るのは平常の 10 まで
- ただし全ての土地の手配の重さの合計が全域手配の線以上の間は、
  合計がその線に届いたところで止める（追手は続く。そこから先は役場の罰金）。
  線は追手を出す MOD（`316_`）がローダの窓口 `wanted` に置く。置かれていなければ全域手配は無いので止めない
- 新しい罪は、戻った後の値にそのまま足される（数え直さない）
- 日数は `elapse_days` の前後の暦の差。エリア移動の日数は出発地からも目的地からも離れていた日数として数える

##### 首の懐（追手を倒すと前金が入る）

- 追手（`316_`）に勝つと、追手の難易度での依頼1件の報酬 × 割合（既定 30%）の前金が入る。
  手配が重いほど追手は強く（難易度 20〜75）、懐も厚い
- 追手の戦闘が終わったことは `316_` がローダの窓口（`wanted.hunt_ended`）で知らせる。逃げた・負けた回は何も無い

##### 店で盗む

- 売買画面で店の品を盗む。器用で抜き取り、しくじったら判断で店主の視線に気づけば手を引ける。
  気づけなければゲーム自身の衛兵戦。回数の制限は無く、同じ店で同じ日に続けるほど、
  大きい品・高い品・レア度の高い品ほど抜き取りにくい（本人の決定。DOC.md「店で盗む」）
- 版1の途中までは施設の選択肢の「盗みを働く」だった（棚から無作為に1つ、同じ店は1日1回）。
  売買画面の右クリックへ移すので外した（セーブに焼かれた残骸を消すためラベルだけ残す）

##### 裏の仕事（裏の事務所の違法な依頼）

- 裏の事務所に「裏の仕事を探す」。種類（密輸・盗掘・破壊工作・脱獄の手引き・強盗・暗殺）は
  手配の重さの合計で解禁され、ゲーム自身の依頼の生成に種類ごとの指示を差し込んで作る
- 報酬は種類ごとの倍率（5〜10倍）、片付けると依頼の街の手配度が下がる。ギルドの掲示板からは隠す
- 事務所の公式の「裏の依頼掲示板」（`NotImplementedManager`）と同じ役割なので、公式が未実装のあいだだけ
  別のボタンとして出す（本人の判断で方針の例外）。公式が実装したら出さない。公式のボタンには触らない

##### 脱獄（服役中の毎年の画面）

- 「服役する」の横に、壁を削る（器用）・看守を手懐ける（金）・抜け道を探る（判断）と「脱獄を決行する」を足す。
  備えはその年の服役と引き換え（ゲームの服役を同じ引数で1年進める）。器用・判断の備えは年ごとに露見しうる。
  露見すれば備えは潰れ、刑期が延びる
- 決行はゲームの衛兵の戦闘。相手は備えの数だけ弱い。勝つか逃げれば牢の外（捕まった場所）へ出て、
  その土地の手配度が下がる。倒れたら死なせず、逃走と同じ終わり方で切り上げて牢へ戻し、刑期を延ばす
  （`334_` の負けの切り上げと同じ手順。GAME.md §2.10）
- 服役の流れは GAME.md §2.20「逮捕・裁判・服役」（`238_probe_prison` の実機）
"""

import os
import random
import sys
import threading
import time

from instantale_modloader import frames, ids, items, llm, prices, ui, wanted
from instantale_modloader import state as loader_state

from . import jailbreak, rules, underworld

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
LOOT_ENABLED = True           # 盗みの稼ぎを整える
LOOT_SHARE_PETTY = 5          # 規模1（小銭）の目安。依頼1件の報酬に対する %
LOOT_SHARE_MODEST = 20        # 規模2（まとまった額）
LOOT_SHARE_LARGE = 50         # 規模3（大金）
LOOT_SHARE_FORTUNE = 100      # 規模4（財産）
LOOT_FLOOR_PCT = 50           # LLM の額がこれ（目安の%）に満たなければ、ここまで足す
LOOT_CEILING_PCT = 200        # LLM の額がこれ（目安の%）を超えれば、ここまで削る
LOOT_NOTICE = True            # 足した・削ったことを本文に1行出す
INTIMIDATION_ENABLED = True   # 手配中の土地で店が怯える
INTIMIDATION_PER_POINT = 0.5  # 手配の重さ1あたりの率（%）
INTIMIDATION_CAP = 30         # 率の上限（%）
INTIMIDATION_SELL = True      # 売値も上げる
INTIMIDATION_NOTICE = True    # 店に入ったときに1行出す
STATUTE_ENABLED = True        # 離れている土地の手配が時とともに戻る
STATUTE_PERIOD_MONTHS = 3     # 戻る間隔（ゲームの月。1ヵ月30日）
STATUTE_STEP = 10             # 1回に戻る手配度
STATUTE_RESTORE_TO = 10       # ここまで戻る（素の平常値）
STATUTE_HOLD_HUNTED = True    # 全域手配の間は、合計がその線に届いたところで止める（追手は続く）
STATUTE_NOTICE = True         # 戻ったときに1行出す
BOUNTY_ENABLED = True         # 追手を倒すと懐の前金が手に入る
BOUNTY_PURSE_PCT = 30         # 前金。追手の難易度での依頼1件の報酬に対する %
THEFT_ENABLED = True          # 売買画面で店の品を右クリックすると「盗む」が出る
THEFT_BASE_PCT = 30           # 能力値 15 のときの確率（器用で抜き取る・判断で視線に気づく、の両方）
THEFT_PER_POINT = 3           # 能力値1点ごとに動く確率（%）
THEFT_MAX_PCT = 90            # 確率の上限（%）
THEFT_STREAK_STEP = 10        # 同じ店で同じ日に試した回数1回ごとに、抜き取る確率から引く（%）
THEFT_CELL_STEP = 5           # 品が1マスを超えたマス1つごとに引く（%）
THEFT_PRICE_STEP = 10         # 買価がその店の棚の中央値の倍になるごとに引く（%）
THEFT_PRICE_CAP = 30          # 値段で引く分の上限（%）
THEFT_RARITY_STEP = 5         # レア度の段（common 0 〜 mythic 5）1つごとに引く（%）
UNDERWORLD_ENABLED = True     # 裏の事務所で裏の仕事を受けられる
UNDERWORLD_REWARD_PCT = 100   # 種類ごとの報酬の倍率（暗殺10倍など）に掛ける調整（%）
UNDERWORLD_LOSS_PCT = 100     # 種類ごとの手配度の下がり幅に掛ける調整（%）
UNDERWORLD_UNLOCK_PCT = 100   # 種類ごとの解禁に要る手配の重さに掛ける調整（%）
JAILBREAK_ENABLED = True      # 服役中の毎年の画面で脱獄に備え、決行できる
JAILBREAK_PREP_MAX = 5        # 備えの上限
JAILBREAK_PREP_BASE_PCT = 50  # 能力値 15 のときに備えが実る確率（壁を削る＝器用・抜け道を探る＝判断）
JAILBREAK_PER_POINT = 3       # 能力値1点ごとに動く確率（%）
JAILBREAK_DETECT_PCT = 10     # 備えの年に企てが露見する確率（%）
JAILBREAK_DETECT_PER_PREP = 5 # 積んだ備え1つごとに露見の確率に足す（%）
JAILBREAK_BRIBE_PCT = 50      # 看守を手懐ける額。その土地の依頼1件の報酬に対する %
JAILBREAK_EASE_PCT = 15       # 備え1つごとに、決行の戦闘の相手を弱くする（%）
JAILBREAK_EXTEND_YEARS = 2    # 露見したとき・決行で倒れたときに延びる刑期（年）
JAILBREAK_LOSS = 20           # 脱獄したとき、その土地で下がる手配度

# ---- 設定にしない定数 ----------------------------------------------------
LOG_BASENAME = "crime_incentive.log"
MANAGER = "scripts.llm.llm_manager"
SEND_TARGET = MANAGER + ":send_request"
FUNCTIONS_MODULE = "scripts.functions"
SHOP_TARGET = "__main__:ShoppingStartManagerRemake.execute"
ELAPSE_TARGET = "__main__:InstantaleApp.elapse_days"
MOVE_TARGET = "__main__:AreaMoveManager.execute"
STATE_DIRNAME = "crime_incentive"
STORE_ATTR = "_instantale_crime_incentive"
#: ゲームの1ヵ月（`elapse_days(months * 30)`。GAME.md §2.17）。
DAYS_PER_MONTH = 30

#: 行動の始まり（所持金を控える）。`119_` と同じ並び。綴りはゲーム側のまま。
FACILITATOR_TARGETS = (
    "master_ai_facilitator",
    "master_ai_facilitator_from_conversation",
    "master_ai_facilitator_in_quest",
    "master_ai_faciltiator_from_conversation_in_quest",
)

#: 行動の締め（判定を相乗りさせる）。`119_` と同じ並び。
#: 適用は `119_` より後（＝外側）なので、`lawfulness_loss` は `119_` が直した後の値が来る。
SUMMARIZER_TARGETS = (
    "master_ai_process_summarizer",
    "master_ai_process_summarizer_in_conversation",
    "master_ai_process_summarizer_in_conversation_in_quest",
    "master_ai_process_summarizer_in_quest",
    "master_ai_process_summarizer_with_no_recipients",
)

#: 要約の返却型をこの項目で見分ける（`Structure{summary, memory_recipients, lawfulness_loss}`）。
LAW_FIELD = "lawfulness_loss"
SCALE_FIELD = "loot_scale"
REASON_FIELD = "loot_reason"
ADDED_FIELDS = (SCALE_FIELD, REASON_FIELD)

INSTRUCTION = """【追加の判定】
要約とは別に、この行動でプレイヤー本人が不法に手に入れた金銭の規模を判定し、loot_scale に 0〜4 の整数で答えること。
- 0: 金銭を不法に得ていない。暴行・殺人・器物損壊のように金を奪っていない犯罪、品物だけを盗んだ場合、正当な報酬や売買も 0
- 1: 小銭（財布を一つすった、少額をゆすった）
- 2: まとまった額（店のレジ、一人の有り金）
- 3: 大金（商店の金庫、裕福な屋敷、商隊の売上）
- 4: 財産（銀行や領主の宝物庫、大商会の蓄え）
発覚したかどうかは問わない。隠しおおせた盗みも数える。
NPC や第三者が奪った場合と、プレイヤーが試みて失敗した場合は 0 とすること。
loot_reason には判定の理由を1文で書くこと。
summary にはこの判定のことを書かないこと。"""

LOOT_TOTAL_TEXT = "盗みの稼ぎは合わせて{total}ゴールドになった。"
LOOT_CUT_TEXT = "奪った金のうち、手元に残ったのは{total}ゴールドだった。"
SHOP_NOTICE_TEXT = "手配書の顔に気づいた店主は、怯えたように値を改めた。（買値 -{pct}%{sell}）"
SHOP_NOTICE_SELL = "・売値 +{pct}%"
STATUTE_TEXT = "{area}では、騒ぎのほとぼりが冷めつつある。（手配度 {before} → {after}）"
BOUNTY_TEXT = "倒した追手の懐から、賞金の前金{amount}ゴールドを抜き取った。"
#: ゲームの `BattleEndManager(app, end_type)` で勝ったときの `end_type`（GAME.md §2.10）。
WON_END_TYPE = "won"

#: 押下を横取りする印（他の MOD と別のキー）。
THEFT_MARK = "mod_crime_incentive_action"
#: 外した施設の選択肢。セーブに焼かれた残骸を `prune_stale` で消すためだけに残す。
THEFT_LABEL = "盗みを働く"
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
OUR_LABELS = (THEFT_LABEL, SEARCH_LABEL, JOB_LABEL_HEAD)
#: 店だと見なす選択肢（`Facility.choices` の鍵。GAME.md §2.20 の役場の項）。
SHOP_CHOICE = "売買する"
#: 施設の選択肢だと見なす目印（出口のマネージャ。`309_` と同じ）。
FACILITY_MARK = "MovePhaseManager"
#: 見咎められたときにゲーム自身の衛兵戦を起こす（GAME.md §2.20）。
GUARD_ENEMY_TYPE = "guard"
GUARD_CHOICE_TEXT = "衛兵"
#: MOD が持つ施設の主（`331_` の店など）。自分の店から盗む話にしない。
MOD_NPC_PREFIX = "mod:"
THEFT_SUCCESS_TEXT = "店主の目を盗み、{item}を懐に滑り込ませた。"
THEFT_ABORT_TEXT = "店主の視線がこちらへ向いた。気取られる前に手を引っ込めた。"
THEFT_CAUGHT_TEXT = "「泥棒だ！」店主の叫びを聞きつけ、衛兵が駆けつけてきた。"
THEFT_NO_ROOM_TEXT = "めぼしい品はあるが、これ以上は持ちきれない。"
THEFT_FAILED_TEXT = "（品を懐へ移せなかった）"
#: 売買画面で店の品を右クリックしたときのボタン。
BUY_LABEL = "購入"
STEAL_LABEL = "盗む"
STEAL_CONFIRM_TEXT = "{item}を盗む\n成功率 {hand}%　被発見率 {caught}%"
STEAL_YES = "はい"
STEAL_NO = "いいえ"
#: 所持品のマスの単位（64px ＋ 隙間 1px。ゲームはこの固定の単位で置き、落とした座標も同じ単位で割る。GAME.md §2.13.3）。
#: 脱獄の決行の戦闘（GAME.md §2.10「逃げたときと倒れたとき」。`334_` と同じ値）。
END_MANAGER_CLS = "BattleEndManager"
ESCAPED_END_TYPE = "escaped"
PLAYER_KEY = "player"
SURVIVE_HP = 1
#: 切り上げた `BattlePhaseManager` の実体に付ける印と、その後に飛ばす手（`334_` の実機）。
JAIL_SURRENDERED_MARK = "_mod_crime_incentive_surrendered"
AFTER_SURRENDER_STEPS = ("handle_battle_situation", "reduce_status_turns_and_log")
ENEMY_DISPLAY_TARGET = "scripts.hud.new_hud:InstanTaleHUD.update_enemy_display"
SURRENDER_GUARD_SECONDS = 10

CELL_PX = 65
#: 売買の窓の表示の状態を持つ HUD の辞書（`turnoff_window_visibility` の2つ目の引数）。
TWIN_WINDOW_DATA = "visible_twin_inventory_data"
#: 借りる確認の窓を探す間隔（秒）。ゲームは落とした後のフレームで窓を出す。
PANEL_WAITS = (0.05, 0.1, 0.2, 0.4)


#: 盗みの判定と品選びの乱数。グローバルの `random` から引くとゲーム自身の乱数列がずれる（TECH.md §6.1）。
_RNG = random.Random()


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None}
        setattr(sys, STORE_ATTR, store)
    return store


# ============================================================ 部品（ゲームに触らない）
def has_field(structure, name):
    """pydantic の型が `name` の項目を持つか。"""
    for attr in ("model_fields", "__fields__"):
        fields = getattr(structure, attr, None)
        if isinstance(fields, dict):
            return name in fields
    return False


def with_instruction(message, text):
    """先頭の system の本文に `text` を書き足した写し。system が無ければ None（`912_` と同じ）。"""
    if not isinstance(message, list):
        return None
    for index, turn in enumerate(message):
        if isinstance(turn, dict) and turn.get("role") == "system" \
                and isinstance(turn.get("content"), str):
            copied = list(message)
            copied[index] = dict(turn, content=turn["content"].rstrip() + "\n\n" + text)
            return copied
    return None


def without_added(data):
    return {key: value for key, value in data.items() if key not in ADDED_FIELDS}


def restore(raw, structure):
    """足した型で返ってきた答えを元の型の形に戻す（`912_` と同じ。要約の記録とセーブに判定を混ぜない）。"""
    if isinstance(raw, dict):
        return without_added(raw)
    if isinstance(raw, str):
        import json
        data = llm.as_dict(raw)
        return json.dumps(without_added(data), ensure_ascii=False) if data is not None else raw
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        data = without_added(dump())
        validate = getattr(structure, "model_validate", None)
        return validate(data) if callable(validate) else structure(**data)
    return raw


def loot_shares():
    """規模 → 目安の割合（%）。設定が書き込まれた後に読むので関数にしてある。"""
    return {1: LOOT_SHARE_PETTY, 2: LOOT_SHARE_MODEST,
            3: LOOT_SHARE_LARGE, 4: LOOT_SHARE_FORTUNE}


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "913_crime_incentive"
    screen = ui.Screen(ctx, write, tag="crime incentive", mark=THEFT_MARK)
    lock = threading.Lock()
    #: いまの行動。最初の facilitator で開き、要約で閉じる。
    action = {"open": False, "gold": None, "claimed": 0, "calls": 0}
    #: 要約の関数から send_request へ「この要約に判定を足す」を渡す。同じスレッドで降りてくる。
    pending = threading.local()
    #: 元の型 -> 足した型。鍵の型も持って id の使い回しを避ける。
    extended_types = {}

    # -------------------------------------------------- ゲームの値を読む
    def area_difficulty(app):
        """いまの土地の平均難易度（ゲームのヘルパ）。引けなければ None。"""
        functions = sys.modules.get(FUNCTIONS_MODULE)
        average = getattr(functions, "get_area_average_difficulty", None)
        area = ui.current_area(app)
        if not callable(average) or area is None:
            return None
        value = average(area, getattr(app, "world", None))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return max(1, int(round(value)))

    def quest_reward(difficulty):
        functions = sys.modules.get(FUNCTIONS_MODULE)
        reward = getattr(functions, "get_quest_reward", None)
        if not callable(reward) or difficulty is None:
            return None
        value = reward(difficulty)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    def here_lawfulness(app):
        """いまの土地の手配度。読めなければ None。"""
        player = getattr(app, "player", None)
        area_id = ui.area_id_of(ui.current_area(app))
        return ui.lawfulness_of(ui.area_record(player, area_id)) if area_id else None

    def refresh_gold(app):
        updater = getattr(app, "update_ui", None)
        if callable(updater):
            updater()

    # -------------------------------------------------- 盗みの稼ぎ
    def open_action():
        app = ui.find_app()
        with lock:
            if not action["open"]:
                action.update(open=True, gold=ui.gold_of(app) if app is not None else None,
                              claimed=0, calls=0)

    def note_claims(result):
        claimed = rules.gold_claims(rules._get(result, "process"))
        with lock:
            if action["open"]:
                action["claimed"] += claimed
                action["calls"] += 1

    def close_action():
        with lock:
            taken = dict(action)
            action.update(open=False, gold=None, claimed=0, calls=0)
        return taken

    def wrap_facilitator(name):
        @ctx.wrap("{}:{}".format(MANAGER, name), required=False, safe=True)
        def facilitator(orig, *args, **kwargs):
            if LOOT_ENABLED:
                try:
                    open_action()
                except Exception:
                    ctx.log_exc("crime incentive: cannot open the action")
            result = orig(*args, **kwargs)
            if LOOT_ENABLED:
                try:
                    note_claims(result)
                except Exception:
                    ctx.log_exc("crime incentive: cannot read the processes")
            return result
        return facilitator

    def extended(structure):
        known = extended_types.get(id(structure))
        if known is not None and known[0] is structure:
            return known[1]
        module = llm.manager()
        factory = getattr(module, "create_model", None) if module is not None else None
        if not callable(factory):
            return None
        child = factory(getattr(structure, "__name__", "Structure"), __base__=structure,
                        **{SCALE_FIELD: (int, ...), REASON_FIELD: (str, ...)})
        extended_types[id(structure)] = (structure, child)
        return child

    def prepare(args, kwargs, structure):
        """足した形の `(args, kwargs)`。足せなければ None（元のまま送る）。"""
        message = args[1] if len(args) > 1 else kwargs.get("message")
        new_message = with_instruction(message, INSTRUCTION)
        if new_message is None:
            write("skip: the summary had no system message")
            return None
        child = extended(structure)
        if child is None:
            write("skip: cannot extend the summary structure")
            return None
        args, kwargs = list(args), dict(kwargs)
        if len(args) > 1:
            args[1] = new_message
        else:
            kwargs["message"] = new_message
        if len(args) > 2:
            args[2] = child
        else:
            kwargs["structure"] = child
        return tuple(args), kwargs

    def install_send(target):
        @ctx.wrap(target, required=False)
        def send_request(orig, *args, **kwargs):
            request = getattr(pending, "request", None)
            if request is None or request["sent"]:
                return orig(*args, **kwargs)
            structure = args[2] if len(args) > 2 else kwargs.get("structure")
            if structure is None or not has_field(structure, LAW_FIELD):
                return orig(*args, **kwargs)
            request["sent"] = True              # 要約1回に1度だけ
            try:
                prepared = prepare(args, kwargs, structure)
            except Exception:
                ctx.log_exc("crime incentive: cannot extend the summary request")
                prepared = None
            if prepared is None:
                return orig(*args, **kwargs)
            try:
                raw = orig(*prepared[0], **prepared[1])
            except Exception as exc:
                write("retry: the extended summary failed ({}: {}); sending it as is".format(
                    type(exc).__name__, exc))
                return orig(*args, **kwargs)
            try:
                request["answer"] = llm.as_dict(raw)
                return restore(raw, structure)
            except Exception:
                ctx.log_exc("crime incentive: cannot restore the summary answer")
                return raw

    def settle(name, request, taken, result):
        """要約が返った後。規模を読んで所持金を整える。"""
        answer = request["answer"]
        if not request["sent"] or not isinstance(answer, dict):
            write("skip: {} no judgment (sent={})".format(name, request["sent"]))
            return
        scale = rules.clamp_scale(answer.get(SCALE_FIELD))
        reason = str(answer.get(REASON_FIELD) or "").strip()[:80]
        loss = rules._get(result, LAW_FIELD)
        app = ui.find_app()
        now = ui.gold_of(app) if app is not None else None
        gained = now - taken["gold"] if now is not None and taken["gold"] is not None else None
        if scale is None:
            write("skip: {} unreadable scale {!r}".format(name, answer.get(SCALE_FIELD)))
            return
        if scale == 0:
            if gained or taken["claimed"] or loss:
                write("none: {} scale 0 gained={} claimed={} loss={!r} ({})".format(
                    name, gained, taken["claimed"], loss, reason))
            return
        if gained is None:
            write("skip: {} scale {} but the gold before the action is unknown "
                  "(facilitator calls {})".format(name, scale, taken["calls"]))
            return
        difficulty = area_difficulty(app)
        reward = quest_reward(difficulty)
        guide = rules.guide_amount(reward, loot_shares()[scale])
        delta, why = rules.plan_loot(max(0, gained), guide, LOOT_FLOOR_PCT, LOOT_CEILING_PCT)
        if delta < 0:
            delta = max(delta, -now)            # 所持金を負にしない
        after = ui.add_gold(app, delta) if delta else now
        if after is None:
            write("WARN {}: cannot change the gold by {}".format(name, delta))
            return
        total = max(0, gained) + delta
        write("loot: {} scale {} {} gained={} claimed={} guide={} (difficulty {} reward {}) "
              "-> {:+d}, gold {} -> {} loss={!r} ({})".format(
                  name, scale, why, gained, taken["claimed"], guide, difficulty, reward,
                  delta, now, after, loss, reason))
        if not delta:
            return

        def show():
            refresh_gold(app)
            if LOOT_NOTICE:
                text = LOOT_CUT_TEXT if delta < 0 else LOOT_TOTAL_TEXT
                screen.say(app, ui.rewrite_coins(text.format(total=ui.money(total))))
        screen.schedule(show)

    def wrap_summarizer(name):
        @ctx.wrap("{}:{}".format(MANAGER, name), required=False, safe=True)
        def summarizer(orig, *args, **kwargs):
            if not LOOT_ENABLED:
                return orig(*args, **kwargs)
            request = {"sent": False, "answer": None}
            previous = getattr(pending, "request", None)
            pending.request = request
            try:
                result = orig(*args, **kwargs)
            finally:
                pending.request = previous
            try:
                settle(name, request, close_action(), result)
            except Exception:
                ctx.log_exc("crime incentive: cannot settle the loot")
            return result
        return summarizer

    for target_name in FACILITATOR_TARGETS:
        wrap_facilitator(target_name)
    for target_name in SUMMARIZER_TARGETS:
        wrap_summarizer(target_name)
    llm.watch_aliases(ctx, [SEND_TARGET], install_send, label="crime incentive")

    # -------------------------------------------------- 怯える店
    def intimidation(item, key, price):
        """関所の段。手配中の土地なら率を掛ける。触らないなら None。"""
        if not INTIMIDATION_ENABLED:
            return None
        app = ui.find_app()
        if app is None:
            return None
        rate = rules.intimidation_rate(here_lawfulness(app), INTIMIDATION_PER_POINT,
                                       INTIMIDATION_CAP)
        return rules.intimidated_price(key, price, rate, INTIMIDATION_SELL)

    prices.install(ctx, write)
    prices.adjust(owner, intimidation, temporary=True, write=write)

    @ctx.wrap(SHOP_TARGET, required=False, safe=True)
    def shop_start(orig, self, *args, **kwargs):
        """店に入ったときに怯え方を1行知らせ、率をログに残す。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if INTIMIDATION_ENABLED and app is not None:
                lawfulness = here_lawfulness(app)
                rate = rules.intimidation_rate(lawfulness, INTIMIDATION_PER_POINT,
                                               INTIMIDATION_CAP)
                if rate > 0:
                    pct = int(round(rate * 100))
                    write("shop: lawfulness {} -> buy -{}%{}".format(
                        lawfulness, pct, " sell +{}%".format(pct) if INTIMIDATION_SELL else ""))
                    if INTIMIDATION_NOTICE:
                        text = SHOP_NOTICE_TEXT.format(
                            pct=pct, sell=SHOP_NOTICE_SELL.format(pct=pct)
                            if INTIMIDATION_SELL else "")
                        screen.schedule(lambda: screen.say(app, text))
        except Exception:
            ctx.log_exc("crime incentive: cannot read the shop's fear")
        return orig(self, *args, **kwargs)

    # -------------------------------------------------- 時効
    # 離れている土地の手配が、離れていた日数で戻る。日数はゲームが実際に進めた分
    # （`elapse_days` の前後の暦の差。他の MOD が日数を変えていれば変えた後の数）。
    # 控えは土地ごとの「まだ戻しに使っていない日数」だけで、手配度そのものはセーブの値を直に書く。
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    #: エリア移動の最中か。移動の日数は出発地からも目的地からも離れている日数として数える。
    moving = {"depth": 0}

    @ctx.wrap(MOVE_TARGET, required=False, safe=True)
    def area_move(orig, self, *args, **kwargs):
        with lock:
            moving["depth"] += 1
        try:
            return orig(self, *args, **kwargs)
        finally:
            with lock:
                moving["depth"] -= 1

    def area_label(app, area_id):
        name = getattr(ui.world_areas(app).get(area_id), "name", None)
        return name if isinstance(name, str) and name else area_id

    def cool_down(app, before, here_before):
        after = ui.game_day(app)
        if before is None or after is None or after <= before:
            return
        days = after - before
        with lock:
            in_move = moving["depth"] > 0
        here_after = ui.area_id_of(ui.current_area(app))
        present = set() if in_move else {here_before, here_after} - {""}
        history = ui.area_history_of(getattr(app, "player", None))
        if not history:
            return
        period = max(1, int(STATUTE_PERIOD_MONTHS)) * DAYS_PER_MONTH
        entries = {str(raw_id): entry for raw_id, entry in history.items()}
        values = {area_id: ui.lawfulness_of(entry) for area_id, entry in entries.items()}
        # 全域手配かどうかは居る土地も含めた合計で見る（`316_` と同じ数え方）。
        total = wanted.total_of(values)
        plans = []
        key = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(key)
            away = bucket.setdefault("away", {})
            for area_id, lawfulness in values.items():
                if lawfulness is None or area_id in present:
                    continue
                new, rest, _times = rules.cool_down(lawfulness, away.get(area_id, 0), days,
                                                    period, STATUTE_STEP, STATUTE_RESTORE_TO)
                # 止められた回の日数も使い切る（溜めておくと、罰金で線を割った途端にまとめて戻る）。
                if rest:
                    away[area_id] = rest
                else:
                    away.pop(area_id, None)
                if new != lawfulness:
                    plans.append((area_id, lawfulness, new))
            worlds.save(key)
        # 全域手配の線は追手を出す MOD が窓口に置く。置かれていなければ全域手配は無い。
        line, line_by = wanted.hunted_line(app) if STATUTE_HOLD_HUNTED else (None, None)
        held = rules.hold_hunted(plans, total, line) if line else list(plans)
        wished = {area_id: after for area_id, _before, after in plans}
        changes = []
        for area_id, old, new in held:
            if new != wished[area_id]:
                write("statute: area {} ({}) held at {} (wanted total {} >= {} by {}; "
                      "would be {})".format(area_id, area_label(app, area_id), new, total,
                                            line, line_by, wished[area_id]))
            if new != old and ui.set_lawfulness(entries[area_id], new):
                changes.append((area_id, old, new))
        for area_id, old, new in changes:
            write("statute: area {} ({}) {} -> {} after {} day(s){}".format(
                area_id, area_label(app, area_id), old, new, days, " [moving]" if in_move else ""))
        if changes and STATUTE_NOTICE:
            lines = [STATUTE_TEXT.format(area=area_label(app, area_id), before=old, after=new)
                     for area_id, old, new in changes]
            def show():
                for line in lines:
                    screen.say(app, line)
            screen.schedule(show)

    @ctx.wrap(ELAPSE_TARGET, required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        """日数が進んだ後に、離れている土地の手配を戻す（日数には触らない）。"""
        if not STATUTE_ENABLED:
            return orig(self, *args, **kwargs)
        before, here_before = None, ""
        try:
            before = ui.game_day(self)
            here_before = ui.area_id_of(ui.current_area(self))
        except Exception:
            ctx.log_exc("crime incentive: cannot read the day before")
        result = orig(self, *args, **kwargs)
        try:
            cool_down(self, before, here_before)
        except Exception:
            ctx.log_exc("crime incentive: cannot cool down the wanted areas")
        return result

    # -------------------------------------------------- 首の懐
    # どの戦闘が追手の戦闘かは追手を出す MOD（`316_`）しか知らないので、その MOD が窓口へ知らせる。
    def hunt_end(app, hunt):
        if not BOUNTY_ENABLED:
            return
        outcome, difficulty = hunt.get("outcome"), hunt.get("difficulty")
        if outcome != WON_END_TYPE:
            write("bounty: {!r} by {} (difficulty {}); no purse".format(
                outcome, hunt.get("by"), difficulty))
            return
        if isinstance(difficulty, bool) or not isinstance(difficulty, (int, float)):
            write("WARN bounty: unreadable difficulty {!r}".format(difficulty))
            return
        reward = quest_reward(max(1, int(round(difficulty))))
        amount = rules.guide_amount(reward, BOUNTY_PURSE_PCT)
        if amount <= 0:
            write("bounty: won (difficulty {} reward {}) but the purse is 0".format(
                difficulty, reward))
            return
        before = ui.gold_of(app)
        after = ui.add_gold(app, amount)
        if after is None:
            write("WARN bounty: cannot add {} gold".format(amount))
            return
        write("bounty: won by {} difficulty {} (here {} total {}) reward {} -> purse {}, "
              "gold {} -> {}".format(hunt.get("by"), difficulty, hunt.get("here"),
                                     hunt.get("total"), reward, amount, before, after))

        def show():
            refresh_gold(app)
            screen.say(app, ui.rewrite_coins(BOUNTY_TEXT.format(amount=ui.money(amount))))
        screen.schedule(show)

    wanted.on_hunt_end(owner, ctx, hunt_end)

    # -------------------------------------------------- 店で盗む
    # 売買画面で店の品を右クリックし、「購入」か「盗む」を選ぶ（本人の決定。DOC.md「店で盗む」）。判定は2段:
    #   器用で抜き取れるか → 成功ならその品が手に入る（何も起きない）
    #   しくじったら判断で店主の視線に気づけるか → 気づけば手を引く（何も起きない）、
    #                                              気づけなければ見咎められ、窓を閉じてゲーム自身の衛兵戦
    # 回数の制限は無い。代わりに、同じ店で同じ日に続けるほど・品が大きく高くレア度が高いほど
    # 抜き取りにくい（`rules.theft_penalties`）。判断の側は品で変わらない。
    # 棚の品は店主の持ち物そのもの（GAME.md §2.13.1）。装備はゲームが作り直さないので、盗めば棚から消える。
    #
    # 売買の窓の仕組み（`227_` 版4 の実機。GAME.md §2.13.1）:
    #   品を落とす → ゲームが確認の窓（品:額G / 買う・キャンセル）を出す
    #   → 押すと `InventoryItem.buy_item` → `Item.buy`（ここでお金が動く）→ `change_inventory`（品が移る）
    # 購入はこの流れにそのまま乗せる（擬似のドラッグで品を手持ちへ落とし、確認の窓はゲームが出す）。
    # 盗むは確認の窓を通さず、`Item.buy` で持ち主を切り替えて払った額を両側へ戻し、
    # `333_` と同じ手順（`clear_current_slots` → `place_new_item` → `change_inventory`）で手持ちへ移す。
    def current_facility(app):
        location = getattr(getattr(app, "player", None), "location", None)
        if isinstance(location, (str, int)):
            facility, _node = ui.find_facility(ui.current_area(app), str(location))
            return facility
        return location

    def shop_of(app):
        """いま居る店の `(施設, 主の id, 主)`。店でなければ None。"""
        facility = current_facility(app)
        choices = getattr(facility, "choices", None)
        if facility is None or not isinstance(choices, dict) or SHOP_CHOICE not in choices:
            return None
        owner_id = getattr(facility, "owner", None)
        if owner_id is None or str(owner_id).startswith(MOD_NPC_PREFIX):
            return None
        keeper = ui.character_of(app, str(owner_id))
        return (facility, str(owner_id), keeper) if keeper is not None else None

    def shop_key(app, facility):
        return "{}/{}".format(ui.area_id_of(ui.current_area(app)), getattr(facility, "id", ""))

    def attempts_today(app, key):
        """その店で今日すでに試した回数（持ちきれなかった回は数えない）。"""
        day = ui.game_day(app)
        with worlds.lock:
            bucket = worlds.load(worlds.playthrough(app))
            entry = (bucket.get("theft") or {}).get(key)
        if day is None or not isinstance(entry, dict) or entry.get("day") != day:
            return 0
        count = entry.get("count")
        return count if isinstance(count, int) and not isinstance(count, bool) else 0

    def count_attempt(app, key):
        day = ui.game_day(app)
        if day is None:
            return
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            # 前の日の控えは要らないので、今日の分だけ残す（繰り返し遊んでも増えない）。
            theft = {k: v for k, v in (bucket.get("theft") or {}).items()
                     if isinstance(v, dict) and v.get("day") == day}
            entry = theft.get(key) or {"day": day, "count": 0}
            entry["count"] = int(entry.get("count") or 0) + 1
            theft[key] = entry
            bucket["theft"] = theft
            worlds.save(playthrough)

    def is_facility_screen(buttons):
        return isinstance(buttons, (list, tuple)) and any(
            ui.spec_cls_name(entry) == FACILITY_MARK for entry in buttons)

    def back_to_shop(app, why):
        """店の選択肢に戻す（自前のボタンは外す。足し直すかは選択肢のフックが決める）。"""
        saved = [entry for entry in (getattr(app, "buttons", None) or [])
                 if not screen.mark_of(entry)]
        write("theft: back to the shop ({})".format(why))
        screen.apply_buttons(app, saved, "theft")

    def call_guards(app):
        manager = ui.cls_of("BattleStartManager")
        if manager is None:
            write("WARN theft: BattleStartManager is not available; no guards")
            back_to_shop(app, "no guards")
            return
        try:
            phase = manager(app, GUARD_ENEMY_TYPE, None)
        except Exception:
            ctx.log_exc("crime incentive: cannot build the guard battle")
            back_to_shop(app, "no guards")
            return
        if not screen.start_phase(app, phase, GUARD_CHOICE_TEXT):
            back_to_shop(app, "guards did not start")

    def key_in(inventory, item):
        """持ち物の辞書の中でこの品を指す鍵。無ければ None。"""
        for key, value in (inventory or {}).items():
            if value is item:
                return key
        return None

    def odds(app, item_key):
        """棚の品1つを盗む確率。`{"hand", "sense", "caught", "base", "penalties", "dex", "wis", "name"}`。

        店でない・棚にその品が無ければ None。確認の画面に出すのも、判定に使うのもこの値。
        """
        found = shop_of(app)
        if found is None:
            return None
        facility, _owner_id, keeper = found
        shelf = items.inventory_of(keeper) or {}
        record = items.to_dict(shelf.get(item_key)) if item_key in shelf else None
        if record is None:
            return None
        player = getattr(app, "player", None)
        dex = ui.ability_score(player, "dexterity")
        wis = ui.ability_score(player, "wisdom")
        penalties = rules.theft_penalties(
            record, [items.to_dict(item) or {} for item in shelf.values()],
            attempts_today(app, shop_key(app, facility)), THEFT_STREAK_STEP, THEFT_CELL_STEP,
            THEFT_PRICE_STEP, THEFT_PRICE_CAP, THEFT_RARITY_STEP)
        base = rules.ability_chance(dex, THEFT_BASE_PCT, THEFT_PER_POINT, THEFT_MAX_PCT)
        hand = rules.theft_chance(base, penalties)
        sense = rules.ability_chance(wis, THEFT_BASE_PCT, THEFT_PER_POINT, THEFT_MAX_PCT)
        return {"hand": hand, "sense": sense, "caught": rules.caught_chance(hand, sense),
                "base": base, "penalties": penalties, "dex": dex, "wis": wis,
                "name": record.get("name") or "品"}

    # ---- 売買の窓の部品（ゲーム自身のウィジェットを使う）
    def player_grid_of(app, widget):
        """売買の窓の手持ちの側のグリッド（品のウィジェットと同じ親に居る）。"""
        player = getattr(app, "player", None)
        for child in list(getattr(getattr(widget, "parent", None), "children", None) or []):
            if (frames.attr(child, "place_new_item", None) is not None
                    and frames.attr(child, "obtainer", None) is player):
                return child
        return None

    def slots_of(widget):
        item = getattr(widget, "item_instance", None)
        return (max(1, int(getattr(item, "width_slots", 1) or 1)),
                max(1, int(getattr(item, "height_slots", 1) or 1)))

    def free_cell(grid, width, height):
        """手持ちのグリッドの空き（左上から）。`(列, 行)`。行は下から（ゲームの `is_valid_placement` の向き）。"""
        cols = frames.attr(grid, "cols", None)
        rows = frames.attr(grid, "rows", None)
        if not isinstance(cols, int) or not isinstance(rows, int):
            return None
        for gy in range(rows - height, -1, -1):
            for gx in range(0, cols - width + 1):
                try:
                    if grid.is_valid_placement(gx, gy, width, height):
                        return gx, gy
                except Exception:
                    return None
        return None

    #: 盗みの間だけ本文を止める（`Item.buy` の購入の文）。
    muted = {"buy": False}

    def take(app, widget, keeper):
        """盗んだ品を手持ちへ移す。`(品の名前, 理由)`。

        持ち主の切り替えは購入と同じ `Item.buy` に任せ（何を書き換えるかはゲームが知っている）、
        払った額を主人公と店主の両方へ戻す。置き場所はゲームの `place_new_item` が決める。
        """
        app_player = getattr(app, "player", None)
        item = getattr(widget, "item_instance", None)
        shelf = items.inventory_of(keeper)
        grid = player_grid_of(app, widget)
        shop_grid = getattr(widget, "inventory", None)
        if item is None or grid is None or shelf is None or key_in(shelf, item) is None:
            return None, "not on the shelf"
        width, height = slots_of(widget)
        if free_cell(grid, width, height) is None:
            return None, "no room"
        name = getattr(item, "name", None) or "品"
        gold, keeper_gold = getattr(app_player, "gold", None), getattr(keeper, "gold", None)
        # `Item.buy` は本文に「〈品〉を購入した。」も出す（実機）。盗みなので、その間の本文は止める。
        muted["buy"] = True
        try:
            item.buy()
        except Exception:
            ctx.log_exc("crime incentive: Item.buy failed while stealing")
        finally:
            muted["buy"] = False
        paid = (gold - app_player.gold) if isinstance(gold, int) else None
        # 払った額を戻す（盗んだのでお金は動かない）。
        if isinstance(gold, int):
            app_player.gold = gold
        if isinstance(keeper_gold, int):
            keeper.gold = keeper_gold
        if getattr(item, "obtainer", None) is not app_player:
            # 所持金が足りずにゲームが断った、など。持ち主だけはこちらで合わせる。
            write("theft: Item.buy did not hand the item over (paid {}); setting the owner".format(paid))
            item.obtainer = app_player
        try:
            widget.clear_current_slots()
            grid.place_new_item(widget)
            widget.change_inventory(grid)
        except Exception:
            ctx.log_exc("crime incentive: cannot move the stolen item")
            if shop_grid is not None and key_in(items.inventory_of(app_player), item) is None:
                try:
                    shop_grid.place_new_item(widget)
                except Exception:
                    ctx.log_exc("crime incentive: cannot put the item back on the shelf")
            return None, "move failed"
        write("theft: took {!r} ({}x{}; Item.buy paid {} and was refunded)".format(
            name, width, height, paid))
        refresh_gold(app)
        return name, "taken"

    def steal(app, widget):
        """右クリックした棚の品を盗む。結果は "stole" / "no room" / "failed" / "backed off" / "caught"。"""
        found = shop_of(app)
        item = getattr(widget, "item_instance", None)
        shelf = items.inventory_of(found[2]) if found is not None else None
        item_key = key_in(shelf, item)
        chances = odds(app, item_key) if item_key is not None else None
        if found is None or chances is None:
            write("theft: not at a shop any more, or the item is gone")
            return "failed"
        facility, owner_id, keeper = found
        key = shop_key(app, facility)
        roll = _RNG.random() * 100
        head = "theft: {} (owner {}) {!r} dex {} -> {}% - {} = {}% roll {:.0f}".format(
            getattr(facility, "name", "?"), owner_id, chances["name"], chances["dex"],
            chances["base"], chances["penalties"], chances["hand"], roll)
        if roll < chances["hand"]:
            name, why = take(app, widget, keeper)
            if why == "no room":
                write(head + " -> no room; the attempt is not counted")
                screen.say(app, THEFT_NO_ROOM_TEXT)
                return "no room"
            count_attempt(app, key)
            if name is None:
                write(head + " -> WARN {}".format(why))
                screen.say(app, THEFT_FAILED_TEXT)
                return "failed"
            write(head + " -> stole")
            screen.say(app, THEFT_SUCCESS_TEXT.format(item=name))
            return "stole"
        count_attempt(app, key)
        roll2 = _RNG.random() * 100
        if roll2 < chances["sense"]:
            write(head + " -> fumbled; wis {} -> {}% roll {:.0f} -> backed off".format(
                chances["wis"], chances["sense"], roll2))
            screen.say(app, THEFT_ABORT_TEXT)
            return "backed off"
        write(head + " -> fumbled; wis {} -> {}% roll {:.0f} -> caught".format(
            chances["wis"], chances["sense"], roll2))
        screen.say(app, THEFT_CAUGHT_TEXT)
        return "caught"

    def close_trade_window(app):
        """売買の窓を閉じる。外側を押したときと同じ `turnoff_window_visibility(instance, 窓の表示の辞書)` → 後始末。

        2つ目の引数は窓の表示の状態を持つ辞書そのもの（売買の窓は `visible_twin_inventory_data`。
        外側を押したときにゲームが渡したものを実機で写した。2026-10-04）。
        """
        hud = ui.find_hud(app)
        close = getattr(hud, "turnoff_window_visibility", None)
        target = getattr(hud, TWIN_WINDOW_DATA, None)
        if not callable(close) or not isinstance(target, dict):
            write("WARN theft: cannot close the trade window (turnoff {} target {})".format(
                callable(close), type(target).__name__))
            return False
        try:
            close(hud, target)
            # 外側を押したときはこの後に HUD の後始末（`on_backdrop_callback` = `app.on_close_window`）が走り、
            # 取引の終わり（「取引を終了した。」・店の選択肢・`is_popup_window_opened` を下ろす）を済ませる。
            # 窓を消すだけだと旗が残り、衛兵を呼ぶ前の待ちが切れた（2026-10-04 の実機）。
            after = getattr(hud, "on_backdrop_callback", None)
            if callable(after):
                after()
        except Exception:
            ctx.log_exc("crime incentive: cannot close the trade window")
            return False
        return True

    def caught(app):
        if not close_trade_window(app):
            # 窓を閉じられないまま衛兵戦を始めると、後で窓を閉じたときにゲームが店の選択肢を戻し、
            # 戦闘のボタンが消える（2026-10-04 の実機）。閉じられなければ衛兵は呼ばない。
            write("WARN theft: the window stays open; no guards this time")
            return
        # 窓が閉じ、店の選択肢が戻って手が空いてから衛兵を呼ぶ（`is_popup_window_opened` が下りるのを待つ）。
        screen.when_idle(app, lambda: call_guards(app), tag="theft guards")

    # ---- 擬似のドラッグ（購入）
    def window_point(widget, x, y):
        parent = getattr(widget, "parent", None)
        return parent.to_window(x, y) if parent is not None else (x, y)

    def drop_into(app, widget, grid):
        """店の品を手持ちのグリッドの空きへ、マウスで運んだのと同じ入力で落とす。

        ゲームの `on_touch_down` / `on_touch_move` / `on_touch_up` がそのまま走るので、
        確認の窓・値段・支払いはどれもゲーム自身のもの。こちらは指の動きを作るだけ。
        """
        from kivy.base import EventLoop
        from kivy.core.window import Window
        from kivy.input.providers.mouse import MouseMotionEvent
        width, height = slots_of(widget)
        cell = free_cell(grid, width, height)
        if cell is None:
            return "no room"
        # 押す点は品の左下のマスの中。落とす点は空きの左下のマスの同じ所（掴んだ位置を保つ）。
        dx, dy = min(CELL_PX / 2.0, widget.width / 2.0), min(CELL_PX / 2.0, widget.height / 2.0)
        start = window_point(widget, widget.x + dx, widget.y + dy)
        end = window_point(grid, grid.x + cell[0] * CELL_PX + dx, grid.y + cell[1] * CELL_PX + dy)
        try:
            w, h = Window._get_effective_size()
        except Exception:
            w, h = Window.size
        touch = MouseMotionEvent("mouse", "mod_crime_incentive_buy",
                                 [start[0] / float(w), start[1] / float(h), "left"],
                                 is_touch=True, type_id="touch")
        EventLoop.post_dispatch_input("begin", touch)
        touch.move([end[0] / float(w), end[1] / float(h)])
        EventLoop.post_dispatch_input("update", touch)
        touch.update_time_end()
        EventLoop.post_dispatch_input("end", touch)
        pending = getattr(widget, "_has_pending_trade", None)
        shown = pending() if callable(pending) else None
        write("buy: dropped {!r} at cell {} ({:.0f},{:.0f} -> {:.0f},{:.0f}); pending trade {}".format(
            getattr(getattr(widget, "item_instance", None), "name", "?"), cell,
            start[0], start[1], end[0], end[1], shown))
        return "shown" if shown else "no confirmation"

    # ---- 右クリックのボタンと確認の画面
    menu = {"buttons": [], "unbind": None}

    def remove_menu():
        for button in menu["buttons"]:
            parent = getattr(button, "parent", None)
            if parent is not None:
                try:
                    parent.remove_widget(button)
                except Exception:
                    pass
        menu["buttons"] = []
        unbind, menu["unbind"] = menu["unbind"], None
        if callable(unbind):
            try:
                unbind()
            except Exception:
                pass

    def show_menu(app, widget, pos):
        """店の品を右クリックした位置に「購入」「盗む」を出す。ゲームは店の品に popup を出さない。"""
        if not THEFT_ENABLED:
            return
        found = shop_of(app)
        item = getattr(widget, "item_instance", None)
        if found is None or item is None or getattr(item, "obtainer", None) is not found[2]:
            return
        if player_grid_of(app, widget) is None:
            return              # 店の品でも売買の窓の外（グリッドが並んでいない）
        from kivy.core.window import Window
        remove_menu()
        template = None
        for child in list(getattr(getattr(widget, "popup_menu", None), "children", None) or []):
            if type(child).__name__ == "Button":
                template = child
                break
        try:
            x, y = float(pos[0]), float(pos[1])
        except Exception:
            x, y = float(widget.x), float(widget.top)
        for index, (label, action) in enumerate(((BUY_LABEL, "buy"), (STEAL_LABEL, "steal"))):
            button = ui.popup_button(label, template)
            button.pos = (x, y - button.height * (index + 1))
            ui.clamp_into_window(button)
            button.bind(on_release=lambda _b, action=action: menu_pressed(app, widget, action))
            Window.add_widget(button)
            menu["buttons"].append(button)

        def outside(_window, touch):
            if not any(button.collide_point(*touch.pos) for button in menu["buttons"]):
                screen.schedule(remove_menu)
            return False

        Window.bind(on_touch_down=outside)
        menu["unbind"] = lambda: Window.unbind(on_touch_down=outside)
        write("menu: {!r} at ({:.0f},{:.0f})".format(getattr(item, "name", "?"), x, y))

    def menu_pressed(app, widget, action):
        """押されたボタンの処理は次のフレームで（押下の配信の最中に別の入力を流さない）。"""
        remove_menu()
        screen.schedule(lambda: buy(app, widget) if action == "buy" else confirm_steal(app, widget))

    def buy(app, widget):
        grid = player_grid_of(app, widget)
        result = drop_into(app, widget, grid) if grid is not None else "no grid"
        if result == "no room":
            screen.say(app, THEFT_NO_ROOM_TEXT)
        elif result != "shown":
            write("WARN buy: the game did not open its confirmation ({})".format(result))

    def confirm_steal(app, widget):
        """盗む前の確認。ゲーム自身の売買の確認の窓を借りて、成功率と被発見率を出す。

        「購入」と同じ擬似のドラッグで品を手持ちへ落とし、ゲームに確認の窓（品:額G／買う・キャンセル）を出させてから、
        文言を「〈品〉を盗む／成功率・被発見率」と「はい」「いいえ」に差し替える。大きさ・位置・見た目はゲームのまま。
        - はい: ゲームの「買う」を止め（支払いを起こさない）、ゲームの「キャンセル」で品を棚へ戻してから盗む
        - いいえ: ゲームの「キャンセル」をそのまま通す
        ゲームの `ConfirmationWindow` / `ConfirmationModalView` を自分で作る形は外した。前者は親に足しても
        並ばず見えなかった（ボタンが 40×24 のまま。2026-10-04 の実機）、後者は大きさが売買の確認と違い後ろも暗くなった。
        """
        found = shop_of(app)
        item = getattr(widget, "item_instance", None)
        item_key = key_in(items.inventory_of(found[2]) if found else None, item)
        chances = odds(app, item_key) if item_key is not None else None
        if chances is None:
            write("theft: cannot read the odds of {!r}".format(getattr(item, "name", "?")))
            return
        grid = player_grid_of(app, widget)
        # 現物を握っておく（`id` だけだと、消えた物の番地を新しい窓が使い回して見落とす）。
        before = {id(node): node for node in screen_nodes()}
        result = drop_into(app, widget, grid) if grid is not None else "no grid"
        if result == "no room":
            screen.say(app, THEFT_NO_ROOM_TEXT)
            return
        if result != "shown":
            write("WARN theft: the game did not open its confirmation ({})".format(result))
            return
        # 確認の窓は落とした呼び出しの中では出ていない（同じフレームで探すと見つからなかった。2026-10-04 の実機）。
        # 次のフレームから少しずつ間を空けて探す。
        waits = list(PANEL_WAITS)

        def look():
            panel = trade_panel(before)
            if panel is not None:
                dress(app, widget, chances, panel)
                return
            if waits:
                screen.schedule(look, waits.pop(0))
                return
            fresh = ["{}:{}".format(type(node).__name__, frames.short(getattr(node, "text", ""), 20))
                     for node in screen_nodes() if before.get(id(node)) is not node][:30]
            write("WARN theft: the game's confirmation did not show up; cancelling it (new: {})".format(fresh))
            cancel = getattr(widget, "cancel_trade_confirmation", None)
            if callable(cancel):
                cancel()
        screen.schedule(look)

    def dress(app, widget, chances, panel):
        """借りた確認の窓の文言を差し替え、はい／いいえに処理を付ける。"""
        title, yes_button, no_button = panel
        title.text = STEAL_CONFIRM_TEXT.format(item=chances["name"], hand=chances["hand"],
                                               caught=int(round(chances["caught"])))
        yes_button.text, no_button.text = STEAL_YES, STEAL_NO
        write("theft: asking {!r} hand {}% (base {} - {}) sense {}% caught {:.0f}%".format(
            chances["name"], chances["hand"], chances["base"], chances["penalties"],
            chances["sense"], chances["caught"]))
        answered = {"done": False}

        def on_yes(*_args):
            if answered["done"]:
                return None
            answered["done"] = True
            write("theft: answered {!r}".format(STEAL_YES))
            # ゲームの「キャンセル」で品を棚へ戻してから（次のフレーム）盗む。
            screen.schedule(lambda: (no_button.dispatch("on_press"), screen.schedule(go)))
            return True                     # ゲームの「買う」（支払い）へは通さない

        def on_no(*_args):
            if not answered["done"]:
                answered["done"] = True
                write("theft: answered {!r}".format(STEAL_NO))
            return None                     # ゲームの「キャンセル」はそのまま

        def go():
            outcome = steal(app, widget)
            if outcome == "caught":
                caught(app)

        # Kivy は後から付けた観測者を先に呼び、True を返すとそこで止まる。
        yes_button.fbind("on_press", on_yes)
        no_button.fbind("on_press", on_no)

    def screen_nodes():
        try:
            from kivy.core.window import Window
        except Exception:
            return []
        return list(ui.walk_widgets(Window))

    def trade_panel(before):
        """ゲームが出した売買の確認の窓。`(題の Label, 買うの Button, キャンセルの Button)`。

        落とす前に無かったウィジェットのうち、中（どの段でも）に `Button` を2つと `Label` を持ついちばん外側のもの。
        実機（2026-10-04）では `ConfirmationWindow` の下に `FloatLayout` と `Label`「品名:額G」、
        `FloatLayout` の下に `Button`「買う」「キャンセル」が新しく出た。どこに置かれるかは測っていないので画面全体から探す。
        """
        for node in screen_nodes():
            if before.get(id(node)) is node:
                continue
            inner = list(ui.walk_widgets(node))
            buttons = [child for child in inner if type(child).__name__ == "Button"]
            labels = [child for child in inner if type(child).__name__ == "Label"]
            if len(buttons) >= 2 and labels:
                ordered = sorted(buttons, key=lambda child: child.x)   # 左が「買う」、右が「キャンセル」
                write("theft: borrowed the game's confirmation ({} under {}; {!r} / {!r})".format(
                    type(node).__name__, type(getattr(node, "parent", None)).__name__,
                    ordered[0].text, ordered[-1].text))
                return labels[0], ordered[0], ordered[-1]
        return None

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.show_popup_menu", required=False, safe=True)
    def show_popup_menu(orig, self, pos, *args, **kwargs):
        """品の右クリック。ゲームの popup はそのまま。店の品なら自前のボタンを足す（`402_` と同じ口）。"""
        result = orig(self, pos, *args, **kwargs)
        try:
            app = ui.find_app()
            if app is not None:
                screen.schedule(lambda: show_menu(app, self, pos))
        except Exception:
            ctx.log_exc("crime incentive: cannot schedule the shop menu")
        return result

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
        if not UNDERWORLD_ENABLED or office_of(app) is None:
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
            if not underworld.unlocked(weight, UNDERWORLD_UNLOCK_PCT):
                return
            entry = screen.button(SEARCH_LABEL, mark="search")
        if entry is None:
            return
        at = next((index for index, item in enumerate(buttons)
                   if ui.spec_cls_name(item) == FACILITY_MARK), len(buttons))
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
        kinds = underworld.unlocked(wanted.total_weight(player), UNDERWORLD_UNLOCK_PCT)
        if not kinds:
            back_to_shop(app, "nothing unlocked")
            return
        kind = _RNG.choice(kinds)
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
        mult = underworld.multiplier(kind, UNDERWORLD_REWARD_PCT)
        loss = underworld.scaled(kind["loss"], UNDERWORLD_LOSS_PCT)
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

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        """帰還の窓の間だけ、報酬の文の額をこちらが払う額に書き換える。盗みで借りた `Item.buy` の文は出さない。"""
        if muted["buy"]:
            write("theft: muted the purchase line {!r}".format(frames.short(str(context), 60)))
            return None
        pending = reward["pending"]
        if pending is not None and pending.get("want") is None:
            base = underworld.reward_amount(context)
            if base:
                want = int(round(base * pending["mult"]))
                rewritten = underworld.replace_amount(context, base, want)
                if rewritten is not None:
                    pending.update(base=base, want=want)
                    context = rewritten
        return orig(self, context, *args, **kwargs)

    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        """裏の仕事を片付けた。報酬に倍率を掛け、依頼の街の手配度を下げ、控えを捨てる。

        どの依頼が終わるのかは `orig` の前に読む（終わった後は片付いている。`318_` と同じ）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        quest_id = ui.current_quest_id(app) if app is not None else None
        job = None
        if UNDERWORLD_ENABLED and quest_id is not None:
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

    # -------------------------------------------------- 脱獄
    # 服役中の毎年の画面（ゲームの「服役する」＝ `ImprisonmentPhaseManager`）に備えと決行を足す
    # （本人の決定。DOC.md「脱獄」）。服役の流れは GAME.md §2.20「逮捕・裁判・服役」。
    #   備え: 判定してから、ゲームの服役を「服役する」と同じ引数で1年進める。
    #         露見したら備えを潰し、残り年数と刑期を延ばして進める。
    #   決行: ゲームの衛兵の戦闘（難易度は備えの数だけ下げる。316_ と同じ2か所で差し替える）。
    #         勝つか逃げれば、その場（捕まった場所）の画面へ戻る＝牢の外。
    #         倒れたら死なせず、逃走と同じ終わり方で切り上げ（334_ と同じ手順）、「服役する」を延ばして置き直す。
    # 備えの数は世界×主人公の控え（`jail`）。刑期の残りはゲームのボタンが持つので控えない。
    jail = {"battle": None, "inside": None, "removed_player": None, "surrendered_at": None}

    def serve_entry(buttons):
        """選択肢の中のゲームの「服役する」。無ければ None。"""
        for entry in buttons or []:
            if ui.spec_cls_name(entry) == jailbreak.SERVE_SPEC:
                return entry
        return None

    def jail_prep(app):
        with worlds.lock:
            bucket = worlds.load(worlds.playthrough(app))
            value = (bucket.get("jail") or {}).get("prep")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def set_jail_prep(app, value):
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            if value:
                bucket["jail"] = {"prep": int(value)}
            else:
                bucket.pop("jail", None)     # 服役していない間は何も残さない
            worlds.save(playthrough)

    def jail_odds(app):
        """`(備え, 露見の率, {備えの鍵: 実る率}, 看守を手懐ける額)`。画面に出すのも判定もこの値。"""
        player = getattr(app, "player", None)
        prep = jail_prep(app)
        detect = jailbreak.detect_chance(prep, JAILBREAK_DETECT_PCT, JAILBREAK_DETECT_PER_PREP)
        chances = {prep_def["key"]: jailbreak.prep_chance(
            ui.ability_score(player, prep_def["ability"]), JAILBREAK_PREP_BASE_PCT,
            JAILBREAK_PER_POINT) for prep_def in jailbreak.PREPS if prep_def["ability"]}
        cost = jailbreak.bribe_cost(quest_reward(area_difficulty(app)), JAILBREAK_BRIBE_PCT)
        return prep, detect, chances, cost

    def insert_jail_buttons(app, buttons):
        """「服役する」の後ろに備えと決行を足す。備えが上限なら決行だけ。"""
        serve = serve_entry(buttons)
        if not JAILBREAK_ENABLED or serve is None \
                or jailbreak.serve_args(ui.spec_args(serve)) is None:
            return
        prep, detect, chances, cost = jail_odds(app)
        texts = []
        if prep < JAILBREAK_PREP_MAX:
            for prep_def in jailbreak.PREPS:
                if prep_def["ability"] is None:
                    gold = ui.gold_of(app)
                    if cost <= 0 or not isinstance(gold, int) or gold < cost:
                        continue        # 払えない回は出さない
                    texts.append((prep_def["key"], ui.rewrite_coins(
                        prep_def["label"].format(cost=ui.money(cost)))))
                else:
                    texts.append((prep_def["key"], prep_def["label"].format(
                        chance=chances[prep_def["key"]], detect=detect)))
        texts.append(("break", jailbreak.BREAK_LABEL.format(prep=prep, max=JAILBREAK_PREP_MAX)))
        entries = [screen.button(text, mark="jail:" + key) for key, text in texts]
        at = buttons.index(serve) + 1
        buttons[at:at] = [entry for entry in entries if entry is not None]

    def serve_year(app, args):
        """ゲームの服役を1年進める（「服役する」を押したのと同じマネージャと引数）。"""
        cls = ui.cls_of(jailbreak.SERVE_SPEC)
        if cls is None:
            write("WARN jailbreak: {} is not available".format(jailbreak.SERVE_SPEC))
            return False
        try:
            phase = cls(app, *args)
        except Exception:
            ctx.log_exc("crime incentive: cannot build the prison year")
            return False
        return screen.start_phase(app, phase, jailbreak.SERVE_TEXT)

    def prepare_year(app, key, args):
        """備えの1年。判定して本文に1行出し、ゲームの服役を1年進める。"""
        prep_def = jailbreak.PREP_BY_KEY[key]
        prep, detect, chances, cost = jail_odds(app)
        next_args = list(args)
        if prep_def["ability"] is None:
            gold = ui.gold_of(app)
            if cost <= 0 or not isinstance(gold, int) or gold < cost \
                    or ui.add_gold(app, -cost) is None:
                write("jailbreak: cannot pay {} for the guard (gold {})".format(cost, gold))
                return False
            new, result = jailbreak.roll_prep(_RNG, prep, JAILBREAK_PREP_MAX, None, 0)
            line = prep_def["ok"].format(cost=ui.money(cost))
        else:
            chance = chances[key]
            new, result = jailbreak.roll_prep(_RNG, prep, JAILBREAK_PREP_MAX, chance, detect)
            if result == "detected":
                next_args = jailbreak.extended(args, JAILBREAK_EXTEND_YEARS)
                line = jailbreak.DETECTED_TEXT.format(years=JAILBREAK_EXTEND_YEARS)
            else:
                line = prep_def[result]
        if result == "ok":
            line += jailbreak.PREP_TEXT.format(prep=new, max=JAILBREAK_PREP_MAX)
        set_jail_prep(app, new)
        write("jailbreak: {} -> {} (prep {} -> {}, chance {} detect {} cost {}); serve {}".format(
            key, result, prep, new, chances.get(key), detect,
            cost if prep_def["ability"] is None else "-", next_args[:1] + next_args[3:]))
        refresh_gold(app)
        screen.say(app, ui.rewrite_coins(line))
        return serve_year(app, next_args)

    def break_out(app, args):
        """決行。ゲームの衛兵の戦闘を、備えの数だけ弱くして起こす。"""
        manager = ui.cls_of("BattleStartManager")
        if manager is None:
            write("WARN jailbreak: BattleStartManager is not available")
            return False
        prep = jail_prep(app)
        base = area_difficulty(app)
        difficulty = jailbreak.eased_difficulty(base, prep, JAILBREAK_EASE_PCT)
        try:
            phase = manager(app, GUARD_ENEMY_TYPE, None)
        except Exception:
            ctx.log_exc("crime incentive: cannot build the jailbreak battle")
            return False
        jail["battle"] = {"phase": phase, "args": list(args), "prep": prep, "started": False,
                          "difficulty": difficulty, "recapture": False,
                          "area": ui.area_id_of(ui.current_area(app))}
        jail["removed_player"] = None
        screen.say(app, jailbreak.BREAK_TEXT)
        if not screen.start_phase(app, phase, GUARD_CHOICE_TEXT):
            jail["battle"] = None
            return False
        write("jailbreak: break out with prep {} (difficulty {} -> {}); sentence {}".format(
            prep, base, difficulty, args[:1] + args[3:]))
        return True

    def press_jail(app, key):
        serve = serve_entry(getattr(app, "buttons", None))
        args = jailbreak.serve_args(ui.spec_args(serve)) if serve is not None else None
        if args is None:
            write("WARN jailbreak: no serve button to follow ({})".format(key))
            return False
        if key == "break":
            return break_out(app, args)
        if key in jailbreak.PREP_BY_KEY:
            return prepare_year(app, key, args)
        return False

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def jail_start_battle(orig, self, *args, **kwargs):
        """決行の戦闘の敵を組む間だけ難易度を差し替える。別の戦闘が始まったら控えを捨てる。"""
        battle = jail["battle"]
        if battle is None:
            return orig(self, *args, **kwargs)
        if self is not battle["phase"]:
            if battle["started"]:
                write("jailbreak: another battle started; dropping the jailbreak battle")
                jail["battle"] = None
            return orig(self, *args, **kwargs)
        battle["started"] = True
        jail["inside"] = battle["difficulty"]
        try:
            return orig(self, *args, **kwargs)
        finally:
            jail["inside"] = None

    @ctx.wrap("scripts.llm.llm_manager:guard_npc_generator", required=False, safe=True)
    def jail_guard_generator(orig, area=None, world=None, npc_difficulty_level=None,
                             *args, **kwargs):
        """決行の相手の姿と説明（難易度は文章の強さに効く。`316_` と同じ口）。"""
        difficulty = jail["inside"]
        if difficulty is not None and jailbreak.is_number(npc_difficulty_level):
            write("jailbreak: guard difficulty {} -> {} (description)".format(
                npc_difficulty_level, difficulty))
            npc_difficulty_level = difficulty
        return orig(area, world, npc_difficulty_level, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.generate_enemy_instance_from_quest_dict",
              required=False, safe=True)
    def jail_enemy_instance(orig, self, enemy_dict=None, *args, **kwargs):
        """決行の相手の実体（難易度からレベルと能力値が決まる。`316_` と同じ口）。"""
        difficulty = jail["inside"]
        if difficulty is not None:
            numbers = [index for index, value in enumerate(args) if jailbreak.is_number(value)]
            index = numbers[0] if len(numbers) == 1 else (4 if 4 in numbers else None)
            if index is None:
                write("jailbreak: cannot find the difficulty among {} argument(s)".format(len(args)))
            else:
                args = args[:index] + (difficulty,) + args[index + 1:]
        return orig(self, enemy_dict, *args, **kwargs)

    def in_jail_battle():
        battle = jail["battle"]
        return battle is not None and battle["started"]

    @ctx.wrap("__main__:InstantaleApp.remove_party_member", required=False, safe=True)
    def jail_remove_party_member(orig, self, member_id=None, *args, **kwargs):
        """決行の戦闘で倒れた主人公を控える（切り上げで一覧へ戻すため。`334_` と同じ）。"""
        try:
            if in_jail_battle() and member_id == PLAYER_KEY:
                party = getattr(self, "party", None)
                if isinstance(party, dict) and member_id in party:
                    jail["removed_player"] = party[member_id]
        except Exception:
            ctx.log_exc("crime incentive: cannot keep the player leaving the party")
        return orig(self, member_id, *args, **kwargs)

    def prepare_escape(app):
        """ゲーム自身の逃走と同じ状態にする（敵の一覧を空に、外された主人公を預かりへ。`334_`）。"""
        enemies = getattr(app, "current_enemy_dict", None)
        if isinstance(enemies, dict) and enemies:
            enemies.clear()
        party = getattr(app, "party", None)
        if isinstance(party, dict) and PLAYER_KEY in party:
            return
        removed = jail["removed_player"]
        escaped = getattr(app, "escaped_member_in_battle", None)
        if removed is not None and isinstance(escaped, dict):
            escaped[PLAYER_KEY] = removed
        else:
            write("WARN jailbreak: the player left the party and cannot be handed back")

    def ensure_player_back(app):
        party = getattr(app, "party", None)
        removed, jail["removed_player"] = jail["removed_player"], None
        if not isinstance(party, dict) or PLAYER_KEY in party or removed is None:
            return
        party[PLAYER_KEY] = removed
        escaped = getattr(app, "escaped_member_in_battle", None)
        if isinstance(escaped, dict):
            escaped.pop(PLAYER_KEY, None)
        write("WARN jailbreak: the game did not bring the player back; put back by hand")

    def recapture(app):
        """倒れた決行を、逃走と同じ終わり方で切り上げる。起こせたら True。"""
        cls = ui.cls_of(END_MANAGER_CLS)
        if cls is None:
            return False
        try:
            manager = cls(app, ESCAPED_END_TYPE)
        except Exception:
            ctx.log_exc("crime incentive: cannot build the battle end manager")
            return False
        write("jailbreak: fell in the break out; ending the battle as an escape")
        jail["surrendered_at"] = time.monotonic()
        jail["battle"]["recapture"] = True
        try:
            prepare_escape(app)
        except Exception:
            ctx.log_exc("crime incentive: cannot prepare the escape")
        try:
            manager.execute("")
        except Exception:
            ctx.log_exc("crime incentive: the escape ending failed")
            return False
        try:
            ensure_player_back(app)
        except Exception:
            ctx.log_exc("crime incentive: cannot put the player back")
        return True

    @ctx.wrap("__main__:BattlePhaseManager.check_battle_end", required=False, safe=True)
    def jail_check_battle_end(orig, self, *args, **kwargs):
        """決行で倒れたら死なせない。ゲームオーバーはこの判定の中で作られる（`334_` の実機）。"""
        if getattr(self, JAIL_SURRENDERED_MARK, False):
            return True
        try:
            battle = jail["battle"]
            if in_jail_battle() and not battle["recapture"]:
                app = getattr(self, "app", None) or ui.find_app()
                player = getattr(app, "player", None)
                hp = getattr(player, "current_hp", None)
                if jailbreak.is_number(hp) and hp <= 0:
                    player.current_hp = SURVIVE_HP
                    if recapture(app):
                        setattr(self, JAIL_SURRENDERED_MARK, True)
                        return True
                    write("WARN jailbreak: hp {} -> {} but the battle goes on".format(
                        hp, SURVIVE_HP))
        except Exception:
            ctx.log_exc("crime incentive: cannot end the break out as a loss")
        return orig(self, *args, **kwargs)

    def install_after_surrender(name):
        @ctx.wrap("__main__:BattlePhaseManager.{}".format(name), required=False)
        def after_surrender(orig, self, *args, **kwargs):
            """切り上げた戦闘の残りの手を飛ばす（消えた敵を引いて落ちる。`334_` の実機）。"""
            if getattr(self, JAIL_SURRENDERED_MARK, False):
                return None
            return orig(self, *args, **kwargs)

    for _step in AFTER_SURRENDER_STEPS:
        install_after_surrender(_step)

    @ctx.wrap(ENEMY_DISPLAY_TARGET, required=False)
    def jail_enemy_display(orig, *args, **kwargs):
        """切り上げた直後の敵の欄の更新の 0 除算だけを握る（`334_` の実機）。"""
        try:
            return orig(*args, **kwargs)
        except ZeroDivisionError:
            since = jail["surrendered_at"]
            if since is None or time.monotonic() - since > SURRENDER_GUARD_SECONDS:
                raise
            write("jailbreak: skipped an enemy panel update after the battle ended")
            return None

    @ctx.wrap("__main__:BattleEndManager.end_phase", required=False, safe=True)
    def jail_battle_end(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        battle = jail["battle"]
        if battle is None or not battle["started"]:
            return result
        jail["battle"] = None
        app = getattr(self, "app", None) or ui.find_app()
        try:
            if battle["recapture"]:
                back_to_cell(app, battle)
            else:
                set_free(app, battle, getattr(self, "end_type", None))
        except Exception:
            ctx.log_exc("crime incentive: cannot settle the break out")
        return result

    def set_free(app, battle, end_type):
        """勝つか逃げた。牢の外（ゲームが戻した場所の画面）。その土地の手配度を下げる。"""
        set_jail_prep(app, 0)
        write("jailbreak: escaped ({!r}) from area {}".format(end_type, battle["area"]))

        def settle():
            lines = [jailbreak.ESCAPED_TEXT]
            entry = ui.area_record(getattr(app, "player", None), battle["area"])
            before = ui.lawfulness_of(entry)
            loss = max(0, int(JAILBREAK_LOSS))
            if before is not None and loss and ui.set_lawfulness(entry, before - loss):
                town = getattr(ui.world_areas(app).get(battle["area"]), "name", None) or "この街"
                write("jailbreak: lawfulness of {} {} -> {}".format(
                    battle["area"], before, before - loss))
                lines.append(jailbreak.ESCAPED_LAW_TEXT.format(town=town, before=before,
                                                               after=before - loss))
            for line in lines:
                screen.say(app, line)
        screen.when_idle(app, settle, proceed_on_timeout=True, tag="jailbreak free")

    def back_to_cell(app, battle):
        """倒れて取り押さえられた。「服役する」を延ばして置き直す（備えは潰れる）。"""
        set_jail_prep(app, 0)
        args = jailbreak.extended(battle["args"], JAILBREAK_EXTEND_YEARS)
        write("jailbreak: recaptured; sentence {} -> {}".format(
            battle["args"][:1] + battle["args"][3:], args[:1] + args[3:]))
        spec = screen.make_spec(jailbreak.SERVE_SPEC, args)

        def settle():
            screen.say(app, jailbreak.RECAPTURED_TEXT.format(years=JAILBREAK_EXTEND_YEARS))
            if spec is not None:
                screen.apply_buttons(app, [{"text": jailbreak.SERVE_TEXT, "spec": spec}],
                                     "jailbreak recaptured")
        screen.when_idle(app, settle, proceed_on_timeout=True, tag="jailbreak recaptured")

    @ctx.wrap("__main__:ImprisonmentStartManager.execute", required=False, safe=True)
    def imprisonment_start(orig, self, *args, **kwargs):
        """新しい刑期。前の服役の備えは持ち越さない。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and jail_prep(app):
                set_jail_prep(app, 0)
        except Exception:
            ctx.log_exc("crime incentive: cannot reset the jailbreak preparation")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ImprisonmentEndManager.execute", required=False, safe=True)
    def imprisonment_end(orig, self, *args, **kwargs):
        """刑期を務め上げた。備えは捨てる。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and jail_prep(app):
                set_jail_prep(app, 0)
        except Exception:
            ctx.log_exc("crime incentive: cannot drop the jailbreak preparation")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list) and buttons:
                if is_facility_screen(buttons):
                    screen.prune_stale(buttons, OUR_LABELS)
                    if not any(screen.mark_of(entry) for entry in buttons):
                        insert_office_buttons(self, buttons)
                elif serve_entry(buttons) is not None:
                    screen.prune_stale(buttons, jailbreak.LABEL_HEADS)
                    if not any(screen.mark_of(entry) for entry in buttons):
                        insert_jail_buttons(self, buttons)
                else:
                    hide_from_board(self, buttons)
        except Exception:
            ctx.log_exc("crime incentive: cannot add the choices")
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        entry = ui.pressed_entry(self, button_index)
        action = screen.mark_of(entry)
        if action == "search":
            write("pressed {!r}".format(SEARCH_LABEL))
            screen.start_phase(self, SearchPhase(self), SEARCH_LABEL,
                               fallback=lambda: search_job(self))
            return None
        if isinstance(action, str) and action.startswith("job:"):
            quest_id = action[len("job:"):]
            write("pressed the job {}".format(quest_id))
            if not open_acceptance(self, quest_id):
                back_to_shop(self, "acceptance failed")
            return None
        if isinstance(action, str) and action.startswith("jail:"):
            key = action[len("jail:"):]
            write("pressed the jailbreak {!r}".format(key))
            if not press_jail(self, key):
                screen.apply_buttons(self, None, "jailbreak failed")
            return None
        return orig(self, button_index, *args, **kwargs)

    ctx.log("crime incentive: installed (loot {} shares {} floor {}% ceiling {}%; "
            "intimidation {} {}%/pt cap {}% sell {}; statute {} +{} per {} month(s) up to {}, "
            "hold {} at the line from {})".format(
                "on" if LOOT_ENABLED else "off", loot_shares(), LOOT_FLOOR_PCT,
                LOOT_CEILING_PCT, "on" if INTIMIDATION_ENABLED else "off",
                INTIMIDATION_PER_POINT, INTIMIDATION_CAP,
                "on" if INTIMIDATION_SELL else "off", "on" if STATUTE_ENABLED else "off",
                STATUTE_STEP, STATUTE_PERIOD_MONTHS, STATUTE_RESTORE_TO,
                "on" if STATUTE_HOLD_HUNTED else "off", wanted.owners() or "nobody"))
