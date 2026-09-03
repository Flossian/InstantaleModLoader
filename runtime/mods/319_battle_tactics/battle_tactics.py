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

## 1. ダメージの圧縮（引き算 → 相手の最大HPの割合）

`resolve_*` が呼ぶ `get_instant_damage(素点, 防御)` を包み、
1手の文脈（誰が誰へ・power・倍率）が揃っているときだけ結果を置き換える:

    割合 = Σ band(power) × multiplier      その手の各エントリの和
         × 格差の係数                      下記。[0.4, 16] に収める
         × レベル差の倍率                  下記。高い側が伸び、低い側は割られる
         × (1 − 軽減)                      軽減 = 防御 ÷ (防御 + PIVOT)、上限 60%
         × 防御の構え・状態異常の倍率
    最終 = 受け側 max_hp × 割合            下限 1%。上限は互角なら MAX_FRACTION%、
                                           格差が開くほどゆるみ、圧倒的なら一撃

格差の係数は、その手の**基礎値**（2×√(能力側×武器攻撃力)。ゲーム自身の
`get_base_damage_value` が同じ手の中で計算する値を控えて使う）が取れたら

    (基礎値 ÷ (受け側 max_hp × 0.5))^(WEAPON_IMPACT÷100)    [0.4, 16] に留める

＝ **武器と能力の伸びがそのまま与ダメの伸びになる**。
互角の戦い（係数 3.5 まで）では1発の上限 MAX_FRACTION が効き、
係数がそれを超えると上限がゆるんで 6.0 で完全に外れる（`oneshot_cap`）。
**圧倒的な格差だけが一撃を出せる**（最強の英雄 対 初期村の魔物、の側も
魔王 対 駆け出し、の側も）。互角の殴り合いに一撃は無い。
一撃の域では**素の計算値（素点 − 防御）をそのまま出す**（`overkill_final`）。
割合のままだと相手の最大HPきっかりの数字しか出ず、
強化した火力が見えない（実機の指摘）。相手はどのみち落ちる域なので、
素の大きさで過剰殺傷を表現しても釣り合いは壊れない。
攻撃面の成長は実質武器だけなので（GAME.md §2.17）、ここを切ると
店とクラフトの遊びが死ぬ。
傾きが設定なのは、基礎値そのものが √武器 でしか伸びないから。
指数 0.5 だと武器 23 → 500 の全幅でも与ダメ 2.2倍と平坦で、強化の実感が薄い
（初版で踏んだ）。既定 0.75 の手数は下の表、1.0 に上げると
武器差はさらに急になり最強武器は格下を2手で落とす。
受け側 max_hp を物差しにするのは、ゲーム自身が難易度で HP と武器の値段を
一緒に伸ばしているから（GAME.md §2.13.1 / §2.20）。
**適正装備で適正な相手なら係数 ≈ 1** になり、
武器を更新すれば同じ相手が柔らかくなり、格上の土地へ行けばまた硬くなる。

基礎値が取れない手は max_hp の比 `(攻撃側 ÷ 受け側)^0.7` で代用する。
`get_base_damage_value` を通るのは**プレイヤーの手だけ**で、
敵も同行の仲間も通らない（実測 §2.68 / §2.69）。
**仲間の代用だけは 1.0 で底上げする**（`ally_floor`）。
仲間は max_hp が低いことが多く（実測で 132 の仲間が居た）、比のままだと係数 0.4 に
張り付いて無力に見える。その場に立てている以上、敵と対等の一員として数え、
仲間の成長はレベル差の倍率の側で受ける（レベルは `306_` の訓練で上がる）。
仲間に武器が乗らないのは**素のゲームの仕様**
（NPC は武器を参照しない旨の公式回答。GAME.md §2.10.2）。
この MOD もそれに合わせ、仲間の強さは本人のレベルと立ち位置で見る。

レベル差は格差の係数とは独立の倍率。
差が `LEVEL_FAIR_GAP`（既定10）までは適正で倍率なし、
`LEVEL_ELITE_GAP`（15）で強敵の域（既定 ×2）、
`LEVEL_OUTCLASS_GAP`（20）以上は格が違う（既定 ×3 ＋一撃の解禁）。
間は直線でつなぎ、低い側から高い側への手は同じ率で割られる
（＝格上は痛く硬く、格下は柔らかく無害に、が1つの倍率で両方出る）。

