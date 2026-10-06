# -*- coding: utf-8 -*-
"""バランス調整: 戦闘を複数手の駆け引きにする。

素の戦闘は「先に殴ったほうが一手で終わらせる」になっている。
原因は実測で3つに割れた（GAME.md §2.10.2 / VERIFICATION_LOG.md §2.68）:

  1. 最終ダメージ ＝ 素点 − 防御 の**引き算**。
     素点（2×√(能力側×武器)）と防御と HP の帯が食い違うと、
     一撃で終わるか無傷かの二択になる。乱数もゼロ
  2. 審判 LLM が付けた状態異常の中身（`intensity` / `effects_per_turn`）を
     変換（`convert_llm_output_to_instruction_dict`）が**捨てている**。
     `AttributeEffect`（能力のバフ・デバフ）は影も形も残らない
  3. 自由入力の防御的な手は narration になるだけで、数には落ちない

この MOD は3つとも数の側から直す。審判 LLM の判断（power の列挙・状態異常の
発案）はそのまま使い、**捨てられているものを拾って効かせる**。
新しい判定は発明しない。

## 1. ダメージの作り直し（引き算 → 割り算）

`resolve_*` が呼ぶ `get_instant_damage(素点, 防御)` を包み、
1手の文脈（誰が誰へ・power・倍率）が揃っているときだけ結果を置き換える。
素点と防御はゲームが計算した値をそのまま使い、効き方だけを変える（版18）:

    1発 = Σ multiplier × r × r ÷ (r + 防御)   r はその裁きの素点
         × レベル差の倍率                     格上の側の与ダメだけ伸びる
         × 防御の構え・状態異常の倍率
         × 敵の一撃の強さ（敵の手だけ）
    下限は受け側 max_hp の 1%。上限は無い
    敵の HP は戦闘の開始で「敵の体力」の率を掛ける

素点はゲームの `calculate_battle_effect` の値（GAME.md §2.10.4）。
プレイヤー 2×√(attack_power×武器)×k、敵と仲間 2×attack_power×k（魔法は magic_power）、
k は審判の power で決まる（weak 1.0 〜 extreme 約 2.45）。
防御もゲームが渡す値（敵は `get_npc_defense`、プレイヤーは防具の防御力）。
ゲームは同じ相手への2本目の裁きで素点を上書きし、`multiplier` はどこでも掛けない
（GAME.md §2.10.4）ので、最後の裁きの素点から k で割り戻して1本ずつ組み直す。

割り算にしたのは、素点・防御・HP がどれも「能力値 × レベル係数」で伸びるため。
防御が素点と同じなら半分が通り、NPC どうしの手数はレベルに依らない。
プレイヤーの素点は √(レベル係数 × 武器) なので装備で差がつく。
版17までは火力を独自に組んでいた（プレイヤーは k の前の基礎値 × 帯、敵と仲間は max_hp）ので、
敵と仲間の筋力もゲームの k も効いていなかった。
既定の率（敵の体力 250%・敵の一撃の強さ 12%）の試算は VERIFICATION.md §3.77。

## 2. 捨てられた効果の復元

変換を包んで、審判の生の戻りから落ちる前の
`TextStatusEffect`（`intensity`・`effects_per_turn`）と `AttributeEffect` を控える。

  * 毎ターンの継続ダメージ・回復: `reduce_status_turns_and_log` の直前に
    最大HP比で適用する（弱3% 〜 極18%、`intensity` で伸縮）。
    **継続ダメージでは死なない**（HP 1 で止める。倒すのは手番の側の仕事）
  * `AttributeEffect`: 対象の `status` へ普通の状態異常として書き
    （筋力低下 など、期限つき）、その間の与ダメ・被ダメに倍率を掛ける。
    筋・敏・知・魅 は与える側、耐・賢 は受ける側
  * スキルの強化・弱体（効果が `buff` / `debuff` のスキル）も同じ形で書く（版18）。
    期限はスキルの `duration`。変換はスキル側の強化・弱体も捨てていて、
    NPC の手の4割がこれを選んでいたのに数は1つも動いていなかった（GAME.md §2.10.4）

状態異常の入れ物はゲーム自身の `Character.status`
（`{status_name, description, duration}`。審判由来の「泥濘の拘束」と同じ形・同じ場所）。
期限の減算と削除はゲームがやるので、**MOD を外せば文章だけの状態異常に戻る**だけで
残骸は増えない（TECH.md §6.4 の趣旨。倍率の帳簿は MOD 内にしか無い）。

## 3. 防御

  * 自由入力に防御の言葉（防御・構え・盾・守る・受け流し・回避）があれば、
    審判の戻りに関わらず**防御の構え**が付く（次の自分の手番まで被ダメ半減）。
    審判の narration はそのまま活きる
  * `GUARD_BUTTON` が ON なら、戦闘画面のスキルのボタンを**「スキル・防御」**に
    改名し、開いた一覧の「やめる」の手前に「防御」を入れる
    （スキル1・（スキル2）・防御・やめる の並び）。
    戦闘画面そのものには足さない ― 枠は4つで埋まっていて、
    5つ目は画面右へあふれて右側の UI と重なる（実機。版4までの置き方）。
    押すと自由入力と同じ経路（`function_correspond_to_input` の `PhaseSpec`）に
    防御の文を流すので、**1手をちゃんと消費して**敵の手番が来る。
    経路が読めない画面では、構えだけ付けてその旨をログに残す
    （ボタンの作法は GAME.md §2.2。無害な `JustSetButtonToNormalPhase` を spec に
    持たせ、押下は `on_button_press` で印を見て横取りする。
    改名した text はセーブに焼かれうるが、MOD 無しで読んでも押せば普通に
    スキル一覧が開き、次の画面の組み直しで素の名前に戻る）

## 4. 回避と見切り（版18）

素のゲームは必中で、主人公の敏捷・判断（賢さ）は戦闘の数に効いていなかった。
受ける側の敏捷が高いほど攻撃を完全にかわし（回避）、判断が高いほど半分で済ませる（見切り）。
格下の攻撃は容易く、格上の攻撃は避けにくい。既定は味方だけで、敵も避ける設定がある。

審判の描写と数を合わせるため、**審判を呼ぶ前に**対象になりうる全員ぶんを振り、
かわす者を頼み文の末尾に「確定済み」として書き足す（`llm.wrap_outgoing`。手番の者の名前入りの目印「である'<名前>'の行動ターン」を含む送信だけ）。
攻撃の重さでは率を変えない（重さで分けると、当てたい審判が大技へ寄る）。
その手のダメージは同じ判定で 0 か半分にし、地の文の後ろに「（〇〇は攻撃をかわした）」を出す。
味方側が全部かわした手では、被弾の画面の揺れ（`InstanTaleHUD.shake_window`）も止める。

## 触らないもの

即時回復・逃走・`physical_integrity`・経験値・戦利品はゲームのまま。
セーブに MOD 独自の鍵は増やさない（敵の HP は戦闘中のセーブに入るが、戦闘の終わりに敵ごと消える）。
"""

import math
import random
import time

from instantale_modloader import choices, combat, frames, llm, ui

# 回避の判定の乱数。グローバルの `random` から引くとゲーム自身の乱数列がずれる（TECH.md §6.1）。
_RNG = random.Random()

LOG_BASENAME = "battle_tactics.log"
LOG_TAG = "battle tactics"

# ボタン辞書に付ける印のキー。mod ごとに別の文字列にする（TECH.md §3.3）。
MARK = "mod_battle_tactics"

ENEMY_SIDE = "enemy"
ALLY_SIDE = "ally"

# ---------------------------------------------------------------- 設定
# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。

# 敵の体力（%）。戦闘の開始で敵の max_hp に掛ける。100 でゲームのまま。
# ゲームの素点は HP に比べて大きく（NPC の weak 1発が HP の半分前後）、割り算の防御だけでは
# 雑魚が1〜2発で倒れる。与ダメを削らずに HP の側で手数を作る（ダメージの数字が小さく
# 見えると強くなった感が薄れる）。250 で雑魚は主人公だけで3発。
ENEMY_HP = 250

# 敵の一撃の強さ（%）。敵の一撃だけに掛かる（仲間には掛からない）。
# 敵の HP を伸ばすと敵が手番を持つ回数が増えるので、1発を小さくして1クエストの消耗を合わせる。
# 12 で、ソロはレベル相応の装備では倒れ・価値 +10 の装備で通り（Lv5〜45）、
# 仲間1人なら1クエストの消耗が3割強（VERIFICATION.md §3.77 の試算）。
ENEMY_DAMAGE = 12

# 防御の構えの軽減（%）。次の自分の手番までの被ダメに掛かる。
GUARD_CUT = 50

