# -*- coding: utf-8 -*-
r"""機能追加: 役場で物件を借りる・買う。その土地に自分の建物が建つ。

素のゲームでプレイヤーが腰を落ち着けられる場所は宿屋だけで、
泊まるたびに宿代と30日を払い直す。荷物を置く場所も無い。
この MOD は役場（`administrative_office`）の選択肢に不動産の窓口を1つ足す。

    [役場]  労働の募集をみる / 市民権の発行 / 物件を扱う / 出る
                                    ↓
            借りる(1,600G・120日) / 建売を買い取る(30,000G) / やめる
                                    ↓
            街の入口に建物が1軒増える

    [入口]  中央居住区 / 外縁スラム / 借りている家 ←ここ / 街を出る
                                    ↓
    [家]    滞在する / 保管庫をあける / 家から出る

繋ぐ先はその土地の入口（`Node.entrance_facility`）。
街に着いて最初に立つ場所で、区画（`ward`）はその先にある。

## ゲームはこの施設を知らない（実機 2026-09-11）

**ゲームが移動の一覧を組むとき、実行時の `Facility.connections` は読まれない。**
足した家は素データ（`world_dict` / `save_data_dict`）に無いので、
繋ぎ先の画面にも、建物の中にも、ゲームは何も並べなかった
（入ること自体は `MovePhaseManager` でできる。例外も出ない）。
GAME.md §2.28 の「遊んでいる最中に生まれた施設で売買を選ぶと `KeyError`」と同じ側面で、
ゲームは施設を素データから引き直している。

だから**道も、中の選択肢も、出口もこの MOD が出す**。
素データに書けばゲーム自身が扱うようになるが、それはセーブに残る（TECH.md §3.11）。

建物の中でできるのは3つ。

* **滞在**   宿屋の宿泊と同じ経路（`VacationStartManager`）。
             休養・訓練・労働・物乞いの活動がそのまま選べて、**宿代は取られない**。
             社交（他者と交流）とアイテム作成は出さない（`HIDDEN_ACTIVITY_CLASSES`）
* **保管庫** 店の売買と同じ2枚並びの窓。預けた品はプレイヤーの持ち物から外れる

## 建物はセーブに焼かない

足すのは実行中の `Area` / `Node` / `Facility` だけで、
`world_data.json` にも `savedata.json` にも施設は書かない（TECH.md §3.11）。
契約は `state\real_estate\<世界名>.json` に持ち、ロードのたびに当て直す
（`325_road_opening` の道と同じ形）。**MOD を外せば街は素に戻る**。

組み立ての中身は `estate.py`。

## 保管庫の中身も控えに持つ

預けた品の置き場所として NPC を1人作れば、セーブの正規の形で残せる。
それはやらない（人が1人増え、契約が切れたときに消す責任がこちらに移る）。
代わりに、預けた品をセーブと同じ形の辞書にして控えへ落とし、
窓を開くたびに**その窓の間だけ生きる持ち主**へ作り直す。中身は `storage.py`。

預けた瞬間にプレイヤーの持ち物から品が消えるので、**その場で `save_game` を呼ぶ**。
呼ばずに落ちると、控えにも持ち物にも同じ品が居る状態でセーブが残る（品が増える）。

## 家賃と期限

賃貸の1期は**宿泊 `RENT_STAYS` 回ぶん**（`lease_days(app)`。既定4回）。
1回ぶんにすると宿に泊まり直すのと変わらないので、まとめて借りる形にしてある。

宿泊1回の長さは**ゲームの宿屋と同じ**（自分の家の滞在も同じ長さ）。
素のゲームは3ヵ月から年齢で伸びる変動式で、
`315_vacation_custom` を入れていればその設定が効く。
どちらも自分では決めず、**宿屋で実際に使われた月数を覚えて使う**
（`VacationStartManager.__init__` の `months`。
そこが `elapse_days(months * 30)` の元。218 の実測）。
まだ宿屋を見ていない世界では年齢から見積もる（`estimated_stay_months`）。

契約の周期は**結んだ時点の長さで固定**する。
年を取って宿泊が伸びても、いま借りている契約の期限は動かない。
日付が進むのは `elapse_days` の1箇所だけなので（GAME.md §2.16）、そこを包んで期限を見る。

    払えた     期限を `term` 日延ばす。移動や宿泊で何期ぶんか飛んだときはまとめて払う
    払えない   契約が切れる。建物は取り壊し、保管庫の中身は役場が預かる

預かった品は、どこの役場でも引き取り料を払えば戻る。
建売（`owned`）には期限も家賃も無い。

**プレイヤーが建物の中に居るあいだは取り壊さない**（出口の無い施設に立たせないため）。
その場合は次に外へ出たときに壊す。

## 自前のクラス名を `PhaseSpec` に書かない

ボタンはセーブに焼かれうる（GAME.md §2.2）。無害な既存クラスを持たせ、
押下は `on_button_press` を包んで印（`mod_real_estate`）で横取りする。
"""

import datetime
import sys

from instantale_modloader import frames, ui
from instantale_modloader.ids import claim
from instantale_modloader.state import (UNKNOWN_WORLD, WorldStore, world_key,
                                        world_key_of_dict)

from . import estate, storage


LOG_BASENAME = "real_estate.log"

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

#: アイテム作成の入口。
CRAFT_CLS = "ItemCraftManager"

#: 自分の家の滞在では出さない活動（DOC.md §3.2）。
#:
#: 社交   … 誰と会うかはゲームが決め、自分の家では大家（役場の役人）が相手になる
#: 作成   … 自分の家ではまだ成り立っていない
#:
#: 宿屋の側には触らない（落とすのは自分の建物での滞在の最中だけ）。
HIDDEN_ACTIVITY_CLASSES = (SOCIALIZE_CLS, CRAFT_CLS)

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む）。
# `estate.py` / `storage.py` へ移さないこと（TECH.md §3.8）。

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

#: 自分の家に居ないとき（宿屋を一度も見ていない世界）の、滞在の月数の見積もり。
#: 素のゲームは3ヵ月から年齢で伸びる。宿屋を1度でも使えば実際の値を覚える。
BASE_STAY_MONTHS = 3

#: 借りた物件の名前。
RENT_NAME = "借りている家"

#: 買った物件の名前。
OWNED_NAME = "自分の家"

# ---------------------------------------------------------------- 文言
#: 役場に足す選択肢。
OFFICE_LABEL = "物件を扱う"
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
RENEWED_TEXT = "{name}の家賃 {price}G を納めた。"
NOTICE_TEXT = "{name}の契約はあと{days}日で切れる。"
LAPSED_TEXT = "家賃を払えず、{area}の{name}を引き払うことになった。"
SEIZED_TEXT = "保管庫にあった{count}点は役場が預かっている。"
RELEASED_TEXT = "{area}の{name}を引き払った。"
RECLAIMED_TEXT = "役場から{count}点を引き取った。"