実測の帯（エリス、normal どうし）の手数。武器の一段が同格相手で 2〜6手動く:

    武器の攻撃力    格下460   同格1560   HP2倍ボス3120
    500 (基礎883)     3手       8手        14手
    245 (基礎618)     4手      10手        18手
     96 (基礎387)     5手      14手        22手
     23 (基礎189)     9手      20手        22手

ボスの extreme は4割を持っていく（防御を固める理由が生まれる）。

文脈が揃わない呼び出し（1手の外・照合できない素点）は**素通し**する。
効かなかったことはログに残る。

## 2. 捨てられた効果の復元

変換を包んで、審判の生の戻りから落ちる前の
`TextStatusEffect`（`intensity`・`effects_per_turn`）と `AttributeEffect` を控える。

  * 毎ターンの継続ダメージ・回復: `reduce_status_turns_and_log` の直前に
    最大HP比で適用する（弱3% 〜 極18%、`intensity` で伸縮）。
    **継続ダメージでは死なない**（HP 1 で止める。倒すのは手番の側の仕事）
  * `AttributeEffect`: 対象の `status` へ普通の状態異常として書き
    （筋力低下 など、期限つき）、その間の与ダメ・被ダメに倍率を掛ける。
    筋・敏・知・魅 は与える側、耐・賢 は受ける側

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

## 触らないもの

