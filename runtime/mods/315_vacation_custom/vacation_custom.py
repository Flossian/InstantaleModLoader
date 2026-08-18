# -*- coding: utf-8 -*-
"""機能調整: 宿の宿泊（期間・部屋の名前・宿代）を設定で変えられるようにする。

素のゲームの宿泊は、期間がゲーム任せ（ボタンは `宿泊する(3ヵ月)`。**プレイヤーの
年齢で 3〜6ヵ月に変わる変動式** ― 仕様情報、2026-08-18。実測は 20代で 3、
31歳で 4）、部屋が4つに固定されている。この MOD は期間・部屋の呼び名・宿代を
mod.json の設定から変えられるようにする。**既定値はすべて素のゲームの値
（期間は「デフォルト」＝ゲームのまま）で、そのままなら挙動は何も変わらない。**
変えた項目だけが効く。

宿泊の流れ（すべて実機で実測。`out/vacation.log` 2026-08-18 ／
`out/events.log` 2026-08-06〜08-14。GAME.md §2.17）:

    [施設の画面]  宿泊する(4ヵ月)                  ← DisplayVacationChoice(app, period_months)
                        ↓                             period_months は int（実測 4）
    [部屋選び]    犬小屋(0G) / 簡易寝台(10G) / 個室(100G) / 高級個室(1000G)
                        ↓                          ← VacationStartManager(app, months, quality)
                  「4ヵ月泊まることにした。」        quality は str（'kennel' 'bunk'
                  ＋ elapse_days(months*30) が        'private_room' 'luxury_suite'）
                     **ここで**1回。宿代もここで1回
                        ↓
                  「何をして過ごす？」
                        ↓
    [過ごし方]    休養をとる / 訓練する / 他者と交流 / ...  ← 日数も金も動かない
                        ↓
                  まだ宿泊する（VacationStartManager でもう1周＝**宿代も日数も
                  もう1回**） / 宿泊を終える

## 5つの柱と、それぞれの当て所

| 何を変えるか | どこで変えるか |
|---|---|
| 部屋の名前・ボタンの表示 | `InstantaleApp.refresh_choice_buttons` の前に `text` だけ書き換える |
| 宿代 | ゲームが引き落とす**前**に差額ぶん所持金をずらす（`314_` と同じ前払い調整） |
| 宿泊期間（月単位） | `DisplayVacationChoice.__init__` に渡る `period_months` を差し替える |
| 宿泊期間（週単位） | `period_months` は 1 にして、宿泊の窓の間だけ `elapse_days` に渡る日数の合計を設定値へ（`314_` の予算方式） |
| LLM の描写の月数 | 窓の間だけ、出ていく本文の `数ヵ月の宿泊` を実際の期間へ（`llm.wrap_outgoing`） |

**表示と実態が必ず一致する**のは `314_area_move_custom` と同じ約束。ボタンに出す
期間・料金は設定値そのもので、ゲームに渡す値も同じ設定値。

## 期間は選択式。年齢による加算はゲームの仕様に寄せる

期間は `デフォルト / 1週間 / 2週間 / 1ヵ月 / 2ヵ月 / 3ヵ月 / 4ヵ月 / 6ヵ月` の
選択式（`STAY_CHOICES`）。「デフォルト」ならゲームのまま何も触らない。

ゲーム自身の期間が年齢の変動式（若いと3ヵ月 → 年を取ると最長6ヵ月）なのに
合わせて、選んだ期間にも**年齢の加算**を掛けられる（`AGE_SCALING`。既定 ON）:

    20代まで  そのまま         30代      +1週間 / +1ヵ月
    40代      +2週間 / +2ヵ月  50代以上  +3週間 / +3ヵ月
    上限は 4週間 / 6ヵ月。週の加算は選んだ期間が週単位（1週間・2週間）のとき、
    月の加算は月単位（1ヵ月以上）のとき（`AGE_BONUS` / `WEEK_MAX` / `MONTH_MAX`）

年齢は `app.player.age`（実セーブの Character に `age` が居るのは実測。値の型は
未実測なので、数字が読めなければ**加算なし**に倒してログに残す）。ボタンの
表示も差し替える値も同じ計算（`compute_stay`）から出すので食い違わない。

## 週単位は「months=1 ＋ 日数の予算」で作る

ゲームの宿泊は月単位しか無い（`period_months`）。週単位の期間は:

1. `period_months` には **1** を渡す（ゲームは最小の1ヵ月の宿泊として進める。
   部屋代も1回ぶんで、ゲームの作りには触らない）
2. 宿泊のマネージャの `execute` の窓の間だけ、`elapse_days` に渡る日数の合計を
   設定の日数（1週間=7日。1ヵ月=30日は `徒歩(3ヵ月)`=`elapse_days(90)` の実測から）
   に切り詰める。予算は `VacationStartManager.execute` ごとに積み直すので、
   連泊（`まだ宿泊する`）の2周目も同じ日数になる
3. ゲームが言う「1ヵ月泊まることにした。」は同じ窓の中でテンプレートに
   置き換える（`STAY_TEXT`。手掛かりは実測の文言 `泊まることにした`）
4. LLM へ出ていく本文の月数も同じ期間へ（次の節）

**日数送りは `elapse_days` を通る**（2026-08-18 の実機で確定。
`elapse_days(30)` が `VacationStartManager.execute` の中で1回 ― 宿泊の開始で
全期間ぶんが一度に進み、その後の活動では1日も動かない）。実機では
`days: 30 -> 14` に切り詰まって成立している。

それでも予算を数える窓は活動マネージャにも張ってある（`STAY_WINDOW_MANAGERS`）。
別経路で進めるビルドへの保険で、`307_` が「日数送りが `elapse_days` を通らない」
ビルドを踏んでいるため。**通らないビルドでは週単位の宿泊が1ヵ月として進む** ―
それを黙って見過ごさないよう、予算が1日も消費されないまま `VacationEndManager`
に着いたら WARN を残す。窓の外の日数送り（エリア移動・依頼）には1バイトも
触らない ― 予算は持っていても、窓の中でしか使わない。

## 週単位のときは LLM の描写の月数も直す

ゲームは LLM に渡す文へ**月数を焼き込んでいる**（実測。2週間の滞在でも
描写だけが「数ヶ月にわたる滞在」と言い続けた）:

    今プレイヤーキャラの…はこのエリアで数ヵ月の宿泊をし、疲労を回復した。

そこで**週単位の宿泊の窓の間だけ**、出ていく本文の `数ヵ月の宿泊` /
`1ヵ月の滞在` を実際の期間（`2週間`）に差し替える（`LLM_STAY_WORDING`。
既定 ON、切れば従来どおり）。仕掛け口はローダの `llm.wrap_outgoing`
（TECH.md §5.3）― ローカルもクラウドも同じ1か所で、`LlamaCppClient.chat` だけに
仕掛けるとクラウド実行で丸ごと落ちる（`119_` が踏んだ）。

**錨は後ろの語（`の宿泊` / `の滞在`）に打つ。** 人生ログや会話の記憶に混じった
「数ヵ月」には当たらない。月単位のときは触らない（ゲームの「数ヵ月」で正しい）。
窓の外は1文字も触らない。書き換えに失敗しても本文はそのまま送られる。

活動ごと（休養・訓練・社交・労働）のプロンプトの実文言はまだ全部読めていない
（`out/events.log` は長い行を途中で切る）。**当たらなかった側を実機から拾える
よう**、宿泊の話に見えるのに手掛かりへ当たらなかった本文は、その一節だけログに
残す（`prompt at ... kept as is:`）。出ていたら錨を足す。

## 部屋の見分けは `quality` の実値で（ラベルの料金は保険と基準値）

`VacationStartManager.__init__(self, app, months, quality)` の `quality` は
**2026-08-18 に実機で観測した**（`GAME_QUALITIES`）:

    犬小屋(0G)='kennel'  簡易寝台(10G)='bunk'
    個室(100G)='private_room'  高級個室(1000G)='luxury_suite'

見分けはこの語彙で行う（`314_` の `kind_of_mode` と同じで、ラベルの文字列には
下がらない）。語彙が変わったビルドのために、部屋選びの画面が並んだ瞬間に
**ラベルの料金**と `quality` の対も控えて保険にする。どちらにも当たらなければ
表示も料金も**全部ゲームのまま**にして、ログに残す。

ラベルの料金はもう1つの役目を持つ ― **前払い調整の基準**（素の宿代）。
画面に実際に出ている値を使うので、ゲームが値上げしたビルドでも合う。

連泊の `まだ宿泊する` も spec は `VacationStartManager`（実測）。ラベルに料金が
無いので表示には触らないが、`quality` は語彙で引けるので、宿代の調整と手持ちの
確認はそのまま効く（**連泊は宿代も日数ももう1回**というのが実測）。

## ボタンは `text` だけ触る（`args` と spec には触らない）

ボタンはセーブに焼かれうる（`PhaseSpec.to_dict()` は text と spec を書く）。spec は
ゲーム自身のものをそのまま残すので、**この MOD を外しても押下の挙動は壊れない**。
画面を開き直せばゲームがラベルを組み直すので、表示も元に戻る。書き換えは
`refresh_choice_buttons` が描く**前**に行うから、`314_` のような塗り直しの
2フレーム目も要らない。自分の書いたラベルは控えておき、二度目に来ても
設定後の料金を素の料金として読み込まない（`314_` の `our_coach_label` と同じ）。

## 宿代は前払い調整（引き落としを1回にする。徴収箇所は推測しない）

宿代の徴収は `VacationStartManager.execute` の中で1回（2026-08-18 の実機で
確定。窓の前後で `gold 19288 -> 19188`＝個室の 100G ちょうど。`314_` の馬車と
同じ形）。前払い調整＋精算の形なので、徴収が別の場所のビルドでも壊れない:

- `execute` の中で素の宿代を1回引くビルド（＝実測のこのビルド）なら、
  引かれる前に「素の宿代 − 設定額」ぶん所持金をずらしてあるので、
  差し引きはちょうど設定額
- `execute` の中で引かないビルドなら、精算（`finally`）で積んだ差額をそのまま
  返す。**設定は効かないが所持金は1Gも狂わない**。その回は `price:` の行が
  「the game did not charge」になるので、ログを添えて報告してもらえば
  徴収箇所を追える（`218_` の出番）

手持ちが設定額に満たないときは押された時点で断る（`on_button_press` で横取り。
画面はそのまま残る）。手持ちが「設定額以上・素の宿代未満」なら、前払い調整で
残高が素の宿代以上になるので、ゲームの残高チェックには弾かれない。

## 隣の MOD との住み分け

- `306_party_train_exp` も `VacationTrainManager.execute` を包む（自前の印方式。
  MethodWatch は使っていないので、こちらが外側から重ねても合成されるだけ。
  この MOD は 306_ より後＝外側に居る）
- 自由生成施設のシーン記述エンジンは `call_phase` で `DisplayVacationChoice` を
  呼べる（GAME.md §2.21）。その経路でも `__init__` を通るので期間の設定は効く
- 部屋の `quality` はゲームに渡したまま（`args` に触らない）ので、部屋ごとの
  背景画像（`change_background_image_to_inn_room(quality)`）や休暇の質の扱いも
  ゲームのまま。**名前と料金を変えても、部屋の中身は元の部屋のまま**

検証の経過は VERIFICATION.md §3.28 へ。実装時に未実測だった4つ（徴収箇所・
`quality` の実値・日数送りの経路・`age` の型）は 2026-08-18 の実機で全部埋まった。
残るのは `period_months` の年齢ごとの境目（実測は 20代=3・31歳=4 の2点だけ）で、
`218_probe_vacation` が `period_months` と `age` を対で録り続ける。
"""