# レベル差の段。攻める側が受け側より高いとき、差に応じて与ダメに倍率が乗る。
# 低い側の与ダメは割らない（両方に掛けた版17までは、差15で消耗が4倍になり、どのレベルでも
# 格上が即死の相手になった。VERIFICATION.md §3.77 の試算）。
#     差 ≤ FAIR         適正。倍率なし
#     差 = ELITE        強敵の域。LEVEL_ELITE_MULT（既定 ×1.5）
#     差 ≥ OUTCLASS     格が違う。LEVEL_OUTCLASS_MULT（既定 ×2）
# 間は直線でつなぐ（1レベルで挙動が跳ねる崖を作らない）。
# ゲームの素点・防御・HP もレベル係数で伸びるので、格上は倍率が無くても手強い。
# 倍率はその差が小さくなる終盤でも「強敵」を残すためのもの。
LEVEL_FAIR_GAP = 10
LEVEL_ELITE_GAP = 15
LEVEL_OUTCLASS_GAP = 20
LEVEL_ELITE_MULT = 150
LEVEL_OUTCLASS_MULT = 200

# 仲間の装備の効き（%）。仲間の素点に 2×√(能力 × 武器) × k × この率を足し、
# 仲間の防御（本人の get_npc_defense）に防具の防御力 × この率を足す。
# 能力は物理なら attack_power、魔法なら magic_power（プレイヤーの素点と同じ組み方）。
# 0 で素のゲームどおり（仲間の装備は数に乗らない）。
ALLY_GEAR_PERCENT = 50

# 審判が付けた状態異常の中身（毎ターンの効果・能力の増減）を復元して効かせる。
RESTORE_EFFECTS = True

# 戦闘の選択肢に「防御」ボタンを足す。
GUARD_BUTTON = True

# 主人公の通常攻撃の審判に「一撃の重さは修正で表し、追加効果にダメージを重ねない」と頼む。
# 通常攻撃の定義は `instant_damage weak`（セーブのスキル表）。ローカルのモデルは通常の武器でも
# 9割の手で `additional_effects` に `instant_damage`（多くは extreme）を重ね、通常攻撃が
# スキル並みになっていた（クラウドの gpt-6-luna は重ねない。VERIFICATION.md §3.77）。
# 敵の体力と敵の一撃の強さの既定値は重ねない出し方で決めたので、この頼みと組で効く。
STEADY_BASIC_ATTACK = True

# 回避と見切り。受ける側の敏捷が攻める側より高いほど攻撃を完全にかわし（回避）、
# 判断（賢さ）が高いほど急所を外して半分で済ませる（見切り）。素のゲームは必中で、
# 主人公の敏捷・判断は戦闘の数に1つも効いていなかった（防御は防具の値そのもの）。
#     率 ＝ min(能力差の上限, 能力差1あたり × 能力差) ＋ レベル差1あたり × 格下のレベル差
#     格上の攻撃は同じ率で避けにくくなる（EVASION_UPHILL）。合計は 0〜EVASION_CEILING
# 2.5% で、敏捷と判断を 30 に振った作りと耐久を 30 に振った作りが同じくらい戦いやすい
# （VERIFICATION.md §3.77 の試算）。能力差の上限 50% で回避だけの無敵は作れない。
# 格下の攻撃はレベル差で加算して容易く避ける（10 下で3回に1回）。
EVASION = True                 # 味方が回避・見切りをする
ENEMY_EVASION = False          # 敵も回避・見切りをする
EVASION_PER_POINT = 2.5        # 能力差1あたり（%）
EVASION_STAT_CAP = 50          # 能力差の部分の上限（%）
EVASION_PER_LEVEL = 3.0        # レベル差1あたり（%）
EVASION_UPHILL = True          # 格上の攻撃は避けにくい

# ---------------------------------------------------------------- 定数
# 回避・見切りの合計の天井。どれだけ差があっても1割は当たる。
EVASION_CEILING = 0.90

# 攻撃の重さ（power）では率を変えない。重さで分けると審判に「大技は当たる」と伝えることになり、
# 当てたい審判が大技へ寄る。ボスの大技は使用回数（`137_fix_npc_skill_uses`）と
# ボス自身の高い敏捷・判断で既に絞られている（VERIFICATION.md §3.77）。

# 見切りで残るダメージの割合。
GRAZE_KEEP = 0.5

# 1手を閉じた後、何秒までに来た画面の揺れをその手のものとみなすか。
# 敵の手と次の手の間は LLM の待ちで数秒あるので、隣の手と取り違えない。
SHAKE_AFTER_ACTION = 1.5

# ゲームの素点の係数 k（power → 倍率。GAME.md §2.10.4 の実測）。
# 同じ相手に裁きが2本あると、ゲームは最後の1本の素点しか残さないので、
# 素点を k で割り戻して1本ずつ組み直すのに使う。
GAME_K = {"weak": 1.0, "normal": 1.2, "strong": 1.5,
          "very_strong": 1.95, "extreme": 2.45}

# 1発の下限割合。「1点」だけが延々続く状態を作らない。
MIN_FRACTION = 0.01

# 敵の HP を掛けたかどうかの見分けの幅。ゲームの HP は 耐久 × 4 × レベル係数 を
# 四捨五入の範囲（±2）で守る（GAME.md §2.10.4）。掛けた後はこの幅から外れる。
HP_FORMULA_SLACK = 2

# 毎ターンの継続効果の基準割合（%）。intensity 3 を等倍とする。
PER_TURN_BANDS = {"weak": 3, "normal": 5, "strong": 8,
                  "very_strong": 12, "extreme": 18}

# AttributeEffect の倍率の振れ幅。enhancement は有利に、reduction は不利に。
ATTR_EFFECT_MULTS = {"weak": 0.10, "normal": 0.15, "strong": 0.20,
                     "very_strong": 0.30, "extreme": 0.40}

# どの能力がどちら側の倍率になるか。
# 筋・敏・知・魅 は与える側（攻撃の質）、耐・賢 は受ける側（守りの質）。
ATTR_SIDE = {"str": "out", "dex": "out", "int": "out", "cha": "out",
             "con": "in", "wis": "in"}

ATTR_LABELS = {"str": "筋力", "dex": "敏捷", "con": "耐久",
               "int": "知力", "wis": "賢さ", "cha": "魅力"}

# 復元した AttributeEffect に付ける期限（審判のスキーマに期限が無いため）。
ATTR_DURATION = 3

# 防御の構え。状態異常としても見えるようにする（表示と審判のプロンプト用）。
GUARD_STATUS_NAME = "防御の構え"
GUARD_STATUS_DESCRIPTION = "身を固めて次の行動まで受けるダメージを抑えている。"

# 自由入力を防御と見なす言葉。
GUARD_WORDS = ("防御", "構え", "盾", "守る", "守り", "受け流", "回避", "かわす")

# 防御ボタンが流す自由入力の文。
GUARD_COMMAND = "防御に徹して身を守る"
GUARD_BUTTON_LABEL = "防御"

# 防御の置き場所はスキル一覧の中（先頭）。
# 戦闘画面のボタンは4つで枠が埋まっていて、5つ目は画面右へあふれて
# 右側の UI と重なる（実機）。戦闘画面には足さず、
# スキルのボタンの名前をこれに変えて、開いた一覧の先頭に防御を入れる。
SKILL_MENU_LABEL = "スキル・防御"

# スキル一覧の画面を組んでいるマネージャ（out/recon/targets.txt）。
# 戦闘画面のスキルのボタンもこのクラス名を spec に持つ。
SKILL_MANAGER_CLS = "SkillChoicePhaseManager"

# スキル一覧の「やめる」。防御はこの手前に入れる
# （並びを スキル1・（スキル2）・防御・やめる にする。
# 見分けは文字列ではなく spec のクラス名で行う。GAME.md §2.5）。
CANCEL_MANAGER_CLS = "CancelBattleActionManager"


# 通常攻撃の審判（`referee_player_attack_new_new`）の頼み文にだけある行。
# 仲間・敵の審判は「スキル詳細とは別の」、スキルと自由入力の審判にはこの行が無い（output_data の実記録）。
BASIC_ATTACK_LINE = ("- additional_effects: 戦闘の状況や流れから本来とは別の追加効果が"
                     "発生すると考えられる場合、その内容を記入する。追加が無い場合は空のリストを返す。")
BASIC_ATTACK_RULE = ("通常攻撃そのもののダメージは skill_effects で既に発動するので、"
                     "ここに instant_damage として重ねて書かない。一撃の重さは modifications で表す。")


# ================================================================ 純関数
# 数の芯はモジュール直下に置く。オフラインの検査（tools/tests/）から直接叩ける。