#: ゲームの1ヵ月（`elapse_days(months * 30)`。GAME.md §2.17 の実測）。
DAYS_PER_MONTH = 30

#: 週ぎめ・月ぎめだった頃の契約。次に読んだときに1本立ての賃貸へ移す。
LEGACY_LEASE_KINDS = ("rent_week", "rent_month")


def age_of(app):
    """プレイヤーの年齢。読めなければ None。"""
    value = getattr(getattr(app, "player", None), "age", None)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def estimated_stay_months(app):
    """宿屋の宿泊期間の見積もり（素のゲームの式）。

    3ヵ月を土台に、30代 +1・40代 +2・50代以上 +3、上限6ヵ月
    （`315_vacation_custom` が同じ式を持っている。
    GAME.md §2.17 の実測は 20代=3・31歳=4 の2点）。
    **見積もりを使うのは宿屋を一度も見ていない間だけ**で、
    1度でも泊まれば実際の値に置き換わる。
    """
    months = int(BASE_STAY_MONTHS or 3)
    age = age_of(app)
    if age is not None:
        if age >= 50:
            months += 3
        elif age >= 40:
            months += 2
        elif age >= 30:
            months += 1
    return max(1, min(months, 6))


#: 契約の家賃の周期（日）。買い切りは 0、賃貸は `lease_days(app)`。
#: 周期は**契約した時点の長さで固定**する（途中で伸び縮みさせない）。


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
    return record.get("kind") in (("rent",) + LEGACY_LEASE_KINDS)


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
                # 保存の世代（連続で動かしたときは最後の1回だけ保存する）。
                "save_generation": 0,
                # 取り壊しを待っている契約（プレイヤーが中に居た）。
                "pending_demolish": [],
                # 自前の画面を出す前の選択肢（`やめる` で戻すために控える）。
                "saved": None,
                # 一度書いた WARN の覚え。選択肢が組まれるたびに当て直すので、
                # 同じ理由をそのたび書くとログが選択肢の回数だけ伸びる。
                "warned": set(),
                # 最後に書いた居場所（変わったときだけ1行書くため）。
                "where": None,
                # こちらが起こした「建物へ入る」移動の控え。
                # ゲームが居場所を書き換えなかったときの拠り所（`inside_home`）。
                "entered": None,
                # 滞在の `execute` の中でこちらが引いた家賃。
                # 宿代を返すとき、これは返さない（同じ `execute` で暦が進むため）。
                "rent_charged": 0,
                # 滞在のあいだ主を据える前の値（戻すために控える）。
                "owner_was": None,
                # 最後に描いた背景（同じ絵を二度描かないため）。
                "background": None,
                # 背景の描き直しを予約した（まだ走っていない）。
                "background_pending": False,
                # 手が空くのを待っているボタンの足し直し（見張りは同時に1つ）。
                "retry": False,
                # いま組んでいる宿泊は自分の家のものか（宿屋の月数と取り違えないため）。
                "own_stay": False,
            },
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]

    write = ctx.logger(LOG_BASENAME)
    worlds = store["worlds"].rebind(ctx, write)
    screen = ui.Screen(ctx, write, tag="real estate", mark=MARK)

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
        for record in contracts_of(world_key(app)):
            if str(record.get("area")) == str(area_id):
                return record
        return None

    def contract_here(app):
        return contract_in(app, ui.area_id_of(ui.current_area(app)))

    def seized_of(app):
        """役場が預かっている品。`{鍵: 辞書}`。"""
        items = bucket_of(world_key(app)).get("seized")
        return items if isinstance(items, dict) else {}

    def storage_of(app, record):
        """その建物から見える保管庫の中身 `{鍵: 辞書}`。

        置き場所は設定で変わる。
        世界で1つ（`STORAGE_SHARED`）なら控えの根に、
        建物ごとならその契約の中に持つ。
        """
        if STORAGE_SHARED:
            bucket = bucket_of(world_key(app))
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
            bucket_of(world_key(app))["shared"] = items
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
        bucket = bucket_of(world_key(app))
        shared = bucket.get("shared")
        if not isinstance(shared, dict):
            shared = bucket["shared"] = {}
        moved = 0
        for record in contracts_of(world_key(app)):
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
        worlds.save(world_key(app))

    def our_facility_ids(key):
        return set(str(record.get("facility")) for record in contracts_of(key)
                   if record.get("facility"))

    def move_choices(area, facility_id, skip=()):
        """その施設に立ったときにゲームが並べる選択肢（繋ぎ先への移動）を組む。

        **ロードは選択肢を組み直さない**。`game_variables["buttons"]` に
        焼かれているものをそのまま戻すだけ（GAME.md §2.3）。
        だから立ち位置だけ直しても、選択肢が空なら動けないままになる
        （実機 2026-09-11。入口へ戻したのに1つも出なかった）。

        返すのは**セーブと同じ形**の辞書。
        文字列は繋ぎ先の名前で、実機の入口の選択肢と同じ並びになる。

        `skip` には自分が建てた建物の id を渡す。
        **セーブに無い施設への道をボタンにしてはいけない**
        （そのボタンはセーブに焼かれ、MOD の無い環境や、
        建物を失った後に押せてしまう）。
        ゲーム自身もこの建物を一覧に出さないので、出さないほうが実機と同じ。
        """
        facility, node = ui.find_facility(area, facility_id)
        if facility is None or node is None:
            return []
        node_id = estate.node_id_of(node)
        area_id = ui.area_id_of(area)
        entries = []
        for target in estate.connections_of(facility):
            if str(target) in skip:
                continue
            other, _node = ui.find_facility(area, target)
            if other is None:
                continue
            entries.append({"text": getattr(other, "name", "") or str(target),
                            "spec": {"cls_name": MOVE_CLS,
                                     "args": [node_id, str(target), area_id]}})
        return entries

    def live_move_buttons(area, facility_id, skip=()):
        """`move_choices` と同じ並びを、走っているゲームのボタンの形で組む。"""
        entries = []
        for entry in move_choices(area, facility_id, skip):
            spec = screen.make_spec(entry["spec"]["cls_name"], entry["spec"]["args"])
            if spec is None:
                return []
            entries.append({"text": entry["text"], "spec": spec})
        return entries

    def repair_player_location(world, save_data_dict, skip=()):
        """セーブの立ち位置が街に無い施設を指していたら、その土地の入口へ直す。

        ゲームのロードは、引けなかった施設に `'facilityが見つからない'` という
        **文字列**を入れてから `.name` を読むので、そこで必ず落ちる
        （実機 2026-09-11。`instantale.py:1493`。
        直すまで、その世界はもう開けない）。
        取り壊した建物に立ったまま保存されると、そうなる。

        建物を建て直した後に呼ぶので、契約が生きている家の中に居るぶんには何もしない。
        ここで直すのは**読み込みの途中の辞書**で、セーブの書き換えではない
        （次にゲームが保存したときに、直った位置で書かれる）。
        """
        player = save_data_dict.get("player_data") \
            if isinstance(save_data_dict, dict) else None
        if not isinstance(player, dict):
            return
        here = str(player.get("location") or "")
        if not here:
            return
        area = ui.areas_of_world(world).get(str(player.get("current_area") or ""))
        if area is None:
            return
        facility, _node = ui.find_facility(area, here)
        if facility is not None:
            return
        _node, hub = estate.hub_of(area)
        hub_id = estate.facility_id_of(hub) if hub is not None else ""
        if not hub_id:
            write("WARN load: the player stands in {!r}, which is not in the town, "
                  "and this area has no entrance".format(here))
            return
        player["location"] = hub_id
        variables = save_data_dict.get("game_variables")
        choices = move_choices(area, hub_id, skip)
        if isinstance(variables, dict) and choices:
            variables["buttons"] = choices
        write("load: the player was standing in {!r}, which is not in the town; "
              "moved to the entrance {} with {} choice(s)".format(
                  here, hub_id, len(choices)))

    def safe_save_location(app):
        """保存のあいだだけ立ち位置を入口へ替える。替えたなら `(player, 元の値)`。

        セーブに残るのは施設の id だけなので、
        **ロードで建て直されない建物の id が残ると、次のロードでゲームが落ちる**
        （§3.2 の14回目）。
        契約が生きているうちはロードで建て直すので、家の中のまま保存してよい
        （そのほうが、入ったところから続けられる）。
        """
        player = getattr(app, "player", None)
        if player is None:
            return None
        record = contract_here(app)
        here = record is not None and standing_in(app, record)
        doomed = here and (record.get("lapsed")
                           or record in state["pending_demolish"])
        if not doomed and (here or not stranded(app)):
            return None
        area = ui.current_area(app)
        _node, hub = estate.hub_of(area) if area is not None else (None, None)
        if hub is None:
            return None
        hub_id = estate.facility_id_of(hub)
        was = getattr(player, "location", None)
        try:
            player.location = hub
        except Exception:
            ctx.log_exc("real estate: cannot move the player for the save")
            return None
        # 立ち位置だけ替えても、焼かれる選択肢が前の場所のままでは動けない。
        buttons = getattr(app, "buttons", None)
        choices = live_move_buttons(area, hub_id, our_facility_ids(world_key(app)))
        if choices:
            app.buttons = choices
        else:
            buttons = None
        write("save: the player was inside {!r}, which will not come back; "
              "saving at the entrance {} with {} choice(s)".format(
                  (record or {}).get("name") or "a building that is gone",
                  hub_id, len(choices)))
        return (player, was, buttons)

    # ------------------------------------------------------------ 建物の当て直し
    def remember_stay_months(app, months):
        """宿屋で実際に使われた月数を控える。

        見ているのは `VacationStartManager.__init__` に渡る `months`。
        **そこが `elapse_days(months * 30)` の元**（218 の実測）で、
        年齢の変動式も `315_vacation_custom` の設定も、
        この時点では答えが出ている。
        """
        try:
            value = int(str(months))
        except (TypeError, ValueError):
            return
        if value < 1:
            return
        key = world_key(app)
        bucket = bucket_of(key)
        if bucket.get("stay_months") == value:
            return
        bucket["stay_months"] = value
        worlds.save(key)
        write("stay length: the inn used {} month(s) = {} days".format(
            value, value * DAYS_PER_MONTH))

    def stay_months(app):
        """1回の滞在の月数。**宿屋と同じ**（本人の指定、2026-09-11）。

        素のゲームは3ヵ月から年齢で伸び、`315_vacation_custom` を入れていれば
        その設定が効く。どちらも自分では決めず、
        宿屋で実際に使われた値を覚えて使う。覚えが無いうちだけ見積もる。
        """
        value = bucket_of(world_key(app)).get("stay_months")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
        return estimated_stay_months(app)

    def stay_days(app):
        """滞在1回で進む日数。"""
        return stay_months(app) * DAYS_PER_MONTH

    def lease_days(app):
        """賃貸の1期の長さ（日）。**滞在 `RENT_STAYS` 回ぶん**。

        宿と同じ長さ（1回ぶん）では、家を借りても泊まり直すのと変わらない。
        既定は4回ぶんで、その間は何度でも戻って過ごせる（本人の指定）。
        """
        return max(1, int(RENT_STAYS or 1)) * stay_days(app)

    def migrate_leases(key, app=None):
        """週ぎめ・月ぎめで結んだ契約を、1本立ての賃貸へ移す（版16）。

        **期限（`due`）はそのまま**にする。
        いま借りている残り日数を取り上げる理由が無いので、
        次に家賃を払うときから新しい周期（滞在1回ぶん）になる。
        """
        if app is None:
            # 周期はいまの宿泊の長さから決める（`app` が要る）。
            # ロードの途中など、まだ渡せないときは次の呼び出しに任せる。
            return 0
        moved = []
        for record in contracts_of(key):
            if record.get("kind") not in LEGACY_LEASE_KINDS:
                continue
            record["kind"] = "rent"
            record["term"] = lease_days(app)
            record["rent"] = int(RENT_PRICE)
            moved.append(record.get("name"))
        if moved:
            worlds.save(key)
            write("migrated {} lease(s) to the single rent ({} days, {}G): {}".format(
                len(moved), lease_days(app), int(RENT_PRICE), moved))
        return len(moved)

    def apply_contracts(app, world, key, why):
        """控えの契約をこの世界へ当てる。立てた棟数を返す。

        ロードの直後と、選択肢が組まれるたびに呼ばれる。
        既に立っているものは何もしない（何度呼んでも増えない）。
        """
        migrate_leases(key, app)
        areas = ui.areas_of_world(world)
        if not areas:
            return 0
        built = 0
        for record in contracts_of(key):
            if record.get("lapsed"):
                continue
            area = areas.get(str(record.get("area")))
            if area is None:
                warn_once(("area", key, str(record.get("area"))),
                          "WARN {}: area {!r} is not in this world".format(
                              why, record.get("area")))
                continue
            facility_id = str(record.get("facility") or "")
            if not facility_id:
                continue
            facility, _node = estate.existing(area, facility_id)
            if facility is not None:
                # 既に建っている。繋ぎ先の選び方が変わっていれば繋ぎ直す
                # （`ward` から入口へ移した版のため。それ以外では何も起きない）。
                node, hub = estate.rehome(app, area, facility_id, record.get("hub"),
                                          write=write)
                if hub is not None and \
                        estate.facility_id_of(hub) != str(record.get("hub") or ""):
                    record["node"] = estate.node_id_of(node)
                    record["hub"] = estate.facility_id_of(hub)
                    worlds.save(key)
                continue
            node, hub = estate.hub_of(area)
            if node is None or hub is None:
                warn_once(("hub", key, str(record.get("area"))),
                          "WARN {}: no hub facility in area {!r}; {!r} is not standing"
                          .format(why, record.get("area"), record.get("name")))
                continue
            try:
                facility = estate.build(app, area, facility_id, record.get("name") or "",
                                        _description_of(record.get("kind")), node, hub,
                                        write=write)
            except Exception:
                ctx.log_exc("real estate: cannot build {!r}".format(record.get("name")))
                continue
            if facility is None:
                continue
            record["node"] = estate.node_id_of(node)
            record["hub"] = estate.facility_id_of(hub)
            state["warned"].discard(("hub", key, str(record.get("area"))))
            state["warned"].discard(("area", key, str(record.get("area"))))
            built += 1
        if built:
            worlds.save(key)
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
        node, hub = estate.hub_of(area)
        if node is None or hub is None:
            write("WARN sign: no hub facility in area {!r}".format(area_id))
            screen.say(app, "この土地には建てられる場所が無いようだ。")
            return False
        facility_id = claim(app, "facility", write=write)
        if not facility_id:
            write("WARN sign: could not claim a facility id")
            return False
        name = _name_of(kind)
        try:
            facility = estate.build(app, area, facility_id, name, _description_of(kind),
                                    node, hub, write=write)
        except Exception:
            ctx.log_exc("real estate: Facility(...) failed while signing")
            return False
        if facility is None:
            return False
        day = ui.game_day(app)
        term = 0 if kind == "owned" else lease_days(app)
        record = {
            "area": area_id,
            "area_name": frames.short(getattr(area, "name", ""), 40) or area_id,
            "node": estate.node_id_of(node),
            "hub": estate.facility_id_of(hub),
            "facility": str(facility_id),
            "kind": kind,
            "name": name,
            "rent": price if term else 0,
            "term": term,
            "since": day,
            "due": (day + term) if (term and day is not None) else None,
            "storage": {},
            "notified": None,
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        bucket_of(world_key(app))["contracts"].append(record)
        save(app)
        ui.add_gold(app, -price, on_error=lambda: write("WARN sign: cannot charge"))
        write("signed: {} {!r} id={} area={!r} price={} due={}".format(
            kind, name, facility_id, area_id, price, record["due"]))
        screen.say(app, ui.rewrite_coins(_fmt(SIGNED_TEXT, area=record["area_name"],
                                              name=name)))
        return True

    def drop_contract(app, record):
        """控えから契約を落とす。建物は呼ぶ側が先に片付けること。"""
        bucket = bucket_of(world_key(app))
        bucket["contracts"] = [c for c in bucket.get("contracts") or []
                               if c is not record]
        save(app)

    def take_down(app, record, why):
        """建物を取り壊す。中にプレイヤーが居るときは後回しにして False を返す。"""
        areas = ui.world_areas(app)
        area = areas.get(str(record.get("area")))
        facility_id = str(record.get("facility") or "")
        if area is None or not facility_id:
            return True
        if standing_in(app, record):
            if record not in state["pending_demolish"]:
                state["pending_demolish"].append(record)
            write("{}: the player is inside {!r}; the demolition waits".format(
                why, record.get("name")))
            return False
        estate.demolish(app, area, facility_id, record.get("node"), record.get("hub"),
                        write=write)
        return True

    def seize(app, record):
        """保管庫の中身を役場の預かりへ移す。移した点数を返す。

        世界で1つの保管庫のときは、**他に物件が残っているあいだは取り上げない**
        （別の家から同じ中身を開けるので、取り上げる理由が無い）。
        最後の1軒を失ったときだけ、開く手段が無くなるので役場へ移す。
        """
        if STORAGE_SHARED:
            others = [c for c in contracts_of(world_key(app))
                      if c is not record and not c.get("lapsed")]
            if others:
                write("seize: the shared storage stays ({} contract(s) left)".format(
                    len(others)))
                return 0
        items = storage_of(app, record)
        if not items:
            return 0
        bucket = bucket_of(world_key(app))
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
        """期限の来た契約を精算する。日付が進んだときと画面が組まれたときに呼ぶ。"""
        day = ui.game_day(app)
        if day is None:
            return
        for record in list(contracts_of(world_key(app))):
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
                ui.add_gold(app, -rent,
                            on_error=lambda: write("WARN rent: cannot charge"))
                # 滞在の `execute` の中から呼ばれることがある。
                # そのときは宿代と一緒に引かれるので、返す額から除くために数えておく。
                state["rent_charged"] = int(state.get("rent_charged") or 0) + rent
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
        """役場の不動産の窓口。"""
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
            screen.say(app, "{}で扱える物件は2件。".format(area_name))
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
        ui.add_gold(app, -fee, on_error=lambda: write("WARN reclaim: cannot charge"))
        moved = 0
        for key, data in sorted(seized.items()):
            target = storage.free_key(inv, key)
            try:
                item = app.generate_item_from_dict(dict(data), str(target), player)
            except Exception:
                ctx.log_exc("real estate: cannot rebuild a seized item")
                continue
            if item is not None and inv.get(str(target)) is not item:
                inv[str(target)] = item
            moved += 1
        bucket_of(world_key(app))["seized"] = {}
        save(app)
        write("reclaimed: {} item(s) for {}".format(moved, fee))
        screen.say(app, ui.rewrite_coins(_fmt(RECLAIMED_TEXT, count=moved)))
        save_soon(app, "reclaim")
        back(app, "reclaimed")

    # ------------------------------------------------------------ 滞在
    def hold_owner(app, record):
        """滞在のあいだだけ建物に主を据える。据えた id を返す（据えなければ None）。

        ゲームの宿泊は主を名簿から引くので、主のいない施設では落ちる
        （`KeyError: None`。実機 2026-09-11）。
        自分の家の主は自分なので、名簿に居るプレイヤーを据えるのが本筋。
        滞在が終わったら元へ戻す（`release_owner`）。
        """
        area = ui.current_area(app)
        facility, _node = estate.existing(area, record.get("facility"))
        if facility is None:
            return None
        owner = estate.owner_candidate(app, area, facility, write=write)
        if owner is None:
            return None
        state["owner_was"] = getattr(facility, "owner", None)
        try:
            facility.owner = owner
        except Exception:
            ctx.log_exc("real estate: cannot set the owner of the building")
            return None
        write("stay: the owner of {!r} is {!r} for this stay".format(
            record.get("name"), owner))
        return owner

    def release_owner(app):
        """滞在のあいだ据えた主を元へ戻す。"""
        home = state.get("free_stay")
        if not isinstance(home, dict) or not home.get("owner"):
            return
        area = ui.world_areas(app).get(str(home.get("area")))
        facility, _node = estate.existing(area, home.get("facility"))
        if facility is not None:
            try:
                facility.owner = state.get("owner_was")
            except Exception:
                ctx.log_exc("real estate: cannot restore the owner")
        state["owner_was"] = None
        home["owner"] = None

    def start_stay(app):
        """宿屋の宿泊と同じ経路を、宿代を取らずに起こす。

        `VacationStartManager(app, months, quality)` は実測した署名
        （GAME.md §2.17）。日数・体力・活動の選択肢はゲームが持っているので、
        こちらが足すのは「主を据えること」と「宿代を返すこと」の2つ。
        """
        record = contract_here(app)
        if record is None:
            write("stay: no contract here")
            return
        cls = getattr(estate.main_module(), STAY_CLS, None)
        if cls is None:
            write("WARN stay: __main__.{} is not available".format(STAY_CLS))
            return
        try:
            state["own_stay"] = True
            try:
                phase = cls(app, int(stay_months(app)), str(STAY_QUALITY))
            finally:
                state["own_stay"] = False
        except Exception:
            ctx.log_exc("real estate: cannot build {}".format(STAY_CLS))
            return
        state["free_stay"] = {"facility": str(record.get("facility")),
                              "area": str(record.get("area")),
                              "name": record.get("name")}
        state["free_stay"]["owner"] = hold_owner(app, record)
        write("stay: starting {} months={} quality={!r} at {!r}".format(
            STAY_CLS, stay_months(app), STAY_QUALITY, record.get("name")))
        screen.start_phase(app, phase, STAY_LABEL)

    def enter_home(app):
        """自前の道のボタンが押されたとき。ゲームの移動をその場で組んで起こす。

        `MovePhaseManager(app, connected_node_id, facility_move_to_id, area_id)` は
        実測した「出る」のボタンと同じ引数（GAME.md §2.2）。
        施設を引き当ててから組むので、建物が無ければ何も起こさない。
        """
        record = contract_here(app)
        if record is None:
            return
        args = estate.move_spec_args(ui.current_area(app), record.get("facility"))
        cls = getattr(estate.main_module(), MOVE_CLS, None)
        if args is None or cls is None:
            write("WARN enter: cannot reach {!r}".format(record.get("name")))
            return
        try:
            phase = cls(app, *args)
        except Exception:
            ctx.log_exc("real estate: cannot build {}".format(MOVE_CLS))
            return
        write("enter: {!r} via {}".format(record.get("name"), args))
        state["entered"] = {"facility": str(record.get("facility") or ""),
                            "area": str(record.get("area") or "")}
        screen.start_phase(app, phase, record.get("name") or "家")

    def leave_home(app):
        """建物から出る。繋ぎ先（入口）へゲームの移動で戻す。

        ゲームは自分で足した施設の出口を作らないので、帰り道もこちらで起こす。
        """
        record = contract_here(app)
        area = ui.current_area(app)
        if area is None:
            write("WARN leave: no area here")
            return
        hub_id = str((record or {}).get("hub") or "")
        args = estate.move_spec_args(area, hub_id) if hub_id else None
        if args is None:
            # 控えの広場が引けない（契約ごと消えた後など）。その土地の入口へ出す。
            _node, hub = estate.hub_of(area)
            if hub is not None:
                hub_id = estate.facility_id_of(hub)
                args = estate.move_spec_args(area, hub_id)
        cls = getattr(estate.main_module(), MOVE_CLS, None)
        if args is None or cls is None:
            write("WARN leave: cannot reach the hub {!r} of {!r}".format(
                hub_id, record.get("name")))
            return
        try:
            phase = cls(app, *args)
        except Exception:
            ctx.log_exc("real estate: cannot build {} to leave".format(MOVE_CLS))
            return
        write("leave: {!r} -> hub {} via {}".format(
            (record or {}).get("name") or "(no contract)", hub_id, args))
        end_stay(app, "left the building")
        state["entered"] = None
        screen.start_phase(app, phase, LEAVE_LABEL)

    def inside_home(app, record):
        """いま**自分の家**の中に立っているか（切れた契約の建物は自分の家ではない）。

        滞在も保管庫も、契約が生きているあいだのものなので、ここで切れた分を落とす。
        取り壊してよいかの判定にこれを使ってはいけない
        ― `lapse` は先に「切れた」印を立てるので、
        **中に居るのに居ないと読めて、そのまま壊す**（実機 2026-09-11）。
        そちらは `standing_in` を見る。
        """
        if record is None or record.get("lapsed"):
            return False
        return standing_in(app, record)

    def standing_in(app, record):
        """いまその契約の建物に立っているか（契約が切れていても見る）。

        本筋は `player.location`。
        ただしゲームは自分で足した施設をよく知らない（一覧にも出さない）ので、
        移動の後に居場所が書き換わらない可能性がある。
        そのときは**こちらが起こした移動の控え**を使う。
        居場所が読めて、しかもよその施設だったときは、その控えを落とす。
        """
        if record is None:
            return False
        facility_id = str(record.get("facility") or "")
        if not facility_id:
            return False
        if estate.player_is_inside(app, facility_id):
            return True
        entered = state.get("entered")
        if not isinstance(entered, dict) or entered.get("facility") != facility_id:
            return False
        location = getattr(getattr(app, "player", None), "location", None)
        here = location if isinstance(location, (str, int)) \
            else estate.facility_id_of(location)
        if str(here or "") and str(here) != facility_id:
            state["entered"] = None
            return False
        return True

    def note_place(app, buttons, record, inside, standing):
        """立っている場所が変わったら1行だけ書く（変わらないあいだは黙る）。

        「建物に入ったのに選択肢が出ない」が起きたとき、
        どちらが欠けていたのか（居場所の読み取りか、ゲームの選択肢か）を
        後から読めるようにするための行。
        """
        location = getattr(getattr(app, "player", None), "location", None)
        if isinstance(location, (str, int)):
            facility_id, kind = str(location), "(id only)"
        else:
            facility_id = estate.facility_id_of(location)
            kind = ui.facility_type_of(location) or "?"
        token = (ui.area_id_of(ui.current_area(app)), facility_id, kind, inside,
                 standing, is_facility_screen(buttons))
        if state.get("where") == token:
            return
        state["where"] = token
        if not inside:
            # 自分の家ではなくなった。宿屋での宿泊を無料にしないため、ここで必ず落とす。
            end_stay(app, "left the building")
        if not standing:
            # 建物の外に出た。
            state["entered"] = None
            # 外の背景になった。次に入ったときは描き直す
            # （中に立っているあいだは覚えを持ち続ける。持たないと毎回描き直す）。
            state["background"] = None
        write("where: area={} facility={} type={} inside={} standing={} "
              "game_choices={} ({})".format(
                  token[0], facility_id, kind, inside, standing, len(buttons),
            "home {}".format(record.get("facility")) if record else "no contract"))

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
        cls = getattr(estate.main_module(), END_CLS, None)
        if cls is None:
            write("WARN stay: __main__.{} is not available".format(END_CLS))
            return

        def close():
            if staying_home(app) is None:
                write("stay: {} finished, but the stay is already over".format(which))
                return
            try:
                phase = cls(app)
            except Exception:
                ctx.log_exc("real estate: cannot build {}".format(END_CLS))
                return
            write("stay: {} finished; ending the stay (one activity per stay)".format(
                which))
            screen.start_phase(app, phase, "宿泊を終える")

        screen.when_idle(app, close, proceed_on_timeout=True, tag="one activity")

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
        name = STORAGE_NAME if STORAGE_SHARED else (record.get("name") or "保管庫")
        try:
            holder = storage.make_holder(app, name, write=write)
        except Exception:
            ctx.log_exc("real estate: cannot make the storage holder")
            return
        if holder is None:
            return
        items = storage_of(app, record)
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
            # 窓が半端に開いたまま操作を受け付けなくなる（実機 2026-09-11）。
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

    def walk_widgets(root):
        """Kivy のウィジェット木を深さ優先で辿る。同じものは1度だけ。"""
        if root is None:
            return
        seen = set()
        stack = [root]
        while stack:
            widget = stack.pop()
            ident = id(widget)
            if ident in seen:
                continue
            seen.add(ident)
            yield widget
            children = getattr(widget, "children", None)
            if isinstance(children, (list, tuple)):
                stack.extend(children)

    def rename_right_header(app, name):
        """2枚並びの窓の右側の見出しを保管庫の名前にする。

        文言「所持品」のウィジェットを HUD から探して、最初の1つだけ書き換える。
        """
        hud = ui.find_hud(app)
        if hud is None:
            return
        for widget in walk_widgets(hud):
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

    def save_soon(app, why):
        """少し待ってからゲーム自身の `save_game` を呼ぶ（連続の移動は最後の1回だけ）。

        預けた品はプレイヤーの持ち物から外れるので、保存しないまま落ちると
        控えと持ち物の両方に同じ品が残る（品が増える）。
        """
        state["save_generation"] += 1
        generation = state["save_generation"]

        def do_save():
            if generation != state["save_generation"]:
                return
            try:
                app.save_game()
                write("{}: save_game complete".format(why))
            except Exception:
                ctx.log_exc("real estate: save_game after {} failed".format(why))

        screen.schedule(do_save, 0.15)

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
        state["storage"] = None
        write("storage: closed ({})".format(why))

    # ------------------------------------------------------------ 選択肢の組み立て
    def at_facility_type(app, kind):
        """いま立っている施設の種類。ロード直後は id の文字列なので引き直す。"""
        location = getattr(getattr(app, "player", None), "location", None)
        if isinstance(location, (str, int)):
            location, _node = ui.find_facility(ui.current_area(app), str(location))
        return ui.facility_type_of(location) == kind

    def at_hub(app, record):
        """広場に立っているか（建物への道が並ぶべき画面か）。"""
        hub_id = str(record.get("hub") or "")
        if not hub_id:
            return False
        location = getattr(getattr(app, "player", None), "location", None)
        if isinstance(location, (str, int)):
            return str(location) == hub_id
        return estate.facility_id_of(location) == hub_id

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

    def our_labels(app):
        """残骸の掃除に使う文言（`prune_stale`）。契約中の建物の名前も入れる。

        建物の名前は設定で変えられるので、いま立っている名前も混ぜないと
        「印を失った古い名前のボタン」が画面に残る。
        """
        labels = list(OUR_LABEL_PREFIXES)
        for record in contracts_of(world_key(app)):
            name = record.get("name")
            if isinstance(name, str) and name:
                labels.append(name)
        return labels

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

    def add_home_buttons(app, buttons, record):
        """建物の中の選択肢。**出口もこちらで出す**。

        ゲームは自分で足した施設の `connections` を読まないので、
        中に立っても選択肢が1つも出ない（実機 2026-09-11）。
        出口が無いと建物から出られなくなるため、
        ゲームの移動のボタンが1つも無い画面では「家から出る」を足す。
        """
        at = len(buttons)
        for index, item in enumerate(buttons):
            if ui.spec_cls_name(item) == MOVE_CLS:
                at = index
                break
        added = False
        labels = [(STAY_LABEL, "stay"), (STORAGE_LABEL, "storage")]
        if not is_facility_screen(buttons):
            labels.append((LEAVE_LABEL, "leave"))
        for label, mark in labels:
            entry = screen.button(label, mark=mark)
            if entry is None:
                continue
            buttons.insert(at, entry)
            at += 1
            added = True
        return added

    def add_exit_button(app, buttons, force=False):
        """出口だけ足す。契約が切れた建物の中と、消えた建物に取り残されたとき。

        ふだんはゲームの移動が1つでもあれば足さない（出る道はもうある）。
        取り残されたときだけは `force` で押し通す。
        実機（2026-09-11）でそうなった画面には**選択肢が4つ残っていた**が、
        そこから街へ戻ることはできなかった
        ― 残っていた選択肢が出口かどうかは当てにできない。
        """
        if not force and is_facility_screen(buttons):
            return False
        entry = screen.button(LEAVE_LABEL, mark="leave")
        if entry is None:
            return False
        buttons.insert(max(len(buttons) - 1, 0), entry)
        return True

    def stranded(app):
        """街に無い施設に立っているか（取り残された形）。

        実機（2026-09-11）で、滞在の最中に契約が切れて建物が取り壊され、
        もう街に無い施設に立ったまま選択肢が1つも無くなった。
        壊す側は直した（`standing_in`）が、
        **すでにそうなった遊びからも戻れるように**出口だけは出す。

        誤って出さないよう、立っている施設が街から引けないことと、
        その土地の入口が引けることの両方を見る。
        エリアはプレイヤー自身が持っているもの（`player.current_area`）なので、
        よその土地の入口へ出してしまうことはない。
        """
        area = ui.current_area(app)
        if area is None:
            return False
        location = getattr(getattr(app, "player", None), "location", None)
        here = location if isinstance(location, (str, int)) \
            else estate.facility_id_of(location)
        if not str(here or ""):
            return False
        facility, _node = ui.find_facility(area, str(here))
        if facility is not None:
            return False
        _node, hub = estate.hub_of(area)
        if hub is None:
            return False
        warn_once(("stranded", str(here)),
                  "WARN stranded: facility {!r} is not in the town any more; "
                  "offering the way out".format(here))
        return True

    def add_move_button(app, buttons, record):
        """建物への道がゲームの一覧に無ければ、こちらで足す（`325_` と同じ保険）。

        ボタンの spec は無害な既存クラスにして、押下は印で横取りする。
        `MovePhaseManager` を spec に書くと、そのボタンがセーブへ焼かれた後に
        **MOD を外した環境で押せてしまう**（建物はもう無いので、そこで落ちる）。
        起こすのは押されたときで、そのときは施設が在ることを確かめてから組む。
        """
        facility_id = str(record.get("facility") or "")
        for entry in buttons:
            if ui.spec_cls_name(entry) != MOVE_CLS:
                continue
            args = ui.spec_args(entry)
            if len(args) > 1 and str(args[1]) == facility_id:
                return False
        args = estate.move_spec_args(ui.current_area(app), facility_id)
        if args is None:
            return False
        entry = screen.button(record.get("name") or "家", mark="enter")
        if entry is None:
            return False
        buttons.insert(max(len(buttons) - 1, 0), entry)
        warn_once(("list", str(record.get("area")), facility_id),
                  "WARN hub: the game did not list {!r}; added the move button myself "
                  "(args={})".format(record.get("name"), args))
        return True

    def retry_when_idle(app):
        """流し込みの最中に来た足し直しを、手が空いてからやり直す。

        `maintain_buttons` は本文が流れている間は何もしない（GAME.md §2.6）。
        ところが**自前の画面から戻す塗り直しは、いつも `screen.say` の直後**に走る。
        そこで黙って戻ると、次にゲームが選択肢を組み直すまで出番が来ない
        ― 実機（2026-09-11）では解約の後、区画へ出て戻るまで
        「物件を扱う」が消えたままだった（契約と引き取りの後も同じ）。

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
        """いまの画面に応じて自前のボタンを足す。何度呼んでも増えない。"""
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return
        if ui.busy_signals(app):
            # 本文が流れている最中は触らない。手が空いてからやり直す。
            retry_when_idle(app)
            return
        record = contract_here(app)
        inside = inside_home(app, record)
        # 契約が切れても、取り壊しを待つ建物の中に立っていることはある。
        standing = standing_in(app, record)
        note_place(app, buttons, record, inside, standing)
        if inside:
            ensure_home_background(app, record)
        if state.get("free_stay") is not None:
            # 滞在の最中。並んでいるのはゲームの活動の選択肢（休養・訓練・労働…）で、
            # そこへ「滞在する」を足すと同じ画面から滞在が二重に始まる。
            # 足さない代わりに、自分の家では出さない活動をここで落とす。
            if drop_unwanted_activities(app, buttons):
                screen.apply_buttons(app, None, "stay menu")
            return
        # 自分の建物の中だけは、ゲームが選択肢を1つも作らない
        # （ゲームは実行時の `Facility.connections` を読んでいない。実機 2026-09-11）。
        # そこでは「施設の画面か」を問わず、出口まで含めてこちらが出す。
        # 契約が切れた建物と、消えた建物に取り残されたときは、出口だけを出す。
        lost = not inside and not standing and stranded(app)
        loose = not inside and (standing or lost)
        if not inside and not loose and not is_facility_screen(buttons):
            return
        screen.prune_stale(buttons, our_labels(app))
        if any(screen.mark_of(entry) for entry in buttons):
            return
        touched = False
        if inside:
            touched = add_home_buttons(app, buttons, record)
        elif loose:
            touched = add_exit_button(app, buttons, force=lost)
        elif at_facility_type(app, OFFICE_FACILITY_TYPE):
            touched = add_office_button(app, buttons)
        elif record is not None and not record.get("lapsed") and at_hub(app, record):
            touched = add_move_button(app, buttons, record)
        if touched:
            screen.apply_buttons(app, None, "facility")

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
        elif action == "enter":
            enter_home(app)
        elif action == "leave":
            leave_home(app)
        elif action == "cancel":
            back(app, "cancelled")
        else:
            write("WARN unknown action {!r}".format(action))

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """選択肢が組み直されるたびに、建物を当て直して自前のボタンを足す。"""
        result = orig(self, reset_page, *args, **kwargs)
        try:
            apply_contracts(self, getattr(self, "world", None), world_key(self),
                            "screen")
            check_leases(self, "screen")
            flush_demolitions(self)
            maintain_buttons(self)
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
            key = world_key_of_dict(save_data_dict, None) or world_key(app)
            if key and key != UNKNOWN_WORLD:
                worlds.forget(key)
                state["free_stay"] = None
                state["storage"] = None
                state["pending_demolish"] = []
                state["warned"] = set()
                apply_contracts(app, self, key, "load")
                repair_player_location(self, save_data_dict,
                                       our_facility_ids(key))
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

    @ctx.wrap("__main__:InstantaleApp.save_game", required=False)
    def save_game(orig, self, *args, **kwargs):
        """建て直されない建物の中に居るあいだは、入口に立っていることにして保存する。"""
        swapped = None
        shopping = None
        try:
            swapped = safe_save_location(self)
            shopping = quiet_shopping_flag(self)
        except Exception:
            ctx.log_exc("real estate: cannot check the place before the save")
        try:
            return orig(self, *args, **kwargs)
        finally:
            if shopping is not None:
                try:
                    self.in_shopping = shopping
                except Exception:
                    ctx.log_exc("real estate: cannot restore in_shopping")
            if swapped is not None:
                player, was, buttons = swapped
                try:
                    player.location = was
                    if buttons is not None:
                        self.buttons = buttons
                except Exception:
                    ctx.log_exc("real estate: cannot put the player back")

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False)
    def elapse_days(orig, self, days, *args, **kwargs):
        """日付が進んだら家賃を精算する。日付を動かすのはここ1箇所（GAME.md §2.16）。"""
        result = orig(self, days, *args, **kwargs)
        try:
            check_leases(self, "elapse", idle=True)
        except Exception:
            ctx.log_exc("real estate: cannot settle the rent")
        return result

    @ctx.wrap("__main__:VacationStartManager.__init__", required=False, safe=True)
    def stay_init(orig, self, app=None, months=None, quality=None, *args, **kwargs):
        """宿屋の宿泊が組まれるたび、その月数を控える。

        自分の家の滞在（こちらが組んだもの）は数えない。
        覚えた値をそのまま渡しているので害は無いが、
        見積もりで始めた世界で、その見積もりが「実測」に化けるのを避ける。
        """
        result = orig(self, app, months, quality, *args, **kwargs)
        try:
            if not state.get("own_stay") and app is not None:
                remember_stay_months(app, months)
        except Exception:
            ctx.log_exc("real estate: cannot note the length of the stay")
        return result

    @ctx.wrap("__main__:VacationStartManager.execute", required=False)
    def vacation_start(orig, self, choice_text="", *args, **kwargs):
        """自分の建物での滞在は宿代を取らない。宿屋での宿泊には触らない。

        宿代の引き落としは `execute` の中で1回だけ起きる（GAME.md §2.17）。
        ただし**同じ `execute` の中で暦も進む**ので、そこで家賃も引かれる
        （実機で 1,100 ＝ 宿代 100 + 家賃 1,000 が動いた）。
        差額をそのまま返すと家賃まで返してしまうので、
        こちらが引いた家賃（`rent_charged`）を除いてから返す。

        ゲーム側が途中で落ちたときは、引かれた宿代を返して操作を戻す。
        ここで例外をそのまま通すと、`execute` がワーカースレッドごと終わって
        **画面が「…」のまま戻らない**（実機 2026-09-11）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        home = staying_home(app) if app is not None else None
        if home is None:
            return orig(self, choice_text, *args, **kwargs)
        before = ui.gold_of(app)
        state["rent_charged"] = 0
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
            refund_room(app, before, "stay failed")
            end_stay(app, "the stay failed")
            recover(app, "stay failed")
            return None
        refund_room(app, before, "stay")
        return result

    def describe_building(app, home):
        """建物のいまの姿。落ちたときの手がかりとしてログに添える。"""
        area = ui.world_areas(app).get(str(home.get("area")))
        facility, node = estate.existing(area, home.get("facility"))
        if facility is None:
            return "the building is not standing"
        roster = getattr(getattr(app, "world", None), "characters", None)
        return ("facility={} type={!r} owner={!r} characters={} node={} "
                "roster={}".format(
                    estate.facility_id_of(facility),
                    ui.facility_type_of(facility),
                    getattr(facility, "owner", None),
                    estate.id_list_of(facility, "characters"),
                    estate.node_id_of(node),
                    len(roster) if isinstance(roster, dict) else "?"))

    def refund_room(app, before, why):
        """引かれた宿代を返す。家賃として引いたぶんは返さない。"""
        after = ui.gold_of(app)
        if not isinstance(before, int) or not isinstance(after, int):
            write("WARN {}: cannot read the gold; nothing refunded".format(why))
            return 0
        room = (before - after) - int(state.get("rent_charged") or 0)
        if room <= 0:
            return 0
        ui.add_gold(app, room,
                    on_error=lambda: write("WARN {}: cannot refund".format(why)))
        write("{}: refunded {} (rent {} was not refunded)".format(
            why, room, state.get("rent_charged")))
        return room

    def end_stay(app, why):
        """滞在の後始末。据えた主を戻し、宿代を返す印を落とす。"""
        if state.get("free_stay") is None:
            return
        release_owner(app)
        write("stay: finished at {!r} ({})".format(
            (state["free_stay"] or {}).get("name"), why))
        state["free_stay"] = None

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

    def home_for_background(app, location_id=None):
        """背景を決めようとしている先が自分の建物なら、その契約を返す。

        `location_id` を渡さないときは「いま立っている場所」で見る。
        施設 id は土地の中でしか一意でないので（GAME.md §2.7）、
        今いる土地の契約とだけ突き合わせる。
        """
        record = contract_here(app)
        if record is None or record.get("lapsed"):
            return None
        facility_id = str(record.get("facility") or "")
        if not facility_id:
            return None
        if location_id is not None:
            return record if str(location_id) == facility_id else None
        return record if inside_home(app, record) else None

    def paint_home_background(app, record, why):
        """自分の建物の背景を部屋の絵にする。描けたら True。

        ゲームは施設 id から背景を引くが、自分で足した施設は素データに無いので
        引けず、街の外の景色のまま残る（実機 2026-09-11。ロード直後に出た）。
        代わりに宿屋の部屋の絵を借りる（`change_background_image_to_inn_room`）。
        滞在で使う等級と同じものを渡すので、泊まったときと同じ部屋になる。
        """
        paint = getattr(app, "change_background_image_to_inn_room", None)
        if not callable(paint):
            warn_once(("bg", "missing"),
                      "WARN background: change_background_image_to_inn_room is gone")
            return False
        try:
            paint(str(STAY_QUALITY))
        except Exception:
            ctx.log_exc("real estate: cannot paint the home background")
            return False
        token = (str(record.get("facility")), str(STAY_QUALITY))
        if state.get("background") != token:
            write("background: {!r} -> the room ({}) [{}]".format(
                record.get("name"), STAY_QUALITY, why))
        state["background"] = token
        return True

    def ensure_home_background(app, record):
        """建物に立っているのに背景をまだ描いていなければ、こちらから描く。

        ゲームが背景を決める経路は1つとは限らないので、包みだけに頼らない
        （ロードの後に街の外の景色が残っていた。実機 2026-09-11）。

        ただし**同じ絵を二度描かない**。
        ゲームの経路が先に描いていればそこで終わりで、
        予約から実行までの間に描かれた場合も、走った時点でもう一度確かめて降りる
        （画像の読み込みが2回走って見えた。実機 2026-09-11）。
        Kivy に触るのでメインスレッドへ回す。
        """
        token = (str(record.get("facility")), str(STAY_QUALITY))
        if state.get("background") == token or state.get("background_pending"):
            return
        state["background_pending"] = True

        def paint_now():
            state["background_pending"] = False
            if state.get("background") == token:
                # 待っている間にゲームの経路が描いた。
                return
            paint_home_background(app, record, "on arrival")

        screen.schedule(paint_now, 0)

    def background_fallback(app, why, detail):
        """ゲーム自身の背景の差し替えが落ちたときの後始末。

        本体の `change_background_image_from_location_id` は `self.app` を読むが、
        `InstantaleApp` にその属性は無い（`AttributeError`。実機 2026-09-11）。
        **呼ばれた時点で必ず落ちる本体の不具合**で、宿屋での社交では通らない。
        自分の家で「他者と交流」を選ぶとゲーム自身がここを通り、
        スレッドごと落ちて活動が終わらなくなっていた。

        絵が変わらないだけなので、ここで握って先へ通す
        （握らないと、その先の描写も好感度の変動も走らない）。
        """
        warn_once(("bg-native", why),
                  "WARN background: the game's own {} raised ({}); "
                  "the picture stays as it is".format(why, detail))
        home = home_for_background(app)
        if home is not None:
            paint_home_background(app, home, "after the game failed")

    @ctx.wrap("__main__:InstantaleApp.change_background_image_to_current_location",
              required=False, safe=True)
    def background_current(orig, self, *args, **kwargs):
        """いまの場所から背景を決める経路（ロードの後もここを通る）。"""
        record = home_for_background(self)
        if record is not None and paint_home_background(self, record, "current"):
            return None
        try:
            return orig(self, *args, **kwargs)
        except AttributeError as exc:
            background_fallback(self, "change_background_image_to_current_location",
                                exc)
            return None

    @ctx.wrap("__main__:InstantaleApp.change_background_image_from_location_id",
              required=False, safe=True)
    def background_from_id(orig, self, location_id=None, *args, **kwargs):
        """施設 id から背景を決める経路（建物へ入ったとき、社交の相手の場所）。"""
        record = home_for_background(self, location_id)
        if record is not None and paint_home_background(self, record, "location id"):
            return None
        try:
            return orig(self, location_id, *args, **kwargs)
        except AttributeError as exc:
            background_fallback(
                self, "change_background_image_from_location_id",
                "location {!r}: {}".format(location_id, exc))
            return None

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
        """滞在が終わったら、据えた主を戻して宿代を返す印を落とす。"""
        app = getattr(self, "app", None) or ui.find_app()
        try:
            if app is not None:
                end_stay(app, "the game ended the stay")
        except Exception:
            ctx.log_exc("real estate: cannot finish the stay")
            state["free_stay"] = None
        return orig(self, choice_text, *args, **kwargs)

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