import re

from instantale_modloader import llm, ui

LOG_BASENAME = "vacation_custom.log"

# ボタンには何も足さないが、`ui.Screen` の道具（say）を使うので
# 印のキーは他の MOD と別にして持つ（TECH.md §3.3）。
MARK = "mod_vacation_custom"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ
# 書き込む。TECH.md §3.8）。他のファイルへ移さないこと。
#
# **既定値はすべて素のゲームの値。** 素の値のままなら、この MOD はその項目に
# 一切触らない。変えた項目だけが効く。素の値との照合は下の GAME_* 定数。
#
# 宿泊期間。選択式で、**「デフォルト」でゲームのまま**（素の期間は年齢の
# 変動式 ― モジュール docstring を参照）。
STAY_LENGTH = "デフォルト"

# 年齢による期間の加算（ON/OFF）。「デフォルト」以外を選んだときだけ意味を持つ。
AGE_SCALING = True

# 部屋の名前と宿代。**部屋は4つ**で、料金の素の値は実測（ボタンのラベル
# 0/10/100/1000。犬小屋は 2026-08-18 の実機で見つかった）。
KENNEL_NAME = "犬小屋"
KENNEL_PRICE = 0
SIMPLE_NAME = "簡易寝台"
SIMPLE_PRICE = 10
PRIVATE_NAME = "個室"
PRIVATE_PRICE = 100
LUXURY_NAME = "高級個室"
LUXURY_PRICE = 1000

