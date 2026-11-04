# -*- coding: utf-8 -*-
"""機能調整: 宿の宿泊（期間・部屋の名前・宿代）を設定で変えられるようにする。

素のゲームの宿泊は、期間が3か月+年齢加算、部屋が4つに固定されている。
宿泊の流れ・`quality` の実値・日数と宿代が `VacationStartManager.execute` の中で
1回ずつ動くこと・LLM の本文に月数が焼き込まれていることが判明している(GAME.md
§2.17)。
この MOD は期間・部屋の呼び名・宿代を mod.json の設定から変えられるようにする。
既定値はすべて素のゲームの値（期間は「デフォルト」＝ゲームのまま）で、
そのままなら挙動は何も変わらない。
変えた項目だけが効く。

変更点は7つ。

| 何を変えるか | どこで変えるか |
|---|---|
| 部屋の名前・宿代・ボタンの表示 | ローダの窓口 `prices` に答え（額・呼び名・ボタンの形）を置く。ボタンの書き直し・手持ちの断り・前払い調整はローダの宿代の関所（`prices.install_inn`。TECH.md §3.3.4）が持つ |
| 宿泊期間（月単位） | `DisplayVacationChoice.__init__` に渡る `period_months` を差し替える |
| 宿泊期間（週単位） | `period_months` は 1 にして、宿泊の窓の間だけローダの日数送りの関所へ「残りは何日か」を出す |
| LLM の描写の月数 | 窓の間だけ、出ていく本文の `数ヵ月の宿泊` を実際の期間へ（`llm.wrap_outgoing`） |
| 宿の主への指示文 | `conversation_facilitator` と `_after_retrieval` の `response_prompt_instruction` の一文を、今の期間・部屋の名前・宿代へ |
| 宿の主の記録 | `VacationStartManager.execute` の間に主の `life_log` へ増えた記録の期間・部屋の名前・日付の範囲を実際の値へ |
| 主人公の活動の記録 | 宿泊の各段の間に主人公の `life_log` へ増えた `生活の記憶` の日付の範囲を、週単位の日数へ |

ボタンに出す期間・料金は設定値そのもので、ゲームに渡す値も同じ設定値。
年齢の加算まで含めて`compute_stay` の1か所から出すので食い違わない。

留意点:

- 週単位は「`months=1` ＋ 日数の予算」で作る。
  ゲームの宿泊は月単位しか無いので、部屋代1回ぶんの最小の宿泊として進めさせ、
  窓の間に進む日数の合計だけを設定の日数へ切り詰める。
  切り詰めるのは**ローダの関所**（`durations`。TECH.md §3.3.3）で、
  `elapse_days` はこちらでは包まない。こちらは窓の間だけ「残りの予算まで」と望みを出す。
  予算は `VacationStartManager.execute` ごとに積み直すので、
  連泊（`まだ宿泊する`）の2周目も同じ日数になる。
  日数送りが `elapse_days` を通らないビルドへの保険として活動マネージャにも窓を張ってあり（`STAY_WINDOW_MANAGERS`）、
  1日も消費されないまま終わったら WARN を残す。
  窓の外の日数送り（エリア移動・依頼）には1バイトも触らない
- 年齢の加算はゲームの仕様に寄せる。
  ゲーム自身の期間が年齢の変動式なので、
  選んだ期間にも加算を掛けられる（`AGE_BONUS`。既定 ON）。
  `app.player.age` が数字として読めなければ加算なしに倒してログに残す
- 週単位のときは LLM の描写の月数も直す。
  錨は後ろの語（`の宿泊` / `の滞在`）に打つので、
  人生ログや会話の記憶に混じった「数ヵ月」には当たらない。
  月単位のときは触らない。
  仕掛け口はローダの `llm.wrap_outgoing`（TECH.md §5.3）。
  ローカルもクラウドも同じ1か所で、
  `LlamaCppClient.chat` だけに仕掛けるとクラウド実行で丸ごと落ちる（`119_` が踏んだ）。
  当たらなかった本文はその一節だけログへ残す
- 部屋の見分け・ラベルの素の料金の控え・前払い調整・精算は、v1.15.1 でローダの宿代の関所へ移した
  （`prices.install_inn`。中身は移す前のこの MOD の形のまま）。
  街の出来事（`338_`）のような宿代へ倍率を掛ける MOD が、この MOD の答えの上に段を重ねられるようにするため。
  こちらは部屋ごとの答え（額・呼び名・ボタンの形）を置くだけで、素のままの部屋では答えない
- **ボタンは `text` だけ触る**（spec と `args` はゲーム自身のものを残す）ので、
  この MOD を外しても押下の挙動は壊れない。
  `refresh_choice_buttons` が描く前に書くため、
  `314_` のような次フレームの塗り直しは要らない。
  `306_party_train_exp` も `VacationTrainManager.execute` を包むが、
  こちらは外側から重なるだけ

遊び方の説明は MODS.md の `315_` の項、検証の経過は VERIFICATION.md §3.28。

版9: 宿泊のフック（`DisplayVacationChoice.__init__`・`VacationStartManager.__init__`・`execute`・各段の `execute`・`add_text`）は、
引数を受け取った形のまま元の関数へ渡す（名前付きで受けて位置引数に直さない。TECH.md の規則の表）。
月数と文言の差し替えは来た側（位置かキーワード）に合わせる。
同じ版で、宿の主に渡る2つの文を今の設定に揃えた（GAME.md §2.17、VERIFICATION.md §3.28）。
会話の指示文（`response_prompt_instruction`）の期間・部屋の名前・宿代と、宿泊のたびに主の `life_log` へ入る記録。
それまでは1週間の設定でも主が「三ヵ月単位の宿だよ」と言い、記録には「1ヵ月間」と残っていた。
主人公の活動の記録（文に期間は無い）も、週単位なら日付の範囲を実際の日数へ縮める。
"""

