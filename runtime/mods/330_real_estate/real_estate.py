# -*- coding: utf-8 -*-
r"""機能追加: 家を借りる・買う。役場で契約すると、その土地に自分の家が建つ。

素のゲームでプレイヤーが腰を落ち着けられる場所は宿屋だけで、
泊まるたびに宿代と30日を払い直す。荷物を置く場所も無い。
この MOD は役場（`administrative_office`）の選択肢に、家を借りる・買うための窓口を1つ足す。

    [役場]  労働の募集をみる / 市民権の発行 / 家を借りる・買う / 出る
                                    ↓
            借りる(1,600G・120日) / 建売を買い取る(30,000G) / やめる
                                    ↓
            街の入口に建物が1軒増える

    [入口]  中央居住区 / 外縁スラム / 借りている家 ←ここ / 街を出る
                                    ↓
    [家]    滞在する / 保管庫をあける / 家から出る

繋ぐ先はその土地の入口（`Node.entrance_facility`）。
街に着いて最初に立つ場所で、区画（`ward`）はその先にある。

## 建物はローダが持つ（`modfacility`。TECH.md §5.8）

**ゲームが移動の一覧を組むとき、実行時の `Facility.connections` は読まれない。**
足した家は素データ（`world_dict` / `save_data_dict`）に無いので、
繋ぎ先の画面にも、建物の中にも、ゲームは何も並べなかった
（入ること自体は `MovePhaseManager` でできる。例外も出ない。VERIFICATION.md §3.62）。
GAME.md §2.28 の「遊んでいる最中に生まれた施設で売買を選ぶと `KeyError`」と同じ側面で、
ゲームは施設を素データから引き直している。

だから道も、中の選択肢も、出口も MOD 側が出すことになる。
その肩代わりは**ローダが引き受ける**（`modfacility`）。
建てる・壊す・控える・建て直す・道と出口を出す・背景を呼ぶ・
保存の直前に立ち位置と選択肢を検める、までがあちらの仕事。

    modfacility.register(OWNER, facility_id=…, fields=…, choices=…, hide=…, on=…)
    modfacility.spawn(app, facility_id, area_id)

この MOD が宣言するのは、ここにしか決められないものだけ
（建物の中で何ができるか・何を描くか・中のまま保存してよいか）。

建物の中でできるのは3つ。

* **滞在**   宿屋の宿泊と同じ経路（`VacationStartManager`）。
             休養・訓練・労働・物乞いの活動がそのまま選べて、**宿代は取られない**。
             社交（他者と交流）とアイテム作成は出さない（`HIDDEN_ACTIVITY_CLASSES`）
* **保管庫** 店の売買と同じ2枚並びの窓。預けた品はプレイヤーの持ち物から外れる

## 建物はセーブに焼かない

足すのは実行中の `Area` / `Node` / `Facility` だけで、
`world_data.json` にも `savedata.json` にも施設は書かない（TECH.md §3.11）。
**MOD を外せば街は素に戻る。**

控えは2つに分かれる。

    state\real_estate\<世界名>.json    契約（いつまで・いくら・管理人・保管庫の中身）
    state\modfacility\<世界名>.json    建物そのもの（素データと置き場所）
    state\modnpc\<世界名>.json         管理人の実体（会話の記憶・置き場所）

施設 id はローダの名前空間（`mod:330_real_estate:<土地 id>`）で、
ゲームの採番台帳（`index['facility']`）を進めない。
土地ごとに1軒なので、土地の id をそのまま鍵にしている。

## 建物には管理人（大家）が1人居る

契約1つにつき人物を1人決め、滞在のあいだその人を建物の主にする。
立場は契約の種類で呼び分ける。賃貸なら大家、建売なら雇われた管理人。
素性は契約を結んだ1回だけ生成 AI に作らせ、**契約の控えに持つ**（`landlord.py`）。

**実体はローダの `modnpc` に預ける**（TECH.md §5.7）。
名簿に居る人物はゲームの保存が居場所の `.id` を読むので、保存の直前に引き上げる仕掛けが要る。
それを持っているのはローダのほうで、こちらで同じものを作らない
（自前で名簿へ足した版33 は、滞在が始まった直後の保存で落ちた。VERIFICATION.md §3.62）。

**「会話する」の一覧には出さない**（`place(listed=False)`）。
自分の家に他人が住んでいるように見えるため。
素のゲームの名簿は「居る人は必ずどこかの施設に立っている」形で、居場所を持たない人物は1人も居ない
（実セーブ7世界・620人。同 §3.62）。`modnpc` の人物はそもそもセーブに入らないので、その形は崩れない。

主を据えるのは、ゲームの宿泊が**主を世界の名簿から引く**ため
（主のいない施設で起こすと `KeyError: None` で落ちる。同 §3.62）。
据えたままにしてよく、外すのは契約が切れたときだけ（`seat_keeper` / `drop_keeper`）。
版31 までは役場の役人を借りていたが、宿の主人を見る MOD がその役人を宿の主人と読んだ（同 §3.62）。
役人を借りる道は、管理人が組めなかったときの逃げ道として残してある。

契約が切れる・解約する・建物を壊すときは、控えからその人も落とす。

## 保管庫の中身は控えに持つ

保管庫の窓の右に立つのは**その建物の管理人**（窓の見出しもその人の名前）。
ただし**預けた品はその人の持ち物に住まわせない**。
`modnpc` は保存のたびに MOD の NPC の持ち物まで控えるので、
そこへ置くと 330 の控えと二重になり、ロードのたびに品が増える。

預けた品はセーブと同じ形の辞書にして 330 の控えへ落とし、
窓を開くたびに管理人へ作り直し、窓を閉じるときに控えへ写して手元から外す。
管理人が引けないときは、その窓の間だけ生きる持ち主を立てる。中身は `storage.py`。

預けた瞬間にプレイヤーの持ち物から品が消えるので、**その場で `save_game` を呼ぶ**。
呼ばずに落ちると、控えにも持ち物にも同じ品が居る状態でセーブが残る（品が増える）。

## 家賃と期限

賃貸の1期は**宿泊 `RENT_STAYS` 回ぶん**（`lease_days(app)`。既定4回）。
1回ぶんにすると宿に泊まり直すのと変わらないので、まとめて借りる形にしてある。

宿泊1回の長さは**ゲームの宿屋と同じ**（自分の家の滞在も同じ長さ）。
素のゲームは3ヵ月から年齢で伸びる変動式で、
`315_vacation_custom` を入れていればその設定が効く。
どちらも自分では決めず、**ローダの窓口に聞く**（`durations.inn_stay`。
変える MOD が入っていればその答え、無ければゲームの式。TECH.md §3.3.2）。
窓口は月数と日数の両方を返すので、賃貸の1期もそこから数える
（月数×30 とは限らない。315 の週単位は月数1のまま7日しか進めない）。

版39 までは宿屋の部屋の選択肢から観測した月数のほうを答えにしていた。
観測は**部屋の一覧を出したときにしか更新されない**ので、設定を変えても古いままで、
`3ヵ月` に戻した後も1ヵ月で滞在していた（実機。VERIFICATION.md §3.62 #11）。
`1ヵ月` と `1週間` はどちらもゲームに渡る月数が 1 なので、
「月数が変わったら測り直す」という見張り方でも見分けられない。

観測そのものは残してあり、窓口と食い違ったら1度だけ書く
（窓口に出さずに長さを変えている MOD が居る、という手掛かりになる）。
観測に `VacationStartManager.__init__` は見ない。
MOD が自分で起こす滞在（`331_facility_investment` の「無料で泊まる」や、この MOD の
家の滞在）も同じ入口を通るので、よその MOD の都合の月数を宿屋の値として拾ってしまう。
部屋のボタンはゲームだけが組む。

契約の周期は**結んだ時点の長さで固定**する。
年を取って宿泊が伸びても、いま借りている契約の期限は動かない。
日付が進むのは `elapse_days` の1箇所だけなので（GAME.md §2.16）、そこを包んで期限を見る。

    払えた     期限を `term` 日延ばす。移動や宿泊で何期ぶんか飛んだときはまとめて払う
    払えない   契約が切れる。建物は取り壊し、保管庫の中身は役場が預かる

預かった品は、どこの役場でも引き取り料を払えば戻る。
建売（`owned`）には期限も家賃も無い。

**精算は滞在の外でだけ行う。**
自分の家の滞在は `VacationStartManager.execute` の中で暦を進めるので、
その最中に来た期限は見送り（`rent_pending`）、滞在が終わってから1回で払う。
宿代の前払いと同じ区間で金を動かさないため。

**プレイヤーが建物の中に居るあいだは取り壊さない**（出口の無い施設に立たせないため）。
その場合は次に外へ出たときに壊す。

## 自前のクラス名を `PhaseSpec` に書かない

ボタンはセーブに焼かれうる（GAME.md §2.2）。無害な既存クラスを持たせ、
押下は `on_button_press` を包んで印（`mod_real_estate`）で横取りする。
"""

import copy
import datetime
import sys

from instantale_modloader import (durations, frames, llm, modfacility, modnpc,
                                  npcs, prices, ui)
from instantale_modloader.state import (UNKNOWN_WORLD, WorldStore, playthrough_key,
                                        playthrough_key_of_dict)

from . import landlord, storage


LOG_BASENAME = "real_estate.log"

#: `modfacility` に名乗る持ち主。控えの中でこの MOD の建物を束ねる鍵になる。
OWNER = "330_real_estate"


def main_module():
    """ゲーム本体のモジュール（`instantale.py`）。"""
    return sys.modules.get("__main__")

#: 世界ごとの控え `state\real_estate\<世界名>.json`。
STATE_DIRNAME = "real_estate"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_real_estate_store__"

#: 押下を横取りするための印。他の MOD と別のキーにすること。
MARK = "mod_real_estate"

#: ボタンに載せる契約の種類（`mod_` で始めるのは、他の MOD の掃除に
#: 「印の無いボタン」と見なされないため。TECH.md §5.1.1）。
KIND_KEY = "mod_real_estate_kind"

#: 役場の `facility_type`（実セーブで確認。GAME.md §2.7）。
OFFICE_FACILITY_TYPE = "administrative_office"

#: 施設の選択肢であることの目印。移動のボタンがある画面だけに足す（`309_` と同じ）。
MOVE_CLS = "MovePhaseManager"

#: 宿泊の入口。実測の署名は `(app, months, quality)`（GAME.md §2.17）。
STAY_CLS = "VacationStartManager"

#: 宿泊を終える側。引数は `app` だけ。
END_CLS = "VacationEndManager"

#: 滞在の活動。1つ終えたらその滞在は終わり（1泊＝活動1回。GAME.md §2.17）。
#: 社交だけは2段（`VacationSocializeManager` → `...ResolveManager`）なので、
#: 締めるのは後段だけにする。前段で締めると相手との場面が来ない。
ACTIVITY_CLASSES = ("VacationRestManager", "VacationTrainManager",
                    "VacationLaborManager", "VacationBeggingManager",
                    "VacationSocializeResolveManager")

#: 社交の入口。
SOCIALIZE_CLS = "VacationSocializeManager"

#: 宿泊の活動に並ぶ `アイテム作成`。
#: このビルドでは**ゲームの未実装の置き場所**が載っている（押しても何も起きない）。
#: `ItemCraftManager` は別に在るが、宿泊の活動からは呼ばれない
#: （実測。宿屋でも自分の家でも同じクラス。VERIFICATION.md §3.62）。
CRAFT_CLS = "NotImplementedManager"

#: 自分の家の滞在では出さない活動（VERIFICATION.md §3.62）。
#:
#: 社交   … 誰と会うかはゲームが決め、自分の家ではその場面が成り立たなかった
#: 作成   … 押しても何も起きないボタン。自分の家の画面はこの MOD が出しているので並べない
#:
#: 宿屋の側には触らない（落とすのは自分の建物での滞在の最中だけ）。
HIDDEN_ACTIVITY_CLASSES = (SOCIALIZE_CLS, CRAFT_CLS)

#: 会話の一覧の入口。
TALK_CLS = "DisplayTalkChoice"

#: 家の中では出さないゲームの選択肢（`modfacility` の `hide`。TECH.md §5.8）。
#:
#: 主を据えるとゲームが `会話する` を出すが、**管理人はその一覧に出さない人**なので
#: （`place(listed=False)`）、誰も並ばない選択肢が家に残る。
#: 大家とは話さない（管理人の存在自体を公開していないため。本人の指定）。
HIDDEN_HOME_CLASSES = (TALK_CLS,)

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む）。
# `landlord.py` / `storage.py` へ移さないこと（TECH.md §3.8）。

#: 家賃。**1期ぶん**。期間は `RENT_STAYS` 回ぶんの滞在と同じ日数。
RENT_PRICE = 1600

#: 賃貸の1期は滞在の何回ぶんか。
#: 1 にすると宿屋の1泊と同じ長さになり、借りる意味が薄い（本人の指定で既定4）。
RENT_STAYS = 4

#: 建売の価格。買い切りで、以後の家賃も期限も無い。
PURCHASE_PRICE = 30000

#: 期限が来たら所持金から家賃を引いて契約を延ばす。
#: 切ると、期限が来た時点で必ず契約が切れる。
AUTO_RENEW = True

#: 契約の残りがこの日数を切ると1行知らせる。0 で知らせない。
NOTICE_DAYS = 7