# ボタンの表示テンプレート。名前か料金を**変えた**部屋だけこれで表示し直す
# （素のままならゲームの表示をそのまま残す）。
ROOM_BUTTON = "{name}({price}G)"

# 宿泊期間のボタンの表示。期間が「デフォルト」以外のときだけこれで表示し直す。
# {length} は加算後の期間（`2週間` / `4ヵ月`）。
STAY_BUTTON = "宿泊する({length})"

# 週単位のときの「Nヵ月泊まることにした。」の置き換え。**空でゲームのまま**
# （そのときは文言が `1ヵ月`、実際の日数が設定値、という食い違いを承知の上）。
# 月単位のときはゲームが正しい月数を言うので触らない。
STAY_TEXT = "{length}泊まることにした。"

# 週単位のとき、LLM へ出ていく本文の中の月数も実際の期間に直すか。
# ゲームは「このエリアで数ヵ月の宿泊をし」と月数を文に焼き込んでいるので
# （実測）、これを切ると**描写だけが「数ヶ月にわたる滞在」と言い続ける**。
LLM_STAY_WORDING = True

# ---------------------------------------------------------------- コード側の設定
# 期間の選択肢。値は (単位, 数)。「デフォルト」だけ None（ゲームのまま）。
STAY_CHOICES = {
    "デフォルト": None,
    "1週間": ("week", 1),
    "2週間": ("week", 2),
    "1ヵ月": ("month", 1),
    "2ヵ月": ("month", 2),
    "3ヵ月": ("month", 3),
    "4ヵ月": ("month", 4),
    "6ヵ月": ("month", 6),
}

# 年齢による加算。(年齢の下限, 加算) を上から見る。20代までは加算なし。
# 週単位なら週数に、月単位なら月数に足す（仕様情報 ― docstring を参照）。
AGE_BONUS = ((50, 3), (40, 2), (30, 1))

# 加算後の上限。
WEEK_MAX = 4
MONTH_MAX = 6

# 1週間＝7日。1ヵ月＝30日は実測（`徒歩(3ヵ月)` = `elapse_days(90)`。GAME.md §2.18）。
WEEK_DAYS = 7

# 素のゲームの部屋。**`quality` の実値 → 部屋**（2026-08-18 に実機で観測。
# `VacationStartManager` の `args` の2番目。GAME.md §2.17）。**実機で観測
# できたものだけ書くこと** ― ゲームの更新で語彙が変わったらここを書き直す。
GAME_QUALITIES = {"kennel": "kennel", "bunk": "simple",
                  "private_room": "private", "luxury_suite": "luxury"}

# **ラベルの料金 → 部屋** の照合表（語彙が変わったビルドでの保険）。値は
# すべて実測（`out/vacation.log` の部屋選びのボタン。犬小屋の 0G を含む）。
GAME_ROOMS = {0: "kennel", 10: "simple", 100: "private", 1000: "luxury"}
GAME_NAMES = {"kennel": "犬小屋", "simple": "簡易寝台", "private": "個室",
              "luxury": "高級個室"}
