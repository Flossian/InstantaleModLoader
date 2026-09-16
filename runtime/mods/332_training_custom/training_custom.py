# -*- coding: utf-8 -*-
"""機能調整: 訓練所の代金・修行の年数・1段の期間を設定で変えられるようにする。

素のゲームの訓練所は、代金が 300G で固定、1回の修行が3年、
活動1段で「その年数 × 365 日」の暦が一度に進む（実測は GAME.md §2.17。
録ったのは `231_probe_training`）。
3年の活動を1つ選ぶと **1095 日**が飛ぶので、訓練所は事実上使いどころが無い。
この MOD はその3つを mod.json の設定から変えられるようにする。
既定値はすべて素のゲームの値で、そのままなら挙動は何も変わらない。

変更点は4つ。

| 何を変えるか | どこで変えるか |
|---|---|
| 代金 | `TrainingStartManager.__init__` に渡る `training_price` を差し替える |
| 1回の修行の年数 | 同じ `__init__` の `training_years` を差し替える |
| 1段の期間（ベース期間 × 倍率） | 段の実行中だけ、ローダの日数送りの関所へ「この段は何日か」を出す |
| 年で言う文言 | 画面の文言（`あと3年間`）と、AI へ渡る頼み文の年数（`残り訓練年数: 3年`）を実際の期間へ |
| 卒業した施設での再訓練 | 施設に立つ訓練済みの印（`config["trained"]`）をたたむ。あとはゲームがいつもどおり進める |

留意点:

- **代金と年数はゲームが自分で受け取る引数を差し替える。**
  ボタンの spec（`args=[3, 300]`）は触らない（それはセーブに入る側）。
  差し替えた値でゲーム自身が引き落とし、ゲーム自身が残り年数を数える。
  `314_area_move_custom` の前払い調整のような細工が要らないのは、
  訓練所の代金が**引数で渡ってくる**から（馬車の運賃は渡ってこない）。
  それでも引き落としが設定額にならなかった回は `execute` の前後の所持金で気付けるので、
  差額をその場で戻して WARN を残す（別の場所で徴収しているビルドに備えた保険）
- **1段の期間は「ベース期間 × 倍率」で決める。**
  ベース期間はいちばん短い修行1回の長さ（1ヵ月〜1年から選ぶ）で、
  倍率は修行内容ごとに持つ（既定は素の年数と同じ 1/2/2/3）。
  素の設定（ベース「1年」）なら 365 / 730 / 730 / 1095 日 ＝ ゲームの値そのもの。
  修行内容の種類（`simple` / `fundamental` / `train_skill` / `learn_new_skill`）は
  `TrainingPhaseManager` の第1引数とボタンの spec の args から読む。
  知らない種類のときだけ、ゲームが出している年数（`(N年)` と `elapse_days` の日数 ÷ 365）を
  倍率の代わりに使う
- **日数はローダの関所**（`durations`。TECH.md §3.3.3）に望みを出すだけで、
  `elapse_days` はこちらでは包まない。
  段の窓（`TrainingPhaseManager.execute` の間）の外では何も望まない
  ＝ 街移動・宿泊・他の依頼の日数送りには触らない。
  **ゲームが数える年数（残り年数）には触らない。**
  残り年数の減り方も、残り年数に収まる修行だけを並べる判定もゲームの内側に在って、
  引数には出てこない。
  こちらが変えるのは暦のほうだけなので、その勘定はゲームの中で辻褄が合ったまま残る
  （`あと3年間` の3は**ゲームが数える修行の量**で、実際に進む暦は選んだ段の長さの合計）
- ボタンは `text` だけ触る（`314_` と同じ）。
  代金を変えたときだけ `訓練を受ける(300G)` を書き直し、
  段のボタンは**実際に進む期間**で出す（`ただ鍛える(1ヵ月)`）。
  素の設定ではその文字列がゲームの表示（`ただ鍛える(1年)`）と一致するので、
  既定のままなら1文字も変わらない

開発中（9xx。TECH.md §2.6）なので、遊び方も検証の記録も同じフォルダの DOC.md にある
（§1 が MODS.md 相当、§3 が実機の記録と残りの確認）。
"""

import os
import re
import sys
import time

from instantale_modloader import durations, llm, ui
from instantale_modloader.state import UNKNOWN_WORLD, WorldStore, world_key

LOG_BASENAME = "training_custom.log"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_training_custom_store__"
SETTINGS_STORE_ATTR = "__instantale_training_custom_settings_store__"

# ボタンには何も足さないが、`ui.Screen` の道具（say / apply_buttons）を使うので
# 印のキーは他の MOD と別にして持つ（TECH.md §3.3）。
MARK = "mod_training_custom"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む。
# TECH.md §3.8）。これが全ワールド共通の一括設定。
# ワールド個別の値は同梱の tool.py が `state/training_custom/<世界>.json` に書き、
# `apply()` がその世界を見ているあいだだけここへ上書きする（`refresh_world`）。
# 他のファイルへ移さないこと。
# 既定値はすべて素のゲームの値。
# 素の値のままなら、この MOD はその項目に一切触らない。
# 「-1 で無効」のような番人値は使わない。
# 素の値との照合は下の GAME_* 定数。

# 訓練の代金（素のゲームは 300G で固定。実測）。
# 0 にするとタダ。
TRAINING_PRICE = 300

# 1回の修行の年数（開始時の残り年数。素のゲームは3年）。
# 活動を1つ選ぶたびにその活動の年数ぶん減り、0 になると卒業する。
# 増やすと1回の修行で活動を何段も受けられる。
COURSE_YEARS = 3

# 1段のベース期間（素のゲームは「1年」＝ 365 日）。
# いちばん短い修行（`ただ鍛える`）1回の長さで、他の修行はこれの倍率で決まる。
# 選択肢は下の `PERIOD_DAYS`（1ヵ月＝30日はゲームの1ヵ月。GAME.md §2.17）。
BASE_PERIOD = "1年"