import os
import sys
import re
import time

from instantale_modloader import choices, durations, frames, llm, prices, ui

LOG_BASENAME = "vacation_custom.log"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_vacation_custom_store__"

# ボタンには何も足さないが、`ui.Screen` の道具（say）を使うので印のキーは他の MOD と別にして持つ（TECH.md
# §3.3）。
MARK = "mod_vacation_custom"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む。TECH.md
# §3.8）。
# 他のファイルへ移さないこと。
# 既定値はすべて素のゲームの値。
# 素の値のままなら、この MOD はその項目に一切触らない。
# 変えた項目だけが効く。
# 素の値との照合は下の GAME_* 定数。
# 宿泊期間。
# 選択式で、**「デフォルト」でゲームのまま**（素の期間は年齢の変動式。
# モジュール docstring を参照）。
STAY_LENGTH = "デフォルト"

# 年齢による期間の加算（ON/OFF）。
# 「デフォルト」以外を選んだときだけ意味を持つ。
AGE_SCALING = True

# 部屋の名前と宿代。
# 部屋は4つで、料金の素の値は実測（ボタンのラベル 0/10/100/1000。
# 犬小屋は実機で見つかった）。
KENNEL_NAME = "犬小屋"
KENNEL_PRICE = 0
SIMPLE_NAME = "簡易寝台"
SIMPLE_PRICE = 10
PRIVATE_NAME = "個室"
PRIVATE_PRICE = 100
LUXURY_NAME = "高級個室"
LUXURY_PRICE = 1000

# ボタンの表示テンプレート。
# 名前か料金を変えた部屋だけこれで表示し直す（素のままならゲームの表示をそのまま残す）。
ROOM_BUTTON = "{name}({price}G)"

# 宿泊期間のボタンの表示。
# 期間が「デフォルト」以外のときだけこれで表示し直す。
# {length} は加算後の期間（`2週間` / `4ヵ月`）。
STAY_BUTTON = "宿泊する({length})"

# 週単位のときの「Nヵ月泊まることにした。」の置き換え。
# 空でゲームのまま（そのときは文言が `1ヵ月`、実際の日数が設定値、
# という食い違いを承知の上）。
# 月単位のときはゲームが正しい月数を言うので触らない。
STAY_TEXT = "{length}泊まることにした。"

# AI に渡る文の宿泊の話を、実際の期間・部屋の名前・宿代に揃えるか。
# ゲームは「このエリアで数ヵ月の宿泊をし」（週単位の描写）、宿の主への指示文、
# 主の記録に素の値を焼き込んでいるので（実測）、
# これを切ると**描写と宿の主だけが素の期間と値段を言い続ける**。
LLM_STAY_WORDING = True