#: 期限切れで役場が預かった品を引き取るときの料金。
RECLAIM_FEE = 2000

#: 保管庫を世界で1つにする。
#: 切ると建物ごとに別の保管庫（既定）。
STORAGE_SHARED = False

#: 滞在の部屋の等級。ゲームの宿屋と同じ語彙（GAME.md §2.17）。
STAY_QUALITY = "private_room"

#: 管理人（大家）を誰が作るか。`llm` はゲームと同じ生成 AI に1回だけ聞く
#: （契約を結ぶときだけ。数秒）。`table` は `landlord.KEEPER_POOL` の12人から選ぶ
#: （待ち時間ゼロ、名前は使い回し）。
KEEPER_SOURCE = "llm"

#: 借りた物件の名前。
RENT_NAME = "借りている家"

#: 買った物件の名前。
OWNED_NAME = "自分の家"

# ---------------------------------------------------------------- 文言
#: 役場に足す選択肢。
#: 「物件を扱う」だと 331 の出資（店を建てて売上を得る）とも読めた
#: ので、**自分が住む家の話だと分かる文言**にした（本人の指摘）。
OFFICE_LABEL = "家を借りる・買う"
RENT_LABEL = "借りる({}G・{}日)"
BUY_LABEL = "建売を買い取る({}G)"
STATUS_LABEL = "契約を確かめる"
RELEASE_LABEL = "解約する"
RECLAIM_LABEL = "預かり品を引き取る({}G)"
CANCEL_LABEL = "やめる"

#: 建物に足す選択肢。
STAY_LABEL = "滞在する"
STORAGE_LABEL = "保管庫をあける"
#: 建物の出口。ゲームの `出る` と別の文言にしてある。
#: 同じ文言だと、印を失った残骸（セーブから戻ったボタン）を掃除で見分けられず、
#: 押しても何も起きない `出る` が画面に残る。
LEAVE_LABEL = "家から出る"
#: 滞在を締めるときに `process_choice` へ渡す文言（ゲームの `宿泊を終える` と同じ）。
END_LABEL = "宿泊を終える"

#: 印を失った残骸を文言で見分けて掃除するための前方一致
#: （`ui.Screen.prune_stale`。GAME.md §2.2）。
#: **この MOD にしか無い文言だけ**を並べること（`やめる` のような共通語を入れると
#: 他の MOD の確認画面を消す）。
OUR_LABEL_PREFIXES = (OFFICE_LABEL, "借りる", "建売を買い取る",
                      STATUS_LABEL, RELEASE_LABEL, "預かり品を引き取る",
                      STAY_LABEL, STORAGE_LABEL, LEAVE_LABEL)

#: 世界で1つの保管庫にしたときの、窓の見出し。
STORAGE_NAME = "保管庫"

#: 建物の説明（街の中でこの建物を選んだときの情景の素）。
RENT_DESCRIPTION = "借り受けた小さな家。家財は少ないが、鍵は自分が持っている。"
OWNED_DESCRIPTION = "買い取った家。狭くはあるが、ここは間違いなく自分の場所だ。"

SIGNED_TEXT = "{area}に{name}を構えた。"
#: 契約した直後に続けて出す一文（管理人が立てられたときだけ）。
KEEPER_TEXT = "{keeper}が{role}として鍵を預かっている。"
RENEWED_TEXT = "{name}の家賃 {price}G を納めた。"
NOTICE_TEXT = "{name}の契約はあと{days}日で切れる。"
LAPSED_TEXT = "家賃を払えず、{area}の{name}を引き払うことになった。"
SEIZED_TEXT = "保管庫にあった{count}点は役場が預かっている。"
RELEASED_TEXT = "{area}の{name}を引き払った。"
RECLAIMED_TEXT = "役場から{count}点を引き取った。"

#: 滞在の描写をゲームが AI に頼むとき、**どこに泊まったかは渡らない**。
#: 頼み文は「このエリアで数ヵ月の宿泊をし」で、あとはエリアの一覧が続くだけ
#: （GAME.md §2.17）。
#: 街に自分の家が建つと、読み手はその一覧から自分の家を選び、
#: 宿屋に泊まったのに自宅で過ごした話になる（VERIFICATION.md §3.62）。
#: こちらが持ち込んだ取り違えなので、**自分の家がある街の滞在の頼み文にだけ**1文を足す。
SCENE_HEAD = "※今回の"
INN_SCENE_TEXT = (SCENE_HEAD
                  + "宿泊先はこのエリアの「{place}」である。{name}の家「{home}」ではない。")
HOME_SCENE_TEXT = SCENE_HEAD + "滞在先は{name}自身の家「{home}」である。宿屋ではない。"

#: 足す位置（ゲームがエリアの一覧を並べる前）と、滞在の頼み文だと見分ける語。
#: 見分けが外れて別の頼み文に足すより、足しそこねるほうがまし。
SCENE_MARK = "【エリアの構造】"
SCENE_HERE = "このエリア"
SCENE_WORDS = ("宿泊", "休暇")

#: 管理人を作らせる頼み文の名前（`output_data\<世界>\<PC>\<この名>\N.json` に残る）。
KEEPER_MANAGER = "mod_real_estate_keeper"
#: 待つ秒数。契約を結ぶ操作の中で1回だけ呼ぶ。返らなければ表の人で立てる。
KEEPER_TIMEOUT = 90

#: ゲームの1ヵ月（`elapse_days(months * 30)`。GAME.md §2.17 の実測）。
DAYS_PER_MONTH = 30


def game_stay(app):
    """いまの宿屋の宿泊1回の長さ。ローダの窓口に聞く（`durations.inn_stay`）。

    素のゲームは3ヵ月から年齢で伸びる式で、変える MOD
    （`315_vacation_custom` など）が入っていればその答えになる。
    **この MOD は式を持たないし、変える MOD の名前も知らない**（TECH.md §3.3.2）。
    返るのは必ず辞書 `{"months", "days", "length", "source"}`。
    """
    return durations.inn_stay(app)


def _fmt(template, **values):
    """文言の穴を埋める。埋められない穴はそのまま残す（文言は設定から来うる）。"""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def _price_of(kind):
    """その契約の値段。1期ぶんの家賃、または建売の価格。"""
    if kind == "owned":
        return int(PURCHASE_PRICE)
    return int(RENT_PRICE)


def _name_of(kind):
    if kind == "owned":
        return (OWNED_NAME or "").strip() or "自分の家"
    return (RENT_NAME or "").strip() or "借りている家"


def _description_of(kind):
    return OWNED_DESCRIPTION if kind == "owned" else RENT_DESCRIPTION


def _is_lease(record):
    if not isinstance(record, dict):
        return False
    return record.get("kind") == "rent"


def ordered_bucket(bucket):
    """控えの項目の並び。読んだときに契約から目に入る順にする。"""
    if not isinstance(bucket, dict):
        return bucket
    order = ("contracts", "seized", "shared", "stay_months")
    ordered = {key: bucket[key] for key in order if key in bucket}
    ordered.update({k: v for k, v in bucket.items() if k not in ordered})
    return ordered