GAME_PRICES = {"kennel": 0, "simple": 10, "private": 100, "luxury": 1000}

# ラベルから料金と月数を読む形。`個室(100G)` → 100。`宿泊する(3ヵ月)` → 3。
PRICE_RE = re.compile(r"(\d[\d,]*)\s*G")
MONTHS_RE = re.compile(r"(\d+)\s*ヵ月")

# 「Nヵ月泊まることにした。」を見分ける手掛かり（実測の文言）。
STAY_TEXT_MARK = "泊まることにした"

# LLM へ出ていく本文の中の月数。**後ろの語で錨を打つ**ので、人生ログや会話の
# 記憶に混じった「数ヵ月」には当たらない（実測の文言は
# `今プレイヤーキャラの…はこのエリアで数ヵ月の宿泊をし、疲労を回復した。`）。
# 数字入りの `1ヵ月の宿泊` も拾う ― 週単位ではゲームに 1 を渡しているため。
PROMPT_MONTHS_RE = re.compile(
    r"(?:数|[0-9０-９]+)[ヵヶカか]月(?=の(?:宿泊|滞在))")

# 宿泊の話に見えるのに手掛かりへ当たらなかった本文をログに残す上限。
# 活動ごとのプロンプトの実文言はまだ全部は読めていない（`out/events.log` は
# 長い行を途中で切る）ので、**当たらなかった側を実機から拾う**ための口。
PROMPT_MISS_MARKS = ("の宿泊", "の滞在")
PROMPT_MISS_LOG_LIMIT = 4

# 週単位の日数の予算を数える宿泊のマネージャ（すべて targets.txt の実在クラス）。
# `VacationStartManager` は自前の窓（宿代）で数えるのでここには入れない。
STAY_WINDOW_MANAGERS = (
    "VacationRestManager",
    "VacationTrainManager",
    "VacationLaborManager",
    "VacationSocializeManager",
    "VacationSocializeResolveManager",
    "VacationBeggingManager",
    "VacationEndManager",
)

# 手持ちが設定した宿代に満たないときに出す一言。
REFUSE_TEXT = "（{name}の宿代{price}Gに足りない ― 手持ち{gold}G）"