# 修行内容ごとの倍率。**ベース期間の何倍か**。
# 既定は素のゲームの年数そのもの（1年 / 2年 / 2年 / 3年）なので、
# ベース期間が「1年」のままなら1日もずれない。
# 種類の綴りは実測（`231_probe_training`。GAME.md §2.17）。
SIMPLE_FACTOR = 1              # ただ鍛える
FUNDAMENTAL_FACTOR = 2         # 基礎を積む
TRAIN_SKILL_FACTOR = 2         # 技を磨く
LEARN_NEW_SKILL_FACTOR = 3     # 新たな技を学ぶ

# 訓練を受けるボタンの表示テンプレート。
# 使える変数: {name}（ボタンから料金の括弧を落とした呼び名）{price} {years}。
# **代金を変えたときだけ**この形で表示し直す（素のままならゲームの表示のまま）。
START_BUTTON = "{name}({price}G)"

# 修行内容のボタンの表示テンプレート。
# 使える変数: {name} {length}（実際に進む期間。`3ヵ月` `2年`）{days} {factor} {base}。
# 既定のままでベース期間が「1年」なら、ゲームの表示（`ただ鍛える(1年)`）と
# 1文字も変わらない。ベース期間を変えると**その長さが表示に出る**
# （`ただ鍛える(1ヵ月)`）ので、表示と実態は常に一致する。
PHASE_BUTTON = "{name}({length})"

# 訓練中の文言の「N年間」を実際の期間に直すか。
# ゲームは残りの量も1段の結果も**年**で言う（`あと3年間。どうする？` /
# `一年間、ひたすら鍛錬した。`）ので、ベース期間を縮めると文言だけが年のまま残る。
# ONだと、訓練の窓の間に出る文言のその部分だけを実際の期間に直す
# （`あと3ヵ月。どうする？` / `1ヵ月、ひたすら鍛錬した。`）。
# 素の設定では**直す先が同じ文字列になるので1文字も変わらない**。
# 切るとゲームの言い方のまま（暦と文言が食い違う）。
PERIOD_WORDING = True

# 卒業した施設でもう一度訓練を受けられるようにするか。
# 素のゲームは断る（`十分に学んだ。これ以上ここで得るものはないだろう。` で、
# 代金も日数も動かない。実測）。
# 断っている根は施設の**訓練済みの印**（`config["trained"]`。実測 2026-09-16）で、
# ONだとその印をたたむだけ。
# ゲームは卒業していない施設とまったく同じ道を通る（代金も支度も背景も選択肢もゲームのまま）。
ALLOW_RETRAIN = False

# AI に渡す頼み文の年数も実際の期間に直すか。
# ゲームは頼み文にも年数を焼き込む（`残り訓練年数: 3年` など。実測）ので、
# 切っていると**画面は3ヵ月なのに師範のセリフだけが「三年間」と言う**
# （実機 2026-09-15 で確認）。
# ONだと、訓練の頼み文の中の年数だけを実際の期間に直す。
# 人生ログや土地との縁の年数（過去の記録）には当てない（`PROMPT_RULES` の注記）。
LLM_PERIOD_WORDING = True

# 手持ちが設定した代金に足りないときの一言（`314_` / `315_` と同じ形）。
REFUSE_TEXT = "（訓練の代金{price}Gに足りない ― 手持ち{gold}G）"

# ---------------------------------------------------------------- コード側の設定
#: 訓練所のマネージャ（`targets.txt` の実在クラス。GAME.md §2.17 の表）。
#:   TrainingStartManager(app, training_years, training_price)
#:   TrainingPhaseManager(app, training_type, remaining_years, training_log)
START_CLS = "TrainingStartManager"
PHASE_CLS = "TrainingPhaseManager"

# 素のゲームの値。
# **設定がこれと同じ項目には触らない**ための照合値。
# 3つとも実測で、日数と年数はローダの窓口が持っているものを借りる
# （同じ数字を2箇所に書かない。`231_probe_training`、GAME.md §2.17）。
GAME_PRICE = 300
GAME_COURSE_YEARS = durations.GAME_TRAINING_COURSE_YEARS
GAME_DAYS_PER_YEAR = durations.GAME_TRAINING_DAYS_PER_YEAR

#: ベース期間の選択肢と日数。
#: 1ヵ月はゲームの1ヵ月（30日。`durations.DAYS_PER_MONTH`）、
#: 「1年」は訓練の1年（365日。`231_probe_training` の実測）で、
#: **素のゲームはここ**（ベース「1年」×倍率 1/2/2/3 ＝ 365/730/730/1095 日）。
#: mod.json の選択肢とこの並びは同じにすること。
PERIOD_DAYS = {
    "1ヵ月": 1 * durations.DAYS_PER_MONTH,
    "2ヵ月": 2 * durations.DAYS_PER_MONTH,
    "3ヵ月": 3 * durations.DAYS_PER_MONTH,
    "6ヵ月": 6 * durations.DAYS_PER_MONTH,
    "1年": durations.GAME_TRAINING_DAYS_PER_YEAR,
}
GAME_BASE_PERIOD = "1年"

#: 修行内容の種類（実測値）と、その倍率を持つ設定の名前。
#: 設定はワールドごとに差し替わる（`refresh_world` が `globals()` を書く）ので、
#: 値ではなく**名前**で持ち、読むのは呼ばれた時（`factor_of`）。
FACTOR_SETTING = {
    "simple": "SIMPLE_FACTOR",
    "fundamental": "FUNDAMENTAL_FACTOR",
    "train_skill": "TRAIN_SKILL_FACTOR",
    "learn_new_skill": "LEARN_NEW_SKILL_FACTOR",
}

#: 修行内容ごとの**ゲームが数える年数**（実測。`durations` が持っているものを借りる）。
#: 使うのは「残りN年ぶんで暦がどれだけ進みうるか」（`budget_days`）だけ。
#: 1段の長さはこの表ではなく設定の倍率で決まる。
GAME_ACTIVITY_YEARS = dict(durations.GAME_TRAINING_ACTIVITY_YEARS)

#: 施設に立つ「訓練済み」の印。ゲームはこれを見て2回目を断る。
#: 実測 2026-09-16 ― 卒業した道場の控え
#: （`state\modfacility\<世界×主人公>.json`）に
#: `snapshot.config.trained = True` が立っていた。
#: セーブ側には同じ名前の項目が無く（`savedata.json` を走査して0件）、
#: 施設そのものが持っている。
#: **落とすのはキーごと**（`False` を入れても断られた。`fold_trained` の注記）。
TRAINED_KEY = "trained"