def steady_basic_attack(text):
    """通常攻撃の審判の頼み文なら、`additional_effects` の行の末尾に頼みを足す。それ以外はそのまま。"""
    if not isinstance(text, str) or BASIC_ATTACK_LINE not in text \
            or BASIC_ATTACK_RULE in text:
        return text
    return text.replace(BASIC_ATTACK_LINE, BASIC_ATTACK_LINE + BASIC_ATTACK_RULE, 1)


def game_k(power):
    """power の列挙 → ゲームの素点の係数 k。知らない語は normal 扱い（黙って 0 にしない）。"""
    return GAME_K.get(power, GAME_K["normal"])


def _number(value, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if value == value else default      # NaN も読めない値として扱う


def _ramp(value, low, high, at_low, at_high):
    """`low`〜`high` の間を直線でつなぐ。外側は端の値。"""
    if high <= low or value <= low:
        return at_low
    if value >= high:
        return at_high
    return at_low + (at_high - at_low) * (value - low) / (high - low)


def level_gap(attacker_level, defender_level):
    """レベル差（攻める側 − 受け側）。読めなければ 0（＝適正扱い）。"""
    try:
        return int(attacker_level) - int(defender_level)
    except Exception:
        return 0


def level_multiplier(gap):
    """レベル差の倍率。攻める側が高いときだけ与ダメに掛かる（低い側は 1.0）。

    FAIR まで 1.0、ELITE で LEVEL_ELITE_MULT、OUTCLASS で LEVEL_OUTCLASS_MULT。間は直線。
    """
    if gap <= LEVEL_FAIR_GAP:
        return 1.0
    elite = max(1.0, LEVEL_ELITE_MULT / 100.0)
    outclass = max(elite, LEVEL_OUTCLASS_MULT / 100.0)
    if gap <= LEVEL_ELITE_GAP:
        return _ramp(gap, LEVEL_FAIR_GAP, LEVEL_ELITE_GAP, 1.0, elite)
    return _ramp(gap, LEVEL_ELITE_GAP, LEVEL_OUTCLASS_GAP, elite, outclass)


def hit_damage(entries, raw, defense, defender_max_hp,
               out_mult=1.0, in_mult=1.0,
               attacker_level=None, defender_level=None,
               enemy=False, bonus=0.0):
    """1発の最終ダメージ。

    `entries` はその対象への `[(power, multiplier), ...]`（1手で同じ相手に複数乗ることがある）。
    `raw` はゲームがその対象に渡した素点で、`entries` の**最後**の裁きの値
    （ゲームは同じ相手の2本目で素点を上書きする）。そこから k で割り戻した基礎に、
    裁き1本ずつの k を掛けて r を作り、`multiplier × r × r ÷ (r + 防御)` を足し合わせる。
    `bonus` は基礎への上乗せ（仲間の武器。`ally_gear_bonus`）。
    `enemy` なら敵の一撃の強さ（`ENEMY_DAMAGE`）を掛ける。
    """
    entries = list(entries) or [("normal", 1)]
    raw = max(0.0, _number(raw, 0.0))
    defense = max(0.0, _number(defense, 0.0))
    base = raw / game_k(entries[-1][0]) + max(0.0, _number(bonus, 0.0))
    damage = 0.0
    for power, multiplier in entries:
        r = base * game_k(power)
        if r > 0:
            damage += _number(multiplier, 1.0) * r * r / (r + defense)
    damage *= level_multiplier(level_gap(attacker_level, defender_level))
    damage *= _number(out_mult, 1.0) * _number(in_mult, 1.0)
    if enemy:
        damage *= ENEMY_DAMAGE / 100.0
    defender_max_hp = _number(defender_max_hp, 0.0)
    if defender_max_hp > 0:
        damage = max(damage, defender_max_hp * MIN_FRACTION)
    return max(1, int(round(damage)))


def evasion_chance(defender_stat, attacker_stat, defender_level=None, attacker_level=None):
    """回避（敏捷）か見切り（判断）の率 [0, EVASION_CEILING]。能力値とレベルが読めなければ 0 として扱う。"""
    diff = _number(defender_stat, 0.0) - _number(attacker_stat, 0.0)
    stat = min(EVASION_STAT_CAP / 100.0, max(0.0, EVASION_PER_POINT / 100.0 * diff))
    gap = level_gap(defender_level, attacker_level)          # 正 ＝ 攻める側が格下
    if gap < 0 and not EVASION_UPHILL:
        gap = 0
    return max(0.0, min(EVASION_CEILING, stat + EVASION_PER_LEVEL / 100.0 * gap))


def evasion_outcome(roll):
    """先に振った判定 `roll`（`(u_回避, u_見切り, 回避率, 見切り率)`）から結果を出す。

    "evade"（0）・"graze"（半分）・None（当たる）。攻撃の重さでは変えない。
    """
    u_evade, u_graze, evade, graze = roll
    if u_evade < evade:
        return "evade"
    if u_graze < graze:
        return "graze"
    return None


# 審判に伝える文。
EVASION_WORDS = {"evade": "身のこなしで完全にかわす（傷を負わない）",
                 # 「見切って」と書いた版では、審判が完全にかわした描写にした（10-01 の実機。傷は 10 入った）
                 "graze": "当たるが、急所を外して浅い傷で済む（かわしはしない。傷は負う）"}


def evasion_hint(name, roll):
    """1人ぶんの審判への指示。何も起きなければ None。"""
    outcome = evasion_outcome(roll)
    if outcome is None:
        return None
    return "- {}: この手番に受ける攻撃は{}".format(name, EVASION_WORDS[outcome])


# 審判の頼み文の目印（手番の者の名前入り。`output_data` の実記録の文面）。回避の文はこれを含む送信にだけ足す。
NPC_TURN_MARKER = "である'{}'の行動ターン"
PLAYER_TURN_MARKER = "今、プレイヤーである{}は"

EVASION_HEADER = ("【回避の判定（ゲームが確定済み。必ずこのとおりに描写する）】\n"
                  "この手番の攻撃の対象になった者のうち、次の者は攻撃をかわすか浅い傷で済ませる。"
                  "対象に選ぶかどうかはいつもどおり判断してよい。")


def ally_gear_bonus(ability, weapon, percent=None):
    """仲間の武器の上乗せ（素点の基礎＝k を掛ける前の値へ足す）。

    プレイヤーと同じ 2×√(能力 × 武器) に率（`ALLY_GEAR_PERCENT`）を掛ける。
    能力は物理なら attack_power、魔法なら magic_power。読めなければ 0（ゲームのまま）。
    """
    rate = (ALLY_GEAR_PERCENT if percent is None else percent) / 100.0
    ability, weapon = _number(ability, 0.0), _number(weapon, 0.0)
    if ability <= 0 or weapon <= 0 or rate <= 0:
        return 0.0
    return 2.0 * math.sqrt(ability * weapon) * rate


def level_factor(level):
    """ゲームのレベル係数 m = (レベル + 5) ÷ 5（GAME.md §2.10.4）。"""
    return (_number(level, 0.0) + 5.0) / 5.0


def game_max_hp(constitution, level):
    """ゲームの式どおりの max_hp（耐久 × 4 × m）。読めなければ None。"""
    constitution = _number(constitution, None)
    if constitution is None or constitution <= 0 or _number(level, None) is None:
        return None
    return constitution * 4.0 * level_factor(level)


def scaled_hp(hp, constitution, level, percent=None):
    """敵の HP を「敵の体力」の率で伸ばした値。掛けない（掛け済み・読めない・100%）なら None。

    `hp` がゲームの式どおりのときだけ掛ける。掛けた後は式から外れるので二度掛けにならない
    （戦闘中のセーブから読み直した敵も同じ判定で素通りする）。
    """
    percent = ENEMY_HP if percent is None else percent
    hp = _number(hp, None)
    expected = game_max_hp(constitution, level)
    if hp is None or hp <= 0 or expected is None or percent == 100:
        return None
    if abs(hp - expected) > HP_FORMULA_SLACK:
        return None
    return max(1, int(round(hp * percent / 100.0)))


def gear_defense(defense, gear=None, percent=None):
    """受け側の防御。本体が渡した値（仲間は `get_npc_defense`）に装備の防御力 × 率を足す。"""
    rate = (ALLY_GEAR_PERCENT if percent is None else percent) / 100.0
    try:
        gear = float(gear)
        base = float(defense)
    except (TypeError, ValueError):
        return defense
    if gear <= 0 or rate <= 0:
        return defense
    return base + gear * rate


def per_turn_amount(max_hp, power, intensity):
    """毎ターンの継続効果の点数。intensity 3 を等倍に、1〜5 で 2/3〜4/3 倍。"""
    band = PER_TURN_BANDS.get(power, PER_TURN_BANDS["normal"]) / 100.0
    try:
        scale = (2.0 + max(1, min(5, int(intensity)))) / 5.0
    except Exception:
        scale = 1.0
    return max(1, int(round(max_hp * band * scale)))


def read_field(holder, name, default=None):
    """辞書でも pydantic のモデルでも同じように読む。"""
    if holder is None:
        return default
    if isinstance(holder, dict):
        return holder.get(name, default)
    return getattr(holder, name, default)


def extract_extras(referee_response):
    """審判の生の戻りから、変換が捨てる効果を抜き出す。

    戻りは `[{"kind": "status", "targets": [...], "status_name": ...,
    "per_turn": [(種別, power), ...], "intensity": n, "duration": n}, ...]` と
    `[{"kind": "attribute", "targets": [...], "type": "enhancement"|"reduction",
    "attribute_type": "str".., "power": ...}]` の混在リスト。
    読めない項目は黙って飛ばす（復元は上乗せであって、失敗しても素の戦闘は動く）。
    """
    found = []
    effects = read_field(referee_response, "additional_effects") or []
    if not isinstance(effects, (list, tuple)):
        return found
    for effect in effects:
        kind = read_field(effect, "type")
        targets = read_field(effect, "target") or []
        if isinstance(targets, str):
            targets = [targets]
        targets = [str(t) for t in targets]
        if kind == "text_status":
            per_turn = []
            for tick in read_field(effect, "effects_per_turn") or []:
                tick_kind = read_field(tick, "type")
                if tick_kind in ("instant_damage", "instant_heal"):
                    per_turn.append((tick_kind,
                                     str(read_field(tick, "power", "normal"))))
            found.append({
                "kind": "status", "targets": targets,
                "status_name": str(read_field(effect, "status_name", "")),
                "per_turn": per_turn,
                "intensity": read_field(effect, "intensity", 3),
                "duration": read_field(effect, "duration", 3),
            })
        elif kind in ("enhancement", "reduction"):
            found.append({
                "kind": "attribute", "targets": targets, "type": kind,
                "attribute_type": str(read_field(effect, "attribute_type", "")),
                "power": str(read_field(effect, "power", "normal")),
            })
    return found


def skill_extras(skills, referee_response):
    """選ばれたスキルの強化・弱体（`buff` / `debuff`）を、`extract_extras` の attribute と同じ形で返す。

    `skills` は手番の者の `Character.skills`。審判の戻りの `skill` で引き、
    `skill_effects` の `effect_id`（1 始まり）と `targets` で、どの効果が誰に掛かったかを読む。
    期限はスキルの `duration`（`"duration"` の鍵。無ければ呼び出し側が既定を使う）。
    """
    found = []
    name = read_field(referee_response, "skill")
    skill = skills.get(name) if isinstance(skills, dict) and isinstance(name, str) else None
    effects = skill.get("effects") if isinstance(skill, dict) else None
    if not isinstance(effects, list):
        return found
    for entry in read_field(referee_response, "skill_effects") or []:
        try:
            effect = effects[int(read_field(entry, "effect_id")) - 1]
        except (TypeError, ValueError, IndexError):
            continue
        if not isinstance(effect, dict) or effect.get("type") not in ("buff", "debuff"):
            continue
        targets = read_field(entry, "targets") or []
        if isinstance(targets, str):
            targets = [targets]
        duration = effect.get("duration")
        found.append({
            "kind": "attribute", "targets": [str(t) for t in targets],
            "type": "enhancement" if effect["type"] == "buff" else "reduction",
            "attribute_type": str(effect.get("attribute_type", "")),
            "power": str(effect.get("power", "normal")),
            "duration": duration if isinstance(duration, int) and not isinstance(duration, bool)
            and duration > 0 else None,
        })
    return found


# 継続効果の説明に使う強さの語。画面では `308_` が「泥の浸食(毎ターン小ダメージ)」と添える。
PER_TURN_LABELS = {"weak": "小", "normal": "中", "strong": "大",
                   "very_strong": "特大", "extreme": "極大"}


def per_turn_description(per_turn):
    """継続効果の帳簿 `[(種別, power), ...]` → 短い説明文。

    審判の `description` は「泥が足元から這い上がってくる」のような描写で、
    何が起きるかが読めない。
    数字は出さず「毎ターン小ダメージ」の粒度に留める（実数は毎巡の行で出る）。
    """
    parts = []
    for tick_kind, power in per_turn:
        label = PER_TURN_LABELS.get(power, PER_TURN_LABELS["normal"])
        parts.append("毎ターン{}{}".format(
            label, "ダメージ" if tick_kind == "instant_damage" else "回復"))
    return "・".join(parts)


def attribute_recipe(kind, attribute_type, power):
    """AttributeEffect 1件 → (状態異常の名前, 説明, 倍率の帳簿)。対象外は None。"""
    side = ATTR_SIDE.get(attribute_type)
    label = ATTR_LABELS.get(attribute_type)
    if side is None or label is None:
        return None
    amount = ATTR_EFFECT_MULTS.get(power, ATTR_EFFECT_MULTS["normal"])
    helpful = (kind == "enhancement")
    name = label + ("強化" if helpful else "低下")
    if side == "out":
        mult = 1.0 + amount if helpful else 1.0 - amount
        description = "与えるダメージが{}".format("増加" if helpful else "減少")
        book = {"out_mult": mult}
    else:
        mult = 1.0 - amount if helpful else 1.0 + amount
        description = "受けるダメージが{}".format("減少" if helpful else "増加")
        book = {"in_mult": mult}
    # 説明は短く。画面では `308_` が「筋力低下(与えるダメージが減少)」と添える。
    return name, description, book


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag=LOG_TAG + ":")
    warn = ctx.warner(LOG_TAG)

    state = {
        # 開いている1手。{"actor_key","side","attacker","entries","plan"}。
        "action": None,
        # 防御の構え。{表示名: True}。自分の手番が来たら消える。
        "guards": {},
        # 復元した効果の帳簿。{(持ち主の鍵, 状態異常名): recipe}。鍵は `recipe_owner`
        "recipes": {},
        # get_npc_defense の直近の呼び出し（防御値 → 持ち主の照合用）。
        "last_defense": None,
        # 防御ボタンで流す先の記録（画面の spec を1戦闘1回だけ写す）。
        "input_spec_logged": False,
        # スキル一覧を組んでいる最中か（防御を差し込む場面の印）。
        "skill_screen": False,
        # 回避の判定。審判を呼ぶ前に対象になりうる全員ぶん振り、その者の1手で使う。
        # {"actor": 手番の者, "rolls": {表示名: (u_回避, u_見切り, 回避率, 見切り率)}}
        "evasion": None,
        # 審判へ足す文。{頼み文の目印: 文}。目印はその手番の者の名前を含む一文
        # （審判の送信は審判を呼んだのとは別のスレッドで走るので、スレッドでは見分けられない。実機）
        "hints": {},
        # 防御ボタンを押してから次のスキル一覧が出るまで。二度目の押下は捨てる
        "guard_pending": False,
        # 防御ボタンの手番の者。その手のダメージは入れない（審判が敵の攻撃を描いて、
        # そのダメージを主人公の手の結果として出すことがある。自分へ 282・敵へ 568 が実機で出た）
        "guard_turn": None,
        # 1手の終わりに画面へ出す行（地の文の後ろ、`308_` の数字の前に付く）
        "notes": [],
        # 閉じた直後の1手の味方側の被弾。{"at": 時刻, "evaded": 全部かわしたか}。
        # 揺れが1手を閉じた後に来ても見分けられるように残す
        "last_ally_hits": None,
    }

    screen = ui.Screen(ctx, write, tag=LOG_TAG, mark=MARK)

    # ------------------------------------------------------------ 面々
    def enemy_dict(app):
        found = getattr(app, "current_enemy_dict", None)
        return found if isinstance(found, dict) else None

    def combatants(app):
        """[(side, key, 表示名, 持ち主)]。308_ と同じ読み方。"""
        found = []
        enemies = enemy_dict(app)
        if enemies:
            for key, holder in list(enemies.items()):
                name = frames.attr(holder, "name", None) or str(key)
                found.append((ENEMY_SIDE, str(key), str(name), holder))
        player = getattr(app, "player", None)
        if player is not None:
            name = frames.attr(player, "name", None) or ui.PLAYER_ID
            found.append((ALLY_SIDE, ui.PLAYER_ID, str(name), player))
        try:
            member_ids = ui.party_member_ids(app)
        except Exception:
            member_ids = []
        for member_id in member_ids:
            member = ui.character_of(app, member_id)
            if member is None:
                continue
            name = frames.attr(member, "name", None) or str(member_id)
            found.append((ALLY_SIDE, str(member_id), str(name), member))
        return found

    def holders_named(app, name):
        """表示名で持ち主を引く。同名の敵が並ぶことがあるので複数返す。"""
        name = str(name)
        found = []
        for _side, key, display, holder in combatants(app):
            if display == name or key == name:
                found.append(holder)
        return found

    def actor_holder(app, character_key, character_side):
        """1手の主。敵は `current_enemy_dict` の鍵、味方は名前で引く。"""
        if character_side == "敵側":
            enemies = enemy_dict(app)
            if enemies is not None:
                return enemies.get(character_key)
            return None
        for _side, _key, display, holder in combatants(app):
            if display == str(character_key):
                return holder
        return None

    def max_hp_of(holder):
        value = frames.attr(holder, "max_hp", None)
        if not isinstance(value, (int, float)) or value <= 0:
            value = frames.attr(holder, "current_hp", None)
        if not isinstance(value, (int, float)) or value <= 0:
            return None
        return float(value)

    def ability_of(holder, category):
        """素点の能力側。物理は attack_power、魔法は magic_power（ゲームの素点と同じ。GAME.md §2.10.4）。"""
        name = "magic_power" if category == "magical" else "attack_power"
        value = frames.attr(holder, name, None)
        return float(value) if isinstance(value, (int, float)) and value > 0 else None

    def name_of(holder, fallback="?"):
        name = frames.attr(holder, "name", None)
        return str(name) if name else str(fallback)

    # ------------------------------------------------------------ 状態異常
    def status_dict(holder):
        found = frames.attr(holder, "status", None)
        return found if isinstance(found, dict) else None

    def add_status(holder, name, description, duration):
        """ゲーム自身の入れ物（`Character.status`）へ同じ形で書く。

        審判由来の状態異常と同じ形・同じ場所なので、期限の減算も削除も
        ゲームに任せられる（GAME.md §2.10.2 の実測の形）。
        """
        statuses = status_dict(holder)
        if statuses is None:
            return False
        statuses[str(name)] = {"status_name": str(name),
                               "description": str(description),
                               "duration": int(duration)}
        return True

    def owner_key(side, display, holder):
        """帳簿の持ち主の鍵。敵は個体ごと、味方は表示名。

        敵の `Character.name` には連番が付かず、同名の敵が並ぶ（GAME.md §2.10）。
        名前で鍵を付けると、片方に付いた倍率がもう片方にも掛かり、
        もう片方の手番で帳簿ごと消える。
        味方は人名が世界で1つなので名前のまま（戦闘をまたいで残る状態異常の帳簿を、
        実体が作り直されても引き継ぐ）。
        """
        return (side, id(holder)) if side == ENEMY_SIDE else (side, display)

    def recipe_owner(app, holder):
        """戦闘に居る `holder` の帳簿の鍵。居なければ None。"""
        if holder is None:
            return None
        for side, _key, display, found in combatants(app):
            if found is holder:
                return owner_key(side, display, found)
        return None

    def status_mults(app, holder):
        """`holder` にいま乗っている倍率（与える側, 受ける側）。"""
        out_mult, in_mult = 1.0, 1.0
        owner = recipe_owner(app, holder)
        if owner is None:
            return out_mult, in_mult
        for (target, _status), recipe in state["recipes"].items():
            if target != owner:
                continue
            out_mult *= recipe.get("out_mult", 1.0)
            in_mult *= recipe.get("in_mult", 1.0)
        return out_mult, in_mult

    def active_recipes(app, holder):
        """持ち主の status に残っている帳簿だけを返す。消えた分は落とす。"""
        statuses = status_dict(holder)
        owner = recipe_owner(app, holder)
        if statuses is None or owner is None:
            return []
        found, dead = [], []
        for key, recipe in state["recipes"].items():
            if key[0] != owner:
                continue
            if key[1] in statuses:
                found.append((key, recipe))
            else:
                dead.append(key)
        for key in dead:
            del state["recipes"][key]
        return found

    def describe_restored_statuses(app):
        """帳簿にある継続効果の `description` を短い説明文に書き換える。

        `text_status` はゲームが `resolve_battle_effect` の中で `status` へ書く
        （`convert_...` の時点ではまだ無い）ので、その直後に当てる。
        `308_` の報告は `handle_battle_situation` の外側なので、書き換え後の文が出る。
        """
        holders = {owner_key(side, display, holder): holder
                   for side, _key, display, holder in combatants(app)}
        for (owner, status_name), recipe in list(state["recipes"].items()):
            if not recipe.get("per_turn") or owner not in holders:
                continue
            statuses = status_dict(holders[owner])
            entry = statuses.get(status_name) if statuses else None
            if isinstance(entry, dict):
                entry["description"] = per_turn_description(recipe["per_turn"])

    def say(app, text):
        try:
            app.add_text(text)
        except Exception:
            ctx.log_exc("battle tactics: add_text failed")

    # ------------------------------------------------------------ 回避
    def ability(holder, key):
        scores = frames.attr(holder, "ability_scores", None)
        return scores.get(key) if isinstance(scores, dict) else None

    def prepare_evasion(actor, marker):
        """審判を呼ぶ前に、`actor` の攻撃の対象になりうる全員の回避・見切りを振る。

        結果は審判への文（`state["hints"][marker]`）と、その手のダメージ（`state["evasion"]`）の両方に使う。
        `marker` はその審判の頼み文にだけある一文（手番の者の名前入り）。
        味方は `EVASION`、敵は `ENEMY_EVASION` が ON のときだけ振る。
        """
        state["evasion"] = None
        app = ui.find_app()
        if app is None or actor is None:
            return
        enemies = list((enemy_dict(app) or {}).values())
        actor_is_enemy = any(holder is actor for holder in enemies)
        if actor_is_enemy and not EVASION:
            return
        if not actor_is_enemy and not ENEMY_EVASION:
            return
        wanted = ALLY_SIDE if actor_is_enemy else ENEMY_SIDE
        rolls, lines = {}, []
        for side, _key, name, holder in combatants(app):
            if side != wanted or name in rolls:
                continue
            level, own = frames.attr(holder, "experience_level", None), frames.attr(actor, "experience_level", None)
            roll = (_RNG.random(), _RNG.random(),
                    evasion_chance(ability(holder, "dexterity"), ability(actor, "dexterity"), level, own),
                    evasion_chance(ability(holder, "wisdom"), ability(actor, "wisdom"), level, own))
            rolls[name] = roll
            line = evasion_hint(name, roll)
            if line:
                lines.append(line)
        state["evasion"] = {"actor": actor, "rolls": rolls}
        if lines and marker:
            state["hints"][marker] = EVASION_HEADER + "\n" + "\n".join(lines)
        if rolls:
            # 誰もかわさない回も書く（書かないと「振って外れた」と「振っていない」が見分けられない）
            write("evasion rolled for {}'s turn: {}{}".format(name_of(actor), "; ".join(
                "{} evade {:.0%} graze {:.0%}".format(n, r[2], r[3]) for n, r in rolls.items()),
                "" if lines else " (nobody dodges)"))

    def evasion_for(attacker, defender_name):
        """この手の攻め手に振ってあった、受け側の判定。無ければ None。"""
        evasion = state["evasion"]
        if not evasion or evasion["actor"] is not attacker:
            return None
        return evasion["rolls"].get(defender_name)

    # ------------------------------------------------------------ 1手の文脈
    def open_action(app, character_key, character_side):
        side = ENEMY_SIDE if character_side == "敵側" else ALLY_SIDE
        attacker = actor_holder(app, character_key, character_side)
        actor_name = name_of(attacker, character_key)
        # 構えは「武装済み」のものだけ、自分の手番が来たら終わる。
        # 立てたばかり（未武装）の構えはこの手そのもの ―
        # ここで消すと、構えた手自身に食われて次の敵の手を守れない
        # （版2までの欠陥。実機で guard begins の3秒後に ended が出た）。
        guard = state["guards"].get(actor_name)
        if guard is not None and guard.get("armed"):
            del state["guards"][actor_name]
            write("guard ended: {} acted".format(actor_name))
        state["action"] = {"app": app, "actor_key": str(character_key),
                           "side": side, "attacker": attacker,
                           "actor_name": actor_name,
                           "entries": {}, "categories": {}, "plan": {}}

    def close_action():
        action = state["action"]
        state["action"] = None
        state["last_defense"] = None
        if action is not None and action.get("ally_hits"):
            state["last_ally_hits"] = {"at": time.monotonic(),
                                       "evaded": all(action["ally_hits"])}
        # 回避の行は地の文の後ろに出す（ダメージの計算中に言うと地の文より先に出る）
        notes, state["notes"] = state["notes"], []
        if action is not None and notes:
            say(action["app"], "\n".join(notes))
        if action is not None and state["evasion"] and state["evasion"]["actor"] is action["attacker"]:
            state["evasion"] = None
        if action is not None and state["guard_turn"] is not None                 and state["guard_turn"] is action["attacker"]:
            state["guard_turn"] = None
        # 構えを立てた手が閉じた。ここから次の自分の手まで構えが生きる。
        if action is not None:
            guard = state["guards"].get(action["actor_name"])
            if guard is not None and not guard.get("armed"):
                guard["armed"] = True
                write("guard armed: {} (until their next action)".format(
                    action["actor_name"]))

    def register_plan(battle_action, effect):
        """`calculate_battle_effect` の入出力から、素点 → 対象の照合表を作る。"""
        action = state["action"]
        if action is None:
            return
        entries, categories = {}, {}
        for entry in read_field(battle_action, "instant_damage") or []:
            target = read_field(entry, "target")
            if target is None:
                continue
            entries.setdefault(str(target), []).append(
                (str(read_field(entry, "power", "normal")),
                 read_field(entry, "multiplier", 1)))
            # 素点は最後の裁きのもの（ゲームが上書きする）なので、分類も最後のものを残す
            categories[str(target)] = str(read_field(entry, "category", "physical"))
        action["entries"] = entries
        action["categories"] = categories
        plan = {}
        if isinstance(effect, (list, tuple)) and effect:
            first = effect[0]
            if isinstance(first, dict):
                for target, raw in first.items():
                    if isinstance(raw, (int, float)):
                        plan[str(target)] = raw
        action["plan"] = plan

    def find_defender(app, raw, defense):
        """`get_instant_damage(素点, 防御)` の受け側を特定する。

        1手の照合表（素点 → 対象）を第一に、
        直前の `get_npc_defense` の持ち主（防御値が一致すれば確実）で裏を取る。
        どちらでも決まらなければ None ＝ 素通し。
        """
        action = state["action"]
        if action is None:
            return None, None
        last = state["last_defense"]
        if last is not None and last[1] == defense:
            # 直前に防御値を聞かれた者が受け側（敵被弾の経路。GAME.md §2.10.2）。
            # 素点の照合が取れればその対象名、取れなくても持ち主は確かなので通す。
            holder = last[0]
            for target, value in list(action["plan"].items()):
                if value == raw:
                    del action["plan"][target]
                    return target, holder
            return name_of(holder), holder
        # 防御の裏が取れないとき（味方被弾は防具の値で来る。GAME.md §2.10.2）は
        # 素点の照合だけで決める。
        for target, value in list(action["plan"].items()):
            if value == raw:
                del action["plan"][target]
                holders = holders_named(app, target)
                return target, holders[0] if holders else None
        return None, None

    # ================================================================ 圧縮
    @ctx.wrap("__main__:BattlePhaseManager.handle_battle_situation",
              required=False, safe=True)
    def handle_battle_situation(orig, self, character_key=None,
                                character_side=None, battle_action=None,
                                *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        try:
            open_action(app, character_key, character_side)
        except Exception:
            ctx.log_exc("battle tactics: cannot open the action")
        # 本体が投げても1手は必ず閉じる。閉じないと古い `state["action"]` が残って
        # 手の外で呼ばれた `get_instant_damage` まで圧縮し、立てた構えも武装されない。
        try:
            return orig(self, character_key, character_side, battle_action,
                        *args, **kwargs)
        finally:
            close_action()

    @ctx.wrap("__main__:BattlePhaseManager.calculate_battle_effect",
              required=False, safe=True)
    def calculate_battle_effect(orig, self, battle_action=None, *args, **kwargs):
        result = orig(self, battle_action, *args, **kwargs)
        try:
            register_plan(battle_action, result)
        except Exception:
            ctx.log_exc("battle tactics: cannot register the plan")
        return result

    @ctx.wrap("scripts.characters:Character.get_npc_defense",
              required=False, safe=True)
    def get_npc_defense(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if state["action"] is not None:
            state["last_defense"] = (self, result)
        return result

    @ctx.wrap("scripts.functions:get_instant_damage", required=False, safe=True)
    def get_instant_damage(orig, attack=None, defense=None, *args, **kwargs):
        result = orig(attack, defense, *args, **kwargs)
        try:
            action = state["action"]
            if action is None or not isinstance(attack, (int, float)):
                return result
            app = action["app"]
            target, holder = find_defender(app, attack, defense)
            if holder is None:
                if action["plan"]:
                    warn("unmatched",
                         "a damage roll did not match the plan (attack={}, "
                         "defense={}); left as is".format(attack, defense))
                return result
            defender_max = max_hp_of(holder)
            if defender_max is None:
                return result
            entries = action["entries"].get(target) or [("normal", 1)]
            attacker = action["attacker"]
            attacker_name = action["actor_name"]
            defender_name = name_of(holder, target)
            out_mult, _ = status_mults(app, attacker)
            _, in_mult = status_mults(app, holder)
            guard = state["guards"].get(defender_name)
            if guard:
                in_mult *= 1.0 - GUARD_CUT / 100.0
            attacker_level = frames.attr(attacker, "experience_level", None)
            defender_level = frames.attr(holder, "experience_level", None)
            player = getattr(app, "player", None)
            gear_note, bonus = "", 0.0
            if action["side"] == ALLY_SIDE and attacker is not None and attacker is not player:
                # 仲間の武器（窓口 `combat`。333_ が置く）。無ければゲームの素点のまま。
                # プレイヤーの武器はゲームが素点に入れている
                weapon = combat.attack(app, attacker)
                if weapon:
                    ability = ability_of(attacker, action["categories"].get(target))
                    bonus = ally_gear_bonus(ability, weapon)
                    gear_note += " weapon={:g}x{:g}".format(ability or 0, weapon)
            if holder is not player:
                # 仲間の防具（窓口 `combat`）。プレイヤーの防具は本体が渡す値に入っている
                gear = combat.defense(app, holder)
                if gear:
                    defense = gear_defense(defense, gear)
                    gear_note += " armor={:g}".format(gear)
            enemy = action["side"] == ENEMY_SIDE
            final = hit_damage(entries, attack, defense, defender_max,
                               out_mult=out_mult, in_mult=in_mult,
                               attacker_level=attacker_level,
                               defender_level=defender_level,
                               enemy=enemy, bonus=bonus)
            guard_turn = state["guard_turn"] is not None and attacker is state["guard_turn"]
            if guard_turn:
                final = 0
                gear_note += " GUARD-TURN"
            roll = None if guard_turn else evasion_for(attacker, defender_name)
            dodge = evasion_outcome(roll) if roll else None
            if dodge == "evade":
                final = 0
                state["notes"].append("（{}は攻撃をかわした）".format(defender_name))
                gear_note += " EVADED"
            elif dodge == "graze":
                final = max(1, int(round(final * GRAZE_KEEP)))
                state["notes"].append("（{}は攻撃を見切り、傷を浅くした）".format(defender_name))
                gear_note += " grazed"
            if enemy:
                # 敵の手の味方側の被弾。かわしたかを揺れの判断に使う（`shake_window` の包み）
                action.setdefault("ally_hits", []).append(dodge == "evade")
            write("hit: {} -> {} {} raw={:g} def={:g} vanilla={} lv={}->{} "
                  "final={} ({:.0%} of {}){}{}{}".format(
                      attacker_name, defender_name,
                      "+".join("{}x{}".format(p, m) for p, m in entries),
                      attack, _number(defense, 0.0), result,
                      attacker_level if attacker_level is not None else "?",
                      defender_level if defender_level is not None else "?",
                      final, final / defender_max, int(defender_max),
                      " enemy x{}%".format(ENEMY_DAMAGE) if enemy else "",
                      (" guard" if guard else "") + gear_note,
                      "" if out_mult == 1.0 and in_mult == 1.0 else
                      " mults=({:.2f},{:.2f})".format(out_mult, in_mult)))
            return final
        except Exception:
            ctx.log_exc("battle tactics: compression failed; vanilla damage")
            return result

    # ================================================================ 復元
    @ctx.wrap("__main__:BattlePhaseManager.convert_llm_output_to_instruction_dict",
              required=False, safe=True)
    def convert_llm_output_to_instruction_dict(orig, self, actor=None, skill=None,
                                               referee_response=None,
                                               *args, **kwargs):
        result = orig(self, actor, skill, referee_response, *args, **kwargs)
        if not RESTORE_EFFECTS:
            return result
        try:
            app = getattr(self, "app", None) or ui.find_app()
            extras = extract_extras(referee_response) + skill_extras(
                frames.attr(actor, "skills", None), referee_response)
            for extra in extras:
                if extra["kind"] == "status":
                    if not extra["per_turn"] or not extra["status_name"]:
                        continue
                    for target in extra["targets"]:
                        for holder in holders_named(app, target):
                            owner = recipe_owner(app, holder)
                            if owner is None:
                                continue
                            state["recipes"][(owner, extra["status_name"])] = {
                                "per_turn": extra["per_turn"],
                                "intensity": extra["intensity"]}
                            write("restored per-turn effects: {!r} on {} {}x{}"
                                  .format(extra["status_name"], name_of(holder, target),
                                          extra["per_turn"], extra["intensity"]))
                elif extra["kind"] == "attribute":
                    recipe = attribute_recipe(extra["type"],
                                              extra["attribute_type"],
                                              extra["power"])
                    if recipe is None:
                        continue
                    name, description, book = recipe
                    for target in extra["targets"]:
                        for holder in holders_named(app, target):
                            owner = recipe_owner(app, holder)
                            if owner is None or not add_status(
                                    holder, name, description,
                                    extra.get("duration") or ATTR_DURATION):
                                continue
                            state["recipes"][(owner, name)] = book
                            # 画面の行は出さない。付与は `308_` が status の差で
                            # 「名前(効果) が付いた」と出す（審判由来と同じ扱い）。
                            write("restored attribute effect: {!r} on {} ({})"
                                  .format(name, name_of(holder, target), book))
        except Exception:
            ctx.log_exc("battle tactics: cannot restore the referee's effects")
        return result

    @ctx.wrap("__main__:BattlePhaseManager.resolve_battle_effect",
              required=False, safe=True)
    def resolve_battle_effect(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if RESTORE_EFFECTS:
            try:
                describe_restored_statuses(getattr(self, "app", None) or ui.find_app())
            except Exception:
                ctx.log_exc("battle tactics: cannot describe the restored statuses")
        return result

    def shake_verdict():
        """画面の揺れを止めるか。(止めるか, どこで判断したか)。

        敵の手で味方側の被弾が全部かわした回だけ止める。見切り（傷あり）と被弾は揺らす。
        ゲームがどの時点で揺らすかは測っていないので、1手の中と、閉じた直後
        （`SHAKE_AFTER_ACTION` 秒）の両方を見る。
        """
        action = state["action"]
        if action is not None:
            hits = action.get("ally_hits")
            if not hits:
                return False, "in the action, no ally hit yet"
            return all(hits), "in the action"
        last = state["last_ally_hits"]
        if last is not None and time.monotonic() - last["at"] <= SHAKE_AFTER_ACTION:
            return last["evaded"], "just after the action"
        return False, "no recent ally hit"

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.shake_window",
              required=False, safe=True)
    def shake_window(orig, self, *args, **kwargs):
        """回避した手では画面を揺らさない（攻撃が当たっていないので）。"""
        try:
            skip, where = shake_verdict() if EVASION else (False, "evasion off")
            write("shake: {} ({}) from {}".format(
                "skipped, all evaded" if skip else "passed", where, frames.caller(depth=6)))
            if skip:
                return None
        except Exception:
            ctx.log_exc("battle tactics: cannot judge the shake")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:BattlePhaseManager.reduce_status_turns_and_log",
              required=False, safe=True)
    def reduce_status_turns_and_log(orig, self, character=None, *args, **kwargs):
        # 元の減算より先に適用する。
        # 内側（先に適用済み）の `308_` がこの回の報告で数字を出してくれる。
        if RESTORE_EFFECTS and character is not None:
            try:
                max_hp = max_hp_of(character)
                name = name_of(character)
                app = getattr(self, "app", None) or ui.find_app()
                for (_owner, status_name), recipe in active_recipes(app, character):
                    per_turn = recipe.get("per_turn")
                    if not per_turn or max_hp is None:
                        continue
                    for tick_kind, power in per_turn:
                        amount = per_turn_amount(max_hp, power,
                                                 recipe.get("intensity", 3))
                        hp = frames.attr(character, "current_hp", None)
                        if not isinstance(hp, (int, float)):
                            continue
                        if tick_kind == "instant_damage":
                            # 継続ダメージでは死なない。HP 1 で止める。
                            new_hp = max(1, hp - amount)
                        else:
                            # `max_hp_of` は float を返すので、上限に当たった回に
                            # `1560.0` のような float が HUD とセーブへ漏れる。整数に戻す。
                            new_hp = int(min(max_hp, hp + amount))
                        if new_hp != hp:
                            # 出どころを控えてから動かす。
                            # 画面の行（308_）が「泥の浸食 で 29 の
                            # ダメージ」と言えるように（ui.note_damage）。
                            try:
                                ui.note_damage(name, abs(new_hp - hp),
                                               status_name)
                            except Exception:
                                pass
                            character.current_hp = new_hp
                            write("per-turn {}: {!r} on {} {} -> {}".format(
                                tick_kind, status_name, name, hp, new_hp))
            except Exception:
                ctx.log_exc("battle tactics: per-turn effects failed")
        return orig(self, character, *args, **kwargs)

    # ================================================================ 防御
    def begin_guard(app, holder, source):
        name = name_of(holder)
        fresh = state["guards"].get(name) is None
        # 既に構えていても未武装で立て直す。
        # 連続で防御した場合、前の構えは自分の手番（今の手）で消えるのが筋で、
        # この手の構えが改めて次の手まで生きる。
        state["guards"][name] = {"armed": False}
        if not fresh:
            write("guard refreshed: {} ({})".format(name, source))
            return
        add_status(holder, GUARD_STATUS_NAME, GUARD_STATUS_DESCRIPTION, 1)
        write("guard begins: {} ({})".format(name, source))
        say(app, "（{}は防御の構えを取った。次の行動まで受けるダメージが減る）"
            .format(name))

    def watch_referee(target, actor_index, command_index=None, name_index=None):
        """審判の呼び出しを包む。呼ぶ前に回避を振って頼み文へ足し、自由入力なら防御の言葉を拾う。

        引数は名前を付け替えずにそのまま渡す（キーワードで呼ばれても二重にならない）。
        頼み文の目印は、NPC なら「である'<actor_name>'の行動ターン」、主人公なら
        「今、プレイヤーである<名前>は」（`output_data` の実記録の文面）。
        """
        def pick(args, kwargs, index, name):
            return args[index] if len(args) > index else kwargs.get(name)

        @ctx.wrap("scripts.llm.llm_manager_battle:{}".format(target),
                  required=False, safe=True)
        def referee(orig, *args, **kwargs):
            actor = pick(args, kwargs, actor_index, "actor")
            if name_index is not None:
                marker = NPC_TURN_MARKER.format(pick(args, kwargs, name_index, "actor_name"))
            else:
                marker = PLAYER_TURN_MARKER.format(name_of(actor))
            try:
                prepare_evasion(actor, marker)
            except Exception:
                ctx.log_exc("battle tactics: cannot roll the evasion")
            try:
                result = orig(*args, **kwargs)
            finally:
                state["hints"].pop(marker, None)
            if command_index is not None:
                command = pick(args, kwargs, command_index, "command")
                if isinstance(command, str) and command.strip() == GUARD_COMMAND and actor is not None:
                    state["guard_turn"] = actor
                try:
                    if actor is not None and isinstance(command, str) \
                            and any(word in command for word in GUARD_WORDS):
                        begin_guard(ui.find_app(), actor, "free input")
                except Exception:
                    ctx.log_exc("battle tactics: cannot read the free input")
            return result
        return referee

    # 審判の入口（GAME.md §2.10.1）。仲間と敵は referee_npc、主人公は通常攻撃・スキル・自由入力
    watch_referee("referee_npc", 3, name_index=2)
    watch_referee("referee_player_attack_new_new", 1)
    watch_referee("referee_player_skill_new_new", 1)
    watch_referee("referee_player_any_input_new_new", 1, command_index=2)
    watch_referee("referee_player_any_input_new_new_with_skill", 1, command_index=2)

    # ---------------------------------------------------------------- ボタン
    def in_battle_screen(app):
        enemies = enemy_dict(app)
        return bool(getattr(app, "in_battle", 0)) and bool(enemies)

    # スキル一覧を組んでいる最中の印。
    # `display_skill_choices` が立て、何かボタンが押されたら降ろす。
    # 一覧の組み立ての中でゲームが `refresh_choice_buttons` を呼ぶので、
    # 差し込みは下の refresh の包みの側でやる（`app.buttons` を書いて
    # 自分で refresh を呼び直しても画面は塗り替わらない。GAME.md §2.3）。
    @ctx.wrap("__main__:SkillChoicePhaseManager.display_skill_choices",
              required=False, safe=True)
    def display_skill_choices(orig, self, *args, **kwargs):
        state["skill_screen"] = True
        state["guard_pending"] = False        # 新しいスキル一覧 ＝ 次の手番。防御をまた押せる
        return orig(self, *args, **kwargs)

    def refresh_choice_buttons(self, _buttons):
        if GUARD_BUTTON:
            try:
                buttons = getattr(self, "buttons", None)
                if isinstance(buttons, list):
                    screen.prune_stale(buttons, (GUARD_BUTTON_LABEL,))
                    if not in_battle_screen(self):
                        state["skill_screen"] = False
                        state["input_spec_logged"] = False
                    else:
                        # 戦闘画面: スキルのボタンを「スキル・防御」に改名する
                        # だけで、ボタンは足さない（5つ目は画面右へあふれて
                        # 右側の UI と重なる。実機）。
                        skill_entry = ui.find_spec_button(buttons,
                                                          SKILL_MANAGER_CLS)
                        if skill_entry is not None \
                                and skill_entry.get("text") != SKILL_MENU_LABEL:
                            skill_entry["text"] = SKILL_MENU_LABEL
                        # スキル一覧: 「やめる」の手前に防御を入れる
                        # （スキル1・（スキル2）・防御・やめる の並び）。
                        # 「やめる」が見つからなければ末尾。
                        if state["skill_screen"] \
                                and not any(screen.mark_of(entry)
                                            for entry in buttons):
                            entry = screen.button(GUARD_BUTTON_LABEL,
                                                  mark="guard")
                            if entry is not None:
                                at = len(buttons)
                                for index, existing in enumerate(buttons):
                                    if ui.spec_cls_name(existing) \
                                            == CANCEL_MANAGER_CLS:
                                        at = index
                                        break
                                buttons.insert(at, entry)
                                write("guard into the skill list at {}: {}"
                                      .format(at,
                                              [(e.get("text"),
                                                ui.spec_cls_name(e))
                                               for e in buttons
                                               if isinstance(e, dict)]))
                            if not state["input_spec_logged"]:
                                # 自由入力の流し先を1戦闘1回だけ写す。
                                # ボタンが1手を消費できない画面が来たとき、
                                # この行が原因の切り分けになる。
                                state["input_spec_logged"] = True
                                spec = getattr(self,
                                               "function_correspond_to_input",
                                               None)
                                write("battle input spec: {} {}".format(
                                    ui.spec_cls_name({"spec": spec}),
                                    ui.spec_args({"spec": spec})))
            except Exception:
                ctx.log_exc("battle tactics: cannot place the guard button")

    def look_at_press(self, entry, _button_index):
        """どのボタンが押されても先に通る（窓口 `choices` の `intercept`）。押下は握らない。"""
        # どのボタンでも、押した時点でスキル一覧の場面は終わる
        # （スキルを選んだ・防御した・やめた、のどれでも）。
        state["skill_screen"] = False
        return False

    def on_button_press(self, action):
        """自前のボタンが押された（窓口 `choices` の `presses`）。"""
        if state["guard_pending"]:
            # 前の押下の手番がまだ動いている。ゲームのボタンは本体が連打を止めるが、
            # 防御は本体を通さないので自前で捨てる（0.18 秒差の2発で1巡が丸ごと2回走り、
            # 並んだ2つの手番が1手の控えを奪い合った。実機）
            write("guard button ignored: the previous press is still running")
            return None
        state["guard_pending"] = True
        write("guard button pressed")
        player = getattr(self, "player", None)
        try:
            spec = getattr(self, "function_correspond_to_input", None)
            cls_name = ui.spec_cls_name({"spec": spec})
            if cls_name == "BattlePhaseManager":
                # 自由入力と同じ経路に防御の文を流す ＝ 1手をちゃんと消費する。
                # 構えは審判を待たずに立てる（言葉の検出はこの文にも効くが、
                # `begin_guard` は二重には積まない）。
                if player is not None:
                    begin_guard(self, player, "guard button")
                manager = getattr(ui.main_module(), cls_name)(
                    self, *(ui.spec_args({"spec": spec}) or []))
                self.process_choice(manager, GUARD_COMMAND)
            else:
                # 流し先が読めない画面。構えだけ立てて、手番は消費しない。
                # ログの `battle input spec:` 行が次の手掛かり。
                if player is not None:
                    begin_guard(self, player, "guard button (no turn)")
                write("guard button: input spec is {!r}; the turn was not spent"
                      .format(cls_name))
        except Exception:
            ctx.log_exc("battle tactics: the guard button failed")
        return None

    # 選択肢と押下はローダの窓口 `choices`（TECH.md §3.3.14）に預ける。
    choices.provide(ctx, screen, refresh=refresh_choice_buttons, presses={"guard": on_button_press}, intercept=look_at_press)

    # ---------------------------------------------------------------- 節目
    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            state["guards"] = {}
            state["input_spec_logged"] = False
            state["skill_screen"] = False
            state["guard_pending"] = False
            state["guard_turn"] = None
            # 前の戦闘の敵の帳簿を落とす（敵は戦闘ごとに別の個体で、消えた個体の id は使い回されうる）。
            # いま居る味方（プレイヤー・仲間の残留状態異常）の分は残す。
            alive = set(owner_key(side, name, holder)
                        for side, _k, name, holder in combatants(app) if side == ALLY_SIDE)
            for key in [k for k in state["recipes"] if k[0] not in alive]:
                del state["recipes"][key]
        except Exception:
            ctx.log_exc("battle tactics: cannot reset at the battle start")
        try:
            stretch_enemy_hp(getattr(self, "app", None) or ui.find_app())
        except Exception:
            ctx.log_exc("battle tactics: cannot stretch the enemies' HP")
        return result

    def stretch_enemy_hp(app):
        """敵の HP を「敵の体力」の率で伸ばす。HP は3つ組（current / max / original_max）で動かす。

        掛けるのはゲームの式どおりの HP だけ（`scaled_hp`）なので、戦闘中のセーブから
        読み直した敵には二度掛けしない。敵は戦闘の終わりに一覧ごと消える。
        """
        for key, holder in list((enemy_dict(app) or {}).items()):
            scores = frames.attr(holder, "ability_scores", None)
            constitution = scores.get("constitution") if isinstance(scores, dict) else None
            level = frames.attr(holder, "experience_level", None)
            original = frames.attr(holder, "original_max_hp", None)
            reference = original if isinstance(original, (int, float)) else frames.attr(holder, "max_hp", None)
            target = scaled_hp(reference, constitution, level)
            if target is None:
                continue
            ratio = target / float(reference)
            for field in ("original_max_hp", "max_hp", "current_hp"):
                value = frames.attr(holder, field, None)
                if isinstance(value, (int, float)):
                    setattr(holder, field, max(1, int(round(value * ratio))))
            write("enemy hp: {} {} -> {} (x{}%)".format(
                name_of(holder, key), int(reference), frames.attr(holder, "max_hp", "?"), ENEMY_HP))

    # ================================================================ 通常攻撃の頼み
    def rewrite_outgoing(texts, site):
        result = list(texts)
        if STEADY_BASIC_ATTACK:
            result = [steady_basic_attack(t) for t in result]
            if result != list(texts):
                write("basic attack: asked the referee to weigh the blow by modifications ({})".format(site))
        # 回避の判定は、その手番の者の審判の送信にだけ足す（末尾の本文へ。再送で二重にしない）
        for marker, hint in list(state["hints"].items()):
            if result and any(marker in text for text in result) and hint not in result[-1]:
                result[-1] = result[-1] + "\n\n" + hint
                write("evasion: told the referee ({}, {})".format(marker, site))
        return None if result == list(texts) else result

    llm.wrap_outgoing(ctx, rewrite_outgoing, label=LOG_TAG)

    ctx.log("battle tactics: enemy_hp={}% enemy_damage={}% level={}/{}/{} x{}%/x{}% "
            "guard_cut={}% ally_gear={}% restore={} button={} steady_attack={} "
            "evasion={}/{} {}%/pt cap={}% {}%/lv uphill={} (log -> {})".format(
                ENEMY_HP, ENEMY_DAMAGE, LEVEL_FAIR_GAP, LEVEL_ELITE_GAP, LEVEL_OUTCLASS_GAP,
                LEVEL_ELITE_MULT, LEVEL_OUTCLASS_MULT, GUARD_CUT, ALLY_GEAR_PERCENT,
                RESTORE_EFFECTS, GUARD_BUTTON, STEADY_BASIC_ATTACK,
                EVASION, ENEMY_EVASION, EVASION_PER_POINT, EVASION_STAT_CAP, EVASION_PER_LEVEL,
                EVASION_UPHILL, ctx.out_path(LOG_BASENAME)))