class _SafeDict(dict):
    """テンプレートに無い変数名が来ても落とさない（`{typo}` はそのまま残る）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def fmt(template, **values):
    """設定のテンプレートを埋める。壊れたテンプレートでも素の文字列で返す。"""
    try:
        return str(template).format_map(_SafeDict(values))
    except Exception:
        return str(template)


def parse_price(text):
    """ラベルから料金を読む。読めなければ None。"""
    match = PRICE_RE.search(text or "")
    if match is None:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_age(value):
    """`app.player.age` から年齢を読む。型が未実測なので数字ならなんでも拾う。"""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value))
        if match is None:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None


def compute_stay(choice, age, scaling):
    """選んだ期間と年齢から、実際の期間を出す。「デフォルト」なら None。

    ボタンの表示・`period_months` の差し替え・日数の予算の**全部が
    この1つの計算から出る**（表示と実態の一致）。年齢が読めないときは
    加算なし（`age=None`）。

    返り値: {"unit", "count", "months", "days", "length"}。
    `months` はゲームに渡す `period_months`（週単位は 1）。`days` は週単位の
    日数の予算（月単位は None ＝ ゲームの日数送りに触らない）。
    """
    base = STAY_CHOICES.get(str(choice).strip())
    if base is None:
        return None
    unit, count = base
    if scaling and age is not None:
        for floor, add in AGE_BONUS:
            if age >= floor:
                count += add
                break
    count = min(count, WEEK_MAX if unit == "week" else MONTH_MAX)
    if unit == "week":
        return {"unit": unit, "count": count, "months": 1,
                "days": count * WEEK_DAYS,
                "length": "{}週間".format(count)}
    return {"unit": unit, "count": count, "months": count, "days": None,
            "length": "{}ヵ月".format(count)}


def room_conf(slot):
    """その部屋の設定値。"""
    return {
        "kennel": {"name": str(KENNEL_NAME), "price": int(KENNEL_PRICE)},
        "simple": {"name": str(SIMPLE_NAME), "price": int(SIMPLE_PRICE)},
        "private": {"name": str(PRIVATE_NAME), "price": int(PRIVATE_PRICE)},
        "luxury": {"name": str(LUXURY_NAME), "price": int(LUXURY_PRICE)},
    }[slot]


def price_changed(slot):
    """その部屋の宿代が設定で変えられているか。素の値のままなら触らない。"""
    return room_conf(slot)["price"] != GAME_PRICES[slot]


def name_changed(slot):
    return room_conf(slot)["name"] != GAME_NAMES[slot]


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="vacation custom", mark=MARK)

    state = {
        # いま `VacationStartManager.execute` の中に居るかの窓（宿代）。
        "window": None,
        # 週単位の宿泊の、いまの1泊ぶんの日数の予算。
        # {"left": 残り日数, "spent": 消費, "length": "2週間"}。月単位・
        # デフォルトでは None。`VacationStartManager.execute` ごとに積み直す。
        "block": None,
        # 宿泊のマネージャの `execute` の中に居る深さ。予算と文言の
        # 置き換えは**この窓の中でしか**使わない（窓の外の日数送り・文言には
        # 1バイトも触らない）。
        "depth": 0,
        # 画面で観測した「quality → ラベルの素の料金」の対。部屋の見分けは
        # **この対だけ**で行う（モジュール docstring を参照）。
        "price_by_quality": {},
        # 自分が書いたラベル。組み直さない画面でもう一度来たとき、設定後の
        # 料金を素の料金として読み込まないための目印（`314_` と同じ）。
        "our_labels": set(),
        # 一度ログに残した印（毎フレーム書かないため）。
        "logged_unknown": set(),
        "age_warned": False,
        # 宿泊の話に見えるのに手掛かりへ当たらなかった本文を残した数。
        "prompt_misses": 0,
    }

    def slot_of_quality(quality):
        """部屋を見分ける。当たらなければ None（＝何も触らない）。

        **実測した `quality` の語彙が第一**（`GAME_QUALITIES`）。語彙が変わった
        ビルドのために、画面で観測した「quality → ラベルの素の料金」の対も
        保険として見る。どちらにも当たらなければ表示も宿代も**全部ゲームの
        まま**にして、ログに残す（`314_` の `kind_of_mode` と同じ約束 ―
        ラベルの文字列だけで緩く当てて食い違いを作らない）。
        """
        slot = GAME_QUALITIES.get(str(quality))
        if slot is not None:
            return slot
        price = state["price_by_quality"].get(str(quality))
        return GAME_ROOMS.get(price)

    def player_age(app):
        """年齢。読めなければ None（加算なしに倒し、1回だけログに残す）。"""
        age = parse_age(getattr(getattr(app, "player", None), "age", None))
        if age is None and bool(AGE_SCALING) and not state["age_warned"]:
            state["age_warned"] = True
            write("age: cannot read app.player.age; staying without the "
                  "age bonus")
        return age

    def stay_for(app):
        """いまの設定と年齢での実際の期間。「デフォルト」なら None。"""
        return compute_stay(STAY_LENGTH, player_age(app), bool(AGE_SCALING))

    def set_gold(app, value):
        """所持金を書く。**型を保つ**（`901_` と同じ。float の世界に int を混ぜない）。"""
        player = getattr(app, "player", None)
        current = getattr(player, "gold", None)
        player.gold = float(value) if isinstance(current, float) else int(round(value))

    # ============================================================ ボタンの表示
    def relabel_room(entry):
        """部屋のボタン。描かれる前に `text` だけ書き直し、素の料金を対に控える。

        ラベルに料金が無いボタン（連泊の `まだ宿泊する`）には触らない。
        料金が 10/100/1000 のどれでもないビルドも触らない（ログにだけ残す）。
        """
        argv = ui.spec_args(entry)
        if not argv or len(argv) < 2:
            return
        quality = str(argv[1])
        old = entry.get("text") or ""
        if old not in state["our_labels"]:
            price = parse_price(old)
            if price is None:
                return                      # 料金の無いラベル（まだ宿泊する 等）
            # 素の料金はここで控える（前払い調整の基準）。**実際に画面へ
            # 出ている値**を使うので、ゲームが値上げしたビルドでも合う。
            slot = GAME_QUALITIES.get(quality) or GAME_ROOMS.get(price)
            if slot is None:
                if quality not in state["logged_unknown"]:
                    state["logged_unknown"].add(quality)
                    write("unknown room {!r} (quality {!r}); leaving it alone "
                          "(the game may have changed its rooms)".format(
                              old, quality))
                return
            if state["price_by_quality"].get(quality) != price:
                state["price_by_quality"][quality] = price
                write("room: quality {!r} shows {}G -> {}".format(
                    quality, price, slot))
        slot = slot_of_quality(quality)
        if slot is None:
            return
        if not (name_changed(slot) or price_changed(slot)):
            return                          # 素のままの部屋には触らない
        conf = room_conf(slot)
        new = fmt(ROOM_BUTTON, name=conf["name"], price=conf["price"])
        if new != old:
            entry["text"] = new
            state["our_labels"].add(new)
            write("label: {!r} -> {!r} ({})".format(old, new, slot))

    def relabel_stay(entry, stay):
        """`宿泊する(3ヵ月)` のボタン。期間を変えたときだけ書き直す。

        出す期間は加算後の実値（`compute_stay` の `length`）で、押したときに
        `period_months` へ渡す値と同じ計算から出ている。月単位でゲームの月数と
        同じになったときは new == old で素通りする。
        """
        old = entry.get("text") or ""
        new = fmt(STAY_BUTTON, length=stay["length"], months=stay["months"])
        if new != old:
            entry["text"] = new
            write("label: {!r} -> {!r} (stay {})".format(old, new,
                                                         stay["length"]))

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False,
              safe=True)
    def refresh_buttons(orig, self, *args, **kwargs):
        """ボタンが描かれる直前の1か所で、宿泊まわりのラベルだけ書き直す。

        宿の画面を描く入口はここに集まる（部屋選びも施設のメニューも、組んだ
        ボタンはこの呼び出しで表に出る）。spec で名指しして触るので、宿と
        無関係な画面では辞書引きだけで素通りする。
        """
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, (list, tuple)):
                stay = None
                for entry in buttons:
                    if not isinstance(entry, dict):
                        continue
                    cls_name = ui.spec_cls_name(entry)
                    if cls_name == "VacationStartManager":
                        relabel_room(entry)
                    elif cls_name == "DisplayVacationChoice":
                        if stay is None:
                            stay = stay_for(self) or ()
                        if stay:
                            relabel_stay(entry, stay)
        except Exception:
            ctx.log_exc("vacation custom: cannot relabel the buttons")
        return orig(self, *args, **kwargs)

    # ============================================================ 手持ちの確認
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """設定した宿代に手持ちが満たないときは、押された時点で断る。

        ゲームのボタンは触らず押下だけ握る。部屋選びの画面はそのまま残るので、
        別の部屋を選び直せる。対に無い quality は通す（値が読めないことを
        理由に遊びを止めない。`314_` の断り方と同じ）。
        """
        try:
            entry = ui.pressed_entry(self, button_index)
            if isinstance(entry, dict) \
                    and ui.spec_cls_name(entry) == "VacationStartManager":
                argv = ui.spec_args(entry)
                if argv and len(argv) >= 2:
                    slot = slot_of_quality(argv[1])
                    if slot is not None and price_changed(slot):
                        conf = room_conf(slot)
                        gold = ui.gold_of(self)
                        if gold is not None and gold < conf["price"]:
                            write("refused: {} price {} > gold {}".format(
                                slot, conf["price"], gold))
                            screen.say(self, fmt(
                                REFUSE_TEXT, name=conf["name"],
                                price=conf["price"], gold=gold))
                            return None
        except Exception:
            ctx.log_exc("vacation custom: price check failed")
        return orig(self, button_index, *args, **kwargs)

    # ============================================================ 宿泊期間
    @ctx.wrap("__main__:DisplayVacationChoice.__init__", required=False, safe=True)
    def vacation_choice_init(orig, self, app, period_months=None,
                             *args, **kwargs):
        """`period_months` を設定の期間へ差し替える。**渡す値を差し替えるだけ**。

        部屋ボタンの `args` の月数も「Nヵ月泊まることにした。」の文言も
        ここから流れるので、下流はすべて設定値で揃う。週単位は 1 を渡し、
        日数は宿泊の窓の中の予算で切り詰める（モジュール docstring）。
        型は来た値に合わせる（spec の args は文字列で来ることがある。
        GAME.md §2.18 の実測と同型）。
        """
        state["block"] = None               # 前の宿泊の予算はここで必ず捨てる
        stay = stay_for(app)
        if stay is not None and period_months is not None:
            try:
                current = int(str(period_months))
            except (TypeError, ValueError):
                current = None
            if current is not None and current != stay["months"]:
                replacement = stay["months"]
                try:
                    replacement = type(period_months)(stay["months"])
                except Exception:
                    pass
                write("stay: period_months {!r} -> {!r} ({})".format(
                    period_months, replacement, stay["length"]))
                period_months = replacement
        return orig(self, app, period_months, *args, **kwargs)

    # ============================================================ 宿代と予算
    @ctx.wrap("__main__:VacationStartManager.__init__", required=False, safe=True)
    def start_init(orig, self, app, months=None, quality=None, *args, **kwargs):
        """`execute` には引数が来ないので、月数と部屋をここで自分に控える。

        自分専用の属性名で持つだけで、ゲームのデータには何も書かない
        （マネージャはセーブに入らない。セーブに入るのは PhaseSpec のほう）。
        """
        result = orig(self, app, months, quality, *args, **kwargs)
        try:
            self._mod_vacation_custom = {"months": str(months),
                                         "quality": str(quality)}
        except Exception:
            pass
        return result

    def settle_price(app, window):
        """宿泊の開始が終わった時点の帳尻（`314_` の `settle_fare` と同じ形）。

        前払い調整が入っていれば、ゲームの引き落としは既にちょうど設定額に
        なっている。ここで見るのは**引き落としが本当に起きたか**だけ:
        起きていなければ（徴収が別の場所のビルド）、前払いで積んだ差額を
        そのまま返す。想定外の値になっていたら触らずに WARN を残す。
        """
        before = window.get("gold_before")
        prepaid = window.get("prepaid")
        if before is None or prepaid is None:
            return
        after = ui.gold_of(app)
        if after is None:
            write("WARN price: cannot re-read the gold; leaving it as is")
            return
        conf = room_conf(window["slot"])
        expected = before - conf["price"]
        if after == expected:
            write("price: charged {} in one deduction; gold {} -> {} ({})".format(
                conf["price"], before, after, window["slot"]))
        elif after == prepaid:
            set_gold(app, before)
            write("price: the game did not charge inside execute; gold back to "
                  "{} ({}) -- the charge happens elsewhere in this build; "
                  "run 218_probe_vacation".format(before, window["slot"]))
        else:
            write("WARN price: gold ended at {} (adjusted {}, expected {}); "
                  "leaving it as is ({})".format(
                      after, prepaid, expected, window["slot"]))

    @ctx.wrap("__main__:VacationStartManager.execute", required=False)
    def start_execute(orig, self, choice_text=None, *args, **kwargs):
        """宿泊の開始の間だけ窓を開ける。宿代の前払い調整と、週単位の予算の
        積み直しはこの窓の中だけ。

        徴収箇所は未実測なので、前払い＋精算の形で「引くビルドなら設定額
        ちょうど、引かないビルドなら1Gも狂わない」に倒す（モジュール
        docstring を参照）。連泊（`まだ宿泊する`）も同じ窓を通るので、
        再徴収するビルドなら2周目も設定額になり、週単位の予算も積み直る。
        """
        app = getattr(self, "app", None) or ui.find_app()
        window = None
        info = getattr(self, "_mod_vacation_custom", None) or {}
        # ---- 週単位の予算。ゲームに渡した月数（=1）の宿泊にだけ積む。
        #      設定を途中で変えた・素の宿泊が進行中、という月数が合わない
        #      宿泊では積まない（文言も日数もゲームのまま）。
        try:
            stay = stay_for(app)
            try:
                months = int(str(info.get("months")))
            except (TypeError, ValueError):
                months = None
            if stay is not None and stay["days"] is not None \
                    and months == stay["months"]:
                state["block"] = {"left": int(stay["days"]), "spent": 0,
                                  "length": stay["length"]}
                write("stay: block of {} day(s) for {} (quality {!r})".format(
                    stay["days"], stay["length"], info.get("quality")))
            else:
                state["block"] = None
        except Exception:
            ctx.log_exc("vacation custom: cannot open the day budget")
            state["block"] = None
        # ---- 宿代の窓。
        try:
            slot = slot_of_quality(info.get("quality"))
            if slot is not None and price_changed(slot):
                window = {
                    "slot": slot,
                    "gold_before": ui.gold_of(app),
                    "prepaid": None,
                    "game_price": state["price_by_quality"].get(
                        str(info.get("quality")), GAME_PRICES[slot]),
                }
                write("stay: slot={} quality={!r} months={!r} choice={!r} "
                      "gold={}".format(slot, info.get("quality"),
                                       info.get("months"), choice_text,
                                       window["gold_before"]))
            state["window"] = window
        except Exception:
            ctx.log_exc("vacation custom: cannot open the stay window")
            window = None
        # 前払い調整は窓が立ってから最後に行う。`prepaid` を入れるのは所持金を
        # 実際に書けた後 ― ここで何が起きても `finally` の帳尻が見る。
        # 手持ちが設定額以上なら、調整後は必ず素の宿代以上になるので、ゲームの
        # 残高チェックには弾かれない（設定額未満は押された時点で断っている）。
        if window is not None and window["gold_before"] is not None:
            try:
                conf = room_conf(window["slot"])
                pre = window["gold_before"] + window["game_price"] \
                    - conf["price"]
                set_gold(app, pre)
                window["prepaid"] = pre
                write("price: gold {} -> {} before the game charges {} "
                      "(ours is {}; one deduction, no refund)".format(
                          window["gold_before"], pre, window["game_price"],
                          conf["price"]))
            except Exception:
                ctx.log_exc("vacation custom: cannot pre-adjust the price")
        state["depth"] += 1
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            state["depth"] -= 1
            state["window"] = None
            if window is not None:
                try:
                    settle_price(app, window)
                except Exception:
                    ctx.log_exc("vacation custom: cannot settle the price")

    # ------------------------------------------------ 週単位の予算が効く窓
    def install_stay_window(cls_name):
        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False)
        def stay_window(orig, self, choice_text=None, *args, **kwargs):
            """宿泊の各段の間だけ窓を開ける。日数の予算はこの窓の中だけ。"""
            state["depth"] += 1
            try:
                return orig(self, choice_text, *args, **kwargs)
            finally:
                state["depth"] -= 1
                if cls_name == "VacationEndManager":
                    block = state["block"]
                    state["block"] = None
                    if block is not None and block["spent"] == 0:
                        write("WARN stay: the {} block ended with no "
                              "elapse_days call; this build may not elapse "
                              "the stay through elapse_days -- run "
                              "218_probe_vacation".format(block["length"]))

    for name in STAY_WINDOW_MANAGERS:
        install_stay_window(name)

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False)
    def elapse_days(orig, self, days, *args, **kwargs):
        """宿泊の窓の間だけ、渡る日数の合計を週単位の予算に合わせる。

        **渡す数を差し替えるだけ**で、暦の進め方も日次処理もゲームのまま
        （`orig` は必ず呼ぶ）。窓の外では1バイトも触らない（`314_` と同じ）。
        月単位・デフォルトでは予算そのものが無い。
        """
        try:
            block = state["block"]
            if block is not None and state["depth"] > 0 \
                    and isinstance(days, (int, float)) \
                    and not isinstance(days, bool) and days > 0:
                granted = max(0, min(int(days), block["left"]))
                block["left"] -= granted
                block["spent"] += granted
                if granted != days:
                    write("days: {} -> {} ({} spent {} left {})".format(
                        days, granted, block["length"], block["spent"],
                        block["left"]))
                return orig(self, granted, *args, **kwargs)
        except Exception:
            ctx.log_exc("vacation custom: cannot adjust the days")
        return orig(self, days, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context=None, *args, **kwargs):
        """週単位のときだけ「1ヵ月泊まることにした。」を実際の期間に直す。

        窓の中で、名指しの手掛かりに当たった文言だけ。テンプレートが空なら
        ゲームのまま。月単位はゲームが正しい月数を言うので触らない。
        """
        try:
            block = state["block"]
            if block is not None and state["depth"] > 0 \
                    and isinstance(context, str) and STAY_TEXT_MARK in context:
                template = str(STAY_TEXT)
                if template:
                    replaced = fmt(template, length=block["length"])
                    if replaced != context:
                        write("text: {!r} -> {!r}".format(context, replaced))
                        return orig(self, replaced, *args, **kwargs)
        except Exception:
            ctx.log_exc("vacation custom: cannot reword the stay text")
        return orig(self, context, *args, **kwargs)

    # ================================================ LLM へ出ていく文の月数
    def rewrite_outgoing(texts, site):
        """週単位の宿泊の間だけ、出ていく本文の月数を実際の期間に直す。

        ゲームは「このエリアで**数ヵ月**の宿泊をし」と月数を文に焼き込んで
        LLM に渡す（実測）。2週間の滞在でも描写だけが「数ヶ月にわたる滞在」に
        なるので、ボタン・ゲームの文言と同じ値に揃える。

        **窓の中の、週単位の宿泊のときだけ**（月単位はゲームの「数ヵ月」で
        正しい。窓の外は1文字も触らない）。仕掛け先の選択と「1回の推論で
        1回だけ」はローダ側（`llm.wrap_outgoing`）が持つ ― ここは**何をどう
        書き換えるか**だけを持つ。`LlamaCppClient.chat` だけに仕掛けると
        クラウド実行で丸ごと落ちる（`119_` が踏んだ。TECH.md §5.3）。

        窓（`state["depth"]`）は**わざとスレッドをまたぐ形**にしてある。宿泊の
        `execute` はワーカースレッドで走り（実測）、`send_request` はそこから
        さらに別スレッドへ渡りうる。スレッドローカルにすると、渡った先で窓が
        閉じて見えて書き換えが落ちる。またいだぶん無関係な推論に当たりうるが、
        錨が `の宿泊` / `の滞在` なので実害は無い。

        書き換えに失敗しても本文はそのまま送られる（ローダが握る）。描写の
        月数がずれるだけで、遊びは止めないほうがよい。
        """
        block = state["block"]
        if block is None or state["depth"] <= 0:
            return None
        result, changed = [], False
        for content in texts:
            new = PROMPT_MONTHS_RE.sub(block["length"], content)
            if new != content:
                changed = True
                write("prompt at {}: months -> {}".format(site, block["length"]))
            elif any(mark in content for mark in PROMPT_MISS_MARKS)                     and state["prompt_misses"] < PROMPT_MISS_LOG_LIMIT:
                # 活動ごとの実文言はまだ全部読めていない。当たらなかった側を
                # 実機から拾えるよう、その一節だけ残す（前後 40 字）。
                state["prompt_misses"] += 1
                for mark in PROMPT_MISS_MARKS:
                    at = content.find(mark)
                    if at >= 0:
                        write("prompt at {} kept as is: ...{}...".format(
                            site, content[max(0, at - 40):at + 40]))
                        break
            result.append(new)
        return result if changed else None

    if LLM_STAY_WORDING:
        # 週単位を選んでいなくても仕掛けておく（設定は遊んでいる最中に
        # 変わりうる）。窓の外・月単位では `rewrite_outgoing` が即 None を
        # 返すので、他の推論の邪魔はしない。
        llm.wrap_outgoing(ctx, rewrite_outgoing, label="vacation custom")

    # ------------------------------------------------------------ 自己検証
    # 実経路は宿に泊まるまで通らない。ラベルの読み書きと期間の計算だけは
    # 作ったデータで先に確かめておく（`314_` と同じ方針）。
    parsed = parse_price("個室(1,000G)")
    sample = fmt(ROOM_BUTTON, name="大部屋", price=30)
    survives = fmt("{name}と{typo}", name="個室")
    def stay_field(choice, age, scaling, key):
        stay = compute_stay(choice, age, scaling)
        return None if stay is None else stay.get(key)

    stays = (
        compute_stay("デフォルト", 55, True) is None,
        compute_stay("1週間", 25, True) == {"unit": "week", "count": 1,
                                            "months": 1, "days": 7,
                                            "length": "1週間"},
        stay_field("1週間", 35, True, "length") == "2週間",
        stay_field("2週間", 55, True, "days") == 28,      # 2+3 → 上限4週間
        stay_field("1ヵ月", 42, True, "months") == 3,
        stay_field("3ヵ月", 47, True, "months") == 5,
        stay_field("4ヵ月", 61, True, "months") == 6,     # 4+3 → 上限6ヵ月
        stay_field("6ヵ月", 20, True, "months") == 6,
        stay_field("2週間", 55, False, "days") == 14,     # 加算OFF
        stay_field("1週間", None, True, "days") == 7,     # 年齢が読めない
        parse_age("28歳") == 28 and parse_age(None) is None,
    )
    if parsed == 1000 and sample == "大部屋(30G)" \
            and survives == "個室と{typo}" and all(stays):
        ctx.log("verified: reads prices from labels, formats templates, and "
                "computes the stay lengths with the age bonus")
    else:
        ctx.log("VERIFY FAILED: parsed={!r} sample={!r} survives={!r} "
                "stays={!r}".format(parsed, sample, survives, stays),
                level="ERROR")
    if str(STAY_LENGTH).strip() not in STAY_CHOICES:
        ctx.log("STAY_LENGTH={!r} is not one of {}; staying with the game's "
                "own period".format(STAY_LENGTH, "/".join(STAY_CHOICES)),
                level="WARN")

    ctx.log("vacation custom: stay={!r} age_bonus={} rooms={}/{}G {}/{}G "
            "{}/{}G {}/{}G log={}".format(
                STAY_LENGTH, "on" if AGE_SCALING else "off",
                KENNEL_NAME, KENNEL_PRICE, SIMPLE_NAME, SIMPLE_PRICE,
                PRIVATE_NAME, PRIVATE_PRICE, LUXURY_NAME, LUXURY_PRICE,
                log_path))