即時回復・逃走・`physical_integrity`・経験値・戦利品はゲームのまま。
セーブに MOD 独自の鍵は増やさない。
"""

from instantale_modloader import frames, ui

LOG_BASENAME = "battle_tactics.log"
LOG_TAG = "battle tactics"

# ボタン辞書に付ける印のキー。mod ごとに別の文字列にする（TECH.md §3.3）。
MARK = "mod_battle_tactics"

ENEMY_SIDE = "enemy"
ALLY_SIDE = "ally"

# ---------------------------------------------------------------- 設定
# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。

# power 1段ごとの基準割合（受け側の最大HPに対する %）。
BAND_WEAK = 10
BAND_NORMAL = 16
BAND_STRONG = 25
BAND_VERY_STRONG = 38
BAND_EXTREME = 55

# 互角の戦いで1発が持っていける割合の上限（%）。
# extreme+強化が重なっても、互角の相手からの即死は無い。
# 格差の係数が ONESHOT_START を超えるとこの上限はゆるむ（`oneshot_cap`）。
MAX_FRACTION = 65

# 防御の効き。軽減 = 防御 ÷ (防御 + この値)。
# 大きいほど防御が効かなくなる。実測の帯（防具500・敵防御100前後）で
# 800 なら 38% / 11% になる。
MITIGATION_PIVOT = 800

# 防御の構えの軽減（%）。次の自分の手番までの被ダメに掛かる。
GUARD_CUT = 50

# レベル差の段。攻める側が受け側より高いとき、差に応じて与ダメに倍率が乗る
# （逆向きは同じ倍率で割る ＝ 格上からの被ダメ増・格下への被ダメ減が同時に出る）。
#     差 ≤ FAIR         適正。倍率なし
#     差 = ELITE        強敵の域。LEVEL_ELITE_MULT（既定 ×2）
#     差 ≥ OUTCLASS     格が違う。LEVEL_OUTCLASS_MULT（既定 ×3）＋一撃の解禁
# 間は直線でつなぐ（1レベルで挙動が跳ねる崖を作らない）。
LEVEL_FAIR_GAP = 10
LEVEL_ELITE_GAP = 15
LEVEL_OUTCLASS_GAP = 20
LEVEL_ELITE_MULT = 200
LEVEL_OUTCLASS_MULT = 300

# 武器の効きの強さ（%）。格差の係数の指数 = この値 ÷ 100。
# 75 で、武器のランクが一段上がるごとに同格相手の手数が目に見えて減る
# （実測の武器帯で 20 → 14 → 10 → 8手）。
# 100 に上げると武器差はさらに急になり、最強武器は格下を2手で落とす。
WEAPON_IMPACT = 75

# 審判が付けた状態異常の中身（毎ターンの効果・能力の増減）を復元して効かせる。
RESTORE_EFFECTS = True

# 戦闘の選択肢に「防御」ボタンを足す。
GUARD_BUTTON = True

# ---------------------------------------------------------------- 定数
# 格差の係数の留め。
# 下は 0.4 で止める（どんな格上相手でも削れなくはならない）。
# 上は 16 まで伸ばしてある。互角の戦いでは係数はそもそも 1 前後にしかならず、
# ここまで開くのは「最強の英雄 対 初期村の魔物」級の圧倒的な格差だけ。
STRENGTH_MIN = 0.4
STRENGTH_MAX = 16.0

# 一撃の解禁。1発の上限（MAX_FRACTION）は互角の戦いを守るためのもので、
# 格差の係数が ONESHOT_START を超えると上限がゆるみ始め、
# ONESHOT_FULL で完全に外れる（＝満タンからの一撃が出る）。
# 最強の英雄が初期村の魔物を一撃で払えないのは不自然だし、
# 魔王の一撃が駆け出しを即死させても違和感は無い。互角の相手には出ない。
ONESHOT_START = 3.5
ONESHOT_FULL = 6.0

# 基礎値（2×√(能力×武器)）から見る格差。
# 係数 = (基礎値 ÷ (受け側 max_hp × GEAR_PIVOT))^(WEAPON_IMPACT÷100)。
# GEAR_PIVOT 0.5 は「基礎値 ≈ 受け側 max_hp の半分」を等倍とする物差しで、
# 実測の帯（基礎値883・同格 HP 1560）がほぼ係数1に乗る。
GEAR_PIVOT = 0.5

# 基礎値が取れない手（敵の攻撃）の代用: max_hp の比のカーブ。
STRENGTH_EXPONENT = 0.7

# 軽減の上限。防御をどれだけ積んでも被ダメは4割残る。
MITIGATION_MAX = 0.60

# 1発の下限割合。「1点」だけが延々続く状態を作らない。
MIN_FRACTION = 0.01

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


# ================================================================ 純関数
# 数の芯はモジュール直下に置く。オフラインの検査（tools/tests/）から直接叩ける。

def band_of(power):
    """power の列挙 → 基準割合。知らない語は normal 扱い（黙って 0 にしない）。"""
    table = {"weak": BAND_WEAK, "normal": BAND_NORMAL, "strong": BAND_STRONG,
             "very_strong": BAND_VERY_STRONG, "extreme": BAND_EXTREME}
    return table.get(power, BAND_NORMAL) / 100.0


def strength_ratio(attacker_max_hp, defender_max_hp):
    """格差の係数（代用側）。max_hp の比を緩めて [MIN, MAX] に収める。"""
    try:
        ratio = float(attacker_max_hp) / float(defender_max_hp)
    except Exception:
        return 1.0
    if ratio <= 0:
        return 1.0
    return max(STRENGTH_MIN, min(STRENGTH_MAX, ratio ** STRENGTH_EXPONENT))


def gear_factor(base_value, defender_max_hp):
    """格差の係数（本命側）。武器と能力の伸びを与ダメに戻す項。

    `base_value` はその手でゲーム自身が計算した基礎値（2×√(能力×武器)）。
    受け側 max_hp を物差しに取り、[STRENGTH_MIN, GEAR_MAX] に収める。
    傾きは `WEAPON_IMPACT`（設定）。読めない値なら 1.0（係数で事故らせない）。
    """
    try:
        ratio = float(base_value) / (float(defender_max_hp) * GEAR_PIVOT)
    except Exception:
        return 1.0
    if ratio <= 0:
        return 1.0
    exponent = max(1, WEAPON_IMPACT) / 100.0
    return max(STRENGTH_MIN, min(STRENGTH_MAX, ratio ** exponent))


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
    """レベル差の倍率。高い側の与ダメに掛かり、低い側の与ダメは同じ率で割る。

    FAIR まで 1.0、ELITE で LEVEL_ELITE_MULT、OUTCLASS で LEVEL_OUTCLASS_MULT。
    間は直線。負の差は逆数（格上へ挑む側は同じ段だけ通りが悪くなる）。
    """
    magnitude = abs(gap)
    if magnitude <= LEVEL_FAIR_GAP:
        return 1.0
    elite = max(1.0, LEVEL_ELITE_MULT / 100.0)
    outclass = max(elite, LEVEL_OUTCLASS_MULT / 100.0)
    if magnitude <= LEVEL_ELITE_GAP:
        mult = _ramp(magnitude, LEVEL_FAIR_GAP, LEVEL_ELITE_GAP, 1.0, elite)
    else:
        mult = _ramp(magnitude, LEVEL_ELITE_GAP, LEVEL_OUTCLASS_GAP,
                     elite, outclass)
    return mult if gap >= 0 else 1.0 / mult


def oneshot_cap(factor, gap=0):
    """その格差での1発の上限（割合）。

    互角〜そこそこの格差では `MAX_FRACTION`。
    格差の係数（ONESHOT_START〜ONESHOT_FULL）か、攻める側が上のレベル差
    （ELITE〜OUTCLASS）のどちらかが開くと上限がゆるみ、振り切れば 1.0
    （＝満タンからの一撃）。圧倒的な格差だけが一撃を出せて、互角では出ない。
    """
    top = MAX_FRACTION / 100.0
    if top >= 1.0:
        return top
    by_factor = _ramp(factor, ONESHOT_START, ONESHOT_FULL, top, 1.0)
    by_level = _ramp(gap, LEVEL_ELITE_GAP, LEVEL_OUTCLASS_GAP, top, 1.0)
    return max(by_factor, by_level)


def mitigation(defense):
    """防御 → 軽減率 [0, MITIGATION_MAX]。引き算ではなく飽和曲線。"""
    try:
        defense = float(defense)
    except Exception:
        return 0.0
    if defense <= 0:
        return 0.0
    return min(MITIGATION_MAX, defense / (defense + float(MITIGATION_PIVOT)))


def hit_fraction(entries, attacker_max_hp, defender_max_hp, defense,
                 out_mult=1.0, in_mult=1.0, base_value=None,
                 attacker_level=None, defender_level=None, ally_floor=False,
                 with_raw=False):
    """1発が受け側の最大HPから持っていく割合。

    `entries` はその対象への `[(power, multiplier), ...]`
    （1手で同じ相手に複数のエントリが乗ることがある。実測 §2.68）。
    `base_value` はその手の基礎値（2×√(能力×武器)）。
    取れた手は武器と能力が格差の係数として効き、
    取れない手（敵と仲間。§2.69）は max_hp の比で代用する。
    `ally_floor` は仲間の代用に限って係数を 1.0 で底上げする印
    （仲間は max_hp が低くても「その場に立てている」以上、
    敵と対等の一員として数える。武器が乗らないぶんの弱体化をここで受け止める）。
    レベル差は独立の倍率で、高い側の与ダメが伸び、低い側は同じ率で割られる。
    """
    band_sum = 0.0
    for power, multiplier in entries:
        try:
            multiplier = float(multiplier)
        except Exception:
            multiplier = 1.0
        band_sum += band_of(power) * multiplier
    if base_value is not None:
        factor = gear_factor(base_value, defender_max_hp)
    else:
        factor = strength_ratio(attacker_max_hp, defender_max_hp)
        if ally_floor:
            factor = max(1.0, factor)
    gap = level_gap(attacker_level, defender_level)
    raw = (band_sum * factor * level_multiplier(gap)
           * (1.0 - mitigation(defense))
           * out_mult * in_mult)
    fraction = max(MIN_FRACTION, min(oneshot_cap(factor, gap), raw))
    if with_raw:
        return fraction, raw
    return fraction


def per_turn_amount(max_hp, power, intensity):
    """毎ターンの継続効果の点数。intensity 3 を等倍に、1〜5 で 2/3〜4/3 倍。"""
    band = PER_TURN_BANDS.get(power, PER_TURN_BANDS["normal"]) / 100.0
    try:
        scale = (2.0 + max(1, min(5, int(intensity)))) / 5.0
    except Exception:
        scale = 1.0
    return max(1, int(round(max_hp * band * scale)))


def overkill_final(fraction, raw_fraction, defender_max_hp, compressed):
    """一撃の域（割合が 1.0 に達した）は、同じ式の上限を外した値で表現する。

    割合で削ると、どれだけ強くても「相手の最大HPきっかり」までしか数字が
    出ず、強化したキャラで格下を払う爽快感が消える（実機の指摘）。
    かといって素の計算値（素点 − 防御）を借りると、普段 200〜300 の画面に
    突然 1000 超が出て**物差しが途切れる**（これも実機の指摘。版6の誤り）。
    同じ圧縮式の丸める前の値なら、数字は普段の帯と地続きのまま
    相手の HP を大きく超える ― 例: 英雄(基礎896) → スライム(HP64) の normal は
    割合 5.85 → 374 のダメージ（撃破）。過剰殺傷が見え、武器とレベルで伸びる。
    """
    if fraction >= 1.0 and isinstance(raw_fraction, (int, float)) \
            and raw_fraction > 1.0:
        try:
            return max(compressed, int(round(defender_max_hp * raw_fraction)))
        except Exception:
            return compressed
    return compressed


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
        note = "与えるダメージが{}がる".format("上" if helpful else "下")
        book = {"out_mult": mult}
    else:
        mult = 1.0 - amount if helpful else 1.0 + amount
        note = "受けるダメージが{}える".format("減" if helpful else "増")
        book = {"in_mult": mult}
    description = "{}が{}している（{}）。".format(
        label, "向上" if helpful else "低下", note)
    return name, description, book


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag=LOG_TAG + ":")
    warn = ctx.warner(LOG_TAG)

    state = {
        # 開いている1手。{"actor_key","side","attacker","entries","plan"}。
        "action": None,
        # 防御の構え。{表示名: True}。自分の手番が来たら消える。
        "guards": {},
        # 復元した効果の帳簿。{(表示名, 状態異常名): recipe}
        "recipes": {},
        # get_npc_defense の直近の呼び出し（防御値 → 持ち主の照合用）。
        "last_defense": None,
        # 防御ボタンで流す先の記録（画面の spec を1戦闘1回だけ写す）。
        "input_spec_logged": False,
        # スキル一覧を組んでいる最中か（防御を差し込む場面の印）。
        "skill_screen": False,
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

    def status_mults(name):
        """表示名 `name` の者にいま乗っている倍率（与える側, 受ける側）。"""
        out_mult, in_mult = 1.0, 1.0
        for (target, _status), recipe in state["recipes"].items():
            if target != name:
                continue
            out_mult *= recipe.get("out_mult", 1.0)
            in_mult *= recipe.get("in_mult", 1.0)
        return out_mult, in_mult

    def active_recipes(holder):
        """持ち主の status に残っている帳簿だけを返す。消えた分は落とす。"""
        statuses = status_dict(holder)
        if statuses is None:
            return []
        name = name_of(holder)
        found, dead = [], []
        for key, recipe in state["recipes"].items():
            if key[0] != name:
                continue
            if key[1] in statuses:
                found.append((key, recipe))
            else:
                dead.append(key)
        for key in dead:
            del state["recipes"][key]
        return found

    def say(app, text):
        try:
            app.add_text(text)
        except Exception:
            ctx.log_exc("battle tactics: add_text failed")

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
                           "entries": {}, "plan": {}, "base": None}

    def close_action():
        action = state["action"]
        state["action"] = None
        state["last_defense"] = None
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
        entries = {}
        for entry in read_field(battle_action, "instant_damage") or []:
            target = read_field(entry, "target")
            if target is None:
                continue
            entries.setdefault(str(target), []).append(
                (str(read_field(entry, "power", "normal")),
                 read_field(entry, "multiplier", 1)))
        action["entries"] = entries
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
        result = orig(self, character_key, character_side, battle_action,
                      *args, **kwargs)
        close_action()
        return result

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

    # その手の基礎値（2×√(能力×武器)）を控える。
    # 武器と能力の伸びを与ダメに戻す項の材料で、値はゲーム自身の計算をそのまま使う。
    # 敵の攻撃はこの関数を通らない（実測 §2.68）ので、控えが無い手は代用の比になる。
    @ctx.wrap("scripts.functions:get_base_damage_value", required=False, safe=True)
    def get_base_damage_value(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        action = state["action"]
        if action is not None and isinstance(result, (int, float)):
            action["base"] = result
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
            attacker_max = max_hp_of(action["attacker"])
            if defender_max is None:
                return result
            entries = action["entries"].get(target) or [("normal", 1)]
            attacker_name = action["actor_name"]
            defender_name = name_of(holder, target)
            out_mult, _ = status_mults(attacker_name)
            _, in_mult = status_mults(defender_name)
            guard = state["guards"].get(defender_name)
            if guard:
                in_mult *= 1.0 - GUARD_CUT / 100.0
            base_value = action.get("base")
            attacker_level = frames.attr(action["attacker"],
                                         "experience_level", None)
            defender_level = frames.attr(holder, "experience_level", None)
            # 仲間の手は基礎値が取れない（§2.69）。max_hp の代用を 1.0 で
            # 底上げして、HP の低い仲間が無力に見えないようにする。
            ally_floor = (base_value is None and action["side"] == ALLY_SIDE)
            fraction, raw_fraction = hit_fraction(
                entries, attacker_max or defender_max,
                defender_max, defense,
                out_mult=out_mult, in_mult=in_mult,
                base_value=base_value,
                attacker_level=attacker_level,
                defender_level=defender_level,
                ally_floor=ally_floor, with_raw=True)
            final = max(1, int(round(defender_max * fraction)))
            final = overkill_final(fraction, raw_fraction, defender_max, final)
            write("hit: {} -> {} {} raw={} vanilla={} base={} lv={}->{} "
                  "fraction={:.3f} final={}{}{}".format(
                      attacker_name, defender_name,
                      "+".join("{}x{}".format(p, m) for p, m in entries),
                      attack, result,
                      int(base_value) if base_value else "-",
                      attacker_level if attacker_level is not None else "?",
                      defender_level if defender_level is not None else "?",
                      fraction, final,
                      " guard" if guard else "",
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
            for extra in extract_extras(referee_response):
                if extra["kind"] == "status":
                    if not extra["per_turn"] or not extra["status_name"]:
                        continue
                    for target in extra["targets"]:
                        for holder in holders_named(app, target):
                            key = (name_of(holder, target), extra["status_name"])
                            state["recipes"][key] = {
                                "per_turn": extra["per_turn"],
                                "intensity": extra["intensity"]}
                            write("restored per-turn effects: {!r} on {} {}x{}"
                                  .format(extra["status_name"], key[0],
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
                            if not add_status(holder, name, description,
                                              ATTR_DURATION):
                                continue
                            state["recipes"][(name_of(holder, target), name)] = book
                            write("restored attribute effect: {!r} on {} ({})"
                                  .format(name, name_of(holder, target), book))
                            say(app, "（{}: {}）".format(
                                name_of(holder, target), description))
        except Exception:
            ctx.log_exc("battle tactics: cannot restore the referee's effects")
        return result

    @ctx.wrap("__main__:BattlePhaseManager.reduce_status_turns_and_log",
              required=False, safe=True)
    def reduce_status_turns_and_log(orig, self, character=None, *args, **kwargs):
        # 元の減算より先に適用する。
        # 内側（先に適用済み）の `308_` がこの回の報告で数字を出してくれる。
        if RESTORE_EFFECTS and character is not None:
            try:
                max_hp = max_hp_of(character)
                for (name, status_name), recipe in active_recipes(character):
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
                            new_hp = min(max_hp, hp + amount)
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

    def watch_free_input(target):
        @ctx.wrap("scripts.llm.llm_manager_battle:{}".format(target),
                  required=False, safe=True)
        def referee(orig, combat_log=None, actor=None, command=None,
                    *args, **kwargs):
            result = orig(combat_log, actor, command, *args, **kwargs)
            try:
                if actor is not None and isinstance(command, str) \
                        and any(word in command for word in GUARD_WORDS):
                    begin_guard(ui.find_app(), actor, "free input")
            except Exception:
                ctx.log_exc("battle tactics: cannot read the free input")
            return result
        return referee

    watch_free_input("referee_player_any_input_new_new")
    watch_free_input("referee_player_any_input_new_new_with_skill")

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
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
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
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        # どのボタンでも、押した時点でスキル一覧の場面は終わる
        # （スキルを選んだ・防御した・やめた、のどれでも）。
        state["skill_screen"] = False
        entry = ui.pressed_entry(self, button_index)
        if screen.mark_of(entry) != "guard":
            return orig(self, button_index, *args, **kwargs)
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

    # ---------------------------------------------------------------- 節目
    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            state["guards"] = {}
            state["input_spec_logged"] = False
            state["skill_screen"] = False
            # 前の戦闘の敵の帳簿を落とす（同名の敵が別の戦闘で出るため）。
            # いま居る者（プレイヤー・仲間の残留状態異常）の分は残す。
            alive = set(name for _s, _k, name, _h in combatants(app))
            for key in [k for k in state["recipes"] if k[0] not in alive]:
                del state["recipes"][key]
        except Exception:
            ctx.log_exc("battle tactics: cannot reset at the battle start")
        return result

    ctx.log("battle tactics: bands={}/{}/{}/{}/{}% cap={}% pivot={} guard_cut={}% "
            "restore={} button={} (log -> {})".format(
                BAND_WEAK, BAND_NORMAL, BAND_STRONG, BAND_VERY_STRONG,
                BAND_EXTREME, MAX_FRACTION, MITIGATION_PIVOT, GUARD_CUT,
                RESTORE_EFFECTS, GUARD_BUTTON, ctx.out_path(LOG_BASENAME)))