# 修行内容のボタンの年数（`ただ鍛える(1年)` の 1）。
# 種類が実測の4つに当たらないビルドで、**ゲームが出している数**を倍率の代わりに使う。
YEARS_RE = re.compile(r"[（(]\s*(\d+)\s*年\s*[)）]\s*$")

# ラベル末尾の括弧（`(300G)` `(1年)`）。呼び名だけを取り出すのに使う。
TAIL_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")

# 訓練中の文言の「N年間」（実測は GAME.md §2.17 と `out\training.jsonl`）。
#
#   算用数字     `あと3年間。どうする？` / `残り2年間。どうする？`
#                残りの**量**。ゲームが数える年数なので、暦に直すのは `budget_days`
#   漢数字＋行頭 `一年間、ひたすら鍛錬した。` / `二年間で基礎能力の向上に費やした。`
#                `二年を基礎能力の向上に費やした。`（実機 2026-09-16。**「間」が無い形**）
#                その1段の結果。長さはその段のもの
#
# **漢数字は行頭のものだけ**を見る。
# AI の描写も漢数字で年を言うが（`これからの三年間、…` `この三年間、…`）、
# あちらは文の途中に出るうえ、指しているのが1段ではなく修行全体なので、
# 段の長さで書き換えると数が嘘になる（実測の4文で確認）。
#
# 「間」の有無は修行内容で変わる（`ただ鍛える` は `一年間、`、
# `基礎を積む` は `二年を`）。後ろに続く助詞（`、` `を` `で`）まで見て、
# `三年後` のような**別の意味の年**には当てない。
COUNT_YEARS_RE = re.compile(r"(\d+)年間")
KANJI_YEARS_RE = re.compile(r"^([一二三四五六七八九十]+)年(?:間)?(?=[、をで])")
KANJI_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# AI へ渡る頼み文の中の年数（実測 2026-09-15。
# `output_data\<世界>\<PC>\training_conversation_starter\N.json` と
# `conversation_facilitator\N.json`。数えた結果は DOC.md §3）。
#
#   残り訓練年数: 3年              訓練所の頼み文（開始・完了の両方）
#   費用と3年の時間を費やす代わりに  同上（システム側の説明）
#   入学の希望を受けた場合: 3年間を費やし    会話の頼み文（訓練の勧誘の説明）
#   新たな技の会得に3年を費やした...        訓練の記録（その段の結果）
#
# **錨は訓練の頼み文にしか無い言い回しに打つ。**
# 素の `N年` に当てると、同じ記録に写っている
# 「…は指導者レオンのもとで3年間の過酷な基礎訓練を終え」（人生ログ。訓練の後に書かれ、
# 以後あらゆる頼み文へ運ばれる）や「この土地に合計2年暮らしている」（縁）まで
# 書き換えてしまう。
# どちらも**過去の記録**なので、後から期間を書き換えるのは嘘になる。
PROMPT_RULES = (
    (re.compile(r"残り訓練年数(?P<sep>[:：]\s*)(?P<y>\d+)\s*年"), "budget",
     lambda m, length: "残り訓練期間" + m.group("sep") + length),
    (re.compile(r"費用と(?P<y>\d+)\s*年の時間"), "budget",
     lambda m, length: "費用と" + length + "の時間"),
    (re.compile(r"(?P<head>入学の希望を受けた場合[:：]?\s*)(?P<y>\d+)\s*年間"), "budget",
     lambda m, length: m.group("head") + length),
    (re.compile(r"(?P<y>\d+)\s*年を費やした"), "phase",
     lambda m, length: length + "を費やした"),
)

# 頼み文が訓練のものだと分かる手掛かり（当たらなかったときのログ用）。
PROMPT_MARKS = ("残り訓練年数", "訓練施設", "訓練の記録", "入学の希望")

# ワールド個別の設定の控え。
# 書くのは同梱の tool.py だけ。ゲーム中はこの MOD が読む。
SETTINGS_DIRNAME = "training_custom"
SETTING_NAMES = ("TRAINING_PRICE", "COURSE_YEARS", "BASE_PERIOD",
                 "SIMPLE_FACTOR", "FUNDAMENTAL_FACTOR", "TRAIN_SKILL_FACTOR",
                 "LEARN_NEW_SKILL_FACTOR",
                 "START_BUTTON", "PHASE_BUTTON", "ALLOW_RETRAIN",
                 "PERIOD_WORDING", "LLM_PERIOD_WORDING", "REFUSE_TEXT")


