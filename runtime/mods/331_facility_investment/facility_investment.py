# -*- coding: utf-8 -*-
r"""機能追加: 街に施設を建てる（出資する）。

素のゲームの街は生成された時点で固まっていて、遊んでいる側が何かを足す手段が無い。
この MOD は役場（`administrative_office`）の選択肢に出資の窓口を1つ足す。

    [役場]  労働の募集をみる / 市民権の発行 / 施設の建設（出資） / 出る
                                    ↓
            宿屋を建てる / 雑貨店を建てる / … / 闘技場を建てる / 持っている施設 / やめる
                                    ↓
            並(12,000G) / 上等(21,600G) / 最上(38,400G) / やめる
                                    ↓
            入口の下の区画の1つに建物が1軒増え、主人が1人立つ

    [建物]  無料で泊まる（宿屋）／試合に出る（闘技場）／売買・訓練（店・道場） / 売上を受け取る(N G) / 出る

## 建てられるものは街の規模で決まる

規模が決めるのは2つ（`catalog.py`）。
その街に建てられる**合計**の軒数（村1・町2・都市3。種類は問わず、数えるのはこの MOD で建てた分だけ）と、
種類ごとの**最低の規模**（道場と闘技場は町から）。
ゲームが最初から置いた施設は数えない。

費用と売上は 種類の基礎額 × 等級 × 規模 で、都市は高いが儲かり、村は安いが細い。

## 建物も主人もローダが持つ

建物は `modfacility`（TECH.md §5.8）、主人は `modnpc`（§5.7）。
どちらもセーブには残らず、控え（`state\`）から建て直す。
この MOD が持つのは**出資の帳簿**だけ（`state\facility_investment\<世界名>.json`）。

    持ち株  = {area, facility, keeper, kind, tier, size, name, built, collected}

主人は施設を建てたときに一緒に生まれ、その施設の主として立つ。
雇っているわけではない（表現の問題で、実際にどこかで雇うわけではない）。
宿屋の主が居るので、ゲームの宿泊（`VacationStartManager`）は主を引けて落ちない。

## 売上は訪ねて受け取る

日数が進んでも勝手には入らない。
建物へ行って「売上を受け取る」を押すと、前回受け取った日からの日数 × 1日の売上 が入る。
溜まるのは `HOLD_DAYS` 日ぶんまでで、それ以上は放置しても増えない
（主人が預かっている、という理屈）。

## 中身は本体の経路に任せる

**中の選択肢はゲームが `facility_type` から出す。** 宿屋の `宿泊する`、闘技場の `試合に出る` / `観戦する`、
店の売買、道場の訓練が実行時に足した建物でもそのまま出た（実機）。
だからこの MOD が足すのは `売上を受け取る` と、自分の宿屋の `無料で泊まる` と出口だけ。

ゲームが出すもののうち、**自分の宿の `宿泊する` だけは伏せる**（`modfacility` の `hide`。TECH.md §5.8）。
自分で建てた宿に宿代を払って泊まる理由が無い（本人の指定）。
よその宿屋には掛からない（層は建物ごとに積むので、持ち株の宿にしか効かない）。

本体の経路には**実体ではなく素データを施設 id で引く**ものがある
（売買の `shopping_start_method_1`、闘技場の `ColosseumMatchStart.method`。GAME.md §2.28）。
実行時に足した施設はそこに無いので `KeyError` でワーカースレッドが死ぬ。
そこで `catalog` の `plain` が真の種類は `modfacility.register(plain=True)` で、
**中に立っている間だけ**素データの写しを `world_dict` / `save_data_dict` に置いてもらう。
保存の前に外れ、書き出しの直前にも網があるので、セーブにも `world_data.json` にも残らない。
進み（闘技場の `config` の `current_phase` / `enemy_data`）は実体と同じ辞書なので控えに載り、ロードで戻る。
写しが無いまま押されたときのために、本体の入口は網で包んで握る（`run_guarded`）。
"""

import datetime
import sys

from instantale_modloader import (durations, frames, llm, modfacility, modnpc,
                                  prices, ui)
from instantale_modloader.state import (UNKNOWN_WORLD, WorldStore, playthrough_key,
                                        playthrough_key_of_dict)

from . import catalog


LOG_BASENAME = "facility_investment.log"

#: `modfacility` / `modnpc` に名乗る持ち主。控えの中でこの MOD の建物と主人を束ねる鍵。
OWNER = "331_facility_investment"

#: 世界ごとの控え `state\facility_investment\<世界名>.json`。
STATE_DIRNAME = "facility_investment"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_facility_investment_store__"

#: 押下を横取りするための印。他の MOD と別のキーにすること。
MARK = "mod_facility_investment"

#: ボタンに載せる種類・等級（`mod_` で始めるのは、他の MOD の掃除に
#: 「印の無いボタン」と見なされないため。TECH.md §5.1.1）。
KIND_KEY = "mod_facility_investment_kind"
TIER_KEY = "mod_facility_investment_tier"

#: 役場の `facility_type`（実セーブで確認。GAME.md §2.7）。
OFFICE_FACILITY_TYPE = "administrative_office"

#: 施設の選択肢であることの目印。移動のボタンがある画面だけに足す（`309_` と同じ）。
MOVE_CLS = "MovePhaseManager"

#: 店の種類。中身はどれも売買（`shopping_start_method_1`）。
SHOP_KINDS = ("general_store", "specialty_shop", "blacksmith")

#: 宿泊の入口。実測の署名は `(app, months, quality)`（GAME.md §2.17）。
STAY_CLS = "VacationStartManager"

#: ゲームの部屋選びの入口（`宿泊する(Nヵ月)`。spec は `DisplayVacationChoice`）。
ROOM_CLS = "DisplayVacationChoice"

#: 自分の宿で出さないゲームの選択肢（`modfacility` の `hide`。TECH.md §5.8）。
#: **自分の宿に宿代を取る宿泊を並べる理由が無い**（本人の指定）。`無料で泊まる` で足りる。
#: よその宿屋には触らない（層は建物ごとなので、持ち株の宿にしか掛からない）。
HIDDEN_INN_CLASSES = (ROOM_CLS,)

#: 闘技場の入口。署名は `(app)`（`out\recon\targets.txt`）。入口そのものは実行時の施設でも通る。
ARENA_CLS = "EntryColosseumMatchManager"
#: 素データを引く本体の入口。写し（`plain`）が置いてあれば通る。落ちたときの網だけここで持つ。
#: `(包む先, 何の処理か, その網が効く種類)`。
GUARDED_PHASES = (
    # 闘技場の試合。`申し込む` で `method` が施設 id で `world_dict` を引く
    # （VERIFICATION.md §3.63）。
    ("__main__:ColosseumMatchStart.execute", "match", ("colosseum",)),
    # 売買。`shopping_start_method_1` が同じ引き方をする（GAME.md §2.28）。
    ("__main__:ShoppingStartManagerRemake.execute", "shopping", SHOP_KINDS),
    # 訓練。中で何を引くかは未測（VERIFICATION.md §3.63）。
    ("__main__:TrainingStartManager.execute", "training", ("training_facility",)),
    ("__main__:TrainingPhaseManager.execute", "training", ("training_facility",)),
)

#: ゲーム自身が施設の画面に出す入口の spec。並んでいればこちらの同じ選択肢は出さない

DAYS_PER_MONTH = 30

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む）。
# `catalog.py` へ移さないこと（TECH.md §3.8）。
COST_SCALE = 100
INCOME_SCALE = 100
HOLD_DAYS = 360
STAY_QUALITY = "private_room"

#: 種類ごとの建設費と1日の売上、建てられる最低の規模（本人の指定）。
#: 既定は `catalog.KINDS` の表と同じ。等級と街の規模の倍率はこの上に掛かるので、
#: この額は**並・町**のときの額そのもの。倍率（`COST_SCALE` / `INCOME_SCALE`）はさらにその上。
#: 名前は `<項目>_<種類を大文字にしたもの>`（`base_of` がこの規則で引く）。
COST_INN = 20000
INCOME_INN = 60
MIN_SIZE_INN = 'village'
COST_GENERAL_STORE = 25000
INCOME_GENERAL_STORE = 70
MIN_SIZE_GENERAL_STORE = 'village'
COST_BLACKSMITH = 40000
INCOME_BLACKSMITH = 100
MIN_SIZE_BLACKSMITH = 'village'
COST_SPECIALTY_SHOP = 45000
INCOME_SPECIALTY_SHOP = 110
MIN_SIZE_SPECIALTY_SHOP = 'village'
COST_TRAINING_FACILITY = 35000
INCOME_TRAINING_FACILITY = 80
MIN_SIZE_TRAINING_FACILITY = 'town'
COST_COLOSSEUM = 120000
INCOME_COLOSSEUM = 300
MIN_SIZE_COLOSSEUM = 'town'