# ---------------------------------------------------------------- コード側の設定
# 期間の選択肢。
# 値は (単位, 数)。
# 「デフォルト」だけ None（ゲームのまま）。
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

# 年齢による加算。
# (年齢の下限, 加算) を上から見る。
# 20代までは加算なし。
# 週単位なら週数に、月単位なら月数に足す（仕様情報。docstring を参照）。
AGE_BONUS = ((50, 3), (40, 2), (30, 1))

# 加算後の上限。
WEEK_MAX = 4
MONTH_MAX = 6

# 1週間＝7日。
# 1ヵ月＝30日は実測（`徒歩(3ヵ月)` = `elapse_days(90)`。GAME.md §2.18）。
WEEK_DAYS = 7

# 素のゲームの部屋。
# **`quality` の実値 → 部屋**（実機で観測。
# `VacationStartManager` の `args` の2番目。GAME.md §2.17）。
# **実機で観測できたものだけ書くこと**。
# ゲームの更新で語彙が変わったらここを書き直す。
GAME_QUALITIES = {"kennel": "kennel", "bunk": "simple",
                  "private_room": "private", "luxury_suite": "luxury"}

GAME_NAMES = {"kennel": "犬小屋", "simple": "簡易寝台", "private": "個室",
              "luxury": "高級個室"}
GAME_PRICES = {"kennel": 0, "simple": 10, "private": 100, "luxury": 1000}

# ラベルから月数を読む形。
# `宿泊する(3ヵ月)` → 3。
# 料金を読むのはローダの語彙（`ui.parse_coin`。`314_` と共有）。
# `個室(100G)` → 100。通貨の表記が差し替えられていれば
# （`130_`）`個室(100円)` も読む。
MONTHS_RE = re.compile(r"(\d+)\s*ヵ月")

# 「Nヵ月泊まることにした。」を見分ける手掛かり（実測の文言）。
STAY_TEXT_MARK = "泊まることにした"

# LLM へ出ていく本文の中の月数。
# 後ろの語で錨を打つので、人生ログや会話の記憶に混じった「数ヵ月」には当たらない（実測の文言は
# `今プレイヤーキャラの…はこのエリアで数ヵ月の宿泊をし、疲労を回復した。`）。
# 数字入りの `1ヵ月の宿泊` も拾う。
# 週単位ではゲームに 1 を渡しているため。
PROMPT_MONTHS_RE = re.compile(
    r"(?:数|[0-9０-９]+)[ヵヶカか]月(?=の(?:宿泊|滞在))")

# 宿泊の話に見えるのに手掛かりへ当たらなかった本文の印。
# 活動ごとのプロンプトの実文言は、
# まだ全部を読めていない（`out/events.log` は長い行を途中で切る）。
# だから**当たらなかった側を実機から拾う**ための口を置く。
PROMPT_MISS_MARKS = ("の宿泊", "の滞在")

# 宿の主との会話の LLM と、その `response_prompt_instruction` の引数の位置（recon の signatures.json）。
KEEPER_TALK_TARGETS = (("scripts.llm.llm_manager:conversation_facilitator", 10),
                       ("scripts.llm.llm_manager:conversation_facilitator_after_retrieval", 9))

# 宿の主への指示文の一文（exe の定数。GAME.md §2.17）。
# 金額の後ろは見ない。`130_` が `scripts.languages:tr` の段で `10G` を `10円` へ直していることがあるため。
INN_TERMS_RE = re.compile(
    r"(\d+)ヵ月単位の宿泊が可能であり、前払いで簡易寝台.*?、個室.*?、高級個室.*?が必要な事")
INN_TERMS = ("{length}単位の宿泊が可能であり、前払いで{simple}{simple_price}G、"
             "{private}{private_price}G、{luxury}{luxury_price}Gが必要な事")
INN_TERMS_MARK = "宿泊をしたいと伝えられた場合"
INN_KENNEL_TEXT = "犬小屋なら宿泊無料であることも"
INN_KENNEL_FREE = "{name}なら宿泊無料であることも"
INN_KENNEL_PAID = "{name}なら{price}Gで泊まれることも"