def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "worlds": WorldStore(ctx, STATE_DIRNAME, order=ordered_bucket),
            "state": {
                # 自前のフェーズが動いている間の旗。連打の2発目を捨てる
                # （`325_` が実機で二重の支払いを踏んでいる）。
                "acting": False,
                # 宿代を払わない滞在。`{"facility": id, "area": id}`。
                "free_stay": None,
                # 開いている保管庫。`{"holder": Character, "area": id}`。
                "storage": None,
                # 取り壊しを待っている契約（プレイヤーが中に居た）。
                "pending_demolish": [],
                # 自前の画面を出す前の選択肢（`やめる` で戻すために控える）。
                "saved": None,
                # 一度書いた WARN の覚え。選択肢が組まれるたびに当て直すので、
                # 同じ理由をそのたび書くとログが選択肢の回数だけ伸びる。
                "warned": set(),
                # 滞在の最中に来た家賃の期限（滞在が終わってから精算する）。
                "rent_pending": False,
                # 滞在のあいだ主を据える前の値（戻すために控える）。
                "owner_was": None,
                # 手が空くのを待っているボタンの足し直し（見張りは同時に1つ）。
                "retry": False,
                # 滞在を締める `VacationEndManager` を起こした（二度は起こさない）。
                "ending": False,
                # 活動の後の選択肢を待機表示で覆っている（締め終えたら解く）。
                "covered": False,
                # 覆った滞在（`free_stay` の辞書）。1回の滞在で覆うのは1度だけ。
                "covered_for": None,
            },
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]

    write = ctx.logger(LOG_BASENAME)
    worlds = store["worlds"].rebind(ctx, write)
    screen = ui.Screen(ctx, write, tag="real estate", mark=MARK)
    # 所持金や持ち物を動かして控えを進めたら、ゲーム自身の保存を少し後に1回呼ぶ。
    # 控えはその場でファイルになるが、所持金と持ち物がセーブに入るのは次の保存のとき。
    # 保存しないまま落ちると控えだけが進んだ形が残る（預けた品が増える、家賃を払わずに期限が延びる）。
    save_soon = ui.saver(ctx, write, "real estate")

    # 建物と管理人はローダが持つ（TECH.md §5.7 / §5.8）。
    # 関所は何本の MOD が呼んでも1つしか立たない。
    modfacility.install(ctx, write=write)
    modnpc.install(ctx, write=write)

    # ------------------------------------------------------------ 補助
    def warn_once(token, text):
        """同じ理由の WARN は1度だけ書く。

        当て直しは選択肢が組まれるたびに走るので、素直に書くと
        「立てられない」理由が1手ごとに1行ずつ積もる（ログが読めなくなる）。
        直った（＝建った）ら覚えを落とすので、次に同じことが起きればまた出る。
        """
        if token in state["warned"]:
            return False
        state["warned"].add(token)
        write(text)
        return True

    # ------------------------------------------------------------ 控え
    def current_key(app):
        """いまの周回の鍵（世界×主人公。`state.playthrough_key`）。

        契約を世界名だけで引くと、主人公が死んで同じ世界で作り直したときに
        前の主人公の家と預かりが新しい主人公に引き継がれる（VERIFICATION.md §3.62）。
        ロードの建て直しの間は `world_loaded` が引数のセーブから決めた鍵を使う。
        """
        override = state.get("key_override")
        return override if override else playthrough_key(app)

    def bucket_of(key):
        bucket = worlds.load(key)
        bucket.setdefault("contracts", [])
        bucket.setdefault("seized", {})
        return bucket

    def contracts_of(key):
        return [c for c in bucket_of(key).get("contracts") or [] if isinstance(c, dict)]

    def contract_in(app, area_id):
        """その土地の契約。無ければ None。"""
        if not area_id:
            return None
        for record in contracts_of(current_key(app)):
            if str(record.get("area")) == str(area_id):
                return record
        return None

    def contract_here(app):
        return contract_in(app, ui.area_id_of(ui.current_area(app)))

    def seized_of(app):
        """役場が預かっている品。`{鍵: 辞書}`。"""
        items = bucket_of(current_key(app)).get("seized")
        return items if isinstance(items, dict) else {}

    def storage_of(app, record):
        """その建物から見える保管庫の中身 `{鍵: 辞書}`。

        置き場所は設定で変わる。
        世界で1つ（`STORAGE_SHARED`）なら控えの根に、
        建物ごとならその契約の中に持つ。
        """
        if STORAGE_SHARED:
            bucket = bucket_of(current_key(app))
            items = bucket.get("shared")
            if not isinstance(items, dict):
                items = bucket["shared"] = {}
            return items
        items = record.get("storage") if isinstance(record, dict) else None
        if not isinstance(items, dict):
            items = {}
            if isinstance(record, dict):
                record["storage"] = items
        return items

    def set_storage(app, record, items):
        """保管庫の中身を書き戻す（置き場所は `storage_of` と同じ決まり）。"""
        if STORAGE_SHARED:
            bucket_of(current_key(app))["shared"] = items
        elif isinstance(record, dict):
            record["storage"] = items
        save(app)

    def gather_into_shared(app):
        """建物ごとに預けてあった品を、共有の保管庫へ寄せる。寄せた点数を返す。

        設定を「世界で1つ」に変えた後、建物ごとの控えに残っている品は
        どこからも開けなくなる。開くたびに1度だけ寄せておく
        （逆向き（共有 → 建物ごと）は寄せない。どの建物へ返すか決められないので、
        設定を戻せばまた同じ中身が見える）。
        """
        if not STORAGE_SHARED:
            return 0
        bucket = bucket_of(current_key(app))
        shared = bucket.get("shared")
        if not isinstance(shared, dict):
            shared = bucket["shared"] = {}
        moved = 0
        for record in contracts_of(current_key(app)):
            items = record.get("storage")
            if not isinstance(items, dict) or not items:
                continue
            for key, data in items.items():
                shared[storage.free_key(shared, key)] = data
                moved += 1
            record["storage"] = {}
        if moved:
            save(app)
            write("storage: moved {} item(s) from the buildings into the shared "
                  "storage".format(moved))
        return moved

    def save(app):
        worlds.save(current_key(app))

    # ------------------------------------------------------------ 建物の当て直し
    def note_inn_months(app, buttons):
        """宿屋の部屋の選択肢から、いまの宿泊の月数を読む。控えたら True。

        ゲーム自身が組む部屋のボタン（`犬小屋(0G)` … `高級個室(1000G)`）の spec が
        `VacationStartManager(app, months, quality)`（GAME.md §2.17）。
        **そこに載っている月数が、MOD を全部通った後の答え**
        （年齢の変動式も `315_vacation_custom` の期間の差し替えも、この時点で済んでいる）。

        `VacationStartManager.__init__` を見る形はやめた。
        MOD が自分で起こす滞在（`331_facility_investment` の「無料で泊まる」や
        この MOD の家の滞在）も同じ入口を通るので、
        **よその MOD の都合の月数を「宿屋の値」として覚えてしまう**
        （VERIFICATION.md §3.62。331 の宿で3ヵ月を拾っていた）。
        部屋のボタンはゲームだけが組むので、そこが混ざらない。

        ただし**滞在の最中の画面も同じ spec のボタンを持つ**。
        ゲームは活動の選択肢に `まだ宿泊する`（連泊。`VacationStartManager`）を並べ、
        そこには**いま走っている滞在の月数**が載っている。
        自分の家の滞在でそれを拾うと、見積もりで始めた月数を
        「宿屋の値」として覚え直してしまう（3ヵ月で固まった。VERIFICATION.md §3.62）。
        MOD が建てた建物の中では数えない。
        """
        if inside_mod_building(app):
            return False
        for entry in buttons or []:
            if ui.spec_cls_name(entry) != STAY_CLS:
                continue
            args = ui.spec_args(entry)
            if args and remember_stay_months(app, args[0]):
                return True
        return False

    def remember_stay_months(app, months):
        """宿屋の宿泊の月数を控える。控えたら True。"""
        try:
            value = int(str(months))
        except (TypeError, ValueError):
            return False
        if value < 1:
            return False
        key = current_key(app)
        bucket = bucket_of(key)
        if bucket.get("stay_months") == value:
            return True
        bucket["stay_months"] = value
        worlds.save(key)
        write("stay length: the inn's rooms are booked by {} month(s)".format(
            value))
        return True

    def inside_mod_building(app):
        """MOD が建てた建物の中に立っているか。

        よその MOD の宿（`331_facility_investment` の「無料で泊まる」）で測ると、
        その MOD の都合の日数を宿屋の値として覚えてしまう。
        """
        try:
            return bool(modfacility.inside(app))
        except Exception:
            return False

    def game_stay_here(app):
        """いまの宿泊の長さ。**これが答え**。誰が決めたかを1度だけログに残す。

        版39 まではここを「まだ宿屋を見ていない世界の落ちどころ」にして、
        宿屋の部屋の選択肢から観測した値のほうを答えにしていた。
        観測は設定を変えても更新されない（書き直すのは部屋の一覧を出したときだけ）ので、
        `3ヵ月` に戻した後も1ヵ月のまま滞在していた（実機。§3.62 #11）。
        月数と日数の両方を持っているのも窓口だけ
        （`1ヵ月` と `1週間` はどちらも月数が 1 で、月数では見分けられない）。
        """
        plan = durations.inn_stay(app, write=write)
        warn_once(("stay-source", plan.get("source"), plan.get("length")),
                  "stay length: {} month(s){} by {}".format(
                      plan["months"],
                      "/{} day(s)".format(plan["days"]) if plan.get("days") else "",
                      plan.get("source") or "the game's own rule"))
        return plan

    def stay_months(app):
        """1回の滞在の月数。**宿屋と同じ**（本人の指定）。

        素のゲームは3ヵ月から年齢で伸び、`315_vacation_custom` を入れていれば
        その設定が効く。どちらも自分では決めず、ローダの窓口に聞く。
        観測した値は答えにしないが、食い違ったら1度だけ残す
        （窓口に出さずに長さを変えている MOD が居る、という手掛かりになる）。
        """
        months = game_stay_here(app)["months"]
        seen = bucket_of(current_key(app)).get("stay_months")
        if isinstance(seen, int) and not isinstance(seen, bool) and seen >= 1 \
                and seen != months:
            warn_once(("stay-months", seen, months),
                      "stay length: the inn's rooms were booked by {} month(s) "
                      "but the window says {}; using the window".format(
                          seen, months))
        return months

    def remember_stay_days(app, days, months=None, why="the inn"):
        """滞在1回で**実際に進んだ日数**を控える。

        月数×30 とは限らない。`315_vacation_custom` の週単位は
        ゲームに渡す月数を1にしたまま、日数だけを設定の値へ縮める
        （宿は `宿泊する(1週間)` で7日だった。VERIFICATION.md §3.62）。
        賃貸の1期はこの日数を数えるので、月数ではなくこちらで数える。

        **どの月数で測ったか**を一緒に控える。
        月数が変わったら（宿屋の部屋を見て覚え直した・設定を変えた）、
        前に測った日数はもうその答えではない。
        """
        try:
            value = int(days)
        except (TypeError, ValueError):
            return False
        if value < 1:
            return False
        try:
            tag = int(str(months))
        except (TypeError, ValueError):
            tag = None
        key = current_key(app)
        bucket = bucket_of(key)
        if bucket.get("stay_days") == value and bucket.get("stay_days_for") == tag:
            return True
        bucket["stay_days"] = value
        bucket["stay_days_for"] = tag
        worlds.save(key)
        write("stay length: {} moved the calendar {} day(s) (months={})".format(
            why, value, tag))
        return True

    def stay_days(app):
        """滞在1回で進む日数。ローダの窓口の答え（`days` が無ければ月数×30）。

        版39 までは実際に進んだ日数の控えを先に見ていた。
        `315_vacation_custom` の週単位も `1ヵ月` もゲームに渡る月数は 1 なので、
        「測ったときの月数が今と違えば使わない」（`stay_days_for`）では
        この2つを見分けられず、`1週間` に変えた後も30日で数えていた
        （実機。§3.62 #11）。
        窓口は月数と日数の両方を持っている。
        """
        plan = game_stay_here(app)
        return plan["days"] if plan.get("days") else plan["months"] * DAYS_PER_MONTH

    def lease_days(app):
        """賃貸の1期の長さ（日）。**滞在 `RENT_STAYS` 回ぶん**。

        宿と同じ長さ（1回ぶん）では、家を借りても泊まり直すのと変わらない。
        既定は4回ぶんで、その間は何度でも戻って過ごせる（本人の指定）。
        """
        return max(1, int(RENT_STAYS or 1)) * stay_days(app)

    def drop_legacy_contracts(key):
        """建物をゲームの採番で建てていた頃の契約を落とす（版20）。

        施設 id を `mod:` の文字列に変えたので（TECH.md §5.8）、
        整数の id を指している控えはもう建て直せない。
        落とすのは控えだけで、セーブには何も残っていない
        （建物は実行時にしか無く、立ち位置が古い id を指していれば
        ローダが入口へ直す）。
        """
        bucket = bucket_of(key)
        old = [c for c in bucket.get("contracts") or []
               if isinstance(c, dict)
               and not modfacility.is_mod_facility(c.get("facility") or "")]
        if not old:
            return 0
        bucket["contracts"] = [c for c in bucket.get("contracts") or []
                               if c not in old]
        worlds.save(key)
        write("dropped {} contract(s) from before the loader owned the buildings: "
              "{}".format(len(old), [c.get("name") for c in old]))
        return len(old)

    # ------------------------------------------------------------ 管理人（`modnpc`）
    def keeper_id_for(area_id):
        """その土地の管理人の id。建物は土地に1軒なので、土地の id をそのまま鍵にする。"""
        return modnpc.make_id(OWNER, "keeper-{}".format(area_id))

    def keeper_of(record):
        """契約に控えた管理人。まだ居なければ None。"""
        found = record.get("keeper") if isinstance(record, dict) else None
        return found if isinstance(found, dict) and found.get("id") else None

    def keeper_name(app, record, world=None):
        """管理人の名。控えた名 → 実体の名 → 鍵から引いた表の名。

        控えを先に見るのは、**建て直しても同じ人**にするため。
        """
        keeper = keeper_of(record)
        if keeper is None:
            return ""
        stored = keeper.get("name")
        if stored:
            return str(stored)
        return landlord.keeper_choice(keeper.get("id"))[0]

    def keeper_names(app, skip=None):
        """新しい管理人が避ける名前。**その世界で使われている名前を全部**。

        自分の控えの管理人に加えて、ローダの窓口（`modnpc.names_in_use`）が返す
        素の NPC・実行時の名簿・他の MOD の NPC・プレイヤーまで見る。
        名前が重なると、**名前でしか相手を引けない場所**で別人に当たる
        （ゲームの人物欄。VERIFICATION.md §3.68）。
        """
        found = []
        mine = keeper_of(skip) if skip is not None else None
        for record in contracts_of(current_key(app)):
            if record is skip:
                continue
            keeper = keeper_of(record)
            if keeper is not None and keeper.get("name"):
                found.append(str(keeper.get("name")))
        for name in modnpc.names_in_use(
                app, skip=[mine.get("id")] if mine else ()):
            if name not in found:
                found.append(name)
        return found

    def generate_keeper(app, record, keeper_id, taken):
        """管理人の素データをゲームと同じ生成 AI に作らせる。作れなければ None。

        呼ぶのは**契約を結ぶときの1回だけ**。答えは控えに持つので、
        建て直しでもロードでも二度と聞かない（同じ人が戻る）。
        呼べない版・読めない答えのときは None を返し、呼ぶ側が表から選ぶ。
        """
        if str(KEEPER_SOURCE).lower() != "llm":
            return None
        structure = llm.create_structure(ctx, "HouseKeeper", {
            "name": (str, ...), "category": (str, ...),
            "speech_style": (str, ...), "personality": (str, ...),
            "profile": (str, ...),
        }, label="real estate")
        if structure is None:
            write("keeper: cannot build the structure; using the table")
            return None
        system, user = landlord.keeper_prompt(
            record.get("kind"), record.get("area_name"), record.get("name"),
            ui.world_overview(app), player_name(app), taken)
        answer = llm.ask(ctx, KEEPER_MANAGER,
                         [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                         timeout=KEEPER_TIMEOUT, structure=structure,
                         label="real estate", write=write)
        fields = landlord.keeper_fields_from(
            answer, record.get("kind"), record.get("area_name"), record.get("name"),
            keeper_id, player=player_name(app), taken=taken)
        if fields is None:
            write("keeper: the answer was not usable; using the table")
            return None
        write("keeper: {!r}（{}）was made for {!r}".format(
            fields.get("name"), fields.get("category"), record.get("name")))
        return fields

    def make_keeper(app, record):
        """契約を結んだときに管理人を1人決め、控えへ書く。書いた辞書を返す。

        **名前が無いまま登録しない**（生成 AI が使えなければ表の人で組む）。
        """
        keeper_id = keeper_id_for(record.get("area"))
        taken = keeper_names(app, skip=record)
        fields = generate_keeper(app, record, keeper_id, taken)
        source = "llm"
        if fields is None:
            source = "table"
            fields = landlord.keeper_fields(
                record.get("kind"), record.get("area_name"), record.get("name"),
                keeper_id, player=player_name(app), taken=taken)
        keeper = {"id": keeper_id,
                  "name": fields.get("name"),
                  "role": landlord.role_of(record.get("kind")),
                  "source": source,
                  "fields": fields}
        record["keeper"] = keeper
        return keeper

    #: この世代で層を積んだ管理人（ロードのたびに積み直す）。
    keepers_registered = set()

    def keeper_fields_of(record):
        """控えの素データ（無ければ表の人で組む）。"""
        keeper = keeper_of(record) or {}
        made = keeper.get("fields")
        if isinstance(made, dict) and made.get("name"):
            return dict(made)
        return landlord.keeper_fields(record.get("kind"), record.get("area_name"),
                                      record.get("name"), keeper.get("id"),
                                      name=keeper.get("name"))

    def register_keeper(app, record):
        """管理人の層を積む。id を返す（控えに居なければ空）。

        積むのは**この世代のこのロードで1度だけ**。
        塗り直しのたびに積むとログが毎手流れ、「層が在れば積まない」にすると
        注入し直しても前の版の層が残る（TECH.md §5.7）。
        """
        keeper = keeper_of(record)
        if keeper is None:
            return ""
        keeper_id = str(keeper.get("id"))
        if keeper_id in keepers_registered:
            return keeper_id
        keepers_registered.add(keeper_id)
        modnpc.register(OWNER, npc_id=keeper_id, fields=keeper_fields_of(record),
                        write=write)
        return keeper_id

    def settle_keeper_name(app, record, world=None):
        """管理人の名がその世界の誰かと重なっていたら、空いている名前へ寄せる。

        名前が重なると、ゲームが名前で人を扱う経路（立ち絵は
        `characters\\<名前>\\` のフォルダ、LLM へ渡すのは名前の列挙）で別人に当たる
        （実機。VERIFICATION.md §3.68）。
        直すのは控えの2か所（`keeper.name` と素データの `name`）と、居れば実体。
        **変わるのは一度だけ**で、次のロードでは控えの名前がもう空いている。
        """
        keeper = keeper_of(record)
        if keeper is None:
            return ""
        name = str(keeper.get("name") or "")
        taken = keeper_names(app, skip=record)
        if not name or name not in taken:
            return name
        fresh = str(landlord.keeper_choice(keeper.get("id"), taken)[0])
        if fresh in taken:
            write("WARN keeper: {!r} is already used in this world and the table has "
                  "no free name; leaving it as it is".format(name))
            return name
        keeper["name"] = fresh
        made = keeper.get("fields")
        if isinstance(made, dict):
            made["name"] = fresh
        save(app)
        handle = modnpc.get(app, keeper.get("id"), world=world)
        if handle is not None and getattr(handle, "name", None) != fresh:
            try:
                handle.name = fresh
            except Exception:
                ctx.log_exc("real estate: cannot rename the keeper")
        write("keeper: {} was renamed {!r} -> {!r} "
              "(the name was already used in this world)".format(
                  keeper.get("id"), name, fresh))
        return fresh

    def keeper_character(app, record, world=None):
        """管理人の実体。名簿に居なければ None。"""
        keeper = keeper_of(record)
        if keeper is None:
            return None
        handle = modnpc.get(app, keeper.get("id"), world=world)
        return getattr(handle, "character", None) if handle is not None else None

    def seat_keeper(app, record, facility, world=None):
        """管理人を建物の主に据える。据えた id を返す（駄目なら None）。

        **`modnpc` に預ける。** 名簿に居る人物はゲームの保存が舐めるので
        （`save_game` が居場所の `.id` を読む。実機で `AttributeError` が出た）、
        保存の前に名簿から引き上げる仕掛けが要る。それはローダが持っている
        （`hide` / `restore`。TECH.md §5.7）ので、こちらで同じものを作らない。

        **一覧には出さない**（`listed=False`）。自分の家の `会話する` に大家が並ぶと、
        他人がそこに住んでいるように見える（本人の指定）。
        """
        if facility is None:
            return None
        keeper = keeper_of(record)
        if keeper is None and isinstance(record, dict):
            # **版31 までに結んだ契約には管理人が居ない。** 決めるのは契約のときだけなので、
            # そのままでは役人を借り続ける。ここで1人決めて控えへ書く（聞くのは1回だけ）。
            write("keeper: {!r} was signed before the house had a keeper; "
                  "choosing one now".format(record.get("name")))
            keeper = make_keeper(app, record)
            save(app)
        if keeper is None:
            return None
        # 名前が世界の誰かと重なっていたらここで寄せる（層を積む前。積んだ後だと
        # その世代の頼み文に古い名前が載る）。
        settle_keeper_name(app, record, world=world)
        keeper_id = register_keeper(app, record)
        if not keeper_id:
            return None
        # **名簿や `owner` を見て飛ばさない。** `spawn` も `place` も何度呼んでもよく、
        # 名簿に実体だけ在れば `spawn` がそれを拾って記録に繋ぎ直す。
        # 手前で飛ばすと記録が歯抜けのまま残り、`keeper_character` は空を返し、
        # `placed` が空なので保存の直前の `unplace` も効かない
        # （実機。前の版が名簿へ直に足した実体が残っていた世界で踏んだ）。
        if modnpc.spawn(app, keeper_id, world=world, write=write) is None:
            warn_once(("keeper", keeper_id),
                      "WARN keeper: {} did not spawn; the house has no keeper"
                      .format(keeper_id))
            return None
        modnpc.place(app, keeper_id, str(record.get("area")),
                     str(record.get("facility")),
                     owner=True, listed=False, world=world, write=write)
        state["warned"].discard(("keeper", keeper_id))
        return keeper_id

    def drop_keeper(app, record):
        """管理人を世界から降ろし、控えからも落とす。降ろしたら True。

        解約・期限切れ・取り壊しで呼ぶ（MOD が足したものは MOD が片付ける）。
        """
        keeper = keeper_of(record)
        if keeper is None:
            return False
        keeper_id = str(keeper.get("id"))
        modnpc.unregister(OWNER, keeper_id, app=app, write=write)
        keepers_registered.discard(keeper_id)
        state["warned"].discard(("keeper", keeper_id))
        record.pop("keeper", None)
        write("keeper: {!r} is no longer the keeper of {!r}".format(
            keeper.get("name"), record.get("name")))
        return True

    # ------------------------------------------------------------ 建物（`modfacility`）
    def facility_id_for(area_id):
        """その土地の物件の id。土地ごとに1軒なので、土地の id をそのまま鍵にする。"""
        return modfacility.make_id(OWNER, str(area_id))

    def record_of(app, facility_id):
        """建物の id から契約を引く。無ければ None。"""
        for record in contracts_of(current_key(app)):
            if str(record.get("facility") or "") == str(facility_id):
                return record
        return None

    def home_choices(app, facility_id):
        """建物の中の選択肢。出口はローダが足すので、ここには入れない。

        契約が切れた建物（取り壊し待ち）では滞在も保管庫も出さない。
        滞在の最中（活動の選択肢）に混ぜないのはローダの判定（`is_top_screen`。TECH.md §5.8）。
        ここでは画面を見ない（MOD の旗で見ていると、終える処理の中の組み直しに間に合わない）。
        """
        record = record_of(app, facility_id)
        if record is None or record.get("lapsed"):
            return []
        return [{"key": "stay", "label": STAY_LABEL,
                 "on": lambda info: start_stay(info["app"])},
                {"key": "storage", "label": STORAGE_LABEL,
                 "on": lambda info: open_storage(info["app"])}]

    def register_home(record):
        """契約1つぶんの層を積む。id を返す。

        建物そのもの（建てる・壊す・控える・建て直す・道・出口・背景の呼び出し）は
        ローダの `modfacility`（TECH.md §5.8）が持つ。
        ここで宣言するのは、この MOD にしか決められないものだけ。
        """
        facility_id = str(record.get("facility") or "")
        if not facility_id:
            return ""

        def choices(info):
            found = home_choices(info["app"], info["facility_id"])
            return [] if found is None else found

        def no_exit(info):
            # 滞在の最中は出口も出さない（活動の選択肢に混ぜない）。
            return [] if home_choices(info["app"], info["facility_id"]) is None \
                else info["args"]["choices"]

        modfacility.register(
            OWNER, facility_id=facility_id,
            fields={"name": record.get("name") or "",
                    "description": _description_of(record.get("kind"))},
            choices=choices, exit_label=LEAVE_LABEL,
            keep_inside=lambda info: keeps_inside(info["app"], info["facility_id"]),
            # 家では `会話する` を出さない（`modfacility` の `hide`。TECH.md §5.8）。
            hide=HIDDEN_HOME_CLASSES,
            # 絵は描かない。ゲームが名前で引いて生成する（`modfacility は絵に触らない`。TECH.md §5.8）。
            on={"choices": no_exit,
                "leave": lambda info: end_stay(info["app"], "left the building")},
            write=write)
        return facility_id

    def keeps_inside(app, facility_id):
        """中に立ったまま保存してよいか（`modfacility` が保存の直前に聞く）。

        生きている契約の建物はロードで必ず建ち直るので、中のままでよい
        （そのほうが、入ったところから続けられる）。
        切れた契約と取り壊し待ちは建ち直らないので、入口へ移してもらう
        （**建て直されない建物の id が立ち位置に残ると、その世界は二度と開けない**）。
        """
        record = record_of(app, facility_id)
        if record is None or record.get("lapsed"):
            return False
        return record not in state["pending_demolish"]

    def apply_contracts(app, world, key, why):
        """控えの契約をこの世界へ当てる。立てた棟数を返す。

        ロードの直後と、選択肢が組まれるたびに呼ばれる。
        既に立っているものは何もしない（何度呼んでも増えない）。
        """
        drop_legacy_contracts(key)
        areas = ui.areas_of_world(world)
        if not areas:
            return 0
        built = 0
        for record in contracts_of(key):
            if record.get("lapsed"):
                continue
            area_id = str(record.get("area") or "")
            if area_id not in areas:
                warn_once(("area", key, area_id),
                          "WARN {}: area {!r} is not in this world".format(
                              why, area_id))
                continue
            facility_id = register_home(record)
            if not facility_id:
                continue
            standing = bool((modfacility.registry().get(facility_id)
                             or {}).get("placed"))
            facility = modfacility.spawn(app, facility_id, area_id, world=world,
                                         write=write)
            if facility is None:
                warn_once(("hub", key, area_id),
                          "WARN {}: {!r} is not standing in area {!r}".format(
                              why, record.get("name"), area_id))
                continue
            state["warned"].discard(("hub", key, area_id))
            state["warned"].discard(("area", key, area_id))
            if not standing:
                built += 1
        if built:
            write("{}: {} building(s) standing in world {!r}".format(why, built, key))
        return built

    # ------------------------------------------------------------ 契約
    def sign(app, kind):
        """契約して建物を建てる。成否を返す。"""
        area = ui.current_area(app)
        if area is None:
            write("sign: no current area")
            return False
        area_id = ui.area_id_of(area)
        if contract_in(app, area_id) is not None:
            write("sign: a contract already exists in area {!r}".format(area_id))
            return False
        price = _price_of(kind)
        gold = ui.gold_of(app)
        if gold is None:
            write("WARN sign: cannot read the player's gold")
            return False
        if gold < price:
            screen.say(app, ui.rewrite_coins(
                "手持ちが足りない（{}G 必要だ）。".format(ui.money(price))))
            return False
        day = ui.game_day(app)
        term = 0 if kind == "owned" else lease_days(app)
        name = _name_of(kind)
        facility_id = facility_id_for(area_id)
        record = {
            "area": area_id,
            "area_name": frames.short(getattr(area, "name", ""), 40) or area_id,
            "facility": facility_id,
            "kind": kind,
            "name": name,
            # 管理人は建物が立ってから作る（`make_keeper`）。
            "keeper": None,
            "rent": price if term else 0,
            "term": term,
            "since": day,
            "due": (day + term) if (term and day is not None) else None,
            "storage": {},
            "notified": None,
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        # 層を先に積む（`modfacility` は登録されていない建物を建てない）。
        register_home(record)
        facility = modfacility.spawn(app, facility_id, area_id, write=write)
        if facility is None:
            modfacility.unregister(OWNER, facility_id, app=app, write=write)
            write("WARN sign: cannot build in area {!r}".format(area_id))
            screen.say(app, "この土地には建てられる場所が無いようだ。")
            return False
        # 管理人を決める（生成 AI に聞くのはここ1回だけ）。控えに書くだけで、
        # ここでは控えに書くだけ。名簿へ載せて主に据えるのは `seat_keeper`。
        keeper = make_keeper(app, record)
        # 代金を先に引き、引けたときだけ契約を控えに載せる。控えは即座にファイルになるので、
        # 逆の順では引けなかった回にも家が残る。
        if ui.add_gold(app, -price, on_error=lambda msg: write("WARN sign: " + msg)) is None:
            modfacility.unregister(OWNER, facility_id, app=app, write=write)
            write("WARN sign: cannot charge {}; the contract in area {!r} was not made".format(
                price, area_id))
            screen.say(app, "手続きが済まなかった。")
            return False
        bucket_of(current_key(app))["contracts"].append(record)
        save(app)
        save_soon(app, "sign")
        write("signed: {} {!r} id={} area={!r} price={} due={} keeper={}".format(
            kind, name, facility_id, area_id, price, record["due"],
            keeper.get("id") if keeper else None))
        lines = [_fmt(SIGNED_TEXT, area=record["area_name"], name=name)]
        if keeper:
            lines.append(_fmt(KEEPER_TEXT, keeper=keeper_name(app, record),
                              role=landlord.role_of(kind)))
        screen.say(app, ui.rewrite_coins(" ".join(lines)))
        return True

    def drop_contract(app, record):
        """控えから契約を落とす。建物は呼ぶ側が先に片付けること。"""
        bucket = bucket_of(current_key(app))
        bucket["contracts"] = [c for c in bucket.get("contracts") or []
                               if c is not record]
        save(app)

    def take_down(app, record, why):
        """建物を取り壊し、管理人を降ろす。中にプレイヤーが居るときは後回しにして False。

        壊すのも層を外すのもローダに任せる（`unregister` が両方やる）。
        外した時点で `modfacility` / `modnpc` の控えからも消えるので、
        次のロードで建ち直りも立ち直りもしない。
        """
        facility_id = str(record.get("facility") or "")
        if not facility_id:
            drop_keeper(app, record)
            return True
        if standing_in(app, record):
            if record not in state["pending_demolish"]:
                state["pending_demolish"].append(record)
            write("{}: the player is inside {!r}; the demolition waits".format(
                why, record.get("name")))
            return False
        # 管理人を先に降ろす（建物が消えてからだと立ち位置を戻せない）。
        drop_keeper(app, record)
        modfacility.unregister(OWNER, facility_id, app=app, write=write)
        return True

    def seize(app, record):
        """保管庫の中身を役場の預かりへ移す。移した点数を返す。

        世界で1つの保管庫のときは、**他に物件が残っているあいだは取り上げない**
        （別の家から同じ中身を開けるので、取り上げる理由が無い）。
        最後の1軒を失ったときだけ、開く手段が無くなるので役場へ移す。
        """
        if STORAGE_SHARED:
            others = [c for c in contracts_of(current_key(app))
                      if c is not record and not c.get("lapsed")]
            if others:
                write("seize: the shared storage stays ({} contract(s) left)".format(
                    len(others)))
                return 0
        items = storage_of(app, record)
        if not items:
            return 0
        bucket = bucket_of(current_key(app))
        seized = bucket.get("seized")
        if not isinstance(seized, dict):
            seized = bucket["seized"] = {}
        moved = 0
        for key, data in items.items():
            target = key if key not in seized else storage.free_key(seized, key)
            seized[str(target)] = data
            moved += 1
        set_storage(app, record, {})
        write("seized: {} item(s) from {!r}".format(moved, record.get("name")))
        return moved

    def lapse(app, record, idle=False):
        """家賃を払えなかった契約を切る。"""
        record["lapsed"] = True
        count = seize(app, record)
        standing = take_down(app, record, "lapse")
        lines = [_fmt(LAPSED_TEXT, area=record.get("area_name") or "その土地",
                      name=record.get("name") or "家")]
        if count:
            lines.append(_fmt(SEIZED_TEXT, count=count))
        if standing:
            drop_contract(app, record)
        else:
            save(app)
        write("lapsed: {!r} in area {!r} (seized {})".format(
            record.get("name"), record.get("area"), count))
        announce(app, " ".join(lines), idle=idle)

    def release(app, record):
        """自分から解約する。保管庫に残っていた品は役場の預かりへ回る。"""
        count = seize(app, record)
        standing = take_down(app, record, "release")
        if standing:
            drop_contract(app, record)
        else:
            record["lapsed"] = True
            save(app)
        lines = [_fmt(RELEASED_TEXT, area=record.get("area_name") or "その土地",
                      name=record.get("name") or "家")]
        if count:
            lines.append(_fmt(SEIZED_TEXT, count=count))
        write("released: {!r} in area {!r}".format(record.get("name"),
                                                   record.get("area")))
        screen.say(app, " ".join(lines))

    def announce(app, text, idle=False):
        """1行出す。日数送りの最中は、流れている文に割り込まず手が空いてから出す。"""
        if not text:
            return
        text = ui.rewrite_coins(text)
        if idle:
            screen.when_idle(app, lambda: screen.say(app, text),
                             proceed_on_timeout=True, tag="announce")
        else:
            screen.say(app, text)

    # ------------------------------------------------------------ 家賃
    def check_leases(app, why, idle=False):
        """期限の来た契約を精算する。日付が進んだときと画面が組まれたときに呼ぶ。

        **滞在の最中は精算しない。**
        自分の家の滞在では `VacationStartManager.execute` の中で暦が進むので、
        ここで引くと宿代の前払いと同じ区間で金が動く
        （どちらがいくら動かしたのかが所持金の差からは読めなくなる）。
        見送ったことだけ `rent_pending` に控え、滞在が終わったら `end_stay` が
        1回呼び直す。滞在が終わらないまま次に暦が進んだときも、
        そちらの `check_leases` が同じ期をまとめて払う（期限は日付で見るので取りこぼさない）。
        """
        if staying_home(app) is not None:
            if not state.get("rent_pending"):
                state["rent_pending"] = True
                write("rent: postponed while the stay is running ({})".format(why))
            return
        state["rent_pending"] = False
        day = ui.game_day(app)
        if day is None:
            return
        for record in list(contracts_of(current_key(app))):
            if not _is_lease(record) or record.get("lapsed"):
                continue
            term = int(record.get("term") or 0)
            due = record.get("due")
            if not term or not isinstance(due, int):
                continue
            paid = 0
            while day >= due:
                if not AUTO_RENEW:
                    lapse(app, record, idle=idle)
                    due = None
                    break
                rent = int(record.get("rent") or 0)
                gold = ui.gold_of(app)
                if gold is None or gold < rent:
                    lapse(app, record, idle=idle)
                    due = None
                    break
                # 引けたときだけ期限を進める（逆の順では、引けなかった回にも期限だけ延びる）。
                if ui.add_gold(app, -rent,
                               on_error=lambda msg: write("WARN rent: " + msg)) is None:
                    write("WARN rent: cannot charge {} for {!r}; the due day stays {}".format(
                        rent, record.get("name"), due))
                    break
                due += term
                paid += rent
                record["due"] = due
                record["notified"] = None
                save(app)
            if due is None:
                continue
            if paid:
                write("rent: {!r} paid {} (next due {})".format(
                    record.get("name"), paid, due))
                # 日数送りの最中なら、その処理が終わって手が空いてから保存する
                # （移動や宿泊の途中の形をセーブに焼かない）。
                if idle:
                    screen.when_idle(app, lambda: save_soon(app, "rent"),
                                     proceed_on_timeout=True, tag="rent save")
                else:
                    save_soon(app, "rent")
                announce(app, _fmt(RENEWED_TEXT, name=record.get("name") or "家",
                                   price=ui.money(paid)), idle=idle)
                continue
            left = due - day
            if NOTICE_DAYS and left <= int(NOTICE_DAYS) and record.get("notified") != due:
                record["notified"] = due
                save(app)
                announce(app, _fmt(NOTICE_TEXT, name=record.get("name") or "家",
                                   days=left), idle=idle)

    # ------------------------------------------------------------ 画面
    def show_office(app):
        """役場の窓口（家を借りる・買う）。"""
        area = ui.current_area(app)
        area_name = frames.short(getattr(area, "name", ""), 40) or "この土地"
        record = contract_here(app)
        # 戻すための控え。自前のボタンは外しておく（戻した後に足し直されるのは
        # `refresh_choice_buttons` のフックの仕事。`309_` と同じ持ち方）。
        state["saved"] = [item for item in (getattr(app, "buttons", None) or [])
                          if not screen.mark_of(item)]
        entries = []
        if record is None:
            texts = (("rent", RENT_LABEL.format(ui.money(_price_of("rent")),
                                                lease_days(app))),
                     ("owned", BUY_LABEL.format(ui.money(_price_of("owned")))))
            for kind, label in texts:
                entry = screen.button(ui.rewrite_coins(label), mark="sign",
                                      extra={KIND_KEY: kind})
                if entry is not None:
                    entries.append(entry)
            screen.say(app, "{}で借りられる家と、買い取れる家。".format(
                area_name))
        else:
            entry = screen.button(STATUS_LABEL, mark="status")
            if entry is not None:
                entries.append(entry)
            entry = screen.button(RELEASE_LABEL, mark="release")
            if entry is not None:
                entries.append(entry)
        seized = seized_of(app)
        if seized:
            entry = screen.button(
                ui.rewrite_coins(RECLAIM_LABEL.format(ui.money(int(RECLAIM_FEE)))),
                mark="reclaim")
            if entry is not None:
                entries.append(entry)
        cancel = screen.button(CANCEL_LABEL, mark="cancel")
        if cancel is not None:
            entries.append(cancel)
        if not entries:
            write("WARN office: could not build the desk")
            return
        screen.apply_buttons(app, entries, "office")

    def show_status(app):
        """契約の中身を1行で出して、元の画面へ戻す。"""
        record = contract_here(app)
        if record is None:
            screen.say(app, "この土地に契約は無い。")
        else:
            day = ui.game_day(app)
            due = record.get("due")
            if record.get("kind") == "owned":
                line = "{}は買い取った物件で、期限は無い。".format(record.get("name"))
            elif isinstance(due, int) and isinstance(day, int):
                line = "{}の契約はあと{}日、家賃は{}日ごとに{}G。".format(
                    record.get("name"), due - day, record.get("term"),
                    ui.money(record.get("rent")))
            else:
                line = "{}の契約は続いている。".format(record.get("name"))
            items = storage_of(app, record)
            if items:
                line += " {}には{}点。".format(
                    STORAGE_NAME if STORAGE_SHARED else "保管庫", len(items))
            screen.say(app, ui.rewrite_coins(line))
        back(app, "status")

    def back(app, why="back"):
        """自前の画面から役場の選択肢へ戻す。

        `refresh_choice_buttons` は `to_display_buttons` を組み直すだけで、
        施設の選択肢そのものは作らない（GAME.md §2.3）。
        だから戻す先は**開く前に控えたもの**で、窓口のボタンは
        塗り直しの中でフックが足し直す（`309_` と同じ形）。
        """
        saved, state["saved"] = state["saved"], None
        write("back: {} ({} entries)".format(
            why, len(saved) if saved is not None else "keep"))
        screen.apply_buttons(app, saved, "back")

    def reclaim(app):
        """役場の預かり品を引き取る。"""
        seized = seized_of(app)
        if not seized:
            screen.say(app, "預かっている品は無い。")
            back(app, "nothing seized")
            return
        fee = int(RECLAIM_FEE)
        gold = ui.gold_of(app)
        if gold is None or gold < fee:
            screen.say(app, ui.rewrite_coins(
                "引き取り料の{}G が足りない。".format(ui.money(fee))))
            back(app, "cannot pay the fee")
            return
        player = getattr(app, "player", None)
        inv = storage.inventory_dict(player)
        if inv is None:
            write("WARN reclaim: cannot read the player's inventory")
            back(app, "no inventory")
            return
        # 持ち物に戻った品だけを数え、戻らなかった品は預かりに残す
        # （`storage.fill` と同じ確かめ方）。控えから消した品は世界から消える。
        moved, left = 0, {}
        for key, data in sorted(seized.items()):
            target = storage.free_key(inv, key)
            try:
                item = app.generate_item_from_dict(dict(data), str(target), player)
            except Exception:
                ctx.log_exc("real estate: cannot rebuild a seized item")
                left[key] = data
                continue
            if item is not None and inv.get(str(target)) is not item:
                inv[str(target)] = item
            if str(target) in inv:
                moved += 1
            else:
                write("WARN reclaim: {} {} did not land in the inventory".format(
                    key, storage.describe(data)))
                left[key] = data
        if not moved:
            # 1点も戻せなかった回は料金を取らない（預かりはそのまま）。
            write("WARN reclaim: nothing came back; the fee was not charged")
            screen.say(app, "預かり品を受け取れなかった。")
            back(app, "nothing reclaimed")
            return
        ui.add_gold(app, -fee, on_error=lambda msg: write("WARN reclaim: " + msg))
        bucket_of(current_key(app))["seized"] = left
        save(app)
        write("reclaimed: {} item(s) for {}{}".format(
            moved, fee, " ({} left)".format(len(left)) if left else ""))
        screen.say(app, ui.rewrite_coins(_fmt(RECLAIMED_TEXT, count=moved)))
        save_soon(app, "reclaim")
        back(app, "reclaimed")

    # ------------------------------------------------------------ 滞在
    def hold_owner(app, record):
        """滞在の主を決める。`(据えた id, 借り物か)`。

        ゲームの宿泊は主を名簿から引くので、主のいない施設では落ちる
        （`KeyError: None`。VERIFICATION.md §3.62）。
        据えるのはその建物の管理人で、実体は `modnpc` が持つ（`seat_keeper`）。
        据えたままでよく、保存の直前の引き上げはローダがやる。

        管理人が組めないときだけ**滞在のあいだ役人を借りる**（`release_owner` で戻す）。
        借り物を主にすると宿の主人を見る MOD がその役人を宿の主人と読むので、
        落ちたことは WARN に残す。
        """
        area = ui.current_area(app)
        facility = modfacility.facility_of(app, record.get("facility"))
        if facility is None:
            return None, False
        keeper_id = seat_keeper(app, record, facility)
        if keeper_id and str(getattr(facility, "owner", "")) == str(keeper_id):
            write("stay: the owner of {!r} is its keeper {!r}".format(
                record.get("name"), keeper_name(app, record)))
            return keeper_id, False
        owner = landlord.owner_candidate(app, area, facility, write=write)
        if owner is None:
            write("WARN stay: {!r} has no keeper and nobody can stand in".format(
                record.get("name")))
            return None, False
        state["owner_was"] = getattr(facility, "owner", None)
        try:
            facility.owner = owner
        except Exception:
            ctx.log_exc("real estate: cannot set the owner of the building")
            return None, False
        write("WARN stay: {!r} has no keeper; borrowing {!r} for this stay".format(
            record.get("name"), owner))
        return owner, True

    def release_owner(app):
        """滞在のあいだ借りた役人を元へ戻す。管理人は据えたままでよい（`modnpc` が持つ）。"""
        home = state.get("free_stay")
        if not isinstance(home, dict) or not home.get("owner") \
                or not home.get("borrowed"):
            return
        facility = modfacility.facility_of(app, home.get("facility"))
        if facility is not None:
            try:
                facility.owner = state.get("owner_was")
            except Exception:
                ctx.log_exc("real estate: cannot restore the owner")
        state["owner_was"] = None
        home["owner"] = None
        home["borrowed"] = False

    def start_stay(app):
        """宿屋の宿泊と同じ経路を、宿代を取らずに起こす。

        `VacationStartManager(app, months, quality)` は実測した署名
        （GAME.md §2.17）。日数・体力・活動の選択肢はゲームが持っているので、
        こちらが足すのは「主が名簿に居ることを確かめること」と「宿代を前払いすること」の2つ。
        """
        record = contract_here(app)
        if record is None:
            write("stay: no contract here")
            return
        cls = getattr(main_module(), STAY_CLS, None)
        if cls is None:
            write("WARN stay: __main__.{} is not available".format(STAY_CLS))
            return
        try:
            phase = cls(app, int(stay_months(app)), str(STAY_QUALITY))
        except Exception:
            ctx.log_exc("real estate: cannot build {}".format(STAY_CLS))
            return
        state["free_stay"] = {"facility": str(record.get("facility")),
                              "area": str(record.get("area")),
                              "name": record.get("name")}
        owner, borrowed = hold_owner(app, record)
        state["free_stay"]["owner"] = owner
        state["free_stay"]["borrowed"] = borrowed
        write("stay: starting {} months={} quality={!r} at {!r}".format(
            STAY_CLS, stay_months(app), STAY_QUALITY, record.get("name")))
        screen.start_phase(app, phase, STAY_LABEL)

    def inside_home(app, record):
        """いま**自分の家**の中に立っているか（切れた契約の建物は自分の家ではない）。

        滞在も保管庫も、契約が生きているあいだのものなので、ここで切れた分を落とす。
        取り壊してよいかの判定にこれを使ってはいけない。
        `lapse` は先に「切れた」印を立てるので、
        **中に居るのに居ないと読めて、そのまま壊す**（VERIFICATION.md §3.62）。
        そちらは `standing_in` を見る。
        """
        if record is None or record.get("lapsed"):
            return False
        return standing_in(app, record)

    def standing_in(app, record):
        """いまその契約の建物に立っているか（契約が切れていても見る）。

        見分けはローダに任せる（`modfacility.inside`）。
        居場所が書き換わらなかったときにこちらが起こした移動の控えで補うところまで、
        あちらが持っている。
        """
        if record is None:
            return False
        facility_id = str(record.get("facility") or "")
        return bool(facility_id) and bool(modfacility.inside(app, facility_id))

    def close_stay_after_activity(app, which):
        """活動を1つ終えたら、その滞在を締める（1泊＝活動1回）。

        素のゲームは宿泊1回につき活動1回で、`327_inn_quality` は部屋の等級で
        それを増やす。自分の家では**等級によらず1回**に固定する
        （もう一度過ごしたければ「滞在する」を押し直せばよい。宿代は取られない）。

        締めるのは画面が落ち着いてから。
        活動の描写が流れている最中に割り込むと、文の途中で場面が変わる。
        """
        if staying_home(app) is None:
            return
        screen.when_idle(app, lambda: end_home_stay(app, "{} finished".format(which)),
                         proceed_on_timeout=True, tag="one activity")

    def end_home_stay(app, why):
        """自分の家の滞在を締める（ゲームの `VacationEndManager` を起こす）。起こすのは1回だけ。

        起こせなかったときは待機表示を解き、ゲームの選択肢
        （`まだ宿泊する` / `宿泊を終える`）を見せる。そこから自分で終えられる。
        """
        if staying_home(app) is None:
            write("stay: {}, but the stay is already over".format(why))
            uncover(app, "the stay is already over")
            return
        if state.get("ending"):
            return
        cls = getattr(main_module(), END_CLS, None)
        if cls is None:
            write("WARN stay: __main__.{} is not available".format(END_CLS))
            uncover(app, "cannot end the stay")
            return
        try:
            phase = cls(app)
        except Exception:
            ctx.log_exc("real estate: cannot build {}".format(END_CLS))
            uncover(app, "cannot end the stay")
            return
        state["ending"] = True
        write("stay: {}; ending the stay (one activity per stay)".format(why))
        screen.start_phase(app, phase, END_LABEL)

    def staying_home(app):
        """いま自分の建物で滞在中か。宿屋での宿泊と取り違えないための確認。"""
        home = state.get("free_stay")
        if not isinstance(home, dict):
            return None
        if not inside_home(app, contract_here(app)):
            return None
        return home

    # ------------------------------------------------------------ 保管庫
    def open_storage(app):
        """保管庫の窓を開く。左がプレイヤー、右がこの建物（仲間との受け渡しと同じ窓）。"""
        record = contract_here(app)
        if record is None:
            write("storage: no contract here")
            return
        player = getattr(app, "player", None)
        if storage.inventory_dict(player) is None:
            write("WARN storage: cannot read the player's inventory")
            return
        gather_into_shared(app)
        # 窓の右に立つのはその建物の管理人。まだ名簿に居なければここで据える
        # （滞在を1度もしていない家でも、窓を開けたら鍵を預かっている人が出る）。
        # 引けないときだけ、その窓の間だけ生きる持ち主を立てる。
        #
        # **見出しに管理人の名前は出さない**（本人の指定）。
        # この人は「会話する」の一覧に出さないので、名乗る場面が無いまま名前だけが並ぶ
        # （前からある契約に後から決まった管理人には、契約のときの紹介の一文も無い）。
        # 見出しは共有なら保管庫の名、そうでなければ建物の名。
        seat_keeper(app, record, modfacility.facility_of(app, record.get("facility")))
        holder = keeper_character(app, record)
        name = STORAGE_NAME if STORAGE_SHARED else (record.get("name") or "保管庫")
        if holder is None:
            write("WARN storage: {!r} has no keeper; the window borrows a holder"
                  .format(record.get("name")))
            try:
                holder = storage.make_holder(app, name, write=write)
            except Exception:
                ctx.log_exc("real estate: cannot make the storage holder")
                return
        if holder is None:
            return
        items = storage_of(app, record)
        # 管理人の持ち物は**窓を開いている間だけ**の姿。前の窓の残りが在れば先に払う
        # （控えが正で、実体はそこから毎回作り直す）。
        empty_holder(holder)
        try:
            storage.fill(app, holder, items, write=write)
        except Exception:
            ctx.log_exc("real estate: cannot rebuild the stored items")
            return
        state["storage"] = {"holder": holder, "area": str(record.get("area")),
                            "name": name}
        player_name = frames.short(frames.text_of(player, "name"), 40) or "所持品"

        def show():
            # **窓を開くのはメインスレッドから**。
            # ここは `process_choice` が渡したワーカースレッドの中なので
            # （GAME.md §2.1）、そのまま呼ぶと Kivy が
            # `Cannot change graphics instruction outside the main Kivy thread` を出し、
            # 窓が半端に開いたまま操作を受け付けなくなる（VERIFICATION.md §3.62）。
            # ゲーム自身も売買の窓を Clock でメインスレッドへ回している（GAME.md §2.13.1）。
            try:
                app.toggle_twin_inventory_window(player, holder, player_name,
                                                 storage.SITUATION)
            except Exception:
                ctx.log_exc("real estate: toggle_twin_inventory_window failed")
                state["storage"] = None
                return
            # 借りているのは店の売買の窓なので、右の見出しは「所持品」で固定されている。
            # 窓が組み上がる次のフレームで、この保管庫の名前に描き替える（`402_` と同じ）。
            screen.schedule(lambda: rename_right_header(app, name), 0)
            write("storage: opened {!r} with {} item(s) ({})".format(
                name, len(items), "shared" if STORAGE_SHARED else "this building"))

        screen.schedule(show, 0)

    def rename_right_header(app, name):
        """2枚並びの窓の右側の見出しを保管庫の名前にする。

        文言「所持品」のウィジェットを HUD から探して、最初の1つだけ書き換える。
        """
        hud = ui.find_hud(app)
        if hud is None:
            return
        for widget in ui.walk_widgets(hud, oldest_first=True):
            text = frames.text_of(widget, "text")
            if isinstance(text, str) and text.strip() == "所持品":
                try:
                    widget.text = name
                    write("storage: the right header is {!r}".format(name))
                except Exception:
                    ctx.log_exc("real estate: cannot rename the right header")
                return
        write("WARN storage: the right header label was not found")

    def storage_holder():
        open_window = state.get("storage")
        return open_window.get("holder") if isinstance(open_window, dict) else None

    def empty_holder(holder):
        """窓の持ち主の持ち物を空にする。外した点数を返す。

        預けた品は 330 の控えが正で、持ち主はその写しを窓のあいだだけ持つ。
        管理人は名簿に居る人なので、持たせたままにすると `modnpc` の控えにも
        同じ品が写り（保存のたびに持ち物まで控える。TECH.md §5.7）、
        次に開いたときに控えと写しで二重になる。
        """
        inv = storage.inventory_dict(holder) if holder is not None else None
        if not isinstance(inv, dict) or not inv:
            return 0
        count = len(inv)
        inv.clear()
        return count

    def record_of_open_storage(app):
        open_window = state.get("storage")
        if not isinstance(open_window, dict):
            return None
        return contract_in(app, open_window.get("area"))

    def write_down(app, why):
        """開いている保管庫の中身を控えへ写す。写した点数を返す。"""
        holder = storage_holder()
        record = record_of_open_storage(app)
        if holder is None or record is None:
            return 0
        items, lost = storage.dump(holder, write=write)
        if items is None:
            write("WARN {}: cannot read the holder's inventory".format(why))
            return 0
        set_storage(app, record, items)
        write("{}: the storage now holds {} item(s){}".format(
            why, len(items), " (lost {})".format(lost) if lost else ""))
        return len(items)

    def sync_storage(app, widget, old_owner, new_owner):
        """窓の中でアイテムが片側から片側へ移った直後に、持ち物の実体を揃える。

        本体の `InventoryItem.change_inventory` は画面側の登録を動かすだけなので、
        辞書・`Item.id`・`Item.obtainer` はこちらで合わせる（`402_` と同じ手順）。
        """
        item = getattr(widget, "item_instance", None)
        if item is None:
            write("WARN storage sync: the widget has no item_instance")
            return
        old_inv = storage.inventory_dict(old_owner)
        new_inv = storage.inventory_dict(new_owner)
        if not isinstance(old_inv, dict) or not isinstance(new_inv, dict):
            write("WARN storage sync: unreadable inventory")
            return
        widget_id = getattr(widget, "item_id", None)
        old_key = storage.key_for_instance(old_inv, item, widget_id)
        # 預ける品が装備中なら、持ち主も装備欄もまだ揃っているこの時点で
        # ゲーム自身に外させる（辞書だけ直すと「装備中」の表示が残り、
        # そこから外そうとして本体が落ちる。`402_` の実機）。
        if _is_equipped(old_owner, item, (old_key, widget_id,
                                          getattr(item, "id", None))):
            try:
                item.unequip()
            except Exception:
                ctx.log_exc("real estate: unequip before storing failed")
        for key, value in list(old_inv.items()):
            if value is item:
                old_inv.pop(key, None)
        for key, value in list(new_inv.items()):
            if value is item:
                new_inv.pop(key, None)
        new_key = storage.free_key(new_inv, old_key or widget_id
                                   or getattr(item, "id", None))
        new_inv[new_key] = item
        for attr, value in (("id", str(new_key)), ("obtainer", new_owner)):
            try:
                setattr(item, attr, value)
            except Exception:
                pass
        try:
            widget.item_id = str(new_key)
        except Exception:
            pass
        write("storage sync: {!r} {} -> {} (key {} -> {})".format(
            frames.short(getattr(item, "name", "?"), 40),
            frames.short(frames.text_of(old_owner, "name"), 20),
            frames.short(frames.text_of(new_owner, "name"), 20), old_key, new_key))
        write_down(app, "storage sync")
        save_soon(app, "storage sync")

    def _is_equipped(owner, item, keys):
        """その品が装備欄から参照されているか（実行時は id でも実体でも入る）。"""
        equipments = getattr(owner, "equipments", None)
        if not isinstance(equipments, dict):
            return False
        wanted = {str(k) for k in keys if k is not None}
        for value in equipments.values():
            if value is item:
                return True
            if value is not None and str(value) in wanted:
                return True
        return False

    def close_storage(app, why):
        """窓が閉じたときの後始末。"""
        if state.get("storage") is None:
            return
        write_down(app, why)
        # 控えへ写したら持ち主の手元は空にする（管理人に品を住まわせない）。
        empty_holder(storage_holder())
        state["storage"] = None
        write("storage: closed ({})".format(why))

    # ------------------------------------------------------------ 選択肢の組み立て
    def drop_unwanted_activities(app, buttons):
        """自分の家の滞在では出さない活動（`HIDDEN_ACTIVITY_CLASSES`）を落とす。

        落としたら True。
        宿屋の側には触らない（ここは自分の建物での滞在の最中にしか走らない）。

        見分けるのは spec のクラス名。文言で見ると、同じ言葉を使う
        ゲーム側の別のボタンまで巻き込む（GAME.md §2.2）。
        """
        dropped = [(entry.get("text"), ui.spec_cls_name(entry)) for entry in buttons
                   if ui.spec_cls_name(entry) in HIDDEN_ACTIVITY_CLASSES]
        if not dropped:
            return False
        buttons[:] = [entry for entry in buttons
                      if ui.spec_cls_name(entry) not in HIDDEN_ACTIVITY_CLASSES]
        write("stay menu: dropped {}".format(dropped))
        return True

    def cover_after_activity(app):
        """自分の家で活動を1つ終えた後の選択肢を、締め終えるまで待機表示（…）で覆う。

        ゲームは活動の後に `まだ宿泊する` と `宿泊を終える` を並べ、
        `327_inn_quality` は残りの回数があれば活動を先頭に足し直す。
        こちらは1泊＝活動1回なので手が空いたら締めるが、手が空くまで
        （`when_idle` の待ち）その選択肢が見えていた（実機 2026-09-25）。

        選択肢そのもの（`app.buttons`）には触らない。覆うのは表示だけで
        （`screen.busy_on`。GAME.md §2.4）、締められなかったときは覆いを解けば
        ゲームの選択肢がそのまま押せる。
        `when_idle` は自分の出した待機表示を待たない（`_others_busy`）ので、締めは遅れない。

        見分けるのは `まだ宿泊する`（`VacationStartManager`）と `宿泊を終える` の対。
        最初の活動の選択肢には `VacationStartManager` が無い（実機のログ）。

        覆ったら True。
        """
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return False
        if ui.find_spec_button(buttons, STAY_CLS) is None \
                or ui.find_spec_button(buttons, END_CLS) is None:
            return False
        home = staying_home(app)
        if home is None or state.get("covered"):
            return False
        # 覆うのは1回の滞在につき1度。解いたとき（締められなかったとき）の塗り直しで
        # 同じ選択肢がまた組まれるので、ここで覆い直すと二度と解けない。
        if state.get("covered_for") is home:
            return False
        state["covered"] = True
        state["covered_for"] = home
        write("stay menu: covered {} until the stay ends (one activity per stay)".format(
            [entry.get("text") for entry in buttons if isinstance(entry, dict)]))
        # その場で覆う（ワーカースレッドのまま）。ゲームは組んだ次のフレームで塗るので、
        # Clock へ回すと1フレームだけ選択肢が見える（実機 2026-09-25）。
        # `busy_on` が直に触るのは旗と一覧だけで、画面に触る手は向こうが Clock へ回す。
        screen.busy_on(app)
        return True

    def uncover(app, why):
        """覆いを解く。選択肢を塗り直すので、そのときの `app.buttons` が見える。"""
        if not state.get("covered"):
            return
        state["covered"] = False
        write("stay menu: uncovered ({})".format(why))
        screen.schedule(lambda: screen.busy_off(app))

    def our_labels(app):
        """残骸の掃除に使う文言（`prune_stale`）。

        印はセーブに焼かれないので、ロードや再注入のあと**印を失った自分のボタン**が
        画面に戻っている。文言でしか見分けられない（TECH.md §6.2）。
        ここに並ぶのは役場の窓口とその確認画面のぶんだけで、
        建物の名前と中の選択肢はローダが自分の印で掃除する（TECH.md §5.8）。
        """
        return list(OUR_LABEL_PREFIXES)

    def is_facility_screen(buttons):
        """施設の選択肢の画面か（移動のボタンが1つでもある）。"""
        if not isinstance(buttons, list):
            return False
        return any(ui.spec_cls_name(entry) == MOVE_CLS for entry in buttons)

    def add_office_button(app, buttons):
        entry = screen.button(OFFICE_LABEL, mark="office")
        if entry is None:
            return False
        buttons.insert(max(len(buttons) - 1, 0), entry)
        return True

    def retry_when_idle(app):
        """流し込みの最中に来た足し直しを、手が空いてからやり直す。

        `maintain_buttons` は本文が流れている間は何もしない（GAME.md §2.6）。
        ところが**自前の画面から戻す塗り直しは、いつも `screen.say` の直後**に走る。
        そこで黙って戻ると、次にゲームが選択肢を組み直すまで出番が来ない。
        役場に立ったままではそれが来ず、解約・契約・引き取りの後に
        窓口のボタンが消えたままになる（VERIFICATION.md §3.62）。

        見張りは同時に1つだけ立てる。塗り直しは1手に何度も走るので、
        素直に立てると同じ見張りがその回数だけ並ぶ。
        """
        if state.get("retry"):
            return
        state["retry"] = True

        def again():
            state["retry"] = False
            try:
                maintain_buttons(app)
            except Exception:
                ctx.log_exc("real estate: cannot maintain the choices (idle)")

        screen.when_idle(app, again, proceed_on_timeout=True, tag="retry")

    def maintain_buttons(app):
        """役場の窓口を足す。何度呼んでも増えない。

        建物の中の選択肢・出口・建物への道は**ローダが出す**（TECH.md §5.8）。
        ここに残るのは、ゲームの施設（役場）に足す1つだけ。
        """
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return
        # 宿屋の部屋の選択肢が出ていれば、そこから宿泊の月数を覚える。
        # 読むだけなので、本文が流れていても構わない。
        note_inn_months(app, buttons)
        if ui.busy_signals(app):
            # 本文が流れている最中は触らない。手が空いてからやり直す。
            retry_when_idle(app)
            return
        if state.get("free_stay") is not None:
            # 滞在の最中。並んでいるのはゲームの活動の選択肢（休養・訓練・労働…）で、
            # 自分の家では出さない活動をここで落とす。
            if drop_unwanted_activities(app, buttons):
                screen.apply_buttons(app, None, "stay menu")
            return
        if not is_facility_screen(buttons):
            return
        screen.prune_stale(buttons, our_labels(app))
        if any(screen.mark_of(entry) for entry in buttons):
            return
        if landlord.at_facility_type(app, OFFICE_FACILITY_TYPE) \
                and add_office_button(app, buttons):
            screen.apply_buttons(app, None, "office")

    # ------------------------------------------------------------ 自前のフェーズ
    class EstatePhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**（セーブに焼かれる）。"""

        def __init__(self, app, action, kind):
            self.app = app
            self.action = action
            self.kind = kind

        def execute(self, choice_text):
            state["acting"] = True
            try:
                run_action(self.app, self.action, self.kind)
            except Exception:
                ctx.log_exc("real estate: phase {!r} failed".format(self.action))
            finally:
                state["acting"] = False

    def run_action(app, action, kind):
        if action == "office":
            show_office(app)
        elif action == "sign":
            # 契約できてもできなくても、戻す先は同じ役場の選択肢。
            # 建物が増えていれば、塗り直しの中でフックが窓口を足し直す。
            sign(app, kind)
            back(app, "signed")
        elif action == "status":
            show_status(app)
        elif action == "release":
            record = contract_here(app)
            if record is not None:
                release(app, record)
            back(app, "released")
        elif action == "reclaim":
            reclaim(app)
        elif action == "stay":
            start_stay(app)
        elif action == "storage":
            open_storage(app)
        elif action == "cancel":
            back(app, "cancelled")
        else:
            write("WARN unknown action {!r}".format(action))

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """選択肢が組み直されるたびに、建物を当て直して自前のボタンを足す。

        最後にローダの塗り直しをもう一度呼ぶ。
        あちらの関所も同じ場面を包んでいるが、**どちらが内側かは適用順で変わる**
        （フレームワークどうしは順序を約束しない。TECH.md §5.8）。
        先に走られると、建物を当て直す前の画面で判断されてしまう。
        二度呼んでも増えない作りなので、ここで順序を確かめる。
        """
        result = orig(self, reset_page, *args, **kwargs)
        try:
            cover_after_activity(self)
        except Exception:
            ctx.log_exc("real estate: cannot cover the choices after the activity")
        try:
            apply_contracts(self, getattr(self, "world", None), current_key(self),
                            "screen")
            check_leases(self, "screen")
            flush_demolitions(self)
            maintain_buttons(self)
            modfacility.maintain_buttons(self, write=write)
        except Exception:
            ctx.log_exc("real estate: cannot maintain the choices")
        return result

    def flush_demolitions(app):
        """中に居たせいで残っていた取り壊しを、外に出た後で片付ける。"""
        if not state["pending_demolish"]:
            return
        for record in list(state["pending_demolish"]):
            if take_down(app, record, "pending"):
                state["pending_demolish"].remove(record)
                drop_contract(app, record)

    def sweep_lapsed(app, key, why):
        """切れたまま控えに残っている契約を片付ける。片付けた数を返す。

        中に居るあいだの期限切れ・解約は取り壊しを後回しにするが（`pending_demolish`）、
        その覚えはメモリだけなので、外へ出ずに終了するとロードで空に戻る。
        残った契約は `contract_here` が拾い、役場は「契約を確かめる／解約する」のままになる。
        ロードの直後は建物の中に居ない（切れた建物は建て直さず、立ち位置はローダが入口へ直す）ので、
        ここで必ず壊せる。

        層を先に積むのは、`unregister` が登録の無い id の控え
        （`state/modfacility` と `state/modnpc`）を落とさないため。
        """
        done = 0
        for record in contracts_of(key):
            if not record.get("lapsed"):
                continue
            register_home(record)
            register_keeper(app, record)
            if take_down(app, record, why):
                drop_contract(app, record)
                done += 1
        if done:
            write("{}: swept {} lapsed contract(s)".format(why, done))
        return done

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        entry = ui.pressed_entry(self, button_index)
        action = screen.mark_of(entry)
        if action is None:
            return orig(self, button_index, *args, **kwargs)
        if state["acting"]:
            write("ignored {!r}: the previous press is still running".format(
                entry.get("text") if isinstance(entry, dict) else None))
            return None
        text = (entry.get("text") if isinstance(entry, dict) else None) or OFFICE_LABEL
        kind = entry.get(KIND_KEY) if isinstance(entry, dict) else None
        write("pressed {!r} ({} kind={})".format(text, action, kind))
        screen.start_phase(self, EstatePhase(self, action, kind), text,
                           fallback=lambda: run_action(self, action, kind))
        return None

    @ctx.wrap("__main__:World.__init__", required=False, safe=True)
    def world_loaded(orig, self, save_data_dict, app, *args, **kwargs):
        """セーブを読み込んだ直後、契約中の建物をこの世界へ建て直す。"""
        result = orig(self, save_data_dict, app, *args, **kwargs)
        try:
            key = playthrough_key_of_dict(save_data_dict, None) or playthrough_key(app)
            if key and key != UNKNOWN_WORLD:
                worlds.forget(key)
                state["free_stay"] = None
                state["ending"] = False
                state["covered"] = False
                state["storage"] = None
                state["pending_demolish"] = []
                state["warned"] = set()
                state["key_override"] = key
                # 管理人の層はロードのたびに積み直す。周回（世界×主人公）が変わると
                # 同じ id（`keeper-<土地>`）に別の人が立つ。
                keepers_registered.clear()
                try:
                    sweep_lapsed(app, key, "load")
                    apply_contracts(app, self, key, "load")
                finally:
                    state["key_override"] = None
                write("load: the ledger of {!r} has {} contract(s)".format(
                    key, len(bucket_of(key).get("contracts") or [])))
        except Exception:
            ctx.log_exc("real estate: cannot rebuild the buildings on load")
        return result

    def quiet_shopping_flag(app):
        """保管庫を開いたまま保存するときは、売買中の旗を下ろして保存する。

        保管庫はゲームの売買の窓を借りているので、開いているあいだ
        `in_shopping` が立つ。品を移した直後にこちらが保存を呼ぶので
        （持ち物から外れた品を控えだけに残さないため）、
        **窓を開いたまま落ちた形がセーブに残る**。
        ロードで窓は開かないので、旗だけ残ると噛み合わない。
        """
        if state.get("storage") is None:
            return None
        was = getattr(app, "in_shopping", None)
        if not was:
            return None
        try:
            app.in_shopping = False
        except Exception:
            ctx.log_exc("real estate: cannot lower in_shopping")
            return None
        write("save: the storage window is open; saving with in_shopping down")
        return was

    def quiet_storage_items(app):
        """保管庫を開いたまま保存するときは、管理人の手元を空にして保存する。

        窓の右に立っているのは名簿に居る管理人で、`modnpc` の関所は保存と同じ時点で
        MOD の NPC の持ち物まで控える（TECH.md §5.7）。開いたまま保存すると
        330 の控えと二重になり、次に開いたときに品が増える。
        中身は先に控えへ写し、保存の間だけ手元から外す。
        """
        holder = storage_holder()
        inv = storage.inventory_dict(holder) if holder is not None else None
        if not isinstance(inv, dict) or not inv:
            return None
        write_down(app, "save")
        kept = dict(inv)
        inv.clear()
        write("save: the storage window is open; {} item(s) set aside".format(
            len(kept)))
        return inv, kept

    @ctx.wrap("__main__:InstantaleApp.save_game", required=False)
    def save_game(orig, self, *args, **kwargs):
        """保管庫を開いたまま保存するときの後始末（売買中の旗と、管理人の手元）。

        立ち位置（建て直されない建物の中に居るとき入口へ移す）と、
        建物を指す選択肢の掃除はローダの関所が持つ（TECH.md §5.8）。
        生きている契約の建物は `keeps_inside` で「中のままでよい」と名乗ってある。
        """
        shopping, stored = None, None
        try:
            shopping = quiet_shopping_flag(self)
            stored = quiet_storage_items(self)
        except Exception:
            ctx.log_exc("real estate: cannot check the place before the save")
        try:
            return orig(self, *args, **kwargs)
        finally:
            if stored is not None:
                try:
                    stored[0].update(stored[1])
                except Exception:
                    ctx.log_exc("real estate: cannot put the stored items back")
            if shopping is not None:
                try:
                    self.in_shopping = shopping
                except Exception:
                    ctx.log_exc("real estate: cannot restore in_shopping")

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False)
    def elapse_days(orig, self, days, *args, **kwargs):
        """日付が進んだら家賃を精算する。日付を動かすのはここ1箇所（GAME.md §2.16）。

        滞在の最中に進んだぶんは `check_leases` が見送る（精算は `end_stay` で1回）。
        """
        result = orig(self, days, *args, **kwargs)
        try:
            check_leases(self, "elapse", idle=True)
        except Exception:
            ctx.log_exc("real estate: cannot settle the rent")
        return result

    # ------------------------------------------------------ 滞在の描写
    def player_name(app):
        name = getattr(getattr(app, "player", None), "name", "")
        return name.strip() if isinstance(name, str) and name.strip() else "プレイヤー"

    def stay_place(app):
        """描写に添える `(いま居る場所, この街の自分の家, 自分の家か)`。

        この街に契約が無ければ None ＝ 頼み文には触らない。
        取り違えを持ち込んでいない街の描写はゲームのままでよい。
        """
        record = contract_here(app)
        if record is None:
            return None
        home = str(record.get("name") or "").strip() or _name_of(record.get("kind"))
        if standing_in(app, record):
            return home, home, True
        facility, _node = ui.find_facility(ui.current_area(app),
                                           modfacility.player_facility_id(app))
        place = ui.facility_name(app, facility)
        return (place, home, False) if place else None

    def name_the_place(app, text):
        """滞在の描写の頼み文に、いま居る場所の1文を足す。触らないなら None。"""
        if not isinstance(text, str) or SCENE_MARK not in text \
                or SCENE_HERE not in text or SCENE_HEAD in text \
                or not any(word in text for word in SCENE_WORDS):
            return None
        found = stay_place(app)
        if found is None:
            return None
        place, home, at_home = found
        line = (_fmt(HOME_SCENE_TEXT, name=player_name(app), home=home) if at_home
                else _fmt(INN_SCENE_TEXT, place=place, name=player_name(app),
                          home=home))
        write("stay scene: telling the writer the stay is at {!r} (home {!r})".format(
            place, home))
        return text.replace(SCENE_MARK, line + "\n" + SCENE_MARK, 1)

    def tell_where_the_stay_is(texts, site):
        """ローダから呼ばれる（`llm.wrap_outgoing`）。触らないときは None。"""
        app = ui.find_app()
        if app is None:
            return None
        changed = [name_the_place(app, text) for text in texts]
        if not any(text is not None for text in changed):
            return None
        return [new if new is not None else old for old, new in zip(texts, changed)]

    # LLM へ出ていく文章を捕まえる口はローダが持っている
    # （ローカルの3点・クラウドの別名・後から生える別名の掛け直しまで向こうの担当。
    # GAME.md §2.12）。
    llm.wrap_outgoing(ctx, tell_where_the_stay_is, label="real estate")

    @ctx.wrap("__main__:VacationStartManager.execute", required=False)
    def vacation_start(orig, self, choice_text="", *args, **kwargs):
        """自分の建物での滞在は宿代を取らない。宿屋での宿泊には触らない。

        取り方は**前払い**（`314_area_move_custom` の運賃・`315_vacation_custom`
        の宿代と同じ形）。ゲームが引く額を先に足しておき、ゲームが引いて元に戻す。
        引かせてから差額を返す形はやめた。差を取る区間が `execute` 全体で、
        その中で暦も進むため、同じ区間で金を動かした他の MOD のぶんまで
        巻き込んでいた（VERIFICATION.md §3.62）。

        ゲーム側が途中で落ちたときも同じ帳尻を通してから操作を戻す。
        ここで例外をそのまま通すと、`execute` がワーカースレッドごと終わって
        **画面が「…」のまま戻らない**（VERIFICATION.md §3.62）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        home = staying_home(app) if app is not None else None
        if home is None:
            # 宿屋（か、よその MOD の宿）の宿泊。
            # **実際に何日進んだか**をここで測る（月数×30 とは限らない）。
            day_before = ui.game_day(app) if app is not None else None
            result = orig(self, choice_text, *args, **kwargs)
            try:
                day_after = ui.game_day(app) if app is not None else None
                if isinstance(day_before, int) and isinstance(day_after, int) \
                        and not inside_mod_building(app):
                    remember_stay_days(app, day_after - day_before,
                                       getattr(self, "months", None))
            except Exception:
                ctx.log_exc("real estate: cannot measure the stay")
            return result
        quality = getattr(self, "quality", None) or STAY_QUALITY
        before = ui.gold_of(app)
        day_before = ui.game_day(app)
        prepaid = prepay_room(app, quality, before)
        try:
            result = orig(self, choice_text, *args, **kwargs)
        except Exception as exc:
            # 何を引き損ねたかは例外自身が持っている（GAME.md §2.28）。
            # 施設のどの値が足りなかったのかを、その場の値と一緒に残す。
            write("WARN stay: the game's stay failed: {}({!r}) owner={!r} {}".format(
                type(exc).__name__, getattr(exc, "args", ()), home.get("owner"),
                describe_building(app, home)))
            ctx.log_exc("real estate: the game's stay failed at {!r}".format(
                home.get("name")))
            settle_room(app, before, prepaid, "stay failed")
            end_stay(app, "the stay failed")
            recover(app, "stay failed")
            return None
        settle_room(app, before, prepaid, "stay")
        try:
            # 自分の家の滞在でも日数は測れる。
            # **渡した月数はゲームの宿屋と同じ**なので、他 MOD の日数の細工も
            # そのまま乗る（宿屋へ行かなくても、1期の長さがここで決まる）。
            day_after = ui.game_day(app)
            if isinstance(day_before, int) and isinstance(day_after, int):
                remember_stay_days(app, day_after - day_before,
                                   getattr(self, "months", None), why="the home stay")
        except Exception:
            ctx.log_exc("real estate: cannot measure the home stay")
        return result

    def describe_building(app, home):
        """建物のいまの姿。落ちたときの手がかりとしてログに添える。"""
        facility = modfacility.facility_of(app, home.get("facility"))
        if facility is None:
            return "the building is not standing"
        roster = getattr(getattr(app, "world", None), "characters", None)
        spot = (modfacility.registry().get(str(home.get("facility"))) or {}).get("placed")
        return ("facility={} type={!r} owner={!r} characters={} node={} "
                "roster={}".format(
                    modfacility.facility_id_of(facility),
                    ui.facility_type_of(facility),
                    getattr(facility, "owner", None),
                    landlord.id_list_of(facility, "characters"),
                    spot[1] if spot and len(spot) > 1 else "?",
                    len(roster) if isinstance(roster, dict) else "?"))

    def prepay_room(app, quality, before):
        """ゲームが引く宿代を先に足しておく。足せた額を返す（足さなければ 0）。

        額はローダの窓口に聞く（`prices.inn_room`）。
        宿代を変える MOD が入っていればその額、入っていなければゲームの値。
        **知らない等級では None** が返る。そのときは 0 のまま進み、
        帳尻の枝が引かれた額をそのまま返す（WARN が残るので気づける）。
        """
        price = prices.inn_room(app, quality, write=write)
        if not isinstance(price, int) or price <= 0 or not isinstance(before, int):
            if price is None:
                write("WARN stay: no price for the room ({!r}); "
                      "falling back to paying the difference".format(quality))
            return 0
        if ui.add_gold(app, price,
                       on_error=lambda msg: write("WARN stay: " + msg)) is None:
            return 0
        write("stay: prepaid {} for the room ({!r})".format(price, quality))
        return price

    def settle_room(app, before, prepaid, why):
        """滞在の後の帳尻。前払いが効いていれば所持金は元に戻っている。

        正常な回はここで何も動かない（足した額と引かれた額が同じ）。
        動くのは、ゲームが引かなかったとき（前払いを引き戻す）と、
        額が分からず前払いできなかったとき（引かれたぶんを返す）の2つ。
        どちらも所持金の差で当てているので、同じ区間で他の MOD が金を
        動かしていれば巻き込む。WARN を残すのはそのため。

        **前払いした回に減ったぶんは返さない。** 滞在の `execute` の中では暦が進み、
        他の MOD がその区間で金を引く（`331_` の宿に泊まっている間に来たこの MOD の家賃など）。
        差で返すと、引かれた家賃まで返して期限だけ延びる。
        前払いした回に増えたぶんは、前払いの額までを引き戻す（ゲームが引かなかった回）。
        """
        after = ui.gold_of(app)
        if not isinstance(before, int) or not isinstance(after, int):
            write("WARN {}: cannot read the gold; the books are left as they are".format(
                why))
            return 0
        off = before - after
        if off == 0:
            return 0
        if prepaid <= 0 and off < 0:
            # 前払いできなかった回に**増えた**ぶんは宿代の話ではない
            # （この区間では他の MOD も金を動かす）。取り上げない。
            write("WARN {}: the gold grew by {} during the stay; leaving it "
                  "alone".format(why, -off))
            return 0
        if prepaid > 0 and off > 0:
            write("WARN {}: the gold fell by {} more than the prepaid room ({}); "
                  "leaving it alone (other charges run inside the stay)".format(
                      why, off, prepaid))
            return 0
        if prepaid > 0:
            off = max(off, -prepaid)
        ui.add_gold(app, off, on_error=lambda msg: write("WARN {}: {}".format(why, msg)))
        write("WARN {}: the room cost {} but we prepaid {}; corrected {}".format(
            why, prepaid + off, prepaid, off))
        return off

    def end_stay(app, why):
        """滞在の後始末。据えた主を戻し、滞在の印を落とし、家賃を精算する。

        滞在の最中に来た期限は `check_leases` が見送っている。
        印を落としてから呼ぶので、ここでの精算はもう滞在の外の1回になる。
        """
        if state.get("free_stay") is None:
            return
        release_owner(app)
        write("stay: finished at {!r} ({})".format(
            (state["free_stay"] or {}).get("name"), why))
        state["free_stay"] = None
        if state.get("rent_pending"):
            try:
                check_leases(app, "after the stay", idle=True)
            except Exception:
                ctx.log_exc("real estate: cannot settle the rent after the stay")

    def recover(app, why):
        """ゲームの処理が途中で落ちた後、操作を戻す。

        待機表示はゲームが `is_button_enabled=False` で止めているだけなので
        （GAME.md §2.4）、戻して塗り直せば選択肢が押せる状態に戻る。
        ここもワーカースレッドの中なので、画面を触る手はメインスレッドへ回す。
        """
        def give_back():
            try:
                screen.busy_off(app)
            except Exception:
                ctx.log_exc("real estate: cannot clear the waiting display")

        screen.schedule(give_back, 0)
        screen.say(app, "落ち着かない。今日は出直したほうがよさそうだ。")
        write("{}: gave the controls back".format(why))

    @ctx.wrap("__main__:VacationRestManager.execute", required=False)
    def vacation_rest(orig, self, choice_text="", *args, **kwargs):
        """休養。自分の家ではこれで滞在を締める。"""
        result = orig(self, choice_text, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        if app is not None:
            close_stay_after_activity(app, "rest")
        return result

    @ctx.wrap("__main__:VacationTrainManager.execute", required=False)
    def vacation_train(orig, self, choice_text="", *args, **kwargs):
        """訓練。同上。"""
        result = orig(self, choice_text, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        if app is not None:
            close_stay_after_activity(app, "train")
        return result

    @ctx.wrap("__main__:VacationLaborManager.execute", required=False)
    def vacation_labor(orig, self, choice_text="", *args, **kwargs):
        """労働。同上。"""
        result = orig(self, choice_text, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        if app is not None:
            close_stay_after_activity(app, "labor")
        return result

    @ctx.wrap("__main__:VacationBeggingManager.execute", required=False)
    def vacation_begging(orig, self, choice_text="", *args, **kwargs):
        """物乞い。同上。"""
        result = orig(self, choice_text, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        if app is not None:
            close_stay_after_activity(app, "begging")
        return result

    @ctx.wrap("__main__:VacationSocializeResolveManager.execute", required=False)
    def vacation_socialize(orig, self, choice_text="", *args, **kwargs):
        """社交の後段。前段（相手を選ぶ側）では締めない。"""
        result = orig(self, choice_text, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        if app is not None:
            close_stay_after_activity(app, "socialize")
        return result

    @ctx.wrap("__main__:VacationEndManager.execute", required=False)
    def vacation_end(orig, self, choice_text="", *args, **kwargs):
        """滞在が終わったら、据えた主を戻して滞在の印を落とす。"""
        app = getattr(self, "app", None) or ui.find_app()
        state["ending"] = False
        try:
            if app is not None:
                end_stay(app, "the game ended the stay")
        except Exception:
            ctx.log_exc("real estate: cannot finish the stay")
            state["free_stay"] = None
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            # 建物の選択肢が組み直された後で解く（先に解くと活動の後の選択肢が見える）。
            if app is not None:
                uncover(app, "the stay ended")

    @ctx.wrap("scripts.hud.new_hud:InventoryItem.change_inventory", required=False)
    def change_inventory(orig, self, new_inventory, *args, **kwargs):
        """保管庫の窓の中の移動だけを見る。店の売買と受け渡しは素通し。"""
        holder = storage_holder()
        if holder is None:
            return orig(self, new_inventory, *args, **kwargs)
        old_owner = _owner_of_grid(getattr(self, "inventory", None))
        result = orig(self, new_inventory, *args, **kwargs)
        try:
            new_owner = _owner_of_grid(new_inventory)
            app = ui.find_app()
            pair = (old_owner, new_owner)
            if app is not None and old_owner is not new_owner \
                    and holder in pair and getattr(app, "player", None) in pair:
                sync_storage(app, self, old_owner, new_owner)
        except Exception:
            ctx.log_exc("real estate: cannot sync the storage")
        return result

    def _owner_of_grid(grid):
        """`InventoryGrid` の持ち主。読めなければ None。"""
        return getattr(grid, "obtainer", None) if grid is not None else None

    @ctx.wrap("__main__:InstantaleApp.close_shopping_window_process", required=False,
              safe=True)
    def close_shopping_window(orig, self, *args, **kwargs):
        """窓が閉じたら控えを書いて持ち主を捨てる。"""
        try:
            close_storage(self, "window closed")
        except Exception:
            ctx.log_exc("real estate: cannot close the storage")
        return orig(self, *args, **kwargs)

    ctx.log("real estate: ready (rent {} per {} stay(s), buy {}, "
            "auto_renew={})".format(RENT_PRICE, RENT_STAYS, PURCHASE_PRICE,
                                    AUTO_RENEW))