#: 主人を誰が作るか。`llm` はゲームと同じ生成 AI に1回だけ聞く（建てるときだけ。数秒）。
#: `table` は `catalog.KEEPER_POOL` の12人から選ぶ（待ち時間ゼロ、名前は使い回し）。
KEEPER_SOURCE = "llm"

# ---------------------------------------------------------------- 文言
#: 役場の窓口の名。「出資する」では何をして何を得るかが分からず、
#: 「施設を建てて売上を得る」は説明文で選択肢の名ではなかった。役場の他の項目
#: （市民権の発行）と同じ名詞の形にし、得られるものは本文で言う（本人の選択）。
DESK_LABEL = "施設の建設（出資）"
#: 前の版の窓口の名。セーブに焼かれた古い選択肢を掃除するために残す（`OUR_LABEL_PREFIXES`）。
OLD_DESK_LABELS = ("出資する", "施設を建てて売上を得る")
BUILD_LABEL = "{}を建てる"
TIER_LABEL = "{}({}G)"
STATUS_LABEL = "持っている施設"
CANCEL_LABEL = "やめる"
COLLECT_LABEL = "売上を受け取る({}G)"
COLLECT_EMPTY_LABEL = "売上を受け取る(まだ無い)"
#: 自分の宿屋の無料の宿泊。ゲームの `宿泊する` は自分の宿では伏せるので、泊まり方はこれだけ。
#: 版33 までは同じ `宿泊する` を押させて宿代を後から返していたが、払って戻るのが見えず
#: 分かりづらかった（本人の指摘）。
STAY_LABEL = "無料で泊まる"
LEAVE_LABEL = "出る"

BUILT_TEXT = "{area}の{ward}に{name}が建った。{keeper}が{role}として店を開けている。"
COLLECTED_TEXT = "{keeper}から{days}日ぶんの売上、{gold}Gを受け取った。"
NOTHING_TEXT = "{keeper}は帳簿を開いたが、まだ渡せるものは無いという。"
NO_GOLD_TEXT = "手持ちが足りない（{gold}G 必要だ）。"
NO_ROOM_TEXT = "{area}にこれ以上建てる余地は無いようだ（合計{slots}軒まで）。"
NO_SIZE_TEXT = "{area}の規模では{kind}は成り立たないと言われた。"
NO_HUB_TEXT = "この土地には建てられる場所が無いようだ。"
DESK_TEXT = ("建設費を払うと{area}（{size}）に主人つきの施設が建つ。"
             "以後は建物を訪ねるたびに、溜まった売上（1日の額は種類と等級で決まる）を受け取れる。")
DESK_FULL_TEXT = ("建設費を払うと{area}（{size}）に主人つきの施設が建ち、訪ねるたびに売上を受け取れる。"
                  "ただし、いまここに建てられるものは無い。条件はこうだ。")
DESK_NO_SIZE_TEXT = "{area}では施設の建設を受け付けていない（街でだけ建てられる）。"
GAME_FAILED_TEXT = "{name}は今日はここまでのようだ。出直したほうがよさそうだ。"

#: 主人を作らせる頼み文の名前（`output_data\<世界>\<PC>\<この名>\N.json` に残る）。
KEEPER_MANAGER = "mod_facility_keeper"
#: 待つ秒数。建てる操作の中で1回だけ呼ぶ。返らなければ表の人で建てる。
KEEPER_TIMEOUT = 90

#: 印を失った残骸を文言で見分けて掃除するための前方一致（TECH.md §6.2）。
OUR_LABEL_PREFIXES = ((DESK_LABEL, STATUS_LABEL) + OLD_DESK_LABELS
                      + tuple(BUILD_LABEL.format(spec["label"])
                              for spec in catalog.KINDS.values())
                      + ("並(", "上等(", "最上("))


def main_module():
    """ゲーム本体のモジュール（`instantale.py`）。"""
    return sys.modules.get("__main__")


def _fmt(template, **values):
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def ordered_bucket(bucket):
    """控えの項目の並び。"""
    if not isinstance(bucket, dict):
        return bucket
    order = ("holdings",)
    ordered = {key: bucket[key] for key in order if key in bucket}
    ordered.update({k: v for k, v in bucket.items() if k not in ordered})
    return ordered


def fighters_so_far(app, record):
    """この闘技場に既に出た相手の名前。ゲームが `config["enemy_data"]` に貯める順。"""
    place = modfacility.get(app, record.get("facility"))
    config = getattr(place, "config", None) if place is not None else None
    enemies = config.get("enemy_data") if isinstance(config, dict) else None
    if not isinstance(enemies, dict):
        return []
    names = []

    def key_of(item):
        try:
            return int(item[0])
        except (TypeError, ValueError):
            return 0
    for _key, entry in sorted(enemies.items(), key=key_of):
        data = entry.get("data") if isinstance(entry, dict) else None
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names

class _LocationView(object):
    """`location` の身代わり。`description` だけ差し替え、ほかは本物へ素通し。"""
    def __init__(self, target, description):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "description", description)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_target"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_target"), name, value)

def vary_location(location, names):
    """相手を作る頼み文に渡す `location` に、既出の闘士の一文を足したものを返す。

    ゲームの頼み文は施設の名前と概要（`description`）だけを読む（`output_data` の記録。
    VERIFICATION.md §3.63）ので、概要の末尾に足す。本物の施設には書かない（身代わりを渡す）。
    `location` は辞書のことも実体のこともありうるので両方受ける。

    既に同じ一文が入っていれば足さない。`334_colosseum_custom` もどの闘技場にも同じ一文を足し、
    読む順ではあちらが外側の包みになる（先に足す）ので、こちらで見ないと二重になった（実機）。
    """
    note = catalog.arena_variety_note(names)
    if not note or location is None:
        return location
    head = catalog.ARENA_VARIETY_NOTE.split("{names}")[0].strip()
    if isinstance(location, dict):
        if head in (location.get("description") or ""):
            return location
        copied = dict(location)
        copied["description"] = "{}\n{}".format(location.get("description") or "", note).strip()
        return copied
    description = getattr(location, "description", None)
    if head in (description or ""):
        return location
    return _LocationView(location, "{}\n{}".format(description or "", note).strip())