# 宿泊のたびに宿の主の `life_log` へ入る記録（exe の定数と `out/npc_memory.log` の実物）。
# `〈生活の記憶〉<主人公>を高級個室に1ヵ月間宿泊させた。\n`
KEEPER_MEMORY_RE = re.compile(
    r"(〈生活の記憶〉.*?を)(犬小屋|簡易寝台|個室|高級個室)に(\d+ヵ月間)宿泊させた")
# 主人公の活動の記録の印（`〈生活の記憶〉…しばらく休暇をとり`、`<生活の記憶>…自己鍛錬に費やした`）。
KEEPER_MEMORY_MARK = "生活の記憶"

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

#: 設定のテンプレートを埋める（知らない変数名は残し、通貨の表記を今の表記へ直す）。
#: ローダの語彙（`314_` / `315_` / `332_` で共有）。
fmt = ui.fill_template


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

    ボタンの表示・`period_months` の差し替え・日数の予算の**全部がこの1つの計算から出る**（表示と実態の一致）。
    年齢が読めないときは加算なし（`age=None`）。

    返り値: {"unit", "count", "months", "days", "length"}。
    `months` はゲームに渡す `period_months`（週単位は 1）。
    `days` は週単位の日数の予算（月単位は None ＝ ゲームの日数送りに触らない）。
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

    # **置き場は `sys`。** `apply()` は1プロセスで何度も走り、
    # 当て直しは背景スレッドの `boot()` から来る（未 import のモジュールが
    # 現れた時＝最初の LLM リクエストの時）。移動や滞在の最中にそれが挟まると、
    # ここで作り直した空の器を新しいラッパが握り、窓や予算が None のまま
    # 日数の頭打ちが効かなくなる。「2週間」の滞在が素の30日を、
    # 調整した徒歩が素の90日を消費する。
    # `311_` / `312_` が控えを `sys` に置いているのと同じ理由。
    state = getattr(sys, STATE_STORE_ATTR, None)
    if state is None:
        state = {
            # 週単位の宿泊の、いまの1泊ぶんの日数の予算。
            # {"left": 残り日数, "spent": 消費, "length": "2週間"}。
            # 月単位・デフォルトでは None。
            # `VacationStartManager.execute` ごとに積み直す。
            "block": None,
            # 宿泊のマネージャの `execute` の中に居る深さ。
            # 予算と文言の置き換えはこの窓の中でしか使わない（窓の外の日数送り・文言には
            # 1バイトも触らない）。
            "depth": 0,
            "age_warned": False,
        }
        setattr(sys, STATE_STORE_ATTR, state)

    def slot_of_quality(quality):
        """部屋を見分ける。当たらなければ None（＝答えない）。

        `quality` はローダの宿代の関所がゲームの語彙へ直して渡す
        （語彙が変わったビルドの保険＝画面のラベルの素の額との対も、関所が持つ）。
        """
        return GAME_QUALITIES.get(str(quality))

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

    # 宿泊の長さはローダの窓口（`durations`）に答えを置く。
    # 読む側（自分の家の滞在を宿屋に合わせる MOD など）は窓口に聞くだけで、
    # この MOD の名前を知らない（TECH.md §3.3.2）。
    # 置くのは**関数**で、値ではない。年齢で変わるうえ、
    # 設定を変えたときに置き直す責任を読む側に持たせないため。
    # 「デフォルト」では None を返し、窓口がゲームの式へ落とす。
    # `ctx.mod_dir` はフックの中では読めない（apply() の間だけ）ので、ここで控える。
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "vacation_custom"
    durations.declare(durations.INN_STAY, stay_for, owner=owner, write=write)

    # 宿代も同じ約束でローダの窓口へ置く（`prices`）。
    # 答えは額・呼び名・ボタンの形。ボタンの書き直し・手持ちの断り・前払い調整は
    # ローダの宿代の関所が引き受ける（TECH.md §3.3.4）。街の出来事（`338_`）のような段は、この額の上に掛かる。
    # 自分の建物での滞在を無料にする MOD は、ゲームに引かせてから返すのではなく、
    # 窓口で聞いた額を先に足しておく（前払い調整）。
    # 部屋が見分けられないときと、額も呼び名も素のままの部屋では None を返し、窓口がゲームの値へ落とす
    # （素の額は窓口がラベルから控える。ここで設定の額を答えると、ゲームが額を変えた版で旧い額へ書き換えてしまう）。
    def room_price_for(app, quality=None):
        slot = slot_of_quality(quality)
        if slot is None or not (price_changed(slot) or name_changed(slot)):
            return None
        conf = room_conf(slot)
        return {"price": int(conf["price"]), "name": conf["name"], "label": str(ROOM_BUTTON)}

    prices.declare(prices.INN_ROOM, room_price_for, owner=owner, write=write)
    prices.install_inn(ctx, write)

    # ============================================================ ボタンの表示
    def relabel_stay(entry, stay):
        """`宿泊する(3ヵ月)` のボタン。期間を変えたときだけ書き直す。

        出す期間は加算後の実値（`compute_stay` の `length`）で、
        押したときに `period_months` へ渡す値と同じ計算から出ている。
        月単位でゲームの月数と同じになったときは new == old で素通りする。
        """
        old = entry.get("text") or ""
        new = fmt(STAY_BUTTON, length=stay["length"], months=stay["months"])
        if new != old:
            entry["text"] = new
            write("label: {!r} -> {!r} (stay {})".format(old, new,
                                                         stay["length"]))

    def refresh_buttons(self, _buttons):
        """ボタンが描かれる直前の1か所で、宿泊の期間のラベルだけ書き直す（部屋のボタンは関所）。

        spec で名指しして触るので、宿と無関係な画面では辞書引きだけで素通りする。
        """
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, (list, tuple)):
                stay = None
                for entry in buttons:
                    if not isinstance(entry, dict):
                        continue
                    if ui.spec_cls_name(entry) == "DisplayVacationChoice":
                        if stay is None:
                            stay = stay_for(self) or ()
                        if stay:
                            relabel_stay(entry, stay)
        except Exception:
            ctx.log_exc("vacation custom: cannot relabel the buttons")

    # 選択肢はローダの窓口 `choices`（TECH.md §3.3.14）に預ける。
    choices.provide(ctx, screen, refresh=refresh_buttons)

    # ============================================================ 宿泊期間
    @ctx.wrap("__main__:DisplayVacationChoice.__init__", required=False, safe=True)
    def vacation_choice_init(orig, self, *args, **kwargs):
        """`period_months` を設定の期間へ差し替える。渡す値を差し替えるだけ。

        部屋ボタンの `args` の月数も「Nヵ月泊まることにした。」
        の文言もここから流れるので、下流はすべて設定値で揃う。
        週単位は 1 を渡し、日数は宿泊の窓の中の予算で切り詰める（モジュール
        docstring）。
        型は来た値に合わせる（spec の args は文字列で来ることがある。
        GAME.md §2.18 の実測と同型）。
        """
        state["block"] = None               # 前の宿泊の予算はここで必ず捨てる
        # (app, period_months)。受け取った形のまま渡し、月数だけを来た側に合わせて差し替える。
        app = frames.arg(args, kwargs, "app", 0)
        period_months = frames.arg(args, kwargs, "period_months", 1)
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
                args, kwargs, _ = frames.replace_arg(args, kwargs, "period_months", 1, replacement)
        return orig(self, *args, **kwargs)

    # ============================================================ 宿代と予算
    @ctx.wrap("__main__:VacationStartManager.__init__", required=False, safe=True)
    def start_init(orig, self, *args, **kwargs):
        """`execute` には引数が来ないので、月数と部屋をここで自分に控える。

        自分専用の属性名で持つだけで、ゲームのデータには何も書かない（マネージャはセーブに入らない。セーブに入るのは
        PhaseSpec のほう）。
        """
        result = orig(self, *args, **kwargs)
        try:
            # (app, months, quality)。受け取った形のまま渡し、ここでは読むだけ。
            months = frames.arg(args, kwargs, "months", 1)
            quality = frames.arg(args, kwargs, "quality", 2)
            self._mod_vacation_custom = {"months": str(months),
                                         "quality": str(quality)}
        except Exception:
            pass
        return result

    # ------------------------------------------------ 宿の主の記録
    def keeper_memory_mark(app):
        """宿の主と、宿泊の前の記録の件数を控える。主が読めなければ None。"""
        if not LLM_STAY_WORDING:
            return None
        try:
            location = getattr(getattr(app, "player", None), "location", None)
            keeper = ui.character_of(app, getattr(location, "owner", None))
            logs = getattr(keeper, "life_log", None)
            if not isinstance(logs, list):
                return None
            return {"keeper": keeper, "size": len(logs)}
        except Exception:
            ctx.log_exc("vacation custom: cannot read the keeper's memory")
            return None

    def correct_memory_range(entry, days, today):
        """週単位の記録の日付の範囲を実際の日数へ縮める。

        ゲームは宿泊を終えた日を `day_end`、その月数×30日前を `day_start` に書く（GAME.md §2.17）。
        """
        start, end = entry.get("day_start"), entry.get("day_end")
        if not isinstance(start, int) or not isinstance(end, int) or end - start <= days:
            return                          # 実際の日数に収まる記録（1日の出来事など）はそのまま
        if end == today:
            entry["day_start"] = end - days
        else:
            write("WARN memory: the range {}-{} does not end today ({}); kept as is".format(
                start, end, today))

    def correct_keeper_memory(app, mark):
        """宿泊の間に主の記録へ増えた1行を、実際の期間・部屋の名前・日付の範囲へ直す。

        増えた分だけを見るので、前からある記録には触らない。
        """
        if mark is None:
            return
        try:
            block = state["block"]
            logs = getattr(mark["keeper"], "life_log", None)
            added = logs[mark["size"]:] if isinstance(logs, list) else []
            names = {name: slot for slot, name in GAME_NAMES.items()}

            def reword(match):
                length = block["length"] if block is not None else match.group(3)
                return "{}{}に{}宿泊させた".format(
                    match.group(1), room_conf(names[match.group(2)])["name"], length)

            for entry in added:
                content = entry.get("content") if isinstance(entry, dict) else None
                if not isinstance(content, str) or KEEPER_MEMORY_RE.search(content) is None:
                    continue
                replaced = KEEPER_MEMORY_RE.sub(reword, content)
                if block is not None:
                    correct_memory_range(entry, int(block["days"]), ui.game_day(app))
                if replaced != content:
                    entry["content"] = replaced
                write("memory: {!r} {}-{}".format(
                    replaced.strip(), entry.get("day_start"), entry.get("day_end")))
                return
            write("memory: no new stay record on the keeper ({} added)".format(len(added)))
        except Exception:
            ctx.log_exc("vacation custom: cannot correct the keeper's memory")

    # ------------------------------------------------ 主人公の活動の記録
    def player_memory_mark(app):
        """週単位の宿泊の活動の前に、主人公の記録の件数を控える。対象でなければ None。"""
        if not LLM_STAY_WORDING or state["block"] is None:
            return None
        logs = getattr(getattr(app, "player", None), "life_log", None)
        return len(logs) if isinstance(logs, list) else None

    def correct_player_memory(app, size):
        """活動の間に主人公の記録へ増えた `生活の記憶` の範囲を実際の日数へ縮める。文に期間は無い。"""
        block = state["block"]
        if size is None or block is None:
            return
        try:
            logs = getattr(getattr(app, "player", None), "life_log", None)
            for entry in logs[size:] if isinstance(logs, list) else ():
                if isinstance(entry, dict) and KEEPER_MEMORY_MARK in str(entry.get("content")):
                    correct_memory_range(entry, int(block["days"]), ui.game_day(app))
                    write("memory: player {!r} {}-{}".format(
                        str(entry.get("content")).strip(), entry.get("day_start"), entry.get("day_end")))
        except Exception:
            ctx.log_exc("vacation custom: cannot correct the player's memory")

    @ctx.wrap("__main__:VacationStartManager.execute", required=False)
    def start_execute(orig, self, *args, **kwargs):
        """宿泊の開始の間だけ窓を開け、週単位の予算を積み直す（宿代は関所が持つ）。

        連泊（`まだ宿泊する`）も同じ窓を通るので、週単位の予算も積み直る。
        抜けるときに、ゲームが宿の主へ書いた記録を実際の値へ直す。
        """
        app = getattr(self, "app", None) or ui.find_app()
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
                                  "days": int(stay["days"]),
                                  "length": stay["length"],
                                  # この宿泊が始まった時刻（ローダの関所が
                                  # 「先に始まった事情が決める」で使う）。
                                  "since": time.time()}
                write("stay: block of {} day(s) for {} (quality {!r})".format(
                    stay["days"], stay["length"], info.get("quality")))
            else:
                state["block"] = None
        except Exception:
            ctx.log_exc("vacation custom: cannot open the day budget")
            state["block"] = None
        memory = keeper_memory_mark(app)
        state["depth"] += 1
        try:
            return orig(self, *args, **kwargs)
        finally:
            state["depth"] -= 1
            correct_keeper_memory(app, memory)

    # ------------------------------------------------ 週単位の予算が効く窓
    def install_stay_window(cls_name):
        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False)
        def stay_window(orig, self, *args, **kwargs):
            """宿泊の各段の間だけ窓を開ける。日数の予算はこの窓の中だけ。"""
            app = getattr(self, "app", None) or ui.find_app()
            memory = player_memory_mark(app)
            state["depth"] += 1
            try:
                return orig(self, *args, **kwargs)
            finally:
                state["depth"] -= 1
                correct_player_memory(app, memory)
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

    # `elapse_days` はこちらでは包まない。包むのはローダの関所1枚だけで、
    # ここは「この宿泊であと何日進めてよいか」を答える側に回る（TECH.md §3.3.3）。
    def days_wish(app, days):
        """宿泊の窓の間だけ、**予算までの頭打ち**を望む。窓の外・月単位では None。"""
        block = state["block"]
        if block is None or state["depth"] <= 0:
            return None
        return {"days": max(0, min(int(days), block["left"])),
                "since": block.get("since")}

    def days_note(app, days, granted):
        """実際に進んだ日数を予算から引く。**他所が決めた回も来る**。"""
        block = state["block"]
        if block is None or state["depth"] <= 0:
            return
        block["left"] = max(0, block["left"] - max(0, int(granted)))
        block["spent"] += max(0, int(granted))
        if granted != days:
            write("stay: {} spent {} left {}".format(
                block["length"], block["spent"], block["left"]))

    durations.claim_days(owner, days_wish, note=days_note, write=write)
    durations.install(ctx, write)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, *args, **kwargs):
        """週単位のときだけ「1ヵ月泊まることにした。」を実際の期間に直す。

        窓の中で、名指しの手掛かりに当たった文言だけ。
        テンプレートが空ならゲームのまま。
        月単位はゲームが正しい月数を言うので触らない。
        """
        try:
            block = state["block"]
            if block is not None and state["depth"] > 0:
                context = frames.arg(args, kwargs, "context", 0)
                template = str(STAY_TEXT)
                if isinstance(context, str) and STAY_TEXT_MARK in context and template:
                    replaced = fmt(template, length=block["length"])
                    if replaced != context:
                        write("text: {!r} -> {!r}".format(context, replaced))
                        args, kwargs, _ = frames.replace_arg(args, kwargs, "context", 0, replaced)
        except Exception:
            ctx.log_exc("vacation custom: cannot reword the stay text")
        return orig(self, *args, **kwargs)

    # ================================================ LLM へ出ていく文の月数
    def rewrite_outgoing(texts, site):
        """週単位の宿泊の間だけ、出ていく本文の月数を実際の期間に直す。

        ここが持つのは何をどう書き換えるかだけ。
        仕掛け先の選択と「1回の推論で1回だけ」はローダ側（`llm.wrap_outgoing`）が持つ。

        窓（`state["depth"]`）はわざとスレッドをまたぐ形にしてある。
        宿泊の `execute` はワーカースレッドで走り、
        `send_request` はそこからさらに別のスレッドへ渡りうる。
        スレッドローカルにすると、渡った先で窓が閉じて見えて書き換えが落ちる。
        またいだぶん無関係な推論に当たりうるが、
        錨が `の宿泊` / `の滞在` なので実害は無い。

        書き換えに失敗しても本文はそのまま送られる（ローダが握る）。
        描写の月数がずれるだけで、遊びは止めないほうがよい。
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
            elif any(mark in content for mark in PROMPT_MISS_MARKS):
                # 活動ごとの実文言はまだ全部読めていない。
                # 当たらなかった側を実機から拾えるよう、
                # その一節だけ残す（前後 40 字）。
                for mark in PROMPT_MISS_MARKS:
                    at = content.find(mark)
                    if at >= 0:
                        write("prompt at {} kept as is: ...{}...".format(
                            site, content[max(0, at - 40):at + 40]))
                        break
            result.append(new)
        return result if changed else None

    if LLM_STAY_WORDING:
        # 週単位を選んでいなくても仕掛けておく（設定は遊んでいる最中に変わりうる）。
        # 窓の外・月単位では `rewrite_outgoing` が即 None を返すので、
        # 他の推論の邪魔はしない。
        llm.wrap_outgoing(ctx, rewrite_outgoing, label="vacation custom")

    # ================================================ 宿の主への指示文
    def room_words(app, slot, quality):
        """部屋の名前と、いま引かれる額（`338_` の段まで込み）。"""
        conf = room_conf(slot)
        price = prices.inn_room(app, quality, write=write)
        return conf["name"], conf["price"] if price is None else price

    def reword_inn_terms(app, text):
        """指示文の期間・部屋の名前・宿代を今の値にする。宿の主の文でなければそのまま返す。

        素の設定なら同じ文ができるので、何も変わらない。
        """
        if not isinstance(text, str) or INN_TERMS_MARK not in text:
            return text
        match = INN_TERMS_RE.search(text)
        if match is None:
            if not state.get("terms_missed"):
                state["terms_missed"] = True
                write("WARN keeper: the inn terms did not match: ...{}...".format(
                    text[text.find(INN_TERMS_MARK):][:120]))
            return text
        stay = stay_for(app)
        simple, simple_price = room_words(app, "simple", "bunk")
        private, private_price = room_words(app, "private", "private_room")
        luxury, luxury_price = room_words(app, "luxury", "luxury_suite")
        terms = fmt(INN_TERMS, length=stay["length"] if stay else "{}ヵ月".format(match.group(1)),
                    simple=simple, simple_price=simple_price,
                    private=private, private_price=private_price,
                    luxury=luxury, luxury_price=luxury_price)
        kennel, kennel_price = room_words(app, "kennel", "kennel")
        kennel_text = (fmt(INN_KENNEL_PAID, name=kennel, price=kennel_price) if kennel_price
                       else fmt(INN_KENNEL_FREE, name=kennel))
        return (text[:match.start()] + terms + text[match.end():]).replace(INN_KENNEL_TEXT, kennel_text)

    def install_keeper_talk(target, text_at):
        @ctx.wrap(target, required=False, safe=True)
        def keeper_talk(orig, *args, **kwargs):
            """指示文の差し替えは来た側（位置かキーワード）に合わせる。"""
            try:
                text = frames.arg(args, kwargs, "response_prompt_instruction", text_at)
                new = reword_inn_terms(ui.find_app(), text)
                if new != text:
                    args, kwargs, _ = frames.replace_arg(
                        args, kwargs, "response_prompt_instruction", text_at, new)
                    write("keeper: the inn terms were reworded for {}".format(target.rsplit(":", 1)[1]))
            except Exception:
                ctx.log_exc("vacation custom: cannot reword the inn terms")
            return orig(*args, **kwargs)
        return keeper_talk

    if LLM_STAY_WORDING:
        for target, text_at in KEEPER_TALK_TARGETS:
            install_keeper_talk(target, text_at)

    # ------------------------------------------------------------ 自己検証
    # 実経路は宿に泊まるまで通らない。
    # ラベルの読み書きと期間の計算だけは作ったデータで先に確かめておく（`314_` と同じ方針）。
    # 通貨の表記は `130_` が差し替えていることがあるので、
    # 見本のほうも同じ表記へ通してから突き合わせる。
    parsed = ui.parse_coin(ui.rewrite_coins("個室(1,000G)"))
    # テンプレートは既定の形を直に書く（`ROOM_BUTTON` は GUI で変えられるので、
    # 設定を通すと変えた回に期待値とずれる）。
    sample = fmt("{name}({price}G)", name="大部屋", price=30)
    room_label = ui.rewrite_coins("大部屋(30G)")
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
    if parsed == 1000 and sample == room_label \
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