class _SafeDict(dict):
    """テンプレートに無い変数名が来ても落とさない（`{typo}` はそのまま残る）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def fmt(template, **values):
    """設定のテンプレートを埋める。壊れたテンプレートでも素の文字列で返す。

    埋めた後に通貨の表記を今の表記へ直す（`130_` が差し替えていれば
    `訓練を受ける(500G)` → `訓練を受ける(500円)`）。
    設定のテンプレートは素のゲームの言い方（`G`）のままでよい。
    """
    try:
        filled = str(template).format_map(_SafeDict(values))
    except Exception:
        filled = str(template)
    return ui.rewrite_coins(filled)


def phase_years(label):
    """修行内容のボタンから年数を読む。読めなければ None。"""
    match = YEARS_RE.search(str(label or ""))
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def plain_name(label):
    """ラベルから末尾の括弧を落とした呼び名。落とすものが無ければそのまま。"""
    return TAIL_RE.sub("", str(label or "")) or str(label or "")


def base_days(period=None):
    """ベース期間の日数。知らない選択肢なら素の値（365日）。"""
    period = BASE_PERIOD if period is None else period
    value = PERIOD_DAYS.get(str(period))
    return int(value) if value else GAME_DAYS_PER_YEAR


def factor_of(activity, fallback=None):
    """その修行内容の倍率。実測の4種に当たらなければ `fallback`。

    値ではなく設定の名前から引くのは、ワールドごとに差し替わるため
    （`refresh_world` が `globals()` を書き直す）。
    """
    name = FACTOR_SETTING.get(str(activity))
    if name is None:
        return fallback
    try:
        return max(1, int(globals()[name]))
    except (KeyError, TypeError, ValueError):
        return fallback


def phase_days(activity, game_days=None, years=None):
    """その1段で進む日数（ベース期間 × 倍率）。

    倍率は修行内容の種類から引く。
    知らない種類のときは**ゲーム自身が言っている年数**を倍率の代わりに使う
    （段の実行中なら `elapse_days` に来た日数 ÷ 365、画面ならボタンの `(N年)`）。
    どちらも無ければ 1 倍。1日は必ず進める（0 日の段を作らない）。
    """
    fallback = years
    if fallback is None and game_days:
        fallback = int(round(int(game_days) / float(GAME_DAYS_PER_YEAR)))
    factor = factor_of(activity, fallback)
    try:
        factor = max(1, int(factor))
    except (TypeError, ValueError):
        factor = 1
    return max(1, base_days() * factor), factor


def budget_days(years):
    """残りN年ぶんで進みうる暦の (最短, 最長)。読めなければ `(None, None)`。

    ゲームが数える年数は、こちらの倍率では**一意に暦へ直せない**。
    残り3年は「3年の修行1回」でも「1年の修行3回」でも埋まり、
    倍率を修行ごとに変えているとその2つは長さが違う。
    そこで、残りを埋める組み合わせの中での最短と最長を返す
    （素の比のまま＝倍率がゲームの年数と同じなら、最短＝最長＝その年数 × ベース期間）。

    使うのは文言の書き換えだけ（`あと3年間` を実際の期間へ）。
    暦そのものは1段ずつ `phase_days` で決まるので、ここの数は何も動かさない。
    """
    try:
        years = int(years)
    except (TypeError, ValueError):
        return None, None
    if years < 0:
        return None, None
    costs = []
    for key, game_years in GAME_ACTIVITY_YEARS.items():
        days, _factor = phase_days(key)
        if isinstance(game_years, int) and game_years >= 1:
            costs.append((game_years, days))
    if not costs:
        return None, None
    # 残り year 年を埋める組み合わせの最短・最長（どちらも 1年の修行で必ず埋まる）。
    shortest, longest = [0] * (years + 1), [0] * (years + 1)
    for year in range(1, years + 1):
        fits = [(cost, days) for cost, days in costs if cost <= year]
        if not fits:
            return None, None
        shortest[year] = min(days + shortest[year - cost] for cost, days in fits)
        longest[year] = max(days + longest[year - cost] for cost, days in fits)
    return shortest[years], longest[years]


def length_text(days):
    """日数を読める長さに（`365` → `1年`、`90` → `3ヵ月`、それ以外は `N日`）。

    素の設定（ベース「1年」×1/2/2/3）ではゲームの表示（`1年` `2年` `3年`）と
    同じ文字列になるので、既定のままならボタンは1文字も変わらない。
    """
    days = int(days)
    if days and days % GAME_DAYS_PER_YEAR == 0:
        return "{}年".format(days // GAME_DAYS_PER_YEAR)
    if days and days % durations.DAYS_PER_MONTH == 0:
        return "{}ヵ月".format(days // durations.DAYS_PER_MONTH)
    return "{}日".format(days)


def budget_text(years):
    """残りN年ぶんの言い方。ゲームの言い方と同じなら None（＝文言を触らない）。

    最短と最長が同じなら1つの長さ（`3ヵ月`）、違えば幅（`3ヵ月〜18ヵ月`）。
    """
    shortest, longest = budget_days(years)
    if shortest is None:
        return None
    if shortest == longest:
        return length_text(shortest) if shortest != int(years) * GAME_DAYS_PER_YEAR \
            else None
    return "{}〜{}".format(length_text(shortest), length_text(longest))


def reprompt(text, activity=None, phase=False):
    """AI へ渡る頼み文の年数を実際の期間へ。触らないなら None。

    書き換えるのは `PROMPT_RULES` の錨に当たった所だけ。
    `phase` の規則（その段の結果）は段の中でのみ当てる
    ― 訓練の外では、同じ言い回しが過去の記録として運ばれてくることがある。
    """
    if not LLM_PERIOD_WORDING or not isinstance(text, str) or not text:
        return None
    new = text
    for pattern, kind, build in PROMPT_RULES:
        if kind == "phase" and not phase:
            continue

        def one(match, kind=kind, build=build):
            years = match.group("y")
            if kind == "phase":
                days, _factor = phase_days(activity, years=int(years))
                said = length_text(days) \
                    if days != int(years) * GAME_DAYS_PER_YEAR else None
            else:
                said = budget_text(years)
            return build(match, said) if said else match.group(0)

        new = pattern.sub(one, new)
    return new if new != text else None


def reword(text, activity=None, phase=False):
    """訓練中の文言の「N年間」を実際の期間へ。触らないなら None。

    算用数字（`あと3年間`）は残りの量なので `budget_text`。
    行頭の漢数字（`一年間、ひたすら鍛錬した。`）はその1段の長さで直す
    ― これは段の中（`phase=True`）だけ。
    どちらも当たらない文・直した先が同じ文字列になる文は None。
    """
    if not PERIOD_WORDING or not isinstance(text, str) or not text:
        return None

    def by_count(match):
        said = budget_text(match.group(1))
        return said if said else match.group(0)

    new = COUNT_YEARS_RE.sub(by_count, text)
    match = KANJI_YEARS_RE.match(new) if phase else None
    if match is not None:
        years = KANJI_NUMBERS.get(match.group(1))
        if years:
            days, _factor = phase_days(activity, years=years)
            if days != years * GAME_DAYS_PER_YEAR:
                new = length_text(days) + new[match.end():]
    return new if new != text else None


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="training custom", mark=MARK)

    # **置き場は `sys`。** `apply()` は1プロセスで何度も走り、当て直しは背景スレッドの
    # `boot()` から来る（未 import のモジュールが現れた時＝最初の LLM リクエストの時）。
    # 訓練の最中にそれが挟まると、ここで作り直した空の器を新しいラッパが握り、
    # 段の窓が None のまま素の 1095 日が通る（`314_` が同じ理由で `sys` に置いている）。
    state = getattr(sys, STATE_STORE_ATTR, None)
    if state is None:
        state = {
            # いま `TrainingPhaseManager.execute` の中に居るかの窓。
            "phase": None,
            # いま `TrainingStartManager.execute` の中に居るか（文言の書き換え用）。
            "start": False,

            # ボタンと引数から読み取った素の代金（読めた最新の値）。
            "game_price": None,
            # 自分が書いた「訓練を受ける」のラベル。画面がラベルを組み直さない
            # ビルドで同じ処理がもう一度来たとき、**自分の書いた額を素の代金として
            # 読み込まない**ための目印（`314_` の `our_coach_label` と同じ）。
            # 段のラベルはここに入れない ― あちらは spec の args から組み直すので
            # 読み直しても同じ文字列になり、素の設定では**ゲームの表示と一致する**
            # （溜めると、後から素のラベルが自分のものに見えて塗り直しが止まる）。
            "our_start_label": None,
            # 年数が読めなかったラベル（同じものを何度もログに出さない）。
            "unknown": set(),
            # 施設が読めない旨を1度だけ言うための印。
            "quiet": False,
        }
        setattr(sys, STATE_STORE_ATTR, state)

    # ワールド個別の設定。控えがあればその世界を見ているあいだだけ上書きし、
    # 無い世界では一括設定（ローダが注入した値）へ戻す。
    settings_store = getattr(sys, SETTINGS_STORE_ATTR, None)
    if not isinstance(settings_store, WorldStore):
        settings_store = WorldStore(ctx, SETTINGS_DIRNAME, default=dict, write=write)
        setattr(sys, SETTINGS_STORE_ATTR, settings_store)
    elif settings_store is not None and hasattr(settings_store, "rebind"):
        settings_store.rebind(ctx, write)
    # 注入のたびにモジュールは作り直され、一括設定を注入してからここへ来る（TECH.md §3.8）。
    # だからここで控えた値がそのまま一括設定（`sys` に固定してはいけない。
    # 一括設定を変えて注入し直しても古い値が残る）。
    base_settings = {name: globals()[name] for name in SETTING_NAMES}
    active_settings = [None]    # 直前に反映した (世界, 値) の署名

    def refresh_world(app=None):
        """現在のワールドの控えを読み直す。変わっていれば True。"""
        current_world = UNKNOWN_WORLD
        if app is None:
            try:
                app = ui.find_app()
            except Exception:
                app = None
        try:
            if app is not None:
                current_world = world_key(app)
        except Exception:
            pass
        record = {}
        if isinstance(current_world, str) and current_world != UNKNOWN_WORLD:
            try:
                loaded = settings_store.load(current_world, fresh=True)
                if isinstance(loaded, dict):
                    record = loaded
            except Exception:
                ctx.log_exc("training custom: cannot read world settings")
        values = {}
        for name in SETTING_NAMES:
            default = base_settings[name]
            value = record.get(name, default)
            values[name] = value if type(value) is type(default) else default
        signature = (current_world,
                     tuple((name, values[name]) for name in SETTING_NAMES))
        changed = signature != active_settings[0]
        if changed:
            globals().update(values)
            active_settings[0] = signature
        return changed

    # 起動時に現在ワールドが分かっていれば即時に反映する。
    refresh_world()

    # `ctx.mod_dir` はフックの中では読めない（apply() の間だけ）ので、ここで控える。
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "training_custom"

    def training_for(app):
        """ローダの窓口（`durations.TRAINING`）に置く答え。

        窓口は「1段の日数 ＝ `activity_years` × `days_per_year`」の形で持つので、
        ベース期間を `days_per_year` に、倍率を `activity_years` に入れる
        （掛け算の形が同じなので、読む側が受け取る日数は正しい。TECH.md §3.3.2）。
        素の設定なら 365 と 1/2/2/3 ＝ 窓口の素の値そのものになる。
        読む側はこの MOD の名前を知らず、窓口に聞くだけ。
        """
        return {"days_per_year": base_days(),
                "course_years": max(1, int(COURSE_YEARS)),
                "activity_years": dict((key, factor_of(key, 1))
                                       for key in FACTOR_SETTING)}

    durations.declare(durations.TRAINING, training_for, owner=owner, write=write)

    def set_gold(app, value):
        """所持金を書く。型を保つ（float の世界に int を混ぜない）。"""
        player = getattr(app, "player", None)
        current = getattr(player, "gold", None)
        player.gold = float(value) if isinstance(current, float) else int(round(value))

    # ============================================================ ボタンの表示
    def relabel_start(old, entry=None):
        """訓練を受けるボタンの新しいラベル。触らないなら None。

        代金を変えたときだけテンプレートで表示し直す。
        素の値のままならゲームの表示（`訓練を受ける(300G)`）をそのまま残す。
        """
        if int(TRAINING_PRICE) == GAME_PRICE:
            return None
        new = fmt(START_BUTTON, name=plain_name(old), price=int(TRAINING_PRICE),
                  years=int(COURSE_YEARS))
        return new if new != old else None

    def relabel_phase(old, entry=None):
        """修行内容のボタンの新しいラベル。触らないなら None。

        修行内容の種類は**ボタンの spec の args から読む**（`['simple', 3, '']`。
        ラベルの文字列には下がらない）。
        知らない種類のときだけ、ゲームが出している `(N年)` を倍率の代わりに使う。
        素の設定ならゲームの表示と同じ文字列になるので None が返る。
        """
        argv = ui.spec_args(entry) if entry is not None else None
        activity = argv[0] if argv else None
        years = phase_years(old)
        if activity is None and years is None:
            if old not in state["unknown"]:
                state["unknown"].add(old)
                write("no activity and no years in the phase label {!r}; leaving "
                      "it alone (the game may have changed its wording)".format(old))
            return None
        days, factor = phase_days(activity, years=years)
        new = fmt(PHASE_BUTTON, name=plain_name(old), length=length_text(days),
                  days=days, factor=factor, base=str(BASE_PERIOD))
        return new if new != old else None

    def relabel(app, cls_name, make):
        """そのクラスのボタンのラベルを書き直す。1つでも変えたら True。

        触るのは `text` だけ（spec と `args` はゲーム自身のものを残す）ので、
        この MOD を外しても押下の挙動は壊れない。
        """
        changed = False
        buttons = getattr(app, "buttons", None) if app is not None else None
        if not isinstance(buttons, (list, tuple)):
            return False
        for entry in buttons:
            if not isinstance(entry, dict) or ui.spec_cls_name(entry) != cls_name:
                continue
            old = entry.get("text") or ""
            if not old or (cls_name == START_CLS
                           and old == state["our_start_label"]):
                continue          # 自分が書いた代金のラベルは読み直さない
            new = make(old, entry)
            if new and new != old:
                entry["text"] = new
                if cls_name == START_CLS:
                    state["our_start_label"] = new
                changed = True
                write("label: {!r} -> {!r} ({})".format(old, new, cls_name))
        return changed

    def repaint(app, cls_name, make):
        """ラベルを書き直して、変わっていれば次のフレームで塗り直す。"""
        try:
            if relabel(app, cls_name, make):
                screen.apply_buttons(app, None, "relabel")
        except Exception:
            ctx.log_exc("training custom: cannot relabel the buttons")

    @ctx.wrap("__main__:DisplayTrainingChoice.update_button_display",
              required=False, safe=True)
    def choice_buttons(orig, self, *args, **kwargs):
        """`訓練を受ける(300G)` が並び終えた後、ラベルの `text` だけを書き直す。

        素の代金はここで（書き換える前のラベルから）読み取って控える。
        """
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is None:
                return result
            refresh_world(app)
            for entry in getattr(app, "buttons", None) or []:
                if isinstance(entry, dict) and ui.spec_cls_name(entry) == START_CLS \
                        and (entry.get("text") or "") != state["our_start_label"]:
                    price = ui.parse_coin(entry.get("text") or "")
                    if price is not None:
                        state["game_price"] = price
            repaint(app, START_CLS, relabel_start)
        except Exception:
            ctx.log_exc("training custom: cannot relabel the training choice")
        return result

    # ============================================================ 代金と年数
    @ctx.wrap("__main__:{}.__init__".format(START_CLS), required=False)
    def start_init(orig, self, app, *args, **kwargs):
        """ゲームが受け取る `training_years` / `training_price` を差し替える。

        位置で決め打ちせず、**渡ってきた数だけ**を触る
        （引数が減ったビルドでも、そこには何も足さずに素通しする）。
        差し替えた値でゲーム自身が引き落とし、ゲーム自身が残り年数を数えるので、
        画面の文言（`あと3年間`）も勝手に追随する。
        """
        argv = list(args)
        try:
            refresh_world(app)
            if len(argv) >= 2 and isinstance(argv[1], int) \
                    and not isinstance(argv[1], bool):
                state["game_price"] = int(argv[1])
                if int(TRAINING_PRICE) != GAME_PRICE:
                    argv[1] = max(0, int(TRAINING_PRICE))
            if len(argv) >= 1 and isinstance(argv[0], int) \
                    and not isinstance(argv[0], bool) \
                    and int(COURSE_YEARS) != GAME_COURSE_YEARS:
                argv[0] = max(1, int(COURSE_YEARS))
            if argv != list(args):
                write("start: the terms {} became {}".format(list(args), argv))
        except Exception:
            ctx.log_exc("training custom: cannot set the training terms")
            argv = list(args)
        return orig(self, app, *argv, **kwargs)

    def settle_price(app, before):
        """訓練の開始時点の帳尻。

        引数を差し替えているのでゲームは既に設定額を引いているはず。
        ここで見るのは**本当にその額だったか**だけ。
        素の代金のほうが引かれていたら（代金を別の場所で決めているビルド）、
        差額をその場で戻して WARN を残す。
        想定外の値になっていたら触らずに WARN（訓練の中で代金以外の出入りがあった場合を
        壊さないため）。
        """
        want = int(TRAINING_PRICE)
        if want == GAME_PRICE or before is None:
            return
        after = ui.gold_of(app)
        if after is None:
            write("WARN price: cannot re-read the gold; leaving it as is")
            return
        moved = before - after
        game = state["game_price"] if state["game_price"] is not None else GAME_PRICE
        if moved == want:
            write("price: charged {} in one deduction; gold {} -> {}".format(
                want, before, after))
        elif moved == 0:
            write("price: nothing was charged (gold {}); the training did not "
                  "start".format(before))
        elif moved == game:
            set_gold(app, before - want)
            write("WARN price: the game charged its own {} instead of {}; "
                  "gold {} -> {} (corrected)".format(
                      game, want, after, before - want))
        else:
            write("WARN price: the gold moved {} (ours is {}); leaving it as is "
                  "({} -> {})".format(moved, want, before, after))

    # ============================================================ 再訓練
    def facility_here(app):
        """いま立っている施設の実体。読めなければ None。

        立ち位置はロード直後だけ文字列で、遊んでいる最中は施設の実体（GAME.md §2.7）。
        文字列のときは今のエリアから id で引く。
        """
        location = getattr(getattr(app, "player", None), "location", None)
        if location is not None and not isinstance(location, (str, int)):
            return location
        facility, _node = ui.find_facility(ui.current_area(app), str(location or ""))
        return facility

    def fold_trained(app):
        """訓練済みの印を**キーごと**落とす。落としたら True。

        これが再訓練の全部。
        印さえ無ければ、ゲームは卒業していない施設と同じ道を通るので、
        代金も支度（主の絵と背景）も選択肢も文言もゲームのまま。

        **`False` を入れるのでは足りない**（実機 2026-09-16）。
        `config[TRAINED_KEY] = False` にしても断られ、控えにも `"trained": false` が
        残っていた。一度も卒業していない道場（同じ世界の `天流院`）の config には
        **キー自体が無い**ので、ゲームが見ているのは値ではなくキーの有無と読める。
        未訓練の施設と同じ姿にする。

        版5〜7 は断られた回を引き取って選択肢を組み直していた。
        引数の形は実測どおりでも支度が飛ぶので背景の出ない画面になり（実機 2026-09-16）、
        伏せる・払い戻すの後始末も要った。印を落とす側に寄せて全部やめた。
        """
        facility = facility_here(app)
        config = getattr(facility, "config", None)
        if not isinstance(config, dict):
            # 施設が読めない（立ち位置が引けない・config を持たない作り）。
            # **黙って諦めない** ― 断られたのに手掛かりが無い、を作らない。
            if not state["quiet"]:
                state["quiet"] = True
                write("WARN retrain: cannot read the config of the place we are "
                      "standing in ({!r}); the mark is left alone".format(facility))
            return False
        state["quiet"] = False
        if TRAINED_KEY not in config:
            return False                  # もともと未訓練（何もしない）
        config.pop(TRAINED_KEY, None)
        write("retrain: dropped the {!r} mark of {!r} (config now {})".format(
            TRAINED_KEY, ui.facility_name(app, facility), sorted(config)))
        return True

    @ctx.wrap("__main__:{}.execute".format(START_CLS), required=False)
    def start_execute(orig, self, choice_text=None, *args, **kwargs):
        """代金の引き落としを前後の所持金で確かめ、続く修行内容のボタンを整える。

        文言の書き換え（`あと3年間` → `あと3ヵ月`）のために、この間だけ窓を開ける。
        卒業済みの施設は、設定が許していれば**印をたたんでから**ゲームに通す。
        """
        app = getattr(self, "app", None) or ui.find_app()
        refresh_world(app)
        before = ui.gold_of(app)
        state["start"] = True
        if ALLOW_RETRAIN:
            # 断られる前に印をたたむ。ここから先はゲームがいつもどおり進める。
            fold_trained(app)
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            # 窓（文言の直しと断りの伏せ）は**再訓練の支度が終わるまで**開けておく。
            # `training_start()` も `あと3年間。どうする？` を出すので、
            # 先に閉じるとそこだけ年のまま残る。
            try:
                settle_price(app, before)
            except Exception:
                ctx.log_exc("training custom: cannot settle the price")
            state["start"] = False
            repaint(app, PHASE_CLS, relabel_phase)

    # ============================================================ 日数
    @ctx.wrap("__main__:{}.__init__".format(PHASE_CLS), required=False, safe=True)
    def phase_init(orig, self, app, *args, **kwargs):
        """`execute` には引数が来ないので、活動の種類と残り年数をここで控える。

        自分専用の属性名で持つだけで、ゲームのデータには何も書かない
        （マネージャはセーブに入らない。セーブに入るのは spec のほう）。
        """
        result = orig(self, app, *args, **kwargs)
        try:
            self._mod_training_custom = {
                "activity": args[0] if len(args) >= 1 else None,
                "remaining": args[1] if len(args) >= 2 else None,
            }
        except Exception:
            pass
        return result

    @ctx.wrap("__main__:{}.execute".format(PHASE_CLS), required=False)
    def phase_execute(orig, self, choice_text=None, *args, **kwargs):
        """段の間だけ窓を開ける。日数の差し替えはこの窓の中だけ。"""
        app = getattr(self, "app", None) or ui.find_app()
        refresh_world(app)
        window = None
        try:
            info = getattr(self, "_mod_training_custom", None) or {}
            window = {"activity": info.get("activity"),
                      "remaining": info.get("remaining"),
                      # この段が始まった時刻。日数送りに複数の MOD が望みを出したとき、
                      # **先に始まった事情が決める**（ローダの `durations`）。
                      "since": time.time(),
                      "granted": [],
                      # 暦が動いたかの見張り。**自分の望みとは別の経路**で見る
                      # （素の設定では望みを出さないので `granted` は空のままになる）。
                      "day_before": ui.game_day(app)}
            write("phase: activity={!r} remaining={!r} choice={!r} base={} "
                  "factor={}".format(
                      window["activity"], window["remaining"], choice_text,
                      BASE_PERIOD, factor_of(window["activity"], "?")))
            state["phase"] = window
        except Exception:
            ctx.log_exc("training custom: cannot open the phase window")
            window = None
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            state["phase"] = None
            if window is not None and not window["granted"] \
                    and str(BASE_PERIOD) != GAME_BASE_PERIOD:
                write("WARN phase: no elapse_days call came through this phase; "
                      "the days were left as they are")
            if ALLOW_RETRAIN:
                # 卒業した段でまた印が立つ。**立たせたままにしない**（本人の指定）。
                try:
                    fold_trained(app)
                except Exception:
                    ctx.log_exc("training custom: cannot fold the trained mark")
            repaint(app, PHASE_CLS, relabel_phase)

    # `elapse_days` はこちらでは包まない。包むのはローダの関所1枚だけで、
    # ここは「この段を何日にしたいか」を答える側に回る（TECH.md §3.3.3）。
    def days_wish(app, days):
        """段の窓の間だけ、ベース期間 × 倍率の日数を望む。窓の外では None。

        ゲームが送ってきた数と同じなら None（＝素の設定。この段には関心が無い）。
        1段で何回来ても、そのつど同じ式で答えるので合計がずれない。
        実測では1段につき1回（`231_probe_training`）。
        """
        window = state["phase"]
        if window is None:
            return None
        want, _factor = phase_days(window.get("activity"), game_days=days)
        if want == int(days):
            return None
        return {"days": want, "since": window.get("since")}

    def days_note(app, days, granted):
        """実際に渡った日数を控える。**他所が決めた回も来る**。"""
        window = state["phase"]
        if window is not None:
            window["granted"].append(granted)

    durations.claim_days(owner, days_wish, note=days_note, write=write)
    durations.install(ctx, write)

    # ============================================================ 文言
    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context=None, *args, **kwargs):
        """訓練の窓の間だけ、文言の「N年間」を実際の期間に直す。

        窓の外（他の場面の文言）には一切触らない。
        当たらなかった文はログに残す（ゲームの言い回しが変わったときの手掛かり）。
        """
        try:
            window = state["phase"]
            if (window is not None or state["start"]) and isinstance(context, str):
                refresh_world(self)
                activity = window.get("activity") if window is not None else None
                replaced = reword(context, activity, phase=window is not None)
                if replaced is not None and replaced != context:
                    write("text: {!r} -> {!r}".format(context, replaced))
                    return orig(self, replaced, *args, **kwargs)
                if PERIOD_WORDING and "年" in context and len(context) < 40:
                    write("text passing through (no year mark matched): {!r}"
                          .format(context))
        except Exception:
            ctx.log_exc("training custom: cannot reword the training text")
        return orig(self, context, *args, **kwargs)

    def rewrite_outgoing(texts, site):
        """AI へ出ていく頼み文の年数を実際の期間に直す。

        ここが持つのは何をどう書き換えるかだけで、仕掛け先の選択と
        「1回の推論で1回だけ」はローダ側（`llm.wrap_outgoing`。TECH.md §5.3）。
        ローカルもクラウドも同じ1か所を通る。

        窓（`state`）はわざとスレッドをまたぐ形にしてある（`315_` と同じ）。
        訓練の `execute` はワーカースレッドで走り、送信はそこからさらに
        別のスレッドへ渡りうる。スレッドローカルにすると渡った先で窓が閉じて見える。
        またいだぶん無関係な推論にも掛かりうるが、
        錨が訓練の頼み文にしか無い言い回しなので実害は無い。

        書き換えに失敗しても本文はそのまま送られる（ローダが握る）。
        描写の年数がずれるだけで、遊びは止めないほうがよい。
        """
        window = state["phase"]
        result, changed = [], False
        for content in texts:
            new = reprompt(content, window.get("activity") if window else None,
                           phase=window is not None)
            if new is not None:
                changed = True
                write("prompt at {}: the years became the real period".format(site))
            elif any(mark in content for mark in PROMPT_MARKS):
                # 訓練の頼み文なのに1つも当たらなかった。
                # ゲームの言い回しが実測から変わったときのために、その一節だけ残す。
                for mark in PROMPT_MARKS:
                    at = content.find(mark)
                    if at >= 0:
                        write("prompt at {} kept as is: ...{}...".format(
                            site, content[max(0, at - 30):at + 50]))
                        break
            result.append(new if new is not None else content)
        return result if changed else None

    if LLM_PERIOD_WORDING:
        # 設定は遊んでいる最中に変わりうるので、素の設定でも仕掛けておく
        # （素のままなら `reprompt` が即 None を返すので、他の推論の邪魔はしない）。
        llm.wrap_outgoing(ctx, rewrite_outgoing, label="training custom")

    # ============================================================ 手持ちの確認
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """設定した代金に手持ちが満たないときは、押された時点で断る。

        ゲームのボタンは触らず押下だけ握る。画面はそのまま残るので選び直せる。
        読めなかったときは通す（値が読めないことを理由に遊びを止めない。`314_` と同じ）。
        """
        try:
            refresh_world(self)
            if int(TRAINING_PRICE) != GAME_PRICE:
                entry = ui.pressed_entry(self, button_index)
                if isinstance(entry, dict) \
                        and ui.spec_cls_name(entry) == START_CLS:
                    gold = ui.gold_of(self)
                    if gold is not None and gold < int(TRAINING_PRICE):
                        write("refused: price {} > gold {}".format(
                            int(TRAINING_PRICE), gold))
                        screen.say(self, fmt(REFUSE_TEXT,
                                             price=int(TRAINING_PRICE), gold=gold))
                        return None
        except Exception:
            ctx.log_exc("training custom: the price check failed")
        return orig(self, button_index, *args, **kwargs)

    # ------------------------------------------------------------ 自己検証
    # 実経路は訓練所で1回訓練するまで通らない。
    # ラベルの読み書きと日数の式だけは作ったデータで先に確かめておく（`314_` と同じ方針）。
    # 通貨の表記は `130_` が差し替えていることがあるので、
    # 見本のほうも同じ表記へ通してから突き合わせる。
    sample = fmt(START_BUTTON, name="訓練を受ける", price=500, years=3)
    expected = ui.rewrite_coins("訓練を受ける(500G)")
    survives = fmt("{name}と{typo}", name="訓練")
    labels = (plain_name("訓練を受ける(300G)"), plain_name("ただ鍛える(1年)"),
              plain_name("やった"))
    years = (phase_years("ただ鍛える(1年)"), phase_years("新たな技を学ぶ(3年)"),
             phase_years("やった"))
    # ベース期間の表と長さの言い方。設定と無関係に確かめる（期間名を明示で渡す）。
    periods = tuple(base_days(name) for name in
                    ("1ヵ月", "2ヵ月", "3ヵ月", "6ヵ月", "1年", "そんな期間は無い"))
    lengths = (length_text(365), length_text(1095), length_text(30),
               length_text(540), length_text(7))
    # 知らない修行内容のときに、ゲームの言う年数が倍率の代わりに立つこと。
    fallbacks = (phase_days("no_such_activity", game_days=1095)[1],
                 phase_days("no_such_activity", years=2)[1],
                 factor_of("no_such_activity", 4))
    # 文言の書き換え。**設定に関わらず成り立つこと**だけを見る（実測の文で。
    # 設定しだいで変わる側は `tools\tests\test_wip_training_custom.py` が見ている）。
    #   AI の描写（文の途中の漢数字）には触らない
    #   段の外では行頭の漢数字にも触らない
    spoken = (reword("これからの三年間、私が責任を持ってお手伝いいたしましょう。"),
              reword("一年間、ひたすら鍛錬した。0の経験値を得た。", "simple"))
    if sample == expected and survives == "訓練と{typo}" \
            and labels == ("訓練を受ける", "ただ鍛える", "やった") \
            and years == (1, 3, None) \
            and periods == (30, 60, 90, 180, 365, 365) \
            and lengths == ("1年", "3年", "1ヵ月", "18ヵ月", "7日") \
            and fallbacks == (3, 2, 4) and spoken == (None, None):
        ctx.log("verified: reads the activity and the years, formats templates, "
                "turns a base period into days, and leaves the game's own "
                "wording alone")
    else:
        ctx.log("VERIFY FAILED: sample={!r} survives={!r} labels={!r} years={!r} "
                "periods={!r} lengths={!r} fallbacks={!r} spoken={!r}".format(
                    sample, survives, labels, years, periods, lengths, fallbacks,
                    spoken), level="ERROR")

    ctx.log("training custom: price={} course={}y base={}({}d) factors={} "
            "log={}".format(
                TRAINING_PRICE, COURSE_YEARS, BASE_PERIOD, base_days(),
                tuple(factor_of(key, 1) for key in FACTOR_SETTING), log_path))