def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "worlds": WorldStore(ctx, STATE_DIRNAME, order=ordered_bucket),
            "state": {
                # 自前のフェーズが動いている間の旗。連打の2発目を捨てる。
                "acting": False,
                # 自前の画面を出す前の選択肢（`やめる` で戻すために控える）。
                "saved": None,
                # 窓口で選んだ種類（等級を選ぶ画面へ持ち回る）。
                "choosing": None,
                # 自分の宿屋での宿泊。`{"facility": id, "name": …, "quality": …}`。
                # 宿代を前払いする部屋を覚える。
                "own_stay": None,
                # 一度書いた WARN の覚え。
                "warned": set(),
                # 手が空くのを待っているボタンの足し直し。
                "retry": False,
                # 主人の姿を書いた建物（訪ねるたびに1度）。
                "noted": None,
            },
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]
    #: この世代で `modnpc` の層を積んだ主人。`apply()` ごとに空から始まるので、
    #: 注入し直せば層は積み直る（登録簿は注入をまたいで生きる。TECH.md §5.7）。
    keepers_registered = set()

    write = ctx.logger(LOG_BASENAME)
    worlds = store["worlds"].rebind(ctx, write)
    screen = ui.Screen(ctx, write, tag="investment", mark=MARK)

    # 建物と主人はローダが持つ。関所は何本の MOD が呼んでも1つしか立たない。
    modfacility.install(ctx, write=write)
    modnpc.install(ctx, write=write)

    # ------------------------------------------------------------ 補助
    def warn_once(token, text):
        if token in state["warned"]:
            return False
        state["warned"].add(token)
        write(text)
        return True

    # ------------------------------------------------------------ 控え
    def current_key(app):
        """いまの周回の鍵（世界×主人公。`state.playthrough_key`）。

        帳簿を世界名だけで引くと、主人公が死んで同じ世界で作り直したときに
        前の主人公の建物と主人が新しい主人公に引き継がれる（実機）。
        ロードの建て直しの間は `world_loaded` が引数のセーブから決めた鍵を使う
        （`app` の辞書と `player` はまだ前の周回を指していることがある）。
        """
        override = state.get("key_override")
        return override if override else playthrough_key(app)

    def bucket_of(key):
        bucket = worlds.load(key)
        bucket.setdefault("holdings", [])
        return bucket

    def holdings_of(app):
        key = current_key(app)
        if not key or key == UNKNOWN_WORLD:
            return []
        return [h for h in bucket_of(key).get("holdings") or [] if isinstance(h, dict)]

    def holding_of(app, facility_id):
        for record in holdings_of(app):
            if str(record.get("facility") or "") == str(facility_id):
                return record
        return None

    def holdings_in(app, area_id):
        return [h for h in holdings_of(app) if str(h.get("area")) == str(area_id)]

    def save(app):
        key = current_key(app)
        if key and key != UNKNOWN_WORLD:
            worlds.save(key)

    # ------------------------------------------------------------ 種類ごとの値
    def setting_of(prefix, kind):
        """`<prefix>_<種類>` の設定。宣言が無ければ None（表の値が使われる）。

        モジュールのグローバルから引く（ローダは `apply()` の前にそこへ書き込む。TECH.md §3.8）。
        18個の分岐を書く代わりに規則で引くので、種類が増えても宣言を足すだけでよい。
        """
        return globals().get("{}_{}".format(prefix, str(kind).upper()))

    def base_cost(kind):
        return setting_of("COST", kind)

    def base_income(kind):
        return setting_of("INCOME", kind)

    def min_size_of(kind):
        return setting_of("MIN_SIZE", kind)

    def cost_of(kind, tier, size):
        return catalog.cost_of(kind, tier, size, COST_SCALE, base=base_cost(kind))

    def income_of(kind, tier, size):
        return catalog.income_per_day(kind, tier, size, INCOME_SCALE,
                                      base=base_income(kind))

    # ------------------------------------------------------------ 街の規模と軒数
    def area_size(app, area_id):
        """街の規模。実行時の `Area.size` は読めない（`324_` の実機 38/38）ので
        素データから取る。街でなければ None。"""
        for attr in ("save_data_dict", "world_dict"):
            container = getattr(app, attr, None)
            areas = container.get("areas") if isinstance(container, dict) else None
            if not isinstance(areas, dict):
                continue
            entry = areas.get(str(area_id))
            if entry is None:
                for k, v in areas.items():
                    if str(k) == str(area_id):
                        entry = v
                        break
            if isinstance(entry, dict):
                size = catalog.normalize_size(entry.get("size"))
                if size:
                    return size
        return None

    def built_here(app, area_id):
        """その街にこの MOD で建てた軒数。種類は問わない。

        **ゲームが最初から置いた施設は数えない**（本人の指定）。
        上限は「その街に合計で何軒建てられるか」なので、素の街の中身とは別の話。
        """
        return len(holdings_in(app, area_id))

    def room_for(app, area, kind):
        """建ててよいか。`(可否, 理由)`。理由は `size` / `cap` / `hub` / None。"""
        area_id = ui.area_id_of(area)
        size = area_size(app, area_id)
        if size is None or not catalog.allowed(kind, size, min_size_of(kind)):
            return False, "size"
        if built_here(app, area_id) >= catalog.slots(size):
            return False, "cap"
        node, hub = modfacility.hub_of(area)
        if node is None or hub is None:
            return False, "hub"
        return True, None

    # ------------------------------------------------------------ 建物と主人
    def facility_id_for(area_id, serial):
        return modfacility.make_id(OWNER, "{}-{}".format(area_id, serial))

    def keeper_id_for(area_id, serial):
        return modnpc.make_id(OWNER, "keeper-{}-{}".format(area_id, serial))

    def next_serial(app, area_id):
        used = set()
        for record in holdings_in(app, area_id):
            tail = str(record.get("facility") or "").rsplit("-", 1)[-1]
            if tail.isdigit():
                used.add(int(tail))
        serial = 1
        while serial in used:
            serial += 1
        return serial

    def per_day(record):
        return income_of(record.get("kind"), record.get("tier"), record.get("size"))

    def owed(app, record):
        """いま受け取れる売上と、その日数。`(金額, 日数)`。"""
        today = ui.game_day(app)
        since = record.get("collected")
        if today is None or not isinstance(since, int):
            return 0, 0
        days = max(0, min(int(today) - since, int(HOLD_DAYS)))
        return days * per_day(record), days

    def investor_name(app, record=None):
        """出資者（プレイヤー）の名。帳簿に控えた名 → いまのプレイヤー名。"""
        name = (record or {}).get("investor") if isinstance(record, dict) else None
        if not name:
            name = getattr(getattr(app, "player", None), "name", None)
        return str(name) if name else ""

    def keeper_name(app, record):
        """主人の名。帳簿に控えた名 → 実体の名 → 鍵から引いた名。

        控えを先に見るのは、**建て直しても同じ人**にするため（版25 から帳簿に持つ）。
        """
        stored = record.get("keeper_name") if isinstance(record, dict) else None
        if stored:
            return str(stored)
        handle = modnpc.get(app, record.get("keeper"))
        name = getattr(handle, "name", None) if handle is not None else None
        return name or catalog.keeper_choice(record.get("keeper"))[0]

    def generate_keeper(app, record, taken, places):
        """建物の名前と主人をゲームと同じ生成 AI に作らせる。`(主人の素データ, 施設名)`。

        呼ぶのは**建てるときの1回だけ**。答えは帳簿に控えるので、
        建て直しでもロードでも二度と呼ばない（同じ建物と同じ人が戻る）。
        作れなければ `(None, "")` で、呼ぶ側が表から選ぶ。
        """
        if str(KEEPER_SOURCE).lower() != "llm":
            return None, ""
        structure = llm.create_structure(ctx, "FacilityKeeper", {
            "name": (str, ...), "category": (str, ...),
            "speech_style": (str, ...), "personality": (str, ...),
            "profile": (str, ...), "facility_name": (str, ...),
        }, label="investment")
        if structure is None:
            write("keeper: cannot build the structure; using the table")
            return None, ""
        system, user = catalog.keeper_prompt(
            record.get("kind"), record.get("tier"), record.get("area_name"),
            record.get("size"), ui.world_overview(app),
            record.get("investor"), taken, places)
        answer = llm.ask(ctx, KEEPER_MANAGER,
                         [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                         timeout=KEEPER_TIMEOUT, structure=structure,
                         label="investment", write=write)
        made_name = catalog.made_name_from(answer, places)
        fields = catalog.keeper_fields_from(
            answer, record.get("kind"), record.get("area_name"),
            made_name or record.get("name"),
            record.get("keeper"), investor=record.get("investor"), taken=taken)
        if fields is None:
            write("keeper: the answer was not usable; using the table")
            return None, made_name
        write("keeper: {!r}（{}）was made for {!r}".format(
            fields.get("name"), fields.get("category"), made_name or record.get("name")))
        return fields, made_name

    def keeper_names(app, skip=None):
        """新しい主人が避ける名前。**その世界で使われている名前を全部**。

        自分の帳簿の主人に加えて、ローダの窓口（`modnpc.names_in_use`）が返す
        素の NPC・実行時の名簿・他の MOD の NPC・プレイヤーまで見る。
        名前が重なると、**名前でしか相手を引けない場所**で別人に当たる
        （ゲームの人物欄。VERIFICATION.md §3.68）。
        """
        found = []
        for record in holdings_of(app):
            if record is skip:
                continue
            name = keeper_name(app, record)
            if name and name not in found:
                found.append(name)
        mine = str(skip.get("keeper") or "") if isinstance(skip, dict) else ""
        for name in modnpc.names_in_use(app, skip=[mine] if mine else ()):
            if name not in found:
                found.append(name)
        return found

    def place_names(app, skip=None):
        """その世界の施設の名前。新しい建物はこれと重ならないように選ぶ（版37）。"""
        return [str(record.get("name") or "") for record in holdings_of(app)
                if record is not skip and record.get("name")]

    def building_choices(app, facility_id):
        """建物の中の選択肢。出口はローダが足す。"""
        record = holding_of(app, facility_id)
        if record is None:
            return []
        # 宿泊の最中（活動の選択肢）に混ぜないのはローダの判定（`is_top_screen`）。
        # ここで `own_stay` を見ていたら、終える処理の中の組み直しに旗が間に合わず
        # 2つだけの画面で止まった（実機）。画面の判断は1か所（TECH.md §5.8）。
        out = []
        kind = record.get("kind")
        # 自分の宿屋の泊まり方はこれだけ（版40 でゲームの `宿泊する` を伏せた）。
        # 同じ入口を押させて後から返す形は、払って戻るのが見えず分かりづらかった（版34）。
        if kind == "inn":
            out.append({"key": "stay", "label": STAY_LABEL,
                        # 伏せたゲームの `宿泊する` と同じ並びで出す（本人の指定）。
                        "replaces": ROOM_CLS,
                        "on": lambda info: act(info["app"], "stay", facility_id)})
        # 闘技場の `試合に出る` はゲーム自身が出す（`colosseum` 型。実機）。こちらは足さない。
        gold, _days = owed(app, record)
        label = COLLECT_LABEL.format(ui.money(gold)) if gold > 0 else COLLECT_EMPTY_LABEL
        out.append({"key": "collect", "label": ui.rewrite_coins(label),
                    "on": lambda info: act(info["app"], "collect", facility_id)})
        return out

    def register_holding(record):
        """持ち株1つぶんの層を積む（建物と主人）。"""
        facility_id = str(record.get("facility") or "")
        keeper_id = str(record.get("keeper") or "")
        if not facility_id or not keeper_id:
            return ""

        def choices(info):
            found = building_choices(info["app"], info["facility_id"])
            return [] if found is None else found

        def no_exit(info):
            return [] if building_choices(info["app"], info["facility_id"]) is None \
                else info["args"]["choices"]

        modfacility.register(
            OWNER, facility_id=facility_id,
            fields={"name": record.get("name") or "",
                    "description": catalog.description_of(record.get("kind"),
                                                          record.get("tier")),
                    "facility_type": record.get("kind"),
                    "tier": record.get("tier"),
                    "owner": keeper_id},
            choices=choices, exit_label=LEAVE_LABEL,
            # 持ち株の建物はロードで必ず建ち直る（手放す経路がまだ無い）。
            keep_inside=True,
            # 自分の宿ではゲームの `宿泊する`（宿代を取る）を出さない。
            hide=HIDDEN_INN_CLASSES if record.get("kind") == "inn" else None,
            # 入口ではなく、入口の下の区画の1つに繋ぐ（本人の指定）。
            # 入口に置くと街に着いた瞬間に店が見え、区画を回っても見つからない。
            hub="ward",
            # ゲームが素データを施設 id で引く種類（売買・闘技場の試合）だけ、
            # 中に立っている間だけ写しを置いてもらう。宿屋は要らない
            # （`VacationStartManager` は実体で足りた）ので、余計な写しは置かない。
            plain=catalog.needs_plain(record.get("kind")),
            # 絵は描かない。ゲームが名前で引いて生成する（`modfacility は絵に触らない`。TECH.md §5.8）。
            on={"choices": no_exit,
                "leave": lambda info: end_stay(info["app"], "left the building")},
            write=write)
        # 主人の層は**この世代のこのロードで1度だけ**積む（ロードのたびに積み直す）。塗り直しのたびに呼ぶとログが毎手流れる。
        # 「登録簿に層が在れば積まない」にしていたら、注入し直しても前の版の層が残り、
        # 版13 で足した `notes`（出資者の一文）が実機の頼み文に出なかった（VERIFICATION.md §3.63）。
        if keeper_id not in keepers_registered:
            keepers_registered.add(keeper_id)
            # 建てたときに作った主人（LLM か表）を帳簿から使う。
            # 無ければ表から組む（版25 より前の持ち株）。
            made = record.get("keeper_fields")
            fields = dict(made) if isinstance(made, dict) and made.get("name") else \
                catalog.keeper_fields(record.get("kind"), record.get("area_name"),
                                      record.get("name"), keeper_id,
                                      investor=record.get("investor"),
                                      name=record.get("keeper_name"))
            modnpc.register(
                OWNER, npc_id=keeper_id,
                fields=fields,
                # 会話の頼み文に毎回足す。相手が出資者本人だと主人に分からせる
                # （`profile` は控えの写しが勝つので、版12 までの主人にはここでしか届かない）。
                notes=lambda info: catalog.keeper_notes(
                    investor_name(info.get("app"), record), record.get("name")),
                write=write)
        return facility_id

    def heal_keeper(app, keeper_id, world=None):
        """版4以前に生まれた主人の `experience_level` を埋める。

        控えの写しは層の初期値より勝つので（`modnpc.spawn`）、None のまま写った主人は
        建て直しても None のまま。詳細生成がこれを掛け算に使って落ちる（VERIFICATION.md §3.63）。
        """
        handle = modnpc.get(app, keeper_id, world=world)
        if handle is None:
            return False
        healed = False
        try:
            if getattr(handle, "experience_level", None) is None:
                handle.experience_level = catalog.KEEPER_LEVEL
                write("keeper: {} had no experience_level; set to {}".format(
                    keeper_id, catalog.KEEPER_LEVEL))
                healed = True
            if warm_keeper(handle):
                write("keeper: {} now holds the investor in some regard (affinity {})".format(
                    keeper_id, catalog.KEEPER_AFFINITY))
                healed = True
        except Exception:
            ctx.log_exc("investment: cannot heal the keeper {}".format(keeper_id))
        return healed

    def warm_keeper(handle):
        """主人のプレイヤーへの好感度が初期値より低ければ上げる。上げたら True。

        版12 までに生まれた主人は「警戒心がある」（`affinity` 0）のまま。
        会話で育った値は下げない（初期値より上ならそのまま）。
        """
        relationship = getattr(handle, "relationship", None)
        if not isinstance(relationship, dict):
            relationship = {}
            handle.relationship = relationship
        player = relationship.get("player")
        if not isinstance(player, dict):
            relationship["player"] = dict(catalog.keeper_relationship()["player"])
            return True
        try:
            affinity = int(player.get("affinity") or 0)
        except (TypeError, ValueError):
            affinity = 0
        if affinity >= catalog.KEEPER_AFFINITY:
            return False
        player["affinity"] = catalog.KEEPER_AFFINITY
        tags = player.get("relationship")
        if isinstance(tags, list) and "出資者" not in tags:
            tags.append("出資者")
        return True

    def note_keeper(app, record, facility, world=None):
        """主人のいまの姿を、建物に立っている間に1度だけ書く（一覧に出ないときの手掛かり）。

        会話の一覧は `world.characters` を舐めて各人物の `.location` と `.config` を読む
        （`229_` の実測）。どちらで落ちたかを後から読めるように、同じものを並べる。
        """
        keeper_id = str(record.get("keeper") or "")
        facility_id = str(record.get("facility") or "")
        if not modfacility.inside(app, facility_id):
            state["noted"] = None
            return
        token = (facility_id, getattr(ctx, "generation", None))
        if state.get("noted") == token:
            return
        state["noted"] = token
        target = world if world is not None else getattr(app, "world", None)
        roster = getattr(target, "characters", None)
        instance = roster.get(keeper_id) if isinstance(roster, dict) else None
        player = getattr(app, "player", None)
        player_at = getattr(player, "location", None)
        rec = modnpc.registry().get(keeper_id) or {}
        at = getattr(instance, "location", None)
        write("keeper: {} in_roster={} record_has_instance={} same_instance={} "
              "location_is_building={} location_is_player_location={} location_id={!r} "
              "current_area_is_player_area={} config={!r} level={!r} "
              "in_facility_characters={} owner={!r}".format(
                  keeper_id, instance is not None, rec.get("character") is not None,
                  rec.get("character") is instance, at is facility, at is player_at,
                  getattr(at, "id", None),
                  getattr(instance, "current_area", None) is getattr(player, "current_area", None),
                  getattr(instance, "config", None), getattr(instance, "experience_level", None),
                  keeper_id in (getattr(facility, "characters", None) or []),
                  getattr(facility, "owner", None)))

    def keeper_at(app, keeper_id, facility, world=None):
        """主人が**その建物の実体**に立っているか。

        記録（`placed`）ではなく実体の `.location` を見る。
        世界を読み直すと施設もエリアも別のオブジェクトになるので、
        記録だけで判じると古い世界の建物に立ったままの人物を「置いてある」と読む
        （実機。会話の一覧から主人が消えた）。
        """
        if facility is None:
            return False
        handle = modnpc.get(app, str(keeper_id), world=world)
        character = getattr(handle, "character", None) if handle is not None else None
        if character is None or getattr(character, "location", None) is not facility:
            return False
        # 中に立っているなら、プレイヤーが立っている実体とも同じでなければ置き直す
        # （建て直しで別のオブジェクトになった建物に居残っている形）。
        player_at = getattr(getattr(app, "player", None), "location", None)
        if player_at is not None and not isinstance(player_at, (str, int)) \
                and str(getattr(player_at, "id", "")) == str(getattr(facility, "id", "")) \
                and player_at is not facility:
            return False
        return True

    def apply_holdings(app, world, key, why):
        """控えの持ち株をこの世界へ当てる。建てた棟数を返す。

        ロードの直後と、選択肢が組まれるたびに呼ばれる。既に立っているものは何もしない。
        主人は建物が立った後に置く（フレームワークどうしは順序を約束しない。TECH.md §5.8）。
        """
        areas = ui.areas_of_world(world)
        if not areas:
            return 0
        built = 0
        for record in holdings_of(app) if key else []:
            area_id = str(record.get("area") or "")
            if area_id not in areas:
                warn_once(("area", key, area_id),
                          "WARN {}: area {!r} is not in this world".format(why, area_id))
                continue
            facility_id = register_holding(record)
            if not facility_id:
                continue
            standing = bool((modfacility.registry().get(facility_id) or {}).get("placed"))
            facility = modfacility.spawn(app, facility_id, area_id, world=world, write=write)
            if facility is None:
                warn_once(("hub", key, area_id),
                          "WARN {}: {!r} is not standing in area {!r}".format(
                              why, record.get("name"), area_id))
                continue
            keeper_id = str(record.get("keeper"))
            # 名簿も**読み直しの最中は `world` 側**（`app.world` はまだ前の世界）。
            roster = getattr(world if world is not None
                             else getattr(app, "world", None), "characters", None)
            if not (isinstance(roster, dict) and keeper_id in roster):
                # 居なければ組む。毎手呼ぶと `modnpc` が毎回 `is in the world` を書く
                # （組み直してはいないが、ログが選択肢の回数だけ伸びる）。
                modnpc.spawn(app, keeper_id, world=world, write=write)
            if not keeper_at(app, keeper_id, facility, world=world):
                modnpc.place(app, keeper_id, area_id, facility_id, owner=True,
                             world=world, write=write)
            heal_keeper(app, keeper_id, world=world)
            note_keeper(app, record, facility, world=world)
            state["warned"].discard(("hub", key, area_id))
            state["warned"].discard(("area", key, area_id))
            if not standing:
                built += 1
        settle_keeper_names(app, world=world)
        if built:
            write("{}: {} building(s) standing in world {!r}".format(why, built, key))
        return built

    def settle_keeper_names(app, world=None):
        """帳簿に主人の名を控える。重なっていたら後の方を空いている名前へ寄せる。

        版24 までは名前を控えず、建てるたびに鍵から選んでいた。候補が6人しかなかったので
        4軒目で重なった（実機：闘技場と道場がどちらもトビアス）。
        版25 は建てたときに控えるが、**それ以前の持ち株には控えが無い**ので、ここで埋める。
        埋めるついでに、重なっている分だけ空いている名前へ寄せる（その1回だけ実体の名も変える）。

        **見るのは自分の主人だけではない。** その世界で使われている名前を全部
        （素の NPC・他の MOD の人物・プレイヤー。`modnpc.names_in_use`）先に置いてから
        突き合わせる。名前が重なると、ゲームが名前で人を扱う経路
        （立ち絵のフォルダ・LLM へ渡す名前の列挙）で別人に当たるため（VERIFICATION.md §3.68）。
        """
        own = [str(record.get("keeper")) for record in holdings_of(app)
               if record.get("keeper")]
        used = list(modnpc.names_in_use(app, skip=own))
        changed = False
        for record in holdings_of(app):
            keeper_id = record.get("keeper")
            name = record.get("keeper_name")
            handle = modnpc.get(app, keeper_id, world=world) if keeper_id else None
            if not name:
                name = getattr(handle, "name", None) \
                    or catalog.keeper_choice(keeper_id)[0]
            if name in used:
                was, name = name, catalog.keeper_choice(keeper_id, used)[0]
                if name in used:
                    warn_once(("keeper-name", keeper_id),
                              "WARN keeper: {!r} is already used in this world and the "
                              "table has no free name; leaving {} as it is".format(
                                  was, keeper_id))
                    name = was
                else:
                    # 控えの素データにも同じ名前を書く。建て直しはこちらが勝つので、
                    # 直さないと次のロードで元の名前に戻る。
                    made = record.get("keeper_fields")
                    if isinstance(made, dict) and made.get("name"):
                        made["name"] = name
                        changed = True
                    if handle is not None and getattr(handle, "name", None) != name:
                        try:
                            handle.name = name
                        except Exception:
                            ctx.log_exc(
                                "investment: cannot rename the keeper {}".format(keeper_id))
                    write("keeper: {} was renamed {!r} -> {!r} "
                          "(the name was already used in this world)".format(
                              keeper_id, was, name))
            if record.get("keeper_name") != name:
                record["keeper_name"] = name
                changed = True
            used.append(name)
        if changed:
            save(app)
        return used

    # ------------------------------------------------------------ 出資
    def build(app, kind, tier):
        """出資して建てる。成否を返す。"""
        area = ui.current_area(app)
        if area is None:
            write("build: no current area")
            return False
        area_id = ui.area_id_of(area)
        area_name = frames.short(getattr(area, "name", ""), 40) or area_id
        spec = catalog.kind_of(kind)
        if spec is None or tier not in catalog.TIERS:
            write("WARN build: unknown kind/tier {!r}/{!r}".format(kind, tier))
            return False
        ok, why = room_for(app, area, kind)
        if not ok:
            write("build: {} refused in area {!r} ({})".format(kind, area_id, why))
            text = {"size": NO_SIZE_TEXT, "cap": NO_ROOM_TEXT}.get(why, NO_HUB_TEXT)
            screen.say(app, _fmt(text, area=area_name, kind=spec["label"],
                                 slots=catalog.slots(area_size(app, area_id))))
            return False
        size = area_size(app, area_id)
        price = cost_of(kind, tier, size)
        gold = ui.gold_of(app)
        if gold is None or price is None:
            write("WARN build: cannot read the gold or the price")
            return False
        if gold < price:
            screen.say(app, ui.rewrite_coins(_fmt(NO_GOLD_TEXT, gold=ui.money(price))))
            return False
        serial = next_serial(app, area_id)
        facility_id = facility_id_for(area_id, serial)
        keeper_id = keeper_id_for(area_id, serial)
        places = place_names(app)
        # 表から選ぶときも、その世界で使っている建物の名前は飛ばす（版37。
        # 鍵だけで選んでいた頃は別の世界でも同じ土地の同じ番号なら同じ名前になった）。
        name = catalog.facility_name(kind, tier, area_name, facility_id, taken=places)
        day = ui.game_day(app)
        record = {
            "area": area_id,
            "area_name": area_name,
            "investor": investor_name(app),
            # 主人は下で作る（`KEEPER_SOURCE`）。表のときは使っていない名前を選ぶ
            # （6人だと4軒目で重なった。実機：闘技場と道場がどちらもトビアス）。
            "keeper_name": "",
            "facility": facility_id,
            "keeper": keeper_id,
            "kind": kind,
            "tier": tier,
            "size": size,
            "name": name,
            "price": price,
            "built": day,
            "collected": day,
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        # 主人を先に決める（帳簿に控える）。`register_holding` はそれを使う。
        taken = keeper_names(app)
        made, made_name = generate_keeper(app, record, taken, places)
        if made_name:
            # 生成 AI が付けた名前。建物の描写も帳簿もこれで揃える。
            name = made_name
            record["name"] = name
            write("build: the generator named it {!r}".format(name))
        if made is None:
            made = catalog.keeper_fields(kind, area_name, name, keeper_id,
                                         investor=record.get("investor"), taken=taken)
        record["keeper_fields"] = made
        record["keeper_name"] = made.get("name")
        register_holding(record)
        # 新築。同じ id の写しが登録簿に残っていても使わない（周回をまたぐと id が重なる）。
        facility = modfacility.spawn(app, facility_id, area_id, fresh=True, write=write)
        if facility is None:
            modfacility.unregister(OWNER, facility_id, app=app, write=write)
            modnpc.unregister(OWNER, keeper_id, app=app, write=write)
            # 層は外したので「積んだ」覚えも戻す。残すと、同じ土地で建て直したとき
            # （同じ番号＝同じ id）に `register_holding` が主人の層を積まず、主の居ない建物になる。
            keepers_registered.discard(keeper_id)
            screen.say(app, NO_HUB_TEXT)
            return False
        if modnpc.spawn(app, keeper_id, write=write) is not None:
            modnpc.place(app, keeper_id, area_id, facility_id, owner=True, write=write)
            heal_keeper(app, keeper_id)
        else:
            write("WARN build: the keeper {} did not spawn; the building has no owner"
                  .format(keeper_id))
        bucket_of(current_key(app))["holdings"].append(record)
        save(app)
        ui.add_gold(app, -price, on_error=lambda msg: write("WARN build: cannot charge"))
        write("built: {} {} {!r} id={} keeper={} area={!r} size={} price={}".format(
            kind, tier, name, facility_id, keeper_id, area_id, size, price))
        spot = (modfacility.registry().get(facility_id) or {}).get("placed") or ()
        hub = modfacility.facility_of(app, spot[2]) if len(spot) > 2 else None
        hub = hub or (ui.find_facility(area, spot[2])[0] if len(spot) > 2 else None)
        screen.say(app, _fmt(BUILT_TEXT, area=area_name, name=name,
                             ward=getattr(hub, "name", None) or "区画",
                             keeper=keeper_name(app, record),
                             role=spec.get("keeper_role", "主人")))
        return True

    def collect(app, facility_id):
        """溜まった売上を受け取る。受け取った額を返す。"""
        record = holding_of(app, facility_id)
        if record is None:
            return 0
        gold, days = owed(app, record)
        who = keeper_name(app, record)
        if gold <= 0:
            screen.say(app, _fmt(NOTHING_TEXT, keeper=who))
            return 0
        ui.add_gold(app, gold, on_error=lambda msg: write("WARN collect: cannot pay"))
        today = ui.game_day(app)
        record["collected"] = today if today is not None else record.get("collected")
        save(app)
        write("collected: {}G for {} day(s) at {!r}".format(gold, days, record.get("name")))
        screen.say(app, ui.rewrite_coins(_fmt(COLLECTED_TEXT, keeper=who, days=days,
                                              gold=ui.money(gold))))
        return gold

    # ------------------------------------------------------------ 中でできること
    def start_stay(app, facility_id):
        """自分の宿屋に泊まる。宿屋の宿泊と同じ経路で、宿代は前払いで打ち消す。"""
        record = holding_of(app, facility_id)
        if record is None:
            return
        cls = getattr(main_module(), STAY_CLS, None)
        if cls is None:
            write("WARN stay: __main__.{} is not available".format(STAY_CLS))
            return
        quality = str(STAY_QUALITY)
        # 第1引数は**いまの宿屋と同じ月数**。ローダの窓口に聞く
        # （`durations.inn_stay`。TECH.md §3.3.2）ので、この MOD は式も、
        # 長さを変える MOD の名前も持たない。
        # 版40 までは 1 を直に渡していた。「本体の経路は5回とも `['1', ...]`」という
        # 実測から決めたが、その5回は `315_vacation_custom` が週単位の設定で走っていた回で、
        # 週単位は月数を1に落として日数だけを縮める。素の `3ヵ月` では本体は 3 を渡すので、
        # 同じ宿で払えば3ヵ月、無料なら1ヵ月という食い違いになっていた
        # （実機 2026-09-20。VERIFICATION.md §3.63 #7e）。
        months = max(1, int(durations.inn_stay(app, write=write)["months"]))
        try:
            phase = cls(app, months, quality)
        except Exception:
            ctx.log_exc("investment: cannot build {}".format(STAY_CLS))
            return
        # `quality` を控えるのは、前払いする額を**この部屋で**引くため。設定が滞在の
        # 最中に変わっても、渡した部屋と払う部屋が食い違わない。
        state["own_stay"] = {"facility": str(facility_id), "name": record.get("name"),
                             "quality": quality}
        write("stay: starting {} months={} quality={!r} at {!r}".format(
            STAY_CLS, months, quality, record.get("name")))
        screen.start_phase(app, phase, STAY_LABEL)

    def end_stay(app, why):
        if state.get("own_stay") is None:
            return
        write("stay: finished at {!r} ({})".format(state["own_stay"].get("name"), why))
        state["own_stay"] = None

    def staying_here(app):
        """こちらの「無料で泊まる」から始めた宿泊か（`own_stay`）。

        ゲーム自身の `宿泊する` から始めた宿泊は**素のまま**（宿代を取る）。
        版33 までは両方を無料にしていたが、払って戻るのが見えず分かりづらかったので、
        無料は別のボタンにした（本人の指摘）。
        版40 からは自分の宿にその `宿泊する` を出さない（`HIDDEN_INN_CLASSES`）ので、
        ここを通るのはよその宿屋の宿泊だけになる。判定は残す
        （ボタンを伏せてもゲームの経路そのものは塞いでいない）。
        """
        home = state.get("own_stay")
        if isinstance(home, dict) and modfacility.inside(app, home.get("facility")):
            return home
        return None

    def own_building(app, kinds=None):
        """立っているのが自分の建物ならその持ち株。`kinds` を渡すとその種類だけ。"""
        here = modfacility.inside(app)
        record = holding_of(app, here) if here else None
        if record is None:
            return None
        return record if kinds is None or record.get("kind") in kinds else None

    def own_arena(app):
        """立っているのが自分の闘技場ならその持ち株。"""
        return own_building(app, ("colosseum",))

    def recover(app, why):
        """ゲームの処理が途中で落ちた後、操作を戻す（`330_` と同じ手当て）。

        待機表示はゲームが `is_button_enabled=False` で止めているだけなので（GAME.md §2.4）、
        戻して塗り直せば押せる状態に戻る。ワーカースレッドの中なので画面はメインスレッドへ回す。
        """
        screen.schedule(lambda: screen.busy_off(app), 0)
        write("{}: gave the controls back".format(why))

    # ------------------------------------------------------------ 窓口
    def show_desk(app):
        """役場の出資の窓口。建てられる種類を並べ、**全部の種類の条件**を本文に出す。

        建てられないときに「いま建てられるものは無い」だけだと、規模が足りないのか
        軒数が埋まっているのかが分からない（本人の指摘。村で出た）。
        """
        area = ui.current_area(app)
        area_name = frames.short(getattr(area, "name", ""), 40) or "この土地"
        state["saved"] = [item for item in (getattr(app, "buttons", None) or [])
                          if not screen.mark_of(item)]
        state["choosing"] = None
        entries = []
        lines = []
        buildable = 0
        no_hub = False
        size = area_size(app, ui.area_id_of(area)) if area is not None else None
        for kind in catalog.enabled_kinds():
            spec = catalog.kind_of(kind)
            ok, why = room_for(app, area, kind) if area is not None else (False, "size")
            no_hub = no_hub or why == "hub"
            if ok:
                entry = screen.button(BUILD_LABEL.format(spec["label"]), mark="kind",
                                      extra={KIND_KEY: kind})
                if entry is not None:
                    entries.append(entry)
                    buildable += 1
            # 本文に出すのは**建たない種類の条件だけ**（本人の指定）。
            # 建つ種類の値札は選択肢を押した先（等級の画面）で出る。
            line = catalog.requirement_text(kind, size, min_size_of(kind))
            if line:
                lines.append(line)
        if holdings_of(app):
            entry = screen.button(STATUS_LABEL, mark="status")
            if entry is not None:
                entries.append(entry)
        cancel = screen.button(CANCEL_LABEL, mark="cancel")
        if cancel is not None:
            entries.append(cancel)
        if size is None:
            screen.say(app, _fmt(DESK_NO_SIZE_TEXT, area=area_name))
        else:
            head = _fmt(DESK_TEXT if buildable else DESK_FULL_TEXT,
                        area=area_name, size=catalog.SIZE_LABEL.get(size, size))
            # 合計の軒数が先、種類ごとの「その規模には建たない」が後。
            lines.insert(0, catalog.slots_text(
                size, built_here(app, ui.area_id_of(area)) if area is not None else 0))
            if no_hub:
                lines.append(NO_HUB_TEXT)
            screen.say(app, "\n".join([head] + lines))
        write("desk: {} kind(s) offered in area {!r} ({})".format(
            buildable, ui.area_id_of(area) if area is not None else None, size))
        screen.apply_buttons(app, entries, "desk")

    def show_tiers(app, kind):
        """等級を選ぶ。値札は今の街の規模で計算する。"""
        area = ui.current_area(app)
        spec = catalog.kind_of(kind)
        if area is None or spec is None:
            back(app, "no area")
            return
        size = area_size(app, ui.area_id_of(area))
        state["choosing"] = kind
        entries = []
        for tier in catalog.TIERS:
            price = cost_of(kind, tier, size)
            if price is None:
                continue
            label = TIER_LABEL.format(catalog.TIER_LABEL[tier], ui.money(price))
            entry = screen.button(ui.rewrite_coins(label), mark="tier",
                                  extra={KIND_KEY: kind, TIER_KEY: tier})
            if entry is not None:
                entries.append(entry)
        cancel = screen.button(CANCEL_LABEL, mark="cancel")
        if cancel is not None:
            entries.append(cancel)
        income = [income_of(kind, t, size) for t in catalog.TIERS]
        screen.say(app, ui.rewrite_coins(
            "{}の等級を選ぶ。1日の売上は 並 {}G / 上等 {}G / 最上 {}G の見込み。".format(
                spec["label"], *[ui.money(v) for v in income])))
        screen.apply_buttons(app, entries, "tiers")

    def show_status(app):
        lines = []
        for record in holdings_of(app):
            gold, days = owed(app, record)
            spot = (modfacility.registry().get(str(record.get("facility"))) or {}).get("placed")
            area = ui.world_areas(app).get(str(record.get("area")))
            hub = ui.find_facility(area, spot[2])[0] if (area is not None and spot) else None
            where = "{}・{}".format(record.get("area_name"),
                                    getattr(hub, "name", None) or "区画")
            lines.append("{}（{}・{}・{}）: 売上 {}G（{}日ぶん）".format(
                record.get("name"), where,
                catalog.kind_of(record.get("kind"))["label"]
                if catalog.kind_of(record.get("kind")) else record.get("kind"),
                catalog.TIER_LABEL.get(record.get("tier"), record.get("tier")),
                ui.money(gold), days))
        screen.say(app, ui.rewrite_coins("\n".join(lines) if lines else "持っている施設は無い。"))
        back(app, "status")

    def back(app, why="back"):
        """自前の画面から役場の選択肢へ戻す（窓口のボタンは塗り直しの中で足し直される）。"""
        saved, state["saved"] = state["saved"], None
        state["choosing"] = None
        write("back: {} ({} entries)".format(why, len(saved) if saved is not None else "keep"))
        screen.apply_buttons(app, saved, "back")

    # ------------------------------------------------------------ 自前のフェーズ
    class InvestPhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**（セーブに焼かれる）。"""

        def __init__(self, app, action, kind=None, tier=None, facility_id=None):
            self.app = app
            self.action = action
            self.kind = kind
            self.tier = tier
            self.facility_id = facility_id

        def execute(self, choice_text):
            state["acting"] = True
            try:
                run_action(self.app, self.action, self.kind, self.tier, self.facility_id)
            except Exception:
                ctx.log_exc("investment: phase {!r} failed".format(self.action))
            finally:
                state["acting"] = False

    def run_action(app, action, kind=None, tier=None, facility_id=None):
        if action == "desk":
            show_desk(app)
        elif action == "kind":
            show_tiers(app, kind)
        elif action == "tier":
            build(app, kind, tier)
            back(app, "built")
        elif action == "status":
            show_status(app)
        elif action == "cancel":
            back(app, "cancelled")
        elif action == "collect":
            collect(app, facility_id)
        elif action == "stay":
            start_stay(app, facility_id)
        else:
            write("WARN unknown action {!r}".format(action))

    def act(app, action, facility_id):
        """建物の中の選択肢（ローダが押下を渡してくる）。フェーズに乗せて起こす。"""
        if state["acting"]:
            write("ignored {!r}: the previous press is still running".format(action))
            return
        text = {"collect": "売上", "stay": STAY_LABEL}.get(action, action)
        screen.start_phase(app, InvestPhase(app, action, facility_id=facility_id), text,
                           fallback=lambda: run_action(app, action, facility_id=facility_id))

    # ------------------------------------------------------------ 役場のボタン
    def is_facility_screen(buttons):
        if not isinstance(buttons, list):
            return False
        return any(ui.spec_cls_name(entry) == MOVE_CLS for entry in buttons)

    def at_office(app):
        location = getattr(getattr(app, "player", None), "location", None)
        if location is None or isinstance(location, (str, int)):
            return False
        return ui.facility_type_of(location) == OFFICE_FACILITY_TYPE

    def retry_when_idle(app):
        if state.get("retry"):
            return
        state["retry"] = True

        def again():
            state["retry"] = False
            try:
                maintain_buttons(app)
            except Exception:
                ctx.log_exc("investment: cannot maintain the choices (idle)")

        screen.when_idle(app, again, proceed_on_timeout=True, tag="retry")

    def maintain_buttons(app):
        """役場に「出資する」を足す。建物の中はローダが出す。何度呼んでも増えない。"""
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return
        if ui.busy_signals(app):
            retry_when_idle(app)
            return
        if not is_facility_screen(buttons):
            return
        if modfacility.game_is_busy(app):
            # 会話・戦闘・自由入力の最中は足さない（建物の選択肢と同じ判定。TECH.md §5.8）。
            return
        screen.prune_stale(buttons, list(OUR_LABEL_PREFIXES))
        if any(screen.mark_of(entry) for entry in buttons):
            return
        if not at_office(app):
            return
        entry = screen.button(DESK_LABEL, mark="desk")
        if entry is None:
            return
        buttons.insert(max(len(buttons) - 1, 0), entry)
        screen.apply_buttons(app, None, "office")

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """選択肢が組み直されるたびに、建物を当て直して自前のボタンを足す。

        最後にローダの塗り直しをもう一度呼ぶ（どちらの関所が内側かは適用順で変わる）。
        """
        result = orig(self, reset_page, *args, **kwargs)
        try:
            apply_holdings(self, getattr(self, "world", None), current_key(self), "screen")
            maintain_buttons(self)
            modfacility.maintain_buttons(self, write=write)
        except Exception:
            ctx.log_exc("investment: cannot maintain the choices")
        return result

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
        text = (entry.get("text") if isinstance(entry, dict) else None) or DESK_LABEL
        kind = entry.get(KIND_KEY) if isinstance(entry, dict) else None
        tier = entry.get(TIER_KEY) if isinstance(entry, dict) else None
        write("pressed {!r} ({} kind={} tier={})".format(text, action, kind, tier))
        screen.start_phase(self, InvestPhase(self, action, kind, tier), text,
                           fallback=lambda: run_action(self, action, kind, tier))
        return None

    @ctx.wrap("__main__:World.__init__", required=False, safe=True)
    def world_loaded(orig, self, save_data_dict, app, *args, **kwargs):
        """セーブを読んだ直後。前の世界の覚えを捨て、持ち株を当て直す。"""
        result = orig(self, save_data_dict, app, *args, **kwargs)
        try:
            key = playthrough_key_of_dict(save_data_dict, None) or playthrough_key(app)
            if app is not None and key and key != UNKNOWN_WORLD:
                worlds.forget(key)
                state["own_stay"] = None
                state["saved"] = None
                state["choosing"] = None
                state["warned"] = set()
                state["key_override"] = key
                # 主人の層はロードのたびに積み直す。周回（世界×主人公）が変わると同じ id
                # （`keeper-<土地>-<番>`）に別の主人が立つので、前の周回の層を残せない。
                keepers_registered.clear()
                try:
                    apply_holdings(app, self, key, "load")
                finally:
                    state["key_override"] = None
                write("load: the ledger of {!r} has {} holding(s)".format(
                    key, len(bucket_of(key).get("holdings") or [])))
        except Exception:
            ctx.log_exc("investment: cannot rebuild the holdings on load")
        return result

    @ctx.wrap("__main__:VacationStartManager.execute", required=False)
    def vacation_start(orig, self, choice_text="", *args, **kwargs):
        """自分の宿屋の「無料で泊まる」は宿代を取らない。よその宿屋には触らない。

        ゲームが引く額を**先に足しておき**、ゲームが引いて元へ戻す（前払い）。
        額はローダの窓口（`prices.inn_room`）が答える。`315_vacation_custom` が
        宿代を変えていればその額になる。

        前後の所持金の差で返すのはやめた。差を取る区間が `execute` 全体で、
        その中で暦も進むので、同じ区間で金を動かした他の MOD のぶんまで巻き込む。
        前払いなら正常な回は差が0で、こちらは何も動かさない。
        落ちたときは操作を戻す（`330_` と同じ手当て）。宿代の引き落としが
        `execute` の中で1回だけ起きることは GAME.md §2.17、この経路の確認は
        VERIFICATION.md §3.63。
        """
        app = getattr(self, "app", None) or ui.find_app()
        home = staying_here(app) if app is not None else None
        if home is None:
            return orig(self, choice_text, *args, **kwargs)
        before = ui.gold_of(app)
        prepaid = prepay_room(app, home, before)
        try:
            result = orig(self, choice_text, *args, **kwargs)
        except Exception as exc:
            write("WARN stay: the game's stay failed: {}({!r}) at {!r}".format(
                type(exc).__name__, getattr(exc, "args", ()), home.get("name")))
            ctx.log_exc("investment: the game's stay failed at {!r}".format(home.get("name")))
            settle_room(app, before, prepaid, "stay failed")
            end_stay(app, "the stay failed")
            screen.schedule(lambda: screen.busy_off(app), 0)
            return None
        settle_room(app, before, prepaid, "stay")
        return result

    def prepay_room(app, home, before):
        """ゲームが引く宿代を先に足す。足せた額。足せなければ 0。

        部屋は宿泊を始めたときに控えた `quality`（設定の `STAY_QUALITY`）。
        額が分からない（窓口が `None` を返す）ときは 0 のまま進み、`settle_room` の
        帳尻が引かれたぶんを戻す。当て推量の額は前払いしない。
        """
        quality = home.get("quality") or STAY_QUALITY
        price = prices.inn_room(app, quality, write=write)
        if price is None:
            write("WARN stay: the price of the room ({!r}) is unknown; "
                  "settling by the difference instead".format(quality))
            return 0
        if not isinstance(price, int) or price <= 0 or not isinstance(before, int):
            return 0
        if ui.add_gold(app, price, on_error=lambda msg:
                       write("WARN stay: cannot prepay: " + msg)) is None:
            write("WARN stay: cannot prepay {} for the room ({!r})".format(price, quality))
            return 0
        write("stay: prepaid {} for the room ({!r})".format(price, quality))
        return price

    def settle_room(app, before, prepaid, why):
        """前払いと引き落としの帳尻。動かした額（正常なら 0）。

        前払いした額とゲームが引いた額が同じなら差は0で、ここは何もしない。
        差が残るのは、額が分からずに前払いできなかったか、ゲームが引かなかったか、
        ゲームの額がこちらの知る額と違うとき。どれも WARN で残す。
        """
        after = ui.gold_of(app)
        if not isinstance(before, int) or not isinstance(after, int):
            return 0
        off = before - after
        if off == 0:
            return 0
        if prepaid <= 0 and off < 0:
            # 前払いできなかった回に**増えた**ぶんは、宿代とは関係が無い
            # （この区間では他の MOD も金を動かす）。取り上げない。
            write("WARN {}: the gold grew by {} during the stay; leaving it "
                  "alone".format(why, -off))
            return 0
        ui.add_gold(app, off, on_error=lambda msg:
                    write("WARN {}: cannot correct: {}".format(why, msg)))
        write("WARN {}: the room cost {} but we prepaid {}; corrected {}".format(
            why, prepaid + off, prepaid, off))
        return off

    @ctx.wrap("scripts.llm.llm_manager:colosseum_enemy_generator", required=False)
    def colosseum_enemy_generator(orig, location=None, *args, **kwargs):
        """自分の闘技場の相手を作るとき、既に出た闘士の名前を頼み文に足す。

        ゲームの頼み文は世界観・エリア・施設名と概要・ランクだけで、前の相手を載せない。
        同じ施設では毎回ほぼ同じ人物に収束した（実機。4試合とも同じ「<相手の名>」）。
        よその闘技場には触らない。
        """
        try:
            app = ui.find_app()
            record = own_arena(app) if app is not None else None
            if record is not None:
                names = fighters_so_far(app, record)
                varied = vary_location(location, names) if names else location
                if varied is not location:
                    write("arena: {} earlier fighter(s) told to the generator: {}".format(
                        len(names), "、".join(names[-3:])))
                    location = varied
        except Exception:
            ctx.log_exc("investment: cannot vary the arena prompt")
        return orig(location, *args, **kwargs)

    def run_guarded(orig, self, choice_text, args, kwargs, what, kinds):
        """自分の建物で起きる本体の処理を包む。落ちたら握って操作を戻す。

        素データの写しはローダが置く（`modfacility` の `plain`。中に立っている間）ので普通は通る。
        写しが無いまま来たら（塗り直しの前に押された）`KeyError` でワーカースレッドが死ぬので、
        ここで握って「…」のまま止まらないようにする。よその施設には触らない。
        """
        app = getattr(self, "app", None) or ui.find_app()
        record = own_building(app, kinds) if app is not None else None
        if record is None:
            return orig(self, choice_text, *args, **kwargs)
        try:
            return orig(self, choice_text, *args, **kwargs)
        except Exception as exc:
            write("WARN {}: the game's {} failed: {}({!r}) at {!r}".format(
                what, what, type(exc).__name__, getattr(exc, "args", ()),
                record.get("name")))
            ctx.log_exc("investment: the game's {} failed at {!r}".format(
                what, record.get("name")))
            screen.say(app, _fmt(GAME_FAILED_TEXT, name=record.get("name") or "ここ"))
            recover(app, what)
            return None

    def guard(target, what, kinds):
        """`GUARDED_PHASES` の1本を包む。"""
        @ctx.wrap(target, required=False)
        def guarded(orig, self, choice_text="", *args, **kwargs):
            return run_guarded(orig, self, choice_text, args, kwargs, what, kinds)
        return guarded

    for _target, _what, _kinds in GUARDED_PHASES:
        guard(_target, _what, _kinds)

    @ctx.wrap("__main__:VacationEndManager.execute", required=False)
    def vacation_end(orig, self, choice_text="", *args, **kwargs):
        """宿泊の覚え（`own_stay`）を落とすだけ。

        中の選択肢と絵の戻しはローダが持つ（`modfacility` の `PHASE_END_TARGETS` と
        `BG_OTHER_TARGETS`。TECH.md §5.8）。ここで画面を触っていたら MOD ごとに同じ直しが要る。
        """
        result = orig(self, choice_text, *args, **kwargs)
        try:
            end_stay(getattr(self, "app", None) or ui.find_app(), "vacation ended")
        except Exception:
            ctx.log_exc("investment: cannot close the stay")
        return result

    write("applied: kinds={} scales cost={}% income={}% hold={}d".format(
        catalog.enabled_kinds(), COST_SCALE, INCOME_SCALE, HOLD_DAYS))
